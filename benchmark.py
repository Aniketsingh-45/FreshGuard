"""Measure FreshGuard accuracy on a hand-labelled claim set.

Usage:  python benchmark.py benchmark_claims.json
Each item: {"text": "...", "expected_stale": true|false}
A text counts as predicted-stale if any claim is outdated/contradicted.
"""
import json
import sys
import time

from main import run_check

STALE = {"outdated", "contradicted"}


def main(path: str):
    items = json.load(open(path, encoding="utf-8"))
    tp = fp = tn = fn = 0
    latencies = []
    for item in items:
        start = time.time()
        result = run_check(item["text"], max_claims=3)
        latencies.append(time.time() - start)
        predicted = any(c["verdict"] in STALE for c in result["claims"])
        expected = item["expected_stale"]
        tp += predicted and expected
        fp += predicted and not expected
        tn += (not predicted) and (not expected)
        fn += (not predicted) and expected
        print(f"expected_stale={expected!s:5} predicted={predicted!s:5} | {item['text'][:60]}")
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    accuracy = (tp + tn) / len(items)
    print(f"\nN={len(items)}  precision={precision:.2f}  recall={recall:.2f}  "
          f"accuracy={accuracy:.2f}  avg_latency={sum(latencies)/len(latencies):.1f}s")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "benchmark_claims.json")
