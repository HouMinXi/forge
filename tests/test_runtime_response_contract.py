# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Delivered-content contract for the actual runtime advisory consumer.

The transport is stubbed at llm_invoke: these controls do not establish
provider-root provenance for content already decoded by the transport.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import json

import pytest

from code_forge import runtime
from code_forge.advisory import AdvisoryFinding


DIFF = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n"
REASON = "LLM response parse error: invalid runtime response"
NESTED_TRUNCATION = (
    '{"surfaces":["network"],"findings":[{"file":"a.py","line":1,'
    '"description":"risk","details":{"surfaces":[],"findings":[]}}'
)
# Complete JSON with deeply nested inert metadata; decoder capacity varies.
DEEPLY_NESTED_JSON = '{"surfaces":[],"findings":[],"metadata":' + "[" * 10_000 + "0" + "]" * 10_000 + "}"


def _finding(**changes):
    return {"file": "a.py", "line": 7, "description": "risk", **changes}


def _payload(**changes):
    return {"surfaces": ["network"], "findings": [_finding()], **changes}


@pytest.fixture
def invoke(monkeypatch):
    """No test in this module can reach a provider, including error paths."""
    stub = Mock(side_effect=AssertionError("unexpected provider invocation"))
    monkeypatch.setattr(runtime, "llm_invoke", stub)
    return stub


def _deliver(invoke, content):
    invoke.side_effect = None
    invoke.return_value = SimpleNamespace(content=content)


def _assert_refusal(runner, result):
    assert result == [
        AdvisoryFinding(
            id="runtime-skipped",
            axis="RUNTIME",
            file="",
            line_range=[0, 0],
            description="RUNTIME axis SKIPPED: " + REASON,
            attribution="runtime-axis/infra-error",
        )
    ]
    assert runner.infra_errors == [REASON]
    assert runner.last_surfaces == []


@pytest.mark.parametrize("key", ["surfaces", "findings"])
@pytest.mark.parametrize("value", [None, False, 0, "", "network", {}, (), {"network"}])
def test_requires_actual_lists(key, value, invoke, tmp_path):
    runner = runtime.RuntimeRunner(backend=object())
    _deliver(invoke, _payload(**{key: value}))
    _assert_refusal(runner, runner.run(DIFF, tmp_path))


@pytest.mark.parametrize("key", ["surfaces", "findings"])
def test_requires_both_keys(key, invoke, tmp_path):
    content = {"surfaces": [], "findings": []}
    del content[key]
    runner = runtime.RuntimeRunner(backend=object())
    _deliver(invoke, content)
    _assert_refusal(runner, runner.run(DIFF, tmp_path))


@pytest.mark.parametrize("value", [None, False, 3, 1.5, {}, [], ["network"]])
def test_every_surface_must_be_a_string(value, invoke, tmp_path):
    runner = runtime.RuntimeRunner(backend=object())
    _deliver(invoke, _payload(surfaces=["valid-first", value]))
    _assert_refusal(runner, runner.run(DIFF, tmp_path))


@pytest.mark.parametrize("value", [None, False, 3, "private-bad-sibling", [], ()])
def test_every_finding_must_be_an_object(value, invoke, tmp_path):
    runner = runtime.RuntimeRunner(backend=object())
    _deliver(invoke, _payload(findings=[_finding(), value]))
    _assert_refusal(runner, runner.run(DIFF, tmp_path))


@pytest.mark.parametrize("key", ["file", "line", "description"])
def test_requires_every_consumed_finding_field(key, invoke, tmp_path):
    finding = _finding()
    del finding[key]
    runner = runtime.RuntimeRunner(backend=object())
    _deliver(invoke, _payload(findings=[finding]))
    _assert_refusal(runner, runner.run(DIFF, tmp_path))


@pytest.mark.parametrize("key", ["file", "description", "surface"])
@pytest.mark.parametrize("value", [None, False, 0, 1.5, [], {}])
def test_string_fields_are_not_coerced(key, value, invoke, tmp_path):
    runner = runtime.RuntimeRunner(backend=object())
    _deliver(invoke, _payload(findings=[_finding(**{key: value})]))
    _assert_refusal(runner, runner.run(DIFF, tmp_path))


@pytest.mark.parametrize("value", [None, True, False, 1.0, 1.5, "1", "private-line", [], {}])
def test_line_requires_integer_excluding_bool(value, invoke, tmp_path):
    runner = runtime.RuntimeRunner(backend=object())
    _deliver(invoke, _payload(findings=[_finding(line=value)]))
    _assert_refusal(runner, runner.run(DIFF, tmp_path))


@pytest.mark.parametrize("line", [-(10**30), -1, 0, 1, 10**30])
def test_integer_boundaries_preserved(line, invoke, tmp_path):
    runner = runtime.RuntimeRunner(backend=object())
    _deliver(invoke, _payload(findings=[_finding(line=line)]))
    result = runner.run(DIFF, tmp_path)
    assert result[0].line_range == (line, line)
    assert runner.infra_errors == []


@pytest.mark.parametrize("wrapper", [lambda p: p, lambda p: [p], json.dumps, lambda p: json.dumps([p])])
@pytest.mark.parametrize("empty", [False, True])
def test_valid_complete_payloads_preserved(wrapper, empty, invoke, tmp_path):
    content = {"surfaces": [], "findings": []} if empty else _payload()
    runner = runtime.RuntimeRunner(backend=object())
    _deliver(invoke, wrapper(content))
    result = runner.run(DIFF, tmp_path)
    expected = (
        []
        if empty
        else [
            AdvisoryFinding("runtime-0", "RUNTIME", "a.py", [7, 7], "risk", "runtime-axis/llm"),
            AdvisoryFinding(
                "runtime-smoke-summary",
                "RUNTIME",
                "",
                [0, 0],
                "smoke: 0/1 surfaces verified; NOT VERIFIED: [network]",
                "runtime-axis/smoke-evidence",
            ),
        ]
    )
    assert result == expected
    assert runner.last_surfaces == content["surfaces"]
    assert runner.infra_errors == []
    invoke.assert_called_once()


@pytest.mark.parametrize("surface", ["", "unlisted"])
def test_optional_surface_blank_strings_and_inert_metadata_preserved(surface, invoke, tmp_path):
    content = _payload(
        surfaces=[""],
        findings=[_finding(file="", line=0, description="", surface=surface, extra={"x": 1})],
        code_excerpts={"a.py": "transport-added excerpt"},
    )
    before = deepcopy(content)
    surfaces, findings = runtime._parse_llm_response(content)
    assert surfaces == content["surfaces"]
    assert findings == content["findings"]
    runner = runtime.RuntimeRunner(backend=object())
    _deliver(invoke, content)
    result = runner.run(DIFF, tmp_path)
    assert result[0] == AdvisoryFinding("runtime-0", "RUNTIME", "", [0, 0], "", "runtime-axis/llm")
    assert runner.infra_errors == []
    assert content == before


@pytest.mark.parametrize("content", [None, 1, True, (), [], [_payload(), _payload()], [[]]])
def test_rejects_unsupported_decoded_roots(content, invoke, tmp_path):
    runner = runtime.RuntimeRunner(backend=object())
    _deliver(invoke, content)
    _assert_refusal(runner, runner.run(DIFF, tmp_path))


@pytest.mark.parametrize(
    "content",
    [
        "",
        "null",
        "true",
        "1",
        '"surfaces"',
        "[]",
        "[{} , {}]",
        '{"surfaces":[],"findings":[]} {"surfaces":[],"findings":[]}',
        'prefix {"surfaces":[],"findings":[]}',
        '{"surfaces":[],"findings":[]} trailing-private-content',
        '```json\n{"surfaces":[],"findings":[]}\n```',
        NESTED_TRUNCATION,
        '{"surfaces":[],"surfaces":["private"],"findings":[]}',
        '{"surfaces":[],"findings":[],"findings":[]}',
        '{"surfaces":[],"findings":[],"meta":{"duplicate":1,"duplicate":2}}',
        '{"surfaces":[],"findings":[],"meta":{"a":1,"\\u0061":2}}',
        '{"surfaces":["network"],"findings":[{"file":"a.py","line":1,"line":2,"description":"risk"}]}',
        '{"surfaces":[],"findings":[],"meta":NaN}',
        '{"surfaces":[],"findings":[],"meta":Infinity}',
        '{"surfaces":[],"findings":[],"meta":-Infinity}',
        '{"surfaces":[],"findings":[],"meta":1e999}',
        '{"surfaces":[],"findings":[],"meta":-1e999}',
        '[{"surfaces":[],"findings":[],"meta":NaN}]',
    ],
)
def test_raw_json_requires_one_complete_unambiguous_root(content, invoke, tmp_path):
    runner = runtime.RuntimeRunner(backend=object())
    _deliver(invoke, content)
    _assert_refusal(runner, runner.run(DIFF, tmp_path))


def test_complete_raw_json_with_whitespace_and_finite_metadata(invoke, tmp_path):
    runner = runtime.RuntimeRunner(backend=object())
    _deliver(invoke, '\n {"surfaces":[],"findings":[],"metadata":[1.5,1e300]} \t')
    assert runner.run(DIFF, tmp_path) == []
    assert runner.infra_errors == []


@pytest.mark.parametrize("bad", [None, _finding(line="private-line"), _finding(description=None)])
def test_invalid_second_finding_cannot_publish_prefix_or_read_receipts(
    bad, invoke, monkeypatch, tmp_path
):
    runner = runtime.RuntimeRunner(backend=object())
    receipt_reader = Mock(side_effect=AssertionError("receipts read before full validation"))
    monkeypatch.setattr(runtime, "read_smoke_receipts", receipt_reader)
    _deliver(invoke, _payload(findings=[_finding(), bad]))
    _assert_refusal(runner, runner.run(DIFF, tmp_path))
    receipt_reader.assert_not_called()


@pytest.mark.parametrize("error", [ValueError, TypeError, KeyError])
def test_conversion_failure_is_atomic_and_sanitized(error, invoke, monkeypatch, tmp_path):
    runner = runtime.RuntimeRunner(backend=object())
    converted = []

    def convert(**kwargs):
        assert runner.last_surfaces == []
        converted.append(kwargs["id"])
        if kwargs["id"] == "runtime-1":
            raise error("private-conversion-value")
        return AdvisoryFinding(**kwargs)

    monkeypatch.setattr(runtime, "AdvisoryFinding", convert)
    receipts = Mock(side_effect=AssertionError("receipts read before conversion"))
    monkeypatch.setattr(runtime, "read_smoke_receipts", receipts)
    _deliver(invoke, _payload(findings=[_finding(), _finding(line=8)]))
    _assert_refusal(runner, runner.run(DIFF, tmp_path))
    assert converted == ["runtime-0", "runtime-1", "runtime-skipped"]
    receipts.assert_not_called()


def test_all_conversions_precede_receipts_and_fresh_surface_publication(invoke, monkeypatch, tmp_path):
    runner = runtime.RuntimeRunner(backend=object())
    content = _payload(findings=[_finding(), _finding(line=8)])
    converted = []

    def convert(**kwargs):
        assert runner.last_surfaces == []
        converted.append(kwargs["id"])
        return AdvisoryFinding(**kwargs)

    def read_receipts(path):
        assert converted == ["runtime-0", "runtime-1"]
        assert runner.last_surfaces == []
        assert path == tmp_path / ".code-forge" / "smoke-receipts"
        return []

    monkeypatch.setattr(runtime, "AdvisoryFinding", convert)
    monkeypatch.setattr(runtime, "read_smoke_receipts", read_receipts)
    _deliver(invoke, content)
    result = runner.run(DIFF, tmp_path)
    assert [f.id for f in result] == ["runtime-0", "runtime-1", "runtime-smoke-summary"]
    assert runner.last_surfaces == ["network"]
    assert runner.last_surfaces is not content["surfaces"]


@pytest.mark.parametrize("target", ["read_smoke_receipts", "_build_smoke_summary"])
@pytest.mark.parametrize("error", [ValueError, RecursionError])
def test_unrelated_receipt_summary_errors_propagate_without_publication(
    target, error, invoke, monkeypatch, tmp_path
):
    runner = runtime.RuntimeRunner(backend=object())
    monkeypatch.setattr(runtime, target, Mock(side_effect=error("receipt-error")))
    _deliver(invoke, _payload())
    with pytest.raises(error, match="receipt-error"):
        runner.run(DIFF, tmp_path)
    assert runner.last_surfaces == []
    assert runner.infra_errors == []


@pytest.mark.parametrize("error", [MemoryError, RuntimeError])
def test_unrelated_decoder_faults_are_not_reclassified(error, invoke, monkeypatch, tmp_path):
    runner = runtime.RuntimeRunner(backend=object())
    _deliver(invoke, '{"surfaces":[],"findings":[]}')
    monkeypatch.setattr(runtime.json, "loads", Mock(side_effect=error("decoder-error")))
    with pytest.raises(error, match="decoder-error"):
        runner.run(DIFF, tmp_path)
    assert runner.last_surfaces == []
    assert runner.infra_errors == []


def test_repeated_valid_invalid_valid_run_replaces_state_without_aliasing(invoke, tmp_path):
    runner = runtime.RuntimeRunner(backend=object())
    original = _payload()
    before = deepcopy(original)
    _deliver(invoke, original)
    first = runner.run(DIFF, tmp_path)
    first_surfaces = runner.last_surfaces
    first_surfaces.append("caller-added")
    assert original == before
    original["surfaces"].append("payload-added")
    original["findings"][0]["line"] = 99
    assert first_surfaces == ["network", "caller-added"]
    assert first[0].line_range == (7, 7)

    _deliver(invoke, _payload(findings=[_finding(), _finding(line="private-line")]))
    _assert_refusal(runner, runner.run(DIFF, tmp_path))
    assert runner.last_surfaces is not first_surfaces
    assert first_surfaces == ["network", "caller-added"]

    next_content = _payload(surfaces=["systemd"], findings=[])
    _deliver(invoke, next_content)
    result = runner.run(DIFF, tmp_path)
    assert [f.id for f in result] == ["runtime-smoke-summary"]
    assert runner.last_surfaces == ["systemd"]
    assert runner.last_surfaces is not next_content["surfaces"]
    assert runner.infra_errors == []


@pytest.mark.parametrize(
    "content",
    [
        _payload(findings=[_finding(), _finding(line="private-line")]),
        NESTED_TRUNCATION,
        {"surfaces": [], "findings": []},
    ],
)
def test_actual_runner_advisory_display_keeps_refusal_distinct_from_empty(
    content, invoke, tmp_path, capsys
):
    from code_forge.machine import _FixpointResult

    runner = runtime.RuntimeRunner(backend=object())
    _deliver(invoke, content)
    machine = _make_machine(runner, tmp_path)
    machine._run_advisory_axes()
    machine._display_advisories()
    output = capsys.readouterr().err
    assert "Smoke Status" in output
    assert machine._state.findings == []
    assert machine._fixpoint_reached() == _FixpointResult.CLEAN
    if content == {"surfaces": [], "findings": []}:
        assert "smoke: no runtime surfaces detected" in output
        assert machine._advisories == []
        assert machine._state.infra_errors == []
    else:
        _assert_refusal(runner, machine._advisories)
        assert machine._state.infra_errors == [REASON]
        assert "smoke: UNVERIFIED (axis skipped: " + REASON + ")" in output
        assert "no runtime surfaces detected" not in output
        assert "private-line" not in output
        assert "Traceback" not in output


def _make_machine(runner, tmp_path):
    from code_forge.autofix import StubAutoFixer
    from code_forge.baseline import ResolvedReview
    from code_forge.falsify import StubFalsifier
    from code_forge.machine import StateMachine
    from code_forge.state import Mode

    machine = StateMachine(
        mode=Mode.LOCAL,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda f: None,
        resolved_review=ResolvedReview(
            source_files=[Path("a.py")],
            baseline_content=None,
            git_diff=DIFF,
            mode_hint="git",
        ),
        source_hash="contract-control",
        baseline_spec_repr="empty",
        cwd=tmp_path,
        registry={},
        l0_runner=lambda reg, files: ([], []),
        advisory_runners=[runner],
    )
    return machine


def test_deep_valid_metadata_respects_actual_decoder_capacity(
    invoke, tmp_path, record_testsuite_property
):
    runner = runtime.RuntimeRunner(backend=object())
    _deliver(invoke, DEEPLY_NESTED_JSON)
    try:
        decoded = json.loads(DEEPLY_NESTED_JSON)
    except RecursionError:
        outcome = "RecursionError"
    else:
        assert decoded["surfaces"] == [] and decoded["findings"] == []
        depth, current = 0, decoded["metadata"]
        while isinstance(current, list):
            assert len(current) == 1
            depth += 1
            current = current[0]
        assert depth == 10_000 and current == 0
        outcome = "decoded"
    record_testsuite_property("deep_valid_stdlib_outcome", outcome)
    record_testsuite_property("deep_valid_raw_bytes", len(DEEPLY_NESTED_JSON.encode()))
    result = runner.run(DIFF, tmp_path)
    if outcome == "RecursionError":
        _assert_refusal(runner, result)
    else:
        assert result == []
        assert runner.infra_errors == []
        assert runner.last_surfaces == []


def test_decoder_recursion_is_atomic_sanitized_and_does_not_read_receipts(invoke, monkeypatch, tmp_path):
    runner = runtime.RuntimeRunner(backend=object())
    _deliver(invoke, '{"surfaces":[],"findings":[]}')
    decoder = Mock(side_effect=RecursionError("private-decoder-value"))
    receipts = Mock(side_effect=AssertionError("receipt read after decoder failure"))
    monkeypatch.setattr(runtime.json, "loads", decoder)
    monkeypatch.setattr(runtime, "read_smoke_receipts", receipts)
    _assert_refusal(runner, runner.run(DIFF, tmp_path))
    decoder.assert_called_once()
    receipts.assert_not_called()


def test_actual_machine_decoder_recursion_is_visible_and_sanitized(
    invoke, monkeypatch, tmp_path, capsys
):
    from code_forge.machine import _FixpointResult

    runner = runtime.RuntimeRunner(backend=object())
    _deliver(invoke, '{"surfaces":[],"findings":[]}')
    machine = _make_machine(runner, tmp_path)
    decoder = Mock(side_effect=RecursionError("private-decoder-value"))
    receipts = Mock(side_effect=AssertionError("receipt read after decoder failure"))
    monkeypatch.setattr(runtime.json, "loads", decoder)
    monkeypatch.setattr(runtime, "read_smoke_receipts", receipts)
    machine._run_advisory_axes()
    machine._display_advisories()
    output = capsys.readouterr().err
    _assert_refusal(runner, machine._advisories)
    assert machine._state.infra_errors == [REASON]
    assert machine._state.findings == []
    assert machine._fixpoint_reached() == _FixpointResult.CLEAN
    assert "smoke: UNVERIFIED (axis skipped: " + REASON + ")" in output
    assert "no runtime surfaces detected" not in output
    assert "private-decoder-value" not in output
    assert "Traceback" not in output
    decoder.assert_called_once()
    receipts.assert_not_called()
