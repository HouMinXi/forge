"""One-shot paired smoke orchestration, with only a caller-supplied transport."""

import json
import os
import time
import uuid
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path
from typing import BinaryIO

from .attempt_plan import AttemptRow, PairedAttemptGate
from .fixtures import InputError, digest, identifier, require
from .smoke_artifacts import private_root, run_artifacts, store_payload
from .smoke_deadline import SmokeDeadline
from .smoke_transport import DirectSmokeTransport, WireReceipt


def _append(journal: BinaryIO, event: dict) -> None:
    journal.write((json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii"))
    journal.flush()
    os.fsync(journal.fileno())


def run_smoke_once(
    *,
    journal_dir: Path,
    run_id: str,
    gate: PairedAttemptGate,
    request_bodies: tuple[bytes, ...],
    send_once: Callable[[bytes], bool | WireReceipt],
    artifact_dir: Path | None = None,
    max_elapsed_s: float | None = None,
) -> tuple[AttemptRow, ...]:
    """Run a frozen pair in a trusted, pre-existing local POSIX directory.

    One durable marker per preregistered run ID, not per process or plan hash.
    Keep the same directory and ID across invocations; do not remove markers.
    There is no recovery or resume. Even an incomplete marker blocks reentry.

    The injected sender is responsible for delivering at most one request per
    call (no redirects or internal retries). False is a known failed attempt;
    exceptions leave the reservation unknown and abort the run. This module
    provides no CLI or credential discovery. A DirectSmokeTransport supplies
    sanitized wire receipts and stops the run on the first non-accepted reply.
    Opt-in artifact_dir retains request and accepted response bodies outside
    the journal. It is supported only by the direct transport.
    """
    started_at = time.monotonic()
    require(
        max_elapsed_s is None
        or (
            type(send_once) is DirectSmokeTransport
            and type(max_elapsed_s) in (int, float)
            and 0 < max_elapsed_s <= 3600
        ),
        "smoke_run.max_elapsed_s",
    )
    deadline = SmokeDeadline(started_at + max_elapsed_s) if max_elapsed_s is not None else None
    require(identifier(run_id) and type(request_bodies) is tuple, "smoke_run")
    gate.check_bodies(request_bodies)
    policy = {}
    retaining = artifact_dir is not None
    require(not retaining or type(send_once) is DirectSmokeTransport, "smoke_artifacts.transport")
    if type(send_once) is DirectSmokeTransport:
        for body in request_bodies:
            send_once.check_body(body, retaining=retaining)
        policy["wire_policy"] = {
            "host": send_once.host,
            "model": send_once.model,
            "output_cap": send_once.output_cap,
            "response_cap": send_once.response_cap,
            "socket_timeout_s": send_once.timeout,
        }
    if deadline is not None:
        deadline.remaining()
        policy["max_elapsed_s"] = max_elapsed_s
    slots = tuple(row.slot for row in gate.rows)
    marker = digest(run_id.encode("ascii")) + ".jsonl"
    with ExitStack() as resources:
        root = resources.enter_context(private_root(artifact_dir)) if artifact_dir is not None else None
        directory = os.open(journal_dir, os.O_RDONLY | os.O_DIRECTORY)
        resources.callback(os.close, directory)
        try:
            fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
        except FileExistsError as exc:
            raise InputError("smoke-run-already-started", run_id) from exc
        with os.fdopen(fd, "wb") as journal:
            artifact_name = marker.removesuffix(".jsonl")
            if root is not None:
                policy["artifact_namespace"] = artifact_name
            _append(
                journal,
                {
                    "schema": 1,
                    "event": "started",
                    "run_id": run_id,
                    "slots": [asdict(slot) for slot in slots],
                    **policy,
                },
            )
            # fsync(file) alone does not make a newly created name durable.
            os.fsync(directory)
            artifacts = (
                resources.enter_context(run_artifacts(root, artifact_name)) if root is not None else None
            )
            for slot, body in zip(slots, request_bodies, strict=True):
                if deadline is not None:
                    deadline.remaining()
                ticket = gate.reserve(slot.arm, slot.group, slot.pass_name, body)
                reservation = {"event": "reserved", "attempt": ticket, "state": "unknown_outcome"}
                response_artifact = None

                def retain_reply(raw: bytes, number: int = ticket) -> None:
                    nonlocal response_artifact
                    assert artifacts is not None
                    response_artifact = store_payload(artifacts, f"{number:02d}-response.json", raw)

                if type(send_once) is DirectSmokeTransport:
                    request_id = uuid.uuid4().hex
                    reservation["client_request_id"] = request_id
                    if artifacts is not None:
                        reservation["request_artifact"] = store_payload(
                            artifacts, f"{ticket:02d}-request.json", body
                        )
                    _append(journal, reservation)
                    if deadline is not None:
                        deadline.remaining()
                    outcome = send_once.send(
                        body,
                        request_id=request_id,
                        retain_reply=retain_reply if artifacts is not None else None,
                        deadline=deadline,
                    )
                else:
                    _append(journal, reservation)
                    outcome = send_once(body)
                receipt = outcome if type(outcome) is WireReceipt else None
                success = receipt.outcome == "accepted" if receipt is not None else outcome
                require(type(success) is bool, "attempt_plan.success")
                event = {
                    "event": "finished",
                    "attempt": ticket,
                    "state": "completed" if success else "failed",
                }
                if receipt is not None:
                    event["wire"] = asdict(receipt)
                if response_artifact is not None:
                    event["response_artifact"] = response_artifact
                _append(journal, event)
                # Do not expose a terminal state before it is durable.
                gate.complete(ticket, success=success)
                if receipt is not None and not success:
                    break
        return gate.rows
