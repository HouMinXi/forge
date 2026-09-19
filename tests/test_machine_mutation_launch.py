"""The async mutation run must outlive the review process that starts it.

If a daemon thread is killed the moment the interpreter's main thread exits,
the mutation run never gets past the "running" marker it writes on entry: the
next round then reads a dead PID and dismisses the gate. The run must be executed
in a detached session (start_new_session=True) starting a completely separate
subprocess that will survive after the CLI process terminates.

This is asserted on the Popen call.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

from code_forge import mutation as mutation_module
from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.falsify import StubFalsifier
from code_forge.machine import StateMachine
from code_forge.state import Mode, Verdict


def _make_ci_sm(tmp_path):
    return StateMachine(
        mode=Mode.CI,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda f: None,
        resolved_review=ResolvedReview(
            source_files=[Path("test.py")],
            baseline_content=None,
            git_diff="diff --git a/test.py b/test.py\n",
            mode_hint="git",
        ),
        source_hash="abc",
        baseline_spec_repr="empty",
        cwd=tmp_path,
        registry={},
        l0_runner=lambda reg, files: ([], []),
    )


class TestAsyncMutationLaunch:
    def test_mutation_run_is_detached(self, tmp_path, monkeypatch):
        captured = {}

        class _RecordingPopen:
            def __init__(self, args, **kw):
                captured["args"] = args
                captured.update(kw)
                self.pid = 99999

        monkeypatch.setattr(
            mutation_module.subprocess, "Popen", _RecordingPopen
        )
        monkeypatch.setattr(
            shutil, "which", lambda name: "/usr/bin/" + name
        )
        monkeypatch.setattr(
            "code_forge.gate_check.load_gate_config",
            lambda p: {"test": {"command": ["pytest", "-q"]}},
        )
        monkeypatch.setattr(
            StateMachine, "_execute_round", lambda self, round_index: None
        )

        gate_dir = tmp_path / ".code-forge"
        gate_dir.mkdir(exist_ok=True)
        sm = _make_ci_sm(tmp_path)
        sm._run_ci()

        assert "args" in captured, (
            "mutation subprocess was never started; the launch path did not run "
            "and this test asserts nothing"
        )
        assert captured.get("start_new_session") is True, (
            "mutation subprocess was launched with start_new_session={!r}. A regular "
            "subprocess might be killed when the reviewing shell exits, so the run dies before "
            "writing its result and the gate silently degrades to SKIPPED.".format(captured.get("start_new_session"))
        )

    def test_unusable_gate_config_is_recorded_not_swallowed(
        self, tmp_path, monkeypatch
    ):
        """A gate.yaml without test.command must not skip mutation silently.

        This is the shape a worktree actually carries: .code-forge/ is
        gitignored per directory, so a worktree gate.yaml can hold outlet
        and backends while missing the test section entirely.
        """
        gate_dir = tmp_path / ".code-forge"
        gate_dir.mkdir()
        (gate_dir / "gate.yaml").write_text(
            "outlet: subprocess\n"
            "backends:\n"
            "  some-backend:\n"
            "    type: api\n",
            encoding="utf-8",
        )

        captured = []

        class _RecordingPopen:
            def __init__(self, args, **kw):
                captured.append(args)
                self.pid = 99999

        monkeypatch.setattr(
            mutation_module.subprocess, "Popen", _RecordingPopen
        )
        monkeypatch.setattr(
            shutil, "which", lambda name: "/usr/bin/" + name
        )
        monkeypatch.setattr(
            StateMachine, "_execute_round", lambda self, round_index: None
        )

        sm = _make_ci_sm(tmp_path)
        sm._run_ci()

        # The mutation launcher spawns `sys.executable -c <script>`; a CI run
        # may also legitimately spawn other subprocesses (e.g. resolve_ledger_
        # root's `git rev-parse` via subprocess.run, which internally uses
        # Popen). Assert no MUTATION process launched, not "no Popen at all".
        mutation_launches = [
            a for a in captured
            if isinstance(a, (list, tuple)) and len(a) >= 2
            and a[0] == sys.executable and a[1] == "-c"
        ]
        assert not mutation_launches, (
            "mutation launched despite an unusable gate config; this test "
            "no longer exercises the skip path it claims to"
        )
        assert any(
            "test.command" in e
            for e in sm._state.infra_errors
        ), (
            "mutation was skipped for an unusable gate.yaml and left no "
            f"trace: infra_errors={sm._state.infra_errors!r}. The verdict then reads identically to "
            "one where the mutation gate actually ran."
        )

    def test_pid_none_in_result_file_defers_not_launches(
        self, tmp_path, monkeypatch
    ):
        """When mutation-result.json has pid=None and status=running,
        _run_ci must return PENDING without launching a duplicate mutation.

        Bug-injection: remove the `return Verdict.PENDING` guard -> this
        test FAILS because a second Popen is started, overwriting the
        result file while the first child is still starting.
        """
        gate_dir = tmp_path / ".code-forge"
        gate_dir.mkdir()

        # Pre-write a result file with pid=None (child hasn't started yet)
        result_path = gate_dir / "mutation-result.json"
        import time as _time
        result_path.write_text(
            json.dumps({
                "pid": None,
                "started_at": _time.time(),
                "status": "running",
                "survivors": [],
            }),
            encoding="utf-8",
        )

        launched = []

        class _RecordingPopen:
            def __init__(self, args, **kw):
                launched.append(True)
                self.pid = 88888

        monkeypatch.setattr(
            mutation_module.subprocess, "Popen", _RecordingPopen
        )
        monkeypatch.setattr(
            shutil, "which", lambda name: "/usr/bin/" + name
        )
        monkeypatch.setattr(
            "code_forge.gate_check.load_gate_config",
            lambda p: {"test": {"command": ["pytest", "-q"]}},
        )
        monkeypatch.setattr(
            StateMachine, "_execute_round", lambda self, round_index: None
        )

        sm = _make_ci_sm(tmp_path)
        verdict = sm._run_ci()

        assert not launched, (
            "a duplicate mutation was launched even though "
            "mutation-result.json already had status=running with pid=None"
        )
        assert verdict == Verdict.PENDING, (
            f"expected PENDING to defer to next round, got {verdict!r}"
        )

    def test_missing_gate_yaml_records_specific_error(
        self, tmp_path, monkeypatch
    ):
        """When gate.yaml does not exist at all, the infra_error must
        say 'gate.yaml not found', not the generic 'test.command not
        configured' message.

        Bug-injection: change the FileNotFoundError message back to the
        generic one -> this test FAILS because the message no longer
        distinguishes "file missing" from "file exists but malformed".
        """
        monkeypatch.setattr(
            shutil, "which", lambda name: "/usr/bin/" + name
        )
        monkeypatch.setattr(
            StateMachine, "_execute_round", lambda self, round_index: None
        )

        # No .code-forge directory at all -> FileNotFoundError on load_gate_config
        sm = _make_ci_sm(tmp_path)
        sm._run_ci()

        assert any(
            "gate.yaml not found" in e
            for e in sm._state.infra_errors
        ), (
            "missing gate.yaml should produce a specific 'not found' error, "
            f"got infra_errors={sm._state.infra_errors!r}"
        )

    def test_launch_creates_parent_directory_for_result_file(
        self, tmp_path, monkeypatch
    ):
        """launch_detached_mutation must mkdir the parent of result_path
        before writing. Without the mkdir call, writing to a non-existent
        directory raises FileNotFoundError and the function returns None.

        Bug-injection: remove result_path.parent.mkdir() -> this test FAILS
        because the initial write raises FileNotFoundError.
        """
        # Point result_path at a directory that does NOT exist yet
        nested = tmp_path / "deep" / "nested" / ".code-forge"
        result_path = nested / "mutation-result.json"

        monkeypatch.setattr(
            mutation_module.subprocess, "Popen",
            lambda *a, **kw: type("P", (), {
                "pid": 77777,
                "wait": lambda self, timeout=None: 0,
            })(),
        )

        pid = mutation_module.launch_detached_mutation(
            diff_files=["test.py"],
            baseline_cmd=["pytest", "-q"],
            cwd=tmp_path,
            result_path=result_path,
        )

        assert pid is True, (
            "launch_detached_mutation reported a failed start -- the "
            "initial write to result_path probably failed because the "
            "parent directory "
            "was not created"
        )
        assert result_path.exists(), (
            "result_path does not exist after launch; mkdir is missing"
        )

    def test_stale_pid_none_relaunches_after_timeout(
        self, tmp_path, monkeypatch
    ):
        """When mutation-result.json has pid=None and started_at is older
        than 120s, the child likely crashed before writing its PID. The
        stale file must be unlinked and a new mutation launched.

        Bug-injection: remove the staleness check -> this test FAILS
        because the code returns PENDING forever instead of re-launching.
        """
        import time as _time

        gate_dir = tmp_path / ".code-forge"
        gate_dir.mkdir()

        result_path = gate_dir / "mutation-result.json"
        result_path.write_text(
            json.dumps({
                "pid": None,
                "started_at": _time.time() - 200,
                "status": "running",
                "survivors": [],
            }),
            encoding="utf-8",
        )

        launched = []

        class _RecordingPopen:
            def __init__(self, args, **kw):
                launched.append(True)
                self.pid = 88888

        monkeypatch.setattr(
            mutation_module.subprocess, "Popen", _RecordingPopen
        )
        monkeypatch.setattr(
            shutil, "which", lambda name: "/usr/bin/" + name
        )
        monkeypatch.setattr(
            "code_forge.gate_check.load_gate_config",
            lambda p: {"test": {"command": ["pytest", "-q"]}},
        )
        monkeypatch.setattr(
            StateMachine, "_execute_round", lambda self, round_index: None
        )

        sm = _make_ci_sm(tmp_path)
        sm._run_ci()

        assert launched, (
            "stale mutation-result.json (pid=None, started_at 200s ago) "
            "should have been unlinked and a new mutation launched, but "
            "no Popen was called"
        )

    def test_launch_failure_records_infra_error(
        self, tmp_path, monkeypatch
    ):
        """If launch_detached_mutation fails (Popen raises), the caller
        must record an infra error. Without this, the result file stays
        with pid=None and the next round returns PENDING forever.

        Bug-injection: remove the `if pid is None` check in machine.py
        -> this test FAILS because no infra error is recorded.
        """
        gate_dir = tmp_path / ".code-forge"
        gate_dir.mkdir()

        def _failing_popen(*a, **kw):
            raise OSError("simulated Popen failure")

        monkeypatch.setattr(
            mutation_module.subprocess, "Popen", _failing_popen
        )
        monkeypatch.setattr(
            shutil, "which", lambda name: "/usr/bin/" + name
        )
        monkeypatch.setattr(
            "code_forge.gate_check.load_gate_config",
            lambda p: {"test": {"command": ["pytest", "-q"]}},
        )
        monkeypatch.setattr(
            StateMachine, "_execute_round", lambda self, round_index: None
        )

        sm = _make_ci_sm(tmp_path)
        sm._run_ci()

        assert any(
            "failed to start" in e
            for e in sm._state.infra_errors
        ), (
            f"Popen failure should produce an infra error, got {sm._state.infra_errors!r}"
        )



class TestAlsoCopyReachesTheMirror:
    """Tests that load a file by path need that file inside mutants/.

    mutmut mirrors only source_paths. A test doing
    Path(__file__).parents[1] / "scripts" / "x.py" then dies with
    FileNotFoundError under the mirror, and mutmut exits non-zero
    before measuring anything. also_copy is the mutmut-side answer;
    it has to survive the whole launch path to be of any use.
    """

    def test_launch_forwards_also_copy_to_the_child(self, tmp_path, monkeypatch):
        captured = {}

        def fake_popen(argv, **kw):
            captured["script"] = argv[2]
            return type("P", (), {"pid": 4242})()

        monkeypatch.setattr(mutation_module.subprocess, "Popen", fake_popen)

        mutation_module.launch_detached_mutation(
            diff_files=["src/pkg/mod.py"],
            baseline_cmd=["pytest", "-q"],
            cwd=tmp_path,
            result_path=tmp_path / ".code-forge" / "mutation-result.json",
            also_copy=["scripts/"],
        )

        assert "scripts/" in captured["script"], (
            "also_copy never reached the spawned child, so the mirror will "
            "lack the directory and path-loading tests will fail"
        )

    def test_gate_yaml_also_copy_reaches_the_launch(self, tmp_path, monkeypatch):
        captured = {}

        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr(
            StateMachine, "_execute_round", lambda self, round_index: None
        )
        monkeypatch.setattr(
            "code_forge.gate_check.load_gate_config",
            lambda p: {
                "test": {"command": ["pytest", "-q"], "also_copy": ["scripts/"]}
            },
        )

        def fake_launch(diff_files, baseline_cmd, cwd, result_path,
                        baseline_timeout=120, also_copy=None,
                        max_children=None, memory_limit_bytes=None):
            captured["also_copy"] = also_copy
            captured["resource_guards"] = (max_children, memory_limit_bytes)
            return 5150

        monkeypatch.setattr(
            "code_forge.machine.launch_detached_mutation", fake_launch
        )

        sm = _make_ci_sm(tmp_path)
        sm._run_ci()

        assert captured.get("also_copy") == ["scripts/"], (
            "test.also_copy in gate.yaml never reached launch_detached_mutation; "
            "the mirror will lack the directory and path-loading tests will die"
        )


def test_launch_leaves_no_child_to_reap(tmp_path):
    # The caller only keeps the pid; the result travels through a file.
    # If the launcher stays the direct parent, nobody ever calls wait()
    # and the finished run lingers as a zombie.
    result_path = tmp_path / ".code-forge" / "mutation-result.json"

    started = mutation_module.launch_detached_mutation(
        diff_files=["test.py"],
        baseline_cmd=[sys.executable, "-c", "pass"],
        cwd=tmp_path,
        result_path=result_path,
    )
    assert started is True

    # No child may be left behind: the middle process is waited for
    # inside the launcher, and the run itself belongs to init. Ask about
    # that one pid only -- waiting on -1 would steal another test's child.
    # The run writes a placeholder first and its own pid later; wait for
    # the pid, not merely for the file to exist.
    run_pid = None
    for _ in range(100):
        if result_path.exists():
            run_pid = json.loads(result_path.read_text()).get("pid")
            if run_pid is not None:
                break
        time.sleep(0.05)
    assert run_pid is not None, "the run never reported its pid"
    try:
        reaped, _ = os.waitpid(run_pid, os.WNOHANG)
    except ChildProcessError:
        return
    raise AssertionError(
        f"run {run_pid} is still our child (waitpid returned {reaped}), so it "
        "will sit as a zombie once the caller drops the handle"
    )


def test_the_run_is_reparented_away_from_us(tmp_path):
    # The run writes its own pid; if it were still our child, that pid
    # would report us as its parent and we would owe it a wait().
    result_path = tmp_path / "result.json"
    started = mutation_module.launch_detached_mutation(
        diff_files=["test.py"],
        baseline_cmd=[sys.executable, "-c", "import time; time.sleep(3)"],
        cwd=tmp_path,
        result_path=result_path,
    )
    assert started is True

    run_pid = None
    for _ in range(100):
        try:
            run_pid = json.loads(result_path.read_text()).get("pid")
        except (OSError, ValueError):
            run_pid = None
        if run_pid:
            break
        time.sleep(0.05)
    assert run_pid, "the run never reported its pid"

    if not Path("/proc").is_dir():  # pragma: no cover - non-Linux
        return

    ppid = int(
        Path(f"/proc/{run_pid}/stat").read_text().rsplit(")", 1)[1].split()[1]
    )
    assert ppid != os.getpid(), (
        "the run is still our direct child, so nothing reaps it once "
        "the caller drops the handle"
    )


def test_a_middle_process_that_hangs_is_cleaned_up(tmp_path, monkeypatch):
    # A middle process that never exits would otherwise be left behind
    # as a zombie, which is the very leak this launcher exists to avoid.
    events = []

    class Hanging:
        pid = 4242

        def wait(self, timeout=None):
            if timeout is not None:
                events.append("waited")
                raise mutation_module.subprocess.TimeoutExpired("x", timeout)
            events.append("reaped")
            return -9

        def kill(self):
            events.append("killed")

    monkeypatch.setattr(
        mutation_module.subprocess, "Popen", lambda *a, **k: Hanging(),
    )
    started = mutation_module.launch_detached_mutation(
        diff_files=["source.py"],
        baseline_cmd=["pytest"],
        cwd=tmp_path,
        result_path=tmp_path / "r.json",
    )
    assert started is False
    assert events == ["waited", "killed", "reaped"]
