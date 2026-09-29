"""Keep Vertex response selection and recovery diagnostics stable."""

import io
import json
from unittest.mock import Mock, patch

import pytest

from code_forge.backend import BackendConfig
from code_forge.llm_invoke import LLMInvokeError, _TruncatedResponse, _invoke_vertex


def _invoke(payload):
    pytest.importorskip("google.auth")
    pytest.importorskip("google.auth.transport.requests")
    backend = BackendConfig(
        name="sample",
        type="api",
        model="test-model",
        format="vertex",
        project_id="test-project",
        max_tokens=8,
    )
    credentials = Mock(token="test-token")
    with (
        patch("google.auth.default", return_value=(credentials, "test-project")),
        patch("google.auth.transport.requests.Request"),
        patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(payload).encode())),
    ):
        return _invoke_vertex("prompt", backend, timeout_s=10)


@pytest.mark.parametrize("text_block", [{"text": "partial"}, {"type": "text", "text": "partial"}])
def test_truncation_preserves_first_text_and_recovery_data(text_block):
    with pytest.raises(_TruncatedResponse) as caught:
        _invoke(
            {
                "stop_reason": "max_tokens",
                "content": [
                    {"type": "thinking", "text": "not-output"},
                    None,
                    text_block,
                    {"type": "text", "text": "later"},
                ],
                "usage": {"input_tokens": 12, "output_tokens": 8},
            }
        )
    exc = caught.value
    assert str(exc) == (
        "vertex backend response truncated (stop_reason=max_tokens, "
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


def test_nonlist_content_is_still_classified_as_truncation():
    with pytest.raises(_TruncatedResponse) as caught:
        _invoke({"stop_reason": "max_tokens", "content": 7, "usage": None})
    assert caught.value.content is None
    assert caught.value.usage_data == {}
    assert caught.value.retryable is False


@pytest.mark.parametrize("text_block", [{"text": "first"}, {"type": "text", "text": "first"}])
def test_normal_response_selects_first_text_block(text_block):
    result = _invoke(
        {
            "stop_reason": "end_turn",
            "content": [
                {"type": "thinking", "text": "not-output"},
                text_block,
                {"type": "text", "text": "later"},
            ],
            "usage": {"input_tokens": 12, "output_tokens": 8},
        }
    )
    assert result == ("first", {"input_tokens": 12, "output_tokens": 8})


def test_no_text_preserves_diagnostic_cause_without_retry():
    with pytest.raises(LLMInvokeError) as caught:
        _invoke({"content": [{"type": "thinking", "thinking": "reasoning"}]})
    exc = caught.value
    assert type(exc) is LLMInvokeError
    assert str(exc) == "unexpected response structure from vertex backend"
    assert exc.retryable is False
    assert isinstance(exc.__cause__, KeyError)
    assert exc.__cause__.args == ("no text block in content",)
