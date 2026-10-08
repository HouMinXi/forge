"""Report-only observer, loaded explicitly with ``-p forge_ci.pytest_observer``.

Never changes collection, selection, marks, fixtures, reports or exit status.
Only the exact eleven required nodes are retained. Incomplete sessions and writer
errors cannot pass the companion validator. Use a fresh controller-owned output.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile

import pytest

from .outcomes import MAX_EVENTS, MAX_EVENTS_BYTES, MAX_XFAIL_REASON, REQUIRED_NODEIDS, SCHEMA_VERSION

EVENTS_ENV = "FORGE_CI_REQUIRED_EVENTS"


class RequiredOutcomeObserver:
    def __init__(self, path: Path):
        self.path = path
        self.fd: int | None = None
        self.document = {
            "schema_version": SCHEMA_VERSION,
            "required_nodeids": list(REQUIRED_NODEIDS),
            "session": {
                "started": False,
                "collection_complete": False,
                "collected": {nodeid: 0 for nodeid in REQUIRED_NODEIDS},
                "finished": False,
                "exitstatus": None,
            },
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
            if item.nodeid in counts:
                counts[item.nodeid] += 1
        self._write()

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.nodeid not in REQUIRED_NODEIDS:
            return
        if len(self.document["events"]) >= MAX_EVENTS:
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
        if when not in {"setup", "call", "teardown"}:
            self._error("invalid report phase")
            when = "invalid"
        if outcome not in {"passed", "failed", "skipped"}:
            self._error("invalid report outcome")
            outcome = "invalid"
        self.document["events"].append({
            "nodeid": report.nodeid,
            "when": when,
            "outcome": outcome,
            "wasxfail_present": present,
            "wasxfail": reason,
        })
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
        config.pluginmanager.register(RequiredOutcomeObserver(Path(path)), "forge-required-outcomes")
