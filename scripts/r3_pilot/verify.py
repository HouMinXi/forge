"""Local sample verifier for a trusted producer; not an attestation."""
from copy import deepcopy
from pathlib import Path
import argparse
import json
import sys

REV = "12fbfac533f19c8d167500aa372543e4d2696089"
NODE = "tests/test_e2e_check.py::TestGroupSourceFiles::test_two_top_dirs_produce_two_groups"
SOURCE = "src/code_forge/e2e_check.py"
TEST_HASH = "302edd36eb8e2160071f328103679a73c1adff1aef6d0720983a52493d0e78dd"
SOURCE_HASH = "ae6b4d1374e98b976e1b68e580adb4d34db0d17daa102377263a633e83ed756e"
VARIANT_HASH = "3a4dad81c3f6948a006674bf769231b74545c2372e21efa5a2f29db5804173a0"
CONTROL = {
    "baseline": (SOURCE_HASH, 0, "passed", ["a", "b"]),
    "variant": (VARIANT_HASH, 1, "failed", ["one_group", "one_group"]),
    "restored": (SOURCE_HASH, 0, "passed", ["a", "b"]),
}

def require(condition, reason):
    if not condition:
        raise ValueError(reason)

def verify(record, stage):
    sha, exitcode, call_outcome, keys = CONTROL[stage]
    for key, expected in {
        "revision": REV, "node": NODE, "phase": stage, "source_sha256": sha,
        "test_sha256": TEST_HASH, "source_line": 151, "exit": exitcode,
    }.items():
        require(type(record.get(key)) is type(expected) and record.get(key) == expected, key)
    reports = record.get("reports")
    require(isinstance(reports, list) and len(reports) == 3, "missing execution reports")
    for report, when, outcome in zip(reports, ("setup", "call", "teardown"),
                                     ("passed", call_outcome, "passed"), strict=True):
        require(report.get("node") == NODE and report.get("phase") == when, "record node/phase")
        require(report.get("outcome") == outcome, "report outcome")
        failure = report.get("failure")
        if when == "call" and stage == "variant":
            require(isinstance(failure, str) and "assert {'one_group'} == {'a', 'b'}" in failure
                    and "tests/test_e2e_check.py:206: AssertionError" in failure, "wrong target assertion")
        else:
            require(failure is None, "unexpected failure")
    events = record.get("trace_events")
    require(isinstance(events, list) and len(events) == len(keys), "missing target trace")
    for event, key in zip(events, keys, strict=True):
        require(event == {"event": "line", "node": NODE, "phase": "call", "file": SOURCE,
                          "line": 151, "group_key": key}, "event not bound to target node/call/line")

def controls(original):
    variants = {
        "wrong revision": ("revision", "0"*40), "wrong source": ("source_sha256", "0"*64),
        "wrong test": ("test_sha256", "0"*64), "wrong node": ("node", "unrelated"),
        "collection only": ("reports", []), "missing trace": ("trace_events", []),
    }
    for name, (key, value) in variants.items():
        bad = deepcopy(original)
        bad[key] = value
        yield name, bad
    for phase in ("setup", "teardown"):
        bad = deepcopy(original)
        for event in bad["trace_events"]:
            event["phase"] = phase
        yield f"passing call with {phase}-only target event", bad
    bad = deepcopy(original)
    bad["trace_events"][0]["node"] = "unrelated"
    yield "passing call with unrelated-node target event", bad

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--negative-controls", action="store_true")
    args = parser.parse_args()
    rows = {stage: json.loads((args.directory / f"{stage}.json").read_text()) for stage in CONTROL}
    for stage, row in rows.items():
        verify(row, stage)
        print(f"ACCEPT {stage}")
    if args.negative_controls:
        for name, bad in controls(rows["baseline"]):
            try:
                verify(bad, "baseline")
            except ValueError as exc:
                print(f"REJECT {name}: {exc}")
            else:
                raise SystemExit(f"FAIL OPEN: {name}")

if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
        sys.exit(f"REJECT: {exc}")
