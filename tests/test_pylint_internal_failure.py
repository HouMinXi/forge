# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Pylint's explicit internal failure is independent of diagnostic locations."""

import json

import pytest

from code_forge.parsers import parse_output
from code_forge.parsers.base import Finding, ToolError
from code_forge.parsers.pylint import parse_pylint


def _diagnostic(**fields):
    return {
        "type": "fatal",
        "path": "src/example.py",
        "line": 1,
        "column": 0,
        "endLine": None,
        "message": "AstroidError: unable to build an AST; please report this failure",
        **fields,
    }


@pytest.mark.parametrize("exit_code", [0, 1, 3, -9])
@pytest.mark.parametrize(
    "identity,rule_id",
    [
        ({"message-id": "F0002", "symbol": "astroid-error"}, "F0002"),
        ({"message-id": "F0002"}, "F0002"),
        ({"message-id": "F0002", "symbol": None}, "F0002"),
        ({"message-id": "F0002", "symbol": "other-symbol"}, "F0002"),
        ({"symbol": "astroid-error"}, "astroid-error"),
        ({"message-id": None, "symbol": "astroid-error"}, "astroid-error"),
        ({"message-id": "", "symbol": "astroid-error"}, "astroid-error"),
    ],
    ids=["both", "id-only", "null-symbol", "id-wins", "symbol-only", "null-id", "empty-id"],
)
def test_explicit_internal_failure_keeps_finding_and_adds_tool_error(identity, rule_id, exit_code):
    diagnostic = _diagnostic(**identity)
    parsed = parse_output(json.dumps([diagnostic]), "pylint_json", "python-quality", exit_code)

    assert parsed[0] == Finding(
        file="src/example.py",
        line=1,
        end_line=1,
        column=0,
        rule_id=rule_id,
        level="error",
        message=diagnostic["message"],
        tool_name="python-quality",
    )
    assert len(parsed) == 2 and isinstance(parsed[1], ToolError)
    error = parsed[1]
    assert error.tool_name == "python-quality"
    assert error.exit_code == exit_code
    assert error.stderr == ""
    assert "internal failure" in error.message
    assert rule_id in error.message
    assert diagnostic["message"] in error.message


def test_internal_failure_preserves_partial_findings_before_and_after_it():
    records = [
        _diagnostic(type="warning", line=3, **{"message-id": "W0611", "message": "Unused import os"}),
        _diagnostic(**{"message-id": "F0002"}),
        _diagnostic(type="error", line=8, **{"message-id": "E0602", "message": "Undefined variable x"}),
    ]
    parsed = parse_pylint(json.dumps(records), exit_code=7)
    findings = [item for item in parsed if isinstance(item, Finding)]
    errors = [item for item in parsed if isinstance(item, ToolError)]

    assert [(item.rule_id, item.line, item.message) for item in findings] == [
        (record["message-id"], record["line"], record["message"]) for record in records
    ]
    assert len(errors) == 1
    assert errors[0].tool_name == "pylint"
    assert records[1]["message"] in errors[0].message


@pytest.mark.parametrize(
    "identity",
    [
        {"message-id": "F0001", "symbol": "fatal"},
        {"message-id": "F0010", "symbol": "parse-error"},
        {"message-id": "F0011", "symbol": "config-parse-error"},
        {"message-id": "E0001", "symbol": "syntax-error", "type": "error"},
        {"message-id": "F0001", "symbol": "astroid-error"},
        {"message-id": "astroid-error", "symbol": "astroid-error"},
        {"message-id": "F0002-extra", "symbol": "astroid-error"},
        {"message-id": "f0002", "symbol": "astroid-error"},
        {"message-id": " F0002", "symbol": "astroid-error"},
        {"message-id": " ", "symbol": "astroid-error"},
        {"symbol": "F0002"},
        {"symbol": "Astroid-error"},
        {"symbol": "astroid-error-extra"},
        {"message-id": None, "symbol": None},
        {"message-id": "", "symbol": ""},
        {},
        {"message": "F0002 astroid-error mentioned in unrelated diagnostic text"},
    ],
    ids=[
        "other-fatal",
        "parse-error",
        "config-error",
        "syntax-error",
        "other-id-wins",
        "symbol-is-not-an-id",
        "id-suffix",
        "id-case",
        "id-padding",
        "whitespace-id",
        "id-is-not-a-symbol",
        "symbol-case",
        "symbol-suffix",
        "null-identity",
        "empty-identity",
        "missing-identity",
        "message-is-not-identity",
    ],
)
def test_other_diagnostics_do_not_imply_an_internal_failure(identity):
    parsed = parse_pylint(json.dumps([_diagnostic(**identity)]), exit_code=1)
    assert len(parsed) == 1 and isinstance(parsed[0], Finding)


@pytest.mark.parametrize("exit_code", [0, 1, 3, 32])
@pytest.mark.parametrize("output", ["", "   ", "[]"])
def test_exit_status_alone_does_not_create_an_internal_failure(output, exit_code):
    assert parse_pylint(output, exit_code=exit_code) == []
