"""Public gate failures must not be reported as successful verification."""

import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

from code_forge.cli import _run_e2e_check_cmd
from code_forge.e2e_check import load_components_yaml
from code_forge.errors import ComponentsConfigError
from code_forge.exit_codes import EXIT_CLI_ERROR, EXIT_FAIL, EXIT_PASS


DIFF = "--- a/hub/a.py\n+++ b/hub/a.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"


def test_real_pytest_internal_error_blocks_gate(tmp_path):
    """A crashing pytest hook must not approve a commit without running tests."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "test_sample.py").write_text("def test_sample():\n    assert True\n")
    (tmp_path / "conftest.py").write_text(
        "def pytest_sessionstart(session):\n    raise RuntimeError('runner exploded')\n"
    )
    subprocess.run(["git", "add", "test_sample.py"], cwd=tmp_path, check=True)
    config = tmp_path / ".code-forge"
    config.mkdir()
    (config / "gate.yaml").write_text(
        yaml.safe_dump({"test": {"command": ["python3", "-m", "pytest", "-q"]}})
    )
    env = dict(os.environ)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env.pop("FORGE_SKIP_TESTS", None)
    result = subprocess.run(
        [sys.executable, "-m", "code_forge", "gate-check"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == EXIT_FAIL, result.stderr
    assert "internal error" in result.stderr.lower()
    assert "allowing commit" not in result.stderr


@pytest.mark.parametrize(
    "overrides",
    [{"depends_on": None}, {"depends_on": "hub"}, {"paths": [7]}, {"depends_on": [[]]}],
)
def test_component_nested_types_are_configuration_errors(tmp_path, overrides):
    config = tmp_path / ".code-forge"
    config.mkdir()
    (config / "components.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "components": {"hub": {"paths": ["hub/*"], **overrides}},
            }
        )
    )
    with pytest.raises(ComponentsConfigError):
        load_components_yaml(tmp_path)


@pytest.mark.parametrize("kind", ["null-dependency", "directory", "invalid-encoding"])
def test_invalid_components_cannot_pass_public_e2e(tmp_path, capsys, kind):
    config = tmp_path / ".code-forge"
    config.mkdir()
    component_file = config / "components.yaml"
    if kind == "directory":
        component_file.mkdir()
    elif kind == "invalid-encoding":
        component_file.write_bytes(b"\xff")
    else:
        component_file.write_text(
            "version: 1\ncomponents:\n  hub:\n    paths: ['hub/*']\n    depends_on: null\n"
        )
    diff = tmp_path / "change.diff"
    diff.write_text(DIFF)
    result = _run_e2e_check_cmd(SimpleNamespace(diff=str(diff), repo_root=None), tmp_path)
    assert result == EXIT_FAIL
    assert "PASS" not in capsys.readouterr().err


def test_unexpected_e2e_error_is_not_pass(tmp_path, monkeypatch, capsys):
    diff = tmp_path / "change.diff"
    diff.write_text(DIFF)
    monkeypatch.setattr("code_forge.e2e_check.run_e2e_check", lambda **kwargs: ([], ["broken scan"]))
    assert (
        _run_e2e_check_cmd(
            SimpleNamespace(diff=str(diff), repo_root=None),
            tmp_path,
        )
        == EXIT_CLI_ERROR
    )
    assert "PASS" not in capsys.readouterr().err


def test_e2e_without_opt_in_still_passes(tmp_path):
    diff = tmp_path / "change.diff"
    diff.write_text(DIFF)
    assert (
        _run_e2e_check_cmd(
            SimpleNamespace(diff=str(diff), repo_root=None),
            tmp_path,
        )
        == EXIT_PASS
    )
