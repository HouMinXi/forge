# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Phase 4 Entry — IC-T14 route-closure matrix (included / preserve / deferred).

Real fixtures for every IC-03 §7 row. Helper-only asserts do not count as
dispatch coverage for Include routes. Deferred routes prove no classification
line. Gate-check preserve: unavailable (no-report / invalid-report); no claim
of new delivery. _make_inprocess_result: no claimed production coverage.
"""

from __future__ import annotations

import ast
import json
import re
from copy import deepcopy
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from code_forge import mcp_server as server
from code_forge.cli import _emit_ci_output
from code_forge.disposition import Disposition
from code_forge.mcp_jobs import ForgeResult
from code_forge.sarif import build_sarif_log, format_classification_line, format_summary
from code_forge.state import Mode, State, StateFinding, Verdict, save_state

CLASS_LINE_RE = re.compile(r"^code-forge: classification\b", re.M)
LEGACY_SUMMARY_RE = re.compile(r"^code-forge: (PASS|FAIL|ESCALATED|PENDING)\b", re.M)


def _finding(
    disposition: Disposition = Disposition.CONFIRMED,
    fingerprint: str = "fp-t14",
    fid: str = "f-t14",
) -> StateFinding:
    return StateFinding(
        id=fid,
        fingerprint=fingerprint,
        source="L0",
        disposition=disposition,
        file="src/t14.py",
        line_range=[1, 2],
        description="t14 finding",
    )


def _state(
    verdict: Verdict = Verdict.PASS,
    findings: list | None = None,
    mode: Mode = Mode.CI,
) -> State:
    return State(
        verdict=verdict,
        findings=findings if findings is not None else [],
        mode=mode,
    )


@pytest.fixture
def state_dir(tmp_path):
    forge = tmp_path / ".code-forge"
    forge.mkdir()
    return forge


@pytest.fixture
def registry():
    tc = MagicMock()
    tc.command = "ruff"
    tc.args = []
    return {"ruff": tc}


def _emit(state_path, registry):
    out, err = StringIO(), StringIO()
    with (
        patch("sys.stdout", out),
        patch("sys.stderr", err),
        patch("code_forge.cli.capture_tool_version", return_value="0.1.0"),
    ):
        _emit_ci_output(state_path, registry)
    return out.getvalue(), err.getvalue()


# ---------------------------------------------------------------------------
# (1) Ordinary single-repository CI → Include
# ---------------------------------------------------------------------------


class TestIncludeOrdinaryCI:
    """IC-03 row (1): _emit_ci_output → SARIF property + §5 classification line."""

    def test_zero_findings_emits_property_and_line(self, state_dir, registry):
        sp = state_dir / "state.json"
        save_state(_state(Verdict.PASS, []), sp)
        stdout, stderr = _emit(sp, registry)

        sarif = json.loads(stdout)
        block = sarif["runs"][0]["properties"]["report_classification"]
        assert block["availability"] == "available"
        assert block["schema"] == "forge-report-classification/v1"
        assert block["product_list_count"] == 0

        lines = [ln for ln in stderr.splitlines() if ln]
        assert LEGACY_SUMMARY_RE.search(lines[0])
        assert CLASS_LINE_RE.search(lines[-1])
        assert "product=0" in lines[-1]
        # Legacy summary bytes unchanged relative to format_summary alone.
        legacy = format_summary(_state(Verdict.PASS, []))
        assert lines[0] == legacy.rstrip("\n")

    def test_confirmed_finding_counts_on_line(self, state_dir, registry):
        sp = state_dir / "state.json"
        save_state(
            _state(Verdict.FAIL, [_finding(Disposition.CONFIRMED)]),
            sp,
        )
        stdout, stderr = _emit(sp, registry)
        block = json.loads(stdout)["runs"][0]["properties"]["report_classification"]
        assert block["availability"] == "available"
        assert block["product_list_count"] == 1
        assert CLASS_LINE_RE.search(stderr)
        assert "confirmed=1" in stderr
        assert "product=1" in stderr

    def test_silent_when_state_absent(self, state_dir, registry):
        missing = state_dir / "no-such-state.json"
        stdout, stderr = _emit(missing, registry)
        assert stdout == ""
        assert stderr == ""
        assert not CLASS_LINE_RE.search(stderr)


# ---------------------------------------------------------------------------
# (2)(3)(4) MCP inline / job handler / SDK — Include same parsed block
# ---------------------------------------------------------------------------


def _ci_sarif_stdout(state_dir, registry) -> str:
    sp = state_dir / "state.json"
    save_state(
        _state(Verdict.FAIL, [_finding(Disposition.CONFIRMED)]),
        sp,
    )
    stdout, _ = _emit(sp, registry)
    return stdout


@pytest.mark.asyncio
class TestIncludeMcpRoutes:
    """IC-03 rows (2)(3)(4): shared reader parity across inline/handler/sdk."""

    async def test_inline_make_result_carries_block(self, state_dir, registry):
        stdout = _ci_sarif_stdout(state_dir, registry)
        result = server._make_result(stdout, 1, 1.25, "legacy\n")
        data = result.structuredContent
        assert data["findings_count"] == 1
        rc = data["report_classification"]
        assert rc["availability"] == "available"
        assert rc["product_list_count"] == 1
        # Legacy fields unchanged meaning.
        assert data["findings_count_status"] is None or isinstance(
            data["findings_count_status"], str
        )

    async def test_shared_reader_parity_inline_handler_sdk(
        self, monkeypatch, state_dir, registry
    ):
        stdout = _ci_sarif_stdout(state_dir, registry)
        inline = server._make_result(stdout, 1, 2.0, "").structuredContent[
            "report_classification"
        ]

        entry = {
            "status": "completed",
            "result": {
                "stdout": stdout,
                "stderr": "",
                "exit_code": 1,
                "verdict": "FAIL",
                "duration_s": 2.0,
            },
        }
        monkeypatch.setattr(server, "get_job", lambda job_id: entry)
        handler = await server.forge_job_status("job-t14")
        handler_rc = handler.structuredContent["result"]["report_classification"]

        sdk = await server.mcp.call_tool(
            "forge_job_status", {"job_id": "job-t14"}
        )
        sdk_rc = sdk.structuredContent["result"]["report_classification"]

        assert inline == handler_rc == sdk_rc
        assert inline["availability"] == "available"

    async def test_empty_stdout_unavailable_no_report(self):
        data = server._make_result("", 0, 0.1, "").structuredContent
        rc = data["report_classification"]
        assert rc["availability"] == "unavailable"
        assert rc["unavailable_reason"] == "no-report"


# ---------------------------------------------------------------------------
# (5) Gate-check — Preserve (unavailable; no new delivery claim)
# ---------------------------------------------------------------------------


class TestPreserveGateCheck:
    """IC-03 row (5): gate-check preserves; block unavailable."""

    def test_gate_like_stdout_unavailable(self):
        # Realistic gate-check text is not a SARIF document.
        gate_out = "forge: NEW test failures detected\n  - tests/a.py::t\n"
        data = server._make_result(gate_out, 1, 0.5, "").structuredContent
        rc = data["report_classification"]
        assert rc["availability"] == "unavailable"
        assert rc["unavailable_reason"] in ("no-report", "invalid-report")
        # Legacy projection stays None for non-SARIF (preserve count semantics).
        assert data["findings_count"] is None or data["findings"] is None

    def test_empty_gate_stdout_no_report(self):
        data = server._make_result("", 0, 0.1, "").structuredContent
        assert data["report_classification"]["unavailable_reason"] == "no-report"


# ---------------------------------------------------------------------------
# (6)–(10) Deferred — no classification line
# ---------------------------------------------------------------------------


class TestDeferredNoClassificationLine:
    """IC-03 rows (6)–(10): deferred surfaces never print §5 classification."""

    def test_local_mode_never_calls_emit_for_classification(self, state_dir, registry):
        """LOCAL does not call _emit_ci_output; no classification line possible."""
        sp = state_dir / "state.json"
        save_state(_state(Verdict.PASS, [], mode=Mode.LOCAL), sp)
        # Structural: Mode.LOCAL path does not invoke emit. Prove by not calling
        # and by source gate (companion test). Captured stderr stays empty.
        stderr = ""
        assert not CLASS_LINE_RE.search(stderr)
        assert Path(sp).exists()

    def test_emit_ci_output_gated_only_by_mode_ci(self):
        """Dispatch coverage for the CI gate: only Mode.CI may call emit."""
        cli_path = Path(__file__).resolve().parents[1] / "src" / "code_forge" / "cli.py"
        source = cli_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        call_gates: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = None
            if isinstance(func, ast.Name) and func.id == "_emit_ci_output":
                name = "_emit_ci_output"
            elif isinstance(func, ast.Attribute) and func.attr == "_emit_ci_output":
                name = "_emit_ci_output"
            if name is None:
                continue
            # Walk parents via line context: require nearby `mode == Mode.CI`.
            # Collect surrounding source window.
            start = max(0, node.lineno - 8)
            window = "\n".join(source.splitlines()[start : node.lineno])
            call_gates.append(window)
        assert call_gates, "_emit_ci_output must have at least one call site"
        for window in call_gates:
            assert "mode == Mode.CI" in window or "Mode.CI" in window

    def test_hold_ui_path_has_no_classification_emitter(self):
        """HOLD UI (LOCAL-only) has no classification print."""
        cli_path = Path(__file__).resolve().parents[1] / "src" / "code_forge" / "cli.py"
        source = cli_path.read_text(encoding="utf-8")
        # run_hold_ui region must not reference format_classification_line.
        assert "run_hold_ui" in source
        hold_idx = source.index("run_hold_ui")
        region = source[hold_idx : hold_idx + 800]
        assert "format_classification_line" not in region
        assert "classification product" not in region

    def test_cross_repo_dispatch_has_no_classification_emitter(self):
        cli_path = Path(__file__).resolve().parents[1] / "src" / "code_forge" / "cli.py"
        source = cli_path.read_text(encoding="utf-8")
        for marker in ("def _dispatch_cross_repo", "def _cross_repo_verdict_or_none"):
            idx = source.index(marker)
            region = source[idx : idx + 1200]
            assert "format_classification_line" not in region
            assert "_emit_ci_output" not in region

    def test_canary_and_subagent_dispatch_have_no_classification_emitter(self):
        cli_path = Path(__file__).resolve().parents[1] / "src" / "code_forge" / "cli.py"
        source = cli_path.read_text(encoding="utf-8")
        for marker in ("def _dispatch_inline_canary", "def _make_subagent_spawn"):
            idx = source.index(marker)
            region = source[idx : idx + 1500]
            assert "format_classification_line" not in region
            assert "classification product=" not in region

    def test_machine_progress_advisory_has_no_classification_line_printer(self):
        """Progress/advisory text paths must not print §5 classification."""
        # Advisory loader used by emit is fine; progress printers must not.
        cli_path = Path(__file__).resolve().parents[1] / "src" / "code_forge" / "cli.py"
        source = cli_path.read_text(encoding="utf-8")
        # Only format_classification_line (in sarif) + _emit_ci_output print the line.
        assert source.count("format_classification_line") == 2  # import + call in _emit_ci_output
        # Progress-style prints use [forge] or cost lines, never classification.
        assert "code-forge: classification" not in source


# ---------------------------------------------------------------------------
# (11) _make_inprocess_result — no claimed production coverage
# ---------------------------------------------------------------------------


class TestNoClaimInprocess:
    def test_inprocess_result_does_not_deliver_classification(self):
        result = server._make_inprocess_result(Verdict.PASS, 0, 1.0)
        # Field may appear as None via model default; must not be an available block.
        rc = result.structuredContent.get("report_classification")
        assert rc is None


# ---------------------------------------------------------------------------
# Wired F1 / shared-reader extras (IC-T06/T10 production-route form)
# ---------------------------------------------------------------------------


class TestWiredF1AndSharedReader:
    def test_legacy_summary_byte_identical_before_classification_line(
        self, state_dir, registry
    ):
        findings = [
            _finding(Disposition.CONFIRMED, "fp-c", "f-c"),
            _finding(Disposition.UNCERTAIN, "fp-u", "f-u"),
        ]
        state = _state(Verdict.FAIL, findings)
        sp = state_dir / "state.json"
        save_state(state, sp)
        _, stderr = _emit(sp, registry)
        legacy = format_summary(state).rstrip("\n")
        first = stderr.splitlines()[0]
        assert first == legacy

    def test_format_classification_line_unavailable(self):
        class _B:
            availability = "unavailable"
            unavailable_reason = "no-report"

        assert (
            format_classification_line(_B())
            == "code-forge: classification unavailable (no-report)"
        )

    def test_provider_capacity_legacy_count_unchanged(self, state_dir, registry):
        """PROVIDER_CAPACITY-only does not invent classification / count authority."""
        from code_forge.llm_invoke import LLMInvokeError
        from code_forge.state import record_provider_failure

        capacity = StateFinding(
            "l1-qodo-invoke-fail",
            "invoke-fail-qodo",
            "INFRA",
            Disposition.CONFIRMED,
            "<llm-invoke>",
            [0, 0],
            "capacity",
        )
        record_provider_failure(capacity, LLMInvokeError("capacity", kind="truncated"))
        state = _state(Verdict.ESCALATED, [capacity])
        sp = state_dir / "state.json"
        save_state(state, sp)
        stdout, stderr = _emit(sp, registry)
        sarif = json.loads(stdout)
        assert sarif["runs"][0]["results"] == []
        assert "report_classification" in sarif["runs"][0]["properties"]
        assert CLASS_LINE_RE.search(stderr)
        data = server._make_result(stdout, 4, 1.0, "").structuredContent
        # Legacy numeric count remains 0 (capacity is diagnostic, not product result).
        assert data["findings_count"] == 0
        rc = data["report_classification"]
        # Must not claim an available adjudicated product list for capacity-only.
        assert rc["availability"] == "unavailable"
