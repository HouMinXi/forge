"""Infra failures must not be mistaken for convergence input.

The falsifier returned UNCERTAIN when its backend was unreachable,
which is the same value it returns for a finding it genuinely could not
decide. The fixpoint check resets the clean-round counter on any
UNCERTAIN, so a dead backend reset the counter every round until
max_total_rounds at 30-180s per call. Measured 2026-08-27: three hours
of a review that could not converge.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.falsify import Falsifier
from code_forge.llm_invoke import LLMInvokeError, Usage
from code_forge.machine import Mode, StateMachine, TimeoutBreaker
from code_forge.state import StateFinding


class _DeadBackendFalsifier(Falsifier):
    """Falsifier whose backend never answers."""

    def __init__(self):
        self.calls = 0

    def falsify(self, finding: StateFinding) -> Disposition:
        self.calls += 1
        raise LLMInvokeError("backend unreachable (simulated)")


def _candidate(fingerprint: str) -> StateFinding:
    return StateFinding(
        id=fingerprint,
        fingerprint=fingerprint,
        source="L1",
        disposition=Disposition.UNCERTAIN,
        file="src/thing.py",
        line_range=[10, 10],
        description="[qodo] a finding needing adjudication",
    )


def _machine(
    tmp_path: Path,
    *,
    mode: Mode = Mode.LOCAL,
    falsifier=None,
    clean_round_threshold: int = 3,
    candidates=None,
) -> StateMachine:
    if candidates is None:
        candidates = [_candidate("fp-1")]

    def _l1_provider():
        # Fresh objects each round: the machine mutates dispositions.
        fresh = [
            StateFinding(
                id=c.id,
                fingerprint=c.fingerprint,
                source=c.source,
                disposition=Disposition.UNCERTAIN,
                file=c.file,
                line_range=list(c.line_range),
                description=c.description,
            )
            for c in candidates
        ]
        return (fresh, [], Usage(), 0.0)

    return StateMachine(
        mode=mode,
        falsifier=falsifier if falsifier is not None else _DeadBackendFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda f: None,
        resolved_review=ResolvedReview(
            source_files=[Path("src/thing.py")],
            baseline_content=None,
            git_diff="",
            mode_hint="git",
            base_sha="0" * 40,
            head_sha="1" * 40,
        ),
        source_hash="sha-test",
        baseline_spec_repr="test-spec",
        cwd=tmp_path,
        registry={},
        l1_provider=_l1_provider,
        clean_round_threshold=clean_round_threshold,
    )


class TestFalsifyInfraDoesNotSilentlyReset:
    """P2: an unreachable backend stops the run instead of grinding."""

    def test_backend_failure_is_recorded_as_infra_not_verdict(self, tmp_path):
        sm = _machine(tmp_path)
        sm._run_l1_phase()

        joined = " ".join(sm._state.infra_errors)
        assert "falsify backend unavailable" in joined, (
            "an unreachable backend must be recorded as an infrastructure "
            "error, not absorbed as a semantic verdict"
        )

    def test_finding_carries_backend_error_text(self, tmp_path):
        sm = _machine(tmp_path)
        findings, _ = sm._run_l1_phase()

        assert findings[0].error is not None
        assert "backend unavailable" in findings[0].error

    def test_three_consecutive_infra_rounds_stop_the_run(self, tmp_path):
        sm = _machine(tmp_path)

        sm._run_l1_phase()
        sm._run_l1_phase()
        with pytest.raises(TimeoutBreaker) as exc:
            sm._run_l1_phase()

        message = str(exc.value)
        assert "could not reach its backend" in message
        assert "cannot converge" in message, (
            "the operator needs to be told the run is unconvergeable, "
            "not just that a call failed"
        )

    def test_two_rounds_alone_do_not_stop_the_run(self, tmp_path):
        """A transient that recovers must not trip the guard."""
        sm = _machine(tmp_path)

        sm._run_l1_phase()
        sm._run_l1_phase()  # must not raise

        assert sm._rounds_with_falsify_infra == 2

    def test_recovery_resets_the_consecutive_counter(self, tmp_path):
        """Consecutive, not cumulative -- one good round clears it."""

        class _FlakyFalsifier(Falsifier):
            def __init__(self):
                self.calls = 0

            def falsify(self, finding):
                self.calls += 1
                if self.calls <= 2:
                    raise LLMInvokeError("transient outage")
                return Disposition.DISMISSED

        sm = _machine(tmp_path, falsifier=_FlakyFalsifier())

        sm._run_l1_phase()
        sm._run_l1_phase()
        assert sm._rounds_with_falsify_infra == 2

        sm._run_l1_phase()  # backend recovers
        assert sm._rounds_with_falsify_infra == 0, (
            "a recovered backend must clear the counter, otherwise a "
            "single flaky patch eventually kills a healthy run"
        )

        # Two more failures must now be survivable again.
        sm.falsifier = _DeadBackendFalsifier()
        sm._run_l1_phase()
        sm._run_l1_phase()  # must not raise


class TestRealFalsifierPropagatesBackendFailure:
    """P2 at its source: RealFalsifier must not absorb LLMInvokeError.

    The machine-level tests above raise LLMInvokeError from a stub, so
    they prove the CALLER routes it correctly but say nothing about the
    class that swallowed it. Injecting the swallow back into
    falsify_real.py leaves those tests green -- this class is what fails.
    """

    def test_llm_invoke_error_propagates_to_caller(self, monkeypatch):
        from code_forge import falsify_real

        def _boom(*args, **kwargs):
            raise LLMInvokeError("backend unreachable (simulated)")

        monkeypatch.setattr(falsify_real, "llm_invoke", _boom)

        falsifier = falsify_real.RealFalsifier()
        with pytest.raises(LLMInvokeError):
            falsifier.falsify(_candidate("fp-real"))

    def test_backend_failure_is_not_reported_as_uncertain(self, monkeypatch):
        """The exact regression: an outage must not read as a verdict."""
        from code_forge import falsify_real

        def _boom(*args, **kwargs):
            raise LLMInvokeError("429 rate limited")

        monkeypatch.setattr(falsify_real, "llm_invoke", _boom)

        falsifier = falsify_real.RealFalsifier()
        try:
            result = falsifier.falsify(_candidate("fp-real"))
        except LLMInvokeError:
            return  # correct: the caller decides what an outage means
        pytest.fail(
            "RealFalsifier returned %r for an unreachable backend. "
            "UNCERTAIN is a semantic verdict and resets the clean-round "
            "counter, so absorbing an outage here makes a dead backend "
            "indistinguishable from an undecidable finding." % (result,)
        )

    def test_genuine_uncertain_verdict_still_works(self, monkeypatch):
        """Only the outage path changed; real verdicts are untouched."""
        from code_forge import falsify_real

        class _Result:
            content = {"verdict": "UNCERTAIN", "reasoning": "cannot tell"}

        monkeypatch.setattr(
            falsify_real, "llm_invoke", lambda *a, **k: _Result()
        )

        falsifier = falsify_real.RealFalsifier()
        assert falsifier.falsify(_candidate("fp-real")) == (
            Disposition.UNCERTAIN
        )
