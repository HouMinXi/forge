## Plan Review: Forge #332 bounded implementation plan v2 + architecture r5

**Plans reviewed**: 2 (`332-bounded-implementation-plan.md` v2, `332-host-failure-classification-architecture.md` r5)
**Review method**: PBR 8-pass (P1/P2/P5/P6 + Implementer/Tester/Operator/Integrator)
**Reviewer**: Stage 2 independent (Plan Review bot) — Stage 1 (`332-plan-review-stage1.md`) used only as context, not copied
**Adaptation**: Documents are a **planning skeleton** + accepted architecture constraints, not a GSD execute-phase with action/imports/exports. Findings distinguish **Phase-0-ok omissions** from **must-fix-before-Phase-1-code** gaps. Codebase symbols / pin `618ce831` **unverified** in this review (box has no Forge checkout).
**Total findings**: 23 (B:3 H:8 M:10 L:2)

---

### Phase 1: Mechanical Checks

#### P1 Symbol Closure

1. **[HIGH][Omission]** Phase 1 names `classify_report_records(state, receipt_context) -> ReportProjection` (+ `classification_complete`) with no module path, type/field schema, import source, or owning package. Acceptable while Phase 0 runs; **blocks starting Phase 1 implementation** until an Interfaces block exists.
2. **[MEDIUM][Omission]** Shared call-chain rule refers to “projection helper” / `reporting_*` / “legacy helper” without requiring Phase 0 inventory to emit **exact current symbols** (functions, modules). Bucket labels (`presentation` | `control` | `shared`) alone are not symbol-closed.
3. **[MEDIUM][Omission]** Architecture §4.6 requires `classification_errors[]` as the additive error surface; plan Phase 1 deliverables never name this collection or its element shape. Implementer can omit it and still claim Phase 1 exit.
4. **[LOW][Omission]** No `Files: Create/Modify` inventory — expected for skeleton; mandatory before any execute-phase slice.

#### P2 Null Propagation

5. **[HIGH][Ambiguity]** `receipt_context` null / empty / partial / permission-error branches undefined. Architecture says still report with unverified diagnostics; Phase 1 deliverables do not specify return shape or `classification_complete` when receipts are missing.
6. **[HIGH][Ambiguity]** Unparseable `state`: architecture §4.8 says keep existing corrupt-state handling and do not force-fit quarantine; Phase 1 never states whether `classify_report_records` is **not called**, returns a hard error, or emits a dedicated incomplete projection. Crash vs silent mis-quarantine risk.
7. **[MEDIUM][Omission]** Summary qualifier when `classification_complete=false` and unresolved product-shaped quarantine items exist: format/field undefined (checklist forbids bare `product=0` but gives no positive schema).

#### P5 Interface Symmetry

8. **[HIGH][Inconsistency]** Architecture §4.1: quarantine is “no as *trusted* product.” Plan visibility + acceptance require unresolved product candidates remain visible with disposition. **Dual representation** (quarantine primary + product-shaped visibility flag vs dual-list vs summary-only qualifier) is unspecified — CLI / JSON / SARIF wirers will diverge.
9. **[HIGH][Inconsistency]** Architecture §4.9: observation digest **canonicalization field-set is a Phase 1 gate item**. Plan Phase 1: “New binding / digest fields **only if Phase 0 required them; else skip**.” Skipping digests contradicts the architecture gate unless Phase 0 explicitly records “canonicalization deferred with architecture waiver.”
10. **[HIGH][Inconsistency]** Plan Open decision #3 (unknown kind + valid binding display) is still open, yet Phase 1 unit-test list already requires “unknown kind (± valid binding)” behavior. Phase 1 tests cannot be authored to a frozen contract until decision #3 is closed by Phase 0.

#### P6 Metadata Currency

11. **[LOW][Extraneous]** Plan status line still “planning draft” while architecture status is “approved for bounded implementation planning.” Align to “Phase 0 authorized / planning skeleton” once Phase 0 starts to avoid treat-as-unapproved stall.
12. **[MEDIUM][Omission]** Plan acceptance checklist drops architecture §6 items that are not covered by the safety matrix: consistent full-store tamper (no claim beyond existing trust), legal disposition/evidence evolution keeping observation binding, non-blocking gate audit ≠ FAIL via channel migration.
13. **[MEDIUM][Omission]** Architecture cites base pin **`618ce831` (provisional)** and related #46 / PR101 `evidence_downgrade`. Plan Phase 0 never requires recording the **measured pin**, invalidating Phase 0 if pin drifts, or forcing failure-class rows that exercise `evidence_downgrade` / related PR behaviors.

---

### Phase 2: PBR Perspectives

#### Implementer

14. **[BLOCKER][Omission]** Cannot write Phase 1 code verbatim from v2: no file paths, no `ReportProjection` field list/types, no algorithm for product-shaped visibility under INFRA retag + invalid ref, no non-circular binding-check procedure, no empty-collection / error-string contracts. **Remediation:** Phase 0 outputs + a GSD-style executable Phase 1 slice (actions, interfaces, verify). Do **not** execute Phase 1 from this skeleton alone.
15. **[HIGH][Omission]** Phase 0 itself lacks a thin work order: where baseline/caller-map/fact-shape artifacts are written, which repo roots to search, grep/inventory method, and who signs the Phase 0 decision. Phase 0 can start as research, but without this, exit criteria are subjective and two agents will produce incomparable baselines.

#### Tester

16. **[HIGH][Omission]** Classifier boundary fixtures under-specified relative to credit-focused safety matrix: empty findings; product-only findings; corrupt `state`; unknown additive field on a product-shaped record; independent-run semantic compare golden list; conditional old-rewriter drop of supplemental fields. Checklist leans credit/restore, weak on adapter inputs.
17. **[MEDIUM][Ambiguity]** Phase 2 “prove classification does not change safety” while consumers are unswitched: missing fixture protocol (in-process `classify_*` vs CLI), and missing explicit assertion that **control paths never call** the new classifier. Guard-omission tests lack named guard→assertion map (depends on Phase 0 inventory).

#### Operator

18. **[MEDIUM][Ambiguity]** Phase 4 two-gate process lacks: authority who grants Entry/Exit, durable approval artifact, idempotent re-run rules for shadow, and failure mode if Entry is skipped. Rollback “inert supplemental fields” has no verification owner or command. Phase 0 “rare: store required” has **no alternate phase branch** if that decision fires.
19. **[MEDIUM][Ambiguity]** Shadow exit “zero unexpected byte / semantic deltas” vs deliverable “diff log: legacy vs proposed projection”: intentional projection diffs are expected in the log — define in-scope file set, allowed side files (diff logs), and what counts as “unexpected.”

#### Integrator

20. **[BLOCKER][Inconsistency]** No hard PR / wave boundary preventing a single change set from mixing (a) read-only adapter, (b) consumer enablement, and (c) incidental shared-helper edits. Architecture forbids folding new integrity gaps into a presentation PR; plan Phase 2 exit echoes escalation — but without an integration gate, Phase 4 “Work” can still land unsafe coupling. **Require:** Phases 1–3 PRs must not modify presentation consumers; Phase 4 PR must not change classifier semantics without re-shadow; shared-helper splits land in a separately qualified PR before enablement.
21. **[BLOCKER][Inconsistency]** Architecture §5 keeps “migration phases 0→5 from r4”; plan collapses to Phases 0–4 + “Later.” **Phase 5 identity is undefined** (absorbed into Phase 4 exit? deferred?). Cross-doc phase ordering / PR boundaries cannot be scheduled until the map is explicit.
22. **[HIGH][Omission]** Open decision #5 (Phase 59 vs dedicated hotfix track) has no owner or deadline, yet it determines branch/CI/release vehicle for every later phase. Phase 0 content can proceed, but Integrator cannot place Phase 1+ on a track without this freeze.
23. **[MEDIUM][Omission]** LOCAL/CI safety row never names concrete consumer entrypoints (CLI subcommands, CI job IDs) for eventual Phase 4 parity. Phase 0 should list them as part of caller inventory exports.

---

### Summary

| Pass | Findings | Top Defect Type |
|------|----------|----------------|
| P1 Symbol | 4 | Omission |
| P2 Null | 3 | Ambiguity |
| P5 Interface | 3 | Inconsistency |
| P6 Metadata | 3 | Omission |
| Implementer | 2 | Omission |
| Tester | 2 | Omission |
| Operator | 2 | Ambiguity |
| Integrator | 4 | Inconsistency |

### Verdict

**Summary verdict**

**Phase 0 can start. Phase 1 cannot be written or executed from the v2 skeleton.**

- **Phase 0:** Evidence work may start (failure-class matrix, caller inventory, adapter-only vs supplemental facts, first-wave fact shapes). Also add a very thin Phase 0 work order (artifact paths, pin freeze and drift invalidation rules, the relevant `evidence_downgrade` rows). None of the 3 BLOCKERs blocks starting Phase 0, but the "Phase 0->5 vs 0->4 mapping" and "PR boundaries" must be fixed before Phase 0 exits, or the Phase 1 schedule will drift.
- **Phase 1:** **No.** Do not write code from v2 until the Phase 0 outputs and an executable Phase 1 slice (Interfaces / `ReportProjection` fields / visibility notation / acyclic binding algorithm / test fixtures) exist. Three items must be closed first: whether the digest is mandatory (F09), unknown-kind display (F10), and the dual quarantine representation (F08).
- **Relative to Stage 1:** Independently confirms its two BLOCKER directions (cannot be implemented verbatim; missing PR boundaries) and adds or raises: the missing mapping between architecture Phase 0->5 and plan 0->4, the conflict between §4.9 canonicalization and the plan's "may skip digest", the conflict between Open decision #3 and pre-spending Phase 1 tests, and the gaps in `classification_errors[]` and the pin / `evidence_downgrade` obligations.

**English one-liner:** Fit for Phase 0 evidence; unfit for Phase 1 implementation until Phase 0 outputs + executable slice resolve the three BLOCKERs and the P5 contract freezes.

**Next:** After Phase 0 artifacts land, re-run Stage 1 self-review on the Phase 1 executable slice, then Stage 2 again before `/gsd-execute-phase`.
