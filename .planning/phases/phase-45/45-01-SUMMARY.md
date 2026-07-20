# 45-01: MCP allow_main per-call env

## Status: merged

## What was done

forge_review accepts allow_main parameter; FORGE_ALLOW_MAIN=1 set for subprocess; worktree error message points fork-clone users to --allow-main.

## Verification

- Merged to main as part of Phase 45 multi-language batch (2026-07-10, c0f2b3d, 11 commits)
- Full suite: 2695 passed, 7 skipped, 0 failed at merge time
