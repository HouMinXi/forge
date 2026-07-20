# Phase 1: Layer 0 Baseline + Registry - Context

**Gathered:** 2026-05-15
**Status:** Ready for planning

<domain>
## Phase Boundary

Deterministic tool gate: run static analysis tools on diffs, report only NEW violations (delta mode), produce PASS/FAIL verdict. No LLM involvement in this phase -- Layer 0 is purely deterministic.

</domain>

<decisions>
## Implementation Decisions

### v1 Code Strategy
- **D-01:** REWRITE from scratch. v1 forge_cli.py (2861 lines) is reference only. v2.0 architecture (3-state gate, loop-until-fixpoint) is fundamentally different from v1 (3-cycle counter). Clean implementation aligned with v3 design doc.
- **D-02:** v1 --dry-run logic (bash -n, shellcheck, pylint, non-ASCII grep) informs Layer 0 tool list but does not constrain implementation.

### Tool Registry
- **D-03:** YAML config file at .forge/tools.yaml. Declarative, user-editable. Each entry: name, command, args, SARIF parser, language file pattern. Adding a tool = adding a YAML block.
- **D-04:** Default registry ships with entries for: shellcheck (shell), ruff (python), semgrep (all), clippy (rust), checkpatch.pl (kernel C). Users can add/override.

### Output and State
- **D-05:** Terminal output = plain text summary (human-readable, cargo-check style). Machine state = .forge/state.json (round-to-round tracking, inspectable).
- **D-06:** Tool output internally normalized via SARIF parsing before delta computation. SARIF is internal plumbing, not user-facing.

### Hook Integration
- **D-07:** KEEP: check_worktree.sh (still useful for development discipline), check_non_ascii.sh (still catches LLM-generated non-ASCII). DROP: check_git_commit_review.sh, check_git_push_review.sh, check_review_tracker.sh (replaced by v2.0 gate state machine). Hooks = convenience enforcement; gate = authority.

### Claude's Discretion
- SARIF parser implementation strategy (per-tool vs generic)
- .forge/ directory structure for state files
- Internal module layout for v2.0 codebase

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Design Baseline
- `/tmp/draft_20260515_forge_v3_design.txt` Part 1 (3-STATE GATE), Part 2 LAYER 0 section -- gate definition, tool list, baseline mode spec
- `/tmp/draft_20260515_forge_v3_design.txt` Part 7 (STATE MACHINE) -- INTERACTIVE/CI/AUTO_FIX mode branches, ROUND_GUARD
- `/tmp/draft_20260515_forge_v3_design.txt` Part 10 Q5 S1 -- Layer 0 baseline spec requirements (SARIF, line drift, tool versioning)

### Existing Code (v1 reference)
- `cli/forge_cli.py` -- v1 CLI, --dry-run runs Layer 0 tools directly
- `hooks/check_non_ascii.sh` -- non-ASCII detection (kept)
- `hooks/check_worktree.sh` -- worktree enforcement (kept)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `cli/file_utils.py`: atomic_write, validate_diff_spec -- may be reusable for v2.0
- `hooks/check_non_ascii.sh`: grep -P '[^\x00-\x7F]' pattern -- reuse as Layer 0 check
- v1 --dry-run: runs shellcheck/pylint/non-ASCII directly in Python -- architecture reference

### Established Patterns
- .forge/ directory for persistent state (findings.json, runs/*.json)
- Python CLI with argparse
- subprocess for external tool invocation with timeout

### Integration Points
- git diff as input (validate_diff_spec already handles diff-spec parsing)
- .forge/ directory for state persistence
- Hooks via Claude Code settings.json (check_worktree, check_non_ascii remain)

</code_context>

<specifics>
## Specific Ideas

No specific requirements beyond design doc -- open to standard approaches.

</specifics>

<deferred>
## Deferred Ideas

None -- discussion stayed within phase scope.

</deferred>

---

*Phase: 01-layer-0-baseline-registry*
*Context gathered: 2026-05-15*
