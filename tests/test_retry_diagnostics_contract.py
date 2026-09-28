"""Keep retry diagnostics readable and accurate on the real stderr path."""

import pytest

from code_forge import llm_invoke, progress


def test_retry_cause_collapses_whitespace():
    error = RuntimeError("  first\n\tsecond  third\r\n")
    assert llm_invoke._retry_cause(error) == "first second third"


@pytest.mark.parametrize("size", [399, 400, 401])
def test_retry_cause_caps_message_length(size):
    assert llm_invoke._retry_cause(RuntimeError("x" * size)) == "x" * min(size, 400)


@pytest.mark.parametrize("attempt, expected", [(0, 2), (1, 3), (3, 5)])
def test_retrying_reports_next_attempt_on_stderr(monkeypatch, capsys, attempt, expected):
    monkeypatch.setattr(progress, "_elapsed", lambda: 0.0)
    llm_invoke._emit_retrying("backend", attempt, 5, 1.25, "rate limited")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        f"[forge] t+0.0s retrying backend ({expected}/5, waiting 1.2s) after rate limited\n"
    )


def test_retry_failed_reports_attempt_count_on_stderr(monkeypatch, capsys):
    monkeypatch.setattr(progress, "_elapsed", lambda: 0.0)
    llm_invoke._emit_retry_failed("backend", 5, "rate limited")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ("[forge] t+0.0s retry failed backend after 5 attempts: rate limited\n")
