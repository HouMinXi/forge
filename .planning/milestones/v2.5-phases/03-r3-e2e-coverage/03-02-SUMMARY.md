---
phase: 03-r3-e2e-coverage
plan: 02
subsystem: e2e-check-layer2
tags: [e2e-coverage, components-yaml, layer-2-cooccurrence, schema-validation]
dependency_graph:
  requires: [03-01-e2e-check-foundation]
  provides: [components-yaml-loader, layer-2-cooccurrence-trigger, e2e-artifact-matching, layer1-layer2-dedup, components-config-error]
  affects: [e2e_check.py-run_e2e_check, errors.py-typed-errors]
tech_stack:
  added: [yaml-safe-load-config, three-color-dfs-cycle-detection, pathlib-recursive-glob]
  patterns: [opt-in-config, sole-scoping-primitive, commutative-pair-fingerprint, surface-config-error-as-finding]
key_files:
  modified:
    - src/forge/e2e_check.py
    - src/forge/errors.py
decisions:
  - ComponentsConfigError is a dedicated typed error in errors.py (not reused from gate_check); error messages are a contract (prefixed "components.yaml: ", carry one keyword of undefined/self-reference/cycle/version/length 2)
  - Layer 2 is opt-in via .forge/components.yaml; absent file returns None and only Layer 1 runs
  - Schema validated at load time; depends_on traversal is one-level only, but cycles/self-reference/undefined refs are rejected up front
  - check_layer_2 fires UNCERTAIN P2 only on co-occurrence (hub touched AND a touched dependent has no satisfying e2e artifact); hub-only changes are left to Layer 1
  - _artifact_satisfies_pair is the sole per-pair scoping primitive; both arms route through it
  - e2e_absent_ok suppresses P2s symmetrically for either endpoint (hub-spoke and peer data_path arms)
  - sorted_pair_hash makes (a,b)/(b,a) and depends_on/data_paths duplicates collapse to one "e2e-l2:" fingerprint
  - Layer 1 nudge is dropped whole-diff when Layer 2 fires (flagged simplification; valid while Layer 1 emits one finding per diff)
  - A malformed components.yaml surfaces as one UNCERTAIN finding, never a crash
status: complete
metrics:
  completed_at: "2026-05-26"
  tasks_completed: 3
  files_modified: 2
  commits: 1
---

# Phase 03 Plan 02: E2E Check Layer 2 Summary

Filled the check_layer_2 stub with the opt-in components.yaml loader, load-time
schema validation, the hub-and-spoke + peer co-occurrence trigger with per-pair
e2e artifact matching, and the Layer 1 / Layer 2 deduplication inside
run_e2e_check. This is the enforceable-on-opt-in half of R3.

## What Was Built

**Objective:** Turn a declared component graph (.forge/components.yaml) into
specific UNCERTAIN findings when a cross-component change ships without an
integration test, and dedup the Layer 1 nudge when Layer 2 covers the change.

**One-liner:** Opt-in components.yaml drives a co-occurrence trigger that emits
an UNCERTAIN P2 when a touched hub and a touched dependent have no e2e artifact
under the dependent's paths, with an e2e_absent_ok escape hatch and config
errors surfaced (never crashed).

### Completed Tasks

| Task | Description | Commit | Files |
|------|-------------|--------|-------|
| 1 | ComponentsConfigError + components.yaml loader + schema validation | 9cc3b95 | errors.py, e2e_check.py |
| 2 | Layer 2 co-occurrence trigger + e2e artifact matching | 9cc3b95 | e2e_check.py |
| 3 | run_e2e_check loads config + Layer 1/Layer 2 dedup | 9cc3b95 | e2e_check.py |

### Key Changes

**errors.py:**
- Added `ComponentsConfigError(Exception)`, raised when .forge/components.yaml
  fails schema validation.

**e2e_check.py:**
- `load_components_yaml(repo_root) -> dict | None`: returns None when the file
  is absent (opt-in); yaml.safe_load; load-time validation of version (== 1),
  paths presence, depends_on targets, self-reference, cycles, e2e_absent_ok
  components, and data_paths (each entry length 2, both names defined). Every
  failure raises ComponentsConfigError with a "components.yaml: " message that
  names the offending key. Type guards reject a non-list e2e_absent_ok /
  data_paths and a non-mapping entry with a clear error instead of an opaque
  AttributeError. e2e_patterns defaults to ["tests/e2e/**", "test_*integration*"].
- `_detect_cycles`: three-color DFS over the depends_on graph; an undefined
  reference is treated as an invariant violation (fail-fast) rather than
  silently skipped, since depends_on targets are validated before this runs.
- `sorted_pair_hash(a, b)`: commutative sha256-based 16-hex fingerprint.
- `find_e2e_artifacts(repo_root, patterns) -> set[str]`: recursive glob via
  pathlib (** support); returns repo-relative POSIX paths (never mixes Path
  and str).
- `_artifact_satisfies_pair(artifacts, component_paths) -> bool`: the sole
  per-pair scoping primitive (fnmatch); both trigger arms call it.
- `check_layer_2(diff_text, repo_root, components=None)`: returns [] when
  components is None; extracts the name->paths map before grouping and filters
  out fallback (non-component) groups; computes hubs by reverse-scanning
  depends_on; hub-and-spoke arm fires only when both hub and a dependent are
  touched and the dependent has no in-paths e2e artifact; peer data_path arm
  fires when both endpoints are touched and neither endpoint has an artifact;
  e2e_absent_ok skips either endpoint on both arms; findings are
  source="E2E_CHECK", disposition=UNCERTAIN, fingerprint "e2e-l2:<hash>",
  deduped by fingerprint.
- `run_e2e_check(diff_text, repo_root)`: loads components.yaml; a
  ComponentsConfigError becomes one "e2e-config-error" UNCERTAIN finding and
  Layer 1 still runs on default grouping; extracts the name->paths map for
  Layer 1 (never passes the full validated dict to group_source_files); runs
  both layers; drops the Layer 1 finding when Layer 2 fires; the whole body is
  wrapped so any unexpected error degrades to ([], infra_errors).

## Verification Results

**Step 0 (automated, passed):**
- ruff check: clean on e2e_check.py and errors.py.
- non-ASCII: clean on the committed diff.
- py_compile: both files parse.

**Plan verification blocks (per-task + final):**
- ComponentsConfigError importable and an Exception subclass; load_components_yaml
  returns None for a nonexistent root; check_layer_2(..., components=None) == [];
  run_e2e_check("", root) returns a 2-tuple. All pass.

**Behaviors confirmed:**
- Schema validation rejects undefined depends_on, self-reference, cycles,
  unknown e2e_absent_ok component, data_paths entry length != 2, and unknown
  data_paths component, each with a naming "components.yaml: " message.
- Layer 2 fires one UNCERTAIN P2 on hub+touched-dependent with no in-paths e2e
  artifact; an in-paths artifact suppresses it; a hub-only change does not fire.
- A pair expressed in both depends_on and data_paths dedups to one finding.
- A malformed components.yaml yields one e2e-config-error UNCERTAIN finding with
  Layer 1 still running; no crash.

**Regression:** 640 tests pass.

## Notes

- Review for this plan ran in the implementing sub-session rather than a separate
  reviewer session. It hardened schema validation (type guards, fail-fast cycle
  detection) and rewrote in-code comments that referenced planning artifacts into
  plain technical descriptions -- that cleanup also covered the Layer 1 comments
  carried in from plan 03-01 (same file, comments only, no logic change). The
  main session independently re-verified the locked contracts and read the
  check_layer_2 / run_e2e_check control flow.
- The functional and regression results above were produced by the sub-session's
  review run; the main session confirmed structure and logic by inspection but
  did not independently re-execute the suite for this plan.
- Layer 2 is built but not yet wired into the state machine; plan 03-03 injects
  build_e2e_checker into machine.py.
