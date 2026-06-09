# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for Outlet C (subagent) orchestrator.

Cases A (malformed JSON), B (cycle counting), receipts, H3 (excerpt flow).
SC1-SC3: reviewer independence tests (Phase 15).
"""
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from code_forge.baseline import ResolvedReview
from code_forge.falsify import StubFalsifier
from code_forge.llm_invoke import LLMResult, Usage
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


# ---------------------------------------------------------------------------
# SC1-SC3: Reviewer independence tests (Phase 15)
# ---------------------------------------------------------------------------

def _make_valid_json():
    """Minimal reviewer JSON with excerpt covering the test hunk."""
    return json.dumps({
        "findings": [],
        "code_excerpts": [{
            "file": "test.py",
            "start_line": 1,
            "end_line": 4,
            "content": "def f():\n    x = 1\n    y = 2\n    return 1\n",
        }],
    })


def _make_llm_result(content=None):
    if content is None:
        content = _make_valid_json()
    return LLMResult(content=content, usage=Usage(), duration_s=0.1)


class TestIndependence:
    """SC1: spawn_fn calls llm_invoke per pass with fresh context."""

    def test_outlet_c_calls_spawn_fn_per_pass(self, tmp_path):
        """L-R5-04: renamed from test_spawn_fn_called_per_pass.

        outlet_c calls spawn_fn once per pass per round. After 3 consecutive
        clean rounds (9 total passes), llm_invoke call_count == 9.
        """
        with patch("code_forge.llm_invoke.llm_invoke") as mock_llm:
            mock_llm.return_value = _make_llm_result()

            def _spawn(pass_name, diff_text):
                from code_forge.llm_invoke import llm_invoke
                result = llm_invoke("prompt", backend=None)
                return result.content

            result = run_outlet_c(
                resolved_review=_resolved_with_diff(),
                source_hash=_source_hash(),
                cwd=tmp_path,
                spawn_fn=_spawn,
                falsifier=StubFalsifier(),
                max_total_rounds=20,
            )
        assert result == Verdict.PASS
        # 3 passes x 3 clean rounds = 9 llm_invoke calls
        assert mock_llm.call_count == 9

    def test_no_shared_state_between_passes(self, tmp_path):
        """M-01: each pass gets a fresh prompt with only its own role.

        The three roles rotate: qodo (structural), expert (senior engineer),
        adversarial (adversarial QE). For each consecutive call pair (N, N+1):
        the role string from call N must NOT appear in call N+1's prompt.
        """
        prompts = []

        with patch("code_forge.llm_invoke.llm_invoke") as mock_llm:
            mock_llm.return_value = _make_llm_result()

            def _spawn(pass_name, diff_text):
                from code_forge.llm_invoke import llm_invoke
                from code_forge.cli import _make_subagent_spawn
                # build a real spawn closure and call llm_invoke with it
                spawn = _make_subagent_spawn(
                    backend=None, conv_digest="", post_image=""
                )
                raw = spawn(pass_name, diff_text)
                return raw

            run_outlet_c(
                resolved_review=_resolved_with_diff(),
                source_hash=_source_hash(),
                cwd=tmp_path,
                spawn_fn=_spawn,
                falsifier=StubFalsifier(),
                max_total_rounds=20,
            )

        _ROLES = [
            "structural code reviewer",
            "senior engineer",
            "adversarial QE",
        ]
        calls = mock_llm.call_args_list
        assert len(calls) >= 9
        # Each call uses its own role, not the previous call's role
        for i in range(len(calls) - 1):
            prompt_n = calls[i][0][0]
            prompt_n1 = calls[i + 1][0][0]
            # Identify role in call N
            role_n = next(
                (r for r in _ROLES if r in prompt_n), None
            )
            if role_n is not None:
                # Role from call N must not bleed into call N+1
                assert role_n not in prompt_n1, (
                    "Role '%s' from call %d leaked into call %d prompt"
                    % (role_n, i, i + 1)
                )


class TestCriteriaPayload:
    """SC2: prompt contains diff + role, no session context."""

    def test_prompt_contains_diff_and_role(self, tmp_path):
        """Each llm_invoke call must include 'Diff:' and the diff text."""
        with patch("code_forge.llm_invoke.llm_invoke") as mock_llm:
            mock_llm.return_value = _make_llm_result()

            from code_forge.cli import _make_subagent_spawn
            spawn = _make_subagent_spawn(
                backend=None, conv_digest="", post_image=""
            )

            _ROLES = [
                "structural code reviewer",
                "senior engineer",
                "adversarial QE",
            ]

            def _spawn(pass_name, diff_text):
                return spawn(pass_name, diff_text)

            run_outlet_c(
                resolved_review=_resolved_with_diff(),
                source_hash=_source_hash(),
                cwd=tmp_path,
                spawn_fn=_spawn,
                falsifier=StubFalsifier(),
                max_total_rounds=20,
            )

        for call in mock_llm.call_args_list:
            prompt = call[0][0]
            assert "Diff:" in prompt
            # The diff text fragment must appear
            assert "def f():" in prompt or "x = 1" in prompt
            # At least one role string per prompt
            assert any(r in prompt for r in _ROLES)

    def test_prompt_has_no_session_context(self, tmp_path):
        """SC2: no implementer session context in any reviewer prompt."""
        with patch("code_forge.llm_invoke.llm_invoke") as mock_llm:
            mock_llm.return_value = _make_llm_result()

            from code_forge.cli import _make_subagent_spawn
            spawn = _make_subagent_spawn(
                backend=None, conv_digest="", post_image=""
            )

            run_outlet_c(
                resolved_review=_resolved_with_diff(),
                source_hash=_source_hash(),
                cwd=tmp_path,
                spawn_fn=lambda pn, dt: spawn(pn, dt),
                falsifier=StubFalsifier(),
                max_total_rounds=20,
            )

        _FORBIDDEN = [
            "Human:", "Assistant:", "previous message",
            "I think", "let me", "conversation",
        ]
        for call in mock_llm.call_args_list:
            prompt = call[0][0]
            for marker in _FORBIDDEN:
                assert marker not in prompt, (
                    "Session context marker '%s' found in reviewer prompt" % marker
                )


class TestContextIsolation:
    """SC3: no state carries between passes."""

    def test_each_call_independent(self, tmp_path):
        """Each pass gets a fresh prompt not containing previous pass findings."""
        call_index = {"n": 0}
        _UNIQUE_BUGS = ["unique-bug-alpha", "unique-bug-beta", "unique-bug-gamma"]

        def _side_effect(*args, **kwargs):
            idx = call_index["n"]
            call_index["n"] += 1
            # Return a different unique finding per call
            bug = _UNIQUE_BUGS[idx % len(_UNIQUE_BUGS)]
            content = json.dumps({
                "findings": [{
                    "file": "test.py",
                    "line": 2,
                    "severity": "P3",
                    "description": bug,
                }],
                "code_excerpts": [{
                    "file": "test.py",
                    "start_line": 1,
                    "end_line": 4,
                    "content": "def f():\n    x = 1\n    y = 2\n    return 1\n",
                }],
            })
            return LLMResult(content=content, usage=Usage(), duration_s=0.1)

        with patch("code_forge.llm_invoke.llm_invoke") as mock_llm:
            mock_llm.side_effect = _side_effect

            from code_forge.cli import _make_subagent_spawn
            spawn = _make_subagent_spawn(
                backend=None, conv_digest="", post_image=""
            )

            run_outlet_c(
                resolved_review=_resolved_with_diff(),
                source_hash=_source_hash(),
                cwd=tmp_path,
                spawn_fn=lambda pn, dt: spawn(pn, dt),
                falsifier=StubFalsifier(),
                max_total_rounds=20,
            )

        calls = mock_llm.call_args_list
        # Each call's prompt must not contain the PREVIOUS call's unique bug string
        for i in range(1, len(calls)):
            prompt = calls[i][0][0]
            prev_bug = _UNIQUE_BUGS[(i - 1) % len(_UNIQUE_BUGS)]
            assert prev_bug not in prompt, (
                "Bug from call %d ('%s') leaked into call %d prompt"
                % (i - 1, prev_bug, i)
            )


class TestThresholdThreading:
    """clean_round_threshold threaded to StateMachine."""

    def test_threshold_threading(self, tmp_path):
        """run_outlet_c with clean_round_threshold=2 converges after 2."""
        result = run_outlet_c(
            resolved_review=_resolved_with_diff(),
            source_hash=_source_hash(),
            cwd=tmp_path,
            spawn_fn=lambda pn, dt: _valid_reviewer_json(),
            falsifier=StubFalsifier(),
            max_total_rounds=10,
            clean_round_threshold=2,
        )
        assert result == Verdict.PASS
        state = load_state(tmp_path / ".code-forge" / "state.json")
        assert state.consecutive_clean_rounds >= 2
        assert state.round == 1  # rounds 0 and 1 are clean


class TestOutletCInfraSourceTagging:
    """F3: outlet_c error-path findings tagged source=INFRA."""

    def test_outlet_c_spawn_fail_tagged_infra(self, tmp_path):
        """spawn-fail finding has source=INFRA and disposition=CONFIRMED."""
        def _raise_spawn(pn, dt):
            raise RuntimeError("spawn exploded")

        result = run_outlet_c(
            resolved_review=_resolved_with_diff(),
            source_hash=_source_hash(),
            cwd=tmp_path,
            spawn_fn=_raise_spawn,
            falsifier=StubFalsifier(),
            max_total_rounds=1,
        )
        state = load_state(tmp_path / ".code-forge" / "state.json")
        infra = [f for f in state.findings if f.source == "INFRA"]
        assert len(infra) >= 1
        for f in infra:
            assert f.disposition.value == "CONFIRMED"
            assert "spawn-fail" in f.fingerprint

    def test_outlet_c_schema_fail_tagged_infra(self, tmp_path):
        """schema-fail finding has source=INFRA and disposition=CONFIRMED."""
        result = run_outlet_c(
            resolved_review=_resolved_with_diff(),
            source_hash=_source_hash(),
            cwd=tmp_path,
            spawn_fn=lambda pn, dt: "NOT VALID JSON",
            falsifier=StubFalsifier(),
            max_total_rounds=1,
        )
        state = load_state(tmp_path / ".code-forge" / "state.json")
        infra = [f for f in state.findings if f.source == "INFRA"]
        assert len(infra) >= 1
        for f in infra:
            assert f.disposition.value == "CONFIRMED"
            assert "schema-fail" in f.fingerprint
