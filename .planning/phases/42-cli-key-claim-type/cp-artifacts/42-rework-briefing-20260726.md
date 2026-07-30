# Phase 42 rework briefing

**Executor:** mimo, 2026-07-26
**Against:** `cp-artifacts/42-rework-order-20260726.md`
**Baseline:** 5a7f5ee (old tip), HEAD: 0dda79b (new tip)

---

## Per-item status

| Item | Status | Commit(s) |
|------|--------|-----------|
| W1 | DONE | (git config, no commit) |
| W2 | DONE | 67f47b2 (flattened history, 8 commits) |
| W3 | DONE | d41508d + 0dda79b |
| W4 | DONE | 52eeff0 |
| W5 | DONE | 52eeff0 (trailing newline + annotation) |

---

## W1 — hook literal refusal output

```
$ touch .planning/_hooktest.md && git add -f .planning/_hooktest.md
$ .git/hooks/pre-commit
code-forge: BLOCKED: staged paths must never enter history:
  .planning/_hooktest.md
EXIT=1
```

`core.hooksPath` was `/dev/null`, now unset (verified: `git config --local core.hooksPath` returns nothing).

---

## W2 — history rewrite evidence

OLD_TIP: `5a7f5eead4e41007f6f469ec30d9b37d50240865`
NEW_TIP: `67f47b2`

```
$ git ls-tree -r HEAD --name-only -- .planning
(empty)

$ git diff 5a7f5ee HEAD --stat
 .../42-01-SUMMARY.md  | 86 --------------------
 .../42-02-SUMMARY.md  | 91 ----------------------
 2 files changed, 177 deletions(-)
```

Strategy: cherry-picked non-merge commits onto 49a458d, skipping dacd344 and
8283d32. Merge topology flattened to linear. Trailing newline difference in
test_machine_ledger.py amended out so tree matches exactly (minus the two
.planning files).

---

## W3 — injection proof

### Guard removed (contracts guard try/except deleted):

```
FAILED tests/test_outlet_c_cli.py::TestContractsYamlGuard::test_run_contracts_guard_catches_exception
E   AssertionError: guard did not log to stderr
E   assert 'contracts.yaml load failed' in ''
```

### Reverted (try/except restored):

```
tests/test_outlet_c_cli.py .                                             [100%]
1 passed
```

Mock fix: `fake_backend.api_key_file = None` + `fake_backend.credentials_path = None`.
Also: `api_key_env = ""` changed to `api_key_env = "FAKE_KEY_FOR_TEST"` because
W4's credential_error treats empty string as "not configured" (the old inline
guard treated it as "skip").

---

## W4 — unified validator

`credential_error(backend, env) -> str | None` in `backend.py` (line ~600).
Both wrappers delegate to it:

- `_check_backend_credentials` (cli.py:2225) -> raises CliError
- `_probe_api` (backend.py:600) -> returns ProbeResult(ok=False, error=...)

Behavior change: fast-fail path now enforces 0600 on api_key_file (matching
_probe_api's existing requirement). CLI backends (type=="cli") skip the check
entirely (they authenticate via claude auth, not API keys).

All 9 behavior-table rows tested by TestCredentialErrorTable in test_fast_fail.py.

---

## W5 — trivial fixes

1. `tests/test_machine_ledger.py`: trailing newline added (last byte was `e`, now `\n`)
2. `def _check_backend_credentials(backend) -> None`: annotation restored to
   `backend: BackendConfig` (cli.py has `from __future__ import annotations`
   and BackendConfig under TYPE_CHECKING, so no import cycle)

---

## Test evidence

```
$ PYTHONPATH=src python -m pytest tests/ -q --timeout=300
2936 passed, 8 skipped, 4 warnings in 357.70s (0:05:57)
```

**0 failed.** COLLECTED: 2944 (2936 passed + 8 skipped).

---

## Behaviours changed this order did not ask for

1. **CLI backends skip credential check.** Added `if backend.type == "cli": return`
   in `_check_backend_credentials`. Reason: `credential_error` returns an error
   when neither api_key_env nor api_key_file is configured, which is correct for
   API backends but wrong for CLI backends (they use claude auth). Without this
   guard, 16 integration tests failed.

2. **Error message format changed.** `_check_backend_credentials` now surfaces
   `credential_error`'s messages directly instead of its own wording. Example:
   old `"API key env var 'X' is not set"` → new `"X not set. Export the API key
   for backend 'b'."`. The `test_missing_env_var_raises` match was updated from
   `"is not set"` to `"not set"` accordingly.

Both are side effects of the unification; neither changes observable CLI behavior
for real users (the error content is equivalent, just worded differently).
