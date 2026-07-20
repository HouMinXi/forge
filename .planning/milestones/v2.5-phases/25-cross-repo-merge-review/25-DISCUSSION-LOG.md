# Phase 25: Cross-Repo Merge Review - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md -- this log preserves the alternatives considered.

**Date:** 2026-06-17
**Phase:** 25-Cross-Repo-Merge-Review
**Areas discussed:** Declaration Mechanism, Diff Acquisition, Context Assembly,
Finding Attribution, StateMachine Architecture, Thread Safety, Receipt Files,
gate.yaml Scope, L0 Language Detection, Testing Strategy

---

## Declaration Mechanism

| Option | Description | Selected |
|--------|-------------|----------|
| gate.yaml siblings section | Extend gate.yaml with siblings:; Phase 24 schema ready | yes |
| CLI flags | --sibling path@base..head; no config file required | |
| Both supported | gate.yaml for fixed workflows, CLI for overrides | |

**User's choice:** gate.yaml extension only

### Minimum required fields
- `repo:` (path) + `ref:` (baseline..head) are required; `label:` is optional

### Path resolution
- Relative to gate.yaml directory; reuse resolve_sources() symlink guard

### Relationship to conventions_resolver.py
- Reuse _resolve_paths_as_sources() for path safety validation; siblings concept is separate from naming conventions extraction

---

## Diff Acquisition

| Option | Description | Selected |
|--------|-------------|----------|
| forge runs git diff directly | Same as primary repo; consistent | yes |
| User provides patch files | Less forge involvement but adds user burden | |

- Remote support: local path first; https:// or git@ triggers shallow clone to /tmp
- Error handling: fail-closed on any sibling acquisition failure (invalid ref, network error)

---

## Context Assembly

| Option | Description | Selected |
|--------|-------------|----------|
| Labeled diff blocks | Simple heading per block | |
| Hierarchical summary + details | Summary (file list + counts) then labeled diff blocks | yes |
| Raw concatenation | No labels; boundary-blind | |

- Summary: one line per repo listing files changed and +/- counts
- Each diff block: `## Repo: [label] (ref)` heading
- File paths carry [label] prefix throughout; L0 parsers auto-detect language per repo

---

## Finding Attribution

| Option | Description | Selected |
|--------|-------------|----------|
| [label] file/path prefix | Consistent with context labels; parseable | yes |
| Absolute paths | Unambiguous but exposes local directory tree | |
| Trust LLM to tag | Unpredictable format | |

- Single-repo backward compat: zero format change when no siblings declared
- Verdict output: grouped by repo (=== [forge] === / === [kernel] === sections)
- Cross-repo finding: attributed to repo of the first file/line cited

---

## StateMachine Architecture

| Option | Description | Selected |
|--------|-------------|----------|
| Single merged StateMachine | One unified context; less code change | |
| Multiple StateMachines in parallel | One per repo; threading; isolation | yes |

- Verdict merge: primary FAIL -> joint FAIL; sibling FAIL -> advisory warning only
- Each thread: independent backend + falsifier instances (isolation, not locking)
- Shared: primary gate.yaml config passed as immutable params to all threads
- Sibling gate.yaml: fully ignored in Phase 25

---

## Receipt Files

| Option | Description | Selected |
|--------|-------------|----------|
| One per repo | Consistent with existing single-repo format | yes |
| One merged joint receipt | Requires new schema | |

Named: `{label}-receipt-rN.json` per repo

---

## L0 Language Detection

- Auto-detect per sibling based on file extensions in its diff; no inheritance from primary config

---

## Testing Strategy

- Integration tests: tmp_path with two real temporary git repos (pytest fixture)
- Model: test_install_hooks.py GIT_CEILING_DIRECTORIES isolation pattern

---

## Claude's Discretion

None -- all areas received explicit user decisions.

## Deferred Ideas

- Full remote URL workflow with sparse checkout + credential management
- Sibling gate.yaml layering (config inheritance across repos)
- asyncio migration from threading (deferred until connection count pressures arise)
- Phase 26: Cross-Repo Contract Context
- Phase 27: Cross-Repo Impact via register
