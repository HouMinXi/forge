# Phase 38 Plan: setup-mcp

## Tasks

- [x] T1: R1 docs -- fix init_template.py Kimi+MiniMax stale examples (ccfb679)
- [x] T2: Create setup_mcp.py with preset table (matrix-verified), render, write, trust
- [x] T3: Wire setup-mcp subcommand in cli.py (--backend/--force/--dry-run)
- [x] T4: S3 fix -- _check_backend reads merged view via cli._merge_user_into
- [x] T5: Tests -- test_setup_mcp.py (17 tests) + test_mcp_server.py precheck updates (6 new/modified)
- [x] T6: mimo-pro forge review -- 10 findings, 6 fixed (error msg/stderr/trust-exc/args/mixed-test)
- [ ] T7: C6 dogfood -- migrate mimo-pro from project gate.yaml to user-level config
- [ ] T8: C7 onboarding -- scratch project MCP review through forge server (R5)
- [x] T9: Multi-model review via aicc -- 5 rounds (R1-R5), 15 fixes, all 5 models 0/0/0/0
- [ ] T10: Final amend + delivery briefing

## Commits

1. ccfb679 -- init: update Kimi and MiniMax endpoint examples (docs)
2. dcc687d -- mcp: add setup-mcp command and fix precheck (includes R1-R5 review fixes)

## Test Results

2442 passed / 7 skipped / 0 failed (baseline 2424 + 17 new setup-mcp + 1 partial-key)

## Review History

- C1P1 qodo: 2 Medium (comment-type, not blocking)
- C1P2 expert: 2 P2 + 1 P3, all fixed (path dedup, registration conditional, plan-ref)
- C1P3 adversarial: 1 Medium (5 plan-ref tokens), fixed
- mimo-pro forge: 10 findings, 6 fixed
- CP1b R1 (5 models): 5 bugs found, all fixed (mock, key-env, expanduser, dedup, test assert)
- CP1b R2 (5 models): 3 bugs found, all fixed (log.warning, 4th mock, timeout_s)
- CP1b R3 (5 models): 3 findings, all fixed (partial-key test, gate assert, UX command)
- CP1b R4 (5 models): mimo 0/0/1/3, others 0/0/0/0. 4 fixes (7th mock, resolve, pop, multi-key)
- CP1b R5 (mimo): 0/0/0/0. All models converged.
