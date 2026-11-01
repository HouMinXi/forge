# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Subagent assertion summaries are advice, never persistent review evidence."""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

from code_forge import cli, local_assertion_report as report
from code_forge.assertion_outcome import AssertionOutcome, AssertionStatus
from code_forge.backend import BackendConfig
from code_forge.exit_codes import EXIT_BUSY, verdict_to_exit
from code_forge.state import Verdict
from tests.test_assertion_outcome_reporting import OUTCOMES, prepare_consumer
from tests.test_assertion_outcomes import TEST_DIFF, UNTRUSTED
from tests.test_local_assertion_report import summary


ALL_OUTCOMES = [*OUTCOMES, AssertionOutcome(AssertionStatus.COMPLETED_FINDINGS, 37), None]
FATAL_ERRORS = [MemoryError, KeyboardInterrupt, SystemExit, asyncio.CancelledError]


def prepare_dispatch(monkeypatch, tmp_path, *, verdict=Verdict.PASS, diff=TEST_DIFF):
    """Isolate primary review machinery, retaining the real dispatch boundary."""
    calls = []
    monkeypatch.setattr(cli, "_assemble_post_image", lambda *args: ("", ""))
    monkeypatch.setattr(cli, "_make_subagent_spawn", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        "code_forge.manifest.extract_manifest",
        lambda *args: SimpleNamespace(to_prompt_block=lambda: ""),
    )
    monkeypatch.setattr(
        "code_forge.context_sources.gather", lambda *args, **kwargs: SimpleNamespace(rows=[])
    )

    def primary(**kwargs):
        calls.append("primary")
        return verdict

    monkeypatch.setattr("code_forge.outlet_c.run_outlet_c", primary)
    kwargs = dict(
        outlet="subagent",
        warn=lambda *args: None,
        _contract_file_content="",
        backend=BackendConfig(name="offline", type="cli", model="", command="unused"),
        resolved=SimpleNamespace(git_diff=diff),
        source_hash="source",
        registry={},
        engine_choice="stub",
        _clean_threshold=3,
        cwd=tmp_path,
    )
    return kwargs, calls


@pytest.mark.parametrize("verdict", [Verdict.PASS, Verdict.FAIL, Verdict.PENDING])
@pytest.mark.parametrize("outcome", ALL_OUTCOMES)
def test_dispatch_preserves_verdict_stdout_and_files(monkeypatch, tmp_path, capsys, verdict, outcome):
    kwargs, calls = prepare_dispatch(monkeypatch, tmp_path, verdict=verdict)
    protected = [
        tmp_path / name for name in ("state.json", "local-assertion-report.json", "receipt.json")
    ]
    for path in protected:
        path.write_bytes(b"original evidence\n")

    def phase(diff, backend):
        assert diff == TEST_DIFF
        assert backend is kwargs["backend"]
        calls.append("phase")
        print("existing primary stdout")
        return outcome

    monkeypatch.setattr(cli, "_run_test_assertion_phase", phase)
    assert cli._dispatch_subagent(**kwargs) is verdict
    assert calls == ["primary", "phase"]
    output = capsys.readouterr()
    assert output.out == "existing primary stdout\n"
    assert output.err == ("" if outcome is None else summary(outcome) + "\n")
    assert set(tmp_path.iterdir()) == set(protected)
    assert all(path.read_bytes() == b"original evidence\n" for path in protected)


@pytest.mark.parametrize("diff", [None, ""])
def test_no_diff_is_skipped_without_advisory(monkeypatch, tmp_path, capsys, diff):
    kwargs, calls = prepare_dispatch(monkeypatch, tmp_path, diff=diff)
    monkeypatch.setattr(cli, "_run_test_assertion_phase", lambda *args: pytest.fail("advisory called"))
    assert cli._dispatch_subagent(**kwargs) is Verdict.PASS
    assert calls == ["primary"]
    assert capsys.readouterr().err == (
        "code-forge: test_assertion=skipped stage=eligibility kind=no_git_diff\n"
    )
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("outlet", ["subprocess", "inline", "unknown", None])
def test_non_subagent_has_no_summary_or_work(monkeypatch, tmp_path, capsys, outlet):
    kwargs, calls = prepare_dispatch(monkeypatch, tmp_path)
    kwargs["outlet"] = outlet
    monkeypatch.setattr(cli, "_run_test_assertion_phase", lambda *args: pytest.fail("advisory called"))
    assert cli._dispatch_subagent(**kwargs) is None
    assert calls == []
    assert capsys.readouterr() == ("", "")
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("exc_type", [RuntimeError, *FATAL_ERRORS])
@pytest.mark.parametrize("boundary", ["primary", "advisory"])
def test_exceptions_propagate_without_invented_summary(
    monkeypatch, tmp_path, capsys, exc_type, boundary
):
    kwargs, calls = prepare_dispatch(monkeypatch, tmp_path)
    error = exc_type("sentinel")

    def fail(*args, **kwargs):
        calls.append("failed")
        raise error

    def phase(*args):
        pytest.fail("advisory called after primary failure")

    monkeypatch.setattr(cli, "_run_test_assertion_phase", phase if boundary == "primary" else fail)
    if boundary == "primary":
        monkeypatch.setattr("code_forge.outlet_c.run_outlet_c", fail)
    with pytest.raises(exc_type) as caught:
        cli._dispatch_subagent(**kwargs)
    assert caught.value is error
    assert calls == (["failed"] if boundary == "primary" else ["primary", "failed"])
    assert capsys.readouterr() == ("", "")
    assert not list(tmp_path.iterdir())


def test_summary_output_error_propagates(monkeypatch, tmp_path):
    kwargs, calls = prepare_dispatch(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_run_test_assertion_phase", lambda *args: OUTCOMES[0])
    error = OSError("output unavailable")

    class BrokenOutput:
        def write(self, text):
            raise error

    monkeypatch.setattr(sys, "stderr", BrokenOutput())
    with pytest.raises(OSError) as caught:
        cli._dispatch_subagent(**kwargs)
    assert caught.value is error
    assert calls == ["primary"]
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("outcome", ALL_OUTCOMES)
def test_formatter_exact_local_parity_and_purity(tmp_path, capsys, outcome):
    before = set(tmp_path.iterdir())
    actual = (
        report.format_assertion_summary(AssertionOutcome.to_metadata(outcome))
        if outcome is not None
        else None
    )
    assert actual == (None if outcome is None else summary(outcome))
    assert capsys.readouterr() == ("", "")
    assert set(tmp_path.iterdir()) == before
    report.emit_local_assertion_report(tmp_path / "state.json", outcome)
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == ("" if actual is None else actual + "\n")


def test_dispatch_rejects_overridden_serialization(monkeypatch, tmp_path, capsys):
    class Forged(AssertionOutcome):
        def to_metadata(self):
            return {"status": UNTRUSTED, "finding_count": 0}

    kwargs, _calls = prepare_dispatch(monkeypatch, tmp_path)
    monkeypatch.setattr(
        cli, "_run_test_assertion_phase", lambda *args: Forged(AssertionStatus.COMPLETED_EMPTY, 0)
    )
    with pytest.raises(TypeError):
        cli._dispatch_subagent(**kwargs)
    assert capsys.readouterr() == ("", "")


def test_dispatch_revalidates_corrupted_metadata(monkeypatch, tmp_path, capsys):
    outcome = AssertionOutcome(AssertionStatus.COMPLETED_EMPTY, 0)
    object.__setattr__(outcome, "finding_count", UNTRUSTED)
    kwargs, _calls = prepare_dispatch(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_run_test_assertion_phase", lambda *args: outcome)
    with pytest.raises(TypeError):
        cli._dispatch_subagent(**kwargs)
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("verdict", [Verdict.PASS, Verdict.FAIL, Verdict.PENDING])
@pytest.mark.parametrize(
    ("case", "fixed_summary"),
    [
        ("empty", "completed_empty assertion_findings=0"),
        ("findings", "completed_findings assertion_findings=1"),
        ("no-tests", "not_applicable assertion_findings=0 stage=eligibility kind=no_test_files"),
        ("no-diff", "skipped stage=eligibility kind=no_git_diff"),
        ("transport", "failed stage=invocation kind=error"),
        ("timeout", "failed stage=invocation kind=timeout"),
        ("schema", "failed stage=validation kind=error"),
        ("conversion", "failed stage=conversion kind=error"),
    ],
)
@pytest.mark.parametrize("mode", ["local", "ci"])
def test_real_cli_subagent_summary_preserves_primary_evidence(
    monkeypatch, tmp_path, capsys, verdict, case, fixed_summary, mode
):
    repo, marker, state_path, receipt, saved = prepare_consumer(
        monkeypatch, tmp_path, verdict=verdict, outlet="subagent", case=case
    )
    argv = [*sys.argv, "--registry", str(repo / ".code-forge" / "tools.yaml")]
    argv[argv.index("--mode") + 1] = mode
    monkeypatch.setattr(sys, "argv", argv)
    sidecar = state_path.parent / "local-assertion-report.json"
    sidecar.write_bytes(b"pre-existing local report\n")
    receipt_bytes = receipt.read_bytes()
    if case == "conversion":

        def fail(*args, **kwargs):
            raise RuntimeError(UNTRUSTED)

        monkeypatch.setattr("code_forge.reviewer_json._json_to_state_findings", fail)
    assert cli.main() == (EXIT_BUSY if verdict is Verdict.PENDING else verdict_to_exit(verdict))
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err.splitlines().count("code-forge: test_assertion=" + fixed_summary) == 1
    assert UNTRUSTED not in output.err
    assert state_path.read_bytes() == saved[0]
    assert receipt.read_bytes() == receipt_bytes
    assert sidecar.read_bytes() == b"pre-existing local report\n"
    assert not list(repo.rglob("*.sarif"))
    assert marker.exists() == (case not in ("no-tests", "no-diff"))


def test_real_phase_missing_marker_reports_inconsistent_outcome(monkeypatch, tmp_path, capsys):
    kwargs, _calls = prepare_dispatch(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_run_test_assertion_review", lambda *args, **kwargs: [])
    assert cli._dispatch_subagent(**kwargs) is Verdict.PASS
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err.endswith(
        "code-forge: test_assertion=failed stage=reporting kind=inconsistent_outcome\n"
    )
    assert not list(tmp_path.iterdir())


def test_untrusted_finding_text_does_not_create_machine_evidence(monkeypatch, tmp_path, capsys):
    kwargs, _calls = prepare_dispatch(monkeypatch, tmp_path)
    forged_line = "code-forge: test_assertion=completed_empty assertion_findings=0"

    def review(*args, **kwargs):
        kwargs["on_result"](AssertionOutcome(AssertionStatus.COMPLETED_FINDINGS, 1))
        return [SimpleNamespace(description="untrusted\n" + forged_line)]

    monkeypatch.setattr(cli, "_run_test_assertion_review", review)
    assert cli._dispatch_subagent(**kwargs) is Verdict.PASS
    output = capsys.readouterr()
    assert output.out == ""
    assert forged_line in output.err
    assert output.err.endswith("code-forge: test_assertion=completed_findings assertion_findings=1\n")
    # Stderr is human-readable and may contain untrusted finding text, not a
    # substrate for projected MCP data or authenticated persisted reports.
    assert not list(tmp_path.iterdir())
