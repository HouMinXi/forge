# SPDX-License-Identifier: Apache-2.0
"""Synthetic parser-only fixtures; these never qualify owned execution or PASS.

Hashes and summaries are derived from fixture data to model truthful projection
shape. The attestation fixture is a test input, not external execution authority.
"""

import copy
import hashlib
import inspect

import pytest

from code_forge import fixval_terminal as terminal


COMMAND = ["python3", "-m", "pytest", "tests/test_value.py"]
NODE = "tests/test_value.py::test_original"
FILE = "tests/test_value.py"


def _stream(data):
    return dict(
        bytes=len(data), retained_bytes=len(data), eof=True, sha256=hashlib.sha256(data).hexdigest()
    )


def _phase(index, name):
    green = name.startswith("fixed:")
    nonce = f"{index + 2:032x}"
    rows = [
        [NODE, FILE, "passed", "passed" if green else "failed", "passed", False],
        [FILE + "::test_other", FILE, "passed", "passed", "passed", False],
    ]
    # A compact record-shaped fixture, not a real owned envelope.
    record = dict(nonce=nonce, rows=rows)
    executable = dict(
        path="/usr/bin/python3",
        realpath="/usr/bin/python3.12",
        dev=1,
        ino=123,
        size=100,
        mtime_ns=1,
        ctime_ns=2,
        sha256=terminal.digest("fixture executable"),
    )
    return dict(
        phase=name,
        nonce=nonce,
        timeout=120,
        status="complete",
        duration=0.1,
        returncode=0 if green else 1,
        record_sha256=terminal.digest(record),
        record_bytes=len(terminal.canonical(record)),
        inventory_sha256=terminal.digest(rows),
        collection_sha256=terminal.digest([[r[0], r[1]] for r in rows]),
        collected=2,
        failed=0 if green else 1,
        passed=2 if green else 1,
        skipped=0,
        ordinary_passed=2 if green else 1,
        ordinary_failed=0 if green else 1,
        configured_argv_sha256=terminal.digest(COMMAND),
        effective_argv_sha256=terminal.digest([*COMMAND, "bootstrap:" + nonce]),
        env_sha256=terminal.digest({"capture_nonce": nonce, "PYTHONPATH": "/fixture/src"}),
        base_env_sha256=terminal.digest({"PYTHONPATH": "/fixture/src"}),
        pytest_version="9.1.1",
        cleanup_complete=True,
        retry_eligible=False,
        superseded=False,
        owner=dict(
            caller_pid=10,
            caller_start_ticks=20,
            owner_pid=100 + index * 2,
            owner_start_ticks=30 + index,
            driver_pid=101 + index * 2,
            driver_start_ticks=40 + index,
            invocation_nonce=nonce,
            resolved_executable=executable,
        ),
        diagnostic_truncated=False,
        streams=dict(stdout=_stream(b"fixture stdout\n"), stderr=_stream(b"")),
    )


def _link(phase):
    call = "passed" if phase["phase"].startswith("fixed:") else "failed"
    row = ["passed", call, "passed", False]
    return dict(
        record_sha256=phase["record_sha256"],
        row_sha256=terminal.digest([NODE, FILE, *row]),
        call=call,
        row=row,
    )


def _stage(*, retry=0, overfit=False):
    stage = terminal.new_stage("1" * 32, terminal.digest("fixture source"))
    phases = []
    if retry:
        phases = [_phase(i, f"fixed:0:{i}") for i in range(retry)]
        for phase in phases:
            phase["superseded"] = True
            phase["base_env_sha256"] = terminal.digest("original venv environment")
        failed = phases[-1]
        failed.update(
            status="error",
            returncode=None,
            record_sha256=None,
            record_bytes=0,
            inventory_sha256=None,
            collection_sha256=None,
            pytest_version=None,
            collected=0,
            failed=0,
            passed=0,
            skipped=0,
            ordinary_passed=0,
            ordinary_failed=0,
            retry_eligible=True,
            streams={},
        )
        failed["owner"].update(driver_pid=None, driver_start_ticks=None, resolved_executable=None)
    batch = 1 if retry else 0
    greens = [_phase(retry + i, f"fixed:{batch}:{i}") for i in range(3)]
    red = _phase(retry + 3, "reverted")
    phases += greens + [red]
    if overfit:
        phases.append(_phase(retry + 4, "overfit"))
    raw_bytes = (
        sum(p["record_bytes"] + sum(s["retained_bytes"] for s in p["streams"].values()) for p in phases)
        + 1000
    )
    stage.update(
        outcome="PASS",
        reason="attributable_red",
        config_sha256=terminal.digest("fixture config"),
        candidate_sha256=terminal.digest({"tests": [FILE], "production": ["src/value.py"]}),
        earned_window_sha256=terminal.digest("fixture review window"),
        raw=dict(
            directory="/retained/fixture",
            identity=[1, 2, 0],
            bytes=raw_bytes,
            files=len(phases) * 7,
            sha256=terminal.digest("fixture manifest"),
        ),
        phases=phases,
        witness=dict(
            node=NODE, file=FILE, eligible_count=1, green=[_link(p) for p in greens], red=_link(red)
        ),
        restoration="restored",
        validator_sha256=terminal.digest("fixture validator"),
        reporter_sha256=terminal.digest("fixture reporter"),
    )
    return stage


def _inputs(stage):
    return dict(
        invocation_id=stage["invocation_id"],
        source_hash=stage["source_hash"],
        config_sha256=stage["config_sha256"],
        candidate_sha256=stage["candidate_sha256"],
        earned_window_sha256=stage["earned_window_sha256"],
        validator_sha256=stage["validator_sha256"],
        reporter_sha256=stage["reporter_sha256"],
        expected_stage_sha256=terminal.digest(stage),
        candidate_files={FILE, "tests/test_other.py"},
        command_sha256=terminal.digest(COMMAND),
        expected_command=COMMAND,
    )


@pytest.mark.parametrize("retry", [0, 1, 2, 3])
@pytest.mark.parametrize("overfit", [False, True])
def test_truthful_projection_shapes_are_accepted_parser_only(retry, overfit):
    stage = _stage(retry=retry, overfit=overfit)
    assert terminal.validate_stage(stage) is stage
    assert terminal.validate_terminal_stage(stage, **_inputs(stage)) is True
    authoritative = stage["phases"][retry : retry + 4]
    assert len({p["env_sha256"] for p in authoritative}) == 4
    assert len({p["effective_argv_sha256"] for p in authoritative}) == 4


def test_cleaned_up_incomplete_overfit_is_only_advisory():
    stage = _stage(overfit=True)
    phase = stage["phases"][-1]
    phase.update(
        status="error",
        returncode=None,
        record_sha256=None,
        record_bytes=0,
        inventory_sha256=None,
        collection_sha256=None,
        pytest_version=None,
        collected=0,
        failed=0,
        passed=0,
        skipped=0,
        ordinary_passed=0,
        ordinary_failed=0,
        streams={},
    )
    phase["owner"].update(driver_pid=None, driver_start_ticks=None, resolved_executable=None)
    assert terminal.validate_terminal_stage(stage, **_inputs(stage))
    phase["cleanup_complete"] = False
    with pytest.raises(terminal.EvidenceError, match="cleanup"):
        terminal.validate_stage(stage)


@pytest.mark.parametrize(
    "name,mutate",
    [
        ("witness_node", lambda s: s["witness"].update(node=FILE + "::NEVER_EXECUTED")),
        ("witness_file", lambda s: s["witness"].update(file="tests/test_other.py")),
        ("empty_node", lambda s: s["witness"].update(node="")),
        ("green_failed", lambda s: s["phases"][0].update(collected=3, failed=1, ordinary_failed=1)),
        ("inflated_eligible", lambda s: s["witness"].update(eligible_count=49999)),
        ("missing_runtime", lambda s: s["phases"][3]["owner"].update(resolved_executable=None)),
        ("red_base_env", lambda s: s["phases"][3].update(base_env_sha256="1" * 64)),
        ("exceeded_deadline", lambda s: s["phases"][0].update(duration=999999)),
        ("phase_order", lambda s: s["phases"].reverse()),
        ("missing_streams", lambda s: s["phases"][0].update(streams={})),
        ("same_pids", lambda s: s["phases"][0]["owner"].update(owner_pid=10, driver_pid=10)),
        ("framework_witness", lambda s: s["witness"]["red"]["row"].__setitem__(3, True)),
        ("setup_failed", lambda s: s["witness"]["red"]["row"].__setitem__(0, "failed")),
        ("missing_row", lambda s: s["witness"]["red"].pop("row")),
        ("invented_row_hash", lambda s: s["witness"]["red"].update(row_sha256="1" * 64)),
        (
            "duplicate_record",
            lambda s: s["phases"][1].update(record_sha256=s["phases"][0]["record_sha256"]),
        ),
        ("duplicate_nonce", lambda s: s["phases"][1].update(nonce=s["phases"][0]["nonce"])),
        ("duplicate_phase", lambda s: s["phases"][1].update(phase=s["phases"][0]["phase"])),
        ("changed_collection", lambda s: s["phases"][3].update(collection_sha256="1" * 64)),
        ("changed_inventory", lambda s: s["phases"][1].update(inventory_sha256="1" * 64)),
        ("changed_count", lambda s: s["phases"][1].update(collected=3, passed=3, ordinary_passed=3)),
        ("changed_caller", lambda s: s["phases"][3]["owner"].update(caller_start_ticks=999)),
        ("changed_version", lambda s: s["phases"][3].update(pytest_version="different")),
        ("changed_executable", lambda s: s["phases"][3]["owner"]["resolved_executable"].update(ino=999)),
        ("changed_command", lambda s: s["phases"][3].update(configured_argv_sha256="1" * 64)),
        ("eof_missing", lambda s: s["phases"][0]["streams"]["stdout"].update(eof=False)),
        ("lying_truncation", lambda s: s["phases"][0].update(diagnostic_truncated=True)),
        ("not_cleaned", lambda s: s["phases"][0].update(cleanup_complete=False)),
        ("small_manifest", lambda s: s["raw"].update(bytes=1)),
        (
            "arbitrary_executable",
            lambda s: s["phases"][0]["owner"]["resolved_executable"].update(extra=True),
        ),
        ("arbitrary_owner", lambda s: s["phases"][0]["owner"].update(extra=True)),
    ],
)
def test_reviewer_mutations_fail_semantic_parser_checks(name, mutate):
    stage = _stage()
    mutate(stage)
    with pytest.raises(terminal.EvidenceError):
        terminal.validate_stage(stage)


@pytest.mark.parametrize("field", ["env_sha256", "effective_argv_sha256"])
def test_dynamic_hash_mutation_requires_independent_output_attestation(field):
    stage = _stage()
    admitted = _inputs(stage)
    stage["phases"][3][field] = terminal.digest("valid-looking but altered capture input")
    # A pure parser cannot derive these nonce-dependent values from base_env_sha256.
    terminal.validate_stage(stage)
    with pytest.raises(terminal.EvidenceError, match="independently attested"):
        terminal.validate_terminal_stage(stage, **admitted)


def test_output_attestation_is_mandatory_and_self_hash_is_not_claimed_as_authority():
    stage = _stage()
    args = _inputs(stage)
    args.pop("expected_stage_sha256")
    assert (
        inspect.signature(terminal.validate_terminal_stage).parameters["expected_stage_sha256"].default
        is inspect.Parameter.empty
    )
    with pytest.raises(TypeError):
        terminal.validate_terminal_stage(stage, **args)
    args["expected_stage_sha256"] = None
    with pytest.raises(terminal.EvidenceError, match="independently attested"):
        terminal.validate_terminal_stage(stage, **args)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s["phases"][0].update(superseded=False),
        lambda s: s["phases"][0].update(retry_eligible=True),
        lambda s: s["phases"][1].update(retry_eligible=False),
        lambda s: s["phases"][1].update(status="timeout"),
        lambda s: s["phases"][2].update(superseded=True),
        lambda s: s["phases"].pop(0),
        lambda s: s["phases"].__setitem__(slice(0, 2), reversed(s["phases"][:2])),
    ],
)
def test_invalid_retry_history_is_rejected(mutate):
    stage = _stage(retry=2)
    mutate(stage)
    with pytest.raises(terminal.EvidenceError):
        terminal.validate_stage(stage)


@pytest.mark.parametrize(
    "target,key,value",
    [
        ("stage", "version", True),
        ("stage", "source_hash", None),
        ("stage", "outcome", []),
        ("stage", "restoration", {}),
        ("stage", "config_sha256", "A" * 64),
        ("stage", "reason", 1),
        ("phase", "nonce", 12),
        ("phase", "timeout", True),
        ("phase", "timeout", 86401),
        ("phase", "duration", True),
        ("phase", "duration", float("nan")),
        ("phase", "duration", float("inf")),
        ("phase", "duration", 10**400),
        ("phase", "record_bytes", True),
        ("phase", "returncode", False),
        ("phase", "passed", True),
        ("phase", "collected", 50001),
        ("phase", "streams", []),
        ("phase", "retry_eligible", 0),
        ("phase", "superseded", "false"),
        ("phase", "pytest_version", 1),
        ("phase", "record_sha256", []),
        ("phase", "env_sha256", None),
        ("owner", "caller_pid", True),
        ("owner", "driver_start_ticks", 0),
        ("owner", "invocation_nonce", []),
        ("owner", "resolved_executable", "/usr/bin/python"),
        ("witness", "eligible_count", True),
        ("witness", "eligible_count", 0),
        ("witness", "file", "tests//test_value.py"),
        ("witness", "file", "../test_value.py"),
        ("witness", "file", "./tests/test_value.py"),
        ("witness", "file", "/tests/test_value.py"),
        ("witness", "green", {}),
        ("witness", "red", []),
        ("raw", "identity", [1, True, 0]),
        ("raw", "bytes", True),
        ("raw", "files", 65),
    ],
)
def test_malformed_types_fail_closed_as_evidence_error(target, key, value):
    stage = _stage()
    obj = {
        "stage": stage,
        "phase": stage["phases"][0],
        "owner": stage["phases"][0]["owner"],
        "witness": stage["witness"],
        "raw": stage["raw"],
    }[target]
    obj[key] = value
    with pytest.raises(terminal.EvidenceError):
        terminal.validate_stage(stage)


@pytest.mark.parametrize(
    "target,key,value",
    [
        ("executable", "size", True),
        ("executable", "ino", 0),
        ("executable", "path", "python"),
        ("executable", "realpath", None),
        ("executable", "mtime_ns", "1"),
        ("executable", "sha256", []),
        ("stream", "bytes", True),
        ("stream", "retained_bytes", -1),
        ("stream", "retained_bytes", 100),
        ("stream", "eof", 1),
        ("stream", "sha256", "bad"),
    ],
)
def test_closed_nested_metadata_types(target, key, value):
    stage = _stage()
    phase = stage["phases"][0]
    obj = phase["owner"]["resolved_executable"] if target == "executable" else phase["streams"]["stdout"]
    obj[key] = value
    with pytest.raises(terminal.EvidenceError):
        terminal.validate_stage(stage)


def test_combined_retention_bound_and_truthful_truncation():
    stage = _stage()
    phase = stage["phases"][0]
    for row in phase["streams"].values():
        row.update(bytes=700000, retained_bytes=600000, sha256="f" * 64)
    phase["diagnostic_truncated"] = True
    with pytest.raises(terminal.EvidenceError, match="bound/truncation"):
        terminal.validate_stage(stage)
    phase["streams"]["stderr"]["retained_bytes"] = 100
    stage["raw"]["bytes"] = 2_000_000
    assert terminal.validate_stage(stage) is stage


def test_witness_and_stage_size_bounds():
    stage = _stage()
    stage["witness"]["node"] = "x" * terminal.MAX_WITNESS_BYTES
    with pytest.raises(terminal.EvidenceError, match="witness"):
        terminal.validate_stage(stage)
    stage = _stage()
    stage["reason"] = "x" * terminal.MAX_STAGE_BYTES
    with pytest.raises(terminal.EvidenceError, match="overflow"):
        terminal.validate_stage(stage)


@pytest.mark.parametrize(
    "outcome,reason,exception",
    [
        ("pending", "pending", None),
        ("ERROR", "execution", None),
        ("BLOCK", "hollow", None),
        ("SKIPPED", "no_tests", None),
        ("WAIVED", "explicit_waiver", {"reason": "nondeterministic fixture", "channel": "env"}),
    ],
)
def test_nonpass_state_shapes_do_not_need_execution_proof(outcome, reason, exception):
    stage = terminal.new_stage("1" * 32, "a" * 64)
    stage.update(outcome=outcome, reason=reason, exception=exception)
    assert terminal.validate_stage(stage) is stage
    stage["earned_window_sha256"] = "b" * 64
    args = _inputs(stage)
    if outcome in {"SKIPPED", "WAIVED"}:
        args["exception"] = dict(outcome=outcome, reason=reason, exception=exception)
        assert terminal.validate_terminal_stage(stage, **args)
    else:
        with pytest.raises(terminal.EvidenceError, match="did not pass"):
            terminal.validate_terminal_stage(stage, **args)


@pytest.mark.parametrize(
    "key,value",
    [
        ("invocation_id", "a" * 32),
        ("source_hash", "b" * 64),
        ("config_sha256", "b" * 64),
        ("candidate_sha256", "b" * 64),
        ("earned_window_sha256", "b" * 64),
        ("validator_sha256", "b" * 64),
        ("reporter_sha256", "b" * 64),
        ("candidate_files", {"tests/unrelated.py"}),
        ("candidate_files", FILE),
        ("command_sha256", "b" * 64),
        ("expected_command", ["python", "-m", "pytest"]),
        ("expected_stage_sha256", "b" * 64),
    ],
)
def test_admitted_inputs_are_checked_independently(key, value):
    stage = _stage()
    args = _inputs(stage)
    args[key] = value
    with pytest.raises(terminal.EvidenceError):
        terminal.validate_terminal_stage(stage, **args)


def test_parser_does_not_mutate_projection():
    stage = _stage(retry=3, overfit=True)
    before = copy.deepcopy(stage)
    terminal.validate_terminal_stage(stage, **_inputs(stage))
    assert stage == before


@pytest.mark.parametrize(
    "key,value",
    [
        ("returncode", 0),
        ("record_bytes", 1),
        ("record_sha256", "a" * 64),
        ("inventory_sha256", "a" * 64),
        ("pytest_version", "9.1.1"),
    ],
)
def test_startup_retry_cannot_claim_completed_pytest_inventory(key, value):
    stage = _stage(retry=1)
    stage["phases"][0][key] = value
    with pytest.raises(terminal.EvidenceError, match="startup-only"):
        terminal.validate_stage(stage)


def test_owner_incarnations_are_distinct_across_repeated_executions():
    stage = _stage()
    first = stage["phases"][0]["owner"]
    second = stage["phases"][1]["owner"]
    second.update(owner_pid=first["owner_pid"], owner_start_ticks=first["owner_start_ticks"])
    with pytest.raises(terminal.EvidenceError, match="incarnation reused"):
        terminal.validate_stage(stage)
    second["owner_start_ticks"] += 1  # PID reuse with a new incarnation is not replay.
    assert terminal.validate_stage(stage) is stage


def test_unknown_pytest_version_fails_even_when_all_phases_agree():
    stage = _stage()
    for phase in stage["phases"]:
        phase["pytest_version"] = "999.0.0"
    with pytest.raises(terminal.EvidenceError, match="pytest version"):
        terminal.validate_stage(stage)


def test_complete_empty_overfit_inventory_remains_advisory():
    stage = _stage(overfit=True)
    stage["phases"][-1].update(
        returncode=5,
        collected=0,
        failed=0,
        passed=0,
        skipped=0,
        ordinary_passed=0,
        ordinary_failed=0,
        inventory_sha256=terminal.digest([]),
        collection_sha256=terminal.digest([]),
    )
    assert terminal.validate_terminal_stage(stage, **_inputs(stage))


@pytest.mark.parametrize("outcome", ["SKIPPED", "WAIVED"])
def test_exception_output_still_requires_independent_attestation(outcome):
    stage = terminal.new_stage("1" * 32, "a" * 64)
    waiver = {"channel": "env", "reason": "fixture waiver"} if outcome == "WAIVED" else None
    stage.update(
        outcome=outcome,
        reason="explicit_waiver" if waiver else "no_tests",
        exception=waiver,
        earned_window_sha256="b" * 64,
    )
    args = _inputs(stage)
    args["exception"] = {key: stage[key] for key in ("outcome", "reason", "exception")}
    args["expected_stage_sha256"] = "f" * 64
    with pytest.raises(terminal.EvidenceError, match="independently attested"):
        terminal.validate_terminal_stage(stage, **args)
