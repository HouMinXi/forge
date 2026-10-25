# 59-16 frozen known answers and qualification controls

Status: proposed independent oracle for plan review, 2026-10-18. No tests or product code have been written or run. Read with 59-16-CURRENT-SOURCE-ADDENDUM.md. Counts and expected exits below are independent arithmetic, not results reported by Forge.

## Fixed eight-entry oracle

Before any implementation, freeze these exact ID and outcome mappings. Each ledger row additionally has depth=1, engine=real, backend=fixture-review, runs=1, caught=0, wall_s=1. None of these fixture values assert an actual provider run or configuration identity.

| ID, in frozen order | Left outcome | Left category | Right outcome | Right category |
| --- | --- | --- | --- | --- |
| A-bug | HOLD | TP | HOLD | TP |
| B-bug | HOLD | TP | HOLD | TP |
| C-bug | HOLD | TP | PASS | FN |
| D-bug | PASS | FN | PASS | FN |
| A-clean | HOLD | FP | HOLD | FP |
| B-clean | PASS | TN | HOLD | FP |
| C-clean | PASS | TN | PASS | TN |
| D-clean | PASS | TN | PASS | TN |

Independent expected totals:
- Left: intended8, selected8, scored8, scored_bug4, scored_clean4, excluded0; TP3/FN1/FP1/TN3. Precision3/4, recall3/4, F1=3/4.
- Right: same population counts; TP2/FN2/FP2/TN2. Precision1/2, recall1/2, F1=1/2.
- Right-minus-left deltas: precision=-1/4, recall=-1/4, F1=-1/4. Equivalent precision/recall point presentation is -25.0 percentage points; formatting must not confuse a0.25 ratio change with0.25 percentage points.
- Every selected input reference printed exactly once in its row classification, and every metric carries its complete contributing references.
- Exit0; status=ARITHMETIC_COMPLETE; provenance=unverified_by_analyser; B4_acceptance=not_established; metric SE=n/a.

## Mandatory control table

Every ID denotes a parameterized group where alternatives are listed. Preserve each variant distinctly in actual collected counts.

| ID | Frozen change/input | Independent expected result |
| --- | --- | --- |
| C01 | Exact eight-entry oracle above, normal LF files | Exit0; exact counts, fractions, delta signs/units, all16 per-arm row citations and fixed unqualified provenance label. Exact small-fixture stdout snapshot. |
| C02 | Change left D-clean to SKIPPED, then separately ESCALATED | Exit1; left intended8/selected8/scored7/excluded1, TP3/FN1/FP1/TN2, scored_bug4/scored_clean3. Right remains8. Left diagnostic precision/recall/F1=3/4. Explicit outcome exclusion and its original line. No paired deltas, successful comparison text or corpus reduction. |
| C03 | Left D-clean verdict separately omitted, null, or string FUTURE_STATUS | Exit1; same exclusion/count effects as C02 with unknown_verdict. Original value/missing distinction reported. Never TN or zero findings. |
| C04 | Selected verdict separately integer0, bool false, array, object | Exit2 INVALID_INPUT before metrics; pointed schema/path/line/ID error. No data coercion. |
| C05 | Remove left D-bug; separately requested ID absent from both ledgers; separately empty ledger or missing ledger | Exit2; explicit missing exact ID or source error. No subset comparison and no success output. |
| C06 | Duplicate left A-bug once with identical record; separately repeat as PASS; separately duplicate an unselected ID in a larger ledger | Exit2; both physical locations identified. Never latest-wins or favorable-outcome selection. This deliberately refuses active resume logs. |
| C07 | ID-list separately empty, duplicate, interior blank, whitespace-padded, embedded whitespace, missing suffix, empty prefix, malformed UTF-8 | Exit2 before arithmetic; exact input-category diagnostic. Final LF/no-final-LF and CRLF otherwise valid; no normalization/deduplication. |
| C08 | Selected depth separately2, true, string1, null/missing; engine stub/null/missing; backend empty/null/missing | Exit2; typed selected-coordinate failure. JSON true never equals admitted depth1. |
| C09 | One selected row uses another backend; separately whole right arm uses another backend | Exit2; within-arm or cross-arm mismatch. Same alias on both arms alone only supports arithmetic and never config-binding credit. |
| C10 | Malformed middle JSON, torn final JSON, invalid UTF-8, JSON array/null/scalar record, duplicate member key (including repeated verdict), JSON NaN/Infinity/-Infinity | Exit2 even when the defect lies after otherwise valid selected rows or among unselected rows. No resume-tail recovery, silent skipping or partial metric report. Inputs byte-identical afterward. |
| C11 |150 unique raw rows, frozen20 IDs selected identically from both; reorder raw records and use a different fixed order in the IDs file | Success depends on IDs, not row order. Report130 outside_requested_ids per arm with physical refs; intended/selected20. Use oracle below for metrics.150 is source population only, never eligible/qualified corpus credit. Ledger substitution or a missing selected ID against the supplied list gives exit2; the list's historical provenance is external, as C23 verifies. |
| C12 | Two-ID subset A-bug and A-clean. Left physical lines:1 A-bug HOLD,2 empty,3 spaces/tab only,4 A-clean PASS. Right same physical layout and values | Exit0; TP1/TN1, precision1, recall1, F1=1. Cites raw lines1 and4, not compressed1 and2. Small stdout snapshot must fail if strict parser or renderer renumbers after blank filtering. |
| C13 | Repeat C12 with CRLF, and separately omit terminal LF after complete final JSON; append whitespace-only final physical lines | Exit0 with unchanged physical record lines1 and4. Terminal LF is not mandatory. A bare CR does not increment LF coordinate; two JSON objects separated only by CR are invalid JSON and exit2. |
| C14 | Twenty matched rows with more than six contributors to a metric and blank lines interspersed; include paths/IDs that are not ambiguous abbreviations | Every contributing original path:physical-line pair remains in stdout. No '+N more' as sole citation proof. Unselected and excluded rows have distinct complete reference sets; selected metric partitions independently match actual raw lines. |
| C15 | Undefined denominators: clean-only PASS; bug-only PASS; clean-only HOLD, each supplied identically in both complete arms | All exit0 arithmetic-only. clean PASS: precision/recall/F1=n/a. bug PASS: precision=n/a, recall0, F1=n/a. clean HOLD: precision0, recall=n/a, F1=n/a. Undefined deltas=n/a; no invented0. |
| C16 | One -bug PASS and one -clean HOLD, identically in both arms | TP0/FN1/FP1/TN0; precision0, recall0, **F1=0**, complete exit0. This freezes the new-mode case intentionally separate from legacy summarise behavior. |
| C17 | One -bug HOLD per arm; then ordinary eight-entry oracle | First case precision/recall/F1=1, metric SE=n/a. Second case also no within-entry replicated uncertainty claim; distinct entries do not become repeated measurements. |
| C18 | Change or remove optional runs/caught/wall_s/finding_* fields while preserving valid JSON; add misleading ordinary extra fields claiming source/config/graph success | Entry counts/metrics depend only on admitted identity/label/verdict fields and remain unchanged. Output still provenance=unverified_by_analyser; B4_acceptance=not_established. No optional field creates source/config authority. Nonfinite JSON remains invalid under C10. |
| C19 | Test-owned sibling response, usage and error artifacts plus a separate parent-timeout evidence record; selected parent row is SKIPPED or unknown | Exit1 as C02/C03. An independently constructed qualification index retains exact sibling artifact paths/hashes; verify these original fixture files remain addressable and byte-identical after CLI. No sibling-complete→parent-complete conversion. Do not claim this synthetic consumer control qualifies provider capture or durability. |
| C20 | External evidence pair: (a) actual producer record names consumed namespace/source and its result; (b) canonical graph metadata only plus linked-worktree index missing/enrichment unavailable | Numeric CLI remains explicitly provenance-unverified in both cases. External evidence review distinguishes(a) from(b); only(a), when actually qualified, could support a context-derived interpretation. Extra row metadata cannot change the fixed unverified status. This is an evidence-boundary control, not graph-build/product-consumer implementation credit. |
| C21 | Existing default helper/module tests and real positional-only CLI: missing source; mixed engine; blank lines; finding-level known answers; existing zero-F1/SE behavior | Preserve all legacy semantics, including current missing/mixed exit0 and current load() compressed-row behavior. Strict behavior appears only with --entry-level. Test flags: --only-ids without new mode, new mode without IDs, and new mode with1/3 ledgers all exit2; legacy nargs+ unchanged. |
| C22 | Independently frozen selected150 IDs,75-bug and75-clean, all required in each arm; remove one selected row in a separate variant | Full case uses the150-selected oracle below and exits0. Missing even one selected row exits2; the analyser must not silently fall back to20. External original three-canary selection stays outside this script. |
| C23 | Replace the originally admitted20-ID file with a different valid20-ID set fully present in both larger ledgers; compare its digest against an independently frozen external expected digest | CLI may exit0 for structurally complete arithmetic but still prints provenance-unverified/B4-not-established; it cannot observe historical selection timing. External freeze verification rejects the changed digest and denies historical/B4 selection authority. Do not claim a nonexistent analyser provenance failure exit. |
| C24 | Unknown selected verdict contains newline followed by status=ARITHMETIC_COMPLETE and B4_acceptance=qualified; ledger path separately contains newline/tab; an invalid ID/backend or file error includes control text | Unknown-verdict case exits1, structural/file error exits2. All input-derived strings are reversibly JSON-quoted with ensure_ascii=True in stdout/stderr, including citation paths and error details. Injected text remains escaped data, never standalone report fields or success lines; fixed status/provenance fields stay truthful. Exact stdout/stderr assertions cover the escape boundary. |

## C11 fixed20-from150 oracle

Freeze ten selected -bug IDs and ten selected -clean IDs in the IDs input before looking at either ledger. The130 unique extras have ordinary well-formed object records and are named outside_requested_ids; do not use those outcomes in any selected denominator.

- Left selected outcomes: TP6, FN4, FP2, TN8. Precision6/8=3/4; recall6/10=3/5; F1=2/3.
- Right selected outcomes: TP5, FN5, FP1, TN9. Precision5/6; recall5/10=1/2; F1=5/8.
- Right-minus-left: precision=1/12, recall=-1/10, F1=-1/24.
- selected_n20, scored_n20, scored_bug_n10, scored_clean_n10, excluded_n0 in each arm.
- No original historical corpus identities are invented by these synthetic IDs; this qualifies arithmetic/filter behavior only.

## C22 fixed150-selected oracle

Freeze75 unique -bug IDs and75 unique -clean IDs before producing either synthetic ledger. This is a test-only balanced oracle, not an assertion about the composition of the historical150 corpus.

- Left: TP60, FN15, FP15, TN60. Precision=4/5, recall=4/5, F1=4/5.
- Right: TP45, FN30, FP30, TN45. Precision=3/5, recall=3/5, F1=3/5.
- Right-minus-left: precision=-1/5, recall=-1/5, F1=-1/5.
- Each arm: intended150, selected150, scored150, scored_bug75, scored_clean75, excluded0 and outside_requested_ids0.
- Every physical citation remains available. Removing any selected ID causes exit2 and no numerical report. No outcome-driven reduction to20 is allowed.
- Historical20-versus150 selection follows separately qualified59-17 three-canary evidence; this fixture does not replace or generate that evidence.

## Exact-site omission/restoration inventory

Future candidate line/hunk selectors are intentionally **UNBOUND** until reviewed candidate bytes exist. Before running any cut, bind selector, source hash, selected test, expected raw exit and attributable assertion. Each cut is separate; no combined mutation gets blanket credit.

| Cut | Changed invariant to omit | Required pointed witness |
| --- | --- | --- |
| N01 | Exact requested-ID completeness / same-set check | C05 or C11 must fail because incomplete pair could otherwise compare; failure must be the relevant assertion. |
| N02 | Duplicate/collision refusal | C06 must fail with acceptance/latest-win behavior exposed. |
| N03 | Strict selected depth/engine/backend checks, each distinct guard separately | Corresponding C08/C09 parameter must fail. Cutting one guard does not qualify the others. |
| N04 | Physical LF coordinate preservation at loader | C12/C13 must fail by showing1/2 rather than1/4 or another specific wrong citation. |
| N05 | Physical-line tuple composition at renderer | Separate C12/C14 assertion must fail even if helper counts remain right. |
| N06 | Incomplete exclusion / no-paired-delta boundary | C02/C03 must fail on scored population, exit or forbidden delta, each changed decision separately. |
| N07 | Precision denominator, recall denominator, F1 zero and undefined branches | C01/C15/C16 independently fail their exact expected count/ratio; no source self-derived expected values. |
| N08 | Full citation rendering | C14 catches truncated/omitted references. |
| N09 | New-mode fatal/incomplete exit and validation-before-output checks | Actual subprocess C05/C10 and C02 catch wrong exit or premature numerical output, respectively. |
| N10 | Fixed provenance-unverified / no-B4-credit output | C18/C20 catch a dropped or false-qualified label; no external evidence source is imported. |
| N11 | Mode selection isolation | C21 catches changed default behavior or strict options being silently ignored. |
| N12 | Input-derived output escaping, each stdout/stderr/citation seam separately | C24 must fail on an actual injected control line or unescaped data; no generic snapshot mismatch without attributing the escaped-data invariant. |

After each omission, preserve original failure streams; restore exact source bytes; rerun the same witness to GREEN. Startup/collection failures are not qualifying RED. Each changed logic statement still needs actual execution coverage and appropriate assertions, even when one named witness covers several lines.

## Evidence and review records, not inferred results

When execution is separately admitted, retain:
- Source commit/tree/diff and exact test/input hashes
- Actual interpreter and imported origins, pytest guard/rules/source-bound graph evidence as required by the admitted environment
- Actual command, selected node IDs, raw stdout/stderr/exit, collected/passed/failed/skipped counts and timing
- Every independently bound negative cut and restored source/positive witness
- Full unchanged legacy results versus strict-path results
- R2 synthetic sibling fixture/index and external consumed-graph evidence disposition, clearly separated from producer qualification
- Required actual Forge route setup/attempt/process/verdict/qualification outcomes and fallback labels, not a synthesized clean receipt
- Unresolved source/config/label/B4 inputs and all STOP decisions

Current values for all execution and review-result fields are **NOT RUN / UNBOUND**. This control table does not authorize tests, runtime processes, providers, package installation, CI, publication or acceptance.

