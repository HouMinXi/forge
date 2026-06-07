import hashlib
import json
from pathlib import Path
from code_forge.verify import run_verify, parse_diff_files


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _receipt(cycle, pass_n, diff_sha, covered_start=1, covered_end=50):
    return {
        "cycle": cycle, "pass": pass_n,
        "skill": ["qodo-review", "code-review-expert", "adversarial-qe"][pass_n - 1],
        "diff_sha256": diff_sha,
        "timestamp": "2026-05-28T10:%02d:00Z" % (cycle * 3 + pass_n),
        "findings_count": 0, "findings": [],
        "anchors": [{"file": "src/f.py", "line": 1, "text": "def f():"}],
        "code_excerpts": [
            {"file": "src/f.py", "start_line": 1, "end_line": 3,
             "content": "def f():\n    return 1\n",
             "rationale": "checked"}
        ],
        "covered_line_ranges": [
            {"file": "src/f.py", "start": covered_start, "end": covered_end}
        ],
    }


def _write_all(rd, diff_sha, vary=True):
    for c in range(1, 4):
        off = (c - 1) * 10 if vary else 0
        for p in range(1, 4):
            name = "receipt-c%dp%d.json" % (c, p)
            (rd / name).write_text(json.dumps(
                _receipt(c, p, diff_sha, 1 + off, 45 + off)
            ))


class TestVerifyChecks:
    def test_pass_complete(self, tmp_path):
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "f.py").write_text("def f():\n    return 1\n")
        sha = _sha("diff")
        _write_all(rd, sha)
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
        assert r.passed

    def test_fail_missing(self, tmp_path):
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        sha = _sha("diff")
        for c in range(1, 4):
            for p in range(1, 3):
                name = "receipt-c%dp%d.json" % (c, p)
                (rd / name).write_text(json.dumps(_receipt(c, p, sha)))
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
        assert not r.passed

    def test_fail_stale_hash(self, tmp_path):
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        _write_all(rd, "old-hash")
        r = run_verify(tmp_path, "new-hash", {"src/f.py": list(range(1, 51))})
        assert not r.passed

    def test_fail_high_jaccard(self, tmp_path):
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        sha = _sha("diff")
        _write_all(rd, sha, vary=False)
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
        assert not r.passed

    def test_fail_low_coverage(self, tmp_path):
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        sha = _sha("diff")
        for c in range(1, 4):
            for p in range(1, 4):
                name = "receipt-c%dp%d.json" % (c, p)
                (rd / name).write_text(json.dumps(
                    _receipt(c, p, sha, covered_start=1, covered_end=5)
                ))
        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 201))})
        assert not r.passed


class TestReceiptVerifyE2E:
    """End-to-end: receipt writer output must pass verify checks."""

    def test_receipt_writer_output_passes_verify(self, tmp_path):
        import datetime
        from unittest.mock import patch
        from code_forge.receipt import write_receipts
        from code_forge.disposition import Disposition
        from code_forge.state import StateFinding

        (tmp_path / "src").mkdir(parents=True)
        lines = ["line%d\n" % i for i in range(1, 81)]
        (tmp_path / "src" / "foo.py").write_text("".join(lines))
        diff_sha = _sha("diff")
        diff_files = {"src/foo.py": list(range(1, 81))}

        base = datetime.datetime(2026, 5, 28, 10, 0, 0,
                                 tzinfo=datetime.timezone.utc)
        cycle_locs = [(10, 30, 50), (20, 40, 60), (30, 50, 70)]
        passes = ["qodo", "expert", "adversarial"]
        for round_idx in range(3):
            fake_now = base + datetime.timedelta(minutes=round_idx * 5)
            locs = cycle_locs[round_idx]
            findings = []
            for pi, pn in enumerate(passes):
                ln = locs[pi]
                findings.append(StateFinding(
                    id="l1-%s-fp%d%d" % (pn, round_idx, pi),
                    fingerprint="fp%d%d" % (round_idx, pi), source="L1",
                    disposition=Disposition.UNCERTAIN,
                    file="src/foo.py",
                    line_range=[ln, ln],
                    description="[%s] test finding r%dp%d" % (pn, round_idx, pi),
                ))
            with patch("code_forge.receipt.datetime") as mock_dt:
                mock_dt.datetime.now.return_value = fake_now
                mock_dt.timedelta = datetime.timedelta
                mock_dt.timezone = datetime.timezone
                write_receipts(
                    receipts_dir=tmp_path / ".code-forge" / "receipts",
                    round_index=round_idx,
                    l1_findings=findings,
                    diff_sha256=diff_sha,
                    source_files=[Path("src/foo.py")],
                    cwd=tmp_path,
                    diff_files=diff_files,
                )

        r = run_verify(tmp_path, diff_sha, diff_files)
        assert r.passed, "E2E failed: %s" % r.reason


# ---------------------------------------------------------------------------
# Hardened-verify fixtures
# ---------------------------------------------------------------------------

# Valid 3-hunk unified diff; parseable by unidiff.
# foo.py hunks: lines 1-3 (y=2 added) and 6-8 (b=2 added).
# bar.py hunk: lines 1-3 (q=2 added).
# parse_diff_files returns foo.py:[1..3,6..8], bar.py:[1..3] = 9 lines total.
_HARDEN_DIFF = (
    "diff --git a/foo.py b/foo.py\n"
    "--- a/foo.py\n"
    "+++ b/foo.py\n"
    "@@ -1,2 +1,3 @@\n"
    " x = 1\n"
    "+y = 2\n"
    " z = 3\n"
    "@@ -5,2 +6,3 @@\n"
    " a = 1\n"
    "+b = 2\n"
    " c = 3\n"
    "diff --git a/bar.py b/bar.py\n"
    "--- a/bar.py\n"
    "+++ b/bar.py\n"
    "@@ -1,2 +1,3 @@\n"
    " p = 1\n"
    "+q = 2\n"
    " r = 3\n"
)

# Excerpts that witness all 3 hunks and match post-image content exactly.
_EXCERPTS_OK = [
    {"file": "foo.py", "start_line": 1, "end_line": 3,
     "content": "x = 1\ny = 2\nz = 3"},
    {"file": "foo.py", "start_line": 6, "end_line": 8,
     "content": "a = 1\nb = 2\nc = 3"},
    {"file": "bar.py", "start_line": 1, "end_line": 3,
     "content": "p = 1\nq = 2\nr = 3"},
]


def _hreceipt(cycle, pass_n, diff_sha, excerpts=None, findings=None):
    """Build one receipt for hardened-verify tests."""
    return {
        "cycle": cycle,
        "pass": pass_n,
        "skill": ["qodo-review", "code-review-expert", "adversarial-qe"][pass_n - 1],
        "diff_sha256": diff_sha,
        # Monotonic: cycle*3+pass_n gives 4..12 across (c,p) in file-sort order.
        "timestamp": "2026-06-07T10:%02d:00Z" % (cycle * 3 + pass_n),
        "findings_count": len(findings or []),
        "findings": findings if findings is not None else [],
        "anchors": [],
        "code_excerpts": excerpts if excerpts is not None else list(_EXCERPTS_OK),
        "covered_line_ranges": [],
    }


def _write_hardened(rd, diff_sha, excerpts=None, findings=None):
    """Write 9 receipts (3 cycles x 3 passes) for hardened-verify tests."""
    for c in range(1, 4):
        for p in range(1, 4):
            (rd / ("receipt-c%dp%d.json" % (c, p))).write_text(
                json.dumps(_hreceipt(c, p, diff_sha,
                                     excerpts=excerpts, findings=findings))
            )


class TestHardenedVerify:
    """Tests that run_verify with diff_text=DIFF enters the hardened branch.

    Every assertion targets a reason string that ONLY the hardened branch
    produces, so a green assertion proves hardened execution (not legacy).
    """

    def _rd(self, tmp_path):
        rd = tmp_path / ".code-forge" / "receipts"
        rd.mkdir(parents=True)
        return rd

    def test_witnessed_pass(self, tmp_path):
        """Hardened path: all hunks witnessed, correct content, >= 60% coverage."""
        rd = self._rd(tmp_path)
        sha = _sha(_HARDEN_DIFF)
        diff_files = parse_diff_files(_HARDEN_DIFF)
        _write_hardened(rd, sha)          # _EXCERPTS_OK, findings=[]
        r = run_verify(tmp_path, sha, diff_files, diff_text=_HARDEN_DIFF)
        assert r.passed, r.reason

    def test_unwitnessed_hunk_fail(self, tmp_path):
        """STEP A: bar.py hunk has no overlapping excerpt -> unwitnessed hunk."""
        rd = self._rd(tmp_path)
        sha = _sha(_HARDEN_DIFF)
        diff_files = parse_diff_files(_HARDEN_DIFF)
        # Omit bar.py excerpt entirely -- STEP A must reject.
        partial = [
            {"file": "foo.py", "start_line": 1, "end_line": 3,
             "content": "x = 1\ny = 2\nz = 3"},
            {"file": "foo.py", "start_line": 6, "end_line": 8,
             "content": "a = 1\nb = 2\nc = 3"},
        ]
        _write_hardened(rd, sha, excerpts=partial)
        r = run_verify(tmp_path, sha, diff_files, diff_text=_HARDEN_DIFF)
        assert not r.passed
        assert "unwitnessed hunk" in r.reason

    def test_diff_marker_content_fail(self, tmp_path):
        """STEP C (Q2 guard): '+y = 2' in content mismatches post-image 'y = 2'."""
        rd = self._rd(tmp_path)
        sha = _sha(_HARDEN_DIFF)
        diff_files = parse_diff_files(_HARDEN_DIFF)
        dirty = [
            # Leading '+' makes line 2 mismatch post-image.
            {"file": "foo.py", "start_line": 1, "end_line": 3,
             "content": "x = 1\n+y = 2\nz = 3"},
            {"file": "foo.py", "start_line": 6, "end_line": 8,
             "content": "a = 1\nb = 2\nc = 3"},
            {"file": "bar.py", "start_line": 1, "end_line": 3,
             "content": "p = 1\nq = 2\nr = 3"},
        ]
        _write_hardened(rd, sha, excerpts=dirty)
        r = run_verify(tmp_path, sha, diff_files, diff_text=_HARDEN_DIFF)
        assert not r.passed
        assert "excerpt content mismatch at" in r.reason

    def test_low_coverage_fail(self, tmp_path):
        """Check 6: per-cycle coverage 3/9 = 33% < 60% -> fail."""
        rd = self._rd(tmp_path)
        sha = _sha(_HARDEN_DIFF)
        diff_files = parse_diff_files(_HARDEN_DIFF)
        # One excerpt per hunk at context-only lines (1,6,1) -- witnesses all
        # hunks for STEP A but covers only lines NOT in parse_diff_files output
        # if parse_diff_files returns added lines only. Even if it returns full
        # hunk ranges (1-3, 6-8, 1-3), 3 single-line excerpts = 3/9 = 33% < 60%.
        sparse = [
            {"file": "foo.py", "start_line": 3, "end_line": 3,
             "content": "z = 3"},
            {"file": "foo.py", "start_line": 6, "end_line": 6,
             "content": "a = 1"},
            {"file": "bar.py", "start_line": 3, "end_line": 3,
             "content": "r = 3"},
        ]
        _write_hardened(rd, sha, excerpts=sparse)
        r = run_verify(tmp_path, sha, diff_files, diff_text=_HARDEN_DIFF)
        assert not r.passed
        assert "< 60%" in r.reason

    def test_missing_field_fail(self, tmp_path):
        """STEP 0: excerpt with start_line missing -> excerpt missing required fields."""
        rd = self._rd(tmp_path)
        sha = _sha(_HARDEN_DIFF)
        diff_files = parse_diff_files(_HARDEN_DIFF)
        bad = [
            {"file": "foo.py", "start_line": 1, "end_line": 3,
             "content": "x = 1\ny = 2\nz = 3"},
            # Missing start_line -- STEP 0 rejects before STEP A.
            {"file": "foo.py", "end_line": 8, "content": "a = 1\nb = 2\nc = 3"},
            {"file": "bar.py", "start_line": 1, "end_line": 3,
             "content": "p = 1\nq = 2\nr = 3"},
        ]
        _write_hardened(rd, sha, excerpts=bad)
        r = run_verify(tmp_path, sha, diff_files, diff_text=_HARDEN_DIFF)
        assert not r.passed
        assert "excerpt missing required fields" in r.reason
