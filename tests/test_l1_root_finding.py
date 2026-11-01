# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Root findings traverse real L1 consumers and the frozen receipt validator."""

import copy
import json
import math
import sys
from pathlib import Path

import pytest

from code_forge.baseline import ResolvedReview
from code_forge.factories import build_l1_provider
from code_forge.llm_invoke import LLMResult
from code_forge.outlet_c import _run_chunk
from code_forge.receipt import write_receipts
from code_forge.state import Disposition
from code_forge.verify import validate_excerpts_against_diff

DIFF = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x = 0\n+x = 1\n"
PASSES = ("qodo", "expert", "adversarial")
INVALID_ROOT_CORE = [
    *[("file", value) for value in (None, False, 1, 1.5, [], {}, ["a.py"], {"path": "a.py"}, "", " ")],
    *[
        ("line", value)
        for value in (None, False, True, [], {}, "", "1", 1.0, float("inf"), float("nan"), 10**400)
    ],
    *[
        ("description", value)
        for value in (None, False, 1, 1.5, [], {}, ["invalid"], {"text": "invalid"}, "", " ")
    ],
]
INVALID_ROOT_CORE = [
    pytest.param(field, value, id=f"{field}-{index}")
    for index, (field, value) in enumerate(INVALID_ROOT_CORE)
]


def _payload():
    return {
        "file": "a.py",
        "line": 1,
        "severity": "P2",
        "description": "synthetic boundary finding",
        "pass_name": "model-root-pass",
        "code_excerpts": [
            {
                "file": "a.py",
                "start_line": 1,
                "end_line": 1,
                "content": "x = 1",
                "pass_name": "model-excerpt-pass",
            }
        ],
    }


@pytest.fixture
def external_guard():
    """Do not permit a transport stub omission to start any external work."""
    active = [False]
    forbidden = []

    def audit(event, args):
        if active[0] and event.startswith(
            (
                "socket.connect",
                "socket.getaddrinfo",
                "socket.sendto",
                "subprocess.Popen",
                "os.system",
                "os.posix_spawn",
                "os.fork",
                "os.exec",
            )
        ):
            forbidden.append(event)
            raise AssertionError("unexpected external operation: " + event)

    sys.addaudithook(audit)
    active[0] = True
    yield forbidden
    active[0] = False
    assert forbidden == []


def _consume(monkeypatch, consumer, payload, encoding):
    raw = json.dumps(payload) if encoding == "json" else payload
    calls = []
    attempts = []
    if consumer == "factory":

        def transport(*args, **kwargs):
            calls.append(args)
            return LLMResult(raw)

        monkeypatch.setattr("code_forge.llm_invoke.llm_invoke", transport)
        provider = build_l1_provider(
            "auto", ResolvedReview([Path("a.py")], None, DIFF, "git"), max_attempts=1
        )
        findings, excerpts, _, _ = provider()
        attempts = provider.attempted_excerpts
    else:

        def spawn(pass_name, diff):
            calls.append((pass_name, diff))
            return raw

        findings, excerpts, _, _ = _run_chunk(DIFF, spawn, PASSES, attempted=attempts)
    assert len(calls) == 3
    return findings, excerpts, attempts


@pytest.mark.parametrize("consumer", ["factory", "outlet-c"])
@pytest.mark.parametrize("encoding", ["dict", "json"])
def test_root_finding_reaches_real_l1_consumers(monkeypatch, external_guard, consumer, encoding):
    payload = _payload()
    original = copy.deepcopy(payload)
    findings, excerpts, attempts = _consume(monkeypatch, consumer, payload, encoding)
    assert attempts == []
    assert len(findings) == len(excerpts) == 3
    assert len({finding.fingerprint for finding in findings}) == 3
    for pass_name, finding, excerpt in zip(PASSES, findings, excerpts, strict=True):
        assert finding.source == "L1" and finding.disposition is Disposition.UNCERTAIN
        assert finding.description == f"[{pass_name}] synthetic boundary finding"
        assert finding.file == "a.py" and finding.line_range == [1, 1] and finding.severity == "P2"
        assert finding.excerpt is None
        assert excerpt["pass_name"] == pass_name
    assert validate_excerpts_against_diff(DIFF, excerpts) == []
    assert payload == original and external_guard == []


@pytest.mark.parametrize("consumer", ["factory", "outlet-c"])
def test_explicit_finding_excerpt_reaches_consumer_unchanged(monkeypatch, external_guard, consumer):
    payload = _payload() | {"excerpt": "x = 1"}
    findings, _, attempts = _consume(monkeypatch, consumer, payload, "dict")
    assert attempts == [] and len(findings) == 3
    assert all(f.excerpt == "x = 1" and f.disposition is Disposition.UNCERTAIN for f in findings)
    assert "findings" not in payload


@pytest.mark.parametrize("consumer", ["factory", "outlet-c"])
@pytest.mark.parametrize("fault", ["empty", "severity", "excerpt-shape"])
def test_rejected_root_payload_stays_exact_attempted_evidence(
    monkeypatch, external_guard, tmp_path, consumer, fault
):
    payload = _payload()
    if fault == "empty":
        payload["code_excerpts"] = []
    elif fault == "severity":
        payload["severity"] = "P9"
    else:
        payload["code_excerpts"][0]["start_line"] = None
    original = copy.deepcopy(payload)
    findings, excerpts, attempts = _consume(monkeypatch, consumer, payload, "dict")
    assert excerpts == []
    assert len(attempts) == 3
    for pass_name, attempt in zip(PASSES, attempts, strict=True):
        assert attempt == original | {"pass_name": pass_name}
        assert "findings" not in attempt
    assert all(f.source == "INFRA" for f in findings)
    assert len(findings) == (0 if fault == "empty" else 3)
    assert payload == original
    paths = write_receipts(
        tmp_path / "receipts",
        0,
        findings,
        "synthetic-diff",
        [],
        tmp_path,
        reviewer_excerpts=excerpts,
        attempted_excerpts=attempts,
        diff_text=DIFF,
        manifest="declared",
    )
    receipts = [json.loads(path.read_text()) for path in paths]
    expected = "incomplete" if fault == "empty" else "schema_fail"
    assert all(receipt["pass_status"] == expected for receipt in receipts)
    assert len(list((tmp_path / "receipts" / "attempted").glob("*.json"))) == 3


@pytest.mark.parametrize("consumer", ["factory", "outlet-c"])
@pytest.mark.parametrize("fault", ["valid", "literal", "file", "range"])
def test_real_receipt_writer_checks_normalized_evidence(
    monkeypatch, external_guard, tmp_path, consumer, fault
):
    payload = _payload()
    if fault == "literal":
        payload["code_excerpts"][0]["content"] = "x = 99"
    elif fault == "file":
        payload["code_excerpts"][0]["file"] = "other.py"
    elif fault == "range":
        payload["code_excerpts"][0].update(start_line=100, end_line=100)
    findings, excerpts, attempts = _consume(monkeypatch, consumer, payload, "json")
    assert len(findings) == len(excerpts) == 3 and attempts == []
    assert all(f.disposition is Disposition.UNCERTAIN and f.excerpt is None for f in findings)
    (tmp_path / "a.py").write_text("x = 1\n")
    paths = write_receipts(
        tmp_path / "receipts",
        0,
        findings,
        "synthetic-diff",
        [Path("a.py")],
        tmp_path,
        diff_text=DIFF,
        reviewer_excerpts=excerpts,
        manifest="declared",
    )
    receipts = [json.loads(path.read_text()) for path in paths]
    expected = "completed" if fault == "valid" else "schema_fail"
    assert all(receipt["pass_status"] == expected for receipt in receipts)
    assert all(receipt["findings_count"] == 1 for receipt in receipts)
    # A completed receipt and envelope evidence do not confirm the finding.
    assert all(receipt["findings"][0]["disposition"] == "UNCERTAIN" for receipt in receipts)
    assert bool(validate_excerpts_against_diff(DIFF, excerpts)) is (fault != "valid")


@pytest.mark.parametrize("consumer", ["factory", "outlet-c"])
def test_explicit_empty_findings_remain_empty_at_real_consumer(monkeypatch, external_guard, consumer):
    payload = _payload() | {"findings": []}
    findings, excerpts, attempts = _consume(monkeypatch, consumer, payload, "dict")
    assert findings == [] and len(excerpts) == 3 and attempts == []
    assert payload["findings"] == []


@pytest.mark.parametrize("consumer", ["factory", "outlet-c"])
@pytest.mark.parametrize("severity", [[], {}])
def test_invalid_root_severity_uses_schema_failure_path(monkeypatch, external_guard, consumer, severity):
    payload = _payload() | {"severity": severity}
    original = copy.deepcopy(payload)
    findings, excerpts, attempts = _consume(monkeypatch, consumer, payload, "dict")
    assert len(findings) == len(attempts) == 3 and excerpts == []
    assert all(f.source == "INFRA" and f.file == "<schema-validation>" for f in findings)
    assert attempts == [original | {"pass_name": name} for name in PASSES]
    assert payload == original and "findings" not in payload


@pytest.mark.parametrize("consumer", ["factory", "outlet-c"])
@pytest.mark.parametrize("error_type", [RuntimeError, AttributeError])
def test_root_owned_excerpt_subclass_uses_original_consumer_schema_path(
    monkeypatch, external_guard, consumer, error_type
):
    """Programmatic dictionaries retain schema refusal without invoking a hook."""
    calls = []

    class BadExcerpt(str):
        def strip(self, *args, **kwargs):
            calls.append("strip")
            raise error_type("synthetic owned-excerpt hook")

    excerpt = BadExcerpt("x = 1")
    payload = _payload() | {"excerpt": excerpt}
    findings, excerpts, attempts = _consume(monkeypatch, consumer, payload, "dict")
    assert len(findings) == 3 and excerpts == attempts == []
    assert all(f.source == "INFRA" and f.file == "<schema-validation>" for f in findings)
    assert all("missing required field: findings" in f.description for f in findings)
    assert calls == [] and payload["excerpt"] is excerpt and "findings" not in payload

    explicit = payload | {
        "findings": [
            {key: payload[key] for key in ("file", "line", "severity", "description", "excerpt")}
        ]
    }
    with pytest.raises(error_type, match="synthetic owned-excerpt hook"):
        _consume(monkeypatch, consumer, explicit, "dict")
    assert calls == ["strip"] and external_guard == []


@pytest.mark.parametrize("consumer", ["factory", "outlet-c"])
def test_explicit_unhashable_severity_keeps_existing_consumer_failure(
    monkeypatch, external_guard, consumer
):
    root = _payload() | {"severity": []}
    payload = root | {
        "findings": [{key: root[key] for key in ("file", "line", "severity", "description")}]
    }
    with pytest.raises(TypeError, match="unhashable"):
        _consume(monkeypatch, consumer, payload, "dict")
    assert len(payload["findings"]) == 1


@pytest.mark.parametrize("consumer", ["factory", "outlet-c"])
@pytest.mark.parametrize("field,value", INVALID_ROOT_CORE)
def test_invalid_root_core_fields_use_original_consumer_schema_path(
    monkeypatch, external_guard, consumer, field, value
):
    payload = _payload() | {field: value}
    findings, excerpts, attempts = _consume(monkeypatch, consumer, payload, "dict")
    assert len(findings) == len(attempts) == 3 and excerpts == []
    assert all(f.source == "INFRA" and f.file == "<schema-validation>" for f in findings)
    if isinstance(value, float) and math.isnan(value):
        assert all(math.isnan(attempt[field]) for attempt in attempts)
        assert [attempt["pass_name"] for attempt in attempts] == list(PASSES)
    else:
        assert attempts == [payload | {"pass_name": name} for name in PASSES]
    assert "findings" not in payload


@pytest.mark.parametrize("consumer", ["factory", "outlet-c"])
@pytest.mark.parametrize("encoding", ["dict", "json"])
@pytest.mark.parametrize("file", ["a\ud800.py", "a\udfff.py"])
def test_unencodable_root_filename_uses_original_consumer_schema_path(
    monkeypatch, external_guard, consumer, encoding, file
):
    payload = _payload() | {"file": file}
    original = copy.deepcopy(payload)
    findings, excerpts, attempts = _consume(monkeypatch, consumer, payload, encoding)
    assert len(findings) == len(attempts) == 3 and excerpts == []
    assert all(f.source == "INFRA" and f.file == "<schema-validation>" for f in findings)
    assert all("missing required field: findings" in f.description for f in findings)
    assert attempts == [original | {"pass_name": name} for name in PASSES]
    assert payload == original and "findings" not in payload


@pytest.mark.parametrize("consumer", ["factory", "outlet-c"])
@pytest.mark.parametrize("error_type", [RuntimeError, AttributeError])
def test_opaque_root_owned_excerpt_uses_original_consumer_schema_path(
    monkeypatch, external_guard, consumer, error_type
):
    calls = []

    class Opaque:
        @property
        def __class__(self):
            calls.append("__class__")
            raise error_type("synthetic class hook")

    payload = _payload() | {"excerpt": Opaque()}
    findings, excerpts, attempts = _consume(monkeypatch, consumer, payload, "dict")
    assert len(findings) == 3 and excerpts == attempts == []
    assert all(f.source == "INFRA" and f.file == "<schema-validation>" for f in findings)
    assert all("missing required field: findings" in f.description for f in findings)
    assert calls == [] and "findings" not in payload
    with pytest.raises(error_type, match="synthetic class hook"):
        _ = payload["excerpt"].__class__
    assert calls == ["__class__"] and external_guard == []


@pytest.mark.parametrize("consumer", ["factory", "outlet-c"])
def test_raw_dictionary_copy_reaches_consumers_without_mapping_hooks(
    monkeypatch, external_guard, consumer
):
    calls = []

    class PoisonDict(dict):
        def __iter__(self):
            return dict.__iter__(self)

        def keys(self):
            calls.append("keys")
            raise RuntimeError("synthetic dictionary-copy hook")

    payload = PoisonDict(_payload())
    assert list(payload) == [key for key, _ in dict.items(payload)]
    findings, excerpts, attempts = _consume(monkeypatch, consumer, payload, "dict")
    assert len(findings) == len(excerpts) == 3 and attempts == []
    assert all(f.source == "L1" and f.file == "a.py" for f in findings)
    assert calls == [] and "findings" not in payload and external_guard == []


@pytest.mark.parametrize("consumer", ["factory", "outlet-c"])
def test_excerpt_dictionary_copy_reaches_consumers_without_mapping_hooks(
    monkeypatch, external_guard, consumer
):
    calls = []

    class PoisonDict(dict):
        def __iter__(self):
            return dict.__iter__(self)

        def keys(self):
            calls.append("keys")
            raise RuntimeError("synthetic dictionary-copy hook")

    excerpt = PoisonDict(_payload()["code_excerpts"][0])
    assert list(excerpt) == [key for key, _ in dict.items(excerpt)]
    payload = _payload() | {"code_excerpts": [excerpt]}
    findings, excerpts, attempts = _consume(monkeypatch, consumer, payload, "dict")
    assert len(findings) == len(excerpts) == 3 and attempts == []
    assert all(f.source == "L1" and f.file == "a.py" for f in findings)
    assert calls == [] and payload["code_excerpts"][0] is excerpt and external_guard == []


ROOT_EVIDENCE_FAULTS = [
    "file",
    "content",
    "code",
    "start_line",
    "end_line",
    "line_start",
    "line_end",
    "content-list",
    "content-item",
    "content-string-subclass",
    "outer-list",
    "entry",
]


def _root_evidence_fault(field):
    calls = []

    class BadText(str):
        def strip(self, *args, **kwargs):
            calls.append("strip")
            raise RuntimeError("synthetic evidence text hook")

    class BadInt(int):
        def __le__(self, other):
            calls.append("le")
            raise RuntimeError("synthetic evidence coordinate hook")

    class BadList(list):
        def __iter__(self):
            calls.append("iter")
            raise RuntimeError("synthetic evidence list hook")

    class Opaque:
        @property
        def __class__(self):
            calls.append("class")
            raise RuntimeError("synthetic evidence class hook")

    payload = _payload()
    excerpt = payload["code_excerpts"][0]
    if field in ("file", "content"):
        excerpt[field] = BadText(excerpt[field])
    elif field == "code":
        excerpt["code"] = BadText(excerpt.pop("content"))
    elif field in ("start_line", "end_line", "line_start", "line_end"):
        canonical = {"line_start": "start_line", "line_end": "end_line"}.get(field, field)
        excerpt[field] = BadInt(excerpt[canonical])
        if field != canonical:
            del excerpt[canonical]
    elif field == "content-list":
        excerpt["content"] = BadList(["x = 1"])
    elif field == "content-item":
        excerpt["content"] = [Opaque()]
    elif field == "content-string-subclass":
        excerpt["content"] = [BadText("x = 1")]
    elif field == "outer-list":
        payload["code_excerpts"] = BadList(payload["code_excerpts"])
    else:
        payload["code_excerpts"] = [Opaque()]
    return payload, calls


def _consume_root_evidence(monkeypatch, consumer, payload, encoding):
    if consumer != "cli":
        findings, excerpts, attempts = _consume(monkeypatch, consumer, payload, encoding)
        return findings, excerpts, attempts, []
    from code_forge.cli import _run_test_assertion_review

    raw = json.dumps(payload) if encoding == "json" else payload
    calls, outcomes = [], []

    def transport(*args, **kwargs):
        calls.append("invoke")
        return LLMResult(raw)

    monkeypatch.setattr("code_forge.llm_invoke.llm_invoke", transport)
    findings = _run_test_assertion_review(DIFF.replace("a.py", "test_a.py"), on_outcome=outcomes.append)
    assert calls == ["invoke"]
    return findings, [], [], outcomes


@pytest.mark.parametrize("consumer", ["factory", "outlet-c", "cli"])
@pytest.mark.parametrize("field", ROOT_EVIDENCE_FAULTS)
def test_root_evidence_hooks_use_original_consumer_refusal(monkeypatch, external_guard, consumer, field):
    payload, calls = _root_evidence_fault(field)
    findings, excerpts, attempts, outcomes = _consume_root_evidence(
        monkeypatch, consumer, payload, "dict"
    )
    assert excerpts == attempts == [] and calls == [] and "findings" not in payload
    if consumer == "cli":
        assert findings == [] and outcomes == ["failed: error"]
    else:
        assert len(findings) == 3 and all(
            f.source == "INFRA" and f.file == "<schema-validation>" for f in findings
        )
        assert all("missing required field: findings" in f.description for f in findings)
    assert external_guard == []


@pytest.mark.parametrize("consumer", ["factory", "outlet-c", "cli"])
@pytest.mark.parametrize("encoding", ["dict", "json"])
@pytest.mark.parametrize("shape", ["native", "aliases", "list-content"])
def test_native_root_evidence_remains_accepted(monkeypatch, external_guard, consumer, encoding, shape):
    payload = _payload()
    if shape == "aliases":
        payload["code_excerpts"] = [{"file": "a.py", "line_start": 1, "line_end": 1, "code": "x = 1"}]
    elif shape == "list-content":
        payload["code_excerpts"][0]["content"] = ["x = 1"]
    original = copy.deepcopy(payload)
    findings, excerpts, attempts, outcomes = _consume_root_evidence(
        monkeypatch, consumer, payload, encoding
    )
    assert attempts == [] and payload == original
    if consumer == "cli":
        assert len(findings) == 1 and outcomes == ["done"]
    else:
        assert len(findings) == len(excerpts) == 3 and all(f.source == "L1" for f in findings)
    assert external_guard == []


@pytest.mark.parametrize("consumer", ["factory", "outlet-c", "cli"])
@pytest.mark.parametrize("encoding", ["dict", "json"])
def test_null_root_filename_consumers_write_schema_failure_receipts(
    monkeypatch, external_guard, tmp_path, consumer, encoding
):
    payload = _payload() | {"file": "a\x00.py"}
    findings, excerpts, attempts, outcomes = _consume_root_evidence(
        monkeypatch, consumer, payload, encoding
    )
    assert excerpts == [] and "findings" not in payload
    if consumer == "cli":
        assert findings == [] and outcomes == ["failed: error"]
    else:
        assert len(findings) == len(attempts) == 3
        assert all(f.source == "INFRA" and f.file == "<schema-validation>" for f in findings)
        assert all("missing required field: findings" in f.description for f in findings)
        (tmp_path / "a.py").write_text("x = 1\n")
        paths = write_receipts(
            tmp_path / "receipts",
            0,
            findings,
            "synthetic-diff",
            [],
            tmp_path,
            reviewer_excerpts=excerpts,
            attempted_excerpts=attempts,
            diff_text=DIFF,
            manifest="declared",
        )
        receipts = [json.loads(path.read_text()) for path in paths]
        assert len(receipts) == 3 and all(
            r["pass_status"] == "schema_fail" and r["anchors"] == [] for r in receipts
        )
    assert external_guard == []
