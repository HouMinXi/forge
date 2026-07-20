---
status: complete
phase: 32-per-change-intent-contract
source: [32-CONTEXT.md, git diff main~3...main]
started: "2026-06-29T00:30:00Z"
updated: "2026-06-29T00:35:00Z"
---

## Current Test

[testing complete]

## Tests

### 1. --contract flag visible in help
expected: `code-forge review --help` includes --contract FILE option with stdin and invariant guidance
result: pass

### 2. Init generates contract template
expected: `code-forge init` in a fresh .code-forge/ dir creates contract-template.md with Invariants/Residual Risks/Change Scope sections
result: pass

### 3. Missing file error
expected: `code-forge review --contract /tmp/nonexistent.md` exits 2 with "contract file not found"
result: pass

### 4. Empty file error
expected: `code-forge review --contract /dev/null` exits 2 with "contract file is empty"
result: pass

### 5. Oversized file error
expected: A >64KB contract file triggers exit 2 with "exceeds 64KB limit"
result: pass

### 6. Automated test suite green
expected: `python3 -m pytest tests/test_contract_flag.py` passes all 29 tests (guards + merge + directive)
result: pass

## Summary

total: 6
passed: 6
issues: 0
pending: 0
skipped: 0

## Gaps

[none]
