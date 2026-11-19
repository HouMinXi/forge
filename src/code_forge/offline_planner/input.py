# SPDX-License-Identifier: Apache-2.0

"""Offline Stage 1 input schema and local reference checks only."""

from __future__ import annotations

import re

from .codec import PASS_LENSES

MAX_INT = 9007199254740991


def _enum(*values):
    return lambda x: type(x) is str and x in values


def _id(x):
    return (
        type(x) is str and len(x) <= 128 and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", x) is not None
    )


def _hex(n):
    return lambda x: type(x) is str and re.fullmatch(r"[0-9a-f]{%d}" % n, x) is not None


def _uint(x):
    return type(x) is int and 0 <= x <= MAX_INT


def _positive(x):
    return type(x) is int and 1 <= x <= MAX_INT


def _nullable(check):
    return lambda x: x is None or check(x)


STATE = _enum("matched", "recognized_no_match", "unknown")
REASON = _enum(
    "matched-signal", "domain-no-match", "unsupported-input", "missing-evidence", "decode-unavailable"
)
SCHEMA = {
    "serialization_version": _enum("plan-json-v1"),
    "schema_version": lambda x: type(x) is int and x == 1,
    "planner_version": _enum("planner-v1"),
    "candidate": {
        "repository_id": _id,
        "base_sha": _hex(40),
        "head_sha": _hex(40),
        "scope_kind": _enum("git-diff"),
        "diff_sha256": _hex(64),
        "snapshot_sha256": _hex(64),
        "context_manifest_sha256": _hex(64),
    },
    "policy": {"version": _id, "sha256": _hex(64)},
    "catalog": {"version": _enum("lenses-v1"), "sha256": _hex(64)},
    "frozen_groups": [
        {"packet_id": _id, "owned_hunk_ids": [_id], "required_hunk_ids": [_id], "evidence_ids": [_id]}
    ],
    "context_manifest": [
        {
            "evidence_id": _id,
            "origin": _id,
            "role": _enum("changed", "contract", "test", "reference"),
            "content_sha256": _hex(64),
            "range_start": _nullable(_positive),
            "range_end": _nullable(_positive),
        }
    ],
    "recognition": [
        {
            "packet_id": _id,
            "pass_name": _enum(*PASS_LENSES),
            "lens_id": _enum("L1", "L2", "L3", "L4", "L5"),
            "state": STATE,
            "domain": _nullable(_id),
            "signal_ids": [_id],
            "reason": REASON,
        }
    ],
    "budget": {
        "tokenizer_id": _nullable(_id),
        **{
            k: _nullable(_uint)
            for k in (
                "request_input_limit",
                "request_output_limit",
                "candidate_attempt_limit",
                "candidate_retrieval_limit",
            )
        },
    },
}


def _pointer(path):
    return "" if not path else "/" + "/".join(str(x).replace("~", "~0").replace("/", "~1") for x in path)


def _first(errors):
    return _pointer(min(errors)) if errors else None


def _walk(value, schema, path, errors):
    if isinstance(schema, dict):
        if type(value) is not dict:
            errors.append(path)
            return
        for key in set(value) | set(schema):
            if key not in schema or key not in value:
                errors.append(path + (key,))
            else:
                _walk(value[key], schema[key], path + (key,), errors)
    elif isinstance(schema, list):
        if type(value) is not list:
            errors.append(path)
            return
        for i, item in enumerate(value):
            _walk(item, schema[0], path + (i,), errors)
    elif not schema(value):
        errors.append(path)


def schema_path(value):
    """Return first Phase 2 JSON pointer, or None. No artifact access."""
    errors = []
    _walk(value, SCHEMA, (), errors)
    if type(value) is not dict:
        return _first(errors)
    rows = value.get("recognition")
    if type(rows) is list:
        for i, row in enumerate(rows):
            if type(row) is not dict:
                continue
            p = ("recognition", i)
            signals = row.get("signal_ids")
            valid_signals = type(signals) is list and all(_id(x) for x in signals)
            if valid_signals and (signals != sorted(signals) or len(signals) != len(set(signals))):
                errors.append(p + ("signal_ids",))
            if "state" not in row or not STATE(row["state"]):
                continue
            state = row["state"]
            if "domain" in row and _nullable(_id)(row["domain"]):
                if (row["domain"] is None) != (state == "unknown"):
                    errors.append(p + ("domain",))
            if "reason" in row and REASON(row["reason"]):
                allowed = {
                    "matched": {"matched-signal"},
                    "recognized_no_match": {"domain-no-match"},
                    "unknown": {"unsupported-input", "missing-evidence", "decode-unavailable"},
                }
                if row["reason"] not in allowed[state]:
                    errors.append(p + ("reason",))
            if valid_signals and ((len(signals) > 0) != (state == "matched")):
                errors.append(p + ("signal_ids",))
    rows = value.get("context_manifest")
    if type(rows) is list:
        for i, row in enumerate(rows):
            if type(row) is not dict or not all(
                k in row and _nullable(_positive)(row[k]) for k in ("range_start", "range_end")
            ):
                continue
            a, b = row["range_start"], row["range_end"]
            if (a is None) != (b is None) or (a is not None and b is not None and a > b):
                errors.append(("context_manifest", i, "range_end"))
    return _first(errors)


def reference_path(value):
    """Return first Phase 3 pointer. Precondition: schema_path is None."""
    groups, manifest, rows = value["frozen_groups"], value["context_manifest"], value["recognition"]
    errors = []
    packets, evidence, owned = set(), set(), set()
    for i, item in enumerate(manifest):
        if item["evidence_id"] in evidence:
            errors.append(("context_manifest", i, "evidence_id"))
        evidence.add(item["evidence_id"])
    for i, g in enumerate(groups):
        p = ("frozen_groups", i)
        if g["packet_id"] in packets:
            errors.append(p + ("packet_id",))
        packets.add(g["packet_id"])
        for key in ("evidence_ids", "owned_hunk_ids", "required_hunk_ids"):
            seen = owned if key == "owned_hunk_ids" else set()
            for j, item in enumerate(g[key]):
                if item in seen:
                    errors.append(p + (key, j))
                seen.add(item)
    if errors:
        return _first(errors)
    # 3b: resolve references before ownership, tuples and aggregate cardinality.
    for i, g in enumerate(groups):
        for j, e in enumerate(g["evidence_ids"]):
            if e not in evidence:
                errors.append(("frozen_groups", i, "evidence_ids", j))
    for i, row in enumerate(rows):
        if row["packet_id"] not in packets:
            errors.append(("recognition", i, "packet_id"))
    if errors:
        return _first(errors)
    for i, row in enumerate(rows):
        if row["lens_id"] not in PASS_LENSES[row["pass_name"]]:
            errors.append(("recognition", i, "lens_id"))
    if errors:
        return _first(errors)
    tuples = []
    seen = set()
    for i, row in enumerate(rows):
        key = (row["packet_id"], row["pass_name"], row["lens_id"])
        if key in seen:
            errors.append(("recognition", i, "lens_id"))
        seen.add(key)
        tuples.append(key)
    if errors:
        return _first(errors)
    expected = [
        (g["packet_id"], pn, lens)
        for g in groups
        for pn, lenses in PASS_LENSES.items()
        for lens in lenses
    ]
    if set(tuples) != set(expected):
        return "/recognition"
    # 3c: ordering/subset only after all prerequisites succeed.
    indices = {e["evidence_id"]: i for i, e in enumerate(manifest)}
    for i, g in enumerate(groups):
        last = -1
        for j, e in enumerate(g["evidence_ids"]):
            current = indices[e]
            if current < last:
                errors.append(("frozen_groups", i, "evidence_ids", j))
                break
            last = current
        offset = 0
        for j, item in enumerate(g["required_hunk_ids"]):
            try:
                offset = g["owned_hunk_ids"].index(item, offset) + 1
            except ValueError:
                errors.append(("frozen_groups", i, "required_hunk_ids", j))
                break
    for i, (actual, wanted) in enumerate(zip(tuples, expected, strict=True)):
        if actual != wanted:
            errors.append(("recognition", i))
            break
    return _first(errors)
