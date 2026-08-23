# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for CrossRepoImpactRunner advisory axis.

Uses tmp_path sqlite fixtures built by hand -- real sqlite files, not mocks --
so the sqlite3 query path is actually exercised. Fixture schema matches the
live code-review-graph schema:
  nodes(id, kind, name, qualified_name, file_path, line_start, line_end)
  edges(kind, source_qualified, target_qualified)

Seam: CRG_REGISTRY_PATH env var points at a fixture registry.json under
tmp_path, so tests never touch ~/.code-review-graph/.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("code_review_graph")

from code_forge.advisory import AdvisoryFinding


# ---------------------------------------------------------------------------
# Helpers: build real sqlite graph.db fixtures
# ---------------------------------------------------------------------------

_NODES_DDL = (
    "CREATE TABLE nodes ("
    "  id INTEGER PRIMARY KEY,"
    "  kind TEXT,"
    "  name TEXT,"
    "  qualified_name TEXT,"
    "  file_path TEXT,"
    "  line_start INTEGER,"
    "  line_end INTEGER"
    ")"
)
_EDGES_DDL = (
    "CREATE TABLE edges ("
    "  kind TEXT,"
    "  source_qualified TEXT,"
    "  target_qualified TEXT"
    ")"
)


def _make_db(path: Path, nodes: list[tuple], edges: list[tuple]) -> Path:
    """Build a real sqlite graph.db at *path* with the given rows.

    nodes: list of (id, kind, name, qualified_name, file_path, line_start,
           line_end)
    edges: list of (kind, source_qualified, target_qualified)
    """
    conn = sqlite3.connect(str(path))
    conn.execute(_NODES_DDL)
    conn.execute(_EDGES_DDL)
    conn.executemany(
        "INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?)", nodes,
    )
    conn.executemany(
        "INSERT INTO edges VALUES (?, ?, ?)", edges,
    )
    conn.commit()
    conn.close()
    return path


def _make_registry(
    registry_path: Path,
    repos: list[dict],
) -> Path:
    """Write a fixture registry.json."""
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps({"repos": repos}, indent=2) + "\n",
        encoding="utf-8",
    )
    return registry_path


def _sample_diff(file_path: str = "src/lib/handler.py") -> str:
    """Return a minimal unified diff touching *file_path*."""
    return (
        "diff --git a/{f} b/{f}\n"
        "--- a/{f}\n"
        "+++ b/{f}\n"
        "@@ -10,3 +10,4 @@\n"
        " existing line\n"
        "+new line\n"
    ).format(f=file_path)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def primary_repo(tmp_path: Path) -> Path:
    """Create a primary repo dir with a graph.db containing named nodes."""
    repo = tmp_path / "primary"
    repo.mkdir()
    crg_dir = repo / ".code-review-graph"
    crg_dir.mkdir()
    _make_db(
        crg_dir / "graph.db",
        nodes=[
            (1, "function", "process_request",
             "handler::process_request",
             "src/lib/handler.py", 10, 20),
            (2, "function", "helper_fn",
             "handler::helper_fn",
             "src/lib/handler.py", 25, 30),
            # unnamed node -- must be skipped
            (3, "module", "module-level",
             "handler::module-level",
             "src/lib/handler.py", 1, 50),
            # unnamed "lines ..." node -- must be skipped
            (4, "block", "lines 5-9",
             "handler::lines 5-9",
             "src/lib/handler.py", 5, 9),
        ],
        edges=[],  # primary has no internal callers for this test
    )
    return repo


@pytest.fixture()
def sibling_repo(tmp_path: Path) -> Path:
    """Create a sibling repo with a graph.db that CALLS primary symbols."""
    repo = tmp_path / "sibling"
    repo.mkdir()
    crg_dir = repo / ".code-review-graph"
    crg_dir.mkdir()
    _make_db(
        crg_dir / "graph.db",
        nodes=[
            (1, "function", "call_handler",
             "consumer::call_handler",
             "lib/consumer.py", 15, 25),
        ],
        edges=[
            # sibling calls process_request from primary
            ("CALLS", "consumer::call_handler", "process_request"),
            # IMPORTS_FROM for disambiguation
            ("IMPORTS_FROM", "consumer::call_handler", "handler"),
        ],
    )
    return repo


@pytest.fixture()
def registry_env(
    tmp_path: Path,
    primary_repo: Path,
    sibling_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Set up CRG_REGISTRY_PATH with primary + sibling registered."""
    reg_path = tmp_path / "registry" / "registry.json"
    _make_registry(reg_path, [
        {"path": str(primary_repo.resolve()), "alias": "primary"},
        {"path": str(sibling_repo.resolve()), "alias": "sibling"},
    ])
    monkeypatch.setenv("CRG_REGISTRY_PATH", str(reg_path))
    return reg_path


# ---------------------------------------------------------------------------
# (a) is_advisory + empty diff
# ---------------------------------------------------------------------------

class TestAdvisoryContract:
    """Verify the runner satisfies AxisRunner Protocol basics."""

    def test_is_advisory_true(self) -> None:
        from code_forge.cross_repo_impact import CrossRepoImpactRunner
        runner = CrossRepoImpactRunner()
        assert runner.is_advisory is True

    def test_empty_diff_returns_empty(
        self, primary_repo: Path, registry_env: Path,
    ) -> None:
        from code_forge.cross_repo_impact import CrossRepoImpactRunner
        runner = CrossRepoImpactRunner()
        result = runner.run("", primary_repo)
        assert result == []
        assert runner.infra_errors == []

    def test_whitespace_diff_returns_empty(
        self, primary_repo: Path, registry_env: Path,
    ) -> None:
        from code_forge.cross_repo_impact import CrossRepoImpactRunner
        runner = CrossRepoImpactRunner()
        result = runner.run("   \n  \n  ", primary_repo)
        assert result == []
        assert runner.infra_errors == []


# ---------------------------------------------------------------------------
# (b) resolve_changed_symbols
# ---------------------------------------------------------------------------

class TestResolveChangedSymbols:
    """Verify symbol resolution from diff + primary graph.db."""

    def test_named_nodes_resolved(self, primary_repo: Path) -> None:
        from code_forge.cross_repo_impact import resolve_changed_symbols
        primary_db = str(
            primary_repo / ".code-review-graph" / "graph.db",
        )
        diff = _sample_diff("src/lib/handler.py")
        symbols = resolve_changed_symbols(diff, primary_db)
        names = [s["name"] for s in symbols]
        assert "process_request" in names
        assert "helper_fn" in names
        # file_path populated
        for s in symbols:
            assert s["file_path"]

    def test_unnamed_nodes_skipped(self, primary_repo: Path) -> None:
        from code_forge.cross_repo_impact import resolve_changed_symbols
        primary_db = str(
            primary_repo / ".code-review-graph" / "graph.db",
        )
        diff = _sample_diff("src/lib/handler.py")
        symbols = resolve_changed_symbols(diff, primary_db)
        names = [s["name"] for s in symbols]
        assert "module-level" not in names
        assert "lines 5-9" not in names


# ---------------------------------------------------------------------------
# (c) find_cross_repo_callers
# ---------------------------------------------------------------------------

class TestFindCrossRepoCallers:
    """Verify caller discovery in a sibling graph.db."""

    def test_matching_caller_found(self, sibling_repo: Path) -> None:
        from code_forge.cross_repo_impact import find_cross_repo_callers
        sib_db = str(
            sibling_repo / ".code-review-graph" / "graph.db",
        )
        changed = [
            {"name": "process_request",
             "qualified_name": "handler::process_request",
             "file_path": "src/lib/handler.py",
             "module": "handler"},
        ]
        callers = find_cross_repo_callers(sib_db, changed)
        assert len(callers) >= 1
        c = callers[0]
        assert c["symbol"] == "process_request"
        assert c["caller_qualified"] == "consumer::call_handler"
        assert c["caller_file"] == "lib/consumer.py"

    def test_unrelated_symbol_no_callers(
        self, sibling_repo: Path,
    ) -> None:
        from code_forge.cross_repo_impact import find_cross_repo_callers
        sib_db = str(
            sibling_repo / ".code-review-graph" / "graph.db",
        )
        changed = [
            {"name": "totally_unrelated",
             "qualified_name": "other::totally_unrelated",
             "file_path": "src/other.py",
             "module": "other"},
        ]
        callers = find_cross_repo_callers(sib_db, changed)
        assert callers == []


# ---------------------------------------------------------------------------
# (d) SKIP states
# ---------------------------------------------------------------------------

class TestSkipStates:
    """Each SKIP cause yields infra_errors non-empty + findings == []."""

    def test_primary_db_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from code_forge.cross_repo_impact import CrossRepoImpactRunner
        # repo with no graph.db
        repo = tmp_path / "no_db_repo"
        repo.mkdir()
        # registry pointing at this repo
        reg_path = tmp_path / "reg" / "registry.json"
        _make_registry(reg_path, [
            {"path": str(repo.resolve())},
        ])
        monkeypatch.setenv("CRG_REGISTRY_PATH", str(reg_path))

        runner = CrossRepoImpactRunner()
        result = runner.run(_sample_diff(), repo)
        assert result == []
        assert len(runner.infra_errors) > 0
        assert "primary" in runner.infra_errors[0].lower() or \
               "graph.db" in runner.infra_errors[0].lower()

    def test_no_siblings_registered(
        self, tmp_path: Path,
        primary_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from code_forge.cross_repo_impact import CrossRepoImpactRunner
        # registry with only the primary repo
        reg_path = tmp_path / "reg" / "registry.json"
        _make_registry(reg_path, [
            {"path": str(primary_repo.resolve()), "alias": "primary"},
        ])
        monkeypatch.setenv("CRG_REGISTRY_PATH", str(reg_path))

        runner = CrossRepoImpactRunner()
        result = runner.run(_sample_diff(), primary_repo)
        assert result == []
        assert len(runner.infra_errors) > 0
        assert "sibling" in runner.infra_errors[0].lower() or \
               "registered" in runner.infra_errors[0].lower()

    def test_sibling_db_file_missing(
        self, tmp_path: Path,
        primary_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from code_forge.cross_repo_impact import CrossRepoImpactRunner
        # sibling dir exists but has no graph.db
        sib = tmp_path / "empty_sibling"
        sib.mkdir()
        reg_path = tmp_path / "reg" / "registry.json"
        _make_registry(reg_path, [
            {"path": str(primary_repo.resolve()), "alias": "primary"},
            {"path": str(sib.resolve()), "alias": "empty-sib"},
        ])
        monkeypatch.setenv("CRG_REGISTRY_PATH", str(reg_path))

        runner = CrossRepoImpactRunner()
        result = runner.run(_sample_diff(), primary_repo)
        assert result == []
        assert any("empty-sib" in e for e in runner.infra_errors)

    def test_sibling_db_corrupt_zero_bytes(
        self, tmp_path: Path,
        primary_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from code_forge.cross_repo_impact import CrossRepoImpactRunner
        # sibling with a zero-byte graph.db
        sib = tmp_path / "corrupt_sibling"
        sib.mkdir()
        crg_dir = sib / ".code-review-graph"
        crg_dir.mkdir()
        (crg_dir / "graph.db").write_bytes(b"")
        reg_path = tmp_path / "reg" / "registry.json"
        _make_registry(reg_path, [
            {"path": str(primary_repo.resolve()), "alias": "primary"},
            {"path": str(sib.resolve()), "alias": "corrupt-sib"},
        ])
        monkeypatch.setenv("CRG_REGISTRY_PATH", str(reg_path))

        runner = CrossRepoImpactRunner()
        result = runner.run(_sample_diff(), primary_repo)
        assert result == []
        assert any("corrupt-sib" in e for e in runner.infra_errors)
        assert any(
            "unreadable" in e.lower() or "error" in e.lower()
            for e in runner.infra_errors
        )


# ---------------------------------------------------------------------------
# (e) Genuine no-callers: [] with EMPTY infra_errors
# ---------------------------------------------------------------------------

class TestGenuineNoCallers:
    """Siblings present, no CALLS match -> [] AND infra_errors EMPTY."""

    def test_no_callers_empty_infra_errors(
        self, tmp_path: Path,
        primary_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from code_forge.cross_repo_impact import CrossRepoImpactRunner
        # sibling with valid db but no CALLS edges at all
        sib = tmp_path / "no_calls_sibling"
        sib.mkdir()
        crg_dir = sib / ".code-review-graph"
        crg_dir.mkdir()
        _make_db(
            crg_dir / "graph.db",
            nodes=[
                (1, "function", "unrelated_fn",
                 "unrelated::unrelated_fn",
                 "src/unrelated.py", 1, 10),
            ],
            edges=[],  # no CALLS edges
        )
        reg_path = tmp_path / "reg" / "registry.json"
        _make_registry(reg_path, [
            {"path": str(primary_repo.resolve()), "alias": "primary"},
            {"path": str(sib.resolve()), "alias": "quiet-sib"},
        ])
        monkeypatch.setenv("CRG_REGISTRY_PATH", str(reg_path))

        runner = CrossRepoImpactRunner()
        result = runner.run(_sample_diff(), primary_repo)
        assert result == []
        # distinguisher: infra_errors MUST be empty for genuine no-impact
        assert runner.infra_errors == []


# ---------------------------------------------------------------------------
# (f) Finding shape
# ---------------------------------------------------------------------------

class TestFindingShape:
    """Verify id, file, line_range, axis, description fields."""

    def test_finding_fields(
        self,
        primary_repo: Path,
        sibling_repo: Path,
        registry_env: Path,
    ) -> None:
        from code_forge.cross_repo_impact import CrossRepoImpactRunner
        runner = CrossRepoImpactRunner()
        findings = runner.run(_sample_diff(), primary_repo)
        assert len(findings) >= 1

        f = findings[0]
        assert isinstance(f, AdvisoryFinding)
        # id format
        assert f.id.startswith("cross-repo-impact-")
        # axis
        assert f.axis == "CROSS-REPO-IMPACT"
        # file: alias:relpath
        assert "sibling:" in f.file
        assert ":" in f.file
        parts = f.file.split(":", 1)
        assert parts[0] == "sibling"
        assert parts[1]  # relpath non-empty
        # line_range
        assert isinstance(f.line_range, tuple)
        assert len(f.line_range) == 2
        # symbol in description
        assert "process_request" in f.description
        # attribution
        assert f.attribution


# ---------------------------------------------------------------------------
# (g) _subsystem_proximity: token-set overlap ordering
# ---------------------------------------------------------------------------

class TestSubsystemProximity:
    """Verify corrected proximity predicate (token-set overlap)."""

    def test_shared_tokens_positive(self) -> None:
        from code_forge.cross_repo_impact import _subsystem_proximity
        # drivers/net vs net/core share "net" token -> score > 0
        score = _subsystem_proximity("drivers/net/foo.c", "net/core/bar.c")
        assert score > 0

    def test_same_path_max(self) -> None:
        from code_forge.cross_repo_impact import _subsystem_proximity
        score = _subsystem_proximity("src/lib/handler.py", "src/lib/other.py")
        assert score > 0.5

    def test_disjoint_paths_zero(self) -> None:
        from code_forge.cross_repo_impact import _subsystem_proximity
        score = _subsystem_proximity("alpha/beta/x.py", "gamma/delta/y.py")
        assert score == 0.0

    def test_ordering_closer_first(
        self,
        tmp_path: Path,
        primary_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Closer subsystem ranks higher in findings output."""
        from code_forge.cross_repo_impact import CrossRepoImpactRunner
        # Two siblings: one close subsystem, one far
        close_sib = tmp_path / "close_sib"
        close_sib.mkdir()
        crg_close = close_sib / ".code-review-graph"
        crg_close.mkdir()
        _make_db(
            crg_close / "graph.db",
            nodes=[
                (1, "function", "close_caller",
                 "lib_handler::close_caller",
                 "src/lib/consumer.py", 5, 10),
            ],
            edges=[
                ("CALLS", "lib_handler::close_caller", "process_request"),
                ("IMPORTS_FROM", "lib_handler::close_caller", "handler"),
            ],
        )

        far_sib = tmp_path / "far_sib"
        far_sib.mkdir()
        crg_far = far_sib / ".code-review-graph"
        crg_far.mkdir()
        _make_db(
            crg_far / "graph.db",
            nodes=[
                (1, "function", "far_caller",
                 "unrelated_pkg::far_caller",
                 "unrelated/pkg/caller.py", 100, 110),
            ],
            edges=[
                ("CALLS", "unrelated_pkg::far_caller", "process_request"),
                ("IMPORTS_FROM", "unrelated_pkg::far_caller", "handler"),
            ],
        )

        reg_path = tmp_path / "reg" / "registry.json"
        _make_registry(reg_path, [
            {"path": str(primary_repo.resolve()), "alias": "primary"},
            {"path": str(close_sib.resolve()), "alias": "close"},
            {"path": str(far_sib.resolve()), "alias": "far"},
        ])
        monkeypatch.setenv("CRG_REGISTRY_PATH", str(reg_path))

        runner = CrossRepoImpactRunner()
        findings = runner.run(_sample_diff(), primary_repo)
        assert len(findings) >= 2
        # first finding should be the closer subsystem
        assert "close" in findings[0].file or \
               "src/lib" in findings[0].file


# ---------------------------------------------------------------------------
# (h) _TOP_N cap
# ---------------------------------------------------------------------------

class TestTopNCap:
    """Verify output is capped at _TOP_N findings."""

    def test_cap_at_top_n(
        self,
        tmp_path: Path,
        primary_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from code_forge.cross_repo_impact import (
            CrossRepoImpactRunner,
            _TOP_N,
        )
        # sibling with > _TOP_N call sites to the same changed symbol
        sib = tmp_path / "many_callers"
        sib.mkdir()
        crg_dir = sib / ".code-review-graph"
        crg_dir.mkdir()

        num_callers = _TOP_N + 5
        nodes = []
        edges = []
        for i in range(num_callers):
            qn = "caller_mod::caller_%d" % i
            nodes.append(
                (i + 1, "function", "caller_%d" % i, qn,
                 "callers/c%d.py" % i, i * 10, i * 10 + 5),
            )
            edges.append(("CALLS", qn, "process_request"))
            edges.append(("IMPORTS_FROM", qn, "handler"))

        _make_db(crg_dir / "graph.db", nodes, edges)

        reg_path = tmp_path / "reg" / "registry.json"
        _make_registry(reg_path, [
            {"path": str(primary_repo.resolve()), "alias": "primary"},
            {"path": str(sib.resolve()), "alias": "many"},
        ])
        monkeypatch.setenv("CRG_REGISTRY_PATH", str(reg_path))

        runner = CrossRepoImpactRunner()
        findings = runner.run(_sample_diff(), primary_repo)
        assert len(findings) == _TOP_N


# ---------------------------------------------------------------------------
# Cache: second run returns cached results
# ---------------------------------------------------------------------------

class TestCaching:
    """Verify run() caches after first call."""

    def test_cached_on_second_call(
        self,
        primary_repo: Path,
        sibling_repo: Path,
        registry_env: Path,
    ) -> None:
        from code_forge.cross_repo_impact import CrossRepoImpactRunner
        runner = CrossRepoImpactRunner()
        first = runner.run(_sample_diff(), primary_repo)
        second = runner.run(_sample_diff(), primary_repo)
        assert first is second
