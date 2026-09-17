"""Executable counterexamples for disputed review assumptions."""
import os

import pytest

from code_forge import kernel_context as k
from code_forge import trust
from tests.test_kernel_context_source import diff, source


def test_empty_normalized_walk_reaches_final_invalid_path(tmp_path):
    # PurePosixPath('.') has no components; the final rejection is reachable.
    with pytest.raises(k.ReadFailure, match="^invalid-path$"):
        k.read_config_bytes(tmp_path, ".")


def test_make_assignment_is_not_defconfig_declaration(tmp_path):
    (tmp_path / "defconfig").write_text("CONFIG_X := y\n")
    rows = source(tmp_path, defconfig="defconfig").facts([], diff(["CONFIG_X"]))
    assert len(rows) == 1
    assert rows[0].dependents == "unknown; reason=ambiguous-declaration"


def test_source_name_and_row_provenance_are_distinct():
    assert k.KernelContextSource.name == "kernel_context"
    assert k._row("config:CONFIG_X").source == "kernel"
    assert isinstance(os.supports_dir_fd, set)
    assert os.open in os.supports_dir_fd


def test_guard_constructs_remain_distinct():
    rows = source(".")._text_rows("#if IS_ENABLED(CONFIG_X)", "x.c", "new", 1)
    assert {row.entity for row in rows} == {
        "guard:new:1:if:CONFIG_X", "guard:new:1:IS_ENABLED:CONFIG_X",
    }


def test_symlink_root_is_canonicalized(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user"))
    root = tmp_path / "root"
    root.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    gate = root / "gate.yaml"
    cfg = k.validate_kernel_context({"enabled": True, "defconfig": "defconfig"})
    trust.record_kernel_context_trust(gate, alias, cfg)
    assert trust.is_trusted_kernel_context(gate, root, cfg)
