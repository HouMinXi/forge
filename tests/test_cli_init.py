# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for code-forge init subcommand."""

import sys

import pytest
import yaml

from code_forge.cli import main


class TestCliInit:
    """code-forge init subcommand tests."""

    def test_init_creates_gate_yaml(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["code-forge", "init"])
        with pytest.raises(SystemExit) as exc_info:
            sys.exit(main())
        assert exc_info.value.code == 0
        gate_path = tmp_path / ".code-forge" / "gate.yaml"
        assert gate_path.exists()
        assert "backends:" in gate_path.read_text()

    def test_init_refuses_overwrite(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        gate_dir = tmp_path / ".code-forge"
        gate_dir.mkdir()
        gate_path = gate_dir / "gate.yaml"
        gate_path.write_text("OLD_SENTINEL")
        monkeypatch.setattr(sys, "argv", ["code-forge", "init"])
        with pytest.raises(SystemExit) as exc_info:
            sys.exit(main())
        assert exc_info.value.code == 2
        assert gate_path.read_text() == "OLD_SENTINEL"

    def test_init_force_overwrites(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        gate_dir = tmp_path / ".code-forge"
        gate_dir.mkdir()
        gate_path = gate_dir / "gate.yaml"
        gate_path.write_text("OLD_SENTINEL")
        monkeypatch.setattr(sys, "argv", ["code-forge", "init", "--force"])
        with pytest.raises(SystemExit) as exc_info:
            sys.exit(main())
        assert exc_info.value.code == 0
        content = gate_path.read_text()
        assert "backends:" in content
        assert "OLD_SENTINEL" not in content

    def test_init_template_valid_yaml(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["code-forge", "init"])
        with pytest.raises(SystemExit):
            sys.exit(main())
        gate_path = tmp_path / ".code-forge" / "gate.yaml"
        data = yaml.safe_load(gate_path.read_text())
        assert "outlet" in data
        assert data["outlet"] == "subprocess"
