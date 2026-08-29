# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi1990@gmail.com>
"""Tests for semantic diff grouping.

The grouping algorithm is deterministic and must stay that way: same diff,
same groups, no LLM anywhere in the path. These tests pin the properties the
charter accepts (.planning/charter_review_decomposition.md), each one traceable
to a measurement recorded there.
"""

from code_forge.diff_grouping import (
    Group,
    build_groups,
    churn_profile,
    classify_file,
    cross_group_edges,
    extract_names,
    group_diff,
)


def _entity(path, change_type="added", name="f"):
    return {
        "filePath": path,
        "changeType": change_type,
        "entityName": name,
        "entityType": "function",
    }


class TestChurnProfile:
    """Role classification keys on total churn, not on added count."""

    def test_counts_every_direction(self):
        prof = churn_profile([
            _entity("a.py", "added"),
            _entity("a.py", "deleted"),
            _entity("a.py", "modified"),
            _entity("a.py", "renamed"),
        ])
        assert prof["total"] == 4
        assert prof["added"] == 1
        assert prof["deleted"] == 1

    def test_unknown_change_type_raises(self):
        """A new sem variant must not silently score as zero churn.

        An earlier draft used "removed"; sem emits "deleted". Scoring an
        unrecognised type as nothing would have made every deletion look like
        an unchanged file.
        """
        import pytest
        with pytest.raises(ValueError, match="unknown sem changeType"):
            churn_profile([_entity("a.py", "obliterated")])


class TestClassifyFile:
    """Roles, including the deletion case that the added-count rule missed."""

    def test_deletion_heavy_file_is_engine(self):
        """Measured on 33110be: 11 deleted entities is engine-sized churn."""
        by_file = {"a.py": [_entity("a.py", "deleted") for _ in range(11)]}
        role, direction = classify_file("a.py", by_file)
        assert role == "engine"
        assert direction == "deletion"

    def test_addition_heavy_file_is_engine(self):
        by_file = {"a.py": [_entity("a.py", "added") for _ in range(19)]}
        role, direction = classify_file("a.py", by_file)
        assert role == "engine"
        assert direction == "addition"

    def test_thin_file_is_integration(self):
        by_file = {"a.py": [_entity("a.py", "modified")]}
        role, _ = classify_file("a.py", by_file)
        assert role == "integration"

    def test_tests_and_config_by_path(self):
        by_file = {
            "tests/test_a.py": [_entity("tests/test_a.py")],
            "conf.yaml": [_entity("conf.yaml")],
            "README.md": [_entity("README.md")],
            "run.sh": [_entity("run.sh")],
        }
        assert classify_file("tests/test_a.py", by_file)[0] == "test"
        assert classify_file("conf.yaml", by_file)[0] == "config"
        assert classify_file("README.md", by_file)[0] == "docs"
        assert classify_file("run.sh", by_file)[0] == "other"


class TestExtractNames:
    """Module-level scope only -- fable-5's correction, 72 edges to 19."""

    def test_module_level_definitions_only(self, tmp_path):
        f = tmp_path / "m.py"
        f.write_text(
            "CONST = 1\n"
            "def top():\n"
            "    inner_local = 2\n"
            "    return inner_local\n"
            "class Cls:\n"
            "    def meth(self):\n"
            "        pass\n"
        )
        defined, _ = extract_names(f)
        assert defined == {"CONST", "top", "Cls"}
        assert "inner_local" not in defined
        assert "meth" not in defined, "methods are not importable names"

    def test_attribute_access_is_not_a_usage(self, tmp_path):
        """obj.data must not create a cross-file edge to whoever defines data."""
        f = tmp_path / "m.py"
        f.write_text("import x\nv = x.data\n")
        _, used = extract_names(f)
        assert "data" not in used

    def test_importfrom_symbols_are_usages(self, tmp_path):
        f = tmp_path / "m.py"
        f.write_text("from .other import Thing\n")
        _, used = extract_names(f)
        assert "Thing" in used

    def test_non_python_yields_nothing(self, tmp_path):
        f = tmp_path / "s.sh"
        f.write_text("echo hi\n")
        assert extract_names(f) == (set(), set())

    def test_syntax_error_yields_nothing(self, tmp_path):
        f = tmp_path / "bad.py"
        f.write_text("def (((\n")
        assert extract_names(f) == (set(), set())


class TestBuildGroups:
    """Roles partition; edges attach tests. No transitive closure."""

    def test_every_file_lands_in_exactly_one_group(self, tmp_path):
        by_file = {
            "src/engine.py": [_entity("src/engine.py") for _ in range(12)],
            "src/thin.py": [_entity("src/thin.py", "modified")],
            "tests/test_engine.py": [_entity("tests/test_engine.py")],
            "c.yaml": [_entity("c.yaml")],
        }
        groups = build_groups(by_file, {}, {}, {})
        placed = [m for g in groups for m in g.members]
        assert sorted(placed) == sorted(by_file)
        assert len(placed) == len(set(placed))

    def test_dependency_chain_does_not_collapse(self, tmp_path):
        """cli -> machine -> rulepack -> state is a real chain.

        Iteration 5 grouped by connectivity and put 9 of 14 files in one
        group. Roles must keep the chain apart.
        """
        by_file = {
            "src/rulepack.py": [_entity("src/rulepack.py") for _ in range(19)],
            "src/cli.py": [_entity("src/cli.py", "modified")],
            "src/machine.py": [_entity("src/machine.py", "modified")],
            "src/state.py": [_entity("src/state.py", "modified")],
        }
        edges = {
            "src/cli.py": {"src/machine.py"},
            "src/machine.py": {"src/rulepack.py", "src/state.py"},
        }
        groups = build_groups(by_file, edges, {}, {})
        engine = [g for g in groups if g.role == "engine"]
        assert len(engine) == 1
        assert engine[0].members == ["src/rulepack.py"]
        assert max(len(g.members) for g in groups) < len(by_file)

    def test_test_attaches_to_the_subject_it_shares_symbols_with(self):
        """Attachment is evidence (shared symbols), not a filename guess."""
        by_file = {
            "src/alpha.py": [_entity("src/alpha.py") for _ in range(12)],
            "src/beta.py": [_entity("src/beta.py") for _ in range(12)],
            "tests/test_misleading_name.py": [
                _entity("tests/test_misleading_name.py"),
            ],
        }
        file_defined = {
            "src/alpha.py": {"Alpha"},
            "src/beta.py": {"Beta"},
            "tests/test_misleading_name.py": set(),
        }
        file_used = {
            "src/alpha.py": set(),
            "src/beta.py": set(),
            # the name says nothing; the symbols say beta
            "tests/test_misleading_name.py": {"Beta"},
        }
        groups = build_groups(by_file, {}, file_defined, file_used)
        owner = {m: g for g in groups for m in g.members}
        assert owner["tests/test_misleading_name.py"] is owner["src/beta.py"]

    def test_every_group_gets_three_passes(self):
        """Receipts: each pass is a near-disjoint detector (30/37/37% recall).

        A group with fewer than three passes loses roughly two thirds of the
        findings for the files in it.
        """
        by_file = {
            "src/a.py": [_entity("src/a.py") for _ in range(12)],
            "src/b.py": [_entity("src/b.py", "modified")],
            "tests/test_a.py": [_entity("tests/test_a.py")],
        }
        groups = build_groups(by_file, {}, {}, {})
        reviewed = [g for g in groups if g.role not in ("config", "docs")]
        assert reviewed, "expected at least one reviewable group"
        for g in reviewed:
            assert g.passes == 3, (
                "group %s got %d passes; fewer than three cuts its recall "
                "to about a third" % (g.name, g.passes)
            )

    def test_deterministic_across_runs(self):
        by_file = {
            "src/a.py": [_entity("src/a.py") for _ in range(12)],
            "src/b.py": [_entity("src/b.py", "modified")],
            "tests/test_a.py": [_entity("tests/test_a.py")],
            "z.yaml": [_entity("z.yaml")],
        }
        first = [(g.name, g.members, g.passes) for g in build_groups(by_file, {}, {}, {})]
        for _ in range(5):
            again = [
                (g.name, g.members, g.passes)
                for g in build_groups(by_file, {}, {}, {})
            ]
            assert again == first


class TestCrossGroupEdges:
    """Split contracts must be reported, not silently dropped."""

    def test_edge_crossing_a_boundary_is_reported(self):
        groups = [
            Group(name="engine:a.py", role="engine",
                  members=["src/a.py"], passes=3),
            Group(name="integration", role="integration",
                  members=["src/b.py"], passes=3),
        ]
        edges = {"src/b.py": {"src/a.py"}}
        file_defined = {"src/a.py": {"Runner"}, "src/b.py": set()}
        file_used = {"src/b.py": {"Runner"}, "src/a.py": set()}
        xg = cross_group_edges(groups, edges, file_defined, file_used)
        assert len(xg) == 1
        assert xg[0]["symbols"] == ["Runner"]
        assert xg[0]["from_group"] == "integration"
        assert xg[0]["to_group"] == "engine:a.py"

    def test_edge_inside_one_group_is_not_reported(self):
        groups = [
            Group(name="engine:a.py", role="engine",
                  members=["src/a.py", "src/b.py"], passes=3),
        ]
        edges = {"src/b.py": {"src/a.py"}}
        file_defined = {"src/a.py": {"Runner"}, "src/b.py": set()}
        file_used = {"src/b.py": {"Runner"}, "src/a.py": set()}
        assert cross_group_edges(groups, edges, file_defined, file_used) == []


class TestGroupDiffDegradation:
    """Non-Python and empty input must degrade, never crash."""

    def test_non_python_diff_produces_role_groups_and_no_edges(self, tmp_path):
        changes = [
            _entity("docs/a.md", "modified"),
            _entity("hooks/b.sh", "modified"),
            _entity("c.json", "modified"),
        ]
        result = group_diff(changes, tmp_path)
        placed = [m for g in result.groups for m in g.members]
        assert sorted(placed) == ["c.json", "docs/a.md", "hooks/b.sh"]
        assert result.cross_group_edges == []

    def test_empty_changes_produces_no_groups(self, tmp_path):
        result = group_diff([], tmp_path)
        assert result.groups == []
        assert result.cross_group_edges == []


class TestNonPythonSourceIsStillReviewed:
    """A language this module cannot parse must not become unreviewable.

    Shell and other non-Python source get no def-use edges, which is correct
    -- but "no edges" must not decay into "no review". forge's own rules
    treat a service/init script that changes process lifecycle as
    logic-bearing, so silently allocating it zero passes would skip exactly
    the change that most needs looking at.
    """

    def test_shell_file_gets_review_passes(self):
        by_file = {"hooks/check.sh": [_entity("hooks/check.sh", "modified")]}
        groups = build_groups(by_file, {}, {}, {})
        owner = [g for g in groups if "hooks/check.sh" in g.members]
        assert len(owner) == 1
        assert owner[0].passes == 3, (
            "a shell source change got %d passes; unparseable is not the "
            "same as unreviewable" % owner[0].passes
        )

    def test_docs_and_config_still_get_no_passes(self):
        by_file = {
            "README.md": [_entity("README.md", "modified")],
            "c.yaml": [_entity("c.yaml", "modified")],
        }
        groups = build_groups(by_file, {}, {}, {})
        for g in groups:
            assert g.passes == 0, (
                "%s is deterministic-check territory, not LLM territory"
                % g.name
            )
