# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""State machine core: loop-until-fixpoint orchestration.

Owned by 02-02. Mode-agnostic core; mode (LOCAL / CI) is a constructor
parameter (D3). Mode resolution (TTY / env / flag) is 02-04 + 02-05.

Per-round flow:
  1. Run L0 (Phase 1 runner) -> auto-CONFIRMED StateFindings (GATE-04a)
  2. Collect L1 candidate findings (Phase 5; 02-02 default = [])
  3. For each L1 candidate: falsifier.falsify() -> Disposition
  4. Merge by fingerprint (FP-04: L0 wins; DISPO-05 UNCERTAIN sticks)
  5. LOCAL mode: for each unfixed CONFIRMED, invoke autofixer
  6. CI mode: skip auto-fix; CONFIRMED > 0 -> FAIL immediately
  7. Update state.round_history; save_state
  8. Convergence check / HOLD check
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .autofix import AutoFixer, FixOutcome
from .baseline import ResolvedReview
from .diagnose import diagnose_non_convergence
from .disposition import (
    Disposition,
    MAX_FIX_ATTEMPTS_PER_FINGERPRINT,
)
from .falsify import Falsifier
from .parsers.base import Finding, ToolError
from .state import (
    Mode,
    State,
    StateFinding,
    Verdict,
    save_state,
)

# L1 candidate provider type alias.
L1Provider = Callable[[], list[StateFinding]]


def _default_l0_runner(
    registry: dict, files: list[Path]
) -> tuple[list[StateFinding], list[str]]:
    """Phase 1 Finding -> 02-01 StateFinding adapter.

    R2-1 fix: returns tuple (state_findings, infra_errors).
    R1 B4 fix: interim fingerprint = sha256(tool:file:line:rule_id)[:16].
    R1 H2 fix: ToolError -> infra_errors, NOT promoted to StateFinding.
    R3 LOW1+LOW2+LOW6 fixes: StateFinding.line_range is list[int].
    """
    from .parsers import parse_output
    from .runner import run_tools

    file_strs = [str(f) for f in files]
    tool_results, _versions, _skipped = run_tools(registry, file_strs)
    state_findings: list[StateFinding] = []
    infra_errors: list[str] = []

    for tool, (stdout, returncode, stderr) in tool_results.items():
        tc = registry[tool]
        items = parse_output(stdout, tc.output_format, tool, returncode)
        for item in items:
            if isinstance(item, ToolError):
                infra_errors.append(
                    "L0 ToolError tool=%s msg=%s" % (tool, item.message)
                )
                continue
            f: Finding = item
            fp_raw = "%s:%s:%s:%s" % (tool, f.file, f.line, f.rule_id)
            fp = hashlib.sha256(
                fp_raw.encode("utf-8")
            ).hexdigest()[:16]
            state_findings.append(
                StateFinding(
                    id=fp,
                    fingerprint=fp,
                    source="L0",
                    disposition=Disposition.CONFIRMED,
                    file=f.file,
                    line_range=[f.line, f.end_line],
                    description=f.message,
                    error=None,
                    anchor=None,
                    evidence_files=[],
                )
            )
    return state_findings, infra_errors


@dataclass
class StateMachine:
    """Forge state machine. Constructor wires dependencies; .run() executes.

    Parameters:
      mode: Mode.LOCAL or Mode.CI
      falsifier: Falsifier impl (StubFalsifier for tests)
      autofixer: AutoFixer impl (StubAutoFixer for tests)
      revert_fn: Callable[[StateFinding], None] -- invoked on PARSE_FAIL
      resolved_review: from 02-03 resolve_baseline
      source_hash: from 02-03 compute_source_hash
      baseline_spec_repr: from 02-03 serialize_baseline_spec
      cwd: working directory (state.json at cwd/.forge/state.json)
      registry: dict[str, ToolConfig] passed to l0_runner
      l0_runner: callable (registry, files) -> (findings, infra_errors)
      l1_provider: callable returning L1 candidates (default: no L1)
      post_round_hook: optional callable for test observability (R1 H6)
      max_total_rounds: STATE-04 LOCAL bound (default 20)
      max_fix_attempts: per-fingerprint budget (default from disposition)
    """
    mode: Mode
    falsifier: Falsifier
    autofixer: AutoFixer
    revert_fn: Callable[[StateFinding], None]
    resolved_review: ResolvedReview
    source_hash: str
    baseline_spec_repr: str
    cwd: Path
    registry: dict
    l0_runner: Callable = field(default=_default_l0_runner)
    l1_provider: L1Provider = field(default=lambda: [])
    post_round_hook: Optional[Callable[[int], None]] = None
    max_total_rounds: int = 20
    max_fix_attempts: int = MAX_FIX_ATTEMPTS_PER_FINGERPRINT
    _state: State = field(default_factory=State, init=False)

    def run(self) -> Verdict:
        """Dispatch to LOCAL or CI execution per mode."""
        self._state.mode = self.mode
        self._state.source_hash = self.source_hash
        self._state.baseline_spec_repr = self.baseline_spec_repr
        if self.mode == Mode.LOCAL:
            return self._run_local()
        if self.mode == Mode.CI:
            return self._run_ci()
        raise ValueError("unknown mode: %s" % self.mode)

    def _run_ci(self) -> Verdict:
        """CI: linear single round; FAIL on any CONFIRMED, else PASS.

        Per R1 H5: converged=True on PASS only; FAIL exits early so
        converged=False.
        """
        self._execute_round(round_index=0)
        confirmed = self._count(Disposition.CONFIRMED)
        verdict = Verdict.FAIL if confirmed > 0 else Verdict.PASS
        self._state.verdict = verdict
        self._state.converged = (verdict == Verdict.PASS)
        self._persist_state()
        return verdict

    def _run_local(self) -> Verdict:
        """LOCAL: loop until fixpoint / HOLD / MAX_TOTAL_ROUNDS.

        STATE-01 / STATE-02 / STATE-04 / STATE-05 / GATE-01b.
        """
        for round_index in range(self.max_total_rounds):
            self._execute_round(round_index)
            self._apply_autofix_loop()
            if self._fixpoint_reached():
                self._finalize_local_terminal()
                return self._state.verdict
            if self._should_enter_hold():
                self._state.verdict = Verdict.PENDING
                self._persist_state()
                return Verdict.PENDING

        # MAX_TOTAL_ROUNDS exhausted -> STATE-05 diagnosis + ESCALATED
        category = diagnose_non_convergence(
            self._state.round_history, self._state.infra_errors
        )
        self._state.verdict = Verdict.ESCALATED
        self._state.converged = False
        self._state.infra_errors.append(
            "ESCALATED category=%s" % category
        )
        self._persist_state()
        return Verdict.ESCALATED

    def _execute_round(self, round_index: int) -> None:
        """One round: L0 + L1 falsify + merge + state update."""
        self._state.round = round_index

        # 1. L0 runner -> auto-CONFIRMED per GATE-04a + FP-04
        try:
            l0_findings, l0_infra = self.l0_runner(
                self.registry, self._source_files()
            )
            self._state.infra_errors.extend(l0_infra)
        except Exception as exc:  # noqa: BLE001
            self._state.infra_errors.append(
                "L0 runner failed: %s" % exc
            )
            l0_findings = []

        # 2. L1 candidates
        l1_candidates = self.l1_provider()

        # 3. Falsify each L1 candidate
        l1_findings: list[StateFinding] = []
        for f in l1_candidates:
            try:
                disposition = self.falsifier.falsify(f)
                f.disposition = disposition
            except RuntimeError as exc:
                f.disposition = Disposition.UNCERTAIN
                f.error = "falsify() raised: %s" % exc
                self._state.infra_errors.append(
                    "falsify exception on %s: %s" % (f.fingerprint, exc)
                )
            l1_findings.append(f)

        # 4. Merge by fingerprint -- FP-04 (L0 wins; promoted sticks)
        merged = self._merge_findings(l0_findings, l1_findings)

        # 5. Apply DISPO-05 promotion stickiness against prior round
        merged = self._apply_promotion_stickiness(merged)

        self._state.findings = merged
        self._append_round_snapshot(round_index, l0_findings, l1_findings)
        self._persist_state()
        if self.post_round_hook is not None:
            self.post_round_hook(round_index)

    def _apply_autofix_loop(self) -> None:
        """LOCAL only: attempt auto-fix on each unfixed CONFIRMED finding.

        For each CONFIRMED finding:
          - If fix_attempts >= max_fix_attempts: promote to UNCERTAIN
            (DISPO-05, exactly once per fingerprint)
          - Else: invoke autofixer.fix()
            - SUCCESS -> FIXED
            - PARSE_FAIL -> revert_fn(finding) + fix_attempts++
            - NO_CHANGE -> fix_attempts++ (no revert needed, R1 H1)
            - EXCEPTION -> fix_attempts++ + infra_errors append (R1 H1)
        """
        mode_hint = self.resolved_review.mode_hint
        for finding in self._state.findings:
            if finding.disposition != Disposition.CONFIRMED:
                continue
            fp = finding.fingerprint
            attempts = self._state.fix_attempts.get(fp, 0)

            # DISPO-05: promote CONFIRMED -> UNCERTAIN once
            if attempts >= self.max_fix_attempts:
                finding.disposition = Disposition.UNCERTAIN
                continue

            try:
                outcome = self.autofixer.fix(finding, mode_hint)
            except Exception as exc:  # noqa: BLE001
                outcome = FixOutcome.EXCEPTION
                self._state.infra_errors.append(
                    "autofixer exception on %s: %s" % (fp, exc)
                )

            if outcome == FixOutcome.SUCCESS:
                finding.disposition = Disposition.FIXED
            elif outcome == FixOutcome.PARSE_FAIL:
                self.revert_fn(finding)
                self._state.fix_attempts[fp] = attempts + 1
            elif outcome == FixOutcome.NO_CHANGE:
                self._state.fix_attempts[fp] = attempts + 1
            elif outcome == FixOutcome.EXCEPTION:
                self._state.fix_attempts[fp] = attempts + 1
                if "autofixer exception" not in str(
                    self._state.infra_errors[-1:]
                ):
                    self._state.infra_errors.append(
                        "autofixer EXCEPTION on %s" % fp
                    )

    def _fixpoint_reached(self) -> bool:
        """R1 B3 + R2-2: precise fixpoint (LOCAL only).

        TRUE iff ALL FOUR conditions hold:
          (a) zero NEW CONFIRMED this round (new = fingerprint not in
              prior round's dispositions; round 0 treats prior as empty)
          (b) zero FIXED->CONFIRMED reversions this round
          (c) zero unfixed CONFIRMED remain in active findings
          (d) zero UNCERTAIN remain in active findings
        """
        history = self._state.round_history
        current_disps = {
            f.fingerprint: f.disposition
            for f in self._state.findings
        }

        # Prior round dispositions (empty set for round 0, R3 LOW4)
        if len(history) >= 2:
            prior_disps = history[-2].get("dispositions", {})
        else:
            prior_disps = {}

        # (a) zero NEW CONFIRMED this round
        for fp, disp in current_disps.items():
            if disp == Disposition.CONFIRMED and fp not in prior_disps:
                return False

        # (b) zero FIXED->CONFIRMED reversions
        for fp, disp in current_disps.items():
            if (
                disp == Disposition.CONFIRMED
                and prior_disps.get(fp) == "FIXED"
            ):
                return False

        # (c) zero unfixed CONFIRMED remain
        for f in self._state.findings:
            if f.disposition == Disposition.CONFIRMED:
                return False

        # (d) zero UNCERTAIN remain
        for f in self._state.findings:
            if f.disposition == Disposition.UNCERTAIN:
                return False

        return True

    def _should_enter_hold(self) -> bool:
        """GATE-01b: HOLD when UNCERTAIN > 0 AND unfixed CONFIRMED == 0.

        CI mode never HOLDs.
        """
        if self.mode == Mode.CI:
            return False
        has_uncertain = any(
            f.disposition == Disposition.UNCERTAIN
            for f in self._state.findings
        )
        has_unfixed_confirmed = any(
            f.disposition == Disposition.CONFIRMED
            for f in self._state.findings
        )
        return has_uncertain and not has_unfixed_confirmed

    def _finalize_local_terminal(self) -> None:
        """R3 LOW5: terminal state writer for LOCAL fixpoint exit."""
        self._state.verdict = Verdict.PASS
        self._state.converged = True
        self._persist_state()

    def _merge_findings(
        self,
        l0_findings: list[StateFinding],
        l1_findings: list[StateFinding],
    ) -> list[StateFinding]:
        """Merge L0 + L1 by fingerprint. FP-04: L0 wins on conflict."""
        merged: dict[str, StateFinding] = {}
        # L1 first so L0 overwrites on conflict
        for f in l1_findings:
            merged[f.fingerprint] = f
        for f in l0_findings:
            merged[f.fingerprint] = f
        return list(merged.values())

    def _apply_promotion_stickiness(
        self, findings: list[StateFinding]
    ) -> list[StateFinding]:
        """DISPO-05: promoted UNCERTAIN sticks against L0 re-detect.

        If a finding was promoted to UNCERTAIN in a prior round
        (fix_attempts >= max and disposition was UNCERTAIN), preserve
        UNCERTAIN even if L0 re-detected it as CONFIRMED this round.
        """
        for f in findings:
            fp = f.fingerprint
            attempts = self._state.fix_attempts.get(fp, 0)
            if attempts >= self.max_fix_attempts:
                # Check prior round for promotion evidence
                prior_was_uncertain = False
                if self._state.round_history:
                    last_disps = self._state.round_history[-1].get(
                        "dispositions", {}
                    )
                    if last_disps.get(fp) == "UNCERTAIN":
                        prior_was_uncertain = True
                if prior_was_uncertain:
                    f.disposition = Disposition.UNCERTAIN
        return findings

    def _append_round_snapshot(
        self,
        round_index: int,
        l0_findings: list[StateFinding],
        l1_findings: list[StateFinding],
    ) -> None:
        """Append per-round snapshot to round_history for STATE-05."""
        snapshot = {
            "round": round_index,
            "l0_fingerprints": [f.fingerprint for f in l0_findings],
            "l1_fingerprints": [f.fingerprint for f in l1_findings],
            "dispositions": {
                f.fingerprint: f.disposition.value
                for f in self._state.findings
            },
            "fixed_fingerprints": [
                f.fingerprint
                for f in self._state.findings
                if f.disposition == Disposition.FIXED
            ],
        }
        self._state.round_history.append(snapshot)

    def _count(self, disposition: Disposition) -> int:
        """Count findings with a given disposition."""
        return sum(
            1 for f in self._state.findings
            if f.disposition == disposition
        )

    def _persist_state(self) -> None:
        """Write state.json to cwd/.forge/state.json."""
        state_path = self.cwd / ".forge" / "state.json"
        save_state(self._state, state_path)

    def _source_files(self) -> list[Path]:
        """Return source files from resolved review."""
        return self.resolved_review.source_files
