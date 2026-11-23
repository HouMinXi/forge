#!/usr/bin/env python3
"""Repeat a fixed workload and compare its median to a stored baseline.

One function, one input, one number. The workload is ``parse_diff_files`` on a
diff that never changes, so a slower result can only come from the function
itself. The median of several runs drops a single slow run; a change is reported
only when the median moves past a fixed fraction of the baseline. A test that
takes longer does not count here, and nothing in this script says why a change
got slower.
"""

import argparse
import json
import statistics
import time

from code_forge.verify import parse_diff_files

DEFINITION = "forge-perf-regression-v1"
WORKLOAD = "parse_diff_files"
RUNS = 7
SLOWDOWN = 0.5

# A diff big enough that the loop dominates the call, and fixed so two runs
# measure the same work.
_DIFF = "".join(
    f"diff --git a/f{n}.py b/f{n}.py\n+++ b/f{n}.py\n@@ -1,1 +1,200 @@\n"
    + "".join(f"+line {i}\n" for i in range(200))
    for n in range(40)
)


def measure(runs: int = RUNS) -> float:
    """Median seconds for one call, over ``runs`` calls."""
    samples = []
    for _ in range(runs):
        start = time.perf_counter()
        parse_diff_files(_DIFF)
        samples.append(time.perf_counter() - start)
    return statistics.median(samples)


def compare(baseline: float, current: float, slowdown: float = SLOWDOWN) -> str:
    """``slower`` when the median grew past ``slowdown`` of the baseline."""
    if baseline <= 0:
        raise ValueError("baseline must be positive")
    if current > baseline * (1 + slowdown):
        return "slower"
    return "within"


def report(baseline: float, current: float) -> dict:
    verdict = compare(baseline, current)
    return {
        "definition": DEFINITION,
        "workload": WORKLOAD,
        "runs": RUNS,
        "baseline_s": baseline,
        "median_s": current,
        "slowdown_limit": SLOWDOWN,
        "verdict": verdict,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=float, help="stored median, in seconds")
    parser.add_argument("--record", action="store_true", help="print a fresh median and stop")
    args = parser.parse_args()
    current = measure()
    if args.record or args.baseline is None:
        print(json.dumps({"definition": DEFINITION, "workload": WORKLOAD, "median_s": current}))
        return 0
    result = report(args.baseline, current)
    print(json.dumps(result))
    return 1 if result["verdict"] == "slower" else 0


if __name__ == "__main__":
    raise SystemExit(main())
