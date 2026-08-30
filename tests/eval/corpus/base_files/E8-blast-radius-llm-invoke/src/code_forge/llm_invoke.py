"""LLM invocation dispatcher (cli subprocess + api HTTP).

Dispatches by BackendConfig.type:
  - cli: subprocess (claude or custom binary)
  - api: HTTP call (openai or anthropic format)

FORGE_LLM_MODEL env var overrides default model for cli backends.

Public types: Usage, LLMResult, LLMInvokeError
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .backend import BackendConfig


@dataclass(frozen=True)
class Usage:
    """Token usage from LLM response."""
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class LLMResult:
    """Result from LLM invocation."""
    content: str = ""
    usage: Usage = Usage()
    duration_s: float = 0.0


class LLMInvokeError(Exception):
    """Raised when LLM invocation fails."""


DEFAULT_TIMEOUT_S = 120


# Placeholder for functions that precede llm_invoke in the real file.
# Lines 50-199 contain _invoke_cli, _invoke_api, _extract_json_from_text,
# signal handlers, etc. Only the llm_invoke function signature matters
# for this eval corpus entry.

def _invoke_cli(prompt, backend, timeout_s):
    pass

def _invoke_api(prompt, backend, timeout_s, expected_keys=None):
    pass

_REVIEW_ENVELOPE_KEYS = frozenset({"findings", "code_excerpts"})

def _extract_json_from_text(text, expected_keys=None, max_attempts=10):
    pass

def _install_signal_handlers():
    pass

def _cleanup_children():
    pass

# Line padding to reach approximately line 200.
# This ensures the diff hunk header @@ -206,6 +206,7 @@ aligns.
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#

# Install at module load time so cleanup is always active.
_install_signal_handlers()


def llm_invoke(
    prompt: str,
    backend: Optional[BackendConfig] = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    expected_keys: frozenset[str] | None = None,
) -> LLMResult:
    """Invoke LLM via backend (cli subprocess or api HTTP).

    Args:
        prompt: LLM prompt text
        backend: Backend config (defaults to DEFAULT_BACKEND)
        timeout_s: Timeout in seconds
        expected_keys: Top-level keys expected in the JSON response envelope.
    """
    pass
