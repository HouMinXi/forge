# Phase 1a Context: Trust Instrumentation

**Phase:** 1a
**Created:** 2026-05-12
**Source:** discuss-phase session + 4 deep research studies (FP-TAXONOMY-DEEP, ADAPTIVE-SYSTEMS-DEEP, COMMERCIAL-CASES-DEEP, GAP-ANALYSIS-DEEP)

## Decisions

### D1: FP Tracking Storage

**Decision:** JSON file at `.forge/findings.json`, permanent retention, no TTL.

**Rationale:** Phase 1b needs 30+ days of accumulated data for calibration. Deleting old data defeats the purpose. JSON is human-readable and git-friendly. SQLite rejected (overkill for single-user CLI tool at current scale).

**Schema (minimum viable):**
```json
{
  "findings": [
    {
      "id": "uuid",
      "timestamp": "ISO-8601",
      "file": "path/to/file",
      "line": 42,
      "dimension": "security",
      "pass": 2,
      "cycle": 1,
      "severity": "P2",
      "description": "finding text",
      "outcome": "accepted|rejected|pending",
      "reject_reason": "HALLUCINATION|CONTEXT_MISSING|INTENTIONAL|NOT_APPLICABLE|STYLE_PREFERENCE|ACCEPTABLE_RISK",
      "commit_sha": "abc123",
      "cost_tokens": { "input": 1200, "output": 450 }
    }
  ]
}
```

### D2: FP Taxonomy -- 6 Categories

**Decision:** Reduce from 13 categories to 6. Cognitive load theory (Miller/Cowan) and commercial practice (no tool uses >6) support this.

**Categories:**
1. **HALLUCINATION** -- LLM invents a non-existent problem (pattern similarity, self-correction blind spots included here)
2. **CONTEXT_MISSING** -- Business scenario, call contract, or cross-file context unknown to reviewer (subsumes: cross_function, cascaded_constraints, long_context_overflow, sanitization_invisible)
3. **INTENTIONAL** -- Intentional hardcoding, test code, config, or known tradeoff
4. **NOT_APPLICABLE** -- Language feature misparse, stale rule, unreachable boundary (the tool is technically wrong)
5. **STYLE_PREFERENCE** -- Subjective preference, not a defect (user disagrees on approach, naming, structure)
6. **ACCEPTABLE_RISK** -- Real issue, but user accepts the risk (won't fix, documented tradeoff)

**Key insight:** Categories 1-4 = "tool wrong" (improve the tool). Categories 5-6 = "tool right, user won't act" (don't count as FP for tool quality metrics).

**Source:** FP-TAXONOMY-DEEP.md synthesis of BitsAI-CR, QASecClaw, Tencent FP Study.

### D3: Severity-Gated Cycle Reset (TRUST-07)

**Decision:** Replace current "any finding resets everything" with severity-gated reset.

| Severity | Action | Rationale |
|----------|--------|-----------|
| P0/P1 (Critical/High) | Full reset (cycle_counter = 0) | Serious issues invalidate prior clean passes |
| P2 (Medium) | Current cycle restart | Issue is real but doesn't invalidate other cycles |
| P3 (Low/Style) | Accumulate, no interrupt | Style nits should not waste 9+ passes |

**Source:** GAP-ANALYSIS-DEEP.md identified this as "single highest-risk design flaw". Could cut wasted passes by 60%+.

### D4: Auto-Continue on Clean Pass (TRUST-06)

**Decision:** When a pass reports zero findings, forge automatically proceeds to the next pass/cycle. Only pause when findings exist and require user decision (accept/reject/fix).

**Source:** Real-world UX pain observed in session (user typed "continue" after every LGTM pass). Zero-finding passes should be silent transitions.

### D5: Feedback Collection in Phase 1a (LEARN-07-LITE)

**Decision:** Binary accept/reject tracking per finding, with 6-category reject_reason taxonomy (D2 above). Moved from Phase 3 to Phase 1a.

**Rationale:** Circular dependency -- Phase 1b needs 30 days of feedback data, so data collection must start in Phase 1a. ALL successful commercial systems (BitsAI-CR, Tricorder, Copilot) started with the feedback loop before any calibration.

**What is NOT included (deferred to v2):**
- Threshold tuning via RLHF (LEARN-07-FULL)
- Outdated rate tracking (passive signal from git history)
- Automatic FP rate-based suppression

### D6: Step 0 -> LLM Context Fusion (FUSE-01)

**Decision:** Step 0 (deterministic) findings are passed as context to LLM passes (Steps 1-3). LLM passes receive a summary of what Step 0 already found, preventing redundant flagging.

**Implementation:** After Step 0 completes, serialize its findings into a context block appended to LLM prompts for Steps 1-3. Format: structured list of already-detected issues with file/line/type.

**Source:** Semgrep Multimodal achieved 8x more true positives and 50% less noise with deterministic+LLM fusion. COMMERCIAL-CASES-DEEP.md.

### D7: CLI Design

**Decision:** Python wrapper script that invokes forge as a Claude Code skill externally. NOT a standalone reimplementation.

**Rationale:** Forge's value is in the LLM passes (Steps 1-3), which require Claude Code. A standalone CLI that only runs Step 0 has minimal value. The wrapper should:
- Accept `forge <git-diff-spec>` syntax
- Invoke `claude --skill forge` with the diff as context
- Output structured results (JSON or human-readable)
- Support `--dry-run` (Step 0 only, no LLM cost)

**Deferred to v2:** CI/CD integration mode (CLI-02), GitHub Actions workflow.

### D8: Cost Metering

**Decision:** Track token count (input + output) per pass and estimated cost per pipeline run.

**Implementation:**
- Each pass logs `{ input_tokens, output_tokens }` to findings.json metadata
- Estimated cost = `input_tokens * model_input_price + output_tokens * model_output_price`
- Model pricing stored as config (updated manually when prices change)
- Phase 1a dashboard shows cost per run, average cost per tier

**Rationale:** 9-pass pipeline cost must be known before scaling. Users need to understand the economics.

### D9: Per-Dimension Escalation (from Cynefin)

**Decision:** Not every dimension needs every review level. Escalation should be per-dimension, not system-wide.

| Domain (Cynefin) | Example Dimensions | Max Level |
|-------------------|-------------------|-----------|
| Clear (known rules) | Style, formatting, lint | Level 1 forever |
| Complicated (expert analysis) | Logic errors, security | Level 2 |
| Complex (emergent patterns) | Cross-file dependencies, architecture | Level 3 |

**Impact on Phase 1a:** This is a design principle, not a Phase 1a deliverable. Phase 1a records per-dimension data; Phase 1b uses it to determine which dimensions need which level.

**Source:** ADAPTIVE-SYSTEMS-DEEP.md, Cynefin framework mapping.

### D10: Outdated Rate as Passive Metric

**Decision:** Track "did developer modify flagged code in subsequent commits?" as a passive signal alongside explicit accept/reject.

**Implementation for Phase 1a:** Deferred. Phase 1a collects explicit feedback only. Outdated rate requires git history correlation which adds complexity. Will add in Phase 1b or 2 if explicit feedback volume is insufficient.

**Source:** BitsAI-CR (12K WAU at ByteDance) uses outdated rate as primary adoption metric. More objective than explicit accept/reject.

## YAGNI List (validated by deep research)

Things we confirmed should NOT be built in Phase 1a:

| Item | Reason | Source |
|------|--------|--------|
| Embedding-based dedup | grep sufficient at current scale (12-15 dimensions) | Direct Corpus Interaction paper |
| AST parsing for SKILL.md | Line-based insertion works for v1 edits | BitsAI-CR uses taxonomy, not AST |
| Parallel passes within a cycle | Convergence problem (Pass 3 can't catch Pass 1-2 fix regressions) | User correction during discussion |
| 13-category FP taxonomy | Cognitive overload; reduce to 6 | FP-TAXONOMY-DEEP.md |
| OODA framework | PDCA fits monthly feedback cycles better | DeepSeek + Kimi consensus |
| Automatic dimension retirement | Needs 30+ days data first | YAGNI principle validated by Semgrep pattern |

## Requirements Mapped to This Phase

| Requirement | Description | Decision Reference |
|-------------|-------------|-------------------|
| TRUST-01 | FP tracking | D1 |
| TRUST-05 | FP rate dashboard | D1, D2 |
| TRUST-06 | Auto-continue on clean pass | D4 |
| TRUST-07 | Severity-gated cycle reset | D3 |
| LEARN-07-LITE | Binary feedback collection | D5 |
| FUSE-01 | Step 0 -> LLM context fusion | D6 |
| CLI-01 | Standalone CLI wrapper | D7 |

## Open Questions for Planning

1. **Dashboard format**: Terminal table? Markdown report? JSON dump? (Planner decides based on implementation complexity)
2. **Finding ID generation**: UUID v4? Sequential? Hash-based? (Planner decides)
3. **Step 0 finding serialization format for FUSE-01**: How exactly to inject deterministic findings into LLM prompts? (Researcher/planner scope)

## Commercial Maturity Reference

Forge is currently between Stage 1 (Simple Rules) and Stage 2 (Workflow Integration).
Phase 1a targets Stage 2 completion + Stage 3 foundation (feedback loop infrastructure).

```
Stage 1: Simple Rules (checkpatch.pl, basic lint)          [DONE - Step 0]
Stage 2: Workflow Integration (push into code review)      [DONE - forge skill]
Stage 3: Feedback Loops (Not Useful button, outdated rate)  [Phase 1a TARGET]
Stage 4: Adaptive Mechanisms (rule retirement, precision)   [Phase 1b-2]
Stage 5: Hybrid Intelligence (deterministic + LLM fusion)   [Phase 1a partial - FUSE-01]
```
