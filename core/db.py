import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = Path(__file__).with_name("schema.sql")


def connect(path: str | Path) -> sqlite3.Connection:
    """Open the shared database in autocommit mode with WAL and the schema applied."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    return conn


@contextmanager
def tx(conn: sqlite3.Connection):
    """Write transaction that takes the write lock up front (no upgrade deadlocks)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
