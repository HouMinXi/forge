# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Unsupported assertion advice stays separate from review evidence."""

from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import Mock

import pytest
import yaml

from code_forge import cli, trust
from code_forge.assertion_outcome import (
    AssertionKind,
    AssertionOutcome,
    AssertionStage,
    AssertionStatus,
    legacy_progress_outcome,
)
from code_forge.exit_codes import EXIT_CLI_ERROR, verdict_to_exit
from code_forge.kernel_context import validate_kernel_context
from code_forge.outlet_resolver import resolve_outlet as real_resolve_outlet
from code_forge.state import Verdict


SUMMARY = "code-forge: test_assertion=skipped stage=eligibility kind=unsupported_outlet"


def prepare(monkeypatch, tmp_path, outlet, *, kernel=False, trusted=True, mode="ci"):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user"))
    monkeypatch.setenv("FORGE_SKIP_WORKTREE_CHECK", "1")
    monkeypatch.delenv("FORGE_PROJECT_DIR", raising=False)
    monkeypatch.setattr(sys, "argv", ["code-forge", "review", "--allow-main", "--mode", mode])
    gate = tmp_path / ".code-forge" / "gate.yaml"
    gate.parent.mkdir()
    data = {"kernel_context": {"enabled": kernel, "defconfig": "defconfig"}}
    gate.write_text(yaml.safe_dump(data))
    trust.record_trust(gate, data)
    if kernel and trusted:
        trust.record_kernel_context_trust(
            gate, tmp_path, validate_kernel_context(data["kernel_context"])
        )
    monkeypatch.setattr("code_forge.outlet_resolver.resolve_outlet", lambda *a, **kw: outlet)
    monkeypatch.setattr(cli, "_load_gate_backends", lambda *a: ([], data))
    advice = Mock(side_effect=AssertionError("assertion advice must not run"))
    monkeypatch.setattr(cli, "_run_test_assertion_phase", advice)
    protected = [
        gate.parent / name for name in ("state.json", "local-assertion-report.json", "receipt.json")
    ]
    for path in protected:
        path.write_bytes(b"previous evidence\n")
    return advice, protected


def assert_evidence_unchanged(protected):
    assert all(path.read_bytes() == b"previous evidence\n" for path in protected)
    assert not (protected[0].parent / "code-forge.lock").exists()
    assert not list(protected[0].parent.glob("*.sarif"))


@pytest.mark.parametrize("mode", ["ci", "local"])
@pytest.mark.parametrize("case", ["plain", "pass", "fail", "unreliable", "fallback"])
def test_inline_real_dispatch_preserves_verdict_and_evidence(monkeypatch, tmp_path, capsys, mode, case):
    advice, protected = prepare(monkeypatch, tmp_path, "inline", mode=mode)
    expected = Verdict.DELEGATED
    canary = Mock(side_effect=AssertionError("canary not requested"))
    if case != "plain":
        monkeypatch.setattr(sys, "argv", [*sys.argv, "--canary"])
        if case == "fallback":
            canary.side_effect = RuntimeError("synthetic canary failure")
        else:
            expected = {"pass": Verdict.PASS, "fail": Verdict.FAIL, "unreliable": Verdict.UNRELIABLE}[
                case
            ]
            canary.side_effect = None
            canary.return_value = (expected, [])
    monkeypatch.setattr("code_forge.canary_gen.run_inline_canary", canary)
    assert cli.main() == verdict_to_exit(expected)
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err.splitlines().count(SUMMARY) == 1
    assert "assertion_findings=" not in output.err
    if case == "plain":
        canary.assert_not_called()
    else:
        canary.assert_called_once()
    advice.assert_not_called()
    assert_evidence_unchanged(protected)


@pytest.mark.parametrize("mode", ["ci", "local"])
@pytest.mark.parametrize("kernel,outlet", [(True, "inline"), (True, "subagent")])
def test_rejected_outlets_keep_cli_error_and_evidence(
    monkeypatch, tmp_path, capsys, mode, kernel, outlet
):
    advice, protected = prepare(monkeypatch, tmp_path, outlet, kernel=kernel, mode=mode)
    dispatch = Mock(side_effect=AssertionError("kernel guard must precede dispatch"))
    monkeypatch.setattr(cli, "_dispatch_inline_canary", dispatch)
    assert cli.main() == EXIT_CLI_ERROR
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err.splitlines().count(SUMMARY) == 1
    expected = f"kernel-context: outlet {outlet} is not supported; only the CLI subprocess review path is supported"
    assert f"code-forge: error: {expected}" in output.err.splitlines()
    dispatch.assert_not_called()
    advice.assert_not_called()
    assert_evidence_unchanged(protected)


@pytest.mark.parametrize("outlet", ["inline", "subagent"])
def test_untrusted_kernel_rejection_does_not_invent_outlet_status(monkeypatch, tmp_path, capsys, outlet):
    advice, protected = prepare(monkeypatch, tmp_path, outlet, kernel=True, trusted=False)
    resolve = Mock(side_effect=AssertionError("outlet must not resolve before trust"))
    monkeypatch.setattr("code_forge.outlet_resolver.resolve_outlet", resolve)
    assert cli.main() == EXIT_CLI_ERROR
    output = capsys.readouterr()
    assert "test_assertion=" not in output.err
    assert "file access is not authorized" in output.err
    assert output.out == ""
    resolve.assert_not_called()
    advice.assert_not_called()
    assert_evidence_unchanged(protected)


@pytest.mark.parametrize(
    "error_type", [MemoryError, KeyboardInterrupt, SystemExit, asyncio.CancelledError]
)
def test_inline_fatal_dispatch_propagates_with_truthful_skip(monkeypatch, tmp_path, capsys, error_type):
    advice, protected = prepare(monkeypatch, tmp_path, "inline")
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--canary"])
    error = error_type("synthetic fatal")
    monkeypatch.setattr("code_forge.canary_gen.run_inline_canary", Mock(side_effect=error))
    args = cli._build_parser().parse_args(sys.argv[1:])
    with pytest.raises(error_type) as caught:
        cli._run(args, os.environ, tmp_path)
    assert caught.value is error
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err.splitlines().count(SUMMARY) == 1
    advice.assert_not_called()
    assert_evidence_unchanged(protected)


@pytest.mark.parametrize("status", list(AssertionStatus))
@pytest.mark.parametrize("stage", [None, *AssertionStage])
@pytest.mark.parametrize("count", [None, 0, 1, -1, True, "0"])
def test_unsupported_kind_has_one_canonical_shape(status, stage, count):
    if status is AssertionStatus.SKIPPED and stage is AssertionStage.ELIGIBILITY and count is None:
        outcome = AssertionOutcome(status, count, stage, AssertionKind.UNSUPPORTED_OUTLET)
        assert outcome.to_metadata() == {
            "status": "skipped",
            "finding_count": None,
            "stage": "eligibility",
            "kind": "unsupported_outlet",
        }
        assert outcome.legacy_outcome() == "skipped: unsupported outlet"
        assert legacy_progress_outcome(outcome.legacy_outcome()) == "skipped: unsupported outlet"
    else:
        with pytest.raises((TypeError, ValueError)):
            AssertionOutcome(status, count, stage, AssertionKind.UNSUPPORTED_OUTLET)


@pytest.mark.parametrize(
    "kernel,outlet", [(False, "subprocess"), (False, "subagent"), (True, "subprocess")]
)
def test_supported_routes_have_no_unsupported_summary_at_dispatch(
    monkeypatch, tmp_path, capsys, kernel, outlet
):
    advice, protected = prepare(monkeypatch, tmp_path, outlet, kernel=kernel)
    stop = RuntimeError("stop after outlet checks")
    dispatch = Mock(side_effect=stop)
    monkeypatch.setattr(cli, "_dispatch_inline_canary", dispatch)
    args = cli._build_parser().parse_args(sys.argv[1:])
    with pytest.raises(RuntimeError) as caught:
        cli._run(args, os.environ, tmp_path)
    assert caught.value is stop
    assert dispatch.call_args.args[0] == outlet
    output = capsys.readouterr()
    assert output.out == ""
    assert "test_assertion=" not in output.err
    advice.assert_not_called()
    assert_evidence_unchanged(protected)


def test_invalid_backend_flags_precede_outlet_status(monkeypatch, tmp_path, capsys):
    advice, protected = prepare(monkeypatch, tmp_path, "inline")
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--backend-url", "synthetic-invalid-url"])
    resolve = Mock(side_effect=AssertionError("preflight must precede outlet resolution"))
    monkeypatch.setattr("code_forge.outlet_resolver.resolve_outlet", resolve)
    assert cli.main() == EXIT_CLI_ERROR
    output = capsys.readouterr()
    assert "inline backend requires all 4 flags" in output.err
    assert "test_assertion=" not in output.err
    assert output.out == ""
    resolve.assert_not_called()
    advice.assert_not_called()
    assert_evidence_unchanged(protected)


def select_real_outlet(monkeypatch, tmp_path, source, outlet):
    monkeypatch.setattr("code_forge.outlet_resolver.resolve_outlet", real_resolve_outlet)
    monkeypatch.delenv("FORGE_OUTLET", raising=False)
    if source == "env":
        monkeypatch.setenv("FORGE_OUTLET", outlet)
    else:
        gate = tmp_path / ".code-forge" / "gate.yaml"
        data = yaml.safe_load(gate.read_text())
        data["outlet"] = outlet
        gate.write_text(yaml.safe_dump(data))


@pytest.mark.parametrize("mode", ["ci", "local"])
@pytest.mark.parametrize("source", ["env", "gate"])
@pytest.mark.parametrize("kernel,outlet", [(False, "inline"), (True, "inline"), (True, "subagent")])
def test_real_outlet_resolution_reaches_unsupported_summary(
    monkeypatch, tmp_path, capsys, mode, source, kernel, outlet
):
    advice, protected = prepare(monkeypatch, tmp_path, outlet, kernel=kernel, mode=mode)
    select_real_outlet(monkeypatch, tmp_path, source, outlet)
    assert cli.main() == (EXIT_CLI_ERROR if kernel else verdict_to_exit(Verdict.DELEGATED))
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err.splitlines().count(SUMMARY) == 1
    if kernel:
        assert (
            f"code-forge: error: kernel-context: outlet {outlet} is not supported; "
            "only the CLI subprocess review path is supported"
        ) in output.err.splitlines()
    advice.assert_not_called()
    assert_evidence_unchanged(protected)


@pytest.mark.parametrize("mode", ["ci", "local"])
@pytest.mark.parametrize("source", ["env", "gate"])
@pytest.mark.parametrize("kernel", [False, True])
def test_sampling_configuration_is_rejected_before_advisory_status(
    monkeypatch, tmp_path, capsys, mode, source, kernel
):
    advice, protected = prepare(monkeypatch, tmp_path, "sampling", kernel=kernel, mode=mode)
    select_real_outlet(monkeypatch, tmp_path, source, "sampling")
    dispatch = Mock(side_effect=AssertionError("sampling must not reach dispatch"))
    monkeypatch.setattr(cli, "_dispatch_inline_canary", dispatch)
    assert cli.main() == EXIT_CLI_ERROR
    output = capsys.readouterr()
    source_name = "FORGE_OUTLET env" if source == "env" else "gate.yaml outlet"
    assert (
        f"code-forge: error: invalid outlet 'sampling' from {source_name} "
        "(expected: inline|subagent|subprocess)"
    ) in output.err.splitlines()
    assert output.out == ""
    assert "test_assertion=" not in output.err
    dispatch.assert_not_called()
    advice.assert_not_called()
    assert_evidence_unchanged(protected)


def test_sampling_cli_flag_remains_an_argparse_error(monkeypatch, tmp_path, capsys):
    advice, protected = prepare(monkeypatch, tmp_path, "sampling")
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--outlet", "sampling"])
    resolve = Mock(side_effect=AssertionError("argparse must reject before resolution"))
    monkeypatch.setattr("code_forge.outlet_resolver.resolve_outlet", resolve)
    assert cli.main() == EXIT_CLI_ERROR
    output = capsys.readouterr()
    assert "argument --outlet: invalid choice: 'sampling'" in output.err
    assert "test_assertion=" not in output.err
    assert output.out == ""
    resolve.assert_not_called()
    advice.assert_not_called()
    assert_evidence_unchanged(protected)
