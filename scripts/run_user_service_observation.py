"""Bounded GitHub workflow wrapper; structured observations are not provenance.

This wrapper invokes only the reviewed topology collector. It installs nothing,
changes no host policy and never promotes a declared receipt to qualification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
from types import ModuleType

LIMIT = 16384
STDERR_LIMIT = 2048
COLLECTOR_SECONDS = 90
WORKFLOW = ".github/workflows/strict-linux-user-service.yml"
COLLECTOR = "scripts/strict_user_service_probe.py"
WRAPPER = "scripts/run_user_service_observation.py"
EXECUTION_FILES = (WORKFLOW, COLLECTOR, WRAPPER)
ENV = {
    "PATH": "/usr/bin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
}
CONTEXT_FIELDS = {
    "repository": "CONTEXT_REPOSITORY",
    "run_id": "CONTEXT_RUN_ID",
    "run_attempt": "CONTEXT_RUN_ATTEMPT",
    "job_name": "CONTEXT_JOB_NAME",
    "workflow_ref": "CONTEXT_WORKFLOW_REF",
    "workflow_sha": "CONTEXT_WORKFLOW_SHA",
    "source_sha": "CONTEXT_SOURCE_SHA",
    "event_name": "CONTEXT_EVENT_NAME",
}


ERROR_REASONS = {
    "context_invalid",
    "source_metadata",
    "unsafe_tool",
    "checkout_mismatch",
    "source_changed",
    "artifact_limit",
    "artifact_directory",
    "ordinary_user",
    "pidfd_unavailable",
    "collector_timeout",
    "collector_output_limit",
    "collector_capture_error",
    "collector_reap_unverified",
    "collector_stderr",
    "receipt_context_mismatch",
    "receipt_identity_mismatch",
    "collector_exit_mismatch",
    "receipt_missing",
    "wrapper_internal_error",
}


class WrapperError(ValueError):
    def __init__(self, reason):
        super().__init__(
            reason if type(reason) is str and reason in ERROR_REASONS else "wrapper_internal_error"
        )


def require(value, reason):
    if not value:
        raise WrapperError(reason)


def context_from_environment():
    # Read only these declared fields, never enumerate/copy the job environment.
    value = {key: os.environ.get(name, "") for key, name in CONTEXT_FIELDS.items()}
    require(value["repository"] == "HouMinXi/forge", "context_invalid")
    require(value["event_name"] in {"pull_request", "workflow_dispatch"}, "context_invalid")
    for name in ("source_sha", "workflow_sha"):
        require(re.fullmatch(r"[0-9a-f]{40}", value[name]), "context_invalid")
    require(re.fullmatch(r"[1-9][0-9]{0,19}", value["run_id"]), "context_invalid")
    require(re.fullmatch(r"[1-9][0-9]{0,5}", value["run_attempt"]), "context_invalid")
    value["run_attempt"] = int(value["run_attempt"])
    require(value["job_name"] == "observe", "context_invalid")
    ref = value["workflow_ref"]
    require(
        len(ref) <= 512
        and all(" " <= c <= "~" for c in ref)
        and ref.startswith(value["repository"] + "/" + WORKFLOW + "@refs/"),
        "context_invalid",
    )
    return value


def verified_tool(path):
    resolved = Path(path).resolve(strict=True)
    require(str(resolved).startswith("/usr/bin/"), "unsafe_tool")
    for item in (resolved, *resolved.parents):
        info = item.lstat()
        require(info.st_uid == 0 and not info.st_mode & 0o6022, "unsafe_tool")
        require(
            stat.S_ISREG(info.st_mode) if item == resolved else stat.S_ISDIR(info.st_mode), "unsafe_tool"
        )
    require(bool(resolved.stat().st_mode & 0o111), "unsafe_tool")
    return str(resolved)


def verify_tree(root, raw):
    require(raw.endswith(b"\0"), "checkout_mismatch")
    entries = {}
    for entry in raw[:-1].split(b"\0"):
        metadata, sep, name = entry.partition(b"\t")
        require(sep == b"\t", "checkout_mismatch")
        name = name.decode("ascii")
        fields = metadata.decode("ascii").split(" ")
        require(
            len(fields) == 3
            and fields[0] in {"100644", "100755"}
            and fields[1] == "blob"
            and re.fullmatch(r"[0-9a-f]{40}", fields[2]),
            "checkout_mismatch",
        )
        require(name in EXECUTION_FILES and name not in entries, "checkout_mismatch")
        data, mode = read_source(root, name)
        require(bool(mode & stat.S_IXUSR) == (fields[0] == "100755"), "checkout_mismatch")
        # SHA1 is the Git object identifier only; artifact content uses SHA256.
        blob = hashlib.sha1(
            b"blob " + str(len(data)).encode("ascii") + b"\0" + data, usedforsecurity=False
        ).hexdigest()
        require(blob == fields[2], "checkout_mismatch")
        entries[name] = blob
    require(set(entries) == set(EXECUTION_FILES), "checkout_mismatch")


def source_identity(info):
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_gid,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def read_source(root, name):
    path = root / name
    require(name in EXECUTION_FILES and path.resolve(strict=True) == path, "source_metadata")
    for item in (path, *path.parents):
        info = item.lstat()
        require(info.st_uid in {0, os.getuid()} and not info.st_mode & 0o6022, "source_metadata")
        require(
            stat.S_ISREG(info.st_mode) if item == path else stat.S_ISDIR(info.st_mode), "source_metadata"
        )
    initial = path.lstat()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(
            stat.S_ISREG(before.st_mode)
            and source_identity(initial) == source_identity(before)
            and before.st_size <= 131072,
            "source_metadata",
        )
        data = os.read(fd, 131073)
        after = os.fstat(fd)
        # Reading may update atime; compare identity/size/modification metadata.
        require(
            source_identity(before) == source_identity(after) and len(data) == before.st_size,
            "source_changed",
        )
        return data, before.st_mode
    finally:
        os.close(fd)


def source_hashes(root):
    return {name: hashlib.sha256(read_source(root, name)[0]).hexdigest() for name in EXECUTION_FILES}


def capture(argv, seconds, cwd):
    """Drain capped buffers, letting normal collector cleanup finish before 90s.

    On a last-resort timeout only the direct child pidfd is signalled. No cleanup
    claim is inferred from that signal or from the service's independent timer.
    """
    child = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=ENV,
        cwd=cwd,
        start_new_session=True,
    )
    pidfd = None
    output = bytearray()
    result = {
        "reason": "completed",
        "returncode": None,
        "stdout": b"",
        "stderr_bytes": 0,
        "cleanup_error": False,
    }
    deadline = time.monotonic() + seconds
    try:
        pidfd = os.pidfd_open(child.pid)
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ, "stdout")
            selector.register(child.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map() or child.poll() is None:
                left = deadline - time.monotonic()
                if left <= 0:
                    result["reason"] = "timeout"
                    break
                for key, _ in selector.select(min(left, 0.1)):
                    block = os.read(key.fileobj.fileno(), 4096)
                    if not block:
                        selector.unregister(key.fileobj)
                    elif key.data == "stdout":
                        space = LIMIT - len(output)
                        output.extend(block[:space])
                        if len(block) > space:
                            result["reason"] = "output_limit"
                    else:
                        result["stderr_bytes"] = min(
                            STDERR_LIMIT + 1, result["stderr_bytes"] + len(block)
                        )
                        if result["stderr_bytes"] > STDERR_LIMIT:
                            result["reason"] = "output_limit"
    except (OSError, ValueError):
        result["reason"] = "capture_error"
    finally:
        try:
            if child.poll() is None:
                # pidfd signalling cannot hit a reused PID. If opening failed,
                # this unreaped direct child is still reserved for its parent.
                if pidfd is not None:
                    signal.pidfd_send_signal(pidfd, signal.SIGTERM)
                else:
                    child.terminate()
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    if pidfd is not None:
                        signal.pidfd_send_signal(pidfd, signal.SIGKILL)
                    else:
                        child.kill()
                    child.wait(timeout=2)
            else:
                child.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            result["cleanup_error"] = True
        finally:
            for stream in (child.stdout, child.stderr):
                try:
                    stream.close()
                except OSError:
                    result["cleanup_error"] = True
            if pidfd is not None:
                try:
                    os.close(pidfd)
                except OSError:
                    result["cleanup_error"] = True
    result["returncode"] = child.returncode
    result["stdout"] = bytes(output)
    return result


def write_record(directory, name, value):
    data = (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode(
        "ascii"
    )
    require(len(data) <= LIMIT, "artifact_limit")
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(directory_fd)
        require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700, "artifact_directory")
        fd = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
    finally:
        os.close(directory_fd)


def load_collector(root, expected_hash):
    # exec_module may consume an unverified .pyc even under -B. Compile only the
    # captured bytes whose SHA256 was matched to the selected Git tree source.
    data, _ = read_source(root, COLLECTOR)
    require(hashlib.sha256(data).hexdigest() == expected_hash, "source_changed")
    module = ModuleType("reviewed_user_service_collector")
    module.__file__ = str(root / COLLECTOR)
    code = compile(data, module.__file__, "exec")
    exec(code, module.__dict__)  # noqa: S102 - exact hash-verified source-only module loading; never pyc.
    return module


def run(root, directory, context):
    status = {
        "schema": "forge-user-service-wrapper-status-v1",
        "status": "STOP",
        "reason": "not_started",
        "collector_exit_code": None,
        "collector_signal": None,
        "collector_stderr_bytes": 0,
        "errno": None,
        "receipt": "MISSING",
        "cleanup": "NOT_RUN",
        "source_rechecked": False,
        "boundary": "NOT_RUN",
        "qualification": "NOT_RUN",
        "authentication": "UNVERIFIED_DECLARED_CONTEXT",
    }
    hashes = None
    try:
        require(os.getuid() == os.geteuid() != 0 and os.getgid() == os.getegid() != 0, "ordinary_user")
        # Fail before collector start if the wrapper cannot bind its own child.
        fd = os.pidfd_open(os.getpid())
        os.close(fd)
        require(hasattr(signal, "pidfd_send_signal"), "pidfd_unavailable")
        hashes = source_hashes(root)
        git, python = verified_tool("/usr/bin/git"), verified_tool("/usr/bin/python3")
        prefix = [git, "--no-pager", "--no-optional-locks", "-c", "core.fsmonitor=false"]
        head = capture([*prefix, "rev-parse", "HEAD"], 5, root)
        tree = capture([*prefix, "ls-tree", "-z", "HEAD", "--", *EXECUTION_FILES], 5, root)
        for result in (head, tree):
            require(
                result["reason"] == "completed"
                and not result["cleanup_error"]
                and result["returncode"] == 0
                and result["stderr_bytes"] == 0,
                "checkout_mismatch",
            )
        require(head["stdout"] == (context["source_sha"] + "\n").encode("ascii"), "checkout_mismatch")
        verify_tree(root, tree["stdout"])
        require(source_hashes(root) == hashes, "source_changed")
        module = load_collector(root, hashes[COLLECTOR])
        envelope = {
            "schema": "forge-user-service-declared-context-v1",
            "authentication": "UNVERIFIED_DECLARED_CONTEXT",
            "context": context,
            "checkout_head": context["source_sha"],
            "checked_out_files_sha256": hashes,
            "hash_scope": "CHECKED_OUT_SOURCE_ONLY_NOT_EXECUTED_WORKFLOW_AUTHENTICATION",
        }
        write_record(directory, "declared-context.json", envelope)
        collector_context = {
            "source_sha": context["source_sha"],
            "collector_sha256": hashes[COLLECTOR],
            "run_id": context["run_id"],
            "run_attempt": context["run_attempt"],
        }
        argv = [
            python,
            "-I",
            "-S",
            "-B",
            str(root / COLLECTOR),
            "--allow-transient-service",
            "--source-sha",
            collector_context["source_sha"],
            "--collector-sha256",
            collector_context["collector_sha256"],
            "--run-id",
            collector_context["run_id"],
            "--run-attempt",
            str(collector_context["run_attempt"]),
        ]
        status["cleanup"] = "UNKNOWN"
        result = capture(argv, COLLECTOR_SECONDS, root)
        code = result["returncode"]
        status["collector_exit_code"] = code if code is not None and code >= 0 else None
        status["collector_signal"] = -code if code is not None and code < 0 else None
        status["collector_stderr_bytes"] = result["stderr_bytes"]
        status["source_rechecked"] = source_hashes(root) == hashes
        require(status["source_rechecked"], "source_changed")
        require(
            result["reason"] == "completed" and not result["cleanup_error"],
            "collector_" + result["reason"]
            if result["reason"] != "completed"
            else "collector_reap_unverified",
        )
        require(result["stderr_bytes"] == 0, "collector_stderr")
        if result["stdout"]:
            status["receipt"] = "INVALID"
            receipt = module.parse_receipt(result["stdout"])
            require(receipt["context"] == collector_context, "receipt_context_mismatch")
            require(
                receipt["owner_uid"] == os.getuid() and receipt["owner_gid"] == os.getgid(),
                "receipt_identity_mismatch",
            )
            status["receipt"] = "VALID"
            status["cleanup"] = receipt["cleanup"]["status"]
            write_record(directory, "observation.json", receipt)
            require(code == (0 if receipt["status"] == "OBSERVED" else 2), "collector_exit_mismatch")
            status["status"] = receipt["status"]
            status["reason"] = (
                "observation_complete" if receipt["status"] == "OBSERVED" else "collector_stop"
            )
        else:
            raise WrapperError("receipt_missing")
    except WrapperError as exc:
        status["reason"] = str(exc)
    except OSError as exc:
        status["reason"] = "io_error"
        status["errno"] = exc.errno
    except ValueError:
        status["reason"] = "receipt_or_metadata_invalid"
    except Exception:  # noqa: BLE001 - privacy boundary: fixed STOP, never arbitrary exception values.
        # Fail closed without serializing an unexpected exception or source text.
        status["reason"] = "wrapper_internal_error"
    write_record(directory, "collector-status.json", status)
    return 0 if status["status"] == "OBSERVED" else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    context = context_from_environment()
    return run(Path(__file__).resolve().parents[1], Path(args.output_dir), context)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 - CLI boundary: nonzero failure without raw source/value traceback.
        sys.stderr.write("workflow wrapper failed before structured output\n")
        sys.exit(2)
