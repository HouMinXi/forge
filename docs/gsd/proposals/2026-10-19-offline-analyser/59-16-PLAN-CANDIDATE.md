---
phase: 59-judgment-and-evidence
plan: "16"
type: execute
wave: 1
depends_on: []
files_modified: ["scripts/analyse_arms.py", "tests/test_analyse_arms.py"]
autonomous: true
requirements: ["P59-CLOSE"]
must_haves:
  truths:
    - "Synthetic, pre-result frozen IDs select exactly the same complete set in both arms; selected depth=1, engine=real and backend match, and every excluded outcome remains explicit."
    - "Every aggregate and classified row maps to the full supplied file and original physical LF line; metric uncertainty is n/a without replicated measurements."
    - "Strict malformed, empty, mixed or unmatched inputs exit 2 before numerical output; incomplete outcomes exit 1 without paired deltas; complete arithmetic exits 0 with provenance unverified and B4 acceptance not established."
    - "The unchanged positional-only finding-level path retains its existing behavior."
    - "Synthetic capability qualification neither consumes unavailable historical binding as a prerequisite nor establishes historical source/config/labels, canary selection, B3/B4 acceptance or P59-CLOSE."
  artifacts:
    - path: "scripts/analyse_arms.py"
      provides: "Strict two-arm entry-level arithmetic on explicit synthetic inputs with exact raw citations and fixed unverified status"
    - path: "tests/test_analyse_arms.py"
      provides: "Independent frozen C01-C24 arithmetic, completeness, physical-coordinate, legacy and evidence-boundary controls"
  key_links:
    - from: "tests/test_analyse_arms.py independent frozen synthetic ID/ledger bytes"
      to: "analyse_arms.py --entry-level --only-ids IDS LEFT_LEDGER RIGHT_LEDGER"
      via: "Real subprocess assertions on exit, stdout, stderr and unmodified inputs"
    - from: "load_entry_rows physical LF map"
      to: "entry_level_summary row_indices and strict renderer"
      via: "CLI composes original physical lines, never compressed list positions"
    - from: "qualified 59-16 arithmetic capability"
      to: "existing 59-17.3 historical-use qualification"
      via: "59-17 independently checks 59-02 binding, source/config/labels and frozen canary-selected IDs before interpreting the arithmetic report"
---

# Additive 59-16 current planning candidate

Status: PROPOSED FOR REVIEW, NOT ADMITTED. Prepared 2026-10-19 UTC. This new file is not an edit to any published or dated plan. See REVISION-AND-DISPOSITIONS.md for the exact precedence proposal, dependency delta and unchanged historical obligations. A reviewed disposition of this complete candidate, its frozen controls and its research/validation overlays is required before treating it as the operative plan.

The conceptual label **16A** means the capability implemented and qualified by this existing numbered plan 59-16. **16B** means historical-use qualification inside existing 59-17.1 through 59-17.3, especially 59-17.3. Neither is a new plan ID, numbered phase or new dependency node.

<objective>
Implement and qualify strict matched entry-level arithmetic using only independent synthetic inputs, in the existing two-file analyser scope. The capability is a prerequisite for later B4 interpretation; it is not itself a B4 result. Keep P59-CLOSE assigned as a contributing requirement while leaving its acceptance entirely pending the existing 59-02/59-15/59-17 chain. Target context cost 35-45%; three sequential bounded tasks.
</objective>

<execution_context>
The applicable admitted execute-plan and summary workflow references must be available in the selected implementation environment before execution. This candidate does not assert a local installation, SDK run, interpreter, source graph or executable environment. The pinned upstream 1.42.3 checker procedure is review evidence only; its final applicability/limitations remain explicit.
</execution_context>

<context>
Required reviewed planning inputs, bound by PUBLIC-INPUTS.json:
- This candidate and REVISION-AND-DISPOSITIONS.md.
- the exact published research plus RESEARCH-PLANNING-DISPOSITIONS.md, whose section-only planning delta leaves measured endpoint/worker/history qualification pending.
- 59-VALIDATION-CANDIDATE.md, which supplements the unchanged phase validation strategy only for this slice.
- Unchanged 59-16-CURRENT-SOURCE-ADDENDUM.md and 59-16-FROZEN-CONTROLS.md included beside this candidate.
- PATCH-DESIGN.md included beside this candidate.
- Applicable current project requirements, decisions, instructions and patterns already required by the published plan.
- Exact current scoped product/test bytes and read-only ledger/label contracts, refreshed in the admitted checkout.

The published 59-02 and 59-17 plans are read-only acceptance-boundary references. Their historical files and BINDING.json are NOT a required input, import, fixture, read_first file or execution prerequisite for any 59-16 task. Their absence cannot be silently replaced with data. Historical evidence becomes mandatory only at the unchanged 59-17 entrance.
</context>

<interfaces>
Retain load, _mean_se, _fmt, summarise, _lines and print_arm unchanged; retain the positional-only main path and nargs="+" behavior. Add exactly:
- load_entry_rows(path: pathlib.Path) -> tuple[list[dict], tuple[int, ...]]
- entry_level_summary(rows: list[dict], *, expected_ids: tuple[str, ...]) -> dict
- --entry-level and --only-ids PATH, used together with exactly two ledgers.
Use standard-library-only private helpers inside scripts/analyse_arms.py. No new product file, binding CLI, product import, provider/config/graph/recorder integration, ledger writer or dependency.

The unchanged addendum and frozen C01-C24 controls supply the full input/schema/metric/exit/citation contract; PATCH-DESIGN.md supplies proposed exact implementation seams. Strict UTF-8/JSON includes duplicate member rejection, all-arm duplicate IDs and full raw-file validation, including unselected records and torn tails. Preserve physical LF coordinates through loader-to-renderer composition. Validate whole pair before output. Selected labels derive only from explicit frozen -bug/-clean synthetic conventions; historical label authority is never inferred.

Precision=TP/(TP+FP), recall=TP/(TP+FN); zero base denominator is n/a. F1 is n/a when either base metric is undefined; defined zero precision and recall yield F1=0 only in the new mode. Retain exact numerator/denominator counts; rational deltas are right-minus-left in ratio units. No wall_s/finding_* contribution or invented metric SE. All input-derived text, including errors and CLI misuse details, is JSON escaped with ensure_ascii=True. Complete and incomplete reports always retain provenance=unverified_by_analyser and B4_acceptance=not_established.
</interfaces>

<entrance_gate>
This plan revision is documentation only and grants no product edits or execution. Before product work the parent must disposition the exact candidate with genuine checker findings, complete PBR and independent full-plan convergence under the existing gate, plus exact source ownership and current-base conditions. The complete 581-line PBR skill and its includes/source identity are currently missing: no PBR PASS is claimed. An explicit separately reviewed narrow-source disposition, if proposed by the parent, must be recorded as a distinct decision; this document does not infer or grant one.

For admitted implementation, fetch real origin/main, bind an owned isolated checkout and nonempty merge-base, current rules/source/import origins/graph, all plan/input hashes and a finite runtime budget. Refresh both exact file preimages and obtain acknowledged exact-base/file/function ownership with merge order. A source read does not establish runtime or host availability. Preserve foreign changes. No provider use, installation, service restart, config/key/memory synchronization or Phase60 work follows from this slice.
</entrance_gate>

<dependency_contract>
Proposed hard execution dependencies: none; proposed wave: 1. Remove 59-02 from this capability-only plan's hard depends_on because no task consumes its output. The immutable acceptance rules quoted in the reviewed contract are reference constraints, not a completed historical-binding deliverable. This is an explicit plan change requiring review, not an interpretation of the old depends_on while leaving it contradictory.

59-02 remains unchanged at wave 1 and produces explicit verified/partial/unknown historical binding or reconstruction prerequisites. 59-17 remains wave 8, depends_on=["59-02", "59-15", "59-16"], and is the sole owner of historical-use qualification. There is no 59-16-to-59-17 back edge and no 59-16-to-59-15 dependency. A 59-02 disposition of unavailable/unknown does not satisfy the 59-17 historical entrance; 59-17 must either stop or admit its explicit independently reviewed pre-result reconstruction. Shared public validator files remain serialized by their existing 02-to-17 ownership chain.
</dependency_contract>

<tasks>

<task type="auto" tdd="true">
<name>59-16.1: Freeze synthetic controls and prove baseline RED</name>
<files>tests/test_analyse_arms.py</files>
<read_first>scripts/analyse_arms.py; tests/test_analyse_arms.py; src/code_forge/eval/ledger_jsonl.py; src/code_forge/eval/corpus.py; exact candidate and pinned addendum/frozen controls/PATCH-DESIGN. Read every existing modified file before changing it. No historical evidence or BINDING.json is required.</read_first>
<behavior>All C01-C24 groups and each listed variant have independent expected counts, fractions, citations, stdout/stderr and exits. Actual CLI missing-feature behavior exposes absent strict mode. Legacy tests are preserved. No import/collection error counts as RED.</behavior>
<action>Freeze the eight-entry, 20-from-150 and full-150 synthetic ID/outcome oracles before implementation; retain all parameter variants and independent expected physical coordinates. Append tests only in the existing test module, using its normal test-only fixture patterns and temporary files. Freeze complete/incomplete/error and escaping snapshots, public signatures, exact argv mode isolation, every exclusion and strict raw-input rejection. Synthetic C19/C20/C23 evidence-boundary fixtures live in test-owned temporary paths and never consume historical archives or alter the shared schema. Missing new helper lookup occurs inside the test if needed so collection can complete; a collection scaffold must be in the reviewed diff. Demonstrate baseline RED through actual assertions and direct CLI subprocess output, retain raw results and genuine collected/pass/fail/skip counts.</action>
<verify><automated>PYTHONPATH=src /usr/bin/python3 -m pytest -q tests/test_analyse_arms.py</automated></verify>
<acceptance_criteria>Independent frozen controls collect and expose the absent strict capability at a behavior boundary; existing legacy expectations remain intact. Actual RED is not claimed until run under the admitted runtime.</acceptance_criteria>
<done>All frozen control variants and small literal snapshots are reviewable, missing-feature RED is attributable, and no test imports unavailable historical evidence.</done>
</task>

<task type="auto" tdd="true">
<name>59-16.2: Implement strict two-arm arithmetic</name>
<files>scripts/analyse_arms.py, tests/test_analyse_arms.py</files>
<read_first>Both current scoped files; Task59-16.1 frozen tests and raw RED evidence; pinned source contracts and PATCH-DESIGN. Read each existing modified file before changing it.</read_first>
<behavior>Whole-pair strict validation, exact selected identity and explicit exclusions precede any numerical output. Complete arithmetic is exit0 with fixed unqualified labels; incomplete is exit1 without paired deltas; invalid input is exit2 with empty stdout. Every original physical citation is preserved and every legacy default test retains its result.</behavior>
<action>Add the two public interfaces, same-file private validators/renderers and strict-only dispatch. Preserve original row dictionaries and keep zero-based row_indices distinct from physical lines. Validate ID file, all raw records, selected schema and both arms before one report emission. Use exact rational metric arithmetic and the frozen zero/undefined F1 distinction. Escape all input-derived stdout/stderr values, including OS/argparse details, without hiding programming exceptions. Keep legacy helpers and positional path unchanged. Execute every frozen control and its real CLI composition using independent expectations; no helper-only provenance credit. If a requirement needs source/config identity, graph consumption or a new evidence-consumer API, stop that expansion and report the exact boundary rather than adding an interface.</action>
<verify><automated>PYTHONPATH=src /usr/bin/python3 -m pytest -q tests/test_analyse_arms.py</automated></verify>
<acceptance_criteria>C01-C24 and unchanged legacy controls pass on the exact candidate under the admitted runtime, and no successful output claims historical/B4 qualification. All failures and actual counts remain retained.</acceptance_criteria>
<done>The two-file strict capability matches the frozen independent contract and default behavior remains unchanged; implementation status stays distinct from later review, publication, adoption and historical acceptance.</done>
</task>

<task type="auto" tdd="true">
<name>59-16.3: Qualify composition and deliver capability evidence</name>
<files>scripts/analyse_arms.py, tests/test_analyse_arms.py</files>
<read_first>Exact Task59-16.2 source/test bytes and diff; all frozen controls; PATCH-DESIGN section8; retained raw RED/GREEN and CLI evidence; current admitted environment and applicable review/delivery rules.</read_first>
<behavior>Every changed guard/branch and loader-to-renderer/CLI composition has executed coverage and an attributable separate omission RED followed by restored GREEN. Native setup, attempt, completion, verdict and qualification remain separate identities. No synthetic evidence creates historical credit.</behavior>
<action>Bind every N01-N12 cut to the exact candidate SHA, statement/hunk, test node, raw expected exit and invariant assertion before any omission. Separate each typed-coordinate guard, F1 branch, stdout/stderr/citation quote seam, completeness decision and public dispatch site; generic snapshot mismatch or collection failure is insufficient. Preserve original failure streams and restore exact candidate bytes after each cut, rerunning the same witness. Capture changed-logic execution coverage with existing tooling or admitted stdlib trace, real script subprocess smoke and unchanged legacy tests. Verify C19 fixture addressability/hash preservation, C20 synthetic consumed-versus-canonical-only disposition and C23 external frozen-digest rejection without giving producer or historical credit. Parent arranges three consecutive independent clean whole-diff Forge reviews with required CLI/CI/MCP rotation and exact source/runtime/config pins; actual unavailability and any authorized fallback are separately labeled. New logic resets clean credit. Parent owns exact-head CI, one signed reviewed product delivery, authorized integration, dual-host adoption and owned-only cleanup under the existing gates. Do not mark any missing gate passed.</action>
<verify><automated>PYTHONPATH=src /usr/bin/python3 -m pytest -q tests/test_analyse_arms.py</automated></verify>
<acceptance_criteria>Exact composition, independent cuts/restorations, executed changed logic, raw outcomes and review/delivery dispositions are retained with no synthetic-to-native or arithmetic-to-history conversion. Missing review/CI/adoption gates remain explicitly incomplete.</acceptance_criteria>
<done>Report capability implementation, source review, runtime qualification, publication, adoption and hygiene separately. Provide 59-17 the exact qualified capability identity, tests and interface, while all historical-use evidence and P59-CLOSE remain pending their own gates.</done>
</task>

</tasks>

<threat_model>
T-59-16-1 ID/denominator/source mismatch: C01-C11/C22-C23; Task1 freezes evidence, Task2 rejects structural mismatch, Task3 cuts each guard. Historical source identity stays external.
T-59-16-2 false metric/SE certainty: C01/C15-C18; Task2 exact defined/undefined arithmetic, Task3 separate denominator cuts.
T-59-16-3 citation/control-text spoofing: C12-C14/C24; Task2 exact LF composition and escaping; Task3 loader, renderer and every quote seam separately.
T-59-16-4 false historical or producer acceptance: C19-C20/C23 and fixed unverified labels; Tasks1-3 preserve consumer-only boundaries; actual qualification remains 59-17.
T-59-16-SC runtime dependency risk: no package installation; any new dependency needs its own legitimacy and exact-plan disposition.
</threat_model>

<verification>
The focused test anchor exists at both pinned scoped-source observations; Task1 adds new cases to it, so there is no missing test-file dependency or false Wave0 completion claim. The three tasks are sequential within wave1 and each has the focused automated check. This is a prospective sampling map, not executed test evidence. Full phase Nyquist status remains unchanged and pending. All C01-C24 variants and N01-N12 exact-site families remain mandatory as stated in the immutable frozen controls; no line/hunk selector is bound until actual candidate bytes exist.
</verification>

<success_criteria>
Synthetic arithmetic capability is independently qualified on exact bytes, including real CLI behavior and unchanged legacy semantics. That result alone never completes P59-CLOSE, 59-02, 59-15, 59-17, the phase, measured endpoint/worker qualification or historical B3/B4 acceptance.
</success_criteria>

<output>
After admitted execution, retain the normal 59-16-SUMMARY and execution/59-16 evidence namespace with raw commands/counts/outputs, hashes, cuts/restorations, coverage and independent dispositions; keep local planning/evaluation records unstaged. Nothing in this candidate creates a new product file, acceptance record or implementation authorization. Current product/runtime/native-review/CI/adoption/acceptance fields are NOT RUN or NOT ESTABLISHED.
</output>
