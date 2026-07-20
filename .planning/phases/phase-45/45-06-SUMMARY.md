# 45-06: C/C++ support via cppcheck

## Status: merged

## What was done

C_CPP_TOOL_REGISTRY added to ALL_REGISTRIES. cppcheck stderr-swap fix applied. C and C++ SARIF spike passes known-answer test.

## Verification

- Merged to main as part of Phase 45 multi-language batch (2026-07-10, c0f2b3d, 11 commits)
- Full suite: 2695 passed, 7 skipped, 0 failed at merge time
