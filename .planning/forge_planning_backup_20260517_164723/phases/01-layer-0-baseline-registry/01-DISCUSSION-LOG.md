# Phase 1: Layer 0 Baseline + Registry - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md -- this log preserves the alternatives considered.

**Date:** 2026-05-15
**Phase:** 01-layer-0-baseline-registry
**Areas discussed:** v1 code strategy, Tool registry format, Output + state format, Hook integration

---

## v1 Code Strategy

| Option | Description | Selected |
|--------|-------------|----------|
| Rewrite | v2.0 from scratch. v1 code as reference only. Clean architecture. | [x] |
| Incremental refactor | Keep v1 structure, replace internals. Less risk, carries debt. | |
| Parallel + migrate | Build v2.0 alongside v1. Feature parity then switch. | |

**User's choice:** Rewrite (Recommended)
**Notes:** v2.0 architecture (3-state gate, loop-until-fixpoint) fundamentally different from v1 (3-cycle counter). Clean break justified.

---

## Tool Registry Format

| Option | Description | Selected |
|--------|-------------|----------|
| YAML config file | .forge/tools.yaml -- declarative, user-editable. Add tool = add YAML block. | [x] |
| Python registry module | tools/registry.py with ToolDef dataclass. Programmatic. | |
| Plugin directory | .forge/tools.d/*.yaml -- one file per tool. Most modular. | |

**User's choice:** YAML config file (Recommended)
**Notes:** None

---

## Output + State Format

| Option | Description | Selected |
|--------|-------------|----------|
| Plain text + JSON state | Terminal = human summary. State = .forge/state.json. | [x] |
| SARIF throughout | All output in SARIF. Standard but verbose. | |
| Structured JSON only | Both output and state in JSON. Machine-first. | |

**User's choice:** Plain text + JSON state (Recommended)
**Notes:** None

---

## Hook Integration

| Option | Description | Selected |
|--------|-------------|----------|
| Keep useful, drop redundant | Keep worktree + non-ASCII. Drop review cycle hooks. | [x] |
| Drop all hooks | v2.0 gate is single authority. Simpler but loses worktree. | |
| Redesign all hooks | New hooks for v2.0 events. Most flexible, most work. | |

**User's choice:** Keep useful, drop redundant (Recommended)
**Notes:** Gate = authority, hooks = convenience.

---

## Claude's Discretion

- SARIF parser implementation strategy
- .forge/ directory structure
- Internal module layout

## Deferred Ideas

None
