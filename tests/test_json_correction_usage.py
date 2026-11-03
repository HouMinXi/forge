"""Observed token accounting for the one JSON-correction attempt; no provider calls."""

import pytest

import code_forge.llm_invoke as invoke
from code_forge.backend import BackendConfig


BROKEN = '{"findings":[{"ok":true},,{"ok":false}]}'


def metadata(inp=10, out=5, cached=2):
    return {
        "prompt_tokens": inp,
        "completion_tokens": out,
        "prompt_tokens_details": {"cached_tokens": cached},
        "_forge_finish_reason": "stop",
    }


@pytest.fixture
def dispatch(monkeypatch):
    import socket
    import subprocess

    def forbidden(*args, **kwargs):
        pytest.fail("synthetic accounting control attempted external I/O")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(invoke.time, "sleep", forbidden)
    monkeypatch.setenv("CORRECTION_TEST_KEY", "synthetic-not-secret")
    backend = BackendConfig(
        name="synthetic",
        type="api",
        model="synthetic",
        format="openai",
        api_key_env="CORRECTION_TEST_KEY",
    )

    def run(responses, *, attempts=5):
        calls = []
        sequence = iter(responses)

        def acquire(prompt, *args, **kwargs):
            calls.append(prompt)
            value = next(sequence)
            if isinstance(value, BaseException):
                raise value
            return value

        monkeypatch.setattr(invoke, "_invoke_openai", acquire)
        try:
            result = invoke._invoke_api("synthetic prompt", backend, 10, max_attempts=attempts)
        except invoke.LLMInvokeError as error:
            result = error
        return result, calls

    return run


def counts(usage):
    return usage.input_tokens, usage.output_tokens, usage.cached_input_tokens


def test_failed_json_correction_retains_both_observed_usages(dispatch):
    result, calls = dispatch([(BROKEN, metadata()), (BROKEN, metadata(7, 3, 1))])
    assert len(calls) == 2 and calls[0] != calls[1]
    assert isinstance(result, invoke.InvalidJSONResponseError)
    assert result.kind == "no_json" and result.retryable is False
    assert result.raw_response == BROKEN and isinstance(result.__cause__, ValueError)
    assert counts(result.observed_usage) == (17, 8, 3)
    assert counts(result.usage) == (17, 8, 3)
    assert result.observed_usage.known_fields == frozenset(invoke._USAGE_FIELDS)


@pytest.mark.parametrize("unknown_first", [False, True])
def test_partial_correction_usage_stays_unknown_per_field(dispatch, unknown_first):
    first, second = metadata(), metadata(7, None, 1)
    if unknown_first:
        first, second = metadata(10, None, 2), metadata(7, 3, 1)
    result, calls = dispatch([(BROKEN, first), (BROKEN, second)])
    assert len(calls) == 2 and result.retryable is False
    assert counts(result.observed_usage) == (17, 3 if unknown_first else 5, 3)
    assert result.observed_usage.known_fields == frozenset({"input_tokens", "cached_input_tokens"})
    assert result.usage is None


def test_correction_transport_failure_preserves_identity_and_known_lower_bound(dispatch):
    error = invoke.LLMInvokeError("synthetic timeout", kind="conn", is_timeout=True)
    result, calls = dispatch([(BROKEN, metadata()), error])
    assert result is error and len(calls) == 2
    assert result.kind == "conn" and result.is_timeout and result.retryable is False
    assert counts(result.observed_usage) == (10, 5, 2)
    assert result.observed_usage.known_fields == frozenset()
    assert result.usage is None


def test_successful_correction_is_not_double_counted(dispatch):
    result, calls = dispatch([(BROKEN, metadata()), ('{"findings":[]}', metadata(7, 3, 1))])
    assert isinstance(result, invoke.LLMResult) and len(calls) == 2
    assert counts(result.usage) == (17, 8, 3)


@pytest.mark.parametrize("out", [3, None])
def test_truncated_correction_uses_acquired_usage_without_continuing(dispatch, out):
    error = invoke._TruncatedResponse(
        "synthetic truncation",
        content='{"findings":[',
        usage_data=metadata(7, out, 1),
        resolved_cap=100,
        kind="truncated",
        retryable=False,
    )
    cause = ValueError("synthetic underlying cause")
    error.__cause__ = cause
    result, calls = dispatch([(BROKEN, metadata()), error])
    assert result is error and len(calls) == 2 and result.retryable is False
    assert result.content == '{"findings":[' and result.__cause__ is cause
    assert result.kind == "truncated" and result.resolved_cap == 100
    assert counts(result.observed_usage) == (17, 8 if out is not None else 5, 3)
    if out is None:
        assert result.usage is None
        assert "output_tokens" not in result.observed_usage.known_fields
    else:
        assert counts(result.usage) == (17, 8, 3)
        assert result.observed_usage.known_fields == frozenset(invoke._USAGE_FIELDS)


def test_unknown_cache_does_not_discard_complete_primary_counts(dispatch):
    result, calls = dispatch([(BROKEN, metadata()), (BROKEN, metadata(7, 3, None))])
    assert len(calls) == 2
    assert counts(result.usage) == counts(result.observed_usage) == (17, 8, 2)
    assert result.observed_usage.known_fields == frozenset({"input_tokens", "output_tokens"})


def test_single_failed_attempt_keeps_existing_usage(dispatch):
    result, calls = dispatch([(BROKEN, metadata())], attempts=1)
    assert isinstance(result, invoke.InvalidJSONResponseError) and len(calls) == 1
    assert counts(result.usage) == counts(result.observed_usage) == (10, 5, 2)


def test_reported_zero_is_still_known_after_failed_correction(dispatch):
    result, calls = dispatch([(BROKEN, metadata(0, 0, 0)), (BROKEN, metadata(0, 0, 0))])
    assert len(calls) == 2
    assert counts(result.usage) == counts(result.observed_usage) == (0, 0, 0)
    assert result.observed_usage.known_fields == frozenset(invoke._USAGE_FIELDS)
