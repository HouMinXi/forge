# SPDX-License-Identifier: Apache-2.0
"""Tests for the resumable JSONL ledger (Phase 58-2).

The plan's bar: kill a run mid-flight, restart, and the union of both
runs equals a single uninterrupted run -- no duplicates, no gaps, no
entry silently absent.

These tests use real processes and a real SIGKILL rather than simulating
them. A test that writes a torn line by hand proves the parser handles a
string; it does not prove the writer produces recoverable files under an
actual kill, which is the property being claimed.
"""

from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from code_forge.eval.ledger_jsonl import (
    DEFAULT_RETRY_CAP,
    ResumeKey,
    append_record,
    iter_arm_records,
    load_state,
    make_record,
    pending_keys,
    read_records,
)


def _key(entry_id: str, depth: int = 1, engine: str = "real", backend: str = "b") -> ResumeKey:
    return ResumeKey(entry_id=entry_id, depth=depth, engine=engine, backend=backend)


class TestAppendAndRead:
    def test_roundtrip(self, tmp_path):
        p = tmp_path / "l.jsonl"
        append_record(p, make_record(_key("e1"), "PASS", runs=1, caught=1))
        append_record(p, make_record(_key("e2"), "HOLD", runs=1))
        recs, truncated = read_records(p)
        assert [r["entry_id"] for r in recs] == ["e1", "e2"]
        assert truncated is False

    def test_missing_file_is_empty_not_an_error(self, tmp_path):
        recs, truncated = read_records(tmp_path / "absent.jsonl")
        assert recs == []
        assert truncated is False

    def test_torn_trailing_line_is_dropped(self, tmp_path):
        p = tmp_path / "l.jsonl"
        append_record(p, make_record(_key("e1"), "PASS"))
        # A worker killed mid-write: payload without its newline.
        with open(p, "a") as fh:
            fh.write('{"entry_id": "e2", "depth": 1, "engine": "re')
        recs, truncated = read_records(p)
        assert truncated is True
        assert [r["entry_id"] for r in recs] == ["e1"]

    def test_complete_final_line_without_newline_is_kept(self, tmp_path):
        """A valid last record must survive a missing trailing newline.

        Dropping it makes resume re-run an entry that already finished,
        and nothing reports that it happened. The torn-write case is
        distinguished by whether the line parses, not by the newline
        alone.
        """
        p = tmp_path / "l.jsonl"
        append_record(p, make_record(_key("e1"), "PASS"))
        raw = p.read_bytes()
        assert raw.endswith(b"\n")
        p.write_bytes(raw[:-1])  # strip the newline, keep valid JSON

        recs, truncated = read_records(p)
        assert [r["entry_id"] for r in recs] == ["e1"]
        assert truncated is False, "a parseable final line is not torn"

    def test_corruption_before_the_last_line_raises(self, tmp_path):
        """A mid-file parse failure is not a torn write.

        Silently skipping it would make resume believe the entry never
        ran and queue it again, producing the duplicate this ledger
        exists to prevent.
        """
        p = tmp_path / "l.jsonl"
        append_record(p, make_record(_key("e1"), "PASS"))
        with open(p, "a") as fh:
            fh.write("not json at all\n")
        append_record(p, make_record(_key("e3"), "PASS"))
        with pytest.raises(ValueError, match="corrupt at line 2"):
            read_records(p)

    def test_invalid_utf8_raises_instead_of_replacing(self, tmp_path):
        """errors=replace can mint a parseable record from garbage.

        A 0xff byte inside a JSON string becomes U+FFFD and the line
        still loads; resume then treats a corrupted entry as done.
        """
        p = tmp_path / "l.jsonl"
        append_record(p, make_record(_key("e1"), "PASS"))
        p.write_bytes(p.read_bytes() + b'{"entry_id":"\xff"}\n')
        with pytest.raises(ValueError, match="not valid UTF-8"):
            read_records(p)

    def test_missing_file_after_unlink_is_empty(self, tmp_path):
        """read_bytes FileNotFoundError is the exists()-then-read race."""
        recs, truncated = read_records(tmp_path / "gone.jsonl")
        assert recs == []
        assert truncated is False

    def test_iter_arm_records_filters_backend(self, tmp_path):
        """Resume key includes backend; the report must too.

        Filtering only on depth+engine mixes backends that share a
        ledger -- the same failure a shared key would cause on resume.
        """
        p = tmp_path / "l.jsonl"
        append_record(p, make_record(_key("e1", backend="a"), "PASS"))
        append_record(p, make_record(_key("e1", backend="b"), "HOLD"))
        append_record(p, make_record(_key("e2", depth=2, backend="a"), "PASS"))
        got = list(iter_arm_records(p, depth=1, engine="real", backend="a"))
        assert [r["entry_id"] for r in got] == ["e1"]
        assert all(r["backend"] == "a" for r in got)


class TestResumeSemantics:
    def test_completed_entries_are_not_pending(self, tmp_path):
        p = tmp_path / "l.jsonl"
        keys = [_key("e1"), _key("e2"), _key("e3")]
        append_record(p, make_record(keys[0], "PASS"))
        append_record(p, make_record(keys[1], "HOLD"))
        assert pending_keys(keys, p) == [keys[2]]

    def test_arms_do_not_share_a_key(self, tmp_path):
        """depth-1 completing an entry must not satisfy depth-2.

        This is the failure the composite key exists to prevent: with a
        bare entry id, the second arm would skip everything the first
        recorded and report a corpus it never ran.
        """
        p = tmp_path / "l.jsonl"
        d1 = _key("e1", depth=1)
        d2 = _key("e1", depth=2)
        append_record(p, make_record(d1, "PASS"))
        assert pending_keys([d1, d2], p) == [d2]

    def test_skipped_is_retried(self, tmp_path):
        p = tmp_path / "l.jsonl"
        k = _key("e1")
        append_record(p, make_record(k, "SKIPPED", skipped_reason="backend down"))
        assert pending_keys([k], p) == [k], "a SKIPPED entry must be retried"

    def test_skipped_stops_being_retried_at_the_cap(self, tmp_path):
        p = tmp_path / "l.jsonl"
        k = _key("e1")
        for _ in range(DEFAULT_RETRY_CAP + 1):
            append_record(p, make_record(k, "SKIPPED", skipped_reason="down"))
        assert pending_keys([k], p) == []
        done, _ = load_state(p)
        assert done[k]["verdict"] == "SKIPPED"
        assert done[k]["skipped_reason"] == "down"

    def test_a_later_success_supersedes_an_earlier_skip(self, tmp_path):
        p = tmp_path / "l.jsonl"
        k = _key("e1")
        append_record(p, make_record(k, "SKIPPED", skipped_reason="flake"))
        append_record(p, make_record(k, "PASS", runs=1))
        assert pending_keys([k], p) == []
        done, _ = load_state(p)
        assert done[k]["verdict"] == "PASS"


class TestConcurrentWriters:
    def test_parallel_appends_do_not_interleave(self, tmp_path):
        """Every line must parse after concurrent writers finish.

        Uses real processes: threads in one interpreter would serialise
        on the GIL around the write and could pass while the locking is
        wrong.

        Scope, stated honestly: this test does NOT prove the flock is
        load-bearing. Measured on this kernel, O_APPEND with a single
        os.write is atomic against 8 concurrent writers at payloads from
        200 B to 1 MB -- removing the lock leaves this test green. The
        lock is defence for the cases the measurement does not cover: a
        filesystem where O_APPEND is not atomic (NFS being the one that
        matters), and any future change that splits the payload into more
        than one write. What this test does catch is a writer that stops
        being single-write or stops being O_APPEND.
        """
        p = tmp_path / "l.jsonl"
        src = Path(__file__).resolve().parents[1] / "src"
        prog = textwrap.dedent(
            """
            import sys
            sys.path.insert(0, %r)
            from pathlib import Path
            from code_forge.eval.ledger_jsonl import (
                ResumeKey, append_record, make_record)
            wid = int(sys.argv[2])
            for i in range(40):
                k = ResumeKey("w%%d-e%%d" %% (wid, i), 1, "real", "b")
                append_record(Path(sys.argv[1]),
                              make_record(k, "PASS", runs=1,
                                          skipped_reason=str(wid) * 20000))
            """
            % str(src)
        )
        procs = [subprocess.Popen([sys.executable, "-c", prog, str(p), str(w)]) for w in range(6)]
        for proc in procs:
            assert proc.wait(timeout=120) == 0

        recs, truncated = read_records(p)
        assert truncated is False
        assert len(recs) == 6 * 40, "lost or merged lines: got %d" % len(recs)
        assert len({r["entry_id"] for r in recs}) == 6 * 40

    def test_record_reaches_the_file_in_exactly_one_write(
        self,
        tmp_path,
        monkeypatch,
    ):
        """The atomicity argument rests on one write per record.

        O_APPEND is atomic per write call, not per logical line, so a
        writer that emits the payload and its newline separately can
        interleave with a concurrent writer no matter what the lock does
        on a filesystem that ignores it. This is the property the
        concurrency test above cannot see.
        """
        p = tmp_path / "l.jsonl"
        real_write = os.write
        writes: list[int] = []

        def counting_write(fd, data):
            writes.append(len(data))
            return real_write(fd, data)

        # monkeypatch rather than assigning os.write directly: a direct
        # assignment leaks to every other test if this one dies between
        # the swap and the finally, and the module-scope name is shared.
        monkeypatch.setattr(os, "write", counting_write)
        append_record(p, make_record(_key("e1"), "PASS", runs=1))

        assert len(writes) == 1, "record must reach the file in one write, saw %d: %r" % (
            len(writes),
            writes,
        )
        assert p.read_bytes().endswith(b"\n")


class TestKilledRunResumes:
    def test_union_of_killed_and_resumed_equals_one_clean_run(self, tmp_path):
        """The plan's Done-when, with a real SIGKILL.

        Run 12 entries, kill the process partway, restart with resume,
        and compare against an uninterrupted run of the same 12.
        """
        src = Path(__file__).resolve().parents[1] / "src"
        prog = textwrap.dedent(
            """
            import sys, time
            sys.path.insert(0, %r)
            from pathlib import Path
            from code_forge.eval.ledger_jsonl import (
                ResumeKey, append_record, make_record, pending_keys)

            path = Path(sys.argv[1])
            keys = [ResumeKey("e%%02d" %% i, 1, "real", "b") for i in range(12)]
            for k in pending_keys(keys, path):
                time.sleep(0.12)
                append_record(path, make_record(k, "PASS", runs=1))
            """
            % str(src)
        )

        # Uninterrupted reference run.
        ref = tmp_path / "ref.jsonl"
        assert subprocess.run([sys.executable, "-c", prog, str(ref)], timeout=120).returncode == 0
        ref_recs, _ = read_records(ref)
        assert len(ref_recs) == 12

        # Interrupted run: kill it partway through.
        live = tmp_path / "live.jsonl"
        proc = subprocess.Popen([sys.executable, "-c", prog, str(live)])
        time.sleep(0.8)
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=30)

        partial, _ = read_records(live)
        assert 0 < len(partial) < 12, "kill landed outside the run: %d records" % len(partial)

        # Resume.
        assert subprocess.run([sys.executable, "-c", prog, str(live)], timeout=120).returncode == 0

        resumed, truncated = read_records(live)
        ids = [r["entry_id"] for r in resumed]
        assert len(ids) == len(set(ids)), "duplicates after resume: %s" % ids
        assert set(ids) == {r["entry_id"] for r in ref_recs}, "resumed set differs from a clean run"


class TestAppendRepairsTail:
    @pytest.mark.parametrize("tail_kind", ["valid", "torn", "torn_only", "whitespace"])
    def test_load_append_reload(self, tmp_path, tail_kind):
        p = tmp_path / "l.jsonl"
        first = make_record(_key("first"), "PASS")
        last = make_record(_key("last"), "HOLD")
        # Preserve formatting, CRLF, blank lines and multibyte UTF-8 exactly.
        prefix = (json.dumps(first, indent=None) + "\r\n\n").encode()
        prefix += b" \t\n"
        complete = json.dumps(make_record(_key("caf\u00e9"), "PASS"), ensure_ascii=False).encode()
        if tail_kind == "valid":
            original = prefix + complete + b" \r\t"
            expected_ids = {"first", "caf\u00e9"}
            preserved = original + b"\n"
        else:
            prefix = b"" if tail_kind == "torn_only" else prefix
            tail = b" \t\r" if tail_kind == "whitespace" else b'{"entry_id":"unfinished'
            original = prefix + tail
            expected_ids = set() if tail_kind == "torn_only" else {"first"}
            preserved = prefix
        p.write_bytes(original)
        before, truncated = load_state(p)
        assert {k.entry_id for k in before} == expected_ids
        assert truncated is (tail_kind != "valid")
        assert p.read_bytes() == original, "loading must remain read-only"
        append_record(p, last)
        expected_line = (json.dumps(last, sort_keys=True, separators=(",", ":")) + "\n").encode()
        assert p.read_bytes() == preserved + expected_line
        after, truncated = load_state(p)
        assert {k.entry_id for k in after} == expected_ids | {"last"}
        assert truncated is False

    @pytest.mark.parametrize("original", [b"", b"\n", b"{}\n", b"{}\r\n"])
    def test_empty_and_terminated_fast_path(self, tmp_path, monkeypatch, original):
        p = tmp_path / "l.jsonl"
        p.write_bytes(original)

        def unexpected_parse(*args, **kwargs):
            pytest.fail("terminated ledger should not be parsed on append")

        monkeypatch.setattr(json, "loads", unexpected_parse)
        append_record(p, {"new": 1})
        assert p.read_bytes() == original + b'{"new":1}\n'

    @pytest.mark.parametrize("tail", [b"null", b"42", b"[]", b'"text"', b"{} \r\t"])
    def test_valid_json_values_are_preserved(self, tmp_path, tail):
        p = tmp_path / "l.jsonl"
        p.write_bytes(tail)
        append_record(p, {"new": 1})
        assert p.read_bytes() == tail + b'\n{"new":1}\n'
        records, truncated = read_records(p)
        assert records == [json.loads(tail), {"new": 1}]
        assert truncated is False

    @pytest.mark.parametrize("tail", [b'{"x":"\xff"}', b'{"x":"\xe2\x82'])
    def test_invalid_utf8_is_untouched(self, tmp_path, tail):
        p = tmp_path / "l.jsonl"
        original = b"{}\n" + tail
        p.write_bytes(original)
        with pytest.raises(UnicodeDecodeError):
            append_record(p, {"new": 1})
        assert p.read_bytes() == original

    @pytest.mark.parametrize("tail", [b"", b'{"torn":', b"{}"])
    def test_complete_line_corruption_is_not_repaired(self, tmp_path, tail):
        p = tmp_path / "l.jsonl"
        prefix = b"not json\n"
        p.write_bytes(prefix + tail)
        append_record(p, {"new": 1})
        assert p.read_bytes().startswith(prefix)
        with pytest.raises(ValueError, match="corrupt at line 1"):
            read_records(p)

    @pytest.mark.parametrize("size", [65534, 65535, 65536, 65537, 131073])
    @pytest.mark.parametrize("valid", [True, False])
    def test_suffix_crosses_scan_boundaries(self, tmp_path, size, valid):
        p = tmp_path / "l.jsonl"
        prefix = b'{"prefix":1}\n'
        tail = b'{"padding":"' + b"x" * size + (b'"}' if valid else b"")
        p.write_bytes(prefix + tail)
        append_record(p, {"new": 1})
        assert p.read_bytes() == prefix + (tail + b"\n" if valid else b"") + b'{"new":1}\n'

    def test_serialization_failure_precedes_repair(self, tmp_path):
        p = tmp_path / "l.jsonl"
        original = b'{}\n{"torn":'
        p.write_bytes(original)
        with pytest.raises(TypeError):
            append_record(p, {"bad": object()})
        assert p.read_bytes() == original

    @pytest.mark.parametrize("failure", ["decode", "read", "short_read"])
    def test_unexpected_failures_precede_mutation(self, tmp_path, monkeypatch, failure):
        p = tmp_path / "l.jsonl"
        original = b'{}\n{"torn":'
        p.write_bytes(original)

        def fail(*args, **kwargs):
            if failure == "short_read":
                return b""
            raise RuntimeError("injected failure")

        monkeypatch.setattr(
            json if failure == "decode" else os, "loads" if failure == "decode" else "pread", fail
        )
        with pytest.raises((RuntimeError, OSError)):
            append_record(p, {"new": 1})
        assert p.read_bytes() == original

    def test_short_write_does_not_report_success(self, tmp_path, monkeypatch):
        p = tmp_path / "l.jsonl"
        p.write_bytes(b"{}")
        real_write = os.write

        def short_write(fd, data):
            return real_write(fd, data[:2])

        monkeypatch.setattr(os, "write", short_write)
        with pytest.raises(OSError, match="short write"):
            append_record(p, {"new": 1})
        assert p.read_bytes() == b"{}\n{"
        monkeypatch.setattr(os, "write", real_write)
        append_record(p, {"retry": 1})
        assert read_records(p) == ([{}, {"retry": 1}], False)

    def test_tail_repair_write_and_fsync_share_one_locked_descriptor(self, tmp_path, monkeypatch):
        p = tmp_path / "l.jsonl"
        p.write_bytes(b'{}\n{"torn":')
        locked = set()
        events = []
        real_flock = fcntl.flock

        def flock(fd, operation):
            if operation == fcntl.LOCK_EX:
                real_flock(fd, operation)
                locked.add(fd)
            else:
                assert fd in locked
                locked.remove(fd)
                real_flock(fd, operation)

        monkeypatch.setattr(fcntl, "flock", flock)
        for name in ("pread", "ftruncate", "write", "fsync"):
            real = getattr(os, name)

            def checked(fd, *args, _name=name, _real=real):
                assert fd in locked, _name + " ran outside LOCK_EX"
                events.append((_name, fd))
                return _real(fd, *args)

            monkeypatch.setattr(os, name, checked)
        append_record(p, {"new": 1})
        assert not locked
        assert len({fd for _, fd in events}) == 1
        assert [name for name, _ in events][-3:] == ["ftruncate", "write", "fsync"]

    def test_valid_tail_separator_and_record_use_one_write(self, tmp_path, monkeypatch):
        p = tmp_path / "l.jsonl"
        p.write_bytes(b"{}")
        writes = []
        real_write = os.write

        def write(fd, data):
            writes.append(data)
            return real_write(fd, data)

        monkeypatch.setattr(os, "write", write)
        append_record(p, {"new": 1})
        assert writes == [b'\n{"new":1}\n']

    @pytest.mark.parametrize("valid", [True, False])
    def test_parallel_appends_repair_tail_once(self, tmp_path, valid):
        p = tmp_path / "l.jsonl"
        initial = make_record(_key("initial"), "PASS")
        p.write_bytes(json.dumps(initial).encode() if valid else b'{"entry_id":')
        src = Path(__file__).resolve().parents[1] / "src"
        prog = textwrap.dedent(
            """
            import sys
            sys.path.insert(0, %r)
            from pathlib import Path
            from code_forge.eval.ledger_jsonl import ResumeKey, append_record, make_record
            append_record(Path(sys.argv[1]),
                          make_record(ResumeKey(sys.argv[2], 1, "real", "b"), "PASS"))
            """
            % str(src)
        )
        procs = []
        try:
            procs.extend(
                subprocess.Popen([sys.executable, "-c", prog, str(p), str(i)]) for i in range(8)
            )
            for proc in procs:
                assert proc.wait(timeout=30) == 0
        finally:
            for proc in procs:
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=30)
        records, truncated = read_records(p)
        ids = [r["entry_id"] for r in records]
        assert set(ids) == {str(i) for i in range(8)} | ({"initial"} if valid else set())
        assert len(ids) == len(set(ids))
        assert truncated is False
