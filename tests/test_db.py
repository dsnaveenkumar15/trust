import pytest

from core.db import connect, tx

TABLES = {"jobs", "stage_artifacts", "llm_calls", "events", "consumer_cursors",
          "quota_state", "review_queue", "manifests", "http_cache"}


def test_schema_creates_tables_and_wal(conn):
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert TABLES <= names
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_connect_is_idempotent_and_creates_parent_dir(tmp_path):
    path = tmp_path / "nested" / "dir" / "x.db"
    connect(path).close()
    connect(path).close()
    assert path.exists()


def test_tx_rolls_back_on_error(conn):
    with pytest.raises(RuntimeError):
        with tx(conn):
            conn.execute("INSERT INTO events(type, version, payload, created_at) VALUES ('t', 1, '{}', 0)")
            raise RuntimeError("boom")
    assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 0


def test_tx_commits(conn):
    with tx(conn):
        conn.execute("INSERT INTO events(type, version, payload, created_at) VALUES ('t', 1, '{}', 0)")
    assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 1
