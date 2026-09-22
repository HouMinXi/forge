"""Contract tests for JSON diagnostic and retry helpers in code_forge.llm_invoke.

Covers:
- src/code_forge/llm_invoke.py::_no_json_retryable
- src/code_forge/llm_invoke.py::_no_json_diagnostic
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from code_forge.llm_invoke import _no_json_diagnostic, _no_json_retryable


class TestNoJsonRetryableContract:
    """Contract tests for _no_json_retryable."""

    @pytest.mark.parametrize(
        ("reason", "expected"),
        [
            # Empty / None / whitespace outcomes are treated as incomplete streams and retry
            ("", True),
            (None, True),
            ("   ", True),
            ("\n\t", True),
            # Incomplete / cut stream reasons are retryable
            ("length", True),
            ("LENGTH", True),
            (" length ", True),
            ("max_tokens", True),
            ("MAX_TOKENS", True),
            (" max_tokens ", True),
            ("max_completion_tokens", True),
            ("content_filter", True),
            ("unknown", True),
            ("incomplete_stream", True),
            # Completed stream reasons (exact tokens or variants with casing/spaces/underscores) are terminal
            ("stop", False),
            ("STOP", False),
            (" Stop ", False),
            ("endturn", False),
            ("end_turn", False),
            ("END_TURN", False),
            (" End_Turn ", False),
            ("stopsequence", False),
            ("stop_sequence", False),
            ("STOP_SEQUENCE", False),
            ("tooluse", False),
            ("tool_use", False),
            ("TOOL_USE", False),
        ],
    )
    def test_finish_reason_retryable_classification(
        self, reason: Any, expected: bool
    ) -> None:
        assert _no_json_retryable(reason) is expected

    def test_finish_reason_underscore_stripping(self) -> None:
        # Underscores must not change the normalized finish-reason classification.
        assert _no_json_retryable("_s_t_o_p_") is False
        assert _no_json_retryable("_e_n_d_t_u_r_n_") is False
        assert _no_json_retryable("_t_o_o_l_u_s_e_") is False
        assert _no_json_retryable("m_a_x_t_o_k_e_n_s") is True


class TestNoJsonDiagnosticContract:
    """Contract tests for _no_json_diagnostic."""

    def test_diagnostic_exact_structure(self) -> None:
        content = '{"key": "value", INVALID}'
        with pytest.raises(json.JSONDecodeError) as exc_info:
            json.loads(content)
        exc = exc_info.value
        diag = _no_json_diagnostic(exc, content, finish_reason="stop")

        lines = diag.splitlines()
        assert len(lines) == 3
        assert lines[0].startswith("JSONDecodeError: ")
        assert (
            lines[1]
            == f"finish_reason='stop' content_len={len(content)} pos={exc.pos}"
        )
        assert lines[2].startswith("around_pos: ")

    def test_window_at_start_of_content(self) -> None:
        # pos=0: lo must be 0, preserving the very first character
        content = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ" + ("x" * 200)
        exc = json.JSONDecodeError("Expecting value", content, 0)
        diag = _no_json_diagnostic(exc, content, finish_reason="length")

        # hi = min(len, 0 + 80) = 80; lo = max(0, 0 - 80) = 0
        expected_window = content[0:80]
        assert "pos=0" in diag
        assert f"around_pos: {expected_window!r}" in diag
        # Specifically check first char '0' is not dropped by an off-by-one lo boundary
        assert expected_window.startswith("0123456789")

    def test_window_in_middle_of_long_content(self) -> None:
        # pos=100 in 300-char content: lo=20, hi=180 (exact 160-char window)
        prefix = "A" * 100
        suffix = "B" * 200
        content = prefix + suffix
        exc = json.JSONDecodeError("Syntax error", content, 100)
        diag = _no_json_diagnostic(exc, content, finish_reason="max_tokens")

        expected_window = content[20:180]
        assert len(expected_window) == 160
        assert f"around_pos: {expected_window!r}" in diag
        assert "content_len=300 pos=100" in diag

    def test_window_at_end_of_content(self) -> None:
        # pos=95 in 100-char content: lo=15, hi=100 (clamped to len)
        content = "Y" * 100
        exc = json.JSONDecodeError("Unexpected EOF", content, 95)
        diag = _no_json_diagnostic(exc, content, finish_reason="stop")

        expected_window = content[15:100]
        assert f"around_pos: {expected_window!r}" in diag
        assert "content_len=100 pos=95" in diag

    def test_window_beyond_content_length(self) -> None:
        # pos exceeds len(content): hi clamped to len(content)
        content = "Z" * 50
        exc = json.JSONDecodeError("Corrupt pos", content, 150)
        diag = _no_json_diagnostic(exc, content, finish_reason="unknown")

        # lo = max(0, 150 - 80) = 70; hi = min(50, 150 + 80) = 50 -> content[70:50] is ""
        assert "around_pos: ''" in diag
        assert "content_len=50 pos=150" in diag

    def test_window_empty_content(self) -> None:
        content = ""
        exc = json.JSONDecodeError("Empty text", content, 0)
        diag = _no_json_diagnostic(exc, content, finish_reason="")

        assert "finish_reason='' content_len=0 pos=0" in diag
        assert "around_pos: ''" in diag

    def test_invalid_or_negative_pos_handled_safely(self) -> None:
        # Negative or non-int pos in exc must safely default pos to 0
        content = "SOME_CORRUPTED_JSON_CONTENT"
        # Manually create JSONDecodeError with negative pos
        exc_neg = json.JSONDecodeError("Test negative pos", content, -5)
        diag_neg = _no_json_diagnostic(exc_neg, content, finish_reason="stop")
        assert "pos=0" in diag_neg
        assert f"around_pos: {content[0:80]!r}" in diag_neg

        # Manually assign non-int pos
        exc_non_int = json.JSONDecodeError("Test non-int pos", content, 0)
        exc_non_int.pos = "not-an-int"  # type: ignore[assignment]
        diag_non_int = _no_json_diagnostic(exc_non_int, content, finish_reason="stop")
        assert "pos=0" in diag_non_int
        assert f"around_pos: {content[0:80]!r}" in diag_non_int

    def test_diagnostic_with_real_file_input(self, tmp_path: Path) -> None:
        # Exercises reading corrupted JSON from an actual file on disk
        payload_file = tmp_path / "model_response.json"
        # Place the malformed token beyond the beginning of the document
        raw_text = (
            '{"findings": [{"file": "test.py", "reason": "'
            + ("W" * 200)
            + '"}], BROKEN_TOKEN: true}'
        )
        payload_file.write_text(raw_text, encoding="utf-8")

        # Read back from disk to guarantee real filesystem I/O
        disk_content = payload_file.read_text(encoding="utf-8")
        assert len(disk_content) == len(raw_text)

        with pytest.raises(json.JSONDecodeError) as exc_info:
            json.loads(disk_content)

        diag = _no_json_diagnostic(
            exc_info.value, disk_content, finish_reason="stop"
        )
        assert "BROKEN_TOKEN" in diag
        assert f"content_len={len(disk_content)}" in diag
        assert "finish_reason='stop'" in diag
        assert exc_info.value.pos > 200
        assert f"pos={exc_info.value.pos}" in diag
