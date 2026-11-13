"""Worker lifetime controls with fixtures; no actual StateMachine/SQLite."""

import threading
import unittest
from pathlib import Path
from execution_binding import ExecutionContext, DiscoveryContext
from scoped_impact_candidate import HostInputs, make_scoped_impact_type
from worker_dispatch_candidate import WorkerScope, ScopeUnavailable, run_primary_machine


class FixtureImpact:
    def __init__(self):
        self.calls = 0
        self.infra_errors = []

    def run(self, diff_text, repo_root):
        self.calls += 1
        return []


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.inputs = HostInputs(ExecutionContext.new_host_execution("source"), DiscoveryContext(()))

    def test_inactive_before_and_after_execution(self):
        scope = WorkerScope()
        with self.assertRaises(ScopeUnavailable):
            scope.current_inputs()
        with scope.activate(self.inputs):
            self.assertIs(scope.current_inputs(), self.inputs)
        with self.assertRaises(ScopeUnavailable):
            scope.current_inputs()

    def test_nested_and_repeated_activation_rejected(self):
        scope = WorkerScope()
        with scope.activate(self.inputs):
            with self.assertRaises(ScopeUnavailable):
                with scope.activate(self.inputs):
                    pass
        with self.assertRaises(ScopeUnavailable):
            with scope.activate(self.inputs):
                pass

    def test_old_runner_rejected_after_scope_ends(self):
        scope = WorkerScope()
        runner = make_scoped_impact_type(FixtureImpact)(current_inputs=scope.current_inputs)
        with scope.activate(self.inputs):
            runner.run("A", Path("."))
        with self.assertRaises(ScopeUnavailable):
            runner.run("A", Path("."))
        self.assertEqual(runner.calls, 1)

    def test_main_exception_preserved_and_scope_closed(self):
        error = RuntimeError("original")
        scope = WorkerScope()
        with self.assertRaises(RuntimeError) as caught:
            with scope.activate(self.inputs):
                raise error
        self.assertIs(caught.exception, error)
        with self.assertRaises(ScopeUnavailable):
            scope.current_inputs()

    def test_foreign_thread_rejected(self):
        scope = WorkerScope()
        errors = []

        def consume():
            try:
                scope.current_inputs()
            except ScopeUnavailable as exc:
                errors.append(exc)

        with scope.activate(self.inputs):
            thread = threading.Thread(target=consume)
            thread.start()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)

    def test_machine_seam_returns_original_value_and_closes_runner(self):
        saved = []
        marker = object()

        def build(runner):
            saved.append(runner)

            class Machine:
                def run(self):
                    runner.run("A", Path("."))
                    return marker

            return Machine()

        result = run_primary_machine(impact_type=FixtureImpact, inputs=self.inputs, build_machine=build)
        self.assertIs(result, marker)
        with self.assertRaises(ScopeUnavailable):
            saved[0].run("A", Path("."))

    def test_construction_exception_propagates(self):
        saved = []
        error = RuntimeError("constructor")

        def build(runner):
            saved.append(runner)
            raise error

        with self.assertRaises(RuntimeError) as caught:
            run_primary_machine(impact_type=FixtureImpact, inputs=self.inputs, build_machine=build)
        self.assertIs(caught.exception, error)
        with self.assertRaises(ScopeUnavailable):
            saved[0].run("A", Path("."))

    def test_keyboard_interrupt_propagates_and_closes_scope(self):
        scope = WorkerScope()
        error = KeyboardInterrupt("cancel")
        with self.assertRaises(KeyboardInterrupt) as caught:
            with scope.activate(self.inputs):
                raise error
        self.assertIs(caught.exception, error)
        with self.assertRaises(ScopeUnavailable):
            scope.current_inputs()

    def test_system_exit_propagates_and_closes_scope(self):
        scope = WorkerScope()
        error = SystemExit("exit")
        with self.assertRaises(SystemExit) as caught:
            with scope.activate(self.inputs):
                raise error
        self.assertIs(caught.exception, error)
        with self.assertRaises(ScopeUnavailable):
            scope.current_inputs()


if __name__ == "__main__":
    unittest.main(verbosity=2)
