"""LLM invocation utilities for forge review pipeline."""
from __future__ import annotations

import os

import requests


def invoke_llm(
    prompt: str,
    model: str = "gpt-4",
    max_tokens: int = 4096,
    temperature: float = 0.0,
) -> str:
    """Invoke an LLM and return the response text."""
    body = _build_openai_request(prompt, model, max_tokens)
    base_url = os.environ.get("FORGE_LLM_BASE_URL", "http://localhost:0/v1")
    resp = requests.post(
        f"{base_url}/chat/completions",
        json=body,
        timeout=300,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


class Usage:
    """Token usage tracking."""

    def __init__(self, prompt_tokens: int = 0, completion_tokens: int = 0):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def _build_openai_request(
        prompt: str,
        model: str,
        max_tokens: int,
        **kwargs,
) -> dict:
    """Build an OpenAI-compatible chat completion request body.

    Args:
        prompt: the user message to send.
        model: model identifier string.
        max_tokens: maximum tokens in response.

    Returns:
        Dict ready for requests.post(json=...).
    """
    return {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "user", "content": prompt},
        ],
    }
