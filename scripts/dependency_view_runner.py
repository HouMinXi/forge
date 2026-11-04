"""Exact-tree, explicitly dispatched wrapper for the fixed two-stat diagnostic."""

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import types

ROOT = Path(__file__).resolve().parents[1]
BRANCH = "diag/ordinary-dependency-snapshot-20261103"
WORKFLOW = ".github/workflows/strict-linux-admission.yml"
FILES = (
    WORKFLOW,
    "scripts/dependency_view_runner.py",
    "scripts/dependency_view.py",
    "scripts/dependency_snapshot.py",
    "scripts/fixed_runner_metadata.py",
    "scripts/dependency_candidates.py",
    "scripts/dependency_fragment_evidence.py",
)
ENV = {
    "PATH": "/usr/bin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_NO_REPLACE_OBJECTS": "1",
}


def need(value):
    if not value:
        raise ValueError("invalid")


def context():
    keys = "REPOSITORY RUN_ID RUN_ATTEMPT JOB REF WORKFLOW_SHA WORKFLOW_REF SOURCE_SHA EXPECTED_SHA EVENT ORDINARY FIXED VIEW".split()
    value = {key: os.environ.get("CONTEXT_" + key, "") for key in keys}
    need(value["REPOSITORY"] == "HouMinXi/forge" and value["EVENT"] == "workflow_dispatch")
    need(value["JOB"] == "dependency-view" and value["REF"] == "refs/heads/" + BRANCH)
    need(value["VIEW"] == "true" and value["ORDINARY"] == value["FIXED"] == "false")
    need(re.fullmatch(r"[1-9][0-9]{0,19}", value["RUN_ID"]) and value["RUN_ATTEMPT"] == "1")
    need(
        all(
            re.fullmatch(r"[0-9a-f]{40}", value[k])
            for k in ("SOURCE_SHA", "EXPECTED_SHA", "WORKFLOW_SHA")
        )
    )
    need(value["SOURCE_SHA"] == value["EXPECTED_SHA"] == value["WORKFLOW_SHA"])
    need(value["WORKFLOW_REF"] == "HouMinXi/forge/" + WORKFLOW + "@refs/heads/" + BRANCH)
    return value


def read_source(name):
    fd = os.open(ROOT / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        need(stat.S_ISREG(info.st_mode) and info.st_size <= 65536)
        data = b""
        while len(data) <= 65536:
            chunk = os.read(fd, 65537 - len(data))
            if not chunk:
                break
            data += chunk
        after = os.fstat(fd)
        need(
            len(data) == info.st_size
            and (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        )
        return data
    finally:
        os.close(fd)


def bind(sha):
    git = Path("/usr/bin/git")
    info = git.stat()
    need(
        stat.S_ISREG(info.st_mode)
        and info.st_uid == 0
        and info.st_mode & 0o111
        and not info.st_mode & 0o6022
    )
    need(git.read_bytes().startswith(b"\x7fELF"))

    def command(args):
        proc = subprocess.run(
            [str(git), "--no-pager", "--no-optional-locks", "-c", "core.fsmonitor=false", *args],
            cwd=ROOT,
            env=ENV,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=2,
            check=False,
        )
        need(proc.returncode == 0 and not proc.stderr and len(proc.stdout) <= 65536)
        return proc.stdout

    need(command(["rev-parse", "HEAD"]) == (sha + "\n").encode())
    records, hashes = [], {}
    for name in FILES:
        data = read_source(name)
        blob = hashlib.sha1(
            b"blob " + str(len(data)).encode() + b"\0" + data, usedforsecurity=False
        ).hexdigest()
        records.append(f"100644 blob {blob}\t{name}".encode())
        hashes[name] = hashlib.sha256(data).hexdigest()
    tree = command(["ls-tree", "-z", sha, "--", *FILES])
    need(tree.endswith(b"\0") and sorted(tree[:-1].split(b"\0")) == sorted(records))
    need(command(["rev-parse", "HEAD"]) == (sha + "\n").encode())
    return hashes


def observe():
    result = {
        "schema": "forge-dependency-view-dispatch-v1",
        "status": "STOP",
        "native": "NOT_RUN",
        "qualification": "NOT_RUN",
    }
    try:
        need(not sys.argv[1:] and os.getuid() == os.geteuid() != 0 and os.getgid() == os.getegid() != 0)
        ctx = context()
        hashes = bind(ctx["SOURCE_SHA"])
        # Source has been compared with the exact approved tree before execution.
        source = read_source("scripts/fixed_runner_metadata.py")
        need(hashlib.sha256(source).hexdigest() == hashes["scripts/fixed_runner_metadata.py"])
        base = types.ModuleType("_bound_metadata")
        base.__file__ = str(ROOT / "scripts/fixed_runner_metadata.py")
        exec(compile(source, base.__file__, "exec"), base.__dict__)  # noqa: S102 -- exact reviewed tree bound above.
        result["native"] = "ATTEMPTED"
        worker = read_source("scripts/dependency_view.py")
        need(hashlib.sha256(worker).hexdigest() == hashes["scripts/dependency_view.py"])
        # Pass the exact verified worker bytes as code, avoiding a path re-open race.
        code = (
            "__file__ = "
            + repr(str(ROOT / "scripts/dependency_view.py"))
            + "\n"
            + worker.decode("utf-8")
        )
        env = {**base.ENV, "BOUND_SOURCE_SHA256": json.dumps(hashes, sort_keys=True)}
        output = base.capture(
            ["/usr/bin/python3", "-I", "-S", "-B", "-c", code],
            env,
            time.monotonic() + 40,
            limit=262144,
            cwd=ROOT,
        )
        need(not output["reason"] and not output["stderr_bytes"] and output["exit_code"] in (0, 2))
        value = json.loads(output["stdout"])
        need(
            isinstance(value, dict)
            and value.get("schema") == "forge-dependency-view-v1"
            and value.get("qualification") == "NOT_RUN"
            and (value.get("status"), output["exit_code"]) in (("OBSERVED", 0), ("UNKNOWN", 2))
        )
        need(bind(ctx["SOURCE_SHA"]) == hashes)
        result.update(
            status=value["status"],
            native="RETURNED",
            context=ctx,
            source_sha256=hashes,
            observation=value,
        )
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        result["reason"] = "incomplete_or_changed"
    return result


def bounded_output(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    if len(raw) + 1 <= 300000:
        return raw, False
    observation = value.get("observation") or {}
    retained = {
        "schema": "forge-dependency-view-dispatch-v1",
        "status": "STOP",
        "reason": "output",
        "native": value.get("native"),
        "qualification": "NOT_RUN",
        "observation": {
            key: observation.get(key)
            for key in (
                "phase",
                "stat_attempts",
                "snapshot_consistency",
                "stability",
                "manager_disk_equivalence",
                "package_provenance",
                "transaction_closure",
                "qualification",
                "service_execution",
                "library_reply_allocation",
                "privileged_descendants",
            )
        },
    }
    return json.dumps(retained, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(), True


def main():
    value = observe()
    raw, overflow = bounded_output(value)
    sys.stdout.buffer.write(raw + b"\n")
    return 0 if value["status"] == "OBSERVED" and not overflow else 2


if __name__ == "__main__":
    sys.exit(main())
