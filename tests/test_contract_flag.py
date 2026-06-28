# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for --contract flag: _load_contract_file, _merge_contract_spec."""

import io
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from code_forge.cli import (
    _CONFIRMATION_BIAS_DIRECTIVE,
    _load_contract_file,
    _merge_contract_spec,
)
from code_forge.errors import CliError


# ---------------------------------------------------------------------------
# _load_contract_file: file path (happy + guards)
# ---------------------------------------------------------------------------


class TestLoadContractFile:
    """Guards: empty path, missing, permission, encoding, empty content,
    whitespace-only, binary, oversize.  Happy path: normal file read."""

    def test_happy_path(self, tmp_path):
        f = tmp_path / "contract.md"
        f.write_text("## Invariants\n- x > 0\n", encoding="utf-8")
        result = _load_contract_file(str(f))
        assert "x > 0" in result

    def test_empty_path_raises(self):
        with pytest.raises(CliError, match="contract path is empty"):
            _load_contract_file("")

    def test_missing_file_raises(self, tmp_path):
        missing = str(tmp_path / "nope.md")
        with pytest.raises(CliError, match="not found"):
            _load_contract_file(missing)

    def test_permission_error_raises(self, tmp_path):
        f = tmp_path / "locked.md"
        f.write_text("content")
        f.chmod(0o000)
        try:
            with pytest.raises(CliError, match="not readable"):
                _load_contract_file(str(f))
        finally:
            f.chmod(0o644)

    def test_empty_content_raises(self, tmp_path):
        f = tmp_path / "empty.md"
        f.write_text("", encoding="utf-8")
        with pytest.raises(CliError, match="is empty"):
            _load_contract_file(str(f))

    def test_whitespace_only_raises(self, tmp_path):
        f = tmp_path / "ws.md"
        f.write_text("   \n\t\n  ", encoding="utf-8")
        with pytest.raises(CliError, match="is empty"):
            _load_contract_file(str(f))

    def test_binary_content_raises(self, tmp_path):
        f = tmp_path / "bin.md"
        f.write_bytes(b"header\x00\x01\x02rest")
        with pytest.raises(CliError, match="binary"):
            _load_contract_file(str(f))

    def test_oversize_raises(self, tmp_path):
        f = tmp_path / "big.md"
        f.write_text("x" * 65537, encoding="utf-8")
        with pytest.raises(CliError, match="64KB"):
            _load_contract_file(str(f))

    def test_exactly_64kb_ok(self, tmp_path):
        f = tmp_path / "exact.md"
        # 65536 bytes of single-byte chars = exactly 64KB
        f.write_text("a" * 65536, encoding="utf-8")
        result = _load_contract_file(str(f))
        assert len(result) == 65536

    def test_oserror_generic_raises(self, tmp_path):
        """OSError subtypes beyond FileNotFoundError/PermissionError."""
        with patch("code_forge.cli.Path.read_text", side_effect=OSError("disk")):
            with pytest.raises(CliError, match="contract file error"):
                _load_contract_file("/some/path")

    def test_value_error_encoding_raises(self, tmp_path):
        """ValueError from read_text (encoding issue)."""
        with patch(
            "code_forge.cli.Path.read_text",
            side_effect=ValueError("bad encoding"),
        ):
            with pytest.raises(CliError, match="not valid UTF-8"):
                _load_contract_file("/some/path")


# ---------------------------------------------------------------------------
# _load_contract_file: stdin path ("-")
# ---------------------------------------------------------------------------


class TestLoadContractStdin:
    """D-32-16: stdin via sys.stdin.buffer.read(65537)."""

    def test_stdin_happy(self):
        fake_buf = io.BytesIO(b"## Contract\n- invariant A\n")
        with patch.object(sys, "stdin", new=SimpleNamespace(buffer=fake_buf)):
            result = _load_contract_file("-")
        assert "invariant A" in result

    def test_stdin_oversize(self):
        fake_buf = io.BytesIO(b"x" * 65537)
        with patch.object(sys, "stdin", new=SimpleNamespace(buffer=fake_buf)):
            with pytest.raises(CliError, match="stdin exceeds 64KB"):
                _load_contract_file("-")

    def test_stdin_binary(self):
        fake_buf = io.BytesIO(b"hello\x00world")
        with patch.object(sys, "stdin", new=SimpleNamespace(buffer=fake_buf)):
            with pytest.raises(CliError, match="stdin.*binary"):
                _load_contract_file("-")

    def test_stdin_bad_utf8(self):
        fake_buf = io.BytesIO(b"\xff\xfe invalid")
        with patch.object(sys, "stdin", new=SimpleNamespace(buffer=fake_buf)):
            with pytest.raises(CliError, match="stdin.*not valid UTF-8"):
                _load_contract_file("-")

    def test_stdin_empty(self):
        fake_buf = io.BytesIO(b"")
        with patch.object(sys, "stdin", new=SimpleNamespace(buffer=fake_buf)):
            with pytest.raises(CliError, match="is empty"):
                _load_contract_file("-")

    def test_stdin_whitespace_only(self):
        fake_buf = io.BytesIO(b"   \n  ")
        with patch.object(sys, "stdin", new=SimpleNamespace(buffer=fake_buf)):
            with pytest.raises(CliError, match="is empty"):
                _load_contract_file("-")


# ---------------------------------------------------------------------------
# _merge_contract_spec
# ---------------------------------------------------------------------------


class TestMergeContractSpec:
    """D-32-21, D-32-23: merge helper, empty-yaml no leading newline,
    confirmation bias directive appended."""

    def test_both_empty_returns_empty(self):
        assert _merge_contract_spec("", "") == ""

    def test_yaml_only(self):
        result = _merge_contract_spec("yaml-digest", "")
        assert result == "yaml-digest" + _CONFIRMATION_BIAS_DIRECTIVE

    def test_file_only(self):
        result = _merge_contract_spec("", "file-content")
        assert result == "file-content" + _CONFIRMATION_BIAS_DIRECTIVE

    def test_file_only_no_leading_newline(self):
        """D-32-23: when yaml_digest is empty, no leading '\\n\\n'."""
        result = _merge_contract_spec("", "file-content")
        assert not result.startswith("\n")

    def test_both_present_joined(self):
        result = _merge_contract_spec("yaml", "file")
        assert result == "yaml\n\nfile" + _CONFIRMATION_BIAS_DIRECTIVE

    def test_directive_content(self):
        assert "NOT a proof of correctness" in _CONFIRMATION_BIAS_DIRECTIVE
        assert "Assume violations exist" in _CONFIRMATION_BIAS_DIRECTIVE

    def test_small_content_no_summarization(self):
        """Content <= 4KB: no llm_invoke call even with backend."""
        backend = SimpleNamespace(name="test")
        with patch("code_forge.llm_invoke.llm_invoke") as mock_llm:
            result = _merge_contract_spec("", "short", backend=backend)
            mock_llm.assert_not_called()
        assert "short" in result

    def test_large_content_summarized_with_backend(self):
        """Content > 4KB + backend: llm_invoke called, summary used."""
        big = "x" * 4097
        mock_result = SimpleNamespace(content="summary-text")
        with patch(
            "code_forge.llm_invoke.llm_invoke", return_value=mock_result
        ) as mock_llm:
            result = _merge_contract_spec(
                "", big, backend=SimpleNamespace(name="test")
            )
            mock_llm.assert_called_once()
        assert "summary-text" in result
        assert big not in result

    def test_large_content_no_backend_uses_raw(self):
        """Content > 4KB but no backend: raw content injected."""
        big = "x" * 4097
        result = _merge_contract_spec("", big, backend=None)
        assert big in result

    def test_summarization_failure_falls_back_to_raw(self):
        """llm_invoke raises: fall back to raw, warn."""
        big = "x" * 4097
        warnings = []
        with patch(
            "code_forge.llm_invoke.llm_invoke",
            side_effect=RuntimeError("boom"),
        ):
            result = _merge_contract_spec(
                "",
                big,
                backend=SimpleNamespace(name="test"),
                warn_fn=warnings.append,
            )
        assert big in result
        assert any("failed" in w for w in warnings)

    def test_summarization_empty_falls_back_to_raw(self):
        """llm_invoke returns empty summary: fall back to raw, warn."""
        big = "x" * 4097
        warnings = []
        mock_result = SimpleNamespace(content="   ")
        with patch(
            "code_forge.llm_invoke.llm_invoke", return_value=mock_result
        ):
            result = _merge_contract_spec(
                "",
                big,
                backend=SimpleNamespace(name="test"),
                warn_fn=warnings.append,
            )
        assert big in result
        assert any("empty" in w for w in warnings)
