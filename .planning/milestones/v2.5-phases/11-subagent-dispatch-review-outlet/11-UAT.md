---
status: complete
phase: 11-subagent-dispatch-review-outlet
source: [11-01-SUMMARY.md, 11-03-SUMMARY.md]
started: 2026-06-04T03:10:39.886509+00:00
updated: 2026-06-04T03:16:26.089436+00:00
---

## Current Test

number: 5
name: --baseline help text corrected
expected: |
  Run: code-forge resolve-outlet (with FORGE_OUTLET=subagent set)
  Should print "subagent" to stdout and exit 0.
awaiting: user response

## Tests

### 1. resolve-outlet accepts subagent value
expected: |
  FORGE_OUTLET=subagent code-forge resolve-outlet
  prints "subagent" to stdout, exit 0. No error about invalid outlet.
result: pass

### 2. --outlet subagent accepted by CLI parser
expected: |
  code-forge review --outlet subagent (on any repo with changes)
  CLI accepts the flag without "invalid choice" argparse error.
  Returns immediately (outlet B/C both return PASS early, SKILL.md drives).
result: pass
note: argparse accepted; hook fired after (worktree check), not argparse rejection

### 3. --model flag omitted by default (no-pin contract)
expected: |
  code-forge review (on any repo with FORGE_LLM_MODEL unset, outlet=cli)
  The subprocess call to claude does NOT include "--model" in its args.
  Verify via: FORGE_OUTLET=cli code-forge review --dry-run (or check logs).
  If no --dry-run, confirm by running and checking that your current session
  model handles the review (not a pinned sonnet-4-6).
result: pass

### 4. --whole-file PATH accepted by CLI
expected: |
  code-forge review --whole-file ~/code/kernel/networking/common/include.sh
  CLI accepts the flag, treats the file as a whole-file review (no "error:
  unrecognized arguments"). Returns with a SARIF result (even if empty).
result: pass

### 5. --baseline help text mentions empty works in git repos
expected: |
  code-forge review --help
  The --baseline line should say something like:
  "(HEAD/INDEX/<sha>/empty/<snapshot-path>; empty reviews whole file in any repo)"
  NOT the old text "(git: ...; non-git: empty|...)"
result: [pending]

## Summary

total: 5
passed: 5
issues: 0
pending: 0
skipped: 0

## Gaps

[none yet]
