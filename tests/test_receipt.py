import hashlib
import json
from pathlib import Path

import pytest

from code_forge.disposition import Disposition
from code_forge.receipt import write_receipts
from code_forge.state import StateFinding


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
