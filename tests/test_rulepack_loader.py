# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for the rulepack loader: load_pack / discover_packs / resolve_active_packs.

Covers AC-2 missing-rule detection, fail-soft error handling, built-in vs
repo-local discovery precedence, and strict allowlist resolution.
"""
from __future__ import annotations

import pytest

from code_forge.rulepack import (
    RuleMeta,
    RulepackManifest,
    discover_packs,
    load_pack,
    resolve_active_packs,
)

VALID_META = """\
name: testpack
version: "1.0"
source: https://example.com/testpack
rules:
  - id: rule-a
    title: Rule A
    category: security
    impact_tier: high
    languages: [python]
    source: https://example.com/rule-a
  - id: rule-b
    title: Rule B
    category: performance
    impact_tier: medium
    languages: [python]
    source: https://example.com/rule-b
"""

VALID_RULES = """\
rules:
  - id: rule-a
    pattern: foo(...)
    languages: [python]
    message: avoid foo
    severity: ERROR
  - id: rule-b
    pattern: bar(...)
    languages: [python]
    message: avoid bar
    severity: WARNING
"""


def _write_pack(tmp_path, meta=VALID_META, rules=VALID_RULES, name="testpack"):
    """Write a pack dir at tmp_path/<name> and return its path."""
    pack_dir = tmp_path / name
    pack_dir.mkdir(exist_ok=True)
    (pack_dir / "meta.yaml").write_text(meta, encoding="utf-8")
    (pack_dir / "rules.yaml").write_text(rules, encoding="utf-8")
    return pack_dir


class TestLoadPack:
    def test_valid_pack_loads(self, tmp_path):
        pack_dir = _write_pack(tmp_path)
        manifest = load_pack(pack_dir)

        assert isinstance(manifest, RulepackManifest)
        assert manifest.name == "testpack"
        assert manifest.load_error is None
        assert manifest.missing_rule_ids == set()
        assert [r.id for r in manifest.rules] == ["rule-a", "rule-b"]

        first = manifest.rules[0]
        assert isinstance(first, RuleMeta)
        assert first.title == "Rule A"
        assert first.category == "security"
        assert first.impact_tier == "high"
        assert first.languages == ["python"]
        assert first.source.startswith("https://")
        assert first.adjudication is False

    def test_ac2_missing_rule_detection(self, tmp_path):
        """AC-2: a meta rule absent from rules.yaml lands in missing_rule_ids."""
        rules = """\
rules:
  - id: rule-a
    pattern: foo(...)
    languages: [python]
    message: avoid foo
    severity: ERROR
"""
        pack_dir = _write_pack(tmp_path, rules=rules)
        manifest = load_pack(pack_dir)

        assert manifest.load_error is None
        assert manifest.missing_rule_ids == {"rule-b"}

    def test_bad_yaml_fail_soft(self, tmp_path):
        """Malformed YAML produces a manifest with load_error, not an exception."""
        pack_dir = _write_pack(tmp_path, meta="not: [valid: yaml")
        manifest = load_pack(pack_dir)

        assert isinstance(manifest, RulepackManifest)
        assert manifest.load_error is not None
        assert "YAML" in manifest.load_error
        # name falls back to the directory name for diagnostics
        assert manifest.name == "testpack"

    def test_missing_required_field_fail_soft(self, tmp_path):
        meta = """\
name: testpack
rules:
  - id: rule-a
    title: Rule A
    # missing: category / impact_tier / languages / source
"""
        pack_dir = _write_pack(tmp_path, meta=meta)
        manifest = load_pack(pack_dir)

        assert manifest.load_error is not None
        assert "missing required field" in manifest.load_error

    def test_duplicate_meta_rule_ids_fail_soft(self, tmp_path):
        meta = """\
name: testpack
rules:
  - id: rule-a
    title: Rule A
    category: security
    impact_tier: high
    languages: [python]
    source: x
  - id: rule-a
    title: Rule A again
    category: security
    impact_tier: high
    languages: [python]
    source: x
"""
        pack_dir = _write_pack(tmp_path, meta=meta)
        manifest = load_pack(pack_dir)

        assert manifest.load_error is not None
        assert "duplicate" in manifest.load_error

    def test_duplicate_rules_yaml_ids_fail_soft(self, tmp_path):
        rules = """\
rules:
  - id: rule-a
    pattern: foo(...)
    languages: [python]
    message: x
    severity: ERROR
  - id: rule-a
    pattern: bar(...)
    languages: [python]
    message: y
    severity: WARNING
"""
        pack_dir = _write_pack(tmp_path, rules=rules)
        manifest = load_pack(pack_dir)

        assert manifest.load_error is not None
        assert "duplicate rule IDs in rules.yaml" in manifest.load_error

    def test_missing_meta_yaml_fail_soft(self, tmp_path):
        pack_dir = _write_pack(tmp_path)
        (pack_dir / "meta.yaml").unlink()
        manifest = load_pack(pack_dir)

        assert manifest.load_error is not None
        assert "meta.yaml" in manifest.load_error


class TestDiscoverPacks:
    def test_discovers_repo_local_and_builtin(self, tmp_path):
        local = tmp_path / ".code-forge" / "packs" / "localpack"
        local.mkdir(parents=True)
        (local / "meta.yaml").write_text("name: localpack\nrules: []", encoding="utf-8")
        (local / "rules.yaml").write_text("rules: []", encoding="utf-8")

        packs = discover_packs(tmp_path)

        assert "localpack" in packs
        # Demo pack shipped in the source tree is visible from any repo root.
        assert "vercel-react" in packs

    def test_builtin_wins_on_name_collision(self, tmp_path):
        """Documented precedence: built-in pack shadows a repo-local one."""
        local = tmp_path / ".code-forge" / "packs" / "vercel-react"
        local.mkdir(parents=True)
        (local / "meta.yaml").write_text("name: override\nrules: []", encoding="utf-8")
        (local / "rules.yaml").write_text("rules: []", encoding="utf-8")

        packs = discover_packs(tmp_path)

        assert packs["vercel-react"] != local
        assert packs["vercel-react"].name == "vercel-react"


class TestResolveActivePacks:
    def _write_repo_local_pack(self, tmp_path, name="testpack"):
        pack_dir = tmp_path / ".code-forge" / "packs" / name
        pack_dir.mkdir(parents=True)
        (pack_dir / "meta.yaml").write_text(VALID_META, encoding="utf-8")
        (pack_dir / "rules.yaml").write_text(VALID_RULES, encoding="utf-8")
        return pack_dir

    def test_allowlist_resolves_loaded_manifests(self, tmp_path):
        self._write_repo_local_pack(tmp_path)
        manifests = resolve_active_packs({"rulepacks": ["testpack"]}, tmp_path)

        assert [m.name for m in manifests] == ["testpack"]
        assert manifests[0].load_error is None
        assert len(manifests[0].rules) == 2

    def test_unknown_pack_raises_loud_value_error(self, tmp_path):
        with pytest.raises(ValueError, match="Unknown rulepack"):
            resolve_active_packs({"rulepacks": ["does-not-exist"]}, tmp_path)

    def test_no_allowlist_returns_empty(self, tmp_path):
        assert resolve_active_packs({}, tmp_path) == []
        assert resolve_active_packs({"rulepacks": []}, tmp_path) == []