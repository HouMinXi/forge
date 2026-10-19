"""Baseline guard shared by fix validation and the mutation run.

Both need to know whether the configured test command can start, and
both retry once after dropping a virtualenv that shadows the runner.
"""

import glob
import os
import re
import shutil
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
            if _session_ran(result.stdout or ""):
                # #126: the runner started; the text came from a test's own
                # output (e.g. a child interpreter), not a missing runner.
                return False
            combined = (result.stdout or "") + (result.stderr or "")
            if (f"No module named '{runner_module}'") in combined:
                return True
            if (f"No module named {runner_module}") in combined:
                return True

    return False


_SESSION_EVIDENCE = re.compile(
    r"^(?:FAILED|ERROR) \S+"
    r"|\b\d+ (?:passed|failed|errors?|skipped|xfailed|xpassed|deselected)\b"
    r"|^collected \d+ items?\b"
    r"|^=+ test session starts =+$",
    re.MULTILINE,
)


def _session_ran(stdout: str) -> bool:
    """True when stdout shows a pytest session got going (node results, a
    summary count, a collected count or the session banner)."""
    return _SESSION_EVIDENCE.search(stdout) is not None


def _venv_has_runner(baseline_cmd: list[str], run_env: dict[str, str]) -> bool:
    """True when the inherited virtualenv itself provides the runner (#244).

    Output text alone (``No module named pytest``) or a FileNotFoundError
    raised somewhere in an owned launch is not proof that the venv lacks the
    runner. Stripping a venv that does have it hands the baseline to the
    system interpreter, whose own "No module named pytest" then hides the
    real cause and leaves mutation unmeasured. Checked on disk, so no extra
    process is started: the configured interpreter must resolve into
    VIRTUAL_ENV and the runner module (or binary) must be installed there.
    """
    venv = run_env.get("VIRTUAL_ENV")
    if not venv or not baseline_cmd or not os.path.isdir(venv):
        return False
    venv_real = os.path.realpath(venv)
    runner = baseline_cmd[0]
    resolved = runner if os.sep in runner else shutil.which(runner, path=run_env.get("PATH"))
    if resolved is None:
        return False
    resolved_dir = os.path.dirname(os.path.abspath(resolved))
    if os.path.commonpath([os.path.realpath(resolved_dir), venv_real]) != venv_real:
        return False
    if os.path.basename(runner).startswith("python") and "-m" in baseline_cmd:
        index = baseline_cmd.index("-m")
        if index + 1 >= len(baseline_cmd):
            return False
        top = baseline_cmd[index + 1].split(".", 1)[0]
        site_dirs = glob.glob(os.path.join(venv, "lib", "python*", "site-packages"))
        site_dirs += glob.glob(os.path.join(venv, "Lib", "site-packages"))
        return any(
            os.path.isfile(os.path.join(site, top, "__init__.py"))
            or os.path.isfile(os.path.join(site, top + ".py"))
            for site in site_dirs
        )
    return os.path.isfile(resolved)


def env_retry_record(first_attempt: str | None, inherited_env: dict[str, str]) -> dict[str, str | None]:
    """What an env-strip retry changed, kept whatever the retry's outcome (#246).

    A retry that passes otherwise leaves no trace that the baseline (and the
    measurement after it) ran without the inherited virtualenv, nor of the
    first attempt's cause.
    """
    return {
        "first_attempt": first_attempt,
        "stripped_virtual_env": inherited_env.get("VIRTUAL_ENV"),
    }


def env_retry_note(record: dict[str, str | None]) -> str:
    return (
        "baseline env-strip retry: VIRTUAL_ENV %s and its PATH entry were dropped; first attempt: %s"
        % (record.get("stripped_virtual_env") or "(unset)", record.get("first_attempt") or "(not recorded)")
    )


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


def _without_ignored_exceptions(lines: list[str]) -> list[str]:
    """Drop interpreter "Exception ignored ..." reports (unraisable hooks).

    These are printed for errors in __del__ or at shutdown, for example the
    multiprocessing ResourceTracker's ``ChildProcessError: [Errno 10]`` under
    a subreaper. The run's exit status never depends on them, so they must not
    be reported as the baseline failure cause. A report is its header, an
    optional traceback (header plus indented frames), and the final
    exception line, which is dropped with or without a traceback (#185).
    A message spanning several lines continues until a line that looks
    like real output (_REAL_OUTPUT); those continuation lines are dropped
    too (#182 P3).
    """
    kept: list[str] = []
    state = None  # None, "report" (header/traceback), "message" (after the exception line)
    for line in lines:
        if re.match(r"Exception ignored\b", line):
            state = "report"
            continue
        if state == "report":
            if line.startswith("Traceback (most recent call last):") or (line and line[0].isspace()):
                continue
            state = "message"
            continue  # the report's final exception line
        if state == "message" and not _ends_ignored_message(line):
            continue  # a multi-line message's continuation (#182 P3)
        state = None
        kept.append(line)
    return kept


# A line that can't continue an ignored exception's message: real output
# resumes. Python prints nothing after the message, so this is a heuristic;
# it errs towards keeping anything shaped like a cause.
_REAL_OUTPUT = re.compile(
    r"\s*$"  # blank line
    r"|\s"  # indented (pytest/traceback body)
    r"|Traceback \(most recent call last\):"
    r"|[\w./\\:-]+: "  # "<prog or path or ExcType>: message"
    r"|No module named\b"
    r"|E\s"  # pytest assertion detail
    r"|(?:FAILED|ERROR|PASSED|SKIPPED|XFAIL|XPASS)\b"
    r"|[=_]{3}|=+ "  # pytest section rules and summary
    r"|collected \d|\[gw\d"
)


def _ends_ignored_message(line: str) -> bool:
    return bool(_REAL_OUTPUT.match(line))


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
        # #185: every excerpt (short, fallback, or selected) comes from the
        # stream without interpreter "Exception ignored" reports.
        lines = _without_ignored_exceptions((value or "").splitlines())
        normalized = " ".join(" ".join(lines).split())
        excerpt = normalized[:200]
        if len(normalized) > 200:
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
    retry_cause: list[str] | None = None,
    first_attempt: str | None = None,
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
    allow_strip_retry=False). It is only offered when the inherited env
    does not verifiably have the runner (_venv_has_runner, #244).

    retry_cause, when given, receives the first attempt's observed failure
    whenever "needs_strip_retry" is returned. Pass it back as first_attempt
    on the retry so a retry skip reports the original cause too (#244).
    """
    suffix = ""
    if not allow_strip_retry:
        suffix = " (after env retry)"
    if first_attempt:
        suffix += f" [first attempt: {first_attempt}]"

    def _strip(cause: str):
        if retry_cause is not None:
            retry_cause.append(cause)
        return ("needs_strip_retry", [], [])

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
                and not _venv_has_runner(baseline_cmd, run_env)
            ):
                return _strip(f"run {run_num}: runner could not start" + _output_detail(str(exc), None))
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
                    and not _venv_has_runner(baseline_cmd, run_env)
                ):
                    return _strip(
                        f"run {run_num}: returncode {result.returncode}"
                        + _output_detail(result.stderr, result.stdout)
                    )
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
