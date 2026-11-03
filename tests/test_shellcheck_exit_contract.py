# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""ShellCheck exit status must survive JSON parsing and L0 delta filtering.

Synthetic output follows the tagged producer, not a native ShellCheck run:
https://github.com/koalaman/shellcheck/blob/v0.10.0/shellcheck.hs#L200-L259
https://github.com/koalaman/shellcheck/blob/v0.10.0/src/ShellCheck/Formatter/JSON.hs#L100-L111
"""

import json
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from code_forge.baseline import ResolvedReview
from code_forge.detect import detect_toolchain, generate_tools_yaml
from code_forge.machine import StateMachine
from code_forge.parsers.base import Finding, ToolError
from code_forge.parsers.shellcheck import parse_shellcheck
from code_forge.registry import ToolConfig, load_registry
from code_forge.state import Mode


def _diagnostic(line=3):
    return {
        "file": "script.sh",
        "line": line,
        "endLine": line,
        "column": 6,
        "code": 2086,
        "level": "info",
        "message": "Double quote to prevent globbing and word splitting.",
    }


@pytest.mark.parametrize("exit_code", [2, 3, 4, 127, -9])
@pytest.mark.parametrize("output", ["[]\n", "", json.dumps([_diagnostic()])])
def test_abnormal_exit_retains_diagnostics_and_error(exit_code, output):
    items = parse_shellcheck(output, tool_name="shell-lint", exit_code=exit_code)
    assert [item for item in items if isinstance(item, Finding)] == parse_shellcheck(
        output, tool_name="shell-lint"
    )
    errors = [item for item in items if isinstance(item, ToolError)]
    assert len(errors) == 1
    assert errors[0].tool_name == "shell-lint"
    assert errors[0].exit_code == exit_code
    assert str(exit_code) in errors[0].message


@pytest.mark.parametrize("exit_code", [0, 1])
def test_normal_status_preserves_existing_empty_and_diagnostic_contract(exit_code):
    for output in ("", "[]", json.dumps([_diagnostic()])):
        assert parse_shellcheck(output, exit_code=exit_code) == parse_shellcheck(output)


@pytest.mark.parametrize("output", ["not json", '[{"file": "script.sh"}]'])
def test_malformed_output_keeps_existing_error_contract(output):
    items = parse_shellcheck(output, exit_code=2)
    assert len(items) == 1
    assert isinstance(items[0], ToolError)
    assert items[0].exit_code == 2


@pytest.mark.parametrize("exit_code", [0, 1, 2])
@pytest.mark.parametrize("line", [None, 1, 3])
def test_actual_generated_runner_preserves_infra_and_partial_results(tmp_path, exit_code, line):
    (tmp_path / "script.sh").write_text("#!/bin/sh\necho old\necho $value\n")
    detected = detect_toolchain(
        tmp_path, which_fn=lambda name: "/mock/shellcheck" if name == "shellcheck" else None
    )
    assert detected.detected == ["shellcheck"]
    yaml_path = tmp_path / ".code-forge" / "tools.yaml"
    generate_tools_yaml(detected, yaml_path)
    registry = load_registry(str(yaml_path))
    # Another successful tool's diagnostics must survive ShellCheck failure.
    registry["other"] = ToolConfig("other", "other", [], "flake8", ["*.sh"])
    diff = (
        "diff --git a/script.sh b/script.sh\n--- a/script.sh\n+++ b/script.sh\n"
        "@@ -3 +3 @@\n-echo old\n+echo $value\n"
    )
    machine = StateMachine(
        mode=Mode.CI,
        falsifier=None,
        autofixer=None,
        revert_fn=lambda _: None,
        resolved_review=ResolvedReview([Path("script.sh"), Path("missing.sh")], None, diff, "git"),
        source_hash="fixture",
        baseline_spec_repr="fixture",
        cwd=tmp_path,
        registry=registry,
    )
    payload = json.dumps([] if line is None else [_diagnostic(line)])
    stderr = "missing.sh: openBinaryFile: does not exist" if exit_code == 2 else ""

    def completed(argv, **kwargs):
        assert kwargs["cwd"] == tmp_path
        if "--version" in argv:
            return subprocess.CompletedProcess(argv, 0, "mock version", "")
        if argv[0] == "/mock/other":
            return subprocess.CompletedProcess(argv, 1, "script.sh:3:1: F401 other diagnostic", "")
        return subprocess.CompletedProcess(argv, exit_code, payload, stderr)

    with (
        patch(
            "code_forge.runner._resolve_command",
            side_effect=lambda command, **_: "/mock/" + command.split()[0],
        ),
        patch("code_forge.runner.subprocess.run", side_effect=completed) as launch,
    ):
        findings = machine._run_l0_phase()
    assert [call.args[0] for call in launch.call_args_list] == [
        ["/mock/other", "--version"],
        ["/mock/other", "script.sh", "missing.sh"],
        ["/mock/shellcheck", "--version"],
        ["/mock/shellcheck", "-f", "json", "script.sh", "missing.sh"],
    ]
    assert len(findings) == 1 + (line == 3)
    assert any(f.description == "other diagnostic" for f in findings)
    assert len(machine._preexisting_buf) == (line == 1)
    if exit_code == 2:
        assert len(machine._state.infra_errors) == 1
        assert "shellcheck" in machine._state.infra_errors[0]
        assert "(2)" in machine._state.infra_errors[0]
        assert stderr in machine._state.infra_errors[0]
    else:
        assert machine._state.infra_errors == []
