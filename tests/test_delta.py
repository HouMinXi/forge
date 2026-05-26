# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Unit tests for the delta computation module.

Tests use constructed Finding objects and changed_lines dicts
directly -- no fixture files needed (pure data filtering).
"""

from forge.delta import filter_delta
from forge.parsers.base import Finding, ToolError


def _finding(
    file="src/main.sh",
    line=10,
    end_line=None,
    column=1,
    rule_id="SC2086",
    level="warning",
    message="Double quote to prevent globbing",
    tool_name="shellcheck",
    fix=None,
):
    """Helper to build a Finding with sane defaults."""
    return Finding(
        file=file,
        line=line,
        end_line=end_line if end_line is not None else line,
        column=column,
        rule_id=rule_id,
        level=level,
        message=message,
        tool_name=tool_name,
        fix=fix,
    )


class TestFilterDelta:
    """Tests for filter_delta -- core of baseline mode."""

    def test_keeps_finding_on_changed_line(self):
        finding = _finding(file="a.sh", line=5)
        changed = {"a.sh": {5, 6, 7}}
        delta, all_f = filter_delta([finding], changed)
        assert len(delta) == 1
        assert delta[0] is finding

    def test_removes_finding_file_not_in_changed(self):
        finding = _finding(file="b.sh", line=5)
        changed = {"a.sh": {5, 6, 7}}
        delta, all_f = filter_delta([finding], changed)
        assert len(delta) == 0

    def test_removes_finding_line_not_changed(self):
        """Pre-existing violation on unchanged line should be filtered."""
        finding = _finding(file="a.sh", line=100)
        changed = {"a.sh": {5, 6, 7}}
        delta, all_f = filter_delta([finding], changed)
        assert len(delta) == 0

    def test_multiline_finding_any_line_intersects(self):
        """Multi-line finding: keep if ANY line in range intersects."""
        finding = _finding(file="a.sh", line=8, end_line=12)
        changed = {"a.sh": {10}}
        delta, _ = filter_delta([finding], changed)
        assert len(delta) == 1

    def test_multiline_finding_no_intersection(self):
        """Multi-line finding: remove if NO line in range intersects."""
        finding = _finding(file="a.sh", line=8, end_line=12)
        changed = {"a.sh": {5, 6, 7}}
        delta, _ = filter_delta([finding], changed)
        assert len(delta) == 0

    def test_empty_findings(self):
        delta, all_f = filter_delta([], {"a.sh": {1, 2}})
        assert delta == []
        assert all_f == []

    def test_empty_changed_lines(self):
        findings = [_finding(file="a.sh", line=5)]
        delta, all_f = filter_delta(findings, {})
        assert delta == []
        assert len(all_f) == 1

    def test_preserves_all_findings(self):
        """Mimo: all_findings must be preserved for reporter."""
        f1 = _finding(file="a.sh", line=5)
        f2 = _finding(file="a.sh", line=100)
        changed = {"a.sh": {5}}
        delta, all_f = filter_delta([f1, f2], changed)
        assert len(delta) == 1
        assert len(all_f) == 2
        # all_findings is a separate copy
        assert all_f is not [f1, f2]

    def test_tool_error_passes_through(self):
        """ToolError items pass through unchanged (not filtered by line)."""
        err = ToolError(
            tool_name="shellcheck",
            exit_code=2,
            stderr="parse error",
            message="shellcheck crashed",
        )
        finding = _finding(file="a.sh", line=100)
        changed = {"a.sh": {5}}
        delta, all_f = filter_delta([err, finding], changed)
        # ToolError should be in delta, Finding on line 100 should not
        assert len(delta) == 1
        assert isinstance(delta[0], ToolError)
        assert len(all_f) == 2

    def test_deterministic_same_inputs_same_outputs(self):
        """GATE-02: same inputs must produce same outputs."""
        findings = [
            _finding(file="a.sh", line=5),
            _finding(file="a.sh", line=10),
            _finding(file="b.sh", line=1),
        ]
        changed = {"a.sh": {5, 10}}
        for _ in range(10):
            delta, all_f = filter_delta(findings, changed)
            assert len(delta) == 2
            assert len(all_f) == 3
            assert delta[0].line == 5
            assert delta[1].line == 10

    def test_multiple_files_mixed(self):
        """Multiple files with mixed findings."""
        findings = [
            _finding(file="a.sh", line=5),
            _finding(file="a.sh", line=100),
            _finding(file="b.py", line=3),
            _finding(file="c.rs", line=1),
        ]
        changed = {"a.sh": {5}, "b.py": {3, 4}}
        delta, all_f = filter_delta(findings, changed)
        assert len(delta) == 2
        assert delta[0].file == "a.sh" and delta[0].line == 5
        assert delta[1].file == "b.py" and delta[1].line == 3
        assert len(all_f) == 4

    def test_single_line_finding_boundary(self):
        """line == end_line, line is in changed set."""
        finding = _finding(file="a.sh", line=5, end_line=5)
        changed = {"a.sh": {5}}
        delta, _ = filter_delta([finding], changed)
        assert len(delta) == 1
