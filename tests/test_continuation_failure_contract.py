"""A failed continuation reports why it failed, and does not retry.

_continue_truncated folds every failed attempt into one exhaustion
error. The reason it keeps (last_failure) is the only record of what
went wrong, and nothing else in the suite reads it, so a mutant that
rewrites or drops one of the four reasons used to pass.
"""

import json

import pytest

from code_forge.backend import BackendConfig
from code_forge.llm_invoke import (
    LLMInvokeError,
    _TruncatedResponse,
    _continue_truncated,
)

_PARTIAL = '{"findings": ['
_ENVELOPE = {"findings": [], "code_excerpts": []}


def _backend():
    return BackendConfig(
        name="local", type="api", model="m", format="openai",
        base_url="http://127.0.0.1:9", api_key_env="FORGE_TEST_KEY",
    )


def _truncated(content):
    return _TruncatedResponse(
        "output truncated", content=content, usage_data={}, resolved_cap=100,
    )


@pytest.fixture
def no_wait(monkeypatch):
    monkeypatch.setattr("code_forge.llm_invoke._CONTINUE_DELAY_S", 0)


@pytest.fixture
def stub_openai(monkeypatch):
    """Stub every format branch.

    A mutant that rewrites the format string drops the call out of the
    openai branch into anthropic or vertex. Those must answer the same
    way, or the test waits on a real network call.
    """
    def install(behaviour):
        def fake_keyed(prompt, backend, api_key, timeout_s):
            return behaviour(prompt)

        def fake_vertex(prompt, backend, timeout_s):
            return behaviour(prompt)

        monkeypatch.setattr("code_forge.llm_invoke._invoke_openai", fake_keyed)
        monkeypatch.setattr("code_forge.llm_invoke._invoke_anthropic", fake_keyed)
        monkeypatch.setattr("code_forge.llm_invoke._invoke_vertex", fake_vertex)

    return install


def _recover(stub_openai, behaviour, partial=_PARTIAL):
    stub_openai(behaviour)
    return _continue_truncated(
        "prompt", _backend(), "key", 5, _truncated(partial), None, budget=2,
    )


def test_invalid_combined_json_names_itself(no_wait, stub_openai):
    with pytest.raises(LLMInvokeError) as caught:
        _recover(stub_openai, lambda prompt: ("not json", {}))

    error = caught.value
    assert error.kind == "truncated"
    assert error.retryable is False
    assert "after 2 attempts" in str(error)
    assert str(error).endswith("last failure: combined output is not valid JSON")


def test_non_envelope_json_names_itself(no_wait, stub_openai):
    # The partial is already a whole document and the continuation adds
    # nothing, so the recorded reason is the envelope check rather than
    # a parse error.
    with pytest.raises(LLMInvokeError) as caught:
        _recover(stub_openai, lambda prompt: ("", {}), partial='{"other": 1}')

    assert str(caught.value).endswith("last failure: combined output is not a forge envelope")


def test_invoke_error_is_folded_and_logged(no_wait, stub_openai, caplog):
    def fail(prompt):
        raise LLMInvokeError("gateway down", retryable=True)

    with caplog.at_level("WARNING", logger="code_forge"):
        with pytest.raises(LLMInvokeError) as caught:
            _recover(stub_openai, fail)

    assert str(caught.value).endswith("last failure: gateway down")
    assert not caught.value.__cause__
    assert any(
        "continuation request failed: LLMInvokeError: gateway down" in record.message
        for record in caplog.records
    )


def test_truncated_continuation_names_the_partial(no_wait, stub_openai):
    def fail(prompt):
        raise _TruncatedResponse(
            "output truncated", content="{", usage_data={}, resolved_cap=100,
        )

    with pytest.raises(LLMInvokeError) as caught:
        _recover(stub_openai, fail)

    assert str(caught.value).endswith("last failure: output truncated")


def test_a_complete_partial_is_returned_without_a_continuation(no_wait, stub_openai):
    """A partial that is already the envelope is returned as-is, with no continuation issued."""
    recovered = _recover(
        stub_openai, lambda prompt: pytest.fail("continuation was issued"), partial=json.dumps(_ENVELOPE)
    )
    assert recovered is not None

    assert recovered[0] == _ENVELOPE


def test_breaker_trip_escapes_instead_of_being_folded(no_wait, monkeypatch):
    from code_forge.llm_invoke import TruncationBreaker, TruncationBreakerError

    breaker = TruncationBreaker()

    def trip(prompt, backend, api_key, timeout_s):
        # tripped is derived from the count. Fill it, then one more record
        # raises; the dispatch surfaces that raise.
        while not breaker.tripped:
            breaker.record_truncation()
        breaker.record_truncation()

    monkeypatch.setattr("code_forge.llm_invoke._invoke_openai", trip)

    with pytest.raises(TruncationBreakerError):
        _continue_truncated(
            "prompt", _backend(), "key", 5, _truncated(_PARTIAL), None,
            budget=2, breaker=breaker,
        )


def test_a_spent_continuation_is_an_invoke_error_the_caller_can_fold(no_wait, stub_openai):
    """_invoke_api keeps the exhaustion error only because it is an LLMInvokeError.

    The caller's handler catches that type and re-raises it as the diagnosis.
    A continuation that spends its budget any other way would lose the reason.
    """
    with pytest.raises(LLMInvokeError) as caught:
        _recover(stub_openai, lambda prompt: ("not json", {}))

    assert isinstance(caught.value, LLMInvokeError)
    assert type(caught.value) is LLMInvokeError
    assert "last failure:" in str(caught.value)
