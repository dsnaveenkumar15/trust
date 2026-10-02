"""Public, deterministic scoring. Same findings in, same score out. The LLM never touches this."""
from collections import defaultdict
from dataclasses import dataclass

from trust.models import CATEGORIES, Finding

SCORING_VERSION = "1.0.0"
SEVERITY_POINTS = {"critical": 40, "high": 20, "medium": 8, "low": 3}
CONFIDENCE_FACTOR = {"high": 1.0, "medium": 0.6, "low": 0.3}
CATEGORY_WEIGHTS = {"injection": 0.30, "capabilities": 0.20, "exfiltration": 0.25, "supply_chain": 0.15,
                    "transparency": 0.10}
GRADE_BANDS = ((95, "A"), (85, "B"), (70, "C"), (55, "D"))
RULE_CAP_MULTIPLIER = 2   # one rule can cost a category at most 2x its severity points


@dataclass(frozen=True)
class Score:
    total: int
    grade: str
    categories: dict
    capped_by_critical: bool


def _points(finding: Finding) -> float:
    return SEVERITY_POINTS[finding.severity] * CONFIDENCE_FACTOR[finding.confidence]


def score(findings: list[Finding]) -> Score:
    penalties = {c: 0.0 for c in CATEGORIES}
    per_rule: dict[tuple[str, str], float] = defaultdict(float)
    for finding in sorted(findings, key=lambda x: (x.category, x.rule, -_points(x), x.file, x.line)):
        key = (finding.rule, finding.category)
        cap = SEVERITY_POINTS[finding.severity] * RULE_CAP_MULTIPLIER
        added = min(_points(finding), max(0.0, cap - per_rule[key]))
        per_rule[key] += added
        penalties[finding.category] += added
    categories = {c: max(0, round(100 - penalties[c])) for c in CATEGORIES}
    total = round(sum(categories[c] * weight for c, weight in CATEGORY_WEIGHTS.items()))
    grade = next((g for floor, g in GRADE_BANDS if total >= floor), "F")
    critical = any(f.severity == "critical" and f.confidence in ("high", "medium") for f in findings)
    capped = critical and grade in ("A", "B", "C")
    return Score(total, "D" if capped else grade, categories, capped)
