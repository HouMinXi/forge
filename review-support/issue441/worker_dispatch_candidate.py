"""One-shot worker lifetime candidate, not wired into production Forge."""
from contextlib import contextmanager
import os
import threading
from scoped_impact_candidate import HostInputs, make_scoped_impact_type

class ScopeUnavailable(RuntimeError):
    pass

class WorkerScope:
    """Create inside the worker, never on the coordinator for later sharing.

    The host supplies the execution identity; this scope does not issue one.
    The same worker thread must activate, consume and close this scope. A host
    callback must not retain it for another execution. No async/concurrent use.
    """
    def __init__(self):
        self._owner = (os.getpid(), threading.get_ident())
        self._inputs = None
        self._used = False

    def _check_owner(self):
        if self._owner != (os.getpid(), threading.get_ident()):
            raise ScopeUnavailable("scope belongs to another process/thread")

    def current_inputs(self):
        self._check_owner()
        if self._inputs is None:
            raise ScopeUnavailable("worker execution scope is not active")
        return self._inputs

    @contextmanager
    def activate(self, inputs):
        self._check_owner()
        if type(inputs) is not HostInputs:
            raise TypeError("exact HostInputs required")
        if self._used:
            raise ScopeUnavailable("worker scope is single-use")
        self._used = True
        self._inputs = inputs
        try:
            yield self
        finally:
            self._inputs = None

def run_primary_machine(*, impact_type, inputs, build_machine):
    """Candidate seam: build_machine receives the one scoped impact runner.

    The approved host must preserve all other advisory axes and source_root.
    This helper does not construct an actual StateMachine, freeze discovery,
    publish a diagnostic or provide a persistent locator.
    """
    scope = WorkerScope()
    scoped_type = make_scoped_impact_type(impact_type)
    with scope.activate(inputs):
        runner = scoped_type(current_inputs=scope.current_inputs)
        machine = build_machine(runner)
        return machine.run()
