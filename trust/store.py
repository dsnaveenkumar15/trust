import hashlib
import json
from pathlib import Path

SCHEMA = Path(__file__).with_name("schema.sql")


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))


def save_audit(conn, audit: dict, now: float) -> int:
    cur = conn.execute(
        "INSERT INTO trust_audits(repo, sha, status, grade, score, categories, findings, tools, needs_review, "
        "provenance, summary, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (audit["repo"], audit["sha"], audit["status"], audit["grade"], audit["score"],
         json.dumps(audit["categories"]), json.dumps(audit["findings"]), json.dumps(audit["tools"]),
         json.dumps(audit["needs_review"]), json.dumps(audit["provenance"], sort_keys=True), audit["summary"], now),
    )
    return cur.lastrowid


def latest_audit(conn, repo: str):
    return conn.execute("SELECT * FROM trust_audits WHERE repo=? ORDER BY created_at DESC, id DESC LIMIT 1",
                        (repo,)).fetchone()


def tools_hash(tools: list[dict]) -> str:
    """Identity of what an agent sees: tool names and descriptions, independent of file layout and order."""
    pairs = sorted([t["name"], t.get("description", "")] for t in tools)
    return hashlib.sha256(json.dumps(pairs, ensure_ascii=False).encode("utf-8")).hexdigest()


def upsert_server(conn, repo: str, source: str, stars: int, now: float) -> None:
    row = conn.execute("SELECT sources, stars FROM trust_servers WHERE repo=?", (repo,)).fetchone()
    if row is None:
        conn.execute("INSERT INTO trust_servers(repo, sources, stars, first_seen) VALUES (?, ?, ?, ?)",
                     (repo, source, stars, now))
        return
    sources = sorted(set(row["sources"].split(",")) | {source})
    conn.execute("UPDATE trust_servers SET sources=?, stars=? WHERE repo=?",
                 (",".join(sources), max(row["stars"], stars), repo))


def get_server(conn, repo: str):
    return conn.execute("SELECT * FROM trust_servers WHERE repo=?", (repo,)).fetchone()


def servers_without_audit(conn, limit: int) -> list:
    return conn.execute(
        "SELECT * FROM trust_servers s WHERE s.last_checked IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM trust_audits a WHERE a.repo = s.repo) "
        "ORDER BY s.stars DESC, s.first_seen ASC, s.repo ASC LIMIT ?", (limit,)).fetchall()


def servers_due_for_check(conn, checked_before: float, limit: int) -> list:
    return conn.execute(
        "SELECT * FROM trust_servers s WHERE EXISTS (SELECT 1 FROM trust_audits a WHERE a.repo = s.repo) "
        "AND (s.last_checked IS NULL OR s.last_checked < ?) "
        "ORDER BY s.last_checked IS NOT NULL, s.last_checked, s.repo LIMIT ?", (checked_before, limit)).fetchall()


def mark_checked(conn, repo: str, head_sha: str | None, tools_hash_value: str | None, tools_changed: bool,
                 now: float) -> None:
    conn.execute("UPDATE trust_servers SET last_checked=?, head_sha=?, tools_hash=COALESCE(?, tools_hash), "
                 "tools_changed=? WHERE repo=?", (now, head_sha, tools_hash_value, int(tools_changed), repo))


def record_audited(conn, repo: str, sha: str, tools_hash_value: str, now: float) -> None:
    upsert_server(conn, repo, "audit", 0, now)
    conn.execute("UPDATE trust_servers SET head_sha=?, tools_hash=?, tools_changed=0, last_checked=? WHERE repo=?",
                 (sha, tools_hash_value, now, repo))


def audited_repos(conn) -> list[str]:
    return [r["repo"] for r in conn.execute("SELECT DISTINCT repo FROM trust_audits ORDER BY repo")]


def audits_for(conn, repo: str) -> list:
    return conn.execute("SELECT * FROM trust_audits WHERE repo=? ORDER BY created_at DESC, id DESC",
                        (repo,)).fetchall()
