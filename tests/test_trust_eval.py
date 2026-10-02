import pytest

from core.evidence import verify_quote
from evals.trust_eval import FIXTURES, MIN_PRECISION, MIN_RECALL, evaluate, predict

CASES = sorted(p for p in FIXTURES.iterdir() if p.is_dir())


def test_golden_set_has_ten_cases():
    assert len(CASES) == 10


def test_rules_meet_precision_and_recall_gates():
    result = evaluate()
    assert result["precision"] >= MIN_PRECISION and result["recall"] >= MIN_RECALL, result["cases"]


@pytest.mark.parametrize("case_dir", CASES, ids=lambda p: p.name)
def test_case_hits_expected_and_avoids_forbidden_rules(case_dir):
    case = evaluate()["cases"][case_dir.name]
    assert case["missed"] == [] and case["false_positives"] == [], case


@pytest.mark.parametrize("case_dir", CASES, ids=lambda p: p.name)
def test_every_rule_finding_quote_verifies(case_dir):
    _, findings, snapshot = predict(case_dir)
    for f in findings:
        if f["source"] == "rule":
            assert verify_quote(snapshot["files"], f["file"], f["line"], f["quote"]), f
