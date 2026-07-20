# Research Summary: Forge v2.4 "Honest Green"

**Synthesized:** 2026-06-09
**Sources:** STACK.md, FEATURES.md, ARCHITECTURE.md, PITFALLS.md
**Overall confidence:** MEDIUM-HIGH

---

## Executive Summary

Forge v2.4 adds 6 review axes + eval scorecard to the existing 12K LOC pipeline.
**Zero new runtime dependencies** -- all work uses existing deps (pyyaml, unidiff),
Python stdlib (ast, subprocess), and two opt-in external binaries (semgrep, sem-cli)
detected at runtime via shutil.which.

Hard licensing constraint: inspect-core (FSL-1.1-ALv2) is off-limits (competing-use
violation); sem-cli (MIT/Apache-2.0) is the safe alternative.

## Key Findings

### Stack

- **Zero new pip deps.** All new functionality from existing deps + stdlib.
- **Semgrep CE:** Intraprocedural taint only. Cross-function flows need adversarial
  prompt fallback. LGPL-2.1 satisfied by subprocess invocation. Custom rules only --
  DO NOT vendor Semgrep-authored community rules (restrictive license).
- **sem-cli:** Entity extraction + blast-radius for 28 languages. MIT/Apache-2.0.
- **Eval harness:** Custom YAML manifest + git fixtures. No BugsInPy/Defects4J import.
- **Revert-RED:** unidiff (existing) + git apply -R. STING overfit guard via stdlib ast.

### Features

**Table stakes:** UNVERIFIED verdict, false-green-rate metric, semgrep taint, entity
extraction from diffs.

**Differentiators:** Revert-test-RED, input-provenance question, danger-score metadata,
file-age annotation, blast-radius ranking, per-backend eval comparison.

**Defer:** Testora intent at 55% precision (advisory-only still useful), CR-Bench
(too new), blame-based reviewer routing (no multi-user workflow).

### Architecture

- **7 new files, ~550-700 LOC.** 9 modified files, ~200 LOC delta.
- **All integration is additive:** new optional fields on StateFinding, new source
  literals, new factory builders. No schema version bump (D2 convention).
- **AxisRunner protocol:** advisory axes run once post-convergence (not per-cycle).
  Single architectural decision that caps code growth and cost growth.

### Top Pitfalls

| # | Pitfall | Prevention |
|---|---------|------------|
| 1 | Semgrep CE intraprocedural ceiling | CE direct paths + prompt cross-function + loud-fail |
| 2 | Revert-RED fragility | Fallback: apply -R -> checkout -> UNVERIFIED |
| 3 | Advisory creep to blocking | Type-level flag + disposition constraint + test invariant |
| 4 | Eval corpus 9 items = smoke not benchmark | Raw counts not percentages; 3-run averaging |
| 7 | Integration complexity explosion | AxisRunner protocol; advisory runs once |

## Roadmap Implications

Research suggests the current roadmap (Phases 17-22) is sound. Two adjustments to
consider during discuss-phase:

1. **Phase 17 should include AxisRunner protocol definition** alongside SEC-01/EVAL-01,
   since advisory type enforcement is foundational for all subsequent advisory axes.
2. **Advisory runs-once vs per-cycle** is a cost/quality tradeoff needing user sign-off
   during discuss-phase.

## Open Questions for Discuss-Phase

1. Advisory runs-once vs per-cycle (cost vs quality)
2. Eval "smoke" framing (9 pairs insufficient for statistical benchmark)
3. Semgrep CE boundary acceptance (cross-function prompt-only fallback ok?)
4. sem-cli maturity (v0.8.0, small user base, fallback plan?)
5. STING scope (Python-only MVP, Shell/Go demand unclear?)
6. AxisRunner protocol timing (must ship Phase 17 or machine.py bloats)

---
*Synthesized from 4 research outputs, 2026-06-09*
