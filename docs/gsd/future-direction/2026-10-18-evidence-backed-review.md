# Candidate direction: evidence-backed review with bounded human attention

Date: 2026-10-18

Status: additive design proposal, not an admitted implementation or policy change.
Baseline: `HouMinXi/forge@618ce83172cef90bee2c4a8188efa1ba57550131`.
Suggested home: `docs/gsd/future-direction/` alongside the dated GSD snapshot.

## Decision and limits

Forge should make review effort better targeted and its conclusions more
reproducible: spend attention on consequential uncertainty, preserve the evidence
behind each finding, and measure whether the process actually improves outcomes.
That is a useful future direction. It is not evidence that Forge can already
reduce approval or clean-cycle requirements.

This proposal adds no phase number or completion credit. Phase59 stays active,
Phase60 pending, the formal denominator stays 3/5, and all 11 active requirements
remain pending. P59-CLOSE retains its original 18/20 calibration gate and B3/B4
evidence obligations. EVAL60-01 still requires the recorded 150-entry real-checkout
corpus; the pilot below cannot substitute for it. The historical snapshot,
MANIFEST, acceptance thresholds and original source documents remain unchanged.

Any future change to gating, approval requirements, clean-cycle semantics,
execution authority or landing behavior needs an explicit, separately reviewed
proposal and user approval. Existing admitted work does not acquire a blanket
new permission gate from this document.

## External evidence, not transferred acceptance

The October 15, 2026 article
[How the Claude Code team does code review](https://claude.dev/blog/solving-code-review-at-anthropic/),
by Addy Osmani, Morgan Westlee Lunt and Qing Wang, reports that Claude Code's
merged PR volume more than doubled from June to
September while its reverted share fell by over 40%. It describes incident-based
reviewer evaluation, evidence checks, risk-sensitive review, convergence controls,
and rapid recovery. These are the team's observations and practices, not a
controlled demonstration of which intervention caused the improvement. Its
approval choices do not establish suitable Forge policy. The useful hypothesis
is that review can consume less human attention without worsening outcomes;
Forge must test that hypothesis on its own work.

## What the baseline actually establishes

This is a comparison of published contracts and documented capability limits,
not a fresh runtime certification.

- [REQUIREMENTS](../REQUIREMENTS.md) and [PROJECT](../PROJECT.md) retain the
  deterministic source, receipt and clean-cycle gate; advisory axes cannot
  silently become blocking checks. Forge delivers patch artifacts and evidence,
  and does not apply fixes to the reviewed tree.
- The recorded foundation includes outcome-ledger export, falsification and
  convergence disclosure, environment manifests and bounded native falsification.
  Historical completion is not current-source qualification of a new workflow.
- [Phase59/60 in ROADMAP](../ROADMAP.md) already own bounded reads and call
  queries, qualified experiments, typed tool loops, finite budgets, retained raw
  failures, per-attempt accounting and the matched corpus comparison. Reuse their
  eventual accepted outputs instead of creating parallel substitutes.
- [README limitations](../../../README.md#honest-limitations) and
  [#33](https://github.com/HouMinXi/forge/issues/33) explicitly leave feedback
  learning, longitudinal maintainability scoring and performance benchmarking
  undelivered. Cross-repository impact is advisory and one-hop, with an empty
  result for unregistered repositories. R3 artifact presence does not prove
  exercised coverage. None is closed by this proposal.
- #33's [digest-control report](https://github.com/HouMinXi/forge/issues/33#issuecomment-6147063663)
  and [checkpoint report](https://github.com/HouMinXi/forge/issues/33#issuecomment-6148056764)
  record narrow control and transport results, with explicit limits. They do not
  establish full campaign, architecture or Phase59 acceptance. Future work must
  bind its own source and inputs.

## Proposed Forge contract

### 1. Risk selects evidence effort, not authority

Start with a versioned, inspectable change description: affected paths and
interfaces, execution/security boundaries, data persistence, dependency edges,
reversibility and missing context. Record each classification's reasons and
source. Unknown dependencies or uncertain impact cannot receive a cheaper
evidence allocation merely because the diff is small.

Initially this description is advisory. It can recommend additional checks or
human attention, but cannot reduce the existing minimum review, block through
an advisory axis, choose a different model from the diff, or alter required human
approvals. Sensitive-path rules and reviewer roles remain external policy.
AI agreement is a discovery signal, never proof or a substitute for a required
human approval.

### 2. An evidence packet links claims to a reproducible subject

A candidate packet records repository identity, base/head/tree, source-snapshot
digest, configuration and tool/prompt versions, intended behavior, environment,
finite budgets and the exact evidence used. Findings retain affected locations,
failure mechanism, evidence references, counterevidence, confidence limits and
the origin of every disposition.

Keep machine-observed results distinct from author statements. Distinguish
verified, refuted, unresolved, unavailable and stale evidence. A dismissed
comment alone is not a false-positive label. A passing fix alone cannot confirm
the original defect; preserve the existing asymmetric falsification contract.
Missing output, exhausted budgets and failed retention cannot be rewritten as a
clean result. Raw-data storage follows the existing retention/redaction boundary.

### 3. Incident feedback becomes an auditable evaluation input

Associate each eligible real incident or revert with a confirmed bug location,
buggy and repaired source identities, a reproducible oracle or explicit evidence
limit, the missed review layer, and an adjudicator. A revert alone does not prove
a defect; product reversals and unrelated reverts are separate dispositions.

Deduplicate related fixes into incident families before splitting development
and held-out cases. Retain real clean controls and harmful-fix outcomes. Preserve
unavailable cases in the inventory and denominator; do not quietly replace them.
Corrections are versioned. No automatic rule suppression, preference learning or
prompt promotion follows from one dismissal. This is a candidate for #33's
feedback-learning design, not its implementation.

### 4. Convergence is bounded, without weakening clean cycles

Identify findings by failure mechanism and affected source, while retaining
distinct instances. Carry unresolved findings forward. A changed location or
new evidence may reopen a prior disposition; deduplication must not hide that.

Evaluate a finite author/reviewer retry budget separately from the existing
clean-cycle requirement. Its exhaustion produces HOLD with the remaining
findings, evidence gaps and a named escalation recipient. It never produces
PASS, counts an incomplete round as clean, or resets history to evade the cap.
Choose any numerical retry cap only after the offline comparison and a reviewed
policy decision; the article's value is not a Forge default.

Incremental re-review must include the new diff, unresolved findings and unchanged
but affected callers, callees, contracts, configuration and tests. Bind the impact
closure to the source/graph snapshot. Missing or stale graph/context triggers
broader review or HOLD, never an empty-impact conclusion. Changed base, dependency
or configuration invalidates affected evidence; a sound reuse rule must be
demonstrated before any review credit is reused.

### 5. Merge composition and outcome ownership remain explicit

Represent both the reviewed head and the actual proposed composition with its
target base. Check results for two isolated heads are not evidence for their
combined tree. A new head, advanced base or manual resolution requires fresh
binding and the applicable checks; unsupported composition remains unverified.
This is an evidence contract for a future integration, not permission to enable
auto-merge or a claim that Forge currently operates a merge queue.

Record the responsible human owner, recovery contact, expected observability,
rollback preconditions and the last verified recovery exercise. Deployment and
rollback stay with the authorized delivery system. Persist failures to detect,
notify or recover as observable outcomes. Never equate a written rollback command
with a proven reversible change; data migrations may require a separate recovery
plan. Forge may report missing ownership or recovery evidence without converting
its current advisory semantics into a new gate.

## Small roadmap candidate: offline packet and replay pilot

Only the following sequence is proposed now. Implementation and execution are
not authorized by publication. Use an owned worktree rebound to the exact
then-current source, with independently reviewed plan and acceptance controls.

### FD-01: Freeze an advisory packet and case inventory

Scope: one schema/design, a documented mapping to current receipts/ledger, and
an inventory of 12 real incident-linked bug/fix pairs. No runtime integration,
paid calls, repository execution, new model routing or approval classifier.
Twelve pairs is a proposed feasibility budget, not an external requirement or
evidence of statistical sufficiency. A clean counterpart means adjudicated clean
for the scoped oracle; it does not claim the entire repaired commit is bug-free.

Dependencies: source rebinding; the existing receipt and ledger contracts;
adjudicated source availability; a named owner for case labels. Missing Phase59
fields are recorded unavailable rather than invented.

Exit criteria:

1. All proposed fields have a producer, provenance, explicit unavailable state
   and consumer; no field claims authority that its producer lacks.
2. Every one of 12 pairs has exact bug/fix identities, an independently checked
   direction and applicability, a real-incident reference and a qualified clean
   counterpart. If fewer qualify, report the shortfall and do not advance.
3. Freeze incident-family splits, metrics, cost treatment, failure denominators
   and review budget before comparison. Freeze the candidate and baseline bytes.
4. Independent documentary review finds no acceptance/policy weakening. This
   closes only FD-01, not any existing GSD requirement or capability limit.

### FD-02: Run a deterministic offline evidence replay

Scope: read only frozen packets and saved outcomes; compare baseline presentation
with the candidate's provenance, disposition, incremental-context and escalation
rules. No new live model call and no execution of arbitrary repository content.
Unavailable historical evidence remains unavailable in both arms.

Dependencies: admitted FD-01; a separately reviewed bounded replay harness and
its own negative controls. This tests accounting/contracts, not model quality.

Exit criteria:

1. Every included record reconstructs the same source/evidence identity or an
   explicit refusal. Replaying identical input twice gives identical normalized
   dispositions and accounting, excluding declared run identifiers/timestamps.
2. All controls reject stale head, stale base/composition, missing evidence,
   budget exhaustion, repeated unresolved findings, and a falsely empty graph.
   An unchanged dependency affected by the diff remains in review scope.
3. Zero controls yield false PASS, erase an unresolved finding or treat AI votes
   as evidence/human approval. All cap-exhaustion controls terminate in HOLD.
4. Report denominators, unavailable cases, duplicate suppression, round counts
   and accounting completeness for every case; preserve original raw artifacts.
   Failure means repair/review the candidate, not lower the acceptance criteria.

### FD-03: Decide whether a matched shadow comparison is worth admitting

This is a later decision gate, not a scheduled run. It depends on the relevant
qualified Phase59 capabilities and EVAL60 corpus/measurement work; those phases
keep their own acceptance. Use the same frozen sources, model configuration,
budgets and failure handling in both arms. Any material difference is explicit.
Approval for actual scope/cost remains separate.

Before running, set the minimum sample size, per-risk strata, confidence method
and acceptable harm margin. Measure confirmed-defect recall, false positives,
harmful proposed fixes, incomplete runs, human handling time, p50/p95 rounds,
observable calls/tokens/time and unknown cost. Model stochasticity needs matched
repeated trials. The 12-pair pilot supports feasibility only, not a quality claim.

Promotion requires no observed regression in any preregistered critical case,
all false-green controls passing, and an adequately powered bound meeting the
predeclared harm margin. If a sample is too small, results are inconclusive.
Lower rounds or human time alone is insufficient. A null or worse result is a
valid outcome. Neither result authorizes a gate or approval change.

## Later work, explicitly outside this pilot

Live merge-composition integration, owner notification and recovery telemetry
need their own source-bound designs and authorized delivery-system adapters.
Define incident/revert rates per eligible deployment, observation windows,
severity and detection/recovery latency before making outcome claims. Missing
telemetry is unknown, not zero incidents. Any future rollout should remain
reversible and retain existing approval requirements until the user explicitly
approves a different policy after reviewing evidence.

Before rollout admission, freeze metric thresholds, observation windows,
telemetry completeness, escalation ownership and authorized recovery actions.
Missing preregistration blocks admission. Stop rollout progression on any false
PASS, source/composition-binding or authority violation, critical harmful fix,
or breach of a frozen harm/telemetry threshold. Escalate to the owner and follow
only the approved recovery plan; this proposal grants no rollback authority.

No new maintainability score, benchmark harness, multi-hop graph or R3 coverage
claim is proposed here. Preserve #33's separate completion criteria for each.
The first useful deliverable is a trustworthy packet and falsifiable offline
comparison, not a broader autonomous reviewer or another count of clean votes.

## Source binding

Read on 2026-10-18. Forge links below are pinned to the proposal baseline;
relative links above are navigation within the published planning bundle.

- [GSD index](https://github.com/HouMinXi/forge/blob/618ce83172cef90bee2c4a8188efa1ba57550131/docs/gsd/README.md)
- [Requirements](https://github.com/HouMinXi/forge/blob/618ce83172cef90bee2c4a8188efa1ba57550131/docs/gsd/REQUIREMENTS.md)
- [Roadmap](https://github.com/HouMinXi/forge/blob/618ce83172cef90bee2c4a8188efa1ba57550131/docs/gsd/ROADMAP.md)
- [Project contract](https://github.com/HouMinXi/forge/blob/618ce83172cef90bee2c4a8188efa1ba57550131/docs/gsd/PROJECT.md)
- [Formal dated state](https://github.com/HouMinXi/forge/blob/618ce83172cef90bee2c4a8188efa1ba57550131/docs/gsd/STATE-2026-10-18.md)
- [Documented capability limits](https://github.com/HouMinXi/forge/blob/618ce83172cef90bee2c4a8188efa1ba57550131/README.md#honest-limitations)
- [Capability roadmap #33](https://github.com/HouMinXi/forge/issues/33)

This proposal records a source/article comparison and candidate design only.
Its publication grants no implementation, runtime qualification, evaluation,
merge or deployment acceptance, and does not rewrite historical GSD records.
