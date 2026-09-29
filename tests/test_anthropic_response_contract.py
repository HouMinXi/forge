"""Preserve Anthropic partial responses and actionable failure diagnostics."""

import json
from unittest.mock import Mock, patch

import pytest

from code_forge.backend import BackendConfig
from code_forge.llm_invoke import LLMInvokeError, _TruncatedResponse, _invoke_anthropic


def _invoke(payload):
    backend = BackendConfig(
        name="sample",
        type="api",
        model="test-model",
        format="anthropic",
        base_url="https://example.test",
        api_key_env="TEST_KEY",
        max_tokens=8,
    )
    response = Mock()
    response.read.return_value = json.dumps(payload).encode("utf-8")
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    with patch("urllib.request.urlopen", return_value=response):
        return _invoke_anthropic("prompt", backend, api_key="test-key", timeout_s=10)


@pytest.mark.parametrize("text_block", [{"text": "partial"}, {"type": "text", "text": "partial"}])
def test_truncation_selects_first_text_block_and_preserves_recovery_data(text_block):
    payload = {
        "stop_reason": "max_tokens",
        "content": [
            {"type": "thinking", "thinking": "reasoning", "text": "not-output"},
            None,
            text_block,
            {"type": "text", "text": "later"},
        ],
        "usage": {"input_tokens": 12, "output_tokens": 8},
    }
    with pytest.raises(_TruncatedResponse) as caught:
        _invoke(payload)
    exc = caught.value
    assert str(exc) == (
        "sample backend response truncated (stop_reason=max_tokens, "
        "input=12 output=8). Review output truncated: output "
        "capacity (8 tokens) insufficient for this diff. Raise "
        "output_ceiling on this backend in gate.yaml or use a "
        "higher-output model."
    )
    assert exc.content == "partial"
    assert exc.usage_data == {"input_tokens": 12, "output_tokens": 8}
    assert exc.resolved_cap == 8
    assert exc.kind == "truncated"
    assert exc.retryable is False


def test_no_text_block_preserves_the_diagnostic_cause_and_disables_retry():
    with pytest.raises(LLMInvokeError) as caught:
        _invoke({"content": [{"type": "thinking", "thinking": "reasoning"}]})
    exc = caught.value
    assert type(exc) is LLMInvokeError
    assert str(exc) == "unexpected response structure from sample backend"
    assert exc.retryable is False
    assert isinstance(exc.__cause__, KeyError)
    assert exc.__cause__.args == ("no text block in content",)
