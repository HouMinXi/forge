# Phase 2 Plan Reviews

**Reviewed by:** Claude Opus (self), Kimi, Mimo, DeepSeek
**Date:** 2026-05-12
**Plans reviewed:** 02-01 through 02-05

## Consolidated Findings (Deduplicated)

### P0: Must Fix Before Execution

**R1 [HIGH] Plan 03 Task 1: Custom rules prompt injection is dead code (4/4 consensus)**
- `run_forge()` builds `cmd = ['claude', '-p', prompt, ...]` at line 1389. Plan 03 appends rules AFTER cmd construction: `prompt = prompt + rules_prompt`. Python strings are immutable -- `cmd[2]` holds the original string, rules never reach claude -p.
- **Fix:** Move custom rules injection BEFORE `cmd = [...]` construction (before line 1388). The plan's intent ("after prompt assembly, before subprocess invocation") is correct but the insertion point must be before `cmd = [...]`, not after it.

**R2 [HIGH] Plan 05 Task 2: git apply will fail on all 7 diffs (DeepSeek)**
- Seed test creates 1-line placeholder files (`# placeholder\n`), then tries `git apply` on diffs referencing original line numbers (e.g., `@@ -15,8 +15,12 @@`). Placeholder has 1 line, git apply fails 100% of the time. Fallback `_create_after_files()` reconstructs only the after-state, so `git diff HEAD~1` shows entire file as new -- triggers wrong dimensions.
- **Fix:** Abandon git apply approach entirely. Instead: (a) parse diff to construct both before-state and after-state files, (b) commit before-state, (c) overwrite with after-state, (d) commit. This produces the exact intended diff.

**R3 [HIGH] Plan 04 Task 1: evaluate_dimensions() signature loses config parameter (DeepSeek)**
- Plan shows OLD signature as `def evaluate_dimensions(findings, json_format=False):` but actual code (line 699) is `def evaluate_dimensions(findings, config=None, json_format=False):`. Mechanical "Change X to Y" instruction drops `config` parameter.
- **Fix:** Correct the plan to preserve config: `def evaluate_dimensions(findings, config=None, json_format=False, include_shadow=False):`

**R4 [HIGH] Plan 01 Task 1: Shell function regex misses `function name {` syntax (3/4 consensus)**
- Regex `r'^\s*(?:function\s+)?(\w[\w-]*)\s*\(\s*\)\s*\{?\s*$'` requires `()`. Bash allows `function name { ... }` (no parens). Common in older scripts.
- **Fix:** Use two patterns: (1) original for `name()` syntax, (2) `r'^\s*function\s+(\w[\w-]*)\s*\{?\s*$'` for `function name {`. Second pattern requires `function` keyword to reduce false positives.

**R5 [HIGH] Plan 05 Task 1-2: Seed test dimension name `error_handling` not in VALID_DIMENSIONS (Kimi+DeepSeek, Mimo incorrectly retracted)**
- VALID_DIMENSIONS has `'correctness'` but NOT `'error_handling'`. LLM findings for error handling get validated against VALID_DIMENSIONS and downgraded to `'unknown'`. Seed test checks `if target_dim in dims_found` and fails.
- **Fix:** Plan 02 must extend VALID_DIMENSIONS to include `'error_handling'`, `'edge_cases'`, and remove orphaned entries `'style'` and `'architecture'` that don't correspond to any adversarial-qe dimension. Seed test target names must match the updated VALID_DIMENSIONS.

### P1: Should Fix

**R6 [MEDIUM] Plan 02 Task 2: `--promote <dim>` CLI referenced but never implemented (Claude+Kimi)**
- SKILL.md shadow mode section says "User runs `forge --promote <dim>`" but no plan implements this command.
- **Fix:** Either add `--promote <dim>` to Plan 04 (updates all findings for that dimension to shadow=False in findings.json), or remove the CLI reference from SKILL.md and describe manual process.

**R7 [MEDIUM] Plan 04 Task 1: show_recommendations() not filtered for shadow findings (Kimi+Claude)**
- evaluate_dimensions() and show_stats() get shadow filter but show_recommendations() (line 973) loads all findings unfiltered. Shadow dimension findings participate in rule improvement recommendations.
- **Fix:** Add shadow filter at start of show_recommendations(): `findings = [f for f in findings if not f.get('shadow', False)]`

**R8 [MEDIUM] Plan 05 Task 2: `forge HEAD~1` is not a CLI command (Mimo+Claude)**
- `forge` is a Claude Code skill, not a standalone binary. Phase 1a CLI wrapper is invoked as `python3 cli/forge_cli.py <diff_spec>`, not `forge`.
- **Fix:** Use `sys.executable` + absolute path to `cli/forge_cli.py`, or document that `forge` must be installed/aliased first.

**R9 [MEDIUM] Plan 01 Task 1: Step 0b insertion point description contradicts code (Claude)**
- Plan says "insert between Step 0a and Step 0c" but the Step 0b code block is a standalone if/elif that conflicts with Step 0a's existing if/elif structure. Executor may create nested or conflicting conditionals.
- **Fix:** Make explicit: Step 0b is a SEPARATE if/elif block at the same indentation level as Step 0a's if/elif, placed AFTER Step 0a's elif closes and BEFORE the Step 0c comment.

**R10 [MEDIUM] Plan 01 Task 1: load_config() placement contradictory (Claude+DeepSeek)**
- Plan action shows `config = load_config()` inside the Step 0b per-file block, then IMPORTANT note says hoist outside. Contradictory instructions.
- **Fix:** Remove config = load_config() from the inner Step 0b block code snippet. Only show the hoisted version: add `config = load_config()` after `findings = []` line, before `for filepath in changed_files:`.

**R11 [MEDIUM] Plan 03 Task 1: scope as string crashes join() (Kimi)**
- D4 spec says `scope: list[glob]` but users may write `scope: "**/*.py"` (string). `', '.join(meta['scope'])` iterates characters of the string instead of joining a list.
- **Fix:** Add `if isinstance(scope, str): scope = [scope]` before join.

**R12 [MEDIUM] Plan 04 Task 1: co-location rate min() formula asymmetric (3/4 consensus)**
- If dim A has 1000 findings, dim B has 10, 5 co-located: rate = 5/10 = 50% (merge candidate). But dim A only overlaps 0.5%.
- **Fix:** Report both directional rates, or require BOTH rates above threshold for merge recommendation.

**R13 [MEDIUM] Plan 01 Task 1: Heredoc braces poison brace-depth counting (Kimi+Claude)**
- Shell parser counts `{`/`}` in heredoc content, corrupting brace_depth. Common in real shell scripts.
- **Fix:** Add simple heredoc detection: track `<<` / `<<-` delimiter, skip lines until delimiter line seen.

**R14 [MEDIUM] Plan 03 Task 1: depends_on incomplete (DeepSeek)**
- Plan 03 declares `depends_on: [02-01]` but custom rules may reference dimension names from Plan 02 (e.g., `dimension: doc_completeness`). Should depend on `[02-01, 02-02]`.
- **Fix:** Add 02-02 to depends_on.

**R15 [MEDIUM] Plan 02+04: Shadow findings visible between Wave 1 and Wave 3 (Mimo)**
- Plan 02 (Wave 1) adds shadow dimensions, Plan 04 (Wave 3) adds display filter. Between waves, shadow findings appear in stats/eval output.
- **Fix:** Add note to Plan 02: "Shadow mode not fully operational until Plan 04 completes display filter."

**R16 [MEDIUM] Plan 02 Task 1: Dim 9 readability bullets overlap with DIM-03 Step 0b (DeepSeek)**
- Dim 9 adds "Nesting depth" and "Function length" bullets, which overlap with Step 0b deterministic complexity checks.
- **Fix:** Add note that Step 0b findings are passed as context to LLM passes (FUSE-01), so dim 9 should not re-flag same (file, line) if complexity already covers it. Or narrow dim 9 readability to semantic aspects only.

## Summary

| # | Severity | Plan | Finding | Source |
|---|----------|------|---------|--------|
| R1 | HIGH | 03 | Prompt injection dead code | 4/4 consensus |
| R2 | HIGH | 05 | git apply 100% failure on placeholders | DeepSeek |
| R3 | HIGH | 04 | evaluate_dimensions drops config param | DeepSeek |
| R4 | HIGH | 01 | Shell regex misses `function name {` | 3/4 consensus |
| R5 | HIGH | 05+02 | error_handling not in VALID_DIMENSIONS | Kimi+DeepSeek |
| R6 | MEDIUM | 02 | --promote CLI unimplemented | Claude+Kimi |
| R7 | MEDIUM | 04 | show_recommendations no shadow filter | Kimi+Claude |
| R8 | MEDIUM | 05 | forge HEAD~1 not a CLI command | Mimo+Claude |
| R9 | MEDIUM | 01 | Step 0b insertion point ambiguous | Claude |
| R10 | MEDIUM | 01 | load_config() placement contradictory | Claude+DeepSeek |
| R11 | MEDIUM | 03 | scope string crashes join | Kimi |
| R12 | MEDIUM | 04 | co-location min() asymmetric | 3/4 consensus |
| R13 | MEDIUM | 01 | Heredoc braces poison depth | Kimi+Claude |
| R14 | MEDIUM | 03 | depends_on missing 02-02 | DeepSeek |
| R15 | MEDIUM | 02+04 | Shadow visible between waves | Mimo |
| R16 | MEDIUM | 02 | Dim 9 readability overlaps Step 0b | DeepSeek |
