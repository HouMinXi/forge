"""Fixed ordinary-user dispatch receipt. Not qualification or a generic launcher."""

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
import types

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ".github/workflows/strict-linux-admission.yml"
BRANCH = "diag/ordinary-dependency-snapshot-20261103"
PROBE = "scripts/dependency_snapshot.py"
PROBE_SHA256 = "8eaecf046c8fa2689a1577ac1aeb1e1f51077ce2cb68c2014426e4554eb07014"
FILES = (PROBE, "scripts/fixed_runner_metadata.py", "scripts/dependency_snapshot_runner.py", WORKFLOW)
KEYS = "REPOSITORY RUN_ID RUN_ATTEMPT JOB REF WORKFLOW_SHA WORKFLOW_REF SOURCE_SHA EXPECTED_SHA EVENT ORDINARY FIXED".split()


class Stop(ValueError):
    pass


def need(condition, reason):
    if not condition:
        raise Stop(reason)


def context():
    value = {key: os.environ.get("CONTEXT_" + key, "") for key in KEYS}
    need(value["REPOSITORY"] == "HouMinXi/forge" and value["EVENT"] == "workflow_dispatch", "context")
    need(value["ORDINARY"] == "true" and value["FIXED"] == "false", "context")
    need(value["JOB"] == "ordinary-dependency" and value["REF"] == "refs/heads/" + BRANCH, "context")
    need(re.fullmatch(r"[1-9][0-9]{0,19}", value["RUN_ID"]) and value["RUN_ATTEMPT"] == "1", "context")
    need(
        all(
            re.fullmatch(r"[0-9a-f]{40}", value[k])
            for k in ("SOURCE_SHA", "EXPECTED_SHA", "WORKFLOW_SHA")
        ),
        "context",
    )
    need(value["SOURCE_SHA"] == value["EXPECTED_SHA"] == value["WORKFLOW_SHA"], "context")
    need(value["WORKFLOW_REF"] == "HouMinXi/forge/" + WORKFLOW + "@refs/heads/" + BRANCH, "context")
    return value


def read_source(name):
    fd = os.open(ROOT / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        need(stat.S_ISREG(info.st_mode) and info.st_size <= 65536, "source")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            data = handle.read(65537)
        need(len(data) == info.st_size <= 65536, "source")
        return data
    finally:
        os.close(fd)


def source_binding(base, sha):
    env = {
        **base.ENV,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_NO_REPLACE_OBJECTS": "1",
    }
    git = ["/usr/bin/git", "--no-pager", "--no-optional-locks", "-c", "core.fsmonitor=false"]

    def command(args):
        output = base.capture([*git, *args], env, time.monotonic() + 2, limit=65536, cwd=ROOT)
        need(not output["reason"] and output["exit_code"] == 0 and not output["stderr_bytes"], "source")
        return output["stdout"]

    need(command(["rev-parse", "HEAD"]) == (sha + "\n").encode(), "source")
    hashes, records = {}, []
    for name in FILES:
        data = read_source(name)
        blob = hashlib.sha1(
            b"blob " + str(len(data)).encode() + b"\0" + data, usedforsecurity=False
        ).hexdigest()
        records.append(f"100644 blob {blob}\t{name}".encode())
        hashes[name] = hashlib.sha256(data).hexdigest()
    tree = command(["ls-tree", "-z", "HEAD", "--", *FILES])
    need(tree.endswith(b"\0") and sorted(tree[:-1].split(b"\0")) == sorted(records), "source")
    return hashes


def observe():
    result = {
        "schema": "forge-dependency-dispatch-v1",
        "status": "STOP",
        "reason": None,
        "native": "NOT_RUN",
        "qualification": "NOT_RUN",
        "context": None,
        "source_sha256": None,
        "probe": None,
        "authentication": "UNVERIFIED_DECLARED_CONTEXT",
    }
    try:
        need(not sys.argv[1:], "context")
        need(os.getuid() == os.geteuid() != 0 and os.getgid() == os.getegid() != 0, "identity")
        result["context"] = ctx = context()
        data = read_source(PROBE)
        need(hashlib.sha256(data).hexdigest() == PROBE_SHA256, "source")
        probe = types.ModuleType("_reviewed_dependency_snapshot")
        probe.__file__ = str(ROOT / PROBE)
        exec(compile(data, probe.__file__, "exec"), probe.__dict__)  # noqa: S102 -- exact hash-bound reviewed probe declarations only.
        base = probe.metadata()
        result["source_sha256"] = hashes = source_binding(base, ctx["SOURCE_SHA"])
        result["native"] = "ATTEMPTED"
        output = base.capture(
            ["/usr/bin/python3", "-I", "-S", "-B", str(ROOT / PROBE)],
            base.ENV,
            time.monotonic() + 40,
            limit=probe.MAX_RECEIPT,
            cwd=ROOT,
        )
        result["command"] = {key: output[key] for key in ("exit_code", "reason", "stderr_bytes")}
        need(
            not output["reason"] and not output["stderr_bytes"] and output["exit_code"] in (0, 2),
            "command",
        )
        value = json.loads(output["stdout"])
        need(
            isinstance(value, dict)
            and value.get("schema") == "forge-dependency-snapshot-v1"
            and value.get("qualification") == "NOT_RUN"
            and (
                (
                    output["exit_code"] == 0
                    and value.get("status") == "OBSERVED"
                    and value.get("complete") is True
                )
                or (
                    output["exit_code"] == 2
                    and value.get("status") == "STOP"
                    and value.get("complete") is False
                )
            ),
            "format",
        )
        need(source_binding(base, ctx["SOURCE_SHA"]) == hashes, "source")
        result.update(status=value["status"], native="RETURNED", probe=value)
    except (OSError, ValueError, AttributeError, TypeError, RecursionError) as exc:
        result["reason"] = (
            str(exc) if isinstance(exc, Stop) else "io" if isinstance(exc, OSError) else "format"
        )
    return result


def main():
    result = observe()
    raw = json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    if len(raw) > 300000:
        result.update(status="STOP", reason="output", probe=None)
        raw = json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    print(raw)
    return 0 if result["status"] == "OBSERVED" else 2


if __name__ == "__main__":
    sys.exit(main())
