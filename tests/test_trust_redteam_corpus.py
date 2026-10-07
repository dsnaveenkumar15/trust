import json
from pathlib import Path

import pytest

from trust.redteam import analyst, reach, scenarios
from trust.redteam.inputs import from_manifest

ROOT = Path(__file__).parent / "fixtures" / "trust_redteam"
BAD = sorted((ROOT / "bad").glob("*.json"))
CLEAN = sorted((ROOT / "clean").glob("*.json"))


def load(p):
    data = json.loads(p.read_text(encoding="utf-8"))
    return data, from_manifest(json.dumps({"tools": data["tools"]}))


def test_corpus_size():
    assert len(BAD) == 8 and len(CLEAN) == 4
    assert {json.loads(p.read_text(encoding="utf-8"))["expect"] for p in BAD} == set(scenarios.catalog())


@pytest.mark.parametrize("path", BAD, ids=lambda p: p.stem)
def test_every_bad_case_gives_its_scenario_a_foothold(path):
    data, t = load(path)
    assert data["expect"] in {c.scenario for c in analyst.candidates(t, reach.reach_map(t))}


@pytest.mark.parametrize("path", CLEAN, ids=lambda p: p.stem)
def test_clean_cases_give_no_description_attack_a_foothold(path):
    _, t = load(path)
    found = {c.scenario for c in analyst.candidates(t, reach.reach_map(t))}
    assert not found & {"hidden-instructions", "hide-from-user", "encoded-payload", "tool-shadowing", "secret-file-read"}


def test_the_launch_gate_script_imports_and_scores_a_result():
    from evals import redteam_eval
    got = {"scenarios": [{"id": "impersonation", "verdict": "got_through"}], "static": []}
    assert redteam_eval.caught(got, "impersonation") and not redteam_eval.caught(got, "tool-shadowing")
    assert redteam_eval.caught({"scenarios": [], "static": [{"rule": "hidden-instructions"}]}, "anything")   # rules caught it
    assert redteam_eval.false_alarm(got) and not redteam_eval.false_alarm({"scenarios": [{"verdict": "resisted"}], "static": []})
    assert redteam_eval.main.__name__ == "main"


def test_the_lean_gate_attacks_only_the_expected_scenario_and_runs_clean_lists_in_full():
    from evals import redteam_eval
    bad = {"expect": "impersonation", "tools": [{"name": "read_file", "description": "Reads."}]}
    clean = {"expect": None, "tools": [{"name": "add", "description": "Adds."}]}
    lean = redteam_eval.case_input(bad, lean=True)
    assert lean["only"] == "impersonation" and lean["fixcheck"] is False and lean["kind"] == "manifest" and lean["consent"] is True
    assert "only" not in redteam_eval.case_input(bad, lean=False) and "only" not in redteam_eval.case_input(clean, lean=True)


def test_the_lean_verdict_counts_the_agent_and_the_rules_separately():
    from evals import redteam_eval
    both = {"scenarios": [{"id": "impersonation", "verdict": "got_through"}], "static": [{"rule": "name-collision"}]}
    rules_only = {"scenarios": [], "static": [{"rule": "name-collision"}]}
    assert redteam_eval.by_agent(both, "impersonation") and not redteam_eval.by_agent(rules_only, "impersonation")
    assert redteam_eval.caught(rules_only, "impersonation")


def test_the_lean_gate_runs_end_to_end_with_fake_models_and_prints_a_verdict(monkeypatch, capsys):
    """A free dry run of the whole script (13 runs: 8 lean bad, 4 clean, 1 extra full) so a typo cannot waste paid money."""
    from evals import redteam_eval
    from tests.trust_redteam_fakes import FakeModel, leaky
    for k, v in {"REDTEAM_ENABLED": "1", "OPENROUTER_API_KEY": "k", "REDTEAM_FAST_MODELS": "v", "REDTEAM_STRONG_MODELS": "a"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(redteam_eval, "chat_factory", lambda cfg: (lambda name: FakeModel(analyst=[RuntimeError("x")], victim=leaky)))
    code = redteam_eval.main([])
    out = capsys.readouterr().out
    assert out.count("ok ") + out.count("BAD") == 13 and "(extra full run)" in out
    assert "caught 8/8" in out and "LEAN GATE" in out and code in (0, 1)
    assert out.count("lean $") == 8                       # the eight bad lists ran lean, the clean ones in full
