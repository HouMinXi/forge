# 59-16 current-source implementation-contract addendum

**Status: PROPOSED, ready for independent plan review. Not admitted.**
Date: 2026-10-18. Lead: Secondary dot under the owner's current GSD direction.
Scope: one offline matched entry-level analyser capability. No code, canonical plan, or dated snapshot is changed by this document.

## 1. Decision and bounded result

Implement the existing 59-16 numerical-analysis capability in exactly:
- scripts/analyse_arms.py
- tests/test_analyse_arms.py

Preserve all existing default finding-level behavior. Add an explicitly selected strict entry-level mode that checks two ledgers against one frozen ID set, exposes all exclusions and exact physical input coordinates, and never describes an unbound numerical result as qualified B4 acceptance.

This is independent of 59-15's proposed context/accounting schema. Current ledger fields suffice for entry verdict arithmetic. The upstream 59-02 source/config/label binding and later 59-17 acceptance decision remain required before using a numerical report as historical B4 evidence. No missing original ledger, source identity, label authority, graph consumption, or provider outcome will be reconstructed from appearances.

**Admission distinction requiring explicit disposition:** this addendum proposes a source-independent offline capability slice with fixed synthetic known answers before historical evidence is available. It does not claim 59-02 complete, delete the parent plan's dependency, or qualify a real B4 run. The coordinator must explicitly accept that bounded dependency interpretation during exact-plan admission; if not accepted, implementation remains stopped.

## 2. Source and documentary pins

Fresh public main observed at **618ce83172cef90bee2c4a8188efa1ba57550131**, tree **de30489e869fa4e5ea6084aa1988b71a96604b36**. Source snapshots are exact bytes retrieved by the GitHub connector at that commit. Hashing used only data files; no product import or execution.

| Role | Repository path | Git blob | SHA-256 |
| --- | --- | --- | --- |
| Sole product file | scripts/analyse_arms.py | 6eec4a6a618721326736b86b50b8c5b57030507b | 838185ed5dd7f5c4e13b1fa6b1965a5217b2097f333c901a663e3f6a0f589ad0 |
| Sole test file | tests/test_analyse_arms.py | b62db8096e56c86e896541757eeae0d8a3ea1272 | 3d4be2327417fc722902968968960e0148e8584530c45c39fb6447422c79d80e |
| Read-only input producer | src/code_forge/eval/ledger_jsonl.py | 2ebaeb53507d4e42ce5e3825f8f4ae51f9efedd7 | 51c6ece2e67b368504b99d979f73646d5d6a27f920cb8e2076f6cf03821ddd67 |
| Read-only label boundary | src/code_forge/eval/corpus.py | b25e29b33c03a1abea09875ca85b2c0667fea0f4 | 9064e14df4fc95482b796c73787b71cfceac1ff6e2628d6bd9fe79e5704b3a62 |

The published 59-16 snapshot is SHA-256 **4a42694f23faaf047ab27c72a24636708fa0ab604da79dd41342b870f8ba6ce3** (12,714 bytes); the manifest dates its original to 2026-10-04. It remains untouched.

The subsequently received R2 proposal is SHA-256 **082633222362d531df514bbc5fcb9df4004db8a6acf094e7e360635ed08a101a**. This addendum supplements that response. Its documentary receipt was accepted; canonical application, implementation/runtime admission, and full-plan convergence were explicitly not granted.

- [R2 complete response](https://github.com/HouMinXi/forge/issues/68#issuecomment-6207932549)
- [R2 byte index](https://github.com/HouMinXi/forge/issues/68#issuecomment-6207935462)
- [Coordinator receipt and remaining gate](https://github.com/HouMinXi/forge/issues/68#issuecomment-6208032441)
- [Published plan and archived limits](https://github.com/HouMinXi/forge/blob/618ce83172cef90bee2c4a8188efa1ba57550131/docs/gsd/phases/59-judgment-and-evidence/59-16-PLAN.md)
- [Repair ownership](https://github.com/HouMinXi/forge/issues/43)

All 19 public open PR file lists were inspected. Neither of the two proposed files overlaps. The full 333-comment #43 record yielded no explicit analyser claim. Refresh these observations and record an acknowledged exact-base/file/function claim immediately before any code edit. Absence of a public claim is not proof about unpublished private work.

## 3. Existing field contract and source delta

### Existing producer, unchanged

ResumeKey contains entry_id:str, depth:int, engine:str, backend:str.
make_record(key, verdict, *, runs=0, caught=0, wall_s=0.0, skipped_reason='', rounds=None, findings=None) emits:
- Always: entry_id, depth, engine, backend, verdict, runs, caught, wall_s
- Optional: skipped_reason, rounds, finding_hits, finding_misses, finding_fps

There is no current source/config hash, label manifest, context_summary or audit_refs contract here. Do not add any of those fields or change ResumeKey.

The general corpus's label authority is CorpusEntry.expected_verdict. The historical 59-16 suffix mapping is a bounded fixture/acceptance convention, not a general truth about arbitrary entry names. Applying it to historical evidence requires the separately frozen label manifest.

### Existing analyser, unchanged default branch

Current load() at line24 returns [] for a missing file, skips blank lines, and loses physical line coordinates. summarise() at line56 enumerates the compressed list as if it were file lines. _lines() at line113 abbreviates long references. main() at line154 only handles positional ledgers, checks depth/engine within one file, and returns0 even after skipping empty/mixed inputs.

Do not repair or reinterpret this default behavior in this slice: existing tests explicitly cover its blank-line and missing-file semantics. New physical-line provenance must use a separate strict loader, not change load() or retrofit hidden metadata into legacy rows.

## 4. Proposed interface, frozen for review

Keep existing public helpers unchanged. Add:
- load_entry_rows(path: pathlib.Path) -> tuple[list[dict], tuple[int, ...]]
- entry_level_summary(rows: list[dict], *, expected_ids: tuple[str, ...]) -> dict
- Private ID-list, pair-validation and strict-rendering helpers as needed inside the same script.

load_entry_rows returns parsed objects plus their actual one-based physical LF line numbers, in the same order. It does not mutate the row dictionaries or insert reserved keys. Summary classification references use explicitly named zero-based **row_indices**, not file-line claims. The CLI maps each row index through the returned physical-line tuple before printing any citation. Tests must prove this composition; helper-only arithmetic does not qualify provenance.

New CLI form:
**analyse_arms.py --entry-level --only-ids IDS LEFT_LEDGER RIGHT_LEDGER**

- Exactly two positional ledger paths in the new mode.
- --only-ids is mandatory with --entry-level and rejected without it.
- Existing positional-only/default invocation remains unchanged.
- No provider, config loader, product imports, subprocess launch, recorder, graph, ledger writer or new dependency is introduced into the script.
- No binding-manifest CLI option is added in this slice. Upstream source/config/label evidence is checked separately.
- Stdout is a deterministic human-readable report with explicit counts, categories, full citations, completeness and arithmetic-only status. Tests freeze exact full stdout for small known answers; no new output file is written by the analyser.

## 5. Input and matching contract

### 5.1 ID list

Decode UTF-8 strictly. Split on LF; accept CRLF by removing only the one CR that terminates a CRLF line. A final LF is optional. Ignore only the terminal empty split caused by that final LF; reject interior blank lines, empty identifiers, leading/trailing or embedded whitespace, duplicate identifiers, and identifiers lacking a nonempty prefix followed by exactly -bug or -clean. Preserve spelling/case/order; no normalization or deduplication.

The frozen list must be selected before seeing results. The analyser does not require a fixed numeric size because independent known-answer fixtures are smaller. Original B4 requires the same separately bound selected20-or150 set on both arms, following59-17's frozen three-canary decision: all-zero selects10bug+10clean; any-positive selects the original150. This analyser does not make or rerun that selection decision. A 150-row source may be filtered to20 only when20 is the independently selected set, without claiming the remaining130 entries were eligible or qualified; a selected150 set must require all150 in each arm.

Pre-result selection and the expected ID-file hash are external59-02/17 evidence obligations. With no binding-manifest input, this script cannot detect that an otherwise valid supplied list was replaced after results. It validates each invocation against the list actually supplied, always labels the result provenance-unverified, and never certifies the list's historical authority. A changed list fails external freeze verification when it disagrees with the admitted digest; do not invent an analyser error for provenance it cannot observe.

### 5.2 Ledger bytes and physical coordinates

Read bytes once, decode strict UTF-8, then split on literal LF. The one-based index in that split is the physical line number. Whitespace-only lines may be ignored as records but still increment line numbers. CRLF does not double-count a line; bare CR does not create a new physical line.

Terminal LF is **not required** if the last nonblank line is complete valid JSON. A malformed or torn final row fails; never invoke ledger_jsonl.read_records/load_state or copy their tail-recovery/last-wins semantics. Invalid UTF-8, invalid JSON anywhere, non-object JSON rows, JSON duplicate member names, and nonfinite JSON constants fail explicitly. Preserve input bytes; never trim, repair or append to a ledger.

Every parsed row must have a nonempty string entry_id. Reject duplicate entry_id rows anywhere in an arm, including identical repeats and conflicting/resume outcomes, rather than choosing a winner. This strict mode consumes frozen analysis input, not an active resume log. A larger ledger is permitted, but all of its raw records must be syntactically unambiguous. Unselected rows are listed as outside_requested_ids with their physical citations; their outcomes do not enter metrics.

### 5.3 Selected row identities

Each requested ID must occur exactly once in each arm. Missing IDs, duplicate IDs, and malformed IDs are fatal mismatches. For every selected row:
- depth must be integer1, not bool
- engine must be the exact string real
- backend must be a nonempty string
- all selected rows in both arms must have the same backend
- IDs must match the frozen list; -bug/-clean provides the bounded label mapping

The analyser validates row-level backend identity only. A reused backend alias can hide different model/config settings; equality of its text does not prove configuration equality. External59-02/17 binding must resolve that.

Extra selected-row data fields never confer authority. runs, caught and optional finding_* values do not override verdict-based entry scoring. Unselected rows' experiment coordinates/outcomes are not silently used as proof for selected rows; the report names them excluded.

### 5.4 Outcome and exit classification

For selected rows:
- HOLD/PASS are scored.
- SKIPPED/ESCALATED are explicit incomplete exclusions.
- Missing, null, or an unknown string verdict is an explicit unknown_verdict exclusion.
- Any other verdict type is malformed schema, not a legitimate outcome.

Exit contract:
- **2: INVALID_INPUT.** CLI misuse, unreadable/missing/empty source, invalid bytes/JSON/schema, duplicate IDs, ID-set mismatch, unknown suffix label, or selected coordinate/backend mismatch. Emit pointed stderr diagnostics with available path/physical-line/ID. No partial numerical report or paired delta may have been printed before whole-pair validation completes.
- **1: INCOMPLETE.** Both structures/identities validate, but at least one selected outcome is excluded. Always print both per-arm intended/scored/excluded totals and explicitly diagnostic metrics on their own scored populations, using n/a when a denominator is undefined. Print no paired metric deltas or success language. Excluded entries never become TN, zero findings or successful reviewed entries.
- **0: ARITHMETIC_COMPLETE.** Both arms have the full selected set scored as HOLD/PASS. Print matched numerical results/deltas and all raw citations. Always print **provenance=unverified_by_analyser; B4_acceptance=not_established**. Exit0 establishes only completed arithmetic on supplied bytes.

All intended/excluded counts remain visible; no successful comparison can shrink its corpus by dropping failed outcomes. Unequal incomplete scored populations never produce a paired delta.

## 6. Arithmetic and output contract

For each selected scored entry:
- -bug + HOLD = TP
- -bug + PASS = FN
- -clean + HOLD = FP
- -clean + PASS = TN

Print intended_n, selected_n, scored_n, scored_bug_n, scored_clean_n, excluded_n, TP, FN, FP, TN and each exclusion category. Include every selected and unselected record's full supplied path plus physical LF line number, labeled ID, original outcome, and category. Aggregate metric citations cover all participating rows without a six-line cap or hidden-tail shorthand.

Quote every input-derived path, ID, outcome, backend and error-detail string in both stdout and stderr using JSON string representation with ensure_ascii=True. Missing and null values use distinct fixed markers. Control fields/status lines are generated only by the analyser, never copied from raw input text. A citation renders the full JSON-quoted path followed by its integer physical line, so embedded newline/tab/control/Unicode characters remain escaped data on one report line. Do not print an unescaped operating-system exception message that reintroduces an input filename. These formatting requirements preserve original values reversibly without permitting an unknown verdict or filename to inject apparent success/provenance fields.

Precision is TP/(TP+FP); recall is TP/(TP+FN). A zero base denominator is n/a, never an invented0. F1 is n/a if either precision or recall is undefined. When both are defined:
- If both are0, F1=0.
- Otherwise F1=2PR/(P+R).

This deliberate new-mode zero-TP rule is frozen here; legacy summarise() keeps its existing behavior. Store exact numerator/denominator counts for known-answer assertions and display them with metrics. No metric standard error is inferred from distinct entries; with one run per entry/no within-entry replicates print SE=n/a. wall_s is not used by this initial new mode, avoiding a separate timing schema or precision claim. Legacy timing output remains unchanged.

Render paired deltas only after both complete arms match. n/a inputs yield n/a deltas. Keep labelled counts and ratios separate from quality superiority or a phase-completion verdict.

## 7. R2 clauses retained verbatim

The following is the unchanged R2 acceptance clause. Its evidence obligations remain in force; this addendum does not convert them into current implementation claims.

> Matched entry-level CLI gives correct metrics and raw references; mismatch/error cannot be a successful B4 comparison; source binding stays independently required. Evidence qualification: retain and cite completed sibling attempt response/usage/error evidence independently of containing-round completion; an incomplete/timeout parent remains explicitly incomplete and cannot supply zero findings, a successful B4 comparison or native clean credit. In bounded offline known-answer analysis, use independent synthetic data representing surviving sibling attempt refs and a separately preserved parent-termination outcome; verify raw citations stay addressable without treating attempt completion as a completed evaluation row. For context-derived interpretation, require separately bound evidence of the actual producer's consumed graph namespace/source identity and producer result; distinguish a matching consumed index from canonical-only metadata with a missing linked-worktree index and explicitly unavailable enrichment. Absent binding remains unknown/unavailable and cannot establish consumed context or a qualified context-derived claim. These checks consume retained upstream evidence, do not infer provenance from aggregates, do not change legacy mode or the shared schema, and authorize no graph build or provider/recorder implementation.

The unchanged R2/D14 review paragraph is:

> Keep author and reviewer separate. Parent arranges three independent consecutive clean whole-diff Forge reviews using authorized Sol/Luna; use authorized Ultra fallback only after recording actual Forge unavailability. Per locked D14, this later source batch must exercise actual public Forge review with the parent's CLI/CI/MCP rotation and source/runtime/config pins. Retain setup outcome, observable/paid attempt count, process completion, code verdict and required gate qualification separately. Failed, unavailable or unconverged native courses remain failed/unqualified and earn no native clean credit. Authorized fallback opinions are separately labeled and never relabeled native convergence. Retain exact diff/input hashes, raw reviews, dispositions and smoke results. Any new logic edit resets clean credit. Do not commit per task or after RED: retain uncommitted evidence, then ONE signed product commit/MR for this admitted slice after review. Planning/evaluation records stay local and unstaged.

Operational boundary for this consumer-only slice:
- Synthetic completed-sibling artifacts and a separate timed-out parent record are qualification inputs retained outside the shared ledger schema. The analyser must remain incomplete when the parent row is SKIPPED/unknown. The qualification evidence index cites the surviving sibling artifacts and verifies they are unchanged/addressable; the analyser does not manufacture their refs or implement capture.
- Positive consumed-graph evidence and canonical-only/missing-index evidence are separate external qualification records. Both numerical outputs remain provenance-unverified by this analyser. Only the actual-producer record may support a subsequent context-derived interpretation. No graph query/build or producer implementation is hidden in the two-file scope.
- These external checks cannot be credited as newly executed product lines. If full current acceptance requires a new executable evidence-consumer interface, stop and seek a separate exact interface disposition instead of expanding the script/schema silently.

## 8. Three bounded tasks after admission

### Task1: Freeze behavior and prove baseline RED

Files: tests/test_analyse_arms.py only. Read both actual scoped files and read-only producer contracts. Turn the companion frozen table into independent explicit expected counts, stdout/stderr/exit and physical-line assertions. Preserve all existing tests. Demonstrate missing strict behavior at a real subprocess/public CLI or assertion boundary; absent imports and collection failures do not count as RED. Any temporary collection scaffold must be included in the reviewed exact diff and grant no behavior credit.

### Task2: Implement strict two-arm path

Files: scripts/analyse_arms.py and tests/test_analyse_arms.py only. Add the interfaces above and a new branch in main; leave existing helpers/default branch behavior unchanged. Validate both input structures before producing metric output. Use only standard library facilities already supported. Execute all controls, including actual script subprocesses, strict physical-line composition and unchanged default tests. No product module, shared schema or other test file is changed.

### Task3: Qualify exact composition and hand off

Files: same two files only if a confirmed defect requires correction. In a separately admitted owned runtime, retain exact-source focused tests, subprocess smoke, each independent changed-site omission RED and restored GREEN, changed-logic execution coverage, and separate independent reviews. New logic resets clean review credit. Parent owns required actual Forge route rotation, exact-head CI, authorized publication/integration and dual-host adoption/hygiene. No third task creates a new permission or relaxes a failed gate.

Future candidate verification anchor, **not executed by this packet**:
PYTHONPATH=src /usr/bin/python3 -m pytest -q tests/test_analyse_arms.py

## 9. Threat and control map

- Evidence cherry-picking: fixed same IDs, reject duplicates/collisions, no incomplete paired deltas. Controls C01–C11.
- Citation spoofing through blank-line compression: strict LF map and complete references, actual CLI composition. C12–C14.
- Schema/coercion ambiguity: strict UTF-8/JSON/objects/member identity/types; no bool depth or unknown outcome success. C03–C10.
- False arithmetic certainty: explicit denominator counts, zero-vs-unknown and n=1 SE rules. C01,C02,C15–C18.
- Unjustified provenance/native credit: fixed unqualified output; retained R2 separate evidence and review identities. C19–C21.
- Unbounded scope/ownership: exactly two files, no imports/dependencies/provider/graph/schema writes; renewed claim and exact-source freeze.

## 10. STOP criteria and gate handoff

Stop the affected source task if:
- Main/scoped preimages or reviewed contract changes; rebind and review the delta rather than applying stale postimages.
- A new overlapping owner/PR exists or the exact claim is not acknowledged.
- Typed checker/PBR/independent plan convergence or explicit narrow source disposition is missing.
- The coordinator does not accept offline capability qualification before historical59-02 restoration.
- A requirement needs configuration/source/label identity not in current rows, actual graph consumption, provider/recorder changes, shared-schema changes, or a third product file.
- Any strict input is malformed/mismatched; no fallback to tolerant legacy parsing or latest-resume selection.
- Independent/runtime checks reveal a failure; preserve original output, correct within scope, reset affected review credit.
- No admitted interpreter/source/import/graph or execution budget is available. Source planning remains allowed; runtime stays unrun.
- Required native review is unavailable, fails or times out; retain that truth and label any actually authorized fallback separately.

Gate handoff must contain:
1. This exact addendum, companion control table, manifest and byte hashes; unchanged received R2 digest and public receipt links.
2. Exact current main/tree and the two product/test preimages plus read-only producer pins.
3. Refreshed open-PR/file and #43 ownership evidence, named publisher/integration owner and recorded exact-scope acknowledgment.
4. Explicit disposition of the bounded59-02 dependency interpretation, arithmetic-only output, strict exit/cardinality rules, and F1 zero rule.
5. Actual typed-checker report, complete PBR, independent full-plan convergence dispositions, including unresolved findings. Do not substitute documentary receipt.
6. For later runtime: selected owned source checkout, actual interpreter/import/rules/source-bound graph evidence and budget; these are absent in this planning packet.
7. For actual B4 use: external frozen IDs/labels and the three-canary selected20-or150 disposition, source/config/backend identity, actual original or explicitly authorized reconstruction ledgers, and their hashes. Unknown fields stay unknown; no threshold or original18/20 obligation changes.

## 11. Current completion claim

Only the source/planning addendum and independent known-answer specification are delivered. Every product test, baseline RED, candidate GREEN, omission/restoration, runtime import, native review, CI, publication, adoption and phase qualification remains **NOT RUN / NOT ESTABLISHED**. Formal3/5, Phase59 active, Phase60 pending and all11 active requirements pending remain unchanged.

