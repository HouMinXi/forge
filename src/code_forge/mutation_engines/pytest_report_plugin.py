"""Forge pytest reporting plugin (spec corpus-oracle / Python adapter section).

Loaded into every pytest invocation the Python adapter supervises.
baseline and per-mutant alike.  Records collection, executed call-phase
results, setup/teardown/collection/internal errors and the final exit in
one bounded, versioned JSON event file per invocation.

Identity and destination come from the environment:

- ``FORGE_MUTATION_EVENTS_DIR``: directory the event file lands in
- ``FORGE_MUTATION_RUN_ID``: run identity stamped into the record
- ``MUTANT_UNDER_TEST``: set by mutmut for mutant runs; absent on baseline

Without both FORGE variables the plugin disarms itself completely.
"""

from __future__ import annotations

import json
import os
import re

PLUGIN_ID = "forge-mutation-report"
PLUGIN_VERSION = "1"
SCHEMA_VERSION = 1

_MAX_FAILED_NODES = 64
_MAX_MESSAGE = 512

_SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9_.-]+")


def _component(value: str) -> str:
    safe = _SAFE_COMPONENT.sub("_", value)
    return safe[:128] or "unknown"


class _Recorder:
    def __init__(self, events_dir: str, run_id: str, mutant_id: str | None) -> None:
        self.events_dir = events_dir
        self.run_id = run_id
        self.mutant_id = mutant_id
        self.collected = 0
        self.executed = 0
        self.failed_assertions = 0
        self.setup_errors = 0
        self.teardown_errors = 0
        self.collection_errors = 0
        self.internal_errors = 0
        self.skipped = 0
        self.failed_nodes: list[str] = []
        self.exit_status: int | None = None

    def event_path(self) -> str:
        name = "%s__%s.json" % (
            _component(self.run_id),
            _component(self.mutant_id) if self.mutant_id else "baseline",
        )
        return os.path.join(self.events_dir, name)

    def write_final(self) -> None:
        record = {
            "schema_version": SCHEMA_VERSION,
            "plugin": PLUGIN_ID,
            "plugin_version": PLUGIN_VERSION,
            "run_id": self.run_id,
            "mutant_id": self.mutant_id,
            "collected": self.collected,
            "executed": self.executed,
            "failed_assertions": self.failed_assertions,
            "setup_errors": self.setup_errors,
            "teardown_errors": self.teardown_errors,
            "collection_errors": self.collection_errors,
            "internal_errors": self.internal_errors,
            "skipped": self.skipped,
            "failed_nodes": self.failed_nodes[:_MAX_FAILED_NODES],
            "exit_status": self.exit_status,
            "final": True,
        }
        payload = json.dumps(record, sort_keys=True).encode("utf-8")
        path = self.event_path()
        tmp = path + ".tmp-%d" % os.getpid()
        with open(tmp, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.rename(tmp, path)


class _Hooks:
    """Object-form hooks so the recorder is reachable from every hook."""

    def __init__(self) -> None:
        self.recorder: _Recorder | None = None

    def pytest_configure(self, config) -> None:
        events_dir = os.environ.get("FORGE_MUTATION_EVENTS_DIR")
        run_id = os.environ.get("FORGE_MUTATION_RUN_ID")
        if not events_dir or not run_id:
            self.recorder = None
            return
        os.makedirs(events_dir, exist_ok=True)
        self.recorder = _Recorder(
            events_dir=events_dir,
            run_id=run_id,
            mutant_id=os.environ.get("MUTANT_UNDER_TEST") or None,
        )

    def pytest_collection_finish(self, session) -> None:
        if self.recorder is not None:
            self.recorder.collected = len(session.items)

    def pytest_collectreport(self, report) -> None:
        if self.recorder is not None and report.failed:
            self.recorder.collection_errors += 1

    def pytest_runtest_logreport(self, report) -> None:
        recorder = self.recorder
        if recorder is None:
            return
        if report.skipped:
            # skipif / pytest.mark.skip fire in setup, never the call phase
            recorder.skipped += 1
            return
        if report.when == "call":
            recorder.executed += 1
            if report.failed:
                recorder.failed_assertions += 1
                if len(recorder.failed_nodes) < _MAX_FAILED_NODES:
                    recorder.failed_nodes.append(report.nodeid[:_MAX_MESSAGE])
        elif report.when == "setup" and report.failed:
            recorder.setup_errors += 1
        elif report.when == "teardown" and report.failed:
            recorder.teardown_errors += 1

    def pytest_internalerror(self, excrepr) -> None:
        if self.recorder is not None:
            self.recorder.internal_errors += 1

    def pytest_sessionfinish(self, session, exitstatus) -> None:
        if self.recorder is None:
            return
        try:
            self.recorder.exit_status = int(exitstatus)
        except (TypeError, ValueError):
            self.recorder.exit_status = -1
        self.recorder.write_final()


_HOOKS = _Hooks()

# pytest discovers hooks from module-level functions; delegate each one.
def pytest_configure(config) -> None:  # noqa: F811
    _HOOKS.pytest_configure(config)


def pytest_collection_finish(session) -> None:
    _HOOKS.pytest_collection_finish(session)


def pytest_collectreport(report) -> None:
    _HOOKS.pytest_collectreport(report)


def pytest_runtest_logreport(report) -> None:
    _HOOKS.pytest_runtest_logreport(report)


def pytest_internalerror(excrepr) -> None:
    _HOOKS.pytest_internalerror(excrepr)


def pytest_sessionfinish(session, exitstatus) -> None:
    _HOOKS.pytest_sessionfinish(session, exitstatus)
