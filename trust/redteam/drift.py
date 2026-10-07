"""Has this server's tool text changed since Trust audited it? Descriptions that change after people approved a server
are a known attack (a 'rug pull'). Free: it reads Trust's own audit table."""
import json
import sqlite3

from trust import store
from trust.redteam.models import Target


def changes(conn, target: Target) -> dict:
    if target.kind != "repo" or not target.repo:
        return {"earlier": False}
    try:
        row = store.latest_audit(conn, target.repo)
    except sqlite3.Error:                         # Trust's tables are absent in this database
        return {"earlier": False}
    if row is None:
        return {"earlier": False}
    old = {t["name"]: t.get("description", "") for t in json.loads(row["tools"])}
    new = {t.name: t.description for t in target.tools}
    return {"earlier": True, "sha": row["sha"], "audited_at": row["created_at"], "added": sorted(set(new) - set(old)),
            "removed": sorted(set(old) - set(new)), "changed": sorted(n for n in set(new) & set(old) if new[n] != old[n])}
