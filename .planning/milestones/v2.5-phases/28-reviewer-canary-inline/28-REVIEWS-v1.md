---
phase: 28-reviewer-canary-inline
reviewed: 2026-06-24
reviewers:
  - deepseek (V4 Pro)
  - mimo-pro (v2.5-pro)
  - kimi (K2.7 Code)
  - gemini (3.1 Pro)
verdict: REQUEST_CHANGES (all 4 reviewers)
---

# Phase 28 Cross-AI Plan Review

## Reviewer Verdicts

| Reviewer | Verdict | Blockers | Warnings | Notes |
|----------|---------|----------|----------|-------|
| DeepSeek V4 Pro | REQUEST_CHANGES | 3 | 4 | 4 |
| MiMo v2.5 Pro | REQUEST_CHANGES | 3 | 10 | 3 |
| Kimi K2.7 Code | REQUEST_CHANGES | 4 | 10 | 3 |
| Gemini 3.1 Pro | REQUEST_CHANGES | 3 | 4 | 1 |

## Consensus Findings (found by 2+ reviewers)

### BLOCKER: Unhandled errors crash CLI instead of graceful degradation
- **Found by:** DeepSeek (B3), MiMo (B3), Kimi (B4), Gemini (B3)
- **Issue:** LLM errors (timeout, network, bad JSON) propagate as unhandled exceptions, crashing the CLI. D-28-03 requires SKIPPED with notice, never hard-fail.
- **Fix:** Wrap canary dispatch in try/except; on failure fall through to DELEGATED with stderr notice. Catch LLMInvokeError specifically; return empty findings for canary provider (triggers template fallback) and empty list for review provider (triggers UNRELIABLE).

### BLOCKER: CanaryProvider protocol signature undefined (hunk/context)
- **Found by:** DeepSeek (W1), Kimi (B2), Gemini (W1)
- **Issue:** CanaryProvider.__call__(self, hunk: str, context: str) but generate_canaries receives diff_text as single string. No procedure to extract hunk/context.
- **Fix:** Simplify to CanaryProvider.__call__(self, diff_text: str) -> list[dict]. Provider parses hunks internally.

### BLOCKER: gate.yaml n range (3..10) contradicts D-28-03 (3..5)
- **Found by:** DeepSeek (W2), Kimi (B3)
- **Issue:** validate_canary_config accepts n up to 10, but D-28-03 locks N=3..5.
- **Fix:** Cap validation range at 3..5.

### BLOCKER: Missing real-model smoke test (deliverable g)
- **Found by:** DeepSeek (B2), Kimi (W4), Gemini (B1)
- **Issue:** No plan covers the real-model smoke test required by CONTEXT.md sec 6g and acceptance criterion 5.
- **Fix:** Document as main-session acceptance task (not a plan deliverable), or add Plan 05/task.

### BLOCKER: No canary_provider wired in CLI (LLM generation bypassed)
- **Found by:** Gemini (B2)
- **Issue:** Plan 03 passes review_provider but omits canary_provider, so generate_canaries always falls back to templates.
- **Fix:** Implement _canary_provider closure in Plan 03 that invokes LLM with mutation prompt. Pass to run_inline_canary.

### WARNING: threshold_ratio: 0.0 crashes evaluate_canary_coverage
- **Found by:** Kimi (W6), Gemini (W3)
- **Issue:** ceil(0.0 * n) = 0, but M1 raises ValueError for threshold < 1.
- **Fix:** Clamp threshold to max(1, ceil(ratio * n)) or reject ratio <= 0.0 in validation.

### WARNING: git diff --cached ignores --whole-file and --mode
- **Found by:** DeepSeek (W3), Kimi (W2), Gemini (W2)
- **Issue:** Hardcoded git diff --cached is wrong for unstaged/whole-file review modes.
- **Fix:** Move diff computation inside canary opt-in block; honor args.mode.

### WARNING: validate_reviewer_json not called on fresh-context output
- **Found by:** Kimi (W1), Gemini (W4)
- **Issue:** dispatch_canary_review parses raw JSON without validating required keys.
- **Fix:** Pass findings through validate_reviewer_json before returning.

### WARNING: gate.schema.json not updated for canary: block
- **Found by:** Kimi (W3)
- **Issue:** Phase 24 made gate.schema.json authoritative. New canary: section needs schema entry.
- **Fix:** Add canary object schema to gate.schema.json in Plan 02.

## Solo Findings (unique to one reviewer)

### MiMo BLOCKER: cite reverification swallows canary findings
- **Issue:** reverify_finding_cites runs before evaluate_canary_coverage. Canary findings use synthetic filenames not in working tree, so all are marked unverified and dropped. Gate sees 0 caught -> always UNRELIABLE.
- **Fix:** Partition first, then cite-verify only real findings. Gate evaluates unfiltered findings.
- **Assessment:** VALID -- this is a real pipeline ordering bug. Must fix.

### MiMo BLOCKER: CONTEXT.md exit code map lists code 4 as available
- **Issue:** CONTEXT.md sec 9 says "Codes 4 and 7 are available" but EXIT_ESCALATED = 4.
- **Fix:** Already fixed in 28-CONTEXT.md update (sec 9 now correctly lists only 7). Plans already use 7.
- **Assessment:** RESOLVED -- context was updated this session.

### Kimi BLOCKER: Canary injection is synthetic appended file (D-28-02 violation)
- **Issue:** Plan 01 appends new unified-diff hunks with UUID-based filenames. This is structurally a synthetic appended file, violating D-28-02 (in-place semantic mutation).
- **Fix:** Parse existing diff hunks, mutate real changed lines in place. Manifest records existing file path + mutated line.
- **Assessment:** VALID but nuanced. Template fallback inherently uses synthetic patterns. LLM-backed generation should mutate real hunks. The plan's description is ambiguous -- executor should mutate real diff hunks, not append synthetic ones.

### DeepSeek BLOCKER: Path traversal in _source_lookup
- **Issue:** os.path.join(cwd, filepath) without containment check allows ../../../etc/passwd.
- **Fix:** Add os.path.realpath + commonpath containment check.
- **Assessment:** VALID -- defense-in-depth even though the reviewer would need to fabricate exact paths.

### Kimi WARNING: Template library not calibrated against spike subtlety findings
- **Issue:** Spike found some categories too trivial (hardcoded secret, bare except). Plan includes all 6 without filtering.
- **Fix:** Prefer medium-difficulty categories; add subtlety guard.
- **Assessment:** VALID for quality but not blocking -- templates are the fallback, not the primary generator.

### Kimi WARNING: Non-Python diffs have no canary mechanism
- **Issue:** ast.parse and template library are Python-only. Non-Python diffs get no canary.
- **Fix:** Return CanarySkip with notice for non-Python diffs.
- **Assessment:** VALID -- matches CONTEXT.md scope boundary "Python defects only for v1" but needs explicit skip logic.

## Action Items for Replanning

### Must-fix (Blockers, all reviewers agree)
1. Error handling: wrap canary dispatch in try/except, graceful degradation to DELEGATED
2. CanaryProvider signature: simplify to (diff_text: str) -> list[dict]
3. gate.yaml n range: cap at 3..5 (not 3..10)
4. Wire canary_provider in Plan 03 (not just review_provider)
5. Pipeline ordering: partition BEFORE cite-verify (MiMo finding)
6. Path traversal defense in _source_lookup
7. threshold_ratio: clamp to max(1, ...) or reject 0.0

### Should-fix (Warnings, consensus)
8. Diff computation: honor args.mode, not hardcoded git diff --cached
9. validate_reviewer_json on fresh-context output
10. gate.schema.json update for canary: block
11. Non-Python diff skip logic
12. Real-model smoke test: document as acceptance task

### Informational
13. Canary injection method: clarify in-place mutation vs synthetic append
14. Template subtlety calibration (future improvement)
15. Step-0 lint/non-ASCII in plan verification blocks
