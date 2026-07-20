# Phase 12: Backend API Wiring - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md -- this log preserves the alternatives considered.

**Date:** 2026-06-04
**Phase:** 12-backend-api-wiring
**Areas discussed:** fallback behavior, max_tokens, F1/F2/F3 scope, inline flags, gate.yaml schema, test strategy, cost output, error messages, backward compat

---

## Fallback Behavior

| Option | Description | Selected |
|--------|-------------|----------|
| FAIL CLOSED | CliError, no fallback | yes |
| Warn + fallback | Keep current warn + DEFAULT_BACKEND | |
| Tiered | No gate.yaml=warn; has gate.yaml but missing=CliError | |

**User's choice:** FAIL CLOSED
**Notes:** Consistent with D-29. Config error = stop, not silently degrade.

## No gate.yaml + --backend

| Option | Description | Selected |
|--------|-------------|----------|
| CliError (consistent) | Force user to write gate.yaml first | |
| Allow inline --backend-url | 3 flags for quick trial | yes |
| You decide | | |

**User's choice:** Allow inline --backend-url

## Default Backend Selection

| Option | Description | Selected |
|--------|-------------|----------|
| default: true marker | User marks one backend | yes |
| First api type wins | Order-sensitive | |

**User's choice:** default: true marker

## Missing API Key

| Option | Description | Selected |
|--------|-------------|----------|
| CliError + hint | Clear error message | yes |

## max_tokens Value

| Option | Description | Selected |
|--------|-------------|----------|
| 16384 fixed | Simple | |
| 8192 fixed | Conservative | |
| Configurable per backend | BackendConfig.max_tokens | yes |

**User's choice:** Configurable per backend, default 16384

## OpenAI max_tokens

| Option | Description | Selected |
|--------|-------------|----------|
| Same configurable field | Unified with anthropic | yes |
| Keep unset | Provider default | |

## F1 Scope

| Option | Description | Selected |
|--------|-------------|----------|
| Pure refactor | Flatten only, no behavior change | yes |
| Also fix edge cases | Remove dead branches | |

**User's follow-up:** Confirmed the for-loop is dead abstraction -- 4 independent ifs is clearer.

## F2 DRY Direction

| Option | Description | Selected |
|--------|-------------|----------|
| Extract helper | New helper function | |
| Merge into one function | Thorough merge | yes |

## F3 --whole-file

| Option | Description | Selected |
|--------|-------------|----------|
| Keep single file | Current behavior | |
| Expand to multi-file | nargs='+' | yes |

## Inline Flag Design

| Option | Description | Selected |
|--------|-------------|----------|
| 3 independent flags | --backend-url + --backend-format + --backend-key-env | yes |
| 1 unified flag | Compound parameter | |
| --backend-url + auto-detect | Infer format from URL | |

## gate.yaml Schema

**User's choice:** Approved the schema preview (see D-11 in CONTEXT.md)

## Test Strategy

| Option | Description | Selected |
|--------|-------------|----------|
| Mock + 1 real smoke | Unit mock + integration test | yes |
| Mock only | Defer real API to Phase 13 | |

## Cost Output

| Option | Description | Selected |
|--------|-------------|----------|
| stderr one-liner | Per-pass token count on stderr | yes |
| Summary at end | Aggregate after all passes | |

## Error Messages

| Option | Description | Selected |
|--------|-------------|----------|
| Wrapped CliError | Backend name + HTTP status + hint | yes |

## Backward Compatibility

| Option | Description | Selected |
|--------|-------------|----------|
| Zero change | New features only on explicit config | yes |

---

## Deferred Ideas

- **Cross-repo joint scanning (MULTI-01):** --whole-file across sibling repos. Deferred to v2.4+ -- requires multi-repo diff model, current baseline.py/git.py are single-repo. Tracked in REQUIREMENTS.md Future Requirements.

---

*Discussion log: 2026-06-04*
