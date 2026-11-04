# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Cargo's explicit failed completion is independent of diagnostic locations."""

import json

import pytest

from code_forge.parsers import parse_output
from code_forge.parsers.base import Finding, ToolError
from code_forge.parsers.clippy import parse_clippy


def _diagnostic(level="warning", line=3, *, target="fixture", file="src/lib.rs"):
    return {
        "reason": "compiler-message",
        "target": {"name": target},
        "message": {
            "level": level,
            "message": f"{target} {level} diagnostic",
            "code": {"code": "clippy::fixture" if level == "warning" else "E0308"},
            "spans": []
            if line is None
            else [
                {
                    "file_name": file,
                    "line_start": line,
                    "line_end": line,
                    "column_start": 1,
                    "is_primary": True,
                }
            ],
        },
    }


def _output(*records):
    return "\n".join(json.dumps(record) for record in records)


@pytest.mark.parametrize("exit_code", [0, 1, 101, -9])
def test_explicit_failed_build_is_a_tool_error_even_without_diagnostics(exit_code):
    parsed = parse_clippy(_output({"reason": "build-finished", "success": False}), exit_code=exit_code)
    assert len(parsed) == 1 and isinstance(parsed[0], ToolError)
    assert parsed[0].tool_name == "clippy"
    assert parsed[0].exit_code == exit_code
    assert parsed[0].stderr == ""
    assert "unsuccessful build" in parsed[0].message
    assert "cause unknown" in parsed[0].message


@pytest.mark.parametrize("level", ["warning", "error"])
@pytest.mark.parametrize("line", [None, 1, 3], ids=["spanless", "preexisting", "new"])
def test_failed_completion_retains_every_located_diagnostic(level, line):
    parsed = parse_clippy(
        _output(_diagnostic(level, line), {"reason": "build-finished", "success": False}),
        exit_code=101,
    )
    findings = [item for item in parsed if isinstance(item, Finding)]
    errors = [item for item in parsed if isinstance(item, ToolError)]
    assert len(errors) == 1 and "unsuccessful build" in errors[0].message
    assert [(item.level, item.line, item.message) for item in findings] == (
        [] if line is None else [(level, line, f"fixture {level} diagnostic")]
    )


@pytest.mark.parametrize(
    "statuses", [[False, True], [True, False], [False, False], [False, True, False]]
)
def test_failed_completion_is_monotonic_and_emitted_once_for_an_alias(statuses):
    records = [_diagnostic("error", 1, target="library")]
    records.extend({"reason": "build-finished", "success": status} for status in statuses)
    records.append(_diagnostic("warning", 3, target="binary"))
    parsed = parse_output(_output(*records), "clippy_json", "rust-quality", 101)
    assert [item.message for item in parsed if isinstance(item, Finding)] == [
        "library error diagnostic",
        "binary warning diagnostic",
    ]
    errors = [item for item in parsed if isinstance(item, ToolError)]
    assert len(errors) == 1
    assert errors[0].tool_name == "rust-quality"
    assert errors[0].exit_code == 101


@pytest.mark.parametrize(
    "record",
    [
        {"reason": "build-finished", "success": True},
        {"reason": "build-finished", "success": None},
        {"reason": "build-finished", "success": 0},
        {"reason": "build-finished", "success": "false"},
        {"reason": "build-finished"},
        {"reason": "build-script-executed", "success": False},
        {"success": False},
    ],
    ids=["true", "null", "zero", "string", "missing-success", "other-reason", "missing-reason"],
)
def test_only_the_explicit_boolean_failed_completion_introduces_failure(record):
    parsed = parse_clippy(_output(_diagnostic(), record), exit_code=101)
    assert len(parsed) == 1 and isinstance(parsed[0], Finding)


@pytest.mark.parametrize("exit_code", [0, 101])
@pytest.mark.parametrize("level", ["warning", "error"])
@pytest.mark.parametrize("line", [None, 1, 3], ids=["spanless", "preexisting", "new"])
def test_legacy_no_completion_marker_keeps_existing_diagnostic_policy(exit_code, level, line):
    parsed = parse_clippy(_output(_diagnostic(level, line)), exit_code=exit_code)
    assert not any(isinstance(item, ToolError) for item in parsed)
    assert len(parsed) == (line is not None)


@pytest.mark.parametrize("exit_code", [0, 101])
def test_empty_legacy_output_does_not_infer_failure_from_exit_code(exit_code):
    assert parse_clippy("", exit_code=exit_code) == []
