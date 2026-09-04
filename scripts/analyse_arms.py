"""Turn arm ledgers into the numbers Phase 58 reports.

Every headline number has to trace back to lines in the run artifacts, so
each metric carries its n and the ledger it came from. Standard error is
reported where there is enough data to compute one and printed as n/a where
there is not -- at one run per entry there is no within-entry spread, and a
0.0 in that slot reads as "no variance measured" rather than "no variance".

Two ledger shapes exist. The 58-3 depth arms were launched before finding
counts were recorded, so they carry verdicts only; 58-4 arms carry both.
The analyser reports what each ledger can support and says so, rather than
inventing zeros for fields that were never written.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
from collections import Counter


def load(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]


def _mean_se(values: list[float]) -> tuple[float | None, float | None]:
    """Mean and standard error, or (mean, None) when SE is undefined.

    None rather than 0.0. A standard error of zero is a measurement -- it
    says repeated runs agreed. Printing it for n=1, where nothing was
    repeated, states a result that was never observed.
    """
    if not values:
        return None, None
    mean = sum(values) / len(values)
    if len(values) < 2:
        return mean, None
    var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return mean, math.sqrt(var / len(values))


def _fmt(value: float | None, pct: bool = False) -> str:
    if value is None:
        return "n/a"
    return "%.1f%%" % (value * 100) if pct else "%.3f" % value


def summarise(rows: list[dict], label: str) -> dict:
    """Per-arm summary, reporting only what the ledger actually carries.

    Rows arrive in ledger order, so their index is a coordinate a reader can
    use: line N of the file. Every aggregate records which lines it came
    from, because a number nobody can trace back to specific rows cannot be
    checked -- and checking is the only thing that separates a measurement
    from an assertion.
    """
    scored = [
        (i, r) for i, r in enumerate(rows, 1) if "finding_hits" in r
    ]
    walls = [r["wall_s"] for r in rows if r.get("wall_s")]
    wall_mean, wall_se = _mean_se(walls)

    verdict_lines: dict[str, list[int]] = {}
    for i, r in enumerate(rows, 1):
        verdict_lines.setdefault(r.get("verdict", "?"), []).append(i)

    out = {
        "label": label,
        "entries": len(rows),
        "verdicts": Counter(r.get("verdict", "?") for r in rows),
        "verdict_lines": verdict_lines,
        "wall_mean": wall_mean,
        "wall_se": wall_se,
        "wall_total_h": sum(walls) / 3600.0 if walls else 0.0,
        "scored": len(scored),
        "scored_lines": [i for i, _ in scored],
        "precision": None,
        "recall": None,
        "f1": None,
        "hits": 0,
        "misses": 0,
        "fps": 0,
    }
    if not scored:
        return out

    hits = sum(r["finding_hits"] for _, r in scored)
    misses = sum(r["finding_misses"] for _, r in scored)
    fps = sum(r["finding_fps"] for _, r in scored)
    out.update(hits=hits, misses=misses, fps=fps)

    # Zero denominators stay None. A precision of 0.0 when nothing was
    # reported claims the reviewer was wrong every time; it reported
    # nothing, which is a different result.
    if hits + fps:
        out["precision"] = hits / (hits + fps)
    if hits + misses:
        out["recall"] = hits / (hits + misses)
    p, r = out["precision"], out["recall"]
    if p is not None and r is not None and (p + r):
        out["f1"] = 2 * p * r / (p + r)
    return out


def _lines(nums: list[int], cap: int = 6) -> str:
    """Render line numbers compactly, keeping them checkable.

    Truncation names the count it hid rather than trailing off, so a reader
    knows whether they are looking at the whole set.
    """
    if len(nums) <= cap:
        return ",".join(str(n) for n in nums)
    head = ",".join(str(n) for n in nums[:cap])
    return "%s (+%d more)" % (head, len(nums) - cap)


def print_arm(s: dict, source: pathlib.Path) -> None:
    print("%s  (n=%d, %s)" % (s["label"], s["entries"], source.name))
    verdicts = ", ".join(
        "%s=%d" % (k, v) for k, v in sorted(s["verdicts"].items())
    )
    print("  verdicts:   %s" % verdicts)
    for name in sorted(s["verdict_lines"]):
        print("    %-9s lines %s" % (
            name, _lines(s["verdict_lines"][name])))
    print("  wall/entry: %s s (SE %s)  total %.1f h" % (
        "%.0f" % s["wall_mean"] if s["wall_mean"] else "n/a",
        "%.0f" % s["wall_se"] if s["wall_se"] is not None else "n/a",
        s["wall_total_h"],
    ))
    if s["scored"]:
        print("  findings:   hits=%d misses=%d fps=%d  (scored %d/%d)" % (
            s["hits"], s["misses"], s["fps"], s["scored"], s["entries"]))
        print("    scored lines %s" % _lines(s["scored_lines"]))
        print("  precision=%s recall=%s f1=%s" % (
            _fmt(s["precision"], pct=True),
            _fmt(s["recall"], pct=True),
            _fmt(s["f1"]),
        ))
    else:
        print("  findings:   not recorded in this ledger "
              "(verdict-only; arm predates finding-count recording)")
    print()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Summarise Phase 58 arm ledgers",
    )
    ap.add_argument("ledgers", nargs="+", type=pathlib.Path)
    args = ap.parse_args(argv)

    summaries = []
    for path in args.ledgers:
        rows = load(path)
        if not rows:
            print("%s: empty or missing" % path)
            continue
        depths = {r.get("depth") for r in rows}
        engines = {r.get("engine") for r in rows}
        # An arm whose rows disagree about their own coordinates did not run
        # one experiment, and averaging across it would hide that.
        if len(depths) > 1 or len(engines) > 1:
            print("%s: MIXED COORDINATES depth=%s engine=%s -- not scorable"
                  % (path,
                     sorted(str(d) for d in depths),
                     sorted(str(e) for e in engines)))
            continue
        label = "depth=%s engine=%s" % (depths.pop(), engines.pop())
        s = summarise(rows, label)
        summaries.append(s)
        print_arm(s, path)

    scored = [s for s in summaries if s["scored"]]
    if len(scored) >= 2:
        base = scored[0]
        print("deltas against %s:" % base["label"])
        for s in scored[1:]:
            for metric in ("precision", "recall", "f1"):
                a, b = base[metric], s[metric]
                if a is None or b is None:
                    print("  %-10s %s: n/a" % (metric, s["label"]))
                    continue
                print("  %-10s %s: %+.1f pts" % (
                    metric, s["label"], (b - a) * 100))
        print()
        print("Note: differences are point estimates at one run per entry. "
              "With no within-entry replicates there is no standard error "
              "to compare them against, so a small difference is not "
              "evidence of a difference.")
    elif summaries:
        print("Only %d arm(s) carry finding counts; no comparison possible."
              % len(scored))
    return 0


if __name__ == "__main__":
    sys.exit(main())
