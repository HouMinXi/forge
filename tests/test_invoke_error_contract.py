"""Keep failure metadata usable by retry and reporting callers."""

import pytest

from code_forge.llm_invoke import (
    FalsifyProtocolError,
    LLMInvokeError,
    TruncationBreaker,
    TruncationBreakerError,
)


def test_unspecified_diagnostics_do_not_invent_output_or_elapsed_time():
    error = LLMInvokeError("connection failed")

    assert error.stderr == ""
    assert error.duration_s == 0.0
    assert error.kind == ""
    assert error.retryable is True
    assert error.exit_code == -1
    assert error.is_timeout is False
    assert error.retry_after is None
    assert str(error) == "connection failed"


def test_protocol_failure_preserves_payload_and_disables_retries():
    payload = {"verdict": "not-a-verdict"}
    error = FalsifyProtocolError("invalid verdict", raw=payload)

    assert isinstance(error, LLMInvokeError)
    assert error.kind == "protocol"
    assert error.retryable is False
    assert error.raw is payload
    assert str(error) == "invalid verdict"


def test_default_breaker_trips_on_fifth_event_and_stays_tripped():
    breaker = TruncationBreaker()
    assert breaker.count == 0
    assert breaker.tripped is False
    breaker.check_tripped()

    for count in range(1, 5):
        breaker.record_truncation()
        assert breaker.count == count
        assert breaker.tripped is False
        breaker.check_tripped()

    with pytest.raises(TruncationBreakerError) as caught:
        breaker.record_truncation()
    assert caught.value.kind == "truncated"
    assert caught.value.retryable is False
    assert breaker.count == 5
    assert breaker.tripped is True
    with pytest.raises(TruncationBreakerError):
        breaker.check_tripped()
