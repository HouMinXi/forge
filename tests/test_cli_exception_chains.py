"""Preserve failure causes when translating errors for command-line callers."""
import io
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from code_forge import cli
from code_forge.backend import BackendConfig
from code_forge.baseline import BaselineResolutionError, ResolvedReview
from code_forge.errors import CliError, CoverageConfigError
from code_forge.state import Verdict


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    """Use real CLI control flow; keep model calls outside these tests."""
    monkeypatch.chdir(tmp_path)
    # A temporary directory may live inside the caller's Git checkout.
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent.resolve()))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user-config"))
    monkeypatch.setenv("FORGE_PROJECT_DIR", str(tmp_path))
    args = cli._build_parser().parse_args([
        "review", "--allow-main", "--backend", "test", "--registry", "custom.yaml",
        "--falsification-engine", "stub", "a.py",
    ])
    (tmp_path / "custom.yaml").write_text("tools: {}\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    backend = BackendConfig(name="test", type="api", format="openai",
                            base_url="https://example.invalid", model="test")
    monkeypatch.setattr("code_forge.outlet_resolver.resolve_outlet", lambda *a, **kw: "subprocess")
    monkeypatch.setattr("code_forge.backend.resolve_backend", lambda *a, **kw: backend)
    monkeypatch.setattr(cli, "_check_backend_credentials", lambda *a, **kw: None)
    monkeypatch.setattr("code_forge.user_config.load_user_retry", dict)
    monkeypatch.setattr(cli, "_run_hold_loop", Mock(return_value=Verdict.PASS))
    return tmp_path, args


def test_missing_registry_preserves_file_error(pipeline):
    root, args = pipeline
    args.registry = "missing-registry.yaml"
    with pytest.raises(CliError, match="registry load failed") as caught:
        cli._run(args, {"FORGE_PROJECT_DIR": str(root)}, root)
    assert isinstance(caught.value.__cause__, FileNotFoundError)


def test_baseline_preserves_resolution_error(pipeline, monkeypatch):
    root, args = pipeline
    error = BaselineResolutionError("missing ref")
    monkeypatch.setattr(cli, "resolve_baseline", Mock(side_effect=error))
    with pytest.raises(CliError, match="baseline resolution failed") as caught:
        cli._run(args, {"FORGE_PROJECT_DIR": str(root)}, root)
    assert caught.value.__cause__ is error


def test_snapshot_preserves_resolution_error(pipeline, monkeypatch):
    root, args = pipeline
    error = BaselineResolutionError("broken snapshot")
    resolved = ResolvedReview(source_files=[root / "a.py"], baseline_content=None,
                              git_diff=None, mode_hint="non-git")
    monkeypatch.setattr(cli, "resolve_baseline", Mock(side_effect=[resolved, error]))
    monkeypatch.setattr("code_forge.snapshot.find_existing_snapshot", lambda *a: root / "snapshot")
    with pytest.raises(CliError, match="snapshot baseline resolution failed") as caught:
        cli._run(args, {"FORGE_PROJECT_DIR": str(root)}, root)
    assert caught.value.__cause__ is error


def test_malformed_coverage_preserves_config_error(pipeline):
    root, args = pipeline
    directory = root / ".code-forge"
    directory.mkdir()
    (directory / "coverage.yaml").write_text("[]\n", encoding="utf-8")
    with pytest.raises(CliError, match="coverage.yaml") as caught:
        cli._run(args, {"FORGE_PROJECT_DIR": str(root)}, root)
    assert isinstance(caught.value.__cause__, CoverageConfigError)


def test_missing_contract_preserves_file_error(tmp_path):
    with pytest.raises(CliError, match="not found") as caught:
        cli._load_contract_file(str(tmp_path / "missing.md"))
    assert isinstance(caught.value.__cause__, FileNotFoundError)


def test_invalid_contract_preserves_decode_error(tmp_path):
    path = tmp_path / "contract.md"
    path.write_bytes(b"\xff")
    with pytest.raises(CliError, match="not valid UTF-8") as caught:
        cli._load_contract_file(str(path))
    assert isinstance(caught.value.__cause__, UnicodeDecodeError)


@pytest.mark.parametrize("error", [PermissionError("denied"), OSError("disk failure")])
def test_contract_read_preserves_original_error(tmp_path, monkeypatch, error):
    # Inject only the filesystem failure; the real loader translates it.
    monkeypatch.setattr(cli.Path, "read_text", Mock(side_effect=error))
    with pytest.raises(CliError) as caught:
        cli._load_contract_file(str(tmp_path / "contract.md"))
    assert caught.value.__cause__ is error


def test_closed_contract_stdin_preserves_value_error(monkeypatch):
    stream = io.BytesIO(b"contract")
    stream.close()
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=stream))
    with pytest.raises(CliError, match="cannot read") as caught:
        cli._load_contract_file("-")
    assert isinstance(caught.value.__cause__, ValueError)


def test_invalid_contract_stdin_preserves_decode_error(monkeypatch):
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"\xff")))
    with pytest.raises(CliError, match="not valid UTF-8") as caught:
        cli._load_contract_file("-")
    assert isinstance(caught.value.__cause__, UnicodeDecodeError)


def test_invalid_focus_preserves_decode_error(tmp_path):
    path = tmp_path / "focus.md"
    path.write_bytes(b"\xff")
    with pytest.raises(CliError, match="not valid UTF-8") as caught:
        cli._load_focus_file(str(path))
    assert isinstance(caught.value.__cause__, UnicodeDecodeError)


def test_malformed_siblings_preserves_yaml_error(tmp_path):
    path = tmp_path / "gate.yaml"
    path.write_text("siblings: [\n", encoding="utf-8")
    with pytest.raises(CliError, match="malformed gate.yaml") as caught:
        cli._load_gate_siblings(path)
    assert isinstance(caught.value.__cause__, yaml.YAMLError)


def test_init_file_collision_preserves_os_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".code-forge").write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["code-forge", "init"])
    with pytest.raises(CliError, match="not a directory") as caught:
        cli.main()
    assert isinstance(caught.value.__cause__, FileExistsError)


def test_escaping_whole_file_path_preserves_value_error(tmp_path):
    args = cli._build_parser().parse_args(["review", "--whole-file", "../outside.py"])
    with pytest.raises(CliError, match="escapes repo root") as caught:
        cli._resolve_whole_file_specs(args, tmp_path)
    assert isinstance(caught.value.__cause__, ValueError)


def test_interrupt_suppresses_traceback_context(monkeypatch, capsys):
    # Replace the review boundary, not main's exception handling.
    monkeypatch.setattr(cli, "_run", Mock(side_effect=KeyboardInterrupt()))
    monkeypatch.setattr(sys, "argv", ["code-forge", "review"])
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert caught.value.code == 130
    assert caught.value.__suppress_context__ is True
    assert caught.value.__cause__ is None
    assert capsys.readouterr().err == "code-forge: interrupted\n"
