---
gsd_state_version: 1.0
milestone: v2.8
milestone_name: Onboarding + Throughput
status: active
stopped_at: main @ c72ff06, pushed. Router batch F1 merged (695f739); F3 + F4-live-probe + F2/F5 docs remain, SSE parse tolerance still deferred
last_updated: "2026-08-09T00:00:00.000Z"
progress:
  total_phases: 17
  completed_phases: 11
  total_plans: 35
  completed_plans: 33
  percent: 64
---

# State: Forge

**Last updated:** 2026-08-09

## Project Reference

See: .planning/PROJECT.md (updated 2026-06-09)

**Core value:** No code ships without surviving three consecutive clean review cycles; a green verdict is honest or declares what it did not verify
**Current focus:** No phase in flight as of 2026-08-09, main @ c72ff06. Working queue is the eight pending todos + Phase 43.1 (charter ratified 08-08) + the Router batch remainder (F3 trust path, F4 live probe, F2/F5 docs; F1 landed 695f739, SSE parse tolerance deferred on a stated trigger). Phase 42 MERGED (933032d, 2026-07-29). Windows wave-2 parked until gpu-win evidence

**CHARTER RATIFIED 2026-08-08.** `charter_review_pipeline_gaps` accepted,
placed in v2.8 tail as Phase 43.1. 10 defect items + the
silent-failure-signature process change (adopted in forge phase planning
only, not promoted to ~/CLAUDE.md global per the charter's own route).
Backlog mapping (charter item <-> todo #) recorded in the charter file.
Five todos fall outside this charter (#49, #50, #55, #57, #61) and
remain independent. Headers commit a3abdf3 landed on
feat/backend-custom-headers (user --no-verify; 17 review rounds could not
reach three-clean-cycles on this backend/diff, three real findings fixed,
one refuted by WHATWG spec). Three subagent tasks (#49, #57, #59)
failed with 503 upstream-inactive; worktrees and partial progress on
disk, pending resume. **Superseded 2026-08-09:** #49 and #57 landed on
main (6e5650f, 6b05a6e); only #59 is still open. See the 08-09 reconcile.

## Current Position

**RECONCILE 2026-08-09 (authoritative main pointer; supersedes the F1 block
below, whose scope narrative stays valid).** main @ c72ff06, pushed,
`origin/main..main` empty. Eighteen commits landed since 695f739; the F1 block
below described the tree as of the first of them.

Full suite at c72ff06: 3135 passed, 3 skipped, 239.89s, run with
`--ignore=.claude`. Collection after the worktree removal is 3175, a gap of 37
that is NOT explained by that flag -- see Cleanup below. The 3010/9 figure in
the F1 block is the 695f739 measurement and is not a regression.

What those eighteen closed, by todo number where one applies:

- Verify/receipt chain: #46 (e29f9f4 count the last three consecutive cycles),
  #51 (1cc45b0 annotate the diff in place so excerpt line numbers are
  post-image), #52 + the infra-anchor defect (73d6c9e refuse a cycle whose pass
  never ran, stop anchoring failures), 888333a one timestamp per round --
  which is charter item 1, landed on main rather than on
  defects/receipt-timestamp.
- Gate wiring: 8f745dd makes the mutation gate measure something (the
  daemon-thread + l2_runner pair from the charter's process-change section),
  373710c stops a run that provably cannot converge.
- Config/backends: a3abdf3 per-backend request headers, #57 (6b05a6e
  gate.schema describes the fields the loader reads), a1b6943 retry log carries
  the cause.
- Tooling/DX: #48 (45e4376 pre-commit declared-class carve-out), #49 (6e5650f
  stale-hook detection), 9bc8b3a handshake coroutine closed, 1fb3eea busy-lock
  guidance, 7b6101a ledger location + claim.
- Eval runner: #56 (8da83a5 signal the process group, not only the child),
  c72ff06 keeps both ends of a child's stderr so an infra error written before
  a flood of later output still reaches the classifier. bd9e268 is the
  gitignore chore beside it.

Todo-state correction: the block below the fold still says three subagent
tasks (#49, #57, #59) failed with 503 and await resume. #49 and #57 have since
LANDED on main (6e5650f, 6b05a6e). Only #59 (detect a reviewer ignoring the
annotated line numbers) is still pending, alongside #50, #53, #54, #55, #58,
#60, #61. #53 and #59 are not untouched, though: their implementation exists
on `fix/excerpt-fabrication-guard` @ e755156, rescued from an agent worktree
and unreviewed -- see Cleanup below.

Cleanup DONE 2026-08-09. The worktree
`.claude/worktrees/agent-a7835ea5f5c306467` is removed and
`worktree-agent-a7835ea5f5c306467`, `defects/infra-anchor-poison`,
`fix/receipt-followups` are deleted (all three merged). How close that came to
destroying work is the part worth keeping:

`git worktree remove` refused with "contains modified or untracked files".
Those files were six STAGED modifications carrying 315 lines -- an
excerpt-fabrication preflight in `receipt.py` (+61), refusal logic in
`verify.py` (+44), and six new tests including
`test_fabricated_excerpt_lines_refused` and
`test_mismatch_names_failing_line_not_excerpt_start`. That is the
implementation of #53 and #59, and none of it was on main. The branch it sat
on WAS merged, so `git branch -d` would have taken it without a word: `-d`
asks whether a branch's commits exist elsewhere and cannot see a working tree
at all. The worktree guard was the only thing in the way, and `--force`
removes precisely that guard.

Rescued to `fix/excerpt-fabrication-guard` @ e755156, committed under
`FORGE_COMMIT_CLASS=wip` -- which skips verify/review/chain/gate-check while
still running the non-ASCII and AI-vocab text gates, so it is narrower than
`--no-verify`, which runs none of them. Being unmerged, that branch now
refuses `git branch -d` on its own. The work is logic-bearing and needs a full
forge review before it lands anywhere real.

Removing the worktree fixed collection: bare `pytest` used to die on
`ImportPathMismatchError` raised by that worktree's own `tests/conftest.py`,
and now collects 3175 tests in ~1.2s. That worktree is also the likeliest
source of the `mutation-flaky` advisory in two reviews, since a collection
error makes the mutation baseline look unstable.

An unexplained gap sits beside that, recorded as open rather than as a
diagnosis. This session's full runs reported 3135 passed + 3 skipped = 3138,
using `--ignore=.claude` to get past the collection error. Current collection
is 3175, a 37-test difference. The obvious explanation -- that the flag was
excluding 37 tests -- is DISPROVED by direct measurement: with the worktree
gone, `--ignore=.claude` and no flag both collect exactly 3175. There are no
`addopts` and no marker-based deselection in `pyproject.toml`. The 3138 state
cannot be reproduced now that the worktree is deleted, so the cause is
undetermined; a conftest-identity collision altering collection of the main
`tests/` tree is a candidate, not a finding. Treat 3175 as the first
reproducible count and 3138 as an unexplained earlier reading, not as evidence
that anything was skipped.

Still owed: `fix/rebase-msg` is 1 commit ahead of main -- inspect before
deleting.

Counter question left open, not guessed at: the frontmatter reads 11/17 phases
= 64%, while the 07-25 block below says v2.8 is 16/18 (89%) and the 07-25
reconcile says 16/17 (94%). Three different bases (all phases vs v2.8-only vs
plans) are in play and none is labelled. A recount needs the Wrap-Up
Protocol's basis decision first; the numbers are left as found rather than
replaced by a fourth unlabelled one.

**F1 LANDED 2026-07-30, main @ 695f739 (fast-forward from 933032d).** Two
commits: a47d888 sends the stream flag explicitly on every api request, 695f739
makes an unusable content field retryable (a separate crash ashare hit on the
deepseek path). Suite 3010 passed / 9 skipped.

Read this before planning the rest of the batch -- F1 was closed by a DIFFERENT
mechanism than the triage recommended, and the difference decides what is left:

- The triage proposed SSE auto-detect in the shared parse path (3 sites). What
  shipped instead sends `stream: false` explicitly, so a router that picks its
  own default never picks SSE. Prevention, not tolerance.
- PM-verified on the real path, not inferred. Against OmniRoute
  (192.168.100.10:20128, onmi-gemini3.6): stream omitted returns a
  `data: {...,"object":"chat.completion.chunk"}` event stream; `stream: false`
  returns a `chat.completion` JSON object. Through forge's own llm_invoke the
  call succeeds; re-injecting the old conditional reproduces the exact F1 error
  and restoring it byte-identically passes again. The injection site was
  llm_invoke.py:152 when this was written and is llm_invoke.py:226 as of
  2026-08-09 (`body["stream"] = bool(backend.stream)`, with the reason in the
  comment three lines above it).
- What is therefore NOT covered: a router that returns SSE despite being told
  not to. `_parse_response_body` still has no SSE tolerance and the three
  non-streaming parse sites are untouched. If such a router shows up, the
  triage's shared-path fix is still the right answer -- it is deferred, not done.
  Re-grepped 2026-08-09: the function is at llm_invoke.py:1135 and its three
  call sites are :1197 openai, :1295 anthropic, :1469 vertex. Do not reuse the
  triage's 1023/1121/1295 -- 1295 appears in both lists and means a different
  site now (vertex then, anthropic today), so a stale number here resolves to a
  real call of the same function and reads as correct while pointing at the
  wrong provider. `_read_sse` exists (:326) but is reached only when
  `backend.stream` is true (guards at :1188, :1266, :1386), so it is the opt-in
  streaming path, not tolerance on the non-streaming one.
- F4's live probe was scoped as F1's acceptance test. That rationale now needs
  restating: the acceptance test already exists as the real-path check above,
  so F4 has to justify itself on its own value (faster debug loop), or shrink.

**SCHEDULED 2026-07-25.** Router onboarding compat batch added to the v2.8 tail
after Phase 42, from the OmniRoute/gemini default-router RCA
(/tmp/forge-router-friction-rca.md). PM-verified all 5 findings against main @
74adbf2, NOT the report's self-assessment: F1 (SSE body parsed as JSON) is a real
reproduced blocker -- unblocks the gemini CP3 backend the fleet wants; F3
(CWD-scoped trust) is a real footgun; F4 partially disproved (doctor already
probes, but _probe_api is offline -- needs a live mode); F2 doc-only; F5 DISPROVED
(user-level backend inheritance already ships via _merge_user_into, Phase 37.1).
Triage + evidence: reports/router-friction-triage-20260725.md. v2.8 recount 16/18
(89%).

**RECONCILE 2026-07-25 (authoritative main pointer; supersedes 07-23 below).**
main @ 74adbf2. Phase 41 (review focus) MERGED fast-forward (7 commits on
ca0d860, +565/-60, new tests/test_focus.py). v2.8 count now 16/17 (94%); only
Phase 42 remains. PM-verified independently, NOT executor self-report: full
suite 2903/8/0 on the real editable-install main path (582s), per-site
bug-injection on all 3 header sites each proven caught, post-trust-edit
adversary proved focus trust independent of backend trust, degenerate focus
inputs graceful. F1 (plan-ref "D5.6" leak the repo [Dd]-[0-9] self-check misses)
fixed pre-merge via rebase, range-diff confirmed comment/message-only. Sibling
41-sampling-fix (2edb9d4 + 5c8e001) already in, not rebuilt. Cleanup done:
worktree removed, branch + rescue tag deleted (both confirmed merged). Full
record: 41-review-focus/41-ACCEPTANCE.md + cp-artifacts/cp3-impl-pm-verification.md.

**RECONCILE 2026-07-23 (superseded by 07-25 above for the main pointer; Phase
41 scope context still valid).** main @ ca0d860. Phase 47 (invoke-error-visibility, a
fleet-reported bug outside the v2.8 37-42 set) MERGED fast-forward, 2 commits
(79907c2 API + ca0d860 CLI), +85/-6. PM-verified independently: bug-injection
at both raise sites, deepseek CP3 (one warning dismissed on ground truth),
real-path CLI subprocess smoke, byte-identical rebase, full suite 2882/8/0 at
423.90s. Full record: .planning/phases/47-invoke-error-visibility/
47-ACCEPTANCE.md. Because Phase 47 is out-of-milestone, the v2.8 count is
UNCHANGED at 15/17 (88%) -- the two pending phases are still 41 and 42, and
the working queue below (41 -> 42) still holds. Cleanup: worktree removed;
branch fix/invoke-error-visibility still needs `git branch -d` (user-owned).
Tech debt from this phase logged as TD-9 (kind= asymmetry, latent,
condition-triggered). The 07-20 block below remains valid for the 41/42 queue
context; only its "main @ 8e18aa0" pointer is superseded.

**RECONCILE 2026-07-20 (main SHA superseded by 07-23 above; queue + Phase 41
scope still current).**
main @ 8e18aa0. Landed since 07-14:
(1) Phase 40 Honest-partial-results (mechanical half) MERGED 2026-07-16 as
25b063e. The worktree tip dd6d40f was rebased on the way in -> now a dangling
orphan unreachable from main; audit history by 25b063e (S1 disclosure; same
pattern as Phase 46 a18844a superseding f53bf84). Landed: PassOutcome enum +
derive_pass_outcomes (state.py), passes=N/M suffix (sarif.py), pass_status in
receipt JSON (receipt.py, backward-compat), large-diff file-based chunking
(outlet_c.py). 31 tests, suite 2798/0 at merge. Semantic half DEFERRED
(convergence plateau 7.2, prior-round memory 7.3, cross-file findings under
chunking, bin-packing, hunk-fallback, per-pass retry).
(2) contract/memoryerror fix MERGED 2026-07-20 as 8e18aa0 (fast-forward from
fix/contract-guard-memoryerror, user-pushed; worktree + branch cleaned). Three
contract guards re-raise MemoryError instead of laundering it into an empty
digest + ruff import cleanup. Chore-class.
Phase count RECOUNT (2026-07-20, v2.8 tracked-checklist basis incl. batches):
15/17 = 88% (was 14/17 before Phase 40 flipped [x]). Pending = Phase 41, 42.
Working queue: next Phase 41 -> 42.
Phase 41 SCOPE EXPANDED 2026-07-20 (pre-CP1): pain point P4 (general
review-focus/emphasis param) folded into 41-PLAN + 41-CONTEXT D5 per user
decision, BEFORE any review cycle starts. Also fixed a pre-existing 41-PLAN
defect discovered by grep: the design-intent header rename now covers ALL 3
prompt builders (cli.py:780 + factories.py:281 + factories.py:576), not just
cli.py:780 (renaming one left 2 outlets emitting the old header). Plan carries
no CP1/CP1b/CP3 markers yet; next step is CP1 when the user starts Phase 41.

**RECONCILE 2026-07-14 (superseded by 2026-07-20 above; kept for history).**
main @ cfade37. User merged fix/e2e-report3-mutation-skip-isolation
(3 commits: c2e663c test_round mutmut-axis isolation, 2c103bc empty-lock
busy, cfade37 lock atomic write-temp-then-link -- worktree SHA was
f279e00, patch-id 9c6d1c53 verified identical). Lock review history:
cycle-17 real deepseek review, 5 findings all exempt (2 pre-existing L0
lints blamed 46c6c262; 1 CONFIRMED = documented rolling-upgrade
limitation; 2 dismissed/repeat); full suite 2768/8 at commit time.
flock migration researched + DEFERRED (memory
reference_flock_migration_deferred.md; artifacts
~/docs/forge/flock-migration-future/). origin/main is 3 behind
(unpushed -- user owns push). Cleanup owed by user: remove worktree
.worktrees/fix-e2e-r3 then `git branch -d
fix/e2e-report3-mutation-skip-isolation`; humantest worktree STAYS
(customer mac test tonight). Working queue: next Phase 40 -> 41 -> 42.

Previous reconciliation (2026-07-11 evening, kept for history):
main @ 4b060bd (superseded).
main @ 4b060bd. Two landings today:
(1) Phase 46 doctor tool-audit MERGED as a18844a. The morning entry's
"f53bf84 not yet merged" is superseded: the user merged it and the SHA
changed on the way in (rebase); patch-id 47d8c647 verified identical,
feat/doctor-tool-audit deleted, f53bf84 now dangling. Anyone auditing
history: look for a18844a, not f53bf84.
(2) Windows MCP wave 1 MERGED as 4b060bd (fix/win-mcp-signal, deleted).
lifespan add_signal_handler pair guarded try/except NotImplementedError;
before the fix every MCP start on Windows died at lifespan setup,
taking doctor self-check and CLI review down. Review: 12 passes with a
legitimate cycle reset -- mutation (except Exception) survived all
tests, fixed by a propagation test, then 3 clean cycles; full suite
2712/7. gpu-win reverified all green: T1b initialize exit 0, doctor
exit 0, T4 real review E2E archived H:\forge-test\t4_review_summary.txt.
Wave-2 backlog (G1 lock kill / G2 encoding / G3 killpg) is
evidence-gated -- see memory project_forge_windows_support.md.
Between the two: CodeQL + Dependabot landed (ab1fff5 + 3 dependabot
merges, GitHub-side).
Working queue: next Phase 40 -> 41 -> 42.

Previous reconciliation (2026-07-11 morning, kept for history):
Phase 46 doctor tool-audit COMMITTED (f53bf84 on feat/doctor-tool-audit,
worktree .worktrees/p46). NOT yet merged to main. 4 files +311/-3,
full suite 2710/7/0. 11-round plan review converged 0/0/0/0 x3 models.
Injection matrix (I1-I4) bidirectional verified. PM applied 4 acceptance
fixes. Backend: mimo-pro deleted (401 expired), unified on OmniRoute.
forge review inline: 4 cycles 12 passes zero findings.

Previous reconciliation (2026-07-10, kept for history):
main @ c0f2b3d: Phase 45 multi-language MERGED (ff, 11 commits,
feat/multi-language deleted). Two-round Wave 2 L4: round 1 REJECTED
(3 languages dead on pipeline argv, 2 false-green; systemic empty-
stdout guard gap), round 2 CONDITIONAL (2 test fallouts + plan-ref
cleanup), final ACCEPTED (spike-verified end-to-end, full suite
2695/7/0). Major S1 disclosure recorded in memory
feedback_resolve_command_false_green.md: main's _resolve_command
whole-string which meant flagged tools (ruff/pylint sarif modes)
NEVER ran on the auto-detect path -- flake8-only L0 historically.
MERGED 7011ade: fix/llm-body-json -- body-level JSON parse wrapped
into LLMInvokeError across openai/anthropic/vertex + 4 tests
(injection-verified). Review: cycle 1 inline trio (1 Medium fixed) +
3 consecutive clean external rounds (deepseek/kimi/mimo-v2.5-pro,
all fact-checked) + real-path smoke (deepseek direct PASS 0.7s;
OmniRoute probe fired the wrap on a real SSE-always body as
designed). Post-merge note: main was filter-branch rewritten
(c0f2b3d -> 15fdbc6, tree-identical, metadata only); branch rebased
onto the rewrite before merge, tree parity verified byte-exact.
Fallout closed alongside: gate.yaml review-route + omni-sandbox now
stream:true (harness side; OmniRoute is SSE-always -- see harness
memory incident_omniroute_ops_20260707.md I6, Q2 still open).
Worktrees b1-json and multilang removable; branches deleted/deletable.
Working queue: Phase 46 first (doctor audit, timeliness: resolve
false-green evidence hot + pmd in /tmp volatile), then 40 -> 41 -> 42.
Reorder rationale: 46's cost curve steepens (context cools, pmd
evaporates on reboot -> silent false-green resurrection); 40 has no
time pressure, losing one small phase costs nothing. ROADMAP "Phase
46 paths" for deferred languages fixed to "Phase 47+" (numbering
collision with doctor audit phase).

**RECONCILE 2026-07-10 midday (superseded above; kept for history).**
main @ 4c5f46d. Landed since 07-09:

- W3 slow-drip deadline fix MERGED 2026-07-10 (4c5f46d; tree byte-identical
  to e5ccf8f, message amend only). _read_with_deadline daemon-thread +
  sock.shutdown(SHUT_RDWR); three-round L4 convergence.

- Phase 45 multi-language IN FLIGHT on feat/multi-language (worktree
  .worktrees/multilang), ff-clean on 4c5f46d. Wave 1 (Go via golangci-lint
  SARIF + mcp allow_main per-call env) ACCEPTED @ a31e3a8, 5 commits.
  Wave 2 (ALL_REGISTRIES refactor + C/C++ cppcheck + Java PMD + JS/TS
  ESLint) REJECTED @ 69215b4: B1 cppcheck SARIF on stderr, B2 eslint
  formatter never resolves (false green), B3 pmd crashes under pipeline
  argv (false green), B4 systemic empty-stdout+nonzero-rc reads as clean.
  Rework dispatched (verdict /tmp/L4_verdict_phase45_wave2_20260710.txt);
  eaf33c1 refactor itself accepted. C4 refined: spike must run the landed
  command in pipeline argv shape end-to-end through run_tools+parse_output.

- Phase 45 multi-language runs PARALLEL to the 40->41->42 working queue
  (queue unchanged). NUMBERING NOTE: it is NOT the learning-loop flywheel.
  The v3.x sketch lane (ESCAPE/SYNTHESIS/REGISTRY/SCOUT/COMPILATION/driver)
  was sketched as 45-50 BEFORE this phase took 45, and 46 is earmarked as
  the multi-language upgrade path. Sketch numbers renumber at firm-up;
  until then read sketch 45-50 as labels, not queue positions (collision
  notes added to ROADMAP + v2.9 schedule, 2026-07-10).

**RECONCILE 2026-07-09 (superseded above; kept for history).**
main @ 14b3985. Landed since the 07-08 snapshot:

- 38.3 MCP OOB UX: content is IN main (T1 roots mcp_server.py:173-216, T2
  api_key_file backend.py:89/309-326/636, _truncate limit<4 :568). The
  mcp-oobe branch @ 98f1ddd was superseded by the onramp/surflare work,
  then deleted; 98f1ddd + 6bf1682 dangle (safe to GC). NOT an open merge.

- Usability on-ramp batch @ 89a091f (merged 2026-07-07, PM L4 verified).
- surflare consumer-pain fixes @ e50b375 (merged 2026-07-08, PM L4 x4 rounds).

Next: Phase 40 (Honest partial results + convergence). QUEUE (authoritative,
2026-07-08): the in-flight working queue is 40 -> 41 -> 42, UNCHANGED. Phase 44
is now scoped (EVAL-ON-DUTY, v2.9 AMENDMENT 1 rev 2) as the root of the parallel
v2.9 ENV-GROUNDING lane 44 -> 51 -> 52 -> 53a -> 53b; that lane runs parallel to
the 45-49 flywheel and does NOT enter the 40 -> 41 -> 42 in-flight queue. 51's
only hard prereq (Phase 43 provenance) is merged (14328bb), so pulling 51
forward to post-43 is PERMITTED -- whether to exercise it is scheduling
sovereignty, not a map decision. This supersedes the stale "40 -> 44 -> 41 ->
42" line in the 38.x tail below.
In flight (this direction): surflare acceptance re-test of e50b375 (consumer
loop close) + forge doctor phase (productize the recurring UX diagnosis).
Phase-count RECOUNT (2026-07-09): v2.8 = 11 units (done 9: 37/37.1/38/38.1/38.1-5_6/38.2/38.3/39/43; pending 3: 40/41/42) = 82%. Convention: sub-phases count as units; Phase 43 (LEDGER) is counted ONCE here in v2.8 where it physically landed, though it is logically the v3-arc root (44/51 consume it) -- NOT double-counted in v2.9.

Milestone: v2.8 Onboarding + Throughput (10/11 phases complete)
Completed: 37 user-config (6fb427e), 37.1 F5+F1 (965c247), 38 setup-mcp (07d0381), 38.1 stale-guard (0a85662), Phase 43 LEDGER (14328bb), 38.2 PDEATHSIG (9f96fd5), 39 parallel-passes (6abb6fb), 38.1-5/6 contract+duration (14b3985), Phase 46 doctor tool-audit (f53bf84)
Also landed (pre-Phase 39, this session): sig_b OSError (0b94363), output-ceiling (8991129)
Current: Phase 40 (next in queue)
Next: Phase 40 (queue: 40 -> 41 -> 42; see the authoritative QUEUE note above -- Phase 44 is the parallel v2.9 lane root, not in-flight)
Open 38.x tail:

- 38.2 PDEATHSIG: MERGED 2026-07-04 (9f96fd5). All B1-B4 blockers
  closed, mimo-pro 3-round convergence, commit messages reworded.

- 38.1-5 + 38.1-6: MERGED 2026-07-09 (14b3985). Contract heading
  matcher (fuzzy + level-aware + CommonMark indent guard) + duration
  inflation fix (wall-clock for parallel paths). 7 commits, 464+/15-,
  2647 tests pass, PM L4 verified across 3 rounds.

- 38.2 ppid probe: RUN 2026-07-04 -- zero orphans (3 live servers,
  all with live claude parents, ages match). Deciding fact still
  uncollected; send_ping stays gated. Spec's cmdline-grep probe is a
  false-negative (0/3 hits); corrected comm-match probe verified 3/3:
  for d in /proc/[0-9]*; do [ "$(cat $d/comm 2>/dev/null)" =
  "code-forge-mcp" ] && echo "$d ppid=$(awk '/^PPid:/{print $2}'
  $d/status)"; done

- 38.2 B5 tripwire: HALF-RETRACTED. The "distinct log per PDEATHSIG
  exit" half is unsatisfiable (normal parent-death SIGTERM is
  signal-indistinguishable) -> dropped. The OTHER half -- observe
  whether a parent-ALIVE orphan ever exists -- stays OPEN and is
  exactly 38.4 GATE A's deciding fact (ppid probe above = current
  reading, zero so far). Not superseded by PDEATHSIG (that covers
  parent-DEATH only).

- 38.4 send_ping = SEPARATE gated unit (NOT bundled into 38.2 --
  unproven + false-kill-capable must not block the proven PDEATHSIG
  fix). Briefing /tmp/draft_20260704_phase38.4_send_ping.txt. Double
  gate: GATE A necessity (tripwire catches a parent-ALIVE orphan) +
  GATE B efficacy. SDK grounded: send_ping server/session.py:443.

- send_ping CLIENT-COMPAT TEST RUN 2026-07-04 (real, not desk):
  * Python axis 3.8-3.14 mostly N/A: MCP SDK (mcp 1.27.0)
    requires-python >=3.10 -> 3.8/3.9 CANNOT run send_ping at all
    (server-side floor, client-independent; 3.9 confirmed installed
    but below floor). 3.10-3.14: ping is JSON-RPC protocol-level,
    server Python version invisible to client, one SDK serves all ->
    axis collapses to per-CLIENT. Tested on 3.14 (forge's deploy).

  * GATE B HALF-A PASS for Claude Code: real headless CC ran a probe
    tool firing ctx.session.send_ping(); server log ground truth
    "client=claude-code/2.1.197 -> ANSWERED in 2.4ms" (Python ref
    client control 1.0ms). CC answers server pings -> false-kill
    risk on healthy connection RETIRED for CC. HALF-B (does CC stop
    answering when abandoned) still open -> needs T1 pinger + real
    orphan. Codex/Copilot untested (not installed; gh copilot is not
    an MCP stdio client). Harness kept /tmp/ping_probe_server.py.

  * CLIENT-FAMILY MATRIX extended 2026-07-04 (all py3.14, server-log
    ground truth): Claude Code 2.1.197 ANSWERED 2.4ms (real); TS ref
    SDK 1.29.0 ANSWERED 2.5ms (proxy for VS Code/Cursor/Windsurf --
    all on @modelcontextprotocol/sdk, ping auto-answered in Protocol
    base); Python ref SDK 1.27.0 ANSWERED 1.0ms (control). Both SDK
    families auto-answer -> false-kill risk broadly retired.

  * Still no DIRECT test: PyCharm (native Kotlin client, plugin
    `mcpserver`; NO proxy covers it -- genuinely untested) and VS
    Code GUI (TS-family implies yes; bundle grep inconclusive, core
    minified). Flatpak VS Code sandbox has NO python3.14 -> probe
    (and forge) MUST launch via flatpak-spawn --host; user's real VS
    Code mcp.json already does this + pins FORGE_PROJECT_DIR=
    ~/code/hermes-agent (path EXISTS, so not stale there, unlike the
    ~/.claude.json trinity pin). GUI-probe handoff (30s/IDE):
    /tmp/draft_20260704_send_ping_gui_test.txt

  * FLATPAK VS CODE SPAWN ROOT CAUSE found 2026-07-04 (live, MCP
    server log): forge/probe MCP fails NOT from gpg/multi-instance
    but because flatpak-spawn --host inherits cwd = VS Code's
    document-portal path /run/flatpak/doc/<hash>/<folder> (sandbox-
    only FUSE); host chdir fails "No such file or directory", exit 1,
    zero output. Fix VERIFIED: add --directory=<host path> to
    flatpak-spawn args (--directory=/tmp made full MCP handshake
    succeed). forge's VS Code config needs --directory=<host project
    root> (doubles as walk-up root since FORGE_PROJECT_DIR removed).
    Folded into 38.3 briefing as "why sampling matters" (sampling
    outlet = no host spawn at all -> immune to both gpg and cwd
    failure; strongest Flatpak onramp). This spawn layer is NOT
    forge-fixable in code; the fix is config (--directory) + the
    sampling on-ramp.

- 38.1-5 + 38.1-6: MERGED 2026-07-09 (14b3985)
- MCP "failed" status diagnosed 2026-07-04: launch chain verified OK
  now (pass rc=0, wrapper rc=0); failure is state-dependent -- wrapper
  runs `pass show` x2 pre-exec with no TTY (gpg cache cold = dead/hung
  wrapper = CC "failed"). Secondary confirmed: FORGE_PROJECT_DIR env
  trusted blindly at module import (mcp_server.py:106, no existence
  check) -> stale path poisons every call even when "connected";
  wrapper ~/.local/bin/code-forge-mcp-pass is UNTRACKED in any repo.
  38.3 MCP credential UX APPROVED + REFRAMED 2026-07-04 (user asked
  "better way? account/vertex/own key+url?" -- ground-truth answer:
  2 of 3 already exist). sampling outlet = client's own model, no key
  (outlet_resolver.py:12/51, factories.py:440); vertex = OAuth2 via
  credentials_path, no gpg (backend.py:94, llm_invoke.py:733). Only
  gap = api-key backends are env-var-ONLY (backend.py:88,
  llm_invoke.py:739), which is what FORCED the gpg wrapper. So 38.3 is
  NOT wrapper-hardening -- it RETIRES the wrapper: T1 FORGE_PROJECT_DIR
  validate+walkup-fallback, T2 api_key_path file field (trust-gated
  like credentials_path; arbitrary-file-exfil risk noted), T3
  onboarding flip to the 3 robust paths + README matrix, T4 wrapper
  demoted to opt-in. Briefing:
  /tmp/draft_20260704_phase38.3_mcp_credential_ux.txt

## Performance Metrics

**Velocity:**

- Total plans completed: 69 (25 phases across v2.0-v2.7)
- Average duration: 13m 43s
- Total execution time: 27m 26s

## Accumulated Context

### Roadmap Evolution

- Phase 46 COMPLETED: doctor tool-audit merged (f53bf84). _audit_tools + whitespace guard + 10 tests + injection matrix. mimo-pro backend deleted (401), unified on OmniRoute.
- Phase 46 added: doctor: registry-vs-executed tool audit -- verify every registry entry actually runs in the pipeline (prevent resolve-command false-green class)
- Phase 18.1 inserted after Phase 18: P0: forge test suite pollutes real repo .git (hooksPath/identity/stub hooks/fixture commits on live branch); fix isolation + regression guard + pre-commit non-code carve-out, then re-arm R1. Until landed: targeted tests only, never full pytest (URGENT)
- Phase 19.1 inserted after Phase 19: Presubmit Gate Hardening: external deterministic commit/send gates for checkpatch+vng+anti-AI (URGENT)
- 2026-07-08 AMENDMENT 1 appended to v2.9-V3-GROUNDTRUTH-SCHEDULE.md (fleet PM, user-commissioned; rev 2 after ds+lc external review, 0 blockers, ledger in dispatch/forge-env-r1-adjudication.txt): ENV-GROUNDING lane Phases 51 BASIS-DISCLOSE / 52 ENV-MANIFEST / 53a EXEC-FALSIFY native / 53b container opt-in + fix-delivery constitution (diagnose/propose/apply three authorities; delivery = patch artifact never applied state; AutoFixer stays stub-for-delivery until 53a; forge never writes the reviewed tree's code, .code-forge/ carved out). Parallel lane to the 45-49 flywheel; does NOT change the 40 -> 41 -> 42 working queue.

### Decisions (carried from v2.3 + v2.4 founding)

- Deterministic 3-cycle pre-commit pipeline = sole gate (founding principle)
- Advisory axes NEVER block; only FIXVAL and TRUST/SEC may block
- Agentic-as-gate = anti-feature, stays OUT
- RUNTIME-01 is ADVISORY, NOT a 4th blocking gate
- Eval corpus = real bugs only (no synthetic)
- Do NOT vendor inspect-core (FSL) -- sem-core (MIT/Apache) only
- SEC-01 is urgent (CVE-class, partly live on main) -- scheduled Phase 17
- danger_score_from_diff constructs StateFinding directly (no SARIF round-trip; D-15 deviation)
- D-12 open-as-sink deferred per self-loop constraint (open is source only in forge-taint.yaml)
- Provenance test uses git-rev-parse cwd anchor for worktree/editable-install compatibility

### Pending Todos

- ~~**USER-DECIDE: review pipeline self-attestation gaps**~~ -- **RATIFIED**
  2026-08-08, placed in the v2.8 tail as Phase 43.1; the "needs user slotting"
  line below is closed. Charter item 1 (receipt timestamp) landed on main as
  888333a and the mutation/l2_runner pair as 8f745dd, so the scope the charter
  ratified is smaller than the charter text describes -- read the charter's
  backlog-mapping table against the 08-09 reconcile before planning. Original
  entry kept below for the item descriptions.

- **USER-DECIDE: review pipeline self-attestation gaps** (2026-08-01) --
  charter_review_pipeline_gaps.md. 6 items found while main-session
  verifying fix-receipt-ts (accepted on direct evidence, not blocked on
  these): mutation-gate engine's own stale receipt.py, e2e_runner CLI
  wiring (l2_runner's sibling gap, same shape, now fixed for l2 only),
  mutation baseline's hardcoded 120s timeout vs gate.yaml's configured
  900s, an unresolved intra-run cache-replay mechanism (cycles 2-3 of a
  3-cycle LOCAL review returned byte-identical output at cache speed),
  a missing negative test for timestamp rejection, and a coverage-floor
  calibration question for test-heavy diffs. Not numbered into any lane
  (checked against 45-50 and 51-53b, no collision) -- needs user
  slotting decision before a plan.

- ~~**USER-DECIDE: merge three ready branches**~~ -- **RESOLVED** (2026-06-11).
  All three (forge/p18-ai-smell 05508e7, forge/p18.1-test-isolation 9411e9c,
  docs/readme-r1-install 05b936f) merged to main and pushed clean to public
  origin (66e8df0..7e4c110). A re-leaked Phase 17 .planning set was purged via
  filter-repo before the push; origin verified zero .planning (gh api).

- **USER-DECIDE: stash@{0} disposal** (2026-06-11) -- WIP from branch
  worktree-agent-a0a2b136f6e0aad78 (12-04 backend/tests era). Inspect or drop.

- ~~**EVAL-CORPUS-REPAIR**~~ -- **RESOLVED** (2026-06-10).
  All 9 corpus entries now apply (base_files seeds + diff header fixes).
  Runner uses seed-then-patch. Guard test_all_corpus_entries_apply passes 9/9.
  Skip-taxonomy: infra failures score SKIPPED, not caught. 1315 tests pass.

### Blockers/Concerns

- ~~**INTERIM BAN**~~ -- **LIFTED** (2026-06-11). Phase 18.1 landed
  (9411e9c): session-scoped GIT_CEILING_DIRECTORIES + per-test ceiling +
  regression guard. Full pytest verified 1371 passed, 0 failed, .git
  state clean. SC 2 met.

SEC-01 resolved in Phase 17 (trust gate shipped).

## Session Continuity

Last session: 2026-08-09 (defect-batch session)
Stopped at: main @ c72ff06, pushed. Eighteen commits since 695f739; the stderr
both-ends fix is the last of them.
Resume: no phase in flight. The open queue is the eight pending todos (#50,
#53, #54, #55, #58, #59, #60, #61) plus Phase 43.1 (charter, ratified 08-08,
scope now smaller -- see the 08-09 reconcile) and the Router batch remainder
(F3 trust path, F4 live probe, F2/F5 docs; SSE parse tolerance deferred on a
stated trigger). #54 carries [BLOCKS DEPLOYMENT].
Cleanup first: the merged agent worktree is breaking bare `pytest` collection
and is the likeliest source of the mutation-flaky advisory.
F-3 binding: DISCHARGED

Superseded entry (2026-07-23): Phase 47 merged (ca0d860); resume was Phase 41,
queue 41 -> 42. Both phases have since merged.

---
*State initialized: 2026-06-09 (v2.4 roadmap created)*

## Post-reboot: VS Code direct send_ping test PASSED (2026-07-04)

After reboot + the --directory fix, VS Code's real MCP client
answered a server-initiated ping: `client=Visual Studio Code/
1.127.0 -> ANSWERED in 16.2ms` (server-log ground truth). This is
the DIRECT GUI confirmation (supersedes the TS-SDK proxy). Two
takeaways: (1) --directory fix works end-to-end through the real
VS Code MCP client -> forge's own VS Code config fix is validated
by the same path; (2) 16.2ms >> SDK's 1-2.5ms -> 38.4 T1 ping
timeout must be seconds, sized from slowest real client under
load. GATE B half-A now confirmed DIRECTLY for both clients that
matter here (CC + VS Code). Only PyCharm's native Kotlin client
still untested (harness README has the steps). Updated the 38.4
dispatch matrix + memory + probe README.

## PyCharm send_ping test PASSED via Gemini plugin (2026-07-04)

PyCharm cell CLOSED. Gemini Code Assist plugin (backend Gemini 3.1
Pro Preview) answered a server-initiated ping: server-log ground
truth `client=aiplugin-mcp-client-ping-probe-stdio/0.0.1 ->
ANSWERED in 0.8ms`. That is the 5th confirmed client; GATE B
half-A now PASS across CC (2.4ms) / VS Code (16.2ms) / Gemini-
PyCharm (0.8ms) / TS-SDK (2.5ms) / Python-SDK (1.0ms) -- three
distinct client families, false-kill-on-healthy-connection risk
broadly retired. Only Codex left, not worth chasing (data covered).

ROOT CAUSE of the multi-hour PyCharm hunt (this is the reusable
finding): PyCharm has THREE+ INDEPENDENT, non-communicating MCP
registries -- (1) AI Assistant -> ~/.ai/mcp/mcp.json, (2) each ACP
agent entry (Claude Agent / Codex / GitHub Copilot / Gemini CLI /
Junie) via its per-agent "pass custom MCP servers" toggle (labels
observed on a Chinese-locale PyCharm UI), (3) Gemini Code
Assist -> Tools > Gemini > MCP Servers. Registering in one does
NOT expose the server to the others. Same ping-probe was live+
answering in Gemini's registry while GitHub Copilot's ACP `/mcp
list` returned "No MCP servers configured" despite the toggle ON
(config passthrough to the Copilot ACP is empirically broken --
not a ping failure, probe was simply unreachable there). This
silent-in-one-registry / absent-in-another split is almost
certainly the same class as forge's own `Forge failed` reports.
Full pain-point catalog + forge onboarding requirements recorded
in memory project_forge_38x_mcp_hardening_2026-07-04.md (Debug
session pain points section). Zombie probe procs cleaned, 8765
freed. Probe README results table updated + planning snapshot.

## Pre-reboot solidification (2026-07-04, done)

User is rebooting; /tmp confirmed tmpfs (wiped on reboot). Rescued
today's live artifacts to durable disk storage (byte-diff verified
identical to /tmp originals):

- .planning/dispatch/draft_20260704_{phase38.2_gate_return,
  phase38.3_mcp_credential_ux, phase38.4_send_ping,
  phase43_ledger_dispatch, send_ping_gui_test}.txt

- .planning/evidence/send_ping_probe/{ping_probe_server.py,
  ping_probe_client.py, README.md (rerun instructions + results
  table + the flatpak cwd bug note)}

- Project memory: project_forge_38x_mcp_hardening_2026-07-04.md
  (full session narrative) + MEMORY.md index line
Also fixed proactively (was going to break identically on next
use regardless of pending questions): forge's REAL VS Code
mcp.json now carries --directory=/home/houminxi/code/hermes-agent
(backup .bak_20260704_065626) -- same flatpak-spawn cwd bug the
ping-probe test hit would have hit forge itself.
NOT rescued (out of scope, different projects, already
actioned/merged per their own history): the ~90 other /tmp
draft_2026*.txt files from Jun27-Jul3 spanning ashare-lab/harness/
other forge phases. Flag to user: if any of those are still
needed, they vanish on this reboot too -- say so before reboot,
don't silently let them go.

- Forward /tmp/draft_20260704_phase38.2_gate_return.txt to the 38.2
  exec session (fix on branch orphan-guard, resubmit)

- Dispatch /tmp/draft_20260704_phase43_ledger_dispatch.txt (Phase 43
  LEDGER, anchors re-verified at 0a85662) to a fresh exec session

- 38.1-5/6: MERGED 2026-07-09 (14b3985)
- Fix ~/.claude.json forge env: remove FORGE_PROJECT_DIR (stale path;
  server walk-up takes over). Edit outside a live CC session, then
  /mcp Reconnect in affected sessions

- Dispatch /tmp/draft_20260704_phase38.3_mcp_credential_ux.txt
  (credential UX: retire the wrapper; T1 env-validate + T2 api_key_path

  + T3 onboarding flip). Parallel-safe with 38.2/43 (different files).
- Open v2.9 milestone requirements via GSD flow at/before 43 merge
- Queue: 43 -> 39 -> 40 -> 44 -> 41 -> 42 (per v2.9 schedule)
