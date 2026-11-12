"""Controlled-startup unit contracts; mocked 3.14 is never runtime qualification."""

import copy
import hashlib
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixture_module = load(ROOT / "tests/test_cli_capture_entrypoint.py")
evidence = fixture_module.evidence
controller = fixture_module.entry
worker = load(ROOT / "qualification/installed_cli/capture_worker.py")
entry = load(ROOT / "qualification/installed_cli/no_site_entry.py")


@pytest.fixture
def prepared(evidence, monkeypatch):
    binding, _, _ = controller.verify_installation(evidence.root)
    binding.update(backend="stock-bwrap", startup_mode="isolated-no-site", mode="baseline")
    binding["target"].update(version_info=[3, 14], prefix=str(evidence.prefix))
    spec = binding["startup_admission"]
    spec.update(startup_mode="isolated-no-site", no_site_system_roots=binding["target"]["stdlib_paths"])
    spec["observed"] = worker.admit_startup(spec)
    binding["capture_hashes"] = {
        name: hashlib.sha256((ROOT / "qualification/installed_cli" / name).read_bytes()).hexdigest()
        for name in ("capture_worker.py", "no_site_entry.py", "diagnostic_probe.py")
    }
    cache = evidence.root / "cache"
    cache.mkdir(mode=0o700)
    fake = SimpleNamespace(
        implementation=SimpleNamespace(name="cpython"),
        version_info=(3, 14),
        flags=SimpleNamespace(isolated=True, no_site=True),
        dont_write_bytecode=True,
        pycache_prefix=str(cache),
        executable=str(evidence.target),
        prefix=str(evidence.prefix),
        path=list(binding["target"]["stdlib_paths"]),
    )
    monkeypatch.setattr(entry, "sys", fake)
    return binding, cache


def test_prepare_preserves_verified_console_bytes_and_one_site(prepared):
    binding, cache = prepared
    launcher, raw = entry.prepare(binding, str(cache), entry.FIXED_ARGS)
    assert launcher == binding["launcher"] and raw == Path(launcher).read_bytes()
    assert entry.sys.path == [
        *binding["target"]["stdlib_paths"],
        binding["startup_admission"]["site_packages"],
    ]


@pytest.mark.parametrize(
    "fault",
    [
        "runtime",
        "flags",
        "prefix",
        "path",
        "cache",
        "roots",
        "console",
        "binary",
        "argv",
        "helper",
        "populated_cache",
    ],
)
def test_prepare_rejects_changed_contract(prepared, fault):
    binding, cache = prepared
    args = list(entry.FIXED_ARGS)
    if fault == "runtime":
        entry.sys.version_info = (3, 12)
    elif fault == "flags":
        entry.sys.flags.no_site = False
    elif fault == "prefix":
        entry.sys.prefix = "/wrong"
    elif fault == "path":
        entry.sys.path.append("/wrong")
    elif fault == "cache":
        entry.sys.pycache_prefix = None
    elif fault == "roots":
        binding["startup_admission"]["no_site_system_roots"] = []
    elif fault == "console":
        binding["installed_provenance"]["launcher_sha256"] = "0" * 64
    elif fault == "binary":
        binding["target"]["sha256"] = "0" * 64
    elif fault == "argv":
        args = ["--help"]
    elif fault == "helper":
        binding["capture_hashes"]["capture_worker.py"] = "0" * 64
    elif fault == "populated_cache":
        (cache / "stale.pyc").write_bytes(b"OLD_BYTECODE")
    with pytest.raises(ValueError):
        entry.prepare(binding, str(cache), args)


def test_diagnostic_fd_noninheritability_before_probe(prepared, monkeypatch, tmp_path):
    binding, cache = prepared
    binding["mode"] = "diagnostic"
    raw = b"import os\ndef install(fd):\n    assert not os.get_inheritable(fd)\n    os.write(fd, b'PROBE_INSTALLED')\n"
    binding["capture_hashes"]["diagnostic_probe.py"] = hashlib.sha256(raw).hexdigest()
    original = entry.read
    monkeypatch.setattr(
        entry,
        "read",
        lambda path, *args: raw if Path(path).name == "diagnostic_probe.py" else original(path, *args),
    )
    fd = os.open(tmp_path / "events", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.set_inheritable(fd, True)
        monkeypatch.setenv("FORGE_CLI_DIAGNOSTIC_FD", str(fd))
        entry.prepare(binding, str(cache), entry.FIXED_ARGS)
        assert not os.get_inheritable(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        assert os.read(fd, 100) == b"PROBE_INSTALLED"
        assert "FORGE_CLI_DIAGNOSTIC_FD" not in os.environ
    finally:
        os.close(fd)


def test_system_hook_inert_only_new_policy_launcher_shadow_still_rejected(evidence):
    binding, _, _ = controller.verify_installation(evidence.root)
    spec = binding["startup_admission"]
    hook = evidence.root / "stdlib/sitecustomize.py"
    hook.write_text("raise RuntimeError('MUST_NOT_EXECUTE')")
    with pytest.raises(ValueError):
        worker.admit_startup(spec)
    new = copy.deepcopy(spec)
    new.update(startup_mode="isolated-no-site", no_site_system_roots=binding["target"]["stdlib_paths"])
    worker.admit_startup(new)
    (hook.parent / "code_forge_launcher.py").write_text("raise RuntimeError('SHADOW')")
    with pytest.raises(ValueError):
        worker.admit_startup(new)


def test_exact_launch_flags_and_source_hash(prepared):
    binding, cache = prepared
    source = (ROOT / "qualification/installed_cli/no_site_entry.py").read_bytes()
    command = entry.command(
        binding, "/private/binding.json", cache, [binding["launcher"], *entry.FIXED_ARGS], source
    )
    assert command[1:5] == ["-I", "-S", "-B", "-X"]
    assert command[5] == "pycache_prefix=" + str(cache)
    with pytest.raises(ValueError):
        entry.command(
            binding,
            "/private/binding.json",
            cache,
            [binding["launcher"], *entry.FIXED_ARGS],
            source + b"\n",
        )


def test_workflow_controlled_controller_flags_and_readonly_ci_control():
    source = (
        (ROOT / ".github/workflows/cli-capture-diagnostic.yml")
        .read_text()
        .split("  stock_cli_capture:", 1)[1]
    )
    assert source.count("startup_flags=(-I -S -B)") == 2
    assert source.count('--startup-mode "$STARTUP_MODE"') == 2
    workflow = yaml.safe_load((ROOT / ".github/workflows/installed-wheel.yml").read_text())
    step = next(
        s for s in workflow["jobs"]["wheel-runtime"]["steps"] if s.get("id") == "no_site_control"
    )
    assert "matrix.runtime == 'py314'" in step["if"]
    assert "python -I -S -B scripts/check_no_site_installation.py" in step["run"]
    assert "continue-on-error" not in step


@pytest.mark.skipif(
    __import__("sys").version_info[:2] != (3, 14),
    reason="real CPython3.14 unavailable; no runtime qualification",
)
def test_real_current_wheel_readonly_control():
    path = os.environ.get("FORGE_NO_SITE_WHEEL_EVIDENCE")
    sha = os.environ.get("FORGE_NO_SITE_SOURCE_SHA")
    if not path or not sha:
        pytest.skip("fresh wheel evidence not supplied; dedicated py314 CI step is mandatory")
    load(ROOT / "scripts/check_no_site_installation.py").check(Path(path), sha)


@pytest.mark.parametrize("fault", ["missing_fd", "probe_hash"])
def test_diagnostic_missing_or_changed_probe_stops_before_console(
    prepared, monkeypatch, tmp_path, fault
):
    binding, cache = prepared
    binding["mode"] = "diagnostic"
    fd = os.open(tmp_path / "events", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        monkeypatch.delenv("FORGE_CLI_DIAGNOSTIC_FD", raising=False)
        if fault == "probe_hash":
            monkeypatch.setenv("FORGE_CLI_DIAGNOSTIC_FD", str(fd))
            binding["capture_hashes"]["diagnostic_probe.py"] = "0" * 64
        with pytest.raises(ValueError):
            entry.prepare(binding, str(cache), entry.FIXED_ARGS)
        assert os.fstat(fd).st_size == 0
    finally:
        os.close(fd)


def test_no_site_worker_bootstrap_also_suppresses_site(monkeypatch):
    binding = {
        "backend": "stock-bwrap",
        "startup_mode": "isolated-no-site",
        "capture_hashes": {"capture_worker.py": "a" * 64, "capture_stock.py": "b" * 64},
    }
    monkeypatch.setattr(
        controller, "stock_helper", lambda: (SimpleNamespace(command=lambda b, p: p), "b" * 64)
    )
    argv = controller.worker_command(
        binding,
        "baseline",
        Path("/private/worker"),
        Path("/helper.py"),
        Path("/private/binding.json"),
        "# bound",
    )
    assert argv[1:4] == ["-I", "-S", "-B"]


@pytest.mark.parametrize("kind", ["system_cache", "site_hook", "zip_shadow"])
def test_no_site_candidate_boundaries(evidence, kind):
    import zipfile

    binding, _, _ = controller.verify_installation(evidence.root)
    spec = binding["startup_admission"]
    spec.update(startup_mode="isolated-no-site", no_site_system_roots=binding["target"]["stdlib_paths"])
    if kind == "system_cache":
        cache = evidence.root / "stdlib/__pycache__"
        cache.mkdir()
        (cache / "sitecustomize.cpython-314.pyc").write_bytes(b"not executable")
        worker.admit_startup(spec)
    else:
        if kind == "site_hook":
            (evidence.site / "sitecustomize.py").write_text("raise RuntimeError('MUST_NOT_EXECUTE')")
        else:
            with zipfile.ZipFile(evidence.root / "python312.zip", "w") as archive:
                archive.writestr("code_forge_launcher.py", "raise RuntimeError('ZIP_SHADOW')")
        with pytest.raises(ValueError):
            worker.admit_startup(spec)


def test_diagnostic_no_site_does_not_create_or_export_startup_hook(monkeypatch, tmp_path):
    from contextlib import ExitStack

    diagnostic = load(ROOT / "qualification/installed_cli/diagnostic_run.py")
    calls = []

    def fake_popen(argv, **kwargs):
        calls.append((argv, kwargs))
        raise RuntimeError("stop before launching")

    monkeypatch.setattr(diagnostic.shutil, "which", lambda *a, **k: "/usr/bin/strace")
    monkeypatch.setattr(diagnostic.subprocess, "Popen", fake_popen)
    output = tmp_path / "diagnostic"
    with pytest.raises(RuntimeError), ExitStack() as stack:
        stack.enter_context(
            diagnostic.traced_child(
                ["/python", "-I", "-S", "-B", "-X", "pycache_prefix=/cache", "-c", "bound"],
                case_id="normal",
                cwd=tmp_path,
                env={},
                output=output,
                stdout=-1,
                stderr=-1,
                startup_mode="isolated-no-site",
            )
        )
    assert calls and "PYTHONPATH" not in calls[0][1]["env"]
    assert not (output / "shim/sitecustomize.py").exists()


@pytest.mark.parametrize("code", [0, 7, 73])
def test_execute_original_bytes_exit_and_console_globals(prepared, code):
    binding, cache = prepared
    launcher = Path(binding["launcher"])
    raw = (
        "assert __name__ == '__main__'\nassert __file__ == "
        + repr(str(launcher))
        + "\nraise SystemExit("
        + str(code)
        + ")\n"
    ).encode()
    launcher.write_bytes(raw)
    binding["installed_provenance"]["launcher_sha256"] = hashlib.sha256(raw).hexdigest()
    entry.sys.modules = {}
    with pytest.raises(SystemExit) as caught:
        entry.execute(binding, str(cache), entry.FIXED_ARGS)
    assert caught.value.code == code
    assert entry.sys.argv == [str(launcher), *entry.FIXED_ARGS]
    assert entry.sys.modules["__main__"].__file__ == str(launcher)


def test_probe_install_failure_cannot_fall_back(prepared, monkeypatch, tmp_path):
    binding, cache = prepared
    binding["mode"] = "diagnostic"
    raw = b"def install(fd):\n    raise RuntimeError('installation failed')\n"
    binding["capture_hashes"]["diagnostic_probe.py"] = hashlib.sha256(raw).hexdigest()
    original = entry.read
    monkeypatch.setattr(
        entry,
        "read",
        lambda path, *args: raw if Path(path).name == "diagnostic_probe.py" else original(path, *args),
    )
    fd = os.open(tmp_path / "events", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        monkeypatch.setenv("FORGE_CLI_DIAGNOSTIC_FD", str(fd))
        with pytest.raises(RuntimeError, match="installation failed"):
            entry.execute(binding, str(cache), entry.FIXED_ARGS)
    finally:
        os.close(fd)


def test_readonly_control_runtime_gap_is_nonzero_not_success(monkeypatch, tmp_path):
    control = load(ROOT / "scripts/check_no_site_installation.py")
    monkeypatch.setattr(control, "sys", SimpleNamespace(version_info=(3, 12)))
    with pytest.raises(ValueError, match="required runtime unavailable"):
        control.check(tmp_path, "a" * 40)


@pytest.mark.parametrize("fault", [None, "missing", "sequence", "error"])
def test_readonly_probe_terminal_framing(fault):
    import json

    control = load(ROOT / "scripts/check_no_site_installation.py")
    rows = [
        {"schema": 1, "seq": 1, "event": "diagnostic_start"},
        {"schema": 1, "seq": 2, "event": "diagnostic_footer", "complete": False},
    ]
    if fault == "missing":
        rows.pop()
    elif fault == "sequence":
        rows[-1]["seq"] = 4
    elif fault == "error":
        rows[0]["event"] = "diagnostic_error"
    raw = b"".join((json.dumps(row) + "\n").encode() for row in rows)
    if fault is None:
        control.check_probe_receipt(raw)
    else:
        with pytest.raises(ValueError):
            control.check_probe_receipt(raw)


@pytest.mark.parametrize("fault", ["mode", "symlink"])
def test_private_cache_identity_rechecked_in_child(prepared, fault):
    binding, cache = prepared
    if fault == "mode":
        cache.chmod(0o755)
    else:
        other = cache.with_name("other-cache")
        cache.rename(other)
        cache.symlink_to(other, target_is_directory=True)
    with pytest.raises(ValueError):
        entry.prepare(binding, str(cache), entry.FIXED_ARGS)
