# SPDX-License-Identifier: Apache-2.0
"""Unwired low-level final-text boundary candidate for #400.

NOT the same-snapshot orchestrator. The CLI currently has no pure complete
baseline factory. Callers must not treat this helper as proof that baseline,
candidate and State belong together, or as permission to publish. It prepares
strings and derives selection metadata from those exact final bytes only.
"""

from dataclasses import dataclass
import json

from code_forge.report_presentation import _legacy_envelope_accepted
from code_forge.report_reader_v2 import ReaderLimits, ReadSelection, read_report_view


@dataclass(frozen=True)
class FinalText:
    stdout: str
    stderr: str

    def __post_init__(self):
        if type(self.stdout) is not str or type(self.stderr) is not str:
            raise TypeError("final streams must be strings")


@dataclass(frozen=True)
class PreparationLimits:
    legacy_chars: int
    candidate_bytes: int
    candidate_stderr_bytes: int
    reader: ReaderLimits

    def __post_init__(self):
        if type(self.reader) is not ReaderLimits:
            raise TypeError("expected ReaderLimits")
        for value in (self.legacy_chars, self.candidate_bytes, self.candidate_stderr_bytes):
            if type(value) is not int or value <= 0:
                raise ValueError("preparation limits must be positive exact integers")


@dataclass(frozen=True)
class PreparedText:
    text: FinalText
    selection: ReadSelection
    writer_reason: str


def finalize_payload(stdout_body: str, stderr_final: str) -> FinalText:
    """Append precisely the legacy print suffix, never normalize input."""
    if type(stdout_body) is not str or type(stderr_final) is not str:
        raise TypeError("expected unframed stdout body and final stderr")
    return FinalText(stdout_body + "\n", stderr_final)


def _selection(text: FinalText, limits: ReaderLimits) -> ReadSelection:
    if len(text.stdout) > limits.input_bytes:
        return ReadSelection("v2", None, "limit-exceeded", "byte-limit", None)
    try:
        encoded = text.stdout.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return ReadSelection("v2", None, "invalid-report", "invalid-utf8", None)
    return read_report_view(encoded, requested_version="v2", limits=limits)


def _restore_baseline(baseline: FinalText, limits: ReaderLimits, reason: str) -> PreparedText:
    return PreparedText(baseline, _selection(baseline, limits), reason)


def prepare_final_candidate(
    baseline: FinalText, *, candidate_body: str, candidate_stderr: str, limits: PreparationLimits
) -> PreparedText:
    """Bounded text selection only. No load, classification, rendering or I/O.

    baseline is already FINAL. Candidate stdout is explicitly unframed;
    candidate stderr already FINAL. Candidate creation faults must be handled
    by a future genuine snapshot orchestrator, not represented by empty JSON.
    Baseline metadata is independently derived, even if its old-readable
    artifact exceeds the new reader's limits and cannot supply a selected view.
    """
    if type(baseline) is not FinalText or type(limits) is not PreparationLimits:
        raise TypeError("expected FinalText and PreparationLimits")
    if type(candidate_body) is not str or type(candidate_stderr) is not str:
        raise TypeError("candidate streams must be strings")

    def restore(reason):
        return _restore_baseline(baseline, limits.reader, reason)

    readable, baseline_document = _legacy_envelope_accepted(baseline.stdout)
    if not readable or len(baseline.stdout) > limits.legacy_chars:
        return restore("baseline-unusable")
    # UTF-8 uses at least one byte per scalar; reject this necessary bound
    # before adding framing or allocating an encoded copy.
    final_chars = len(candidate_body) + 1
    if (
        final_chars > limits.legacy_chars
        or final_chars > limits.candidate_bytes
        or final_chars > limits.reader.input_bytes
        or len(candidate_stderr) > limits.candidate_stderr_bytes
    ):
        return restore("v2-budget-refused")
    final = finalize_payload(candidate_body, candidate_stderr)
    try:
        encoded = final.stdout.encode("utf-8", errors="strict")
        stderr_encoded = final.stderr.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return restore("v2-preparation-failed")
    if len(encoded) > limits.candidate_bytes or len(stderr_encoded) > limits.candidate_stderr_bytes:
        return restore("v2-budget-refused")
    selected = read_report_view(encoded, requested_version="v2", limits=limits.reader)
    if selected.selected_version != "v2" or selected.block is None:
        return restore("v2-preparation-failed")
    candidate_document = json.loads(encoded)
    for document in (baseline_document, candidate_document):
        document["runs"][0].setdefault("properties", {}).pop("report_classification", None)
    if json.dumps(baseline_document, sort_keys=True) != json.dumps(candidate_document, sort_keys=True):
        return restore("v2-substrate-mismatch")
    if selected.block["availability"] != "available":
        return restore("v2-unavailable")
    if selected.block["delivery_mode"] != "projection" or any(
        error["stage"] in ("projection", "render") for error in selected.block["classification_errors"]
    ):
        return restore("v2-global-fallback")
    return PreparedText(final, selected, "v2-prepared")
