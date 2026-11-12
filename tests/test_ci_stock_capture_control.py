"""Synthetic stock capability contracts. Never launch bwrap or strace in tests."""

import builtins
import struct
import errno
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


control = load("ci_stock_capture_control")
publication = load("ci_stock_capture_publication")
stock = load("ci_stock_runner")


def child():
    return dict(
        stock=dict(
            cache_tag="cpython-314",
            profile="bwrap//&unpriv_bwrap (enforce)",
            status=dict(CapEff="0" * 16, CapPrm="0" * 16, CapAmb="0" * 16, NoNewPrivs="1"),
            namespaces={name: f"{name}:[2]" for name in stock.NAMESPACES},
        ),
        loopback_flags=9,
        trace=dict(returncode=0, marker=True, stderr_empty=True),
    )


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    state = SimpleNamespace(
        payload=child(),
        code=0,
        ownership=dict(cleanup_complete=True, owned=[dict(remaining=False)]),
        calls=[],
    )

    def owner(argv, **kw):
        state.calls.append((argv, kw))
        return SimpleNamespace(
            returncode=state.code,
            stdout=(json.dumps(state.payload) + "\n").encode(),
            stderr=b"",
            ownership=state.ownership,
        )

    def require(value, message):
        if not value:
            raise ValueError(message)

    capture = SimpleNamespace(
        new_private_directory=lambda p: p,
        sanitize_controller=lambda p: None,
        apply_limits=lambda: None,
        require=require,
        regular_bytes=lambda *a: b"# synthetic",
        load_owner=lambda *a: owner,
        safe_environment=lambda p: {"PATH": "/usr/bin:/bin"},
        no_duplicate_keys=dict,
    )
    fake_stock = SimpleNamespace(
        security_state=lambda: {"fixed": "state"},
        root_file=lambda p: b"fixed",
        BWRAP=Path("/usr/bin/bwrap"),
        NAMESPACES=stock.NAMESPACES,
        verify_child=stock.verify_child,
    )
    ownership = load("cli_capture_launch_contrast")
    monkeypatch.setattr(
        control,
        "helper",
        lambda n: {
            "capture_installed_cli": capture,
            "ci_stock_runner": fake_stock,
            "cli_capture_launch_contrast": ownership,
        }[n],
    )
    monkeypatch.setattr(
        control,
        "sys",
        SimpleNamespace(
            executable="/usr/bin/python3",
            flags=SimpleNamespace(isolated=True),
            implementation=SimpleNamespace(cache_tag="cpython-314"),
        ),
    )
    monkeypatch.setattr(control, "no_file_caps", lambda p: None)
    monkeypatch.setattr(control.os, "readlink", lambda p: Path(p).name + ":[1]")
    monkeypatch.setattr(control.resource, "setrlimit", lambda *a: None)
    return state, capture, fake_stock, tmp_path


def test_observed_fixed_control(fixture):
    state, capture, fake_stock, path = fixture
    value = control.observe(path / "control")
    assert value["status"] == "OBSERVED" and value["stock_safety"] == "VERIFIED"
    assert publication.validate(value) == value
    argv, kw = state.calls[0]
    assert (
        argv == control.command(fake_stock, {name: f"{name}:[1]" for name in stock.NAMESPACES})
        and kw["timeout"] == 10
    )
    assert "--unshare-all" in argv and "--die-with-parent" in argv
    assert argv[-6:-2] == ["/usr/bin/python3", "-I", "-B", "-c"]
    assert "ip link" not in control.CHILD and "SIOCSIFFLAGS" not in control.CHILD
    assert "trace=none" in control.CHILD and "dir='/tmp'" in control.CHILD


@pytest.mark.parametrize(
    "change,stage",
    [
        ("loopdown", "LOOPBACK"),
        ("looptype", "LOOPBACK"),
        ("trace", "STRACE"),
        ("cap", "CHILD"),
        ("profile", "CHILD"),
        ("required_runtime", "CHILD"),
        ("namespace", "CHILD"),
        ("code", "LAUNCH"),
        ("cleanup", "CLEANUP"),
    ],
)
def test_fail_closed_stages(fixture, change, stage):
    state, _, _, path = fixture
    if change == "loopdown":
        state.payload["loopback_flags"] = 8
    if change == "looptype":
        state.payload["loopback_flags"] = True
    if change == "trace":
        state.payload["trace"]["returncode"] = 1
    if change == "cap":
        state.payload["stock"]["status"]["CapEff"] = "0000000000000001"
    if change == "profile":
        state.payload["stock"]["profile"] = "unconfined"
    if change == "required_runtime":
        state.payload["stock"]["cache_tag"] = "cpython-312"
    if change == "namespace":
        state.payload["stock"]["namespaces"]["net"] = "net:[1]"
    if change == "code":
        state.code = 1
    if change == "cleanup":
        state.ownership["cleanup_complete"] = False
    value = control.observe(path / "control")
    assert value["status"] == "UNSUPPORTED" and value["stage"] == stage
    assert publication.validate(value) == value


@pytest.mark.parametrize(
    "missing", [FileNotFoundError("secret"), PermissionError("secret"), ValueError("secret")]
)
def test_prerequisite_failure_no_launch(fixture, missing):
    state, _, fake_stock, path = fixture

    def fail():
        raise missing

    fake_stock.security_state = fail
    value = control.observe(path / "control")
    assert value["status"] == "UNSUPPORTED" and value["stage"] == "PREREQUISITE" and not state.calls
    assert "secret" not in json.dumps(value)


@pytest.mark.parametrize("raw", [b"", b"capabilities", None])
def test_any_file_cap_attribute_rejected(monkeypatch, raw):
    monkeypatch.setattr(control.os, "getxattr", lambda *a, **k: raw)
    with pytest.raises(ValueError):
        control.no_file_caps(Path("/fixed"))


@pytest.mark.parametrize(
    "number,accepted", [(errno.ENODATA, True), (errno.EPERM, False), (errno.ENOTSUP, False)]
)
def test_no_caps_requires_authoritative_absence(monkeypatch, number, accepted):
    def fail(*a, **k):
        raise OSError(number, "secret")

    monkeypatch.setattr(control.os, "getxattr", fail)
    if accepted:
        control.no_file_caps(Path("/fixed"))
    else:
        with pytest.raises(OSError):
            control.no_file_caps(Path("/fixed"))


@pytest.mark.parametrize(
    "key,value",
    [
        ("raw", "secret"),
        ("extra", "secret"),
        ("status", "PASS"),
        ("control_returncode", True),
        ("cleanup", "UNKNOWN"),
        ("loopback", "DOWN"),
        ("qualification", "PASS"),
        ("stage", "PREREQUISITE"),
        ("required_runtime", "SYSTEM_CPYTHON_312"),
    ],
)
def test_public_rejects_mutations(fixture, key, value):
    result = control.observe(fixture[3] / "control")
    result[key] = value
    with pytest.raises(ValueError):
        publication.validate(result)


def test_producer_to_private_to_published(fixture):
    root = fixture[3]
    value = control.observe(root / "control")
    private = root / "private"
    private.mkdir(mode=0o700)
    public = root / "public"
    public.mkdir(mode=0o700)
    source = private / "summary.json"
    source.write_text(json.dumps(value) + "\n")
    source.chmod(0o600)
    publication.publish(source, public / "stock-control.json")
    raw = (public / "stock-control.json").read_text()
    assert json.loads(raw) == value and str(root) not in raw and "bwrap//" not in raw


def test_workflow_is_independent_default_off():
    source = (ROOT / ".github/workflows/cli-capture-diagnostic.yml").read_text()
    declaration = source.split("      run_stock_bwrap_control:")[1].split("      expected_source_sha:")[
        0
    ]
    assert "default: false" in declaration
    job = source.split("  stock_bwrap_control:")[1]
    assert "runs-on: ubuntu-26.04" in job and "inputs.run_stock_bwrap_control" in job
    assert "needs:" not in job and "pip install" not in job and "sudo" not in job
    assert "stock-control/public/stock-control.json" in job
    assert "runs-on: ubuntu-24.04" in source.split("  stock_bwrap_control:")[0]


@pytest.mark.parametrize(
    "unsafe", ["none", "profile", "caps", "namespace", "interfaces", "down", "ioctlerror", "traceerror"]
)
def test_child_gates_tracer_before_launch(monkeypatch, capsys, unsafe):
    import fcntl
    import socket
    import subprocess
    import os

    values = child()["stock"]
    if unsafe == "profile":
        values["profile"] = "unconfined"
    if unsafe == "caps":
        values["status"]["CapEff"] = "1" * 16
    if unsafe == "namespace":
        values["namespaces"]["net"] = "net:[1]"
    original = Path.read_text

    def read(path, *a, **kw):
        if str(path) == "/proc/self/status":
            return "\n".join(k + ":" + v for k, v in values["status"].items())
        if str(path) == "/proc/self/attr/current":
            return values["profile"]
        return original(path, *a, **kw)

    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(os, "readlink", lambda path: values["namespaces"][Path(path).name])
    monkeypatch.setattr(
        socket,
        "if_nameindex",
        lambda: [(1, "lo"), (2, "eth0")] if unsafe == "interfaces" else [(1, "lo")],
    )

    class Sock:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def fileno(self):
            return 123

    monkeypatch.setattr(socket, "socket", lambda *a: Sock())

    def ioctl(*args):
        if unsafe == "ioctlerror":
            raise OSError("secret ioctl")
        return b"x" * 16 + struct.pack("H", 8 if unsafe == "down" else 9)

    monkeypatch.setattr(fcntl, "ioctl", ioctl)
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        if unsafe == "traceerror":
            raise OSError("secret trace")
        assert argv[-1] == "import os;os.write(1,b'forge-stock-control-v1\\n')"
        kw["stdout"].write(control.MARKER)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", run)
    fake_sys = SimpleNamespace(
        implementation=SimpleNamespace(cache_tag="cpython-314"),
        argv=["control", json.dumps({name: f"{name}:[1]" for name in stock.NAMESPACES})],
    )
    original_import = builtins.__import__

    def imports(name, *a, **kw):
        return fake_sys if name == "sys" else original_import(name, *a, **kw)

    exec(  # noqa: S102 - fixed reviewed child source under synthetic imports; no live probes
        compile(control.CHILD, "<fixed-synthetic-child>", "exec"),
        {"__builtins__": dict(vars(builtins), __import__=imports)},
    )
    result = json.loads(capsys.readouterr().out)
    assert len(calls) == (1 if unsafe in {"none", "traceerror"} else 0)
    assert result["trace"]["marker"] == (unsafe == "none")


def test_seed_no_delivery_is_not_launch_claim():
    value = control.empty() | {"stage": "NO_DELIVERY"}
    assert publication.validate(value) == value
    value["control_returncode"] = 0
    with pytest.raises(ValueError):
        publication.validate(value)


@pytest.mark.parametrize(
    "mutation", ["symlink", "hardlink", "mode", "suffix", "duplicate", "oversize", "fifo"]
)
def test_publication_rejects_unsafe_receipts(tmp_path, mutation):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    dest = tmp_path / "public"
    dest.mkdir(mode=0o700)
    path = root / "summary.json"
    value = control.empty() | {"stage": "NO_DELIVERY"}
    path.write_text(json.dumps(value) + "\n")
    path.chmod(0o600)
    if mutation == "symlink":
        other = root / "other"
        path.rename(other)
        path.symlink_to(other)
    if mutation == "hardlink":
        control.os.link(path, root / "other")
    if mutation == "mode":
        path.chmod(0o644)
    if mutation == "suffix":
        path.write_text(path.read_text() + "secret\n")
    if mutation == "duplicate":
        path.write_text('{"schema":"secret",' + path.read_text()[1:])
    if mutation == "oversize":
        path.write_text("x" * 4097)
    if mutation == "fifo":
        path.unlink()
        control.os.mkfifo(path, 0o600)
    with pytest.raises((ValueError, OSError)):
        publication.publish(path, dest / "stock-control.json")
    assert not (dest / "stock-control.json").exists()


def test_host_security_change_is_not_observed(fixture):
    _, _, fake_stock, path = fixture
    state = iter([{"same": True}, {"same": False}])
    fake_stock.security_state = lambda: next(state)
    value = control.observe(path / "control")
    assert value["stage"] == "HOST_STATE" and value["status"] == "UNSUPPORTED"
    assert publication.validate(value) == value


def test_owner_timeout_has_no_causal_claim(fixture):
    import subprocess

    state, capture, _, path = fixture

    def owner(*a, **k):
        error = subprocess.TimeoutExpired(["private"], 10)
        error.ownership = state.ownership
        raise error

    capture.load_owner = lambda *a: owner
    value = control.observe(path / "control")
    assert value["error"] == "TIMEOUT" and value["cleanup"] == "COMPLETE"
    assert value["control_returncode"] is None
    assert publication.validate(value) == value


def test_stock_controller_runtime_rejected_before_launch(fixture, monkeypatch):
    state, _, _, path = fixture
    monkeypatch.setattr(control.sys.implementation, "cache_tag", "cpython-312")
    result = control.observe(path / "control")
    assert result["stage"] == "PREREQUISITE" and not state.calls


def test_main_atomic_delivery_then_publication(fixture, monkeypatch):
    import sys

    root = fixture[3]
    result = control.observe(root / "control")
    private = root / "private"
    private.mkdir(mode=0o700)
    public = root / "public"
    public.mkdir(mode=0o700)
    path = private / "summary.json"
    path.write_text(json.dumps(control.empty() | {"stage": "NO_DELIVERY"}) + "\n")
    path.chmod(0o600)
    capture = load("capture_installed_cli")
    monkeypatch.setattr(control, "helper", lambda n: capture)
    monkeypatch.setattr(control, "observe", lambda p: result)
    monkeypatch.setattr(
        sys, "argv", ["control", "--output", str(private / "control"), "--summary", str(path)]
    )
    assert control.main() == 0
    publication.publish(path, public / "stock-control.json")
    assert json.loads((public / "stock-control.json").read_text()) == result
    assert not path.with_name(".summary.pending").exists()


def test_workflow_keeps_exact_failure_and_seed():
    source = (
        (ROOT / ".github/workflows/cli-capture-diagnostic.yml")
        .read_text()
        .split("  stock_bwrap_control:")[1]
    )
    assert "stage='NO_DELIVERY'" in source
    assert "required_runtime='SYSTEM_CPYTHON_314'" in source
    assert "printf 'control_exit=%s\\n'" in source
    assert 'exit "$CONTROL_EXIT"' in source
    assert source.index("git rev-parse HEAD") < source.index("ci_stock_capture_control.py")


@pytest.mark.parametrize("uid,euid", [(0, 0), (1000, 0), (0, 1000), (1000, 1001)])
def test_privileged_or_mismatched_parent_does_not_launch(fixture, monkeypatch, uid, euid):
    state, _, _, path = fixture
    monkeypatch.setattr(control.os, "getuid", lambda: uid)
    monkeypatch.setattr(control.os, "geteuid", lambda: euid)
    result = control.observe(path / "control")
    assert result["status"] == "UNSUPPORTED" and result["stage"] == "PREREQUISITE"
    assert not state.calls


@pytest.mark.parametrize("stage", ["LOOPBACK", "STRACE"])
def test_public_rejects_late_stage_without_verified_stock(stage):
    value = control.empty() | dict(
        stage=stage, error="INVALID", control_returncode=0, cleanup="COMPLETE"
    )
    with pytest.raises(ValueError):
        publication.validate(value)


@pytest.mark.parametrize("loopback", ["NOT_OBSERVED", "DOWN"])
def test_public_strace_requires_up_loopback(loopback):
    value = control.empty() | dict(
        stage="STRACE",
        error="INVALID",
        control_returncode=0,
        cleanup="COMPLETE",
        stock_safety="VERIFIED",
        loopback=loopback,
    )
    with pytest.raises(ValueError):
        publication.validate(value)


def test_public_actual_code_requires_cleanup_observation():
    value = control.empty() | dict(stage="LAUNCH", error="INVALID", control_returncode=1)
    with pytest.raises(ValueError):
        publication.validate(value)


@pytest.mark.parametrize(
    "stage,loopback", [("LOOPBACK", "NOT_OBSERVED"), ("LOOPBACK", "DOWN"), ("STRACE", "UP")]
)
def test_public_preserves_legitimate_partial_stages(stage, loopback):
    value = control.empty() | dict(
        stage=stage,
        error="INTERRUPTED",
        control_returncode=0,
        cleanup="COMPLETE",
        stock_safety="VERIFIED",
        loopback=loopback,
    )
    assert publication.validate(value) == value


def test_public_child_stage_requires_zero_control_exit():
    value = control.empty() | dict(
        stage="CHILD", error="INVALID", control_returncode=1, cleanup="COMPLETE"
    )
    with pytest.raises(ValueError):
        publication.validate(value)
