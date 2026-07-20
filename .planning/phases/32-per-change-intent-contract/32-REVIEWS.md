---
phase: 32
reviewers: [kimi, deepseek]
reviewed_at: "2026-06-28T13:55:00Z"
plans_reviewed: []
context_reviewed: [32-CONTEXT.md]
---

# Cross-AI Context Review -- Phase 32

## Kimi Review

### Summary
Decisions are directionally correct and success criteria are largely covered,
but 4 gaps would cause downstream planning/implementation agents to get stuck
or make wrong choices. Overall risk: MEDIUM.

### Strengths
- Reuse of existing contract_spec slot is well-grounded (cli.py:614-617,
  factories.py:283-286)
- Merge strategy D-32-02 gives explicit ordering and separator
- Fail-closed D-32-06 is consistent with CliError and ADOPT-04
- Test boundary D-32-08 is exhaustive
- stdin support and init template are good UX additions

### Concerns
- **HIGH**: No encoding/decoding error handling specified. `read_text()` throws
  `UnicodeDecodeError` on non-UTF-8 files; D-32-06's "unreadable -> CliError"
  promise doesn't auto-cover this.
- **HIGH**: Summarization failure behavior (>4KB path) unspecified. What happens
  when LLM summarization fails/times out/backend unavailable?
- **MEDIUM**: Confirmation bias directive wording not locked -- planner can't
  write testable assertions.
- **MEDIUM**: `code-forge init` template overwrite strategy unspecified (--force?
  skip-if-exists?).
- **MEDIUM**: Inline outlet (`return Verdict.PASS`) doesn't consume contract_spec
  -- D-32-09 "both Outlets" claim may mislead.
- **LOW**: 4KB summarization threshold lacks justification.
- **LOW**: Canonical ref line numbers may have drifted.

### Suggestions
- Lock directive wording in D-32-03.
- Use `read_text(encoding="utf-8", errors="replace")` + catch OSError/ValueError.
- Specify summarization failure mode: fallback to raw injection or hard-fail.
- Specify init template existence strategy.
- Note inline outlet limitation in D-32-09.

### Risk: MEDIUM

---

## DeepSeek Review

### Summary
Methodical and covers major decision dimensions. Correctly identifies
contract_spec is already wired. Contains one factual error (exit code), one
hollow test design (SC2 sleep), and several specification gaps. Close to
plan-ready but needs three corrections.

### Strengths
- "Reuse, not rebuild" correctly maps existing injection points.
- D-32-09: zero new injection points needed -- both outlets consume contract_spec.
- D-32-03 against keyword scanning is sound (FP on "safe against SQL injection").
- D-32-04 dual-layer verification follows Golden Rule 2 pattern.
- D-32-05 binary detection and 64KB limit appropriate for per-change documents.
- D-32-08 test boundary enumeration is exhaustive.

### Concerns
- **HIGH**: D-32-06 says "exit 1" but CliError actually produces EXIT_CLI_ERROR=2.
  Parser epilog at cli.py:189 documents "2 = CLI_ERROR". VERIFIED: exit_codes.py:15
  confirms `EXIT_CLI_ERROR = 2`.
- **HIGH**: SC2 bug-inject test with `sleep()` is hollow -- any competent reviewer
  flags sleep() with or without a contract. The test proves the reviewer is awake,
  not that the CONTRACT guided the detection. Need a semantically-contract-dependent
  violation (e.g., contract says "preserve ordering", plant a sort).
- **MEDIUM**: Summarization failure modes unspecified (same as Kimi finding).
- **MEDIUM**: Confirmation bias directive placement ambiguous -- append to merged
  contract_spec in _run() (single source of truth), not at individual injection
  sites.
- **MEDIUM**: stdin empty-input behavior unspecified. `sys.stdin.read()` blocks
  forever with no piped input; empty pipe should be CliError.
- **LOW**: D-32-01 "injected directly" tension with D-32-10 summarization.
- **LOW**: NUL byte detection mechanism unstated (read_text() UnicodeDecodeError
  vs pre-check bytes).
- **LOW**: Missing relative-path resolution spec (trivial, pathlib handles it).

### Suggestions
- Fix "exit 1" to "exit 2 (EXIT_CLI_ERROR)" in D-32-06 and canonical refs.
- Redesign SC2 test: contract-dependent violation (e.g., "must preserve ordering"
  + planted sort), not universally-obvious sleep().
- Add D-32-15: summarization failure -> fallback to raw injection up to 64KB.
- Specify directive placement: append to merged contract_spec AFTER D-32-02
  concatenation, not at injection sites.
- stdin `-` with empty bytes -> CliError (same as empty file).

### Risk: MEDIUM

---

## Consensus Summary

### Agreed Strengths
- **Reuse of existing contract_spec slot** is well-grounded and correctly mapped
  (both reviewers verified the injection points independently)
- **D-32-03 prompt-only bias protection** is the correct fix for the arXiv finding
  (both agreed keyword scanning has high FP rate)
- **D-32-08 test boundary enumeration** is exhaustive (both confirmed)
- **Phase scope is genuinely small** and well-contained

### Agreed Concerns
1. **Summarization failure behavior unspecified** (Kimi HIGH, DeepSeek MEDIUM)
   -- Both flagged that D-32-10/D-32-12 don't specify what happens when LLM
   summarization fails. Resolution: add fallback to raw injection.
2. **Confirmation bias directive needs specificity** (Kimi MEDIUM, DeepSeek MEDIUM)
   -- Kimi: wording not locked. DeepSeek: placement ambiguous. Both need fixing.
3. **Encoding error handling** (Kimi HIGH) overlaps with **NUL byte detection**
   (DeepSeek LOW) -- both are about what happens when read_text() encounters
   non-UTF-8 content.

### Unique Findings (one reviewer only)
- **EXIT CODE WRONG** (DeepSeek HIGH, VERIFIED): CliError -> exit 2, not exit 1.
  This is a factual error that would produce incorrect implementation.
- **SC2 BUG-INJECT TEST IS HOLLOW** (DeepSeek HIGH): sleep() is universally
  obvious, not contract-dependent. Needs redesign.
- **Inline outlet gap** (Kimi MEDIUM): D-32-09 claims "both Outlets" but inline
  outlet (`return Verdict.PASS`) never consumes contract_spec.
- **Init template overwrite strategy** (Kimi MEDIUM): unspecified.
- **Stdin empty-input edge case** (DeepSeek MEDIUM): unspecified.

### Divergent Views
None -- reviewers agreed on direction for all decisions. Differences were in
severity grading (e.g., summarization failure: HIGH vs MEDIUM), not in
whether an issue exists.

---

# External AI Plan Review (4 models, 2026-06-28)

## Reviewers: Gemini, MiniMax, DeepSeek, MiMo

### Consensus Concerns (2+ models)

1. **BLOCKER: Backend timing** — `_load_contract_file(backend=backend)` placed
   at ~line 1339, but backend resolved at ~line 1484. NameError at runtime.
   (ds BLOCKER, mimo HIGH, mm HIGH)
   FIX: Move call after backend resolution, or handle backend=None by skipping
   summarization (D-32-15 fallback).

2. **BLOCKER: Exception scope diverges from CONTEXT D-32-06** — Plan catches
   `(OSError, UnicodeDecodeError)` but D-32-06 says `(OSError, ValueError)`.
   ValueError covers closed-stdin case (`ValueError("I/O operation on closed file")`).
   (mimo BLOCKER, mm HIGH)
   FIX: Revert to `(OSError, ValueError)` per locked CONTEXT decision.

3. **HIGH: Outlet A/B naming confusion** — Plan says "Outlet A" for subprocess
   path, but CONTEXT.md uses "Outlet B". Code has no "Outlet A" label.
   (ds HIGH, mimo HIGH, mm MEDIUM)
   FIX: Use CONTEXT.md terminology throughout.

4. **HIGH: Merge logic duplication** — merge + directive append copy-pasted at
   both outlet sites. Golden Rule #4 violation.
   (ds HIGH)
   FIX: Extract `_merge_contract_spec()` helper.

5. **HIGH: Test only covers Outlet C** — Mock captures `_make_subagent_spawn`
   but not `build_l1_provider`. 50% coverage gap.
   (ds HIGH, mm HIGH)
   FIX: Add Outlet B test via `build_l1_provider` mock.

6. **MEDIUM: SC2 label mismatch** — Automated test proves SC1 (injection), but
   acceptance criteria label it "SC2".
   (mimo MEDIUM)
   FIX: Relabel to SC1; document SC2 as manual smoke.

### Per-Model Risk Ratings
- Gemini: LOW (no architectural concerns)
- MiniMax: MEDIUM (fixable mechanical issues)
- DeepSeek: HIGH (BLOCKER blocks execution)
- MiMo: MEDIUM (BLOCKER is decision inconsistency, not design flaw)
