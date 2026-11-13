"""Synthetic offline evidence only; no namespace, product, or target interpreter runs."""

import importlib.util
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


obs = load(ROOT / "scripts/cli_capture_observations.py")
pub = load(ROOT / "scripts/cli_capture_observations_publication.py")
fixtures = load(ROOT / "tests/test_cli_capture_public_summary.py")
entry_fixtures = load(ROOT / "tests/test_cli_capture_entrypoint.py")
evidence = entry_fixtures.evidence


def write(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_bytes(value if isinstance(value, bytes) else (json.dumps(value) + "\n").encode())
    path.chmod(0o600)


def event_rows():
    base = {"schema": 1, "pid": 1234, "tid": 1234, "monotonic_ns": 1000, "time_ns": 2000}
    request = {"host_id": 1, "operation_id": 1, "phase": "open", "deadline_ns": None, "attempt": 0}
    rows = [
        base | {"event": "diagnostic_start"},
        base | request | {"event": "host_request_begin"},
        base | request | {"event": "host_request_end"},
        base | {"event": "diagnostic_footer", "complete": True} | dict.fromkeys(obs.PENDING, 0),
    ]
    for i, row in enumerate(rows, 1):
        row.update(seq=i, monotonic_ns=1000 + i, time_ns=2000 + i)
    return rows


def encode_rows(rows):
    return b"".join((json.dumps(row) + "\n").encode() for row in rows)


@pytest.fixture
def captured(tmp_path):
    root = tmp_path / "capture"
    record = fixtures.receipt()
    record["worker"]["peer_transport"] = {"requests": 2, "exchanges": 2, "errors": []}
    write(root / "result.json", record)
    write(root / "worker/diagnostic/events.jsonl", encode_rows(event_rows()))
    write(
        root / "worker/diagnostic/syscalls.log",
        b"1234 1700000000.000000 fsync(4</PRIVATE/CANARY>) = 0 <0.000001>\n1234 1700000001.000000 +++ exited with 7 +++\n",
    )
    return root, record


def test_actual_offline_cli_closed_pipeline_preserves_seven(captured):
    root, _ = captured
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            str(ROOT / "scripts/cli_capture_observations.py"),
            "--capture-root",
            str(root),
            "--mode",
            "diagnostic",
            "--shell-exit",
            "7",
        ],
        capture_output=True,
    )
    assert result.returncode == 0 and not result.stderr
    row = pub.validate(json.loads(result.stdout))
    assert row["shell_exit"] == row["cli_exit"] == 7 and row["worker_exit"] == 0
    assert row["fixture_outcome"] == "UNASSESSED"
    assert row["event_integrity"] == row["trace_integrity"] == "COMPLETE"
    assert row["event_count"] == 4 and row["trace_count"] == 2 and row["sync_count"] == 1
    assert (
        row["cross_stream_order"]
        == row["ack_consistency"]
        == row["power_loss_durability"]
        == "NOT_ASSESSED"
    )
    assert b"PRIVATE" not in result.stdout and b"1234" not in result.stdout


@pytest.mark.parametrize(
    "fault",
    [
        "unknown",
        "extra",
        "bool",
        "sequence",
        "pid",
        "tid",
        "clock",
        "unpaired",
        "mismatch",
        "footer",
        "pending",
        "trailing",
        "unsupported",
    ],
)
def test_event_shapes_and_lifecycles_fail_closed(captured, fault):
    root, _ = captured
    rows = event_rows()
    if fault == "unknown":
        rows[1]["event"] = "PRIVATE_CANARY"
    elif fault == "extra":
        rows[1]["secret"] = "PRIVATE_CANARY"
    elif fault == "bool":
        rows[1]["host_id"] = True
    elif fault == "sequence":
        rows[2]["seq"] = 4
    elif fault == "pid":
        rows[1]["pid"] = 4321
    elif fault == "tid":
        rows[2]["tid"] = 4321
    elif fault == "clock":
        rows[2]["monotonic_ns"] = 1
    elif fault == "unpaired":
        rows[2]["event"] = "host_request_begin"
    elif fault == "mismatch":
        rows[2]["host_id"] = 2
    elif fault == "footer":
        rows[-1]["complete"] = False
    elif fault == "pending":
        rows[-1]["open_tickets"] = 1
    elif fault == "unsupported":
        rows[1]["event"] = "ticket_create"
    raw = encode_rows(rows)
    if fault == "trailing":
        raw += b"PRIVATE_CANARY"
    write(root / "worker/diagnostic/events.jsonl", raw)
    result = obs.analyze(root, "diagnostic", 7)
    assert result["event_integrity"] == "INCOMPLETE" and result["event_count"] is None
    assert result["event_reason"] == (
        "UNSUPPORTED_EVENT_SHAPE" if fault == "unsupported" else "MALFORMED_OR_INCOMPLETE"
    )
    assert "PRIVATE" not in pub.encode(result)


@pytest.mark.parametrize("fault", ["missing-terminal", "bad-syscall", "unknown", "empty", "nonutf8"])
def test_trace_parser_failure_never_earns_complete(captured, fault):
    root, _ = captured
    raw = {
        "missing-terminal": b"1234 1.000000 fsync(4) = 0 <0.000001>\n",
        "bad-syscall": b"1234 1.000000 madeup() = 0 <0.000001>\n",
        "unknown": b"PRIVATE_CANARY\n",
        "empty": b"",
        "nonutf8": b"\xff\n",
    }[fault]
    write(root / "worker/diagnostic/syscalls.log", raw)
    row = obs.analyze(root, "diagnostic", 7)
    assert row["trace_integrity"] == "INCOMPLETE" and row["trace_count"] is row["sync_count"] is None
    assert row["power_loss_durability"] == "NOT_ASSESSED"


@pytest.mark.parametrize(
    "fault", ["remaining", "not-clean", "primary", "cleanup", "outer", "worker-code", "cache", "shell"]
)
def test_no_stream_reads_before_confirmed_quiescence(captured, monkeypatch, fault):
    root, record = captured
    if fault == "remaining":
        record["ownership"]["owned"][0]["remaining"] = True
    elif fault == "not-clean":
        record["ownership"]["cleanup_complete"] = False
    elif fault == "primary":
        record["worker"]["primary_exception"] = {"type": "ValueError", "message": "PRIVATE"}
    elif fault == "cleanup":
        record["worker"]["cleanup_errors"] = [{"type": "ValueError"}]
    elif fault == "outer":
        record["outer_exception"] = {"type": "ValueError", "message": "PRIVATE"}
    elif fault == "worker-code":
        record["worker_returncode"] = 1
    elif fault == "cache":
        record["final_collector_health"]["cache_health"]["status"] = "failed"
    write(root / "result.json", record)
    original = obs.read_private

    def guarded(path, *args):
        assert Path(path).name == "result.json"
        return original(path, *args)

    monkeypatch.setattr(obs, "read_private", guarded)
    row = obs.analyze(root, "diagnostic", 0 if fault == "shell" else 7)
    assert row["receipt_status"] == "INVALID" and row["fixture_outcome"] == "UNASSESSED"


@pytest.mark.parametrize(
    "fault", ["extra", "enum", "bool", "negative", "huge", "ack", "fixture", "complete", "scope"]
)
def test_independent_publication_rejects_unearned_fields(fault):
    row = obs.empty("diagnostic", 7)
    if fault == "extra":
        row["raw"] = "PRIVATE"
    elif fault == "enum":
        row["fixture_outcome"] = "PRIVATE"
    elif fault == "bool":
        row["event_count"] = True
    elif fault == "negative":
        row["event_count"] = -1
    elif fault == "huge":
        row["event_count"] = 200001
    elif fault == "ack":
        row["ack_consistency"] = "CONFIRMED"
    elif fault == "fixture":
        row["fixture_outcome"] = "BOUND_EXPECTED_UNRELIABLE"
    elif fault == "complete":
        row["event_integrity"] = "COMPLETE"
    elif fault == "scope":
        row["event_scope"] = "FULL"
    with pytest.raises(ValueError):
        pub.validate(row)


@pytest.mark.parametrize("fault", ["symlink", "hardlink", "mode", "parent", "fifo"])
def test_private_reader_rejects_unsafe_files(tmp_path, fault):
    path = tmp_path / "private/data"
    write(path, b"{}\n")
    if fault == "symlink":
        moved = tmp_path / "moved"
        path.rename(moved)
        path.symlink_to(moved)
    elif fault == "hardlink":
        os.link(path, tmp_path / "other")
    elif fault == "mode":
        path.chmod(0o644)
    elif fault == "parent":
        path.parent.chmod(0o755)
    elif fault == "fifo":
        path.unlink()
        os.mkfifo(path, 0o600)
    with pytest.raises((ValueError, OSError)):
        obs.read_private(path)


def test_fixture_source_expectation_not_current_hash_alone(evidence, captured, monkeypatch):
    root, record = captured

    def never(*args, **kwargs):
        raise AssertionError("must not start target interpreter")

    monkeypatch.setattr(obs.controller, "interpreter_identity", never)
    provenance, _, _ = obs.controller.verify_installation(evidence.root, owner_only=True)
    binding = {"installed_provenance": provenance, "capture_hashes": {"capture_worker.py": "0" * 64}}
    write(root / "binding.json", binding)
    for index in (0, 1):
        write(root / "worker" / f"response-{index}.json", obs.fixed_response(index))
    row = obs.analyze(root, "diagnostic", 7, evidence=evidence.root, expected_sha=entry_fixtures.COMMIT)
    # The synthetic archive/product is not the reviewed real source, despite valid RECORD.
    assert row["fixture_outcome"] == "UNASSESSED"
    assert row["cli_exit"] == 7


@pytest.fixture
def bound_fixture(evidence, captured, tmp_path, monkeypatch):
    """Synthetic reviewed tree and wheel; exercises binding, not product qualification."""
    root, record = captured
    reviewed_root = tmp_path / "reviewed"
    archive_path = evidence.root / "source.tar"
    with tarfile.open(archive_path, "r:") as bundle:
        members = {member.name: bundle.extractfile(member).read() for member in bundle.getmembers()}
    members["qualification/installed_cli/capture_worker.py"] = (
        ROOT / "qualification/installed_cli/capture_worker.py"
    ).read_bytes()
    for name, raw in members.items():
        write(reviewed_root / name, raw)
    archive = io.BytesIO()
    with tarfile.open(
        fileobj=archive,
        mode="w",
        format=tarfile.PAX_FORMAT,
        pax_headers={"comment": entry_fixtures.COMMIT},
    ) as bundle:
        for name, raw in members.items():
            member = tarfile.TarInfo(name)
            member.size = len(raw)
            bundle.addfile(member, io.BytesIO(raw))
    write(archive_path, archive.getvalue())
    evidence.proof["source_archive_sha256"] = hashlib.sha256(archive.getvalue()).hexdigest()
    write(evidence.root / "build-proof.json", evidence.proof)
    provenance, _, _ = obs.controller.verify_installation(evidence.root, owner_only=True)
    binding = {
        "mode": "diagnostic",
        "installed_provenance": provenance,
        "capture_hashes": {
            "capture_worker.py": hashlib.sha256(
                members["qualification/installed_cli/capture_worker.py"]
            ).hexdigest()
        },
    }
    write(root / "binding.json", binding)
    for index in (0, 1):
        write(root / "worker" / f"response-{index}.json", obs.fixed_response(index))
    monkeypatch.setattr(obs, "ROOT", reviewed_root)

    def never(*args, **kwargs):
        raise AssertionError("offline analysis must not start a target interpreter")

    monkeypatch.setattr(obs.controller, "interpreter_identity", never)
    return root, record, evidence, binding, reviewed_root


def test_complete_synthetic_binding_classifies_expected_seven(bound_fixture):
    root, _, evidence, _, _ = bound_fixture
    row = obs.analyze(root, "diagnostic", 7, evidence=evidence.root, expected_sha=entry_fixtures.COMMIT)
    assert row["fixture_outcome"] == "BOUND_EXPECTED_UNRELIABLE"
    assert row["shell_exit"] == row["cli_exit"] == 7
    assert row["execution_identity"] == "UNASSESSED"
    assert row["cross_stream_order"] == row["ack_consistency"] == "NOT_ASSESSED"
    assert row["power_loss_durability"] == row["qualification"] == "NOT_ASSESSED"


@pytest.mark.parametrize(
    "fault",
    [
        "expected-source",
        "provenance",
        "helper-hash",
        "helper-source",
        "product-source",
        "response",
        "response-missing",
        "response-extra",
        "response-number-type",
        "transport-count",
        "transport-number-type",
        "transport-error",
        "mode",
    ],
)
def test_fixture_binding_counterexamples_never_classify(bound_fixture, fault):
    root, record, evidence, binding, reviewed = bound_fixture
    expected = entry_fixtures.COMMIT
    if fault == "expected-source":
        expected = "f" * 40
    elif fault == "provenance":
        binding["installed_provenance"]["source_commit"] = "f" * 40
    elif fault == "helper-hash":
        binding["capture_hashes"]["capture_worker.py"] = "f" * 64
    elif fault == "helper-source":
        write(reviewed / "qualification/installed_cli/capture_worker.py", b"changed")
    elif fault == "product-source":
        write(reviewed / "src/code_forge_launcher.py", b"changed")
    elif fault.startswith("response"):
        path = root / "worker/response-1.json"
        response = obs.fixed_response(1)
        if fault == "response-missing":
            path.unlink()
        else:
            if fault == "response":
                response = obs.fixed_response(0)
            elif fault == "response-extra":
                response["private"] = "PRIVATE_CANARY"
            else:
                response["usage"]["prompt_tokens"] = 2.0
            write(path, response)
    elif fault == "transport-count":
        record["worker"]["peer_transport"]["requests"] = 3
    elif fault == "transport-number-type":
        record["worker"]["peer_transport"]["requests"] = 2.0
    elif fault == "transport-error":
        record["worker"]["peer_transport"]["errors"] = ["PRIVATE_CANARY"]
    elif fault == "mode":
        binding["mode"] = "baseline"
    write(root / "binding.json", binding)
    write(root / "result.json", record)
    row = obs.analyze(root, "diagnostic", 7, evidence=evidence.root, expected_sha=expected)
    assert row["fixture_outcome"] == "UNASSESSED"
    assert row["shell_exit"] == row["cli_exit"] == 7
    assert "PRIVATE" not in pub.encode(row)


def test_deep_receipt_is_closed_rejection(captured):
    root, _ = captured
    write(root / "result.json", b"[" * 10000 + b"0" + b"]" * 10000)
    row = obs.analyze(root, "diagnostic", 7)
    assert row["receipt_status"] == "INVALID"
    assert row["shell_exit"] == 7


def test_stream_counts_do_not_claim_same_execution(captured):
    root, _ = captured
    write(root / "worker/diagnostic/syscalls.log", b"4321 1.000000 +++ exited with 0 +++\n")
    row = obs.analyze(root, "diagnostic", 7)
    assert row["trace_integrity"] == row["event_integrity"] == "COMPLETE"
    assert row["execution_identity"] == "UNASSESSED"
    assert row["cross_stream_order"] == "NOT_ASSESSED"
    assert row["trace_scope"] == "SUPPORTED_SYNTAX_AND_PER_TASK_FRAMING"


@pytest.mark.parametrize("bad_cache", [None, [], "PRIVATE_CANARY", 1, True])
def test_malformed_cache_health_is_closed_cli_rejection(captured, bad_cache):
    root, record = captured
    record["final_collector_health"]["cache_health"] = bad_cache
    write(root / "result.json", record)
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            str(ROOT / "scripts/cli_capture_observations.py"),
            "--capture-root",
            str(root),
            "--mode",
            "diagnostic",
            "--shell-exit",
            "7",
        ],
        capture_output=True,
    )
    assert result.returncode == 0 and not result.stderr
    row = pub.validate(json.loads(result.stdout))
    assert row["receipt_status"] == "INVALID" and row["shell_exit"] == 7
    assert b"PRIVATE" not in result.stdout


@pytest.mark.parametrize("fault", ["deep-proof", "deep-url", "record-limit"])
def test_bad_physical_evidence_is_closed_cli_rejection(bound_fixture, fault):
    root, _, evidence, _, _ = bound_fixture
    if fault == "deep-proof":
        path = evidence.root / "build-proof.json"
        raw = b"[" * 10000 + b"0" + b"]" * 10000
    elif fault == "deep-url":
        path = evidence.site / entry_fixtures.DIST / "direct_url.json"
        raw = b"[" * 10000 + b"0" + b"]" * 10000
    else:
        path = evidence.site / entry_fixtures.DIST / "RECORD"
        raw = b"x" * 200000 + b",sha256=x,1\n"
    write(path, raw)
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            str(ROOT / "scripts/cli_capture_observations.py"),
            "--capture-root",
            str(root),
            "--mode",
            "diagnostic",
            "--shell-exit",
            "7",
            "--wheel-evidence",
            str(evidence.root),
            "--expected-source-sha",
            entry_fixtures.COMMIT,
        ],
        capture_output=True,
    )
    assert result.returncode == 0 and not result.stderr
    row = pub.validate(json.loads(result.stdout))
    assert row["fixture_outcome"] == "UNASSESSED" and row["shell_exit"] == 7
