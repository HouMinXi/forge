# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""LOCAL advice is separate, atomic metadata, never primary review evidence."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from code_forge import cli, local_assertion_report as report
from code_forge.assertion_outcome import AssertionOutcome, AssertionStatus
from code_forge.exit_codes import EXIT_BUSY, EXIT_CLI_ERROR, verdict_to_exit
from code_forge.lock import ForgeLock, ForgeLockBusy
from code_forge.state import Verdict, load_state
from tests.test_assertion_outcome_reporting import OUTCOMES, prepare_consumer
from tests.test_assertion_outcomes import UNTRUSTED


def destination(state_path):
    return state_path.parent / "local-assertion-report.json"


def prepare_local(monkeypatch, tmp_path, **kwargs):
    prepared = prepare_consumer(monkeypatch, tmp_path, **kwargs)
    argv = [*sys.argv, "--registry", str(prepared[0] / ".code-forge" / "tools.yaml")]
    argv[argv.index("--mode") + 1] = "local"
    monkeypatch.setattr(sys, "argv", argv)
    return prepared


def summary(outcome):
    metadata = AssertionOutcome.to_metadata(outcome)
    line = "code-forge: test_assertion=" + metadata["status"]
    if metadata["finding_count"] is not None:
        line += f" assertion_findings={metadata['finding_count']}"
    if "stage" in metadata:
        line += f" stage={metadata['stage']} kind={metadata['kind']}"
    return line


@pytest.mark.parametrize(
    "outcome", [*OUTCOMES, AssertionOutcome(AssertionStatus.COMPLETED_FINDINGS, 2), None]
)
@pytest.mark.parametrize("state_bytes", [None, b"invalid state\n"])
def test_canonical_report_without_state_dependency(tmp_path, capsys, outcome, state_bytes):
    state = tmp_path / "state.json"
    if state_bytes is not None:
        state.write_bytes(state_bytes)
    destination(state).write_text('{"testAssertion":{"status":"old"}}')
    report.emit_local_assertion_report(state, outcome)
    content = destination(state).read_bytes()
    assert content.endswith(b"\n")
    assert json.loads(content) == ({} if outcome is None else {"testAssertion": outcome.to_metadata()})
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == ("" if outcome is None else summary(outcome) + "\n")
    assert state.read_bytes() == state_bytes if state_bytes is not None else not state.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        ["local-assertion-report.json"] + (["state.json"] if state_bytes is not None else [])
    )


def test_clear_and_replace_remove_symlink_entry_only(tmp_path, capsys):
    state = tmp_path / "state.json"
    target = tmp_path / "unrelated.txt"
    target.write_bytes(b"do not change\n")
    dest = destination(state)
    dest.symlink_to(target)
    report.clear_local_assertion_report(state)
    assert not dest.is_symlink()
    assert target.read_bytes() == b"do not change\n"
    report.clear_local_assertion_report(state)
    dest.symlink_to(target)
    report.emit_local_assertion_report(state, OUTCOMES[0])
    assert not dest.is_symlink()
    assert target.read_bytes() == b"do not change\n"
    assert json.loads(dest.read_text()) == {"testAssertion": OUTCOMES[0].to_metadata()}
    assert capsys.readouterr().out == ""


def test_atomic_replacement_uses_unique_same_directory_files(monkeypatch, tmp_path):
    state = tmp_path / "state.json"
    dest = destination(state)
    dest.write_text("old")
    real_replace = report.os.replace
    real_temp = report.tempfile.NamedTemporaryFile
    streams = []
    sources = []

    def temp(*args, **kwargs):
        stream = real_temp(*args, **kwargs)
        streams.append(stream)
        return stream

    def replace(source, target):
        source, target = Path(source), Path(target)
        assert streams[-1].closed
        assert source.parent == dest.parent
        assert target == dest
        assert source != dest
        assert source not in [entry[0] for entry in sources]
        assert json.loads(source.read_text(encoding="utf-8")) == {
            "testAssertion": OUTCOMES[0].to_metadata()
        }
        assert dest.read_text() == ("old" if not sources else sources[0][1])
        text = source.read_text()
        real_replace(source, target)
        sources.append((source, text))

    monkeypatch.setattr(report.os, "replace", replace)
    monkeypatch.setattr(report.tempfile, "NamedTemporaryFile", temp)
    report.emit_local_assertion_report(state, OUTCOMES[0])
    report.emit_local_assertion_report(state, OUTCOMES[0])
    assert len({entry[0] for entry in sources}) == 2
    assert list(tmp_path.iterdir()) == [dest]


def test_replace_failure_is_fixed_warning_and_temp_is_cleaned(monkeypatch, tmp_path, capsys):
    state = tmp_path / "state.json"
    destination(state).write_text("old")

    def fail(*args):
        raise OSError(UNTRUSTED)

    monkeypatch.setattr(report.os, "replace", fail)
    report.emit_local_assertion_report(state, OUTCOMES[1])
    output = capsys.readouterr()
    assert output.out == ""
    assert summary(OUTCOMES[1]) in output.err.splitlines()
    assert "warning" in output.err.lower()
    assert UNTRUSTED not in output.err
    assert destination(state).read_text() == "old"
    assert list(tmp_path.iterdir()) == [destination(state)]


def test_clear_directory_does_not_recurse(tmp_path, capsys):
    state = tmp_path / "state.json"
    dest = destination(state)
    dest.mkdir()
    (dest / "keep").write_text("retained")
    report.clear_local_assertion_report(state)
    output = capsys.readouterr()
    assert output.out == ""
    assert "warning" in output.err.lower()
    assert "previous" in output.err.lower()
    assert (dest / "keep").read_text() == "retained"


@pytest.mark.parametrize("verdict", [Verdict.PASS, Verdict.FAIL, Verdict.PENDING])
@pytest.mark.parametrize(
    ("case", "outcome_index"),
    [
        ("empty", 0),
        ("findings", 1),
        ("no-tests", 2),
        ("no-diff", 3),
        ("preparation", 4),
        ("transport", 5),
        ("schema", 6),
        ("conversion", 7),
        ("timeout", 8),
    ],
)
def test_actual_local_dispatch_preserves_primary(
    monkeypatch, tmp_path, capsys, verdict, case, outcome_index
):
    repo, marker, state, receipt, saved = prepare_local(
        monkeypatch, tmp_path, verdict=verdict, case=case
    )
    if case == "conversion":

        def fail_conversion(*args, **kwargs):
            raise RuntimeError(UNTRUSTED)

        monkeypatch.setattr("code_forge.reviewer_json._json_to_state_findings", fail_conversion)
    if case == "preparation":

        def fail_preparation(*args, **kwargs):
            raise RuntimeError(UNTRUSTED)

        monkeypatch.setattr("code_forge.diff.annotated_diff_prompt_block", fail_preparation)
    before = receipt.read_bytes()
    destination(state).write_text("stale")
    assert cli.main() == (EXIT_BUSY if verdict is Verdict.PENDING else verdict_to_exit(verdict))
    output = capsys.readouterr()
    assert output.out == ""
    expected = OUTCOMES[outcome_index]
    assert json.loads(destination(state).read_text()) == {"testAssertion": expected.to_metadata()}
    assert summary(expected) in output.err.splitlines()
    assert "synthetic-private-value" not in output.err
    assert state.read_bytes() == saved[0]
    assert receipt.read_bytes() == before
    loaded = load_state(state)
    assert loaded.verdict is verdict
    assert loaded.round == loaded.consecutive_clean_rounds == 3
    assert [finding.id for finding in loaded.findings] == ["static"]
    assert marker.exists() == (case not in ("no-tests", "no-diff", "preparation"))
    assert not (repo / ".code-forge" / "code-forge.lock").exists()


def test_lock_spans_clear_primary_advice_and_publication(monkeypatch, tmp_path):
    repo, _, state, _, _ = prepare_local(monkeypatch, tmp_path)
    dest = destination(state)
    dest.write_text("stale")
    lock = repo / ".code-forge" / "code-forge.lock"
    events = []
    real_clear = cli.clear_local_assertion_report
    real_primary = cli._run_hold_loop
    real_advice = cli._run_test_assertion_phase
    real_emit = cli.emit_local_assertion_report

    def locked():
        assert int(lock.read_text()) == os.getpid()
        with pytest.raises(ForgeLockBusy), ForgeLock(lock):
            pytest.fail("lock was not held")

    def clear(*args):
        locked()
        assert dest.read_text() == "stale"
        events.append("clear")
        return real_clear(*args)

    def primary(**kwargs):
        locked()
        assert not dest.exists()
        events.append("primary")
        return real_primary(**kwargs)

    def advice(*args):
        locked()
        assert not dest.exists()
        events.append("advice")
        return real_advice(*args)

    def emit(*args):
        locked()
        assert not dest.exists()
        events.append("emit")
        return real_emit(*args)

    monkeypatch.setattr(cli, "clear_local_assertion_report", clear)
    monkeypatch.setattr(cli, "_run_hold_loop", primary)
    monkeypatch.setattr(cli, "_run_test_assertion_phase", advice)
    monkeypatch.setattr(cli, "emit_local_assertion_report", emit)
    assert cli.main() == 0
    assert events == ["clear", "primary", "advice", "emit"]
    assert dest.exists()
    assert not lock.exists()


@pytest.mark.parametrize(
    "exc_type", [MemoryError, KeyboardInterrupt, SystemExit, asyncio.CancelledError]
)
@pytest.mark.parametrize("phase", ["primary", "advice"])
def test_interruption_clears_stale_without_fabrication(monkeypatch, tmp_path, exc_type, phase):
    repo, _, state, _, _ = prepare_local(monkeypatch, tmp_path)
    destination(state).write_text("stale")
    error = exc_type("original")

    def fatal(*args, **kwargs):
        assert not destination(state).exists()
        raise error

    monkeypatch.setattr(
        cli, "_run_hold_loop" if phase == "primary" else "_run_test_assertion_phase", fatal
    )
    args = cli._build_parser().parse_args(sys.argv[1:])
    with pytest.raises(exc_type) as caught:
        cli._run(args, os.environ, repo)
    assert caught.value is error
    assert not destination(state).exists()
    assert not (repo / ".code-forge" / "code-forge.lock").exists()


@pytest.mark.parametrize("path", ["ci", "subagent", "inline", "busy", "preflight", "no-changes"])
def test_excluded_paths_never_touch_sidecar(monkeypatch, tmp_path, path):
    repo, _, state, _, _ = prepare_local(
        monkeypatch, tmp_path, outlet=path if path in ("subagent", "inline") else "subprocess"
    )
    destination(state).write_bytes(b"previous result\n")
    if path == "ci":
        argv = list(sys.argv)
        argv[argv.index("--mode") + 1] = "ci"
        monkeypatch.setattr(sys, "argv", argv)
    elif path == "preflight":
        monkeypatch.setattr(sys, "argv", [*sys.argv, "--backend-url", "invalid"])
    elif path == "no-changes":
        from dataclasses import replace

        real_resolve = cli.resolve_baseline
        monkeypatch.setattr(
            cli,
            "resolve_baseline",
            lambda *args: replace(real_resolve(*args), git_diff="", source_files=[]),
        )
    if path == "busy":
        with ForgeLock(repo / ".code-forge" / "code-forge.lock"):
            assert cli.main() == EXIT_BUSY
    else:
        result = cli.main()
        assert result == (
            EXIT_CLI_ERROR
            if path == "preflight"
            else verdict_to_exit(Verdict.DELEGATED)
            if path == "inline"
            else 0
        )
    assert destination(state).read_bytes() == b"previous result\n"


def test_missing_outcome_replaces_old_report_with_empty_object(monkeypatch, tmp_path, capsys):
    _, _, state, _, _ = prepare_local(monkeypatch, tmp_path)
    destination(state).write_text('{"testAssertion":{"status":"old"}}')
    monkeypatch.setattr(cli, "_run_test_assertion_phase", lambda *args: None)
    assert cli.main() == 0
    assert json.loads(destination(state).read_text()) == {}
    output = capsys.readouterr()
    assert output.out == ""
    assert "test_assertion=" not in output.err


@pytest.mark.parametrize("failure", ["create", "write", "replace", "clear"])
@pytest.mark.parametrize("verdict", [Verdict.PASS, Verdict.FAIL, Verdict.PENDING])
def test_io_failure_does_not_change_primary_exit(monkeypatch, tmp_path, capsys, failure, verdict):
    _, _, state, _, saved = prepare_local(monkeypatch, tmp_path, verdict=verdict)
    dest = destination(state)
    dest.write_text("stale")
    real_temp = report.tempfile.NamedTemporaryFile
    real_unlink = Path.unlink
    real_replace = report.os.replace

    def replace(source, target):
        if Path(target) == dest:
            raise OSError(UNTRUSTED)
        return real_replace(source, target)

    def fail(*args, **kwargs):
        raise OSError(UNTRUSTED)

    def unlink(path, *args, **kwargs):
        if path == dest:
            raise OSError(UNTRUSTED)
        return real_unlink(path, *args, **kwargs)

    def broken_stream(*args, **kwargs):
        stream = real_temp(*args, **kwargs)
        stream.write = fail
        return stream

    if failure == "create":
        monkeypatch.setattr(report.tempfile, "NamedTemporaryFile", fail)
    elif failure == "write":
        monkeypatch.setattr(report.tempfile, "NamedTemporaryFile", broken_stream)
    elif failure == "replace":
        monkeypatch.setattr(report.os, "replace", replace)
    else:
        monkeypatch.setattr(Path, "unlink", unlink)
    assert cli.main() == (EXIT_BUSY if verdict is Verdict.PENDING else verdict_to_exit(verdict))
    output = capsys.readouterr()
    assert output.out == ""
    assert summary(OUTCOMES[0]) in output.err.splitlines()
    expected_warning = (
        "code-forge: warning: cannot clear local assertion report; the previous report is not this run's result"
        if failure == "clear"
        else "code-forge: warning: cannot write local assertion report"
    )
    assert expected_warning in output.err.splitlines()
    assert UNTRUSTED not in output.err
    assert state.read_bytes() == saved[0]
    assert not list(state.parent.glob(".local-assertion-report.json.*.tmp"))
    assert dest.exists() == (failure == "clear")


@pytest.mark.parametrize(
    "exc_type", [MemoryError, KeyboardInterrupt, SystemExit, asyncio.CancelledError]
)
def test_emitter_cleanup_does_not_mask_fatal_error(monkeypatch, tmp_path, exc_type):
    state = tmp_path / "state.json"
    error = exc_type("original")

    def fail_replace(*args):
        raise error

    def fail_cleanup(*args, **kwargs):
        raise OSError("cleanup")

    monkeypatch.setattr(report.os, "replace", fail_replace)
    monkeypatch.setattr(Path, "unlink", fail_cleanup)
    with pytest.raises(exc_type) as caught:
        report.emit_local_assertion_report(state, OUTCOMES[0])
    assert caught.value is error
    assert not destination(state).exists()


def test_outcome_validation_precedes_temporary_creation(monkeypatch, tmp_path, capsys):
    calls = []
    monkeypatch.setattr(report.tempfile, "NamedTemporaryFile", lambda **kwargs: calls.append(kwargs))
    invalid = object.__new__(AssertionOutcome)
    object.__setattr__(invalid, "status", "raw reviewer content")
    with pytest.raises(TypeError):
        report.emit_local_assertion_report(tmp_path / "state.json", invalid)
    assert calls == []
    assert list(tmp_path.iterdir()) == []
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("state_bytes", [None, b"corrupt state"])
def test_actual_dispatch_report_does_not_require_saved_state(monkeypatch, tmp_path, state_bytes):
    _, _, state, receipt, _ = prepare_local(monkeypatch, tmp_path)
    if state_bytes is not None:
        state.write_bytes(state_bytes)
    before = receipt.read_bytes()
    monkeypatch.setattr(cli, "_run_hold_loop", lambda **kwargs: Verdict.PENDING)
    assert cli.main() == EXIT_BUSY
    assert json.loads(destination(state).read_text()) == {"testAssertion": OUTCOMES[0].to_metadata()}
    assert state.read_bytes() == state_bytes if state_bytes is not None else not state.exists()
    assert receipt.read_bytes() == before
