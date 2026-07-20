# Phase 45: Multi-language support

## Status: merged (2026-07-10)

## What was done

Added multi-language review support to forge's detect pipeline. Landed
Go, C/C++, Java, JS/TS end-to-end. Refactored to ALL_REGISTRIES pattern
for extensibility. Deferred C#, Ruby, Swift, PHP with recorded blockers.

### Landed (7 plans)
- 45-01: MCP allow_main per-call env
- 45-02: Go support + SARIF parser trailing-noise hardening
- 45-02b: ALL_REGISTRIES refactor (1-list extensibility)
- 45-03: JS/TS via ESLint
- 45-05: Java via PMD
- 45-06: C/C++ via cppcheck (stderr-swap fix)

### Deferred to Phase 47+ (5 plans)
- 45-04: C# (blockers recorded)
- 45-07: Ruby (blockers recorded)
- 45-08: Swift (blockers recorded)
- 45-09: PHP (blockers recorded)
- 45-10: Multi-language documentation

## Verification

- Merged: c0f2b3d, 11 commits, ff
- Full suite at merge: 2695 passed, 7 skipped, 0 failed
- Additional fixes post-merge: b68d3e1 (pipeline compat for C/C++, JS/TS, Java)
