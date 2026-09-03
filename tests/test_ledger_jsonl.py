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
    load_state,
    make_record,
    pending_keys,
    read_records,
)


def _key(entry_id: str, depth: int = 1, engine: str = "real",
         backend: str = "b") -> ResumeKey:
    return ResumeKey(entry_id=entry_id, depth=depth, engine=engine,
                     backend=backend)


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
        append_record(p, make_record(k, "SKIPPED",
                                     skipped_reason="backend down"))
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
        procs = [
            subprocess.Popen([sys.executable, "-c", prog, str(p), str(w)])
            for w in range(6)
        ]
        for proc in procs:
            assert proc.wait(timeout=120) == 0

        recs, truncated = read_records(p)
        assert truncated is False
        assert len(recs) == 6 * 40, "lost or merged lines: got %d" % len(recs)
        assert len({r["entry_id"] for r in recs}) == 6 * 40

    def test_record_reaches_the_file_in_exactly_one_write(self, tmp_path):
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

        os.write = counting_write
        try:
            append_record(p, make_record(_key("e1"), "PASS", runs=1))
        finally:
            os.write = real_write

        assert len(writes) == 1, (
            "record must reach the file in one write, saw %d: %r"
            % (len(writes), writes)
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
        assert subprocess.run(
            [sys.executable, "-c", prog, str(ref)], timeout=120
        ).returncode == 0
        ref_recs, _ = read_records(ref)
        assert len(ref_recs) == 12

        # Interrupted run: kill it partway through.
        live = tmp_path / "live.jsonl"
        proc = subprocess.Popen([sys.executable, "-c", prog, str(live)])
        time.sleep(0.8)
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=30)

        partial, _ = read_records(live)
        assert 0 < len(partial) < 12, (
            "kill landed outside the run: %d records" % len(partial)
        )

        # Resume.
        assert subprocess.run(
            [sys.executable, "-c", prog, str(live)], timeout=120
        ).returncode == 0

        resumed, truncated = read_records(live)
        ids = [r["entry_id"] for r in resumed]
        assert len(ids) == len(set(ids)), "duplicates after resume: %s" % ids
        assert set(ids) == {r["entry_id"] for r in ref_recs}, (
            "resumed set differs from a clean run"
        )
