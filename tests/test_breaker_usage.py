"""Per-invocation observations at breaker stop, not run-wide usage or billing."""

from unittest.mock import Mock

import pytest

import code_forge.llm_invoke as invoke
from code_forge.backend import BackendConfig


def metadata(inp, out, cached):
    return {
        "prompt_tokens": inp,
        "completion_tokens": out,
        "prompt_tokens_details": {"cached_tokens": cached},
    }


@pytest.fixture
def stop(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("unexpected external I/O")

    monkeypatch.setattr("socket.socket", forbidden)
    monkeypatch.setattr("subprocess.Popen", forbidden)
    monkeypatch.setattr(invoke.time, "sleep", forbidden)
    monkeypatch.setenv("BREAKER_USAGE_KEY", "synthetic")
    backend = BackendConfig(
        name="synthetic",
        type="api",
        model="synthetic",
        format="openai",
        api_key_env="BREAKER_USAGE_KEY",
        max_tokens=100,
        output_token_limit=200,
    )

    def execute(mode, usage):
        breaker = invoke.TruncationBreaker({"entry": 1, "second": 2, "between": 3, "before": 1}[mode])
        raised = []
        for name in ("record_truncation", "check_tripped"):
            original = getattr(breaker, name)

            def capture(original=original):
                try:
                    return original()
                except invoke.TruncationBreakerError as error:
                    raised.append(error)
                    raise

            monkeypatch.setattr(breaker, name, capture)

        if mode == "before":
            with pytest.raises(invoke.TruncationBreakerError):
                breaker.record_truncation()

        def dispatch(*args):
            if calls.call_count == 1 or mode == "second":
                raise invoke._TruncatedResponse(
                    "synthetic cut",
                    content='{"verdict":',
                    usage_data=usage,
                    resolved_cap=100,
                    kind="truncated",
                    retryable=False,
                )
            # Model another worker tripping the shared breaker; its usage is
            # deliberately unavailable to this invocation and must not be inferred.
            breaker.record_truncation()
            with pytest.raises(invoke.TruncationBreakerError):
                breaker.record_truncation()
            return "invalid", usage

        calls = Mock(side_effect=dispatch)
        monkeypatch.setattr(invoke, "_invoke_openai", calls)
        with pytest.raises(invoke.TruncationBreakerError) as caught:
            invoke._invoke_api(
                "original prompt",
                backend,
                10,
                max_attempts=5,
                expected_keys=frozenset({"verdict", "reasoning"}),
                continuation_breaker=breaker,
            )
        error = caught.value
        assert error is raised[-1]
        assert error.kind == "truncated" and error.retryable is False
        assert "truncations" in str(error)
        assert all(c.args[1].max_tokens == 100 for c in calls.call_args_list)
        return error, calls, breaker

    return execute


@pytest.mark.parametrize("mode", ["entry", "second", "between"])
@pytest.mark.parametrize(
    ("usage", "counts", "known"),
    [
        (metadata(10, 5, 2), (10, 5, 2), set(invoke._USAGE_FIELDS)),
        (metadata(10, None, 2), (10, 0, 2), {"input_tokens", "cached_input_tokens"}),
        ({}, (0, 0, 0), set()),
        (metadata(0, 0, 0), (0, 0, 0), set(invoke._USAGE_FIELDS)),
        (metadata(10, 5, None), (10, 5, 0), {"input_tokens", "output_tokens"}),
    ],
)
def test_breaker_retains_only_this_invocations_observations(stop, mode, usage, counts, known):
    error, calls, breaker = stop(mode, usage)
    attempts = 1 if mode == "entry" else 2
    assert calls.call_count == attempts
    assert breaker.count == {"entry": 1, "second": 2, "between": 3}[mode]
    observed = error.observed_usage
    assert (observed.input_tokens, observed.output_tokens, observed.cached_input_tokens) == tuple(
        value * attempts for value in counts
    )
    assert observed.known_fields == frozenset(known)
    if {"input_tokens", "output_tokens"} <= known:
        assert error.usage is observed
    else:
        assert error.usage is None


def test_already_tripped_before_dispatch_does_not_invent_usage(stop):
    error, calls, breaker = stop("before", metadata(10, 5, 2))
    assert calls.call_count == 0 and breaker.count == 1
    assert error.usage is None and error.observed_usage is None
