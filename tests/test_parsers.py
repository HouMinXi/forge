# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for forge.parsers -- tool output to Finding conversion.

Covers all eight parsers (shellcheck, ruff, semgrep, clippy, checkpatch,
non_ascii, flake8, pylint) plus the PARSER_DISPATCH system.  Each parser
is tested for:
  - valid output -> correct Finding fields
  - empty string -> [] (clean run)
  - malformed/corrupt input -> [ToolError]
"""

import json
from pathlib import Path

import pytest

from code_forge.parsers.base import Finding, ToolError
from code_forge.parsers.shellcheck import parse_shellcheck
from code_forge.parsers.ruff import parse_ruff
from code_forge.parsers.semgrep import parse_semgrep
from code_forge.parsers.clippy import parse_clippy
from code_forge.parsers.checkpatch import parse_checkpatch
from code_forge.parsers.non_ascii import parse_non_ascii
from code_forge.parsers.flake8 import parse_flake8
from code_forge.parsers.pylint import parse_pylint
from code_forge.parsers import PARSER_DISPATCH, parse_output

FIXTURES = Path(__file__).parent / "fixtures"


# -- shellcheck -------------------------------------------------------

class TestParseShellcheck:
    """Tests for parse_shellcheck()."""

    def test_valid_output(self):
        raw = (FIXTURES / "shellcheck_output.json").read_text()
        findings = parse_shellcheck(raw)
        assert len(findings) == 2
        # first finding
        f0 = findings[0]
        assert isinstance(f0, Finding)
        assert f0.file == "deploy.sh"
        assert f0.line == 4
        assert f0.end_line == 4
        assert f0.column == 7
        assert f0.rule_id == "SC2154"
        assert f0.level == "warning"
        assert f0.tool_name == "shellcheck"
        assert "referenced but not assigned" in f0.message
        # second finding
        f1 = findings[1]
        assert f1.rule_id == "SC2086"
        assert f1.level == "error"
        assert f1.line == 10

    def test_empty_input(self):
        assert parse_shellcheck("") == []
        assert parse_shellcheck("   ") == []

    def test_malformed_json(self):
        result = parse_shellcheck("not json at all", exit_code=2)
        assert len(result) == 1
        assert isinstance(result[0], ToolError)
        assert result[0].tool_name == "shellcheck"
        assert result[0].exit_code == 2

    def test_exit_code_propagated(self):
        result = parse_shellcheck("{bad", exit_code=127)
        assert result[0].exit_code == 127


# -- ruff (SARIF) -----------------------------------------------------

class TestParseRuff:
    """Tests for parse_ruff()."""

    def test_valid_sarif(self):
        raw = (FIXTURES / "ruff_sarif.json").read_text()
        findings = parse_ruff(raw)
        assert len(findings) == 2
        f0 = findings[0]
        assert isinstance(f0, Finding)
        assert f0.rule_id == "F401"
        assert f0.level == "error"
        # file:///src/app.py -> /src/app.py (absolute path preserved)
        assert f0.file == "/src/app.py"
        assert f0.line == 1
        assert f0.tool_name == "ruff"
        f1 = findings[1]
        assert f1.rule_id == "E501"
        assert f1.file == "src/utils.py"
        assert f1.line == 42

    def test_empty_input(self):
        assert parse_ruff("") == []

    def test_malformed_json(self):
        result = parse_ruff("corrupt garbage", exit_code=1)
        assert len(result) == 1
        assert isinstance(result[0], ToolError)
        assert result[0].tool_name == "ruff"
        assert result[0].exit_code == 1


# -- semgrep (SARIF) --------------------------------------------------

class TestParseSemgrep:
    """Tests for parse_semgrep()."""

    def test_valid_sarif(self):
        raw = (FIXTURES / "semgrep_sarif.json").read_text()
        findings = parse_semgrep(raw)
        assert len(findings) == 1
        f0 = findings[0]
        assert isinstance(f0, Finding)
        assert f0.rule_id == (
            "python.lang.security.audit.subprocess-shell-true"
        )
        assert f0.level == "warning"
        assert f0.file == "src/runner.py"
        assert f0.line == 15
        assert f0.end_line == 17
        assert f0.tool_name == "semgrep"

    def test_empty_input(self):
        assert parse_semgrep("") == []

    def test_malformed_json(self):
        result = parse_semgrep("random noise", exit_code=3)
        assert len(result) == 1
        assert isinstance(result[0], ToolError)
        assert result[0].tool_name == "semgrep"
        assert result[0].exit_code == 3


# -- clippy ------------------------------------------------------------

class TestParseClippy:
    """Tests for parse_clippy()."""

    def test_valid_output(self):
        raw = (FIXTURES / "clippy_output.json").read_text()
        findings = parse_clippy(raw)
        # Only the first compiler-message with non-empty spans
        # and the third with code=None but non-empty spans
        assert len(findings) == 2
        f0 = findings[0]
        assert isinstance(f0, Finding)
        assert f0.file == "src/main.rs"
        assert f0.line == 12
        assert f0.end_line == 12
        assert f0.column == 5
        assert f0.rule_id == "clippy::needless_return"
        assert f0.level == "warning"
        assert f0.tool_name == "clippy"
        # third diagnostic has code=None, should use fallback
        f1 = findings[1]
        assert f1.rule_id == "unknown"
        assert f1.file == "src/lib.rs"

    def test_empty_spans_skipped(self):
        """Diagnostic with empty spans array is skipped."""
        line = json.dumps({
            "reason": "compiler-message",
            "message": {
                "level": "warning",
                "message": "something",
                "code": {"code": "clippy::test", "explanation": None},
                "spans": [],
            },
        })
        findings = parse_clippy(line)
        assert findings == []

    def test_code_none_fallback(self):
        """Diagnostic with code=None uses 'unknown' rule_id."""
        line = json.dumps({
            "reason": "compiler-message",
            "message": {
                "level": "warning",
                "message": "aborting",
                "code": None,
                "spans": [{
                    "file_name": "src/x.rs",
                    "line_start": 1,
                    "line_end": 1,
                    "column_start": 1,
                }],
            },
        })
        findings = parse_clippy(line)
        assert len(findings) == 1
        assert findings[0].rule_id == "unknown"

    def test_empty_input(self):
        assert parse_clippy("") == []

    def test_malformed_json(self):
        result = parse_clippy("totally broken json\nmore garbage")
        assert len(result) == 1
        assert isinstance(result[0], ToolError)
        assert result[0].tool_name == "clippy"


# -- checkpatch --------------------------------------------------------

class TestParseCheckpatch:
    """Tests for parse_checkpatch()."""

    def test_valid_output(self):
        raw = (FIXTURES / "checkpatch_output.txt").read_text()
        findings = parse_checkpatch(raw)
        assert len(findings) == 2
        f0 = findings[0]
        assert isinstance(f0, Finding)
        assert f0.file == "file.c"
        assert f0.line == 10
        assert f0.end_line == 10
        assert f0.column == 0
        assert f0.rule_id == "LONG_LINE"
        assert f0.level == "warning"
        assert f0.tool_name == "checkpatch"
        f1 = findings[1]
        assert f1.file == "file.c"
        assert f1.line == 25
        assert f1.rule_id == "SPACING"
        assert f1.level == "error"

    def test_empty_input(self):
        assert parse_checkpatch("") == []

    def test_malformed_input(self):
        result = parse_checkpatch(
            "random gibberish\nno match here\n",
            exit_code=5,
        )
        assert len(result) == 1
        assert isinstance(result[0], ToolError)
        assert result[0].tool_name == "checkpatch"
        assert result[0].exit_code == 5

    def test_summary_only_not_error(self):
        """Output with only a summary line is not malformed."""
        result = parse_checkpatch(
            "total: 0 errors, 0 warnings, 10 lines checked\n"
        )
        assert result == []


# -- non_ascii ---------------------------------------------------------

class TestParseNonAscii:
    """Tests for parse_non_ascii()."""

    def test_valid_output(self):
        raw = "src/app.py:42:x = 'hello \xe2\x80\x93 world'\n"
        findings = parse_non_ascii(raw)
        assert len(findings) == 1
        f0 = findings[0]
        assert isinstance(f0, Finding)
        assert f0.file == "src/app.py"
        assert f0.line == 42
        assert f0.rule_id == "NON_ASCII"
        assert f0.level == "error"
        assert f0.tool_name == "non_ascii"
        assert "non-ASCII" in f0.message

    def test_multiple_findings(self):
        raw = "a.py:1:foo\xc2\xa0bar\nb.py:5:baz\xe2\x80\x94qux\n"
        findings = parse_non_ascii(raw)
        assert len(findings) == 2
        assert findings[0].file == "a.py"
        assert findings[1].file == "b.py"
        assert findings[1].line == 5

    def test_empty_input(self):
        assert parse_non_ascii("") == []

    def test_malformed_input(self):
        """Input with no matching lines is not an error -- grep
        simply found nothing parseable. But non-empty + zero matches
        could indicate a problem. The current parser treats no-match
        lines as valid empty output."""
        # grep -Pn output always matches the pattern, so non-matching
        # lines are filtered out silently.
        result = parse_non_ascii("just random text with no colons\n")
        # Non-matching lines are skipped, result is empty
        assert result == []


# -- dispatch ----------------------------------------------------------

class TestParserDispatch:
    """Tests for PARSER_DISPATCH and parse_output()."""

    def test_dispatch_keys(self):
        expected = {
            "shellcheck_json",
            "sarif",
            "clippy_json",
            "checkpatch_emacs",
            "grep_line",
            "flake8",
            "pylint_json",
            "eslint_json",
        }
        assert set(PARSER_DISPATCH.keys()) == expected

    def test_dispatch_shellcheck(self):
        raw = (FIXTURES / "shellcheck_output.json").read_text()
        findings = parse_output(raw, "shellcheck_json", "shellcheck")
        assert len(findings) == 2
        assert all(isinstance(f, Finding) for f in findings)

    def test_dispatch_sarif_ruff(self):
        raw = (FIXTURES / "ruff_sarif.json").read_text()
        findings = parse_output(raw, "sarif", "ruff")
        assert len(findings) == 2
        assert all(f.tool_name == "ruff" for f in findings)

    def test_dispatch_sarif_semgrep(self):
        raw = (FIXTURES / "semgrep_sarif.json").read_text()
        findings = parse_output(raw, "sarif", "semgrep")
        assert len(findings) == 1
        assert findings[0].tool_name == "semgrep"

    def test_dispatch_empty_clean(self):
        result = parse_output("", "sarif", "ruff")
        assert result == []

    def test_dispatch_corrupt(self):
        result = parse_output("corrupt garbage", "sarif", "ruff")
        assert len(result) == 1
        assert isinstance(result[0], ToolError)

    def test_dispatch_flake8(self):
        raw = "src/foo.py:10:1: E501 line too long\n"
        findings = parse_output(raw, "flake8", "flake8")
        assert len(findings) == 1
        assert isinstance(findings[0], Finding)
        assert findings[0].tool_name == "flake8"

    def test_dispatch_pylint(self):
        raw = json.dumps([{
            "type": "warning",
            "path": "a.py",
            "line": 1,
            "column": 0,
            "endLine": 1,
            "endColumn": 2,
            "symbol": "x",
            "message": "m",
            "message-id": "W0001",
        }])
        findings = parse_output(raw, "pylint_json", "pylint")
        assert len(findings) == 1
        assert isinstance(findings[0], Finding)
        assert findings[0].tool_name == "pylint"
        assert findings[0].rule_id == "W0001"
        assert findings[0].level == "warning"

    def test_dispatch_unknown_format(self):
        with pytest.raises(ValueError, match="unknown_format"):
            parse_output("data", "unknown_format", "x")


# -- flake8 ---------------------------------------------------------------

class TestParseFlake8:
    """Tests for parse_flake8()."""

    def test_valid_output(self):
        raw = (
            "src/foo.py:10:1: E501 line too long\n"
            "src/bar.py:3:5: F401 'os' imported but unused\n"
        )
        findings = parse_flake8(raw)
        assert len(findings) == 2
        f0 = findings[0]
        assert isinstance(f0, Finding)
        assert f0.file == "src/foo.py"
        assert f0.line == 10
        assert f0.end_line == 10
        assert f0.column == 1
        assert f0.rule_id == "E501"
        assert f0.level == "warning"
        assert f0.message == "line too long"
        assert f0.tool_name == "flake8"
        f1 = findings[1]
        assert f1.file == "src/bar.py"
        assert f1.line == 3
        assert f1.column == 5
        assert f1.rule_id == "F401"

    def test_empty_input(self):
        assert parse_flake8("") == []
        assert parse_flake8("   ") == []

    def test_malformed_input(self):
        result = parse_flake8(
            "random gibberish\nno colons here\n",
            exit_code=5,
        )
        assert len(result) == 1
        assert isinstance(result[0], ToolError)
        assert result[0].tool_name == "flake8"
        assert result[0].exit_code == 5

    def test_multiple_findings_same_file(self):
        raw = (
            "src/x.py:1:1: E302 expected 2 blank lines\n"
            "src/x.py:5:3: W291 trailing whitespace\n"
        )
        findings = parse_flake8(raw)
        assert len(findings) == 2
        assert findings[0].line == 1
        assert findings[1].line == 5

    def test_message_with_colon(self):
        raw = (
            "src/x.py:7:2: E712 comparison to True should be "
            "'if cond is True:' or 'if cond:'\n"
        )
        findings = parse_flake8(raw)
        assert len(findings) == 1
        assert ":" in findings[0].message
        assert findings[0].rule_id == "E712"


# -- pylint ----------------------------------------------------------------

class TestParsePylint:
    """Tests for parse_pylint()."""

    def test_valid_output(self):
        data = [
            {
                "type": "warning",
                "path": "src/foo.py",
                "line": 10,
                "column": 4,
                "endLine": 10,
                "endColumn": 9,
                "symbol": "unused-import",
                "message": "Unused import os",
                "message-id": "W0611",
            },
            {
                "type": "convention",
                "path": "src/bar.py",
                "line": 1,
                "column": 0,
                "endLine": None,
                "endColumn": None,
                "symbol": "missing-module-docstring",
                "message": "Missing module docstring",
                "message-id": "C0114",
            },
        ]
        findings = parse_pylint(json.dumps(data))
        assert len(findings) == 2
        f0 = findings[0]
        assert isinstance(f0, Finding)
        assert f0.file == "src/foo.py"
        assert f0.line == 10
        assert f0.end_line == 10
        assert f0.column == 4
        assert f0.rule_id == "W0611"
        assert f0.level == "warning"
        assert f0.message == "Unused import os"
        assert f0.tool_name == "pylint"
        f1 = findings[1]
        assert f1.file == "src/bar.py"
        assert f1.line == 1
        assert f1.end_line == 1  # endLine was null -> falls back to line
        assert f1.column == 0
        assert f1.rule_id == "C0114"
        assert f1.level == "note"  # "convention" maps to "note"

    def test_level_mapping(self):
        """All 6 pylint types map to the correct Finding level."""
        mapping = {
            "fatal": "error",
            "error": "error",
            "warning": "warning",
            "convention": "note",
            "refactor": "note",
            "information": "note",
        }
        for pylint_type, expected_level in mapping.items():
            data = [
                {
                    "type": pylint_type,
                    "path": "a.py",
                    "line": 1,
                    "column": 0,
                    "endLine": 1,
                    "endColumn": 1,
                    "symbol": "test",
                    "message": "test",
                    "message-id": "X0001",
                },
            ]
            findings = parse_pylint(json.dumps(data))
            assert len(findings) == 1, (
                "type=%s should produce 1 finding" % pylint_type
            )
            assert findings[0].level == expected_level, (
                "type=%s should map to level=%s, got %s"
                % (pylint_type, expected_level, findings[0].level)
            )

    def test_endline_null_fallback(self):
        data = [
            {
                "type": "warning",
                "path": "a.py",
                "line": 5,
                "column": 0,
                "endLine": None,
                "endColumn": None,
                "symbol": "test",
                "message": "test",
                "message-id": "W0001",
            },
        ]
        findings = parse_pylint(json.dumps(data))
        assert len(findings) == 1
        assert findings[0].end_line == findings[0].line

    def test_empty_input(self):
        assert parse_pylint("") == []
        assert parse_pylint("   ") == []
        # pylint emits "[]" for a clean file
        assert parse_pylint("[]") == []

    def test_malformed_input(self):
        result = parse_pylint("not json at all", exit_code=5)
        assert len(result) == 1
        assert isinstance(result[0], ToolError)
        assert result[0].tool_name == "pylint"
        assert result[0].exit_code == 5
