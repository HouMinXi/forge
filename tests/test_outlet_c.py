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

import pytest

from code_forge.baseline import ResolvedReview
from code_forge.falsify import StubFalsifier
from code_forge.llm_invoke import LLMResult, Usage
from code_forge.outlet_c import run_outlet_c
from code_forge.state import Verdict, load_state, validate_round_history
from code_forge.verify import parse_diff_files, run_verify

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
        code_excerpts = [
            {
                "file": "test.py",
                "start_line": 1,
                "end_line": 4,
                "content": _POST_IMAGE_CONTENT,
            }
        ]
    return json.dumps(
        {
            "findings": findings,
            "code_excerpts": code_excerpts,
        }
    )


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
            spawn_fn=lambda pn, dt: json.dumps(
                {
                    "findings": [],
                    "no_excerpts": True,
                }
            ),
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
            spawn_fn=lambda pn, dt: json.dumps(
                {
                    "findings": [],
                    "code_excerpts": [],
                }
            ),
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
                return json.dumps(
                    {
                        "findings": [
                            {
                                "file": "test.py",
                                "line": 2,
                                "severity": "P1",
                                "description": "bug-%d" % calls["n"],
                                "excerpt": _POST_IMAGE_CONTENT.splitlines(keepends=True)[1],
                            }
                        ],
                        "code_excerpts": [
                            {
                                "file": "test.py",
                                "start_line": 1,
                                "end_line": 4,
                                "content": _POST_IMAGE_CONTENT,
                            }
                        ],
                    }
                )
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
            2026,
            5,
            28,
            10,
            0,
            0,
            tzinfo=datetime.UTC,
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
    return json.dumps(
        {
            "findings": [],
            "code_excerpts": [
                {
                    "file": "test.py",
                    "start_line": 1,
                    "end_line": 4,
                    "content": "def f():\n    x = 1\n    y = 2\n    return 1\n",
                }
            ],
        }
    )


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
        _prompts = []

        with patch("code_forge.llm_invoke.llm_invoke") as mock_llm:
            mock_llm.return_value = _make_llm_result()

            def _spawn(pass_name, diff_text):
                from code_forge.cli import _make_subagent_spawn

                # build a real spawn closure and call llm_invoke with it
                spawn = _make_subagent_spawn(backend=None, conv_digest="", post_image="")
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
            role_n = next((r for r in _ROLES if r in prompt_n), None)
            if role_n is not None:
                # Role from call N must not bleed into call N+1
                assert role_n not in prompt_n1, "Role '%s' from call %d leaked into call %d prompt" % (
                    role_n,
                    i,
                    i + 1,
                )


class TestCriteriaPayload:
    """SC2: prompt contains diff + role, no session context."""

    def test_prompt_contains_diff_and_role(self, tmp_path):
        """Each llm_invoke call must include 'Diff:' and the diff text."""
        with patch("code_forge.llm_invoke.llm_invoke") as mock_llm:
            mock_llm.return_value = _make_llm_result()

            from code_forge.cli import _make_subagent_spawn

            spawn = _make_subagent_spawn(backend=None, conv_digest="", post_image="")

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

            spawn = _make_subagent_spawn(backend=None, conv_digest="", post_image="")

            run_outlet_c(
                resolved_review=_resolved_with_diff(),
                source_hash=_source_hash(),
                cwd=tmp_path,
                spawn_fn=spawn,
                falsifier=StubFalsifier(),
                max_total_rounds=20,
            )

        _FORBIDDEN = [
            "Human:",
            "Assistant:",
            "previous message",
            "I think",
            "let me",
            "conversation",
        ]
        for call in mock_llm.call_args_list:
            prompt = call[0][0]
            for marker in _FORBIDDEN:
                assert marker not in prompt, (
                    f"Session context marker '{marker}' found in reviewer prompt"
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
            content = json.dumps(
                {
                    "findings": [
                        {
                            "file": "test.py",
                            "line": 2,
                            "severity": "P3",
                            "description": bug,
                        }
                    ],
                    "code_excerpts": [
                        {
                            "file": "test.py",
                            "start_line": 1,
                            "end_line": 4,
                            "content": "def f():\n    x = 1\n    y = 2\n    return 1\n",
                        }
                    ],
                }
            )
            return LLMResult(content=content, usage=Usage(), duration_s=0.1)

        with patch("code_forge.llm_invoke.llm_invoke") as mock_llm:
            mock_llm.side_effect = _side_effect

            from code_forge.cli import _make_subagent_spawn

            spawn = _make_subagent_spawn(backend=None, conv_digest="", post_image="")

            run_outlet_c(
                resolved_review=_resolved_with_diff(),
                source_hash=_source_hash(),
                cwd=tmp_path,
                spawn_fn=spawn,
                falsifier=StubFalsifier(),
                max_total_rounds=20,
            )

        calls = mock_llm.call_args_list
        # Each call's prompt must not contain the PREVIOUS call's unique bug string
        for i in range(1, len(calls)):
            prompt = calls[i][0][0]
            prev_bug = _UNIQUE_BUGS[(i - 1) % len(_UNIQUE_BUGS)]
            assert prev_bug not in prompt, "Bug from call %d ('%s') leaked into call %d prompt" % (
                i - 1,
                prev_bug,
                i,
            )


class TestThresholdThreading:
    """clean_round_threshold threaded to StateMachine."""

    @pytest.mark.parametrize("requested_threshold, expected_rounds", [(2, 3), (4, 4)])
    def test_threshold_threading(self, tmp_path, requested_threshold, expected_rounds):
        """The receipt floor and a higher requested threshold both reach the public result."""
        calls = []

        def spawn(pass_name, diff_text):
            calls.append((pass_name, diff_text))
            return _valid_reviewer_json()

        result = run_outlet_c(
            resolved_review=_resolved_with_diff(),
            source_hash=_source_hash(),
            cwd=tmp_path,
            spawn_fn=spawn,
            falsifier=StubFalsifier(),
            max_total_rounds=10,
            clean_round_threshold=requested_threshold,
        )
        assert result == Verdict.PASS
        state = load_state(tmp_path / ".code-forge" / "state.json")
        assert state.verdict == Verdict.PASS
        assert state.converged is True
        assert state.consecutive_clean_rounds == expected_rounds
        assert state.round == expected_rounds - 1
        assert calls == [
            (name, _DIFF_TEXT) for name in ("qodo", "expert", "adversarial") * expected_rounds
        ]
        verified = run_verify(
            cwd=tmp_path,
            diff_sha256=_source_hash(),
            diff_files=parse_diff_files(_DIFF_TEXT),
            diff_text=_DIFF_TEXT,
            required_cycles=expected_rounds,
        )
        assert verified.passed, verified.reason


class TestOutletCInfraSourceTagging:
    """F3: outlet_c error-path findings tagged source=INFRA."""

    def test_outlet_c_spawn_fail_tagged_infra(self, tmp_path):
        """Spawn failures retain all three findings and the independent receipt diagnostic."""
        calls = []

        def _raise_spawn(pass_name, diff_text):
            calls.append((pass_name, diff_text))
            raise RuntimeError("spawn exploded")

        result = run_outlet_c(
            resolved_review=_resolved_with_diff(),
            source_hash=_source_hash(),
            cwd=tmp_path,
            spawn_fn=_raise_spawn,
            falsifier=StubFalsifier(),
            max_total_rounds=1,
        )
        assert result == Verdict.FAIL
        state = load_state(tmp_path / ".code-forge" / "state.json")
        assert state.verdict == Verdict.FAIL
        assert state.converged is False
        assert state.round == 0
        assert state.consecutive_clean_rounds == 0
        pass_names = ("qodo", "expert", "adversarial")
        assert calls == [(name, _DIFF_TEXT) for name in pass_names]
        findings = {finding.id: finding for finding in state.findings}
        assert len(state.findings) == 4
        assert set(findings) == {"l1-%s-spawn-fail" % name for name in pass_names} | {"RECEIPT_INVALID"}
        for name in pass_names:
            finding = findings["l1-%s-spawn-fail" % name]
            assert finding.fingerprint == "spawn-fail-%s" % name
            assert finding.source == "INFRA"
            assert finding.disposition.value == "CONFIRMED"
            assert finding.file == "<spawn>"
            assert finding.line_range == [0, 0]
            assert finding.description == "spawn failed: spawn exploded"

        receipt = findings["RECEIPT_INVALID"]
        description = "receipt acceptance: unwitnessed hunk test.py:1-4"
        assert receipt.source == "INFRA"
        assert receipt.disposition.value == "CONFIRMED"
        assert receipt.file == "<receipt-evidence>"
        assert receipt.line_range == [0, 0]
        assert receipt.description == description
        assert receipt.fingerprint == "receipt-" + hashlib.sha256(description.encode()).hexdigest()[:12]
        assert [error for error in state.infra_errors if error.startswith("receipt:")] == [
            "receipt: " + description
        ]

    def test_outlet_c_schema_fail_tagged_infra(self, tmp_path):
        """schema-fail finding has source=INFRA and disposition=CONFIRMED."""
        _result = run_outlet_c(
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

    def test_outlet_c_excerpt_line_count_is_not_confirmed_infra(self, tmp_path):
        """Line-count drift must not masquerade as a dead backend."""
        # A non-empty findings list so the downgrade has something to carry:
        # an empty one satisfies the INFRA assertion vacuously.
        payload = json.dumps(
            {
                "findings": [
                    {
                        "file": "test.py",
                        "line": 2,
                        "severity": "P2",
                        "description": "candidate worth auditing",
                    }
                ],
                "code_excerpts": [
                    {
                        "file": "test.py",
                        "start_line": 1,
                        "end_line": 70,
                        "content": "\n".join(["x"] * 85),
                    }
                ],
            }
        )
        _result = run_outlet_c(
            resolved_review=_resolved_with_diff(),
            source_hash=_source_hash(),
            cwd=tmp_path,
            spawn_fn=lambda pn, dt: payload,
            falsifier=StubFalsifier(),
            max_total_rounds=1,
        )
        state = load_state(tmp_path / ".code-forge" / "state.json")
        confirmed_infra = [
            f
            for f in state.findings
            if f.source == "INFRA"
            and f.disposition.value == "CONFIRMED"
            and "schema-fail" in f.fingerprint
        ]
        assert not confirmed_infra, (
            "rejected evidence must not masquerade as an infrastructure "
            "failure: %s" % [f.description for f in confirmed_infra]
        )
        untrusted = [f for f in state.findings if f.source == "UNTRUSTED"]
        assert untrusted, "the candidate must survive as audit data, not disappear"


# ---------------------------------------------------------------------------
# Phase 24.1-02: real legs wiring tests
# ---------------------------------------------------------------------------


class TestRealLegsWiring:
    """registry, backend, advisory_runners, engine params passed correctly."""

    def test_outlet_c_registry_passed(self, tmp_path):
        """StateMachine receives the real registry dict, not {}."""
        real_registry = {"semgrep": object()}
        with patch("code_forge.outlet_c.StateMachine") as mock_sm:
            mock_sm.return_value.run.return_value = Verdict.PASS
            run_outlet_c(
                resolved_review=_resolved_with_diff(),
                source_hash=_source_hash(),
                cwd=tmp_path,
                spawn_fn=lambda pn, dt: _valid_reviewer_json(),
                falsifier=StubFalsifier(),
                registry=real_registry,
            )
        _, kwargs = mock_sm.call_args
        assert kwargs["registry"] is real_registry

    def test_outlet_c_advisory_runners_passed(self, tmp_path):
        """StateMachine receives the advisory_runners list."""
        mock_runner = object()
        with patch("code_forge.outlet_c.StateMachine") as mock_sm:
            mock_sm.return_value.run.return_value = Verdict.PASS
            run_outlet_c(
                resolved_review=_resolved_with_diff(),
                source_hash=_source_hash(),
                cwd=tmp_path,
                spawn_fn=lambda pn, dt: _valid_reviewer_json(),
                falsifier=StubFalsifier(),
                advisory_runners=[mock_runner],
            )
        _, kwargs = mock_sm.call_args
        assert kwargs["advisory_runners"] == [mock_runner]

    def test_outlet_c_falsifier_gets_backend(self, tmp_path):
        """When falsifier=None, build_falsifier is called with backend=backend."""
        mock_backend = object()
        with (
            patch("code_forge.outlet_c.StateMachine") as mock_sm,
            patch("code_forge.factories.build_falsifier") as mock_bf,
        ):
            mock_sm.return_value.run.return_value = Verdict.PASS
            mock_bf.return_value = StubFalsifier()
            run_outlet_c(
                resolved_review=_resolved_with_diff(),
                source_hash=_source_hash(),
                cwd=tmp_path,
                spawn_fn=lambda pn, dt: _valid_reviewer_json(),
                falsifier=None,
                backend=mock_backend,
            )
        mock_bf.assert_called_once()
        _, kw = mock_bf.call_args
        assert kw.get("backend") is mock_backend

    def test_outlet_c_backward_compat_no_registry(self, tmp_path):
        """Calling without registry param raises no TypeError."""
        result = run_outlet_c(
            resolved_review=_resolved_with_diff(),
            source_hash=_source_hash(),
            cwd=tmp_path,
            spawn_fn=lambda pn, dt: _valid_reviewer_json(),
            falsifier=StubFalsifier(),
            max_total_rounds=4,
        )
        assert result == Verdict.PASS


# ---------------------------------------------------------------------------
# Attempted-evidence forwarding (outlet-c correction)
# ---------------------------------------------------------------------------

_THREE_FILE_DIFF = (
    "diff --git a/file1.py b/file1.py\n"
    "--- a/file1.py\n"
    "+++ b/file1.py\n"
    "@@ -1,2 +1,3 @@\n"
    " x = 1\n"
    " y = 2\n"
    "+z = 3\n"
    "diff --git a/file2.py b/file2.py\n"
    "--- a/file2.py\n"
    "+++ b/file2.py\n"
    "@@ -1,2 +1,3 @@\n"
    " a = 1\n"
    " b = 2\n"
    "+c = 3\n"
    "diff --git a/file3.py b/file3.py\n"
    "--- a/file3.py\n"
    "+++ b/file3.py\n"
    "@@ -1,2 +1,3 @@\n"
    " m = 1\n"
    " n = 2\n"
    "+o = 3\n"
)

_BINARY_DIFF = "diff --git a/image.png b/image.png\nBinary files /dev/null and b/image.png differ\n"


def _attempted_spawn_factory(marker_prefix):
    """Per-pass schema-invalid payloads with distinct markers.

    Each payload also carries a payload-provided pass_name lie that the
    loop must override with its own pass name.
    """

    def _spawn(pass_name, diff_text):
        chunk_tag = "unknown-chunk"
        for fname in ("file1.py", "file2.py", "file3.py", "test.py", "image.png"):
            if fname in diff_text:
                chunk_tag = fname
                break
        return json.dumps(
            {
                "findings": "not-a-list",
                "code_excerpts": [],
                "pass_name": "payload-lie",
                "marker": f"{marker_prefix}-{pass_name}",
                "chunk_tag": chunk_tag,
            }
        )

    return _spawn


def _read_attempted(cwd):
    attempted_dir = cwd / ".code-forge" / "receipts" / "attempted"
    out = {}
    for fp in attempted_dir.glob("*.json"):
        data = json.loads(fp.read_text())
        assert data["attempted"] is True
        out.setdefault(data["pass_name"], []).append(data["payload"])
    return out


class TestAttemptedForwardingUnderThreshold:
    """Under-threshold route: attempted payloads reach real receipts."""

    def test_attempted_reaches_receipts(self, tmp_path, monkeypatch):
        monkeypatch.delenv(
            "FORGE_DIFF_CHUNK_THRESHOLD_KB",
            raising=False,
        )
        result = run_outlet_c(
            resolved_review=_resolved_with_diff(),
            source_hash=_source_hash(),
            cwd=tmp_path,
            spawn_fn=_attempted_spawn_factory("under"),
            falsifier=StubFalsifier(),
            max_total_rounds=1,
        )
        assert result != Verdict.PASS
        by_pass = _read_attempted(tmp_path)
        assert set(by_pass) == {"qodo", "expert", "adversarial"}
        for pname, payloads in by_pass.items():
            assert len(payloads) == 1
            assert payloads[0]["marker"] == f"under-{pname}"
            assert payloads[0]["pass_name"] == pname


class TestAttemptedForwardingSplitFallback:
    """Split-fallback route (binary-only diff over threshold)."""

    def test_binary_diff_attempted_reaches_receipts(
        self,
        tmp_path,
        monkeypatch,
    ):
        monkeypatch.setenv("FORGE_DIFF_CHUNK_THRESHOLD_KB", "0")
        resolved = ResolvedReview(
            source_files=[Path("image.png")],
            baseline_content=None,
            git_diff=_BINARY_DIFF,
            mode_hint="git",
        )
        result = run_outlet_c(
            resolved_review=resolved,
            source_hash=hashlib.sha256(_BINARY_DIFF.encode()).hexdigest(),
            cwd=tmp_path,
            spawn_fn=_attempted_spawn_factory("fallback"),
            falsifier=StubFalsifier(),
            max_total_rounds=1,
        )
        assert result != Verdict.PASS
        by_pass = _read_attempted(tmp_path)
        assert set(by_pass) == {"qodo", "expert", "adversarial"}
        for pname, payloads in by_pass.items():
            assert payloads[0]["marker"] == f"fallback-{pname}"
            assert payloads[0]["pass_name"] == pname


class TestAttemptedForwardingPerFileChunks:
    """Per-file chunk loop: every chunk's attempts land in one list."""

    def test_all_chunks_attempted_collected(
        self,
        tmp_path,
        monkeypatch,
    ):
        monkeypatch.setenv("FORGE_DIFF_CHUNK_THRESHOLD_KB", "0")
        resolved = ResolvedReview(
            source_files=[
                Path("file1.py"),
                Path("file2.py"),
                Path("file3.py"),
            ],
            baseline_content=None,
            git_diff=_THREE_FILE_DIFF,
            mode_hint="git",
        )
        result = run_outlet_c(
            resolved_review=resolved,
            source_hash=hashlib.sha256(_THREE_FILE_DIFF.encode()).hexdigest(),
            cwd=tmp_path,
            spawn_fn=_attempted_spawn_factory("chunks"),
            falsifier=StubFalsifier(),
            max_total_rounds=1,
        )
        assert result != Verdict.PASS
        by_pass = _read_attempted(tmp_path)
        assert set(by_pass) == {"qodo", "expert", "adversarial"}
        for pname, payloads in by_pass.items():
            # 3 chunks x 1 pass entry each.
            assert len(payloads) == 3
            assert {p["chunk_tag"] for p in payloads} == {
                "file1.py",
                "file2.py",
                "file3.py",
            }
            for p in payloads:
                assert p["marker"] == f"chunks-{pname}"
                assert p["pass_name"] == pname


class TestAttemptedNoRetentionAcrossInvocations:
    """A new provider invocation starts with a fresh attempted list."""

    def test_second_run_has_no_first_run_attempts(
        self,
        tmp_path,
        monkeypatch,
    ):
        monkeypatch.delenv(
            "FORGE_DIFF_CHUNK_THRESHOLD_KB",
            raising=False,
        )
        run1 = tmp_path / "run1"
        run1.mkdir()
        run_outlet_c(
            resolved_review=_resolved_with_diff(),
            source_hash=_source_hash(),
            cwd=run1,
            spawn_fn=_attempted_spawn_factory("run1"),
            falsifier=StubFalsifier(),
            max_total_rounds=1,
        )
        run2 = tmp_path / "run2"
        run2.mkdir()
        result = run_outlet_c(
            resolved_review=_resolved_with_diff(),
            source_hash=_source_hash(),
            cwd=run2,
            spawn_fn=_attempted_spawn_factory("run2"),
            falsifier=StubFalsifier(),
            max_total_rounds=1,
        )
        assert result != Verdict.PASS
        by_pass = _read_attempted(run2)
        assert set(by_pass) == {"qodo", "expert", "adversarial"}
        for pname, payloads in by_pass.items():
            assert len(payloads) == 1
            assert payloads[0]["marker"] == f"run2-{pname}"
        texts = [
            fp.read_text() for fp in (run2 / ".code-forge" / "receipts" / "attempted").glob("*.json")
        ]
        assert all("run1" not in t for t in texts)


# ---------------------------------------------------------------------------
# C3 v3 (fix4): rule 2a counts every received reviewer response per pass per
# round at the single _run_chunk site. Chunked Outlet C with two chunks gives
# a count of 2, so a schema-fail decoy in one chunk can no longer earn credit
# by masking a trusted finding from the other chunk (H-1 / P1d).
# Each test uses its own fresh tmp_path state dir (TD-P6-C3f).
# ---------------------------------------------------------------------------

_C3_A_POST = "def a():\n    return 1\n"
_C3_B_POST = "def b():\n    x = 1\n    y = 2\n    return 1\n"
_C3_DIFF_A = (
    "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,2 @@\n def a():\n+    return 1\n"
)
_C3_DIFF_B = (
    "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -1,2 +1,4 @@\n def b():\n"
    "+    x = 1\n+    y = 2\n     return 1\n"
)
_C3_EXC_A = [{"file": "a.py", "start_line": 1, "end_line": 2, "content": _C3_A_POST}]
_C3_EXC_B = [{"file": "b.py", "start_line": 1, "end_line": 4, "content": _C3_B_POST}]
_C3_DEFECT = {
    "file": "b.py",
    "line": 2,
    "severity": "P1",
    "description": "real defect in b.py",
    "excerpt": "    x = 1\n",
}

_C3_P_POST = "def p():\n    return 1\n"
_C3_DIFF_P = (
    "diff --git a/p.py b/p.py\n--- a/p.py\n+++ b/p.py\n@@ -1,1 +1,2 @@\n def p():\n+    return 1\n"
)
_C3_EXC_P = [{"file": "p.py", "start_line": 1, "end_line": 2, "content": _C3_P_POST}]
_C3_DIFF_Q_DEL = (
    "diff --git a/q.py b/q.py\n--- a/q.py\n+++ b/q.py\n@@ -1,3 +1,1 @@\n def q():\n"
    "-    check_auth()\n-    return 0\n"
)
_C3_TRUSTED_P0 = {"file": "q.py", "line": 2, "severity": "P0", "description": "auth check removed"}


def _c3_run_chunked(tmp_path, monkeypatch, files, diff, spawn, *, rounds=3, threshold_kb="0"):
    """Real run_outlet_c over a per-file chunked diff on a fresh state dir."""
    monkeypatch.setenv("FORGE_DIFF_CHUNK_THRESHOLD_KB", threshold_kb)
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    resolved = ResolvedReview(
        source_files=[Path(name) for name in files],
        baseline_content=None,
        git_diff=diff,
        mode_hint="git",
    )
    verdict = run_outlet_c(
        resolved_review=resolved,
        source_hash=hashlib.sha256(diff.encode()).hexdigest(),
        cwd=tmp_path,
        spawn_fn=spawn,
        falsifier=StubFalsifier(),
        max_total_rounds=rounds,
        clean_round_threshold=3,
    )
    return verdict, load_state(tmp_path / ".code-forge" / "state.json")


def _c3_round_of(calls, per_round=6):
    calls["n"] += 1
    return (calls["n"] - 1) // per_round + 1


def _c3_assert_decoy_round_reset(state, companion_id):
    rows = state.round_history
    assert [row["clean_credit_action"] for row in rows] == ["earned", "earned", "reset"]
    latest = rows[-1]
    assert latest["fixpoint"] == "RESET" and latest["clean_rounds_after"] == 0
    comps = [f for f in state.findings if f.id == companion_id and f.source == "UNTRUSTED"]
    assert len(comps) == 1
    fp = comps[0].fingerprint
    # The companion stays in the persisted row: nothing was stripped.
    assert latest["dispositions"][fp] == "UNCERTAIN"
    # Window wiped: the two earned cycles are gone.
    assert state.consecutive_clean_rounds == 0
    assert state.earned_clean_window["cycles"] == []
    assert not any("clean credit preserved" in err for err in state.infra_errors)
    return fp


def test_c3v2_outlet_c_chunked_decoy_resets(tmp_path, monkeypatch):
    """T1 (CR1 dedup probe, run-level): chunk A's schema-fail decoy claims F in
    b.py; chunk B's accepted expert response carries the trusted F. The fold
    keeps the decoy's companion and drops F, but expert received 2 responses in
    the round, so rule 2a makes it ineligible and the round resets."""
    calls = {"n": 0}

    def spawn(pass_name, chunk):
        cycle = _c3_round_of(calls)
        if "b.py" in chunk:
            findings = [_C3_DEFECT] if (cycle == 3 and pass_name == "expert") else []
            return json.dumps({"findings": findings, "code_excerpts": _C3_EXC_B})
        if cycle == 3 and pass_name == "expert":
            return json.dumps({"findings": [_C3_DEFECT], "code_excerpts": "x"})
        return json.dumps({"findings": [], "code_excerpts": _C3_EXC_A})

    verdict, state = _c3_run_chunked(
        tmp_path,
        monkeypatch,
        {"a.py": _C3_A_POST, "b.py": _C3_B_POST},
        _C3_DIFF_A + _C3_DIFF_B,
        spawn,
    )
    assert verdict is not Verdict.PASS and calls["n"] == 18
    comps = [f for f in state.findings if f.source == "UNTRUSTED"]
    assert len(comps) == 1 and comps[0].id.startswith("l1-expert-untrusted-")
    fp = _c3_assert_decoy_round_reset(state, comps[0].id)
    # The trusted copy lost the first-in-wins dedup (pre-existing residual).
    assert [f.source for f in state.findings if f.fingerprint == fp] == ["UNTRUSTED"]
    assert state.round_history[-1]["dispositions"]["schema-fail-expert"] == "CONFIRMED"


def test_c3v2_outlet_c_pure_deletion_chunk_resets_without_belt(tmp_path, monkeypatch):
    """T2 (P1d): chunk B is an accepted, excerpt-exempt pure-deletion response
    with a P0 at F and `code_excerpts: []`. It appends no attempt and no
    excerpt, but the pre-branch site counts it, so adversarial received 2
    responses and the round resets by the count alone (no receipt-diff belt)."""
    from code_forge.reviewer_json import _requires_l1_excerpts

    assert _requires_l1_excerpts(_C3_DIFF_Q_DEL) is False
    calls = {"n": 0}

    def spawn(pass_name, chunk):
        cycle = _c3_round_of(calls)
        if "q.py" in chunk:
            if cycle == 3 and pass_name == "adversarial":
                return json.dumps({"findings": [_C3_TRUSTED_P0], "code_excerpts": []})
            return json.dumps({"findings": [], "code_excerpts": []})
        if cycle == 3 and pass_name == "adversarial":
            decoy = dict(_C3_TRUSTED_P0, severity="P9")
            return json.dumps({"findings": [decoy], "code_excerpts": _C3_EXC_P})
        return json.dumps({"findings": [], "code_excerpts": _C3_EXC_P})

    verdict, state = _c3_run_chunked(
        tmp_path,
        monkeypatch,
        {"p.py": _C3_P_POST, "q.py": "def q():\n"},
        _C3_DIFF_P + _C3_DIFF_Q_DEL,
        spawn,
    )
    assert verdict is not Verdict.PASS and calls["n"] == 18
    comps = [f for f in state.findings if f.source == "UNTRUSTED"]
    assert len(comps) == 1 and comps[0].id.startswith("l1-adversarial-untrusted-")
    fp = _c3_assert_decoy_round_reset(state, comps[0].id)
    assert not [f for f in state.findings if f.source == "L1" and f.fingerprint == fp]


def _c3_counting_call():
    from code_forge.factories import _L1Call

    call = _L1Call(lambda c: None)
    call()
    assert call.reviewer_responses is None
    return call


def test_c3v2_run_chunk_counts_every_outcome_and_sums_chunks(tmp_path, monkeypatch):
    """T8: _run_chunk counts accepted, exempt, schema-fail and non-JSON responses
    once each; chunks sum on one owner; a spawn exception and an ownerless call
    record nothing."""
    from code_forge.outlet_c import _run_chunk

    outcomes = {
        "qodo": json.dumps({"findings": [], "code_excerpts": _C3_EXC_P}),  # accepted
        "expert": json.dumps({"findings": "not a list", "code_excerpts": _C3_EXC_P}),  # schema-fail
        "adversarial": "no json here at all",  # non-JSON
    }
    call = _c3_counting_call()
    attempted: list[dict] = []
    _run_chunk(
        _C3_DIFF_P,
        lambda p, d: outcomes[p],
        ("qodo", "expert", "adversarial"),
        attempted=attempted,
        rejection_state=call,
    )
    assert call.reviewer_responses == {"qodo": 1, "expert": 1, "adversarial": 1}

    # Excerpt-exempt (pure deletion, missing excerpts) still counts 1.
    exempt = _c3_counting_call()
    _run_chunk(
        _C3_DIFF_Q_DEL,
        lambda p, d: json.dumps({"findings": [], "code_excerpts": []}),
        ("qodo",),
        attempted=[],
        rejection_state=exempt,
    )
    assert exempt.reviewer_responses == {"qodo": 1}

    # Two chunks on one owner sum to 2 per pass.
    summed = _c3_counting_call()
    for chunk, exc in ((_C3_DIFF_P, _C3_EXC_P), (_C3_DIFF_A, _C3_EXC_A)):
        _run_chunk(
            chunk,
            lambda p, d, exc=exc: json.dumps({"findings": [], "code_excerpts": exc}),
            ("qodo", "expert"),
            attempted=[],
            rejection_state=summed,
        )
    assert summed.reviewer_responses == {"qodo": 2, "expert": 2}

    # A spawn exception yields no response: nothing recorded for that pass.
    def boom(p, d):
        if p == "expert":
            raise RuntimeError("constructed spawn failure")
        return outcomes["qodo"]

    failed = _c3_counting_call()
    _run_chunk(_C3_DIFF_P, boom, ("qodo", "expert"), attempted=[], rejection_state=failed)
    assert failed.reviewer_responses == {"qodo": 1}

    # No owner: nothing anywhere records (count-without-owner). Every owner
    # used above is a live bystander of this ownerless run and must keep its
    # exact count; the ownerless run itself must still complete.
    _run_chunk(_C3_DIFF_P, lambda p, d: outcomes[p], ("qodo", "expert", "adversarial"))
    assert call.reviewer_responses == {"qodo": 1, "expert": 1, "adversarial": 1}
    assert exempt.reviewer_responses == {"qodo": 1}
    assert summed.reviewer_responses == {"qodo": 2, "expert": 2}
    assert failed.reviewer_responses == {"qodo": 1}


def test_c3v2_run_outlet_c_counts_per_round_sum_and_single_chunk(tmp_path, monkeypatch):
    """T8 (provider level): over the threshold, 2 chunks -> 2 per pass; exactly
    1 chunk over the threshold -> 1; the count restarts every invocation."""
    import code_forge.outlet_c as outlet_c

    captured = {}

    class _Capture:
        def __init__(self, **kw):
            captured.update(kw)

        def run(self):
            return None

    monkeypatch.setattr(outlet_c, "StateMachine", _Capture)
    monkeypatch.setenv("FORGE_DIFF_CHUNK_THRESHOLD_KB", "0")

    def spawn(pass_name, chunk):
        exc = _C3_EXC_A if "a.py" in chunk else _C3_EXC_P
        return json.dumps({"findings": [], "code_excerpts": exc})

    for diff, expected in ((_C3_DIFF_A + _C3_DIFF_P, 2), (_C3_DIFF_P, 1)):
        rr = ResolvedReview(
            source_files=[Path("a.py"), Path("p.py")],
            baseline_content=None,
            git_diff=diff,
            mode_hint="git",
        )
        outlet_c.run_outlet_c(
            resolved_review=rr, source_hash="h", cwd=tmp_path, spawn_fn=spawn, falsifier=StubFalsifier()
        )
        provider = captured["l1_provider"]
        for _ in range(2):
            provider()
            assert provider.reviewer_responses == dict.fromkeys(
                ("qodo", "expert", "adversarial"), expected
            )


@pytest.mark.parametrize("threshold_kb", ["1000", "0"], ids=["unchunked", "single-chunk-over-threshold"])
def test_c3v2_outlet_c_single_response_preserves(tmp_path, monkeypatch, threshold_kb):
    """J-L2n (promoted from the CR2 fix4 probe): the positive Outlet C run-level
    case. One file, so every pass receives exactly one response per round
    (unchunked, or a single chunk over the threshold). Round 3's adversarial
    response is schema-invalid but parses into a dict with a finding at its own
    location; rule 2a sees a count of 1, the companion is stripped from the
    preserved row and the earned window is kept (spec v3 §1.2 eligibility)."""
    calls = {"n": 0}

    def spawn(pass_name, chunk):
        cycle = _c3_round_of(calls, per_round=3)
        if cycle == 3 and pass_name == "adversarial":
            decoy = {
                "file": "p.py",
                "line": 2,
                "severity": "P9",
                "description": "own-location candidate",
                "excerpt": "    return 1\n",
            }
            return json.dumps({"findings": [decoy], "code_excerpts": _C3_EXC_P})
        return json.dumps({"findings": [], "code_excerpts": _C3_EXC_P})

    verdict, state = _c3_run_chunked(
        tmp_path, monkeypatch, {"p.py": _C3_P_POST}, _C3_DIFF_P, spawn, threshold_kb=threshold_kb
    )
    assert verdict is not Verdict.PASS and calls["n"] == 9
    rows = state.round_history
    assert [row["clean_credit_action"] for row in rows] == ["earned", "earned", "preserved"]
    assert rows[-1]["dispositions"]["schema-fail-adversarial"] == "CONFIRMED"
    comps = [f for f in state.findings if f.source == "UNTRUSTED"]
    assert len(comps) == 1 and comps[0].id.startswith("l1-adversarial-untrusted-")
    fp = comps[0].fingerprint
    # Writer strip: the companion is omitted from dispositions, kept in l1_fingerprints.
    assert fp not in rows[-1]["dispositions"]
    assert fp in rows[-1]["l1_fingerprints"]
    assert state.earned_clean_window["cycles"] != []
    validate_round_history(state.round_history, state.round)
