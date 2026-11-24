# SPDX-License-Identifier: Apache-2.0
"""Opt-in product view that drops only records this round can still name.

The default v1 report counts every record reporting_product_findings keeps.
This view is separate and never changes that count. It drops a record only
when the caller hands it the capture made while this round handled the
failure, and that capture points at exactly one persisted finding. After a
reload, two findings that share an id and a fingerprint cannot be told
apart, so neither is dropped.
"""

from .disposition import Disposition
from .state import StateFinding, Verdict, reporting_product_findings

_INTERRUPT_FILES = {"<llm-invoke>"}
_RECEIPT_FILE = "<receipt-evidence>"


def _interrupt_shape(finding: StateFinding) -> bool:
    return (
        type(finding.id) is str
        and finding.id.startswith("l1-")
        and finding.id.endswith("-invoke-fail")
        and type(finding.fingerprint) is str
        and finding.fingerprint.startswith("invoke-fail-")
        and finding.file in _INTERRUPT_FILES
        and finding.line_range == [0, 0]
        and finding.source == "INFRA"
        and finding.disposition is Disposition.CONFIRMED
    )


def _receipt_shape(finding: StateFinding) -> bool:
    return (
        finding.id == "RECEIPT_INVALID"
        and type(finding.fingerprint) is str
        and finding.fingerprint.startswith("receipt-")
        and len(finding.fingerprint) == len("receipt-") + 12
        and finding.file == _RECEIPT_FILE
        and finding.line_range == [0, 0]
        and finding.source == "INFRA"
        and finding.disposition is Disposition.CONFIRMED
    )


def _names_this_finding(capture: dict, finding: StateFinding) -> bool:
    """True when a capture recorded at handling time matches one finding.

    The id and fingerprint repeat across rounds, so the match also requires
    the description and timeout flag taken from the finding while the
    exception was still in hand. A capture that omits them matches nothing.
    """
    if capture.get("id") != finding.id or capture.get("fingerprint") != finding.fingerprint:
        return False
    if "description" not in capture or "is_timeout" not in capture:
        return False
    return (
        capture.get("description") == finding.description
        and capture.get("is_timeout") is finding.is_timeout
    )


def _captured_indices(findings: list[StateFinding], captured: list[dict]) -> set[int]:
    """Indices of findings a capture names uniquely.

    A capture that fits more than one finding names none of them: the report
    cannot tell which record this round handled, and it does not guess from
    list order or drop both.
    """
    chosen: set[int] = set()
    for capture in captured:
        if type(capture) is not dict or type(capture.get("kind")) is not str:
            continue
        hits = [
            index
            for index, finding in enumerate(findings)
            if _interrupt_shape(finding) and _names_this_finding(capture, finding)
        ]
        if len(hits) == 1:
            fingerprint = findings[hits[0]].fingerprint
            shared = any(
                index != hits[0] and finding.fingerprint == fingerprint
                for index, finding in enumerate(findings)
            )
            if not shared:
                chosen.add(hits[0])
    return chosen


def _booked_indices(findings: list[StateFinding], booked: list[dict]) -> set[int]:
    """Indices of receipt records this round appended, not ones it skipped.

    The receipt gate refuses to append when the fingerprint already exists,
    so a matching fingerprint can belong to an older record. Only a booking
    that carries the description of the record it appended, and that fits
    exactly one finding, counts.
    """
    chosen: set[int] = set()
    for booking in booked:
        if type(booking) is not dict or type(booking.get("fingerprint")) is not str:
            continue
        if "description" not in booking:
            continue
        hits = [
            index
            for index, finding in enumerate(findings)
            if _receipt_shape(finding)
            and finding.fingerprint == booking["fingerprint"]
            and finding.description == booking["description"]
        ]
        if len(hits) == 1:
            fingerprint = findings[hits[0]].fingerprint
            shared = any(
                index != hits[0] and finding.fingerprint == fingerprint
                for index, finding in enumerate(findings)
            )
            if not shared:
                chosen.add(hits[0])
    return chosen


def host_attributed_report(
    state,
    *,
    captured: list[dict],
    booked: list[dict],
    attributable: bool = True,
) -> dict:
    """Product count and SARIF results for one explicit view.

    ``captured`` holds the records taken when this round caught an
    interruption. ``booked`` holds the receipt records this round appended.
    ``attributable`` is false when a booked receipt failure mixes the
    interruption with an independent evidence problem and the two cannot be
    separated; the whole record then keeps its product classification.
    """
    findings = list(state.findings)
    excluded = _captured_indices(findings, captured)
    if attributable:
        excluded |= _booked_indices(findings, booked)
    product_path = {id(finding) for finding in reporting_product_findings(findings)}
    product = [
        finding
        for index, finding in enumerate(findings)
        if index not in excluded and id(finding) in product_path
    ]
    interrupts = [
        findings[index].fingerprint for index in sorted(excluded) if _interrupt_shape(findings[index])
    ]
    blocks = [
        findings[index].fingerprint for index in sorted(excluded) if _receipt_shape(findings[index])
    ]
    # The verdict is the one the run already reached. This view never
    # reclassifies it; the caller passes the run's verdict in.
    verdict = state.verdict.value if isinstance(state.verdict, Verdict) else str(state.verdict)
    summary = "code-forge: %s findings=%d confirmed=%d" % (
        verdict,
        len(product),
        sum(1 for finding in product if finding.disposition is Disposition.CONFIRMED),
    )
    return {
        "product_count": len(product),
        "interrupts": interrupts,
        "receipt_blocks": blocks,
        "summary": summary,
        "results": [{"fingerprint": finding.fingerprint, "file": finding.file} for finding in product],
        "excluded_indices": sorted(excluded),
        "kept_fingerprints": [finding.fingerprint for finding in product],
    }
