# GSD 59-16 strict offline matched analyser: source-level patch design

Status: DESIGN ONLY. No product edits, product imports, test collection, subprocesses, provider calls, installation, runtime review, CI, or acceptance performed. Source copies are unchanged input data. Actual plan admission remains with the parent/coordinator.

Prepared 2026-10-19 UTC against exact published comments:
- https://github.com/HouMinXi/forge/issues/68#issuecomment-6252965603
- https://github.com/HouMinXi/forge/issues/68#issuecomment-6252967554

## 1. Exact source binding and overlap observation

Observed main: e9c78d47f2e1a191e7675168223c7b2bf084aa9c.
Tree: 930ebad4f13a17502f7702670fbb78348dbb8019.
Read via GitHub connector at that immutable commit, then hashed as data.

| Scoped file | Bytes | Git blob | SHA-256 |
| --- | ---: | --- | --- |
| scripts/analyse_arms.py | 7548 | 6eec4a6a618721326736b86b50b8c5b57030507b | 838185ed5dd7f5c4e13b1fa6b1965a5217b2097f333c901a663e3f6a0f589ad0 |
| tests/test_analyse_arms.py | 10111 | b62db8096e56c86e896541757eeae0d8a3ea1272 | 3d4be2327417fc722902968968960e0148e8584530c45c39fb6447422c79d80e |

Both match the published source-contract preimages exactly despite main moving. Exact immutable GitHub source URLs and payload hashes are listed in PUBLIC-INPUTS.json; no product source is duplicated in this documentation bundle.

The open-PR collection contained 37 PRs at this read. Every paginated changed-file list was checked against these two exact paths. Zero overlaps were returned. open-pr-overlap.json records the numbers and file lists. This is a public overlap observation only; it does not establish an acknowledged claim or absence of private work. Refresh source/claims before actual edits. No write to GitHub was made.

Published addendum body pin: ecd2ca8d2495d5d83cd303111cb9114aaa5f0f5e8ee628ec5ba2aa40ed73d9c4.
Published frozen-controls pin: c34edb7ed52546099383cd126a947adac7c5aaf3909a55c7527fef422834a617.
Those pins cover the embedded documents named in the comments, not the entire GitHub comment including publication preamble. The embedded document copies are included beside this design and hash-identical to the listed payload pins; exact authoritative comment URLs remain above.

## 2. Edit envelope

Product: add standard-library-only strict helpers between print_arm and main; add a narrow dispatch before the existing default loop in main. The helper definitions load, _mean_se, _fmt, summarise, _lines, and print_arm remain byte-for-byte unchanged. Existing main's positional-ledger analysis and exit behavior remain unchanged below dispatch. Keep existing ledgers nargs="+" behavior.

Tests: retain the entire existing test body and original helper aliases; add standard-library imports needed for new fixtures/subprocess checks, append strict-mode test classes and legacy-regression CLI controls. Do not rewrite _row, _load, or _script_path.

No third product/test file. Fixtures, snapshots, and control cases are expressed inside tests/test_analyse_arms.py; ephemeral synthetic files use tmp_path. No shared schema, ledger writer, producer, graph, provider, config reader, recorder, documentation, canonical plan, or package changes.

Before any actual edit the coordinator must disposition exact plan admission, bounded offline-before-59-02 interpretation, acknowledged claim, and source/runtime constraints. This design is not that disposition.

## 3. Public interfaces, private interfaces, and exception contract

Required public signatures remain exactly:
- load_entry_rows(path: pathlib.Path) -> tuple[list[dict], tuple[int, ...]]
- entry_level_summary(rows: list[dict], *, expected_ids: tuple[str, ...]) -> dict

Proposed private signatures:
- _entry_quote(value: object) -> str
- _EntryInputError(ValueError), storing code, details, row_indices, physical_lines, optional entry_id, and optional path
- _entry_read_bytes(path: pathlib.Path) -> bytes
- _entry_decode(raw: bytes, path: pathlib.Path) -> str
- _entry_object(pairs: list[tuple[str, object]]) -> dict
- _entry_nonfinite(token: str) -> object  # always raises structured error
- _load_entry_ids(path: pathlib.Path) -> tuple[str, ...]
- _validate_entry_ids(expected_ids: tuple[str, ...]) -> None
- _validate_entry_rows(rows: list[dict], expected_ids: tuple[str, ...]) -> dict
- _entry_fraction(numerator: int, denominator: int) -> Fraction | None
- _validate_entry_pair(left: dict, right: dict) -> None
- _entry_ref(source: pathlib.Path, physical_lines: tuple[int, ...], row_index: int) -> str
- _entry_refs(source: pathlib.Path, physical_lines: tuple[int, ...], row_indices: tuple[int, ...]) -> str
- _render_entry_arm(name: str, summary: dict, source: pathlib.Path, physical_lines: tuple[int, ...], *, diagnostic: bool) -> list[str]
- _render_entry_report(left: dict, right: dict, sources: tuple[pathlib.Path, pathlib.Path], physical_lines: tuple[tuple[int, ...], tuple[int, ...]]) -> str
- _render_entry_error(error: _EntryInputError, *, source: pathlib.Path | None = None, physical_lines: tuple[int, ...] = ()) -> str
- _main_entry_level(ledgers: list[pathlib.Path], only_ids: pathlib.Path) -> int

Fraction is from the Python standard library. It represents exact rational values; do not use legacy _fmt for these metrics. No additional dependency is needed.

The structured exception holds raw data rather than interpolated multiline messages. Error codes and structural labels are fixed analyser strings; every input-derived string is JSON-rendered with ensure_ascii=True only when rendering. Loader errors already have physical_lines. Summary errors have zero-based row_indices; the CLI attaches the relevant source and composes physical lines before rendering. A duplicate error carries both row indices or both physical lines, not only the last location.

For unavailable fields, use fixed <missing> and <null> markers. String values, including literal strings "<missing>" and "<null>", are JSON quoted, preserving the distinction. Unselected non-string outcomes may be rendered as canonical JSON values without admitting them into scoring. Selected non-string/non-null outcomes fail schema validation.

Do not build errors by concatenating raw input into a string. If an OS/JSON/argparse exception supplies a detail string, retain it as a value and JSON-quote the entire detail on output. Never print an exception traceback or raw exception text for expected invalid input.

## 4. Strict input pipeline

### 4.1 ID file

Read bytes once and strict UTF-8 decode. Split only on literal LF. Remove exactly the last empty split if the input ends with LF. For each LF-terminated logical line, remove one terminal CR if present; do not remove a bare CR from a final non-LF-terminated line.

Reject an empty list; empty/interior blank identifiers; any character for which str.isspace() is true; duplicates; identifiers not ending exactly in -bug or -clean with a nonempty prefix. Preserve case, spelling, and input order. A name such as "a-bug-bug" has a nonempty prefix and a terminal suffix; no additional undocumented naming grammar is imposed. Unicode remains data and is output escaped.

The suffix convention applies only to the explicitly requested IDs. All selected ledger IDs match this exact set. For an outside_requested_ids record, the published raw-row requirement is a nonempty string ID, not the selected suffix/coordinate/outcome schema; do not silently strengthen that into a blanket requirement on larger source ledgers.

Invoke the same ID validation from public entry_level_summary so direct callers cannot supply an empty/duplicate/malformed expected_ids tuple.

### 4.2 Ledger loader

Read bytes once, strict UTF-8 decode, then use `enumerate(text.split("\n"), start=1)` (one LF delimiter). The enumeration value is the original physical LF coordinate. Whitespace-only segments are omitted from records but never removed before enumeration. A complete final JSON row with no LF is accepted. CRLF works through JSON whitespace handling; bare CR does not create a new coordinate.

Use json.loads with an object_pairs_hook that rejects duplicate keys at every nesting level, and parse_constant rejecting NaN, Infinity, and -Infinity. Each parsed record must be a dict; each entry_id must be a nonempty str. Reject duplicate entry_id anywhere in the entire arm, including outside the requested set. Track first occurrence and report both physical locations.

A supplementary guard may reject float overflow into a nonfinite Python value (for example 1e9999), using a strict parse_float helper or equivalent; bind that explicitly during review rather than claiming it is a separate frozen C10 variant. The required C10 constants must definitely be rejected. Do not accept arbitrary inf/nan after parsing.

For UTF-8 decode failure, compute the physical line from the failing byte prefix's LF count plus one. For JSON failure, report the enclosing physical line; json.JSONDecodeError's internal line coordinate is not the file coordinate.

Reject missing, unreadable, or record-empty ledgers. Preserve exact source bytes. Do not call load, splitlines, ledger_jsonl.read_records/load_state, strip the whole file, repair a torn row, choose the last duplicate, or write any input.

Return (rows, tuple(physical_lines)) with equal lengths. Leave every row dictionary unchanged; no reserved citation field is inserted.

### 4.3 Summary validation and whole-pair validation

_validate_entry_rows returns:
{
  "by_id": {exact_id: zero_based_row_index, ...},
  "selected_row_indices": (... in expected_ids order ...),
  "outside_row_indices": (... in raw-record order ...),
  "backend": exact_nonempty_backend
}

It repeats raw object/ID uniqueness checks for callers that supplied rows directly, then checks completeness against expected_ids. There is no subset intersection fallback.

For every selected row:
- type(depth) is int and depth == 1 (bool is rejected)
- type(engine) is str and engine == "real"
- type(backend) is str and backend is nonempty
- all selected backend strings are equal
- verdict is absent, None, or a string; any other type is fatal

The contract requires nonempty backend, not a new whitespace-normalization rule. Preserve it exactly and quote it; if further restrictions are desired, seek a contract disposition.

Pair validation requires both summaries' requested tuple and selected tuple to match the supplied frozen tuple, and left/right backend equality. No backend alias is treated as source/config identity.

ID validation precedes reading numerical inputs. Both complete ledger structures and selected coordinates are validated before any report is emitted. Computation is in memory; neither arm prints during loading/validation. A right-arm defect therefore leaves stdout empty even when the left arm was valid.

## 5. Summary data shape and independent arithmetic

Suggested return shape from entry_level_summary:
{
  "expected_ids": tuple[str, ...],
  "backend": str,
  "intended_n": int, "selected_n": int, "scored_n": int,
  "scored_bug_n": int, "scored_clean_n": int, "excluded_n": int,
  "outside_requested_ids_n": int,
  "counts": {"TP": int, "FN": int, "FP": int, "TN": int},
  "exclusion_counts": {"SKIPPED": int, "ESCALATED": int, "unknown_verdict": int},
  "selected_row_indices": tuple[int, ...],
  "outside_row_indices": tuple[int, ...],
  "row_indices": {
    "TP": tuple[int, ...], "FN": tuple[int, ...],
    "FP": tuple[int, ...], "TN": tuple[int, ...],
    "SKIPPED": tuple[int, ...], "ESCALATED": tuple[int, ...],
    "unknown_verdict": tuple[int, ...],
    "outside_requested_ids": tuple[int, ...]
  },
  "classified_rows": tuple[dict, ...],
  "metrics": {
    "precision": {"value": Fraction | None, "numerator": int,
                  "denominator": int, "row_indices": tuple[int, ...]},
    "recall": {...},
    "f1": {...}
  },
  "complete": bool
}

Each classified row contains row_index, entry_id, outcome_present, outcome, category, and selected. Use selected frozen-ID order followed by outside rows in raw order. Store outcome_present separately so missing differs from null. The original row objects are not modified.

Metric terms:
- precision: numerator=TP, denominator=TP+FP; refs are TP and FP rows, merged in selected order.
- recall: numerator=TP, denominator=TP+FN; refs are TP and FN rows, in selected order.
- f1: numerator=2*TP, denominator=2*TP+FP+FN; refs are TP/FN/FP rows, in selected order. Its value is None if either precision or recall is undefined, even if the raw F1 count denominator is nonzero.
- If both P and R are defined and zero, F1 is Fraction(0, 1). This is a result invariant, not a requirement for a separate zero branch.
- Whenever both base metrics are defined, including the defined-zero case, F1 may be computed directly as Fraction(2*TP, 2*TP+FP+FN). A branch-free count formula is allowed.
- The raw numerator/denominator fields retain unreduced counts; Fraction stores the reduced exact ratio.
- TN remains visible in counts and classification even though it contributes to none of these metric denominators.
- No wall-time or runs/caught/finding_* field enters these metrics, and no SE is estimated.

Every selected row goes into exactly one scored/excluded partition. Every unselected record goes into outside_requested_ids exactly once. intended_n never shrinks for an incomplete outcome. selected_n is the full required ID count after validation; scored_n may shrink only in the explicitly incomplete diagnostic report.

## 6. CLI dispatch and rendering boundary

Existing positional-only invocation retains legacy parsing, old helper calls, mixed/missing behavior, line compression, finding arithmetic, truncation, and exit 0.

Recognize the new strict options before the first literal "--" marker. A token equal to --entry-level, --only-ids, or an "=" form of those exact names selects strict-option error handling. Tokens after "--" remain filenames. Do not let option abbreviation create an accidental strict/legacy boundary.

Strict argument parsing uses a private ArgumentParser subclass (or equivalent bounded error handler), fixed safe program name, and error() that raises _EntryInputError("cli_usage", details={...}). This prevents argparse from printing input-derived filenames/unknown tokens unescaped before main can enforce C24. It still accepts the same positional nargs="+". Keep ordinary help behavior; help is not an arithmetic result.

After parsing:
- --only-ids without --entry-level => exit 2
- --entry-level without --only-ids => exit 2
- --entry-level with anything other than exactly two ledger paths => exit 2
- Correct strict invocation => _main_entry_level

The strict parser design is an implementation choice for the existing escaping contract, not a new CLI feature. Include injected CLI-error text tests in the C24 seam group. Do not alter legacy invalid-option output merely to reuse this renderer.

_main_entry_level:
1. Load/validate IDs.
2. Load each ledger and retain each physical-line tuple.
3. Validate/classify/summarize both arms; attach errors to the corresponding path/map.
4. Validate cross-arm backend and exact ID agreement.
5. Render a complete in-memory report, then emit once.
6. Return 1 if either arm has exclusions, otherwise 0.
7. Catch expected _EntryInputError and output a pointed fixed-status diagnostic to stderr only, then return 2.

Do not broadly catch unexpected programming errors as valid incomplete outcomes.

Suggested deterministic report grammar (to be frozen in appended literal snapshots before implementation):
mode=entry_level
status=ARITHMETIC_COMPLETE  # or INCOMPLETE, generated fixed token
provenance=unverified_by_analyser
B4_acceptance=not_established
metric_SE=n/a
arm=left source="FULL_SUPPLIED_PATH" backend="EXACT_BACKEND"
counts intended_n=... selected_n=... scored_n=... scored_bug_n=... scored_clean_n=... excluded_n=... outside_requested_ids_n=...
categories TP=... FN=... FP=... TN=... SKIPPED=... ESCALATED=... unknown_verdict=...
row id="EXACT_ID" outcome="HOLD" category=TP ref="FULL_SUPPLIED_PATH":1
...
metric precision value=3/4 numerator=3 denominator=4 SE=n/a refs=["FULL_SUPPLIED_PATH":1,...]
...
arm=right ...
delta direction=right_minus_left metric=precision value=-1/4 units=ratio
...

Use exact rational value text (including 0/1, 1/1) and n/a for undefined values. Always label deltas as ratio units; do not print ambiguous "pts". If presentation later adds percentage points, convert ratios by 100 and assert exact units/signs.

In incomplete reports, label each arm's metrics as diagnostic, print both arms' full intended/scored/excluded counts, and omit every delta line entirely. Do not print success/comparison-complete language. Both fixed unverified labels remain.

Every citation calls _entry_ref(source, physical_lines, row_index), mapping zero-based row index to actual physical_lines[row_index]. The path is json.dumps(str(source), ensure_ascii=True), followed by ":" and the integer physical line. Never source.name, Path.resolve, compressed row_index+1, _lines, abbreviated path, or "+N more". Each classified row is printed once; a contributor may legitimately reappear in each relevant aggregate metric citation.

Structured error grammar:
status=INVALID_INPUT
error code=FIXED_CODE source="FULL_PATH" lines=[1,4] entry_id="ID" detail="ESCAPED_DETAIL"

Fields unavailable for a particular failure are omitted or use fixed absence markers. Error details, supplied paths/IDs, duplicate keys, invalid backends, OS exception descriptions, and unknown verdicts all pass the same quoting boundary. No input-derived string can create a new physical output line.

The supplied Path string is preserved without basename abbreviation or resolution. pathlib's existing lexical normalization is not presented as byte-for-byte argv provenance; the exact public signature already accepts Path.

## 7. Test structure and control-to-function map

All new helpers below are test-only and live in the existing test file:
- Explicit ORACLE_IDS, LEFT_OUTCOMES, RIGHT_OUTCOMES constants.
- _entry_fixture_row(id, verdict, ...) returns basic synthetic fields; never computes an expected category.
- _write_entry_fixture(tmp_path, ...) writes requested raw bytes/layout.
- _run_entry_cli(...) launches [sys.executable, str(SCRIPT), ...], captures raw stdout/stderr/returncode with timeout, without shell=True.
- Expected small stdout strings are literal test data with only safely serialized temporary paths substituted.
- Keep expected metrics/counts/citations independent of analyser helpers. Expected citation coordinates are fixed fixture constants or independently counted fixture LF bytes, never load_entry_rows output.
- In baseline-RED stage, obtain new helper attributes inside test functions if needed. Do not add top-level aliases that cause missing-helper collection errors. Use the actual CLI unknown-option result versus expected success as an attributable missing-feature witness.

Required groups:
| Control | Proposed test group / variants | Main affected functions and pointed assertions |
| --- | --- | --- |
| C01 | test_entry_oracle_summary_and_cli_snapshot | summary, render report, CLI: exact TP3/FN1/FP1/TN3 and TP2/FN2/FP2/TN2, 3/4 and 1/2, deltas -1/4; 16 classified references; exact complete labels and exit 0 |
| C02 | test_entry_explicit_incomplete[SKIPPED,ESCALATED] | classify/summary/CLI: left scored7, excluded1, TN2, right8; diagnostic3/4; exit1; no delta or success line |
| C03 | test_entry_unknown_incomplete[missing,null,FUTURE_STATUS] | missing/null distinction; unknown_verdict partition; same arithmetic boundary as C02 |
| C04 | test_entry_invalid_verdict_type[int0,false,array,object] | selected schema validator: exit2, pointed ref, stdout empty |
| C05 | test_entry_missing_selection[one_left,both,empty_source,missing_source] | loader/completeness: exit2 and exact requested ID/source; no reduced comparison |
| C06 | test_entry_duplicate_id[identical,conflicting,unselected] | loader/direct-validator: two physical locations; never last-wins |
| C07 | test_entry_id_file[each frozen malformed variant and valid LF/noLF/CRLF] | ID loader: category-specific failure before numerical output; exact order unchanged |
| C08 | test_entry_selected_coordinates[depth2,true,string1,null,missing; engine_stub,null,missing; backend_empty,null,missing] | each independent guard; type bool never passes depth |
| C09 | test_entry_backend_mismatch[within_arm,cross_arm] | arm and pair validators; exit2; same-alias valid case remains unverified |
| C10 | test_entry_strict_raw_json[all frozen variants, selected-tail/unselected placement] | loader: any raw defect fatal; no tolerant tail; inputs unchanged; no partial stdout |
| C11 | test_entry_twenty_from_150[raw_reorder,id_order,source_substitution,missing] | exact selection and outside130 references; left3/4,3/5,2/3; right5/6,1/2,5/8; deltas1/12,-1/10,-1/24 |
| C12 | test_entry_gap_snapshot | physical loader + renderer: refs1 and4, TP1/TN1, metrics1, exact snapshot |
| C13 | test_entry_line_endings[CRLF,no_terminal_LF,blank_tail,bare_CR] | loader coordinates: first3 success same1/4, bare-CR-separated objects invalid |
| C14 | test_entry_all_citations_preserved | renderer: >6 contributors; all exact metric, outside, and exclusion refs independently asserted |
| C15 | test_entry_undefined[clean_PASS,bug_PASS,clean_HOLD] | denominator/F1 gates: frozen None/0 behavior, n/a deltas, complete exit0 |
| C16 | test_entry_defined_zero_f1 | new-only zero rule: TP0/FN1/FP1/TN0, P0/R0/F1=0, exit0 |
| C17 | test_entry_no_metric_se[single_bug,oracle] | fixed SE=n/a despite distinct entry count |
| C18 | test_entry_optional_fields_have_no_authority | ignored optional fields/misleading ordinary extras; exact unchanged arithmetic and fixed labels |
| C19 | test_entry_sibling_artifacts_survive_incomplete_parent | test-owned external fixture/index hash check plus CLI incomplete; no producer/durability credit |
| C20 | test_entry_consumed_graph_evidence_boundary | separate positive producer and canonical-only fixture dispositions; numerical output unverified in both; no product graph import |
| C21 | test_legacy_cli_and_strict_option_isolation | real positional-only CLI missing/mixed/blank/finding behavior; old zeroF1 and SE; all invalid strict combinations exit2 |
| C22 | test_entry_selected_150[complete,one_missing] | 75/75 full exact set: left4/5 right3/5 delta-1/5; all refs; missing => exit2/no report |
| C23 | test_entry_changed_id_file_remains_unverified | structurally complete alternate20 may exit0; external independently frozen digest rejects historical authority; no invented CLI provenance failure |
| C24 | test_entry_escaped_data[unknown_verdict,path,invalid_id,backend,file_error,cli_error] | exact stdout/stderr escaped data and absence of injected standalone success/provenance lines |

C14 needs an incomplete-outcome variant as well as complete >6-citation case to prove excluded and outside reference sets independently. C10 includes duplicate nested keys, repeated verdict, every non-object kind, invalid UTF8, middle/tail failures and defect placement after selected rows. Preserve parameter IDs so later actual collection counts show each variant; do not state a predicted test count as executed.

C19/C20/C23 are evidence-boundary controls in the test/qualification layer. They do not authorize a new script evidence-consumer interface. Every supplied external artifact remains synthetic unless actually qualified elsewhere.

## 8. Exact-site negative-control planning

Source selectors remain UNBOUND until actual reviewed candidate bytes exist. Bind each cut to candidate SHA, exact statement/hunk, test node ID, expected raw exit, and relevant assertion before running it. Planned correspondence:
- N01 completeness/same-set guard -> C05/C11/C22 relevant missing-ID assertion.
- N02 duplicate guard -> each C06 variant; two-ref error versus illicit acceptance.
- N03 depth type/value, engine type/value, backend type/nonempty, within-arm/cross-arm equality -> separate C08/C09 variants, one guard per cut.
- N04 enumerate before blank filtering -> C12/C13 raw coordinate mismatch.
- N05 renderer physical_lines[row_index] composition -> separate C12/C14 citation mismatch, not only helper metrics.
- N06 exclusion partition, completeness flag, diagnostic prefix, delta suppression/exit -> C02/C03 individually attributable changed decisions.
- N07 precision/recall denominators and F1 undefined/defined-zero invariants -> C01/C15/C16 exact numeric assertions. For defined-zero F1, bind an exact-site non-equivalent fault that returns undefined instead of zero; C16 must detect the changed value/output. Removing a redundant zero branch can be equivalent because the remaining count formula still returns zero. Record that deletion as equivalent with its algebraic and source rationale, never as RED, then use the separate non-equivalent invariant witness. See PBR-FINDING-DISPOSITIONS.md for the narrow frozen-N07 override.
- N08 removal/truncation of full metric refs -> C14 missing exact original location.
- N09 validation-before-emission and exit2/exit1 dispatch -> subprocess C05/C10/C02 empty-stdout/exit assertions.
- N10 fixed provenance/B4 labels -> C18/C20 exact false/dropped-label assertion.
- N11 strict-only dispatch or legacy semantic change -> C21 default behavior and invalid flags.
- N12 quote seams (row, metric citation, path, backend, unknown outcome, structured error, argparse error) -> each C24 variant catches actual unescaped control text; a generic snapshot mismatch alone is not enough.

Before each separate non-equivalent omission or mutation, copy every affected uncommitted candidate file to a named owned temporary backup outside the mutation targets, record target/backup paths and candidate hashes, and verify the saved bytes before cutting. Restore only from those backups in a try/finally transaction once the bounded owned witness is no longer running; forbid git restore and all HEAD/index-based mutation recovery. Verify restored hashes before the restored-positive test or further cuts, review or publication. Retain backups and raw results. If an interruption bypasses finally or restoration is uncertain, stop and recover the candidate from its verified backups before continuing; preserve foreign files. Record equivalent deletions explicitly and do not manufacture RED or claim them as qualifying witnesses. Collection/startup errors do not qualify RED. Changed-line execution and actual GREEN restoration remain later evidence obligations, not deliverables of this design.

## 9. Pending admission, verification, and stopping conditions

Do not implement until the parent clears the actual exact-plan gate and source ownership claim. Do not execute this design or tests without the separately admitted owned runtime/source/interpreter/budget and required repository rules. No environment pin is established by a connector source read.

Before edit: recheck main/scoped blob preimages; current scoped bytes must equal this packet or require a rebind/review. Refresh #43 acknowledgment and overlapping exact-path PR check. Read applicable repository instructions in the selected owned checkout.

After admission: follow the published three-task order: independent baseline RED, strict implementation, exact-composition qualification. Retain raw commands/stdout/stderr/exit, distinct collected/passed/failed/skipped counts, source and fixture hashes, actual imported origins, negative cuts/restored positives, and independent review dispositions. Parent owns actual Forge route rotation, distinct setup/process/verdict/qualification identities, and any later authorized publication/integration. No native clean credit exists here.

All current test/runtime/native-review/CI/adoption/acceptance fields: NOT RUN / NOT ESTABLISHED. Scope decision and function/data-shape/rendering choices above are proposed implementation details, not a claim that the contract or plan has been admitted.
