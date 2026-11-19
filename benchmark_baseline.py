"""Baseline benchmark: test LLM knowledge alone without live web search."""
import json
import sys
from datetime import date

from main import ask_json, JUDGE_MODEL


def main(path: str = "benchmark_claims.json"):
    with open(path, encoding="utf-8") as f:
        items = json.load(f)

    tp = fp = tn = fn = 0
    today = date.today().isoformat()
    system_prompt = (
        f"Today is {today}. Decide whether the statement is still accurate today, "
        'using only your own knowledge. Reply ONLY with JSON: {"stale": true or false}.'
    )

    for item in items:
        result = {}
        try:
            result = ask_json(JUDGE_MODEL, system_prompt, item["text"])
        except Exception:
            pass

        predicted_stale = bool(result.get("stale"))
        expected_stale = item["expected_stale"]

        tp += predicted_stale and expected_stale
        fp += predicted_stale and not expected_stale
        tn += (not predicted_stale) and (not expected_stale)
        fn += (not predicted_stale) and expected_stale

        print(f"expected_stale={expected_stale!s:5} predicted={predicted_stale!s:5} | {item['text'][:60]}")

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    accuracy = (tp + tn) / len(items) if items else 0.0

    print(
        f"\nBASELINE (LLM only, no search): N={len(items)} "
        f"precision={precision:.2f} recall={recall:.2f} accuracy={accuracy:.2f}"
    )


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "benchmark_claims.json")
