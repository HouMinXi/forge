"""Offline adversarial tests for the non-qualifying public failure diagnostic.

Only synthetic reviewed identities are used. No test process, service, installer,
or live collection is launched; the subprocess boundary is always replaced.
"""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from scripts import ci_failure_diagnostic as diagnostic
from scripts import ci_private_transport as transport
from scripts import ci_test_shards as ci
from tests.test_ci_private_transport import (
    CANARY,
    REASON,
    SHA,
    approved_plan,
    evidence,
    execution,
    expected_collection,
    fake_execution,
    jobs,
    materialize_public,
)


PHASE = "shard-0"
RUN = "123456789"
ATTEMPT = "3"
PUBLIC_KEYS = {"schema", "phase", "stage", "status", "tests", "raw_evidence"}
STAGES = {
    "prepare",
    "pytest",
    "receipt_read",
    "receipt_validation",
    "outcome_validation",
    "projection",
    "coverage_validation",
    "log_close",
    "publication",
    "output_validation",
    "unavailable",
}


def _failure_report(receipt):
    report = next(item for item in receipt["reports"] if item["when"] == "call")
    report["outcome"] = "failed"
    return report


def _case(tmp_path, monkeypatch, count=2):
    example = evidence(count)
    private = tmp_path / "private"
    private.mkdir()
    case = SimpleNamespace(
        example=example,
        private=private,
        public=tmp_path / "public",
        plan_path=tmp_path / "inputs/plan.json",
        manifest_path=tmp_path / "inputs/manifest.json",
        policy_path=tmp_path / "inputs/policy.json",
        state_path=private / transport.FAILURE_STATE,
        receipt_path=private / f"receipt-{PHASE}.json",
        plan=approved_plan(example),
        receipt=execution(example.plan, PHASE),
        state={
            "sha": SHA,
            "phase": PHASE,
            "run": RUN,
            "attempt": ATTEMPT,
            "stage": "pytest",
            "returncode": 1,
        },
    )
    case.receipt["exitstatus"] = 1
    _failure_report(case.receipt)
    monkeypatch.setenv("GITHUB_RUN_ID", RUN)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", ATTEMPT)
    monkeypatch.setattr(ci, "POLICY", case.policy_path)
    _save(case)
    return case


def _save(case):
    for path, value in (
        (case.plan_path, case.plan),
        (case.manifest_path, case.example.manifest),
        (case.policy_path, case.example.policy),
        (case.receipt_path, case.receipt),
        (case.state_path, case.state),
    ):
        ci.write(path, value)


@pytest.fixture
def case(tmp_path, monkeypatch):
    return _case(tmp_path, monkeypatch)


def _export(case, **overrides):
    args = {
        "sha": SHA,
        "phase": PHASE,
        "plan_path": case.plan_path,
        "directory": case.private,
        "public_directory": case.public,
        "manifest_path": case.manifest_path,
    }
    args.update(overrides)
    return diagnostic.export_failure(**args)


def _assert_public(case, value, *, status=None, stage=None):
    assert set(value) == PUBLIC_KEYS
    assert value["schema"] == "forge-public-ci-failure-v1"
    assert value["phase"] in {f"shard-{number}" for number in range(4)}
    assert value["stage"] in STAGES
    assert value["status"] in {"recorded", "suppressed", "unavailable"}
    assert value["raw_evidence"] == "NOT_PUBLISHED"
    if status is not None:
        assert value["status"] == status
    if stage is not None:
        assert value["stage"] == stage
    if value["status"] != "recorded":
        assert value["tests"] == []
    for item in value["tests"]:
        assert set(item) == {"id", "stage", "status", "kind"}
        assert re.fullmatch(r"public-(?:0|[1-9][0-9]*)$", item["id"])
        assert int(item["id"].split("-")[1]) < len(case.example.manifest["nodes"])
        assert item["stage"] in {"setup", "call", "teardown"}
        assert item["status"] in {"failed", "unexpected_skip", "unexpected_xfail"}
        # Only a failure carries a category, and only from the fixed set.
        if item["status"] == "failed":
            assert item["kind"] in {None, "assertion", "timeout", "error"}
        else:
            assert item["kind"] is None
    text = (case.public / "failure.json").read_text()
    assert json.loads(text) == value
    for forbidden in (
        CANARY,
        "7654321",
        SHA,
        RUN,
        str(case.private),
        str(case.plan_path),
        hashlib.sha256(CANARY.encode()).hexdigest(),
        *case.example.manifest["nodes"],
    ):
        assert forbidden not in text
    assert "tests/" not in text and "/home/" not in text and "::" not in text
    assert not (os.stat(case.public / "failure.json").st_mode & 0o077)
    return value


def _run(case, **overrides):
    args = {
        "phase": PHASE,
        "plan": case.plan,
        "directory": case.private,
        "public_directory": case.public,
        "policy": case.example.policy,
        "manifest": case.example.manifest,
        "failure_diagnostic": True,
    }
    args.update(overrides)
    return transport.run_phase(**args)


def _args(case):
    return [
        "--sha",
        SHA,
        "--phase",
        PHASE,
        "--plan",
        str(case.plan_path),
        "--directory",
        str(case.private),
        "--public-directory",
        str(case.public),
        "--manifest",
        str(case.manifest_path),
    ]


def test_failed_report_exports_only_reviewed_bare_ordinal(case, capsys):
    report = _failure_report(case.receipt)
    result = _assert_public(case, _export(case), status="recorded", stage="pytest")
    assert result["tests"] == [
        {
            "id": f"public-{case.example.manifest['nodes'].index(report['nodeid'])}",
            "stage": "call",
            "status": "failed",
            "kind": None,
        }
    ]
    assert list(case.public.iterdir()) == [case.public / "failure.json"]
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("label", [None, "assertion", "timeout", "error"])
def test_reviewed_failure_kind_is_published_as_given(case, label):
    """The runner's fixed category travels unchanged; absence stays absent."""
    report = _failure_report(case.receipt)
    if label is not None:
        report["failure_kind"] = label
    _save(case)
    result = _assert_public(case, _export(case), status="recorded", stage="pytest")
    assert [item["kind"] for item in result["tests"]] == [label]


def test_unreviewed_failure_kind_suppresses_the_whole_list(case):
    """A value outside the fixed set is runtime text in disguise; drop everything."""
    report = _failure_report(case.receipt)
    report["failure_kind"] = f"AssertionError: {CANARY}"
    _save(case)
    result = _export(case)
    assert result["status"] == "suppressed"
    assert result["tests"] == []


@pytest.mark.parametrize("kind", ["reports", "subtests"])
@pytest.mark.parametrize("when", ["setup", "call", "teardown"])
def test_failed_reports_and_subtests_keep_only_fixed_stage_labels(case, kind, when):
    receipt = execution(case.example.plan, PHASE)
    receipt["exitstatus"] = 1
    report = dict(receipt["reports"][0], when=when, outcome="failed")
    if kind == "reports":
        next(
            item for item in receipt[kind] if item["nodeid"] == report["nodeid"] and item["when"] == when
        ).update(report)
    else:
        receipt[kind] = [report, dict(report)]
    case.receipt = receipt
    _save(case)
    result = _assert_public(case, _export(case), status="recorded")
    assert result["tests"] == [
        {
            "id": f"public-{case.example.manifest['nodes'].index(report['nodeid'])}",
            "stage": when,
            "status": "failed",
            "kind": None,
        }
    ]


@pytest.mark.parametrize("kind", ["reports", "subtests"])
@pytest.mark.parametrize("outcome", ["passed", "failed", "skipped"])
def test_unexpected_xfail_has_fixed_label_without_forwarding_reason(case, kind, outcome):
    case.receipt = execution(case.example.plan, PHASE)
    case.receipt["exitstatus"] = 1
    report = case.receipt["reports"][1]
    report.update(outcome=outcome, wasxfail=True, skip_reason=REASON if outcome == "skipped" else None)
    if kind == "subtests":
        case.receipt["subtests"] = [dict(report)]
        report.update(outcome="passed", wasxfail=False, skip_reason=None)
    _save(case)
    result = _assert_public(case, _export(case), status="recorded")
    statuses = {item["status"] for item in result["tests"]}
    assert "unexpected_xfail" in statuses
    assert ("failed" in statuses) == (outcome == "failed")
    assert REASON not in (case.public / "failure.json").read_text()


@pytest.mark.parametrize("known_node", [False, True])
def test_only_exact_runtime_skip_policy_avoids_unexpected_skip(case, known_node):
    case.receipt = execution(case.example.plan, PHASE)
    case.receipt["exitstatus"] = 0
    case.state.update(stage="outcome_validation", returncode=0)
    nodes = case.receipt["collected"]
    node = next(node for node in nodes if (node in case.example.policy["runtime_skips"]) == known_node)
    report = next(item for item in case.receipt["reports"] if item["nodeid"] == node)
    report.update(outcome="skipped", skip_reason=REASON)
    _save(case)
    result = _assert_public(case, _export(case), status="recorded", stage="outcome_validation")
    assert [item["status"] for item in result["tests"]] == ([] if known_node else ["unexpected_skip"])


def test_private_log_junit_and_unrelated_raw_siblings_are_never_read_or_copied(case, monkeypatch):
    paths = [case.private / name for name in ("shard-0.log", "shard-0.xml", "raw.json")]
    for path in paths:
        path.write_text(CANARY)
    reader = transport.read_json

    def read(path):
        assert Path(path) not in paths
        return reader(path)

    monkeypatch.setattr(transport, "read_json", read)
    _assert_public(case, _export(case), status="recorded")
    assert set(path.name for path in case.public.iterdir()) == {"failure.json"}
    assert all(path.read_text() == CANARY for path in paths)


@pytest.mark.parametrize("source", ["state", "receipt", "plan", "manifest", "policy"])
@pytest.mark.parametrize("damage", ["missing", "malformed", "extra-key", "duplicate-key", "symlink"])
def test_missing_or_malformed_sources_never_release_partial_ids(case, source, damage):
    path = getattr(case, f"{source}_path")
    if damage == "missing":
        path.unlink()
    elif damage == "malformed":
        path.write_text("{" + CANARY)
    elif damage == "extra-key":
        value = ci.read(path)
        value[CANARY] = CANARY
        ci.write(path, value)
    elif damage == "duplicate-key":
        text = path.read_text()
        key = next(iter(json.loads(text)))
        path.write_text(text[:-2] + "," + json.dumps(key) + ":" + json.dumps(CANARY) + "}")
    else:
        target = case.private / f"raw-{source}.json"
        path.rename(target)
        path.symlink_to(target)
    result = _export(case)
    _assert_public(case, result, status="unavailable" if damage == "missing" else "suppressed")


@pytest.mark.parametrize("field", sorted(transport.RECEIPT_KEYS))
def test_missing_receipt_fields_suppress_all_ids(case, field):
    del case.receipt[field]
    _save(case)
    _assert_public(case, _export(case), status="suppressed")


@pytest.mark.parametrize("kind", ["reports", "subtests"])
@pytest.mark.parametrize("field", sorted(transport.REPORT_KEYS))
def test_missing_fields_in_later_report_suppress_previously_valid_failure(case, kind, field):
    later = dict(case.receipt["reports"][-1])
    del later[field]
    case.receipt[kind].append(later)
    _save(case)
    _assert_public(case, _export(case), status="suppressed")


@pytest.mark.parametrize("kind", ["reports", "subtests"])
@pytest.mark.parametrize("field", ["nodeid", "when", "outcome", "skip_reason", "wasxfail", "extra"])
def test_canary_in_every_report_location_suppresses_entire_id_list(case, kind, field):
    later = dict(case.receipt["reports"][-1])
    later[field] = CANARY
    case.receipt[kind].append(later)
    _save(case)
    _assert_public(case, _export(case), status="suppressed")


@pytest.mark.parametrize(
    "field",
    [
        "sha",
        "phase",
        "exitstatus",
        "collected",
        "collection_skips",
        "collection_skip_reasons",
        "collection_errors",
        "reports",
        "subtests",
        "extra",
    ],
)
def test_canary_in_every_receipt_location_is_never_forwarded(case, field):
    values = {
        "collected": [*case.receipt["collected"], CANARY],
        "collection_skips": [CANARY],
        "collection_skip_reasons": {CANARY: CANARY},
        "collection_errors": [CANARY],
        "reports": [*case.receipt["reports"], CANARY],
        "subtests": [CANARY],
    }
    case.receipt[field] = values.get(field, CANARY)
    _save(case)
    _assert_public(case, _export(case), status="suppressed")


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate-collected",
        "duplicate-report",
        "uncollected-known-report",
        "wrong-phase-node",
        "unmapped-collected",
        "unmapped-report",
        "exit-mismatch",
        "bool-exit",
        "negative-exit",
        "too-large-exit",
        "collection-reason",
        "reason-on-passed",
        "unknown-skip-reason",
        "unhashable-node",
        "unhashable-when",
        "null-report",
        "tuple-reports",
        "unknown-outcome",
    ],
)
def test_any_inconsistent_record_suppresses_all_ids(case, mutation):
    if mutation == "duplicate-collected":
        case.receipt["collected"].append(case.receipt["collected"][0])
    elif mutation == "duplicate-report":
        case.receipt["reports"].append(dict(case.receipt["reports"][0]))
    elif mutation == "uncollected-known-report":
        case.receipt["reports"][-1]["nodeid"] = case.example.spare
    elif mutation == "wrong-phase-node":
        other = case.example.plan["phases"]["shard-1"]["nodes"][0]
        case.receipt["collected"].append(other)
        case.receipt["reports"].append(dict(case.receipt["reports"][-1], nodeid=other))
    elif mutation == "unmapped-collected":
        case.receipt["collected"].append(CANARY)
    elif mutation == "unmapped-report":
        case.receipt["reports"][-1]["nodeid"] = CANARY
    elif mutation.endswith("exit") or mutation == "exit-mismatch":
        case.receipt["exitstatus"] = {
            "exit-mismatch": 0,
            "bool-exit": True,
            "negative-exit": -1,
            "too-large-exit": 6,
        }[mutation]
    elif mutation == "collection-reason":
        case.receipt["collection_skip_reasons"] = {"unknown": CANARY}
    elif mutation == "reason-on-passed":
        case.receipt["reports"][-1]["skip_reason"] = REASON
    elif mutation == "unknown-skip-reason":
        case.receipt["reports"][-1].update(outcome="skipped", skip_reason=CANARY)
    elif mutation == "unhashable-node":
        case.receipt["reports"][-1]["nodeid"] = [CANARY]
    elif mutation == "unhashable-when":
        case.receipt["reports"][-1]["when"] = {CANARY: CANARY}
    elif mutation == "null-report":
        case.receipt["subtests"] = [None]
    elif mutation == "tuple-reports":
        # JSON has no tuple, so use a non-list JSON object instead.
        case.receipt["reports"] = {"0": case.receipt["reports"][0]}
    else:
        case.receipt["reports"][-1]["outcome"] = "xpassed"
    _save(case)
    _assert_public(case, _export(case), status="suppressed")


@pytest.mark.parametrize("field", sorted(diagnostic.STATE_KEYS))
@pytest.mark.parametrize("damage", ["missing", "canary"])
def test_state_requires_exact_context_and_closed_fields(case, field, damage):
    if damage == "missing":
        del case.state[field]
    else:
        case.state[field] = CANARY
    _save(case)
    _assert_public(case, _export(case), status="suppressed", stage="unavailable")


@pytest.mark.parametrize(
    "mutation",
    [
        "sha",
        "phase",
        "run",
        "attempt",
        "run-int",
        "attempt-int",
        "bool-code",
        "float-code",
        "large-code",
        "small-code",
        "prepare-code",
        "validation-code",
        "extra",
    ],
)
def test_state_mismatch_or_illegal_stage_status_pair_cannot_select_receipt(case, mutation):
    values = {
        "sha": ("sha", "b" * 40),
        "phase": ("phase", "shard-1"),
        "run": ("run", "123456790"),
        "attempt": ("attempt", "4"),
        "run-int": ("run", int(RUN)),
        "attempt-int": ("attempt", int(ATTEMPT)),
        "bool-code": ("returncode", True),
        "float-code": ("returncode", 1.0),
        "large-code": ("returncode", 256),
        "small-code": ("returncode", -256),
        "prepare-code": ("stage", "prepare"),
        "validation-code": ("stage", "receipt_read"),
        "extra": (CANARY, CANARY),
    }
    field, value = values[mutation]
    case.state[field] = value
    _save(case)
    _assert_public(case, _export(case), status="suppressed", stage="unavailable")


@pytest.mark.parametrize("name", ["GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"])
@pytest.mark.parametrize(
    "value",
    [None, "", "0", "-1", "01", "1\n", CANARY, "9" * 30],
    ids=[
        "missing",
        "empty",
        "zero",
        "negative",
        "leading-zero",
        "control",
        "private-canary",
        "oversize",
    ],
)
def test_missing_or_invalid_current_github_context_suppresses_receipt(case, monkeypatch, name, value):
    if value is None:
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, value)
    _assert_public(case, _export(case), status="suppressed", stage="unavailable")


@pytest.mark.parametrize(
    "mutation",
    [
        "request-sha",
        "plan-sha",
        "plan-policy",
        "plan-node",
        "manifest-drift",
        "policy-drift",
        "receipt-sha",
        "receipt-phase",
    ],
)
def test_plan_manifest_policy_and_receipt_must_match_request(case, mutation):
    overrides = {}
    if mutation == "request-sha":
        overrides["sha"] = "b" * 40
    elif mutation == "plan-sha":
        case.plan["sha"] = "b" * 40
    elif mutation == "plan-policy":
        case.plan["policy_sha256"] = "b" * 64
    elif mutation == "plan-node":
        case.plan["phases"][PHASE]["nodes"][0] = "tests/unknown.py::public-0"
    elif mutation == "manifest-drift":
        case.example.manifest["nodes"].append(case.example.spare + "_new")
        case.example.manifest["nodes"].sort()
    elif mutation == "policy-drift":
        case.example.policy["runtime_skips"][case.receipt["collected"][0]] = CANARY
    elif mutation == "receipt-sha":
        case.receipt["sha"] = "b" * 40
    else:
        case.receipt["phase"] = "shard-1"
    _save(case)
    _assert_public(case, _export(case, **overrides), status="suppressed")


@pytest.mark.parametrize("stage", ["prepare", "pytest"])
def test_no_subprocess_result_never_associates_stale_receipt(case, stage):
    case.state.update(stage=stage, returncode=None)
    _save(case)
    _assert_public(case, _export(case), status="unavailable", stage=stage)


@pytest.mark.parametrize("returncode", [-15, -9, 2, 3, 4, 5, 17, 124, 137, 143, 255])
def test_subprocess_code_is_private_and_diagnostic_cannot_turn_failure_green(
    case, monkeypatch, returncode
):
    fake_execution(monkeypatch, case.receipt, returncode)
    with pytest.raises(SystemExit) as error:
        _run(case)
    assert error.value.code == returncode
    state = ci.read(case.state_path)
    assert state == {**case.state, "returncode": returncode}
    result = _export(case)
    _assert_public(case, result, status="suppressed", stage="pytest")
    assert not (case.public / f"receipt-{PHASE}.json").exists()


def test_success_clears_old_failure_state_and_preserves_strict_receipt(case, monkeypatch, capsys):
    receipt = execution(case.example.plan, PHASE)
    fake_execution(monkeypatch, receipt)
    result = _run(case)
    assert not case.state_path.exists()
    assert result == expected_collection(receipt, case.example.manifest)
    assert ci.read(case.public / f"receipt-{PHASE}.json") == result
    captured = capsys.readouterr()
    assert CANARY not in captured.out + captured.err


@pytest.mark.parametrize(
    "stage",
    [
        "prepare",
        "pytest",
        "receipt_read",
        "receipt_validation",
        "outcome_validation",
        "projection",
        "coverage_validation",
        "log_close",
        "publication",
        "output_validation",
    ],
)
def test_sidecar_records_last_reached_stage_and_exact_original_exception(case, monkeypatch, stage):
    sentinel = RuntimeError(CANARY)
    receipt = execution(case.example.plan, PHASE)
    fake_execution(monkeypatch, receipt)

    def fail(*_args, **_kwargs):
        raise sentinel

    if stage == "prepare":
        monkeypatch.setattr(transport, "_validate_public_plan", fail)
    elif stage == "pytest":
        monkeypatch.setattr(transport.subprocess, "run", fail)
    elif stage == "receipt_read":
        reader = transport.read_json

        def read(path):
            if Path(path) == case.receipt_path:
                raise sentinel
            return reader(path)

        monkeypatch.setattr(transport, "read_json", read)
    elif stage == "receipt_validation":
        monkeypatch.setattr(transport, "_validate_receipt", fail)
    elif stage in {"outcome_validation", "coverage_validation"}:
        validator = ci.validate_execution

        def validate(value, *args):
            projected = "::public-" in value["collected"][0]
            if projected == (stage == "coverage_validation"):
                raise sentinel
            return validator(value, *args)

        monkeypatch.setattr(ci, "validate_execution", validate)
    elif stage == "projection":
        monkeypatch.setattr(transport, "project_receipt", fail)
    elif stage == "log_close":
        logger = transport._log

        @contextmanager
        def log(*args):
            with logger(*args) as stream:
                yield stream
            raise sentinel

        monkeypatch.setattr(transport, "_log", log)
    elif stage == "publication":
        writer = transport._write_json

        def write(path, value):
            if Path(path).parent == case.public:
                raise sentinel
            return writer(path, value)

        monkeypatch.setattr(transport, "_write_json", write)
    else:
        validator = transport._existing_outputs

        def existing(*args):
            if (case.public / f"receipt-{PHASE}.json").exists():
                raise sentinel
            return validator(*args)

        monkeypatch.setattr(transport, "_existing_outputs", existing)
    with pytest.raises(RuntimeError) as caught:
        _run(case)
    assert caught.value is sentinel
    state = ci.read(case.state_path)
    assert state == {
        **case.state,
        "stage": stage,
        "returncode": None if stage in {"prepare", "pytest"} else 0,
    }


@pytest.mark.parametrize("original", [RuntimeError(CANARY), KeyboardInterrupt(CANARY), SystemExit(137)])
@pytest.mark.parametrize("write_error", [OSError(CANARY), KeyboardInterrupt(CANARY), SystemExit(99)])
def test_sidecar_write_failure_preserves_exact_received_baseexception(
    case, monkeypatch, original, write_error
):
    def fail_run(*_args):
        raise original

    def fail_write(*_args):
        raise write_error

    monkeypatch.setattr(transport, "_run_phase", fail_run)
    monkeypatch.setattr(transport, "_write_json", fail_write)
    with pytest.raises(type(original)) as caught:
        _run(case)
    assert caught.value is original
    assert not case.state_path.exists()


@pytest.mark.parametrize("damage", ["missing-plan", "bad-plan", "missing-manifest", "bad-sha"])
def test_transport_cli_clears_stale_sidecar_before_input_validation(case, damage, capsys):
    args = ["run", *_args(case), "--failure-diagnostic"]
    if damage == "missing-plan":
        case.plan_path.unlink()
    elif damage == "bad-plan":
        case.plan_path.write_text(CANARY)
    elif damage == "missing-manifest":
        case.manifest_path.unlink()
    else:
        args[args.index("--sha") + 1] = CANARY
    assert transport.main(args) == 1
    assert not case.state_path.exists()
    assert capsys.readouterr() == ("", transport.ERROR + "\n")


def test_stale_receipt_and_junit_are_cleared_and_reserved_before_pytest(case, monkeypatch):
    junit = case.private / f"{PHASE}.xml"
    junit.write_text(CANARY)
    sentinel = KeyboardInterrupt(CANARY)

    def run(*_args, **_kwargs):
        assert not case.state_path.exists()
        for path in (case.receipt_path, junit):
            assert path.read_bytes() == b""
            assert os.stat(path).st_mode & 0o777 == 0o600
        raise sentinel

    monkeypatch.setattr(transport.subprocess, "run", run)
    with pytest.raises(KeyboardInterrupt) as caught:
        _run(case)
    assert caught.value is sentinel
    _assert_public(case, _export(case), status="unavailable", stage="pytest")


@pytest.mark.parametrize("location", ["output-file", "output-parent", "input-parent"])
def test_symlink_paths_are_never_followed_and_targets_remain_unchanged(case, location):
    target = case.private / "protected.txt"
    target.write_text(CANARY)
    if location == "output-file":
        case.public.mkdir()
        (case.public / "failure.json").symlink_to(target)
    elif location == "output-parent":
        case.public.symlink_to(case.private, target_is_directory=True)
    else:
        linked = case.private.parent / "linked-private"
        linked.symlink_to(case.private, target_is_directory=True)
        case.private = linked
    if location == "input-parent":
        _assert_public(case, _export(case), status="suppressed")
    else:
        with pytest.raises((OSError, transport.TransportError)):
            _export(case)
    assert target.read_text() == CANARY


@pytest.mark.parametrize("relative", ["same", "public-inside-private", "private-inside-public"])
def test_raw_and_public_output_trees_cannot_overlap(case, relative):
    if relative == "same":
        case.public = case.private
    elif relative == "public-inside-private":
        case.public = case.private / "public"
    else:
        case.public = case.private.parent
    with pytest.raises(transport.TransportError):
        _export(case)
    assert not (case.public / "failure.json").exists()


@pytest.mark.parametrize("kind", ["raw-file", "directory", "symlink-file", "symlink-directory"])
def test_public_directory_with_unapproved_sibling_is_rejected(case, kind):
    case.public.mkdir()
    sibling = case.public / "unapproved"
    if kind == "raw-file":
        sibling.write_text(CANARY)
    elif kind == "directory":
        sibling.mkdir()
        (sibling / "raw.log").write_text(CANARY)
    elif kind == "symlink-file":
        sibling.symlink_to(case.receipt_path)
    else:
        sibling.symlink_to(case.private, target_is_directory=True)
    with pytest.raises((OSError, transport.TransportError)):
        _export(case)
    assert not (case.public / "failure.json").exists()


def test_existing_diagnostic_is_replaced_with_current_safe_unavailability(case):
    ci.write(case.public / "failure.json", {"raw": CANARY})
    case.state_path.unlink()
    _assert_public(case, _export(case), status="unavailable", stage="unavailable")


@pytest.mark.parametrize("count", [256, 257])
def test_diagnostic_id_count_is_bounded_with_no_partial_list(tmp_path, monkeypatch, count):
    case = _case(tmp_path, monkeypatch, count)
    for report in case.receipt["reports"]:
        if report["when"] == "call":
            report["outcome"] = "failed"
    _save(case)
    result = _assert_public(case, _export(case), status="recorded" if count == 256 else "suppressed")
    assert len(result["tests"]) == (256 if count == 256 else 0)


@pytest.mark.parametrize("limit", ["bytes", "depth", "reports", "node", "reason"])
def test_oversized_or_excessively_nested_input_suppresses_all_ids(case, monkeypatch, limit):
    if limit == "bytes":
        monkeypatch.setattr(transport, "MAX_BYTES", 16384)
        case.receipt_path.write_text(" " * 16385)
    elif limit == "depth":
        case.receipt_path.write_text("[" * 17 + json.dumps(CANARY) + "]" * 17)
    elif limit == "reports":
        monkeypatch.setattr(transport, "MAX_REPORTS", 128)
        case.receipt["subtests"] = [dict(case.receipt["reports"][0])] * 129
        _save(case)
    elif limit == "node":
        monkeypatch.setattr(transport, "MAX_NODE", 1024)
        case.receipt["collected"].append(CANARY * 100)
        _save(case)
    else:
        case.receipt["reports"][-1].update(outcome="skipped", skip_reason=CANARY * 1000)
        _save(case)
    _assert_public(case, _export(case), status="suppressed")


@pytest.mark.parametrize("args", [[], [CANARY], ["--phase", CANARY], ["--sha", CANARY]])
def test_diagnostic_cli_parse_errors_are_fixed_and_silent_about_raw_input(args, capsys):
    assert diagnostic.main(args) == 1
    assert capsys.readouterr() == ("", "STOP: Failure diagnostic unavailable.\n")


@pytest.mark.parametrize("error", [OSError(CANARY), KeyboardInterrupt(CANARY), SystemExit(CANARY)])
def test_diagnostic_cli_export_errors_never_echo_exception_or_traceback(
    case, monkeypatch, capsys, error
):
    def fail(*_args):
        raise error

    monkeypatch.setattr(diagnostic, "export_failure", fail)
    assert diagnostic.main(_args(case)) == 1
    assert capsys.readouterr() == ("", "STOP: Failure diagnostic unavailable.\n")


def test_diagnostic_cli_valid_output_is_quiet(case, capsys):
    assert diagnostic.main(_args(case)) == 0
    _assert_public(case, ci.read(case.public / "failure.json"), status="recorded")
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    "phase",
    ["ownership", "cgroup", "local", "shard-4", CANARY],
    ids=["ownership", "cgroup", "local", "invalid-shard", "private-canary"],
)
def test_only_fixed_shard_phases_are_publishable(case, phase):
    with pytest.raises(transport.TransportError):
        _export(case, phase=phase)
    assert not (case.public / "failure.json").exists()


@pytest.mark.parametrize("mutation", ["nonzero", "failed", "xfail", "skip", "subtest-failure"])
def test_failure_diagnostic_does_not_relax_any_strict_success_validation(case, mutation):
    receipt = execution(case.example.plan, PHASE)
    if mutation == "nonzero":
        receipt["exitstatus"] = 1
    elif mutation == "failed":
        receipt["reports"][1]["outcome"] = "failed"
    elif mutation == "xfail":
        receipt["reports"][1]["wasxfail"] = True
    elif mutation == "skip":
        receipt["reports"][1].update(outcome="skipped", skip_reason=CANARY)
    else:
        receipt["subtests"] = [dict(receipt["reports"][1], outcome="failed")]
    with pytest.raises(ValueError):
        transport.project_receipt(receipt, case.example.manifest, case.example.policy)
    with pytest.raises(ValueError):
        ci.validate_execution(receipt, PHASE, case.example.plan, case.example.policy)


@pytest.mark.parametrize("placement", ["extra", "receipt", "summary", "only"])
def test_aggregate_never_accepts_failure_payload_as_success_evidence(case, placement):
    payload = _export(case)
    directory = case.private.parent / "aggregate"
    plan = materialize_public(directory, case.example)
    transport.verify(plan, directory, SHA, case.example.policy, case.example.manifest, jobs())
    if placement == "extra":
        ci.write(directory / "failure.json", payload)
    elif placement == "receipt":
        ci.write(directory / f"receipt-{PHASE}.json", payload)
    elif placement == "summary":
        ci.write(directory / "summary.json", payload)
    else:
        for path in directory.iterdir():
            path.unlink()
        ci.write(directory / "failure.json", payload)
    with pytest.raises(ValueError):
        transport.verify(plan, directory, SHA, case.example.policy, case.example.manifest, jobs())


@pytest.mark.parametrize("kind", ["reports", "subtests"])
@pytest.mark.parametrize("when", ["setup", "call", "teardown"])
def test_reviewed_skip_reason_does_not_allow_skipped_subtest_or_teardown(case, kind, when):
    case.receipt = execution(case.example.plan, PHASE)
    case.state.update(stage="outcome_validation", returncode=0)
    node = next(iter(case.example.policy["runtime_skips"]))
    report = next(
        item for item in case.receipt["reports"] if item["nodeid"] == node and item["when"] == when
    )
    skipped = dict(report, outcome="skipped", skip_reason=REASON)
    if kind == "reports":
        report.update(skipped)
        if when == "setup":
            case.receipt["reports"] = [
                item
                for item in case.receipt["reports"]
                if not (item["nodeid"] == node and item["when"] == "call")
            ]
    else:
        case.receipt["subtests"] = [skipped]
    _save(case)
    result = _assert_public(case, _export(case), status="recorded")
    rejected = kind == "subtests" or when == "teardown"
    assert result["tests"] == (
        [
            {
                "id": f"public-{case.example.manifest['nodes'].index(node)}",
                "stage": when,
                "status": "unexpected_skip",
                "kind": None,
            }
        ]
        if rejected
        else []
    )
    if rejected:
        with pytest.raises(ValueError):
            ci.validate_execution(case.receipt, PHASE, case.example.plan, case.example.policy)
    else:
        ci.validate_execution(case.receipt, PHASE, case.example.plan, case.example.policy)


def test_reviewed_canary_identity_and_reason_are_still_only_bare_ordinal(case):
    old = case.receipt["collected"][0]
    replacement = old + "[" + CANARY + "]"
    example = case.example
    for receipt in example.collections.values():
        receipt["collected"] = [replacement if node == old else node for node in receipt["collected"]]
    example.policy["runtime_skips"] = {replacement: CANARY}
    example.policy["collection_skip_reasons"] = {
        module: CANARY for module in example.policy["collection_skips"]
    }
    for receipt in example.collections.values():
        receipt["collection_skip_reasons"] = {module: CANARY for module in receipt["collection_skips"]}
    example.manifest["nodes"] = sorted(
        replacement if node == old else node for node in example.manifest["nodes"]
    )
    example.plan = ci.build_plan(example.collections, SHA, example.policy)
    case.plan = approved_plan(example)
    case.receipt = execution(example.plan, PHASE)
    case.receipt["exitstatus"] = 1
    _failure_report(case.receipt)
    # The helper's expected policy assumes one reviewed reason, which remains true.
    _save(case)
    result = _assert_public(case, _export(case), status="recorded")
    assert result["tests"] == [
        {
            "id": f"public-{example.manifest['nodes'].index(replacement)}",
            "stage": "call",
            "status": "failed",
            "kind": None,
        }
    ]


def test_partial_failure_receipt_can_diagnose_collected_failed_tests_without_qualifying(case):
    node = case.receipt["collected"][0]
    case.receipt["collected"] = [node]
    case.receipt["reports"] = [item for item in case.receipt["reports"] if item["nodeid"] == node]
    _save(case)
    result = _assert_public(case, _export(case), status="recorded")
    assert len(result["tests"]) == 1
    with pytest.raises(ValueError):
        ci.validate_execution(case.receipt, PHASE, case.example.plan, case.example.policy)


def test_diagnostics_disabled_does_not_create_sidecar_for_failure(case, monkeypatch):
    case.state_path.unlink()
    fake_execution(monkeypatch, case.receipt, 1)
    with pytest.raises(SystemExit) as error:
        _run(case, failure_diagnostic=False)
    assert error.value.code == 1
    assert not case.state_path.exists()


@pytest.mark.parametrize("returncode", [-15, 1, 137, 255])
def test_sidecar_disk_error_cannot_change_real_subprocess_exit_code(
    case, monkeypatch, capsys, returncode
):
    fake_execution(monkeypatch, case.receipt, returncode)
    writer = transport._write_json

    def write(path, value):
        if Path(path) == case.state_path:
            raise OSError(CANARY)
        return writer(path, value)

    monkeypatch.setattr(transport, "_write_json", write)
    assert transport.main(["run", *_args(case), "--failure-diagnostic"]) == returncode
    assert not case.state_path.exists()
    assert capsys.readouterr() == ("", transport.ERROR + "\n")


def test_bad_github_context_does_not_replace_original_received_failure(case, monkeypatch):
    sentinel = KeyboardInterrupt(CANARY)

    def fail(*_args):
        raise sentinel

    monkeypatch.setattr(transport, "_run_phase", fail)
    monkeypatch.setenv("GITHUB_RUN_ID", CANARY)
    with pytest.raises(KeyboardInterrupt) as error:
        _run(case)
    assert error.value is sentinel
    assert not case.state_path.exists()


def test_raw_sibling_appearing_during_output_write_prevents_success(case, monkeypatch, capsys):
    writer = transport._write_json

    def write(path, value):
        writer(path, value)
        (case.public / "raw.log").write_text(CANARY)

    monkeypatch.setattr(transport, "_write_json", write)
    assert diagnostic.main(_args(case)) == 1
    assert capsys.readouterr() == ("", "STOP: Failure diagnostic unavailable.\n")
    _assert_public(case, ci.read(case.public / "failure.json"), status="recorded")


def test_publication_disk_failure_removes_stale_output_and_has_fixed_cli_error(
    case, monkeypatch, capsys
):
    ci.write(case.public / "failure.json", {"raw": CANARY})

    def fail(*_args):
        raise OSError(CANARY)

    monkeypatch.setattr(transport, "_write_json", fail)
    assert diagnostic.main(_args(case)) == 1
    assert not (case.public / "failure.json").exists()
    assert capsys.readouterr() == ("", "STOP: Failure diagnostic unavailable.\n")
