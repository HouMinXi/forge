# Phase 1b: Trust Calibration - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md -- this log preserves the alternatives considered.

**Date:** 2026-05-12
**Phase:** 1b-trust-calibration
**Areas discussed:** Confidence scoring, Tiered review depth, Threshold tuning, Data sufficiency, Dimension evaluation framework

---

## Confidence Scoring (TRUST-02)

| Option | Description | Selected |
|--------|-------------|----------|
| Dimension-level FP rate | confidence = 1 - dimension_fp_rate. Simple but all findings in same dimension get same score | |
| Multi-signal schema + progressive calculation | Schema records all signals from day one, formula upgrades in stages | First selection |
| Direct multi-signal composite | Use composite formula immediately with initial weights | |
| Fixed weights, day one | Remove progressive complexity, use fixed formula | User reconsidered but kept progressive |

**User's choice:** Multi-signal schema + progressive calculation
**Notes:** User challenged the initial recommendation of dimension-level FP rate: "with more data over time, why would dimension-level be the best?" This led to redesigning D1 to capture all signals from day one. User later questioned whether progressive calculation is necessary (fixed weights for common-sense signals like pass agreement), but decided to keep progressive for LLM self-report calibration needs.

---

## Tiered Review Depth (TRUST-03)

| Option | Description | Selected |
|--------|-------------|----------|
| Composite scoring + upward override | 4 signals weighted, --full escalates, can't downgrade security | Initial selection |
| Conservative two-tier | Only step0-only (allowlist) and full, no light tier | |
| User manual selection | User picks tier every time | |

**User's choice:** Composite scoring + upward override + deterministic classification + 10% audit
**Notes:** User raised critical concern: "How do I ensure AI doesn't slack off?" This led to adding three anti-gaming layers: (1) Python does classification, not LLM, (2) LLM doesn't know tiers exist, (3) 10% random audit sampling on light reviews.

---

## Threshold Tuning (LEARN-07-FULL)

| Option | Description | Selected |
|--------|-------------|----------|
| FP data drives rule improvement | Fix SKILL.md prompts, not numerical thresholds | After research |
| Recommend-then-apply thresholds | Compute suppression thresholds, user approves | Initial recommendation |
| Graduated response | 50/70/90% FP triggers different actions | |
| Manual threshold setting | User adjusts thresholds in config | |

**User's choice:** FP data drives rule improvement
**Notes:** User requested tavily research before deciding. Research found: SonarQube (15 years, fix rules not thresholds), Semgrep Memories (pattern-based, not threshold), Google Tricorder (human team decides, not auto-suppress), FSE 2025 (50.8% of suppressions are useless). This evidence shifted recommendation from "recommend-then-apply thresholds" to "fix rules, not thresholds."

---

## Data Sufficiency

| Option | Description | Selected |
|--------|-------------|----------|
| Tricorder mode (deploy first, watch data) | All dimensions active immediately, 10% FP as exit threshold | After research |
| Progressive three-tier | Exploratory/provisional/confirmed based on data volume | Initial recommendation |
| Fixed threshold (wait for data) | No calibration until 30 points per dimension | |

**User's choice:** Tricorder mode (deploy first, watch data)
**Notes:** User requested tavily research. Key findings: Google Tricorder deploys checks first then monitors (no "wait for data"), Apple BayesCNS uses Bayesian priors for cold-start (+10.60% improvement), Chromium uses provisional approval with 1-month monitoring. Evidence shifted from progressive tiers to immediate deployment.

---

## Dimension Evaluation Framework

| Option | Description | Selected |
|--------|-------------|----------|
| Tricorder 4 criteria + 10% threshold | Understandable, Actionable, <10% FP, Significant Impact | |
| Partial adoption | Only 10% FP threshold, other 3 as reference | |
| Keep 50% threshold | Original ROADMAP threshold | |

**User's choice:** Tricorder 4 criteria + 10% FP threshold
**Notes:** User noticed the Tricorder quality criteria from the data sufficiency research and asked "why aren't we considering this?" This was a user-driven addition, not in the original gray areas. Updated ROADMAP threshold from 50% to 10%.

---

## Claude's Discretion

- Finding schema extension details
- CLI flag naming
- Audit implementation details
- Report output format
- Wilson score CI implementation

## Deferred Ideas

- Outdated rate tracking (Phase 2+)
- Semgrep Memories-style pattern matching (Phase 3)
- Cross-project dimension transfer (v2)
