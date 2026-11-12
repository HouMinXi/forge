# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""The installed-CLI trace parser must never turn missing evidence into success."""

import json
import os
import stat

import pytest

from qualification.installed_cli import trace_parser
from qualification.installed_cli.trace_parser import ISSUE_CODES, observed_end_ns, parse_trace


def trace(*lines: str) -> str:
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize(
    "replacement",
    [
        pytest.param(False, id="directory-control"),
        pytest.param(True, id="implicit-dup2-replacement"),
    ],
)
def test_actual_sync_kind_stays_unknown_despite_matching_directory_open_history(tmp_path, replacement):
    from qualification.installed_cli.diagnostic_run import SYSCALLS as collector_syscalls

    assert "dup2" not in collector_syscalls.split(",")
    path = tmp_path / "object"
    path.mkdir(mode=0o700)
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    other = None
    try:
        assert stat.S_ISDIR(os.fstat(descriptor).st_mode)
        lines = [f'1 1.000001 open("{path}", O_RDONLY|O_DIRECTORY) = {descriptor}<{path}> <0.000001>']
        if replacement:
            path.rmdir()
            other = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            os.dup2(other, descriptor)
            lines.append(
                f'1 1.000003 open("{path}", O_RDWR|O_CREAT|O_EXCL, 0600) = {other}<{path}> <0.000001>'
            )
        actual_mode = os.fstat(descriptor).st_mode
        assert stat.S_ISREG(actual_mode) is replacement
        assert stat.S_ISDIR(actual_mode) is not replacement
        assert os.readlink(f"/proc/self/fd/{descriptor}") == str(path)
        os.fsync(descriptor)
        # Construct only the operations admitted by the collector filter; this
        # is a local kernel/projection control, not an actual strace capture.
        lines.extend(
            [
                f"1 1.000005 fsync({descriptor}<{path}>) = 0 <0.000001>",
                "1 1.000007 +++ exited with 0 +++",
            ]
        )
        parsed = parse_trace(trace(*lines))
        assert parsed["complete"] and parsed["issues"] == []
        opened, synced = parsed["events"][0], parsed["events"][-2]
        assert opened["flags"] == ["O_RDONLY", "O_DIRECTORY"]
        assert opened["returned_fd"]["number"] == synced["fd"]["number"] == descriptor
        assert opened["returned_fd"]["path"] == synced["fd"]["path"] == str(path)
        assert synced["result"] == 0 and synced["completed"]
        assert synced["prior_directory_open_event"] == 0
        assert synced["sync_target_kind"] == "unknown"
    finally:
        os.close(descriptor)
        if other is not None:
            os.close(other)


def test_directory_open_history_does_not_establish_sync_target_kind():
    parsed = parse_trace(
        trace(
            '101 1794360000.000001 openat(AT_FDCWD, "/tmp/private", O_RDONLY|O_DIRECTORY|O_CLOEXEC) = 3</tmp/private> <0.000020>',
            "101 1794360000.000031 fsync(3</tmp/private>) = 0 <0.000002>",
            "101 1794360000.000041 close(3</tmp/private>) = 0 <0.000001>",
            "101 1794360000.000050 +++ exited with 0 +++",
        )
    )
    assert parsed["complete"] is True
    assert parsed["issues"] == []
    sync = parsed["events"][1]
    assert sync["sync_target_kind"] == "unknown"
    assert sync["prior_directory_open_event"] == 0
    assert sync["started_ns"] == 1_794_360_000_000_031_000
    assert sync["ended_ns"] == 1_794_360_000_000_033_000
    assert sync["duration_ns"] == 2_000
    assert sync["result"] == 0
    assert sync["error"] is None


@pytest.mark.parametrize(
    "fd, open_flags",
    [("3</tmp/state>", "O_RDONLY"), ("3", "O_RDONLY|O_DIRECTORY"), ("3</tmp/other>", "O_DIRECTORY")],
)
def test_unproven_descriptor_kind_is_unknown_not_file(fd, open_flags):
    parsed = parse_trace(
        trace(
            f'1 1.000001 open("/tmp/state", {open_flags}) = 3</tmp/state> <0.000001>',
            f"1 1.000003 fsync({fd}) = 0 <0.000001>",
            "1 1.000005 +++ exited with 0 +++",
        )
    )
    assert parsed["complete"]
    assert parsed["events"][1]["sync_target_kind"] == "unknown"
    assert parsed["events"][1]["prior_directory_open_event"] is None


def test_openat2_and_renameat2_preserve_structured_private_arguments():
    parsed = parse_trace(
        trace(
            '11 10.000001 openat2(AT_FDCWD</tmp>, "private", {flags=O_RDONLY|O_DIRECTORY, resolve=RESOLVE_BENEATH}, 24) = 4</tmp/private> <0.000001>',
            '11 10.000003 renameat2(4</tmp/private>, "from", 4</tmp/private>, "to", RENAME_NOREPLACE) = 0 <0.000001>',
            "11 10.000005 fdatasync(4</tmp/private>) = -1 EINVAL (Invalid argument) <0.000001>",
            "11 10.000007 +++ exited with 1 +++",
        )
    )
    assert parsed["complete"]
    opened, renamed, synced, exited = parsed["events"]
    assert opened["dirfd"]["number"] == -100
    assert opened["resolve"] == ["RESOLVE_BENEATH"]
    assert renamed["old_path"] == "from"
    assert renamed["new_path"] == "to"
    assert renamed["flags"] == ["RENAME_NOREPLACE"]
    assert synced["result"] == -1 and synced["error"] == "EINVAL"
    assert synced["sync_target_kind"] == "unknown"
    assert synced["prior_directory_open_event"] == 0
    assert exited["exit_code"] == 1


def test_interleaved_unfinished_syscalls_are_joined_only_by_tid():
    parsed = parse_trace(
        trace(
            '12 10.000001 renameat(AT_FDCWD, "/tmp/first", <unfinished ...>',
            "13 10.000002 fsync(4</tmp/second> <unfinished ...>",
            '12 10.000006 <... renameat resumed>AT_FDCWD, "/tmp/renamed") = 0 <0.000005>',
            "13 10.000008 <... fsync resumed>) = -1 EIO (Input/output error) <0.000006>",
            "13 10.000009 +++ exited with 1 +++",
            "12 10.000010 +++ exited with 0 +++",
        )
    )
    assert parsed["complete"]
    rename, sync = parsed["events"][:2]
    assert rename["tid"] == 12 and rename["new_path"] == "/tmp/renamed"
    assert rename["started_ns"] == 10_000_001_000
    assert rename["ended_ns"] == rename["resumed_ns"] == 10_000_006_000
    assert sync["tid"] == 13 and sync["error"] == "EIO"
    assert sync["duration_ns"] == 6_000


def test_normal_signal_during_unfinished_call_does_not_steal_pending_entry():
    parsed = parse_trace(
        trace(
            "12 10.000001 fsync(3</tmp/private> <unfinished ...>",
            "12 10.000002 --- SIGUSR1 {si_signo=SIGUSR1, si_code=SI_USER, si_pid=20, si_uid=1000} ---",
            "12 10.000004 <... fsync resumed>) = -1 EINTR (Interrupted system call) <0.000003>",
            "12 10.000005 +++ exited with 1 +++",
        )
    )
    assert parsed["complete"]
    assert parsed["events"][0]["kind"] == "signal"
    assert parsed["events"][0]["signal"] == "SIGUSR1"
    assert parsed["events"][1]["error"] == "EINTR"


@pytest.mark.parametrize("restart", ["ERESTARTSYS", "ERESTARTNOINTR", "ERESTARTNOHAND"])
def test_restart_indeterminate_result_never_becomes_complete(restart):
    parsed = parse_trace(
        trace(
            f"5 10.000001 fsync(3</tmp/state>) = ? {restart} (To be restarted) <0.000020>",
            "5 10.000030 fsync(3</tmp/state>) = 0 <0.000001>",
            "5 10.000040 +++ exited with 0 +++",
        )
    )
    assert not parsed["complete"]
    assert "interrupted_syscall" in parsed["issues"]
    assert parsed["events"][0]["result"] is None
    assert parsed["events"][0]["error"] == restart
    assert parsed["events"][0]["completed"] is False


@pytest.mark.parametrize(
    "line, issue",
    [
        ("1 1.000001 <... fsync resumed>) = 0 <0.000001>", "orphan_resumed"),
        ("1 1.000001 fsync(3</tmp/a>) <unfinished ...>", "unfinished_syscall"),
        ("1 1.000001 getpid() = 1 <0.000001>", "unknown_syscall"),
        ("strace: Process 1 detached", "unknown_record"),
        ("1 1.000001 fsync(3) = 0", "malformed_record"),
        ("1 1.000001 fsync(3) = 12 <0.000001>", "malformed_record"),
        ("1 1.000001 fsync(3) = -2 EIO (error) <0.000001>", "malformed_record"),
        ('1 1.000001 rename("/tmp/short"..., "/tmp/to") = 0 <0.000001>', "truncated_record"),
        ('1 1.000001 rename("/tmp/short", "/tmp/to") = 0 <0.00', "malformed_record"),
        ("1 1.000001 fsync(3) = ? <0.000001>", "unknown_result"),
        ("1 1.000001 --- SIGTERM {si_signo=SIGTERM ---", "malformed_record"),
    ],
)
def test_unknown_malformed_incomplete_records_fail_closed(line, issue):
    parsed = parse_trace(trace(line, "1 2.000000 +++ exited with 0 +++"))
    assert parsed["complete"] is False
    assert issue in parsed["issues"]
    assert set(parsed["issues"]) <= ISSUE_CODES


def test_missing_thread_terminal_record_is_incomplete():
    parsed = parse_trace(
        trace(
            "1 1.000001 fsync(3) = 0 <0.000001>",
            "2 1.000002 fdatasync(4) = 0 <0.000001>",
            "1 1.000003 +++ exited with 0 +++",
        )
    )
    assert parsed["complete"] is False
    assert "missing_terminal_record" in parsed["issues"]


def test_killed_terminal_is_preserved_and_does_not_mean_success():
    parsed = parse_trace(
        trace(
            "1 1.000001 --- SIGSEGV {si_signo=SIGSEGV, si_code=SEGV_MAPERR, si_addr=NULL} ---",
            "1 1.000002 +++ killed by SIGSEGV (core dumped) +++",
        )
    )
    assert parsed["complete"]  # Complete evidence of a failure, not a success verdict.
    assert parsed["events"][-1]["kind"] == "killed"
    assert parsed["events"][-1]["signal"] == "SIGSEGV"
    assert parsed["events"][-1]["exit_code"] is None


def test_killed_unfinished_sync_is_incomplete():
    parsed = parse_trace(
        trace(
            "1 1.000001 fsync(3</tmp/private> <unfinished ...>",
            "1 1.000002 +++ killed by SIGKILL +++",
        )
    )
    assert not parsed["complete"]
    assert "unfinished_syscall" in parsed["issues"]
    assert any(event["kind"] == "killed" for event in parsed["events"])


@pytest.mark.parametrize(
    "middle",
    [
        "1 1.000003 close(3</tmp/directory>) = 0 <0.000001>",
        "2 1.000003 close(3</tmp/directory>) = 0 <0.000001>",
        '1 1.000003 open("/tmp/directory", O_RDONLY) = 3</tmp/directory> <0.000001>',
        '2 1.000003 open("/tmp/directory", O_RDONLY) = 3</tmp/directory> <0.000001>',
    ],
)
def test_observed_close_or_reuse_by_any_tid_clears_prior_directory_open(middle):
    lines = [
        '1 1.000001 open("/tmp/directory", O_RDONLY|O_DIRECTORY) = 3</tmp/directory> <0.000001>',
        middle,
        "1 1.000005 fsync(3</tmp/directory>) = 0 <0.000001>",
        "1 1.000007 +++ exited with 0 +++",
    ]
    if middle.startswith("2 "):
        lines.append("2 1.000007 +++ exited with 0 +++")
    parsed = parse_trace(trace(*lines))
    assert parsed["complete"]
    assert parsed["events"][2]["sync_target_kind"] == "unknown"
    assert parsed["events"][2]["prior_directory_open_event"] is None


def test_prior_directory_open_is_not_linked_across_tids():
    parsed = parse_trace(
        trace(
            '1 1.000001 open("/tmp/directory", O_DIRECTORY) = 3</tmp/directory> <0.000001>',
            "2 1.000003 fsync(3</tmp/directory>) = 0 <0.000001>",
            "2 1.000005 +++ exited with 0 +++",
            "1 1.000007 +++ exited with 0 +++",
        )
    )
    assert parsed["complete"]
    assert parsed["events"][1]["sync_target_kind"] == "unknown"
    assert parsed["events"][1]["prior_directory_open_event"] is None


def test_failed_open_never_supplies_prior_directory_open():
    parsed = parse_trace(
        trace(
            '1 1.000001 open("/tmp/directory", O_DIRECTORY) = -1 ENOENT (No such file or directory) <0.000001>',
            "1 1.000003 fsync(3</tmp/directory>) = -1 EBADF (Bad file descriptor) <0.000001>",
            "1 1.000005 +++ exited with 1 +++",
        )
    )
    assert parsed["complete"]
    assert parsed["events"][1]["sync_target_kind"] == "unknown"
    assert parsed["events"][1]["prior_directory_open_event"] is None


def test_hostile_escaped_paths_stay_private_and_cannot_inject_issue_text():
    marker = "PRIVATE_MARKER_68321"
    source = f'/tmp/{marker}\\n\\"), fake() = 0 <0.000001>\\\\\\377'
    parsed = parse_trace(
        trace(
            f'1 1.000001 rename("{source}", "/tmp/to,...") = -1 EACCES (Permission denied) <0.000001>',
            f"1 1.000003 {marker}",
            "1 1.000005 +++ exited with 1 +++",
        )
    )
    event = parsed["events"][0]
    assert event["old_path"] == f'/tmp/{marker}\n"), fake() = 0 <0.000001>\\\udcff'
    assert event["new_path"] == "/tmp/to,..."
    assert event["error"] == "EACCES"
    assert not parsed["complete"]
    assert marker not in json.dumps({"complete": parsed["complete"], "issues": parsed["issues"]})
    assert set(parsed["issues"]) <= ISSUE_CODES


@pytest.mark.parametrize("path", ['"/tmp/\\q"', '"/tmp/\\777"', '"/tmp/\\xQ0"'])
def test_invalid_c_escapes_are_rejected_without_evaluation(path):
    parsed = parse_trace(
        trace(f"1 1.000001 open({path}, O_RDONLY) = 3 <0.000001>", "1 1.000003 +++ exited with 0 +++")
    )
    assert not parsed["complete"]
    assert "malformed_record" in parsed["issues"]


@pytest.mark.parametrize(
    "text, issue",
    [
        ("", "empty_trace"),
        ("1 1.000000 +++ exited with 0 +++", "missing_final_newline"),
        ("\udcff", "invalid_text"),
    ],
)
def test_empty_or_byte_truncated_input_cannot_be_complete(text, issue):
    parsed = parse_trace(text)
    assert not parsed["complete"]
    assert issue in parsed["issues"]


def test_limits_fail_closed(monkeypatch):
    monkeypatch.setattr(trace_parser, "MAX_TRACE_BYTES", 10)
    assert parse_trace("x" * 11)["issues"] == ["input_limit"]
    assert parse_trace("\N{EURO SIGN}" * 4)["issues"] == ["input_limit"]
    monkeypatch.setattr(trace_parser, "MAX_TRACE_BYTES", 1000)
    monkeypatch.setattr(trace_parser, "MAX_LINE_CHARS", 10)
    assert "line_limit" in parse_trace("1 1.000000 +++ exited with 0 +++\n")["issues"]
    monkeypatch.setattr(trace_parser, "MAX_LINE_CHARS", 1000)
    monkeypatch.setattr(trace_parser, "MAX_EVENTS", 1)
    parsed = parse_trace(trace("1 1.000000 fsync(3) = 0 <0.000001>", "1 1.000002 +++ exited with 0 +++"))
    assert "event_limit" in parsed["issues"]
    assert not parsed["complete"] and len(parsed["events"]) == 1


def test_resumed_name_mismatch_and_overlapping_entries_fail_closed():
    parsed = parse_trace(
        trace(
            "1 1.000001 fsync(3 <unfinished ...>",
            "1 1.000002 fdatasync(3 <unfinished ...>",
            "1 1.000003 <... fsync resumed>) = 0 <0.000002>",
            "1 1.000004 +++ exited with 0 +++",
        )
    )
    assert not parsed["complete"]
    assert "overlapping_syscalls" in parsed["issues"]
    assert "resumed_syscall_mismatch" in parsed["issues"]


def test_event_after_exit_and_reversed_resume_time_fail_closed():
    parsed = parse_trace(
        trace(
            "1 1.000005 fsync(3 <unfinished ...>",
            "1 1.000003 <... fsync resumed>) = 0 <0.000002>",
            "1 1.000010 +++ exited with 0 +++",
            "1 1.000011 fsync(3) = 0 <0.000001>",
        )
    )
    assert not parsed["complete"]
    assert "time_order" in parsed["issues"]
    assert "event_after_terminal" in parsed["issues"]


def test_error_pointer_and_open_mode_are_parsed_without_inventing_paths():
    parsed = parse_trace(
        trace(
            "1 1.000001 rename(NULL, 0x12345) = -1 EFAULT (Bad address) <0.000001>",
            '1 1.000003 openat(AT_FDCWD, "/tmp/state", O_WRONLY|O_CREAT|O_TRUNC, 0600) = 3</tmp/state> <0.000001>',
            "1 1.000005 +++ exited with 1 +++",
        )
    )
    assert parsed["complete"]
    assert parsed["events"][0]["old_path"] is None
    assert parsed["events"][0]["new_path"] is None
    assert parsed["events"][1]["mode"] == "0600"


@pytest.mark.parametrize("prefix", ["[pid 1", "1]", "[pid 1]]", "[1]"])
def test_malformed_tid_prefix_fails_closed(prefix):
    parsed = parse_trace(f"{prefix} 1.000001 +++ exited with 0 +++\n")
    assert not parsed["complete"]
    assert "unknown_record" in parsed["issues"]


def test_bracketed_tid_and_realtime_signal_forms_are_supported():
    parsed = parse_trace(
        trace(
            "[pid 1] 1.000001 --- SIGRT_3 {si_signo=SIGRT_3, si_code=SI_USER, si_pid=2} ---",
            "[pid 1] 1.000002 +++ killed by SIGRT_3 +++",
        )
    )
    assert parsed["complete"]
    assert parsed["events"][0]["tid"] == 1


def test_arbitrary_error_token_cannot_escape_in_error_field():
    parsed = parse_trace(
        trace(
            "1 1.000001 fsync(3) = -1 EPRIVATE_MARKER (private detail) <0.000001>",
            "1 1.000002 +++ exited with 0 +++",
        )
    )
    assert not parsed["complete"]
    assert parsed["issues"] == ["malformed_record"]
    assert not any(event.get("error") for event in parsed["events"])


@pytest.mark.parametrize(
    "call",
    ["fsync(-1) = 0", "open(NULL, O_RDONLY) = 3", 'rename(NULL, "/tmp/to") = 0'],
)
def test_impossible_success_is_not_accepted(call):
    parsed = parse_trace(trace(f"1 1.000001 {call} <0.000001>", "1 1.000003 +++ exited with 0 +++"))
    assert not parsed["complete"]
    assert parsed["issues"] == ["malformed_record"]


def test_deleted_descriptor_does_not_link_prior_directory_open():
    parsed = parse_trace(
        trace(
            '1 1.000001 open("/tmp/directory", O_DIRECTORY) = 3</tmp/directory> <0.000001>',
            "1 1.000003 fsync(3</tmp/directory (deleted)>) = 0 <0.000001>",
            "1 1.000005 +++ exited with 0 +++",
        )
    )
    assert parsed["complete"]
    assert parsed["events"][1]["sync_target_kind"] == "unknown"
    assert parsed["events"][1]["prior_directory_open_event"] is None


def test_unfinished_records_cannot_bypass_record_bound(monkeypatch):
    monkeypatch.setattr(trace_parser, "MAX_EVENTS", 2)
    parsed = parse_trace(
        trace(
            "1 1.000001 fsync(3 <unfinished ...>",
            "2 1.000002 fsync(3 <unfinished ...>",
            "3 1.000003 fsync(3 <unfinished ...>",
        )
    )
    assert not parsed["complete"]
    assert "event_limit" in parsed["issues"]


@pytest.mark.parametrize("syscall", ["exit", "exit_group"])
@pytest.mark.parametrize("duration", ["", " <0.000004>"])
def test_no_return_exit_call_requires_and_preserves_matching_terminal(syscall, duration):
    parsed = parse_trace(
        trace(
            f"123 10.000001 {syscall}(7) = ?{duration}",
            "123 10.000010 +++ exited with 7 +++",
        )
    )
    assert parsed["complete"]
    call, terminal = parsed["events"]
    assert call["kind"] == "exit_call" and call["syscall"] == syscall
    assert call["exit_argument"] == call["expected_exit_code"] == 7
    assert call["result"] is None and call["error"] is None
    assert call["noreturn"] and call["terminal_validated"] and call["completed"]
    assert call["terminal_kind"] == "exit" and call["terminal_exit_code"] == 7
    assert call["terminal_ns"] == terminal["started_ns"] == 10_000_010_000
    assert call["duration_ns"] == (4000 if duration else None)
    assert call["ended_ns"] == (10_000_005_000 if duration else 10_000_010_000)
    assert terminal["kind"] == "exit" and terminal["exit_code"] == 7


@pytest.mark.parametrize("syscall", ["exit", "exit_group"])
@pytest.mark.parametrize("closing", ["", ")"])
@pytest.mark.parametrize("killed", [False, True])
def test_unfinished_exit_call_resolves_only_against_its_terminal(syscall, closing, killed):
    terminal = "+++ killed by SIGKILL +++" if killed else "+++ exited with 7 +++"
    parsed = parse_trace(
        trace(f"123 10.000001 {syscall}(7{closing} <unfinished ...>", f"123 10.000010 {terminal}")
    )
    assert parsed["complete"]
    call, terminal_event = parsed["events"]
    assert call["kind"] == "exit_call" and call["exit_argument"] == 7
    assert call["terminal_validated"]
    assert call["completed"] is not killed
    assert call["terminal_exit_code"] == (None if killed else 7)
    assert call["terminal_signal"] == ("SIGKILL" if killed else None)
    assert call["result"] is None and call["duration_ns"] is None
    assert call["ended_ns"] == terminal_event["started_ns"]


def test_printed_no_return_exit_can_be_superseded_by_kill_without_fake_status():
    parsed = parse_trace(
        trace(
            "123 10.000001 exit_group(7) = ?",
            "123 10.000010 +++ killed by SIGKILL +++",
        )
    )
    assert parsed["complete"]
    call, terminal = parsed["events"]
    assert call["expected_exit_code"] == 7 and call["terminal_exit_code"] is None
    assert call["terminal_validated"] and not call["completed"]
    assert terminal["kind"] == "killed" and terminal["exit_code"] is None


@pytest.mark.parametrize(
    "argument, status", [(256, 0), (263, 7), (-1, 255), (-257, 255), (2147483647, 255), (-2147483648, 0)]
)
def test_linux_exit_status_masks_low_eight_bits_without_losing_signed_argument(argument, status):
    parsed = parse_trace(
        trace(
            f"123 10.000001 exit_group({argument}) = ?",
            f"123 10.000010 +++ exited with {status} +++",
        )
    )
    assert parsed["complete"]
    call = parsed["events"][0]
    assert call["arguments"] == [str(argument)] and call["exit_argument"] == argument
    assert call["expected_exit_code"] == status and call["terminal_exit_code"] == status


@pytest.mark.parametrize(
    "argument",
    [
        "2147483648",
        "4294967296",
        "-2147483649",
        "99999999999999999999999",
        "7, 8",
        '"7"',
        "",
        "0x7",
        "+7",
    ],
)
def test_malformed_or_non_c_int_exit_argument_cannot_create_a_terminal_status(argument):
    parsed = parse_trace(
        trace(f"123 10.000001 exit_group({argument}) = ?", "123 10.000010 +++ exited with 0 +++")
    )
    assert not parsed["complete"]
    assert "malformed_record" in parsed["issues"]
    assert not any(event["kind"] == "exit_call" for event in parsed["events"])


@pytest.mark.parametrize("call", ["exit_group(263) = ?", "exit(263 <unfinished ...>"])
def test_exit_status_mismatch_is_incomplete_even_with_a_zero_terminal(call):
    parsed = parse_trace(trace(f"123 10.000001 {call}", "123 10.000010 +++ exited with 0 +++"))
    assert not parsed["complete"]
    assert "exit_status_mismatch" in parsed["issues"]
    exit_call = next(event for event in parsed["events"] if event["kind"] == "exit_call")
    assert not exit_call["terminal_validated"] and not exit_call["completed"]
    assert exit_call["expected_exit_code"] == 7 and exit_call["terminal_exit_code"] == 0


@pytest.mark.parametrize("call", ["exit_group(0) = ?", "exit(0 <unfinished ...>"])
def test_exit_call_without_own_terminal_is_incomplete(call):
    parsed = parse_trace(trace(f"123 10.000001 {call}", "124 10.000010 +++ exited with 0 +++"))
    assert not parsed["complete"]
    assert "missing_terminal_record" in parsed["issues"]


def test_interleaved_exit_calls_and_resumed_exit_keep_thread_statuses_separate():
    parsed = parse_trace(
        trace(
            "123 10.000001 exit_group(7 <unfinished ...>",
            "124 10.000002 exit(0) = ?",
            "123 10.000003 <... exit_group resumed>) = ?",
            "124 10.000004 +++ exited with 0 +++",
            "123 10.000005 +++ exited with 7 +++",
        )
    )
    assert parsed["complete"]
    calls = {event["tid"]: event for event in parsed["events"] if event["kind"] == "exit_call"}
    assert calls[123]["exit_argument"] == 7 and calls[123]["resumed_ns"] == 10_000_003_000
    assert calls[124]["exit_argument"] == 0
    assert all(call["terminal_validated"] for call in calls.values())


@pytest.mark.parametrize(
    "intervening", ["fsync(3) = 0 <0.000001>", "exit_group(0) = ?", "close(3 <unfinished ...>"]
)
def test_syscalls_after_no_return_exit_are_not_silently_accepted(intervening):
    parsed = parse_trace(
        trace(
            "123 10.000001 exit_group(0) = ?",
            f"123 10.000003 {intervening}",
            "123 10.000010 +++ exited with 0 +++",
        )
    )
    assert not parsed["complete"]
    assert "event_after_exit_call" in parsed["issues"]


def test_signal_observation_between_no_return_exit_and_terminal_is_allowed():
    parsed = parse_trace(
        trace(
            "123 10.000001 exit_group(0) = ?",
            "123 10.000003 --- SIGTERM {si_signo=SIGTERM, si_code=SI_USER, si_pid=124, si_uid=1000} ---",
            "123 10.000010 +++ killed by SIGTERM +++",
        )
    )
    assert parsed["complete"]
    assert parsed["events"][0]["terminal_signal"] == "SIGTERM"
    assert parsed["events"][1]["kind"] == "signal"


def test_returning_failed_exit_is_retained_and_does_not_predict_terminal_status():
    parsed = parse_trace(
        trace(
            "123 10.000001 exit_group(7) = -1 EPERM (Operation not permitted) <0.000001>",
            "123 10.000003 exit_group(0) = ?",
            "123 10.000010 +++ exited with 0 +++",
        )
    )
    assert parsed["complete"]
    failure, noreturn, _ = parsed["events"]
    assert failure["kind"] == "exit_call" and failure["result"] == -1
    assert failure["error"] == "EPERM" and not failure["noreturn"]
    assert noreturn["terminal_validated"]


@pytest.mark.parametrize(
    "call",
    [
        "exit_group(0) = 0 <0.000001>",
        "exit_group(0) =",
        "exit_group(0 <unfinished ...> PRIVATE",
        "exit_group(7, <unfinished ...>",
    ],
)
def test_bad_exit_syntax_still_fails_closed(call):
    parsed = parse_trace(trace(f"123 10.000001 {call}", "123 10.000010 +++ exited with 0 +++"))
    assert not parsed["complete"]
    assert set(parsed["issues"]) <= ISSUE_CODES
    assert "malformed_record" in parsed["issues"]


def test_unfinished_storage_call_does_not_become_complete_when_another_tid_exits():
    parsed = parse_trace(
        trace(
            "123 10.000001 fsync(3 <unfinished ...>",
            "124 10.000002 exit_group(0) = ?",
            "123 10.000003 +++ exited with 0 +++",
            "124 10.000004 +++ exited with 0 +++",
        )
    )
    assert not parsed["complete"]
    assert "unfinished_syscall" in parsed["issues"]


@pytest.mark.parametrize("device_kind, major, minor", [("char", 1, 9), ("block", 8, 16)])
def test_strace73_nested_device_fd_annotations_are_typed_private_metadata(device_kind, major, minor):
    decoded = f"3</dev/urandom<{device_kind} {major}:{minor}>>"
    parsed = parse_trace(
        trace(
            f'123 10.000001 openat(AT_FDCWD, "/dev/urandom", O_RDONLY|O_CLOEXEC) = {decoded} <0.000012>',
            f"123 10.000020 fsync({decoded}) = -1 EINVAL (Invalid argument) <0.000002>",
            f"123 10.000030 close({decoded}) = 0 <0.000007>",
            "123 10.000040 exit_group(0) = ?",
            "123 10.000050 +++ exited with 0 +++",
        )
    )
    assert parsed["complete"]
    opened, synced, closed = parsed["events"][:3]
    for fd in (opened["returned_fd"], synced["fd"], closed["fd"]):
        assert fd["number"] == 3 and fd["path"] == "/dev/urandom"
        assert fd["decoded"] == "/dev/urandom"
        assert fd["device_kind"] == device_kind
        assert fd["device_major"] == major and fd["device_minor"] == minor
    assert synced["sync_target_kind"] == "unknown"
    assert synced["prior_directory_open_event"] is None


@pytest.mark.parametrize(
    "returned_annotation, synced_annotation",
    [("<char 1:9>", ""), ("", "<char 1:9>"), ("<block 8:16>", "<block 8:16>")],
)
def test_annotated_device_does_not_link_prior_directory_open(returned_annotation, synced_annotation):
    parsed = parse_trace(
        trace(
            f'123 10.000001 openat(AT_FDCWD, "/tmp/private", O_RDONLY|O_DIRECTORY) = 3</tmp/private{returned_annotation}> <0.000001>',
            f"123 10.000003 fsync(3</tmp/private{synced_annotation}>) = 0 <0.000001>",
            "123 10.000005 +++ exited with 0 +++",
        )
    )
    assert parsed["complete"]
    assert parsed["events"][1]["sync_target_kind"] == "unknown"
    assert parsed["events"][1]["prior_directory_open_event"] is None


@pytest.mark.parametrize(
    "fd",
    [
        "3</tmp/path<PRIVATE_MARKER>>",
        "3</tmp/path<char 1:9>/tail>",
        "3</tmp/path<nested<char 1:9>>>",
        "3</tmp/path<char 1:9><block 8:16>>",
        "3</tmp/path<char x:9>>",
        "3</tmp/path<char -1:9>>",
        "3</tmp/path<char 4294967296:9>>",
        "3</tmp/path<char 1:4294967296>>",
        "3</tmp/path<socket 1:9>>",
        "3<socket:[123]<char 1:9>>",
        "3</tmp/path<char 1:9>",
        "3</tmp/path<char 1:9>>>",
    ],
)
def test_ambiguous_or_unknown_nested_fd_text_is_not_blindly_stripped(fd):
    parsed = parse_trace(
        trace(f"123 10.000001 close({fd}) = 0 <0.000001>", "123 10.000003 +++ exited with 0 +++")
    )
    assert not parsed["complete"]
    assert "malformed_record" in parsed["issues"]
    assert "PRIVATE_MARKER" not in json.dumps(parsed["issues"])


def test_escaped_literal_angles_in_directory_path_are_not_device_annotation():
    escaped = r"/tmp/private\074char 1:9\076"
    parsed = parse_trace(
        trace(
            f'123 10.000001 openat(AT_FDCWD, "{escaped}", O_RDONLY|O_DIRECTORY) = 3<{escaped}> <0.000001>',
            f"123 10.000003 fsync(3<{escaped}>) = 0 <0.000001>",
            "123 10.000005 +++ exited with 0 +++",
        )
    )
    assert parsed["complete"]
    opened, synced = parsed["events"][:2]
    assert opened["returned_fd"]["path"] == "/tmp/private<char 1:9>"
    assert opened["returned_fd"]["device_kind"] is None
    assert synced["sync_target_kind"] == "unknown"
    assert synced["prior_directory_open_event"] == 0


def test_trace_runner_allowlist_is_supported_by_parser():
    from qualification.installed_cli.diagnostic_run import SYSCALLS as runner_syscalls

    assert set(runner_syscalls.split(",")) <= trace_parser.SYSCALLS


@pytest.mark.parametrize(
    "annotation, protocol, local, peer",
    [
        ("TCP:[127.0.0.1:60390->127.0.0.1:33353]", "TCP", "127.0.0.1", "127.0.0.1"),
        ("UDP:[127.0.0.1:1234->192.0.2.1:53]", "UDP", "127.0.0.1", "192.0.2.1"),
        ("TCPv6:[[::1]:60390->[::1]:33353]", "TCPv6", "::1", "::1"),
        ("UDPv6:[[2001:db8::1]:53]", "UDPv6", "2001:db8::1", None),
    ],
)
def test_socket_peer_arrows_are_data_only_in_validated_socket_envelopes(
    annotation, protocol, local, peer
):
    parsed = parse_trace(
        trace(
            f"1595932 1794392671.765053 close(5<{annotation}>) = 0 <0.000076>",
            "1595932 1794392671.766000 exit_group(0) = ?",
            "1595932 1794392671.767000 +++ exited with 0 +++",
        )
    )
    assert parsed["complete"]
    fd = parsed["events"][0]["fd"]
    assert fd["decoded"] == annotation and fd["socket_protocol"] == protocol
    assert fd["path"] is None and fd["device_kind"] is None
    assert fd["socket_local"]["address"] == local
    assert (fd["socket_peer"]["address"] if fd["socket_peer"] else None) == peer


@pytest.mark.parametrize(
    "protocol", ["TCP", "UDP", "TCPv6", "UDPv6", "UNIX-STREAM", "UNIX-DGRAM", "UNIX-SEQPACKET"]
)
def test_known_unconnected_socket_inode_annotations_are_private_non_paths(protocol):
    parsed = parse_trace(
        trace(
            f"123 10.000001 close(5<{protocol}:[12345]>) = 0 <0.000001>",
            "123 10.000003 +++ exited with 0 +++",
        )
    )
    assert parsed["complete"]
    fd = parsed["events"][0]["fd"]
    assert fd["socket_inode"] == 12345 and fd["socket_peer_inode"] is None
    assert fd["path"] is None and fd["socket_local"] is None and fd["socket_peer"] is None


@pytest.mark.parametrize("protocol", ["UNIX-STREAM", "UNIX-DGRAM", "UNIX-SEQPACKET"])
def test_unix_socket_peer_inode_arrows_are_validated_without_path_inference(protocol):
    parsed = parse_trace(
        trace(
            f"123 10.000001 close(5<{protocol}:[12345->67890]>) = 0 <0.000001>",
            "123 10.000003 +++ exited with 0 +++",
        )
    )
    assert parsed["complete"]
    fd = parsed["events"][0]["fd"]
    assert fd["socket_inode"] == 12345 and fd["socket_peer_inode"] == 67890
    assert fd["socket_protocol"] == protocol and fd["path"] is None


def test_socket_does_not_link_prior_directory_open_for_same_number():
    parsed = parse_trace(
        trace(
            '123 10.000001 open("/tmp/private", O_DIRECTORY) = 5</tmp/private> <0.000001>',
            "123 10.000003 fsync(5<TCP:[127.0.0.1:12345->127.0.0.1:6789]>) = -1 EINVAL (Invalid argument) <0.000001>",
            "123 10.000005 +++ exited with 0 +++",
        )
    )
    assert parsed["complete"]
    assert parsed["events"][1]["sync_target_kind"] == "unknown"
    assert parsed["events"][1]["prior_directory_open_event"] is None


@pytest.mark.parametrize(
    "annotation",
    [
        "TCP:[127.0.0.1:12345->127.0.0.1:65536]",
        "TCP:[127.0.0.999:12345->127.0.0.1:80]",
        "TCP:[127.0.0.1:12345->127.0.0.1:80->127.0.0.1:81]",
        "TCP:[127.0.0.1:12345->PRIVATE_MARKER]",
        "TCPv6:[[not-an-address]:12345->[::1]:80]",
        "TCPv6:[::1:12345->::1:80]",
        "TCP:[]",
        "TCP:[18446744073709551616]",
        "TCP:[127.0.0.1:12345->127.0.0.1:80]<char 1:9>",
        "UNIX-STREAM:[12345->18446744073709551616]",
        "UNIX-DGRAM:[12345->PRIVATE_MARKER]",
        "UNIX-SEQPACKET:[12345->67890->98765]",
        "UNKNOWN:[12345->67890]",
        "/tmp/PRIVATE_MARKER->file",
        "/tmp/TCP:[127.0.0.1:12345->127.0.0.1:80]",
    ],
)
def test_socket_support_does_not_generically_strip_arrows_or_malformed_endpoints(annotation):
    parsed = parse_trace(
        trace(
            f"123 10.000001 close(5<{annotation}>) = 0 <0.000001>", "123 10.000003 +++ exited with 0 +++"
        )
    )
    assert not parsed["complete"]
    assert "malformed_record" in parsed["issues"]
    assert "PRIVATE_MARKER" not in json.dumps(parsed["issues"])


@pytest.mark.parametrize("sync", ["fsync", "fdatasync"])
@pytest.mark.parametrize("close_result", ["0", "-1 EINTR (Interrupted system call)"])
def test_observed_cross_tid_split_close_clears_open_history_at_entry(sync, close_result):
    parsed = parse_trace(
        trace(
            '1 10.000001 openat(AT_FDCWD, "/tmp/directory", O_DIRECTORY|O_RDONLY) = 4</tmp/directory> <0.000001>',
            "2 10.000003 close(4</tmp/directory> <unfinished ...>",
            f"1 10.000005 {sync}(4</tmp/directory>) = 0 <0.000001>",
            f"2 10.000010 <... close resumed>) = {close_result} <0.000007>",
            "1 10.000020 +++ exited with 0 +++",
            "2 10.000020 +++ exited with 0 +++",
        )
    )
    assert parsed["complete"]
    assert (
        next(event for event in parsed["events"] if event.get("syscall") == sync)["sync_target_kind"]
        == "unknown"
    )
    assert (
        next(event for event in parsed["events"] if event.get("syscall") == sync)[
            "prior_directory_open_event"
        ]
        is None
    )


@pytest.mark.parametrize("output_order", ["before_open", "after_sync"])
@pytest.mark.parametrize(
    "mutation", ["close(4</tmp/directory>) = 0", 'open("/tmp/directory", O_RDONLY) = 4</tmp/directory>']
)
def test_completed_cross_tid_fd_mutations_follow_timestamps_not_output_order(output_order, mutation):
    opened = '1 10.000001 open("/tmp/directory", O_DIRECTORY) = 4</tmp/directory> <0.000001>'
    synced = "1 10.000005 fsync(4</tmp/directory>) = 0 <0.000001>"
    mutated = f"2 10.000003 {mutation} <0.000010>"
    lines = [mutated, opened, synced] if output_order == "before_open" else [opened, synced, mutated]
    parsed = parse_trace(
        trace(*lines, "1 10.000020 +++ exited with 0 +++", "2 10.000020 +++ exited with 0 +++")
    )
    assert parsed["complete"]
    assert (
        next(event for event in parsed["events"] if event.get("syscall") == "fsync")["sync_target_kind"]
        == "unknown"
    )
    assert (
        next(event for event in parsed["events"] if event.get("syscall") == "fsync")[
            "prior_directory_open_event"
        ]
        is None
    )
    assert [event["syscall"] for event in parsed["events"][:3]] == (
        [mutation.split("(")[0], "open", "fsync"]
        if output_order == "before_open"
        else ["open", "fsync", mutation.split("(")[0]]
    )


@pytest.mark.parametrize(
    "mutation", ["close(4</tmp/directory>) = 0", 'open("/tmp/directory", O_RDONLY) = 4</tmp/directory>']
)
def test_observed_mutation_during_directory_open_prevents_history_link(mutation):
    parsed = parse_trace(
        trace(
            f"2 10.000003 {mutation} <0.000001>",
            '1 10.000001 open("/tmp/directory", O_DIRECTORY) = 4</tmp/directory> <0.000009>',
            "1 10.000011 fsync(4</tmp/directory>) = 0 <0.000001>",
            "1 10.000020 +++ exited with 0 +++",
            "2 10.000020 +++ exited with 0 +++",
        )
    )
    assert parsed["complete"]
    assert parsed["events"][2]["sync_target_kind"] == "unknown"
    assert parsed["events"][2]["prior_directory_open_event"] is None


def test_directory_open_overlapping_observed_close_cannot_restore_history_link():
    parsed = parse_trace(
        trace(
            "2 10.000001 close(4</tmp/directory> <unfinished ...>",
            '1 10.000003 open("/tmp/directory", O_DIRECTORY) = 4</tmp/directory> <0.000001>',
            "1 10.000005 fsync(4</tmp/directory>) = 0 <0.000001>",
            "2 10.000010 <... close resumed>) = 0 <0.000009>",
            "1 10.000020 +++ exited with 0 +++",
            "2 10.000020 +++ exited with 0 +++",
        )
    )
    assert parsed["complete"]
    assert parsed["events"][1]["sync_target_kind"] == "unknown"
    assert parsed["events"][1]["prior_directory_open_event"] is None


def test_fresh_directory_reopen_after_observed_close_can_supply_new_history_link():
    parsed = parse_trace(
        trace(
            '1 10.000001 open("/tmp/directory", O_DIRECTORY) = 4</tmp/directory> <0.000001>',
            "2 10.000003 close(4</tmp/directory> <unfinished ...>",
            "1 10.000005 fsync(4</tmp/directory>) = 0 <0.000001>",
            "2 10.000008 <... close resumed>) = 0 <0.000005>",
            '1 10.000010 open("/tmp/directory", O_DIRECTORY) = 4</tmp/directory> <0.000001>',
            "1 10.000012 fsync(4</tmp/directory>) = 0 <0.000001>",
            "1 10.000020 +++ exited with 0 +++",
            "2 10.000020 +++ exited with 0 +++",
        )
    )
    assert parsed["complete"]
    assert [
        event["sync_target_kind"] for event in parsed["events"] if event.get("syscall") == "fsync"
    ] == ["unknown", "unknown"]
    assert [
        event["prior_directory_open_event"]
        for event in parsed["events"]
        if event.get("syscall") == "fsync"
    ] == [None, 3]
    assert parsed["events"][3]["syscall"] == "open"
    assert parsed["events"][3]["started_ns"] == 10_000_010_000


def test_sync_tied_with_directory_open_completion_has_no_history_link():
    parsed = parse_trace(
        trace(
            '1 10.000001 open("/tmp/directory", O_DIRECTORY) = 4</tmp/directory> <0.000004>',
            "1 10.000005 fsync(4</tmp/directory>) = 0 <0.000001>",
            "1 10.000020 +++ exited with 0 +++",
        )
    )
    assert parsed["complete"]
    assert parsed["events"][1]["sync_target_kind"] == "unknown"
    assert parsed["events"][1]["prior_directory_open_event"] is None


@pytest.mark.parametrize(
    "mutation", ["close(4</tmp/directory>) = 0", 'open("/tmp/directory", O_RDONLY) = 4</tmp/directory>']
)
def test_observed_invalidation_tied_with_sync_entry_precedes_history_link(mutation):
    parsed = parse_trace(
        trace(
            '1 10.000001 open("/tmp/directory", O_DIRECTORY) = 4</tmp/directory> <0.000001>',
            "1 10.000005 fsync(4</tmp/directory>) = 0 <0.000001>",
            f"2 10.000005 {mutation} <0.000001>",
            "1 10.000020 +++ exited with 0 +++",
            "2 10.000020 +++ exited with 0 +++",
        )
    )
    assert parsed["complete"]
    assert parsed["events"][1]["sync_target_kind"] == "unknown"
    assert parsed["events"][1]["prior_directory_open_event"] is None


@pytest.mark.parametrize("fd_prefix", ["4</tmp/directory>", "4", ""])
def test_unresolved_observed_close_clears_prior_directory_open(fd_prefix):
    parsed = parse_trace(
        trace(
            '1 10.000001 open("/tmp/directory", O_DIRECTORY) = 4</tmp/directory> <0.000001>',
            f"2 10.000003 close({fd_prefix} <unfinished ...>",
            "1 10.000005 fsync(4</tmp/directory>) = 0 <0.000001>",
            "1 10.000020 +++ exited with 0 +++",
            "2 10.000020 +++ killed by SIGKILL +++",
        )
    )
    assert not parsed["complete"] and "unfinished_syscall" in parsed["issues"]
    assert parsed["events"][1]["sync_target_kind"] == "unknown"
    assert parsed["events"][1]["prior_directory_open_event"] is None


def test_observed_close_of_other_descriptor_retains_prior_directory_open():
    parsed = parse_trace(
        trace(
            '1 10.000001 open("/tmp/directory", O_DIRECTORY) = 4</tmp/directory> <0.000001>',
            "2 10.000003 close(5</tmp/other> <unfinished ...>",
            "1 10.000005 fsync(4</tmp/directory>) = 0 <0.000001>",
            "2 10.000010 <... close resumed>) = 0 <0.000007>",
            "1 10.000020 +++ exited with 0 +++",
            "2 10.000020 +++ exited with 0 +++",
        )
    )
    assert parsed["complete"]
    assert parsed["events"][1]["sync_target_kind"] == "unknown"
    assert parsed["events"][1]["prior_directory_open_event"] == 0


def test_resumed_directory_open_cannot_restore_history_cleared_while_pending():
    parsed = parse_trace(
        trace(
            '101 10.000001 openat(AT_FDCWD, "/tmp/d", O_RDONLY|O_DIRECTORY <unfinished ...>',
            "102 10.000003 close(4</tmp/d>) = -1 EBADF (Bad file descriptor) <0.000001>",
            "101 10.000010 <... openat resumed>) = 4</tmp/d> <0.000009>",
            "101 10.000011 fsync(4</tmp/d>) = 0 <0.000001>",
            "101 10.000020 +++ exited with 0 +++",
            "102 10.000020 +++ exited with 0 +++",
        )
    )
    assert parsed["complete"]
    assert parsed["events"][2]["sync_target_kind"] == "unknown"
    assert parsed["events"][2]["prior_directory_open_event"] is None


def test_close_interval_remains_ambiguous_until_later_resumed_record_timestamp():
    parsed = parse_trace(
        trace(
            "102 10.000003 close(4</tmp/d> <unfinished ...>",
            '101 10.000006 openat(AT_FDCWD, "/tmp/d", O_DIRECTORY) = 4</tmp/d> <0.000001>',
            "101 10.000008 fsync(4</tmp/d>) = 0 <0.000001>",
            "102 10.000010 <... close resumed>) = 0 <0.000002>",
            "101 10.000020 +++ exited with 0 +++",
            "102 10.000020 +++ exited with 0 +++",
        )
    )
    assert parsed["complete"]
    assert parsed["events"][1]["sync_target_kind"] == "unknown"
    assert parsed["events"][1]["prior_directory_open_event"] is None


@pytest.mark.parametrize("terminal", ["+++ exited with 0 +++", "+++ killed by SIGKILL +++"])
def test_reported_syscall_duration_cannot_extend_past_its_thread_terminal(terminal):
    parsed = parse_trace(
        trace("101 10.000001 fsync(3</tmp/file>) = 0 <1.000000>", f"101 10.000003 {terminal}")
    )
    assert not parsed["complete"]
    assert "time_order" in parsed["issues"]


@pytest.mark.parametrize(
    "start, duration, terminal, complete, precision",
    [
        ("10.000001", "0.000005", "10.000003", True, 1000),
        ("10.000001", "0.000006", "10.000003", False, 1000),
        ("10.000000001", "0.000000005", "10.000000003", True, 1),
        ("10.000000001", "0.000000006", "10.000000003", False, 1),
    ],
)
def test_terminal_timing_tolerance_is_bounded_by_printed_numeric_precision(
    start, duration, terminal, complete, precision
):
    parsed = parse_trace(
        trace(
            f"101 {start} fsync(3</tmp/file>) = 0 <{duration}>", f"101 {terminal} +++ exited with 0 +++"
        )
    )
    assert parsed["complete"] is complete
    assert parsed["events"][0]["started_precision_ns"] == precision
    assert parsed["events"][0]["duration_precision_ns"] == precision
    assert parsed["events"][1]["started_precision_ns"] == precision


def test_resumed_syscall_retains_original_entry_timestamp_precision():
    parsed = parse_trace(
        trace(
            "101 10.000001 fsync(3</tmp/file> <unfinished ...>",
            "101 10.000001500 <... fsync resumed>) = 0 <0.000000900>",
            "101 10.000001600 +++ exited with 0 +++",
        )
    )
    assert parsed["complete"]
    assert parsed["events"][0]["started_precision_ns"] == 1000
    assert parsed["events"][0]["duration_precision_ns"] == 1


def test_duration_is_checked_against_same_tid_terminal_only():
    parsed = parse_trace(
        trace(
            "101 10.000001 fsync(3</tmp/file>) = 0 <0.000020>",
            "102 10.000003 +++ exited with 0 +++",
            "101 10.000030 +++ exited with 0 +++",
        )
    )
    assert parsed["complete"]


@pytest.mark.parametrize(
    "event, expected",
    [
        ({}, None),
        ({"ended_ns": None, "resumed_ns": 20}, None),
        ({"ended_ns": 10}, 10),
        ({"ended_ns": 10, "resumed_ns": None}, 10),
        ({"ended_ns": 10, "resumed_ns": 20}, 20),
        ({"ended_ns": 20, "resumed_ns": 10}, 20),
        ({"ended_ns": 10, "resumed_ns": 10}, 10),
        ({"ended_ns": True}, None),
        ({"ended_ns": "10"}, None),
        ({"ended_ns": 10, "resumed_ns": "20"}, None),
        ({"ended_ns": -1}, None),
        ({"ended_ns": 10, "resumed_ns": -1}, None),
        ({"ended_ns": 10, "noreturn": True, "terminal_validated": False}, None),
    ],
)
def test_observed_end_is_conservative_and_does_not_invent_unknown_end(event, expected):
    assert observed_end_ns(event) == expected


@pytest.mark.parametrize(
    "resume, expected", [("10.000850", 10_000_850_000), ("10.001200", 10_001_200_000)]
)
def test_delayed_resume_is_the_observed_end_even_when_reported_duration_is_short(resume, expected):
    parsed = parse_trace(
        trace(
            '111 10.000510 openat(AT_FDCWD, "/tmp/d", O_DIRECTORY|O_RDONLY) = 4</tmp/d> <0.000001>',
            "111 10.000800 fsync(4</tmp/d> <unfinished ...>",
            f"111 {resume} <... fsync resumed>) = 0 <0.000001>",
            "111 10.001350 exit(0) = ?",
            "111 10.001360 +++ exited with 0 +++",
        )
    )
    assert parsed["complete"]
    sync = parsed["events"][1]
    assert sync["ended_ns"] == 10_000_801_000
    assert observed_end_ns(sync) == expected
    assert sync["resumed_precision_ns"] == 1000
    assert sync["sync_target_kind"] == "unknown"
    assert sync["prior_directory_open_event"] == 0


@pytest.mark.parametrize("duration, complete", [("0.000100", False), ("0.000050", True)])
@pytest.mark.parametrize("split", [False, True])
def test_same_tid_next_syscall_entry_must_follow_computed_end(duration, complete, split):
    first = (
        [
            "111 10.000520 fsync(3</tmp/file> <unfinished ...>",
            f"111 10.000570 <... fsync resumed>) = 0 <{duration}>",
        ]
        if split
        else [f"111 10.000520 fsync(3</tmp/file>) = 0 <{duration}>"]
    )
    parsed = parse_trace(
        trace(
            *first,
            "111 10.000600 fsync(3</tmp/file>) = 0 <0.000001>",
            "111 10.001000 +++ exited with 0 +++",
        )
    )
    assert parsed["complete"] is complete
    assert ("time_order" in parsed["issues"]) is not complete


@pytest.mark.parametrize("duration, complete", [("0.000100", False), ("0.000050", True)])
def test_ordinary_syscall_end_is_checked_against_next_split_syscall_entry(duration, complete):
    parsed = parse_trace(
        trace(
            f"111 10.000520 fsync(3</tmp/file>) = 0 <{duration}>",
            "111 10.000600 close(3</tmp/file> <unfinished ...>",
            "111 10.000650 <... close resumed>) = 0 <0.000001>",
            "111 10.001000 +++ exited with 0 +++",
        )
    )
    assert parsed["complete"] is complete
    assert ("time_order" in parsed["issues"]) is not complete


@pytest.mark.parametrize("duration, complete", [("0.000100", False), ("0.000050", True)])
def test_ordinary_syscall_end_cannot_cross_no_return_exit_entry(duration, complete):
    parsed = parse_trace(
        trace(
            f"111 10.000520 fsync(3</tmp/file>) = 0 <{duration}>",
            "111 10.000600 exit(0) = ?",
            "111 10.001000 +++ exited with 0 +++",
        )
    )
    assert parsed["complete"] is complete
    assert ("time_order" in parsed["issues"]) is not complete


@pytest.mark.parametrize(
    "start, duration, next_start, complete",
    [
        ("10.000520", "0.000083", "10.000600", True),
        ("10.000520", "0.000084", "10.000600", False),
        ("10.000000520", "0.000000083", "10.000000600", True),
        ("10.000000520", "0.000000084", "10.000000600", False),
    ],
)
def test_same_tid_entry_comparison_has_explicit_printed_precision_bound(
    start, duration, next_start, complete
):
    parsed = parse_trace(
        trace(
            f"111 {start} fsync(3</tmp/file>) = 0 <{duration}>",
            f"111 {next_start} close(3</tmp/file>) = 0 <0.000000001>",
            "111 10.001000 +++ exited with 0 +++",
        )
    )
    assert parsed["complete"] is complete
    assert ("time_order" in parsed["issues"]) is not complete


def test_precise_computed_end_cannot_be_masked_by_coarse_resumed_timestamp():
    parsed = parse_trace(
        trace(
            "111 10.000000520 fsync(3</tmp/file> <unfinished ...>",
            "111 10.000001 <... fsync resumed>) = 0 <0.000000580>",
            "111 10.000001001 close(3</tmp/file>) = 0 <0.000000001>",
            "111 10.000002 +++ exited with 0 +++",
        )
    )
    assert not parsed["complete"] and "time_order" in parsed["issues"]
    sync = parsed["events"][0]
    assert sync["started_precision_ns"] == sync["duration_precision_ns"] == 1
    assert sync["resumed_precision_ns"] == 1000


@pytest.mark.parametrize("next_start", ["10.000650", "10.000651"])
def test_same_tid_entry_tied_with_or_after_resume_is_valid(next_start):
    parsed = parse_trace(
        trace(
            "111 10.000520 fsync(3</tmp/file> <unfinished ...>",
            "111 10.000650 <... fsync resumed>) = 0 <0.000001>",
            f"111 {next_start} close(3</tmp/file>) = 0 <0.000001>",
            "111 10.001000 +++ exited with 0 +++",
        )
    )
    assert parsed["complete"]
    assert observed_end_ns(parsed["events"][0]) == 10_000_650_000


@pytest.mark.parametrize("split", [False, True])
def test_valid_different_tid_overlap_does_not_fence_syscalls_on_other_thread(split):
    lines = (
        [
            "111 10.000520 fsync(3</tmp/file> <unfinished ...>",
            "112 10.000600 fsync(5</tmp/other>) = 0 <0.000001>",
            "111 10.000620 <... fsync resumed>) = 0 <0.000100>",
        ]
        if split
        else [
            "111 10.000520 fsync(3</tmp/file>) = 0 <0.000100>",
            "112 10.000600 fsync(5</tmp/other>) = 0 <0.000001>",
        ]
    )
    parsed = parse_trace(
        trace(
            *lines,
            "111 10.000650 close(3</tmp/file>) = 0 <0.000001>",
            "111 10.001000 +++ exited with 0 +++",
            "112 10.001000 +++ exited with 0 +++",
        )
    )
    assert parsed["complete"]


def test_signal_and_eintr_between_split_calls_do_not_create_false_overlap():
    parsed = parse_trace(
        trace(
            "111 10.000520 fsync(3</tmp/file> <unfinished ...>",
            "111 10.000530 --- SIGUSR1 {si_signo=SIGUSR1, si_code=SI_USER, si_pid=222, si_uid=1000} ---",
            "111 10.000570 <... fsync resumed>) = -1 EINTR (Interrupted system call) <0.000050>",
            "111 10.000600 fsync(3</tmp/file>) = 0 <0.000001>",
            "111 10.001000 +++ exited with 0 +++",
        )
    )
    assert parsed["complete"]
    assert parsed["events"][0]["kind"] == "signal"
    assert parsed["events"][1]["error"] == "EINTR"
    assert observed_end_ns(parsed["events"][1]) == 10_000_570_000


@pytest.mark.parametrize("error", ["EIO (Input/output error)", "EINTR (Interrupted system call)"])
def test_failed_syscall_duration_still_cannot_overlap_next_entry(error):
    parsed = parse_trace(
        trace(
            f"111 10.000520 fsync(3</tmp/file>) = -1 {error} <0.000100>",
            "111 10.000600 fsync(3</tmp/file>) = 0 <0.000001>",
            "111 10.001000 +++ exited with 0 +++",
        )
    )
    assert not parsed["complete"] and "time_order" in parsed["issues"]


@pytest.mark.parametrize("duration", ["", " <0.000001>"])
def test_unvalidated_no_return_exit_keeps_observed_end_unknown(duration):
    parsed = parse_trace(f"111 10.000520 exit(0) = ?{duration}\n")
    assert not parsed["complete"] and "missing_terminal_record" in parsed["issues"]
    assert observed_end_ns(parsed["events"][0]) is None


def test_resumed_no_return_exit_has_no_observed_end_until_terminal():
    parsed = parse_trace(
        trace("111 10.000520 exit(0 <unfinished ...>", "111 10.000600 <... exit resumed>) = ?")
    )
    assert not parsed["complete"]
    assert parsed["events"][0]["resumed_ns"] == 10_000_600_000
    assert observed_end_ns(parsed["events"][0]) is None


@pytest.mark.parametrize("corrupt", [False, True])
def test_many_same_tid_calls_have_bounded_chronology_validation(corrupt):
    lines = [
        f"111 10.{index * 10:06d} fsync(3</tmp/file>) = 0 <{'0.000100' if corrupt and index == 500 else '0.000001'}>"
        for index in range(1000)
    ]
    parsed = parse_trace(trace(*lines, "111 10.020000 +++ exited with 0 +++"))
    assert parsed["complete"] is not corrupt
    assert len(parsed["events"]) == 1001


@pytest.mark.parametrize(
    "separator, complete",
    [
        pytest.param("\n", True, id="LF"),
        pytest.param("\r\n", True, id="CRLF"),
        pytest.param("\r", False, id="CR"),
        pytest.param("\v", False, id="VT"),
        pytest.param("\f", False, id="FF"),
        pytest.param("\x1c", False, id="FS"),
        pytest.param("\x1d", False, id="GS"),
        pytest.param("\x1e", False, id="RS"),
        pytest.param("\x85", False, id="NEL"),
        pytest.param("\u2028", False, id="LS"),
        pytest.param("\u2029", False, id="PS"),
    ],
)
@pytest.mark.parametrize(
    "ending",
    [
        pytest.param("+++ exited with 0 +++", id="exit"),
        pytest.param("+++ killed by SIGRT_3 +++", id="realtime-signal"),
    ],
)
def test_only_lf_delimits_physical_trace_records(separator, complete, ending):
    source = "123 10.000001 fsync(3) = 0 <0.000001>" + separator + "123 10.000003 " + ending + "\n"
    parsed = parse_trace(source)
    assert parsed["complete"] is complete
    if complete:
        assert parsed["issues"] == []
        assert len(parsed["events"]) == 2
        assert parsed["events"][0]["syscall"] == "fsync"
        assert parsed["events"][1]["kind"] == ("exit" if "exited" in ending else "killed")
    else:
        assert source.count("\n") == 1
        assert parsed["issues"]
        assert not any(event["kind"] in ("exit", "killed") for event in parsed["events"])


@pytest.mark.parametrize("terminator", ["\n", "\r\n"], ids=["LF", "CRLF"])
def test_physical_record_terminators_preserve_supported_event_fields(terminator):
    records = [
        '1 10.000001 open("/tmp/private", O_DIRECTORY) = 3</tmp/private> <0.000001>',
        "1 10.000003 fsync(3</tmp/private> <unfinished ...>",
        "1 10.000005 <... fsync resumed>) = 0 <0.000002>",
        "1 10.000007 close(3</tmp/private>) = 0 <0.000001>",
        "1 10.000009 +++ exited with 0 +++",
    ]
    expected = parse_trace("\n".join(records) + "\n")
    parsed = parse_trace(terminator.join(records) + terminator)
    assert parsed == expected and parsed["complete"]
    opened, synced, closed, terminal = parsed["events"]
    assert opened["returned_fd"]["path"] == "/tmp/private"
    assert synced["prior_directory_open_event"] == 0 and synced["sync_target_kind"] == "unknown"
    assert synced["started_ns"] == 10_000_003_000
    assert synced["resumed_ns"] == synced["ended_ns"] == 10_000_005_000
    assert closed["syscall"] == "close" and terminal["kind"] == "exit"


@pytest.mark.parametrize("terminator", ["\n", "\r\n"], ids=["LF", "CRLF"])
@pytest.mark.parametrize("difference", [-1, 0, 1], ids=["under-limit", "exact-limit", "over-limit"])
def test_line_limit_applies_to_physical_record_excluding_supported_terminator(terminator, difference):
    prefix, suffix = "1 10.000001 fsync(3</", ">) = 0 <0.000001>"
    length = trace_parser.MAX_LINE_CHARS + difference
    line = prefix + "x" * (length - len(prefix) - len(suffix)) + suffix
    assert len(line) == length
    parsed = parse_trace(line + terminator + "1 10.000003 +++ exited with 0 +++" + terminator)
    assert parsed["complete"] is (difference <= 0)
    assert ("line_limit" in parsed["issues"]) is (difference > 0)
    assert len(parsed["events"]) == (2 if difference <= 0 else 1)


@pytest.mark.parametrize(
    "separator, complete",
    [pytest.param("\n", True, id="many-LF-records"), pytest.param("\v", False, id="one-long-VT-record")],
)
def test_non_lf_framing_cannot_evade_physical_line_limit(separator, complete):
    records = [f"{number} 10.000003 +++ exited with 0 +++" for number in range(1, 2001)]
    source = separator.join(records) + "\n"
    parsed = parse_trace(source)
    assert parsed["complete"] is complete
    if complete:
        assert len(parsed["events"]) == 2000 and parsed["issues"] == []
    else:
        assert source.count("\n") == 1 and len(source) - 1 > trace_parser.MAX_LINE_CHARS
        assert "line_limit" in parsed["issues"] and parsed["events"] == []


@pytest.mark.parametrize(
    "ending, limit, complete, issues",
    [
        pytest.param("\n", 1, True, [], id="LF-sentinel-is-not-record"),
        pytest.param("\r\n", 1, True, [], id="CRLF-sentinel-is-not-record"),
        pytest.param("\n\n", 2, False, ["unknown_record"], id="trailing-LF-blank-record"),
        pytest.param("\r\n\r\n", 2, False, ["unknown_record"], id="trailing-CRLF-blank-record"),
        pytest.param("\n\n", 1, False, ["event_limit"], id="blank-record-counts-toward-bound"),
        pytest.param("\n\n\n", 2, False, ["unknown_record", "event_limit"], id="multiple-real-blanks"),
    ],
)
def test_physical_record_bound_counts_real_trailing_blanks(monkeypatch, ending, limit, complete, issues):
    monkeypatch.setattr(trace_parser, "MAX_EVENTS", limit)
    parsed = parse_trace("1 10.000003 +++ exited with 0 +++" + ending)
    assert parsed["complete"] is complete and parsed["issues"] == issues
    assert len(parsed["events"]) == 1 and parsed["events"][0]["kind"] == "exit"


@pytest.mark.parametrize("ending", ["", "\r", "\v"], ids=["EOF", "bare-CR", "bare-VT"])
def test_non_lf_terminal_endings_remain_incomplete(ending):
    parsed = parse_trace("1 10.000003 +++ exited with 0 +++" + ending)
    assert not parsed["complete"] and "missing_final_newline" in parsed["issues"]


@pytest.mark.parametrize(
    "encoded, decoded",
    [
        pytest.param("\x85", "\x85", id="literal-NEL-field"),
        pytest.param("\u2028", "\u2028", id="literal-LS-field"),
        pytest.param("\u2029", "\u2029", id="literal-PS-field"),
        pytest.param("\\r", "\r", id="escaped-CR-field"),
        pytest.param("\\v", "\v", id="escaped-VT-field"),
        pytest.param("\\f", "\f", id="escaped-FF-field"),
        pytest.param("\\034", "\x1c", id="escaped-FS-field"),
        pytest.param("\\035", "\x1d", id="escaped-GS-field"),
        pytest.param("\\036", "\x1e", id="escaped-RS-field"),
        pytest.param("\\n", "\n", id="escaped-LF-field"),
        pytest.param("\\r\\n", "\r\n", id="escaped-CRLF-field"),
    ],
)
def test_non_lf_separators_in_supported_fields_remain_data(encoded, decoded):
    path = "/tmp/private" + encoded + "value"
    parsed = parse_trace(
        trace(
            f'1 10.000001 open("{path}", O_DIRECTORY) = 3<{path}> <0.000001>',
            f"1 10.000003 fsync(3<{path}>) = 0 <0.000001>",
            f"1 10.000005 close(3<{path}>) = 0 <0.000001>",
            "1 10.000007 +++ exited with 0 +++",
        )
    )
    assert parsed["complete"] and parsed["issues"] == [] and len(parsed["events"]) == 4
    opened, synced, closed = parsed["events"][:3]
    expected = "/tmp/private" + decoded + "value"
    assert opened["path"] == opened["returned_fd"]["path"] == expected
    assert synced["fd"]["path"] == closed["fd"]["path"] == expected
    assert synced["prior_directory_open_event"] == 0 and synced["sync_target_kind"] == "unknown"
