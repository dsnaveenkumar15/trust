import math
from dataclasses import dataclass

import pytest

from core.control import store
from core.db import connect

AGENTS = {"redteam"}


@dataclass
class Cfg:
    enabled: bool = True
    daily_usd: float = 0.25
    run_usd: float = 0.10
    per_ip_day: int = 3
    concurrent: int = 2
    queue: int = 3


@pytest.fixture
def conn(tmp_path):
    c = connect(str(tmp_path / "c.db"))
    store.ensure(c)
    return c


def test_an_override_beats_the_env_default_and_clearing_returns_to_it(conn):
    assert store.effective(conn, "redteam", Cfg()).daily_usd == 0.25
    store.set(conn, "redteam.daily_usd", 0.5, "demo day", 100.0, AGENTS)
    eff = store.effective(conn, "redteam", Cfg())
    assert eff.daily_usd == 0.5 and Cfg().daily_usd == 0.25          # the original is never mutated
    store.clear(conn, "redteam.daily_usd", "back to normal", 200.0)
    assert store.effective(conn, "redteam", Cfg()).daily_usd == 0.25
    assert [(c["key"], c["old"], c["new"], c["note"]) for c in store.changes(conn)] == [
        ("redteam.daily_usd", 0.5, None, "back to normal"), ("redteam.daily_usd", None, 0.5, "demo day")]


@pytest.mark.parametrize("key, value", [("redteam.daily_usd", 6), ("redteam.daily_usd", -1), ("redteam.daily_usd", "abc"),
                                        ("redteam.daily_usd", math.nan), ("redteam.daily_usd", math.inf),
                                        ("redteam.enabled", "true"), ("redteam.concurrent", 0), ("redteam.per_ip_day", 2.5),
                                        ("global.brake_pct", 40), ("nope.daily_usd", 1), ("redteam.models", "x"),
                                        ("global.stop_all", 1), ("batch.daily_usd", True)])
def test_bad_values_and_unknown_keys_are_refused_and_nothing_is_stored(conn, key, value):
    with pytest.raises(ValueError):
        store.set(conn, key, value, "", 1.0, AGENTS)
    assert store.get(conn, key) is None and store.changes(conn) == []


def test_good_values_of_each_kind(conn):
    assert store.set(conn, "redteam.enabled", False, "", 1.0, AGENTS) is False
    assert store.set(conn, "redteam.per_ip_day", 10, "", 1.0, AGENTS) == 10
    assert store.set(conn, "global.brake_pct", 90, "", 1.0, AGENTS) == 90
    assert store.set(conn, "batch.daily_usd", 0.03, "", 1.0, AGENTS) == 0.03
    assert store.effective(conn, "redteam", Cfg()).enabled is False


def test_check_key_knows_only_allowed_keys():
    store.check_key("redteam.queue", AGENTS)
    with pytest.raises(ValueError):
        store.check_key("redteam.models", AGENTS)


def test_cache_expires(conn):
    store.cache_put(conn, "or.key", {"usage_weekly": 1.0}, 100.0)
    assert store.cache_get(conn, "or.key", 300, 350.0) == {"usage_weekly": 1.0}
    assert store.cache_get(conn, "or.key", 300, 500.0) is None


def test_ensure_inside_an_open_transaction_does_not_end_it(tmp_path):
    from core.db import tx
    c = connect(str(tmp_path / "t.db"))
    c.execute("CREATE TABLE t(x)")
    with tx(c):
        c.execute("INSERT INTO t VALUES (1)")
        store.ensure(c)                                   # the admission check calls this under the run-creation lock
        assert c.in_transaction
        raise_after = True
    assert raise_after and c.execute("SELECT count(*) FROM t").fetchone()[0] == 1
    c2 = connect(str(tmp_path / "t.db"))
    try:
        with tx(c2):
            c2.execute("INSERT INTO t VALUES (2)")
            store.ensure(c2)
            raise RuntimeError("roll back")
    except RuntimeError:
        pass
    assert c2.execute("SELECT count(*) FROM t").fetchone()[0] == 1          # the insert was rolled back: ensure did not commit it
