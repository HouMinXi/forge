"""Opt-in v1 diagnostic tickets through real host and new-process CLI."""

from pathlib import Path
import sys
import os
import json
import subprocess
from tests import test_cli_empty_diff as empty_diff
from uuid import uuid4
import pytest
from code_forge import cli
from code_forge import _diagnostic_history as history
from code_forge import _advisory_diagnostics as storage
from code_forge._cross_repo_diagnostics import DiagnosticCapture
from code_forge.cross_repo_impact import CrossRepoImpactRunner
from tests import test_cross_repo_immediate_diagnostic as immediate

graphs = immediate.graphs
primary_cli_output = immediate.primary_cli_output


def run_native(graphs, tmp_path, enabled=True):
    directory = tmp_path / "tickets"
    directory.mkdir(mode=0o700)
    owner = DiagnosticCapture(
        execution_id=str(uuid4()),
        source_hash="source",
        diff_text=immediate.DIFF,
        repo_root=graphs[0],
        excluded_roots=(),
        diagnostic_ticket_directory=directory if enabled else None,
    )
    messages = []
    runner = CrossRepoImpactRunner(_diagnostics=owner, output_fn=messages.append)
    findings = runner.run(immediate.DIFF, graphs[0])
    return owner, runner, messages, findings, directory


def test_real_host_export_cache_no_reexport_and_default_unchanged(graphs, tmp_path):
    owner, runner, messages, findings, directory = run_native(graphs, tmp_path)
    assert owner.diagnostic_ticket_status == "exported"
    assert history.read_ticket(owner.diagnostic_ticket_path).analysis_state == "no_named_targets"
    assert messages == [immediate.MESSAGE]
    owner.present(owner.records[-1], origin="cache")
    assert len(list(directory.iterdir())) == 1
    owner.close()
    assert history.read_ticket(owner.diagnostic_ticket_path).analysis_state == "no_named_targets"
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "code_forge.cli",
            "diagnostic-history",
            "--ticket",
            str(owner.diagnostic_ticket_path),
        ],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert json.loads(result.stdout)["analysis_state"] == "no_named_targets"
    assert json.loads(result.stdout)["current_run_credit"] is False
    for secret in (str(directory), str(graphs[0]), owner.execution_id, owner.repository_id):
        assert secret not in result.stdout + result.stderr


def test_no_opt_in_keeps_native_text_and_writes_no_tickets(graphs, tmp_path):
    owner, _, messages, _, directory = run_native(graphs, tmp_path, enabled=False)
    assert owner.diagnostic_ticket_status == "not_requested"
    assert messages == [immediate.MESSAGE]
    assert list(directory.iterdir()) == []


@pytest.mark.parametrize("error", [KeyboardInterrupt(), SystemExit(7)])
def test_export_cancel_propagates_identity(graphs, tmp_path, monkeypatch, error):
    def fail(*args):
        raise error

    monkeypatch.setattr(history, "export_ticket", fail)
    with pytest.raises(type(error)) as caught:
        run_native(graphs, tmp_path)
    assert caught.value is error


def test_export_failure_does_not_erase_genuine_diagnostic(graphs, tmp_path, monkeypatch):
    def fail(*args):
        raise PermissionError("PRIVATE PATH")

    monkeypatch.setattr(history, "export_ticket", fail)
    owner, _, messages, findings, directory = run_native(graphs, tmp_path)
    assert owner.diagnostic_ticket_status == "unavailable"
    assert messages == [immediate.MESSAGE]
    assert findings == [] and not list(directory.iterdir())


@pytest.mark.parametrize("failed_storage", [False, True])
def test_real_workers_report_only_primary_export_terminal(
    primary_cli_output, graphs, tmp_path, monkeypatch, capsys, failed_storage
):
    from code_forge import cross_repo

    directory = tmp_path / "tickets"
    directory.mkdir(mode=0o700)
    native = cross_repo.run_cross_repo

    def host(**kwargs):
        return native(**kwargs, diagnostic_ticket_directory=directory)

    monkeypatch.setattr(cross_repo, "run_cross_repo", host)
    if failed_storage:
        monkeypatch.setattr(
            storage,
            "publish_observation",
            lambda *a: storage.PublicationResult("unknown", failure="io_failed"),
        )
    primary_cli_output()
    lines = [x for x in capsys.readouterr().out.splitlines() if "diagnostic-ticket:" in x]
    assert len(lines) == 1 and "primary: ADVISORY:" in lines[0] and "consumer:" not in lines[0]
    assert ("unavailable" if failed_storage else "exported") in lines[0]
    assert str(directory) not in lines[0]


def test_dispatch_only_forwards_opt_in(monkeypatch, tmp_path):
    from code_forge.state import Verdict

    seen = []
    monkeypatch.setattr(cli, "_cross_repo_verdict_or_none", lambda **kw: seen.append(kw) or Verdict.PASS)
    assert cli._dispatch_cross_repo(*([None] * 11)) is Verdict.PASS
    assert "diagnostic_ticket_directory" not in seen[-1]
    assert cli._dispatch_cross_repo(*([None] * 11), diagnostic_ticket_directory=tmp_path) is Verdict.PASS
    assert seen[-1]["diagnostic_ticket_directory"] == tmp_path


@pytest.mark.parametrize("outlet", ["inline", "subagent", "sampling"])
def test_unsupported_outlets_refused_before_dispatch(tmp_path, monkeypatch, outlet):
    directory = tmp_path / "tickets"
    directory.mkdir(mode=0o700)
    args = cli._build_parser().parse_args(["review", "--diagnostic-ticket-dir", str(directory)])
    monkeypatch.setattr(cli, "is_git_repo", lambda root: False)
    monkeypatch.setattr(
        cli, "_load_gate_backends", lambda path: ({}, {"siblings": [{"repo": "sibling"}]})
    )
    monkeypatch.setattr(cli, "_merge_user_into", lambda cfg, gate: cfg)
    monkeypatch.setattr("code_forge.outlet_resolver.resolve_outlet", lambda *a, **kw: outlet)
    with pytest.raises(cli.CliError, match="only subprocess cross-repository"):
        cli._run(args, {}, tmp_path)
    assert not list(directory.iterdir())


def test_parser_default_and_single_repo_refusal(monkeypatch, tmp_path):
    assert cli._build_parser().parse_args(["review"]).diagnostic_ticket_dir is None
    assert cli._build_parser().parse_args(
        ["review", "--diagnostic-ticket-dir", "/x"]
    ).diagnostic_ticket_dir == Path("/x")
    monkeypatch.setattr(cli, "_cross_repo_verdict_or_none", lambda **kw: None)
    with pytest.raises(cli.CliError, match="requires cross-repository"):
        cli._dispatch_cross_repo(*([None] * 11), diagnostic_ticket_directory=tmp_path)


def test_failed_read_never_runs_review_or_echoes_private_path(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        sys, "argv", ["forge", "diagnostic-history", "--ticket", str(tmp_path / "SECRET")]
    )
    monkeypatch.setattr(cli, "_run", lambda *a, **kw: pytest.fail("review invoked"))
    assert cli.main() == 2
    output = capsys.readouterr()
    assert output.out == "" and output.err == "code-forge: historical diagnostic unavailable\n"


@pytest.mark.parametrize("change", ["closed", "thread", "pid", "identity"])
def test_export_requires_live_owner_context(tmp_path, change):
    import threading

    root = tmp_path / "repo"
    root.mkdir(mode=0o700)
    tickets = tmp_path / "tickets"
    tickets.mkdir(mode=0o700)
    owner = DiagnosticCapture(
        execution_id=str(uuid4()),
        source_hash="source",
        diff_text="",
        repo_root=root,
        excluded_roots=(),
        diagnostic_ticket_directory=tickets,
    )
    record = owner.finish("no_named_targets")
    if change == "closed":
        owner.close()
    elif change == "pid":
        owner._pid -= 1
    elif change == "identity":
        owner._root = tickets
    if change == "thread":
        thread = threading.Thread(target=owner.present, args=(record,))
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive()
    else:
        owner.present(record)
    assert not list(tickets.iterdir())
    assert owner.diagnostic_ticket_status == "unavailable"


def test_failed_second_observation_is_not_masked_by_first_export(tmp_path):
    from dataclasses import replace

    root = tmp_path / "repo"
    root.mkdir(mode=0o700)
    tickets = tmp_path / "tickets"
    tickets.mkdir(mode=0o700)
    owner = DiagnosticCapture(
        execution_id=str(uuid4()),
        source_hash="source",
        diff_text="",
        repo_root=root,
        excluded_roots=(),
        diagnostic_ticket_directory=tickets,
    )
    first = owner.finish("no_named_targets")
    owner.present(first)
    second = owner.finish("unavailable")
    second = replace(second, publication=storage.PublicationResult("unknown", failure="io_failed"))
    owner.present(second)
    assert owner.diagnostic_ticket_outcomes[1] is not None
    assert owner.diagnostic_ticket_outcomes[2] is None
    assert len(list(tickets.iterdir())) == 1


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
        ["--diagnostic-ticket-dir", str(directory)],
        extra_env={"PATH": str(Path(sys.executable).parent) + os.pathsep + env["PATH"]},
    )
    assert result.returncode == 0, result.stderr
    assert "no changes to review" in result.stderr
    assert result.stderr.count("diagnostic-ticket: no receipt was issued") == 1
    assert list(directory.iterdir()) == []
