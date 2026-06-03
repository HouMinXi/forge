"""LLM invocation dispatcher (cli subprocess + api HTTP).

Dispatches by BackendConfig.type:
  - cli: subprocess (claude or custom binary)
  - api: HTTP call (openai or anthropic format)

FORGE_LLM_MODEL env var overrides default model for cli backends.

Public types: Usage, LLMResult, LLMInvokeError
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Any, Optional

from .backend import BackendConfig, DEFAULT_BACKEND


@dataclass(frozen=True)
class Usage:
    """Token usage from LLM response."""

    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class LLMResult:
    """LLM invocation result with cost metadata."""

    content: Any
    usage: Usage = Usage()
    duration_s: float = 0.0


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

# Module-level active process tracker for signal handler cleanup. Per D-03.
_active_proc: Optional[subprocess.Popen] = None


def _resolve_model() -> str:
    return os.environ.get("FORGE_LLM_MODEL", DEFAULT_MODEL)


def _strip_fences(text: str) -> str:
    """Strip markdown fences from LLM response."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill process group with SIGTERM escalation. Per D-04."""
    import signal as _signal
    try:
        os.killpg(proc.pid, _signal.SIGTERM)
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, _signal.SIGKILL)
        proc.wait()
    except ProcessLookupError:
        pass  # already dead


def llm_invoke(
    prompt: str,
    backend: Optional[BackendConfig] = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> Any:
    """Invoke LLM via backend (cli subprocess or api HTTP).

    Args:
        prompt: LLM prompt text
        backend: Backend config (defaults to DEFAULT_BACKEND)
        timeout_s: Timeout in seconds

    Raises:
        LLMInvokeError: on timeout, nonzero exit, HTTP error, or JSON parse failure
    """
    if backend is None:
        backend = DEFAULT_BACKEND

    if backend.type == "cli":
        return _invoke_cli(prompt, backend, timeout_s)
    elif backend.type == "api":
        return _invoke_api(prompt, backend, timeout_s)
    else:
        raise LLMInvokeError(
            "unsupported backend type: %r" % backend.type
        )


def _invoke_cli(
    prompt: str,
    backend: BackendConfig,
    timeout_s: int,
) -> Any:
    """Invoke LLM via cli subprocess."""
    # Resolve binary: use backend.command if set, else default to "claude"
    binary_name = backend.command or "claude"
    binary = shutil.which(binary_name)
    if binary is None:
        raise LLMInvokeError("%s binary not found on PATH" % binary_name)

    # Resolve model: use backend.model if set, else fallback to FORGE_LLM_MODEL
    effective_model = backend.model or _resolve_model()

    # Large prompt handling: write to temp file for prompts > 1MB
    import tempfile as _tf
    _prompt_file = None
    if len(prompt.encode("utf-8")) > 1_000_000:
        fd, _prompt_file = _tf.mkstemp(suffix=".txt", prefix="forge-llm-")
        os.write(fd, prompt.encode("utf-8"))
        os.close(fd)
        cmd = [
            "sh", "-c",
            "%s -p \"$(<%s)\" --model %s --output-format json"
            % (shlex.quote(binary), shlex.quote(_prompt_file), shlex.quote(effective_model)),
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
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,  # Unix: creates new session (setsid) per D-02
        )
    except OSError as exc:
        duration = time.monotonic() - start
        if _prompt_file and os.path.exists(_prompt_file):
            os.unlink(_prompt_file)
        raise LLMInvokeError(
            "LLM subprocess failed: %s" % exc,
            exit_code=-1, stderr=str(exc), duration_s=duration,
        ) from exc

    global _active_proc
    _active_proc = proc
    try:
        try:
            stdout_data, stderr_data = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired as exc:
            _kill_tree(proc)
            duration = time.monotonic() - start
            raise LLMInvokeError(
                "LLM subprocess timed out after %ds" % timeout_s,
                exit_code=-1, stderr=str(exc), duration_s=duration,
            ) from exc
    finally:
        _active_proc = None
        if _prompt_file and os.path.exists(_prompt_file):
            os.unlink(_prompt_file)

    duration = time.monotonic() - start

    if proc.returncode != 0:
        raise LLMInvokeError(
            "LLM subprocess exited with code %d" % proc.returncode,
            exit_code=proc.returncode,
            stderr=stderr_data, duration_s=duration,
        )

    stdout = _strip_fences(stdout_data)

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


def _invoke_api(
    prompt: str,
    backend: BackendConfig,
    timeout_s: int,
) -> Any:
    """Invoke LLM via HTTP API (openai or anthropic format)."""
    # Look up API key from environment
    if not backend.api_key_env:
        raise LLMInvokeError(
            "backend %r: api_key_env not configured" % backend.name
        )
    api_key = os.environ.get(backend.api_key_env, "")
    if not api_key:
        raise LLMInvokeError(
            "API key env var %r is not set" % backend.api_key_env
        )

    start = time.monotonic()

    if backend.format == "openai":
        content = _invoke_openai(prompt, backend, api_key, timeout_s)
    elif backend.format == "anthropic":
        content = _invoke_anthropic(prompt, backend, api_key, timeout_s)
    else:
        raise LLMInvokeError(
            "unsupported api format: %r" % backend.format
        )

    duration = time.monotonic() - start

    # Strip fences and parse JSON
    content = _strip_fences(content)
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMInvokeError(
            "API response content is not valid JSON",
            exit_code=0,
            stderr="JSONDecodeError: %s\ncontent[:500]: %s"
            % (exc, content[:500]),
            duration_s=duration,
        ) from exc


def _invoke_openai(
    prompt: str,
    backend: BackendConfig,
    api_key: str,
    timeout_s: int,
) -> str:
    """OpenAI-format API call."""
    url = backend.base_url + "/chat/completions"
    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
    }
    body = {
        "model": backend.model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }

    try:
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"), headers=headers
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as response:
            resp_data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body_excerpt = exc.read().decode("utf-8", errors="replace")[:200]
        raise LLMInvokeError(
            "HTTP %d from %s backend: %s" % (exc.code, backend.format, body_excerpt),
            exit_code=exc.code,
        ) from exc
    except urllib.error.URLError as exc:
        raise LLMInvokeError(
            "URLError from %s backend: %s" % (backend.format, exc.reason)
        ) from exc

    # Extract content from OpenAI response structure
    try:
        return resp_data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMInvokeError(
            "unexpected response structure from %s backend" % backend.format
        ) from exc


def _invoke_anthropic(
    prompt: str,
    backend: BackendConfig,
    api_key: str,
    timeout_s: int,
) -> str:
    """Anthropic-format API call."""
    url = backend.base_url + "/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    body = {
        "model": backend.model,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
    }

    try:
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"), headers=headers
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as response:
            resp_data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body_excerpt = exc.read().decode("utf-8", errors="replace")[:200]
        raise LLMInvokeError(
            "HTTP %d from %s backend: %s" % (exc.code, backend.format, body_excerpt),
            exit_code=exc.code,
        ) from exc
    except urllib.error.URLError as exc:
        raise LLMInvokeError(
            "URLError from %s backend: %s" % (backend.format, exc.reason)
        ) from exc

    # Extract content from Anthropic response structure
    try:
        return resp_data["content"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMInvokeError(
            "unexpected response structure from %s backend" % backend.format
        ) from exc
