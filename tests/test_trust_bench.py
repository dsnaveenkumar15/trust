import json
import random

import pytest

from tests.agent_fakes import ai
from tests.trust_redteam_fakes import FakeModel, leaky, polite
from trust.redteam import bench
from trust.redteam import bench_store as bs
from trust.redteam.runs import RedTeamConfig
from trust.redteam.sim import VICTIM_SYSTEM

CFG = RedTeamConfig(prices={"a": (0.0, 0.0), "b": (0.0, 0.0), "x": (0.0, 0.0)})


def rng():
    return random.Random(7)


def factory(victims: dict):
    models = {name: FakeModel(victim=v) for name, v in victims.items()}

    def make(name):
        if isinstance(models[name].victim, Exception):
            raise models[name].victim
        return models[name]
    return make


def one_fixture(tmp_path, name="hidden-instructions"):
    d = tmp_path / "bad"
    d.mkdir()
    (d / f"{name}.json").write_bytes((bench.FIXTURES / f"{name}.json").read_bytes())
    return d


@pytest.fixture
def db(conn):
    bs.ensure_schema(conn)
    return conn


def test_every_bad_fixture_has_a_candidate_for_its_attack():
    found = bench.targets()
    assert len(found) == 8 and all(c is not None and c.scenario == expect for expect, _, c in found)


def test_conditions_change_what_the_victim_sees():
    expect, target, cand = next(t for t in bench.targets() if t[0] == "hidden-instructions")
    seen = []

    def spy(messages, tools):
        seen.append((messages[0].content, tools[0]["function"]["description"]))
        return leaky(messages, tools)
    gw = bench.make_gateway(CFG, ["a"], 1.0, factory=factory({"a": spy}))
    out = bench.one_try(gw, "a", expect, target, cand, "none", rng())
    assert out["verdict"] == "got_through" and out["canaries"]
    assert seen[-1][0] == VICTIM_SYSTEM and cand.quote in seen[-1][1]
    bench.one_try(gw, "a", expect, target, cand, "prompt", rng())
    assert seen[-1][0].endswith(bench.WARNING)
    bench.one_try(gw, "a", expect, target, cand, "guard", rng())
    assert cand.quote not in seen[-1][1]


def test_run_fills_cells_and_skips_stored_tries(db, tmp_path):
    t = bench.targets(one_fixture(tmp_path))
    gw = bench.make_gateway(CFG, ["a", "b"], 1.0, factory=factory({"a": leaky, "b": polite}))
    first = bench.run(db, gw, ["a", "b"], "v", t, rng, parallel=2)
    assert first == {"tries": 18, "stopped": None, "skipped": []}           # 2 models x 3 conditions x 3 unanimous tries
    again = bench.run(db, gw, ["a", "b"], "v", t, rng, parallel=2)
    assert again["tries"] == 0
    assert bs.verdicts(db, "v", "b", "hidden-instructions", "none") == ["resisted"] * 3


def test_disagreeing_cell_gets_five_tries(db, tmp_path):
    flip = {"n": 0}

    def sometimes(messages, tools):
        if len(messages) == 2:                                   # a new try starts
            flip["n"] += 1
        return (leaky if flip["n"] % 2 else polite)(messages, tools)
    t = bench.targets(one_fixture(tmp_path))
    gw = bench.make_gateway(CFG, ["a"], 1.0, factory=factory({"a": sometimes}))
    bench.run(db, gw, ["a"], "v", t, rng, parallel=1)
    assert len(bs.verdicts(db, "v", "a", "hidden-instructions", "none")) == 5


def test_cap_stops_cleanly_and_resumes(db, tmp_path):
    def paid(messages, tools):
        return ai("No thanks.", cost=0.01)
    t = bench.targets(one_fixture(tmp_path))
    out = bench.run(db, bench.make_gateway(CFG, ["a"], 0.035, factory=factory({"a": paid})), ["a"], "v", t, rng, parallel=1)
    assert out["stopped"] == "cap" and 1 <= out["tries"] < 9
    kept = sum(len(bs.verdicts(db, "v", "a", "hidden-instructions", c)) for c in bs.CONDITIONS)
    assert kept == out["tries"]
    rest = bench.run(db, bench.make_gateway(CFG, ["a"], 1.0, factory=factory({"a": paid})), ["a"], "v", t, rng, parallel=1)
    assert rest["stopped"] is None and kept + rest["tries"] == 9


def test_a_model_that_always_errors_leaves_gaps(db, tmp_path):
    t = bench.targets(one_fixture(tmp_path))
    gw = bench.make_gateway(CFG, ["x", "b"], 1.0, factory=factory({"x": RuntimeError("gone"), "b": polite}))
    bench.run(db, gw, ["x", "b"], "v", t, rng, parallel=2)
    assert bs.verdicts(db, "v", "x", "hidden-instructions", "none") == ["not_run"] * 3      # each after one retry
    assert bs.verdicts(db, "v", "b", "hidden-instructions", "none") == ["resisted"] * 3


def test_missing_candidate_is_skipped_with_a_note(db, tmp_path):
    d = tmp_path / "bad"
    d.mkdir()
    (d / "plain.json").write_text(json.dumps({"expect": "hidden-instructions", "tools": [
        {"name": "add", "description": "Adds two numbers.", "inputSchema": {"type": "object", "properties": {}}}]}))
    t = bench.targets(d)
    assert t[0][2] is None
    out = bench.run(db, bench.make_gateway(CFG, ["b"], 1.0, factory=factory({"b": polite})), ["b"], "v", t, rng)
    assert out == {"tries": 0, "stopped": None, "skipped": ["hidden-instructions"]}


def test_trial_is_one_try_per_cell_on_two_models(db, tmp_path):
    t = bench.targets(one_fixture(tmp_path))
    gw = bench.make_gateway(CFG, ["a", "b", "x"], 1.0, factory=factory({"a": leaky, "b": polite, "x": polite}))
    out = bench.run(db, gw, ["a", "b", "x"], "v", t, rng, trial=True)
    assert out["tries"] == 6                                                      # 2 models x 3 conditions x 1


ENV = {"REDTEAM_ENABLED": "1", "OPENROUTER_API_KEY": "k", "REDTEAM_FAST_MODELS": "a", "REDTEAM_BENCH_MODELS": "b"}


def test_headroom_refusal(monkeypatch, tmp_path):
    monkeypatch.setattr(bench, "FIXTURES", one_fixture(tmp_path))
    env = {**ENV, "DB_PATH": str(tmp_path / "t.db")}
    fake = factory({"b": polite})
    monkeypatch.setattr(bench, "key_headroom", lambda *a, **k: 0.60)
    assert bench.main(["run"], env=env, factory=fake) == 2                      # 0.60 - 0.50 cap < 0.20
    monkeypatch.setattr(bench, "key_headroom", lambda *a, **k: None)
    assert bench.main(["run"], env=env, factory=fake) == 2                      # unreadable: refuse
    assert bench.main(["run", "--force"], env=env, factory=fake) == 0
    monkeypatch.setattr(bench, "key_headroom", lambda *a, **k: 5.0)
    assert bench.main(["run"], env=env, factory=fake) == 0
    assert bench.main(["publish"], env=env, factory=fake) == 0


def test_disabled_config_refuses(tmp_path):
    assert bench.main(["run"], env={"DB_PATH": str(tmp_path / "t.db")}, factory=factory({})) == 2


def test_version_changes_when_warning_changes(monkeypatch):
    before = bench.current_version()
    monkeypatch.setattr(bench, "WARNING", "something else")
    assert bench.current_version() != before


def test_headroom_reads_limit_minus_usage():
    class Resp:
        def __init__(self, body, code=200):
            self.body, self.status_code = body, code

        def json(self):
            return self.body

    class Http:
        def __init__(self, body):
            self.body = body

        def get(self, url, headers, timeout):
            assert headers["Authorization"] == "Bearer k"
            return Resp(self.body)
    assert bench.key_headroom("k", "https://x/api/v1", http=Http({"data": {"limit": 2.0, "usage": 1.25}})) == pytest.approx(0.75)
    assert bench.key_headroom("k", "https://x/api/v1", http=Http({"data": {"limit": None, "usage": 1.0}})) == float("inf")
    assert bench.key_headroom("k", "https://x/api/v1", http=Http({"nope": 1})) is None
