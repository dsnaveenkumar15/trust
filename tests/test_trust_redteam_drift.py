from core.db import connect
from trust import store
from trust.redteam import drift
from trust.redteam.models import Target, Tool


def audit(conn, tools):
    store.save_audit(conn, {"repo": "acme/tools", "sha": "b" * 40, "status": "graded", "grade": "B", "score": 90,
                            "categories": {}, "findings": [], "tools": tools, "needs_review": [], "provenance": {},
                            "summary": ""}, 100.0)


def target(*tools):
    return Target("repo", [Tool(n, d, {}) for n, d in tools], {}, "acme/tools", "c" * 40)


def test_changes_against_the_last_audit(tmp_path):
    conn = connect(str(tmp_path / "d.db"))
    store.ensure_schema(conn)
    audit(conn, [{"name": "a", "description": "A."}, {"name": "b", "description": "B."}])
    out = drift.changes(conn, target(("a", "A, now evil."), ("c", "C.")))
    assert out == {"earlier": True, "sha": "b" * 40, "audited_at": 100.0, "added": ["c"], "removed": ["b"], "changed": ["a"]}


def test_no_audit_no_table_or_a_pasted_list_mean_no_earlier_audit(tmp_path):
    conn = connect(str(tmp_path / "d.db"))
    assert drift.changes(conn, target(("a", "A."))) == {"earlier": False}          # no table
    store.ensure_schema(conn)
    assert drift.changes(conn, target(("a", "A."))) == {"earlier": False}          # no row
    assert drift.changes(conn, Target("manifest", [], {})) == {"earlier": False}
