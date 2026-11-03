# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""L0 failures must survive real L1 receipts, CI persistence and CLI reporting.

Only analyzer availability/process output and the model transport are synthetic.
Detection, registry, parsing, orchestration, L1 provider, receipt writer/verifier
and canonical CLI reporting run normally. No native analyzer or live model runs.
"""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace

import pytest

from code_forge.autofix import StubAutoFixer
from code_forge.baseline import GitRefBaseline, ResolvedReview, resolve_baseline
from code_forge.cli import _emit_ci_output
from code_forge.detect import detect_toolchain, generate_tools_yaml
from code_forge.exit_codes import verdict_to_exit
from code_forge.factories import build_l1_provider
from code_forge.falsify import StubFalsifier
from code_forge.llm_invoke import LLMResult, Usage
from code_forge.machine import StateMachine, _default_l0_runner
from code_forge.mutation import mutation_source_guard_matches
from code_forge.mutation_findings import mutation_review_context
from code_forge.registry import ToolConfig, load_registry
from code_forge.source import compute_source_hash
from code_forge.state import Mode, Verdict
from code_forge.verify import run_verify


_DIFF = (
    "diff --git a/script.sh b/script.sh\n--- a/script.sh\n+++ b/script.sh\n"
    "@@ -3 +3 @@\n-echo old\n+echo $value\n"
)


def _output(line=None):
    return json.dumps(
        []
        if line is None
        else [
            {
                "file": "script.sh",
                "line": line,
                "endLine": line,
                "column": 6,
                "code": 2086,
                "level": "info",
                "message": "Double quote to prevent globbing and word splitting.",
            }
        ]
    )


class _Review:
    def __init__(self, tmp_path, monkeypatch, *, mode=Mode.CI, python_file=False):
        self.root = tmp_path / "checkout"
        self.root.mkdir()
        (self.root / "script.sh").write_text("#!/bin/sh\necho old\necho $value\n")
        config = tmp_path / "config"
        (config / ".code-forge").mkdir(parents=True)
        (config / ".code-forge/gate.yaml").write_text("{}\n")
        detected = detect_toolchain(
            self.root,
            which_fn=lambda name: "/mock/shellcheck" if name == "shellcheck" else None,
        )
        assert detected.detected == ["shellcheck"]
        registry_path = self.root / ".code-forge/tools.yaml"
        generate_tools_yaml(detected, registry_path)
        self.registry = load_registry(str(registry_path))
        assert self.registry["shellcheck"].output_format == "shellcheck_json"
        self.exit_code = 0
        self.payload = "[]"
        self.stderr = ""
        self.error = None
        self.available = True
        self.calls = []
        self.llm_calls = []
        self.verifications = []
        self.tool_files = ["script.sh", "missing.sh"]
        files = [Path("script.sh"), Path("missing.sh")]
        diff = _DIFF
        self.excerpts = [
            {"file": "script.sh", "start_line": 3, "end_line": 3, "content": "echo $value\n"}
        ]
        if python_file:
            (self.root / "sample.py").write_text("value = 2\n")
            files.append(Path("sample.py"))
            diff += (
                "diff --git a/sample.py b/sample.py\n--- a/sample.py\n+++ b/sample.py\n"
                "@@ -1 +1 @@\n-value = 1\n+value = 2\n"
            )
            self.excerpts.append(
                {"file": "sample.py", "start_line": 1, "end_line": 1, "content": "value = 2\n"}
            )
        self.machine = StateMachine(
            mode=mode,
            falsifier=StubFalsifier(),
            autofixer=StubAutoFixer(),
            revert_fn=lambda _: None,
            resolved_review=ResolvedReview(files, None, diff, "git"),
            source_hash=compute_source_hash(git_diff=diff),
            baseline_spec_repr="fixture",
            cwd=self.root,
            test_config_root=config,
            registry=self.registry,
            coverage_l1_active=True,
        )
        monkeypatch.setattr("code_forge.runner._resolve_command", self.resolve)
        # Do not patch the shared subprocess module: real internal read-only
        # git and receipt operations must retain their normal implementation.
        monkeypatch.setattr(
            "code_forge.runner.subprocess",
            SimpleNamespace(run=self.completed, TimeoutExpired=subprocess.TimeoutExpired),
        )
        monkeypatch.setattr("code_forge.llm_invoke.llm_invoke", self.invoke)
        monkeypatch.setattr("code_forge.verify.run_verify", self.verify)
        self.machine.l1_provider = build_l1_provider(
            "auto", self.machine.resolved_review, backend=None, max_attempts=1
        )
        assert self.machine.l0_runner is _default_l0_runner
        assert not self.machine.l1_provider.is_stub_l1

    def resolve(self, command, **kwargs):
        command = command.split()[0]
        if command == "shellcheck" and not self.available:
            return None
        assert command in ("shellcheck", "sibling")
        return "/mock/" + command

    def completed(self, argv, **kwargs):
        self.calls.append(argv)
        assert argv[0] in ("/mock/shellcheck", "/mock/sibling")
        if "--version" in argv:
            return subprocess.CompletedProcess(argv, 0, "fixture version", "")
        assert kwargs["cwd"] == self.root
        if argv[0] == "/mock/sibling":
            return subprocess.CompletedProcess(argv, 1, "script.sh:3:1: F401 sibling diagnostic", "")
        assert argv == ["/mock/shellcheck", "-f", "json", *self.tool_files]
        if self.error is not None:
            raise self.error
        return subprocess.CompletedProcess(argv, self.exit_code, self.payload, self.stderr)

    def invoke(self, prompt, **kwargs):
        self.llm_calls.append(prompt)
        return LLMResult(
            {"findings": [], "code_excerpts": [dict(item) for item in self.excerpts]}, Usage(), 0.0
        )

    def verify(self, *args, **kwargs):
        result = run_verify(*args, **kwargs)
        self.verifications.append(result)
        return result

    def run(self, expected, *, infra=None):
        before_llm = len(self.llm_calls)
        before_verify = len(self.verifications)
        verdict = self.machine.run()
        state_path = self.root / ".code-forge/state.json"
        disk = json.loads(state_path.read_text())
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            _emit_ci_output(state_path, self.registry)
        assert verdict == expected
        assert self.machine._state.verdict == expected
        assert disk["verdict"] == expected.value
        assert disk["converged"] is (expected == Verdict.PASS)
        assert verdict_to_exit(verdict) == (0 if expected == Verdict.PASS else 1)
        assert expected.value in stderr.getvalue()
        assert json.loads(stdout.getvalue())["version"] == "2.1.0"
        current_verifications = self.verifications[before_verify:]
        if self.machine.l1_provider.is_stub_l1:
            assert len(self.llm_calls) == before_llm
            assert current_verifications == []
        else:
            assert len(self.llm_calls) - before_llm >= 3
            assert current_verifications, "real terminal receipt verifier must run"
            assert all(result.passed for result in current_verifications)
        receipts = list((self.root / ".code-forge/receipts").glob("receipt-*.json"))
        assert len(receipts) >= 3
        assert all(
            json.loads(path.read_text())["diff_sha256"] == self.machine.source_hash for path in receipts
        )
        if infra is not None:
            assert any(infra in error for error in disk["infra_errors"])
            if expected == Verdict.FAIL:
                assert "code-forge: infra: " in stderr.getvalue()
                assert infra in stderr.getvalue()
        if expected == Verdict.PASS:
            assert "code-forge: infra: " not in stderr.getvalue()
        return disk


@pytest.fixture
def review(tmp_path, monkeypatch):
    return _Review(tmp_path, monkeypatch)


@pytest.mark.parametrize(
    ("exit_code", "line", "expected"),
    [(0, None, Verdict.PASS), (1, 1, Verdict.PASS), (1, 3, Verdict.FAIL)],
    ids=["clean", "normal-preexisting", "normal-new"],
)
def test_normal_shellcheck_status_retains_delta_behavior(review, exit_code, line, expected):
    review.exit_code, review.payload = exit_code, _output(line)
    disk = review.run(expected)
    assert disk["infra_errors"] == []
    assert not review.machine._l0_failed
    assert len([finding for finding in disk["findings"] if finding["source"] == "L0"]) == (line == 3)
    assert len([item for item in review.machine._advisories if item.axis == "pre-existing-l0"]) == (
        line == 1
    )


@pytest.mark.parametrize(
    ("exit_code", "line"),
    [(2, None), (2, 1), (2, 3), (3, None), (4, 1), (-9, None), (127, 3)],
    ids=["empty-2", "preexisting-2", "new-2", "empty-3", "partial-4", "signal", "unknown"],
)
def test_abnormal_shellcheck_status_fails_with_valid_l1_receipts(review, exit_code, line):
    review.exit_code, review.payload = exit_code, _output(line)
    review.stderr = "missing.sh: file not found"
    disk = review.run(Verdict.FAIL, infra=f"shellcheck exited abnormally ({exit_code})")
    assert review.machine._l0_failed
    assert len(disk["infra_errors"]) == 1
    assert review.stderr in disk["infra_errors"][0]
    assert len([finding for finding in disk["findings"] if finding["source"] == "L0"]) == (line == 3)
    assert len([item for item in review.machine._advisories if item.axis == "pre-existing-l0"]) == (
        line == 1
    )


@pytest.mark.parametrize("exit_code", [0, 2])
def test_malformed_output_is_not_a_clean_l0_run(review, exit_code):
    review.exit_code, review.payload = exit_code, "not json"
    review.run(Verdict.FAIL, infra="Failed to parse shellcheck JSON output")
    assert review.machine._l0_failed


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (subprocess.TimeoutExpired("shellcheck", 30), "tool shellcheck: timed out"),
        (OSError("synthetic launch failure"), "tool shellcheck: OS error: synthetic launch failure"),
        (RuntimeError("unexpected transport failure"), "L0 runner failed: unexpected transport failure"),
    ],
    ids=["timeout", "os-error", "unexpected-runner-exception"],
)
def test_execution_errors_fail_ci_even_for_optional_tool(review, error, message):
    assert not review.registry["shellcheck"].required
    review.error = error
    review.run(Verdict.FAIL, infra=message)
    assert review.machine._l0_failed


def test_unexpected_parser_exception_fails_ci(review):
    # json.loads raises RecursionError before the parser can return a ToolError.
    # Inject only producer output, never a replacement parser or L0 runner.
    review.payload = "[" * 10000 + "]" * 10000
    disk = review.run(Verdict.FAIL, infra="L0 runner failed:")
    assert "recursion" in disk["infra_errors"][0].lower()
    assert review.machine._l0_failed


@pytest.mark.parametrize("required", [False, True], ids=["optional", "required"])
def test_missing_tool_keeps_required_optional_distinction(review, required):
    review.registry["shellcheck"].required = required
    review.available = False
    disk = review.run(
        Verdict.FAIL if required else Verdict.PASS,
        infra="Required tool not found: shellcheck" if required else None,
    )
    assert review.machine._l0_failed is required
    assert bool(disk["infra_errors"]) is required
    assert review.calls == []


def test_no_matching_tool_remains_a_skip_with_real_l1_coverage(review):
    review.registry["shellcheck"].file_patterns = ["*.unmatched"]
    review.error = AssertionError("nonmatching tool must never run")
    disk = review.run(Verdict.PASS)
    assert disk["infra_errors"] == []
    assert all("--version" in call for call in review.calls)
    assert not review.machine._l0_failed


@pytest.mark.parametrize("timeout", [False, True], ids=["partial-abnormal", "timeout"])
def test_healthy_sibling_diagnostics_survive_failed_tool(review, timeout):
    review.registry["sibling"] = ToolConfig("sibling", "sibling", [], "flake8", ["*.sh"])
    review.exit_code, review.payload = 2, _output(1)
    if timeout:
        review.error = subprocess.TimeoutExpired("shellcheck", 30)
    disk = review.run(Verdict.FAIL, infra="timed out" if timeout else "exited abnormally")
    findings = [finding for finding in disk["findings"] if finding["source"] == "L0"]
    assert [finding["description"] for finding in findings] == ["sibling diagnostic"]
    assert len([item for item in review.machine._advisories if item.axis == "pre-existing-l0"]) == (
        not timeout
    )


def test_same_machine_failure_then_clean_run_resets_current_run_failure(review):
    """Isolate run-state reset from the independent multi-run receipt window.

    Reusing real L1 currently accumulates CI receipt cycles and fails the
    one-cycle verifier. Only this lifecycle case uses the supported stub-L1
    setup; the failure matrix above exercises real L1 and receipt acceptance.
    """
    review.machine.l1_provider = build_l1_provider("stub", review.machine.resolved_review)
    review.machine.coverage_l1_active = False
    review.exit_code = 2
    review.run(Verdict.FAIL, infra="exited abnormally")
    assert review.machine._l0_failed
    review.exit_code = 0
    review.payload = "[]"
    disk = review.run(Verdict.PASS)
    assert not review.machine._l0_failed
    assert disk["infra_errors"] == []
    assert len(list((review.root / ".code-forge/receipts").glob("receipt-*.json"))) == 6


def test_old_or_unrelated_infra_notes_do_not_create_current_l0_failure(review):
    notes = ["L0 ToolError tool=old: stale diagnostic", "Danger-score: unrelated advisory"]
    review.machine._state.infra_errors.extend(notes)
    disk = review.run(Verdict.PASS)
    assert disk["infra_errors"] == notes
    assert not review.machine._l0_failed


def test_l0_failure_does_not_launch_detached_mutation(tmp_path, monkeypatch):
    review = _Review(tmp_path, monkeypatch, python_file=True)
    review.exit_code = 2
    # Availability is synthetic; the tripwire must never spawn any workload.
    monkeypatch.setattr("shutil.which", lambda name: "/mock/mutmut" if name == "mutmut" else None)
    launched = []

    def forbidden_launch(*args, **kwargs):
        launched.append(args)
        raise AssertionError("L0 failure must prevent detached mutation launch")

    monkeypatch.setattr("code_forge.machine.launch_detached_mutation", forbidden_launch)
    disk = review.run(Verdict.FAIL, infra="exited abnormally")
    assert not launched
    assert "CI: detached mutation not launched: verdict is already FAIL" in disk["infra_errors"]


def test_local_mode_keeps_existing_receipt_policy(tmp_path, monkeypatch):
    review = _Review(tmp_path, monkeypatch, mode=Mode.LOCAL)
    review.exit_code = 2
    review.run(Verdict.PASS, infra="exited abnormally")
    assert review.machine._l0_failed


def _bind_working_review(review):
    """Give the bound marker a real source guard, not a fabricated acceptance."""

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=review.root, check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-q")
    (review.root / "script.sh").write_text("#!/bin/sh\necho old\necho old\n")
    git("add", "script.sh")
    git(
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "commit.gpgSign=false",
        "commit",
        "-qm",
        "fixture baseline",
    )
    (review.root / "script.sh").write_text("#!/bin/sh\necho old\necho $value\n")
    resolved = resolve_baseline(
        GitRefBaseline("HEAD"), GitRefBaseline("WORKING"), [Path("script.sh")], review.root
    )
    review.tool_files = ["script.sh"]
    review.machine.resolved_review = resolved
    review.machine.source_hash = compute_source_hash(git_diff=resolved.git_diff)
    review.machine.l1_provider = build_l1_provider("auto", resolved, backend=None, max_attempts=1)
    context = mutation_review_context(
        source_root=review.root,
        base_sha=resolved.base_sha,
        head_sha=resolved.head_sha,
        git_diff=resolved.git_diff,
    )
    assert context is not None
    return context


@pytest.mark.parametrize("marker", ["bound", "legacy-fresh-no-pid", "legacy-live-pid"])
@pytest.mark.parametrize("l0_failed", [False, True], ids=["healthy-control", "l0-failure"])
def test_running_mutation_marker_cannot_hide_current_l0_failure(review, marker, l0_failed):
    data = {"status": "running", "pid": os.getpid(), "started_at": time.time()}
    if marker == "bound":
        data.update(
            survivor_details_version=1,
            review_context=_bind_working_review(review),
            infra_errors=[],
        )
    elif marker == "legacy-fresh-no-pid":
        data["pid"] = None
    marker_path = review.root / ".code-forge/mutation-result.json"
    frozen = json.dumps(data, indent=2).encode("utf-8")
    marker_path.write_bytes(frozen)
    if l0_failed:
        review.exit_code = 2
        review.run(Verdict.FAIL, infra="exited abnormally")
    else:
        # Healthy controls establish that each real marker branch is reachable;
        # PENDING is deliberately not passed to the terminal CLI exit mapper.
        assert review.machine.run() == Verdict.PENDING
        assert len(review.llm_calls) == 3
        assert review.verifications and all(result.passed for result in review.verifications)
    assert review.machine._l0_failed is l0_failed
    assert marker_path.read_bytes() == frozen
    if marker == "bound":
        assert mutation_source_guard_matches(
            review.machine._mutation_source_guard, source_root=review.root
        )
        assert "CI: bound mutation measurement still running" in review.machine._state.infra_errors
