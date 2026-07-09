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
    _DO_NOT_FLAG_PREAMBLE,
    _load_contract_file,
    _merge_contract_spec,
    _split_do_not_flag,
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

    def test_stdin_closed(self):
        """Closed stdin raises CliError, not bare ValueError."""
        closed_buf = io.BytesIO(b"")
        closed_buf.close()
        with patch.object(sys, "stdin", new=SimpleNamespace(buffer=closed_buf)):
            with pytest.raises(CliError, match="cannot read from stdin"):
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


# ---------------------------------------------------------------------------
# _split_do_not_flag
# ---------------------------------------------------------------------------


class TestSplitDoNotFlag:
    """Extract '## Do NOT Flag' section from contract content."""

    def test_no_section_returns_original(self):
        body, dnf = _split_do_not_flag("invariant: x must be positive")
        assert body == "invariant: x must be positive"
        assert dnf == ""

    def test_section_extracted(self):
        content = (
            "## Invariants\ncheck x\n\n"
            "## Do NOT Flag\n"
            "- repeated timeout constant is intentional\n"
            "- sys.argv read at cli.py:10 is the documented entrypoint\n"
        )
        body, dnf = _split_do_not_flag(content)
        assert "## Do NOT Flag" not in body
        assert "## Invariants" in body
        assert "repeated timeout constant" in dnf
        assert "sys.argv read" in dnf

    def test_section_between_other_headings(self):
        content = (
            "## Before\nstuff\n\n"
            "## Do NOT Flag\nexempt item\n\n"
            "## After\nmore stuff\n"
        )
        body, dnf = _split_do_not_flag(content)
        assert "## Before" in body
        assert "## After" in body
        assert "exempt item" in dnf
        assert "## Do NOT Flag" not in body

    def test_empty_section(self):
        content = "## Do NOT Flag\n\n## Next\ndata\n"
        body, dnf = _split_do_not_flag(content)
        assert dnf == ""
        assert "## Next" in body

    def test_heading_parenthetical(self):
        content = (
            "## Structural decisions (do NOT flag)\n"
            "- timeout is intentional\n"
            "## Other\nstuff\n"
        )
        body, dnf = _split_do_not_flag(content)
        assert "timeout is intentional" in dnf
        assert "## Other" in body

    def test_heading_h3(self):
        content = (
            "### Do NOT Flag\n"
            "- item A\n"
            "### Other\nstuff\n"
        )
        body, dnf = _split_do_not_flag(content)
        assert "item A" in dnf
        assert "### Other" in body

    def test_heading_h3_section_end(self):
        content = (
            "### Do NOT Flag\n- item\n### Next\nmore\n"
        )
        body, dnf = _split_do_not_flag(content)
        assert "item" in dnf
        assert "### Next" in body

    def test_heading_prefix_text(self):
        content = (
            "## Decisions: do not flag\n"
            "- intentional constant\n"
            "## Next\nstuff\n"
        )
        body, dnf = _split_do_not_flag(content)
        assert "intentional constant" in dnf

    def test_heading_all_caps(self):
        content = (
            "## DO NOT FLAG\n- exempt\n## Next\nstuff\n"
        )
        body, dnf = _split_do_not_flag(content)
        assert "exempt" in dnf

    def test_indented_comment_not_heading(self):
        """Indented # comment inside section does NOT terminate it."""
        content = (
            "## Do NOT Flag\n"
            "- item\n"
            "    # this is a code comment, not a heading\n"
            "- item 2\n"
            "## Next\nstuff\n"
        )
        body, dnf = _split_do_not_flag(content)
        assert "item" in dnf
        assert "item 2" in dnf
        assert "code comment" in dnf

    def test_bare_hashtag_not_heading(self):
        """#hashtag (no space) inside section does NOT terminate it."""
        content = (
            "## Do NOT Flag\n"
            "- item\n"
            "#hashtag\n"
            "- item 2\n"
            "## Next\nstuff\n"
        )
        body, dnf = _split_do_not_flag(content)
        assert "item 2" in dnf
        assert "#hashtag" in dnf

    def test_two_matching_headings_first_wins(self):
        """Greedy first-match: first heading containing 'do not flag'
        starts the section; second heading ends it."""
        content = (
            "## How to do not flag issues\n"
            "- guidance\n"
            "## Structural decisions (do NOT flag)\n"
            "- exempt\n"
            "## Next\nstuff\n"
        )
        body, dnf = _split_do_not_flag(content)
        assert "guidance" in dnf
        assert "exempt" not in dnf
        assert "Structural decisions" in body

    def test_heading_h1(self):
        """h1 heading splits correctly; h2/h3 do NOT terminate it."""
        content = (
            "# Do NOT Flag\n"
            "- item\n"
            "## Sub-heading\nmore\n"
            "### Sub-sub\n"
            "# Next Top\nstuff\n"
        )
        body, dnf = _split_do_not_flag(content)
        assert "item" in dnf
        assert "Sub-heading" in dnf
        assert "Sub-sub" in dnf
        assert "# Next Top" in body

    def test_do_not_flag_last_section_runs_to_eof(self):
        """Do-not-flag as last section: runs to EOF, all items captured."""
        content = (
            "## Invariants\ncheck x\n\n"
            "## Do NOT Flag\n"
            "- item 1\n"
            "- item 2\n"
            "- item 3\n"
        )
        body, dnf = _split_do_not_flag(content)
        assert "item 1" in dnf
        assert "item 2" in dnf
        assert "item 3" in dnf
        assert "## Invariants" in body

    def test_bare_hash_not_heading_start(self):
        """## or # alone (no space, no content) does NOT match."""
        content = (
            "##\n"
            "- not a heading\n"
            "## Next\nstuff"
        )
        body, dnf = _split_do_not_flag(content)
        assert dnf == ""
        assert body == content

    def test_h3_section_terminated_by_h2(self):
        """h2 heading terminates h3 do-not-flag section (level-aware)."""
        content = (
            "### Do NOT Flag\n"
            "- item\n"
            "## Other Section\nstuff\n"
        )
        body, dnf = _split_do_not_flag(content)
        assert "item" in dnf
        assert "## Other Section" in body

    def test_indented_sibling_terminates_section(self):
        """Indented sibling heading terminates the section (no
        swallow-to-EOF). CommonMark allows <=3 leading spaces."""
        content = (
            "  ## Do NOT Flag\n"
            "- exempt item\n"
            "  ## Other Section\n"
            "more stuff\n"
        )
        body, dnf = _split_do_not_flag(content)
        assert "exempt item" in dnf
        assert "Other Section" in body

    def test_deeply_indented_heading_not_recognized(self):
        """4+ space indent is a code block, not a heading. Must NOT
        match as do-not-flag section start."""
        content = (
            "    ## Do NOT Flag\n"
            "- would be exempt\n"
            "## Invariants\ncheck x\n"
        )
        body, dnf = _split_do_not_flag(content)
        assert dnf == ""
        assert "would be exempt" in body

    def test_3_space_indented_heading_recognized(self):
        """0-3 space indent is a valid CommonMark heading. Must match
        as do-not-flag section start."""
        for indent in ("", " ", "  ", "   "):
            content = (
                indent + "## Do NOT Flag\n"
                "- exempt item\n"
                "## Next\nstuff\n"
            )
            body, dnf = _split_do_not_flag(content)
            assert "exempt item" in dnf, (
                "indent %r should be recognized" % indent
            )
            assert "## Next" in body

    def test_tab_indented_heading_not_recognized(self):
        """Tab indent is not a CommonMark heading (requires spaces).
        Must NOT match as do-not-flag section start."""
        content = (
            "\t## Do NOT Flag\n"
            "- would be exempt\n"
            "## Invariants\ncheck x\n"
        )
        body, dnf = _split_do_not_flag(content)
        assert dnf == ""
        assert "would be exempt" in body


# ---------------------------------------------------------------------------
# Do-not-flag integration (bidirectional scoping proof)
# ---------------------------------------------------------------------------


class TestDoNotFlagIntegration:
    """The exemption must be SCOPED: named idioms exempt, unnamed bugs
    still governed by the bias directive."""

    _CONTRACT_WITH_EXEMPTION = (
        "## Invariants\n"
        "all functions must handle errors\n\n"
        "## Do NOT Flag\n"
        "- repeated _exec_timeout_s constant at cli.py:100 and "
        "cli.py:200 is the intended per-call timeout, not a "
        "magic-number smell\n"
        "- sys.argv read at cli.py:10 is the documented entrypoint\n"
    )

    def test_exemption_placed_after_bias_directive(self):
        """Named idioms appear AFTER the bias directive so the
        directive does not trail and undercut them."""
        result = _merge_contract_spec("", self._CONTRACT_WITH_EXEMPTION)
        bias_pos = result.find("Assume violations exist")
        preamble_pos = result.find("SPECIFIC patterns are author-asserted")
        assert bias_pos > 0, "bias directive missing"
        assert preamble_pos > 0, "exemption preamble missing"
        assert preamble_pos > bias_pos, (
            "exemption must follow the bias directive, not precede it"
        )

    def test_named_idiom_present_in_exemption(self):
        """The contract's named idiom appears in the output's
        exemption block."""
        result = _merge_contract_spec("", self._CONTRACT_WITH_EXEMPTION)
        assert "repeated _exec_timeout_s" in result
        assert "sys.argv read at cli.py:10" in result

    def test_invariants_still_governed_by_bias(self):
        """Content NOT in the exemption list stays before the bias
        directive and is governed by it."""
        result = _merge_contract_spec("", self._CONTRACT_WITH_EXEMPTION)
        invariant_pos = result.find("all functions must handle errors")
        bias_pos = result.find("Assume violations exist")
        assert invariant_pos < bias_pos, (
            "invariants must precede the bias directive"
        )

    def test_bug_inject_removing_split_collapses_exemption(self):
        """Bug-inject: if _split_do_not_flag is neutralized (always
        returns empty dnf), the exemption block disappears from the
        output -- proving the feature has teeth."""
        with patch(
            "code_forge.cli._split_do_not_flag",
            return_value=(self._CONTRACT_WITH_EXEMPTION, ""),
        ):
            result = _merge_contract_spec(
                "", self._CONTRACT_WITH_EXEMPTION
            )
        assert _DO_NOT_FLAG_PREAMBLE.strip() not in result
        assert "repeated _exec_timeout_s" in result  # still in body

    def test_no_exemption_section_leaves_bias_only(self):
        """Contract without '## Do NOT Flag': only the bias directive
        appears, no exemption preamble."""
        plain = "## Invariants\ncheck everything\n"
        result = _merge_contract_spec("", plain)
        assert "Assume violations exist" in result
        assert _DO_NOT_FLAG_PREAMBLE.strip() not in result

    def test_variation_heading_reaches_preamble(self):
        """Fuzzy heading match: preamble + exempt items in output."""
        contract = (
            "## Invariants\ncheck x\n\n"
            "## Structural decisions (do NOT flag)\n"
            "- timeout is intentional\n"
        )
        result = _merge_contract_spec("", contract)
        assert _DO_NOT_FLAG_PREAMBLE.strip() in result
        assert "timeout is intentional" in result

    def test_fuzzy_match_emits_warning(self):
        """Non-canonical heading triggers warn_fn."""
        contract = (
            "## Structural decisions (do NOT flag)\n- item\n"
        )
        warnings = []
        _merge_contract_spec(
            "", contract, warn_fn=warnings.append
        )
        assert any("canonical heading" in w for w in warnings)

    def test_exact_heading_no_warning(self):
        """Canonical heading does NOT trigger warn_fn."""
        contract = "## Do NOT Flag\n- item\n"
        warnings = []
        _merge_contract_spec(
            "", contract, warn_fn=warnings.append
        )
        assert not any("canonical heading" in w for w in warnings)

    def test_bug_inject_strict_match_misses_variation(self):
        """Two-level proof: old strict match misses variation heading;
        new fuzzy match catches it."""
        content = (
            "## Structural decisions (do NOT flag)\n"
            "- timeout is intentional\n"
            "## Next\nstuff\n"
        )
        # Level 1: new fuzzy matcher finds it
        _, dnf_new = _split_do_not_flag(content)
        assert dnf_new != "", "fuzzy match must find do-not-flag"

        # Level 1: old strict matcher misses it
        lines = content.split("\n")
        dnf_old = ""
        for i, line in enumerate(lines):
            if line.strip().lower() == "## do not flag":
                dnf_old = line
                break
        assert dnf_old == "", "strict match must miss variation"

        # Level 2: end-to-end via _merge_contract_spec
        result = _merge_contract_spec("", content)
        assert _DO_NOT_FLAG_PREAMBLE.strip() in result
