"""Pin the per-format usage mapping and the credential gate in _invoke_api.

OpenAI reports prompt_tokens and completion_tokens; anthropic and vertex
report input_tokens and output_tokens. Each branch has to read its own
field names. A backend with no key source, and a format the dispatcher
does not know, must fail before any format helper runs.
"""

import pytest

import code_forge.llm_invoke as invoke
from code_forge.backend import BackendConfig
from code_forge.llm_invoke import LLMInvokeError, _invoke_api


def _backend(fmt, **kwargs):
    return BackendConfig(
        name="b", type="api", format=fmt, model="m", api_key_env="FORGE_TEST_KEY", **kwargs
    )


def _stub(monkeypatch, name, content, usage):
    calls = []

    def fake(*args, **kwargs):
        calls.append((args, kwargs))
        return content, usage

    monkeypatch.setattr(invoke, name, fake)
    return calls


def test_openai_reads_prompt_and_completion_tokens(monkeypatch):
    monkeypatch.setenv("FORGE_TEST_KEY", "k")
    calls = _stub(
        monkeypatch, "_invoke_openai", '{"ok": true}', {"prompt_tokens": 11, "completion_tokens": 7}
    )
    _stub(monkeypatch, "_invoke_anthropic", "wrong", {"input_tokens": 1, "output_tokens": 1})

    result = _invoke_api("prompt", _backend("openai"), 30, max_attempts=1)

    assert len(calls) == 1
    assert calls[0][0][0] == "prompt"
    assert calls[0][0][2] == "k"
    assert calls[0][0][3] == 30
    assert result.content == {"ok": True}
    assert result.usage.input_tokens == 11
    assert result.usage.output_tokens == 7


def test_anthropic_reads_input_and_output_tokens(monkeypatch):
    monkeypatch.setenv("FORGE_TEST_KEY", "k")
    calls = _stub(
        monkeypatch, "_invoke_anthropic", '{"ok": true}', {"input_tokens": 5, "output_tokens": 9}
    )

    result = _invoke_api("prompt", _backend("anthropic"), 30, max_attempts=1)

    assert len(calls) == 1
    assert result.usage.input_tokens == 5
    assert result.usage.output_tokens == 9


def test_vertex_reads_input_and_output_tokens_without_a_key(monkeypatch):
    calls = _stub(monkeypatch, "_invoke_vertex", '{"ok": true}', {"input_tokens": 3, "output_tokens": 8})

    result = _invoke_api(
        "prompt", BackendConfig(name="b", type="api", format="vertex", model="m"), 30, max_attempts=1
    )

    assert len(calls) == 1
    assert calls[0][0][2] == 30
    assert result.usage.input_tokens == 3
    assert result.usage.output_tokens == 8


def test_missing_usage_fields_count_as_zero(monkeypatch):
    monkeypatch.setenv("FORGE_TEST_KEY", "k")
    _stub(monkeypatch, "_invoke_openai", '{"ok": true}', {})

    result = _invoke_api("prompt", _backend("openai"), 30, max_attempts=1)

    assert result.usage.input_tokens == 0
    assert result.usage.output_tokens == 0


def test_no_key_source_fails_before_any_format_helper(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("format helper was called")

    monkeypatch.setattr(invoke, "_invoke_openai", fail)
    backend = BackendConfig(name="b", type="api", format="openai", model="m")

    with pytest.raises(LLMInvokeError) as caught:
        _invoke_api("prompt", backend, 30, max_attempts=1)

    assert caught.value.retryable is False
    assert caught.value.kind == "credentials"


def test_unreadable_key_file_is_not_retried(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise AssertionError("format helper was called")

    monkeypatch.setattr(invoke, "_invoke_openai", fail)
    missing = tmp_path / "absent.key"
    backend = BackendConfig(name="b", type="api", format="openai", model="m", api_key_file=str(missing))

    with pytest.raises(LLMInvokeError) as caught:
        _invoke_api("prompt", backend, 30, max_attempts=3)

    assert caught.value.retryable is False
    assert caught.value.kind == "credentials"
    assert str(missing) in str(caught.value)


def test_empty_key_file_is_not_retried(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise AssertionError("format helper was called")

    monkeypatch.setattr(invoke, "_invoke_openai", fail)
    empty = tmp_path / "empty.key"
    empty.write_text("   ")
    backend = BackendConfig(name="b", type="api", format="openai", model="m", api_key_file=str(empty))

    with pytest.raises(LLMInvokeError) as caught:
        _invoke_api("prompt", backend, 30, max_attempts=3)

    assert caught.value.retryable is False
    assert caught.value.kind == "credentials"


def test_unknown_format_fails_before_any_format_helper(monkeypatch):
    monkeypatch.setenv("FORGE_TEST_KEY", "k")

    def fail(*args, **kwargs):
        raise AssertionError("format helper was called")

    for name in ("_invoke_openai", "_invoke_anthropic", "_invoke_vertex"):
        monkeypatch.setattr(invoke, name, fail)

    with pytest.raises(LLMInvokeError) as caught:
        _invoke_api("prompt", _backend("grpc"), 30, max_attempts=1)

    assert "grpc" in str(caught.value)


def test_duration_is_the_elapsed_monotonic_time(monkeypatch):
    monkeypatch.setenv("FORGE_TEST_KEY", "k")
    _stub(monkeypatch, "_invoke_openai", '{"ok": true}', {"prompt_tokens": 1, "completion_tokens": 1})
    clock = iter([100.0, 100.25])
    monkeypatch.setattr(invoke.time, "monotonic", lambda: next(clock))

    result = _invoke_api("prompt", _backend("openai"), 30, max_attempts=1)

    assert result.duration_s == 0.25


def test_completed_non_json_is_not_retried(monkeypatch):
    monkeypatch.setenv("FORGE_TEST_KEY", "k")
    calls = _stub(
        monkeypatch,
        "_invoke_openai",
        "not json at all",
        {"prompt_tokens": 1, "completion_tokens": 1, "_forge_finish_reason": "stop"},
    )

    with pytest.raises(LLMInvokeError) as caught:
        _invoke_api("prompt", _backend("openai"), 30, max_attempts=3)

    assert len(calls) == 1
    assert caught.value.kind == "no_json"
    assert caught.value.retryable is False
    assert "finish_reason='stop'" in str(caught.value)


def test_missing_finish_reason_is_unknown(monkeypatch):
    monkeypatch.setenv("FORGE_TEST_KEY", "k")
    _stub(monkeypatch, "_invoke_openai", "not json at all", {"prompt_tokens": 1, "completion_tokens": 1})

    with pytest.raises(LLMInvokeError) as caught:
        _invoke_api("prompt", _backend("openai"), 30, max_attempts=1)

    assert "finish_reason=''" in str(caught.value)


def test_cut_off_json_is_continued(monkeypatch):
    monkeypatch.setenv("FORGE_TEST_KEY", "k")
    answers = iter(
        [
            (
                '{"findings":',
                {"prompt_tokens": 1, "completion_tokens": 1, "_forge_finish_reason": "stop"},
            ),
            ('[], "code_excerpts": []}', {"prompt_tokens": 1, "completion_tokens": 1}),
        ]
    )
    calls = []

    def fake(*args, **kwargs):
        calls.append(args[0])
        return next(answers)

    monkeypatch.setattr(invoke, "_invoke_openai", fake)

    result = _invoke_api("prompt", _backend("openai"), 30, max_attempts=1)

    assert len(calls) == 2
    assert result.content == {"findings": [], "code_excerpts": []}


def test_retryable_failure_is_retried_then_reported(monkeypatch):
    monkeypatch.setenv("FORGE_TEST_KEY", "k")
    calls = []

    def fail(*args, **kwargs):
        calls.append(len(calls))
        raise LLMInvokeError("overloaded", retryable=True)

    monkeypatch.setattr(invoke, "_invoke_openai", fail)
    monkeypatch.setattr(invoke.time, "sleep", lambda delay: None)
    messages = []
    monkeypatch.setattr(invoke.progress, "emit", messages.append)

    with pytest.raises(LLMInvokeError):
        _invoke_api("prompt", _backend("openai"), 30, max_attempts=2, initial_delay_s=0)

    assert calls == [0, 1]
    assert "retry failed b after 2 attempts: overloaded" in messages
    assert any(m.startswith("retrying b (2/2") for m in messages)


def test_first_non_retryable_attempt_stays_quiet(monkeypatch):
    monkeypatch.setenv("FORGE_TEST_KEY", "k")

    def fail(*args, **kwargs):
        raise LLMInvokeError("rejected", retryable=False)

    monkeypatch.setattr(invoke, "_invoke_openai", fail)
    messages = []
    monkeypatch.setattr(invoke.progress, "emit", messages.append)

    with pytest.raises(LLMInvokeError):
        _invoke_api("prompt", _backend("openai"), 30, max_attempts=3)

    assert messages == []
