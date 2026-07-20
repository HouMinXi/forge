# Phase 03 Code Review - Cycle 1, Pass 1 (qodo-review)

**Date:** 2026-05-14
**Files reviewed:** 15 (7864 lines)
**Pass type:** qodo-review (correctness, security, error handling, API contracts, data validation)

## Summary

| Severity | Count |
|----------|-------|
| BLOCKER/CRITICAL | 6 |
| HIGH | 20 |
| MEDIUM | 33 |
| LOW | 29 |
| **Total** | **88** |

## BLOCKER / CRITICAL Findings

### B1. Subprocess command injection in github_pr adapter
**File:** cli/adapters/github_pr.py:104
**Issue:** `endpoint` constructed from user-controlled `source_ref` without validation. Malicious PR reference could inject flags.
**Fix:** Validate `repo` format with `^[\w\-\.]+/[\w\-\.]+$` and `pr_num.isdigit()` before constructing endpoint.

### B2. Subprocess injection in git_log adapter
**File:** cli/adapters/git_log.py:71,104,140
**Issue:** `source_ref` and `sha` passed to subprocess without format validation. While list-form subprocess mitigates shell injection, git flag injection remains.
**Fix:** Validate branch names with `^[\w\-/\.]+$` and SHAs with `^[0-9a-f]{7,40}$`.

### B3. ValueError crash in classify_finding on empty dim_counts
**File:** cli/gap_detector.py:221
**Issue:** `max(dim_counts.values())` crashes with ValueError when active_dims exist but all have empty keyword lists.
**Fix:** Add `if not dim_counts: return ('outcome_3', None)` before max() call.

### B4. Circular import deadlock risk (architectural)
**File:** cli/forge_cli.py:116, cli/migration.py:116
**Issue:** `atomic_write()` in forge_cli.py imported by migration.py, which is imported by modules that forge_cli.py lazy-imports. Chain: forge_cli -> migration -> forge_cli.
**Fix:** Extract `atomic_write()` to `cli/file_utils.py`.

### B5. Subprocess injection via diff_spec in forge_cli
**File:** cli/forge_cli.py:350,388,559,1607
**Issue:** User-supplied `diff_spec` passed to subprocess without validation. `--exec=evil` flag injection possible.
**Fix:** Validate diff_spec: reject specs starting with `-` or containing shell metacharacters.

### B6. Seed test writes to live config.json
**File:** tests/seed_tests/run_seed_tests.py:376-430
**Issue:** `_write_seed_test_status()` writes directly to production `cli/config.json`, breaking test isolation.
**Fix:** Add config_path parameter injection, use temp file in test mode.

## HIGH Findings

### H1. Missing error handling for Anthropic API exceptions
**File:** cli/llm_parser.py:188-199
**Issue:** Broad `except Exception` catches KeyboardInterrupt/SystemExit. `response.content[0].text` can IndexError on empty content.
**Fix:** Catch specific `anthropic.APIError`, check `response.content` before indexing.

### H2. Type inconsistency in BaseAdapter.fetch return
**File:** cli/adapters/base.py:88-90
**Issue:** Contract says "never None" but no enforcement. Subclasses can violate.
**Fix:** Add wrapper method enforcing List return, subclasses override `_fetch_impl()`.

### H3. Cross-file API contract: context dict schema undefined
**File:** cli/llm_parser.py:243-247
**Issue:** `extract_findings` uses `.get('path')` or `.get('file')` but adapters use inconsistent keys.
**Fix:** Document exact context dict schema in CanonicalFinding docstring.

### H4. Unchecked file encoding in CI log adapter
**File:** cli/adapters/ci_log.py:40
**Issue:** Hardcoded UTF-8 may fail on CI logs with other encodings.
**Fix:** Try UTF-8, fallback to Latin-1, then binary with errors='replace'.

### H5. Race condition in concurrent gap management
**File:** cli/gap_manager.py:683-747
**Issue:** Load-edit-save over minutes-long interactive sessions. Last-writer-wins on concurrent `forge --gaps`.
**Fix:** Add file-based lock or optimistic locking with version counter.

### H6. Silent timestamp dedup failure
**File:** cli/gap_detector.py:124-126
**Issue:** Malformed timestamps cause silent dedup skip with zero audit trail.
**Fix:** Log warning on timestamp parse failure.

### H7. Code duplication: three identical JSON loader functions
**File:** cli/gap_detector.py:28-64
**Issue:** `load_external_findings()`, `load_gap_candidates()`, `load_keyword_expansion_queue()` are structurally identical.
**Fix:** Extract to `_load_json_file(filepath, default_structure)`.

### H8. Code duplication: interactive choice handling
**File:** cli/gap_manager.py:202-290, 540-675
**Issue:** Two interactive review loops with near-identical structure.
**Fix:** Extract to `_get_user_choice(prompt, valid_choices)`.

### H9. Unused loop variables expose incomplete alerting
**File:** cli/forge_cli.py:2664
**Issue:** `val` and `thresh` unused in escalation alert display. Alert says what but not the actual metric value.
**Fix:** Either display all values or explicitly ignore with `_`.

### H10. Broad exception catches hide real errors
**File:** cli/forge_cli.py:2676, 2740
**Issue:** `except Exception: pass` silences all exceptions including programming errors.
**Fix:** Catch specific `(ImportError, FileNotFoundError, json.JSONDecodeError)`.

### H11. Race condition in atomic_write
**File:** cli/forge_cli.py:136-154
**Issue:** TOCTOU between makedirs and mkstemp. Also `tmp` may not be defined in exception handler.
**Fix:** Initialize `tmp = None` before try, set to None after successful rename.

### H12. Migration idempotency check is insufficient
**File:** cli/migration.py:43-44
**Issue:** Checks in-memory dict not on-disk state. Stale config dict causes silent skip.
**Fix:** Check on-disk config state or document single-process assumption.

### H13. Seed test creates git config pollution risk
**File:** tests/seed_tests/run_seed_tests.py:234-240
**Issue:** `git config user.email` in temp dir is one typo away from global config pollution.
**Fix:** Use `git -c user.email=test@test.com commit` instead.

### H14. Seed test diff parser has zero unit tests
**File:** tests/seed_tests/run_seed_tests.py:139-201
**Issue:** `_parse_after_state()` implements complex diff parsing with no tests.
**Fix:** Extract and add unit tests.

### H15. /tmp fallback path in bootstrap is world-writable
**File:** bootstrap/convert_historical.py:24-28
**Issue:** Predictable filename in /tmp. Symlink attack vector.
**Fix:** Remove /tmp fallback, use `~/.cache/forge/`.

### H16. Missing None/text handling test for classify_finding
**File:** tests/test_phase3.py:27-105
**Issue:** No test for `finding['text']` being None. Production crashes on malformed input.
**Fix:** Add test, then decide on graceful handling.

### H17. Cross-source dedup 7-day boundary not tested
**File:** tests/test_phase3.py:126-169
**Issue:** Tests cover 2-day and 10-day but not exact 7-day boundary. Contract undefined.
**Fix:** Add boundary test at exactly 168 hours.

### H18. Duplicate config loading
**File:** cli/forge_cli.py:1877, 1989
**Issue:** `load_config()` called twice in `run_forge()`. Wasteful and could load different data.
**Fix:** Reuse config from first call.

### H19. Hardcoded /tmp fallback in bootstrap
**File:** bootstrap/convert_historical.py:24-28
**Issue:** Same as H15 (duplicate finding from different perspective).

### H20. Keyword list duplication between bootstrap and migration
**File:** bootstrap/convert_historical.py:205-233
**Issue:** `map_dimension` keywords hardcoded, duplicating `migration.SEED_KEYWORD_DICTIONARIES`.
**Fix:** Import from canonical source.

## MEDIUM Findings (33 total)

M1. Inconsistent truncation limits across adapters (ci_log vs git_log)
M2. Missing validation for required CanonicalFinding fields
M3. Potential unbounded loop in _search_log
M4. Regex DoS risk in _parse_json_response (bounded by token limit)
M5. Inconsistent timeout values across subprocess calls
M6. Missing candidate_ids list type validation from LLM
M7. Unbounded LLM prompt construction (10K+ candidates)
M8. State mutation without validation in reclassify
M9. Missing dedup check in keyword expansion rejection
M10. No bounds check warning on finding_count decrement
M11. main() function complexity (39 branches, 142 statements)
M12. Config validation missing (required keys not checked)
M13. Findings data structure not validated before use
M14. Migration rename map one-way only (no reverse validation)
M15. sys.path manipulation creates test-order dependency
M16. Test coverage gap: dedup with corrupted timestamp
M17. Assertion anti-pattern: assertTrue for equality
M18. Incomplete error message in security alert
M19. UUID collision risk in bootstrap (theoretical)
M20. Non-FP pattern matching could skip valid FPs
M21. Case block parsing assumes strict format
M22. Timeout 300s arbitrary and undocumented
M23. Partial match fallback in seed tests has no tests
M24-M27. dimension_manager: large functions (run_propose 257L, _run_pr_pipeline 186L), missing state transitions, TOCTOU in final_dir removal, no LLM retry, empty except swallows import errors
M28-M33. Various minor data validation and error handling gaps

## LOW Findings (29 total)

L1-L29. Magic numbers, missing type hints, inconsistent error messages, unused imports, variable shadowing, print statements in library code, etc.
