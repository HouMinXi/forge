#!/usr/bin/env python3
"""Repeat a fixed workload and compare its median to a stored baseline.

One function, one input, one number. The workload is ``parse_diff_files`` on a
diff that never changes, so a slower result can only come from the function
itself. The median of several runs is less moved by one slow run than a single
sample would be, but it is not immune to one: where that run lands among the
others decides it. A change is reported only when the median moves past a fixed
fraction of the baseline. A test that takes longer does not count here, and
nothing in this script says why a change got slower.
"""

import argparse
import json
import math
import statistics
import time

from code_forge.verify import parse_diff_files

DEFINITION = "forge-perf-regression-v1"
WORKLOAD = "parse_diff_files"
RUNS = 7
SLOWDOWN = 0.5
_FILES = 40
_LINES = 200

# A diff big enough that the loop dominates the call, and fixed so two runs
# measure the same work.
_DIFF = "".join(
    f"diff --git a/f{n}.py b/f{n}.py\n+++ b/f{n}.py\n@@ -1,1 +1,{_LINES} @@\n"
    + "".join(f"+line {i}\n" for i in range(_LINES))
    for n in range(_FILES)
)


def measure(runs: int = RUNS) -> float:
    """Median seconds for one call, over ``runs`` calls.

    The parsed result is checked on every call. A parser that returns less than
    the workload asks for raises, so a run that did less work cannot report a
    time.
    """
    samples = []
    for _ in range(runs):
        start = time.perf_counter()
        parsed = parse_diff_files(_DIFF)
        samples.append(time.perf_counter() - start)
        if len(parsed) != _FILES or any(len(lines) != _LINES for lines in parsed.values()):
            raise RuntimeError("workload parsed to less than it contains")
    return statistics.median(samples)


def compare(baseline: dict, current: float, slowdown: float = SLOWDOWN) -> str:
    """``slower``, ``within``, or ``incomparable``.

    A record that names another definition or workload, or a median that is not
    a finite positive number, cannot be compared. The script then says so
    instead of reporting the change as acceptable.
    """
    if baseline.get("definition") != DEFINITION or baseline.get("workload") != WORKLOAD:
        return "incomparable"
    stored = baseline.get("median_s")
    if not _finite_positive(stored) or not _finite_positive(current):
        return "incomparable"
    if not isinstance(stored, (int, float)) or isinstance(stored, bool):
        return "incomparable"
    try:
        stored = float(stored)
    except OverflowError:
        return "incomparable"
    if current > stored * (1 + slowdown):
        return "slower"
    return "within"


def _finite_positive(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def _jsonable(value):
    """A number JSON can carry, or None.

    NaN and infinity are not numbers here, and neither is an integer too large
    to become a float: JSON would reject it, so the report says null instead.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        if math.isfinite(float(value)):
            return value
    except OverflowError:
        return None
    return None


def report(baseline: dict, current: float) -> dict:
    verdict = compare(baseline, current)
    return {
        "definition": DEFINITION,
        "workload": WORKLOAD,
        "runs": RUNS,
        "baseline_s": _jsonable(baseline.get("median_s")),
        "median_s": _jsonable(current),
        "slowdown_limit": SLOWDOWN,
        "verdict": verdict,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", help="path to a stored record, as JSON")
    parser.add_argument("--record", action="store_true", help="print a fresh median and stop")
    args = parser.parse_args()
    current = measure()
    if args.record or args.baseline is None:
        print(json.dumps({"definition": DEFINITION, "workload": WORKLOAD, "median_s": current}))
        return 0
    try:
        baseline = json.loads(open(args.baseline, encoding="utf-8").read())
    except (OSError, UnicodeError, json.JSONDecodeError):
        print(json.dumps({"definition": DEFINITION, "verdict": "incomparable"}))
        return 2
    if not isinstance(baseline, dict):
        print(json.dumps({"definition": DEFINITION, "verdict": "incomparable"}))
        return 2
    result = report(baseline, current)
    print(json.dumps(result, allow_nan=False))
    return {"slower": 1, "incomparable": 2}.get(result["verdict"], 0)


if __name__ == "__main__":
    raise SystemExit(main())
