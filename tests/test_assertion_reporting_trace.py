# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Normally returned assertion advice always has a validated host-owned trace."""

from __future__ import annotations

import asyncio
import itertools
import json
from types import SimpleNamespace

import pytest

from code_forge import cli, progress
from code_forge.assertion_outcome import AssertionKind, AssertionOutcome, AssertionStage, AssertionStatus


PRIVATE = "synthetic-marker-private-value"
FINDING = "Existing finding description remains visible."
DIFF = (
    "diff --git a/tests/test_sample.py b/tests/test_sample.py\n"
    "--- a/tests/test_sample.py\n+++ b/tests/test_sample.py\n"
    "@@ -1 +1,2 @@\n def test_sample():\n+    assert True\n"
)


def fallback():
    return AssertionOutcome(
        AssertionStatus.FAILED, None, AssertionStage.REPORTING, AssertionKind.INCONSISTENT_OUTCOME
    )


def empty():
    return AssertionOutcome(AssertionStatus.COMPLETED_EMPTY, 0)


def install_helper(monkeypatch, markers, count=0, legacy=("done",)):
    findings = [SimpleNamespace(description=FINDING) for _ in range(count)]

    def helper(*args, on_outcome, on_result):
        for value in legacy:
            on_outcome(value)
        for value in markers:
            on_result(value)
        return findings

    monkeypatch.setattr(cli, "_run_test_assertion_review", helper)
    return findings


def assert_fallback(result):
    assert result == fallback()
    assert result.to_metadata() == {
        "status": "failed",
        "finding_count": None,
        "stage": "reporting",
        "kind": "inconsistent_outcome",
    }
    assert PRIVATE not in json.dumps(result.to_metadata())


@pytest.mark.parametrize(
    ("status", "count", "stage", "kind"),
    [
        args
        for args in itertools.product(
            AssertionStatus, (None, 0, 1), (None, *AssertionStage), (None, *AssertionKind)
        )
        if args[2] is AssertionStage.REPORTING or args[3] is AssertionKind.INCONSISTENT_OUTCOME
    ],
)
def test_new_reporting_pair_has_exact_cross_product(status, count, stage, kind):
    valid = (status, count, stage, kind) == (
        AssertionStatus.FAILED,
        None,
        AssertionStage.REPORTING,
        AssertionKind.INCONSISTENT_OUTCOME,
    )
    if valid:
        assert_fallback(AssertionOutcome(status, count, stage, kind))
    else:
        with pytest.raises(ValueError):
            AssertionOutcome(status, count, stage, kind)


@pytest.mark.parametrize(
    ("status", "count", "stage", "kind", "legacy"),
    [
        (AssertionStatus.COMPLETED_EMPTY, 0, None, None, "done"),
        (AssertionStatus.COMPLETED_FINDINGS, 1, None, None, "done"),
        (
            AssertionStatus.NOT_APPLICABLE,
            0,
            AssertionStage.ELIGIBILITY,
            AssertionKind.NO_TEST_FILES,
            "skipped: no test files",
        ),
        (
            AssertionStatus.SKIPPED,
            None,
            AssertionStage.ELIGIBILITY,
            AssertionKind.NO_GIT_DIFF,
            "skipped: no git diff",
        ),
        *[
            (AssertionStatus.FAILED, None, stage, kind, legacy)
            for stage, kind, legacy in [
                (AssertionStage.PREPARATION, AssertionKind.ERROR, "failed: error"),
                (AssertionStage.INVOCATION, AssertionKind.ERROR, "failed: error"),
                (AssertionStage.INVOCATION, AssertionKind.TIMEOUT, "failed: timeout"),
                (AssertionStage.VALIDATION, AssertionKind.ERROR, "failed: error"),
                (AssertionStage.CONVERSION, AssertionKind.ERROR, "failed: error"),
            ]
        ],
    ],
)
def test_nine_existing_outcomes_are_unchanged(monkeypatch, status, count, stage, kind, legacy):
    candidate = AssertionOutcome(status, count, stage, kind)
    install_helper(monkeypatch, [candidate], count or 0, legacy=(PRIVATE,))
    assert cli._run_test_assertion_phase(DIFF) is candidate
    expected = {"status": status.value, "finding_count": count}
    if stage is not None:
        expected.update(stage=stage.value, kind=kind.value)
    assert candidate.to_metadata() == expected
    assert candidate.legacy_outcome() == legacy


class OutcomeSubclass(AssertionOutcome):
    def to_metadata(self):
        raise AssertionError("subclass dispatch must not occur")

    def legacy_outcome(self):
        raise AssertionError("subclass dispatch must not occur")


@pytest.mark.parametrize(
    "markers",
    [
        [],
        [empty(), empty()],
        [{"status": PRIVATE}],
        [PRIVATE],
        [OutcomeSubclass(AssertionStatus.COMPLETED_EMPTY, 0)],
    ],
)
@pytest.mark.parametrize("legacy", [("done",), (PRIVATE,), ()])
def test_bad_markers_cannot_be_repaired_by_legacy_callback(monkeypatch, capsys, markers, legacy):
    install_helper(monkeypatch, markers, legacy=legacy)
    assert_fallback(cli._run_test_assertion_phase(DIFF))
    output = capsys.readouterr().err
    assert "phase failed: error: findings=0" in output
    assert "stage=reporting kind=inconsistent_outcome" in output
    assert PRIVATE not in output
    assert "phase done" not in output


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", PRIVATE),
        ("finding_count", True),
        ("finding_count", "0"),
        ("finding_count", -1),
        ("stage", PRIVATE),
        ("kind", PRIVATE),
    ],
)
def test_mutated_exact_type_is_revalidated(monkeypatch, capsys, field, value):
    candidate = empty()
    object.__setattr__(candidate, field, value)
    install_helper(monkeypatch, [candidate])
    assert_fallback(cli._run_test_assertion_phase(DIFF))
    assert PRIVATE not in capsys.readouterr().err


@pytest.mark.parametrize("field", ["status", "finding_count"])
def test_missing_required_field_is_reconciled(monkeypatch, field):
    candidate = empty()
    object.__delattr__(candidate, field)
    install_helper(monkeypatch, [candidate])
    assert_fallback(cli._run_test_assertion_phase(DIFF))


@pytest.mark.parametrize(
    ("candidate", "count"),
    [
        (empty(), 1),
        (AssertionOutcome(AssertionStatus.COMPLETED_FINDINGS, 1), 0),
        (AssertionOutcome(AssertionStatus.COMPLETED_FINDINGS, 2), 1),
        (
            AssertionOutcome(
                AssertionStatus.FAILED, None, AssertionStage.INVOCATION, AssertionKind.ERROR
            ),
            1,
        ),
        (
            AssertionOutcome(
                AssertionStatus.SKIPPED, None, AssertionStage.ELIGIBILITY, AssertionKind.NO_GIT_DIFF
            ),
            1,
        ),
    ],
)
def test_count_mismatch_preserves_findings_and_order(monkeypatch, capsys, candidate, count):
    findings = install_helper(monkeypatch, [candidate], count)
    before = list(findings)
    assert_fallback(cli._run_test_assertion_phase(DIFF))
    assert findings == before
    output = capsys.readouterr().err
    assert f"phase failed: error: findings={count}" in output
    assert output.count("[test-assertion] " + FINDING) == count
    assert output.index("phase start") < output.index("stage=reporting")
    if count:
        assert output.index("phase start") < output.index(FINDING) < output.index("phase failed")


def test_instance_method_shadowing_cannot_override_validation_or_progress(monkeypatch, capsys):
    candidate = empty()

    def unexpected():
        raise AssertionError("instance method dispatch must not occur")

    object.__setattr__(candidate, "to_metadata", unexpected)
    object.__setattr__(candidate, "legacy_outcome", unexpected)
    install_helper(monkeypatch, [candidate])
    assert cli._run_test_assertion_phase(DIFF) is candidate
    assert "phase done: findings=0" in capsys.readouterr().err


@pytest.mark.parametrize(
    "exception_type",
    [
        RuntimeError,
        TypeError,
        ValueError,
        AttributeError,
        MemoryError,
        KeyboardInterrupt,
        SystemExit,
        asyncio.CancelledError,
    ],
)
@pytest.mark.parametrize("emit_marker", [False, True])
def test_helper_exception_is_not_a_reporting_fallback(monkeypatch, capsys, exception_type, emit_marker):
    error = exception_type(PRIVATE)

    def helper(*args, on_outcome, on_result):
        if emit_marker:
            on_result(empty())
        raise error

    monkeypatch.setattr(cli, "_run_test_assertion_review", helper)
    with pytest.raises(exception_type) as caught:
        cli._run_test_assertion_phase(DIFF)
    assert caught.value is error
    output = capsys.readouterr().err
    if exception_type in (MemoryError, KeyboardInterrupt, asyncio.CancelledError):
        assert (
            output.splitlines().count(
                "code-forge: test_assertion=interrupted stage=reporting kind=interrupted"
            )
            == 1
        )
    else:
        assert "phase aborted" in output
        assert "stage=reporting" not in output
    assert "phase done" not in output


@pytest.mark.parametrize(
    "exception_type", [MemoryError, KeyboardInterrupt, SystemExit, asyncio.CancelledError, RuntimeError]
)
def test_validation_fatal_or_unexpected_exception_propagates(monkeypatch, capsys, exception_type):
    install_helper(monkeypatch, [empty()])
    error = exception_type(PRIVATE)

    def fail(self):
        raise error

    monkeypatch.setattr(AssertionOutcome, "to_metadata", fail)
    with pytest.raises(exception_type) as caught:
        cli._run_test_assertion_phase(DIFF)
    assert caught.value is error
    output = capsys.readouterr().err
    if exception_type in (MemoryError, KeyboardInterrupt, asyncio.CancelledError):
        assert (
            output.splitlines().count(
                "code-forge: test_assertion=interrupted stage=reporting kind=interrupted"
            )
            == 1
        )
    else:
        assert "phase aborted" in output
        assert "stage=reporting" not in output


@pytest.mark.parametrize("exception_type", [TypeError, ValueError, AttributeError])
def test_validation_contract_errors_have_fixed_fallback(monkeypatch, capsys, exception_type):
    install_helper(monkeypatch, [empty()])

    def fail(self):
        raise exception_type(PRIVATE)

    monkeypatch.setattr(AssertionOutcome, "to_metadata", fail)
    assert cli._run_test_assertion_phase(DIFF) == fallback()
    output = capsys.readouterr().err
    assert "stage=reporting kind=inconsistent_outcome" in output
    assert PRIVATE not in output


@pytest.mark.parametrize("sink", ["on_result", "on_outcome"])
def test_real_helper_callback_error_propagates_through_phase(monkeypatch, capsys, sink):
    original = cli._run_test_assertion_review
    error = TypeError("callback defect")

    def fail(value):
        raise error

    def wrapper(*args, **kwargs):
        kwargs[sink] = fail
        return original(*args, **kwargs)

    monkeypatch.setattr(cli, "_run_test_assertion_review", wrapper)
    with pytest.raises(TypeError) as caught:
        cli._run_test_assertion_phase(DIFF)
    assert caught.value is error
    output = capsys.readouterr().err
    assert "phase aborted" in output
    assert "stage=reporting" not in output


@pytest.mark.parametrize("exception_type", [TypeError, ValueError, AttributeError, MemoryError])
def test_finding_write_failure_remains_aborted(monkeypatch, exception_type):
    install_helper(monkeypatch, [], count=1)
    events = []
    error = exception_type("stderr defect")

    def fail_write(value):
        raise error

    monkeypatch.setattr(progress, "emit", events.append)
    monkeypatch.setattr(cli.sys, "stderr", SimpleNamespace(write=fail_write))
    with pytest.raises(exception_type) as caught:
        cli._run_test_assertion_phase(DIFF)
    assert caught.value is error
    assert events[0] == "test-assertion phase start"
    if exception_type is MemoryError:
        assert events == ["test-assertion phase start"]
    else:
        assert "phase aborted: findings=1" in events[-1]
    assert not any("stage=reporting" in event for event in events)
