# Phase 42 GREEN -- PM verification result

**Verified by:** PM session, 2026-07-26, against merged `main @ 5a7f5ee`.
**Against:** `.planning/reports/42-green-exit-verifier-heldout-20260725.md`
(frozen 2026-07-25, before execution -- pre-registration held).
**Delivery under test:** `cp-artifacts/42-delivery-briefing.md`.
**Injection script + raw output:** `cp-artifacts/42-green-injection-proof.sh`,
`cp-artifacts/42-green-injection-proof.out` (S2: gate ran from a saved
script, not a pipe).

Every number below was re-derived here. None was taken from the briefing.
Verification ran in a detached worktree `.worktrees/p42-verify` with
`PYTHONPATH=src` forced, so no result comes from the installed package.
One exception, disclosed: the first E1 run executed in the MAIN worktree, not
the verification one -- its traceback paths show it
(`cp-artifacts/42-e1-fullsuite-run1-maintree.out`). E1 was therefore run a
second time inside the verification worktree
(`cp-artifacts/42-e1-fullsuite-run2-verifytree.out`). Both trees sit at
`5a7f5ee` and both runs agree exactly: `1 failed, 2925 passed, 8 skipped`, same
test, 633s and 457s.

A trap found while doing this, recorded because it nearly cost the whole gate.
Both background runs reported **exit code 0** in their completion notification
while pytest actually exited 1. The cause is the command shape, not the
harness: a chain ending in `...; echo "EXIT=$?" >> "$OUT"` reports the exit
status of the `echo`, which always succeeds. Any run wrapped that way is
structurally incapable of signalling failure through its exit code. The
completion signal says a process ended, never that it passed -- and here it
said 0 over a red suite, twice.

## Gate results

| Gate | Verdict | Evidence |
|------|---------|----------|
| E1  full suite green | **FAIL** | 1 failed / 2925 passed / 8 skipped, twice -- see F6 |
| E2  no `axis_claim="review"` in machine.py | PASS | grep = 0 |
| E3  `axis_claim="manual"` kept in cli.py | PASS | grep = 1 |
| E4  `version_sensitive` in ledger.py | PASS | grep = 2 |
| E5  `_check_backend_credentials` def+call | PASS | grep = 2 |
| E6  non-ASCII in phase diff | PASS | 0 over `74adbf2..HEAD` |
| E7  no plan-ref comments introduced | PASS | 0 in added lines |
| E8  xfail removed, not neutered | PASS | 0 `xfail` in test_machine_ledger.py |
| E9  wiring reaches derive_claim_type(f.source) | PASS | read the function, not grepped |
| E10 four injections, distinct signatures | PASS | all four run here |
| E11 version_sensitive survives to disk | PASS | raw JSONL carries the key |
| E12 backward compat on a real old-format line | PASS | hand-built 11-field row |
| E13 F8 branches injected separately | PASS | each breaks only its own tests |
| E14 diff coverage | PASS | claim.py 100%; guard lines covered |
| E15 scope containment | **FAIL** | two `.planning/` files committed |

### E9 -- the wiring, read rather than grepped

    ct = derive_claim_type(f.source)
    ...
    axis_claim=ct.type,
    version_sensitive=ct.version_sensitive,

No literal and no intermediate carrying a constant. `pass_provenance=f.source`
still records the raw source beside the derived claim.

### E10 / E13 -- injection matrix (run by the PM, not reported)

| # | injection | behavioural | Test 13 |
|---|-----------|-------------|---------|
| 1 | `axis_claim=ct.type` -> `"review"` | RED `'review' == 'lint'` | RED |
| 2 | `derive_claim_type("L1")` | RED `'review' == 'lint'` (L0 assert) | GREEN |
| 3 | drop `version_sensitive` write | RED `assert False is True` (L1) | RED |
| 4 | `derive_claim_type("L0")` mirror | RED `'lint' == 'review'` (L1 assert) | GREEN |
| 5 | disable api_key_file branch | api_key_file RED, vertex GREEN | -- |
| 6 | disable vertex branch | api_key_file GREEN, vertex RED | -- |

The asymmetry E10 demanded is present: #2 fails on the L0 assertion and #4 on
the L1 assertion, so they are different mutations rather than one run twice.
Test 13 stays GREEN under both, which is the direct proof that the
source-text guard alone could never have caught argument-hardcoding -- the
hole Item A opened this rework to close. #5 and #6 break only their own
branch, satisfying per-site injection.

Tree verified clean after the last revert.

## Findings

### F6 (HIGH, regression) -- Phase 42 turns the suite red on main

Numbered last because it was found last. It is the most severe finding here,
and it is the thing F3 existed to catch.

    FAILED tests/test_outlet_c_cli.py::TestContractsYamlGuard::
           test_run_contracts_guard_catches_exception
    1 failed, 2925 passed, 8 skipped in 633.41s

Bisected, not guessed. The test fails ALONE at `5a7f5ee` (1.08s) and passes
ALONE at the baseline `74adbf2` (0.23s), so it is a deterministic regression,
not test-ordering pollution.

Mechanism, measured by running the guard against the test's own mock rather
than by reading it:

    m = MagicMock(); m.format = "openai"; m.api_key_env = ""
    bool(m.api_key_file)  -> True
    Path(m.api_key_file)  -> PosixPath('MagicMock/mock.api_key_file/1405...')
    _check_backend_credentials(m)
      -> CliError: API key file not found: <MagicMock ...>

`MagicMock` implements `__fspath__`, so the mock's untouched `api_key_file`
attribute is both truthy and path-like. Phase 42's new `elif
backend.api_key_file:` branch therefore fires and raises at `cli.py:2432`.
The contracts guard the test is actually about sits at `cli.py:2586` -- same
function, later line -- so it never runs, stderr stays empty, and the
assertion at `test_outlet_c_cli.py:371` fails.

Production impact, checked rather than assumed: none found.
`BackendConfig.api_key_file` defaults to `None` (`backend.py:89`) and the XOR
guard at `backend.py:310-319` rejects both-set and neither-set, so a real
backend reaches that branch only when the user genuinely configured
`api_key_file`. The damage is a red suite on `main`, not a broken CLI.

The trap is a known local repeat offender. This one test already carries two
comments about it (`args.backend_url` and friends, then `args.focus`), and the
baseline commit is literally `74adbf2 tests: add args.focus=None to contracts
guard test mock`. Phase 42 added the fourth instance. A mock that answers
every attribute is not a fixture, it is a fixture-shaped hazard, and the
guard extension walked into it.

### F1 (HIGH, process) -- `.planning/` is committed and every git hook is off

`git ls-tree -r HEAD -- .planning` returns two tracked files:
`42-01-SUMMARY.md` and `42-02-SUMMARY.md`, added by `dacd344` and `8283d32`.
`.planning/` is gitignored (`.gitignore:23`) and the project rule is that it
never enters history.

The pre-commit hook contains a working planning-leak guard -- replayed against
the leaked path it matches under ugrep, GNU grep, and `sh`. It did not fire
because `core.hooksPath` is set to `/dev/null` in this repo's local config.
That disables every hook, so the same config also disables the pre-push hook
whose entire job is refusing to push a tip containing `.planning/`.

Blast radius, measured: no remote-tracking ref contains the leak
(`git branch -r --contains 8283d32` is empty; `origin/main` is at `8e18aa0`).
The content is local.

**Correction, on the owner's ground truth (2026-07-26):** `origin` is NOT a
public repository. My first write-up called it public, sourced from the
pre-push hook's own error text (`.git/hooks/pre-push:9`, "origin is PUBLIC"),
not from checking. That is a stale claim living in a hook message, and it is
what made me escalate. It stays recorded here so the next cold session reading
that hook does not repeat the escalation. The hook is local-only and untracked;
its conservative wording is harmless, so it is left alone.

With the repo private, the leak is a hygiene breach rather than a disclosure
event, and the `git filter-repo` public-remote runbook does not apply. The
severity that survives is the disabled-hook config, not the two files.

Because hooks were off for the whole phase, the pre-commit non-ASCII check,
AI-vocab check, forge review, and gate-check did not run on any of the 11
commits either. Those gates were re-run here independently and pass, so the
outcome is fine; the mechanism was absent.

### F2 (MEDIUM) -- two credential validators now disagree, in both directions

Phase 42 added `_check_backend_credentials` (cli.py:2225) while
`_probe_api` (backend.py:600) already validated the same `api_key_file`.
Neither calls the other, and their rules diverge. Measured, not reasoned:

| api_key_file | `_check_backend_credentials` | `_probe_api` |
|--------------|------------------------------|--------------|
| non-empty, mode 0644 | ACCEPT | REJECT (group/world readable) |
| empty, mode 0600 | REJECT (empty) | ACCEPT |

Which one runs is configuration-dependent. `resolve_outlet` returns early at
Step 1 (`FORGE_OUTLET` env) and Step 2 (a gate.yaml `outlet:` field) and only
reaches `reachability_fn()` at Step 4. Forge's own documented setup sets
`outlet` in gate.yaml, so on that configuration `_probe_api` never runs and
the permission check is never enforced -- while the guard that always runs
does not check permissions.

This is not a regression: before Phase 42 the fast-fail path checked neither.
It is a missed unification. The phase's goal was fast-fail on credential
problems, and it shipped a second validator that contradicts the project's
existing one instead of extending it. Golden Rule 4, and the SYSTEM lens
question ("what else validates this?") that the plan never asked.

### F3 (HIGH, process) -- E1 was reported from a subset, and the subset hid F6

Filed as LOW when it was only a rule violation. F6 promotes it: the subset is
what let a red suite be reported as green.

The briefing's E1 evidence is `33 passed`. The suite is 2934 tests; 33 is
exactly the three Phase-42 files. The gate as frozen is the whole suite, and
the risk it exists to cover is precisely the rest of the suite -- which is
where the one failure lives. The three Phase-42 files are all green; every
test the executor ran did pass. The claim was not false about what it
measured, it was false about what it covered.

### F4 (LOW) -- `tests/test_machine_ledger.py` still ends without a newline

Round 1's delta note claimed this was fixed; it was not, and it still is not
(`tail -c1 | od -c` shows `e`). Consequence beyond style: `wc -l` reports 303
for a 304-line file, and the final assertion is the unterminated line. It
nearly cost this pass a false finding (see below).

### F5 (NIT) -- annotation dropped, with no reason available

`def _check_backend_credentials(backend) -> None` -- the plan specified
`backend: BackendConfig`.

I first wrote that this was "probably to dodge an import cycle." That guess is
disproved: cli.py:9 carries `from __future__ import annotations`, so
annotations are lazy strings at runtime, and `BackendConfig` is already
imported in the `TYPE_CHECKING` block at cli.py:26. The machinery was in
place and the annotation is a one-word change with no runtime effect. There
was no cycle to dodge, so the plan's signature should simply have been used.

## Disproved -- raised here and then killed by evidence

- **"A permission check was lost."** No. `_probe_api` is untouched; the gap
  is that it is bypassed on early-return outlet paths, which was already true
  before this phase. F2 is downgraded to a unification defect accordingly.
- **Forge review finding #4 (elif branches mutually exclusive) was dismissed
  by the executor citing backend.py XOR enforcement.** Verified rather than
  trusted: `backend.py:310-314` rejects both-set and `:315-319` rejects
  neither-set. The dismissal is correct and the `elif` is safe.
- **"The merge at `88c1af4` may have dropped a test."** No. The file went
  10 -> 11 tests and the only delta is the added behavioural test.
- **"The skipif hides a failure."** No. It is scoped to one test and to
  `uid == 0`; this run is uid 1000, and all 9 fast-fail tests execute.

## PM errors this pass -- disclosed under S1

1. **Asserted main was unpushable** because the pre-push hook file exists.
   Wrong: hooks are disabled. Inference from an artifact's presence rather
   than from its effect -- the exact failure Golden Rule 6 names. Corrected
   by reading `core.hooksPath`.
2. **My own E7 gate has a false positive.** `[Dd]-[0-9]` matches
   `BSD-2-Clause`, so the raw gate reported 3 hits on untouched files. Scoping
   it to added lines gives the real answer.
3. **Nearly claimed the L1 `version_sensitive` assertion was missing.** The
   Read window ended at line 303 because of my own `limit`, and the file's
   last line is 304 with no trailing newline. Third instance of the
   truncated-view error class this engagement -- caught before assertion this
   time, by re-reading unbounded rather than trusting the window.
4. **My E1 run was not in the worktree this report claims.** The first
   full-suite run executed in the main worktree; the header said all
   verification ran in `.worktrees/p42-verify`. Both trees are at `5a7f5ee`,
   so no result changes, but the header was wrong as written and is corrected
   above. Found by reading the traceback paths in my own saved output -- the
   same artifact that produced the E1 number.
5. **I nearly reported E1 from a completion signal instead of an output.**
   The background run's notification said the task finished; the report's
   E1 row said "see note". Had that stood, this pass would have shipped the
   identical defect it charges the executor with in F3. The number only
   exists because the output file was read.

## Dispositions (PM ruling, 2026-07-26)

**F6 -- fix, and it blocks the phase.** `main` is red; nothing else about
Phase 42 matters until it is green. The fix is one line in the test mock
(`fake_backend.api_key_file = None`), matching what the baseline commit did
for `args.focus`. It is a test-only change and classifies as chore.

Do not "fix" it by softening `_check_backend_credentials`. The guard behaved
correctly: it was handed something that claims to be a path and is not one.
Weakening a credential check to accommodate an over-permissive mock trades a
real guard for a test convenience.

Two things the fix must carry, because a one-line change is exactly where
verification gets skipped. First, the whole suite -- not the file, not the
class -- must be run and its collected count quoted; F3 is the standing rule
now. Second, the test's own recorded injection must still hold afterwards:
remove the contracts guard, watch this test FAIL, revert, watch it PASS. A
green test proves the CliError is gone; only the injection proves the test is
still testing the guard it is named after.

Worth carrying past this phase: this is the fourth `MagicMock`-truthiness
casualty in a single test. The durable fix is a real `BackendConfig` in that
fixture instead of a mock that answers every attribute, but that is a
separate change and does not belong in a red-suite hotfix.

**F1 -- remediate, routed to the executor.** Two parts, and the second is the
load-bearing one: drop the two files from history, AND unset
`core.hooksPath`. Dropping the files while the config stays `/dev/null`
re-arms nothing -- it just resets the trap for the next phase. After the
config is restored, the guard gets a known-answer run: stage a `.planning/`
file, confirm the hook blocks it, unstage. A restored guard that was never
fired is an assumption, not a fix.

**F2 -- FIX, inside this rework. Owner ruling, overriding mine.**

My original ruling was DEFER: the change lands in `backend.py`, outside this
phase's declared seven files, and E15 was already failing on scope, so
widening it would compound that. The owner overruled it -- "F2 I require
fixed, not carried further" -- and the override stands. Recorded as a
reversal rather than edited away, because the reasoning that lost is still
the reasoning that will be reused next time a scope question comes up, and
because the widening is now deliberate: E15 stays failed on scope, but the
`backend.py` touch is reclassified from accident to approved extension.

The shape, unchanged from what the deferral had recorded: one shared rule
(`credential_error(backend, env) -> str | None`) with `_probe_api` wrapping it
into `ProbeResult` and `_check_backend_credentials` raising `CliError`. Two
contracts, one rule set, union semantics (exists + readable + non-empty +
mode 0600).

The one question I refused to decide as a review call -- warn-first or
hard-fail on 0600, since it is user-visible -- went to the owner and came back
**hard-fail, both paths**. So enforcing 0600 on the fast-fail path will start
failing anyone whose key file is 0644 on a gate.yaml-`outlet` config. That is
accepted, not overlooked: `_probe_api` already hard-fails on a group- or
world-readable key file today, so this applies an existing project
requirement uniformly rather than inventing a new one.

Dispatched as W4 of `.planning/phases/42-cli-key-claim-type/cp-artifacts/
42-rework-order-20260726.md`.

**F3 -- upheld, and no longer hypothetical.** When I first ruled on this, the
suite had not finished and I wrote that whether the subset hid anything was
unknown. It hid F6. A gate that says "whole suite" is not satisfied by a
subset, and the failure was in exactly the part the subset omitted.

The defect is the briefing *format*, not this one executor: `33 passed` and
`2934 passed` are indistinguishable in it, so the format cannot express the
difference between a passing gate and an unrun one. Future deliveries quote
the command and the collected count, not the pass count alone. A pass count
with no denominator is not evidence.

**F4 + F5 -- fix, in one chore commit, NOT folded into the history rewrite.**
Both are trivial (one byte; one word). Keeping them out of the rewrite is
deliberate: the rewrite must stay mechanically checkable as "removed exactly
two files and changed nothing else." Mixing content edits into it destroys
that property. Classification is chore -- no review gate.

## Verdict

**Phase 42 does not close.** Two gates fail.

The engineering it set out to do is sound, and the round-2 rework is proven to
work: the mirror mutation that survived round 1 is now caught, by the L1
assertion, under a real injection I ran. E2-E14 pass.

E1 fails: `main` is red, one test, deterministic, caused by this phase. E15
fails on a contained hygiene breach whose real content is a disabled hook
config, not two committed files.

The two failures share a cause worth naming. Every gate that was checked by
this phase's own runner passed; both gates that failed are ones nothing
mechanical was watching -- the suite outside the three touched files, and the
hooks that were switched off before the first commit. Phase 42 was reviewed
four times by external models and self-verified against all fifteen exit
criteria, and neither failure is subtle. They were simply outside where anyone
was looking.

To close: F6 fixed and the whole suite green with its collected count quoted,
F1 remediated in both halves (files dropped AND `core.hooksPath` restored, with
the guard fired once to prove it), F2 fixed (owner override of my deferral --
the two validators unified behind one rule, hard-fail 0600), F4/F5 in a chore
commit. All five are dispatched as W1-W5 of
`.planning/phases/42-cli-key-claim-type/cp-artifacts/42-rework-order-20260726.md`.
Then, and not before, the wrap-up accounting.
