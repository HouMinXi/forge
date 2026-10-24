## Plan Review: Forge #332 bounded implementation plan **v3** (incremental Stage 2)

**Plans reviewed**: 2 (`332-bounded-implementation-plan.md` v3, `332-host-failure-classification-architecture.md` r5)
**Review method**: PBR 8-pass incremental vs `/workspace/332-plan-review-stage2.md` (v2)
**Reviewer**: Stage 2 independent (Plan Review bot)
**Scope rule**: Do **not** re-file closed v2 items; only residuals, regressions, or new defects.
**Supplement**: Debt / public-GSD consistency patch appended 2026-10-19 (CoS context).
**Symbol note**: Seed inventory symbols verified on research pin `618ce831` (`state.py`); HEAD at review = `1db6ff23a` (descendant of pin). `classification_complete` absent on pin (expected new).
**Total findings (open on v3 + debt patch; digest HIGH closed after arch §4.9 amend)**: 12 (B:0 H:2 M:8 L:2)

---

### Disposition of v2 focus items (requested close-check)

| Focus / prior | v3 disposition |
| --- | --- |
| Phase map arch 0→5 ↔ plan | **CLOSED** — Phase map table; Phase 5 = Later; Phase 6 = out of scope |
| PR / wave boundaries | **CLOSED** — explicit May touch / Must not table + shared-helper own PR |
| Phase 0 thin work order | **CLOSED** — pin invalidate, artifact paths, inventory method, sign-off contents |
| Digest waiver vs arch §4.9 | **CLOSED** — arch §4.9 now requires dated waiver in `phase0-decision.md` when adapter-only (plan-only note insufficient) |
| Open #3 gate | **CLOSED** — freeze before Phase 1 tests that cover that case |
| Dual representation A/B | **CLOSED** — Phase 0 must pick A or B; consumers share choice |
| `classification_errors[]` | **CLOSED** — Phase 1 minimum fields + acceptance |
| pin + `evidence_downgrade` | **CLOSED** — worktree/pin + matrix rows for PR101 / PR73 / PR98 |
| v2 BLOCKER implement-from-skeleton | **CLOSED as process** — non-goal + Phase 1 Entry requires executable slice (Phase 1 **code** still correctly blocked) |
| v2 status “draft” | **CLOSED** — “Phase 0 authorized” |
| Arch §6 checklist gaps | **CLOSED** — Phase 2 “Also” + acceptance |
| Consumer entrypoints | **CLOSED** — `consumer-entrypoints.md` |
| Phase 2 classify vs control protocol | **CLOSED** — control must not call classifier; guard map from Phase 0 |
| Rare store branch | **CLOSED** — stop + alternate branch before Phase 1 |
| Shadow expected diffs | **CLOSED for skeleton** — deferred in-scope definition to executable slice |

---

### Phase 1: Mechanical Checks

#### P1 Symbol Closure

**Prior F01–F04:** F02/F03 closed. F01/F04 intentionally deferred to executable Phase 1 slice — not re-filed as open blockers for Phase 0.

1. **[LOW][Ambiguity]** Inventory seed symbols (`reporting_product_findings`, `is_provider_diagnostic`, `record_provider_failure`) **exist on pin `618ce831`** (verified via `git grep`). Residual: plan still reads like an allowlist — say “seed list, expand when absent,” and re-grep on the **measured** Phase 0 pin if it leaves `618ce831`.

#### P2 Null Propagation

2. **[MEDIUM][Ambiguity]** (residual of v2 F05/F06) Phase 1 now names null branches, but still allows **“do not call *or* hard-fail”** for unparseable state, and does not specify `ReportProjection` / `classification_complete` when `receipt_context` is missing/partial. Two incompatible corrupt-state behaviors remain open until the executable slice picks one and matches existing corrupt handling.

3. **[MEDIUM][Omission]** (residual of v2 F07) Positive **summary qualifier schema** (field names / copy) still undefined; only the negative “no bare product=0” rule. Acceptable to defer to Phase 1 slice **after** dual-representation A/B freeze — must appear in that slice’s Interfaces.

#### P5 Interface Symmetry

**Prior F08–F10 closed.** No new caller/callee disagreement introduced for Phase 0.

4. **[CLOSED][was HIGH Inconsistency]** Arch §4.9 amended (local draft): canonicalization gate applies when digests in scope; adapter-only requires **dated waiver in `phase0-decision.md`** (approver + reason); plan-only note insufficient. Aligns with plan v3. Residual: ensure published/remote arch matches local draft before treating as frozen (user: no push until docs finalized).

#### P6 Metadata Currency

**Prior F11–F13 closed.** CLEAN for currency of status / pin / §6 carry-forward relative to v2.

---

### Phase 2: PBR Perspectives

#### Implementer

**Prior F14/F15 closed as process / work order.** Phase 1 code still correctly forbidden until slice — expected, not a new defect.

CLEAN for Phase 0 executability of evidence work (artifacts paths + decisions listed).

#### Tester

5. **[MEDIUM][Omission]** (residual of v2 F16) Skeleton still lacks an explicit unhappy-fixture **checklist** for the future Phase 1 slice (empty findings; product-only; corrupt state; unknown additive on product-shaped; old-rewriter drop if supplemental). Phase 1 Entry requires Stage 1+2 on the slice — residual is “slice must include these,” not a Phase 0 stopper. Recommend adding a one-line “Phase 1 slice must list fixtures: …” in v3 or in the slice template.

#### Operator

6. **[MEDIUM][Ambiguity]** (residual of v2 F18) Phase 4 Entry now says “who/what artifact,” but **default authority role**, failure if Entry skipped, shadow **idempotent re-run** rules, and rollback **verification owner/command** remain unspecified. Rare-store branch is fixed. Not a Phase 0 blocker; freeze before Phase 4 Entry work.

7. **[LOW][Ambiguity]** Phase 0 artifacts default to `/workspace/332-phase0/` with optional forge `.planning/` mirror. Operators on the forge worktree may never see box paths. Phase 0 exit should record the **canonical** copy location used for sign-off.

#### Integrator

**Prior F20/F21/F23 closed.** Track (F22) must be answered in `phase0-decision.md` — closed as Phase 0 obligation.

8. **[MEDIUM][Ambiguity]** Phase map: Phase 5 = Later **unless Phase 0 pulls a minimal label set**. If Phase 0 does pull labels, **which phase/PR** owns that work (still Later? folded into Phase 1?). Undefined ownership can breach PR boundaries. Phase 0 decision must either forbid wave-1 labels or name the owning phase and PR class.

---

### Debt & public GSD consistency patch (CoS context, 2026-10-19)

**Context accepted (verified where noted):**

- Public GSD history is in-repo at `docs/gsd/` (Phase59 eighteen PLANs, `STATE-2026-10-18.md`, `SOURCE-RECONCILIATION-2026-10-19.md`, `future-direction/`). Private `.planning/` stays local / out of public tree — plan v3’s optional forge `.planning/` mirror matches that split.
- Research pin for published packets remains `618ce831…`; forge main HEAD at this review = `1db6ff23a` (`618ce831` is an ancestor). `SOURCE-RECONCILIATION-2026-10-19.md` states pin-bound docs’ “current” / line numbers apply **to the pin**, and source must be rechecked before implementation (index itself observed main `c1053f59…` at publication).
- Phase59 **D-10** (`59-CONTEXT.md`): clearer error classification (code defect vs unusable model output vs transport/ops vs missing evidence) while preserving deterministic receipt and clean-cycle authority — **adjacent but not identical** to #332 report-channel migration.
- Public stance: STATE — “Publication grants no execution admission.” `future-direction/` proposal is documentation-only; no gating/clean-cycle change without separate proposal.

#### New findings (debt)

9. **[HIGH][Omission]** Plan v3 pin work order (“measure `618ce831` or record successor; invalidate if HEAD ≠ pin”) does **not** cite or bind Phase 0 to public `docs/gsd/SOURCE-RECONCILIATION-2026-10-19.md` **current≠pin** discipline (pin-bound wording / line numbers are not HEAD facts). With HEAD already `1db6ff23a`, Phase 0 **must** record the measured SHA and forbid treating 2026-10-18 packet line numbers as current-main bindings. **Remediation:** SOURCE-RECONCILIATION (or equivalent) as mandatory Phase 0 input; `phase0-decision.md` lists measured SHA + packet-pin vs measured delta.

10. **[HIGH][Inconsistency]** #332 plan/arch can be misread as advancing or closing Phase59 **D-10**. D-10 preserves receipt/clean-cycle **authority**; #332 is report-only and must not change credit/restore. Plan never states non-goals: **does not admit D-10 execution**, **does not satisfy D-10**, **report channels ≠ D-10 taxonomy** unless Phase 0 publishes an explicit mapping (or refusal). **Remediation:** Non-goals + Phase 0 appendix mapping or explicit non-mapping.

11. **[MEDIUM][Ambiguity]** Open decision #5 (Phase 59 vs hotfix track) sits beside public GSD “Publication grants no execution admission.” Choosing “Phase 59 track” must not mean Phase59 plan admission, 59-16 runtime admission, or formal 3/5 / P59-CLOSE progress. **Remediation:** one sentence in plan + `phase0-decision.md`: track = **VCS/CI vehicle only**; grants no GSD phase admission.

12. **[MEDIUM][Omission]** Phase 0 work order does not require reading public `docs/gsd/` debt anchors before freezing the failure-class matrix (Phase59 PLAN set / D-10, SOURCE-RECONCILIATION freshness, future-direction “no gating change”). Without that, `evidence_downgrade` / `evidence_rejected` / typed-diagnostic rows risk contradicting published snapshot language. **Remediation:** checklist “GSD public inputs reviewed” with paths.

13. **[MEDIUM][Omission]** Matrix rows for `evidence_downgrade` / PR101 / PR73 / PR98 “where present on the measured pin” do not require binding each row’s credit/restore cells to the **measured SHA** + source symbol/test id (vs assuming HEAD or pin-packet prose). Wrong pin binding → silent Incorrect Fact in Phase 0. **Remediation:** each row cites measured commit + symbol/test id.

**Symbol / graph note:** Seed symbols verified on pin via `git grep` on the forge worktree. CoS reports `code-review-graph` now aligned to HEAD `1db6ff23a` (`built_at_commit` match); this patch did not re-query the graph.

**Publish / cloud-dot standing process (user):** After #332 GSD plan + plan-review artifacts are **finalized and user-confirmed**, publish under public `docs/gsd/` on GitHub for cloud dots (Forge default going forward). **Do not push** undrafted local/box drafts. Private `.planning/` stays out of the public tree.


### Summary (base incremental + debt patch)

| Pass | Findings | Top Defect Type |
|------|----------|----------------|
| P1 Symbol | 1 | Ambiguity (LOW after pin verify) |
| P2 Null | 2 | Ambiguity |
| P5 Interface | 0 (1 closed) | — |
| P6 Metadata | 0 | — |
| Implementer | 0 | — |
| Tester | 1 | Omission |
| Operator | 2 | Ambiguity |
| Integrator | 1 | Ambiguity |
| Debt / public GSD | 5 | Omission / Inconsistency |

---

### Verdict

**Summary verdict**

**The debt patch is merged into this same report. Phase 0 can start; Phase 1 cannot be written from the skeleton. 12 open items (B:0 H:2 M:8 L:2), no BLOCKER.**

- **Digest waiver HIGH:** The local architecture §4.9 now has a dated waiver clause. **Closed** (until the remote is pushed, the local draft is authoritative).
- **Still open HIGH (2):** (1) Phase 0 must bind the public `SOURCE-RECONCILIATION` **current != pin** discipline and record the measured SHA (HEAD=`1db6ff23a`). (2) The plan must state that #332 is not Phase 59 D-10 execution or closure, and that report channels are not equated with D-10 by default.
- **MEDIUM debt items:** The Open#5 track is not GSD admission. Phase 0 must read the `docs/gsd` debt anchors. Matrix rows must bind the measured commit plus symbol / test id.
- **Skeleton:** No further Stage 2. The next round reviews the Phase 0 artifacts and the executable Phase 1 slice.

**English one-liner:** Debt patch landed on the same Stage2-v3 file. Phase 0 go; Phase 1 gated. Digest HIGH closed by arch §4.9 amend; two HIGH remain (current≠pin discipline; D-10 non-equivalence).

**Next:** Thin-edit plan for findings 9–11 if desired; then Phase 0. No further skeleton Stage 2 unless plan changes again.
