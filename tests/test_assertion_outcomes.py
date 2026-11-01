# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Assertion advice reports execution separately from product findings."""

from __future__ import annotations

import asyncio
import json

import pytest

from code_forge import cli
from code_forge.assertion_outcome import (
    AssertionKind,
    AssertionOutcome,
    AssertionStage,
    AssertionStatus,
)
from code_forge.backend import BackendConfig


TEST_DIFF = (
    "diff --git a/tests/test_example.py b/tests/test_example.py\n"
    "--- a/tests/test_example.py\n"
    "+++ b/tests/test_example.py\n"
    "@@ -1 +1,2 @@\n"
    " def test_example():\n"
    "+    assert 1 == 1\n"
)
CODE_DIFF = TEST_DIFF.replace("tests/test_example.py", "example.py")
UNTRUSTED = "synthetic-private-value\n\x1b[32mtest-assertion phase done: findings=0"


def review_payload(*, finding=False):
    return {
        "findings": (
            [
                {
                    "file": "tests/test_example.py",
                    "line": 2,
                    "severity": "P2",
                    "description": "The assertion does not check the function's result.",
                }
            ]
            if finding
            else []
        ),
        "code_excerpts": [
            {
                "file": "tests/test_example.py",
                "start_line": 1,
                "end_line": 2,
                "content": "def test_example():\n    assert 1 == 1\n",
            }
        ],
    }


def cli_backend(tmp_path, payload=None, *, failure=False, timeout=False):
    """Use the real CLI subprocess transport with a deterministic executable."""
    script = tmp_path / "review-backend"
    marker = tmp_path / "transport-called"
    body = (
        "#!/usr/bin/python3\nimport json, pathlib, sys, time\n"
        f"pathlib.Path({str(marker)!r}).write_text(sys.argv[1])\n"
    )
    if timeout:
        body += "time.sleep(2)\n"
    elif failure:
        body += f"sys.stderr.write({UNTRUSTED!r})\nsys.exit(7)\n"
    else:
        body += f"print({json.dumps(payload if payload is not None else review_payload())!r})\n"
    script.write_text(body)
    script.chmod(0o700)
    return BackendConfig(name="offline", type="cli", model="", command=str(script), timeout_s=1), marker


@pytest.mark.parametrize("finding", [False, True])
def test_real_transport_validator_and_converter_report_count(tmp_path, finding):
    backend, marker = cli_backend(tmp_path, review_payload(finding=finding))
    legacy, outcomes = [], []
    findings = cli._run_test_assertion_review(
        TEST_DIFF,
        backend,
        on_outcome=legacy.append,
        on_result=outcomes.append,
    )
    assert marker.read_text() == "-p"
    assert legacy == ["done"]
    assert len(findings) == int(finding)
    assert outcomes == [
        AssertionOutcome(
            AssertionStatus.COMPLETED_FINDINGS if finding else AssertionStatus.COMPLETED_EMPTY,
            int(finding),
        )
    ]
    if finding:
        assert findings[0].backend == "offline"
        assert findings[0].source == "L1"
        assert findings[0].description.startswith("[test-assertion]")


@pytest.mark.parametrize(
    ("case", "stage", "kind", "legacy"),
    [
        ("no-tests", AssertionStage.ELIGIBILITY, AssertionKind.NO_TEST_FILES, "skipped: no test files"),
        ("schema", AssertionStage.VALIDATION, AssertionKind.ERROR, "failed: error"),
        ("transport", AssertionStage.INVOCATION, AssertionKind.ERROR, "failed: error"),
        ("timeout", AssertionStage.INVOCATION, AssertionKind.TIMEOUT, "failed: timeout"),
    ],
)
def test_real_failure_and_inapplicability_are_distinct(tmp_path, case, stage, kind, legacy):
    payload = review_payload()
    if case == "schema":
        payload["findings"] = UNTRUSTED
    backend, marker = cli_backend(
        tmp_path, payload, failure=case == "transport", timeout=case == "timeout"
    )
    outcomes, strings = [], []
    assert (
        cli._run_test_assertion_review(
            CODE_DIFF if case == "no-tests" else TEST_DIFF,
            backend,
            on_outcome=strings.append,
            on_result=outcomes.append,
        )
        == []
    )
    assert marker.exists() == (case != "no-tests")
    assert strings == [legacy]
    expected = AssertionStatus.NOT_APPLICABLE if case == "no-tests" else AssertionStatus.FAILED
    assert outcomes[0].to_metadata() == {
        "status": expected.value,
        "finding_count": 0 if case == "no-tests" else None,
        "stage": stage.value,
        "kind": kind.value,
    }
    assert UNTRUSTED not in json.dumps(outcomes[0].to_metadata())


@pytest.mark.parametrize(
    ("dependency", "stage"),
    [
        ("code_forge.diff.get_changed_files", AssertionStage.PREPARATION),
        ("code_forge.reviewer_json._json_to_state_findings", AssertionStage.CONVERSION),
    ],
)
def test_operation_failure_is_sanitized(monkeypatch, tmp_path, capsys, dependency, stage):
    backend, marker = cli_backend(tmp_path)
    reached = []

    def fail(*args, **kwargs):
        reached.append(True)
        raise RuntimeError(UNTRUSTED)

    monkeypatch.setattr(dependency, fail)
    outcome = cli._run_test_assertion_phase(TEST_DIFF, backend)
    assert reached == [True]
    assert marker.exists() == (stage is AssertionStage.CONVERSION)
    assert outcome == AssertionOutcome(AssertionStatus.FAILED, None, stage, AssertionKind.ERROR)
    output = capsys.readouterr().err
    assert f"stage={stage.value} kind=error" in output
    assert "synthetic-private-value" not in output
    assert "\x1b" not in output
    assert "phase done" not in output


@pytest.mark.parametrize(
    "exc_type", [MemoryError, KeyboardInterrupt, SystemExit, asyncio.CancelledError]
)
@pytest.mark.parametrize(
    "dependency",
    [
        "code_forge.diff.get_changed_files",
        "code_forge.llm_invoke.llm_invoke",
        "code_forge.reviewer_json.validate_reviewer_json",
        "code_forge.reviewer_json._json_to_state_findings",
    ],
)
def test_fatal_operation_propagates_without_completion(
    monkeypatch, tmp_path, capsys, exc_type, dependency
):
    backend, _marker = cli_backend(tmp_path)
    reached = []

    def fail(*args, **kwargs):
        reached.append(True)
        raise exc_type()

    monkeypatch.setattr(dependency, fail)
    with pytest.raises(exc_type):
        cli._run_test_assertion_phase(TEST_DIFF, backend)
    assert reached == [True]
    output = capsys.readouterr().err
    if exc_type is SystemExit:
        assert "phase aborted" in output
        assert "test_assertion=interrupted" not in output
    else:
        assert (
            output.splitlines().count(
                "code-forge: test_assertion=interrupted stage=reporting kind=interrupted"
            )
            == 1
        )
    assert "phase done" not in output


@pytest.mark.parametrize("sink", ["on_outcome", "on_result"])
def test_callback_failure_is_not_reclassified(monkeypatch, tmp_path, sink):
    backend, _marker = cli_backend(tmp_path)
    notifications = []

    def fail(value):
        notifications.append(value)
        raise RuntimeError("callback defect")

    with pytest.raises(RuntimeError, match="callback defect"):
        cli._run_test_assertion_review(TEST_DIFF, backend, **{sink: fail})
    assert len(notifications) == 1
    assert notifications[0] == (
        "done" if sink == "on_outcome" else AssertionOutcome(AssertionStatus.COMPLETED_EMPTY, 0)
    )


@pytest.mark.parametrize("marker", [None, {"status": "completed_empty"}, "done"])
def test_legacy_or_malformed_helper_metadata_reports_inconsistency(monkeypatch, capsys, marker):
    def legacy(*args, **kwargs):
        kwargs["on_outcome"]("done")
        if marker is not None:
            kwargs["on_result"](marker)
        return []

    monkeypatch.setattr(cli, "_run_test_assertion_review", legacy)
    assert cli._run_test_assertion_phase(TEST_DIFF) == AssertionOutcome(
        AssertionStatus.FAILED,
        None,
        AssertionStage.REPORTING,
        AssertionKind.INCONSISTENT_OUTCOME,
    )
    assert "phase failed: error" in capsys.readouterr().err


def test_provider_text_cannot_forge_progress(monkeypatch, capsys):
    def legacy(*args, **kwargs):
        kwargs["on_outcome"](UNTRUSTED)
        return []

    monkeypatch.setattr(cli, "_run_test_assertion_review", legacy)
    assert cli._run_test_assertion_phase(TEST_DIFF) == AssertionOutcome(
        AssertionStatus.FAILED,
        None,
        AssertionStage.REPORTING,
        AssertionKind.INCONSISTENT_OUTCOME,
    )
    output = capsys.readouterr().err
    assert "stage=reporting kind=inconsistent_outcome" in output
    assert "synthetic-private-value" not in output
    assert "phase done" not in output


@pytest.mark.parametrize("count", [True, -1, 0.5, float("nan"), "0", None])
def test_completed_count_rejects_invalid_values(count):
    with pytest.raises((TypeError, ValueError)):
        AssertionOutcome(AssertionStatus.COMPLETED_EMPTY, count)


@pytest.mark.parametrize(
    "args",
    [
        (AssertionStatus.COMPLETED_FINDINGS, 0),
        (AssertionStatus.COMPLETED_EMPTY, 1),
        (AssertionStatus.FAILED, 0, AssertionStage.INVOCATION, AssertionKind.ERROR),
        (AssertionStatus.FAILED, None),
        (AssertionStatus.FAILED, None, AssertionStage.VALIDATION, AssertionKind.TIMEOUT),
        (AssertionStatus.NOT_APPLICABLE, 0),
        (AssertionStatus.SKIPPED, None, AssertionStage.ELIGIBILITY, AssertionKind.NO_TEST_FILES),
        (AssertionStatus.COMPLETED_EMPTY, 0, AssertionStage.INVOCATION, AssertionKind.ERROR),
        ("completed_empty", 0),
        (AssertionStatus.COMPLETED_EMPTY, 0, "invocation"),
        (AssertionStatus.FAILED, None, AssertionStage.INVOCATION, "error"),
    ],
)
def test_inconsistent_or_untyped_outcome_is_rejected(args):
    with pytest.raises((TypeError, ValueError)):
        AssertionOutcome(*args)


def test_no_backend_reaches_invocation_failure():
    outcomes = []
    assert cli._run_test_assertion_review(TEST_DIFF, on_result=outcomes.append) == []
    assert outcomes == [
        AssertionOutcome(
            AssertionStatus.FAILED,
            None,
            AssertionStage.INVOCATION,
            AssertionKind.ERROR,
        )
    ]


@pytest.mark.parametrize(
    "records",
    [
        [AssertionOutcome(AssertionStatus.COMPLETED_FINDINGS, 1)],
        [AssertionOutcome(AssertionStatus.COMPLETED_EMPTY, 0)] * 2,
    ],
)
def test_inconsistent_helper_markers_do_not_establish_completion(monkeypatch, records):
    def helper(*args, **kwargs):
        for record in records:
            kwargs["on_result"](record)
        return []

    monkeypatch.setattr(cli, "_run_test_assertion_review", helper)
    assert cli._run_test_assertion_phase(TEST_DIFF) == AssertionOutcome(
        AssertionStatus.FAILED,
        None,
        AssertionStage.REPORTING,
        AssertionKind.INCONSISTENT_OUTCOME,
    )


def test_forged_record_is_revalidated_before_serialization():
    outcome = AssertionOutcome(AssertionStatus.COMPLETED_EMPTY, 0)
    object.__setattr__(outcome, "status", UNTRUSTED)
    with pytest.raises(TypeError):
        outcome.to_metadata()
