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
        is_timeout: bool = False,
        retryable: bool = True,
        retry_after: float | None = None,
    ):
        super().__init__(message)
        self.exit_code = exit_code
        self.stderr = stderr
        self.duration_s = duration_s
        self.is_timeout = is_timeout
        self.retryable = retryable
        self.retry_after = retry_after


DEFAULT_TIMEOUT_S = 120  # documented fallback (seconds); FORGE_LLM_TIMEOUT_S overrides per call


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
    """Format error message per D-31-08."""
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
    if timeout_s is None or timeout_s <= 0:
        timeout_s = _default_timeout_s()

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
    _prompt_file = None
    if len(prompt.encode("utf-8")) > 1_000_000:
        fd, _prompt_file = _tf.mkstemp(suffix=".txt", prefix="forge-llm-")
        os.write(fd, prompt.encode("utf-8"))
        os.close(fd)
        model_part = " --model %s" % shlex.quote(effective_model) if effective_model else ""
        cmd = [
            "sh", "-c",
            "%s -p \"$(<%s)\"%s --output-format json"
            % (shlex.quote(binary), shlex.quote(_prompt_file), model_part),
        ]
    else:
        cmd = [binary, "-p", prompt]
        if effective_model:
            cmd.extend(["--model", effective_model])
        cmd.extend(["--output-format", "json"])

    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,  # Unix: creates new session (setsid)
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
                is_timeout=True,
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
        parsed = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise LLMInvokeError(
            "LLM subprocess returned non-JSON stdout",
            exit_code=0,
            stderr="JSONDecodeError: %s\nstdout[:500]: %s"
            % (exc, stdout[:500]),
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
    # Look up API key from environment (not needed for vertex which uses OAuth2)
    if backend.format != "vertex":
        if not backend.api_key_env:
            raise LLMInvokeError(
                "backend %r: api_key_env not configured" % backend.name
            )
        api_key = os.environ.get(backend.api_key_env, "")
        if not api_key:
            raise LLMInvokeError(
                "API key env var %r is not set" % backend.api_key_env
            )
    else:
        api_key = ""

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
            except TimeoutError as exc:
                raise LLMInvokeError(
                    "%s backend timed out after %ds"
                    % (backend.format, timeout_s),
                    stderr=str(exc),
                    duration_s=time.monotonic() - start,
                    is_timeout=True,
                    retryable=False,  # 120s is sufficient evidence
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
            sys.stderr.write(
                "code-forge: retrying %s (%d/%d, waiting %.1fs)...\n"
                % (backend.name, attempt + 2, max_attempts, delay)
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
            raise LLMInvokeError(
                "API response content is not valid JSON",
                exit_code=0,
                stderr="JSONDecodeError: %s\ncontent[:500]: %s"
                % (exc, content[:500]),
                duration_s=duration,
            ) from exc

    return LLMResult(content=parsed_content, usage=usage, duration_s=duration)


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
        "temperature": 0,
        "max_tokens": backend.max_tokens,
    }

    try:
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"), headers=headers
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as response:
            resp_data = json.loads(response.read().decode("utf-8"))
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
            "URLError from %s backend: %s" % (backend.format, exc.reason),
            retryable=True,
        ) from exc

    # Body-based error detection: Zhipu error.code, MiniMax base_resp
    _check_body_error(resp_data, backend)

    # Extract content and usage from OpenAI response structure
    try:
        content = resp_data["choices"][0]["message"]["content"]
        usage_data = resp_data.get("usage", {})
        return (content, usage_data)
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMInvokeError(
            "unexpected response structure from %s backend" % backend.format
        ) from exc


def _invoke_anthropic(
    prompt: str,
    backend: BackendConfig,
    api_key: str,
    timeout_s: int,
) -> tuple[str, dict]:
    """Anthropic-format API call. Returns (content_str, usage_dict)."""
    url = backend.base_url + "/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    body = {
        "model": backend.model,
        "max_tokens": backend.max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }

    try:
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"), headers=headers
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as response:
            resp_data = json.loads(response.read().decode("utf-8"))
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
            "URLError from %s backend: %s" % (backend.format, exc.reason),
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
        return (content, usage_data)
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMInvokeError(
            "unexpected response structure from %s backend" % backend.format
        ) from exc


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
    try:
        from google.oauth2 import service_account
        import google.auth
        import google.auth.transport.requests
        from google.auth.exceptions import DefaultCredentialsError, RefreshError
    except ImportError as exc:
        raise LLMInvokeError(
            "Vertex AI format requires google-auth and requests. "
            "Install: pip install code-review-forge[vertex]"
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
            % (backend.credentials_path, exc)
        ) from exc
    except DefaultCredentialsError as exc:
        raise LLMInvokeError(
            "No GCP credentials found. Set GOOGLE_APPLICATION_CREDENTIALS "
            "or use credentials_path in gate.yaml"
        ) from exc

    # Refresh token
    try:
        auth_req = google.auth.transport.requests.Request()
        creds.refresh(auth_req)
    except RefreshError as exc:
        raise LLMInvokeError(
            "Failed to refresh GCP credentials: %s" % exc
        ) from exc

    if not backend.project_id:
        raise LLMInvokeError(
            "vertex format requires project_id. Configure a vertex backend "
            "in gate.yaml (see code-forge init)."
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
        "max_tokens": backend.max_tokens,
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
            "HTTP %d from vertex backend: %s" % (exc.code, body_excerpt),
            exit_code=exc.code,
        ) from exc
    except urllib.error.URLError as exc:
        raise LLMInvokeError(
            "URLError from vertex backend: %s" % exc.reason
        ) from exc

    try:
        blocks = resp_data["content"]
        text_blocks = [b for b in blocks if b.get("type", "text") == "text"]
        if not text_blocks:
            raise KeyError("no text block in content")
        content = text_blocks[0]["text"]
        usage_data = resp_data.get("usage", {})
        return (content, usage_data)
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMInvokeError(
            "unexpected response structure from vertex backend"
        ) from exc
