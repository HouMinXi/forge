# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Rejected acquisition remains audit data through the real publication path."""

import copy
import hashlib
import json
import math
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path
from unittest.mock import Mock

import pytest

from code_forge import llm_invoke
from code_forge.autofix import StubAutoFixer
from code_forge.backend import BackendConfig
from code_forge.baseline import ResolvedReview
from code_forge.factories import build_grouped_l1_provider, build_l1_provider
from code_forge.falsify import StubFalsifier
from code_forge.llm_invoke import LLMInvokeError, LLMResult, Usage
from code_forge.machine import StateMachine, TimeoutBreaker
from code_forge.source import compute_source_hash
from code_forge.state import Mode, Verdict

CONTENT = 'value = "quoted\\path $(touch NEVER_EXECUTED)";\n'
NAMES = ("qodo", "expert", "adversarial")
BROKEN = '{"findings":[true,,false]}'


def _diff(file):
    return (
        f"diff --git a/{file} b/{file}\n--- a/{file}\n+++ b/{file}\n"
        "@@ -1 +1 @@\n-value = 1;\n+" + CONTENT
    )


def _healthy(file="control.txt"):
    return {
        "findings": [],
        "code_excerpts": [{"file": file, "start_line": 1, "end_line": 1, "content": CONTENT}],
    }


def _role(prompt):
    role = prompt.rsplit("You are a ", 1)[-1]
    return "qodo" if "structural" in role else "expert" if "senior" in role else "adversarial"


@pytest.fixture
def external_guard(monkeypatch):
    """Block external work during the measured product call, including threads."""
    counts = Counter()
    active = [False]

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
            counts["forbidden"] += 1
            raise AssertionError("external operation: " + event)

    sys.addaudithook(audit)
    monkeypatch.setenv("RAW_FIXTURE_KEY", "offline-fixture")
    yield counts, active
    active[0] = False
    assert counts["forbidden"] == 0


@pytest.mark.parametrize("pass_name", NAMES)
@pytest.mark.parametrize("status", ["completed", "timeout", "error", "schema_fail", "incomplete"])
def test_writer_unavailable_pass_precedence(tmp_path, pass_name, status):
    from code_forge.disposition import Disposition
    from code_forge.receipt import write_receipts
    from code_forge.state import StateFinding

    (tmp_path / "control.txt").write_text(CONTENT)
    findings = []
    if status != "completed":
        suffix = {
            "timeout": "spawn-fail",
            "error": "invoke-fail",
            "schema_fail": "schema-fail",
            "incomplete": "incomplete-coverage",
        }[status]
        findings = [
            StateFinding(
                id=f"l1-{pass_name}-{suffix}",
                fingerprint="fixed failure",
                source="INFRA",
                disposition=Disposition.CONFIRMED,
                file="<offline>",
                line_range=[0, 0],
                description="offline failure",
            )
        ]
    passes = {pass_name}
    receipts = write_receipts(
        tmp_path / "receipts",
        0,
        findings,
        "offline",
        [Path("control.txt")],
        tmp_path,
        reviewer_excerpts=[_healthy()["code_excerpts"][0] | {"pass_name": name} for name in NAMES],
        manifest={"tier": "declared"},
        unavailable_rejected_passes=passes,
    )
    data = [json.loads(p.read_text()) for p in receipts]
    expected = "incomplete" if status == "completed" else status
    assert next(r for r in data if r["pass"] == NAMES.index(pass_name) + 1)["pass_status"] == expected
    assert all(r["pass_status"] == "completed" for r in data if r["pass"] != NAMES.index(pass_name) + 1)
    assert passes == {pass_name} and not (tmp_path / "receipts/attempted").exists()


@pytest.mark.parametrize("option", ["omitted", "none", "empty", "positional"])
def test_writer_pass_set_default_and_positional_compatibility(tmp_path, option):
    from code_forge.receipt import write_receipts

    arguments = [tmp_path / "receipts", 0, [], "offline", [], tmp_path]
    if option == "positional":
        paths = write_receipts(
            *arguments, None, None, None, {"tier": "declared"}, None, None, None, None
        )
    else:
        kwargs = (
            {}
            if option == "omitted"
            else {"unavailable_rejected_passes": None if option == "none" else set()}
        )
        paths = write_receipts(*arguments, manifest={"tier": "declared"}, **kwargs)
    assert len(paths) == 3 and all(
        json.loads(p.read_text())["pass_status"] == "completed" for p in paths
    )
    assert not (tmp_path / "receipts/attempted").exists()


@pytest.mark.parametrize("kind", ["list", "unknown", "nonstring", "hostile", "set-subclass"])
def test_writer_pass_set_refuses_bad_metadata_before_files(tmp_path, kind):
    from code_forge.receipt import write_receipts

    sentinel = Hostile()

    class HostileSet(set):
        def __iter__(self):
            pytest.fail("host set subclass iterated")

    value = {
        "list": ["qodo"],
        "unknown": {"other"},
        "nonstring": {1},
        "hostile": {sentinel},
        "set-subclass": HostileSet({"qodo"}),
    }[kind]
    try:
        write_receipts(
            tmp_path / "receipts",
            0,
            [],
            "offline",
            [],
            tmp_path,
            manifest={"tier": "declared"},
            unavailable_rejected_passes=value,
        )
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        assert type(exc) is ValueError, "bad host metadata must be explicitly refused"
        assert "unavailable rejected passes" in str(exc)
    else:
        pytest.fail("bad host metadata was accepted")
    assert not (tmp_path / "receipts").exists() and sentinel.counts == Counter()


def test_unavailable_boolean_is_read_only_derived():
    from code_forge.factories import _L1Call

    call = _L1Call(lambda c: ([], [], Usage(), 0.0))
    assert not call.unavailable_rejected_evidence
    call.unavailable_rejected_passes.add("expert")
    assert call.unavailable_rejected_evidence
    with pytest.raises(AttributeError):
        call.unavailable_rejected_evidence = False
    assert "unavailable_rejected_evidence" not in vars(call)
    call.unavailable_rejected_passes.clear()
    assert not call.unavailable_rejected_evidence


def test_machine_unavailable_pass_set_is_detached(tmp_path, external_guard):
    from code_forge.factories import _L1Call

    def body(call):
        call.unavailable_rejected_passes.add("expert")
        return [], [], Usage(), 0.0

    provider = _L1Call(body)
    machine = _empty_machine(tmp_path, provider)
    counts, active = external_guard
    active[0] = True
    try:
        machine._execute_round(0)
    finally:
        active[0] = False
    assert machine._unavailable_rejected_passes_last_round == {"expert"}
    assert machine._unavailable_rejected_passes_last_round is not provider.unavailable_rejected_passes
    provider.unavailable_rejected_passes.clear()
    assert machine._unavailable_rejected_passes_last_round == {"expert"}
    receipts = [
        json.loads(p.read_text()) for p in (tmp_path / ".code-forge/receipts").glob("receipt-*.json")
    ]
    assert next(r for r in receipts if r["pass"] == 2)["pass_status"] == "incomplete"
    assert all(r["pass_status"] == "completed" for r in receipts if r["pass"] != 2)
    assert counts["forbidden"] == 0


@pytest.mark.parametrize("kind", ["list", "hostile"])
def test_machine_rejects_malformed_host_set_without_coercion(tmp_path, external_guard, kind):
    def producer():
        return [], [], Usage(), 0.0

    sentinel = Hostile()
    producer.unavailable_rejected_passes = ["qodo"] if kind == "list" else sentinel
    machine = _empty_machine(tmp_path, producer)
    counts, active = external_guard
    active[0] = True
    try:
        with pytest.raises(ValueError, match="host-owned set"):
            machine._execute_round(0)
    finally:
        active[0] = False
    assert machine._unavailable_rejected_passes_last_round == set()
    assert sentinel.counts == Counter() and counts["forbidden"] == 0
    assert not (tmp_path / ".code-forge/receipts").exists()


def test_writer_copies_pass_set_before_excerpt_assembly(tmp_path):
    from code_forge.receipt import write_receipts

    passes = {"adversarial"}

    class HostExcerpts(list):
        def __iter__(self):
            passes.clear()
            return super().__iter__()

    (tmp_path / "control.txt").write_text(CONTENT)
    excerpts = HostExcerpts([_healthy()["code_excerpts"][0] | {"pass_name": name} for name in NAMES])
    paths = write_receipts(
        tmp_path / "receipts",
        0,
        [],
        "offline",
        [Path("control.txt")],
        tmp_path,
        reviewer_excerpts=excerpts,
        manifest={"tier": "declared"},
        unavailable_rejected_passes=passes,
    )
    data = [json.loads(p.read_text()) for p in paths]
    assert passes == set()
    assert next(r for r in data if r["pass"] == 3)["pass_status"] == "incomplete"
    assert all(r["pass_status"] == "completed" for r in data if r["pass"] != 3)


def test_grouped_unavailable_pass_union_and_subsequent_reset(tmp_path, monkeypatch, external_guard):
    files = ["control.txt", "other.txt"]
    for file in files:
        (tmp_path / file).write_text(CONTENT)
    combined = "".join(_diff(file) for file in files)
    slices = [ResolvedReview([Path(file)], None, _diff(file), "git") for file in files]
    bad, _ = _declined("depth")
    bad.update(findings=[], code_excerpts=[])
    rejecting = [True]

    def transport(prompt, *args, **kwargs):
        file = "other.txt" if "other.txt" in prompt else "control.txt"
        name = _role(prompt)
        refused = rejecting[0] and (file, name) in (("control.txt", "qodo"), ("other.txt", "expert"))
        return LLMResult(bad if refused else _healthy(file), Usage(), 0.0)

    monkeypatch.setattr(llm_invoke, "llm_invoke", transport)
    provider = build_grouped_l1_provider(
        "auto",
        [{"name": f"group-{i}", "resolved": item} for i, item in enumerate(slices)],
        max_attempts=1,
        initial_delay_s=0,
        pass_stagger_s=0,
    )
    machine = _empty_machine(tmp_path, provider)
    machine.resolved_review = ResolvedReview([Path(file) for file in files], None, combined, "git")
    machine.source_hash = compute_source_hash(git_diff=combined)
    counts, active = external_guard
    active[0] = True
    try:
        machine._execute_round(0)
        assert provider.unavailable_rejected_passes == {"qodo", "expert"}
        assert machine._unavailable_rejected_passes_last_round == {"qodo", "expert"}
        first = [
            json.loads(p.read_text())
            for p in (tmp_path / ".code-forge/receipts").glob("receipt-c1*.json")
        ]
        assert [r["pass_status"] for r in sorted(first, key=lambda r: r["pass"])] == [
            "incomplete",
            "incomplete",
            "completed",
        ]
        rejecting[0] = False
        machine._execute_round(1)
    finally:
        active[0] = False
    assert (
        provider.unavailable_rejected_passes == machine._unavailable_rejected_passes_last_round == set()
    )
    second = [
        json.loads(p.read_text()) for p in (tmp_path / ".code-forge/receipts").glob("receipt-c2*.json")
    ]
    assert len(second) == 3 and all(r["pass_status"] == "completed" for r in second)
    assert counts["forbidden"] == 0


def _publish(
    tmp_path,
    monkeypatch,
    external_guard,
    value,
    *,
    api=False,
    failed_pass=None,
    budget=1,
    grouped=False,
    terminal=False,
    recovered=False,
    first_only=False,
    legacy=False,
    persistent=False,
    mode=Mode.LOCAL,
):
    failed_pass = failed_pass or NAMES[0]
    counts, active = external_guard
    files = ["control.txt", "other.txt"] if grouped else ["control.txt"]
    for file in files:
        (tmp_path / file).write_text(CONTENT)
    slices = [ResolvedReview([Path(file)], None, _diff(file), "git") for file in files]
    combined_diff = "".join(item.git_diff for item in slices)
    resolved = ResolvedReview([Path(file) for file in files], None, combined_diff, "git")
    prompts = []

    def transport(prompt, *args, **kwargs):
        name = _role(prompt)
        file = "other.txt" if "other.txt" in prompt else "control.txt"
        counts[name] += 1
        prompts.append(prompt)
        bad = name == failed_pass and file == "control.txt"
        if first_only and counts[name] > 1:
            bad = False
        response = value if bad else _healthy(file)
        if isinstance(response, Exception):
            raise response
        if api:
            if bad and recovered and counts[name] > 1:
                response = _healthy(file)
            if isinstance(response, Exception):
                raise response
            text = response if type(response) is str else json.dumps(response)
            return text, {"prompt_tokens": 2, "completion_tokens": 3, "_forge_finish_reason": "stop"}
        return LLMResult(response, Usage(2, 3), 0.0)

    if api:
        monkeypatch.setattr(llm_invoke, "_invoke_openai", transport)
        backend = BackendConfig(
            name="offline",
            type="api",
            format="openai",
            model="fixture",
            base_url="http://fixture.invalid",
            api_key_env="RAW_FIXTURE_KEY",
        )
    else:
        monkeypatch.setattr(llm_invoke, "llm_invoke", transport)
        backend = None
    kwargs = {"backend": backend, "max_attempts": budget, "initial_delay_s": 0, "pass_stagger_s": 0}
    if grouped:
        provider = build_grouped_l1_provider(
            "auto",
            [{"name": f"group-{i}", "resolved": item} for i, item in enumerate(slices)],
            **kwargs,
        )
    else:
        provider = build_l1_provider("auto", resolved, **kwargs)
    machine = StateMachine(
        mode=mode,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda finding: None,
        resolved_review=resolved,
        source_hash=compute_source_hash(git_diff=combined_diff),
        baseline_spec_repr="offline raw",
        cwd=tmp_path,
        registry={},
        l0_runner=lambda *args: ([], []),
        l1_provider=provider,
        l2_runner=lambda *args, **kw: ([], []),
        e2e_runner=lambda *args, **kw: ([], []),
        advisory_runners=[],
        max_total_rounds=4 if first_only else 3 if recovered or persistent else 1,
        clean_round_threshold=3,
    )
    machine._state.env_manifest = {"tier": "declared", "offline_fixture": True}
    if terminal:
        machine._state.rounds_with_failed_pass = 2
    active[0] = True
    try:
        try:
            outcome = machine.run()
        except TimeoutBreaker as exc:
            outcome = exc
    finally:
        active[0] = False
    receipts_dir = tmp_path / ".code-forge/receipts"
    attempts = [
        json.loads(path.read_text()) for path in sorted((receipts_dir / "attempted").glob("*.json"))
    ]
    receipts = [json.loads(path.read_text()) for path in sorted(receipts_dir.glob("receipt-*.json"))]
    expected_cycles = 4 if first_only and not legacy else 3 if recovered or persistent else 1
    assert len(receipts) == 3 * expected_cycles
    assert all(item["diff_sha256"] == machine.source_hash for item in receipts)
    assert all(
        item["pass_status"] == "completed"
        for item in receipts
        if item["pass"] != NAMES.index(failed_pass) + 1
    )
    assert not (tmp_path / "NEVER_EXECUTED").exists()
    assert counts["forbidden"] == 0
    if not recovered and not first_only:
        assert (
            isinstance(outcome, TimeoutBreaker)
            if terminal or persistent
            else outcome == (Verdict.FAIL if legacy or mode == Mode.CI else Verdict.ESCALATED)
        )
        failed = next(item for item in receipts if item["pass"] == NAMES.index(failed_pass) + 1)
        assert failed["pass_status"] != "completed"
    return attempts, provider, machine, counts, prompts, outcome


@pytest.mark.parametrize(
    "raw,budget,calls",
    [
        (" \n```json\n" + BROKEN + "\n```\t ", 1, 1),
        (" \n```json\n" + BROKEN + "\n```\t ", 5, 2),
        ("  Review cannot produce JSON.\n ", 5, 1),
    ],
)
@pytest.mark.parametrize(
    "grouped,terminal,failed_pass",
    [(False, False, "qodo"), (True, False, "expert"), (False, True, "adversarial")],
)
def test_final_raw_survives_publication(
    tmp_path, monkeypatch, external_guard, raw, budget, calls, grouped, terminal, failed_pass
):
    attempts, provider, machine, counts, prompts, _ = _publish(
        tmp_path,
        monkeypatch,
        external_guard,
        raw,
        api=True,
        budget=budget,
        grouped=grouped,
        terminal=terminal,
        failed_pass=failed_pass,
    )
    assert len(attempts) == 1, "terminal acquired text must have one durable attempted artifact"
    assert attempts[0]["payload"] == {"raw_response": raw, "pass_name": failed_pass}
    assert attempts[0]["pass_name"] == failed_pass
    assert attempts[0]["attempted"] is True
    assert counts[failed_pass] == calls + int(grouped)
    assert provider.raw_observations[0]["raw_response"] == raw
    assert provider.attempted_excerpts == [] and not provider.unavailable_rejected_evidence
    if grouped:
        assert attempts[0]["group_scope"] == {
            "name": "group-0",
            "diff_sha256": hashlib.sha256(_diff("control.txt").encode()).hexdigest(),
            "source_files": ["control.txt"],
        }
    assert (
        any(f.source == "INFRA" and "invoke-fail" in f.id for f in machine._state.findings) or terminal
    )


@pytest.mark.parametrize(
    "value",
    [
        None,
        False,
        True,
        12,
        -2,
        1.5,
        ["one", "two"],
        [None, {"nested": [True, 1, "value"]}],
        "[1, null]",
        "null",
    ],
)
def test_direct_rejection_retains_type(tmp_path, monkeypatch, external_guard, value):
    attempts, _, _, _, _, _ = _publish(tmp_path, monkeypatch, external_guard, value)
    assert len(attempts) == 1, "admitted direct value must survive the real writer"
    actual = attempts[0]["payload"]["raw_response"]
    assert type(actual) is type(value)
    assert actual == value
    assert attempts[0]["payload"]["pass_name"] == "qodo"


class Hostile:
    def __init__(self):
        self.counts = Counter()

    def __repr__(self):
        self.counts["repr"] += 1
        raise AssertionError("audit must not repr an unknown object")

    def __deepcopy__(self, memo):
        self.counts["copy"] += 1
        raise AssertionError("audit must not copy an unknown object")


def _metaclass_raw(shape):
    equality = Mock(side_effect=AssertionError("raw admission invoked metaclass equality"))
    inequality = Mock(side_effect=AssertionError("raw admission invoked metaclass inequality"))
    copying = Mock(side_effect=AssertionError("raw admission copied an unknown value"))

    class Meta(type):
        __eq__ = equality
        __ne__ = inequality
        __hash__ = type.__hash__

    class Unknown(metaclass=Meta):
        __deepcopy__ = copying

    value = Unknown()
    if shape == "dict":
        value = {"nested": {"unknown": value}}
    elif shape == "list":
        value = {"nested": [value]}
    return value, (equality, inequality, copying)


@pytest.mark.parametrize("shape", ["direct", "dict", "list"])
def test_snapshot_refuses_metaclass_methods(shape):
    from code_forge.factories import _RAW_RESPONSE_REFUSED, _snapshot_raw_response

    value, methods = _metaclass_raw(shape)
    assert _snapshot_raw_response(value) is _RAW_RESPONSE_REFUSED
    for method in methods:
        method.assert_not_called()


@pytest.mark.parametrize("producer", ["ordinary", "grouped", "c-small", "c-fallback", "c-chunks"])
@pytest.mark.parametrize("shape", ["dict", "list"])
def test_metaclass_refusal_preserves_publication_authority(
    tmp_path, monkeypatch, external_guard, producer, shape
):
    value, methods = _metaclass_raw(shape)
    if producer.startswith("c-"):
        machine = _publish_c(tmp_path, monkeypatch, external_guard, value, producer[2:], legacy=True)
        provider = machine.l1_provider
    else:
        attempts, provider, machine, _, _, _ = _publish(
            tmp_path, monkeypatch, external_guard, value, grouped=producer == "grouped", legacy=True
        )
        assert attempts == []
    assert provider.unavailable_rejected_evidence is True
    assert provider.unavailable_rejected_passes == {"qodo"}
    assert machine._unavailable_rejected_passes_last_round == {"qodo"}
    receipts = [
        json.loads(p.read_text()) for p in (tmp_path / ".code-forge/receipts").glob("receipt-*.json")
    ]
    assert len(receipts) == 3
    assert next(r for r in receipts if r["pass"] == 1)["pass_status"] == "schema_fail"
    assert all(r["pass_status"] == "completed" for r in receipts if r["pass"] != 1)
    for method in methods:
        method.assert_not_called()


def _declined(kind):
    sentinel = Hostile()
    if kind == "unknown":
        return {"nested": sentinel}, sentinel
    if kind == "cycle":
        value = {}
        value["self"] = value
    elif kind == "indirect-cycle":
        value = {"nested": []}
        value["nested"].append(value)
    elif kind == "keys":
        value = {1: "wrong key"}
    else:
        value = {}
        for _ in range(129):
            value = {"nested": value}
    return value, sentinel


@pytest.mark.parametrize("kind", ["unknown", "cycle", "indirect-cycle", "depth", "keys"])
def test_declined_snapshot_is_not_null(tmp_path, monkeypatch, external_guard, kind):
    value, sentinel = _declined(kind)
    attempts, _, machine, _, _, _ = _publish(tmp_path, monkeypatch, external_guard, value, legacy=True)
    assert attempts == [], "refused raw must not turn into a null artifact"
    assert any(f.source == "INFRA" and "schema-fail" in f.id for f in machine._state.findings)
    assert sentinel.counts == Counter()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_writer_compatibility(tmp_path, monkeypatch, external_guard, value):
    attempts, _, _, _, _, _ = _publish(tmp_path, monkeypatch, external_guard, value)
    assert len(attempts) == 1
    actual = attempts[0]["payload"]["raw_response"]
    assert math.isnan(actual) if math.isnan(value) else actual == value


@pytest.mark.parametrize(
    "negative,nested,above,text",
    [
        (n, d, a, t)
        for n in (False, True)
        for d in (False, True)
        for a in (False, True)
        for t in (False, True)
    ],
)
def test_integer_writer_boundary(
    tmp_path, monkeypatch, external_guard, request, negative, nested, above, text
):
    limit = sys.get_int_max_str_digits()
    if limit == 0:
        root = Path(__file__).resolve().parents[1]
        child = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                request.node.nodeid,
                "--basetemp",
                str(tmp_path / "integer-limit-child"),
            ],
            cwd=root,
            env=dict(os.environ, PYTHONINTMAXSTRDIGITS="4300", PYTHONPATH=str(root / "src")),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert child.returncode == 0, child.stdout + child.stderr
        assert "1 passed" in child.stdout
        return
    digits = limit + int(above)
    literal = ("-" if negative else "") + "1" + "0" * (digits - 1)
    value = -(10 ** (digits - 1)) if negative else 10 ** (digits - 1)
    if nested:
        value = {"nested": value}
    if text:
        value = '{"nested":' + literal + "}" if nested else literal
    parsed_dict = nested and (not text or not above)
    try:
        attempts, _, _, _, _, _ = _publish(
            tmp_path, monkeypatch, external_guard, value, legacy=parsed_dict
        )
    except ValueError:
        pytest.fail("optional integer audit must preserve caller outcome without serializer escape")
    if above and not text:
        assert attempts == [], "unrepresentable integers must be refused before writer"
    else:
        assert len(attempts) == 1, "original integer text must survive optional decoding failure"
        payload = attempts[0]["payload"]
        assert payload == (
            {"raw_response": value, "pass_name": "qodo"}
            if not parsed_dict
            else (json.loads(value) if text else value) | {"pass_name": "qodo"}
        )


def test_schema_dict_snapshot_precedes_validator_mutation(tmp_path, monkeypatch, external_guard):
    value = {"nested": {"findings": [], "code_excerpts": []}, "pass_name": "hostile-label"}
    original = copy.deepcopy(value)
    attempts, _, _, _, _, _ = _publish(tmp_path, monkeypatch, external_guard, value, legacy=True)
    assert attempts[0]["payload"] == original | {"pass_name": "qodo"}


def test_outlet_c_shared_snapshot_declines_without_copy(external_guard):
    from code_forge.outlet_c import _run_chunk

    value, sentinel = _declined("unknown")
    attempts = []
    counts, active = external_guard
    active[0] = True
    try:
        findings, excerpts, _, _ = _run_chunk(
            _diff("control.txt"), lambda *args: value, ("qodo",), attempted=attempts
        )
    finally:
        active[0] = False
    assert excerpts == [] and attempts == []
    assert findings[0].source == "INFRA"
    assert sentinel.counts == Counter() and counts["forbidden"] == 0


def test_recovered_correction_has_no_rejected_history(tmp_path, monkeypatch, external_guard):
    attempts, _, _, counts, _, outcome = _publish(
        tmp_path, monkeypatch, external_guard, BROKEN, api=True, budget=5, recovered=True
    )
    assert attempts == [] and outcome == Verdict.PASS
    assert counts["qodo"] == 4 and counts["expert"] == counts["adversarial"] == 3


def test_final_error_preserves_routing_and_cause(monkeypatch, external_guard):
    monkeypatch.setattr(
        llm_invoke, "_invoke_openai", lambda *args, **kw: (BROKEN, {"_forge_finish_reason": "stop"})
    )
    backend = BackendConfig(
        name="offline",
        type="api",
        format="openai",
        model="fixture",
        base_url="http://fixture.invalid",
        api_key_env="RAW_FIXTURE_KEY",
    )
    counts, active = external_guard
    active[0] = True
    try:
        with pytest.raises(LLMInvokeError) as caught:
            llm_invoke.llm_invoke("prompt", backend=backend, max_attempts=1)
    finally:
        active[0] = False
    error = caught.value
    assert error.kind == "no_json" and error.exit_code == 0 and not error.retryable
    assert not error.is_timeout and error.retry_after is None and error.duration_s >= 0
    assert isinstance(error.__cause__, json.JSONDecodeError)
    assert getattr(error, "raw_response", None) == BROKEN
    assert counts["forbidden"] == 0


@pytest.mark.parametrize("grouped", [False, True])
def test_first_failed_then_healthy_recovery(tmp_path, monkeypatch, external_guard, grouped):
    raw = " \n```json\n" + BROKEN + "\n```\t "
    attempts, provider, machine, counts, _, outcome = _publish(
        tmp_path, monkeypatch, external_guard, raw, api=True, first_only=True, grouped=grouped
    )
    assert outcome == Verdict.PASS, "raw observation must preserve four-round recovery"
    assert machine._written_cycles == [1, 2, 3, 4]
    assert counts["qodo"] == counts["expert"] == counts["adversarial"] == (8 if grouped else 4)
    assert len(attempts) == 1 and attempts[0]["cycle"] == 1
    assert attempts[0]["payload"] == {"raw_response": raw, "pass_name": "qodo"}
    assert provider.attempted_excerpts == provider.raw_observations == []
    assert not provider.unavailable_rejected_evidence
    assert machine._raw_observations_last_round == []
    assert not machine._unavailable_rejected_passes_last_round
    if grouped:
        assert attempts[0]["group_scope"]["name"] == "group-0"


@pytest.mark.parametrize("grouped", [False, True])
@pytest.mark.parametrize("text", [False, True])
def test_legacy_dict_rejection_stays_blocking(tmp_path, monkeypatch, external_guard, grouped, text):
    value, _ = _declined("depth")
    attempts, provider, machine, counts, _, outcome = _publish(
        tmp_path,
        monkeypatch,
        external_guard,
        json.dumps(value) if text else value,
        legacy=True,
        first_only=True,
        grouped=grouped,
    )
    assert outcome == Verdict.FAIL, "refused old dict rejection must still block"
    assert machine._written_cycles == [1]
    assert counts["qodo"] == counts["expert"] == counts["adversarial"] == (2 if grouped else 1)
    assert provider.unavailable_rejected_evidence and machine._unavailable_rejected_passes_last_round
    assert provider.attempted_excerpts == []
    if text:
        assert len(provider.raw_observations) == len(attempts) == 1
        assert attempts[0]["payload"] == {"raw_response": json.dumps(value), "pass_name": "qodo"}
    else:
        assert provider.raw_observations == attempts == []
    assert any("audit payload unavailable" in e for e in machine._state.infra_errors)


@pytest.mark.parametrize("mode", [Mode.LOCAL, Mode.CI])
def test_observation_persistent_and_ci_outcomes(tmp_path, monkeypatch, external_guard, mode):
    attempts, provider, machine, counts, _, outcome = _publish(
        tmp_path, monkeypatch, external_guard, BROKEN, api=True, persistent=mode == Mode.LOCAL, mode=mode
    )
    cycles = 3 if mode == Mode.LOCAL else 1
    assert isinstance(outcome, TimeoutBreaker) if mode == Mode.LOCAL else outcome == Verdict.FAIL
    assert counts["qodo"] == counts["expert"] == counts["adversarial"] == cycles
    assert len(attempts) == cycles and {a["cycle"] for a in attempts} == set(range(1, cycles + 1))
    assert provider.attempted_excerpts == [] and len(provider.raw_observations) == 1
    assert not provider.unavailable_rejected_evidence
    assert machine._attempted_last_round == []


def test_generic_timeout_has_no_observation(tmp_path, monkeypatch, external_guard):
    error = LLMInvokeError("offline timeout", is_timeout=True, retryable=False)
    attempts, provider, _, counts, _, outcome = _publish(
        tmp_path, monkeypatch, external_guard, error, persistent=True
    )
    assert isinstance(outcome, TimeoutBreaker) and counts["qodo"] == 3
    assert attempts == provider.raw_observations == provider.attempted_excerpts == []
    assert not provider.unavailable_rejected_evidence


def _publish_c(tmp_path, monkeypatch, external_guard, value, route, *, legacy):
    from code_forge import outlet_c

    files = ["control.txt", "other.txt"] if route == "chunks" else ["control.txt"]
    for file in files:
        (tmp_path / file).write_text(CONTENT)
    diff = "".join(_diff(file) for file in files)
    resolved = ResolvedReview([Path(file) for file in files], None, diff, "git")
    monkeypatch.setattr(outlet_c, "_read_chunk_threshold_kb", lambda: 100 if route == "small" else -1)
    if route == "fallback":
        monkeypatch.setattr(outlet_c, "_split_diff_by_file", lambda diff: [])
    machines = []
    counts, active = external_guard
    events = []

    def construct(**kwargs):
        kwargs.update(
            l0_runner=lambda *a: ([], []),
            l2_runner=lambda *a, **k: ([], []),
            e2e_runner=lambda *a, **k: ([], []),
        )
        result = StateMachine(**kwargs)
        result._state.env_manifest = {"tier": "declared", "offline_fixture": True}
        machines.append(result)
        return result

    def spawn(name, chunk):
        file = "other.txt" if "other.txt" in chunk else "control.txt"
        counts[(name, file)] += 1
        events.append((name, file, counts[(name, file)]))
        bad = name == "qodo" and file == "control.txt" and counts[(name, file)] == 1
        return value if bad else json.dumps(_healthy(file))

    monkeypatch.setattr(outlet_c, "StateMachine", construct)
    active[0] = True
    try:
        outcome = outlet_c.run_outlet_c(
            resolved,
            compute_source_hash(git_diff=diff),
            tmp_path,
            spawn,
            falsifier=StubFalsifier(),
            max_total_rounds=4,
            clean_round_threshold=3,
            registry={},
            advisory_runners=[],
        )
    finally:
        active[0] = False
    machine = machines[0]
    assert outcome == (Verdict.FAIL if legacy else Verdict.PASS), "C legacy/recovery outcome parity"
    cycles = 1 if legacy else 4
    assert machine._written_cycles == list(range(1, cycles + 1))
    assert len(events) == cycles * 3 * len(files)
    assert all(counts[(name, file)] == cycles for file in files for name in NAMES)
    assert getattr(machine.l1_provider, "unavailable_rejected_evidence", False) is legacy
    assert getattr(machine.l1_provider, "raw_observations", []) == []
    receipts_dir = tmp_path / ".code-forge/receipts"
    receipts = [json.loads(p.read_text()) for p in receipts_dir.glob("receipt-*.json")]
    assert len(receipts) == cycles * 3
    assert all(r["diff_sha256"] == machine.source_hash for r in receipts)
    assert list((receipts_dir / "attempted").glob("*.json")) == []
    assert counts["forbidden"] == 0
    return machine


@pytest.mark.parametrize("route", ["small", "fallback", "chunks"])
@pytest.mark.parametrize("text", [False, True])
def test_outlet_c_legacy_unavailable_rejection(tmp_path, monkeypatch, external_guard, route, text):
    value, _ = _declined("depth")
    machine = _publish_c(
        tmp_path, monkeypatch, external_guard, json.dumps(value) if text else value, route, legacy=True
    )
    counts, active = external_guard
    active[0] = True
    try:
        machine.l1_provider()
    finally:
        active[0] = False
    assert not machine.l1_provider.unavailable_rejected_evidence
    assert machine.l1_provider.raw_observations == machine.l1_provider.attempted_excerpts == []
    assert counts["forbidden"] == 0


@pytest.mark.parametrize("route", ["small", "fallback", "chunks"])
def test_outlet_c_malformed_recovery(tmp_path, monkeypatch, external_guard, route):
    _publish_c(tmp_path, monkeypatch, external_guard, BROKEN, route, legacy=False)


@pytest.mark.parametrize("route", ["small", "fallback", "chunks"])
@pytest.mark.parametrize("kind", ["missing", "shape"])
def test_outlet_c_excerpt_rejection_keeps_original_authority(
    tmp_path, monkeypatch, external_guard, route, kind
):
    value, _ = _declined("depth")
    value.update(_healthy())
    value.update(unavailable_rejected_evidence=False, raw_observations=[], pass_name="adversarial")
    if kind == "missing":
        value["code_excerpts"] = []
    else:
        value["code_excerpts"][0]["end_line"] = 3
    _publish_c(tmp_path, monkeypatch, external_guard, value, route, legacy=True)


def test_outlet_c_missing_exempt_and_spoofed_safe_attempt(external_guard):
    from code_forge.factories import _L1Call
    from code_forge.outlet_c import _run_chunk

    value, _ = _declined("depth")
    value.update(findings=[], code_excerpts=[])
    host = _L1Call(lambda call: ([], [], Usage(), 0.0))
    attempts = []
    counts, active = external_guard
    active[0] = True
    try:
        _run_chunk("", lambda *a: value, ("qodo",), attempted=attempts, rejection_state=host)
        assert not host.unavailable_rejected_evidence and attempts == []
        spoof = {"missing": True, "pass_name": "adversarial", "unavailable_rejected_evidence": False}
        _run_chunk(
            _diff("control.txt"), lambda *a: spoof, ("qodo",), attempted=attempts, rejection_state=host
        )
    finally:
        active[0] = False
    assert attempts == [spoof | {"pass_name": "qodo"}]
    assert not host.unavailable_rejected_evidence and counts["forbidden"] == 0


@pytest.mark.parametrize("branch", ["normal", "exception", "empty", "stub"])
def test_host_call_state_reset(monkeypatch, branch):
    from code_forge.factories import _L1Call

    if branch in ("empty", "stub"):
        provider = build_l1_provider(
            "stub" if branch == "stub" else "auto", ResolvedReview([], None, "", "git")
        )
    else:

        def body(call):
            assert call.raw_observations == [] and not call.unavailable_rejected_evidence
            if branch == "exception":
                raise RuntimeError("declared abort")
            return [], [], Usage(), 0.0

        provider = _L1Call(body)
    assert getattr(provider, "raw_observations", None) == []
    assert getattr(provider, "unavailable_rejected_evidence", None) is False
    for _ in range(2):
        provider.raw_observations = [{"old": True}]
        provider.unavailable_rejected_passes = {"qodo"}
        if branch == "exception":
            with pytest.raises(RuntimeError, match="declared abort"):
                provider()
        else:
            provider()
        assert provider.raw_observations == [] and not provider.unavailable_rejected_evidence


def test_successful_unsafe_extras_do_not_invent_rejection(monkeypatch):
    value, sentinel = _declined("unknown")
    response = _healthy() | value | {"unavailable_rejected_evidence": True, "raw_observations": [BROKEN]}
    monkeypatch.setattr(llm_invoke, "llm_invoke", lambda *a, **k: LLMResult(response, Usage(), 0.0))
    provider = build_l1_provider(
        "auto", ResolvedReview([Path("control.txt")], None, _diff("control.txt"), "git")
    )
    findings, excerpts, _, _ = provider()
    assert findings == [] and excerpts
    assert provider.raw_observations == provider.attempted_excerpts == []
    assert not provider.unavailable_rejected_evidence and sentinel.counts == Counter()


@pytest.mark.parametrize("grouped", [False, True])
def test_legacy_model_fields_cannot_waive_rejection(tmp_path, monkeypatch, external_guard, grouped):
    value = {
        "missing": True,
        "pass_name": "adversarial",
        "raw_observations": [],
        "unavailable_rejected_evidence": False,
        "group_scope": {"name": "fake"},
    }
    attempts, provider, _, _, _, outcome = _publish(
        tmp_path, monkeypatch, external_guard, value, grouped=grouped, legacy=True
    )
    assert outcome == Verdict.FAIL and len(attempts) == 1
    assert attempts[0]["payload"] == value | {"pass_name": "qodo"}
    assert provider.raw_observations == [] and not provider.unavailable_rejected_evidence
    if grouped:
        assert attempts[0]["group_scope"]["name"] == "group-0"


@pytest.mark.parametrize("grouped", [False, True])
def test_missing_excerpt_legacy_authority(tmp_path, monkeypatch, external_guard, grouped):
    value = {"findings": [], "code_excerpts": []}
    attempts, provider, _, _, _, outcome = _publish(
        tmp_path, monkeypatch, external_guard, value, grouped=grouped, legacy=True
    )
    assert outcome == Verdict.FAIL and len(attempts) == 1
    assert attempts[0]["payload"] == value | {"pass_name": "qodo"}
    assert provider.raw_observations == [] and not provider.unavailable_rejected_evidence


@pytest.mark.parametrize("nested", [False, True])
def test_unknown_non_dict_has_no_legacy_authority(tmp_path, monkeypatch, external_guard, nested):
    value = Hostile()
    attempts, provider, _, _, _, outcome = _publish(
        tmp_path, monkeypatch, external_guard, [value] if nested else value
    )
    assert outcome == Verdict.ESCALATED and attempts == []
    assert provider.raw_observations == [] and not provider.unavailable_rejected_evidence
    assert value.counts == Counter()


def test_empty_group_call_resets_observations():
    provider = build_grouped_l1_provider("auto", [])
    provider.raw_observations = [{"old": True}]
    provider.unavailable_rejected_passes = {"qodo"}
    assert provider() == ([], [], Usage(), 0.0)
    assert provider.raw_observations == [] and not provider.unavailable_rejected_evidence


def _empty_machine(tmp_path, provider):
    machine = StateMachine(
        mode=Mode.LOCAL,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda f: None,
        resolved_review=ResolvedReview([], None, "", "git"),
        source_hash="empty-fixture",
        baseline_spec_repr="empty",
        cwd=tmp_path,
        registry={},
        l0_runner=lambda *a: ([], []),
        l1_provider=provider,
        l2_runner=lambda *a, **k: ([], []),
        e2e_runner=lambda *a, **k: ([], []),
        advisory_runners=[],
    )
    machine._state.env_manifest = {"tier": "declared", "offline_fixture": True}
    return machine


def test_machine_observation_reset_before_acquisition_failure(tmp_path, external_guard):
    def fail():
        raise RuntimeError("declared abort")

    machine = _empty_machine(tmp_path, fail)
    assert getattr(machine, "_raw_observations_last_round", None) == []
    assert getattr(machine, "_unavailable_rejected_passes_last_round", None) == set()
    counts, active = external_guard
    machine._raw_observations_last_round = [{"old": True}]
    machine._unavailable_rejected_passes_last_round = {"qodo"}
    active[0] = True
    try:
        with pytest.raises(RuntimeError, match="declared abort"):
            machine._execute_round(0)
    finally:
        active[0] = False
    assert machine._raw_observations_last_round == []
    assert not machine._unavailable_rejected_passes_last_round and counts["forbidden"] == 0


def test_legacy_custom_producer_defaults(tmp_path, external_guard):
    machine = _empty_machine(tmp_path, lambda: ([], [], Usage(), 0.0))
    machine._raw_observations_last_round = [{"old": True}]
    machine._unavailable_rejected_passes_last_round = {"qodo"}
    counts, active = external_guard
    active[0] = True
    try:
        machine._execute_round(0)
    finally:
        active[0] = False
    assert machine._raw_observations_last_round == []
    assert not machine._unavailable_rejected_passes_last_round and counts["forbidden"] == 0


def test_raw_dict_interface_and_refusal_do_not_decode(monkeypatch):
    from code_forge import factories

    healthy = {"findings": []}
    assert factories._assess_raw_response(healthy) == (healthy, True)
    assert factories._raw_response_data(healthy) is healthy
    assert factories._raw_response_data(json.dumps(healthy)) == healthy
    assert factories._raw_response_data("null") is None
    refused = factories._snapshot_raw_response(Hostile())
    monkeypatch.setattr(factories.json, "loads", lambda *a, **k: pytest.fail("refused audit decoded"))
    assert factories._assess_raw_response(refused) == (None, False)
    assert factories._raw_response_data(refused) is None


@pytest.mark.parametrize("producer", ["ordinary", "grouped", "c-small", "c-fallback", "c-chunks"])
@pytest.mark.parametrize("kind", ["missing", "shape", "schema"])
@pytest.mark.parametrize("failed_pass", NAMES)
def test_refused_pass_publication_and_verify(
    tmp_path, monkeypatch, external_guard, producer, kind, failed_pass, raw_text=False
):
    from code_forge import outlet_c
    from code_forge.verify import parse_diff_files, run_verify

    files = ["control.txt", "other.txt"] if producer in ("grouped", "c-chunks") else ["control.txt"]
    for file in files:
        (tmp_path / file).write_text(CONTENT)
    slices = [ResolvedReview([Path(file)], None, _diff(file), "git") for file in files]
    combined = "".join(_diff(file) for file in files)
    resolved = ResolvedReview([Path(file) for file in files], None, combined, "git")
    rejected, _ = _declined("depth")
    if kind != "schema":
        rejected.update(_healthy())
        if kind == "missing":
            rejected["code_excerpts"] = []
        else:
            rejected["code_excerpts"][0]["end_line"] = 3
    rejected.update(unavailable_rejected_passes=[], pass_name="forged", group_scope={"name": "fake"})
    counts, active = external_guard
    calls = Counter()
    machines = []
    events = []

    def acquire(name, file):
        calls[(name, file)] += 1
        events.append((name, file, calls[(name, file)]))
        bad = name == failed_pass and file == "control.txt" and calls[(name, file)] == 1
        return rejected if bad else _healthy(file)

    def transport(prompt, *args, **kwargs):
        file = "other.txt" if "other.txt" in prompt else "control.txt"
        response = acquire(_role(prompt), file)
        if raw_text and response is rejected:
            response = json.dumps(response)
        return LLMResult(response, Usage(2, 3), 0.0)

    def construct(**kwargs):
        kwargs.update(
            l0_runner=lambda *a: ([], []),
            l2_runner=lambda *a, **k: ([], []),
            e2e_runner=lambda *a, **k: ([], []),
        )
        machine = StateMachine(**kwargs)
        machine._state.env_manifest = {"tier": "declared", "offline_fixture": True}
        machines.append(machine)
        return machine

    active[0] = True
    try:
        if producer.startswith("c-"):
            monkeypatch.setattr(outlet_c, "StateMachine", construct)
            monkeypatch.setattr(
                outlet_c, "_read_chunk_threshold_kb", lambda: 100 if producer == "c-small" else -1
            )
            if producer == "c-fallback":
                monkeypatch.setattr(outlet_c, "_split_diff_by_file", lambda text: [])
            outcome = outlet_c.run_outlet_c(
                resolved,
                compute_source_hash(git_diff=combined),
                tmp_path,
                lambda name, chunk: acquire(
                    name, "other.txt" if "other.txt" in chunk else "control.txt"
                ),
                falsifier=StubFalsifier(),
                max_total_rounds=4,
                clean_round_threshold=3,
                registry={},
                advisory_runners=[],
            )
            machine = machines[0]
        else:
            monkeypatch.setattr(llm_invoke, "llm_invoke", transport)
            kwargs = {"max_attempts": 1, "initial_delay_s": 0, "pass_stagger_s": 0}
            provider = (
                build_grouped_l1_provider(
                    "auto",
                    [{"name": f"group-{i}", "resolved": s} for i, s in enumerate(slices)],
                    **kwargs,
                )
                if producer == "grouped"
                else build_l1_provider("auto", resolved, **kwargs)
            )
            machine = construct(
                mode=Mode.LOCAL,
                falsifier=StubFalsifier(),
                autofixer=StubAutoFixer(),
                revert_fn=lambda f: None,
                resolved_review=resolved,
                source_hash=compute_source_hash(git_diff=combined),
                baseline_spec_repr="offline pass publication",
                cwd=tmp_path,
                registry={},
                l1_provider=provider,
                advisory_runners=[],
                max_total_rounds=4,
                clean_round_threshold=3,
            )
            outcome = machine.run()
        receipts_dir = tmp_path / ".code-forge/receipts"
        receipts = [json.loads(p.read_text()) for p in receipts_dir.glob("receipt-*.json")]
        failed = next(
            r for r in receipts if r["cycle"] == 1 and r["pass"] == NAMES.index(failed_pass) + 1
        )
        expected = "schema_fail" if kind == "schema" else "incomplete"
        assert failed["pass_status"] == expected, "refused raw must preserve the rejected pass status"
        for convergence in (True, False):
            result = run_verify(
                tmp_path,
                machine.source_hash,
                parse_diff_files(combined),
                diff_text=combined,
                required_cycles=1,
                cycles=[1],
                respect_floor=False,
                require_convergence=convergence,
            )
            assert not result.passed
            assert result.checks_run == 8 and result.checks_passed == 6
            assert f"status={expected}" in result.reason
    finally:
        active[0] = False
    assert outcome == Verdict.FAIL
    rounds = 1
    assert machine._written_cycles == list(range(1, rounds + 1))
    assert len(receipts) == 3 * rounds and len(events) == 3 * rounds * len(files)
    assert all(calls[(name, file)] == rounds for file in files for name in NAMES)
    assert all(
        r["pass_status"] == "completed" for r in receipts if r["pass"] != NAMES.index(failed_pass) + 1
    )
    artifacts = [json.loads(p.read_text()) for p in (receipts_dir / "attempted").glob("*.json")]
    if raw_text:
        assert len(artifacts) == 1, "original parsed text must persist independently of rejection status"
        artifact = artifacts[0]
        assert artifact["cycle"] == 1 and artifact["pass_name"] == failed_pass
        assert artifact["payload"] == {"raw_response": json.dumps(rejected), "pass_name": failed_pass}
        if producer == "grouped":
            assert artifact["group_scope"] == {
                "name": "group-0",
                "diff_sha256": hashlib.sha256(_diff("control.txt").encode()).hexdigest(),
                "source_files": ["control.txt"],
            }
        else:
            assert "group_scope" not in artifact
    else:
        assert artifacts == []
    expected_set = {failed_pass}
    assert getattr(machine.l1_provider, "unavailable_rejected_passes", set()) == expected_set
    assert getattr(machine, "_unavailable_rejected_passes_last_round", set()) == expected_set
    assert counts["forbidden"] == 0


@pytest.mark.parametrize("producer", ["ordinary", "grouped"])
@pytest.mark.parametrize("failed_pass", NAMES)
def test_parsed_shape_text_rejection_status_and_verify_parity(
    tmp_path, monkeypatch, external_guard, producer, failed_pass
):
    original_publish = StateMachine._publish_l1_receipts
    authority = []

    def publish(machine, *args, **kwargs):
        authority.append(
            (list(machine._attempted_last_round), set(machine._unavailable_rejected_passes_last_round))
        )
        return original_publish(machine, *args, **kwargs)

    monkeypatch.setattr(StateMachine, "_publish_l1_receipts", publish)
    test_refused_pass_publication_and_verify(
        tmp_path, monkeypatch, external_guard, producer, "shape", failed_pass, raw_text=True
    )
    assert authority == [([], {failed_pass})], "parsed shape rejection keeps host-owned pass authority"


@pytest.mark.parametrize(
    "option", ["omitted", "none", "empty", "positional14", "positional15", "positional16", "keyword"]
)
def test_diagnostic_writer_parameter_compatibility(tmp_path, option):
    import inspect
    from code_forge.receipt import write_receipts

    assert list(inspect.signature(write_receipts).parameters)[-2:] == [
        "unavailable_rejected_passes",
        "raw_observations",
    ], "writer must expose a separate additive diagnostic channel"
    base = [tmp_path / "receipts", 0, [], "offline", [], tmp_path]
    old_optional = [None, None, None, {"tier": "declared"}, None, None, None, None]
    diagnostic = {"raw_response": BROKEN, "pass_name": "expert"}
    if option.startswith("positional"):
        extra = [] if option == "positional14" else [set()]
        if option == "positional16":
            extra.append([diagnostic])
        paths = write_receipts(*base, *old_optional, *extra)
    else:
        kwargs = (
            {}
            if option == "omitted"
            else {
                "raw_observations": None
                if option == "none"
                else []
                if option == "empty"
                else [diagnostic]
            }
        )
        paths = write_receipts(*base, manifest={"tier": "declared"}, **kwargs)
    assert all(json.loads(p.read_text())["pass_status"] == "completed" for p in paths)
    artifacts = [json.loads(p.read_text()) for p in (tmp_path / "receipts/attempted").glob("*.json")]
    assert [a["payload"] for a in artifacts] == (
        [diagnostic] if option in ("keyword", "positional16") else []
    )


def test_legacy_model_raw_response_field_stays_blocking(tmp_path):
    from code_forge.receipt import write_receipts

    legacy = {"raw_response": "model diagnostic claim", "pass_name": "qodo", "findings": []}
    paths = write_receipts(
        tmp_path / "receipts",
        0,
        [],
        "offline",
        [],
        tmp_path,
        attempted_excerpts=[legacy],
        manifest={"tier": "declared"},
    )
    assert json.loads(paths[0].read_text())["pass_status"] == "incomplete"
    assert all(json.loads(p.read_text())["pass_status"] == "completed" for p in paths[1:])
    artifact = json.loads(next((tmp_path / "receipts/attempted").glob("*.json")).read_text())
    assert artifact["payload"] == legacy


def test_diagnostic_artifact_merge_keeps_order_and_host_scope(tmp_path):
    import inspect
    from code_forge.receipt import write_receipts
    from code_forge.reviewer_json import GroupedReviewAttempt, ReviewGroupScope

    assert "raw_observations" in inspect.signature(write_receipts).parameters, "diagnostic input absent"
    legacy = {"raw_response": "model claim", "pass_name": "qodo", "findings": []}
    scope = ReviewGroupScope("trusted-group", "bound-diff", ("control.txt",))
    raw = ' fenced raw\n{"pass_name":"forged","group_scope":"model"}\n '
    diagnostic = GroupedReviewAttempt({"raw_response": raw, "pass_name": "expert"}, scope)
    paths = write_receipts(
        tmp_path / "receipts",
        0,
        [],
        "offline",
        [],
        tmp_path,
        attempted_excerpts=[legacy],
        raw_observations=[diagnostic],
        manifest={"tier": "declared"},
    )
    assert [json.loads(p.read_text())["pass_status"] for p in paths] == [
        "incomplete",
        "completed",
        "completed",
    ]
    artifacts = [
        json.loads(p.read_text()) for p in sorted((tmp_path / "receipts/attempted").glob("*.json"))
    ]
    assert [a["payload"] for a in artifacts] == [legacy, diagnostic], (
        "artifact order must remain legacy then diagnostic"
    )
    assert [p.name for p in sorted((tmp_path / "receipts/attempted").glob("*.json"))] == [
        "attempted-c1p1-0.json",
        "attempted-c1p2-1.json",
    ]
    assert "group_scope" not in artifacts[0]
    assert artifacts[1]["group_scope"] == {
        "name": "trusted-group",
        "diff_sha256": "bound-diff",
        "source_files": ["control.txt"],
    }
    assert artifacts[1]["payload"]["raw_response"] == raw
