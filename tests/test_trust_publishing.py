import json

import pytest

from core import review
from trust import store
from trust.publishing import (EMBARGO_S, STALE_AFTER_S, SUMMARY_KEYS, build_entries, publishable_audit, review_ref,
                              slug)


def audit(conn, repo="acme/weather", sha="a" * 40, grade="B", status="graded", findings=None, now=1000.0):
    store.ensure_schema(conn)
    findings = findings if findings is not None else [
        {"rule": "shell-exec", "category": "capabilities", "severity": "high", "confidence": "high",
         "file": "server.py", "line": 3, "quote": "subprocess.run(cmd)", "message": "Runs shell commands.",
         "tool": None, "dismissable": True, "source": "rule", "explanation": "<b>confirmed</b>"}]
    store.save_audit(conn, {
        "repo": repo, "sha": sha, "status": status, "grade": grade, "score": 94, "categories": {"injection": 100},
        "findings": findings, "tools": [{"name": "run", "description": "Run.", "file": "server.py", "line": 2,
                                         "language": "python", "opaque": False}],
        "needs_review": [], "provenance": {"ruleset": "1.0.0", "scoring": "1.0.0", "prompt": "trust.review@1",
                                           "model": "sonnet"}, "summary": "LLM summary"}, now)
    return store.audits_for(conn, repo)[0]


def test_slug():
    assert slug("Acme/Weather.Server") == "acme--weather.server"
    assert slug("local/x y") == "local--x-y"


def test_unreviewed_audit_is_published_with_findings_and_no_summary(conn):
    a = audit(conn)
    entry = build_entries(conn, now=2000.0)[0]
    assert (entry["repo"], entry["grade"], entry["slug"]) == ("acme/weather", "B", "acme--weather")
    assert entry["findings"][0]["quote"] == "subprocess.run(cmd)"
    assert entry["counts"]["high"] == 1 and entry["provenance"]["audit_id"] == a["id"]
    assert "summary" not in entry and entry["under_disclosure"] is False and entry["stale"] is False


def test_open_review_publishes_the_previous_good_audit_and_rejected_is_never_published(conn):
    first = audit(conn, sha="a" * 40, grade="A", now=1000.0)
    second = audit(conn, sha="b" * 40, grade="D", now=2000.0)
    rid = review.add(conn, "trust", "trust_audit", review_ref(second), {})
    assert publishable_audit(conn, "acme/weather")[0]["id"] == first["id"]
    review.decide(conn, rid, "rejected")
    assert publishable_audit(conn, "acme/weather")[0]["id"] == first["id"]
    review.add(conn, "trust", "trust_audit", review_ref(first), {})
    assert publishable_audit(conn, "acme/weather") is None
    assert build_entries(conn, now=3000.0) == []


def test_embargo_hides_findings_in_the_data_until_it_ends(conn):
    a = audit(conn)
    rid = review.add(conn, "trust", "trust_audit", review_ref(a), {})
    review.decide(conn, rid, "embargoed", now=5000.0)
    hidden = build_entries(conn, now=5000.0 + EMBARGO_S - 1)[0]
    assert hidden["under_disclosure"] is True and hidden["findings"] == []
    assert hidden["counts"]["high"] == 1 and hidden["disclosure_ends"] == 5000.0 + EMBARGO_S
    assert "subprocess.run" not in json.dumps(hidden)
    shown = build_entries(conn, now=5000.0 + EMBARGO_S)[0]
    assert shown["under_disclosure"] is False and shown["findings"][0]["rule"] == "shell-exec"


def test_stale_by_age_or_changed_tools(conn):
    audit(conn, now=1000.0)
    assert build_entries(conn, now=1000.0 + STALE_AFTER_S + 1)[0]["stale"] is True
    store.record_audited(conn, "acme/weather", "a" * 40, "h", now=1000.0)
    store.mark_checked(conn, "acme/weather", "c" * 40, "h2", True, now=1100.0)
    assert build_entries(conn, now=1200.0)[0]["stale"] is True


def test_unscored_statuses_are_listed_without_grade(conn):
    audit(conn, repo="acme/go", status="unsupported", grade=None, findings=[])
    entry = build_entries(conn, now=2000.0)[0]
    assert (entry["status"], entry["grade"]) == ("unsupported", None)


from core.publish import load_current
from evals.trust_eval import predict, FIXTURES
from trust.jobs import schedule_periodic
from trust.models import NON_DISMISSABLE_RULES
from trust.publishing import build_files, methodology, publish_directory
from trust.rules import RULE_CATALOG


def test_rule_catalog_covers_every_rule_the_engine_emits():
    names = {r["rule"] for r in RULE_CATALOG}
    for case in (p for p in FIXTURES.iterdir() if p.is_dir()):
        _, findings, _ = predict(case)
        assert {f["rule"] for f in findings} <= names, case.name
    assert NON_DISMISSABLE_RULES <= names


def test_methodology_has_formula_rules_and_eval():
    m = methodology()
    assert m["category_weights"]["injection"] == 0.30 and m["critical_cap"] == "D"
    assert {"rule": "hidden-instructions", "dismissable": False}.items() <= next(
        r for r in m["rules"] if r["rule"] == "hidden-instructions").items()
    assert m["evaluation"]["precision"] >= 0.9 and m["evaluation"]["cases"] == 10


def test_build_files_shapes_and_keeps_findings_out_of_the_index(conn):
    audit(conn)
    files = build_files(conn, now=2000.0)
    assert set(files) == {"index.json", "methodology.json", "servers/acme--weather.json"}
    index = json.loads(files["index.json"])
    assert index["count"] == 1 and set(index["servers"][0]) == set(SUMMARY_KEYS)
    assert json.loads(files["servers/acme--weather.json"])["findings"]


def test_publish_directory_skips_unchanged_and_republishes_on_change(conn, tmp_path):
    audit(conn)
    first = publish_directory(conn, tmp_path, now=2000.0)
    assert first["published"] is True
    assert publish_directory(conn, tmp_path, now=2100.0) == {"published": False, "release": first["release"]}
    audit(conn, repo="acme/other", now=2200.0)
    again = publish_directory(conn, tmp_path, now=2300.0)
    assert again["published"] is True and load_current(tmp_path, "trust", "directory").id == again["release"]


def test_schedule_periodic_enqueues_all_three(conn):
    store.ensure_schema(conn)
    schedule_periodic(conn, now=0.0)
    schedule_periodic(conn, now=10.0)
    types = sorted(r["job_type"] for r in conn.execute("SELECT job_type FROM jobs"))
    assert types == ["trust.discover", "trust.publish", "trust.refresh"]


CRITICAL = [{"rule": "hidden-instructions", "category": "injection", "severity": "critical", "confidence": "high",
             "file": "server.py", "line": 2, "quote": "<IMPORTANT>", "message": "m", "tool": "run",
             "dismissable": False, "source": "rule", "explanation": ""}]
SECRET = [{"rule": "hardcoded-secret", "category": "supply_chain", "severity": "high", "confidence": "high",
           "file": "server.py", "line": 4, "quote": "KEY = \"sk-t", "message": "m", "tool": None,
           "dismissable": True, "source": "rule", "explanation": ""}]


def test_unreviewed_critical_audit_is_never_published_even_without_a_review_row(conn):
    audit(conn, grade="D", findings=CRITICAL)            # e.g. worker died before the review row was written
    assert build_entries(conn, now=2000.0) == []


def test_unreviewed_vulnerability_findings_wait_for_the_owner(conn):
    a = audit(conn, findings=SECRET)
    assert build_entries(conn, now=2000.0) == []
    rid = review.add(conn, "trust", "trust_audit", review_ref(a), {})
    review.decide(conn, rid, "approved", now=2100.0)
    assert build_entries(conn, now=2200.0)[0]["findings"][0]["rule"] == "hardcoded-secret"


def test_embargo_carries_over_to_newer_audits_of_the_same_repo(conn):
    first = audit(conn, findings=SECRET, now=1000.0)
    rid = review.add(conn, "trust", "trust_audit", review_ref(first), {})
    review.decide(conn, rid, "embargoed", now=5000.0)
    audit(conn, sha="b" * 40, now=6000.0)                  # re-audit after a tools change, no review needed
    entry = build_entries(conn, now=7000.0)[0]
    assert entry["sha"] == "b" * 40 and entry["under_disclosure"] is True and entry["findings"] == []
    assert build_entries(conn, now=5000.0 + EMBARGO_S)[0]["findings"]


def test_fallback_to_an_older_audit_is_flagged(conn):
    audit(conn, grade="A", findings=[], now=1000.0)
    audit(conn, sha="b" * 40, grade="D", findings=CRITICAL, now=2000.0)
    entry = build_entries(conn, now=3000.0)[0]
    assert entry["sha"] == "a" * 40 and entry["stale"] is True and entry["newer_audit_pending"] is True


def test_local_audits_stay_private_unless_asked(conn):
    audit(conn, repo="local/private-checkout")
    assert build_entries(conn, now=2000.0) == []
    assert build_entries(conn, now=2000.0, include_local=True)[0]["repo"] == "local/private-checkout"
