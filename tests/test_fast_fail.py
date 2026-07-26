"""Tests for the CLI fast-fail credential guard.

Exercises _check_backend_credentials directly -- no full CLI entry
point mocking needed because the guard runs before the review pipeline.
"""

from __future__ import annotations

import os

import pytest

from code_forge.backend import BackendConfig, credential_error
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
        """Non-empty api_key_file with mode 0600 does NOT raise CliError."""
        key_file = tmp_path / "good.key"
        key_file.write_text("sk-abc123\n", encoding="utf-8")
        key_file.chmod(0o600)
        backend = BackendConfig(
            name="test",
            type="api",
            model="m",
            format="openai",
            api_key_file=str(key_file),
        )
        # Must not raise
        _check_backend_credentials(backend)

    @pytest.mark.skipif(
        os.getuid() == 0,
        reason="root bypasses file permissions, chmod 0o077 check ineffective",
    )
    def test_world_readable_file_raises(self, tmp_path):
        """api_key_file with group/world-readable perms raises 'chmod 600'."""
        key_file = tmp_path / "perms.key"
        key_file.write_text("sk-abc123\n", encoding="utf-8")
        key_file.chmod(0o644)
        backend = BackendConfig(
            name="test",
            type="api",
            model="m",
            format="openai",
            api_key_file=str(key_file),
        )
        try:
            with pytest.raises(CliError, match="chmod 600"):
                _check_backend_credentials(backend)
        finally:
            key_file.chmod(0o600)

    @pytest.mark.skipif(
        os.getuid() == 0,
        reason="root bypasses file permissions, chmod 0o000 ineffective",
    )
    def test_unreadable_file_raises(self, tmp_path):
        """api_key_file that raises OSError on read wraps in CliError."""
        key_file = tmp_path / "unreadable.key"
        key_file.write_text("sk-abc123\n", encoding="utf-8")
        key_file.chmod(0o000)
        backend = BackendConfig(
            name="test",
            type="api",
            model="m",
            format="openai",
            api_key_file=str(key_file),
        )
        try:
            with pytest.raises(CliError, match="unreadable"):
                _check_backend_credentials(backend)
        finally:
            key_file.chmod(0o644)


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

    def test_existing_credentials_path_passes(self, tmp_path):
        """Vertex backend with existing credentials_path does NOT raise."""
        cred_file = tmp_path / "service-account.json"
        cred_file.write_text('{"type":"service_account"}', encoding="utf-8")
        backend = BackendConfig(
            name="test-vertex",
            type="api",
            model="gemini-pro",
            format="vertex",
            project_id="my-project",
            credentials_path=str(cred_file),
        )
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
        with pytest.raises(CliError, match="not set"):
            _check_backend_credentials(backend)

    def test_env_var_present_passes(self, monkeypatch):
        """Set api_key_env value does NOT raise CliError."""
        monkeypatch.setenv("TEST_PRESENT_KEY_XYZ", "sk-abc123")
        backend = BackendConfig(
            name="test",
            type="api",
            model="m",
            format="openai",
            api_key_env="TEST_PRESENT_KEY_XYZ",
        )
        _check_backend_credentials(backend)


# -- credential_error behavior table tests ---------------------------------

class TestCredentialErrorTable:
    """Verify credential_error matches the agreed behavior table.

    Tests the shared rule directly (not through wrappers) so both
    _check_backend_credentials and _probe_api inherit the contract.
    """

    def test_api_key_file_missing(self, tmp_path):
        err = credential_error(
            BackendConfig(name="b", type="api", model="m", format="openai",
                          api_key_file=str(tmp_path / "nope")),
            {},
        )
        assert err is not None and "not found" in err

    def test_api_key_file_unreadable(self, tmp_path):
        key = tmp_path / "unread.key"
        key.write_text("x", encoding="utf-8")
        key.chmod(0o000)
        try:
            err = credential_error(
                BackendConfig(name="b", type="api", model="m",
                              format="openai", api_key_file=str(key)),
                {},
            )
            assert err is not None and "unreadable" in err
        finally:
            key.chmod(0o644)

    def test_api_key_file_empty(self, tmp_path):
        key = tmp_path / "empty.key"
        key.write_text("", encoding="utf-8")
        err = credential_error(
            BackendConfig(name="b", type="api", model="m", format="openai",
                          api_key_file=str(key)),
            {},
        )
        assert err is not None and "empty" in err

    def test_api_key_file_group_readable(self, tmp_path):
        key = tmp_path / "perms.key"
        key.write_text("sk-abc\n", encoding="utf-8")
        key.chmod(0o640)
        try:
            err = credential_error(
                BackendConfig(name="b", type="api", model="m",
                              format="openai", api_key_file=str(key)),
                {},
            )
            assert err is not None and "chmod 600" in err
        finally:
            key.chmod(0o600)

    def test_api_key_file_ok(self, tmp_path):
        key = tmp_path / "good.key"
        key.write_text("sk-abc\n", encoding="utf-8")
        key.chmod(0o600)
        assert credential_error(
            BackendConfig(name="b", type="api", model="m", format="openai",
                          api_key_file=str(key)),
            {},
        ) is None

    def test_api_key_env_set(self):
        assert credential_error(
            BackendConfig(name="b", type="api", model="m", format="openai",
                          api_key_env="MY_KEY"),
            {"MY_KEY": "sk-abc"},
        ) is None

    def test_api_key_env_missing(self):
        err = credential_error(
            BackendConfig(name="b", type="api", model="m", format="openai",
                          api_key_env="MY_KEY"),
            {},
        )
        assert err is not None and "not set" in err

    def test_vertex_credentials_path_ok(self, tmp_path):
        cred = tmp_path / "sa.json"
        cred.write_text("{}")
        assert credential_error(
            BackendConfig(name="b", type="api", model="m", format="vertex",
                          credentials_path=str(cred)),
            {},
        ) is None

    def test_vertex_credentials_path_missing(self, tmp_path):
        err = credential_error(
            BackendConfig(name="b", type="api", model="m", format="vertex",
                          credentials_path=str(tmp_path / "nope.json")),
            {},
        )
        assert err is not None and "not found" in err
