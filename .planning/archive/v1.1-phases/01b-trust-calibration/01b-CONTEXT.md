# Phase 1b: Trust Calibration - Context

**Gathered:** 2026-05-12
**Status:** Ready for planning

<domain>
## Phase Boundary

Calibrate forge's review pipeline using real FP/TP data. Evaluate existing 12 dimensions for effectiveness, add confidence scoring to findings, enable tiered review depth to reduce cost on low-risk changes, and establish a data-driven rule improvement loop. This phase transforms forge from "run everything the same way" to "invest review effort where it matters."

</domain>

<decisions>
## Implementation Decisions

### D1: Confidence Scoring -- Multi-Signal Schema + Progressive Calculation

**Decision:** Record all confidence signals from day one in findings.json schema. Calculation formula upgrades in stages as data accumulates.

**Schema fields per finding (new):**
```json
{
  "confidence": 0.72,
  "confidence_signals": {
    "dimension_fp_rate": 0.15,
    "pass_agreement": 1.0,
    "evidence_count": 3,
    "llm_self_report": 0.8
  }
}
```

**Progressive calculation stages:**

| Stage | Data Volume | Formula |
|-------|-------------|---------|
| Phase 1b launch | <100 findings | confidence = 1 - dimension_fp_rate (only reliable signal) |
| Accumulation | 100-300 findings | Add pass_agreement weight (multi-pass consensus) |
| Mature | 300+ findings | Full composite with all 4 signals, weights from data regression |

**Low-confidence finding handling:** Show with confidence label (`[confidence: 0.4]`), do not suppress. Suppression is a separate decision gated by D3 (rule improvement, not threshold tuning).

**Rationale:** Schema captures all signals from day one (no migration needed). Progressive calculation avoids false precision from uncalibrated weights on sparse data. Industry precedent: SonarQube provides confidence ratings per finding; Semgrep uses high/medium/low tiers; BitsAI-CR uses two-stage RuleChecker+ReviewFilter.

### D2: Tiered Review Depth -- Composite Scoring + Upward Override + Deterministic Classification + Audit

**Decision:** Auto-classify changes into full/light/step0-only using composite scoring. Classification is deterministic (Python code in forge_cli.py), never delegated to LLM. User override only escalates (`--full`), never downgrades security-critical files.

**Classification signals:**

| Signal | Weight | Example |
|--------|--------|---------|
| File criticality | High | auth/security paths -> always full |
| Change type | High | comment-only/whitespace -> step0-only |
| Diff size | Medium | <10 lines -> bias toward light |
| AI-generated flag | High | AI code -> minimum light (29-45% contain vulns) |

**Tier definitions:**

| Tier | Scope | Cost |
|------|-------|------|
| Full | All 5 steps, 3 cycles (current default) | $$$ |
| Light | Step 0 + 1 cycle of Steps 1-3, no Step 4 | $ |
| Step0-only | Deterministic checks only, zero LLM cost | Free |

**Anti-gaming design (critical):**
1. **Classification authority stays in Python** -- tier is determined before LLM is invoked. LLM receives "execute full review" or "execute light review", never knows other tiers exist.
2. **LLM has no tier awareness** -- prompt says what to do, not what options exist.
3. **10% audit sampling** -- randomly run full review on light-classified changes, compare findings. Light tier missing P0/P1 findings -> adjust classification weights.

**Override rules:**
- `--full` always accepted (escalate)
- `--step0` rejected for files matching criticality patterns (cannot downgrade security)
- Default bias: conservative (toward full) until audit data validates lighter tiers

**Rationale:** Chromium uses CQ Dry Run vs Full CQ vs Mega CQ with user selection. Semgrep maps severity to action (Monitor/Comment/Block). Initial weights bias toward full to prevent false economy.

### D3: Threshold Tuning -- FP Data Drives Rule Improvement

**Decision:** When FP rate is high, fix the rules (SKILL.md prompts/dimension definitions), not set numerical suppression thresholds. Generate improvement suggestions as PRs for user approval.

**Flow:**
```
FP rate > 10% detected (from findings.json)
  -> Analyze reject_reason breakdown:
     tool-wrong (categories 1-4): HALLUCINATION, CONTEXT_MISSING, INTENTIONAL, NOT_APPLICABLE
     user-won't-act (categories 5-6): STYLE_PREFERENCE, ACCEPTABLE_RISK
  -> If tool-wrong dominant: suggest SKILL.md prompt modification to improve detection accuracy
  -> If user-won't-act dominant: suggest severity downgrade or dimension scope narrowing
  -> Generate recommendation with evidence
  -> User approves/rejects
```

**Separate tracking (mandatory):**
- **ToolFP rate** = (HALLUCINATION + CONTEXT_MISSING + INTENTIONAL + NOT_APPLICABLE) / total decided
- **UserFP rate** = (STYLE_PREFERENCE + ACCEPTABLE_RISK) / total decided
- Dashboard shows both rates per dimension (already partially implemented in show_stats)

**Why not numerical thresholds:** SonarQube's 15 years of experience: fix rules, not thresholds. FSE 2025 empirical study (46 Python projects): 50.8% of static analysis suppressions are useless, suppressions accumulate over time and are never cleaned up. Semgrep Memories: pattern-based learning from user FP marks, not threshold tuning.

**Minimum data for recommendations:** 20 observations per dimension (rule of np > 5 for Wilson score intervals at typical FP rates).

### D4: Data Sufficiency -- Tricorder Mode (Deploy First, Watch Data)

**Decision:** All dimensions immediately active. 10% FP rate as exit threshold (not entry threshold). Data-insufficient dimensions marked "provisional" rather than suppressed.

**Operational model:**
```
Day 1: All 12 dimensions active, all marked "provisional"
       Collect accept/reject data on every finding
Day N: Dimension reaches 20+ data points -> remove "provisional" label
       FP rate computed with Wilson score confidence intervals
       FP > 10% -> trigger D3 rule improvement flow
       FP <= 10% -> dimension confirmed effective
```

**Bootstrap data treatment:** ~53 historical data points serve as initial signal. Not weighted differently from structured data (extraction quality was manually verified during Phase 1a). Provides global prior FP rate (~28%) while per-dimension estimates remain wide.

**No waiting:** Unlike the original ROADMAP's "30-day data collection" prerequisite, Phase 1b ships immediately. The 30-day window is for data accumulation in production, not a blocker for development.

**Rationale:** Google Tricorder deploys checks immediately with 4 quality criteria, monitors Not Useful rate, fixes or disables checks that exceed threshold. Apple BayesCNS (AAAI): Bayesian cold-start in production search, +10.60% new item interactions. Chromium clang-tidy: provisional approval with 1-month monitoring.

### D5: Dimension Evaluation Framework -- Tricorder 4 Criteria

**Decision:** Evaluate all 12 existing review dimensions using Tricorder's 4 quality criteria. Replaces ROADMAP's original "50% effectiveness" threshold with the industry-standard 10% FP threshold.

**Evaluation criteria:**

| Criterion | How to Assess | Failure Action |
|-----------|---------------|----------------|
| **Understandable** | Can a developer understand the finding in <30 seconds? | Rewrite finding format in SKILL.md prompt |
| **Actionable** | Does the finding include fix guidance? | Add fix suggestions to prompt template |
| **<10% FP rate** | Computed from findings.json (ToolFP rate, not UserFP) | Trigger D3 rule improvement flow |
| **Significant Impact** | Do accepted findings lead to meaningful code changes? | Downgrade severity, merge with another dimension, or retire |

**Process:**
1. Compute per-dimension metrics from findings.json (automated)
2. Generate evaluation report (automated)
3. For dimensions failing any criterion: generate specific improvement recommendation
4. User reviews and approves changes via PR

**ROADMAP update required:** Change success criterion from "dimensions below 50% effectiveness are flagged" to "dimensions exceeding 10% ToolFP rate trigger rule improvement."

**Rationale:** Google Tricorder (ICSE 2015): these 4 criteria maintained developer trust at scale (3000 Please Fix clicks/day, only 250 Not Useful clicks/day). The 10% threshold is industry standard -- CodeAnt research shows >15% is "unacceptable for production use."

### Claude's Discretion

- Finding persistence schema extensions (exact field names, validation rules)
- CLI flag naming (`--full`, `--light`, `--step0` vs alternatives)
- Audit sampling implementation details (random seed, logging format)
- Evaluation report output format (terminal table vs markdown vs JSON)
- Wilson score confidence interval implementation (scipy vs manual formula)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase 1a Artifacts (foundation)
- `.planning/phases/01a-trust-instrumentation/01a-CONTEXT.md` -- D1-D10 decisions that Phase 1b builds on
- `skills/forge/SKILL.md` -- Current state machine, severity logic, finding persistence schema
- `cli/forge_cli.py` -- Existing CLI with show_stats (partial FP dashboard), classify_findings, run_dry_run
- `hooks/check_review_tracker.sh` -- Hook with severity detection, session state tracking
- `cli/config.json` -- Model pricing config

### Research (Phase 1b specific)
- `.planning/research/P3-THRESHOLD-RESEARCH.md` -- P3 density-based escalation research (applies to tier classification)
- `.planning/research/PORTABILITY-RESEARCH.md` -- Multi-platform considerations for CLI changes

### External References (from research)
- Google Tricorder (ICSE 2015): `research.google.com/pubs/archive/43322.pdf` -- 4 quality criteria, Not Useful rate monitoring
- BitsAI-CR (FSE 2025): `arxiv.org/abs/2501.15134` -- Two-stage RuleChecker+ReviewFilter, outdated rate
- SonarQube FP strategy: `sonarsource.com/blog/how-sonarqube-minimizes-false-positives` -- 3.2% FP at 137M issues, rule improvement over threshold tuning
- Semgrep Memories: `semgrep.dev/blog/2025/making-zero-false-positive-sast-a-reality-with-ai-powered-memory` -- Pattern-based FP learning
- FSE 2025 Suppression Study: `software-lab.org/publications/fse2025_suppressions.pdf` -- 50.8% useless suppressions, accumulation problem
- Apple BayesCNS (AAAI): `machinelearning.apple.com/research/unified-bayesian` -- Bayesian cold-start in production

### Requirements
- `.planning/REQUIREMENTS.md` -- TRUST-02, TRUST-03, LEARN-07-FULL definitions

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `cli/forge_cli.py:show_stats()` -- Already computes per-dimension FP rates with ToolFP/UserFP split. Extend for D5 evaluation report.
- `cli/forge_cli.py:classify_findings()` -- Interactive accept/reject with 6-category taxonomy. Data source for all calibration.
- `cli/forge_cli.py:run_dry_run()` -- Step 0 runner with file-type detection. Extend for tier classification (D2).
- `cli/forge_cli.py:atomic_write()` -- Safe JSON persistence pattern. Reuse for new config files.
- `hooks/check_review_tracker.sh:_max_severity()` -- Severity classification from review output. Input signal for tier determination.

### Established Patterns
- **Atomic JSON write**: tempfile.mkstemp + os.replace (used in CLI, hook, bootstrap)
- **Finding schema**: UUID id, ISO timestamp, dimension, severity, outcome, reject_reason (extend, don't replace)
- **Sidecar pattern**: run metadata in .forge/runs/*.json separate from findings.json
- **Config pattern**: cli/config.json for model pricing (extend for tier weights, evaluation thresholds)

### Integration Points
- `skills/forge/SKILL.md` -- Confidence score must be computed and stored during finding persistence (Section: Finding Persistence Protocol)
- `cli/forge_cli.py:run_forge()` -- Tier classification happens here, before claude invocation
- `cli/forge_cli.py:show_stats()` -- Extend to show evaluation report (D5) and confidence distribution
- `.forge/findings.json` -- Schema extension for confidence_signals field

</code_context>

<specifics>
## Specific Ideas

### From Discussion
- User specifically validated that dimension-level FP rate alone is NOT sufficient for confidence scoring -- individual finding quality matters (evidence count, pass agreement)
- User insisted on anti-gaming design for tiered review: AI must not choose its own workload
- User challenged the "progressive calculation" necessity -- accepted it after considering LLM self-report calibration needs
- User drove adoption of Tricorder's 4 quality criteria over the original 50% effectiveness threshold
- User preferred "fix rules, not thresholds" approach based on SonarQube/Semgrep evidence

### Research-Backed Decisions
- All 5 decisions validated by tavily search: Tricorder (Google), BitsAI-CR (Tencent), SonarQube (Sonar), Semgrep, Apple BayesCNS, Chromium clang-tidy, FSE 2025 suppression study

</specifics>

<deferred>
## Deferred Ideas

- **Outdated rate tracking** (D10 from Phase 1a): passive signal from git history correlation. Deferred to Phase 2+ if explicit feedback volume is insufficient.
- **Semgrep Memories-style pattern matching**: learn from FP marks to auto-skip similar future FPs. More complex than rule improvement, consider for Phase 3.
- **Cross-project dimension transfer**: share calibration data across forge installations. Out of scope (v2 ADV-02).

</deferred>

---

*Phase: 01b-trust-calibration*
*Context gathered: 2026-05-12*
