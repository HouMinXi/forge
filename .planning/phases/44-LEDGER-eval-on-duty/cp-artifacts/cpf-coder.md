# CP-FINAL Convergence Review -- Phase 44 (coder / executability axis)

**Reviewer:** coder (kanban t_8b59a4e6)
**Date:** 2026-08-22
**Scope:** FINAL convergence check on the Phase 44 plan set
(44-CONTEXT.md / 44-01 / 44-02 / 44-03) after CP1 (PASS B=0) and CP1b
(kimi B=3, deepseek H=1, gemini B=2). All 6 blockers adjudicated and
fixed; this review confirms each fix against live ground truth and
sweeps for residual executability gaps. Ground truth re-grepped against
main @ 8bd01bc: src/code_forge/{machine,ledger,gate_check,coverage,
advisory,reviewer_json,cli,outlet_resolver,mcp_server}.py and
eval/{corpus,runner}.py. No file modifications made.

---

## Per-blocker verdicts (with live-code evidence)

### 1. kimi B-1 -- style findings ejected from data model (D-27)
**CONFIRMED-FIXED**

Live: advisory.py:26-36 -- AdvisoryFinding docstring lists `fingerprint`
under "Fields intentionally excluded" (line 34). Rerouting would have
ejected style findings from ledger/adjudicate/export permanently.

Fix: CONTEXT D-27 (lines 241-254) -- style findings "STAY StateFindings
(keep their fingerprint) but are recorded with a NON-BLOCKING
disposition, NOT rerouted to AdvisoryFinding". 44-03 Task 2 test (d)
(lines 226-234), action (lines 263-274), acceptance (lines 287-291)
all pin fingerprint-preserved StateFinding with bug-injection.
44-03 frontmatter (line 22), artifacts, and key_links (lines 43-46) all
carry the fixed design. Non-blocking confirmed: verdict counts
`_count(Disposition.CONFIRMED)` + `_count_coverage_gaps` only
(machine.py:526-531, verified live).

### 2. kimi B-2 -- kill-switch via load_gate_config dead in review mode
**CONFIRMED-FIXED**

Live: gate_check.py:64-71 -- `load_gate_config` raises
`ValueError("gate.yaml needs an active 'test' section...")` when
`"test" not in data` (verified live). outlet_resolver.py:125-143
precedent: `load_outlet_from_gate` "Does NOT call load_gate_config"
and uses `yaml.safe_load` -- the same pattern the fix mandates.

Fix: CONTEXT D-19 (lines 171-183) documents the loader constraint.
44-01 Task 2 test (g) (lines 228-234) + action (lines 277-282) +
acceptance (lines 300-303, 317-318): kill-switch config read is
`yaml.safe_load + dict.get`, INSIDE the try, with a grep acceptance
("NO load_gate_config call in the kill-switch path") and bug-injection
("route the config read through load_gate_config, watch test (g) FAIL").
44-03 Task 2 action (lines 241-245) routes pinned_paths/style_downgrade
through the same tolerant read. Remaining load_gate_config mentions in
the plans are interfaces/read_first context or negations -- grep-clean.

### 3. kimi B-3 -- mutation-survivor FAIL (:360) skipped ledger write
**CONFIRMED-FIXED**

Live: machine.py:351-360 -- `status == "done"` + survivors ->
`verdict = Verdict.FAIL; converged = False; infra_errors.append(...);
_persist_state(); return Verdict.FAIL` (verified live). The normal CI
terminal with the ledger-write insertion point is at :538-542. The :360
return bypasses :541. PENDING returns at :381/:391 (verified live) are
non-terminal and correctly excluded.

Fix: 44-01 Task 2 test (g2) (lines 235-239), action (lines 251-256:
"Call it from EVERY CI terminal exit ... the normal terminal at :541
AND the mutation-survivor FAIL return at :360 -- funnel both through
the single write call"), acceptance (lines 296-297).

### 4. gemini B-1 -- pinned_paths did not suppress COVERAGE findings
**CONFIRMED-FIXED**

Live: machine.py:1635-1645 `_count_coverage_gaps` counts
`f.source == "COVERAGE" and f.disposition != Disposition.DISMISSED`
SEPARATELY from CONFIRMED (verified live). coverage.py:100-113 generates
COVERAGE findings as `disposition=Disposition.UNCERTAIN` (verified
live), so a pinned-path COVERAGE finding would stay UNCERTAIN and keep
coverage_gaps > 0 -> FAIL. Setting DISMISSED removes it from the count.

Fix: 44-03 Task 2 test (b2) (lines 219-222) + action CRITICAL note
(lines 256-264: cites machine.py:1635-1643 and coverage.py:100-115,
mandates setting pinned-path COVERAGE findings to DISMISSED or routing
pinned_paths into the coverage exempt filter) + acceptance (lines
285-286). Same DISMISSED mechanism as Task 1 suppression -- consistent
with both count predicates.

### 5. gemini B-2 -- ESCAPED base==head -> empty diff false-green
**CONFIRMED-FIXED**

Live: cli.py:1619-1622 (verified live): when both SHAs omitted,
`head_sha = _git_head(cwd); base_sha = head_sha`. An ESCAPED row so
created materializes `git diff X..X` = empty; replay yields PASS while
expected_verdict=HOLD -> MISSED forever.

Fix: 44-02 Task 1 test (f) (lines 164-170): extractor skips
base_sha==head_sha / empty-diff rows under a dedicated empty-diff
counter ("check base_sha != head_sha AND the produced diff is
non-empty -- never emit an expect-catch entry for an empty diff").
Counter integration now complete: 44-02-PLAN.md line 24 must_haves
enumerates all five skip reasons with precedence; Task 1 test (d)
(lines 155-160) covers the two CP1b counters' precedence slots; Task 3
test (a) (lines 302-303) summary string lists all six counters.

### 6. deepseek H-1 -- DUPLICATE mapped to expect-no-catch penalizes real bugs
**CONFIRMED-FIXED**

Fix: CONTEXT D-02 (lines 40-47): "DUPLICATE -> EXCLUDED from export ...
skipped under their own counter, carrying no independent signal".
D-13 (line 126): "DUPLICATE -> excluded from export".
44-02-PLAN.md line 21 must_haves: "DUPLICATE rows are EXCLUDED (deepseek
H-1 -- a real bug, reported twice)" -- correct mapping.
Task 1 test (a) (lines 144-149) and action (line 176:
"Classification per D-02/D-13 (FIXED/ESCAPED -> expect-catch ...;
DISPROVED -> expect-no-catch; DUPLICATE -> excluded)") both carry the
fixed mapping. Full grep across all three plans + CONTEXT: no stale
DUPLICATE->no-catch mapping remains. (44-03's use of DUPLICATE as a
suppressing terminal state at D-23/D-24 is a different, correct axis.)

---

## Executability sweep (coder axis)

### Wiring points verified live
- **StateMachine constructors**: cli.py:3303 and mcp_server.py:928 both
  exist; cli.py constructor already receives
  `coverage_exempt_patterns` (:3317) -- the precedent flow for
  pinned_paths wiring is real. mcp_server.py:928 constructor passes NO
  config-derived kwargs (verified live: falsifier/autofixer/
  resolved_review/source_hash/baseline only), so 44-03's statement "the
  MCP constructor currently passes NO config path list -- that wiring is
  part of this task" is accurate and test (c) pins both sites.
- **Fingerprint recipe**: reviewer_json.py:168-170 --
  `sha256("%s:%d:%s" % (file, line, desc))[:16]`, description carries
  the `[pass_name] ` prefix (:179) -- matches 44-03's style_downgrade
  table-driven keyword-match design (pass name present in the desc
  string the fingerprint covers).
- **_run_ci insertion points**: verdict determination at :526-542
  (verified live) -- suppression pass inserts before
  `_count(Disposition.CONFIRMED)` at :526; ledger write inserts after
  `_persist_state()` at :541. Both anchors real.
- **export_eval call site**: _run_ledger dispatch at cli.py:1458 with
  argparse at :781-820 (verified live in prior CP rounds; mark at
  :1474, list at :1661). eval/export.py is a new file -- no collision.
- **LOCAL writer DISMISSED->DISPROVED mapping**: machine.py:1330-1334
  (verified live). 44-01 Task 2 action (lines 263-269) explicitly
  forbids routing style findings through this mapping and mandates the
  per-finding terminal-state decision as an INPUT to the shared builder
  (CP1 W-5, resolved).
- **D-18 coupling**: 44-02 Task 1 fixture = real StateMachine CI run +
  adjudicate (lines 135-138) with grep acceptance against hand-written
  JSONL (line 196). 44-03 verification carries the reciprocal coupling
  proof. Honest bidirectional coupling.
- **axis_tags**: corpus.py:76 -- `axis_tags: list[str]` free-text
  (verified live); 44-02 interfaces correctly state free-text, not
  enum. load_corpus ignores unknown keys (verified :106-128), so
  eval-bank-compat extras ride without breaking the load.
- **_create_gate_yaml merge hazard**: runner.py:522-560 (verified live) --
  existing non-backend keys WIN. 44-02 Task 2's emission-side strip of
  foreign `.code-forge/gate.yaml` sections from the diff is the only
  in-scope resolution (replay lives in runner.py, outside
  files_modified); the hostile-test.command test (d) is a real-path
  check. Accepted tradeoff documented in the module docstring per the
  plan.

### Acceptance-criteria testability
Every new test (44-01 a-h incl. g/g2, 44-02 a-f, 44-03 a-e incl. b2/c/d)
is concrete and automated, with bug-injection pins on the fix behaviors
(truncation, try/except, kill-switch, metadata inheritance, cat-file
validation, counter exclusivity, style-fingerprint assertion,
mcp_server wiring). No new unverifiable acceptance criteria found.

### Task independence
No task requires a judgment call the plans do not make. CP1's W-5 (CI
write scope for non-blocking style findings) is resolved in 44-01 Task 2
(lines 259-269 + acceptance 315-316). D-06's planner choice is recorded
(keep enum name, rewrite docstring -- 44-01:166-170). D-14's fallback is
recorded (skip-with-warning -- 44-02:227).

---

## Residual concerns

### [L-1] Diff truncation cap vs ledger-row content interplay (LOW)
D-07 caps `evidence` at 500 chars (machine.py `f.error` / mark
`--evidence`), keeping rows under PIPE_BUF. `description` (the
fingerprint payload, reviewer_json.py:179) is NOT capped and rides the
in-memory StateFinding only -- it does NOT go into the ledger row
(LedgerRow has no description field; evidence_class is the only
free-text field, and it is capped). Verified: no row field other than
evidence_class carries unbounded text (ledger.py:40-60 schema, fields
are sha/fp/file/int/claim -- all bounded in practice by SHA=40,
fp=16, paths). [COMPUTED/HIGH] No action needed; noting so the executor
does not waste a pass looking for a description cap.

### [L-2] 44-03 files_modified still lists advisory.py (LOW)
44-03-PLAN.md files_modified (line 7) does not list advisory.py -- the
frontmatter is clean. However Task 2 `<files>` line 204 still lists
`src/code_forge/advisory.py` and read_first line 208 lists it. Under
the revised D-27, advisory.py needs NO change (44-03 interfaces
line 124 states this explicitly). The Task 2 `<files>` entry is a
harmless read-context artifact, but a strict executor might add a
no-op touch to advisory.py to satisfy it. One-word fix (drop it from
Task 2 `<files>`) or leave as-is; the acceptance criteria pin the real
behavior regardless. Not blocking.

### [M-1] resolve_ledger_root does not yet exist -- 44-02/44-03 interfaces reference it as if present (MEDIUM, informational only)
44-02 <interfaces> line 105 and 44-03 <interfaces> line 105 read
"resolve_ledger_root(cwd) -- from 44-01 Task 1" -- correct, since both
plans declare `depends_on: [44-01]` and branch from main after 44-01
merges. Executors must not attempt 44-02/44-03 before 44-01 lands.
The dependency is explicit in the frontmatter (44-02:6, 44-03:6), so
this is a sequencing reminder, not a gap. [KNOWN -- frontmatter
dependency declared]

---

## Verdict

All 6 CP1b blockers are CONFIRMED-FIXED with binding-layer evidence
(CONTEXT decisions + task behavior/action/acceptance) verified against
live ground truth at main @ 8bd01bc. The five CP1 convergence warnings
(W-1..W-5) are all resolved in the current plan text (verified by
grep: frontmatter fixed, counters enumerated, DUPLICATE mapping clean,
active_findings note corrected, write-scope sentence present). No new
blockers. Residual concerns: one MEDIUM (sequencing reminder, covered
by declared depends_on) and two LOW (documentation/no-op artifacts).

NO OBJECTION from coder.

SCORECARD: B=0 H=0 M=1 L=2
