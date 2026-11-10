# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Runner integration tests and source-level advisory wiring checks.

Checks:
  SC-1: the registered runner produces exactly one direct-caller finding
  SC-2: missing registry -> SKIP with the registry-specific diagnostic
  SC-3: nonempty results retain the nonblocking AdvisoryFinding type

These tests invoke the runner directly, not the review state machine.

Fixture topology: two in-process sqlite graph.db repos (A and B) plus a
fixture registry.json under tmp_path. Repo A exports a symbol "shared_api";
repo B has a function that CALLS "shared_api" (with IMPORTS_FROM for
disambiguation). CRG_REGISTRY_PATH env var points at the fixture registry.

Schema matches the live code-review-graph schema:
  nodes(id, kind, name, qualified_name, file_path, line_start, line_end)
  edges(kind, source_qualified, target_qualified)
  metadata(key, value), including the owning repo_root
"""

from __future__ import annotations

import json
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


def _make_db(path: Path, nodes: list[tuple], edges: list[tuple], *, repo_root: Path) -> Path:
    """Build a graph bound to the root spelling used by its stored paths."""
    conn = sqlite3.connect(str(path))
    conn.execute(_NODES_DDL)
    conn.execute(_EDGES_DDL)
    conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO metadata VALUES (?, ?)", ("repo_root", str(repo_root.absolute())))
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


def _make_registry(registry_path: Path, repos: list[dict]) -> Path:
    """Write a fixture registry.json in the expected {"repos": [...]} format."""
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps({"repos": repos}, indent=2) + "\n",
        encoding="utf-8",
    )
    return registry_path


def _sample_diff(file_path: str = "a_pkg/api.py") -> str:
    """Return a minimal unified diff touching *file_path*."""
    return (
        "diff --git a/{f} b/{f}\n--- a/{f}\n+++ b/{f}\n@@ -10,3 +10,4 @@\n existing line\n+new line\n"
    ).format(f=file_path)


# ---------------------------------------------------------------------------
# Two-repo fixture builder
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_graph_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the real provider's default registry and locator inside the fixture."""
    monkeypatch.setenv("CRG_HOME", str(tmp_path / "graph-home"))
    monkeypatch.delenv("CRG_DATA_DIR", raising=False)


@pytest.fixture()
def two_repo_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict:
    """Build two repos (A exports shared_api, B calls it) + registry.

    Returns a dict with keys: repo_a, repo_b, registry_path.
    Sets CRG_REGISTRY_PATH via monkeypatch.
    """
    # Repo A: defines symbol "shared_api" in a_pkg/api.py
    repo_a = tmp_path / "repo_a"
    repo_a.mkdir()
    crg_a = repo_a / ".code-review-graph"
    crg_a.mkdir()
    _make_db(
        crg_a / "graph.db",
        repo_root=repo_a,
        nodes=[
            (1, "function", "shared_api", "api::shared_api", "a_pkg/api.py", 10, 20),
            (2, "function", "internal_fn", "api::internal_fn", "a_pkg/api.py", 25, 35),
        ],
        edges=[],
    )

    # Repo B: a function that CALLS shared_api from repo A
    repo_b = tmp_path / "repo_b"
    repo_b.mkdir()
    crg_b = repo_b / ".code-review-graph"
    crg_b.mkdir()
    _make_db(
        crg_b / "graph.db",
        repo_root=repo_b,
        nodes=[
            (1, "function", "use_shared", "consumer::use_shared", "b_pkg/consumer.py", 5, 15),
            (2, "function", "indirect", "outer::indirect", "b_pkg/outer.py", 2, 4),
        ],
        edges=[
            # B calls shared_api from A
            ("CALLS", "consumer::use_shared", "shared_api"),
            # IMPORTS_FROM for disambiguation
            ("IMPORTS_FROM", "consumer::use_shared", "api"),
            # A caller of the direct caller must stay outside the one-hop result.
            ("CALLS", "outer::indirect", "use_shared"),
            ("IMPORTS_FROM", "outer::indirect", "consumer"),
        ],
    )

    # Registry pointing at both repos
    reg_path = tmp_path / "registry" / "registry.json"
    _make_registry(
        reg_path,
        [
            {"path": str(repo_a.resolve()), "alias": "repoa"},
            {"path": str(repo_b.resolve()), "alias": "repob"},
        ],
    )
    monkeypatch.setenv("CRG_REGISTRY_PATH", str(reg_path))

    return {
        "repo_a": repo_a,
        "repo_b": repo_b,
        "registry_path": reg_path,
    }


# ---------------------------------------------------------------------------
# SC-1: Wiring test -- runner surfaces findings through wired path
# ---------------------------------------------------------------------------


class TestSC1Wiring:
    """CrossRepoImpactRunner wired into advisory_runners produces findings."""

    def test_wired_runner_produces_finding(
        self,
        two_repo_fixture: dict,
    ) -> None:
        """Check source wiring and the runner's exact direct-caller finding."""
        from code_forge.cross_repo_impact import CrossRepoImpactRunner

        # Verify the runner IS in the primary advisory_runners list
        # (import-level wiring proof).
        import code_forge.cross_repo as cross_repo_mod
        import inspect

        source = inspect.getsource(cross_repo_mod)
        assert "CrossRepoImpactRunner" in source, "CrossRepoImpactRunner not imported in cross_repo.py"

        # Now run the runner directly against the fixture to prove
        # it produces correct findings with the two-repo topology.
        runner = CrossRepoImpactRunner()
        diff = _sample_diff("a_pkg/api.py")
        repo_a = two_repo_fixture["repo_a"]
        findings = runner.run(diff, repo_a)

        assert runner.infra_errors == []
        assert findings == [
            AdvisoryFinding(
                id="cross-repo-impact-1",
                axis="CROSS-REPO-IMPACT",
                file="repob:b_pkg/consumer.py",
                line_range=(5, 5),
                description="shared_api used by repob at b_pkg/consumer.py:5",
                attribution="cross-repo graph.db",
            )
        ]

    def test_runner_in_primary_advisory_list(self) -> None:
        """Verify CrossRepoImpactRunner is in the primary advisory_runners
        list by inspecting the source of the is_primary branch.
        """
        import inspect
        import code_forge.cross_repo as cross_repo_mod

        source = inspect.getsource(cross_repo_mod)
        # The runner must appear in the advisory_runners list construction
        # inside the is_primary branch
        assert "CrossRepoImpactRunner(" in source, "CrossRepoImpactRunner not in advisory_runners list"


# ---------------------------------------------------------------------------
# SC-3: Advisory finding type contract
# ---------------------------------------------------------------------------


class TestSC3AdvisoryContract:
    """Nonempty runner results retain the nonblocking advisory finding type."""

    def test_advisory_finding_never_blocks(
        self,
        two_repo_fixture: dict,
    ) -> None:
        """Cross-repo results use the nonblocking advisory type."""
        from code_forge.cross_repo_impact import CrossRepoImpactRunner

        runner = CrossRepoImpactRunner()
        assert runner.is_advisory is True

        diff = _sample_diff("a_pkg/api.py")
        repo_a = two_repo_fixture["repo_a"]
        findings = runner.run(diff, repo_a)

        assert runner.infra_errors == []
        assert len(findings) == 1
        # All findings must be AdvisoryFinding (structurally cannot block)
        for f in findings:
            assert isinstance(f, AdvisoryFinding), "Finding %s is not AdvisoryFinding" % f.id

    def test_advisory_findings_are_advisory_type(
        self,
        two_repo_fixture: dict,
    ) -> None:
        """Verify findings are AdvisoryFinding, not StateFinding."""
        from code_forge.cross_repo_impact import CrossRepoImpactRunner
        from code_forge.state import StateFinding

        runner = CrossRepoImpactRunner()
        diff = _sample_diff("a_pkg/api.py")
        repo_a = two_repo_fixture["repo_a"]
        findings = runner.run(diff, repo_a)

        assert runner.infra_errors == []
        assert len(findings) == 1
        for f in findings:
            assert isinstance(f, AdvisoryFinding)
            assert not isinstance(f, StateFinding), (
                "Finding must not be StateFinding (would participate in convergence)"
            )


# ---------------------------------------------------------------------------
# SC-2: Missing registry -> registry-specific SKIP
# ---------------------------------------------------------------------------


class TestSC2RegistryAbsent:
    """Missing registry yields no findings and the registry-specific diagnostic."""

    def test_missing_registry_skip(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """CRG_REGISTRY_PATH pointing at a nonexistent file -> SKIP."""
        from code_forge.cross_repo_impact import CrossRepoImpactRunner

        # Point at a path that does not exist
        monkeypatch.setenv(
            "CRG_REGISTRY_PATH",
            str(tmp_path / "nonexistent" / "registry.json"),
        )

        # Need a repo with a valid graph.db for the primary check
        repo = tmp_path / "repo"
        repo.mkdir()
        crg = repo / ".code-review-graph"
        crg.mkdir()
        _make_db(
            crg / "graph.db",
            repo_root=repo,
            nodes=[
                (1, "function", "some_fn", "mod::some_fn", "src/mod.py", 1, 10),
            ],
            edges=[],
        )

        runner = CrossRepoImpactRunner()
        result = runner.run(_sample_diff("src/mod.py"), repo)

        # The primary graph must be readable before registry absence is observed.
        assert result == []
        assert runner.infra_errors == [
            "cross-repo-impact: no sibling repos registered; use code-review-graph register"
        ]

    def test_unset_registry_skip(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """CRG_REGISTRY_PATH unset -> an isolated missing default registry -> SKIP."""
        from code_forge.cross_repo_impact import CrossRepoImpactRunner

        monkeypatch.delenv("CRG_REGISTRY_PATH", raising=False)

        repo = tmp_path / "repo"
        repo.mkdir()
        crg = repo / ".code-review-graph"
        crg.mkdir()
        _make_db(
            crg / "graph.db",
            repo_root=repo,
            nodes=[
                (1, "function", "fn", "m::fn", "src/m.py", 1, 5),
            ],
            edges=[],
        )

        runner = CrossRepoImpactRunner()
        result = runner.run(_sample_diff("src/m.py"), repo)

        assert result == []
        assert runner.infra_errors == [
            "cross-repo-impact: no sibling repos registered; use code-review-graph register"
        ]


@pytest.mark.parametrize("side", ["repo_a", "repo_b"])
def test_missing_graph_metadata_is_not_registry_absence(two_repo_fixture: dict, side: str) -> None:
    """Legacy graphs report their identity failure, not a missing registry."""
    db = two_repo_fixture[side] / ".code-review-graph" / "graph.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute("DROP TABLE metadata")
        conn.commit()
    finally:
        conn.close()

    runner = CrossRepoImpactRunner()
    result = runner.run(_sample_diff(), two_repo_fixture["repo_a"])

    graph = "primary" if side == "repo_a" else "sibling 'repob'"
    assert result == []
    assert runner.infra_errors == [
        f"cross-repo-impact: {graph} graph.db unreadable: "
        "graph root metadata unavailable; rebuild the graph"
    ]


# ---------------------------------------------------------------------------
# Wiring assertion: present in primary, absent from sibling
# ---------------------------------------------------------------------------


class TestWiringAssertion:
    """CrossRepoImpactRunner is in primary advisory_runners, NOT in sibling."""

    def test_present_in_primary_source(self) -> None:
        """Verify CrossRepoImpactRunner appears in the is_primary branch."""
        import inspect
        import code_forge.cross_repo as mod

        source = inspect.getsource(mod)

        # Find the is_primary branch and verify the runner is listed
        primary_idx = source.find("if is_primary:")
        sibling_idx = source.find("else:", primary_idx)
        assert primary_idx != -1
        assert sibling_idx != -1

        primary_block = source[primary_idx:sibling_idx]
        assert "CrossRepoImpactRunner(" in primary_block, (
            "CrossRepoImpactRunner must be in the is_primary branch"
        )

    def test_absent_from_sibling_source(self) -> None:
        """Verify CrossRepoImpactRunner does NOT appear in the sibling branch."""
        import inspect
        import code_forge.cross_repo as mod

        source = inspect.getsource(mod)

        # The else branch (sibling) should have advisory_runners = []
        sibling_idx = source.find("# Siblings: no L1 cost, no advisory runners")
        assert sibling_idx != -1
        # Grab a window after the sibling comment
        sibling_block = source[sibling_idx : sibling_idx + 200]
        assert "CrossRepoImpactRunner" not in sibling_block, (
            "CrossRepoImpactRunner must NOT be in the sibling branch"
        )


# ---------------------------------------------------------------------------
# Absolute-path regression: tool-built graph.db stores absolute file_path
# ---------------------------------------------------------------------------


class TestAbsolutePathStrip:
    """Verify the relpath strip handles tool-built absolute file_path.

    code-review-graph stores nodes.file_path as the absolute path on the
    build machine.  The runner must strip the repo root so that
    AdvisoryFinding.file is repo-relative ("alias:rel/path") and
    _subsystem_proximity is not inflated by shared /tmp/... parents.
    """

    def test_absolute_caller_stripped_to_relpath(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Absolute file_path in sibling nodes -> finding.file is relative."""
        repo_a = tmp_path / "repo_a"
        repo_a.mkdir()
        crg_a = repo_a / ".code-review-graph"
        crg_a.mkdir()
        # Primary node uses absolute path (mimics tool output)
        abs_a = str(repo_a.resolve() / "a_pkg" / "api.py")
        _make_db(
            crg_a / "graph.db",
            repo_root=repo_a,
            nodes=[
                (1, "function", "shared_api", abs_a + "::shared_api", abs_a, 10, 20),
            ],
            edges=[],
        )

        repo_b = tmp_path / "repo_b"
        repo_b.mkdir()
        crg_b = repo_b / ".code-review-graph"
        crg_b.mkdir()
        # Sibling node uses absolute path (mimics tool output)
        abs_b = str(repo_b.resolve() / "client_b" / "consumer.py")
        _make_db(
            crg_b / "graph.db",
            repo_root=repo_b,
            nodes=[
                (1, "function", "use_shared", abs_b + "::use_shared", abs_b, 5, 15),
            ],
            edges=[
                ("CALLS", abs_b + "::use_shared", "shared_api"),
                ("IMPORTS_FROM", abs_b, "api"),
            ],
        )

        reg_path = tmp_path / "registry" / "registry.json"
        _make_registry(
            reg_path,
            [
                {"path": str(repo_a.resolve()), "alias": "repoa"},
                {"path": str(repo_b.resolve()), "alias": "repob"},
            ],
        )
        monkeypatch.setenv("CRG_REGISTRY_PATH", str(reg_path))

        runner = CrossRepoImpactRunner()
        diff = _sample_diff("a_pkg/api.py")
        findings = runner.run(diff, repo_a)

        assert len(findings) == 1, "Expected exactly one direct caller from absolute-path fixture"
        f = findings[0]

        # The file field must be repo-relative, not absolute
        assert f.file == "repob:client_b/consumer.py", "Expected repo-relative path, got: %s" % f.file
        assert "/tmp" not in f.file, "Absolute path leaked into finding.file: %s" % f.file
        assert f.line_range == (5, 5), "line_range should come from sibling node, got: %s" % (
            f.line_range,
        )

        # Proximity should use relative paths (no inflated Jaccard
        # from shared /tmp/pytest-xxx/ parent segments)
        assert f.description == "shared_api used by repob at client_b/consumer.py:5"
        assert len(runner.infra_errors) == 0

    def test_symlink_registered_realpath_in_db(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Hand-seeded lexical root metadata binds symlink-spelled node paths.

        The registry uses canonical roots, so the runner must accept the
        matching recorded aliases and strip each caller to a relative path.
        This covers alternate graph root spellings, not current builder output.
        """
        # Real directories (graph.db lives here)
        real_a = tmp_path / "real_a"
        real_a.mkdir()
        crg_a = real_a / ".code-review-graph"
        crg_a.mkdir()

        real_b = tmp_path / "real_b"
        real_b.mkdir()
        crg_b = real_b / ".code-review-graph"
        crg_b.mkdir()

        # Symlinks (user's working paths)
        link_a = tmp_path / "link_a"
        link_a.symlink_to(real_a)
        link_b = tmp_path / "link_b"
        link_b.symlink_to(real_b)

        # Deliberately record the same symlink spelling in metadata and nodes.
        symlink_a_file = str(link_a / "pkg" / "api.py")
        _make_db(
            crg_a / "graph.db",
            repo_root=link_a,
            nodes=[
                (1, "function", "target_fn", symlink_a_file + "::target_fn", symlink_a_file, 10, 20),
            ],
            edges=[],
        )

        symlink_b_file = str(link_b / "client" / "use.py")
        _make_db(
            crg_b / "graph.db",
            repo_root=link_b,
            nodes=[
                (1, "function", "call_target", symlink_b_file + "::call_target", symlink_b_file, 7, 12),
            ],
            edges=[
                ("CALLS", symlink_b_file + "::call_target", "target_fn"),
                ("IMPORTS_FROM", symlink_b_file, "api"),
            ],
        )

        # Register via REAL paths (user ran 'register' from realpath)
        # -> prefix = str(Path(real_b).resolve()) + os.sep = real_b/
        # -> db file_path = link_b/client/use.py (symlink)
        # -> first startswith FAILS (real != symlink)
        # -> fallback resolves link_b -> real_b, then matches
        reg_path = tmp_path / "registry" / "registry.json"
        _make_registry(
            reg_path,
            [
                {"path": str(real_a), "alias": "repoa"},
                {"path": str(real_b), "alias": "repob"},
            ],
        )
        monkeypatch.setenv("CRG_REGISTRY_PATH", str(reg_path))

        runner = CrossRepoImpactRunner()
        diff = _sample_diff("pkg/api.py")
        findings = runner.run(diff, real_a)

        assert len(findings) == 1, "Symlink-root metadata must bind exactly one direct caller"
        f = findings[0]

        # Must be repo-relative despite symlink mismatch
        assert f.file == "repob:client/use.py", "Expected repo-relative path, got: %s" % f.file
        assert "/tmp" not in f.file, "Absolute path leaked through symlink: %s" % f.file
        assert f.line_range == (7, 7)
        assert f.description == "target_fn used by repob at client/use.py:7"
        assert len(runner.infra_errors) == 0
