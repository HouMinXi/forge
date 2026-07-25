# Phase 41 (Review focus) -- ACCEPTANCE

**Merged:** 2026-07-25, main @ 74adbf2 (fast-forward from phase-41-review-focus,
7 commits on ca0d860, +565/-60, 18 files incl. new tests/test_focus.py).
**Status:** DONE. v2.8 count 16/17 (94%); only Phase 42 remains.

## Delivered (all 8 Must-Haves)
- "## Contract Reference" renamed to "## Design Intent" in all 3 prompt builders
  (cli.py:786, factories.py:282, factories.py:584); all test occurrences updated.
- Review-focus emphasis mechanism at parity with --contract: gate.yaml
  `review_focus` (trust-gated) + `--focus FILE` CLI flag + MCP `focus` param,
  merged into a "## Review Focus" section on all 3 builders, distinct from
  design-intent.
- `review_focus` has its own trust hash: untrusted or post-trust-edited focus is
  dropped with a warning, and dropping it does not break backend loading
  (trust independence, verified live).
- git_blame() parses committer-time into a UTC "date" key; attribution includes
  the ISO date; missing-date degrades gracefully (join filters empties).
- Full suite passes with zero regressions.

## PM independent verification (not executor self-report)
- Full suite 2903 passed / 8 skipped / 0 failed, on the REAL editable-install
  main path (no PYTHONPATH override), 582.12s. Re-confirmed post-merge on main.
- Per-site bug-injection on all 3 header sites, each proven caught:
  cli.py:786 -> test_contract_wiring; factories.py:282 -> test_contract_wiring +
  test_cross_repo_contracts; factories.py:584 -> test_mcp_server (build_sampling_l1_provider).
- Post-trust-edit adversary (isolated trust store): trusted focus authorized;
  editing review_focus after trust -> dropped + warned, tampered text never
  surfaces, BACKEND trust stays valid (independence proven), re-trust restores.
- Degenerate --focus: empty file -> "" no crash; missing file -> clean CliError.
- Diff scope: did not re-touch the already-merged 2edb9d4 / 5c8e001 hunks;
  touched no kimi file; non-ASCII 0.

## Findings
- F1 (minor, fixed pre-merge): plan-ref "D5.6" in 3 code comments (cli.py) + 1
  commit body. The repo's own `[Dd]-[0-9]` self-check misses it (no hyphen in
  "D5.6"). User rebased to strip it; range-diff proved the amendment was
  comment/message-only with zero logic lines touched; py_compile + B-B re-run
  green on the amended tip.
- F2 (dismissed): legacy attribution suspected double-space on missing date;
  actual code `" ".join(p for p in parts if p)` filters empties. Correct.
- F3 (report nit): executor report's bug-injection table named test_cross_repo
  as factories.py:584's cover; the real cover is test_mcp_server:2229. Coverage
  exists; attribution corrected here. Disclosed per S1 (a PM verification flip:
  first suspected false-green, then proven covered).

## Scope boundary
Sibling phase 41-sampling-fix (2edb9d4 contract_spec wiring + 5c8e001 tmpfile
leak) was merged earlier, out-of-milestone, and explicitly NOT rebuilt by this
phase -- verified as ancestors of HEAD, diff did not re-touch their hunks.

## Artifacts
- cp-artifacts/cp3-impl-pm-verification.md -- full mechanical + adversary log.
- cp-artifacts/cp3-impl-bb-posttrustedit.py -- persisted B-B adversary script (S2).
- cp-artifacts/cp3-impl-executor-report.txt -- executor's original deliverable.

## Cleanup
Worktree removed; branch phase-41-review-focus + tag phase-41-rescue deleted
(user-owned; both confirmed fully merged into main before deletion).

## Not run (disclosed)
Forge's formal CP3 external panel (kimi/gemini/deepseek, 3 rounds 0/0/0/0) was
NOT run. A sub-session forge_review on deepseek-v4-flash (3 internal passes, 0
findings, ~32K tokens) was reported but is NOT persisted to disk, so it could
not be PM-verified; it is a single-backend signal, not a full CP3. User elected
to merge on the strength of the independent PM verification above.
