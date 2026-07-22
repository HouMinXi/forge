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

---

# CP3 ROUND 1 -- kimi K3 delivery, PM adjudication 2026-07-21

Delivery: /tmp/draft_p41_review_report_20260721.txt. Verdict: 0 high, 0
medium, 4 low (F1-F4), plus 3 pre-existing (P1-P3).

## Exit verifier applied

Mechanical checks (frozen above):
1. Citations resolve + quote matches: PASS. Every F1-F4 and P1-P3 line was
   reopened at the reviewed SHA by the PM; all quoted lines are byte-exact.
2. No locked-decision restatement: PASS. No finding restates D5/D6/D7/
   backend=None/gate-check.
3. No bare re-raise of adjudicated (a)/(b)/(c): PASS. F1 explicitly
   distinguishes itself from closed-(b): (b) was SIGKILL (unhandleable), F1
   is a Python exception path (handleable) with the cleanup idiom 12 lines
   away. New substance -> legitimate, counts.
4. "What I could not check" present + non-empty: PASS (7 items).
5. Per-cycle counts present: PASS (c1 4/0/6, c2 0/4/5, c3 0/0/2).

Held-out adversaries (the independence test):
- H1 (commit-message review-process phrasing): NOT caught. K3 verified both
  commit bodies "accurate against the code" and stopped there -- it read
  accuracy, not forge's banned-terms rule. Expected: H1 was disclosed as a
  weak signal, so missing it is uninformative.
- H2 (three weak tests): PARTIALLY caught, and DEEPENED. K3's F4 nails
  test_sampling_builder_injects_contract_into_prompt with a sharper mechanism
  than the PM had (false-PASS on guard deletion, not merely "fragile"). It did
  NOT separately flag test_sampling_builder_receives_contract (callable-only)
  or name the e2e test's boundary-capture limitation. So: 1 of 3, but the 1 it
  took, it took deeper than the held-out note.
- H3 (site (a) single-test guard): NOT caught as such. K3's F3 is adjacent
  (job-branch zero coverage) but is a DIFFERENT gap than H3 (site-(a) guarded
  by one test). Both real.

Independence read: K3 caught a real superset on the test-quality dimension
(F3 + F4 are both genuine and F4 exceeds the held-out note), and added F1/F2
which were NOT in the held-out set at all -- two real findings the PM had not
pre-measured. This is a genuine independent review, not a rubber stamp. The
H1 miss is discounted per the frozen weak-signal note. Trust the delivery.

## PM re-derivation of each finding (not taken on K3's word)

F1 -- CONFIRMED real. _run_cli_budgeted re-raises after unlinking only its
own stderr log (mcp_server.py:535-541 BaseException, 569-575 CancelledError).
The await at :863 has no guard; raw_contract_tmp (:855-860, NEW code this PR)
leaks on any raise. Severity low (0600 tmp, OS eventually reaps), but it is
NEW this change and the fix idiom is already present at :875-887.

F2 -- CONFIRMED real. Sampling _merge_contract_spec (cli.py:1861/1884) only
warns at >4096 backend=None, never rejects; CLI _load_contract (cli.py:1715,
1721) hard-raises CliError on empty and >65536. Same MCP input: sampling
succeeds, fallback dies naming a "contract file" the user never passed.

F3 -- CONFIRMED real. Sampling fallback test returns a 4-str-tuple
(test:2213-2243) -> only the inline branch (:864) runs. The job branch
(:872-888) is reached by no sampling test; start_job raising side_effect
exists in no test. Zero coverage confirmed by grep.

F4 -- CONFIRMED real, and it upgrades the PM's own A4 note. getsource
assertion (test:2132) matches source text that survives deleting the
`if contract_spec:` guard (factories.py:575). Delete guard -> every
no-contract review emits an empty "## Contract Reference" header, test stays
green. False-PASS, not merely "fragile" as A4 had it.

P1/P2/P3 -- all three CONFIRMED pre-existing and correctly scoped out. P1
line-ref stale (cli.py:1653 is siblings=, lock is :2276); :814 not in any
diff hunk. P2 is F1's twin on the non-sampling path. P3 gate-check start_job
(:1090) has no cleanup guard. None touched by this diff.

## PM disposition -- does NOT follow K3's suggested fixes verbatim

F1 -> FIX IN THIS PR. But not K3's `except BaseException`: prefer the existing
   :875-887 idiom (except-cleanup-reraise) wrapping the :863 await, or a
   finally that unlinks only when neither success-unlink nor job-transfer
   ran. NEW code this PR = must fix here, not deferred to P2.
F3 -> FIX IN THIS PR. Add the start_job-raises test through the sampling
   fallback; it simultaneously pins F1's fix. This is the highest-value fix
   (covers the branch that contains F1).
F4 -> FIX IN THIS PR. Add a behavioural test that drives the real provider
   closure with invoke_sampling patched and asserts the contract body in the
   captured prompt. Keep or drop the getsource pin as secondary.
F2 -> DEFER, needs discussion. Root cause is an outlet-policy divergence
   (should the MCP boundary pre-validate?), a product decision OUTSIDE the
   D5.7 wiring scope. Folding a boundary-validation behaviour change into this
   PR is scope creep. Record as follow-up; do not touch here.
P1 -> trivial comment fix, fold into this PR opportunistically (stale line
   ref, one line). P2/P3 -> follow-up, out of scope.

## Still open (revised)

1. H1 amend to af665a8 (unchanged from before).
2. Implement F1 + F3 + F4 fixes + P1 comment. Executor != PM, != kimi.
   These are logic-bearing (F1) and test changes (F3/F4) -> the fixes
   themselves need the 3-cycle review before commit.
3. F2 + P2 + P3 -> follow-up notes, not this PR.
4. Re-review after fixes (CP3 does not exit at round 1 -- new code landed).
5. Smoke, CP4, CP5, then merge.

CP3 round 1 did NOT converge (4 findings, 3 to fix). Next round reviews the
fix commits, and its prompt must carry: what F1/F3/F4 fixes landed, that F2
was deferred-by-decision (not missed), and that P1-P3 are pre-existing.

## CP3 R1 -> fix-dispatch decisions (2026-07-21, SUPERSEDES "Still open" P1-P3 rows above)

PM decisions after user review of the R1 adjudication:

- P1, P2, P3 PULLED IN-SCOPE this round (user: "P1-P3 pre-existing, lean to
  fix this round too"). Reverses the review work order's out-of-scope note.
  Verified all three sit OUTSIDE this PR's diff hunks (git diff main..HEAD
  hunk headers) -- genuinely pre-existing.
- F1 + P2 + P3 are two defect classes across three near-identical dispatch
  sites (A _dispatch_sampling fallback ~847, B forge_review direct ~988, C
  gate-check ~1082). Class 1 = contract tmpfile leaks if _run_cli_budgeted
  raises (F1 site A NEW, P2 site B pre-existing twin). Class 2 = transferred
  stderr/tmpfile leaks if start_job raises (site C P3 is the lone unguarded
  site; this PR added the guard to A, left C behind).
- Fix method CHOSEN (user picked): extract ONE shared dispatch helper, route
  A/B/C through it. Not three in-place guards -- the three sites are already
  duplicated and three guards add more copies (Golden Rule 4). Grounded facts
  for the helper: all sites return _make_result/_make_job_ref uniformly; cap
  via _job_cap_s(workspace, backend_name="") differs per site so it is a
  helper param; no _unlink helper exists today (new symbol); site B carries
  env=child_env (FORGE_ALLOW_MAIN) which must survive.
- INVERSION owned: the helper touches gate-check + direct-review, two paths
  this PR did not otherwise touch and which currently work. Fix blast radius
  exceeds the bug (low-sev leak on exception paths). User accepted this
  trade for completeness.
- F2 STILL DEFERRED. Reason sharpened this round: there is NO policy-free
  fix. Every in-place fix path routes back to one product-policy fork (should
  the MCP contract boundary be lenient like the sampling path or strict like
  cli._load_contract), and any fix touches _load_contract which real CLI
  file-path users depend on for the current "contract file" message. Defer
  DESTINATION: a dedicated follow-up "MCP<->CLI contract-validation parity",
  bundled with nothing else (P2/P3 no longer travel with it -- they are now
  fixed here). Blocked on the user's direction call (lenient vs strict); not
  started.
- Implementation work order (R7 ENTRANCE) frozen at
  /tmp/draft_p41_impl_workorder_20260721.txt (275 lines, ASCII-clean): T1
  helper+F1/P2/P3, T2 F3 coverage + per-site injection, T3 F4 behavioral
  test, T4 P1 comment. Dispatch channel = user's call; executor != PM,
  != kimi (impl != reviewer). Fix commits need their own 3-cycle review.
- H1 amend to af665a8: DONE by user this session.

Revised open list: (1) dispatch the impl work order to an executor.
(2) 3-cycle review the fix commits. (3) CP3 R2 with non-convergence
disclosure (T1-T4 landed, P1-P3 pulled in by decision, F2 deferred).
(4) F2 parity follow-up: get user's lenient-vs-strict direction, then plan.
(5) Smoke, CP4, CP5, merge.

## CP3 R1 fix delivery -- PM acceptance verification (2026-07-21)

Delivery: branch fix/sampling-contract @ 75e846b (7 commits on main@8e18aa0).
Briefing at /tmp/draft_p41_briefing_20260721.txt. Verified independently, not
accepted on the briefing's word.

VERIFIED BY PM (own hands, not narrated):
- HEAD 75e846b, worktree md5 == HEAD (fdb6023...), clean. diffstat 598/63,
  3 in-scope files only. code ASCII gate clean.
- af665a8 (H1) amended -> 20258ce; content is import-removal only.
- FULL SUITE: 2881 passed, 8 skipped, 0 failed, 498s -- run against WORKTREE
  src with forced PYTHONPATH (editable install resolves to MAIN tree by
  default; unforced pytest would validate the wrong code). Baseline was 2874;
  +7 net. Briefing only ran a 137-test subset -- its "None remaining / all
  verified" was overclaimed; the real regression number is this one.
- Helper _dispatch_cli (647): contract lifecycle correct; 3 call sites
  (917/1025/1078) route through it; cap per-site; env forwarded at site B.
- HELD-OUT PROBE (order preservation, executor not told): inline branch
  unpacks stdout/exit_code/elapsed/stderr and passes them to _make_result in
  the same order -- no silent stdout/stderr swap. PASS.
- Test quality read: T3 captures the real runtime prompt (not getsource);
  site-C test asserts real stderr-file cleanup + assert_called_once route
  lock; 3 _dispatch_cli direct tests assert real filesystem state.
- BUG-INJECTION (Golden Rule 2, own hands): removed _unlink(stderr_path) from
  the helper -> test_gate_check_start_job_cleans_up_on_raise AND
  test_dispatch_cli_start_job_raises_unlinks_both both FAIL -> restore
  (md5==HEAD) -> both PASS. Breaking the HELPER reddened the through-handler
  site-C test, proving site C really routes through the helper (the
  equal-looking-coverage collapse point).
- F2 NOT touched (cli.py diff is the original backend=None warn branch;
  _load_contract untouched). Correctly deferred, not silently fixed.

PM FINDINGS (not in briefing, not caught by executor's self-review):
- L1 except Exception (mcp_server.py:678) misses asyncio.CancelledError
  (BaseException in py3.8+): cancellation during the await orphans
  contract_tmp -- same leak class the change set out to kill, via
  cancellation. Low sev. PM owns it: the impl work order named except
  Exception as an acceptable floor, so this is a floor gap, not executor
  error. Fix: widen the run-except to except BaseException (work order
  pre-authorized it) in the R2 fix batch.
- Commit-message defects (message layer; the diff-based ASCII/vocab gate
  does not cover messages):
    * 20258ce body has an em-dash (non-ASCII). Introduced by the H1 amend.
    * 75e846b body has "review finding" (banned review-process vocab).
  Both must be amended before merge.
- Coverage note (minor): site A/B routing verified by source-read + inline
  tests; only site C has a dedicated job-branch through-handler regression
  test. Centralized cleanup makes the risk low.

GOVERNANCE: the briefing's two deepseek-v4-flash reviews are the executor's
OWN self-review (the impl order said "you are not the reviewer"). They do NOT
satisfy CP3 R2. impl != reviewer -> CP3 R2 still owed to an independent
reviewer that is neither the executor nor kimi.

VERDICT: fix is correct, complete, and genuinely tested. PM-accepted as a
delivery. NOT merge-ready. CP3 has NOT converged -- this was the fix, not a
review round.

Open, ordered:
1. Amend 2 commit messages (20258ce em-dash -> ASCII; 75e846b drop "review
   finding" phrasing). Changes those SHAs.
2. Widen except Exception -> except BaseException at the helper run-except
   (folds the CancelledError leak). Own commit, needs review with the batch.
3. CP3 R2: independent review of the fix commits (not executor, not kimi).
   Non-convergence prompt must carry: what landed (helper T1 + F3/F4 tests +
   P1), that P1-P3 were pulled in-scope by decision, that F2 stays deferred,
   and the PM findings above so R2 does not re-discover them cold.
4. CP3 R3 if R2 finds anything; exit at 3 consecutive 0/0/0/0.
5. Then smoke, CP4, CP5, merge. F2 parity follow-up remains separate.

## CP3 R2 EXIT VERIFIER -- frozen 2026-07-22, BEFORE any R2 delivery exists

R2 review work order (ENTRANCE) frozen at
/tmp/draft_p41_r2_workorder_20260721.txt (260 lines, ASCII-clean,
held-out-leak-clean). Dispatch pending user's channel choice; executor of
the fix and kimi (R1 reviewer) are both disqualified from R2.

MECHANICAL CHECKS (apply to the returned R2 report before reading findings):
  M1 every finding cites file:line + a quoted line that resolves in
     75e846b source (anti-hallucination gate).
  M2 no section-4 locked decision reported as a finding.
  M3 F2 deferral / P1-P3 in-scope reversal not reported as problems.
  M4 "What I could not check" present and non-empty.
  M5 per-cycle count lines present for all 3 cycles; >=9 passes.
  M6 if it re-raises F1/F2/F3/F4/P1/P2/P3 it carries NEW substance or is
     a fix-is-wrong claim, not a bare repeat.

HELD-OUT ADVERSARY (deliberately absent from the R2 work order):
  HX -- the helper's run-except is `except Exception` (mcp_server.py:678),
        which does NOT catch asyncio.CancelledError (a BaseException in
        py3.8+). If the awaiting task is cancelled (client disconnect /
        server shutdown) between tmpfile creation and completion,
        contract_tmp orphans -- the SAME leak class F1/P2 set out to kill,
        reached via cancellation instead of a normal exception. PM found
        and verified this independently; it is real, low-severity, and
        UNFIXED on purpose so R2 has a live target. The impl work order
        named `except Exception` as an acceptable floor, so this is a
        floor gap, not executor error.

  WEAK-SIGNAL DISCLOSURE (against my own interest): the R2 work order
  section 6 pass 2 says "catch breadth on the async paths" and pass 3 says
  "error handling and RESILIENCE on the async dispatch paths". Those lines
  point a diligent reviewer toward the except-breadth question, i.e.
  toward HX's neighborhood. They are NOT removed -- deleting them would
  tell the reviewer to skip async exception paths, turning HX into a
  target that can never be hit (a dead adversary proves nothing when
  missed). Consequence: a caught HX is a weaker independence signal here
  than a fully-blind catch would be. Do not over-credit an HX catch; do
  still discount an HX miss.

READING THE R2 RESULT:
  - catches HX (or a superset: any BaseException/cancellation-path leak):
    genuine independent async-path tracing -> trust the review; fold HX
    into the fix batch as already-planned.
  - misses HX but surfaces other substantive, source-verified findings:
    partial -- accept those findings on their own evidence; do not read
    "clean on the helper" as proof the helper is clean; PM still fixes HX.
  - misses HX and reports the branch clean: reject the clean verdict as
    non-independent (a blind spot the prior self-review also had); HX
    fixed regardless; consider re-dispatch to a different model.
  - fails M1 (citations do not resolve): treat every finding as unverified
    until PM re-derives, same bar as any delivery.


## Message-rewrite + self-review event -- PM verification (2026-07-22)

Branch rewritten 75e846b -> 1f2a613 (rebase from commit 2 onward). A
"briefing audit" fixed 2 commit messages + polished the R2 briefing.

VERIFIED BY PM (own hands, git ground truth, not the audit's table):
- CODE UNCHANGED. 75e846b^{tree} == 1f2a613^{tree} == 17e749c3..., and
  mcp_server.py md5 still fdb6023. range-diff 8e18aa0..75e846b vs
  8e18aa0..1f2a613: commits 1/3/4/5/6 identical; only 2 and 7 changed,
  and each shows ONLY a "Commit message" hunk, zero code hunks.
  => Message-only rewrite. My 2881 full-suite result and the injection
  FAIL->restore->PASS proof transfer VERBATIM to 1f2a613 (identical tree
  cannot behave differently). worktree clean, 598/63, 3 in-scope files.
- MY TWO MESSAGE FINDINGS CLOSED CORRECTLY:
    20258ce->1945ca8: em-dash "--" now ASCII "--". Fixed.
    75e846b->1f2a613: "addressing the review finding that..." removed,
      now "-- a bare tempfile... leaves residual files on disk". Fixed.
  Full re-scan of all 7 messages at 1f2a613: non-ASCII CLEAN, banned
  review-vocab CLEAN, no truncated subjects. The buried-commit filter-repo
  concern is moot -- it was rewritten and verified correct.

BRIEFING SECTION 6 CLAIM -- REJECTED as CP3 R2 satisfaction:
- The briefing claims "R2 exit: 4 consecutive clean cycles, 0 confirmed"
  from an MCP deepseek review + a 12-pass CLI deepseek review. But section 7
  ("I re-ran with --mode local") shows THE EXECUTOR ran them itself.
  That is the implementer reviewing its own code = self-review. The R2
  work order section 0 pre-empted exactly this: "A prior self-review by
  the implementer is NOT binding and NOT evidence." impl != reviewer is
  categorical; a forge deepseek run invoked by the implementer is at most
  an advisory pre-check (like the inline gate), NOT the independent R2.
- HELD-OUT HX MISSED. The self-review reported 0 findings and did not
  surface the except-Exception/CancelledError leak (mcp_server.py:678,
  still present -- byte-identical tree). By the frozen R2 exit rubric
  ("misses HX and reports the branch clean -> reject the clean verdict as
  non-independent"), this verdict is rejected. Two independent grounds:
  procedural (self-review) and empirical (blind to a PM-verified bug).
- section 9 is honest where it counts: it does NOT claim to have re-run the full
  suite (timeout; leaned on prior-session = PM ground truth). That honesty
  is correct and noted. The overclaim is confined to section 6's R2-converged
  framing.

STATUS UNCHANGED BY THIS EVENT: CP3 R2 (independent review) STILL OWED.
The R2 work order is ready at 1f2a613 (SHAs updated). HX remains the live
held-out. The executor's self-review producing a false-clean (missed HX)
is itself fresh evidence for why independence is required here -- fold that
into the R2 dispatch decision, do not treat it as R2 done.

SHA anchor update: everywhere this file said 75e846b as ground truth,
1f2a613 is the identical-tree successor. 2881/8skip/498s and the injection
proof stand at 1f2a613 without re-run (tree identity).
