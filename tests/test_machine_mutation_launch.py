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
from contextlib import contextmanager
from pathlib import Path

import pytest

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

        monkeypatch.setattr(mutation_module.subprocess, "Popen", _RecordingPopen)
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr(
            "code_forge.gate_check.load_gate_config",
            lambda p: {"test": {"command": ["pytest", "-q"]}},
        )
        monkeypatch.setattr(StateMachine, "_execute_round", lambda self, round_index: None)

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
            "writing its result and the gate silently degrades to SKIPPED.".format(
                captured.get("start_new_session")
            )
        )

    def test_unusable_gate_config_is_recorded_not_swallowed(self, tmp_path, monkeypatch):
        """A gate.yaml without test.command must not skip mutation silently.

        This is the shape a worktree actually carries: .code-forge/ is
        gitignored per directory, so a worktree gate.yaml can hold outlet
        and backends while missing the test section entirely.
        """
        gate_dir = tmp_path / ".code-forge"
        gate_dir.mkdir()
        (gate_dir / "gate.yaml").write_text(
            "outlet: subprocess\nbackends:\n  some-backend:\n    type: api\n",
            encoding="utf-8",
        )

        captured = []

        class _RecordingPopen:
            def __init__(self, args, **kw):
                captured.append(args)
                self.pid = 99999

        monkeypatch.setattr(mutation_module.subprocess, "Popen", _RecordingPopen)
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr(StateMachine, "_execute_round", lambda self, round_index: None)

        sm = _make_ci_sm(tmp_path)
        sm._run_ci()

        # The mutation launcher spawns `sys.executable -c <script>`; a CI run
        # may also legitimately spawn other subprocesses (e.g. resolve_ledger_
        # root's `git rev-parse` via subprocess.run, which internally uses
        # Popen). Assert no MUTATION process launched, not "no Popen at all".
        mutation_launches = [
            a
            for a in captured
            if isinstance(a, (list, tuple)) and len(a) >= 2 and a[0] == sys.executable and a[1] == "-c"
        ]
        assert not mutation_launches, (
            "mutation launched despite an unusable gate config; this test "
            "no longer exercises the skip path it claims to"
        )
        assert any("test.command" in e for e in sm._state.infra_errors), (
            "mutation was skipped for an unusable gate.yaml and left no "
            f"trace: infra_errors={sm._state.infra_errors!r}. The verdict then reads identically to "
            "one where the mutation gate actually ran."
        )

    def test_pid_none_in_result_file_defers_not_launches(self, tmp_path, monkeypatch):
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
            json.dumps(
                {
                    "pid": None,
                    "started_at": _time.time(),
                    "status": "running",
                    "survivors": [],
                }
            ),
            encoding="utf-8",
        )

        launched = []

        class _RecordingPopen:
            def __init__(self, args, **kw):
                launched.append(True)
                self.pid = 88888

        monkeypatch.setattr(mutation_module.subprocess, "Popen", _RecordingPopen)
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr(
            "code_forge.gate_check.load_gate_config",
            lambda p: {"test": {"command": ["pytest", "-q"]}},
        )
        monkeypatch.setattr(StateMachine, "_execute_round", lambda self, round_index: None)

        sm = _make_ci_sm(tmp_path)
        verdict = sm._run_ci()

        assert not launched, (
            "a duplicate mutation was launched even though "
            "mutation-result.json already had status=running with pid=None"
        )
        assert verdict == Verdict.PENDING, f"expected PENDING to defer to next round, got {verdict!r}"

    def test_missing_gate_yaml_records_specific_error(self, tmp_path, monkeypatch):
        """When gate.yaml does not exist at all, the infra_error must
        say 'gate.yaml not found', not the generic 'test.command not
        configured' message.

        Bug-injection: change the FileNotFoundError message back to the
        generic one -> this test FAILS because the message no longer
        distinguishes "file missing" from "file exists but malformed".
        """
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr(StateMachine, "_execute_round", lambda self, round_index: None)

        # No .code-forge directory at all -> FileNotFoundError on load_gate_config
        sm = _make_ci_sm(tmp_path)
        sm._run_ci()

        assert any("gate.yaml not found" in e for e in sm._state.infra_errors), (
            "missing gate.yaml should produce a specific 'not found' error, "
            f"got infra_errors={sm._state.infra_errors!r}"
        )

    def test_launch_creates_parent_directory_for_result_file(self, tmp_path, monkeypatch):
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
            mutation_module.subprocess,
            "Popen",
            lambda *a, **kw: type(
                "P",
                (),
                {
                    "pid": 77777,
                    "wait": lambda self, timeout=None: 0,
                },
            )(),
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
        assert result_path.exists(), "result_path does not exist after launch; mkdir is missing"

    def test_stale_pid_none_relaunches_after_timeout(self, tmp_path, monkeypatch):
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
            json.dumps(
                {
                    "pid": None,
                    "started_at": _time.time() - 200,
                    "status": "running",
                    "survivors": [],
                }
            ),
            encoding="utf-8",
        )

        launched = []

        class _RecordingPopen:
            def __init__(self, args, **kw):
                launched.append(True)
                self.pid = 88888

        monkeypatch.setattr(mutation_module.subprocess, "Popen", _RecordingPopen)
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr(
            "code_forge.gate_check.load_gate_config",
            lambda p: {"test": {"command": ["pytest", "-q"]}},
        )
        monkeypatch.setattr(StateMachine, "_execute_round", lambda self, round_index: None)

        sm = _make_ci_sm(tmp_path)
        sm._run_ci()

        assert launched, (
            "stale mutation-result.json (pid=None, started_at 200s ago) "
            "should have been unlinked and a new mutation launched, but "
            "no Popen was called"
        )

    def test_launch_failure_records_infra_error(self, tmp_path, monkeypatch):
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

        monkeypatch.setattr(mutation_module.subprocess, "Popen", _failing_popen)
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr(
            "code_forge.gate_check.load_gate_config",
            lambda p: {"test": {"command": ["pytest", "-q"]}},
        )
        monkeypatch.setattr(StateMachine, "_execute_round", lambda self, round_index: None)

        sm = _make_ci_sm(tmp_path)
        sm._run_ci()

        assert any("failed to start" in e for e in sm._state.infra_errors), (
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
        monkeypatch.setattr(StateMachine, "_execute_round", lambda self, round_index: None)
        monkeypatch.setattr(
            "code_forge.gate_check.load_gate_config",
            lambda p: {"test": {"command": ["pytest", "-q"], "also_copy": ["scripts/"]}},
        )

        def fake_launch(
            diff_files,
            baseline_cmd,
            cwd,
            result_path,
            baseline_timeout=120,
            also_copy=None,
            max_children=None,
            memory_limit_bytes=None,
            mutation_skip_globs=None,
            mutation_include_globs=None,
        ):
            captured["also_copy"] = also_copy
            captured["resource_guards"] = (max_children, memory_limit_bytes)
            return 5150

        monkeypatch.setattr("code_forge.machine.launch_detached_mutation", fake_launch)

        sm = _make_ci_sm(tmp_path)
        sm._run_ci()

        assert captured.get("also_copy") == ["scripts/"], (
            "test.also_copy in gate.yaml never reached launch_detached_mutation; "
            "the mirror will lack the directory and path-loading tests will die"
        )


def _run_identity(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
    except (FileNotFoundError, ProcessLookupError):
        return None
    return fields[19], fields[0]


@pytest.mark.parametrize("error", [FileNotFoundError(2, "gone"), ProcessLookupError(3, "gone")])
@pytest.mark.parametrize("reader", ["_run_identity", "_parent_of"])
def test_detached_proc_read_disappearance_is_non_live(monkeypatch, error, reader):
    paths = []

    def disappeared(path):
        paths.append(path)
        raise error

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "read_text", disappeared)
        assert globals()[reader](42) is None
    assert paths == [Path("/proc/42/stat")]


@pytest.mark.parametrize("denied", [False, True])
@pytest.mark.parametrize("reader", ["_run_identity", "_parent_of"])
def test_detached_proc_read_rejects_unavailable_or_malformed(monkeypatch, denied, reader):
    error = PermissionError("stat denied")

    def unreadable(path):
        if denied:
            raise error
        return "malformed stat"

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "read_text", unreadable)
        with pytest.raises(PermissionError if denied else IndexError) as caught:
            globals()[reader](42)
    if denied:
        assert caught.value is error


class _DetachedRun:
    def __init__(self, result_path):
        self.result_path = result_path
        self.identity = None
        self.reported_pid = None
        self.completed_without_start = False

    def observe(self):
        try:
            data = json.loads(self.result_path.read_text())
        except (OSError, ValueError):
            return None
        pid = data.get("pid")
        if type(pid) is not int or pid <= 0:
            return None
        if self.reported_pid is None:
            self.reported_pid = pid
        elif pid != self.reported_pid:
            raise AssertionError("Detached run changed its reported PID")
        if self.identity is None and not self.completed_without_start:
            current = _run_identity(pid)
            if current is not None:
                self.identity = pid, current[0]
            elif data.get("status") in ("done", "error"):
                # The owned invocation completed before a start could be observed.
                self.completed_without_start = True
        return data

    def wait_for_pid(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.observe()
            if self.identity is not None:
                return self.identity[0]
            if self.completed_without_start:
                return self.reported_pid
            time.sleep(0.005)
        raise AssertionError("Detached run never supplied an observable PID/start identity")

    def finish(self):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            data = self.observe()
            if self.completed_without_start and data is not None:
                if data.get("pid") == self.reported_pid and data.get("status") in ("done", "error"):
                    return
            if self.identity is not None and data is not None:
                pid, start = self.identity
                current = _run_identity(pid)
                terminal = data.get("pid") == pid and data.get("status") in ("done", "error")
                non_live = current is None or current[0] != start or current[1] == "Z"
                if terminal and non_live:
                    return
            time.sleep(0.005)
        raise AssertionError("Detached run did not reach terminal/non-live state with owned identity")


@contextmanager
def _detached_run(result_path):
    if not Path("/proc/self/stat").is_file() or not hasattr(os, "fork"):
        pytest.skip("Detached parentage checks require Linux /proc and fork")
    assert not result_path.exists(), "Detached invocation needs a fresh owned result path"
    run = _DetachedRun(result_path)
    primary = None
    try:
        yield run
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            run.finish()
        except BaseException as error:
            if primary is None:
                raise
            primary.add_note(f"Detached run cleanup failed: {error!r}")


def test_launch_leaves_no_child_to_reap(tmp_path):
    # The caller only keeps the pid; the result travels through a file.
    # If the launcher stays the direct parent, nobody ever calls wait()
    # and the finished run lingers as a zombie.
    result_path = tmp_path / ".code-forge" / "mutation-result.json"

    with _detached_run(result_path) as run:
        started = mutation_module.launch_detached_mutation(
            diff_files=["README.md"],
            baseline_cmd=[sys.executable, "-c", "pass"],
            cwd=tmp_path,
            result_path=result_path,
        )
        assert started is True

        # Ask about that one pid only -- waiting on -1 would steal
        # another test's child. Completion is awaited on context exit.
        run_pid = run.wait_for_pid()
        try:
            reaped, _ = os.waitpid(run_pid, os.WNOHANG)
        except ChildProcessError:
            return
        raise AssertionError(
            f"run {run_pid} is still our child (waitpid returned {reaped}), so it "
            "will sit as a zombie once the caller drops the handle"
        )


def _parent_of(pid: int) -> int | None:
    """Parent pid from /proc, or None once that pid is already gone.

    mutmut's stats pass runs pytest with -x. A run that wrote its pid and
    exited before we opened /proc used to raise FileNotFoundError and abort
    the whole collection. A missing proc entry means the process is not our
    child anymore.
    """
    stat = Path(f"/proc/{pid}/stat")
    try:
        text = stat.read_text()
    except (FileNotFoundError, ProcessLookupError):
        return None
    return int(text.rsplit(")", 1)[1].split()[1])


def test_a_finished_run_is_not_our_child():
    assert _parent_of(1 << 30) is None


def test_the_run_is_reparented_away_from_us(tmp_path):
    # The run writes its own pid; if it were still our child, that pid
    # would report us as its parent and we would owe it a wait().
    result_path = tmp_path / "result.json"
    with _detached_run(result_path) as run:
        started = mutation_module.launch_detached_mutation(
            diff_files=["test.py"],
            baseline_cmd=[sys.executable, "-c", "import time; time.sleep(3); raise SystemExit(1)"],
            cwd=tmp_path,
            result_path=result_path,
        )
        assert started is True
        run_pid = run.wait_for_pid()
        ppid = _parent_of(run_pid)
        if ppid is None:
            return
        assert ppid != os.getpid(), (
            "the run is still our direct child, so nothing reaps it once the caller drops the handle"
        )


@pytest.fixture
def observed_runs(monkeypatch):
    runs = []
    initialize = _DetachedRun.__init__

    def record(self, result_path):
        initialize(self, result_path)
        runs.append(self)

    monkeypatch.setattr(_DetachedRun, "__init__", record)
    return runs


@pytest.mark.parametrize(
    "reused_pid,observe_start",
    [(False, True), (True, True), (True, False)],
    ids=["ordinary", "reused-observed", "reused-fast"],
)
def test_fast_parentage_fixture_waits_for_a_live_run(
    tmp_path, monkeypatch, observed_runs, reused_pid, observe_start
):
    launch = mutation_module.launch_detached_mutation

    def slow_launch(**kwargs):
        kwargs["diff_files"] = ["test.py"]
        kwargs["baseline_cmd"] = [
            sys.executable,
            "-c",
            "import time; time.sleep(3); raise SystemExit(1)",
        ]
        return launch(**kwargs)

    monkeypatch.setattr(mutation_module, "launch_detached_mutation", slow_launch)
    if not observe_start:
        monkeypatch.setattr(sys.modules[__name__], "_run_identity", lambda pid: None)
    test_launch_leaves_no_child_to_reap(tmp_path)
    assert len(observed_runs) == 1
    run = observed_runs[0]
    if not observe_start:
        assert run.identity is None and run.completed_without_start
    if reused_pid:
        monkeypatch.setattr(sys.modules[__name__], "_run_identity", lambda pid: ("reused-start", "S"))
    data = json.loads((tmp_path / ".code-forge" / "mutation-result.json").read_text())
    assert data["status"] in ("done", "error")
    current = _run_identity(data["pid"])
    if run.identity is None:
        assert run.completed_without_start
    else:
        assert current is None or current[0] != run.identity[1] or current[1] == "Z"


@pytest.mark.parametrize("failure_point", ["started", "before_pid", "after_pid"])
@pytest.mark.parametrize(
    "reused_pid,observe_start",
    [(False, True), (True, True), (True, False)],
    ids=["ordinary", "reused-observed", "reused-fast"],
)
def test_parentage_failure_still_waits_for_its_run(
    tmp_path, monkeypatch, failure_point, observed_runs, reused_pid, observe_start
):
    marker = AssertionError("original parentage assertion")
    events = []

    def fail(*args):
        raise marker

    if failure_point == "started":
        launch = mutation_module.launch_detached_mutation

        def started_false(**kwargs):
            returned = launch(**kwargs)
            events.append(("launch_returned", returned))
            assert returned is True
            events.append(("returning_false", False))
            return False

        monkeypatch.setattr(mutation_module, "launch_detached_mutation", started_false)
    elif failure_point == "before_pid":
        monkeypatch.setattr(_DetachedRun, "wait_for_pid", fail)
    else:
        monkeypatch.setattr(sys.modules[__name__], "_parent_of", fail)
    if not observe_start:
        monkeypatch.setattr(sys.modules[__name__], "_run_identity", lambda pid: None)
    with pytest.raises(AssertionError) as caught:
        test_the_run_is_reparented_away_from_us(tmp_path)
    if failure_point != "started":
        assert caught.value is marker
    else:
        assert events == [("launch_returned", True), ("returning_false", False)]
    assert not getattr(caught.value, "__notes__", [])
    assert len(observed_runs) == 1
    run = observed_runs[0]
    if not observe_start:
        assert run.identity is None and run.completed_without_start
    if reused_pid:
        monkeypatch.setattr(sys.modules[__name__], "_run_identity", lambda pid: ("reused-start", "S"))
    data = json.loads((tmp_path / "result.json").read_text())
    assert data["status"] in ("done", "error")
    current = _run_identity(data["pid"])
    if run.identity is None:
        assert run.completed_without_start
    else:
        assert current is None or current[0] != run.identity[1] or current[1] == "Z"


def test_detached_cleanup_keeps_the_primary_assertion(tmp_path, monkeypatch):
    def fail(self):
        raise AssertionError("cleanup observation failed")

    monkeypatch.setattr(_DetachedRun, "finish", fail)
    primary = AssertionError("parentage assertion failed")
    with pytest.raises(AssertionError) as caught:
        with _detached_run(tmp_path / "unused-result.json"):
            raise primary
    assert caught.value is primary
    assert caught.value.__notes__ == [
        "Detached run cleanup failed: AssertionError('cleanup observation failed')"
    ]
    with pytest.raises(AssertionError, match="cleanup observation failed"):
        with _detached_run(tmp_path / "unused-result.json"):
            pass


def test_detached_completion_requires_terminal_and_non_live(tmp_path, monkeypatch):
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"pid": 42, "status": "running"}))
    run = _DetachedRun(path)
    run.identity = (42, "owned-start")
    ticks = iter([0, 0, 11])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(time, "sleep", lambda delay: None)
    monkeypatch.setattr(sys.modules[__name__], "_run_identity", lambda pid: None)
    with pytest.raises(AssertionError, match="terminal/non-live"):
        run.finish()
    path.write_text(json.dumps({"pid": 42, "status": "done"}))
    ticks = iter([0, 0, 11])
    monkeypatch.setattr(sys.modules[__name__], "_run_identity", lambda pid: ("owned-start", "S"))
    with pytest.raises(AssertionError, match="terminal/non-live"):
        run.finish()
    ticks = iter([0, 0])
    monkeypatch.setattr(sys.modules[__name__], "_run_identity", lambda pid: ("owned-start", "Z"))
    run.finish()
    ticks = iter([0, 0])
    monkeypatch.setattr(sys.modules[__name__], "_run_identity", lambda pid: ("reused-start", "S"))
    run.finish()


def test_detached_observation_retains_first_identity(tmp_path, monkeypatch):
    path = tmp_path / "result.json"
    run = _DetachedRun(path)
    assert run.observe() is None
    path.write_text("{")
    assert run.observe() is None
    path.write_text(json.dumps({"pid": 42, "status": "running"}))
    monkeypatch.setattr(sys.modules[__name__], "_run_identity", lambda pid: ("first-start", "S"))
    assert run.wait_for_pid() == 42
    monkeypatch.setattr(sys.modules[__name__], "_run_identity", lambda pid: ("reused-start", "S"))
    run.observe()
    assert run.identity == (42, "first-start")
    path.write_text(json.dumps({"pid": 43, "status": "done"}))
    with pytest.raises(AssertionError, match="changed its reported PID"):
        run.observe()


def test_detached_missing_identity_is_an_explicit_failure(tmp_path, monkeypatch):
    run = _DetachedRun(tmp_path / "missing-result.json")
    ticks = iter([0, 0, 6])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(time, "sleep", lambda delay: None)
    with pytest.raises(AssertionError, match="observable PID/start identity"):
        run.wait_for_pid()
    ticks = iter([0, 0, 11])
    with pytest.raises(AssertionError, match="owned identity"):
        run.finish()
    assert _run_identity(1 << 30) is None


@pytest.mark.parametrize("status", ["done", "error"])
def test_detached_fast_absent_completion_has_no_invented_start(tmp_path, status):
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"pid": 1 << 30, "status": status}))
    run = _DetachedRun(path)
    assert run.wait_for_pid() == 1 << 30
    assert run.reported_pid == 1 << 30
    assert run.identity is None and run.completed_without_start
    run.finish()


@pytest.mark.parametrize("pid", [True, 0, -1, "42"])
def test_detached_fast_completion_rejects_invalid_pid(tmp_path, pid):
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"pid": pid, "status": "done"}))
    run = _DetachedRun(path)
    run.observe()
    assert run.identity is None and not run.completed_without_start
    assert run.reported_pid is None


@pytest.mark.parametrize("pid,terminal_pid", [(42, 42.0), (1, True)])
@pytest.mark.parametrize("fast_complete", [False, True])
def test_detached_terminal_rejects_changed_pid_type(
    tmp_path, monkeypatch, pid, terminal_pid, fast_complete
):
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"pid": pid, "status": "done"}))
    monkeypatch.setattr(
        sys.modules[__name__],
        "_run_identity",
        lambda value: None if fast_complete else ("owned-start", "Z"),
    )
    run = _DetachedRun(path)
    assert run.wait_for_pid() == pid
    assert run.completed_without_start is fast_complete
    path.write_text(json.dumps({"pid": terminal_pid, "status": "done"}))
    ticks = iter([0, 0, 11])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(time, "sleep", lambda delay: None)
    with pytest.raises(AssertionError, match="terminal/non-live"):
        run.finish()
    assert run.reported_pid == pid


@pytest.mark.parametrize("error", [PermissionError("stat denied"), ValueError("malformed stat")])
def test_detached_fast_completion_requires_real_absence(tmp_path, monkeypatch, error):
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"pid": os.getpid(), "status": "done"}))
    monkeypatch.setattr(sys.modules[__name__], "_run_identity", lambda pid: ("owned-start", "S"))
    run = _DetachedRun(path)
    assert run.wait_for_pid() == os.getpid()
    assert run.identity is not None and not run.completed_without_start

    def denied(pid):
        raise error

    monkeypatch.setattr(sys.modules[__name__], "_run_identity", denied)
    run = _DetachedRun(path)
    with pytest.raises(type(error), match=str(error)):
        run.observe()
    assert not run.completed_without_start
    path.write_text(json.dumps({"pid": 1 << 30, "status": "running"}))
    monkeypatch.setattr(sys.modules[__name__], "_run_identity", lambda pid: None)
    run = _DetachedRun(path)
    run.observe()
    assert not run.completed_without_start


def test_detached_absence_control_is_platform_independent(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "_run_identity", lambda pid: None)
    test_detached_fast_completion_requires_real_absence(
        tmp_path, monkeypatch, PermissionError("stat denied")
    )


def test_detached_context_rejects_a_preexisting_result(tmp_path, monkeypatch):
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"pid": 1 << 30, "status": "done"}))
    monkeypatch.setattr(_DetachedRun, "finish", lambda self: None)
    with pytest.raises(AssertionError, match="fresh owned result path"):
        with _detached_run(path):
            pass


@pytest.mark.parametrize("already_absent", [False, True])
def test_detached_platform_check_precedes_launch(tmp_path, monkeypatch, already_absent):
    if already_absent:
        monkeypatch.delattr(os, "fork", raising=False)
    monkeypatch.delattr(os, "fork", raising=False)
    with pytest.raises(pytest.skip.Exception, match="Linux /proc and fork"):
        with _detached_run(tmp_path / "unused-result.json"):
            raise AssertionError("Unsupported fixture must not reach launch")


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
        mutation_module.subprocess,
        "Popen",
        lambda *a, **k: Hanging(),
    )
    started = mutation_module.launch_detached_mutation(
        diff_files=["source.py"],
        baseline_cmd=["pytest"],
        cwd=tmp_path,
        result_path=tmp_path / "r.json",
    )
    assert started is False
    assert events == ["waited", "killed", "reaped"]


def test_a_done_run_without_a_baseline_holds():
    """No survivors is not a pass when the run never proved a baseline."""
    from code_forge.machine import mutation_result_verdict

    assert mutation_result_verdict({"status": "done", "survivors": []}) is Verdict.UNRELIABLE
    assert mutation_result_verdict({"status": "done", "survivors": ["m1"]}) is Verdict.FAIL
    assert mutation_result_verdict({"status": "done", "survivors": [], "baseline_passed": True}) is None
