---
phase: 33
reviewers: [deepseek, minimax, mimo, gemini]
reviewed_at: "2026-06-29"
plans_reviewed: [33-01-PLAN.md, 33-02-PLAN.md]
rounds: 5
convergence: "R5 -- 3/3 returning models 0 findings"
---

# External AI Plan Review -- Phase 33 MCP Server

## Convergence Ledger

| Round | Models | Findings | Fixes Applied |
|-------|--------|----------|---------------|
| R1 | DS/MM/MIMO | 3B + 8H | D-33-11 ~ D-33-21 |
| R2 | DS/MM/MIMO | 2B + 6H | D-33-22 ~ D-33-28 |
| R3 | DS/MIMO/GM/MM | DS:0 MIMO:0 GM:1B+1H+2M MM:1M | D-33-29 ~ D-33-32 |
| R4 | DS/MIMO/GM/MM | DS:1H+1M MIMO:0 GM:3H+2M MM:0 | evict test fix, Path.exists mock, D-33-29/30 tests |
| R5 | DS/MM/MIMO | **0/0/0/0** (all 3) | -- converged |

GM (Gemini): VPN disruption in R3/R5, skipped. Contributed 1B+1H+2M in R3, 3H+2M in R4.

## Round 1 Consensus (D-33-11 ~ D-33-21)

### BLOCKERs

1. **C-B1: communicate() double-call data loss** (DS)
   asyncio.wait_for cancels inner coroutine; second communicate() gets empty.
   FIX: D-33-11 -- asyncio.shield() + pass comm_task to start_job.

2. **C-B2: resolve_outlet wrong call signature** (MM, MIMO)
   No configs passed -> zero-config guard always fires.
   FIX: D-33-12 -- _load_gate_backends() first, pass configs=.

3. **C-B3: FastMCP wraps Pydantic as JSON text** (MM, MIMO)
   structured_output=True puts JSON in content[], not raw stdout.
   FIX: D-33-13 -- manual CallToolResult construction.

### HIGHs

4. **C-H1: Subprocess orphans** (DS, MM, MIMO) -> D-33-14
5. **C-H2: _jobs memory leak** (MM, MIMO) -> D-33-15
6. **C-H3: forge_job_status raw dict** (DS, MM, MIMO) -> D-33-16
7. **C-H4: Backend enum unimplementable** (DS, MM) -> D-33-17
8. **C-H5: Tempfile race on timeout** (MM, MIMO) -> D-33-18
9. **C-H6: Path.cwd() fragile** (MM, MIMO) -> D-33-19
10. **C-H7: findings_count always 0** (DS, MM, MIMO) -> D-33-20
11. **C-H8: ValueError unhandled** (DS, MIMO) -> D-33-21

## Round 2 Consensus (D-33-22 ~ D-33-28)

### BLOCKERs

12. **C2-B1: Shield placement wrong** (MM, DS)
    ensure_future(shield(coro)) leaves comm_task cancelled after timeout.
    Python code verified: comm_task.cancelled() == True.
    FIX: D-33-22 -- create_task first, shield wraps for wait_for, pass inner_task.

13. **C2-B2: resolve_outlet does HTTP reachability probe** (MIMO)
    _check_backend calling resolve_outlet adds 2-7s latency + burns API quota.
    Grep verified: outlet_resolver.py L225 calls probe_backend().
    FIX: D-33-23 -- emptiness check only, no resolve_outlet.

### HIGHs

14. **C2-H1: Test patches wrong module** (MM) -> D-33-24
15. **C2-H2: Test patches lifespan context not module var** (MM) -> D-33-24
16. **C2-H3: _wait_for_job no exception handling** (MIMO) -> D-33-25
17. **C2-H4: Shield semantics untested** (MM, MIMO) -> D-33-26
18. **C2-H5: cleanup_all kill-grace untested** (MM) -> D-33-27
19. **C2-H6: Tempfile finally scope ambiguity** (MM, MIMO) -> D-33-28

## Round 3 Findings (D-33-29 ~ D-33-32)

20. **GM-B1: CancelledError orphans proc+task** (GM)
    FIX: D-33-29 -- catch CancelledError, kill proc, cancel task, re-raise.

21. **GM-H1: gate.yaml missing vs untrusted indistinguishable** (GM)
    FIX: D-33-30 -- check gate_yaml_path.exists() before _load_gate_backends.

22. **GM-M1: Tempfile not flushed before subprocess** (GM)
    FIX: D-33-31 -- tmp.close() before _run_cli_budgeted.

23. **GM-M2: _evict_stale + _wait_for_job KeyError race** (GM)
    FIX: D-33-32 -- evict does NOT remove running entries.

24. **MM-M1: evict only fires from _wait_for_job** (MM)
    FIX: get_job() calls _evict_stale() on every retrieval.

## Round 4 Findings (doc consistency)

25. **DS-H1: test_evict_stale asserts entry removed, violates D-33-32** (DS, GM)
    FIX: test renamed, asserts entry STILL in _jobs.

26. **DS-M1: success_criteria says "handler finally" -- violates D-33-28** (DS)
    FIX: reworded to "handler deletes immediately after inline completion".

27. **GM-H2: pre-flight tests lack Path.exists mock** (GM)
    FIX: NOTE added, all pre-flight tests patch Path.exists=True.

28. **GM-H3: missing D-33-30 gate.yaml-missing test** (GM)
    FIX: test_preflight_gate_yaml_missing_raises added.

29. **GM-M1: missing D-33-29 CancelledError test** (GM)
    FIX: test_run_cli_budgeted_cancelled_kills_proc added.

## Round 5 -- Convergence

All 3 returning models (DS, MM, MIMO) reported **0 findings**.
User gate: PASSED with pre-execute doc fixes (MED-1, LOW-1~3).

## Decision Trace

| Decision | Origin | Supersedes | Round |
|----------|--------|------------|-------|
| D-33-11 | R1 C-B1 | -- | 1 |
| D-33-12 | R1 C-B2 | -- | 1 |
| D-33-13 | R1 C-B3 | -- | 1 |
| D-33-14 | R1 C-H1 | -- | 1 |
| D-33-15 | R1 C-H2 | -- | 1 |
| D-33-16 | R1 C-H3 | -- | 1 |
| D-33-17 | R1 C-H4 | D-33-07 enum | 1 |
| D-33-18 | R1 C-H5 | -- | 1 |
| D-33-19 | R1 C-H6 | -- | 1 |
| D-33-20 | R1 C-H7 | D-33-05 int | 1 |
| D-33-21 | R1 C-H8 | -- | 1 |
| D-33-22 | R2 C2-B1 | D-33-11 shield | 2 |
| D-33-23 | R2 C2-B2 | D-33-03/D-33-12 | 2 |
| D-33-24 | R2 C2-H1/H2 | -- | 2 |
| D-33-25 | R2 C2-H3 | -- | 2 |
| D-33-26 | R2 C2-H4 | -- | 2 |
| D-33-27 | R2 C2-H5 | -- | 2 |
| D-33-28 | R2 C2-H6 | D-33-18 | 2 |
| D-33-29 | R3 GM-B1 | -- | 3 |
| D-33-30 | R3 GM-H1 | -- | 3 |
| D-33-31 | R3 GM-M1 | -- | 3 |
| D-33-32 | R3 GM-M2 | -- | 3 |
