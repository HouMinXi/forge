---
phase: 20-verdict-honesty
plan: 01
subsystem: runtime-advisory
tags: [advisory-axis, smoke-receipt, tdd, AxisRunner, REVIEW-RUNTIME-01]

dependency_graph:
  requires:
    - src/code_forge/advisory.py (AdvisoryFinding, AxisRunner Protocol)
    - src/code_forge/source.py (compute_source_hash)
    - src/code_forge/llm_invoke.py (llm_invoke, LLMInvokeError)
  provides:
    - RuntimeRunner (AxisRunner Protocol, is_advisory=True)
    - RUNTIME_LIFECYCLE_QUESTION constant (D-05/D-10)
    - write_smoke_receipt (atomic JSON, diff-hash-keyed)
    - read_smoke_receipts (directory reader)
  affects:
    - Plan 20-02: smoke-run CLI subcommand uses write_smoke_receipt
    - Plan 20-02: machine.py advisory_runners wires RuntimeRunner
    - Plan 20-03: SKILL.md mirror drift test reads RUNTIME_LIFECYCLE_QUESTION

tech_stack:
  added: []
  patterns:
    - TDD RED/GREEN cycle (failing tests before implementation)
    - Atomic tmp+replace receipt write (from receipt.py model)
    - AxisRunner Protocol (advisory.py, TaintRunner structural model)
    - str.replace not str.format for diff-with-braces safety

key_files:
  created:
    - src/code_forge/runtime.py
    - tests/test_runtime.py
    - tests/test_smoke_receipt.py
  modified: []

decisions:
  - "str.replace not str.format for diff placeholder: diffs with literal { or } would KeyError with .format()"
  - "runtime-smoke-summary finding only when surfaces > 0: GM-R5-L2 fix prevents spurious summary on no-surface diffs"
  - "Case-insensitive substring containment (either direction) for surface matching: tolerates LLM free-text vs user --surface flag mismatch"
  - "Atomic write via tempfile.mkstemp + Path.replace: no partial JSON visible during write"

metrics:
  duration: "10 minutes"
  completed: "2026-06-12"
  tasks_completed: 1
  files_created: 3
  tests_added: 56
  tests_passing: 1426
---

# Phase 20 Plan 01: RuntimeRunner Advisory Axis + Smoke Receipt Summary

**One-liner:** RuntimeRunner advisory axis (AxisRunner Protocol, is_advisory=True) with RUNTIME_LIFECYCLE_QUESTION constant, atomic diff-hash-keyed smoke receipts, and SKIPPED-on-failure never-silent semantics.

## What Was Built

### src/code_forge/runtime.py (355 lines)

**RUNTIME_LIFECYCLE_QUESTION constant (D-05/D-10):**
Fixed, auditable question asking about runtime surfaces (systemd, nftables, sockets, subprocess, file side effects, kernel modules, firewall), lifecycle/side-effect risks, and required smoke test coverage. Contains `{diff_text}` placeholder. Exports the constant so Plan 20-03 can drift-test the SKILL.md mirror.

**write_smoke_receipt(receipts_dir, diff_text, surface, command, exit_code, transcript, timestamp) -> Path:**
- Creates JSON file named `smoke-receipt-{surface}.json` via atomic tmp+replace pattern
- Fields: diff_sha256 (compute_source_hash), surface, command, exit_code (int), transcript_sha256 (sha256 of bytes), timestamp, status (VERIFIED/FAILED by exit_code)
- Calls `receipts_dir.mkdir(parents=True, exist_ok=True)` before writing
- D-01/D-07: machine-verifiable receipt keyed by diff content-hash

**read_smoke_receipts(receipts_dir) -> list[dict]:**
- Reads only `smoke-receipt-*.json` files (ignores review receipts)
- Returns `[]` when directory absent or empty
- Skips unreadable/malformed files silently

**RuntimeRunner class:**
- `is_advisory = True` (structural never-block invariant)
- `__init__(backend=None)`: stores backend, source_files=None, infra_errors=[]
- `run(diff_text, repo_root)`:
  - Guard: empty diff -> `[]`
  - Computes diff hash via `compute_source_hash(git_diff=diff_text)`
  - LLM call via `RUNTIME_LIFECYCLE_QUESTION.replace("{diff_text}", diff_text)` (NOT .format -- brace-safe)
  - On LLMInvokeError: returns SKIPPED AdvisoryFinding (D-04 never-silent-skip)
  - Parses `{"surfaces": [...], "findings": [...]}` from LLM response
  - On malformed JSON / missing "surfaces" key: returns SKIPPED finding (D-04)
  - Reads receipts from `repo_root/.code-forge/smoke-receipts/`
  - Validates: `diff_sha256 == current_hash AND status == "VERIFIED"` (TOCTOU detection, Pitfall 3)
  - Case-insensitive substring containment match (either direction) for surface -> receipt mapping (D-11)
  - Builds per-finding AdvisoryFinding list (axis="RUNTIME")
  - When surfaces > 0: adds `id="runtime-smoke-summary"` finding
  - When surfaces == 0: no summary finding (GM-R5-L2 fix)
  - Clears infra_errors at start of each run

### tests/test_runtime.py (546 lines, 38 tests)

Covers: constant properties, Protocol conformance, empty-diff guard, str.replace vs str.format, JSON parsing, LLMInvokeError SKIPPED, malformed JSON SKIPPED, smoke receipt UNVERIFIED/VERIFIED/TOCTOU, case-insensitive matching, 0-surfaces-no-summary, summary axis=RUNTIME, infra_errors cleared between runs.

### tests/test_smoke_receipt.py (440 lines, 18 tests)

Covers: file creation, naming, VERIFIED/FAILED status, diff_sha256 keying, transcript_sha256, all required fields, exit_code int, surface/command/timestamp stored, parents created, atomic write, valid JSON, read empty/absent dir, read single/multiple, list[dict] return, non-smoke-receipt files ignored, round-trip.

## TDD Gate Compliance

- RED commit: `1bbe5e5` -- all 56 tests failing (`ModuleNotFoundError: No module named 'code_forge.runtime'`)
- GREEN commit: `273ec9a` -- all 56 tests pass; full suite 1426 pass, 1 pre-existing semgrep skip unrelated to this plan

## Deviations from Plan

None -- plan executed exactly as written.

## Known Stubs

None. RuntimeRunner, write_smoke_receipt, read_smoke_receipts are fully implemented. Ready for Plan 20-02 wiring into machine.py advisory_runners and cli.py smoke-run subcommand.

## Threat Surface Scan

No new threat surface beyond the plan's threat model:
- T-20-01 mitigated: receipt diff_sha256 re-validated at read time; mismatch = discard
- T-20-02 mitigated: surface name stored as JSON data only, never shell-interpolated
- T-20-03 mitigated: LLM JSON parse wrapped in try/except; missing keys -> SKIPPED
- T-20-04 accepted: transcript sha256-hashed, not stored verbatim

## Self-Check: PASSED

Verified:
- `src/code_forge/runtime.py` exists: YES
- `tests/test_runtime.py` exists: YES (546 lines, > 80 line minimum)
- `tests/test_smoke_receipt.py` exists: YES (440 lines, > 60 line minimum)
- RED commit `1bbe5e5` exists: YES (`git log --oneline` confirmed)
- GREEN commit `273ec9a` exists: YES (`git log --oneline` confirmed)
- 56 tests pass: YES (verified with `pytest tests/test_runtime.py tests/test_smoke_receipt.py -x -q`)
- Full suite: 1426 pass, 1 pre-existing failure (test_taint_rule::test_semgrep_validate, requires semgrep)
- No unexpected file deletions in commits: VERIFIED (both commits are create-only)
