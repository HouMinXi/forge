# Forge #332 — Bounded implementation plan (v3)

Status: **Phase 0 authorized** — planning skeleton; **not** an execute-phase lease for Phase 1+  
Parent: `332-host-failure-classification-architecture.md` (r5)  
Reviews: Stage 1; Stage 2 v2; Stage 2 v3 + debt patch (`332-plan-review-stage2-v3.md`). Digest waiver HIGH closed by arch §4.9. Remaining HIGH: current≠pin; #332 ≠ Phase59 D-10.  
Not approved: production report-surface **enablement**; credit-policy change; new authority store “for neatness”

Architecture open choices (adapter-only vs minimal facts, first-wave shapes, old-reader range, unknown-kind display, track) are **Phase 0 evidence**, not pre-guessed here.

---

## Goal

Ship a **read-only** report classification adapter and, after **explicit switch authorization**, enable CLI / JSON / SARIF / summary together — without changing terminal verdict, credit, or restore policy.

## Non-goals

- `classification_complete == false → FAIL`
- Product candidates vanishing when bindings missing
- Circular validation via channel / cache / ref
- Full `host_diagnostics[]` center unless Phase 0 proves need
- Wave-1 modifiers / channel-cache persistence / full subtype taxonomy
- Granted-policy (schema/coverage credit) changes
- Mandatory rewrite of all control paths “for neatness”
- Implementing Phase 1+ from this skeleton alone (requires executable Phase 1 slice after Phase 0)
- **Admitting or closing Phase59 D-10** (or any Phase59 execution/progress). #332 is report-classification migration only; report channels are **not** the D-10 taxonomy unless Phase 0 publishes an explicit mapping (or explicit refusal to map)
- Treating a “Phase 59 track” choice as GSD phase admission, 59-16 runtime admission, or formal 3/5 / P59-CLOSE progress — track = **VCS/CI vehicle only**

---

## Phase map (architecture ↔ this plan)

| Architecture (r4/r5) | This plan | Notes |
| --- | --- | --- |
| Phase 0 Freeze + decision gate | **Phase 0** | Baseline, callers, adapter vs facts |
| Phase 1 Bindings / adapter | **Phase 1** | Read-only adapter only |
| Phase 2 Independent enforcement audit | **Phase 2** | Safety regression; no new gates |
| Phase 3 Shadow | **Phase 3** | Shadow only |
| Phase 4 Switch report | **Phase 4** | Two-gate enablement |
| Phase 5 Subtype detail | **Later / Phase 5** | Out of wave 1 unless Phase 0 pulls a minimal label set |
| Phase 6 Credit policy | **Out of scope** | Separate proposal |

---

## PR / wave boundaries (BLOCKER freeze)

| Change set | May touch | Must not |
| --- | --- | --- |
| Phase 0 | Docs / matrices / inventories under agreed paths | Production report consumers, classifier behavior in shipped paths |
| Phase 1–3 PRs | Adapter + tests + shadow harness | Presentation consumers (CLI/JSON/SARIF/summary enablement); silent shared-helper semantic edits |
| Shared-helper split (if needed) | **Own PR**, behaviorally qualified vs legacy accounting | Land inside Phase 4 enablement PR |
| Phase 4 | Consumer wiring only (after Entry auth) | Classifier semantic changes without re-shadow + Phase 3 re-exit |
| Any new integrity/safety behavior | Explicit safety PR / approval | Presentation / enablement PR |

---

## Hard planning constraints (from r5 + reviews)

1. Report classification failure alone does not alter terminal verdict.
2. Quarantine ≠ “proved not a product issue”; product candidates stay visible when bindings fail.
3. Validation must not circularly depend on proposed channel / cache / ref.
4. Classifier is **read-only** at report time.
5. Prefer reuse of existing proofs; minimal supplemental facts only if Phase 0 requires.
6. Changing a shared projection helper must **not** silently change control semantics.

### Shared call-chain rule (required)

If Phase 0 finds a **control** caller still depends on a projection helper that report work will change:

- Do **not** claim “control unchanged” merely because the control caller’s source text was not edited.
- Retain legacy accounting via an **explicit unchanged path** (report-only adapter + legacy control helper, or split report vs accounting with regression).
- Any shared-dependency split must be **separately identified and behaviorally qualified** before report switch.
- Phase 2 proves: **new report classification does not change existing safety decisions** — not a new gatekeeping system.

### Product-shaped visibility (dual representation — freeze choice in Phase 0 exit)

Unresolved product candidates that cannot take a trusted `product` channel must still be **user-visible**. Phase 0 must pick **one** representation and record it:

- **A:** primary `quarantine` + `product_shaped: true` (or equivalent) carrying file/location/description/disposition; summary counts qualified unresolved candidates; or
- **B:** dual listing (trusted product[] + unresolved_product_candidates[]) with explicit non-trusted marking.

SARIF/CLI/JSON must use the same choice. Never promote these to trusted `execution`/`gate`. Never show bare `product=0` without qualifier while such items exist.

### Digest / canonicalization vs skip

Architecture §4.9: canonicalization field-set is a Phase 1 gate **when bindings/digests are in scope**.

- If Phase 0 is **adapter-only** (no new binding/digest fields): Phase 0 exit must record an explicit **architecture waiver** — “canonicalization deferred; no new digests in wave 1.”
- If Phase 0 requires supplemental binding/digest fields: canonicalization artifact is a **Phase 1 entry gate** (not skippable).

### Open decision #3 gate

Unknown kind + valid binding display must be **frozen in Phase 0 exit** before Phase 1 unit tests that cover that case are authored.

---

## Phase 0 — Freeze baseline + decision gate

### Thin work order

| Item | Contract |
| --- | --- |
| Worktree / pin | Measure against current HEAD; record **measured SHA**. Treat published research pin `618ce831` and `docs/gsd/SOURCE-RECONCILIATION-2026-10-19.md` as **historical**: words like "current" and line numbers bind that pin, not HEAD (`1db6ff23a` at last review). If measured SHA drifts after Phase 0 sign-off, **invalidate** matrices until re-measured. |
| Public GSD inputs | Before freezing the failure-class matrix, read and checklist: `docs/gsd/SOURCE-RECONCILIATION-2026-10-19.md`, Phase59 D-10 / related PLAN notes, dated STATE snapshot, `docs/gsd/future-direction/` “no gating / no admission” language. Record paths in `phase0-decision.md`. |
| Artifact paths | Write under `/workspace/332-phase0/` (mirror to forge `.planning/` only if user asks; never `git add` gitignored planning without ask): `failure-class-matrix.md`, `caller-inventory.md`, `fact-shapes.md`, `phase0-decision.md`, `consumer-entrypoints.md` |
| Inventory method | Grep/AST seed: `reporting_product_findings`, `is_provider_diagnostic`, `record_provider_failure`, SARIF/CLI summary emitters (**seed list, not exhaustive allowlist** — confirm on measured SHA and expand); list **exact symbols + modules**; optional later `code-review-graph` for fuller caller closure |
| Sign-off | `phase0-decision.md` states adapter-only vs minimal fields (exact list) vs store; dual-representation choice A/B; unknown-kind display; track (Phase 59 vs hotfix); digest waiver or canonicalization required |

**Deliverables**

1. **Failure-class matrix** columns:

   | failure class | producer/stage | verifiable raw facts | allowed report class + fallback | classification rationale | current report | current verdict | current credit | current restore |

   Include rows that exercise **`evidence_downgrade`** / related PR101 behavior and PR73 / PR98 shapes where present on the **measured** pin. **Each row** cites measured commit SHA + source symbol and/or test id for credit/restore cells (do not copy pin-packet prose as if it were HEAD).

2. **Caller inventory**: every projection helper caller → `presentation` | `control` | `shared`, with **exact symbol names**.

3. **Consumer entrypoints**: LOCAL CLI subcommands + CI job IDs that consume report/verdict (for Phase 4 parity).

4. **Decision** + first-wave **fact shapes** (not bare kind names).

5. Phase map + PR boundaries acknowledged in `phase0-decision.md`.

**Exit:** artifacts above + decisions frozen; **no** product-projection change. Then author **executable Phase 1 slice** (interfaces, schema, verify) before any Phase 1 code.

---

## Phase 1 — Read-only adapter (after executable slice)

**Entry:** Phase 0 exit + executable Phase 1 plan slice reviewed (Stage 1+2) + digest waiver or canonicalization ready + Open #3 frozen + dual-representation choice frozen.

**Deliverables (minimum in executable slice)**

- Module path + `classify_report_records(state, receipt_context) -> ReportProjection`
- `ReportProjection` fields: `product[]`, `execution[]`, `gate[]`, `quarantine[]`, `classification_errors[]`, `classification_complete: bool`, plus fields required by chosen dual-representation
- Null branches: `receipt_context` missing/partial; unparseable state → **do not call** or hard-fail per existing corrupt handling (not force-quarantine)
- Non-circular binding checks; read-only (no write-back)
- Unit tests per Phase 0 fact shapes + visibility under INFRA retag + invalid ref

**Exit:** adapter green; no consumer enablement.

---

## Phase 2 — Safety regression (no new blockers)

**Intent:** prove new report classification does not change existing safety decisions.

**Protocol:** control paths **must not call** the new classifier. Regression uses existing safety entrypoints + in-process classify only where asserting report/safety decoupling. Guard→assertion map from Phase 0 inventory.

**Compact safety regression matrix (must stay green):**

| Scenario | Must keep |
| --- | --- |
| Two real prior clean credits, then acquisition failure | Zero new credit; existing restore unchanged |
| PASS threshold met, then interrupt | Still non-PASS; prior credit does not authorize pass |
| Product defect + execution failure same round | Existing product-accounting priority unchanged |
| Schema / coverage failure | Existing reset + terminal unchanged |
| Typed excerpt rejection / unavailable | Existing restore behaviors respectively unchanged |
| LOCAL / CI real consumers (named in Phase 0) | Safety adjudication matches prior contract |
| Save → reload → later healthy request | Not memory-only / counter-only |

**Also (from architecture §6, not optional):**

- Consistent full-store tamper: no claim beyond existing trust model
- Legal disposition/evidence updates do not break observation binding (if digests in scope; else N/A under waiver)
- Non-blocking gate audit ≠ FAIL solely via channel migration

**Exit:** matrix green; new integrity gaps → separate safety approval, not presentation PR.

---

## Phase 3 — Shadow

- Same persisted snapshot: findings/receipts/history **file bytes** identical around classify
- Independent runs: semantic/stable fields only
- Diff log may change (expected projection diffs); define in-scope set vs allowed side files in executable slice
- Classifier read-only

**Exit:** shadow green.

---

## Phase 4 — Implement wiring, then enable (two gates)

| Gate | Meaning |
| --- | --- |
| **Entry** | Phase 0–3 passed; output contracts frozen; **explicit authorization** recorded (who/what artifact) to implement wiring + real-output tests |
| **Work** | Wire CLI, JSON, SARIF, summary together; product-visibility tests; control on proof/legacy paths |
| **Exit** | Report deltas match contract; safety matrix green; **explicit approve** merge/enable |

**Rollback:** Consumer-only rollback only if supplemental fields inert to legacy control **and** load/rewrite compatibility verified; else include serialization/loader in rollback tests. Adapter-only → consumer-only rollback is default expectation.

If Phase 0 chose “store required” (rare): stop and write an alternate phase branch before Phase 1 — do not improvise inside Phase 4.

---

## Later (Phase 5 / out of wave 1)

- Persistent modifiers / channel cache  
- Full subtype labels (architecture Phase 5)  
- Credit-policy proposal (architecture Phase 6)

---

## Acceptance checklist

### Report / classification

- `[ ]` Classification failure alone does not change terminal verdict  
- `[ ]` Product candidate + INFRA retag + invalid ref stays visible (chosen dual representation)  
- `[ ]` Summary never bare `product=0` without qualifier while unresolved product-shaped items exist  
- `[ ]` Validation non-circular; classifier read-only  
- `[ ]` `classification_errors[]` present; unknown additive field does not mass-quarantine  
- `[ ]` Unknown kind + valid binding per Phase 0 freeze  
- `[ ]` Historical display-quarantine ≠ current permanent blocker  
- `[ ]` Channel-cache mutation alone ≠ verify/credit change  
- `[ ]` Report surfaces share one projection; verify does not  

### Safety matrix + arch §6

- `[ ]` Seven credit/restore/CI rows above  
- `[ ]` Full-store tamper honesty  
- `[ ]` Disposition/evidence evolution binding (or N/A under digest waiver)  
- `[ ]` Non-blocking gate audit ≠ FAIL via migration  

### Process

- `[ ]` Shared control deps split or legacy path with behavioral proof (own PR if split)  
- `[ ]` Phase map + PR boundaries respected  
- `[ ]` Phase 4 Entry auth before enablement work; Exit auth before merge/release  
- `[ ]` Rollback conditions documented for Phase 0 path  
- `[ ]` Executable Phase 1 slice Stage 1+2 reviewed before Phase 1 code  

---

## Open decisions (Phase 0 must answer)

1. Adapter-only vs minimal supplemental facts (exact fields) vs store  
2. First-wave **fact shapes**  
3. Unknown kind + valid binding display (**blocks Phase 1 tests**)  
4. Old rewrite support range  
5. Track: Phase 59 vs hotfix — **VCS/CI vehicle only**; grants no GSD admission  
6. Dual representation A vs B  
7. Digest waiver vs canonicalization required  
8. D-10: explicit mapping appendix or explicit non-mapping refusal  
