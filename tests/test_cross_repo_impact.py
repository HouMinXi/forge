# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for CrossRepoImpactRunner advisory axis.

Uses tmp_path sqlite fixtures built by hand -- real sqlite files, not mocks --
so the sqlite3 query path is actually exercised. Fixture schema matches the
live code-review-graph schema:
  nodes(id, kind, name, qualified_name, file_path, line_start, line_end)
  edges(kind, source_qualified, target_qualified)
  metadata(key, value), including the owning repo_root

Seam: CRG_REGISTRY_PATH env var points at a fixture registry.json under
tmp_path, so tests never touch ~/.code-review-graph/.
"""

from __future__ import annotations

from contextlib import closing
import hashlib
import json
import socket
import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("code_review_graph")

from code_forge.advisory import AdvisoryFinding
from code_forge.cross_repo_impact import CrossRepoImpactRunner


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
_EDGES_DDL = "CREATE TABLE edges (  kind TEXT,  source_qualified TEXT,  target_qualified TEXT)"


def _make_db(path: Path, nodes: list[tuple], edges: list[tuple]) -> Path:
    """Build a real sqlite graph.db at *path* with the given rows.

    nodes: list of (id, kind, name, qualified_name, file_path, line_start,
           line_end)
    edges: list of (kind, source_qualified, target_qualified)
    """
    conn = sqlite3.connect(str(path))
    conn.execute(_NODES_DDL)
    conn.execute(_EDGES_DDL)
    conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        "INSERT INTO metadata VALUES (?, ?)",
        ("repo_root", str(path.parent.parent.resolve())),
    )
    conn.executemany(
        "INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?)",
        nodes,
    )
    conn.executemany(
        "INSERT INTO edges VALUES (?, ?, ?)",
        edges,
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
        "diff --git a/{f} b/{f}\n--- a/{f}\n+++ b/{f}\n@@ -10,3 +10,4 @@\n existing line\n+new line\n"
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
            (1, "function", "process_request", "handler::process_request", "src/lib/handler.py", 10, 20),
            (2, "function", "helper_fn", "handler::helper_fn", "src/lib/handler.py", 25, 30),
            # unnamed node -- must be skipped
            (3, "module", "module-level", "handler::module-level", "src/lib/handler.py", 1, 50),
            # unnamed "lines ..." node -- must be skipped
            (4, "block", "lines 5-9", "handler::lines 5-9", "src/lib/handler.py", 5, 9),
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
            (1, "function", "call_handler", "consumer::call_handler", "lib/consumer.py", 15, 25),
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
    _make_registry(
        reg_path,
        [
            {"path": str(primary_repo.resolve()), "alias": "primary"},
            {"path": str(sibling_repo.resolve()), "alias": "sibling"},
        ],
    )
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
        self,
        primary_repo: Path,
        registry_env: Path,
    ) -> None:
        from code_forge.cross_repo_impact import CrossRepoImpactRunner

        runner = CrossRepoImpactRunner()
        result = runner.run("", primary_repo)
        assert result == []
        assert runner.infra_errors == []

    def test_whitespace_diff_returns_empty(
        self,
        primary_repo: Path,
        registry_env: Path,
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
        symbols = resolve_changed_symbols(diff, primary_db, repo_root=primary_repo)
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
        symbols = resolve_changed_symbols(diff, primary_db, repo_root=primary_repo)
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
            {
                "name": "process_request",
                "qualified_name": "handler::process_request",
                "file_path": "src/lib/handler.py",
                "module": "handler",
            },
        ]
        callers = find_cross_repo_callers(sib_db, changed, repo_root=sibling_repo)
        assert len(callers) >= 1
        c = callers[0]
        assert c["symbol"] == "process_request"
        assert c["caller_qualified"] == "consumer::call_handler"
        assert c["caller_file"] == "lib/consumer.py"

    def test_unrelated_symbol_no_callers(
        self,
        sibling_repo: Path,
    ) -> None:
        from code_forge.cross_repo_impact import find_cross_repo_callers

        sib_db = str(
            sibling_repo / ".code-review-graph" / "graph.db",
        )
        changed = [
            {
                "name": "totally_unrelated",
                "qualified_name": "other::totally_unrelated",
                "file_path": "src/other.py",
                "module": "other",
            },
        ]
        callers = find_cross_repo_callers(sib_db, changed, repo_root=sibling_repo)
        assert callers == []


# ---------------------------------------------------------------------------
# (d) SKIP states
# ---------------------------------------------------------------------------


class TestSkipStates:
    """Each SKIP cause yields infra_errors non-empty + findings == []."""

    def test_primary_db_missing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from code_forge.cross_repo_impact import CrossRepoImpactRunner

        # repo with no graph.db
        repo = tmp_path / "no_db_repo"
        repo.mkdir()
        # registry pointing at this repo
        reg_path = tmp_path / "reg" / "registry.json"
        _make_registry(
            reg_path,
            [
                {"path": str(repo.resolve())},
            ],
        )
        monkeypatch.setenv("CRG_REGISTRY_PATH", str(reg_path))

        runner = CrossRepoImpactRunner()
        result = runner.run(_sample_diff(), repo)
        assert result == []
        assert len(runner.infra_errors) > 0
        assert (
            "primary" in runner.infra_errors[0].lower() or "graph.db" in runner.infra_errors[0].lower()
        )

    def test_no_siblings_registered(
        self,
        tmp_path: Path,
        primary_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from code_forge.cross_repo_impact import CrossRepoImpactRunner

        # registry with only the primary repo
        reg_path = tmp_path / "reg" / "registry.json"
        _make_registry(
            reg_path,
            [
                {"path": str(primary_repo.resolve()), "alias": "primary"},
            ],
        )
        monkeypatch.setenv("CRG_REGISTRY_PATH", str(reg_path))

        runner = CrossRepoImpactRunner()
        result = runner.run(_sample_diff(), primary_repo)
        assert result == []
        assert len(runner.infra_errors) > 0
        assert (
            "sibling" in runner.infra_errors[0].lower() or "registered" in runner.infra_errors[0].lower()
        )

    def test_sibling_db_file_missing(
        self,
        tmp_path: Path,
        primary_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from code_forge.cross_repo_impact import CrossRepoImpactRunner

        # sibling dir exists but has no graph.db
        sib = tmp_path / "empty_sibling"
        sib.mkdir()
        reg_path = tmp_path / "reg" / "registry.json"
        _make_registry(
            reg_path,
            [
                {"path": str(primary_repo.resolve()), "alias": "primary"},
                {"path": str(sib.resolve()), "alias": "empty-sib"},
            ],
        )
        monkeypatch.setenv("CRG_REGISTRY_PATH", str(reg_path))

        runner = CrossRepoImpactRunner()
        result = runner.run(_sample_diff(), primary_repo)
        assert result == []
        assert any("empty-sib" in e for e in runner.infra_errors)

    def test_sibling_db_corrupt_zero_bytes(
        self,
        tmp_path: Path,
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
        _make_registry(
            reg_path,
            [
                {"path": str(primary_repo.resolve()), "alias": "primary"},
                {"path": str(sib.resolve()), "alias": "corrupt-sib"},
            ],
        )
        monkeypatch.setenv("CRG_REGISTRY_PATH", str(reg_path))

        runner = CrossRepoImpactRunner()
        result = runner.run(_sample_diff(), primary_repo)
        assert result == []
        assert any("corrupt-sib" in e for e in runner.infra_errors)
        assert any("unreadable" in e.lower() or "error" in e.lower() for e in runner.infra_errors)


# ---------------------------------------------------------------------------
# (e) Genuine no-callers: [] with EMPTY infra_errors
# ---------------------------------------------------------------------------


class TestGenuineNoCallers:
    """Siblings present, no CALLS match -> [] AND infra_errors EMPTY."""

    def test_no_callers_empty_infra_errors(
        self,
        tmp_path: Path,
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
                (1, "function", "unrelated_fn", "unrelated::unrelated_fn", "src/unrelated.py", 1, 10),
            ],
            edges=[],  # no CALLS edges
        )
        reg_path = tmp_path / "reg" / "registry.json"
        _make_registry(
            reg_path,
            [
                {"path": str(primary_repo.resolve()), "alias": "primary"},
                {"path": str(sib.resolve()), "alias": "quiet-sib"},
            ],
        )
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
                (
                    1,
                    "function",
                    "close_caller",
                    "lib_handler::close_caller",
                    "src/lib/consumer.py",
                    5,
                    10,
                ),
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
                (
                    1,
                    "function",
                    "far_caller",
                    "unrelated_pkg::far_caller",
                    "unrelated/pkg/caller.py",
                    100,
                    110,
                ),
            ],
            edges=[
                ("CALLS", "unrelated_pkg::far_caller", "process_request"),
                ("IMPORTS_FROM", "unrelated_pkg::far_caller", "handler"),
            ],
        )

        reg_path = tmp_path / "reg" / "registry.json"
        _make_registry(
            reg_path,
            [
                {"path": str(primary_repo.resolve()), "alias": "primary"},
                {"path": str(close_sib.resolve()), "alias": "close"},
                {"path": str(far_sib.resolve()), "alias": "far"},
            ],
        )
        monkeypatch.setenv("CRG_REGISTRY_PATH", str(reg_path))

        runner = CrossRepoImpactRunner()
        findings = runner.run(_sample_diff(), primary_repo)
        assert len(findings) >= 2
        # first finding should be the closer subsystem
        assert "close" in findings[0].file or "src/lib" in findings[0].file


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
                (i + 1, "function", "caller_%d" % i, qn, "callers/c%d.py" % i, i * 10, i * 10 + 5),
            )
            edges.append(("CALLS", qn, "process_request"))
            edges.append(("IMPORTS_FROM", qn, "handler"))

        _make_db(crg_dir / "graph.db", nodes, edges)

        reg_path = tmp_path / "reg" / "registry.json"
        _make_registry(
            reg_path,
            [
                {"path": str(primary_repo.resolve()), "alias": "primary"},
                {"path": str(sib.resolve()), "alias": "many"},
            ],
        )
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


# Read-only discovery regression fixtures are opt-in, never autouse.
discovery_diff = "--- a/api.py\n+++ b/api.py\n@@ -1 +1 @@\n-old\n+new\n"
discovery_missing_primary = "cross-repo-impact: primary graph.db not found; build code-review-graph"
discovery_no_siblings = "cross-repo-impact: no sibling repos registered; use code-review-graph register"


@pytest.fixture
def discovery_state(tmp_path, monkeypatch):
    root = tmp_path / "owned"
    root.mkdir()
    monkeypatch.setenv("CRG_HOME", str(root / "home"))
    monkeypatch.setenv("CRG_REGISTRY_PATH", str(root / "registry" / "registry.json"))
    monkeypatch.delenv("CRG_DATA_DIR", raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("network access is forbidden in graph discovery tests")

    for name in ("create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket, name, forbidden)
    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, forbidden)
    return root


def discovery_inventory(root):
    """Include directory identities and file content/mtime; reads may update atime."""
    result = {}
    for path in [root, *sorted(root.rglob("*"))]:
        stat = path.stat()
        result[str(path.relative_to(root))] = (
            stat.st_mode,
            stat.st_ino,
            stat.st_mtime_ns,
            None if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest(),
        )
    return result


def discovery_graph(root, *, data_dir=None, sibling=False):
    root.mkdir(exist_ok=True)
    data = data_dir if data_dir is not None else root / ".code-review-graph"
    data.mkdir(parents=True)
    db = data / "graph.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.executescript(
            "CREATE TABLE metadata(key TEXT, value TEXT);"
            "CREATE TABLE nodes(id INTEGER PRIMARY KEY, kind TEXT, name TEXT, "
            "qualified_name TEXT, file_path TEXT, line_start INTEGER, line_end INTEGER);"
            "CREATE TABLE edges(kind TEXT, source_qualified TEXT, target_qualified TEXT);"
        )
        conn.execute("INSERT INTO metadata VALUES ('repo_root', ?)", (str(root),))
        if sibling:
            (root / "use.py").write_text("def use():\n    return process()\n", encoding="utf-8")
            conn.execute("INSERT INTO nodes VALUES (1,'function','use','use::use','use.py',2,2)")
            conn.executemany(
                "INSERT INTO edges VALUES (?,?,?)",
                [("CALLS", "use::use", "process"), ("IMPORTS_FROM", "use::use", "api")],
            )
        else:
            conn.execute("INSERT INTO nodes VALUES (1,'function','process','api::process','api.py',1,2)")
        conn.commit()
    return db


def discovery_registry(path, repos):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"repos": repos}), encoding="utf-8")


def discovery_legacy(root):
    for suffix in ("", "-wal", "-shm", "-journal"):
        (root / (".code-review-graph.db" + suffix)).write_bytes(("owned fixture" + suffix).encode())


def discovery_run(root):
    runner = CrossRepoImpactRunner()
    result = runner.run(discovery_diff, root)
    return result, runner.infra_errors


@pytest.mark.parametrize("with_legacy", [False, True], ids=["absent", "legacy"])
@pytest.mark.parametrize("external", [False, True], ids=["default-data", "external-data"])
def test_discovery_primary_lookup_never_creates_or_migrates(
    discovery_state, monkeypatch, with_legacy, external
):
    primary = discovery_state / "primary"
    primary.mkdir()
    if with_legacy:
        discovery_legacy(primary)
    if external:
        monkeypatch.setenv("CRG_DATA_DIR", str(discovery_state / "external"))
    before = discovery_inventory(discovery_state)
    assert discovery_run(primary) == ([], [discovery_missing_primary])
    assert discovery_inventory(discovery_state) == before


@pytest.mark.parametrize("with_legacy", [False, True], ids=["absent", "legacy"])
def test_discovery_sibling_lookup_never_creates_or_migrates(discovery_state, with_legacy):
    primary, sibling = discovery_state / "primary", discovery_state / "sibling"
    discovery_graph(primary)
    sibling.mkdir()
    if with_legacy:
        discovery_legacy(sibling)
    discovery_registry(
        discovery_state / "registry" / "registry.json", [{"path": str(sibling), "alias": "consumer"}]
    )
    before = discovery_inventory(discovery_state)
    assert discovery_run(primary) == ([], ["cross-repo-impact: sibling 'consumer' graph.db missing"])
    assert discovery_inventory(discovery_state) == before


@pytest.mark.parametrize("default", [False, True], ids=["explicit", "default"])
def test_discovery_absent_registry_does_not_create_parent(discovery_state, monkeypatch, default):
    primary = discovery_state / "primary"
    discovery_graph(primary)
    if default:
        monkeypatch.delenv("CRG_REGISTRY_PATH")
    before = discovery_inventory(discovery_state)
    assert discovery_run(primary) == ([], [discovery_no_siblings])
    assert discovery_inventory(discovery_state) == before


@pytest.mark.parametrize("external", [False, True], ids=["default-data", "registry-data"])
def test_discovery_modern_graphs_keep_findings_and_all_legacy_sidecars(
    discovery_state, monkeypatch, external
):
    primary, sibling = discovery_state / "primary", discovery_state / "sibling"
    p_data = discovery_state / "data" / "primary" if external else None
    s_data = discovery_state / "data" / "sibling" if external else None
    discovery_graph(primary, data_dir=p_data)
    discovery_graph(sibling, data_dir=s_data, sibling=True)
    discovery_legacy(primary)
    discovery_legacy(sibling)
    repos = [{"path": str(primary), "alias": "main"}, {"path": str(sibling), "alias": "consumer"}]
    if external:
        repos[0]["data_dir"] = str(p_data)
        repos[1]["data_dir"] = str(s_data)
    discovery_registry(discovery_state / "home" / "registry.json", repos)
    monkeypatch.delenv("CRG_REGISTRY_PATH")
    before = discovery_inventory(discovery_state)
    findings, errors = discovery_run(primary)
    assert errors == []
    assert len(findings) == 1
    finding = findings[0]
    assert (finding.id, finding.axis, finding.file, finding.line_range, finding.description) == (
        "cross-repo-impact-1",
        "CROSS-REPO-IMPACT",
        "consumer:use.py",
        (2, 2),
        "process used by consumer at use.py:2",
    )
    assert finding.attribution == "cross-repo graph.db"
    assert discovery_inventory(discovery_state) == before


def discovery_forbid_mutators(patch):
    """Fail on write intent, not only observable end-state changes."""
    import code_review_graph.incremental as incremental
    import code_review_graph.registry as provider_registry

    def forbidden(*args, **kwargs):
        raise AssertionError("discovery called a filesystem mutator")

    original_open = Path.open

    def readonly_open(path, mode="r", *args, **kwargs):
        assert not any(flag in mode for flag in "wax+"), "discovery opened a writable file"
        return original_open(path, mode, *args, **kwargs)

    patch.setattr(Path, "open", readonly_open)
    patch.setattr(provider_registry, "Registry", forbidden)
    for name in ("get_db_path", "get_data_dir"):
        patch.setattr(incremental, name, forbidden)
    for name in ("mkdir", "rename", "unlink", "write_text"):
        patch.setattr(Path, name, forbidden)


def test_discovery_malformed_registry_is_reported_without_repair(discovery_state, monkeypatch):
    primary = discovery_state / "primary"
    discovery_graph(primary)
    reg = discovery_state / "registry" / "registry.json"
    reg.parent.mkdir()
    reg.write_text('{"repos": [', encoding="utf-8")
    before = discovery_inventory(discovery_state)
    with monkeypatch.context() as guarded:
        discovery_forbid_mutators(guarded)
        findings, errors = discovery_run(primary)
    assert findings == []
    assert errors and "registry" in errors[0].lower()
    assert discovery_inventory(discovery_state) == before


@pytest.mark.parametrize("explicit", [False, True], ids=["default", "explicit"])
def test_discovery_guarded_external_graphs_keep_real_findings(discovery_state, monkeypatch, explicit):
    primary, sibling = discovery_state / "primary", discovery_state / "sibling"
    primary_data, sibling_data = (
        discovery_state / "data" / "primary",
        discovery_state / "data" / "sibling",
    )
    discovery_graph(primary, data_dir=primary_data)
    discovery_graph(sibling, data_dir=sibling_data, sibling=True)
    discovery_legacy(primary)
    discovery_legacy(sibling)
    default = discovery_state / "home" / "registry.json"
    repos = [
        {"path": str(primary), "alias": "main", "data_dir": str(primary_data)},
        {"path": str(sibling), "alias": "consumer", "data_dir": str(sibling_data)},
    ]
    discovery_registry(default, repos)
    if explicit:
        # The override selects siblings, but its data_dir must not replace the
        # provider's default-registry graph-location precedence.
        discovery_registry(
            discovery_state / "registry" / "registry.json",
            [
                {"path": str(sibling), "alias": "consumer", "data_dir": str(discovery_state / "wrong")},
            ],
        )
    else:
        monkeypatch.delenv("CRG_REGISTRY_PATH")
    original_read = Path.open
    reads = []

    def count_read(path, *args, **kwargs):
        if path == default:
            reads.append(path)
        return original_read(path, *args, **kwargs)

    before = discovery_inventory(discovery_state)
    with monkeypatch.context() as guarded:
        guarded.setattr(Path, "open", count_read)
        discovery_forbid_mutators(guarded)
        findings, errors = discovery_run(primary)
    assert errors == []
    assert len(findings) == 1
    assert findings[0].file == "consumer:use.py"
    assert findings[0].description == "process used by consumer at use.py:2"
    assert reads == [default]
    assert discovery_inventory(discovery_state) == before


@pytest.mark.parametrize("explicit", [False, True], ids=["default", "explicit"])
def test_discovery_registry_deleted_during_read_stays_absent(discovery_state, monkeypatch, explicit):
    primary, sibling = discovery_state / "primary", discovery_state / "sibling"
    discovery_graph(primary)
    discovery_graph(sibling, sibling=True)
    reg = discovery_state / ("registry" if explicit else "home") / "registry.json"
    discovery_registry(reg, [{"path": str(sibling), "alias": "consumer"}])
    if not explicit:
        monkeypatch.delenv("CRG_REGISTRY_PATH")
    original_read, original_unlink, original_rmdir = Path.open, Path.unlink, Path.rmdir
    deleted = []

    def delete_before_read(path, *args, **kwargs):
        if path == reg and not deleted:
            # Controlled fixture race uses saved primitives; production mutators
            # remain guarded throughout the runner invocation.
            original_unlink(path)
            original_rmdir(path.parent)
            deleted.append(path)
        return original_read(path, *args, **kwargs)

    with monkeypatch.context() as guarded:
        guarded.setattr(Path, "open", delete_before_read)
        discovery_forbid_mutators(guarded)
        assert discovery_run(primary) == ([], [discovery_no_siblings])
    assert deleted == [reg]
    assert not reg.exists()
    assert not reg.parent.exists()


@pytest.mark.parametrize(
    "contents",
    [
        "null",
        "[]",
        "{}",
        '{"repos": {}}',
        '{"repos": [null]}',
        '{"repos": [{"path": ""}]}',
        '{"repos": [{"path": 7}]}',
        '{"repos": [{"path": "/repo", "alias": 7}]}',
        '{"repos": [{"path": "/repo", "data_dir": 7}]}',
    ],
)
def test_discovery_registry_validation_never_writes(discovery_state, monkeypatch, contents):
    from code_forge.cross_repo_impact import GraphDiscoveryError, _read_registry

    reg = discovery_state / "registry.json"
    reg.write_text(contents, encoding="utf-8")
    before = discovery_inventory(discovery_state)
    with monkeypatch.context() as guarded:
        discovery_forbid_mutators(guarded)
        with pytest.raises(GraphDiscoveryError):
            _read_registry(reg)
    assert discovery_inventory(discovery_state) == before


def test_discovery_unreadable_registry_never_repairs(discovery_state, monkeypatch):
    from code_forge.cross_repo_impact import GraphDiscoveryError, _read_registry

    reg = discovery_state / "registry.json"
    discovery_registry(reg, [])
    original_read = Path.open

    def deny_read(path, *args, **kwargs):
        if path == reg:
            raise PermissionError("fixture unreadable registry")
        return original_read(path, *args, **kwargs)

    before = discovery_inventory(discovery_state)
    with monkeypatch.context() as guarded:
        guarded.setattr(Path, "open", deny_read)
        discovery_forbid_mutators(guarded)
        with pytest.raises(GraphDiscoveryError):
            _read_registry(reg)
    assert discovery_inventory(discovery_state) == before


def test_discovery_locator_preserves_precedence_without_writes(discovery_state, monkeypatch):
    from code_forge.cross_repo_impact import _readonly_db_path

    root = discovery_state / "primary"
    root.mkdir()
    alias = discovery_state / "alias"
    alias.symlink_to(root, target_is_directory=True)
    monkeypatch.setenv("CRG_DATA_DIR", "  ~/env-data  ")
    monkeypatch.setenv("HOME", str(discovery_state))
    monkeypatch.setenv("USERPROFILE", str(discovery_state))
    monkeypatch.chdir(discovery_state)
    before = discovery_inventory(discovery_state)
    with monkeypatch.context() as guarded:
        discovery_forbid_mutators(guarded)
        # Canonical root matching and first-match behavior, even without data_dir.
        entries = [{"path": str(root)}, {"path": str(root), "data_dir": "ignored"}]
        assert _readonly_db_path(alias, entries) == discovery_state / "env-data" / "graph.db"
        # Registry data directories resolve literally, without tilde expansion.
        entries = [{"path": str(root), "data_dir": "~/registry-data"}]
        assert _readonly_db_path(root, entries) == discovery_state / "~" / "registry-data" / "graph.db"
        guarded.setenv("CRG_DATA_DIR", "  ")
        assert _readonly_db_path(root, []) == root / ".code-review-graph" / "graph.db"
    assert discovery_inventory(discovery_state) == before


def test_discovery_unresolvable_sibling_is_infrastructure_skip(discovery_state, monkeypatch):
    primary = discovery_state / "primary"
    discovery_graph(primary)
    sibling = discovery_state / "unresolvable"
    sibling.mkdir()
    original_resolve = Path.resolve

    def deny_resolution(path, *args, **kwargs):
        if path == sibling:
            raise OSError("fixture sibling resolution denied")
        return original_resolve(path, *args, **kwargs)

    discovery_registry(
        discovery_state / "registry" / "registry.json",
        [{"path": str(sibling), "alias": "unresolvable"}],
    )
    with monkeypatch.context() as guarded:
        guarded.setattr(Path, "resolve", deny_resolution)
        discovery_forbid_mutators(guarded)
        findings, errors = discovery_run(primary)
    assert findings == []
    assert errors and "registry" in errors[0].lower()
