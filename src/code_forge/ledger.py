# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Append-only outcome ledger for reviewed findings.

Stores one JSON object per line at `<cwd>/.code-forge/ledger.jsonl`.
Survives across sessions so the self-evolution arc has a durable
record of every terminal disposition (FIXED / DISPROVED / DUPLICATE /
ESCAPED). Rows carry base/head SHAs so Phase 44 can re-extract the
reviewed diff for downstream case generation.

Cooperating writers serialize final-byte observation and append with a
process guard and a same-file OS lock, using one read/write descriptor.
Only a non-LF tail receives a separator; original bytes are never repaired.
Lock waiting has a single one-second cooperative budget, not a deadline for
filesystem calls or total append latency. Effective filesystem locking must
be qualified on each platform; no PIPE_BUF guarantee applies to regular files.

Read/write access is required, including for an empty ledger. Windows locks
byte zero, which can temporarily deny readers access to that byte, and writes
new rows in binary LF format. Readers remain unlocked and strict consumers
still reject old corruption. Unrelated closes can defeat process-owned flock
emulation; hostile/older writers, weak network locks and undetected path races
are outside this guarantee. Stable symlink aliases are supported, not hardened.
There is no fsync or exactly-once promise: a failed append may leave bytes,
and callers must not automatically replay it. Existing LOCAL caller exceptions
can skip subsequent state persistence; this writer does not change that behavior.
The writer requires native CPython RLock ownership support and fails closed
before filesystem creation otherwise. Cleanup covers operation-raised exceptions,
not arbitrary asynchronous delivery before the final guard cleanup invocation or
between raw descriptor acquisition and its Python assignment; those interruptions
can retain resources and require process restart.
"""

from __future__ import annotations

import _thread
import errno
import hashlib
import json
import os
import stat
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from enum import Enum
from pathlib import Path
from types import BuiltinMethodType
from typing import Callable, Final, Iterator

_TRUNCATION_MARKER = "... [truncated]"
_MAX_EVIDENCE_LEN = 500
_LEDGER_APPEND_LOCK = threading.RLock()
_LEDGER_LOCK_TIMEOUT_SECONDS = 1.0
_LEDGER_LOCK_POLL_SECONDS = 0.01


def _truncate_evidence(text: str) -> str:
    """Truncate evidence to <= 500 chars with an explicit marker (D-07, D-21)."""
    if len(text) <= _MAX_EVIDENCE_LEN:
        return text
    prefix_len = _MAX_EVIDENCE_LEN - len(_TRUNCATION_MARKER)
    return text[:prefix_len] + _TRUNCATION_MARKER


def resolve_ledger_root(cwd: Path) -> Path:
    """Resolve the main repo root for durable ledger storage (D-05, D-11, D-20b).

    In a git repository (including linked worktrees), returns the parent directory
    of the git common-dir (the main repo worktree root). On any failure (non-git cwd,
    git execution failure, or OS error), falls back to returning cwd unchanged.
    """
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if res.returncode == 0:
            out = res.stdout.strip()
            if out:
                common_dir = Path(out).resolve()
                return common_dir.parent
    except Exception:  # noqa: S110, BLE001 -- Ledger root discovery is best-effort; failure falls back to cwd.
        pass
    return cwd


class TerminalState(str, Enum):
    """Outcomes recorded in the ledger: four terminal + one pending-adjudication.

    Entry rules:
      - FIXED: enters via the local state machine hook on fix confirmation or via adjudication.
      - DISPROVED: enters via the local state machine hook on falsifier rejection or via adjudication.
      - DUPLICATE: enters via `code-forge ledger mark --new` or via adjudication.
      - ESCAPED: enters via `code-forge ledger mark --new` or via adjudication.
      - UNADJUDICATED: enters via CI review runs on confirmed findings pending human adjudication.
    """

    FIXED = "FIXED"
    DISPROVED = "DISPROVED"
    DUPLICATE = "DUPLICATE"
    ESCAPED = "ESCAPED"
    UNADJUDICATED = "UNADJUDICATED"


@dataclass(frozen=True)
class LedgerRow:
    """One row of the outcome ledger. Schema v1.1.

    The backend and ctx_* fields carry model attribution: which backend
    raised a finding, and which auxiliary context the pipeline fed the
    review that round. They are additive with defaults so v1 rows -- every
    row written before this schema -- keep parsing.
    """

    fingerprint: str
    repo_root: str
    base_sha: str
    head_sha: str
    file: str
    line: int
    axis_claim: str
    pass_provenance: str
    terminal_state: TerminalState
    evidence_class: str
    ts: str  # ISO-8601 UTC
    version_sensitive: bool = False
    # Backend that produced the finding; "" for human-entered mark rows.
    backend: str = ""
    # Auxiliary context this review round fed the model. Pipeline-internal
    # only: what the CALLING agent consulted is invisible to forge.
    ctx_graph_triage: bool = False
    ctx_contract: bool = False
    ctx_whole_file: bool = False
    ctx_canary: bool = False
    suppression_key: str = ""


def _ledger_path(cwd: Path) -> Path:
    return cwd / ".code-forge" / "ledger.jsonl"


def _ledger_guard_backend() -> tuple[Callable, Callable, Callable]:
    """Bind qualified native ownership operations before filesystem creation."""
    guard = _LEDGER_APPEND_LOCK
    if sys.implementation.name != "cpython" or type(guard) is not getattr(_thread, "RLock", None):
        raise OSError("ledger: native process locking unavailable")
    try:
        methods = (guard.acquire, guard._is_owned, guard.release)
    except AttributeError as exc:
        raise OSError("ledger: native process locking unavailable") from exc
    if any(
        not isinstance(method, BuiltinMethodType) or method.__self__ is not guard for method in methods
    ):
        raise OSError("ledger: native process locking unavailable")
    return methods


def _ledger_lock_backend() -> tuple[str, Callable, int, int]:
    """Select a lazy, nonblocking OS lock before creating filesystem objects."""
    try:
        if os.name == "posix":
            import fcntl

            function = fcntl.flock
            modes = (fcntl.LOCK_EX, fcntl.LOCK_NB, fcntl.LOCK_UN)
            if not callable(function) or any(type(mode) is not int for mode in modes):
                raise OSError("ledger: locking unavailable")
            return "posix", function, modes[0] | modes[1], modes[2]
        if os.name == "nt":
            import msvcrt

            function = msvcrt.locking
            modes = (msvcrt.LK_NBLCK, msvcrt.LK_UNLCK, os.O_BINARY)
            if not callable(function) or any(type(mode) is not int for mode in modes):
                raise OSError("ledger: locking unavailable")
            return "nt", function, modes[0], modes[1]
    except (ImportError, AttributeError) as exc:
        raise OSError("ledger: locking unavailable") from exc
    raise OSError("ledger: locking unavailable")


@contextmanager
def _locked_ledger_writer(
    path: Path,
    backend: tuple[str, Callable, int, int],
    guard_backend: tuple[Callable, Callable, Callable] | None = None,
) -> Iterator[int]:
    """Own guard, descriptor and OS lock through unlock and the close attempt.

    One deadline covers both acquisitions. Only OS acquisition contention is
    retried. Close is attempted once, even on cancellation; cleanup failures
    remain visible without replacing an earlier exception. Forking while the
    process guard is held can cause a safe timeout; no fork recovery is added.
    """
    platform, lock, acquire_mode, release_mode = backend
    acquire, is_owned, release = _ledger_guard_backend() if guard_backend is None else guard_backend
    if is_owned():
        raise TimeoutError("ledger: lock acquisition timed out")
    deadline = time.monotonic() + _LEDGER_LOCK_TIMEOUT_SECONDS
    fd = None
    locked = False
    primary = None
    unlock_error = None
    close_error = None
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not acquire(timeout=remaining):
            raise TimeoutError("ledger: lock acquisition timed out")
        if time.monotonic() >= deadline:
            raise TimeoutError("ledger: lock acquisition timed out")
        flags = os.O_RDWR | os.O_CREAT | os.O_APPEND
        if platform == "nt":
            flags |= os.O_BINARY
        fd = os.open(path, flags, 0o666)
        _check_ledger_identity(path, fd)
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("ledger: lock acquisition timed out")
            if platform == "nt":
                os.lseek(fd, 0, os.SEEK_SET)
            try:
                if platform == "nt":
                    lock(fd, acquire_mode, 1)
                else:
                    lock(fd, acquire_mode)
            except OSError as exc:
                contention = (
                    (errno.EACCES,)
                    if platform == "nt"
                    else (errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES)
                )
                if exc.errno != errno.EINTR and exc.errno not in contention:
                    raise
            else:
                locked = True
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("ledger: lock acquisition timed out")
            time.sleep(min(_LEDGER_LOCK_POLL_SECONDS, remaining))
        if time.monotonic() >= deadline:
            raise TimeoutError("ledger: lock acquisition timed out")
        _check_ledger_identity(path, fd)
        yield fd
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            try:
                if locked:
                    if platform == "nt":
                        os.lseek(fd, 0, os.SEEK_SET)
                        lock(fd, release_mode, 1)
                    else:
                        lock(fd, release_mode)
            except BaseException as exc:  # noqa: BLE001 - cleanup must survive cancellation
                unlock_error = exc
            finally:
                try:
                    if fd is not None:
                        os.close(fd)
                except BaseException as exc:  # noqa: BLE001 - retain the primary failure
                    close_error = exc
        finally:
            if is_owned():
                release()
        failure = primary if primary is not None else unlock_error
        if failure is None:
            failure = close_error
        if failure is not None:
            # Bounded, literal notes avoid formatting arbitrary exception objects
            # and leave cause/context/suppression untouched, even for reused errors.
            # A diagnostic allocation failure must never replace the real failure.
            try:
                if unlock_error is not None:
                    BaseException.add_note(failure, "ledger: OS unlock failed during cleanup")
                if close_error is not None:
                    BaseException.add_note(failure, "ledger: descriptor close failed during cleanup")
                if unlock_error is not None or close_error is not None:
                    BaseException.__setattr__(
                        failure, "_ledger_cleanup_errors", (unlock_error, close_error)
                    )
            except BaseException:  # noqa: BLE001, S110 - diagnostics must not mask failure
                pass
            if primary is None:
                raise failure


def _check_ledger_identity(path: Path, fd: int) -> os.stat_result:
    """Fail closed unless descriptor and current path name the same regular file."""
    current = os.fstat(fd)
    named = os.stat(path)
    if (
        not stat.S_ISREG(current.st_mode)
        or not stat.S_ISREG(named.st_mode)
        or not current.st_ino
        or not named.st_ino
        or (current.st_dev, current.st_ino) != (named.st_dev, named.st_ino)
    ):
        raise OSError("ledger: file identity unavailable or changed")
    return current


def _ledger_append_separator(fd: int, size: int) -> bytes:
    """Inspect exactly one final byte, without parsing or modifying old bytes."""
    if size == 0:
        return b""
    os.lseek(fd, size - 1, os.SEEK_SET)
    tail = os.read(fd, 1)
    if len(tail) != 1:
        raise OSError("ledger: incomplete tail read")
    return b"" if tail == b"\n" else b"\n"


def append_row(cwd: Path, row: LedgerRow) -> None:
    """Append one row, preserving all original bytes and strict-reader authority.

    Requires read/write access and qualified same-file locking. Contention may
    raise TimeoutError after one cooperative second. Windows byte-zero locks
    may deny concurrent readers access; there is no unlocked fallback.
    Evidence retains its <= 500-character conversion (D-07, D-21). Exactly one
    unbuffered write adds a conditional LF and the serialized row. A short write
    fails without retry or rollback. Unlock/close failures also prevent success,
    even when the row has already been written; no power-loss durability is added.
    Native ownership support is required; same-thread reentry fails promptly.
    Arbitrary cancellation before cleanup invocation or descriptor assignment is
    outside the bounded cleanup guarantee and can require process restart.
    """
    payload = asdict(row)
    payload["terminal_state"] = row.terminal_state.value
    payload["evidence_class"] = _truncate_evidence(row.evidence_class)
    line = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
    backend = _ledger_lock_backend()
    guard_backend = _ledger_guard_backend()
    path = _ledger_path(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _locked_ledger_writer(path, backend, guard_backend) as fd:
        before = _check_ledger_identity(path, fd)
        separator = _ledger_append_separator(fd, before.st_size)
        data = separator + line
        if _check_ledger_identity(path, fd).st_size != before.st_size:
            raise OSError("ledger: size changed before append")
        if os.write(fd, data) != len(data):
            raise OSError("ledger: incomplete append")
        _check_ledger_identity(path, fd)


_SUPPRESSIBLE_TERMINAL_STATES: Final[frozenset[str]] = frozenset(
    {
        "FIXED",
        "DISPROVED",
        "DUPLICATE",
    }
)
"""Terminal states whose fingerprint suppresses a re-appearing CONFIRMED finding (D-23)."""


def known_terminal_fingerprints(root: Path) -> set[str]:
    """Return fingerprints whose LATEST row is FIXED, DISPROVED, or DUPLICATE.

    Latest = last in iteration order (append-only file -> last write wins).
    UNADJUDICATED and ESCAPED do NOT suppress (D-23, D-25).
    Missing ledger or empty ledger -> empty set (no crash).
    """
    latest: dict[str, tuple[str, str]] = {}  # fp -> (terminal_state, _)
    for r in iter_rows(root):
        latest[r.fingerprint] = (r.terminal_state.value, r.ts)
    return {fp for fp, (state, _) in latest.items() if state in _SUPPRESSIBLE_TERMINAL_STATES}


def finding_suppression_key(
    *,
    base_sha: str | None,
    head_sha: str | None,
    git_diff: str | None,
    file: str,
    line: int,
    source: str,
    description: str,
) -> str:
    """Bind a ruling to the verbatim reviewed diff and exact finding claim."""
    if not base_sha or not head_sha or git_diff is None or not description:
        return ""
    payload = [base_sha, head_sha, git_diff, file, line, source, description]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=True).encode()).hexdigest()


def iter_rows(cwd: Path, *, strict: bool = False) -> Iterator[LedgerRow]:
    """Yield ledger rows from `<cwd>/.code-forge/ledger.jsonl`.

    Missing file -> empty iterator. Malformed lines are skipped with
    a warning to stderr (does not raise) so a corrupt tail cannot
    block downstream consumers. A strict caller gets an exception for any
    malformed or unknown row and must consume the entire iterator before
    relying on its append order.
    """
    path = _ledger_path(cwd)
    if not path.exists():
        return

    def unique_object(pairs):
        data = {}
        for key, value in pairs:
            if key in data:
                raise ValueError("duplicate ledger field: " + key)
            data[key] = value
        return data

    with path.open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line:
                if strict:
                    raise ValueError("ledger: empty row at line %d" % lineno)
                continue
            try:
                data = json.loads(line, object_pairs_hook=unique_object) if strict else json.loads(line)
            except ValueError as exc:
                if strict:
                    raise ValueError("ledger: malformed line %d: %s" % (lineno, exc)) from exc
                print(
                    "ledger: skipping malformed line %d: %s" % (lineno, exc),
                    file=sys.stderr,
                )
                continue
            try:
                if strict:
                    if not isinstance(data, dict) or set(data) - {f.name for f in fields(LedgerRow)}:
                        raise ValueError("unknown ledger fields or non-object row")
                    for key in (
                        "fingerprint",
                        "repo_root",
                        "base_sha",
                        "head_sha",
                        "file",
                        "axis_claim",
                        "pass_provenance",
                        "terminal_state",
                        "evidence_class",
                        "ts",
                    ):
                        if type(data[key]) is not str:
                            raise ValueError("non-string ledger field: " + key)
                    if not data["fingerprint"] or type(data["line"]) is not int or data["line"] < 0:
                        raise ValueError("invalid fingerprint or line")
                    for key in (
                        "version_sensitive",
                        "ctx_graph_triage",
                        "ctx_contract",
                        "ctx_whole_file",
                        "ctx_canary",
                    ):
                        if key in data and type(data[key]) is not bool:
                            raise ValueError("non-boolean ledger field: " + key)
                    for key in ("backend", "suppression_key"):
                        if key in data and type(data[key]) is not str:
                            raise ValueError("non-string ledger field: " + key)
                yield LedgerRow(
                    fingerprint=data["fingerprint"],
                    repo_root=data["repo_root"],
                    base_sha=data["base_sha"],
                    head_sha=data["head_sha"],
                    file=data["file"],
                    line=int(data["line"]),
                    axis_claim=data["axis_claim"],
                    pass_provenance=data["pass_provenance"],
                    terminal_state=TerminalState(data["terminal_state"]),
                    evidence_class=data["evidence_class"],
                    ts=data["ts"],
                    version_sensitive=data.get("version_sensitive", False),
                    # .get with a default, never data[...]: v1 rows carry
                    # none of these and a KeyError here would silently skip
                    # the entire pre-enrichment ledger.
                    backend=data.get("backend", ""),
                    ctx_graph_triage=data.get("ctx_graph_triage", False),
                    ctx_contract=data.get("ctx_contract", False),
                    ctx_whole_file=data.get("ctx_whole_file", False),
                    ctx_canary=data.get("ctx_canary", False),
                    suppression_key=data.get("suppression_key", ""),
                )
            except (KeyError, ValueError, TypeError) as exc:
                if strict:
                    raise ValueError("ledger: schema-invalid line %d: %s" % (lineno, exc)) from exc
                print(
                    "ledger: skipping schema-invalid line %d: %s" % (lineno, exc),
                    file=sys.stderr,
                )
                continue
