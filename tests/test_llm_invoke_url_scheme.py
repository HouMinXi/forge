"""Tests for the URL scheme guard and exception chaining in llm_invoke.

The scheme guard fails closed when a configured endpoint URL is not
http/https: a tampered base_url would otherwise turn the LLM call into a
local file read or an unexpected protocol dial. The chaining pins keep
diagnostic causality attached to raised errors.
"""
import json
import os
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from code_forge.llm_invoke import (
    BackendConfig,
    LLMInvokeError,
    _invoke_anthropic,
    _invoke_openai,
    _invoke_vertex,
    _require_http_scheme,
    llm_invoke,
)


def _api_backend(base_url="https://example.com", fmt="openai"):
    return BackendConfig(
        name="test", type="api", model="model", format=fmt,
        base_url=base_url, api_key_env="TEST_KEY",
    )


class TestRequireHttpScheme:
    def test_accepts_http_and_https(self):
        _require_http_scheme("http://localhost:8080/v1")
        _require_http_scheme("https://api.example.com")

    @pytest.mark.parametrize("url", [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "gopher://example.com",
        "//example.com/no-scheme",
        "",
    ])
    def test_rejects_non_http_schemes_fail_closed(self, url):
        with pytest.raises(LLMInvokeError, match="scheme") as excinfo:
            _require_http_scheme(url)
        assert excinfo.value.retryable is False


class TestSchemeGuardAtInvokeSites:
    def test_openai_rejects_file_scheme_before_network(self):
        backend = _api_backend(base_url="file:///etc")
        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("code_forge.llm_invoke.urllib.request.urlopen") as m_open:
            with pytest.raises(LLMInvokeError, match="scheme"):
                _invoke_openai("prompt", backend, "sk-test", 30)
        m_open.assert_not_called()

    def test_anthropic_rejects_file_scheme_before_network(self):
        backend = _api_backend(base_url="file:///etc", fmt="anthropic")
        with patch("code_forge.llm_invoke.urllib.request.urlopen") as m_open:
            with pytest.raises(LLMInvokeError, match="scheme"):
                _invoke_anthropic("prompt", backend, "sk-test", 30)
        m_open.assert_not_called()

    def test_vertex_guard_runs_on_final_url(self):
        backend = BackendConfig(
            name="test", type="api", model="m", format="vertex",
            base_url="https://example.com", api_key_env="TEST_KEY",
            project_id="p", region="global",
        )
        fake_creds = Mock(token="t")
        import google.auth
        with patch.object(google.auth, "default",
                          return_value=(fake_creds, None)), \
             patch("code_forge.llm_invoke._build_vertex_url",
                   return_value="file:///etc/shadow"), \
             patch("code_forge.llm_invoke.urllib.request.urlopen") as m_open:
            with pytest.raises(LLMInvokeError, match="scheme"):
                _invoke_vertex("prompt", backend, 30)
        m_open.assert_not_called()


class TestExceptionChaining:
    def test_budget_exhaustion_suppresses_misleading_chain(self):
        """The spent-budget error must not chain to the last cut-off reply:
        its message already names every attempt and reason (raise-from-None).
        """
        from code_forge.llm_invoke import _TruncatedResponse

        def _truncated():
            return _TruncatedResponse(
                "backend response truncated (finish_reason=length)",
                content='{"partial": ',
                usage_data={"prompt_tokens": 5, "completion_tokens": 5},
                resolved_cap=1024,
            )

        backend = _api_backend()
        side_effect = [
            _truncated(),
            ("plain prose, no json", {"prompt_tokens": 1, "completion_tokens": 1}),
            ("still no json", {"prompt_tokens": 1, "completion_tokens": 1}),
            _truncated(),
        ]
        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("code_forge.llm_invoke._invoke_openai",
                   side_effect=side_effect), \
             patch("time.sleep"):
            with pytest.raises(LLMInvokeError,
                               match="exhausted") as excinfo:
                llm_invoke("p", backend=backend, max_attempts=5)
        assert excinfo.value.__suppress_context__ is True

    @pytest.mark.asyncio
    async def test_sampling_no_json_chains_parse_error(self):
        """The no-json sampling error chains the underlying parse failure so
        the traceback names the real cause (raise-from)."""
        from code_forge.llm_invoke import invoke_sampling
        from mcp.types import CreateMessageResult, TextContent

        session = MagicMock()
        session.create_message = AsyncMock()
        session.create_message.return_value = CreateMessageResult(
            role="assistant",
            content=TextContent(type="text", text="not json at all"),
            model="test-model",
            stopReason="endTurn",
        )
        with pytest.raises(LLMInvokeError,
                           match="no valid JSON") as excinfo:
            await invoke_sampling(session, prompt="p", max_attempts=1)
        assert isinstance(excinfo.value.__cause__, json.JSONDecodeError)
