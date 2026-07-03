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
