# Phase 28: Reviewer Canary for the Inline Outlet (M2: LLM half) - Research

**Researched:** 2026-06-24
**Domain:** LLM-driven canary generation, mutation injection, fresh-context dispatch, gate wiring
**Confidence:** HIGH

## Summary

Phase 28 builds the LLM half (M2) of the inline canary system on top of the
already-committed deterministic harness (M1 @ c515db7). M1 provides
`evaluate_canary_coverage`, `partition_canary_findings`, and
`reverify_finding_cites` with 31 passing tests. M2 must: (a) generate semantic
mutations of real diffs that are behavior-changing but have no local tell,
(b) inject them into an isolated copy of the diff, (c) dispatch a fresh-context
review via `llm_invoke`, (d) wire the result into the inline outlet behind
`--canary` / `gate.yaml canary:` opt-in.

The primary risk identified by the canary calibration spike is NOT the gate
logic (trivially correct) but canary GENERATION quality. The spike proved that
operator flips are too trivial (zero discriminating power), docstring-adjacent
bugs are too salient (caught even under overload), and N=1 is
variance-dominated. The generator must produce mutations that require
NON-LOCAL reasoning to detect, at N=3..5 per review with threshold >= ceil(0.6*N).

**Primary recommendation:** Use a two-tier generation strategy: a built-in
template library of parameterized Python defect patterns (6 categories from
SPEC-01 sec 4) as the deterministic fallback, plus an LLM-backed generator
(via the injected provider seam) that mutates REAL diff hunks for higher
realism. Non-equivalence verification uses Python `ast.parse` structural
comparison (pure logic, no LLM): a mutation must produce a different AST
from the original to be accepted; comment/whitespace-only changes are
rejected. The wiring target is cli.py line 1321-1327 (the `if outlet ==
"inline":` branch), augmented with a `run_inline_canary()` helper when
`--canary` is set or `gate.yaml canary:` block is present.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
D-28-01  Opt-in only. --canary flag OR gate.yaml canary: block. Default inline path UNCHANGED.
D-28-02  In-place SEMANTIC mutation, NOT synthetic appended file. Template fallback reuses SPEC-01 sec 4 categories. Generated canary MUST be verified NON-EQUIVALENT before use. Non-equivalence verify is pure logic (no LLM).
D-28-03  N=3..5, threshold=ceil(0.6*N). Fewer than 2 verified canaries -> SKIP with notice.
D-28-04  Isolation: planted into ISOLATED review COPY. Real tree NEVER mutated. partition_canary_findings IS the strip.
D-28-05  Fresh-context dispatch: independent pass, no author narrative, cold claude -p / subprocess via llm_invoke, user's own model.
D-28-06  Gate consequence (honors D-16): miss -> FAIL/UNRELIABLE, MUST NOT switch outlet/model/auto-retry. Pass -> real findings proceed.
D-28-07  Backend-agnostic (honors D-25): generation + injection at prompt/diff layer above llm_invoke.
D-28-08  Anti-fabrication: fresh-context findings pass through reverify_finding_cites before the gate trusts them.
D-28-09  Provider seam for testability: every LLM call through injected callable (DI pattern, like machine.py l1_provider). No real network in unit suite.

### Claude's Discretion (gray areas)
(a) gate.yaml canary: schema -- field names, defaults, validation (consistent with existing gate_check.py loader).
(b) Generation prompt + non-equivalence verification strategy -- structural vs test-kill bar.
(c) FAIL/UNRELIABLE consequence surface -- new Verdict member vs existing + infra_error. Exit code 4 or 7 available.
(d) Wiring strategy in cli.py -- wrapper vs dedicated run_inline_canary() helper.

### Deferred Ideas (OUT OF SCOPE)
- Outlet-A / StateMachine canary (SPEC-01, separate phase)
- L0 canary injection
- Adaptive difficulty, telemetry dashboard, custom gate.yaml templates
- Cross-language template library (Python only for v1)
- Canary for non-inline outlets
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SPEC-01 (inline variant) | Inline outlet gains opt-in objective laziness check: planted defects the reviewer cannot distinguish from real ones, gated on catch rate | Full M1 harness exists (canary.py, evidence.py, findings.py); M2 builds generation, injection, dispatch, and wiring on top. SPEC-01 sec 10 item 7 defers inline enforcement to Phase 28. |
</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Canary generation (template + LLM) | Code module (canary_gen.py) | -- | Pure Python logic + injected LLM provider; no I/O beyond the provider seam |
| Non-equivalence verification | Code module (canary_gen.py) | -- | Python ast.parse structural comparison -- deterministic, no LLM |
| Diff isolation + injection | Code module (canary_gen.py) | -- | String manipulation on the diff text; never touches git or filesystem |
| Fresh-context dispatch | Code module (via llm_invoke.py) | -- | Uses existing llm_invoke seam; backend-agnostic per D-25 |
| Gate evaluation | Code module (canary.py -- M1) | -- | Already built: evaluate_canary_coverage + partition_canary_findings |
| Cite re-verification | Code module (evidence.py -- M1) | -- | Already built: reverify_finding_cites |
| CLI wiring + opt-in | CLI layer (cli.py) | gate_check.py (canary: block validation) | Thin wiring in the inline branch; gate.yaml loading in gate_check.py |
| gate.yaml canary: schema | Config layer (gate_check.py) | init_template.py (template) | Validation follows existing section patterns (graph_triage, daemon_state) |

## Standard Stack

### Core (already in project -- NO new dependencies)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| ast (stdlib) | Python 3.14 | AST parse for non-equivalence verification | Deterministic structural diff; no external dependency [VERIFIED: stdlib] |
| hashlib (stdlib) | Python 3.14 | SHA-256 for canary manifest audit trail | Already used in canary.py Canary.sha256 field [VERIFIED: M1 commit c515db7] |
| uuid (stdlib) | Python 3.14 | UUID4 canary_id generation | Already used for canary IDs per SPEC-01 sec 9 [VERIFIED: SPEC-01 design doc] |
| textwrap (stdlib) | Python 3.14 | Diff hunk indentation manipulation | Useful for template code formatting [VERIFIED: stdlib] |
| pyyaml | >=6.0 | gate.yaml canary: block loading | Already a project dependency [VERIFIED: pyproject.toml line 29] |

### Supporting (already in project)
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| code_forge.llm_invoke | current | Fresh-context review dispatch | llm_invoke(prompt, backend) for the canary review pass |
| code_forge.canary (M1) | c515db7 | Gate evaluation + partition | evaluate_canary_coverage, partition_canary_findings |
| code_forge.evidence (M1) | c515db7 | Cite re-verification | reverify_finding_cites |
| code_forge.findings (M1) | c515db7 | Finding line parsing | finding_line |
| code_forge.reviewer_json | current | Finding validation | validate_reviewer_json -- contract: {file, line, severity, description} |

**Installation:** No new packages. All dependencies are stdlib or already in pyproject.toml.

## Package Legitimacy Audit

No new external packages are introduced in this phase. All functionality uses
Python stdlib modules (ast, hashlib, uuid, textwrap) and existing project
dependencies (pyyaml, code_forge.*).

**Packages removed due to slopcheck [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none

## Architecture Patterns

### System Architecture Diagram

```
User invokes: code-forge review --outlet inline --canary
                        |
                        v
              +---------+----------+
              | cli.py _run()      |
              | outlet == "inline" |
              | --canary opted in? |
              +---------+----------+
                   |yes          |no
                   v             v
    +------------------+   return Verdict.DELEGATED
    | run_inline_canary|   (unchanged default path)
    +--------+---------+
             |
     1. Load gate.yaml canary: config (or CLI defaults)
     2. Read diff text from resolved_review.git_diff
             |
             v
    +--------+---------+
    | generate_canaries |  <-- injected provider (LLM or stub)
    | (canary_gen.py)   |
    +--------+---------+
             |
     For each candidate mutation:
       a. Apply mutation to isolated diff copy
       b. Verify non-equivalence (ast.parse structural diff)
       c. Accept if non-equivalent, discard if equivalent
       d. Stop when N verified canaries collected (or exhausted)
             |
             v
    +--------+---------+
    | inject_canaries   |  Merge mutations into isolated diff copy
    | Build manifest    |  Record Canary(id, file, line, sha256, desc)
    +--------+---------+
             |
     If < 2 verified canaries: SKIP with notice, return DELEGATED
             |
             v
    +--------+---------+
    | dispatch_review   |  <-- injected provider (LLM or stub)
    | via llm_invoke    |  Fresh context, no author narrative
    +--------+---------+
             |
     Parse reviewer JSON -> validate_reviewer_json
             |
             v
    +--------+---------+
    | reverify_finding  |  Drop fabricated citations
    | _cites (M1)       |
    +--------+---------+
             |
             v
    +--------+---------+
    | evaluate_canary   |  Score: caught >= threshold?
    | _coverage (M1)    |
    +--------+---------+
             |
        +----+----+
        |         |
      PASS      MISS
        |         |
        v         v
    partition   return Verdict.FAIL
    _canary     (UNRELIABLE,
    _findings   exit 7, infra notice)
    (M1)
        |
        v
    return Verdict.DELEGATED
    (real findings on stderr)
```

### Recommended Project Structure

```
src/code_forge/
+-- canary.py           # M1 -- gate evaluation + partition (EXISTS)
+-- evidence.py         # M1 -- cite re-verify (EXISTS)
+-- findings.py         # M1 -- finding_line (EXISTS)
+-- canary_gen.py       # M2 -- NEW: generation, non-equiv verify, injection
+-- cli.py              # MODIFIED: --canary flag, run_inline_canary() helper
+-- gate_check.py       # MODIFIED: validate_canary_config() for gate.yaml
+-- exit_codes.py       # MODIFIED: EXIT_UNRELIABLE = 7
+-- state.py            # MODIFIED: Verdict.UNRELIABLE member
+-- init_template.py    # MODIFIED: canary: block in template
tests/
+-- test_canary.py      # M1 tests (EXISTS, 31 tests) -- cherry-picked
+-- test_evidence.py    # M1 tests (EXISTS) -- cherry-picked
+-- test_findings.py    # M1 tests (EXISTS) -- cherry-picked
+-- test_canary_gen.py  # M2 -- NEW: generation, non-equiv, injection tests
+-- test_canary_cli.py  # M2 -- NEW: CLI wiring + gate.yaml integration tests
```

### Pattern 1: Provider Seam (Dependency Injection)

**What:** Every LLM call goes through an injected callable so the unit suite
tests with a stub provider. Follows machine.py's l1_provider pattern.

**When to use:** All LLM-dependent operations (canary generation, fresh-context
review dispatch).

**Example:**
```python
# Source: machine.py line 198 (verified in codebase)
# StateMachine uses:
l1_provider: L1Provider = field(default=lambda: ([], [], Usage(), 0.0))

# canary_gen.py should follow the same pattern:
from typing import Protocol, Callable

class CanaryProvider(Protocol):
    """Generate candidate mutations from a diff hunk."""
    def __call__(self, hunk: str, context: str) -> list[dict]: ...

class ReviewProvider(Protocol):
    """Dispatch a fresh-context review and return findings JSON."""
    def __call__(self, prompt: str) -> str: ...

# Default: no-op stubs for testing
_default_canary_provider: CanaryProvider = lambda hunk, ctx: []
_default_review_provider: ReviewProvider = lambda prompt: '{"findings":[],"code_excerpts":[]}'
```

### Pattern 2: gate.yaml Section Validation

**What:** New gate.yaml sections follow the existing validate_* pattern in
gate_check.py: a standalone validator function called from load_gate_config.

**When to use:** Adding the `canary:` block to gate.yaml.

**Example:**
```python
# Source: gate_check.py line 137-168 (verified in codebase)
# Follows validate_graph_triage pattern:
def validate_canary_config(section: object) -> None:
    """Validate the canary section of gate.yaml."""
    if not isinstance(section, dict):
        raise ValueError(
            "gate.yaml 'canary' must be a mapping, got: %s"
            % type(section).__name__
        )
    if "enabled" in section:
        if not isinstance(section["enabled"], bool):
            raise ValueError(...)
    # ... validate n, threshold_ratio, etc.
```

### Pattern 3: Non-Equivalence via AST Structural Diff

**What:** A mutation is non-equivalent when `ast.dump(ast.parse(original)) !=
ast.dump(ast.parse(mutated))`. This is pure logic, testable without an LLM,
and catches comment/whitespace-only mutations that would unfairly fail a
genuine reviewer.

**When to use:** After generating each candidate mutation, before accepting it
into the manifest.

**Example:**
```python
import ast

def is_non_equivalent(original: str, mutated: str) -> bool:
    """True when the mutation changes program structure (not just cosmetics).

    Both inputs must be syntactically valid Python. A SyntaxError in either
    returns False (invalid mutation discarded).
    """
    try:
        orig_ast = ast.dump(ast.parse(original), annotate_fields=False)
        mut_ast = ast.dump(ast.parse(mutated), annotate_fields=False)
    except SyntaxError:
        return False
    return orig_ast != mut_ast
```

### Pattern 4: Isolated Diff Copy (Prompt-Level Injection)

**What:** Canaries are injected into the PROMPT string, never the working tree.
The isolation mechanism is string manipulation: take the real diff text, apply
mutations to a COPY, pass the modified copy to the fresh-context reviewer.

**When to use:** Before dispatching the canary review.

**Example:**
```python
def inject_canaries_into_diff(
    diff_text: str,
    mutations: list[dict],
) -> tuple[str, list[Canary]]:
    """Apply mutations to a copy of the diff text.

    Returns (modified_diff, manifest) where manifest records each
    canary's file, line, and sha256 for the gate.
    """
    modified = diff_text  # copy -- original unchanged
    manifest = []
    for mut in mutations:
        modified = _apply_mutation(modified, mut)
        manifest.append(Canary(
            canary_id=str(uuid.uuid4()),
            file=mut["file"],
            line=mut["line"],
            sha256=hashlib.sha256(mut["code"].encode()).hexdigest(),
            description=mut["description"],
        ))
    return modified, manifest
```

### Anti-Patterns to Avoid

- **Mutating the working tree:** NEVER write canary code to disk. D-28-04
  and SPEC-01 sec 9 are non-negotiable. The diff is a string; mutate the
  string.
- **Running tests for non-equivalence:** The spike found this unnecessary
  and slow. AST structural diff is sufficient for v1. Test-kill is a future
  enhancement (CONTEXT.md gray area b).
- **Sharing context between impl and canary review:** The fresh-context
  dispatch MUST be independent (D-28-05). No session state, no prior
  findings, no author narrative in the canary review prompt.
- **Auto-retrying on canary miss:** D-28-06 explicitly forbids auto-retry
  loops. A miss returns UNRELIABLE once.
- **Using the canary result to switch models/outlets:** D-16 and BOTH-04
  are locked. Canary validates attention, not capability.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Gate evaluation | Custom threshold/catch logic | `evaluate_canary_coverage` (M1) | Already built with 31 tests, handles edge cases (empty manifest fails closed, threshold validation) |
| Finding partition | Custom filter loop | `partition_canary_findings` (M1) | Already built, preserves order, handles file-level findings |
| Cite verification | Custom file/line checker | `reverify_finding_cites` (M1) | Already built, handles line<=0 file-level claims |
| Finding line parse | int(finding["line"]) | `finding_line` (M1) | Handles absent/unparseable gracefully, returns 0 |
| JSON validation | Custom schema check | `validate_reviewer_json` | Already validates {file, line, severity, description} contract |
| LLM dispatch | Raw subprocess/HTTP | `llm_invoke` | Backend-agnostic (cli/api), timeout, signal handler, JSON extraction |
| YAML loading | Raw yaml.safe_load | `load_gate_config` pattern in gate_check.py | Trust guard, type validation, error messages |

**Key insight:** M1 was explicitly designed so that M2 only needs to generate
and inject; all evaluation, partitioning, and verification are ready to use.

## Common Pitfalls

### Pitfall 1: Canaries Too Easy (Zero Discriminating Power)
**What goes wrong:** The canary is caught by every reviewer including an
overloaded one, providing no laziness signal.
**Why it happens:** Operator flips (+ to -), hardcoded secrets with obvious
variable names, bare `except: pass` -- all trivially catchable.
**How to avoid:** The spike proved this empirically (widget.py: both caught).
Generated canaries must have NO LOCAL TELL -- the bug must require non-local
reasoning (checking a docstring, tracing a data flow, understanding
1-indexed vs 0-indexed semantics). Template fallback categories from SPEC-01
sec 4 are calibrated: use the medium-difficulty ones (off-by-one,
resource leak, SQL injection) over the easy ones (hardcoded secret, bare
except) when possible.
**Warning signs:** In spike validation, both genuine and overloaded reviewers
catch the canary.

### Pitfall 2: Canaries with Local Tells
**What goes wrong:** A comment, docstring, or variable name next to the bug
explains the contract it violates, making the bug catchable by pattern
scanning rather than code comprehension.
**Why it happens:** Generated code often includes descriptive comments.
The spike proved this: ledger.py had docstrings stating "1-indexed" and
"exactly equal still affords" -- the overloaded reviewer caught both by
reading the docstrings, not by tracing the logic.
**How to avoid:** Strip or neutralize comments/docstrings in the vicinity
of the mutation. The generation prompt must explicitly instruct: "do NOT
include any comment or docstring that describes the correct behavior near
the mutation."
**Warning signs:** The mutation's line has a comment explaining what the
correct value should be.

### Pitfall 3: Non-Equivalent Verification Too Strict or Too Loose
**What goes wrong:** Too strict: rejects valid mutations that change behavior
but happen to produce the same AST structure (e.g., numeric literal changes).
Too loose: accepts whitespace/comment-only changes that are actually
equivalent.
**Why it happens:** `ast.dump` normalizes away whitespace and comments but
preserves all structural elements including literal values.
**How to avoid:** Use `ast.dump(tree, annotate_fields=False)` which strips
field names but preserves structure. This catches all operator changes,
literal value changes, argument reorderings, and control flow changes
while ignoring pure cosmetic edits. For the v1 bar this is sufficient --
a literal `0` changed to `1` produces different AST dumps.
**Warning signs:** In unit tests, a mutation known to change behavior is
rejected by the verifier, or a known-equivalent mutation is accepted.

### Pitfall 4: Exit Code Collision
**What goes wrong:** The new UNRELIABLE exit code collides with an existing
one, breaking callers.
**Why it happens:** Exit codes 0-6 are already assigned (PASS, FAIL,
CLI_ERROR, BUSY, ESCALATED, DELEGATED, TIMEOUT). Code 4 = ESCALATED.
**How to avoid:** Use exit code 7 for UNRELIABLE. CONTEXT.md sec 9 confirms
codes 4 and 7 are "available" but code 4 is actually EXIT_ESCALATED
(exit_codes.py line 17). Only code 7 is truly free.
**Warning signs:** Check exit_codes.py before assigning.

### Pitfall 5: Breaking the Default Inline Path
**What goes wrong:** Non-opted-in users see different behavior from
`code-forge review --outlet inline`.
**Why it happens:** The canary wiring modifies the inline branch without
proper opt-in gating.
**How to avoid:** The `run_inline_canary()` helper is called ONLY when
`--canary` is set OR gate.yaml has `canary.enabled: true`. The else
branch is the EXACT existing code (stderr write + return
Verdict.DELEGATED). Test: with no opt-in, output and exit code must
be byte-for-byte identical to current behavior.
**Warning signs:** `git diff` of the inline branch shows changes outside
the new `if canary_opted_in:` block.

### Pitfall 6: Forge Worktree Test Pollution
**What goes wrong:** Running `pytest` in a forge worktree pollutes the
real .git directory.
**Why it happens:** Phase 18.1 identified this issue; the worktree shares
.git state.
**How to avoid:** Always run targeted tests:
`PYTHONPATH=src python -m pytest tests/test_canary_gen.py -x`
NEVER run bare `pytest` in a forge worktree.
**Warning signs:** CONTEXT.md sec 9 explicitly warns about this.

## Code Examples

### Fresh-Context Review Dispatch (verified pattern from cli.py)

```python
# Source: cli.py lines 554-591 (verified in codebase)
# The _spawn function in _build_spawn_fn shows the pattern:
def _spawn(pass_name: str, diff_text: str) -> str:
    from .llm_invoke import llm_invoke
    role = _PASS_ROLES.get(pass_name, "code reviewer")
    prompt = (
        "You are a " + role + ". Review this diff.\n"
        'Return JSON: {"findings": [...], "code_excerpts": [...]}\n'
    )
    prompt += "\nDiff:\n" + diff_text
    result = llm_invoke(prompt, backend=backend)
    content = result.content
    if isinstance(content, dict):
        return json.dumps(content)
    return str(content)

# Canary review dispatch follows the same pattern but with:
# 1. No pass_name / role differentiation (single cold pass)
# 2. The MODIFIED diff (with canaries injected)
# 3. No conventions digest or contract spec (anti-anchoring: D-28-05)
```

### gate.yaml canary: Block (recommended schema)

```yaml
# gate.yaml -- canary configuration (opt-in)
canary:
  enabled: true              # bool, required for opt-in
  n: 5                       # int, 3..10, default 5
  threshold_ratio: 0.6       # float, 0.0..1.0, default 0.6
  # threshold = ceil(threshold_ratio * n)
  # e.g. n=5, ratio=0.6 -> threshold=3
```

### Verdict.UNRELIABLE + EXIT_UNRELIABLE

```python
# state.py -- add to Verdict enum:
class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    ESCALATED = "ESCALATED"
    PENDING = "PENDING"
    DELEGATED = "DELEGATED"
    UNRELIABLE = "UNRELIABLE"   # canary miss on inline outlet

# exit_codes.py -- add:
EXIT_UNRELIABLE = 7

# verdict_to_exit -- add mapping:
if verdict == Verdict.UNRELIABLE:
    return EXIT_UNRELIABLE
```

## Gray Area Recommendations

### (a) gate.yaml canary: schema

**Recommendation:** Minimal surface, consistent with existing sections.

```yaml
canary:
  enabled: true         # bool -- REQUIRED for opt-in
  n: 5                  # int, 3..10 -- how many canaries to plant
  threshold_ratio: 0.6  # float, 0.0..1.0 -- catch ratio to pass
```

Rationale: `enabled` follows `graph_triage.enabled` / `daemon_state.enabled`
pattern. `n` and `threshold_ratio` are the two knobs from D-28-03. No
`templates` field for v1 (deferred). Validation in `validate_canary_config()`
following the `validate_graph_triage()` pattern in gate_check.py (lines
137-168). `threshold_ratio` is a float rather than an absolute `threshold`
integer because the user sets `n` and the ratio is more intuitive ("catch
60%") than an absolute number that must be recalculated when `n` changes.

### (b) Non-equivalence verification strategy

**Recommendation:** AST structural comparison (pure logic, no test execution).

`ast.dump(ast.parse(original)) != ast.dump(ast.parse(mutated))` is the bar.
This catches ALL behavior-changing mutations (operator changes, literal
changes, argument additions/removals, control flow changes) while rejecting
comment/whitespace-only edits. It is:
- Deterministic (no LLM, no flakiness)
- Fast (microseconds, not seconds)
- Testable (unit tests with known equivalent/non-equivalent pairs)
- Sufficient for v1 (the spike's failures were about subtlety, not
  equivalence detection)

Test-kill (running tests to confirm the mutation is killed) is deferred.
The spike showed the binding risk is subtlety, not equivalence.

For the template fallback library, non-equivalence is GUARANTEED BY
CONSTRUCTION (each template introduces a structural code change), so the
AST check is a safety net, not the primary gate.

### (c) FAIL/UNRELIABLE consequence

**Recommendation:** New `Verdict.UNRELIABLE` member + `EXIT_UNRELIABLE = 7`.

Rationale:
- Exit code 7 is the only truly free code (code 4 = EXIT_ESCALATED, verified
  at exit_codes.py line 17, despite CONTEXT.md listing both 4 and 7 as
  "available").
- A new Verdict member is cleaner than overloading FAIL + infra_error because:
  (1) callers can pattern-match on it directly, (2) the exit code is unique
  so scripts can distinguish "review found real bugs" (exit 1) from "review
  was unreliable" (exit 7), (3) it does not pollute the FAIL semantics
  which means "real findings exist."
- The `verdict_to_exit` function gains one line: `if verdict ==
  Verdict.UNRELIABLE: return EXIT_UNRELIABLE`.
- State.py Verdict enum is a str enum; adding a member is additive (no
  schema_version bump per project D2 convention).

### (d) CLI wiring strategy

**Recommendation:** Dedicated `run_inline_canary()` helper function.

The inline branch at cli.py line 1321-1327 becomes:

```python
if outlet == "inline":
    canary_config = _load_canary_config(args, gate_yaml_path)
    if canary_config is not None:
        return run_inline_canary(
            diff_text=resolved.git_diff or "",
            canary_config=canary_config,
            backend=backend_for_canary,
            cwd=cwd,
        )
    # Default: unchanged
    sys.stderr.write(
        "code-forge: DELEGATED -- review delegated to session"
        " + external R1; exit 5\n"
    )
    return Verdict.DELEGATED
```

Where `_load_canary_config()` returns the canary config dict when opted in
(via `--canary` flag or gate.yaml `canary.enabled: true`), or None when not
opted in. `run_inline_canary()` lives in canary_gen.py (not cli.py) to keep
cli.py thin and canary_gen.py testable without CLI dependency.

This keeps the default branch readable and unchanged, and concentrates all
canary logic in the helper. The helper is independently testable with stub
providers.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Marker string echo (check #8, deleted at 1f105ec) | Planted defect in diff | SPEC-01, 2026-06-03 | Pattern matching -> code comprehension |
| Single synthetic file (_canary_NNN.py, SPEC-01) | In-place semantic mutation of real diff | Phase 28, 2026-06-17 | More realistic, harder to detect by format |
| N=1 canary per round | N=3..5 with threshold tuning | Phase 28, 2026-06-17 | Variance reduction (spike proved N=1 unreliable) |
| Mutation testing via mutmut subprocess | AST-based non-equivalence verify | Phase 28 | No test execution needed; microsecond verify |

**Deprecated/outdated:**
- Check #8 (anti-shirk marker): deleted at commit 1f105ec. Superseded by SPEC-01.
- SPEC-01 synthetic file approach: still valid for Outlet A / StateMachine, but
  Phase 28's inline variant uses in-place mutation instead (complementary, not
  replacement).

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `ast.dump` comparison is sufficient for non-equivalence detection of all Python mutation types (no false negatives for behavior-changing mutations) | Architecture Patterns, Pattern 3 | A mutation that changes behavior but produces identical AST dump would be accepted as equivalent and discarded. Mitigated: ast.dump preserves literal values, operators, and all structural elements. Risk is LOW for Python. |
| A2 | Exit code 7 is free and will not collide with future phases | Gray Area (c) | Another phase could assign exit code 7 before Phase 28 lands. Mitigated: Phase 28 is the next phase to execute; no parallel work targets exit_codes.py. |
| A3 | The `_spawn` pattern in cli.py (lines 554-591) is the correct model for fresh-context dispatch | Code Examples | If the dispatch pattern changes between now and execution, the canary dispatch may need updating. Mitigated: the pattern is stable (used by Outlet C since Phase 13). |

## Open Questions (RESOLVED)

1. **Backend resolution for canary dispatch when outlet=inline** (RESOLVED: Plan 03 uses gate.yaml cfgs via resolve_backend; if empty, default CLI backend)
   - What we know: When outlet="inline", the main _run() function returns
     DELEGATED before backend resolution (cli.py line 1321-1327). The canary
     dispatch needs a backend to call llm_invoke.
   - What's unclear: Should the canary use the user's configured backend
     (from gate.yaml), the session-default CLI backend, or a CLI --canary
     flag that also specifies the backend?
   - Recommendation: Load backends from gate.yaml (already done at line 1298:
     `cfgs = _load_gate_backends(gate_yaml_path)`). If cfgs is non-empty,
     resolve_backend from cfgs. If empty, construct a default CLI backend
     (claude binary). This means the canary dispatch uses whatever the user
     already configured -- zero extra config. The `--canary` flag opts in
     to canary checking, not to a specific backend.

2. **Diff availability at the inline branch point** (RESOLVED: Plan 03 computes diff via subprocess git diff --cached)
   - What we know: The inline branch returns BEFORE baseline resolution
     (which computes git_diff). The canary needs the diff.
   - What's unclear: Must the canary path compute its own diff, or can it
     reuse the baseline resolution logic?
   - Recommendation: Call the minimal diff computation directly:
     `subprocess.run(["git", "diff", "--cached"], ...)` or the existing
     `git diff HEAD` path. This is simpler than pulling in the full
     baseline resolution pipeline. The canary only needs the text diff,
     not the full ResolvedReview.
   - Resolution: Plan 03 Task 1 implements the recommendation.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.14 | All modules | Yes | 3.14.5 | -- |
| ast (stdlib) | Non-equivalence verify | Yes | stdlib | -- |
| pytest | Test suite | Yes | (project dep) | -- |
| git | Diff computation | Yes | (system) | -- |
| claude binary | CLI backend dispatch | Conditional | -- | API backend via gate.yaml |

**Missing dependencies with no fallback:** None
**Missing dependencies with fallback:** claude binary -- if not on PATH, user
must configure an API backend in gate.yaml for canary dispatch.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (configured in pyproject.toml) |
| Config file | pyproject.toml [tool.pytest.ini_options] |
| Quick run command | `PYTHONPATH=src python -m pytest tests/test_canary_gen.py tests/test_canary_cli.py -x` |
| Full suite command | `PYTHONPATH=src python -m pytest tests/test_canary.py tests/test_evidence.py tests/test_findings.py tests/test_canary_gen.py tests/test_canary_cli.py -x` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| GEN-01 | Template fallback generates valid Python mutations | unit | `PYTHONPATH=src python -m pytest tests/test_canary_gen.py::test_template_generates_valid_mutation -x` | Wave 0 |
| GEN-02 | LLM provider generates mutations via injected seam | unit | `PYTHONPATH=src python -m pytest tests/test_canary_gen.py::test_provider_seam -x` | Wave 0 |
| GEN-03 | Non-equivalence rejects comment-only changes | unit | `PYTHONPATH=src python -m pytest tests/test_canary_gen.py::test_nonequiv_rejects_comment -x` | Wave 0 |
| GEN-04 | Non-equivalence accepts operator changes | unit | `PYTHONPATH=src python -m pytest tests/test_canary_gen.py::test_nonequiv_accepts_operator -x` | Wave 0 |
| GEN-05 | Fewer than 2 verified canaries -> SKIP | unit | `PYTHONPATH=src python -m pytest tests/test_canary_gen.py::test_skip_on_insufficient -x` | Wave 0 |
| INJ-01 | Canaries injected into diff copy, original unchanged | unit | `PYTHONPATH=src python -m pytest tests/test_canary_gen.py::test_injection_isolation -x` | Wave 0 |
| DSP-01 | Fresh-context dispatch uses injected provider | unit | `PYTHONPATH=src python -m pytest tests/test_canary_gen.py::test_dispatch_provider -x` | Wave 0 |
| DSP-02 | Findings pass through reverify_finding_cites | unit | `PYTHONPATH=src python -m pytest tests/test_canary_gen.py::test_cite_reverify -x` | Wave 0 |
| GAT-01 | Canary pass -> real findings proceed, Verdict.DELEGATED | unit | `PYTHONPATH=src python -m pytest tests/test_canary_gen.py::test_gate_pass -x` | Wave 0 |
| GAT-02 | Canary miss -> Verdict.UNRELIABLE, exit 7 | unit | `PYTHONPATH=src python -m pytest tests/test_canary_gen.py::test_gate_miss -x` | Wave 0 |
| CLI-01 | --canary flag accepted by parser | unit | `PYTHONPATH=src python -m pytest tests/test_canary_cli.py::test_canary_flag -x` | Wave 0 |
| CLI-02 | gate.yaml canary: block validated | unit | `PYTHONPATH=src python -m pytest tests/test_canary_cli.py::test_gate_yaml_canary -x` | Wave 0 |
| CLI-03 | No opt-in -> unchanged DELEGATED behavior | unit | `PYTHONPATH=src python -m pytest tests/test_canary_cli.py::test_no_optin_unchanged -x` | Wave 0 |
| CLI-04 | Exit code 7 for UNRELIABLE | unit | `PYTHONPATH=src python -m pytest tests/test_canary_cli.py::test_exit_unreliable -x` | Wave 0 |
| SMOKE-01 | Real-model canary round (gated, not in unit suite) | smoke (manual) | `PYTHONPATH=src python -m pytest tests/test_canary_gen.py -m real_api -x` | acceptance (post-plan, main-session) |

### Sampling Rate
- **Per task commit:** `PYTHONPATH=src python -m pytest tests/test_canary_gen.py tests/test_canary_cli.py -x`
- **Per wave merge:** Full canary suite (all 5 test files)
- **Phase gate:** Full suite green before verification

### Wave 0 Gaps
- [ ] `tests/test_canary_gen.py` -- covers GEN-01 through GAT-02
- [ ] `tests/test_canary_cli.py` -- covers CLI-01 through CLI-04
- [ ] M1 tests cherry-picked and passing on new feature branch

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | -- |
| V3 Session Management | no | -- |
| V4 Access Control | no | -- |
| V5 Input Validation | yes | gate.yaml canary: block validated by validate_canary_config(); reviewer JSON validated by validate_reviewer_json() |
| V6 Cryptography | no (sha256 for audit trail only, not security) | hashlib.sha256 stdlib |

### Known Threat Patterns for this phase

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Canary code written to working tree | Tampering | D-28-04: prompt-only injection, never disk; partition strips canary findings |
| Canary code committed to git history | Tampering | SPEC-01 sec 9: never staged, never committed; working tree mutation is a hard error |
| gate.yaml injection via canary: block | Tampering | validate_canary_config() type-checks all fields; trust guard in _load_gate_backends |
| Reviewer fabricates findings to pass gate | Spoofing | reverify_finding_cites (M1) drops findings citing nonexistent file/line |
| Canary result used to switch model/outlet | Elevation of privilege | D-16 + BOTH-04: locked, never feeds back into outlet selection |

## Sources

### Primary (HIGH confidence)
- M1 commit c515db7 -- canary.py, evidence.py, findings.py (read via `git show`)
- docs/design/reviewer-canary-spec.md -- SPEC-01 full design (789 lines, read in full)
- 28-CONTEXT.md -- 9 locked decisions, 4 gray areas (read in full)
- cli.py lines 1321-1327, 537-591 -- inline outlet branch and _spawn pattern (read in codebase)
- gate_check.py lines 39-134 -- load_gate_config, validate_graph_triage pattern (read in codebase)
- exit_codes.py -- all 7 exit code assignments (read in full)
- state.py -- Verdict enum, State dataclass (read in full)
- llm_invoke.py -- llm_invoke function, BackendConfig (read in full)
- machine.py line 198 -- l1_provider DI pattern (read in codebase)
- spikes/canary_fence/README.md @ 5d9b1dc -- spike empirical findings (read via git show)

### Secondary (MEDIUM confidence)
- [Hybrid Fault-Driven Mutation Testing for Python (PyTation)](https://arxiv.org/html/2601.19088v1) -- AST-based mutation operators and equivalent mutant detection heuristics
- [Mutation Analysis - The Fuzzing Book](https://www.fuzzingbook.org/html/MutationAnalysis.html) -- mutation operator taxonomy
- [Mutatest documentation](https://mutatest.readthedocs.io/en/latest/) -- AST grammar-based mutation that ensures valid mutants

### Tertiary (LOW confidence)
- None

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new packages, all stdlib + existing deps
- Architecture: HIGH -- M1 provides the evaluation layer, patterns verified in codebase
- Pitfalls: HIGH -- empirically validated by the canary calibration spike (3 fixtures, run protocol documented)
- Gray areas: HIGH -- recommendations grounded in verified codebase patterns

**Research date:** 2026-06-24
**Valid until:** 2026-07-24 (stable -- no external dependency changes expected)
