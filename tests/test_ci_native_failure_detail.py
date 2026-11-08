"""Offline tests for temporary native failure publication; no native host is run."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import ci_native_failure_detail as native
from tests import test_ci_private_transport as fixtures
from tests.test_ci_failure_diagnostic import PHASE, _case, _export, _save


@pytest.mark.parametrize("stage", ["setup", "call", "teardown"])
def test_first_natural_failure_keeps_chain_assertion_not_captures_or_locals(tmp_path, stage):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    # A target-named synthetic module proves hook behavior, not native admission.
    (tests / "test_ordinary_audit_native.py").write_text(
        "import pytest, os\n"
        "def fail():\n"
        "    unrelated_local = os.environ['FIXTURE_LOCAL']\n"
        "    print(os.environ['FIXTURE_OUTPUT'])\n"
        "    try:\n"
        "        raise ValueError('original cause')\n"
        "    except ValueError:\n"
        "        assert 17 == 29\n"
        "@pytest.fixture\n"
        "def resource():\n"
        + ("    fail()\n" if stage == "setup" else "")
        + "    yield\n"
        + ("    fail()\n" if stage == "teardown" else "")
        + "def test_failure(resource):\n"
        + ("    fail()\n" if stage == "call" else "    pass\n")
    )
    report = tmp_path / "receipt.json"
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        "FIXTURE_LOCAL": "LOCAL_CANARY_7788",
        "FIXTURE_OUTPUT": "CAPTURE_CANARY_7788",
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            "pytest.ini",
            "-p",
            "scripts.ci_test_shards",
            "--ci-report",
            str(report),
            "--ci-phase",
            PHASE,
            "--ci-sha",
            "a" * 40,
            "--showlocals",
            "tests/test_ordinary_audit_native.py",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 1
    receipt = json.loads(report.read_text())
    failures = [item for item in receipt["reports"] if item["outcome"] == "failed"]
    assert len(failures) == 1 and failures[0]["when"] == stage
    detail = failures[0]["native_failure_detail"]
    assert native.validate(detail) and not detail["truncated"]
    assert "original cause" in detail["traceback"] and "assert 17 == 29" in detail["traceback"]
    assert "Captured stdout" not in detail["traceback"]
    assert "LOCAL_CANARY_7788" not in detail["traceback"]
    assert "CAPTURE_CANARY_7788" not in detail["traceback"]


def test_scrub_known_credentials_and_recognizable_tokens(monkeypatch):
    monkeypatch.setenv("ACTIONS_RUNTIME_TOKEN", "opaque-runtime-credential-123")
    text = "opaque-runtime-credential-123 ghp_" + "a" * 30 + " https://name:password@host/x"
    assert (
        native.scrub(text, native.credential_values())
        == "[REDACTED] [REDACTED] https://[REDACTED]@host/x"
    )
    assert native.scrub("NATIVE_PRIVATE_RESPONSE_SENTINEL synthetic-fixture-value") == (
        "NATIVE_PRIVATE_RESPONSE_SENTINEL synthetic-fixture-value"
    )


@pytest.mark.parametrize("enabled", [False, True])
def test_export_is_explicitly_enabled_and_binds_original_failure(tmp_path, monkeypatch, enabled):
    monkeypatch.setattr(fixtures, "PUBLIC_MODULES", [native.MODULE, *fixtures.PUBLIC_MODULES[1:]])
    case = _case(tmp_path, monkeypatch)
    report = next(
        r for r in case.receipt["reports"] if native.eligible(r["nodeid"]) and r["when"] == "call"
    )
    report.update(
        outcome="failed",
        native_failure_detail={"traceback": "AssertionError: 17 != 29", "truncated": False},
    )
    _save(case)
    result = _export(case, native_details=enabled)
    assert result["status"] == "recorded"
    details = [item["native_detail"] for item in result["tests"] if "native_detail" in item]
    assert len(details) == int(enabled)
    if enabled:
        assert details[0] == {
            **report["native_failure_detail"],
            "schema": native.SCHEMA,
            "nodeid": report["nodeid"],
            **{k: case.state[k] for k in ("sha", "run", "attempt", "phase")},
            "stage": "call",
        }


def test_non_target_failure_never_publishes_detail(tmp_path, monkeypatch):
    case = _case(tmp_path, monkeypatch)
    for report in case.receipt["reports"]:
        report["native_failure_detail"] = {"traceback": "UNRELATED_PRIVATE", "truncated": False}
    _save(case)
    assert "UNRELATED_PRIVATE" not in json.dumps(_export(case, native_details=True))


@pytest.mark.parametrize("error", [ValueError, SystemExit, KeyboardInterrupt])
def test_capture_failure_cannot_replace_original_report(error, monkeypatch):
    from types import SimpleNamespace

    from scripts.ci_test_shards import Recorder

    recorder = Recorder(SimpleNamespace(getoption=lambda _: None, rootpath=Path.cwd()))
    report = SimpleNamespace(nodeid=native.MODULE + "::test_failure", when="call", failed=True)

    def broken(*args):
        raise error("diagnostic rendering failed")

    monkeypatch.setattr(native, "capture", broken)
    hook = recorder.pytest_runtest_makereport(None, SimpleNamespace(excinfo=object()))
    next(hook)
    with pytest.raises(StopIteration) as stopped:
        hook.send(report)
    assert stopped.value.value is report
    assert recorder.native_failures[(report.nodeid, "call")] is None


def test_first_failure_is_not_replaced_by_later_report(monkeypatch):
    from types import SimpleNamespace

    from scripts.ci_test_shards import Recorder

    recorder = Recorder(SimpleNamespace(getoption=lambda _: None, rootpath=Path.cwd()))
    report = SimpleNamespace(nodeid=native.MODULE + "::test_failure", when="call", failed=True)
    for text in ("first", "later"):
        monkeypatch.setattr(
            native, "capture", lambda *_, text=text: {"traceback": text, "truncated": False}
        )
        hook = recorder.pytest_runtest_makereport(None, object())
        next(hook)
        with pytest.raises(StopIteration):
            hook.send(report)
    assert recorder.native_failures[(report.nodeid, "call")]["traceback"] == "first"


def test_known_credentials_include_short_and_escaped_values(monkeypatch):
    monkeypatch.setenv("FIXTURE_PASSWORD", "s3c")
    monkeypatch.setenv("FIXTURE_API_KEY", "opaque\nsecret")
    for value in ("s3c", "opaque\nsecret", r"opaque\nsecret"):
        assert native.scrub(value, native.credential_values()) == "[REDACTED]"


@pytest.mark.parametrize(
    "detail",
    [
        None,
        {},
        {"traceback": "", "truncated": False},
        {"traceback": "x" * (native.MAX_TEXT + 1), "truncated": False},
        {"traceback": "bad\x00value", "truncated": False},
        {"traceback": "valid", "truncated": 1},
        {"traceback": "valid", "truncated": False, "captured": "private"},
    ],
)
def test_malformed_detail_is_not_valid(detail):
    assert not native.validate(detail)


def test_capture_bounds_text_and_marks_truncation():
    from types import SimpleNamespace

    call = SimpleNamespace(excinfo=SimpleNamespace(getrepr=lambda **_: "x" * (native.MAX_TEXT + 1)))
    detail = native.capture(call, ())
    assert native.validate(detail)
    assert detail == {"traceback": "x" * native.MAX_TEXT, "truncated": True}
