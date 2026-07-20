# Phase 6: Outlet B Inline Merge - Research

**Date:** 2026-06-01
**Confidence:** HIGH
**External research needed:** None (local skill files only)

## Key Findings

### 1. Load Directive Pattern (Proven)

Code-review-expert already uses `Load references/*.md` pattern:
- SKILL.md line 42: `Load references/solid-checklist.md for specific prompts.`
- SKILL.md line 50: `Load references/removal-plan.md for template.`
- SKILL.md line 63: `Load references/security-checklist.md for coverage.`
- SKILL.md line 76: `Load references/code-quality-checklist.md for coverage.`

Mechanism: "Load" tells the AI assistant to Read the file and follow its instructions. No sub-skill session. No Invoke. No hang risk.

### 2. Source Skill Content Extraction

| Source | Lines | Content to Extract | Adaptation Needed |
|--------|-------|--------------------|--------------------|
| qodo-review/SKILL.md | 135 | Full content (skip frontmatter ~4 lines) | Remove "When to Use"/"When NOT to Use" (redundant with main SKILL.md). Add BOTH-01 instruction. Normalize severity to P0-P3. |
| code-review-expert/SKILL.md | 164 | Full content (skip frontmatter ~6 lines) | Remove "Overview" meta. Add BOTH-01 instruction. Already uses P0-P3 natively. |
| adversarial-qe/SKILL.md | 224 | Full content (skip frontmatter ~4 lines) | Remove "Posting review comments" / "Policy reminder" / "Relationship to other skills" sections (not applicable inline). Add BOTH-01 instruction. Map Critical/High/Medium/Low/Nit to P0-P3. |
| kernel-fp-verify/SKILL.md | ~40 | 10-step verification protocol + dismissal reasons | Already self-contained. Inline directly into Step 3.5 section. |
| smoke-test/SKILL.md | ~60 | Coverage matrix + test runners + footguns | Already self-contained. Inline directly into Step 4 section. |
| anti-ai-audit/SKILL.md | TBD | Non-ASCII + AI smell + plan-ref detection | New pipeline position: after 3x3, before Step 3.5. |

### 3. Heading Conflicts (T6 Verified)

5 conflicts identified when merging:
1. `## Arguments` (main + qodo)
2. `## When to Use` (main + qodo)
3. `## Output format` (expert + adversarial)
4. `## Workflow` (expert + Step 4)
5. `## Resources` (expert, generic name)

Resolution: Separate files per pass (D-01) eliminates all 5 conflicts. Each file has its own heading namespace.

### 4. Directory Structure (D-03)

```
~/.claude/skills/code-forge/
  SKILL.md (~900 lines, main pipeline + Load directives)
  passes/
    pass1-qodo.md (~131 lines)
    pass2-expert.md (~158 lines)
    pass3-adversarial.md (~220 lines)
  references/
    solid-checklist.md (67 lines)
    security-checklist.md (119 lines)
    code-quality-checklist.md (131 lines)
    removal-plan.md (53 lines)
```

### 5. Pipeline Position for Anti-AI Audit (D-13)

```
Step 0 -> Steps 1-3 (3x3) -> anti-ai-audit -> Step 3.5 -> Step 4 -> Commit
```

- Runs ONCE after 3 consecutive clean cycles
- Finding -> fix -> re-run anti-ai-audit only (D-14: does NOT reset 3x3 counter)
- Clean -> proceed to Step 3.5

### 6. Outlet Branch Point (D-09)

Insert in Execution Protocol between current lines ~823-825:
```
4. Resolve outlet (code-forge resolve-outlet)
5. If "inline": Load and execute each pass from passes/ (Outlet B)
   If "cli": [Phase 7 placeholder]
```

### 7. Size Budget (T1 Verified)

| Scenario | Lines | Tokens |
|----------|-------|--------|
| Current | 852 | ~12,780 |
| B: Inline all 5 Invokes (chosen) | ~1,457 | ~21,855 |

Under 2K threshold. Acceptable.

## Pitfalls to Watch

1. **D-11 Hard Constraint:** Do NOT modify standalone pass skills. passes/ files are COPIES.
2. **Severity mapping:** qodo uses R/Y/G, adversarial uses Critical..Nit. Both must normalize to P0-P3. The mapping table at SKILL.md:300-310 already exists -- reuse.
3. **step N semantics (D-07):** step N = pipeline stage, NOT individual pass. No per-pass selection.
4. **FUSE-01 threading:** Step 0 context fusion block must be passed to each inline pass via the Load directive's context (mention in each pass file's preamble).
5. **agents/agent.yaml:** NOT needed in the merge (standalone discovery artifact).

## Ready for Planning

All source files read and measured. Load pattern verified. Directory structure designed. Anti-AI audit position confirmed. No external research needed.
