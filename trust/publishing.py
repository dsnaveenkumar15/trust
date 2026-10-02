"""What Trust may publish, and in what form. Owner review gates harsh results; embargoes hide details."""
import hashlib
import json
import logging
import os
import re

from core import review
from core.publish import load_current, prune, publish
from trust import store
from trust.models import NON_DISMISSABLE_RULES, SEVERITIES
from trust.review import PROMPT_VERSION
from trust.rules import RULE_CATALOG, RULESET_VERSION
from trust.scoring import (CATEGORY_WEIGHTS, CONFIDENCE_FACTOR, GRADE_BANDS, RULE_CAP_MULTIPLIER, SCORING_VERSION,
                           SEVERITY_POINTS)

REVIEW_KIND = "trust_audit"
EMBARGO_S = 30 * 86400
STALE_AFTER_S = 30 * 86400
FINDING_KEYS = ("rule", "category", "severity", "confidence", "file", "line", "quote", "message", "tool", "source",
                "explanation")
SUMMARY_KEYS = ("repo", "slug", "status", "grade", "score", "stale", "under_disclosure", "audited_at", "stars",
                "counts", "newer_audit_pending")
# Findings that look like an unintentional vulnerability: the owner decides publish vs embargo before anything goes out.
DISCLOSURE_RULES = frozenset({"hardcoded-secret", "sensitive-path"})
_SEVERITY_ORDER = {s: i for i, s in enumerate(SEVERITIES)}


def slug(repo: str) -> str:
    return re.sub(r"[^a-z0-9._-]", "-", repo.lower().replace("/", "--"))


def review_ref(audit) -> str:
    return f"{audit['repo']}@{audit['sha']}#{audit['id']}"


def requires_review(findings: list[dict], grade: str | None, needs_review: list) -> bool:
    """Single source of truth for 'a person must decide before this is published' (queueing and publishing)."""
    return bool(needs_review) or grade == "F" or any(
        f["severity"] == "critical" or (f["rule"] in DISCLOSURE_RULES and f["severity"] in ("critical", "high"))
        for f in findings)


def publishable_audit(conn, repo: str):
    """(audit, embargo_until, is_newest) for the newest audit the owner allows, or None.

    Publishability is decided from the audit itself, not from whether a review row happens to exist, so an audit
    whose review row was never written (worker died mid-record) stays unpublished. Any active embargo on the repo
    also covers newer audits, so a re-audit cannot publish details early."""
    audits = store.audits_for(conn, repo)
    repo_embargo = None
    for audit in audits:
        entry = review.entry_for(conn, REVIEW_KIND, review_ref(audit))
        if entry is not None and entry["state"] == "embargoed":
            repo_embargo = max(repo_embargo or 0.0, entry["decided_at"] + EMBARGO_S)
    for position, audit in enumerate(audits):
        entry = review.entry_for(conn, REVIEW_KIND, review_ref(audit))
        state = entry["state"] if entry is not None else None
        if state in ("approved", "embargoed") or (state is None and not requires_review(
                json.loads(audit["findings"]), audit["grade"], json.loads(audit["needs_review"]))):
            return audit, repo_embargo, position == 0
    return None


def _entry(audit, server, embargo_until, now: float, is_newest: bool = True) -> dict:
    findings = sorted(json.loads(audit["findings"]),
                      key=lambda f: (_SEVERITY_ORDER.get(f["severity"], 9), f["file"], f["line"]))
    embargoed = embargo_until is not None and now < embargo_until
    provenance = json.loads(audit["provenance"])
    return {
        "repo": audit["repo"], "slug": slug(audit["repo"]), "sha": audit["sha"], "status": audit["status"],
        "grade": audit["grade"], "score": audit["score"], "categories": json.loads(audit["categories"]),
        "audited_at": audit["created_at"],
        "stale": (now - audit["created_at"] > STALE_AFTER_S or bool(server is not None and server["tools_changed"])
                  or not is_newest),
        "newer_audit_pending": not is_newest,
        "under_disclosure": embargoed, "disclosure_ends": embargo_until if embargoed else None,
        "counts": {s: sum(1 for f in findings if f["severity"] == s) for s in SEVERITIES},
        "findings": [] if embargoed else [{k: f.get(k) for k in FINDING_KEYS} for f in findings],
        "tools": [t["name"] for t in json.loads(audit["tools"])],
        "provenance": {"audit_id": audit["id"],
                       **{k: provenance.get(k) for k in ("ruleset", "scoring", "prompt", "model")}},
        "stars": server["stars"] if server is not None else 0,
    }


def third_party_enabled() -> bool:
    """Naming and grading other people's projects is off unless the owner turns it on (TRUST_THIRD_PARTY=1)."""
    return os.environ.get("TRUST_THIRD_PARTY") == "1"


def build_entries(conn, now: float, include_local: bool = False) -> list[dict]:
    """local/ audits come from someone's checkout; they are published only for explicit local previews.
    Third-party repositories are published only when third_party_enabled()."""
    store.ensure_schema(conn)
    entries = []
    for repo in store.audited_repos(conn):
        if repo.startswith("local/"):
            if not include_local:
                continue
        elif not third_party_enabled():
            continue
        found = publishable_audit(conn, repo)
        if found is not None:
            audit, embargo_until, is_newest = found
            entries.append(_entry(audit, store.get_server(conn, repo), embargo_until, now, is_newest))
    return entries


log = logging.getLogger("trust.publishing")
DATASET = "directory"
KEEP_RELEASES = 10


def methodology() -> dict:
    try:
        from evals.trust_eval import evaluate
        result = evaluate()
        evaluation = {"precision": round(result["precision"], 3), "recall": round(result["recall"], 3),
                      "cases": len(result["cases"])}
    except (ImportError, OSError) as exc:
        log.warning("eval harness unavailable: %s", exc)
        evaluation = None
    return {
        "versions": {"ruleset": RULESET_VERSION, "scoring": SCORING_VERSION, "prompt": PROMPT_VERSION},
        "severity_points": SEVERITY_POINTS, "confidence_factor": CONFIDENCE_FACTOR,
        "category_weights": CATEGORY_WEIGHTS,
        "grade_bands": [{"grade": grade, "min": floor} for floor, grade in GRADE_BANDS],
        "rule_cap_multiplier": RULE_CAP_MULTIPLIER, "critical_cap": "D",
        "rules": [dict(r, dismissable=r["rule"] not in NON_DISMISSABLE_RULES) for r in RULE_CATALOG],
        "evaluation": evaluation, "embargo_days": EMBARGO_S // 86400, "stale_days": STALE_AFTER_S // 86400,
    }


def _dump(obj) -> bytes:
    return json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False).encode("utf-8")


def build_files(conn, now: float, include_local: bool = False) -> dict[str, bytes]:
    """Release contents. No timestamps inside, so an unchanged directory produces identical bytes."""
    entries = build_entries(conn, now, include_local)
    index = {"count": len(entries), "servers": [{k: e[k] for k in SUMMARY_KEYS} for e in entries]}
    files = {"index.json": _dump(index), "methodology.json": _dump(methodology())}
    for entry in entries:
        files[f"servers/{entry['slug']}.json"] = _dump(entry)
    return files


def _unchanged(files: dict[str, bytes], release) -> bool:
    current = release.manifest.get("files", {})
    return set(current) == set(files) and all(
        current[name] == hashlib.sha256(data).hexdigest() for name, data in files.items())


def publish_directory(conn, data_root, now: float, include_local: bool = False) -> dict:
    files = build_files(conn, now, include_local)
    release = load_current(data_root, "trust", DATASET)
    if release is not None and _unchanged(files, release):
        return {"published": False, "release": release.id}
    release_id = publish(data_root, "trust", DATASET, files,
                         {"ruleset": RULESET_VERSION, "scoring": SCORING_VERSION, "generated_at": now},
                         conn=conn, now=now)
    prune(data_root, "trust", DATASET, keep=KEEP_RELEASES)
    return {"published": True, "release": release_id}
