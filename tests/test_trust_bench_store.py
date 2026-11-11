import sqlite3

import pytest

from trust.redteam import bench_store as bs

V = "v1"
TITLES = {"hidden-instructions": "Hidden instructions", "impersonation": "Impersonation"}


@pytest.fixture
def db(conn):
    bs.ensure_schema(conn)
    return conn


def add(conn, model, attack, condition, verdicts, cost=0.001):
    for n, v in enumerate(verdicts):
        bs.add_try(conn, V, model, attack, condition, n, v, [], {"calls": []}, ["s"], cost, 1.0)


def test_tries_needed():
    assert bs.tries_needed([]) == 3
    assert bs.tries_needed(["resisted"] * 3) == 0
    assert bs.tries_needed(["resisted", "got_through", "resisted"]) == 2
    assert bs.tries_needed(["resisted", "resisted", "not_run"]) == 1
    assert bs.tries_needed(["resisted", "got_through", "resisted", "got_through", "resisted"]) == 0


def test_version_changes_with_any_part():
    a = bs.bench_version([b"a", b"b"])
    assert len(a) == 12 and a != bs.bench_version([b"a", b"c"]) and a == bs.bench_version([b"a", b"b"])


def test_summary_counts_and_headline_skip_excluded(db):
    add(db, "m1", "hidden-instructions", "none", ["got_through", "partial", "not_run", "resisted"])
    add(db, "m1", "hidden-instructions", "guard", ["resisted"] * 3)
    add(db, "m1", "impersonation", "none", ["got_through"] * 3)
    add(db, "m2", "hidden-instructions", "none", ["got_through"] * 3)
    s = bs.summarize(db, V, ["m1", "m2"], list(TITLES), TITLES, {"impersonation"})
    cell = s["cells"]["m1"]["hidden-instructions"]["none"]
    assert (cell["got"], cell["partial"], cell["counted"], cell["gaps"]) == (1, 1, 3, 1) and len(cell["try_ids"]) == 4
    assert cell["show"] == cell["try_ids"][0]                              # the first try that got through
    assert s["cells"]["m1"]["hidden-instructions"]["guard"]["show"] == s["cells"]["m1"]["hidden-instructions"]["guard"]["try_ids"][0]
    assert s["cells"]["m1"]["hidden-instructions"]["prompt"]["show"] is None
    assert s["headline"]["none"] == {"got": 4, "counted": 6}             # impersonation left out
    assert s["headline"]["guard"] == {"got": 0, "counted": 3}
    assert s["headline"]["prompt"] == {"got": 0, "counted": 0}
    assert [a["id"] for a in s["by_attack"]] == ["hidden-instructions"]
    assert s["attacks"][1] == {"id": "impersonation", "title": "Impersonation", "excluded": True}
    assert s["cost_usd"] == pytest.approx(0.001 * 13) and s["tries"] == 13
    assert s["format"] == "redteam-bench@1" and s["version"] == V and s["models"] == ["m1", "m2"]


def test_publish_and_latest(db):
    assert bs.latest(db) is None
    bs.publish(db, {"n": 1}, 1.0)
    bs.publish(db, {"n": 2}, 2.0)
    assert bs.latest(db)["summary"] == {"n": 2}


def test_get_try(db):
    tid = bs.add_try(db, V, "m", "a", "none", 0, "resisted", ["x"], {"calls": [], "final": "hi"}, ["s"], 0.0, 1.0)
    got = bs.get_try(db, tid)
    assert got["model"] == "m" and got["trace"]["final"] == "hi" and got["hits"] == ["x"] and got["canaries"] == ["s"]
    assert bs.get_try(db, 999) is None


def test_add_try_twice_same_slot_is_refused(db):
    add(db, "m", "a", "none", ["resisted"])
    with pytest.raises(sqlite3.IntegrityError):
        add(db, "m", "a", "none", ["resisted"])
