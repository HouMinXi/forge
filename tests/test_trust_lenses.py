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
    assert "No backends or review_focus configured in this gate.yaml. Configure at least one." in err


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
    # Lenses never mentioned: the status output is what it was before lenses.
    assert "Review lenses" not in err
    assert "Lens hash" not in err
    assert err.splitlines()[0] == "Trusted: True"

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


def test_status_shows_lens_lines_when_only_a_lens_hash_is_stored(tmp_path, home, capsys):
    repo = tmp_path / "repo"
    gate = _gate(tmp_path, {"backends": BACKEND, "review_lenses": True})
    _trust(gate)
    gate.write_text(yaml.safe_dump({"backends": BACKEND}))
    err = _status(repo, capsys)
    assert "Lens hash: " + hash_lenses_flag({"review_lenses": True}) in err
    assert "Review lenses: off (not set)" in err


# Literal on purpose: sha256(b"code-forge:review_lenses:true"). Not derived from
# hash_lenses_flag, so a change to the production hash shows up here.
LENS_ON_HASH = "4a9a3ebd219e7ddd795cf37714f44ca8a9385f5232cca4dad22c2a6e90218d52"


def _stored(config_dir: Path, gate: Path) -> dict:
    store = json.loads((config_dir / "trusted.json").read_text(encoding="utf-8"))
    return store[str(gate.resolve())]


def test_setup_mcp_auto_trust_records_lens_hash(tmp_path, home, monkeypatch):
    from code_forge.setup_mcp import _auto_trust_gate, run_setup_mcp

    # End to end: the generated gate.yaml never mentions lenses, so the stored
    # lens hash is empty and lenses stay off.
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("FORGE_CONFIG_DIR", str(tmp_path / "user-config"))
    assert run_setup_mcp(project, ["deepseek"]) == 0
    gate = project / ".code-forge" / "gate.yaml"
    entry = _stored(home / "code-forge", gate)
    assert entry["lens_hash"] == ""
    assert entry["hash"]

    # The helper trusts exactly what it is given, lens switch included.
    on = _gate(tmp_path / "on", {"outlet": "subprocess", "review_lenses": True})
    _auto_trust_gate(on)
    assert _stored(home / "code-forge", on)["lens_hash"] == LENS_ON_HASH
    assert is_trusted_lenses(on, _load(on))


def test_eval_harness_trust_records_lens_hash(tmp_path, monkeypatch):
    from code_forge.eval.runner import _create_gate_yaml, _trust_eval_gate

    xdg = tmp_path / "xdg"
    trust_dir = xdg / "code-forge"
    trust_dir.mkdir(parents=True)
    # The default store is somewhere else: the grant must land in trust_dir.
    elsewhere = tmp_path / "elsewhere"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(elsewhere))

    # A gate.yaml that came with the diff under review is merged, then trusted.
    repo = tmp_path / "repo"
    (repo / ".code-forge").mkdir(parents=True)
    (repo / ".code-forge" / "gate.yaml").write_text(
        yaml.safe_dump({"review_lenses": True}), encoding="utf-8"
    )
    gate = _create_gate_yaml(repo, "harness")
    _trust_eval_gate(gate, trust_dir)
    assert _stored(trust_dir, gate)["lens_hash"] == LENS_ON_HASH
    assert not (elsewhere / "code-forge" / "trusted.json").exists()
    # The child resolves its store as $XDG_CONFIG_HOME/code-forge, i.e. trust_dir.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    assert is_trusted_lenses(gate, _load(gate))

    plain_repo = tmp_path / "plain"
    plain = _create_gate_yaml(plain_repo, "harness")
    _trust_eval_gate(plain, trust_dir)
    assert _stored(trust_dir, plain)["lens_hash"] == ""
    assert hash_lenses_flag(_load(plain)) == ""  # nothing to authorize, lenses stay off


def test_eval_run_single_trusts_through_the_helper():
    """_run_single trusts the gate it just built through _trust_eval_gate, into trust_dir."""
    import ast

    from code_forge.eval import runner

    tree = ast.parse(Path(runner.__file__).read_text(encoding="utf-8"))
    fn = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_run_single"
    )
    calls = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "_trust_eval_gate"
    ]
    assert len(calls) == 1
    assert [getattr(a, "id", None) for a in calls[0].args] == ["gate_path", "trust_dir"]


def test_setup_mcp_run_trusts_through_the_helper():
    import ast

    from code_forge import setup_mcp

    tree = ast.parse(Path(setup_mcp.__file__).read_text(encoding="utf-8"))
    fn = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "run_setup_mcp"
    )
    calls = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "_auto_trust_gate"
    ]
    assert len(calls) == 1
    assert [getattr(a, "id", None) for a in calls[0].args] == ["gate_yaml_path"]
