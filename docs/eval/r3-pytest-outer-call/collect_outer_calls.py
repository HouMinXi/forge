"""Manual-only observation of pytest's ordinary outer call reports."""

import pytest


class OuterCallEvidence:
    """Use one instance per trusted, single-process invocation of pytest."""

    def __init__(self) -> None:
        self.observed_files: set[str] = set()
        self._files_by_nodeid: dict[str, set[str]] = {}

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        root = session.config.rootpath
        for item in session.items:
            if item.path.is_relative_to(root):
                self._files_by_nodeid.setdefault(item.nodeid, set()).add(
                    item.path.relative_to(root).as_posix()
                )

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        # Subtests call makereport with synthetic CallInfo(when="call") and
        # an ordinary intermediate TestReport. The concrete report type only
        # distinguishes it from an outer report after subtests wraps it.
        if (
            type(report) is not pytest.TestReport
            or report.when != "call"
            or report.outcome not in {"passed", "failed"}
        ):
            return
        paths = self._files_by_nodeid.get(report.nodeid)
        if paths is not None and len(paths) == 1:
            self.observed_files.update(paths)
