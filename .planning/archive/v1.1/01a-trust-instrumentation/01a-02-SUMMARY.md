---
phase: 01a-trust-instrumentation
plan: 02
subsystem: bootstrap/infrastructure
tags: [gitignore, pricing-config, historical-data, bootstrap]
dependency_graph:
  requires: []
  provides: [".forge/ gitignored", "cli/config.json pricing", "bootstrap/convert_historical.py", ".planning/research/historical_review_analysis.txt"]
  affects: [".gitignore", ".forge/findings.json"]
tech_stack:
  added: []
  patterns: ["atomic JSON write (tempfile.mkstemp + os.replace)", "case-block text parser with regex"]
key_files:
  created:
    - cli/config.json
    - bootstrap/convert_historical.py
    - .planning/research/historical_review_analysis.txt
  modified:
    - .gitignore
decisions:
  - "Historical data produces 18 findings (not 15 as source file summary claims) -- source summary had arithmetic errors in category totals"
  - "Instance count regex uses open-paren match without requiring close-paren to handle inline explanations in parenthetical"
metrics:
  duration_seconds: 396
  completed: "2026-05-12T04:51:23Z"
  tasks_completed: 2
  tasks_total: 2
  files_created: 3
  files_modified: 1
---

# Phase 1a Plan 02: Infrastructure Summary

Gitignore for .forge/, model pricing config, historical FP bootstrap with filtering and mixed-classification splitting.

## Tasks Completed

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | Add .forge/ to .gitignore, create model pricing config, persist historical data | 63ede0e | .gitignore, cli/config.json, .planning/research/historical_review_analysis.txt |
| 2 | Create historical FP data bootstrap script | 3d81a55 | bootstrap/convert_historical.py |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed instance count regex in split_mixed_classification**
- **Found during:** Task 2
- **Issue:** Regex `\((\d+)\s+instances?\)` required closing paren immediately after "instances", but historical data has patterns like "(3 instances -- Gemini invented problems...)" where explanation text precedes the closing paren.
- **Fix:** Changed to `\((\d+)\s+instances?` (no closing paren required). This correctly parses all 3 instance-count patterns in the source data.
- **Files modified:** bootstrap/convert_historical.py
- **Commit:** 3d81a55

### Scope Notes

- The source file claims "TOTAL FALSE POSITIVES CLASSIFIED: 15 instances" with category totals summing to 17, but parsing the actual case data produces 18 individual finding records. The discrepancy is in the source file's summary (Cases 1 split=5, Case 2=1, Case 3=1, Case 4=2, Case 5=4, Case 6=3, Case 7=1, Case 8=1 = 18). The script correctly parses the case data rather than relying on the summary.

## Verification Results

- .gitignore contains ".forge/" entry and retains all 4 original entries
- cli/config.json is valid JSON with correct pricing structure (2 models, 4 keys each, default_model set)
- .planning/research/historical_review_analysis.txt persisted (686 lines)
- bootstrap/convert_historical.py passes py_compile syntax check
- All 8 required functions present
- NON_FP_PATTERNS filters out Cases 9-13 (FN, code bug, process failure)
- split_mixed_classification correctly handles "Mix of X (N) and Y (M)" pattern
- End-to-end run produces 18 findings with valid D1 schema
- All findings have outcome=rejected, commit_sha=historical, line=-1
- All reject_reasons are from the valid 6-category set
- No non-ASCII characters in any created/modified file
- No external dependencies (stdlib only)

## Known Stubs

None -- all functions are fully implemented.

## Self-Check: PASSED

All 4 created/modified files exist in worktree. Both task commits (63ede0e, 3d81a55) verified in git log.
