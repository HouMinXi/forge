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
- A temporary setup.cfg is written to project root and cleaned up after.
- mutants/ directory created by mutmut is also cleaned up after each run.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .disposition import Disposition
from .state import StateFinding

# Marker in setup.cfg so we never accidentally overwrite user config
_CODE_FORGE_CFG_MARKER = "# managed-by-code-forge-mutation"


@dataclass
class Survivor:
    """A mutant that survived (no test killed it)."""
    mutant_name: str  # mutmut 3.x identifier e.g. "code_forge.mutation.x_run__mutmut_1"
    file: str         # source file (empty; mutmut 3.x results omit file paths)


_TEST_DIR_PREFIXES = ("tests/", "test/")
_TEST_DIR_PREFIXES_WIN = ("tests\\", "test\\")

def _source_roots(py_files: list[str]) -> list[str]:
    """Derive mirror roots from diff-scoped python files.

    mutmut copies only source_paths into the mutants/ mirror, and the
    tests then run from inside that mirror. Scoping source_paths to the
    diff FILES alone mirrors single files without their package, so
    imports die under mutants/. The mirror must carry the whole top-level
    source tree; diff scoping belongs to only_mutate instead.
    """
    roots: set[str] = set()
    for f in py_files:
        if f.startswith(_TEST_DIR_PREFIXES) or f.startswith(_TEST_DIR_PREFIXES_WIN):
            continue
        # Mutmut config is POSIX; Windows diffs still arrive with "\\".
        posix = f.replace("\\", "/")
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
            return list(baseline_cmd[i + 1:])
    return []


def _is_pytest_token(tok: str) -> bool:
    """True for pytest, .../pytest, pytest.exe, ...\\pytest.exe."""
    name = tok.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name.removesuffix(".exe") == "pytest"


def _build_mutmut_config(
    py_files: list[str],
    baseline_cmd: list[str],
    also_copy: list[str] | None = None,
) -> str:
    """Render the temporary [mutmut] setup.cfg content.

    source_paths mirrors whole source roots (importability), only_mutate
    keeps mutation diff-scoped, and the test selection reuses the gate's
    baseline arguments so stats collection runs exactly the tests the
    gate trusts.
    """
    roots = _source_roots(py_files)
    lines = [
        _CODE_FORGE_CFG_MARKER,
        "[mutmut]",
        "source_paths=" + ",".join(roots),
        "only_mutate=" + ",".join(py_files),
    ]
    selection = " ".join(_baseline_test_selection(baseline_cmd))
    if selection:
        lines.append(f"pytest_add_cli_args_test_selection={selection}")
    also_copy = [p for p in (also_copy or []) if p]
    if also_copy:
        # First path on the key line. An empty also_copy= plus
        # indented continuations parses, but leaves a leading
        # newline that mutmut then keeps as an empty Path.
        lines.append("also_copy=" + also_copy[0])
        lines.extend("    " + p for p in also_copy[1:])

    return "\n".join(lines) + "\n"


def _resolve_mutmut_invocation(baseline_cmd: list[str]) -> list[str] | None:
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
        dirpart = runner[:-len(name)] if name else runner
        stem, ext = os.path.splitext(name)
        if stem.lower().startswith("python"):
            python = runner
        else:
            python = dirpart + "python" + ext
        try:
            probe = subprocess.run(
                [python, "-c", "import mutmut"],
                capture_output=True,
                timeout=30,
                check=False,
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
    survivors = []
    warnings = []

    for line in stdout.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue

        # Format: "module.fn__mutmut_N: status"
        if ": " not in stripped:
            continue

        mutant_name, _, status = stripped.partition(": ")
        mutant_name = mutant_name.strip()
        status = status.strip()

        if not mutant_name:
            continue

        if status == "survived":
            survivors.append(Survivor(mutant_name=mutant_name, file=""))

    return survivors, warnings


def _is_runner_missing(
    baseline_cmd: list[str],
    result: subprocess.CompletedProcess | None,
    exc: Exception | None,
) -> bool:
    """Check whether a baseline failure means the runner itself is missing.

    Returns True only when the configured test runner could not start,
    NOT when tests ran and failed. Conservative: returns False when
    unsure, so that genuine failures are never masked by an env retry.

    Detection rules:
      - python3 -m <MOD> form: True if combined output contains
        "No module named '<MOD>'" or "No module named <MOD>",
        matching the RUNNER module specifically (not any project dep).
      - Bare binary form: True only on FileNotFoundError (binary
        not found on PATH).
    """
    # FileNotFoundError from subprocess.run means the binary is absent
    if isinstance(exc, FileNotFoundError):
        return True

    if result is None or result.returncode == 0:
        return False

    # Detect python -m <MOD> form. Only when the command starts with a
    # Python interpreter; otherwise -m is the tool's own flag (e.g.
    # pytest -m slow).  Flags like -W may appear between python and -m.
    interpreter = os.path.basename(baseline_cmd[0]) if baseline_cmd else ""
    if interpreter.startswith("python") and "-m" in baseline_cmd:
        m_idx = baseline_cmd.index("-m")
        if m_idx + 1 < len(baseline_cmd):
            runner_module = baseline_cmd[m_idx + 1]
            combined = (result.stdout or "") + (result.stderr or "")
            if ("No module named '%s'" % runner_module) in combined:
                return True
            if ("No module named %s" % runner_module) in combined:
                return True

    return False


def _strip_venv_from_env(env: dict[str, str]) -> dict[str, str]:
    """Return a copy of env with VIRTUAL_ENV removed and its bin dir
    stripped from PATH."""
    stripped = dict(env)
    venv_path = stripped.pop("VIRTUAL_ENV", None)
    if venv_path:
        path_val = stripped.get("PATH", "")
        stripped["PATH"] = os.pathsep.join(
            p for p in path_val.split(os.pathsep)
            if p and not p.startswith(venv_path)
        )
    return stripped


def _run_baseline_guard(
    baseline_cmd: list[str],
    run_env: dict[str, str],
    repo_root: str,
    *,
    allow_strip_retry: bool,
    timeout: int = 120,
) -> tuple[str, list[StateFinding], list[str]]:
    """Run the 3x flaky baseline guard and report the outcome.

    Returns (status, findings, infra_errors) where status is one of:
      "passed"            -- all 3 runs succeeded under run_env
      "skip"              -- a run failed and it is NOT a runner-missing
                             problem (genuine test failure, timeout, etc.)
      "needs_strip_retry" -- only when allow_strip_retry is True and the
                             first failing run is a runner-missing error

    The caller decides what to do with each status. When "skip" is
    returned, findings and infra_errors are populated with the reason.
    When "needs_strip_retry" is returned, findings and infra_errors are
    empty (the caller should strip the env and call again with
    allow_strip_retry=False).
    """
    suffix = ""
    if not allow_strip_retry:
        suffix = " (after env retry)"

    for run_num in range(1, 4):
        try:
            result = subprocess.run(
                baseline_cmd,
                env=run_env,
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=timeout,
                check=False,
                cwd=repo_root,
            )
        except FileNotFoundError as exc:
            if (
                allow_strip_retry
                and "VIRTUAL_ENV" in run_env
                and _is_runner_missing(baseline_cmd, None, exc)
            ):
                return ("needs_strip_retry", [], [])
            desc = "run %d: runner not found%s" % (run_num, suffix)
            finding = StateFinding(
                id="MUTATION_SKIPPED",
                fingerprint="mutation-flaky",
                source="MUTANT",
                disposition=Disposition.DISMISSED,
                file="",
                line_range=[],
                description=desc,
            )
            infra = "flaky guard: runner not found on run %d%s" % (
                run_num, suffix,
            )
            return ("skip", [finding], [infra])
        except subprocess.TimeoutExpired:
            desc = "baseline tests timed out (flaky guard)%s" % suffix
            finding = StateFinding(
                id="MUTATION_SKIPPED",
                fingerprint="mutation-baseline-timeout",
                source="MUTANT",
                disposition=Disposition.DISMISSED,
                file="",
                line_range=[],
                description=desc,
            )
            infra = (
                "flaky guard: baseline timeout on run %d%s"
                % (run_num, suffix)
            )
            return ("skip", [finding], [infra])
        else:
            if result.returncode != 0:
                if (
                    allow_strip_retry
                    and "VIRTUAL_ENV" in run_env
                    and _is_runner_missing(baseline_cmd, result, None)
                ):
                    return ("needs_strip_retry", [], [])
                desc = (
                    "run %d: tests flaky, mutation unreliable "
                    "(3x baseline check%s)" % (
                        run_num,
                        ", after env retry" if suffix else "",
                    )
                )
                finding = StateFinding(
                    id="MUTATION_SKIPPED",
                    fingerprint="mutation-flaky",
                    source="MUTANT",
                    disposition=Disposition.DISMISSED,
                    file="",
                    line_range=[],
                    description=desc,
                )
                infra = (
                    "flaky guard: baseline failed on run %d%s"
                    % (run_num, suffix)
                )
                return ("skip", [finding], [infra])

    return ("passed", [], [])


def run_mutation(
    diff_files: list[str],
    baseline_cmd: list[str],
    timeout: int = 600,
    cwd: Path | None = None,
    baseline_timeout: int = 120,
    also_copy: list[str] | None = None,
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

    Implementation note:
        mutmut 3.x requires cwd to be the project root. A temporary
        setup.cfg is written with source_paths pointing at the
        diff-scoped files. The mutants/ directory and temporary setup.cfg
        are cleaned up after each run.

    Returns:
        (findings, infra_errors) where findings contains:
        - CONFIRMED MUTANT findings for survivors
        - DISMISSED MUTATION_SKIPPED findings for skip conditions
    """
    findings: list[StateFinding] = []
    infra_errors: list[str] = []

    if cwd is None:
        cwd = Path.cwd()
    repo_root = str(cwd.resolve())

    # Empty files: no work
    if not diff_files:
        return ([], [])

    # Filter to .py files only (Python MVP)
    py_files = [f for f in diff_files if f.endswith(".py")]
    if not py_files:
        findings.append(
            StateFinding(
                id="MUTATION_SKIPPED",
                fingerprint="mutation-no-python",
                source="MUTANT",
                disposition=Disposition.DISMISSED,
                file="",
                line_range=[],
                description="no Python files in the diff (mutation is Python-only MVP)",
            )
        )
        infra_errors.append("no Python files in the diff")
        return (findings, infra_errors)

    roots = _source_roots(py_files)
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
        return (findings, [])

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

    status, guard_findings, guard_infra = _run_baseline_guard(
        baseline_cmd, run_env, repo_root, allow_strip_retry=True, timeout=baseline_timeout
    )
    if status == "needs_strip_retry":
        run_env = _strip_venv_from_env(run_env)
        run_env["PYTHONPATH"] = pythonpath
        status, guard_findings, guard_infra = _run_baseline_guard(
            baseline_cmd, run_env, repo_root, allow_strip_retry=False, timeout=baseline_timeout
        )
    if status == "skip":
        return (guard_findings, guard_infra)

    # Resolve the mutmut invocation from the baseline environment. mutmut
    # must share the interpreter with the project test deps (it drives
    # pytest.main in-process); a foreign PATH mutmut only produces
    # collection errors, so its absence is a clean skip, not an error.
    try:
        invocation = _resolve_mutmut_invocation(baseline_cmd)
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
                    "mutmut import probe timed out in baseline "
                    "test env; not the same as mutmut missing"
                ),
            )
        )
        return (findings, [])
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
        return (findings, [])

    # Refuse to overwrite user's existing setup.cfg or [tool.mutmut] config.
    setup_cfg_path = os.path.join(repo_root, "setup.cfg")
    pyproject_path = os.path.join(repo_root, "pyproject.toml")
    wrote_setup_cfg = False
    try:
        has_user_setup_cfg = False
        if os.path.exists(setup_cfg_path):
            with open(setup_cfg_path, encoding="utf-8") as _fh:
                has_user_setup_cfg = _CODE_FORGE_CFG_MARKER not in _fh.read()
        has_user_pyproject_mutmut = False
        if os.path.exists(pyproject_path):
            try:
                with open(pyproject_path, encoding="utf-8") as fh:
                    raw = fh.read()
                has_user_pyproject_mutmut = "[tool.mutmut]" in raw
            except OSError:
                pass

        if has_user_setup_cfg or has_user_pyproject_mutmut:
            infra_errors.append(
                "mutmut config conflict: project already has [mutmut] or "
                "[tool.mutmut] config; forge cannot override it safely"
            )
            findings.append(
                StateFinding(
                    id="MUTATION_SKIPPED",
                    fingerprint="mutation-config-conflict",
                    source="MUTANT",
                    disposition=Disposition.DISMISSED,
                    file="",
                    line_range=[],
                    description=(
                        "mutmut config conflict: existing setup.cfg or "
                        "pyproject.toml [tool.mutmut] detected"
                    ),
                )
            )
            return (findings, infra_errors)

        # Write temporary setup.cfg to the project root.
        # mutmut renamed the key in 3.4: source_paths replaced paths_to_mutate.
        # 3.3 does not recognise the new name and falls back to guessing the
        # source tree, which silently widens the run past the diff scope, so
        # pyproject pins >=3.4. Paths stay relative to the project root.
        config_content = _build_mutmut_config(py_files, baseline_cmd, also_copy)

        # Write to a temp file first, then rename for atomicity
        fd, tmp_cfg = tempfile.mkstemp(
            prefix=".code-forge-mutation-cfg-",
            dir=repo_root,
            suffix=".cfg",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(config_content)
            os.rename(tmp_cfg, setup_cfg_path)
            wrote_setup_cfg = True
        except OSError:
            try:
                os.unlink(tmp_cfg)
            except OSError:
                pass
            raise

        # mutmut 3.x rewrites sys.path itself (inserts mutants/src, then
        # chdir into mutants/ before pytest.main). Pointing PYTHONPATH at
        # mutants/src here races the directory that does not exist yet and
        # breaks collection. Inherit the baseline env; mutmut drops the
        # original src entry after it has built the mirror.
        try:
            result = subprocess.run(
                invocation + ["run"],
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=timeout,
                check=False,
                env=run_env,
                cwd=repo_root,
            )

            # Any non-zero exit is an error (attempt 2 bug: only caught ==2)
            if result.returncode != 0:
                findings.append(
                    StateFinding(
                        id="MUTATION_ERROR",
                        fingerprint="mutation-invocation-error",
                        source="MUTANT",
                        disposition=Disposition.CONFIRMED,
                        file="",
                        line_range=[],
                        description=(
                            "mutmut run failed (exit %d): %s"
                            % (result.returncode, result.stderr[:200])
                        ),
                    )
                )
                infra_errors.append(
                    "mutmut error (exit %d): %s"
                    % (result.returncode, result.stderr[:100])
                )
                return (findings, infra_errors)

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
            return (findings, [])

        # Parse results from repo_root
        try:
            results_proc = subprocess.run(
                invocation + ["results"],
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=10,
                check=False,
                cwd=repo_root,
                env=run_env,
            )
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
            return (findings, [])

        # Convert survivors to findings
        for survivor in survivors:
            findings.append(
                StateFinding(
                    id="mutant-%s" % survivor.mutant_name,
                    fingerprint="mutant:%s" % survivor.mutant_name,
                    source="MUTANT",
                    disposition=Disposition.CONFIRMED,
                    file=survivor.file,
                    line_range=[0, 0],  # mutmut 3.x results omit line numbers
                    description=(
                        "mutant survived: %s" % survivor.mutant_name
                    ),
                )
            )

    finally:
        # Clean up temporary setup.cfg and mutants/ directory
        if wrote_setup_cfg:
            try:
                os.unlink(setup_cfg_path)
            except OSError:
                pass
        mutants_dir = os.path.join(repo_root, "mutants")
        shutil.rmtree(mutants_dir, ignore_errors=True)

    return (findings, infra_errors)

def launch_detached_mutation(
    diff_files: list[str],
    baseline_cmd: list[str],
    cwd: "Path",
    result_path: "Path",
    baseline_timeout: int = 120,
) -> int | None:
    """Launch the mutation run in a detached process group, returning its PID."""
    import subprocess
    import sys
    import json
    import os
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
    }
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
sys.path.insert(0, {repr(forge_src)})
try:
    from code_forge.mutation import run_mutation
    from code_forge.disposition import Disposition
except ImportError:
    # Installed package layout: the cwd itself may be the package root
    import os as _os
    _os.chdir(str(Path({repr(str(cwd))})))
    sys.path.insert(0, str(Path({repr(str(cwd))})))
    from code_forge.mutation import run_mutation
    from code_forge.disposition import Disposition

result_path = Path({repr(str(result_path))})
cwd_ref = Path({repr(str(cwd))})
diff_files = {repr(diff_files)}
baseline_cmd = {repr(baseline_cmd)}

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
    mm_findings, _infra = run_mutation(
        diff_files=diff_files,
        baseline_cmd=baseline_cmd,
        cwd=cwd_ref,
        baseline_timeout=int({baseline_timeout}),
    )
    survivor_list = [
        f.id
        for f in mm_findings
        if f.source == "MUTANT"
        and f.disposition == Disposition.CONFIRMED
        and f.id != "MUTATION_ERROR"
    ]
    data["status"] = "done"
    data["survivors"] = survivor_list
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
except Exception as e:
    data["status"] = "error"
    data["error"] = str(e)
    try:
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass
"""
    try:
        p = subprocess.Popen(
            [sys.executable, "-c", script],
            start_new_session=True,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=cwd,
        )
    except Exception:  # noqa: BLE001
        return None

    return p.pid
