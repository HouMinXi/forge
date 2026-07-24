SUMMARY: B=1 H=0 M=0 L=0

---

## **B1 (Blocker): `_evict_stale` test design conflates `max_lifetime_s` with `_JOB_TTL_SECONDS`**

**Plan section:** REPLAN (i), within "(e) Tests" block — `_evict_stale` per-consumer leak test

**Real code:** `mcp_jobs.py:334-359` (evict_stale), `mcp_jobs.py:74` (`_JOB_TTL_SECONDS = 3600.0`), `mcp_jobs.py:85-92` (`max_lifetime_s` docstring)

**Description:**

The plan instructs (REPLAN block (i)):
> "drive `_evict_stale` via TTL expiry (small `max_lifetime_s`) on a job carrying a focus tmpfile, then assert that tmpfile is unlinked"

This conflates two independent timeout mechanisms:

1. **`max_lifetime_s`** (`mcp_jobs.py:85`) — stored in the job dict at line 105, used by `_wait_for_job` at line 227-230 as an `asyncio.wait_for` timeout on the `comm_task`. It controls how long the subprocess is allowed to run before being killed (SIGTERM → SIGKILL). It has **zero effect** on `_evict_stale`.

2. **`_JOB_TTL_SECONDS = 3600.0`** (`mcp_jobs.py:74`) — used by `_evict_stale` at line 345 (`age <= _JOB_TTL_SECONDS`). This is the TTL for discarding **terminal** entries from `_jobs`. Only entries past 3600 seconds old and in `completed`/`failed` status are removed, triggering tempfile cleanup at lines 353-358.

A small `max_lifetime_s` (e.g. 1s) would cause the job to be killed after 1 second via `asyncio.wait_for`, transitioning to `status="failed"`. But the entry sits in `_jobs` for another 3599 seconds before `_evict_stale` touches it. The test would **never reach** the tempfile-unlink code inside `_evict_stale` — it would pass vacuously (the focus tmpfile is never asserted to be unlinked in a reasonable timeframe).

The test also cannot trivially wait 3600s. So the design as stated is unimplementable without mocking.

**Nothing else breaks** — the production `_dispatch_cli` / `_dispatch_sampling` code, the `_wait_for_job` finally-block cleanup, the `_do_shutdown` snapshot+unlink path, and the `_run_cli_budgeted` raise path are all unaffected by this defect. The bug is only in the **test design for the *third* consumer** (`_evict_stale`).

**Required fix:** Rewrite the test design for this case. Replace "small max_lifetime_s" with one of:
- (a) Patch `_JOB_TTL_SECONDS` module-level constant to a small value (e.g. `mcp_jobs._JOB_TTL_SECONDS = 0.001`), create a completed job entry, call `get_job` (which triggers `_evict_stale`), then assert the tmpfile is unlinked.
- (b) Directly inject a stale entry into `mcp_jobs._jobs` dict with a past `created_at`, then call `_evict_stale()` directly and assert.
- (c) Mock `time.monotonic()` to return a value > `created_at + 3600`.

Option (a) or (b) is simplest. Remove `max_lifetime_s` from the instruction entirely — it's irrelevant to `_evict_stale`.

---

## Observations (no plan defect, just note)

- **Sub-task 4.1 already covered** (`test_legacy.py:220-238`): The existing `test_git_blame_unavailable_produces_unavailable_attribution` already tests the "blame fails → git-blame: unavailable" case. The plan's instruction to add this test would produce a redundant duplicate. Not a defect — a duplicate test passes harmlessly — but the implementer should know the coverage already exists so they can decide whether to add a separate untracked-file test or skip.

- **All 11 R3 fixes verified correct against real code.** The `_load_trusted_yaml_focus` helper correctly serves both callers (CLI `_run` and `_dispatch_sampling`); the dual-tmpfile guard covers every exit path (creation-failure → both unlinked, dispatch-raise → both unlinked, inline → both unlinked, `start_job`-raise → all three unlinked, `start_job` success → both transferred to job); the invariant-comment carve-out is sound because the second raw read is exclusively gated by `is_trusted_focus`; cross-references across all sections are consistent.

---

**Verdict: 1 confirmed blocker on the `_evict_stale` test design. Fixing that unblocks the plan for implementation.**
