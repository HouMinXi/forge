"""One-shot, fail-closed orchestration for the reviewed ephemeral-runner policy.

The fixed bootstrap must authenticate every helper before importing this module.
Importing it has no side effects. A launch PASS is not admission. No fallback Gate
exists: the CLI refuses to proceed when the real admission module is unavailable.
No workflow is installed or activated by this module.

Gate(manifest, repo, vendor_dir, evidence_dir) must expose prepare(),
recheck_before_load(receipt), verify_after_load(receipt), and
final_source_recheck(receipt). The latter three return PASS evidence with the same
binding or raise. Admission owns authentication of the vendor, compiler, utility,
include, runner, cgroup, fixture, dependency and attachment facts. The controller
owns the private empty evidence_dir/parser.conf and checks its bytes/mode/owner
before EVERY parser invocation. No receipt field supplies command options. The
second launch/API check plus final admission recheck must finish within 60 seconds
before the one-shot load; expiry stops rather than widening the freshness window.

Parser options are verified against the Ubuntu Noble apparmor_parser(8) manual
and upstream v4.0.1 parser/parser_main.c (warnflag_table, process_arg). --add is
add-only, --skip-cache disables read/write caching, --Werror first makes all
warnings fatal. Only compile/load append --Werror=no-rule-not-enforced; the
controller still accepts only two exact authenticated-profile io_uring warnings.
Preprocessing remains warning-free. --jobs=0 disables parallel workers. --preprocess and --stdout are separate
no-load commands. Sources:
https://manpages.ubuntu.com/manpages/noble/man8/apparmor_parser.8.html
https://gitlab.com/apparmor/apparmor/-/raw/v4.0.1/parser/parser_main.c

Finalization accepts ONLY direct receipts from a trusted same-run phase driver.
The fixed same-process driver runs only these selections; a pytest-produced
claim of controller success is never trusted.
Each receipt records phase_argv(), binding, integer exit_code, completed=true,
cancelled=false, timed_out=false, started/ended UTC+monotonic times, junit_sha256,
observer_sha256 (null outside full), log_sha256/log_bytes, and a null error.
The driver retains all three original
selections and their 20/40/5 minute caps. It continuously drains combined output
to private logs bounded at 64 MiB each. Only ordinary pytest test failures (exit 1) do not suppress
later phases; signal exits, interruption exit 2 and other unexpected codes stop. Cancellation, timeout, lost supervision or output overflow stops
all later phases conservatively, leaving the disposable VM unqualified.
The report-only full-suite observer uses
FORGE_CI_REQUIRED_EVENTS=<evidence_dir>/required-events.json. Artifacts must later
be uploaded and independently verified by the publisher; local success is never
reported as final qualified=true.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import hashlib
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
from typing import Any, Protocol

from . import launch, outcomes, payload, probes

PARSER = "/usr/sbin/apparmor_parser"
INCLUDE_BASE = "/etc/apparmor.d"
PROFILE_MEMBER = "apparmor-profiles/usr/share/apparmor/extra-profiles/bwrap-userns-restrict"
PARSER_LIMIT = 1024 * 1024
PRELOAD_FRESHNESS_SECONDS = 60
RECORD_LIMIT = 8 * 1024 * 1024
PARSER_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "LANG": "C", "HOME": "/nonexistent"}
BINDING_KEYS = {"nonce", "control_sha", "source_sha256", "run_id", "run_attempt", "job", "boot_id"}
PHASE_FILES = {"ownership": "ownership.xml", "full": "pytest.xml", "local-integration": "local-integration.xml"}
PHASE_LOG_LIMIT = 64 * 1024 * 1024
PHASE_SECONDS = {"ownership": 1200, "full": 2400, "local-integration": 300}


class ControllerError(RuntimeError):
    """Missing, contradictory, failed or incomplete evidence requires STOP."""


class Cancelled(ControllerError):
    """A termination signal is unqualified even if policy was already added."""


class Gate(Protocol):
    def prepare(self) -> dict: ...
    def recheck_before_load(self, receipt: dict) -> dict: ...
    def verify_after_load(self, receipt: dict) -> dict: ...
    def final_source_recheck(self, receipt: dict) -> dict: ...


def need(condition: bool, message: str) -> None:
    if not condition:
        raise ControllerError(message)


def stamp() -> dict:
    return {"utc_ns": time.time_ns(), "monotonic_ns": time.monotonic_ns()}


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _regular(path: Path, limit: int) -> bytes:
    return launch.read_regular(path, limit=limit)


def _private_file(path: Path, expected: bytes) -> None:
    details = path.lstat()
    need(stat.S_ISREG(details.st_mode) and details.st_uid == os.getuid()
         and stat.S_IMODE(details.st_mode) == 0o600 and details.st_nlink == 1,
         "parser configuration is not a private owned regular file")
    need(_regular(path, len(expected) + 1) == expected, "parser configuration changed")


class Evidence:
    """Fresh owner-only directory, exclusive immutable stage records, no resume."""
    def __init__(self, path: Path):
        path = path.absolute()
        need(path.parent.resolve(strict=True) == path.parent, "evidence parent is noncanonical")
        path.mkdir(mode=0o700)  # EEXIST, including an old empty directory, is STOP.
        self.path = path
        details = path.lstat()
        self.identity = (details.st_dev, details.st_ino)
        self.sequence = 0
        self.check()
        self.write("parser.conf", b"")

    def check(self) -> None:
        details = self.path.lstat()
        need(self.path.parent.resolve(strict=True) == self.path.parent
             and (details.st_dev, details.st_ino) == self.identity, "evidence directory identity changed")
        need(stat.S_ISDIR(details.st_mode) and details.st_uid == os.getuid()
             and stat.S_IMODE(details.st_mode) == 0o700, "evidence directory ownership/mode changed")

    def write(self, name: str, raw: bytes) -> None:
        self.check()
        need(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", name) is not None,
             "invalid evidence filename")
        need(type(raw) is bytes and len(raw) <= RECORD_LIMIT, "evidence byte bound exceeded")
        fd = os.open(self.path / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())

    def json(self, name: str, record: Any) -> None:
        self.write(name, launch.canonical_bytes(record) + b"\n")

    def record(self, stage: str, record: Any) -> None:
        self.sequence += 1
        need(self.sequence <= 64, "too many controller stages")
        self.json(f"{self.sequence:02d}-{stage}.json", record)


def parser_argv(operation: str, profile: Path, config: Path) -> list[str]:
    """Fixed finite operations; this is not a general privileged command runner."""
    need(operation in {"preprocess", "compile", "load"}, "unknown parser operation")
    need(profile.is_absolute() and profile.name == "bwrap-userns-restrict", "invalid vendor profile path")
    need(config.is_absolute() and config.name == "parser.conf", "invalid parser configuration path")
    options = ["--config-file=" + str(config), "--base=" + INCLUDE_BASE, "--Include=" + INCLUDE_BASE,
               "--skip-cache", "--warn=all", "--Werror", "--abort-on-error", "--jobs=0"]
    if operation != "preprocess":
        options.append("--Werror=no-rule-not-enforced")
    if operation == "preprocess":
        options += ["--skip-kernel-load", "--preprocess"]
    elif operation == "compile":
        options += ["--skip-kernel-load", "--stdout"]
    else:
        options += ["--add"]
    prefix = ["/usr/bin/timeout", "--signal=KILL", "20s", PARSER]
    if operation == "load":
        # Root-owned timeout kills the privileged descendant even if this
        # unprivileged collector is cancelled or cannot signal that descendant.
        prefix = ["/usr/bin/sudo", "-n", "--", *prefix]
    return [*prefix, *options, "--", str(profile)]


def _raw_stream(record: dict, name: str, limit: int) -> bytes:
    encoded = record.get(name + "_hex")
    need(type(encoded) is str and len(encoded) <= 2 * limit, "missing or oversized raw " + name)
    try:
        raw = bytes.fromhex(encoded)
    except ValueError as exc:
        raise ControllerError("invalid raw " + name) from exc
    need(raw.hex() == encoded and len(raw) <= limit, "noncanonical raw " + name)
    # Binary compiler stdout is preserved, not parsed via this display string.
    need(record.get(name) == raw.decode("utf-8", errors="replace"), "display/raw disagreement: " + name)
    return raw


def _times(started: Any, ended: Any, limit: float) -> None:
    for value in (started, ended):
        need(type(value) is dict and set(value) == {"utc_ns", "monotonic_ns"}, "invalid operation time fields")
        need(all(type(item) is int and item > 0 for item in value.values()), "invalid operation time")
    need(started["utc_ns"] <= ended["utc_ns"], "operation UTC time reversed")
    elapsed = ended["monotonic_ns"] - started["monotonic_ns"]
    need(0 <= elapsed <= int(limit * 1_000_000_000), "operation duration outside bound")


def _probe_command(record: dict) -> None:
    need(type(record.get("wrapper_pid")) is int and record["wrapper_pid"] > 0,
         "missing actual probe wrapper PID")
    _times(record.get("started"), record.get("ended"), 30)
    stdout = _raw_stream(record, "stdout", payload.MAX_COMMAND_OUTPUT)
    stderr = _raw_stream(record, "stderr", payload.MAX_COMMAND_OUTPUT)
    need(len(stdout) + len(stderr) <= payload.MAX_COMMAND_OUTPUT, "probe command output exceeded bound")
    try:
        stdout.decode("utf-8")
        stderr.decode("utf-8")
    except UnicodeError as exc:
        raise ControllerError("probe command text is not valid UTF-8") from exc


def validate_parser(record: dict, argv: list[str], operation: str) -> tuple[bytes, bytes]:
    need(type(record) is dict and record.get("argv") == argv, "parser argv changed")
    need(not record.get("error") and type(record.get("returncode")) is int and record["returncode"] == 0,
         "parser failed, timed out or returned incomplete evidence")
    _times(record.get("started"), record.get("ended"), 30)
    stdout = _raw_stream(record, "stdout", PARSER_LIMIT)
    stderr = _raw_stream(record, "stderr", PARSER_LIMIT)
    need(len(stdout) + len(stderr) <= PARSER_LIMIT, "parser output exceeded combined bound")
    if operation == "preprocess":
        need(not stderr, "preprocessor emitted warnings or other stderr")
    else:
        # The parser category also covers other rule classes. Only these two
        # exact nonfatal diagnostics from the authenticated vendor path qualify.
        expected = [f"Warning from profile {name} ({argv[-1]}): io_uring rules not enforced\n".encode("utf-8")
                    for name in ("bwrap", "unpriv_bwrap")]
        need(sorted(stderr.splitlines(keepends=True)) == sorted(expected),
             "missing, duplicate or unreviewed parser diagnostic")
    if operation != "load":
        need(bool(stdout), "missing preprocessed/compiled policy output")
    if operation != "compile":
        try:
            text = stdout.decode("utf-8")
        except UnicodeError as exc:
            raise ControllerError("parser text is not valid UTF-8") from exc
        if operation == "load":
            need(not re.search(r"warning|error|failed|not enforced|downgrad", text, re.IGNORECASE),
                 "load output reported a warning or failure")
    return stdout, stderr


class Runtime:
    """Only reviewed fixed helpers and one fixed parser operation are executable."""
    def __init__(self, document: dict, repo: Path, manifest: Path):
        self.document, self.repo, self.manifest = document, repo, manifest

    def launch(self) -> dict:
        event_path = os.environ.get("GITHUB_EVENT_PATH")
        need(type(event_path) is str and bool(event_path), "missing native event path")
        event = launch.parse_json(_regular(Path(event_path), launch.MAX_API), limit=launch.MAX_API)
        checkout = launch.inspect_checkout(self.repo, self.document, manifest_path=self.manifest)
        return launch.validate_launch(self.document, os.environ, event, checkout)

    def boot_id(self) -> str:
        raw = _regular(Path("/proc/sys/kernel/random/boot_id"), 64)
        value = raw.decode("ascii").strip()
        need(re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value) is not None,
             "invalid current boot identity")
        return value

    def parser(self, operation: str, profile: Path, config: Path) -> dict:
        _private_file(config, b"")
        return payload.bounded_command(parser_argv(operation, profile, config), 25,
                                       env=dict(PARSER_ENV), limit=PARSER_LIMIT)

    def negative(self, evidence: Path) -> dict:
        return probes.run_negative_control(evidence)

    def positive(self, evidence: Path) -> dict:
        return probes.run_positive_probe(evidence)

    def boundary(self, cgroup_root: str, evidence: Path) -> dict:
        return probes.run_boundary_probe(cgroup_root, evidence)

    def python_context(self, cgroup_root: str, evidence: Path) -> dict:
        return probes.run_python_context_probe(cgroup_root, evidence, repo=self.repo)

    def phase(self, name: str, evidence: Evidence, binding: dict) -> dict:
        return run_phase(name, self.repo, evidence, binding)


def _binding(launch_receipt: dict, boot_id: str) -> dict:
    need(type(launch_receipt) is dict and launch_receipt.get("status") == "PASS"
         and launch_receipt.get("policy_authorized") is False, "launch identity gate did not pass")
    value = {name: launch_receipt.get(name) for name in BINDING_KEYS - {"boot_id"}}
    value["boot_id"] = boot_id
    need(re.fullmatch(r"[0-9a-f]{32}", str(value["nonce"])) is not None, "invalid nonce binding")
    for field, length in (("control_sha", 40), ("source_sha256", 64)):
        need(type(value[field]) is str and re.fullmatch(r"[0-9a-f]{" + str(length) + "}", value[field]) is not None,
             "invalid source/control binding")
    for field in ("run_id", "run_attempt"):
        need(type(value[field]) is int and value[field] > 0, "invalid run binding")
    need(type(value["job"]) is str and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,99}", value["job"]) is not None,
         "invalid job binding")
    need(type(boot_id) is str and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", boot_id) is not None,
         "invalid boot binding")
    return value


def phase_argv(phase: str, evidence: Path) -> list[str]:
    """Exact current three selections; only report-only observation is added."""
    need(phase in PHASE_FILES, "unknown test phase")
    if phase == "ownership":
        args = ["-v", "-ra", "-p", "no:cacheprovider", "tests/test_mutation_process.py",
                "tests/test_mutation_process_capability.py", "tests/test_mutation_cancellation.py",
                "tests/test_mcp_simple_cancel.py", "tests/test_mcp_budgeted_cancel.py"]
    elif phase == "full":
        args = ["-q", "-ra", "-m", "not real_api and not integration", "-p", "no:cacheprovider",
                "-p", "forge_ci.pytest_observer"]
    else:
        args = ["-v", "-ra", "-m", "integration", "-p", "no:cacheprovider",
                "tests/test_lock_signals.py", "tests/test_mutation_detach_integration.py"]
    return ["python", "-m", "pytest", *args, "--junitxml=" + str(evidence / PHASE_FILES[phase])]


def _kill_owned_phase(process) -> None:
    """Only this Popen's new session/process group; no name/global process kill."""
    # A successful wait has reaped the leader and released its numeric PID.
    # Never signal that potentially reused PGID after reaping. Do not poll here:
    # polling would itself reap an exited leader before the signal decision.
    if process.returncode is not None:
        return
    need(type(process.pid) is int and process.pid > 0, "missing owned phase process group")
    # A Python signal may interrupt wait after waitpid reaps but before Popen
    # assigns returncode. WNOWAIT checks child ownership without reaping an
    # exited leader; its PID stays reserved until we signal this owned group.
    # The controller is single-threaded here and starts no intervening child.
    need(all(hasattr(os, name) for name in ("waitid", "P_PID", "WEXITED", "WNOHANG", "WNOWAIT")),
         "non-reaping child-ownership check is unavailable")
    try:
        observed = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except ChildProcessError:
        return  # Already reaped, including the interrupted returncode gap.
    need(observed is None or observed.si_pid == process.pid, "ambiguous phase child ownership")
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass  # It exited between timeout detection and the signal; still reap.
    process.wait(timeout=5)


def run_phase(name: str, repo: Path, evidence: Evidence, binding: dict) -> dict:
    """Fixed three-phase driver. There is deliberately no arbitrary argv input."""
    argv = phase_argv(name, evidence.path)
    evidence.check()
    log_name = Path(PHASE_FILES[name]).with_suffix(".log").name
    receipt = {"phase": name, "binding": copy.deepcopy(binding), "argv": argv, "exit_code": None,
               "completed": False, "cancelled": False, "timed_out": False, "started": stamp(),
               "ended": None, "junit_sha256": None, "observer_sha256": None,
               "log_sha256": None, "log_bytes": 0, "error": None}
    process = None
    hasher = hashlib.sha256()
    deadline = time.monotonic() + PHASE_SECONDS[name]
    try:
        print(f"PHASE {name}: starting ({PHASE_SECONDS[name]}s cap)", flush=True)
        fd = os.open(evidence.path / log_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as log:
            need(os.getuid() > 0 and os.getgid() > 0 and os.getuid() == os.geteuid()
                 and os.getgid() == os.getegid(), "test phases must remain the ordinary non-root user")
            expected = [PHASE_FILES[name]] + (["required-events.json"] if name == "full" else [])
            for filename in expected:
                path = evidence.path / filename
                need(not path.exists() and not path.is_symlink(), "test phase evidence must be fresh")
            # Preserve the admitted natural test/tool environment and execute
            # the current authenticated interpreter, not a new PATH substitute.
            environment = dict(os.environ)
            environment.pop("FORGE_CI_REQUIRED_EVENTS", None)
            if name == "full":
                environment["FORGE_CI_REQUIRED_EVENTS"] = str(evidence.path / "required-events.json")
            process = subprocess.Popen(argv, executable=sys.executable, cwd=repo,
                                       env=environment, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       start_new_session=True)
            os.set_blocking(process.stdout.fileno(), False)
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        receipt["timed_out"] = True
                        raise ControllerError("test phase deadline exceeded")
                    for key, _ in selector.select(min(remaining, 0.2)):
                        raw = os.read(key.fileobj.fileno(), 65536)
                        if not raw:
                            selector.unregister(key.fileobj)
                            continue
                        available = PHASE_LOG_LIMIT - receipt["log_bytes"]
                        retained = raw[:available]
                        log.write(retained)
                        hasher.update(retained)
                        receipt["log_bytes"] += len(retained)
                        need(len(raw) <= available, "test phase output exceeded the 64 MiB log bound")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    receipt["timed_out"] = True
                    raise ControllerError("test phase deadline exceeded")
                process.wait(timeout=remaining)
            log.flush()
            os.fsync(log.fileno())
            receipt["exit_code"] = process.returncode
            if type(process.returncode) is int and process.returncode in (0, 1):
                # Pytest 1 is an ordinary test failure; later original phases
                # still run. Signals, interruption (2), and all other unknown
                # terminal outcomes must not launch any later phase.
                receipt["completed"] = True
            else:
                receipt["cancelled"] = type(process.returncode) is int and (
                    process.returncode < 0 or process.returncode == 2)
                receipt["error"] = "pytest interrupted or returned an unexpected exit: " + str(process.returncode)
    except BaseException as exc:  # noqa: BLE001 - persist interrupted operation before STOP
        receipt["cancelled"] = isinstance(exc, (Cancelled, KeyboardInterrupt, SystemExit))
        receipt["timed_out"] = receipt["timed_out"] or isinstance(exc, subprocess.TimeoutExpired)
        receipt["error"] = type(exc).__name__ + ": " + str(exc)[:512]
        if process is not None:
            try:
                _kill_owned_phase(process)
            except (OSError, subprocess.TimeoutExpired, ControllerError) as cleanup:
                receipt["error"] += "; failed to terminate/reap owned phase: " + type(cleanup).__name__
            receipt["exit_code"] = process.returncode
    finally:
        if process is not None and process.stdout is not None:
            process.stdout.close()
        receipt["ended"] = stamp()
        receipt["log_sha256"] = hasher.hexdigest()
        # Failed/aborted phases may not have emitted a complete JUnit/observer.
        # That is not repaired or accepted: finalization requires both on PASS.
        for key, file, limit in (("junit_sha256", PHASE_FILES[name], outcomes.MAX_JUNIT_BYTES),
                                 ("observer_sha256", "required-events.json", outcomes.MAX_EVENTS_BYTES)):
            if key == "observer_sha256" and name != "full":
                continue
            try:
                receipt[key] = sha256(_regular(evidence.path / file, limit))
            except (OSError, launch.LaunchError):
                receipt[key] = None
        evidence.json(name + "-phase.json", receipt)
        duration = (receipt["ended"]["monotonic_ns"] - receipt["started"]["monotonic_ns"]) / 1_000_000_000
        print(f"PHASE {name}: {duration:.3f}s; exit={receipt['exit_code']}; completed={receipt['completed']}", flush=True)
    return receipt


def _phase_junit(raw: bytes, *, no_skips: bool) -> int:
    root = outcomes._load_junit(raw)
    cases = list(root.iter("testcase"))
    need(bool(cases), "phase collected no testcases")
    for case in cases:
        need(case.find("failure") is None and case.find("error") is None, "phase JUnit has failure/error")
        if no_skips:
            need(case.find("skipped") is None, "ownership/cancellation cases were skipped")
        need(all(child.tag in {"properties", "system-out", "system-err", "skipped"} for child in case),
             "phase JUnit contains unknown outcome")
    for suite in root.iter():
        if suite.tag not in {"testsuites", "testsuite"}:
            continue
        contained = list(suite.iter("testcase"))
        counts = {"tests": len(contained), "failures": 0, "errors": 0,
                  "skipped": sum(case.find("skipped") is not None for case in contained)}
        for key, count in counts.items():
            if key in suite.attrib:
                need(suite.attrib[key].isascii() and suite.attrib[key].isdigit()
                     and int(suite.attrib[key]) == count, "phase JUnit summary disagrees")
    return len(cases)


class Controller:
    """Pure orchestration with injectable trusted Gate/Runtime for offline tests."""
    def __init__(self, gate: Gate, runtime: Runtime, evidence: Evidence, vendor_dir: Path):
        self.gate, self.runtime, self.evidence = gate, runtime, evidence
        self.vendor_dir = vendor_dir.resolve(strict=True)
        self.binding: dict = {}
        self.receipt: dict | None = None
        self.state = "CREATED"
        self.load_attempted = False
        self.preload_deadline: float | None = None
        self.ready: dict | None = None
        self.started = stamp()
        self.evidence.record("created", {"status": self.state, "started": self.started})

    def _capture(self, name: str, callback) -> Any:
        self.state = name
        self.evidence.record(name + "-started", {"started": stamp(), "binding": self.binding})
        result = copy.deepcopy(callback())
        self.evidence.record(name, result)
        return result

    def _gate_result(self, result: dict) -> None:
        need(type(result) is dict and result.get("status") == "PASS"
             and type(result.get("binding")) is dict
             and launch.canonical_bytes(result["binding"]) == launch.canonical_bytes(self.binding),
             "admission gate failed or binding changed")

    def _admission(self, result: dict) -> None:
        self._gate_result(result)
        need(set(result) == {"schema_version", "status", "vendor_profile", "cgroup_root", "binding"}
             and type(result["schema_version"]) is int and result["schema_version"] == 1,
             "malformed admission receipt")
        profile = self.vendor_dir / PROFILE_MEMBER
        need(type(result["vendor_profile"]) is str and result["vendor_profile"] == str(profile)
             and profile.resolve(strict=True) == profile and profile.is_file(), "vendor path is not fixed/canonical")
        uid = os.getuid()
        need(result["cgroup_root"] == f"/sys/fs/cgroup/user.slice/user-{uid}.slice/user@{uid}.service",
             "cgroup root is not the ordinary runner subtree")

    def _fresh_load_window(self) -> None:
        need(self.preload_deadline is not None and time.monotonic() <= self.preload_deadline,
             "fresh launch/admission recheck exceeded the 60-second pre-load window")

    def _parser(self, operation: str) -> dict:
        need(self.receipt is not None, "no admission receipt")
        profile, config = Path(self.receipt["vendor_profile"]), self.evidence.path / "parser.conf"
        _private_file(config, b"")
        argv = parser_argv(operation, profile, config)
        self.evidence.record(operation + "-input", {"argv": argv, "config_sha256": sha256(b""),
                                                    "environment": PARSER_ENV, "binding": self.binding})
        def execute():
            if operation == "load":
                self._fresh_load_window()
            return self.runtime.parser(operation, profile, config)
        record = self._capture(operation, execute)
        stdout, stderr = validate_parser(record, argv, operation)
        self.evidence.write(operation + ".stdout", stdout)
        self.evidence.write(operation + ".stderr", stderr)
        return record

    def _stop(self, error: BaseException, *, not_needed: bool = False) -> dict:
        failed_stage = self.state
        self.state = "STOP_NOT_NEEDED" if not_needed else "STOP"
        result = {"schema_version": 1, "status": self.state, "qualified": False,
                  "qualification_complete": False, "evidence_upload_verified": False,
                  "binding": self.binding, "failed_stage": failed_stage,
                  "load_attempted": self.load_attempted,
                  "error_type": type(error).__name__, "error": str(error)[:1024], "ended": stamp()}
        # If this write itself fails, the exception propagates. Never claim that
        # evidence was preserved after an unsuccessful preservation operation.
        self.evidence.json("stop.json", result)
        return result

    def qualify(self) -> dict:
        try:
            need(self.state == "CREATED", "controller cannot resume or retry qualification")
            need(os.getuid() > 0 and os.getgid() > 0 and os.getuid() == os.geteuid()
                 and os.getgid() == os.getegid(), "controller must remain the ordinary non-root user")
            initial = self._capture("launch-initial", self.runtime.launch)
            self.binding = _binding(initial, self.runtime.boot_id())
            self.receipt = self._capture("admission", self.gate.prepare)
            self._admission(self.receipt)
            negative = self._capture("negative", lambda: self.runtime.negative(self.evidence.path))
            need(type(negative) is dict and type(negative.get("returncode")) is int
                 and not negative.get("error"), "incomplete negative-control operation")
            _probe_command(negative)
            if negative.get("already_capable") is True:
                need(negative["returncode"] == 0, "contradictory already-capable evidence")
                return self._stop(ControllerError("production pre-probe is already capable; policy not exercised"),
                                  not_needed=True)
            need(negative.get("already_capable") is False, "missing negative-control capability decision")
            probes.validate_negative_control(negative)
            self._parser("preprocess")
            self._parser("compile")
            self.preload_deadline = time.monotonic() + PRELOAD_FRESHNESS_SECONDS
            fresh = self._capture("launch-preload", self.runtime.launch)
            need(_binding(fresh, self.runtime.boot_id()) == self.binding, "launch/boot identity changed before load")
            recheck = self._capture("admission-preload", lambda: self.gate.recheck_before_load(self.receipt))
            self._gate_result(recheck)
            self._admission(self.receipt)
            self._fresh_load_window()
            # Set and persist this BEFORE entering the privileged call. Any lost
            # response is a possibly partial add and cannot be retried.
            self.load_attempted = True
            self._parser("load")
            post = self._capture("postconditions", lambda: self.gate.verify_after_load(self.receipt))
            self._gate_result(post)
            self._admission(self.receipt)
            positive = self._capture("positive", lambda: self.runtime.positive(self.evidence.path))
            need(type(positive) is dict and positive.get("argv") == probes.production_probe_argv()
                 and not positive.get("error") and type(positive.get("returncode")) is int
                 and positive["returncode"] == 0, "unchanged positive production probe failed")
            _probe_command(positive)
            boundary = self._capture("boundary", lambda: self.runtime.boundary(
                self.receipt["cgroup_root"], self.evidence.path))
            probes.validate_boundary_record(boundary)
            context = self._capture("python-context", lambda: self.runtime.python_context(
                self.receipt["cgroup_root"], self.evidence.path))
            context_gate = self._capture("python-context-admission", lambda:
                self.gate.verify_python_context(self.receipt, context))
            self._gate_result(context_gate)
            self._admission(self.receipt)
            self.state = "QUALIFICATION_READY"
            self.ready = {"schema_version": 1, "status": self.state, "qualified": False,
                          "qualification_complete": False, "full_suite_passed": False,
                          "evidence_upload_verified": False, "binding": dict(self.binding),
                          "load_attempted": True, "boundary_proved": True,
                          "python_context_proved": True, "ended": stamp()}
            self.evidence.json("ready.json", self.ready)
            return copy.deepcopy(self.ready)
        except BaseException as exc:  # noqa: BLE001 - cancellation/SystemExit must persist STOP
            return self._stop(exc)

    def run_tests(self) -> dict:
        """Run exact phases in this live controller, with independent durations."""
        try:
            need(self.state == "QUALIFICATION_READY", "tests require this controller's proved boundary")
            receipts = []
            for phase in PHASE_FILES:
                receipt = self._capture("phase-" + phase, lambda phase=phase: self.runtime.phase(
                    phase, self.evidence, self.binding))
                need(type(receipt) is dict and receipt.get("phase") == phase,
                     "missing direct fixed-phase receipt")
                need(receipt.get("completed") is True and receipt.get("cancelled") is False
                     and receipt.get("timed_out") is False and receipt.get("error") is None,
                     "test phase interrupted, timed out or lost bounded supervision")
                need(type(receipt.get("exit_code")) is int and receipt["exit_code"] in (0, 1),
                     "pytest signal, interruption or unexpected exit stops subsequent phases")
                # Only ordinary pytest failure (exit 1) permits later phases.
                # The finalizer refuses overall success unless every exit is 0.
                receipts.append(receipt)
            self.state = "QUALIFICATION_READY"
        except BaseException as exc:  # noqa: BLE001 - never continue phases after interruption
            return self._stop(exc)
        return self.finalize(receipts)

    def finalize(self, phase_receipts: list[dict]) -> dict:
        """Consume direct trusted-driver results, never run or alter selections."""
        try:
            need(self.state == "QUALIFICATION_READY" and self.ready is not None and self.receipt is not None,
                 "finalization requires this controller's live ready state")
            need(type(phase_receipts) is list and len(phase_receipts) == len(PHASE_FILES), "missing or duplicate phase")
            need([item.get("phase") for item in phase_receipts] == list(PHASE_FILES), "test phase identity/order mismatch")
            phase_receipts = copy.deepcopy(phase_receipts)
            previous = self.ready["ended"]["monotonic_ns"]
            summaries = {}
            for receipt in phase_receipts:
                self.state = "tests-" + receipt["phase"]
                self.evidence.record(self.state, receipt)
                need(set(receipt) == {"phase", "binding", "argv", "exit_code", "completed", "cancelled", "timed_out",
                                      "started", "ended", "junit_sha256", "observer_sha256",
                                      "log_sha256", "log_bytes", "error"}, "malformed phase receipt")
                phase = receipt["phase"]
                need(type(receipt["binding"]) is dict
                     and launch.canonical_bytes(receipt["binding"]) == launch.canonical_bytes(self.binding),
                     "test phase belongs to another run/boot/source")
                need(receipt["argv"] == phase_argv(phase, self.evidence.path), "test selection changed")
                need(type(receipt["exit_code"]) is int and receipt["exit_code"] == 0
                     and receipt["completed"] is True and receipt["cancelled"] is False
                     and receipt["timed_out"] is False and receipt["error"] is None,
                     "test phase failed or did not complete")
                _times(receipt["started"], receipt["ended"], PHASE_SECONDS[phase])
                need(receipt["started"]["monotonic_ns"] >= previous, "stale, overlapping or pre-boundary test receipt")
                previous = receipt["ended"]["monotonic_ns"]
                need(previous <= time.monotonic_ns(), "test receipt ends in the future")
                log = _regular(self.evidence.path / Path(PHASE_FILES[phase]).with_suffix(".log"), PHASE_LOG_LIMIT)
                need(type(receipt["log_bytes"]) is int and receipt["log_bytes"] == len(log)
                     and receipt["log_sha256"] == sha256(log), "phase log digest/length changed")
                raw = _regular(self.evidence.path / PHASE_FILES[phase], outcomes.MAX_JUNIT_BYTES)
                need(sha256(raw) == receipt["junit_sha256"], "phase JUnit digest changed")
                summaries[phase] = {"cases": _phase_junit(raw, no_skips=phase == "ownership"),
                                    "junit_sha256": sha256(raw)}
                if phase == "full":
                    events = _regular(self.evidence.path / "required-events.json", outcomes.MAX_EVENTS_BYTES)
                    need(sha256(events) == receipt["observer_sha256"], "observer digest changed")
                else:
                    need(receipt["observer_sha256"] is None, "unexpected phase observer evidence")
            required = self._capture("required-outcomes", lambda: outcomes.validate_outcomes(
                self.evidence.path / "pytest.xml", self.evidence.path / "required-events.json"))
            need(required.get("status") == "PASS" and required.get("qualified") is True,
                 "required eleven-node outcome gate failed")
            need(required.get("sha256") == {"junit": phase_receipts[1]["junit_sha256"],
                                           "events": phase_receipts[1]["observer_sha256"]},
                 "outcome files changed during validation")
            live = self._capture("launch-final", self.runtime.launch)
            need(_binding(live, self.runtime.boot_id()) == self.binding, "final run/boot/source binding changed")
            source = self._capture("source-final", lambda: self.gate.final_source_recheck(self.receipt))
            self._gate_result(source)
            self.state = "TESTS_PASSED"
            result = {"schema_version": 1, "status": self.state, "qualified": False,
                      "qualification_complete": True, "full_suite_passed": True,
                      "evidence_upload_verified": False, "binding": self.binding, "phases": summaries,
                      "required_count": len(outcomes.REQUIRED_NODEIDS), "ended": stamp()}
            self.evidence.json("tests-passed.json", result)
            return result
        except BaseException as exc:  # noqa: BLE001 - incomplete finalization never qualifies
            return self._stop(exc)


@contextmanager
def cancellation_guard():
    """The CLI records TERM/INT as STOP; SIGKILL/missing result stays unqualified."""
    def cancelled(signum, frame):
        raise Cancelled(f"controller cancelled by signal {signum}")
    previous = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
    try:
        for number in previous:
            signal.signal(number, cancelled)
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--vendor-dir", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args(argv)
    evidence = None
    try:
        with cancellation_guard():
            evidence = Evidence(args.evidence)
            # Missing admission code is a terminal error, never a synthetic PASS.
            from .admission import Gate as AdmissionGate
            document = launch.load_manifest(args.manifest)
            gate = AdmissionGate(document, args.repo, args.vendor_dir, evidence.path)
            controller = Controller(gate, Runtime(document, args.repo, args.manifest), evidence, args.vendor_dir)
            result = controller.qualify()
            if result["status"] == "QUALIFICATION_READY":
                result = controller.run_tests()
            print(result["status"] + ": artifact upload verification remains a separate publisher gate")
            return 0 if result["status"] == "TESTS_PASSED" else 1

    except BaseException as exc:  # noqa: BLE001 - cancellation/import failures stay unqualified
        if evidence is not None and not (evidence.path / "stop.json").exists():
            evidence.json("stop.json", {"schema_version": 1, "status": "STOP", "qualified": False,
                                        "error_type": type(exc).__name__, "error": str(exc)[:1024]})
        print("STOP: controller startup or evidence preservation failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
