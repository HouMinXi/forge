# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Mutation testing integration via mutmut subprocess.

Python-only MVP. Runs mutmut on diff-scoped files and parses survivors.
Swappable design: keep subprocess calls in one place so future language
runners (cargo-mutants, go-mutesting) can replace implementation without
changing the l2_runner interface.

mutmut integration notes (>=3.4, where source_paths replaced the old key):
- No --paths-to-mutate CLI flag; use setup.cfg [mutmut] source_paths.
- Run cwd MUST be project root (where src/ and tests/ live).
- source_paths must be relative to project root.
- PYTHONPATH must point into src/ so imports resolve without src. prefix.
- results format: "    module.fn__mutmut_N: status" (one per line).
- Any non-zero exit code from mutmut run is a hard error.
- mutmut 3.x has no --config flag: it reads pyproject.toml [tool.mutmut]
  first, else setup.cfg [mutmut]. A project that already has either
  (gxcicd's language_inventory job) used to skip the whole gate.
- A leased workspace preserves original configuration nodes and cache,
  publishes scoped setup.cfg and hides [tool.mutmut]. Complete owned
  teardown permits restoration; incomplete or foreign states retain
  recovery artifacts and produce infrastructure evidence.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from code_forge.baseline_guard import _run_baseline_guard, _strip_venv_from_env
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NotRequired, TypedDict

from ._mutation_process import (
    MutationProcessError, _bind_cancellation_evidence, limit_address_space, run_owned_command,
)
from ._mutation_workspace import MutationWorkspace, MutationWorkspaceError, OrphanMutationContainerError
from .disposition import Disposition
from .errors import BaselineResolutionError
from .mutation_config import DEFAULT_MUTATION_TIMEOUT, validate_mutation_timeout
from .state import StateFinding

# Marker in the scoped setup.cfg. User files without it are snapshotted
# and restored after the run; a leftover marked file is deleted.
_CODE_FORGE_CFG_MARKER = "# managed-by-code-forge-mutation"


@dataclass
class Survivor:
    """A mutant that survived (no test killed it)."""

    mutant_name: str  # mutmut 3.x identifier e.g. "code_forge.mutation.x_run__mutmut_1"
    file: str  # source file (empty; mutmut 3.x results omit file paths)


class MutationOutcome(TypedDict):
    """Terminal evidence for the supported Python measurement and sibling notes."""

    status: Literal["done", "error"]
    baseline_passed: bool
    survivors: list[str]
    skipped: list[str]
    infra_errors: list[str]
    inventory: dict[str, str]
    message: str
    survivor_details_version: NotRequired[int]
    survivor_details: list[dict]
    completed_measurement: bool
    review_context: dict | None


def _mutation_outcome(
    findings: list[StateFinding], infra: list[str], evidence: dict, *, review_context: dict | None = None,
) -> MutationOutcome:
    from .mutation_findings import is_mutation_diagnostic, is_mutation_survivor, mutation_survivor_details

    errors = [f.description for f in findings if f.id == "MUTATION_ERROR" or is_mutation_diagnostic(f)]
    skipped = [f.description for f in findings if f.id == "MUTATION_SKIPPED"]
    errors.extend(infra)
    completed = evidence.get("completed_measurement", evidence.get("baseline_passed")) is True
    if not completed:
        errors.extend(skipped)
        if not errors:
            errors.append("mutation did not prove its baseline and completed measurement")
    outcome: MutationOutcome = {
        "status": "error" if errors else "done",
        "baseline_passed": completed and not errors,
        "survivors": [f.id for f in findings if is_mutation_survivor(f)],
        "skipped": skipped,
        "infra_errors": list(infra),
        "inventory": evidence.get("inventory", {}),
        "message": "\n".join(errors),
        "survivor_details": mutation_survivor_details(findings) if not errors else [],
        "completed_measurement": evidence.get("completed_measurement") is True and not errors,
        "review_context": review_context if not errors else None,
    }
    # Preserve failure/cleanup metadata for the bound consumer. Dropping a
    # failed cleanup marker would manufacture a successful canonical carrier.
    for key in ("publication_error", "cancellation_type", "cleanup_error", "process_failure",
                "cancelled", "cleanup_complete"):
        if key in evidence:
            outcome[key] = evidence[key]
    if review_context is not None:
        outcome["survivor_details_version"] = 1
    return outcome


# #135: test code is identified by a tests/ directory, not by file name. A
# module like pkg/test_helpers.py or pkg/api_test.py is production code and
# must be mutated (and must not make a diff "tests-only"). A singular test/
# directory stays production (see test_mutation_venv case2/case5). Repos that keep tests elsewhere say so in
# the trusted gate.yaml mutation_skip_globs.
_DEFAULT_MUTATION_SKIP_GLOBS = [
    "tests/**",
    "**/tests/**",
    "**/conftest.py",
]


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Compile a glob so * and ? stay inside one path segment.

    ** matches across directories. fnmatch lets * cross /, which made
    **/test_*.py skip a production file under a test_* directory.
    """
    i = 0
    n = len(pattern)
    parts: list[str] = ["^"]
    while i < n:
        char = pattern[i]
        if char == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                i += 2
                if i < n and pattern[i] == "/":
                    i += 1
                    parts.append("(?:.*/)?")
                else:
                    parts.append(".*")
            else:
                parts.append("[^/]*")
                i += 1
        elif char == "?":
            parts.append("[^/]")
            i += 1
        else:
            parts.append(re.escape(char))
            i += 1
    parts.append("$")
    return re.compile("".join(parts))


def _glob_match(posix: str, pattern: str) -> bool:
    return _glob_to_regex(pattern).fullmatch(posix) is not None


def _matches_glob(path: str, pattern: str, cwd: Path | str | None = None) -> bool:
    """Match a path against a glob pattern.

    Handles POSIX normalization, directory patterns, and basename patterns.
    A single * does not cross /.
    """
    posix = path.replace("\\", "/")
    if cwd is not None and Path(path).is_absolute():
        try:
            posix = str(Path(path).relative_to(cwd)).replace("\\", "/")
        except ValueError:
            pass

    # A pattern with no slash matches the filename at any depth.
    if "/" not in pattern:
        filename = posix.rsplit("/", 1)[-1]
        if _glob_match(filename, pattern):
            return True

    effective = pattern
    if effective.endswith("/") and not effective.endswith("**/"):
        effective = effective + "**"

    if _glob_match(posix, effective):
        return True

    # Absolute path, relative pattern: match against each suffix.
    if posix.startswith("/") and not effective.startswith("/"):
        parts = posix.lstrip("/").split("/")
        for i in range(len(parts)):
            if _glob_match("/".join(parts[i:]), effective):
                return True

    return False


def _is_test_path(
    path: str,
    skip_globs: list[str] | None = None,
    include_globs: list[str] | None = None,
    cwd: Path | str | None = None,
) -> bool:
    """True for a path that should be excluded from mutation.

    Include globs take precedence over skip globs.
    When skip_globs is None, defaults to files under a tests/
    directory and conftest.py files (#135: never by file name alone).
    """
    effective_skips = _DEFAULT_MUTATION_SKIP_GLOBS if skip_globs is None else skip_globs
    effective_includes = [] if include_globs is None else include_globs

    for pat in effective_includes:
        if _matches_glob(path, pat, cwd=cwd):
            return False

    for pat in effective_skips:
        if _matches_glob(path, pat, cwd=cwd):
            return True

    return False


# Resource guards for the mutmut subprocess tree. mutmut >=3.4 defaults
# --max-children to os.cpu_count(); every child is a forked interpreter
# running the gate's pytest selection, so on a 16-core host the default
# fans out to cpu_count() full-suite processes and the OOM killer takes
# the whole review service (measured: 6.3G memory peak, forge reviewing
# its own cli.py+mutation.py diff, 2026-09-15). The cap bounds fan-out;
# the address-space limit is a backstop so an accidental blow-up kills
# one child with MemoryError instead of the service.
_DEFAULT_MAX_CHILDREN_CAP = 4
_DEFAULT_MEMORY_LIMIT_BYTES = 8 * 1024**3
_ENV_MAX_CHILDREN = "FORGE_MUTATION_MAX_CHILDREN"
_ENV_MEMORY_LIMIT_MB = "FORGE_MUTATION_MEMORY_LIMIT_MB"


def _effective_max_children(max_children: int | None) -> int:
    """Resolve the mutmut --max-children value.

    An explicit argument wins. The default is min(cpu_count, cap) because
    mutmut's own default is raw cpu_count -- the fan-out that OOM'd the
    review service. FORGE_MUTATION_MAX_CHILDREN overrides the default for
    ops emergencies without a code change.
    """
    if max_children is not None:
        return max(1, max_children)
    env = os.environ.get(_ENV_MAX_CHILDREN)
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    return min(os.cpu_count() or 4, _DEFAULT_MAX_CHILDREN_CAP)


def _memory_limit_bytes(memory_limit_bytes: int | None) -> int:
    """Resolve the RLIMIT_AS ceiling for the mutmut process tree."""
    if memory_limit_bytes is not None:
        return memory_limit_bytes
    env = os.environ.get(_ENV_MEMORY_LIMIT_MB)
    if env:
        try:
            return int(env) * 1024**2
        except ValueError:
            pass
    return _DEFAULT_MEMORY_LIMIT_BYTES


def _limit_address_space(memory_limit_bytes: int) -> None:
    """preexec_fn: cap address space so runaway children fail bounded.

    mutmut forks one child per mutant and the limit is inherited, so a
    single runaway mutant kills itself with MemoryError instead of the
    OOM killer taking the whole review service. POSIX only.

    The hard ceiling cannot be raised. An outer prlimit already below the
    requested cap must be kept, not treated as a launch failure.
    """
    limit_address_space(memory_limit_bytes)


_MUTATION_STAGE_EXCLUSION = "not integration and not source_scan"


def _exclude_unmirrorable_tests(selection: list[str]) -> list[str]:
    """Add the mutation-stage marker exclusions to mutmut's selection.

    The per-mutant loop re-runs the gate's pytest selection once per
    mutant. Two kinds of test do not survive that loop:

    'integration' spawns real mutation runs, nesting a full mutmut tree
    inside every mutant child.

    'source_scan' greps the source tree as data. Inside the mirror that
    tree is mutmut's rewritten copy, so a mutated string literal reads as
    the very violation the scan forbids, at a line number past the end of
    the real file.

    The R1 gate baseline still runs both unchanged; this narrows only the
    mutation stage's repeated runs.
    """
    result = list(selection)
    for i, tok in enumerate(result):
        if tok == "-m" and i + 1 < len(result):
            result[i + 1] = f"({result[i + 1]}) and ({_MUTATION_STAGE_EXCLUSION})"
            return result
    return result + ["-m", _MUTATION_STAGE_EXCLUSION]


def _source_roots(
    py_files: list[str],
    skip_globs: list[str] | None = None,
    include_globs: list[str] | None = None,
    cwd: Path | str | None = None,
) -> list[str]:
    """Derive mirror roots from diff-scoped python files.

    mutmut copies only source_paths into the mutants/ mirror, and the
    tests then run from inside that mirror. Scoping source_paths to the
    diff FILES alone mirrors single files without their package, so
    imports die under mutants/. The mirror must carry the whole top-level
    source tree; diff scoping belongs to only_mutate instead.
    """
    roots: set[str] = set()
    for f in py_files:
        if _is_test_path(f, skip_globs=skip_globs, include_globs=include_globs, cwd=cwd):
            continue
        # Mutmut config is POSIX; Windows diffs still arrive with "\\".
        posix = f.replace("\\", "/")
        if cwd is not None and Path(f).is_absolute():
            try:
                posix = str(Path(f).relative_to(cwd)).replace("\\", "/")
            except ValueError:
                pass
        head, sep, _ = posix.partition("/")
        roots.add(head if sep else posix)
    return sorted(roots)


def _baseline_test_selection(baseline_cmd: list[str]) -> list[str]:
    """Extract the pytest argument tail from a baseline command.

    The command may invoke pytest directly ([.../bin/pytest, tests/, -q])
    or through an interpreter ([python3, -m, pytest, tests/]). Only the
    tokens after the pytest executable are pytest arguments; the
    interpreter prefix (-m pytest included) must not leak into
    pytest_add_cli_args_test_selection, where '-m' would be parsed as a
    marker expression. A command with no pytest token returns empty:
    the previous fallback leaked the command tail (e.g. -m unittest)
    into mutmut's pytest selection.
    """
    for i, tok in enumerate(baseline_cmd):
        if _is_pytest_token(tok):
            return list(baseline_cmd[i + 1 :])
    return []


def _is_pytest_token(tok: str) -> bool:
    """True for pytest, .../pytest, pytest.exe, ...\\pytest.exe."""
    name = tok.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name.removesuffix(".exe") == "pytest"


def _build_mutmut_config(
    py_files: list[str],
    baseline_cmd: list[str],
    also_copy: list[str] | None = None,
    skip_globs: list[str] | None = None,
    include_globs: list[str] | None = None,
    cwd: Path | str | None = None,
) -> str:
    """Render the temporary [mutmut] setup.cfg content.

    source_paths mirrors whole source roots (importability), only_mutate
    keeps mutation diff-scoped (production files only; mutating the
    test suite poisons stats collection), and the test selection reuses
    the gate's baseline arguments so stats collection runs exactly the
    tests the gate trusts.

    also_copy: extra relative paths copied into the mutants/ mirror
    (mutmut also_copy). Empty or whitespace entries are dropped.
    """
    roots = _source_roots(py_files, skip_globs=skip_globs, include_globs=include_globs, cwd=cwd)
    mutate = [
        f
        for f in py_files
        if not _is_test_path(f, skip_globs=skip_globs, include_globs=include_globs, cwd=cwd)
    ]
    if not mutate:
        raise ValueError(
            "no production files to mutate; tests-only diffs must skip before writing setup.cfg"
        )
    lines = [
        _CODE_FORGE_CFG_MARKER,
        "[mutmut]",
        "source_paths=" + "\n    ".join(roots),
        "only_mutate=" + "\n    ".join(mutate),
    ]
    # mutmut splits this value on newlines, so a space-joined string arrives
    # as one argv token ("-q --ignore=x") that pytest rejects with exit 4.
    # One argument per line, first on the key line (a bare key plus indented
    # continuations leaves a leading newline mutmut keeps as an empty token).
    # The selection is narrowed to unit scope: integration tests spawn real
    # mutation runs and would nest a mutmut tree inside every mutant child.
    selection = _exclude_unmirrorable_tests(_baseline_test_selection(baseline_cmd))
    if selection:
        lines.append("pytest_add_cli_args_test_selection=" + selection[0])
        lines.extend("    " + a for a in selection[1:])
    also_copy = [p for p in (also_copy or []) if p.strip()]
    if also_copy:
        # First path on the key line. An empty also_copy= plus
        # indented continuations parses, but leaves a leading
        # newline that mutmut then keeps as an empty Path.
        lines.append("also_copy=" + also_copy[0])
        lines.extend("    " + p for p in also_copy[1:])

    return "\n".join(lines) + "\n"


_CHILD_ENV_PLUGIN = "mutation_child_env.py"


def _plant_child_env_plugin(repo_root: Path | str) -> None:
    """Put the child-env plugin where mutmut's pytest can import it.

    pytest runs with its rootdir inside mutants/, so a pytest.ini there adds
    that directory to sys.path and ``-p mutation_child_env`` loads the copy.
    Both files are removed with the rest of mutants/ when the run ends.
    """
    mirror = Path(repo_root) / "mutants"
    mirror.mkdir(exist_ok=True)
    source = Path(__file__).with_name(_CHILD_ENV_PLUGIN)
    (mirror / _CHILD_ENV_PLUGIN).write_bytes(source.read_bytes())
    # mutmut creates mutants/ empty and only writes the files it copies, so
    # this ini is ours to create. Refusing an existing one avoids splicing a
    # new section onto whatever a project already put there.
    ini = mirror / "pytest.ini"
    if ini.exists():
        raise FileExistsError("mutants/pytest.ini already exists; refusing to overwrite it")
    ini.write_text(
        "[pytest]\npythonpath = .\naddopts = -p mutation_child_env\n",
        encoding="utf-8",
    )


def _pyproject_has_tool_mutmut(text: str) -> bool:
    """True when mutmut 3.x would take [tool.mutmut] over setup.cfg."""
    try:
        import tomllib
    except ImportError:  # pragma: no cover
        return "[tool.mutmut]" in text
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return "[tool.mutmut]" in text
    tool = data.get("tool")
    return isinstance(tool, dict) and "mutmut" in tool


def _hide_tool_mutmut(text: str) -> str:
    """Drop [tool.mutmut] tables and a mutmut key under [tool].

    mutmut reads pyproject.toml first. The original bytes are restored
    after the run; this string only has to hide the table for cwd.
    """
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    skipping_table = False
    in_tool_table = False
    skipping_inline = False
    braces = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            skipping_table = False
            skipping_inline = False
            name = stripped[1:-1].strip()
            in_tool_table = name == "tool"
            if name == "tool.mutmut" or name.startswith("tool.mutmut."):
                skipping_table = True
                continue
        if skipping_table:
            continue
        if in_tool_table and not skipping_inline and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key == "mutmut":
                skipping_inline = True
                braces = stripped.count("{") - stripped.count("}")
                if braces <= 0:
                    skipping_inline = False
                continue
        if skipping_inline:
            braces += stripped.count("{") - stripped.count("}")
            if braces <= 0:
                skipping_inline = False
            continue
        out.append(line)
    return "".join(out)


def _scoped_pyproject(data: bytes) -> bytes | None:
    """Return only the measured mutmut-table transformation, if needed."""
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not _pyproject_has_tool_mutmut(content):
        return None
    return _hide_tool_mutmut(content).encode("utf-8")


def _resolve_mutmut_invocation(
    baseline_cmd: list[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> list[str] | None:
    """Resolve the mutmut invocation from the baseline test command.

    mutmut 3.x runs pytest in its OWN interpreter (pytest.main in-process),
    so mutmut must live in the same environment as the project test deps.
    A bare PATH mutmut from another interpreter fails collection with
    exit 4 -> exit 1 and, worse, can shadow the mutants mirror. When the
    baseline runner is a path into a venv, use its sibling python; only a
    bare command falls back to PATH resolution.

    Returns the command prefix (without 'run'/'results'), or None when
    mutmut is unavailable in the resolved environment.
    """
    runner = baseline_cmd[0] if baseline_cmd else ""
    if "/" in runner or "\\" in runner:
        # python3.exe -m pytest: the runner IS the interpreter.
        # pytest.exe: sibling python.exe (keep the suffix).
        # Split on both separators so a Windows path still
        # resolves when this code runs under POSIX tests.
        posix = runner.replace("\\", "/")
        name = posix.rsplit("/", 1)[-1]
        # Trailing separator: the path is already a directory.
        dirpart = runner[: -len(name)] if name else runner
        stem, ext = os.path.splitext(name)
        if stem.lower().startswith("python"):
            python = runner
        else:
            python = dirpart + "python" + ext
        try:
            probe = run_owned_command(
                [python, "-c", "import mutmut"],
                capture_output=True,
                timeout=30,
                check=False,
                cwd=cwd,
                env=env,
            )
        except subprocess.TimeoutExpired:
            raise
        except OSError:
            return None
        if probe.returncode != 0:
            return None
        return [python, "-m", "mutmut"]
    if shutil.which("mutmut") is None:
        return None
    return ["mutmut"]


_NATIVE_STATUS = {
    0: "survived",
    1: "killed",
    3: "killed",
    5: "no tests",
    2: "check was interrupted by user",
    33: "no tests",
    34: "skipped",
    35: "suspicious",
    36: "timeout",
    37: "caught by type check",
    -24: "timeout",
    24: "timeout",
    152: "timeout",
    255: "timeout",
    -11: "segfault",
    -9: "segfault",
}
_MUTANT_NAME = re.compile(r"[^:\r\n]+__mutmut_[0-9]+$")


def _result_records(stdout: str) -> tuple[dict[str, str], list[str]]:
    records: dict[str, str] = {}
    warnings = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        name, separator, status = line.strip().partition(": ")
        if not separator or not _MUTANT_NAME.fullmatch(name):
            warnings.append(f"unparseable mutmut result: {line.strip()}")
        elif status not in set(_NATIVE_STATUS.values()) | {"not checked"}:
            warnings.append(f"unknown mutmut status: {name}: {status}")
        elif name in records:
            warnings.append(f"duplicate mutmut result: {name}")
        else:
            records[name] = status
    return records, warnings


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate mutation metadata key: {key}")
        result[key] = value
    return result


def _read_inventory_file(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError("mutation inventory must use regular files")
        chunks = []
        while block := os.read(descriptor, 1024 * 1024):
            chunks.append(block)
        final = os.fstat(descriptor)
        named = path.lstat()

        def identity(info):
            return (
                info.st_dev,
                info.st_ino,
                info.st_mode,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
            )

        if identity(opened) != identity(final) or identity(final) != identity(named):
            raise ValueError("mutation inventory changed while reading")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class MutationSourceGuard:
    """WORKING endpoint observations; no immutable execution/copy authority."""

    source_root: str
    base_sha: str
    head_sha: str
    git_diff: str
    paths: tuple[str, ...]
    diff_paths: tuple[str, ...]
    selection: str
    entries: tuple[tuple[str, int | None, bytes | None], ...]


def _guard_head(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD^{commit}"], cwd=root,
        capture_output=True, text=True, encoding="utf-8", errors="strict",
        check=False, timeout=15,
    )
    if result.returncode != 0:
        raise ValueError("mutation source guard cannot resolve HEAD")
    return result.stdout.strip()


def _guard_diff(root: Path, base_sha: str, paths: tuple[str, ...], selection: str) -> str:
    from .git import _MACHINE_DIFF_OPTIONS, working_tree_diff

    if selection == "WORKING":
        return working_tree_diff(base_sha, [Path(path) for path in paths], root)
    if selection != "TRACKED_WORKING" or paths:
        raise ValueError("unsupported mutation review diff selection")
    result = subprocess.run(
        ["git", "diff", *_MACHINE_DIFF_OPTIONS, base_sha], cwd=root,
        capture_output=True, text=True, encoding="utf-8", errors="strict",
        check=False, timeout=15,
    )
    if result.returncode != 0:
        raise ValueError("mutation source guard cannot repeat reviewed diff")
    return result.stdout


def _guard_entries(root: Path, paths: tuple[str, ...]) -> tuple[tuple[str, int | None, bytes | None], ...]:
    entries = []
    for name in paths:
        path = root / _lexical_source(name, root)
        relative = path.relative_to(root).as_posix()
        parents = []
        for parent in path.parents:
            if not parent.is_relative_to(root):
                break
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError("mutation source parents must be physical directories")
            parents.append((parent, info.st_dev, info.st_ino, info.st_mode))
        try:
            before = path.lstat()
        except FileNotFoundError as exc:
            if any((node.lstat().st_dev, node.lstat().st_ino, node.lstat().st_mode) != (dev, ino, mode)
                   for node, dev, ino, mode in parents):
                raise ValueError("mutation source parents changed during absence capture") from exc
            entries.append((relative, None, None))
            continue
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError("mutation source guard requires unaliased regular files")
        raw = _read_inventory_file(path)
        after = path.lstat()
        fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")
        if any(getattr(before, field) != getattr(after, field) for field in fields):
            raise ValueError("mutation source changed during endpoint capture")
        if any((node.lstat().st_dev, node.lstat().st_ino, node.lstat().st_mode) != (dev, ino, mode)
               for node, dev, ino, mode in parents):
            raise ValueError("mutation source parents changed during endpoint capture")
        entries.append((relative, before.st_mode, raw))
    if len({row[0] for row in entries}) != len(entries):
        raise ValueError("mutation source scope contains aliases")
    return tuple(entries)


def capture_mutation_source_guard(
    *, source_root: Path, target_kind: str | None, base_sha: str | None,
    head_sha: str | None, git_diff: str | None, paths: tuple[str, ...],
    diff_paths: tuple[str, ...] | None, selection: str = "WORKING",
) -> MutationSourceGuard | None:
    """Refuse an unsupported/incoherent WORKING review before native execution."""
    if (target_kind != "WORKING" or type(git_diff) is not str or not git_diff
            or type(paths) is not tuple or not paths or type(diff_paths) is not tuple
            or any(type(path) is not str or not path for path in paths + diff_paths)
            or any(type(value) is not str or re.fullmatch(r"[0-9a-f]{40}", value) is None
                   for value in (base_sha, head_sha))):
        return None
    try:
        root = Path(os.path.abspath(source_root))
        for node in (*reversed(root.parents), root):
            if not stat.S_ISDIR(node.lstat().st_mode):
                return None
        if "\nrename from " in git_diff or "\ncopy from " in git_diff:
            return None
        if _guard_head(root) != head_sha:
            return None
        before = _guard_entries(root, paths)
        diff_before = _guard_diff(root, base_sha, diff_paths, selection)
        after = _guard_entries(root, paths)
        diff_after = _guard_diff(root, base_sha, diff_paths, selection)
        if before != after or _guard_head(root) != head_sha:
            return None
        if diff_before != git_diff or diff_after != git_diff:
            return None
        from .diff import get_removed_files

        removed = set(get_removed_files(git_diff))
        if any(raw is None and name not in removed for name, _mode, raw in after):
            return None
        return MutationSourceGuard(str(root), base_sha, head_sha, git_diff, paths,
                                   diff_paths, selection, after)
    except (OSError, ValueError, TypeError, BaselineResolutionError, subprocess.SubprocessError):
        return None


def mutation_source_guard_matches(guard: MutationSourceGuard | None, *, source_root: Path) -> bool:
    """Compare current endpoint only; equal endpoints do not prove lifetime binding."""
    if type(guard) is not MutationSourceGuard:
        return False
    try:
        root = Path(os.path.abspath(source_root))
        if str(root) != guard.source_root:
            return False
        for node in (*reversed(root.parents), root):
            if not stat.S_ISDIR(node.lstat().st_mode):
                return False
        return (_guard_head(root) == guard.head_sha
                and _guard_entries(root, guard.paths) == guard.entries
                and _guard_entries(root, guard.paths) == guard.entries
                and _guard_head(root) == guard.head_sha)
    except (OSError, ValueError, TypeError, BaselineResolutionError, subprocess.SubprocessError):
        return False


def is_native_mutation_runner(runner, *, source_root: Path) -> bool:
    """Recognize only the canonical runner and its exact source-bound factory."""
    from functools import partial

    if runner is run_mutation:
        return True
    if type(runner) is partial and runner.func is run_mutation:
        if not runner.args and set(runner.keywords) == {"cwd"}:
            try:
                return Path(runner.keywords["cwd"]).resolve() == source_root.resolve()
            except (OSError, ValueError, TypeError):
                return False
    return False


def executable_review_sources(source_files: list[Path], git_diff: str | None, source_root: Path) -> list[Path]:
    """Project semantic scope onto entries with a reviewed post-image."""
    if git_diff is None:
        return source_files
    from .diff import get_removed_files

    removed = {os.path.abspath(source_root / path) for path in get_removed_files(git_diff)}
    return [path for path in source_files if os.path.abspath(source_root / path) not in removed]


def _valid_globs(value) -> list[str] | None:
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    return None


def trusted_mutation_globs(
    *, config_root, source_root, base_sha: str | None, config_test: dict | None = None,
    notes: list[str] | None = None, allow_untracked: bool = True,
) -> tuple[list[str] | None, list[str] | None]:
    """Mutation skip/include globs from a source the reviewed change cannot edit
    (#134), by the same rule as every other ``test.*`` key (#140): see
    ``gate_check.load_trusted_gate_config``.

    - An explicit config root outside the reviewed tree: its already-loaded
      ``test`` block (``config_test``) when given.
    - Otherwise the trusted gate.yaml: the review baseline's copy, or an
      untracked/gitignored checked-out file that no reviewed change can edit.
    - No trusted gate.yaml (none at all, or one the change itself adds): the
      built-in defaults (None, None).
    - A trusted gate.yaml that cannot be read or is invalid: the defaults,
      and a note is appended to ``notes`` so the fallback is visible.
    """
    from .gate_check import config_root_is_outside_review, load_trusted_gate_config

    if config_test is not None and config_root_is_outside_review(config_root, source_root):
        test = config_test
    else:
        try:
            test = load_trusted_gate_config(
                config_root=config_root, source_root=source_root, base_sha=base_sha,
                allow_untracked=allow_untracked,
            ).get("test") or {}
        except FileNotFoundError:
            return (None, None)
        except (OSError, ValueError, TypeError) as exc:
            if notes is not None:
                notes.append(
                    "mutation globs: trusted .code-forge/gate.yaml is unreadable or invalid "
                    f"({exc}); using the default test-path globs"
                )
            return (None, None)
    return _valid_globs(test.get("mutation_skip_globs")), _valid_globs(test.get("mutation_include_globs"))


def mutation_review_admission_error(
    *, target_kind: str | None, git_diff: str | None, source_files: list[Path],
    source_root: Path, config_root: Path, runner, ci_detached: bool = False,
    base_sha: str | None = None, allow_untracked: bool = True,
) -> str | None:
    """Reject unsupported staged/committed native work before it starts."""
    if target_kind not in {"INDEX", "COMMIT"} or not git_diff:
        return None
    native = is_native_mutation_runner(runner, source_root=source_root)
    detached = ci_detached and shutil.which("mutmut") is not None
    if not native and not detached:
        return None
    from .gate_check import load_trusted_gate_config
    from .mutation_dispatch import group_by_adapter

    files = executable_review_sources(source_files, git_diff, source_root)
    py_files = group_by_adapter([str(path) for path in files]).get("python-mutmut", [])
    if not py_files:
        return None
    try:
        test = load_trusted_gate_config(
            config_root=config_root, source_root=source_root, base_sha=base_sha,
            allow_untracked=allow_untracked,
        )["test"]
    except (OSError, ValueError, TypeError):
        return None
    try:
        root = Path(os.path.abspath(source_root))
        py_files = [_lexical_source(path, root).as_posix() for path in py_files]
    except ValueError:
        return None
    skip, include = trusted_mutation_globs(
        config_root=config_root, source_root=source_root, base_sha=base_sha, config_test=test,
        allow_untracked=allow_untracked,
    )
    selected = native and any(not _is_test_path(path, skip, include, source_root) for path in py_files)
    if detached:
        selected = selected or any(not _is_test_path(path, skip, include, source_root) for path in py_files)
    if not selected:
        return None
    if target_kind == "COMMIT":
        return (
            "COMMIT source is unsupported for native mutation review; "
            "choose an explicit baseline with --head WORKING to review physical current files "
            "(for the last commit: --baseline HEAD~1 --head WORKING), "
            "or use a static/non-native review path"
        )
    return (
        "INDEX source is unsupported for native mutation review; "
        "rerun with --head WORKING to review physical current files, "
        "or use a static/non-native review path"
    )


def _mutation_inventory(repo_root: str, py_files: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    inventory: dict[str, str] = {}
    digests = {}
    root = Path(repo_root)
    for name in py_files:
        relative = Path(name)
        if relative.is_absolute():
            relative = relative.relative_to(root)
        if ".." in relative.parts:
            raise ValueError("mutation source is outside the project root")
        mirror = root / "mutants" / relative
        meta_path = mirror.with_name(mirror.name + ".meta")
        mirror_components = [root / "mutants" / component for component in (relative, *relative.parents)]
        if meta_path.is_symlink() or any(path.is_symlink() for path in mirror_components):
            raise ValueError("mutation inventory must use regular files")
        generated = ast.parse(_read_inventory_file(mirror).decode("utf-8"))
        module = relative.with_suffix("").as_posix().replace("/", ".").removeprefix("src.")
        keys = {
            f"{module}.{node.name}".replace(".__init__.", ".")
            for node in ast.walk(generated)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and re.search(r"__mutmut_[0-9]+$", node.name)
        }
        raw = _read_inventory_file(meta_path)
        meta = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(meta, dict) or not isinstance(meta.get("exit_code_by_key"), dict):
            raise TypeError(f"invalid mutation metadata: {relative}")
        exits = meta["exit_code_by_key"]
        if set(exits) != keys:
            raise ValueError(f"generated and recorded mutant identities disagree: {relative}")
        for key, code in exits.items():
            if key in inventory or type(code) is not int or code not in _NATIVE_STATUS:
                raise ValueError(f"incomplete or unknown mutation result: {key}: {code}")
            status = _NATIVE_STATUS[code]
            if status not in {"survived", "killed", "caught by type check", "timeout"}:
                raise ValueError(f"mutation measurement is incomplete: {key}: {status}")
            inventory[key] = status
        digests[str(relative)] = hashlib.sha256(raw).hexdigest()
    return inventory, digests


def parse_mutmut_results(stdout: str) -> tuple[list[Survivor], list[str]]:
    """Parse mutmut 3.x results output to extract surviving mutants.

    Expected format from mutmut 3.x results command:
        {indent}{module}.{mangled_fn}__mutmut_{N}: {status}

    Where status is one of: survived, killed, no tests, not checked,
    timeout, suspicious, check was interrupted by user.

    Only lines with status "survived" are returned as Survivor objects.

    The mutant_name is the full identifier (e.g. "add.x_add__mutmut_1").
    The file field is empty string since mutmut 3.x results do not include
    file paths; callers that need file attribution must use py_files list.

    Args:
        stdout: raw stdout from mutmut results subprocess

    Returns:
        tuple[list[Survivor], list[str]] where second element is warnings
        about unparseable lines. Never raises.
    """
    records, warnings = _result_records(stdout)
    return [
        Survivor(mutant_name=name, file="") for name, status in records.items() if status == "survived"
    ], warnings


def _process_error(message: str) -> StateFinding:
    return StateFinding(
        id="MUTATION_ERROR",
        fingerprint="mutation-evidence-error",
        source="INFRA",
        disposition=Disposition.CONFIRMED,
        file="",
        line_range=[],
        description=message,
    )


def _lexical_source(name: str, root: Path) -> Path:
    path = Path(name.replace("\\", "/"))
    if ".." in path.parts:
        raise ValueError(f"mutation source is outside the project root: {name}")
    if path.is_absolute():
        try:
            path = path.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"mutation source is outside the project root: {name}") from exc
    return path


def _relative_sources(py_files: list[str], cwd: Path) -> list[str]:
    root = Path(os.path.abspath(cwd))
    relative = []
    seen = set()
    for name in py_files:
        path = _lexical_source(name, root)
        if any((root / component).is_symlink() for component in [path, *path.parents]):
            raise ValueError(f"mutation source links are not supported: {name}")
        mirror = root / "mutants" / path
        mirror_components = [root / "mutants" / component for component in (path, *path.parents)]
        if mirror.with_name(mirror.name + ".meta").is_symlink() or any(
            component.is_symlink() for component in mirror_components
        ):
            raise ValueError(f"mutation mirror links are not supported: {name}")
        canonical = path.as_posix()
        if canonical not in seen:
            seen.add(canonical)
            relative.append(canonical)
    return relative


def _source_is_absent(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return True
    return False


def _without_deleted_sources(py_files: list[str], cwd: Path) -> list[str]:
    missing = {name for name in py_files if _source_is_absent(cwd / name)}
    if not missing or not (cwd / ".git").exists():
        return py_files
    try:
        result = run_owned_command(
            [
                "git",
                "--no-optional-locks",
                "diff",
                "--relative",
                "--no-renames",
                "--no-ext-diff",
                "--no-textconv",
                "--name-only",
                "-z",
                "--diff-filter=D",
                "HEAD",
                "--",
            ],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return py_files
    if result.returncode != 0 or (result.stdout and not result.stdout.endswith("\0")):
        return py_files
    deleted = set(result.stdout.split("\0"))
    return [
        name
        for name in py_files
        if name not in missing or name not in deleted or not _source_is_absent(cwd / name)
    ]


def _mutation_command_error(phase: str, result: subprocess.CompletedProcess) -> StateFinding:
    """Keep both diagnostic tails: pytest commonly reports failures on stdout."""
    streams = []
    for name, text in (("stdout", result.stdout), ("stderr", result.stderr)):
        if text and text.strip():
            streams.append(f"{name}: {text.strip()[-1000:]}")
    detail = "\n".join(streams) or "no output captured"
    return StateFinding(
        id="MUTATION_ERROR",
        fingerprint=f"mutation-{phase}-error",
        source="INFRA",
        disposition=Disposition.CONFIRMED,
        file="",
        line_range=[],
        description=f"mutmut {phase} failed (exit {result.returncode}): {detail}",
    )


OTHER_ADAPTER_INFRA = "diff has no Python files; other adapters are registered but not invoked here"


def run_mutation(
    diff_files: list[str],
    baseline_cmd: list[str],
    timeout: int = DEFAULT_MUTATION_TIMEOUT,
    cwd: Path | None = None,
    baseline_timeout: int = 120,
    also_copy: list[str] | None = None,
    max_children: int | None = None,
    memory_limit_bytes: int | None = None,
    mutation_skip_globs: list[str] | None = None,
    mutation_include_globs: list[str] | None = None,
    *,
    _evidence: dict | None = None,
) -> tuple[list[StateFinding], list[str]]:
    """Measure the selected source with owned processes and an exclusive fresh mirror.

    Return the existing (findings, infra_errors) pair. The optional private
    evidence dictionary retains measured proof for the detached producer.
    """
    validate_mutation_timeout(timeout)
    evidence = {} if _evidence is None else _evidence
    root = Path.cwd() if cwd is None else cwd
    workspace = MutationWorkspace(os.path.abspath(root))
    outcome: tuple[list[StateFinding], list[str]] = ([], [])
    cancellation = None
    workspace_error = None

    def failed(exc):
        nonlocal outcome
        message = str(exc)
        if isinstance(exc, OrphanMutationContainerError):
            # #136: structured marker; the machine never matches free text.
            evidence["workspace_orphan"] = {"path": str(exc.path), "message": message}
        findings = outcome[0] + [_process_error(message)]
        infra = outcome[1] + [message]
        evidence["baseline_passed"] = False
        evidence["completed_measurement"] = False
        evidence["infra_errors"] = infra
        outcome = findings, infra

    def record_cancellation(exc, cleanup_allowed):
        try:
            cleanup_complete = getattr(exc, "cleanup_complete", False) is True
            ownership = getattr(exc, "ownership", {})
            workspace.cleanup_complete = cleanup_allowed and cleanup_complete
            evidence.update(process_failure=ownership, cancelled=True,
                            baseline_passed=False, completed_measurement=False)
        except BaseException as evidence_exc:  # noqa: BLE001 - an interrupted proof cannot authorize restoration
            workspace.cleanup_complete = False
            _bind_cancellation_evidence(exc, cleanup_complete=False, cleanup_evidence_error=evidence_exc)
            evidence.update(process_failure={}, cancelled=True,
                            baseline_passed=False, completed_measurement=False)

    try:
        try:
            outcome = _run_mutation(
                diff_files,
                baseline_cmd,
                timeout,
                root,
                baseline_timeout,
                also_copy,
                max_children,
                memory_limit_bytes,
                mutation_skip_globs,
                mutation_include_globs,
                _evidence=evidence,
                _workspace=workspace,
            )
        except MutationProcessError as exc:
            workspace.cleanup_complete &= exc.cleanup_complete
            evidence["process_failure"] = exc.report
            failed(exc)
        except (MutationWorkspaceError, OSError) as exc:
            failed(exc)
    except BaseException as exc:  # noqa: BLE001 - bind cleanup evidence without consuming cancellation
        if isinstance(exc, Exception):
            raise
        cancellation = exc
        try:
            cleanup_allowed = workspace.cleanup_complete
            workspace.cleanup_complete = False
            record_cancellation(exc, cleanup_allowed)
        except BaseException as evidence_exc:  # noqa: BLE001 - retain the selected object if metadata dispatch is interrupted
            workspace.cleanup_complete = False
            _bind_cancellation_evidence(exc, cleanup_complete=False, cleanup_evidence_error=evidence_exc)
            evidence.update(process_failure={}, cancelled=True,
                            baseline_passed=False, completed_measurement=False)
    finally:
        try:
            try:
                workspace.release()
            except BaseException as exc:  # noqa: BLE001 - teardown failure remains secondary to cancellation
                if cancellation is not None:
                    workspace_error = exc
                    workspace.cleanup_complete = False
                    _bind_cancellation_evidence(
                        cancellation, cleanup_complete=False, workspace_cleanup_error=exc,
                        note=f"mutation workspace cleanup failed: {exc}",
                    )
                    evidence["cleanup_error"] = str(exc)
                elif isinstance(exc, (MutationWorkspaceError, OSError)):
                    failed(exc)
                else:
                    raise
        except BaseException as evidence_exc:  # noqa: BLE001 - diagnostic failure cannot replace cancellation
            if cancellation is None:
                if isinstance(evidence_exc, Exception):
                    raise
                cancellation = evidence_exc
                try:
                    cleanup_allowed = workspace.cleanup_complete
                    workspace.cleanup_complete = False
                    record_cancellation(evidence_exc, cleanup_allowed)
                except BaseException as secondary:  # noqa: BLE001 - a later interruption cannot replace the selected object
                    workspace.cleanup_complete = False
                    _bind_cancellation_evidence(evidence_exc, cleanup_complete=False, cleanup_evidence_error=secondary)
                    evidence.update(process_failure={}, cancelled=True,
                                    baseline_passed=False, completed_measurement=False)
            else:
                _bind_cancellation_evidence(
                    cancellation, cleanup_complete=False,
                    workspace_cleanup_error=workspace_error if workspace_error is not None else evidence_exc,
                    cleanup_evidence_error=evidence_exc,
                )
        finally:
            if cancellation is not None:
                raise cancellation
    return outcome


def _run_mutation(
    diff_files: list[str],
    baseline_cmd: list[str],
    timeout: int,
    cwd: Path,
    baseline_timeout: int,
    also_copy: list[str] | None,
    max_children: int | None,
    memory_limit_bytes: int | None,
    mutation_skip_globs: list[str] | None,
    mutation_include_globs: list[str] | None,
    *,
    _evidence: dict,
    _workspace: MutationWorkspace,
) -> tuple[list[StateFinding], list[str]]:
    """Run mutation testing on diff-scoped files.

    Returns tuple[list[StateFinding], list[str]] matching l0_runner
    signature for consistency.

    Args:
        diff_files: changed files from git diff --name-only (relative to cwd)
        baseline_cmd: test command to run for flaky guard and mutmut
        timeout: mutmut run timeout in seconds (default 600)
        cwd: project root for mutmut (default: Path.cwd()). Must be the
            directory containing src/ and tests/.
        also_copy: extra relative paths copied into the mutants/ mirror.
        max_children: mutmut --max-children cap. None applies the default
            min(cpu_count, 4); mutmut's own default of raw cpu_count fans
            out one full-suite pytest process per core and OOM'd the review
            service on a 16-core host.
        memory_limit_bytes: RLIMIT_AS ceiling inherited by the whole
            mutmut tree (parent + forked mutant children). A runaway
            child dies with MemoryError instead of the OOM killer taking
            the review service. Default 8 GiB.
        mutation_skip_globs: glob patterns for files to exclude from mutation.
        mutation_include_globs: glob patterns for files to include in mutation,
            taking precedence over skip globs.
        _evidence: optional per-call internal proof used by the detached producer.

    Implementation note:
        mutmut 3.x requires cwd to be the project root. User setup.cfg
        and pyproject.toml [tool.mutmut] are snapshotted, a scoped
        setup.cfg is installed for the run, and the originals are
        restored afterwards. mutants/ is removed too.

    Returns:
        (findings, infra_errors) where findings contains:
        - CONFIRMED MUTANT findings for survivors
        - DISMISSED MUTATION_SKIPPED findings for skip conditions
    """
    validate_mutation_timeout(timeout)
    evidence = {} if _evidence is None else _evidence
    evidence.clear()
    evidence.update(
        baseline_passed=False, completed_measurement=False, skipped=[], infra_errors=[], inventory={}
    )
    baseline_proven = False
    findings: list[StateFinding] = []
    infra_errors: list[str] = []

    repo_root = os.path.abspath(cwd)

    # #134: globs come only from the caller's trusted configuration. The
    # reviewed tree's own .code-forge/gate.yaml is never consulted here: a
    # change could otherwise exclude its own production files from mutation.

    # Empty files: no work
    if not diff_files:
        return ([], [])

    from .mutation_dispatch import group_by_adapter, other_adapter_note

    grouped = group_by_adapter(diff_files)
    py_files = grouped.get("python-mutmut", [])
    other = [key for key in grouped if key and key != "python-mutmut"]
    if not py_files:
        if other:
            description = other_adapter_note(diff_files, root=cwd)
            fingerprint = "mutation-other-adapter"
            infra = OTHER_ADAPTER_INFRA
        else:
            description = "no registered mutation adapter for this diff"
            fingerprint = "mutation-no-adapter"
            infra = ""
        findings.append(
            StateFinding(
                id="MUTATION_SKIPPED",
                fingerprint=fingerprint,
                source="MUTANT",
                disposition=Disposition.DISMISSED,
                file="",
                line_range=[],
                description=description,
            )
        )
        if infra:
            infra_errors.append(infra)
        evidence["skipped"] = [description]
        evidence["infra_errors"] = list(infra_errors)
        return (findings, infra_errors)

    try:
        py_files = [_lexical_source(name, Path(repo_root)).as_posix() for name in py_files]
        py_files = [
            name
            for name in py_files
            if not _is_test_path(name, mutation_skip_globs, mutation_include_globs, cwd)
        ]
        py_files = _relative_sources(py_files, cwd)
        py_files = _without_deleted_sources(py_files, cwd)
    except ValueError as exc:
        error = _process_error(str(exc))
        evidence["infra_errors"] = [error.description]
        return [error], [error.description]

    def _with_other(out_findings: list[StateFinding], out_infra: list[str]):
        # Applicability notes for a sibling must not erase a completed
        # supported measurement. Keep the aggregate baseline flag below
        # separate from this proof established before sibling decoration.
        from .mutation_findings import is_mutation_diagnostic

        evidence["completed_measurement"] = (
            baseline_proven
            and not out_infra
            and not any(
                f.id in ("MUTATION_SKIPPED", "MUTATION_ERROR") or is_mutation_diagnostic(f)
                for f in out_findings
            )
        )
        if other:
            out_findings.append(
                StateFinding(
                    id="MUTATION_SKIPPED",
                    fingerprint="mutation-other-adapter",
                    source="MUTANT",
                    disposition=Disposition.DISMISSED,
                    file="",
                    line_range=[],
                    description=other_adapter_note(diff_files),
                )
            )
        evidence["skipped"] = [f.description for f in out_findings if f.id == "MUTATION_SKIPPED"]
        evidence["infra_errors"] = list(out_infra)
        evidence["baseline_passed"] = (
            baseline_proven
            and not out_infra
            and not evidence["skipped"]
            and not any(f.id == "MUTATION_ERROR" for f in out_findings)
        )
        return (out_findings, out_infra)

    roots = _source_roots(
        py_files,
        skip_globs=mutation_skip_globs,
        include_globs=mutation_include_globs,
        cwd=cwd,
    )
    if not roots:
        findings.append(
            StateFinding(
                id="MUTATION_SKIPPED",
                fingerprint="mutation-tests-only",
                source="MUTANT",
                disposition=Disposition.DISMISSED,
                file="",
                line_range=[],
                description="diff is tests-only; nothing to mutate",
            )
        )
        return _with_other(findings, [])

    _workspace.acquire()

    # Flaky guard: run the baseline 3x at the repo root.
    #
    # Start with the inherited environment (including VIRTUAL_ENV if
    # set). If the first attempt fails because the test runner itself
    # is missing -- not because tests failed -- strip VIRTUAL_ENV and
    # retry once. This handles ephemeral runners (e.g. uv injects
    # pytest into the run but not into the venv) without regressing
    # the normal case where the venv has the runner and project deps.
    run_env = os.environ.copy()
    pythonpath = os.path.join(repo_root, "src")
    run_env["PYTHONPATH"] = pythonpath
    retry_cause: list[str] = []
    # mutmut runs pytest in its own process and inherits this env. The
    # review service's PATH leads with the forge venv, so project tools
    # (kextractlinux and the like) are invisible. When the baseline runner
    # is a path into a virtualenv, lead PATH with that env's bin.
    runner = baseline_cmd[0] if baseline_cmd else ""
    if "/" in runner or "\\" in runner:
        project_bin = os.path.dirname(os.path.abspath(runner))
        if os.path.isdir(project_bin):
            run_env["PATH"] = project_bin + os.pathsep + run_env.get("PATH", "")

    try:
        status, guard_findings, guard_infra = _run_baseline_guard(
            baseline_cmd,
            run_env,
            repo_root,
            allow_strip_retry=True,
            retry_cause=retry_cause,
            timeout=baseline_timeout,
            run_command=run_owned_command,
        )
        if status == "needs_strip_retry":
            run_env = _strip_venv_from_env(run_env)
            run_env["PYTHONPATH"] = pythonpath
            status, guard_findings, guard_infra = _run_baseline_guard(
                baseline_cmd,
                run_env,
                repo_root,
                allow_strip_retry=False,
                first_attempt=retry_cause[0] if retry_cause else None,
                timeout=baseline_timeout,
                run_command=run_owned_command,
            )
    except MutationProcessError as exc:
        _workspace.cleanup_complete = exc.cleanup_complete
        evidence["process_failure"] = exc.report
        error = _process_error(str(exc))
        return _with_other([error], [error.description])
    if status == "skip":
        return _with_other(guard_findings, guard_infra)
    if status != "passed":
        error = _process_error(f"baseline guard returned an unknown status: {status}")
        return _with_other([error], [error.description])
    baseline_proven = status == "passed"

    # Resolve the mutmut invocation from the baseline environment. mutmut
    # must share the interpreter with the project test deps (it drives
    # pytest.main in-process); a foreign PATH mutmut only produces
    # collection errors, so its absence is a clean skip, not an error.
    try:
        invocation = _resolve_mutmut_invocation(baseline_cmd, cwd=repo_root, env=run_env)
    except MutationProcessError as exc:
        _workspace.cleanup_complete = exc.cleanup_complete
        evidence["process_failure"] = exc.report
        error = _process_error(str(exc))
        return _with_other([error], [error.description])
    except subprocess.TimeoutExpired:
        findings.append(
            StateFinding(
                id="MUTATION_SKIPPED",
                fingerprint="mutation-probe-timeout",
                source="MUTANT",
                disposition=Disposition.DISMISSED,
                file="",
                line_range=[],
                description=(
                    "mutmut import probe timed out in baseline test env; not the same as mutmut missing"
                ),
            )
        )
        return _with_other(findings, [])
    if invocation is None:
        runner = baseline_cmd[0] if baseline_cmd else ""
        if os.sep in runner:
            desc = f"mutmut not installed in the baseline test env ({runner})"
        else:
            desc = "mutmut not installed (soft dependency)"
        findings.append(
            StateFinding(
                id="MUTATION_SKIPPED",
                fingerprint="mutation-unavailable",
                source="MUTANT",
                disposition=Disposition.DISMISSED,
                file="",
                line_range=[],
                description=desc,
            )
        )
        return _with_other(findings, [])

    # The leased workspace owns configuration recovery as well as the
    # fresh native mirror; originals are never overwritten by name alone.
    cleanup_complete = True
    try:
        _workspace.prepare()
        # mutmut renamed the key in 3.4: source_paths replaced paths_to_mutate.
        # 3.3 does not recognise the new name and falls back to guessing the
        # source tree, which silently widens the run past the diff scope, so
        # pyproject pins >=3.4. Paths stay relative to the project root.
        config_content = _build_mutmut_config(
            py_files,
            baseline_cmd,
            also_copy,
            skip_globs=mutation_skip_globs,
            include_globs=mutation_include_globs,
            cwd=cwd,
        )
        _workspace.install_configs(config_content.encode("utf-8"), _scoped_pyproject)

        # mutmut sets MUTANT_UNDER_TEST for its trampoline and the value is
        # inherited by every subprocess a test starts. The trampoline then
        # looks for mutmut's configuration in that child's working directory.
        # A test that runs the mutated code from outside the mirror has none,
        # so stats collection dies before the code under test runs. The plugin
        # removes the variable once pytest has started; the trampoline keeps a
        # process-local copy, so measurement inside pytest is unaffected.
        # mutmut creates mutants/ itself and only writes the files it copies,
        # so a plugin and a pytest.ini planted here survive.
        _plant_child_env_plugin(repo_root)

        # mutmut 3.x rewrites sys.path itself (inserts mutants/src, then
        # chdir into mutants/ before pytest.main). Pointing PYTHONPATH at
        # mutants/src here races the directory that does not exist yet and
        # breaks collection. Inherit the baseline env; mutmut drops the
        # original src entry after it has built the mirror.
        children = _effective_max_children(max_children)
        address_space = _memory_limit_bytes(memory_limit_bytes)
        try:
            result = run_owned_command(
                invocation + ["run", "--max-children", str(children)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                env=run_env,
                cwd=repo_root,
                memory_limit_bytes=address_space,
            )

            # Any non-zero exit is an error (attempt 2 bug: only caught ==2)
            if result.returncode != 0:
                error = _mutation_command_error("run", result)
                return _with_other([error], [error.description])

        except subprocess.TimeoutExpired:
            findings.append(
                StateFinding(
                    id="MUTATION_SKIPPED",
                    fingerprint="mutation-timeout",
                    source="MUTANT",
                    disposition=Disposition.DISMISSED,
                    file="",
                    line_range=[],
                    description="mutmut timed out after %ds" % timeout,
                )
            )
            return _with_other(findings, [])

        # Parse results from repo_root
        try:
            inventory, metadata_digests = _mutation_inventory(
                repo_root,
                [
                    f
                    for f in py_files
                    if not _is_test_path(f, mutation_skip_globs, mutation_include_globs, cwd)
                ],
            )
            evidence["inventory"] = inventory
            evidence["metadata_sha256"] = metadata_digests
            results_proc = run_owned_command(
                invocation + ["results", "--all", "true"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
                cwd=repo_root,
                env=run_env,
            )
            if results_proc.returncode != 0:
                error = _mutation_command_error("results", results_proc)
                return _with_other([error], [error.description])
            records, parse_warnings = _result_records(results_proc.stdout)
            if parse_warnings or records != inventory:
                raise ValueError(
                    "mutmut result transport disagrees with generated inventory: "
                    + "; ".join(parse_warnings)
                )
            evidence["results_sha256"] = hashlib.sha256(results_proc.stdout.encode("utf-8")).hexdigest()
            if not inventory:
                findings.append(
                    StateFinding(
                        id="MUTATION_SKIPPED",
                        fingerprint="mutation-no-mutants",
                        source="MUTANT",
                        disposition=Disposition.DISMISSED,
                        file="",
                        line_range=[],
                        description="mutmut generated no mutants; no mutation measurement was made",
                    )
                )
                return _with_other(findings, [])
            survivors, parse_warnings = parse_mutmut_results(results_proc.stdout)
            infra_errors.extend(parse_warnings)
        except subprocess.TimeoutExpired:
            findings.append(
                StateFinding(
                    id="MUTATION_SKIPPED",
                    fingerprint="mutation-results-timeout",
                    source="MUTANT",
                    disposition=Disposition.DISMISSED,
                    file="",
                    line_range=[],
                    description="mutmut results timed out",
                )
            )
            return _with_other(findings, [])
        except (TypeError, ValueError, OSError, SyntaxError) as exc:
            error = _process_error(f"mutmut result evidence failed: {exc}")
            return _with_other([error], [error.description])

        findings.extend(
            StateFinding(
                id=f"mutant-{survivor.mutant_name}",
                fingerprint=f"mutant:{survivor.mutant_name}",
                source="MUTANT",
                disposition=Disposition.CONFIRMED,
                file=survivor.file,
                line_range=[0, 0],  # mutmut 3.x results omit line numbers
                description=(f"mutant survived: {survivor.mutant_name}"),
            )
            for survivor in survivors
        )
        findings.extend(
            StateFinding(
                id=f"mutation-timeout-{name}",
                fingerprint=f"mutation-timeout:{name}",
                source="MUTANT",
                disposition=Disposition.DISMISSED,
                file="",
                line_range=[],
                description=f"mutant timed out: {name}",
            )
            for name, status in inventory.items()
            if status == "timeout"
        )

    except MutationProcessError as exc:
        cleanup_complete = exc.cleanup_complete
        evidence["process_failure"] = exc.report
        error = _process_error(str(exc))
        return _with_other([error], [error.description])
    finally:
        _workspace.cleanup_complete = _workspace.cleanup_complete and cleanup_complete

    return _with_other(findings, infra_errors)


def _publish_mutation_error(
    result_path: Path, data: dict, error: BaseException, evidence: dict,
    publication_error: BaseException | None = None,
) -> None:
    """Publish terminal non-measurement evidence without changing cleanup proof."""
    cancelled = not isinstance(error, Exception)
    data.update(
        status="error", cancelled=cancelled,
        cancellation_type=type(error).__name__ if cancelled else None,
        cleanup_complete=getattr(error, "cleanup_complete", None),
        process_failure=evidence.get("process_failure", {}),
        cleanup_error=evidence.get("cleanup_error"),
        baseline_passed=False, completed_measurement=False, survivors=[],
        survivor_details=[], review_context=None,
        message=str(error), skipped=[], infra_errors=[str(error)],
        inventory=evidence.get("inventory", {}),
    )
    if publication_error is not None:
        data["publication_error"] = f"{type(publication_error).__name__}: {publication_error}"
    with open(result_path, "w", encoding="utf-8") as stream:
        json.dump(data, stream)


def _record_mutation_publication_failure(
    error: BaseException, failure: BaseException, *, attribute: str, note_prefix: str,
) -> BaseException:
    """Retain a pending cancellation while best-effort publication diagnostics run."""
    try:
        _bind_cancellation_evidence(error, **{attribute: failure}, note=f"{note_prefix}: {failure}")
    except BaseException as diagnostic_error:  # noqa: BLE001 - diagnostics must retain the selected cancellation
        if isinstance(error, Exception) and not isinstance(diagnostic_error, Exception):
            error = diagnostic_error
        try:
            _bind_cancellation_evidence(error, **{attribute: failure},
                                        detached_publication_diagnostic_error=diagnostic_error)
        except BaseException as fallback_error:  # noqa: BLE001 - select the first control interruption
            if isinstance(error, Exception) and not isinstance(fallback_error, Exception):
                error = fallback_error
    return error


def launch_detached_mutation(
    diff_files: list[str],
    baseline_cmd: list[str],
    cwd: Path,
    result_path: Path,
    baseline_timeout: int = 120,
    also_copy: list[str] | None = None,
    max_children: int | None = None,
    memory_limit_bytes: int | None = None,
    mutation_skip_globs: list[str] | None = None,
    mutation_include_globs: list[str] | None = None,
    timeout: int = DEFAULT_MUTATION_TIMEOUT,
    *,
    review_context: dict | None = None,
) -> bool:
    """Launch the mutation run detached, reporting whether it started.

    Where os.fork exists the run is reparented to init, and True means
    the middle process exited cleanly. Elsewhere the run is a direct
    child and True only means Popen succeeded -- the caller still reads
    the outcome from result_path either way.

    The run writes its own pid into result_path; callers track it from
    there rather than from a handle held here.

    max_children and memory_limit_bytes are forwarded to run_mutation; see
    its docstring for the resource-guard semantics (mutmut defaults to
    cpu_count() children, which OOM'd the review service on a 16-core host).
    timeout limits mutmut run independently of the baseline test deadline.
    """
    validate_mutation_timeout(timeout)
    import json
    import os
    import subprocess
    import sys
    import time

    # Write initial data BEFORE launching the child so we never overwrite
    # the child's "done" status. pid=None means "not yet started"; the
    # reader guards against non-positive pids so None is safe.
    result_path.parent.mkdir(parents=True, exist_ok=True)
    initial_data = {
        "pid": None,
        "started_at": time.time(),
        "status": "running",
        "survivors": [],
        "baseline_passed": False,
        "skipped": [],
        "infra_errors": [],
        "inventory": {},
        "survivor_details": [],
        "completed_measurement": False,
        "review_context": review_context,
    }
    if review_context is not None:
        initial_data["survivor_details_version"] = 1
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(initial_data, f)

    forge_src = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    script = f"""
import os
import sys
import json
import time
from pathlib import Path

# Add src to path so code_forge is importable if run from source
sys.path.insert(0, {forge_src!r})
try:
    from code_forge.mutation import run_mutation, _mutation_outcome, _publish_mutation_error, _record_mutation_publication_failure
except ImportError:
    # Installed package layout: the cwd itself may be the package root
    import os as _os
    _os.chdir(str(Path({str(cwd)!r})))
    sys.path.insert(0, str(Path({str(cwd)!r})))
    from code_forge.mutation import run_mutation, _mutation_outcome, _publish_mutation_error, _record_mutation_publication_failure

result_path = Path({str(result_path)!r})
cwd_ref = Path({str(cwd)!r})
diff_files = {diff_files!r}
baseline_cmd = {baseline_cmd!r}
also_copy = {also_copy!r}
mutation_skip_globs = {mutation_skip_globs!r}
mutation_include_globs = {mutation_include_globs!r}
review_context = {review_context!r}

try:
    with open(result_path, "r", encoding="utf-8") as f:
        data = json.load(f)
except Exception:
    data = {{"started_at": time.time(), "status": "running", "survivors": []}}

data["pid"] = os.getpid()
try:
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
except Exception:
    pass

try:
    evidence = {{}}
    mm_findings, _infra = run_mutation(
        diff_files=diff_files,
        baseline_cmd=baseline_cmd,
        cwd=cwd_ref,
        timeout={timeout!r},
        baseline_timeout=int({baseline_timeout}),
        also_copy=also_copy,
        max_children={max_children!r},
        memory_limit_bytes={memory_limit_bytes!r},
        mutation_skip_globs=mutation_skip_globs,
        mutation_include_globs=mutation_include_globs,
        _evidence=evidence,
    )
    data.update(_mutation_outcome(mm_findings, _infra, evidence, review_context=review_context))
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
except BaseException as e:
    original_error = e
    try:
        _publish_mutation_error(result_path, data, e, evidence)
    except BaseException as publication_error:
        if isinstance(e, Exception) and not isinstance(publication_error, Exception):
            e = publication_error
        e = _record_mutation_publication_failure(e, publication_error,
            attribute="detached_publication_error", note_prefix="mutation result publication failed")
        try:
            _publish_mutation_error(result_path, data, e, evidence, publication_error)
        except BaseException as retry_error:
            if isinstance(e, Exception) and not isinstance(retry_error, Exception):
                e = retry_error
            e = _record_mutation_publication_failure(e, retry_error,
                attribute="detached_publication_retry_error", note_prefix="mutation result publication retry failed")
    finally:
        if not isinstance(e, Exception):
            if e is not original_error:
                raise e from original_error
            raise e
"""
    # The run reports its own pid into result_path, and every later
    # check reads it from there. Keeping it as a direct child only
    # buys a handle nobody waits on, so the finished run lingers as a
    # zombie. Fork once more and let init do the reaping; the short
    # lived middle process is the one waited for here.
    detaches_itself = hasattr(os, "fork")
    launch_script = (
        ("import os\nif os.fork():\n    os._exit(0)\n" + script) if detaches_itself else script
    )
    try:
        p = subprocess.Popen(
            [sys.executable, "-c", launch_script],
            start_new_session=True,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=cwd,
        )
        # Without fork the run itself is the child, so waiting on it
        # would block until the whole mutation pass finishes.
        if not detaches_itself:
            return p.pid > 0
        # The middle process only forks and exits; measured at ~30ms.
        # A generous ceiling still keeps the caller's budget intact.
        try:
            return p.wait(timeout=5) == 0
        except subprocess.TimeoutExpired:
            # It never exited, so it is ours to clean up; the run it
            # forked is already detached and keeps going.
            p.kill()
            p.wait()
            return False
    except Exception:  # noqa: BLE001
        return False
