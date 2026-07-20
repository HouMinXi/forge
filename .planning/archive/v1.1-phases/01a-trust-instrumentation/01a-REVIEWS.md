---
phase: 1a
reviewers: [deepseek, kimi]
reviewed_at: 2026-05-12T04:30:00Z
plans_reviewed: [01a-01-PLAN.md, 01a-02-PLAN.md, 01a-03-PLAN.md, 01a-04-PLAN.md]
---

# Cross-AI Plan Review -- Phase 1a

## DeepSeek Review

### Summary
Four plans collectively transform forge from a stateless pipeline into an observable system. Architecture is sound -- JSON persistence, Python stdlib CLI, embedded Python heredocs. However, plans have gaps in three areas: (1) integration gap between Plan 01 and 03 where hook writes a sidecar file that SKILL.md never reads; (2) reliability of finding data extraction via SKILL.md instructions alone without hook-enforced validation; (3) Plan 04's --dry-run breaks the zero-LLM-cost promise.

### Concerns

#### HIGH
1. **Integration gap: current_session.json not consumed.** Plan 03 Task 2 writes severity data to .forge/current_session.json. But Plan 01's SKILL.md modifications never mention this file. Hook writes data with no reader.
2. **SKILL.md inline finding data extraction is fragile.** Plan 01 instructs Claude to extract file, line, dimension, severity from each pass output using Python heredoc templates. Three passes produce fundamentally different output formats. No validation step confirms extracted values are valid. If Claude extracts a wrong dimension name, Phase 1b calibration uses corrupted data.
3. **--dry-run still invokes claude -p (incurs LLM cost).** Plan 04's run_forge() calls claude -p even with dry_run=True. Prompt is "Run Step 0 only" but the call still generates LLM token cost. Step 0 checks (bash -n, shellcheck, pylint, grep) are deterministic and don't need LLM. Requirement violation (CLI-01: "Step 0 only, zero LLM cost").

#### MEDIUM
4. **SKILL.md has no presentation mechanism for feedback collection.** Plan 01 says "present summary of all findings after pipeline completes" but SKILL.md has no mechanism for "present question to user." Under auto-continue (TRUST-06), pipeline flows silently. Feedback collection "pause" contradicts auto-continue.
5. **Hook only tracks qodo runs, not all three passes.** check_review_tracker.sh's _is_real_qodo() only detects qodo invocations. Severity-gated state machine applies to all three passes. Plan 03 adds _max_severity() but never extends detection to code-review-expert or adversarial-qe. A P0 finding in Pass 2 won't trigger hook-layer hard stop.
6. **Dashboard doesn't distinguish tool-error FP from user-preference FP.** D2's key insight is categories 1-4 = "tool wrong" vs 5-6 = "tool right, user won't act." Plan 04's show_stats() treats all rejections the same. For Phase 1b, tool-error FP rate is the more useful metric.
7. **Shared file modification ordering dependency.** Plans 01 (wave 1) and 03 (wave 2) both modify SKILL.md. Plan 03 should reference insertion points by content anchors (grep specific headings), not line numbers.
8. **claude -p large output risk.** Plan 04's subprocess.run uses capture_output=True. A full 9-pass review could produce very large JSON output. Consider streaming or max output size.

#### LOW
9. **Historical analysis contains non-FP entries.** Cases 9-11 are FN (false negatives), Case 12 is a code bug, Case 13 is a process failure. Bootstrap script must filter these.
10. **Plan 01 adds ~200 lines to already 418-line file.** LLM instruction following degrades with length/complexity (RESEARCH.md Pitfall 2).
11. **Bootstrap mixed classifications should be split.** Case 1's "Mix of CONTEXT_MISSING (3) and HALLUCINATION (2)" should generate 5 separate finding records.
12. **Classification mode has no --non-interactive flag.** input() loop will hang in CI.

### Risk Assessment: MEDIUM

---

## Kimi Review

### Summary
Plans are generally well-structured with clear acceptance criteria and good use of atomic write patterns. However, high-severity risks around LLM instruction following, CLI reliability, and data race conditions need attention before execution.

### Concerns

#### HIGH
1. **--append-system-prompt-file reliability.** GitHub issue #38505 reports hanging. Plan has 600s timeout but no fallback strategy. If this flag hangs reproducibly, CLI wrapper is dead on arrival.
2. **Race condition between SKILL.md and CLI wrapper writes to findings.json.** During claude -p invocation, SKILL.md (inside Claude) writes individual findings to findings.json. After claude -p completes, CLI wrapper writes run_record to the same file. Two uncoordinated read-modify-write processes on the same file will eventually cause lost updates or corruption.
3. **LLM instruction following degradation.** SKILL.md grows from ~417 to ~600+ lines. RESEARCH.md Pitfall 2 warns about this explicitly. A 43% increase for a state machine that must be followed precisely is risky.

#### MEDIUM
4. **commit_sha extraction broken in heredoc.** Shell command substitution $(git rev-parse ...) does not execute inside a quoted heredoc << 'PYEOF'. Literal string will be stored instead of actual SHA.
5. **Chinese character encoding ambiguity in hook.** Plan presents Chinese strings as byte escapes but says executor should verify. Risk of inconsistent encoding within same file.
6. **Silent sidecar write failure.** .forge/current_session.json write is wrapped in try/except pass. If write fails, nobody is notified, state machine behaves incorrectly without explanation.
7. **Unbounded FUSE-01 context size.** If Step 0 finds 50+ lint errors, the markdown table could become very large. No truncation or size limit mentioned.
8. **Hardcoded total_passes.** Run record sets total_passes: 9 but severity-gated reset may cause more than 9 passes.
9. **Broken cost_usd truthiness check.** cost_usd of 0.0 (legitimate for cached run) evaluates as falsy. Should use `is not None`.

#### LOW
10. **Historical data file in /tmp may not survive across sessions.**
11. **No --model override flag in CLI.**
12. **Missing diff_files and diff_lines population in run record.**

### Cross-Cutting Issues
1. **findings.json schema versioning** -- no migration logic defined
2. **Worktree requirement** -- CLAUDE.md requires worktree but no plan mentions it
3. **Three-cycle review gate** -- plans don't include review of their own changes
4. **"Same output" ambiguity** in success criterion 4

### Risk Assessment: HIGH
