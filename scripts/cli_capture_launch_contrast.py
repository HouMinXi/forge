# SPDX-License-Identifier: Apache-2.0
"""Two fixed, independent launch controls, never a target workload fallback."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import resource
import subprocess
import sys

MARKER = b"forge-launch-control-v1\n"
PROGRAM = "import os;os.write(1,b'forge-launch-control-v1\\n')"
LIMIT = 65536


def empty_arm():
    return dict(
        execution="NOT_RUN",
        returncode=None,
        stdout="NOT_OBSERVED",
        stderr="NOT_OBSERVED",
        cleanup="NOT_ASSESSED",
    )


def empty():
    return dict(
        binding="NOT_ASSESSED",
        direct=empty_arm(),
        namespaced=empty_arm(),
        causal_attribution="NOT_ASSESSED",
    )


def cleanup(report):
    if type(report) is not dict or type(report.get("cleanup_complete")) is not bool:
        return "UNKNOWN"
    rows = report.get("owned")
    if type(rows) is not list or any(
        type(r) is not dict or type(r.get("remaining")) is not bool for r in rows
    ):
        return "UNKNOWN"
    if not report["cleanup_complete"] or any(r["remaining"] for r in rows):
        return "INCOMPLETE"
    return "COMPLETE"


def arm(owner, argv, output, env):
    result = empty_arm()
    try:
        done = owner(argv, timeout=5, cwd=str(output), env=env, memory_limit_bytes=1024**3)
        result["cleanup"] = cleanup(getattr(done, "ownership", None))
        if type(done.returncode) is not int or not -127 <= done.returncode <= 255:
            raise ValueError("invalid owner result")
        if (
            type(done.stdout) is not bytes
            or type(done.stderr) is not bytes
            or max(len(done.stdout), len(done.stderr)) > LIMIT
        ):
            raise ValueError("invalid output")
        result.update(
            execution="COMPLETED",
            returncode=done.returncode,
            stdout="EXACT_MARKER" if done.stdout == MARKER else "EMPTY" if not done.stdout else "OTHER",
            stderr="PRESENT" if done.stderr else "EMPTY",
        )
    except (Exception, KeyboardInterrupt) as error:  # noqa: BLE001 - closed outcome only; never publish exception text
        result = empty_arm() | dict(
            execution="TIMED_OUT"
            if isinstance(error, subprocess.TimeoutExpired)
            else "INTERRUPTED"
            if isinstance(error, KeyboardInterrupt)
            else "OWNER_ERROR",
            cleanup=cleanup(getattr(error, "ownership", getattr(error, "report", None))),
        )
    return result


def load_capture():
    path = Path(__file__).with_name("capture_installed_cli.py")
    spec = importlib.util.spec_from_file_location("_launch_control_capture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def contrast(capture, evidence, baseline, diagnostic, output):
    result = empty()
    try:
        output = capture.new_private_directory(output)
        capture.sanitize_controller(output)
        # Limits apply only to this dedicated controller and its fixed children.
        capture.apply_limits()
        _, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
        bound = LIMIT if hard == resource.RLIM_INFINITY else min(LIMIT, hard)
        resource.setrlimit(resource.RLIMIT_FSIZE, (bound, bound))
        capture.checked_directory(baseline.parent, private=True)
        for mode, path in (("baseline", baseline), ("diagnostic", diagnostic)):
            capture.checked_directory(path.parent, private=True)
            previous = capture.read_json(path.with_name("result.json"))
            capture.require(
                type(previous) is dict
                and type(previous.get("schema")) is int
                and previous["schema"] == 1
                and previous.get("mode") == mode
                and cleanup(previous.get("ownership")) == "COMPLETE",
                "previous target cleanup not proved",
            )
        binding = capture.read_json(baseline)
        capture.require(
            type(binding) is dict and type(binding.get("schema")) is int and binding["schema"] == 1,
            "binding",
        )
        capture.checked_directory(diagnostic.parent, private=True)
        other = capture.read_json(diagnostic)
        capture.require(
            type(other) is dict and type(other.get("schema")) is int and other["schema"] == 1,
            "diagnostic binding",
        )
        capture.require(
            all(
                other.get(k) == binding.get(k) for k in ("controller", "unshare", "installed_provenance")
            ),
            "bindings disagree",
        )
        provenance, sources, origin = capture.verify_installation(evidence, owner_only=True)
        capture.require(provenance == binding.get("installed_provenance"), "owner provenance")
        current_python = capture.interpreter_identity(sys.executable)
        current_unshare = capture.utility("unshare")
        if current_python != binding.get("controller") or current_unshare != binding.get("unshare"):
            result["binding"] = "CHANGED"
            return result
        owner = capture.load_owner(sources, origin)
        result["binding"] = "MATCHED_CAPTURE_BINDING"
        command = [sys.executable, "-I", "-B", "-c", PROGRAM]
        env = capture.safe_environment(output)
        result["direct"] = arm(owner, command, output, env)
        if result["direct"]["cleanup"] == "COMPLETE" and result["direct"]["execution"] != "INTERRUPTED":
            result["namespaced"] = arm(
                owner,
                [current_unshare["path"], "--user", "--map-root-user", "--net", "--", *command],
                output,
                env,
            )
    except (FileNotFoundError, PermissionError):
        result["binding"] = "UNAVAILABLE"
    except Exception:  # noqa: BLE001 - fail closed before any control launch
        result["binding"] = "INVALID"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("evidence", "baseline", "diagnostic", "output", "capability"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    capture = load_capture()
    result = contrast(capture, args.evidence, args.baseline, args.diagnostic, args.output)
    capture.checked_directory(args.capability.parent, private=True)
    value = capture.read_json(args.capability)
    capture.require(value.get("schema") == "forge-cli-capture-capability-v2", "capability version")
    value.update(schema="forge-cli-capture-capability-v2", launch_contrast=result)
    # Existing closed publisher independently validates every field before upload.
    pending = args.capability.with_name(".capability-contrast.pending")
    capture.private_bytes(pending, (json.dumps(value, sort_keys=True) + "\n").encode())
    os.replace(pending, args.capability)
    return (
        130
        if result["direct"]["execution"] == "INTERRUPTED"
        or result["namespaced"]["execution"] == "INTERRUPTED"
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
