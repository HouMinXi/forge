# SPDX-License-Identifier: Apache-2.0
"""Actual consumers refuse untrusted sibling evidence with synthetic acquisition."""

from dataclasses import asdict
import json
import os
import time

import pytest

from code_forge import mutation
from code_forge.cli import _build_parser, _run_mutation_check
from code_forge.disposition import Disposition
from code_forge.ledger import iter_rows
from code_forge.mutation_findings import is_mutation_survivor
from code_forge.state import Verdict
from tests.test_mutation_ledger_ci_controls import (
    ci_project as ci_project,
    _context,
    _measure,
    _machine,
    _put_result,
    _saved,
    _assert_timeouts,
    _payload,
    _rule,
    _row,
    _one,
    SOURCE_A,
    SOURCE_B,
    _identity,
    _timeout_text,
)


@pytest.mark.parametrize(
    "key,value",
    [
        ("cleanup_complete", False),
        ("publication_error", "RECORDED_PUBLICATION_FAILURE"),
        ("cancellation_type", "KeyboardInterrupt"),
        ("cleanup_error", "RECORDED_CLEANUP_FAILURE"),
        ("process_failure", {"cleanup_complete": False}),
        ("cancelled", True),
    ],
    ids=[
        "cleanup-false",
        "publication-error",
        "cancellation-type",
        "cleanup-error",
        "process-failure",
        "cancelled",
    ],
)
def test_native_failure_marker_survives_outcome_and_blocks_ci(ci_project, key, value):
    p = ci_project
    p.codes, p.statuses = (1,), ("killed",)
    findings, errors, evidence = _measure(p)
    # These are synthetic failure markers on an otherwise actual physical proof.
    evidence[key] = value
    payload = mutation._mutation_outcome(
        findings,
        errors,
        evidence,
        review_context=_context(p),
    )
    _put_result(p, payload)
    machine = _machine(p)
    assert machine.run() is Verdict.FAIL
    saved = _saved(p, Verdict.FAIL)
    assert payload[key] == value
    assert "CI: invalid bound mutation survivor details" in saved.infra_errors
    assert machine._mutation_measurement_eligible is False
    assert not any(is_mutation_survivor(finding) for finding in saved.findings)
    _assert_timeouts(saved.findings, [])
    assert not any(row.evidence_class == "clean_pass" for row in iter_rows(p.root, strict=True))
    assert p.launches == []


@pytest.mark.parametrize("change", ["carrier-postcapture-drift", "same-review-subject-restored"])
def test_completed_evidence_loses_revoked_source_authority(ci_project, change):
    p = ci_project
    payload, findings = _payload(p)
    _rule(p, findings)
    source = p.root / "calc.py"
    if change == "carrier-postcapture-drift":
        result = _put_result(p, payload)
        machine = _machine(p)
        p.l0_hook = lambda: source.write_bytes(SOURCE_B)
    else:
        machine = _machine(p, runner=mutation.run_mutation)
        review = machine.resolved_review
        original_files = list(review.source_files)
        machine._capture_mutation_review_source()
        assert machine._mutation_source_guard is not None
        review.source_files.append(review.source_files[0])
        observed_files = []

        def restore_subject():
            observed_files.append(list(review.source_files))
            review.source_files[:] = original_files

        p.baseline_hook = restore_subject
    assert machine.run() is Verdict.FAIL
    saved = _saved(p, Verdict.FAIL)
    assert machine._mutation_measurement_eligible is False
    _assert_timeouts(saved.findings, [])
    assert not any(row.evidence_class == "clean_pass" for row in iter_rows(p.root, strict=True))
    assert p.launches == []
    if change == "carrier-postcapture-drift":
        assert source.read_bytes() == SOURCE_B and not result.exists()
        assert "CI: invalid bound mutation survivor details" in saved.infra_errors
        assert not any(is_mutation_survivor(finding) for finding in saved.findings)
    else:
        assert review is machine.resolved_review and review.source_files == original_files
        assert observed_files == [original_files + [original_files[0]], original_files, original_files]
        assert source.read_bytes() == SOURCE_A
        assert machine._mutation_source_guard is None
        assert _one(saved.findings, findings[0].id).disposition is Disposition.CONFIRMED


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "unknown-status"])
def test_nonfinite_raw_carrier_and_unknown_legacy_status_are_refused(ci_project, token):
    p = ci_project
    p.codes, p.statuses = (1,), ("killed",)
    payload, _ = _payload(p)
    if token == "unknown-status":
        raw = b'{"status":"unrecognized"}'
    else:
        encoded = json.dumps(payload).encode()
        raw = encoded[:-1] + b',"metadata":' + token.encode() + b"}"
    result = p.state / ".code-forge/mutation-result.json"
    result.write_bytes(raw)
    (p.artifacts / (token + ".raw")).write_bytes(raw)
    machine = _machine(p)
    assert machine.run() is Verdict.FAIL
    saved = _saved(p, Verdict.FAIL)
    assert machine._mutation_measurement_eligible is False
    assert not any(is_mutation_survivor(finding) for finding in saved.findings)
    _assert_timeouts(saved.findings, [])
    assert not any(row.evidence_class == "clean_pass" for row in iter_rows(p.root, strict=True))
    assert p.launches == []
    if token == "unknown-status":
        assert "CI: mutation-result.json invalid status field" in saved.infra_errors
        assert not result.exists()
    else:
        assert (
            "CI: failed to read mutation-result.json: non-JSON mutation result constant"
            in saved.infra_errors
        )
        assert result.read_bytes() == raw


@pytest.mark.parametrize("pid_kind", ["fresh-no-pid", "live-own-pid"])
def test_failed_current_native_is_not_downgraded_by_legacy_running(ci_project, pid_kind):
    p = ci_project
    p.fault = "deadline"
    marker = {
        "status": "running",
        "pid": None if pid_kind == "fresh-no-pid" else os.getpid(),
        "started_at": time.time(),
    }
    result = _put_result(p, marker)
    original = result.read_bytes()
    machine = _machine(p, runner=mutation.run_mutation)
    assert machine.run() is Verdict.FAIL
    saved = _saved(p, Verdict.FAIL)
    assert machine._mutation_bound_failure is True
    assert machine._mutation_measurement_eligible is False
    assert result.read_bytes() == original
    assert saved.infra_errors
    assert not any(is_mutation_survivor(finding) for finding in saved.findings)
    _assert_timeouts(saved.findings, [])
    assert not any(row.evidence_class == "clean_pass" for row in iter_rows(p.root, strict=True))
    assert p.launches == []


def test_nonstring_required_ledger_tail_keeps_current_survivor_active(ci_project, capsys):
    p = ci_project
    findings, _, _ = _measure(p)
    _rule(p, findings)
    malformed = asdict(_row(p, findings[0]))
    malformed["repo_root"] = []
    with (p.root / ".code-forge/ledger.jsonl").open("a") as stream:
        stream.write(json.dumps(malformed) + "\n")
    machine = _machine(p, runner=mutation.run_mutation)
    capsys.readouterr()
    escaped = None
    verdict = None
    try:
        verdict = machine.run()
    except TypeError as exc:
        escaped = exc
    assert escaped is None, "malformed required ledger field must be refused without TypeError escape"
    assert verdict is Verdict.FAIL
    output = capsys.readouterr()
    saved = _saved(p, Verdict.FAIL)
    assert machine._mutation_measurement_eligible is True
    assert _one(saved.findings, findings[0].id).disposition is Disposition.CONFIRMED
    assert "non-string ledger field: repo_root" in output.err
    assert not any(row.evidence_class == "clean_pass" for row in iter_rows(p.root))
    assert p.launches == []


def test_bound_standalone_mutation_check_renders_physical_timeout(ci_project, capsys):
    p = ci_project
    p.codes, p.statuses = (36,), ("timeout",)
    args = _build_parser().parse_args(["mutation-check"])
    capsys.readouterr()
    assert _run_mutation_check(args, p.root) == 0
    output = capsys.readouterr()
    expected = "code-forge: mutation-check: " + _timeout_text(_identity(1))
    assert output.out == ""
    assert output.err.count(expected) == 1
    assert "code-forge: mutation-check: PASS" in output.err
    assert len(_timeout_text(_identity(1))) <= 181
    assert p.stages == ["baseline"] * 3 + ["probe", "run", "results"]
    assert len(p.measurements) == 1 and p.launches == []
    assert (p.measurements[0] / "results.txt").is_file()
    assert (p.root / "calc.py").read_bytes() == SOURCE_A
    assert not (p.root / "mutants").exists()
