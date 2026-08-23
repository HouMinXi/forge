# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""State machine core: loop-until-fixpoint orchestration.

Owned by 02-02. Mode-agnostic core; mode (LOCAL / CI) is a constructor
parameter (D3). Mode resolution (TTY / env / flag) is 02-04 + 02-05.

Per-round flow (STATE-08 ordering):
  1. _run_l0_phase: L0 detect -> auto-CONFIRMED StateFindings (GATE-04a)
  2. LOCAL only: _apply_autofix_loop_to(l0_findings) (STATE-03)
  3. _run_l1_phase: L1 candidates -> falsify -> Disposition
  4. Merge by fingerprint (FP-04: L0 wins; DISPO-05 UNCERTAIN sticks)
  5. Update state.round_history; save_state
  6. Convergence check / HOLD check
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from enum import Enum
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from .advisory import AdvisoryFinding, AxisRunner

import logging

from .autofix import AutoFixer, FixOutcome
from .baseline import ResolvedReview
from .claim import derive_claim_type
from .diagnose import diagnose_non_convergence
from .ledger import (
    LedgerRow,
    TerminalState,
    append_row as ledger_append,
    resolve_ledger_root,
)
from .disposition import (
    Disposition,
    MAX_FIX_ATTEMPTS_PER_FINGERPRINT,
)
from .falsify import Falsifier
from .mutation import launch_detached_mutation
from .flow_contract import DEFAULT_CLEAN_ROUND_THRESHOLD
from .hold import check_escalated_frozen
from .llm_invoke import Usage
from .parsers.base import Finding, ToolError
from . import progress
from .state import (
    Mode,
    State,
    StateFinding,
    Verdict,
    load_state,
    save_state,
)

# L1 candidate provider type alias.
# Returns (candidates, excerpts, usage, duration_s) 4-tuple.
L1Provider = Callable[[], tuple[list[StateFinding], list[dict], Usage, float]]


class TimeoutBreaker(Exception):
    """Raised when a run provably cannot converge and should stop early.

    Two triggers, both meaning "more rounds cannot help": the consecutive
    timeout threshold in TimeoutCircuitBreaker, and consecutive rounds where
    an L1 pass did not complete (_check_l1_can_still_converge). The name
    predates the second one; cli.py prints the message verbatim, so what the
    operator reads is the specific reason, not the class name.
    """


class TimeoutCircuitBreaker:
    """Tracks consecutive timeout errors. Trips when threshold is reached."""
    def __init__(self, threshold: int = 5):
        self.threshold = threshold
        self._consecutive = 0

    def record_timeout(self) -> None:
        self._consecutive += 1
        if self._consecutive >= self.threshold:
            raise TimeoutBreaker(
                "backend produced %d consecutive timeouts (>=%d); "
                "review cannot converge. Raise "
                "FORGE_LLM_TIMEOUT_S or switch to a faster backend."
                % (self._consecutive, self.threshold)
            )

    def record_success(self) -> None:
        self._consecutive = 0

    def record_other_error(self) -> None:
        pass

    @property
    def count(self) -> int:
        return self._consecutive


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
    tool_results, _versions, _skipped, l0_infra = run_tools(registry, file_strs)
    state_findings: list[StateFinding] = []
    infra_errors: list[str] = list(l0_infra)

    for tool, (stdout, returncode, stderr) in tool_results.items():
        tc = registry[tool]
        items = parse_output(stdout, tc.output_format, tool, returncode)
        # Nonzero exit with empty stdout is a tool crash, not a clean
        # run. Every real tool that exits nonzero WITH findings produces
        # non-empty stdout, so this guard cannot eat real findings.
        if not items and returncode != 0 and not stdout.strip():
            items = [ToolError(
                tool_name=tool,
                exit_code=returncode,
                stderr=stderr or "",
                message=(
                    "Tool exited %d with no output -- "
                    "likely a crash or configuration error"
                    % returncode
                ),
            )]
        for item in items:
            if isinstance(item, ToolError):
                err_msg = "L0 ToolError tool=%s msg=%s" % (tool, item.message)
                # Attach tool stderr when parser did not capture it
                tool_stderr = item.stderr or stderr
                if tool_stderr:
                    err_msg += " stderr=%s" % tool_stderr.strip()
                infra_errors.append(err_msg)
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


class _FixpointResult(str, Enum):
    """Return type of _fixpoint_reached: signals CLEAN, full RESET, or CYCLE_RESTART."""

    CLEAN = "CLEAN"
    RESET = "RESET"
    CYCLE_RESTART = "CYCLE_RESTART"


def _severity_tier(finding: StateFinding) -> str:
    """Return severity tier "P0"/"P1"/"P2"/"P3" for a CONFIRMED StateFinding.

    Parse the description prefix first. Unprefixed L0/L1 findings default
    to "P1" (infrastructure failures are blocking by nature). All other
    unprefixed findings default to "P2".
    """
    desc = finding.description or ""
    for prefix in ("P0:", "P1:", "P2:", "P3:"):
        if desc.startswith(prefix):
            return prefix[:-1]
    if finding.source in ("L0", "L1"):
        return "P1"
    return "P2"


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
      cwd: working directory (state.json at cwd/.code-forge/state.json)
      registry: dict[str, ToolConfig] passed to l0_runner
      l0_runner: callable (registry, files) -> (findings, infra_errors)
      l1_provider: callable returning L1 candidates (default: no L1)
      l2_runner: callable (diff_files, baseline_cmd) -> (findings, infra_errors)
      e2e_runner: callable (diff_text, repo_root) -> (findings, infra_errors)
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
    l1_provider: L1Provider = field(default=lambda: ([], [], Usage(), 0.0))
    l2_runner: Callable = field(
        default=lambda diff_files, baseline_cmd: ([], [])
    )
    e2e_runner: Callable = field(
        default=lambda diff_text, repo_root: ([], [])
    )
    coverage_l1_active: bool = True
    coverage_exempt_patterns: list = field(default_factory=list)
    post_round_hook: Optional[Callable[[int], None]] = None
    max_total_rounds: int = 20
    max_fix_attempts: int = MAX_FIX_ATTEMPTS_PER_FINGERPRINT
    clean_round_threshold: int = field(default=DEFAULT_CLEAN_ROUND_THRESHOLD)
    advisory_runners: "list[AxisRunner]" = field(default_factory=list)
    _state: State = field(default_factory=State, init=False)

    @property
    def active_findings(self) -> list:
        """Non-dismissed findings (public accessor for MCP layer)."""
        return [f for f in self._state.findings
                if f.disposition != Disposition.DISMISSED]

    def __post_init__(self) -> None:
        """Initialize per-round cost accumulator (CLI-08 H3)."""
        self._round_input_tokens: int = 0
        self._round_output_tokens: int = 0
        self._round_cached_tokens: int = 0
        self._round_duration: float = 0.0
        self._pass_counter: int = 0
        self._advisories: "list[AdvisoryFinding]" = []
        self._preexisting_buf: "list[AdvisoryFinding]" = []
        self._rounds_with_failed_pass: int = 0

    def run(self) -> Verdict:
        """Dispatch to LOCAL or CI execution per mode."""
        progress.reset()
        progress.emit("run start: mode=%s" % self.mode.value)
        self._maybe_load_prior_state()
        self._state.mode = self.mode
        self._state.source_hash = self.source_hash
        self._state.baseline_spec_repr = self.baseline_spec_repr
        if self.mode == Mode.LOCAL:
            verdict = self._run_local()
        elif self.mode == Mode.CI:
            verdict = self._run_ci()
        else:
            raise ValueError("unknown mode: %s" % self.mode)

        # Advisory axes run once after convergence, regardless of verdict
        #. Covers PASS, HOLD/PENDING, ESCALATED.
        self._run_advisory_axes()
        self._advisories.extend(self._preexisting_buf)
        self._preexisting_buf.clear()
        self._serialize_advisories()
        self._display_advisories()
        progress.emit(
            "run done: verdict=%s findings=%d confirmed=%d"
            % (verdict.value,
               len(self._state.findings),
               self._count(Disposition.CONFIRMED))
        )
        return verdict

    def _maybe_load_prior_state(self) -> None:
        """Load .code-forge/state.json if LOCAL mode; skip if CI (STATE-09).

        CI mode starts fresh every run to avoid inheriting human-
        DISMISSED findings into shared CI runs. LOCAL mode loads if
        file present (CorruptedStateError propagates per 02-01 contract).
        """
        state_path = self.cwd / ".code-forge" / "state.json"
        if self.mode == Mode.CI:
            if state_path.exists():
                logging.getLogger("code_forge").warning(
                    "ignoring prior state.json in CI mode (STATE-09) -- "
                    "each CI run starts fresh and cannot accumulate "
                    "dispositions, so repeated runs on an unchanged diff "
                    "report the same findings; run LOCAL mode for "
                    "iterative multi-round review"
                )
            return
        if state_path.exists():
            loaded = load_state(state_path)
            if loaded is not None:
                self._state = loaded

    def _run_ci(self) -> Verdict:
        """CI: linear single round; FAIL on any CONFIRMED, else PASS.

        Per R1 H5: converged=True on PASS only; FAIL exits early so
        converged=False.
        02-02: Added async mutation result check and launch.

        A prior mutation-result.json is consumed on read once terminal
        (done or error); a "running" marker with a live PID is the only
        case left in place -- a "running" marker whose PID has died, or
        never had one, is also consumed.
        """
        self._execute_round(
            round_index=self._continuation_round_index(),
        )

        # Check for prior mutation result
        result_path = self.cwd / ".code-forge" / "mutation-result.json"
        if result_path.exists():
            try:
                with open(result_path, "r", encoding="utf-8") as f:
                    result_data = json.load(f)

                if not isinstance(result_data, dict):
                    self._state.infra_errors.append(
                        "CI: mutation-result.json is not a JSON object"
                    )
                    self._unlink_mutation_result(result_path)
                elif "status" not in result_data:
                    self._state.infra_errors.append(
                        "CI: mutation-result.json missing status field"
                    )
                    self._unlink_mutation_result(result_path)
                else:
                    status = result_data["status"]
                    if status == "done":
                        # Consume the result: a finished run is a
                        # one-shot measurement, and the relaunch below
                        # re-measures on the next review instead of
                        # replaying this verdict forever.
                        self._unlink_mutation_result(result_path)
                        survivors = result_data.get("survivors", [])
                        if survivors:
                            self._state.verdict = Verdict.FAIL
                            self._state.converged = False
                            self._state.infra_errors.append(
                                "CI: mutation survivors found: %d survivors"
                                % len(survivors)
                            )
                            self._persist_state()
                            self._write_ci_ledger_rows()
                            return Verdict.FAIL
                    elif status == "running":
                        pid = result_data.get("pid")
                        if pid is None or (
                            isinstance(pid, int) and pid <= 0
                        ):
                            # Child has not yet written its PID.
                            # If the file is stale (>120s), the child
                            # likely crashed before writing -- unlink and
                            # fall through to re-launch.
                            started = result_data.get("started_at", 0)
                            if not isinstance(started, (int, float)):
                                started = 0
                            if time.time() - started > 120:
                                self._state.infra_errors.append(
                                    "CI: mutation-result.json stale "
                                    "(pid not set after 120s); "
                                    "re-launching"
                                )
                                self._unlink_mutation_result(result_path)
                            else:
                                return Verdict.PENDING
                        elif pid is not None:
                            from .lock import _pid_alive

                            if _pid_alive(pid):
                                # PID alive, skip new launch
                                self._state.infra_errors.append(
                                    "CI: mutation PID %d still running, "
                                    "skipping new launch" % pid
                                )
                                return Verdict.PENDING
                            else:
                                # PID dead, treat as error
                                from .disposition import (
                                    Disposition as Disp,
                                )

                                finding = StateFinding(
                                    id="MUTATION_SKIPPED",
                                    fingerprint="mutation-process-died",
                                    source="MUTANT",
                                    disposition=Disp.DISMISSED,
                                    file="",
                                    line_range=[],
                                    description=(
                                        "CI: mutation process died (PID %d)"
                                        % pid
                                    ),
                                )
                                self._state.findings.append(finding)
                                self._unlink_mutation_result(result_path)
                    elif status == "error":
                        # A crashed mutation run is an infra problem,
                        # not a review result: report it and consume the
                        # file so the verdict stays with the findings,
                        # matching how a dead mutation PID and a LOCAL
                        # l2_runner failure already degrade.
                        error_msg = result_data.get(
                            "message", "unknown error"
                        )
                        self._state.infra_errors.append(
                            "CI: mutation error: %s" % error_msg
                        )
                        self._unlink_mutation_result(result_path)
            except (json.JSONDecodeError, KeyError, OSError) as e:
                self._state.infra_errors.append(
                    "CI: failed to read mutation-result.json: %s" % e
                )

        # Launch new async mutation via run_mutation (single invocation point)
        import shutil

        diff_files = [str(f) for f in self._source_files()]
        py_files = [f for f in diff_files if f.endswith(".py")]

        # No Python files or no mutmut means there is nothing to
        # measure. That is an environment fact, not a mutation result,
        # so it is recorded as a DISMISSED finding on this same run
        # (below) rather than a mutation-result.json file a later run
        # would read back and could get stuck on.
        if py_files and shutil.which("mutmut") is not None:
            try:
                from .gate_check import load_gate_config

                config = load_gate_config(
                    self.cwd / ".code-forge" / "gate.yaml"
                )
                baseline_cmd = config["test"]["command"]
                test_config = config.get("test", {})
                baseline_timeout = test_config.get("timeout_seconds", 120)
            except FileNotFoundError as exc:
                baseline_cmd = None
                baseline_timeout = 120
                self._state.infra_errors.append(
                    "CI: mutation skipped -- gate.yaml not found: %s"
                    % exc
                )
            except Exception as exc:  # noqa: BLE001
                baseline_cmd = None
                baseline_timeout = 120
                # Both sibling skips below say why they skipped. Without
                # this one the gate simply never launches: no finding, no
                # error, and a PASS indistinguishable from a run where
                # mutation actually measured something. A worktree carries
                # its own gitignored gate.yaml, so an incomplete one is the
                # common case rather than a rare misconfiguration.
                self._state.infra_errors.append(
                    "CI: mutation skipped -- gate.yaml lacking "
                    "test.command or other config error: %s" % exc
                )

            if baseline_cmd is not None:
                try:
                    pid = launch_detached_mutation(
                        diff_files, baseline_cmd, self.cwd,
                        result_path, baseline_timeout,
                    )
                except Exception as exc:  # noqa: BLE001
                    pid = None
                    self._state.infra_errors.append(
                        "CI: mutation launch error: %s" % exc
                    )
                if pid is None:
                    self._state.infra_errors.append(
                        "CI: mutation subprocess failed to start"
                    )
        elif not py_files:
            # DISMISSED, not a file write: visible in this same run's
            # findings/summary instead of silently deferred to a file
            # only a later run would read (and could get stuck on).
            self._state.findings.append(
                StateFinding(
                    id="MUTATION_SKIPPED",
                    fingerprint="mutation-no-python",
                    source="MUTANT",
                    disposition=Disposition.DISMISSED,
                    file="",
                    line_range=[],
                    description=(
                        "no Python files in diff "
                        "(mutation is Python-only MVP)"
                    ),
                )
            )
        else:
            # py_files is non-empty here (the elif above), so reaching
            # this branch at all already means shutil.which("mutmut")
            # was None in the first condition -- no need to re-check it
            # (and re-checking risks a different answer if PATH changed
            # between the two calls).
            self._state.findings.append(
                StateFinding(
                    id="MUTATION_SKIPPED",
                    fingerprint="mutation-unavailable",
                    source="MUTANT",
                    disposition=Disposition.DISMISSED,
                    file="",
                    line_range=[],
                    description="mutmut not installed (soft dependency)",
                )
            )

        # Proceed with normal L0+L1 verdict determination.
        # Coverage gaps (files no review layer examined) also FAIL CI:
        # a silent PASS over an unreviewed file is a false green.
        confirmed = self._count(Disposition.CONFIRMED)
        coverage_gaps = self._count_coverage_gaps()
        verdict = (
            Verdict.FAIL
            if confirmed > 0 or coverage_gaps > 0
            else Verdict.PASS
        )
        if coverage_gaps > 0 and confirmed == 0:
            self._state.infra_errors.append(
                "coverage: %d in-scope file(s) had no review layer "
                "(non-git review or no matching linter); see COVERAGE "
                "findings" % coverage_gaps
            )
        self._state.verdict = verdict
        self._state.converged = (verdict == Verdict.PASS)
        self._persist_state()
        self._write_ci_ledger_rows()
        return verdict

    def _continuation_round_index(self) -> int:
        """The round index CI should start from.

        CI restarts at round 0 on every invocation, which made every
        separate `code-forge review` call overwrite receipt-c1p{1,2,3}
        -- three clean rounds across three invocations were three
        files' worth of evidence. When existing receipts carry the same
        diff_sha256, the tree did not move between them, so this run
        continues the cycle count instead of clobbering: the next cycle
        number after the highest one on disk, whether this diff or
        another one wrote it.

        receipt-c{cycle}p{pass}.json carries no diff identity in its own
        filename -- only the diff_sha256 field inside it does. If a
        changed diff restarted at cycle 0 (as an earlier version of this
        method did), its first round would overwrite whatever diff last
        held that same low cycle number: diff A writes cycles 1-2, diff B
        arrives, restarts at 0, and B's cycle-1 write clobbers A's
        receipt-c1p*.json still sitting on disk. A changed diff instead
        continues from the highest cycle number found on disk for ANY
        diff, so its own cycle numbers can never collide with a file an
        earlier diff already wrote.
        """
        receipts_dir = self.cwd / ".code-forge" / "receipts"
        if not receipts_dir.is_dir():
            return 0
        max_cycle_any_diff = 0
        for f in receipts_dir.glob("receipt-*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (ValueError, OSError, RecursionError):
                # ValueError covers json.JSONDecodeError; matches the
                # same class of receipt-corruption handling in
                # verify.py's _load_receipts, which additionally guards
                # against invalid-UTF-8 (UnicodeDecodeError, a ValueError
                # subclass) and pathologically deep JSON.
                continue
            if not isinstance(data, dict):
                continue
            cycle = data.get("cycle", 0)
            if not isinstance(cycle, int) or isinstance(cycle, bool):
                # bool is an int subclass, so a receipt carrying
                # cycle: true would otherwise count as cycle 1 and
                # silently shift where the next round lands.
                continue
            max_cycle_any_diff = max(max_cycle_any_diff, cycle)
        # The next cycle must sit above everything on disk. Diff A
        # writing cycles 1-2, diff B cycle 3, then A re-run: resuming
        # A's own sequence would collide with B's receipts, since
        # filenames carry no diff identity. The global maximum is the
        # only value whose next cycle collides with nothing.
        return max_cycle_any_diff

    def _run_local(self) -> Verdict:
        """LOCAL: loop until fixpoint / HOLD / MAX_TOTAL_ROUNDS.

        STATE-01 / STATE-02 / STATE-04 / STATE-05 / GATE-01b.
        ESCALATED-frozen check at top of each iteration (after HOLD
        resume): if check_escalated_frozen() -> Verdict.ESCALATED.
        """
        for round_index in range(self.max_total_rounds):
            if check_escalated_frozen(self._state):
                self._append_round_snapshot(
                    round_index,
                    l0_findings=[],
                    l1_findings=[],
                    l2_findings=[],
                )
                self._state.verdict = Verdict.ESCALATED
                self._state.converged = False
                frozen_fps = [
                    f.fingerprint for f in self._state.findings
                    if (
                        f.disposition == Disposition.CONFIRMED
                        and f.fingerprint
                        in self._state.promoted_fingerprints
                    )
                ]
                preview = ",".join(frozen_fps[:3])
                more = "..." if len(frozen_fps) > 3 else ""
                self._state.infra_errors.append(
                    "ESCALATED frozen (DISPO-05) fingerprints=[%s%s]"
                    % (preview, more)
                )
                self._persist_state()
                return Verdict.ESCALATED
            self._execute_round(round_index)

            # Check consecutive_survivor_rounds
            mutant_survivors = sum(
                1 for f in self._state.findings
                if f.source == "MUTANT"
                and f.disposition == Disposition.CONFIRMED
            )
            if mutant_survivors > 0:
                self._state.consecutive_survivor_rounds += 1
            else:
                self._state.consecutive_survivor_rounds = 0

            if self._state.consecutive_survivor_rounds >= 3:
                self._state.verdict = Verdict.FAIL
                self._state.converged = False
                self._state.infra_errors.append(
                    "mutation: 3 consecutive rounds with survivors -- "
                    "tests are demonstrably weak"
                )
                self._persist_state()
                return Verdict.FAIL

            _threshold = self.clean_round_threshold

            _fp = self._fixpoint_reached()
            if _fp == _FixpointResult.CLEAN:
                self._state.consecutive_clean_rounds += 1
            elif _fp == _FixpointResult.CYCLE_RESTART:
                # P2 / P3-density restart. The counter is already 0 here: clause (a)
                # zeroes it on any finding's first (NEW) appearance, and a finding cannot
                # reach CYCLE_RESTART without first being NEW, so no clean rounds can have
                # accumulated. This reset is therefore redundant, kept explicit to match
                # the SKILL.md "P2 resets to cycle 1" contract and stay correct if clause
                # (a) ever changes. FALL THROUGH (no `continue`) so _should_enter_hold() runs.
                self._state.consecutive_clean_rounds = 0
                self._state.infra_errors.append(
                    "tiered-reset: P2/P3-density -- restarting cycle %d"
                    % round_index
                )
            else:
                self._state.consecutive_clean_rounds = 0

            if self._state.consecutive_clean_rounds >= _threshold:
                self._finalize_local_terminal()
                return self._state.verdict
            if self._should_enter_hold():
                uncertain_count = sum(
                    1 for f in self._state.findings
                    if f.disposition == Disposition.UNCERTAIN
                )
                self._state.hold_reason = (
                    "%d UNCERTAIN finding(s) awaiting human disposition"
                    % uncertain_count
                )
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

    def _run_l0_phase(self) -> list[StateFinding]:
        """STATE-08 L0 detect phase. Returns L0 StateFindings (CONFIRMED).

        No file mutations here -- L0 detect only; autofix is separate.
        """
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

        # Delta filter: separate new (in-diff) from pre-existing L0 findings.
        # Pre-existing findings go to advisories; only new findings enter
        # the fix loop and verdict.  Non-git mode: no filtering.
        self._preexisting_buf = []
        if self.resolved_review.git_diff is not None and l0_findings:
            from .advisory import AdvisoryFinding
            from .delta import lines_intersect
            from .diff import extract_changed_lines

            changed = extract_changed_lines(
                self.resolved_review.git_diff, repo_root=self.cwd
            )
            delta: list = []
            for f in l0_findings:
                file_lines = changed.get(f.file)
                if file_lines is not None and lines_intersect(
                    f.line_range, file_lines
                ):
                    delta.append(f)
                else:
                    self._preexisting_buf.append(
                        AdvisoryFinding(
                            id="pre-existing-%s" % f.fingerprint,
                            axis="pre-existing-l0",
                            file=f.file,
                            line_range=f.line_range,
                            description=f.description,
                            attribution=f.source,
                        )
                    )
            l0_findings = delta

        # Danger-score: L0 CONFIRMED StateFinding, causes HOLD on dangerous config fields.
        if self.resolved_review.git_diff is None:
            self._state.infra_errors.append(
                "Danger-score requires a diff"
                " -- skipping in non-git mode"
            )
        else:
            try:
                from .taint import danger_score_from_diff

                danger_findings = danger_score_from_diff(
                    self.resolved_review.git_diff,
                )
                l0_findings.extend(danger_findings)
            except Exception as exc:  # noqa: BLE001
                self._state.infra_errors.append(
                    "Danger-score failed: %s" % exc
                )
        return l0_findings

    def _run_l1_phase(self) -> tuple[list[StateFinding], list[dict]]:
        """STATE-08 L1 detect phase. Runs AFTER L0 autofix in LOCAL mode.

        Both modes invoke L1 per LAYER0-07 (SARIF includes L1 candidates).
        LOCAL L1 sees post-fix code; CI L1 sees raw L0 output (no autofix).

        CLI-08: l1_provider returns (candidates, excerpts, usage, duration_s).
        Usage accumulated to _round_input_tokens/_round_output_tokens for
        cost tracking. Cost written to State after full round (H3 fix).
        """
        l1_candidates, l1_excerpts, usage, duration = self.l1_provider()
        # Accumulate round-level token usage (H3: applied after round ends)
        self._round_input_tokens += usage.input_tokens
        self._round_output_tokens += usage.output_tokens
        self._round_cached_tokens += usage.cached_input_tokens
        self._round_duration += duration
        l1_findings: list[StateFinding] = []
        total = len(l1_candidates)
        for i, f in enumerate(l1_candidates, 1):
            if f.source == "INFRA":
                l1_findings.append(f)
                continue
            progress.emit(
                "falsify %d/%d: %s:%s (%s)"
                % (i, total, f.file, f.line_range, f.fingerprint)
            )
            t_falsify = time.monotonic()
            try:
                f.disposition = self.falsifier.falsify(f)
                progress.emit(
                    "falsify %d/%d: done %s (%.1fs)"
                    % (i, total, f.disposition,
                       time.monotonic() - t_falsify)
                )
            except RuntimeError as exc:
                f.disposition = Disposition.UNCERTAIN
                f.error = "falsify() raised: %s" % exc
                self._state.infra_errors.append(
                    "falsify exception on %s: %s" % (f.fingerprint, exc)
                )
                progress.emit(
                    "falsify %d/%d: failed (%.1fs)"
                    % (i, total, time.monotonic() - t_falsify)
                )
            except Exception:
                # Close the event pair before re-raising: whatever
                # escapes falsify must not leave the stream looking
                # like the falsify is still running.
                progress.emit(
                    "falsify %d/%d: failed (%.1fs)"
                    % (i, total, time.monotonic() - t_falsify)
                )
                raise
            l1_findings.append(f)
        return (l1_findings, l1_excerpts)

    def _check_l1_can_still_converge(
        self, l1_findings: list[StateFinding]
    ) -> None:
        """Stop a run that provably cannot reach a clean round.

        A pass that fails leaves a CONFIRMED INFRA finding, which tiers P2 and
        zeroes consecutive_clean_rounds. Repeat that every round and the
        threshold is unreachable, so the run walks all the way to
        max_total_rounds to learn nothing -- on a slow backend, hours of it.

        TimeoutCircuitBreaker cannot see this, for two separate reasons. It
        only counts timeouts (record_other_error is deliberately a no-op, per
        its own tests), and it counts per PASS, so the one pass that did
        succeed calls record_success and zeroes the counter. A round where
        one of three passes worked looks exactly like a healthy round to it.

        Consecutive rounds, not cumulative: a backend that fails once and
        recovers is a transient the retry logic already handles, and stopping
        on that would be worse than the problem.
        """
        from .state import derive_pass_outcomes, PassOutcome

        outcomes = derive_pass_outcomes(l1_findings)
        failed = {
            name: outcome.value
            for name, outcome in outcomes.items()
            if outcome != PassOutcome.COMPLETED
        }
        if not failed:
            self._rounds_with_failed_pass = 0
            return
        self._rounds_with_failed_pass += 1
        self._state.infra_errors.append(
            "round %d: pass(es) did not complete: %s"
            % (self._state.round, failed)
        )
        if self._rounds_with_failed_pass >= 3:
            raise TimeoutBreaker(
                "%d consecutive rounds had a pass that did not complete "
                "(latest: %s). Each one leaves a CONFIRMED infra finding, "
                "which resets the clean-round counter, so this review cannot "
                "converge no matter how many rounds remain. Fix the backend "
                "or switch to another one rather than waiting."
                % (self._rounds_with_failed_pass, failed)
            )

    def _run_l2_phase(self) -> list[StateFinding]:
        """L2 mutation phase. Runs after L1.

        Calls l2_runner with diff-scoped files and baseline test command.
        Returns MUTANT findings (survivors or MUTATION_SKIPPED).
        """
        try:
            from .gate_check import load_gate_config

            config = load_gate_config(self.cwd / ".code-forge" / "gate.yaml")
            baseline_cmd = config["test"]["command"]
        except Exception as exc:  # noqa: BLE001
            self._state.infra_errors.append(
                "L2: gate.yaml missing or test.command not configured: %s"
                % exc
            )
            return []

        diff_files = [str(f) for f in self._source_files()]

        progress.emit("mutation: running baseline")
        try:
            l2_findings, l2_infra = self.l2_runner(diff_files, baseline_cmd)
            self._state.infra_errors.extend(l2_infra)
            return l2_findings
        except Exception as exc:  # noqa: BLE001
            self._state.infra_errors.append(
                "L2 runner failed: %s" % exc
            )
            return []

    def _run_e2e_phase(self) -> list[StateFinding]:
        """E2e coverage phase. Runs after L2.

        Reads diff_text from the resolved review's canonical diff (captured at
        review setup, same scope as L0/L1). Returns E2E_CHECK findings:
        Layer 1 DISMISSED (advisory), Layer 2 UNCERTAIN (enters HOLD for human
        triage). A failing e2e runner degrades to no findings, never crashes
        the round.

        Non-git mode: no diff is available; records a non-fatal infra signal
        and returns no findings.
        """
        diff_text = self.resolved_review.git_diff
        if diff_text is None:
            self._state.infra_errors.append(
                "e2e: no git diff available (non-git review)"
            )
            return []
        try:
            e2e_findings, e2e_infra = self.e2e_runner(diff_text, self.cwd)
            self._state.infra_errors.extend(e2e_infra)
            return e2e_findings
        except Exception as exc:  # noqa: BLE001
            self._state.infra_errors.append("e2e runner failed: %s" % exc)
            return []

    def _run_coverage_phase(self) -> list[StateFinding]:
        """Per-file review coverage gate. Runs after E2E.

        Flags in-scope files that no review layer examined: no matching
        L0 tool AND L1 inactive (non-git review or stub engine). Prevents
        a silent clean PASS over an effectively unreviewed file. A failing
        coverage computation degrades to no findings, never crashes the
        pipeline (mirrors L2/E2E graceful degradation).
        """
        try:
            from .coverage import (
                build_coverage_findings,
                compute_uncovered_files,
            )
            uncovered = compute_uncovered_files(
                [str(f) for f in self._source_files()],
                self.registry,
                self.coverage_l1_active,
                self.coverage_exempt_patterns,
            )
            return build_coverage_findings(uncovered)
        except Exception as exc:  # noqa: BLE001
            self._state.infra_errors.append(
                "coverage runner failed: %s" % exc
            )
            return []

    def _execute_round(self, round_index: int) -> None:
        """STATE-08: both modes run L0 + L1 + L2 + E2E each round.

        Difference is autofix scope:
          LOCAL: L0 detect -> L0 autofix loop -> L1 -> L2 -> E2E
          CI:    L0 detect -> L1 -> L2 -> E2E (no autofix loop per STATE-03)
        """
        self._state.round = round_index
        progress.emit("round %d start" % round_index)
        l0_findings = self._run_l0_phase()
        if self.mode == Mode.LOCAL:
            self._apply_autofix_loop_to(l0_findings)
        l1_findings, l1_excerpts = self._run_l1_phase()
        self._check_l1_can_still_converge(l1_findings)
        l2_findings = self._run_l2_phase()
        e2e_findings = self._run_e2e_phase()
        coverage_findings = self._run_coverage_phase()
        merged = self._merge_findings(
            l0_findings, l1_findings, l2_findings, e2e_findings,
            coverage_findings,
        )
        merged = self._apply_promotion_stickiness(merged)
        self._state.findings = merged
        self._append_round_snapshot(
            round_index, l0_findings, l1_findings, l2_findings, e2e_findings
        )
        # CLI-08 H3: accumulate cost AFTER full round (L0+L1+L2+E2E done).
        # B7: "pass" = 1-3 within round (qodo/expert/adversarial),
        #     "cycle" = round_index.
        self._state.cost_total_input += self._round_input_tokens
        self._state.cost_total_output += self._round_output_tokens
        self._state.cost_total_cached += self._round_cached_tokens
        self._state.cost_total_duration += self._round_duration
        for i in range(3):
            self._pass_counter += 1
            self._state.cost_per_pass.append({
                "pass": i + 1,
                "cycle": round_index,
                "input": self._round_input_tokens // 3,
                "output": self._round_output_tokens // 3,
                "cached": self._round_cached_tokens // 3,
                "duration_s": round(self._round_duration / 3.0, 3),
            })
        self._state.cost_passes = self._pass_counter
        # Reset round accumulators for next round.
        self._round_input_tokens = 0
        self._round_output_tokens = 0
        self._round_cached_tokens = 0
        self._round_duration = 0.0
        from .receipt import write_receipts
        from .verify import parse_diff_files
        diff_text = self.resolved_review.git_diff
        diff_files = parse_diff_files(diff_text) if diff_text else None
        write_receipts(
            receipts_dir=self.cwd / ".code-forge" / "receipts",
            round_index=round_index,
            l1_findings=l1_findings,
            diff_sha256=self.source_hash,
            source_files=list(self._source_files()),
            cwd=self.cwd,
            diff_files=diff_files,
            diff_text=diff_text,
            reviewer_excerpts=l1_excerpts,
        )
        self._persist_state()
        if self.post_round_hook is not None:
            self.post_round_hook(round_index)

    def _apply_autofix_loop_to(
        self, findings: list[StateFinding]
    ) -> None:
        """LOCAL only: attempt auto-fix on the given finding list.

        STATE-08 parameterized: operates on the provided list (L0 only)
        rather than the merged full list. For each CONFIRMED finding:
          - If fix_attempts >= max_fix_attempts: promote to UNCERTAIN
            (DISPO-05, exactly once per fingerprint)
          - Else: invoke autofixer.fix()
            - SUCCESS -> FIXED
            - PARSE_FAIL -> revert_fn(finding) + fix_attempts++
            - NO_CHANGE -> fix_attempts++ (no revert needed)
            - EXCEPTION -> fix_attempts++ + infra_errors append
        """
        mode_hint = self.resolved_review.mode_hint
        for finding in findings:
            # Coverage-gap and gate-mechanism findings skip autofix:
            # they are not code defects the autofix loop can address.
            if finding.source in ("MUTANT", "E2E_CHECK", "FIXVAL", "INFRA"):
                continue
            if finding.disposition != Disposition.CONFIRMED:
                continue
            fp = finding.fingerprint
            attempts = self._state.fix_attempts.get(fp, 0)

            # DISPO-05: promote CONFIRMED -> UNCERTAIN once
            if attempts >= self.max_fix_attempts:
                finding.disposition = Disposition.UNCERTAIN
                self._state.promoted_fingerprints.add(fp)
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

    def _fixpoint_reached(self) -> "_FixpointResult":
        """Severity-tiered fixpoint for LOCAL mode.

        Clauses (a) and (b) are safety nets that fire BEFORE severity tiering:
          (a) Any NEW CONFIRMED finding (not in prior round) -> RESET
          (b) Any FIXED->CONFIRMED reversion -> RESET
        Only recurring CONFIRMED findings reach the tier check.

        After (a)/(b):
          - Any UNCERTAIN -> RESET  (clause d, unchanged)
          - Zero CONFIRMED -> CLEAN
          - P0 or P1 CONFIRMED -> RESET
          - P2 CONFIRMED (no P0/P1) -> CYCLE_RESTART (resets clean-round counter)
          - Only P3 CONFIRMED -> density check (skipped when no git_diff):
              distinct_per_file > threshold -> CYCLE_RESTART
              distinct_per_diff > threshold -> CYCLE_RESTART
              density > threshold -> CYCLE_RESTART
              else -> CLEAN (P3 below threshold counts as clean)
        """
        from .flow_contract import (
            P3_DENSITY_THRESHOLD,
            P3_DISTINCT_PER_DIFF_THRESHOLD,
            P3_DISTINCT_PER_FILE_THRESHOLD,
        )

        history = self._state.round_history
        current_disps = {
            f.fingerprint: f.disposition
            for f in self._state.findings
        }

        # Prior round dispositions (empty set for round 0)
        if len(history) >= 2:
            prior_disps = history[-2].get("dispositions", {})
        else:
            prior_disps = {}

        # (a) zero NEW CONFIRMED this round -- any new finding = full reset
        for fp, disp in current_disps.items():
            if disp == Disposition.CONFIRMED and fp not in prior_disps:
                return _FixpointResult.RESET

        # (b) zero FIXED->CONFIRMED reversions
        for fp, disp in current_disps.items():
            if (
                disp == Disposition.CONFIRMED
                and prior_disps.get(fp) == "FIXED"
            ):
                return _FixpointResult.RESET

        confirmed = [
            f for f in self._state.findings
            if f.disposition == Disposition.CONFIRMED
        ]

        # (d) zero UNCERTAIN remain (unchanged from binary version)
        for f in self._state.findings:
            if f.disposition == Disposition.UNCERTAIN:
                return _FixpointResult.RESET

        # (c) zero CONFIRMED -> CLEAN
        if not confirmed:
            return _FixpointResult.CLEAN

        # Severity-tiered check on recurring CONFIRMED findings
        tiers = [_severity_tier(f) for f in confirmed]

        if "P0" in tiers or "P1" in tiers:
            return _FixpointResult.RESET

        if "P2" in tiers:
            return _FixpointResult.CYCLE_RESTART

        # Only P3: apply density thresholds
        p3_findings = confirmed  # all are P3 at this point
        total_p3 = len(p3_findings)

        def _rule_type(f: StateFinding) -> str:
            desc = f.description or ""
            if desc.startswith("P3:"):
                return desc[3:].split("(")[0].strip()
            return desc[:50].strip()

        from collections import defaultdict
        by_file: dict = defaultdict(set)
        all_rules: set = set()
        for f in p3_findings:
            rt = _rule_type(f)
            by_file[f.file].add(rt)
            all_rules.add(rt)

        distinct_per_file = max(len(rules) for rules in by_file.values()) if by_file else 0
        distinct_per_diff = len(all_rules)

        if (
            distinct_per_file > P3_DISTINCT_PER_FILE_THRESHOLD
            or distinct_per_diff > P3_DISTINCT_PER_DIFF_THRESHOLD
        ):
            return _FixpointResult.CYCLE_RESTART

        # Density check only when diff is available -- no diff means no meaningful
        # line count, so we skip rather than falling back to a misleading denominator.
        if self.resolved_review.git_diff:
            from .diff import count_diff_lines
            changed_lines = max(1, count_diff_lines(self.resolved_review.git_diff))
            if total_p3 / changed_lines > P3_DENSITY_THRESHOLD:
                return _FixpointResult.CYCLE_RESTART

        return _FixpointResult.CLEAN

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
        """R3 LOW5: terminal state writer for LOCAL fixpoint exit.

        FIXVAL gate: runs only on otherwise-GREEN diffs, after
        convergence, before the verdict is written. Hollow tests block
        with FAIL; non-hollow proceed to PASS. Overfit guard runs on
        PASS status (advisory only, never blocking).
        """
        from .fixval import (
            FixvalSkip,
            FixvalStatus,
            classify_fixval_candidate,
            run_fixval,
            run_overfit_guard,
        )

        changed_files = [str(f) for f in self._source_files()]
        candidate = classify_fixval_candidate(changed_files)

        if isinstance(candidate, FixvalSkip):
            # Record SKIPPED with reason (never silent)
            skip_finding = StateFinding(
                id="FIXVAL_SKIPPED",
                fingerprint="fixval-skipped",
                source="FIXVAL",
                disposition=Disposition.DISMISSED,
                file="",
                line_range=[],
                description="FIXVAL skipped: %s" % candidate.reason,
            )
            self._state.findings.append(skip_finding)
            # Skip is not a block -- proceed to PASS
            self._state.verdict = Verdict.PASS
            self._state.converged = True
            self._write_ledger_rows()
            self._persist_state()
            return

        # FixvalCandidate: run the gate
        try:
            from .gate_check import load_gate_config

            config = load_gate_config(
                self.cwd / ".code-forge" / "gate.yaml"
            )
            test_cmd = config["test"]["command"]
        except Exception as exc:  # noqa: BLE001
            self._state.infra_errors.append(
                "FIXVAL: gate.yaml missing or test.command not "
                "configured: %s" % exc
            )
            # Cannot run FIXVAL without test command -- proceed to PASS
            self._state.verdict = Verdict.PASS
            self._state.converged = True
            self._write_ledger_rows()
            self._persist_state()
            return

        commit_message = self._get_commit_message()
        diff_text = self.resolved_review.git_diff

        result = run_fixval(
            candidate, test_cmd, self.cwd, commit_message, diff_text,
        )

        # Extend findings and advisories
        self._state.findings.extend(result.findings)
        self._advisories.extend(result.advisories)

        if result.status == FixvalStatus.BLOCK:
            # Hollow test blocks the pipeline
            # block_message stored in the FIXVAL_HOLLOW finding's error
            for f in result.findings:
                if f.id == "FIXVAL_HOLLOW":
                    f.error = result.block_message
            self._state.verdict = Verdict.FAIL
            self._state.converged = False
            self._write_ledger_rows()
            self._persist_state()
            return

        if result.status == FixvalStatus.PASS:
            # Non-hollow: run overfit guard (advisory only)
            overfit_advisories = run_overfit_guard(
                candidate, test_cmd, self.cwd,
            )
            self._advisories.extend(overfit_advisories)

        # PASS / SKIPPED / WAIVED -- proceed to PASS verdict
        self._state.verdict = Verdict.PASS
        self._state.converged = True
        self._write_ledger_rows()
        self._persist_state()

    def _build_ledger_row(
        self,
        fingerprint: str,
        file: str,
        line: int,
        axis_claim: str,
        pass_provenance: str,
        terminal_state: TerminalState,
        evidence_class: str,
        ts: str,
        version_sensitive: bool = False,
        repo_root: str | None = None,
    ) -> LedgerRow:
        """Construct a LedgerRow from review state (shared between local and CI writers)."""
        base = self.resolved_review.base_sha
        head = self.resolved_review.head_sha
        resolved_root = repo_root or str(resolve_ledger_root(self.cwd).resolve())
        return LedgerRow(
            fingerprint=fingerprint,
            repo_root=resolved_root,
            base_sha=base or "",
            head_sha=head or "",
            file=file,
            line=line,
            axis_claim=axis_claim,
            pass_provenance=pass_provenance,
            terminal_state=terminal_state,
            evidence_class=evidence_class,
            ts=ts,
            version_sensitive=version_sensitive,
        )

    def _write_ledger_rows(self) -> int:
        """Append terminal-StateFinding rows to .code-forge/ledger.jsonl (LOCAL mode).

        One row per finding whose disposition is FIXED or DISMISSED.
        UNCERTAIN and still-open CONFIRMED findings are not terminal
        and yield no row. Returns the number of rows written; non-git
        runs (where SHAs are unavailable) yield 0 to satisfy the
        "no placeholder/empty values" invariant.

        Best-effort dedup: if a (fingerprint, terminal_state) pair is
        already present in the ledger from any prior run, we do not
        append a duplicate row regardless of SHAs. This is a
        TOCTOU window but the worst case is one extra row per
        convergence -- still monotonic non-decreasing, still
        append-only, still auditable.
        """
        base = self.resolved_review.base_sha
        head = self.resolved_review.head_sha
        if base is None or head is None:
            return 0
        from .ledger import iter_rows
        ledger_root = resolve_ledger_root(self.cwd)
        existing = {
            (r.fingerprint, r.terminal_state)
            for r in iter_rows(ledger_root)
        }
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = 0
        for f in self._state.findings:
            if f.disposition == Disposition.FIXED:
                state = TerminalState.FIXED
            elif f.disposition == Disposition.DISMISSED:
                state = TerminalState.DISPROVED
            else:
                continue
            if (f.fingerprint, state) in existing:
                continue
            evidence = (
                "fix_applied"
                if state == TerminalState.FIXED
                else (f.error or "falsifier_rejected")
            )
            ct = derive_claim_type(f.source)
            row = self._build_ledger_row(
                fingerprint=f.fingerprint,
                file=f.file,
                line=f.line_range[0] if f.line_range else 0,
                axis_claim=ct.type,
                pass_provenance=f.source,
                terminal_state=state,
                evidence_class=evidence,
                ts=ts,
                version_sensitive=ct.version_sensitive,
                repo_root=str(ledger_root.resolve()),
            )
            ledger_append(ledger_root, row)
            existing.add((f.fingerprint, state))
            rows += 1
        return rows

    def _write_ci_ledger_rows(self) -> int:
        """Append UNADJUDICATED rows from CI review runs to the main repo ledger.

        D-01: CI review runs record CONFIRMED and style-downgraded findings as
        UNADJUDICATED rows so the self-evolution corpus receives real reviewed
        signal. On zero-finding PASS, a diff-scoped clean row is emitted
        (axis_claim="clean", file="", line=0, fingerprint=sha256("clean:<base>:<head>")[:16]).

        D-05, D-11, D-20b: Persistence routes to the main repo root via
        `resolve_ledger_root(self.cwd)`. The row's `repo_root` field records the
        main repo path so downstream diff extraction survives worktree cleanup.

        D-08: Dedup key for UNADJUDICATED rows is (fingerprint, base_sha, head_sha).
        Re-running the same diff does not re-append duplicate rows.

        D-16: Growth expectations and compaction trigger: append-only growth is
        ~20 CI runs/day (~3600 rows/repo/6mo), millisecond-scale full scans.
        Compaction / rotation is deferred until >10k rows or measured write-latency
        regression.

        D-19: Two-layer kill-switch:
          1. Environment variable CODE_FORGE_DISABLE_LEDGER (truthy).
          2. gate.yaml `ledger.enabled: false`, read via tolerant raw YAML.
        Failure isolation: wrapped in try/except (OSError, yaml.YAMLError,
        UnicodeDecodeError, AttributeError, TypeError). Any read/write failure
        is logged to stderr and appended to infra_errors; review verdict is NEVER
        altered (fail-open default).
        """
        import hashlib
        import os
        import sys
        import yaml
        from datetime import datetime, timezone
        from .ledger import iter_rows

        # Layer 1: Environment variable kill-switch
        try:
            env_val = os.environ.get("CODE_FORGE_DISABLE_LEDGER", "").strip().lower()
            if env_val in ("1", "true", "yes", "on"):
                return 0
        except Exception:
            pass

        # Layer 2: gate.yaml kill-switch (tolerant raw YAML load, fail-open on parse error)
        try:
            gate_path = self.cwd / "gate.yaml"
            if gate_path.exists():
                with open(gate_path, "r", encoding="utf-8") as f:
                    gate_data = yaml.safe_load(f)
                if isinstance(gate_data, dict):
                    ledger_cfg = gate_data.get("ledger")
                    if isinstance(ledger_cfg, dict) and ledger_cfg.get("enabled") is False:
                        return 0
        except (OSError, yaml.YAMLError, UnicodeDecodeError, AttributeError, TypeError) as exc:
            msg = f"CI: gate.yaml ledger kill-switch read failed (fail-open, ledger enabled): {exc}"
            self._state.infra_errors.append(msg)
            print(msg, file=sys.stderr)

        # Write path: failure-isolated (degrades to warning on write error)
        try:
            base = self.resolved_review.base_sha
            head = self.resolved_review.head_sha
            if base is None or head is None:
                return 0

            ledger_root = resolve_ledger_root(self.cwd)
            existing = {
                (r.fingerprint, r.base_sha, r.head_sha)
                for r in iter_rows(ledger_root)
            }
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            # Collect findings in scope: CONFIRMED + style-downgraded findings (CP1 W-5)
            findings_to_write = [
                f for f in self._state.findings
                if f.disposition == Disposition.CONFIRMED or getattr(f.disposition, "value", str(f.disposition)) == "STYLE"
            ]

            rows_written = 0
            if not findings_to_write:
                # Zero-finding PASS: emit a single clean row with diff-scoped fingerprint (D-07)
                if self._state.verdict == Verdict.PASS:
                    clean_fp = hashlib.sha256(f"clean:{base}:{head}".encode("utf-8")).hexdigest()[:16]
                    if (clean_fp, base, head) not in existing:
                        row = self._build_ledger_row(
                            fingerprint=clean_fp,
                            file="",
                            line=0,
                            axis_claim="clean",
                            pass_provenance="CI",
                            terminal_state=TerminalState.UNADJUDICATED,
                            evidence_class="clean_pass",
                            ts=ts,
                            version_sensitive=False,
                            repo_root=str(ledger_root.resolve()),
                        )
                        ledger_append(ledger_root, row)
                        existing.add((clean_fp, base, head))
                        rows_written += 1
                return rows_written

            for f in findings_to_write:
                if (f.fingerprint, base, head) in existing:
                    continue
                evidence = f.error or f.description or "confirmed_finding"
                ct = derive_claim_type(f.source)
                row = self._build_ledger_row(
                    fingerprint=f.fingerprint,
                    file=f.file,
                    line=f.line_range[0] if f.line_range else 0,
                    axis_claim=ct.type,
                    pass_provenance=f.source,
                    terminal_state=TerminalState.UNADJUDICATED,
                    evidence_class=evidence,
                    ts=ts,
                    version_sensitive=ct.version_sensitive,
                    repo_root=str(ledger_root.resolve()),
                )
                ledger_append(ledger_root, row)
                existing.add((f.fingerprint, base, head))
                rows_written += 1

            return rows_written

        except (OSError, yaml.YAMLError, UnicodeDecodeError, AttributeError, TypeError) as exc:
            msg = f"CI: ledger write failure (degraded to warning): {exc}"
            self._state.infra_errors.append(msg)
            print(msg, file=sys.stderr)
            return 0

    def _get_commit_message(self) -> str:
        """Read the current commit message (worktree-safe).

        Uses git rev-parse --git-path COMMIT_EDITMSG to resolve the
        correct path (worktree-safe: .git may be a file, not a dir).
        Falls back to git log -1 --format=%B for post-commit / CI.
        Returns empty string on any failure (with logger.warning).
        """
        import subprocess as _sp

        logger = logging.getLogger("code_forge")

        try:
            result = _sp.run(
                ["git", "rev-parse", "--git-path", "COMMIT_EDITMSG"],
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                check=False,
                cwd=str(self.cwd),
            )
            if result.returncode == 0:
                path = Path(result.stdout.strip())
                if not path.is_absolute():
                    path = self.cwd / path
                if path.exists():
                    return path.read_text(encoding="utf-8").strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "FIXVAL: COMMIT_EDITMSG read failed: %s", exc
            )

        # Fallback: git log -1 for post-commit / CI
        try:
            result = _sp.run(
                ["git", "log", "-1", "--format=%B"],
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                check=False,
                cwd=str(self.cwd),
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "FIXVAL: git log fallback failed: %s", exc
            )

        return ""

    def _run_advisory_axes(self) -> None:
        """Post-convergence dispatch point for advisory axes.

        Iterates self.advisory_runners, calls runner.run() on each,
        and extends self._advisories with results. Advisory findings
        are stored in self._advisories (list[AdvisoryFinding]), completely
        separate from self._state.findings (list[StateFinding]).
        """
        diff_text = self.resolved_review.git_diff or ""
        # Inject source_files for runners that support it (no git dependency).
        for runner in self.advisory_runners:
            if hasattr(runner, "source_files"):
                runner.source_files = list(
                    self.resolved_review.source_files
                )
            if hasattr(runner, "registry"):
                runner.registry = self.registry
            if hasattr(runner, "_runtime_runner"):
                from .runtime import RuntimeRunner as _RuntimeRunner

                for candidate in self.advisory_runners:
                    if isinstance(candidate, _RuntimeRunner):
                        runner._runtime_runner = candidate
                        break
        for runner in self.advisory_runners:
            try:
                findings = runner.run(diff_text, self.cwd)
                self._advisories.extend(findings)
            except Exception as exc:  # noqa: BLE001
                self._state.infra_errors.append(
                    "advisory runner failed: %r\n%s" % (exc, traceback.format_exc())
                )
            # Collect infra_errors from runners that track them.
            if hasattr(runner, "infra_errors"):
                self._state.infra_errors.extend(runner.infra_errors)

    def _serialize_advisories(self) -> None:
        """Write advisory findings to advisory-findings.json.

        Separate file from review-state.json. Uses tmp+replace atomic
        pattern matching state.py convention.
        """
        if not self._advisories:
            return
        out_dir = self.cwd / ".code-forge"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "advisory-findings.json"
        tmp_path = out_path.with_suffix(".tmp")
        data = [asdict(f) for f in self._advisories]
        tmp_path.write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )
        tmp_path.replace(out_path)

    def _display_smoke_status(self) -> None:
        """Print the RUNTIME smoke status block to stderr.

        Precondition: only called when a RuntimeRunner is in advisory_runners.

        Three cases:
          (a) runtime-smoke-summary finding: display verified/unverified counts.
          (b) runtime-skipped finding: print UNVERIFIED (axis skipped: reason).
          (c) neither: print "smoke: no runtime surfaces detected" fallback.

        Always prints -- silence never reads as verified.
        Called BEFORE the early-return guard in _display_advisories so an
        empty _advisories list does not suppress the smoke status.
        """
        from .runtime import RuntimeRunner as _RuntimeRunner

        # Only print when a RuntimeRunner is present in advisory_runners.
        has_runtime = any(
            isinstance(r, _RuntimeRunner) for r in self.advisory_runners
        )
        if not has_runtime:
            return

        print("", file=sys.stderr)
        print("--- Smoke Status ---", file=sys.stderr)

        # Case (a): summary finding
        for f in self._advisories:
            if f.id == "runtime-smoke-summary":
                print(f.description, file=sys.stderr)
                return

        # Case (b): skipped finding
        for f in self._advisories:
            if f.id == "runtime-skipped":
                desc = f.description
                prefix = "RUNTIME axis SKIPPED: "
                reason = desc[len(prefix):] if desc.startswith(prefix) else desc
                print(
                    "smoke: UNVERIFIED (axis skipped: %s)" % reason,
                    file=sys.stderr,
                )
                return

        # Case (c): fallback -- no summary and no skipped finding
        print("smoke: no runtime surfaces detected", file=sys.stderr)

    def _display_advisories(self) -> None:
        """Display advisory findings on stderr after separator.

        Smoke status is always printed first when a RuntimeRunner is
        present, before the early-return guard. The generic advisory loop
        skips runtime-smoke-summary and runtime-skipped findings to avoid
        double-printing.

        Each generic finding formatted as: [AXIS] file:line_range - description
        """
        # smoke status ALWAYS prints when RuntimeRunner is present,
        # even when _advisories is empty (before early-return guard).
        self._display_smoke_status()

        # IDs exclusively displayed by _display_smoke_status -- skip in generic loop.
        _RUNTIME_EXCLUSIVE_IDS = {"runtime-smoke-summary", "runtime-skipped"}

        if not self._advisories:
            return
        print("", file=sys.stderr)
        print("--- Advisory ---", file=sys.stderr)
        for f in self._advisories:
            if f.id in _RUNTIME_EXCLUSIVE_IDS:
                continue
            line_str = "%d-%d" % (f.line_range[0], f.line_range[1]) \
                if len(f.line_range) == 2 else str(f.line_range)
            print(
                "[%s] %s:%s - %s" % (f.axis, f.file, line_str, f.description),
                file=sys.stderr,
            )

    def _merge_findings(
        self,
        l0_findings: list[StateFinding],
        l1_findings: list[StateFinding],
        l2_findings: list[StateFinding] = None,
        e2e_findings: list[StateFinding] = None,
        coverage_findings: list[StateFinding] = None,
    ) -> list[StateFinding]:
        """Merge L0 + L1 + L2 + E2E + COVERAGE by fingerprint.

        FP-04: L0 wins on conflict. Merge order (lowest priority first;
        higher overwrites): coverage/e2e (lowest) -> l2 -> l1 -> l0
        (highest). COVERAGE fingerprints use a "coverage:" prefix and E2E
        a "e2e-" prefix; neither collides with L0/L1/L2 fingerprints, so
        the ordering is defensive correctness.
        """
        merged: dict[str, StateFinding] = {}
        # coverage + e2e lowest priority: insert first so l2/l1/l0 win.
        for f in (coverage_findings or []):
            merged[f.fingerprint] = f
        for f in (e2e_findings or []):
            merged[f.fingerprint] = f
        for f in (l2_findings or []):
            merged[f.fingerprint] = f
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
        l2_findings: list[StateFinding] = None,
        e2e_findings: list[StateFinding] = None,
    ) -> None:
        """Append per-round snapshot to round_history for STATE-05."""
        snapshot = {
            "round": round_index,
            "l0_fingerprints": [f.fingerprint for f in l0_findings],
            "l1_fingerprints": [f.fingerprint for f in l1_findings],
            "l2_fingerprints": [
                f.fingerprint for f in (l2_findings or [])
            ],
            "e2e_fingerprints": [
                f.fingerprint for f in (e2e_findings or [])
            ],
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

    def _count_coverage_gaps(self) -> int:
        """Count active COVERAGE findings (per-file review gaps).

        A COVERAGE finding marks an in-scope file that no review layer
        examined. DISMISSED (human-waived) gaps do not count.
        """
        return sum(
            1 for f in self._state.findings
            if f.source == "COVERAGE"
            and f.disposition != Disposition.DISMISSED
        )

    def _persist_state(self) -> None:
        """Write state.json to cwd/.code-forge/state.json."""
        state_path = self.cwd / ".code-forge" / "state.json"
        save_state(self._state, state_path)

    def _unlink_mutation_result(self, result_path: Path) -> None:
        """Remove a mutation-result.json this round has consumed.

        Isolated in its own try/except so a delete failure is reported
        accurately instead of being folded into the caller's broader
        "failed to read" message.
        """
        try:
            result_path.unlink()
        except OSError as e:
            self._state.infra_errors.append(
                "CI: failed to remove mutation-result.json: %s" % e
            )

    def _source_files(self) -> list[Path]:
        """Return source files from resolved review."""
        return self.resolved_review.source_files
