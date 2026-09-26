"""Baseline guard shared by fix validation and the mutation run.

Both need to know whether the configured test command can start, and
both retry once after dropping a virtualenv that shadows the runner.
"""

import os
import subprocess

from code_forge.disposition import Disposition
from code_forge.state import StateFinding


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
            if (f"No module named '{runner_module}'") in combined:
                return True
            if (f"No module named {runner_module}") in combined:
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
            desc = f"baseline tests timed out (flaky guard){suffix}"
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


