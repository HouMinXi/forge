## ISSUES FOUND

# Independent documentary GSD 59-16 plan review

Date: 2026-10-19 UTC
Subject: the complete received R2 59-16 plan, the unchanged published plan, proposed current-source addendum, frozen controls, and supplemental patch design.
Result: **2 BLOCKERS, 1 WARNING. No verification-pass or execution-admission claim.**

This is an independent static review using the pinned upstream GSD v1.42.3 checker as documentary methodology. It is not an SDK invocation, installed typed-checker result, PBR, native Forge review, implementation verification, or full-phase gate closure. The complete private 581-line eight-pass PBR specification was unavailable. No product code was edited, imported, installed, collected, tested, or run. No external writes occurred.

## 1. Method, version and evidence boundary

The full 978-line agents/gsd-plan-checker.md was read, including all dimensions, process, anti-patterns and returns. Required gates.md, thinking-models-planning.md and few-shot-examples/plan-checker.md were read in full. agent-contracts.md and revision-loop.md were also read for handoff and bounded revision semantics.

Upstream identity: gsd-build/get-shit-done, v1.42.3, commit 7dfeb7ad8acbd6febd2c8c6cf7d3dcb7d1aeb7b9.
Checker SHA-256: e92c2937abab2460d5abee05371d3e532b3ff8ea7999dad48beb62d0f59a6a1a.
The supplied upstream manifest's checker/reference hashes were checked with data-only hashing.

Adaptations explicitly made:
- Source/text reads and manual structural inspection replace the checker's SDK queries, under this assignment's prohibition on SDK/product execution.
- No claim that the upstream checker is equivalent to the user's installed/customized typed checker.
- Full substantive review is of 59-16. Plans 59-02, 59-15 and 59-17 were read for dependencies/data contracts; all 18 published plan frontmatter excerpts were obtained for phase graph, ownership and requirement-declaration checks. The other plans' full task bodies were not all reviewed; phase-wide task coverage is therefore not certified.
- Current-source copies are read as interface/compatibility evidence, not credited as an implemented strict mode.
- Earlier reviewer conclusions were not used as evidence that this plan passes.

Project documentation was independently retrieved at e0129c083c0bbb4daee1e5440e6a3a66abfc79b8, then rechecked at the candidate's newer exact commit e9c78d47f2e1a191e7675168223c7b2bf084aa9c. ROADMAP, PROJECT, REQUIREMENTS, CONTEXT, RESEARCH, PATTERNS, VALIDATION and plans 02/17 were byte-identical across those pins. Local copies were verified against their returned Git blob identities. Current pinned tree 930ebad4f13a17502f7702670fbb78348dbb8019 contained 1,752 entries and was not truncated. This is a pinned source observation, not a live runtime/graph/adoption claim.

See FETCH-PROVENANCE.json and INPUT-SHA256SUMS.txt for the exact source identities and input hashes.

## 2. Artifacts and precedence

| Input | Identity / role |
|---|---|
| published-plan16.txt | SHA-256 4a42694f23faaf047ab27c72a24636708fa0ab604da79dd41342b870f8ba6ce3; historical published plan, unchanged |
| received-r2-plan16.txt | SHA-256 082633222362d531df514bbc5fcb9df4004db8a6acf094e7e360635ed08a101a; complete R2 proposal |
| current-source addendum | SHA-256 ecd2ca8d2495d5d83cd303111cb9114aaa5f0f5e8ee628ec5ba2aa40ed73d9c4; proposed contract, not admitted |
| frozen controls | SHA-256 c34edb7ed52546099383cd126a947adac7c5aaf3909a55c7527fef422834a617; C01-C24 and N01-N12 prospective oracles |
| PATCH-DESIGN.md | SHA-256 1cc93e18c5501c60eff33621bc3c0dd2aa0500fffea6d10e3391620f794761e9; proposed function/data/rendering choices, not accepted |
| scripts/analyse_arms.py | Git blob 6eec4a6a618721326736b86b50b8c5b57030507b; SHA-256 838185ed5dd7f5c4e13b1fa6b1965a5217b2097f333c901a663e3f6a0f589ad0 |
| tests/test_analyse_arms.py | Git blob b62db8096e56c86e896541757eeae0d8a3ea1272; SHA-256 3d4be2327417fc722902968968960e0148e8584530c45c39fb6447422c79d80e |

The addendum says it supplements R2 and explicitly requires coordinator disposition of its dependency interpretation and arithmetic/exit/F1 choices. A useful design proposal is not itself evidence that those changes have replaced the plan's executable metadata.

## 3. Goal-backward requirement analysis

The active Phase 59 goal is to provide bounded source/call/experiment evidence, make observable attempts/failures inspectable, and distinguish delivered capability from accepted quality. The roadmap's phase requirements are P59-CLOSE and INV-01 through INV-08. Phase 60 owns EVAL60-01/02.

59-16 claims only P59-CLOSE. Its proper contribution is an honest matched arithmetic tool for the B4 report; it does not alone deliver calibration, B3 instrumentation, restored history, complete investigation capabilities, or phase closure. PROJECT.md's real-bug corpus requirement does not prohibit synthetic unit-test known answers, provided they are never relabeled as the historical evaluation corpus.

| 59-16 obligation | Planned coverage | Finding |
|---|---|---|
| Same selected IDs on both arms | R2 tasks 16.1/16.2; addendum 5; C05-C11/C22/C23 | Concrete, including all-150 requirement and external selected-list authority |
| Matched depth/engine/backend | addendum 5.3; C08/C09 | Concrete typed checks; backend alias does not certify configuration |
| Preserve incomplete and invalid outcomes | addendum 5.4; C02-C10 | Exit 1 incomplete; exit 2 invalid; no incomplete paired deltas |
| Independent known-answer metrics | frozen eight/20/150 oracles; C01/C11/C15-C17/C22 | Arithmetic independently checked below |
| Physical raw citations | strict loader plus row-index/line-map composition; C12-C14 | Composition explicitly tested, legacy compressed loader excluded |
| Source/config/label provenance | fixed unverified labels; external 02/17 qualification; C18-C20/C23 | Honest limitation, not an analyser provenance validator |
| Legacy finding-level preservation | existing helpers/default branch unchanged; C21 | Specific compatibility boundary |
| Completed sibling evidence cannot complete timed-out parent | retained R2 paragraph; C19 | External fixture evidence + incomplete analyser outcome; no producer-credit claim |
| Consumed graph versus canonical-only metadata | retained R2 paragraph; C20 | Separate evidence disposition; analyser remains unverified in both |
| Historical acceptance claim | existing 02/17 contract | Not delivered by this proposal; remains pending |

All nine Phase 59 requirement IDs appear in at least one of the 18 published plan frontmatter excerpts. That satisfies the declaration-presence subcheck, not all-plan substantive implementation coverage. No missing INV requirement is wrongly charged to 59-16 alone.

## 4. Findings

### B1 — BLOCKER — Dependency correctness / contradictory executable scope

Evidence:
- received-r2-plan16.txt:5-6 declares wave 2 and depends_on ["59-02"].
- Its task 16.1 required reads include closeout/BINDING.json and recovered historical material.
- Addendum:17 proposes independent synthetic capability before historical evidence, explicitly says the dependency is not deleted, and requires coordinator acceptance.
- Addendum:204 lists that dependency interpretation among still-required dispositions.
- 59-17-PLAN.md:5-6 remains wave 8, depends_on ["59-02", "59-15", "59-16"]; its historical-use entrance requires binding/reconstruction evidence.

Impact: the currently combined packet has no single executable dependency interpretation. An executor cannot follow the original required reads/dependency and simultaneously treat them as unnecessary for offline-first work. Proceeding by silently treating a proposal as an accepted exception would bypass the plan's own control.

Required revision:
1. Keep plan 59-16 as the synthetic arithmetic capability, with precise synthetic-only must-haves and no historical-artifact required reads.
2. If the coordinator selects that split, use depends_on [] and wave 1 for the capability; do not mark 59-02 complete.
3. Leave historical-use qualification in existing 59-17, preserving its dependencies on 59-02, 59-15 and the arithmetic capability. Update its consumer key link if needed.
4. Do not create a new historical 16B -> 17 edge while retaining 17 -> 16B. A conceptual historical-use subsection belongs within 17, not a reciprocal dependency.
5. Preserve all source/runtime/checker/PBR/review gates; this revision changes scope allocation, not authority or historical thresholds.

### B2 — BLOCKER — Research resolution under pinned upstream Dimension 11

Evidence: 59-RESEARCH.md:621 is "## Open Questions", without "(RESOLVED)". Items at 623-625 have no inline RESOLVED status:
1. Endpoint capability evidence: qualify selected Chat tool dialect on exact configured route.
2. Worker deployment qualification: qualify the selected namespace/seccomp/resource/cleanup profile.
3. Historical acceptance: source/config/label binding and B3/B4 restoration or reconstruction remain plan items.

Pinned Dimension 11 explicitly fails unresolved questions. This is a documentary resolution failure, not evidence that endpoint or worker capability has failed. Questions 1/2 are outside the arithmetic slice, while item 3 directly motivates its dependency boundary.

Required revision: explicitly resolve planning choices and assign remaining measured qualification to named producer/closeout plans. For questions 1/2, record selected architecture, responsible plan and fail-closed runtime gate; do not mark actual endpoint/worker evidence successful. For question 3, resolve routing to 59-02/17, retaining all historical unknowns and original 18/20 obligations. A supplemental disposition may resolve the candidate's planning interpretation without rewriting a dated snapshot, but must be bound into the checked artifact; it is not retroactive research/runtime success.

### W1 — WARNING — Exact task-set and validation-map reconciliation

Evidence:
- R2 contains two fully formed tasks, 59-16.1 and 59-16.2, each with files/action/automated verify/done.
- Addendum:161-176 describes three prose tasks, with exact-composition qualification as Task 3 and one shared future pytest anchor after the task descriptions.
- PATCH-DESIGN.md:285-293 says to follow that three-task order.
- 59-VALIDATION.md:17 and sign-off require final plan/task IDs and waves; its P59-CLOSE row remains generic acceptance-recovery/closeout validation, without this proposed three-task mapping.

Impact: the two original tasks are not structurally incomplete, but the intended replacement task set and task-3 verification boundary have not been normalized into one executable plan. This is a warning rather than falsely claiming the existing two task blocks lack mandatory fields.

Fix: create a single additive checked candidate with three complete typed tasks and explicit automated commands/acceptance/done. Map 16.1 to independent controls and meaningful baseline RED, 16.2 to strict implementation, 16.3 to exact-composition qualification, separate N01-N12 omissions and restored GREEN. Give the validation addendum exact task IDs/wave/commands and keep actual execution results NOT RUN. A candidate that actually promotes an incomplete prose Task 3 to an auto task would incur the checker's mandatory-field blocker.

## 5. Dimension-by-dimension result

### Dimension 1 — Requirement Coverage: SCOPED COVERAGE; PHASE-WIDE TASK COVERAGE NOT CERTIFIED

P59-CLOSE is present in 16 frontmatter. Specific arithmetic/citation/mismatch/exclusion work maps to tasks 16.1/16.2 plus the addendum controls. Historical acceptance remains with 02/17. All nine phase IDs occur in the 18-plan frontmatter catalog. Other plans' entire task bodies are not all reviewed here, so exhaustive Phase 59 task coverage is unavailable. No whole-phase pass follows.

### Dimension 2 — Task Completeness: ORIGINAL TWO TASKS COMPLETE; W1

Both R2 auto/tdd=true tasks contain files, concrete action, behavior, automated verify, acceptance_criteria and done. Verification is an actual pytest command, not an echo/trivial always-green marker. Baseline RED must expose new behavior absence at assertion/public CLI, not import/collection failure. Task 2's expanded R2 acceptance remains binding even though its shorter done text does not repeat it. The three-task additive structure needs W1 reconciliation.

### Dimension 3 — Dependency Correctness: STRUCTURAL GRAPH VALID; B1 BLOCKS PROPOSED REORDERING

Published waves:
- Wave 1: 01, 02, 03
- Wave 2: 04, 16
- Wave 3: 05, 09
- Wave 4: 06, 07, 10, 11
- Wave 5: 08, 12
- Wave 6: 13
- Wave 7: 14, 15
- Wave 8: 17
- Wave 9: 18

All declared references exist in the 18-plan catalog; each dependency is earlier-wave; no cycle or same-wave declared file overlap was found. 16's sole declared dependency 02 is wave 1, consistent with its existing wave 2. Source ownership/public-PR overlap observation is separate from the plan graph and does not prove an acknowledged private claim. B1 concerns the unintegrated offline-first revision, not a nonexistent cycle in the original graph.

### Dimension 4 — Key Links Planned: CONCRETE WITH HISTORICAL BOUNDARY OPEN

Public CLI -> strict ID loader -> both strict ledger loaders -> summaries -> whole-pair validation -> physical-line renderer -> exit is specified. Helpers do not silently call legacy tolerant loader. No numerical output appears before whole-pair validation. Tests exercise subprocess composition, not only helper arithmetic. 17's B4 report -> 16 CLI link exists. Binding authority remains outside the CLI; B1 must reconcile when that external link becomes required.

### Dimension 5 — Scope Sanity: WITHIN DOCUMENTED COUNTS; RUNTIME COST UNMEASURED

Original: 2 tasks, 2 modified product/test files. Proposed: 3 tasks, same 2 files. Both fit the checker's 2-3-task target and remain far below file-count warnings. C01-C24 are parameterized control groups, not 24 separate implementation tasks. Task 3 qualification is substantial but does not introduce a new product subsystem. The claimed 35-45% context estimate is a planning estimate, not measured resource evidence; no fabricated context-budget percentage was assigned.

### Dimension 6 — Verification Derivation: SUBSTANTIVE, SUBJECT TO B1/W1

Truths are user-observable: same IDs/coordinates, visible exclusions, raw-line traceability, no false successful comparison, n/a uncertainty. The strict path's 0/1/2 exits, fixed provenance status and complete references make those claims falsifiable. The supplemental public-helper and renderer design improves specificity without being credited as implemented. Matched historical evidence truth must not be conflated with a structurally complete synthetic arithmetic result.

### Dimension 7 — Context Compliance: RELEVANT DECISIONS PRESERVED; NO FULL-PHASE 100% CLAIM

- D-01: preserves original 18/20, historical failures and 59-02/17 qualification.
- D-05/D-06: source refresh and graph/runtime gates remain; old source pins are not today’s execution authority.
- D-07/D-08: existing authorization and exact-site tests/coverage/independent reviews/signed delivery remain.
- D-03/D-04: R2 sibling and graph-evidence distinctions retained; incomplete remains incomplete.
- D12/D14: R2 explicitly requires actual public Forge review/CLI-CI-MCP rotation and separates setup, paid attempt, process completion, verdict and gate qualification.
- D13: refreshed source/ownership and stop-on-drift are preserved; no force write or import of foreign changes.
- D-02/D-09/D-10/D-11 describe other phase capabilities or broader work; 16 does not claim to implement them.
No provider runs, new tool adapter, phase50/53b expansion, config/key/memory sync, or phase60 quality comparison is added. Full phase-wide decision coverage cannot be inferred from a scoped plan review.

### Dimension 7b — Scope Reduction Detection: NO CONFIRMED SILENT REDUCTION; SPLIT MUST BE EXPLICIT

C19/C20 are explicitly evidence-boundary tests, not provider capture or graph-consumer implementation. This respects R2's wording that these checks consume upstream evidence and authorize no producer/schema changes. A producer requirement would require a separately reviewed interface, not silently moving it into tests. Arithmetic-only output never claims B4 acceptance. The offline-first proposal is explicit but not yet reconciled (B1). It can be made sound by a documented capability/qualification split preserving 17.

### Dimension 7c — Architectural Tier Compliance: NO IDENTIFIED MISMATCH FOR THIS SLICE

RESEARCH.md:43-55 contains the responsibility map. Its rows allocate snapshot/broker/worker/journal/receipt authority, not an analyser service. 16 remains a local offline consumer; it does not put acceptance, graph provenance, model authority or provider dispatch into the report renderer. This is not a certification of the other phase tiers or measured worker isolation.

### Dimension 8 — Nyquist Compliance: APPLICABLE; TWO-TASK SUBCHECK SATISFIED, PHASE MAP UNQUALIFIED

RESEARCH.md includes Validation Architecture; VALIDATION.md exists. Private config.json is unavailable; no explicit disabled flag was found, so no skip is claimed.

| Task | Published wave | Automated command | Documentary assessment |
|---|---:|---|---|
| 59-16.1 | 2 | PYTHONPATH=src /usr/bin/python3 -m pytest -q tests/test_analyse_arms.py | Present; behavior/CLI meaningful-RED contract |
| 59-16.2 | 2 | PYTHONPATH=src /usr/bin/python3 -m pytest -q tests/test_analyse_arms.py | Present; strict/legacy/asserted counts |
| Proposed addendum Task 3 | not separately typed | shared future anchor only | W1; give exact task-level mapping |

No watch flag, E2E-only suite or explicit MISSING test reference is used in 16. Existing test file is the anchor and Task 1 extends it; no fabricated Wave 0 stub is required. Sampling for the original pair is 2/2 automated. Latency is unmeasured; under-30-second success is not asserted. VALIDATION retains status draft, nyquist_compliant false and wave_0_complete false. No phase-wide Nyquist pass is awarded.

### Dimension 9 — Cross-Plan Data Contracts: COMPATIBLE CONSUMER DESIGN, EXTERNAL QUALIFICATION OPEN

Current ResumeKey has entry_id/depth/engine/backend; make_record adds verdict/runs/caught/wall_s and optional findings/skipped fields. Current corpus label authority is expected_verdict, not arbitrary suffix inference. The addendum limits suffix labels to frozen selected fixtures/historical label bindings.

59-15 proposes additive telemetry and unchanged ResumeKey. 16 does not require those proposed fields, ignores optional telemetry for scoring, leaves rows/bytes unchanged and never mutates a shared schema. It rejects duplicate/resume rows rather than copying the producer's last-wins semantics, explicitly consuming a frozen analysis input rather than an active resume log. 17 must supply a frozen unambiguous view or retain a failure; the analyser must not silently normalize it. 17's source/config/label/selection checks remain independent. No incompatible transform or loss of raw data is planned.

### Dimension 10 — CLAUDE.md Compliance: SKIPPED (no CLAUDE.md found in available project view); PRIVATE RULES UNVERIFIED

Root CLAUDE.md fetch returned 404 and the complete public tree lacks it; PROJECT.md states CLAUDE.md/.planning are local-only. No CLAUDE.md or project skill directories were present in the isolated candidate directory. The historical research recites some private rules but does not establish today's actual private instructions. Its visible rules (English docs, ASCII code, existing pytest, no installs, read-only graph during planning, independent reviews) are compatible with the proposal. Exact selected-runtime rules/import/graph evidence still require later authorized entrance. Public absence is not proof of private absence.

### Dimension 11 — Research Resolution: FAIL (B2)

All three Open Questions remain unmarked in the pinned published research. Minimal resolutions distinguish resolved planning decisions from unresolved measured qualification, with exact ownership/routing and no false historical pass.

### Dimension 12 — Pattern Compliance: NO APPLICABLE FILE-CLASSIFICATION ROW; COVERAGE LIMIT DISCLOSED

PATTERNS.md:18-46 has 23 candidate file rows but does not classify scripts/analyse_arms.py or tests/test_analyse_arms.py. No nonexistent prescribed analog is claimed. Applicable shared concerns are preservation of raw data, unknown-versus-zero and separation of observed data from authority; the addendum and controls address them. Source-bound analog is the existing script and its existing import-by-path tests, explicitly read and preserved. Expanding the phase pattern map is advisable in the additive candidate, but omission of an analyser-specific row is not mislabeled as violating a row that does not exist.

## 6. Independent arithmetic and adversarial findings

Recomputed by inspection, without running product code:
- Eight-entry left: TP3/FN1/FP1/TN3 -> P=3/4, R=3/4, F1=3/4.
- Eight-entry right: TP2/FN2/FP2/TN2 -> P=1/2, R=1/2, F1=1/2. Right-minus-left = -1/4 for each.
- C11 left: TP6/FN4/FP2/TN8 -> P=3/4, R=3/5, F1=2/3.
- C11 right: TP5/FN5/FP1/TN9 -> P=5/6, R=1/2, F1=5/8. Deltas = 1/12, -1/10, -1/24.
- C22 left 60/15/15/60 -> all metrics 4/5; right 45/30/30/45 -> all 3/5; delta -1/5.
- C02 excluding left D-clean removes one TN, not any precision/recall contributor; diagnostic metrics remain 3/4 while scored7/intended8 and exit1 remain explicit.
- C15 undefined denominators and C16 defined P=R=0 -> F1=0 are internally consistent with the proposed new-mode rule. Legacy F1 behavior is not silently changed.

The strongest constraints are honest provenance and exact citation composition. Planned counterexamples address missing/duplicate IDs, bool depth, mixed backends, post-selected malformed rows, incomplete pairs, unselected records, physical LF coordinates, full reference lists, outcome/path/control-line injection and replacement of an externally frozen ID list. The design explicitly covers argparse error escaping and does not claim CLI access to a provenance manifest it never consumes.

No confirmed arithmetic or raw-coordinate defect was found in the proposed contract/design. This statement is not a passing test result. Exact stdout snapshots, parser-overflow handling choice, implementation lines, omission selectors and runtime command results are not yet frozen/executed code.

## 7. Unavailable inputs and claims not established

- Complete private 581-line eight-pass PBR specification; therefore no PBR pass.
- Actual installed/custom typed checker invocation/result; documentary upstream application is not substitution.
- Current selected execution environment, its CLAUDE/AGENTS/skill rules, interpreter/import closure, private source-bound graph and runtime budget.
- Actual historical BINDING.json, frozen corpus/labels/source/config and original d1/B3/B4 ledgers for this review. Public tree absence is bounded, not a search of historical hosts/archives.
- Current private ownership acknowledgment and integration/publisher disposition. The candidate's 37-PR zero-overlap snapshot is evidence of that public check only.
- Actual source changes, RED/GREEN, collection/pass/skip counts, executed changed lines, independent omissions/restoration, native review, CI, merge/adoption/hygiene or historical acceptance.
- Full substantive review of every other phase plan. Frontmatter graph/coverage checks do not replace that work.

No future test/fixture list is reported as already run. No fixed status labels grant external binding or native-clean credit.

## 8. Structured issues

```yaml
issues:
  - id: B1
    plan: "59-16"
    dimension: dependency_correctness
    severity: BLOCKER
    description: "Offline-first addendum conflicts with the still-operative 59-02 dependency and historical required reads."
    evidence:
      - "received-r2-plan16.txt:5-6, task 59-16.1 read_first"
      - "59-16-CURRENT-SOURCE-ADDENDUM.md:17,204"
      - "59-17-PLAN.md:5-6 and dependency_contract"
    fix_hint: "Normalize 59-16 as synthetic-only capability; preserve historical qualification in 59-17 with 59-02/15/16 prerequisites; do not create reciprocal 16B/17 dependencies."
  - id: B2
    plan: null
    dimension: research_resolution
    severity: BLOCKER
    description: "Published research has three Open Questions without RESOLVED planning dispositions."
    evidence:
      - "59-RESEARCH.md:621-625"
    fix_hint: "Resolve planning architecture/routing explicitly, assign measured endpoint/worker/history qualification to responsible plans, and keep actual evidence unknown until measured."
  - id: W1
    plan: "59-16"
    dimension: task_completeness
    severity: WARNING
    description: "Two typed R2 tasks and three additive prose tasks are not consolidated into one exact task/validation map."
    evidence:
      - "received-r2-plan16.txt tasks 59-16.1/59-16.2"
      - "59-16-CURRENT-SOURCE-ADDENDUM.md:161-176"
      - "59-VALIDATION.md:17 and Validation Sign-Off"
    fix_hint: "Bind a complete three-task candidate with per-task automated verify/done and C01-C24/N01-N12 mapping; preserve NOT RUN results."
```

## 9. Recommendation and revision-loop handling

Return this feedback to the planner. A sound bounded revision can resolve B1/B2/W1 without changing historical acceptance thresholds or expanding the two-file product scope. Preserve the original/R2 snapshots, bind the additive revision and exact dispositions, and recheck the revised input independently.

This is the first issue baseline for this specific pinned-documentary review. It does not retroactively count prior informal reviews as clean cycles. Apply the upstream bounded revision/escalation rule to actual subsequent review results; no automatic acceptance is inferred from progress or issue-count reduction. Even a clean documentary recheck would leave PBR, installed typed-checker equivalence/invocation, runtime and native-review gates separately unestablished.

