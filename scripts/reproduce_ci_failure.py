#!/usr/bin/env python3
"""Resolve a published failure ordinal back to its test and run it locally.

The CI diagnostic publishes only fixed ordinals (`public-N`) so that no runtime
text leaves the runner. The ordinal is an index into the reviewed public
identity manifest that ships with the repository: entry N of `nodes` is the
exact node id behind `public-N`. This tool reads that manifest, checks the
checkout is the revision the run used, and runs the single test with a full
traceback on the machine that already has the private context.

Usage:

    python3 scripts/reproduce_ci_failure.py --run 42061408999 --id public-20349
    python3 scripts/reproduce_ci_failure.py --run 42061408999 --id public-20349 --dry-run

Requires the `gh` CLI, authenticated for the repository.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NoReturn

REPO = "HouMinXi/forge"
PLAN_ARTIFACT = "linux-collection-plan"
PLAN_FILE = "plan.json"
ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "scripts" / "ci_public_test_identities.json"
COMMAND_TIMEOUT_S = 300
PYTEST_TIMEOUT_S = 1800
ORDINAL = re.compile(r"public-(0|[1-9][0-9]*)")
# A manifest entry must stay a path under tests/. Parameter ids may contain
# spaces, so argv safety comes from the shape here, not from quoting: a node
# id that begins with "-" would reach pytest as an option and load a plugin.
NODE_ID = re.compile(r"tests/[^:\s]+\.py(?:.+)?")
# The path half of a node id is the part that can escape the checkout. The
# parameter half may legitimately contain dots, slashes and spaces.


def _die(message: str) -> NoReturn:
    print(f"STOP: {message}", file=sys.stderr)
    raise SystemExit(2)


def _gh(*args: str) -> str:
    if shutil.which("gh") is None:
        _die("the gh CLI is required")
    try:
        result = subprocess.run(
            ["gh", *args, "--repo", REPO],
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        _die(f"gh {' '.join(args)} did not finish within {COMMAND_TIMEOUT_S}s")
    if result.returncode != 0:
        _die(f"gh {' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def _fetch_plan(run: str, workdir: Path) -> Path:
    # A run id is a decimal number. Checking it keeps a value like
    # "--repo attacker/repo" from reaching gh as a second flag and replacing
    # the repository the tool was pointed at.
    if not run.isdigit():
        _die(f"run id must be a decimal number, got {run!r}")
    _gh("run", "download", run, "-n", PLAN_ARTIFACT, "-D", str(workdir))
    candidates = sorted(workdir.rglob(PLAN_FILE))
    if not candidates:
        _die(f"no {PLAN_FILE} inside the downloaded {PLAN_ARTIFACT} artifact")
    if len(candidates) > 1:
        _die(f"ambiguous plan: {[str(path) for path in candidates]}")
    return candidates[0]


def _manifest_nodes(sha: str | None = None) -> list[str]:
    """Read the manifest as of the run's revision when it is available.

    The ordinal is a position in a sorted list, so inserting an entry renumbers
    every later one. Reading the working tree instead of the run's revision is
    how `public-N` ends up naming a different test than the one CI published.
    """
    if sha is not None:
        try:
            result = subprocess.run(
                ["git", "show", f"{sha}:{MANIFEST.relative_to(ROOT)}"],
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            _die("cannot read the run revision's manifest; restore access to that commit first")
        if result.returncode != 0:
            _die("cannot read the run revision's manifest; fetch that commit first")
        return _nodes_from(result.stdout, f"{sha}:{MANIFEST.name}")
    return _nodes_from(MANIFEST.read_text(), str(MANIFEST))


def _nodes_from(text: str, source: str) -> list[str]:
    try:
        document = json.loads(text)
    except ValueError as error:
        _die(f"{source} is not valid JSON: {error}")
    nodes = document.get("nodes") if isinstance(document, dict) else None
    if not isinstance(nodes, list) or not all(isinstance(node, str) for node in nodes):
        _die(f"{source} has no string node list")
    return nodes


def _checked_node(node: str) -> str:
    """Refuse a node id that could reach pytest as anything but a test path."""
    path = node.split("::", 1)[0]
    if (
        not path.startswith("tests/")
        or not path.endswith(".py")
        or ".." in path
        or node.startswith("-")
        or NODE_ID.fullmatch(node) is None
    ):
        _die(f"{node!r} is not a test path under tests/")
    return node


def _resolve(ordinal: str, nodes: list[str]) -> str:
    """Return the node id that ordinal indexes, or stop.

    The ordinal is a position in the reviewed manifest, not a search key. That
    list is the published contract, so it is read directly instead of being
    reparsed out of the run's plan, which carries only module-prefixed forms.
    """
    match = ORDINAL.fullmatch(ordinal)
    if match is None:
        _die(f"ordinal must look like public-<n>, got {ordinal!r}")
    index = int(match.group(1))
    if index >= len(nodes):
        _die(f"{ordinal} is past the end of the manifest ({len(nodes)} entries)")
    return _checked_node(nodes[index])


def _plan_sha(plan: dict) -> str:
    sha = plan.get("sha")
    if not isinstance(sha, str) or not sha:
        _die("plan has no sha; refusing to run against an unknown revision")
    return sha


def _checkout_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _run_node(node: str) -> int:
    command = [sys.executable, "-m", "pytest", node, "-q", "--tb=long", "-p", "no:cacheprovider"]
    print(f"command: {' '.join(command)}")
    try:
        completed = subprocess.run(command, timeout=PYTEST_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        _die(f"pytest did not finish within {PYTEST_TIMEOUT_S}s")
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--run", required=True, help="GitHub Actions run id")
    parser.add_argument("--id", required=True, help="published ordinal, e.g. public-20349")
    parser.add_argument("--dry-run", action="store_true", help="resolve only; do not run pytest")
    parser.add_argument("--expect-sha", default=None, help="fail unless the plan sha matches")
    parser.add_argument(
        "--skip-revision-check",
        action="store_true",
        help="run even when the checkout is not the revision the plan was built from",
    )
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="forge-ci-repro.") as raw:
        plan_path = _fetch_plan(args.run, Path(raw))
        sha = _plan_sha(json.loads(plan_path.read_text()))
        if args.expect_sha and args.expect_sha != sha:
            _die(f"plan sha {sha} does not match --expect-sha {args.expect_sha}")
        if not args.skip_revision_check:
            head = _checkout_sha()
            if not head:
                _die("cannot read the checkout revision; pass --skip-revision-check to override")
            if head != sha:
                _die(f"checkout is {head}, but the run was {sha}; check out that revision first")

    node = _resolve(args.id, _manifest_nodes(sha))

    print(f"run    : {args.run}")
    print(f"sha    : {sha}")
    print(f"test   : {node}")
    if args.dry_run:
        return 0
    return _run_node(node)


if __name__ == "__main__":
    raise SystemExit(main())
