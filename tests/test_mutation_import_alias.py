# SPDX-License-Identifier: Apache-2.0
"""Physical directory aliases retain strict installed-file authority."""

import csv
import importlib.metadata
import io
import os
import sys
import types
from pathlib import Path

import pytest

from code_forge import _mutation_imports as imports


@pytest.fixture
def installed(tmp_path, monkeypatch):
    root = tmp_path / "lib" / "site-packages"
    root.mkdir(parents=True)
    alias = tmp_path / "lib64"
    alias.symlink_to(root.parent, target_is_directory=True)
    metadata = root / "alias_control-1.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Name: alias-control\nVersion: 1\n")
    package = root / "alias_control"
    package.mkdir()
    (package / "__init__.py").write_text("value = 1\n")
    (package / "module.py").write_text("value = 2\n")
    record = io.StringIO()
    csv.writer(record).writerows(
        [["alias_control/__init__.py", "", ""], ["alias_control/module.py", "", ""]]
    )
    (metadata / "RECORD").write_text(record.getvalue())
    distribution = types.SimpleNamespace(
        _path=alias / "site-packages" / metadata.name,
        locate_file=lambda name: alias / "site-packages" / name,
    )
    monkeypatch.setattr(importlib.metadata, "distribution", lambda _: distribution)
    return root, alias / "site-packages", metadata, distribution


def test_installed_directory_aliases_pin_all_files(installed):
    root, alias, metadata, _ = installed
    assert os.path.samefile(root, alias)
    distribution, modules, _ = imports._distribution("alias-control")
    assert distribution["root"] == str(root)
    assert distribution["metadata"]["path"] == str(metadata / "METADATA")
    assert distribution["record"]["path"] == str(metadata / "RECORD")
    assert modules["alias_control"]["path"] == str(root / "alias_control/__init__.py")
    assert modules["alias_control.module"]["path"] == str(root / "alias_control/module.py")


@pytest.mark.parametrize("leaf", ["METADATA", "RECORD", "module.py"])
@pytest.mark.parametrize("target_location", ["inside", "outside"])
def test_installed_alias_rejects_final_leaf_symlinks(installed, tmp_path, leaf, target_location):
    root, _, metadata, _ = installed
    path = root / "alias_control/module.py" if leaf == "module.py" else metadata / leaf
    target = (root if target_location == "inside" else tmp_path) / "target"
    target.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(target)
    with pytest.raises(OSError):
        imports._distribution("alias-control")


def test_installed_alias_rejects_foreign_metadata(installed, tmp_path):
    _, _, metadata, distribution = installed
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    distribution._path = foreign
    with pytest.raises(ValueError, match="metadata has a foreign origin"):
        imports._distribution("alias-control")


def test_installed_alias_rejects_foreign_module_directory(installed, tmp_path):
    root, alias, _, _ = installed
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "module.py").write_text("value = 2\n")
    (root / "alias_control/module.py").unlink()
    (root / "alias_control/module.py").parent.rename(root / "original-package")
    (root / "alias_control").symlink_to(foreign, target_is_directory=True)
    (foreign / "__init__.py").write_text("value = 1\n")
    with pytest.raises(ValueError, match="dependency has a foreign origin"):
        imports._distribution("alias-control")


def test_installed_alias_preserves_metadata_name_check(installed):
    _, _, metadata, _ = installed
    (metadata / "METADATA").write_text("Name: wrong\nVersion: 1\n")
    with pytest.raises(ValueError, match="name/version mismatch"):
        imports._distribution("alias-control")


@pytest.fixture
def preloaded(tmp_path, monkeypatch):
    root = tmp_path / "real"
    root.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    path = root / "requirements.py"
    path.write_text("Requirement = object\n")
    pin, _ = imports._pin_file(path)
    origins = {"packaging.requirements": dict(pin, kind="source")}
    monkeypatch.setattr(imports, "_distribution", lambda _: ({}, origins, []))
    for name in list(sys.modules):
        if name == "packaging" or name.startswith("packaging."):
            monkeypatch.delitem(sys.modules, name)
    module = types.ModuleType("packaging.requirements")
    module.__file__ = str(alias / path.name)
    module.Requirement = object()
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return path, alias, module, pin


def test_preloaded_packaging_parent_alias_is_same_origin(preloaded):
    _, _, module, _ = preloaded
    assert imports._requirements_api() is module.Requirement


def test_preloaded_packaging_foreign_origin_is_refused(preloaded, tmp_path):
    _, _, module, _ = preloaded
    module.__file__ = str(tmp_path / "foreign.py")
    with pytest.raises(ValueError, match="foreign parent packaging module"):
        imports._requirements_api()


@pytest.mark.parametrize("loaded_path", [None, Path("relative.py")])
def test_preloaded_packaging_malformed_origin_is_refused(preloaded, loaded_path):
    _, _, module, _ = preloaded
    module.__file__ = loaded_path
    with pytest.raises(ValueError, match="foreign parent packaging module"):
        imports._requirements_api()


def test_preloaded_packaging_leaf_symlink_is_refused(preloaded):
    path, alias, module, _ = preloaded
    (path.parent / "linked.py").symlink_to(path)
    module.__file__ = str(alias / "linked.py")
    with pytest.raises(ValueError, match="foreign parent packaging module"):
        imports._requirements_api()


def test_preloaded_packaging_replaced_file_is_refused(preloaded):
    path, _, _, _ = preloaded
    path.write_text("changed = True\n")
    with pytest.raises(ValueError, match="parent packaging origin changed"):
        imports._requirements_api()


def test_real_isolated_distribution_aliases_are_accepted():
    distribution = importlib.metadata.distribution("packaging")
    parent = Path(distribution._path).parent
    if str(parent) == str(parent.resolve()):
        pytest.skip("runtime installation has no parent directory alias")
    row, modules, _ = imports._distribution("packaging")
    assert os.path.samefile(parent, row["root"])
    assert "packaging.requirements" in modules
    assert imports._requirements_api()("packaging>=1").name == "packaging"
