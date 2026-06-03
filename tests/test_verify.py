import hashlib
import json
from pathlib import Path
import pytest
from code_forge.verify import run_verify, VerifyResult


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
        import time
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
