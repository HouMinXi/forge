# Phase 48 T0 Probe Record

**Date:** 2026-08-16
**Script:** .planning/phases/phase-48/a1_probe.py (kept on disk)
**Command:** `cd /home/houminxi/code/forge && python3 .planning/phases/phase-48/a1_probe.py`
**Environment:** OMNIROUTE_API_KEY set; gateway 192.168.100.10:20128 reachable (HTTP 307 on /); code_forge imported from main-tree src (identical to worktree at T0, no source changes yet).

## Verdict line (verbatim)

```
A1_PROBE unexpected_success output_tokens=11590
```

## Interpretation

The single bounded call (max_attempts=1, timeout_s=600) completed normally with
11590 output tokens and did NOT raise kind=truncated. A1
(sn-deepseek-flash reports finish_reason=length on its ~16384 clamp) is
neither confirmed nor refuted by this probe: the engineered prompt asked for
1500 findings entries of ~150+ chars each, which should far exceed 16384
output tokens, but the model returned a shorter, complete response and
finished under the clamp.

Assessment: the synthetic prompt under-produced relative to the clamp; it
does not reproduce the load shape of the real review workload (64K-input
review prompt) under which gate.yaml records finish_reason=length at ~16384
output, measured 2026-08-11. A1 therefore remains grounded by the
gate.yaml measurement; the probe records only that the drift check could
not force the clamp in one bounded call.

Per PLAN.md D-3: the probe is non-blocking and all three defined outcomes
(truncated / no_json / not_run) leave the plan's tasks unchanged. This
fourth outcome (unexpected_success) likewise changes nothing: the
continuation recovery ships anyway and is correct for any backend that
reports length.

## Re-run post-T3 (A-11: threshold=1 breaker, no continuation)

**Command:** `cd /home/houminxi/code/forge && PYTHONPATH=/home/houminxi/code/forge/.worktrees/stream-ttft/src python3 .planning/phases/phase-48/a1_probe.py`
**code_forge resolved to:** worktree src (T3 code) -- verified via `m.__file__` before the run.

```
A1_PROBE unexpected_success output_tokens=11590
```

Identical verdict and identical token count, returned fast. [INFERRED, MED]
this is an OmniRoute semantic-cache replay (same prompt, temperature 0,
cache key = SHA256(model+messages+temperature+top_p); documented in forge
memory `feedback_gate_yaml_split_brain.md`), not a fresh model run. The
discriminating experiment -- varying the prompt text or adding the
cache-bypass header -- is a follow-up candidate, not executed this phase
(the probe is bounded: one call per attempt, two attempts made).

Combined probe record: neither attempt forced the clamp. A1 remains
grounded by the gate.yaml 2026-08-11 measurement (finish_reason=length at
~16384 output on the 64K-input review prompt); the synthetic prompt
under-produces relative to the clamp. The recovery path is correct for
any backend that reports length regardless of the probe outcome; the
no_json extension point (Contract non-goals) stays the recorded fallback
if a future varied-prompt probe refutes A1.

Follow-up candidate (no action this phase): a re-probe with a prompt that
constrains the model more tightly to bulk output (or a replay of the real
review prompt), plus a prompt variation to defeat the semantic cache,
would force the clamp and give a definitive verdict.
