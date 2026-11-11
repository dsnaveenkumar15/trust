"""Red team benchmark storage: every try of (model, attack, condition) under one benchmark version, and the summaries
the owner publishes. A try is never stored twice in the same slot, so a re-run only fills what is missing."""
import hashlib
import json

CONDITIONS = ("none", "prompt", "guard")
FORMAT = "redteam-bench@1"
TARGET, DISAGREE_TARGET = 3, 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS redteam_bench_tries (
  id INTEGER PRIMARY KEY,
  version TEXT NOT NULL,
  model TEXT NOT NULL,
  attack TEXT NOT NULL,
  condition TEXT NOT NULL,
  n INTEGER NOT NULL,
  verdict TEXT NOT NULL,
  hits TEXT NOT NULL,
  trace TEXT NOT NULL,
  canaries TEXT NOT NULL,
  cost_usd REAL NOT NULL,
  created_at REAL NOT NULL,
  UNIQUE (version, model, attack, condition, n)
);
CREATE TABLE IF NOT EXISTS redteam_bench_published (
  id INTEGER PRIMARY KEY,
  version TEXT NOT NULL,
  created_at REAL NOT NULL,
  summary TEXT NOT NULL
);
"""


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)


def bench_version(parts: list[bytes]) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(hashlib.sha256(p).digest())          # hash of hashes: part boundaries count
    return h.hexdigest()[:12]


def add_try(conn, version, model, attack, condition, n, verdict, hits, trace, canaries, cost_usd, now) -> int:
    cur = conn.execute("INSERT INTO redteam_bench_tries(version, model, attack, condition, n, verdict, hits, trace, canaries, "
                       "cost_usd, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (version, model, attack, condition, n, verdict, json.dumps(hits), json.dumps(trace),
                        json.dumps(canaries), float(cost_usd), now))
    return cur.lastrowid


def verdicts(conn, version, model, attack, condition) -> list[str]:
    return [r["verdict"] for r in conn.execute(
        "SELECT verdict FROM redteam_bench_tries WHERE version=? AND model=? AND attack=? AND condition=? ORDER BY n",
        (version, model, attack, condition))]


def tries_needed(found: list[str]) -> int:
    """3 counted tries per cell; 5 when the counted ones disagree. `not_run` never counts."""
    counted = [v for v in found if v != "not_run"]
    target = TARGET if len(counted) < TARGET or len(set(counted)) == 1 else DISAGREE_TARGET
    return max(0, target - len(counted))


def _empty() -> dict:
    return {"got": 0, "partial": 0, "counted": 0, "gaps": 0, "try_ids": [], "show": None}


def summarize(conn, version, models: list[str], attacks: list[str], titles: dict[str, str], excluded: set[str]) -> dict:
    cells = {m: {a: {c: _empty() for c in CONDITIONS} for a in attacks} for m in models}
    cost, tries = 0.0, 0
    for r in conn.execute("SELECT id, model, attack, condition, verdict, cost_usd FROM redteam_bench_tries WHERE version=? "
                          "ORDER BY id", (version,)):
        cell = cells.get(r["model"], {}).get(r["attack"], {}).get(r["condition"])
        if cell is None:
            continue
        cost, tries = cost + r["cost_usd"], tries + 1
        cell["try_ids"].append(r["id"])
        if cell["show"] is None or (r["verdict"] == "got_through" and cell["got"] == 0):
            cell["show"] = r["id"]                  # the trace a reader sees: the first that got through, else the first try
        if r["verdict"] == "not_run":
            cell["gaps"] += 1
            continue
        cell["counted"] += 1
        cell["got"] += r["verdict"] == "got_through"
        cell["partial"] += r["verdict"] == "partial"
    scored = [a for a in attacks if a not in excluded]
    headline = {c: {"got": sum(cells[m][a][c]["got"] for m in models for a in scored),
                    "counted": sum(cells[m][a][c]["counted"] for m in models for a in scored)} for c in CONDITIONS}
    by_attack = [{"id": a, "got": sum(cells[m][a]["none"]["got"] for m in models),
                  "counted": sum(cells[m][a]["none"]["counted"] for m in models)} for a in scored]
    by_attack.sort(key=lambda x: -(x["got"] / x["counted"]) if x["counted"] else 0.0)
    return {"format": FORMAT, "version": version, "models": list(models),
            "attacks": [{"id": a, "title": titles.get(a, a), "excluded": a in excluded} for a in attacks],
            "cells": cells, "headline": headline, "by_attack": by_attack, "cost_usd": round(cost, 6), "tries": tries}


def publish(conn, summary: dict, now) -> int:
    return conn.execute("INSERT INTO redteam_bench_published(version, created_at, summary) VALUES (?,?,?)",
                        (summary.get("version", ""), now, json.dumps(summary))).lastrowid


def latest(conn) -> dict | None:
    r = conn.execute("SELECT id, version, created_at, summary FROM redteam_bench_published ORDER BY id DESC LIMIT 1").fetchone()
    return {"id": r["id"], "version": r["version"], "created_at": r["created_at"], "summary": json.loads(r["summary"])} if r else None


def get_try(conn, try_id: int) -> dict | None:
    r = conn.execute("SELECT * FROM redteam_bench_tries WHERE id=?", (try_id,)).fetchone()
    if r is None:
        return None
    out = dict(r)
    for k in ("hits", "trace", "canaries"):
        out[k] = json.loads(out[k])
    return out
