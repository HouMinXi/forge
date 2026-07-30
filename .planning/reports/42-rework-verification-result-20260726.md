# Phase 42 rework -- PM verification result

**Date:** 2026-07-26
**Against:** `cp-artifacts/42-rework-order-20260726.md` (the order)
and `cp-artifacts/42-rework-briefing-20260726.md` (the executor's claim)
**Delivered HEAD:** `0dda79b` (old tip `5a7f5ee`, rewrite tip `67f47b2`)
**Verification worktrees:** `.worktrees/p42-rework-verify` (0dda79b),
`.worktrees/p42-inject` (0dda79b), `.worktrees/p42-verify` (5a7f5ee),
`.worktrees/p42-base` (74adbf2)

Every number below was measured by the PM. The briefing was treated as a
claim throughout; nothing in it was carried forward unverified.

---

## Gate outcome

**W1-W5 all DONE. E1 is green.** Two hygiene findings remain open (R1, R2);
neither is code and neither blocks the engineering.

---

## Per-item verification

### W1 -- hooks restored, and fired

`core.hooksPath` is unset at both local and global scope.

The briefing's evidence ran `.git/hooks/pre-commit` directly. That proves the
script rejects, not that `git` invokes it -- which is the entire thing
`core.hooksPath = /dev/null` had broken. So the PM ran the real path instead:

    $ cd .worktrees/p42-base
    $ : > .planning/_hooktest_pm.md && git add -f .planning/_hooktest_pm.md
    $ git commit -m "test: pm hook known-answer probe"
    code-forge: BLOCKED: staged paths must never enter history:
      .planning/_hooktest_pm.md
    exit 1

Probe cleaned up; `git status` clean, HEAD unmoved at 74adbf2.

### W2 -- history rewrite, tree-identical

    git diff 5a7f5ee 67f47b2 --name-status
    D  .planning/phases/42-cli-key-claim-type/42-01-SUMMARY.md
    D  .planning/phases/42-cli-key-claim-type/42-02-SUMMARY.md
    (nothing else)

    git ls-tree -r HEAD --name-only -- .planning   -> empty
    git branch -a --contains 8283d32               -> empty
    git branch -a --contains dacd344               -> empty

Chain went 9 commits -> 5: the two leak commits dropped, two merge commits
flattened away. Nothing else in the tree moved.

**PM error, disclosed.** The order's done-condition said this diff "must be
EMPTY". That was self-contradictory: dropping the two commits that ADD those
files necessarily changes the tree by exactly those two files. The property
worth checking was "only those two files and nothing else", which is what the
executor reported and what is verified above. The spec was wrong, not the work.

### W3 -- red suite fixed, injection re-run by the PM

Fix is `fake_backend.api_key_file = None`, plus `credentials_path = None` and
`api_key_env` pointed at the env dict the test already carried. Diff on
`tests/test_outlet_c_cli.py` is three lines. The guard was not weakened --
`_check_backend_credentials` still validates; `git diff` on `cli.py` shows the
old inline block replaced by delegation, not by a relaxation.

PM re-ran the injection rather than accepting the pasted output:

    guard deleted   -> 1 failed in 0.14s  (AssertionError: guard did not log to stderr)
    guard restored  -> git diff --stat EMPTY (byte-identical revert)
                    -> 1 passed in 0.80s

Artifact: `cp-artifacts/42-w3-injection-pm-rerun.out`.

### W4 -- validators unified, proven by a held-out adversary

`credential_error(backend, env) -> str | None` lives in `backend.py`;
`_check_backend_credentials` raises `CliError` on it, `_probe_api` wraps it in
`ProbeResult` and keeps the vertex ADC fallback probe-only, exactly as the
order's trap section required. `backend.py`'s diff is a single hunk confined
to that region.

The delivered tests do NOT satisfy the order's done-condition on their own.
`TestCredentialErrorTable` (9 rows) exercises `credential_error` alone, and its
docstring asserts that "`_check_backend_credentials` and `_probe_api` inherit
the contract". Inheritance by structure is precisely what F2 disproved once
already. So the PM built the check the order never mentioned: drive BOTH
wrappers over the same matrix and compare verdicts row by row.

    cp-artifacts/42-w4-heldout-union-adversary.py

Against the delivered code (0dda79b): **10 rows, 0 diverged, 0 table
mismatches**, every required substring present.

The instrument was itself known-answer validated against the pre-rework code
(5a7f5ee) before its PASS was believed -- a verifier that cannot fail proves
nothing:

    rows=10 diverged=4 table_mismatch=2   RESULT: FAIL

**New information the report did not have.** F2's blast radius was larger than
recorded. The report documented two contradicting rows; the adversary finds
four:

    row                        fast-fail   probe
    api_key_file empty         REJECT      accept     <- was recorded
    api_key_file 0644          accept      REJECT     <- was recorded
    api_key_file unreadable    REJECT      accept     <- NEW
    neither configured         accept      REJECT     <- NEW

### W5 -- both trivial fixes present

`tests/test_machine_ledger.py` last byte is `0a`, 304 lines, diff is the one
line. `def _check_backend_credentials(backend: BackendConfig, ...)` -- the
annotation is back.

Deviation from the order: W5 rode in `52eeff0` with W4 rather than in its own
chore commit. The order's constraint was that W5 stay out of the W2 rewrite so
"changed nothing at all" stayed checkable; that constraint holds. Folding it
into W4 instead is a minor deviation, noted, not charged.

---

## Test evidence -- and one flip, disclosed

PM run, `.worktrees/p42-rework-verify` at 0dda79b:

    PYTHONPATH=src python -m pytest tests/ -q --timeout=300
    2935 passed, 9 skipped, 4 warnings in 492.50s
    EXIT=0

The briefing reported **2936 passed, 8 skipped**. Collected total agrees at
2944; one test flipped PASS -> SKIP between the two runs. Under S1 that gets
disclosed and root-caused, not smoothed over.

First hypothesis -- the fresh worktree's `graph.db` has no CALLS target, so
`test_pipeline_no_crash_on_real_db` takes its internal `pytest.skip` -- was
tested and **disproved**: both databases return a suitable target
(21620 vs 785 CALLS edges).

The timestamps settle it:

    worktree .git      born 11:08:50
    suite ran          11:08:59 -> 11:17:11   (492.5s)
    worktree graph.db  born 11:17:39          <- 28s AFTER the suite ended

For the whole run the file did not exist, so
`@pytest.mark.skipif(not _GRAPH_DB.exists())` skipped that test. `graph.db` is
a gitignored local artifact a hook rebuilds per working directory; the earlier
8-skip worktree had one from 05:38, before its own run. The extra skip is an
artifact of a brand-new verification worktree, not a property of the code.

**Adjusted: 2936 passed, 8 skipped, 0 failed -- the executor's number is
corroborated. E1 is GREEN.**

Artifact: `cp-artifacts/42-rework-fullsuite-verify.out`.

---

## Scope, hygiene, real path

Files touched by `5a7f5ee..0dda79b`: `backend.py`, `cli.py`,
`test_fast_fail.py`, `test_machine_ledger.py`, `test_outlet_c_cli.py`, plus the
two `.planning` deletions. All inside the fence. The claim_type work
(`claim.py`, `machine.py`, `ledger.py`, `test_claim_type.py`) is untouched.

Non-ASCII gate on added lines: clean.

Real-path smoke (`cp-artifacts/42-realpath-smoke.out`): `doctor` and
`resolve-outlet` both run. `doctor` output is **byte-identical before and
after** the rework, same exit 1 (two backends have no key exported in this
shell). `resolve-outlet` -> `subprocess`, exit 0. No user-visible regression.

Blast radius of the 0600 hard-fail on this machine, measured rather than
assumed: **zero**. Neither `~/.config/code-forge/config.yaml` nor
`.code-forge/gate.yaml` configures `api_key_file` at all -- every backend uses
`api_key_env`.

---

## Open findings

**R1 (MEDIUM) -- `.planning` still in local history, on two stale branches.**

    worktree-agent-ab681072cd99fa49f  54d5e31  .planning/.../42-01-SUMMARY.md
    worktree-agent-ae88bfeee5596845c  b1875da  .planning/.../42-02-SUMMARY.md

Eleven hours old, not checked out by any worktree. The order's done-condition
only checked `main`, so this is a gap in the ORDER, not a failure of the work.
Exposure is bounded: `.git/hooks/pre-push` refuses any ref whose tip carries
`.planning/`, and W1 re-armed it. Fix is deleting both branches; `git branch -D`
is blocked for AI agents by project rule, so the user runs it.

**R2 (LOW-MEDIUM) -- commit message hygiene on the three new commits.**
None carries `Signed-off-by:`, which the commit format requires. `d41508d`
says "The F8 credential guard" twice -- a finding-ID leak that Golden Rule 5
and forge's own commit rules ban. Author identity is correct on all three.
Fix is a message amend; all three are unpushed.

PM error: the scan that should have caught this used `F[0-9]+:` and missed
"F8 credential guard" because there is no colon. Narrow instrument.

**R3 (LOW) -- the union property lives in cp-artifacts, not in the suite.**
The held-out adversary is what actually proves W4's done-condition, and it is
not a test. Nothing stops the two wrappers drifting apart again silently.
Recommend promoting the row-by-row union check into `tests/`.

**R4 (LOW, pre-existing, not charged to this rework)** -- plan/finding refs
already in code outside this diff: `install_hooks.py:272` ("F3 constraint"),
`cli.py:930` ("T-20-05/F5"), `test_llm_invoke.py:1228/1242` ("F1/F2
reproducer"). Reported, not silently fixed.

**R5 (NIT)** -- test docstrings cite `cli.py:2400/2401` for the contracts
guard; it actually sits at ~1832-1841.

---

## PM errors this round

1. W2's done-condition ("diff must be EMPTY") was self-contradictory. See W2.
2. The exit verifier was not frozen before the delivery existed, which R7
   requires. The union adversary was written after. Mitigated by two-sided
   known-answer validation, but post-hoc, and recorded as such.
3. The banned-vocabulary scan pattern was too narrow to catch "F8".
4. The first root-cause hypothesis for the skip flip was wrong and was
   disproved by query before it reached this report.

---

## To close

R1 (delete two stale branches) and R2 (amend three commit messages). Both are
hygiene; the engineering is verified. Then the wrap-up accounting.

---

# Addendum -- R1-R4 cleanup round, PM verification (2026-07-26, later)

**Claim received:** R1 DONE (3 branches deleted), R2 DONE (Signed-off-by, no
F-number), R3 DONE (TestCredentialErrorTable in tests/), R4 recorded as
out of scope.

**HEAD moved** `0dda79b` -> `aaee8c8`. Three commits rewritten:
`d41508d`->`45b2705`, `52eeff0`->`eff61c1`, `0dda79b`->`aaee8c8`.

## Message-only rewrite, proven

    git diff 0dda79b aaee8c8            -> empty
    tree 0dda79b: 0b3a0831a618c1e8d501403500cfcbd13f24f56b
    tree aaee8c8: 0b3a0831a618c1e8d501403500cfcbd13f24f56b   (identical)

The working tree did not change by a single byte. No test re-run was
performed and none is needed: an identical tree cannot produce a different
suite result, and 0dda79b was already measured at 2936 passed / 8 skipped /
0 failed. All six SHAs (old and new) are absent from `origin/main`; main is
29 ahead, 0 behind. Nothing was force-pushed because nothing was pushed.

## R2 -- mostly done, two defects survive

Verified present on all three: `Signed-off-by: Minxi Hou <houminxi@gmail.com>`,
author `Minxi Hou <houminxi@gmail.com>`, and the "F8" leak is gone.

Two defects remain in `45b2705`:

1. **Trailing whitespace**, line 6 of the body. Deleting the token "F8" left
   the line ending in a space:

       Set both to None to skip the $     <- cat -A, '$' is EOL

2. **"Phase 42" survives** -- a plan ID. Global rule bans plan/task IDs in
   commit messages; a git reader who never saw `.planning/` cannot resolve it.

       The credential guard added by Phase 42 fires before the contracts

**PM error, disclosed.** This is the SECOND round my banned-vocabulary scan
was too narrow. Last round the pattern needed a colon and missed "F8"; this
round the widened pattern still had no "phase" term, so "Phase 42" was
already in the original `d41508d` message and I never reported it. The
executor removed exactly what I named and nothing more -- correct behaviour
against a defective specification. The instrument was mine.

## R1 -- branch level clean, verified mechanically

The earlier check (`git branch -a --contains`) is blind to tags, remote-tracking
refs, and `refs/original/`. Replaced with a loop over EVERY ref plus every
worktree HEAD:

    dacd344  -> sole holder: worktree HEAD 5a7f5ee
    8283d32  -> sole holder: worktree HEAD 5a7f5ee

No branch, no tag, no remote-tracking ref, and not `refs/original/refs/heads/main`
(9b6840b, the amend backup -- checked, its tree carries no `.planning`) reaches
the leak commits. The only thing keeping them alive is `.worktrees/p42-verify`,
detached at the pre-rework tip -- my own verification worktree, not a leftover
of anyone's work. Removing that worktree closes it; the reflog will hold them
until expiry, which is normal and unpushable.

I could not verify WHICH three branches were deleted: a deleted branch's reflog
is removed with it, and `.git/logs/HEAD` records no branch deletions. Two of
them are the `worktree-agent-*` pair the user deleted personally. The third is
unaccounted for in my evidence chain. It is not load-bearing -- the property
that matters (no ref holds `.planning`) is verified directly above -- but the
count itself is unverified.

**PM error, disclosed.** `git log --all --single-worktree` reported the leak
commits as still present, which contradicts the per-ref result and would have
sent me hunting a nonexistent ref. Mechanism UNKNOWN -- I did not determine why
`--single-worktree` failed to exclude the other worktree's HEAD. The per-ref
loop is the instrument that settled it; the `--all` variant should not be
trusted for this question again.

## R3 -- NOT DONE. The claim names the wrong artifact.

R3 asked for the row-by-row UNION check -- drive BOTH `_check_backend_credentials`
and `probe_backend` over one matrix and compare verdicts -- to be promoted into
`tests/`. The claim answers with `TestCredentialErrorTable`, which is the thing
R3 was written ABOUT, not the fix for it.

Three independent proofs it did not happen:

    git diff --name-only 0dda79b aaee8c8 -- tests/     -> empty (no test changed)
    git log -S 'class TestCredentialErrorTable' -- tests/test_fast_fail.py
                                                       -> eff61c1 (the W4 commit)
    grep probe_backend + _check_backend_credentials in one file under tests/
                                                       -> no file

`tests/test_fast_fail.py:194` still reads "`_check_backend_credentials` and
`_probe_api` inherit the contract" -- an assertion by docstring, which is
exactly what F2 disproved once. The union property still lives only in
`cp-artifacts/42-w4-heldout-union-adversary.py`, outside the suite, where
nothing runs it and nothing stops the two wrappers drifting apart again.

R3 stays OPEN.

## Standing

| Item | Verdict |
|---|---|
| R1 | Branch level clean (verified per-ref). Residual holder is my own p42-verify worktree. |
| R2 | Signed-off-by and F8 done. Trailing whitespace + "Phase 42" plan-ref still in 45b2705. |
| R3 | NOT DONE -- claim names a pre-existing artifact, no test changed. |
| R4 | Correctly left out of scope. |

Engineering remains green and unchanged. Wrap-up accounting stays blocked on
R3 (or an explicit owner decision to accept the gap and file it forward).

---

# Addendum 2 -- R3 delivery, PM verification (2026-07-26, later still)

**Claim received:** R2 DONE (9 commits, 0 F-number, 0 plan-ID, 0 trailing ws,
8 Signed-off-by). R3 DONE (TestWrapperUnion, 9 tests, drives
`_check_backend_credentials` + `_probe_api` over one matrix). R1 DONE.

**HEAD** `aaee8c8` -> `f7bd6ad`. Three commits rewritten again
(`45b2705`->`eb73c22`, `eff61c1`->`1760be8`, `aaee8c8`->`d5caecb`) plus one
new commit `f7bd6ad`. Tree delta is exactly one file:

    tests/test_fast_fail.py | 95 ++++++++++++++++++++++++-
    1 file changed, 94 insertions(+), 1 deletion(-)

No source file moved. Unlike Addendum 1 the tree DID change, so the suite
was re-run rather than inherited.

## R3 -- DONE, and proven by injection

`TestWrapperUnion` drives both wrappers over 9 rows and compares verdicts.
Reading it is not proof, so it was bug-injected at the site that IS the F2
fix -- the delegation in `_check_backend_credentials`:

    err = credential_error(backend, env)
    +   if err is not None and "group/world readable" in err:
    +       return          <- fast-fail swallows the rule, probe still enforces

    INJECTED: FAILED TestWrapperUnion::test_union_api_key_file_0644
              1 failed, 8 passed
              same tree, TestCredentialErrorTable: 9 PASSED  <- blind, as argued
    REVERT:   git diff --stat empty (byte-identical), 9 passed

That second line is the whole point of R3: with F2 live in the tree, the
pre-existing table-only class stays green. The new class does not. The gap
named in R3 is closed and the closure is demonstrated, not asserted.

Artifact: `cp-artifacts/42-r3-union-injection-pm-rerun.out`.

The frozen held-out adversary was re-run against `f7bd6ad` (scratch output,
the .py itself untouched at 5636 bytes per S2b): 10 rows, 0 diverged,
0 table mismatches, PASS.

Three coverage gaps remain, measured rather than inferred:

1. The `api_key_file unreadable` row is absent from `TestWrapperUnion` --
   and it was one of the FOUR pre-rework divergences. The row exists in
   `TestApiKeyFileGuard` and `TestCredentialErrorTable`, neither of which
   compares wrappers. So one of the four original divergences has no union
   regression guard.
2. The class drives `_probe_api` (private) rather than `probe_backend` (the
   public entry the adversary used), so the dispatch and cache layers are
   outside the union claim.
3. No `type="cli"` row, though the rework's own CLI skip is wrapper-specific.
   Measured directly rather than assumed: cli backends (ordinary name AND
   `session-default`) currently agree across both wrappers, so the gap hides
   no live divergence today.

None of the three blocks R3; all three are worth a follow-up line.

## R2 -- last round's two defects fixed, two new ones introduced

`eb73c22` is clean: "Phase 42" is now "The fast-fail credential guard", the
trailing space is gone, `Signed-off-by` present, author correct.

Two defects arrived with the R3 fix:

1. **`f7bd6ad` carries no `Signed-off-by`.** Measured over `74adbf2..HEAD`:
   10 non-merge commits, 9 with the trailer. The one without is the commit
   that closes R3. The claim's own evidence line reads "8 Signed-off-by"
   across "9 commits" -- the arithmetic discloses the gap while the verdict
   says DONE. Cited evidence should not contradict its own verdict.

2. **`tests/test_fast_fail.py:293` now contains "F2"** -- a finding ID in
   source, the exact defect class R2 exists to remove:

       Inheritance by structure is what F2 disproved once already.

   Forge's own self-check (`grep -rnE '#.*(F[0-9]+:|[Dd]-[0-9])' src/ tests/`)
   cannot see it: the pattern needs a `#` comment and a colon, and this is a
   docstring. Third round in a row that a narrow instrument passed a real
   leak -- the pattern in CLAUDE.md is due a widening, not just this file.

   **PM error, disclosed.** That sentence is nearly verbatim from the
   docstring of my own held-out adversary. The wording, and therefore the
   leak, most likely originates in my artifact. Charge it to me.

   Context, not excuse: F-number leaks are already endemic in this tree
   (`test_outlet_c.py:489`, `test_machine_local.py:371`, `test_f3_legacy_exit.py`,
   `llm_invoke.py:548/564`, plus the R4 set). New code should stop adding to
   the pile; the pile itself stays out of scope.

## Test evidence -- and a pre-registered prediction that held

    .worktrees/p42-rework-verify at f7bd6ad
    2945 passed, 8 skipped, 4 warnings in 483.99s
    EXIT=0, zero FAILED, zero ERROR

Collected 2953 vs 2944 at `0dda79b`: +9 collected, +9 passed, and
`TestWrapperUnion` has exactly 9 tests. Nothing else moved.

**8 skipped, as predicted before the run.** Addendum 1 root-caused the
earlier 9-vs-8 flip to `graph.db` being born 28s after that run started in a
brand-new worktree. Before this run I checked the same worktree's `graph.db`
(born 11:17:39, well before the run) and registered the prediction that the
count would be 8. It is. The diagnosis was correct and is now confirmed from
the other side, with no code change to that test.

Artifact: `cp-artifacts/42-r3-fullsuite-f7bd6ad.out`.

Step 0 on the changed file: `py_compile` OK, `ruff check` all passed,
non-ASCII gate on added lines clean.

## R1 -- unchanged

`dacd344` and `8283d32` are still reachable from exactly one place: the
worktree HEAD `5a7f5ee` (`.worktrees/p42-verify`), my own pre-rework
verification checkout. No branch, tag, remote-tracking ref, or
`refs/original/` reaches them. Removing that worktree closes it.

## Standing

| Item | Verdict |
|---|---|
| R3 | DONE. Injection-proven; three coverage gaps noted, none blocking. |
| R2 | Two old defects fixed, two new: missing Signed-off-by on `f7bd6ad`, "F2" at `test_fast_fail.py:293`. |
| R1 | Branch level clean. Residual holder is my own p42-verify worktree. |
| R4 | Correctly out of scope. |
| E1 | GREEN -- 2945 passed / 8 skipped / 0 failed at `f7bd6ad`. |

Nothing is pushed; `main` is 30 ahead of `origin/main`, 0 behind. Both open
R2 items are one amend and one word.

---

# Addendum 3 -- the close attempt, and what it uncovered (2026-07-27)

The cleanup ran. R1 closed. **The R2 amend did not land** -- it was rejected
by forge's own pre-commit gate, and the rejection uncovered a pre-existing
defect in forge plus a structural hole in the gate.

## R1 -- CLOSED

    git worktree list        -> only /home/houminxi/code/forge [f7bd6ad]
    dacd344 HOLDERS: 0
    8283d32 HOLDERS: 0

All four verification worktrees removed, reflog expired, gc run. No ref, tag,
remote-tracking ref, or worktree HEAD reaches the two `.planning` leak
commits. R1 is done.

## R2 -- BLOCKED, not done

    $ git commit --amend -F /tmp/p42_msg_union.txt
    code-forge: receipt verification failed. Run: code-forge verify

Measured after the fact:

    HEAD                                        f7bd6ad   (unmoved)
    git status                                  M  tests/test_fast_fail.py (staged)
    HEAD:tests/test_fast_fail.py:293            "...what F2 disproved once already."
    :tests/test_fast_fail.py:293                "Sharing a helper is not..."
    non-merge commits 74adbf2..HEAD             10
    with Signed-off-by                          9

The docstring fix is on disk and in the index. Neither R2 item is committed.

**PM error 1, disclosed.** Three of the five checks I wrote for Part B were
vacuously green under this exact failure mode. The `grep` for finding IDs
read the WORKING TREE, which the python step had already rewritten -- it
passes whether or not the commit happened. `git diff f7bd6ad HEAD` was empty
precisely BECAUSE HEAD was still `f7bd6ad`. Only B2 (10 commits / 9 trailers)
could fail, and it did, printing the answer in the transcript. A verification
block that mostly cannot fail is not a verification block.

## Root cause -- a forge defect, and a misleading message

The hook does not report what actually happened. `code-forge verify --quiet
2>/dev/null` hides a crash:

    File "src/code_forge/verify.py", line 61, in _load_receipts
      return [json.loads(f.read_text(...)) for f in sorted(rd.glob(...))]
    json.decoder.JSONDecodeError: Invalid control character at:
      line 4 column 24 (char 52)

Two of the nine receipts carry a raw newline inside a JSON string value:

    .code-forge/receipts/receipt-c2p1.json   (Jun 1 09:52)
    .code-forge/receipts/receipt-c3p1.json   (Jun 1 09:52)

      "skill": "qodo-review<LF>code-review-expert",

`_load_receipts` has no error handling, so ONE malformed receipt bricks every
code commit in the repo, and the operator is told "receipt verification
failed" -- which points at the review, not at a corrupt file. This is a real
forge bug (silent-failure class) and deserves its own reviewed commit:
catch `JSONDecodeError`, name the offending path. Not done here; filed.

## Repairing the receipts does NOT unblock -- measured, not assumed

Scratch copy, both files repaired to valid JSON, `run_verify` re-run against
the current staged diff:

    VerifyResult(passed=False, reason='diff hash mismatch c1p1',
                 checks_run=2, checks_passed=1)

    recorded diff_sha256:  51860eb009ac, c20060b91781
    current staged diff:   aa0780e92229

The receipts attest to different diffs. The gate is behaving exactly as
designed: it wants nine FRESH receipts for THIS diff. Repair fixes the crash
and the misleading message; it does not and should not open the gate.

## The gate has a structural hole -- and that is how the last three commits passed

Hook, lines 17-20:

    STAGED=$(git diff --cached --name-only)
    if [ -z "$STAGED" ]; then exit 0; fi

A message-only amend stages nothing, so it exits before `verify` ever runs.
Confirmed by probe: with the staged change stashed, `git diff --cached` is
empty. Every message-only rewrite this phase -- all three of them -- passed
the gate structurally, not by attestation. That is not an executor failure;
it is what the hook does.

How `f7bd6ad` itself landed is **UNKNOWN**. It staged a real `.py` file, so
it should have hit the same crash. `--no-verify` is the obvious candidate and
I cannot prove it.

**PM error 2, disclosed.** C5 in my own draft (`git reflog expire --expire=now
--all`) was optional, I recommended it, and it destroyed the trail that would
have answered this. Optional destructive steps do not belong in a closing
checklist before the close is verified.

**PM error 3, disclosed.** I classified the change as "trivial text, no forge
review" and then handed over commands that walk straight into the mechanical
gate, having never checked whether the gate would accept my classification.
Forge's own CLAUDE.md says inline judgment is advisory and the external
deterministic gate is the un-fakeable layer. I inverted that.

## Standing

| Item | Verdict |
|---|---|
| R1 | CLOSED -- 0 holders, worktrees removed, verified |
| R2 | BLOCKED -- neither item committed; staged only |
| R3 | DONE (Addendum 2, injection-proven) |
| E1 | GREEN at `f7bd6ad` -- unchanged, nothing committed since |
| NEW | `_load_receipts` crashes on malformed receipt JSON; misleading operator message |
| NEW | Message-only amends bypass the receipt gate entirely |

`main` is 30 ahead of `origin/main`, 0 behind, nothing pushed. Wrap-up
accounting stays blocked on an owner decision about R2.

---

# Addendum 4 -- fixing the receipt crash (2026-07-27)

Owner ruling: fix the forge defect first, then come back to R2. The R2
docstring change stays staged in `main` and is untouched by this work.

Worktree `.worktrees/fix-receipt-crash`, branch `fix/receipt-load-crash`,
based on `f7bd6ad`.

## What the SYSTEM lens found before any edit

`_load_receipts` has two call sites, both in `verify.py`. Sibling readers of
receipt JSON:

    runtime.py:153    smoke-receipt-*.json   ALREADY correct (skip + warn)
    cross_repo.py:409 receipt-*.json         SAME defect, unguarded json.loads
    verify.py:61      receipt-*.json         the crash

`runtime.py` is not a sibling defect -- skip-and-warn is right there, because
smoke receipts are best-effort. `cross_repo.py:409` IS the same defect class
and is reported, not silently fixed (pre-existing, different consumer).

The project already applies both policies deliberately: `ledger.py:94` and
`runtime.py:153` skip malformed input because they are best-effort;
`detect.py:614` states "Present but malformed -> fail loud" and `state.py:214`
does the same, because those reads are authoritative. Verify is a tamper
check, so it belongs in the second group.

## The fix

`_load_receipts` raises `CorruptedReceiptError` naming the file;
`run_verify` catches it and returns `VerifyResult(False, "corrupt receipt
<name>: <reason>")`. New error type added to `errors.py` beside its six
siblings.

Skipping was rejected on purpose: the receipt COUNT and the cycle/pass matrix
are themselves checks, so dropping an unreadable file reports corruption as
absence.

## Bug-injection -- at each fix site separately

The fix is two edits, so each was injected on its own. Equal-looking coverage
collapses otherwise.

    site A -- _load_receipts guard reverted to the bare comprehension
              -> 4 failed, 1 passed  (UnicodeDecodeError / JSONDecodeError escape)
    site B -- run_verify catch removed, _load_receipts guard intact
              -> 4 failed, 1 passed  (CorruptedReceiptError escapes)
    both reverted
              -> 16 passed

`test_intact_receipts_still_pass` is the one that stays green under both
injections -- it guards the opposite direction and is supposed to.

## The INVERSION probe -- the wrong fix was built and caught

Lens 5 named the likely wrong fix: copy `runtime.py`'s skip-and-warn. So it
was actually implemented and run:

    assert "missing receipts" not in r.reason
    E  AssertionError: assert 'missing receipts' not in 'missing receipts: 8/9'

The wrong fix converts a corrupt receipt into a missing one -- a tamper
signal degraded into a count. `test_corrupt_is_not_reported_as_missing`
catches it. A test that only asserted `not passed` would have gone green on
the wrong fix; that is why the assertion is on the reason string.

## Real path, real files, real CLI

Same repo, same nine receipts (two genuinely corrupt since Jun 1):

    shipped code:  json.decoder.JSONDecodeError  (raw traceback, no filename)
    fixed code:    verify: FAIL -- corrupt receipt receipt-c2p1.json:
                   Invalid control character at: line 4 column 24 (char 52)
                   EXIT=1

## Suite

    2950 passed, 8 skipped, 5 warnings in 484.52s   EXIT=0

Baseline at `f7bd6ad` was 2945 passed / 8 skipped. +5 is exactly the five new
tests; nothing else moved. Step 0 clean: `py_compile`, `ruff`, non-ASCII gate
on added lines, banned-vocabulary scan on the diff.

## Reported, not fixed here

- `cross_repo.py:409` -- unguarded `json.loads` on collected receipts, same
  defect class, different consumer.
- `write_attestation` (`verify.py:373`) -- no caller anywhere in the repo.
  Dead code; also calls `_load_receipts` unguarded.
- The hook's message ("receipt verification failed. Run: code-forge verify")
  now leads somewhere useful, but still names the review rather than the
  data. `install_hooks.py` could say "verify failed -- run it for the reason".

## Review -- forge's own pipeline could not run it

Three attempts, all producing `passes=0/3`, `infra=3`, `tokens: 0`. The three
"findings" are invoke failures, not code findings. **No review happened**, and
none of this is treated as a verdict in either direction.

    attempt 1  gate.yaml stream: true      -> non-JSON response body (SSE)
    attempt 2  inline backend, no stream   -> same
    attempt 3  worktree gate.yaml stream: false -> same

The circuit breaker fired after two, so attempt 3 was chosen for how it
DIFFERED: curl showed the endpoint returns clean JSON for an explicit
`"stream": false` and SSE frames when the field is absent, so an explicit
false was the one configuration not yet tried. It failed the same way, which
disproves the hypothesis: forge does not send the flag, and OmniRoute 3.8.48
streams whenever `stream` is omitted -- non-standard, since the OpenAI schema
defaults it to false.

Failing closed (exit 1) is the correct direction and forge did that. But the
review instrument is broken against this backend and should be fixed
separately. Attempt 3 wrote a throwaway `gate.yaml` inside the worktree; the
main config was never touched (`grep` confirms `stream: true` still there).

## Review -- external model instead (impl != reviewer)

deepseek, round 1. Four findings, all rated minor by the reviewer. Their
severity ratings were not taken at face value:

**F1 `write_attestation` uncaught** -- AGREED, and already found independently
before the review. Not fixed: zero callers, guarding dead code is
speculative. Recorded above for whoever revives it.

**F2 `f.name` vs full path** -- REJECTED. The receipts dir is always
`cwd/.code-forge/receipts`, and `cross_repo.py:409` already prefixes copied
receipts with a repo label, so bare names stay distinct. A full path is noise
in a one-line CLI message.

**F3 `RecursionError` escapes the tuple** -- the reviewer called it
theoretical and rated it very low. **Tested rather than accepted, and it is
real:**

    deep = "[" * 100000 + "]" * 100000
    -> *** RecursionError ESCAPES the guard ***

`RecursionError` is not a `ValueError`, so `JSONDecodeError` does not cover
it, and a receipt file shaped like that reproduces the exact unhandled crash
this fix exists to prevent. The severity rating was beside the point: the
fix's own contract had a hole. FIXED -- added to the tuple, with
`MemoryError` deliberately left out as a resource condition rather than a bad
file, matching `cli.py`'s existing `except MemoryError: raise`.

New test `test_deeply_nested_json_reports_the_file`, injection-proven:
removing `RecursionError` from the tuple fails that test alone (`1 failed,
5 passed`), restoring it returns 17 passed.

**F4 redundant prefix** -- ACCEPTED. Now `corrupt receipt: <name>: <why>`.

Round 2 dispatched to a different model (kimi) carrying the disposition of
all four, per the non-convergence protocol. Result pending at time of writing.

## Suite and real path, after the review fix

    2951 passed, 8 skipped, 4 warnings in 523.14s   EXIT=0

2945 at `f7bd6ad` + 6 new tests. Real CLI against the real corrupt receipts:

    verify: FAIL -- corrupt receipt: receipt-c2p1.json:
            Invalid control character at: line 4 column 24 (char 52)

## Addendum 5 -- round 2 found a real hole, and so did my own injection

Round 2 (kimi) came back with one confirmed defect, and running the
injection matrix properly afterwards turned up a second one that neither
reviewer had seen. Both are now fixed. The fix is not converged: three
independent looks have each produced a finding, so declaring it done here
would be a decision against the evidence.

### Integrity of the worktree first

The round-2 reviewer had shell access and said it had restored the tree.
That is a claim, not evidence, and a sub-session rewriting a frozen
artifact mid-verification is a recorded incident, so it was checked before
anything else. `git diff` (worktree against index) empty; staged diff
byte-identical to what I authored; no commits; HEAD still `f7bd6ad`. The
reflog `reset: moving to HEAD` entry is stamped 02:42:21, before the
dispatch, and belongs to my own worktree setup. `verify.py`'s mtime of
03:23:49 does line up with the reviewer editing and restoring the file to
run its own injection, which it disclosed. Clean.

### Q1 -- a plain ValueError escapes the guard. CONFIRMED, FIXED.

Past `sys.get_int_max_str_digits()` (4300 by default), `json.loads` refuses
an integer literal with a bare `ValueError`, which is not a
`JSONDecodeError` and so was not in the round-1 tuple. Reproduced here
independently on Python 3.14.6, end to end through `run_verify`, in two
forms -- a whole-file literal and an oversized `findings_count` field --
both aborting with an uncaught `ValueError` out of `raw_decode`. That is
the precise accident shape this fix exists to prevent, so round 1 had
closed only a subset of it.

Fixed by catching `ValueError` itself:
`(json.JSONDecodeError, OSError, UnicodeDecodeError, RecursionError)` ->
`(ValueError, OSError, RecursionError)`. `JSONDecodeError` and
`UnicodeDecodeError` both derive from `ValueError` (verified), so the tuple
gets shorter while covering strictly more.

Worth recording against myself: I added `RecursionError` in round 1 with a
comment reasoning explicitly about the `ValueError` hierarchy, and still
enumerated subclasses instead of catching the parent. Enumerating variants
where they should have been unified is a named failure mode in the workspace
rules, and knowing the hierarchy at the moment of writing did not prevent it.

### P1 -- OSError in the tuple was covered by nothing. My finding, not a
reviewer's.

Golden Rule 2 says inject at each site separately because equal-looking
coverage collapses when you do. Deleting `OSError` from the tuple broke no
test at all: seven passed. Reachability then confirmed by experiment --
`glob` returns directories too, and `read_text` on one raises
`IsADirectoryError`, an `OSError` and not a `ValueError`. Fixed with
`test_unreadable_entry_reports_the_file`.

### Injection matrix, every element individually load-bearing

    drop ValueError (round-1 tuple) -> 1 failed: test_oversized_int_reports_the_file
    drop RecursionError             -> 1 failed: test_deeply_nested_json_reports_the_file
    drop OSError                    -> 1 failed: test_unreadable_entry_reports_the_file
    except () (guard removed)       -> 7 failed, 1 passed (the healthy-set test)
    restored baseline               -> 8 passed

### Q2, Q3, Q4

Q2 (excluding `MemoryError` is correct) agreed, no change; the reviewer
verified the `cli.py:1833` precedent independently. Q3 asked for the
oversized-int test and an assertion locking the `corrupt receipt:` prefix
itself -- both added, the second because otherwise the round-1 wording fix
could be reverted with the suite still green. Q4 (no out-of-scope content in
the diff) agreed.

### Suite, mechanical, real path

    2953 passed, 8 skipped, 5 warnings in 1035.48s   EXIT=0

2951 previously plus exactly the two tests added this round. The warning
count moving 4 -> 5 is disclosed rather than rounded off: all five are the
same pre-existing `coroutine ... was never awaited` RuntimeWarning from mock
objects, and the tests they are attributed to differ between runs
(`test_no_key_leakage` / `test_skip_cargo_root`), which is the signature of
GC timing deciding when the warning fires. None touch `verify.py`,
`errors.py`, or `test_verify.py`.

ruff clean, `py_compile` clean, no non-ASCII in the diff, no trailing
whitespace. The banned-vocabulary self-check reports three hits repo-wide,
none in the changed files: two are the checker's own regex matching `BSD-2`
in an SPDX line, and one is a pre-existing `D-07` reference in
`tests/eval/corpus/corpus.yaml`. Left alone as a pre-existing issue outside
this change's scope.

Real path, worktree code against the two genuinely corrupt production
receipts, exit codes measured without a pipe masking them:

    code-forge verify           EXIT=1
      verify: FAIL -- corrupt receipt: receipt-c2p1.json:
              Invalid control character at: line 4 column 24 (char 52)
    code-forge verify --quiet   EXIT=1, empty stdout

The fix does not unblock the commit, and should not: the receipts really are
corrupt. What changes is that the operator is handed a filename instead of a
traceback. Both production receipts are unmodified (622 and 695 bytes, mtimes
still 2026-06-01 09:52).

### Round 3 is running, and why it is not ceremony

Round 2 produced a confirmed finding, so the clean-cycle count is zero. The
empirical case is stronger than the rule: round 1 found a hole I missed,
round 2 found one both of us missed, and my own injection found a third that
all three of us missed. Every independent look so far has produced a finding.

Round 3 was dispatched to two models that have not seen this fix (glm,
mimo-pro), carrying the full disposition table, and pointed at the axes
nobody has attacked yet -- both prior rounds worked the same one (what still
escapes the guard). The four axes: is the catch now too BROAD and would it
mislabel a code bug as a corrupt file; is the new directory-based test
portable given the repo claims OS independence and ships a PowerShell E2E
script while CI runs ubuntu only; does the early return's `checks_run=1`
mislead any consumer; and can `CorruptedReceiptError` be swallowed by any of
the 75 broad `except Exception` handlers in `src/`.

My own answers, recorded before the reviews land so agreement cannot be
mistaken for confirmation: axis 1 clean, because the `try` block contains no
forge code at all -- only `read_text` and `json.loads` -- so every
`ValueError` reaching it is a property of the file; the one imprecision is
that a file which vanished between glob and read gets labelled "corrupt"
rather than "gone", which is cosmetic since the errno text travels with it.
Axis 3 clean: `checks_run` and `checks_passed` are written but read nowhere
in the repo, tests included. Axis 4 clean: the only live path that can raise
is caught explicitly inside `run_verify`, and the pre-fix traceback we
observed is itself proof that no top-level handler swallows exceptions on
that path. Axis 2 is the one I cannot settle from here -- Windows raises
`PermissionError` rather than `IsADirectoryError` when opening a directory,
which is still an `OSError`, and the test asserts on the filename in the
reason rather than on the exception type, so it should hold; but that is
general knowledge, not something this host can execute, and it is flagged as
such rather than asserted.

The first glm dispatch died mid-response with a connection error after
emitting only its baseline understanding. That is an infrastructure failure
and is recorded as such -- not a clean round, and not counted in either
direction. It was re-dispatched once.

## Addendum 6 -- the guard rejected the data it was written to protect

Round 3 (glm) cleared all four assigned axes and found F5: a receipt that is
valid JSON but not an object parses fine, then crashes a later check with a
raw `AttributeError`. Patching F5 where it surfaced exposed the real problem.
Six-plus places index straight into receipt fields, and guarding them one at a
time is how the anchors regression happened -- one draft replaced a non-list
`anchors` with `[]` instead of reporting it, turning a corrupt receipt into a
PASS. So the approach was replaced: validate the receipt shape once at load
time in `_validate_receipt_schema`, then let the 7 checks use plain access.

That redesign closed the known crash points, including a sixth nobody had
named: a non-hashable `cycle` or `pass` crashes check 1 when it builds the
`(cycle, pass)` set key. The int check deliberately excludes `bool`, because
`bool` subclasses `int` and `hash(True) == hash(1)`, so a stray `"cycle": true`
would otherwise collide with a legitimate `(1,1)` receipt rather than crash --
a wrong diagnosis, which is worse. Injection confirmed each mechanism is
load-bearing: disabling `_STR_FIELDS`, `_INT_FIELDS`, `_LIST_OF_DICT_FIELDS`,
`_NESTED_SCHEMAS`, or the bool exclusion each reproduced its specific
regression, and each was restored byte-exact.

Then the schema was run against the receipts actually on disk, which had not
been done before. It rejected 11 of the 14 real receipts in this repo. Only 3
passed. Shipping that would have failed every commit in the repository -- the
same outage this fix exists to prevent, arriving from the other direction.

The cause was not a typo. `covered_line_ranges` exists in two real shapes:
`{"file": ..., "start": ..., "end": ...}` and the string `"SKILL.md:1-1400"`.
The schema had been derived from `write_receipts()`, the current writer, which
emits only the first. Older receipts carry the second. A specification taken
from today's writer cannot see yesterday's data.

Tracing the field settled how to fix it. `covered_line_ranges` is read only by
`_covered()`, which is reached only from the legacy branch. `run_verify` takes
`hardened: bool = True`, and `grep -n "hardened" src/code_forge/cli.py` returns
nothing -- the sole production caller at `cli.py:1513` never overrides it and
always passes `diff_text`, so the legacy branch is unreachable in production.
`verify.py:269` already documented the field as "self-reported, not measured --
audit-only. Ignored here." The validator was rejecting real files to assert a
shape on a field nothing on the live path reads. It was removed from the schema
rather than taught both shapes: teaching it would mean writing a parser for a
dead branch.

`_covered()` still raises `TypeError` on the string shape. It does so on `main`
as well, so this is pre-existing and on a production-unreachable path; per
project rule it is left untouched and noted rather than fixed inline, and the
comment there says so instead of claiming a guarantee the schema no longer
makes.

Verification after the correction. All 14 real receipts accepted, 0 rejected,
plus the 4 known-unparseable incident files from the original 2026-06-01
incident, which are untouched and still the right kind of failure.
`tests/test_verify.py` 46 passed. Full project suite 2980 passed, 8 skipped,
exit 0 -- down 3 from 2983, which reconciles exactly: six covered_line_ranges
assertions removed, three real-shape acceptance cases added. Injection at the
new fix site, putting `covered_line_ranges` back into `_LIST_OF_DICT_FIELDS`,
fails the string-shape test with `CorruptedReceiptError`; restored, green.
ruff clean, non-ASCII gate empty, diff confined to the three intended files.

Two of the regression tests in `TestReceiptSchema` guard mistakes made while
building this fix rather than the original defect. That is deliberate. Both
mistakes shared one cause -- a specification derived from the wrong source,
first from imagination about the crash points, then from the writer instead of
the data -- and the durable lesson is recorded in global memory as
`feedback_schema_from_disk_not_writer.md`.

Not covered, stated plainly rather than left to be discovered. The semantic
tampering branches -- duplicate receipt, findings_count mismatch, anchor file
absent from the diff, non-monotonic timestamps -- still have no dedicated
tests. They predate this change and are untouched by it; `git diff HEAD` hunk
boundaries confirm the uncovered lines are outside this diff. `diff-cover` is
not installed on this host, so coverage gaps were cross-referenced by hand
against those hunk boundaries rather than measured by tool.

Round 4 went to LongCat, the first reviewer from a vendor that has seen none of
this. Gemini and mimo-pro were both excluded on evidence, not preference: each
has a recorded run on this project that emitted a preamble and stopped without
producing a review. MiniMax was unavailable for quota reasons. The round-4
prompt carries the disposition of every prior finding including both
self-caught regressions, and directs the reviewer at the axis that has now
drawn blood twice -- whether the schema still rejects anything healthy.

## Addendum 7 -- two clean external rounds, then a gate that would not converge

Round 4 went to LongCat and came back clean on all five axes. It also corrected
me twice. The first correction stands and matters: my brief claimed the diff
"deletes defensive guards from `_covered`", and it does not -- `git diff` shows
only added comment lines there, zero deletions. The guard I believed I had
removed was one I had added earlier in the same uncommitted session, so the net
change against the base is nothing. The reviewer found that by reading the diff
instead of believing the brief. The second correction, that my receipt count of
14 disagreed with its 12, turned out to be a scoping difference rather than an
error: the main repo holds 18 receipt files and the worktree 12, and the
substantive claim of zero rejections holds under either.

Round 5 went to Kimi K3 and was also clean, with two non-blocking observations
and one alarm that turned out to be a false one. It reported that the diff had
vanished from the worktree mid-review, and built a detailed forensic case for
it: yesterday's reflog reset, mtimes matching the main repo to the nanosecond, a
`cp -p`-style overwrite by another session. The fix was never touched. The
status it quoted as the worktree's is byte-identical to what the main repo
prints, and its own strongest piece of evidence -- mtimes matching main exactly
-- was the tell that it had simply been reading main. The same mix-up explains
the 2944 test count it reported against the 2980 measured here.

Its substantive finding is worth keeping. Receipts have two writers, not one:
`write_receipts()` in receipt.py, and a reviewer hand-writing JSON from the
shape documented at `skills/code-forge/SKILL.md:1361` -- the same document that
warns a reviewer can hand-write coverage it never performed. The schema comment
credited only the first, which is the identical single-source attribution that
produced the earlier rejection of real receipts. The comment now names both
writers. Independent measurement across the whole codebase, run here rather than
inherited: 213 receipt files, 207 accepted, 6 unparseable, zero rejected, with
352 dict-shaped and 156 string-shaped `covered_line_ranges` entries confirming
both shapes are common rather than theoretical.

The forge pipeline itself was then run against the diff with deepseek-v4-flash
to produce receipts. It did not fake anything -- 96,403 tokens, three passes,
91 seconds, real code-anchored findings -- and it failed with two confirmed. One
was mine and real: a comment asserting a precondition about "each of these two
fields" and naming `covered_line_ranges`, left stale when that field was removed
from the nested schema. Five external reviewers had read that function without
noticing it. The other was a coverage-inflation weakness at verify.py:326, which
hunk boundaries confirm is outside this diff; it is recorded below rather than
fixed here.

The second pipeline run, after those fixes, returned six findings instead of
three. Its headline finding was fabricated: it reported `test_intact_receipts_
still_pass` as duplicated across two classes at lines 253 and 340, when the two
methods have different names (`..._still_pass` and `..._still_pass_schema`) at
lines 207 and 350. Both the claim and the line numbers were wrong. The remaining
five were re-raises of findings already dispositioned in earlier rounds or
pre-existing code outside the diff. This is the oscillation the project already
documents for this backend beyond a short sweep, so iteration stopped there
rather than continuing to chase it. The two real things it surfaced -- the stale
comment and a weak assertion in the anchors regression test -- were fixed and
kept.

Test count is now 55. The nine added absence cases were proven load-bearing by
injection: giving the string-field check a default value fails exactly the
`diff_sha256` and `timestamp` absence cases and nothing else.

### Carried forward, not fixed here

Coverage inflation, `verify.py:326`, pre-existing and outside this diff.
`_cycle_excerpt_covered` credits the full `start_line`-`end_line` span of an
excerpt without checking that the excerpt's content actually spans it, so a
receipt can claim a wide range, supply a few lines, and inflate the denominator
past the 60% floor. This attacks the anti-fabrication guarantee directly and
deserves its own change. Related and smaller: nothing validates that
`start_line <= end_line`.
