"""Operator-facing and recovery contracts for OpenAI-format responses."""

import json
from unittest.mock import Mock, patch

import pytest

from code_forge.backend import BackendConfig
from code_forge.llm_invoke import LLMInvokeError, _TruncatedResponse, _invoke_openai


def _invoke(payload, *, max_tokens=0):
    backend = BackendConfig(
        name="sample",
        type="api",
        model="test-model",
        format="openai",
        base_url="https://example.test",
        api_key_env="TEST_KEY",
        max_tokens=max_tokens,
    )
    response = Mock()
    response.read.return_value = json.dumps(payload).encode("utf-8")
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    with patch("urllib.request.urlopen", return_value=response):
        return _invoke_openai("prompt", backend, api_key="test-key", timeout_s=10)


def _response(*, finish="length", input_tokens=12, output_tokens=1):
    return {
        "choices": [{"message": {"content": '{"partial":'}, "finish_reason": finish}],
        "usage": {"prompt_tokens": input_tokens, "completion_tokens": output_tokens},
    }


def test_bad_response_is_nonretryable_and_identifies_backend():
    with pytest.raises(LLMInvokeError) as caught:
        _invoke({"choices": []})
    assert type(caught.value) is LLMInvokeError
    assert str(caught.value) == "unexpected response structure from sample backend"
    assert caught.value.retryable is False
    assert isinstance(caught.value.__cause__, IndexError)


def test_abandoned_stream_discards_partial_response_and_can_retry():
    with pytest.raises(LLMInvokeError) as caught:
        _invoke(_response(finish="error"))
    assert type(caught.value) is LLMInvokeError
    assert str(caught.value) == (
        "sample backend stream ended with finish_reason=error; partial response discarded"
    )
    assert caught.value.retryable is True
    assert caught.value.exit_code == 0


def test_backend_hard_cap_carries_partial_and_advises_switching_models():
    with pytest.raises(_TruncatedResponse) as caught:
        _invoke(_response(), max_tokens=8)
    exc = caught.value
    assert str(exc) == (
        "sample backend response truncated at 1 output tokens "
        "(finish_reason=length, input=12). The configured "
        "output cap is 8, so the backend clamped below it "
        "on its own; raising the configured cap will not help "
        "-- use a backend/model with a higher hard output limit."
    )
    assert exc.content == '{"partial":'
    assert exc.usage_data == {"prompt_tokens": 12, "completion_tokens": 1}
    assert exc.resolved_cap == 8
    assert exc.kind == "truncated"
    assert exc.retryable is False


@pytest.mark.parametrize("output_tokens, cap", [(8, 8), (0, 1)])
def test_configured_cap_names_the_knob_and_carries_partial(output_tokens, cap):
    with pytest.raises(_TruncatedResponse) as caught:
        _invoke(_response(output_tokens=output_tokens), max_tokens=cap)
    exc = caught.value
    assert str(exc) == (
        "sample backend response truncated (finish_reason=length, "
        f"input=12 output={output_tokens}). Review output truncated: output "
        f"capacity ({cap} tokens) insufficient for this diff. Raise "
        "output_ceiling on this backend in gate.yaml or use a "
        "higher-output model."
    )
    assert exc.content == '{"partial":'
    assert exc.usage_data == {"prompt_tokens": 12, "completion_tokens": output_tokens}
    assert exc.resolved_cap == cap
    assert exc.kind == "truncated"
    assert exc.retryable is False


def test_unconfigured_cap_tells_operator_to_set_a_limit():
    with pytest.raises(_TruncatedResponse) as caught:
        _invoke(_response(output_tokens=3))
    exc = caught.value
    assert str(exc) == (
        "sample backend response truncated (finish_reason=length, "
        "input=12 output=3). Review output truncated: no usable "
        "output cap is configured for this backend, so its own "
        "limit ended the response. Set max_tokens or "
        "output_ceiling on this backend in gate.yaml."
    )
    assert exc.content == '{"partial":'
    assert exc.usage_data == {"prompt_tokens": 12, "completion_tokens": 3}
    assert exc.resolved_cap == 0
    assert exc.kind == "truncated"
    assert exc.retryable is False


def test_missing_finish_reason_does_not_invent_a_completion_status():
    payload = _response(finish="stop")
    del payload["choices"][0]["finish_reason"]
    content, usage = _invoke(payload)
    assert content == '{"partial":'
    assert usage == {"prompt_tokens": 12, "completion_tokens": 1}


def test_finished_response_preserves_completion_reason_for_json_recovery():
    content, usage = _invoke(_response(finish="stop"))
    assert content == '{"partial":'
    assert usage == {
        "prompt_tokens": 12,
        "completion_tokens": 1,
        "_forge_finish_reason": "stop",
    }
