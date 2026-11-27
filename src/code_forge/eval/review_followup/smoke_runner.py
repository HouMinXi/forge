"""One-shot paired smoke orchestration, with only a caller-supplied transport."""

import json
import os
import uuid
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import BinaryIO

from .attempt_plan import AttemptRow, PairedAttemptGate
from .fixtures import InputError, digest, identifier, require
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
    """
    require(identifier(run_id) and type(request_bodies) is tuple, "smoke_run")
    gate.check_bodies(request_bodies)
    policy = {}
    if type(send_once) is DirectSmokeTransport:
        for body in request_bodies:
            send_once.check_body(body)
        policy["wire_policy"] = {
            "host": send_once.host,
            "model": send_once.model,
            "output_cap": send_once.output_cap,
            "response_cap": send_once.response_cap,
            "socket_timeout_s": send_once.timeout,
        }
    slots = tuple(row.slot for row in gate.rows)
    marker = digest(run_id.encode("ascii")) + ".jsonl"
    directory = os.open(journal_dir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        try:
            fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
        except FileExistsError as exc:
            raise InputError("smoke-run-already-started", run_id) from exc
        with os.fdopen(fd, "wb") as journal:
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
            for slot, body in zip(slots, request_bodies, strict=True):
                ticket = gate.reserve(slot.arm, slot.group, slot.pass_name, body)
                reservation = {"event": "reserved", "attempt": ticket, "state": "unknown_outcome"}
                if type(send_once) is DirectSmokeTransport:
                    request_id = uuid.uuid4().hex
                    reservation["client_request_id"] = request_id
                    _append(journal, reservation)
                    outcome = send_once.send(body, request_id=request_id)
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
                _append(journal, event)
                # Do not expose a terminal state before it is durable.
                gate.complete(ticket, success=success)
                if receipt is not None and not success:
                    break
        return gate.rows
    finally:
        os.close(directory)
