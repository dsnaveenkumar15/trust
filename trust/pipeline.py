"""Trust audit stages: fetch -> extract -> rules -> review -> score -> record. Outputs are JSON-serialisable."""
import hashlib
import re
from dataclasses import asdict

from core import events
from core import review as review_queue
from core.github import RepoNotFound
from core.worker import PermanentError
from trust import store
from trust.extract import extract_tools, is_source
from trust.models import Finding, ToolDef
from trust.publishing import requires_review
from trust.review import PROMPT_VERSION, review
from trust.rules import RULESET_VERSION, run_rules
from trust.scoring import SCORING_VERSION, score

_SUPPORT_FILE = re.compile(r"(^|/)(requirements[^/]*\.txt|package\.json|readme[^/]*)$", re.I)


def wanted_path(path: str) -> bool:
    if "node_modules/" in path.lower():
        return False
    return is_source(path) or bool(_SUPPORT_FILE.search(path))


def stage_fetch(github, repo: str, sha: str | None) -> dict:
    try:
        return github.snapshot(repo, sha, wanted_path)
    except RepoNotFound as exc:
        raise PermanentError(f"repository not found: {repo}") from exc
    except ValueError as exc:
        raise PermanentError(str(exc)) from exc


def stage_extract(snapshot: dict) -> dict:
    tools, languages = extract_tools(snapshot["files"])
    status = "unsupported" if not languages else "no_tools" if not tools else "ok"
    return {"status": status, "languages": languages, "tools": [t.to_dict() for t in tools]}


def stage_rules(snapshot: dict, extracted: dict, now: float) -> dict:
    if extracted["status"] != "ok":
        return {"findings": []}
    tools = [ToolDef.from_dict(t) for t in extracted["tools"]]
    found = run_rules(snapshot["files"], tools, snapshot.get("meta") or {}, now)
    return {"findings": [f.to_dict() for f in found]}


def stage_review(conn, backend, snapshot: dict, extracted: dict, ruled: dict) -> dict:
    if extracted["status"] != "ok" or backend is None:
        return {"reviewed": False, "findings": ruled["findings"], "needs_review": [], "dropped_new": 0, "summary": ""}
    result = review(conn, backend, [ToolDef.from_dict(t) for t in extracted["tools"]],
                    [Finding.from_dict(f) for f in ruled["findings"]], snapshot["files"])
    return {"reviewed": True, "findings": [f.to_dict() for f in result.findings],
            "needs_review": result.needs_review, "dropped_new": result.dropped_new, "summary": result.summary}


def stage_score(extracted: dict, reviewed: dict) -> dict:
    if extracted["status"] != "ok":
        return {"total": None, "grade": None, "categories": {}, "capped_by_critical": False}
    return asdict(score([Finding.from_dict(f) for f in reviewed["findings"]]))


def stage_record(conn, outputs: dict, model: str | None, now: float) -> dict:
    snap, extracted, reviewed, scored = outputs["fetch"], outputs["extract"], outputs["review"], outputs["score"]
    repo, sha = snap["repo"], snap["sha"]
    status = extracted["status"] if extracted["status"] != "ok" else "graded" if reviewed["reviewed"] else "provisional"
    provenance = {
        "repo": repo, "sha": sha, "ruleset": RULESET_VERSION, "scoring": SCORING_VERSION,
        "prompt": PROMPT_VERSION if reviewed["reviewed"] else None, "model": model if reviewed["reviewed"] else None,
        "files": {p: hashlib.sha256(t.encode("utf-8")).hexdigest() for p, t in snap["files"].items()},
        "skipped": snap.get("skipped", []), "truncated": snap.get("truncated", False),
        "languages": extracted["languages"], "dropped_new_findings": reviewed["dropped_new"],
    }
    needs_review = list(reviewed["needs_review"])
    skipped_sources = [p for p in snap.get("skipped", []) if is_source(p)]
    if skipped_sources or snap.get("truncated"):
        # A grade over a partial snapshot could hide the poisoned file; the owner decides before it is published.
        needs_review.append(f"incomplete snapshot: {len(skipped_sources)} source file(s) skipped"
                            + (", file list truncated" if snap.get("truncated") else ""))
    previous = store.latest_audit(conn, repo)
    audit_id = store.save_audit(conn, {
        "repo": repo, "sha": sha, "status": status, "grade": scored["grade"], "score": scored["total"],
        "categories": scored["categories"], "findings": reviewed["findings"], "tools": extracted["tools"],
        "needs_review": needs_review, "provenance": provenance, "summary": reviewed["summary"],
    }, now)
    if not repo.startswith("local/"):
        store.record_audited(conn, repo, sha, store.tools_hash(extracted["tools"]), now)
    events.emit(conn, "trust.audit_published",
                {"server_id": repo, "sha": sha, "grade": scored["grade"], "status": status, "audit_id": audit_id},
                now=now)
    if (previous is not None and previous["status"] == "graded" and status == "graded"
            and previous["grade"] != scored["grade"]):
        events.emit(conn, "trust.grade_changed", {"server_id": repo, "old_grade": previous["grade"],
                                                  "new_grade": scored["grade"], "audit_id": audit_id}, now=now)
    if requires_review(reviewed["findings"], scored["grade"], needs_review):
        # Same rule the publisher applies: harsh or vulnerability findings wait for the owner (spec §5.4, §5.6).
        review_queue.add(conn, "trust", "trust_audit", f"{repo}@{sha}#{audit_id}",
                         {"grade": scored["grade"], "needs_review": needs_review}, now=now)
    return {"audit_id": audit_id, "status": status, "grade": scored["grade"]}


def run_audit(conn, snapshot: dict, backend, model: str | None, now: float) -> dict:
    """Run every stage after fetch in-process (CLI, tests). The worker runs the same stages as a job."""
    store.ensure_schema(conn)
    outputs = {"fetch": snapshot}
    outputs["extract"] = stage_extract(snapshot)
    outputs["rules"] = stage_rules(snapshot, outputs["extract"], now)
    outputs["review"] = stage_review(conn, backend, snapshot, outputs["extract"], outputs["rules"])
    outputs["score"] = stage_score(outputs["extract"], outputs["review"])
    outputs["record"] = stage_record(conn, outputs, model, now)
    return outputs
