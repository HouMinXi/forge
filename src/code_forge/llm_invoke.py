"""LLM subprocess invocation shim.

Encapsulates `claude -p --model <model> --output-format json` with
timeout handling and structured error reporting.  FORGE_LLM_MODEL
env var overrides the default model (12-factor convention).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from typing import Any, Optional


class LLMInvokeError(Exception):
    def __init__(
        self,
        message: str,
        exit_code: int = -1,
        stderr: str = "",
        duration_s: float = 0.0,
    ):
        super().__init__(message)
        self.exit_code = exit_code
        self.stderr = stderr
        self.duration_s = duration_s


DEFAULT_TIMEOUT_S = 120
DEFAULT_MODEL = "claude-sonnet-4-6"


def _resolve_model() -> str:
    return os.environ.get("FORGE_LLM_MODEL", DEFAULT_MODEL)


def _resolve_claude_binary() -> str:
    binary = shutil.which("claude")
    if binary is None:
        raise LLMInvokeError("claude binary not found on PATH")
    return binary


def llm_invoke(
    prompt: str,
    model: Optional[str] = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> Any:
    """Invoke LLM as a subprocess, return parsed JSON response.

    Raises:
        LLMInvokeError: on timeout, nonzero exit, or JSON parse failure
    """
    effective_model = model or _resolve_model()
    binary = _resolve_claude_binary()

    # DEFECT FIX: claude -p passes prompt as CLI arg, subject to
    # OS ARG_MAX (~2MB Linux).  Large diffs exceed this.
    # claude -p reads /dev/tty, NOT stdin (strace confirmed, see
    # memory reference_aicc_tool), so input= pipe does NOT work.
    # Fix: write prompt to temp file when > 1MB, pass via -p "$(<tmpfile)".
    import tempfile as _tf
    _prompt_file = None
    if len(prompt.encode("utf-8")) > 1_000_000:
        fd, _prompt_file = _tf.mkstemp(suffix=".txt", prefix="forge-llm-")
        os.write(fd, prompt.encode("utf-8"))
        os.close(fd)
        # Shell expansion reads the file as the -p argument value
        cmd = [
            "sh", "-c",
            "%s -p \"$(<'%s')\" --model %s --output-format json"
            % (binary, _prompt_file, effective_model),
        ]
    else:
        cmd = [
            binary,
            "-p", prompt,
            "--model", effective_model,
            "--output-format", "json",
        ]

    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - start
        raise LLMInvokeError(
            "LLM subprocess timed out after %ds" % timeout_s,
            exit_code=-1, stderr=str(exc), duration_s=duration,
        ) from exc
    finally:
        if _prompt_file and os.path.exists(_prompt_file):
            os.unlink(_prompt_file)

    duration = time.monotonic() - start

    if result.returncode != 0:
        raise LLMInvokeError(
            "LLM subprocess exited with code %d" % result.returncode,
            exit_code=result.returncode,
            stderr=result.stderr, duration_s=duration,
        )

    # DEFECT FIX: LLMs frequently wrap JSON in markdown fences
    # (```json ... ```) even when told "JSON only".
    stdout = result.stdout.strip()
    if stdout.startswith("```"):
        lines = stdout.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stdout = "\n".join(lines)

    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise LLMInvokeError(
            "LLM subprocess returned non-JSON stdout",
            exit_code=0,
            stderr="JSONDecodeError: %s\nstdout[:500]: %s"
            % (exc, stdout[:500]),
            duration_s=duration,
        ) from exc
