## R4 — final confirmation of the two text-only micro-fixes (Task 5 <action> + <verify>)

Scope honored: I re-verified ONLY the two edits against the live plan file
(54-01-PLAN.md Task 5, lines 632-663), plus the cross-references needed to
judge them (Task 2/4 import wording, Task 4 verify command,
54-VALIDATION.md quick-run, doctor.py/cli.py live import style, my own R3
findings). The rest of the plan was NOT re-reviewed.

### Edit 1 — Task 5 <action> now writes the import explicitly (line 639-641)

Live text: "_check_backends: add `from .backend import probe_backend_live`
to doctor.py's existing function-local import block (match the style at
doctor.py:110/126/128/168)."

Verified:
- The cited line anchors are real function-local imports in doctor.py:
  :110 `from code_forge.trust import trust_status`, :126 `from
  code_forge.backend import load_backend_configs, probe_backend`, :128
  `from code_forge.user_config import ...`, :168 `from
  code_forge.outlet_resolver import resolve_outlet`. The import block the
  edit refers to (the `_check_backends` block at :126-128) exists.
- Consistency with sibling tasks: Task 2 (plan line 290) writes
  `from .workspace import resolve_workspace` — relative style, matching
  cli.py's actual `_run_trust` relative block (`from .trust import ...`,
  cli.py:1306-1314). Task 4 (plan line 530) writes "in the same
  function-local import from .llm_invoke" — also relative. Task 5's new
  `from .backend import ...` now follows the same plan-wide convention.
- One nuance I checked and adjudicate as NOT a finding: doctor.py's own
  existing function-local imports use ABSOLUTE style (`from
  code_forge.backend import ...`, doctor.py:126), so "match the style at
  doctor.py:110/126/128/168" literally points at absolute-import lines
  while the statement itself is written relative. Both forms resolve
  identically at runtime inside the `code_forge` package, the plan's own
  convention across Tasks 2/4/5 is the relative spelling, and the
  parenthetical's clear intent is "function-local, one per line" placement
  rather than absolute-vs-relative. An executor copying either form
  produces working code. This is a pre-existing plan-wide stylistic
  shorthand (Task 2 has the same shape vs cli.py's mixed absolute/relative
  usage), not introduced by this edit, and below the LOW bar for a plan
  document whose Tasks 2/4 already establish the convention.

### Edit 2 — Task 5 <verify> appends tests/test_mcp_server.py (line 662)

Live text: the second chained command now ends
`... tests/test_schema_corpus.py tests/test_mcp_server.py -q`.

Verified:
- Matches 54-VALIDATION.md:46 ROUTER-04 verification command, which
  includes test_mcp_server.py (for the whitelist-negative witness).
- Matches Task 4's verify (plan line 560), which includes
  test_mcp_server.py.
- Task 5's <files> (line 594) does not add test_mcp_server.py as a MODIFIED
  file — correct: the file is only RUN here as a regression witness
  (plan line 571 confirms it is "one of the five task files" for Task 4,
  and Task 5 behavior (d) depends on the whitelist consumer at
  mcp_server.py:958-960 staying green). Including it in the verify run but
  not in <files> is internally consistent.
- The first half of the chain (`pytest tests/test_doctor.py -q &&`) is
  unchanged; only the broad subset gained one file.

### (a) Textual correctness

- Both edited lines are well-formed prose inside their tags; tag balance
  in the Task 5 region verified mechanically (opens==closes for every
  element type in the action/verify span).
- Note on the whole-file XML parse: a strict ElementTree parse of the
  `<tasks>` block fails at a PRE-EXISTING bare `<` in Task 1's text
  ("`-newer <a marker file`", plan line ~231 inside the block) — unrelated
  to these edits, present in the R3-reviewed text, and harmless for the
  plan's consumers (the gsd-executor reads these as structured text, not
  strict XML; the same parse failure existed before this round).
- No dangling references: the doctor.py line anchors and the
  test_mcp_server.py reference both resolve to live content.

### (b) No contradiction with the plan or my R3 findings

- R3 (cp1b-r3-kimi.md) found B=0/H=0/M=0/L=0 on taxonomy, truncation
  ordering, trust hashing, env hygiene, replace() copy semantics, and
  ROUTER-02..05 coverage. Neither edit touches any of those surfaces:
  edit 1 makes an already-asserted import explicit (the <behavior> patch
  rationale at lines 604-607 already presupposed this exact import); edit
  2 widens a test invocation to match VALIDATION.md. Both REDUCE
  plan-internal inconsistency rather than add any.
- The import statement also closes the only gap between Task 5's
  <behavior> ("doctor.py imports it function-locally") and its <action>
  (previously silent on where the import comes from) — the gemini flag was
  real and the fix lands it.

### (c) No new issue introduced

- Edit 1 introduces no new claim beyond what <behavior> already asserted;
  the relative-vs-absolute nuance above is inherited plan convention, not
  new.
- Edit 2 adds one test file to a pytest command line; the file exists
  (tests/test_mcp_server.py, referenced at plan lines 20/404/442/560/571)
  and adds no new dependency, fixture, or ordering assumption.

Both fixes are correct, minimal, and consistent. No BLOCKER, HIGH, MEDIUM,
or LOW findings.

SCORECARD: B=0 H=0 M=0 L=0
