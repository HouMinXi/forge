# Phase 14 Plan 03 Summary

## Status

COMPLETE (pending main session final verify).

## What was implemented

### outlet_c.py (NEW, ~110 lines)

Outlet C (subagent) orchestrator. Routes subagent review through the same
StateMachine that Outlet A uses. Key design decisions:

- l1_provider 4-tuple: (findings, excerpts, Usage, duration) -- excerpts flow
  through the l1_provider channel, not post_round_hook. No _round_state needed.
- falsifier defaults to build_falsifier("auto") -- uses RealFalsifier when
  falsify_real.py is present (same as live Outlet A path). NOT StubFalsifier.
- validate_reviewer_json imported from shared reviewer_json.py (DRY with factories).
- Fail-closed: malformed JSON, missing code_excerpts, empty findings+excerpts
  all produce CONFIRMED findings -> dirty round.
- spawn_fn injection point: NotImplementedError ceiling documented explicitly.

### cli.py changes

Two edits:
1. outlet==subagent dispatch (~line 690): replaced `return Verdict.PASS` stub
   with run_outlet_c call using build_falsifier("auto") by default.
2. verify subcommand (line 561): `run_verify(cwd, diff_sha, diff_f)` ->
   `run_verify(cwd, diff_sha, diff_f, diff_text=diff_text)`.
   Activates the hardened path (STEP A/B/C + check 6) on the only live caller.

### factories.py prompt fix

Added to code_excerpts prompt instruction:
  "code_excerpts content must be actual source code lines, not diff format --
  no +/- prefixes, no @@ headers."

Confirmed effective by DOGFOOD: real deepseek-chat output changed from
  content='import os\n+import sys\n...'  (diff markers)
to
  content='x = 1\ny = 2\nz = 3'  (clean source)

### test_outlet_c.py (NEW, 6 tests)

- TestMalformedJsonFailClosed (3 cases): not-JSON, missing findings key,
  missing code_excerpts key all produce non-PASS verdict.
- TestCycleCountingViaStateMachine: 2 dirty rounds then clean -> consecutive
  clean >= 3 confirmed via StateMachine reuse.
- TestReceiptsWritten: >= 3 receipt files produced on convergence.
- TestExcerptFlowIntegration (H3): excerpts flow from reviewer JSON through
  receipt.py to hardened verify. Uses datetime mock for monotonic timestamps.

## Done criteria met

### cli.py:561 flip (grep evidence)

```
src/code_forge/cli.py:561: vr = run_verify(cwd, diff_sha, diff_f, diff_text=diff_text)
```

### DOGFOOD evidence (input-side proof: real LLM emits clean excerpts)

DOGFOOD proves the INPUT side: a real LLM (deepseek-chat) reliably emits
per-hunk code_excerpts whose content is clean source (no +/- diff markers).
DOGFOOD does NOT prove the GATE side -- it reimplemented STEP A/coverage
checks inline and never called run_verify. Gate-side proof is TestHardenedVerify.

Backend: deepseek-chat via build_falsifier("auto") = RealFalsifier
Diff: 3 hunks across 2 files (foo.py x2, bar.py x1)
Real API calls: 3 (qodo/expert/adversarial passes)
Receipts: 3/3 have real code_excerpts
Content clean (no +/- diff markers): True
Coverage: 100% (9/9 diff post-image lines)
DOGFOOD INPUT-SIDE PASS: LLM emits valid per-hunk excerpts with clean content.

### TestHardenedVerify (gate-side proof: verify.py enforcement)

5 tests calling run_verify(diff_text=DIFF) into the hardened branch; each
asserts a hardened-only reason string unreachable from the legacy path:

  test_witnessed_pass           -> r.passed is True (100% coverage, Jaccard skipped)
  test_unwitnessed_hunk_fail    -> "unwitnessed hunk" in r.reason  (STEP A)
  test_diff_marker_content_fail -> "excerpt content mismatch at"   (STEP C, Q2 guard)
  test_low_coverage_fail        -> "< 60%" in r.reason             (check 6)
  test_missing_field_fail       -> "excerpt missing required fields" (STEP 0)

All 5 PASS (verified by main session independent run). Ground-truth proof
that verify.py hardened branch enforces correctly.

### outlet_c.py falsifier (grep evidence)

```
src/code_forge/outlet_c.py:66: from .factories import build_falsifier
src/code_forge/outlet_c.py:67: falsifier = build_falsifier("auto")
```

### pytest (full suite)

1085 passed, 5 skipped, 0 failed (2456.95s / 40:56)

Note: test_outlet_c.py passes StubFalsifier explicitly to avoid RealFalsifier
making API calls during the test suite. This is intentional -- the test proves
the mechanism (spawn_fn -> validate -> l1_provider 4-tuple -> receipt -> verify)
not the real LLM path. Real LLM coverage comes from DOGFOOD (Outlet A).

### Blast radius triage

No tests drive the `verify` subcommand through CLI. Grep of tests/ for
`subcommand.*verify` or `cli.*verify` returns 0 results. Zero test changes
needed. Tests calling `run_verify(diff_text=None)` directly are unaffected
(they exercise the legacy fallback path on purpose).

## Backward compatibility (explicit, not silent)

After the cli.py:561 flip, `code-forge verify` on the live path now requires
receipts to carry reviewer-provided per-hunk code_excerpts. Receipts written
before Phase 14 -- which have no code_excerpts -- will FAIL verify at STEP A
with "unwitnessed hunk: <file>:<start>-<end>".

This is INTENTIONAL tightening of the anti-fabrication gate, not a regression.

Action for pre-Phase-14 receipts: re-run `code-forge review` to produce
receipts with per-hunk code_excerpts, then re-run `code-forge verify`.

## E2E coverage ceiling (honest)

| Path | Status |
|------|--------|
| Outlet A input (factories.py -> llm_invoke -> real LLM -> clean excerpts) | DOGFOOD PASS (deepseek-chat, 3 hunks, clean content) |
| verify.py hardened gate (STEP A/B/C + check 6 enforcement) | TestHardenedVerify PASS (5 cases, hardened-only reason strings) |
| Outlet C mechanism (spawn_fn -> outlet_c.py -> receipt -> verify) | H3 test PASS (synthetic spawn_fn, StubFalsifier) |
| Outlet C + real LLM (spawn_fn calling actual subagent) | NOT TESTED -- cli.py spawn_fn is NotImplementedError stub |

The H3 test proves data flows correctly through outlet_c.py's pipeline.
It does NOT prove a real LLM subagent would produce valid excerpts.
Full Outlet C E2E requires a wired spawn_fn, which is a future phase concern.

## Lesson: verify live routing before "impossible" diagnosis

Prior DOGFOOD failure (reported as "StubFalsifier makes convergence
architecturally impossible") was a wrong diagnosis. The dogfood script itself
hardcoded StubFalsifier while production routes build_falsifier("auto") to
RealFalsifier via falsify_real.py. Lesson saved to global memory:
grep live routing before any "architecturally impossible" claim.

## Files changed (Wave 2)

```
src/code_forge/cli.py        (2 edits: subagent dispatch + :561 hardened flip)
src/code_forge/factories.py  (prompt: no diff markers in code_excerpts content)
src/code_forge/outlet_c.py   (NEW)
tests/test_outlet_c.py       (NEW, 6 tests)
```

## Remedy wave (Phase 14 PASS-WITH-FINDINGS close-out)

Phase 14 was adjudicated PASS-WITH-FINDINGS (not DONE) pending three fixes:

F1 -- Add TestHardenedVerify to tests/test_verify.py (5 cases):
  The hardened branch (verify.py:152-280) was exercised only by the H3
  happy-path test (test_outlet_c.py: a passing scenario with synthetic excerpts)
  before this wave. All failure-path testing called run_verify with
  diff_text=None (legacy branch) -- no test asserted any hardened failure case.
  TestHardenedVerify adds 5 tests that call run_verify(diff_text=DIFF) and
  assert hardened-only reason strings (see TestHardenedVerify section above).
  Result: 11 passed (6 pre-existing + 5 new), verified by main session.

F2 -- Fix verify.py:153 "cost-raiser, not a gate" comment:
  The comment contradicted the cli.py:561 flip's FAIL semantics. Corrected to:
  "per-hunk excerpt witness + content/coverage gate. Returns FAIL on an
   unwitnessed or fabricated excerpt."
  Behavior unchanged; comment now matches gate intent.

F3 -- Remove factories.py "NOT shipped v2.0" stale docstring:
  Line 33 and lines 50-53 claimed RealFalsifier was not shipped. falsify_real.py
  exists; build_falsifier("auto") routes to RealFalsifier. This exact stale text
  caused a wrong "architecturally impossible" diagnosis in a prior wave.
  Corrected to reflect actual behavior. Behavior unchanged.

F4 -- outlet_c.py:67 backend omission note (optional, done):
  Added one-line comment: "No backend passed -- thread one through run_outlet_c
  before Outlet C goes live." No behavior change.

Files changed (Remedy wave):
  src/code_forge/verify.py    (F2: comment fix, line 153-154)
  src/code_forge/factories.py (F3: docstring fix, lines 33+50-53)
  src/code_forge/outlet_c.py  (F4: comment, line 67)
  tests/test_verify.py        (F1: TestHardenedVerify + 5 tests + parse_diff_files import)
