"""Production CLI opt-in keeps the already completed legacy streams."""

from types import SimpleNamespace
import json

import pytest

from code_forge import cli, report_runtime_v2 as runtime
from code_forge.report_preparation_v2 import FinalText
from code_forge.state import State, Mode, Verdict, save_state


def state_file(tmp_path):
    path = tmp_path / "state.json"
    state = State(mode=Mode.CI, verdict=Verdict.ESCALATED)
    state.infra_errors = ["public failure"] * 5
    save_state(state, path)
    return path


def test_parser_default_and_explicit():
    parser = cli._build_parser()
    assert parser.parse_args(["review"]).report_version == "v1"
    assert parser.parse_args(["review", "--report-version", "v2"]).report_version == "v2"


def test_default_and_explicit_v1_exact(tmp_path, capsys, monkeypatch):
    path = state_file(tmp_path)
    monkeypatch.setattr(runtime, "prepare_production_report", lambda *a: pytest.fail("v1 called v2"))
    cli._emit_ci_output(path, {})
    implicit = capsys.readouterr()
    cli._emit_ci_output(path, {}, report_version="v1")
    assert capsys.readouterr() == implicit


def test_v2_receives_exact_baseline_and_single_context(tmp_path, capsys, monkeypatch):
    path = state_file(tmp_path)
    registry = {"fixture": SimpleNamespace(command="tool", args=[])}
    calls = []
    monkeypatch.setattr(cli, "capture_tool_version", lambda *a: calls.append("tool") or "1")
    monkeypatch.setattr(cli, "_load_advisories", lambda *a: calls.append("advisory") or [])
    cli._emit_ci_output(path, registry)
    baseline = capsys.readouterr()
    calls.clear()

    def select(state, context, text):
        assert text == FinalText(baseline.out, baseline.err)
        assert text.stdout.endswith("\n") and "infra:" in text.stderr
        assert context.tool_versions == {"fixture": "1"}
        assert context.resolved_locator == str(path.resolve())
        calls.append("v2")
        return SimpleNamespace(text=text, writer_reason="v2-prepared")

    monkeypatch.setattr(runtime, "prepare_production_report", select)
    cli._emit_ci_output(path, registry, report_version="v2", post_emit_hook=lambda: calls.append("hook"))
    assert capsys.readouterr() == baseline
    assert calls == ["tool", "advisory", "v2", "hook"]


def test_actual_v2_selection(tmp_path, capsys):
    cli._emit_ci_output(state_file(tmp_path), {}, report_version="v2")
    output = capsys.readouterr()
    assert runtime.select_report_bytes(output.out.encode()).selected_version == "v2"
    assert "report-v2 execution-diagnostics=" in output.err
    assert output.out.endswith("\n")


def test_actual_optional_fallback_exact(tmp_path, capsys, monkeypatch):
    path = state_file(tmp_path)
    cli._emit_ci_output(path, {})
    baseline = capsys.readouterr()
    monkeypatch.setattr(runtime, "SNAPSHOT_LIMITS", runtime.SnapshotLimits(1, 1, 1))
    cli._emit_ci_output(path, {}, report_version="v2")
    output = capsys.readouterr()
    assert output.out == baseline.out
    assert output.err == baseline.err + (
        "code-forge: report-v2 unavailable; using unchanged v1 report [snapshot-rejected]\n"
    )


@pytest.mark.parametrize("error", [RuntimeError("bug"), KeyboardInterrupt(), MemoryError()])
def test_v2_unexpected_errors_propagate_before_output(tmp_path, capsys, monkeypatch, error):
    def fail(*a):
        raise error

    monkeypatch.setattr(runtime, "prepare_production_report", fail)
    hooks = []
    with pytest.raises(type(error)) as caught:
        cli._emit_ci_output(
            state_file(tmp_path), {}, report_version="v2", post_emit_hook=lambda: hooks.append(1)
        )
    assert caught.value is error and not hooks
    assert capsys.readouterr() == ("", "")


def test_v2_hook_error_after_emission(tmp_path, capsys):
    error = RuntimeError("hook")

    def hook():
        raise error

    with pytest.raises(RuntimeError) as caught:
        cli._emit_ci_output(state_file(tmp_path), {}, report_version="v2", post_emit_hook=hook)
    assert caught.value is error
    assert json.loads(capsys.readouterr().out)["runs"]


@pytest.mark.parametrize(
    "flags",
    [
        ["--mode", "local"],
        ["--whole-file", "a.py"],
        ["--canary"],
        ["--outlet", "inline"],
        ["--outlet", "subagent"],
        ["--scope-ticket-dir", "/no-access"],
        ["--diagnostic-ticket-dir", "/no-access"],
    ],
)
def test_explicit_unsupported_before_any_execution(tmp_path, monkeypatch, flags):
    args = cli._build_parser().parse_args(["review", "--report-version", "v2", *flags])
    monkeypatch.setattr(cli, "is_git_repo", lambda *a: pytest.fail("executed repository probe"))
    with pytest.raises(cli.CliError, match="report-version"):
        cli._run(args, {}, tmp_path)


@pytest.mark.parametrize(
    "gate",
    [
        {"siblings": [{"name": "other"}]},
        {"canary": {"enabled": True}},
        {"kernel_context": {"enabled": True}},
    ],
)
def test_config_unsupported_before_backend_probe(tmp_path, monkeypatch, gate):
    args = cli._build_parser().parse_args(["review", "--report-version", "v2", "--mode", "ci"])
    monkeypatch.setattr(cli, "is_git_repo", lambda *a: False)
    monkeypatch.setattr(cli, "_load_gate_backends", lambda *a: ([], gate))
    monkeypatch.setattr(cli, "_load_gate_siblings", lambda *a: (gate, gate.get("siblings")))
    monkeypatch.setattr(cli, "_merge_user_into", lambda *a: pytest.fail("backend resolution reached"))
    with pytest.raises(cli.CliError, match="report-version"):
        cli._run(args, {}, tmp_path)


def test_normal_baseline_head_not_rejected(tmp_path, monkeypatch):
    args = cli._build_parser().parse_args(
        ["review", "--report-version", "v2", "--mode", "ci", "--baseline", "HEAD~1", "--head", "HEAD"]
    )

    class Reached(Exception):
        pass

    def reached(*a):
        raise Reached

    monkeypatch.setattr(cli, "is_git_repo", reached)
    with pytest.raises(Reached):
        cli._run(args, {}, tmp_path)


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_review_callsite_forwards_only_optin(tmp_path, monkeypatch, version):
    import subprocess
    import sys
    from code_forge.assertion_outcome import (
        AssertionOutcome,
        AssertionStatus,
        AssertionStage,
        AssertionKind,
    )

    def git(*args):
        subprocess.run(
            ["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )

    git("init", "-q")
    git("config", "user.name", "fixture")
    git("config", "user.email", "fixture@example.invalid")
    (tmp_path / "a.py").write_text("x = 1\n")
    git("add", "a.py")
    git("commit", "-qm", "fixture")
    (tmp_path / "a.py").write_text("x = 2\n")
    (tmp_path / ".code-forge").mkdir()
    (tmp_path / ".code-forge" / "tools.yaml").write_text("tools: {}\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("code_forge.outlet_resolver.resolve_outlet", lambda *a, **k: "subprocess")
    monkeypatch.setattr(cli, "_run_hold_loop", lambda **k: Verdict.FAIL)
    seen = []
    monkeypatch.setattr(cli, "_emit_ci_output", lambda *a, **k: seen.append(k))
    monkeypatch.setattr(
        cli,
        "_run_test_assertion_phase",
        lambda *a: AssertionOutcome(
            AssertionStatus.SKIPPED, None, AssertionStage.ELIGIBILITY, AssertionKind.NO_GIT_DIFF
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "code-forge",
            "review",
            "--mode",
            "ci",
            "--allow-main",
            "--falsification-engine",
            "stub",
            "--report-version",
            version,
            "a.py",
        ],
    )
    assert cli.main() == 1
    assert len(seen) == 1
    assert seen[0].get("report_version", "v1") == version
    assert ("report_version" in seen[0]) is (version == "v2")


@pytest.mark.parametrize("reason", ["snapshot-rejected", "private path /tmp/secret"])
def test_downgrade_notice_byte_budget_and_labels(reason):
    prefix = "baseline é\n"
    expected = cli._report_v2_downgrade_notice(prefix, reason, 4096)
    assert expected.startswith(prefix)
    assert "secret" not in expected
    exact = len(expected.encode("utf-8"))
    assert cli._report_v2_downgrade_notice(prefix, reason, exact) == expected
    assert cli._report_v2_downgrade_notice(prefix, reason, exact - 1) == prefix
    assert cli._report_v2_downgrade_notice("\ud800", reason, 4096) == "\ud800"


def test_actual_downgrade_notice_overflow_preserves_stderr(tmp_path, capsys, monkeypatch):
    from dataclasses import replace

    path = state_file(tmp_path)
    cli._emit_ci_output(path, {})
    baseline = capsys.readouterr()
    monkeypatch.setattr(runtime, "SNAPSHOT_LIMITS", runtime.SnapshotLimits(1, 1, 1))
    monkeypatch.setattr(
        runtime,
        "PREPARATION_LIMITS",
        replace(runtime.PREPARATION_LIMITS, candidate_stderr_bytes=len(baseline.err.encode("utf-8"))),
    )
    cli._emit_ci_output(path, {}, report_version="v2")
    assert capsys.readouterr() == baseline


@pytest.mark.parametrize("error", [MemoryError("private"), KeyboardInterrupt(), RuntimeError("private")])
def test_downgrade_preparation_failure_precedes_both_streams(tmp_path, capsys, monkeypatch, error):
    path = state_file(tmp_path)

    def fallback(state, context, baseline):
        return SimpleNamespace(text=baseline, writer_reason="snapshot-rejected")

    def fail(*args):
        raise error

    monkeypatch.setattr(runtime, "prepare_production_report", fallback)
    monkeypatch.setattr(cli, "_report_v2_downgrade_notice", fail)
    hooks = []
    with pytest.raises(type(error)) as caught:
        cli._emit_ci_output(path, {}, report_version="v2", post_emit_hook=lambda: hooks.append(True))
    assert caught.value is error
    output = capsys.readouterr()
    assert output.out == output.err == ""
    assert hooks == []
