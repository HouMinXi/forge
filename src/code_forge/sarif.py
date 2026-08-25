# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""LAYER0-07: SARIF 2.1.0 emission for CI mode.

Pure data transformation: State + tool_versions -> SARIF log dict.
Caller (cli.py CI path) handles I/O.
"""
from __future__ import annotations

from typing import Any, Optional

from .basis import derive_basis
from .disposition import Disposition
from .manifest import EnvManifest, ManifestTier
from .state import (
    State,
    StateFinding,
    Verdict,
    Mode,
    PassOutcome,
    derive_pass_outcomes,
    _PASS_NAMES,
)


SARIF_VERSION = "2.1.0"
SARIF_SCHEMA_URI = "https://json.schemastore.org/sarif-2.1.0.json"


# Disposition -> SARIF level. DISMISSED + FIXED use "note" (lowest severity)
# because they are emitted-but-suppressed per LAYER0-07; the suppressions
# array does the actual non-blocking signaling.
DISPOSITION_TO_LEVEL: dict[Disposition, str] = {
    Disposition.CONFIRMED: "error",
    Disposition.UNCERTAIN: "warning",
    Disposition.DISMISSED: "note",
    Disposition.FIXED: "note",
    Disposition.STYLE: "note",
}


def _suppressions_for(disposition: Disposition) -> Optional[list[dict[str, Any]]]:
    """Return suppressions array or None to omit.

    Disposition -> suppressions array. CONFIRMED + UNCERTAIN have no
    suppressions (raw, blocking-relevant signal). DISMISSED + FIXED carry
    kind=external + kind=inSource respectively per LAYER0-07.
    Explicit dispatch on all 4 known states + ValueError default.
    Silent None on unknown disposition (e.g., enum gained 5th state) would
    emit wrong SARIF; loud raise surfaces the issue at deploy time.
    """
    if disposition in (Disposition.CONFIRMED, Disposition.UNCERTAIN):
        return None
    if disposition == Disposition.DISMISSED:
        return [{"kind": "external"}]
    if disposition == Disposition.FIXED:
        return [{
            "kind": "inSource",
            "properties": {"fix_commit": None},
        }]
    raise ValueError(
        "unknown Disposition %r; sarif.py mapping table needs update"
        % disposition
    )


def build_sarif_log(
    state: State,
    tool_versions: dict[str, str],
    forge_version: str,
    backend_name: Optional[str] = None,
    backend_model: Optional[str] = None,
    advisories: Optional[list] = None,
    manifest: Optional[EnvManifest | dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build SARIF 2.1.0 log dict.

    When backend_name is provided and at least one review pass ran
    (cost_passes > 0), a tokenCost property bag is attached to the run.

    When advisories is non-empty, an "advisories" key is added to the
    run's properties (not results[], since AdvisoryFinding is a
    different shape from StateFinding).
    CLI backends pass backend_name=None so tokenCost is omitted.

    Raises:
        ValueError: state.verdict is PENDING and mode is LOCAL -- a
            local run enters the interactive HOLD UX instead, so a
            PENDING state reaching the SARIF writer is a caller bug.
            CI never enters HOLD (GATE-01b) but CAN legitimately finish
            PENDING with UNCERTAIN findings: there is no human at the
            keyboard, so the findings land in state.json.
    """
    if state.verdict == Verdict.PENDING and state.mode == Mode.LOCAL:
        raise ValueError(
            "build_sarif_log called with PENDING verdict on a LOCAL "
            "run; HOLD UX should have resolved it. Caller bug."
        )
    if manifest is None and getattr(state, "env_manifest", None) is not None:
        manifest = state.env_manifest
    run = _build_run(state, tool_versions, forge_version, manifest=manifest)
    if backend_name is not None and state.cost_passes > 0:
        run.setdefault("properties", {})["tokenCost"] = {
            "inputTokens": state.cost_total_input,
            "outputTokens": state.cost_total_output,
            "cachedTokens": state.cost_total_cached,
            # totalTokens deliberately excludes cachedTokens: it tracks
            # what the caller paid prefill for, not the full prompt size.
            "totalTokens": state.cost_total_input + state.cost_total_output,
            "backend": backend_name,
            "model": backend_model or "",
            "passes": state.cost_passes,
            "durationSeconds": round(state.cost_total_duration, 1),
        }
    if advisories:
        run.setdefault("properties", {})["advisories"] = [
            {
                "id": a.id,
                "axis": a.axis,
                "file": a.file,
                "lineRange": a.line_range,
                "description": a.description,
                "attribution": a.attribution,
            }
            for a in advisories
        ]
    if manifest is not None:
        manifest_dict = (
            manifest.to_dict() if isinstance(manifest, EnvManifest) else manifest
        )
        run.setdefault("properties", {})["manifest"] = manifest_dict
    return {
        "$schema": SARIF_SCHEMA_URI,
        "version": SARIF_VERSION,
        "runs": [run],
    }


def _build_run(
    state: State,
    tool_versions: dict[str, str],
    forge_version: str,
    manifest: Optional[EnvManifest | dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build SARIF run dict.

    tool.driver.rules is intentionally NOT populated in v2.0.
    Rationale: v2.0 fingerprints are sha256(tool:file:line:rule_id)[:16]
    placeholders (Phase 3 replaces with semantic_hash). Generating rule
    definitions from opaque hashes adds JSON noise without integrator
    value. Integrators that need rule[] can construct from results;
    v2.x adds rules[] when fingerprint generation evolves (Phase 3).
    Documented as known v2.0 limitation in Out of Scope.
    """
    if manifest is not None and isinstance(manifest, EnvManifest):
        manifest_tier = manifest.tier
    elif manifest is not None and isinstance(manifest, dict):
        try:
            manifest_tier = ManifestTier(manifest.get("tier", "declared"))
        except (ValueError, KeyError):
            manifest_tier = ManifestTier.DECLARED
    else:
        manifest_tier = ManifestTier.DECLARED
    rounds = max(1, state.round)
    return {
        "tool": {
            "driver": {
                "name": "code-forge",
                "semanticVersion": _build_semantic_version(
                    forge_version, tool_versions
                ),
                "informationUri": "https://github.com/HouMinXi/code-forge",
            },
        },
        "results": [
            _finding_to_result(f, convergence_rounds=rounds, manifest_tier=manifest_tier)
            for f in state.findings
        ],
    }


def _build_semantic_version(
    forge_version: str,
    tool_versions: dict[str, str],
) -> str:
    """LAYER0-07: 'code-forge <version> [<tool>=<ver> ...]' for reproducibility.

    Sorted tool list -> deterministic output for byte-equality testing.

    Tool names MUST be alphanumeric + dash/underscore (matches Phase 1
    registry.py validation regex). Names containing `=` or `]` would
    corrupt the format string -- pre-validated upstream by registry
    loader; this builder trusts the input.
    """
    tools_str = " ".join(
        "%s=%s" % (t, v) for t, v in sorted(tool_versions.items())
    )
    if tools_str:
        return "code-forge %s [%s]" % (forge_version, tools_str)
    return "code-forge %s []" % forge_version


def _finding_to_result(
    finding: StateFinding,
    convergence_rounds: int = 1,
    manifest_tier: ManifestTier = ManifestTier.DECLARED,
) -> dict[str, Any]:
    """Convert StateFinding -> SARIF result dict."""
    props = _build_properties(
        finding,
        convergence_rounds=convergence_rounds,
        manifest_tier=manifest_tier,
    )
    level = DISPOSITION_TO_LEVEL[finding.disposition]
    if props.get("env_capped") and level == "error":
        level = "warning"
    result: dict[str, Any] = {
        "ruleId": finding.fingerprint,
        "level": level,
        "message": {"text": finding.description},
        "locations": [_build_location(finding)],
    }
    suppressions = _suppressions_for(finding.disposition)
    if suppressions is not None:
        result["suppressions"] = suppressions
    result["properties"] = props
    return result


def _build_location(finding: StateFinding) -> dict[str, Any]:
    """Build SARIF physicalLocation.

    Bounds-check line_range. Production line_range is always a 2-element
    list (02-02 _default_l0_runner sets [f.line, f.end_line]; snapshot
    reload preserves list[int]). line_range values are 1-based (per
    parsers/base.py Finding.line definition). SARIF spec uses 1-based
    (region.startLine >= 1), so values pass through directly.

    Defensive guard handles malformed state.json (corrupted file, partial
    write, future schema change): empty -> startLine=1 endLine=1;
    single-element -> endLine mirrors startLine; >2 elements -> first
    two used, extras silently ignored (upstream schema drift case).
    """
    line_range = finding.line_range
    if not line_range:
        start = end = 1
    elif len(line_range) == 1:
        start = end = line_range[0]
    else:
        start = line_range[0]
        end = line_range[1]
    return {
        "physicalLocation": {
            "artifactLocation": {"uri": finding.file},
            "region": {
                "startLine": start,
                "endLine": end,
            },
        },
    }


def _build_properties(
    finding: StateFinding,
    convergence_rounds: int = 1,
    manifest_tier: ManifestTier = ManifestTier.DECLARED,
) -> dict[str, Any]:
    """Optional fields (anchor, evidence_files, error, basis) -> properties dict.

    Absent fields are OMITTED, not emitted as null. Keeps SARIF compact
    for integrators that pretty-print.
    """
    props: dict[str, Any] = {}
    if finding.anchor is not None:
        props["anchor"] = finding.anchor
    if finding.evidence_files is not None:
        props["evidence_files"] = finding.evidence_files
    if finding.error is not None:
        props["error"] = finding.error
    props["source"] = finding.source
    basis = derive_basis(
        finding, convergence_rounds=convergence_rounds,
        manifest_tier=manifest_tier,
    )
    props["basis"] = basis.to_dict()
    if basis.not_verified_against_declared_env:
        props["env_capped"] = True
    return props


def _count_pass_outcomes(
    l1_findings: list[StateFinding],
) -> tuple[int, int]:
    """Return (completed, total) by deriving from findings.

    Calls derive_pass_outcomes (same logic as receipt.py).
    Empty findings -> (3,3) all-completed (clean run).
    """
    pass_outcomes = derive_pass_outcomes(l1_findings)
    completed = sum(
        1 for v in pass_outcomes.values()
        if v == PassOutcome.COMPLETED
    )
    return (completed, len(_PASS_NAMES))


def format_summary(
    state: State,
    advisory_count: int = 0,
    manifest: Optional[EnvManifest | ManifestTier | str | dict[str, Any]] = None,
) -> str:
    """One-line stderr summary per LAYER0-07.

    Format matches regex:
      ^code-forge: (PASS|FAIL|ESCALATED|PENDING) findings=\\d+ confirmed=\\d+
      uncertain=\\d+ dismissed=\\d+ fixed=\\d+( infra=\\d+)?( advisory=\\d+)?$

    LOCAL PENDING is rejected (the HOLD UX resolves it; caller guards).
    CI PENDING is legitimate: UNCERTAIN findings with no human at the
    keyboard (GATE-01b).
    """
    if state.verdict == Verdict.PENDING and state.mode == Mode.LOCAL:
        raise ValueError(
            "format_summary called with PENDING verdict on a LOCAL "
            "run; HOLD UX should have resolved it. Caller bug."
        )
    counts = {
        Disposition.CONFIRMED: 0,
        Disposition.UNCERTAIN: 0,
        Disposition.DISMISSED: 0,
        Disposition.FIXED: 0,
    }
    infra = 0
    for f in state.findings:
        counts[f.disposition] += 1
        if f.source == "INFRA":
            infra += 1
    total = len(state.findings)
    line = (
        "code-forge: %s findings=%d confirmed=%d uncertain=%d "
        "dismissed=%d fixed=%d" % (
            state.verdict.value, total,
            counts[Disposition.CONFIRMED],
            counts[Disposition.UNCERTAIN],
            counts[Disposition.DISMISSED],
            counts[Disposition.FIXED],
        )
    )
    if infra:
        line += " infra=%d" % infra
    if advisory_count:
        line += " advisory=%d" % advisory_count
    completed, total = _count_pass_outcomes(state.findings)
    if completed < total:
        line += " passes=%d/%d" % (completed, total)
    if manifest is None and getattr(state, "env_manifest", None) is not None:
        manifest = state.env_manifest
    if manifest is not None:
        if isinstance(manifest, EnvManifest):
            tier_value = manifest.tier.value
        elif isinstance(manifest, ManifestTier):
            tier_value = manifest.value
        elif isinstance(manifest, dict):
            tier_value = manifest.get("tier", "declared")
        else:
            tier_value = str(manifest)
        line += " [manifest: %s]" % tier_value
    return line
