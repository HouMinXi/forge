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


def status_publisher():
    workflow = yaml.safe_load((ROOT / ".github/workflows/installed-wheel.yml").read_text())
    steps = next(
        job["steps"]
        for job in workflow["jobs"].values()
        if any(step.get("id") == "no_site_control" for step in job.get("steps", []))
    )
    shell = next(
        step["run"]
        for step in steps
        if step.get("name") == "Publish only validated no-site control stage"
    )
    return shell.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]


def publish_status(tmp_path, raw, code=73):
    import subprocess
    import sys

    private = tmp_path / "forge-no-site-status.ABCDEFGH"
    private.mkdir(mode=0o700)
    path = private / "status.json"
    path.write_bytes(raw)
    path.chmod(0o600)
    return subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", status_publisher()],
        env={"RUNNER_TEMP": str(tmp_path), "CONTROL_EXIT": str(code)},
        capture_output=True,
    )


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "extra",
        "path",
        "bool",
        "nan",
        "duplicate",
        "contradiction",
        "wrong-stage",
        "nonzero-success",
    ],
)
def test_actual_workflow_stage_publisher_closed(tmp_path, fault):
    import json

    row = {
        "schema": "forge-no-site-control-v1",
        "stage": "VERIFY_INSTALLATION",
        "category": "VERIFY_TARGET_METADATA",
        "child_exit": None,
    }
    code = 73
    if fault == "extra":
        row["secret"] = "/PRIVATE_CANARY"
    if fault == "path":
        row["category"] = "/PRIVATE_CANARY"
    if fault == "bool":
        row.update(stage="BASELINE_IMPORT", category="INVALID_DATA", child_exit=True)
    if fault == "nan":
        row["child_exit"] = float("nan")
    if fault == "contradiction":
        code = 0
    if fault == "wrong-stage":
        row["stage"] = "BASELINE_IMPORT"
    if fault == "nonzero-success":
        row.update(stage="COMPLETE", category="NONE", child_exit=0)
    raw = (json.dumps(row) + "\n").encode()
    if fault == "duplicate":
        raw = raw.replace(b'"child_exit": null', b'"child_exit": null, "child_exit": null')
    result = publish_status(tmp_path, raw, code)
    assert result.returncode == (0 if fault is None else 74)
    assert b"PRIVATE_CANARY" not in result.stdout + result.stderr
    if fault is not None:
        assert not result.stdout
    else:
        public = json.loads(result.stdout)
        assert public["control_shell_exit"] == 73 and public["qualification"] == "NOT_ASSESSED"


@pytest.mark.parametrize("fault", [None, "prepare", "import"])
def test_actual_child_classifier_workflow_pipeline(tmp_path, fault):
    """Synthetic helper/dependencies, actual CHILD source and independent workflow publisher."""
    import json
    import subprocess
    import sys

    control = load(ROOT / "scripts/check_no_site_installation.py")
    helper = tmp_path / "helper.py"
    site = tmp_path / "site"
    site.mkdir()
    source = """import sys,types, pathlib
FIXED_ARGS=[]
def prepare(binding,cache,args):
    if binding['fault']=='prepare': raise ValueError('/PRIVATE_CANARY')
    site=pathlib.Path(binding['startup_admission']['site_packages'])
    launcher=types.ModuleType('code_forge_launcher')
    launcher.__file__=str(site/'code_forge_launcher.py')
    def select(*args,**kwargs):
        if binding['fault']=='import': raise RuntimeError('/PRIVATE_CANARY')
        return types.SimpleNamespace(mode='installed',package_root=site/'code_forge')
    launcher.select_source=select
    launcher.bind_source=lambda source: None
    sys.modules['code_forge_launcher']=launcher
    for name in ('code_forge','yaml','unidiff'):
        module=types.ModuleType(name);module.__file__=str(site/name/'__init__.py');sys.modules[name]=module
    return '/synthetic/console',b'console'
"""
    helper.write_text(source)
    binding = {
        "fault": fault,
        "capture_hashes": {"no_site_entry.py": hashlib.sha256(source.encode()).hexdigest()},
        "startup_admission": {"site_packages": str(site)},
        "installed_provenance": {"launcher_sha256": hashlib.sha256(b"console").hexdigest()},
    }
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            "-c",
            control.CHILD,
            json.dumps(binding),
            str(tmp_path / "cache"),
            str(helper),
            "positive",
        ],
        cwd=tmp_path,
        capture_output=True,
    )
    assert result.returncode == (0 if fault is None else 1)
    assert not result.stderr and b"PRIVATE_CANARY" not in result.stdout
    receipt = control.child_observation(result.stdout, "positive", result.returncode)
    assert receipt["stage"] == ("BASELINE_PREPARE" if fault == "prepare" else "BASELINE_IMPORT")
    row = control.initial_observation()
    row.update(stage=receipt["stage"], category=receipt["category"], child_exit=result.returncode)
    if fault is None:
        row.update(stage="COMPLETE")
    published = publish_status(tmp_path, (json.dumps(row) + "\n").encode(), 0 if fault is None else 73)
    assert published.returncode == 0
    public = json.loads(published.stdout)
    assert public["child_exit"] == result.returncode and public["control_shell_exit"] == (
        0 if fault is None else 73
    )


@pytest.mark.parametrize(
    "fault", ["schema", "extra", "bool", "stage", "code", "success-prepare", "suffix", "duplicate"]
)
def test_child_receipt_rejects_contradictions(fault):
    import json

    control = load(ROOT / "scripts/check_no_site_installation.py")
    row = {"schema": 1, "stage": "BASELINE_IMPORT", "category": "NONE"}
    code = 0
    if fault == "schema":
        row["schema"] = True
    if fault == "extra":
        row["raw"] = "/PRIVATE_CANARY"
    if fault == "bool":
        code = True
    if fault == "stage":
        row["stage"] = "DIAGNOSTIC_IMPORT"
    if fault == "code":
        code = 1
    if fault == "success-prepare":
        row["stage"] = "BASELINE_PREPARE"
    raw = (json.dumps(row) + "\n").encode()
    if fault == "suffix":
        raw += b"/PRIVATE_CANARY"
    if fault == "duplicate":
        raw = raw.replace(b'"schema": 1', b'"schema":1,"schema":1')
    with pytest.raises(ValueError):
        control.child_observation(raw, "positive", code)


def test_classification_uses_exact_literals_and_preserves_control_code(monkeypatch, tmp_path):
    import json

    control = load(ROOT / "scripts/check_no_site_installation.py")

    def fail(evidence, sha, observation):
        observation["stage"] = "VERIFY_INSTALLATION"
        observation["category"] = "NONE"
        raise ValueError("target interpreter metadata unavailable")

    monkeypatch.setattr(control, "check", fail)
    out = tmp_path / "status.json"
    monkeypatch.setattr(
        control.sys,
        "argv",
        [
            "control",
            "--wheel-evidence",
            str(tmp_path),
            "--expected-source-sha",
            "a" * 40,
            "--status-output",
            str(out),
        ],
    )
    assert control.main() == 73
    assert out.stat().st_mode & 0o777 == 0o600
    assert json.loads(out.read_text())["category"] == "VERIFY_TARGET_METADATA"
    assert (
        control.error_category(
            ValueError("target interpreter metadata unavailable /PRIVATE_CANARY"), "VERIFY_INSTALLATION"
        )
        == "INVALID_DATA"
    )
    before = out.read_bytes()
    assert control.main() == 73
    assert out.read_bytes() == before


@pytest.mark.parametrize(
    "fault",
    [
        "symlink",
        "hardlink",
        "mode",
        "directory",
        "oversize",
        "parent-mode",
        "parent-link",
        "two-parents",
    ],
)
def test_actual_publisher_private_boundary(tmp_path, fault):
    import json
    import subprocess
    import sys

    private = tmp_path / "forge-no-site-status.ABCDEFGH"
    private.mkdir(mode=0o700)
    path = private / "status.json"
    raw = (
        json.dumps(
            {
                "schema": "forge-no-site-control-v1",
                "stage": "COMPLETE",
                "category": "NONE",
                "child_exit": 0,
            }
        )
        + "\n"
    ).encode()
    path.write_bytes(raw)
    path.chmod(0o600)
    if fault == "symlink":
        moved = tmp_path / "moved"
        path.rename(moved)
        path.symlink_to(moved)
    elif fault == "hardlink":
        os.link(path, tmp_path / "other")
    elif fault == "mode":
        path.chmod(0o644)
    elif fault == "directory":
        path.unlink()
        path.mkdir()
    elif fault == "oversize":
        path.write_bytes(b"x" * 2049)
    elif fault == "parent-mode":
        private.chmod(0o755)
    elif fault == "parent-link":
        moved = tmp_path / "moved"
        private.rename(moved)
        private.symlink_to(moved, target_is_directory=True)
    elif fault == "two-parents":
        (tmp_path / "forge-no-site-status.SECOND").mkdir(mode=0o700)
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", status_publisher()],
        env={"RUNNER_TEMP": str(tmp_path), "CONTROL_EXIT": "0"},
        capture_output=True,
    )
    assert result.returncode == 74 and not result.stdout


def test_workflow_preserves_control_failure_and_no_dynamic_path_environment():
    workflow = yaml.safe_load((ROOT / ".github/workflows/installed-wheel.yml").read_text())
    steps = next(
        job["steps"]
        for job in workflow["jobs"].values()
        if any(s.get("id") == "no_site_control" for s in job.get("steps", []))
    )
    control = next(s for s in steps if s.get("id") == "no_site_control")
    publisher = next(s for s in steps if s.get("name") == "Publish only validated no-site control stage")
    assert (
        'exit "$control_code"' in control["run"] and "control_shell_exit=$control_code" in control["run"]
    )
    assert "always()" in publisher["if"]
    assert set(publisher["env"]) == {"CONTROL_EXIT"}
    assert not control.get("continue-on-error") and not publisher.get("continue-on-error")


@pytest.mark.parametrize("control_fails", [False, True])
@pytest.mark.parametrize("delivery_fails", ["open", "write"])
def test_status_delivery_failure_preserves_original_exit(
    monkeypatch, tmp_path, control_fails, delivery_fails
):
    control = load(ROOT / "scripts/check_no_site_installation.py")

    def check(evidence, sha, observation):
        if control_fails:
            observation.update(stage="VERIFY_INSTALLATION", category="NONE")
            raise ValueError("target interpreter metadata unavailable")
        observation.update(stage="COMPLETE", category="NONE", child_exit=0)

    monkeypatch.setattr(control, "check", check)

    def fail(*args, **kwargs):
        raise OSError("/PRIVATE_CANARY")

    monkeypatch.setattr(control.os, "open" if delivery_fails == "open" else "write", fail)
    monkeypatch.setattr(
        control.sys,
        "argv",
        [
            "control",
            "--wheel-evidence",
            str(tmp_path),
            "--expected-source-sha",
            "a" * 40,
            "--status-output",
            str(tmp_path / "status"),
        ],
    )
    assert control.main() == (73 if control_fails else 0)
    monkeypatch.undo()
    import subprocess
    import sys

    publication = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", status_publisher()],
        env={"RUNNER_TEMP": str(tmp_path), "CONTROL_EXIT": "73" if control_fails else "0"},
        capture_output=True,
    )
    assert publication.returncode == 74 and not publication.stdout


def test_readonly_integration_not_an_unapproved_general_skip():
    import ast

    tree = ast.parse(Path(__file__).read_text())
    assert not any(
        isinstance(node, ast.FunctionDef) and node.name == "test_real_current_wheel_readonly_control"
        for node in ast.walk(tree)
    )
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"skip", "skipif"}
        for node in ast.walk(tree)
    )
    workflow = yaml.safe_load((ROOT / ".github/workflows/installed-wheel.yml").read_text())
    step = next(
        s for s in workflow["jobs"]["wheel-runtime"]["steps"] if s.get("id") == "no_site_control"
    )
    assert "matrix.runtime == 'py314'" in step["if"]
    assert "scripts/check_no_site_installation.py" in step["run"]
    assert not step.get("continue-on-error")


def test_real_shard_validator_rejects_removed_unapproved_integration_skip():
    import json

    shard = load(ROOT / "scripts/ci_test_shards.py")
    policy = json.loads((ROOT / "scripts/ci_test_policy.json").read_text())
    node = "tests/test_cli_no_site.py::test_real_current_wheel_readonly_control"
    phase = "full"
    receipt = {
        "sha": "a" * 40,
        "phase": phase,
        "exitstatus": 0,
        "collection_errors": [],
        "collected": [node],
        "collection_skips": [],
        "collection_skip_reasons": {},
        "subtests": [],
        "reports": [
            {
                "nodeid": node,
                "when": "setup",
                "outcome": "skipped",
                "skip_reason": "real CPython3.14 unavailable; no runtime qualification",
            }
        ],
    }
    plan = {"sha": "a" * 40, "phases": {phase: {"nodes": [node], "collection_skips": []}}}
    with pytest.raises(ValueError, match="Unexpected runtime skip"):
        shard.validate_execution(receipt, phase, plan, policy)


@pytest.mark.parametrize(
    "stage,accepted", [("VERIFY_INSTALLATION", True), ("CHILD_SETUP", True), ("SOURCE_IDENTITY", False)]
)
def test_metadata_timeout_main_to_actual_publisher(monkeypatch, tmp_path, stage, accepted):
    import json
    import subprocess
    import sys

    control = load(ROOT / "scripts/check_no_site_installation.py")
    private = tmp_path / "forge-no-site-status.ABCDEFGH"
    private.mkdir(mode=0o700)
    path = private / "status.json"

    def timeout(evidence, sha, observation):
        observation.update(stage=stage, category="NONE", child_exit=None)
        raise subprocess.TimeoutExpired("/PRIVATE_CANARY", 5, output=b"PRIVATE_RAW")

    monkeypatch.setattr(control, "check", timeout)
    monkeypatch.setattr(
        control.sys,
        "argv",
        [
            "control",
            "--wheel-evidence",
            str(tmp_path),
            "--expected-source-sha",
            "a" * 40,
            "--status-output",
            str(path),
        ],
    )
    assert control.main() == 73
    row = json.loads(path.read_text())
    assert row["category"] == "TIMEOUT" and row["child_exit"] is None
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", status_publisher()],
        env={"RUNNER_TEMP": str(tmp_path), "CONTROL_EXIT": "73"},
        capture_output=True,
    )
    assert result.returncode == (0 if accepted else 74)
    assert b"PRIVATE" not in result.stdout + result.stderr
    if accepted:
        public = json.loads(result.stdout)
        assert public["control_shell_exit"] == 73 and public["stage"] == stage
    else:
        assert not result.stdout
