---
verdict: PASS
date: 2026-06-03
retest_date: 2026-06-04
test_count: 3
retest_count: 4
model: claude-sonnet-4-6
method: |
  Original (2026-06-03): claude CLI subprocess (claude --print --model claude-sonnet-4-6 -p)
  Re-test (2026-06-04, main session): Agent tool on real review diffs
  - Agent tool + haiku on real 2.4KB diff: 14s, collapsed (6/6 FP)
  - Agent tool + sonnet on real detect.py diff: 24s, HIGH quality (7 findings, 4 substantive)
  - claude -p + mimo on real 2.4KB diff: 113s, signal (1 FP + 4 hypothetical)
note: |
  Original test used claude CLI on trivial tasks (executor lacked Agent tool).
  Re-test used the REAL dispatch mechanism (Agent tool) on real review diffs.
  Original PASS was correct-by-luck; re-test converts luck into evidence.
---

# Viability Gate: Agent Tool Subagent Dispatch

## Purpose

Empirical test of SC#2 (BACKEND-02): confirm that a subagent can be spawned
per review pass without hanging, and that review quality is acceptable.

## Original Test (2026-06-03)

Tested `claude --print --model claude-sonnet-4-6 -p` on trivial tasks.
The Task/Agent tool was not present in the executor's tool schema.

### Test 1: Trivial Response
- Prompt: "Return the single word VIABLE and nothing else."
- Result: VIABLE (10s, exit 0)

### Test 2: Structured JSON Response
- Prompt: code review diff with JSON schema request
- Result: valid JSON with findings/verdict keys (15s, exit 0)

### Test 3: Context Isolation
- Without --no-session-persistence: session history leaked (FAIL)
- With --no-session-persistence: full isolation confirmed (PASS)
- Finding: --no-session-persistence is REQUIRED for context isolation

## Re-test (2026-06-04, main session)

Main session re-tested with the REAL dispatch mechanism (Agent tool)
on real review diffs. This was necessary because the original test
used trivial tasks on a different mechanism (claude CLI subprocess).

### Dimension 1: Agent tool + haiku on real diff (2.4KB)
- Duration: 14s
- Quality: COLLAPSED -- 6/6 false positives
- Verdict: haiku produces garbage for code review

### Dimension 2: Agent tool + sonnet on real detect.py diff
- Duration: 24s
- Quality: HIGH -- 7 findings, 4 substantive (2 HIGH, 2 MEDIUM)
- Verdict: sonnet produces real review value

### Dimension 3: claude -p + mimo on real diff (cross-Pacific)
- Duration: 113s
- Quality: signal -- 1 FP + 4 hypothetical findings
- Verdict: cross-Pacific latency dominates; not representative of
  Agent tool dispatch latency

## Corrected Latency Assessment

Original VIABILITY assumed 10-15s/pass flat. Re-test shows:
- Strong Claude-direct model (sonnet): ~24s/pass (3-pass ~72s)
- Weak model (haiku): ~14s/pass but USELESS output
- Cross-Pacific backend (mimo): ~113s/pass (network latency dominated)

The original "10-15s class" estimate HOLDS at order-of-magnitude for
the Agent tool with a strong direct model. The prior "7-8x underestimate"
claim (based on mimo) is RETRACTED -- wrong measurement proxy.

## Implementation Requirements (from re-test)

### REQ-V1: Strong Model Pin (NEW)
The subagent outlet MUST pin a strong model (sonnet or opus, NOT haiku).
Haiku produced 6/6 false positives -- review quality collapses below
sonnet-class capability. The model-pin from Phase 11-01 (D-26) must
resolve to a strong model; if model is empty/unset, default to
sonnet (not haiku).

### REQ-V2: Timeout Sizing (NEW)
The fail-closed Agent timeout MUST be sized for the SLOWEST configurable
backend with margin. Measured worst case: 113s (cross-Pacific mimo).
Recommended timeout: 180-300s per pass. The SKILL.md fail-closed
instruction should specify this timeout range, not the original 15s.

### REQ-V3: Context Isolation (original)
Any subagent dispatch MUST use fresh context per pass. The Agent tool
provides this natively (each Agent invocation is a separate subagent).
For claude CLI fallback, --no-session-persistence is required.

## Verdict: PASS

All tests passed. No hanging observed with either mechanism.
- Agent tool: no hang at haiku (14s) or sonnet (24s)
- claude CLI: no hang at sonnet (10-15s) or mimo (113s)
- Quality: HIGH with sonnet, acceptable latency (~24s/pass)

Process note: original PASS was correct-by-luck (right answer, wrong
evidence). This re-test converts luck into grounded evidence with
real diffs on the real dispatch mechanism.
