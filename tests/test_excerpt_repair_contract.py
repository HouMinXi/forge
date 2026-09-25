"""Pin how the excerpt repair follow-up dispatches and preserves findings.

The repair call asks once for code_excerpts. It must route by backend
type, pass the repair prompt and the caller's timeout through, and on
failure hand back the original envelope with zero usage and duration so
the caller still sees the findings as UNTRUSTED.
"""

import code_forge.llm_invoke as invoke
from code_forge import progress
from code_forge.backend import BackendConfig
from code_forge.llm_invoke import (
    LLMInvokeError,
    LLMResult,
    Usage,
    _repair_missing_excerpts,
)

_PARSED = {"findings": [{"file": "a.py", "line_range": [3, 5]}]}
_EXCERPTS = [
    {"file": "a.py", "start_line": 3, "end_line": 5, "content": "x = 1"}
]


def _backend(kind):
    return BackendConfig(name="b", type=kind, model="m")


def _record(monkeypatch, name, result=None, error=None):
    calls = []

    def fake(*args, **kwargs):
        calls.append((args, kwargs))
        if error is not None:
            raise error
        return result

    monkeypatch.setattr(invoke, name, fake)
    return calls


def _capture_progress(monkeypatch):
    messages = []
    monkeypatch.setattr(progress, "emit", messages.append)
    return messages


def test_cli_backend_gets_repair_prompt_and_keeps_findings(monkeypatch):
    usage = Usage(input_tokens=4, output_tokens=2)
    follow = LLMResult(
        content={"code_excerpts": _EXCERPTS}, usage=usage, duration_s=2.5
    )
    cli_calls = _record(monkeypatch, "_invoke_cli", result=follow)
    api_calls = _record(monkeypatch, "_invoke_api", result=follow)
    messages = _capture_progress(monkeypatch)
    backend = _backend("cli")

    repaired, got_usage, got_duration = _repair_missing_excerpts(
        _PARSED, "review a.py", backend, 90
    )

    assert api_calls == []
    assert len(cli_calls) == 1
    args, kwargs = cli_calls[0]
    assert len(args) == 3
    repair_prompt, got_backend, got_timeout = args
    assert repair_prompt == invoke._excerpt_repair_prompt(
        _PARSED, "review a.py"
    )
    assert got_backend is backend
    assert got_timeout == 90
    assert kwargs == {}
    assert repaired is not _PARSED
    assert repaired["findings"] == _PARSED["findings"]
    assert repaired["code_excerpts"] == _EXCERPTS
    assert got_usage is usage
    assert got_duration == 2.5
    assert messages == ["excerpt-repair: asking for code_excerpts"]


def test_api_backend_asks_once_without_expected_keys(monkeypatch):
    follow = LLMResult(content={"code_excerpts": _EXCERPTS})
    api_calls = _record(monkeypatch, "_invoke_api", result=follow)
    cli_calls = _record(monkeypatch, "_invoke_cli", result=follow)
    backend = _backend("api")

    repaired, _, _ = _repair_missing_excerpts(
        _PARSED, "review a.py", backend, 45
    )

    assert cli_calls == []
    assert len(api_calls) == 1
    args, kwargs = api_calls[0]
    assert args[1] is backend
    assert args[2] == 45
    assert "expected_keys" in kwargs
    assert kwargs["expected_keys"] is None
    assert "max_attempts" in kwargs
    assert kwargs["max_attempts"] == 1
    assert repaired["findings"] == _PARSED["findings"]
    assert repaired["code_excerpts"] == _EXCERPTS


def test_llm_error_returns_original_with_zero_usage_and_duration(
    monkeypatch,
):
    _record(
        monkeypatch, "_invoke_api", error=LLMInvokeError("backend down")
    )
    _capture_progress(monkeypatch)

    repaired, got_usage, got_duration = _repair_missing_excerpts(
        _PARSED, "review a.py", _backend("api"), 90
    )

    assert repaired is _PARSED
    assert got_usage.input_tokens == 0
    assert got_usage.output_tokens == 0
    assert got_usage.cached_input_tokens == 0
    assert got_duration == 0.0


def test_empty_follow_up_returns_original_with_real_usage(monkeypatch):
    usage = Usage(input_tokens=7)
    follow = LLMResult(content={}, usage=usage, duration_s=1.5)
    _record(monkeypatch, "_invoke_api", result=follow)

    repaired, got_usage, got_duration = _repair_missing_excerpts(
        _PARSED, "review a.py", _backend("api"), 90
    )

    assert repaired is _PARSED
    assert got_usage is usage
    assert got_duration == 1.5


def test_success_returns_copy_with_excerpts_added_and_keys_kept(monkeypatch):
    parsed = {"findings": _PARSED["findings"], "summary": "one issue"}
    follow = LLMResult(content={"code_excerpts": _EXCERPTS})
    _record(monkeypatch, "_invoke_api", result=follow)
    _capture_progress(monkeypatch)

    repaired, _, _ = _repair_missing_excerpts(parsed, "review a.py", _backend("api"), 90)

    assert repaired == {
        "findings": _PARSED["findings"],
        "summary": "one issue",
        "code_excerpts": _EXCERPTS,
    }
    assert parsed == {
        "findings": _PARSED["findings"],
        "summary": "one issue",
    }
