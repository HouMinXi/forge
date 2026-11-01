# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Host-owned receipt display provenance. Never an evidence/credit authority."""

from __future__ import annotations

from .state import Disposition, StateFinding, _PASS_NAMES, is_mutation_infra_diagnostic


def receipt_finding_provenance(finding: StateFinding) -> dict[str, str]:
    """Do not infer infrastructure from reviewer-controlled paths or prose.

    Source is assigned by the host producer. INFRA can also be a product
    candidate after a falsifier failure, so only recognized host structures
    qualify for execution/gate labels; the fallback remains product.
    """
    result = {"source": finding.source, "channel": "product"}
    if finding.source != "INFRA":
        return result
    if finding.error is not None:
        return result
    if is_mutation_infra_diagnostic(finding):
        result["channel"] = "execution"
        return result
    if finding.disposition != Disposition.CONFIRMED or finding.line_range != [0, 0]:
        return result
    for name in _PASS_NAMES:
        for kind, file, channel, subtype in (
            ("schema", "<schema-validation>", "gate", "RECEIPT_SCHEMA"),
            ("incomplete-coverage", "<coverage-guard>", "gate", "RECEIPT_COVERAGE"),
            ("invoke", "<llm-invoke>", "execution", None),
            ("spawn", "<spawn>", "execution", None),
        ):
            suffix = kind if kind == "incomplete-coverage" else kind + "-fail"
            if (
                finding.id == f"l1-{name}-{suffix}"
                and finding.fingerprint == f"{suffix}-{name}"
                and finding.file == file
            ):
                result["channel"] = channel
                if subtype is not None:
                    result["receipt_subtype"] = subtype
                return result
    return result


def unresolved_receipt_label(finding: dict, fallback: str) -> str:
    """Unknown/partial provenance keeps the legacy, deliberately ambiguous label.

    Called only after the existing basis classifier says infra-unavailable.
    These labels never affect verified status, failure_kind or credit.
    """
    source, channel = finding.get("source"), finding.get("channel")
    if source == "UNTRUSTED" and channel == "product":
        return "unresolved UNTRUSTED product candidate without verified evidence"
    if source == "INFRA" and channel in ("execution", "gate"):
        return "unresolved host infrastructure diagnostic without verified evidence"
    if source == "INFRA" and channel == "product":
        return "unresolved product candidate the host could not verify"
    return fallback
