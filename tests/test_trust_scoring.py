from trust.models import CATEGORIES, make_finding
from trust.scoring import Score, score


def f(rule="r", category="capabilities", severity="high", confidence="high"):
    return make_finding(rule, category, severity, confidence, "a.py", 1, "abcd", "m")


def test_clean_repo_scores_a():
    assert score([]) == Score(100, "A", {c: 100 for c in CATEGORIES}, False)


def test_single_high_finding():
    s = score([f("shell-exec")])
    assert (s.categories["capabilities"], s.total, s.grade) == (80, 96, "A")


def test_two_distinct_high_rules_drop_to_b():
    s = score([f("shell-exec"), f("dynamic-eval", confidence="medium")])
    assert (s.categories["capabilities"], s.total, s.grade) == (68, 94, "B")


def test_critical_caps_grade_at_d():
    s = score([f("hidden-instructions", "injection", "critical")])
    assert (s.total, s.grade, s.capped_by_critical) == (88, "D", True)


def test_low_confidence_critical_does_not_cap():
    s = score([f("hidden-instructions", "injection", "critical", "low")])
    assert (s.grade, s.capped_by_critical) == ("A", False)


def test_per_rule_cap_stops_one_noisy_rule_dominating():
    s = score([f("shell-exec")] * 10)
    assert (s.categories["capabilities"], s.grade) == (60, "B")


def test_order_independent():
    items = [f("a", "injection", "critical"), f("b"), f("c", "exfiltration", "medium", "low")]
    assert score(items) == score(list(reversed(items)))


def test_many_serious_findings_reach_f():
    items = ([f(r, "injection", "critical") for r in ("a", "b", "c")]
             + [f(r, "exfiltration") for r in ("d", "e", "g")]
             + [f(r, "capabilities") for r in ("h", "i")])
    s = score(items)
    assert (s.total, s.grade) == (47, "F")
