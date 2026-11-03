# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Primary-span routing through the real Clippy/L0 delta consumers.

Payloads are constructed from the rustc JSON contract, not captured native runs:
https://doc.rust-lang.org/rustc/json.html#diagnostics
Clippy result_large_err can label an enum variant before the primary return type.
"""

import json
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from code_forge.baseline import ResolvedReview
from code_forge.machine import StateMachine
from code_forge.parsers.base import Finding, ToolError
from code_forge.parsers.clippy import parse_clippy
from code_forge.registry import ToolConfig
from code_forge.state import Mode


_MISSING = object()


def _span(line, primary=_MISSING, *, file="src/lib.rs", end=None, column=1):
    span = {
        "file_name": file,
        "line_start": line,
        "line_end": end or line,
        "column_start": column,
    }
    if primary is not _MISSING:
        span["is_primary"] = primary
    return span


def _record(spans):
    return json.dumps(
        {
            "reason": "compiler-message",
            "message": {
                "level": "warning",
                "message": "the Err-variant returned from this function is very large",
                "code": {"code": "clippy::result_large_err"},
                "spans": spans,
            },
        }
    )


def _phase(tmp_path, output, changed=("src/lib.rs",)):
    diff = ""
    for name in changed:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("// context\n// context\nfn changed() {}\n", encoding="utf-8")
        diff += (
            f"diff --git a/{name} b/{name}\n--- a/{name}\n+++ b/{name}\n"
            "@@ -3 +3 @@\n-fn old() {}\n+fn changed() {}\n"
        )
    registry = {
        "clippy": ToolConfig(
            "clippy",
            "cargo clippy",
            ["--message-format=json"],
            "clippy_json",
            ["*.rs"],
            working_dir="cargo_root",
        )
    }
    machine = StateMachine(
        mode=Mode.CI,
        falsifier=None,
        autofixer=None,
        revert_fn=lambda _: None,
        resolved_review=ResolvedReview([Path(name) for name in changed], None, diff, "git"),
        source_hash="fixture",
        baseline_spec_repr="fixture",
        cwd=tmp_path,
        registry=registry,
    )

    def completed(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 0, stdout="mock cargo" if "--version" in argv else output, stderr=""
        )

    with (
        patch("code_forge.runner._resolve_command", return_value="/mock/cargo"),
        patch("code_forge.runner.subprocess.run", side_effect=completed) as launch,
    ):
        findings = machine._run_l0_phase()
    assert [call.args[0] for call in launch.call_args_list] == [
        ["/mock/cargo", "--version"],
        ["/mock/cargo", "clippy", "--message-format=json"],
    ]
    return machine, findings


@pytest.mark.parametrize("secondary_first", [True, False])
def test_primary_location_survives_actual_l0_delta(tmp_path, secondary_first):
    spans = [_span(1, False), _span(3, True)]
    if not secondary_first:
        spans.reverse()
    machine, findings = _phase(tmp_path, _record(spans))
    assert [(finding.file, finding.line_range) for finding in findings] == [("src/lib.rs", [3, 3])]
    assert machine._preexisting_buf == []
    assert machine._state.infra_errors == []


def test_later_primary_is_not_lost_when_first_primary_is_unchanged(tmp_path):
    machine, findings = _phase(tmp_path, _record([_span(1, True), _span(3, True)]))
    assert [finding.line_range for finding in findings] == [[3, 3]]
    assert [finding.line_range for finding in machine._preexisting_buf] == [(1, 1)]
    assert machine._state.infra_errors == []


def test_distinct_primary_files_survive_merge_and_exact_duplicates_are_removed(tmp_path):
    spans = [_span(3, True), _span(3, True), _span(3, True, file="src/other.rs")]
    parsed = parse_clippy(_record(spans))
    assert [finding.file for finding in parsed] == ["src/lib.rs", "src/other.rs"]
    machine, findings = _phase(tmp_path, _record(spans), ("src/lib.rs", "src/other.rs"))
    assert len(findings) == 2
    assert len({finding.fingerprint for finding in findings}) == 2
    assert machine._merge_findings(findings, []) == findings
    assert machine._state.infra_errors == []


def test_overlapping_primary_ranges_are_not_collapsed_before_delta(tmp_path):
    spans = [_span(1, True, end=2), _span(1, True, end=3)]
    assert [(finding.line, finding.end_line) for finding in parse_clippy(_record(spans))] == [
        (1, 2),
        (1, 3),
    ]
    machine, findings = _phase(tmp_path, _record(spans))
    assert [finding.line_range for finding in findings] == [[1, 3]]
    assert [finding.line_range for finding in machine._preexisting_buf] == [(1, 2)]
    assert len(machine._merge_findings(findings, [])) == 1


@pytest.mark.parametrize(
    "markers",
    [
        [False],
        [False, False],
        [True, None],
        [True, "true"],
        [True, 1],
        [True, _MISSING],
        [_MISSING, True],
    ],
)
def test_invalid_primary_markers_reach_infrastructure_without_erasing_valid_records(tmp_path, markers):
    invalid = _record([_span(3, marker) for marker in markers])
    parsed = parse_clippy(invalid)
    assert len(parsed) == 1 and isinstance(parsed[0], ToolError)
    assert "primary span" in parsed[0].message
    machine, findings = _phase(tmp_path, invalid + "\n" + _record([_span(3, True)]))
    assert len(findings) == 1
    assert len(machine._state.infra_errors) == 1
    assert "L0 ToolError tool=clippy" in machine._state.infra_errors[0]
    assert "L0 runner failed" not in machine._state.infra_errors[0]
    assert machine._preexisting_buf == []


def test_legacy_unmarked_and_empty_spans_keep_existing_behavior(tmp_path):
    output = _record([_span(3), _span(1)]) + "\n" + _record([])
    parsed = parse_clippy(output)
    assert len(parsed) == 1 and isinstance(parsed[0], Finding)
    assert parsed[0].line == 3
    machine, findings = _phase(tmp_path, output)
    assert [finding.line_range for finding in findings] == [[3, 3]]
    assert machine._state.infra_errors == []
