# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for user_config shared module and cli._merge_user_into."""
from __future__ import annotations

from unittest.mock import patch

from code_forge.user_config import merge_backends


class TestMergeBackends:
    """merge_backends: project wins by name, project first in order."""

    def test_project_wins_by_name(self):
        project = {"shared": {"model": "proj-model"}}
        user = {"shared": {"model": "user-model"}, "extra": {"model": "u"}}
        merged = merge_backends(project, user)
        assert merged["shared"]["model"] == "proj-model"
        assert "extra" in merged

    def test_order_project_first(self):
        project = {"b-proj": {}}
        user = {"a-user": {}}
        merged = merge_backends(project, user)
        assert list(merged.keys()) == ["b-proj", "a-user"]

    def test_empty_user_returns_project(self):
        project = {"p": {"model": "m"}}
        merged = merge_backends(project, {})
        assert merged == project

    def test_empty_project_returns_user(self):
        user = {"u": {"model": "m"}}
        merged = merge_backends({}, user)
        assert merged == user

    def test_both_empty(self):
        assert merge_backends({}, {}) == {}


class TestMergeUserInto:
    """cli._merge_user_into: BackendConfig-level merge for CLI path."""

    def test_user_only_backend_appended(self):
        from code_forge.backend import BackendConfig
        from code_forge.cli import _merge_user_into

        proj_cfg = BackendConfig(
            name="proj", type="api", model="m", format="openai",
            base_url="http://x", api_key_env="K",
        )
        user_raw = {"user-back": {
            "type": "api", "model": "u", "format": "openai",
            "base_url": "http://y", "api_key_env": "UK",
        }}
        gate_data = {"backends": {"proj": {"type": "api"}}}

        with patch("code_forge.user_config.load_user_backends", return_value=user_raw):
            result = _merge_user_into([proj_cfg], gate_data)

        names = [c.name for c in result]
        assert names == ["proj", "user-back"]

    def test_project_wins_by_name(self):
        from code_forge.backend import BackendConfig
        from code_forge.cli import _merge_user_into

        proj_cfg = BackendConfig(
            name="shared", type="api", model="proj-model", format="openai",
            base_url="http://x", api_key_env="K",
        )
        user_raw = {"shared": {
            "type": "api", "model": "user-model", "format": "openai",
            "base_url": "http://y", "api_key_env": "UK",
        }}
        gate_data = {"backends": {"shared": {"type": "api"}}}

        with patch("code_forge.user_config.load_user_backends", return_value=user_raw):
            result = _merge_user_into([proj_cfg], gate_data)

        assert len(result) == 1
        assert result[0].model == "proj-model"

    def test_empty_user_no_change(self):
        from code_forge.backend import BackendConfig
        from code_forge.cli import _merge_user_into

        proj_cfg = BackendConfig(
            name="p", type="api", model="m", format="openai",
            base_url="http://x", api_key_env="K",
        )
        with patch("code_forge.user_config.load_user_backends", return_value={}):
            result = _merge_user_into([proj_cfg], {"backends": {"p": {}}})

        assert len(result) == 1
        assert result[0].name == "p"

    def test_malformed_user_config_no_crash(self):
        from code_forge.backend import BackendConfig
        from code_forge.cli import _merge_user_into

        proj_cfg = BackendConfig(
            name="p", type="api", model="m", format="openai",
            base_url="http://x", api_key_env="K",
        )
        # "bad" entry has no type field -- load_backend_configs will raise
        user_raw = {"bad": {"not_a_valid": "config"}}

        with patch("code_forge.user_config.load_user_backends", return_value=user_raw):
            result = _merge_user_into([proj_cfg], {"backends": {"p": {}}})

        # Should gracefully return project-only cfgs
        assert len(result) == 1
        assert result[0].name == "p"

    def test_non_dict_project_backends_still_merges_user(self):
        """gate_data backends is non-dict -> guard resets to {},
        user backends still appended."""
        from code_forge.backend import BackendConfig
        from code_forge.cli import _merge_user_into

        proj_cfg = BackendConfig(
            name="p", type="api", model="m", format="openai",
            base_url="http://x", api_key_env="K",
        )
        user_raw = {"user-back": {
            "type": "api", "model": "u", "format": "openai",
            "base_url": "http://y", "api_key_env": "UK",
        }}
        # backends is a list, not a dict -- triggers :166 guard
        gate_data = {"backends": ["not", "a", "dict"]}

        with patch("code_forge.user_config.load_user_backends", return_value=user_raw):
            result = _merge_user_into([proj_cfg], gate_data)

        names = [c.name for c in result]
        assert "user-back" in names

    def test_user_backend_probe_failure_falls_back_to_project(self):
        """probe_backend_with_fallback: a failing user backend falls
        back to the first reachable project backend."""
        from code_forge.backend import BackendConfig, ProbeResult
        from code_forge.cli import probe_backend_with_fallback

        user_cfg = BackendConfig(
            name="stray", type="api", model="u", format="openai",
            base_url="http://bad", api_key_env="MISSING",
        )
        proj_cfg = BackendConfig(
            name="proj", type="api", model="m", format="openai",
            base_url="http://x", api_key_env="K",
        )

        def probe_results(backend, env=None):
            if backend.name == "stray":
                return ProbeResult(ok=False, error="unreachable")
            return ProbeResult(ok=True)

        with patch("code_forge.backend.probe_backend",
                   side_effect=probe_results) as probe_spy:
            result = probe_backend_with_fallback(
                user_cfg, [proj_cfg], project_names={"proj"},
            )

        assert result.ok
        assert probe_spy.call_count == 2

    def test_project_backend_failure_does_not_fall_back(self):
        """A deliberately configured project backend that fails the
        probe must not be silently swapped for a sibling."""
        from code_forge.backend import BackendConfig, ProbeResult
        from code_forge.cli import probe_backend_with_fallback

        proj_cfg = BackendConfig(
            name="proj", type="api", model="m", format="openai",
            base_url="http://x", api_key_env="K",
        )
        other_cfg = BackendConfig(
            name="other", type="api", model="o", format="openai",
            base_url="http://y", api_key_env="K2",
        )

        def probe_results(backend, env=None):
            if backend.name == "proj":
                return ProbeResult(ok=False, error="unreachable")
            return ProbeResult(ok=True)

        with patch("code_forge.backend.probe_backend",
                   side_effect=probe_results) as probe_spy:
            result = probe_backend_with_fallback(
                proj_cfg, [proj_cfg, other_cfg],
                project_names={"proj", "other"},
            )

        assert not result.ok
        assert probe_spy.call_count == 1

    def test_all_backends_down_returns_original_failure(self):
        """User backend fails and every project backend fails: the
        original failure surfaces, nothing is invented."""
        from code_forge.backend import BackendConfig, ProbeResult
        from code_forge.cli import probe_backend_with_fallback

        user_cfg = BackendConfig(
            name="stray", type="api", model="u", format="openai",
            base_url="http://bad", api_key_env="MISSING",
        )
        proj_cfg = BackendConfig(
            name="proj", type="api", model="m", format="openai",
            base_url="http://x", api_key_env="K",
        )

        def probe_results(backend, env=None):
            return ProbeResult(
                ok=False, error="down: %s" % backend.name,
            )

        with patch("code_forge.backend.probe_backend",
                   side_effect=probe_results):
            result = probe_backend_with_fallback(
                user_cfg, [proj_cfg], project_names={"proj"},
            )

        assert not result.ok
        assert "down: stray" in result.error

    def test_no_project_backends_returns_original_failure(self):
        """No project backend to fall back to: the failure stands."""
        from code_forge.backend import BackendConfig, ProbeResult
        from code_forge.cli import probe_backend_with_fallback

        user_cfg = BackendConfig(
            name="stray", type="api", model="u", format="openai",
            base_url="http://bad", api_key_env="MISSING",
        )

        with patch("code_forge.backend.probe_backend",
                   return_value=ProbeResult(ok=False, error="down")) as probe_spy:
            result = probe_backend_with_fallback(
                user_cfg, [], project_names=set(),
            )

        assert not result.ok
        assert probe_spy.call_count == 1

    def test_review_path_applies_fallback_too(self):
        """The review path (not just the outlet probe) must fall back
        to a project backend when the resolved user backend is down --
        otherwise the probe passes and the review still fails."""
        from code_forge.backend import BackendConfig, ProbeResult
        from code_forge.cli import resolve_backend_with_fallback

        user_cfg = BackendConfig(
            name="stray", type="api", model="u", format="openai",
            base_url="http://bad", api_key_env="MISSING",
        )
        proj_cfg = BackendConfig(
            name="proj", type="api", model="m", format="openai",
            base_url="http://x", api_key_env="K",
        )

        def probe_results(backend, env=None):
            if backend.name == "stray":
                return ProbeResult(ok=False, error="unreachable")
            return ProbeResult(ok=True)

        with patch("code_forge.backend.probe_backend",
                   side_effect=probe_results):
            chosen = resolve_backend_with_fallback(
                user_cfg, [proj_cfg], project_names={"proj"},
            )

        assert chosen is proj_cfg

    def test_review_path_keeps_project_backend_even_when_down(self):
        """A deliberately configured project backend is never
        silently swapped for a sibling."""
        from code_forge.backend import BackendConfig, ProbeResult
        from code_forge.cli import resolve_backend_with_fallback

        proj_cfg = BackendConfig(
            name="proj", type="api", model="m", format="openai",
            base_url="http://x", api_key_env="K",
        )
        other_cfg = BackendConfig(
            name="other", type="api", model="o", format="openai",
            base_url="http://y", api_key_env="K2",
        )

        with patch("code_forge.backend.probe_backend",
                   return_value=ProbeResult(ok=False, error="down")):
            chosen = resolve_backend_with_fallback(
                proj_cfg, [proj_cfg, other_cfg],
                project_names={"proj", "other"},
            )

        assert chosen is proj_cfg

    def test_non_dict_backends_block_names_no_project(self):
        """A list-valued backends block must not have its elements
        misread as project backend names."""
        from code_forge.cli import _project_backend_names

        assert _project_backend_names({"backends": ["proj"]}) == set()
        assert _project_backend_names({"backends": None}) == set()
        assert _project_backend_names({}) == set()

    def test_dict_backends_block_yields_names(self):
        from code_forge.cli import _project_backend_names

        assert _project_backend_names(
            {"backends": {"proj": {"type": "api"}}},
        ) == {"proj"}
