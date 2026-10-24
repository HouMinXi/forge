## Plan Review: Forge #332 bounded implementation plan v2

**Plans reviewed**: 2 (`332-bounded-implementation-plan.md` v2, companion `332-host-failure-classification-architecture.md` r5)
**Review method**: PBR 8-pass adapted (plan is a **planning skeleton**, not a GSD phase with action/imports/exports blocks)
**Reviewer**: Stage 1 self-review (Chief of Staff) — **not** a quality gate per skill ceiling
**Total findings**: 14 (B:2 H:5 M:5 L:2)

### Adaptation note

Mechanical P1/P5 expect per-task identifiers, imports, and interface blocks. This plan intentionally defers symbols to Phase 0. Findings below mark **skeleton-appropriate omissions** vs **must-fix-before-Phase-1-code** gaps. Do not treat Stage 1 CLEAN as stop criteria; cross-AI / independent bot review is required.

### Phase 1: Mechanical Checks

#### P1 Symbol Closure

1. **[HIGH][Omission]** `classify_report_records` / `ReportProjection` / `receipt_context` named with no module path, type fields, or import source. Acceptable for Phase 0; **BLOCK Phase 1 start** until Interfaces block exists (module, signature, field list, exports).
2. **[MEDIUM][Omission]** “legacy helper” / “projection helper” / `reporting_*` referenced without concrete current symbols (`reporting_product_findings`, `is_provider_diagnostic`, etc.). Phase 0 caller inventory must fill exact names; plan should require that inventory to name symbols, not only buckets.
3. **[LOW][Omission]** No `Files: Create/Modify` inventory — expected for skeleton; add before any execute-phase.

#### P2 Null Propagation

4. **[HIGH][Ambiguity]** `receipt_context` nullable behavior unspecified: missing receipts, partial proof, permission errors. Architecture says “still report”; plan Phase 1 deliverables do not define `receipt_context is None` / empty / incomplete branches.
5. **[MEDIUM][Omission]** `classification_complete=false` interaction with empty `product[]` + non-empty quarantine product-shaped items — summary qualifier format undefined (crash/ambiguity risk for implementer).

#### P5 Interface Symmetry

6. **[HIGH][Inconsistency]** Arch §4.1: quarantine is “no as *trusted* product.” Plan visibility contract requires unresolved product candidates remain visible. **Dual representation** (primary quarantine + product-shaped visibility flag vs dual listing) not specified — caller and SARIF wirers will disagree.
7. **[MEDIUM][Inconsistency]** Arch §4.9: observation digest / canonicalization is “Phase 1 gate item.” Plan Phase 1 says new fields only if Phase 0 requires — OK — but if Phase 0 chooses supplemental facts, plan never points to a canonicalization artifact path/owner.

#### P6 Metadata Currency

8. **[LOW][Extraneous]** Status still “planning draft” while architecture says “approved for bounded planning” — align status line to “Phase 0 authorized” vs “draft” once user starts Phase 0.
9. **[MEDIUM][Omission]** Acceptance checklist omits arch §6 items present in architecture but not plan checklist: full-store consistent tamper (arch §6), legal disposition/evidence evolution binding, non-blocking gate audit ≠ FAIL. Safety matrix covers credit cases; **tamper / disposition / non-blocking audit** under-specified in plan checklist.

### Phase 2: PBR Perspectives

#### Implementer

10. **[BLOCKER][Omission]** Cannot write Phase 1 code verbatim: no file paths, no `ReportProjection` schema, no algorithm for “product-shaped quarantine” visibility, no error strings, no empty-collection behavior. **Remediation:** after Phase 0, produce an executable Phase 1 plan slice (GSD-style action + interfaces + verify) — do not execute from v2 skeleton alone.
11. **[HIGH][Ambiguity]** Phase 2 “document non-product-dependent enforcement path” vs “prove classification does not change safety”: when adapter exists but consumers unswitched, how is regression run? Missing: fixture protocol (call classify in-process vs CLI), and assertion that control paths never call classify.

#### Tester

12. **[HIGH][Omission]** Missing explicit unhappy fixtures in checklist: empty findings; only-product findings; corrupt `state.json`; unknown additive field on product finding; save/reload with supplemental fields dropped by old rewriter (conditional on Phase 0). Safety matrix is strong on credit; weak on classifier input boundaries.
13. **[MEDIUM][Ambiguity]** Guard-omission tests: which guards, which test commands, which assertion IDs? Phase 2 needs a named guard→assertion map from Phase 0 inventory.

#### Operator

14. **[MEDIUM][Ambiguity]** Phase 4 two-gate process lacks: who grants Entry/Exit, artifact to record approval, idempotent re-run of shadow job, failure if Entry skipped. Rollback condition “inert supplemental fields” has no verification step owner.
15. **[LOW][Ambiguity]** Shadow “zero unexpected byte deltas”: if classifier is pure, bytes always match — define what file set is in scope and what “expected” metadata side files (diff logs) may change.

#### Integrator

16. **[BLOCKER][Inconsistency]** Plan Phase 2 exit allows escalating new integrity gaps as safety changes; Arch forbids presentation PR absorbing them — good — but **no integration gate** prevents a single PR from mixing adapter + consumer switch + incidental helper edit. Require: Phase 1–3 PRs cannot modify presentation consumers; Phase 4 PR cannot change classifier semantics without re-shadow.
17. **[MEDIUM][Inconsistency]** LOCAL/CI row says “report may change later” during Phase 2 — correct — but does not name concrete consumer entrypoints (`forge` CLI subcommands, CI job names) for the eventual Phase 4 parity check. Phase 0 should list them.

### Summary

| Pass | Findings | Top Defect Type |
|------|----------|----------------|
| P1 Symbol | 3 | Omission |
| P2 Null | 2 | Ambiguity |
| P5 Interface | 2 | Inconsistency |
| P6 Metadata | 2 | Omission |
| Implementer | 2 | Omission |
| Tester | 2 | Omission |
| Operator | 2 | Ambiguity |
| Integrator | 2 | Inconsistency |

**Verdict (Stage 1):** Plan v2 is **fit to run Phase 0 evidence work**, **not** fit to implement Phase 1+ from this document alone. Two BLOCKERs are about missing executable specificity and PR boundary discipline — fix via Phase 0 outputs + a Phase 1 executable slice, not another architecture round.

**Next (skill-mandated):** Stage 2 independent / cross-AI review — do not stop on self-review.
