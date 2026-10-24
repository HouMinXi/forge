# PBR complete recheck 2: 59-16 and the 59-17 boundary

Date: 2026-10-19 UTC. **Source-only result: B0 / H0 / M0 / L0.** All eight core passes and applicable additional Pass 9 were repeated against the complete exact successor. All four previously identified issues are resolved; no further scoped finding was identified.

**Documentary self-review is clean. Full plan-review qualification and implementation admission remain HOLD.** This is not an external-model convergence round, a designated-checkout source-graph receipt, a native Forge result, an SDK run or runtime evidence.

## Exact input and scope

Reviewed core: the 19-file successor in repository-relative directory `docs/gsd/proposals/2026-10-19-offline-analyser/`. It was not yet published when reviewed.

| Binding | Exact identity |
| --- | --- |
| Core MANIFEST.json SHA-256 | `5fcd84bffa7a80a7440eaedd74e63e27c996f07feb660958d71f6495934a5672` |
| Core inventory | 19 files, 232,857 bytes; all 18 listed payload hashes and sizes verified |
| 59-16-PLAN-CANDIDATE.md SHA-256 | `8a27233fe529b2c8d0aae8ec4913c870f04c0f79f0b50a779ebeb6edf7ad3ad6` |
| PATCH-DESIGN.md SHA-256 | `5301aab0ffe172fd7d5be3f4f7cb17a0b619fc096eb9fabdf596dc36faa30167` |
| 59-VALIDATION-CANDIDATE.md SHA-256 | `26222e7798f434d11f9c226746b7704b962b3af24dd65b13336695c2f52f99bd` |
| PBR-FINDING-DISPOSITIONS.md SHA-256 | `9985e9957f9b9fa11b0c9be9085a3b54fe609e5b2e80ce6ba597e8333c77fee9` |
| Supplied methodology SHA-256 | `9c9fa4ed73f259696603ea48f7d41a2adbea6bd65b908a679b29cbaf1e07e793` |
| Current source observation | `0ec35ee3c1c1fd8a24f460672ad19233a5265e4f` |

The review covered the entire three-task 59-16 candidate, source contract, frozen controls, detailed design, validation, research and precedence/disposition overlays, current finding dispositions and packaging consistency. The full 59-17 plan was reconsidered for consumption/dependency interfaces; relevant 59-02/59-15 boundaries and all 18 phase-plan frontmatters were checked. Other plans' full bodies were not independently reviewed.

The private supplied method was used without republishing its text. Its four actual mechanical passes and four reasoning perspectives were applied, together with its additional empirical pass. Descriptive pass-count inconsistencies did not cause any named pass to be skipped. Existing unchanged material was verified byte-identical and reconsidered within each complete perspective; this was not just a spot check of the new recovery paragraph.

### Immutable review history

1. Baseline: [PR261 head a62cdfa30432361ff3603abd74c7d5506b4447d4](https://github.com/HouMinXi/forge/tree/a62cdfa30432361ff3603abd74c7d5506b4447d4/docs/gsd/proposals/2026-10-19-offline-analyser), manifest `245540e29c19371920114718e798bfc2237e3ce5c7082b11a3444d9966ac00da`; result B0/H0/M1/L2. Report SHA-256 `6922d8115cd28a49aa624e580933be2cf9341fe7baf938567fccb4f77930c6c0`.
2. First successor: manifest `5a60cd280a6828d504a0b589dc09c7a5b400e8dc42141e138aeba715ae939095`, 19 files/227,215 bytes; complete rerun result B0/H0/M1/L0. It closed the first three findings and identified the pre-existing M02 omission. Report SHA-256 `961ff2c181bbc6f4879a75c8cae6f928d368addb5f52830668f6200f1fad3b4c`.
3. This second successor: the exact core identified above; complete source-only rerun B0/H0/M0/L0. Neither earlier finding-bearing review is retrospectively counted clean.

## Verified finding dispositions

| Finding | Prior severity / Travassos type | Result and exact evidence |
| --- | --- | --- |
| PBR-M01: equivalent zero-F1 deletion | MEDIUM / Ambiguity, Omission | FIXED. PATCH:157-159,276,283; candidate:114-115,132; validation:38,48; finding dispositions:17-27. Defined F1 may use the branch-free exact count formula. C16 must catch a separately bound non-equivalent zero-to-undefined fault. Equivalent redundant-branch deletions receive explicit algebraic/source disposition and no RED credit. Other denominator/undefined witnesses, execution coverage and exact restoration remain mandatory. |
| PBR-L01: raw/rendered LF ambiguity | LOW / Ambiguity | FIXED. PATCH:85 has an inline-code expression containing one raw backslash for the LF delimiter. Enumeration still precedes blank filtering. Raw and rendered forms now agree; the withdrawn preliminary HIGH is not reinstated. |
| PBR-L02: public-helper export inventory | LOW / Omission | FIXED. Candidate:18-20 exports both new public helpers; their signatures at candidate:64-65 and PATCH:41-42 remain consistent. Existing helpers/default behavior are preserved. |
| PBR-M02: uncommitted-candidate mutation recovery | MEDIUM / Omission | FIXED. Candidate:115, PATCH:283, validation:48 and finding dispositions:41-49 require verified named backups before each cut; target-to-backup/hash records; no mutation after failed backup verification; restoration from backups only after the owned bounded witness stops; a ban on git restore and HEAD/index recovery; restored-hash verification before positive tests or continuation; retained evidence and explicit stop/recovery after interruption. Only owned target files may be restored; foreign work is preserved. |

The M02 protocol distinguishes ordinary `finally` cleanup from interruption that prevents it running. It does not claim atomic recovery, resurrect candidate bytes from a hash alone, introduce a temporary source commit or authorize mutation now. Its qualification artifacts fit the existing evidence namespace and do not add a product interface/file.

The frozen C01-C24 expected values, source addendum, received R2 response, earlier checker reports and research delta remain unchanged. The N07 override changes only the mutation mechanism; it does not excuse missing arithmetic assertions or coverage. Historical report/status statements are explicitly dated inputs, not current gate claims.

## Complete pass matrix

| Pass | Findings | Repeated checks |
| --- | ---: | --- |
| P1 Symbol closure | 0 | Existing seven analyser functions, proposed public/private helpers, test aliases/fixture helpers and standard-library references resolve. Read-only producer/label contracts match source. No phantom binding flag, hidden product import, new source module or interface dependency was added. |
| P2 Null propagation | 0 | Nonobject/null raw records reject before access. IDs and selected coordinates have type/value guards. Missing/null verdicts remain explicit unknown exclusions, not TN. Optional finding/timing metadata cannot enter strict arithmetic. Undefined metric/delta rendering is guarded; zero remains distinct from undefined. Structured error absence and input strings remain distinguishable. |
| P5 Interface symmetry | 0 | Path → rows plus equal-length physical-line tuple → zero-based summary indices → whole-pair validation → renderer composition → one CLI output/exit was retraced. Public exports/signatures agree. 59-17 consumes the qualified capability plus separate historical binding, not a new analyser provenance API. |
| P6 Metadata currency | 0 | Five truths, two artifacts/exports, key links, three typed tasks/verification anchors, threat/control maps, acceptance criteria and dependencies match the action text. Backup and equivalent-mutation dispositions are consistent across operative documents. Earlier status/source observations remain historical. |
| Implementer | 0 | The complete ID/UTF-8/JSON/selection/classification/rational-arithmetic/rendering flow was walked, including empty/error cases, duplicate members/IDs, whole-tail validation, physical coordinates, quoted diagnostics and strict/legacy option separation. Recovery now specifies preserved bytes, ownership, ordering and failure/interruption behavior without altering arithmetic. |
| Tester | 0 | All C01-C24 families and listed variants retain independent oracles and composition witnesses. Invalid exit2/empty stdout, incomplete exit1/no deltas and complete exit0/unverified states remain distinct. Boundaries, unknown values, empty inputs and denominators were rechecked. N07's new non-equivalent witness remains observable; exact-site bindings and actual execution counts are still future evidence. |
| Operator | 0 | Repeated runs are read-only/deterministic; changed inputs revalidate. No analyser output-file write, append, provider or cache side effect exists. Strict flags/cardinality and escaped OS/CLI errors are specified. The new backup/restore sequence fails closed, waits for the owned witness to stop, and retains recoverable candidate bytes; dirty/foreign work and source/runtime changes remain protected by entrance stops. |
| Integrator | 0 | 59-16 remains a wave1 synthetic capability root; 59-17 stays wave8 with 02+15+16. No reverse edge, new 16B plan or hidden 59-15 field dependency. Exact frozen IDs, producer fields, label/source/config authority, exit semantics and historical consumer responsibilities remain aligned. |
| Additional Pass 9 | 0 | Blind synthetic oracles, denominator integrity, missing/undefined formatting, no spurious SE, non-equivalent mutation witnesses, safe recovery and source freshness were checked. Historical failures and pre-result canary/corpus gates remain intact. No sampling, voting/Best-of-N, provider loop, corpus patch reversal, schema migration or module extraction is implemented here; those specialized checks are inapplicable, not tested successes. |

Independent rational checks again reproduce C01 left/right 3/4 and 1/2 with −1/4 deltas; C11 left 3/4,3/5,2/3 and right 5/6,1/2,5/8 with deltas 1/12,−1/10,−1/24; C22 4/5 versus 3/5 with −1/5 deltas. C15's three undefined-denominator cases, C16's defined zero and C17's one are unchanged. These calculations are reviewer data checks, not product tests.

Incomplete metrics are explicitly conditional diagnostics on scored rows, with intended/scored/excluded counts retained and no paired result or acceptance. They do not claim an intention-to-treat benchmark estimate. C19 preserves sibling fixture evidence without parent-completion credit; C20 preserves actual-consumed versus canonical-only evidence distinctions; C23 leaves frozen-digest authority external. Original ≥18/20, retained 12/20 and 13/20 failures, actual three-canary pre-result 20-or-150 selection, B3/B4 and P59-CLOSE remain unchanged.

## Source currency and integrity

A fresh read-only comparison around 13:39 UTC confirmed `main` identical to `0ec35ee3c1c1fd8a24f460672ad19233a5265e4f` (zero commits ahead/behind). The four immutable source blobs match the packet; full retained inert payloads were rehashed and statically parsed:

| Source | SHA-256 |
| --- | --- |
| scripts/analyse_arms.py | `838185ed5dd7f5c4e13b1fa6b1965a5217b2097f333c901a663e3f6a0f589ad0` |
| tests/test_analyse_arms.py | `3d4be2327417fc722902968968960e0148e8584530c45c39fb6447422c79d80e` |
| src/code_forge/eval/ledger_jsonl.py | `51c6ece2e67b368504b99d979f73646d5d6a27f920cb8e2076f6cf03821ddd67` |
| src/code_forge/eval/corpus.py | `9064e14df4fc95482b796c73787b71cfceac1ff6e2628d6bd9fe79e5704b3a62` |

Fresh metadata for 59-02/15/17 returned unchanged whole-file git blobs `18d789bdc2f4b6a0524b3e039beb127f09af30dd`, `deedee6759b5fe617f332f8680b9ce02f31716fd` and `7291c18d7039ddf6af94dd9ae41d1e87431668f6`. The complete 59-17 reference SHA-256 remains `2a032fb8bb4ed8b42e052ac51cbfcce37e3f2c9e44545e048a0b34fc528c5d8c`.

The 18-plan document dependency calculation was repeated with only candidate16 overlaid: acyclic, each dependency in an earlier wave, no declared plan16 file overlap. This is not a source-graph qualification. Packaging change-record hashes also match their current files, and the proposal contains no executor-local private paths. The old open-PR/ownership observation was not renewed.

## Remaining conditions and packaging boundary

- **Completed:** the bounded complete source-only review round, all named core passes and applicable empirical checks; zero remaining scoped findings on the exact core manifest above.
- **Unfulfilled:** the method's designated existing indexed-checkout source-graph check and required independent cross-AI convergence. This same-family recheck earns no external-model round credit. Two required independent qualifying rounds have not been established.
- **Unestablished:** operative exact-plan admission, current ownership ACK, owned interpreter/import/rules/graph/runtime budget, actual baseline RED/GREEN, mutations/coverage/subprocess tests, native Forge review, installed SDK/checker equivalence, CI, signed delivery, adoption/hygiene and historical acceptance.

Only inert document/source reads, hashes, static AST/metadata checks, reviewer-authored arithmetic and read-only GitHub retrieval were performed. No embedded product command, product import/test collection, installer, graph CLI, external-model call or product mutation was run.

The core's pending-recheck statements accurately describe its state when frozen; this additive report records the later result. Adding this report and updating status/provenance metadata produces a different package. Retain this reviewed core identity and separately bind/check that packaging delta; any substantive plan/design/control change requires a new review. A zero-finding source-only round does not complete the overall methodology or grant implementation permission.
