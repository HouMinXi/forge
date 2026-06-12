"""TDD tests for smoke receipt write/read infrastructure (Plan 20-01, RED phase).

Covers:
- write_smoke_receipt creates JSON with required fields
- write_smoke_receipt sets status="VERIFIED" when exit_code==0, "FAILED" otherwise
- write_smoke_receipt names file smoke-receipt-{surface}.json
- write_smoke_receipt calls receipts_dir.mkdir(parents=True, exist_ok=True)
- write_smoke_receipt uses atomic tmp+replace pattern
- receipt diff_sha256 keyed by compute_source_hash(git_diff=diff_text)
- read_smoke_receipts returns list of receipt dicts from directory
- read_smoke_receipts returns [] when directory absent or empty
- transcript_sha256 is sha256 of transcript bytes
"""
from __future__ import annotations

import hashlib
import json

from code_forge.source import compute_source_hash

# ---------------------------------------------------------------------------
# write_smoke_receipt
# ---------------------------------------------------------------------------


class TestWriteSmokeReceipt:
    """write_smoke_receipt writes correct JSON with atomic pattern."""

    def test_creates_file_in_receipts_dir(self, tmp_path):
        from code_forge.runtime import write_smoke_receipt

        receipts_dir = tmp_path / "smoke-receipts"
        diff = "diff --git a/f b/f\n+change"
        write_smoke_receipt(
            receipts_dir=receipts_dir,
            diff_text=diff,
            surface="nftables",
            command="pytest tests/",
            exit_code=0,
            transcript=b"ok",
            timestamp="2026-06-12T10:00:00Z",
        )

        expected = receipts_dir / "smoke-receipt-nftables.json"
        assert expected.exists()

    def test_file_named_by_surface(self, tmp_path):
        from code_forge.runtime import write_smoke_receipt

        receipts_dir = tmp_path / "receipts"
        write_smoke_receipt(
            receipts_dir=receipts_dir,
            diff_text="diff --git a/f b/f\n+x",
            surface="systemd",
            command="systemctl status",
            exit_code=0,
            transcript=b"active",
            timestamp="2026-06-12T10:00:00Z",
        )

        assert (receipts_dir / "smoke-receipt-systemd.json").exists()

    def test_status_verified_when_exit_code_zero(self, tmp_path):
        from code_forge.runtime import write_smoke_receipt

        receipts_dir = tmp_path / "receipts"
        write_smoke_receipt(
            receipts_dir=receipts_dir,
            diff_text="diff --git a/f b/f\n+x",
            surface="default",
            command="pytest",
            exit_code=0,
            transcript=b"ok",
            timestamp="2026-06-12T10:00:00Z",
        )

        data = json.loads((receipts_dir / "smoke-receipt-default.json").read_text())
        assert data["status"] == "VERIFIED"

    def test_status_failed_when_exit_code_nonzero(self, tmp_path):
        from code_forge.runtime import write_smoke_receipt

        receipts_dir = tmp_path / "receipts"
        write_smoke_receipt(
            receipts_dir=receipts_dir,
            diff_text="diff --git a/f b/f\n+x",
            surface="nftables",
            command="pytest",
            exit_code=1,
            transcript=b"FAILED",
            timestamp="2026-06-12T10:00:00Z",
        )

        data = json.loads((receipts_dir / "smoke-receipt-nftables.json").read_text())
        assert data["status"] == "FAILED"

    def test_diff_sha256_keyed_by_compute_source_hash(self, tmp_path):
        from code_forge.runtime import write_smoke_receipt

        receipts_dir = tmp_path / "receipts"
        diff = "diff --git a/rules.sh b/rules.sh\n+nft add"
        write_smoke_receipt(
            receipts_dir=receipts_dir,
            diff_text=diff,
            surface="nftables",
            command="pytest",
            exit_code=0,
            transcript=b"ok",
            timestamp="2026-06-12T10:00:00Z",
        )

        data = json.loads((receipts_dir / "smoke-receipt-nftables.json").read_text())
        expected_hash = compute_source_hash(git_diff=diff)
        assert data["diff_sha256"] == expected_hash

    def test_transcript_sha256_is_sha256_of_transcript_bytes(self, tmp_path):
        from code_forge.runtime import write_smoke_receipt

        receipts_dir = tmp_path / "receipts"
        transcript = b"test output\nok\n"
        write_smoke_receipt(
            receipts_dir=receipts_dir,
            diff_text="diff --git a/f b/f\n+x",
            surface="default",
            command="pytest",
            exit_code=0,
            transcript=transcript,
            timestamp="2026-06-12T10:00:00Z",
        )

        data = json.loads((receipts_dir / "smoke-receipt-default.json").read_text())
        expected = hashlib.sha256(transcript).hexdigest()
        assert data["transcript_sha256"] == expected

    def test_all_required_fields_present(self, tmp_path):
        from code_forge.runtime import write_smoke_receipt

        receipts_dir = tmp_path / "receipts"
        write_smoke_receipt(
            receipts_dir=receipts_dir,
            diff_text="diff --git a/f b/f\n+x",
            surface="nftables",
            command="pytest tests/test_rules.py",
            exit_code=0,
            transcript=b"ok",
            timestamp="2026-06-12T10:00:00Z",
        )

        data = json.loads((receipts_dir / "smoke-receipt-nftables.json").read_text())
        required_fields = [
            "diff_sha256", "surface", "command", "exit_code",
            "transcript_sha256", "timestamp", "status",
        ]
        for field in required_fields:
            assert field in data, "Missing field: %s" % field

    def test_exit_code_stored_as_int(self, tmp_path):
        from code_forge.runtime import write_smoke_receipt

        receipts_dir = tmp_path / "receipts"
        write_smoke_receipt(
            receipts_dir=receipts_dir,
            diff_text="diff --git a/f b/f\n+x",
            surface="default",
            command="pytest",
            exit_code=42,
            transcript=b"",
            timestamp="2026-06-12T10:00:00Z",
        )

        data = json.loads((receipts_dir / "smoke-receipt-default.json").read_text())
        assert data["exit_code"] == 42
        assert isinstance(data["exit_code"], int)

    def test_surface_stored_in_receipt(self, tmp_path):
        from code_forge.runtime import write_smoke_receipt

        receipts_dir = tmp_path / "receipts"
        write_smoke_receipt(
            receipts_dir=receipts_dir,
            diff_text="diff --git a/f b/f\n+x",
            surface="my-surface",
            command="cmd",
            exit_code=0,
            transcript=b"",
            timestamp="2026-06-12T10:00:00Z",
        )

        data = json.loads((receipts_dir / "smoke-receipt-my-surface.json").read_text())
        assert data["surface"] == "my-surface"

    def test_command_stored_in_receipt(self, tmp_path):
        from code_forge.runtime import write_smoke_receipt

        receipts_dir = tmp_path / "receipts"
        write_smoke_receipt(
            receipts_dir=receipts_dir,
            diff_text="diff --git a/f b/f\n+x",
            surface="default",
            command="pytest tests/ -v",
            exit_code=0,
            transcript=b"",
            timestamp="2026-06-12T10:00:00Z",
        )

        data = json.loads((receipts_dir / "smoke-receipt-default.json").read_text())
        assert data["command"] == "pytest tests/ -v"

    def test_timestamp_stored_in_receipt(self, tmp_path):
        from code_forge.runtime import write_smoke_receipt

        receipts_dir = tmp_path / "receipts"
        ts = "2026-06-12T15:30:00Z"
        write_smoke_receipt(
            receipts_dir=receipts_dir,
            diff_text="diff --git a/f b/f\n+x",
            surface="default",
            command="cmd",
            exit_code=0,
            transcript=b"",
            timestamp=ts,
        )

        data = json.loads((receipts_dir / "smoke-receipt-default.json").read_text())
        assert data["timestamp"] == ts

    def test_creates_parents(self, tmp_path):
        """receipts_dir.mkdir(parents=True, exist_ok=True) is called."""
        from code_forge.runtime import write_smoke_receipt

        # Deep nested path that doesn't exist yet
        receipts_dir = tmp_path / "a" / "b" / "c" / "smoke-receipts"
        assert not receipts_dir.exists()

        write_smoke_receipt(
            receipts_dir=receipts_dir,
            diff_text="diff --git a/f b/f\n+x",
            surface="default",
            command="cmd",
            exit_code=0,
            transcript=b"",
            timestamp="2026-06-12T10:00:00Z",
        )

        assert receipts_dir.exists()
        assert (receipts_dir / "smoke-receipt-default.json").exists()

    def test_atomic_write_tmp_replace(self, tmp_path):
        """File is written atomically (no partial file visible during write)."""
        from code_forge.runtime import write_smoke_receipt

        receipts_dir = tmp_path / "receipts"
        write_smoke_receipt(
            receipts_dir=receipts_dir,
            diff_text="diff --git a/f b/f\n+x",
            surface="nftables",
            command="pytest",
            exit_code=0,
            transcript=b"ok",
            timestamp="2026-06-12T10:00:00Z",
        )

        # Result must be valid JSON (not partial)
        path = receipts_dir / "smoke-receipt-nftables.json"
        data = json.loads(path.read_text())
        assert "diff_sha256" in data

    def test_receipt_is_valid_json(self, tmp_path):
        from code_forge.runtime import write_smoke_receipt

        receipts_dir = tmp_path / "receipts"
        write_smoke_receipt(
            receipts_dir=receipts_dir,
            diff_text="diff --git a/f b/f\n+x",
            surface="test",
            command="cmd",
            exit_code=0,
            transcript=b"output",
            timestamp="2026-06-12T10:00:00Z",
        )

        path = receipts_dir / "smoke-receipt-test.json"
        content = path.read_text()
        # Should not raise
        parsed = json.loads(content)
        assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# read_smoke_receipts
# ---------------------------------------------------------------------------


class TestReadSmokeReceipts:
    """read_smoke_receipts returns list of dicts from a directory."""

    def test_returns_empty_when_directory_absent(self, tmp_path):
        from code_forge.runtime import read_smoke_receipts

        missing_dir = tmp_path / "nonexistent"
        result = read_smoke_receipts(missing_dir)
        assert result == []

    def test_returns_empty_when_directory_empty(self, tmp_path):
        from code_forge.runtime import read_smoke_receipts

        receipts_dir = tmp_path / "receipts"
        receipts_dir.mkdir()
        result = read_smoke_receipts(receipts_dir)
        assert result == []

    def test_reads_single_receipt(self, tmp_path):
        from code_forge.runtime import read_smoke_receipts, write_smoke_receipt

        receipts_dir = tmp_path / "receipts"
        write_smoke_receipt(
            receipts_dir=receipts_dir,
            diff_text="diff --git a/f b/f\n+x",
            surface="nftables",
            command="pytest",
            exit_code=0,
            transcript=b"ok",
            timestamp="2026-06-12T10:00:00Z",
        )

        result = read_smoke_receipts(receipts_dir)
        assert len(result) == 1
        assert result[0]["surface"] == "nftables"

    def test_reads_multiple_receipts(self, tmp_path):
        from code_forge.runtime import read_smoke_receipts, write_smoke_receipt

        receipts_dir = tmp_path / "receipts"
        diff = "diff --git a/f b/f\n+x"
        write_smoke_receipt(
            receipts_dir=receipts_dir,
            diff_text=diff,
            surface="nftables",
            command="pytest",
            exit_code=0,
            transcript=b"ok",
            timestamp="2026-06-12T10:00:00Z",
        )
        write_smoke_receipt(
            receipts_dir=receipts_dir,
            diff_text=diff,
            surface="systemd",
            command="systemctl status",
            exit_code=0,
            transcript=b"active",
            timestamp="2026-06-12T10:00:01Z",
        )

        result = read_smoke_receipts(receipts_dir)
        assert len(result) == 2
        surfaces = {r["surface"] for r in result}
        assert "nftables" in surfaces
        assert "systemd" in surfaces

    def test_returns_list_of_dicts(self, tmp_path):
        from code_forge.runtime import read_smoke_receipts, write_smoke_receipt

        receipts_dir = tmp_path / "receipts"
        write_smoke_receipt(
            receipts_dir=receipts_dir,
            diff_text="diff --git a/f b/f\n+x",
            surface="default",
            command="cmd",
            exit_code=0,
            transcript=b"",
            timestamp="2026-06-12T10:00:00Z",
        )

        result = read_smoke_receipts(receipts_dir)
        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, dict)

    def test_ignores_non_smoke_receipt_files(self, tmp_path):
        """Only smoke-receipt-*.json files are read."""
        from code_forge.runtime import read_smoke_receipts

        receipts_dir = tmp_path / "receipts"
        receipts_dir.mkdir()

        # Write a non-matching file
        (receipts_dir / "receipt-c1p1.json").write_text(
            json.dumps({"cycle": 1, "pass": 1})
        )
        # Write a matching file
        receipt_data = {
            "diff_sha256": "abc",
            "surface": "nftables",
            "command": "pytest",
            "exit_code": 0,
            "transcript_sha256": "def",
            "timestamp": "2026-06-12T10:00:00Z",
            "status": "VERIFIED",
        }
        (receipts_dir / "smoke-receipt-nftables.json").write_text(
            json.dumps(receipt_data)
        )

        result = read_smoke_receipts(receipts_dir)
        assert len(result) == 1
        assert result[0]["surface"] == "nftables"

    def test_round_trip_write_then_read(self, tmp_path):
        """Data written by write_smoke_receipt is readable by read_smoke_receipts."""
        from code_forge.runtime import read_smoke_receipts, write_smoke_receipt

        receipts_dir = tmp_path / "receipts"
        diff = "diff --git a/rules.sh b/rules.sh\n+nft add"
        transcript = b"nftables reload ok\n"
        ts = "2026-06-12T10:00:00Z"

        write_smoke_receipt(
            receipts_dir=receipts_dir,
            diff_text=diff,
            surface="nftables",
            command="pytest tests/test_rules.py",
            exit_code=0,
            transcript=transcript,
            timestamp=ts,
        )

        receipts = read_smoke_receipts(receipts_dir)
        assert len(receipts) == 1
        r = receipts[0]
        assert r["surface"] == "nftables"
        assert r["status"] == "VERIFIED"
        assert r["diff_sha256"] == compute_source_hash(git_diff=diff)
        assert r["transcript_sha256"] == hashlib.sha256(transcript).hexdigest()
        assert r["timestamp"] == ts
        assert r["command"] == "pytest tests/test_rules.py"
        assert r["exit_code"] == 0
