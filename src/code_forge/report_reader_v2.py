# SPDX-License-Identifier: Apache-2.0
"""Experimental #400 S1b byte reader; no defaults, I/O or consumer wiring.

Every limit is caller-supplied. Depth counts containers with the document root
at one. Record limits bound both results and total classification occurrences.
V1 validation is reused only after a bounded index preflight; #399 is untouched.
"""

from dataclasses import dataclass
import json
import math
from typing import Any, Literal

from code_forge.report_presentation import (
    _BlockInvalid,
    _MAX_BLOCK_DEPTH,
    _container_depth,
    _require,
    _validate_product,
    _validate_host,
    _validate_fallback,
    _validate_error,
    _validate_acq,
    _validate_obs,
    _validate_record_view,
    _cross_envelope_cardinality_ok,
    validate_block_dict,
    UNAVAILABLE_REASONS,
    DELIVERY_MODES,
)
from code_forge.report_presentation_v2 import SCHEMA_V2, PROJECTION_SCHEMA_V2


@dataclass(frozen=True)
class ReaderLimits:
    input_bytes: int
    container_depth: int
    record_occurrences: int

    def __post_init__(self):
        for value in (self.input_bytes, self.container_depth, self.record_occurrences):
            if type(value) is not int or value <= 0:
                raise ValueError("reader limits must be positive exact integers")


@dataclass(frozen=True)
class ReadSelection:
    requested_version: Literal["v1", "v2"]
    selected_version: Literal["v1", "v2"] | None
    envelope_condition: str
    selection_reason: str
    block: dict[str, Any] | None


class _Refusal(Exception):
    pass


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _Refusal("duplicate-key")
        result[key] = value
    return result


def _float(text):
    value = float(text)
    if not math.isfinite(value):
        raise _Refusal("nonfinite-number")
    return value


def _depth_preflight(text: str, limit: int):
    depth = 0
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > limit:
                raise _Refusal("depth-limit")
        elif char in "]}":
            depth -= 1


def _scalar_strings(document):
    pending = [document]
    while pending:
        value = pending.pop()
        if type(value) is str:
            value.encode("utf-8", errors="strict")
        elif type(value) is dict:
            pending.extend(value.keys())
            pending.extend(value.values())
        elif type(value) is list:
            pending.extend(value)


def _validate_execution_v2(item):
    _require(type(item) is dict)
    diagnostic = item.get("diagnostic")
    _require(type(diagnostic) is dict)
    if diagnostic.get("kind") != "mutation-infra":
        _validate_host(item, gate=False)
        return
    _validate_record_view(item.get("record"), full_domain=True)
    _validate_obs(diagnostic, allowed_kinds=frozenset({"mutation-infra"}), assoc=False)
    _require("host_failure_kind" in item and item["host_failure_kind"] is None)
    fields = item["record"]["fields"]
    _require(fields["id"] == "MUTATION_ERROR")
    _require(fields["fingerprint"] == "mutation-evidence-error")
    _require(fields["source"] == "INFRA")
    # The wire does not carry the enum object. This is a label consistency
    # check, not proof that the originating State passed the typed predicate.


def _validate_v2_block(block: Any) -> str | None:
    """Return None if valid; otherwise an unavailable_reason tag for failure.

    Structural failures → 'invalid-block'. Unsupported schema literal →
    'unsupported-schema'. Caller handles missing property separately.
    """
    if type(block) is not dict:
        return "invalid-block"
    if "schema" not in block or type(block.get("schema")) is not str:
        return "invalid-block"
    if block["schema"] != SCHEMA_V2:
        return "unsupported-schema"
    try:
        # Whole-block depth
        _require(_container_depth(block) <= _MAX_BLOCK_DEPTH)
        avail = block.get("availability")
        _require(avail in ("available", "unavailable"))
        if avail == "unavailable":
            reason = block.get("unavailable_reason")
            _require(reason in UNAVAILABLE_REASONS)
            for k in (
                "delivery_mode",
                "projection_schema",
                "product",
                "execution",
                "gate",
                "quarantine",
                "unresolved_product_candidates",
                "classification_errors",
                "classification_complete",
                "product_list_count",
                "unresolved_candidate_count",
                "product_path_record_count",
                "acquisition_complete",
                "acquisition_reason",
                "persisted_pass_outcomes",
            ):
                _require(k in block and block[k] is None, k)
            return None

        # available
        _require(block.get("unavailable_reason") is None)
        mode = block.get("delivery_mode")
        _require(mode in DELIVERY_MODES)
        _require(block.get("projection_schema") == PROJECTION_SCHEMA_V2)
        product = block.get("product")
        execution = block.get("execution")
        gate = block.get("gate")
        quarantine = block.get("quarantine")
        unresolved = block.get("unresolved_product_candidates")
        errors = block.get("classification_errors")
        _require(
            all(type(x) is list for x in (product, execution, gate, quarantine, unresolved, errors))
        )
        for p in product:
            _validate_product(p)
        for p in unresolved:
            _validate_product(p)
        for h in execution:
            _validate_execution_v2(h)
        for h in gate:
            _validate_host(h, gate=True)
        for f in quarantine:
            _validate_fallback(f)
        for e in errors:
            _validate_error(e)

        # Disposition partition
        for p in product:
            _require(p["record"]["fields"]["disposition"] != "UNCERTAIN")
        for p in unresolved:
            _require(p["record"]["fields"]["disposition"] == "UNCERTAIN")

        # Index union uniqueness 0..N-1
        indexes: list[int] = []
        indexes.extend((p["record"]["record_index"]) for p in product)
        indexes.extend((p["record"]["record_index"]) for p in unresolved)
        indexes.extend((h["record"]["record_index"]) for h in execution)
        indexes.extend((h["record"]["record_index"]) for h in gate)
        indexes.extend((f["record_index"]) for f in quarantine)
        _require(len(indexes) == len(set(indexes)))
        if indexes:
            _require(set(indexes) == set(range(len(indexes))) and min(indexes) == 0)

        def _increasing(seq: list[int]) -> bool:
            return all(seq[i] < seq[i + 1] for i in range(len(seq) - 1))

        _require(_increasing([p["record"]["record_index"] for p in product]))
        _require(_increasing([p["record"]["record_index"] for p in unresolved]))
        _require(_increasing([h["record"]["record_index"] for h in execution]))
        _require(_increasing([h["record"]["record_index"] for h in gate]))
        _require(_increasing([f["record_index"] for f in quarantine]))

        # Error / quarantine correlation
        local_errors = [e for e in errors if e["stage"] == "record"]
        proj_errors = [e for e in errors if e["stage"] == "projection"]
        render_errors = [e for e in errors if e["stage"] == "render"]
        _require(len(proj_errors) <= 1)
        _require(len(render_errors) <= 1)
        # Order: locals by index, then at most one projection, then at most one render
        reordered = sorted(local_errors, key=lambda e: e["record_index"]) + proj_errors + render_errors
        _require(errors == reordered)
        q_by_idx = {f["record_index"]: f for f in quarantine}
        for e in local_errors:
            fb = q_by_idx.get(e["record_index"])
            _require(fb is not None and fb["reason"] == e["code"])
        if proj_errors:
            # Every occurrence in quarantine
            _require(set(q_by_idx) == set(indexes))
        if mode == "projection":
            _require(len(render_errors) == 0)
            _require(all(f["reason"] != "renderer-computation-fault" for f in quarantine))
        if mode == "renderer-fallback":
            _require(len(render_errors) == 1)
            _require(render_errors[0]["code"] == "renderer-computation-fault")
            _require(render_errors[0] is errors[-1])

        # Counts
        cc = block.get("classification_complete")
        _require(type(cc) is bool)
        plc = block.get("product_list_count")
        ucc = block.get("unresolved_candidate_count")
        pprc = block.get("product_path_record_count")
        if errors:
            _require(cc is False)
        else:
            _require(cc is True)
            _require(len(quarantine) == 0)

        has_proj = len(proj_errors) > 0
        has_render = len(render_errors) > 0
        if has_proj or has_render or mode == "renderer-fallback":
            _require(plc is None and ucc is None and pprc is None)
            if has_proj or mode == "renderer-fallback":
                _require(len(product) == 0 and len(unresolved) == 0)
                _require(len(execution) == 0 and len(gate) == 0)
        else:
            _require(plc == len(product))
            _require(ucc == len(unresolved))
            if pprc is not None:
                _require(type(pprc) is int and pprc >= 0)
                lo = len(product) + len(unresolved)
                hi = lo + len(quarantine)
                _require(lo <= pprc <= hi)

        _validate_acq(
            block.get("acquisition_complete"),
            block.get("acquisition_reason"),
            block.get("persisted_pass_outcomes"),
        )
        # UTF-8 strict on all strings in exposed block
        json.dumps(block, ensure_ascii=False, allow_nan=False).encode("utf-8")
        return None
    except _BlockInvalid:
        return "invalid-block"
    except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
        return "invalid-block"


_PARTITIONS = ("product", "unresolved_product_candidates", "execution", "gate", "quarantine")
_BLOCK_KEYS = frozenset(
    {
        "schema",
        "availability",
        "unavailable_reason",
        "delivery_mode",
        "projection_schema",
        *_PARTITIONS,
        "classification_errors",
        "classification_complete",
        "product_list_count",
        "unresolved_candidate_count",
        "product_path_record_count",
        "acquisition_complete",
        "acquisition_reason",
        "persisted_pass_outcomes",
    }
)


def _bounded_indexes(block, limit):
    if block.get("availability") == "unavailable":
        return
    indexes = []
    for name in _PARTITIONS:
        rows = block.get(name)
        if type(rows) is not list:
            raise _Refusal("invalid-block")
        if len(rows) > limit - len(indexes):
            raise _Refusal("record-limit")
        for row in rows:
            if type(row) is not dict:
                raise _Refusal("invalid-block")
            record = row if name == "quarantine" else row.get("record")
            if type(record) is not dict:
                raise _Refusal("invalid-block")
            index = record.get("record_index")
            if type(index) is not int or index < 0:
                raise _Refusal("invalid-block")
            indexes.append(index)
    count = len(indexes)
    if any(index >= count for index in indexes) or len(set(indexes)) != count:
        raise _Refusal("invalid-block")


def _closed_v2_structure(block):
    if set(block) != _BLOCK_KEYS:
        raise _Refusal("invalid-block")
    if block.get("availability") == "unavailable":
        return
    for name in _PARTITIONS:
        for row in block[name]:
            if name == "quarantine":
                expected = {"record_index", "safe_fields", "unavailable_fields", "reason"}
            elif name in ("product", "unresolved_product_candidates"):
                expected = {"record", "associated_diagnostics", "host_failure_kind"}
            else:
                expected = {"record", "diagnostic", "host_failure_kind"}
            if set(row) != expected:
                raise _Refusal("invalid-block")
            if name != "quarantine":
                if set(row["record"]) != {"record_index", "fields"}:
                    raise _Refusal("invalid-block")
                observations = row.get("associated_diagnostics", [row.get("diagnostic")])
                if type(observations) is not list:
                    raise _Refusal("invalid-block")
                for observation in observations:
                    if type(observation) is not dict or set(observation) != {
                        "kind",
                        "basis",
                        "observation_scope",
                        "text",
                    }:
                        raise _Refusal("invalid-block")
    errors = block.get("classification_errors")
    if type(errors) is not list:
        raise _Refusal("invalid-block")
    for error in errors:
        if type(error) is not dict or set(error) != {"record_index", "stage", "code"}:
            raise _Refusal("invalid-block")
    for count in ("product_list_count", "unresolved_candidate_count", "product_path_record_count"):
        value = block.get(count)
        if value is not None and (type(value) is not int or value < 0):
            raise _Refusal("invalid-block")


def read_report_view(
    raw: bytes, *, requested_version: Literal["v1", "v2"], limits: ReaderLimits
) -> ReadSelection:
    """Validate original bytes and one full envelope before selecting one view."""
    if type(raw) is not bytes or type(limits) is not ReaderLimits:
        raise TypeError("expected bytes and ReaderLimits")
    if type(requested_version) is not str or requested_version not in ("v1", "v2"):
        raise ValueError("requested_version must be v1 or v2")

    def refused(envelope, reason):
        return ReadSelection(requested_version, None, envelope, reason, None)

    if not raw:
        return refused("no-report", "no-report")
    if len(raw) > limits.input_bytes:
        return refused("limit-exceeded", "byte-limit")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return refused("invalid-report", "invalid-utf8")
    try:
        _depth_preflight(text, limits.container_depth)
        doc = json.loads(text, object_pairs_hook=_pairs, parse_float=_float, parse_constant=_float)
        _scalar_strings(doc)
    except _Refusal as error:
        reason = str(error)
        return refused("limit-exceeded" if reason == "depth-limit" else "invalid-report", reason)
    except (ValueError, UnicodeError, RecursionError):
        return refused("invalid-report", "invalid-json")
    if type(doc) is not dict or doc.get("version") != "2.1.0":
        return refused("invalid-report", "invalid-envelope")
    runs = doc.get("runs")
    if type(runs) is not list or len(runs) != 1 or type(runs[0]) is not dict:
        return refused("invalid-report", "invalid-envelope")
    run = runs[0]
    tool = run.get("tool")
    if (
        type(tool) is not dict
        or type(tool.get("driver")) is not dict
        or tool["driver"].get("name") != "code-forge"
    ):
        return refused("invalid-report", "invalid-envelope")
    results = run.get("results")
    properties = run.get("properties", {})
    if (
        type(results) is not list
        or any(type(row) is not dict for row in results)
        or type(properties) is not dict
    ):
        return refused("invalid-report", "invalid-envelope")
    if len(results) > limits.record_occurrences:
        return refused("limit-exceeded", "record-limit")
    if "receiptAudit" in properties:
        audit = properties["receiptAudit"]
        if type(audit) is not list or any(type(row) is not dict for row in audit):
            return refused("invalid-report", "invalid-envelope")
    if "report_classification" not in properties:
        return refused("accepted", "missing-block")
    block = properties["report_classification"]
    if block is None:
        return refused("accepted", "null-block")
    if type(block) is not dict:
        return refused("accepted", "invalid-block")
    schema = block.get("schema")
    versions = {"forge-report-classification/v1": "v1", SCHEMA_V2: "v2"}
    if type(schema) is not str:
        return refused("accepted", "invalid-block")
    version = versions.get(schema)
    if version is None:
        return refused("accepted", "unsupported-schema")
    try:
        _bounded_indexes(block, limits.record_occurrences)
        if version == "v2":
            _closed_v2_structure(block)
    except _Refusal as error:
        return refused("accepted", str(error))
    reason = _validate_v2_block(block) if version == "v2" else validate_block_dict(block)
    if reason is not None or not _cross_envelope_cardinality_ok(block, run):
        return refused("accepted", reason or "invalid-block")
    if requested_version == "v1" and version == "v2":
        return refused("accepted", "requested-version-unavailable")
    reason = "v2-absent" if requested_version == "v2" and version == "v1" else "selected"
    return ReadSelection(requested_version, version, "accepted", reason, block)
