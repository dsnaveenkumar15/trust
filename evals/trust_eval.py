"""Golden-set evaluation of the Trust rules. CI fails if precision or recall on critical/high rules drops."""
import json
import sys
import time
from pathlib import Path

from trust.local import load_local
from trust.pipeline import stage_extract, stage_rules

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "trust"
GATED_SEVERITIES = ("critical", "high")
MIN_PRECISION = 0.9
MIN_RECALL = 0.8


def predict(case_dir: Path) -> tuple[set[str], list[dict], dict]:
    snapshot = load_local(case_dir)
    findings = stage_rules(snapshot, stage_extract(snapshot), time.time())["findings"]
    return {f["rule"] for f in findings if f["severity"] in GATED_SEVERITIES}, findings, snapshot


def evaluate(fixtures: Path = FIXTURES) -> dict:
    cases, tp, fp, fn = {}, 0, 0, 0
    for case_dir in sorted(p for p in fixtures.iterdir() if p.is_dir()):
        expected = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))
        predicted, _, _ = predict(case_dir)
        must, must_not = set(expected["must"]), set(expected["must_not"])
        hits, false_positives, missed = must & predicted, must_not & predicted, must - predicted
        tp, fp, fn = tp + len(hits), fp + len(false_positives), fn + len(missed)
        cases[case_dir.name] = {"hits": sorted(hits), "false_positives": sorted(false_positives),
                                "missed": sorted(missed)}
    return {"precision": tp / (tp + fp) if tp + fp else 1.0, "recall": tp / (tp + fn) if tp + fn else 1.0,
            "cases": cases}


def main() -> int:
    result = evaluate()
    for name, case in result["cases"].items():
        print(f"{name:14} hits={case['hits']} false_positives={case['false_positives']} missed={case['missed']}")
    print(f"precision={result['precision']:.2f} (min {MIN_PRECISION})  recall={result['recall']:.2f} (min {MIN_RECALL})")
    return 0 if result["precision"] >= MIN_PRECISION and result["recall"] >= MIN_RECALL else 1


if __name__ == "__main__":
    sys.exit(main())
