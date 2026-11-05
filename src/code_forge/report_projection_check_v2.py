# SPDX-License-Identifier: Apache-2.0
"""Independent projection-to-received-block check; no serializer calls.

The byte reader establishes schema validity. This check separately establishes
that the selected artifact did not omit or alter the actual projection. Shared
schema vocabulary is not a substitute for occurrence/content comparison.
"""

from code_forge.report_presentation import FIELD_INVENTORY


def exact_json_equal(left, right) -> bool:
    """Structural JSON equality without Python's bool/int/float coercion."""
    pending = [(left, right)]
    while pending:
        expected, actual = pending.pop()
        if type(expected) is not type(actual):
            return False
        if type(expected) is dict:
            if (
                len(expected) != len(actual)
                or any(type(key) is not str for key in expected)
                or any(type(key) is not str for key in actual)
                or expected.keys() != actual.keys()
            ):
                return False
            pending.extend((value, actual[key]) for key, value in expected.items())
        elif type(expected) is list:
            if len(expected) != len(actual):
                return False
            pending.extend(zip(expected, actual, strict=True))
        elif type(expected) in (str, int, float, bool, type(None)):
            if expected != actual:
                return False
        else:
            return False
    return True


def projection_matches_block(projection, block) -> bool:
    if (
        block is None
        or block.get("availability") != "available"
        or block.get("delivery_mode") != "projection"
    ):
        return False
    for name in (
        "classification_complete",
        "product_list_count",
        "unresolved_candidate_count",
        "product_path_record_count",
        "acquisition_complete",
        "acquisition_reason",
    ):
        if not exact_json_equal(getattr(projection, name), block.get(name)):
            return False
    reason = projection.acquisition_reason
    if reason in ("completion-not-provable-from-state", "capacity-incomplete-record"):
        expected_outcomes = [[name, "completed"] for name in ("qodo", "expert", "adversarial")]
    elif reason == "pass-outcome-not-completed":
        value = projection.persisted_pass_outcomes
        expected_outcomes = None if value is None else [list(pair) for pair in value]
    else:
        expected_outcomes = None
    if not exact_json_equal(expected_outcomes, block.get("persisted_pass_outcomes")):
        return False

    observation_kinds = {
        "falsifier-failure": "persisted-falsify-error",
        "evidence-downgrade": "persisted-evidence-downgrade",
    }
    host_kinds = {
        "S1": "provider-capacity",
        "S2": "capacity-incomplete",
        "S3": "receipt-audit",
        "MUTATION_INFRA": "mutation-infra",
    }
    for partition in ("product", "unresolved_product_candidates", "execution", "gate"):
        projected = getattr(projection, partition)
        received = block.get(partition)
        if type(received) is not list or len(received) != len(projected):
            return False
        for record, item in zip(projected, received, strict=True):
            actual = item.get("record", {})
            if not exact_json_equal(record.index, actual.get("record_index")):
                return False
            expected_fields = dict.fromkeys(FIELD_INVENTORY)
            expected_fields.update(
                id=record.finding_id,
                fingerprint=record.fingerprint,
                source=record.source,
                file=record.file,
                line_range=list(record.line_range),
                description=record.description,
                disposition=record.disposition,
            )
            observations = []
            for diagnostic in record.associated_diagnostics:
                if diagnostic.kind not in observation_kinds:
                    return False
                slot = "error" if diagnostic.kind == "falsifier-failure" else "evidence_downgrade"
                expected_fields[slot] = diagnostic.reason
                observations.append(
                    dict(
                        kind=observation_kinds[diagnostic.kind],
                        basis="persisted-field",
                        observation_scope="persisted-record-unbound",
                        text=diagnostic.reason,
                    )
                )
            if not exact_json_equal(expected_fields, actual.get("fields")):
                return False
            if not exact_json_equal(record.host_failure_kind, item.get("host_failure_kind")):
                return False
            if partition in ("product", "unresolved_product_candidates"):
                if not exact_json_equal(observations, item.get("associated_diagnostics")):
                    return False
            else:
                expected = dict(
                    kind=host_kinds.get(record.shape),
                    basis="inherited-predicate",
                    observation_scope="persisted-record-unbound",
                    text=None,
                )
                if expected["kind"] is None or not exact_json_equal(expected, item.get("diagnostic")):
                    return False
    errors = [
        dict(
            record_index=error.index,
            stage="projection" if error.stage == "projection" else "record",
            code="projection-computation-fault"
            if error.stage == "projection"
            else "record-computation-fault",
        )
        for error in projection.classification_errors
    ]
    if not exact_json_equal(errors, block.get("classification_errors")):
        return False
    quarantine = block.get("quarantine")
    if type(quarantine) is not list or len(quarantine) != len(projection.quarantine):
        return False
    for fallback, item in zip(projection.quarantine, quarantine, strict=True):
        expected_safe = {}
        for field, attribute in (
            ("id", "finding_id"),
            ("fingerprint", "fingerprint"),
            ("file", "file"),
            ("description", "description"),
            ("disposition", "disposition"),
        ):
            value = getattr(fallback, attribute)
            if value is not None:
                expected_safe[field] = value
        if fallback.line_range is not None:
            expected_safe["line_range"] = list(fallback.line_range)
        expected = dict(
            record_index=fallback.index,
            safe_fields=expected_safe,
            unavailable_fields=[name for name in FIELD_INVENTORY if name not in expected_safe],
            reason="record-computation-fault",
        )
        if not exact_json_equal(expected, item):
            return False
    return True
