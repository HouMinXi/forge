"""Contract tests pinning _parse_response_body failure-shape boundaries.

Each test names the archived mutant boundary it pins. See
.planning/quick/260922-parallel-debt/historical-replay-ledger.json.
"""
import pytest

from code_forge.llm_invoke import LLMInvokeError, _parse_response_body


class TestSseBodyDetection:
    """SSE-shaped bodies must raise kind='sse_body' with retryable=True."""

    def test_leading_whitespace_still_detects_sse(self):
        # Pins mutmut_12: lstrip must not become rstrip.
        with pytest.raises(LLMInvokeError) as ei:
            _parse_response_body(b'   data: {"a": 1}\n\n', "testbe")
        assert ei.value.kind == "sse_body"

    def test_sse_comment_line_detects_sse(self):
        # Pins mutmut_18: ':' must stay in the startswith tuple.
        with pytest.raises(LLMInvokeError) as ei:
            _parse_response_body(b': keep-alive\n\n', "testbe")
        assert ei.value.kind == "sse_body"

    def test_sse_body_is_retryable(self):
        # Pins mutmut_20: retryable must stay exactly True, not None.
        with pytest.raises(LLMInvokeError) as ei:
            _parse_response_body(b'data: {"a": 1}\n\n', "testbe")
        assert ei.value.retryable is True

    def test_sse_message_prefix_exact(self):
        # Pins mutmut_26/27: message prefix and 'SSE' casing are contract.
        with pytest.raises(LLMInvokeError) as ei:
            _parse_response_body(b'data: {"a": 1}\n\n', "testbe")
        assert str(ei.value).startswith(
            "testbe backend returned an SSE stream body: ")

    def test_sse_excerpt_capped_at_200(self):
        # Pins mutmut_29: excerpt is body_text[:200], not [:201].
        body = b"data: " + b"x" * 194 + b"Z" + b"x" * 50
        assert len(body) > 200 and body[200:201] == b"Z"
        with pytest.raises(LLMInvokeError) as ei:
            _parse_response_body(body, "testbe")
        assert "Z" not in str(ei.value)


class TestBadBodyDetection:
    """Non-JSON, non-SSE bodies must raise kind='bad_body', retryable=True."""

    def test_bad_body_is_retryable(self):
        # Pins mutmut_30: retryable must stay True, not False.
        with pytest.raises(LLMInvokeError) as ei:
            _parse_response_body(b"this is not json", "testbe")
        assert ei.value.kind == "bad_body"
        assert ei.value.retryable is True

    def test_bad_body_message_prefix_exact(self):
        # Pins mutmut_40/41: message prefix and 'JSON' casing are contract.
        with pytest.raises(LLMInvokeError) as ei:
            _parse_response_body(b"this is not json", "testbe")
        assert str(ei.value).startswith(
            "testbe backend returned non-JSON response body: ")

    def test_bad_body_excerpt_capped_at_200(self):
        # Pins mutmut_43: excerpt is body_text[:200], not [:201].
        body = b"garbage " + b"x" * 192 + b"Z" + b"x" * 50
        assert len(body) > 200 and body[200:201] == b"Z"
        with pytest.raises(LLMInvokeError) as ei:
            _parse_response_body(body, "testbe")
        assert "Z" not in str(ei.value)

    def test_valid_json_still_parses(self):
        # Guard: the happy path must not regress while pinning failures.
        assert _parse_response_body(b'{"ok": true}', "testbe") == {"ok": True}
