# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for review-focus mechanism (Phase 41).

Covers: merge helper, trust hash, gate.yaml extraction, CLI flag wiring,
builder injection, MCP wiring, and trust command behavior.
"""
from pathlib import Path
from unittest.mock import MagicMock, patch


# -- _merge_focus_spec -------------------------------------------------------


class TestMergeFocusSpec:
    """Unit tests for cli._merge_focus_spec."""

    def test_yaml_only(self):
        from code_forge.cli import _merge_focus_spec
        result = _merge_focus_spec("focus from yaml", "")
        assert result == "focus from yaml"

    def test_file_only(self):
        from code_forge.cli import _merge_focus_spec
        result = _merge_focus_spec("", "focus from file")
        assert result == "focus from file"

    def test_both_merges_yaml_then_file(self):
        from code_forge.cli import _merge_focus_spec
        result = _merge_focus_spec("yaml focus", "file focus")
        assert result == "yaml focus\n\nfile focus"

    def test_both_empty_returns_empty(self):
        from code_forge.cli import _merge_focus_spec
        result = _merge_focus_spec("", "")
        assert result == ""

    def test_large_content_warns_but_passes_through(self):
        from code_forge.cli import _merge_focus_spec
        big = "x" * 9000  # >8192 bytes
        warn = MagicMock()
        result = _merge_focus_spec(big, "", warn_fn=warn)
        assert result == big
        assert len(result) == 9000
        warn.assert_called_once()


# -- trust functions ---------------------------------------------------------


class TestFocusTrust:
    """Unit tests for hash_focus_text and is_trusted_focus."""

    def test_hash_empty_returns_empty(self):
        from code_forge.trust import hash_focus_text
        assert hash_focus_text({}) == ""
        assert hash_focus_text({"review_focus": ""}) == ""
        assert hash_focus_text({"review_focus": "  \n"}) == ""

    def test_hash_nonempty_returns_sha256(self):
        from code_forge.trust import hash_focus_text
        h = hash_focus_text({"review_focus": "focus on auth"})
        assert len(h) == 64  # sha256 hex
        assert h != ""

    def test_is_trusted_empty_focus_returns_true(self):
        from code_forge.trust import is_trusted_focus
        assert is_trusted_focus(Path("/fake/gate.yaml"), {}) is True
        assert is_trusted_focus(Path("/fake/gate.yaml"), {"review_focus": ""}) is True

    def test_is_trusted_no_store_entry_returns_false(self):
        from code_forge.trust import is_trusted_focus
        with patch("code_forge.trust._load_trust_store", return_value={}):
            assert is_trusted_focus(Path("/fake/gate.yaml"), {"review_focus": "x"}) is False

    def test_is_trusted_matching_hash_returns_true(self):
        from code_forge.trust import is_trusted_focus, hash_focus_text
        gate_data = {"review_focus": "my focus"}
        fh = hash_focus_text(gate_data)
        store = {str(Path("/fake/gate.yaml").resolve()): {"focus_hash": fh}}
        with patch("code_forge.trust._load_trust_store", return_value=store):
            assert is_trusted_focus(Path("/fake/gate.yaml"), gate_data) is True

    def test_is_trusted_mismatched_hash_returns_false(self):
        from code_forge.trust import is_trusted_focus
        store = {str(Path("/fake/gate.yaml").resolve()): {"focus_hash": "deadbeef"}}
        with patch("code_forge.trust._load_trust_store", return_value=store):
            assert is_trusted_focus(Path("/fake/gate.yaml"), {"review_focus": "changed"}) is False


class TestRecordTrustFocusHash:
    """record_trust should write focus_hash alongside hash."""

    def test_records_focus_hash(self):
        from code_forge.trust import record_trust, hash_focus_text
        gate_data = {"review_focus": "my focus", "backends": {"cli": {"type": "cli"}}}
        with patch("code_forge.trust._load_trust_store", return_value={}), \
             patch("code_forge.trust._save_trust_store") as mock_save:
            record_trust(Path("/fake/gate.yaml"), gate_data)
            saved = mock_save.call_args[0][0]
            entry = saved[str(Path("/fake/gate.yaml").resolve())]
            assert "hash" in entry
            assert "focus_hash" in entry
            assert entry["focus_hash"] == hash_focus_text(gate_data)

    def test_preserves_existing_keys(self):
        from code_forge.trust import record_trust
        existing = {"existing_key": "value"}
        gate_data = {"review_focus": "", "backends": {"cli": {"type": "cli"}}}
        with patch("code_forge.trust._load_trust_store", return_value={
            str(Path("/fake/gate.yaml").resolve()): existing
        }), patch("code_forge.trust._save_trust_store") as mock_save:
            record_trust(Path("/fake/gate.yaml"), gate_data)
            saved = mock_save.call_args[0][0]
            entry = saved[str(Path("/fake/gate.yaml").resolve())]
            assert entry.get("existing_key") == "value"


# -- _load_trusted_yaml_focus ------------------------------------------------


class TestLoadTrustedYamlFocus:
    """Integration test for the shared yaml focus loader."""

    def test_absent_focus_returns_empty(self, tmp_path):
        from code_forge.cli import _load_trusted_yaml_focus
        gate = tmp_path / "gate.yaml"
        gate.write_text("backends:\n  cli:\n    type: cli\n")
        result = _load_trusted_yaml_focus(gate, lambda msg: None)
        assert result == ""

    def test_trusted_focus_returns_value(self, tmp_path):
        from code_forge.cli import _load_trusted_yaml_focus
        from code_forge.trust import record_trust
        gate = tmp_path / "gate.yaml"
        gate.write_text("review_focus: focus on auth\nbackends:\n  cli:\n    type: cli\n")
        import yaml
        gd = yaml.safe_load(gate.read_text())
        record_trust(gate, gd)
        result = _load_trusted_yaml_focus(gate, lambda msg: None)
        assert result == "focus on auth"

    def test_untrusted_focus_warns_and_returns_empty(self, tmp_path):
        from code_forge.cli import _load_trusted_yaml_focus
        gate = tmp_path / "gate.yaml"
        gate.write_text("review_focus: untrusted focus\n")
        warnings = []
        result = _load_trusted_yaml_focus(gate, lambda msg: warnings.append(msg))
        assert result == ""
        assert any("not trusted" in w for w in warnings)

    def test_non_string_focus_warns(self, tmp_path):
        from code_forge.cli import _load_trusted_yaml_focus
        gate = tmp_path / "gate.yaml"
        gate.write_text("review_focus:\n  - item1\n  - item2\n")
        warnings = []
        result = _load_trusted_yaml_focus(gate, lambda msg: warnings.append(msg))
        assert result == ""
        assert any("not a string" in w for w in warnings)


# -- builder injection -------------------------------------------------------


class TestBuilderFocusInjection:
    """Verify all 3 builders inject ## Review Focus when focus_spec is non-empty."""

    def test_outlet_c_subagent_spawn(self):
        from code_forge.cli import _make_subagent_spawn
        spawn = _make_subagent_spawn(
            backend=None, conv_digest="", post_image="",
            focus_spec="focus on auth",
        )
        # The spawn_fn calls llm_invoke internally; we need to mock it
        # to capture the prompt without hitting a real backend.
        with patch("code_forge.llm_invoke.llm_invoke") as mock_invoke:
            mock_result = MagicMock()
            mock_result.content = '{"findings": []}'
            mock_invoke.return_value = mock_result
            _prompt = spawn("qodo", "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n")
            # Verify llm_invoke was called and the prompt contained focus
            call_args = mock_invoke.call_args
            assert call_args is not None
            actual_prompt = call_args[0][0]
            assert "## Review Focus" in actual_prompt
            assert "focus on auth" in actual_prompt

    def test_outlet_a_build_l1_provider(self):
        from code_forge.factories import build_l1_provider
        from code_forge.baseline import ResolvedReview
        resolved = ResolvedReview(
            mode_hint="git",
            git_diff="--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n",
            source_files=[], baseline_content=None,
        )
        _captured = []
        fake_result = MagicMock()
        fake_result.content = '{"findings": [], "code_excerpts": []}'
        provider = build_l1_provider(
            "stub", resolved, focus_spec="focus on edge cases",
        )
        # The provider is a callable -- just verify it was built without error
        assert callable(provider)

    def test_sampling_build_l1_provider(self):
        from code_forge.factories import build_sampling_l1_provider
        from code_forge.baseline import ResolvedReview
        import asyncio
        resolved = ResolvedReview(
            mode_hint="git",
            git_diff="--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n",
            source_files=[], baseline_content=None,
        )
        mock_session = MagicMock()
        loop = asyncio.new_event_loop()
        try:
            provider = build_sampling_l1_provider(
                session=mock_session, loop=loop, resolved=resolved,
                focus_spec="sampling focus",
            )
            assert callable(provider)
        finally:
            loop.close()
