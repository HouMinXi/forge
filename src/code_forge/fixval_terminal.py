# SPDX-License-Identifier: Apache-2.0
"""Pure, bounded FIXVAL projection checks, not execution authentication.

An external consumer must independently authenticate the producer/revalidator
invocation and attest its exact canonical output digest. Neither a module hash
inside this object nor hashing an untrusted object supplies that authority.
Raw inventories, qualified-runtime admission and dynamic capture environments
are checked by the authenticated producer, not reconstructed by this parser.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import PurePosixPath
import re

MAX_TIMEOUT = 86400
MAX_STAGE_BYTES = 32 * 1024
MAX_WITNESS_BYTES = 8 * 1024
MAX_RAW_BYTES = 128 * 1024 * 1024
MAX_RAW_FILES = 64
MAX_COMMANDS = 8
MAX_ITEMS = 50_000
MAX_RECORD_BYTES = 8 * 1024 * 1024
MAX_RETAINED_BYTES = 1024 * 1024
QUALIFIED_VERSIONS = frozenset({"8.4.2", "9.1.0", "9.1.1"})
HASH = re.compile(r"[0-9a-f]{64}\Z")
NONCE = re.compile(r"[0-9a-f]{32}\Z")
SKIP_REASONS = frozenset({"no_files", "no_tests", "no_production", "non_git", "no_production_patch"})
STAGE_FIELDS = frozenset(
    {
        "version",
        "invocation_id",
        "source_hash",
        "outcome",
        "reason",
        "exception",
        "config_sha256",
        "candidate_sha256",
        "earned_window_sha256",
        "raw",
        "phases",
        "witness",
        "restoration",
        "validator_sha256",
        "reporter_sha256",
    }
)
PHASE_FIELDS = frozenset(
    {
        "phase",
        "nonce",
        "timeout",
        "status",
        "duration",
        "returncode",
        "record_sha256",
        "record_bytes",
        "inventory_sha256",
        "collection_sha256",
        "collected",
        "failed",
        "passed",
        "skipped",
        "ordinary_passed",
        "ordinary_failed",
        "configured_argv_sha256",
        "effective_argv_sha256",
        "env_sha256",
        "base_env_sha256",
        "pytest_version",
        "cleanup_complete",
        "owner",
        "diagnostic_truncated",
        "streams",
        "retry_eligible",
        "superseded",
    }
)
OWNER_FIELDS = frozenset(
    {
        "caller_pid",
        "caller_start_ticks",
        "owner_pid",
        "owner_start_ticks",
        "driver_pid",
        "driver_start_ticks",
        "invocation_nonce",
        "resolved_executable",
    }
)
EXECUTABLE_FIELDS = frozenset(
    {
        "path",
        "realpath",
        "dev",
        "ino",
        "size",
        "mtime_ns",
        "ctime_ns",
        "sha256",
    }
)
COUNTS = ("collected", "failed", "passed", "skipped", "ordinary_passed", "ordinary_failed")
PHASE_HASHES = (
    "record_sha256",
    "inventory_sha256",
    "collection_sha256",
    "configured_argv_sha256",
    "effective_argv_sha256",
    "env_sha256",
    "base_env_sha256",
)


class EvidenceError(ValueError):
    """A projection is malformed, contradictory, or differs from admitted input."""


def canonical(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _hash(value):
    return type(value) is str and HASH.fullmatch(value) is not None


def _nonce(value):
    return type(value) is str and NONCE.fullmatch(value) is not None


def _integer(value, minimum=0, maximum=None):
    return type(value) is int and value >= minimum and (maximum is None or value <= maximum)


def _path(value, *, absolute=False):
    if type(value) is not str or not value or "\x00" in value:
        return False
    path = PurePosixPath(value)
    return (
        path.is_absolute() == absolute
        and path.as_posix() == value
        and ".." not in path.parts
        and value not in {".", "/"}
    )


def new_stage(invocation_id, source_hash):
    return dict(
        version=1,
        invocation_id=invocation_id,
        source_hash=source_hash,
        outcome="pending",
        reason="pending",
        exception=None,
        config_sha256=None,
        candidate_sha256=None,
        earned_window_sha256=None,
        raw=None,
        phases=[],
        witness=None,
        restoration="not_started",
        validator_sha256=None,
        reporter_sha256=None,
    )


def _validate_executable(value):
    if (
        type(value) is not dict
        or set(value) != EXECUTABLE_FIELDS
        or not _path(value["path"], absolute=True)
        or not _path(value["realpath"], absolute=True)
        or not _hash(value["sha256"])
        or not _integer(value["dev"])
        or not _integer(value["ino"], 1)
        or not _integer(value["size"], 1)
        or type(value["mtime_ns"]) is not int
        or type(value["ctime_ns"]) is not int
    ):
        raise EvidenceError("invalid resolved executable binding")


def _validate_owner(phase):
    owner = phase["owner"]
    complete = phase["status"] == "complete"
    if type(owner) is not dict or set(owner) != OWNER_FIELDS:
        raise EvidenceError("invalid terminal owner shape")
    pids = []
    for role in ("caller", "owner", "driver"):
        pid, ticks = owner[role + "_pid"], owner[role + "_start_ticks"]
        if pid is None and ticks is None and not complete:
            continue
        if not _integer(pid, 1) or not _integer(ticks, 1):
            raise EvidenceError("terminal phase incarnation missing")
        pids.append(pid)
    if len(set(pids)) != len(pids):
        raise EvidenceError("terminal process identities are contradictory")
    nonce = owner["invocation_nonce"]
    if nonce is not None or complete:
        if not _nonce(nonce) or nonce != phase["nonce"]:
            raise EvidenceError("terminal phase owner nonce mismatch")
    executable = owner["resolved_executable"]
    if executable is not None or complete:
        _validate_executable(executable)


def _validate_streams(phase):
    streams = phase["streams"]
    complete = phase["status"] == "complete"
    if type(streams) is not dict:
        raise EvidenceError("invalid stream metadata")
    if not streams and not complete:
        if phase["diagnostic_truncated"]:
            raise EvidenceError("missing truncated stream metadata")
        return
    if set(streams) != {"stdout", "stderr"}:
        raise EvidenceError("missing terminal stream evidence")
    retained = total = 0
    for row in streams.values():
        if (
            type(row) is not dict
            or set(row) != {"bytes", "sha256", "retained_bytes", "eof"}
            or not _integer(row["bytes"])
            or not _integer(row["retained_bytes"])
            or row["retained_bytes"] > row["bytes"]
            or not _hash(row["sha256"])
            or type(row["eof"]) is not bool
            or (complete and row["eof"] is not True)
            or (row["bytes"] == 0 and row["sha256"] != hashlib.sha256(b"").hexdigest())
        ):
            raise EvidenceError("invalid terminal stream evidence or missing EOF")
        retained += row["retained_bytes"]
        total += row["bytes"]
    if retained > MAX_RETAINED_BYTES or phase["diagnostic_truncated"] != (total > retained):
        raise EvidenceError("terminal diagnostic bound/truncation mismatch")


def _validate_phase(phase):
    if type(phase) is not dict or set(phase) != PHASE_FIELDS:
        raise EvidenceError("invalid terminal phase shape")
    if type(phase["phase"]) is not str or not re.fullmatch(
        r"(?:fixed:[01]:[012]|reverted|overfit)", phase["phase"]
    ):
        raise EvidenceError("unknown terminal phase")
    if type(phase["status"]) is not str or phase["status"] not in {"complete", "error", "timeout"}:
        raise EvidenceError("unknown phase outcome")
    complete = phase["status"] == "complete"
    if not _nonce(phase["nonce"]) or not _integer(phase["timeout"], 1, MAX_TIMEOUT):
        raise EvidenceError("invalid phase nonce or timeout")
    duration = phase["duration"]
    if (
        type(duration) not in (int, float)
        or not 0 <= duration <= phase["timeout"] + 30
        or not math.isfinite(duration)
    ):
        raise EvidenceError("invalid phase duration or envelope deadline exceeded")
    for key in ("cleanup_complete", "diagnostic_truncated", "retry_eligible", "superseded"):
        if type(phase[key]) is not bool:
            raise EvidenceError("invalid phase cleanup/retry/output metadata")
    for count in COUNTS:
        if not _integer(phase[count], 0, MAX_ITEMS):
            raise EvidenceError("invalid terminal phase count")
    if (
        phase["ordinary_passed"] > phase["passed"]
        or phase["ordinary_failed"] > phase["failed"]
        or phase["passed"] + phase["failed"] + phase["skipped"] != phase["collected"]
    ):
        raise EvidenceError("terminal counts disagree")
    if not _integer(phase["record_bytes"], 1 if complete else 0, MAX_RECORD_BYTES):
        raise EvidenceError("invalid terminal raw-record size")
    if type(phase["returncode"]) is not int and (complete or phase["returncode"] is not None):
        raise EvidenceError("invalid terminal return code")
    for key in PHASE_HASHES:
        if phase[key] is not None or complete or key in {"configured_argv_sha256", "base_env_sha256"}:
            if not _hash(phase[key]):
                raise EvidenceError("unbound terminal phase: " + key)
    version = phase["pytest_version"]
    if version is not None or complete:
        if type(version) is not str or version not in QUALIFIED_VERSIONS:
            raise EvidenceError("invalid pytest version")
    _validate_owner(phase)
    _validate_streams(phase)


def _validate_raw(raw, *, required):
    if raw is None and not required:
        return
    if (
        type(raw) is not dict
        or set(raw) != {"directory", "identity", "bytes", "files", "sha256"}
        or not _integer(raw["bytes"], 1 if required else 0, MAX_RAW_BYTES)
        or not _integer(raw["files"], 1 if required else 0, MAX_RAW_FILES)
        or not _hash(raw["sha256"])
        or not _path(raw["directory"], absolute=True)
        or type(raw["identity"]) is not list
        or len(raw["identity"]) != 3
        or any(not _integer(value) for value in raw["identity"])
        or raw["identity"][1] < 1
    ):
        raise EvidenceError("invalid raw evidence manifest")


def _pass_order(phases):
    names = [p["phase"] for p in phases]
    overfit = names[-1:] == ["overfit"]
    core = phases[:-1] if overfit else phases
    fixed0 = [p for p in core if p["phase"].startswith("fixed:0:")]
    retry = any(p["phase"].startswith("fixed:1:") for p in core)
    if retry:
        if not 1 <= len(fixed0) <= 3:
            raise EvidenceError("invalid superseded fixed batch")
        expected = [f"fixed:0:{i}" for i in range(len(fixed0))]
        expected += [f"fixed:1:{i}" for i in range(3)] + ["reverted"]
        for index, phase in enumerate(fixed0):
            last = index == len(fixed0) - 1
            if (
                phase["superseded"] is not True
                or phase["retry_eligible"] is not last
                or phase["status"] != ("error" if last else "complete")
            ):
                raise EvidenceError("invalid startup-only retry history")
            if last and (
                phase["returncode"] == 0
                or phase["record_bytes"] != 0
                or any(
                    phase[key] is not None
                    for key in (
                        "record_sha256",
                        "inventory_sha256",
                        "collection_sha256",
                        "pytest_version",
                    )
                )
                or any(phase[key] != 0 for key in COUNTS)
            ):
                raise EvidenceError("startup-only retry contradicts pytest inventory")
            if not last:
                _require_green(phase)
        authoritative = core[len(fixed0) :]
    else:
        expected = [f"fixed:0:{i}" for i in range(3)] + ["reverted"]
        authoritative = core
    if [p["phase"] for p in core] != expected:
        raise EvidenceError("terminal phase order differs from fixed/GREEN/RED protocol")
    for phase in authoritative + (phases[-1:] if overfit else []):
        if phase["retry_eligible"] or phase["superseded"]:
            raise EvidenceError("authoritative phase is superseded or retry eligible")
    if any(p["status"] != "complete" for p in authoritative):
        raise EvidenceError("authoritative FIXVAL phase incomplete")
    return authoritative[:3], authoritative[3], phases[-1] if overfit else None


def _require_green(phase):
    if (
        phase["returncode"] != 0
        or phase["failed"] != 0
        or phase["ordinary_failed"] != 0
        or phase["ordinary_passed"] < 1
    ):
        raise EvidenceError("fixed GREEN contains failure or lacks ordinary passing call")


def _validate_pass(stage):
    if stage["reason"] != "attributable_red" or stage["restoration"] != "restored":
        raise EvidenceError("missing restored attributable FIXVAL proof")
    for key in (
        "source_hash",
        "config_sha256",
        "candidate_sha256",
        "validator_sha256",
        "reporter_sha256",
    ):
        if not _hash(stage[key]):
            raise EvidenceError("unbound FIXVAL proof: " + key)
    phases = stage["phases"]
    greens, red, overfit = _pass_order(phases)
    incarnations = set()
    for phase in phases:
        if phase["cleanup_complete"] is not True:
            raise EvidenceError("terminal phase cleanup incomplete")
        owner = phase["owner"]
        if (
            any(
                not _integer(owner[k], 1)
                for k in ("caller_pid", "caller_start_ticks", "owner_pid", "owner_start_ticks")
            )
            or owner["invocation_nonce"] != phase["nonce"]
        ):
            raise EvidenceError("PASS phase lacks trusted owner incarnation")
        for role in ("owner", "driver"):
            incarnation = (owner[role + "_pid"], owner[role + "_start_ticks"])
            if incarnation == (None, None):
                continue
            if incarnation in incarnations:
                raise EvidenceError("terminal subprocess incarnation reused across phases")
            incarnations.add(incarnation)
    for phase in greens:
        _require_green(phase)
    if red["returncode"] != 1 or red["ordinary_failed"] < 1:
        raise EvidenceError("reverted phase lacks ordinary test-call failure")
    for key in ("inventory_sha256", "collection_sha256", *COUNTS):
        if any(p[key] != greens[0][key] for p in greens[1:]):
            raise EvidenceError("fixed inventories/counts disagree")
    if (
        red["collection_sha256"] != greens[0]["collection_sha256"]
        or red["collected"] != greens[0]["collected"]
    ):
        raise EvidenceError("reverted collection changed")
    for phase in phases:
        if phase["configured_argv_sha256"] != greens[0]["configured_argv_sha256"]:
            raise EvidenceError("terminal scoped command changed")
        for key in ("caller_pid", "caller_start_ticks"):
            if phase["owner"][key] != greens[0]["owner"][key]:
                raise EvidenceError("terminal caller incarnation changed")
    # Dynamic bootstrap argv/env hashes legitimately vary with each nonce.
    # The admitted stage digest binds them; only the uninstrumented base env
    # and executable are comparable across authoritative phases.
    comparable = greens + [red] + ([overfit] if overfit is not None else [])
    for phase in comparable:
        if phase["base_env_sha256"] != greens[0]["base_env_sha256"]:
            raise EvidenceError("authoritative base environment changed")
        executable = phase["owner"]["resolved_executable"]
        if executable is not None and executable != greens[0]["owner"]["resolved_executable"]:
            raise EvidenceError("authoritative resolved executable changed")
        if phase["status"] == "complete" and phase["pytest_version"] != greens[0]["pytest_version"]:
            raise EvidenceError("authoritative pytest version changed")
    witness = stage["witness"]
    if type(witness) is not dict or set(witness) != {"node", "file", "eligible_count", "green", "red"}:
        raise EvidenceError("missing complete FIXVAL witness")
    if (
        type(witness["node"]) is not str
        or not witness["node"]
        or len(canonical(witness["node"])) > MAX_WITNESS_BYTES
        or not _path(witness["file"])
        or not _integer(witness["eligible_count"], 1, MAX_ITEMS)
        or witness["eligible_count"] > min(p["ordinary_passed"] for p in greens)
        or witness["eligible_count"] > red["ordinary_failed"]
        or type(witness["green"]) is not list
        or len(witness["green"]) != 3
    ):
        raise EvidenceError("invalid complete FIXVAL witness or eligible count")
    links = witness["green"] + [witness["red"]]
    for index, (link, phase) in enumerate(zip(links, greens + [red], strict=True)):
        call = "passed" if index < 3 else "failed"
        if (
            type(link) is not dict
            or set(link) != {"record_sha256", "row_sha256", "call", "row"}
            or not _hash(link["record_sha256"])
            or not _hash(link["row_sha256"])
            or link["record_sha256"] != phase["record_sha256"]
            or link["call"] != call
            or type(link["row"]) is not list
            or len(link["row"]) != 4
        ):
            raise EvidenceError("invalid terminal witness link")
        setup, observed, teardown, framework = link["row"]
        if (
            setup != "passed"
            or observed != call
            or teardown not in ("passed", "skipped")
            or framework is not False
            or link["row_sha256"] != digest([witness["node"], witness["file"], *link["row"]])
        ):
            raise EvidenceError("noncanonical or contradictory ordinary witness row")
    if any(link["row"] != links[0]["row"] for link in links[1:3]):
        raise EvidenceError("fixed witness rows disagree")
    known_bytes = sum(p["record_bytes"] for p in phases)
    known_bytes += sum(s["retained_bytes"] for p in phases for s in p["streams"].values())
    if stage["raw"]["bytes"] < known_bytes:
        raise EvidenceError("raw manifest is smaller than projected retained evidence")


def validate_stage(stage):
    """Check shape and PASS consistency without authenticating execution origin."""
    if (
        type(stage) is not dict
        or set(stage) != STAGE_FIELDS
        or type(stage["version"]) is not int
        or stage["version"] != 1
    ):
        raise EvidenceError("invalid FIXVAL terminal stage schema")
    try:
        encoded = canonical(stage)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise EvidenceError("FIXVAL stage is not finite canonical JSON") from exc
    if len(encoded) > MAX_STAGE_BYTES or not _nonce(stage["invocation_id"]):
        raise EvidenceError("invalid FIXVAL invocation or projection overflow")
    # Legacy/stub reviews use opaque source identifiers. They may retain
    # diagnostics or policy exceptions, never an executed PASS proof. External
    # acceptance separately requires the independently admitted SHA256 value.
    if stage["outcome"] == "PASS":
        if not _hash(stage["source_hash"]):
            raise EvidenceError("invalid FIXVAL source hash")
    elif stage["source_hash"] is not None and (
        type(stage["source_hash"]) is not str or len(stage["source_hash"]) > 256
    ):
        raise EvidenceError("invalid passive FIXVAL source identity")
    for key in (
        "config_sha256",
        "candidate_sha256",
        "earned_window_sha256",
        "validator_sha256",
        "reporter_sha256",
    ):
        if stage[key] is not None and not _hash(stage[key]):
            raise EvidenceError("invalid FIXVAL hash: " + key)
    if (
        type(stage["outcome"]) is not str
        or stage["outcome"] not in {"pending", "PASS", "BLOCK", "ERROR", "SKIPPED", "WAIVED"}
        or type(stage["reason"]) is not str
        or not stage["reason"]
        or type(stage["restoration"]) is not str
        or stage["restoration"] not in {"not_started", "restored", "failed"}
    ):
        raise EvidenceError("unknown FIXVAL terminal outcome/restoration")
    if stage["outcome"] == "SKIPPED" and stage["reason"] not in SKIP_REASONS:
        raise EvidenceError("unapproved FIXVAL skip")
    if stage["outcome"] == "WAIVED":
        exception = stage["exception"]
        if (
            type(exception) is not dict
            or set(exception) != {"reason", "channel"}
            or type(exception["reason"]) is not str
            or not exception["reason"].strip()
            or type(exception["channel"]) is not str
            or exception["channel"] not in {"env", "trailer"}
            or stage["reason"] != "explicit_waiver"
        ):
            raise EvidenceError("invalid explicit waiver")
    elif stage["exception"] is not None:
        raise EvidenceError("unexpected FIXVAL exception metadata")
    if type(stage["phases"]) is not list or len(stage["phases"]) > MAX_COMMANDS:
        raise EvidenceError("invalid phase inventory")
    nonces, names, records = set(), set(), set()
    for phase in stage["phases"]:
        _validate_phase(phase)
        if phase["nonce"] in nonces or phase["phase"] in names:
            raise EvidenceError("phase nonce/name reused")
        nonces.add(phase["nonce"])
        names.add(phase["phase"])
        if phase["record_sha256"] is not None:
            if phase["record_sha256"] in records:
                raise EvidenceError("phase record reused")
            records.add(phase["record_sha256"])
    _validate_raw(stage["raw"], required=stage["outcome"] == "PASS")
    if stage["outcome"] == "PASS":
        _validate_pass(stage)
    elif stage["witness"] is not None:
        raise EvidenceError("non-PASS stage falsely claims witness")
    if stage["outcome"] in {"SKIPPED", "WAIVED"} and stage["phases"]:
        raise EvidenceError("policy exception falsely claims execution")
    return stage


def validate_terminal_stage(
    stage,
    *,
    invocation_id,
    source_hash,
    config_sha256,
    candidate_sha256,
    earned_window_sha256,
    validator_sha256,
    reporter_sha256,
    expected_stage_sha256,
    candidate_files=None,
    command_sha256=None,
    exception=None,
    expected_command=None,
):
    """Validate independently attested output against independently admitted inputs.

    ``expected_stage_sha256`` must come from the authenticated producer or
    revalidator output, never from an external consumer hashing an untrusted
    projection. Local producers may use their own digest for self-consistency;
    that self-check is explicitly not external execution authentication.
    """
    validate_stage(stage)
    if not _hash(expected_stage_sha256) or digest(stage) != expected_stage_sha256:
        raise EvidenceError("terminal output differs from independently attested stage")
    if not _nonce(invocation_id) or stage["invocation_id"] != invocation_id:
        raise EvidenceError("terminal invocation mismatch")
    for key, expected in (("source_hash", source_hash), ("earned_window_sha256", earned_window_sha256)):
        if not _hash(expected) or stage[key] != expected:
            raise EvidenceError("terminal " + key + " mismatch")
    if exception is not None:
        if type(exception) is not dict or set(exception) != {"outcome", "reason", "exception"}:
            raise EvidenceError("invalid admitted exception")
        if any(stage[key] != exception[key] for key in exception):
            raise EvidenceError("terminal policy exception mismatch")
        if (
            stage["outcome"] not in {"SKIPPED", "WAIVED"}
            or stage["phases"]
            or stage["witness"] is not None
        ):
            raise EvidenceError("policy exception falsely claims execution")
        return True
    if stage["outcome"] != "PASS":
        raise EvidenceError("applicable FIXVAL proof did not pass")
    for key, expected in (
        ("config_sha256", config_sha256),
        ("candidate_sha256", candidate_sha256),
        ("validator_sha256", validator_sha256),
        ("reporter_sha256", reporter_sha256),
    ):
        if not _hash(expected) or stage[key] != expected:
            raise EvidenceError("terminal " + key + " mismatch")
    if (
        type(candidate_files) not in (set, frozenset, list, tuple)
        or not candidate_files
        or any(not _path(file) for file in candidate_files)
        or stage["witness"]["file"] not in candidate_files
    ):
        raise EvidenceError("witness is outside the admitted candidate test projection")
    if not _hash(command_sha256):
        raise EvidenceError("missing admitted scoped command")
    if expected_command is not None:
        if (
            type(expected_command) not in (list, tuple)
            or not expected_command
            or any(type(arg) is not str or "\x00" in arg for arg in expected_command)
            or not expected_command[0]
            or digest(expected_command) != command_sha256
        ):
            raise EvidenceError("admitted command argv/hash mismatch")
    if any(p["configured_argv_sha256"] != command_sha256 for p in stage["phases"]):
        raise EvidenceError("terminal command differs from admitted scope")
    return True
