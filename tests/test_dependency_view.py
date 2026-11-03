"""Mocked privileged operations only; never invoke sudo or a manager."""

import importlib.util
from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

path = Path(__file__).resolve().parents[1] / "scripts/dependency_view.py"
spec = importlib.util.spec_from_file_location("dependency_view", path)
view = importlib.util.module_from_spec(spec)
spec.loader.exec_module(view)


def test_exact_two_process_argv_and_no_self():
    args = view.argv(123, 456)
    assert args == [
        "/usr/bin/sudo",
        "-n",
        "--",
        "/usr/bin/timeout",
        "--signal=TERM",
        "--kill-after=1s",
        "--",
        "2s",
        "/usr/bin/stat",
        "--dereference",
        "--printf=%d:%i\\n",
        "--",
        "/proc/123/ns/mnt",
        "/proc/123/root",
        "/proc/456/ns/mnt",
        "/proc/456/root",
    ]
    for pid in (0, -1, True, 2**31, "123"):
        with pytest.raises(ValueError):
            view.argv(pid, 456)


def test_sample_accepts_only_bounded_exact_output():
    good = {
        "stdout": b"1:2\n3:4\n1:2\n3:4\n",
        "reason": None,
        "exit_code": 0,
        "stderr_bytes": 0,
        "direct_sudo_reaped": True,
    }
    base = SimpleNamespace(ENV={}, capture=Mock(return_value=good))
    assert view.stat_sample(base, 123, 456, time.monotonic() + 10) == [[1, 2], [3, 4], [1, 2], [3, 4]]
    assert base.capture.call_count == 1
    assert base.capture.call_args.kwargs["privileged"] is True
    for changed in (
        {"stdout": b"1:2\n"},
        {"stderr_bytes": 1},
        {"exit_code": 1},
        {"reason": "timeout"},
        {"direct_sudo_reaped": False},
        {"stdout": b"-1:2\n3:4\n1:2\n3:4\n"},
    ):
        base.capture.return_value = {**good, **changed}
        with pytest.raises(ValueError):
            view.stat_sample(base, 123, 456, time.monotonic() + 10)


def test_observe_uses_at_most_two_fixed_samples_and_fails_closed(monkeypatch):
    from contextlib import contextmanager

    record = {
        "Id": "basic.target",
        "FragmentPath": "/root/example",
        "NeedDaemonReload": False,
        "Transient": False,
        "RequiresMountsFor": ["/private-path"],
    }
    snapshot = {
        "manager": {"UnitPath": ["/root"], "ControlGroup": "/private-cgroup"},
        "roster": [],
        "jobs": [],
        "units": [{"properties": record}],
    }
    base = SimpleNamespace(
        tools_snapshot=Mock(return_value={"tool": "same"}),
        starttime=Mock(return_value=91),
        ordinary=Mock(return_value=b"timeout (GNU coreutils) 9.4\n"),
        file_bytes=Mock(return_value=(b"\x7fELF", (1, 2))),
        live=Mock(),
    )
    bus = SimpleNamespace(close=Mock())

    @contextmanager
    def channel(*args):
        yield object(), lambda: None, {"pid": 123, "uid": 1000, "starttime": 90}

    probe = SimpleNamespace(
        ordinary_identity=Mock(),
        metadata=lambda: base,
        need=lambda value, reason: None,
        C=SimpleNamespace(CDLL=lambda path: object()),
        verified_channel=channel,
        Bus=lambda *args: bus,
        collect=lambda *args: snapshot,
        remaining=lambda deadline: None,
    )
    candidate = SimpleNamespace(
        derive=lambda value: {
            "status": "DERIVED_OFFLINE_ONLY",
            "candidates": ["basic.target"],
            "inbound_coverage": "REPORTED_FIELDS_ONLY",
        }
    )
    fragment = SimpleNamespace(
        collect_fragment_hashes=lambda *args, **kwargs: {
            "status": "HASHED",
            "fragment_sha256": ["a" * 64],
        }
    )
    monkeypatch.setattr(view.os, "pidfd_open", lambda pid: 42)
    monkeypatch.setattr(view.os, "close", lambda fd: None)
    monkeypatch.setattr(view.Path, "resolve", lambda self, strict: self)
    monkeypatch.setattr(view, "recheck_snapshot", lambda *args: None)
    sample = [[1, 2], [3, 4], [1, 2], [3, 4]]
    stat = Mock(return_value=sample)
    monkeypatch.setattr(view, "stat_sample", stat)
    result = view.observe(probe, candidate, fragment)
    assert result["status"] == "OBSERVED"
    assert result["candidates"]["inbound_coverage"] == "NOT_ESTABLISHED"
    assert result["fragments"]["unit_ids"] == ["basic.target"]
    assert stat.call_count == 2
    assert result["qualification"] == "NOT_RUN"
    assert "private-path" not in repr(result) and "private-cgroup" not in repr(result)
    stat.reset_mock(side_effect=True)
    stat.side_effect = [sample, [[9, 9], *sample[1:]]]
    assert view.observe(probe, candidate, fragment)["status"] == "UNKNOWN"
    assert stat.call_count == 2
    stat.reset_mock(side_effect=True)
    fragment.collect_fragment_hashes = lambda *args, **kwargs: {"status": "UNKNOWN"}
    assert view.observe(probe, candidate, fragment)["status"] == "UNKNOWN"
    assert stat.call_count == 1
    fragment.collect_fragment_hashes = lambda *args, **kwargs: {
        "status": "HASHED",
        "fragment_sha256": ["a" * 64],
    }
    stat.reset_mock(side_effect=True)
    stat.return_value = [[9, 9], *sample[1:]]
    assert view.observe(probe, candidate, fragment)["phase"] == "first_stat"
    assert stat.call_count == 1
    stat.reset_mock(side_effect=True)
    base.ordinary.return_value = b"timeout (GNU coreutils) 9.5\n"
    assert view.observe(probe, candidate, fragment)["phase"] == "preflight"
    assert stat.call_count == 0
    base.ordinary.return_value = b"timeout (GNU coreutils) 9.4\n"
    stat.reset_mock(side_effect=True)
    stat.side_effect = [sample, ValueError("simulated second failure")]
    assert view.observe(probe, candidate, fragment)["phase"] == "second_stat"
    assert stat.call_count == 2
    stat.reset_mock(side_effect=True)
    stat.return_value = sample
    # Identities check four times; a fifth deadline check is after final library read.
    checks = Mock(side_effect=[None, None, None, None, ValueError("expired")])
    probe.remaining = checks
    assert view.observe(probe, candidate, fragment)["status"] == "UNKNOWN"
    assert checks.call_count == 5


def test_empty_fragment_is_reported_only_for_exact_root_slice():
    root = {
        "Id": "-.slice",
        "Names": ["-.slice"],
        "FragmentPath": "",
        "SourcePath": "",
        "DropInPaths": [],
        "Following": "",
        "LoadState": "loaded",
        "Transient": False,
        "NeedDaemonReload": False,
    }
    unit = {"Id": "invented.service", "FragmentPath": "/units/invented.service"}
    snapshot = {"units": [{"properties": root}, {"properties": unit}]}
    paths, names, absent = view.fragment_inputs(snapshot, {"-.slice", "invented.service"})
    assert paths == ["/units/invented.service"] and names == ["invented.service"]
    assert absent == [{"unit_id": "-.slice", "state": "FRAGMENT_PATH_EMPTY_REPORTED"}]
    for key, wrong in (
        ("Id", "another.slice"),
        ("Names", ["-.slice", "alias.slice"]),
        ("SourcePath", "/elsewhere"),
        ("DropInPaths", ["/override"]),
        ("Following", "alias"),
        ("LoadState", "not-found"),
        ("Transient", True),
        ("NeedDaemonReload", True),
    ):
        bad = {**root, key: wrong}
        with pytest.raises(ValueError):
            view.fragment_inputs({"units": [{"properties": bad}]}, {bad["Id"]})


def test_stat_receipt_records_fixed_failure_metadata():
    receipt = []
    base = SimpleNamespace(
        ENV={},
        capture=Mock(
            return_value={
                "stdout": b"",
                "reason": "timeout",
                "exit_code": None,
                "errno": None,
                "stderr_bytes": 0,
                "direct_sudo_reaped": False,
            }
        ),
    )
    with pytest.raises(ValueError):
        view.stat_sample(base, 123, 456, time.monotonic() + 1, receipt)
    assert receipt == [
        {
            "sequence": 1,
            "exit_code": None,
            "reason": "timeout",
            "errno": None,
            "stderr_bytes": 0,
            "direct_sudo_reaped": False,
            "privileged_descendants": "NOT_INDEPENDENTLY_OBSERVED",
        }
    ]


def test_import_hash_drift_never_executes_source(monkeypatch):
    import json

    monkeypatch.setenv("BOUND_SOURCE_SHA256", json.dumps({"scripts/dependency_candidates.py": "0" * 64}))
    with pytest.raises(ValueError):
        view.load("dependency_candidates")


def test_output_overflow_keeps_bounded_stat_receipt_without_large_payload():
    import json

    attempts = [{"sequence": 1}, {"sequence": 2}]
    raw, overflow = view.bounded_output(
        {
            "schema": "forge-dependency-view-v1",
            "status": "OBSERVED",
            "phase": "complete",
            "stat_attempts": attempts,
            "qualification": "NOT_RUN",
            "snapshot": "x" * 300000,
        }
    )
    result = json.loads(raw)
    assert overflow and result["status"] == "UNKNOWN" and result["reason"] == "output"
    assert result["phase"] == "complete" and result["stat_attempts"] == attempts
    assert "snapshot" not in result and len(raw) < 4096


@pytest.mark.parametrize("length,overflow", [(262143, False), (262144, True), (262145, True)])
def test_worker_output_reserves_newline_at_exact_boundary(length, overflow):
    import json

    base = {
        "schema": "forge-dependency-view-v1",
        "status": "OBSERVED",
        "phase": "complete",
        "stat_attempts": [{"sequence": 1}, {"sequence": 2}],
        "payload": "",
    }
    overhead = len(json.dumps(base, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode())
    base["payload"] = "x" * (length - overhead)
    raw, exceeded = view.bounded_output(base)
    assert exceeded is overflow and len(raw) + 1 <= 262144
    if overflow:
        assert json.loads(raw)["stat_attempts"] == base["stat_attempts"]
