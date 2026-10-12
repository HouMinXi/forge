# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Keep grep's physical LF records and source content intact.

Source is ASCII-only; escaped characters represent decoded UTF-8 grep output.
Producer subprocess qualification is separate from these offline regressions.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from code_forge.disposition import Disposition
from code_forge.machine import _default_l0_runner
from code_forge.parsers import parse_output
from code_forge.parsers.base import Finding
from code_forge.parsers.non_ascii import parse_non_ascii
from code_forge.registry import ToolConfig


_CONTENTS = [
    pytest.param("\u00a0", id="nbsp-only"),
    pytest.param("\u2003", id="em-space-only"),
    pytest.param("\u0085", id="nel-only"),
    pytest.param("\u2028", id="line-separator-only"),
    pytest.param("\u2029", id="paragraph-separator-only"),
    pytest.param(" \t\u00a0 \t", id="ascii-padding"),
    pytest.param("\u00a0 \t# caf\u00e9 \t\u2003", id="mixed-padding"),
    pytest.param("# caf\u00e9", id="visible-control"),
]


def _finding(filename, line, content, tool_name="non_ascii"):
    return Finding(
        file=filename,
        line=line,
        end_line=line,
        column=0,
        rule_id="NON_ASCII",
        level="error",
        message="non-ASCII character found: " + content,
        tool_name=tool_name,
    )


@pytest.mark.parametrize("content", _CONTENTS)
def test_unicode_whitespace_content_is_preserved(content):
    output = "source.txt:17:" + content + "\n"
    expected = [_finding("source.txt", 17, content)]

    assert parse_non_ascii(output) == expected
    assert parse_output(output, "grep_line", "non_ascii", 0) == expected


@pytest.mark.parametrize(
    "separator",
    ["\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\u0085", "\u2028", "\u2029"],
    ids=["cr", "vt", "ff", "fs", "gs", "rs", "nel", "ls", "ps"],
)
def test_only_lf_separates_adjacent_records(separator):
    content = "left\u00e9" + separator + "right"
    output = "a.txt:2:" + content + "\na.txt:3:\u00a0\nb.txt:11:caf\u00e9\n"

    assert parse_output(output, "grep_line", "non_ascii") == [
        _finding("a.txt", 2, content),
        _finding("a.txt", 3, "\u00a0"),
        _finding("b.txt", 11, "caf\u00e9"),
    ]


@pytest.mark.parametrize("filename", [" leading.txt", "trailing.txt ", " \u00a0 both.txt "])
def test_filename_whitespace_is_significant(filename):
    output = filename + ":9:caf\u00e9\n"

    assert parse_output(output, "grep_line", "non_ascii") == [_finding(filename, 9, "caf\u00e9")]


@pytest.mark.parametrize("terminator", ["", "\n"])
def test_supplied_content_cr_is_not_a_record_delimiter(terminator):
    # This is the parser input boundary. Upstream text-mode capture may already
    # have normalized CRLF; the parser must not strip any remaining source CR.
    content = " \u00e9\r"
    output = "source.txt:4:" + content + terminator

    assert parse_output(output, "grep_line", "custom-grep", 0) == [
        _finding("source.txt", 4, content, tool_name="custom-grep")
    ]


@pytest.mark.parametrize("exit_code", [0, 1, 2])
@pytest.mark.parametrize(
    "output",
    [
        "",
        " \t\r\n\u00a0\u2028",
        "not a record\n",
        "file.txt:no-line:content\n",
        "file.txt:1:\n",
        ":1:content\n",
        "file.txt:-1:content\n",
    ],
)
def test_empty_and_malformed_output_remains_ignored(output, exit_code):
    assert parse_non_ascii(output, exit_code=exit_code) == []
    assert parse_output(output, "grep_line", "non_ascii", exit_code) == []


def test_malformed_records_do_not_hide_valid_neighbors():
    output = "not a record\nsource.txt:1:\nsource.txt:8: \u00a0 \ninvalid:line:text\n"

    assert parse_output(output, "grep_line", "non_ascii", 2) == [_finding("source.txt", 8, " \u00a0 ")]


@pytest.mark.parametrize("content", _CONTENTS)
def test_l0_adapter_retains_captured_grep_content(content, tmp_path):
    registry = {
        "non_ascii": ToolConfig(
            name="non_ascii",
            command="grep",
            args=["-HPn", r"[^\x00-\x7F]"],
            output_format="grep_line",
            file_patterns=["*.txt"],
        )
    }
    # Replay the producer's stdout/exit/stderr boundary, without running tools
    # or replacing the public parser, dispatch table, or L0 adapter.
    capture = ({"non_ascii": ("source.txt:7:" + content + "\n", 0, "")}, {}, [], [])
    with patch("code_forge.runner.run_tools", return_value=capture) as run_tools:
        findings, errors = _default_l0_runner(registry, [Path("source.txt")], cwd=tmp_path)

    run_tools.assert_called_once_with(registry, ["source.txt"], cwd=tmp_path)
    assert errors == []
    assert len(findings) == 1
    finding = findings[0]
    assert finding.file == "source.txt"
    assert finding.line_range == [7, 7]
    assert finding.description == "non-ASCII character found: " + content
    assert finding.source == "L0"
    assert finding.disposition is Disposition.CONFIRMED
