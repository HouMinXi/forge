"""Pin the accounting llm_invoke does after an excerpt repair.

The repair follow-up spends its own tokens and its own time. The result
handed back has to carry the sum of both calls, for every usage field and
for the duration. A repair that is not needed must not add a second call.
"""

import code_forge.llm_invoke as invoke
from code_forge.backend import BackendConfig
from code_forge.llm_invoke import LLMResult, Usage, llm_invoke

_FINDINGS = {"findings": [{"file": "a.py", "line_range": [3, 5]}]}
_EXCERPTS = [{"file": "a.py", "start_line": 3, "end_line": 5, "content": "x = 1"}]


def _usage(inp, out, cached):
    return Usage(input_tokens=inp, output_tokens=out, cached_input_tokens=cached)


def test_repair_usage_and_duration_are_added_to_the_first_call(monkeypatch):
    first = LLMResult(content=_FINDINGS, usage=_usage(10, 4, 3), duration_s=1.5)
    monkeypatch.setattr(invoke, "_invoke_cli", lambda *a, **k: first)
    repair_calls = []

    def fake_repair(parsed, prompt, backend, timeout_s, *, l1_evidence_required=False):
        assert l1_evidence_required is False
        repair_calls.append((parsed, prompt, backend, timeout_s))
        return ({**parsed, "code_excerpts": _EXCERPTS}, _usage(6, 2, 1), 0.5)

    monkeypatch.setattr(invoke, "_repair_missing_excerpts", fake_repair)
    backend = BackendConfig(name="b", type="cli", model="m")

    result = llm_invoke("review a.py", backend, 90)

    assert len(repair_calls) == 1
    parsed, prompt, got_backend, got_timeout = repair_calls[0]
    assert parsed is _FINDINGS
    assert prompt == "review a.py"
    assert got_backend is backend
    assert got_timeout == 90
    assert result.content["code_excerpts"] == _EXCERPTS
    assert result.content["findings"] == _FINDINGS["findings"]
    assert result.usage.input_tokens == 16
    assert result.usage.output_tokens == 6
    assert result.usage.cached_input_tokens == 4
    assert result.duration_s == 2.0
    assert result.is_truncated is False


def test_envelope_with_excerpts_already_present_is_not_repaired(monkeypatch):
    content = {**_FINDINGS, "code_excerpts": _EXCERPTS}
    first = LLMResult(content=content, usage=_usage(10, 4, 3), duration_s=1.5)
    monkeypatch.setattr(invoke, "_invoke_cli", lambda *a, **k: first)

    def fail_repair(*args, **kwargs):
        raise AssertionError("repair was called")

    monkeypatch.setattr(invoke, "_repair_missing_excerpts", fail_repair)

    result = llm_invoke("review a.py", BackendConfig(name="b", type="cli", model="m"), 90)

    assert result.content is content
    assert result.usage.input_tokens == 10
    assert result.duration_s == 1.5


def test_error_usage_carrier_preserves_existing_positional_constructor():
    error = invoke.LLMInvokeError("failed", 7, "owned stderr", 2.5, True, False, 3.0, "empty")
    assert (
        str(error),
        error.exit_code,
        error.stderr,
        error.duration_s,
        error.is_timeout,
        error.retryable,
        error.retry_after,
        error.kind,
        error.usage,
    ) == ("failed", 7, "owned stderr", 2.5, True, False, 3.0, "empty", None)
    known_zero = invoke.LLMInvokeError("failed", usage=Usage())
    assert known_zero.usage == Usage() and known_zero.usage is not None
