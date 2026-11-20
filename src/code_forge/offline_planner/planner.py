# SPDX-License-Identifier: Apache-2.0

"""Forge #537 offline plan generator. Does not execute a review."""

from collections.abc import Callable

from .artifacts import artifact_error_path
from .codec import PASS_LENSES, SCHEMA_VERSION, SERIALIZATION_VERSION, canon, digest
from .input import reference_path, schema_path
from .lexical import InvalidJSON, decode_request


def _error(code, path):
    return canon(
        {
            "error_code": code,
            "field_path": path,
            "schema_version": SCHEMA_VERSION,
            "serialization_version": SERIALIZATION_VERSION,
        }
    )


def _null_first(value):
    return value is not None, value or ""


def _output(value):
    states = {(r["packet_id"], r["pass_name"], r["lens_id"]): r["state"] for r in value["recognition"]}
    budget = value["budget"]
    assignments, diagnostics, estimates = [], [], []
    uncertainty = ["prompt-not-rendered"]
    if budget["tokenizer_id"] is None:
        uncertainty.append("tokenizer-unavailable")

    def observe(code, packet_id=None, pass_name=None):
        diagnostics.append({"code": code, "packet_id": packet_id, "pass_name": pass_name})

    for group in value["frozen_groups"]:
        packet = group["packet_id"]
        for pass_name, lenses in PASS_LENSES.items():
            diagnoses = {lens: states[packet, pass_name, lens] for lens in lenses}
            fallback = "unknown" in diagnoses.values()
            effective = (
                list(lenses) if fallback else [lens for lens in lenses if diagnoses[lens] == "matched"]
            )
            assignments.append(
                {
                    "packet_id": packet,
                    "pass_name": pass_name,
                    "diagnosis_by_lens": diagnoses,
                    "pass_fallback_reason": "unknown-recognition" if fallback else None,
                    "effective_lens_ids": effective,
                }
            )
            estimates.append(
                {
                    "packet_id": packet,
                    "pass_name": pass_name,
                    "input_tokens": None,
                    "output_allowance": budget["request_output_limit"],
                    "method": "unavailable-v1",
                    "uncertainty_codes": uncertainty,
                }
            )
            observe("estimate-unavailable", packet, pass_name)
            if fallback:
                observe("unknown-recognition", packet, pass_name)
            if budget["request_input_limit"] == 0:
                observe("zero-input-limit", packet, pass_name)
            if budget["request_output_limit"] == 0:
                observe("zero-output-limit", packet, pass_name)
    attempt_limit = budget["candidate_attempt_limit"]
    if attempt_limit is not None and attempt_limit < len(assignments):
        observe("attempt-limit-below-slots")

    diagnostics.sort(key=lambda d: (d["code"], _null_first(d["packet_id"]), _null_first(d["pass_name"])))
    return {
        "serialization_version": SERIALIZATION_VERSION,
        "schema_version": SCHEMA_VERSION,
        "input_digest": digest(canon(value)),
        "assignments": assignments,
        "estimates": estimates,
        "diagnostics": diagnostics,
    }


def plan(request_bytes: bytes, read_artifact: Callable[[str], bytes | None]) -> bytes:
    """Return canonical plan or contract error bytes; host exceptions propagate."""
    try:
        value = decode_request(request_bytes)
    except InvalidJSON:
        return _error("invalid-json", "")
    path = schema_path(value)
    if path is not None:
        return _error("invalid-schema", path)
    path = reference_path(value)
    if path is not None:
        return _error("invalid-reference", path)
    path = artifact_error_path(value, read_artifact)
    if path is not None:
        return _error("artifact-mismatch", path)

    result = _output(value)
    result["plan_digest"] = digest(canon(result))
    return canon(result)
