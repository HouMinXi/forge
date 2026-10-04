import datetime
import hashlib
import json
import logging
import types
from pathlib import Path

import pytest

from code_forge import receipt as receipt_module
from code_forge.disposition import Disposition
from code_forge.manifest import ManifestTier
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
    @pytest.mark.parametrize("rejected_pass", ["qodo", "expert", "adversarial"])
    def test_source_rejections_retain_exact_pass_diagnostics(self, tmp_path, monkeypatch, rejected_pass):
        from code_forge import verify

        content = "const first = 1;\nconst second = 2;\n"
        (tmp_path / "control.ts").write_text(content)
        diff = (
            "diff --git a/control.ts b/control.ts\n--- a/control.ts\n+++ b/control.ts\n"
            "@@ -0,0 +1,2 @@\n+const first = 1;\n+const second = 2;\n"
        )
        sha = hashlib.sha256(diff.encode()).hexdigest()
        names = ("qodo", "expert", "adversarial")
        excerpts = [
            {"pass_name": name, "file": "control.ts", "start_line": line, "end_line": line,
             "content": text + (" wrong" if name == rejected_pass else "")}
            for name in names for line, text in enumerate(content.splitlines(), 1)
        ]
        calls = []
        validator = verify.validate_excerpts_against_diff

        def capture(diff_text, offered, *, cwd=None):
            errors = validator(diff_text, offered, cwd=cwd)
            calls.append((diff_text, offered, cwd, list(errors)))
            return errors

        monkeypatch.setattr(verify, "validate_excerpts_against_diff", capture)
        paths = write_receipts(
            tmp_path / "receipts", 4,
            [_finding(name, name, file="control.ts", line=1) for name in names],
            sha, [Path("control.ts")], tmp_path, diff_text=diff, reviewer_excerpts=excerpts,
            manifest_tier=ManifestTier.DECLARED,
        )
        assert len(calls) == 3
        for name, path, call in zip(names, paths, calls, strict=True):
            obj = json.loads(path.read_text())
            assert call[:3] == (diff, obj["code_excerpts"], tmp_path)
            assert obj["pass"] == names.index(name) + 1
            assert obj["cycle"] == 5
            assert obj["diff_sha256"] == sha
            assert obj["code_excerpts"] == [
                {k: v for k, v in exc.items() if k != "pass_name"} | {"rationale": "reviewer-provided"}
                for exc in excerpts if exc["pass_name"] == name
            ]
            assert obj["findings_count"] == 1
            assert [f["description"] for f in obj["findings"]] == [f"[{name}] test"]
            if name == rejected_pass:
                assert len(call[3]) == 2
                assert obj["excerpt_validation_errors"] == call[3]
                assert obj["pass_status"] == "schema_fail"
            else:
                assert call[3] == []
                assert "excerpt_validation_errors" not in obj
                assert obj["pass_status"] == "completed"

    def test_excerpt_diagnostics_preserve_order_duplicates_and_string_data(self, tmp_path, monkeypatch):
        from code_forge import verify

        errors = ['quote " and slash \\ and $(touch never)', "duplicate", "duplicate", "last"]
        returned = iter([errors, [], []])
        monkeypatch.setattr(verify, "validate_excerpts_against_diff", lambda *a, **k: next(returned))
        paths = write_receipts(tmp_path / "receipts", 0, [], "hash", [], tmp_path,
                               diff_text="diff", manifest_tier=ManifestTier.DECLARED)
        assert json.loads(paths[0].read_text())["excerpt_validation_errors"] == errors
        assert all("excerpt_validation_errors" not in json.loads(p.read_text()) for p in paths[1:])
        assert not (tmp_path / "never").exists()

    @pytest.mark.parametrize("diff_text", [None, "", "diff"])
    def test_empty_evidence_omits_excerpt_diagnostics(self, tmp_path, diff_text):
        paths = write_receipts(tmp_path / "receipts", 0, [], "hash", [], tmp_path,
                               diff_text=diff_text, manifest_tier=ManifestTier.DECLARED)
        for path in paths:
            obj = json.loads(path.read_text())
            assert obj["pass_status"] == "completed"
            assert "excerpt_validation_errors" not in obj

    @pytest.mark.parametrize("diff_text", [None, ""])
    def test_missing_diff_keeps_offered_excerpts_without_diagnostics(self, tmp_path, monkeypatch, diff_text):
        from code_forge import verify
        from unittest.mock import Mock

        validator = Mock(wraps=verify.validate_excerpts_against_diff)
        monkeypatch.setattr(verify, "validate_excerpts_against_diff", validator)
        excerpt = {"pass_name": "qodo", "file": "control.ts", "start_line": 1,
                   "end_line": 1, "content": "wrong literal"}
        paths = write_receipts(tmp_path / "receipts", 0, [], "hash", [], tmp_path,
                               diff_text=diff_text, reviewer_excerpts=[excerpt],
                               manifest_tier=ManifestTier.DECLARED)
        assert validator.call_count == 0
        obj = json.loads(paths[0].read_text())
        assert obj["code_excerpts"][0]["content"] == excerpt["content"]
        for path in paths:
            obj = json.loads(path.read_text())
            assert obj["pass_status"] == "completed"
            assert "excerpt_validation_errors" not in obj

    @pytest.mark.parametrize("kind,status", [
        ("spawn-fail", "timeout"), ("invoke-fail", "error"),
        ("incomplete-coverage", "incomplete"), ("schema-fail", "schema_fail"),
    ])
    def test_skipped_pass_has_no_invented_excerpt_diagnostics(self, tmp_path, monkeypatch, kind, status):
        from code_forge import verify

        calls = []
        validator = verify.validate_excerpts_against_diff

        def capture(diff_text, offered, *, cwd=None):
            calls.append(offered)
            return validator(diff_text, offered, cwd=cwd)

        monkeypatch.setattr(verify, "validate_excerpts_against_diff", capture)
        finding = _finding("qodo", kind, file="<infra>", line=0)
        finding.source = "INFRA"
        diff = ("diff --git a/control.ts b/control.ts\n--- a/control.ts\n+++ b/control.ts\n"
                "@@ -0,0 +1 @@\n+const value = 1;\n")
        excerpt = {"pass_name": "qodo", "file": "control.ts", "start_line": 1,
                   "end_line": 1, "content": "wrong literal"}
        paths = write_receipts(tmp_path / "receipts", 0, [finding], "hash", [], tmp_path,
                               diff_text=diff, reviewer_excerpts=[excerpt],
                               manifest_tier=ManifestTier.DECLARED)
        assert len(calls) == 2
        assert json.loads(paths[0].read_text())["pass_status"] == status
        assert all("excerpt_validation_errors" not in json.loads(p.read_text()) for p in paths)

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
        (tmp_path / "src" / "foo.py").write_text("line1\nline2\ndef bar():\n    pass\n")
        write_receipts(
            receipts_dir=tmp_path / ".code-forge" / "receipts",
            round_index=0,
            l1_findings=findings,
            diff_sha256=diff_sha,
            source_files=[Path("src/foo.py")],
            cwd=tmp_path,
        )
        r = json.loads((tmp_path / ".code-forge" / "receipts" / "receipt-c1p1.json").read_text())
        assert r["cycle"] == 1
        assert r["pass"] == 1
        assert r["skill"] == "qodo-review"
        assert r["diff_sha256"] == diff_sha
        assert "timestamp" in r
        assert "findings" in r
        assert "anchors" in r
        assert "code_excerpts" in r
        assert "covered_line_ranges" in r

    def test_manifest_loader_bug_is_not_declared(self, tmp_path, monkeypatch):
        import code_forge.manifest as manifest

        def boom(_cwd):
            raise RuntimeError("manifest loader bug")

        monkeypatch.setattr(manifest, "extract_manifest", boom)
        try:
            write_receipts(
                receipts_dir=tmp_path / ".code-forge" / "receipts",
                round_index=0,
                l1_findings=[_finding("qodo", "fp1")],
                diff_sha256=hashlib.sha256(b"diff").hexdigest(),
                source_files=[Path("src/foo.py")],
                cwd=tmp_path,
            )
        except RuntimeError as exc:
            assert "manifest loader bug" in str(exc)
            return
        raise AssertionError("loader bug was swallowed")

    def test_missing_manifest_module_is_declared(self, tmp_path, monkeypatch):
        import builtins
        import sys

        real_import = builtins.__import__
        calls = []

        def hide_manifest(name, *args, **kwargs):
            calls.append(name)
            if name in {"manifest", "code_forge.manifest"}:
                raise ImportError("manifest missing")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", hide_manifest)
        monkeypatch.delitem(sys.modules, "code_forge.manifest", raising=False)
        write_receipts(
            receipts_dir=tmp_path / ".code-forge" / "receipts",
            round_index=0,
            l1_findings=[_finding("qodo", "fp1")],
            diff_sha256=hashlib.sha256(b"diff").hexdigest(),
            source_files=[Path("src/foo.py")],
            cwd=tmp_path,
        )
        assert "manifest" in calls
        written = json.loads((tmp_path / ".code-forge" / "receipts" / "receipt-c1p1.json").read_text())
        assert written["findings"][0]["basis"]["authority"] == "llm-docs-pinned"

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

    def test_timestamps_stay_ordered_across_back_to_back_rounds(self, tmp_path, monkeypatch):
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
        round_starts = iter([base + datetime.timedelta(milliseconds=50 * i) for i in range(3)])

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
        assert len(files_on_disk) == 9, "expected 9 receipt files, got %d" % len(files_on_disk)
        for f in files_on_disk:
            obj = json.loads(f.read_text())
            assert "timestamp" in obj, "missing timestamp in %s" % f.name
            assert "cycle" in obj, "missing cycle in %s" % f.name
            assert "pass" in obj, "missing pass in %s" % f.name

        names = [f.name for f in files_on_disk]
        assert names == [
            "receipt-c1p1.json",
            "receipt-c1p2.json",
            "receipt-c1p3.json",
            "receipt-c2p1.json",
            "receipt-c2p2.json",
            "receipt-c2p3.json",
            "receipt-c3p1.json",
            "receipt-c3p2.json",
            "receipt-c3p3.json",
        ]
        stamps = [json.loads(f.read_text())["timestamp"] for f in sorted(rd.glob("receipt-*.json"))]
        assert stamps == sorted(stamps), "timestamps invert between rounds: %s" % stamps
        for start in range(0, 9, 3):
            round_stamps = stamps[start : start + 3]
            assert len(set(round_stamps)) == 1, (
                "passes in one round should share the round's write time, got %s" % round_stamps
            )

    def test_run_verify_accepts_a_full_set_this_writer_produced(self, tmp_path, monkeypatch):
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
        round_starts = iter([base + datetime.timedelta(milliseconds=50 * i) for i in range(3)])

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
            "the timestamp gate rejected a receipt set this very writer produced: %s" % result.reason
        )


class TestBuildExcerpts:
    """_build_excerpts: content normalization for reviewer-supplied
    excerpts, and the fail-closed handling of shapes that must NOT be
    laundered into a plausible-looking string."""

    def test_list_of_lines_joined_into_string(self):
        from code_forge.receipt import _build_excerpts

        out = _build_excerpts(
            [
                {
                    "file": "src/foo.py",
                    "start_line": 1,
                    "end_line": 2,
                    "content": ["line one", "line two"],
                }
            ]
        )
        assert out[0]["content"] == "line one\nline two"

    def test_list_with_non_string_lines_left_unconverted(self):
        """A list containing a non-string element stays a list so the
        downstream schema check rejects it: joining with str(ln) would
        launder None into the string "None", the same fail-open trap
        the scalar case avoids."""
        from code_forge.receipt import _build_excerpts

        out = _build_excerpts(
            [
                {
                    "file": "src/foo.py",
                    "start_line": 1,
                    "end_line": 1,
                    "content": [1, None, "x"],
                }
            ]
        )
        assert out[0]["content"] == [1, None, "x"]

    def test_string_content_left_unchanged(self):
        from code_forge.receipt import _build_excerpts

        out = _build_excerpts(
            [
                {
                    "file": "src/foo.py",
                    "start_line": 1,
                    "end_line": 1,
                    "content": "def foo():\n    pass",
                }
            ]
        )
        assert out[0]["content"] == "def foo():\n    pass"

    def test_null_content_not_stringified_into_the_word_none(self):
        """A None content must stay None so the downstream receipt
        schema check rejects it -- str(None) == "None" is a valid
        string and would pass the isinstance(content, str) gate as a
        fabricated excerpt nobody wrote."""
        from code_forge.receipt import _build_excerpts

        out = _build_excerpts(
            [
                {
                    "file": "src/foo.py",
                    "start_line": 1,
                    "end_line": 1,
                    "content": None,
                }
            ]
        )
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
                reviewer_excerpts=[
                    {
                        "file": "src/foo.py",
                        "start_line": 1,
                        "end_line": 1,
                        "content": None,
                        "pass_name": "qodo",
                    }
                ],
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

    def test_receipt_findings_include_epistemic_basis(self, tmp_path):
        """Verify receipt finding entries include basis dictionary."""
        f_conf = _finding("qodo", "fp1", file="src/foo.py", line=10)
        f_conf.disposition = Disposition.CONFIRMED

        f_dism = _finding("qodo", "fp2", file="src/foo.py", line=20)
        f_dism.disposition = Disposition.DISMISSED

        diff_sha = hashlib.sha256(b"diff").hexdigest()
        (tmp_path / "src").mkdir(parents=True)
        (tmp_path / "src" / "foo.py").write_text("line1\nline2\n")

        write_receipts(
            receipts_dir=tmp_path / ".code-forge" / "receipts",
            round_index=2,  # cycle = 3
            l1_findings=[f_conf, f_dism],
            diff_sha256=diff_sha,
            source_files=[Path("src/foo.py")],
            cwd=tmp_path,
            manifest_tier=ManifestTier.DECLARED,
        )

        r = json.loads((tmp_path / ".code-forge" / "receipts" / "receipt-c3p1.json").read_text())
        assert len(r["findings"]) == 2

        finding_conf = r["findings"][0]
        assert "basis" in finding_conf
        assert finding_conf["basis"] == {
            "authority": "llm-docs-pinned",
            "falsification_survived": True,
            "convergence_rounds": 3,
        }

        finding_dism = r["findings"][1]
        assert "basis" in finding_dism
        assert finding_dism["basis"] == {
            "authority": "llm-docs-pinned",
            "falsification_survived": False,
            "convergence_rounds": 3,
        }


class TestExcerptPreflight:
    """Warn at receipt-write time about excerpts verify will refuse.

    verify already rejects an excerpt covering lines the diff never
    produced, but it runs at the end of the round.  By then the round
    has spent its passes.  Saying the same thing while the receipts are
    being written gives the reviewer the answer at a point where it can
    still act on it, and costs one pass over the excerpt list.

    The warning never blocks: verify remains the gate.  This is a
    diagnostic that happens to arrive earlier.
    """

    _DIFF = (
        "diff --git a/src/foo.py b/src/foo.py\n"
        "--- a/src/foo.py\n"
        "+++ b/src/foo.py\n"
        "@@ -1,2 +1,3 @@\n"
        " line1\n"
        "+added\n"
        " line2\n"
    )

    def test_warns_when_an_excerpt_covers_a_line_the_diff_never_made(
        self,
        tmp_path,
        caplog,
    ):
        # Post-image holds lines 1-3; the excerpt claims up to 99.
        with caplog.at_level(logging.WARNING):
            write_receipts(
                receipts_dir=tmp_path / ".code-forge" / "receipts",
                round_index=0,
                l1_findings=[_finding("qodo", "fp1")],
                diff_sha256=hashlib.sha256(b"d").hexdigest(),
                source_files=[Path("src/foo.py")],
                cwd=tmp_path,
                diff_text=self._DIFF,
                reviewer_excerpts=[
                    {
                        "file": "src/foo.py",
                        "start_line": 1,
                        "end_line": 99,
                        "content": "line1\nadded\nline2\n",
                        "pass_name": "qodo",
                    }
                ],
            )
        assert "pre-flight" in caplog.text
        assert "not in diff post-image" in caplog.text

    def test_silent_when_every_claimed_line_exists(self, tmp_path, caplog):
        with caplog.at_level(logging.WARNING):
            write_receipts(
                receipts_dir=tmp_path / ".code-forge" / "receipts",
                round_index=0,
                l1_findings=[_finding("qodo", "fp1")],
                diff_sha256=hashlib.sha256(b"d").hexdigest(),
                source_files=[Path("src/foo.py")],
                cwd=tmp_path,
                diff_text=self._DIFF,
                reviewer_excerpts=[
                    {
                        "file": "src/foo.py",
                        "start_line": 1,
                        "end_line": 3,
                        "content": "line1\nadded\nline2\n",
                        "pass_name": "qodo",
                    }
                ],
            )
        assert "pre-flight" not in caplog.text

    def test_warns_when_the_file_is_absent_from_the_diff(
        self,
        tmp_path,
        caplog,
    ):
        with caplog.at_level(logging.WARNING):
            write_receipts(
                receipts_dir=tmp_path / ".code-forge" / "receipts",
                round_index=0,
                l1_findings=[_finding("qodo", "fp1")],
                diff_sha256=hashlib.sha256(b"d").hexdigest(),
                source_files=[Path("src/foo.py")],
                cwd=tmp_path,
                diff_text=self._DIFF,
                reviewer_excerpts=[
                    {
                        "file": "src/never_touched.py",
                        "start_line": 1,
                        "end_line": 2,
                        "content": "whatever\n",
                        "pass_name": "qodo",
                    }
                ],
            )
        assert "not in the diff" in caplog.text

    def test_a_binary_file_excerpt_does_not_cry_wolf(self, tmp_path, caplog):
        """Exempt files have no post-image; every line would look invented.

        verify exempts binary/rename/mode-change entries, so the
        pre-flight has to exempt them too or it warns on every review
        that touches an image.
        """
        binary_diff = (
            "diff --git a/logo.png b/logo.png\n"
            "index 1111111..2222222 100644\n"
            "Binary files a/logo.png and b/logo.png differ\n"
        )
        with caplog.at_level(logging.WARNING):
            write_receipts(
                receipts_dir=tmp_path / ".code-forge" / "receipts",
                round_index=0,
                l1_findings=[_finding("qodo", "fp1")],
                diff_sha256=hashlib.sha256(b"d").hexdigest(),
                source_files=[Path("logo.png")],
                cwd=tmp_path,
                diff_text=binary_diff,
                reviewer_excerpts=[
                    {
                        "file": "logo.png",
                        "start_line": 1,
                        "end_line": 50,
                        "content": "binary\n",
                        "pass_name": "qodo",
                    }
                ],
            )
        assert "pre-flight" not in caplog.text

    def test_receipts_are_still_written_when_a_warning_fires(self, tmp_path):
        """The pre-flight is diagnostic; it must not abort the write."""
        write_receipts(
            receipts_dir=tmp_path / ".code-forge" / "receipts",
            round_index=0,
            l1_findings=[_finding("qodo", "fp1")],
            diff_sha256=hashlib.sha256(b"d").hexdigest(),
            source_files=[Path("src/foo.py")],
            cwd=tmp_path,
            diff_text=self._DIFF,
            reviewer_excerpts=[
                {
                    "file": "src/foo.py",
                    "start_line": 1,
                    "end_line": 99,
                    "content": "x\n",
                    "pass_name": "qodo",
                }
            ],
        )
        files = list((tmp_path / ".code-forge" / "receipts").glob("*.json"))
        assert len(files) == 3
