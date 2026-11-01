# SPDX-License-Identifier: Apache-2.0
"""Shared offline review harness for the interruption and credit controls.

The reviewer transport sequence is constructed at llm_invoke._invoke_api;
exception fields match current emitting sites, but their transport constructors
are NOT exercised. L0/L2/E2E command runners are explicit empty-result seams.
Ledger-root discovery is an explicit fixture seam; fixture ledger consumption is
real. No provider, subprocess, native mutation, or full Forge acceptance is claimed.
Factory, exact _L1Call identity, machine accounting, receipt writing/verification,
state serialization and cli._emit_ci_output are real. Receipt proof is produced
and checked by those consumers; no receipt or verifier success is fabricated.

Not a test module: pytest collects nothing here, and `offline_workspace` is
registered in each test module that binds it.
"""

import contextlib
import copy
import dataclasses
import enum
import hashlib
import io
import json
import sys
import threading
import types
from collections import Counter
from pathlib import Path

import pytest

from code_forge import cli, llm_invoke, verify
from code_forge.autofix import NoChangeAutoFixer
from code_forge.backend import BackendConfig
from code_forge.baseline import ResolvedReview
from code_forge.factories import build_l1_provider
from code_forge.falsify import StubFalsifier
from code_forge.falsify_real import RealFalsifier
from code_forge.machine import StateMachine
from code_forge.source import compute_source_hash
from code_forge.state import Mode, Verdict, load_state, save_state

ROLES = ("qodo", "expert", "adversarial")

CONTENT = {
    "control.ts": "const context = 1;\nconst value = 2;\nconst end = 3;\n",
    "other.ts": "const context = 4;\nconst value = 5;\nconst end = 6;\n",
}

DIFF = (
    "diff --git a/control.ts b/control.ts\n--- a/control.ts\n+++ b/control.ts\n"
    "@@ -1,2 +1,3 @@\n const context = 1;\n+const value = 2;\n const end = 3;\n"
    "diff --git a/other.ts b/other.ts\n--- a/other.ts\n+++ b/other.ts\n"
    "@@ -1,2 +1,3 @@\n const context = 4;\n+const value = 5;\n const end = 6;\n"
)

CHANGED_DIFF = DIFF.replace("const value = 2;", "const value = 20;")

# C3 v3 T3: the same review plus an excerpt-exempt pure-deletion section.
DELETED_CONTENT = {"legacy.ts": "const keep = 7;\n"}

DELETION_DIFF = DIFF + (
    "diff --git a/legacy.ts b/legacy.ts\n--- a/legacy.ts\n+++ b/legacy.ts\n"
    "@@ -1,3 +1,1 @@\n const keep = 7;\n-const drop = 8;\n-const gone = 9;\n"
)

# Acquisition: case, observed pass status, current kind/timeout/exit.
ACQUISITION_CASES = [
    ("no-json", "error", "no_json", False, 0),
    ("timeout-empty-kind", "timeout", "", True, -1),
    ("connect-timeout", "timeout", "conn", True, -1),
    ("credentials", "error", "credentials", False, -1),
    ("http-401", "error", "", False, 401),
    ("http-429", "error", "", False, 429),
    ("http-503", "error", "", False, 503),
    ("capacity", "error", "truncated", False, -1),
    ("generic-host-error", "error", None, False, None),
]


class _ForbiddenExternalOperation(BaseException):
    """Production catches Exception; a forbidden operation must escape that fold."""


@pytest.fixture
def offline_workspace(tmp_path, monkeypatch):
    """No command is allowed; the request-count and forbidden-call logs are oracles."""
    active = [True]
    forbidden = []

    def audit(event, args):
        if active[0] and (
            event.startswith("socket.")
            or event.startswith(
                ("subprocess.Popen", "os.system", "os.fork", "os.posix_spawn", "os.exec")
            )
        ):
            forbidden.append(event)
            raise _ForbiddenExternalOperation("unexpected external operation: " + event)

    sys.addaudithook(audit)
    try:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("FORGE_FALSIFY_WORKERS", "1")

        def fixture_ledger_root(cwd):
            assert Path(cwd) == tmp_path
            return tmp_path

        monkeypatch.setattr("code_forge.machine.resolve_ledger_root", fixture_ledger_root)
        monkeypatch.setattr("code_forge.ledger.resolve_ledger_root", fixture_ledger_root)
        for name, content in CONTENT.items():
            (tmp_path / name).write_text(content, encoding="utf-8")
        config = tmp_path / ".code-forge"
        config.mkdir()
        # The declared command is never executed: l2_runner below is a seam,
        # and these source filenames are not FIXVAL test-file candidates.
        (config / "gate.yaml").write_text(
            "verify:\n  required_cycles: 3\n"
            "test:\n  command: [python, -m, pytest, -q]\n  timeout_seconds: 1\n",
            encoding="utf-8",
        )
        yield tmp_path
    finally:
        active[0] = False
        assert forbidden == [], "external operation attempted, even if caught: " + repr(forbidden)


def _healthy(diff=DIFF):
    content = dict(CONTENT)
    if diff == CHANGED_DIFF:
        content["control.ts"] = content["control.ts"].replace("const value = 2;", "const value = 20;")
    elif diff == DELETION_DIFF:
        content.update(DELETED_CONTENT)
    else:
        assert diff == DIFF, "fixture supports only its explicit source versions"
    return {
        "findings": [],
        "code_excerpts": [
            {"file": name, "start_line": 1, "end_line": text.count("\n"), "content": text}
            for name, text in content.items()
        ],
    }


def _reviewer_role(prompt):
    # This fixture selects constructed replies; this is NOT production trust
    # derived from prompt text. The production factory owns role attribution.
    role_sentence = prompt.rsplit("\nYou are a ", 1)[-1]
    prefixes = ("structural code reviewer:", "senior engineer:", "adversarial QE:")
    matches = [
        role for role, prefix in zip(ROLES, prefixes, strict=True) if role_sentence.startswith(prefix)
    ]
    assert len(matches) == 1, "unexpected reviewer prompt at the explicit transport seam"
    return matches[0]


def _acquisition_error(case):
    if case == "generic-host-error":
        return RuntimeError("constructed unexpected host failure")
    if case == "no-json":
        return llm_invoke.InvalidJSONResponseError(
            "constructed final JSON rejection",
            raw_response="constructed non-JSON response",
            kind="no_json",
            exit_code=0,
            retryable=False,
        )
    if case == "timeout-empty-kind":
        return llm_invoke.LLMInvokeError("constructed body timeout", is_timeout=True, retryable=False)
    if case == "connect-timeout":
        return llm_invoke.LLMInvokeError("constructed connect timeout", kind="conn", is_timeout=True)
    if case == "credentials":
        return llm_invoke.LLMInvokeError(
            "constructed missing credentials", kind="credentials", retryable=False
        )
    if case.startswith("http-"):
        code = int(case.split("-", 1)[1])
        return llm_invoke.LLMInvokeError(
            "constructed HTTP failure", exit_code=code, retryable=code in (429, 503)
        )
    if case == "capacity":
        return llm_invoke.LLMInvokeError(
            "constructed capacity failure", kind="truncated", retryable=False
        )
    raise AssertionError("unknown constructed acquisition case: " + case)


def _candidate_payload(payload, severity="P1"):
    payload["findings"] = [
        {
            "file": "control.ts",
            "line": 2,
            "severity": severity,
            "description": "constructed product defect",
            "excerpt": "const value = 2;",
        }
    ]
    return payload


def _apply_reviewer_fault(body, fault):
    """Mutate one constructed reviewer reply into the named receipt fault."""
    if fault == "schema":
        body["findings"] = "not an array"
    elif fault == "coverage":
        body["code_excerpts"] = body["code_excerpts"][:1]
    elif fault == "missing-excerpts":
        body["code_excerpts"] = []
    elif fault == "excerpt-line-count":
        body["code_excerpts"][0]["content"] = "const value = 2;\n"
    elif fault == "schema-findings":
        # C3: schema-invalid (severity) but the findings list and the excerpts
        # are intact, so the factory yields schema-fail + one UNTRUSTED companion.
        body["findings"] = [
            {
                "file": "control.ts",
                "line": 2,
                "severity": "P9",
                "description": "constructed schema-invalid candidate",
                "excerpt": "const value = 2;",
            }
        ]
    elif fault == "schema-findings-two":
        # C3 T6: one schema-invalid response, two candidates -> two companions.
        body = _apply_reviewer_fault(body, "schema-findings")
        body["findings"].append(
            {
                "file": "other.ts",
                "line": 2,
                "severity": "P9",
                "description": "second schema-invalid candidate",
                "excerpt": "const value = 5;",
            }
        )
    elif fault == "literal":
        body["code_excerpts"][0]["content"] = CONTENT["control.ts"].replace("value = 2", "value = 200")
    elif fault == "inadmissible-dict":
        # Real path: a schema-invalid dict the audit writer cannot
        # encode (a tuple is not built-in JSON data), so the factory records
        # schema-fail and marks the pass "audit payload unavailable".
        body["findings"] = "not an array"
        body["constructed_unencodable"] = ("tuple",)
    else:
        raise AssertionError("unknown constructed reviewer fault: " + str(fault))
    return body


def _new_machine(
    root,
    monkeypatch,
    *,
    fault=None,
    rounds=1,
    mode=Mode.LOCAL,
    diff=DIFF,
    mixed_product=False,
    falsifier_reply=None,
    threshold=3,
    qodo_fault=None,
    product_severity="P1",
    fault_rounds=None,
):
    """Keep production _L1Call intact; inject candidates through reviewer JSON."""
    calls = []
    lock = threading.Lock()
    holder = {}

    def transport(prompt, *args, **kwargs):
        role = _reviewer_role(prompt)
        phase = (
            "excerpt-repair"
            if prompt.startswith(
                "The previous JSON is a review response without required code_excerpts."
            )
            else "review"
        )
        with lock:
            calls.append((holder["machine"]._state.round + 1, role, phase))
        body = _healthy(diff)
        if role == "qodo" and qodo_fault is not None:
            return llm_invoke.LLMResult(_apply_reviewer_fault(body, qodo_fault))
        if role == "qodo" and (mixed_product or falsifier_reply is not None):
            return llm_invoke.LLMResult(_candidate_payload(body, product_severity))
        cycle = holder["machine"]._state.round + 1
        if (
            role != "adversarial"
            or fault is None
            or (fault_rounds is not None and cycle not in fault_rounds)
        ):
            return llm_invoke.LLMResult(body)
        if fault in {row[0] for row in ACQUISITION_CASES}:
            raise _acquisition_error(fault)
        return llm_invoke.LLMResult(_apply_reviewer_fault(body, fault))

    monkeypatch.setattr(llm_invoke, "_invoke_api", transport)
    backend = BackendConfig(
        name="constructed-offline",
        type="api",
        format="openai",
        model="offline",
        base_url="http://127.0.0.1:1",
        timeout_s=1,
    )
    # Supplied diff identity is sufficient for these receipt controls. Leave
    # base/head/target absent: this is not #67 native WORKING-source authority.
    # Its real capture guard returns None before any Git command in this seam.
    resolved = ResolvedReview([Path(name) for name in CONTENT], None, diff, "git")
    provider = build_l1_provider("auto", resolved, backend=backend, max_attempts=1)
    falsifier = StubFalsifier()
    if mixed_product or falsifier_reply is not None:
        falsifier = RealFalsifier(backend=backend, diff_text=diff)

        def falsify_transport(*args, **kwargs):
            reply = falsifier_reply
            if mixed_product:
                reply = {"verdict": "CONFIRMED", "reasoning": "constructed diff-local defect"}
            if isinstance(reply, Exception):
                raise reply
            return llm_invoke.LLMResult(copy.deepcopy(reply))

        monkeypatch.setattr("code_forge.falsify_real.llm_invoke", falsify_transport)
    machine = StateMachine(
        mode=mode,
        falsifier=falsifier,
        autofixer=NoChangeAutoFixer(),
        revert_fn=lambda finding: None,
        resolved_review=resolved,
        source_hash=compute_source_hash(git_diff=diff),
        baseline_spec_repr="issue70 constructed offline diff",
        cwd=root,
        registry={},
        l0_runner=lambda *args: ([], []),
        l1_provider=provider,
        l2_runner=lambda *args, **kwargs: ([], []),
        e2e_runner=lambda *args: ([], []),
        max_total_rounds=rounds,
        clean_round_threshold=threshold,
    )
    machine._state.env_manifest = {}  # explicit fixture manifest, no environment discovery
    holder["machine"] = machine
    return machine, calls


def _state(root):
    path = root / ".code-forge" / "state.json"
    loaded = load_state(path)
    assert loaded is not None
    raw = json.loads(path.read_text(encoding="utf-8"))
    # Exercise a genuine second serialization/load, without overwriting source
    # state or using that copy as authority for the machine under test.
    roundtrip = root / ".code-forge" / "issue70-roundtrip.json"
    save_state(loaded, roundtrip)
    assert json.loads(roundtrip.read_text(encoding="utf-8")) == raw
    again = load_state(roundtrip)
    assert again.consecutive_clean_rounds == loaded.consecutive_clean_rounds
    assert again.earned_clean_window == loaded.earned_clean_window
    assert again.round_history == loaded.round_history
    return loaded, raw


def _report(root):
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        cli._emit_ci_output(root / ".code-forge" / "state.json", {})
    log = json.loads(stdout.getvalue())
    assert log["version"] == "2.1.0" and len(log["runs"]) == 1
    return stderr.getvalue(), log["runs"][0]


def _verify(root, diff=DIFF):
    return verify.run_verify(
        root, compute_source_hash(git_diff=diff), verify.parse_diff_files(diff), diff_text=diff
    )


def _cycle_receipts(root, cycle):
    return [
        json.loads((root / ".code-forge" / "receipts" / f"receipt-c{cycle}p{number}.json").read_text())
        for number in (1, 2, 3)
    ]


def _assert_calls(calls, cycles, *, missing_excerpt_repair=False):
    expected = Counter((cycle, role, "review") for cycle in cycles for role in ROLES)
    if missing_excerpt_repair:
        # Real llm_invoke asks once outside max_attempts. The constructed
        # follow-up stays empty; do not skip or fabricate repair success.
        expected[(cycles[-1], "adversarial", "excerpt-repair")] += 1
    assert Counter(calls) == expected


def _earned_prefix(root, monkeypatch, diff=DIFF):
    machine, calls = _new_machine(root, monkeypatch, rounds=2, diff=diff)
    assert machine.run() is Verdict.ESCALATED  # two is below the unchanged floor
    _assert_calls(calls, [1, 2])
    state, _ = _state(root)
    assert state.consecutive_clean_rounds == 2
    assert [entry["cycle"] for entry in state.earned_clean_window["cycles"]] == [1, 2]
    assert [row["clean_credit_action"] for row in state.round_history] == ["earned", "earned"]
    original = {}
    for cycle, stored in zip((1, 2), state.earned_clean_window["cycles"], strict=True):
        entry, result = verify._capture_earned_cycle(
            root,
            compute_source_hash(git_diff=diff),
            verify.parse_diff_files(diff),
            cycle=cycle,
            diff_text=diff,
        )
        assert result.passed and result.checks_run == result.checks_passed == 8, result.reason
        assert entry == stored
        assert [receipt["pass_status"] for receipt in _cycle_receipts(root, cycle)] == ["completed"] * 3
        for number in (1, 2, 3):
            path = root / ".code-forge" / "receipts" / f"receipt-c{cycle}p{number}.json"
            original[path] = path.read_bytes()
            assert hashlib.sha256(original[path]).hexdigest() == stored["receipt_sha256"][str(number)]
    assert not _verify(root, diff).passed
    return copy.deepcopy(state.earned_clean_window), original


def _assert_original_receipts(original):
    assert {path: path.read_bytes() for path in original} == original


def _restore_result(root, diff=DIFF):
    # Restoration is the real machine's independently verified proof operation,
    # distinct from public terminal verification and from the stored number.
    loaded = load_state(root / ".code-forge" / "state.json")
    return verify._restore_earned_window(
        loaded,
        root,
        compute_source_hash(git_diff=diff),
        verify.parse_diff_files(diff),
        diff_text=diff,
        reviewed_repositories=None,
    )


def _recover(root, monkeypatch, original, expected_cycles, *, rounds=1, diff=DIFF):
    machine, calls = _new_machine(root, monkeypatch, rounds=rounds, diff=diff)
    assert machine.run() is Verdict.PASS
    _assert_calls(calls, expected_cycles[-rounds:])
    state, _ = _state(root)
    assert state.converged is True
    assert state.consecutive_clean_rounds == 3
    assert [entry["cycle"] for entry in state.earned_clean_window["cycles"]] == expected_cycles
    assert _verify(root, diff).passed
    summary, _ = _report(root)
    assert "code-forge: PASS " in summary
    _assert_original_receipts(original)


def _inject_gate_errors(monkeypatch, machine, extra):
    """Append gate errors; record what the predicate and real gate saw at that moment."""
    original = machine._receipt_gate_round_errors
    seen = []

    def errors():
        observed = original()
        seen.append((machine._schema_coverage_credit_preserve(machine._fixpoint_reached()), observed))
        return observed + list(extra)

    monkeypatch.setattr(machine, "_receipt_gate_round_errors", errors)
    return seen


def _c3_companion(machine):
    companions = [
        f
        for f in machine._state.findings
        if f.source == "UNTRUSTED" and f.id == f"l1-adversarial-untrusted-{f.fingerprint}"
    ]
    assert len(companions) == 1 and companions[0].disposition.value == "UNCERTAIN"
    return companions[0].fingerprint


def _survivor(severity):
    from code_forge.disposition import Disposition
    from code_forge.state import StateFinding

    finding = StateFinding(
        id="mutant-control.x_mutmut_1",
        fingerprint="mutant:control.x_mutmut_1",
        source="MUTANT",
        disposition=Disposition.CONFIRMED,
        file="control.ts",
        line_range=[0, 0],
        description="mutant survived: control.x_mutmut_1",
    )
    if severity is not None:
        finding.severity = severity
    return finding


def _fail_state_save_after_publish(mp, machine):
    """Fail the round-end save once this round's receipts were published (no test seam)."""
    publish = machine._publish_l1_receipts
    save = machine._persist_state
    armed = {"published": False, "raised": False}

    def published(*args, **kwargs):
        try:
            return publish(*args, **kwargs)
        finally:
            armed["published"] = True

    def failing_save():
        if armed["published"] and not armed["raised"]:
            armed["raised"] = True
            raise OSError(5, "constructed state save failure")
        return save()

    mp.setattr(machine, "_publish_l1_receipts", published)
    mp.setattr(machine, "_persist_state", failing_save)
    return armed


def _raise_in_gate(mp, machine):
    def gate():
        raise OSError(5, "constructed receipt gate read failure")

    mp.setattr(machine, "_receipt_gate_round_errors", gate)


def _crash_before_publish(mp, machine):
    def publish(*_args, **_kwargs):
        raise RuntimeError("constructed crash before receipt publication")

    mp.setattr(machine, "_publish_l1_receipts", publish)


def _p7_raise_after_l1(mp, machine, nth=1):
    """Fail the `nth` attempt after its L1 returned and before L2 starts.

    `_downgrade_one_line_slips` runs after L1 and before this attempt's
    receipt-write bookings are cleared, which the relog test depends on.
    """
    real = machine._downgrade_one_line_slips
    calls = {"n": 0}

    def downgrade(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == nth:
            raise RuntimeError("constructed failure after L1 returned")
        return real(*args, **kwargs)

    mp.setattr(machine, "_downgrade_one_line_slips", downgrade)
    return RuntimeError


def _freeze(value, seen=frozenset()):
    """Comparable deep snapshot: contents plus object identity of containers."""
    if value is None or type(value) in (bool, int, float, str, bytes):
        return (type(value), value)
    if isinstance(value, enum.Enum):
        return ("enum", type(value).__name__, value.value)
    if isinstance(value, Path):
        return ("path", str(value))
    if id(value) in seen:
        return ("cycle", id(value))
    if isinstance(value, (type, types.ModuleType, types.FunctionType, types.BuiltinFunctionType)):
        return ("ref", id(value))
    if isinstance(value, types.MethodType):
        return ("method", value.__func__.__qualname__, id(value.__self__))
    inner = seen | {id(value)}
    if isinstance(value, dict):
        items = tuple((_freeze(key, inner), _freeze(item, inner)) for key, item in value.items())
        return ("dict", type(value).__name__, id(value), items)
    if isinstance(value, (set, frozenset)):
        members = tuple(sorted(repr(_freeze(item, inner)) for item in value))
        return ("set", type(value).__name__, id(value), members)
    if isinstance(value, (list, tuple)):
        return ("seq", type(value).__name__, id(value), tuple(_freeze(item, inner) for item in value))
    if dataclasses.is_dataclass(value):
        fields = tuple(
            (field.name, _freeze(getattr(value, field.name), inner))
            for field in dataclasses.fields(value)
        )
        return ("dataclass", type(value).__name__, id(value), fields)
    attributes = getattr(value, "__dict__", None)
    if type(attributes) is dict:
        return ("object", type(value).__name__, id(value), _freeze(attributes, inner))
    return ("opaque", type(value).__name__, id(value))


def _machine_snapshot(machine):
    """Every instance attribute except the guard itself, keyed by name.

    Instance attributes only: writes to files, module globals or class
    attributes are not part of this snapshot.
    """
    return {name: _freeze(value) for name, value in vars(machine).items() if name != "_fixpoint_reached"}


def _guard_fixpoint(monkeypatch, machine):
    """Wrap the real `_fixpoint_reached`; record call site, result and any change
    to the machine's instance attributes (see `_machine_snapshot` for the scope).

    Failures are recorded, not raised: `_finish_failed_host_attempt` swallows
    secondary exceptions, so an assertion raised there would be lost.
    """
    original = machine._fixpoint_reached
    record = {"sites": [], "results": [], "violations": []}

    def guarded():
        site = sys._getframe(1).f_code.co_name
        results = []
        for _ in range(3):
            before = _machine_snapshot(machine)
            results.append(original())
            after = _machine_snapshot(machine)
            if after != before:
                changed = sorted(
                    name for name in before.keys() | after.keys() if before.get(name) != after.get(name)
                )
                record["violations"].append((site, "machine state changed", changed))
        record["sites"].append(site)
        record["results"].append(results[0])
        if any(result is not results[0] for result in results[1:]):
            record["violations"].append((site, "not idempotent", results))
        return results[0]

    monkeypatch.setattr(machine, "_fixpoint_reached", guarded)
    return record
