# SPDX-License-Identifier: Apache-2.0
"""Phase 59.1 Task 3: review_lenses is gated by its own trust hash."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
import yaml

from code_forge import trust as trust_mod
from code_forge.cli import (
    _build_parser,
    _load_trusted_review_lenses,
    _run_trust,
)
from code_forge.trust import (
    hash_lenses_flag,
    is_trusted_lenses,
    lens_trust_state,
    record_trust,
)

BACKEND = {
    "test-backend": {
        "type": "api",
        "format": "openai",
        "base_url": "https://api.example.com/v1",
        "api_key_env": "TEST_KEY",
        "model": "m",
    }
}
NOTICE = "review_lenses: true changes the baseline review prompts for this repo."


@pytest.fixture()
def home(tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    cfg.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    return cfg


def _gate(tmp_path: Path, data: dict) -> Path:
    d = tmp_path / "repo" / ".code-forge"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "gate.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _trust(path: Path) -> None:
    record_trust(path, _load(path))


def _loader(path: Path, warnings: list[str] | None = None) -> bool:
    sink = warnings if warnings is not None else []
    return _load_trusted_review_lenses(path, sink.append)


def _cli(args: list[str], repo: Path) -> int:
    ns = _build_parser().parse_args(args)
    return _run_trust(ns, repo)


def test_hash_is_fixed_and_only_for_true():
    want = hashlib.sha256(b"code-forge:review_lenses:true").hexdigest()
    assert hash_lenses_flag({"review_lenses": True}) == want
    for off in ({}, {"review_lenses": False}, {"review_lenses": None}):
        assert hash_lenses_flag(off) == ""
    for odd in ("true", "yes", 1, [True], {"a": 1}):
        assert hash_lenses_flag({"review_lenses": odd}) == ""


def test_a_trusted_false_then_true_without_retrust_is_ignored(tmp_path, home):
    gate = _gate(tmp_path, {"backends": BACKEND, "review_lenses": False})
    _trust(gate)
    gate.write_text(yaml.safe_dump({"backends": BACKEND, "review_lenses": True}))
    warnings: list[str] = []
    assert _loader(gate, warnings) is False
    assert any("review_lenses ignored: not trusted" in w for w in warnings)
    _trust(gate)
    assert _loader(gate) is True


def test_b_true_false_true_needs_no_retrust(tmp_path, home, capsys):
    gate = _gate(tmp_path, {"backends": BACKEND, "review_lenses": True})
    _trust(gate)
    assert _loader(gate) is True
    gate.write_text(yaml.safe_dump({"backends": BACKEND, "review_lenses": False}))
    assert _loader(gate) is False
    assert (
        lens_trust_state(gate, _load(gate))
        == "off (true was trusted before; enabling needs no re-trust)"
    )
    gate.write_text(yaml.safe_dump({"backends": BACKEND, "review_lenses": True}))
    assert _loader(gate) is True


def test_c_old_store_entry_without_lens_hash(tmp_path, home):
    gate = _gate(tmp_path, {"backends": BACKEND})
    _trust(gate)
    store = trust_mod._load_trust_store()
    for entry in store.values():
        entry.pop("lens_hash", None)
    trust_mod._save_trust_store(store)
    assert _loader(gate) is False
    assert is_trusted_lenses(gate, _load(gate)) is True
    gate.write_text(yaml.safe_dump({"backends": BACKEND, "review_lenses": False}))
    assert _loader(gate) is False
    assert is_trusted_lenses(gate, _load(gate)) is True
    gate.write_text(yaml.safe_dump({"backends": BACKEND, "review_lenses": True}))
    assert is_trusted_lenses(gate, _load(gate)) is False
    assert _loader(gate) is False


def test_d_only_review_lenses_true_can_be_trusted(tmp_path, home, capsys):
    gate = _gate(tmp_path, {"review_lenses": True})
    assert _cli(["trust"], tmp_path / "repo") == 0
    err = capsys.readouterr().err
    assert NOTICE in err
    assert _loader(gate) is True


def test_d_no_notice_when_lenses_off(tmp_path, home, capsys):
    _gate(tmp_path, {"backends": BACKEND, "review_lenses": False})
    assert _cli(["trust"], tmp_path / "repo") == 0
    assert NOTICE not in capsys.readouterr().err


def test_d_guard_still_rejects_an_empty_gate(tmp_path, home, capsys):
    _gate(tmp_path, {"review_lenses": False})
    assert _cli(["trust"], tmp_path / "repo") != 0
    err = capsys.readouterr().err
    assert "No backends, review_focus or review_lenses configured" in err


@pytest.mark.parametrize("value", ["true", 1, "yes"])
def test_e_non_bool_values_are_ignored_with_a_warning(tmp_path, home, value):
    gate = _gate(tmp_path, {"backends": BACKEND, "review_lenses": value})
    _trust(gate)
    warnings: list[str] = []
    assert _loader(gate, warnings) is False
    kind = type(value).__name__
    want = "gate.yaml review_lenses ignored: not a bool (got %s)" % kind
    assert any(w.startswith(want) for w in warnings)


def test_e_absent_or_null_is_silent(tmp_path, home):
    gate = _gate(tmp_path, {"backends": BACKEND})
    warnings: list[str] = []
    assert _loader(gate, warnings) is False
    assert warnings == []
    assert _loader(tmp_path / "missing" / "gate.yaml") is False


def _status(repo: Path, capsys) -> str:
    assert _cli(["trust", "--status"], repo) == 0
    return capsys.readouterr().err


def test_f_status_states(tmp_path, home, capsys):
    repo = tmp_path / "repo"
    gate = _gate(tmp_path, {"backends": BACKEND})
    _trust(gate)
    err = _status(repo, capsys)
    assert "Review lenses: off (not set)" in err
    assert "Lens hash: (none)" in err

    gate.write_text(yaml.safe_dump({"backends": BACKEND, "review_lenses": "true"}))
    assert "Review lenses: off (not a bool)" in _status(repo, capsys)

    gate.write_text(yaml.safe_dump({"backends": BACKEND, "review_lenses": False}))
    assert "Review lenses: off (re-trust after enabling)" in _status(repo, capsys)

    gate.write_text(yaml.safe_dump({"backends": BACKEND, "review_lenses": True}))
    assert "Review lenses: untrusted (run code-forge trust)" in _status(repo, capsys)

    _trust(gate)
    err = _status(repo, capsys)
    assert "Review lenses: on" in err
    assert "Lens hash: " + hash_lenses_flag({"review_lenses": True}) in err

    gate.write_text(yaml.safe_dump({"backends": BACKEND, "review_lenses": False}))
    err = _status(repo, capsys)
    assert "Review lenses: off (true was trusted before; enabling needs no re-trust)" in err


def test_g_lens_readers_never_write_the_store(tmp_path, home):
    gate = _gate(tmp_path, {"backends": BACKEND, "review_lenses": True})
    data = _load(gate)
    # An old-format entry that trust_status would silently migrate and rewrite.
    legacy = trust_mod._hash_all_backends(data)
    store_path = trust_mod._trust_store_path()
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps({str(gate.resolve()): {"hash": legacy}}, indent=2))
    os.utime(store_path, (1_000_000_000, 1_000_000_000))
    before = (store_path.read_bytes(), store_path.stat().st_mtime_ns)

    assert is_trusted_lenses(gate, data) is False
    assert lens_trust_state(gate, data) == "untrusted (run code-forge trust)"
    assert _loader(gate) is False
    after = (store_path.read_bytes(), store_path.stat().st_mtime_ns)
    assert after == before

    # Control: the migrating reader does rewrite this store, so the check bites.
    trust_mod.trust_status(gate, data)
    assert store_path.read_bytes() != before[0]


def test_h_hash_is_not_bound_to_the_block(monkeypatch):
    from code_forge import lenses

    before = hash_lenses_flag({"review_lenses": True})
    monkeypatch.setattr(lenses, "LENS_BLOCK", "changed\n")
    assert hash_lenses_flag({"review_lenses": True}) == before


def test_record_trust_stores_lens_hash(tmp_path, home):
    gate = _gate(tmp_path, {"backends": BACKEND, "review_lenses": True})
    _trust(gate)
    entry = trust_mod._load_trust_store()[str(gate.resolve())]
    assert entry["lens_hash"] == hash_lenses_flag({"review_lenses": True})
    assert entry["hash"] and "focus_hash" in entry


def test_schema_declares_review_lenses_as_boolean_default_false():
    schema = json.loads(
        (Path(trust_mod.__file__).parent / "gate.schema.json").read_text(encoding="utf-8")
    )
    prop = schema["properties"]["review_lenses"]
    assert prop["type"] == "boolean"
    assert prop["default"] is False
    assert "code-forge trust" in prop["description"]
