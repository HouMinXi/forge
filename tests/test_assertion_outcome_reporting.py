# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Actual CLI callers retain advisory outcomes through the CI writer."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from code_forge import cli
from code_forge.assertion_outcome import AssertionKind, AssertionOutcome, AssertionStage, AssertionStatus
from code_forge.disposition import Disposition
from code_forge.exit_codes import EXIT_BUSY, EXIT_CLI_ERROR, EXIT_FAIL, verdict_to_exit
from code_forge.lock import ForgeLock, ForgeLockBusy
from code_forge.sarif import build_sarif_log, format_summary
from code_forge.state import Mode, State, StateFinding, Verdict, load_state, save_state
from code_forge.user_config import load_user_backends as real_load_user_backends
from tests.test_assertion_outcomes import UNTRUSTED, cli_backend, review_payload
from tests.test_cli_phase_progress import _prepare_main, _review_repo


OUTCOMES = [
    AssertionOutcome(AssertionStatus.COMPLETED_EMPTY, 0),
    AssertionOutcome(AssertionStatus.COMPLETED_FINDINGS, 1),
    AssertionOutcome(
        AssertionStatus.NOT_APPLICABLE, 0, AssertionStage.ELIGIBILITY, AssertionKind.NO_TEST_FILES
    ),
    AssertionOutcome(
        AssertionStatus.SKIPPED, None, AssertionStage.ELIGIBILITY, AssertionKind.NO_GIT_DIFF
    ),
    *[
        AssertionOutcome(AssertionStatus.FAILED, None, stage, AssertionKind.ERROR)
        for stage in (
            AssertionStage.PREPARATION,
            AssertionStage.INVOCATION,
            AssertionStage.VALIDATION,
            AssertionStage.CONVERSION,
        )
    ],
    AssertionOutcome(AssertionStatus.FAILED, None, AssertionStage.INVOCATION, AssertionKind.TIMEOUT),
    AssertionOutcome(
        AssertionStatus.FAILED, None, AssertionStage.REPORTING, AssertionKind.INCONSISTENT_OUTCOME
    ),
    AssertionOutcome(
        AssertionStatus.SKIPPED, None, AssertionStage.ELIGIBILITY, AssertionKind.UNSUPPORTED_OUTLET
    ),
]


def _legacy_stderr_ok(err: str, legacy_summary: str, *, exact_first_line: bool = False) -> None:
    """IC-05: legacy format_summary line preserved; §5 classification may follow.

    When ``exact_first_line`` is True, the first stderr line must equal
    ``legacy_summary`` byte-for-byte (no advisory suffix). Otherwise the first
    line must start with ``legacy_summary`` (advisory ``test_assertion=`` may
    extend the same summary line, as before Entry).
    """
    lines = err.splitlines()
    assert lines, err
    if exact_first_line:
        assert lines[0] == legacy_summary, (lines[0], legacy_summary)
    else:
        assert lines[0].startswith(legacy_summary), (lines[0], legacy_summary)
    for line in lines[1:]:
        assert line.startswith("code-forge: classification"), line


def _sarif_without_report_classification(doc: dict) -> dict:
    """Return SARIF dict copy with additive IC-02 report_classification stripped."""
    import copy

    out = copy.deepcopy(doc)
    for run in out.get("runs") or []:
        props = run.get("properties")
        if isinstance(props, dict) and "report_classification" in props:
            props = dict(props)
            props.pop("report_classification", None)
            if props:
                run["properties"] = props
            else:
                run.pop("properties", None)
    return out


@pytest.mark.parametrize("outcome", OUTCOMES)
def test_saved_state_writer_adds_only_advisory_metadata(tmp_path, capsys, outcome):
    state = State(verdict=Verdict.PASS, mode=Mode.CI, round=3, consecutive_clean_rounds=3)
    state_path = tmp_path / "state.json"
    save_state(state, state_path)
    original = state_path.read_bytes()
    hooks = []
    cli._emit_ci_output(
        state_path, {}, lambda: hooks.append(True), None, None, None, assertion_outcome=outcome
    )
    output = capsys.readouterr()
    actual = json.loads(output.out)
    expected = build_sarif_log(load_state(state_path), {}, cli.__version__)
    properties = actual["runs"][0].pop("properties")
    # IC-02 additive report_classification alongside advisory metadata.
    properties.pop("report_classification", None)
    assert properties == {"testAssertion": outcome.to_metadata()}
    assert actual == expected
    legacy = format_summary(load_state(state_path))
    _legacy_stderr_ok(output.err, legacy)
    assert f"test_assertion={outcome.status.value}" in output.err
    if outcome.stage is not None:
        assert f"stage={outcome.stage.value} kind={outcome.kind.value}" in output.err
    assert state_path.read_bytes() == original
    assert hooks == [True]


def test_missing_metadata_stays_absent_and_default_outputs_match(tmp_path, capsys):
    state = State(verdict=Verdict.PASS, mode=Mode.CI)
    state_path = tmp_path / "state.json"
    save_state(state, state_path)
    cli._emit_ci_output(state_path, {})
    output = capsys.readouterr()
    actual = json.loads(output.out)
    expected = build_sarif_log(state, {}, cli.__version__)
    assert _sarif_without_report_classification(actual) == expected
    assert "report_classification" in (actual["runs"][0].get("properties") or {})
    _legacy_stderr_ok(output.err, format_summary(state), exact_first_line=True)
    assert "testAssertion" not in output.out
    assert "test_assertion=" not in output.err


def test_absent_state_emits_nothing_even_with_outcome(tmp_path, capsys):
    cli._emit_ci_output(tmp_path / "absent.json", {}, assertion_outcome=OUTCOMES[0])
    output = capsys.readouterr()
    assert output.out == output.err == ""


def prepare_consumer(monkeypatch, tmp_path, *, verdict=Verdict.PASS, outlet="subprocess", case="empty"):
    repo = _review_repo(tmp_path, test_file=case != "no-tests")
    _prepare_main(monkeypatch, repo, outlet, verdict)
    payload = review_payload(finding=case == "findings")
    if case == "schema":
        payload["findings"] = UNTRUSTED
    backend, marker = cli_backend(
        tmp_path, payload, failure=case == "transport", timeout=case == "timeout"
    )
    (tmp_path / "user-config" / "config.yaml").write_text(
        json.dumps(
            {
                "backends": {
                    "offline": {
                        "type": "cli",
                        "model": "",
                        "command": backend.command,
                    }
                }
            }
        )
    )
    monkeypatch.setattr("code_forge.user_config.load_user_backends", real_load_user_backends)
    monkeypatch.setenv("FORGE_LLM_TIMEOUT_S", "1")
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--backend", "offline"])
    if case == "no-diff":
        (repo / "tests" / "test_example.py").write_text("def test_example():\n    assert 1 == 1\n")
        monkeypatch.setattr(sys, "argv", [*sys.argv, "--baseline", "HEAD", "tests/test_example.py"])
    state_path = repo / ".code-forge" / "state.json"
    receipt = repo / ".code-forge" / "receipts" / "static.json"
    receipt.parent.mkdir()
    receipt.write_text('{"static_evidence":"retained"}\n')
    state = State(
        verdict=verdict,
        mode=Mode.CI,
        round=3,
        consecutive_clean_rounds=3,
        findings=[
            StateFinding(
                id="static",
                fingerprint="static",
                source="L0",
                disposition=Disposition.DISMISSED,
                file="example.py",
                line_range=[1, 1],
                description="Static primary finding",
            )
        ],
    )
    saved = []

    def primary(**kwargs):
        save_state(state, state_path)
        saved.append(state_path.read_bytes())
        return verdict

    monkeypatch.setattr(cli, "_run_hold_loop", primary)
    monkeypatch.setattr("code_forge.outlet_c.run_outlet_c", primary)
    return repo, marker, state_path, receipt, saved


@pytest.mark.parametrize("verdict", [Verdict.PASS, Verdict.FAIL, Verdict.PENDING])
@pytest.mark.parametrize(
    ("case", "status", "stage", "kind"),
    [
        ("empty", "completed_empty", None, None),
        ("findings", "completed_findings", None, None),
        ("no-tests", "not_applicable", "eligibility", "no_test_files"),
        ("no-diff", "skipped", "eligibility", "no_git_diff"),
        ("transport", "failed", "invocation", "error"),
        ("timeout", "failed", "invocation", "timeout"),
        ("schema", "failed", "validation", "error"),
        ("conversion", "failed", "conversion", "error"),
    ],
)
def test_actual_cli_ci_carries_outcome_without_changing_primary(
    monkeypatch,
    tmp_path,
    capsys,
    verdict,
    case,
    status,
    stage,
    kind,
):
    repo, marker, state_path, receipt, saved = prepare_consumer(
        monkeypatch, tmp_path, verdict=verdict, case=case
    )
    if case == "conversion":

        def fail_conversion(*args, **kwargs):
            raise RuntimeError(UNTRUSTED)

        monkeypatch.setattr("code_forge.reviewer_json._json_to_state_findings", fail_conversion)
    receipt_before = receipt.read_bytes()
    result = cli.main()
    output = capsys.readouterr()
    expected_exit = EXIT_BUSY if verdict is Verdict.PENDING else verdict_to_exit(verdict)
    assert result == expected_exit, output.err
    run = json.loads(output.out)["runs"][0]
    metadata = run["properties"]["testAssertion"]
    expected = {
        "status": status,
        "finding_count": 1 if case == "findings" else (None if status in ("failed", "skipped") else 0),
    }
    if stage is not None:
        expected.update(stage=stage, kind=kind)
    assert metadata == expected
    assert marker.exists() == (case not in ("no-tests", "no-diff"))
    assert len(run["results"]) == 1
    assert run["results"][0]["message"]["text"] == "Static primary finding"
    assert state_path.read_bytes() == saved[0]
    assert receipt.read_bytes() == receipt_before
    loaded = load_state(state_path)
    assert loaded.verdict is verdict
    assert loaded.round == loaded.consecutive_clean_rounds == 3
    assert [f.id for f in loaded.findings] == ["static"]
    assert not (repo / ".code-forge" / "code-forge.lock").exists()
    lines = output.err.splitlines()
    summary = next(
        i for i, line in enumerate(lines) if line.startswith(f"code-forge: {verdict.value} findings=")
    )
    assert f"test_assertion={status}" in lines[summary]
    assert "findings=1 confirmed=0 uncertain=0 dismissed=1 fixed=0" in lines[summary]
    if case != "no-diff":
        starts = [i for i, line in enumerate(lines) if "test-assertion phase start" in line]
        ends = [
            i for i, line in enumerate(lines) if "test-assertion phase " in line and "elapsed=" in line
        ]
        assert len(starts) == len(ends) == 1
        assert starts[0] < ends[0] < summary
    assert "synthetic-private-value" not in output.err
    assert "\x1b" not in output.err


def test_actual_ci_lock_spans_transport_and_writer(monkeypatch, tmp_path, capsys):
    from code_forge import llm_invoke

    repo, _marker, state_path, _receipt, _saved = prepare_consumer(monkeypatch, tmp_path)
    lock_path = repo / ".code-forge" / "code-forge.lock"
    events = []
    real_transport = llm_invoke._invoke_cli
    real_emit = cli._emit_ci_output

    def assert_locked():
        assert int(lock_path.read_text()) == os.getpid()
        with pytest.raises(ForgeLockBusy):
            with ForgeLock(lock_path):
                pytest.fail("another review acquired the running command's lock")

    def transport(*args, **kwargs):
        assert_locked()
        snapshot = state_path.read_bytes()
        result = real_transport(*args, **kwargs)
        assert_locked()
        assert state_path.read_bytes() == snapshot
        events.append("advice")
        return result

    def emit(*args, **kwargs):
        assert_locked()
        events.append("report")
        return real_emit(*args, **kwargs)

    monkeypatch.setattr(llm_invoke, "_invoke_cli", transport)
    monkeypatch.setattr(cli, "_emit_ci_output", emit)
    assert cli.main() == 0
    output = capsys.readouterr()
    assert events == ["advice", "report"]
    assert (
        json.loads(output.out)["runs"][0]["properties"]["testAssertion"]["status"] == "completed_empty"
    )
    assert not lock_path.exists()


def test_actual_subagent_cli_keeps_phase_and_primary_verdict(monkeypatch, tmp_path, capsys):
    repo, marker, state_path, receipt, saved = prepare_consumer(
        monkeypatch,
        tmp_path,
        verdict=Verdict.FAIL,
        outlet="subagent",
        case="findings",
    )
    receipt_before = receipt.read_bytes()
    assert cli.main() == 1
    output = capsys.readouterr()
    assert marker.read_text() == "-p"
    assert output.out == ""
    assert output.err.count("test-assertion phase start") == 1
    assert output.err.count("test-assertion phase done: findings=1") == 1
    assert output.err.index("phase done") < output.err.index("command done: verdict=FAIL")
    assert state_path.read_bytes() == saved[0]
    assert receipt.read_bytes() == receipt_before
    assert not (repo / ".code-forge" / "code-forge.lock").exists()


@pytest.mark.parametrize("exc_type", [MemoryError, KeyboardInterrupt, SystemExit])
def test_interrupted_ci_emits_no_completed_report(monkeypatch, tmp_path, capsys, exc_type):
    repo, _marker, _state_path, _receipt, _saved = prepare_consumer(monkeypatch, tmp_path)
    # Keep this fatal consumer control independent of installed toolchains.
    from code_forge.context_sources import GatherResult

    monkeypatch.setattr(cli, "load_registry", lambda path: {})
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--registry", "isolated-registry"])
    monkeypatch.setattr("code_forge.context_sources.gather", lambda *args, **kwargs: GatherResult())
    monkeypatch.setattr(
        "code_forge.manifest.extract_manifest",
        lambda *args: SimpleNamespace(to_prompt_block=lambda: ""),
    )

    def fatal(*args, **kwargs):
        raise exc_type()

    monkeypatch.setattr("code_forge.llm_invoke.llm_invoke", fatal)
    if exc_type is KeyboardInterrupt:
        with pytest.raises(SystemExit) as caught:
            cli.main()
        assert caught.value.code == 130
    else:
        assert cli.main() == (EXIT_FAIL if exc_type is MemoryError else EXIT_CLI_ERROR)
    output = capsys.readouterr()
    assert output.out == ""
    if exc_type is SystemExit:
        assert "phase aborted" in output.err
        assert "test_assertion=interrupted" not in output.err
    else:
        assert (
            output.err.splitlines().count(
                "code-forge: test_assertion=interrupted stage=reporting kind=interrupted"
            )
            == 1
        )
    assert "test_assertion=completed" not in output.err
    assert not (repo / ".code-forge" / "code-forge.lock").exists()


def test_legacy_helper_cannot_supply_structured_completion(monkeypatch, tmp_path, capsys):
    prepare_consumer(monkeypatch, tmp_path)

    def legacy(*args, **kwargs):
        kwargs["on_outcome"]("done")
        return []

    monkeypatch.setattr(cli, "_run_test_assertion_review", legacy)
    assert cli.main() == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["runs"][0]["properties"]["testAssertion"] == {
        "status": "failed",
        "finding_count": None,
        "stage": "reporting",
        "kind": "inconsistent_outcome",
    }
    assert "test_assertion=failed stage=reporting kind=inconsistent_outcome" in output.err


def test_outcome_reporting_does_not_mutate_in_memory_state():
    state = State(verdict=Verdict.PASS, mode=Mode.CI, round=3, consecutive_clean_rounds=3)
    original = asdict(state)
    for outcome in OUTCOMES:
        build_sarif_log(state, {}, "test", assertion_outcome=outcome)
        format_summary(state, assertion_outcome=outcome)
    assert asdict(state) == original
