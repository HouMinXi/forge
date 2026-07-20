# Phase 3: Adaptive Learning MVP - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md -- this log preserves the alternatives considered.

**Date:** 2026-05-13
**Phase:** 03-adaptive-learning-mvp
**Areas discussed:** Source Adapter Design, Gap Detection Strategy, PR Pipeline & Guardrails, Validation Strategy

---

## Source Adapter Data Sources

| Option | Description | Selected |
|--------|-------------|----------|
| git log + gh api + CI logs | Cover all main feedback channels | Yes |
| git log + gh api | No CI log parsing, medium complexity | |
| Only git log | Simplest but misses PR review detail | |

**User's choice:** git log + gh api + CI logs
**Notes:** User selected three-source coverage from the start.

## Source Adapter Parsing Depth

| Option | Description | Selected |
|--------|-------------|----------|
| Keyword matching only | Level 1, cheap, but misses implicit findings | |
| Dual-layer (keyword + LLM fallback) | Keywords first, LLM for unmatched | |
| Three-layer progressive | Keywords + structural heuristics + LLM | |
| Full LLM with context | All comments to LLM with diff hunk context | Yes |

**User's choice:** Full LLM with context
**Notes:** User raised the SSRF multi-step attack chain example where a reviewer describes "maintenance endpoint copies admin data to public path" with zero security keywords. This drove deeper analysis:

1. **Adversarial analysis** (gsd-assumptions-analyzer) confirmed keyword matching would miss 40-60% of actionable findings from external reviewers
2. **Paper research** (arXiv:2604.23667, EASE 2026) confirmed zero-shot LLM without context achieves only F1 0.36-0.37 on "evidence-sensitive labels"
3. **Cost analysis** showed full LLM at MVP volumes costs ~$4.5/month -- trivially acceptable
4. **Key distinction**: progressive escalation constrains gap detector (dimension matching), not source adapter (comment parsing). These are different problems.

## Glue Layer Architecture

| Option | Description | Selected |
|--------|-------------|----------|
| Canonical schema + adapter | SARIF-style per-source adapter, canonical finding schema | Yes |
| Need more research | Not enough confidence | |

**User's choice:** Canonical schema + adapter
**Notes:** Required deep cross-industry research. Final evidence spanned 6 industries:
- Security SAST: SARIF (OASIS standard)
- Security ASPM: DefectDojo (200+ scanner parsers)
- Threat Intel: STIX/TAXII (MITRE)
- Vulnerability: CWE/CVE normalization
- Observability: OpenTelemetry Collector (CNCF graduated)
- Healthcare: HL7 FHIR (FDA recognized)
- Enterprise Integration: Canonical Data Model (Hohpe 2003, 20+ years)

User initially wanted more research after SARIF/Aether/BitsAI evidence. Cross-industry deep dive across security, healthcare, observability, and enterprise integration convinced.

## Gap Detection Strategy

| Option | Description | Selected |
|--------|-------------|----------|
| Keyword dictionary + exclusion | Each dimension has keyword dict, unmatched = gap | Yes |
| LLM classification | LLM assigns dimension, "unknown" = gap | |
| Two-stage (keyword + TF-IDF) | Between Level 1 and Level 2 | |

**User's choice:** Keyword dictionary + exclusion
**Notes:** User asked for paper validation. Research found:
- HYDRA-REVIEWER (IEEE TSE 2025): taxonomy-based agent routing
- Linux checkpatch.pl: 700+ regex rules, 18 years incremental evolution
- SonarQube: keyword-based 4-category classification
- OOD detection literature (arXiv:2507.21160v1): rule-based is valid Level 1 baseline

## Gap Trigger Threshold

| Option | Description | Selected |
|--------|-------------|----------|
| 3 similar gaps | Trigger after 3 occurrences | Yes |
| 1 gap immediately | Every gap triggers proposal | |
| 5 gaps | More conservative | |

**User's choice:** 3 similar gaps
**Notes:** Aligns with Sashiko (3 dimensions from repeated findings) and checkpatch.pl (multi-patch observation before rule addition).

## PR Content

| Option | Description | Selected |
|--------|-------------|----------|
| SKILL.md + evidence + seed test | Most complete, full audit trail | Yes |
| SKILL.md diff only | Simple but no traceability | |
| SKILL.md + evidence | No seed test | |

**User's choice:** SKILL.md + evidence + seed test (most complete)

## Dimension Budget Cap

| Option | Description | Selected |
|--------|-------------|----------|
| 20 dimensions | Current 14 + 6 slots | Yes |
| 15 dimensions | Aggressive constraint | |
| 25 dimensions | Loose | |

**User's choice:** 20 dimensions

## Staleness Decay

| Option | Description | Selected |
|--------|-------------|----------|
| Mark stale + suggest archive | 90 days, no auto-removal | Yes |
| Auto-archive | Violates "never auto-upgrade" | |
| No decay | May accumulate junk | |

**User's choice:** Mark stale + suggest archive

## Validation Strategy

| Option | Description | Selected |
|--------|-------------|----------|
| Multi-source synthetic test suite | Per-adapter samples with gap + known-dim findings | Yes |
| Sashiko replay only | Single event, can't prove generality | |
| Real data driven | Depends on data availability | |

**User's choice:** Multi-source synthetic test suite
**Notes:** User challenged the Sashiko-centric validation approach: "What if someone uses Qodo review? Or another review tool?" This reframed validation from event-specific replay to source-agnostic gap detection capability. DefectDojo's approach (sample data per parser) was the convincing evidence.

## Claude's Discretion

- Internal data structures for gap accumulation
- LLM prompt template for comment parsing
- Specific keyword lists per dimension
- CI log adapter parsing heuristics

## Deferred Ideas

- LEARN-03/04/05 remain deferred to v2 with explicit escalation triggers
- Cross-project dimension transfer (ADV-02)
- LLM-based gap detection (Level 2 upgrade when keyword miss rate >20%)
