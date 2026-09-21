"""Optional input errors must not hide internal loader failures."""

import json
from pathlib import Path

import pytest

from code_forge import advisory, backend, cli, user_config
from code_forge.user_config import load_user_backends as real_load_user_backends


def _advisory(**changes):
    item = {
        "id": "advisory-1",
        "axis": "RUNTIME",
        "file": "sample.py",
        "line_range": [3, 7],
        "description": "Keep quoted content: head=\"\" and \\n",
        "attribution": "local-test",
    }
    item.update(changes)
    return item


@pytest.mark.parametrize(
    "payload",
    [
        b"{broken",
        b"\xff",
        b"null",
        b"42",
        b"[null]",
        b'[{"wrong": "fields"}]',
        json.dumps([_advisory(line_range="bad")]).encode(),
        json.dumps([_advisory(line_range=["bad", 7])]).encode(),
        json.dumps([_advisory(line_range=[float("inf"), 7])]).encode(),
        json.dumps([_advisory(line_range={"start": 3})]).encode(),
    ],
    ids=[
        "syntax", "encoding", "null", "scalar", "null-entry", "schema",
        "range-type", "range-value", "range-overflow", "range-mapping",
    ],
)
def test_malformed_advisory_file_keeps_empty_fallback(tmp_path, payload):
    path = tmp_path / "advisory-findings.json"
    path.write_bytes(payload)
    assert cli._load_advisories(path) == []


def test_advisory_parser_recursion_keeps_empty_fallback(tmp_path, monkeypatch):
    path = tmp_path / "advisory-findings.json"
    path.write_text("[]", encoding="utf-8")

    def fail_decode(content):
        assert content == "[]"
        raise RecursionError("decoder nesting limit")

    with monkeypatch.context() as patcher:
        patcher.setattr(json, "loads", fail_decode)
        assert cli._load_advisories(path) == []


def test_advisory_roundtrip_preserves_fields(tmp_path):
    item = _advisory()
    path = tmp_path / "advisory-findings.json"
    path.write_text(json.dumps([item]), encoding="utf-8")
    result = cli._load_advisories(path)
    assert len(result) == 1
    assert result[0] == advisory.AdvisoryFinding(**item)
    assert result[0].description == item["description"]
    assert result[0].line_range == (3, 7)


@pytest.mark.parametrize("error", [PermissionError, FileNotFoundError])
def test_advisory_read_failure_keeps_empty_fallback(tmp_path, monkeypatch, error):
    path = tmp_path / "advisory-findings.json"
    path.write_text("[]", encoding="utf-8")

    def fail_read(self, *args, **kwargs):
        assert self == path
        raise error("unreadable advisory file")

    monkeypatch.setattr(Path, "read_text", fail_read)
    assert cli._load_advisories(path) == []


@pytest.mark.parametrize("error_type", [MemoryError, RuntimeError, ImportError])
def test_advisory_internal_failure_propagates(tmp_path, monkeypatch, error_type):
    path = tmp_path / "advisory-findings.json"
    path.write_text(json.dumps([_advisory()]), encoding="utf-8")
    error = error_type("advisory implementation failure")

    def fail_construct(**kwargs):
        raise error

    monkeypatch.setattr(advisory, "AdvisoryFinding", fail_construct)
    with pytest.raises(error_type) as caught:
        cli._load_advisories(path)
    assert caught.value is error


@pytest.fixture
def project_configs():
    return [backend.BackendConfig(name="project", type="cli", model="")]


@pytest.mark.parametrize(
    "entry",
    [
        {"type": "unknown"},
        {"type": "cli", "output_ceiling": "bad"},
        {"type": "cli", "env": {"set": ["invalid"]}},
        {"type": "cli", "env": {"set": {"text": "v", 1: "w"}}},
        {"type": "cli", "env": {"unset": 42}},
        {"type": []},
    ],
    ids=["schema", "numeric", "env-mapping", "mixed-keys", "env-sequence", "enum"],
)
def test_malformed_user_backend_keeps_project_fallback(
    monkeypatch, caplog, project_configs, entry,
):
    monkeypatch.setattr(user_config, "load_user_backends", lambda: {"bad": entry})
    result = cli._merge_user_into(project_configs, {})
    assert result is project_configs
    assert [cfg.name for cfg in result] == ["project"]
    assert "User backend config error, using project only:" in caplog.text


@pytest.mark.parametrize("error_type", [MemoryError, RuntimeError, ImportError])
def test_user_backend_internal_failure_propagates(
    monkeypatch, caplog, project_configs, error_type,
):
    monkeypatch.setattr(
        user_config, "load_user_backends", lambda: {"user": {"type": "cli"}},
    )
    error = error_type("backend implementation failure")

    def fail_parse(data):
        raise error

    monkeypatch.setattr(backend, "load_backend_configs", fail_parse)
    with pytest.raises(error_type) as caught:
        cli._merge_user_into(project_configs, {})
    assert caught.value is error
    assert [cfg.name for cfg in project_configs] == ["project"]
    assert "using project only" not in caplog.text


def test_real_user_file_preserves_project_precedence(tmp_path, monkeypatch, project_configs):
    path = tmp_path / "config.yaml"
    path.write_text(
        "backends:\n"
        "  project:\n"
        "    type: cli\n"
        "    model: ignored\n"
        "  user:\n"
        "    type: cli\n"
        "    model: local\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(user_config, "user_config_path", lambda: path)
    monkeypatch.setattr(user_config, "load_user_backends", real_load_user_backends)
    project = project_configs[0]
    result = cli._merge_user_into(project_configs, {"backends": {"project": {}}})
    assert result is project_configs
    assert result[0] is project
    assert [(cfg.name, cfg.model) for cfg in result] == [("project", ""), ("user", "local")]
