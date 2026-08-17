# Phase 48 Summary -- stream TTFT + truncation continuation

**Shipped 2026-08-16, merged to main as 59c1c51 (7 commits,
3be9d46..2d2c932, branch fix/stream-ttft-continuation). First phase of
the v2.9 ENV-GROUNDING lane to land.**

## What shipped

Two llm_invoke defects closed, in the order the plan scheduled them:

1. **Stream first-token visibility.** SSE passes now emit a `first
   token` progress event the moment the first content chunk arrives
   (llm_invoke.py:508 in `_read_sse`), so a streaming pass is no longer
   silent until the whole response lands. Additive only -- the stream
   parse path is otherwise untouched.
2. **Truncation recovery.** `finish_reason=length` detection already
   existed at three raise sites; the gap was recovery. Now: the
   truncated partial rides a `_TruncatedResponse` carrier; a run-level
   `TruncationBreaker` (threshold 5, sticky trip, monotonic count,
   locked increments) records each truncation and stops the run once
   tripped; `_continue_truncated` continues the reply with a budget of 2
   attempts. The partial is fenced into the continuation prompt and
   declared untrusted data (never instructions), the combined reply
   must parse as a full forge envelope (findings AND code_excerpts),
   already-complete JSON short-circuits before any continuation
   request, and trip propagation is two-level: a trip error is
   re-raised as the first clause of both try layers, before any broad
   invoke-error handling can budget it away.

## Plan lineage

- RESEARCH.md: Claude Code query.ts layered recovery, OpenCode
  finish_reason=length detection + single continuation + zero-output
  no-continue, Codex TTFT event stream. Layer 1 (raising max_tokens at
  request time) was researched and dropped (D-1..D-4).
- PLAN.md: D-1..D-11, then CP1b external panel -- kimi R1, mimo
  R1/R2/R3, deepseek-v4-flash R3 (substituted for kimi after the K2.7
  256K context wall). Five rounds total, amendments A-1..A-23 appended;
  amendments win over the plan body. Exit per the user's protocol:
  external models unanimous 0/0 -- mimo R3 NO FINDINGS + dsflash NO
  FINDINGS (cp-artifacts/).
- Execution: T0 drift probe (non-blocking, T0-PROBE.md), T1-T4 atomic
  commits, TDD with injection matrix I1-I9 + amendment-driven EXTRA-1/2
  (INJECTIONS.md, including the honest I4b lock-variant observation).

## Forge code review

Three rounds on the branch. Round 1 (10 findings) -> fix batch b2a7a3b;
round 2 (10) -> 7b0ddcf; round 3 (10) -> 2d2c932. Every applied fix was
injected at its own fix site and proven fail -> revert -> pass (per-fix
records in INJECTIONS.md). Dismissed findings were substance-free
repeats (threading import, envelope any-key covered by the downstream
schema gate) or non-code artifacts. Loop exited on the
substance-free-repeat criterion under the deployment deadline; full
dispositions in EXIT-CHECKLIST.md.

## Verification (real output, not narration)

- Worktree post-R3 full suite: 3393 passed, 9 skipped.
- Main @ 59c1c51 full suite: 3415 passed, 9 skipped, 4 warnings,
  778.89s. The 22-test delta over the worktree count is the
  eval-corpus-findings merge (cf99468) already on main.
- Deployment: rsync src/ to yinhe-laptop (192.168.100.120), live
  checks passed -- `_continue_truncated`, `TruncationBreaker`, and the
  progress emit all present on the laptop copy.
- Pushed: origin + gitee (b2d800d..59c1c51).

## Commits

```
3be9d46 llm: emit first-token progress event from the SSE stream
0d34ec2 llm: carry truncated payloads and count truncation events per run
f8ec5f2 llm: recover truncated replies with a bounded continuation
b2a015d llm: wire the truncation breaker through the review run
b2a7a3b llm: harden truncation recovery against review findings
7b0ddcf llm: recover already-complete JSON and harden the continuation loop
2d2c932 llm: harden the continuation dispatch against trip swallowing
```

## Open follow-ups

Six, none blocking: 48-FOLLOWUPS.md (configurable breaker threshold,
cross_repo.py wiring decision, T0 drift re-probe, no_json extension
point, bonsai TTFT smoke, worktree review artifacts).

## Known gaps

- Bonsai TTFT smoke not run (BONSAI_API_KEY absent in the execution
  environment).
- T0 probe could not force the sn-deepseek-flash clamp in one bounded
  call (unexpected_success, output_tokens=11590 both attempts); A1
  remains grounded by the gate.yaml measurement.
- cross_repo.py runs get per-call recovery only, no run-level trip
  memory (Contract non-goal, follow-up 2).
