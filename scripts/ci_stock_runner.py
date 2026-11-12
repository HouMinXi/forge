"""Fail-closed stock-runner prerequisites, without changing host security policy.

This is a prerequisite probe, not a substitute for the original CI phases.
It never loads a profile, changes a sysctl, grants a capability, or repairs a host.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BWRAP = Path("/usr/bin/bwrap")
PROFILE = Path("/etc/apparmor.d/bwrap-userns-restrict")
RESTRICTION = Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns")
ENABLED = Path("/sys/module/apparmor/parameters/enabled")
NAMESPACES = ("user", "mnt", "pid", "net", "ipc", "uts")
SHA = re.compile(r"[0-9a-f]{40}\Z")
CHILD = """import json,os,sys
from pathlib import Path
status=dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines())
print(json.dumps({
 'cache_tag':sys.implementation.cache_tag,
 'profile':Path('/proc/self/attr/current').read_text().strip(),
 'status':{key:status[key].strip() for key in ('CapEff','CapPrm','CapAmb','NoNewPrivs')},
 'namespaces':{key:os.readlink('/proc/self/ns/'+key) for key in ('user','mnt','pid','net','ipc','uts')},
}))
"""


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def execute(argv):
    return subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=30, check=True)


def read_bounded(path, maximum=65536):
    with path.open("rb") as handle:
        value = handle.read(maximum + 1)
    require(len(value) <= maximum, "oversized prerequisite metadata")
    return value


def root_file(path):
    info = path.lstat()
    require(
        stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o6022,
        "prerequisite file is not a regular root-owned, non-setid, non-writable file",
    )
    return read_bounded(path, maximum=16 * 1024 * 1024)


def security_state():
    require(read_bounded(ENABLED).strip() == b"Y", "AppArmor is not enabled")
    require(read_bounded(RESTRICTION).strip() == b"1", "user namespace restriction is not enabled")
    return {
        "apparmor": "enabled",
        "restrict_userns": 1,
        "profile_sha256": hashlib.sha256(root_file(PROFILE)).hexdigest(),
    }


def verify_request(expected_sha, actual_sha, event_name, checked_out_sha):
    require(event_name == "workflow_dispatch", "qualification requires an explicit dispatch")
    require(SHA.fullmatch(expected_sha) is not None, "expected source SHA must be a full commit")
    require(expected_sha == actual_sha == checked_out_sha, "qualification source SHA mismatch")


def verify_runtime(info):
    require(
        info == {"cache_tag": "cpython-314", "pytest": "9.2.0"},
        "both CI interpreters must be CPython 3.14 with pytest 9.2.0",
    )


def verify_child(child, parent_namespaces):
    require(set(child) == {"cache_tag", "profile", "status", "namespaces"}, "invalid probe envelope")
    require(child["cache_tag"] == "cpython-314", "sandbox interpreter is not CPython 3.14")
    require(
        child["profile"]
        in (
            "bwrap//&unpriv_bwrap (enforce)",
            "unpriv_bwrap//&bwrap (enforce)",
        ),
        "sandbox child lacks the enforced stock profile stack",
    )
    status = child["status"]
    require(set(status) == {"CapEff", "CapPrm", "CapAmb", "NoNewPrivs"}, "invalid capability fields")
    for field in ("CapEff", "CapPrm", "CapAmb"):
        require(re.fullmatch(r"0{16}", status[field]) is not None, "sandbox child retained capabilities")
    require(status["NoNewPrivs"] == "1", "sandbox child lacks no-new-privileges")
    require(set(child["namespaces"]) == set(NAMESPACES), "incomplete namespace observations")
    require(set(parent_namespaces) == set(NAMESPACES), "incomplete parent namespace observations")
    for name in NAMESPACES:
        value = child["namespaces"][name]
        require(re.fullmatch(name + r":\[\d+\]", value) is not None, "invalid namespace identity")
        require(value != parent_namespaces[name], "sandbox namespace is shared with the parent")


def verify_host():
    before = security_state()
    root_file(BWRAP)
    parent_namespaces = {name: os.readlink(f"/proc/self/ns/{name}") for name in NAMESPACES}
    result = execute(
        [
            str(BWRAP),
            "--unshare-all",
            "--ro-bind",
            "/",
            "/",
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--",
            "/usr/bin/python3",
            "-I",
            "-B",
            "-c",
            CHILD,
        ]
    )
    require(len(result.stdout) <= 8192, "oversized sandbox probe")
    verify_child(json.loads(result.stdout), parent_namespaces)
    require(security_state() == before, "host security state changed during the probe")


def verify_interpreters():
    code = (
        "import json,sys,pytest; "
        "print(json.dumps({'cache_tag':sys.implementation.cache_tag,'pytest':pytest.__version__}))"
    )
    for interpreter in (sys.executable, "/usr/bin/python3"):
        verify_runtime(json.loads(execute([interpreter, "-B", "-c", code]).stdout))


def verify_system_runtime(output):
    """Exercise the actual system build, not another interpreter with the same tag."""
    if __package__:
        from . import ci_test_shards as ci
    else:
        import ci_test_shards as ci

    sha = os.environ["GITHUB_SHA"]
    require(SHA.fullmatch(sha) is not None, "invalid source SHA")
    manifest = ci.read(ROOT / "scripts/ci_public_test_identities.json")
    nodes = {
        node
        for node in manifest["nodes"]
        if node.startswith("tests/test_gate_pytest_evidence.py::") and "cpython-314-pytest9.2.0" in node
    }
    require(len(nodes) == 27, "reviewed system-runtime qualification inventory changed")
    output.mkdir(mode=0o700)
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": os.environ["HOME"],
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(ROOT / "src"),
    }
    selection = ["tests/test_gate_pytest_evidence.py", "-k", "cpython-314-pytest9.2.0"]
    phase = "system-runtime"
    policy = {"collection_skips": [], "collection_skip_reasons": {}, "runtime_skips": {}}
    for label, extra in (("collection", ["--collect-only"]), ("execution", [])):
        report = output / (label + ".json")
        argv = ci.command_for_pytest(phase, sha, report, [*selection, *extra])
        argv[0] = "/usr/bin/python3"
        with (output / (label + ".log")).open("xb") as log:
            subprocess.run(
                argv, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=300, check=True
            )
        receipt = ci.read(report)
        collected, skips = ci.check_collection(receipt, sha, phase, policy)
        require(
            collected == nodes and not skips, "system-runtime collection differs from reviewed inventory"
        )
        require(receipt["exitstatus"] == 0, "system-runtime qualification failed")
        if label == "execution":
            plan = {"sha": sha, "phases": {phase: {"nodes": sorted(nodes), "collection_skips": []}}}
            ci.validate_execution(receipt, phase, plan, policy)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("request", "host", "interpreters", "system-runtime"))
    args = parser.parse_args()
    if args.operation == "request":
        head = execute(["git", "rev-parse", "HEAD"]).stdout.strip()
        verify_request(
            os.environ.get("EXPECTED_SOURCE_SHA", ""),
            os.environ.get("GITHUB_SHA", ""),
            os.environ.get("GITHUB_EVENT_NAME", ""),
            head,
        )
    elif args.operation == "host":
        verify_host()
    elif args.operation == "interpreters":
        verify_interpreters()
    else:
        verify_system_runtime(Path(os.environ["RUNNER_TEMP"]) / "forge-stock-system-runtime")
    print("PASS: stock-runner prerequisite only; complete CI receipts are still required")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print("STOP: stock-runner prerequisite unavailable; no repair was attempted", file=sys.stderr)
        raise SystemExit(2) from None
