"""Synthetic stock capture admission: never execute namespaces, tracer or product."""

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stock = load(ROOT / "qualification/installed_cli/capture_stock.py")
worker = load(ROOT / "qualification/installed_cli/capture_worker.py")
controller = load(ROOT / "scripts/capture_installed_cli.py")
publication = load(ROOT / "scripts/cli_capture_stock_publication.py")


@pytest.fixture
def binding(tmp_path):
    raw = b"fixed"
    h = hashlib.sha256(raw).hexdigest()
    programs = {
        name: {"path": path, "sha256": h}
        for name, path in {
            "bwrap": "/usr/bin/bwrap",
            "python": str(Path("/usr/bin/python3").resolve()),
            "strace": "/usr/bin/strace",
        }.items()
    }
    return dict(
        backend="stock-bwrap",
        launcher="/opt/installed/bin/code-forge",
        startup_admission={
            "roots": ["/opt/installed/lib"],
            "site_packages": "/opt/installed/lib",
            "venv_config_path": "/opt/installed/pyvenv.cfg",
        },
        host_netns="net:[1]",
        target_python="/usr/bin/python3",
        target={"version_info": [3, 14], "sha256": h},
        utility_identities={"strace": programs["strace"]},
        stock=dict(
            mode="diagnostic",
            profile_sha256=h,
            parent_namespaces={n: f"{n}:[1]" for n in stock.NAMES},
            programs=programs,
            output=str(tmp_path),
            output_identity=dict(dev=1, ino=2, owner="effective_user", mode=0o700),
        ),
    )


@pytest.fixture
def child_gate(binding, monkeypatch):
    monkeypatch.setattr(
        stock, "sys", SimpleNamespace(implementation=SimpleNamespace(cache_tag="cpython-314"))
    )
    monkeypatch.setattr(stock, "host_state", lambda **kw: binding["stock"]["profile_sha256"])
    monkeypatch.setattr(stock, "namespaces", lambda: {n: f"{n}:[2]" for n in stock.NAMES})
    monkeypatch.setattr(stock, "directory", lambda p: binding["stock"]["output_identity"])
    monkeypatch.setattr(stock, "readonly_loopback", lambda: None)

    def read(path, *a):
        if str(path) == "/proc/self/attr/current":
            return b"bwrap//&unpriv_bwrap (enforce)"
        if str(path) == "/proc/self/status":
            return b"CapEff:0000000000000000\nCapPrm:0000000000000000\nCapAmb:0000000000000000\nNoNewPrivs:1"
        return b"fixed"

    monkeypatch.setattr(stock, "read", read)
    monkeypatch.setattr(
        stock.os,
        "statvfs",
        lambda p: SimpleNamespace(f_flag=0 if str(p) == binding["stock"]["output"] else os.ST_RDONLY),
    )
    return binding


def test_child_rechecks_readonly_root_output_binding(child_gate):
    stock.child(child_gate)


@pytest.mark.parametrize("mount", ["root", "output", "binding"])
def test_child_rejects_wrong_mount_flags(child_gate, monkeypatch, mount):
    output = child_gate["stock"]["output"]

    def flags(path):
        value = 0 if str(path) == output else os.ST_RDONLY
        if (
            (mount == "root" and str(path) == "/")
            or (mount == "output" and str(path) == output)
            or (mount == "binding" and str(path) == output + "/binding.json")
        ):
            value ^= os.ST_RDONLY
        return SimpleNamespace(f_flag=value)

    monkeypatch.setattr(stock.os, "statvfs", flags)
    with pytest.raises(ValueError):
        stock.child(child_gate)


@pytest.mark.parametrize(
    "fault", ["profile", "caps", "namespace", "hash", "runtime", "directory", "host"]
)
def test_child_rejects_unsafe_state(child_gate, monkeypatch, fault):
    original = stock.read
    if fault == "profile":
        monkeypatch.setattr(
            stock,
            "read",
            lambda p, *a: b"unconfined" if str(p) == "/proc/self/attr/current" else original(p, *a),
        )
    if fault == "caps":
        monkeypatch.setattr(
            stock,
            "read",
            lambda p, *a: original(p, *a).replace(
                b"CapEff:0000000000000000", b"CapEff:0000000000000001"
            ),
        )
    if fault == "namespace":
        monkeypatch.setattr(stock, "namespaces", lambda: child_gate["stock"]["parent_namespaces"])
    if fault == "hash":
        monkeypatch.setattr(
            stock, "read", lambda p, *a: original(p, *a) if str(p).startswith("/proc/") else b"changed"
        )
    if fault == "runtime":
        monkeypatch.setattr(stock.sys.implementation, "cache_tag", "cpython-312")
    if fault == "directory":
        monkeypatch.setattr(
            stock, "directory", lambda p: dict(child_gate["stock"]["output_identity"], ino=9)
        )
    if fault == "host":
        monkeypatch.setattr(stock, "host_state", lambda **kw: "changed")
    with pytest.raises(ValueError):
        stock.child(child_gate)


@pytest.mark.parametrize("fault", ["extra", "program", "hash", "mode", "identity", "parent", "target"])
def test_stock_binding_schema_closed(binding, fault):
    if fault == "extra":
        binding["stock"]["extra"] = "secret"
    if fault == "program":
        binding["stock"]["programs"]["other"] = {}
    if fault == "hash":
        binding["stock"]["programs"]["bwrap"]["sha256"] = "secret"
    if fault == "mode":
        binding["stock"]["mode"] = "other"
    if fault == "identity":
        binding["stock"]["output_identity"]["ino"] = True
    if fault == "parent":
        binding["stock"]["parent_namespaces"]["net"] = "net:[99]"
    if fault == "target":
        binding["target"]["sha256"] = "0" * 64
    with pytest.raises(ValueError):
        stock.validate(binding)


def test_command_only_current_output_writable(binding, monkeypatch):
    monkeypatch.setattr(stock, "directory", lambda p: binding["stock"]["output_identity"])
    monkeypatch.setattr(stock, "host_state", lambda **kw: binding["stock"]["profile_sha256"])
    monkeypatch.setattr(stock, "root_program", lambda p: binding["stock"]["programs"]["bwrap"]["sha256"])
    argv = stock.command(binding, ["fixed", "payload"])
    assert argv.count("--bind") == 1
    index = argv.index("--bind")
    assert argv[index + 1 : index + 3] == [binding["stock"]["output"]] * 2
    assert argv[argv.index("--ro-bind") + 1 : argv.index("--ro-bind") + 3] == ["/", "/"]
    assert argv[-2:] == ["fixed", "payload"]
    assert [binding["stock"]["output"] + "/binding.json"] * 2 == argv[-5:-3]


@pytest.mark.parametrize("unsafe", ["down", "other_interface"])
def test_loopback_readonly_rejects_no_repair(monkeypatch, unsafe):
    monkeypatch.setattr(
        stock.socket,
        "if_nameindex",
        lambda: [(1, "lo"), (2, "eth0")] if unsafe == "other_interface" else [(1, "lo")],
    )

    class Handle:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def fileno(self):
            return 1

    monkeypatch.setattr(stock.socket, "socket", lambda *a: Handle())

    def ioctl(fd, request, arg):
        assert request == 0x8913
        return b"x" * 16 + struct.pack("H", 8)

    monkeypatch.setattr(stock.fcntl, "ioctl", ioctl)
    with pytest.raises(ValueError):
        stock.readonly_loopback()


@pytest.mark.parametrize("deny", [False, True])
def test_stock_gate_before_target_startup(monkeypatch, deny):
    calls = []
    path = str(Path("/usr/bin/python3").resolve())
    names = (*worker.CAPTURE_FILES, "capture_stock.py")
    hashes = {
        n: hashlib.sha256((ROOT / "qualification/installed_cli" / n).read_bytes()).hexdigest()
        for n in names
    }
    value = dict(
        schema=1,
        backend="stock-bwrap",
        launcher=path,
        target_python=path,
        git=path,
        installed_provenance={},
        capture_hashes=hashes,
        host_netns="net:[1]",
        startup_admission={"observed": []},
    )

    def guard(binding):
        calls.append("guard")
        if deny:
            raise ValueError("denied")

    monkeypatch.setattr(worker, "_module", lambda n: SimpleNamespace(child=guard))
    monkeypatch.setattr(worker, "admit_startup", lambda b: calls.append("startup") or [])
    if deny:
        with pytest.raises(ValueError):
            worker._verify_binding(value)
        assert calls == ["guard"]
    else:
        worker._verify_binding(value)
        assert calls == ["guard", "startup"]


def test_stock_isolate_never_runs_ip(monkeypatch):
    calls = []
    monkeypatch.setattr(
        worker, "_module", lambda n: SimpleNamespace(child=lambda b: calls.append("readonly"))
    )
    monkeypatch.setattr(worker, "_run_setup", lambda *a, **k: pytest.fail("network mutation"))
    worker._isolate({"backend": "stock-bwrap"}, {}, Path("/unused"))
    assert calls == ["readonly"]


def test_context_and_summary_publication(tmp_path):
    tmp_path.chmod(0o700)
    value = dict(
        schema="forge-stock-cli-capability-v1",
        status="PRESENT_NOT_QUALIFIED",
        facts=dict.fromkeys(["linux", "pidfd_bindings", "git", "bwrap", "strace"], True),
        live_admission="NOT_RUN",
        qualification="NOT_ASSESSED",
        raw_evidence="NOT_PUBLISHED",
    )
    for name, record in [("context.json", publication.CONTEXT), ("capability.json", value)]:
        p = tmp_path / name
        p.write_text(json.dumps(record) + "\n")
        p.chmod(0o600)
    publication.publish(tmp_path, tmp_path / "published")
    assert json.loads((tmp_path / "published/context.json").read_text()) == publication.CONTEXT


@pytest.mark.parametrize("mutation", ["extra", "backend", "runtime", "raw"])
def test_context_rejects_unreviewed_claims(tmp_path, mutation):
    tmp_path.chmod(0o700)
    value = copy.deepcopy(publication.CONTEXT)
    value[
        {"extra": "extra", "backend": "backend", "runtime": "required_runtime", "raw": "raw"}[mutation]
    ] = "secret"
    path = tmp_path / "context.json"
    path.write_text(json.dumps(value) + "\n")
    path.chmod(0o600)
    cap = dict(
        schema="forge-stock-cli-capability-v1",
        status="PRESENT_NOT_QUALIFIED",
        facts=dict.fromkeys(publication.FACTS, True),
        live_admission="NOT_RUN",
        qualification="NOT_ASSESSED",
        raw_evidence="NOT_PUBLISHED",
    )
    cap_path = tmp_path / "capability.json"
    cap_path.write_text(json.dumps(cap) + "\n")
    cap_path.chmod(0o600)
    with pytest.raises(ValueError):
        publication.publish(tmp_path, tmp_path / "published")


def test_workflow_stock_explicit_and_independent():
    source = (ROOT / ".github/workflows/cli-capture-diagnostic.yml").read_text()
    job = source.split("  stock_cli_capture:")[1]
    assert "runs-on: ubuntu-26.04" in job and "inputs.run_stock_cli_capture" in job
    assert job.count("scripts/capture_installed_cli.py --backend stock-bwrap") == 2
    assert "scripts/cli_capture_stock_publication.py" in job
    assert "published/context.json" in job
    assert "python scripts/qualify_installed_wheel.py" not in job
    assert "/usr/bin/python3 scripts/qualify_installed_wheel.py" in job
    assert (
        "default: false"
        in source.split("      run_stock_cli_capture:")[1].split("      expected_source_sha:")[0]
    )


@pytest.mark.parametrize("field", ["site_packages", "venv_config_path", "roots"])
def test_target_startup_inputs_cannot_be_writable(binding, field):
    path = binding["stock"]["output"] + "/untrusted"
    binding["startup_admission"][field] = [path] if field == "roots" else path
    with pytest.raises(ValueError):
        stock.validate(binding)


def test_stock_capability_does_not_invent_unshare_or_ip_facts():
    value = dict(
        schema="forge-stock-cli-capability-v1",
        status="PRESENT_NOT_QUALIFIED",
        facts=dict.fromkeys(publication.FACTS, True),
        live_admission="NOT_RUN",
        qualification="NOT_ASSESSED",
        raw_evidence="NOT_PUBLISHED",
    )
    assert publication.capability(value) == value
    value["facts"]["unshare"] = True
    with pytest.raises(ValueError):
        publication.capability(value)


def test_child_profile_owner_may_be_namespace_mapped(monkeypatch):
    profile = SimpleNamespace(lstat=lambda: SimpleNamespace(st_mode=0o100644, st_uid=65534))
    monkeypatch.setattr(stock, "PROFILE", profile)
    monkeypatch.setattr(
        stock,
        "read",
        lambda p, *a: (
            b"Y"
            if str(p).endswith("enabled")
            else b"1"
            if str(p).endswith("userns")
            else b"trusted profile"
        ),
    )
    assert stock.host_state(host_owner=False) == hashlib.sha256(b"trusted profile").hexdigest()
    with pytest.raises(ValueError):
        stock.host_state()
    profile.lstat = lambda: SimpleNamespace(st_mode=0o100666, st_uid=65534)
    with pytest.raises(ValueError):
        stock.host_state(host_owner=False)


def test_complete_trace_denial_stops_before_real_cli(monkeypatch, tmp_path):
    monkeypatch.setattr(worker, "_verify_binding", lambda b: None)
    monkeypatch.setattr(worker, "_limits", lambda: None)
    monkeypatch.setattr(worker, "_environment", lambda *a: {})
    monkeypatch.setattr(worker, "_isolate", lambda *a: None)

    def denied(*args):
        raise RuntimeError("full trace unavailable")

    monkeypatch.setattr(worker, "_diagnostic_capability", denied)
    monkeypatch.setattr(worker, "_Peer", lambda *a: pytest.fail("peer/CLI setup after denial"))
    result = worker.execute(
        {"backend": "stock-bwrap", "stock": {"mode": "diagnostic"}}, "diagnostic", tmp_path
    )
    assert result["child_pid"] is None and result["child_returncode"] is None
    assert result["primary_exception"]["type"] == "RuntimeError"


def test_namespace_local_pid_not_host_pid_for_trace(tmp_path, monkeypatch):
    collector = load(ROOT / "qualification/installed_cli/diagnostic_run.py")
    path = tmp_path / "syscalls.log"
    path.write_bytes(b"17 123456.789 +++ exited with 7 +++\n")
    assert collector._terminal_observed(path, 17, timeout=0)
    assert not collector._terminal_observed(path, 9999, timeout=0)


def test_stock_mode_mismatch_stops_before_binding_admission(monkeypatch, tmp_path, binding):
    monkeypatch.setattr(worker, "_verify_binding", lambda b: pytest.fail("mismatched mode admitted"))
    binding["stock"]["mode"] = "baseline"
    binding["stock"]["programs"].pop("strace")
    stock.validate(binding)
    result = worker.execute(binding, "diagnostic", tmp_path)
    assert result["child_pid"] is None and result["primary_exception"]["type"] == "ValueError"


def test_context_not_published_when_capture_envelope_invalid(tmp_path):
    tmp_path.chmod(0o700)
    for name, value in (
        ("context.json", publication.CONTEXT),
        ("capability.json", {"secret": "invalid"}),
    ):
        path = tmp_path / name
        path.write_text(json.dumps(value) + "\n")
        path.chmod(0o600)
    with pytest.raises(ValueError):
        publication.publish(tmp_path, tmp_path / "published")
    assert not (tmp_path / "published/context.json").exists()


def test_nested_target_mount_must_be_readonly(child_gate, monkeypatch):
    original = stock.os.statvfs
    target = str(Path(child_gate["target_python"]))
    monkeypatch.setattr(
        stock.os, "statvfs", lambda p: SimpleNamespace(f_flag=0) if str(p) == target else original(p)
    )
    with pytest.raises(ValueError):
        stock.child(child_gate)


def test_workflow_artifact_names_are_distinct():
    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/cli-capture-diagnostic.yml").read_text())
    names = [
        step["with"]["name"]
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if step.get("uses", "").startswith("actions/upload-artifact@")
    ]
    assert len(names) == len(set(names))
    stock_job = workflow["jobs"]["stock_cli_capture"]
    build = next(
        step["run"] for step in stock_job["steps"] if "qualify_installed_wheel.py" in step.get("run", "")
    )
    assert "/usr/bin/python3 scripts/qualify_installed_wheel.py --symlink-interpreter" in build
    for name, job in workflow["jobs"].items():
        if name != "stock_cli_capture":
            assert "--symlink-interpreter" not in str(job)


@pytest.mark.parametrize("symlink", [False, True])
def test_actual_qualification_venv_interpreter_composition(tmp_path, symlink):
    import sys

    harness = load(ROOT / "scripts/qualify_installed_wheel.py")
    import ast
    import inspect

    calls = [
        node
        for node in ast.walk(ast.parse(inspect.getsource(harness.main)))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "create_environment"
    ]
    assert len(calls) == 1
    keyword = next(item for item in calls[0].keywords if item.arg == "symlink_interpreter")
    assert ast.dump(keyword.value) == ast.dump(ast.parse("args.symlink_interpreter", mode="eval").body)
    environment = tmp_path / "venv"
    if symlink:
        harness.create_environment(environment, symlink_interpreter=True)
    else:
        harness.create_environment(environment)
    target = environment / "bin/python"
    original = Path(sys._base_executable).resolve()
    assert target.is_symlink() is symlink
    assert (target.resolve() == original) is symlink
    assert hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(original.read_bytes()).digest()
    assert "include-system-site-packages = false" in (environment / "pyvenv.cfg").read_text()


@pytest.mark.parametrize("shell_exit", [7, 73])
@pytest.mark.parametrize("corruption", [None, "schema", "extra", "summary_extra"])
@pytest.mark.parametrize("delivered", [False, True])
def test_workflow_inline_producers_projectors_and_publication(
    tmp_path, shell_exit, corruption, delivered
):
    """Execute workflow producers, not hand-built replacements; never launch capture."""
    import subprocess
    import sys

    import yaml

    source = tmp_path / "public"
    source.mkdir(mode=0o700)
    workflow = yaml.safe_load((ROOT / ".github/workflows/cli-capture-diagnostic.yml").read_text())
    steps = workflow["jobs"]["stock_cli_capture"]["steps"]
    for step_id, name, delimiter in (
        ("prepare", "context.json", "CONTEXT"),
        ("capability", "capability.json", "PY"),
    ):
        shell = next(step["run"] for step in steps if step.get("id") == step_id)
        inline = shell.split("<<'" + delimiter + "'\n", 1)[1].split("\n" + delimiter, 1)[0]
        # The producers create mode0600 themselves. Only read-only utility discovery
        # occurs here; no namespace, tracer, installation, or product is executed.
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-B", "-", str(source / name)],
            input=inline,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        assert not result.stdout and not result.stderr
        if step_id == "prepare":
            assert result.returncode == 0
        else:
            value = json.loads((source / name).read_text())
            assert result.returncode == (0 if all(value["facts"].values()) else 78)
        assert (source / name).stat().st_mode & 0o777 == 0o600
    receipt_fixture = load(ROOT / "tests/test_cli_capture_public_summary.py")
    for mode in ("baseline", "diagnostic"):
        result_path = tmp_path / (mode + "-result.json")
        if delivered:
            private_receipt = receipt_fixture.receipt(mode, code=7)
            private_receipt["shell_exit"] = shell_exit
            result_path.write_text(json.dumps(private_receipt) + "\n")
            result_path.chmod(0o600)
        summary = subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                "-B",
                str(ROOT / "scripts/cli_capture_public_summary.py"),
                "--result",
                str(result_path),
                "--mode",
                mode,
                "--shell-exit",
                str(shell_exit),
            ],
            capture_output=True,
            timeout=10,
            check=False,
        )
        assert summary.returncode == (0 if delivered else 2)
        value = json.loads(summary.stdout)
        assert value["harness_shell_exit"] == shell_exit
        assert value["qualification"] == "NOT_ASSESSED"
        path = source / (mode + ".json")
        path.write_bytes(summary.stdout)
        path.chmod(0o600)  # Equivalent to the workflow's umask077 redirection.
    if corruption is not None:
        path = source / ("baseline.json" if corruption == "summary_extra" else "capability.json")
        value = json.loads(path.read_text())
        if corruption == "schema":
            value["schema"] = "forge-stock-cli-capture-capability-v1"
        else:
            value["unreviewed"] = "PRIVATE_CANARY_NOT_FOR_PUBLICATION"
        path.write_text(json.dumps(value) + "\n")
    destination = source / "published"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            str(ROOT / "scripts/cli_capture_stock_publication.py"),
            str(source),
            str(destination),
        ],
        capture_output=True,
        timeout=10,
        check=False,
    )
    if corruption is not None:
        assert result.returncode != 0
        assert not destination.exists()
    else:
        assert result.returncode == 0 and not result.stdout and not result.stderr
        assert {path.name for path in destination.iterdir()} == {
            "context.json",
            "capability.json",
            "baseline.json",
            "diagnostic.json",
        }
        for mode in ("baseline", "diagnostic"):
            value = json.loads((destination / (mode + ".json")).read_text())
            assert value["harness_shell_exit"] == shell_exit
            assert value["qualification"] == "NOT_ASSESSED"
            assert value["raw_evidence"] == "NOT_PUBLISHED"
            assert value["cli_returncode"] == (7 if delivered else None)
            assert receipt_fixture.CANARY not in (destination / (mode + ".json")).read_text()
            if delivered:
                assert value["receipt_status"] == "DELIVERED"
                assert value["collector_delivery"] == (
                    "NOT_APPLICABLE" if mode == "baseline" else "HEALTHY"
                )
