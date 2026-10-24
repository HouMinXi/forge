# Forge #332 — Host-issued failure classification (architecture draft, r5)

Status: **approved for bounded implementation planning** — not an implementation lease; **not** approved to switch production report surfaces yet
Companion plan: `332-bounded-implementation-plan.md` (v3: phase map, PR boundaries, Phase 0 work order; Stage 2 review applied)
Audience: lead / architecture review → planning
Date: 2026-10-19 (r5: planning-ready constraints; no new architecture layer)
Base pin (provisional until Phase 0): **`618ce831`**
Issue: **private Gitea #332**
Related: Phase 59; PR73; PR98; #46 / PR101 `evidence_downgrade`

**Doc honesty:** private `.planning` / pin claims are not independently re-verified here.

---

## 0. Verdict and how r5 differs from r4

**Verdict:** Approve for **bounded implementation planning**. Architecture direction accepted. Implementation and report-surface switch still require **phase gates**. This is **not** a fully frozen implementation contract (Phase 0, canonicalization, and compatibility choices remain open).

r5 only tightens four planning constraints (no new channels / stores / abstract layers):

| # | Tightening |
| --- | --- |
| 1 | Failure to obtain a **trusted report classification** does **not** itself alter terminal verdict |
| 2 | Unresolved **product candidates** must not vanish from user view when report bindings are missing |
| 3 | “Independently validated” must not circularly depend on proposed channel / cache / ref |
| 4 | Unknown **fields** vs unknown **kinds** vs unknown **authority versions** vs unparseable state — separate contracts; classifier is **read-only** |

r4 foundations kept: report ≠ safety authority; reuse attempt/receipt facts; digests = binding not authentication; historical quarantine ≠ auto current blocker; audit enforcement before switch; credit policy unchanged for #332.

---

## 1. Problem (unchanged)

Unclassified INFRA still enters product projection under narrow capacity-only host escape. #332 is a **reporting classification migration**, not a clean-counter fix.

---

## 2. Hard invariants (planning must not violate)

1. **Report consumers** share one classification; **safety consumers** share existing proofs. Neither impersonates the other.
2. Report channel / `classification_complete` / channel cache are **not** terminal-verdict oracles and have **no** clean-credit authority.
3. Prefer classification **adapter** over a new authority store; add minimal host facts only if Phase 0 proves existing facts insufficient.
4. Digests inherit existing receipt/attempt trust; no stronger tamper claim.
5. Never mint host authority from post-load finding alone.
6. Schema/coverage (and sibling) **credit actions unchanged** in #332.
7. Classifier is **read-only** at report time: no channel-cache write-back, no host-fact backfill while generating reports.

---

## 3. Trust and validation (non-circular)

> Support for a trusted report class must be establishable **without** depending on the proposed `report_channel`, channel cache, or the diagnostic ref under validation.

Valid: existing attempt/receipt/verifier outcomes already proven by today’s safety paths.  
Invalid: “host record says execution → verifier accepts → therefore execution.”

Threat model unchanged from r4 (model forge / accidental corruption in-scope; full-store local rewrite out of scope for digest-only proofs).

---

## 4. Report model (constraints for the plan)

### 4.1 Channels (semantics only)

| Channel | Meaning | Product results? | Diagnostic output? |
| --- | --- | --- | --- |
| `product` | Product candidate (trusted placement when bindings allow) | yes | optional |
| `execution` | Trusted host execution diagnostic | no | yes |
| `gate` | Trusted evidence/authority/audit diagnostic | no | yes |
| `quarantine` | Unverified for **display** — “don’t know what it is,” **not** “proved not a product issue” | no as *trusted* product | yes, marked unverified |

### 4.2 Classification failure ≠ new terminal policy

| Situation | Contract |
| --- | --- |
| Report adapter does not recognize a type, but existing proofs are complete and valid | Emit unverified diagnostics; `classification_complete=false`; **no** new safety decision |
| Existing verifier/restore detects current evidence damage | Keep **existing** non-PASS behavior |
| New binding check finds integrity issues **not** covered by today’s safety paths | Treat as an **explicit safety-behavior change** requiring separate approval — **not** “presentation-only migration” |

> Failure to obtain a trusted report classification does **not** itself alter the terminal verdict. Independently detected proof/integrity failures retain their existing enforcement. Any **additional** blocker requires explicit approval.

Forbidden implementation smell: `classification_complete == false → FAIL`.

### 4.3 Product candidates must remain visible

A pre-existing product candidate must **not** be reinterpreted as a non-product record solely because:

- new report bindings are missing,
- channel cache is damaged,
- or `diagnostic_ref` is invalid.

Minimum display contract:

- Must **not** be promoted to trusted `execution` / `gate`
- Retain original file, location, description, and disposition
- Summary must still show unresolved **candidate** count with qualification when classification is incomplete
- Must **not** show bare `product findings = 0` while unresolved quarantined product-shaped candidates exist without that qualifier
- Safety paths continue to treat the original candidate under **existing** rules

Quarantine means **unknown for trusted display**, not **proven not a product issue**.

### 4.4 Gate / execution lists ≠ automatic FAIL

Informational, historical, or resolved diagnostics may appear under `gate` / `execution` for reporting. Blockers come only from **existing** attempt/gate outcomes (and any separately approved safety changes). Do not implement `channel_list.nonempty → FAIL`.

### 4.5 Credit / receipt table (unchanged from r4)

Report class may change; history/credit/restore actions for #332 stay as today (schema/coverage keep reset; excerpt paths keep `evidence_rejected`; etc.). Verifier priority unchanged. Granted-policy changes = separate proposal.

### 4.6 Completeness

Every parseable raw record has a visible destination. `classification_errors[]` are additive, not a hide-sink. Prefer `classification_complete=false` over dropping records.

### 4.7 Consumers

**Share `ReportProjection`:** CLI lists, JSON report payloads, SARIF presentation, presentation-only summary counts.

**Must not treat projection as pass authority:** verify, restore, credit, terminal decision, fixpoint/HOLD/control callers.

Before Phase 4: inventory today’s projection-helper callers; split presentation vs control.

When proof is thin: emit raw + unverified reasons; never claim trusted execution/gate without recovered non-circular support; never replace primary failure with classification error alone.

### 4.8 Compatibility (split decisions)

| Case | Planning default to freeze |
| --- | --- |
| Unknown additive field with no safety meaning | Do **not** quarantine the whole record merely for its presence |
| Unknown failure kind with **valid** host binding | May show as trusted “unspecified host diagnostic” |
| Unsupported binding / authority **version** | No trusted class; explicit reason |
| State unparseable | Keep **existing** corrupt-state handling; do not force-fit quarantine |
| New→old rewrite | Explicit supported/unsupported mix; test host-fact drop |

### 4.9 Observation digest

Capture-time immutable facts only; disposition/evidence evolve under existing rules; report cache/channel/ref must not enter their own hash. Canonicalization field-set is a Phase 1 gate item **when binding/digest fields are in scope**.

If Phase 0 chooses **adapter-only** (no new binding/digest fields), Phase 0 may record a **dated waiver of this gate** in `phase0-decision.md` (approver + reason). That waiver is the architecture-side record; a plan-only note is not sufficient. If supplemental digests are required, canonicalization remains a hard Phase 1 entry gate (not waivable by the plan alone).

---

## 5. Bounded plan scope (first planning package)

Keep the first plan **narrow**:

1. **Freeze baseline** — per failure class: current report, verdict, credit, restore  
2. **Caller audit** — which projection callers are presentation vs control  
3. **Read-only classification adapter** — cover real existing host-failure shapes first  
4. **Shadow validation** — same persisted snapshot: bytes of findings/receipts/history unchanged around classify; for independent runs compare semantic/stable fields only (not timestamps / random IDs); no formal-output fields old consumers misread  
5. **Switch real report consumers together** — CLI, JSON, SARIF, summary in one gated delivery  

**Defer** unless Phase 0 proves need: modifiers persistence, new channel cache fields, full subtype taxonomy, new `host_diagnostics[]` store “for neatness.”

Independent-enforcement acceptance: after omitting the **target** guard, the **corresponding assertion** goes red and recovers green when restored. Do **not** require the whole run to become PASS (redundant guards may still fail).

Migration phases 0→5 from r4 remain; Phase 6 credit policy stays separate. Phase 0 decision gate unchanged: adapter-first.

---

## 6. Acceptance (planning must include)

Carry prior cases; emphasize:

- Classification failure alone does not change terminal verdict  
- Real product candidate retagged INFRA + invalid diagnostic ref → remains visibly an unresolved product candidate; never trusted infra diagnostic; summary not bare zero without qualifier  
- Non-circular validation  
- Classifier read-only (no write-back at report time)  
- Consistent full-store tamper: no claim beyond existing trust model  
- Historical display-quarantine ≠ permanent current blocker  
- Legal disposition/evidence evolution keeps observation binding  
- Non-blocking gate audit ≠ FAIL via channel migration  
- Mutating channel cache alone does not change verify/restore/credit; damaging **safety** proof facts keeps existing independent failure; damaging **report-only** supplements → unverified display / `classification_complete=false`, not a new FAIL policy  

---

## 7. Open decisions for the plan (not new architecture)

1. Phase 0: adapter-only vs minimal supplemental facts?  
2. First-wave host shapes to route beyond capacity?  
3. Unknown kind with valid binding: trusted-unspecified vs quarantine?  
4. Old-code rewrite support range?  
5. Phase 59 vs dedicated hotfix track?

---

## 8. Closing line

Architecture direction **accepted**; **bounded planning allowed**. Implementation and production report switch still require **phase gates**. Prefer “report consumers share classification; safety consumers share existing proofs” over “every consumer reads one object.”
