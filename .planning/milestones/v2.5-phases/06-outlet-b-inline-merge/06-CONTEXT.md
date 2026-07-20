# Phase 6: Outlet B Inline Merge - Context

**Gathered:** 2026-06-01
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 6 merges the 3 standalone review pass skills + fp-verify + smoke-test into the aggregate code-forge SKILL.md so trusted strong models (e.g. terminal Opus) run the full review pipeline inline without sub-skill Invoke calls. This eliminates the Invoke hang bug (feedback_subagent_hangs.md). No subprocess, no auth, no CLI dispatch -- that is Phase 7.

**Requirements in scope:** INL-01, INL-02, INL-03, INL-04, INL-05, BOTH-01 + anti-ai-audit gate

</domain>

<decisions>
## Implementation Decisions

### File structure (INL-01, INL-05)

- **D-01:** Pass content stored as separate files under `~/.claude/skills/code-forge/passes/`: `pass1-qodo.md` (131 lines), `pass2-expert.md` (158 lines), `pass3-adversarial.md` (220 lines). Main SKILL.md uses `Load passes/xxx.md` directives to pull them in at runtime. This keeps the main file at ~900 lines (under 2K threshold from T1 measurement), avoids the 5 heading conflicts identified in T6, and matches the proven pattern from code-review-expert's `Load references/xxx.md` (T3 verified).
- **D-02:** Expert reference files (SOLID/security/code-quality/removal-plan, 365 lines total) move to `~/.claude/skills/code-forge/references/` as-is, loaded by Pass 2 via `Load references/xxx.md`. Same mechanism already proven in code-review-expert (T3 verified).
- **D-03:** Final directory structure:
  ```
  ~/.claude/skills/code-forge/
    SKILL.md (~900 lines, main pipeline + Load directives)
    passes/
      pass1-qodo.md
      pass2-expert.md
      pass3-adversarial.md
    references/
      solid-checklist.md
      security-checklist.md
      code-quality-checklist.md
      removal-plan.md
  ```

### Invoke elimination (INL-02, G6-3)

- **D-04:** ALL 5 sub-skill Invoke calls are eliminated: 3 review passes (SKILL.md:256/:267/:277) + fp-verify (:639) + smoke-test (:678). Each replaced with inline content loaded from the passes/ directory or inlined directly. This is Scenario B from T1 (1,457 lines total across all files, ~21K tokens).
- **D-05:** The replacement is `Load passes/xxx.md` (not a raw Invoke). The Load directive tells the AI assistant to Read the file and follow its instructions inline, without spawning a sub-skill session. This is the mechanism that eliminates the hang.

### Severity unification (INL-03)

- **D-06:** The severity normalization table at SKILL.md:300-310 already maps qodo R/Y/G + expert P0-P3 + adversarial Critical..Nit to unified P0-P3. Reuse as-is. Each pass file's instructions direct findings to use P0-P3 directly (not their native vocabulary). The table stays as a reference for edge cases.

### step N semantics (INL-04)

- **D-07:** `step N` continues to mean pipeline stage (Step 0/1-3/3.5/4), NOT individual pass within a cycle. There is NO per-pass selection mechanism.

### L0 behavior

- **D-08:** L0 deterministic tools run BEFORE outlet resolution. They are outlet-agnostic. Outlet B only affects Steps 1-3 (LLM pass execution method).

### Outlet branch point

- **D-09:** The outlet branch inserts in the Execution Protocol AFTER Step 0 completes and BEFORE Steps 1-3 begin (between current lines ~823-825). Phase 7 slots in by implementing the "cli" branch.

### BOTH-01 coverage instruction

- **D-10:** Each of the 3 pass files includes the instruction: "Systematically cover the whole diff risk surface -- do not focus on one area and neglect others."

### Hard constraints (carried forward)

- **D-11:** Do NOT modify the 3 standalone pass skills (qodo-review, code-review-expert, adversarial-qe). They remain the source of truth. Pass files under passes/ are COPIES, not symlinks.
- **D-12:** Outlet B needs no auth and spawns no subprocess. The Load mechanism is in-process file reading by the AI assistant.

### Anti-AI audit gate

- **D-13:** anti-ai-audit runs ONCE after 3x3 completes (all 3 consecutive clean cycles), before Step 3.5 (FP verification). It checks for AI-generated patterns: non-ASCII typography, AI套话, plan-ref labels, model/tool names in code/comments. Pipeline position: Step 0 -> Steps 1-3 (3x3) -> anti-ai-audit -> Step 3.5 -> Step 4 -> Commit gate.
- **D-14:** Anti-AI finding -> fix -> re-run anti-ai-audit only (does NOT reset the 3x3 cycle counter, because the fix is wording/formatting, not logic). Clean anti-AI -> proceed to Step 3.5.
- **D-15:** anti-ai-audit content is inlined into SKILL.md (or loaded from passes/) like the other passes. No Invoke call. Source: ~/.claude/skills/anti-ai-audit/SKILL.md.

### Claude's Discretion

- Exact wording of the Load directives
- How much of fp-verify and smoke-test content to inline directly vs load from files
- Whether to add a brief TOC/navigation section at the top of the merged SKILL.md

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Source skills (COPY FROM, do not modify)
- `~/.claude/skills/qodo-review/SKILL.md` -- Pass 1 source (135 lines)
- `~/.claude/skills/code-review-expert/SKILL.md` -- Pass 2 source (164 lines)
- `~/.claude/skills/code-review-expert/references/` -- 4 reference files (365 lines total)
- `~/.claude/skills/adversarial-qe/SKILL.md` -- Pass 3 source (224 lines)

### Aggregate skill (the file being modified)
- `~/.claude/skills/code-forge/SKILL.md` -- Current 852 lines. Invoke sites at :256/:267/:277/:639/:678

### Phase 5 integration
- `src/code_forge/outlet_resolver.py` -- resolve_outlet() returns "cli" or "inline"
- `.planning/phases/05-prerequisites/05-CONTEXT.md` -- D-29 outlet precedence, D-16 no model-capability auto-detect

### Project constraints
- `CLAUDE.md` -- Three-cycle review before commit; worktree required
- `.planning/REQUIREMENTS.md` -- INL-01 through INL-05, BOTH-01 full text
- `.planning/ROADMAP.md` -- Phase 6 success criteria SC#1-4

### Acceptance test data
- T1: Scenario B = 1,457 lines, ~21K tokens (acceptable)
- T3: Load references/ pattern proven in code-review-expert
- T6: 5 heading conflicts resolved by separate-files structure

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- Severity normalization table (SKILL.md:300-310): maps all 3 vocabularies to P0-P3. Reuse directly.
- State machine (SKILL.md:172-236): cycle counter + finding handling. Unchanged by this phase.
- Step 0 (SKILL.md:66-108): L0 deterministic checks. Outlet-agnostic, unchanged.
- FUSE-01 context fusion (SKILL.md:110-167): Step 0 findings injected into LLM passes. Unchanged.

### Established Patterns
- `Load references/xxx.md`: code-review-expert already uses this pattern. Proven mechanism.
- Skill directory structure: SKILL.md as entry point, subdirectories for supporting files.
- Each pass is sequential within a cycle, auto-continue on clean (TRUST-06).

### Integration Points
- SKILL.md Execution Protocol (lines 816-832): branch point insertion for outlet resolution
- SKILL.md Pass sections (lines 252-297): replaced by Load directives to passes/ files
- SKILL.md Step 3.5 (lines 637-673): fp-verify Invoke replaced with inline content
- SKILL.md Step 4 (lines 675-733): smoke-test Invoke replaced with inline content

</code_context>

<specifics>
## Specific Ideas

- The main SKILL.md Pass 1/2/3 sections become 2-3 line summaries with Load directives.
- Each pass file starts with the BOTH-01 instruction as its first non-heading line.
- The agents/agent.yaml from code-review-expert is NOT needed in the merge.

</specifics>

<deferred>
## Deferred Ideas

- Per-pass selection (pass 1/2/3 arguments) -- declined per D-07
- Outlet A CLI dispatch (Phase 7)
- Backend adapter implementations (Phase 7, BACKEND-01)
- Subprocess orphan protection (Phase 8, CLI-07)
- Cost transparency (Phase 8, CLI-08)
- Reviewer Canary spec (Phase 9, SPEC-01)

</deferred>

---

*Phase: 06-outlet-b-inline-merge*
*Context gathered: 2026-06-01*
*Acceptance tests: T1-T6 executed, all design decisions data-validated*
