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
import random
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .backend import BackendConfig, DEFAULT_BACKEND
from .errors import CliError


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
    is_truncated: bool = False


class LLMInvokeError(Exception):
    def __init__(
        self,
        message: str,
        exit_code: int = -1,
        stderr: str = "",
        duration_s: float = 0.0,
        is_timeout: bool = False,
        retryable: bool = True,
        retry_after: float | None = None,
        kind: str = "",
    ):
        super().__init__(message)
        self.exit_code = exit_code
        self.stderr = stderr
        self.duration_s = duration_s
        self.is_timeout = is_timeout
        self.retryable = retryable
        self.retry_after = retry_after
        # Machine-readable failure class for dispatch decisions.
        # invoke_sampling and the api dispatch set one of: "truncated",
        # "empty", "stub_model", "no_json". Note "empty" covers a response
        # that carried no usable text, whatever the wording of the message.
        # Matching on kind (not message text) keeps the MCP
        # fallback routing immune to message rewording and to model output
        # that happens to contain a keyword.
        self.kind = kind


DEFAULT_TIMEOUT_S = 1800  # documented fallback (seconds); FORGE_LLM_TIMEOUT_S overrides per call
_CLI_TIMEOUT_CAP_S = 300  # CLI subprocesses cap; only applies when no explicit timeout is set
_API_TIMEOUT_CAP_S = 600  # API backends cap; prevents 30-minute hangs on dead endpoints


def _apply_params(
    body: dict,
    backend: "BackendConfig",
    *,
    outcap_key: str,
    allow_thinking: bool,
    allow_effort,  # False | True | "output_config"
    default_temperature: float = -1.0,
    field_selects_key: bool = False,
) -> int:
    """Apply typed config fields and generic params to a request body.

    Returns the resolved output cap actually sent to the API (for use in
    truncation diagnostics).

    default_temperature: format-specific fallback when backend.temperature
    is -1 (sentinel = not configured).  openai callers pass 0.0 for
    backward compat; anthropic/vertex pass -1.0 (omit).

    field_selects_key: when True (openai), the populated field chooses the
    wire key (max_completion_tokens field set -> "max_completion_tokens" key;
    only max_tokens field set -> "max_tokens" key).  When False
    (anthropic/vertex), the outcap_key pin is used unconditionally.
    backend.outcap_key always wins as an explicit override in either mode.
    """
    # Output cap: exactly one key, never both.
    # Priority: backend.outcap_key > field-derived (openai) > outcap_key pin
    cap = backend.max_completion_tokens or backend.max_tokens
    if backend.output_ceiling > 0:
        cap = backend.output_ceiling
    if backend.outcap_key:
        resolved_key = backend.outcap_key
    elif field_selects_key and backend.max_completion_tokens > 0:
        resolved_key = "max_completion_tokens"
    elif field_selects_key:
        resolved_key = "max_tokens"
    else:
        resolved_key = outcap_key
    body[resolved_key] = cap

    # Thinking block
    if allow_thinking and backend.thinking_type:
        th = {"type": backend.thinking_type}
        if backend.thinking_budget > 0:
            th["budget_tokens"] = backend.thinking_budget
        body["thinking"] = th

    # Reasoning effort
    if allow_effort and backend.reasoning_effort:
        if allow_effort == "output_config":
            body.setdefault("output_config", {})["effort"] = (
                backend.reasoning_effort
            )
        else:
            body["reasoning_effort"] = backend.reasoning_effort

    # Temperature: configured > format default > omit
    effective_temp = (
        backend.temperature if backend.temperature >= 0
        else default_temperature
    )
    if effective_temp >= 0:
        body["temperature"] = effective_temp

    # Stream flag, always sent explicitly.  A server is free to pick its own
    # default when the field is absent, and OmniRoute picks SSE: omitting
    # stream on a non-streaming backend gets a "data: {...}" body that
    # _parse_response_body cannot parse, reported as a non-JSON response.
    body["stream"] = bool(backend.stream)

    # Generic params passthrough (protected keys blocked at parse time)
    for k, v in (backend.params or {}).items():
        body[k] = v

    return cap


def _read_with_deadline(response, deadline, backend_name):
    """Read response body, enforcing a total-wall deadline.

    urllib's timeout only bounds per-socket reads.  A server that drips
    bytes at intervals shorter than timeout_s never triggers the socket
    timeout, so total wall time is unbounded.  This helper reads the
    body in a daemon thread and joins with a timeout; if the join
    expires, the socket is shut down to interrupt the blocking recv(),
    and a timeout error is raised.
    """
    import socket as _socket

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise LLMInvokeError(
            "%s backend exceeded total read deadline" % backend_name,
            is_timeout=True,
            retryable=False,
        )
    result = [None]
    error = [None]

    def _worker():
        try:
            result[0] = response.read()
        except Exception as exc:
            error[0] = exc

    # Capture the raw socket before starting the read.  shutdown()
    # wakes the blocked recv immediately without freeing the fd number
    # (no fd-reuse race, no double-close).
    sock = None
    try:
        sock = response.fp.raw._sock
    except Exception:
        pass

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=remaining)
    if t.is_alive():
        # Shutdown the socket to interrupt the blocking recv() in the
        # worker thread.  shutdown() is a direct syscall that does NOT
        # touch the BufferedReader lock (so response.close()-blocks
        # does not apply).  It wakes recv immediately without freeing
        # the fd number -- no fd-reuse race, no double-close.
        if sock is not None:
            try:
                sock.shutdown(_socket.SHUT_RDWR)
            except OSError:
                pass
        # Suppress EBADF from response.close() in the caller's
        # with-statement __exit__.
        try:
            response.close()
        except OSError:
            pass
        raise LLMInvokeError(
            "%s backend exceeded total read deadline" % backend_name,
            is_timeout=True,
            retryable=False,
        )
    if error[0] is not None:
        raise error[0]
    return result[0]


def _read_sse(response, deadline=None, backend_name="") -> dict:
    """Read OpenAI SSE stream, assemble into a single response dict.

    Drops reasoning_content (thinking output) -- forge review needs the
    final verdict, not the chain of thought.  Error-only chunks (no
    choices key) are returned as-is for _check_body_error.
    """
    content_parts: list[str] = []
    model = ""
    finish_reason = ""
    usage: dict = {}
    last_error: dict | None = None

    for raw_line in response:
        if deadline is not None and time.monotonic() > deadline:
            raise LLMInvokeError(
                "%s backend exceeded total read deadline" % backend_name,
                is_timeout=True,
                retryable=False,
            )
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line or not line.startswith("data: "):
            continue
        payload = line[6:]
        if payload == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue

        # Error-only chunk (no choices key)
        if "error" in chunk and "choices" not in chunk:
            last_error = chunk
            continue

        if not model:
            model = chunk.get("model", "")
        if chunk.get("usage"):
            usage = chunk["usage"]
        for choice in chunk.get("choices", []):
            delta = choice.get("delta", {})
            if delta.get("content"):
                content_parts.append(delta["content"])
            # reasoning_content intentionally dropped
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]

    # If only errors were received, return for _check_body_error
    if last_error and not content_parts:
        return last_error

    return {
        "model": model,
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "".join(content_parts),
            },
            "finish_reason": finish_reason,
        }],
        "usage": usage,
    }


def _default_timeout_s() -> int:
    """Resolve the LLM-invocation timeout in seconds, honoring FORGE_LLM_TIMEOUT_S.

    Resolved per call (not frozen at import) so the override takes effect even
    when the env var is set after import (test fixtures, embedding code). A
    missing, non-integer, or non-positive value falls back to DEFAULT_TIMEOUT_S.
    """
    raw = os.environ.get("FORGE_LLM_TIMEOUT_S")
    if raw is None:
        return DEFAULT_TIMEOUT_S
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_S
    return value if value > 0 else DEFAULT_TIMEOUT_S


def effective_invoke_timeout_s(
    backend: BackendConfig,
    timeout_s: Optional[int] = None,
) -> int:
    """Return the timeout that llm_invoke would use for this backend.

    Extracts the timeout-priority chain so both llm_invoke() and the MCP
    job watchdog derive the same value from one shared helper.

    Priority:
      1. backend.timeout_s > 0
      2. caller-supplied timeout_s > 0
      3. FORGE_LLM_TIMEOUT_S env var
      4. DEFAULT_TIMEOUT_S (1800s)
      5. type-based cap: _CLI_TIMEOUT_CAP_S (300) / _API_TIMEOUT_CAP_S (600)
         when timeout came from #3 or #4

    timeout_s=0 (or negative) is treated as "not configured" and falls
    through to the next priority level, same as None.
    """
    caller_explicit = timeout_s is not None and timeout_s > 0
    be_timeout = backend.timeout_s if backend.timeout_s is not None else 0
    if be_timeout > 0:
        resolved = be_timeout
    elif caller_explicit:
        resolved = timeout_s  # type: ignore[assignment]
    else:
        resolved = _default_timeout_s()

    if not caller_explicit and be_timeout <= 0:
        cap = (_CLI_TIMEOUT_CAP_S if backend.type == "cli"
               else _API_TIMEOUT_CAP_S)
        if resolved > cap:
            resolved = cap
    return resolved


# DEFAULT_MODEL is kept for backward-compat (external importers). It is no longer
# used as the fallback inside _resolve_model(); omitting --model when unset lets the
# session default model run instead of pinning a specific model.
DEFAULT_MODEL = "claude-sonnet-4-6"

# Default envelope keys accepted by _extract_json_from_text when the caller does not
# specify expected_keys. Covers all review-pass callers:
#   factories.py:272   L1 passes            findings / code_excerpts
#   cli.py:586         _spawn L1 passes     findings / code_excerpts
#   cli.py:678         test-assertion pass  findings / code_excerpts
#   runtime.py:320     RUNTIME axis         surfaces / findings
# NOT covered by this default: falsify_real.py (verdict/reasoning) -- that caller
# MUST pass expected_keys=frozenset({"verdict", "reasoning"}) explicitly.
# Adding a new caller that uses different keys: pass expected_keys, then update this
# comment so the next reviewer sees the full map without re-deriving it.
_REVIEW_ENVELOPE_KEYS: frozenset[str] = frozenset({"findings", "code_excerpts", "surfaces"})

# Provider-specific error code classification.
# Keys: provider name (BackendConfig.name substring match).
# Values: dict of body error code (str) -> "retryable" | "non-retryable".
# Codes not in the map default to retryable (safe: retry unknown, fail-closed
# after exhaustion).
PROVIDER_ERROR_CODES: dict[str, dict[str, str]] = {
    "zhipu": {
        "1113": "non-retryable",  # insufficient balance
        "1302": "retryable",      # rate limit
        "1305": "retryable",      # service overloaded
        "1308": "non-retryable",  # usage limit per time unit
        "1309": "non-retryable",  # coding plan expired
        "1000": "non-retryable",  # auth failed
        "1001": "non-retryable",  # auth param missing
    },
    "minimax": {
        "1002": "retryable",      # rate limit
        "1008": "non-retryable",  # insufficient balance
        "1039": "non-retryable",  # token limit exceeded
        "1041": "retryable",      # connection limit
        "2045": "retryable",      # rate growth limit
        "2049": "non-retryable",  # invalid API key
        "2056": "non-retryable",  # usage limit exhausted
    },
}

# HTTP status codes classified as retryable.
RETRYABLE_HTTP_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})

# Cap computed backoff so extreme configs (initial_delay_s=30, max_attempts=10)
# cannot produce multi-hour sleeps.  Retry-After from the provider is applied
# as a floor AFTER the cap, so a provider asking for >60s is still honored.
MAX_BACKOFF_S: float = 60.0


def _parse_retry_after(headers: Any) -> float | None:
    """Parse Retry-After header as seconds. Cap at 120s."""
    raw = headers.get("Retry-After") if hasattr(headers, "get") else None
    if raw is None:
        return None
    try:
        value = float(raw)
    except (ValueError, TypeError):
        return None
    if value <= 0:
        return None
    return min(value, 120.0)


def _is_body_code_retryable(provider_name: str, code_str: str) -> bool:
    """Look up body error code retryability. Default True for unknown."""
    for key, codes in PROVIDER_ERROR_CODES.items():
        if key in provider_name:
            disposition = codes.get(code_str)
            if disposition is not None:
                return disposition == "retryable"
            return True  # unknown code for known provider
    return True  # unknown provider


def _suggestion(provider_name: str, code_str: str) -> str:
    """Return actionable suggestion for a provider error code."""
    # Balance/payment codes
    if code_str in ("1113", "1008"):
        if "zhipu" in provider_name:
            return "Top up at open.bigmodel.cn"
        if "minimax" in provider_name:
            return "Top up at platform.minimaxi.com"
    if code_str in ("1302", "1002", "1305", "1041", "2045"):
        return "Retry after a short wait or reduce request rate"
    if code_str in ("1000", "1001", "2049"):
        return "Check API key configuration"
    if code_str in ("1039",):
        return "Reduce prompt size or increase token limit"
    if code_str in ("1308", "1309", "2056"):
        return "Check usage limits on provider dashboard"
    return "Check provider status page"


def _format_error_message(
    provider_name: str, http_code: int, body_excerpt: str,
) -> str:
    """Format error message."""
    if http_code == 402:
        problem = "payment required"
        tip = "Top up account balance"
    elif http_code == 403:
        problem = "forbidden"
        tip = "Check API key permissions"
    elif http_code == 401:
        problem = "unauthorized"
        tip = "Check API key configuration"
    elif http_code == 429:
        problem = "rate limited"
        tip = "Retry after a short wait"
    elif http_code >= 500:
        problem = "server error"
        tip = "Retry or check provider status page"
    else:
        problem = "HTTP error"
        tip = "Check provider documentation"
    return "code-forge: %s backend: %s (%d). %s" % (
        provider_name, problem, http_code, tip,
    )


def _check_body_error(resp_data: dict, backend: "BackendConfig") -> None:
    """Detect provider-specific errors in response body (HTTP 200 with error).

    Zhipu: {"error": {"code": "1302", "message": "..."}}
    MiniMax (openai format): {"base_resp": {"status_code": 1008, "status_msg": "..."}}
    """
    # Zhipu: error.code (string)
    error_obj = resp_data.get("error")
    if isinstance(error_obj, dict) and error_obj.get("code") is not None:
        code_str = str(error_obj["code"])
        msg = error_obj.get("message", "")
        retryable = _is_body_code_retryable(backend.name, code_str)
        raise LLMInvokeError(
            "code-forge: %s backend: %s (code %s). %s"
            % (backend.name, msg, code_str, _suggestion(backend.name, code_str)),
            exit_code=0,
            retryable=retryable,
        )

    # MiniMax openai format: base_resp.status_code (int)
    base_resp = resp_data.get("base_resp")
    if isinstance(base_resp, dict):
        status_code = base_resp.get("status_code", 0)
        if status_code != 0:
            code_str = str(status_code)
            msg = base_resp.get("status_msg", "")
            retryable = _is_body_code_retryable(backend.name, code_str)
            raise LLMInvokeError(
                "code-forge: %s backend: %s (code %s). %s"
                % (backend.name, msg, code_str, _suggestion(backend.name, code_str)),
                exit_code=0,
                retryable=retryable,
            )
    return None


# Module-level active process tracker for signal handler cleanup.
_active_proc: Optional[subprocess.Popen] = None

# Signal handler state.
_original_sigint: Any = None
_original_sigterm: Any = None
_handlers_installed = False


def _resolve_model() -> str:
    return os.environ.get("FORGE_LLM_MODEL", "")


def _strip_fences(text: str) -> str:
    """Strip markdown fences from LLM response.

    Stops at the FIRST closing fence so that trailing explanatory text
    appended by some models (e.g. mimo-pro) is not included in the
    extracted content.  The old implementation only removed the last
    line if it was exactly "```", which failed when extra prose followed
    the closing fence.
    """
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        content_lines = []
        for line in lines[1:]:  # skip opening ```[lang] line
            if line.strip() == "```":
                break  # stop at first closing fence; ignore trailing text
            content_lines.append(line)
        text = "\n".join(content_lines).strip()
    return text


def _extract_json_from_text(
    text: str,
    expected_keys: frozenset[str] | None = None,
) -> dict | None:
    """Find and return the first valid forge-envelope JSON object in text.

    Module-private helper called only from _invoke_api() when _strip_fences
    + json.loads fails. Not part of the public API.

    expected_keys: the set of top-level keys the caller expects in its
    response envelope. Only dicts whose key set overlaps expected_keys are
    accepted, preventing stray JSON fragments (arrays, unrelated dicts) that
    appear in model prose before the real envelope (F1 fix). None uses
    _REVIEW_ENVELOPE_KEYS (the default for all review-pass callers).

    Callers and their expected_keys (maintain this list when adding new axes):
      factories.py:272   L1 passes            findings / code_excerpts  (default)
      cli.py:586         _spawn L1 passes     findings / code_excerpts  (default)
      cli.py:678         test-assertion pass  findings / code_excerpts  (default)
      runtime.py:320     RUNTIME axis         surfaces / findings       (default)
      falsify_real.py:44 falsify              verdict / reasoning       (explicit)
      daemon_state.py Q1:   external_state                              (explicit)
      daemon_state.py Q2Q3: conflicts                                   (explicit)

    Scans left-to-right for '{' only; all forge envelopes are dicts, never
    bare arrays.  No attempt cap: raw_decode fails in O(1) for invalid JSON
    (exits at the first non-JSON character), so the total scan remains
    O(n) amortised across all '{' positions even when many invalid braces
    precede the real envelope (F2 fix).

    Returns None if no valid envelope can be extracted.
    """
    keys = expected_keys if expected_keys is not None else _REVIEW_ENVELOPE_KEYS
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.keys() & keys:
            return obj
    return None


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill process group with SIGTERM escalation."""
    import signal as _signal
    try:
        os.killpg(proc.pid, _signal.SIGTERM)
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, _signal.SIGKILL)
        proc.wait()
    except ProcessLookupError:
        pass  # already dead


def _install_signal_handlers() -> None:
    """Install chained signal handlers for subprocess cleanup.

    Copies lock.py chain pattern: save previous handler, call it after cleanup.
    Idempotent: does nothing if handlers already installed.
    """
    import signal as _signal

    global _original_sigint, _original_sigterm, _handlers_installed
    if _handlers_installed:
        return

    def _make_chained_handler(prev: Any):
        def _handler(signum: int, frame: Any) -> None:
            global _active_proc
            if _active_proc is not None:
                try:
                    _kill_tree(_active_proc)
                except Exception:  # noqa: BLE001
                    pass
            if callable(prev):
                prev(signum, frame)
                return
            if prev == _signal.SIG_IGN:
                return
            raise KeyboardInterrupt
        return _handler

    _original_sigint = _signal.getsignal(_signal.SIGINT)
    _original_sigterm = _signal.getsignal(_signal.SIGTERM)
    _signal.signal(_signal.SIGINT, _make_chained_handler(_original_sigint))
    _signal.signal(_signal.SIGTERM, _make_chained_handler(_original_sigterm))
    _handlers_installed = True


# Install at module load time so cleanup is always active.
_install_signal_handlers()


def llm_invoke(
    prompt: str,
    backend: Optional[BackendConfig] = None,
    timeout_s: Optional[int] = None,
    expected_keys: frozenset[str] | None = None,
    max_attempts: int = 5,
    initial_delay_s: float = 2.0,
) -> LLMResult:
    """Invoke LLM via backend (cli subprocess or api HTTP).

    Args:
        prompt: LLM prompt text
        backend: Backend config (defaults to DEFAULT_BACKEND)
        timeout_s: Timeout in seconds. None (default) or a non-positive value
            resolves FORGE_LLM_TIMEOUT_S at call time, falling back to
            DEFAULT_TIMEOUT_S.
        expected_keys: Top-level keys expected in the JSON response envelope.
            Used by _invoke_api's fallback JSON extractor when the model
            prepends prose before the JSON (e.g. mimo-pro). None uses
            _REVIEW_ENVELOPE_KEYS (correct for all review-pass callers).
            Falsify callers must pass frozenset({"verdict", "reasoning"}).
            Ignored for cli backends (those do not use _extract_json_from_text).
        max_attempts: Maximum retry attempts for API backends (default 5).
        initial_delay_s: Initial backoff delay in seconds (default 2.0).

    Returns:
        LLMResult with content, usage (tokens), and duration_s

    Raises:
        LLMInvokeError: on timeout, nonzero exit, HTTP error, or JSON parse failure
    """
    if backend is None:
        backend = DEFAULT_BACKEND
    timeout_s = effective_invoke_timeout_s(backend, timeout_s)

    if backend.type == "cli":
        return _invoke_cli(prompt, backend, timeout_s)
    elif backend.type == "api":
        return _invoke_api(
            prompt, backend, timeout_s, expected_keys=expected_keys,
            max_attempts=max_attempts, initial_delay_s=initial_delay_s,
        )
    else:
        raise LLMInvokeError(
            "unsupported backend type: %r" % backend.type
        )


def _invoke_cli(
    prompt: str,
    backend: BackendConfig,
    timeout_s: int,
) -> LLMResult:
    """Invoke LLM via cli subprocess. Returns LLMResult with Usage(0,0)"""
    # Resolve binary: use backend.command if set, else default to "claude"
    binary_name = backend.command or "claude"
    binary = shutil.which(binary_name)
    if binary is None:
        raise LLMInvokeError("%s binary not found on PATH" % binary_name)

    # Resolve model: use backend.model if set, else fallback to FORGE_LLM_MODEL
    effective_model = backend.model or _resolve_model()

    # Large prompt handling: write to temp file for prompts > 1MB
    import tempfile as _tf
    prompt_file = None
    if len(prompt.encode("utf-8")) > 1_000_000:
        fd, prompt_file = _tf.mkstemp(suffix=".txt", prefix="forge-llm-")
        os.write(fd, prompt.encode("utf-8"))
        os.close(fd)
        model_part = " --model %s" % shlex.quote(effective_model) if effective_model else ""
        cmd = [
            "sh", "-c",
            "%s -p \"$(<%s)\"%s --output-format json"
            % (shlex.quote(binary), shlex.quote(prompt_file), model_part),
        ]
    else:
        cmd = [binary, "-p", prompt]
        if effective_model:
            cmd.extend(["--model", effective_model])
        cmd.extend(["--output-format", "json"])

    # Build child env: unset then set, or None to inherit parent env
    if backend.env_unset or backend.env_set:
        child_env = dict(os.environ)
        for k in backend.env_unset:
            child_env.pop(k, None)
        child_env.update(dict(backend.env_set))
    else:
        child_env = None

    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            start_new_session=True,  # Unix: creates new session (setsid)
            env=child_env,
        )
    except OSError as exc:
        duration = time.monotonic() - start
        if prompt_file:
            try:
                os.unlink(prompt_file)
            except OSError:  # cleanup best-effort; do not mask Popen error
                pass
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
                is_timeout=True,
            ) from exc
    finally:
        _active_proc = None
        if prompt_file:
            try:
                os.unlink(prompt_file)
            except OSError:  # cleanup best-effort; do not mask in-flight error
                pass

    duration = time.monotonic() - start

    if proc.returncode != 0:
        raise LLMInvokeError(
            "LLM subprocess exited with code %d" % proc.returncode,
            exit_code=proc.returncode,
            stderr=stderr_data, duration_s=duration,
        )

    stdout = _strip_fences(stdout_data)

    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError as exc:
        diag = "JSONDecodeError: %s\nstdout[:500]: %r" % (exc, stdout[:500])
        raise LLMInvokeError(
            "LLM subprocess returned non-JSON stdout -- %s" % diag,
            exit_code=0,
            stderr=diag,
            duration_s=duration,
        ) from exc

    # Current claude CLI versions emit a streaming event array instead of a
    # single envelope dict. Find the last result event and treat it as the
    # envelope so the existing dict-unwrap path below handles it normally.
    if isinstance(parsed, list):
        result_events = [
            e for e in parsed
            if isinstance(e, dict) and e.get("type") == "result"
        ]
        if result_events:
            parsed = result_events[-1]

    # Claude Code CLI -p --output-format json emits a JSON envelope:
    #   {"type": "result", "result": "<model response>", "usage": {...}}
    # When detected, unwrap the inner result and extract token usage.
    # Fall back to treating the full output as content when no envelope.
    usage_data: dict = {}
    content = parsed
    if isinstance(parsed, dict) and parsed.get("type") == "result" and "result" in parsed:
        usage_data = parsed.get("usage") or {}
        raw_result = parsed["result"]
        if isinstance(raw_result, str):
            stripped = _strip_fences(raw_result)
            try:
                content = json.loads(stripped)
            except json.JSONDecodeError:
                content = raw_result
        else:
            content = raw_result

    return LLMResult(
        content=content,
        usage=Usage(
            input_tokens=usage_data.get("input_tokens", 0),
            output_tokens=usage_data.get("output_tokens", 0),
        ),
        duration_s=duration,
    )


def _invoke_api(
    prompt: str,
    backend: BackendConfig,
    timeout_s: int,
    expected_keys: frozenset[str] | None = None,
    max_attempts: int = 5,
    initial_delay_s: float = 2.0,
) -> LLMResult:
    """Invoke LLM via HTTP API (openai or anthropic format). Returns LLMResult."""
    # Look up API key (not needed for vertex which uses OAuth2)
    if backend.format != "vertex":
        if backend.api_key_file:
            try:
                api_key = Path(backend.api_key_file).read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise LLMInvokeError(
                    "backend %r: cannot read api_key_file: %s"
                    % (backend.name, exc)
                ) from exc
            if not api_key:
                raise LLMInvokeError(
                    "backend %r: api_key_file is empty" % backend.name
                )
        elif backend.api_key_env:
            api_key = os.environ.get(backend.api_key_env, "")
            if not api_key:
                raise LLMInvokeError(
                    "API key env var %r is not set" % backend.api_key_env
                )
        else:
            raise LLMInvokeError(
                "backend %r: no api_key_env or api_key_file configured"
                % backend.name
            )
    else:
        api_key = ""

    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1, got %d" % max_attempts)
    if initial_delay_s < 0:
        raise ValueError(
            "initial_delay_s must be non-negative, got %.2f" % initial_delay_s
        )

    start = time.monotonic()

    # Retry loop with exponential backoff + jitter.
    # Inner try catches TimeoutError (socket.timeout alias on Python 3.12+).
    # Outer except catches LLMInvokeError from both the format dispatch and
    # the converted TimeoutError, applying retry logic.
    for attempt in range(max_attempts):
        try:
            try:
                if backend.format == "openai":
                    content, usage_data = _invoke_openai(
                        prompt, backend, api_key, timeout_s,
                    )
                    usage = Usage(
                        input_tokens=usage_data.get("prompt_tokens", 0),
                        output_tokens=usage_data.get("completion_tokens", 0),
                    )
                elif backend.format == "anthropic":
                    content, usage_data = _invoke_anthropic(
                        prompt, backend, api_key, timeout_s,
                    )
                    usage = Usage(
                        input_tokens=usage_data.get("input_tokens", 0),
                        output_tokens=usage_data.get("output_tokens", 0),
                    )
                elif backend.format == "vertex":
                    content, usage_data = _invoke_vertex(
                        prompt, backend, timeout_s,
                    )
                    usage = Usage(
                        input_tokens=usage_data.get("input_tokens", 0),
                        output_tokens=usage_data.get("output_tokens", 0),
                    )
                else:
                    raise LLMInvokeError(
                        "unsupported api format: %r" % backend.format
                    )
                # All three formats can hand back a response whose content
                # field is present but unusable -- null, empty, or not a
                # string at all.  A backend loose enough to send null is
                # loose enough to send a number, and both reach .strip()
                # the same way: deepseek sends null intermittently with
                # finish_reason "stop", and a
                # proxy can do it on the anthropic/vertex block shape.  The
                # three extraction sites converge here, inside the retry
                # loop, so one check covers them and a transient empty
                # response gets retried instead of ending the run.  A capped
                # response never reaches this point: each per-format helper
                # detects its own truncation signal and raises before it
                # returns, so finish_reason=length still surfaces with the
                # advice to raise output_ceiling.  Cost of retrying: a
                # backend that returns null on every call resubmits the
                # whole prompt max_attempts times before failing.  That is
                # the price of recovering the intermittent case, which is
                # the one actually observed.
                if not isinstance(content, str) or not content.strip():
                    raise LLMInvokeError(
                        "%s backend returned no content (format=%s, "
                        "content type=%s). The request succeeded and the "
                        "response carried no text, which is usually "
                        "transient. On the openai shape a refusal or a "
                        "reasoning-only reply arrives the same way; the "
                        "block formats surface those as a missing text "
                        "block instead."
                        % (
                            backend.name, backend.format,
                            type(content).__name__,
                        ),
                        kind="empty",
                    )
            except TimeoutError as exc:
                raise LLMInvokeError(
                    "%s backend timed out after %ds"
                    % (backend.name, timeout_s),
                    stderr=str(exc),
                    duration_s=time.monotonic() - start,
                    is_timeout=True,
                    retryable=False,  # socket timeout is not transient
                ) from exc
        except LLMInvokeError as exc:
            if not exc.retryable or attempt == max_attempts - 1:
                raise
            delay = min(
                initial_delay_s * (2 ** attempt),
                MAX_BACKOFF_S,
            ) + random.uniform(0, 0.5)
            if exc.retry_after is not None:
                delay = max(delay, exc.retry_after)
            # Carry the cause. Without it this line names a backend and a
            # delay and nothing else, so a run that retries forever gives the
            # operator no way to tell a rate limit from a proxy queue drop
            # from an empty response -- the status, the message, and any
            # self-diagnosing text the gateway sent all stop here. One such
            # message ("this is OmniRoute's request queue, not an upstream
            # timeout") had to be recovered from the proxy's container log
            # because this line discarded it.
            #
            # Read the timing carefully when debugging from this output: the
            # delay is printed BEFORE the sleep, so the gap between two lines
            # is the sleep plus the NEXT attempt's duration. The line does not
            # say how long an attempt took.
            cause = " ".join(str(exc).split())[:400]
            sys.stderr.write(
                "code-forge: retrying %s (%d/%d, waiting %.1fs) after %s\n"
                % (backend.name, attempt + 2, max_attempts, delay, cause)
            )
            time.sleep(delay)
            continue
        break  # success

    duration = time.monotonic() - start

    # Strip fences and parse JSON; fall back to embedded-JSON extraction.
    content = _strip_fences(content)
    try:
        parsed_content = json.loads(content)
    except json.JSONDecodeError as exc:
        # Some models (e.g. mimo-pro) prepend prose before the JSON or wrap
        # it in a code fence with trailing text.  Try to locate the first
        # balanced JSON object/array in the raw text as a fallback.
        parsed_content = _extract_json_from_text(content, expected_keys=expected_keys)
        if parsed_content is None:
            diag = "JSONDecodeError: %s\ncontent[:500]: %r" % (
                exc, content[:500],
            )
            raise LLMInvokeError(
                "API response content is not valid JSON -- %s" % diag,
                exit_code=0,
                stderr=diag,
                duration_s=duration,
            ) from exc

    return LLMResult(content=parsed_content, usage=usage, duration_s=duration)


def _parse_response_body(raw: bytes, backend_name: str) -> dict:
    """Parse a raw HTTP 200 response body as JSON.

    Providers can return 200 with a non-JSON body (proxy error page,
    truncated upstream response).  Wrap the parse failure in
    LLMInvokeError so retry/circuit-breaker callers that only catch
    LLMInvokeError see it instead of a raw JSONDecodeError.  Decode
    with errors="replace" (the error-path convention in this file) so
    a non-UTF-8 body becomes the same wrapped parse failure instead of
    an escaping UnicodeDecodeError.
    """
    body_text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(body_text)
    except json.JSONDecodeError as exc:
        raise LLMInvokeError(
            "%s backend returned non-JSON response body: %s"
            % (backend_name, body_text[:200]),
            retryable=True,
        ) from exc


def _invoke_openai(
    prompt: str,
    backend: BackendConfig,
    api_key: str,
    timeout_s: int,
) -> tuple[str, dict]:
    """OpenAI-format API call. Returns (content_str, usage_dict)."""
    url = backend.base_url + "/chat/completions"
    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
    }
    body = {
        "model": backend.model,
        "messages": [{"role": "user", "content": prompt}],
    }
    resolved_cap = _apply_params(
        body, backend,
        outcap_key="max_completion_tokens",
        allow_thinking=True,
        allow_effort=True,
        default_temperature=0.0,
        field_selects_key=True,
    )

    try:
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"), headers=headers
        )
        deadline = time.monotonic() + timeout_s
        with urllib.request.urlopen(req, timeout=timeout_s) as response:
            if backend.stream:
                resp_data = _read_sse(
                    response, deadline=deadline,
                    backend_name=backend.name,
                )
            else:
                raw = _read_with_deadline(
                    response, deadline, backend.name
                )
                resp_data = _parse_response_body(raw, backend.name)
    except urllib.error.HTTPError as exc:
        body_bytes = exc.read()  # read once (second read returns b"")
        body_excerpt = body_bytes.decode("utf-8", errors="replace")[:200]
        retry_after = _parse_retry_after(exc.headers)
        retryable = exc.code in RETRYABLE_HTTP_STATUSES
        raise LLMInvokeError(
            _format_error_message(backend.name, exc.code, body_excerpt),
            exit_code=exc.code,
            retryable=retryable,
            retry_after=retry_after,
        ) from exc
    except urllib.error.URLError as exc:
        raise LLMInvokeError(
            "URLError from %s backend: %s" % (backend.name, exc.reason),
            retryable=True,
        ) from exc
    except TimeoutError:
        raise  # preserve non-retryable timeout handling in retry loop
    except OSError as exc:
        raise LLMInvokeError(
            "connection error from %s backend: %s" % (backend.name, exc),
            retryable=True,
        ) from exc

    # Body-based error detection: Zhipu error.code, MiniMax base_resp
    _check_body_error(resp_data, backend)

    # Extract content and usage from OpenAI response structure
    try:
        choice = resp_data["choices"][0]
        content = choice["message"]["content"]
        usage_data = resp_data.get("usage", {})
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMInvokeError(
            "unexpected response structure from %s backend" % backend.name,
            retryable=False,
        ) from exc

    # Truncation detection: openai format uses finish_reason == "length"
    # when the response hit max_tokens / max_completion_tokens.  Same
    # pattern as the anthropic path (stop_reason == "max_tokens") and
    # the sampling path (stopReason == "maxTokens") -- all three now
    # raise kind="truncated" so _dispatch_sampling routes them uniformly.
    finish = choice.get("finish_reason", "")
    if finish == "length":
        in_tok = usage_data.get("prompt_tokens", "?")
        out_tok = usage_data.get("completion_tokens", "?")
        raise LLMInvokeError(
            "%s backend response truncated (finish_reason=length, "
            "input=%s output=%s). Review output truncated: output "
            "capacity (%d tokens) insufficient for this diff. Raise "
            "output_ceiling on this backend in gate.yaml or use a "
            "higher-output model."
            % (backend.name, in_tok, out_tok, resolved_cap),
            kind="truncated",
            retryable=False,
        )

    return (content, usage_data)


def _invoke_anthropic(
    prompt: str,
    backend: BackendConfig,
    api_key: str,
    timeout_s: int,
) -> tuple[str, dict]:
    """Anthropic-format API call. Returns (content_str, usage_dict)."""
    if backend.stream:
        raise CliError(
            "backend %r: streaming not supported for %s format; "
            "use format: openai" % (backend.name, backend.format)
        )
    url = backend.base_url + "/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    body = {
        "model": backend.model,
        "messages": [{"role": "user", "content": prompt}],
    }
    resolved_cap = _apply_params(
        body, backend,
        outcap_key="max_tokens",
        allow_thinking=True,
        allow_effort=False,
    )

    try:
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"), headers=headers
        )
        deadline = time.monotonic() + timeout_s
        with urllib.request.urlopen(req, timeout=timeout_s) as response:
            raw = _read_with_deadline(response, deadline, backend.name)
            resp_data = _parse_response_body(raw, backend.name)
    except urllib.error.HTTPError as exc:
        body_bytes = exc.read()
        body_excerpt = body_bytes.decode("utf-8", errors="replace")[:200]
        retry_after = _parse_retry_after(exc.headers)
        retryable = exc.code in RETRYABLE_HTTP_STATUSES
        raise LLMInvokeError(
            _format_error_message(backend.name, exc.code, body_excerpt),
            exit_code=exc.code,
            retryable=retryable,
            retry_after=retry_after,
        ) from exc
    except urllib.error.URLError as exc:
        raise LLMInvokeError(
            "URLError from %s backend: %s" % (backend.name, exc.reason),
            retryable=True,
        ) from exc
    except TimeoutError:
        raise  # preserve non-retryable timeout handling in retry loop
    except OSError as exc:
        raise LLMInvokeError(
            "connection error from %s backend: %s" % (backend.name, exc),
            retryable=True,
        ) from exc

    # Extract first text block from Anthropic response.  Some backends
    # (e.g. MiniMax) prepend a thinking block before the text block.
    try:
        blocks = resp_data["content"]
        text_blocks = [b for b in blocks if b.get("type", "text") == "text"]
        if not text_blocks:
            raise KeyError("no text block in content")
        content = text_blocks[0]["text"]
        usage_data = resp_data.get("usage", {})
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMInvokeError(
            "unexpected response structure from %s backend" % backend.name,
            retryable=False,
        ) from exc

    # Truncation detection: anthropic format uses stop_reason == "max_tokens"
    # when the response hit the output cap.  Thinking tokens (if enabled)
    # count against max_tokens, so a large diff + thinking can exhaust the
    # budget before the JSON content is complete.  Detect this before the
    # caller attempts JSON parsing -- a truncated JSON always fails parse,
    # and "not valid JSON" hides the real cause from the user.
    stop = resp_data.get("stop_reason", "")
    if stop == "max_tokens":
        in_tok = usage_data.get("input_tokens", "?")
        out_tok = usage_data.get("output_tokens", "?")
        raise LLMInvokeError(
            "%s backend response truncated (stop_reason=max_tokens, "
            "input=%s output=%s). Review output truncated: output "
            "capacity (%d tokens) insufficient for this diff. Raise "
            "output_ceiling on this backend in gate.yaml or use a "
            "higher-output model."
            % (backend.name, in_tok, out_tok, resolved_cap),
            kind="truncated",
            retryable=False,
        )

    return (content, usage_data)


def _build_vertex_url(project_id: str, region: str = "global", model: str = "") -> str:
    """Build the Vertex AI rawPredict endpoint URL."""
    if region == "global":
        base = "https://aiplatform.googleapis.com"
    elif region in ("us", "eu"):
        base = "https://aiplatform.%s.rep.googleapis.com" % region
    else:
        base = "https://%s-aiplatform.googleapis.com" % region
    return (
        "%s/v1/projects/%s/locations/%s/publishers/anthropic/models/%s:rawPredict"
        % (base, project_id, region, model)
    )


def _invoke_vertex(
    prompt: str,
    backend: BackendConfig,
    timeout_s: int,
) -> tuple[str, dict]:
    """Vertex AI rawPredict API call. Returns (content_str, usage_dict).

    Uses OAuth2 Bearer token (google-auth). Requires code-review-forge[vertex].
    Wire protocol:
      - anthropic_version in body (not header)
      - model in URL (not body)
      - Bearer token auth (not x-api-key)
    """
    if backend.stream:
        raise CliError(
            "backend %r: streaming not supported for %s format; "
            "use format: openai" % (backend.name, backend.format)
        )
    try:
        from google.oauth2 import service_account
        import google.auth
        import google.auth.transport.requests
        from google.auth.exceptions import DefaultCredentialsError, RefreshError
    except ImportError as exc:
        raise LLMInvokeError(
            "Vertex AI format requires google-auth and requests. "
            "Install: pip install code-review-forge[vertex]",
            retryable=False,
        ) from exc

    # Resolve credentials
    try:
        if backend.credentials_path:
            creds = service_account.Credentials.from_service_account_file(
                backend.credentials_path,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
        else:
            creds, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
    except (FileNotFoundError, ValueError) as exc:
        raise LLMInvokeError(
            "Failed to load GCP credentials from %s: %s"
            % (backend.credentials_path, exc),
            retryable=False,
        ) from exc
    except DefaultCredentialsError as exc:
        raise LLMInvokeError(
            "No GCP credentials found. Set GOOGLE_APPLICATION_CREDENTIALS "
            "or use credentials_path in gate.yaml",
            retryable=False,
        ) from exc

    # Refresh token
    try:
        auth_req = google.auth.transport.requests.Request()
        creds.refresh(auth_req)
    except RefreshError as exc:
        raise LLMInvokeError(
            "Failed to refresh GCP credentials: %s" % exc,
            retryable=False,
        ) from exc

    if not backend.project_id:
        raise LLMInvokeError(
            "vertex format requires project_id. Configure a vertex backend "
            "in gate.yaml (see code-forge init).",
            retryable=False,
        )

    url = _build_vertex_url(
        backend.project_id, backend.region or "global", backend.model
    )
    headers = {
        "Authorization": "Bearer " + creds.token,
        "Content-Type": "application/json",
    }
    body = {
        "anthropic_version": "vertex-2023-10-16",
        "messages": [{"role": "user", "content": prompt}],
    }
    resolved_cap = _apply_params(
        body, backend,
        outcap_key="max_tokens",
        allow_thinking=True,
        allow_effort="output_config",
    )

    try:
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"), headers=headers
        )
        deadline = time.monotonic() + timeout_s
        with urllib.request.urlopen(req, timeout=timeout_s) as response:
            raw = _read_with_deadline(response, deadline, backend.name)
            resp_data = _parse_response_body(raw, backend.name)
    except urllib.error.HTTPError as exc:
        body_excerpt = exc.read().decode("utf-8", errors="replace")[:200]
        raise LLMInvokeError(
            "HTTP %d from vertex backend: %s" % (exc.code, body_excerpt),
            exit_code=exc.code,
        ) from exc
    except urllib.error.URLError as exc:
        raise LLMInvokeError(
            "URLError from vertex backend: %s" % exc.reason,
            retryable=True,
        ) from exc
    except TimeoutError:
        raise  # preserve non-retryable timeout handling in retry loop
    except OSError as exc:
        raise LLMInvokeError(
            "connection error from %s backend: %s" % (backend.name, exc),
            retryable=True,
        ) from exc

    try:
        blocks = resp_data["content"]
        text_blocks = [b for b in blocks if b.get("type", "text") == "text"]
        if not text_blocks:
            raise KeyError("no text block in content")
        content = text_blocks[0]["text"]
        usage_data = resp_data.get("usage", {})
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMInvokeError(
            "unexpected response structure from vertex backend",
            retryable=False,
        ) from exc

    # Vertex uses anthropic response format: same stop_reason field.
    stop = resp_data.get("stop_reason", "")
    if stop == "max_tokens":
        in_tok = usage_data.get("input_tokens", "?")
        out_tok = usage_data.get("output_tokens", "?")
        raise LLMInvokeError(
            "vertex backend response truncated (stop_reason=max_tokens, "
            "input=%s output=%s). Review output truncated: output "
            "capacity (%d tokens) insufficient for this diff. Raise "
            "output_ceiling on this backend in gate.yaml or use a "
            "higher-output model."
            % (in_tok, out_tok, resolved_cap),
            kind="truncated",
            retryable=False,
        )

    return (content, usage_data)


async def invoke_sampling(
    session,
    prompt: str,
    system_prompt: str | None = None,
    max_tokens: int = 16384,
    temperature: float = 0.0,
    model_hint: str | None = None,
) -> LLMResult:
    from mcp.types import (
        SamplingMessage, TextContent as MCPTextContent,
        ModelPreferences, ModelHint,
    )
    t0 = time.time()
    kwargs = {
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if system_prompt:
        kwargs["system_prompt"] = system_prompt
    if model_hint:
        kwargs["model_preferences"] = ModelPreferences(
            hints=[ModelHint(name=model_hint)],
            intelligencePriority=0.8,
        )
    messages = [SamplingMessage(
        role="user",
        content=MCPTextContent(type="text", text=prompt),
    )]
    result = await session.create_message(messages, **kwargs)
    elapsed = time.time() - t0

    # content is Union[TextContent, ImageContent, AudioContent]
    if isinstance(result.content, MCPTextContent):
        raw_text = result.content.text
    else:
        raw_text = str(result.content)

    # Empty response: some MCP clients (e.g. Copilot free tier) advertise
    # sampling capability but return empty text.
    result_model = getattr(result, 'model', '') or ''
    if not raw_text.strip():
        raise LLMInvokeError(
            "sampling response is empty (model=%s, stopReason=%s). "
            "The MCP client may not fully implement createMessage. "
            "Set outlet: subprocess in gate.yaml and configure an API backend."
            % (result_model or '?', getattr(result, 'stopReason', '?')),
            duration_s=elapsed,
            kind="empty",
        )

    # copilotcli/auto and similar stub models return syntactically valid
    # but useless responses. Detect early before wasting JSON parse effort.
    if result_model.startswith('copilotcli/'):
        raise LLMInvokeError(
            "sampling model '%s' is a Copilot CLI stub that cannot "
            "generate review content. Upgrade to Copilot Pro or set "
            "outlet: subprocess with an API backend." % result_model,
            duration_s=elapsed,
            kind="stub_model",
        )

    # Only maxTokens is true truncation. stopSequence/toolUse are normal completions.
    # Check truncation BEFORE JSON parse: truncated output is almost always
    # invalid JSON, and kind="truncated" (not "no_json") tells
    # _dispatch_sampling the real failure class.
    if result.stopReason == "maxTokens":
        raise LLMInvokeError(
            "sampling response truncated (stopReason == maxTokens)",
            duration_s=elapsed,
            kind="truncated",
            retryable=False,
        )

    # Parse JSON same as _invoke_api path
    text = _strip_fences(raw_text)
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        parsed = _extract_json_from_text(raw_text)
        if parsed is None:
            raise LLMInvokeError(
                "sampling response contains no valid JSON "
                "(first 120 chars: %r)" % raw_text[:120],
                duration_s=elapsed,
                kind="no_json",
            )

    return LLMResult(
        content=parsed,
        usage=Usage(0, 0),
        duration_s=elapsed,
    )
