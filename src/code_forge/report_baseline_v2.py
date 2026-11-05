# SPDX-License-Identifier: Apache-2.0
"""Unwired owned-snapshot baseline factory candidate for #400.

Capturing external inputs/locator and publishing/hook execution remain caller
responsibilities. No filesystem reads, tool recapture or callback execution.
"""

import copy
import json
from dataclasses import dataclass, fields
from typing import Any

from code_forge.report_projection import classify_report_records
from code_forge.report_projection_v2 import classify_report_records_v2
from code_forge.report_presentation import (
    prepare_legacy_report,
    prepare_report_views,
    _legacy_envelope_accepted,
)
from code_forge.report_preparation_v2 import FinalText
from code_forge.sarif import build_sarif_log, format_summary, format_classification_line
from code_forge.state import State, StateFinding, Verdict, Mode, PassOutcome, FindingDiagnosticKind
from code_forge.disposition import Disposition
from code_forge.advisory import AdvisoryFinding
from code_forge.manifest import EnvManifest, ManifestTier
from code_forge.assertion_outcome import AssertionOutcome, AssertionStatus, AssertionStage, AssertionKind


@dataclass(frozen=True)
class RenderingInputs:
    forge_version: str
    tool_versions: dict[str, str]
    advisories: tuple = ()
    backend_name: str | None = None
    backend_model: str | None = None
    manifest: Any = None
    assertion_outcome: Any = None
    # Exact already-resolved absolute locator string, or None. Caller must
    # verify identity at its capture boundary. This conveys no authority.
    resolved_locator: str | None = None


@dataclass(frozen=True)
class SnapshotLimits:
    source_nodes: int
    source_characters: int
    finding_occurrences: int

    def __post_init__(self):
        for value in (self.source_nodes, self.source_characters, self.finding_occurrences):
            if type(value) is not int or value <= 0:
                raise ValueError("snapshot limits must be positive exact integers")


@dataclass(frozen=True)
class OwnedReportPair:
    baseline: FinalText
    projection: Any


_SAFE_ENUM_TYPES = (
    Verdict,
    Mode,
    PassOutcome,
    FindingDiagnosticKind,
    Disposition,
    ManifestTier,
    AssertionStatus,
    AssertionStage,
    AssertionKind,
)


def _preflight(value, limits):
    # Count expanded occurrences, not unique identities: JSON rendering expands
    # alias DAGs even though deepcopy preserves their shared graph. Active-path
    # tracking rejects cycles without rejecting a repeated finding occurrence.
    pending = [(value, False)]
    active = set()
    nodes = 1
    characters = 0
    safe_records = (State, StateFinding, RenderingInputs, AdvisoryFinding, EnvManifest, AssertionOutcome)
    while pending:
        item, leaving = pending.pop()
        if leaving:
            active.remove(id(item))
            continue
        if type(item) is str:
            characters += len(item)
            if characters > limits.source_characters:
                raise ValueError("snapshot character limit")
        elif item is None or type(item) in (bool, int, float, *_SAFE_ENUM_TYPES):
            continue
        else:
            if id(item) in active:
                raise ValueError("cyclic snapshot object")
            if type(item) is dict:
                count = 2 * len(item)
                children = None
            elif type(item) in (list, tuple, set, frozenset):
                count = len(item)
                children = item
            elif type(item) in safe_records:
                attributes = fields(item)
                declared = {field.name for field in attributes}
                instance_values = getattr(item, "__dict__", None)
                if instance_values is not None and (
                    type(instance_values) is not dict
                    or len(instance_values) != len(declared)
                    or set(instance_values) != declared
                ):
                    raise TypeError("unexpected snapshot attributes")
                count = len(attributes)
                children = None
            else:
                raise TypeError("unsupported snapshot object")
            if count > limits.source_nodes - nodes:
                raise ValueError("snapshot node limit")
            nodes += count
            active.add(id(item))
            pending.append((item, True))
            if type(item) is dict:
                pending.extend((child, False) for child in item.keys())
                pending.extend((child, False) for child in item.values())
            elif type(item) in safe_records:
                for field in attributes:
                    value = getattr(item, field.name)
                    if (
                        type(item) is State
                        and field.name == "_legacy_survivor_state"
                        and type(value) is bytes
                    ):
                        # Canonical loader retains this opaque original file.
                        # Exact bytes are immutable, deepcopy reuses identity,
                        # and report builders never serialize/archive this slot.
                        # It counts as a node, not expanded report characters.
                        continue
                    pending.append((value, False))
            else:
                pending.extend((child, False) for child in children)


def _frozen_infra_text(state, locator):
    """Pure counterpart of the existing footer; verified by real-emitter tests.

    Uses only a captured locator string. Does not re-check path existence;
    that real I/O policy stays at the caller's capture/publication boundary.
    """
    if state.verdict == Verdict.PASS or not state.infra_errors:
        return ""
    errors = [" ".join(str(error).split()) for error in state.infra_errors]
    errors = [error for error in errors if error]
    lines = [
        "code-forge: infra: " + (error if len(error) <= 300 else error[:297] + "...")
        for error in errors[:5]
    ]
    extra = max(0, len(errors) - 5)
    if extra or any(len(error) > 300 for error in errors[:5]):
        reason = f"{extra} more" if extra else "truncated"
        location = "state_path unavailable; field=infra_errors"
        if locator is not None:
            location = f"state_path={json.dumps(locator, ensure_ascii=True)}; field=infra_errors"
        detail = f"({reason}; {location})"
        if len(detail) > 300:
            detail = f"({reason}; state_path too long to display; field=infra_errors)"
        lines.append("code-forge: infra: " + detail)
    return "\n".join(lines) + "\n" if lines else ""


@dataclass(frozen=True)
class _BaselineParts:
    text: FinalText
    legacy_summary_final: str
    infra_footer: str


def _build_baseline_parts(state, context):
    document = build_sarif_log(
        state,
        context.tool_versions,
        forge_version=context.forge_version,
        backend_name=context.backend_name,
        backend_model=context.backend_model,
        advisories=list(context.advisories) or None,
        manifest=context.manifest,
        assertion_outcome=context.assertion_outcome,
    )
    summary = format_summary(
        state,
        advisory_count=len(context.advisories),
        manifest=context.manifest,
        assertion_outcome=context.assertion_outcome,
    )
    substrate = prepare_legacy_report(
        document, stdout=json.dumps(document, ensure_ascii=False, allow_nan=False), stderr=summary
    )
    projection = classify_report_records(state) if substrate.sarif_document is not None else None
    prepared = prepare_report_views(substrate, projection)
    stderr = prepared.stderr
    if stderr and not stderr.endswith("\n"):
        stderr += "\n"
    stderr += format_classification_line(prepared.report_classification) + "\n"
    footer = _frozen_infra_text(state, context.resolved_locator)
    stderr += footer
    summary_final = summary + ("\n" if summary and not summary.endswith("\n") else "")
    return _BaselineParts(FinalText(prepared.stdout + "\n", stderr), summary_final, footer)


def _build_baseline(state, context):
    return _build_baseline_parts(state, context).text


def _capture_owned_rendering(state: State, context: RenderingInputs, *, limits: SnapshotLimits):
    """Privately copy once, then give identical State to real v1/v2 builders.

    No external projection/baseline is accepted or paired by token. Copying
    errors and baseline failures propagate before optional classification.
    Captured context is detached with State in one copy. A returned pair does
    not qualify canonical acquisition, stream publication or v2 preparation.
    """
    if (
        type(state) is not State
        or type(context) is not RenderingInputs
        or type(limits) is not SnapshotLimits
    ):
        raise TypeError("expected State, RenderingInputs and SnapshotLimits")
    if type(context.resolved_locator) not in (str, type(None)):
        raise TypeError("locator must be captured string or None")
    if type(state.findings) is not list:
        raise TypeError("findings must be an exact list")
    if len(state.findings) > limits.finding_occurrences:
        raise ValueError("snapshot finding limit")
    _preflight((state, context), limits)
    # Approved enums are trusted immutable value identities. Memoize them
    # explicitly so an instance-level copy callback can never run.
    enum_memo = {id(member): member for enum_type in _SAFE_ENUM_TYPES for member in enum_type}
    owned_state, owned_context = copy.deepcopy((state, context), enum_memo)
    return owned_state, owned_context


def build_owned_report_pair(
    state: State, context: RenderingInputs, *, limits: SnapshotLimits
) -> OwnedReportPair:
    """Compatibility seam for the factory's separately tested baseline/projection pair."""
    owned_state, owned_context = _capture_owned_rendering(state, context, limits=limits)
    baseline = _build_baseline(owned_state, owned_context)
    readable, _ = _legacy_envelope_accepted(baseline.stdout)
    projection = classify_report_records_v2(owned_state) if readable else None
    return OwnedReportPair(baseline, projection)
