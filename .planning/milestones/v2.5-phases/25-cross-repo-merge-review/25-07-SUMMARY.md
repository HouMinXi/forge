---
reconstructed: true
provenance: "git show af378f9 (2026-06-20)"
---

# 25-07 Summary: Remove Deprecated CLI Flags

## One-liner

Removed deprecated --state-dir and --staged flags from CLI (superseded since v2.1).

## Accomplishments

- Removed --state-dir argument (value was always ignored with a warning; state hardcoded to cwd/.code-forge)
- Removed --staged argument (replaced by --head INDEX)
- Removed deprecation warning handlers and mutual-exclusion checks against --committed and --whole-file
- Deleted corresponding test classes verifying deprecated behavior (-233 lines net cleanup)

## Files Changed

- src/code_forge/cli.py (-43 lines)
- tests/test_cli_parser.py (-46 lines reduced)
- tests/test_cli_phase1_compat.py (-157 lines)

## Commit

af378f9 cli: remove deprecated --state-dir and --staged flags
