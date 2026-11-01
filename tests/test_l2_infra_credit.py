# SPDX-License-Identifier: Apache-2.0
"""#114 (option 2): an L2 mutation infrastructure error earns no clean credit
and is non-PASS, but it does not reset a clean window already earned on the
same source. It is a typed infra diagnostic, not a product finding, and a
persistent one stops the run with a dedicated breaker message."""

from __future__ import annotations

from pathlib import Path

import pytest

from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.falsify import StubFalsifier
from code_forge.machine import TimeoutBreaker, StateMachine
from code_forge.state import FindingDiagnosticKind, Mode, StateFinding, Verdict, load_state

GATE = "test:\n  command: [python3, -m, pytest, -q]\n  timeout_seconds: 60\n"
INFRA_TEXT = "mutation owner failed: ChildProcessError: [Errno 10] No child processes"


def _infra():
    return StateFinding(
        id="MUTATION_ERROR",
        fingerprint="mutation-evidence-error",
        source="INFRA",
        disposition=Disposition.CONFIRMED,
        file="",
        line_range=[],
        description=INFRA_TEXT,
    )


def _survivor(n=1):
    return StateFinding(
        id=f"mutant-{n}",
        fingerprint=f"mutant-fp-{n}",
        source="MUTANT",
        disposition=Disposition.CONFIRMED,
        file="calc.py",
        line_range=[2, 2],
        description="surviving mutant: return max -> return min",
        severity="P1",
    )


class Script:
    """l2_runner that plays one scripted result per round."""

    def __init__(self, rounds):
        self.rounds = list(rounds)
        self.calls = 0

    def __call__(self, diff_files, baseline_cmd, **_kw):
        step = self.rounds[min(self.calls, len(self.rounds) - 1)]
        self.calls += 1
        if isinstance(step, BaseException):
            raise step
        return [f() for f in step], ([INFRA_TEXT] if _infra in step else [])


def _machine(tmp_path, l2, *, mode=Mode.LOCAL, source_hash="h1", threshold=3):
    # The gate lives in an outside config root, which CI trusts too.
    cfg = tmp_path / "cfg" / ".code-forge"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "gate.yaml").write_text(GATE)
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    (repo / "calc.py").write_text("def minimum(a, b):\n    return max(a, b)\n")
    return StateMachine(
        mode=mode,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda f: None,
        resolved_review=ResolvedReview(
            source_files=[Path("calc.py")],
            baseline_content=None,
            git_diff=None,
            mode_hint="git",
        ),
        source_hash=source_hash,
        baseline_spec_repr="empty",
        cwd=repo,
        test_config_root=tmp_path / "cfg",
        registry={},
        l0_runner=lambda registry, files: ([], []),
        l2_runner=l2,
        clean_round_threshold=threshold,
        max_total_rounds=8,
    )


def _actions(machine):
    return [row.get("clean_rounds_after") for row in machine._state.round_history]


def test_local_infra_round_preserves_earned_window(tmp_path):
    l2 = Script([[], [], [_infra], []])
    m = _machine(tmp_path, l2)
    assert m.run() == Verdict.PASS
    # Credit 1, 2, held at 2 through the infra round (not reset), then 3.
    assert l2.calls == 4
    assert _actions(m) == [1, 2, 2, 3]
    diag = [f for f in m._state.round_history[2].get("l2_infra", [])] or None
    assert diag is None or diag == [INFRA_TEXT]


def test_infra_finding_is_typed_diagnostic_not_product(tmp_path):
    m = _machine(tmp_path, Script([[_infra]]), threshold=9)
    m.max_total_rounds = 1
    m.run()
    findings = [f for f in m._state.findings if f.id == "MUTATION_ERROR"]
    assert len(findings) == 1
    assert findings[0].diagnostic_kind is FindingDiagnosticKind.MUTATION_INFRA
    assert m._count(Disposition.CONFIRMED) == 0  # not reported as a product finding
    assert m._state.round_history[-1]["fixpoint"] == "INCOMPLETE"
    persisted = load_state(tmp_path / "repo" / ".code-forge" / "state.json")
    assert persisted.findings[0].diagnostic_kind is FindingDiagnosticKind.MUTATION_INFRA
    assert persisted.rounds_with_mutation_infra == 1


def test_mixed_infra_and_survivor_round_still_resets(tmp_path):
    l2 = Script([[], [], [_infra, _survivor], [], [], []])
    m = _machine(tmp_path, l2)
    m.run()
    assert _actions(m)[:3] == [1, 2, 0]


def test_runner_exception_earns_no_credit_and_keeps_window(tmp_path):
    l2 = Script([[], [], OSError(22, "Invalid argument"), []])
    m = _machine(tmp_path, l2)
    assert m.run() == Verdict.PASS
    assert _actions(m) == [1, 2, 2, 3]


def test_persistent_infra_stops_with_dedicated_breaker(tmp_path):
    l2 = Script([[_infra]])
    m = _machine(tmp_path, l2)
    with pytest.raises(TimeoutBreaker) as caught:
        m.run()
    message = str(caught.value)
    assert "mutation infrastructure" in message
    assert INFRA_TEXT in message
    assert l2.calls == 3
    persisted = load_state(tmp_path / "repo" / ".code-forge" / "state.json")
    assert persisted.verdict == Verdict.FAIL
    assert persisted.rounds_with_mutation_infra == 3


def test_recovered_infra_resets_breaker_counter(tmp_path):
    l2 = Script([[_infra], [_infra], [], [_infra], [_infra], [], []])
    m = _machine(tmp_path, l2, threshold=3)
    assert m.run() == Verdict.PASS
    assert m._state.rounds_with_mutation_infra == 0


def test_ci_infra_error_is_never_pass(tmp_path):
    m = _machine(tmp_path, Script([[_infra]]), mode=Mode.CI)
    assert m.run() == Verdict.FAIL


def test_ci_runner_exception_is_never_pass(tmp_path):
    m = _machine(tmp_path, Script([OSError(10, "No child processes")]), mode=Mode.CI)
    assert m.run() == Verdict.FAIL


def test_source_drift_still_resets_preserved_window(tmp_path):
    first = _machine(tmp_path, Script([[], [], [_infra]]), threshold=9)
    first.max_total_rounds = 3
    first.run()
    assert first._state.consecutive_clean_rounds == 2
    # Same source: the window survives the infra round and keeps growing.
    same = _machine(tmp_path, Script([[]]), threshold=9)
    same.max_total_rounds = 1
    same.run()
    assert same._state.consecutive_clean_rounds == 3
    # Changed source: the earned window does not carry over.
    drifted = _machine(tmp_path, Script([[]]), source_hash="h2", threshold=9)
    drifted.max_total_rounds = 1
    drifted.run()
    assert drifted._state.consecutive_clean_rounds == 1


@pytest.mark.parametrize("with_infra", [False, True], ids=["no-infra", "infra"])
def test_l2_infra_does_not_exempt_a_falsifier_protocol_violation(tmp_path, with_infra):
    """#188: a protocol-violation UNCERTAIN finding still resets the window in a
    round that also had an L2 infra error (the mixed round of #114)."""
    from code_forge.falsify import Falsifier
    from code_forge.llm_invoke import FalsifyProtocolError, Usage

    class ProtoFalsifier(Falsifier):
        def falsify(self, finding):
            raise FalsifyProtocolError("invalid verdict", raw={})

    m = _machine(
        tmp_path, Script([[], [], [_infra] if with_infra else [], [], [], [], [], []]), threshold=4
    )
    m.falsifier = ProtoFalsifier()
    calls = {"n": 0}

    def provider():
        calls["n"] += 1
        if calls["n"] == 3:
            f = StateFinding(
                id="f1",
                fingerprint="fp-1",
                source="L1",
                disposition=Disposition.CONFIRMED,
                file="calc.py",
                line_range=[1, 2],
                description="off by one",
            )
            f.excerpt = "def minimum(a, b):\n"
            return [f], [], Usage(), 0.0
        return [], [], Usage(), 0.0

    m.l1_provider = provider
    verdict = m.run()
    assert str(getattr(verdict, "value", verdict)) == "PENDING"
    assert [r.get("fixpoint") for r in m._state.round_history] == ["CLEAN", "CLEAN", "RESET"]
