"""Read-only, bounded evidence collection; success is NEVER policy admission.

Run as the ordinary runner, after the existing dependency installation.  Privilege
is used only for fixed, read-only system utilities.  This module cannot compile,
load, replace, unload, or install anything.  Every attachment requires a later
human review, bound to the exported semantic inventory digest.
"""
from __future__ import annotations

import argparse
import ast
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import time
from typing import Any

POLICY_ROOT = "/sys/kernel/security/apparmor/policy"
APPARMOR_ROOT = "/sys/kernel/security/apparmor"
PROFILE_ROOT = "/etc/apparmor.d"
VENDOR_PROFILE_SHA256 = "11d39094f044f0cda0febb3ad517b830301da6b2ce929664af09ee9e4dd264f9"
VENDOR_PACKAGE_SHA256 = "4e7d728322f899a7a06e71bedd4f4bd1f20c21f0b3361120f34cf5c0feec849e"
VENDOR_VERSION = "4.0.1really4.0.1-0ubuntu0.24.04.8"
DEFAULT_SNAPSHOTS = {
    "source": "src", "tests": "tests", "helpers": ".github/scripts",
    "helper_tests": ".github/tests", "workflows": ".github/workflows",
    "project": "pyproject.toml",
}
MAX_READ = 4 * 1024 * 1024
MAX_COMMAND = 32 * 1024 * 1024
MAX_FILES = 20000
MAX_TOTAL = 128 * 1024 * 1024


class FactError(RuntimeError):
    """A missing or ambiguous observation; never evidence of absence."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def decode(data: bytes, description: str) -> str:
    try:
        return data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise FactError(f"non-UTF-8 {description}") from exc


class Evidence:
    def __init__(self, output: Path):
        output.mkdir(parents=True, exist_ok=True)
        if list(output.iterdir()):
            raise FactError("output must be a fresh empty directory")
        self.output = output
        (output / "raw").mkdir()
        self.sources: list[dict[str, Any]] = []
        self.total = 0

    def save(self, source: str, data: bytes) -> dict[str, Any]:
        self.total += len(data)
        if self.total > MAX_TOTAL or len(self.sources) >= MAX_FILES:
            raise FactError("aggregate evidence limit exceeded")
        sha = hashlib.sha256(data).hexdigest()
        path = f"raw/{sha}"
        (self.output / path).write_bytes(data)
        item = {"source": source, "sha256": sha, "bytes": len(data), "artifact": path}
        self.sources.append(item)
        return item

    def json(self, filename: str, value: Any) -> None:
        (self.output / filename).write_bytes(canonical_bytes(value) + b"\n")


class Commands:
    """Bound time AND streamed output; no shell, stdin, or inherited stdin."""
    def __init__(self, evidence: Evidence, total_seconds: float = 300):
        self.evidence = evidence
        self.deadline = time.monotonic() + total_seconds
        self.records: list[dict[str, Any]] = []

    def run(self, argv: list[str], *, timeout: float = 10,
            limit: int = MAX_COMMAND, env: dict[str, str] | None = None,
            cwd: Path | None = None) -> bytes:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise FactError("total collection deadline expired")
        timeout = min(timeout, remaining)
        record: dict[str, Any] = {"argv": argv, "timeout_seconds": timeout}
        self.records.append(record)
        chunks: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
        failure = None
        started = time.monotonic()
        try:
            process = subprocess.Popen(  # noqa: S603 - explicit argv, no shell
                argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, start_new_session=True, env=env, cwd=cwd,
            )
        except OSError as exc:
            record["error"] = str(exc)
            raise FactError(f"cannot execute {argv[0]}: {exc}") from exc
        try:
            with selectors.DefaultSelector() as selector:
                for label, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
                    selector.register(stream, selectors.EVENT_READ, label)
                count = 0
                while selector.get_map():
                    wait = timeout - (time.monotonic() - started)
                    if wait <= 0:
                        failure = "command deadline exceeded"
                        break
                    for key, _ in selector.select(min(wait, 0.1)):
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        count += len(chunk)
                        if count > limit:
                            failure = "command output limit exceeded"
                            break
                        chunks[key.data].extend(chunk)
                    if failure:
                        break
                if not failure:
                    try:
                        process.wait(timeout=max(0.001, timeout - (time.monotonic() - started)))
                    except subprocess.TimeoutExpired:
                        failure = "command deadline exceeded"
        finally:
            if process.poll() is None or failure:
                # Also terminate descendants that retain a pipe after the leader exits.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()
            record["returncode"] = process.returncode
            record["duration_seconds"] = round(time.monotonic() - started, 6)
            for key, data in chunks.items():
                record[key] = self.evidence.save(f"command:{len(self.records)}:{key}", bytes(data))
        if failure or process.returncode != 0:
            record["error"] = failure or f"exit {process.returncode}"
            raise FactError(f"{argv[0]}: {record['error']}")
        return bytes(chunks["stdout"])


class Reader:
    """Auditable readers. Never run repository-controlled code as root."""
    def __init__(self, commands: Commands, evidence: Evidence):
        self.commands, self.evidence = commands, evidence

    @staticmethod
    def privileged(path: str) -> bool:
        return (path == APPARMOR_ROOT or path.startswith(APPARMOR_ROOT + "/")
                or path == PROFILE_ROOT or path.startswith(PROFILE_ROOT + "/")
                or path == "/etc/apparmor/parser.conf")

    @staticmethod
    def prefix() -> list[str]:
        return ["/usr/bin/sudo", "-n", "--", "/usr/bin/timeout", "--signal=KILL", "5s"]

    def read(self, path: str, *, limit: int = MAX_READ) -> bytes:
        if time.monotonic() > self.commands.deadline:
            raise FactError("total collection deadline expired")
        revision = path.startswith(POLICY_ROOT + "/") and path.endswith("/revision")
        try:
            if revision:
                # Revision interfaces can remain open awaiting policy changes.
                # Read exactly one nonblocking record, never wait for their EOF.
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
                try:
                    data = os.read(fd, 129)
                finally:
                    os.close(fd)
            else:
                with open(path, "rb") as stream:
                    data = stream.read(limit + 1)
        except PermissionError as exc:
            if not self.privileged(path):
                raise FactError(f"unreadable {path}: {exc}") from exc
            utility = (["/usr/bin/dd", "if=" + path, "iflag=nonblock", "bs=129", "count=1", "status=none"]
                       if revision else ["/usr/bin/head", "-c", str(limit + 1), "--", path])
            data = self.commands.run(self.prefix() + utility, timeout=7, limit=limit + 65536)
        except OSError as exc:
            raise FactError(f"unreadable {path}: {exc}") from exc
        if revision and not re.fullmatch(rb"[0-9]+\n", data):
            raise FactError(f"missing/truncated namespace revision: {path}")
        if len(data) > limit:
            raise FactError(f"read limit exceeded: {path}")
        self.evidence.save(path, data)
        return data

    def tree(self, path: str) -> dict[str, dict[str, Any]]:
        # Kernel policy is a magic command-line symlink. Follow exactly this root
        # with -H, never symlinked descendants; other inventory roots use -P.
        traversal = "-H" if path == POLICY_ROOT else "-P"
        argv = ["/usr/bin/find", traversal, path, "-printf", "%y\\0%p\\0%l\\0%m\\0%U\\0%G\\0%s\\0"]
        if self.privileged(path):
            argv = self.prefix() + argv
        raw = self.commands.run(argv, timeout=7, limit=MAX_COMMAND)
        parts = raw.split(b"\0")
        if not parts or parts.pop() != b"" or len(parts) % 7:
            raise FactError(f"truncated tree inventory: {path}")
        result = {}
        for offset in range(0, len(parts), 7):
            kind, name, target, mode, uid, gid, size = map(
                lambda value: decode(value, "filesystem inventory"), parts[offset:offset + 7])
            if name in result or not (name == path or name.startswith(path + "/")):
                raise FactError(f"duplicate or escaped filesystem entry: {name}")
            if len(PurePosixPath(name).parts) > 48 or len(result) >= MAX_FILES:
                raise FactError("filesystem inventory bound exceeded")
            try:
                result[name] = {"type": kind, "target": target, "mode": int(mode, 8),
                                "uid": int(uid), "gid": int(gid), "size": int(size)}
            except ValueError as exc:
                raise FactError(f"malformed filesystem metadata: {name}") from exc
        if result.get(path, {}).get("type") != "d":
            raise FactError(f"missing authoritative directory: {path}")
        return result


def _children(tree: dict[str, dict], path: str) -> list[str]:
    return sorted(p for p in tree if str(PurePosixPath(p).parent) == path)


def _field(reader: Reader, path: str) -> str:
    value = decode(reader.read(path, limit=65536), path).rstrip("\n")
    if not value or "\n" in value or "\x00" in value:
        raise FactError(f"missing or ambiguous scalar: {path}")
    return value


def parse_loaded_profiles(raw: bytes) -> list[dict[str, str]]:
    items = []
    seen = set()
    for line in decode(raw, "loaded profile listing").splitlines():
        match = re.fullmatch(r"(.+) \((enforce|complain|unconfined|kill|prompt|mixed)\)", line)
        if not match or match[1] in seen:
            raise FactError("malformed or duplicate loaded profile listing")
        seen.add(match[1])
        items.append({"qualified_name": match[1], "mode": match[2]})
    return sorted(items, key=lambda item: item["qualified_name"])


def semantic_inventory(namespaces: list[str], profiles: list[dict[str, Any]]) -> dict[str, Any]:
    """No kernel directory IDs, inodes, boot IDs, collection paths, or ordering."""
    canonical = []
    seen = set()
    if len(namespaces) != len(set(namespaces)) or "" not in namespaces:
        raise FactError("duplicate or missing root policy namespace")
    for profile in profiles:
        key = (profile["namespace"], profile["name"])
        if key in seen or key[0] not in namespaces:
            raise FactError("duplicate profile identity or unknown namespace")
        seen.add(key)
        if profile["attachment"].strip().lower() in {"<unknown>", "unknown", "<opaque>", ""}:
            raise FactError(f"opaque attachment for {key}")
        canonical.append({key: profile[key] for key in (
            "namespace", "name", "mode", "attachment", "metadata")})
    canonical.sort(key=lambda item: (item["namespace"], item["name"]))
    return {"schema": 1, "namespaces": sorted(namespaces), "profiles": canonical}


def collect_kernel_scope(reader: Reader) -> dict[str, Any]:
    # Both policy's magic symlink and the loaded-profile listing are relative
    # to the task's AppArmor namespace. An unconfined label alone is insufficient.
    values = {name: _field(reader, APPARMOR_ROOT + "/." + name)
              for name in ("ns_level", "ns_name", "stacked", "ns_stacked")}
    if values["ns_level"] != "0" or values["stacked"] != "no" or values["ns_stacked"] != "no":
        raise FactError("AppArmor inventory is not root-namespace, unstacked host scope")
    return values


def collect_kernel_inventory(reader: Reader) -> dict[str, Any]:
    scope = collect_kernel_scope(reader)
    listing_before = reader.read(APPARMOR_ROOT + "/profiles")
    tree = reader.tree(POLICY_ROOT)
    profiles: list[dict[str, Any]] = []
    namespaces: list[str] = []
    revisions: dict[str, bytes] = {}
    consumed: set[str] = set()

    def metadata_bytes(path: str, visited: set[str] | None = None) -> bytes:
        visited = set() if visited is None else visited
        if path in visited or len(visited) > 8:
            raise FactError(f"cyclic kernel metadata link: {path}")
        visited.add(path)
        entry = tree.get(path)
        if not entry:
            raise FactError(f"missing kernel metadata: {path}")
        if entry["type"] == "l":
            target = entry["target"]
            resolved = os.path.normpath(os.path.join(os.path.dirname(path), target))
            if not resolved.startswith(POLICY_ROOT + "/"):
                raise FactError(f"escaped kernel metadata link: {path}")
            return metadata_bytes(resolved, visited)
        if entry["type"] != "f":
            raise FactError(f"unsupported kernel metadata type: {path}")
        consumed.add(path)
        return reader.read(path)

    def walk_profiles(directory: str, namespace: str, parent_name: str = "") -> None:
        if tree.get(directory, {}).get("type") != "d":
            raise FactError(f"missing profile directory: {directory}")
        for path in _children(tree, directory):
            if tree[path]["type"] != "d":
                raise FactError(f"unexpected profile tree entry: {path}")
            values = {}
            for field in ("name", "mode", "attach"):
                if tree.get(path + "/" + field, {}).get("type") != "f":
                    raise FactError(f"missing authoritative profile {field}: {path}")
                values[field] = _field(reader, path + "/" + field)
                consumed.add(path + "/" + field)
            if values["mode"] not in {"enforce", "complain", "unconfined", "kill", "prompt", "mixed"}:
                raise FactError(f"unknown profile mode: {values['mode']}")
            metadata = {}
            for leaf in sorted(tree):
                if not leaf.startswith(path + "/"):
                    continue
                rel = leaf[len(path) + 1:]
                if rel.split("/")[0] == "profiles" or rel in {"name", "mode", "attach"}:
                    continue
                if tree[leaf]["type"] == "d":
                    continue
                data = metadata_bytes(leaf)
                # Preserve conditional/xattr/alias metadata verbatim, when exposed.
                metadata[rel] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
                if len(data) <= 65536 and b"\x00" not in data:
                    try:
                        metadata[rel]["text"] = data.decode("utf-8")
                    except UnicodeDecodeError:
                        pass
            # The kernel name file exposes base.name, not base.hname. Rebuild
            # the hierarchy from the authoritative nested profiles directories.
            local_name = values["name"]
            if "//" in local_name:
                raise FactError(f"ambiguous local kernel profile name: {local_name}")
            full_name = parent_name + "//" + local_name if parent_name else local_name
            profile = {"namespace": namespace, "name": full_name, "local_name": local_name,
                       "mode": values["mode"], "attachment": values["attach"],
                       "metadata": metadata, "kernel_path": path}
            profiles.append(profile)
            nested = path + "/profiles"
            if nested in tree:
                walk_profiles(nested, namespace, full_name)

    def walk_namespace(path: str, namespace: str) -> None:
        namespaces.append(namespace)
        revision = path + "/revision"
        if tree.get(revision, {}).get("type") != "f":
            raise FactError(f"missing policy revision: {path}")
        revisions[revision] = reader.read(revision)
        walk_profiles(path + "/profiles", namespace)
        nested = path + "/namespaces"
        if tree.get(nested, {}).get("type") != "d":
            raise FactError(f"missing namespace inventory: {path}")
        for child in _children(tree, nested):
            if tree[child]["type"] != "d":
                raise FactError(f"unexpected namespace entry: {child}")
            name = PurePosixPath(child).name
            if ":" in name or "\n" in name:
                raise FactError("ambiguous namespace identity")
            walk_namespace(child, namespace + "//" + name if namespace else name)

    walk_namespace(POLICY_ROOT, "")
    # Detect unparsed profile directories, including unexpected hierarchy shapes.
    for path in tree:
        if PurePosixPath(path).name == "attach" and path not in consumed:
            raise FactError(f"unaccounted attachment: {path}")
    listing_after = reader.read(APPARMOR_ROOT + "/profiles")
    if listing_before != listing_after or tree != reader.tree(POLICY_ROOT):
        raise FactError("kernel policy inventory changed during collection")
    for path, before in revisions.items():
        if before != reader.read(path):
            raise FactError("kernel namespace revision changed during collection")
    if scope != collect_kernel_scope(reader):
        raise FactError("AppArmor namespace/stack scope changed during inventory")
    semantic = semantic_inventory(namespaces, profiles)
    semantic["scope"] = scope
    actual = sorted((p["qualified_name"], p["mode"]) for p in parse_loaded_profiles(listing_before))
    expected = sorted(((f":{p['namespace']}://" if p["namespace"] else "") + p["name"], p["mode"])
                      for p in profiles)
    if actual != expected:
        raise FactError("authoritative profile tree and loaded-profile listing disagree")
    conflicts = [p["name"] for p in profiles if p["name"].split("//")[0] in {"bwrap", "unpriv_bwrap"}]
    return {"semantic": semantic, "semantic_sha256": digest(semantic), "scope": scope, "raw_profiles": profiles,
            "raw_tree": tree, "loaded_profiles": parse_loaded_profiles(listing_before),
            "conflicting_names": conflicts, "attachment_review": "REQUIRED",
            "reviewed_inventory_sha256": None,
            "conditional_semantics": "Raw metadata exported; no attachment expression has been approved."}


def source_snapshot(repo: Path, relative: str, reader: Reader) -> dict[str, Any]:
    target = repo / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts or not target.exists():
        raise FactError(f"missing or escaped snapshot binding: {relative}")
    output = reader.commands.run(["/usr/bin/git", "ls-files", "-z", "--cached", "--others",
                                  "--exclude-standard", "--", relative], cwd=repo)
    paths = [decode(part, "git file list") for part in output.split(b"\0") if part]
    if not paths or len(paths) != len(set(paths)) or len(paths) > MAX_FILES:
        raise FactError(f"empty/duplicate/oversize source snapshot: {relative}")
    items = []
    for path in sorted(paths):
        resolved = (repo / path).resolve(strict=True)
        if not resolved.is_relative_to(repo):
            raise FactError(f"source symlink escape: {path}")
        info = (repo / path).lstat()
        sha = hash_file(repo / path)
        items.append({"path": path, "sha256": sha,
                      "mode": stat.S_IMODE(info.st_mode),
                      "symlink": os.readlink(repo / path) if stat.S_ISLNK(info.st_mode) else None})
    return {"binding": relative, "sha256": digest(items), "files": items}


def executable_identity(path: str, reader: Reader, *, require_root: bool = True) -> dict[str, Any]:
    try:
        canonical = Path(path).resolve(strict=True)
        info = canonical.stat()
        if not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o111:
            raise FactError(f"not a regular executable: {path}")
        with canonical.open("rb") as stream:
            first = stream.read(4096)
        # Hash from offset zero; binaries may exceed the bounded text-input limit.
        if info.st_size > 128 * 1024 * 1024:
            raise FactError(f"executable size bound exceeded: {path}")
        with canonical.open("rb") as stream:
            sha = hashlib.file_digest(stream, "sha256").hexdigest()
        try:
            capability = os.getxattr(canonical, "security.capability")
        except OSError as exc:
            if exc.errno != errno.ENODATA:
                raise FactError(f"unknown file capabilities: {path}: {exc}") from exc
            capability = b""
    except OSError as exc:
        raise FactError(f"unreadable executable {path}: {exc}") from exc
    current = Path(path)
    # resolve() handles merged-/usr directory symlinks; record each prefix too.
    chain = [{"path": str(prefix), "target": os.readlink(prefix)}
             for prefix in (current, *current.parents) if prefix.is_symlink()]
    result = {"requested": path, "canonical": str(canonical), "sha256": sha,
              "mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid,
              "bytes": info.st_size, "symlinks": chain,
              "file_capabilities_hex": capability.hex(), "elf": first.startswith(b"\x7fELF")}
    if first.startswith(b"#!"):
        line = decode(first.split(b"\n", 1)[0][2:], "shebang").strip()
        result["shebang"] = line
        raise FactError(f"unreviewed executable shebang closure: {path}: {line}")
    if not result["elf"] or capability or info.st_mode & 0o6022 or (require_root and info.st_uid != 0):
        raise FactError(f"unexpected executable identity/privilege: {path}")
    return result


def collect_executables(reader: Reader) -> dict[str, Any]:
    paths = ["/usr/bin/bwrap", "/usr/bin/true", "/bin/true", "/bin/sh", "/usr/bin/date",
             "/usr/bin/python3", "/bin/sleep", "/usr/bin/bash"]
    for name in ("bwrap", "ip", "bash", "date", "sleep"):
        resolved = shutil.which(name, path="/usr/bin:/bin")
        if not resolved:
            raise FactError(f"missing production-PATH executable: {name}")
        paths.append(resolved)
    result = {path: executable_identity(path, reader) for path in sorted(set(paths))}
    if result["/usr/bin/bwrap"]["canonical"] != "/usr/bin/bwrap":
        raise FactError("bwrap canonical identity is not /usr/bin/bwrap")
    if result["/bin/sh"]["canonical"] != "/usr/bin/dash":
        raise FactError("fixture shell is not the reviewed dash")
    return {"production_path": "/usr/bin:/bin", "executables": result}


GUARDS = {
    "javascript": ("test_mutation_js_real.py", "requires", {
        "NODE_MODULES": "/home/houminxi/code/hermes/cache/scratch/js-qual/node_modules",
        "NODE": "/home/houminxi/.local/bin/node"},
        "not (os.path.isdir(CGROUP_ROOT) and os.path.isdir(NODE_MODULES) and os.path.isfile(NODE))"),
    "css": ("test_mutation_css_real.py", "requires", {
        "NODE": "/home/houminxi/code/hermes/node/bin",
        "PLAYWRIGHT": "/home/houminxi/code/hermes/cache/scratch/css-tools/node_modules",
        "CHROME": "/opt/google/chrome"},
        "not (os.path.isdir(CGROUP_ROOT) and os.path.isdir(NODE) and os.path.isdir(PLAYWRIGHT) and os.path.isdir(CHROME))"),
    "rust": ("test_mutation_rust_real.py", "requires", {
        "CARGO_MUTANTS": "/home/houminxi/code/hermes/cache/scratch/cargo-tools/bin/cargo-mutants"},
        "not (os.path.isdir(CGROUP_ROOT) and os.path.isfile(CARGO_MUTANTS))"),
    "go": ("test_mutation_go_real.py", "requires_isolation", {
        "GREMLINS": "/home/houminxi/code/hermes/cache/scratch/go-tools"},
        'not os.path.isdir(CGROUP_ROOT) or not os.path.isfile(GREMLINS + "/gremlins")'),
    "python": ("test_mutation_pyadapter_real.py", None, {}, "not os.path.isdir(CGROUP_ROOT)"),
}


def guard_path(path: str, kind: str) -> dict[str, Any]:
    try:
        info = os.stat(path)
    except FileNotFoundError:
        return {"path": path, "kind": kind, "exists": False, "matches": False}
    except OSError as exc:
        raise FactError(f"unknown adapter eligibility: {path}: {exc}") from exc
    match = stat.S_ISDIR(info.st_mode) if kind == "isdir" else stat.S_ISREG(info.st_mode)
    return {"path": path, "kind": kind, "exists": True, "matches": match,
            "mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid}


def collect_guards(repo: Path, reader: Reader, *, uid: int | None = None) -> dict[str, Any]:
    uid = os.getuid() if uid is None else uid
    results = {}
    for name, (filename, variable, constants, expected) in GUARDS.items():
        source = reader.read(str(repo / "tests" / filename))
        module = ast.parse(source, filename)
        assignments = {node.targets[0].id: node.value for node in module.body
                       if isinstance(node, ast.Assign) and len(node.targets) == 1
                       and isinstance(node.targets[0], ast.Name)}
        values = dict(constants)
        values["CGROUP_ROOT"] = ("/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service" if name == "python"
                                 else f"/sys/fs/cgroup/user.slice/user-{uid}.slice/user@{uid}.service")
        for key, value in constants.items():
            if not isinstance(assignments.get(key), ast.Constant) or assignments[key].value != value:
                raise FactError(f"natural {name} adapter guard path changed: {key}")
        cgroup = assignments.get("CGROUP_ROOT")
        if name == "python":
            if not isinstance(cgroup, ast.Constant) or cgroup.value != values["CGROUP_ROOT"]:
                raise FactError("Python-real fixed user-1000 guard changed")
        else:
            reference = ast.parse('"/sys/fs/cgroup/user.slice/user-%d.slice/user@%d.service" % (os.getuid(), os.getuid())', mode="eval").body
            if ast.dump(cgroup) != ast.dump(reference):
                raise FactError(f"natural {name} cgroup guard changed")
        if variable:
            candidates = [assignments.get(variable)]
        else:
            candidates = [decorator for node in module.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                          for decorator in node.decorator_list if isinstance(decorator, ast.Call)
                          and ast.unparse(decorator.func) == "pytest.mark.skipif"]
        if len(candidates) != 1 or not isinstance(candidates[0], ast.Call) or not candidates[0].args:
            raise FactError(f"ambiguous {name} availability guard")
        call = candidates[0]
        if ast.unparse(call.func) != "pytest.mark.skipif":
            raise FactError(f"changed {name} availability guard")
        predicate = call.args[0]
        if ast.dump(predicate) != ast.dump(ast.parse(expected, mode="eval").body):
            raise FactError(f"natural {name} availability predicate changed")
        observations = []

        def evaluate(node: ast.AST, values=values, observations=observations, name=name) -> Any:
            if isinstance(node, ast.Constant):
                return node.value
            if isinstance(node, ast.Name) and node.id in values:
                return values[node.id]
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
                return evaluate(node.left) + evaluate(node.right)
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
                return not evaluate(node.operand)
            if isinstance(node, ast.BoolOp):
                children = [evaluate(child) for child in node.values]  # inspect every guard input
                return all(children) if isinstance(node.op, ast.And) else any(children)
            if isinstance(node, ast.Call) and ast.unparse(node.func) in {"os.path.isdir", "os.path.isfile"}:
                observation = guard_path(evaluate(node.args[0]), node.func.attr)
                observations.append(observation)
                return observation["matches"]
            raise FactError(f"unsupported guard expression: {name}")
        skipped = evaluate(predicate)
        results[name] = {"source": "tests/" + filename, "source_sha256": hashlib.sha256(source).hexdigest(),
                         "predicate": ast.unparse(predicate), "inputs": observations,
                         "naturally_eligible": not skipped}
    return {"adapters": results, "unexpectedly_eligible": sorted(name for name, entry in results.items()
                                                                if entry["naturally_eligible"])}


_INCLUDE = re.compile(r'^\s*(?:#\s*)?(include|abi)\s+(?:(if\s+exists)\s+)?(?:<([^>]+)>|"([^"]+)")\s*,?\s*(?:#.*)?$')


def collect_policy_inputs(reader: Reader, vendor_dir: Path | None) -> dict[str, Any]:
    tree = reader.tree(PROFILE_ROOT)
    parser_conf = reader.read("/etc/apparmor/parser.conf")
    relevant = {"bwrap", "unpriv_bwrap", "bwrap-userns-restrict"}
    forbidden = []
    for path, entry in tree.items():
        relative = path[len(PROFILE_ROOT) + 1:]
        parts = PurePosixPath(relative).parts
        if parts and parts[0] == "local" and len(parts) > 1 and parts[1] in relevant:
            forbidden.append(path)
        if parts and parts[0] in {"disable", "force-complain"} and len(parts) > 1:
            if any(name in relevant for name in parts[1:]) or PurePosixPath(entry["target"]).name in relevant:
                forbidden.append(path)
    closure: dict[str, Any] = {}
    absent_optional = []
    visiting = set()

    def include(relative: str, optional: bool = False) -> None:
        if (relative.startswith("/") or ".." in PurePosixPath(relative).parts
                or any(character in relative for character in "@*?{}[]\x00")):
            raise FactError(f"unresolved/escaped policy include: {relative}")
        path = PROFILE_ROOT + "/" + relative
        if relative in visiting:
            raise FactError(f"cyclic policy include: {relative}")
        if relative in closure:
            return
        entry = tree.get(path)
        if entry is None:
            if optional:
                absent_optional.append(relative)
                return
            raise FactError(f"missing policy include: {relative}")
        if entry["type"] == "l":
            raise FactError(f"symlinked policy include requires review: {relative}")
        if entry["type"] == "d":
            visiting.add(relative)
            members = []
            for child in _children(tree, path):
                name = PurePosixPath(child).name
                # These are the parser's conventional ignored backup/control inputs;
                # rather than reproduce that filter, inventory every member.
                members.append(name)
                include(relative + "/" + name)
            visiting.remove(relative)
            closure[relative] = {"type": "directory", "members": members}
            return
        if entry["type"] != "f":
            raise FactError(f"unsupported policy input type: {relative}")
        visiting.add(relative)
        data = reader.read(path)
        text = decode(data, path)
        inputs = []
        for line in text.splitlines():
            if not re.match(r'^\s*(?:#\s*include\b|include\b|abi\b)', line):
                continue
            match = _INCLUDE.fullmatch(line)
            if not match:
                raise FactError(f"unparsed include directive: {relative}: {line}")
            target = match[3] or match[4]
            inputs.append({"path": target, "optional": bool(match[2]), "kind": match[1]})
            include(target, bool(match[2]))
        record = {"type": "file", "sha256": hashlib.sha256(data).hexdigest(),
                  "bytes": len(data), "metadata": entry, "includes": inputs}
        if vendor_dir:
            counterpart = vendor_dir / "apparmor" / "etc/apparmor.d" / relative
            if not counterpart.is_file() or counterpart.is_symlink() or counterpart.read_bytes() != data:
                raise FactError(f"policy include differs from authenticated apparmor package: {relative}")
            record["matches_vendor_package"] = True
        closure[relative] = record
        visiting.remove(relative)

    include("abi/4.0")
    include("tunables/global")
    # The vendor's two optional local hooks are known and must be absent.
    absent_optional.extend(relative for relative in ("local/bwrap-userns-restrict", "local/unpriv_bwrap")
                           if PROFILE_ROOT + "/" + relative not in tree)
    result = {"filesystem_inventory": tree, "include_closure": closure,
              "include_closure_sha256": digest(closure), "absent_optional": sorted(set(absent_optional)),
              "forbidden_overrides": sorted(set(forbidden)),
              "parser_conf": {"sha256": hashlib.sha256(parser_conf).hexdigest(),
                              "text": decode(parser_conf, "parser.conf")},
              "vendor_comparison": "not supplied" if vendor_dir is None else "matched include closure"}
    if vendor_dir:
        profile = vendor_dir / "apparmor-profiles/usr/share/apparmor/extra-profiles/bwrap-userns-restrict"
        data = reader.read(str(profile))
        if len(data) != 1936 or hashlib.sha256(data).hexdigest() != VENDOR_PROFILE_SHA256:
            raise FactError("vendor profile does not match pinned .8 member")
        result["vendor_profile"] = {"sha256": VENDOR_PROFILE_SHA256, "bytes": len(data),
                                    "package_version": VENDOR_VERSION}
        packages = {}
        for package in sorted(vendor_dir.glob("*.deb")):
            sha = hash_file(package)
            packages[package.name] = sha
            if package.name.startswith("apparmor-profiles_") and sha != VENDOR_PACKAGE_SHA256:
                raise FactError("apparmor-profiles archive does not match pinned .8 package")
        result["package_archive_hashes"] = packages
        if VENDOR_PACKAGE_SHA256 not in packages.values():
            raise FactError("pinned apparmor-profiles .deb is absent from vendor-dir")
    return result


def hash_file(path: Path, max_bytes: int = 256 * 1024 * 1024) -> str:
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
        raise FactError(f"file size/type bound exceeded: {path}")
    with path.open("rb") as stream:
        sha = hashlib.sha256()
        total = 0
        while chunk := stream.read(1024 * 1024):
            total += len(chunk)
            if total > max_bytes:
                raise FactError(f"file grew beyond bound: {path}")
            sha.update(chunk)
    if total != info.st_size:
        raise FactError(f"file changed size during hashing: {path}")
    return sha.hexdigest()


def collect_host(reader: Reader) -> dict[str, Any]:
    status = decode(reader.read("/proc/self/status"), "caller status")
    fields = {}
    required = {"Uid", "Gid", "CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb", "NoNewPrivs", "Seccomp"}
    for line in status.splitlines():
        name, _, value = line.partition(":")
        if name in required:
            if name in fields:
                raise FactError("duplicate caller status field")
            fields[name] = value.strip()
    if set(fields) != required:
        raise FactError("missing caller status/capability field")
    label = _field(reader, "/proc/self/attr/current")
    if label != "unconfined":
        raise FactError(f"unexpected ordinary-runner security label: {label}")
    try:
        uids = [int(value) for value in fields["Uid"].split()]
        gids = [int(value) for value in fields["Gid"].split()]
        if len(uids) != 4 or len(gids) != 4 or len(set(uids)) != 1 or len(set(gids)) != 1:
            raise FactError("mismatching real/effective/saved/filesystem caller IDs")
        if uids[0] == 0 or gids[0] == 0 or uids[0] != os.getuid() or gids[0] != os.getgid():
            raise FactError("collector must remain the ordinary non-root caller")
        if any(int(fields[name], 16) for name in ("CapInh", "CapPrm", "CapEff", "CapAmb")):
            raise FactError("caller has capability grants")
    except ValueError as exc:
        raise FactError("malformed caller identity/capabilities") from exc
    environment_names = ("GITHUB_REPOSITORY", "GITHUB_REPOSITORY_ID", "GITHUB_SHA", "GITHUB_WORKFLOW_SHA",
                         "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB", "GITHUB_EVENT_NAME",
                         "RUNNER_OS", "RUNNER_ARCH", "RUNNER_ENVIRONMENT", "ImageOS", "ImageVersion")
    environment = {name: os.environ.get(name) for name in environment_names}
    if any(not value for value in environment.values()):
        raise FactError("missing GitHub hosted-runner/run/image identity")
    if environment["RUNNER_ENVIRONMENT"] != "github-hosted" or environment["RUNNER_OS"] != "Linux":
        raise FactError("not the required GitHub-hosted Linux runner")
    enabled = _field(reader, "/sys/module/apparmor/parameters/enabled")
    restriction = _field(reader, "/proc/sys/kernel/apparmor_restrict_unprivileged_userns")
    if enabled != "Y" or restriction != "1":
        raise FactError("required AppArmor/userns restriction state is not enabled/1")
    if any(os.environ.get(name) for name in ("LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT")):
        raise FactError("unexpected dynamic-loader environment")
    cgroup = f"/sys/fs/cgroup/user.slice/user-{uids[0]}.slice/user@{uids[0]}.service"
    cgroup_state = guard_path(cgroup, "isdir")
    if not cgroup_state["matches"]:
        raise FactError("ordinary runner's delegated cgroup root is absent")
    return {"identity": environment, "uname": list(os.uname()),
            "os_release": decode(reader.read("/etc/os-release"), "os-release"),
            "boot_id": _field(reader, "/proc/sys/kernel/random/boot_id"),
            "caller": {"pid": os.getpid(), "ppid": os.getppid(), "label": label, "status": fields,
                       "groups": os.getgroups(), "namespaces": {name: os.readlink("/proc/self/ns/" + name)
                       for name in ("user", "mnt", "pid", "net", "uts", "ipc", "cgroup")}},
            "apparmor_enabled": enabled, "userns_restriction": restriction,
            "cgroup": cgroup_state, "cgroup_controllers": _field(reader, "/sys/fs/cgroup/cgroup.controllers")}


def collect_system_tools(reader: Reader, vendor_dir: Path | None) -> dict[str, Any]:
    parser = shutil.which("apparmor_parser", path="/usr/sbin:/usr/bin:/sbin:/bin")
    if not parser:
        raise FactError("installed AppArmor parser is missing; installation is forbidden")
    parser_identity = executable_identity(parser, reader)
    binaries = {"apparmor": parser_identity, "bubblewrap": executable_identity("/usr/bin/bwrap", reader)}
    for package, identity in binaries.items():
        if vendor_dir:
            counterpart = vendor_dir / package / identity["canonical"].lstrip("/")
            # A merged-/usr canonical path can differ from a package's member path.
            alternatives = [counterpart]
            if identity["canonical"].startswith("/usr/"):
                alternatives.append(vendor_dir / package / identity["canonical"][5:])
            matches = [path for path in alternatives if path.is_file() and not path.is_symlink()]
            if len(matches) != 1 or hash_file(matches[0]) != identity["sha256"]:
                raise FactError(f"installed {package} binary differs from authenticated package")
            identity["matches_vendor_package"] = True
    feature_tree = reader.tree(APPARMOR_ROOT + "/features")
    features = {}
    for path, entry in feature_tree.items():
        if entry["type"] == "d":
            continue
        if entry["type"] != "f":
            raise FactError(f"unsupported AppArmor kernel feature entry: {path}")
        data = reader.read(path)
        features[path.removeprefix(APPARMOR_ROOT + "/features/")] = {
            "sha256": hashlib.sha256(data).hexdigest(), "text": decode(data, path)}
    return {"binaries": binaries, "features": features, "features_sha256": digest(features),
            "package_versions": decode(reader.commands.run(["/usr/bin/dpkg-query", "-W",
                "-f=${Package}\t${Version}\t${Architecture}\t${db:Status-Status}\n", "apparmor", "bubblewrap"]), "dpkg versions"),
            "parser_version": decode(reader.commands.run([parser, "--version"]), "parser version"),
            "bubblewrap_version": decode(reader.commands.run(["/usr/bin/bwrap", "--version"]), "bwrap version")}


# This runs unprivileged under each interpreter's NATURAL startup. No package is
# imported by name or installed, no pytest plugin is loaded, and site hooks are
# recorded rather than bypassed with -I/-S. The runner's installed environment
# is a trusted input that still needs explicit human review before activation.
PYTHON_FACTS_SCRIPT = r'''
import base64, hashlib, importlib.metadata, json, os, pathlib, site, stat, sys, sysconfig
MAX_FILES = 100000
MAX_BYTES = 2 * 1024 * 1024 * 1024
count = 0
total = 0
cache = {}
def describe(path, raw=False):
    global count, total
    p = pathlib.Path(path)
    canonical = str(p.resolve(strict=True))
    if canonical not in cache:
        st = p.stat()
        if not stat.S_ISREG(st.st_mode) or st.st_size > 256 * 1024 * 1024:
            raise RuntimeError('unsupported Python input type/size: ' + str(p))
        count += 1
        total += st.st_size
        if count > MAX_FILES or total > MAX_BYTES:
            raise RuntimeError('Python file inventory bound exceeded')
        with p.open('rb') as stream:
            sha = hashlib.file_digest(stream, 'sha256').hexdigest()
        cache[canonical] = dict(path=str(p), canonical=canonical, sha256=sha, bytes=st.st_size,
                                mode=stat.S_IMODE(st.st_mode), uid=st.st_uid, gid=st.st_gid)
    item = dict(cache[canonical])
    if raw:
        if item['bytes'] > 1024 * 1024:
            raise RuntimeError('startup input too large: ' + str(p))
        item['base64'] = base64.b64encode(p.read_bytes()).decode('ascii')
    return item
if sys.version_info[:2] != (3, 12):
    raise RuntimeError('reviewed interpreters must both be Python 3.12')
distributions = []
seen = set()
for distribution in importlib.metadata.distributions():
    name = distribution.metadata.get('Name')
    key = (name or '').lower().replace('_', '-').replace('.', '-')
    if not key or key in seen:
        raise RuntimeError('missing/duplicate distribution identity: ' + str(name))
    seen.add(key)
    if distribution.files is None:
        raise RuntimeError('missing installed file inventory: ' + name)
    members = []
    for member in distribution.files:
        # Generated bytecode is a real executable input too, if it exists.
        path = pathlib.Path(distribution.locate_file(member))
        try:
            path.stat()
        except FileNotFoundError:
            if str(member).endswith(('.pyc', '.pyo')):
                continue
            raise RuntimeError('missing distribution file: ' + str(path))
        members.append(describe(path))
    plugins = [dict(name=ep.name, value=ep.value, group=ep.group)
               for ep in distribution.entry_points if ep.group == 'pytest11']
    distributions.append(dict(name=name, version=distribution.version,
                             location=str(distribution.locate_file('')),
                             files=sorted(members, key=lambda x:x['canonical']),
                             pytest_entry_points=sorted(plugins, key=lambda x:x['name'])))
site_paths = site.getsitepackages() + [site.getusersitepackages()]
startup = []
path_states = []
for value in sorted(set(site_paths + sys.path)):
    directory = pathlib.Path(value or os.getcwd())
    try:
        info = directory.stat()
    except FileNotFoundError:
        path_states.append(dict(path=str(directory), state='absent'))
        continue
    if stat.S_ISREG(info.st_mode):
        path_states.append(dict(path=str(directory), state='file', identity=describe(directory)))
        continue
    if not stat.S_ISDIR(info.st_mode):
        raise RuntimeError('unsupported Python search path: ' + str(directory))
    names = list(directory.iterdir())  # permission failures are NOT absence
    path_states.append(dict(path=str(directory), state='directory'))
    for path in names:
        if path.suffix == '.pth' or path.name in ('sitecustomize.py', 'usercustomize.py', 'sitecustomize.pyc', 'usercustomize.pyc'):
            startup.append(describe(path, raw=True))
        if path.name in ('sitecustomize', 'usercustomize') and path.is_dir():
            for member in sorted(path.rglob('*')):
                if member.is_file():
                    startup.append(describe(member, raw=True))
loaded_hooks = {}
for name in ('sitecustomize', 'usercustomize'):
    module = sys.modules.get(name)
    if module is None:
        loaded_hooks[name] = None
    else:
        path = getattr(module, '__file__', None)
        if not path:
            raise RuntimeError('opaque loaded startup hook: ' + name)
        loaded_hooks[name] = describe(path, raw=True)
result = dict(executable=sys.executable, version=sys.version, prefix=sys.prefix,
              base_prefix=sys.base_prefix, path=sys.path, site_paths=site_paths,
              sysconfig_paths=sysconfig.get_paths(), enable_user_site=site.ENABLE_USER_SITE,
              search_path_states=path_states, startup_inputs=startup, loaded_hooks=loaded_hooks,
              distributions=sorted(distributions,key=lambda x:x['name'].lower()),
              freeze=sorted(x['name']+'=='+x['version'] for x in distributions),
              environment={key:os.environ.get(key) for key in ('PYTHONPATH','PYTHONHOME','PYTHONNOUSERSITE','PYTEST_ADDOPTS','PYTEST_PLUGINS','PYTEST_DISABLE_PLUGIN_AUTOLOAD')},
              files_hashed=count, bytes_hashed=total)
print(json.dumps(result, sort_keys=True, separators=(',', ':')))
'''


def strict_json(raw: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in items:
            if key in result:
                raise FactError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs)
    except (ValueError, UnicodeDecodeError) as exc:
        raise FactError("missing, contaminated, or truncated interpreter JSON") from exc


def collect_python(reader: Reader, repo: Path, executables: list[str]) -> dict[str, Any]:
    if len(executables) != 2 or len({str(Path(path).resolve(strict=True)) for path in executables}) != 2:
        raise FactError("both distinct installed 3.12 interpreters must be inventoried")
    records = []
    for executable in executables:
        identity = executable_identity(executable, reader, require_root=(executable == "/usr/bin/python3"))
        raw = reader.commands.run([executable, "-c", PYTHON_FACTS_SCRIPT], timeout=90, cwd=repo)
        inventory = strict_json(raw)
        if not isinstance(inventory, dict) or not inventory.get("distributions") or not inventory.get("freeze"):
            raise FactError("empty Python distribution inventory")
        if not any(entry["name"].lower() == "pytest" for entry in inventory["distributions"]):
            raise FactError("pytest is absent from a required interpreter")
        records.append({"binary": identity, "inventory": inventory, "inventory_sha256": digest(inventory)})
    return {"interpreters": records, "script_sha256": hashlib.sha256(PYTHON_FACTS_SCRIPT.encode()).hexdigest(),
            "trust_review": "REQUIRED; installed code, startup hooks, and plugins are not admitted here"}


def collect(output: Path, *, repo: Path, vendor_dir: Path | None = None,
            snapshots: dict[str, str] | None = None, pythons: list[str] | None = None) -> int:
    evidence = Evidence(output)
    commands = Commands(evidence)
    reader = Reader(commands, evidence)
    report: dict[str, Any] = {"schema": 1, "status": "COLLECTING", "admission": False,
                             "reviewed_inventory_sha256": None, "errors": [], "facts": {}}
    repo = repo.resolve(strict=True)
    report["started_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    stages = [
        ("host", lambda: collect_host(reader)),
        ("kernel_policy", lambda: collect_kernel_inventory(reader)),
        ("policy_inputs", lambda: collect_policy_inputs(reader, vendor_dir)),
        ("tools", lambda: collect_system_tools(reader, vendor_dir)),
        ("executables", lambda: collect_executables(reader)),
        ("natural_adapter_guards", lambda: collect_guards(repo, reader)),
        ("snapshots", lambda: {name: source_snapshot(repo, path, reader)
                               for name, path in (snapshots or DEFAULT_SNAPSHOTS).items()}),
        ("python", lambda: collect_python(reader, repo, pythons or [sys.executable, "/usr/bin/python3"])),
    ]
    try:
        if os.getuid() == 0 or os.geteuid() == 0:
            raise FactError("never run the fact collector as root")
        for name, callback in stages:
            try:
                result = callback()
                report["facts"][name] = result
                if name == "kernel_policy" and result["conflicting_names"]:
                    raise FactError("pre-existing conflicting bwrap/unpriv_bwrap definitions")
                if name == "policy_inputs" and result["forbidden_overrides"]:
                    raise FactError("relevant local/disable/force-complain override exists")
                if name == "natural_adapter_guards" and result["unexpectedly_eligible"]:
                    raise FactError("extra real adapters unexpectedly eligible: " + ", ".join(result["unexpectedly_eligible"]))
            except (FactError, OSError, ValueError, SyntaxError) as exc:
                report["errors"].append({"stage": name, "error": str(exc)})
            evidence.json("facts.json", report)
    except (FactError, OSError) as exc:
        report["errors"].append({"stage": "collector", "error": str(exc)})
    finally:
        report["status"] = "STOP" if report["errors"] else "COLLECTED_UNREVIEWED"
        report["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        report["commands"] = commands.records
        report["raw_sources"] = evidence.sources
        evidence.json("facts.json", report)
        kernel = report["facts"].get("kernel_policy")
        if kernel:
            evidence.json("attachment-inventory.json", kernel["semantic"])
            evidence.json("attachment-review-required.json", {
                "observed_inventory_sha256": kernel["semantic_sha256"], "reviewed_inventory_sha256": None,
                "admission": False, "status": "MANUAL_REVIEW_REQUIRED"})
    return 2 if report["errors"] else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--vendor-dir", type=Path,
                        help="data-only authenticated .deb files plus extracted apparmor-profiles/, apparmor/, bubblewrap/")
    parser.add_argument("--snapshot", action="append", default=[], metavar="NAME=RELATIVE_PATH",
                        help="override/add snapshot bindings; defaults capture source/tests/helpers/workflows/project")
    parser.add_argument("--python", action="append", metavar="PATH",
                        help="exactly twice to override setup-python and /usr/bin/python3")
    args = parser.parse_args(argv)
    snapshots = dict(DEFAULT_SNAPSHOTS)
    for binding in args.snapshot:
        name, separator, relative = binding.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", name) or not relative:
            parser.error("--snapshot requires NAME=RELATIVE_PATH")
        snapshots[name] = relative
    try:
        return collect(args.output, repo=args.repo, vendor_dir=args.vendor_dir,
                       snapshots=snapshots, pythons=args.python)
    except (FactError, OSError) as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
