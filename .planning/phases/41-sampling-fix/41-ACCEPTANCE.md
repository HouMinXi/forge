# Phase 41 -- PM acceptance protocol

Written 2026-07-21 before dispatch. This is HOW the PM verifies, distinct
from the plan's Acceptance section (WHAT must be true).

Executor: mimo-pro. Known profile (memory `reference_aicc_model_review_profiles`):
competent executor, UNRELIABLE reporter -- completion claims need PM
re-execution and are never accepted as self-certified. So every item below is
something the PM RUNS, not something the PM READS in a report.

Worktree: `.worktrees/sampling-contract`, branch `fix/sampling-contract`,
base main @ 8e18aa0.

## A1 Scope

    git diff main.. --stat

Expect only: src/code_forge/mcp_server.py, src/code_forge/cli.py,
tests/test_mcp_server.py. Anything else is out-of-scope drift -- revert it
before reviewing further (global rule: check for out-of-scope files after any
agent run).

## A2 D7 -- no focus anywhere

    git diff main.. | grep -i focus

Must be empty. A hit means the executor built the reversed shape.

## A3 Suite, re-run by PM

    python3 -B -m pytest tests/ -q

Zero regressions against the pre-change baseline. Run it here; do not accept
a pasted number.

## A4 The 7 tests are real, not vacuous

Read the new test bodies. For each, ask: what would make this fail? A test
asserting on a mock's return value, or asserting a string the test itself
constructed, proves nothing. Specifically check case 2 asserts on prompt
content actually delivered to the sampling builder.

## A5 Bug-inject, re-executed by the PM  <-- the decisive one

The fix IS a call site, so the inject must be at the call site (Golden Rule 2
as amended). PM does this personally:

1. Delete `contract_spec=contract` at the `forge_review` call site
   (mcp_server.py ~:914)
2. `pytest tests/test_mcp_server.py -k <case2>` -> must FAIL
3. Restore
4. Re-run -> must PASS

If it stays green with the kwarg deleted, the test is testing the builder
directly and the wiring is unproven -- which is the original D5.7 bug still
present behind a green suite. This single check is why the executor's report
is not sufficient.

## A6 MemoryError test asserts propagation

Read case 7. It must assert the MemoryError ESCAPES (pytest.raises) and that
no review result is produced. A test asserting a specific message, or one that
catches and checks a degraded empty digest, has pinned the wrong behavior and
inverts D6.

## A7 Non-ASCII

    git diff HEAD --diff-filter=AM -U0 | grep '^+' | grep -P '[^\x00-\x7F]'

Must be empty.

## A8 Commit hygiene

    git log main.. --format='%B'

No D6/D7, no "Task N", no plan/doc references, no P0-P3, no bullet
inventories. Body states WHY. Author is Minxi Hou <houminxi@gmail.com>, never
an AI co-author line. Contract fix is its own commit (D4).

## A9 Real-path smoke -- and its honest boundary

Golden Rule 3 wants the real path exercised once. Boundary for this phase,
stated rather than glossed:

- CAN be real: `_merge_contract_spec` called directly with backend=None and
  >4KB input; `_safe_load_contract_digest` against a real contracts.yaml on
  disk; the CLI `--contract` path end to end.
- CANNOT easily be real: the MCP sampling transport itself needs a client
  advertising sampling capability. It stays mocked.

So the sampling wiring's real-path proof is A5 (call-site inject), not a live
sampling run. Record this gap in the phase summary rather than claiming a
smoke that did not happen.

## Exit

All of A1-A9 pass, THEN the separate 9-pass review runs (impl != reviewer --
the executor does not review its own change), THEN CP4 briefing. No merge
before that sequence completes.

---

# RESULTS -- executed by the PM 2026-07-21

Delivery under test: branch `fix/sampling-contract`, commits 2edb9d4 +
af665a8, base main @ 8e18aa0. Every line below was run by the PM in this
session; nothing here is copied from the executor's report.

| Item | Verdict | Evidence |
|------|---------|----------|
| A1 scope | PASS | 3 files changed, 278 insertions, 3 deletions. Changed-line counts: cli.py 5, mcp_server.py 62, test_mcp_server.py 214. No drift. |
| A2 D7 no focus | PASS | `git diff main.. \| grep -in focus` empty |
| A3 suite | PASS | 2874 passed, 8 skipped, 5 warnings, 342.74s, exit 0 |
| A4 test quality | PASS with notes | 5 of 8 strong, 3 weak -- see below |
| A5 bug-inject | PASS | two sites, independently injected -- see below |
| A6 MemoryError | PASS | case 7 uses `pytest.raises(MemoryError)` on a `side_effect=MemoryError` digest loader; asserts escape, not a degraded `""` |
| A7 non-ASCII | PASS | added-lines scan empty |
| A8 commit hygiene | PASS with 1 nit | see below |
| A9 real-path | boundary stated, not claimed | see below |

## A5 detail -- the decisive check

The plan predicted one inject site at mcp_server.py ~:914. The delivered code
has TWO sites in the chain, so both were injected separately (Golden Rule 2:
equal-looking coverage collapses the moment you inject at each site).

Site (a) -- `forge_review` -> `_dispatch_sampling`, mcp_server.py:972,
`contract_spec=contract`. This is the literal D5.7 bug location.

    deleted    -> 1 failed, 18 passed
                  test_sampling_e2e_contract_in_prompt
                  AssertionError: assert 'test contract' in ''
                  builder kwargs were {'contract_spec': ''}
    restored   -> git status clean (byte-identical to HEAD)
    re-run     -> 19 passed

Site (b) -- `_dispatch_sampling` -> `build_sampling_l1_provider`,
mcp_server.py:792, `contract_spec=contract_spec`.

    deleted    -> 2 failed, 17 passed
                  test_sampling_e2e_contract_in_prompt
                  test_sampling_digest_loaded_from_workspace
                  AssertionError: assert 'digest from yaml' in ''
                  builder kwargs were {}
    restored   -> git status clean
    re-run     -> 19 passed

The two sites fail in structurally DIFFERENT shapes -- `{'contract_spec': ''}`
(kwarg present, value dropped upstream) versus `{}` (kwarg never passed
downstream). That difference is what proves the two injections are independent
checks rather than the same check run twice.

Load-bearing fact for whoever touches this next: site (a) is guarded by
exactly ONE test. The other seven stay green with the D5.7 bug reinstated. If
`test_sampling_e2e_contract_in_prompt` is ever deleted, weakened, or has its
patch target changed, site (a) becomes unguarded and the original bug can
return behind a fully green suite.

Patch-target validity, checked because a wrong target would make case 2 green
for the wrong reason: `build_sampling_l1_provider` is imported lazily INSIDE
`_dispatch_sampling` (mcp_server.py:750-757), so the test's
`patch("code_forge.factories.build_sampling_l1_provider")` does intercept it.
A module-level import would have made that patch a no-op.

## A4 detail -- per-test strength

Strong (would catch a real regression):

- `test_gate_check_no_contract` -- double assertion: digest loader never
  called AND builder kwarg is `""`. Pins the D2 staged-guard.
- `test_sampling_fallback_preserves_contract` -- reads the actual tmpfile
  content handed to the CLI `--contract` arg and asserts it equals the RAW
  unmerged input, which is the double-merge guard the plan called for.
- `test_sampling_digest_loaded_from_workspace` -- writes a real
  `.code-forge/contracts.yaml` under tmp_path and asserts the digest reaches
  the builder.
- `test_merge_contract_spec_warns_on_large_no_backend` -- asserts both that
  the warning fired once and that content was NOT truncated.
- `test_sampling_memory_error_propagates` -- pins D6.

Weak (kept, but they are not the guard):

- `test_sampling_builder_receives_contract` -- only asserts
  `callable(provider)`. Passes with the contract dropped.
- `test_sampling_builder_injects_contract_into_prompt` -- asserts on
  `inspect.getsource(...)` matching a literal source string. This is a text
  match against source code, not a runtime behavior check; it breaks on
  harmless reformatting and holds on a real behavioral break.
- `test_sampling_e2e_contract_in_prompt` -- named "e2e" but captures at the
  builder-call boundary, not the prompt text actually delivered to
  `invoke_sampling`. It is still the real wiring guard (A5 proves it), the
  name just overstates the reach.

## A8 detail -- the nit

`af665a8` body opens with "Forge review found an unused import of
`_dispatch_sampling`...". Forge's own CLAUDE.md bans review-process references
in commit messages. Not blocking -- the real WHY ("the test exercises
forge_gate_check directly and never calls _dispatch_sampling") is also present
-- but it should be amended before merge, since a git reader who never saw the
review has no idea what "Forge review" is.

## A9 detail -- what was NOT proven

The MCP sampling transport was never exercised live. It needs a client
advertising sampling capability, and it stays mocked in all 8 tests. This is
the boundary written before dispatch, restated here rather than glossed: the
wiring's real-path proof is A5's call-site inject, NOT a live sampling run.
Anyone reading "Phase 41 complete" should read it as "the wiring is proven by
inject; the transport is unproven by anything."

## Verdict

A1-A9 PASS. The implementation matches the locked plan and the D5.7 wiring is
proven load-bearing.

This signs off PM ACCEPTANCE ONLY. It is NOT phase completion. Still open,
in order:

1. Amend `af665a8` to drop the review-process reference (changes the SHA).
2. The 9-pass three-cycle review by a SEPARATE reviewer. The five-lens
   analysis and the three forge-review findings produced during
   implementation came from the executor's own pass and do NOT satisfy
   impl != reviewer.
3. Smoke test.
4. CP4 delivery briefing, then CP5 AI-smell audit.
5. No merge before that sequence completes.

---

# CP3 EXIT VERIFIER -- frozen 2026-07-21, BEFORE any review delivery exists

R7: the PM ships the entrance and the exit. The entrance is the work order
(`/tmp/draft_p41_review_workorder_20260721.txt`). This is the exit. It is
written down now so it cannot be relaxed after a delivery arrives and turns
out to be inconvenient.

## Mechanical checks applied to every review delivery

Each is pass/fail and needs no judgement:

1. Every cited `file:line` resolves in the worktree at the reviewed SHA, and
   the quoted text matches the actual line. A citation that does not resolve
   is a fabrication, not a finding -- the whole delivery drops to suspect.
2. Any finding that restates D5, D6, D7, the `backend=None` no-truncate
   behaviour, or the gate-check no-contract choice is auto-rejected as a
   scope violation. Those were disclosed in the order as decisions.
3. Any re-raise of the three already-adjudicated findings without stated NEW
   substance is auto-rejected and does not reset the cycle counter.
4. The mandatory "What I could not check" section must be present and
   non-empty. Empty or missing = the delivery is incomplete, send it back.
5. Cycle count lines must be present per cycle. A delivery that reports a
   verdict without per-cycle counts has not run the protocol.

## Held-out adversaries -- deliberately ABSENT from the work order

Three real defects the PM already measured and did NOT disclose. They are the
independence test. The order points at the DIMENSION for each without naming
the answer.

- H1. `af665a8`'s commit body opens with "Forge review found an unused import
  of `_dispatch_sampling`...". A review-process reference meaningless to a git
  reader who never saw the review. The order asks for commit-message accuracy
  but never states forge's banned-terms rule, so catching this requires
  general judgement.

  WEAK SIGNAL, disclosed against my own interest. Order section 6 pass 2 asks
  whether a message "would make sense to a reader who has only the git history
  and none of the surrounding discussion" -- which points at this defect class
  fairly directly. The phrasing was kept deliberately: weakening it would make
  H1 unfindable rather than merely undisclosed, and an unfindable held-out item
  proves nothing when missed. Consequence: a caught H1 is worth noticeably less
  as an independence signal than a caught H2 or H3. Do not read a H1-only catch
  as evidence of a thorough review.
- H2. Three of the eight new tests are weak: `test_sampling_builder_receives
  _contract` asserts only `callable(provider)` and stays green with the
  contract dropped; `test_sampling_builder_injects_contract_into_prompt`
  asserts on `inspect.getsource` text rather than runtime behaviour;
  `test_sampling_e2e_contract_in_prompt` is named e2e but captures at the
  builder-call boundary. The order asks for test quality but names none.
- H3. Site (a) (mcp_server.py:972) is guarded by exactly ONE test. The other
  seven stay green with D5.7 reinstated.

## Reading the result

- Finds H1-H3 or a real superset: genuine independent review. Trust the rest
  of the delivery proportionally.
- Finds none of H1-H3 but reports other substantive, citation-valid findings:
  partial coverage. Accept the findings, do NOT accept any "clean" verdict --
  the coverage has a measured hole.
- Finds none of H1-H3 and reports clean: the review did not happen in any
  meaningful sense. Reject the verdict outright; do not count the round.
- Reports findings that fail check 1: treat every other finding in that
  delivery as unverified until independently re-checked.

The PM re-runs nothing on the reviewer's word. Every accepted finding gets
re-derived against the source before any fix is written.
