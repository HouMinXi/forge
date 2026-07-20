---
phase: 24-config-legibility
plan: "01"
subsystem: config
tags: [template, schema, docs, gate-yaml]
dependency_graph:
  requires: []
  provides: [gate.schema.json, self-documenting-template]
  affects: [docs/configuration.md, README.md, docs/setup-vscode.md, docs/setup-cursor.md, docs/setup-pycharm.md]
tech_stack:
  added: [gate.schema.json (JSON Schema draft-2020-12)]
  patterns: [yaml-language-server schema directive, allOf conditional backend validation]
key_files:
  created:
    - src/code_forge/gate.schema.json
  modified:
    - src/code_forge/init_template.py
    - docs/configuration.md
    - docs/setup-vscode.md
    - docs/setup-cursor.md
    - docs/setup-pycharm.md
    - README.md
decisions:
  - "Template ships outlet: subprocess uncommented so yaml.safe_load returns a dict (not None), making load_backend_configs work without special-casing."
  - "Schema uses additionalProperties: false on backendEntry with all loader-accepted fields listed; uses additionalProperties: true on top-level and test/presubmit/graph_triage/daemon_state for forward compat."
  - "allOf with separate if/then pairs (not multiple top-level if keywords) per JSON Schema draft-2020-12 spec."
metrics:
  duration: ~20 minutes
  completed: 2026-06-15
  tasks_completed: 2
  files_changed: 7
---

# Phase 24 Plan 01: Self-Documenting Template, Schema, and Doc Fixes Summary

One-liner: JSON Schema draft-2020-12 for gate.yaml plus inline-documented template and three doc-fiction purges (backends.yaml path, list-format backends, FORGE_OUTLET=cli).

## What Was Done

### Task 1: Rewrote GATE_YAML_TEMPLATE and added gate.schema.json

**src/code_forge/init_template.py** -- complete rewrite of `GATE_YAML_TEMPLATE`:
- Added `# yaml-language-server: $schema=./gate.schema.json` as the mandatory first line (E6).
- Uncommented `outlet: subprocess` so `yaml.safe_load` returns a dict (not None); `load_backend_configs` accepts it cleanly (E2).
- Replaced all CN-stack backends (mimo, deepseek, kimi, glm, minimax) with generic commented examples: `local-claude`, `claude-api`, `openai-compatible`, `vertex-claude`.
- Corrected `vertex-claude` from `type: cli` to `type: api, format: vertex` (ground truth from `backend.py`).
- Removed the incorrect `# pattern: "*.py"` comment; replaced with `# source_patterns: ["*.py"]` (gate_check.py:91 reads `source_patterns`, not `pattern`).
- Added inline documentation for all loader-recognized fields: `test`, `non_ascii`, `presubmit`, `graph_triage`, `daemon_state` with their sub-fields and defaults.
- Documented `outlet:` with all three canonical values and the `cli` deprecation note.

**src/code_forge/gate.schema.json** -- new file, JSON Schema draft-2020-12:
- Top-level `additionalProperties: true` (loader ignores unknown top-level keys).
- `outlet` enum: `["subprocess", "inline", "subagent"]` -- `cli` intentionally excluded.
- `backends` as object with `additionalProperties` pointing to `$defs/backendEntry`.
- `backendEntry` with `additionalProperties: false`, `required: ["type"]`, and `allOf` with three independent `if/then` pairs for api/anthropic+openai/vertex conditional requirements.
- All loader-accepted backend fields declared: type, format, command, model, max_tokens, base_url, api_key_env, project_id, region, credentials_path, default.
- `test`, `non_ascii`, `presubmit`, `graph_triage`, `daemon_state` sections with correct types and `additionalProperties` settings per loader behavior.
- `$comment` documents loader-only constraints (at-most-one default, cli alias exclusion).

### Task 2: Fixed docs/configuration.md, README.md, and setup docs

**Fiction A (backends.yaml wrong path)**: Replaced all occurrences across docs/ and README.md. Rewrote the `## backends.yaml` section as `## gate.yaml backends block`. Prose now correctly says `.code-forge/gate.yaml` under `backends:` key.

**Fiction B (list-format backends)**: All four YAML examples in configuration.md and the example in README.md converted from `- name:` list format to dict-keyed format with generic names.

**Fiction C (FORGE_OUTLET=cli)**: Replaced in docs/setup-vscode.md (both shell and JSON forms). Updated FORGE_OUTLET table in README.md to `subprocess | inline | subagent`. Rewrote FORGE_OUTLET section in configuration.md with all three outlet descriptions. Fixed `# no CLI subprocess` comment in README.md to `# no subprocess`.

**Additional fixes**:
- Removed `name:` column from API/CLI backend field tables (key IS the name).
- Added `max_tokens` and vertex-specific fields (`project_id`, `region`, `credentials_path`) to field tables.
- Added `gate.schema.json` subsection to configuration.md explaining VS Code/Cursor auto-honor and PyCharm manual registration.
- Added `## See also` sections to setup-cursor.md and setup-pycharm.md.
- Updated all "Related Documentation" `backends.yaml` references to `gate.yaml`.

## E-Gate Results

| Gate | Command | Result |
|------|---------|--------|
| E2 | load_backend_configs + load_gate_config on template | PASS -- 0 backends, no test section (expected) |
| E3 | Draft202012Validator.check_schema + validate({}) | PASS -- "gate.schema.json validates OK" |
| E6 | GATE_YAML_TEMPLATE.startswith(schema directive) | PASS -- "E6 PASS" |
| E2-post | grep pattern: (excluding source_patterns) | PASS -- zero output |
| E5-A | grep backends.yaml in docs/README/init_template | PASS -- zero output |
| E5-B | grep list-format backends | PASS -- zero output |
| E5-C | grep FORGE_OUTLET=cli (shell format) | PASS -- zero output |
| E5-D | grep "FORGE_OUTLET": "cli" (JSON format) | PASS -- zero output |
| E5-E | grep 'no CLI subprocess' | PASS -- zero output |
| E1 | Non-ASCII on gate.schema.json (direct) | PASS |
| E1 | Non-ASCII on init_template.py (git diff) | PASS |
| E1 | Non-ASCII on all changed docs (git diff) | PASS |
| Step 0a | py_compile init_template.py | PASS -- syntax OK |

## Deviations from Plan

**1. [Rule 3 - Blocking] Python 3.14 / pip version mismatch for jsonschema**

- **Found during:** E3 verification
- **Issue:** System `python3` is 3.14 but `pip3` targets Python 3.9 site-packages. `jsonschema` and `referencing` were installed into 3.9 site-packages and invisible to 3.14.
- **Fix:** Ran `python3.14 -m pip install "jsonschema>=4.18" "referencing"` to install into the correct site-packages.
- **Files modified:** None (environment fix only)
- **Commit:** N/A (no code change)

None -- plan executed exactly as written for code/doc changes.

## Known Stubs

None. This plan ships static files (template + schema + docs). No data wiring is involved. The schema deployment via `code-forge init` is explicitly deferred to 24-02 by plan design decision (F).

## Threat Flags

None. No new network endpoints, auth paths, file access patterns, or schema changes at trust boundaries introduced.

## Self-Check: PASSED

- src/code_forge/init_template.py: modified, committed in 6dd0539
- src/code_forge/gate.schema.json: created, committed in 6dd0539
- docs/configuration.md: modified, committed in 6dd0539
- docs/setup-vscode.md: modified, committed in 6dd0539
- docs/setup-cursor.md: modified, committed in 6dd0539
- docs/setup-pycharm.md: modified, committed in 6dd0539
- README.md: modified, committed in 6dd0539
- Commit 6dd0539 exists: confirmed via git log
