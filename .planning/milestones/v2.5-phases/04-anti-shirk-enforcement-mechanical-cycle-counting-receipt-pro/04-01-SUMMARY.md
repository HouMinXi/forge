---
phase: 04-anti-shirk-enforcement-mechanical-cycle-counting-receipt-pro
plan: 01
subsystem: anti-shirk
tags: [llm-invoke, falsify-real, consecutive-clean, receipt, verify, attestation, skill-md]
dependency_graph:
  requires: [01-r1-commit-gate, 02-r2-mutation, 03-r3-e2e-coverage]
  provides: [llm-invoke-shim, real-falsifier, consecutive-clean-counter, receipt-writer, verify-subcommand, pre-commit-attestation, receipt-protocol-docs]
  affects: [machine.py, cli.py, factories.py, state.py, install_hooks.py, verify.py, receipt.py, llm_invoke.py, falsify_real.py, SKILL.md]
tech_stack:
  added: [subprocess LLM shim, receipt JSON protocol, 7-check verify, attestation check]
  patterns: [consecutive-clean-convergence, per-pass-receipt, engine-gating]
key_files:
  created:
    - src/code_forge/llm_invoke.py
    - src/code_forge/falsify_real.py
    - src/code_forge/receipt.py
    - src/code_forge/verify.py
    - tests/test_llm_invoke.py (9 tests)
    - tests/test_falsify_real.py (6 tests)
    - tests/test_consecutive_clean.py (5 tests)
    - tests/test_receipt.py (3 tests)
    - tests/test_verify.py (6 tests)
  modified:
    - src/code_forge/machine.py
    - src/code_forge/state.py
    - src/code_forge/factories.py
    - src/code_forge/cli.py
    - src/code_forge/install_hooks.py
    - src/code_forge/skills/code-forge/SKILL.md
decisions:
  - compute_source_hash (Option A) is the single hash path for receipts, verify, and hook
  - consecutive_clean_rounds REPLACES machine.py:423-425 (early-return made counter unreachable)
  - check #8 (progressive obligation) added then deleted: diligent reviewers always produce Jaccard distance 0.0 -- 100% false-positive rate; verify reduced to 7 checks
  - machine.py diff_files fix: write_receipts receives actual diff lines so clean-cycle receipts pass check #6 (coverage >=60%) without start:1-end:1 fallback
  - FORGE_CLEAN_ROUND_THRESHOLD=1 env var recovers single-round behavior (backward compat)
  - stub engine returns lambda:[] for l1_provider (engine-gating, no LLM calls in stub mode)
  - initial disposition UNCERTAIN (not CONFIRMED) per state protocol invariant
metrics:
  duration: multi-session
  tasks_completed: 7
  files_created: 9
  files_modified: 6
  tests_added: 29
  tests_total: 776
  commits: 22
completed_date: 2026-05-30
---

# Phase 04 Plan 01: Anti-Shirk Enforcement

**One-liner:** Connected `l1_provider` and `Falsifier` extension points with 3-LLM-pass mechanical cycle counting, per-pass receipt JSON protocol, 7-check `code-forge verify` subcommand, and pre-commit attestation.

## Tasks Completed

### Task 1: llm_invoke Subprocess Shim

Created `src/code_forge/llm_invoke.py` encapsulating `claude -p --model <model> --output-format json`:
- Timeout handling with structured `LLMInvokeError(exit_code, stderr, duration_s)`
- ARG_MAX guard: prompts >1MB written to temp file (`claude -p` reads `/dev/tty`, not stdin)
- Markdown fence stripping: LLMs wrap JSON in triple-backtick blocks despite JSON-only instruction
- `FORGE_LLM_MODEL` env var overrides default model (12-factor config)

### Task 2: RealFalsifier

Created `src/code_forge/falsify_real.py` with 10-step anti-hallucination verification protocol. Maps LLM `{"verdict": "CONFIRMED|DISMISSED|UNCERTAIN"}` to Disposition values. `LLMInvokeError` degrades to UNCERTAIN. Rejects FIXED verdict (ValueError, defense in depth per falsify.py invariant).

### Task 3: Consecutive-Clean Convergence

Replaced `machine.py:423-425` single-fixpoint early return with `consecutive_clean_rounds` counter:
- `state.py`: `consecutive_clean_rounds: int = 0` with full load/save serialization
- Machine requires N consecutive clean rounds (default 3, `FORGE_CLEAN_ROUND_THRESHOLD` env var)
- `FORGE_CLEAN_ROUND_THRESHOLD=1` recovers single-round behavior for backward compat

### Task 4: l1_provider Wiring

Added `build_l1_provider(engine, resolved)` to `factories.py`. `engine=stub` returns `lambda: []` (no LLM calls). `engine=real/auto` spawns 3 sequential passes: qodo, expert, adversarial. Finding ID prefix `l1-<pass_name>-<fp>` used by receipt writer to split by pass. Initial disposition `UNCERTAIN` per protocol.

### Task 4.5: Receipt Writer

Created `src/code_forge/receipt.py` producing 9 per-pass receipt JSON files per 3-cycle run (`receipt-c{cycle}p{pass}.json`). Fields: cycle, pass, skill, diff_sha256, timestamp, findings_count, findings, anchors, code_excerpts, covered_line_ranges. Clean-pass fallback uses actual `diff_files` changed lines (not start:1,end:1) to pass coverage check.

### Task 5: code-forge verify Subcommand

Created `src/code_forge/verify.py` with 7-check validation:
1. Completeness: 9 receipts, unique c*p matrix, findings_count matches
2. Diff hash: all receipts reference current `compute_source_hash()` output
3. Anchor reality: anchor files exist in diff
4. Timestamps: monotonically increasing
5. Excerpt verification: content byte-for-byte match; missing file = FAIL
6. Coverage quota: each cycle covers >= 60% of changed lines
7. Jaccard overlap: coverage Jaccard for cycles-with-findings < 0.8

Check #8 (progressive obligation) was added then deleted: all-clean reviewers covering
full diff always produce Jaccard distance 0.0 -- 100% false-positive rate. Deleted at 2c3acb2.

### Task 6: Pre-commit Attestation

Extended `install_hooks.py` generated hook with `code-forge verify --quiet` block.
On failure prints: "receipt verification failed. Run: code-forge verify"

### Task 7: SKILL.md Receipt Protocol

Added Receipt Protocol section (receipt JSON schema, 7 verification checks, compute_source_hash as canonical hash source, Path C editor instructions).

## Exit Criteria Verified

- [x] llm_invoke: subprocess, timeout, ARG_MAX guard, markdown fence strip, FORGE_LLM_MODEL
- [x] RealFalsifier: 10-step protocol, disposition mapping, FIXED rejection, graceful degradation
- [x] consecutive_clean_rounds replaces machine.py:423-425; FORGE_CLEAN_ROUND_THRESHOLD works
- [x] l1_provider wired; stub=lambda:[], real=3-pass LLM; initial disposition UNCERTAIN
- [x] Receipt writer: 9 files per run, diff_files coverage, clean-pass fallback correct
- [x] code-forge verify: 7 checks, PASS writes attestation, FAIL exits 1
- [x] Pre-commit hook includes code-forge verify --quiet
- [x] SKILL.md Receipt Protocol + Verification checks sections present
- [x] 29 new tests, 776 total green; forge 3-cycle review 9 passes zero findings

## Post-merge Fixes

- c38e586 / 2c3acb2: check #8 add-then-delete cycle; machine.py diff_files threading added
- 1f105ec: CLAUDE.md documents verify's anti-shirk ceiling (coverage claims not read-verified)
