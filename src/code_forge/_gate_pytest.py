# SPDX-License-Identifier: Apache-2.0
"""Private, bounded pytest evidence for the commit gate.

Only a closed direct pytest invocation can authorize a known-failure waiver.
The receipt binds execution; it does not authenticate arbitrary tested code.
This module also loads by physical filename without importing code_forge.
"""

from __future__ import annotations

import hashlib
import errno
import json
import os
import re
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import tempfile
import time
from types import MethodType

PLUGIN = "forge-gate-pytest"
QUALIFIED_RUNTIMES = frozenset(
    {("cpython-39", "8.4.2"), ("cpython-314", "9.1.0"), ("cpython-312", "9.1.1")}
)
QUALIFIED_VERSIONS = frozenset(version for _, version in QUALIFIED_RUNTIMES)
MAX_BYTES = 8 * 1024 * 1024
MAX_TRANSPORT_BYTES = 4096
MAX_ITEMS = 50_000
MAX_NODE_BYTES = 64 * 1024
MAX_CACHE_ENTRIES = 16
ERROR_KINDS = ("collection", "setup", "teardown", "internal")
OUTCOMES = frozenset({"passed", "failed", "skipped"})
RECORD_FIELDS = frozenset(
    {
        "schema",
        "plugin",
        "pytest_version",
        "binding",
        "pid",
        "sessions",
        "collected",
        "rows",
        "errors",
        "collection_count",
        "executed_count",
        "failed_count",
        "exitstatus",
        "cmdline_return",
        "normal_return",
        "unconfigured",
        "complete",
        "invalid",
    }
)


def _node_size(node):
    try:
        return len(node.encode("utf-8"))
    except UnicodeEncodeError:
        return None


class EvidenceResult:
    def __init__(self, valid=False, reason="", failed_nodes=None):
        self.valid = valid
        self.reason = reason
        self.failed_nodes = failed_nodes or []


def _stamp(info):
    return (
        info.st_dev,
        info.st_ino,
        info.st_uid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _safe_read(path, *, dir_fd=None, limit=MAX_BYTES, deadline=None, reporter_source=False):
    """Open without following links or blocking on a substituted FIFO."""
    if deadline is not None and time.monotonic() > deadline:
        raise ValueError("evidence deadline exceeded")
    if not all(hasattr(os, name) for name in ("O_NOFOLLOW", "O_NONBLOCK", "O_DIRECTORY")):
        raise ValueError("safe evidence reads unavailable")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open(path, flags, dir_fd=dir_fd)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or (
            not reporter_source and (before.st_uid != os.getuid() or before.st_nlink != 1)
        ):
            raise ValueError("evidence is not an owned regular file")
        if before.st_size > limit:
            raise ValueError("evidence byte limit exceeded")
        chunks = []
        remaining = limit + 1
        while remaining:
            if deadline is not None and time.monotonic() > deadline:
                raise ValueError("evidence deadline exceeded")
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(fd)
        current = os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
        if _stamp(before) != _stamp(after) or _stamp(after) != _stamp(current):
            raise ValueError("evidence changed during read")
        if len(data) > limit or len(data) != after.st_size:
            raise ValueError("evidence byte limit or size mismatch")
        return data, _stamp(after)
    finally:
        os.close(fd)


def _digest(path, *, reporter_source=False):
    data, identity = _safe_read(path, reporter_source=reporter_source)
    return hashlib.sha256(data).hexdigest(), identity


def _entries(fd):
    os.lseek(fd, 0, os.SEEK_SET)
    return os.listdir(fd)


def _bootstrap_cache_name(module, name):
    """Recognize bounded compiler names only in this bootstrap's namespace."""
    tag = r"[A-Za-z0-9_][A-Za-z0-9_-]{0,63}"
    optimization = r"[A-Za-z0-9]{1,16}"
    version = r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}"
    return (
        re.fullmatch(
            re.escape(module)
            + rf"\.(?:{tag}(?:\.opt-{optimization})?\.pyc|{tag}-pytest-{version}\.py[co])",
            name,
        )
        is not None
    )


class Capture:
    def __init__(self, directory, fd, binding, command, module, prefix):
        self.directory = directory
        self.fd = fd
        self.identity = _stamp(os.fstat(fd))[:2]
        self.binding = binding
        self.command = command
        self.module = module
        self.prefix = prefix
        self.source_root = None
        self.cleanup_error = ""
        self.transport = None
        self.authority = True

    def intact(self):
        held = os.fstat(self.fd)
        current = os.stat(self.directory, follow_symlinks=False)
        return (
            stat.S_ISDIR(current.st_mode)
            and current.st_uid == os.getuid()
            and _stamp(current)[:2] == self.identity == _stamp(held)[:2]
        )

    def close(self):
        """Remove only the bounded manifest, refusing unknown entries."""
        if self.fd is None:
            return not self.cleanup_error
        disposed = False
        try:
            if not self.intact():
                raise ValueError("capture directory changed")
            names = {self.module + ".py", "binding.json", "begin.json", "final.json", "violation"}
            entries = set(_entries(self.fd))
            if "__pycache__" in entries:
                cache_fd = os.open(
                    "__pycache__", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.fd
                )
                try:
                    cache_info = os.fstat(cache_fd)
                    if cache_info.st_uid != os.getuid():
                        raise ValueError("foreign bootstrap cache")
                    cache_entries = set(_entries(cache_fd))
                    if len(cache_entries) > MAX_CACHE_ENTRIES or any(
                        not _bootstrap_cache_name(self.module, name) for name in cache_entries
                    ):
                        raise ValueError("unknown bootstrap cache entries")
                    for cache_name in cache_entries:
                        _safe_read(cache_name, dir_fd=cache_fd)
                        os.unlink(cache_name, dir_fd=cache_fd)
                finally:
                    os.close(cache_fd)
                os.rmdir("__pycache__", dir_fd=self.fd)
                entries.remove("__pycache__")
            if entries - names:
                raise ValueError("unknown capture entries")
            for name in entries:
                _safe_read(name, dir_fd=self.fd)
                os.unlink(name, dir_fd=self.fd)
            if not self.intact():
                raise ValueError("capture directory changed")
            os.rmdir(self.directory)
            disposed = True
        except (OSError, ValueError) as exc:
            self.cleanup_error = str(exc)
        finally:
            try:
                os.close(self.fd)
            except OSError as exc:
                self.cleanup_error = "capture descriptor close failed: " + str(exc)
                disposed = False
            self.fd = None
        if self.cleanup_error:
            self.authority = False
        return disposed


def prepare_pytest_capture(
    command: list[str], *, test_cwd: Path, test_env: dict[str, str], reporter_path: Path
) -> Capture | None:
    """Prepare only exact supported direct Python pytest prefixes."""
    prefix = 0
    if command and command[0] in ("python", "python3"):
        if command[1:3] == ["-m", "pytest"]:
            prefix = 3
        elif command[1:4] == ["-B", "-m", "pytest"]:
            prefix = 4
    if not prefix or test_env.get("PYTHONPYCACHEPREFIX"):
        return None
    directory = None
    capture = None
    fd = None
    try:
        reporter_path = reporter_path.resolve(strict=True)
        reporter_source, reporter_identity = _safe_read(reporter_path, reporter_source=True)
        reporter_hash = hashlib.sha256(reporter_source).hexdigest()
        module = "_forge_gate_" + secrets.token_hex(16)
        directory = Path(tempfile.mkdtemp(prefix="forge-gate-"))
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        capture = Capture(directory, fd, {}, [], module, prefix)
        bootstrap = directory / (module + ".py")
        source = (
            "import importlib.util, json, os, sys\n"
            f"_spec = importlib.util.spec_from_file_location({module + '_reporter'!r}, {str(reporter_path)!r})\n"
            "_reporter = importlib.util.module_from_spec(_spec)\n"
            "sys.modules[_spec.name] = _reporter\n"
            "try:\n"
            f"    exec(compile({reporter_source!r}, _spec.origin, 'exec'), _reporter.__dict__)\n"
            "except OSError:\n"
            "    _hooks = None\n"
            "else:\n"
            "    _binding = _reporter.load_binding_transport(os.environ.get('FORGE_GATE_BINDING'), __file__)\n"
            "    _hooks = _reporter.load_plugin(_binding, __file__) if _binding is not None else None\n"
            "def pytest_addhooks(pluginmanager):\n"
            "    if _hooks is not None:\n"
            "        _hooks.guard_dispatch(pluginmanager)\n"
            "        pluginmanager.register(_hooks, name=os.environ['FORGE_GATE_BOOTSTRAP'] + '_hooks')\n"
        )
        if len(source.encode("utf-8")) > MAX_BYTES:
            raise ValueError("bootstrap byte limit exceeded")
        bootstrap.write_text(source, encoding="utf-8")
        bootstrap_hash, bootstrap_identity = _digest(bootstrap)
        effective = command[:prefix] + ["-p", module] + command[prefix:]
        binding = {
            "nonce": secrets.token_hex(32),
            "configured_argv": list(command),
            "effective_argv": effective,
            "cwd": str(test_cwd.resolve()),
            "parent_pid": os.getpid(),
            "reporter_path": str(reporter_path),
            "reporter_hash": reporter_hash,
            "reporter_identity": list(reporter_identity),
            "bootstrap_path": str(bootstrap),
            "bootstrap_hash": bootstrap_hash,
            "bootstrap_identity": list(bootstrap_identity),
            "directory": str(directory),
            "module": module,
        }
        capture.binding = binding
        capture.command = effective
        data = json.dumps(binding, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        if len(data) > MAX_BYTES:
            raise ValueError("binding byte limit exceeded")
        binding_path = directory / "binding.json"
        with binding_path.open("xb") as stream:
            stream.write(data)
        digest, identity = _digest(binding_path)
        capture.transport = {
            "schema": 1,
            "path": str(binding_path),
            "sha256": digest,
            "identity": list(identity),
        }
        if len(json.dumps(capture.transport).encode("utf-8")) > MAX_TRANSPORT_BYTES:
            raise ValueError("binding transport byte limit exceeded")
        return capture
    except (OSError, ValueError):
        if capture is not None:
            capture.close()
        elif directory is not None:
            if fd is not None:
                os.close(fd)
            directory.rmdir()
        return None


class CapturedResult:
    def __init__(self, process, stdout, stderr):
        self.pid = process.pid
        self.returncode = process.returncode
        self.stdout = stdout
        self.stderr = stderr
        self.reaped = process.returncode is not None
        self.pipes_closed = all(pipe is None or pipe.closed for pipe in (process.stdout, process.stderr))


def run_captured_pytest(
    command: list[str],
    *,
    capture: Capture,
    test_cwd: Path,
    test_env: dict[str, str],
    timeout_seconds: int,
) -> CapturedResult:
    child_env = dict(test_env)
    child_env["FORGE_GATE_BINDING"] = json.dumps(capture.transport)
    child_env["FORGE_GATE_BOOTSTRAP"] = capture.module
    inherited = child_env.get("PYTHONPATH", "")
    parts = [str(capture.directory)]
    if capture.source_root and inherited.split(os.pathsep)[0] == capture.source_root:
        parts.insert(0, capture.source_root)
        inherited = os.pathsep.join(inherited.split(os.pathsep)[1:])
    if inherited:
        parts.append(inherited)
    child_env["PYTHONPATH"] = os.pathsep.join(parts)
    if (
        command != capture.binding["configured_argv"]
        or str(test_cwd.resolve()) != capture.binding["cwd"]
    ):
        raise ValueError("capture invocation changed")
    options = dict(
        stdin=None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(test_cwd),
    )
    try:
        process = subprocess.Popen(capture.command, env=child_env, **options)
    except OSError as exc:
        if exc.errno != errno.E2BIG:
            raise
        # Failed exec started no child. Run original once, without receipt authority.
        capture.authority = False
        process = subprocess.Popen(command, env=test_env, **options)
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except BaseException:
        # Signal only the direct child; inherited grandchild pipes can outlive it.
        if process.poll() is None:
            process.kill()
        for pipe in (process.stdout, process.stderr):
            if pipe is not None:
                pipe.close()
        process.wait(timeout=5)
        raise
    finally:
        for pipe in (process.stdout, process.stderr):
            if pipe is not None:
                pipe.close()
    return CapturedResult(process, stdout, stderr)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _integer(value):
    if len(value.lstrip("-")) > 20:
        raise ValueError("oversized JSON integer")
    return int(value)


def _constant(value):
    raise ValueError("nonfinite JSON number: " + value)


def decode_record(data):
    try:
        if len(data) > MAX_BYTES:
            raise ValueError("evidence byte limit exceeded")
        record = json.loads(
            data.decode("utf-8"), object_pairs_hook=_pairs, parse_int=_integer, parse_constant=_constant
        )
        if not isinstance(record, dict):
            raise ValueError("evidence must be an object")  # noqa: TRY004 - decode failures share ValueError
        return record
    except (UnicodeError, RecursionError, OverflowError) as exc:
        raise ValueError("malformed evidence") from exc


def read_binding_transport(transport, bootstrap_path, *, deadline=None):
    expected_path = str(Path(bootstrap_path).parent / "binding.json")
    if (
        not isinstance(transport, dict)
        or set(transport) != {"schema", "path", "sha256", "identity"}
        or type(transport["schema"]) is not int
        or transport["schema"] != 1
        or transport["path"] != expected_path
    ):
        raise ValueError("invalid binding transport")
    data, identity = _safe_read(expected_path, deadline=deadline)
    if (
        hashlib.sha256(data).hexdigest() != transport["sha256"]
        or list(identity) != transport["identity"]
    ):
        raise ValueError("binding transport changed")
    binding = decode_record(data)
    fields = {
        "nonce",
        "configured_argv",
        "effective_argv",
        "cwd",
        "parent_pid",
        "reporter_path",
        "reporter_hash",
        "reporter_identity",
        "bootstrap_path",
        "bootstrap_hash",
        "bootstrap_identity",
        "directory",
        "module",
    }
    if set(binding) != fields or binding["bootstrap_path"] != str(bootstrap_path):
        raise ValueError("invalid binding fields")
    if (
        any(
            not isinstance(binding[name], str) or not binding[name]
            for name in (
                "nonce",
                "cwd",
                "reporter_path",
                "reporter_hash",
                "bootstrap_path",
                "bootstrap_hash",
                "directory",
                "module",
            )
        )
        or type(binding["parent_pid"]) is not int
        or binding["parent_pid"] <= 0
        or any(
            not isinstance(binding[name], list)
            or len(binding[name]) != 7
            or any(type(value) is not int for value in binding[name])
            for name in ("reporter_identity", "bootstrap_identity")
        )
    ):
        raise ValueError("invalid binding values")
    configured = binding["configured_argv"]
    prefix = 4 if configured[1:4] == ["-B", "-m", "pytest"] else 3
    if (
        not isinstance(configured, list)
        or not configured
        or any(not isinstance(arg, str) for arg in configured)
        or configured[0] not in ("python", "python3")
        or configured[prefix - 2 : prefix] != ["-m", "pytest"]
        or binding["module"] != Path(bootstrap_path).stem
        or binding["directory"] != str(Path(expected_path).parent)
        or binding["effective_argv"]
        != configured[:prefix] + ["-p", binding["module"]] + configured[prefix:]
    ):
        raise ValueError("invalid binding arguments")
    return binding


def load_binding_transport(encoded, bootstrap_path):
    """Refuse only bootstrap-owned transport failures without changing pytest."""
    try:
        if not isinstance(encoded, str) or len(encoded.encode("utf-8")) > MAX_TRANSPORT_BYTES:
            return None
        return read_binding_transport(decode_record(encoded.encode("utf-8")), bootstrap_path)
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        return None


def validate_record(record, *, binding, child_pid, returncode):
    """Re-derive all identities and counts from the closed inventory."""
    bad = EvidenceResult(reason="incomplete or unsupported pytest evidence")
    if not isinstance(record, dict) or set(record) != RECORD_FIELDS:
        return bad
    if (
        type(record["schema"]) is not int
        or record["schema"] != 1
        or record["plugin"] != PLUGIN
        or not isinstance(record["pytest_version"], str)
        or record["pytest_version"] not in QUALIFIED_VERSIONS
        or record["binding"] != binding
    ):
        return bad
    for name, expected in (
        ("pid", child_pid),
        ("sessions", 1),
        ("exitstatus", returncode),
        ("cmdline_return", returncode),
    ):
        if type(record[name]) is not int or record[name] != expected:
            return bad
    if any(
        record[name] is not True for name in ("collected", "normal_return", "unconfigured", "complete")
    ):
        return bad
    if record["invalid"] != "":
        return EvidenceResult(reason="pytest evidence refused: " + str(record["invalid"]))
    errors = record["errors"]
    if not isinstance(errors, dict) or set(errors) != set(ERROR_KINDS):
        return bad
    if any(type(value) is not int or value != 0 for value in errors.values()):
        return EvidenceResult(reason="pytest harness errors in evidence")
    rows = record["rows"]
    if not isinstance(rows, list) or len(rows) > MAX_ITEMS:
        return bad
    seen = set()
    failures = []
    executed = 0
    for row in rows:
        if not isinstance(row, list) or len(row) != 4:
            return bad
        node, setup, call, teardown = row
        if not isinstance(node, str) or not node:
            return bad
        size = _node_size(node)
        if size is None or size > MAX_NODE_BYTES or node in seen:
            return bad
        seen.add(node)
        if setup not in ("passed", "skipped") or teardown not in ("passed", "skipped"):
            return bad
        if setup == "passed":
            if not isinstance(call, str) or call not in OUTCOMES:
                return bad
            executed += 1
        elif call is not None:
            return bad
        if call == "failed":
            failures.append(node)
    for field, expected in (
        ("collection_count", len(rows)),
        ("executed_count", executed),
        ("failed_count", len(failures)),
    ):
        if type(record[field]) is not int or record[field] != expected:
            return bad
    if returncode != 1 or not failures:
        return EvidenceResult(reason="pytest evidence has no call failures")
    return EvidenceResult(True, "complete pytest evidence", failures)


def read_pytest_evidence(capture: Capture, *, child_pid: int, returncode: int) -> EvidenceResult:
    deadline = time.monotonic() + 2
    try:
        if not capture.authority:
            raise ValueError("instrumented exec unavailable")
        if not capture.intact() or type(child_pid) is not int or child_pid <= 0:
            raise ValueError("capture identity unavailable")
        if "violation" in _entries(capture.fd):
            raise ValueError("multiple or invalid pytest sessions")
        if (
            read_binding_transport(
                capture.transport, capture.binding["bootstrap_path"], deadline=deadline
            )
            != capture.binding
        ):
            raise ValueError("binding file does not match invocation")
        for kind in ("reporter", "bootstrap"):
            data, identity = _safe_read(
                capture.binding[kind + "_path"], deadline=deadline, reporter_source=kind == "reporter"
            )
            if (
                hashlib.sha256(data).hexdigest() != capture.binding[kind + "_hash"]
                or list(identity) != capture.binding[kind + "_identity"]
            ):
                raise ValueError(kind + " changed during invocation")
        begin = decode_record(_safe_read("begin.json", dir_fd=capture.fd, deadline=deadline)[0])
        if (
            set(begin) != {"binding", "pid"}
            or begin["binding"] != capture.binding
            or type(begin["pid"]) is not int
            or begin["pid"] != child_pid
        ):
            raise ValueError("begin evidence binding mismatch")
        final = decode_record(_safe_read("final.json", dir_fd=capture.fd, deadline=deadline)[0])
        result = validate_record(
            final, binding=capture.binding, child_pid=child_pid, returncode=returncode
        )
        if time.monotonic() > deadline or not capture.intact():
            raise ValueError("evidence deadline or directory identity changed")
        return result
    except (OSError, ValueError, TypeError, RecursionError, OverflowError) as exc:
        return EvidenceResult(reason="pytest evidence unavailable: " + str(exc))


def compare_failed_nodes(failed_nodes: list[str], baseline: dict | None) -> tuple[bool, list[str]]:
    if baseline is None or not failed_nodes:
        return False, []
    results = baseline.get("test_results")
    if not isinstance(results, dict):
        return True, list(failed_nodes)
    new = [node for node in failed_nodes if results.get(node) != "failed"]
    return bool(new), new


class Recorder:
    def __init__(self, binding, bootstrap_path):
        self.binding = binding
        self.directory = Path(binding["directory"])
        self.pid = os.getpid()
        self.invalid = ""
        self.sessions = 0
        self.collected = False
        self.rows = {}
        self.errors = dict.fromkeys(ERROR_KINDS, 0)
        self.identity_bytes = 0
        self.exitstatus = None
        self.cmdline_return = None
        self.normal_return = False
        self.unconfigure_count = 0
        for kind, path in (
            ("reporter", Path(__file__).resolve()),
            ("bootstrap", Path(bootstrap_path).resolve()),
        ):
            try:
                digest, identity = _digest(path, reporter_source=kind == "reporter")
            except (OSError, ValueError):
                self.refuse(kind + " origin unavailable")
                continue
            if (
                str(path) != binding[kind + "_path"]
                or digest != binding[kind + "_hash"]
                or list(identity) != binding[kind + "_identity"]
            ):
                self.refuse(kind + " origin mismatch")
        if os.getppid() != binding["parent_pid"] or str(Path.cwd().resolve()) != binding["cwd"]:
            self.refuse("producer identity mismatch")
        prefix = binding["effective_argv"].index("pytest") + 1
        if sys.argv[1:] != binding["effective_argv"][prefix:]:
            self.refuse("producer arguments changed")

    def refuse(self, reason):
        if not self.invalid:
            self.invalid = reason
        try:
            with (self.directory / "violation").open("xb") as stream:
                stream.write(reason.encode("utf-8"))
        except OSError:
            pass

    def publish(self, name, record):
        data = json.dumps(record, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        if len(data) > MAX_BYTES:
            self.refuse("evidence byte overflow")
            return
        try:
            with (self.directory / name).open("xb") as stream:
                stream.write(data)
        except FileExistsError:
            self.refuse("duplicate evidence publication")
        except OSError:
            self.refuse("evidence publication unavailable")

    def finish(self, version):
        rows = list(self.rows.values())
        record = {
            "schema": 1,
            "plugin": PLUGIN,
            "pytest_version": version,
            "binding": self.binding,
            "pid": self.pid,
            "sessions": self.sessions,
            "collected": self.collected,
            "rows": rows,
            "errors": self.errors,
            "collection_count": len(rows),
            "executed_count": sum(row[2] is not None for row in rows),
            "failed_count": sum(row[2] == "failed" for row in rows),
            "exitstatus": self.exitstatus,
            "cmdline_return": self.cmdline_return,
            "normal_return": self.normal_return,
            "unconfigured": self.unconfigure_count == 2,
            "complete": self.collected and self.normal_return and self.unconfigure_count == 2,
            "invalid": self.invalid,
        }
        self.publish("final.json", record)


def load_plugin(binding, bootstrap_path):
    """Install guards before autoload, environment and conftest plugins."""
    import pytest
    import _pytest.config as pytest_config

    recorder = Recorder(binding, bootstrap_path)
    version = pytest.__version__
    if (
        (sys.implementation.cache_tag, version) not in QUALIFIED_RUNTIMES
        or sys.flags.optimize
        or sys.pycache_prefix is not None
    ):
        recorder.refuse("unsupported pytest runtime " + version)
        return None
    original_config = pytest_config.Config
    trusted_unconfigure = original_config._ensure_unconfigure
    original_prepare = pytest_config._prepareconfig
    from pluggy import HookCaller

    original_manager = pytest_config.PytestPluginManager
    trusted_hookexec = original_manager._hookexec
    trusted_entries = {
        name: getattr(HookCaller, name) for name in ("__call__", "call_historic", "_maybe_apply_history")
    }

    def hook_entries_intact():
        return all(getattr(HookCaller, name) is entry for name, entry in trusted_entries.items())

    trusted_proxy = None
    trusted_proxy_getattr = None
    if version == "8.4.2":
        from _pytest.config.compat import PathAwareHookProxy

        trusted_proxy = PathAwareHookProxy
        trusted_proxy_getattr = PathAwareHookProxy.__getattr__
    dispatchers = {}

    def prepare_guard(*args, **kwargs):
        recorder.refuse("second pytest preparation")
        return original_prepare(*args, **kwargs)

    pytest_config._prepareconfig = prepare_guard

    class Hooks:
        relay = None

        def guard_dispatch(self, pluginmanager):
            if (
                self.relay is not None
                or type(pluginmanager) is not original_manager
                or original_manager._hookexec is not trusted_hookexec
                or not hook_entries_intact()
            ):
                recorder.refuse("unsupported hook dispatch boundary")
                return
            pending = []
            for name in (
                "pytest_configure",
                "pytest_sessionstart",
                "pytest_collection",
                "pytest_sessionfinish",
                "pytest_internalerror",
                "pytest_keyboard_interrupt",
                "pytest_runtestloop",
            ):
                caller = getattr(pluginmanager.hook, name, None)
                original = getattr(caller, "_hookexec", None)
                if (
                    type(caller) is not HookCaller
                    or type(original) is not MethodType
                    or original.__func__ is not trusted_hookexec
                    or original.__self__ is not pluginmanager
                ):
                    recorder.refuse("replaced incoming hook dispatch boundary")
                    return
                pending.append((name, caller, original))
            self.relay = pluginmanager.hook
            for name, caller, original in pending:

                def dispatch_guard(
                    hook_name, methods, kwargs, firstresult, *, _name=name, _original=original
                ):
                    if _name == "pytest_internalerror":
                        recorder.errors["internal"] += 1
                    elif _name == "pytest_keyboard_interrupt":
                        recorder.refuse("interrupted pytest runner")
                    elif _name == "pytest_sessionfinish":
                        status = kwargs.get("exitstatus")
                        recorder.exitstatus = (
                            int(status) if isinstance(status, pytest.ExitCode) else status
                        )
                    try:
                        return _original(hook_name, methods, kwargs, firstresult)
                    except BaseException:
                        recorder.refuse("aborted " + _name + " dispatch")
                        raise

                caller._hookexec = dispatch_guard
                dispatchers[name] = (caller, dispatch_guard)

        @pytest.hookimpl(tryfirst=True)
        def pytest_load_initial_conftests(self, early_config, parser, args):
            imports = []
            index = 0
            while index < len(args):
                arg = args[index]
                index += 1
                if arg == "-p":
                    if index == len(args):
                        break
                    name = args[index].strip()
                    index += 1
                elif arg.startswith("-p"):
                    name = arg[2:].strip()
                else:
                    continue
                if not name.startswith("no:"):
                    imports.append(name)
            if not imports or imports[0] != binding["module"] or imports.count(binding["module"]) != 1:
                recorder.refuse("earlier importing plugin or missing bootstrap")
            runner_args = args[: args.index("--")] if "--" in args else args
            if any(
                arg in ("-n", "--numprocesses", "--dist", "--forked", "--reruns")
                or arg.startswith(("--numprocesses=", "--dist=", "--reruns="))
                or (arg.startswith("-n") and len(arg) > 2)
                for arg in runner_args
            ):
                recorder.refuse("distributed or repeated runner")

        @pytest.hookimpl(wrapper=True, tryfirst=True)
        def pytest_cmdline_main(self, config):
            recorder.sessions += 1
            if recorder.sessions != 1 or os.getpid() != recorder.pid:
                recorder.refuse("duplicate or foreign session")
            recorder.publish("begin.json", {"binding": binding, "pid": os.getpid()})
            original_unconfigure = config._ensure_unconfigure
            trusted_cleanup = (
                type(config) is original_config
                and original_config._ensure_unconfigure is trusted_unconfigure
                and type(original_unconfigure) is MethodType
                and original_unconfigure.__func__ is trusted_unconfigure
                and original_unconfigure.__self__ is config
            )
            if not trusted_cleanup:
                recorder.refuse("replaced incoming cleanup boundary")

            def unconfigure_guard(*args, **kwargs):
                result = original_unconfigure(*args, **kwargs)
                recorder.unconfigure_count += 1
                if recorder.unconfigure_count > 2:
                    recorder.refuse("unexpected cleanup count")
                if recorder.unconfigure_count == 2:
                    if config._ensure_unconfigure is not unconfigure_guard:
                        recorder.refuse("replaced cleanup boundary")
                    elif (
                        not (
                            config.hook is self.relay
                            or (
                                trusted_proxy is not None
                                and type(config.hook) is trusted_proxy
                                and config.hook._hook_relay is self.relay
                                and trusted_proxy.__getattr__ is trusted_proxy_getattr
                            )
                        )
                        or len(dispatchers) != 7
                        or not hook_entries_intact()
                        or any(
                            getattr(self.relay, name) is not caller
                            or getattr(config.hook, name) is not caller
                            or caller._hookexec is not guard
                            for name, (caller, guard) in dispatchers.items()
                        )
                    ):
                        recorder.refuse("replaced hook dispatch boundary")
                    elif not recorder.normal_return or sys.exc_info()[0] is not None:
                        recorder.refuse("escaping cmdline exception")
                    else:
                        recorder.finish(version)
                return result

            if trusted_cleanup:
                config._ensure_unconfigure = unconfigure_guard
            result = yield
            recorder.cmdline_return = int(result) if isinstance(result, pytest.ExitCode) else result
            recorder.normal_return = type(recorder.cmdline_return) is int
            return result

        def pytest_collection_finish(self, session):
            if recorder.collected:
                recorder.refuse("duplicate collection")
            recorder.collected = True
            for item in session.items:
                node = item.nodeid
                if not isinstance(node, str) or not node:
                    recorder.refuse("invalid node identity")
                    continue
                size = _node_size(node)
                if size is None:
                    recorder.refuse("unsupported node encoding")
                    continue
                if size > MAX_NODE_BYTES or len(recorder.rows) >= MAX_ITEMS:
                    recorder.refuse("inventory overflow")
                    continue
                recorder.identity_bytes += size
                if recorder.identity_bytes > MAX_BYTES:
                    recorder.refuse("identity byte overflow")
                    continue
                if node in recorder.rows:
                    recorder.refuse("duplicate selected identity")
                else:
                    recorder.rows[node] = [node, None, None, None]

        def pytest_runtest_logreport(self, report):
            node, phase, outcome = report.nodeid, report.when, report.outcome
            if phase not in ("setup", "call", "teardown") or outcome not in OUTCOMES:
                recorder.refuse("unsupported test report")
                return
            if phase in ("setup", "teardown") and outcome == "failed":
                recorder.errors[phase] += 1
            row = recorder.rows.get(node)
            index = {"setup": 1, "call": 2, "teardown": 3}[phase]
            if row is None or row[index] is not None:
                recorder.refuse("unknown or duplicate test report")
            else:
                row[index] = outcome

        def pytest_collectreport(self, report):
            if report.failed:
                recorder.errors["collection"] += 1

    return Hooks()
