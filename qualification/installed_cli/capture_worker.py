# SPDX-License-Identifier: Apache-2.0
"""Private synthetic capture inside an already isolated network namespace.

This is not a qualifier. Only the enclosing verified owner owns descendants.
The receipt preserves the console's signed status independently of delivery.
"""

import argparse
from contextlib import ExitStack
import hashlib
import io
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import resource
import re
import shutil
import socket
import stat
import subprocess
import threading
import time
import types
import zipfile


MAX_FILE_BYTES = 8 * 1024 * 1024
CHILD_TIMEOUT = 20
SYNTHETIC_KEY = "synthetic-only"
_VERIFIED_SOURCES = {}
CAPTURE_FILES = ("capture_worker.py", "diagnostic_run.py", "diagnostic_probe.py")


def _error(error):
    return {"type": type(error).__name__, "message": str(error)[:4096]}


def _absolute(path):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("absolute non-traversing path required")
    for ancestor in [*reversed(path.parents), path]:
        if ancestor.is_symlink():
            raise ValueError("symlink path rejected")
    return path


def _read_regular(path, limit, *, private=False):
    path = _absolute(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("invalid bounded regular file")
        if private and (info.st_mode & 0o077 or info.st_uid != os.geteuid()):
            raise ValueError("invalid private file owner or permissions")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        after = os.fstat(fd)
        current = path.lstat()

        def identity(item):
            return (
                item.st_dev,
                item.st_ino,
                item.st_size,
                item.st_mtime_ns,
                item.st_ctime_ns,
            )

        if len(raw) > limit or identity(info) != identity(after) or identity(info) != identity(current):
            raise ValueError("file changed during bounded read")
        return raw
    finally:
        os.close(fd)


def _read_private(path, limit=MAX_FILE_BYTES):
    return _read_regular(path, limit, private=True)


def _decode_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=unique)


def _create(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(raw)
            stream.flush()
    finally:
        os.close(fd)


def _json(path, value):
    _create(path, (json.dumps(value, sort_keys=True) + "\n").encode())


def _module(name):
    filename = name + ".py"
    raw = _VERIFIED_SOURCES[filename]
    module = types.ModuleType("capture_" + name)
    module.__file__ = str(Path(__file__).with_name(filename))
    exec(compile(raw, module.__file__, "exec"), module.__dict__)  # noqa: S102 - exact hash-bound source, no bytecode loader
    return module


DISTUTILS_PTH = (
    b"import os; var = 'SETUPTOOLS_USE_DISTUTILS'; enabled = os.environ.get(var, 'local') == 'local'; "
    b"enabled and __import__('_distutils_hack').add_shim(); \n"
)


def _startup_candidate(name, suffixes):
    for module in ("code_forge_launcher", "sitecustomize", "usercustomize"):
        if name == module or name in {module + suffix for suffix in (".py", ".pyc", *suffixes)}:
            return module
    return None


def observe_stdlib_alias(target, home):
    """Admit one corroborated uv version-directory alias, never arbitrary links."""
    alias = Path(target["base_prefix"])
    if not alias.is_symlink():
        return None
    # Parents, destination and all descendants retain the ordinary no-link rule.
    _absolute(alias.parent)
    version = target["runtime_version"]
    match = re.fullmatch(r"cpython-(\d+)\.(\d+)-([A-Za-z0-9_-]+)", alias.name)
    if (
        not alias.is_absolute()
        or ".." in alias.parts
        or target["base_exec_prefix"] != str(alias)
        or home != str(alias / "bin")
        or not isinstance(version, list)
        or len(version) != 3
        or any(type(part) is not int or part < 0 for part in version)
        or match is None
        or [int(match[1]), int(match[2])] != version[:2]
    ):
        raise ValueError("unsupported target version alias metadata")
    destination = alias.with_name("cpython-" + ".".join(map(str, version)) + "-" + match[3])
    before = alias.lstat()
    link = os.readlink(alias)
    if link not in (destination.name, str(destination)):
        raise ValueError("target version alias is not the matching physical sibling")
    _absolute(destination)

    def directory_identity(path):
        info = path.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_gid != os.getegid()
            or info.st_mode & 0o022
        ):
            raise ValueError("untrusted target version alias directory")
        return {key: getattr(info, "st_" + key) for key in ("dev", "ino", "mode")} | {
            "owner": "effective_user_and_group"
        }

    def link_identity(info):
        if not stat.S_ISLNK(info.st_mode) or info.st_uid != os.geteuid() or info.st_gid != os.getegid():
            raise ValueError("untrusted target version alias owner")
        return {
            key: getattr(info, "st_" + key)
            for key in ("dev", "ino", "mode", "size", "mtime_ns", "ctime_ns")
        } | {"owner": "effective_user_and_group"}

    observed = {
        "metadata": {key: target[key] for key in ("base_prefix", "base_exec_prefix", "runtime_version")},
        "venv_home": home,
        "path": str(alias),
        "link_target": link,
        "link_identity": link_identity(before),
        "parent_identity": directory_identity(alias.parent),
        "physical": str(destination),
        "physical_identity": directory_identity(destination),
    }
    if link_identity(alias.lstat()) != observed["link_identity"] or os.readlink(alias) != link:
        raise ValueError("target version alias changed during observation")
    return observed


def check_stdlib_alias(spec):
    """Recheck the selected config, then its optional bound version alias."""
    if spec:
        config = Path(spec["venv_config_path"])
        _absolute(config.parent / "bin")
        if os.path.lexists(config.parent / "bin/pyvenv.cfg"):
            raise ValueError("higher-priority bin/pyvenv.cfg rejected")
        if hashlib.sha256(_read_regular(config, 65536)).hexdigest() != spec["venv_config_sha256"]:
            raise ValueError("pyvenv config binding changed")
    alias = spec.get("stdlib_alias")
    if alias is not None and observe_stdlib_alias(alias["metadata"], alias["venv_home"]) != alias:
        raise ValueError("target version alias binding changed")
    return alias


def admit_startup(spec):
    """Inspect ordinary startup routes as data; never import their candidates."""
    if spec.get("policy") != "private_empty_bytecode_cache" or spec.get("distutils_mode") != "stdlib":
        raise ValueError("unsupported startup policy")
    roots, suffixes = spec["roots"], spec["extension_suffixes"]
    if (
        not isinstance(roots, list)
        or not 2 <= len(roots) <= 16
        or len(roots) != len(set(roots))
        or not isinstance(suffixes, list)
        or not suffixes
        or len(suffixes) > 16
        or any(not isinstance(item, str) or not item.startswith(".") or "/" in item for item in suffixes)
    ):
        raise ValueError("invalid target startup roots or suffixes")
    alias = check_stdlib_alias(spec)
    site = _absolute(spec["site_packages"])
    expected = site / "code_forge_launcher.py"
    observed = []
    found_source = False
    for name in roots:
        root = Path(name)
        if alias is not None and name in spec["stdlib_roots"] and root.is_relative_to(alias["path"]):
            root = Path(alias["physical"]) / root.relative_to(alias["path"])
        root = _absolute(root)
        if not root.exists():
            if root.suffix != ".zip":
                raise ValueError("missing non-ZIP startup root")
            observed.append({"path": name, "physical_path": str(root), "kind": "missing_zip"})
            continue
        info = root.lstat()
        if stat.S_ISDIR(info.st_mode):
            row = {
                "path": name,
                "physical_path": str(root),
                "kind": "directory",
                "dev": info.st_dev,
                "ino": info.st_ino,
                "candidates": {},
                "pth": {},
            }
            entries = list(root.iterdir())
            if len(entries) > 20000:
                raise ValueError("startup root entry bound exceeded")
            for candidate in entries:
                module = _startup_candidate(candidate.name, suffixes)
                if module is not None:
                    if candidate != expected or module != "code_forge_launcher":
                        raise ValueError("competing launcher or startup customization")
                    raw = _read_regular(candidate, MAX_FILE_BYTES)
                    digest = hashlib.sha256(raw).hexdigest()
                    if digest != spec["launcher_source_sha256"]:
                        raise ValueError("startup launcher source changed")
                    row["candidates"][candidate.name] = digest
                    found_source = True
                if candidate.name.endswith(".pth") and root == site:
                    raw = _read_regular(candidate, 65536)
                    if candidate.name != "distutils-precedence.pth" or raw != DISTUTILS_PTH:
                        raise ValueError("unrecognized site path hook")
                    row["pth"][candidate.name] = hashlib.sha256(raw).hexdigest()
            cache = root / "__pycache__"
            if cache.exists() or cache.is_symlink():
                _absolute(cache)
                if not cache.is_dir():
                    raise ValueError("unsupported startup cache directory")
                entries = list(cache.iterdir())
                if len(entries) > 20000:
                    raise ValueError("startup cache entry bound exceeded")
                if any(item.name.startswith(("sitecustomize.", "usercustomize.")) for item in entries):
                    raise ValueError("startup customization cache rejected")
            observed.append(row)
        elif stat.S_ISREG(info.st_mode) and root.suffix == ".zip":
            raw = _read_regular(root, 64 * 1024 * 1024)
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                names = archive.namelist()
                if len(names) > 20000 or len(names) != len(set(names)):
                    raise ValueError("unsupported startup ZIP inventory")
                for member in names:
                    head = member.split("/", 1)[0]
                    if _startup_candidate(head, suffixes) is not None:
                        raise ValueError("startup candidate in standard-library ZIP")
            observed.append(
                {
                    "path": name,
                    "physical_path": str(root),
                    "kind": "zip",
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            )
        else:
            raise ValueError("unsupported target startup root")
    check_stdlib_alias(spec)
    if not found_source:
        raise ValueError("verified launcher source missing from startup roots")
    return observed


def cache_snapshot(path):
    """A fresh cache namespace stays empty because writes are disabled."""
    path = _absolute(path)
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o700
        or info.st_uid != os.geteuid()
        or any(path.iterdir())
    ):
        raise ValueError("cache prefix must be owned, private and empty")
    return {
        "prefix": str(path),
        "dev": info.st_dev,
        "ino": info.st_ino,
        "mode": stat.S_IMODE(info.st_mode),
    }


def check_cache_snapshot(expected):
    if not isinstance(expected, dict) or cache_snapshot(expected["prefix"]) != expected:
        raise ValueError("private cache prefix identity changed")
    return {"status": "healthy", "policy": "private_empty_bytecode_cache"}


def _verify_binding(binding):
    if not isinstance(binding, dict) or binding.get("schema") != 1:
        raise ValueError("invalid binding")
    for name in ("launcher", "target_python", "ip", "git"):
        path = Path(binding[name])
        if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
            raise ValueError("missing absolute executable: " + name)
    if not isinstance(binding.get("installed_provenance"), dict):
        raise TypeError("missing installed provenance")
    hashes = binding["capture_hashes"]
    sources = {}
    for name in CAPTURE_FILES:
        raw = _read_regular(Path(__file__).with_name(name), 1024 * 1024)
        actual = hashlib.sha256(raw).hexdigest()
        if hashes.get(name) != actual:
            raise ValueError("capture hash mismatch: " + name)
        sources[name] = raw
    host = binding["host_netns"]
    if not isinstance(host, str) or not host.startswith("net:[") or not host.endswith("]"):
        raise ValueError("invalid host network namespace")
    if admit_startup(binding["startup_admission"]) != binding["startup_admission"]["observed"]:
        raise ValueError("target startup admission changed")
    _VERIFIED_SOURCES.clear()
    _VERIFIED_SOURCES.update(sources)


def _limits():
    # Per-process/per-file limits, NOT an aggregate disk or descendant quota.
    for key, bound in (
        (resource.RLIMIT_AS, 1024**3),
        (resource.RLIMIT_CPU, 25),
        (resource.RLIMIT_FSIZE, MAX_FILE_BYTES),
        (resource.RLIMIT_CORE, 0),
    ):
        _, hard = resource.getrlimit(key)
        ceiling = bound if hard == resource.RLIM_INFINITY else min(bound, hard)
        resource.setrlimit(key, (ceiling, ceiling))


def _environment(output, binding):
    env = {
        "PATH": str(Path(binding["target_python"]).parent) + ":" + os.defpath,
        "LC_ALL": "C.UTF-8",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "SETUPTOOLS_USE_DISTUTILS": "stdlib",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "FIXTURE_API_KEY": SYNTHETIC_KEY,
    }
    for key, leaf in (
        ("HOME", "home"),
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_STATE_HOME", "state"),
        ("XDG_CACHE_HOME", "cache"),
        ("XDG_DATA_HOME", "data"),
        ("XDG_RUNTIME_DIR", "runtime"),
        ("TMPDIR", "tmp"),
        ("PYTHONPYCACHEPREFIX", "pycache"),
    ):
        directory = output / leaf
        directory.mkdir(mode=0o700)
        env[key] = str(directory)
    return env


def _run_setup(argv, *, cwd, env):
    # Setup output is discarded to avoid an unbounded pipe capture.
    result = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=3,
    )
    if result.returncode:
        raise RuntimeError("fixture setup failed")


def _isolate(binding, env, output):
    # Check BEFORE invoking ip: never change the caller's network namespace.
    current = os.readlink("/proc/self/ns/net")
    if current == binding["host_netns"]:
        raise RuntimeError("private network namespace unavailable")
    if set(socket.if_nameindex()) != {(1, "lo")}:
        raise RuntimeError("non-loopback interface present")
    _run_setup([binding["ip"], "link", "set", "lo", "up"], cwd=output, env=env)
    if os.readlink("/proc/self/ns/net") != current or set(socket.if_nameindex()) != {(1, "lo")}:
        raise RuntimeError("uncertain loopback network namespace")


def _diagnostic_capability(binding, env, output):
    """One owned sys-only capability child before the first normal product import."""
    tracer = binding["utility_identities"]["strace"]
    selected = shutil.which("strace", path=os.defpath)
    if selected is None or str(Path(selected).resolve()) != tracer["path"]:
        raise ValueError("collector tracer selection changed")
    if (
        hashlib.sha256(_read_regular(Path(tracer["path"]), MAX_FILE_BYTES)).hexdigest()
        != tracer["sha256"]
    ):
        raise ValueError("collector tracer bytes changed")
    collector = _module("diagnostic_run")
    directory = output / "capability"
    directory.mkdir(mode=0o700)
    child = None
    observed = {
        "schema": 1,
        "returncode": None,
        "terminal_observed": False,
        "primary_exception": None,
        "cleanup_errors": [],
    }
    with ExitStack() as stack:
        descriptors = []
        for leaf in ("stdout", "stderr", "syscalls.log"):
            fd = os.open(directory / leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            stack.callback(os.close, fd)
            descriptors.append(fd)
        command = [
            tracer["path"],
            "-D",
            "-f",
            "-ttt",
            "-T",
            "-yy",
            "-s",
            "4096",
            "-e",
            "trace=" + collector.SYSCALLS,
            "-o",
            str(directory / "syscalls.log"),
            "--",
            binding["target_python"],
            "-I",
            "-S",
            "-B",
            "-c",
            "raise SystemExit(7)",
        ]
        primary = None
        try:
            child = subprocess.Popen(
                command,
                cwd=output,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=descriptors[0],
                stderr=descriptors[1],
            )
            observed["pid"] = child.pid
            observed["returncode"] = child.wait(timeout=5)
            observed["terminal_observed"] = collector._terminal_observed(
                directory / "syscalls.log", child.pid
            )
            if (
                observed["returncode"] != 7
                or not observed["terminal_observed"]
                or (directory / "stderr").stat().st_size
            ):
                raise RuntimeError("diagnostic capability unavailable; real CLI not launched")
        except BaseException as error:  # noqa: BLE001 - retain capability denial without retrying trace
            primary = error
            observed["primary_exception"] = _error(error)
        finally:
            if child is not None and child.poll() is None:
                try:
                    child.kill()
                    observed["cleanup_returncode"] = child.wait(timeout=2)
                except BaseException as error:  # noqa: BLE001 - exact child cleanup is secondary
                    observed["cleanup_errors"].append(_error(error))
                    primary = primary or error
            try:
                _json(directory / "outcome.json", observed)
            except BaseException as error:  # noqa: BLE001 - metadata must not erase a denied capability
                primary = primary or error
        if primary is not None:
            raise primary


class _RequestReader:
    """Bound header/body input with an absolute deadline, not idle resets."""

    def __init__(self, connection):
        self.connection = connection
        self.deadline = time.monotonic() + 3
        self.header_bytes = 0
        self.body_bytes = 0

    def _receive(self, count):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("fixture request deadline exceeded")
        self.connection.settimeout(min(2, remaining))
        data = self.connection.recv(count)
        if time.monotonic() > self.deadline:
            raise TimeoutError("fixture request deadline exceeded")
        return data

    def readline(self, size=-1):
        line = bytearray()
        while size < 0 or len(line) < size:
            if self.header_bytes >= 32768:
                raise ValueError("fixture header bound exceeded")
            data = self._receive(1)
            if not data:
                break
            line.extend(data)
            self.header_bytes += 1
            if data == b"\n":
                break
        return bytes(line)

    def read(self, size):
        if size < 0 or self.body_bytes + size > 1024 * 1024:
            raise ValueError("fixture body bound exceeded")
        chunks = bytearray()
        while len(chunks) < size:
            data = self._receive(min(65536, size - len(chunks)))
            if not data:
                break
            chunks.extend(data)
            self.body_bytes += len(data)
        return bytes(chunks)

    def close(self):
        # BaseHTTPRequestHandler owns and closes the underlying connection.
        pass


class _Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.rfile.close()
        self.rfile = _RequestReader(self.connection)

    def log_message(self, *_args):
        pass

    def do_POST(self):
        peer = self.server
        try:
            if peer.attempts > 2:
                raise ValueError("fixture request count exceeded")
            self.connection.settimeout(2)
            if (
                self.path != "/v1/chat/completions"
                or self.headers.get("Authorization") != "Bearer " + SYNTHETIC_KEY
            ):
                raise ValueError("unexpected fixture request")
            if (
                self.headers.get("Transfer-Encoding")
                or len(self.headers.get_all("Content-Length", [])) != 1
            ):
                raise ValueError("unsupported fixture framing")
            size = int(self.headers["Content-Length"])
            if not 0 < size <= 1024 * 1024 or len(peer.exchanges) >= 2:
                raise ValueError("fixture request bound exceeded")
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise ValueError("short fixture request")
            body = json.loads(raw)
            if body.get("stream") is not False:
                raise ValueError("ordinary JSON response required")
            index = len(peer.exchanges)
            content = {"mutations": []} if index == 0 else {"findings": []}
            response = json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(content),
                                "reasoning_content": "PRIVATE_SYNTHETIC_WIRE_SENTINEL",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 3},
                }
            ).encode()
            _create(peer.output / ("request-%d.json" % index), raw)
            _create(peer.output / ("response-%d.json" % index), response)
            peer.exchanges.append(index)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
            self.wfile.flush()
        except Exception as error:  # noqa: BLE001 - preserve private fixture errors
            if len(peer.errors) < 8:
                peer.errors.append(_error(error))
            peer.stop.set()
        finally:
            self.close_connection = True


class _Peer(HTTPServer):
    allow_reuse_address = False

    def __init__(self, output):
        self.output, self.exchanges, self.errors = output, [], []
        self.attempts = 0
        self.stop = threading.Event()
        super().__init__(("127.0.0.1", 0), _Handler)
        self.timeout = 0.1
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def get_request(self):
        connection, address = super().get_request()
        self.attempts += 1
        if self.attempts > 2:
            connection.close()
            self.stop.set()
            raise ValueError("fixture connection count exceeded")
        connection.settimeout(2)
        return connection, address

    def handle_error(self, request, client_address):
        if len(self.errors) < 8:
            self.errors.append({"type": "RequestHandlingError", "message": "bounded request rejected"})
        self.stop.set()

    def _serve(self):
        try:
            while not self.stop.is_set():
                self.handle_request()
        except Exception as error:  # noqa: BLE001 - report fixture transport failure
            self.errors.append(_error(error))

    def finish(self):
        self.stop.set()
        self.thread.join(3)
        self.server_close()
        if self.thread.is_alive():
            raise RuntimeError("fixture server did not stop")


def _fixture(output, binding, env, peer):
    project = output / "fixture"
    project.mkdir(mode=0o700)
    git = binding["git"]

    def git_run(*args):
        _run_setup([git, *args], cwd=project, env=env)

    git_run("init", "--quiet", "--template=", "-b", "fixture")
    _create(project / "sample.py", b"x = 1\n")
    git_run("add", "sample.py")
    git_run(
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "synthetic baseline",
    )
    # Replace our own synthetic file, never an existing user file.
    (project / "sample.py").unlink()
    _create(project / "sample.py", b"x = 2\n")
    config = Path(env["XDG_CONFIG_HOME"]) / "code-forge"
    config.mkdir(mode=0o700)
    _json(
        config / "config.yaml",
        {
            "backends": {
                "fixture": {
                    "type": "api",
                    "format": "openai",
                    "model": "fixture",
                    "base_url": "http://127.0.0.1:%d/v1" % peer.server_port,
                    "api_key_env": "FIXTURE_API_KEY",
                    "stream": False,
                }
            },
            "retry": {"max_attempts": 1},
        },
    )
    argv = [
        binding["launcher"],
        "review",
        "--outlet",
        "inline",
        "--canary",
        "--audit-strict",
        "--backend",
        "fixture",
        "--allow-main",
        "--quiet",
    ]
    return argv, project


def _collector_health(output, child_pid=None, child_returncode=None):
    """Check collector delivery framing only; no ACK or native verdict."""
    errors = []
    try:
        execution_raw = _read_private(output / "execution.json", 65536)
        if not execution_raw.endswith(b"\n") or b"\r" in execution_raw:
            raise ValueError("truncated execution framing")
        execution = _decode_json(execution_raw)
        if (
            execution.get("schema") != 1
            or execution.get("mode") != "instrumented"
            or execution.get("case_id") != "normal"
        ):
            raise ValueError("invalid collector metadata identity")
        if execution.get("pid") != child_pid or execution.get("returncode") != child_returncode:
            raise ValueError("collector execution does not match observed child")
        if (
            type(execution.get("pid")) is not int
            or execution["pid"] <= 0
            or type(execution.get("returncode")) is not int
        ):
            raise ValueError("invalid collector execution identity")
        if execution.get("collector_terminal_observed") is not True:
            errors.append("missing_collector_terminal")
        raw = _read_private(output / "events.jsonl")
        if not raw or not raw.endswith(b"\n"):
            raise ValueError("missing or truncated events")
        if b"\r" in raw:
            raise ValueError("invalid physical event framing")
        rows = raw[:-1].split(b"\n")
        if len(rows) > 200_000:
            raise ValueError("event count exceeded")
        first = last = None
        for index, line in enumerate(rows, 1):
            if len(line) > 4096:
                raise ValueError("event line exceeded")
            row = _decode_json(line)
            if (
                type(row.get("schema")) is not int
                or row["schema"] != 1
                or type(row.get("seq")) is not int
                or row["seq"] != index
                or type(row.get("pid")) is not int
                or row["pid"] != execution["pid"]
            ):
                raise ValueError("invalid event identity or sequence")
            if index > 1 and row.get("event") == "diagnostic_start":
                errors.append("duplicate_probe_start")
            if index < len(rows) and row.get("event") == "diagnostic_footer":
                errors.append("early_probe_footer")
            if row.get("event") in ("diagnostic_error", "diagnostic_dropped"):
                errors.append("probe_reported_failure")
            if first is None:
                first = row
            last = row
        if (
            first.get("event") != "diagnostic_start"
            or last.get("event") != "diagnostic_footer"
            or last.get("complete") is not True
        ):
            errors.append("incomplete_probe")
        if any(
            type(last.get(key)) is not int or last[key] != 0
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
        ):
            errors.append("inconsistent_probe_footer")
        trace = _read_private(output / "syscalls.log")
        if not trace or not trace.endswith(b"\n"):
            errors.append("missing_or_truncated_syscalls")
    except Exception as error:  # noqa: BLE001 - missing evidence is never success
        errors.append(type(error).__name__ + ": " + str(error)[:512])
    return {
        "status": "failed" if errors else "healthy",
        "errors": errors,
        "scope": "delivery_framing_only",
        "stream_correlation": "NOT_ASSESSED",
        "ack_consistency": "NOT_ASSESSED",
    }


def execute(binding, mode, output):
    """Create private evidence and return a transport-independent outcome."""
    outcome = {
        "schema": 1,
        "mode": mode,
        "child_pid": None,
        "cache_policy": "private_empty_bytecode_cache",
        "cache_before_launch": None,
        "cache_health": {"status": "not_observed"},
        "child_returncode": None,
        "child_returncode_before_cleanup": None,
        "primary_exception": None,
        "cleanup_errors": [],
        "cleanup_induced_termination": False,
        "collector_health": {
            "status": "not_applicable" if mode == "baseline" else "failed",
            "errors": [],
        },
        "collector_health_stage": "preliminary_before_owner_cleanup",
        "peer_transport": {"requests": 0, "exchanges": 0, "errors": []},
        "transport_status": "receipt_written",
    }
    child = peer = collector = None
    stack = ExitStack()
    try:
        if mode not in ("baseline", "diagnostic"):
            raise ValueError("unknown mode")
        _verify_binding(binding)
        _limits()
        env = _environment(output, binding)
        _isolate(binding, env, output)
        if mode == "diagnostic":
            _diagnostic_capability(binding, env, output)
        peer = _Peer(output)
        argv, project = _fixture(output, binding, env, peer)
        for name in ("stdout", "stderr"):
            fd = os.open(
                output / (name + ".log"), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            stack.callback(os.close, fd)
            if name == "stdout":
                stdout = fd
            else:
                stderr = fd
        peer.thread.start()
        if env.get("PYTHONPYCACHEPREFIX") != str(output / "pycache"):
            raise ValueError("cache prefix escaped the new private worker output")
        outcome["cache_before_launch"] = cache_snapshot(env["PYTHONPYCACHEPREFIX"])
        if env.get("PYTHONDONTWRITEBYTECODE") != "1" or env.get("SETUPTOOLS_USE_DISTUTILS") != "stdlib":
            raise ValueError("controlled startup environment changed")
        check_stdlib_alias(binding.get("startup_admission", {}))
        if mode == "diagnostic":
            collector = _module("diagnostic_run").traced_child(
                argv,
                case_id="normal",
                cwd=project,
                env=env,
                output=output / "diagnostic",
                stdout=stdout,
                stderr=stderr,
            )
            child = collector.__enter__()
        else:
            child = subprocess.Popen(
                argv, cwd=project, env=env, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr
            )
        # Capture body failure here so collector/server cleanup cannot replace it.
        outcome["child_pid"] = child.pid
        outcome["child_returncode"] = child.wait(timeout=CHILD_TIMEOUT)
        outcome["child_returncode_before_cleanup"] = outcome["child_returncode"]
    except BaseException as error:  # noqa: BLE001 - persist primary even during cleanup failure
        outcome["primary_exception"] = _error(error)
    finally:
        if child is not None:
            try:
                status = outcome["child_returncode_before_cleanup"]
                if status is None:
                    status = child.poll()
                    outcome["child_returncode_before_cleanup"] = status
                if status is None:
                    outcome["cleanup_induced_termination"] = True
                    child.kill()
                    outcome["child_returncode"] = child.wait(timeout=2)
                elif outcome["child_returncode"] is None:
                    outcome["child_returncode"] = status
            except BaseException as error:  # noqa: BLE001 - record cleanup without masking the primary
                outcome["cleanup_errors"].append(_error(error))
        if collector is not None:
            try:
                collector.__exit__(None, None, None)
            except BaseException as error:  # noqa: BLE001 - record cleanup without masking the primary
                outcome["cleanup_errors"].append(_error(error))
        if peer is not None:
            try:
                if peer.thread.ident is not None:
                    peer.finish()
                else:
                    peer.server_close()
                if peer.errors or len(peer.exchanges) != 2:
                    raise RuntimeError("fixture did not complete two ordinary exchanges")
            except BaseException as error:  # noqa: BLE001 - record cleanup without masking the primary
                outcome["cleanup_errors"].append(_error(error))
            outcome["peer_transport"] = {
                "requests": peer.attempts,
                "exchanges": len(peer.exchanges),
                "errors": list(peer.errors),
            }
        try:
            stack.close()
        except BaseException as error:  # noqa: BLE001 - record cleanup without masking the primary
            outcome["cleanup_errors"].append(_error(error))
        if outcome["cache_before_launch"] is not None:
            try:
                outcome["cache_health"] = check_cache_snapshot(outcome["cache_before_launch"])
            except (OSError, ValueError, KeyError) as error:
                outcome["cleanup_errors"].append(_error(error))
                outcome["cache_health"] = {"status": "failed", "policy": "private_empty_bytecode_cache"}
        if mode == "diagnostic":
            try:
                outcome["collector_health"] = _collector_health(
                    output / "diagnostic", outcome["child_pid"], outcome["child_returncode"]
                )
            except BaseException as error:  # noqa: BLE001 - metadata cannot erase the observed status
                outcome["cleanup_errors"].append(_error(error))
                outcome["collector_health"] = {"status": "failed", "errors": ["health_check_failed"]}
    return outcome


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binding", required=True)
    parser.add_argument("--mode", choices=("baseline", "diagnostic"), required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        output = _absolute(args.output)
        parent = output.parent.stat()
        if parent.st_mode & 0o077 or parent.st_uid != os.geteuid() or not stat.S_ISDIR(parent.st_mode):
            raise ValueError("output parent must be private")
        output.mkdir(mode=0o700, exist_ok=False)
        try:
            binding = _decode_json(_read_private(args.binding, 1024 * 1024))
            outcome = execute(binding, args.mode, output)
        except BaseException as error:  # noqa: BLE001 - record cleanup without masking the primary
            outcome = {
                "schema": 1,
                "mode": args.mode,
                "child_pid": None,
                "child_returncode": None,
                "child_returncode_before_cleanup": None,
                "primary_exception": _error(error),
                "cleanup_errors": [],
                "cleanup_induced_termination": False,
                "collector_health": {
                    "status": "not_applicable" if args.mode == "baseline" else "failed",
                    "errors": [],
                },
                "collector_health_stage": "preliminary_before_owner_cleanup",
                "peer_transport": {"requests": 0, "exchanges": 0, "errors": []},
                "transport_status": "receipt_written",
            }
        temporary = output / "outcome.pending"
        _json(temporary, outcome)
        # Atomic no-overwrite publication in the exclusively created private tree.
        os.link(temporary, output / "outcome.json", follow_symlinks=False)
        # Retain the temporary hardlink: post-publication cleanup cannot turn a
        # delivered receipt into an ambiguous transport failure.
        return 0
    except BaseException:  # noqa: BLE001 - no receipt is a transport failure
        return 73


if __name__ == "__main__":
    raise SystemExit(main())
