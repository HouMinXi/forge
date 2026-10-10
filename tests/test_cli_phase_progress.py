# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""The review verdict precedes optional work; the command terminates after it."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from code_forge import cli
from code_forge.llm_invoke import LLMInvokeError
from code_forge.state import Verdict


TEST_DIFF = (
    "diff --git a/tests/test_example.py b/tests/test_example.py\n"
    "--- a/tests/test_example.py\n"
    "+++ b/tests/test_example.py\n"
    "@@ -1,2 +1,3 @@\n"
    " def test_example():\n"
    "+    assert 2 == 2\n"
    "     assert 1 == 1\n"
)
CODE_DIFF = (
    "diff --git a/src/example.py b/src/example.py\n"
    "--- a/src/example.py\n"
    "+++ b/src/example.py\n"
    "@@ -1 +1,2 @@\n"
    " VALUE = 1\n"
    "+VALUE = 2\n"
)
VALID_REVIEW = json.dumps({
    "findings": [],
    "code_excerpts": [{
        "file": "tests/test_example.py",
        "start_line": 1,
        "end_line": 3,
        "content": "def test_example():\n    assert 2 == 2\n    assert 1 == 1\n",
    }],
})


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _review_repo(tmp_path: Path, *, test_file: bool = True) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    forge_dir = repo / ".code-forge"
    forge_dir.mkdir()
    (forge_dir / "tools.yaml").write_text("tools: {}\n")
    source = repo / ("tests/test_example.py" if test_file else "example.py")
    source.parent.mkdir(exist_ok=True)
    source.write_text("def test_example():\n    assert 1 == 1\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
         "-c", "core.hooksPath=/dev/null", "commit", "-qm", "fixture")
    source.write_text("def test_example():\n    assert 2 == 2\n    assert 1 == 1\n")
    return repo


def _prepare_main(monkeypatch, repo: Path, outlet: str, verdict: Verdict) -> None:
    config_dir = repo.parent / "user-config"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text("backends: {}\n")
    cache_dir = repo.parent / "user-cache"
    cache_dir.mkdir()
    monkeypatch.setenv("FORGE_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_dir))
    monkeypatch.delenv("FORGE_BACKEND", raising=False)
    monkeypatch.chdir(repo)
    monkeypatch.setattr(sys, "argv", [
        "code-forge", "review", "--mode", "ci", "--falsification-engine", "stub",
        "--allow-main",
    ])
    monkeypatch.setattr(
        "code_forge.outlet_resolver.resolve_outlet", lambda *args, **kwargs: outlet,
    )
    monkeypatch.setattr(cli, "_run_hold_loop", lambda **kwargs: verdict)
    monkeypatch.setattr("code_forge.outlet_c.run_outlet_c", lambda **kwargs: verdict)


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("success", "test-assertion phase done: findings=0"),
        ("failure", "test-assertion phase failed: error"),
        ("timeout", "test-assertion phase failed: timeout"),
        ("no-tests", "test-assertion phase skipped: no test files"),
    ],
)
def test_assertion_phase_reports_actual_outcome(monkeypatch, capsys, kind, expected):
    calls = []

    def invoke(*args, **kwargs):
        calls.append(1)
        if kind == "failure":
            raise RuntimeError("backend failed")
        if kind == "timeout":
            raise LLMInvokeError("backend timed out", is_timeout=True)
        return SimpleNamespace(content=VALID_REVIEW)

    monkeypatch.setattr("code_forge.llm_invoke.llm_invoke", invoke)
    cli._run_test_assertion_phase(CODE_DIFF if kind == "no-tests" else TEST_DIFF)
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 2
    assert "test-assertion phase start" in lines[0]
    assert expected in lines[1]
    assert re.search(r"elapsed=\d+\.\ds", lines[1])
    assert len(calls) == (0 if kind == "no-tests" else 1)


@pytest.mark.parametrize("outlet", ["subprocess", "subagent"])
@pytest.mark.parametrize("verdict", [Verdict.PASS, Verdict.FAIL])
def test_both_outlets_finish_after_assertion(monkeypatch, tmp_path, capsys, outlet, verdict):
    repo = _review_repo(tmp_path)
    _prepare_main(monkeypatch, repo, outlet, verdict)
    monkeypatch.setattr(
        "code_forge.llm_invoke.llm_invoke",
        lambda *args, **kwargs: SimpleNamespace(content=VALID_REVIEW),
    )
    exit_code = cli.main()
    lines = capsys.readouterr().err.splitlines()
    starts = [i for i, line in enumerate(lines) if "test-assertion phase start" in line]
    ends = [i for i, line in enumerate(lines) if "test-assertion phase done" in line]
    terminals = [i for i, line in enumerate(lines) if "command done:" in line]
    assert len(starts) == len(ends) == len(terminals) == 1
    assert starts[0] < ends[0] < terminals[0]
    assert f"verdict={verdict.value} exit={exit_code}" in lines[terminals[0]]
    assert exit_code == (0 if verdict == Verdict.PASS else 1)


def test_slow_assertion_keeps_command_open(monkeypatch, tmp_path, capsys):
    repo = _review_repo(tmp_path)
    _prepare_main(monkeypatch, repo, "subprocess", Verdict.FAIL)
    entered = threading.Event()
    release = threading.Event()
    result = []

    def slow_invoke(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return SimpleNamespace(content=VALID_REVIEW)

    monkeypatch.setattr("code_forge.llm_invoke.llm_invoke", slow_invoke)
    worker = threading.Thread(target=lambda: result.append(cli.main()), daemon=True)
    worker.start()
    try:
        assert entered.wait(5)
        before = capsys.readouterr().err
        assert "test-assertion phase start" in before
        assert "command done:" not in before
        assert worker.is_alive()
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert result == [1]
    after = capsys.readouterr().err
    assert after.index("test-assertion phase done") < after.index("command done:")


@pytest.mark.parametrize(
    ("outlet", "verdict", "failure", "status"),
    [
        ("subprocess", Verdict.FAIL, "timeout", "failed: timeout"),
        ("subagent", Verdict.PASS, "failure", "failed: error"),
    ],
)
def test_advisory_failure_preserves_main_verdict(
    monkeypatch, tmp_path, capsys, outlet, verdict, failure, status
):
    repo = _review_repo(tmp_path)
    _prepare_main(monkeypatch, repo, outlet, verdict)

    def fail(*args, **kwargs):
        if failure == "timeout":
            raise LLMInvokeError("timed out", is_timeout=True)
        raise RuntimeError("invalid output")

    monkeypatch.setattr("code_forge.llm_invoke.llm_invoke", fail)
    exit_code = cli.main()
    stderr = capsys.readouterr().err
    assert "test-assertion phase " + status in stderr
    assert f"command done: verdict={verdict.value} exit={exit_code}" in stderr
    assert exit_code == (0 if verdict == Verdict.PASS else 1)


def test_no_test_file_skips_phase_before_terminal(monkeypatch, tmp_path, capsys):
    repo = _review_repo(tmp_path, test_file=False)
    _prepare_main(monkeypatch, repo, "subprocess", Verdict.PASS)

    def unexpected_call(*args, **kwargs):
        raise AssertionError("a non-test diff must not call the model")

    monkeypatch.setattr("code_forge.llm_invoke.llm_invoke", unexpected_call)
    assert cli.main() == 0
    stderr = capsys.readouterr().err
    assert "test-assertion phase skipped: no test files" in stderr
    assert stderr.index("test-assertion phase skipped") < stderr.index("command done:")


def test_memory_error_aborts_phase_without_claiming_success(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise MemoryError("injected")

    monkeypatch.setattr("code_forge.llm_invoke.llm_invoke", fail)
    with pytest.raises(MemoryError):
        cli._run_test_assertion_phase(TEST_DIFF)
    stderr = capsys.readouterr().err
    assert "test-assertion phase start" in stderr
    assert "test-assertion phase aborted" in stderr
    assert "test-assertion phase done" not in stderr


def test_advisory_output_precedes_phase_end(monkeypatch, capsys):
    def findings(*args, on_outcome, **kwargs):
        on_outcome("done")
        return [SimpleNamespace(description="check this assertion")]

    monkeypatch.setattr(cli, "_run_test_assertion_review", findings)
    cli._run_test_assertion_phase(TEST_DIFF)
    stderr = capsys.readouterr().err
    assert stderr.index("test-assertion phase start") < stderr.index(
        "[test-assertion] check this assertion"
    ) < stderr.index("test-assertion phase done: findings=1")


def test_offline_cli_subprocess_stays_live_during_post_review(tmp_path):
    """Exercise the actual parser, Git diff, stderr, process, and exit code."""
    repo = _review_repo(tmp_path)
    config_dir = tmp_path / "child-config"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text("backends: {}\n")
    cache_dir = tmp_path / "child-cache"
    cache_dir.mkdir()
    entered = tmp_path / "entered"
    release = tmp_path / "release"
    log = tmp_path / "stderr.log"
    child = (
        "import os, sys, time\n"
        "from pathlib import Path\n"
        "from types import SimpleNamespace\n"
        "from code_forge import cli, llm_invoke, outlet_resolver\n"
        "from code_forge.state import Verdict\n"
        "outlet_resolver.resolve_outlet = lambda *a, **k: 'subprocess'\n"
        "cli._run_hold_loop = lambda **k: Verdict.FAIL\n"
        "def review(*a, **k):\n"
        "    Path(os.environ['FORGE_TEST_ENTERED']).write_text('entered')\n"
        "    deadline = time.monotonic() + 5\n"
        "    while not Path(os.environ['FORGE_TEST_RELEASE']).exists():\n"
        "        if time.monotonic() >= deadline: raise TimeoutError('fixture release')\n"
        "        time.sleep(0.01)\n"
        "    return SimpleNamespace(content=os.environ['FORGE_TEST_REVIEW_JSON'])\n"
        "llm_invoke.llm_invoke = review\n"
        "sys.argv = ['code-forge', 'review', '--mode', 'ci', '--falsification-engine', "
        "'stub', '--allow-main']\n"
        "raise SystemExit(cli.main())\n"
    )
    env = dict(os.environ)
    env.update({
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "FORGE_CONFIG_DIR": str(config_dir),
        "XDG_CACHE_HOME": str(cache_dir),
        "FORGE_TEST_ENTERED": str(entered),
        "FORGE_TEST_RELEASE": str(release),
        "FORGE_TEST_REVIEW_JSON": VALID_REVIEW,
    })
    env.pop("FORGE_BACKEND", None)
    with log.open("w+") as stderr:
        proc = subprocess.Popen(
            [sys.executable, "-c", child], cwd=repo, env=env,
            stdout=subprocess.DEVNULL, stderr=stderr,
        )
        try:
            deadline = time.monotonic() + 10
            while not entered.exists() and proc.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert entered.exists(), log.read_text()
            assert proc.poll() is None
            stderr.flush()
            live_log = log.read_text()
            assert "test-assertion phase start" in live_log
            assert "command done:" not in live_log
        finally:
            release.write_text("release")
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
                raise
    assert proc.returncode == 1
    final_log = log.read_text()
    assert final_log.index("test-assertion phase done") < final_log.index(
        "command done: verdict=FAIL exit=1"
    )


def test_terminal_event_follows_error_handling(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["code-forge", "review"])

    def fail(*args, **kwargs):
        raise cli.CliError("setup failed")

    monkeypatch.setattr(cli, "_run", fail)
    assert cli.main() == cli.EXIT_CLI_ERROR
    stderr = capsys.readouterr().err
    assert stderr.index("code-forge: error: setup failed") < stderr.index("command done: exit=2")


@pytest.mark.parametrize(
    ("verdict", "exit_code"),
    [(Verdict.PENDING, cli.EXIT_BUSY)],
)
def test_pending_has_real_busy_terminal_exit(monkeypatch, capsys, verdict, exit_code):
    monkeypatch.setattr(sys, "argv", ["code-forge", "review"])
    monkeypatch.setattr(cli, "_run", lambda *args, **kwargs: verdict)
    assert cli.main() == exit_code
    assert f"command done: verdict=PENDING exit={exit_code}" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("error", "exit_code"),
    [(SystemExit(7), cli.EXIT_CLI_ERROR), (KeyboardInterrupt(), 130)],
)
def test_interrupt_and_internal_exit_mark_terminal(monkeypatch, capsys, error, exit_code):
    monkeypatch.setattr(sys, "argv", ["code-forge", "review"])

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(cli, "_run", fail)
    if isinstance(error, KeyboardInterrupt):
        with pytest.raises(SystemExit) as raised:
            cli.main()
        assert raised.value.code == 130
    else:
        assert cli.main() == exit_code
    assert f"command done: exit={exit_code}" in capsys.readouterr().err
