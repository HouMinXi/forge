# Phase 3: Adaptive Learning MVP - Cross-AI Reviews (R1)

**Date:** 2026-05-13
**Reviewers:** DeepSeek v4-pro, Kimi K2.6, Mimo
**Artifact reviewed:** 03-CONTEXT.md (decisions D1-D9)
**Round:** 1

## Cross-Model Consensus

### BLOCKER: D2/D4 Contradiction (all 3 models)

**Mimo (B1)**: LLM vs keyword authority undefined. When LLM says "security" but keywords say "unknown", which wins?
**Kimi (BLOCKER)**: D2 correctly diagnoses keyword failure for implicit findings, then D4 applies keywords anyway. SSRF example would be misclassified as a NEW dimension gap instead of mapping to existing "security".
**DeepSeek (H1)**: Three possible interpretations of who classifies dimensions. Implementer won't know which code path to write.

**Consensus fix**: Clarify data flow explicitly. LLM proposes dimension (advisory), keyword dictionary validates. If LLM says known-dim but keywords miss it, that's a keyword dictionary gap (expand dict), not a new dimension.

### HIGH: D5 "Similar Gaps" Undefined (all 3 models)

**Mimo (H2)**: "Similarity" for unknown findings is undefined -- embeddings are deferred to v2.
**Kimi (HIGH)**: Unimplementable at Level 1. Gaps are in the "unknown" bucket by definition; grouping them requires exactly the capability (LEARN-03) that's deferred.
**DeepSeek (M4)**: No operational definition. Implementer has nothing to code against.

**Consensus fix**: Either (a) use LLM-based grouping ("are these two gaps about the same issue?") -- consistent with D2's full-LLM approach, or (b) replace auto-threshold with human review of gap candidates.

### HIGH: Schema Incompatibility (Mimo + Kimi)

**Mimo (H1)**: Phase 3 canonical schema has different fields than Phase 1a findings.json. Existing CLI functions will break.
**Kimi (MEDIUM)**: Shares only file/line/dimension. No migration plan.

**Consensus fix**: Either separate file (.forge/external_findings.json) or extend Phase 1a schema with source_origin flag.

### HIGH: Cross-Source Deduplication Missing (Mimo + Kimi)

**Mimo (H3)**: Same issue from 3 sources would falsely trigger D5 threshold.
**Kimi (G2)**: No dedup key in canonical schema. Multiple forge --learn runs on same PR ingest duplicates.

**Consensus fix**: Add source_id to schema, dedup by (file, line, dimension_cluster) within rolling window.

---

## Deduplicated Findings

| ID | Severity | Decision | Finding | Models |
|----|----------|----------|---------|--------|
| R1 | BLOCKER | D2+D4 | LLM vs keyword classification authority undefined -- implementer doesn't know which code path to write. SSRF example would be misclassified as new gap instead of existing security. | All 3 |
| R2 | HIGH | D5 | "3 similar gaps" unimplementable at Level 1 -- similarity grouping requires embeddings (deferred) or LLM (not decided) | All 3 |
| R3 | HIGH | D3 | Canonical schema incompatible with Phase 1a findings.json -- different fields, no migration plan | Mimo, Kimi |
| R4 | HIGH | D5 | Cross-source dedup missing -- same issue from 3 sources = 3 gaps = false trigger | Mimo, Kimi |
| R5 | HIGH | D4 | Evidence misattributed -- HYDRA-REVIEWER uses LLM not keywords, SonarQube pre-tags rules, checkpatch has no gap detection | Kimi, DeepSeek |
| R6 | HIGH | General | PDCA Check/Act missing -- new dimensions enter pipeline with no FP monitoring or effectiveness check | DeepSeek |
| R7 | HIGH | D1 | git log is author signal not reviewer feedback; gh api volume near-zero for solo user | Kimi |
| R8 | HIGH | General | No storage decision for external findings (separate file vs extend findings.json) | Kimi |
| R9 | MEDIUM | D7 | Cap + merge requirement = deadlock at 20 -- new dimensions lack co-location data for merge | DeepSeek |
| R10 | MEDIUM | D1 | CI log adapter under-specified -- no worked example, access mechanism missing | DeepSeek, Mimo |
| R11 | MEDIUM | General | No feedback loop from new dimension PR merge back to gap detector keyword dictionary | DeepSeek |
| R12 | MEDIUM | D8 | Conflicts with Phase 2 D1 zero-data seed test -- healthy zero-finding dimensions wrongly marked stale | Kimi |
| R13 | MEDIUM | D6 | PR target undefined for local CLI tool -- "opening PR against own local file is workflow theater" | Kimi |
| R14 | MEDIUM | D9 | Missing false-gap negative test -- validation doesn't test "implicit finding correctly maps to existing dim" | Kimi |
| R15 | MEDIUM | D3 | Cross-industry evidence overstated -- FHIR/STIX are distant analogies, not direct validation | Mimo |
| R16 | MEDIUM | D8 | Staleness decay trigger underspecified -- counter reset behavior, shadow findings | Mimo |
| R17 | MEDIUM | General | Progressive escalation boundary inconsistently applied -- D2 uses LLM but claims Level 1 | Kimi |
| R18 | MEDIUM | D6 | SKILL.md line-level diffs are knowingly fragile with no mitigation | Mimo |
| R19 | LOW | D2 | Cost model assumes unnamed model pricing | Mimo, DeepSeek |
| R20 | LOW | D9 | 1 sample/adapter insufficient -- need valid/malformed/edge per adapter | DeepSeek, Mimo |
| R21 | LOW | D6 | evidence/ directory path undefined | DeepSeek |
| R22 | LOW | General | No rollback/migration plan for dimension removal | Mimo |

## Model Profiles (This Round)

| Model | Findings | BLOCKER | HIGH | Unique Insight |
|-------|----------|---------|------|----------------|
| DeepSeek | 11 | 0 | 3 | PDCA Check/Act missing (R6); keyword dictionary update loop (R11); cap deadlock (R9) |
| Kimi | 12 | 1 | 3 | git log is wrong signal type (R7); D5 requires exactly LEARN-03 (deferred); PR target undefined for local tool (R13) |
| Mimo | 11 | 1 | 3 | Cross-source dedup (R4); evidence overstated (R15); SKILL.md fragility mitigation (R18) |

## Recommended Action

**Must fix before planning (BLOCKER + consensus HIGH):**

1. **R1**: Rewrite D4 -- specify data flow: LLM proposes dimension (advisory) + keyword validates. Add a third outcome: "LLM matches known dim but keywords don't" = keyword dictionary expansion, NOT new dim gap.
2. **R2**: Rewrite D5 -- replace "3 similar gaps" auto-trigger with LLM-based grouping + human confirmation. Or: human reviews all gap candidates, no auto-threshold.
3. **R3**: Add D3b -- specify storage: separate .forge/external_findings.json or extend Phase 1a schema with source_origin field.
4. **R4**: Add dedup decision -- source_id in schema, dedup by (file, line, text_hash) within rolling window.
5. **R5**: Replace D4 evidence -- remove HYDRA-REVIEWER and SonarQube citations. Keep checkpatch (PDCA pattern, not keyword mechanism). Add DefectDojo parser pattern.
6. **R6**: Add PDCA Check/Act -- new dimensions enter shadow mode, same Tricorder evaluation as Phase 2.

**Should fix (consensus MEDIUM):**

7. **R8**: Add storage decision explicitly
8. **R10**: Either provide CI log worked example or defer to v2
9. **R11**: Add keyword dictionary update mechanism post-PR-merge
10. **R14**: Add false-gap negative test to D9
11. **R17**: Redefine Level 1 boundary explicitly

---

*Round 1 of cross-AI review. Fix R1-R6 then replan.*
