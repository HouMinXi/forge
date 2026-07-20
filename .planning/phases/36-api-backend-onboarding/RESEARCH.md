# Phase 36 Research: Usability Hardening

## Research Method

5-round exhaustive workflow audit (305 agents, ~36M tokens) covering all 78
.py files in src/code_forge/. Each round: parallel finders per dimension ->
adversarial verify -> completeness critic. Full methodology and convergence
data in 36-MCP-USABILITY-FINDINGS.md.

## Input Artifacts

- **36-MCP-USABILITY-FINDINGS.md** -- 55 confirmed findings (MCP-01..55),
  per-finding evidence with file:line, fix direction, acceptance signals
- **ROADMAP.md Phase 36 section** -- 7 fix patterns (A-G) with finding map

## Scope Definition (from ROADMAP)

55 usability findings clustered into 7 actionable fix patterns:

### Pattern A: MCP-to-CLI flag alignment (4 findings)
MCP-10, 11, 13, 14. MCP server passes flags (--no-color, --baseline,
--backend, --output, --version) that CLI argparse does not define. Every
MCP subprocess call hitting these silently exits 2.
**Fix:** align argparse definitions with mcp_server.py cli_args.
**Effort:** small -- add_argument calls + tests.

### Pattern B: Error remediation (9 findings)
MCP-09, 17, 18, 26, 27, 34, 38, 43, 44. CliError has no hint field;
36 of 37 raise sites give no fix step; hooks and subcommands give opaque
errors.
**Fix:** add optional remediation field to CliError, backfill operational
errors with one-line hints.
**Effort:** medium -- CliError class change + 15-20 raise site updates +
gate_check diagnostic improvements.

### Pattern C: Docs vs reality (12 findings)
MCP-12, 15, 28, 29, 30, 31, 32, 33, 49, 50, 51, 52. Phantom CLI flags
documented, stale status lines, wrong model IDs, missing exit codes,
outdated pass names.
**Fix:** one docs audit pass reconciling every command/flag/status against
code.
**Effort:** small -- text edits only, no code changes.

### Pattern D: Silent failures (7 findings)
MCP-16, 21, 22, 35, 36, 37, 54. stderr discarded, tool timeouts invisible,
malformed data swallowed, parser exceptions discard prior findings, sqlite3
connections leak.
**Fix:** surface errors through infra_errors/stderr/logging instead of bare
pass.
**Effort:** medium -- 3-5 line fixes per site, ~7 sites.

### Pattern E: Onboarding friction (9 findings)
MCP-01, 02, 03, 04, 05, 06, 07, 08, 42. The original Phase 36 scope.
Workspace resolution, trust ceremony, worktree guard, key provenance,
reconnect zombies.
**Fix:** two-step init + trust-on-first-use + --allow-main + forge doctor.
**Effort:** large -- architectural changes to workspace resolution, trust
model, worktree guard. Most complex pattern.

### Pattern F: Edge-path crashes (7 findings)
MCP-19, 20, 23, 39, 40, 47, 48. CliError not imported, _run_ci returns
None, missing FileNotFoundError catch, ValueError traceback, fixval deletes
recovery file, hold.py IndexError, diagnose masks reason.
**Fix:** per-site 3-5 line fixes.
**Effort:** small -- surgical fixes, each independent.

### Pattern G: Packaging and hygiene (7 findings)
MCP-41, 45, 46, 53, 55. Version desync (pyproject.toml/init/CLAUDE.md),
baseline delta pytest-only, type annotation wrong, dead code, job pop-on-read
contradicts idempotentHint.
**Fix:** single-source version, per-site cleanup.
**Effort:** small -- isolated fixes.

## Severity Distribution

| Severity | Count | Patterns |
|----------|-------|----------|
| BLOCKER  | 2     | E (MCP-01), F (MCP-19) |
| HIGH     | 13    | A (10,11), B (09), E (02-04,07,08), F (20,40), G (41,49) |
| MEDIUM   | 21    | across all patterns |
| LOW      | 19    | across all patterns |

## Main-Path Impact

BLOCKERs do NOT affect CLI-direct review + API backend happy path:
- MCP-19 (CliError not imported): only via stream=true Anthropic/Vertex
- MCP-01 (workspace resolution): MCP server path only, CLI-direct unaffected

Verified by R3 Impact phase (3 independent agents tracing call paths).

## Dependency Analysis

- Patterns A, C, D, F, G are independent -- can parallelize freely
- Pattern B (CliError hint field) is a structural change that Pattern A
  and F raise sites may want to use, but is not blocking (they can add
  hints after B lands)
- Pattern E is the most complex and least parallelizable (workspace
  resolution touches trust, MCP server, CLI)

## Recommended Execution Order

1. **Wave 0:** F (edge crashes) + G (hygiene) + C (docs) -- independent,
   low risk, high count (26 findings cleared)
2. **Wave 1:** A (flag alignment) + D (silent failures) -- independent,
   medium risk (11 findings cleared)
3. **Wave 2:** B (error remediation) -- structural CliError change, then
   backfill (9 findings cleared)
4. **Wave 3:** E (onboarding) -- the big one, depends on stable error
   handling from B (9 findings cleared)

## Acceptance Signals

Full per-pattern acceptance signals in 36-MCP-USABILITY-FINDINGS.md
"Suggested Phase 36 acceptance signals" section (lines 420-468).

Summary gate: all 55 findings verified fixed by the matching acceptance
signal. No new BLOCKER or HIGH regressions in existing 2372 tests.

## Research Conclusion

The findings doc is exhaustive (78/78 files, R5 found only 4 LOW/MEDIUM).
No further research needed. The planner should organize plans around the
4-wave execution order above, with Pattern E potentially split into
sub-plans given its architectural scope.
