---
phase: 02
phase_name: state-machine-rewrite
extracted: 2026-05-18
method: manual (gsd-extract_learnings skill could not find required SUMMARY.md artifacts)
artifacts_consumed:
  - 02-01-PLAN.md through 02-06-PLAN.md (6 plans)
  - .planning/MILESTONES.md (v2.0.0a1 ship summary)
  - cross-AI synthesis files /tmp/forge_0206r{1,2b,3,4,5,6,7}_synthesis.txt
  - memory: forge-plan-gate-corpus, ai-review-strategic-limits, forge-v2-finalized
  - this conversation H1-H8 strategic hypothesis analysis
target_consumer: plan-forge v0.1 corpus input (informs G1-G8 enforcement design)
missing_artifacts:
  - SUMMARY.md (none created during Phase 2)
  - VERIFICATION.md (none)
  - UAT.md (none)
  - STATE.md (none)
---

# Phase 2 LEARNINGS

50+ rounds of cross-AI plan review across 6 sub-plans (02-01..02-06), spanning 7 days,
producing 4,379 LOC + 521 tests, but consuming 5+ user strategic interventions.
This file extracts decisions/lessons/patterns/surprises specifically to inform
plan-forge G1-G8 epistemological enforcement design.

## 1. Decisions

### D1: State machine as separate phase
- **What**: Disposition + state machine extracted from Phase 1 prototype into Phase 2
- **Why**: v1.0 skill orchestrator hardcoded dispositions; v2.0 wanted persistence + HOLD
- **Source**: 02-01-PLAN.md, 02-02-PLAN.md
- **Plan-forge implication**: scope-expansion decision NOT challenged via reference class
  ("what does v1.0 state mgmt look like? do we need a state machine at all?"). G1 absence.

### D2: 7-round AI panel review per sub-plan
- **What**: Each plan goes Kimi + DeepSeek + Mimo until 0 BLOCKER 0 HIGH
- **Why**: cross-model consensus filters individual model bias
- **Source**: synthesis files R1-R7 across 02-06
- **Plan-forge implication**: AI panel = peer review NOT red team; G3 pre-mortem absent

### D3: Stub Falsifier / Stub AutoFixer in Phase 2
- **What**: real Falsifier deferred to Phase 4, real AutoFixer deferred to Phase 6
- **Why**: Phase 2 owns state mgmt only; falsification + fix are separate concerns
- **Source**: 02-05-PLAN.md "Out of Scope"
- **Plan-forge implication**: Phase 2 ships with 0 ground-truth bug-catch capability;
  all review is paper-review. G6 plan-vs-vision: at Phase 2 ship, v2.0 catches 0 real
  bugs in real code (vs v1.0 demo caught 4 in kimi-next-key.sh).

### D4: SARIF 2.1.0 stdout in CI mode
- **What**: CI emits SARIF; LOCAL does not
- **Why**: state.json is canonical for LOCAL; SARIF for GitHub/GitLab integrators
- **Source**: 02-06-PLAN.md Goal section
- **Plan-forge implication**: integrator-driven feature with no actual integrator yet;
  G1 reference class would ask "how many users have CI SARIF needs?" (answer: ~0)

### D5: ForgeLock scope (emission INSIDE with-block, return OUTSIDE)
- **What**: lock held during SARIF emission to prevent race
- **Why**: <10ms emission cost, prevents concurrent forge stdout interleaving
- **Source**: 02-06-PLAN.md R1 B2 fix; SC-26 / SC-50
- **Plan-forge implication**: design decision requiring 2 review rounds to crystallize;
  caught only because user-reviewer flagged contradiction (text said "inside" but
  pseudocode showed "outside"). AI panel did NOT catch the contradiction unaided.

### D6: line_range 1-based (not 0-based)
- **What**: StateFinding.line_range follows parsers/base.py Finding.line definition (1-based)
- **Why**: SARIF spec uses 1-based startLine; passthrough simpler than +1 conversion
- **Source**: 02-06-PLAN.md SC-46; subagent implementation note
- **Plan-forge implication**: cross-plan invariant verified by SUBAGENT at impl time,
  NOT by AI panel during plan review. Plans existed with both interpretations possible
  through R7; no AI reviewer asked.

## 2. Lessons

### L1: AI panel converges to "internally consistent" not "correct"
- **What**: Mimo R7 APPROVE meant "plan is self-consistent", not "plan matches reality"
- **Context**: 50+ rounds across Phase 2; per ai-review-strategic-limits memory, panel
  also converged to *wrong* architecture (3-mode/git-only/no FEEDBACK) until user 5+
  interventions corrected. Panel never asked "is this the right architecture?"
- **Source**: synthesis R7 + ai-review-strategic-limits memory + this conversation
- **Plan-forge implication**: G6 plan-vs-vision and G7 scope-challenge are NOT
  emergent from AI panel; must be ENFORCED mechanically before AI panel runs.

### L2: Plan size > 700 lines breaks single-pass review attention
- **What**: 02-06-PLAN.md grew to 743 lines; R4-R6 found ~1-2 LOW/INFO per round
  even with no real defects
- **Context**: corpus 7 anti-pattern showed reviewer attention budget exhausts before
  plan does. After R3 design clear, remaining rounds are polish noise.
- **Source**: forge-plan-gate-corpus memory + R5/R6 synthesis
- **Plan-forge implication**: mechanical checks scale O(plan_size); should absorb
  70%+ of recurring LOW/INFO findings, freeing AI panel for design checks.

### L3: Own-edit fixes introduce regressions
- **What**: R2b L6 "remove duplicate pseudocode" left BOTH copies (R3 B1 BLOCKER)
- **Context**: edit-in-place changes one local string but doesn't grep file for
  other instances of same fact-cluster. The fix is local; staleness is global.
- **Source**: synthesis R3 + corpus pattern 4
- **Plan-forge implication**: every fix round must auto-grep fact-phrase across plan;
  if multiple hits, raise warning. Mechanical check, not LLM.

### L4: Cross-plan claims about other sub-plans are unverified
- **What**: 02-06 R1 claimed "infra_errors not in State" (false; 02-04 a10edfa added).
  02-06 R4 claimed "main() B2 PENDING guard prevents reach _emit_ci_output" (false;
  no such guard exists in 02-05 main()).
- **Context**: AI authors trust memory of prior plan design without grep evidence.
  AI reviewers also miss because they don't auto-fetch upstream source.
- **Source**: synthesis R1 (B1 FALSE POSITIVE) + R4 (B2 doc inconsistency)
- **Plan-forge implication**: every cross-plan claim (regex: "02-0[1-5]" or named
  upstream symbols) requires grep evidence in plan body or audit notes. Mechanical.

### L5: Subagent silent cleanup violates scope discipline
- **What**: 02-06 subagent removed `BaselineSpec` unused import (out-of-scope cleanup)
  silently, violating CLAUDE.md "Pre-existing bug handling: do NOT silently fix"
- **Context**: subagent treated unused import as "trivially safe to delete";
  no separate commit, no follow-up note
- **Source**: verification check during this conversation
- **Plan-forge implication**: subagent execution must include `git diff --name-only`
  audit and out-of-scope file detection; silent cleanup detection.

### L6: Review tools accumulate work artifacts that pollute worktree
- **What**: forge review (qodo, code-review-expert, adversarial-qe) left REVIEW.md
  untracked in worktree; not in commits but in working tree
- **Context**: 3-cycle review produces tool output files; cleanup not automatic
- **Source**: `git status --short` showed `?? REVIEW.md`
- **Plan-forge implication**: review pipelines should specify "leave-no-trace"
  artifact policy or commit-then-discard cleanup.

### L7: Critical-path metric was wrong (plan optimization vs code shipping)
- **What**: 50+ rounds of plan review without shipping any feature that catches a
  real bug in real code; v1.0 ships 1 demo catching 4 real findings
- **Context**: v1.0 had ground truth (kimi-next-key.sh); v2.0 Phase 2 has 0 equivalent.
  Sunk cost of 50+ rounds made "v2.0 cannot be abandoned" frame stick.
- **Source**: ai-review-strategic-limits + forge-v2-finalized memories +
  H1-H8 strategic analysis this conversation
- **Plan-forge implication**: G1 reference class must include "ships per investment
  ratio" metric. plans that promise high ship rate but produce 0 real-world catches
  = failed plans.

### L8: GSD workflow lacks falsification gates
- **What**: gsd-execute-phase / gsd-next / gsd-autonomous all forward-execution skills;
  gsd-list-phase-assumptions / gsd-discuss-phase / gsd-explore are opt-in
- **Context**: user (Minxi) observation in this conversation: "GSD is confirmatory,
  not disconfirmatory"
- **Source**: user analysis embedded in this conversation
- **Plan-forge implication**: G3 (pre-mortem) and G6 (plan-vs-vision) must be
  MANDATORY gates in plan-forge; opt-in defeats the purpose.

## 3. Patterns (worked well, reusable)

### P1: 2-atomic-commit per sub-plan
- **Pattern**: split implementation into (a) new module + tests + fixtures, (b) wire
  into existing module + integration tests
- **When to use**: any feature with clean module + integration split
- **Source**: 02-06-PLAN.md Task 13; subagent honored split
- **Plan-forge use**: plan-forge implementation itself can follow this pattern

### P2: ASCII Step 0c check before commit
- **Pattern**: `git diff --diff-filter=AM -U0 | grep '^+' | grep -P '[^\x00-\x7F]'`
- **When to use**: every commit (catches LLM-emitted em dash / smart quotes / arrows)
- **Source**: CLAUDE.md Step 0c; verified clean across Phase 2 commits
- **Plan-forge use**: built-in check; reuse the bash one-liner

### P3: SC table with structured fields
- **Pattern**: numbered SC-N entries with rationale, test reference, source attribution
- **When to use**: any plan with multiple acceptance criteria
- **Source**: 02-06-PLAN.md SC-1..SC-50
- **Plan-forge use**: SC table is mechanical-check fodder; F1 SC-test traceability
  check leverages this pattern

### P4: Convergence trajectory tracking
- **Pattern**: table of round x severity counts (R1/R2b/R3/R4/R5/R6/R7 BLOCKER/HIGH/MED/LOW)
- **When to use**: any multi-round review cycle
- **Source**: synthesis files R3-R7
- **Plan-forge use**: convergence is a metric; if rounds > N without convergence,
  scope is wrong -- escalate scope-challenge not polish

### P5: Synthesis file per round
- **Pattern**: /tmp/<project>_<phase>_r<N>_synthesis.txt with verdict table + finding
  table + assessment + fix recommendation
- **When to use**: any cross-AI round; preserves R-tag history outside plan body
- **Source**: synthesis R1..R7
- **Plan-forge use**: same structure; CHANGELOG section reference

### P6: Worktree-per-sub-plan
- **Pattern**: `.worktrees/<phase>-<subplan>` with ancestry SHA verification
- **When to use**: any feature touching shared files
- **Source**: 02-06-PLAN.md Worktree Setup
- **Plan-forge use**: plan-forge implementation worktree pattern

## 4. Surprises

### S1: Plan size grew 700+ lines for a single sub-plan
- **What**: 02-06 SARIF emission (a small CI output feature) reached 743 lines
- **Impact**: review attention exhaustion; LOW/INFO accumulation in late rounds
- **Source**: file wc
- **Plan-forge implication**: G7 scope-challenge should flag plans > 500 lines;
  big plan = either real complexity (split) or over-spec (trim)

### S2: 7 rounds to converge on a polish-stage sub-plan
- **What**: 02-06 R1 design done by R3 essentially; R4-R7 polish + late-introduced
  regressions (own-fix)
- **Impact**: 4 rounds spent on polish instead of next sub-plan
- **Source**: synthesis trajectory
- **Plan-forge implication**: convergence rate per round should be exponential decay;
  plateau or oscillation = scope wrong, not polish needed

### S3: Mimo CONDITIONAL -> APPROVE -> CONDITIONAL oscillation
- **What**: Mimo APPROVE R4, R5; CONDITIONAL R6; APPROVE R7 (not monotonic)
- **Impact**: 3-model consensus is not stable; depends on which polish issues
  trigger each model's threshold
- **Source**: synthesis R4-R7 verdicts
- **Plan-forge implication**: 0/0/0 convergence is fragile signal; better gate
  is "N consecutive rounds 0/0/0" not "1 round 0/0/0"

### S4: Subagent test coverage exceeded plan minimum
- **What**: plan said 22 test_sarif cases minimum; subagent shipped 34
- **Impact**: positive surprise -- better SC-9 + SC-19 coverage than plan required
- **Source**: implementation commit 9b354df
- **Plan-forge implication**: implementation can EXCEED plan; verification should
  check >= plan, not == plan

### S5: 02-06 review took 7 rounds despite being final sub-plan in series
- **What**: expected plan-author skill accumulates; later sub-plans should review
  faster. 02-06 was as long as 02-04 / 02-05
- **Impact**: no economies of scale; each sub-plan still expensive
- **Source**: round counts per sub-plan
- **Plan-forge implication**: per-plan cost stays roughly constant; reducing review
  count per plan REQUIRES mechanical pre-checks (plan-forge mission)

### S6: gsd-extract_learnings skill cannot run on this Phase
- **What**: skill requires SUMMARY.md per phase; Phase 2 had none
- **Impact**: manual extraction needed; institutional learning capture not automated
- **Source**: this extraction itself
- **Plan-forge implication**: plan-forge should REQUIRE SUMMARY.md generation as
  part of completion; otherwise learning extraction is brittle

### S7: 02-06 R1 BLOCKER B1 was a FALSE POSITIVE
- **What**: Kimi R1 said "infra_errors not in State" -- actually it was added in 02-04
  (a10edfa). The reviewer didn't fetch upstream source.
- **Impact**: 1 round wasted on a non-issue
- **Source**: synthesis R1 + 02-04 git commit
- **Plan-forge implication**: AI reviewers need source-grep tool; if no grep happens,
  cross-plan claims are unreliable. Mechanical check (L4 pattern) catches this.

## 5. Meta-Learning (for plan-forge mission specifically)

Phase 2 cost: 50+ AI panel rounds x 3 models = 150+ model invocations + 5+ user
strategic interventions + 7 days calendar time. Output: 4379 LOC + 521 tests +
0 real-world bug catches (Stub Falsifier / Stub AutoFixer).

ROI question: was Phase 2 worth 150+ model invocations? Honest answer: yes for
the engineering scaffold (state machine + disposition + SARIF), no for the
review process (which converged to "internally consistent" not "captures real bugs").

The review process is what plan-forge must fix. Specifically:
- Replace AI-panel-as-quality-gate with mechanical + AI hybrid (corpus 70% mechanical)
- Add epistemological enforcement (G1-G8) that current GSD lacks (0/8 covered in
  122 skill library per skill search)
- Require ground-truth feedback loop (Phase 2 shipped without one; plan-forge must
  not repeat)

## Cross-references

- `~/.claude/projects/-home-houminxi/memory/project_forge_plan_gate_corpus.md` (7 anti-patterns)
- `~/.claude/projects/-home-houminxi/memory/feedback_ai_review_strategic_limits.md`
- `~/.claude/projects/-home-houminxi/memory/project_forge_v2_finalized.md`
- `/tmp/plan_falsification_synthesis.md` (Popper/Klein/Taleb/Wucker/Tetlock/Flyvbjerg synthesis)
- `/tmp/forge_0206r{1,2b,3,4,5,6,7}_synthesis.txt` (cross-AI review history)
- `.planning/MILESTONES.md` (v2.0.0a1 ship summary)
