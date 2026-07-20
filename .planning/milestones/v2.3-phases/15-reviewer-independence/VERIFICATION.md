# Phase 15 Verification -- Reviewer Independence (SHRK-02)

Verifier: main session (independent of sub-session implementer)
Date: 2026-06-08
HEAD verified: c011286
Method: ground-truth against git + tests + actual file reads (no rubber-stamp)

## Verdict

PASS WITH FINDINGS.

Phase 15 success criteria SC1-SC4 are implemented, wired, and tested. Full suite
green at HEAD (Section 4). Two LOW-severity reporting-accuracy findings and one
report omission (now a memory) are recorded. No CRITICAL or HIGH issues.

## 1. Git ground-truth (commits + footprint)

9 commits confirmed in range b030bd4..c011286 (b030bd4 = pre-Phase-15 main HEAD):

    c011286 chore: merge forge review fixes
    cb89e49 forge/review: extract _assemble_post_image helper, strip plan-ref tags
    ff12f74 chore: merge executor worktree
    682a2e4 feat(15-02): wire cross-repo resolver into get_digest; add resolver tests
    fe49414 feat(15-02): add cross-repo conventions-seed resolver
    e93d588 chore: merge executor worktree
    7455876 feat(15-01): test-assertion gate (SC4), human backstop (D13), SC1-SC4 tests
    d7e3b89 chore(15-01): merge Phase 14 base files into Phase 15 worktree
    24c1fd4 wip(15-01): save work before merging missing Phase 14 base files

CORRECTED footprint (net diff b030bd4..c011286): 10 files, +2483 / -69.

    New (4):
      src/code_forge/conventions.py             (158 lines)
      src/code_forge/conventions_resolver.py    (812 lines)
      tests/test_conventions.py                 (151 lines)
      tests/test_conventions_resolver.py        (548 lines)
    Modified (6):
      src/code_forge/cli.py                     (+307 net region)
      src/code_forge/factories.py               (24 lines)
      src/code_forge/machine.py                 (2 lines -- docstring only, P3 fix)
      src/code_forge/skills/code-forge/SKILL.md (+34)
      tests/test_cli_integration.py             (+280)
      tests/test_outlet_c.py                    (+236, SC1-SC4 tests added)

NOTE -- the execution report's file count was inflated. It claimed "7 new + 15
modified". Ground truth is 4 new + 6 modified. The report measured against the
sub-session worktree's fork point (which predated Phase 14), so it swept Phase
14 files (outlet_c.py, reviewer_json.py, diff.py, verify.py, receipt.py) into
the Phase 15 ledger. Confirmed: outlet_c.py, reviewer_json.py, test_outlet_c.py
all already existed at b030bd4 (Phase 14). machine.py shows only a 2-line net
change in Phase 15 (the P3 docstring fix), NOT the 4-tuple work the report
attributed to Phase 15 (that landed in Phase 14). See Finding F1.

## 2. Success Criteria (SC1-SC4) -- all VERIFIED

SC1  Fresh Agent (fresh context) per review pass
     -> cli.py:454 _make_subagent_spawn(backend, conv_digest, post_image) returns
        a _spawn(pass_name, diff_text) closure that calls llm_invoke per pass
        (cli.py:500). No shared session across passes. The Phase-14
        NotImplementedError stub (old cli.py:783) is GONE -- grep = 0.
     -> tests: TestSubagentSpawnIntegration, TestIndependence (test_outlet_c.py).

SC2  Only diff + criteria passed to reviewer
     -> prompt (cli.py:476-499) = pass role + JSON schema + post_image (opt) +
        conventions digest (opt) + diff_text. No implementer session context.
     -> payload assembled by _assemble_post_image (cli.py:509) + get_digest.
     -> tests: TestCriteriaPayload.

SC3  No implementation-context leakage
     -> each _spawn imports llm_invoke fresh and builds the prompt only from its
        arguments; no module-level shared state carrying impl context.
     -> tests: TestContextIsolation.

SC4  Test-assertion review != implementation agent
     -> cli.py:542 _run_test_assertion_review: separate function, own llm_invoke
        (cli.py:559), runs on BOTH outlets (C: cli.py:988, A: cli.py:1093) before
        the verdict. Advisory-only (D8 exception, documented at the call sites and
        in the docstring) so it does not contaminate the 3-cycle counter.
     -> precise test-file heuristic (cli.py:564-573) excludes contest.py/protest.py.
     -> tests: TestAssertionGate.

## 3. Design decisions realized

    D11 conventions-digest slot -> conv_digest threaded into reviewer prompt
                                   (cli.py:494-498).
    D12 cross-repo resolver     -> conventions_resolver.py: resolve_sources (4
                                   prioritized sources), extract_conventions,
                                   get_cross_repo_digest (cached).
    D13 human backstop          -> SKILL.md Step 8 "Human Backstop" (SKILL.md:82,
                                   1143, 1170: "All code changes require the human
                                   backstop").
    D14 test-assertion gate     -> SC4 above; separate gate before R1, fresh
                                   llm_invoke.

## 4. Test verification (at HEAD c011286)

    Collection : 1160 tests collected, 0 collection errors (no import breakage
                 from the forge-review refactor). 1 benign PytestUnknownMark
                 warning (unregistered `integration` mark, pre-existing).
    Affected   : 133 passed, 5 skipped, 0.53s (test_conventions,
                 test_conventions_resolver, test_outlet_c, test_cli_integration,
                 test_factories, test_machine_local, test_verify).
    Full suite : 1155 passed, 5 skipped, 3 warnings in 285.24s (4:45) -- GREEN
                 at HEAD c011286 (main session ran it, exit 0). This CLOSES the
                 execution report's gap (d) "full suite not run after forge-review
                 merge". 1155 + 5 = 1160 = collected total.

The only HEAD-vs-Post-Wave-2 delta is the two forge-review commits (cb89e49
refactor + c011286 merge): a pure DRY extraction (_assemble_post_image) plus a
comment-strip. The full suite confirms no regression.

## 5. Forge review fixes (3 claimed)

    P2 DRY (post-image duplication) -- VERIFIED. _assemble_post_image (cli.py:509)
       is defined once and called by BOTH outlets (C: cli.py:975, A: cli.py:1027).
    P3 docstring (machine.py 3-tuple vs 4) -- VERIFIED. machine.py net change is
       2 lines (docstring only), consistent with a docstring-only fix.
    Plan-ref tag strip (claimed 57 stripped, "kept pre-existing D-16") -- INCOMPLETE.
       Residual plan-ref tags at HEAD: conventions.py = 2 (M-04, M-05),
       conventions_resolver.py = 10 (M-R2-06 x5, B-03, H-05 x2, L-R4-08 x2),
       cli.py = 7 (incl. the intentionally-kept D-16 gate.yaml loader tag, plus
       H-R3-01, M-R2-07, R4-M2, R4-L3, R2-M4, D-04/D-14). The two NEW Phase 15
       files alone carry 12 residual tags. See Finding F2.

## 6. Deviations

    D-a Symlink guard centralized into _symlink_guard_passes()
        (conventions_resolver.py:60) instead of 3 inline copies. VERIFIED
        SECURITY-EQUIVALENT by reading the code: resolves via os.path.realpath +
        Path.resolve, then checks containment with `parent_root in
        real_path.parents` (path-component-safe) -- NOT str.startswith. The
        docstring explicitly calls out and avoids the /tmp/repo vs /tmp/repo_evil
        prefix-collision. Trust boundary not weakened; arguably more auditable.
    D-b Commit d7e3b89 "merge Phase 14 base files" -- a worktree-base artifact
        (the sub-session worktree forked from a pre-Phase-14 base), not a code
        change. Net effect on main (b030bd4..c011286) is clean Phase 15 only.
        Drives F1.

## 7. Findings

F1 LOW (reporting accuracy): execution report file count inflated (7 new + 15
   modified) vs ground truth (4 new + 6 modified). Cause: counted against the
   stale worktree fork point, sweeping in Phase 14 files. No code impact;
   corrected in Section 1. Recommendation: report a phase footprint by diffing
   against the prior main HEAD, not the worktree fork point.

F2 LOW (AI-smell / process): plan-ref comment tags incompletely stripped. 12
   residual tags in the two new Phase 15 files (report claimed a complete strip
   keeping only D-16). Violates feedback_no_planref_comments_in_code. No
   functional impact (comments). Recommendation: a follow-up strip pass over
   conventions.py + conventions_resolver.py (and re-check cli.py Phase-15 regions).

F3 INFO (report omission): the execution report describes only the successful
   inline forge review. It omits the FIRST attempt -- a DeepSeek-backed forge
   review that returned Exit 0 false-green because all 3 passes were dismissed
   on JSON parse errors over the 3560-line / 22-file diff (fail-open). The inline
   re-do (Outlet B, Opus 4.6) is what actually found the 3 fixes. Recorded as
   memory feedback_forge_false_green_large_diff. Not a Phase 15 code defect; it
   is a forge gate defect (dismiss-on-parse-error should be fail-closed) plus a
   report-completeness gap.

F4 INFO (by design): the SC4 test-assertion gate is advisory / fail-open (D8
   exception) -- it signals the human backstop, it does not BLOCK the pipeline.
   This is the locked design (CONTEXT D14 + D8 exception), not a defect, but it
   means test-assertion quality is enforced by the human (Step 8), not the gate.

## 8. Verdict and next steps

Implementation: SC1-SC4 met, design decisions D11-D14 realized, deviations
security-equivalent, full suite green (1155 passed / 5 skipped), collection clean.
Findings are LOW/INFO (reporting accuracy + AI-smell residue + one honest report
omission already turned into a memory). No CRITICAL/HIGH.

VERDICT: PASS WITH FINDINGS. Phase 15 deliverables are real and verified.

Next steps:
  - Mark Phase 15 complete; sync ROADMAP/STATE; run git snapshot-planning.
  - F2 cleanup (strip residual plan-ref tags) -- small follow-up; fold into
    Phase 16 prep or a quick chore commit.
  - F3 forge defect (fail-closed on parse error) -- ROADMAP candidate.

## Addendum (2026-06-08, post-verification) -- history squash + F2 resolution

After this VERIFICATION.md was finalized, two changes landed. This section records
them so the artifact stays accurate.

1. Commit squash. The 9 Phase 15 commits (c011286 .. 24c1fd4) were squashed into a
   single clean commit 1e1550d on top of b030bd4. Section 1's commit list and the
   "HEAD verified: c011286" header refer to pre-squash history (c011286 is now
   dangling). Current HEAD: 1e1550d "reviewer-independence: enforce fresh context
   per pass with conventions-seed". Re-verified: `git diff c011286 1e1550d` is
   comment/docstring only (zero logic change); `git ls-tree -r 1e1550d` contains
   0 .planning and 0 CLAUDE.md entries -- the squash did not leak gitignored files.

2. F2 resolved. The 12 residual plan-ref tags flagged in Finding F2 were stripped
   from conventions.py and conventions_resolver.py as part of the squash. Re-grep at
   HEAD: conventions.py = 0, conventions_resolver.py = 0 residual tags. The strip is
   comment-only; affected test subset re-run at 1e1550d = 133 passed, 5 skipped.
   The full-suite 1155-green carries (the only delta from the verified-green tree is
   comments). F2 status: RESOLVED. (cli.py plan-ref tags, including the
   intentionally-kept D-16, were left as-is -- LOW, partially pre-existing.)

Net: verdict unchanged (PASS), and stronger -- F2 closed, history clean, no leak.
