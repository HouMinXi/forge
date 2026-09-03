# SPDX-License-Identifier: Apache-2.0
"""Append-only result ledger for resumable eval runs (Phase 58-2).

A 30-hour run cannot afford to restart from zero because a worker died at
entry 90. Each entry's outcome is appended to a JSONL file as it
completes, and a restart reads that file to decide what still needs
running.

Three properties matter and each one is a failure that has bitten this
project before:

* **The write must survive concurrency.** Pool workers finish in any
  order, so the line and its newline are written under ``flock`` with
  ``O_APPEND``. Two writers interleaving half-lines produces a file that
  parses as valid JSON right up until it does not.

* **A crash must not poison the resume.** A worker SIGKILLed mid-write
  leaves a torn trailing line. That line is truncated on load and its
  entry treated as never-run, which is the honest reading -- the entry
  did not finish.

* **SKIPPED is not done.** ``scorer.py`` excludes SKIPPED from the
  denominator, so an entry lost to a backend outage shrinks the corpus
  without shrinking any number the report prints. Resume retries those
  entries up to a cap rather than accepting the smaller corpus.

The resume key is ``(entry_id, depth, engine, backend)`` rather than the
entry id alone: arms differ only in depth and engine, and a shared key
would let the depth-2 arm skip everything depth-1 already recorded.
"""
from __future__ import annotations

import fcntl
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

# An entry that ended SKIPPED gets this many further attempts on resume
# before it is reported as skipped for good.
DEFAULT_RETRY_CAP = 2


@dataclass(frozen=True)
class ResumeKey:
    """Identifies one (entry, arm) pair.

    Frozen so it can key a dict; the arm coordinates are part of the
    identity because the same entry runs once per arm.
    """

    entry_id: str
    depth: int
    engine: str
    backend: str

    def as_dict(self) -> dict:
        return {
            "entry_id": self.entry_id,
            "depth": self.depth,
            "engine": self.engine,
            "backend": self.backend,
        }

    @staticmethod
    def from_record(rec: dict) -> "ResumeKey":
        return ResumeKey(
            entry_id=rec["entry_id"],
            depth=rec["depth"],
            engine=rec["engine"],
            backend=rec["backend"],
        )


def append_record(path: Path, record: dict) -> None:
    """Append one JSON record as a single line, atomically.

    The lock is held across the write and the flush so a concurrent
    writer cannot land between the payload and its newline. O_APPEND
    makes the kernel place the write at the current end of file even if
    another process extended it since this one opened the descriptor.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def read_records(path: Path) -> tuple[list[dict], bool]:
    """Read all complete records; report whether a torn line was dropped.

    Returns (records, truncated). A trailing line without its newline, or
    one that fails to parse, is discarded: the process writing it did not
    finish, so the entry did not finish either.

    Only the LAST line can legitimately be torn. A parse failure earlier
    in the file means something else corrupted it, and that is raised
    rather than silently skipped -- quietly dropping a mid-file record
    would make the resume think an entry never ran and duplicate it.
    """
    if not path.exists():
        return [], False

    raw = path.read_bytes()
    if not raw:
        return [], False

    truncated = not raw.endswith(b"\n")
    lines = raw.decode("utf-8", errors="replace").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if truncated and lines:
        # A missing newline usually means a torn write, but not always:
        # a manual edit or an external tool can leave a complete record
        # without one. Parse it before discarding -- dropping a valid
        # record makes resume re-run an entry that already finished, and
        # nothing reports that it happened.
        tail = lines[-1]
        try:
            json.loads(tail)
        except json.JSONDecodeError:
            lines.pop()  # genuinely torn
        else:
            truncated = False  # complete record, just unterminated

    records: list[dict] = []
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(
                "ledger %s is corrupt at line %d: %s "
                "(only the final line may be torn)" % (path, i + 1, exc)
            ) from exc
    return records, truncated


def load_state(
    path: Path, retry_cap: int = DEFAULT_RETRY_CAP
) -> tuple[dict[ResumeKey, dict], bool]:
    """Build the resume map: key -> the record that decides its fate.

    Later records for the same key supersede earlier ones, so a retry
    that succeeded replaces the SKIPPED attempt that preceded it.

    Entries that are still SKIPPED after ``retry_cap`` attempts stay in
    the map as done-for-good; anything else that is SKIPPED is left out
    so the caller re-runs it.
    """
    records, truncated = read_records(path)

    attempts: dict[ResumeKey, int] = {}
    latest: dict[ResumeKey, dict] = {}
    for rec in records:
        key = ResumeKey.from_record(rec)
        attempts[key] = attempts.get(key, 0) + 1
        latest[key] = rec

    done: dict[ResumeKey, dict] = {}
    for key, rec in latest.items():
        if rec.get("verdict") != "SKIPPED":
            done[key] = rec
        elif attempts[key] > retry_cap:
            # Out of retries: keep it, and let the report say why.
            done[key] = rec
    return done, truncated


def pending_keys(
    all_keys: list[ResumeKey],
    path: Path,
    retry_cap: int = DEFAULT_RETRY_CAP,
) -> list[ResumeKey]:
    """Which keys still need running, in the caller's order."""
    done, _ = load_state(path, retry_cap=retry_cap)
    return [k for k in all_keys if k not in done]


def make_record(
    key: ResumeKey,
    verdict: str,
    *,
    runs: int = 0,
    caught: int = 0,
    wall_s: float = 0.0,
    skipped_reason: str = "",
    rounds: Optional[int] = None,
) -> dict:
    """Build one ledger record.

    ``verdict`` carries SKIPPED explicitly rather than being inferred
    from a missing field, so a reader never has to guess whether an entry
    was skipped or simply written by an older version of this code.
    """
    rec = dict(key.as_dict())
    rec.update(
        {
            "verdict": verdict,
            "runs": runs,
            "caught": caught,
            "wall_s": round(wall_s, 3),
        }
    )
    if skipped_reason:
        rec["skipped_reason"] = skipped_reason
    if rounds is not None:
        rec["rounds"] = rounds
    return rec


def iter_arm_records(path: Path, depth: int, engine: str) -> Iterator[dict]:
    """Records belonging to one arm, for reporting."""
    records, _ = read_records(path)
    for rec in records:
        if rec.get("depth") == depth and rec.get("engine") == engine:
            yield rec
