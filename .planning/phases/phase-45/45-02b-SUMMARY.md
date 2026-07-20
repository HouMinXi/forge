# 45-02b: ALL_REGISTRIES refactor

## Status: merged

## What was done

ALL_REGISTRIES list created in detect.py. All iteration sites use ALL_REGISTRIES loop. Enables adding new languages by appending to one list.

## Verification

- Merged to main as part of Phase 45 multi-language batch (2026-07-10, c0f2b3d, 11 commits)
- Full suite: 2695 passed, 7 skipped, 0 failed at merge time
