# SPDX-License-Identifier: Apache-2.0
"""Run pinned Ruff over the repository or a selected Python diff.

CI uses ``--all`` so pre-existing findings cannot hide outside a PR's diff.
``--base`` remains a fast local check: deleted/non-Python files are omitted,
and an empty selection succeeds without invoking Ruff. Both modes honor the
repository configuration, including intentionally excluded corpus fixtures.

Usage::

    python scripts/lint_changed.py --all
    python scripts/lint_changed.py --base origin/main
    python scripts/lint_changed.py --print-requirement
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Callable, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
_PYTHON_SUFFIXES = (".py", ".pyi")
# With --no-renames a rename is a delete plus an add, so the new path shows up
# as added. Deleted (D) paths are excluded because a deleted file cannot be
# linted. Type-changed (T) paths are excluded too, for example a file that was
# replaced by a symlink, so a change that only swaps a path's type is not linted.
_DIFF_FILTER = "AM"

Runner = Callable[[Sequence[str]], int]


def ruff_requirement(pyproject: Path) -> str:
    """Return the exact ``ruff==X.Y.Z`` requirement from the dev extra."""
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    pins = [
        requirement
        for requirement in data["project"]["optional-dependencies"]["dev"]
        if requirement.startswith("ruff==")
    ]
    if len(pins) != 1:
        raise SystemExit(f"expected exactly one 'ruff==' pin in the dev extra, found {pins!r}")
    return pins[0]


def changed_python_files(base: str, head: str = "HEAD", *, cwd: Path = REPO_ROOT) -> list[str]:
    """List Python files changed between the merge base of ``base`` and ``head``.

    Exits with git's own message if the diff cannot be computed (an unknown
    base, a shallow clone without the merge base), so the check fails closed
    instead of linting nothing.
    """
    result = subprocess.run(
        [
            "git",
            "diff",
            "--name-only",
            "--no-renames",
            "-z",
            f"--diff-filter={_DIFF_FILTER}",
            # Everything after this is a revision, never an option (git 2.24+).
            "--end-of-options",
            f"{base}...{head}",
        ],
        cwd=cwd,
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise SystemExit(
            f"lint_changed: git diff {base}...{head} failed with exit status {result.returncode}: {detail}"
        )
    output = result.stdout
    # -z output ends with a NUL, so the last element is empty and has no suffix.
    names = [name.decode("utf-8", "surrogateescape") for name in output.split(b"\0")]
    return [name for name in names if name.endswith(_PYTHON_SUFFIXES)]


def _subprocess_runner(command: Sequence[str]) -> int:
    return subprocess.run(command, cwd=REPO_ROOT, check=False).returncode


def run_ruff(files: Sequence[str], runner: Runner = _subprocess_runner) -> int:
    """Run both ruff checks on ``files``; return non-zero if either fails."""
    if not files:
        print("lint_changed: no changed Python files; nothing to lint")
        return 0
    status = 0
    for subcommand in (["check"], ["format", "--check"]):
        # --force-exclude keeps the [tool.ruff] exclude list in force for paths
        # given explicitly, which ruff would otherwise lint anyway.
        command = [sys.executable, "-m", "ruff", *subcommand, "--force-exclude", "--", *files]
        status = runner(command) or status
    return status


def _base_ref(value: str) -> str:
    """Reject a base that git could read as an option, or that is blank.

    An empty base would turn ``{base}...HEAD`` into ``HEAD...HEAD``, an empty
    diff, so the check would pass without linting anything.
    """
    if not value.strip() or value != value.strip():
        raise argparse.ArgumentTypeError(
            f"base must be a non-empty ref without surrounding whitespace: {value!r}"
        )
    if value.startswith("-"):
        raise argparse.ArgumentTypeError(f"base must not start with '-': {value!r}")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--base", type=_base_ref, help="git ref to diff against, e.g. origin/main")
    group.add_argument(
        "--print-requirement", action="store_true", help="print the pinned ruff requirement"
    )
    group.add_argument("--all", action="store_true", help="check the entire repository")
    args = parser.parse_args(argv)
    if args.print_requirement:
        print(ruff_requirement(REPO_ROOT / "pyproject.toml"))
        return 0
    return run_ruff(["."] if args.all else changed_python_files(args.base))


if __name__ == "__main__":
    raise SystemExit(main())
