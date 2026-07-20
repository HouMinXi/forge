---
phase: 20
slug: verdict-honesty
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-06-12
---

# Phase 20 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.x |
| **Config file** | pyproject.toml |
| **Quick run command** | `python3 -m pytest tests/ -x -q --tb=short` |
| **Full suite command** | `python3 -m pytest --tb=short -q` |
| **Estimated runtime** | ~316 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python3 -m pytest tests/ -x -q --tb=short`
- **After every plan completion:** Run full suite
- **Coverage target:** 80%+ on new code

---

## Validation Architecture

Derived from 20-RESEARCH.md:

### Key Validation Points

1. **RuntimeRunner advisory output** — verify UNVERIFIED when no receipt, VERIFIED when receipt present
2. **smoke-run receipt integrity** — content-hash binding to diff, tamper detection
3. **Per-surface accounting** — N/M surfaces verified count is accurate
4. **Verdict display** — "not verified" section appears in output when surfaces are unverified
5. **Advisory enforcement** — RUNTIME axis never blocks cycle, never gates commit
6. **Eval axis scoring** — E1-E6 expected_advisory matches actual pipeline output
7. **Anti-drift test** — lifecycle question constant in runtime.py matches SKILL.md mirror

### Risk Areas

- E1-E6 expected_verdict correction (must be verified per-entry, not assumed)
- Two-outlet mirroring (inline vs subprocess must produce identical RUNTIME output)
- Receipt forgery resistance (content-hash must bind to actual diff)
