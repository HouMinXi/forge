"""Late-only production proof and fixed three-phase supervision.

The fixed workflow-owned system bootstrap must finish before checkout/install.
This controller has no policy parser, loader, negative-control or replacement
operation. Admission verifies its sealed same-run root-owned setup receipt and
fixed read-only observer before the production proof, and again at finalization.

The original ownership/full selections, reviewed local FIXVAL additions and
existing timeout/ownership/output bounds remain mandatory. Phase-bound observer
results must reconcile all 27 cases. Local TESTS_PASSED still needs
independent publisher artifact verification before qualification is claimed.
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

from . import launch, outcomes, payload, probes, setup_policy

RECORD_LIMIT = 8 * 1024 * 1024
PHASE_FILES = {"ownership": "ownership.xml", "full": "pytest.xml", "local-integration": "local-integration.xml"}
PHASE_LOG_LIMIT = 64 * 1024 * 1024
PHASE_SECONDS = {"ownership": 1200, "full": 2400, "local-integration": 300}
LOCAL_INTEGRATION_HEADROOM_SECONDS = 200
JOB_SECONDS = 9000
CLEANUP_SECONDS = 60
ARTIFACT_SECONDS = 300
FINALIZATION_SECONDS = 360
_active_finalization = None


class ControllerError(RuntimeError):
    """Missing, contradictory, failed or incomplete evidence requires STOP."""


class Cancelled(BaseException):
    """A termination signal is unqualified even if policy was already added."""
    # The product owned-command API preserves control BaseExceptions through
    # cleanup; ordinary Exceptions are translated into owner failures.
    _forge_control = True


class FinalizationTimeout(ControllerError):
    """The one finalization window expired; no late success is acceptable."""


class FinalizationWindow:
    """Main-thread deadline plus cooperative checks; service backstops remain.

    An alarm interrupts interruptible work, not uninterruptible kernel I/O.
    Missing/late evidence remains STOP, including when the backstop must act.
    """
    def __init__(self, deadline_ns: int):
        need(type(deadline_ns) is int and deadline_ns > 0, "invalid final deadline")
        self.deadline_ns = deadline_ns
        self.first_control = None
        self.previous_handler = None

    def check(self):
        if time.monotonic_ns() >= self.deadline_ns:
            raise self.first_control or FinalizationTimeout("FINALIZATION_TIMEOUT")

    def _alarm(self, _signum, _frame):
        self.check()
        # A signal delivered slightly early must not disable the absolute check.
        signal.setitimer(signal.ITIMER_REAL, max(0.000001, (self.deadline_ns - time.monotonic_ns()) / 1e9))

    def __enter__(self):
        global _active_finalization  # noqa: PLW0603 - one fixed main-thread finalizer
        need(_active_finalization is None, "nested finalization window")
        need(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0), "existing alarm would be replaced")
        self.check()
        self.previous_handler = signal.getsignal(signal.SIGALRM)
        signal.signal(signal.SIGALRM, self._alarm)
        _active_finalization = self
        signal.setitimer(signal.ITIMER_REAL, max(0.000001, (self.deadline_ns - time.monotonic_ns()) / 1e9))
        return self

    def __exit__(self, kind, error, traceback):
        global _active_finalization  # noqa: PLW0603 - restore the fixed main-thread guard
        try:
            if error is None:
                self.check()
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, self.previous_handler)
            _active_finalization = None
        return False


def _check_final_time():
    if _active_finalization is not None:
        _active_finalization.check()


def _final_deadline_seconds():
    return None if _active_finalization is None else _active_finalization.deadline_ns / 1e9


class Gate(Protocol):
    def prepare(self) -> dict: ...
    def final_source_recheck(self, receipt: dict) -> dict: ...


def need(condition: bool, message: str) -> None:
    if not condition:
        raise ControllerError(message)


def stamp() -> dict:
    return {"utc_ns": time.time_ns(), "monotonic_ns": time.monotonic_ns()}


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _regular(path: Path, limit: int) -> bytes:
    _check_final_time()
    options = {} if _active_finalization is None else {"deadline": _final_deadline_seconds()}
    result = launch.read_regular(path, limit=limit, **options)
    _check_final_time()
    return result


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
        self.deadline_ns = None
        self.check()

    def check(self) -> None:
        _check_final_time()
        need(self.deadline_ns is None or time.monotonic_ns() < self.deadline_ns, "FINALIZATION_TIMEOUT")
        details = self.path.lstat()
        need(self.path.parent.resolve(strict=True) == self.path.parent
             and (details.st_dev, details.st_ino) == self.identity, "evidence directory identity changed")
        need(stat.S_ISDIR(details.st_mode) and details.st_uid == os.getuid()
             and stat.S_IMODE(details.st_mode) == 0o700, "evidence directory ownership/mode changed")
        _check_final_time()
        need(self.deadline_ns is None or time.monotonic_ns() < self.deadline_ns, "FINALIZATION_TIMEOUT")

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
        self.check()

    def json(self, name: str, record: Any) -> None:
        self.write(name, launch.canonical_bytes(record) + b"\n")

    def record(self, stage: str, record: Any) -> None:
        self.sequence += 1
        need(self.sequence <= 64, "too many controller stages")
        self.json(f"{self.sequence:02d}-{stage}.json", record)


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


class Runtime:
    """Fixed read-only launch checks, production proof and test phases only."""
    def __init__(self, document: dict, repo: Path):
        self.document, self.repo = document, repo

    def launch(self) -> dict:
        event_path = os.environ.get("GITHUB_EVENT_PATH")
        need(type(event_path) is str and bool(event_path), "missing native event path")
        event = launch.parse_json(_regular(Path(event_path), launch.MAX_API), limit=launch.MAX_API)
        options = {} if _active_finalization is None else {"deadline": _final_deadline_seconds()}
        checkout = launch.inspect_checkout(self.repo, self.document["binding"]["candidate_sha"], **options)
        result = launch.validate_local_launch(os.environ, event, checkout, self.document)
        need(result["binding"] == self.document["binding"] and result["source"] == self.document["source"],
             "immutable launch receipt identity changed")
        return result

    def boot_id(self) -> str:
        raw = _regular(Path("/proc/sys/kernel/random/boot_id"), 64)
        value = raw.decode("ascii").strip()
        need(re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value) is not None,
             "invalid current boot identity")
        return value


    def positive(self, evidence: Path) -> dict:
        return probes.run_positive_probe(evidence)

    def boundary(self, cgroup_root: str, evidence: Path) -> dict:
        return probes.run_boundary_probe(cgroup_root, evidence)


    def phase(self, name: str, evidence: Evidence, binding: dict) -> dict:
        return run_phase(name, self.repo, evidence, binding, self.document["source"])


def _binding(launch_receipt: dict, boot_id: str) -> dict:
    need(type(launch_receipt) is dict and set(launch_receipt) == {
        "schema_version", "status", "observation_kind", "binding", "source", "receipt_sha256", "local_checked"}
        and type(launch_receipt["schema_version"]) is int and launch_receipt["schema_version"] == 1
        and launch_receipt["status"] == "PASS"
        and launch_receipt["observation_kind"] == "local_receipt_check", "local identity gate did not pass")
    value = launch_receipt["binding"]
    setup_policy.validate_binding(value)
    launch.validate_source(launch_receipt["source"])
    need(launch_receipt["source"]["candidate_sha"] == value["candidate_sha"], "local source candidate mismatch")
    need(type(launch_receipt["receipt_sha256"]) is str
         and re.fullmatch(r"[0-9a-f]{64}", launch_receipt["receipt_sha256"]) is not None
         and launch_receipt["receipt_sha256"] != "0" * 64, "invalid original receipt digest")
    checked = launch_receipt["local_checked"]
    need(type(checked) is dict and set(checked) == {"utc_ns", "monotonic_ns"}
         and all(type(item) is int and item > 0 for item in checked.values())
         and 0 <= time.monotonic_ns() - checked["monotonic_ns"] <= 30 * 10**9,
         "stale local identity observation")
    need(value["boot_id"] == boot_id, "invalid boot binding")
    return copy.deepcopy(value)


def phase_argv(phase: str, evidence: Path) -> list[str]:
    """Original ownership/full and reviewed exact local16 addition; report-only observer."""
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
                "tests/test_lock_signals.py", "tests/test_mutation_detach_integration.py",
                *outcomes.REQUIRED_INTEGRATION_NODEIDS, "-p", "forge_ci.pytest_observer"]
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


def run_phase(name: str, repo: Path, evidence: Evidence, binding: dict, source: dict) -> dict:
    """Fixed three-phase driver. There is deliberately no arbitrary argv input."""
    argv = phase_argv(name, evidence.path)
    evidence.check()
    log_name = Path(PHASE_FILES[name]).with_suffix(".log").name
    receipt = {"phase": name, "binding": copy.deepcopy(binding), "source": copy.deepcopy(source), "argv": argv, "exit_code": None,
               "completed": False, "cancelled": False, "timed_out": False, "started": stamp(),
               "ended": None, "junit_sha256": None, "observer_sha256": None,
               "log_sha256": None, "log_bytes": 0, "error": None}
    process = None
    hasher = hashlib.sha256()
    job_remaining = (binding["job_started_ns"] + (JOB_SECONDS - CLEANUP_SECONDS - ARTIFACT_SECONDS) * 1_000_000_000 - time.time_ns()) / 1_000_000_000
    deadline = time.monotonic() + min(PHASE_SECONDS[name], job_remaining)
    try:
        need(job_remaining > 0, "authenticated job cleanup/artifact reserve reached")
        print(f"PHASE {name}: starting ({PHASE_SECONDS[name]}s cap)", flush=True)
        fd = os.open(evidence.path / log_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as log:
            need(os.getuid() > 0 and os.getgid() > 0 and os.getuid() == os.geteuid()
                 and os.getgid() == os.getegid(), "test phases must remain the ordinary non-root user")
            expected = [PHASE_FILES[name]] + ([outcomes.EVENT_FILES[name]] if name in outcomes.EVENT_FILES else [])
            for filename in expected:
                path = evidence.path / filename
                need(not path.exists() and not path.is_symlink(), "test phase evidence must be fresh")
            # Preserve the natural trusted test/tool environment and execute
            # this current interpreter, not a new PATH substitute.
            environment = dict(os.environ)
            environment.pop("FORGE_CI_REQUIRED_EVENTS", None)
            environment.pop("FORGE_CI_REQUIRED_PHASE", None)
            if name in outcomes.EVENT_FILES:
                environment["FORGE_CI_REQUIRED_EVENTS"] = str(evidence.path / outcomes.EVENT_FILES[name])
                environment["FORGE_CI_REQUIRED_PHASE"] = name
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
                                 ("observer_sha256", outcomes.EVENT_FILES.get(name), outcomes.MAX_EVENTS_BYTES)):
            if key == "observer_sha256" and name not in outcomes.EVENT_FILES:
                continue
            try:
                receipt[key] = sha256(_regular(evidence.path / file, limit))
            except (OSError, launch.LaunchError):
                receipt[key] = None
        evidence.json(name + "-phase.json", receipt)
        duration = (receipt["ended"]["monotonic_ns"] - receipt["started"]["monotonic_ns"]) / 1_000_000_000
        print(f"PHASE {name}: {duration:.3f}s; exit={receipt['exit_code']}; completed={receipt['completed']}", flush=True)
    return receipt


def _phase_junit(raw: bytes, *, no_skips: bool, observer: dict | None = None, phase: str | None = None) -> int:
    root = outcomes._load_junit(raw)
    cases = list(root.iter("testcase"))
    need(bool(cases), "phase collected no testcases")
    for case in cases:
        need(case.find("failure") is None and case.find("error") is None, "phase JUnit has failure/error")
        if no_skips:
            need(case.find("skipped") is None, "ownership/cancellation cases were skipped")
        need(all(child.tag in {"properties", "system-out", "system-err", "skipped"} for child in case),
             "phase JUnit contains unknown outcome")
    summary_errors = outcomes._junit_summary_errors(root, observer=observer, phase=phase)
    need(not summary_errors, "phase JUnit/observer evidence disagrees: " + "; ".join(summary_errors))
    return len(cases)


class Controller:
    """Pure orchestration with injectable trusted Gate/Runtime for offline tests."""
    def __init__(self, gate: Gate, runtime: Runtime, evidence: Evidence):
        self.gate, self.runtime, self.evidence = gate, runtime, evidence
        self.binding: dict = {}
        self.source: dict = {}
        self.launch_receipt_sha256: str | None = None
        self.cleanup: dict = {}
        self.receipt: dict | None = None
        self.state = "CREATED"
        # Until sealed setup admission passes, the earlier operation is unknown
        # to this late controller, not evidence that no load happened.
        self.load_attempted: bool | None = None
        self.ready: dict | None = None
        self.started = stamp()
        self.evidence.record("created", {"status": self.state, "started": self.started})

    def _capture(self, name: str, callback) -> Any:
        _check_final_time()
        self.state = name
        self.evidence.record(name + "-started", {"started": stamp(), "binding": self.binding, "source": self.source})
        result = copy.deepcopy(callback())
        _check_final_time()
        self.evidence.record(name, result)
        return result

    def _gate_result(self, result: dict) -> None:
        need(type(result) is dict and result.get("status") == "PASS"
             and type(result.get("binding")) is dict
             and launch.canonical_bytes(result["binding"]) == launch.canonical_bytes(self.binding)
             and launch.canonical_bytes(result.get("source")) == launch.canonical_bytes(self.source),
             "admission gate failed or binding changed")

    def _admission(self, result: dict) -> None:
        self._gate_result(result)
        need(set(result) == {"schema_version", "status", "cgroup_root", "binding",
                             "setup_receipt_sha256", "setup_policy_sha256", "source"}
             and type(result["schema_version"]) is int and result["schema_version"] == 2,
             "malformed setup-first admission receipt")
        for key in ("setup_receipt_sha256", "setup_policy_sha256"):
            value = result[key]
            need(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None
                 and value != "0" * 64, "invalid sealed setup identity")
        uid = os.getuid()
        need(result["cgroup_root"] == f"/sys/fs/cgroup/user.slice/user-{uid}.slice/user@{uid}.service",
             "cgroup root is not the ordinary runner subtree")


    def _stop(self, error: BaseException) -> dict:
        failed_stage = self.state
        self.state = "STOP"
        result = {"schema_version": 1, "status": self.state, "qualified": False,
                  "qualification_complete": False, "evidence_upload_verified": False,
                  "binding": self.binding, "source": self.source, "failed_stage": failed_stage,
                  "load_attempted": self.load_attempted, "late_load_attempted": False,
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
            initial = self._capture("local-identity-initial", self.runtime.launch)
            self.binding = _binding(initial, self.runtime.boot_id())
            self.source = copy.deepcopy(initial["source"])
            self.launch_receipt_sha256 = initial["receipt_sha256"]
            self.receipt = self._capture("setup-admission", self.gate.prepare)
            self._admission(self.receipt)
            # This records the independently verified EARLY operation. There is
            # no policy load entrypoint in this late controller.
            self.load_attempted = True
            positive = self._capture("positive", lambda: self.runtime.positive(self.evidence.path))
            need(type(positive) is dict and positive.get("argv") == probes.production_probe_argv()
                 and not positive.get("error") and type(positive.get("returncode")) is int
                 and positive["returncode"] == 0, "unchanged positive production probe failed")
            _probe_command(positive)
            boundary = self._capture("boundary", lambda: self.runtime.boundary(
                self.receipt["cgroup_root"], self.evidence.path))
            probes.validate_boundary_record(boundary)
            cleanup = boundary.get("cleanup")
            need(type(cleanup) is dict and set(cleanup) == {"cancelled", "start_complete", "complete", "error"}
                 and cleanup["cancelled"] is True and cleanup["start_complete"] is True
                 and cleanup["complete"] is True and cleanup["error"] is None,
                 "production boundary owned cleanup is incomplete or unknown")
            self.cleanup = copy.deepcopy(cleanup)
            self.state = "QUALIFICATION_READY"
            self.ready = {"schema_version": 2, "status": self.state, "qualified": False,
                          "qualification_complete": False, "full_suite_passed": False,
                          "evidence_upload_verified": False, "binding": copy.deepcopy(self.binding),
                          "source": copy.deepcopy(self.source), "launch_receipt_sha256": self.launch_receipt_sha256,
                          "cleanup": copy.deepcopy(self.cleanup),
                          "load_attempted": True, "late_load_attempted": False, "boundary_proved": True,
                          "setup_receipt_sha256": self.receipt["setup_receipt_sha256"],
                          "setup_policy_sha256": self.receipt["setup_policy_sha256"], "ended": stamp()}
            self.evidence.json("ready.json", self.ready)
            return copy.deepcopy(self.ready)
        except BaseException as exc:  # noqa: BLE001 - cancellation/SystemExit must persist STOP
            return self._stop(exc)

    def _phase_integrity(self, receipt: dict, phase: str, previous: int) -> None:
        """Fail before the next spawn on malformed evidence; ordinary red continues."""
        need(set(receipt) == {"phase", "binding", "source", "argv", "exit_code", "completed", "cancelled", "timed_out",
                              "started", "ended", "junit_sha256", "observer_sha256", "log_sha256", "log_bytes", "error"},
             "malformed direct phase receipt")
        need(launch.canonical_bytes(receipt["binding"]) == launch.canonical_bytes(self.binding)
             and launch.canonical_bytes(receipt["source"]) == launch.canonical_bytes(self.source)
             and receipt["argv"] == phase_argv(phase, self.evidence.path), "direct phase identity or selection changed")
        _times(receipt["started"], receipt["ended"], PHASE_SECONDS[phase])
        need(previous <= receipt["started"]["monotonic_ns"]
             and receipt["ended"]["monotonic_ns"] <= time.monotonic_ns(),
             "direct phase evidence is stale, overlapping or future dated")
        log = _regular(self.evidence.path / Path(PHASE_FILES[phase]).with_suffix(".log"), PHASE_LOG_LIMIT)
        need(type(receipt["log_bytes"]) is int and receipt["log_bytes"] == len(log)
             and receipt["log_sha256"] == sha256(log), "direct phase log digest changed")
        junit = _regular(self.evidence.path / PHASE_FILES[phase], outcomes.MAX_JUNIT_BYTES)
        need(receipt["junit_sha256"] == sha256(junit), "direct phase JUnit digest changed")
        events = None
        if phase in outcomes.EVENT_FILES:
            events = _regular(self.evidence.path / outcomes.EVENT_FILES[phase], outcomes.MAX_EVENTS_BYTES)
            need(receipt["observer_sha256"] == sha256(events), "direct phase observer digest changed")
        else:
            need(receipt["observer_sha256"] is None, "unexpected direct observer evidence")
        outcomes.validate_phase_integrity(junit, events, phase=phase, exitstatus=receipt["exit_code"])
        if receipt["exit_code"] == 0:
            if phase == "ownership":
                _phase_junit(junit, no_skips=True)
            else:
                passed = outcomes.validate_phase_outcomes(
                    self.evidence.path / PHASE_FILES[phase], self.evidence.path / outcomes.EVENT_FILES[phase], phase=phase)
                need(passed["status"] == "PASS" and passed["qualified"] is True,
                     "exit-zero phase evidence does not reconcile successful mandatory outcomes")
                need(passed["sha256"] == {"junit": receipt["junit_sha256"], "events": receipt["observer_sha256"]},
                     "phase outcome files changed during immediate validation")

    def run_tests(self) -> dict:
        """Run exact phases in this live controller, with independent durations."""
        try:
            need(self.state == "QUALIFICATION_READY", "tests require this controller's proved boundary")
            receipts = []
            previous = self.ready["ended"]["monotonic_ns"]
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
                # Malformed evidence is a supervision failure even on exit 1.
                self._phase_integrity(receipt, phase, previous)
                previous = receipt["ended"]["monotonic_ns"]
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
                need(set(receipt) == {"phase", "binding", "source", "argv", "exit_code", "completed", "cancelled", "timed_out",
                                      "started", "ended", "junit_sha256", "observer_sha256",
                                      "log_sha256", "log_bytes", "error"}, "malformed phase receipt")
                phase = receipt["phase"]
                need(type(receipt["binding"]) is dict
                     and launch.canonical_bytes(receipt["binding"]) == launch.canonical_bytes(self.binding)
                     and launch.canonical_bytes(receipt["source"]) == launch.canonical_bytes(self.source),
                     "test phase belongs to another run/boot/source")
                need(receipt["argv"] == phase_argv(phase, self.evidence.path), "test selection changed")
                need(type(receipt["exit_code"]) is int and receipt["exit_code"] == 0
                     and receipt["completed"] is True and receipt["cancelled"] is False
                     and receipt["timed_out"] is False and receipt["error"] is None,
                     "test phase failed or did not complete")
                _times(receipt["started"], receipt["ended"], PHASE_SECONDS[phase])
                if phase == "local-integration":
                    _times(receipt["started"], receipt["ended"], LOCAL_INTEGRATION_HEADROOM_SECONDS)
                need(receipt["started"]["monotonic_ns"] >= previous, "stale, overlapping or pre-boundary test receipt")
                previous = receipt["ended"]["monotonic_ns"]
                need(previous <= time.monotonic_ns(), "test receipt ends in the future")
                log = _regular(self.evidence.path / Path(PHASE_FILES[phase]).with_suffix(".log"), PHASE_LOG_LIMIT)
                need(type(receipt["log_bytes"]) is int and receipt["log_bytes"] == len(log)
                     and receipt["log_sha256"] == sha256(log), "phase log digest/length changed")
                raw = _regular(self.evidence.path / PHASE_FILES[phase], outcomes.MAX_JUNIT_BYTES)
                need(sha256(raw) == receipt["junit_sha256"], "phase JUnit digest changed")
                observer = None
                if phase in outcomes.EVENT_FILES:
                    events = _regular(self.evidence.path / outcomes.EVENT_FILES[phase], outcomes.MAX_EVENTS_BYTES)
                    need(sha256(events) == receipt["observer_sha256"], "observer digest changed")
                    observer = outcomes._load_events(events)
                else:
                    need(receipt["observer_sha256"] is None, "unexpected phase observer evidence")
                summaries[phase] = {"cases": _phase_junit(raw, no_skips=phase == "ownership", observer=observer, phase=phase),
                                    "junit_sha256": sha256(raw)}
            required = self._capture("required-outcomes", lambda: outcomes.validate_outcomes(
                self.evidence.path / "pytest.xml", self.evidence.path / "required-events.json",
                self.evidence.path / "local-integration.xml", self.evidence.path / "local-integration-required-events.json"))
            need(required.get("status") == "PASS" and required.get("qualified") is True,
                 "required 27-node outcome gate failed")
            need(required.get("required_count") == 27 and required.get("required_counts") == {"full": 11, "local-integration": 16}
                 and required.get("sha256") == {
                     receipt["phase"]: {"junit": receipt["junit_sha256"], "events": receipt["observer_sha256"]}
                     for receipt in phase_receipts if receipt["phase"] in outcomes.EVENT_FILES},
                 "outcome files changed during validation")
            local = self._capture("local-identity-final", self.runtime.launch)
            need(_binding(local, self.runtime.boot_id()) == self.binding
                 and local["source"] == self.source and local["receipt_sha256"] == self.launch_receipt_sha256,
                 "final run/boot/source binding changed")
            source = self._capture("source-final", lambda: self.gate.final_source_recheck(self.receipt))
            self._gate_result(source)
            need(set(source) == {"status", "binding", "source", "live", "setup_final_observation_sha256"},
                 "final root observation is missing or malformed")
            setup_policy.validate_live_evidence(source["live"], self.binding, self.source["tree_oid"], fresh=True)
            observed_raw = _regular(self.evidence.path / "setup-final-observation.json", RECORD_LIMIT)
            need(source["setup_final_observation_sha256"] == sha256(observed_raw),
                 "final root observation digest changed")
            observed = setup_policy.parse_json(observed_raw, limit=RECORD_LIMIT)
            need(launch.canonical_bytes(observed.get("live")) == launch.canonical_bytes(source["live"])
                 and launch.canonical_bytes(observed.get("binding")) == launch.canonical_bytes(self.binding)
                 and observed.get("setup_receipt_sha256") == self.receipt["setup_receipt_sha256"]
                 and observed.get("setup_policy_sha256") == self.receipt["setup_policy_sha256"],
                 "final root observation differs from returned evidence")
            self.state = "TESTS_PASSED"
            result = {"schema_version": 4, "status": self.state, "qualified": False,
                      "qualification_complete": True, "full_suite_passed": True,
                      "evidence_upload_verified": False, "binding": copy.deepcopy(self.binding),
                      "source": copy.deepcopy(self.source), "launch_receipt_sha256": self.launch_receipt_sha256,
                      "phases": summaries, "phase_receipts": phase_receipts,
                      "setup_receipt_sha256": self.receipt["setup_receipt_sha256"],
                      "setup_policy_sha256": self.receipt["setup_policy_sha256"],
                      "required_count": len(outcomes.REQUIRED_NODEIDS), "required_counts": required["required_counts"],
                      "outcome_evidence_sha256": required["sha256"], "observer_bytes": required["events_bytes"],
                      "outcomes_sha256": sha256(launch.canonical_bytes(required)),
                      "final_checks": {"local_source_identity": local, "source_policy": source},
                      "cleanup": {"boundary": self.cleanup, "mandatory_cleanup_cases": "passed",
                                  "service_and_migrated_siblings": "requires_external_reconciliation"},
                      "ended": stamp()}
            self.evidence.json("tests-passed.json", result)
            return result
        except BaseException as exc:  # noqa: BLE001 - incomplete finalization never qualifies
            return self._stop(exc)


class DiagnosticController(Controller):
    """One fixed B observation after genuine phases, under the original clock."""
    def __init__(self, gate, runtime, evidence, *, service_started_monotonic_ns,
                 service_started_utc_ns, service_artifact_deadline_utc_ns, runtime_sha256):
        super().__init__(gate, runtime, evidence)
        from . import user_service
        values = (service_started_monotonic_ns, service_started_utc_ns, service_artifact_deadline_utc_ns)
        need(all(type(value) is int and value > 0 for value in values), "invalid service clock")
        original = runtime.document["binding"]
        need(service_artifact_deadline_utc_ns == original["job_started_ns"] +
             (JOB_SECONDS - ARTIFACT_SECONDS) * 10**9, "changed job artifact deadline")
        # A remains the authenticated artifact boundary; C reserves root cleanup.
        work_deadline_utc_ns = service_artifact_deadline_utc_ns - CLEANUP_SECONDS * 10**9
        need(original["job_started_ns"] <= service_started_utc_ns and
             service_started_utc_ns + user_service.STEP_SECONDS * 10**9 <= work_deadline_utc_ns,
             "service exceeds original job reserve")
        elapsed = time.monotonic_ns() - service_started_monotonic_ns
        need(0 <= elapsed <= user_service.STARTUP_SECONDS * 10**9, "late or future controller start")
        need(abs((time.time_ns() - service_started_utc_ns) - elapsed) <= 5 * 10**9,
             "service wall and monotonic clocks disagree")
        need(type(runtime_sha256) is str and re.fullmatch(r"[0-9a-f]{64}", runtime_sha256) is not None,
             "invalid admitted runtime digest")
        self.service_started_monotonic_ns = service_started_monotonic_ns
        self.service_started_utc_ns = service_started_utc_ns
        self.service_grace_deadline_ns = service_started_monotonic_ns + user_service.CANCEL_SECONDS * 10**9
        self.artifact_deadline_utc_ns = service_artifact_deadline_utc_ns
        self.artifact_deadline_ns = service_started_monotonic_ns + (
            service_artifact_deadline_utc_ns - service_started_utc_ns)
        self.work_deadline_utc_ns = work_deadline_utc_ns
        self.work_deadline_ns = self.artifact_deadline_ns - CLEANUP_SECONDS * 10**9
        self.runtime_sha256 = runtime_sha256
        self.measurement_context = None
        self._measurement_finalized = False

    def _budget_deadline(self, seconds):
        remaining_wall = self.work_deadline_utc_ns - time.time_ns()
        need(remaining_wall > 0, "original work deadline exhausted")
        return min(time.monotonic_ns() + seconds * 10**9,
                   self.service_grace_deadline_ns, self.work_deadline_ns,
                   time.monotonic_ns() + remaining_wall)

    def run_tests(self):
        from . import baseline_measurement, user_service
        try:
            need(self.state == "QUALIFICATION_READY", "diagnostic requires current production boundary")
            environment = dict(os.environ)
            raw = _regular(Path(environment["EVIDENCE"]) / "runtime-admission.json", 256 * 1024)
            need(sha256(raw) == self.runtime_sha256, "runtime admission changed after capsule validation")
            deadline = self._budget_deadline(60)
            trusted = user_service.load_runtime_admission(environment, self.source, deadline_ns=deadline)
            self.measurement_context = baseline_measurement.prepare(
                self.runtime.repo, environment, binding=self.binding, source=self.source,
                trusted_runtime=trusted, deadline_ns=deadline)
        except BaseException as exc:  # noqa: BLE001 - no phase runs without admitted runtime
            return self._stop(exc)
        return super().run_tests()

    def _complete_phases(self, receipts):
        need(type(receipts) is list and len(receipts) == len(PHASE_FILES)
             and [item.get("phase") for item in receipts] == list(PHASE_FILES), "missing successful phases")
        previous = self.ready["ended"]["monotonic_ns"]
        for receipt in receipts:
            phase = receipt["phase"]
            need(type(receipt.get("exit_code")) is int and receipt["exit_code"] == 0
                 and receipt.get("completed") is True and receipt.get("cancelled") is False
                 and receipt.get("timed_out") is False and receipt.get("error") is None,
                 "B requires all three successful phases")
            self._phase_integrity(receipt, phase, previous)
            if phase == "local-integration":
                _times(receipt["started"], receipt["ended"], LOCAL_INTEGRATION_HEADROOM_SECONDS)
            previous = receipt["ended"]["monotonic_ns"]
        required = outcomes.validate_outcomes(
            self.evidence.path / "pytest.xml", self.evidence.path / "required-events.json",
            self.evidence.path / "local-integration.xml", self.evidence.path / "local-integration-required-events.json")
        need(required.get("status") == "PASS" and required.get("qualified") is True
             and required.get("required_count") == 27 and required.get("required_counts") == {"full": 11, "local-integration": 16},
             "B requires genuine full11/local16 outcomes")
        expected = {r["phase"]: {"junit": r["junit_sha256"], "events": r["observer_sha256"]}
                    for r in receipts if r["phase"] in outcomes.EVENT_FILES}
        need(required.get("sha256") == expected, "pre-B mandatory records changed")
        record = {"schema_version": 1, "status": "PHASES_COMPLETE", "qualified": False,
                  "qualification_complete": False, "binding": self.binding, "source": self.source,
                  "phase_receipts": copy.deepcopy(receipts), "required_counts": required["required_counts"],
                  "outcomes_sha256": sha256(launch.canonical_bytes(required)), "ended": stamp()}
        raw = launch.canonical_bytes(record) + b"\n"
        need(len(raw) <= 64 * 1024, "phase completion metadata bound")
        self.evidence.write("phases-complete.json", raw)
        return {"bytes": len(raw), "sha256": sha256(raw)}

    def finalize(self, phase_receipts):
        from . import baseline_measurement
        need(not self._measurement_finalized, "diagnostic cannot resume or repeat B")
        self._measurement_finalized = True
        phase_record = outcome = interruption = None
        try:
            phase_record = self._complete_phases(phase_receipts)
            need(self.measurement_context is not None, "missing live measurement context")
            deadline = self._budget_deadline(60)
            inventory = baseline_measurement.admit_phase_inventory(self.measurement_context, deadline_ns=deadline)
            baseline_measurement.capture_pre_b(self.measurement_context, admitted_generated=inventory, deadline_ns=deadline)
            outcome = baseline_measurement.run_once(
                self.measurement_context, service_started_monotonic_ns=self.service_started_monotonic_ns,
                service_grace_deadline_ns=self.service_grace_deadline_ns,
                job_artifact_deadline_ns=self.artifact_deadline_ns)
            interruption = outcome.interrupted
        except BaseException as exc:  # noqa: BLE001 - preflight cannot authorize another command
            interruption = exc
        final_deadline = self._budget_deadline(FINALIZATION_SECONDS)
        if outcome is not None:
            final_deadline = min(final_deadline, outcome.ended_ns + FINALIZATION_SECONDS * 10**9)
        self.gate.final_deadline_ns = final_deadline
        self.evidence.deadline_ns = final_deadline
        with FinalizationWindow(final_deadline) as window:
            if getattr(interruption, "_forge_control", False) or (
                    interruption is not None and not isinstance(interruption, Exception)):
                window.first_control = interruption
            if interruption is None:
                self.state = "QUALIFICATION_READY"
                original = super().finalize(phase_receipts)
            else:
                original = None
            window.check()
            if outcome is None or phase_record is None:
                return self._stop(interruption or ControllerError("B preflight failed"))
            passed = original is not None and original.get("status") == "TESTS_PASSED"
            checks = {"phases_complete": phase_record, "local_source_identity_sha256": None,
                      "source_policy_sha256": None, "source_policy_passed": passed}
            if passed:
                checks["local_source_identity_sha256"] = sha256(launch.canonical_bytes(original["final_checks"]["local_source_identity"]))
                checks["source_policy_sha256"] = sha256(launch.canonical_bytes(original["final_checks"]["source_policy"]))
            directory = self.evidence.path / "baseline-measurement"
            measured = baseline_measurement.persist(self.measurement_context, outcome, directory,
                                                   final_deadline_ns=final_deadline, final_checks=checks)
            window.check()
            actual = sum(path.stat().st_size for path in directory.iterdir())
            phase_raw = _regular(self.evidence.path / "phases-complete.json", 64 * 1024)
            need(len(phase_raw) == phase_record["bytes"] and sha256(phase_raw) == phase_record["sha256"]
                 and actual + len(phase_raw) <= 2 * 1024 * 1024, "complete B addition byte bound")
            window.check()
            if window.first_control is not None:
                raise window.first_control
            self.state = "B_MEASURED" if passed and measured.get("status") == "MEASUREMENT_COMPLETE" else "STOP"
            return {"schema_version": 1, "status": self.state, "qualified": False,
                    "baseline_passed": False, "measurement_status": measured.get("status"),
                    "binding": self.binding, "source": self.source,
                    "result_sha256": measured.get("result_sha256"), "manifest_sha256": measured.get("manifest_sha256"),
                    "persisted_monotonic_ns": measured.get("persisted_monotonic_ns"),
                    "finalization_deadline_monotonic_ns": final_deadline, "ended": stamp()}


@contextmanager
def cancellation_guard():
    """The CLI records TERM/INT as STOP; SIGKILL/missing result stays unqualified."""
    def cancelled(signum, frame):
        error = Cancelled(f"controller cancelled by signal {signum}")
        if _active_finalization is not None and _active_finalization.first_control is None:
            _active_finalization.first_control = error
        raise error
    previous = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
    try:
        for number in previous:
            signal.signal(number, cancelled)
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


@contextmanager
def terminal_guard(evidence):
    """Continue the identical deadline through terminal and failure output."""
    deadline = None if evidence is None else evidence.deadline_ns
    if deadline is None:
        yield None  # Before finalization, the original service clock still owns startup.
    else:
        with FinalizationWindow(deadline) as window:
            yield window


def emit_terminal(evidence, result):
    with terminal_guard(evidence):
        deadline = result.get("finalization_deadline_monotonic_ns")
        if deadline is not None:
            need(deadline == evidence.deadline_ns, "terminal deadline changed")
        terminal = launch.canonical_bytes(result)
        need(len(terminal) <= 16 * 1024, "terminal journal byte bound")
        print("BASELINE_DIAGNOSTIC_TERMINAL " + terminal.decode("ascii"), flush=True)
        _check_final_time()
        return 0 if result["status"] == "B_MEASURED" else 1


def preserve_main_failure(evidence, error):
    """Best effort inside remaining original time; expired means no further I/O."""
    if evidence is not None and evidence.deadline_ns is not None and time.monotonic_ns() >= evidence.deadline_ns:
        return
    try:
        with terminal_guard(evidence) as window:
            if window is not None and (getattr(error, "_forge_control", False) or not isinstance(error, Exception)):
                window.first_control = error
            if evidence is not None and not (evidence.path / "stop.json").exists():
                evidence.json("stop.json", {"schema_version": 1, "status": "STOP", "qualified": False,
                                            "error_type": type(error).__name__, "error": str(error)[:1024]})
            print("STOP: controller startup or evidence preservation failed", flush=True)
            _check_final_time()
    except BaseException:  # noqa: BLE001 - no retry, fresh deadline, write or flush after this failure
        return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--service-started-monotonic-ns", required=True, type=int)
    parser.add_argument("--service-started-utc-ns", required=True, type=int)
    parser.add_argument("--service-artifact-deadline-utc-ns", required=True, type=int)
    parser.add_argument("--runtime-sha256", required=True)
    args = parser.parse_args(argv)
    evidence = None
    try:
        with cancellation_guard():
            evidence = Evidence(args.evidence)
            # Missing admission code is a terminal error, never a synthetic PASS.
            from .admission import Gate as AdmissionGate
            document = launch.load_receipt(args.receipt)
            gate = AdmissionGate(document, args.repo, evidence.path)
            controller = DiagnosticController(
                gate, Runtime(document, args.repo), evidence,
                service_started_monotonic_ns=args.service_started_monotonic_ns,
                service_started_utc_ns=args.service_started_utc_ns,
                service_artifact_deadline_utc_ns=args.service_artifact_deadline_utc_ns,
                runtime_sha256=args.runtime_sha256)
            result = controller.qualify()
            if result["status"] == "QUALIFICATION_READY":
                result = controller.run_tests()
            return emit_terminal(evidence, result)

    except BaseException as exc:  # noqa: BLE001 - cancellation/import failures stay unqualified
        preserve_main_failure(evidence, exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
