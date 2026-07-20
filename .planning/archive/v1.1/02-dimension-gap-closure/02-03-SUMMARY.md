---
phase: 02-dimension-gap-closure
plan: 03
subsystem: cli
tags: [custom-rules, DIM-07, yaml-frontmatter, prompt-injection]
dependency_graph:
  requires: [02-01, 02-02]
  provides: [custom-rule-loader, forge-rules-md-support, rule-prompt-injection]
  affects: [cli/forge_cli.py, cli/config.json]
tech_stack:
  added: [PyYAML (optional, defensive import)]
  patterns: [yaml-frontmatter-markdown-body, severity-sorted-injection, config-driven-caps]
key_files:
  created: []
  modified: [cli/forge_cli.py, cli/config.json]
decisions:
  - "yaml imported with try/except ImportError -- custom rules gracefully disabled if PyYAML missing"
  - "Duplicate rule names across files trigger fatal error (sys.exit(1)) per D4 spec"
  - "Rules sorted by severity (critical first) before cap enforcement to prioritize high-severity rules"
  - "R1 fix: rules injected into prompt BEFORE cmd= construction (Python string immutability)"
  - "R11 fix: scope field normalized from string to list in _parse_rule_file"
metrics:
  duration: 3m
  completed: "2026-05-12T16:47:51Z"
  tasks_completed: 1
  tasks_total: 1
  files_modified: 2
---

# Phase 02 Plan 03: Custom Rules Loader Summary

Project-specific custom rules loaded from forge-rules.md and .forge/rules/*.md with YAML frontmatter parsing via safe_load, severity-sorted injection into LLM prompt before cmd construction, and configurable caps (20 rules / 15000 chars).

## What Was Done

### Task 1: Add custom rule loader, parser, and prompt injection

**Commit:** `6283233`

Added three functions and one constant to `cli/forge_cli.py`:

1. `VALID_SEVERITIES` -- set of accepted severity values (critical, high, medium, low).

2. `_parse_rule_file(filepath)` -- Parses a single rule file with YAML frontmatter (between `---` delimiters) and Markdown body. Uses `yaml.safe_load` (T-02-06 mitigation). Validates required fields (name, severity). Normalizes unknown severity to 'medium' with warning. R11 fix: normalizes scope from string to list.

3. `load_custom_rules(project_root, config)` -- Discovers rules from forge-rules.md (single file) then .forge/rules/*.md (multi-file directory, sorted). Rejects duplicate names with fatal error (sys.exit(1)). Filters disabled rules (enabled: false). Sorts active rules by severity (critical first). Caps total injection at configurable limits (default 20 rules, 15000 chars). Returns empty list if PyYAML not installed.

4. `format_rules_for_prompt(rules)` -- Formats loaded rules as a Markdown section for LLM prompt injection with rule name, severity, dimension, scope, and body.

5. R1 fix in `run_forge()`: custom rules loaded and appended to prompt BETWEEN the prompt if/else block and the cmd= construction. Python strings are immutable -- cmd captures prompt by value at construction time, so modification must happen before cmd is built.

Added `custom_rules` section to `cli/config.json` with `max_rules: 20` and `max_total_chars: 15000`.

Added `import yaml` with try/except ImportError fallback at module level.

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Defensive yaml import (try/except) | Matches project pattern for optional tools (radon, shellcheck, ruff) -- graceful degradation |
| Fatal error on duplicate rule names | D4 spec requires clear error; silent dedup would hide user misconfiguration |
| Severity-sorted injection | Critical rules get priority when cap truncation occurs |
| Rules injected before cmd= (R1) | Python string immutability means cmd[2] captures prompt by value at construction |
| Scope string-to-list normalization (R11) | Users may write scope as a single string instead of a list |

## Verification Results

| Check | Result |
|-------|--------|
| `python3 -m py_compile cli/forge_cli.py` | PASS |
| `config.json` validation (custom_rules present, max_rules=20) | PASS |
| Function defs + calls count (>=6) | 10 matches |
| `yaml.safe_load` usage | PASS |
| Duplicate name fatal error (sys.exit(1)) | PASS |
| R1: load_custom_rules (line 1717) BEFORE cmd= (line 1728) | PASS |
| R11: isinstance(scope, str) normalization | PASS |
| Non-ASCII check on new code | PASS |
| No accidental file deletions | PASS |
| No untracked files | PASS |

## Deviations from Plan

None -- plan executed exactly as written.

## Known Stubs

None -- all functions are fully implemented with real logic.

## Self-Check: PASSED

- cli/forge_cli.py: FOUND
- cli/config.json: FOUND
- Commit 6283233: FOUND

---
*Phase: 02-dimension-gap-closure*
*Completed: 2026-05-12*
