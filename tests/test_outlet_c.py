# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for Outlet C (subagent) orchestrator.

Cases A (malformed JSON), B (cycle counting), receipts, H3 (excerpt flow).
"""
import hashlib
import json
from pathlib import Path

from code_forge.baseline import ResolvedReview
from code_forge.falsify import StubFalsifier
from code_forge.outlet_c import run_outlet_c
from code_forge.state import Verdict, load_state
from code_forge.verify import run_verify, parse_diff_files


_DIFF_TEXT = (
    "diff --git a/test.py b/test.py\n"
    "--- a/test.py\n"
    "+++ b/test.py\n"
    "@@ -1,2 +1,4 @@\n"
    " def f():\n"
    "+    x = 1\n"
    "+    y = 2\n"
    "     return 1\n"
)

_POST_IMAGE_CONTENT = "def f():\n    x = 1\n    y = 2\n    return 1\n"


def _resolved_with_diff():
    return ResolvedReview(
        source_files=[Path("test.py")],
        baseline_content=None,
        git_diff=_DIFF_TEXT,
        mode_hint="git",
    )


def _valid_reviewer_json(findings=None, code_excerpts=None):
    if findings is None:
        findings = []
    if code_excerpts is None:
        code_excerpts = [{
            "file": "test.py",
            "start_line": 1,
            "end_line": 4,
            "content": _POST_IMAGE_CONTENT,
        }]
    return json.dumps({
        "findings": findings,
        "code_excerpts": code_excerpts,
    })


def _source_hash():
    return hashlib.sha256(_DIFF_TEXT.encode()).hexdigest()


class TestMalformedJsonFailClosed:
    """Case A: malformed JSON = FAIL (not silent PASS)."""

    def test_not_json(self, tmp_path):
        result = run_outlet_c(
            resolved_review=_resolved_with_diff(),
            source_hash=_source_hash(),
            cwd=tmp_path,
            spawn_fn=lambda pn, dt: "NOT JSON AT ALL",
            falsifier=StubFalsifier(),
            max_total_rounds=1,
        )
        assert result != Verdict.PASS

    def test_missing_findings_key(self, tmp_path):
        result = run_outlet_c(
            resolved_review=_resolved_with_diff(),
            source_hash=_source_hash(),
            cwd=tmp_path,
            spawn_fn=lambda pn, dt: json.dumps({"not_findings": True}),
            falsifier=StubFalsifier(),
            max_total_rounds=1,
        )
        assert result != Verdict.PASS

    def test_missing_code_excerpts_key(self, tmp_path):
        result = run_outlet_c(
            resolved_review=_resolved_with_diff(),
            source_hash=_source_hash(),
            cwd=tmp_path,
            spawn_fn=lambda pn, dt: json.dumps({
                "findings": [],
                "no_excerpts": True,
            }),
            falsifier=StubFalsifier(),
            max_total_rounds=1,
        )
        assert result != Verdict.PASS

    def test_zero_cost_fabrication_rejected(self, tmp_path):
        """findings=[] + code_excerpts=[] must be rejected (no free clean pass)."""
        result = run_outlet_c(
            resolved_review=_resolved_with_diff(),
            source_hash=_source_hash(),
            cwd=tmp_path,
            spawn_fn=lambda pn, dt: json.dumps({
                "findings": [],
                "code_excerpts": [],
            }),
            falsifier=StubFalsifier(),
            max_total_rounds=1,
        )
        assert result != Verdict.PASS


class TestCycleCountingViaStateMachine:
    """Case B: cycle counting via StateMachine reuse."""

    def test_needs_3_clean_rounds(self, tmp_path):
        calls = {"n": 0}

        def _spawn(pass_name, diff_text):
            calls["n"] += 1
            if calls["n"] <= 6:
                return json.dumps({
                    "findings": [{
                        "file": "test.py",
                        "line": 2,
                        "severity": "P1",
                        "description": "bug-%d" % calls["n"],
                    }],
                    "code_excerpts": [{
                        "file": "test.py",
                        "start_line": 1,
                        "end_line": 4,
                        "content": _POST_IMAGE_CONTENT,
                    }],
                })
            return _valid_reviewer_json()

        result = run_outlet_c(
            resolved_review=_resolved_with_diff(),
            source_hash=_source_hash(),
            cwd=tmp_path,
            spawn_fn=_spawn,
            falsifier=StubFalsifier(),
            max_total_rounds=20,
        )
        assert result == Verdict.PASS
        state = load_state(tmp_path / ".code-forge" / "state.json")
        assert state.consecutive_clean_rounds >= 3


class TestReceiptsWritten:
    """Outlet C produces receipt files."""

    def test_receipts_exist(self, tmp_path):
        result = run_outlet_c(
            resolved_review=_resolved_with_diff(),
            source_hash=_source_hash(),
            cwd=tmp_path,
            spawn_fn=lambda pn, dt: _valid_reviewer_json(),
            falsifier=StubFalsifier(),
            max_total_rounds=10,
        )
        assert result == Verdict.PASS
        receipt_dir = tmp_path / ".code-forge" / "receipts"
        assert receipt_dir.exists()
        receipts = list(receipt_dir.glob("*.json"))
        assert len(receipts) >= 3


class TestExcerptFlowIntegration:
    """H3: reviewer excerpts flow from JSON through receipt to verify."""

    def test_excerpts_in_receipts_pass_hardened_verify(self, tmp_path):
        import datetime
        from unittest.mock import patch

        (tmp_path / "test.py").write_text(_POST_IMAGE_CONTENT)

        base = datetime.datetime(
            2026, 5, 28, 10, 0, 0, tzinfo=datetime.timezone.utc,
        )
        counter = {"n": 0}

        def _monotonic_now(*args, **kwargs):
            ts = base + datetime.timedelta(minutes=5 * counter["n"])
            counter["n"] += 1
            return ts

        with patch("code_forge.receipt.datetime") as mock_dt:
            mock_dt.datetime.now.side_effect = _monotonic_now
            mock_dt.timedelta = datetime.timedelta
            mock_dt.timezone = datetime.timezone
            result = run_outlet_c(
                resolved_review=_resolved_with_diff(),
                source_hash=_source_hash(),
                cwd=tmp_path,
                spawn_fn=lambda pn, dt: _valid_reviewer_json(),
                falsifier=StubFalsifier(),
                max_total_rounds=10,
            )
        assert result == Verdict.PASS

        receipt_dir = tmp_path / ".code-forge" / "receipts"
        receipts = list(receipt_dir.glob("*.json"))
        assert len(receipts) >= 3

        found_excerpts = False
        for rp in receipts:
            data = json.loads(rp.read_text())
            if data.get("code_excerpts"):
                found_excerpts = True
                exc = data["code_excerpts"][0]
                assert exc["file"] == "test.py"
                break
        assert found_excerpts

        source_hash = _source_hash()
        diff_files = parse_diff_files(_DIFF_TEXT)
        vr = run_verify(
            cwd=tmp_path,
            diff_sha256=source_hash,
            diff_files=diff_files,
            hardened=True,
            diff_text=_DIFF_TEXT,
        )
        assert vr.passed, vr.reason
