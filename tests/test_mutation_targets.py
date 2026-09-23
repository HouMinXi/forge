# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for the mutation_engines.targets module.

Covers: declaration loading, duplicate rejection, oversized rejection,
selection accounting on rename, delete, addition, and overlapping targets.
"""
from __future__ import annotations

import pytest

from code_forge.mutation_engines.schemas import MAX_TARGETS
from code_forge.mutation_engines.targets import (
    ChangedPath,
    DeclarationError,
    _glob_to_regex,
    load_targets,
    select_targets,
)


# -- Helpers ------------------------------------------------------------------

def _valid_budget():
    return {
        "total_seconds": 600,
        "baseline_seconds": 120,
        "mutant_seconds": 60,
        "concurrency": 2,
        "memory_mb": 4096,
        "processes": 128,
        "workspace_mb": 1024,
        "evidence_mb": 64,
    }


def _valid_target(tid="python-core", adapter="mutmut", **overrides):
    d = {
        "id": tid,
        "adapter": adapter,
        "root": ".",
        "sources": ["src/**/*.py"],
        "tests": ["tests/**"],
        "inputs": ["pyproject.toml"],
        "oracle": "pytest",
        "command": ["python", "-m", "pytest"],
        "execution_profile": "linux-isolated",
        "environment": "python-core-v1",
        "budget": _valid_budget(),
    }
    d.update(overrides)
    return d


def _config(*targets):
    return {
        "mutation": {
            "schema_version": 1,
            "targets": list(targets),
        }
    }


# -- load_targets -------------------------------------------------------------

class TestLoadTargets:
    def test_load_single_target(self):
        cfg = _config(_valid_target())
        targets = load_targets(cfg)
        assert len(targets) == 1
        assert targets[0].id == "python-core"

    def test_load_multiple_targets(self):
        cfg = _config(
            _valid_target("python-core"),
            _valid_target("go-core", adapter="gremlins",
                          sources=["pkg/**/*.go"],
                          tests=["pkg/**/*_test.go"],
                          inputs=["go.mod", "go.sum"],
                          oracle="go-test",
                          command=["go", "test", "./pkg/..."],
                          environment="go-core-v1"),
        )
        targets = load_targets(cfg)
        assert len(targets) == 2

    def test_reject_no_mutation_block(self):
        with pytest.raises(DeclarationError, match="no 'mutation' block"):
            load_targets({})

    def test_reject_non_dict_mutation(self):
        with pytest.raises(DeclarationError, match="must be a mapping"):
            load_targets({"mutation": "not-a-dict"})

    def test_reject_missing_schema_version(self):
        with pytest.raises(DeclarationError, match="schema_version must be 1"):
            load_targets({"mutation": {"targets": []}})

    def test_reject_wrong_schema_version(self):
        with pytest.raises(DeclarationError, match="schema_version must be 1"):
            load_targets({"mutation": {"schema_version": 2, "targets": []}})

    def test_reject_unknown_mutation_keys(self):
        with pytest.raises(DeclarationError, match="unknown keys"):
            load_targets({
                "mutation": {
                    "schema_version": 1,
                    "targets": [],
                    "extra_key": True,
                }
            })

    def test_reject_no_targets_list(self):
        with pytest.raises(DeclarationError, match="no 'targets' list"):
            load_targets({"mutation": {"schema_version": 1}})

    def test_reject_non_list_targets(self):
        with pytest.raises(DeclarationError, match="must be a list"):
            load_targets({
                "mutation": {"schema_version": 1, "targets": "not-a-list"}
            })

    def test_reject_duplicate_target_ids(self):
        cfg = _config(
            _valid_target("python-core"),
            _valid_target("python-core"),
        )
        with pytest.raises(DeclarationError, match="duplicate target id"):
            load_targets(cfg)

    def test_reject_oversized_targets(self):
        """Exceeding MAX_TARGETS raises DeclarationError."""
        targets = [
            _valid_target("t%04d" % i) for i in range(MAX_TARGETS + 1)
        ]
        cfg = {
            "mutation": {
                "schema_version": 1,
                "targets": targets,
            }
        }
        with pytest.raises(DeclarationError, match="too many targets"):
            load_targets(cfg)

    def test_reject_malformed_target_entry(self):
        cfg = _config({"id": "bad"})  # missing required keys
        with pytest.raises(DeclarationError, match="target\\[0\\]"):
            load_targets(cfg)


# -- select_targets -----------------------------------------------------------

class TestSelectTargets:
    def _py_target(self):
        cfg = _config(_valid_target())
        return load_targets(cfg)

    def _multi_targets(self):
        cfg = _config(
            _valid_target("python-core",
                          sources=["src/**/*.py"],
                          tests=["tests/**"],
                          inputs=["pyproject.toml"]),
            _valid_target("go-core",
                          adapter="gremlins",
                          sources=["pkg/**/*.go"],
                          tests=["pkg/**/*_test.go"],
                          inputs=["go.mod"],
                          oracle="go-test",
                          command=["go", "test"],
                          environment="go-core-v1"),
        )
        return load_targets(cfg)

    def test_source_change_selects_target(self):
        targets = self._py_target()
        changes = [ChangedPath(old_path=None, new_path="src/foo/bar.py")]
        result = select_targets(targets, changes)
        assert len(result.targets) == 1
        assert result.targets[0].target_id == "python-core"

    def test_test_change_selects_target(self):
        targets = self._py_target()
        changes = [ChangedPath(old_path=None, new_path="tests/test_foo.py")]
        result = select_targets(targets, changes)
        assert len(result.targets) == 1

    def test_input_change_selects_target(self):
        targets = self._py_target()
        changes = [ChangedPath(old_path=None, new_path="pyproject.toml")]
        result = select_targets(targets, changes)
        assert len(result.targets) == 1

    def test_unmatched_path_recorded(self):
        targets = self._py_target()
        changes = [ChangedPath(old_path=None, new_path="README.md")]
        result = select_targets(targets, changes)
        assert len(result.targets) == 0
        assert "README.md" in result.unmatched_paths

    def test_deletion_old_path_selects(self):
        """Deletion: old_path matches, new_path is None."""
        targets = self._py_target()
        changes = [ChangedPath(old_path="src/old_module.py", new_path=None)]
        result = select_targets(targets, changes)
        assert len(result.targets) == 1
        assert result.targets[0].target_id == "python-core"

    def test_rename_selects_via_old_path(self):
        """Rename: old_path matches in before-targets map."""
        targets = self._py_target()
        changes = [
            ChangedPath(old_path="src/old_name.py", new_path="src/new_name.py")
        ]
        result = select_targets(targets, changes)
        assert len(result.targets) == 1

    def test_rename_selects_via_after_targets_for_removed_target(self):
        """When a target is removed (present in before, absent in after),
        a deletion of its source file still selects via the before map."""
        before = self._py_target()
        # after: target removed entirely
        targets_after = []  # type: ignore[var-annotated]

        changes = [ChangedPath(old_path="src/module.py", new_path=None)]
        result = select_targets(
            targets_after, changes, before_targets=before
        )
        assert len(result.targets) == 1
        assert result.targets[0].target_id == "python-core"

    def test_declaration_changed_selects_all(self):
        targets = self._multi_targets()
        changes = []  # type: ignore[var-annotated]
        result = select_targets(
            targets, changes, declaration_changed=True
        )
        ids = {t.target_id for t in result.targets}
        assert ids == {"python-core", "go-core"}

    def test_policy_changed_selects_all(self):
        targets = self._multi_targets()
        changes = []  # type: ignore[var-annotated]
        result = select_targets(targets, changes, policy_changed=True)
        ids = {t.target_id for t in result.targets}
        assert ids == {"python-core", "go-core"}

    def test_multi_target_selective(self):
        targets = self._multi_targets()
        changes = [ChangedPath(old_path=None, new_path="pkg/main.go")]
        result = select_targets(targets, changes)
        ids = {t.target_id for t in result.targets}
        assert ids == {"go-core"}
        assert "python-core" not in ids

    def test_overlapping_targets(self):
        """Two targets that cover the same file -- both must be selected."""
        cfg = _config(
            _valid_target("target-a", sources=["shared/**/*.py"]),
            _valid_target("target-b", sources=["shared/**/*.py"],
                          environment="b-v1"),
        )
        targets = load_targets(cfg)
        changes = [ChangedPath(old_path=None, new_path="shared/module.py")]
        result = select_targets(targets, changes)
        ids = {t.target_id for t in result.targets}
        assert ids == {"target-a", "target-b"}

    def test_corpus_path_selects_target(self):
        cfg = _config(
            _valid_target(
                "shell-config",
                adapter="patch-corpus",
                corpus=".code-forge/corpora/shell-config.json",
                sources=["scripts/**/*.sh"],
                tests=["tests/test_scripts.py"],
                oracle="pytest",
            ),
        )
        targets = load_targets(cfg)
        changes = [
            ChangedPath(
                old_path=None,
                new_path=".code-forge/corpora/shell-config.json",
            )
        ]
        result = select_targets(targets, changes)
        assert len(result.targets) == 1
        assert result.targets[0].target_id == "shell-config"

    def test_engine_config_path_selects_target(self):
        cfg = _config(
            _valid_target(engine_config="pyproject.toml"),
        )
        targets = load_targets(cfg)
        changes = [ChangedPath(old_path=None, new_path="pyproject.toml")]
        result = select_targets(targets, changes)
        assert len(result.targets) == 1

    def test_changed_path_requires_at_least_one(self):
        with pytest.raises(ValueError, match="at least one path"):
            ChangedPath(old_path=None, new_path=None)

    def test_rename_both_maps_checked(self):
        """Rename: both old (before-map) and new (after-map) are checked."""
        before_targets = load_targets(_config(
            _valid_target("old-target", sources=["old_dir/**/*.py"]),
        ))
        after_targets = load_targets(_config(
            _valid_target("new-target", sources=["new_dir/**/*.py"]),
        ))
        changes = [
            ChangedPath(
                old_path="old_dir/module.py",
                new_path="new_dir/module.py",
            )
        ]
        result = select_targets(
            after_targets, changes, before_targets=before_targets
        )
        ids = {t.target_id for t in result.targets}
        # Old file matched old-target in before-map
        # New file matched new-target in after-map
        assert "old-target" in ids
        assert "new-target" in ids

    def test_granularity_source_only_is_file(self):
        targets = self._py_target()
        changes = [ChangedPath(old_path=None, new_path="src/foo.py")]
        result = select_targets(targets, changes)
        assert result.targets[0].granularity == "file"

    def test_granularity_test_change_is_full(self):
        targets = self._py_target()
        changes = [ChangedPath(old_path=None, new_path="tests/test_new.py")]
        result = select_targets(targets, changes)
        assert result.targets[0].granularity == "full"


class TestDeclarationPolicyReasons:
    def test_both_flags_record_both_reasons(self):
        targets = load_targets(_config(_valid_target()))
        result = select_targets(
            targets, [], declaration_changed=True, policy_changed=True
        )
        reasons = result.targets[0].reasons
        assert any("declaration changed" in r for r in reasons)
        assert any("policy changed" in r for r in reasons)


class TestRenameFileAttribution:
    def test_files_attributed_from_both_maps(self):
        before = load_targets(_config(
            _valid_target("core", sources=["old_src/**/*.py"]),
        ))
        after = load_targets(_config(
            _valid_target("core", sources=["new_src/**/*.py"]),
        ))
        changes = [
            ChangedPath(old_path="old_src/a.py", new_path="new_src/a.py")
        ]
        result = select_targets(after, changes, before_targets=before)
        sel = result.targets[0]
        assert sel.target_id == "core"
        assert "old_src/a.py" in sel.files
        assert "new_src/a.py" in sel.files


class TestGlobCharacterClasses:
    def test_bang_negates_class(self):
        rx = _glob_to_regex("src/[!a]*.py")
        assert rx.match("src/b.py")
        assert rx.match("src/!.py")
        assert not rx.match("src/a.py")

    def test_caret_is_literal_in_class(self):
        rx = _glob_to_regex("src/[^a]*.py")
        assert rx.match("src/a.py")
        assert rx.match("src/^.py")
        assert not rx.match("src/b.py")

    def test_empty_class_is_literal_bracket(self):
        rx = _glob_to_regex("a[]b")
        assert rx.match("a[]b")

    def test_class_with_literal_close_bracket(self):
        rx = _glob_to_regex("a[]x]b")
        assert rx.match("a]b")
        assert rx.match("axb")
