"""Nightly summary shown on /admin."""


def build_summary(conn, since: float) -> dict:
    jobs = {
        f"{r['planet']}:{r['state']}": r["n"]
        for r in conn.execute("SELECT planet, state, count(*) AS n FROM jobs WHERE updated_at >= ? "
                              "GROUP BY planet, state ORDER BY planet, state", (since,))
    }
    llm_calls = {
        r["outcome"]: r["n"]
        for r in conn.execute("SELECT outcome, count(*) AS n FROM llm_calls WHERE created_at >= ? GROUP BY outcome",
                              (since,))
    }
    return {
        "since": since,
        "jobs": jobs,
        "llm_calls": llm_calls,
        "open_reviews": conn.execute("SELECT count(*) FROM review_queue WHERE state='open'").fetchone()[0],
        "dead_jobs": conn.execute("SELECT count(*) FROM jobs WHERE state='dead'").fetchone()[0],
    }
