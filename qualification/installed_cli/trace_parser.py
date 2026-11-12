# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Bounded, fail-closed parsing of private Linux ``strace -f -ttt -T -yy`` evidence.

All returned content, including ``complete``, ``issues``, events, paths and
arguments, is PRIVATE evidence. Parser completeness checks supported syntax,
chronology and descriptor evidence, never native outcomes, publication consistency,
qualification, durability or power-loss survival. The caller retains responsibility
for its own observations and must not publish this result.
"""

from __future__ import annotations

import errno
import ipaddress
import re

MAX_TRACE_BYTES = 32 * 1024 * 1024
MAX_EVENTS = 200_000  # Also caps input records, including unfinished entries.
MAX_LINE_CHARS = 65_536
_EXIT_SYSCALLS = frozenset({"exit", "exit_group"})
SYSCALLS = (
    frozenset(
        {"fsync", "fdatasync", "rename", "renameat", "renameat2", "open", "openat", "openat2", "close"}
    )
    | _EXIT_SYSCALLS
)
ISSUE_CODES = frozenset(
    {
        "empty_trace",
        "input_limit",
        "event_limit",
        "line_limit",
        "invalid_text",
        "missing_final_newline",
        "unknown_record",
        "unknown_syscall",
        "malformed_record",
        "truncated_record",
        "orphan_resumed",
        "unfinished_syscall",
        "overlapping_syscalls",
        "resumed_syscall_mismatch",
        "interrupted_syscall",
        "unknown_result",
        "missing_terminal_record",
        "event_after_terminal",
        "event_after_exit_call",
        "exit_status_mismatch",
        "time_order",
    }
)
_HEADER = re.compile(
    r"^(?:([1-9][0-9]{0,9})|\[pid\s+([1-9][0-9]{0,9})\])\s+([0-9]{1,12}\.[0-9]{1,9})\s+(.+)$"
)
_CALL = re.compile(r"^([a-z][a-z0-9_]*)\(")
_RESUMED = re.compile(r"^<\.\.\. ([a-z][a-z0-9_]*) resumed>(.*)$")
_DURATION = re.compile(r"^=\s+(.+?)\s+<([0-9]{1,12}\.[0-9]{1,9})>$")
_INTEGER = re.compile(r"^(?:0x[0-9a-fA-F]+|[0-9]{1,20})$")
_ERROR_NAMES = frozenset(
    name for name, value in vars(errno).items() if name.startswith("E") and isinstance(value, int)
) | {"ERESTARTSYS", "ERESTARTNOINTR", "ERESTARTNOHAND", "ERESTART_RESTARTBLOCK"}
_ERROR = re.compile(r"^(-1|\?) ([A-Z][A-Z0-9_]*)(?: \([^\r\n]*\))?$")
_EXIT = re.compile(r"^\+\+\+ exited with ([0-9]{1,3}) \+\+\+$")
_KILLED = re.compile(r"^\+\+\+ killed by (SIG[A-Z0-9_]+)(?: \(core dumped\))? \+\+\+$")
_SIGNAL = re.compile(r"^--- (SIG[A-Z0-9_]+) (\{.*\}) ---$")
_SOCKET_PROTOCOL = r"(?:(?:TCP|UDP)(?:v6)?|UNIX-(?:STREAM|DGRAM|SEQPACKET))"
_SOCKET_PREFIX = re.compile(rf"{_SOCKET_PROTOCOL}:\[")


class _InvalidRecord(ValueError):
    """An internal exception whose message is always a fixed issue code."""


def _ns(value: str) -> int:
    seconds, fraction = value.split(".")
    return int(seconds) * 1_000_000_000 + int(fraction.ljust(9, "0"))


def _precision_ns(value: str) -> int:
    return 10 ** (9 - len(value.partition(".")[2]))


def _decode_c(value: str) -> str:
    """Decode only strace's C byte escapes, never Python expressions."""
    output = bytearray()
    escapes = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, "\\": 92, '"': 34}
    position = 0
    while position < len(value):
        char = value[position]
        if char != "\\":
            if ord(char) < 32 or ord(char) == 127 or char == '"':
                raise _InvalidRecord("malformed_record")
            output.extend(char.encode("utf-8"))
            position += 1
            continue
        position += 1
        if position == len(value):
            raise _InvalidRecord("malformed_record")
        char = value[position]
        if char in escapes:
            output.append(escapes[char])
            position += 1
        elif char in "01234567":
            end = position + 1
            while end < min(position + 3, len(value)) and value[end] in "01234567":
                end += 1
            number = int(value[position:end], 8)
            if number > 255:
                raise _InvalidRecord("malformed_record")
            output.append(number)
            position = end
        elif char == "x" and re.fullmatch(r"[0-9a-fA-F]{2}", value[position + 1 : position + 3]):
            output.append(int(value[position + 1 : position + 3], 16))
            position += 3
        else:
            raise _InvalidRecord("malformed_record")
    return output.decode("utf-8", errors="surrogateescape")


def _scan(value: str, *, stop_at_close: bool = False) -> tuple[list[str], int]:
    """Split arguments without treating quoted strings or decoded FDs as syntax."""
    parts = []
    stack = []
    angle_starts = []
    quoted = escaped = False
    start = 0
    outside = []
    pairs = {")": "(", "]": "[", "}": "{", ">": "<"}
    for position, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if quoted or (stack and stack[-1] == "<"):
            if char == "\\":
                escaped = True
            elif quoted and char == '"':
                quoted = False
            elif not quoted and char == "<":
                stack.append("<")
                angle_starts.append(position)
            elif not quoted and char == ">":
                if value[position - 1] == "-" and _SOCKET_PREFIX.match(value, angle_starts[-1] + 1):
                    # A peer arrow is data only inside a known socket envelope.
                    # _fd validates the entire endpoint grammar afterward.
                    continue
                stack.pop()
                angle_starts.pop()
            continue
        if char == '"':
            quoted = True
        elif char in "([{<":
            stack.append(char)
            if char == "<":
                angle_starts.append(position)
        elif char in ")]}>":
            if char == ")" and not stack and stop_at_close:
                parts.append(value[start:position].strip())
                if "..." in "".join(outside):
                    raise _InvalidRecord("truncated_record")
                return parts, position
            if not stack or stack.pop() != pairs[char]:
                raise _InvalidRecord("malformed_record")
        elif char == "," and not stack:
            parts.append(value[start:position].strip())
            start = position + 1
        else:
            outside.append(char)
    if quoted or escaped or stack or stop_at_close:
        raise _InvalidRecord("malformed_record")
    if "..." in "".join(outside):
        raise _InvalidRecord("truncated_record")
    parts.append(value[start:].strip())
    return parts, len(value)


def _path(value: str) -> str | None:
    if value.startswith('"') and value.endswith('"'):
        return _decode_c(value[1:-1])
    if value == "NULL" or re.fullmatch(r"0x[0-9a-fA-F]{1,16}", value):
        return None  # EFAULT and unreadable pointers are still observable failures.
    raise _InvalidRecord("malformed_record")


def _socket_fd(value: str) -> dict | None:
    match = re.fullmatch(rf"(-?[0-9]{{1,10}})<({_SOCKET_PROTOCOL}):\[(.+)\]>", value)
    if not match:
        if re.match(rf"(?:-?[0-9]{{1,10}}|AT_FDCWD)<{_SOCKET_PROTOCOL}:", value):
            raise _InvalidRecord("malformed_record")
        return None
    number, protocol, endpoints = match.groups()
    inode = peer_inode = None
    addresses = []
    if protocol.startswith("UNIX-"):
        parts = endpoints.split("->")
        if not 1 <= len(parts) <= 2 or any(
            not re.fullmatch(r"[0-9]{1,20}", part) or int(part) > 0xFFFFFFFFFFFFFFFF for part in parts
        ):
            raise _InvalidRecord("malformed_record")
        inode = int(parts[0])
        peer_inode = int(parts[1]) if len(parts) == 2 else None
    elif re.fullmatch(r"[0-9]{1,20}", endpoints):
        inode = int(endpoints)
        if inode > 0xFFFFFFFFFFFFFFFF:
            raise _InvalidRecord("malformed_record")
    else:
        parts = endpoints.split("->")
        if not 1 <= len(parts) <= 2:
            raise _InvalidRecord("malformed_record")
        ipv6 = protocol.endswith("v6")
        for part in parts:
            endpoint = re.fullmatch(
                r"\[([0-9a-fA-F:.]+)\]:([0-9]{1,5})" if ipv6 else r"([0-9.]+):([0-9]{1,5})",
                part,
            )
            if not endpoint or int(endpoint[2]) > 65535:
                raise _InvalidRecord("malformed_record")
            try:
                (ipaddress.IPv6Address if ipv6 else ipaddress.IPv4Address)(endpoint[1])
            except ipaddress.AddressValueError:
                raise _InvalidRecord("malformed_record") from None
            addresses.append({"address": endpoint[1], "port": int(endpoint[2])})
    return {
        "number": int(number),
        "decoded": f"{protocol}:[{endpoints}]",
        "path": None,
        "deleted": False,
        "device_kind": None,
        "device_major": None,
        "device_minor": None,
        "socket_protocol": protocol,
        "socket_inode": inode,
        "socket_peer_inode": peer_inode,
        "socket_local": addresses[0] if addresses else None,
        "socket_peer": addresses[1] if len(addresses) == 2 else None,
    }


def _fd(value: str, *, dirfd: bool = False) -> dict:
    socket = _socket_fd(value)
    if socket is not None:
        return socket
    # -yy emits a nested, typed suffix for device nodes. Accept only this exact
    # suffix, never strip arbitrary angle-bracket text from a private pathname.
    match = re.fullmatch(
        r"(-?[0-9]{1,10}|AT_FDCWD)(?:<([^<>]*)(?:<(char|block) ([0-9]{1,10}):([0-9]{1,10})>)?>)?",
        value,
    )
    if not match or (match[1] == "AT_FDCWD" and not dirfd):
        raise _InvalidRecord("malformed_record")
    number = -100 if match[1] == "AT_FDCWD" else int(match[1])
    decoded = _decode_c(match[2]) if match[2] is not None else None
    device_kind = match[3]
    major, minor = (int(match[4]), int(match[5])) if device_kind else (None, None)
    if device_kind and (not decoded.startswith("/") or major > 0xFFFFFFFF or minor > 0xFFFFFFFF):
        raise _InvalidRecord("malformed_record")
    return {
        "number": number,
        "decoded": decoded,
        "path": decoded if decoded is not None and decoded.startswith("/") else None,
        "deleted": decoded is not None and decoded.endswith(" (deleted)"),
        "device_kind": device_kind,
        "device_major": major,
        "device_minor": minor,
    }


def _flags(value: str, prefix: str) -> list[str]:
    tokens = value.split("|")
    if not all(
        re.fullmatch(rf"{prefix}[A-Z0-9_]+", token) or _INTEGER.fullmatch(token) for token in tokens
    ):
        raise _InvalidRecord("malformed_record")
    return tokens


def _arguments(syscall: str, arguments: list[str]) -> dict:
    metadata: dict = {"arguments": arguments}
    if syscall in _EXIT_SYSCALLS:
        # strace prints the signed C int, not an arbitrary-width Python value.
        if len(arguments) != 1 or not re.fullmatch(r"-?(?:0|[1-9][0-9]{0,9})", arguments[0]):
            raise _InvalidRecord("malformed_record")
        argument = int(arguments[0])
        if not -(2**31) <= argument < 2**31:
            raise _InvalidRecord("malformed_record")
        metadata.update(exit_argument=argument, expected_exit_code=argument & 0xFF)
    elif syscall in {"fsync", "fdatasync", "close"}:
        if len(arguments) != 1:
            raise _InvalidRecord("malformed_record")
        metadata["fd"] = _fd(arguments[0])
    elif syscall == "rename":
        if len(arguments) != 2:
            raise _InvalidRecord("malformed_record")
        metadata.update(old_path=_path(arguments[0]), new_path=_path(arguments[1]))
    elif syscall in {"renameat", "renameat2"}:
        if len(arguments) != (5 if syscall == "renameat2" else 4):
            raise _InvalidRecord("malformed_record")
        metadata.update(
            old_dirfd=_fd(arguments[0], dirfd=True),
            old_path=_path(arguments[1]),
            new_dirfd=_fd(arguments[2], dirfd=True),
            new_path=_path(arguments[3]),
        )
        if syscall == "renameat2":
            metadata["flags"] = _flags(arguments[4], "RENAME_")
    else:
        offset = 0 if syscall == "open" else 1
        if syscall == "openat2":
            if len(arguments) != 4 or not _INTEGER.fullmatch(arguments[3]):
                raise _InvalidRecord("malformed_record")
        elif len(arguments) not in {2 + offset, 3 + offset}:
            raise _InvalidRecord("malformed_record")
        if offset:
            metadata["dirfd"] = _fd(arguments[0], dirfd=True)
        metadata["path"] = _path(arguments[offset])
        if syscall == "openat2":
            how = arguments[2]
            if not how.startswith("{") or not how.endswith("}"):
                raise _InvalidRecord("malformed_record")
            fields, _ = _scan(how[1:-1])
            parsed = {}
            for field in fields:
                key, separator, value = field.partition("=")
                if not separator or key in parsed or key not in {"flags", "mode", "resolve"}:
                    raise _InvalidRecord("malformed_record")
                parsed[key] = value
            if "flags" not in parsed:
                raise _InvalidRecord("malformed_record")
            metadata["flags"] = _flags(parsed["flags"], "O_")
            if "mode" in parsed and not _INTEGER.fullmatch(parsed["mode"]):
                raise _InvalidRecord("malformed_record")
            if "resolve" in parsed:
                metadata["resolve"] = _flags(parsed["resolve"], "RESOLVE_")
            metadata["open_how"] = parsed
        else:
            metadata["flags"] = _flags(arguments[1 + offset], "O_")
            if len(arguments) == 3 + offset:
                if not _INTEGER.fullmatch(arguments[-1]):
                    raise _InvalidRecord("malformed_record")
                metadata["mode"] = arguments[-1]
    return metadata


def _syscall(body: str, tid: int, started_ns: int, started_precision_ns: int) -> dict:
    match = _CALL.match(body)
    if not match:
        raise _InvalidRecord("malformed_record")
    syscall = match[1]
    if syscall not in SYSCALLS:
        raise _InvalidRecord("unknown_syscall")
    arguments, closing = _scan(body[match.end() :], stop_at_close=True)
    result_tail = body[match.end() + closing + 1 :].strip()
    result_match = _DURATION.fullmatch(result_tail)
    if result_match:
        result_text, duration_text = result_match.groups()
        duration_ns = _ns(duration_text)
        duration_precision_ns = _precision_ns(duration_text)
    elif syscall in _EXIT_SYSCALLS and re.fullmatch(r"=\s+\?", result_tail):
        result_text, duration_ns = "?", None
        duration_precision_ns = None
    else:
        raise _InvalidRecord("malformed_record")
    metadata = _arguments(syscall, arguments)
    event = {
        "kind": "exit_call" if syscall in _EXIT_SYSCALLS else "syscall",
        "syscall": syscall,
        "tid": tid,
        "started_ns": started_ns,
        "started_precision_ns": started_precision_ns,
        "ended_ns": started_ns + duration_ns if duration_ns is not None else None,
        "duration_ns": duration_ns,
        "duration_precision_ns": duration_precision_ns,
        "completed": True,
        "result": None,
        "error": None,
        **metadata,
    }
    if syscall in _EXIT_SYSCALLS:
        event.update(noreturn=result_text == "?", terminal_validated=False)
    error = _ERROR.fullmatch(result_text)
    if error:
        if error[2] not in _ERROR_NAMES:
            raise _InvalidRecord("malformed_record")
        event["result"] = -1 if error[1] == "-1" else None
        event["error"] = error[2]
        event["completed"] = error[1] != "?" and not error[2].startswith("ERESTART")
    elif result_text == "?":
        event["completed"] = False
    elif syscall in {"open", "openat", "openat2"}:
        returned_fd = _fd(result_text)
        if returned_fd["number"] < 0:
            raise _InvalidRecord("malformed_record")
        event["result"] = returned_fd["number"]
        event["returned_fd"] = returned_fd
    elif result_text == "0" and syscall not in _EXIT_SYSCALLS:
        event["result"] = 0
    else:
        raise _InvalidRecord("malformed_record")
    if event["result"] is not None and event["result"] >= 0:
        if any(metadata.get(key, "present") is None for key in ("path", "old_path", "new_path")):
            raise _InvalidRecord("malformed_record")
        if "fd" in metadata and metadata["fd"]["number"] < 0:
            raise _InvalidRecord("malformed_record")
    return event


def observed_end_ns(event: dict) -> int | None:
    """Return the later established end/return observation, never infer an end.

    A resumed timestamp may follow entry plus duration because of tracer
    observation delays. Both remain private evidence. In particular, a no-return
    exit with no terminal still has no established end, even if it was resumed.
    """
    if event.get("noreturn") and not event.get("terminal_validated"):
        return None
    ended_ns = event.get("ended_ns")
    if type(ended_ns) is not int or ended_ns < 0:
        return None
    resumed_ns = event.get("resumed_ns")
    if resumed_ns is None:
        return ended_ns
    if type(resumed_ns) is not int or resumed_ns < 0:
        return None
    return max(ended_ns, resumed_ns)


def _ends_after(event: dict, boundary_ns: int, boundary_precision_ns: int) -> bool:
    """Compare each end source independently within its printed precision."""
    if observed_end_ns(event) is None:
        return False  # Unresolved exits/records already make the trace incomplete.
    if event.get("duration_ns") is not None:
        precision = event["started_precision_ns"] + event["duration_precision_ns"]
    else:
        precision = event["terminal_precision_ns"]
    if event["ended_ns"] > boundary_ns + precision + boundary_precision_ns:
        return True
    return (
        event.get("resumed_ns") is not None
        and event["resumed_ns"] > boundary_ns + event["resumed_precision_ns"] + boundary_precision_ns
    )


def _chronology_issue(events: list[dict]) -> bool:
    """A TID cannot enter another syscall or terminate before its previous end."""
    previous = {}
    for event in events:
        if event["kind"] not in {"syscall", "exit_call", "exit", "killed"}:
            continue  # Signal observations may occur within an unfinished call.
        prior = previous.get(event["tid"])
        if prior is not None and _ends_after(prior, event["started_ns"], event["started_precision_ns"]):
            return True
        if event["kind"] in {"syscall", "exit_call"}:
            previous[event["tid"]] = event
    return False


def _annotate_sync_history(events: list[dict], unfinished_closes: list[tuple[int, int | None]]) -> None:
    """Link compatible recorded open history, never infer the current FD type."""
    syncs = [event for event in events if event.get("syscall") in {"fsync", "fdatasync"}]
    for event in syncs:
        event["sync_target_kind"] = "unknown"
        event["prior_directory_open_event"] = None
    if any(number is None for _, number in unfinished_closes):
        return  # An unresolved close with no readable FD makes every link ambiguous.

    # At tied timestamps, starts clear history before sync annotation, and
    # completions run last. An unresolved close has no completion action.
    actions = []
    for index, event in enumerate(events):
        syscall = event.get("syscall")
        if syscall in {"fsync", "fdatasync"}:
            actions.append((event["started_ns"], 1, index, "sync", event))
        elif syscall == "close" or "returned_fd" in event:
            operation = "close" if syscall == "close" else "open"
            ended_ns = observed_end_ns(event)
            actions.append((event["started_ns"], 0, index, operation + "_start", event))
            if ended_ns is not None:
                actions.append((ended_ns, 2, index, operation + "_end", event))
    for index, (started_ns, number) in enumerate(unfinished_closes, start=len(events)):
        actions.append((started_ns, 0, index, "close_start", {"fd": {"number": number}}))
    actions.sort(key=lambda action: action[:3])
    directories = {}
    states = {}
    opens = {}
    for _, _, index, action, event in actions:
        fd = event["returned_fd"] if action.startswith("open") else event["fd"]
        number = fd["number"]
        if action == "sync":
            prior = directories.get(number)
            if (
                prior is not None
                and prior[:2] == (event["tid"], fd["path"])
                and not fd["deleted"]
                and fd["device_kind"] is None
            ):
                event["prior_directory_open_event"] = prior[2]
            continue
        state = states.setdefault(number, {"generation": 0, "opens": 0, "closes": 0})
        if action.endswith("_start"):
            directories.pop(number, None)
            state["generation"] += 1
            if action == "close_start":
                state["closes"] += 1
            else:
                opens[index] = (state["generation"], bool(state["opens"] or state["closes"]))
                state["opens"] += 1
        elif action == "close_end":
            state["closes"] -= 1
        else:
            state["opens"] -= 1
            generation, overlapped = opens.pop(index)
            if (
                not overlapped
                and generation == state["generation"]
                and not state["opens"]
                and not state["closes"]
                and "O_DIRECTORY" in event["flags"]
                and fd["path"] is not None
                and not fd["deleted"]
                and fd["device_kind"] is None
            ):
                directories[number] = (event["tid"], fd["path"], index)


def parse_trace(text: str) -> dict:
    """Return private events and fixed issue codes; never infer success from absence.

    Timestamps are epoch nanoseconds derived without floating point. For resumed
    syscalls, ``started_ns`` is the unfinished entry timestamp, ``ended_ns`` is
    entry plus the reported duration, and ``resumed_ns`` retains the exit record's
    timestamp. ``observed_end_ns`` exposes the later established endpoint for
    containment and ordering; a short duration never erases a later resume.
    A syscall result of zero is meaningful only after checking
    ``completed``, the syscall name, and the enclosing trace's ``complete`` flag.
    A reported syscall end cannot outlast that TID's next syscall entry or
    terminal beyond printed resolution. Computed ends allow one start, duration,
    and boundary unit (3 us for six-decimal values); resumed ends independently
    allow one resume and boundary unit. Coarse evidence cannot mask a more precise
    contradiction. These bounds cover timestamp rounding, not scheduling.

    ``exit_call`` events retain the signed argument and low-eight-bit expected
    status. Their no-return ``?`` is not an unknown storage result: it requires
    the SAME TID's matching exit terminal or a killed terminal. Killed termination
    validates the observation but leaves ``completed`` false and never invents an
    exit status. Missing syscall duration stays None, even when the terminal
    supplies ``ended_ns``. A failed, returning exit syscall is not a no-return call.

    Every sync's ``sync_target_kind`` stays ``unknown``: the collector omits dup2
    and other implicit descriptor replacements. A matching old path/number does
    not prove current descriptor identity or type. ``prior_directory_open_event``
    is only an index into this event list for compatible recorded O_DIRECTORY-open
    history in the SAME TID, or None when absent/ambiguous. It does not assert that
    the descriptor is still open or refers to that historical object.
    Recorded history follows observed time: close and successful open entries in
    ANY TID clear links, syncs consult history at entry, and directory opens become
    candidates only after completion. Ties use that order; overlapping mutations
    and unfinished closes suppress links. This chronology considers only traced
    operations, not all possible descriptor mutations or filesystem changes.
    """
    if not isinstance(text, str):
        raise TypeError("trace must be text")
    issues: list[str] = []
    events: list[dict] = []

    def issue(code: str) -> None:
        if code not in issues:
            issues.append(code)

    def result() -> dict:
        return {"complete": not issues, "issues": issues, "events": events}

    if len(text) > MAX_TRACE_BYTES:
        issue("input_limit")
        return result()
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError:
        issue("invalid_text")
        return result()
    if size > MAX_TRACE_BYTES:
        issue("input_limit")
        return result()
    if not text:
        issue("empty_trace")
        return result()
    if not text.endswith("\n"):
        issue("missing_final_newline")
    seen = set()
    terminal = set()
    pending: dict[int, tuple[str, str, int, int]] = {}
    exit_calls: dict[int, dict] = {}
    unfinished_closes: dict[tuple[int, int], tuple[int, int | None]] = {}
    last_timestamp: dict[int, int] = {}

    # Only LF frames a physical record. Remove exactly the final split sentinel,
    # not any real blank records. Other Unicode separators can be field data.
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    for record_index, line in enumerate(lines):
        if record_index >= MAX_EVENTS or len(events) >= MAX_EVENTS:
            issue("event_limit")
            break
        if line.endswith("\r") and (record_index < len(lines) - 1 or text.endswith("\n")):
            line = line[:-1]  # Accept one CR only as part of a CRLF terminator.
        if len(line) > MAX_LINE_CHARS:
            issue("line_limit")
            continue
        header = _HEADER.fullmatch(line)
        if not header:
            issue("unknown_record")
            continue
        plain_tid, bracketed_tid, timestamp, body = header.groups()
        tid = int(plain_tid or bracketed_tid)
        started_ns = _ns(timestamp)
        started_precision_ns = _precision_ns(timestamp)
        if started_ns < last_timestamp.get(tid, 0):
            issue("time_order")
        last_timestamp[tid] = started_ns
        seen.add(tid)
        if tid in terminal:
            issue("event_after_terminal")
        event = None
        exited, killed, signal = _EXIT.fullmatch(body), _KILLED.fullmatch(body), _SIGNAL.fullmatch(body)
        if exited or killed:
            entry = pending.pop(tid, None)
            if entry is not None:
                name, prefix, entry_ns, entry_precision_ns = entry
                if name not in _EXIT_SYSCALLS:
                    issue("unfinished_syscall")
                else:
                    # A no-return syscall may never receive a resumed record.
                    # Only its fully printed integer argument is reconstructible.
                    prefix = prefix.rstrip()
                    reconstructed = prefix + ("" if prefix.endswith(")") else ")") + " = ?"
                    try:
                        exit_call = _syscall(reconstructed, tid, entry_ns, entry_precision_ns)
                    except _InvalidRecord as exc:
                        issue(str(exc))
                    else:
                        if tid in exit_calls:
                            issue("event_after_exit_call")
                        else:
                            exit_calls[tid] = exit_call
                        events.append(exit_call)
            terminal.add(tid)
            event = {
                "kind": "exit" if exited else "killed",
                "tid": tid,
                "started_ns": started_ns,
                "started_precision_ns": started_precision_ns,
                "ended_ns": started_ns,
                "exit_code": int(exited[1]) if exited else None,
                "signal": killed[1] if killed else None,
            }
            if exited and int(exited[1]) > 255:
                issue("malformed_record")
            exit_call = exit_calls.pop(tid, None)
            if exit_call is not None:
                status_matches = bool(exited and int(exited[1]) == exit_call["expected_exit_code"])
                if exited and not status_matches:
                    issue("exit_status_mismatch")
                exit_call.update(
                    terminal_validated=bool(killed) or status_matches,
                    terminal_kind=event["kind"],
                    terminal_ns=started_ns,
                    terminal_precision_ns=started_precision_ns,
                    terminal_exit_code=event["exit_code"],
                    terminal_signal=event["signal"],
                    completed=status_matches,
                )
                if exit_call["ended_ns"] is None:
                    exit_call["ended_ns"] = started_ns
        elif signal:
            try:
                _scan(signal[2])
            except _InvalidRecord as exc:
                issue(str(exc))
                continue
            event = {
                "kind": "signal",
                "tid": tid,
                "started_ns": started_ns,
                "ended_ns": started_ns,
                "signal": signal[1],
                "details": signal[2],
            }
        elif body.endswith("<unfinished ...>"):
            if tid in exit_calls:
                issue("event_after_exit_call")
            prefix = body[: -len("<unfinished ...>")]
            call = _CALL.match(prefix)
            if not call or call[1] not in SYSCALLS:
                issue("unknown_syscall" if call else "malformed_record")
                continue
            if tid in pending:
                issue("overlapping_syscalls")
            pending[tid] = (call[1], prefix, started_ns, started_precision_ns)
            if call[1] == "close":
                try:
                    fd = _fd(prefix[call.end() :].strip().removesuffix(")").rstrip())
                except _InvalidRecord:
                    number = None
                else:
                    number = fd["number"]
                unfinished_closes[(tid, started_ns)] = (started_ns, number)
        else:
            if tid in exit_calls:
                issue("event_after_exit_call")
            resumed = _RESUMED.fullmatch(body)
            resumed_ns = None
            resumed_precision_ns = None
            if resumed:
                entry = pending.pop(tid, None)
                if entry is None:
                    issue("orphan_resumed")
                    continue
                name, prefix, entry_ns, entry_precision_ns = entry
                if name != resumed[1]:
                    issue("resumed_syscall_mismatch")
                    continue
                resumed_ns = started_ns
                resumed_precision_ns = started_precision_ns
                if resumed_ns < entry_ns:
                    issue("time_order")
                body, started_ns = prefix + resumed[2], entry_ns
                started_precision_ns = entry_precision_ns
            elif tid in pending:
                issue("overlapping_syscalls")
            try:
                event = _syscall(body, tid, started_ns, started_precision_ns)
            except _InvalidRecord as exc:
                issue(str(exc))
                continue
            if resumed_ns is not None:
                event["resumed_ns"] = resumed_ns
                event["resumed_precision_ns"] = resumed_precision_ns
            if event.get("noreturn"):
                if tid not in exit_calls:
                    exit_calls[tid] = event
            elif not event["completed"]:
                issue("interrupted_syscall" if event["error"] else "unknown_result")
            if event["syscall"] == "close":
                unfinished_closes.pop((tid, started_ns), None)
        if event is not None:
            events.append(event)
    if pending:
        issue("unfinished_syscall")
    if seen - terminal:
        issue("missing_terminal_record")
    if not seen:
        issue("empty_trace")
    if _chronology_issue(events):
        issue("time_order")
    _annotate_sync_history(events, list(unfinished_closes.values()))
    return result()
