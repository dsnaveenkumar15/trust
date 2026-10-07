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
