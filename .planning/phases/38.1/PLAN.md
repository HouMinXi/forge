# Phase 38.1 Plan: MCP Server Process Lifecycle Hardening

## Commit Order (per spec: signal first, then companions)

### Commit 1: Server signal handler (core fix)

- [ ] T1: In `lifespan()` startup (mcp_server.py:82-110), after backend
  loading, get the running event loop and install signal handlers.
  Use per-signal closures to capture signum:
  `loop.add_signal_handler(signal.SIGTERM, lambda: _schedule_shutdown(signal.SIGTERM, loop))`
  `loop.add_signal_handler(signal.SIGINT, lambda: _schedule_shutdown(signal.SIGINT, loop))`
  `_schedule_shutdown(signum, loop)` is a plain `def` (sync, not async)
  that checks the `_shutting_down` flag, sets it, then calls
  `loop.create_task(_do_shutdown(signum))`. The async `_do_shutdown`:
  (a) calls a new `mcp_jobs.snapshot_tempfile_paths() -> list[str]`
      public helper that returns all tempfile_path and stderr_log_path
      values from _jobs entries BEFORE cleanup_all clears the dict,
  (b) runs `cleanup_all()` inside try/finally,
  (c) best-effort unlinks all snapshotted paths (missing_ok),
  (d) calls `sys.stdout.flush()` then `os._exit(128 + signum)` in
      the finally block so exit fires even if cleanup_all raises.
  Add `snapshot_tempfile_paths()` to mcp_jobs.py (new public function).
  This replaces the llm_invoke handler for the server process only.
  Guard: a module-level `_shutting_down` flag, checked in
  _schedule_shutdown, ensures the cleanup task is created at most
  once (prevents re-entrant SIGTERM from spawning duplicate tasks).
  Before `os._exit`, call `sys.stdout.flush()` to avoid truncating
  a partial JSON-RPC response on the stdio transport.
  Race note: SIGTERM during normal teardown is benign -- cleanup_all
  is idempotent (proc.returncode check), and os._exit terminates
  regardless of teardown state.
  `import signal` and `import sys` must be added to mcp_server.py.
  Files: mcp_server.py
  Done: SIGTERM kills idle server within 5s (was: survives indefinitely)

- [ ] T1-test: Acceptance test: spawn real server with stdin on a fifo,
  send SIGTERM, assert process exits within 5s. Bug-inject: remove
  add_signal_handler -> test goes RED (process survives); restore -> GREEN.
  In-flight child test: start a review job, SIGTERM server, confirm child
  terminated via cleanup_all escalation.
  Regression: stdin EOF still exits promptly.
  Files: tests/test_mcp_server.py
  Done: test_sigterm_kills_idle_server PASS + bug-inject RED/GREEN

- [ ] T1b: Probe what Claude Code does on /mcp reconnect. Write a wrapper
  script that logs signals + stdin-EOF to a file, register as a throwaway
  mcpServers entry, have user perform one /mcp reconnect, read log.
  Result determines whether signal fix alone stops accumulation.
  Files: /tmp probe script (not committed)
  Done: probe result documented in delivery briefing

### Commit 2: Remove premature job kill from TTL watchdog

- [ ] T-c: In `_evict_stale()` (mcp_jobs.py:157-173), remove the
  running-branch kill (lines 167-171). Terminal entries past TTL still
  evicted. Running procs keep one forced-death owner: `cleanup_all()`.
  Rationale for no replacement ceiling: a pathologically hung child
  lives until server shutdown, acceptable because (a) the signal fix
  makes shutdown reachable via SIGTERM, and (b) the progress tail from
  T-b lets a human see the job is stuck and act. A configurable
  ceiling is YAGNI until observed demand.
  Tempfile cleanup on eviction: when _evict_stale removes a terminal
  entry, unlink entry.get("stderr_log_path") and entry.get("tempfile_path")
  before popping (use .get() -- pre-T-b entries lack stderr_log_path;
  use missing_ok to handle already-unlinked files).
  Files: mcp_jobs.py
  Done: _evict_stale no longer calls proc.kill() on running entries;
  terminal eviction unlinks associated tempfiles

- [ ] T-c-test: Unit pair -- a running entry aged past TTL survives
  _evict_stale (proc alive, entry present); a terminal entry past TTL
  is removed.
  Files: tests/test_mcp_jobs.py or tests/test_mcp_server.py
  Done: both assertions PASS + bug-inject (restore kill -> test RED)

### Commit 3: Job progress visibility + real duration_s

- [ ] T-b: In `_run_cli_budgeted()` (mcp_server.py:228), create a
  named tempfile for child stderr, pass the file object to Popen
  (close the parent fd immediately after spawn). After `communicate()`,
  read the tempfile content and return it as the stderr string -- same
  inline return type as today, inline callers unchanged.
  Timeout path: `_run_cli_budgeted` returns a 3-tuple
  `(inner_task, proc, stderr_log_path)` instead of the current 2-tuple.
  The 3 call sites (mcp_server.py:488,571,635) destructure the extra
  element and pass `stderr_log_path` to `start_job()`. `start_job()`
  gains a `stderr_log_path` parameter and stores it in the job entry.
  Ground truth: `communicate()` returns `(stdout_bytes, None)` when
  stderr is a file object (verified on Python 3.14); guard: if
  `stderr_bytes is None`, read from the stderr log path instead
  (decode with `errors='replace'` to handle byte-boundary UTF-8 cuts).
  Add a `stderr_log_path` field to job entries (separate from the
  existing `tempfile_path` which holds contract paths). `_wait_for_job`
  reads stderr from the file, stores real elapsed time
  (`time.monotonic() - entry["created_at"]`) in the result dict.
  `forge_job_status` uses the stored elapsed time instead of the
  hardcoded 0.0. While status is "running", `forge_job_status` seeks
  to max(0, file_size - 2048) and reads forward (O(1) per poll).
  Cleanup: `_wait_for_job`'s finally block unlinks stderr_log_path
  alongside tempfile_path (use missing_ok -- _evict_stale may have
  already unlinked it). Inline path: unlink stderr tempfile in a
  try/finally after reading, before returning.
  Files: mcp_server.py, mcp_jobs.py
  Done: forge_job_status returns nonzero duration_s and stderr tail mid-run

- [ ] T-b-test: Bug-inject -- revert stderr redirect -> progress-tail test
  RED -> restore -> GREEN. Unit test: completed job has nonzero duration_s.
  Files: tests/test_mcp_server.py, tests/test_mcp_jobs.py
  Done: tests PASS + bug-inject RED/GREEN

### Commit 4: Contract compliance (different subsystem)

- [ ] T5: Diagnostic task -- Step 1 decides the fix direction.
  First: freeze the trinity-router contract and reviewed diff into repo
  test fixtures (reproducer must survive /tmp cleanup). Then verify
  contract text reaches pass prompts (check receipts or add a temporary
  prompt dump and re-run). Two branches:
  - Text never arrived -> plumbing bug in --contract path; fix plumbing.
  - Text arrived but was ignored -> LLM compliance issue; adjust prompt
    placement/wording of the do-NOT-flag list.
  Acceptance: re-run frozen diff with frozen contract; the 3 previously-
  violated exclusions produce zero findings while at least one unrelated
  true finding persists. This tests plumbing, not LLM obedience (C6:
  compliance is prompt-side best-effort, not a guarantee). State the
  caveat honestly in the delivery report.
  Files: factories.py (prompt wording), tests/fixtures/, tests/
  Done: frozen-contract deterministic test PASS

### Commit 5: Wall-clock attribution (diagnosis, decision gate)

- [ ] T6: Run a real long-diff review CLI-direct with stderr timestamped.
  Measure per-pass and inter-pass overhead. Identify if gap is inherent
  work or a defect (serial retries, sleep, redundant calls).
  Fix ONLY if defect found; otherwise document breakdown.
  Files: depends on diagnosis result
  Done: measured breakdown documented in delivery briefing

### Commit 6: Job ID error message + manual recovery docs

- [ ] T7: Enrich `forge_job_status` error (mcp_server.py:729) from
  "Unknown job_id: %s" to include: unknown in this server instance,
  server may have restarted, completed runs leave receipts.
  Files: mcp_server.py
  Done: unit test asserts enriched message text

- [ ] T4: Document manual recovery recipe in README or troubleshooting
  section: `pgrep -f code-forge-mcp` to list, `pkill -TERM`, then
  `pkill -KILL` if still alive. Also note job_id invalidation on restart.
  Files: README.md or docs/
  Done: recipe exists and is accurate

### Optional: Parent-death guard (secondary layer)

- [ ] T2: If small enough, add periodic `os.getppid()` check in lifespan
  or `prctl(PR_SET_PDEATHSIG)` via ctypes. With signal fix in place,
  PDEATHSIG's SIGTERM actually works. Ship only if it stays small.
  Files: mcp_server.py
  Done: orphaned-parent scenario causes self-exit (if implemented)

## Test Baseline

Current: 2443 passed, 7 skipped (main @ 07d0381 + TestResolveWorkspace
env cleanup). All new tests must maintain or increase this count.

## Review Plan

Step 0: syntax (python -m py_compile) + lint (ruff) + non-ASCII check
Steps 1-3: Three-cycle static review (qodo / expert / adversarial)
Step 4: Smoke test per acceptance criteria above
