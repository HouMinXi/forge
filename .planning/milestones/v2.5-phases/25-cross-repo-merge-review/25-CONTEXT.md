# Phase 25: Cross-Repo Merge Review - Context

**Gathered:** 2026-06-17
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 25 delivers a single capability: a logical change that physically spans
>=2 git repos is reviewed as one joint unit. The user declares which repos and
what diff range constitute the joint change; forge fetches both diffs, assembles
a unified review context, runs parallel review StateMachines, and emits one
joint verdict plus per-repo receipts.

**What changes:**
- `code-forge review` can now accept sibling repo declarations via `gate.yaml`
- A cross-repo invocation produces one verdict and N receipts (one per repo)
- The primary repo's verdict determines pass/fail; sibling findings are advisory

**What does NOT change:**
- Single-repo invocations produce identical output to pre-v2.5 behavior (zero drift)
- Phase 26 (contract context injection) and Phase 27 (cross-repo impact via graph)
  are out of scope here
- Sibling repo's own `gate.yaml` (if it exists) is fully ignored in Phase 25
- R1/R2/R3 enforcement mechanics are unchanged

</domain>

<decisions>
## Implementation Decisions

### Declaration Mechanism
- **D-01:** Siblings are declared in `gate.yaml` under a new `siblings:` top-level key.
  Schema extended from Phase 24's `gate.schema.json`.
- **D-02:** Minimum required fields per sibling entry: `repo:` (path) and `ref:`
  (baseline..head range, e.g. `main..feature`). Optional field: `label:` (friendly
  display name used in verdict output and finding prefixes; defaults to the repo
  directory name if absent).
  ```yaml
  siblings:
    - repo: ../forge-plugin
      ref: main..feature-x
      label: plugin
  ```
  NOTE: Phase 25 v1 requires sibling repos use the same primary language as the
  primary repo (see D-09). A C-language sibling (e.g. kernel-net-next) is NOT
  supported in v1 -- detect.py:339 has no C parser route.
- **D-03:** `repo:` paths are resolved relative to the directory containing `gate.yaml`
  (not cwd). This matches `conventions_resolver.py`'s `resolve_sources()` behavior.
  Symlink guard from `resolve_sources()` is reused directly -- no new path-safety code.

### Diff Acquisition
- **D-04:** forge runs `git diff baseline..head` directly inside each sibling repo path.
  Same mechanism as the primary repo's diff acquisition.
- **D-05 [REVISED R3]:** v1 is local-only. If `repo:` starts with `https://` or
  `git@`, `validate_siblings()` rejects it with a clear ValueError. Remote repo
  support (shallow clone) is deferred to a future phase. Plans correctly implement
  this narrowed scope.
- **D-06:** Any sibling diff acquisition failure (invalid ref, missing branch, network
  error) -> fail-closed: emit a clear error identifying which sibling failed and
  terminate the entire review. Never silently drop a declared sibling.

### Context Assembly
- **D-07:** Review prompt is structured as two layers:
  1. **Summary header** (inserted before the diff blocks): for each repo, one line
     listing the files changed and +/- line counts. Gives the reviewer global scope.
  2. **Labeled diff blocks**: each diff block is preceded by a `## Repo: [label]
     (ref)` heading. L0 parser findings are tagged to their block's repo.
- **D-08:** File paths in the review context include the repo label prefix:
  `[forge] src/machine.py:123`. This is how L0 parsers, L1 reviewers, and
  falsifiers know which repo a file belongs to.
- **D-09 [REVISED R-01 2026-06-17]:** Phase 25 v1 is **same-stack-only**: L0 parsers
  run identically for the primary and all sibling repos using the language detected
  from the PRIMARY repo's diff (detect.py:339 returns one `language` field --
  "python" or "shell" -- with no per-path or per-repo route). A sibling whose
  files require a different toolchain (e.g. C kernel code, which would need
  checkpatch, not pylint/ruff/flake8) is REJECTED at gate config load with a clear
  error. Multi-language sibling support is explicitly DEFERRED (see Deferred Ideas).

### Finding Attribution
- **D-10:** Finding format: `[label] file/path:line -- description`. The `[label]`
  prefix is added only in cross-repo mode. Single-repo mode: format unchanged.
- **D-11:** Single-repo invocation (no `siblings:` in gate.yaml or siblings list
  is empty): all output is byte-for-byte identical to pre-v2.5. No label prefix,
  no summary header, no behavioral change whatsoever.
- **D-12:** Verdict output groups findings by repo:
  ```
  === [forge] ===
    CONFIRMED: src/machine.py:123 -- ...
  === [kernel] ===
    CONFIRMED: net/core/dev.c:45 -- ...
  ```
- **D-13:** When a finding spans code in two repos, it is attributed to the repo that
  contains the finding's first referenced file/line. No cross-repo root-cause analysis
  is attempted in Phase 25.

### Architecture: StateMachine Parallelism
- **D-14:** One StateMachine instance per repo, running concurrently via Python
  `threading`. Execution time = slowest StateMachine. This matches the existing
  threading model in `machine.py`.
- **D-15:** Each thread creates its **own** `backend` and `falsifier` instances
  (not shared). Thread safety is achieved through isolation, not locking. Gate.yaml
  backend config (endpoint, credentials) is read once at startup and passed as
  immutable parameters to each thread's factory call.
- **D-16:** All StateMachine instances share the primary repo's `gate.yaml` backend
  configuration (model, outlet, thresholds). Sibling repos do not have their own
  gate config in Phase 25.
- **D-17:** Verdict merge rule:
  - Primary repo FAIL -> joint verdict = FAIL (exit code per existing exit_codes.py)
  - Sibling FAIL -> advisory warning appended to joint verdict, but joint verdict
    reflects primary repo's outcome. A sibling-only FAIL does NOT block the primary
    repo's pass.
  - All repos PASS -> joint verdict = PASS
  - **DECISION (user, 2026-06-17, resolves R-02): option (b) -- the primary repo
    is authoritative for pass/fail; siblings are advisory.** Rationale: the change
    under review has its center of gravity in the primary repo; siblings are
    coordination context, not co-owners of the gate, and a sibling must never
    silently flip a primary PASS into a FAIL (this also keeps the single-repo
    path's pass/fail semantics intact). CROSS-01's "verdict reflects both" is
    satisfied in the SURFACING sense: both repos' findings appear and are grouped
    in the one joint verdict (D-12) -- "reflects" means both are shown, not that
    either failure blocks. Tradeoff accepted: a CONFIRMED bug in a sibling does
    not block the merge; it is surfaced loudly as an advisory warning, and acting
    on it is the author's call.
- **D-18:** If a sibling repo has its own `gate.yaml`, it is silently ignored.
  Phase 25 does not implement gate.yaml layering.
- **D-19 [REVISED R3]:** Receipt files: one set per repo. Existing receipts are
  `receipt-cNpM.json` (cycle N, pass M -- receipt.py:5). Step 8 copies and renames
  with label prefix: `{label}-receipt-cNpM.json` (e.g. `plugin-receipt-c1p1.json`).
  Glob pattern `receipt-*.json` catches both formats.

### Review Pipeline per Repo (R3 Q3/Q4 resolution)
- **D-21 (Q3 resolution):** L1 (LLM review) runs ONCE on the joint diff, on
  the PRIMARY thread only. Sibling threads get `git_diff=per_repo_diff` (their
  own diff) and a no-op `l1_provider` (lambda returning `([], [], Usage(), 0.0)`).
  Rationale: L1 cross-repo reasoning requires seeing both diffs together (CROSS-01
  SC-1). Running L1 N times on the same joint diff wastes N x LLM cost and
  produces N duplicate findings. Primary carries the cross-repo L1 context;
  siblings contribute L0 findings only.
- **D-22 (Q4 resolution):** L2 (mutation) runs on the PRIMARY repo only. Sibling
  threads skip L2. Rationale: D-16 says siblings share the primary's gate config,
  but the primary's `test.command` (e.g., `pytest`) runs against the primary repo's
  code. Running it from a sibling's tmp cwd tests nothing useful -- the test suite
  lives in the primary repo. Sibling-specific test commands would require their own
  gate.yaml, which D-18 says is ignored in v1.
- **D-23 (F1 resolution -- Design B):** Each thread gets a tmp cwd for state
  isolation (no pollution of real repos' `.code-forge/`), PLUS:
  (a) `source_files` = that repo's changed files resolved to ABSOLUTE paths
      (via `get_changed_files(per_repo_diff)` then `.resolve()` against repo root).
      source_files MUST NOT be []. That was what disabled L0.
  (b) Primary's `gate_config` dict (already loaded by Plan 04's yaml.safe_load)
      is written into `tmp_cwd/.code-forge/gate.yaml` via yaml.safe_dump.
      No second disk read -- eliminates TOCTOU. gate_config param is CONSUMED.
  (c) L0 linters read absolute paths correctly from any cwd (runner.py:131-134:
      `subprocess.run(cmd + files)` with no `cwd=`, files appended verbatim).
  (d) StateMachine gets `resolved_for_sm` with `git_diff=per_repo_raw_diff`.
      L1 gets `resolved_for_l1` with `git_diff=joint_diff` via a separate
      ResolvedReview passed to `build_l1_provider()`. This decouples L1's
      cross-repo reasoning from L0/L2/coverage/e2e which all parse raw diff.
      (machine.py reads resolved_review.git_diff in 6 distinct sites -- markdown-wrapped
      joint_diff with `## Repo:` headers would break density, mutation, coverage.)

### Testing
- **D-20:** Integration tests use `pytest`'s `tmp_path` fixture to create two real
  temporary git repos (git init, commits, branches). This validates actual git diff
  acquisition, not just mock strings. Pattern follows existing test infrastructure
  in `tests/test_install_hooks.py` (GIT_CEILING_DIRECTORIES isolation).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Core modules (read before touching any of them)
- `src/code_forge/conventions_resolver.py` -- `resolve_sources()` path resolution +
  symlink guard to reuse for sibling path validation (D-03)
- `src/code_forge/machine.py` -- StateMachine: the review flow each repo runs
  independently (D-14); `_execute_round`, `run()`, threading model
- `src/code_forge/factories.py` -- `build_l1_provider()`, `build_falsifier()`,
  advisory runner constructors; each thread calls these independently (D-15)
- `src/code_forge/cli.py` -- CLI entry point; where cross-repo mode is detected
  and dispatched; Outlet A dispatch at `_run_hold_loop` (line ~1549)
- `src/code_forge/outlet_c.py` -- reference for multi-leg StateMachine construction
  pattern (how run_outlet_c assembles registry + falsifier + runners)
- `src/code_forge/receipt.py` -- receipt write format to follow for per-repo receipts (D-19)
- `src/code_forge/flow_contract.py` -- canonical threshold constants (from Phase 24.1)
- `src/code_forge/gate_check.py` -- schema validation entry point; siblings section
  must pass through here

### Schema and config
- `src/code_forge/gate.schema.json` -- extend with `siblings:` array definition;
  each entry: `repo` (string, required), `ref` (string, required), `label` (string,
  optional) (D-01, D-02)

### Test infrastructure
- `tests/test_install_hooks.py` -- GIT_CEILING_DIRECTORIES isolation pattern to
  follow for tmp_path git repo fixtures (D-20)
- `tests/test_schema_corpus.py` -- schema round-trip test pattern; add siblings
  corpus entries here (valid + invalid snippets)

### Phase context from prior phases
- `.planning/phases/24.1-outlet-alignment/24.1-CONTEXT.md` -- flow contract,
  outlet alignment decisions; Phase 25 builds on this aligned base
- `.planning/phases/24-config-legibility/24-CONTEXT.md` -- gate.yaml
  self-documentation decisions; siblings section must follow the same
  inline-comment + schema pattern

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `resolve_sources(cwd)` in `conventions_resolver.py`: already implements symlink
  guard, relative-path resolution from a given root, and deduplication. Phase 25
  should call this or extract `_resolve_paths_as_sources()` for sibling path
  validation rather than re-implementing safety checks.
- `compute_source_hash(git_diff=...)` in `source.py`: accepts a unified diff string;
  will be called once per sibling repo diff for state tracking.
- `StateMachine` constructor in `machine.py`: takes injected runners/backend/falsifier;
  already designed for dependency injection, making per-thread instantiation clean.
- `build_l1_provider()` and `build_falsifier()` in `factories.py`: called per thread
  with shared config params (D-15).
- Receipt writing in `receipt.py`: follow existing format; only the filename prefix
  changes in cross-repo mode.

### Established Patterns
- **Dependency injection**: StateMachine and runners are constructed via factory
  functions, not global singletons. This makes per-thread instantiation straightforward.
- **Fail-closed**: all existing error paths in forge fail loudly with clear messages
  (never silently degrade). D-06 follows this same pattern.
- **Backward-compat guard**: Phase 24.1's flow_contract drift test is the model for
  ensuring the single-repo path stays identical.

### Integration Points
- `cli.py`: cross-repo mode is detected here (siblings present in loaded gate config)
  and dispatched to a new `run_cross_repo()` function or equivalent. The existing
  `_run_hold_loop` path for Outlet A is the single-repo fallback and must not change.
- `gate_check.py`: `load_gate_config()` needs to parse and validate the new
  `siblings:` section; validated sibling configs flow into the diff acquisition step.
- `machine.py`: no changes required to StateMachine itself -- the parallelism wrapper
  lives above it (in cli.py or a new `cross_repo.py` module).

</code_context>

<specifics>
## Specific Ideas

- **Verdict merge example** (from discussion):
  Primary FAIL + sibling WARN -> show both sections, joint exit = primary's exit code.
  Primary PASS + sibling FAIL -> show `[kernel] WARNING: 2 findings` below the
  PASS banner; joint exit = 0 (primary passed).

- **Label defaulting**: if `label:` is absent, use the last path component of
  `repo:` (e.g. `../kernel-net-next` -> `kernel-net-next`). Primary repo label =
  `primary` or the basename of the current working directory.

- **Summary header example** (D-07):
  ```
  Cross-repo review: forge (main..feature) + kernel (main..fix-xyz)
  forge:  3 files changed, +45/-12 (src/machine.py, src/cli.py, tests/test_machine.py)
  kernel: 2 files changed, +18/-5  (net/core/dev.c, include/linux/net.h)
  ```

</specifics>

<deferred>
## Deferred Ideas

- **Full remote URL support** (Phase 25: local first, shallow clone as fallback).
  A proper remote workflow (sparse checkout, credential management) is a future phase.
  Note: R-03 (reviewer) flags the shallow-clone fallback as a potential trust-gate gap
  (arbitrary git clone from gate.yaml `repo:` URL). Plan must handle: either restrict
  v1 to local paths only, or add `repo:` URL to the trust gate's dangerous-field list.
- **Multi-language siblings** (R-01 DEFERRED): Phase 25 v1 limits to same-stack siblings.
  A future phase can add per-repo language detection once detect.py supports multi-language
  routing. The canonical example was updated from kernel-net-next (C) to a Python sibling.
- **Sibling gate.yaml layering**: sibling repos having their own gate config that
  merges with the primary. Explicitly out of scope for Phase 25 (D-18).
- **asyncio-based parallelism**: current decision is threading (D-14). Migration to
  asyncio is deferred -- no pressure until connection count becomes a bottleneck.
- **Phase 26: Cross-Repo Contract Context** -- inject sibling repo's spec as
  read-only reviewer reference. Already in ROADMAP; can land before Phase 25 per
  ROADMAP note.
- **Phase 27: Cross-Repo Impact via register** -- advisory finding for symbol changes
  that affect sibling call sites via code-review-graph. Already in ROADMAP.

</deferred>

<known_residuals>
## Known v1 Residuals (accepted, fix tracked elsewhere)

- **L1 truncation false-green (MEDIUM-1, tracked in Phase 25.1):** A response
  that parses as valid JSON with findings==[] but carries >=1 well-formed
  code_excerpt passes validate_reviewer_json and is treated as a genuine clean
  pass. No coverage check verifies excerpts cover every changed file. This is a
  PRE-EXISTING single-repo bug (factories.py / reviewer_json.py / llm_invoke.py);
  Phase 25 amplifies it because the primary thread runs L1 on a ~2x joint diff
  (D-21) and its verdict alone determines the joint result (D-17). Gross failures
  (parse error, invoke error, all-empty response) are already fail-closed
  (factories.py:295-327, reviewer_json.py:69-73, machine.py:120-123). Fix:
  Phase 25.1 Option B (coverage backstop) + Option A (stop_reason plumbing).
  Full spec: /tmp/draft_l1_truncation_guard_20260619_101428.txt. Phase 25 ships
  with this residual documented; no code change needed in Phase 25 scope.
</known_residuals>

<reviewer_notes>
## R3 RESOLVED (sub-session, 2026-06-18): All findings addressed.
See 25-REVIEWS-R3.md for original findings. Resolution:
- F1 [BLOCKER]: Design B adopted (D-23). source_files = absolute paths per repo.
  gate.yaml seeded into primary's tmp cwd. L0/L2 restored.
- F2 [HIGH]: PENDING from primary escalated to FAIL in cross-repo (Plan 03 Step 7).
- F3 [MEDIUM]: gate_config consumed by make_per_repo_cwd gate_yaml_path seed (D-23b).
- F4 [LOW]: D-05 narrowed to local-only, D-19 to cNpM format, Verdict cosmetic fixed.
- Q3: L1 runs ONCE on joint diff, primary thread only (D-21).
- Q4: L2 primary only; siblings skip L2 (D-22).
- CLI chore: --state-dir + --staged removal folded into Plan 04 Task 3.
- Plan 05: test_l0_runs_on_each_repo added (F1 regression guard).
- VALIDATION.md: D-23a + F2 tests added.

## Reviewer Notes (main session review, 2026-06-17)

Independent review against codebase ground truth (file:line verified, not
prose-only). The skeleton is sound -- resolve_sources() reuse is real
(conventions_resolver.py:84 + symlink guard at :60), fail-closed is
consistent, and the single-repo zero-drift guard (D-11) is the right spine.
The items below are corrections, not a teardown. Resolve R-01 and R-02 BEFORE
/gsd-plan-phase 25; fold R-03..R-05 into the PLAN.

### R-01 [RESOLVED 2026-06-17] D-09 per-repo multi-language auto-detect does not exist
Claim (D-09): "each repo's L0 parsers run independently with auto-detected
language stack ... primary Python, sibling C -> correct parser set activates
for each independently."
Ground truth -- detect.py:339:
    language = "python" if has_python else ("shell" if has_shell else "python")
detect_toolchain returns ONE `language` field per invocation: only "python" or
"shell", and everything non-shell falls back to "python". There is no
multi-language and no per-path detection. parsers/ ships a checkpatch (kernel
C) parser, but detect.py NEVER auto-selects it -- a C sibling auto-detects as
"python" and runs pylint/ruff/flake8 on .c files (noise/errors). The D-02
headline example (../kernel-net-next, C) breaks against this.
Fix: scope Phase 25 v1 to same-stack siblings (a C sibling requires an explicit
tools.yaml, NOT auto-detect), OR explicitly defer multi-language siblings. The
plan must not assume per-repo language auto-detection.
RESOLUTION: same-stack-only for v1. D-09 revised. Multi-language deferred. D-02
example updated to Python sibling (forge-plugin). Planner must add same-stack
validation at gate config load time.

### R-02 [RESOLVED 2026-06-17] D-17 narrows CROSS-01 "verdict reflects both"
RESOLUTION: user chose option (b) -- primary-authoritative, siblings advisory.
D-17 updated with the decision, rationale, and CROSS-01 reconciliation. No
further action; this item is closed.

REQUIREMENTS.md:30 (CROSS-01): "reviewed as one joint unit ... the verdict
reflects both." D-17: a sibling FAIL is advisory only; "a sibling-only FAIL
does NOT block the primary repo's pass." Consequence: a sibling with a
CONFIRMED bug + a clean primary -> joint verdict = PASS. The findings appear
(SC#2 satisfied), but the verdict does not reflect the sibling failure.
This is a legitimate design choice (primary-authoritative), but it narrows the
requirement intent -- a strategic-intent question, not the sub-session's to
settle silently. Decide explicitly: does "one joint unit" mean (a) a confirmed
bug in EITHER repo fails the change, or (b) primary is authoritative and
siblings are advisory? Record the answer + rationale, and reconcile D-17
against CROSS-01's wording. The user owns this call.

### R-03 [PLAN-time] D-05 remote clone: scope creep + trust-gate gap
D-05 makes `git clone --depth=1 --filter=blob:none` an in-scope fallback, but
<deferred> defers "full remote URL support" -- the shallow/full boundary is
fuzzy. The ROADMAP success criteria mention only local baseline..head diffs;
remote clone maps to no SC. Security: `repo:` comes from gate.yaml, so D-05
will git-clone an arbitrary URL from config -- a new field that triggers
network + subprocess, on par with api_key_env/base_url, and it should pass the
trust gate's dangerous-field list (not mentioned).
Fix: v1 local-only, defer remote entirely; OR if kept, add `repo:` (when it is
a URL) to the trust-gate dangerous fields and state the network/threat model.

### R-04 [PLAN-time] D-14 "matches existing threading model" is inaccurate
machine.py:385 spawns threading.Thread(target=_async_mutation, daemon=True) --
a background R2-mutation worker WITHIN a single review, not a parallel-
StateMachine harness. So "one StateMachine per repo ... matches the existing
threading model" overstates reuse: cross-repo N-StateMachine parallelism is
net-new. Deeper: each StateMachine spawns its own _async_mutation daemon that
writes a result file (machine.py:378); N concurrent StateMachines sharing the
primary .code-forge dir may collide on those mutation result paths. Analyze and
isolate per-repo mutation/result paths in the plan.

### R-05 [PLAN-time] D-19 label / receipt-filename collision
Label defaults to the repo basename (Specific Ideas). Two siblings whose paths
share a basename (e.g. two different .../net-next dirs) -> same label ->
`{label}-receipt-rN.json` overwrite; a label literally "primary" collides with
the primary receipt. Add label-uniqueness validation (reject duplicate or
reserved labels at gate-config load).

### R-06 [LOW] mechanical
- Canonical ref says _run_hold_loop "line ~1549"; actual is cli.py:1551 (the
  "~" makes this harmless; noted for accuracy).
- This doc's local D-16 ("share primary backend") collides by number with the
  global locked anchor D-16 ("no model self-assessment") referenced in
  28-CONTEXT. Phase-local D-numbering is the established convention, so this is
  not an error -- just be aware when cross-referencing D-NN across phases.

</reviewer_notes>

---

*Phase: 25-Cross-Repo-Merge-Review*
*Context gathered: 2026-06-17*
*Reviewed: 2026-06-17 (see Reviewer Notes above)*
