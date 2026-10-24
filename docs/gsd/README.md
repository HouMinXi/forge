# Forge GSD plans for cloud repair

This is a sanitized planning snapshot published on **2026-10-18**. The
canonical STATE.md was last updated **2026-10-16**; individual documents
retain older source dates. Publication was prepared against public main
`9db1877d2cdb8e9814cd0956150e0d316eeafae7`. Main has advanced beyond the
historical source bindings in these plans. This snapshot provides planning
access, not current engineering acceptance, design approval or permission
to execute historical commands.

The formal v3.0 state remains **3/6 phases complete (50%)**, **Phase59 active**,
**Phase60 pending**, **12 of 15 active requirements pending** and **3 complete** (LENS-01, LENS-02 and LENS-04, delivered by plan 59.1-01 in PR #284, 2026-10-24; Phase 59.1 is 1/2 plans, with LENS-03 waiting on the EVAL60-01 corpus freeze). The other 11 pending requirements belong to Phases 59/60. Phases
56/57/58 are historically complete; 50/53b remain deferred. Formal plan
counts are unreconciled. There are **18 existing Phase59 plans** here;
their presence is not evidence that they were admitted, implemented or
accepted. Phase60 has preparation documents only, with no formal phase plan.

## Start from current source

1. Clone the public repository, or fetch current main in an existing clone:

   ```sh
   git clone https://github.com/HouMinXi/forge.git
   cd forge
   git fetch origin main
   git rev-parse origin/main
   ```

2. Read [the dated state extraction](STATE-2026-10-18.md),
   [active requirements and traceability](REQUIREMENTS.md),
   [the roadmap](ROADMAP.md), and [the project charter](PROJECT.md).
   Use current main and the repository's current constraints when inspecting
   code; source line numbers and old commit bindings in this bundle may be stale.
3. Read [#43: repair coordination](https://github.com/HouMinXi/forge/issues/43)
   before selecting a repair, then the relevant plan and its supporting
   documents. [#33: documented capability limits](https://github.com/HouMinXi/forge/issues/33),
   [#68: raw-attempt work](https://github.com/HouMinXi/forge/issues/68), and
   [#85: native design](https://github.com/HouMinXi/forge/issues/85) provide
   the public coordination threads. In particular, the
   [revised #85 supplement from 2026-10-17](https://github.com/HouMinXi/forge/issues/85#issuecomment-6230111818)
   is proposed and its actual checker is pending. The source's older
   awaiting-revision statement remains historical. None of these links
   automatically grants implementation or phase-completion credit.
4. Rebind the chosen repair to the exact current source, relevant callers,
   requirements and missing evidence before implementation. Use an owned
   feature worktree, preserve existing work, and follow the current review
   and delivery gates. Stop the affected work if a historical claim conflicts
   with current source or if required evidence is unavailable. Request a
   bounded sanitized evidence export through the coordination issue when
   needed; do not invent it or treat an omitted file as a passing result.

## Complete document index

The bundle has 37 files: this index, [MANIFEST.json](MANIFEST.json), and
35 source-backed documents. The manifest binds every source document to its
original SHA-256 and its actual published-byte SHA-256. The STATE record
binds the full original source even though only a formal extraction is public.

| Core document | Role |
| --- | --- |
| [PROJECT.md](PROJECT.md) | Full charter and historical project decisions |
| [REQUIREMENTS.md](REQUIREMENTS.md) | Full requirements; all 15 active entries remain pending |
| [ROADMAP.md](ROADMAP.md) | Full roadmap and preserved phase boundaries |
| [STATE-2026-10-18.md](STATE-2026-10-18.md) | Dated extraction, source last_updated 2026-10-16 |

### Phase59 plans

| Plan | Existing objective |
| --- | --- |
| [59-01-PLAN.md](phases/59-judgment-and-evidence/59-01-PLAN.md) | Retain exact terminal rejected raw through the existing attempted-artifact writer, independently of legacy rejected-evidence authority. |
| [59-02-PLAN.md](phases/59-judgment-and-evidence/59-02-PLAN.md) | Bind recovered historical calibration and restore acceptance inputs without inventing a pass. |
| [59-03-PLAN.md](phases/59-judgment-and-evidence/59-03-PLAN.md) | Reconcile the existing standalone recorder and owner against the bound source as a documentary review unit. |
| [59-04-PLAN.md](phases/59-judgment-and-evidence/59-04-PLAN.md) | Freeze run lifetime, capability authority and one atomic finite budget pool. |
| [59-05-PLAN.md](phases/59-judgment-and-evidence/59-05-PLAN.md) | Record and budget every legacy API/CLI acquisition and recovery send. |
| [59-06-PLAN.md](phases/59-judgment-and-evidence/59-06-PLAN.md) | Propagate one owner through ordinary/grouped L1, falsification and cross-repository review. |
| [59-07-PLAN.md](phases/59-judgment-and-evidence/59-07-PLAN.md) | Include preparation and advisory inference in review accounting and budgets. |
| [59-08-PLAN.md](phases/59-judgment-and-evidence/59-08-PLAN.md) | Own the complete public review lifetime and expose truthful incomplete/statistics state. |
| [59-09-PLAN.md](phases/59-judgment-and-evidence/59-09-PLAN.md) | Capture a source-bound immutable byte map and serve bounded source ranges. |
| [59-10-PLAN.md](phases/59-judgment-and-evidence/59-10-PLAN.md) | Query qualified source-bound static call relationships from a private coherent graph. |
| [59-11-PLAN.md](phases/59-judgment-and-evidence/59-11-PLAN.md) | Implement and qualify the useful fixed review-envelope replay and Linux supervisor. |
| [59-12-PLAN.md](phases/59-judgment-and-evidence/59-12-PLAN.md) | Implement the one nonstreaming Chat tool controller over qualified broker services. |
| [59-13-PLAN.md](phases/59-judgment-and-evidence/59-13-PLAN.md) | Enable the qualified profile through real ordinary/grouped public CLI review. |
| [59-14-PLAN.md](phases/59-judgment-and-evidence/59-14-PLAN.md) | Expose the qualified review profile and truthful lifecycle results through MCP jobs. |
| [59-15-PLAN.md](phases/59-judgment-and-evidence/59-15-PLAN.md) | Carry measured historical context accounting through subprocess evaluation and live ledger rows. |
| [59-16-PLAN.md](phases/59-judgment-and-evidence/59-16-PLAN.md) | Implement matched entry-level analysis with exact raw ledger references. |
| [59-17-PLAN.md](phases/59-judgment-and-evidence/59-17-PLAN.md) | Restore or explicitly reconstruct original calibration/B3/B4 acceptance with frozen evidence. |
| [59-18-PLAN.md](phases/59-judgment-and-evidence/59-18-PLAN.md) | Verify the full source-bound capability, acceptance and dual-host delivery without inferred credit. |

### Phase59 support

| Document | Role |
| --- | --- |
| [59-01-SUMMARY.md](phases/59-judgment-and-evidence/59-01-SUMMARY.md) | Historical narrow raw-retention delivery summary |
| [59-03-CONTRACT.md](phases/59-judgment-and-evidence/59-03-CONTRACT.md) | Prospective recorder contract; unadmitted |
| [59-03-SOURCE-ADDENDUM.md](phases/59-judgment-and-evidence/59-03-SOURCE-ADDENDUM.md) | Source and durability literature addendum |
| [59-CONTEXT.md](phases/59-judgment-and-evidence/59-CONTEXT.md) | Continuation decisions and deferred scope |
| [59-EXECUTION-PROGRESS.md](phases/59-judgment-and-evidence/59-EXECUTION-PROGRESS.md) | Dated execution history; evidence archives omitted |
| [59-PATTERNS.md](phases/59-judgment-and-evidence/59-PATTERNS.md) | Historical source pattern map |
| [59-RESEARCH.md](phases/59-judgment-and-evidence/59-RESEARCH.md) | Existing bounded-investigation research |
| [59-SOURCE-AUDIT.md](phases/59-judgment-and-evidence/59-SOURCE-AUDIT.md) | Historical source-admission audit |
| [59-VALIDATION.md](phases/59-judgment-and-evidence/59-VALIDATION.md) | Draft validation strategy and limits |

### Phase59.1 plans

Phase 59.1 is a decimal insert admitted after the 59 plans above; it does not
renumber Phase 60. Publishing a plan is not itself an execution admission.

| Plan | Objective |
| --- | --- |
| [59.1-01-PLAN.md](phases/59.1-review-lenses/59.1-01-PLAN.md) | Add the opt-in, separately trusted review-lens block to the L1 review prompts and keep every off-path prompt byte-identical to 609aa51. Delivered 2026-10-24 (PR #284, merge commit 314c7d9). |

| Support | Record |
| --- | --- |
| [59.1-01-SUMMARY.md](phases/59.1-review-lenses/59.1-01-SUMMARY.md) | Delivery evidence for the opt-in review lenses: oracles, injection ledger, suite counts, build listing, rollback rehearsal. |

### Phase60 preparation

Both documents are explicitly unaccepted preparation. The diagnostic
inventory does not qualify a full corpus, authorize source acquisition,
admit a paid comparison or complete EVAL60-01/EVAL60-02.

- [PLAN.md](phases/60-full-checkout-corpus-and-comparison/preparation-20261010/PLAN.md)
- [PREPARATION-PROPOSAL.md](phases/60-full-checkout-corpus-and-comparison/preparation-20261010/PREPARATION-PROPOSAL.md)

## Redactions, provenance and unavailable evidence

Full plan text, decisions, signatures, numeric budgets, task definitions,
requirements and historical acceptance/failure limits are retained. Only
private user/check-out prefixes and machine aliases
are replaced with stable labels. `PRIVATE_HOST_A` and `PRIVATE_HOST_B`
preserve the distinction between the two hosts. The public provider aliases
`agnes-intl`, `agnes-intl-2`, `agnes-intl-3`, `agnes-cn`, `agnes-cn-2`, and
`agnes-cn-3` are retained exactly; they are not private endpoint addresses.
`[PRIVATE_HOME]`,
`[PRIVATE_GSD_INSTALL]`, `[PRIVATE_CANONICAL_CHECKOUT]`,
`[PRIVATE_CONTROLLER_CHECKOUT]` and `[PRIVATE_OTHER_CHECKOUT]` are redactions,
not usable filesystem paths. Generic product paths such as
`~/.config/code-forge/config.yaml`, fixture paths, API parameter names and
security requirements are technical descriptions, not published credentials.
Per-document transformation categories and occurrence counts are in the manifest.
Source typography is retained; newly authored publication prose is ASCII.

Historical checklist statuses are displayed as literal code markers, such
as `[ ]` and `[x]`, because these are archived source statuses rather than
newly adjudicated task cards. Every marker state and its accompanying text
is preserved. The reversible `archival-status-formatting` transformation
and its occurrence count are recorded per document in the manifest.
Excess blank lines at the end of a source-backed published file are removed
under `source-final-newline-normalization`; the original source is unchanged.

Included GSD context references have public relative links. Source artifact
paths, output destinations and historical commands otherwise retain their
documentary meaning; they are **not cloud-ready commands**. The local GSD
workflow/template installations referenced by the plans are not bundled.

The following evidence is unavailable in this publication: private research
and review packets, cp-artifacts, raw prompts/provider responses, run logs,
attempt and mutation ledgers, receipts, source exports/checkouts, local
configuration/authentication, and the full STATE runtime-history archive.
Phase60's inventory script, run-02 results and one-pair source exports are
also omitted. A document saying an artifact was retained on both hosts
does not mean that artifact is in this bundle. Historical promises to
publish matching evidence are not fulfilled by this documentation snapshot.

MANIFEST.json inventories unavailable `.planning/` references and unresolved
execution contexts per document. References can name a directory, glob or
brace expression; these remain documentary strings, not completeness claims.
Other bare evidence names (for example run-02/INVENTORY.json or ROOT-READBACK.json)
are unavailable unless explicitly linked in the complete index above.
The manifest also inventories historical full Git/object IDs and short hex
identifiers without asserting that each is a commit, reachable from current
main, or independently verified. An unavailable object or artifact grants
no acceptance credit. Unqualified issue/MR numbers in historical source
may use a private tracker namespace and must not be mapped to GitHub by number.

The extraction and redaction scripts and private verification records are
kept outside this public tree. Source/public hash differences are expected
after redaction, navigation and publication notes. Null means unobserved,
never zero or clean. No source implementation, runtime evidence archive,
configuration or secret material is shipped by this bundle.

## Refresh policy

This snapshot is intentionally dated. A later source delivery does not
silently update these documents or earn phase acceptance. Refresh by
re-reading the canonical sources, preserving their acceptance boundaries,
producing new source/public hashes, and reviewing the exact public diff.
If a refresh cannot be made, mark the snapshot stale and continue to consult
current main and #43 rather than treating historical statements as live status.
