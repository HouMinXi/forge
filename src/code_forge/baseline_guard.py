"""Baseline guard shared by fix validation and the mutation run.

Both need to know whether the configured test command can start, and
both retry once after dropping a virtualenv that shadows the runner.
"""

import os
import re
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
            p for p in path_val.split(os.pathsep) if p and not p.startswith(venv_path)
        )
    return stripped


def _is_runner_startup_failure(baseline_cmd: list[str], result: subprocess.CompletedProcess) -> bool:
    """Require a standalone missing-module diagnostic without test output."""
    if not _is_runner_missing(baseline_cmd, result, None) or (result.stdout or "").strip():
        return False
    module = re.escape(baseline_cmd[baseline_cmd.index("-m") + 1])
    diagnostic = rf"(?:[^\r\n]+: )?No module named (?:{module}|'{module}')"
    return re.fullmatch(diagnostic, (result.stderr or "").strip()) is not None


def _failed_nodes(output: str) -> list[str]:
    """Pull pytest FAILED node ids out of a baseline run's stdout."""
    nodes = []
    for line in (output or "").split("\n"):
        if line.startswith("FAILED "):
            parts = line.split()
            if len(parts) > 1:
                nodes.append(parts[1])
    return nodes


def _traceback_summary(lines: list[str]) -> str | None:
    """Keep the latest root summary and first nonblank empty-message continuation."""
    summary = None
    margin = None
    has_frame = False
    pending_message = False
    for line in lines:
        header = re.fullmatch(
            r"(\s*)(\+ )?(?:Exception Group )?Traceback \(most recent call last\):", line
        )
        if header:
            margin = header[1] + ("| " if header[2] else "")
            has_frame = False
            pending_message = False
        elif margin is not None:
            if not line.startswith(margin):
                margin = None
                continue
            content = line[len(margin):]
            if pending_message:
                if content.strip():
                    summary += " " + content.strip()
                    margin = None
                continue
            if re.fullmatch(r'  File ".+", line \d+(?:, in .+)?', content):
                has_frame = True
            elif content and not content[0].isspace():
                if has_frame and re.fullmatch(r"[\w.<>]+(?::(?:\s.*)?|)", content):
                    summary = content
                    pending_message = re.fullmatch(r"[\w.<>]+:\s*", content) is not None
                if not pending_message:
                    margin = None
    return summary


def _output_detail(stderr: str | bytes | None, stdout: str | bytes | None) -> str:
    """Prefer diagnostic lines in long output, capped at 200 per stream.

    Short output keeps its existing whitespace normalization. Long output
    selects the first pytest cause, latest root traceback summary, direct
    exception, or diagnostic heading, in that order, then the prefix.
    """
    streams = []
    for label, value in (("stderr", stderr), ("stdout", stdout)):
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="replace")
        normalized = " ".join((value or "").split())
        excerpt = normalized[:200]
        if len(normalized) > 200:
            lines = (value or "").splitlines()
            for priority, pattern in enumerate((
                r"^E\s+(?!\[\s*\d+%\]\s*$)\S",
                r"^(?:[\w.]*(?:Error|Exception)|SystemExit|KeyboardInterrupt):(?:\s|$)",
                r"^(?:ERROR(?:\s|:)|FAILED\s|Traceback \(most recent call last\):)",
            )):
                diagnostic = next((line for line in lines if re.match(pattern, line.strip())), None)
                if priority == 1:
                    diagnostic = _traceback_summary(lines) or diagnostic
                if diagnostic is not None:
                    excerpt = " ".join(diagnostic.split())[:200]
                    break
        if excerpt:
            streams.append(f"{label}: {excerpt}")
    return ("; " + "; ".join(streams)) if streams else ""


def _run_baseline_guard(
    baseline_cmd: list[str],
    run_env: dict[str, str],
    repo_root: str,
    *,
    allow_strip_retry: bool,
    timeout: int = 120,
    run_command=None,
) -> tuple[str, list[StateFinding], list[str]]:
    """Run the baseline three times and report the observed failure cause.

    run_command optionally supplies invocation ownership. The default resolves
    subprocess.run at call time so existing callers and their probes are unchanged.

    Returns (status, findings, infra_errors) where status is one of:
      "passed"            -- all 3 runs succeeded under run_env
      "skip"              -- a run failed, timed out, or its runner is
                             unavailable and an env retry is ineligible
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

    execute = subprocess.run if run_command is None else run_command

    for run_num in range(1, 4):
        try:
            result = execute(
                baseline_cmd,
                env=run_env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
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
            desc = f"run {run_num}: runner could not start{suffix}" + _output_detail(str(exc), None)
            finding = StateFinding(
                id="MUTATION_SKIPPED",
                fingerprint="mutation-flaky",
                source="MUTANT",
                disposition=Disposition.DISMISSED,
                file="",
                line_range=[],
                description=desc,
            )
            return ("skip", [finding], [desc])
        except subprocess.TimeoutExpired as exc:
            desc = f"run {run_num}: baseline tests timed out after {timeout}s{suffix}" + _output_detail(
                exc.stderr, exc.output
            )
            finding = StateFinding(
                id="MUTATION_SKIPPED",
                fingerprint="mutation-baseline-timeout",
                source="MUTANT",
                disposition=Disposition.DISMISSED,
                file="",
                line_range=[],
                description=desc,
            )
            return ("skip", [finding], [desc])
        else:
            if result.returncode != 0:
                runner_missing = _is_runner_missing(baseline_cmd, result, None)
                if (
                    allow_strip_retry
                    and "VIRTUAL_ENV" in run_env
                    and runner_missing
                ):
                    return ("needs_strip_retry", [], [])
                unavailable = _is_runner_startup_failure(baseline_cmd, result)
                reason = "baseline runner unavailable" if unavailable else "baseline failed"
                nodes = _failed_nodes(result.stdout)
                node_text = (": " + ", ".join(nodes[:5])) if nodes else ""
                desc = (
                    f"run {run_num}: {reason}{suffix} (returncode {result.returncode}{node_text})"
                    + _output_detail(result.stderr, result.stdout)
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
                return ("skip", [finding], [desc])

    return ("passed", [], [])
