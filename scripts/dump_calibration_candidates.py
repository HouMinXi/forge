#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Dump the L1 findings the falsifier actually saw, for calibration labelling.

The eval ledgers keep counts, not findings, and the runner removes each
entry's temp dir after scoring. This script re-runs the named entries once
at depth 1 with replay_entry(keep_state_dir=...), then flattens every
finding from the kept state.json files into <out>/candidates.json.

    PYTHONPATH=src /usr/bin/python3 scripts/dump_calibration_candidates.py \\
        --corpus .eval-corpus/corpus.yaml \\
        --entries $(paste -sd, tests/calibration/falsify/entries.txt) \\
        --out .planning/eval/phase-59/calibration-raw

Serial on purpose: 20 entries at ~5 min each, and the point is the
state files, not throughput.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from code_forge.eval.corpus import load_corpus  # noqa: E402
from code_forge.eval.runner import replay_entry  # noqa: E402


def _backend_config(gate_path: Path, name: str) -> dict:
    data = yaml.safe_load(gate_path.read_text()) or {}
    entry = (data.get("backends") or {}).get(name)
    if not isinstance(entry, dict):
        sys.exit("backend %r not in %s" % (name, gate_path))
    return dict(entry)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True, type=Path)
    ap.add_argument("--entries", required=True,
                    help="comma-separated entry ids")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--backend", default="review-default")
    ap.add_argument("--gate", type=Path,
                    default=Path(".code-forge/gate.yaml"))
    ap.add_argument("--depth", type=int, default=1)
    args = ap.parse_args()

    wanted = [e for e in args.entries.split(",") if e]
    entries = {e.name: e for e in load_corpus(args.corpus)}
    missing = [w for w in wanted if w not in entries]
    if missing:
        sys.exit("not in corpus: %s" % ", ".join(missing))

    backend_config = _backend_config(args.gate, args.backend)
    os.environ["FORGE_CLEAN_ROUND_THRESHOLD"] = str(args.depth)
    os.environ["FORGE_MAX_TOTAL_ROUNDS"] = str(args.depth)
    args.out.mkdir(parents=True, exist_ok=True)
    corpus_dir = args.corpus.parent

    for i, name in enumerate(wanted, 1):
        t0 = time.monotonic()
        r = replay_entry(entries[name], corpus_dir, args.backend, runs=1,
                         backend_config=backend_config,
                         keep_state_dir=str(args.out))
        print("[%2d/%d] %-45s %-8s %5.0fs" % (
            i, len(wanted), name, r.actual_verdict,
            time.monotonic() - t0), flush=True)

    candidates = []
    for name in wanted:
        p = args.out / name / "state.json"
        if not p.exists():
            print("  no state for %s" % name, file=sys.stderr)
            continue
        for f in json.loads(p.read_text()).get("findings", []):
            if f.get("source") != "L1" or not f.get("file"):
                continue
            candidates.append({
                "entry": name,
                "fingerprint": f["fingerprint"],
                "file": f["file"],
                "line_range": f["line_range"],
                "description": f["description"],
                "disposition": f["disposition"],
            })
    (args.out / "candidates.json").write_text(
        json.dumps(candidates, indent=2) + "\n")
    print("candidates: %d from %d entries" % (
        len(candidates), len({c["entry"] for c in candidates})))
    return 0


if __name__ == "__main__":
    sys.exit(main())
