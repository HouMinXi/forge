# Phase 1b: Trust Calibration - Cross-AI Reviews

**Date:** 2026-05-12
**Reviewers:** DeepSeek (deepseek-v4-pro), Kimi

## Summary

| Plan | DeepSeek Risk | Kimi Risk | HIGH | MEDIUM | LOW |
|------|---------------|-----------|------|--------|-----|
| 01b-01 (Confidence) | MEDIUM | MEDIUM | 1 | 4 | 2 |
| 01b-02 (Tier) | MEDIUM | MEDIUM | 1 | 3 | 2 |
| 01b-03 (Evaluation) | LOW | HIGH | 0/2 | 3 | 2 |
| Cross-plan | - | - | 1 | 3 | 1 |

## HIGH Severity Findings (must fix)

### H1: backfill_confidence() never automatically called [DS+Kimi consensus]
- **Plans affected:** 01b-01, 01b-02
- **Issue:** backfill_confidence() is defined but never wired into run_forge(). Confidence scores remain 0.0 in findings.json forever.
- **Fix:** Add backfill_confidence() + atomic_write call at end of run_forge() in Plan 02.

### H2: Markdown comment regex invalid Python syntax [DS only]
- **Plan:** 01b-02
- **Issue:** `'^\s*<!--' or '^\s*-->' or '^\s*$'` is not valid regex. Needs `r'^\s*(?:<!--|-->)'`.
- **Fix:** Correct the regex in Plan 02 Task 1 _detect_change_type.

### H3: INTENTIONAL incorrectly classified as adjust_scope [Kimi only]
- **Plan:** 01b-03
- **Issue:** D3 lists INTENTIONAL in categories 1-4 (tool-wrong), should trigger improve_detection, not adjust_scope.
- **Fix:** Add INTENTIONAL to improve_detection condition in Plan 03 Task 1.

### H4: Missing ROADMAP update task per D5 [Kimi only]
- **Plan:** 01b-03
- **Issue:** D5 requires updating ROADMAP success criteria from 50% to 10%. No task covers this.
- **Fix:** Add sub-task to Plan 03 to update ROADMAP.md.

## MEDIUM Severity Findings (should fix)

### M1: pass_agreement always 1.0, signal non-discriminative [DS]
- **Fix:** Document that pass_agreement requires cross-finding dedup (matching file+line across passes) to compute. Add computation logic to backfill_confidence.

### M2: Light tier prompt reveals Step 4 is being skipped [DS]
- **Fix:** Remove "Skip Step 4" from light prompt. Just say what to do, not what's being skipped.

### M3: _detect_ai_generated searches diff, misses commit message markers [DS+Kimi]
- **Fix:** Also search git log --format=%B for AI markers. Conservative: if uncertain, route to full.

### M4: llm_self_report hardcoded 0.8, no collection mechanism [DS+Kimi]
- **Fix:** Add LLM instruction in SKILL.md to set llm_self_report. Kimi: "Set llm_self_report to your confidence (0.0-1.0)."

### M5: total_findings should be per-dimension, not global [Kimi]
- **Fix:** Change compute_confidence to use per-dimension finding count for stage determination.

### M6: SKILL.md doesn't instruct LLM to fill new fields [Kimi]
- **Fix:** Add instructions in SKILL.md Finding Persistence section for evidence_count and llm_self_report.

### M7: _count_diff_lines locale-dependent, use --numstat instead [DS+Kimi]
- **Fix:** Replace git diff --stat parsing with git diff --numstat (fixed format, no locale dependency).

### M8: No end-to-end test across plans [DS]
- **Fix:** Add integration verification step noting the 3-plan dependency chain.

## LOW Severity Findings (noted)

- Stage boundary tests (99/100, 299/300) not tested
- No backward-compat test for old findings
- evaluate_dimensions missing TOTAL row
- show_stats JSON output extension not specified
- Confidence label display not specified in any output path

## Full Reviews

See: /tmp/forge-1b-ds-review.txt (DeepSeek)
See: /tmp/forge-1b-kimi-review.txt (Kimi)
