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

    def execute(responses, *, wider=False):
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
                max_attempts=1,
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
def test_wider_seam_keeps_existing_policy_and_exposes_separate_debt(run, wider_success):
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
        # Existing independent debt: wider success returns only its own usage.
        # This slice must not double-count or silently change that seam.
        assert result.content["reasoning"] == "wide"
        assert counts(result.usage) == (13, 6, 3)
    else:
        # The continuation error retains its own budget, not wider-attempt counts.
        assert result.kind == "truncated" and not result.retryable
        assert counts(result.observed_usage) == (28, 12, 5)
