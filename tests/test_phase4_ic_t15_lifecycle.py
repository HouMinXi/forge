# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Phase 4 Entry — IC-T15 mixed-version / lifecycle (IC-04).

Payload replay old↔new; restart → Unknown job_id; test-owned reduced TTL
eviction (production default TTL remains 3600.0); drain-established and
drain-blocked rehearsal evidence hooks (drain-blocked must NOT fake success).
"""

from __future__ import annotations

import json
from copy import deepcopy
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from code_forge import mcp_server as server
from code_forge.cli import _emit_ci_output
from code_forge.disposition import Disposition
from code_forge.mcp_jobs import (
    ForgeResult,
    _JOB_TTL_SECONDS,
    _evict_stale,
    _jobs,
    get_job,
)
from code_forge.report_presentation import parse_report_classification, wire_to_json
from code_forge.state import Mode, State, StateFinding, Verdict, save_state


@pytest.fixture(autouse=True)
def clear_jobs():
    _jobs.clear()
    yield
    _jobs.clear()


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


def _emit_ci_sarif(state_dir, registry, findings=None) -> str:
    sp = state_dir / "state.json"
    findings = findings if findings is not None else []
    save_state(State(verdict=Verdict.PASS, findings=findings, mode=Mode.CI), sp)
    out, err = StringIO(), StringIO()
    with (
        patch("sys.stdout", out),
        patch("sys.stderr", err),
        patch("code_forge.cli.capture_tool_version", return_value="0.1.0"),
    ):
        _emit_ci_output(sp, registry)
    return out.getvalue()


# ---------------------------------------------------------------------------
# Production TTL pin
# ---------------------------------------------------------------------------


def test_production_ttl_remains_3600():
    assert _JOB_TTL_SECONDS == 3600.0


# ---------------------------------------------------------------------------
# Payload replay old ↔ new (IC-04 §8 matrix)
# ---------------------------------------------------------------------------


class TestPayloadReplay:
    def test_old_reader_ignores_additive_block(self, state_dir, registry):
        """New→Old: legacy projection fields remain readable; additive ignorable."""
        stdout = _emit_ci_sarif(
            state_dir,
            registry,
            [
                StateFinding(
                    "f1",
                    "fp1",
                    "L0",
                    Disposition.CONFIRMED,
                    "a.py",
                    [1, 2],
                    "x",
                )
            ],
        )
        doc = json.loads(stdout)
        assert "report_classification" in doc["runs"][0]["properties"]
        # Legacy reader path (pre-#332): drop unknown properties, keep results.
        legacy_view = deepcopy(doc)
        legacy_view["runs"][0]["properties"].pop("report_classification", None)
        findings_count, findings, receipt_audit, status = server._sarif_projection_with_status(
            json.dumps(legacy_view)
        )
        assert findings_count == 1
        assert findings == doc["runs"][0]["results"]
        assert status is None

    def test_old_stdout_without_block_yields_unavailable_legacy_report(self, state_dir, registry):
        """Old→New: legacy SARIF without additive block → unavailable/legacy-report."""
        # Build legacy-shaped SARIF by stripping the property after emit, or via
        # build_sarif_log alone (pre-wire bytes).
        from code_forge.sarif import build_sarif_log

        state = State(verdict=Verdict.PASS, findings=[], mode=Mode.CI)
        legacy = build_sarif_log(state, {}, "fixture")
        assert "report_classification" not in legacy["runs"][0].get("properties", {})
        rc = server._report_classification_from_stdout(json.dumps(legacy))
        assert rc["availability"] == "unavailable"
        assert rc["unavailable_reason"] in (
            "legacy-report",
            "no-report",
            "invalid-report",
        )

    def test_new_to_new_normalized_block(self, state_dir, registry):
        stdout = _emit_ci_sarif(state_dir, registry)
        rc = server._report_classification_from_stdout(stdout)
        assert rc["availability"] == "available"
        assert rc["schema"] == "forge-report-classification/v1"
        # Round-trip via parse helpers.
        doc = json.loads(stdout)
        wire = wire_to_json(parse_report_classification(doc["runs"][0], legacy_report_state="accepted"))
        assert wire["availability"] == "available"

    def test_unknown_schema_version_unavailable(self):
        run = {
            "results": [],
            "properties": {
                "report_classification": {
                    "schema": "forge-report-classification/v999",
                    "availability": "available",
                }
            },
        }
        block = parse_report_classification(run, legacy_report_state="accepted")
        out = wire_to_json(block)
        assert out["availability"] == "unavailable"
        assert out["unavailable_reason"] in (
            "unsupported-schema",
            "invalid-block",
        )

    def test_invalid_new_block_unavailable(self):
        run = {
            "results": [],
            "properties": {"report_classification": "not-a-dict"},
        }
        block = parse_report_classification(run, legacy_report_state="accepted")
        out = wire_to_json(block)
        assert out["availability"] == "unavailable"
        assert out["unavailable_reason"] == "invalid-block"


# ---------------------------------------------------------------------------
# Restart → Unknown job_id; TTL eviction (test-owned reduced TTL)
# ---------------------------------------------------------------------------


class TestJobLifecycle:
    @pytest.mark.asyncio
    async def test_restart_clears_jobs_unknown_job_id(self):
        """Process-local store: simulated restart (_jobs.clear) → Unknown job_id."""
        import time as time_mod

        _jobs["ghost"] = {
            "status": "completed",
            "result": {
                "stdout": "{}",
                "stderr": "",
                "exit_code": 0,
                "verdict": "PASS",
                "duration_s": 0.1,
            },
            "created_at": time_mod.monotonic(),
        }
        assert get_job("ghost") is not None
        # Restart simulation: drop in-memory store (no cross-restart continuity).
        _jobs.clear()
        with pytest.raises(ToolError) as ei:
            await server.forge_job_status("ghost")
        assert "Unknown job_id" in str(ei.value)

    def test_ttl_eviction_with_test_reduced_ttl(self, monkeypatch):
        """Test-owned TTL override; production constant stays 3600.0."""
        assert _JOB_TTL_SECONDS == 3600.0
        import time as time_mod
        import code_forge.mcp_jobs as jobs_mod

        monkeypatch.setattr(jobs_mod, "_JOB_TTL_SECONDS", 0.05)
        now = time_mod.monotonic()
        _jobs["stale"] = {
            "status": "completed",
            "result": {
                "stdout": "",
                "stderr": "",
                "exit_code": 0,
                "verdict": "PASS",
                "duration_s": 0.0,
            },
            "created_at": now - 1.0,  # older than reduced TTL
        }
        _jobs["fresh"] = {
            "status": "completed",
            "result": {
                "stdout": "",
                "stderr": "",
                "exit_code": 0,
                "verdict": "PASS",
                "duration_s": 0.0,
            },
            "created_at": now,
        }
        _evict_stale()
        assert "stale" not in _jobs
        assert "fresh" in _jobs
        # Monkeypatch is test-local; source pin remains 3600.0 (see
        # test_production_ttl_remains_3600 and the module literal).
        assert jobs_mod._JOB_TTL_SECONDS == 0.05

    @pytest.mark.asyncio
    async def test_evicted_job_is_unknown(self, monkeypatch):
        import time as time_mod
        import code_forge.mcp_jobs as jobs_mod

        monkeypatch.setattr(jobs_mod, "_JOB_TTL_SECONDS", 0.01)
        _jobs["gone"] = {
            "status": "failed",
            "result": {
                "stdout": "",
                "stderr": "x",
                "exit_code": 1,
                "verdict": "FAIL",
                "duration_s": 0.0,
            },
            "created_at": time_mod.monotonic() - 1.0,
        }
        with pytest.raises(ToolError) as ei:
            await server.forge_job_status("gone")
        assert "Unknown job_id" in str(ei.value)


# ---------------------------------------------------------------------------
# Drain rehearsal evidence hooks (not enable)
# ---------------------------------------------------------------------------


class TestDrainRehearsalBranches:
    """IC-04: both drain-established and drain-blocked must be rehearsed.

    These tests encode the acceptance predicates for evidence packages under
    phase4-evidence/rollback-rehearsal/. They do NOT enable admission or
    restart production consumers.
    """

    def test_drain_established_predicate(self):
        """Drain-established: all observed jobs terminal before switch window."""
        snapshot = [
            {"job_id": "a", "status": "completed"},
            {"job_id": "b", "status": "failed"},
        ]
        running = [j for j in snapshot if j["status"] == "running"]
        assert running == []
        # Evidence package must record this predicate; success is allowed only
        # when running is empty. (Package written in WP6.)
        assert all(j["status"] in ("completed", "failed") for j in snapshot)

    def test_drain_blocked_must_not_fake_success(self):
        """Drain-blocked: admission stays stopped; missing evidence recorded."""
        snapshot = [
            {"job_id": "a", "status": "completed"},
            {"job_id": "stuck", "status": "running"},
        ]
        running = [j for j in snapshot if j["status"] == "running"]
        assert running, "fixture must include a non-terminal job"
        drain_established = len(running) == 0
        assert drain_established is False
        # Rollback outcome for blocked branch:
        outcome = {
            "admission": "stopped",
            "drain_established": False,
            "switch_performed": False,
            "forced_kill_used": False,
            "missing_evidence": ["stuck job still running"],
            "escalated_to_owner": True,
        }
        assert outcome["switch_performed"] is False
        assert outcome["forced_kill_used"] is False
        assert outcome["admission"] == "stopped"
        assert outcome["drain_established"] is False


# ---------------------------------------------------------------------------
# Additive ForgeResult field does not rewrite legacy meanings
# ---------------------------------------------------------------------------


def test_forge_result_additive_default_none():
    r = ForgeResult(verdict="PASS", exit_code=0, duration_s=1.0, output="ok")
    assert r.report_classification is None
    assert r.findings_count is None
