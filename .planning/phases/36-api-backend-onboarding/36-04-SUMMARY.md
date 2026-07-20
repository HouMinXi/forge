---
phase: 36
plan: 04
status: complete
---

## Summary

Added 4 missing argparse flag definitions to `src/code_forge/cli.py`:
`--no-color` on both `review_parser` and `gate_parser`, plus `--baseline`
and `--backend` on `gate_parser`. MCP subprocess calls that pass these
flags no longer silently exit 2.

## Key Changes

- `src/code_forge/cli.py`: 4 `add_argument` calls added to accept flags
  that MCP subprocess invocations already pass

## Commits

- `85cd6e6`: cli: accept --no-color/--baseline/--backend flags from MCP subprocess

## Deviations

None.

## Self-Check

All 34 CLI parser tests pass. No regressions.
