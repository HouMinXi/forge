"""Synthetic continuation accounting, not live billing or full wire retention."""

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


def cut(inp=10, out=5, cached=2):
    return invoke._TruncatedResponse(
        "synthetic cut",
        content='{"verdict":',
        usage_data=metadata(inp, out, cached),
        resolved_cap=100,
        kind="truncated",
        retryable=False,
    )


GOOD = '"DISMISSED","reasoning":"ok"}'


@pytest.fixture
def run(monkeypatch):
    import socket
    import subprocess

    def forbidden(*args, **kwargs):
        pytest.fail("unexpected external I/O")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setenv("CONTINUATION_TEST_KEY", "synthetic-not-secret")
    slept = Mock()
    monkeypatch.setattr(invoke.time, "sleep", slept)

    def execute(responses, *, wider=False, attempts=1):
        backend = BackendConfig(
            name="synthetic",
            type="api",
            model="synthetic",
            format="openai",
            api_key_env="CONTINUATION_TEST_KEY",
            max_tokens=100,
            output_token_limit=200 if wider else 0,
        )
        dispatch = Mock(side_effect=responses)
        monkeypatch.setattr(invoke, "_invoke_openai", dispatch)
        try:
            result = invoke._invoke_api(
                "original prompt",
                backend,
                10,
                expected_keys=frozenset({"verdict", "reasoning"}),
                max_attempts=attempts,
            )
        except invoke.LLMInvokeError as error:
            result = error
        return result, dispatch, slept

    return execute


def counts(usage):
    return usage.input_tokens, usage.output_tokens, usage.cached_input_tokens


@pytest.mark.parametrize("success", [False, True])
def test_failed_continuation_counts_survive_success_or_exhaustion(run, success):
    result, calls, slept = run(
        [
            cut(),
            ("not JSON", metadata(7, 3, 1)),
            (GOOD if success else "still invalid", metadata(11, 4, 2)),
        ]
    )
    assert calls.call_count == 3
    slept.assert_called_once_with(invoke._CONTINUE_DELAY_S)
    assert counts(result.usage) == (28, 12, 5)
    if success:
        assert result.content == {"verdict": "DISMISSED", "reasoning": "ok"}
        assert result.is_truncated
    else:
        assert result.kind == "truncated" and result.retryable is False
        assert "continuation exhausted after 2 attempts" in str(result)
        assert counts(result.observed_usage) == (28, 12, 5)


@pytest.mark.parametrize("failure", ["unknown", "partial", "truncated"])
def test_failed_continuation_metadata_keeps_unknown_fields(run, failure):
    error = (
        invoke.LLMInvokeError("synthetic transport failure", kind="conn")
        if failure == "unknown"
        else invoke.LLMInvokeError(
            "synthetic partial",
            observed_usage=invoke.Usage(
                7, 0, 1, known_fields=frozenset({"input_tokens", "cached_input_tokens"})
            ),
        )
        if failure == "partial"
        else cut(7, None, 1)
    )
    result, calls, _ = run([cut(), error, ("invalid", metadata(11, 4, 2))])
    assert calls.call_count == 3 and result.retryable is False
    assert result.usage is None
    assert counts(result.observed_usage) == (
        21 if failure == "unknown" else 28,
        9,
        4 if failure == "unknown" else 5,
    )
    expected = (
        frozenset() if failure == "unknown" else frozenset({"input_tokens", "cached_input_tokens"})
    )
    assert result.observed_usage.known_fields == expected


def test_known_zero_continuations_stay_known(run):
    result, calls, _ = run(
        [cut(0, 0, 0), ("invalid", metadata(0, 0, 0)), ("invalid", metadata(0, 0, 0))]
    )
    assert calls.call_count == 3
    assert counts(result.usage) == (0, 0, 0)
    assert result.observed_usage.known_fields == frozenset(invoke._USAGE_FIELDS)


@pytest.mark.parametrize("wider_success", [False, True])
def test_wider_seam_preserves_prior_usage_without_changing_policy(run, wider_success):
    result, calls, _ = run(
        [
            cut(),
            ("invalid", metadata(7, 3, 1)),
            ("invalid", metadata(11, 4, 2)),
            (
                '{"verdict":"DISMISSED","reasoning":"wide"}' if wider_success else "invalid",
                metadata(13, 6, 3),
            ),
        ],
        wider=True,
    )
    assert calls.call_count == 4
    assert calls.call_args_list[-1].args[0] == "original prompt"
    assert calls.call_args_list[-1].args[1].max_tokens == 200
    assert all(call.args[1].max_tokens == 100 for call in calls.call_args_list[:-1])
    if wider_success:
        assert result.content["reasoning"] == "wide"
        assert counts(result.usage) == (41, 18, 8)
    else:
        # The same continuation error now also retains the failed wider attempt.
        assert result.kind == "truncated" and not result.retryable
        assert counts(result.observed_usage) == (41, 18, 8)


@pytest.mark.parametrize(
    ("prior", "wide", "expected", "known"),
    [
        (metadata(0, 0, 0), metadata(0, 0, 0), (0, 0, 0), invoke._USAGE_FIELDS),
        (metadata(10, None, 2), metadata(13, 6, 3), (43, 6, 9), {"input_tokens", "cached_input_tokens"}),
        ({}, metadata(13, 6, 3), (13, 6, 3), set()),
        (metadata(10, 5, 2), {}, (30, 15, 6), set()),
        (metadata(10, 5, None), metadata(13, 6, 3), (43, 21, 3), {"input_tokens", "output_tokens"}),
        (metadata(10, 5, 2), metadata(13, 6, None), (43, 21, 6), {"input_tokens", "output_tokens"}),
    ],
)
def test_wider_success_keeps_unknown_and_zero_contract(run, prior, wide, expected, known):
    initial = cut()
    initial.usage_data = prior
    result, calls, slept = run(
        [
            initial,
            ("invalid", prior),
            ("invalid", prior),
            ('{"verdict":"DISMISSED","reasoning":"wide"}', wide),
        ],
        wider=True,
    )
    assert calls.call_count == 4
    slept.assert_called_once_with(invoke._CONTINUE_DELAY_S)
    assert counts(result.usage) == expected
    assert result.usage.known_fields == frozenset(known)
    assert result.content == {"verdict": "DISMISSED", "reasoning": "wide"}
    assert result.is_truncated
    assert calls.call_args_list[-1].args[0] == "original prompt"
    assert [call.args[1].max_tokens for call in calls.call_args_list] == [100, 100, 100, 200]


def test_json_correction_truncation_does_not_enter_wider_or_double_count(run):
    broken = '{"verdict":"DISMISSED",,"reasoning":"broken"}'
    result, calls, slept = run([(broken, metadata(7, 3, 1)), cut()], wider=True, attempts=2)
    assert calls.call_count == 2
    slept.assert_not_called()
    assert isinstance(result, invoke._TruncatedResponse)
    assert not result.retryable
    assert counts(result.observed_usage) == (17, 8, 3)
    assert all(call.args[1].max_tokens == 100 for call in calls.call_args_list)


@pytest.mark.parametrize(
    "failure",
    ["invalid", "nonstring", "envelope", "error", "truncated", "partial", "unknown", "cached_unknown"],
)
def test_wider_failure_retains_usage_and_original_exhaustion(run, monkeypatch, failure):
    original = invoke._exhaustion_error
    exhausted = []

    def capture(*args):
        error = original(*args)
        exhausted.append(error)
        return error

    monkeypatch.setattr(invoke, "_exhaustion_error", capture)
    responses = {
        "invalid": ("invalid", metadata(13, 6, 3)),
        "nonstring": (None, metadata(13, 6, 3)),
        "envelope": ('{"unrelated": true}', metadata(13, 6, 3)),
        "error": invoke.LLMInvokeError("synthetic", observed_usage=invoke.Usage(13, 6, 3)),
        "truncated": cut(13, 6, 3),
        "partial": ("invalid", metadata(13, None, 3)),
        "unknown": invoke.LLMInvokeError("synthetic unknown"),
        "cached_unknown": ("invalid", metadata(13, 6, None)),
    }
    result, calls, slept = run(
        [cut(), ("invalid", metadata(7, 3, 1)), ("invalid", metadata(11, 4, 2)), responses[failure]],
        wider=True,
    )
    assert result is exhausted[0]
    assert result.kind == "truncated" and not result.retryable
    assert "continuation exhausted after 2 attempts" in str(result)
    assert result.__cause__ is None
    assert calls.call_count == 4
    assert calls.call_args_list[-1].args[0] == "original prompt"
    assert [c.args[1].max_tokens for c in calls.call_args_list] == [100, 100, 100, 200]
    slept.assert_called_once_with(invoke._CONTINUE_DELAY_S)
    expected = (
        (28, 12, 5)
        if failure == "unknown"
        else (
            (41, 12, 8)
            if failure == "partial"
            else ((41, 18, 5) if failure == "cached_unknown" else (41, 18, 8))
        )
    )
    known = (
        set()
        if failure == "unknown"
        else (
            {"input_tokens", "cached_input_tokens"}
            if failure == "partial"
            else (
                {"input_tokens", "output_tokens"}
                if failure == "cached_unknown"
                else set(invoke._USAGE_FIELDS)
            )
        )
    )
    assert counts(result.observed_usage) == expected
    assert result.observed_usage.known_fields == frozenset(known)
    if failure in {"partial", "unknown"}:
        assert result.usage is None
    else:
        assert result.usage is result.observed_usage


def test_wider_failed_zero_is_known(run):
    result, calls, _ = run([cut(0, 0, 0)] + [("invalid", metadata(0, 0, 0))] * 3, wider=True)
    assert calls.call_count == 4
    assert counts(result.usage) == (0, 0, 0)
    assert result.usage.known_fields == frozenset(invoke._USAGE_FIELDS)


@pytest.mark.parametrize("mode", ["guard", "success", "breaker"])
def test_wider_callback_excludes_skip_success_and_breaker(monkeypatch, run, mode):
    # The run fixture installs network/process tripwires; helper dispatch is mocked too.
    backend = BackendConfig(
        name="synthetic",
        type="api",
        model="synthetic",
        format="openai",
        max_tokens=100,
        output_token_limit=0 if mode == "guard" else 200,
    )
    observe = Mock()
    breaker = invoke.TruncationBreakerError(1, 1)
    dispatch = (
        Mock(side_effect=breaker)
        if mode == "breaker"
        else Mock(return_value=('{"verdict":"DISMISSED","reasoning":"ok"}', metadata(13, 6, 3)))
    )
    monkeypatch.setattr(invoke, "_invoke_openai", dispatch)
    if mode == "breaker":
        with pytest.raises(invoke.TruncationBreakerError) as caught:
            invoke._retry_with_more_headroom(
                "p",
                backend,
                "synthetic",
                10,
                frozenset({"verdict", "reasoning"}),
                on_failure_usage=observe,
            )
        assert caught.value is breaker
    else:
        result = invoke._retry_with_more_headroom(
            "p", backend, "synthetic", 10, frozenset({"verdict", "reasoning"}), on_failure_usage=observe
        )
        assert (result is None) == (mode == "guard")
    assert dispatch.call_count == (0 if mode == "guard" else 1)
    observe.assert_not_called()
