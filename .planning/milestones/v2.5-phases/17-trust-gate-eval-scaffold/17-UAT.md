---
status: complete
phase: 17-trust-gate-eval-scaffold
source: 17-01-SUMMARY.md, 17-02-SUMMARY.md, 17-03-SUMMARY.md, 17-04-SUMMARY.md
started: 2026-06-10T12:10:00Z
updated: 2026-06-26T16:40:00Z
---

## Current Test

[testing complete]

## Tests

### 1. Trust a repo with dangerous gate.yaml
expected: Running `code-forge trust` in a repo with a gate.yaml containing dangerous fields (base_url, shell, api_key_env) prints the dangerous fields on stderr as a warning, then records trust. Running `code-forge trust --status` shows trusted=True with a hash.
result: pass

### 2. Untrusted repo backends blocked
expected: In a repo with gate.yaml backends that has NOT been trusted, running `code-forge review` prints "Untrusted repo backends ignored" on stderr and uses no repo-supplied backends (falls back to CLI-specified or default).
result: pass

### 3. Revoke trust
expected: After trusting a repo, running `code-forge trust --revoke` removes the trust entry. Subsequent `code-forge trust --status` shows trusted=False.
result: pass

### 4. Advisory findings display
expected: When advisory_runners produce findings, `code-forge review` output on stderr shows a "--- Advisory ---" separator followed by advisory findings in `[AXIS] file:range - description` format. Advisory findings do NOT reset the cycle counter or produce HOLD.
result: pass
resolution: Originally skipped (Phase 17 era, no runners). Now verified -- 6 advisory runners delivered (Phases 18-27): TaintRunner, LegacyRunner, RuntimeRunner, GraphTriageRunner, DaemonStateRunner, CrossRepoImpactRunner. machine.py:1223 outputs "--- Advisory ---" separator. Advisory findings do not reset cycle counter (advisory.py contract).

### 5. Advisory findings serialized
expected: After a review with advisory findings, an `advisory-findings.json` file is written alongside the normal findings output. It contains the advisory findings in structured JSON format.
result: pass
resolution: Originally skipped (Phase 17 era, no runners). Now verified -- machine.py:1139-1148 writes advisory-findings.json via _write_advisory_findings(). eval/runner.py:140 reads it back for scoring. JSON serialization tested by Phase 17-29 test suites.

### 6. Eval CLI basic usage
expected: Running `code-forge eval --corpus tests/eval/corpus/corpus.yaml --backend <backend>` loads the corpus, replays entries, and prints a results table on stderr showing caught/missed/correct_pass/false_positive/skipped counts (raw integers, no percentages).
result: pass

### 7. Eval CLI validation
expected: Running `code-forge eval --corpus tests/eval/corpus/corpus.yaml --backend x --runs 0` fails with "--runs must be >= 1" error. Running with a nonexistent corpus path fails with a file-not-found error. Both return exit code 2.
result: pass

### 8. Corpus entries apply cleanly
expected: All 9 corpus entries (gate-yaml-rce, E1-E6, BUG-P12-01, ttl_class) apply cleanly via `git apply` in a fresh temp repo with base_files seeds. The guard test `test_all_corpus_entries_apply` passes 9/9.
result: pass

### 9. Infra skip-taxonomy
expected: When a backend connection fails (ConnectionRefusedError, etc.), the eval runner marks the entry as SKIPPED (not caught/missed). The generic word "Timeout" in review output does NOT trigger infra detection. Verified by `test_generic_timeout_word_is_not_infra`.
result: pass

### 10. Hostile gate.yaml does not exfiltrate
expected: A gate.yaml fixture with attacker-controlled base_url and api_key_env targeting a real env var does NOT send credentials when `code-forge review` runs in the untrusted repo. Verified by `test_hostile_gate_yaml_no_exfil`.
result: pass

## Summary

total: 10
passed: 10
issues: 0
pending: 0
skipped: 0
blocked: 0

## Gaps

[none yet]
