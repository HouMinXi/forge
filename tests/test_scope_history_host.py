"""Explicit ticket opt-in preserves native review behavior and historical labels."""

import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from code_forge import cli
from code_forge import _scope_history as history
from code_forge._cross_repo_diagnostics import DiagnosticCapture
from code_forge.cross_repo_impact import CrossRepoImpactRunner
from tests import test_cross_repo_immediate_diagnostic as immediate
from tests import test_cross_repo_query_summary as summary
from tests import test_cli_empty_diff as empty_diff

graphs = immediate.graphs


def run_native(graphs, tmp_path, *, enabled=True, output=None):
    primary = graphs[0]
    summary.named(primary)
    directory = tmp_path / "tickets"
    directory.mkdir(mode=0o700)
    owner = DiagnosticCapture(
        execution_id=str(uuid4()),
        source_hash="review-source",
        diff_text=immediate.DIFF,
        repo_root=primary,
        excluded_roots=(),
        ticket_directory=directory if enabled else None,
    )
    messages = [] if output is None else output
    runner = CrossRepoImpactRunner(_diagnostics=owner, output_fn=messages.append)
    findings = runner.run(immediate.DIFF, primary)
    return owner, runner, messages, findings, directory


def test_real_host_exports_after_independent_read_and_cli_reads_after_close(graphs, tmp_path):
    owner, runner, messages, findings, directory = run_native(graphs, tmp_path)
    assert owner.scope_ticket_status == "exported"
    ticket = owner.scope_ticket_path
    assert ticket.parent == directory and ticket.is_file()
    assert messages == [summary.SUMMARY % (1, 1, 1, 0), "scope-ticket: exported " + ticket.name]
    original = list(messages)
    assert runner.run(immediate.DIFF, graphs[0]) == findings
    assert messages == original and len(list(directory.iterdir())) == 1
    owner.close()
    result = subprocess.run(
        [sys.executable, "-B", "-m", "code_forge.cli", "scope-history", "--ticket", str(ticket)],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    value = json.loads(result.stdout)
    assert value["kind"] == "historical-query-scope"
    assert value["current_run_credit"] is False
    assert value["snapshot"]["registered"] == value["snapshot"]["completed"] == 1
    for secret in [str(graphs[0]), owner.execution_id, owner.source_hash, owner.diff_sha256]:
        assert secret not in result.stdout + result.stderr


def test_no_opt_in_keeps_text_and_writes_no_tickets(graphs, tmp_path):
    owner, _, messages, _, directory = run_native(graphs, tmp_path, enabled=False)
    assert owner.scope_ticket_status == "not_requested"
    assert messages == [summary.SUMMARY % (1, 1, 1, 0)]
    assert list(directory.iterdir()) == []


def test_export_failure_keeps_real_summary_and_does_not_claim_no_file(graphs, tmp_path, monkeypatch):
    def fail(*args):
        raise PermissionError("PRIVATE PATH SECRET")

    monkeypatch.setattr(history, "export_ticket", fail)
    owner, _, messages, findings, directory = run_native(graphs, tmp_path)
    assert owner.scope_ticket_status == "unavailable"
    assert owner.scope_delivery_status == "delivered"
    assert messages == [
        summary.SUMMARY % (1, 1, 1, 0),
        "scope-ticket: unavailable; no saved-ticket guarantee",
    ]
    assert findings == [] and list(directory.iterdir()) == []


@pytest.mark.parametrize("error", [KeyboardInterrupt(), SystemExit(7)])
def test_export_cancellation_propagates_same_object(graphs, tmp_path, monkeypatch, error):
    def fail(*args):
        raise error

    monkeypatch.setattr(history, "export_ticket", fail)
    with pytest.raises(type(error)) as caught:
        run_native(graphs, tmp_path)
    assert caught.value is error


def test_history_cli_refusal_is_bounded_and_never_runs_review(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        sys, "argv", ["code-forge", "scope-history", "--ticket", str(tmp_path / "PRIVATE")]
    )
    monkeypatch.setattr(cli, "_run", lambda *a, **kw: pytest.fail("historical lookup ran review"))
    assert cli.main() == 2
    output = capsys.readouterr()
    assert output.out == "" and output.err == "code-forge: historical scope unavailable\n"


@pytest.mark.parametrize("error", [KeyboardInterrupt(), SystemExit(9)])
def test_history_cli_propagates_cancel(monkeypatch, error):
    def fail(path):
        raise error

    monkeypatch.setattr(history, "read_ticket", fail)
    monkeypatch.setattr(sys, "argv", ["code-forge", "scope-history", "--ticket", "/explicit"])
    with pytest.raises(type(error)) as caught:
        cli.main()
    assert caught.value is error


def test_explicit_directory_option_parses_without_changing_default():
    parser = cli._build_parser()
    assert parser.parse_args(["review"]).scope_ticket_dir is None
    assert parser.parse_args(["review", "--scope-ticket-dir", "/explicit"]).scope_ticket_dir == Path(
        "/explicit"
    )


def test_cross_repo_dispatch_transfers_opt_in_only(monkeypatch, tmp_path):
    seen = []
    from code_forge.state import Verdict

    monkeypatch.setattr(cli, "_cross_repo_verdict_or_none", lambda **kw: seen.append(kw) or Verdict.PASS)
    args = [None] * 11
    assert cli._dispatch_cross_repo(*args) is Verdict.PASS
    assert "scope_ticket_directory" not in seen[-1]
    assert cli._dispatch_cross_repo(*args, scope_ticket_directory=tmp_path) is Verdict.PASS
    assert seen[-1]["scope_ticket_directory"] == tmp_path


def test_single_repo_cannot_silently_accept_export_request(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_cross_repo_verdict_or_none", lambda **kw: None)
    with pytest.raises(cli.CliError, match="requires cross-repository"):
        cli._dispatch_cross_repo(*([None] * 11), scope_ticket_directory=tmp_path)


@pytest.mark.parametrize("outlet", ["inline", "subagent", "sampling"])
def test_real_run_refuses_unsupported_outlet_before_dispatch(tmp_path, monkeypatch, outlet):
    tickets = tmp_path / "tickets"
    tickets.mkdir(mode=0o700)
    args = cli._build_parser().parse_args(["review", "--scope-ticket-dir", str(tickets)])
    monkeypatch.setattr(cli, "is_git_repo", lambda root: False)
    monkeypatch.setattr(
        cli, "_load_gate_backends", lambda path: ({}, {"siblings": [{"repo": "sibling"}]})
    )
    monkeypatch.setattr(cli, "_merge_user_into", lambda cfg, gate: cfg)
    monkeypatch.setattr("code_forge.outlet_resolver.resolve_outlet", lambda *a, **k: outlet)
    monkeypatch.setattr(
        cli, "_dispatch_inline_canary", lambda *a, **k: pytest.fail("unsupported dispatch occurred")
    )
    with pytest.raises(cli.CliError, match="only subprocess cross-repository"):
        cli._run(args, {}, tmp_path)
    assert list(tickets.iterdir()) == []


def test_real_run_refuses_single_repo_before_outlet_resolution(tmp_path, monkeypatch):
    tickets = tmp_path / "tickets"
    tickets.mkdir(mode=0o700)
    args = cli._build_parser().parse_args(["review", "--scope-ticket-dir", str(tickets)])
    monkeypatch.setattr(cli, "is_git_repo", lambda root: False)
    monkeypatch.setattr(cli, "_load_gate_backends", lambda path: ({}, {}))
    monkeypatch.setattr(
        "code_forge.outlet_resolver.resolve_outlet",
        lambda *a, **k: pytest.fail("resolved unsupported outlet"),
    )
    with pytest.raises(cli.CliError, match="requires cross-repository"):
        cli._run(args, {}, tmp_path)


primary_cli_output = immediate.primary_cli_output


@pytest.mark.parametrize("failed_storage", [False, True])
def test_real_workers_only_primary_reports_ticket_terminal(
    primary_cli_output, graphs, tmp_path, monkeypatch, capsys, failed_storage
):
    from code_forge import cross_repo
    from code_forge import _query_scope_receipt as scope
    from code_forge import _advisory_diagnostics as storage

    directory = tmp_path / "tickets"
    directory.mkdir(mode=0o700)
    summary.named(graphs[0])
    native = cross_repo.run_cross_repo

    def host(**kwargs):
        return native(**kwargs, scope_ticket_directory=directory)

    monkeypatch.setattr(cross_repo, "run_cross_repo", host)
    if failed_storage:
        monkeypatch.setattr(
            scope, "publish", lambda *a: storage.PublicationResult("unknown", failure="io_failed")
        )
    primary_cli_output()
    lines = [x for x in capsys.readouterr().out.splitlines() if "scope-ticket:" in x]
    assert len(lines) == 1
    assert "primary: ADVISORY:" in lines[0] and "consumer:" not in lines[0]
    assert ("unavailable" if failed_storage else "exported") in lines[0]


scratch_repo = empty_diff.scratch_repo


def test_real_empty_diff_explicitly_reports_no_receipt(scratch_repo):
    import os

    directory = scratch_repo / "tickets"
    directory.mkdir(mode=0o700)
    gate = scratch_repo / ".code-forge/gate.yaml"
    gate.write_text(gate.read_text() + "siblings:\n  - repo: ../sibling\n    ref: main..feature\n")
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(scratch_repo),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
    }
    subprocess.run(
        [sys.executable, "-B", "-m", "code_forge.cli", "trust"],
        cwd=scratch_repo,
        env=env,
        capture_output=True,
        check=True,
        timeout=10,
    )
    result = empty_diff._run_review(
        scratch_repo,
        ["--scope-ticket-dir", str(directory)],
        extra_env={"PATH": str(Path(sys.executable).parent) + os.pathsep + env["PATH"]},
    )
    assert result.returncode == 0, result.stderr
    assert "no changes to review" in result.stderr
    assert result.stderr.count("scope-ticket: no receipt was issued") == 1
    assert list(directory.iterdir()) == []
