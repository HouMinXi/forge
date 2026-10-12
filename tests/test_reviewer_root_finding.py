# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""A root finding is an envelope normalization, never evidence fabrication."""

import copy
import json
import sys

import pytest

from code_forge.reviewer_json import (
    MissingExcerptEvidenceError,
    _collect_excerpts,
    _json_to_state_findings,
    validate_reviewer_json,
)
from code_forge.receipt import write_receipts
from code_forge.verify import validate_excerpts_against_diff
from tests.test_l1_root_finding import INVALID_ROOT_CORE, ROOT_EVIDENCE_FAULTS, _root_evidence_fault

DIFF = (
    "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
    "@@ -1 +1 @@\n-x = 0\n+x = 1\n"
)
CORE = ("file", "line", "severity", "description")


def _payload():
    return {
        "file": "a.py",
        "line": 1,
        "severity": "P2",
        "description": "synthetic boundary finding",
        "code_excerpts": [{"file": "a.py", "start_line": 1, "end_line": 1, "content": "x = 1"}],
        "pass_name": "adversarial",
    }


def _explicit(payload):
    return payload | {"findings": [{key: payload[key] for key in CORE}]}


def test_root_finding_dictionary_is_normalized_without_mutation():
    payload = _payload()
    payload["context_quotes"] = [{"file": "context.py", "content": "orientation only"}]
    payload["metadata"] = {"status": "model-provided"}
    original = copy.deepcopy(payload)
    validated = validate_reviewer_json(payload)
    assert validated == _explicit(original)
    assert payload == original and "findings" not in payload
    assert validated is not payload
    assert validated["code_excerpts"][0] is not payload["code_excerpts"][0]
    assert set(validated["findings"][0]) == set(CORE)
    assert _json_to_state_findings(validated, "qodo")[0].excerpt is None


@pytest.mark.parametrize("fenced", [False, True])
def test_root_finding_string_uses_existing_json_parser(fenced):
    payload = _payload()
    raw = json.dumps(payload)
    if fenced:
        raw = "```json\n" + raw + "\n```"
    assert validate_reviewer_json(raw) == _explicit(payload)


@pytest.mark.parametrize("key", CORE)
def test_missing_core_field_is_not_normalized(key):
    payload = _payload()
    del payload[key]
    original = copy.deepcopy(payload)
    with pytest.raises(ValueError, match="missing required field: findings"):
        validate_reviewer_json(payload)
    assert payload == original


@pytest.mark.parametrize("value", [None, {}, "not-list", 3, ()])
def test_non_list_root_evidence_does_not_enable_normalization(value):
    payload = _payload() | {"code_excerpts": value}
    original = copy.deepcopy(payload)
    with pytest.raises(ValueError, match="missing required field: findings"):
        validate_reviewer_json(payload)
    assert payload == original


def test_absent_root_evidence_does_not_enable_normalization():
    payload = _payload()
    del payload["code_excerpts"]
    with pytest.raises(ValueError, match="missing required field"):
        validate_reviewer_json(payload)
    assert "findings" not in payload and "code_excerpts" not in payload


@pytest.mark.parametrize("findings", [None, {}, "invalid", 1, [None], [{}]])
def test_explicit_invalid_findings_are_not_overridden(findings):
    payload = _payload() | {"findings": findings}
    original = copy.deepcopy(payload)
    with pytest.raises(ValueError, match="findings must be a list|finding\\[0\\]"):
        validate_reviewer_json(payload)
    assert payload == original


@pytest.mark.parametrize("nonempty", [False, True])
def test_explicit_findings_are_authoritative_even_when_root_fields_are_invalid(nonempty):
    first = {key: _payload()[key] for key in CORE}
    second = first | {"file": "b.py", "description": "second finding"}
    findings = [first, second] if nonempty else []
    payload = _payload() | {"severity": "not-valid", "findings": findings}
    original = copy.deepcopy(payload)
    result = validate_reviewer_json(payload)
    assert result["findings"] == findings
    assert len(result["findings"]) == (2 if nonempty else 0)
    assert payload == original


def test_explicit_empty_findings_and_empty_excerpts_still_refuse():
    payload = _payload() | {"findings": [], "code_excerpts": []}
    original = copy.deepcopy(payload)
    with pytest.raises(MissingExcerptEvidenceError):
        validate_reviewer_json(payload)
    assert payload == original


def test_root_empty_excerpts_keep_generic_one_finding_contract():
    payload = _payload() | {"code_excerpts": []}
    result = validate_reviewer_json(payload)
    assert result == validate_reviewer_json(_explicit(payload))
    assert len(result["findings"]) == 1 and result["code_excerpts"] == []
    assert "findings" not in payload


@pytest.mark.parametrize("severity", ["P9", "p2", ""])
def test_invalid_scalar_severity_reaches_original_schema_refusal(severity):
    payload = _payload() | {"severity": severity}
    original = copy.deepcopy(payload)
    with pytest.raises(ValueError, match="invalid severity"):
        validate_reviewer_json(payload)
    assert payload == original


@pytest.mark.parametrize("severity", [None, [], {}, 0, False])
def test_non_string_root_severity_preserves_original_schema_refusal(severity):
    payload = _payload() | {"severity": severity}
    original = copy.deepcopy(payload)
    with pytest.raises(ValueError, match="missing required field: findings"):
        validate_reviewer_json(payload)
    assert payload == original


@pytest.mark.parametrize("severity", [[], {}])
def test_explicit_unhashable_severity_keeps_existing_failure(severity):
    payload = _explicit(_payload() | {"severity": severity})
    original = copy.deepcopy(payload)
    with pytest.raises(TypeError, match="unhashable"):
        validate_reviewer_json(payload)
    assert payload == original


def test_unhashable_string_subclass_at_root_is_not_normalized():
    class Severity(str):
        __hash__ = None

    payload = _payload() | {"severity": Severity("P2")}
    with pytest.raises(ValueError, match="missing required field: findings"):
        validate_reviewer_json(payload)
    assert "findings" not in payload


@pytest.mark.parametrize("field,value", INVALID_ROOT_CORE)
def test_invalid_root_core_fields_preserve_original_schema_refusal(field, value):
    payload = _payload() | {field: value}
    with pytest.raises(ValueError, match="missing required field: findings"):
        validate_reviewer_json(payload)
    assert "findings" not in payload


@pytest.mark.parametrize("line", [0, -1, 1, sys.maxsize, -sys.maxsize])
def test_native_integer_root_lines_preserve_explicit_envelope_values(line):
    payload = _payload() | {"line": line}
    assert validate_reviewer_json(payload) == validate_reviewer_json(_explicit(payload))
    assert "findings" not in payload


@pytest.mark.parametrize("file", ["a\ud800.py", "a\udfff.py"])
def test_unencodable_root_filename_keeps_original_refusal(file):
    payload = _payload() | {"file": file}
    original = copy.deepcopy(payload)
    with pytest.raises(ValueError, match="missing required field: findings"):
        validate_reviewer_json(payload)
    assert payload == original and "findings" not in payload
    explicit = validate_reviewer_json(_explicit(payload))
    assert explicit["findings"][0]["file"] == file
    with pytest.raises(UnicodeEncodeError):
        _json_to_state_findings(explicit, "qodo")


@pytest.mark.parametrize("file", ["caf\u00e9.py", "\u4ee3\u7801.py"])
def test_encodable_unicode_root_filename_preserves_explicit_values(file):
    payload = _payload() | {"file": file}
    result = validate_reviewer_json(payload)
    assert result == validate_reviewer_json(_explicit(payload))
    assert _json_to_state_findings(result, "qodo")[0].file == file
    assert "findings" not in payload


@pytest.mark.parametrize(
    "change,diagnostic",
    [
        ({"file": ""}, "file must be a non-empty string"),
        ({"start_line": True}, "start_line must be int"),
        ({"start_line": 0}, "must be positive"),
        ({"end_line": 0}, "must be positive"),
        ({"start_line": 2}, "start_line 2 > end_line 1"),
        ({"content": 3}, "content must be str"),
        ({"content": [1]}, "content list must contain only strings"),
        ({"end_line": 5}, "declares 5 lines but carries 1"),
    ],
)
def test_normalized_excerpt_still_uses_existing_shape_checks(change, diagnostic):
    payload = _payload()
    payload["code_excerpts"][0].update(change)
    original = copy.deepcopy(payload)
    refused = change in ({"start_line": True}, {"content": 3}, {"content": [1]})
    with pytest.raises(ValueError, match="missing required field: findings" if refused else diagnostic):
        validate_reviewer_json(payload)
    with pytest.raises(ValueError, match=diagnostic):
        validate_reviewer_json(_explicit(payload))
    assert payload == original


@pytest.mark.parametrize("entry", [None, {"file": "a.py"}])
def test_malformed_excerpt_entry_is_not_repaired(entry):
    payload = _payload() | {"code_excerpts": [entry]}
    original = copy.deepcopy(payload)
    with pytest.raises(ValueError, match="missing required field: findings"):
        validate_reviewer_json(payload)
    with pytest.raises(ValueError, match=r"code_excerpt\[0\]"):
        validate_reviewer_json(_explicit(payload))
    assert payload == original


@pytest.mark.parametrize("nested", [False, True])
def test_aliases_and_nested_hoist_compose_without_mutating_input(nested):
    payload = _payload()
    alias = {"file": "a.py", "line_start": 1, "line_end": 1, "code": "x = 1"}
    if nested:
        payload = {"findings": [{key: payload[key] for key in CORE} | {"code_excerpts": [alias]}]}
    else:
        payload["code_excerpts"] = [alias]
    original = copy.deepcopy(payload)
    result = validate_reviewer_json(payload)
    assert result["code_excerpts"] == [alias | {"start_line": 1, "end_line": 1, "content": "x = 1"}]
    assert "code_excerpts" not in result["findings"][0]
    assert payload == original
    assert "start_line" not in alias and "content" not in alias


@pytest.mark.parametrize("nested", [False, True])
def test_invalid_canonical_excerpt_value_wins_over_valid_alias_without_mutation(nested):
    payload = _payload()
    excerpt = {"file": "a.py", "line_start": 1, "start_line": None, "line_end": 1, "code": "x = 1"}
    payload["code_excerpts"] = [excerpt]
    if nested:
        payload = {"findings": [{key: payload[key] for key in CORE} | {"code_excerpts": [excerpt]}]}
    original = copy.deepcopy(payload)
    with pytest.raises(ValueError, match="start_line must be int" if nested else "missing required field: findings"):
        validate_reviewer_json(payload)
    assert payload == original
    assert "end_line" not in excerpt and "content" not in excerpt


def test_present_root_evidence_does_not_hoist_nested_excerpts():
    root = _payload()["code_excerpts"]
    finding = {key: _payload()[key] for key in CORE} | {"code_excerpts": [{"bad": "nested"}]}
    payload = {"findings": [finding], "code_excerpts": root}
    original = copy.deepcopy(payload)
    result = validate_reviewer_json(payload)
    assert result == original and payload == original
    assert result["findings"][0]["code_excerpts"] == [{"bad": "nested"}]


@pytest.mark.parametrize("excerpt,expected", [
    ("x = 1", "x = 1"), ("", None), (None, None), (0, None), (False, None),
    ([], None), ({}, None), (1, None), (True, None), (1.5, None), ([1], None), ({"native": 1}, None),
])
def test_explicit_finding_excerpt_is_preserved_without_inference(excerpt, expected):
    payload = _payload() | {"excerpt": excerpt}
    original = copy.deepcopy(payload)
    result = validate_reviewer_json(payload)
    assert result["findings"][0]["excerpt"] == excerpt
    finding = _json_to_state_findings(result, "qodo")[0]
    assert finding.excerpt == expected
    assert payload == original


@pytest.mark.parametrize("shape", ["unknown", "cycle", "deep"])
def test_unknown_metadata_is_retained_without_recursive_copy_or_hooks(shape):
    calls = []

    class NoCopy:
        def __deepcopy__(self, memo):
            calls.append("copy")
            raise AssertionError("normalization must not copy arbitrary metadata")

    metadata = NoCopy()
    if shape == "cycle":
        metadata = {}
        metadata["self"] = metadata
    elif shape == "deep":
        for _ in range(150):
            metadata = {"nested": metadata}
    payload = _payload() | {"metadata": metadata}
    result = validate_reviewer_json(payload)
    assert result["metadata"] is metadata and payload["metadata"] is metadata
    assert "metadata" not in result["findings"][0]
    assert "findings" not in payload and calls == []


@pytest.mark.parametrize(
    "change,valid",
    [({}, True), ({"content": "x = 99"}, False), ({"file": "other.py"}, False),
     ({"start_line": 100, "end_line": 100}, False)],
)
def test_real_frozen_diff_checks_remain_authoritative(change, valid):
    payload = _payload()
    payload["code_excerpts"][0].update(change)
    result = validate_reviewer_json(payload)
    explicit = validate_reviewer_json(_explicit(payload))
    assert result["findings"] == explicit["findings"]
    assert bool(validate_excerpts_against_diff(DIFF, result["code_excerpts"])) is not valid


@pytest.mark.parametrize("raw", ['{"file":', "[]", 'prefix {"file":"a.py"}', "```json\n{}"])
def test_malformed_json_and_nonobjects_keep_visible_refusal(raw):
    with pytest.raises(ValueError):
        validate_reviewer_json(raw)


@pytest.mark.parametrize("error_type", [RuntimeError, AttributeError])
def test_root_owned_excerpt_subclass_keeps_original_refusal(error_type):
    """Only caller-supplied dictionaries can carry this overridden str hook."""
    calls = []

    class BadExcerpt(str):
        def strip(self, *args, **kwargs):
            calls.append("strip")
            raise error_type("synthetic owned-excerpt hook")

    excerpt = BadExcerpt("x = 1")
    payload = _payload() | {"excerpt": excerpt}
    with pytest.raises(ValueError, match="missing required field: findings"):
        validate_reviewer_json(payload)
    assert calls == [] and payload["excerpt"] is excerpt and "findings" not in payload

    explicit = _explicit(payload)
    explicit["findings"][0]["excerpt"] = excerpt
    validated = validate_reviewer_json(explicit)
    assert validated["findings"][0]["excerpt"] is excerpt
    with pytest.raises(error_type, match="synthetic owned-excerpt hook"):
        _json_to_state_findings(validated, "qodo")
    assert calls == ["strip"]


@pytest.mark.parametrize("error_type", [RuntimeError, AttributeError])
def test_opaque_root_owned_excerpt_keeps_original_refusal(error_type):
    calls = []

    class Opaque:
        @property
        def __class__(self):
            calls.append("__class__")
            raise error_type("synthetic class hook")

    excerpt = Opaque()
    payload = _payload() | {"excerpt": excerpt}
    with pytest.raises(ValueError, match="missing required field: findings"):
        validate_reviewer_json(payload)
    assert calls == [] and payload["excerpt"] is excerpt and "findings" not in payload

    # Known-answer hook control also distinguishes CPython's swallowed AttributeError.
    with pytest.raises(error_type, match="synthetic class hook"):
        _ = excerpt.__class__
    assert calls == ["__class__"]


def test_raw_dictionary_copy_avoids_mapping_hooks():
    calls = []

    class PoisonDict(dict):
        def __iter__(self):
            return dict.__iter__(self)

        def keys(self):
            calls.append("keys")
            raise RuntimeError("synthetic dictionary-copy hook")

    metadata = object()
    payload = PoisonDict(_payload() | {"metadata": metadata})
    assert list(payload) == [key for key, _ in dict.items(payload)]
    result = validate_reviewer_json(payload)
    assert type(result) is dict and result["metadata"] is metadata and "findings" not in payload
    assert result["findings"] == [{key: payload[key] for key in CORE}] and calls == []


def test_excerpt_dictionary_copy_avoids_mapping_hooks():
    calls = []

    class PoisonDict(dict):
        def __iter__(self):
            return dict.__iter__(self)

        def keys(self):
            calls.append("keys")
            raise RuntimeError("synthetic dictionary-copy hook")

    metadata = object()
    excerpt = PoisonDict(_payload()["code_excerpts"][0] | {"metadata": metadata})
    assert list(excerpt) == [key for key, _ in dict.items(excerpt)]
    payload = _payload() | {"code_excerpts": [excerpt]}
    result = validate_reviewer_json(payload)
    copied = result["code_excerpts"][0]
    assert type(copied) is dict and copied is not excerpt and copied["metadata"] is metadata
    assert result["findings"] == [{key: payload[key] for key in CORE}] and calls == []


@pytest.mark.parametrize("container", [list, dict])
def test_native_optional_excerpt_container_retains_opaque_children(container):
    opaque = object()
    excerpt = [opaque] if container is list else {"opaque": opaque}
    payload = _payload() | {"excerpt": excerpt}
    result = validate_reviewer_json(payload)
    assert result["findings"][0]["excerpt"] is excerpt
    assert _json_to_state_findings(result, "qodo")[0].excerpt is None
    assert payload["excerpt"] is excerpt and "findings" not in payload


@pytest.mark.parametrize("site", ["raw", "excerpt"])
def test_exact_dictionary_copy_retains_cached_key_hash(site):
    armed = [False]

    class Key:
        def __hash__(self):
            if armed[0]:
                raise RuntimeError("synthetic key hash hook")
            return 17

    key, opaque = Key(), object()
    root = _payload()
    payload = _explicit(root)
    target = payload if site == "raw" else payload["code_excerpts"][0]
    target[key] = opaque
    armed[0] = True
    result = validate_reviewer_json(payload)
    copied = result if site == "raw" else result["code_excerpts"][0]
    assert next(value for item, value in dict.items(copied) if item is key) is opaque
    with pytest.raises(RuntimeError, match="synthetic key hash hook"):
        hash(key)


@pytest.mark.parametrize("field", ROOT_EVIDENCE_FAULTS)
def test_root_evidence_hooks_refuse_before_legacy_checks(field):
    payload, calls = _root_evidence_fault(field)
    with pytest.raises(ValueError, match="missing required field: findings"):
        validate_reviewer_json(payload)
    assert calls == [] and "findings" not in payload
    explicit = _explicit(payload)
    if field == "content-string-subclass":
        assert len(validate_reviewer_json(explicit)["code_excerpts"]) == 1
        assert calls == []
    else:
        with pytest.raises(RuntimeError, match="synthetic evidence"):
            validate_reviewer_json(explicit)
        assert calls


@pytest.mark.parametrize("encoding", ["dict", "json"])
def test_null_root_filename_refuses_before_real_receipt_writer(tmp_path, encoding):
    payload = _payload() | {"file": "a\x00.py"}
    raw = json.dumps(payload) if encoding == "json" else payload
    with pytest.raises(ValueError, match="missing required field: findings"):
        validate_reviewer_json(raw)
    assert "findings" not in payload
    # Claimed arrays retain the legacy writer failure; ROOT admission is the fix.
    explicit = validate_reviewer_json(_explicit(payload))
    findings = _json_to_state_findings(explicit, "qodo")
    excerpts = _collect_excerpts(explicit, pass_name="qodo")
    (tmp_path / "a.py").write_text("x = 1\n")
    with pytest.raises(ValueError, match="embedded null byte"):
        write_receipts(tmp_path / "receipts", 0, findings, "synthetic-diff", [], tmp_path,
                       reviewer_excerpts=excerpts, manifest="declared")


@pytest.mark.parametrize("encoding", ["dict", "json"])
def test_native_root_filename_writes_real_receipt_anchor(tmp_path, encoding):
    payload = _payload()
    raw = json.dumps(payload) if encoding == "json" else payload
    validated = validate_reviewer_json(raw)
    (tmp_path / "a.py").write_text("x = 1\n")
    paths = write_receipts(tmp_path / "receipts", 0, _json_to_state_findings(validated, "qodo"),
                           "synthetic-diff", [], tmp_path,
                           reviewer_excerpts=_collect_excerpts(validated, pass_name="qodo"), manifest="declared")
    assert len(paths) == 3 and json.loads(paths[0].read_text())["anchors"][0]["text"] == "x = 1"
    assert "findings" not in payload
