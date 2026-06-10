"""Tests for llm_invoke module."""
from __future__ import annotations

import pytest

from code_forge.llm_invoke import _build_openai_request


class TestBuildOpenAIRequest:
    """Tests for _build_openai_request function."""

    def test_returns_dict(self):
        """Verify return type is dict."""
        body = _build_openai_request("hello", "gpt-4", 1024)
        assert isinstance(body, dict)

    def test_model_set(self):
        """Verify model field is set."""
        body = _build_openai_request("hello", "gpt-4", 1024)
        assert body["model"] == "gpt-4"

    def test_messages_structure(self):
        """Verify messages field structure."""
        body = _build_openai_request("hello", "gpt-4", 1024)
        assert len(body["messages"]) == 1
        assert body["messages"][0]["role"] == "user"

    def test_basic_fields(self):
        body = _build_openai_request("hello", "gpt-4", 1024)
        assert body["model"] == "gpt-4"
        assert body["max_tokens"] == 1024
