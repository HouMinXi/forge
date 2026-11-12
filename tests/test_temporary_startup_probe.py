"""Temporary branch probe: synthetic inputs only; no hook, CLI or namespace execution."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("probe", ROOT / "scripts/temporary_startup_probe.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


@pytest.mark.parametrize(
    "kind,expected",
    [("empty", "NONE"), ("source", "SOURCE"), ("cache", "CACHE"), ("both", "SOURCE_AND_CACHE")],
)
def test_presence_never_executes_candidate(tmp_path, kind, expected):
    if kind in ("source", "both"):
        (tmp_path / "sitecustomize.py").write_text("raise RuntimeError('SECRET_MUST_NOT_EXECUTE')")
    if kind in ("cache", "both"):
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "usercustomize.cpython-314.pyc").write_bytes(b"SECRET_INVALID_BYTECODE")
    assert probe.candidate_presence([str(tmp_path)], [".so"]) == expected


def test_actual_validator_system_and_synthetic_contrast(tmp_path):
    (tmp_path / "sitecustomize.py").write_text("raise RuntimeError('SECRET_MUST_NOT_EXECUTE')")
    result = probe.observe(probe.load_validator(), [str(tmp_path)], [".so"])
    assert result["system_candidates"] == "SOURCE"
    assert result["system_admission"] == result["synthetic_hook"] == "REJECTED"
    assert result["synthetic_empty"] == "ACCEPTED"
    assert result["probe_status"] == "COMPLETE"
    assert "SECRET" not in json.dumps(result)
    assert str(tmp_path) not in json.dumps(result)


def test_presence_bound_and_symlink_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "LIMIT", 1)
    (tmp_path / "a").touch()
    (tmp_path / "b").touch()
    with pytest.raises(ValueError):
        probe.candidate_presence([str(tmp_path)], [".so"])
    result = probe.observe(probe.load_validator(), [str(tmp_path)], [".so"])
    assert result["probe_status"] == "UNAVAILABLE"
    assert result["system_admission"] == "NOT_RUN"


def test_runtime_gate_no_validator_execution(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        probe,
        "sys",
        SimpleNamespace(
            version_info=(3, 12),
            implementation=SimpleNamespace(name="cpython"),
            flags=SimpleNamespace(isolated=True, no_site=True),
            dont_write_bytecode=True,
        ),
    )
    monkeypatch.setattr(
        probe, "load_validator", lambda: pytest.fail("unsupported runtime executed validator")
    )
    value, code = probe.main()
    assert code == 78 and value == probe.initial()


@pytest.mark.parametrize("corrupt", [None, "extra", "enum", "claim", "contradiction"])
def test_real_probe_result_to_inline_publication(tmp_path, corrupt):
    tmp_path.chmod(0o700)
    system = tmp_path / "synthetic-system"
    system.mkdir()
    result = probe.observe(probe.load_validator(), [str(system)], [".so"])
    if corrupt == "extra":
        result["private"] = "SECRET"
    elif corrupt == "enum":
        result["system_candidates"] = "SECRET"
    elif corrupt == "claim":
        result["historical_cause"] = "CONFIRMED"
    elif corrupt == "contradiction":
        result["runtime"] = "UNSUPPORTED"
    path = tmp_path / "observation.json"
    path.write_text(json.dumps(result) + "\n")
    path.chmod(0o600)
    workflow = yaml.safe_load((ROOT / ".github/workflows/cli-capture-diagnostic.yml").read_text())
    steps = workflow["jobs"]["startup_probe"]["steps"]
    shell = next(step["run"] for step in steps if step.get("id") == "publication")
    code = shell.split("<<'PY'\n", 1)[1].split("\nPY", 1)[0]
    process = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-", str(tmp_path)],
        input=code,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert (process.returncode == 0) == (corrupt is None)
    assert (tmp_path / "published.json").exists() == (corrupt is None)


def test_workflow_only_explicit_probe():
    source = (ROOT / ".github/workflows/cli-capture-diagnostic.yml").read_text()
    workflow = yaml.safe_load(source)
    assert set(workflow["jobs"]) == {"startup_probe"}
    assert "default: false" in source and '"$EXPECTED" == "$DISPATCH"' in source
    assert "/usr/bin/python3 -I -S -B scripts/temporary_startup_probe.py" in source
    assert "pip " not in source and "capture_installed_cli.py" not in source
    assert "path: ${{ runner.temp }}/forge-startup-probe/published.json" in source


@pytest.mark.parametrize("index", [0, 1, 2])
def test_incomplete_admission_cannot_be_complete(tmp_path, monkeypatch, index):
    results = ["ACCEPTED", "ACCEPTED", "REJECTED"]
    results[index] = "UNAVAILABLE"
    iterator = iter(results)
    monkeypatch.setattr(probe, "admission", lambda *args: next(iterator))
    result = probe.observe(probe.load_validator(), [str(tmp_path)], [".so"])
    assert result["probe_status"] == "UNAVAILABLE"
