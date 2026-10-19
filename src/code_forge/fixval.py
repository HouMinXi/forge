# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""FIXVAL core module: fix-validation gate.

Proves a diff's new tests are not hollow by reverting non-test changes
and asserting the test goes RED, then restoring and asserting GREEN.

FIXVAL CAN BLOCK -- it gates only the diff's own hollow test.
Overfit guard (STING) is ADVISORY only (never blocking).

Pipeline position: post-convergence, co-located with R2/L2 mutation,
before the verdict.
"""

from __future__ import annotations

import ast
import fnmatch
import glob
import logging
import os
import re
import signal
import subprocess
import sys
import tempfile
from collections.abc import Collection
from dataclasses import dataclass
from enum import Enum
from io import StringIO
from pathlib import Path

import unidiff

from .advisory import AdvisoryFinding
from ._fixval_transaction import FixvalTransaction, TransactionError
from ._mutation_process import child_sigint_options
from .disposition import Disposition
from .diff import changed_files_in_order, iter_diff_sections, patched_file_path
from code_forge.baseline_guard import (
    _run_baseline_guard,
    _strip_venv_from_env,
    env_retry_note,
    env_retry_record,
)
from .state import StateFinding

_logger = logging.getLogger("code_forge")


class FixvalStatus(str, Enum):
    """FIXVAL gate result status."""

    PASS = "PASS"
    BLOCK = "BLOCK"
    SKIPPED = "SKIPPED"
    WAIVED = "WAIVED"


@dataclass(frozen=True)
class FixvalCandidate:
    """A diff that has both test and non-test files -- FIXVAL applicable."""

    test_files: list[str]
    non_test_files: list[str]


@dataclass(frozen=True)
class FixvalSkip:
    """A diff that is not a FIXVAL candidate, with reason."""

    reason: str


@dataclass(frozen=True)
class FixvalResult:
    """Result of running FIXVAL on a candidate diff.

    findings: StateFinding list consumed by machine.py.
      BLOCK -> one DISMISSED (block via Verdict.FAIL, not CONFIRMED).
      PASS -> empty. SKIPPED -> one DISMISSED. WAIVED -> one DISMISSED.
    advisories: AdvisoryFinding list (waiver record, overfit guard).
    block_message: non-empty only for BLOCK status.
    """

    status: FixvalStatus
    findings: list[StateFinding]
    advisories: list[AdvisoryFinding]
    block_message: str = ""


# Test file detection patterns (multi-language).
# Order: longest regex alternative first per project convention.
_TEST_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"(^|/)tests/test_[^/]+\.py$"),
    re.compile(r"(^|/)test_[^/]+\.py$"),
    re.compile(r"_test\.py$"),
    re.compile(r"\.test\.ts$"),
    re.compile(r"\.spec\.ts$"),
    re.compile(r"_test\.go$"),
)


def _is_test_file(path: str) -> bool:
    """Return True if path matches any test file pattern."""
    for pattern in _TEST_PATTERNS:
        if pattern.search(path):
            return True
    return False


def _is_test_support(path: str) -> bool:
    """Test scaffolding that is not itself a test file (#254).

    conftest.py anywhere, and any file under a tests/ directory, matching the
    mutation defaults (tests/**, **/tests/**, **/conftest.py; a singular test/
    stays production, #135). `path` must be repo-relative; backslash
    separators are normalised.
    """
    parts = path.replace("\\", "/").split("/")
    return parts[-1] == "conftest.py" or "tests" in parts[:-1]


def _repo_relative(path: str) -> str | None:
    """`path` relative to the Git checkout holding it.

    Relative review paths already are. An absolute path is taken relative to
    its nearest ancestor holding `.git` (a directory, or a worktree's file),
    so a checkout that itself lives under a directory named tests/ is not all
    scaffolding. None when no checkout is found: the caller then keeps main's
    classification.
    """
    if not os.path.isabs(path):
        return path
    for parent in Path(path).parents:
        if (parent / ".git").exists():
            return Path(path).relative_to(parent).as_posix()
    return None


def classify_fixval_candidate(
    changed_files: list[str],
    *,
    executable_files: Collection[str] | None = None,
) -> FixvalCandidate | FixvalSkip:
    """Classify a diff as FIXVAL candidate or skip.

    A diff needs both executable tests and semantic non-test changes.
    An optional execution projection excludes removed tests while keeping
    deleted production files as revert facts. Omission keeps all files.
    """
    if not changed_files:
        return FixvalSkip(reason="no files in diff")

    test_files: list[str] = []
    non_test_files: list[str] = []
    executable = None if executable_files is None else set(executable_files)

    support_files: set[str] = set()
    has_source = False
    for f in changed_files:
        if _is_test_file(f):
            if executable is None or f in executable:
                test_files.append(f)
            continue
        non_test_files.append(f)
        relative = _repo_relative(f)
        if relative is not None and _is_test_support(relative):
            support_files.add(f)
        elif relative is not None:
            has_source = True  # an unplaceable path keeps main's revert-all
    # Scaffolding stays at its fixed state only beside another non-test change;
    # when it is the whole fix (a conftest guard, #225) it is reverted, as
    # before #254. run_fixval re-checks a would-be HOLLOW with it reverted.
    if has_source:
        non_test_files = [f for f in non_test_files if f not in support_files]

    if test_files and non_test_files:
        return FixvalCandidate(
            test_files=test_files,
            non_test_files=non_test_files,
        )
    if not test_files:
        if executable is not None:
            return FixvalSkip(reason="no executable test file in diff")
        return FixvalSkip(reason="no test file in diff")
    return FixvalSkip(reason="no non-test file in diff")


def parse_fixval_waiver(
    commit_message: str,
    env: dict[str, str] | None = None,
) -> str | None:
    """Parse FIXVAL waiver from env var or commit trailer.

    Dual-channel waiver:
      Channel 1 (primary): FIXVAL_WAIVER env var.
      Channel 2: Fixval-Waiver: trailer in commit message.
    Env takes precedence when both present.
    Empty reason (whitespace-only) returns None.

    Args:
        commit_message: the commit message to scan for trailer.
        env: environment dict (defaults to None = no env check).
             Pass os.environ at call site for real env lookup.

    Returns:
        Waiver reason string, or None if no waiver.
    """
    # Channel 1: env var (primary at pre-commit time)
    if env is not None:
        env_val = env.get("FIXVAL_WAIVER", "")
        if env_val.strip():
            return env_val.strip()

    # Channel 2: commit trailer (case-insensitive)
    for line in commit_message.splitlines():
        match = re.match(
            r"^fixval-waiver:\s*(.*)",
            line,
            re.IGNORECASE,
        )
        if match:
            reason = match.group(1).strip()
            if reason:
                return reason

    return None


def _make_skipped_result(reason: str) -> FixvalResult:
    """Create a SKIPPED result with one DISMISSED finding."""
    return FixvalResult(
        status=FixvalStatus.SKIPPED,
        findings=[
            StateFinding(
                id="FIXVAL_SKIPPED",
                fingerprint="fixval-skipped",
                source="FIXVAL",
                disposition=Disposition.DISMISSED,
                file="",
                line_range=[],
                description=reason,
            ),
        ],
        advisories=[],
    )


def _filter_non_test_patch(diff_text: str, *, revert_support: bool = False) -> str:
    """Project production file blocks without reserializing Git binary bodies.

    Test support (#254) is left out beside any other non-test block, unless
    `revert_support` asks for main's revert-everything-but-tests projection.
    """

    def paths_of(patched_file):
        return [
            path
            for path in (patched_file_path(patched_file, source=True), patched_file_path(patched_file))
            if path
        ]

    def is_reversible(patched_file):
        return not any(_is_test_file(path) for path in paths_of(patched_file))

    def is_support(patched_file):
        return any(_is_test_support(path) for path in paths_of(patched_file))

    if any(line.startswith("diff --git ") for line in StringIO(diff_text)):
        blocks = [(unidiff.PatchSet(block), block) for _path, block in iter_diff_sections(diff_text)]
    else:
        blocks = [([entry], str(entry)) for entry in unidiff.PatchSet(diff_text)]

    # Diff paths are repo-relative. Scaffolding (#254) is kept at its fixed
    # state only when another non-test block is reverted alongside it.
    def is_source(patched_file):
        return is_reversible(patched_file) and not is_support(patched_file)

    keep_support = not revert_support and any(
        entries and all(is_source(entry) for entry in entries) for entries, _block in blocks
    )

    def is_production(patched_file):
        return is_reversible(patched_file) and not (keep_support and is_support(patched_file))

    filtered = []
    production_paths: set[str] = set()
    test_paths: set[str] = set()
    for entries, block in blocks:
        paths = {path for entry in entries for path in paths_of(entry)}
        if entries and all(is_production(entry) for entry in entries):
            filtered.append(block)
            production_paths.update(paths)
        else:
            test_paths.update(paths)
    overlap = production_paths & test_paths
    if overlap:
        raise TransactionError(
            "production reversal overlaps excluded test paths: %s" % ", ".join(sorted(overlap))
        )
    return "".join(filtered)


def _transaction_block(message: str, recovery: str) -> FixvalResult:
    detail = "FIXVAL transaction failed: %s. Recovery: %s" % (message, recovery)
    return FixvalResult(
        status=FixvalStatus.BLOCK,
        findings=[
            StateFinding(
                id="FIXVAL_TRANSACTION",
                fingerprint="fixval-transaction",
                source="FIXVAL",
                disposition=Disposition.UNCERTAIN,
                file="",
                line_range=[],
                description=detail,
                error=detail,
            )
        ],
        advisories=[],
        block_message=detail,
    )


# #252: only a judged test failure counts as "fails on the reverted code".
# pytest's exit codes have fixed meanings; other runners' codes don't (make
# uses 2 for a failed recipe), so they keep "nonzero = fails" except signals.
# Exit 1 (tests failed) and exit 2 (interrupted or collection error) count as
# failing, exactly as before #252: a test importing a name the fix adds exits
# 2 on the reverted code. Whether collection errors are RED witnesses is #28.
_PYTEST_JUDGED = frozenset({1, 2})
_PYTEST_EXIT = {
    3: "internal error",
    4: "usage error",
    5: "no tests collected",
}
_PYTEST_NAMES = frozenset({"pytest", "py.test"})
_RUN_WRAPPERS = frozenset({"uv", "poetry", "pdm", "pipenv", "hatch"})
_PYTHON = re.compile(r"python(\d+(\.\d+)*)?")
# CPython options that consume the following argument.
_PYTHON_ARG_OPTIONS = frozenset({"-W", "-X"})


def _program(arg: str) -> str:
    name = os.path.basename(str(arg).replace("\\", "/")).lower()
    return name[: -len(".exe")] if name.endswith(".exe") else name


def _runs_pytest(cmd: list[str]) -> bool:
    """Whether the runner itself is pytest, not merely an argument named so.

    `pytest ...`, `<venv>/bin/py.test ...`, `python -B -m pytest ...`, and
    either behind `uv run` (or poetry/pdm/pipenv/hatch run). `make pytest`
    and `tox -e pytest` run some other program.
    """
    argv = [str(arg) for arg in cmd]
    if len(argv) >= 2 and _program(argv[0]) in _RUN_WRAPPERS and argv[1] == "run":
        argv = argv[2:]
    if not argv:
        return False
    if _program(argv[0]) in _PYTEST_NAMES:
        return True
    if not _PYTHON.fullmatch(_program(argv[0])):
        return False
    rest = iter(argv[1:])
    for arg in rest:
        if arg == "-m":
            return next(rest, "") in _PYTEST_NAMES
        if arg.startswith("-m"):
            return arg[2:] in _PYTEST_NAMES
        if arg in _PYTHON_ARG_OPTIONS:
            next(rest, None)
        elif not arg.startswith("-") or arg in ("-", "-c"):
            return False  # a script, stdin or -c code: not `-m pytest`
    return False


def _judged_failure(cmd: list[str], result: subprocess.CompletedProcess) -> str | None:
    """Return None when a nonzero run judged the tests as failing, else why not.

    A signal death never judged anything. For pytest, exits 1 and 2 are
    judgements (2 pending #28); 3, 4, 5 and unknown codes are not.
    """
    code = result.returncode
    if code < 0:
        try:
            name = signal.Signals(-code).name
        except ValueError:
            name = "signal %d" % -code
        return "test run killed by %s (exit %d)" % (name, code)
    if code in _PYTEST_JUDGED or not _runs_pytest(cmd):
        return None
    return "pytest exit %d (%s)" % (code, _PYTEST_EXIT.get(code, "not a test-failure exit"))


def _timeout_output(exc: subprocess.TimeoutExpired) -> str:
    value = exc.stderr or exc.stdout or ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def _inconclusive_block(reason: str, output: str = "") -> FixvalResult:
    """Non-PASS FIXVAL when the reverted-code run never judged the test.

    Unmeasured is not PASS: machine.py turns BLOCK into a FAIL verdict, and
    the reason says exactly why the fix is unverified.
    """
    detail = (
        "FIXVAL inconclusive: reverted-code %s; the test was not judged, so the fix is unverified"
        % reason
    )
    tail = " ".join((output or "").split())[-200:]
    if tail:
        detail += ". Output: %s" % tail
    return FixvalResult(
        status=FixvalStatus.BLOCK,
        findings=[
            StateFinding(
                id="FIXVAL_INCONCLUSIVE",
                fingerprint="fixval-inconclusive",
                source="FIXVAL",
                disposition=Disposition.UNCERTAIN,
                file="",
                line_range=[],
                description=detail,
                error=detail,
            )
        ],
        advisories=[],
        block_message=detail,
    )


# pytest's closing summary, e.g. "1 failed, 1 error in 0.12s" (may be framed by "=").
_PYTEST_SUMMARY = re.compile(r"^[= ]*((?:\d+ [a-z]+, )*\d+ [a-z]+) in \d", re.MULTILINE)
_PYTEST_FAILED = re.compile(r"\b[1-9]\d* failed\b")
# Short test summary entry (still printed under -qq).
_PYTEST_FAILED_ENTRY = re.compile(r"^FAILED ", re.MULTILINE)
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _witnessed_failure(cmd: list[str], result: subprocess.CompletedProcess) -> bool:
    """Whether a nonzero run shows a test failing, not only breaking (#255).

    Used for the retry with test support reverted: a test that loses a
    fixture or helper errors at setup or collection, so only a failed test
    witnesses that the support was the fix. A signal death never witnessed
    anything. For pytest the run must exit 1 and its (colour-stripped) output
    must show a failed test: the closing summary's "N failed", or, without a
    summary (-qq), a short-summary FAILED entry. Anything else under pytest is
    no verdict. Other runners keep main's "nonzero = fails".
    """
    if result.returncode < 0:
        return False
    if not _runs_pytest(cmd):
        return True
    if result.returncode != 1:
        return False
    output = _ANSI.sub("", result.stdout or "")
    summaries = _PYTEST_SUMMARY.findall(output)
    if summaries:
        return _PYTEST_FAILED.search(summaries[-1]) is not None
    return _PYTEST_FAILED_ENTRY.search(output) is not None


def _test_reverted_candidate(
    candidate, scoped_cmd, run_env, repo_root, reverted_files=None, *, require_witness=False, timeout=600
) -> FixvalResult | None:
    """Run the tests on reverted code. With `require_witness`, a nonzero run
    that did not show a failing test returns None (no verdict)."""
    try:
        result = subprocess.run(
            scoped_cmd,
            env=run_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            **child_sigint_options(),  # #230
            cwd=repo_root,
        )
    except subprocess.TimeoutExpired as exc:
        # #187: a slow reverted run is not a transaction failure, but nothing
        # was measured, so it is not PASS either: an inconclusive BLOCK with
        # the reason. Restoration still runs (and is checked) in run_fixval.
        return _inconclusive_block("test run timed out after %ss" % timeout, _timeout_output(exc))
    if result.returncode != 0:
        if require_witness:
            # #255 retry: only a witnessed failed test counts; else no verdict.
            if not _witnessed_failure(scoped_cmd, result):
                return None
            return FixvalResult(status=FixvalStatus.PASS, findings=[], advisories=[])
        reason = _judged_failure(scoped_cmd, result)
        if reason is not None:
            return _inconclusive_block(reason, result.stderr or result.stdout or "")
        return FixvalResult(status=FixvalStatus.PASS, findings=[], advisories=[])
    block_msg = "FIXVAL: Test(s) did not fail when the fix was reverted.\n\n  Reverted files:\n"
    for filename in candidate.non_test_files if reverted_files is None else reverted_files:
        block_msg += "    %s\n" % filename
    block_msg += "\n  Tests that should have failed but passed:\n"
    for filename in candidate.test_files:
        block_msg += "    %s\n" % filename
    block_msg += (
        "\n  This means the test passes on both the fixed and unfixed code -- it does\n"
        "  not actually verify the fix.\n\n"
        "  To waive (nondeterministic bug), at pre-commit time use the env var:\n"
        '    FIXVAL_WAIVER="<reason>" git commit ...\n'
        "  and also add the trailer for the permanent git-log record:\n"
        "    Fixval-Waiver: <reason>\n"
    )
    return FixvalResult(
        status=FixvalStatus.BLOCK,
        findings=[
            StateFinding(
                id="FIXVAL_HOLLOW",
                fingerprint="fixval-hollow",
                source="FIXVAL",
                disposition=Disposition.DISMISSED,
                file=candidate.test_files[0] if candidate.test_files else "",
                line_range=[],
                description="hollow test: test passes on both fixed and reverted code",
            )
        ],
        advisories=[],
        block_message=block_msg,
    )


def _isolated_bytecode_env(env: dict[str, str]) -> dict[str, str]:
    """`env` for a FIXVAL child test run: it writes no bytecode (#257).

    A copy with PYTHONDONTWRITEBYTECODE=1. The reverted run then leaves no
    cache of the unfixed code behind to shadow the restored file. Caches of
    the stdlib, site-packages and untouched project files stay readable, so
    children start as fast as before (a private PYTHONPYCACHEPREFIX made
    every child recompile all of them: a trivial run went 0.22s -> 0.84s).
    """
    isolated = dict(env)
    isolated["PYTHONDONTWRITEBYTECODE"] = "1"
    return isolated


# CPython's short options that take no argument, so `X` may follow them in
# one cluster (`-BXpycache_prefix=...`).
_NO_ARGUMENT_FLAGS = frozenset("bBdEhiIOPqRsSuvVx")


def _x_options(args: list[str]) -> list[str]:
    """The values of every `-X` in `args`, also inside a cluster of flags."""
    options = []
    for i, arg in enumerate(args):
        if not arg.startswith("-"):
            continue  # `--x` ends at once: "-" is no flag
        for j, flag in enumerate(arg[1:], start=1):
            if flag == "X":
                if j + 1 < len(arg):
                    options.append(arg[j + 1 :])
                elif i + 1 < len(args):
                    options.append(args[i + 1])
                break
            if flag not in _NO_ARGUMENT_FLAGS:
                break
    return options


def _pycache_prefixes(env: dict[str, str], cmd: Collection[str]) -> list[str]:
    """Every bytecode cache prefix a child may use: PYTHONPYCACHEPREFIX and
    `-X pycache_prefix=...` in the test command (which wins over it), in any
    spelling (`-X p`, `-Xp`, `-BXp`). One set by an `env NAME=value` wrapper,
    sitecustomize or a launcher script stays invisible here."""
    key = "pycache_prefix="
    prefixes = [env.get("PYTHONPYCACHEPREFIX", "")]
    prefixes += [option[len(key) :] for option in _x_options(list(cmd)) if option.startswith(key)]
    return [prefix for prefix in prefixes if prefix]


def _cache_names(cache: Path) -> list[str]:
    """Names in `cache`; [] when it doesn't exist (so nothing was cached).
    A directory that exists but can't be listed raises: its caches can't be
    dropped, and silently skipping them would run stale bytecode."""
    try:
        return os.listdir(cache)
    except (FileNotFoundError, NotADirectoryError):
        return []


def _drop_cached_bytecode(
    root: str, paths: Collection[str], env: dict[str, str], cmd: Collection[str] = ()
) -> None:
    """Remove cached bytecode of exactly the touched .py `paths` (#257).

    CPython and pytest's assertion rewriter validate a cache by whole-second
    source mtime plus size, so a same-second, same-size revert or restore
    would run the other side's bytecode. Before each reverted run and after
    each restore, `<dir>/__pycache__/<stem>.*.pyc` (CPython's and pytest's
    `-pytest-*` one) goes, and the mirror under every cache prefix the
    children use (`_pycache_prefixes`, relative ones from the checkout).
    Raises OSError when a cache can't be listed or removed; only a cache
    that is already gone is tolerated.
    """
    prefixes = _pycache_prefixes(env, cmd)
    for path in paths:
        if not path.endswith(".py"):
            continue
        source = Path(os.path.abspath(os.path.join(root, path)))
        mirror = str(source.parent)[len(source.anchor) :]
        caches = [source.parent / "__pycache__"]
        caches += [Path(os.path.join(root, prefix), mirror) for prefix in prefixes]
        pattern = glob.escape(source.stem) + ".*.pyc"
        for cache in caches:
            for name in _cache_names(cache):
                if not fnmatch.fnmatchcase(name, pattern):
                    continue
                try:
                    os.unlink(cache / name)
                except FileNotFoundError:
                    pass


def _is_tracked(root: str, path: str) -> bool:
    """Whether git tracks `path` in the checkout at `root`; False when that
    can't be told (no git, not a repository), which fails closed."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--", path],
            cwd=root,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and bool(result.stdout)


def _importable_bytecode(path: str) -> bool:
    """Whether some Python 3 could import the sourceless .pyc at `path`.

    The test command may run another interpreter than forge's (python3.13
    while forge runs 3.12), so any CPython 3 magic counts, not only the
    running one's: a 2-byte little-endian number in 3000..19999 followed by
    b"\\r\\n" (fail closed; future 3.x magics stay in range). Python 1/2
    magics (20121 and up) and malformed headers are out of scope: no Python 3
    imports them, so the reverted run measures correctly (#259).
    """
    try:
        with open(path, "rb") as fh:
            header = fh.read(4)
    except OSError:
        return False  # unreadable: the import fails too
    if header[2:] != b"\r\n":  # also rejects a truncated header
        return False
    return 3000 <= int.from_bytes(header[:2], "little") < 20000


def _refuse_sourceless_bytecode(root: str, paths: Collection[str]) -> None:
    """Raise TransactionError when a touched .py the revert removed (one the
    fix adds) has a sourceless `<dir>/<stem>.pyc` beside it (#259).

    Python imports that file when the source is missing, so the reverted run
    would run it instead of failing to import. It isn't a cache FIXVAL may
    delete (it can be a shipped artifact), so the revert is unmeasured. Not
    when no Python 3 can import it (a Python 2 magic), and not when it
    belongs to the reverted tree (the patch restores it, or git tracks it):
    importing it then is the unfixed code, a correct measurement.
    """
    touched = set(paths)
    for path in paths:
        if not path.endswith(".py"):
            continue
        source = os.path.join(root, path)
        legacy_path = path[: -len(".py")] + ".pyc"
        legacy = os.path.join(root, legacy_path)
        if os.path.lexists(source) or not os.path.lexists(legacy):
            continue
        if not _importable_bytecode(legacy):
            continue
        if legacy_path in touched or _is_tracked(root, legacy_path):
            continue
        raise TransactionError(
            "sourceless bytecode %s would be imported in place of reverted %s; "
            "remove it to measure the fix" % (legacy_path, path)
        )


def run_fixval(
    candidate: FixvalCandidate,
    test_cmd: list[str],
    cwd: Path,
    commit_message: str,
    diff_text: str | None,
    *,
    recovery_parent: Path | None = None,
    baseline_timeout: int = 120,
) -> FixvalResult:
    """Run FIXVAL gate on a candidate diff.

    Steps:
      a. Guard: diff_text None -> SKIPPED
      b. Waiver check -> WAIVED with advisory
      c. Baseline guard (3x flaky check)
      d. Revert non-test hunks via git apply -R
      e. Run scoped test cmd; FAIL -> PASS, PASS -> BLOCK
      f. Restore via git apply (forward re-apply) in finally

    Args:
        candidate: classified FIXVAL candidate (test + non-test files).
        test_cmd: base test command (e.g. ["python", "-m", "pytest"]).
        cwd: repository root.
        commit_message: for waiver trailer parsing.
        diff_text: unified diff text (same source as classify).
        recovery_parent: optional preservation parent outside participating source roots.
        baseline_timeout: per-run deadline for the baseline guard, from the
            trusted gate's test.timeout_seconds (a full-suite command needs
            far more than the 120s default).

    Returns:
        FixvalResult with status, findings, advisories, block_message.
    """
    repo_root = str(cwd)

    # (a) Guard: no diff available
    if diff_text is None:
        return _make_skipped_result("non-git review, no diff available")

    # (b) Waiver check
    waiver_reason = parse_fixval_waiver(commit_message, env=os.environ)
    if waiver_reason is not None:
        if os.environ.get("FIXVAL_WAIVER", "").strip():
            channel = "FIXVAL_WAIVER env var"
        else:
            channel = "Fixval-Waiver trailer"
        return FixvalResult(
            status=FixvalStatus.WAIVED,
            findings=[
                StateFinding(
                    id="FIXVAL_WAIVED",
                    fingerprint="fixval-waived",
                    source="FIXVAL",
                    disposition=Disposition.DISMISSED,
                    file="",
                    line_range=[],
                    description="FIXVAL waived: %s" % waiver_reason,
                ),
            ],
            advisories=[
                AdvisoryFinding(
                    id="FIXVAL_WAIVER_RECORD",
                    axis="FIXVAL",
                    file="",
                    line_range=[],
                    description=("FIXVAL waived via %s: %s" % (channel, waiver_reason)),
                    attribution="fixval-waiver",
                ),
            ],
        )

    # Qualify the production reversal before executing tests or allocating recovery.
    try:
        non_test_patch = _filter_non_test_patch(diff_text)
    except (unidiff.errors.UnidiffParseError, TransactionError) as exc:
        return _transaction_block("invalid production patch: %s" % exc, repo_root)
    if not non_test_patch.strip():
        return _make_skipped_result("no non-test changes to revert")

    # Baseline guard
    scoped_cmd = test_cmd + candidate.test_files

    run_env = os.environ.copy()
    pythonpath = os.path.join(repo_root, "src")
    run_env["PYTHONPATH"] = pythonpath
    run_env = _isolated_bytecode_env(run_env)
    retry_cause: list[str] = []

    status, guard_findings, guard_infra = _run_baseline_guard(
        scoped_cmd,
        run_env,
        repo_root,
        allow_strip_retry=True,
        retry_cause=retry_cause,
        timeout=baseline_timeout,
    )
    env_retry = None
    if status == "needs_strip_retry":
        env_retry = env_retry_record(retry_cause[0] if retry_cause else None, run_env)
        run_env = _strip_venv_from_env(run_env)  # a copy: bytecode stays off
        run_env["PYTHONPATH"] = pythonpath
        status, guard_findings, guard_infra = _run_baseline_guard(
            scoped_cmd,
            run_env,
            repo_root,
            allow_strip_retry=False,
            first_attempt=retry_cause[0] if retry_cause else None,
            timeout=baseline_timeout,
        )

    def noted(result: FixvalResult) -> FixvalResult:
        # #246: a strip retry is reported whatever its outcome, so a pass
        # under the stripped env stays attributable.
        if env_retry is not None:
            result.advisories.append(
                AdvisoryFinding(
                    id="FIXVAL_BASELINE_ENV_RETRY",
                    axis="FIXVAL",
                    file="",
                    line_range=[],
                    description=env_retry_note(env_retry),
                    attribution="fixval-baseline-env-retry",
                )
            )
        return result

    if status == "skip":
        return noted(
            FixvalResult(
                status=FixvalStatus.SKIPPED,
                findings=guard_findings,
                advisories=[],
            )
        )

    outcome = _reverted_run(
        candidate, non_test_patch, scoped_cmd, run_env, cwd, recovery_parent, timeout=baseline_timeout
    )
    if not _is_hollow(outcome):
        return noted(outcome)
    # #255: test support was left at its fixed state beside another non-test
    # change (maybe only docs or config). Before calling the tests hollow,
    # revert the support as well, as before #254; that run decides.
    # A retry that only breaks the tests (a fixture or helper gone, #254)
    # leaves the first run's HOLLOW standing; so does a run killed by a signal
    # or a pytest run with no readable failed test (see _witnessed_failure).
    # Known limits of telling "support was the fix" from "support is needed":
    # - fail-closed: a support fix whose absence only *errors* (repairing a
    #   fixture that raised, a sys.path or plugin fix) beside another non-test
    #   change stays HOLLOW (main: PASS); waive with FIXVAL_WAIVER.
    # - as on main: a hollow test reaching new support only at call time
    #   (an import in the test body, getfixturevalue), or a pre-existing
    #   fixture whose value the fix changed, fails on the retry and is PASS.
    full_patch = _filter_non_test_patch(diff_text, revert_support=True)
    if full_patch == non_test_patch:
        return noted(outcome)
    retry = _reverted_run(
        candidate,
        full_patch,
        scoped_cmd,
        run_env,
        cwd,
        recovery_parent,
        require_witness=True,
        timeout=baseline_timeout,
    )
    return noted(outcome if retry is None else retry)


def _is_hollow(result: FixvalResult) -> bool:
    return [f.id for f in result.findings] == ["FIXVAL_HOLLOW"]


def _reverted_run(
    candidate: FixvalCandidate,
    non_test_patch: str,
    scoped_cmd: list[str],
    run_env: dict[str, str],
    cwd: Path,
    recovery_parent: Path | None,
    *,
    require_witness: bool = False,
    timeout: int = 600,
) -> FixvalResult | None:
    """Revert `non_test_patch`, run the scoped tests, restore the fix.

    None only with `require_witness`, when the run gave no verdict."""
    repo_root = str(cwd)
    # Write patch to temp file
    try:
        fd, patch_path = tempfile.mkstemp(prefix=".fixval-revert-", suffix=".patch")
    except OSError as exc:
        return _transaction_block("cannot allocate revert patch: %s" % exc, repo_root)
    transaction = None
    forward_applied = False
    errors = []
    interrupted = None
    outcome = None
    touched = changed_files_in_order(non_test_patch)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(non_test_patch)
        preservation = {"recovery_parent": recovery_parent} if recovery_parent is not None else {}
        transaction = FixvalTransaction(cwd, non_test_patch, **preservation)
        transaction.prepare()
        revert_result = transaction.reverse(patch_path)
        if revert_result.returncode != 0:
            errors.append("revert patch failed: %s" % revert_result.stderr[:200])
        else:
            transaction.mark_reverted()
            _refuse_sourceless_bytecode(repo_root, touched)
            _drop_cached_bytecode(repo_root, touched, run_env, scoped_cmd)
            outcome = _test_reverted_candidate(
                candidate,
                scoped_cmd,
                run_env,
                repo_root,
                changed_files_in_order(non_test_patch),
                require_witness=require_witness,
                timeout=timeout,
            )
    except (OSError, ValueError, TransactionError, subprocess.SubprocessError) as exc:
        errors.append(str(exc))
    except (KeyboardInterrupt, SystemExit) as exc:
        interrupted = exc
    finally:
        if transaction is not None:
            try:
                if transaction.reverted:
                    if transaction.can_apply_forward():
                        restored = subprocess.run(
                            ["git", "apply", "--check", patch_path],
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            check=False,
                            cwd=repo_root,
                            env=transaction.git_environment(),
                        )
                        forward_validated = restored.returncode == 0
                        if not forward_validated:
                            errors.append("forward patch failed: %s" % restored.stderr[:200])
                    else:
                        errors.append("source entries changed during reverted tests")
            except (OSError, TransactionError, subprocess.SubprocessError) as exc:
                errors.append(str(exc))
            except (KeyboardInterrupt, SystemExit) as exc:
                interrupted = interrupted or exc
                errors.append("forward restoration interrupted: %s" % type(exc).__name__)
            try:
                try:
                    errors.extend(transaction.restore(forward_applied))
                except (OSError, TransactionError) as exc:
                    errors.append("entry restoration failed: %s" % exc)
                except (KeyboardInterrupt, SystemExit) as exc:
                    interrupted = interrupted or exc
                    errors.append("entry restoration interrupted: %s" % type(exc).__name__)
                    # One bounded retry finishes known entries after cancellation;
                    # repeated cancellation retains recovery and remains visible.
                    try:
                        errors.extend(transaction.restore(forward_applied))
                    except (OSError, TransactionError) as retry:
                        errors.append("entry restoration retry failed: %s" % retry)
                    except (KeyboardInterrupt, SystemExit) as retry:
                        errors.append("entry restoration retry interrupted: %s" % type(retry).__name__)
            finally:
                try:
                    _drop_cached_bytecode(repo_root, touched, run_env, scoped_cmd)
                except OSError as exc:
                    errors.append("bytecode cache invalidation failed: %s" % exc)
                if sys.exception() is not None:
                    errors.append("entry restoration escaped: %s" % type(sys.exception()).__name__)
                errors.extend(transaction.image_errors)
                transaction.recovery_needed = transaction.recovery_needed or bool(errors)
                try:
                    transaction.close()
                except (OSError, TransactionError) as exc:
                    errors.append("transaction cleanup failed: %s" % exc)
                except (KeyboardInterrupt, SystemExit) as exc:
                    interrupted = interrupted or exc
                    errors.append("transaction cleanup interrupted: %s" % type(exc).__name__)
        if not errors:
            try:
                os.unlink(patch_path)
            except OSError as exc:
                errors.append("patch cleanup failed: %s" % exc)
    recovery = (
        transaction.recovery_location
        if transaction is not None
        and transaction.recovery_location
        and (
            transaction.recovery_needed
            or transaction.directory is not None
            and transaction.directory.exists()
        )
        else patch_path
    )
    if errors:
        _logger.error("FIXVAL: restore failed: %s; recovery: %s", "; ".join(errors), recovery)
    if interrupted is not None:
        if errors:
            interrupted.add_note("FIXVAL recovery: %s (%s)" % (recovery, "; ".join(errors)))
        raise interrupted
    if errors:
        return _transaction_block("; ".join(errors), recovery)
    return outcome


class _VariableRenamer(ast.NodeTransformer):
    """AST transform: rename the first local variable found.

    Appends '_renamed' suffix to one Name target in an assignment.
    Only renames within function bodies (local scope).
    """

    def __init__(self) -> None:
        super().__init__()
        self.renamed: str | None = None
        self.new_name: str | None = None
        self._done = False

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        """Visit function definitions to find local variables."""
        self.generic_visit(node)
        return node

    def visit_Assign(self, node: ast.Assign) -> ast.AST:
        """Rename the first simple Name target found."""
        if self._done:
            return node
        for target in node.targets:
            if isinstance(target, ast.Name):
                self.renamed = target.id
                self.new_name = target.id + "_renamed"
                target.id = self.new_name  # rename the target itself
                self._done = True
                break
        return node

    def visit_Name(self, node: ast.Name) -> ast.AST:
        """Rename all occurrences of the renamed variable."""
        if self.renamed and node.id == self.renamed:
            node.id = self.new_name
        return node


def run_overfit_guard(
    candidate: FixvalCandidate,
    test_cmd: list[str],
    cwd: Path,
    timeout: int = 600,
) -> list[AdvisoryFinding]:
    """Run STING overfit guard: advisory only, never blocking.

    Applies a variable-rename transform to the first .py file in
    non_test_files. If the test breaks after rename, it is overfitting
    to variable names.

    Original file bytes are saved before any transform and restored
    verbatim in the finally block (never re-unparse -- ast.unparse
    is lossy, strips comments/formatting).

    Args:
        candidate: FIXVAL candidate with test and non-test files.
        test_cmd: base test command.
        cwd: repository root.

    Returns:
        List of AdvisoryFinding (0 or 1 items). Empty if test passes
        after rename or no .py files found.
    """
    # Find first .py file in non_test_files
    py_file = None
    for f in candidate.non_test_files:
        if f.endswith(".py"):
            py_file = f
            break

    if py_file is None:
        return []

    file_path = Path(py_file)
    if not file_path.is_absolute():
        file_path = Path(cwd) / file_path

    if not file_path.exists():
        return []

    # Save original bytes before any transform
    original_bytes = file_path.read_bytes()

    try:
        source = original_bytes.decode("utf-8")
        tree = ast.parse(source)

        renamer = _VariableRenamer()
        transformed = renamer.visit(tree)

        if renamer.renamed is None:
            # No local variable found to rename
            return []

        # Validate transform before writing
        ast.fix_missing_locations(transformed)
        new_source = ast.unparse(transformed)
        # Verify the transformed code parses
        ast.parse(new_source)

        # Write transformed code
        file_path.write_text(new_source, encoding="utf-8")

        try:
            # Run test (match run_fixval: set PYTHONPATH=src/ so imports work)
            scoped_cmd = test_cmd + candidate.test_files
            run_env = _isolated_bytecode_env(os.environ.copy())
            run_env["PYTHONPATH"] = os.path.join(str(cwd), "src")
            try:
                _drop_cached_bytecode(str(cwd), [str(file_path)], run_env, scoped_cmd)
            except OSError:
                return []  # advisory only: unmeasured, never a stale verdict
            try:
                result = subprocess.run(
                    scoped_cmd,
                    env=run_env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                    check=False,
                    cwd=str(cwd),
                    **child_sigint_options(),  # #230
                )
            except subprocess.TimeoutExpired:
                # #187: advisory only; a slow run never blocks or crashes,
                # but the advisory says the check was not measured.
                return [
                    AdvisoryFinding(
                        id="FIXVAL_OVERFIT_INCONCLUSIVE",
                        axis="FIXVAL",
                        file=py_file,
                        line_range=[],
                        description=(
                            "overfit guard inconclusive: after renaming '%s' -> '%s' in %s the test run timed out after %ss"
                            % (renamer.renamed, renamer.new_name, py_file, timeout)
                        ),
                        attribution="fixval-overfit-guard",
                    ),
                ]

            if result.returncode != 0:
                reason = _judged_failure(scoped_cmd, result)
                if reason is not None:
                    # #252: an unjudged run says nothing about overfitting.
                    return [
                        AdvisoryFinding(
                            id="FIXVAL_OVERFIT_INCONCLUSIVE",
                            axis="FIXVAL",
                            file=py_file,
                            line_range=[],
                            description=(
                                "overfit guard inconclusive: after renaming '%s' -> '%s' in %s the %s"
                                % (renamer.renamed, renamer.new_name, py_file, reason)
                            ),
                            attribution="fixval-overfit-guard",
                        ),
                    ]
                # Test failed after rename -> overfitting advisory
                return [
                    AdvisoryFinding(
                        id="FIXVAL_OVERFIT",
                        axis="FIXVAL",
                        file=py_file,
                        line_range=[],
                        description=(
                            "test may be overfitting to variable names: "
                            "renamed '%s' -> '%s' in %s and test broke"
                            % (renamer.renamed, renamer.new_name, py_file)
                        ),
                        attribution="fixval-overfit-guard",
                    ),
                ]

            # Test still passes -> not overfitting
            return []

        finally:
            # ALWAYS restore original bytes verbatim
            file_path.write_bytes(original_bytes)
            try:
                _drop_cached_bytecode(str(cwd), [str(file_path)], os.environ, scoped_cmd)
            except OSError as exc:
                _logger.warning("FIXVAL overfit guard: cannot drop cached bytecode: %s", exc)

    except SyntaxError:
        # Cannot parse file -> skip overfit guard
        return []
    finally:
        # Double-ensure original bytes are restored
        if file_path.exists():
            current = file_path.read_bytes()
            if current != original_bytes:
                file_path.write_bytes(original_bytes)
