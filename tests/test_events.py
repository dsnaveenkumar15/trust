import pytest

from core.events import consume, emit


def test_consume_in_order_and_advance_cursor(conn):
    emit(conn, "radar.mcp_server_discovered", {"repo": "a"})
    emit(conn, "radar.mcp_server_discovered", {"repo": "b"})
    seen = []
    assert consume(conn, "trust", lambda e: seen.append(e.payload["repo"])) == 2
    assert seen == ["a", "b"]
    assert consume(conn, "trust", lambda e: seen.append("again")) == 0


def test_consumers_have_independent_cursors(conn):
    emit(conn, "x", {})
    assert consume(conn, "c1", lambda e: None) == 1
    assert consume(conn, "c2", lambda e: None) == 1


def test_handler_failure_redelivers_from_failed_event(conn):
    for i in range(3):
        emit(conn, "x", {"i": i})
    seen, failed = [], {"once": False}

    def flaky(event):
        if event.payload["i"] == 1 and not failed["once"]:
            failed["once"] = True
            seen.append("fail")
            raise RuntimeError("transient")
        seen.append(event.payload["i"])

    with pytest.raises(RuntimeError):
        consume(conn, "c", flaky)
    consume(conn, "c", flaky)
    assert seen == [0, "fail", 1, 2]


def test_type_filter(conn):
    emit(conn, "trust.grade_changed", {"g": "B"})
    emit(conn, "radar.breaking_change_detected", {"p": "x"})
    types = []
    consume(conn, "bp", lambda e: types.append((e.type, e.version)), types=["trust.grade_changed"])
    assert types == [("trust.grade_changed", 1)]
