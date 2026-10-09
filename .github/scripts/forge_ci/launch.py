"""Complete immutable checkout verification and shared late owner-push identity.

The workflow renders this standalone stdlib verifier as a fixed reviewed literal.
No candidate import occurs until all tracked bytes and helper hashes are checked.
There are no publication manifests, source exclusions, or embedded final SHA.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import signal
import stat
import subprocess
import time
from typing import Any

MAX_JSON = 256 * 1024
MAX_API = 1024 * 1024
MAX_FILE = 16 * 1024 * 1024
MAX_TREE = 8 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024
MAX_FILES = 20000
WORKFLOW_PATH = ".github/workflows/linux-tests.yml"
RENDERER_PATH = ".github/scripts/render_linux_workflow.py"
HELPER_PATHS = frozenset(
    ".github/scripts/forge_ci/" + name
    for name in (
        "__init__.py", "facts.py", "launch.py", "admission.py", "setup_policy.py", "controller.py",
        "outcomes.py", "payload.py", "probes.py", "pytest_observer.py", "user_service.py", "baseline_measurement.py",
    )
)
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
SOURCE_KEYS = {"candidate_sha", "tree_oid", "source_sha256", "workflow_sha256", "helper_sha256"}


class LaunchError(RuntimeError):
    """A missing, ambiguous, or mismatched launch fact requires STOP."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise LaunchError(message)


def _keys(value: Any, keys: set[str], label: str) -> dict:
    _need(type(value) is dict and set(value) == keys, f"invalid {label} fields")
    return value


def _sha(value: Any, label: str, *, sha256: bool = False) -> str:
    pattern = _SHA256 if sha256 else _SHA1
    _need(type(value) is str and pattern.fullmatch(value) is not None, f"invalid {label}")
    _need(value != "0" * len(value), f"empty {label}")
    return value


def _pairs(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        _need(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def _constant(_: str) -> None:
    raise LaunchError("nonfinite JSON value")


def _bounded_structure(value: Any, depth: int = 0) -> None:
    _need(depth <= 24, "JSON nesting limit exceeded")
    if type(value) is dict:
        _need(len(value) <= 4096, "JSON object bound exceeded")
        for key, item in value.items():
            _need(len(key) <= 4096, "JSON key bound exceeded")
            _bounded_structure(item, depth + 1)
    elif type(value) is list:
        _need(len(value) <= 20000, "JSON array bound exceeded")
        for item in value:
            _bounded_structure(item, depth + 1)
    elif type(value) is str:
        _need(len(value) <= 65536, "JSON string bound exceeded")


def parse_json(raw: bytes, *, limit: int = MAX_JSON) -> dict:
    _need(type(raw) is bytes and 0 < len(raw) <= limit, "JSON byte bound exceeded")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
        _bounded_structure(value)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise LaunchError("malformed JSON") from exc
    _need(type(value) is dict, "JSON root is not an object")
    return value


def read_regular(path: Path, *, limit: int = MAX_FILE, deadline=None) -> bytes:
    """No symlink or FIFO reads, bounded even if the file changes while reading."""
    _need(deadline is None or time.monotonic() < deadline, "checkout read deadline exceeded")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            _need(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "nonregular input file")
            raw = stream.read(limit + 1)
    except OSError as exc:
        raise LaunchError("cannot read required file") from exc
    _need(len(raw) <= limit, "input file bound exceeded")
    _need(deadline is None or time.monotonic() < deadline, "checkout read deadline exceeded")
    return raw


def _git(repo: Path, *args: str, deadline=None) -> bytes:
    # No inherited Git alternate object locations, config injection or replace refs.
    environment = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LC_ALL": "C",
                   "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_NO_REPLACE_OBJECTS": "1"}
    started = time.monotonic()
    _need(deadline is None or (type(deadline) in (int, float) and math.isfinite(deadline)
                              and deadline > started + 5), "checkout metadata settlement budget unavailable")
    command_cutoff = started + 20 if deadline is None else min(started + 20, deadline - 5)
    cleanup_cutoff = command_cutoff + 5 if deadline is None else min(deadline, command_cutoff + 5)
    process = selector = None
    failure = None
    result = None
    chunks = {"stdout": bytearray(), "stderr": bytearray()}

    def retain(exc):
        nonlocal failure
        expected = (LaunchError, OSError, subprocess.TimeoutExpired)
        if failure is None or (isinstance(failure, expected) and not isinstance(exc, expected)):
            # The first control/unexpected exception outranks an ordinary
            # failure; subsequent teardown cannot replace that exact object.
            failure = exc

    try:
        process = subprocess.Popen(["/usr/bin/git", "--no-replace-objects", "-C", str(repo), *args],
                                   env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=True)
        _need(time.monotonic() < command_cutoff, "checkout metadata deadline exceeded")
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        # EOF does not prove child exit. Poll under the command cutoff; reserve
        # the single positive wait exclusively for failure settlement below.
        while True:
            remaining = command_cutoff - time.monotonic()
            _need(remaining > 0, "checkout metadata deadline exceeded")
            if not selector.get_map() and process.poll() is not None:
                break
            for key, _ in selector.select(min(remaining, 0.1)):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                chunks[key.data].extend(chunk)
                limit = MAX_TREE if key.data == "stdout" else 65536
                _need(len(chunks[key.data]) <= limit, "checkout metadata byte bound exceeded")
        _need(process.returncode == 0, "checkout metadata command failed")
        result = bytes(chunks["stdout"])
        _need(time.monotonic() < command_cutoff, "checkout metadata deadline exceeded")
    except BaseException as exc:  # noqa: BLE001 - retain control through owned cleanup
        if process is None and isinstance(exc, OSError):
            failure = LaunchError("checkout metadata unavailable")
            failure.__cause__ = exc
        else:
            retain(exc)
    finally:
        if selector is not None:
            try:
                selector.close()
            except BaseException as exc:  # noqa: BLE001 - cannot mask first control
                retain(exc)
        if process is not None:
            try:
                if process.poll() is None and time.monotonic() < cleanup_cutoff:
                    # The original session is signaled only while its retained
                    # leader is still live, never after a reaped/reused identity.
                    os.killpg(process.pid, signal.SIGKILL)
                    remaining = cleanup_cutoff - time.monotonic()
                    if remaining > 0:
                        process.wait(timeout=min(5, remaining))
                _need(process.poll() is not None, "checkout metadata cleanup incomplete")
            except BaseException as exc:  # noqa: BLE001 - no second wait or new window
                retain(exc)
            finally:
                for pipe in (process.stdout, process.stderr):
                    if pipe is not None:
                        try:
                            pipe.close()
                        except BaseException as exc:  # noqa: BLE001 - close both, preserve first
                            retain(exc)
    if failure is not None:
        raise failure
    # Successful completion, validation and closure must precede the command
    # cutoff. A late exit0 during settlement is always failure.
    _need(time.monotonic() < command_cutoff, "checkout metadata deadline exceeded")
    return result


def _tree(repo: Path, revision: str, *, deadline=None) -> dict[str, tuple[str, str]]:
    raw = _git(repo, "ls-tree", "-r", "-z", "--full-tree", revision, deadline=deadline)
    _need(raw.endswith(b"\0"), "empty or malformed Git tree")
    result = {}
    try:
        for item in raw[:-1].split(b"\0"):
            metadata, path_raw = item.split(b"\t", 1)
            mode, kind, oid = metadata.decode("ascii").split(" ")
            path = path_raw.decode("utf-8")
            parts = PurePosixPath(path).parts
            _need(path not in result and parts and not path.startswith("/")
                  and all(part not in {".", "..", ".git"} for part in parts)
                  and len(path) <= 1024 and len(parts) <= 48 and str(PurePosixPath(path)) == path,
                  "invalid Git tree path")
            _need(kind == "blob" and mode in {"100644", "100755", "120000"}, "unsupported Git tree entry")
            _sha(oid, "Git blob")
            result[path] = (mode, oid)
            _need(len(result) <= MAX_FILES, "Git tree file bound exceeded")
    except (ValueError, UnicodeError) as exc:
        raise LaunchError("malformed Git tree") from exc
    return result


def source_digest(records: list[dict]) -> str:
    return hashlib.sha256(canonical_bytes(sorted(records, key=lambda item: item["path"]))).hexdigest()


def _unexpected_artifacts(repo, tracked, deadline):
    """Imports and executable additions outside the immutable tree fail closed."""
    count = 0
    for root, directories, files in os.walk(repo, followlinks=False):
        _need(time.monotonic() < deadline, "checkout inspection deadline exceeded")
        if Path(root) == repo:
            directories[:] = [name for name in directories if name != ".git"]
        for name in directories + files:
            count += 1
            _need(count <= MAX_FILES * 2, "checkout filesystem entry bound exceeded")
            path = Path(root) / name
            relative = path.relative_to(repo).as_posix()
            info = path.lstat()
            if relative in tracked:
                continue
            if name == "__pycache__":
                kind = ("directory" if stat.S_ISDIR(info.st_mode) else
                        "regular_file" if stat.S_ISREG(info.st_mode) else
                        "symlink" if stat.S_ISLNK(info.st_mode) else "other")
                detail = {"kind": "unexpected_import_cache", "path": relative[:64],
                          "path_truncated": len(relative) > 64, "type": kind}
                _need(False, "unexpected import cache " + json.dumps(
                    detail, ensure_ascii=True, separators=(",", ":")))
            if stat.S_ISDIR(info.st_mode):
                continue
            _need(stat.S_ISREG(info.st_mode) and not info.st_mode & 0o111
                  and path.suffix.lower() not in {".py", ".pyc", ".pyo", ".pyw", ".so", ".pyd", ".pth", ".zip"},
                  "unexpected executable/import artifact")


def inspect_checkout(repo: Path, candidate_sha: str, *, deadline=None) -> dict:
    _sha(candidate_sha, "admitted candidate")
    deadline = min(float("inf") if deadline is None else deadline, time.monotonic() + 60)
    _need(time.monotonic() < deadline, "checkout inspection deadline exceeded")
    repo = Path(repo).resolve(strict=True)
    head = _git(repo, "rev-parse", "--verify", "HEAD", deadline=deadline).decode("ascii").strip()
    _need(head == candidate_sha, "checkout HEAD differs from admitted candidate")
    tree_oid = _git(repo, "rev-parse", "--verify", candidate_sha + "^{tree}", deadline=deadline).decode("ascii").strip()
    _sha(tree_oid, "immutable commit tree")
    tree = _tree(repo, candidate_sha, deadline=deadline)
    _need(HELPER_PATHS | {WORKFLOW_PATH, RENDERER_PATH} <= set(tree), "workflow/helper/renderer missing")
    _need({path for path in tree if path.startswith(".github/scripts/")} == HELPER_PATHS | {RENDERER_PATH},
          "unexpected helper import root")
    records, hashes, total = [], {}, 0
    for relative, (mode, oid) in sorted(tree.items()):
        _need(time.monotonic() < deadline, "checkout inspection deadline exceeded")
        target = repo / relative
        _need(target.parent.resolve(strict=True) == target.parent, "checkout parent symlink")
        info = target.lstat()
        if mode == "120000":
            _need(stat.S_ISLNK(info.st_mode) and relative not in HELPER_PATHS | {WORKFLOW_PATH, RENDERER_PATH},
                  "invalid helper/workflow symlink")
            raw = os.readlink(target).encode("utf-8")
            _need(len(raw) <= MAX_FILE and target.resolve(strict=False).is_relative_to(repo), "checkout symlink escape")
        else:
            _need(stat.S_ISREG(info.st_mode), "nonregular checkout file")
            _need(bool(info.st_mode & 0o111) == (mode == "100755"), "checkout executable mode drift")
            raw = read_regular(target, deadline=deadline)
        total += len(raw)
        _need(total <= MAX_TOTAL, "checkout byte bound exceeded")
        blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw, usedforsecurity=False).hexdigest()
        _need(blob == oid, "checkout bytes differ from immutable tree")
        checksum = hashlib.sha256(raw).hexdigest()
        hashes[relative] = checksum
        records.append({"path": relative, "mode": mode, "sha256": checksum})
    _unexpected_artifacts(repo, tree, deadline)
    _need(time.monotonic() < deadline, "checkout inspection deadline exceeded")
    return {"candidate_sha": candidate_sha, "tree_oid": tree_oid, "source_sha256": source_digest(records),
            "workflow_sha256": hashes[WORKFLOW_PATH], "helper_sha256": {path: hashes[path] for path in sorted(HELPER_PATHS)}}


def validate_source(source):
    _keys(source, SOURCE_KEYS, "source receipt")
    for name in ("candidate_sha", "tree_oid"):
        _sha(source[name], name)
    for name in ("source_sha256", "workflow_sha256"):
        _sha(source[name], name, sha256=True)
    _keys(source["helper_sha256"], set(HELPER_PATHS), "helper digests")
    for value in source["helper_sha256"].values():
        _sha(value, "helper", sha256=True)
    return source


def verify_initial_checkout(repo, expected_helper_sha256):
    """Called from reviewed workflow literal before ANY checkout helper import."""
    _keys(expected_helper_sha256, set(HELPER_PATHS), "literal helper digests")
    for value in expected_helper_sha256.values():
        _sha(value, "literal helper", sha256=True)
    candidate = os.environ.get("GITHUB_SHA")
    _need(candidate == os.environ.get("GITHUB_WORKFLOW_SHA"), "native workflow/candidate mismatch")
    source = inspect_checkout(Path(repo), candidate)
    _need(source["helper_sha256"] == expected_helper_sha256, "helper bytes differ from reviewed workflow literal")
    return source


def validate_launch(context, event, checkout, *, evidence_dir):
    """Bind verified source to a fresh root observation without a credential handoff."""
    # This lazy import is only used after the caller's literal checkout verifier.
    from . import admission, setup_policy as setup
    validate_source(checkout)
    try:
        native = setup.validate_initial_identity(setup.CONFIG, context, event)
        _need(checkout["candidate_sha"] == native["candidate_sha"], "native/checkout candidate mismatch")
        rule = {"schema_version": 2, "setup_spec_sha256": setup.SPEC_SHA256,
                "setup_module_sha256": checkout["helper_sha256"][".github/scripts/forge_ci/setup_policy.py"]}
        observed = admission.observe_setup(native, rule, checkout["tree_oid"], evidence_dir, "initial")
        live = observed["live"]
        _need(checkout["tree_oid"] == live["tree_oid"], "provider/checkout immutable tree mismatch")
    except (setup.SetupError, admission.AdmissionError) as exc:
        raise LaunchError(str(exc)) from exc
    return validate_receipt({"schema_version": 1, "status": "PASS", "binding": live["binding"], "source": checkout, "live": live})


def validate_receipt(value):
    from . import setup_policy as setup
    _keys(value, {"schema_version", "status", "binding", "source", "live"}, "launch receipt")
    _need(type(value["schema_version"]) is int and value["schema_version"] == 1 and value["status"] == "PASS", "invalid launch receipt")
    setup.validate_binding(value["binding"])
    validate_source(value["source"])
    _need(value["source"]["candidate_sha"] == value["binding"]["candidate_sha"], "source/binding candidate mismatch")
    setup.validate_live_evidence(value["live"], value["binding"], value["source"]["tree_oid"])
    return value


def validate_local_launch(context, event, checkout, authenticated_receipt):
    """Fresh local comparison only; the retained root observer owns live authority."""
    from . import setup_policy as setup
    validate_receipt(authenticated_receipt)
    validate_source(checkout)
    try:
        native = setup.validate_initial_identity(setup.CONFIG, context, event)
        expected = {name: authenticated_receipt["binding"][name] for name in setup.NATIVE_BINDING_KEYS}
        _need(canonical_bytes(native) == canonical_bytes(expected), "native/kernel binding differs from authenticated receipt")
        _need(canonical_bytes(checkout) == canonical_bytes(authenticated_receipt["source"]),
              "complete source differs from authenticated receipt")
    except setup.SetupError as exc:
        raise LaunchError(str(exc)) from exc
    # Do not copy live timestamps or metadata into this distinct local schema.
    return {"schema_version": 1, "status": "PASS", "observation_kind": "local_receipt_check",
            "binding": json.loads(canonical_bytes(authenticated_receipt["binding"])),
            "source": json.loads(canonical_bytes(checkout)),
            "receipt_sha256": hashlib.sha256(canonical_bytes(authenticated_receipt) + b"\n").hexdigest(),
            "local_checked": setup.stamp()}


def load_receipt(path):
    raw = read_regular(Path(path), limit=MAX_JSON)
    value = validate_receipt(parse_json(raw))
    _need(raw == canonical_bytes(value) + b"\n", "noncanonical launch receipt")
    return value


def write_receipt(path: Path, report: dict, *, limit=MAX_JSON, deadline=None) -> None:
    _need(deadline is None or time.monotonic() < deadline, "receipt deadline exceeded")
    # Full sealed policy observations retain their existing separate 8 MiB bound.
    _need(type(limit) is int and 0 < limit <= MAX_TREE, "unreviewed receipt byte bound")
    data = canonical_bytes(report) + b"\n"
    _need(len(data) <= limit, "receipt byte bound exceeded")
    _need(deadline is None or time.monotonic() < deadline, "receipt deadline exceeded")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        _need(deadline is None or time.monotonic() < deadline, "receipt deadline exceeded")
        stream.flush()
        _need(deadline is None or time.monotonic() < deadline, "receipt deadline exceeded")
        os.fsync(stream.fileno())
    _need(deadline is None or time.monotonic() < deadline, "receipt deadline exceeded")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        previous = load_receipt(args.receipt)
        event = parse_json(read_regular(Path(os.environ["GITHUB_EVENT_PATH"]), limit=MAX_API), limit=MAX_API)
        source = inspect_checkout(args.repo, previous["binding"]["candidate_sha"])
        report = validate_local_launch(os.environ, event, source, previous)
        _need(source == previous["source"] and report["binding"] == previous["binding"], "source/live identity changed")
        write_receipt(args.output, report)
    except (LaunchError, OSError, UnicodeError) as exc:
        print("STOP: " + str(exc))
        return 1
    print("PASS: complete checkout and owner-push identity verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
