# SPDX-License-Identifier: Apache-2.0

"""Frozen Stage 1 artifact phases 4–6, after input phases 1–3 succeed.

Only referenced bundle keys are read. Callback exceptions are host failures and
are deliberately not caught. Artifact occurrences retain their roles even when
several occurrences share the same content digest.
"""

import re

from .codec import PASS_LENSES, canon, digest
from .lexical import InvalidJSON, decode_request


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z", re.ASCII)
_HEX40 = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
_HEX64 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_LAYOUT = {name: list(lenses) for name, lenses in PASS_LENSES.items()}


def _string(value, pattern):
    return type(value) is str and pattern.fullmatch(value) is not None


def _object(value, keys):
    return type(value) is dict and set(value) == set(keys)


def _ids(value, *, sorted_required=False):
    return (
        type(value) is list
        and all(_string(item, _ID) for item in value)
        and len(set(value)) == len(value)
        and (not sorted_required or value == sorted(value))
    )


def _groups(value):
    if type(value) is not list:
        return False
    packets = set()
    owned = set()
    for group in value:
        if not _object(group, ("packet_id", "owned_hunk_ids", "required_hunk_ids", "evidence_ids")):
            return False
        if not _string(group["packet_id"], _ID) or group["packet_id"] in packets:
            return False
        packets.add(group["packet_id"])
        if not all(_ids(group[key]) for key in ("owned_hunk_ids", "required_hunk_ids", "evidence_ids")):
            return False
        if owned.intersection(group["owned_hunk_ids"]):
            return False
        owned.update(group["owned_hunk_ids"])
        remaining = iter(group["owned_hunk_ids"])
        if not all(
            any(item == required for item in remaining) for required in group["required_hunk_ids"]
        ):
            return False
    return True


def _snapshot_shape(value):
    if not _object(value, ("candidate_binding", "frozen_groups", "owned_hunk_ids", "required_hunk_ids")):
        return False
    binding = value["candidate_binding"]
    if not _object(binding, ("repository_id", "base_sha", "head_sha", "scope_kind", "diff_sha256")):
        return False
    return (
        _string(binding["repository_id"], _ID)
        and _string(binding["base_sha"], _HEX40)
        and _string(binding["head_sha"], _HEX40)
        and type(binding["scope_kind"]) is str
        and binding["scope_kind"] == "git-diff"
        and _string(binding["diff_sha256"], _HEX64)
        and _groups(value["frozen_groups"])
        and _ids(value["owned_hunk_ids"])
        and _ids(value["required_hunk_ids"])
    )


def _policy_shape(value):
    return (
        _object(value, ("version", "recognized_domains"))
        and _string(value["version"], _ID)
        and _ids(value["recognized_domains"], sorted_required=True)
    )


def _catalog_shape(value):
    if not _object(value, ("version", "pass_lenses")) or not _string(value["version"], _ID):
        return False
    mapping = value["pass_lenses"]
    if not _object(mapping, _LAYOUT):
        return False
    for lenses in mapping.values():
        if (
            type(lenses) is not list
            or not all(type(lens) is str and lens in {"L1", "L2", "L3", "L4", "L5"} for lens in lenses)
            or len(set(lenses)) != len(lenses)
        ):
            return False
    return True


def artifact_error_path(value, read_artifact):
    """Return the earliest artifact-mismatch pointer, or None.

    Input shape/references must already be valid. read_artifact(digest) returns
    exact bytes or None for absence; invalid callback return types and all host
    exceptions remain out-of-band rather than becoming invalid-input results.
    """
    candidate = value["candidate"]
    # Already in canonical input traversal order, including numeric indices.
    occurrences = [
        ("/candidate/context_manifest_sha256", "manifest", candidate["context_manifest_sha256"], None),
        ("/candidate/diff_sha256", "diff", candidate["diff_sha256"], None),
        ("/candidate/snapshot_sha256", "snapshot", candidate["snapshot_sha256"], None),
        ("/catalog/sha256", "catalog", value["catalog"]["sha256"], None),
    ]
    occurrences.extend(
        (f"/context_manifest/{index}/content_sha256", "content", evidence["content_sha256"], evidence)
        for index, evidence in enumerate(value["context_manifest"])
    )
    occurrences.append(("/policy/sha256", "policy", value["policy"]["sha256"], None))

    verified = []
    for path, role, expected, evidence in occurrences:
        if role == "manifest":
            raw = canon(value["context_manifest"])
        else:
            raw = read_artifact(expected)
            if raw is None:
                return path
            if type(raw) is not bytes:
                raise TypeError("read_artifact must return bytes or None")
        if digest(raw) != expected:
            return path
        verified.append((path, role, raw, evidence))

    # Phase 5 must finish for every structured role before any bindings/ranges.
    structured = {}
    shapes = {"snapshot": _snapshot_shape, "policy": _policy_shape, "catalog": _catalog_shape}
    for path, role, raw, _ in verified:
        if role not in shapes:
            continue
        try:
            parsed = decode_request(raw)
        except InvalidJSON:
            return path
        if not shapes[role](parsed) or canon(parsed) != raw:
            return path
        structured[role] = parsed

    # Occurrences are digest-pointer ordered. Comparison names are lexical
    # within each digest; the named predicates deliberately have no fallback.
    for path, role, raw, evidence in verified:
        if role == "snapshot":
            snapshot = structured[role]
            for name in sorted(snapshot["candidate_binding"]):  # snapshot-binding
                if snapshot["candidate_binding"][name] != candidate[name]:
                    return path
            if snapshot["frozen_groups"] != value["frozen_groups"]:  # snapshot-groups
                return path
            for name in ("owned_hunk_ids", "required_hunk_ids"):  # snapshot-inventory
                expected = [hunk for group in value["frozen_groups"] for hunk in group[name]]
                if snapshot[name] != expected:
                    return path
        elif role == "catalog":
            catalog = structured[role]
            if catalog["pass_lenses"] != _LAYOUT:  # catalog-layout
                return path
            if catalog["version"] != value["catalog"]["version"]:  # catalog-version
                return path
        elif role == "content":  # evidence-range
            if evidence["range_end"] is not None:
                lines = raw.count(b"\n") + int(bool(raw) and not raw.endswith(b"\n"))
                if evidence["range_end"] > lines:
                    return path
        elif role == "policy":
            policy = structured[role]
            domains = set(policy["recognized_domains"])
            if any(
                entry["domain"] is not None and entry["domain"] not in domains
                for entry in value["recognition"]
            ):  # policy-domain
                return path
            if policy["version"] != value["policy"]["version"]:  # policy-version
                return path
    return None
