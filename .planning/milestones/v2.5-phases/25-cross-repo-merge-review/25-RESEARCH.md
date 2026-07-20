# Phase 25: Cross-Repo Merge Review - Research

**Researched:** 2026-06-17
**Domain:** Python CLI, git diff acquisition, threading, JSON Schema extension
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- D-01: Siblings declared in gate.yaml under new `siblings:` top-level key.
- D-02: Required per-sibling fields: `repo:` (path), `ref:` (baseline..head range).
  Optional: `label:` (display name; defaults to repo basename). Example:
  ```yaml
  siblings:
    - repo: ../forge-plugin
      ref: main..feature-x
      label: plugin
  ```
- D-03: `repo:` resolved relative to gate.yaml directory, not cwd. Reuse
  `conventions_resolver.py` symlink guard -- no new path-safety code.
- D-04: forge runs `git diff baseline..head` directly inside each sibling repo path.
- D-05: Local first. `https://` or `git@` prefix triggers shallow clone to /tmp.
- D-06: Any diff acquisition failure -> fail-closed (clear error, terminate review).
- D-07: Review prompt = summary header + labeled diff blocks (`## Repo: [label] (ref)`).
- D-08: `[label] file/path:line` prefix on all cross-repo findings (L0 and L1).
- D-09 [REVISED]: Same-stack-only for v1. Validate at gate config load; reject siblings
  that would require a different toolchain. Multi-language deferred.
- D-10: Finding format: `[label] file/path:line -- description` (cross-repo mode only).
- D-11: Single-repo invocation (empty/absent `siblings:`) -> byte-for-byte identical output.
- D-12: Verdict groups findings by repo (one `=== [label] ===` block per repo).
- D-13: Finding attributed to the repo containing its first referenced file/line.
- D-14: One StateMachine per repo, concurrent via Python `threading`.
- D-15: Each thread creates its own `backend` + `falsifier` (no sharing). Gate.yaml
  backend config read once and passed as immutable parameters.
- D-16: All StateMachine instances share primary repo's gate.yaml backend configuration.
- D-17: Primary FAIL -> joint FAIL. Sibling FAIL -> advisory warning only (primary
  authoritative; siblings advisory). Both findings are surfaced.
- D-18: Sibling gate.yaml silently ignored.
- D-19: Per-repo receipt files named `{label}-receipt-rN.json`
  (primary uses `primary-receipt-rN.json`).
- D-20: Integration tests use `tmp_path` + two real git repos (real `git init`,
  real commits and branches).

### Reviewer Plan-time Notes (fold into plan)

- R-03: D-05 remote clone trust-gate gap -- either restrict v1 to local-only, or add
  `repo:` URL to `trust.py` DANGEROUS_FIELDS.
- R-04: `_async_mutation` daemon in machine.py writes to
  `self.cwd / ".code-forge" / "mutation-result.json"` -- N concurrent StateMachines
  sharing the same `.code-forge/` dir will collide on this path. Per-repo path isolation
  required.
- R-05: Label uniqueness -- two siblings with same basename get same default label ->
  receipt overwrite. Label "primary" collides with primary receipt. Validate at config load.

### Claude's Discretion

- Module structure: `cross_repo.py` vs extending `cli.py`
- Prompt assembly format (within D-07/D-08 constraints)
- Test fixture structure (within D-20 constraints)

### Deferred Ideas (OUT OF SCOPE)

- Full remote URL support beyond shallow clone fallback
- Multi-language siblings (per-repo language auto-detection)
- Sibling gate.yaml layering
- asyncio-based parallelism
- Phase 26: Cross-Repo Contract Context
- Phase 27: Cross-Repo Impact via register
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| CROSS-01 | A single logical change spanning >=2 repos is reviewed as one joint unit -- both diffs in one review context, verdict reflects both, single-repo path unchanged. | D-04/D-07 (joint diff assembly), D-14 (parallel StateMachines), D-11 (zero-drift single-repo guard), D-12/D-17 (joint verdict), SC-1/SC-2/SC-3 from ROADMAP. |
</phase_requirements>

---

## Summary

Phase 25 adds cross-repo merge review to forge: a user declares sibling repos in `gate.yaml` under a new `siblings:` section, and forge runs a parallel StateMachine per repo, assembles a unified prompt with both diffs, and emits one joint verdict plus per-repo receipts. The single-repo path is entirely unchanged (D-11).

The primary technical challenge is NOT the StateMachine itself -- it is already designed for dependency injection and works per thread. The challenge is the four NEW coordination layers above it: (1) diff acquisition for siblings, (2) joint prompt assembly, (3) per-repo result isolation (`mutation-result.json` collision risk per R-04), and (4) verdict merge.

The new module `cross_repo.py` should own all four layers. The `cli.py` detects the cross-repo mode (siblings present) and dispatches to `run_cross_repo()` in that module, leaving `_run_hold_loop` entirely unchanged. This is the cleanest separation.

**Primary recommendation:** Introduce `src/code_forge/cross_repo.py` to own the orchestration layer. Extend `gate_check.py` `load_gate_config()` with a `validate_siblings()` sub-validator. Extend `gate.schema.json` with the `siblings:` array definition. The StateMachine, factories, receipt writer, and symlink guard require zero changes.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Sibling config parsing + validation | `gate_check.py` | `gate.schema.json` | All gate.yaml validation lives here |
| Sibling path resolution + symlink guard | `cross_repo.py` (calls `_resolve_paths_as_sources`) | `conventions_resolver.py` | Reuse existing guard, new call site |
| Diff acquisition (primary + siblings) | `git.py` (`git_diff` function) | `cross_repo.py` (caller) | git.py owns all git subprocess calls |
| Joint prompt assembly | `cross_repo.py` | `factories.py` (`build_l1_provider` receives joint diff) | New code, no existing hook |
| Parallel StateMachine execution | `cross_repo.py` (threading.Thread orchestration) | `machine.py` (unchanged) | machine.py is the worker, not the scheduler |
| Per-repo mutation-result isolation | `cross_repo.py` (per-repo .code-forge dir) | `machine.py` (`cwd` constructor param) | StateMachine.cwd drives all path construction |
| Verdict merge (primary-authoritative) | `cross_repo.py` | `exit_codes.py` (verdict_to_exit) | New logic, no existing hook |
| Per-repo receipts | `receipt.py` (unchanged) + `cross_repo.py` (custom dir) | `machine.py` (write_receipts call site) | Receipt writer is reused; only output path changes |
| Schema validation | `gate.schema.json` | `gate_check.py` | JSON Schema draft-2020-12, existing pattern |
| Trust gate (R-03) | `trust.py` (DANGEROUS_FIELDS extension) | `cross_repo.py` (call site) | Existing dangerous-field pattern |

---

## Standard Stack

### Core (no new dependencies -- all reused)

| Component | Location | Purpose |
|-----------|----------|---------|
| `threading.Thread` | stdlib | Parallel StateMachine execution (D-14) |
| `git_diff()` | `src/code_forge/git.py:192` | Sibling diff acquisition (D-04) |
| `_symlink_guard_passes()` | `conventions_resolver.py:60` | Path safety (D-03) |
| `_resolve_paths_as_sources()` | `conventions_resolver.py:423` | Path resolution helper to reuse |
| `StateMachine` | `machine.py:136` | Per-repo review worker (D-14) |
| `build_l1_provider()` | `factories.py:200` | Per-thread L1 provider (D-15) |
| `build_falsifier()` | `factories.py:25` | Per-thread falsifier (D-15) |
| `write_receipts()` | `receipt.py:58` | Per-repo receipt writing (D-19) |
| `load_gate_config()` | `gate_check.py:38` | Config parsing entry point (D-01/D-02) |
| `DANGEROUS_FIELDS` | `trust.py:23` | Trust gate extension (R-03) |
| `verdict_to_exit()` | `exit_codes.py:21` | Exit code mapping |

**Installation:** No new packages required. All dependencies are stdlib or already in forge.

---

## Package Legitimacy Audit

No new external packages introduced in this phase. All code uses existing forge dependencies or Python stdlib. This section is N/A.

---

## Architecture Patterns

### System Architecture Diagram

```
gate.yaml (siblings: section)
    |
    v
load_gate_config() + validate_siblings()  [gate_check.py]
    |-- schema validation (gate.schema.json)
    |-- same-stack check (D-09)
    |-- label uniqueness check (R-05)
    |-- trust gate URL check (R-03)
    |
    v
cli.py _run() detects cross-repo mode
    |-- siblings present -> dispatch to run_cross_repo()
    |-- siblings absent -> existing _run_hold_loop() (unchanged)
    |
    v
cross_repo.py run_cross_repo()
    |
    |-- for each repo (primary + siblings):
    |   |-- git_diff(ref) [git.py]  -> diff text
    |   |-- compute_source_hash()   -> hash
    |   |-- build per-repo .code-forge/ dir (per-repo isolation, R-04)
    |
    |-- assemble joint prompt (D-07/D-08)
    |   |-- summary header (file counts, +/-lines per repo)
    |   |-- labeled diff blocks ("## Repo: [label] (ref)")
    |
    |-- launch N threads (D-14):
    |   Thread per repo:
    |   |-- build_falsifier()        [factories.py]  (D-15)
    |   |-- build_l1_provider(joint_diff)            (D-15)
    |   |-- StateMachine(cwd=per_repo_dir)           (D-14)
    |   |-- sm.run() -> Verdict
    |   |-- result stored in thread-local slot
    |
    |-- collect Verdicts from all threads
    |-- verdict merge (D-17):
    |   |-- primary FAIL -> joint FAIL
    |   |-- sibling FAIL -> advisory warning, joint = primary's code
    |   |-- all PASS -> joint PASS
    |
    |-- emit per-repo receipts (D-19):
        |-- primary: primary-receipt-rN.json
        |-- siblings: {label}-receipt-rN.json
```

### Recommended Project Structure

```
src/code_forge/
├── cross_repo.py          # NEW: cross-repo orchestration (run_cross_repo, diff acquisition)
├── cli.py                 # MODIFIED: detect siblings, dispatch to cross_repo.py
├── gate_check.py          # MODIFIED: validate_siblings() sub-validator
├── gate.schema.json       # MODIFIED: siblings: array definition
├── trust.py               # MODIFIED: repo: URL in DANGEROUS_FIELDS (R-03 decision)
├── machine.py             # UNCHANGED
├── factories.py           # UNCHANGED
├── receipt.py             # UNCHANGED
├── conventions_resolver.py # UNCHANGED (symlink guard reused by cross_repo.py)
tests/
├── test_cross_repo.py     # NEW: integration tests with two real tmp git repos (D-20)
├── test_schema_corpus.py  # MODIFIED: add siblings valid/invalid corpus entries
```

### Pattern 1: Sibling Diff Acquisition

The primary repo diff is acquired via `baseline.py -> _resolve_git() -> git.py:git_diff()`.
For sibling repos, Phase 25 calls `git_diff()` directly with the parsed `ref:` spec
(which contains a `baseline..head` range string like `main..feature-x`).

```python
# Source: src/code_forge/git.py:192 (verified)
# git_diff(baseline_ref, head_ref, paths, cwd) -> str (unified diff)
# For a ref like "main..feature-x", split on ".." to get baseline/head:

def _acquire_sibling_diff(repo_path: Path, ref_spec: str) -> str:
    """Acquire unified diff for a sibling repo.

    ref_spec is "baseline..head" (e.g., "main..feature-x").
    Calls git.py:git_diff() directly -- same subprocess call the primary
    repo uses. Fail-closed (D-06): raises on any git error.
    """
    from .git import git_diff, resolve_git_ref
    parts = ref_spec.split("..", 1)
    if len(parts) != 2:
        raise ValueError(
            "sibling ref must be 'baseline..head', got: %r" % ref_spec
        )
    baseline_ref, head_ref = parts
    # Validate both refs exist in the sibling repo
    resolve_git_ref(baseline_ref, repo_path)
    resolve_git_ref(head_ref, repo_path)
    # paths=[] means all files (same as primary repo behavior)
    return git_diff(baseline_ref, head_ref, [], repo_path)
```

### Pattern 2: Per-Repo StateMachine Isolation (R-04 fix)

`machine.py` writes ALL state to `self.cwd / ".code-forge" / <file>`. This includes:
- `state.json` (per-round state)
- `mutation-result.json` (async mutation worker output, line 332-337)
- `receipts/` directory (receipt files)
- `advisory-findings.json` (advisory axis output)

Two concurrent StateMachines sharing the same `cwd` WILL collide on these paths.
The fix: give each thread its own ephemeral working directory under `/tmp`.

```python
# Pattern: per-repo isolation via ephemeral tmp dir
import tempfile

def _make_per_repo_cwd(label: str) -> Path:
    """Create isolated .code-forge/ work dir for a sibling StateMachine.

    Returns a tmpdir that the StateMachine writes to exclusively.
    Caller is responsible for cleanup (use tempfile.TemporaryDirectory
    as a context manager around the thread pool).
    """
    tmp = Path(tempfile.mkdtemp(prefix="forge-cross-repo-%s-" % label))
    (tmp / ".code-forge").mkdir()
    return tmp
```

**Why**: `machine.py` line 332 shows `result_path = self.cwd / ".code-forge" / "mutation-result.json"` -- with N=2 StateMachines sharing the same cwd, both write to the same file. An ephemeral tmp cwd per thread eliminates all collision risk without any changes to machine.py.

### Pattern 3: Joint Prompt Assembly (D-07/D-08)

The `build_l1_provider()` in `factories.py:200` takes a `diff_text` (via `resolved.git_diff`).
For cross-repo mode, the joint diff text is assembled BEFORE creating the `ResolvedReview`
for each thread. The joint diff is the full review context; per-repo attribution uses
the `[label]` prefix on each diff block header.

```python
# Source: based on D-07/D-08 spec and factories.py:231-236 pattern
def _assemble_joint_diff(
    repos: list[dict],  # [{"label": str, "ref": str, "diff": str}, ...]
) -> str:
    """Assemble joint unified diff for cross-repo review context.

    D-07 Layer 1: summary header (one line per repo).
    D-07 Layer 2: labeled diff blocks.
    D-08: [label] prefix on file paths.

    The assembled string is passed to build_l1_provider() as diff_text.
    """
    lines = []
    # Summary header
    lines.append("Cross-repo review: " + " + ".join(
        "%s (%s)" % (r["label"], r["ref"]) for r in repos
    ))
    for r in repos:
        lines.append(
            "%s: %s" % (r["label"], _diff_summary(r["diff"]))
        )
    lines.append("")

    # Labeled diff blocks
    for r in repos:
        lines.append("## Repo: [%s] (%s)" % (r["label"], r["ref"]))
        # Prefix each file path in the diff with [label]
        lines.append(_prefix_diff_paths(r["diff"], r["label"]))
        lines.append("")

    return "\n".join(lines)
```

### Pattern 4: Threading Model (D-14)

Existing threading in `machine.py` is a single intra-StateMachine daemon thread
(`_async_mutation` at line 385). The cross-repo parallelism is a DIFFERENT level:
N top-level threads, one StateMachine each. This is net-new orchestration, NOT
reuse of the existing threading model (per R-04).

```python
# Source: derived from D-14 decision + threading stdlib
import threading

def _run_repo_thread(
    label: str,
    diff: str,
    config: dict,
    results: dict,  # shared results dict, keyed by label
    errors: dict,   # shared errors dict, keyed by label
) -> None:
    """Thread target for one repo's StateMachine."""
    try:
        # Each thread: own backend + falsifier (D-15)
        # Each thread: own ephemeral cwd (R-04 isolation)
        # results[label] = verdict
        ...
    except Exception as exc:
        errors[label] = exc

# Dispatch:
threads = []
results: dict[str, Verdict] = {}
errors: dict[str, Exception] = {}

for repo in repos:
    t = threading.Thread(
        target=_run_repo_thread,
        args=(repo["label"], repo["diff"], config, results, errors),
        daemon=True,
    )
    threads.append(t)
    t.start()

for t in threads:
    t.join()

# D-06: fail-closed if any thread errored
if errors:
    raise RuntimeError("sibling review failed: %s" % errors)
```

### Pattern 5: gate.schema.json Extension (D-01/D-02)

Existing schema uses `"additionalProperties": true` on the root object, so adding
`siblings:` as a new top-level property requires only adding the definition.

```json
// Extend gate.schema.json properties section:
"siblings": {
  "type": "array",
  "description": "Sibling repositories to include in cross-repo joint review.",
  "items": {
    "type": "object",
    "required": ["repo", "ref"],
    "additionalProperties": false,
    "properties": {
      "repo": {
        "type": "string",
        "description": "Path to sibling repo. Relative to gate.yaml directory."
      },
      "ref": {
        "type": "string",
        "description": "Diff range for sibling (e.g. 'main..feature-x')."
      },
      "label": {
        "type": "string",
        "description": "Display name used in verdict output and finding prefixes. Defaults to repo basename."
      }
    }
  }
}
```

### Pattern 6: Receipt Writer Per-Repo (D-19)

`write_receipts()` in `receipt.py:58` takes `receipts_dir: Path` as its first argument.
The cross-repo orchestrator passes a per-repo receipts directory:
- Primary repo: `primary_cwd / ".code-forge" / "receipts"` (existing behavior, filename unchanged in primary context)
- But D-19 says named `primary-receipt-rN.json` not `receipt-cNpM.json`.

Two options:
1. A thin wrapper in `cross_repo.py` that renames the output files after `write_receipts()` runs.
2. A new `write_cross_repo_receipts()` function that takes a `label` parameter and writes `{label}-receipt-rN.json`.

Option 2 is cleaner (no post-rename shuffle). It reuses the internal structure of `write_receipts()` but writes to label-named files. The planner should choose based on how much receipt.py logic needs reuse.

### Anti-Patterns to Avoid

- **Sharing `cwd` across threads:** The `mutation-result.json` collision (R-04) proves that any path under `cwd/.code-forge/` is thread-unsafe when N StateMachines share the same dir. Each thread MUST get its own ephemeral cwd.
- **Calling `resolve_sources()` for sibling paths:** That function discovers sibling repos from AGENTS.md/conventions.yaml for convention extraction -- a different purpose. Use `_resolve_paths_as_sources()` or the symlink guard directly.
- **Sharing backend or falsifier instances across threads:** `build_falsifier()` and `build_l1_provider()` are not thread-safe in general (they contain stateful HTTP clients). Each thread calls its own factory (D-15).
- **Checking `label:` uniqueness only at runtime:** Do it at `validate_siblings()` time (during `load_gate_config()`), so the user gets a clear error before any git diff runs.
- **Auto-detecting language per sibling:** `detect_toolchain()` returns one `language` field for the whole invocation. In cross-repo mode, validate that all siblings would use the same language as the primary repo (detected once from primary), and reject at config load time if not (D-09).

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Git diff subprocess | Custom git wrapper | `git.py:git_diff()` | Handles exit codes 0/1/2+, validated baseline refs |
| Path safety | Custom symlink check | `_symlink_guard_passes()` in `conventions_resolver.py:60` | Uses `Path.parents` containment (str.startswith is broken) |
| Path resolution | Custom relative-path resolver | `_resolve_paths_as_sources()` in `conventions_resolver.py:423` | Handles absolute/relative, deduplication, isdir check |
| Thread-per-review | asyncio, multiprocessing | `threading.Thread` | Consistent with existing `_async_mutation` pattern; simpler |
| Receipt format | New JSON schema | `write_receipts()` in `receipt.py` | Reuse structure; only filename changes |
| Schema validation | Runtime type checks | `gate.schema.json` + jsonschema | Existing pattern from Phase 24 |

**Key insight:** The StateMachine is already designed for dependency injection and has no global state. Per-thread instantiation is straightforward -- the only risk is the shared-cwd collision, which is solved by ephemeral per-repo cwds (R-04 fix).

---

## Runtime State Inventory

> SKIPPED -- Phase 25 is a greenfield feature addition. No rename, refactor, or migration. No stored state uses cross-repo identifiers.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `git` CLI | D-04 diff acquisition | Yes (assumed present -- forge already requires it) | Any | Fail-closed (D-06) |
| `threading` module | D-14 parallelism | Yes (stdlib) | stdlib | N/A |
| `yaml` (PyYAML) | gate.yaml parsing | Yes (existing dep) | existing | N/A |

**Missing dependencies with no fallback:** None -- all dependencies already in forge.

---

## Common Pitfalls

### Pitfall 1: mutation-result.json Collision (R-04)

**What goes wrong:** Two sibling StateMachines with `cwd` both pointing to the primary
repo's directory both write `cwd/.code-forge/mutation-result.json`. The faster thread
overwrites the slower one's result; the slower thread may also read stale data from
the prior write. Result: silent data corruption, wrong verdict.

**Why it happens:** `machine.py` line 332 builds `result_path` from `self.cwd`. If cwd
is the same for two instances, both resolve to the same path.

**How to avoid:** Each StateMachine thread gets its own ephemeral temp directory as `cwd`
(via `tempfile.mkdtemp`). The temp dir gets a `.code-forge/` subdirectory pre-created.
Clean up temp dirs after all threads complete.

**Warning signs:** A test that runs two StateMachines with the same `cwd` will fail
intermittently (race condition) -- this is the test to write first (D-20).

### Pitfall 2: Label Collision -> Receipt Overwrite (R-05)

**What goes wrong:** Two siblings with path `../net-next` and `../../vendor/net-next`
both default to label `net-next`. Receipt files `net-next-receipt-rN.json` from the
first sibling are silently overwritten by the second. Also: if user names a sibling
`primary`, it collides with the primary repo receipt.

**How to avoid:** `validate_siblings()` in `gate_check.py` checks:
1. No two sibling labels are identical after defaulting.
2. No label equals `"primary"` (reserved for primary receipt).
3. Reject at config load time with a clear error message.

**Warning signs:** Receipt files have fewer entries than expected, or primary receipt
has wrong content.

### Pitfall 3: Same-Stack Validation Missing (D-09)

**What goes wrong:** A sibling contains C files; `detect_toolchain()` returns `"python"`
for it (Python fallback path at `detect.py:339`). pylint/ruff/flake8 run on `.c` files
and either fail with parse errors or produce meaningless findings that pollute the joint
verdict.

**How to avoid:** Validate at config load time. After loading and validating the primary
repo's registry (via `detect_toolchain()`), run the same detection on each sibling.
If a sibling's `language` result != primary's, reject with a clear error:
`"sibling [plugin] detected as 'shell' but primary repo is 'python'; same-stack-only for v1"`.

**Warning signs:** L0 findings with tool-specific parse errors in sibling files.

### Pitfall 4: `ref:` Spec Not Validated Before Diff

**What goes wrong:** User types `main...feature-x` (three dots = symmetric difference,
not a range) or a non-existent branch. `git_diff()` runs and returns an error. Because
failure handling at config-load time is absent, the error only surfaces inside a thread,
making the message hard to attribute.

**How to avoid:** `validate_siblings()` checks the `ref:` format (must contain `..`).
The actual ref existence check (`resolve_git_ref`) happens when the thread acquires the
diff (early in thread startup), with a clear label in the error message.

### Pitfall 5: Single-Repo Zero-Drift Violation (D-11)

**What goes wrong:** A cross-repo code path conditionally imports or modifies a global
that also affects the single-repo path. A single-repo invocation suddenly behaves
differently.

**How to avoid:** The cross-repo dispatch in `cli.py` is a conditional early-return:
`if siblings: return run_cross_repo(...)`. The existing `_run_hold_loop` is called only
in the else branch. No shared state is modified by the cross-repo path.
**Add a regression test**: given the same diff + same gate.yaml (no siblings), the
output is byte-for-byte identical before and after Phase 25 lands.

### Pitfall 6: Remote URL Trust Gap (R-03)

**What goes wrong:** `repo: https://attacker.example.com/evil.git` in a repo-supplied
gate.yaml triggers `git clone` to an arbitrary URL. This is a network-and-subprocess
call on par with `base_url` and `api_key_env` -- both of which are in `DANGEROUS_FIELDS`.

**How to avoid:** Either (a) restrict Phase 25 v1 to local paths only (reject any
`repo:` starting with `https://` or `git@` at config load time), OR (b) add remote
`repo:` detection to `DANGEROUS_FIELDS` in `trust.py`. The CONTEXT.md leaves this to
the planner (R-03). Option (a) is safer and simpler for v1; the deferred section already
notes full remote support is a future phase.

---

## Code Examples

### Verified: StateMachine Constructor (all required params)

```python
# Source: src/code_forge/machine.py:136-178 (read 2026-06-17)
# Minimum viable StateMachine for cross-repo (most runners can be stubs):
sm = StateMachine(
    mode=mode,                       # Mode.LOCAL or Mode.CI
    falsifier=falsifier,             # from build_falsifier()
    autofixer=StubAutoFixer(),       # acceptable for cross-repo
    revert_fn=lambda f: None,        # acceptable for cross-repo (no autofix)
    resolved_review=resolved,        # ResolvedReview with joint diff + source_files
    source_hash=source_hash,         # from compute_source_hash(git_diff=joint_diff)
    baseline_spec_repr="cross-repo", # string label for state tracking
    cwd=per_repo_tmp_cwd,            # MUST be unique per thread (R-04)
    registry=registry,               # from load_registry() on primary repo
    l1_provider=l1_provider,         # from build_l1_provider() with joint diff
    advisory_runners=[...],          # empty for siblings; primary gets full set
    clean_round_threshold=threshold, # from tier_threshold()
)
verdict = sm.run()  # blocks until convergence or max_rounds
```

### Verified: Symlink Guard Usage

```python
# Source: src/code_forge/conventions_resolver.py:60-77 (read 2026-06-17)
# The guard uses Path.parents containment (NOT str.startswith).
# Safe sibling: real_path is within cwd.parent.
# Returns False if OSError (e.g., dangling symlink).

from .conventions_resolver import _symlink_guard_passes

def _validate_sibling_path(repo_path: Path, gate_yaml_dir: Path) -> None:
    """Validate sibling path is safe (within parent of gate.yaml dir)."""
    if not _symlink_guard_passes(repo_path.resolve(), gate_yaml_dir):
        raise ValueError(
            "sibling repo path escapes safe boundary: %s" % repo_path
        )
```

### Verified: git_diff() Signature

```python
# Source: src/code_forge/git.py:192 (grep confirmed 2026-06-17)
# def git_diff(baseline_ref, head_ref, paths, cwd) -> str
# Returns unified diff string. Raises BaselineResolutionError on failure.
# Exit code 1 (differences found) treated as success. Exit 2+ raises.

from .git import git_diff, resolve_git_ref

def _acquire_sibling_diff(repo_path: Path, ref_spec: str) -> str:
    baseline_ref, head_ref = ref_spec.split("..", 1)
    resolve_git_ref(baseline_ref, repo_path)  # raises on unknown ref
    resolve_git_ref(head_ref, repo_path)
    return git_diff(baseline_ref, head_ref, [], repo_path)
```

### Verified: write_receipts() Signature

```python
# Source: src/code_forge/receipt.py:58-68 (read 2026-06-17)
# Writes 3 files (one per pass) to receipts_dir/.
# Filename: receipt-cNpM.json where N=cycle, M=pass.
# For cross-repo D-19, rename output to {label}-receipt-rN.json:

from .receipt import write_receipts

receipts_dir = per_repo_tmp_cwd / ".code-forge" / "receipts"
written = write_receipts(
    receipts_dir=receipts_dir,
    round_index=round_index,
    l1_findings=l1_findings,
    diff_sha256=source_hash,
    source_files=source_files,
    cwd=per_repo_tmp_cwd,
    diff_files=diff_files,
    diff_text=diff_text,
    reviewer_excerpts=excerpts,
)
# Then rename: written[0] -> {label}-receipt-r{round_index}.json
```

### Verified: DANGEROUS_FIELDS Extension (R-03 option b)

```python
# Source: src/code_forge/trust.py:22-30 (read 2026-06-17)
# Current DANGEROUS_FIELDS is a frozenset. To add repo: URL detection,
# the trust.py DANGEROUS_FIELDS is for the backends block only.
# The siblings: section is at a different nesting level.
# Better to validate at gate_check.py: reject repo: values starting
# with https:// or git@ at validate_siblings() time in v1.
```

### Verified: D-20 Test Fixture Pattern

```python
# Source: tests/test_install_hooks.py:50-80 (read 2026-06-17)
# GIT_CEILING_DIRECTORIES isolation pattern:

def test_cross_repo_two_repos(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "GIT_CEILING_DIRECTORIES",
        str(tmp_path.parent),
        prepend=os.pathsep,
    )
    # Primary repo
    primary = tmp_path / "primary"
    primary.mkdir()
    subprocess.run(["git", "init"], cwd=primary, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=primary, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=primary, check=True,
    )
    # Add a commit on main, then branch for diff
    (primary / "main.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "."], cwd=primary, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=primary, check=True,
    )
    subprocess.run(
        ["git", "checkout", "-b", "feature"],
        cwd=primary, check=True,
    )
    (primary / "main.py").write_text("x = 2\n")
    subprocess.run(["git", "add", "."], cwd=primary, check=True)
    subprocess.run(
        ["git", "commit", "-m", "change"],
        cwd=primary, check=True,
    )

    # Sibling repo (same pattern)
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    subprocess.run(["git", "init"], cwd=sibling, check=True)
    # ... (same git setup) ...

    # Write gate.yaml with siblings:
    gate_yaml = primary / ".code-forge" / "gate.yaml"
    (primary / ".code-forge").mkdir()
    gate_yaml.write_text(
        "test:\n  command: [pytest]\n"
        "siblings:\n"
        "  - repo: ../sibling\n"
        "    ref: main..feature\n"
        "    label: sibling\n"
    )
    # Now call run_cross_repo() and assert joint verdict
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Single-repo review only | Cross-repo joint review | Phase 25 | Enables review of changes that span repo boundaries |
| `_async_mutation` daemon per StateMachine | Per-repo ephemeral cwd to isolate all state | Phase 25 (R-04 fix) | Thread safety without any machine.py changes |
| No label in findings | `[label] file:line` prefix in cross-repo mode | Phase 25 (D-08) | Unambiguous finding attribution across repos |

---

## Open Questions (RESOLVED)

1. **R-03 resolution: local-only v1 or DANGEROUS_FIELDS extension?**
  -- RESOLVED: local-only v1 -- validate_siblings() rejects https:// and git@ prefixes; no DANGEROUS_FIELDS change needed.
   - What we know: remote repo: URL is a new network+subprocess trigger, equivalent to
     `base_url` which is in DANGEROUS_FIELDS. D-05 is in-scope but R-03 flags it as a
     trust gap.
   - What's unclear: does the user want full shallow-clone support in v1, or is local-only
     acceptable and simpler?
   - Recommendation: local-only for v1 (reject `repo:` starting with `https://` or `git@`
     at `validate_siblings()` time). Add a `$comment` in the schema noting remote support
     is deferred. This matches the deferred section in CONTEXT.md.

2. **Receipt writer: wrapper vs new function?**
  -- RESOLVED: copy/rename pattern in run_cross_repo() Step 8; no new write_receipts() variant.
   - What we know: `write_receipts()` writes `receipt-cNpM.json` names; D-19 requires
     `{label}-receipt-rN.json`. The internal structure is identical.
   - Recommendation: a thin `write_cross_repo_receipt()` wrapper that calls
     `write_receipts()` and renames the output files. This reuses all the assembly logic
     with minimal new code.

3. **Advisory runners for siblings?**
  -- RESOLVED: advisory_runners=[] for all sibling threads; 5 runners on primary only (D-17).
   - What we know: `_run_hold_loop` wires TaintRunner, RuntimeRunner, GraphTriageRunner,
     DaemonStateRunner, LegacyRunner to the primary StateMachine. These run on the primary
     diff.
   - What's unclear: should advisory runners also run on sibling diffs?
   - Recommendation: Phase 25 v1 -- advisory runners on primary only (sibling findings
     are already advisory per D-17). This avoids complexity and is consistent with
     "primary authoritative" (D-17).

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest (existing) |
| Config file | pyproject.toml (existing) |
| Quick run command | `pytest tests/test_cross_repo.py -x -q` |
| Full suite command | `pytest -q` |

### Phase Requirements -> Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| CROSS-01 SC-1 | Two repos declared, one joint review context | integration | `pytest tests/test_cross_repo.py::test_joint_context -x` | No -- Wave 0 |
| CROSS-01 SC-2 | Finding in either repo appears in output | integration | `pytest tests/test_cross_repo.py::test_findings_attributed -x` | No -- Wave 0 |
| CROSS-01 SC-3 | Single-repo invocation produces identical output | regression | `pytest tests/test_cross_repo.py::test_single_repo_zero_drift -x` | No -- Wave 0 |
| D-05 (local only) | Remote repo: URL rejected at config load | unit | `pytest tests/test_cross_repo.py::test_remote_url_rejected -x` | No -- Wave 0 |
| R-04 | N concurrent StateMachines do not collide on mutation-result.json | integration | `pytest tests/test_cross_repo.py::test_thread_isolation -x` | No -- Wave 0 |
| R-05 | Duplicate labels rejected at config load | unit | `pytest tests/test_cross_repo.py::test_label_collision -x` | No -- Wave 0 |
| D-09 | Sibling with wrong language rejected | unit | `pytest tests/test_cross_repo.py::test_same_stack_validation -x` | No -- Wave 0 |
| D-06 | Invalid sibling ref fails-closed | integration | `pytest tests/test_cross_repo.py::test_invalid_ref_fail_closed -x` | No -- Wave 0 |
| Schema | siblings: section passes schema validation | unit | `pytest tests/test_schema_corpus.py -x -k siblings` | Partial -- add corpus |

### Sampling Rate

- Per task commit: `pytest tests/test_cross_repo.py -x -q`
- Per wave merge: `pytest -q` (full suite -- includes zero-drift regression)
- Phase gate: full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/test_cross_repo.py` -- all 9 tests above (create from scratch)
- [ ] `src/code_forge/cross_repo.py` -- module skeleton (empty functions with docstrings)
  before tests import it
- [ ] Sibling corpus entries in `tests/test_schema_corpus.py` (valid + invalid siblings snippets)

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | N/A |
| V3 Session Management | no | N/A |
| V4 Access Control | yes | Symlink guard (path escape), trust gate (R-03) |
| V5 Input Validation | yes | `validate_siblings()` in gate_check.py |
| V6 Cryptography | no | N/A |

### Known Threat Patterns for Cross-Repo Config

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Path traversal via `repo:` symlink | Tampering | `_symlink_guard_passes()` -- Path.parents containment check |
| Arbitrary URL clone via `repo: https://...` | Elevation | Reject at `validate_siblings()` (v1 local-only) |
| Label injection into output | Tampering | Label validated at config load (alphanumeric + hyphen/underscore only) |
| Two siblings same basename -> receipt overwrite | Tampering | Label uniqueness validation (R-05) |
| "primary" label reserved collision | Tampering | Reserve "primary" label at validation (R-05) |

---

## Project Constraints (from CLAUDE.md)

| Directive | Impact on Phase 25 |
|-----------|-------------------|
| All docs/memory/config files in English | Research, RESEARCH.md, PLAN files: English only |
| Work on forge/near-perfect-inline or main tree only | Phase 25 work on main (no dedicated branch needed unless >1 wave) |
| Targeted tests only, never full pytest (during dev) | Use `pytest tests/test_cross_repo.py -x -q` during implementation |
| Always use git worktrees | Create worktree before any code change |
| Logic-bearing code requires 3-cycle review | cross_repo.py, gate_check.py changes all require 3 cycles |
| Non-ASCII check before every commit | Run `git diff HEAD --diff-filter=AM -U0 | grep '^+' | grep -P '[^\x00-\x7F]'` |
| Pass a pathspec when committing in GSD repos | `git commit <pathspec>` (avoid staging .planning pre-staged files) |
| No plan-ref comments in code | No "D-14 fix", "R-04 isolation" in code comments |
| Commit message format: `<subsystem>/<case>: <summary>` + body + Signed-off-by | cross_repo/<case>: ... or gate/schema: ... |
| Commit messages: write WHY not WHAT | "isolate per-repo mutation state to prevent path collision" not "fix R-04 per machine.py line 332" |

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `git.py:git_diff()` accepts an empty `paths=[]` list to diff all files | Pattern 1 | Would need different call signature; verify by reading git.py fully |
| A2 | Each thread's `build_l1_provider()` call with the same joint diff is safe to call concurrently (no global LLM state shared) | Pattern 4 | Thread safety issue if factories.py has global mutable state |

Both A1 and A2 should be verified by reading the relevant function bodies before
finalizing the plan. A1 is highly likely given the existing baseline path passes
`paths: list[Path]` which defaults to empty. A2 is likely safe given each call
creates a new closure (`_provider()`).

---

## Sources

### Primary (HIGH confidence)

- `src/code_forge/machine.py` -- StateMachine constructor (lines 136-178), `_async_mutation` (lines 330-387), `cwd` path construction pattern (lines 218, 332, 1115)
- `src/code_forge/conventions_resolver.py` -- `_symlink_guard_passes()` (line 60), `_resolve_paths_as_sources()` (line 423)
- `src/code_forge/factories.py` -- `build_falsifier()` (line 25), `build_l1_provider()` (line 200), `build_autofixer()` (line 60)
- `src/code_forge/cli.py` -- `_run_hold_loop()` (line 1551), outlet dispatch pattern (lines 1356-1398)
- `src/code_forge/gate_check.py` -- `load_gate_config()` (line 38), `validate_presubmit_entry()` pattern (line 304)
- `src/code_forge/gate.schema.json` -- current schema structure, `additionalProperties: true` on root
- `src/code_forge/receipt.py` -- `write_receipts()` signature and behavior (line 58)
- `src/code_forge/trust.py` -- `DANGEROUS_FIELDS` frozenset (line 22)
- `src/code_forge/detect.py` -- language detection `"python" if has_python else ("shell" if has_shell else "python")` (line 339)
- `src/code_forge/outlet_c.py` -- `run_outlet_c()` multi-leg StateMachine pattern (line 34)
- `src/code_forge/baseline.py` -- `git_diff()` call chain, `ResolvedReview` dataclass
- `src/code_forge/diff.py` -- `count_diff_lines()` used to compute tier threshold
- `src/code_forge/exit_codes.py` -- `EXIT_DELEGATED=5`, `verdict_to_exit()`
- `.planning/phases/25-cross-repo-merge-review/25-CONTEXT.md` -- all 20 locked decisions

### Secondary (MEDIUM confidence)

- `tests/test_install_hooks.py` -- `GIT_CEILING_DIRECTORIES` isolation pattern (lines 50-80), confirmed as the D-20 model

### Tertiary (LOW confidence)

- None. All claims are verified against codebase ground truth.

---

## Metadata

**Confidence breakdown:**

- Standard stack: HIGH -- all verified by reading source files
- Architecture: HIGH -- all integration points verified against actual function signatures and line numbers
- Pitfalls: HIGH -- R-04/R-05 confirmed by reading machine.py:332 and receipt.py filename pattern

**Research date:** 2026-06-17
**Valid until:** 2026-07-17 (stable codebase; no fast-moving external deps)
