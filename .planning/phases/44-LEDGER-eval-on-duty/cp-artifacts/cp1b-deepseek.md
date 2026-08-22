# Phase 44 EVAL-ON-DUTY: External Adversarial Plan Review (CP1b)
Reviewer: DeepSeek
Subject: Phase 44 (Plans 44-01, 44-02, 44-03) against forge codebase @ 8bd01bc

## Axis 1: Design correctness vs real code
- [M] Hypothesis: `_create_gate_yaml` (eval/runner.py:522) merge logic hazard. D-17 demands stripping the foreign `gate.yaml` from the *diff itself* before emitting the corpus entry. However, if the corpus entry's base commit *already* contains a `.code-forge/gate.yaml` with a hostile `test.command`, `_create_gate_yaml` will load it, and since it only defaults the `backends` section, the existing `test.command` will survive and execute during replay. The patch stripping only guards against foreign modifications, not foreign base states.
  - *Rationale: `git apply` applies the stripped diff, but if the file existed previously, `gate_path.exists()` is true and the old hostile command is loaded and kept.*
  - *File/Line: `src/code_forge/eval/runner.py:539`*

- [L] Hypothesis: `_count_coverage_gaps` and pinning logic (D-26). Task 44-03 T2 states pinned paths suppress findings by matching file paths against `pinned_paths` and appending to `infra_errors`. `machine.py:_run_ci` currently counts coverage gaps separately (`coverage_gaps = self._count_coverage_gaps()`, `machine.py:527`). Will a pinned path that lacks review coverage still trigger a coverage gap failure, bypassing the intent of the pin?
  - *Rationale: D-26 only specifies finding suppression, not coverage calculation adjustment. If a pinned path has no testing, `coverage_gaps > 0` will still fail the run.*
  - *File/Line: `src/code_forge/machine.py:527`*

## Axis 2: Concurrency/atomicity
- [M] Hypothesis: Stale SHA resolution against `resolve_ledger_root(cwd)`. D-09/D-15/D-20b require reading `base_sha` and `head_sha` against `row.repo_root`. When CI is run concurrently across multiple worktrees, `row.repo_root` points to the main repo's common dir. Since different worktrees can be at different SHAs, `git cat-file -e` in the main repo root might fail to resolve a SHA if the git GC or prune has swept it, or if it was a worktree-local experimental commit not yet in the main object store. This violates the assumption that resolving against the main `repo_root` guarantees object existence.
  - *Rationale: Shared object store (`.git/objects`) holds everything, but if objects are pruned or not yet pushed from a worktree, resolution can fail. This is mostly safe but could lead to increased stale-sha counts in highly dynamic parallel environments.*

## Axis 3: CI-write insertion point
- [L] Hypothesis: `_persist_state()` race condition. `_write_ci_ledger_rows` is inserted *after* `_persist_state()` in `_run_ci` (machine.py:540). If the JVM/process crashes *during* `_write_ci_ledger_rows` (e.g., OOM), `state.json` reflects converged/failed, but the ledger misses the row. This violates the "single source of truth for terminal states" principle, leaving a state mismatch between the run's `state.json` and the global `ledger.jsonl`.
  - *Rationale: While `state.json` is ignored in CI (STATE-09), if read by external tools (or MCP response extractors), they might see a CONFIRMED finding that never made it to the ledger, preventing future suppression.*
  - *File/Line: `src/code_forge/machine.py:540`*

## Axis 4: Adjudicate inheritance model
- [M] Hypothesis: `pass_provenance` overwrite. D-10 specifies that `ledger adjudicate` inherits `file/line/axis_claim/base_sha/head_sha`, but sets `pass_provenance="adjudicated"`. The original L0/L1 pass provenance of the finding (e.g., "qodo-review" or "flake8") is permanently lost from the terminal ledger row. This reduces the usefulness of the eval corpus for measuring specific pass performance, as the exported entry will just say "adjudicated".
  - *Rationale: The `adjudicate` action replaces the origin signal with a human action signal, losing origin metadata.*
  - *File/Line: `44-01-PLAN.md:339` (Action for Task 3)*

## Axis 5: export-eval extractor
- [H] Hypothesis: False-green expectation inversion. D-02 states `DISPROVED/DUPLICATE` generate `expected_verdict="PASS"` and expect non-catch. But a `DUPLICATE` finding means the bug *was* real, it was just reported twice. If the corpus case expects `PASS` (no catch), and an LLM reviewer finds the bug (once), the evaluation scores it as a false-positive (because it expected NO catch), penalizing the LLM for finding a real bug!
  - *Rationale: `DUPLICATE` means the underlying issue is valid. Expecting `PASS` for the entire diff based on a duplicate finding asserts that the diff is clean, which is false.*
  - *File/Line: `44-02-PLAN.md:141`*

## Axis 6: Operability
- [L] Hypothesis: Error verbosity on Kill-switch. D-19 requires a stderr warning on OSError during ledger write. In a CI environment with restrictive filesystem permissions (e.g., read-only mounts for security), every single CI run will spam stderr with this warning, polluting logs and potentially breaking strict stderr-empty checks in some pipelines.
  - *Rationale: A missing directory permission might continuously warn.*
  - *File/Line: `44-01-PLAN.md:255`*

## Axis 7: What everyone missed
- [H] Hypothesis: `_truncate_evidence` invalidates JSON. Task 44-01 T1 (D-07/D-21) adds a `_truncate_evidence(text)` helper enforcing a 500-char cap. If `evidence_class` contains multi-byte UTF-8 characters (e.g., Chinese error messages or emojis), a naive `text[:485] + "... [truncated]"` might split a multibyte sequence, creating invalid UTF-8. Depending on how `json.dumps` handles it, this can either crash the write process or produce a corrupt line that causes `json.loads` to fail in `iter_rows`, quietly dropping the row.
  - *Rationale: String slicing in Python counts unicode code points, not bytes. But the D-07 requirement explicitly states "serialized row size < 2048 bytes (PIPE_BUF margin)". If Python slices 500 *code points* of 4-byte emojis, the string is 2000 bytes, which plus overhead exceeds 2048 bytes, breaking atomicity.*
  - *File/Line: `44-01-PLAN.md:180`*

- [B] Hypothesis: Style downgrade (D-27) table matching logic is highly brittle and risks false negatives. 44-03 Task 2 specifies matching via "L1 pass_names and/or description keywords". If this is a naive substring match against `description`, findings like "Missing type hint for *style* parameter" will be incorrectly downgraded to Advisory. Furthermore, if a finding has both a real bug and a style violation mentioned, it might be silenced.
  - *Rationale: Substring matching on unstructured LLM output for critical disposition routing is unsafe. D-25 explicitly rejected fuzzy topic matching for suppression because it is a "false-green risk". Applying fuzzy matching for style downgrades poses the exact same risk.*
  - *File/Line: `44-03-PLAN.md:247`*

SCORECARD: B=1 H=2 M=3 L=3
