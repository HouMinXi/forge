# Phase 41 impl -- PM independent verification (CP2 + CP3 gate)

Date: 2026-07-24. Verifier: forge PM (main session), against the frozen
`dispatch/p41-impl-EXIT-pm-only.md`. Subject: executor branch
`phase-41-review-focus` @ b0b4d03 (7 commits, main @ ca0d860, unmerged/unpushed).
Executor report: `cp3-impl-executor-report.txt`. Every number below was
re-derived by the PM, not copied from that report.

## A. Mechanical verifier
- A1 diff scope: 18 files, +565/-60 -- matches report. No kimi file touched.
  Already-merged 2edb9d4 (sampling contract_spec) and 5c8e001 (tmpfile) were
  EXTENDED, not reverted: read `_dispatch_cli` diff -- contract_tmp lifecycle
  intact, focus_tmp mirrored, all exit paths dual-unlink (now also guards the
  write step under try/except BaseException -- strictly more robust). PASS.
- A2 header rename: `Contract Reference` = 0 in branch src+tests; `## Design
  Intent` at exactly 3 builder sites (cli.py:786, factories.py:282/584);
  `## Review Focus` injected at the same 3 sites, guarded `if focus_spec:`,
  distinct from design-intent. PASS.
- A3 full suite (PM's OWN run, PYTHONPATH=worktree/src, -B, no:cacheprovider):
  2903 passed, 8 skipped, 0 failed in 537.94s. Head-of-log confirms the
  worktree src resolved (not the editable-install main tree). PASS.
- A4 non-ASCII in added diff lines: 0. PASS.
- A5 commit hygiene: 7/7 Signed-off-by Minxi Hou, none AI-authored, wave-ordered,
  WHY-not-WHAT bodies. BLEMISH -> see F1 (plan-ref D5.6 leak; the repo's own
  `[Dd]-[0-9]` self-check misses it because D5.6 has no hyphen).
- A6 Must-Haves (8/8): rename (A2); focus mechanism + `## Review Focus` on 3
  builders (A2, grep); own trust hash + drop-untrusted + no-break-backend (B-B);
  git_blame committer-time->UTC date + ISO in attribution (read git.py/legacy.py
  diffs); test_legacy date+degradation (commit 26d161e); full suite (A3). PASS.
- A7 coverage (not just liveness): all 3 header sites injection-proven (B-A);
  focus trust proven (B-B); blame date proven (test_legacy). PASS.

## B. Held-out adversary (executor never told of these)
- B-A per-site bug injection (Golden Rule 2, inject at each site separately):
  revert each header site to "Contract Reference", confirm a test FAILS.
  Result -- all 3 sites genuinely covered:
    cli.py:786       -> test_contract_wiring (1 failed)
    factories.py:282 -> test_contract_wiring (2 failed) + test_cross_repo (1 failed)
    factories.py:584 -> test_mcp_server:2229 (3 failed) [build_sampling_l1_provider]
  Baseline (no injection) all pass; every restore clean; worktree clean after.
  PASS. See F3: the executor report mislabeled 584's covering test.
- B-B post-trust-EDIT (isolated XDG store, real store untouched): trust focus,
  then edit review_focus only -> dropped + warned; tampered text never surfaces;
  BACKEND trust stays valid after the focus edit (D5.6 independence proven, not
  asserted); re-trust restores. 8/8 PASS. Script: `cp3-impl-bb-posttrustedit.py`.
- C degenerate focus: empty file -> "" no crash; missing file -> clean CliError.
  PASS.

## Findings
- F1 (minor, FIX BEFORE MERGE): plan-ref token `D5.6` in 3 NEW code comments
  (cli.py:1962, 2301, 2326) and 1 commit body (35568e0). Violates the
  no-plan-ref-in-code rule; the repo's grep self-check (`#.*[Dd]-[0-9]`) does
  NOT catch it (no hyphen in "D5.6"), so a human read is the only gate. Fix:
  reword the 3 comments to state the independence self-containedly (drop the
  D5.6 token) and reword the commit body. Branch is unmerged/unpushed -> amend.
- F2 (dismissed, not a bug): legacy.py attribution join was suspected of a
  double-space on missing date; actual code is `" ".join(p for p in parts if p)`
  -- empties filtered. Correct.
- F3 (report nit, non-blocking, disclosed flip): PM's two-file injection first
  showed factories.py:584 uncaught -> flagged as possible false-green. Widening
  to the only other header-asserting file (test_mcp_server) proved it IS caught.
  So coverage exists; the executor report's bug-injection table merely names the
  wrong covering test for 584 (says test_cross_repo_contracts; real =
  test_mcp_server). /tmp report, uncommitted, trivial. Flip disclosed per S1.

## Gate verdict (initial)
A green EXCEPT A5 (F1). B satisfied (B-A + B-B + C all pass; gate needs >=1).
=> Advance to CP3-external / merge ONLY after F1 is fixed. All substantive code,
coverage, trust-independence, and suite claims independently VERIFIED PASS. F1 is
a 4-line hygiene cleanup, not a design defect.

## F1 CLEARED -- amendment verified (2026-07-24, user amended)
User rebased the branch to strip D5.6. New tip 74adbf2 (was b0b4d03); commits
32eb3da/ae00d5c unchanged, fbc3f2b (was 35568e0) + 3 descendants rewritten.
Verified NOT by trusting the claim but by:
- range-diff ca0d860..b0b4d03 vs ca0d860..74adbf2: ONLY deltas are the 4 D5.6
  tokens removed (1 commit-body line + 3 cli.py comment lines); commits 4-7
  identical patches; zero logic lines touched.
- D5.6/D-5 in code = 0; in commit bodies = 0; no P0-3/Task/Wave.
- non-ASCII on amended diff = 0; Contract Reference = 0; Design Intent = 3 sites.
- 3 rewritten comments read as complete self-contained sentences.
- py_compile OK on all 5 touched sources.
- B-B post-trust-edit RE-RUN against amended tip 74adbf2: 8/8 PASS.
- Full suite NOT re-run: amendment is comment/message-only (range-diff proves
  executable lines byte-identical), so 2903/8/0 stands; a re-run would be theater.

## Final gate verdict
A FULLY green (A5 now clean). B satisfied (B-A + B-B + C). => Phase 41 impl is
CLEARED to advance to CP3 external-model forge review (3 rounds, exit all
0/0/0/0) before merge. Final tip: 74adbf2. Branch unmerged/unpushed.
