"""Fail-closed identity gate for the single reviewed control-branch transition.

This module is read-only. PASS is a launch identity receipt, never permission to
load policy. The fixed workflow bootstrap must verify this module BEFORE import.
Repository/workflow freshness and review remain the publisher's responsibilities.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import signal
import ssl
import stat
import subprocess
import time
from typing import Any, Callable, Mapping

MAX_JSON = 256 * 1024
MAX_API = 1024 * 1024
MAX_FILE = 16 * 1024 * 1024
MAX_TREE = 8 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024
MAX_FILES = 20000
MAX_ID = 2**63 - 1
MANIFEST_PATH = ".github/qualification-manifest.json"
HELPER_PATHS = frozenset(
    ".github/scripts/forge_ci/" + name
    for name in (
        "__init__.py", "facts.py", "launch.py", "admission.py", "setup_policy.py", "controller.py",
        "outcomes.py", "payload.py", "probes.py", "pytest_observer.py", "user_service.py",
    )
)
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z")
_LOGIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}\Z")
_ENDPOINT = re.compile(
    r"https://api\.github\.com/repos/[A-Za-z0-9][A-Za-z0-9-]{0,38}/"
    r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/actions/runs/[1-9][0-9]{0,18}"
    r"/attempts/[1-9][0-9]{0,18}\Z"
)


class LaunchError(RuntimeError):
    """A missing, ambiguous, or mismatched launch fact requires STOP."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise LaunchError(message)


def _keys(value: Any, keys: set[str], label: str) -> dict:
    _need(type(value) is dict and set(value) == keys, f"invalid {label} fields")
    return value


def _id(value: Any, label: str, *, native: bool = False) -> int:
    if native:
        _need(type(value) is str and re.fullmatch(r"[1-9][0-9]{0,18}", value) is not None,
              f"invalid native {label}")
        value = int(value)
    _need(type(value) is int and 0 < value <= MAX_ID, f"invalid {label}")
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


def read_regular(path: Path, *, limit: int = MAX_FILE) -> bytes:
    """No symlink or FIFO reads, bounded even if the file changes while reading."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            _need(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "nonregular input file")
            raw = stream.read(limit + 1)
    except OSError as exc:
        raise LaunchError("cannot read required file") from exc
    _need(len(raw) <= limit, "input file bound exceeded")
    return raw


def load_manifest(path: Path) -> dict:
    result = parse_json(read_regular(path, limit=MAX_JSON))
    validate_manifest(result)
    return result


def _identity(value: Any, label: str, *, owner: bool = False, strict: bool = False) -> dict:
    if strict:
        _keys(value, {"id", "login", "type"}, label)
    _need(type(value) is dict, f"missing {label}")
    _id(value.get("id"), label + " id")
    _need(type(value.get("login")) is str and _LOGIN.fullmatch(value["login"]) is not None,
          f"invalid {label} login")
    _need(type(value.get("type")) is str and value["type"] in ({"User", "Organization"} if owner else {"User"}),
          f"invalid {label} type")
    return {key: value[key] for key in ("id", "login", "type")}


def _repository(value: Any, label: str, *, strict: bool = False) -> dict:
    if strict:
        _keys(value, {"id", "name", "full_name", "owner"}, label)
    _need(type(value) is dict, f"missing {label}")
    _id(value.get("id"), label + " id")
    _need(type(value.get("name")) is str and _NAME.fullmatch(value["name"]) is not None,
          f"invalid {label} name")
    owner = _identity(value.get("owner"), label + " owner", owner=True, strict=strict)
    _need(value.get("full_name") == owner["login"] + "/" + value["name"],
          f"inconsistent {label} full name")
    return {"id": value["id"], "name": value["name"], "full_name": value["full_name"], "owner": owner}


def validate_manifest(document: dict) -> dict:
    """Strict launch schema; admission's independent validator owns its contents."""
    _keys(document, {"schema_version", "launch", "admission"}, "manifest")
    _need(type(document["schema_version"]) is int and document["schema_version"] == 1,
          "unsupported manifest schema")
    _need(type(document["admission"]) is dict and bool(document["admission"]),
          "missing admission section")
    launch = _keys(document["launch"], {
        "nonce", "repository", "publisher", "authorized_retrier", "seed_sha", "ref",
        "workflow_path", "workflow_sha256", "source_sha256", "helper_sha256",
    }, "launch")
    nonce = launch["nonce"]
    _need(type(nonce) is str and re.fullmatch(r"[0-9a-f]{32}", nonce) is not None
          and nonce != "0" * 32, "invalid launch nonce")
    _repository(launch["repository"], "manifest repository", strict=True)
    _identity(launch["publisher"], "publisher", strict=True)
    _identity(launch["authorized_retrier"], "authorized retrier", strict=True)
    _sha(launch["seed_sha"], "seed SHA")
    _need(launch["ref"] == "refs/heads/ci/qualify-apparmor-" + nonce, "wrong control ref")
    _need(launch["workflow_path"] == ".github/workflows/qualify-apparmor-" + nonce + ".yml",
          "wrong workflow path")
    for key in ("workflow_sha256", "source_sha256"):
        _sha(launch[key], key, sha256=True)
    hashes = _keys(launch["helper_sha256"], set(HELPER_PATHS), "helper hashes")
    for value in hashes.values():
        _sha(value, "helper hash", sha256=True)
    return launch


def _native_context(launch: dict, context: Mapping[str, str]) -> dict:
    repository, publisher = launch["repository"], launch["publisher"]
    expected = {
        "GITHUB_EVENT_NAME": "push", "GITHUB_REF_TYPE": "branch", "GITHUB_REF": launch["ref"],
        "GITHUB_REPOSITORY": repository["full_name"], "GITHUB_REPOSITORY_OWNER": repository["owner"]["login"],
        "GITHUB_ACTOR": publisher["login"], "GITHUB_TRIGGERING_ACTOR": launch["authorized_retrier"]["login"],
        "GITHUB_WORKFLOW_REF": repository["full_name"] + "/" + launch["workflow_path"] + "@" + launch["ref"],
        "GITHUB_RUN_NUMBER": "1", "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_API_URL": "https://api.github.com",
    }
    for key, value in expected.items():
        _need(type(context.get(key)) is str and context[key] == value, "wrong native " + key)
    for key, value in {
        "GITHUB_REPOSITORY_ID": repository["id"], "GITHUB_REPOSITORY_OWNER_ID": repository["owner"]["id"],
        "GITHUB_ACTOR_ID": publisher["id"],
    }.items():
        _need(_id(context.get(key), key, native=True) == value, "wrong native " + key)
    sha = _sha(context.get("GITHUB_SHA"), "native SHA")
    _need(context.get("GITHUB_WORKFLOW_SHA") == sha, "workflow SHA differs from event SHA")
    job = context.get("GITHUB_JOB")
    _need(type(job) is str and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,99}", job) is not None,
          "invalid native job")
    return {"sha": sha, "run_id": _id(context.get("GITHUB_RUN_ID"), "run id", native=True),
            "run_attempt": _id(context.get("GITHUB_RUN_ATTEMPT"), "run attempt", native=True), "job": job}


def validate_event(launch: dict, context: Mapping[str, str], event: dict, checkout: dict) -> dict:
    native = _native_context(launch, context)
    _need(type(event) is dict, "missing push event")
    for key in ("created", "deleted", "forced"):
        _need(type(event.get(key)) is bool and event[key] is False, "non-transition push " + key)
    _need(event.get("ref") == launch["ref"], "wrong event ref")
    _need(event.get("before") == launch["seed_sha"], "wrong transition seed")
    _need(event.get("after") == native["sha"], "wrong event after SHA")
    _need(_repository(event.get("repository"), "event repository") == launch["repository"],
          "wrong event repository")
    _need(_identity(event.get("sender"), "event sender") == launch["publisher"], "wrong event sender")
    _keys(checkout, {"head", "parents", "source_sha256", "workflow_sha256", "helper_sha256"}, "checkout")
    _need(checkout["head"] == native["sha"], "checkout HEAD differs from event SHA")
    _need(checkout["parents"] == [launch["seed_sha"]], "control commit is not a single seed child")
    _need(checkout["head"] != launch["seed_sha"], "control commit equals seed")
    for key in ("source_sha256", "workflow_sha256", "helper_sha256"):
        _need(checkout[key] == launch[key], "checkout content drift: " + key)
    return native


def attempt_url(launch: dict, native: dict) -> str:
    return ("https://api.github.com/repos/" + launch["repository"]["full_name"]
            + f"/actions/runs/{native['run_id']}/attempts/{native['run_attempt']}")


def validate_attempt(launch: dict, native: dict, attempt: dict) -> dict:
    _need(type(attempt) is dict, "missing run-attempt metadata")
    for key, expected in (("id", native["run_id"]), ("run_attempt", native["run_attempt"]), ("run_number", 1)):
        _need(_id(attempt.get(key), "REST " + key) == expected, "wrong REST " + key)
    workflow_id = _id(attempt.get("workflow_id"), "REST workflow id")
    prefix = "https://api.github.com/repos/" + launch["repository"]["full_name"]
    _need(attempt.get("workflow_url") == prefix + f"/actions/workflows/{workflow_id}", "wrong workflow identity URL")
    _need(attempt.get("url") == prefix + f"/actions/runs/{native['run_id']}", "wrong run identity URL")
    path, ref = launch["workflow_path"], launch["ref"]
    _need(attempt.get("path") in (path, path + "@" + ref, path + "@" + ref.removeprefix("refs/heads/")),
          "wrong REST workflow path/ref")
    _need(attempt.get("event") == "push" and attempt.get("head_branch") == ref.removeprefix("refs/heads/"),
          "wrong REST event/branch")
    _need(attempt.get("head_sha") == native["sha"], "wrong REST head SHA")
    _need(type(attempt.get("head_commit")) is dict and attempt["head_commit"].get("id") == native["sha"],
          "wrong REST head commit")
    _need(attempt.get("status") == "in_progress" and attempt.get("conclusion") is None,
          "run attempt is not live")
    _need(type(attempt.get("pull_requests")) is list and not attempt["pull_requests"],
          "control run has pull requests")
    for key in ("repository", "head_repository"):
        _need(_repository(attempt.get(key), "REST " + key) == launch["repository"], "wrong REST " + key)
        _need(attempt[key].get("private") is False, "run repository is not public")
    _need(_identity(attempt.get("actor"), "REST actor") == launch["publisher"], "wrong REST original actor")
    _need(_identity(attempt.get("triggering_actor"), "REST triggering actor") == launch["authorized_retrier"],
          "unauthorized triggering actor")
    return {"workflow_id": workflow_id, "endpoint": attempt_url(launch, native)}


@contextmanager
def _network_deadline(seconds: float):
    """Linux CI main thread only: bound DNS, TLS, headers and trickling bodies."""
    _need(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0), "network deadline already active")
    previous = signal.getsignal(signal.SIGALRM)

    def expired(signum, frame):
        raise LaunchError("public metadata deadline exceeded")

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def fetch_public_attempt(url: str) -> dict:
    """One exact public HTTPS GET. No proxy, token, redirect or retry fallback."""
    _need(type(url) is str and _ENDPOINT.fullmatch(url) is not None, "invalid metadata endpoint")
    connection = http.client.HTTPSConnection("api.github.com", timeout=10, context=ssl.create_default_context())
    try:
        with _network_deadline(20):
            connection.request("GET", url.removeprefix("https://api.github.com"), headers={
                "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "forge-ci-launch-identity/1", "Accept-Encoding": "identity",
            })
            response = connection.getresponse()
            _need(response.status == 200, "public run-attempt metadata HTTP failure")
            _need(response.getheader("Content-Type", "").split(";", 1)[0].strip() == "application/json",
                  "public metadata is not JSON")
            _need(response.getheader("Content-Encoding", "identity") == "identity", "encoded metadata rejected")
            declared = response.getheader("Content-Length")
            if declared is not None:
                _need(re.fullmatch(r"[0-9]{1,8}", declared) is not None and int(declared) <= MAX_API,
                      "public metadata length bound exceeded")
            raw = response.read(MAX_API + 1)
            _need(len(raw) <= MAX_API, "public metadata byte bound exceeded")
            if declared is not None:
                _need(len(raw) == int(declared), "truncated public metadata")
            return parse_json(raw, limit=MAX_API)
    except (OSError, http.client.HTTPException, ValueError) as exc:
        # Never render request headers or an exception that could contain secrets.
        raise LaunchError("public run-attempt metadata unavailable") from exc
    finally:
        connection.close()


def validate_launch(document: dict, context: Mapping[str, str], event: dict, checkout: dict,
                    *, fetcher: Callable[[str], dict] = fetch_public_attempt) -> dict:
    """Testable gate. A supplied fetcher replaces only the one public API read."""
    launch = validate_manifest(document)
    native = validate_event(launch, context, event, checkout)
    try:
        attempt = fetcher(attempt_url(launch, native))
    except LaunchError:
        raise
    except Exception as exc:  # noqa: BLE001 - all unavailable identity sources must STOP
        raise LaunchError("public run-attempt metadata unavailable") from exc
    live = validate_attempt(launch, native, attempt)
    return {"schema_version": 1, "status": "PASS", "policy_authorized": False,
            "nonce": launch["nonce"], "seed_sha": launch["seed_sha"], "control_sha": native["sha"],
            "repository": launch["repository"], "ref": launch["ref"], "workflow_path": launch["workflow_path"],
            "run_id": native["run_id"], "run_attempt": native["run_attempt"], "run_number": 1,
            "job": native["job"], "publisher": launch["publisher"], "triggering_actor": launch["authorized_retrier"],
            "source_sha256": launch["source_sha256"], "workflow_sha256": launch["workflow_sha256"],
            "helper_sha256": launch["helper_sha256"], "manifest_sha256": hashlib.sha256(canonical_bytes(document)).hexdigest(),
            "metadata_sha256": hashlib.sha256(canonical_bytes(attempt)).hexdigest(), **live}


def _git(repo: Path, *args: str) -> bytes:
    # No inherited Git alternate object locations, config injection or replace refs.
    environment = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LC_ALL": "C",
                   "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_NO_REPLACE_OBJECTS": "1"}
    try:
        process = subprocess.Popen(["/usr/bin/git", "--no-replace-objects", "-C", str(repo), *args],
                                   env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=True)
    except OSError as exc:
        raise LaunchError("checkout metadata unavailable") from exc
    chunks = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + 20
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                remaining = deadline - time.monotonic()
                _need(remaining > 0, "checkout metadata deadline exceeded")
                for key, _ in selector.select(min(remaining, 0.1)):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    chunks[key.data].extend(chunk)
                    limit = MAX_TREE if key.data == "stdout" else 65536
                    _need(len(chunks[key.data]) <= limit, "checkout metadata byte bound exceeded")
            process.wait(timeout=max(0.001, deadline - time.monotonic()))
        _need(process.returncode == 0, "checkout metadata command failed")
        return bytes(chunks["stdout"])
    except subprocess.TimeoutExpired as exc:
        raise LaunchError("checkout metadata deadline exceeded") from exc
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()


def _tree(repo: Path, revision: str) -> dict[str, tuple[str, str]]:
    raw = _git(repo, "ls-tree", "-r", "-z", "--full-tree", revision)
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


def inspect_checkout(repo: Path, document: dict, *, manifest_path: Path | None = None) -> dict:
    launch = validate_manifest(document)
    repo = repo.resolve(strict=True)
    expected_manifest = repo / MANIFEST_PATH
    if manifest_path is not None:
        _need(manifest_path.absolute() == expected_manifest, "manifest is not the fixed reviewed path")
    head = _git(repo, "rev-parse", "--verify", "HEAD").decode("ascii").strip()
    _sha(head, "checkout HEAD")
    commit = _git(repo, "cat-file", "commit", head)
    headers = commit.split(b"\n\n", 1)[0].splitlines()
    parents = [line.removeprefix(b"parent ").decode("ascii") for line in headers if line.startswith(b"parent ")]
    _need(parents == [launch["seed_sha"]], "control commit is not a single seed child")
    seed_tree, tree = _tree(repo, launch["seed_sha"]), _tree(repo, head)
    controls = {MANIFEST_PATH, launch["workflow_path"]}
    _need(launch["workflow_path"] not in seed_tree and MANIFEST_PATH not in seed_tree,
          "seed already contains this control launch")
    _need(controls | HELPER_PATHS <= set(tree), "control/helper files are missing")
    _need(HELPER_PATHS <= set(seed_tree), "reviewed helpers are missing from seed")
    _need({key: value for key, value in seed_tree.items() if key not in controls}
          == {key: value for key, value in tree.items() if key not in controls},
          "non-control source differs from seed")
    records, hashes, total = [], {}, 0
    deadline = time.monotonic() + 60
    for relative, (mode, oid) in sorted(tree.items()):
        _need(time.monotonic() < deadline, "checkout inspection deadline exceeded")
        target = repo / relative
        _need(target.parent.resolve(strict=True).is_relative_to(repo), "checkout parent symlink escape")
        info = target.lstat()
        if mode == "120000":
            _need(stat.S_ISLNK(info.st_mode) and relative not in controls | HELPER_PATHS, "invalid control/source symlink")
            raw = os.readlink(target).encode("utf-8")
        else:
            _need(stat.S_ISREG(info.st_mode), "nonregular checkout file")
            _need(bool(info.st_mode & 0o111) == (mode == "100755"), "checkout executable mode drift")
            raw = read_regular(target)
        total += len(raw)
        _need(total <= MAX_TOTAL, "checkout byte bound exceeded")
        git_blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw, usedforsecurity=False).hexdigest()
        _need(git_blob == oid, "checkout bytes differ from committed tree")
        sha = hashlib.sha256(raw).hexdigest()
        if relative in controls | HELPER_PATHS:
            hashes[relative] = sha
        if relative not in controls:
            records.append({"path": relative, "mode": mode, "sha256": sha})
    actual_manifest = parse_json(read_regular(expected_manifest, limit=MAX_JSON))
    _need(actual_manifest == document, "manifest differs from checked-out manifest")
    return {"head": head, "parents": parents, "source_sha256": source_digest(records),
            "workflow_sha256": hashes[launch["workflow_path"]],
            "helper_sha256": {path: hashes[path] for path in sorted(HELPER_PATHS)}}


def write_receipt(path: Path, report: dict) -> None:
    data = canonical_bytes(report) + b"\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args(argv)
    fresh_output = False
    try:
        _need(not args.output.exists() and not args.output.is_symlink(), "receipt must be fresh")
        fresh_output = True
        document = load_manifest(args.manifest)
        event_path = os.environ.get("GITHUB_EVENT_PATH")
        _need(type(event_path) is str and bool(event_path), "missing native event path")
        event = parse_json(read_regular(Path(event_path), limit=MAX_API), limit=MAX_API)
        checkout = inspect_checkout(args.repo, document, manifest_path=args.manifest)
        report = validate_launch(document, os.environ, event, checkout)
        write_receipt(args.output, report)
    except (LaunchError, OSError, UnicodeError) as exc:
        error = str(exc)
        if fresh_output:
            try:
                write_receipt(args.output, {"schema_version": 1, "status": "STOP",
                                          "policy_authorized": False, "errors": [error]})
            except OSError:
                print("STOP: failure receipt could not be written")
        print("STOP: " + error)
        return 1
    print("PASS: launch identity verified; policy admission remains separate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
