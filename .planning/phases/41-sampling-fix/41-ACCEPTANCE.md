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
