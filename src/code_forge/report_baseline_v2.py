# SPDX-License-Identifier: Apache-2.0
"""Unwired owned-snapshot baseline factory candidate for #400.

Capturing external inputs/locator and publishing/hook execution remain caller
responsibilities. No filesystem reads, tool recapture or callback execution.
"""

import copy
import json
import math
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

# Admission is by type identity, never by equality. A metaclass can redefine
# __eq__ and __hash__, so a member test such as `type(value) in (...)` asks the
# object whether it belongs -- exactly the question an attacker wants to answer.
_SAFE_ATOMS = (bool, int, float, *_SAFE_ENUM_TYPES)
_SAFE_RECORDS = (State, StateFinding, RenderingInputs, AdvisoryFinding, EnvManifest, AssertionOutcome)
_SEQUENCE_TYPES = (list, tuple, set, frozenset)
# copy.deepcopy recurses; a chain deeper than this must be refused here rather
# than surface as a RecursionError from inside the copy it was meant to guard.
_MAX_DEPTH = 200

# Per-record field constraints. The generic walk admits a value because its type
# is on the allowlist; it does not know that `verdict` must be a Verdict and not
# some other admitted enum. Without this, `state.verdict = ManifestTier.ABSENT`
# passes preflight and the report prints `code-forge: absent` for a FAIL state.
_FIELD_CHECKS = {
    State: {
        "mode": lambda value: _admitted(value, (Mode,)),
        "verdict": lambda value: _admitted(value, (Verdict,)),
    },
    StateFinding: {
        "disposition": lambda value: _admitted(value, (Disposition,)),
        "diagnostic_kind": lambda value: value is None or _admitted(value, (FindingDiagnosticKind,)),
    },
    RenderingInputs: {
        "forge_version": lambda value: type(value) is str,
        "backend_name": lambda value: value is None or type(value) is str,
        "backend_model": lambda value: value is None or type(value) is str,
        "resolved_locator": lambda value: value is None or type(value) is str,
        "tool_versions": lambda value: (
            type(value) is dict
            and all(type(key) is str and type(item) is str for key, item in value.items())
        ),
    },
    EnvManifest: {
        "tier": lambda value: _admitted(value, (ManifestTier,)),
    },
    AssertionOutcome: {
        "status": lambda value: _admitted(value, (AssertionStatus,)),
        "stage": lambda value: value is None or _admitted(value, (AssertionStage,)),
        "kind": lambda value: value is None or _admitted(value, (AssertionKind,)),
    },
}


def _checked_field(record, name, value):
    """Apply the position-specific constraint for one field, if one is declared."""
    checks = _FIELD_CHECKS.get(type(record))
    if checks is None:
        return
    check = checks.get(name)
    if check is not None and not check(value):
        raise TypeError(f"unexpected snapshot field: {name}")


def _admitted(value, allowed):
    """True when `type(value)` IS one of `allowed`, compared by identity.

    `value_type in allowed` and `isinstance(value, allowed)` both ask the object
    or its class a question a metaclass can answer for itself.
    """
    value_type = type(value)
    return any(value_type is candidate for candidate in allowed)


def _checked_attributes(item):
    """Return the declared field list after validating the instance attribute map.

    Order matters. The record type is already established by identity; then the
    attribute map must be an exact dict; then its size must match the declared
    fields; then every key must be an exact str. Only after that may the keys be
    compared as a set -- a str subclass with a redefined __eq__/__hash__ would
    otherwise run its own code during the comparison.

    ``fields`` is called on the type, not the instance. ``__dataclass_fields__``
    is a plain class attribute, so an instance-dict entry of that name wins a
    getattr on the instance and ``fields()`` would call ``.values()`` on whatever
    it found -- a protocol hook running on an object we go on to refuse.
    """
    attributes = fields(type(item))
    instance_values = getattr(item, "__dict__", None)
    if type(instance_values) is not dict:
        raise TypeError("unexpected snapshot attributes")
    if len(instance_values) != len(attributes):
        raise TypeError("unexpected snapshot attributes")
    for key in instance_values:
        if type(key) is not str:
            raise TypeError("unexpected snapshot attributes")
    declared = set()
    for field in attributes:
        if type(field.name) is not str:
            raise TypeError("unexpected snapshot attributes")
        declared.add(field.name)
    if set(instance_values) != declared:
        raise TypeError("unexpected snapshot attributes")
    return attributes


def _preflight(value, limits):
    # Count expanded occurrences, not unique identities: JSON rendering expands
    # alias DAGs even though deepcopy preserves their shared graph. Active-path
    # tracking rejects cycles without rejecting a repeated finding occurrence.
    pending = [(value, False, 0)]
    active = set()
    nodes = 1
    characters = 0
    while pending:
        item, leaving, depth = pending.pop()
        if leaving:
            active.remove(id(item))
            continue
        if type(item) is str:
            characters += len(item)
            if characters > limits.source_characters:
                raise ValueError("snapshot character limit")
        elif item is None or _admitted(item, _SAFE_ATOMS):
            if type(item) is float and not math.isfinite(item):
                raise ValueError("non-finite snapshot number")
            continue
        else:
            if id(item) in active:
                raise ValueError("cyclic snapshot object")
            if type(item) is dict:
                count = 2 * len(item)
                children = None
            elif _admitted(item, _SEQUENCE_TYPES):
                count = len(item)
                children = item
            elif _admitted(item, _SAFE_RECORDS):
                attributes = _checked_attributes(item)
                count = len(attributes)
                children = None
            else:
                raise TypeError("unsupported snapshot object")
            # The budget is charged before any field value is queued.
            if count > limits.source_nodes - nodes:
                raise ValueError("snapshot node limit")
            nodes += count
            active.add(id(item))
            if depth >= _MAX_DEPTH:
                raise ValueError("snapshot nesting depth limit")
            pending.append((item, True, depth))
            if type(item) is dict:
                pending.extend((child, False, depth + 1) for child in item.keys())
                pending.extend((child, False, depth + 1) for child in item.values())
            elif _admitted(item, _SAFE_RECORDS):
                for field in attributes:
                    value = getattr(item, field.name)
                    _checked_field(item, field.name, value)
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
                    pending.append((value, False, depth + 1))
            else:
                pending.extend((child, False, depth + 1) for child in children)


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
    locator = context.resolved_locator
    if locator is not None and type(locator) is not str:
        raise TypeError("locator must be captured exact str or None")
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
