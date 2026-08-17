# Surflare pain report triage, 2026-08-16

Source: /tmp/forge_pain_report_20260816.txt (surflare-watchdog dwell-storm
review night, ~5h wall, ~3h infra failure). Grounded against forge src @
f43323a. Dispositions per pain, evidence-tagged.

## Pain 1: job/progress state dies with the process -- CONFIRMED, known issue

mcp_jobs.py:78: `_jobs: dict[str, dict[str, Any]] = {}` -- pure in-memory
registry; any server/session restart orphans it. Module comment line 10
already tracks this ("tracked issue #2806, ~2026-07-28, replace _jobs
with ..."). The mutation heartbeat pattern (machine.py:317-374,
mutation-result.json with pid/status + stale detection) is the model to
generalize. Backlog item; needs design (run-scoped journal: start argv,
per-pass start/end, verdict, exit).

## Pain 2: silent exit 1 after successful L1 passes -- REAL, not repro on main

Evidence: 2x identical repro on 6a3996f (22:48/23:10): three pass token
lines, then exit 1, zero disk writes, .code-forge mtime bump only.

Natural experiments on f43323a (only delta: progress events + hang-idle
read rewrite):
- R6 (same diff/backend/mode): visible FAIL, receipts + state written.
- Probe (this session): trivial diff, all-3-success, near-zero findings
  -- verdict=PASS printed, exit 0, 2 clean rounds.

Static sweep of every post-pass stage (factories fold incl. coverage
guard, falsify, L2/E2E/coverage phases, receipts, CLI handlers): all
fail visibly or degrade. The only silent killer shape is a BaseException
leak, and the only functional change in the window is the hang-idle
rewrite of _read_with_deadline (worker-thread lifecycle).

Disposition: ship defensive hardening (CLI review dispatch catches
SystemExit from the pipeline and prints its traceback instead of dying
silent). Exact trigger stays open: re-verify todo filed; the reporter's
next review on f43323a is the natural test.

## Pain 2 follow-up: connect/TLS-phase hangs escape the idle bound

Observed 2026-08-16 on R5 of fix/silent-exit-visible: the LAN gateway
(192.168.100.10:20128) accepted TCP and stalled the TLS handshake for
~5 min; all three review sockets sat ESTAB with zero bytes and the
round died at exactly t+1200s. The 900s idle bound from the hang-idle
fix never fired because it is installed on response.fp.raw._sock --
which does not exist until the read phase. A handshake stall therefore
gets only the giant urllib-level timeout (backend.timeout_s = 1200s).
Remedy candidate: a separate short connect-timeout (30-60s) on the
HTTP(S) connection, distinct from the read deadline. Evidence: R5 log
+ ss capture in .planning/reviews/silent-exit-visible-r1/forge_r5.log
discussion; gateway recovered ~5 min later and answers in <100ms.

## Pain 3: no in-flight visibility -- FIXED, closed

f43323a progress events validated live three ways this session: R6's
per-pass lines, the wrapper e2e (forge_job_status surfaced events on
4/4 polls), and the probe (per-pass start lines at t+0.0s). Close.

## Pain 4: FORGE_LLM_TIMEOUT_S documented by failure -- CONFIRMED, fix now

llm_invoke.py:441-469 resolves it per call; no startup line anywhere
(grep banner: zero hits). Bundle with pain 8's startup banner.

## Pain 5: no pass-concurrency lever -- CONFIRMED, backlog

factories.py:325-328: ThreadPoolExecutor(max_workers=len(pass_configs))
hard-wired. Enhancement: --pass-concurrency N flag (serial mode = 1).
Backlog.

## Pain 6: no dirty-tree guard on cycle N+1 -- CONFIRMED, backlog

No dirty/uncommitted check besides the main-tree guard (cli.py:2506).
--committed reviews HEAD while post-cycle fixes sit uncommitted, so the
next cycle re-finds them. Enhancement: warn at cycle start when the
worktree is dirty vs the reviewed HEAD (warn, not refuse -- reviewing
with uncommitted fixes in flight is a legitimate pattern). Backlog.

## Pain 7: lock/state lifecycle -- PARTIAL, backlog + doc note

- Stale-lock recovery EXISTS (lock.py _pid_alive, 4c22afd, in 6a3996f
  already). The night's stale lock needs the symlink angle: worktree
  .code-forge symlinked to repo root (setup-mcp) means CLI (worktree)
  and MCP (repo root) share one lock/state/receipts -- cross-run bleed,
  confusing cycle numbering. Per-run isolation or run-id-stamped
  receipts. Backlog.
- state.json pickup by next local run is by design (_maybe_load_prior_state,
  STATE-01 resume) -- document, not a bug.

## Pain 8: startup banner -- CONFIRMED, fix now

No banner anywhere. Bundle with pain 4: one stderr line at review start:
repo @ sha, diff file count, effective LLM timeout (+ env var name),
pass mode. Catches wrong-cwd and wrong-target runs in the first seconds.
