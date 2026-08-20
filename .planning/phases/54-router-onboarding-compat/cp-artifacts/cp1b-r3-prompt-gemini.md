# Review assignment (Gemini) — R3, FIRST + FINAL EXIT ROUND

Read the shared briefing first (it contains full R1+R2 history from
deepseek and kimi-k2.7; you were never able to run R1 due to a CN VPN exit
block, so this is your first pass on this plan):
/home/houminxi/code/forge/.planning/phases/54-router-onboarding-compat/cp-artifacts/cp1b-r1-briefing.md

Then review the CURRENT plan text:
/home/houminxi/code/forge/.planning/phases/54-router-onboarding-compat/54-01-PLAN.md

You have file access to the repo at /home/houminxi/code/forge — read the real
source before asserting anything.

## What the plan looks like now (all fixed since the R1 briefing was written)

- Eight-class error taxonomy fully pinned: timeout, credential-rejected,
  connection-refused, SSE-mixed, JSON-malformed, truncated-output,
  http-error (exit_code >= 400, kind="" — the headline wrong-/v1 case),
  unclassified.
- Probe call passes `continuation_breaker=TruncationBreaker(threshold=1)`
  so the first truncation immediately raises kind="truncated" before any
  continuation request — restores a hard 60s bound on the probe.
- Task 2's warn is scoped to the two mutating trust paths only (bare
  invocation, `--revoke`); `--status` is exempt (test (f) asserts this).
- Both `resolve_contract_specs` call sites pass the resolved workspace, not
  raw cwd.
- Autouse `FORGE_PROJECT_DIR` delenv fixture mandated in both trust test
  files.

Two independent fresh internal reviewers (a goal-backward plan-checker and
an 8-pass PBR review) have each re-verified the current plan text against
the live repo from scratch and both report 0/0/0/0. Two rounds of external
review from deepseek and kimi-k2.7 have also converged to 0 BLOCKER/0 HIGH,
with all their LOW findings fixed as described above.

## Your task

This is your angle from cp1b-r1-prompt-gemini.md, unchanged: RUNTIME
SEMANTICS — mentally execute the plan's prescribed code:

1. RUNTIME SEMANTICS:
   a. Import graph: llm_invoke.py:29 does `from .backend import ...` at
      module level; the plan has backend.py import llm_invoke FUNCTION-locally
      inside probe_backend_live, and doctor.py import the helper
      function-locally. Trace the actual import order at process start and on
      first doctor call. Is there ANY path (tests included, conftest import
      order included) where the cycle still bites?
   b. Timeout chain: effective_invoke_timeout_s priorities
      (llm_invoke.py:558-592, verify against current line numbers) vs the
      probe's replace(cfg, timeout_s=60). What ACTUALLY bounds the call:
      connect timeout, read timeout, total? Where does a hung-but-connected
      backend get killed?
   c. Ordering: `_TruncatedResponse` catch before the attempt check, now
      combined with `continuation_breaker=TruncationBreaker(threshold=1)` —
      with max_tokens=32 + a JSON-demanding prompt, enumerate the response
      shapes that still trigger continuation vs immediately raise
      kind="truncated", and what the probe reports for each. Does the
      threshold=1 breaker actually fire before any continuation request is
      sent, or after the first one completes?
   d. Frozen dataclass replace() with the zeroed fields — verify each
      zeroing has the claimed effect in the request-builder code paths
      (thinking, effort, caps).
   e. Mock realities: the plan's prescribed patch targets
      (code_forge.backend.probe_backend_live, user_config_path, record_trust)
      — do they exist at those module paths AFTER the plan's own import
      style is applied?
2. ENV/CONCURRENCY — os.environ read inside _run_trust; the probe loop is
   serial over backends; doctor's has_fail accumulation. Any host-state or
   ordering dependence that survives the plan's guards?

Verify every claim against the real files. Follow the briefing's output
contract exactly, ending with `SCORECARD: B=<n> H=<n> M=<n> L=<n>`.
