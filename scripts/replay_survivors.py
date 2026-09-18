"""Replay a historical mutmut survivor ledger against the current tests.

The ledger lists mutants that survived on an older revision of git.py.
Each entry carries the original source line and the mutated one. This
script re-applies each single-line substitution to the current source,
runs the test selection, and reports whether the tests now catch it.

A mutant whose original line no longer exists is reported as STALE: the
code moved on, so the old survivor says nothing about today's tests.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

HEADER = re.compile(r"^=== (\S+) \[(survived|no_tests)\] (\S+) ")
ORIG = re.compile(r"^  L(\d+): (.*)$")
MUT = re.compile(r"^  --> (.*)$")

_GENERATED_WRAPPER = re.compile(r"^def x_\w+__mutmut_")


def _is_scaffold(line: str) -> bool:
    """True for mutmut's own bookkeeping lines rather than real source."""
    s = line.strip()
    if not s or s == "<<ABSENT>>":
        return True
    if s.startswith("mutants_x_"):
        return True
    return bool(_GENERATED_WRAPPER.match(s))


def parse(path: Path):
    """Yield (name, kind, [(orig, mutated), ...]) for each ledger entry."""
    entries = []
    name = kind = None
    pairs: list[tuple[str, str]] = []
    pending: str | None = None
    for raw in path.read_text().splitlines():
        m = HEADER.match(raw)
        if m:
            if name and pairs:
                entries.append((name, kind, pairs))
            name, kind, pairs, pending = m.group(1), m.group(3), [], None
            continue
        mo = ORIG.match(raw)
        if mo:
            pending = mo.group(2)
            continue
        mm = MUT.match(raw)
        if mm and pending is not None:
            orig, mutated = pending, mm.group(1)
            pending = None
            if _is_scaffold(orig) or _is_scaffold(mutated):
                continue
            if orig.strip() == mutated.strip():
                continue
            pairs.append((orig, mutated))
    if name and pairs:
        entries.append((name, kind, pairs))
    return entries


def _baseline_ok(tests: list[str], repo_root: Path, env: dict[str, str]) -> tuple[bool, str]:
    """Run the selection unmutated; it must pass before any replay."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *tests, "-q", "--tb=short", "-p", "no:cacheprovider"],
        capture_output=True,
        check=False,
        text=True,
        env=env,
        cwd=repo_root,
    )
    return proc.returncode == 0, (proc.stdout + proc.stderr)[-600:]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ledger", type=Path)
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--tests", nargs="+", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument(
        "--repo-root",
        type=Path,
        help="Repository root; defaults to the git toplevel of --source.",
    )
    args = ap.parse_args()

    source = args.source.resolve()
    if args.repo_root:
        repo_root = args.repo_root.resolve()
    else:
        top = subprocess.run(
            ["git", "-C", str(source.parent), "rev-parse", "--show-toplevel"],
            capture_output=True,
            check=False,
            text=True,
        )
        if top.returncode != 0:
            print("cannot resolve repo root; pass --repo-root", file=sys.stderr)
            return 2
        repo_root = Path(top.stdout.strip())
    print(f"repo root: {repo_root}")

    env = dict(os.environ)
    # Derive the import root from the source layout rather than assuming
    # a src/ directory exists.
    try:
        pkg_root = source.parent.parent
        env["PYTHONPATH"] = str(pkg_root.relative_to(repo_root))
    except ValueError:
        env["PYTHONPATH"] = str(source.parent.parent)

    ok, tail = _baseline_ok(args.tests, repo_root, env)
    if not ok:
        print("baseline test selection does not pass; replay would be meaningless")
        print(tail)
        return 2

    entries = parse(args.ledger)
    if args.limit:
        entries = entries[: args.limit]
    print(f"ledger entries with usable substitutions: {len(entries)}")

    original = args.source.read_text()
    verdicts: Counter[str] = Counter()
    survivors: list[str] = []
    harness_errors: list[str] = []
    partial: list[str] = []
    stale: list[str] = []

    try:
        for idx, (name, kind, pairs) in enumerate(entries, 1):
            text = original
            applied = 0
            for orig, mutated in pairs:
                if text.count(orig) != 1:
                    continue
                text = text.replace(orig, mutated)
                applied += 1
            if applied == 0:
                verdicts["STALE"] += 1
                stale.append(f"{name} [{kind}]")
                continue
            if applied != len(pairs):
                # Only some of the mutant's lines still match. Applying a
                # subset produces code the mutation engine never emitted,
                # usually broken syntax, so the result says nothing.
                verdicts["PARTIAL"] += 1
                partial.append(f"{name} [{kind}] {applied}/{len(pairs)}")
                continue
            try:
                compile(text, str(args.source), "exec")
            except SyntaxError as exc:
                # The ledger line-pair does not reconstruct valid code on
                # this revision. Running it would only test the parser.
                verdicts["PARTIAL"] += 1
                partial.append(f"{name} [{kind}] syntax: {exc.msg}")
                continue

            args.source.write_text(text)
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", *args.tests, "-q", "--tb=no", "-p", "no:cacheprovider"],
                capture_output=True,
                check=False,
                text=True,
                env=env,
                cwd=repo_root,
            )
            # pytest exit codes: 0 all passed (the mutant survived),
            # 1 tests failed (killed), 5 nothing collected, >=2 the run
            # itself broke. Only 1 is evidence of a kill.
            if proc.returncode == 0:
                verdicts["SURVIVED"] += 1
                survivors.append(f"{name} [{kind}] applied={applied}")
            elif proc.returncode == 1:
                verdicts["KILLED"] += 1
            else:
                verdicts["HARNESS_ERROR"] += 1
                harness_errors.append(
                    f"{name} rc={proc.returncode} {proc.stderr.strip()[:160]}"
                )
            if idx % 20 == 0:
                print(f"  ... {idx}/{len(entries)} {dict(verdicts)}", flush=True)
    finally:
        args.source.write_text(original)

    print("\n=== replay verdict ===")
    for k, v in sorted(verdicts.items()):
        print(f"{k:10s} {v}")
    if survivors:
        print("\n--- still surviving ---")
        for s in survivors:
            print(" ", s)
    if harness_errors:
        print(f"\n--- harness errors (not kills): {len(harness_errors)} ---")
        for h in harness_errors[:10]:
            print(" ", h)
    if partial:
        print(f"\n--- partially applied (inconclusive): {len(partial)} ---")
        for pz in partial[:10]:
            print(" ", pz)
    if stale:
        print(f"\n--- stale (original line gone): {len(stale)} ---")
        for s in stale[:10]:
            print(" ", s)
    return 1 if (survivors or harness_errors) else 0


if __name__ == "__main__":
    raise SystemExit(main())
