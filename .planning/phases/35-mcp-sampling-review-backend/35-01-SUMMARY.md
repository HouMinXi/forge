# Phase 35: MCP Sampling Review Backend -- Implementation Summary

## What Phase 35 Delivers

MCP sampling as a new outlet type for forge. When forge runs inside an MCP
client (Copilot, Claude Max), it calls `ServerSession.create_message()` to use
the client's model for review -- zero API key configuration needed.

Implementation (V4 plan, 9 rounds of external multi-model review):

- `invoke_sampling()` in llm_invoke.py -- async bridge to MCP sampling API
- `build_sampling_l1_provider()` in factories.py -- L1 provider closure with
  async-to-sync bridge via `run_coroutine_threadsafe`
- `_dispatch_sampling()` shared helper in mcp_server.py -- ForgeLock in worker
  thread, StateMachine run, Verdict-to-CallToolResult conversion
- `_build_review_context()` / `_make_inprocess_result()` helpers in mcp_server.py
- CLI guard at cli.py:1502 -- rejects `outlet=sampling` outside MCP context
- `"sampling"` added to `VALID_OUTLET_STRINGS` in outlet_resolver.py

## Finishing-Pass Fixes (4 defects from human review)

### Defect 1 (HIGH) -- CLI-guard test broken + tested nothing

**Problem**: `test_cli_guard_sampling_raises` passed a raw list to `_run()`
(which expects an argparse Namespace) and asserted `rc == EXIT_CLI_ERROR`
(but `_run` raises `CliError`, never returns an exit code). The test errored
out on `AttributeError` before reaching the guard.

**Fix**: Rewrote as two tests (`_via_env` and `_via_flag`) using
`_build_parser().parse_args()` for correct Namespace + `pytest.raises(CliError)`
matching the codebase convention.

**Bug-injection proof** (guard commented out -> both FAIL; restored -> both PASS):

```
# Guard removed:
FAILED test_cli_guard_sampling_raises_via_env - AssertionError: Regex pattern did not match.
  Expected: 'only available within the MCP server context'
  Actual: 'No toolchain detected...'
FAILED test_cli_guard_sampling_raises_via_flag - same
2 failed

# Guard restored:
2 passed
```

### Defect 2 (MEDIUM) -- argparse --outlet choices missing "sampling"

**Problem**: cli.py:281 `choices=["subprocess", "cli", "inline", "subagent"]`
did not include `"sampling"`. Running `code-forge review --outlet sampling`
would be rejected by argparse with a generic "invalid choice" error before
reaching the guard.

**Fix**: Added `"sampling"` to the choices list. The `_via_flag` test from
Defect 1 covers this path.

### Defect 3 (LOW) -- async-mock "never awaited" RuntimeWarning

**Problem**: `patch("code_forge.llm_invoke.invoke_sampling")` auto-creates an
`AsyncMock` (Python 3.8+ behavior for async functions). The mock's coroutine
is passed to the also-patched `run_coroutine_threadsafe` which ignores it,
leaving the coroutine never-awaited.

**Fix**: Changed both test sites to `patch(..., new_callable=MagicMock)` to
force a sync mock -- the coroutine is unused when `run_coroutine_threadsafe`
is patched anyway.

**Verification**:
```
$ pytest tests/test_factories.py -k sampling -W error::RuntimeWarning -q
3 passed, 34 deselected in 0.08s
```

### Non-ASCII fix (comment-only)

Two em dashes in mcp_server.py ponytail comments (lines 331-332) replaced with
ASCII double dashes. Not implementation logic.

## Verification Evidence

### 1. All sampling tests (15 passed)

```
$ pytest tests/test_llm_invoke.py tests/test_outlet_resolver.py \
    tests/test_mcp_server.py tests/test_factories.py -k sampling -q
15 passed, 279 deselected in 0.44s
```

### 2. Full regression (294 passed, 0 failed)

```
$ pytest tests/test_llm_invoke.py tests/test_outlet_resolver.py \
    tests/test_mcp_server.py tests/test_factories.py -q
294 passed in 0.70s
```

### 3. RuntimeWarning gate (clean)

```
$ pytest tests/test_factories.py -k sampling -W error::RuntimeWarning -q
3 passed, 34 deselected in 0.08s
```

### 4. Import smokes

```
OK1  (invoke_sampling, LLMResult.is_truncated)
OK2  ('sampling' in VALID_OUTLET_STRINGS)
OK3  (build_l1_provider has no mcp_session param)
OK4  (StateMachine has no mcp_session param)
OK5  (mcp_server imports cleanly)
```

### 5. Non-ASCII gate

```
$ git diff HEAD --diff-filter=AM -U0 | grep '^+' | grep -P '[^\x00-\x7F]'
(no output -- clean)
```

### 6. Diff stat

```
src/code_forge/cli.py             |   6 +-
src/code_forge/factories.py       | 205 +++
src/code_forge/llm_invoke.py      |  62 ++
src/code_forge/mcp_server.py      | 206 +++-
src/code_forge/outlet_resolver.py |   1 +
tests/test_factories.py           |  74 ++
tests/test_llm_invoke.py          |  93 ++
tests/test_mcp_server.py          |  76 ++
tests/test_outlet_resolver.py     |  44 ++
9 files changed, 765 insertions(+), 2 deletions(-)
```

## Deviations from Plan

- Test for CLI guard uses `_build_parser().parse_args()` instead of raw list
  passed to `_run()` -- the plan's pseudocode passed a raw list, but `_run`
  expects an argparse Namespace. This is a plan-level bug in the test
  pseudocode, not a deviation from intent.
- Added `_via_flag` test (Defect 2) in addition to the `_via_env` test -- the
  plan only showed the env-var path but the argparse choices fix requires
  testing the flag path too.

## Constraints Honored

- [x] No git state changes (no commit, no stash, no branch)
- [x] No implementation logic modified (only tests, argparse choices, comments)
- [x] No non-ASCII characters
- [x] No worktree created
- [x] No hooks bypassed
