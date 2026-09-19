# SPDX-License-Identifier: Apache-2.0
"""Falsify candidates in parallel.

Measured 2026-09-05 across seven LOCAL reviews: each falsify call to
mimo-v2.5-pro takes 52-70 s, candidates are adjudicated one after
another, and a round with 13 candidates spends 851 s in that loop
against 75-230 s for the three L1 passes. The candidates are
independent: each call sees one finding and returns one verdict.

Contract this file pins:
  - wall time for N candidates is bounded by the slowest one, not the sum
  - l1_findings keeps input order regardless of completion order
  - every except arm still routes (protocol / unavailable / RuntimeError /
    re-raise) and infra_failures still collects the right fingerprints
  - INFRA candidates bypass the falsifier as before
  - FORGE_FALSIFY_WORKERS=1 restores serial behaviour
"""
from __future__ import annotations

import threading
import time

import pytest

from code_forge.disposition import Disposition
from code_forge.llm_invoke import FalsifyProtocolError, LLMInvokeError, Usage
from code_forge.state import StateFinding
from tests.test_runtime_machine import _make_sm


def _f(i: int, source="L1") -> StateFinding:
    return StateFinding(
        id="f%d" % i, fingerprint="fp-%d" % i, source=source,
        disposition=Disposition.CONFIRMED, file="a.py",
        line_range=[i, i], description="finding %d" % i,
    )


class _SlowFalsifier:
    def __init__(self, delay: float, verdicts=None, backend_type="api"):
        from types import SimpleNamespace
        # An API backend is the parallel case; CLI backends are forced
        # serial (module-global _active_proc in llm_invoke).
        self._backend = SimpleNamespace(type=backend_type)
        self.delay = delay
        self.verdicts = verdicts or {}
        self.seen: list[str] = []
        self.max_concurrent = 0
        self._active = 0
        self._lock = threading.Lock()

    def falsify(self, f):
        with self._lock:
            self._active += 1
            self.max_concurrent = max(self.max_concurrent, self._active)
            self.seen.append(f.fingerprint)
        try:
            time.sleep(self.delay)
            v = self.verdicts.get(f.fingerprint, Disposition.DISMISSED)
            if isinstance(v, BaseException):
                raise v
            return v
        finally:
            with self._lock:
                self._active -= 1


def test_wall_time_is_max_not_sum(tmp_path, monkeypatch):
    monkeypatch.delenv("FORGE_FALSIFY_WORKERS", raising=False)
    sm = _make_sm(tmp_path)
    fals = _SlowFalsifier(0.4)
    sm.falsifier = fals
    cands = [_f(i) for i in range(4)]
    sm.l1_provider = lambda: (cands, [], Usage(), 0.0)
    t0 = time.monotonic()
    out, _ = sm._run_l1_phase()
    dt = time.monotonic() - t0
    assert dt < 1.0, "serial would be >= 1.6 s, got %.2f" % dt
    assert fals.max_concurrent >= 2
    assert [f.fingerprint for f in out] == ["fp-0", "fp-1", "fp-2", "fp-3"]
    assert all(f.disposition == Disposition.DISMISSED for f in out)


def test_output_order_is_input_order_not_completion_order(tmp_path):
    sm = _make_sm(tmp_path)

    class _Reverse:
        def falsify(self, f):
            # later candidates finish first
            time.sleep(0.05 * (5 - int(f.fingerprint.split("-")[1])))
            return Disposition.DISMISSED
    sm.falsifier = _Reverse()
    cands = [_f(i) for i in range(1, 5)]
    sm.l1_provider = lambda: (cands, [], Usage(), 0.0)
    out, _ = sm._run_l1_phase()
    assert [f.fingerprint for f in out] == ["fp-1", "fp-2", "fp-3", "fp-4"]


def test_every_error_arm_still_routes(tmp_path):
    sm = _make_sm(tmp_path)
    fals = _SlowFalsifier(0.01, verdicts={
        "fp-0": Disposition.CONFIRMED,
        "fp-1": FalsifyProtocolError("bad shape", raw="x"),
        "fp-2": LLMInvokeError("down"),
        "fp-3": RuntimeError("boom"),
    })
    sm.falsifier = fals
    cands = [_f(i) for i in range(4)]
    sm.l1_provider = lambda: (cands, [], Usage(), 0.0)
    out, _ = sm._run_l1_phase()
    d = {f.fingerprint: f for f in out}
    assert d["fp-0"].disposition == Disposition.CONFIRMED and d["fp-0"].error is None
    assert d["fp-1"].disposition == Disposition.UNCERTAIN
    assert d["fp-1"].error.startswith("falsify() protocol violation:")
    assert d["fp-2"].disposition == Disposition.UNCERTAIN
    assert d["fp-2"].error.startswith("falsify() backend unavailable:")
    assert d["fp-3"].disposition == Disposition.UNCERTAIN
    assert d["fp-3"].error.startswith("falsify() raised:")
    infra = "\n".join(sm._state.infra_errors)
    assert "protocol violation on fp-1" in infra
    assert "backend unavailable on fp-2" in infra
    assert "falsify exception on fp-3" in infra


def test_unexpected_exception_still_propagates(tmp_path):
    sm = _make_sm(tmp_path)
    fals = _SlowFalsifier(0.01, verdicts={"fp-1": KeyError("nope")})
    sm.falsifier = fals
    cands = [_f(i) for i in range(3)]
    sm.l1_provider = lambda: (cands, [], Usage(), 0.0)
    with pytest.raises(KeyError):
        sm._run_l1_phase()


def test_infra_candidates_skip_the_falsifier(tmp_path):
    sm = _make_sm(tmp_path)
    fals = _SlowFalsifier(0.01)
    sm.falsifier = fals
    cands = [_f(0), _f(1, source="INFRA"), _f(2)]
    sm.l1_provider = lambda: (cands, [], Usage(), 0.0)
    out, _ = sm._run_l1_phase()
    assert sorted(fals.seen) == ["fp-0", "fp-2"]
    assert [f.fingerprint for f in out] == ["fp-0", "fp-1", "fp-2"]


def test_workers_env_one_is_serial(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_FALSIFY_WORKERS", "1")
    sm = _make_sm(tmp_path)
    fals = _SlowFalsifier(0.05)
    sm.falsifier = fals
    cands = [_f(i) for i in range(4)]
    sm.l1_provider = lambda: (cands, [], Usage(), 0.0)
    sm._run_l1_phase()
    assert fals.max_concurrent == 1
    assert fals.seen == ["fp-0", "fp-1", "fp-2", "fp-3"]


def test_infra_convergence_guard_still_sees_failures(tmp_path):
    """_check_falsify_can_still_converge receives the fingerprint list."""
    sm = _make_sm(tmp_path)
    got = {}
    sm._check_falsify_can_still_converge = lambda fps: got.setdefault("fps", list(fps))
    fals = _SlowFalsifier(0.01, verdicts={
        "fp-0": LLMInvokeError("down"),
        "fp-2": FalsifyProtocolError("bad", raw=None),
    })
    sm.falsifier = fals
    cands = [_f(i) for i in range(3)]
    sm.l1_provider = lambda: (cands, [], Usage(), 0.0)
    sm._run_l1_phase()
    assert sorted(got["fps"]) == ["fp-0", "fp-2"]


def test_cli_backend_forces_serial(tmp_path, monkeypatch):
    """Review round 0 on ebf6235: llm_invoke's CLI path keeps the child
    in a module-global _active_proc for signal cleanup; two in flight
    clobber each other. L1 passes already serialise on CLI backends
    (factories.py:329); falsify must too, regardless of the env knob."""
    monkeypatch.setenv("FORGE_FALSIFY_WORKERS", "4")
    sm = _make_sm(tmp_path)
    fals = _SlowFalsifier(0.05, backend_type="cli")
    sm.falsifier = fals
    cands = [_f(i) for i in range(4)]
    sm.l1_provider = lambda: (cands, [], Usage(), 0.0)
    sm._run_l1_phase()
    assert fals.max_concurrent == 1


def test_api_backend_runs_parallel(tmp_path, monkeypatch):
    monkeypatch.delenv("FORGE_FALSIFY_WORKERS", raising=False)
    sm = _make_sm(tmp_path)
    fals = _SlowFalsifier(0.2, backend_type="api")
    sm.falsifier = fals
    cands = [_f(i) for i in range(4)]
    sm.l1_provider = lambda: (cands, [], Usage(), 0.0)
    sm._run_l1_phase()
    assert fals.max_concurrent >= 2


def test_falsifier_without_backend_is_serial(tmp_path, monkeypatch):
    """A falsifier that exposes no backend (stub, or a third-party one)
    gets the conservative default."""
    monkeypatch.delenv("FORGE_FALSIFY_WORKERS", raising=False)
    sm = _make_sm(tmp_path)
    fals = _SlowFalsifier(0.05)
    del fals._backend
    sm.falsifier = fals
    cands = [_f(i) for i in range(4)]
    sm.l1_provider = lambda: (cands, [], Usage(), 0.0)
    sm._run_l1_phase()
    assert fals.max_concurrent == 1
