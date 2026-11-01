# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Fatal advice has a bounded human trace, never completion authority."""

from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
import traceback
from types import SimpleNamespace

import pytest

from code_forge import cli
from code_forge.assertion_outcome import AssertionOutcome, AssertionStatus
from code_forge.state import Verdict
from tests.test_assertion_outcome_reporting import prepare_consumer
from tests.test_subagent_assertion_summary import prepare_dispatch

SUMMARY = "code-forge: test_assertion=interrupted stage=reporting kind=interrupted"
FATAL = [KeyboardInterrupt, asyncio.CancelledError, MemoryError]


@pytest.fixture(autouse=True)
def no_new_reporting_work(monkeypatch):
    attempted = []

    def forbidden(*args, **kwargs):
        attempted.append("unexpected reporting, probe or network call")
        pytest.fail("fatal advice must not start structured reporting or a tool probe")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    for name in ("_emit_ci_output", "emit_local_assertion_report", "capture_tool_version"):
        monkeypatch.setattr(cli, name, forbidden)
    yield attempted
    # Fatal-summary code deliberately swallows secondary BaseException, so an
    # immediate pytest.fail alone would not establish that no work happened.
    assert attempted == []


@pytest.mark.parametrize("error_type", FATAL)
@pytest.mark.parametrize("point", ["review", "finding", "metadata"])
def test_fatal_identity_cause_trace_and_exactly_one_safe_line(monkeypatch, capsys, error_type, point):
    error = error_type("private marker\ncode-forge: forged completion")
    cause = ValueError("private cause")

    def fail():
        raise error from cause

    def helper(*args, on_result, **kwargs):
        if point == "review":
            fail()
        on_result(AssertionOutcome(AssertionStatus.COMPLETED_EMPTY, 0))
        if point == "finding":

            class Finding:
                @property
                def description(self):
                    fail()

            return [Finding()]
        return []

    monkeypatch.setattr(cli, "_run_test_assertion_review", helper)
    if point == "metadata":
        monkeypatch.setattr(AssertionOutcome, "to_metadata", lambda self: fail())
    with pytest.raises(error_type) as caught:
        cli._run_test_assertion_phase("synthetic diff")
    assert caught.value is error
    assert error.__cause__ is cause
    assert traceback.extract_tb(error.__traceback__)[-1].name == "fail"
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err.splitlines().count(SUMMARY) == 1
    assert "private" not in output.err and "forged" not in output.err
    assert "completed" not in output.err and "phase done" not in output.err
    assert "findings=" not in output.err


@pytest.mark.parametrize("error_type", FATAL)
@pytest.mark.parametrize(
    "secondary_type", [OSError, MemoryError, KeyboardInterrupt, asyncio.CancelledError]
)
@pytest.mark.parametrize("operation", ["write", "flush"])
def test_secondary_sink_failure_cannot_replace_original(
    monkeypatch, error_type, secondary_type, operation
):
    error = error_type("original")
    secondary = secondary_type("secondary")
    calls = []

    class Sink:
        def write(self, text):
            calls.append(("write", text))
            if operation == "write":
                raise secondary

        def flush(self):
            calls.append(("flush", None))
            raise secondary

    def helper(*args, **kwargs):
        monkeypatch.setattr(sys, "stderr", Sink())
        raise error

    monkeypatch.setattr(cli, "_run_test_assertion_review", helper)
    with pytest.raises(error_type) as caught:
        cli._run_test_assertion_phase("synthetic diff")
    assert caught.value is error
    assert calls == [("write", SUMMARY + "\n")] + ([("flush", None)] if operation == "flush" else [])


@pytest.mark.parametrize("error_type", FATAL)
@pytest.mark.parametrize("mode", ["ci", "local"])
@pytest.mark.parametrize("verdict", [Verdict.PASS, Verdict.FAIL])
def test_actual_cli_keeps_primary_state_and_fatal_exit(
    monkeypatch, tmp_path, capsys, error_type, mode, verdict, no_new_reporting_work
):
    repo, marker, state_path, receipt, saved = prepare_consumer(monkeypatch, tmp_path, verdict=verdict)
    # Explicit empty test registry: isolate reporting without detecting or
    # enabling real toolchains. Primary review is already a fixture above.
    monkeypatch.setattr(cli, "load_registry", lambda path: {})
    monkeypatch.setattr(
        "code_forge.manifest.extract_manifest",
        lambda *args: SimpleNamespace(to_prompt_block=lambda: ""),
    )
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--mode", mode, "--registry", "isolated-registry"])
    original_popen = subprocess.Popen

    def only_git(args, *rest, **kwargs):
        if args[0] != "git":
            no_new_reporting_work.append("unexpected non-Git process")
            pytest.fail("unexpected non-Git process in isolated consumer")
        return original_popen(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", only_git)
    error = error_type("private fatal")

    def helper(*args, **kwargs):
        raise error

    monkeypatch.setattr(cli, "_run_test_assertion_review", helper)
    if error_type is MemoryError:
        # main's existing generic Exception handler prints the traceback and
        # returns FAIL; the phase itself must still rethrow the exact object.
        assert cli.main() == 1
    else:
        with pytest.raises(SystemExit if error_type is KeyboardInterrupt else error_type) as caught:
            cli.main()
        if error_type is KeyboardInterrupt:
            assert caught.value.code == 130
        else:
            assert caught.value is error
    assert len(saved) == 1 and state_path.read_bytes() == saved[0]
    assert receipt.read_text() == '{"static_evidence":"retained"}\n'
    assert not marker.exists()
    assert not (state_path.parent / "local-assertion-report.json").exists()
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err.splitlines().count(SUMMARY) == 1
    if error_type is not MemoryError:
        assert "private fatal" not in output.err
    else:
        assert "command done: exit=1" in output.err
    assert "command done: verdict=" not in output.err
    if error_type is KeyboardInterrupt:
        assert "command done: exit=130" in output.err


@pytest.mark.parametrize("error_type", FATAL)
def test_subagent_retains_files_and_original_fatal(monkeypatch, tmp_path, capsys, error_type):
    kwargs, calls = prepare_dispatch(monkeypatch, tmp_path)
    protected = [
        tmp_path / name for name in ("state.json", "receipt.json", "local-assertion-report.json")
    ]
    for path in protected:
        path.write_bytes(b"existing evidence")
    error = error_type("private fatal")

    def helper(*args, **kwargs):
        raise error

    monkeypatch.setattr(cli, "_run_test_assertion_review", helper)
    with pytest.raises(error_type) as caught:
        cli._dispatch_subagent(**kwargs)
    assert caught.value is error and calls == ["primary"]
    assert set(tmp_path.iterdir()) == set(protected)
    assert all(path.read_bytes() == b"existing evidence" for path in protected)
    output = capsys.readouterr()
    assert output.out == "" and output.err.splitlines().count(SUMMARY) == 1


@pytest.mark.parametrize("count", [0, 1])
def test_healthy_sibling_has_no_interruption_and_keeps_result(monkeypatch, capsys, count):
    outcome = AssertionOutcome(
        AssertionStatus.COMPLETED_FINDINGS if count else AssertionStatus.COMPLETED_EMPTY, count
    )

    def helper(*args, on_result, **kwargs):
        on_result(outcome)
        return [SimpleNamespace(description="healthy finding")] * count

    monkeypatch.setattr(cli, "_run_test_assertion_review", helper)
    assert cli._run_test_assertion_phase("synthetic diff") is outcome
    output = capsys.readouterr()
    assert SUMMARY not in output.err and "phase done" in output.err
    assert output.out == ""
