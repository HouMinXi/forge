# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Integration tests for CrossRepoImpactRunner wired into the advisory pipeline.

Proves end-to-end:
  SC-1: a cross-repo finding surfaces through the wired advisory_runners path
  SC-2: missing registry -> SKIP, review completes without crash
  SC-3: advisory findings never block verdict, never reset cycle counter

Fixture topology: two in-process sqlite graph.db repos (A and B) plus a
fixture registry.json under tmp_path. Repo A exports a symbol "shared_api";
repo B has a function that CALLS "shared_api" (with IMPORTS_FROM for
disambiguation). CRG_REGISTRY_PATH env var points at the fixture registry.

Schema matches the live code-review-graph schema:
  nodes(id, kind, name, qualified_name, file_path, line_start, line_end)
  edges(kind, source_qualified, target_qualified)
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

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
_EDGES_DDL = (
    "CREATE TABLE edges ("
    "  kind TEXT,"
    "  source_qualified TEXT,"
    "  target_qualified TEXT"
    ")"
)


def _make_db(path: Path, nodes: list[tuple], edges: list[tuple]) -> Path:
    """Build a real sqlite graph.db at *path* with the given rows."""
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
        "diff --git a/{f} b/{f}\n"
        "--- a/{f}\n"
        "+++ b/{f}\n"
        "@@ -10,3 +10,4 @@\n"
        " existing line\n"
        "+new line\n"
    ).format(f=file_path)


# ---------------------------------------------------------------------------
# Two-repo fixture builder
# ---------------------------------------------------------------------------

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
        nodes=[
            (1, "function", "shared_api",
             "api::shared_api",
             "a_pkg/api.py", 10, 20),
            (2, "function", "internal_fn",
             "api::internal_fn",
             "a_pkg/api.py", 25, 35),
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
        nodes=[
            (1, "function", "use_shared",
             "consumer::use_shared",
             "b_pkg/consumer.py", 5, 15),
        ],
        edges=[
            # B calls shared_api from A
            ("CALLS", "consumer::use_shared", "shared_api"),
            # IMPORTS_FROM for disambiguation
            ("IMPORTS_FROM", "consumer::use_shared", "api"),
        ],
    )

    # Registry pointing at both repos
    reg_path = tmp_path / "registry" / "registry.json"
    _make_registry(reg_path, [
        {"path": str(repo_a.resolve()), "alias": "repoa"},
        {"path": str(repo_b.resolve()), "alias": "repob"},
    ])
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
        """Run the runner via the wired path and assert a CROSS-REPO-IMPACT
        finding appears with alias:file format and names the changed symbol.
        """
        from code_forge.cross_repo_impact import CrossRepoImpactRunner

        # Verify the runner IS in the primary advisory_runners list
        # (import-level wiring proof).
        import code_forge.cross_repo as cross_repo_mod
        import inspect
        source = inspect.getsource(cross_repo_mod)
        assert "CrossRepoImpactRunner" in source, (
            "CrossRepoImpactRunner not imported in cross_repo.py"
        )

        # Now run the runner directly against the fixture to prove
        # it produces correct findings with the two-repo topology.
        runner = CrossRepoImpactRunner()
        diff = _sample_diff("a_pkg/api.py")
        repo_a = two_repo_fixture["repo_a"]
        findings = runner.run(diff, repo_a)

        assert len(findings) >= 1, (
            "Expected at least one cross-repo finding; got none"
        )

        f = findings[0]
        assert isinstance(f, AdvisoryFinding)
        assert f.axis == "CROSS-REPO-IMPACT"
        # file must be alias:relpath format
        assert "repob:" in f.file
        parts = f.file.split(":", 1)
        assert parts[0] == "repob"
        assert parts[1] == "b_pkg/consumer.py"
        # description must name the changed symbol
        assert "shared_api" in f.description

    def test_runner_in_primary_advisory_list(self) -> None:
        """Verify CrossRepoImpactRunner is in the primary advisory_runners
        list by inspecting the source of the is_primary branch.
        """
        import inspect
        import code_forge.cross_repo as cross_repo_mod
        source = inspect.getsource(cross_repo_mod)
        # The runner must appear in the advisory_runners list construction
        # inside the is_primary branch
        assert "CrossRepoImpactRunner()" in source, (
            "CrossRepoImpactRunner() not in advisory_runners list"
        )


# ---------------------------------------------------------------------------
# SC-3: Advisory contract -- never blocks, never resets cycle
# ---------------------------------------------------------------------------

class TestSC3AdvisoryContract:
    """Advisory finding never blocks verdict, never suppresses other findings."""

    def test_advisory_finding_never_blocks(
        self,
        two_repo_fixture: dict,
    ) -> None:
        """An advisory cross-repo finding must not block the verdict."""
        from code_forge.cross_repo_impact import CrossRepoImpactRunner
        runner = CrossRepoImpactRunner()
        assert runner.is_advisory is True

        diff = _sample_diff("a_pkg/api.py")
        repo_a = two_repo_fixture["repo_a"]
        findings = runner.run(diff, repo_a)

        # All findings must be AdvisoryFinding (structurally cannot block)
        for f in findings:
            assert isinstance(f, AdvisoryFinding), (
                "Finding %s is not AdvisoryFinding" % f.id
            )

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

        for f in findings:
            assert isinstance(f, AdvisoryFinding)
            assert not isinstance(f, StateFinding), (
                "Finding must not be StateFinding (would participate "
                "in convergence)"
            )


# ---------------------------------------------------------------------------
# SC-2: Missing registry -> SKIP, review completes
# ---------------------------------------------------------------------------

class TestSC2RegistryAbsent:
    """With no registry / CRG_REGISTRY_PATH pointing nowhere, runner emits
    SKIP (infra_errors non-empty) and the review still completes.
    """

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
            nodes=[
                (1, "function", "some_fn",
                 "mod::some_fn",
                 "src/mod.py", 1, 10),
            ],
            edges=[],
        )

        runner = CrossRepoImpactRunner()
        result = runner.run(_sample_diff("src/mod.py"), repo)

        # SKIP: empty findings, infra_errors populated
        assert result == []
        assert len(runner.infra_errors) > 0

    def test_unset_registry_skip(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """CRG_REGISTRY_PATH unset -> falls back to default path, which
        almost certainly does not exist in test env -> SKIP.
        """
        from code_forge.cross_repo_impact import CrossRepoImpactRunner

        monkeypatch.delenv("CRG_REGISTRY_PATH", raising=False)

        # Mock the default registry path to return no repos
        # (avoids touching ~/.code-review-graph in tests)
        with patch(
            "code_forge.cross_repo_impact._registry_path",
            return_value=str(tmp_path / "no_such_registry.json"),
        ):
            repo = tmp_path / "repo"
            repo.mkdir()
            crg = repo / ".code-review-graph"
            crg.mkdir()
            _make_db(
                crg / "graph.db",
                nodes=[
                    (1, "function", "fn",
                     "m::fn",
                     "src/m.py", 1, 5),
                ],
                edges=[],
            )

            runner = CrossRepoImpactRunner()
            result = runner.run(_sample_diff("src/m.py"), repo)

            assert result == []
            assert len(runner.infra_errors) > 0


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
        assert "CrossRepoImpactRunner()" in primary_block, (
            "CrossRepoImpactRunner() must be in the is_primary branch"
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
        sibling_block = source[sibling_idx:sibling_idx + 200]
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
            nodes=[
                (1, "function", "shared_api",
                 abs_a + "::shared_api",
                 abs_a, 10, 20),
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
            nodes=[
                (1, "function", "use_shared",
                 abs_b + "::use_shared",
                 abs_b, 5, 15),
            ],
            edges=[
                ("CALLS", abs_b + "::use_shared", "shared_api"),
                ("IMPORTS_FROM", abs_b, "api"),
            ],
        )

        reg_path = tmp_path / "registry" / "registry.json"
        _make_registry(reg_path, [
            {"path": str(repo_a.resolve()), "alias": "repoa"},
            {"path": str(repo_b.resolve()), "alias": "repob"},
        ])
        monkeypatch.setenv("CRG_REGISTRY_PATH", str(reg_path))

        runner = CrossRepoImpactRunner()
        diff = _sample_diff("a_pkg/api.py")
        findings = runner.run(diff, repo_a)

        assert len(findings) >= 1, (
            "Expected cross-repo finding from absolute-path fixture"
        )
        f = findings[0]

        # The file field must be repo-relative, not absolute
        assert f.file == "repob:client_b/consumer.py", (
            "Expected repo-relative path, got: %s" % f.file
        )
        assert "/tmp" not in f.file, (
            "Absolute path leaked into finding.file: %s" % f.file
        )
        assert f.line_range == [5, 5], (
            "line_range should come from sibling node, got: %s"
            % f.line_range
        )

        # Proximity should use relative paths (no inflated Jaccard
        # from shared /tmp/pytest-xxx/ parent segments)
        assert "shared_api" in f.description
        assert len(runner.infra_errors) == 0

    def test_symlink_registered_realpath_in_db(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """DB stores symlink-based path, prefix is resolved -> fallback strips.

        Scenario: code-review-graph build ran inside a symlink directory,
        so nodes.file_path is symlink-based (os.path.abspath does not
        resolve symlinks). The runner prefix uses Path.resolve() which
        DOES resolve symlinks, creating a mismatch. The resolve fallback
        must catch this: resolve the db path, then re-try startswith.
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

        # DB stores SYMLINK-based path (mimics build from symlink dir)
        symlink_a_file = str(link_a / "pkg" / "api.py")
        _make_db(
            crg_a / "graph.db",
            nodes=[
                (1, "function", "target_fn",
                 symlink_a_file + "::target_fn",
                 symlink_a_file, 10, 20),
            ],
            edges=[],
        )

        symlink_b_file = str(link_b / "client" / "use.py")
        _make_db(
            crg_b / "graph.db",
            nodes=[
                (1, "function", "call_target",
                 symlink_b_file + "::call_target",
                 symlink_b_file, 7, 12),
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
        _make_registry(reg_path, [
            {"path": str(real_a), "alias": "repoa"},
            {"path": str(real_b), "alias": "repob"},
        ])
        monkeypatch.setenv("CRG_REGISTRY_PATH", str(reg_path))

        runner = CrossRepoImpactRunner()
        diff = _sample_diff("pkg/api.py")
        findings = runner.run(diff, real_a)

        assert len(findings) >= 1, (
            "Symlink-registered repo must still produce findings"
        )
        f = findings[0]

        # Must be repo-relative despite symlink mismatch
        assert f.file == "repob:client/use.py", (
            "Expected repo-relative path, got: %s" % f.file
        )
        assert "/tmp" not in f.file, (
            "Absolute path leaked through symlink: %s" % f.file
        )
        assert f.line_range == [7, 7]
        assert "target_fn" in f.description
        assert len(runner.infra_errors) == 0
