# SPDX-License-Identifier: Apache-2.0
"""Closed FIXVAL inventories and bounded terminal proof, separate from L1 receipts.

Raw evidence stays on the runner. External consumers validate the compact
projection AND the admitted validator's execution provenance, not omitted rows.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import tempfile
import time

from . import _gate_pytest as capture_api
from ._mutation_process import run_owned_command, _bind_cancellation_evidence

from .fixval_terminal import (
    EvidenceError,
    canonical,
    digest,
    new_stage,
    validate_stage,
    validate_terminal_stage as validate_terminal_stage,
    MAX_TIMEOUT,
    MAX_STAGE_BYTES as MAX_STAGE_BYTES,
    MAX_WITNESS_BYTES,
    MAX_RAW_BYTES,
    MAX_RAW_FILES,
    MAX_COMMANDS,
)


def validator_digest():
    """Bind the concrete producer/validator/owner implementation, not one module."""
    directory = Path(__file__).parent
    names = (
        "fixval.py",
        "fixval_evidence.py",
        "fixval_terminal.py",
        "_gate_pytest.py",
        "_fixval_transaction.py",
        "_mutation_process.py",
        "_mutation_imports.py",
    )
    return digest({name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in names})


def validate_test_timeout(value, name="test.timeout_seconds") -> int:
    if type(value) is not int or not 1 <= value <= MAX_TIMEOUT:
        raise ValueError(f"{name} must be an integer in [1, {MAX_TIMEOUT}], not a boolean")
    return value


def normalize_file(path, root: Path) -> str:
    path = Path(os.path.abspath(root / path))
    relative = path.relative_to(Path(os.path.abspath(root)))
    if not relative.parts or relative.as_posix() == ".":
        raise ValueError("empty candidate file")
    return relative.as_posix()


@dataclass(frozen=True)
class Inventory:
    rows: dict[str, tuple[str, str, str | None, str, bool]]
    returncode: int
    record_sha256: str
    record_bytes: int
    pytest_version: str

    @property
    def inventory_sha256(self):
        return digest(sorted((node, *row) for node, row in self.rows.items()))

    @property
    def collection(self):
        return sorted((node, row[0]) for node, row in self.rows.items())

    @property
    def passed(self):
        return {node for node, row in self.rows.items() if row[2] == "passed" and not row[4]}

    @property
    def failed(self):
        return {node for node, row in self.rows.items() if row[2] == "failed" and not row[4]}


def validate_owned_envelope(envelope, *, binding, ownership, returncode, raw=None) -> Inventory:
    """Pure validation. Ownership is from the trusted owner channel, never pytest."""
    capture_api._validate_fixval_binding(binding.get("fixval"))
    if not isinstance(envelope, dict) or set(envelope) != {
        "schema",
        "record",
        "files",
        "framework",
        "producer",
    }:
        raise EvidenceError("missing FIXVAL envelope")
    if envelope["schema"] != "fixval-pytest-v1":
        raise EvidenceError("unsupported FIXVAL envelope")
    expected = binding["fixval"]
    if (
        not isinstance(ownership, dict)
        or type(ownership.get("capture_version")) is not int
        or ownership["capture_version"] != 1
        or type(returncode) is not int
    ):
        raise EvidenceError("owned capture unavailable")
    for name in (
        "owner_pid",
        "owner_start_ticks",
        "driver_pid",
        "driver_start_ticks",
        "caller_pid",
        "caller_start_ticks",
    ):
        if type(ownership.get(name)) is not int or ownership[name] <= 0:
            raise EvidenceError("owner incarnation unavailable")
    if (
        ownership.get("invocation_nonce") != expected["nonce"]
        or ownership["caller_pid"] != binding["parent_pid"]
        or ownership["caller_start_ticks"] != expected["caller_start_ticks"]
        or ownership.get("cleanup_complete") is not True
        or ownership.get("timed_out") is not False
        or ownership.get("cancelled") is not False
        or ownership.get("returncode") != returncode
        or ownership.get("error")
    ):
        raise EvidenceError("owned execution did not complete cleanly")
    producer = envelope["producer"]
    if (
        not isinstance(producer, dict)
        or producer
        != {
            "pid": ownership["driver_pid"],
            "start_ticks": ownership["driver_start_ticks"],
            "parent_pid": ownership["owner_pid"],
            "parent_start_ticks": ownership["owner_start_ticks"],
        }
        or any(type(v) is not int for v in producer.values())
    ):
        raise EvidenceError("owned producer incarnation mismatch")
    record = envelope["record"]
    valid = capture_api.validate_inventory(
        record, binding=binding, child_pid=ownership["driver_pid"], returncode=returncode
    )
    if not valid.valid:
        raise EvidenceError(valid.reason)
    files, framework = envelope["files"], envelope["framework"]
    if (
        not isinstance(files, list)
        or not isinstance(framework, list)
        or len(files) != len(valid.rows)
        or len(framework) != len(files)
    ):
        raise EvidenceError("missing collected-file/framework inventory")
    rows = {}
    for row, file, flag in zip(valid.rows, files, framework, strict=True):
        if (
            not isinstance(file, str)
            or not file
            or Path(file).is_absolute()
            or normalize_file(file, Path(expected["source_root"])) != file
        ):
            raise EvidenceError("noncanonical collected-file identity")
        if type(flag) is not bool:
            raise EvidenceError("invalid framework outcome metadata")
        rows[row[0]] = (file, row[1], row[2], row[3], flag)
    encoded = canonical(envelope) if raw is None else raw
    return Inventory(
        rows, returncode, hashlib.sha256(encoded).hexdigest(), len(encoded), record["pytest_version"]
    )


def require_green(inventory: Inventory, candidates: set[str]) -> None:
    if inventory.returncode != 0 or any(row[2] == "failed" for row in inventory.rows.values()):
        raise EvidenceError("fixed baseline did not pass")
    if not any(inventory.rows[node][0] in candidates for node in inventory.passed):
        raise EvidenceError("no ordinary candidate test call passed")


def choose_witness(greens: list[Inventory], red: Inventory, candidates: set[str]):
    if len(greens) != 3 or any(g.rows != greens[0].rows for g in greens[1:]):
        raise EvidenceError("fixed inventory/status changed across three runs")
    if red.collection != greens[0].collection:
        raise EvidenceError("reverted collection changed")
    if red.returncode == 0 and not any(row[2] == "failed" for row in red.rows.values()):
        return None, 0
    if red.returncode != 1:
        raise EvidenceError("reverted execution is not a test-call failure")
    eligible = sorted(
        node
        for node in red.failed
        if red.rows[node][0] in candidates and all(node in g.passed for g in greens)
    )
    if not eligible:
        raise EvidenceError("no attributable ordinary candidate test-call failure")
    for node in eligible:
        if len(canonical(node)) <= MAX_WITNESS_BYTES:
            return node, len(eligible)
    raise EvidenceError("no complete witness fits the compact proof limit")


def _capture_env(capture, env):
    child = dict(env)
    child["FORGE_GATE_BINDING"] = json.dumps(capture.transport)
    child["FORGE_GATE_BOOTSTRAP"] = capture.module
    inherited = child.get("PYTHONPATH", "")
    parts = [str(capture.directory)]
    if capture.source_root and inherited.split(os.pathsep)[0] == capture.source_root:
        parts.insert(0, capture.source_root)
        inherited = os.pathsep.join(inherited.split(os.pathsep)[1:])
    if inherited:
        parts.append(inherited)
    child["PYTHONPATH"] = os.pathsep.join(parts)
    return child


def _owned_capture_intact(capture):
    try:
        return stat.S_ISDIR(capture.directory.lstat().st_mode) and capture.intact()
    except OSError:
        return False


def read_owned_capture(capture, ownership, returncode, child_env) -> Inventory:
    deadline = time.monotonic() + 2
    if (
        not capture.authority
        or not _owned_capture_intact(capture)
        or "violation" in capture_api._entries(capture.fd)
    ):
        raise EvidenceError("capture authority/identity unavailable")
    if (
        capture_api.read_binding_transport(
            capture.transport, capture.binding["bootstrap_path"], deadline=deadline
        )
        != capture.binding
    ):
        raise EvidenceError("capture binding changed")
    for kind in ("reporter", "bootstrap"):
        raw, identity = capture_api._safe_read(
            capture.binding[kind + "_path"], reporter_source=kind == "reporter", deadline=deadline
        )
        if (
            hashlib.sha256(raw).hexdigest() != capture.binding[kind + "_hash"]
            or list(identity) != capture.binding[kind + "_identity"]
        ):
            raise EvidenceError(kind + " changed")
    if (
        ownership.get("argv_sha256") != digest(capture.command)
        or ownership.get("env_sha256") != digest(child_env)
        or ownership.get("cwd") != capture.binding["cwd"]
    ):
        raise EvidenceError("owned command/environment/cwd mismatch")
    begin = capture_api.decode_record(
        capture_api._safe_read("begin.json", dir_fd=capture.fd, deadline=deadline)[0]
    )
    if begin != {"binding": capture.binding, "pid": ownership.get("driver_pid")}:
        raise EvidenceError("begin record mismatch")
    raw = capture_api._safe_read("final.json", dir_fd=capture.fd, deadline=deadline)[0]
    envelope = capture_api.decode_record(raw)
    result = validate_owned_envelope(
        envelope, binding=capture.binding, ownership=ownership, returncode=returncode, raw=raw
    )
    if time.monotonic() > deadline or not _owned_capture_intact(capture):
        raise EvidenceError("capture changed or validation deadline exceeded")
    return result


class EvidenceSession:
    """One bounded external directory; no hidden upload or raw-proof deletion."""

    def __init__(self, root: Path, command, candidates, *, parent=None, stage_id=None):
        self.root = Path(os.path.abspath(root))
        self.stage_id = stage_id or secrets.token_hex(16)
        parent = Path(os.path.abspath(parent if parent is not None else self.root.parent))
        if parent.resolve().is_relative_to(self.root.resolve()) or not stat.S_ISDIR(
            parent.lstat().st_mode
        ):
            raise EvidenceError("evidence parent must be a real directory outside source")
        self.command = list(command)
        self.candidates = {normalize_file(f, self.root) for f in candidates}
        self.directory = Path(tempfile.mkdtemp(prefix=".fixval-evidence-", dir=parent))
        self.identity = capture_api._stamp(self.directory.stat())[:3]
        self.phases = []
        self.captures = []
        self.closed = False

    def execute(self, env, *, phase, timeout):
        validate_test_timeout(timeout, "FIXVAL timeout")
        if len(self.phases) >= MAX_COMMANDS:
            raise EvidenceError("too many FIXVAL commands")
        nonce = secrets.token_hex(16)
        record = {
            "phase": phase,
            "nonce": nonce,
            "timeout": timeout,
            "status": "pending",
            "duration": 0.0,
            "base_env_sha256": digest(env),
            "retry_eligible": False,
            "superseded": False,
        }
        self.phases.append(record)
        binding = {
            "version": 1,
            "mode": "owned",
            "nonce": nonce,
            "phase": phase,
            "source_root": str(self.root),
            "caller_start_ticks": capture_api._process_incarnation(os.getpid()),
        }
        capture = capture_api.prepare_pytest_capture(
            self.command,
            test_cwd=self.root,
            test_env=env,
            reporter_path=Path(capture_api.__file__).resolve(),
            fixval_binding=binding,
            capture_parent=self.directory,
        )
        if capture is None:
            record["status"] = "unsupported"
            raise EvidenceError("unsupported direct pytest command or capture unavailable")
        capture.source_root = str(self.root / "src")
        child = _capture_env(capture, env)
        self.captures.append((capture, record, child))
        start = time.monotonic()
        primary = None
        try:
            result = run_owned_command(
                capture.command,
                env=child,
                cwd=str(self.root),
                timeout=timeout,
                text=False,
                output_limit_bytes=1024 * 1024,
                invocation_nonce=nonce,
            )
            record["ownership"] = result.ownership
            record["returncode"] = result.returncode
            record["stdout"] = result.stdout
            record["stderr"] = result.stderr
            try:
                inventory = read_owned_capture(capture, result.ownership, result.returncode, child)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                raise EvidenceError("closed pytest evidence unavailable: " + str(exc)) from exc
            record["inventory"] = inventory
            record["status"] = "complete"
            return inventory
        except BaseException as exc:
            primary = exc
            record["status"] = "timeout" if isinstance(exc, subprocess.TimeoutExpired) else "error"
            record["error"] = f"{type(exc).__name__}: {exc}"[:2000]
            record["ownership"] = getattr(
                exc, "ownership", getattr(exc, "report", record.get("ownership", {}))
            )
            if not isinstance(exc, Exception) and not hasattr(exc, "cleanup_complete"):
                _bind_cancellation_evidence(
                    exc,
                    cleanup_complete=record["ownership"].get("cleanup_complete", False),
                    ownership=record["ownership"],
                )
            if isinstance(exc, subprocess.TimeoutExpired):
                record["stdout"] = exc.output or ""
                record["stderr"] = exc.stderr or ""
            raise
        finally:
            # Capture is retained, not destructively closed, for terminal replay.
            # Its write/seal work is part of the command envelope, not free time.
            try:
                try:
                    self._persist_phase(capture, record, deadline=start + timeout + 30)
                finally:
                    record["duration"] = round(time.monotonic() - start, 6)
                if record["duration"] > timeout + 30:
                    raise EvidenceError("FIXVAL command envelope deadline exceeded")
            except BaseException as secondary:
                if primary is not None:
                    if isinstance(primary, Exception) and not isinstance(secondary, Exception):
                        _bind_cancellation_evidence(
                            secondary,
                            cleanup_complete=record.get("ownership", {}).get("cleanup_complete", False),
                            ownership=record.get("ownership", {}),
                        )
                        raise secondary from primary
                    primary.add_note("FIXVAL phase retention failed: " + str(secondary))
                else:
                    raise

    def _persist_phase(self, capture, record, *, deadline=None):
        if not _owned_capture_intact(capture):
            raise EvidenceError("capture directory replaced")
        written = {}
        for name, value in (
            ("owner.json", canonical(record.get("ownership", {}))),
            ("stdout.txt", record.get("stdout", "")),
            ("stderr.txt", record.get("stderr", "")),
        ):
            if deadline is not None and time.monotonic() > deadline:
                raise EvidenceError("phase retention deadline exceeded")
            if isinstance(value, str):
                value = value.encode("utf-8", "replace")
            if len(value) > (256 * 1024 if name == "owner.json" else 1024 * 1024):
                raise EvidenceError("phase retention overflow")
            fd = os.open(
                name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=capture.fd
            )
            with os.fdopen(fd, "wb") as stream:
                stream.write(value)
                stream.flush()
                written[name] = (value, capture_api._stamp(os.fstat(stream.fileno())))
        retained = {}
        total_prefix = 0
        for name in ("owner.json", "stdout.txt", "stderr.txt"):
            raw, identity = capture_api._safe_read(
                name, dir_fd=capture.fd, limit=256 * 1024 if name == "owner.json" else 1024 * 1024,
                deadline=deadline,
            )
            if (raw, identity) != written[name]:
                raise EvidenceError("retained phase file changed before sealing")
            retained[name] = {
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "identity": identity,
            }
            if name != "owner.json":
                total_prefix += len(raw)
                expected = (
                    record.get("ownership", {})
                    .get("streams", {})
                    .get(name[:-4], {})
                    .get("retained_bytes", 0)
                )
                if len(raw) != expected:
                    raise EvidenceError("retained diagnostic prefix contradicts owner")
        if total_prefix > 1024 * 1024:
            raise EvidenceError("combined diagnostic prefix overflow")
        record["retained_files"] = retained

    def _replay_retained(self, capture, record):
        retained = record.get("retained_files")
        if not isinstance(retained, dict) or set(retained) != {"owner.json", "stdout.txt", "stderr.txt"}:
            raise EvidenceError("missing retained-file seals")
        prefix = 0
        for name, expected in retained.items():
            raw, identity = capture_api._safe_read(
                name, dir_fd=capture.fd, limit=256 * 1024 if name == "owner.json" else 1024 * 1024
            )
            if {
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "identity": identity,
            } != expected:
                raise EvidenceError("retained phase file changed")
            if name != "owner.json":
                prefix += len(raw)
        if prefix > 1024 * 1024:
            raise EvidenceError("combined diagnostic prefix overflow")

    def replay(self):
        for capture, record, child in self.captures:
            self._replay_retained(capture, record)
            if record["status"] == "complete":
                raw = capture_api._safe_read("owner.json", dir_fd=capture.fd, limit=256 * 1024)[0]
                owner = capture_api.decode_record(raw)
                if owner != record["ownership"]:
                    raise EvidenceError("retained owner report changed")
                current = read_owned_capture(capture, owner, record["returncode"], child)
                if current != record["inventory"]:
                    raise EvidenceError("retained inventory changed")
        return self.manifest()

    def manifest(self):
        root_stat = self.directory.lstat()
        if not stat.S_ISDIR(root_stat.st_mode) or capture_api._stamp(root_stat)[:3] != self.identity:
            raise EvidenceError("raw evidence root changed")
        allowed_dirs = {capture.directory.name for capture, _, _ in self.captures}
        if set(os.listdir(self.directory)) != allowed_dirs:
            raise EvidenceError("unexpected evidence directory entry")
        entries = []
        total = 0
        for capture, _, _ in self.captures:
            if not _owned_capture_intact(capture):
                raise EvidenceError("retained capture changed")
            names = capture_api._entries(capture.fd)
            allowed = {
                "binding.json",
                "begin.json",
                "final.json",
                "owner.json",
                "stdout.txt",
                "stderr.txt",
                capture.module + ".py",
                "violation",
                "__pycache__",
            }
            if set(names) - allowed:
                raise EvidenceError("unexpected capture entry")
            paths = []
            for name in names:
                if name == "__pycache__":
                    cache = capture.directory / name
                    if not stat.S_ISDIR(cache.lstat().st_mode):
                        raise EvidenceError("invalid bootstrap cache directory")
                    for item in os.listdir(cache):
                        if not capture_api._bootstrap_cache_name(capture.module, item):
                            raise EvidenceError("foreign bootstrap cache")
                        paths.append(cache / item)
                else:
                    paths.append(capture.directory / name)
            for path in paths:
                if path.parent == capture.directory:
                    raw, _ = capture_api._safe_read(path.name, dir_fd=capture.fd)
                else:
                    cache_fd = os.open(
                        "__pycache__", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=capture.fd
                    )
                    try:
                        raw, _ = capture_api._safe_read(path.name, dir_fd=cache_fd)
                    finally:
                        os.close(cache_fd)
                total += len(raw)
                entries.append(
                    {
                        "path": str(path.relative_to(self.directory)),
                        "bytes": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest(),
                    }
                )
                if total > MAX_RAW_BYTES or len(entries) > MAX_RAW_FILES:
                    raise EvidenceError("raw evidence bound exceeded")
            if not _owned_capture_intact(capture):
                raise EvidenceError("retained capture changed during manifest")
        if capture_api._stamp(self.directory.lstat())[:3] != self.identity:
            raise EvidenceError("raw evidence root changed during manifest")
        return {
            "directory": str(self.directory),
            "identity": list(self.identity),
            "bytes": total,
            "files": len(entries),
            "sha256": digest(sorted(entries, key=lambda e: e["path"])),
        }

    def projection(
        self,
        *,
        source_hash,
        config_hash,
        candidate_hash,
        greens,
        red,
        witness,
        witness_count,
        outcome,
        reason,
        restoration,
    ):
        manifest = self.replay()
        phases = []
        for capture, record, _ in self.captures:
            inventory = record.get("inventory")
            owner = record.get("ownership", {})
            summary = {key: record[key] for key in ("phase", "nonce", "timeout", "status", "duration")}
            summary.update(
                returncode=record.get("returncode"),
                record_sha256=inventory.record_sha256 if inventory else None,
                record_bytes=inventory.record_bytes if inventory else 0,
                inventory_sha256=inventory.inventory_sha256 if inventory else None,
                collection_sha256=digest(inventory.collection) if inventory else None,
                base_env_sha256=record["base_env_sha256"],
                pytest_version=inventory.pytest_version if inventory else None,
                retry_eligible=record["retry_eligible"],
                superseded=record["superseded"],
                collected=len(inventory.rows) if inventory else 0,
                failed=sum(row[2] == "failed" for row in inventory.rows.values()) if inventory else 0,
                passed=sum(row[2] == "passed" for row in inventory.rows.values()) if inventory else 0,
                skipped=sum(
                    row[1] == "skipped" or row[2] == "skipped" for row in inventory.rows.values()
                )
                if inventory
                else 0,
                ordinary_passed=len(inventory.passed) if inventory else 0,
                ordinary_failed=len(inventory.failed) if inventory else 0,
                configured_argv_sha256=digest(self.command),
                effective_argv_sha256=digest(capture.command),
                env_sha256=owner.get("env_sha256"),
                cleanup_complete=owner.get("cleanup_complete", False),
                owner={
                    key: owner.get(key)
                    for key in (
                        "caller_pid",
                        "caller_start_ticks",
                        "owner_pid",
                        "owner_start_ticks",
                        "driver_pid",
                        "driver_start_ticks",
                        "invocation_nonce",
                        "resolved_executable",
                    )
                },
                diagnostic_truncated=owner.get("diagnostic_truncated", False),
                streams=owner.get("streams", {}),
            )
            phases.append(summary)
        linked = None
        if witness is not None:
            linked = {
                "node": witness,
                "file": red.rows[witness][0],
                "eligible_count": witness_count,
                "green": [
                    {
                        "record_sha256": g.record_sha256,
                        "row_sha256": digest([witness, *g.rows[witness]]),
                        "call": "passed",
                        "row": list(g.rows[witness][1:]),
                    }
                    for g in greens
                ],
                "red": {
                    "record_sha256": red.record_sha256,
                    "row_sha256": digest([witness, *red.rows[witness]]),
                    "call": "failed",
                    "row": list(red.rows[witness][1:]),
                },
            }
        stage = new_stage(self.stage_id, source_hash)
        stage.update(
            outcome=outcome,
            reason=reason,
            config_sha256=config_hash,
            candidate_sha256=candidate_hash,
            raw=manifest,
            phases=phases,
            witness=linked,
            restoration=restoration,
            validator_sha256=validator_digest(),
            reporter_sha256=hashlib.sha256(Path(capture_api.__file__).read_bytes()).hexdigest(),
        )
        validate_stage(stage)
        return stage

    def close(self):
        for capture, _, _ in self.captures:
            if capture.fd is not None:
                os.close(capture.fd)
                capture.fd = None
        self.closed = True


def source_snapshot(root: Path, paths) -> dict:
    """Bind admitted entries and index without following source-entry links.

    This is observation only, never restoration. A changed baseline refuses
    validation rather than silently overwriting a test's or user's changes.
    """
    from ._fixval_transaction import _identity

    root = Path(os.path.abspath(root))
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        root_stat = os.fstat(root_fd)
        entries = {}
        for name in sorted({normalize_file(path, root) for path in paths}):
            components = Path(name).parts
            held = []
            parent = root_fd
            try:
                for part in components[:-1]:
                    parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                    held.append(parent)
                entries[name] = _identity(parent, components[-1])
            except FileNotFoundError:
                entries[name] = None
            finally:
                for descriptor in reversed(held):
                    os.close(descriptor)
        current = root.lstat()
        if (current.st_dev, current.st_ino) != (root_stat.st_dev, root_stat.st_ino):
            raise EvidenceError("source root changed during snapshot")
        git = root / ".git"
        index = None
        try:
            info = git.lstat()
        except FileNotFoundError:
            info = None
        if info is not None:
            if stat.S_ISDIR(info.st_mode):
                git_dir = git
            elif stat.S_ISREG(info.st_mode):
                data = capture_api._safe_read(git, limit=16 * 1024)[0].decode("utf-8")
                if not data.startswith("gitdir: ") or len(data.splitlines()) != 1:
                    raise EvidenceError("invalid worktree Git directory reference")
                git_dir = (root / data.removeprefix("gitdir: ").strip()).resolve(strict=True)
            else:
                raise EvidenceError("unsupported Git directory entry")
            descriptor = os.open(git_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                identity = _identity(descriptor, "index")
                if identity is not None and not stat.S_ISREG(identity[2]):
                    raise EvidenceError("Git index is not a regular file")
                index = {"path": str(git_dir / "index"), "identity": identity}
            finally:
                os.close(descriptor)
        source = {"root": [root_stat.st_dev, root_stat.st_ino], "entries": entries}
        semantic = {
            name: None if value is None else (value[2], value[4], value[-1])
            for name, value in entries.items()
        }
        return {
            "source_sha256": digest(source),
            "semantic_sha256": digest(semantic),
            "index_sha256": digest(index),
            "entries": entries,
        }
    finally:
        os.close(root_fd)
