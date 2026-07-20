---
phase: 06-outlet-b-inline-merge
plan: 01
subsystem: skill-files
tags: [inline-merge, passes, references, outlet-b]
dependency_graph:
  requires: []
  provides:
    - code-forge-pass-files
    - code-forge-references
  affects:
    - ~/.claude/skills/code-forge/
tech_stack:
  added: []
  patterns:
    - verbatim-copy-from-source
    - severity-normalization
    - path-context-declaration
key_files:
  created:
    - ~/.claude/skills/code-forge/passes/pass1-qodo.md
    - ~/.claude/skills/code-forge/passes/pass2-expert.md
    - ~/.claude/skills/code-forge/passes/pass3-adversarial.md
    - ~/.claude/skills/code-forge/references/solid-checklist.md
    - ~/.claude/skills/code-forge/references/security-checklist.md
    - ~/.claude/skills/code-forge/references/code-quality-checklist.md
    - ~/.claude/skills/code-forge/references/removal-plan.md
  modified: []
decisions:
  - Severity normalization uses P0-P3 exclusively across all 3 passes
  - BOTH-01 coverage instruction added to all pass files
  - Path context declaration added to all pass files
  - References copied verbatim (no modifications)
  - Standalone skills remain untouched (D-11 constraint)
metrics:
  start_time: "2026-06-01T20:00:00Z"
  end_time: "2026-06-01T20:10:00Z"
  duration_minutes: 10
  tasks_completed: 2
  files_created: 7
---

# Phase 06 Plan 01: Inline Merge Building Blocks Summary

Create passes/ and references/ subdirectories under ~/.claude/skills/code-forge/ with extracted content from standalone review skills, adapted for inline merge execution.

## Objective

Extract and adapt the three standalone review pass skills (qodo-review, code-review-expert, adversarial-qe) into self-contained pass files, plus copy 4 reference checklists, to enable Outlet B inline merge execution without sub-skill Invoke calls.

## Tasks Completed

### Task 1: Create references/ directory with 4 checklist files ✓

Created ~/.claude/skills/code-forge/references/ with verbatim copies of 4 reference files from code-review-expert:
- solid-checklist.md (SOLID smell prompts and refactor heuristics)
- security-checklist.md (security and reliability checklist including race conditions)
- code-quality-checklist.md (error handling, performance, boundary conditions)
- removal-plan.md (template for deletion candidates and iteration plans)

Verification: `diff -r` confirmed all 4 files are byte-identical to source.

### Task 2: Create passes/ directory with 3 adapted pass files ✓

Created ~/.claude/skills/code-forge/passes/ with 3 adapted pass files:

**pass1-qodo.md** (adapted from qodo-review/SKILL.md, 135 lines → 119 lines):
- Added path context declaration and BOTH-01 instruction as first non-heading lines
- Removed YAML frontmatter, "When to Use/NOT to Use", "Arguments" sections
- Normalized severity: Red/Yellow/Green → P0-P3
  - Category headers: "Red Security Vulnerabilities" → "P0 - Critical"
  - "Red Potential Bugs" → "P1 - High"
  - "Yellow Best Practice Violations" → "P2 - Medium"
  - "Green Minor Issues" → "P3 - Low"
  - Severity brackets: "[🔴 High]" → "[P0]" or "[P1]", "[🟡 Medium]" → "[P2]", "[🟢 Low]" → "[P3]"
- Removed all emoji markers (red/yellow/green circles)
- Kept all core review logic: gathering changes, edge cases, anti-hallucination gate, output structure

**pass2-expert.md** (adapted from code-review-expert/SKILL.md, 164 lines → 138 lines):
- Added path context declaration and BOTH-01 instruction
- Removed YAML frontmatter, Chinese language override, "Overview" paragraph
- Kept P0-P3 severity levels unchanged (already native)
- Kept all 7 workflow sections with Load references/ directives
- Removed "Next steps confirmation" section (conflicts with auto-continue)
- Removed "Resources" section (standalone directory reference)
- Load directives now resolve to ~/.claude/skills/code-forge/references/

**pass3-adversarial.md** (adapted from adversarial-qe/SKILL.md, 224 lines → 194 lines):
- Added path context declaration and BOTH-01 instruction
- Removed YAML frontmatter, tool-agnostic preamble
- Removed "Jira integration", "Posting review comments", "Boundaries", "Policy reminder", "Relationship to other skills" sections
- Normalized severity: Critical/High/Medium/Low/Nit → P0/P1/P2/P3
- Kept all 14 attack dimensions (correctness, edge cases, error handling, security, concurrency, API/contract, bidirectional, graceful degradation, convention adherence, performance, test quality, AI code smells, commit message accuracy, callchain analysis)
- Kept dismissal discipline and finding verification gate

Verification: All 3 pass files exist, contain BOTH-01 instruction, use P0-P3 severity exclusively, and have no Invoke directives.

## Deviations from Plan

None - plan executed exactly as written.

## Known Stubs

None - these are documentation files with no runtime behavior.

## Threat Flags

None - review instruction files contain no security-relevant surface.

## Self-Check: PASSED

**Created files verified:**
```
✓ ~/.claude/skills/code-forge/references/solid-checklist.md (exists)
✓ ~/.claude/skills/code-forge/references/security-checklist.md (exists)
✓ ~/.claude/skills/code-forge/references/code-quality-checklist.md (exists)
✓ ~/.claude/skills/code-forge/references/removal-plan.md (exists)
✓ ~/.claude/skills/code-forge/passes/pass1-qodo.md (exists)
✓ ~/.claude/skills/code-forge/passes/pass2-expert.md (exists)
✓ ~/.claude/skills/code-forge/passes/pass3-adversarial.md (exists)
```

**Content verification:**
```
✓ References identical to source (diff -r returned no differences)
✓ All pass files contain BOTH-01 instruction
✓ pass1-qodo.md uses P0-P3 (no Red/Yellow/Green, no emoji)
✓ pass3-adversarial.md uses P0-P3 (no Critical/High/Medium/Low/Nit)
✓ pass2-expert.md already P0-P3 native (verified)
✓ No Invoke directives in any pass file
✓ Standalone skills untouched (qodo-review, code-review-expert, adversarial-qe)
```

## Next Steps

Plan 06-02 will create the aggregate SKILL.md that Loads these pass files to enable Outlet B inline merge execution, eliminating the sub-skill Invoke hang bug.
