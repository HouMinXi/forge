---
phase: 16-relief-mechanisms
plan: 03
subsystem: documentation
tags: [tiering, relief-mechanism, docs, cli-help, gate-yaml]
dependency_graph:
  requires: [16-02]
  provides: [tiering-docs]
  affects: [SKILL.md, cli.py, init_template.py]
tech_stack:
  added: []
  patterns: [relief-not-defense-framing]
key_files:
  created: []
  modified:
    - src/code_forge/skills/code-forge/SKILL.md
    - src/code_forge/cli.py
    - src/code_forge/init_template.py
decisions:
  - "Tiering docs use relief-not-defense framing per D-07"
  - "CLI epilog keeps concise 3-line format (no table in --help)"
  - "gate.yaml template uses YAML comment block between outlet and backends"
metrics:
  duration: 358s
  completed: 2026-06-09
---

# Phase 16 Plan 03: Tiering Documentation Summary

Diff-size adaptive cycle count documented in SKILL.md (tier table with relief framing), CLI --help epilog (concise tiering note with env var override), and gate.yaml template (YAML comment block with tier boundaries).

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Add tiering documentation to SKILL.md, CLI help, and gate.yaml template | fac7764 | SKILL.md, cli.py, init_template.py |

## Implementation Details

### SKILL.md

Added "Adaptive Cycle Count (Relief Mechanism)" subsection after the state machine cycle counter block, before "Genuine Execution". Contains:
- Tier table: <50 lines = 2 cycles (6 passes), 50-199 = 3 (9), >=200 = 4 (12)
- Override notes: FORGE_CLEAN_ROUND_THRESHOLD env var, --whole-file mode
- Relief framing: "reduces friction for small, safe changes; does NOT weaken review for large, risky changes"

### CLI --help (cli.py)

Appended 3-line tiering note to review_parser epilog after exit codes section:
"Cycle count adapts to diff size: <50 lines = 2 cycles, 50-199 = 3 (default), >=200 = 4. Override with FORGE_CLEAN_ROUND_THRESHOLD=N."

### gate.yaml template (init_template.py)

Added YAML comment block between outlet comment and backends section with tier table and override reference. Uses "relief, not defense" label.

## Deviations from Plan

None - plan executed exactly as written.

## Verification Results

- grep "Adaptive Cycle Count" SKILL.md: FOUND
- grep -i "relief" SKILL.md: FOUND (2 matches)
- grep "diff size" cli.py: FOUND (1 match)
- grep "FORGE_CLEAN_ROUND_THRESHOLD" cli.py: FOUND
- grep "Cycle count adapts" init_template.py: FOUND
- PYTHONPATH=src python3 -m code_forge review --help | grep "diff size": FOUND
- pytest tests/test_cli_parser.py tests/test_cli_init.py: 40 passed
- Non-ASCII check: PASS
