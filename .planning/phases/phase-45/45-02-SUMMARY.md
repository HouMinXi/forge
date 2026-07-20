# 45-02: Go support + SARIF parser hardening

## Status: merged

## What was done

_parse_sarif uses raw_decode (trailing-noise tolerant). GO_TOOL_REGISTRY added with golangci-lint v2.x syntax. Go detection via go.mod or *.go files.

## Verification

- Merged to main as part of Phase 45 multi-language batch (2026-07-10, c0f2b3d, 11 commits)
- Full suite: 2695 passed, 7 skipped, 0 failed at merge time
