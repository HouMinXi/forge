# Phase 42 rework order

**To:** executor (mimo)
**From:** PM session, 2026-07-26
**Repo:** `/home/houminxi/code/forge`, branch `main`, HEAD `5a7f5ee`
**Source of findings:** `.planning/reports/42-green-verification-result-20260726.md`

Phase 42 does not close. Two exit gates fail: E1 (the suite is red) and E15
(scope). This order covers five work items. Nothing else in Phase 42 is
reopened.

---

## Ground truth (measured by the PM on 2026-07-26, not quoted from a report)

Every figure below was re-derived immediately before this order was written.
If you believe any of it is wrong, re-measure and say so -- do not silently
work around it.

    HEAD                5a7f5ee     baseline  74adbf2
    origin/main         8e18aa0     (local main is 11 commits ahead, unpushed)

    full suite at HEAD, run twice, both agree:
      1 failed, 2925 passed, 8 skipped        (633s main tree, 457s worktree)
      FAILED tests/test_outlet_c_cli.py::TestContractsYamlGuard::
             test_run_contracts_guard_catches_exception

    git config --local  core.hooksPath   =  /dev/null      <-- all hooks off
    git config --global core.hooksPath   =  unset

    tracked .planning files in HEAD (must be zero):
      .planning/phases/42-cli-key-claim-type/42-01-SUMMARY.md  <- dacd344
      .planning/phases/42-cli-key-claim-type/42-02-SUMMARY.md  <- 8283d32
      both commits add ONLY that one file each -- nothing else
      no remote ref contains them (`git branch -r --contains 8283d32` empty)

    tests/test_machine_ledger.py last byte = 'e'   (no trailing newline)
    src/code_forge/cli.py:9   from __future__ import annotations
    src/code_forge/cli.py:26  BackendConfig already imported under TYPE_CHECKING
    src/code_forge/backend.py:89   api_key_file: Optional[str] = None
    src/code_forge/backend.py:310-319  XOR guard: rejects both-set and neither-set

    probe_backend call sites (F2 blast radius):
      src/code_forge/doctor.py:151
      src/code_forge/outlet_resolver.py:235
      src/code_forge/cli.py:2379
      src/code_forge/cli.py:3358

---

## Scope fence

**You may touch:** `src/code_forge/cli.py`, `src/code_forge/backend.py`,
`tests/test_outlet_c_cli.py`, `tests/test_fast_fail.py`,
`tests/test_machine_ledger.py`, plus any test file the W4 refactor forces you
to update, plus local git config and local git history.

**You may not touch:** the claim_type work (`src/code_forge/claim.py`,
`src/code_forge/machine.py`, `src/code_forge/ledger.py`,
`tests/test_claim_type.py`). It was verified and passes; leave it alone.

**Scope note on W4:** it lands in `backend.py`, outside Phase 42's originally
declared file set. That widening is approved deliberately by the owner, who
overruled the PM's deferral. Record it as an approved extension, not as an
accident.

---

## W1 -- restore the git hooks, and prove they fire

`core.hooksPath` is `/dev/null` in this repo's local config, so every hook has
been off. That is why `.planning/` reached history, and it also means the
non-ASCII check, the AI-vocab check and the gate check did not run on any of
Phase 42's eleven commits.

Do this FIRST, before any new commit, so everything after it is gated.

1. Unset it: `git config --local --unset core.hooksPath`.
2. Confirm `.git/hooks/pre-commit` and `.git/hooks/pre-push` exist and are
   executable (`chmod 755` if not).
3. Known-answer proof, required: `touch .planning/_hooktest.md`,
   `git add -f .planning/_hooktest.md`, attempt a commit, confirm the hook
   BLOCKS it, then `git reset` and delete the file.

**Done-condition:** paste the hook's actual refusal output. A restored guard
that was never fired is an assumption, not a fix. If the hook does not block,
stop and report -- do not proceed to W2 with a guard you have not seen work.

---

## W2 -- drop the two `.planning` files from history

Both offending commits add exactly one `.planning` file and nothing else, so
this is the easy case: drop the two commits, do not edit them. The leak is
local only -- no remote ref contains it, and `main` is unpushed relative to
`origin/main` at `8e18aa0`. No force-push to a remote is involved.

The repo is PRIVATE. Ignore the `origin is PUBLIC` wording in
`.git/hooks/pre-push`; it is a stale message in a local, untracked hook. This
is a hygiene fix, not a disclosure incident, and the `git filter-repo`
public-remote runbook does not apply.

**Done-condition, mechanically checkable:**

    git ls-tree -r <new-main> --name-only -- .planning      # must be empty
    git diff <old-tip> <new-tip>                            # must be EMPTY

The second one is the point: the rewrite must change the tree in no way
whatsoever. That property is why W5's content edits must NOT be folded in
here. Record the old tip SHA before you start so this diff is possible.

---

## W3 -- fix the red suite

`tests/test_outlet_c_cli.py::TestContractsYamlGuard::test_run_contracts_guard_catches_exception`
fails at HEAD and passes at baseline `74adbf2`. Deterministic, not ordering.

Mechanism, measured:

    m = MagicMock(); m.format = "openai"; m.api_key_env = ""
    bool(m.api_key_file)  -> True
    Path(m.api_key_file)  -> PosixPath('MagicMock/mock.api_key_file/1405...')
    _check_backend_credentials(m)
      -> CliError: API key file not found: <MagicMock ...>

`MagicMock` implements `__fspath__`, so the test's untouched `api_key_file`
attribute is both truthy and path-like. The new `elif backend.api_key_file:`
fires at `cli.py:2432` and aborts `_run` before the contracts guard at
`cli.py:2586` -- same function, later line -- so stderr stays empty.

**Fix:** set `fake_backend.api_key_file = None` in that test's mock. This is
the fourth instance of the same MagicMock-truthiness trap in this one test;
the two existing comments about it and the baseline commit
`74adbf2 tests: add args.focus=None to contracts guard test mock` are the
first three.

**Do NOT** fix this by softening `_check_backend_credentials`. The guard
behaved correctly -- it was handed something claiming to be a path that was
not one. Weakening a credential check to accommodate an over-permissive mock
trades a real guard for a test convenience.

**Done-condition:** the test passes, AND its own recorded injection still
holds: remove the contracts guard, watch this test FAIL, revert, watch it
PASS. A green test only proves the CliError is gone; only the injection proves
the test still tests the guard it is named after.

---

## W4 -- unify the two credential validators

Phase 42 added `_check_backend_credentials` (cli.py:2225) while `_probe_api`
(backend.py:600) already validated the same `api_key_file`. Neither calls the
other and their rules contradict in both directions. Measured:

    api_key_file             _check_backend_credentials   _probe_api
    non-empty, mode 0644     ACCEPT                       REJECT
    empty, mode 0600         REJECT (empty)               ACCEPT

Which one runs is configuration-dependent. `resolve_outlet` returns early at
Step 1 (`FORGE_OUTLET`) and Step 2 (a gate.yaml `outlet:` field) and only
reaches `reachability_fn()` at Step 4, so on forge's own documented setup
`_probe_api` never runs and the permission check is never enforced -- while
the guard that always runs does not check permissions.

**Target:** one shared rule, two wrappers.

    credential_error(backend, env) -> str | None

    _check_backend_credentials -> raise CliError(msg) when not None
    _probe_api                 -> ProbeResult(ok=False, error=msg)

**Required behaviour, decided by the owner -- union semantics, hard-fail:**

    api_key_file missing                  -> error, text contains "not found"
    api_key_file unreadable (OSError)     -> error, text contains "unreadable"
    api_key_file empty                    -> error, text contains "empty"
    api_key_file mode & 0o077             -> error, text contains "chmod 600"
    api_key_file present, 0600, non-empty -> None
    api_key_env set, value present in env -> None
    api_key_env set, value absent         -> error, text contains "is not set"
    vertex credentials_path set, is_file  -> None
    vertex credentials_path set, missing  -> error, text contains "not found"

This is a deliberate behaviour change and the owner accepted it: enforcing
0600 on the fast-fail path will start failing users whose key file is 0644 and
who run on a gate.yaml-`outlet` config. It is not a new requirement -- 
`_probe_api` already hard-fails on group/world-readable key files today -- it
is the project's existing requirement applied uniformly. The error must name
the file and say `chmod 600`.

**Trap, read this before refactoring.** `_probe_api`'s vertex branch has an
ADC fallback (`GOOGLE_APPLICATION_CREDENTIALS`, then
`~/.config/gcloud/application_default_credentials.json`) that runs when
`credentials_path` is NOT set. The fast-fail path deliberately does NOT do
this -- `tests/test_fast_fail.py::test_no_credentials_path_deferred` asserts
it does not raise. Keep the ADC fallback probe-only. The shared rule covers
explicitly-configured credentials only. Unify too much and you break that test.

**Second trap.** Several existing tests match on error message text. If you
reword a message, the suite is the arbiter, not your judgement about which
wording reads better. Run the whole suite and fix what you broke, or keep the
substrings above.

**Done-condition:** the behaviour table above holds for BOTH wrappers, proven
by tests you add; every one of the four `probe_backend` call sites still
works; whole suite green.

---

## W5 -- two trivial fixes, one chore commit

Keep these OUT of W2. The rewrite must stay mechanically checkable as
"changed nothing at all"; mixing content edits into it destroys that.

1. `tests/test_machine_ledger.py` has no trailing newline (last byte is `e`).
   Add one. Consequence beyond style: `wc -l` reports 303 for a 304-line file,
   and the final assertion is the unterminated line.
2. `def _check_backend_credentials(backend) -> None` drops the type annotation
   the plan specified. Restore `backend: BackendConfig`. There is no import
   cycle to avoid: `cli.py:9` carries `from __future__ import annotations` and
   `cli.py:26` already imports `BackendConfig` under `TYPE_CHECKING`.

Classification: chore. No review gate.

---

## Output contract

Report back with a briefing containing exactly this, and nothing invented:

1. **Per item W1-W5:** DONE / PARTIAL / NOT DONE, with the commit SHA.
2. **W1:** the hook's literal refusal output from the known-answer run.
3. **W2:** old tip SHA, new tip SHA, and the literal output of
   `git ls-tree -r HEAD --name-only -- .planning` and
   `git diff <old> <new> --stat`.
4. **W3:** the injection result in both directions (guard removed -> FAIL,
   reverted -> PASS), pasted, not summarised.
5. **Test evidence -- this is the part the last briefing got wrong.** Quote
   the COMMAND and the COLLECTED count, not the pass count alone:

       PYTHONPATH=src python -m pytest tests/ -q --timeout=300

   `33 passed` and `2934 passed` are indistinguishable in a bare pass count.
   The gate is the whole suite. A subset run reported as the gate is what let
   a red suite be delivered as green last time.
6. **Any behaviour you changed that this order did not ask for**, with why.

**Do not** write the exit-verification verdict yourself. The PM re-derives
every number independently; a self-certified PASS is not evidence and will be
re-run regardless.

---

## Honest failure is pre-authorised

If an item cannot be done, or the fix turns out larger than described here, or
the ground truth above proves wrong: say so plainly and stop. A truthful
"W4 not done, here is what I found instead" is a good outcome and costs you
nothing. A plausible-sounding completion claim that does not survive
re-derivation is the one failure mode that matters, and it will be caught,
because every number in your briefing gets re-measured.

---

## Traps that have already bitten this phase

1. **`; echo "EXIT=$?"` masks the exit code.** A chain ending in an `echo`
   exits 0 no matter what ran before it, and a background task's completion
   notification then reports 0 over a red suite. This happened twice here.
   Use `rc=$?; ...; exit "$rc"`, and read the output rather than the status.
2. **A completion signal says a process ended, never that it passed.**
3. **`MagicMock` answers every attribute**, truthy and `__fspath__`-capable.
   Four tests in this repo have now been bitten by it.
4. **The editable install shadows `src/`.** Force `PYTHONPATH=src` on every
   pytest invocation, or you may be testing the installed package.
5. **`.planning/` is gitignored and must never enter history.** With W1 done,
   the hook enforces this again -- do not use `git add -f` to get around it.
