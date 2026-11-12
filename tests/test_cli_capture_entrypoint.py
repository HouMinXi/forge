# SPDX-License-Identifier: Apache-2.0
"""Synthetic controls only: no installed CLI, namespace, tracer, or model run."""

import ast
import base64
from contextlib import contextmanager
import csv
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
from types import MappingProxyType, SimpleNamespace
import zipfile

import pytest

from qualification.installed_cli import capture_worker as worker

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "capture_entrypoint_tested", ROOT / "scripts/capture_installed_cli.py"
)
entry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(entry)
COMMIT = "239b4238d4025d01e3619dfa173fac3dc2d2b13e"
DIST = "code_review_forge-2.9.0.dist-info"


def write(path, raw, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    path.chmod(mode)
    return path


def record(rows):
    stream = io.StringIO()
    writer = csv.writer(stream, lineterminator="\n")
    for name, raw in rows.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode()
        writer.writerow([name, "sha256=" + digest, str(len(raw))])
    return stream.getvalue().encode()


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    root = tmp_path / "evidence"
    root.mkdir()
    package = {
        "code_forge/__init__.py": b"# synthetic package; never imported\n",
        "code_forge/_mutation_imports.py": b"AUTHORITY = 'synthetic'\n",
        "code_forge/_mutation_process.py": b"def run_owned_command(*args, **kwargs):\n    return 'synthetic'\n",
        "code_forge_launcher.py": b"def cli_main():\n    return 7\n",
    }
    source = {"src/" + name: raw for name, raw in package.items()}
    source["pyproject.toml"] = (
        b'[project]\nname="code-review-forge"\nversion="2.9.0"\n'
        b'[project.scripts]\ncode-forge="code_forge_launcher:cli_main"\n'
    )
    archive = io.BytesIO()
    with tarfile.open(
        fileobj=archive, mode="w", format=tarfile.PAX_FORMAT, pax_headers={"comment": COMMIT}
    ) as bundle:
        for name, raw in source.items():
            member = tarfile.TarInfo(name)
            member.size = len(raw)
            bundle.addfile(member, io.BytesIO(raw))
    write(root / "source.tar", archive.getvalue())
    members = dict(package)
    members[DIST + "/METADATA"] = b"Metadata-Version: 2.1\nName: code-review-forge\nVersion: 2.9.0\n"
    members[DIST + "/entry_points.txt"] = (
        b"[console_scripts]\ncode-forge = code_forge_launcher:cli_main\n"
    )
    members[DIST + "/RECORD"] = record(members)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        for name, raw in members.items():
            bundle.writestr(name, raw)
    wheel = write(root / "dist/code_review_forge-2.9.0-py3-none-any.whl", archive.getvalue())
    prefix = root / "venv"
    site = prefix / "lib/python3.12/site-packages"
    for name, raw in members.items():
        write(site / name, raw)
    write(
        prefix / "pyvenv.cfg",
        b"home = /synthetic/base\ninclude-system-site-packages = false\nversion = 3.12.12\n",
    )
    target = write(prefix / "bin/python", b"synthetic binary, never executed", 0o700)
    launcher = write(prefix / "bin/code-forge", entry.launcher_template(target).encode(), 0o700)
    direct = {
        "url": wheel.as_uri(),
        "archive_info": {"hashes": {"sha256": entry.sha256(wheel.read_bytes())}},
    }
    write(site / DIST / "direct_url.json", json.dumps(direct).encode())
    write(
        site / DIST / "RECORD",
        record(dict(members) | {os.path.relpath(launcher, site): launcher.read_bytes()}),
    )
    proof = {
        "source_commit": COMMIT,
        "source_archive_sha256": entry.sha256((root / "source.tar").read_bytes()),
        "wheel": wheel.name,
        "wheel_sha256": entry.sha256(wheel.read_bytes()),
        "harness_sha256": "a" * 64,
        "controls": {},
        "dependency_wheels": {},
        "scope": "synthetic",
        "expected_roster": [],
    }
    write(root / "build-proof.json", (json.dumps(proof) + "\n").encode())
    (root / "stdlib").mkdir()
    monkeypatch.setattr(
        entry,
        "interpreter_identity",
        lambda path, **kwargs: {
            "executable": str(path),
            "physical": str(path),
            "version": "synthetic",
            "version_info": [3, 12],
            "runtime_version": [3, 12, 12],
            "base_prefix": str(root),
            "base_exec_prefix": str(root),
            "platlibdir": "lib",
            "stdlib_paths": [str(root / "stdlib"), str(root / "python312.zip")],
            "extension_suffixes": [".cpython-312-x86_64-linux-gnu.so", ".abi3.so", ".so"],
            "sha256": entry.sha256(Path(path).read_bytes()),
        },
    )
    return SimpleNamespace(
        root=root, site=site, prefix=prefix, wheel=wheel, proof=proof, target=target, launcher=launcher
    )


def test_physical_provenance_without_target_import_or_path_override(evidence):
    before_path = list(sys.path)
    before_modules = set(sys.modules)
    binding, sources, origin = entry.verify_installation(evidence.root)
    assert binding["installed_provenance"]["source_commit"] == COMMIT
    assert binding["installed_provenance"]["source_commit_basis"] == "RECORDED_BUILD_PROVENANCE"
    assert binding["installed_provenance"]["git_object_independently_verified"] is False
    assert binding["launcher"] == str(evidence.launcher)
    assert set(sources) == {"_mutation_imports.py", "_mutation_process.py"}
    assert origin == evidence.site / "code_forge"
    assert sys.path == before_path
    assert not any(name.startswith("code_forge") for name in set(sys.modules) - before_modules)


@pytest.mark.parametrize(
    "fault",
    [
        "archive",
        "wheel",
        "installed",
        "record",
        "editable",
        "direct_hash",
        "direct_destination",
        "extra_source",
        "path_hook",
        "launcher_body",
        "launcher_shebang",
        "metadata",
        "entrypoint",
        "system_site",
        "symlink",
        "duplicate_proof",
        "wrong_commit",
        "wheel_traversal",
    ],
)
def test_provenance_tampering_rejected(evidence, fault):
    r, site = evidence.root, evidence.site
    if fault in {"archive", "wheel", "installed", "record", "metadata", "entrypoint"}:
        path = {
            "archive": r / "source.tar",
            "wheel": evidence.wheel,
            "installed": site / "code_forge/_mutation_process.py",
            "record": site / DIST / "RECORD",
            "metadata": site / DIST / "METADATA",
            "entrypoint": site / DIST / "entry_points.txt",
        }[fault]
        path.write_bytes(path.read_bytes() + b"tampered")
    elif fault in {"editable", "direct_hash", "direct_destination"}:
        path = site / DIST / "direct_url.json"
        data = json.loads(path.read_text())
        if fault == "editable":
            data["dir_info"] = {"editable": True}
        elif fault == "direct_hash":
            data["archive_info"]["hashes"]["sha256"] = "b" * 64
        else:
            data["url"] += "?altered"
        path.write_text(json.dumps(data))
    elif fault in {"extra_source", "path_hook"}:
        write(
            site / ("code_forge/shadow.py" if fault == "extra_source" else "evil.pth"),
            b"import shadow\n",
        )
    elif fault.startswith("launcher_"):
        raw = evidence.launcher.read_bytes()
        evidence.launcher.write_bytes(
            raw.replace(b"cli_main()", b"cli_main(1)")
            if fault == "launcher_body"
            else raw.replace(str(evidence.target).encode(), b"/wrong/python")
        )
    elif fault == "system_site":
        (evidence.prefix / "pyvenv.cfg").write_text("include-system-site-packages = true\n")
    elif fault == "symlink":
        path = site / "code_forge/_mutation_process.py"
        raw = path.read_bytes()
        path.unlink()
        path.symlink_to(write(r / "elsewhere", raw))
    elif fault == "duplicate_proof":
        path = r / "build-proof.json"
        path.write_text(path.read_text().rstrip()[:-1] + ', "source_commit":"' + COMMIT + '"}\n')
    else:
        evidence.proof["source_commit" if fault == "wrong_commit" else "wheel"] = (
            "b" * 40 if fault == "wrong_commit" else "../wheel.whl"
        )
        (r / "build-proof.json").write_text(json.dumps(evidence.proof))
    with pytest.raises((ValueError, OSError, KeyError)):
        entry.verify_installation(r)


@pytest.mark.parametrize("modern", [False, True], ids=["classic", "modern"])
def test_generated_launcher_ast_is_recognized(modern):
    target = Path("/synthetic/venv/bin/python")
    entry.verify_launcher(entry.launcher_template(target, modern=modern).encode(), target)


def test_owner_only_executes_verified_sources_in_private_package(evidence):
    _, sources, origin = entry.verify_installation(evidence.root)
    try:
        command = entry.load_owner(sources, origin)
        assert command() == "synthetic"
        assert sys.modules[entry.OWNER_PACKAGE].__path__ == []
        assert "code_forge" not in command.__module__
        with pytest.raises(ValueError, match="occupied"):
            entry.load_owner(sources, origin)
    finally:
        for name in list(sys.modules):
            if name == entry.OWNER_PACKAGE or name.startswith(entry.OWNER_PACKAGE + "."):
                del sys.modules[name]


def cold_environment(tmp_path):
    prefix = tmp_path / "pycache"
    prefix.mkdir(mode=0o700)
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "PYTHONPYCACHEPREFIX": str(prefix),
        "PYTHONDONTWRITEBYTECODE": "1",
        "SETUPTOOLS_USE_DISTUTILS": "stdlib",
    }


def receipt(mode="baseline", code=0):
    return {
        "schema": 1,
        "mode": mode,
        "child_pid": 123,
        "cache_policy": "private_empty_bytecode_cache",
        "cache_before_launch": None,
        "child_returncode": code,
        "child_returncode_before_cleanup": code,
        "primary_exception": None,
        "cleanup_errors": [],
        "cleanup_induced_termination": False,
        "transport_status": "receipt_written",
        "peer_transport": {"requests": 2, "exchanges": 2, "errors": []},
        "collector_health": {
            "status": "healthy" if mode == "diagnostic" else "not_applicable",
            "errors": [],
        },
    }


def result(mode="baseline", code=0):
    return {
        "mode": mode,
        "worker": receipt(mode, code),
        "final_collector_health": {
            "status": "healthy" if mode == "diagnostic" else "not_applicable",
            "stage": "after_owner_quiescence",
            "cache_health": {"status": "healthy", "policy": "private_empty_bytecode_cache"},
        },
        "worker_returncode": 0,
        "outer_exception": None,
        "record_errors": [],
        "ownership": {"cleanup_complete": True, "owned": []},
    }


@pytest.mark.parametrize(
    "code,expected",
    [(0, 0), (7, 7), (-15, 143), (-9, 137), (255, 255)],
    ids=["zero", "original-seven", "term", "kill", "maximum"],
)
def test_signed_child_shell_mapping(code, expected):
    assert entry.select_exit(result(code=code)) == expected


@pytest.mark.parametrize("failure", ["collector", "cleanup", "owner", "transport", "record", "outer"])
@pytest.mark.parametrize("code", [0, 7], ids=["child-zero", "child-seven"])
def test_capture_failure_never_masks_original_or_creates_success(code, failure):
    value = result(mode="diagnostic", code=code)
    if failure == "collector":
        value["final_collector_health"]["status"] = "failed"
    elif failure == "cleanup":
        value["worker"]["cleanup_errors"].append({"type": "OSError"})
    elif failure == "owner":
        value["ownership"]["cleanup_complete"] = False
    elif failure == "transport":
        value["worker_returncode"] = 73
    elif failure == "record":
        value["record_errors"].append({"type": "OSError"})
    else:
        value["outer_exception"] = {"type": "MutationProcessError"}
    assert entry.select_exit(value) == (7 if code else 73)


@pytest.mark.parametrize(
    "kind,expected",
    [("TimeoutExpired", 124), ("KeyboardInterrupt", 130), ("ValueError", 73)],
    ids=["timeout", "cancel", "setup"],
)
@pytest.mark.parametrize("layer", ["worker", "outer"])
def test_primary_is_not_cleanup_induced_signal(kind, expected, layer):
    value = result(code=-9)
    value["worker"].update(child_returncode_before_cleanup=None, cleanup_induced_termination=True)
    if layer == "worker":
        value["worker"]["primary_exception"] = {"type": kind}
    else:
        value["worker"] = None
        value["outer_exception"] = {"type": kind}
    assert entry.select_exit(value) == expected


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "bool_code",
        "missing_before",
        "no_observation",
        "contradictory",
        "changed",
        "transport",
    ],
)
def test_invalid_worker_receipt_fails_closed(fault):
    value = receipt()
    if fault == "missing":
        value = None
    elif fault == "bool_code":
        value["child_returncode"] = True
    elif fault == "missing_before":
        del value["child_returncode_before_cleanup"]
    elif fault == "no_observation":
        value.update(child_returncode=None, child_returncode_before_cleanup=None)
    elif fault == "contradictory":
        value["cleanup_induced_termination"] = True
    elif fault == "changed":
        value["child_returncode_before_cleanup"] = 7
    else:
        value["transport_status"] = "unknown"
    with pytest.raises(ValueError):
        entry.valid_worker(value, "baseline")


@pytest.mark.parametrize(
    "raw",
    [b'{"schema":1}', b'{"schema":', b'{"x":1,"x":2}\n'],
    ids=["missing-lf", "truncated", "duplicate"],
)
def test_truncated_or_duplicate_receipt_not_success(tmp_path, raw):
    path = write(tmp_path / "receipt.json", raw)
    with pytest.raises(ValueError):
        entry.read_json(path)


def test_private_output_exclusive_and_partial_retained(tmp_path):
    tmp_path.chmod(0o700)
    output = entry.new_private_directory(tmp_path / "new")
    entry.private_bytes(output / "record", b"partial")
    assert output.stat().st_mode & 0o777 == 0o700
    assert (output / "record").stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        entry.new_private_directory(output)
    with pytest.raises(FileExistsError):
        entry.private_bytes(output / "record", b"replacement")
    assert (output / "record").read_bytes() == b"partial"


@pytest.mark.parametrize("kind", ["output", "parent", "record", "fifo"])
def test_symlink_or_nonregular_output_rejected(tmp_path, kind):
    tmp_path.chmod(0o700)
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    if kind in {"output", "parent"}:
        link = tmp_path / "link"
        link.symlink_to(target, target_is_directory=True)
        with pytest.raises((ValueError, FileExistsError)):
            entry.new_private_directory(link if kind == "output" else link / "new")
    else:
        path = tmp_path / "file"
        if kind == "record":
            path.symlink_to(write(target / "raw", b"private"))
        else:
            os.mkfifo(path, 0o600)
        with pytest.raises((ValueError, OSError)):
            entry.regular_bytes(path)


def test_per_file_output_limit_not_aggregate_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(entry, "FILE_LIMIT", 4)
    with pytest.raises(ValueError, match="per-file"):
        entry.private_bytes(tmp_path / "too-big", b"12345")
    assert not (tmp_path / "too-big").exists()


def test_namespace_command_has_no_pid_namespace_and_clean_env(tmp_path):
    binding = {
        "unshare": {"path": "/usr/bin/unshare"},
        "capture_hashes": {"capture_worker.py": "a" * 64},
    }
    command = entry.worker_command(
        binding,
        "baseline",
        tmp_path / "worker",
        Path("/worker.py"),
        tmp_path / "binding",
        "# fixed bootstrap",
    )
    assert command[:6] == [
        "/usr/bin/unshare",
        "--user",
        "--map-root-user",
        "--net",
        "--",
        sys.executable,
    ]
    assert "--pid" not in command and "--fork" not in command
    assert command[6:8] == ["-I", "-B"]
    env = entry.safe_environment(tmp_path)
    assert "PYTHONPATH" not in env and "PYTHONHOME" not in env and "FIXTURE_API_KEY" not in env


@pytest.mark.parametrize(
    "same,interfaces",
    [(True, [(1, "lo")]), (False, [(1, "lo"), (2, "eth0")])],
    ids=["host-namespace", "foreign-interface"],
)
def test_namespace_rejection_precedes_ip_and_cli(tmp_path, monkeypatch, same, interfaces):
    calls = []
    monkeypatch.setattr(worker.os, "readlink", lambda path: "net:[1]" if same else "net:[2]")
    monkeypatch.setattr(worker.socket, "if_nameindex", lambda: interfaces)
    monkeypatch.setattr(worker, "_run_setup", lambda *a, **k: calls.append(a))
    with pytest.raises(RuntimeError):
        worker._isolate({"host_netns": "net:[1]", "ip": "/ip"}, {}, tmp_path)
    assert calls == []


def test_namespace_transition_uses_only_loopback(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(worker.os, "readlink", lambda path: "net:[2]")
    monkeypatch.setattr(worker.socket, "if_nameindex", lambda: [(1, "lo")])
    monkeypatch.setattr(worker, "_run_setup", lambda *a, **k: calls.append(a))
    worker._isolate({"host_netns": "net:[1]", "ip": "/ip"}, {}, tmp_path)
    assert calls == [(["/ip", "link", "set", "lo", "up"],)]


@pytest.mark.parametrize("mode", ["baseline", "diagnostic"])
@pytest.mark.parametrize("code", [0, 7, -signal.SIGTERM], ids=["zero", "seven", "signal"])
def test_worker_selects_exact_direct_or_preenabled_argv_env(tmp_path, monkeypatch, mode, code):
    from qualification.installed_cli import diagnostic_run

    calls, traced_calls = [], []
    original_traced_child = diagnostic_run.traced_child
    argv = ["/installed/code-forge", "review", "--canary"]
    env = cold_environment(tmp_path)
    expected_policy = {
        "PYTHONPYCACHEPREFIX": str(tmp_path / "pycache"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "SETUPTOOLS_USE_DISTUTILS": "stdlib",
    }
    child = SimpleNamespace(pid=123, wait=lambda **kwargs: code, poll=lambda: code)
    peer = SimpleNamespace(
        thread=SimpleNamespace(start=lambda: None, ident=1),
        finish=lambda: None,
        exchanges=[0, 1],
        errors=[],
        attempts=2,
    )
    monkeypatch.setattr(worker, "_verify_binding", lambda binding: None)
    monkeypatch.setattr(worker, "_limits", lambda: None)
    monkeypatch.setattr(worker, "_environment", lambda *args: env)
    monkeypatch.setattr(worker, "_isolate", lambda *args: None)
    monkeypatch.setattr(worker, "_diagnostic_capability", lambda *args: None)
    monkeypatch.setattr(worker, "_Peer", lambda output: peer)
    monkeypatch.setattr(worker, "_fixture", lambda *args: (argv, tmp_path))
    monkeypatch.setattr(
        worker, "_collector_health", lambda *args, **kwargs: {"status": "healthy", "errors": []}
    )

    def popen(actual, **kwargs):
        # Snapshot at the final process boundary, before any later mutation or
        # cleanup. Both worker and the real collector use this same Popen stub.
        calls.append(
            {
                "argv": tuple(actual),
                "env_snapshot": MappingProxyType(dict(kwargs["env"])),
                "original_argv": actual,
                "original_env": kwargs["env"],
                "kwargs": kwargs,
            }
        )
        return child

    @contextmanager
    def traced(actual, **kwargs):
        traced_calls.append((actual, kwargs))
        with original_traced_child(actual, **kwargs) as process:
            yield process

    monkeypatch.setattr(worker.subprocess, "Popen", popen)
    monkeypatch.setattr(worker, "_module", lambda name: SimpleNamespace(traced_child=traced))
    monkeypatch.setattr(diagnostic_run.shutil, "which", lambda *args, **kwargs: "/synthetic/strace")
    monkeypatch.setattr(diagnostic_run, "_terminal_observed", lambda *args, **kwargs: True)
    observed = worker.execute({}, mode, tmp_path)
    assert len(calls) == 1
    launch = calls[0]
    for name, expected in expected_policy.items():
        assert launch["env_snapshot"].get(name) == expected, name
    assert {name: env.get(name) for name in expected_policy} == expected_policy
    assert observed["child_returncode"] == observed["child_returncode_before_cleanup"] == code
    assert observed["primary_exception"] is None and observed["cleanup_errors"] == []
    if mode == "baseline":
        assert traced_calls == []
        assert launch["original_argv"] is argv and launch["original_env"] is env
        assert launch["argv"] == tuple(argv)
        assert "PYTHONPATH" not in launch["env_snapshot"]
        assert "FORGE_CLI_DIAGNOSTIC_FD" not in launch["env_snapshot"]
        assert not (tmp_path / "diagnostic").exists()
    else:
        assert len(traced_calls) == 1
        assert traced_calls[0][0] is argv and traced_calls[0][1]["env"] is env
        assert traced_calls[0][1]["case_id"] == "normal"
        assert launch["original_env"] is not env
        assert launch["argv"][0] == "/synthetic/strace"
        assert launch["argv"][-len(argv) - 1 :] == ("--", *argv)
        assert launch["env_snapshot"]["PYTHONPATH"] == str(tmp_path / "diagnostic/shim")
        assert "PYTHONPATH" not in env
        assert launch["env_snapshot"]["FORGE_CLI_DIAGNOSTIC_FD"] == str(launch["kwargs"]["pass_fds"][0])
        metadata = json.loads((tmp_path / "diagnostic/execution.json").read_bytes())
        assert metadata["case_id"] == "normal" and metadata["returncode"] == code
        assert metadata["pid"] == child.pid and metadata["collector_terminal_observed"] is True
        assert (tmp_path / "diagnostic/events.jsonl").read_bytes() == b""
        assert (tmp_path / "diagnostic/syscalls.log").read_bytes() == b""

        with pytest.raises(OSError):
            os.fstat(launch["kwargs"]["pass_fds"][0])


def test_capture_implementation_has_no_reader_parser_or_qualifier_dependency():
    for path in (
        ROOT / "scripts/capture_installed_cli.py",
        ROOT / "qualification/installed_cli/capture_worker.py",
    ):
        tree = ast.parse(path.read_text())
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        assert not any(
            token in name
            for name in imports
            for token in ("installed_audit", "trace_parser", "qualify", "reader")
        )


def worker_control(tmp_path, monkeypatch, child, *, cleanup_error=False):
    peer = SimpleNamespace(
        thread=SimpleNamespace(start=lambda: None, ident=1),
        finish=lambda: None,
        exchanges=[0, 1],
        errors=[],
        attempts=2,
    )
    monkeypatch.setattr(worker, "_verify_binding", lambda binding: None)
    monkeypatch.setattr(worker, "_limits", lambda: None)
    env = cold_environment(tmp_path)
    monkeypatch.setattr(worker, "_environment", lambda *args: env)
    monkeypatch.setattr(worker, "_isolate", lambda *args: None)
    monkeypatch.setattr(worker, "_diagnostic_capability", lambda *args: None)
    monkeypatch.setattr(worker, "_Peer", lambda output: peer)
    monkeypatch.setattr(worker, "_fixture", lambda *args: (["/synthetic/launcher"], tmp_path))
    monkeypatch.setattr(
        worker, "_collector_health", lambda *args: {"status": "failed", "errors": ["synthetic"]}
    )
    monkeypatch.setattr(worker.subprocess, "Popen", lambda *args, **kwargs: child)

    @contextmanager
    def traced(*args, **kwargs):
        try:
            yield child
        finally:
            if cleanup_error:
                raise OSError("collector metadata failure")

    monkeypatch.setattr(worker, "_module", lambda name: SimpleNamespace(traced_child=traced))


@pytest.mark.parametrize("kind", ["timeout", "cancel", "body", "metadata"])
def test_worker_primary_preserved_across_cleanup_failure(tmp_path, monkeypatch, kind):
    waits, kills = [], []
    error = {
        "timeout": subprocess.TimeoutExpired(["/fixture"], 20),
        "cancel": KeyboardInterrupt(),
        "body": RuntimeError("original"),
        "metadata": None,
    }[kind]

    def wait(**kwargs):
        waits.append(kwargs)
        if error is not None and len(waits) == 1:
            raise error
        return 7 if error is None else -9

    child = SimpleNamespace(pid=123, wait=wait, poll=lambda: None, kill=lambda: kills.append(True))
    worker_control(tmp_path, monkeypatch, child, cleanup_error=True)
    observed = worker.execute({}, "diagnostic", tmp_path)
    assert observed["primary_exception"] == (None if error is None else worker._error(error))
    assert observed["child_returncode"] == (7 if error is None else -9)
    assert observed["child_returncode_before_cleanup"] == (7 if error is None else None)
    assert observed["cleanup_induced_termination"] is (error is not None)
    assert bool(kills) is (error is not None)
    assert observed["cleanup_errors"][0]["message"] == "collector metadata failure"
    mapped = result(mode="diagnostic")
    mapped["worker"] = observed
    assert entry.select_exit(mapped) == {"timeout": 124, "cancel": 130, "body": 73, "metadata": 7}[kind]


def collector_files(tmp_path):
    execution = {
        "schema": 1,
        "mode": "instrumented",
        "case_id": "normal",
        "pid": 123,
        "returncode": 0,
        "collector_terminal_observed": True,
    }
    start = {"schema": 1, "seq": 1, "pid": 123, "event": "diagnostic_start"}
    footer = {"schema": 1, "seq": 2, "pid": 123, "event": "diagnostic_footer", "complete": True}
    footer.update(
        {
            key: 0
            for key in (
                "open_requests",
                "open_tickets",
                "open_workers",
                "open_waits",
                "open_submits",
                "open_publishes",
                "open_frames",
                "unknown_targets",
            )
        }
    )
    write(tmp_path / "execution.json", (json.dumps(execution) + "\n").encode())
    write(tmp_path / "events.jsonl", (json.dumps(start) + "\n" + json.dumps(footer) + "\n").encode())
    write(tmp_path / "syscalls.log", b"synthetic raw trace retained, no parser verdict\n")


@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "missing_execution",
        "truncated_execution",
        "wrong_pid",
        "wrong_status",
        "missing_terminal",
        "missing_events",
        "truncated_events",
        "blank_event",
        "duplicate_key",
        "wrong_sequence",
        "missing_footer",
        "footer_false",
        "open_footer",
        "cr_framing",
        "missing_trace",
        "truncated_trace",
    ],
)
def test_collector_framing_failure_not_success_or_correlation_claim(tmp_path, fault):
    collector_files(tmp_path)
    execution, events, trace = (
        tmp_path / name for name in ("execution.json", "events.jsonl", "syscalls.log")
    )
    if fault.startswith("missing_") and fault != "missing_terminal" and fault != "missing_footer":
        {"missing_execution": execution, "missing_events": events, "missing_trace": trace}[
            fault
        ].unlink()
    elif fault.startswith("truncated_"):
        path = {"truncated_execution": execution, "truncated_events": events, "truncated_trace": trace}[
            fault
        ]
        path.write_bytes(path.read_bytes()[:-1])
    elif fault in {"wrong_pid", "wrong_status", "missing_terminal"}:
        value = json.loads(execution.read_bytes())
        value[
            {
                "wrong_pid": "pid",
                "wrong_status": "returncode",
                "missing_terminal": "collector_terminal_observed",
            }[fault]
        ] = {"wrong_pid": 999, "wrong_status": 7, "missing_terminal": False}[fault]
        execution.write_text(json.dumps(value) + "\n")
    elif fault == "blank_event":
        events.write_bytes(events.read_bytes() + b"\n")
    elif fault == "duplicate_key":
        events.write_bytes(events.read_bytes().replace(b'"seq": 1', b'"seq": 1, "seq": 1'))
    elif fault == "wrong_sequence":
        events.write_bytes(events.read_bytes().replace(b'"seq": 2', b'"seq": 3'))
    elif fault == "missing_footer":
        events.write_bytes(events.read_bytes().split(b"\n")[0] + b"\n")
    elif fault == "footer_false":
        events.write_bytes(events.read_bytes().replace(b'"complete": true', b'"complete": false'))
    elif fault == "open_footer":
        events.write_bytes(events.read_bytes().replace(b'"open_tickets": 0', b'"open_tickets": 1'))
    elif fault == "cr_framing":
        events.write_bytes(events.read_bytes().replace(b"\n", b"\r\n"))
    observed = worker._collector_health(tmp_path, 123, 0)
    assert observed["status"] == ("healthy" if fault == "none" else "failed")
    assert observed["scope"] == "delivery_framing_only"
    assert observed["stream_correlation"] == observed["ack_consistency"] == "NOT_ASSESSED"


@pytest.mark.parametrize("fault", ["deadline", "headers", "body"])
def test_request_reader_hard_bounds_without_network(monkeypatch, fault):
    now = [0.0]
    monkeypatch.setattr(worker.time, "monotonic", lambda: now[0])
    conn = SimpleNamespace(settimeout=lambda value: None, recv=lambda count: b"x" * count)
    reader = worker._RequestReader(conn)
    if fault == "deadline":
        now[0] = 4.0
        with pytest.raises(TimeoutError):
            reader.read(1)
    elif fault == "headers":
        reader.header_bytes = 32768
        with pytest.raises(ValueError):
            reader.readline(1)
    else:
        with pytest.raises(ValueError):
            reader.read(1024 * 1024 + 1)


@pytest.fixture
def preserve_controller_umask():
    # Read the actual caller mask without briefly widening file permissions.
    incoming = os.umask(0o077)
    os.umask(incoming)
    try:
        yield
    finally:
        os.umask(incoming)


def controller_control(tmp_path, monkeypatch, *, code=7, owner_error=None):
    tmp_path.chmod(0o700)
    monkeypatch.setattr(entry, "sanitize_controller", lambda output: None)
    monkeypatch.setattr(entry, "preflight", lambda: "net:[1]")
    monkeypatch.setattr(entry, "apply_limits", lambda: None)
    monkeypatch.setattr(
        entry,
        "verify_installation",
        lambda path: (
            {
                "launcher": "/original/launcher",
                "startup_helper_sha256": entry.sha256(
                    (ROOT / "qualification/installed_cli/capture_worker.py").read_bytes()
                ),
            },
            {},
            tmp_path,
        ),
    )
    monkeypatch.setattr(entry, "interpreter_identity", lambda *a, **k: {"version": "synthetic"})
    monkeypatch.setattr(entry, "utility", lambda name: {"path": "/usr/bin/" + name, "sha256": "a" * 64})
    commands = []

    def owner(argv, **kwargs):
        commands.append((argv, kwargs))
        if owner_error:
            raise owner_error
        output = Path(argv[argv.index("--output") + 1])
        output.mkdir(mode=0o700)
        value = receipt(code=code)
        (output / "pycache").mkdir(mode=0o700)
        value["cache_before_launch"] = worker.cache_snapshot(output / "pycache")
        entry.save_json(output / "outcome.json", value)
        return SimpleNamespace(
            returncode=0, ownership={"cleanup_complete": True, "owned": []}, stdout=b"", stderr=b""
        )

    monkeypatch.setattr(entry, "load_owner", lambda *args: owner)
    return commands


@pytest.mark.usefixtures("preserve_controller_umask")
@pytest.mark.parametrize("fault", ["none", "result_write", "worker_log", "missing_receipt"])
def test_controller_preserves_original_seven_after_capture_metadata_failure(
    tmp_path, monkeypatch, fault
):
    commands = controller_control(tmp_path, monkeypatch)
    save = entry.save_json

    def save_with_failure(path, value):
        if fault == "result_write" and path.name == "result.json":
            raise OSError("synthetic result write error")
        if fault == "missing_receipt" and path.name == "outcome.json":
            return
        return save(path, value)

    monkeypatch.setattr(entry, "save_json", save_with_failure)
    original = entry.private_bytes

    def private_with_failure(path, raw):
        if fault == "worker_log" and path.name == "worker.stdout":
            raise OSError("synthetic worker log write error")
        return original(path, raw)

    monkeypatch.setattr(entry, "private_bytes", private_with_failure)
    output = tmp_path / "output"
    observed = entry.capture(tmp_path / "unused", "baseline", output)
    assert observed == (73 if fault == "missing_receipt" else 7)
    assert commands[0][1]["memory_limit_bytes"] == 1024**3
    assert commands[0][1]["timeout"] == 35
    assert (output / "binding.json").exists()
    if fault in {"worker_log", "missing_receipt"}:
        value = entry.read_json(output / "result.json")
        assert value["controller_stage"] == (
            "WORKER_OUTPUT" if fault == "worker_log" else "WORKER_RECEIPT"
        )
        assert value["controller_error_category"] == (
            "OS_ERROR" if fault == "worker_log" else "NOT_FOUND"
        )


@pytest.mark.usefixtures("preserve_controller_umask")
@pytest.mark.parametrize(
    "error,expected",
    [("timeout", 124), ("cancel", 130), ("setup", 73)],
    ids=["timeout", "cancel", "setup"],
)
def test_outer_owner_exception_retained_without_worker_receipt(tmp_path, monkeypatch, error, expected):
    failure = {
        "timeout": subprocess.TimeoutExpired(["/worker"], 35),
        "cancel": KeyboardInterrupt(),
        "setup": ValueError("unsupported owner"),
    }[error]
    failure.ownership = {"cleanup_complete": True, "owned": []}
    controller_control(tmp_path, monkeypatch, owner_error=failure)
    output = tmp_path / "output"
    assert entry.capture(tmp_path / "unused", "baseline", output) == expected
    observed = entry.read_json(output / "result.json")
    assert observed["worker"] is None
    assert observed["outer_exception"]["type"] == type(failure).__name__
    assert observed["ownership"]["cleanup_complete"] is True
    assert observed["record_errors"]


@pytest.mark.parametrize("mask", [0o022, 0o077], ids=["umask-022", "umask-077"])
def test_controller_pytest_lifecycle_preserves_incoming_umask(tmp_path, mask):
    selectors = [
        "tests/test_cli_capture_entrypoint.py::"
        "test_controller_preserves_original_seven_after_capture_metadata_failure[" + fault + "]"
        for fault in ("none", "result_write", "worker_log", "missing_receipt")
    ] + [
        "tests/test_cli_capture_entrypoint.py::"
        "test_outer_owner_exception_retained_without_worker_receipt[" + error + "]"
        for error in ("timeout", "cancel", "setup")
    ]
    before, after = tmp_path / "parent-before", tmp_path / "parent-after"
    before.touch(mode=0o666)
    parent_mode = before.stat().st_mode & 0o777
    child_dir = tmp_path / "child"
    child_dir.mkdir(mode=0o700)
    code = """
import json, os, pathlib, stat, sys
root, output = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
os.umask(int(sys.argv[3]))
sys.path.insert(0, str(root))
import pytest

def mask():
    value = os.umask(0o077)
    os.umask(value)
    return value

def created_mode(name):
    fd = os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        return stat.S_IMODE(os.fstat(fd).st_mode)
    finally:
        os.close(fd)

reports = []
class Receipt:
    def pytest_runtest_logreport(self, report):
        reports.append([report.nodeid, report.when, report.outcome])

before_mask, before_mode = mask(), created_mode('before')
status = int(pytest.main(['-c', str(root / 'pyproject.toml'), '-q',
                         '-p', 'no:cacheprovider', '--basetemp', str(output / 'pytest'),
                         *json.loads(sys.argv[4])], plugins=[Receipt()]))
record = {'status': status, 'before_mask': before_mask, 'after_mask': mask(),
          'before_mode': before_mode, 'after_mode': created_mode('after'),
          'reports': reports}
print('UMASK_STATE ' + json.dumps(record, sort_keys=True))
raise SystemExit(status)
"""
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            code,
            str(ROOT),
            str(child_dir),
            str(mask),
            json.dumps(selectors),
        ],
        cwd=ROOT,
        env={"PATH": os.defpath, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=20,
    )
    after.touch(mode=0o666)
    assert after.stat().st_mode & 0o777 == parent_mode
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stderr == ""
    records = [
        line.removeprefix("UMASK_STATE ")
        for line in completed.stdout.splitlines()
        if line.startswith("UMASK_STATE ")
    ]
    assert len(records) == 1
    observed = json.loads(records[0])
    assert observed["status"] == 0
    assert observed["before_mask"] == observed["after_mask"] == mask
    assert observed["before_mode"] == observed["after_mode"] == 0o666 & ~mask
    assert sorted(observed["reports"]) == sorted(
        [selector, phase, "passed"] for selector in selectors for phase in ("setup", "call", "teardown")
    )


@pytest.mark.parametrize("fault", ["none", "changed_after_worker", "unclean_owner", "changed_helper"])
def test_final_framing_is_observed_after_owner_quiescence(tmp_path, fault):
    directory = tmp_path / "worker/diagnostic"
    directory.mkdir(parents=True)
    collector_files(directory)
    source = ROOT / "qualification/installed_cli/capture_worker.py"
    binding = {"capture_hashes": {"capture_worker.py": entry.sha256(source.read_bytes())}}
    value = result(mode="diagnostic")
    (tmp_path / "worker/pycache").mkdir(mode=0o700)
    value["worker"]["cache_before_launch"] = worker.cache_snapshot(tmp_path / "worker/pycache")
    if fault == "changed_after_worker":
        (directory / "events.jsonl").write_bytes(b"truncated after preliminary success")
    elif fault == "unclean_owner":
        value["ownership"]["cleanup_complete"] = False
    elif fault == "changed_helper":
        binding["capture_hashes"]["capture_worker.py"] = "0" * 64
    if fault in {"unclean_owner", "changed_helper"}:
        with pytest.raises(ValueError):
            entry.final_delivery(binding, source, tmp_path, value)
    else:
        final = entry.final_delivery(binding, source, tmp_path, value)
        assert final["stage"] == "after_owner_quiescence"
        assert final["status"] == ("healthy" if fault == "none" else "failed")
        value["final_collector_health"] = final
        assert entry.select_exit(value) == (0 if fault == "none" else 73)
        value["worker"].update(child_returncode=7, child_returncode_before_cleanup=7)
        assert entry.select_exit(value) == 7


@pytest.mark.parametrize("view", ["supported", "self_only", "foreign_child", "readiness_timeout"])
def test_live_child_admission_requires_child_view_not_just_self(tmp_path, monkeypatch, view):
    closed, signals = [], []
    stream = SimpleNamespace(fileno=lambda: 901, close=lambda: closed.append("stream"))
    child = SimpleNamespace(
        pid=12345, stdin=stream, stdout=stream, poll=lambda: None, wait=lambda **k: 0
    )
    monkeypatch.setattr(entry.subprocess, "Popen", lambda *a, **k: child)
    monkeypatch.setattr(entry.os, "pidfd_open", lambda pid: 902)
    monkeypatch.setattr(
        entry.signal, "pidfd_send_signal", lambda fd, signum: signals.append((fd, signum))
    )
    monkeypatch.setattr(
        entry.select, "select", lambda *args: ([stream] if view != "readiness_timeout" else [], [], [])
    )
    monkeypatch.setattr(entry.os, "read", lambda fd, count: b"ready\n" if fd == 901 else b"")
    monkeypatch.setattr(entry.os, "close", closed.append)
    actual_open = entry.os.open

    def open_view(path, flags, *args):
        if str(path).startswith("/proc/12345/"):
            if view == "self_only":
                raise FileNotFoundError("live child's children file unsupported")
            return 903
        return actual_open(path, flags, *args)

    monkeypatch.setattr(entry.os, "open", open_view)
    actual_read = Path.read_bytes

    def read_stat(path):
        if str(path) == "/proc/12345/stat":
            return (
                b"12345 (admission) S "
                + str(os.getpid() if view != "foreign_child" else 999).encode()
                + b" 0 0"
            )
        return actual_read(path)

    monkeypatch.setattr(Path, "read_bytes", read_stat)
    if view == "supported":
        entry.live_children_admission()
    else:
        with pytest.raises((ValueError, FileNotFoundError)):
            entry.live_children_admission()
    assert 902 in closed and "stream" in closed
    assert not any(signum == signal.SIGKILL for _, signum in signals)


@pytest.mark.parametrize("fault", ["none", "denied", "missing_terminal", "timeout"])
def test_diagnostic_capability_denial_does_not_launch_real_cli(tmp_path, monkeypatch, fault):
    tracer = write(tmp_path / "strace", b"synthetic", 0o700)
    binding = {
        "target_python": "/target/python",
        "utility_identities": {
            "strace": {"path": str(tracer), "sha256": entry.sha256(tracer.read_bytes())}
        },
    }
    calls, waits = [], []
    monkeypatch.setattr(worker.shutil, "which", lambda *a, **k: str(tracer))
    monkeypatch.setattr(
        worker,
        "_module",
        lambda name: SimpleNamespace(
            SYSCALLS="exit,exit_group", _terminal_observed=lambda *a: fault != "missing_terminal"
        ),
    )

    def wait(**kwargs):
        waits.append(kwargs)
        if fault == "timeout" and len(waits) == 1:
            raise subprocess.TimeoutExpired(["/synthetic"], 5)
        return 1 if fault == "denied" else 7

    child = SimpleNamespace(pid=123, wait=wait, poll=lambda: 7, kill=lambda: None)

    def launch(argv, **kwargs):
        calls.append(argv)
        return child

    monkeypatch.setattr(worker.subprocess, "Popen", launch)
    if fault == "none":
        worker._diagnostic_capability(binding, {}, tmp_path)
    else:
        with pytest.raises((RuntimeError, subprocess.TimeoutExpired)):
            worker._diagnostic_capability(binding, {}, tmp_path)
    assert len(calls) == 1
    assert calls[0][-6:] == ["/target/python", "-I", "-S", "-B", "-c", "raise SystemExit(7)"]
    assert "code-forge" not in " ".join(calls[0])
    observed = json.loads((tmp_path / "capability/outcome.json").read_bytes())
    assert (
        observed["primary_exception"] is None
        if fault == "none"
        else observed["primary_exception"] is not None
    )


def test_capability_failure_stops_worker_before_fixture_or_cli(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(worker, "_verify_binding", lambda binding: None)
    monkeypatch.setattr(worker, "_limits", lambda: None)
    monkeypatch.setattr(worker, "_environment", lambda *args: {})
    monkeypatch.setattr(worker, "_isolate", lambda *args: None)

    def denied(*args):
        raise RuntimeError("synthetic tracing denied")

    monkeypatch.setattr(worker, "_diagnostic_capability", denied)
    monkeypatch.setattr(worker, "_Peer", lambda *args: calls.append("peer"))
    monkeypatch.setattr(worker, "_fixture", lambda *args: calls.append("fixture"))
    monkeypatch.setattr(worker.subprocess, "Popen", lambda *args, **kwargs: calls.append("cli"))
    observed = worker.execute({}, "diagnostic", tmp_path)
    assert calls == []
    assert observed["child_returncode"] is None
    assert observed["primary_exception"]["message"] == "synthetic tracing denied"


def test_controller_helper_environment_is_sanitized(tmp_path, monkeypatch):
    environment = {
        "PYTHONPATH": "/foreign",
        "OPENAI_API_KEY": "synthetic-not-real",
        "LD_PRELOAD": "/foreign",
        "MUTANT_UNDER_TEST": "other",
    }
    monkeypatch.setattr(entry.os, "environ", environment)
    entry.sanitize_controller(tmp_path)
    assert environment == entry.safe_environment(tmp_path)
    assert not any(
        key in environment for key in ("PYTHONPATH", "OPENAI_API_KEY", "LD_PRELOAD", "MUTANT_UNDER_TEST")
    )


def test_peer_connection_count_is_bounded_even_without_post(monkeypatch):
    calls = []
    connection = SimpleNamespace(settimeout=lambda value: None, close=lambda: calls.append("closed"))
    monkeypatch.setattr(worker.HTTPServer, "get_request", lambda self: (connection, ("127.0.0.1", 1)))
    peer = worker._Peer.__new__(worker._Peer)
    peer.attempts = 0
    peer.stop = SimpleNamespace(set=lambda: calls.append("stopped"))
    assert peer.get_request()[0] is connection
    assert peer.get_request()[0] is connection
    with pytest.raises(ValueError, match="connection count"):
        peer.get_request()
    assert calls == ["closed", "stopped"]
    assert peer.attempts == 3


def startup_binding(evidence):
    binding, _, _ = entry.verify_installation(evidence.root)
    return binding, binding["startup_admission"]


@pytest.mark.parametrize("root_name", ["bin", "stdlib", "site"])
@pytest.mark.parametrize(
    "candidate",
    [
        "launcher_package",
        "launcher_legacy",
        "launcher_extension",
        "site_source",
        "site_package",
        "site_legacy",
        "user_source",
        "user_cache",
    ],
)
def test_startup_shadow_candidates_rejected_without_execution(evidence, root_name, candidate):
    _, policy = startup_binding(evidence)
    root = {"bin": evidence.prefix / "bin", "stdlib": evidence.root / "stdlib", "site": evidence.site}[
        root_name
    ]
    name = {
        "launcher_package": "code_forge_launcher/__init__.py",
        "launcher_legacy": "code_forge_launcher.pyc",
        "launcher_extension": "code_forge_launcher.cpython-312-x86_64-linux-gnu.so",
        "site_source": "sitecustomize.py",
        "site_package": "sitecustomize/__init__.py",
        "site_legacy": "sitecustomize.pyc",
        "user_source": "usercustomize.py",
        "user_cache": "__pycache__/usercustomize.cpython-312.pyc",
    }[candidate]
    # Inert marker bytes only. Nothing is executed or unmarshalled.
    candidate_path = write(root / name, b"INERT STARTUP INVENTORY MARKER\n")
    before = candidate_path.read_bytes()
    with pytest.raises(ValueError, match="startup|launcher"):
        worker.admit_startup(policy)
    assert candidate_path.read_bytes() == before


@pytest.mark.parametrize(
    "member",
    [
        "safe.py",
        "code_forge_launcher.py",
        "code_forge_launcher.pyc",
        "code_forge_launcher/__init__.py",
        "sitecustomize.py",
        "sitecustomize/__init__.py",
        "usercustomize.pyc",
    ],
)
def test_actual_stdlib_zip_is_inspected_without_import(evidence, member):
    _, policy = startup_binding(evidence)
    archive = evidence.root / "python312.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(member, b"INERT ZIP MEMBER\n")
    if member == "safe.py":
        observed = worker.admit_startup(policy)
        row = next(row for row in observed if row["path"] == str(archive))
        assert row["kind"] == "zip" and row["sha256"] == entry.sha256(archive.read_bytes())
    else:
        with pytest.raises(ValueError, match="ZIP"):
            worker.admit_startup(policy)


@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "unknown_pth",
        "changed_pth",
        "source_changed",
        "symlink_root",
        "missing_nonzip",
        "second_site",
    ],
)
def test_startup_exact_hook_and_root_admission(evidence, fault):
    write(evidence.site / "distutils-precedence.pth", worker.DISTUTILS_PTH)
    binding, policy = startup_binding(evidence)
    if fault == "unknown_pth":
        write(evidence.site / "unknown.pth", b"INERT UNRECOGNIZED HOOK\n")
    elif fault == "changed_pth":
        write(evidence.site / "distutils-precedence.pth", worker.DISTUTILS_PTH + b"# changed\n")
    elif fault == "source_changed":
        write(evidence.site / "code_forge_launcher.py", b"# changed, never executed\n")
    elif fault == "symlink_root":
        original = evidence.root / "stdlib"
        renamed = evidence.root / "stdlib-original"
        original.rename(renamed)
        original.symlink_to(renamed, target_is_directory=True)
    elif fault == "missing_nonzip":
        (evidence.root / "stdlib").rmdir()
    elif fault == "second_site":
        (evidence.prefix / "lib64/python3.12/site-packages").mkdir(parents=True)
        target = dict(binding["target"], platlibdir="lib64")
        with pytest.raises(ValueError, match="site-packages"):
            entry.startup_policy(
                evidence.prefix, evidence.site, target, policy["launcher_source_sha256"]
            )
        return
    if fault == "none":
        assert worker.admit_startup(policy) == policy["observed"]
        assert policy["distutils_mode"] == "stdlib"
        hook = next(row for row in policy["observed"] if row["path"] == str(evidence.site))["pth"]
        assert hook == {"distutils-precedence.pth": entry.sha256(worker.DISTUTILS_PTH)}
    else:
        with pytest.raises((ValueError, FileNotFoundError)):
            worker.admit_startup(policy)


def test_old_source_cache_is_neither_read_nor_modified(evidence, monkeypatch):
    cache = write(evidence.site / "__pycache__/code_forge_launcher.cpython-312.pyc", b"INERT OLD CACHE")
    before = cache.read_bytes()
    read = worker._read_regular

    def reject_cache_read(path, *args, **kwargs):
        assert Path(path).suffix != ".pyc", "old caches must not be opened"
        return read(path, *args, **kwargs)

    monkeypatch.setattr(worker, "_read_regular", reject_cache_read)
    _, policy = startup_binding(evidence)
    assert worker.admit_startup(policy) == policy["observed"]
    assert cache.read_bytes() == before


@pytest.mark.parametrize("mode", ["baseline", "diagnostic"])
def test_both_modes_get_private_empty_cold_cache_and_disabled_optional_hook(tmp_path, mode):
    output = tmp_path / mode
    output.mkdir(mode=0o700)
    binding = {"target_python": "/original/venv/bin/python"}
    env = worker._environment(output, binding)
    snapshot = worker.cache_snapshot(env["PYTHONPYCACHEPREFIX"])
    assert snapshot["prefix"] == str(output / "pycache") and snapshot["mode"] == 0o700
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert env["SETUPTOOLS_USE_DISTUTILS"] == "stdlib"
    assert "PYTHONPATH" not in env and "PYTHONHOME" not in env and "PYTHONSAFEPATH" not in env
    assert not any((output / "pycache").iterdir())


@pytest.mark.parametrize("fault", ["nonempty", "symlink", "replaced", "permissions", "missing"])
def test_private_cache_reuse_or_identity_change_fails_closed(tmp_path, fault):
    prefix = tmp_path / "cache"
    prefix.mkdir(mode=0o700)
    snapshot = worker.cache_snapshot(prefix)
    if fault == "nonempty":
        write(prefix / "inert.pyc", b"INERT")
    elif fault in {"symlink", "replaced"}:
        prefix.rename(tmp_path / "old-cache")
        if fault == "symlink":
            prefix.symlink_to(tmp_path / "old-cache", target_is_directory=True)
        else:
            prefix.mkdir(mode=0o700)
    elif fault == "permissions":
        prefix.chmod(0o755)
    else:
        prefix.rmdir()
    with pytest.raises((ValueError, OSError)):
        worker.check_cache_snapshot(snapshot)


@pytest.mark.parametrize("mode", ["baseline", "diagnostic"])
@pytest.mark.parametrize("code", [0, 7], ids=["zero", "seven"])
def test_final_cache_failure_is_not_success_and_does_not_mask_seven(tmp_path, mode, code):
    prefix = tmp_path / "worker/pycache"
    prefix.mkdir(mode=0o700, parents=True)
    observed = result(mode=mode, code=code)
    observed["worker"]["cache_before_launch"] = worker.cache_snapshot(prefix)
    write(prefix / "unexpected", b"INERT")
    source = ROOT / "qualification/installed_cli/capture_worker.py"
    binding = {"capture_hashes": {"capture_worker.py": entry.sha256(source.read_bytes())}}
    with pytest.raises(ValueError, match="cache prefix"):
        entry.final_delivery(binding, source, tmp_path, observed)
    observed["record_errors"].append({"type": "ValueError", "message": "cache prefix changed"})
    observed["final_collector_health"] = None
    assert entry.select_exit(observed) == (73 if code == 0 else 7)


def test_worker_rechecks_startup_candidates_after_controller_admission(evidence, monkeypatch):
    binding, _ = startup_binding(evidence)
    binding.update(
        schema=1,
        ip=str(evidence.target),
        git=str(evidence.target),
        host_netns="net:[1]",
        capture_hashes={
            name: entry.sha256((ROOT / "qualification/installed_cli" / name).read_bytes())
            for name in worker.CAPTURE_FILES
        },
    )
    monkeypatch.setattr(worker, "_VERIFIED_SOURCES", {})
    worker._verify_binding(binding)
    write(evidence.prefix / "bin/sitecustomize.py", b"INERT NEW CANDIDATE\n")
    with pytest.raises(ValueError, match="startup"):
        worker._verify_binding(binding)


def test_target_startup_roots_come_from_sys_only_metadata(tmp_path, monkeypatch):
    target = write(tmp_path / "python", b"INERT INTERPRETER", 0o700)
    calls = []
    metadata = {
        "version": "3.12 synthetic",
        "executable": str(target),
        "implementation": "cpython",
        "version_info": [3, 12],
        "runtime_version": [3, 12, 12],
        "base_prefix": str(tmp_path),
        "base_exec_prefix": str(tmp_path),
        "platlibdir": "lib",
        "stdlib_paths": [str(tmp_path / "stdlib")],
        "extension_suffixes": [".so"],
        "cache_prefix_supported": True,
    }

    def run(argv, **kwargs):
        calls.append(argv)
        kwargs["stdout"].write((json.dumps(metadata) + "\n").encode())
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(entry.subprocess, "run", run)
    observed = entry.interpreter_identity(target, target=True)
    assert calls[0][1:5] == ["-I", "-S", "-B", "-c"]
    assert "code_forge" not in calls[0][-1] and "import site" not in calls[0][-1]
    assert observed["stdlib_paths"] == metadata["stdlib_paths"]
    assert observed["extension_suffixes"] == [".so"]
    assert observed["runtime_version"] == [3, 12, 12]
    assert observed["base_prefix"] == observed["base_exec_prefix"] == str(tmp_path)


def test_sys_only_python_uses_new_cache_path_without_loading_old_cache(tmp_path):
    prefix = tmp_path / "new-cache"
    prefix.mkdir(mode=0o700)
    source = write(tmp_path / "source/module.py", b"# never imported\n")
    old_cache = write(tmp_path / "source/__pycache__/module.cpython-312.pyc", b"INERT OLD CACHE")
    code = (
        "import importlib.util,sys; print(sys.pycache_prefix); print(sys.dont_write_bytecode); "
        "print(importlib.util.cache_from_source(sys.argv[1]))"
    )
    completed = subprocess.run(
        [sys.executable, "-S", "-B", "-c", code, str(source)],
        env={
            "PATH": os.defpath,
            "PYTHONPYCACHEPREFIX": str(prefix),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        },
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert completed.returncode == 0 and completed.stderr == ""
    selected_prefix, no_write, selected_cache = completed.stdout.splitlines()
    assert selected_prefix == str(prefix) and no_write == "True"
    assert Path(selected_cache).is_relative_to(prefix) and Path(selected_cache) != old_cache
    assert old_cache.read_bytes() == b"INERT OLD CACHE" and not any(prefix.iterdir())


def test_final_cache_prefix_must_belong_to_exact_worker_output(tmp_path):
    prefix = tmp_path / "unrelated-empty-cache"
    prefix.mkdir(mode=0o700)
    value = result()
    value["worker"]["cache_before_launch"] = worker.cache_snapshot(prefix)
    source = ROOT / "qualification/installed_cli/capture_worker.py"
    binding = {"capture_hashes": {"capture_worker.py": entry.sha256(source.read_bytes())}}
    with pytest.raises(ValueError, match="bound to the new worker output"):
        entry.final_delivery(binding, source, tmp_path, value)


@pytest.fixture
def uv_layout(evidence, monkeypatch):
    parent = evidence.root / "uv/python"
    physical = parent / "cpython-3.12.12-linux-x86_64-gnu"
    (physical / "lib/python3.12/lib-dynload").mkdir(parents=True)
    alias = parent / "cpython-3.12-linux-x86_64-gnu"
    alias.symlink_to(physical.name, target_is_directory=True)
    metadata = entry.interpreter_identity(evidence.target, target=True)
    metadata.update(
        base_prefix=str(alias),
        base_exec_prefix=str(alias),
        stdlib_paths=[
            str(alias / "lib/python312.zip"),
            str(alias / "lib/python3.12"),
            str(alias / "lib/python3.12/lib-dynload"),
        ],
    )
    monkeypatch.setattr(entry, "interpreter_identity", lambda *args, **kwargs: dict(metadata))
    config = evidence.prefix / "pyvenv.cfg"
    config.write_text(f"home = {alias}/bin\ninclude-system-site-packages = false\nversion = 3.12.12\n")
    return SimpleNamespace(
        evidence=evidence,
        parent=parent,
        alias=alias,
        physical=physical,
        metadata=metadata,
        config=config,
    )


@pytest.mark.parametrize("link_kind", ["relative", "absolute"])
@pytest.mark.parametrize("zip_kind", ["missing", "present"])
def test_uv_version_alias_binds_raw_and_physical_startup_roots(uv_layout, link_kind, zip_kind):
    layout = uv_layout
    if link_kind == "absolute":
        layout.alias.unlink()
        layout.alias.symlink_to(layout.physical, target_is_directory=True)
    if zip_kind == "present":
        with zipfile.ZipFile(layout.physical / "lib/python312.zip", "w") as archive:
            archive.writestr("safe.py", b"# INERT ZIP MEMBER; never imported\n")
    binding, policy = startup_binding(layout.evidence)
    alias = policy["stdlib_alias"]
    assert alias["path"] == str(layout.alias) and alias["physical"] == str(layout.physical)
    assert alias["link_target"] == (
        layout.physical.name if link_kind == "relative" else str(layout.physical)
    )
    assert alias["link_identity"]["ino"] == layout.alias.lstat().st_ino
    assert alias["physical_identity"]["ino"] == layout.physical.lstat().st_ino
    assert policy["roots"][1:4] == layout.metadata["stdlib_paths"]
    for row in policy["observed"][1:4]:
        assert row["physical_path"] == str(layout.physical / Path(row["path"]).relative_to(layout.alias))
    assert policy["observed"][1]["kind"] == ("zip" if zip_kind == "present" else "missing_zip")
    assert worker.admit_startup(policy) == policy["observed"]
    assert binding["launcher"] == str(layout.evidence.launcher)
    assert binding["target_python"] == str(layout.evidence.target)
    assert policy["venv_config_sha256"] == entry.sha256(layout.config.read_bytes())
    # The exception does not relax either ordinary directory-admission rule.
    with pytest.raises(ValueError, match="symlink"):
        worker._absolute(layout.alias / "lib/python3.12")
    with pytest.raises(ValueError, match="symlink"):
        entry.checked_directory(layout.alias / "lib/python3.12")


@pytest.mark.parametrize(
    "fault",
    [
        "home",
        "exec_prefix",
        "version",
        "target",
        "dangling",
        "parent_link",
        "destination_link",
        "nested_root",
        "parent_permissions",
        "physical_permissions",
        "unreported_alias",
        "unversioned_name",
    ],
)
def test_uv_alias_admission_rejects_untrusted_or_unsupported_layout(uv_layout, fault):
    layout = uv_layout
    if fault == "home":
        layout.config.write_text("home = /unrelated/bin\ninclude-system-site-packages = false\n")
    elif fault == "exec_prefix":
        layout.metadata["base_exec_prefix"] = str(layout.physical)
    elif fault == "version":
        layout.metadata["runtime_version"] = [3, 12, 13]
    elif fault == "target":
        layout.alias.unlink()
        layout.alias.symlink_to(layout.evidence.root, target_is_directory=True)
    elif fault == "dangling":
        layout.physical.rename(layout.physical.with_name("removed"))
    elif fault == "parent_link":
        original = layout.parent.with_name("original-parent")
        layout.parent.rename(original)
        layout.parent.symlink_to(original, target_is_directory=True)
    elif fault == "destination_link":
        original = layout.physical.with_name("original-destination")
        layout.physical.rename(original)
        layout.physical.symlink_to(original, target_is_directory=True)
    elif fault == "nested_root":
        root = layout.physical / "lib/python3.12/lib-dynload"
        root.rename(root.with_name("original-dynload"))
        root.symlink_to(root.with_name("original-dynload"), target_is_directory=True)
    elif fault == "parent_permissions":
        layout.parent.chmod(0o777)
    elif fault == "physical_permissions":
        layout.physical.chmod(0o777)
    elif fault == "unreported_alias":
        layout.metadata["base_prefix"] = str(layout.physical)
    else:
        layout.metadata["base_prefix"] = str(layout.alias.with_name("arbitrary-alias"))
        Path(layout.metadata["base_prefix"]).symlink_to(layout.physical, target_is_directory=True)
        layout.metadata["base_exec_prefix"] = layout.metadata["base_prefix"]
        layout.config.write_text(
            f"home = {layout.metadata['base_prefix']}/bin\ninclude-system-site-packages = false\n"
        )
    with pytest.raises((ValueError, OSError)):
        startup_binding(layout.evidence)


@pytest.mark.parametrize(
    "fault", ["retarget", "dangling", "recreated", "physical_replaced", "parent_identity", "config"]
)
def test_bound_uv_alias_changes_fail_before_canonical_inventory(uv_layout, monkeypatch, fault):
    layout = uv_layout
    _, policy = startup_binding(layout.evidence)
    if fault in {"retarget", "dangling", "recreated"}:
        layout.alias.rename(layout.alias.with_name("old-alias"))
        target = {
            "retarget": layout.evidence.root,
            "dangling": layout.parent / "absent",
            "recreated": layout.physical.name,
        }[fault]
        layout.alias.symlink_to(target, target_is_directory=True)
    elif fault == "physical_replaced":
        layout.physical.rename(layout.physical.with_name("old-physical"))
        # Preserve an otherwise admissible inert layout. An empty replacement
        # would fail for missing stdlib roots even if identity binding broke.
        (layout.physical / "lib/python3.12/lib-dynload").mkdir(parents=True)
    elif fault == "parent_identity":
        layout.parent.chmod(0o750 if layout.parent.stat().st_mode & 0o777 == 0o700 else 0o700)
    else:
        layout.config.write_text(layout.config.read_text() + "# changed\n")
    original = Path.iterdir

    def no_canonical_inventory(path):
        assert not path.is_relative_to(layout.physical), "changed binding must fail before inventory"
        return original(path)

    monkeypatch.setattr(Path, "iterdir", no_canonical_inventory)
    with pytest.raises((ValueError, OSError)):
        worker.admit_startup(policy)


@pytest.mark.parametrize(
    "candidate",
    [
        "code_forge_launcher.py",
        "code_forge_launcher.pyc",
        "code_forge_launcher/__init__.py",
        "code_forge_launcher.so",
        "sitecustomize.py",
        "__pycache__/usercustomize.cpython-312.pyc",
        "zip",
    ],
)
def test_uv_canonical_roots_still_reject_new_startup_candidates(uv_layout, candidate):
    layout = uv_layout
    _, policy = startup_binding(layout.evidence)
    if candidate == "zip":
        with zipfile.ZipFile(layout.physical / "lib/python312.zip", "w") as archive:
            archive.writestr("sitecustomize.py", b"INERT NEW ZIP CANDIDATE")
    else:
        write(layout.physical / "lib/python3.12" / candidate, b"INERT NEW CANDIDATE")
    with pytest.raises(ValueError, match="startup|launcher|ZIP"):
        worker.admit_startup(policy)


@pytest.mark.parametrize("owner_field", ["st_uid", "st_gid"])
def test_uv_alias_owner_and_mid_observation_replacement_rejected(uv_layout, monkeypatch, owner_field):
    layout = uv_layout
    target, home = layout.metadata, str(layout.alias / "bin")
    original_lstat = Path.lstat

    def foreign_owner(path, *args, **kwargs):
        info = original_lstat(path, *args, **kwargs)
        if path == layout.alias:
            return SimpleNamespace(
                **{
                    name: (424242 if name == owner_field else getattr(info, name))
                    for name in dir(info)
                    if name.startswith("st_")
                }
            )
        return info

    with monkeypatch.context() as context:
        context.setattr(Path, "lstat", foreign_owner)
        with pytest.raises(ValueError, match="owner"):
            worker.observe_stdlib_alias(target, home)
    original_readlink = os.readlink
    reads = []

    def replace_during_observation(path, *args, **kwargs):
        value = original_readlink(path, *args, **kwargs)
        if path == layout.alias and not reads:
            reads.append(value)
            layout.alias.rename(layout.alias.with_name("old-alias"))
            layout.alias.symlink_to(layout.physical, target_is_directory=True)
        return value

    monkeypatch.setattr(worker.os, "readlink", replace_during_observation)
    with pytest.raises(ValueError, match="changed during observation"):
        worker.observe_stdlib_alias(target, home)


@pytest.mark.parametrize("mode", ["baseline", "diagnostic"])
def test_worker_rechecks_alias_after_setup_before_any_cli_launch(uv_layout, tmp_path, monkeypatch, mode):
    layout = uv_layout
    binding, policy = startup_binding(layout.evidence)
    calls = []
    child = SimpleNamespace(pid=123, wait=lambda **kwargs: 7, poll=lambda: 7)
    output = tmp_path / "worker-output"
    output.mkdir()
    worker_control(output, monkeypatch, child)

    def verify(value):
        assert worker.admit_startup(value["startup_admission"]) == policy["observed"]
        calls.append("admitted")

    def fixture(*args):
        layout.alias.rename(layout.alias.with_name("old-alias"))
        layout.alias.symlink_to(layout.physical, target_is_directory=True)
        calls.append("setup-changed-alias")
        return [str(layout.evidence.launcher)], output

    monkeypatch.setattr(worker, "_verify_binding", verify)
    monkeypatch.setattr(worker, "_fixture", fixture)
    monkeypatch.setattr(worker.subprocess, "Popen", lambda *a, **k: calls.append("CLI LAUNCHED"))
    monkeypatch.setattr(
        worker, "_module", lambda *args: pytest.fail("collector entered after alias changed")
    )
    observed = worker.execute(binding, mode, output)
    assert calls == ["admitted", "setup-changed-alias"]
    assert observed["primary_exception"]["type"] == "ValueError"
    assert "alias binding changed" in observed["primary_exception"]["message"]
    assert observed["child_pid"] is None and observed["child_returncode"] is None
    assert observed["cleanup_errors"] == []
    mapped = result(mode=mode)
    mapped["worker"] = observed
    assert entry.select_exit(mapped) == 73


@pytest.mark.parametrize("mode", ["baseline", "diagnostic"])
@pytest.mark.parametrize("code", [0, 7], ids=["zero", "seven"])
def test_post_owner_alias_failure_preserves_original_exit(uv_layout, tmp_path, mode, code):
    binding, _ = startup_binding(uv_layout.evidence)
    prefix = tmp_path / "worker/pycache"
    prefix.mkdir(mode=0o700, parents=True)
    observed = result(mode=mode, code=code)
    observed["worker"]["cache_before_launch"] = worker.cache_snapshot(prefix)
    source = ROOT / "qualification/installed_cli/capture_worker.py"
    binding["capture_hashes"] = {"capture_worker.py": entry.sha256(source.read_bytes())}
    uv_layout.alias.rename(uv_layout.alias.with_name("old-alias"))
    uv_layout.alias.symlink_to(uv_layout.physical, target_is_directory=True)
    with pytest.raises(ValueError, match="alias binding changed"):
        entry.final_delivery(binding, source, tmp_path, observed)
    observed["record_errors"].append({"type": "ValueError", "message": "alias binding changed"})
    observed["final_collector_health"] = None
    assert entry.select_exit(observed) == (73 if code == 0 else 7)


def test_bound_alias_ownership_survives_only_equivalent_user_namespace_view(uv_layout, monkeypatch):
    layout = uv_layout
    _, policy = startup_binding(layout.evidence)
    assert policy["stdlib_alias_controller_ids"] == {"uid": os.geteuid(), "gid": os.getegid()}
    original_lstat = Path.lstat
    owned = {layout.alias, layout.parent, layout.physical}

    # Model the same owning user/group becoming namespace IDs 0/0. No namespace
    # is created; all link/inode/mode/time observations remain the real fixture.
    def mapped_lstat(path, *args, **kwargs):
        info = original_lstat(path, *args, **kwargs)
        if path in owned:
            return SimpleNamespace(
                **{
                    name: (0 if name in {"st_uid", "st_gid"} else getattr(info, name))
                    for name in dir(info)
                    if name.startswith("st_")
                }
            )
        return info

    monkeypatch.setattr(Path, "lstat", mapped_lstat)
    monkeypatch.setattr(worker.os, "geteuid", lambda: 0)
    monkeypatch.setattr(worker.os, "getegid", lambda: 0)
    assert worker.admit_startup(policy) == policy["observed"]
    monkeypatch.setattr(worker.os, "getegid", lambda: 1)
    with pytest.raises(ValueError, match="alias"):
        worker.admit_startup(policy)


def cpython312_venv_rules(raw):
    """Inert reference for site.venv's loop, not site startup or hook execution."""
    system_site, home = "true", None
    # Text files with newline=None normalize CR/CRLF, not other Unicode lines.
    text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    for line in text.split("\n"):
        if "=" in line:
            key, value = line.split("=", 1)
            normalized = key.strip().lower()
            if normalized == "include-system-site-packages":
                system_site = value.strip().lower()
            elif normalized == "home":
                home = value.strip()
    return system_site, home


@pytest.mark.parametrize(
    "raw,home",
    [
        (b"home = /Case/Kept/bin\ninclude-system-site-packages = false\n", "/Case/Kept/bin"),
        (b" Home = /Case/Kept/bin \n INCLUDE-SYSTEM-SITE-PACKAGES = FALSE \n", "/Case/Kept/bin"),
        (b"HOME=/Case/Kept/bin\r\ninclude-system-site-packages=False\r\n", "/Case/Kept/bin"),
        (b"home=/Case/Kept/bin\rinclude-system-site-packages=false\r", "/Case/Kept/bin"),
        (
            b"ignored line\nhome=/Case/Kept=Value/bin\ninclude-system-site-packages=false\n",
            "/Case/Kept=Value/bin",
        ),
    ],
    ids=["ordinary", "mixed-case", "crlf", "cr", "first-equals"],
)
def test_pyvenv_single_keys_follow_cpython312_rules(raw, home):
    assert cpython312_venv_rules(raw) == ("false", home)
    observed = entry.parse_pyvenv(raw)
    assert observed["home"] == home
    assert observed["include-system-site-packages"].lower() == "false"


@pytest.mark.parametrize(
    "raw,site_result",
    [
        (b"include-system-site-packages=false\nINCLUDE-SYSTEM-SITE-PACKAGES=true\n", "true"),
        (b"include-system-site-packages=false\n include-system-site-packages =true\n", "true"),
        (b"include-system-site-packages=false\ninclude-system-site-packages=false\n", "false"),
        (
            b"include-system-site-packages=true\n include-system-site-packages =false\ninclude-system-site-packages=true\n",
            "true",
        ),
        (b"home=/first\nHOME=/last\ninclude-system-site-packages=false\n", "false"),
        (b"unknown=first\n UNKNOWN =last\ninclude-system-site-packages=false\n", "false"),
    ],
    ids=["case-site", "padded-site", "exact-site", "raw-order-site", "case-home", "unknown"],
)
def test_pyvenv_duplicate_normalized_keys_fail_closed(raw, site_result):
    assert cpython312_venv_rules(raw)[0] == site_result
    with pytest.raises(ValueError, match="duplicate normalized pyvenv key"):
        entry.parse_pyvenv(raw)


@pytest.mark.parametrize(
    "raw",
    [
        b"home=/synthetic/bin\n",
        b"include-system-site-packages=TRUE\n",
        b"include-system-site-packages=anything\n",
        b"include-system-site-packages=false\vhome=/synthetic/bin\n",
        "include-system-site-packages=false\u2028home=/synthetic/bin\n".encode(),
    ],
    ids=["missing", "true", "unknown", "vertical-separator", "unicode-separator"],
)
def test_pyvenv_only_explicit_false_is_admitted(raw):
    assert cpython312_venv_rules(raw)[0] != "false"
    with pytest.raises(ValueError, match="system site packages"):
        entry.parse_pyvenv(raw)


@pytest.mark.parametrize("kind", ["mixed-valid", "mixed-unsafe", "raw-order-unsafe"])
def test_installation_pyvenv_admission_matches_selected_site_rules(evidence, kind):
    raw = {
        "mixed-valid": b"HOME=/synthetic/bin\n Include-System-Site-Packages = False\n",
        "mixed-unsafe": b"include-system-site-packages=false\nINCLUDE-SYSTEM-SITE-PACKAGES=true\n",
        "raw-order-unsafe": b"include-system-site-packages=true\n include-system-site-packages =false\ninclude-system-site-packages=true\n",
    }[kind]
    (evidence.prefix / "pyvenv.cfg").write_bytes(raw)
    assert cpython312_venv_rules(raw)[0] == ("false" if kind == "mixed-valid" else "true")
    if kind == "mixed-valid":
        binding, _, _ = entry.verify_installation(evidence.root)
        assert binding["launcher"] == str(evidence.launcher)
        assert "stdlib_alias" not in binding["startup_admission"]
    else:
        with pytest.raises(ValueError, match="duplicate normalized pyvenv key"):
            entry.verify_installation(evidence.root)


@pytest.mark.parametrize("kind", ["mixed-valid", "duplicate-home"])
def test_alias_home_uses_same_normalized_pyvenv_policy(uv_layout, kind):
    home = str(uv_layout.alias / "bin")
    raw = f" Home = {home}\n Include-System-Site-Packages = FaLsE\n".encode()
    if kind == "duplicate-home":
        raw += f"HOME={home}\n".encode()
    uv_layout.config.write_bytes(raw)
    assert cpython312_venv_rules(raw) == ("false", home)
    if kind == "mixed-valid":
        _, policy = startup_binding(uv_layout.evidence)
        assert policy["stdlib_alias"]["venv_home"] == home
        assert worker.admit_startup(policy) == policy["observed"]
    else:
        # Exercise alias derivation directly as well as whole installation.
        with pytest.raises(ValueError, match="duplicate normalized pyvenv key"):
            entry.startup_policy(
                uv_layout.evidence.prefix,
                uv_layout.evidence.site,
                uv_layout.metadata,
                entry.sha256((uv_layout.evidence.site / "code_forge_launcher.py").read_bytes()),
            )
        with pytest.raises(ValueError, match="duplicate normalized pyvenv key"):
            startup_binding(uv_layout.evidence)


@pytest.mark.parametrize("separator", ["\v", "\f", "\x85", "\u2028"], ids=["vt", "ff", "nel", "ls"])
def test_non_newline_characters_do_not_create_pyvenv_keys(separator):
    raw = f"unknown=value{separator}home=/not-a-key\ninclude-system-site-packages=false\n".encode()
    assert cpython312_venv_rules(raw) == ("false", None)
    assert "home" not in entry.parse_pyvenv(raw)
    raw = f"unknown=value{separator}include-system-site-packages=false\n".encode()
    assert cpython312_venv_rules(raw) == ("true", None)
    with pytest.raises(ValueError, match="system site packages"):
        entry.parse_pyvenv(raw)


@pytest.mark.parametrize("kind", ["same-content", "directory", "link", "dangling"])
def test_bin_pyvenv_candidate_rejected_before_target_metadata(evidence, monkeypatch, kind):
    candidate = evidence.prefix / "bin/pyvenv.cfg"
    if kind == "same-content":
        candidate.write_bytes((evidence.prefix / "pyvenv.cfg").read_bytes())
    elif kind == "directory":
        candidate.mkdir()
    else:
        candidate.symlink_to(evidence.prefix / ("pyvenv.cfg" if kind == "link" else "absent.cfg"))
    monkeypatch.setattr(
        entry, "interpreter_identity", lambda *args, **kwargs: pytest.fail("target metadata reached")
    )
    with pytest.raises(ValueError, match="higher-priority bin/pyvenv.cfg"):
        entry.verify_installation(evidence.root)


@pytest.mark.parametrize("mode", ["baseline", "diagnostic"])
@pytest.mark.parametrize("fault", ["bin-same-content", "bin-dangling", "prefix-inert-change"])
def test_worker_rechecks_no_alias_config_after_setup_before_launch(
    evidence, tmp_path, monkeypatch, mode, fault
):
    binding, policy = startup_binding(evidence)
    assert "stdlib_alias" not in policy
    assert policy["venv_config_sha256"] == entry.sha256((evidence.prefix / "pyvenv.cfg").read_bytes())
    calls = []
    output = tmp_path / "worker-output"
    output.mkdir()
    child = SimpleNamespace(pid=123, wait=lambda **kwargs: 7, poll=lambda: 7)
    worker_control(output, monkeypatch, child)

    def verify(value):
        assert worker.admit_startup(value["startup_admission"]) == policy["observed"]
        calls.append("admitted")

    def fixture(*args):
        config = evidence.prefix / "pyvenv.cfg"
        if fault == "bin-same-content":
            (evidence.prefix / "bin/pyvenv.cfg").write_bytes(config.read_bytes())
        elif fault == "bin-dangling":
            (evidence.prefix / "bin/pyvenv.cfg").symlink_to(evidence.prefix / "absent.cfg")
        else:
            config.write_bytes(config.read_bytes() + b"# inert comment; semantics unchanged\n")
        calls.append("config-changed")
        return [str(evidence.launcher)], output

    monkeypatch.setattr(worker, "_verify_binding", verify)
    monkeypatch.setattr(worker, "_fixture", fixture)
    monkeypatch.setattr(worker.subprocess, "Popen", lambda *args, **kwargs: calls.append("CLI LAUNCHED"))
    monkeypatch.setattr(
        worker, "_module", lambda *args: pytest.fail("collector entered after config change")
    )
    observed = worker.execute(binding, mode, output)
    assert calls == ["admitted", "config-changed"]
    assert observed["child_pid"] is None and observed["child_returncode"] is None
    assert observed["primary_exception"]["type"] == "ValueError"
    assert "pyvenv" in observed["primary_exception"]["message"]
    assert observed["cleanup_errors"] == []
    mapped = result(mode=mode)
    mapped["worker"] = observed
    assert entry.select_exit(mapped) == 73


@pytest.mark.parametrize("mode", ["baseline", "diagnostic"])
@pytest.mark.parametrize("code", [0, 7], ids=["zero", "seven"])
@pytest.mark.parametrize("fault", ["bin-same-content", "prefix-inert-change"])
def test_post_owner_no_alias_config_failure_preserves_original_exit(
    evidence, tmp_path, mode, code, fault
):
    binding, policy = startup_binding(evidence)
    assert "stdlib_alias" not in policy
    prefix = tmp_path / "worker/pycache"
    prefix.mkdir(mode=0o700, parents=True)
    observed = result(mode=mode, code=code)
    observed["worker"]["cache_before_launch"] = worker.cache_snapshot(prefix)
    source = ROOT / "qualification/installed_cli/capture_worker.py"
    binding["capture_hashes"] = {"capture_worker.py": entry.sha256(source.read_bytes())}
    config = evidence.prefix / "pyvenv.cfg"
    if fault == "bin-same-content":
        (evidence.prefix / "bin/pyvenv.cfg").write_bytes(config.read_bytes())
    else:
        config.write_bytes(config.read_bytes() + b"# inert comment; semantics unchanged\n")
    with pytest.raises(ValueError, match="pyvenv"):
        entry.final_delivery(binding, source, tmp_path, observed)
    observed["record_errors"].append({"type": "ValueError", "message": "pyvenv config changed"})
    observed["final_collector_health"] = None
    assert entry.select_exit(observed) == (73 if code == 0 else 7)


@pytest.mark.parametrize("name", ["ip", "git", "strace", "unshare"])
def test_utility_search_is_fixed_and_ip_includes_system_sbin(tmp_path, monkeypatch, name):
    binary = tmp_path / name
    binary.write_bytes(b"synthetic utility")
    seen = []
    monkeypatch.setenv("PATH", "/untrusted/credential-path")
    monkeypatch.setattr(
        entry.shutil, "which", lambda command, *, path: seen.append((command, path)) or str(binary)
    )
    result = entry.utility(name)
    expected = os.defpath + os.pathsep + "/usr/sbin:/sbin" if name == "ip" else os.defpath
    assert seen == [(name, expected)]
    assert result == {"path": str(binary), "sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}


@pytest.mark.usefixtures("preserve_controller_umask")
@pytest.mark.parametrize(
    "target,stage,error,category",
    [
        ("sanitize_controller", "SANITIZE", PermissionError(13, "secret/path"), "PERMISSION_DENIED"),
        ("preflight", "PREFLIGHT", ValueError("secret"), "INVALID_DATA"),
        ("apply_limits", "LIMITS", OSError(5, "secret"), "OS_ERROR"),
        ("verify_installation", "INSTALLATION", FileNotFoundError(2, "secret"), "NOT_FOUND"),
        ("utility", "UTILITY_BINDING", NotADirectoryError(20, "secret"), "NOT_DIRECTORY"),
        ("load_owner", "OWNER_LOAD", RuntimeError("secret"), "OTHER_ERROR"),
    ],
    ids=[
        "sanitize-denied",
        "preflight-invalid",
        "limits-io",
        "installation-missing",
        "utility-not-directory",
        "owner-other",
    ],
)
def test_controller_records_closed_failed_stage(tmp_path, monkeypatch, target, stage, error, category):
    controller_control(tmp_path, monkeypatch)

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(entry, target, fail)
    output = tmp_path / "output"
    assert entry.capture(tmp_path / "unused", "baseline", output) == 73
    value = entry.read_json(output / "result.json")
    assert value["controller_stage"] == stage
    assert value["controller_error_category"] == category


@pytest.mark.usefixtures("preserve_controller_umask")
@pytest.mark.parametrize(
    "kind,category,code", [("timeout", "TIMEOUT", 124), ("interrupt", "INTERRUPTED", 130)]
)
def test_first_owner_failure_survives_later_missing_receipt(tmp_path, monkeypatch, kind, category, code):
    failure = subprocess.TimeoutExpired(["secret"], 35) if kind == "timeout" else KeyboardInterrupt()
    controller_control(tmp_path, monkeypatch, owner_error=failure)
    output = tmp_path / "output"
    assert entry.capture(tmp_path / "unused", "baseline", output) == code
    value = entry.read_json(output / "result.json")
    assert value["controller_stage"] == "WORKER_EXECUTION"
    assert value["controller_error_category"] == category
    assert value["record_errors"]  # receipt absence must not replace first failure


@pytest.mark.usefixtures("preserve_controller_umask")
def test_worker_one_without_receipt_is_not_attributed_to_cli_or_namespace(tmp_path, monkeypatch):
    controller_control(tmp_path, monkeypatch)
    monkeypatch.setattr(
        entry,
        "load_owner",
        lambda *a: (
            lambda *a, **k: SimpleNamespace(
                returncode=1,
                ownership={"cleanup_complete": True, "owned": []},
                stdout=b"",
                stderr=b"private arbitrary error",
            )
        ),
    )
    output = tmp_path / "output"
    assert entry.capture(tmp_path / "unused", "baseline", output) == 73
    value = entry.read_json(output / "result.json")
    assert value["controller_stage"] == "WORKER_RECEIPT"
    assert value["controller_error_category"] == "NOT_FOUND"
    assert value["worker_returncode"] == 1 and value["worker"] is None
    assert value["outer_exception"] is None


@pytest.mark.usefixtures("preserve_controller_umask")
def test_controller_no_caught_failure_is_not_cli_success(tmp_path, monkeypatch):
    controller_control(tmp_path, monkeypatch, code=7)
    output = tmp_path / "output"
    assert entry.capture(tmp_path / "unused", "baseline", output) == 7
    value = entry.read_json(output / "result.json")
    assert value["controller_stage"] == "FINALIZE"
    assert value["controller_error_category"] == "NONE"
    assert value["worker"]["child_returncode"] == 7


@pytest.mark.parametrize(
    "number,expected",
    [
        (1, "PERMISSION_DENIED"),
        (13, "PERMISSION_DENIED"),
        (2, "NOT_FOUND"),
        (20, "NOT_DIRECTORY"),
        (5, "OS_ERROR"),
        (True, "OS_ERROR"),
        ("secret", "OS_ERROR"),
        (["secret"], "OS_ERROR"),
    ],
    ids=["eperm", "eacces", "enoent", "enotdir", "eio", "bool", "text", "list"],
)
def test_controller_error_category_never_copies_dynamic_errno(number, expected):
    record = {"controller_stage": "INITIALIZE", "controller_error_category": "NONE"}
    error = OSError(number, "secret/path")
    entry.controller_failure(record, "PREFLIGHT", error)
    assert record == {"controller_stage": "PREFLIGHT", "controller_error_category": expected}


@pytest.mark.usefixtures("preserve_controller_umask")
def test_controller_failure_reaches_public_projection_without_private_error(tmp_path, monkeypatch):
    controller_control(tmp_path, monkeypatch)
    monkeypatch.setattr(
        entry,
        "load_owner",
        lambda *a: (
            lambda *a, **k: SimpleNamespace(
                returncode=1,
                ownership={"cleanup_complete": True, "owned": []},
                stdout=b"",
                stderr=b"PRIVATE-CREDENTIAL-CANARY",
            )
        ),
    )
    output = tmp_path / "output"
    code = entry.capture(tmp_path / "unused", "baseline", output)
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            str(ROOT / "scripts/cli_capture_public_summary.py"),
            "--result",
            str(output / "result.json"),
            "--mode",
            "baseline",
            "--shell-exit",
            str(code),
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0  # receipt projection, not CLI qualification
    assert b"PRIVATE-CREDENTIAL-CANARY" not in result.stdout + result.stderr
    assert str(tmp_path).encode() not in result.stdout + result.stderr
    value = json.loads(result.stdout)
    assert value["controller_stage"] == "WORKER_RECEIPT"
    assert value["controller_error_category"] == "NOT_FOUND"
    assert value["failure_observation"] == "WORKER_NONZERO_NO_RECEIPT"
    assert value["worker_returncode"] == 1 and value["harness_shell_exit"] == 73
    assert value["cli_returncode"] is None and value["qualification"] == "NOT_ASSESSED"


@pytest.mark.parametrize(
    "raw,label",
    [
        (
            b"unshare: unshare failed: Operation not permitted\n",
            "STDERR_EXACT_UNSHARE_OPERATION_NOT_PERMITTED",
        ),
        (b"unshare: unshare failed: Permission denied\n", "STDERR_EXACT_UNSHARE_PERMISSION_DENIED"),
    ],
)
def test_launch_observation_matches_only_entire_literal(raw, label):
    record = {"worker_returncode": 1, "worker": None, "outer_exception": None}
    assert entry.launch_stderr_observation(record, raw) == label
    for changed in (
        raw[:-1],
        raw + b"\n",
        b" " + raw,
        raw + b"SECRET=/private/path",
        raw.upper(),
        raw.replace(b"\n", b"\r\n"),
        b"/usr/bin/" + raw,
    ):
        assert entry.launch_stderr_observation(record, changed) == "NOT_RECOGNIZED"
    for invalid in (None, raw.decode(), bytearray(raw), {}, True):
        assert entry.launch_stderr_observation(record, invalid) == "NOT_ASSESSED"


@pytest.mark.parametrize(
    "patch",
    [
        {"worker_returncode": 0},
        {"worker_returncode": 73},
        {"worker_returncode": -9},
        {"worker_returncode": True},
        {"worker_returncode": "1"},
        {"worker": {}},
        {"outer_exception": {}},
    ],
)
def test_launch_observation_requires_actual_failed_worker_without_receipt(patch):
    record = {"worker_returncode": 1, "worker": None, "outer_exception": None} | patch
    raw = b"unshare: unshare failed: Operation not permitted\n"
    assert entry.launch_stderr_observation(record, raw) == "NOT_ASSESSED"


def test_launch_observation_does_not_match_truncated_prefix(monkeypatch):
    raw = b"unshare: unshare failed: Operation not permitted\n"
    record = {"worker_returncode": 1, "worker": None, "outer_exception": None}
    monkeypatch.setattr(entry, "FILE_LIMIT", len(raw) - 1)
    assert entry.launch_stderr_observation(record, raw) == "NOT_ASSESSED"


@pytest.mark.usefixtures("preserve_controller_umask")
@pytest.mark.parametrize(
    "raw,expected",
    [
        (
            b"unshare: unshare failed: Operation not permitted\n",
            "STDERR_EXACT_UNSHARE_OPERATION_NOT_PERMITTED",
        ),
        (b"unshare: unshare failed: Permission denied\n", "STDERR_EXACT_UNSHARE_PERMISSION_DENIED"),
        (b"unshare: unshare failed: Operation not permitted\nPRIVATE-CANARY", "NOT_RECOGNIZED"),
    ],
    ids=["operation", "permission", "private-suffix"],
)
def test_controller_launch_literal_reaches_public_without_cause_claim(
    tmp_path, monkeypatch, raw, expected
):
    controller_control(tmp_path, monkeypatch)
    monkeypatch.setattr(
        entry,
        "load_owner",
        lambda *a: (
            lambda *a, **k: SimpleNamespace(
                returncode=1,
                ownership={"cleanup_complete": True, "owned": []},
                stdout=b"",
                stderr=raw,
            )
        ),
    )
    output = tmp_path / "output"
    code = entry.capture(tmp_path / "unused", "baseline", output)
    assert code == 73
    private = entry.read_json(output / "result.json")
    assert private["launch_stderr_observation"] == expected
    assert private["controller_stage"] == "WORKER_RECEIPT"
    assert private["controller_error_category"] == "NOT_FOUND"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            str(ROOT / "scripts/cli_capture_public_summary.py"),
            "--result",
            str(output / "result.json"),
            "--mode",
            "baseline",
            "--shell-exit",
            str(code),
        ],
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0 and result.stderr == b""
    public = json.loads(result.stdout)
    assert public["launch_stderr_observation"] == expected
    assert public["worker_returncode"] == 1 and public["harness_shell_exit"] == 73
    assert public["cli_returncode"] is None and public["qualification"] == "NOT_ASSESSED"
    assert b"PRIVATE-CANARY" not in result.stdout
    assert raw not in result.stdout
    spec = importlib.util.spec_from_file_location(
        "launch_publication", ROOT / "scripts/cli_capture_publication.py"
    )
    publication = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publication)
    public_root = tmp_path / "public"
    public_root.mkdir(mode=0o700)
    capability = {
        "schema": "forge-cli-capture-capability-v1",
        "status": "PRESENT_NOT_QUALIFIED",
        "facts": dict.fromkeys(publication.FACTS, True),
        "live_admission": "NOT_RUN",
        "qualification": "NOT_ASSESSED",
        "raw_evidence": "NOT_PUBLISHED",
    }
    write(public_root / "capability.json", (json.dumps(capability) + "\n").encode())
    write(public_root / "baseline.json", result.stdout)
    publication.publish(public_root, public_root / "published")
    artifact = (public_root / "published/baseline.json").read_bytes()
    assert json.loads(artifact) == public
    assert raw not in artifact and b"PRIVATE-CANARY" not in artifact


@pytest.mark.usefixtures("preserve_controller_umask")
@pytest.mark.parametrize("kind", ["owner-error", "worker-zero", "worker-seven"])
def test_controller_ineligible_launch_never_assesses_stderr(tmp_path, monkeypatch, kind):
    raw = b"unshare: unshare failed: Operation not permitted\n"
    if kind == "owner-error":
        controller_control(
            tmp_path, monkeypatch, owner_error=subprocess.TimeoutExpired(["private"], 35, stderr=raw)
        )
        expected = 124
    else:
        controller_control(tmp_path, monkeypatch)
        code = 0 if kind == "worker-zero" else 7
        monkeypatch.setattr(
            entry,
            "load_owner",
            lambda *a: (
                lambda *a, **k: SimpleNamespace(
                    returncode=code,
                    ownership={"cleanup_complete": True, "owned": []},
                    stdout=b"",
                    stderr=raw,
                )
            ),
        )
        expected = 73
    output = tmp_path / "output"
    assert entry.capture(tmp_path / "unused", "baseline", output) == expected
    value = entry.read_json(output / "result.json")
    assert value["launch_stderr_observation"] == "NOT_ASSESSED"


def test_owner_only_verification_does_not_execute_target(evidence, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("target startup must not run")

    monkeypatch.setattr(entry, "interpreter_identity", forbidden)
    monkeypatch.setattr(entry, "startup_policy", forbidden)
    provenance, sources, origin = entry.verify_installation(evidence.root, owner_only=True)
    assert provenance["owner_hashes"] == {name: entry.sha256(raw) for name, raw in sources.items()}
    assert origin.name == "code_forge"


def test_stock_target_metadata_cannot_execute_unbound_interpreter(evidence, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("unbound target metadata executed")

    monkeypatch.setattr(entry, "interpreter_identity", forbidden)
    with pytest.raises(ValueError, match="stock target path differs"):
        entry.verify_installation(
            evidence.root,
            expected_target={
                "path": "/usr/bin/not-the-fixture",
                "sha256": entry.sha256(evidence.target.read_bytes()),
            },
        )


@pytest.mark.usefixtures("preserve_controller_umask")
@pytest.mark.parametrize("original", [0, 7])
def test_stock_post_owner_host_change_preserves_original_failure(tmp_path, monkeypatch, original):
    commands = controller_control(tmp_path, monkeypatch, code=original)
    prior = entry.verify_installation

    def installation(path, **kwargs):
        value, sources, origin = prior(path)
        value.update(target_python="/usr/bin/python3", target={"version_info": [3, 14]})
        assert kwargs["expected_target"]["path"] == str(Path("/usr/bin/python3").resolve())
        return value, sources, origin

    monkeypatch.setattr(entry, "verify_installation", installation)
    helper = SimpleNamespace(
        prepare=lambda output, mode: {
            "profile_sha256": "a" * 64,
            "programs": {
                "python": {"path": str(Path("/usr/bin/python3").resolve()), "sha256": "a" * 64}
            },
        },
        command=lambda binding, payload: ["stock", *payload],
        host_state=lambda: "changed",
    )
    monkeypatch.setattr(entry, "stock_helper", lambda: (helper, "a" * 64))
    result = entry.capture(
        Path("/synthetic-readonly/evidence"), "baseline", tmp_path / "output", backend="stock-bwrap"
    )
    assert result == (7 if original == 7 else 73)
    assert commands[0][0][0] == "stock"
    receipt = json.loads((tmp_path / "output/result.json").read_text())
    assert receipt["worker"]["child_returncode_before_cleanup"] == original
    assert receipt["record_errors"]


# Canonical producer: pip 26.2.2 _internal/operations/install/wheel.py,
# PipScriptMaker.script_template; _install_wheel instantiates that subclass.
# This is NOT the different vendored distlib SCRIPT_TEMPLATE.
PIP_26_2_2_CONSOLE = """import sys
from code_forge_launcher import cli_main
if __name__ == '__main__':
    sys.argv[0] = sys.argv[0].removesuffix('.exe')
    sys.exit(cli_main())
"""


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "module",
        "function",
        "guard",
        "arguments",
        "extra",
        "suffix",
        "shebang",
        "shell",
        "distlib-default",
    ],
)
def test_pip_26_2_2_actual_override_is_exact(fault):
    target = Path("/tmp/short-venv/bin/python")
    body = PIP_26_2_2_CONSOLE
    shebang = f"#!{target}\n"
    if fault == "module":
        body = body.replace("from code_forge_launcher", "from attacker")
    elif fault == "function":
        body = body.replace("cli_main", "other")
    elif fault == "guard":
        body = body.replace("__name__ == '__main__'", "__name__ != '__main__'")
    elif fault == "arguments":
        body = body.replace("cli_main()", "cli_main('--override')")
    elif fault == "extra":
        body += "raise RuntimeError('EXTRA')\n"
    elif fault == "suffix":
        body = body.replace("'.exe'", "'.pyw'")
    elif fault == "shebang":
        shebang = "#!/tmp/other-python\n"
    elif fault == "shell":
        shebang = "#!/bin/sh\n"
    elif fault == "distlib-default":
        body = "import re\nimport sys\nif __name__ == '__main__':\n    from code_forge_launcher import cli_main\n    sys.argv[0] = re.sub(r'(-script\\.pyw|\\.exe)?$', '', sys.argv[0])\n    sys.exit(cli_main())\n"
    raw = (shebang + body).encode()
    if fault is None:
        entry.verify_launcher(raw, target)
    else:
        with pytest.raises(ValueError):
            entry.verify_launcher(raw, target)


@pytest.mark.parametrize("record_matches", [True, False])
def test_pip_26_2_2_console_installed_record_pipeline(evidence, record_matches):
    raw = (f"#!{evidence.target}\n" + PIP_26_2_2_CONSOLE).encode()
    evidence.launcher.write_bytes(raw)
    if record_matches:
        path = evidence.site / DIST / "RECORD"
        rows = list(csv.reader(io.StringIO(path.read_text())))
        name = os.path.relpath(evidence.launcher, evidence.site)
        replacement = next(csv.reader(io.StringIO(record({name: raw}).decode())))
        rows = [replacement if row[0] == name else row for row in rows]
        stream = io.StringIO()
        csv.writer(stream, lineterminator="\n").writerows(rows)
        path.write_text(stream.getvalue())
        binding, _, _ = entry.verify_installation(evidence.root)
        assert binding["installed_provenance"]["launcher_sha256"] == hashlib.sha256(raw).hexdigest()
        assert binding["launcher"] == str(evidence.launcher)
    else:
        with pytest.raises(ValueError, match="installed RECORD digest mismatch"):
            entry.verify_installation(evidence.root)
