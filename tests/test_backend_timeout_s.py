# SPDX-License-Identifier: Apache-2.0
"""Issue #106: timeout_s is parsed for cli backends (not silently dropped)
and validated the same way for every backend type."""

from __future__ import annotations

import copy

import pytest

from code_forge.backend import load_backend_configs
from code_forge.errors import CliError
from code_forge.llm_invoke import effective_invoke_timeout_s

CLI = {"type": "cli", "command": "claude"}
API = {"type": "api", "format": "openai", "base_url": "https://example.invalid/v1", "api_key_env": "K", "model": "m"}
VERTEX = {"type": "api", "format": "vertex", "project_id": "p", "model": "m"}
KINDS = {"cli": CLI, "api": API, "vertex": VERTEX}


def _load(base, **extra):
    (cfg,) = load_backend_configs({"backends": {"b": {**base, **extra}}})
    return cfg


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("value,expected", [(None, 0), (0, 0), (2400, 2400)])
def test_timeout_s_parsed_for_every_kind(kind, value, expected):
    extra = {} if value is None else {"timeout_s": value}
    assert _load(KINDS[kind], **extra).timeout_s == expected
    assert _load(KINDS[kind], timeout_s=None).timeout_s == 0


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("bad", [True, "600", 30.0, 1.5, -1, [600], {"s": 1}])
def test_invalid_timeout_s_is_a_named_config_error(kind, bad):
    with pytest.raises(CliError, match=r"backend 'b'.*timeout_s"):
        _load(KINDS[kind], timeout_s=bad)


def test_caller_dict_untouched():
    entry = {**CLI, "timeout_s": 2400}
    data = {"backends": {"b": entry}}
    snapshot = copy.deepcopy(data)
    load_backend_configs(data)
    assert data == snapshot


def test_cli_timeout_above_implicit_cap_reaches_invoke(monkeypatch):
    monkeypatch.delenv("FORGE_LLM_TIMEOUT_S", raising=False)
    cfg = _load(CLI, timeout_s=2400)
    assert effective_invoke_timeout_s(cfg) == 2400
    assert effective_invoke_timeout_s(cfg, timeout_s=60) == 2400  # backend outranks caller


def test_cli_without_timeout_keeps_implicit_cap(monkeypatch):
    monkeypatch.delenv("FORGE_LLM_TIMEOUT_S", raising=False)
    cfg = _load(CLI)
    assert effective_invoke_timeout_s(cfg) == 300
    assert effective_invoke_timeout_s(cfg, timeout_s=900) == 900


def test_cli_timeout_reaches_subprocess_communicate(monkeypatch):
    import code_forge.llm_invoke as invoke

    monkeypatch.delenv("FORGE_LLM_TIMEOUT_S", raising=False)
    seen = {}

    def fake_cli(prompt, backend, timeout_s):
        seen["timeout_s"] = timeout_s
        return invoke.LLMResult(content={"ok": True}, usage=invoke.Usage(), duration_s=0.0)

    monkeypatch.setattr(invoke, "_invoke_cli", fake_cli)
    invoke.llm_invoke("p", backend=_load(CLI, timeout_s=2400), expected_keys=frozenset({"ok"}))
    assert seen["timeout_s"] == 2400
