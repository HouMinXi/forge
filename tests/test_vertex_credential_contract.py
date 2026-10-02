"""Pin the credential failures of _invoke_vertex.

A missing service-account file, no ambient GCP credentials, and a token
refresh that the provider rejects are all credential problems: not
retryable, kind credentials, and each names its own cause. None of them
may reach the network. The google modules are patched where the function
imports them from, because those imports happen inside the function.
"""

import pytest
import google.auth
import google.auth.transport.requests
import google.oauth2.service_account as service_account
from google.auth.exceptions import DefaultCredentialsError, RefreshError

import code_forge.llm_invoke as invoke
from code_forge.backend import BackendConfig
from code_forge.llm_invoke import LLMInvokeError, _invoke_vertex


class _Creds:
    token = "tok"

    def refresh(self, request):
        return None


def _backend(**kwargs):
    return BackendConfig(name="b", type="api", format="vertex", model="m", project_id="p", **kwargs)


def _block_network(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("urlopen was called")

    monkeypatch.setattr(invoke.urllib.request, "urlopen", fail)


def test_missing_service_account_file_is_a_credential_error(monkeypatch):
    _block_network(monkeypatch)

    def missing(path, scopes):
        raise FileNotFoundError(path)

    monkeypatch.setattr(service_account.Credentials, "from_service_account_file", missing)

    with pytest.raises(LLMInvokeError) as caught:
        _invoke_vertex("prompt", _backend(credentials_path="/no/such/file.json"), 30)

    assert caught.value.retryable is False
    assert caught.value.kind == "credentials"
    assert "/no/such/file.json" in str(caught.value)


def test_no_ambient_credentials_is_a_credential_error(monkeypatch):
    _block_network(monkeypatch)

    def no_default(scopes):
        raise DefaultCredentialsError("none")

    monkeypatch.setattr(google.auth, "default", no_default)

    with pytest.raises(LLMInvokeError) as caught:
        _invoke_vertex("prompt", _backend(), 30)

    assert caught.value.retryable is False
    assert caught.value.kind == "credentials"
    assert "GOOGLE_APPLICATION_CREDENTIALS" in str(caught.value)


def test_refresh_rejected_is_a_credential_error(monkeypatch):
    _block_network(monkeypatch)
    monkeypatch.setattr(google.auth, "default", lambda scopes: (_Creds(), None))

    def rejected(self, request):
        raise RefreshError("denied")

    monkeypatch.setattr(_Creds, "refresh", rejected)

    with pytest.raises(LLMInvokeError) as caught:
        _invoke_vertex("prompt", _backend(), 30)

    assert caught.value.retryable is False
    assert caught.value.kind == "credentials"
    assert "denied" in str(caught.value)
