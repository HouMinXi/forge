"""Tests for the CLI fast-fail credential guard.

Exercises _check_backend_credentials directly -- no full CLI entry
point mocking needed because the guard runs before the review pipeline.
"""

from __future__ import annotations

import pytest

from code_forge.backend import BackendConfig
from code_forge.cli import _check_backend_credentials
from code_forge.errors import CliError


# -- api_key_file tests --------------------------------------------------

class TestApiKeyFileGuard:
    """api_key_file backends: missing or empty file raises CliError."""

    def test_missing_file_raises(self, tmp_path):
        """Non-existent api_key_file path raises CliError 'not found'."""
        backend = BackendConfig(
            name="test",
            type="api",
            model="m",
            format="openai",
            api_key_file=str(tmp_path / "missing.key"),
        )
        with pytest.raises(CliError, match="not found"):
            _check_backend_credentials(backend)

    def test_empty_file_raises(self, tmp_path):
        """Empty api_key_file raises CliError 'empty'."""
        key_file = tmp_path / "empty.key"
        key_file.write_text("", encoding="utf-8")
        backend = BackendConfig(
            name="test",
            type="api",
            model="m",
            format="openai",
            api_key_file=str(key_file),
        )
        with pytest.raises(CliError, match="empty"):
            _check_backend_credentials(backend)

    def test_nonempty_file_passes(self, tmp_path):
        """Non-empty api_key_file does NOT raise CliError."""
        key_file = tmp_path / "good.key"
        key_file.write_text("sk-abc123\n", encoding="utf-8")
        backend = BackendConfig(
            name="test",
            type="api",
            model="m",
            format="openai",
            api_key_file=str(key_file),
        )
        # Must not raise
        _check_backend_credentials(backend)


# -- vertex credentials_path tests ---------------------------------------

class TestVertexCredentialsPathGuard:
    """Vertex backends: missing credentials_path raises CliError."""

    def test_missing_credentials_path_raises(self, tmp_path):
        """Vertex backend with missing credentials_path raises 'not found'."""
        backend = BackendConfig(
            name="test-vertex",
            type="api",
            model="gemini-pro",
            format="vertex",
            project_id="my-project",
            credentials_path=str(tmp_path / "missing.json"),
        )
        with pytest.raises(CliError, match="not found"):
            _check_backend_credentials(backend)

    def test_no_credentials_path_deferred(self):
        """Vertex backend without credentials_path does NOT raise at guard.

        Deferred to ADC/runtime -- the guard only checks when explicitly set.
        """
        backend = BackendConfig(
            name="test-vertex",
            type="api",
            model="gemini-pro",
            format="vertex",
            project_id="my-project",
        )
        # Must not raise
        _check_backend_credentials(backend)


# -- existing api_key_env guard (unchanged) -------------------------------

class TestApiKeyEnvGuard:
    """Existing api_key_env guard still works."""

    def test_missing_env_var_raises(self, monkeypatch):
        """Missing api_key_env value raises CliError 'is not set'."""
        monkeypatch.delenv("TEST_MISSING_KEY_XYZ", raising=False)
        backend = BackendConfig(
            name="test",
            type="api",
            model="m",
            format="openai",
            api_key_env="TEST_MISSING_KEY_XYZ",
        )
        with pytest.raises(CliError, match="is not set"):
            _check_backend_credentials(backend)
