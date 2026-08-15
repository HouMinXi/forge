import datetime
import hashlib
import json
import types
from pathlib import Path

from code_forge import receipt as receipt_module
from code_forge.disposition import Disposition
from code_forge.receipt import write_receipts
from code_forge.state import StateFinding
from code_forge.verify import run_verify


def _finding(pass_name, fp, file="src/foo.py", line=42, desc="test"):
    return StateFinding(
        id="l1-" + pass_name + "-" + fp,
        fingerprint=fp,
        source="L1",
        disposition=Disposition.CONFIRMED,
        file=file,
        line_range=[line, line],
        description="[" + pass_name + "] " + desc,
    )


class TestWriteReceipts:
    def test_writes_3_receipt_files_per_round(self, tmp_path):
        findings = [
            _finding("qodo", "fp1"),
            _finding("expert", "fp2"),
            _finding("adversarial", "fp3"),
        ]
        diff_sha = hashlib.sha256(b"fake diff").hexdigest()
        write_receipts(
            receipts_dir=tmp_path / ".code-forge" / "receipts",
            round_index=0,
            l1_findings=findings,
            diff_sha256=diff_sha,
            source_files=[Path("src/foo.py")],
            cwd=tmp_path,
        )
        files = sorted((tmp_path / ".code-forge" / "receipts").glob("*.json"))
        assert len(files) == 3
        names = [f.name for f in files]
        assert "receipt-c1p1.json" in names
        assert "receipt-c1p2.json" in names
        assert "receipt-c1p3.json" in names

    def test_receipt_contains_required_fields(self, tmp_path):
        findings = [_finding("qodo", "fp1")]
        diff_sha = hashlib.sha256(b"diff").hexdigest()
        (tmp_path / "src").mkdir(parents=True)
        (tmp_path / "src" / "foo.py").write_text(
            "line1\nline2\ndef bar():\n    pass\n"
        )
        write_receipts(
            receipts_dir=tmp_path / ".code-forge" / "receipts",
            round_index=0,
            l1_findings=findings,
            diff_sha256=diff_sha,
            source_files=[Path("src/foo.py")],
            cwd=tmp_path,
        )
        r = json.loads(
            (tmp_path / ".code-forge" / "receipts" / "receipt-c1p1.json").read_text()
        )
        assert r["cycle"] == 1
        assert r["pass"] == 1
        assert r["skill"] == "qodo-review"
        assert r["diff_sha256"] == diff_sha
        assert "timestamp" in r
        assert "findings" in r
        assert "anchors" in r
        assert "code_excerpts" in r
        assert "covered_line_ranges" in r

    def test_empty_l1_still_writes_3_receipts(self, tmp_path):
        diff_sha = hashlib.sha256(b"diff").hexdigest()
        write_receipts(
            receipts_dir=tmp_path / ".code-forge" / "receipts",
            round_index=2,
            l1_findings=[],
            diff_sha256=diff_sha,
            source_files=[Path("src/foo.py")],
            cwd=tmp_path,
        )
        files = list((tmp_path / ".code-forge" / "receipts").glob("*.json"))
        assert len(files) == 3
        r = json.loads(sorted(files)[0].read_text())
        assert r["findings_count"] == 0

    def test_timestamps_stay_ordered_across_back_to_back_rounds(
        self, tmp_path, monkeypatch
    ):
        """Rounds that finish faster than a pass offset must not invert.

        run_verify reads receipt-*.json in sorted filename order and fails
        the run unless the timestamps are non-decreasing in that order. A
        fast backend finishes a round in well under a second, so anything
        added within a round has to stay ordered against the round that
        follows it.

        The clock is driven rather than read: rounds 50ms apart are the
        condition that inverts a per-pass offset, and a test that waited on
        the real clock would go green on a loaded machine whose rounds
        happen to land seconds apart.
        """
        base = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
        round_starts = iter(
            [base + datetime.timedelta(milliseconds=50 * i) for i in range(3)]
        )

        class _Clock:
            @staticmethod
            def now(tz=None):
                return next(round_starts)

        monkeypatch.setattr(
            receipt_module,
            "datetime",
            types.SimpleNamespace(
                datetime=_Clock,
                timezone=datetime.timezone,
                timedelta=datetime.timedelta,
            ),
        )

        rd = tmp_path / ".code-forge" / "receipts"
        diff_sha = hashlib.sha256(b"diff").hexdigest()
        for round_index in range(3):
            write_receipts(
                receipts_dir=rd,
                round_index=round_index,
                l1_findings=[],
                diff_sha256=diff_sha,
                source_files=[Path("src/foo.py")],
                cwd=tmp_path,
            )

        # Verify file side effects: all 9 receipt files landed on disk.
        files_on_disk = sorted(rd.glob("receipt-*.json"))
        assert len(files_on_disk) == 9, (
            "expected 9 receipt files, got %d" % len(files_on_disk)
        )
        for f in files_on_disk:
            obj = json.loads(f.read_text())
            assert "timestamp" in obj, "missing timestamp in %s" % f.name
            assert "cycle" in obj, "missing cycle in %s" % f.name
            assert "pass" in obj, "missing pass in %s" % f.name

        names = [f.name for f in files_on_disk]
        assert names == [
            "receipt-c1p1.json", "receipt-c1p2.json", "receipt-c1p3.json",
            "receipt-c2p1.json", "receipt-c2p2.json", "receipt-c2p3.json",
            "receipt-c3p1.json", "receipt-c3p2.json", "receipt-c3p3.json",
        ]
        stamps = [
            json.loads(f.read_text())["timestamp"]
            for f in sorted(rd.glob("receipt-*.json"))
        ]
        assert stamps == sorted(stamps), (
            "timestamps invert between rounds: %s" % stamps
        )
        for start in range(0, 9, 3):
            round_stamps = stamps[start:start + 3]
            assert len(set(round_stamps)) == 1, (
                "passes in one round should share the round's write time, "
                "got %s" % round_stamps
            )

    def test_run_verify_accepts_a_full_set_this_writer_produced(
        self, tmp_path, monkeypatch
    ):
        """The consumer, not just the files, has to accept what we write.

        The test above reads the timestamps back off disk itself. That
        cannot catch a disagreement between the writer and run_verify,
        which is the code that actually rejects a review: its check 4
        compares timestamps in (cycle, pass) order and fails the whole run
        with "timestamps not monotonic". Asserting through run_verify keeps
        the two sides pinned together.

        Same driven clock as above, for the same reason: 50ms rounds are
        the condition that inverts a per-pass offset.
        """
        base = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
        round_starts = iter(
            [base + datetime.timedelta(milliseconds=50 * i) for i in range(3)]
        )

        class _Clock:
            @staticmethod
            def now(tz=None):
                return next(round_starts)

        monkeypatch.setattr(
            receipt_module,
            "datetime",
            types.SimpleNamespace(
                datetime=_Clock,
                timezone=datetime.timezone,
                timedelta=datetime.timedelta,
            ),
        )

        diff_sha = hashlib.sha256(b"diff").hexdigest()
        diff_files = {"src/foo.py": [1, 2, 3]}
        for round_index in range(3):
            write_receipts(
                receipts_dir=tmp_path / ".code-forge" / "receipts",
                round_index=round_index,
                l1_findings=[],
                diff_sha256=diff_sha,
                source_files=[Path("src/foo.py")],
                cwd=tmp_path,
                diff_files=diff_files,
            )

        result = run_verify(tmp_path, diff_sha, diff_files)

        # checks_passed counts the checks that passed, in order. Checks 1-3
        # (completeness, diff hash, anchors) come first, so anything below 3
        # means run_verify gave up before it ever compared a timestamp and
        # this test would otherwise pass while asserting nothing.
        assert result.checks_passed >= 3, (
            "run_verify stopped at check %d (%s) before reaching the "
            "timestamp gate, so this test asserts nothing about ordering"
            % (result.checks_run, result.reason)
        )
        assert result.checks_passed >= 4, (
            "the timestamp gate rejected a receipt set this very writer "
            "produced: %s" % result.reason
        )


class TestBuildExcerpts:
    """_build_excerpts: content normalization for reviewer-supplied
    excerpts, and the fail-closed handling of shapes that must NOT be
    laundered into a plausible-looking string."""

    def test_list_of_lines_joined_into_string(self):
        from code_forge.receipt import _build_excerpts

        out = _build_excerpts([{
            "file": "src/foo.py", "start_line": 1, "end_line": 2,
            "content": ["line one", "line two"],
        }])
        assert out[0]["content"] == "line one\nline two"

    def test_list_with_non_string_lines_left_unconverted(self):
        """A list containing a non-string element stays a list so the
        downstream schema check rejects it: joining with str(ln) would
        launder None into the string "None", the same fail-open trap
        the scalar case avoids."""
        from code_forge.receipt import _build_excerpts

        out = _build_excerpts([{
            "file": "src/foo.py", "start_line": 1, "end_line": 1,
            "content": [1, None, "x"],
        }])
        assert out[0]["content"] == [1, None, "x"]

    def test_string_content_left_unchanged(self):
        from code_forge.receipt import _build_excerpts

        out = _build_excerpts([{
            "file": "src/foo.py", "start_line": 1, "end_line": 1,
            "content": "def foo():\n    pass",
        }])
        assert out[0]["content"] == "def foo():\n    pass"

    def test_null_content_not_stringified_into_the_word_none(self):
        """A None content must stay None so the downstream receipt
        schema check rejects it -- str(None) == "None" is a valid
        string and would pass the isinstance(content, str) gate as a
        fabricated excerpt nobody wrote."""
        from code_forge.receipt import _build_excerpts

        out = _build_excerpts([{
            "file": "src/foo.py", "start_line": 1, "end_line": 1,
            "content": None,
        }])
        assert out[0]["content"] is None
        assert out[0]["content"] != "None"

    def test_null_content_receipt_fails_schema_validation(self):
        """End-to-end: a null-content excerpt must make the receipt
        schema check reject the receipt, not silently validate it."""
        from code_forge.receipt import write_receipts
        from code_forge.verify import CorruptedReceiptError, _load_receipts

        diff_sha = hashlib.sha256(b"diff").hexdigest()
        receipts_dir = Path("dummy")

        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            receipts_dir = tdp / ".code-forge" / "receipts"
            write_receipts(
                receipts_dir=receipts_dir,
                round_index=0,
                l1_findings=[],
                diff_sha256=diff_sha,
                source_files=[Path("src/foo.py")],
                cwd=tdp,
                diff_files={"src/foo.py": [1]},
                reviewer_excerpts=[{
                    "file": "src/foo.py", "start_line": 1, "end_line": 1,
                    "content": None,
                }],
            )
            try:
                _load_receipts(receipts_dir)
                raised = False
            except CorruptedReceiptError:
                raised = True
            assert raised, (
                "a null-content excerpt must be rejected by receipt "
                "schema validation, not silently accepted"
            )
