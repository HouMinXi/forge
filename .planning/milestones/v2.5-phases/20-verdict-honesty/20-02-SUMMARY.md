---
phase: 20-verdict-honesty
plan: 02
type: summary
status: completed
commits:
  - c10519a  # chore: import wave-1 runtime.py from 20-01
  - 9b50c5b  # test: failing tests for RuntimeRunner wiring + smoke status
  - aa17ecc  # feat: wire RuntimeRunner + smoke-run CLI + smoke status display
  - 87ba03f  # feat: RUNTIME axis SKILL.md mirrors + D-10 drift test
tests_added: 19
tests_file: tests/test_runtime_machine.py (15), tests/test_runtime_drift.py (4)
---

# Phase 20-02 Summary: CLI Wiring + SKILL.md Mirrors

## What Was Done

### Task 1: smoke-run CLI subcommand + machine.py RuntimeRunner wiring

**cli.py** (+154 lines):
- Added `code-forge smoke-run [--surface NAME] -- CMD` subcommand
- Handler executes the command via subprocess, captures stdout+stderr
- Writes smoke receipt via `write_smoke_receipt` (diff-hash keyed, atomic)
- Surface name sanitized to `[a-zA-Z0-9_-]` before filesystem use (T-20-02)
- Exits with the command's exit code (passthrough)
- RuntimeRunner instantiated and added to `advisory_runners` list when creating StateMachine for review

**machine.py** (+66 lines):
- Imports `RuntimeRunner` from `code_forge.runtime`
- `_display_smoke_status()` method added -- always prints `--- Smoke Status ---` on stderr
- Three-case handling per D-09/F4:
  - (a) `runtime-smoke-summary` found: show N/M surfaces + NOT VERIFIED list
  - (b) `runtime-skipped` found: show skip reason
  - (c) fallback: "smoke: no runtime surfaces detected"
- Generic `_display_advisories` loop skips `runtime-smoke-summary` and `runtime-skipped` (DEDUP)
- `_display_smoke_status` called before early-return guard (D-09: silence never reads as verified)

### Task 2: SKILL.md mirrors + drift test

**src/code_forge/skills/code-forge/SKILL.md** (+42 lines):
- Added RUNTIME axis subsection in Step 4 with verbatim `RUNTIME_LIFECYCLE_QUESTION` text

**src/code_forge/skills/smoke-test/SKILL.md** (+43 lines):
- Added `code-forge smoke-run` usage documentation with UNVERIFIED contract

**tests/test_runtime_drift.py** (new, 48 lines, 4 tests):
- Imports `RUNTIME_LIFECYCLE_QUESTION` from `code_forge.runtime`
- Asserts verbatim appearance in `skills/code-forge/SKILL.md` content
- D-10 anti-drift enforcement

## Must-Haves Verification

| Truth | Status |
|-------|--------|
| smoke-run executes cmd + writes receipt | PASS |
| RuntimeRunner in advisory_runners | PASS |
| Smoke status ALWAYS printed on stderr | PASS |
| UNVERIFIED is axis status, not verdict | PASS |
| NOT VERIFIED surfaces in output | PASS |
| RUNTIME never blocks cycle counter | PASS |
| SKILL.md carries lifecycle question | PASS |
| Drift test asserts constant == SKILL.md | PASS |

## Test Results

19 passed in 0.13s (15 test_runtime_machine + 4 test_runtime_drift)

## D-Coverage

- D-02: UNVERIFIED is axis status only
- D-04: RuntimeRunner in advisory_runners; SKILL.md for inline outlet
- D-07: smoke-run wrapper writes receipt keyed by diff content-hash
- D-09: _display_smoke_status always called before early-return guard
- D-10: drift test asserts RUNTIME_LIFECYCLE_QUESTION == SKILL.md mirror
- D-11: NOT VERIFIED surface list in _display_smoke_status output
