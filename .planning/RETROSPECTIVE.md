# Forge Retrospective

## Milestone: v2.2 -- Trusted Review Execution

**Shipped:** 2026-06-04
**Phases:** 7 (Phases 5-11) | **Plans:** 17

### What Was Built

- Python toolchain auto-detection: ruff/pylint/flake8 parsers, pyproject.toml-aware, round-trip validated tools.yaml
- Pluggable backend abstraction: OpenAI/Anthropic HTTP + claude CLI, FORGE_BACKEND resolution, TTL-cached probe
- Outlet A (CLI dispatcher): SKILL.md dispatches to code-forge review subprocess, fail-closed, fresh process per pass
- Outlet B (inline merge): qodo + expert + adversarial merged into one SKILL.md section, no Invoke hang
- Outlet C (subagent): third outlet, fresh Agent per pass, fail-closed, strong model constraint
- Subprocess orphan protection + cost transparency (Phase 8)
- detect.py hardening: *.bash detection, stale-entry merge fix (Phase 10 follow-up)
- Reviewer Canary design spec (Phase 9): injection-based reviewer attention validation
- --whole-file PATH: whole-file review without pending diff (bonus, added after v2.2 phases)
- --model no-pin: session model runs, no forced pin (Phase 11)

### What Worked

- Forge review pipeline (9-pass Outlet B) as both QA gate AND development discipline -- catching 2 real bugs in Phase 10 that 9-pass static review missed initially
- Sub-session architecture for long-running work: main session guards constraints, sub-session executes
- history purge (filter-repo) before milestone close: clean public repo
- GSD workflow for artifact tracking: SUMMARY.md + VERIFICATION.md + UAT.md gives clear audit trail

### What Was Inefficient

- Phase 11 review took 12+ passes before genuine 3-cycle forge review (Outlet B) was enforced
- 20/22 requirements checkboxes not ticked during execution -- accumulated at milestone close instead of per-phase
- Phases 7 and 11 missing formal VERIFICATION.md -- UAT substituted at close
- Sub-session triage of sonnet's findings (BUG1+BUG3) was initially wrong (dismissed as FP), required main session correction

### Patterns Established

- Forge dogfooding (forge reviewing forge) as the primary quality gate
- `# post-review-c3` marker as commit gate enforcement
- Planning-local orphan branch for .planning/ persistence without public leak
- filter-repo + repo-recreate as clean purge mechanism for leaked files
- W1/W2 classification in audit integration checker (warning vs gap vs missing)

### Key Lessons

- diff-only review cannot catch cross-function inconsistency (BUG1+BUG3 both were docstring-implementation gaps)
- Correct-by-luck is still a process failure (Phase 11-02 viability gate: right answer, wrong mechanism)
- Stale help text is a real bug: "--baseline (non-git: empty)" directly caused the "forge can't review static files" misunderstanding
- BACKEND-01 partial gap (load_backend_configs not called) is the exact feature v2.3 will close -- the audit found the right next step

### Cost Observations

- Model mix: Opus 4.6 (main session), Sonnet 4.6 (sub-sessions), mimo/deepseek (plan review)
- Sessions: multiple across multiple days (2026-06-01 to 2026-06-04)
- Notable: cross-Pacific model (mimo) caused 113s/pass vs sonnet's 24s/pass -- latency proxy mattered for backend design

---
*Last updated: 2026-06-04 after v2.2 milestone*


## Milestone: v2.3 -- Backend Wiring + Anti-Shirk

**Shipped:** 2026-06-09
**Phases:** 6 (Phases 12-16) | **Plans:** 19

### What Was Built

- Backend API wiring: gate.yaml backends block routes review through DeepSeek/MiMo/Kimi APIs
- Vertex backend with fail-fast on zero-config (Phase 13.1 root-fix)
- Outlet C receipt verification and coverage-exempt patterns (Phase 14)
- Reviewer independence: conventions resolver + independent reviewer dispatch (Phase 15)
- Diff-size adaptive tiering: 2/3/4 clean cycles by line count (Phase 16)
- F3 false-green fix: INFRA findings skip falsifier, block fixpoint (Phase 16)

### What Worked

- Cross-model plan review (4 models: DS/MiMo/MM/Kimi) caught real consistency issues (test counts, XML structure, grep syntax) that single-model review missed
- Gatekeeper (human) caught semantic defect (tier_threshold(0)=2 vs RESEARCH "safe default=3") that 6 rounds of 4-model panel missed -- validates "panel catches mechanical, human catches semantic" thesis
- Sub-session architecture: main session as gatekeeper + constraint enforcer, sub-session as executor
- Worktree isolation for parallel plan execution within waves
- .planning leak caught and purged before push (squash 11->1 commit)

### What Was Inefficient

- Cross-model plan review non-convergence: R4-R6 spun on cosmetic nits (grep flags, duplicate cd, number wording) while missing the real semantic defect -- panel is not worth running past R4 for plans
- .planning files leaked into main via worktree merge commits (executor SUMMARY.md committed inside worktree, merge carried into main despite gitignore) -- need structural fix: executor should not commit .planning files, or merge should filter
- STATE.md drift: completed_phases counter fell behind across phases (2 vs actual 6) -- gsd-sdk phase.complete does not reliably sync all STATE.md counters

### Patterns Established

- Plan review exit: R4 panel + human gatekeeper, not unbounded cross-model voting
- F3-class defect pattern: error-path findings need source tagging to prevent false-green (INFRA -> skip falsifier)
- Tiering as relief: small diffs get fewer cycles, large diffs get more -- reduces corner-cutting pressure without weakening review

### Key Lessons

- Multi-model voting converges on mechanical text but diverges on semantic intent -- human review is the gate for strategic correctness
- .planning gitignore is necessary but not sufficient: worktree commits bypass gitignore scope, need explicit exclusion at merge time
- STATE.md is a derived artifact: it should be reconstructed from ROADMAP.md + git, not maintained independently
