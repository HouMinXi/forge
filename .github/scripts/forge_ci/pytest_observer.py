"""Report-only observer, loaded explicitly with ``-p forge_ci.pytest_observer``.

Never changes collection, selection, marks, fixtures, reports or exit status.
Only the fixed phase manifest and the full-phase native-subtest parent are retained.
Incomplete sessions and writer errors cannot pass the companion validator.
Use a fresh controller-owned output.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile

import pytest
from _pytest.subtests import SubtestContext, SubtestReport

from .outcomes import (
    MAX_EVENTS_BY_PHASE, MAX_EVENTS_BYTES, MAX_SUBTEST_REPORTS, MAX_XFAIL_REASON,
    NATIVE_REPORT, ORDINARY_REPORT, REQUIRED_BY_PHASE, REQUIRED_NODEIDS, SCHEMA_VERSION, SUBTEST_PARENT, SUBTEST_SEQUENCE,
)

EVENTS_ENV = "FORGE_CI_REQUIRED_EVENTS"
PHASE_ENV = "FORGE_CI_REQUIRED_PHASE"


class RequiredOutcomeObserver:
    def __init__(self, path: Path, phase: str):
        if type(phase) is not str or phase not in REQUIRED_BY_PHASE:
            raise ValueError("unknown required observer phase")
        self.phase = phase
        self.required = REQUIRED_BY_PHASE[phase]
        self.path = path
        self.fd: int | None = None
        self.document = {
            "schema_version": SCHEMA_VERSION,
            "phase": phase,
            "required_nodeids": list(self.required),
            "session": {
                "started": False,
                "collection_complete": False,
                "collected": {nodeid: 0 for nodeid in self.required},
                "finished": False,
                "exitstatus": None,
            },
            "subtest_accounting": {
                "pytest_version": pytest.__version__,
                "parent_nodeid": SUBTEST_PARENT,
                "collected": 0,
                "reports": [],
            } if phase == "full" else None,
            "events": [],
            "errors": [],
            "overflow": False,
        }

    def _error(self, message: str) -> None:
        if len(self.document["errors"]) < 8:
            self.document["errors"].append(message)
        else:
            self.document["overflow"] = True

    def _write(self) -> None:
        if self.fd is None:
            return
        data = (json.dumps(self.document, sort_keys=True, separators=(",", ":")) + "\n").encode()
        if len(data) > MAX_EVENTS_BYTES:
            self.document["overflow"] = True
            self.document["events"] = []
            if self.phase == "full":
                self.document["subtest_accounting"]["reports"] = []
            self._error("observer byte limit exceeded")
            data = (json.dumps(self.document, sort_keys=True) + "\n").encode()
        temporary = None
        try:
            # Publish only a completely written/fsynced snapshot. Until the final
            # rename succeeds, the prior record remains explicitly unfinished.
            fd, temporary = tempfile.mkstemp(prefix=".forge-outcomes-", dir=self.path.parent)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
        except OSError:
            self._error("observer write failed")
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    self._error("observer temporary cleanup failed")

    def pytest_sessionstart(self, session: pytest.Session) -> None:
        self.document["session"]["started"] = True
        try:
            existed = self.path.exists()
            self.fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            if not stat.S_ISREG(os.fstat(self.fd).st_mode):
                os.close(self.fd)
                self.fd = None
                self._error("observer destination is not a regular file")
                return
            # Invalidate stale evidence before any snapshots. A failed new run
            # must never leave a previous successful document in place.
            os.ftruncate(self.fd, 0)
            if existed:
                self._error("observer destination already existed; fresh evidence is required")
        except OSError:
            self._error("cannot open observer destination")
        self._write()

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        if self.document["session"]["collection_complete"]:
            self._error("duplicate collection completion")
        self.document["session"]["collection_complete"] = True
        counts = self.document["session"]["collected"]
        for item in session.items:
            if item.nodeid in REQUIRED_NODEIDS and item.nodeid not in self.required:
                self._error("required node collected in wrong phase")
            if self.phase == "full" and item.nodeid == SUBTEST_PARENT:
                self.document["subtest_accounting"]["collected"] += 1
            if item.nodeid in counts:
                counts[item.nodeid] += 1
        self._write()

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        # Check native reports before filtering: no other node or subclass can
        # silently introduce additional JUnit summary counts.
        native = isinstance(report, SubtestReport)
        if native and (self.phase != "full" or type(report) is not SubtestReport or report.nodeid != SUBTEST_PARENT):
            self._error("unexpected native subtest type or node")
            self._write()
            return
        if report.nodeid in REQUIRED_NODEIDS and report.nodeid not in self.required:
            self._error("required node reported in wrong phase")
            self._write()
            return
        parent = self.phase == "full" and report.nodeid == SUBTEST_PARENT
        if not parent and report.nodeid not in self.required:
            return
        target = (self.document["subtest_accounting"]["reports"] if parent
                  else self.document["events"])
        limit = MAX_SUBTEST_REPORTS if parent else MAX_EVENTS_BY_PHASE[self.phase]
        if len(target) >= limit:
            self.document["overflow"] = True
            self._error("observer event limit exceeded")
            self._write()
            return
        present = hasattr(report, "wasxfail")
        reason = getattr(report, "wasxfail", None)
        if present and (not isinstance(reason, str) or len(reason) > MAX_XFAIL_REASON):
            self._error("invalid or overlong wasxfail value")
            reason = "[invalid or overlong wasxfail value]"
        when = report.when
        outcome = report.outcome
        if when not in ("setup", "call", "teardown"):
            self._error("invalid report phase")
            when = "invalid"
        if outcome not in ("passed", "failed", "skipped"):
            self._error("invalid report outcome")
            outcome = "invalid"
        record = {
            "nodeid": report.nodeid,
            "when": when,
            "outcome": outcome,
            "wasxfail_present": present,
            "wasxfail": reason,
        }
        if parent:
            context = None
            kind = NATIVE_REPORT if native else ORDINARY_REPORT
            if not native and (type(report) is not pytest.TestReport or hasattr(report, "context")):
                self._error("unexpected ordinary subtest-parent report type or context")
                kind = "invalid"
            if native:
                raw = getattr(report, "context", None)
                kwargs = getattr(raw, "kwargs", None)
                if (type(raw) is not SubtestContext or raw.msg is not None or type(kwargs) is not dict
                        or set(kwargs) != {"writer"} or type(kwargs["writer"]) is not str
                        or kwargs["writer"] not in {"'file_utils'", "'gap_detector'"}):
                    self._error("invalid native subtest context")
                else:
                    context = {"msg": None, "kwargs": dict(kwargs)}
            record.update(kind=kind, context=context)
            signature = (kind, when, context)
            prior = [(item["kind"], item["when"], item["context"]) for item in target]
            valid = signature in SUBTEST_SEQUENCE and all(item in SUBTEST_SEQUENCE for item in prior)
            if valid:
                index = SUBTEST_SEQUENCE.index(signature)
                indices = [SUBTEST_SEQUENCE.index(item) for item in prior]
                valid = ((not indices and index == 0) or (indices and index > indices[-1]))
                valid = valid and (index != 2 or 1 in indices)
                if target:
                    valid = valid and (target[0]["outcome"] == "passed" or index == 4)
                if index == 3 and outcome == "passed":
                    valid = valid and indices == [0, 1, 2]
                if index == 4 and target and target[0]["outcome"] == "passed":
                    valid = valid and 3 in indices
            if not valid:
                self._error("unexpected subtest-parent report order or context")
        target.append(record)
        self._write()

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        self.document["session"]["finished"] = True
        self.document["session"]["exitstatus"] = int(exitstatus)
        self._write()
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                # A close failure cannot alter tests; fsync above is the write gate.
                pass
            self.fd = None


def pytest_configure(config: pytest.Config) -> None:
    path = os.environ.get(EVENTS_ENV)
    if path:
        phase = os.environ.get(PHASE_ENV)
        if phase not in REQUIRED_BY_PHASE:
            raise pytest.UsageError("missing or invalid fixed required observer phase")
        config.pluginmanager.register(RequiredOutcomeObserver(Path(path), phase), "forge-required-outcomes")
