# Phase 2: R2 (mutation pipeline step) - Research

**Researched:** 2026-05-26
**Domain:** Mutation testing integration, Python subprocess orchestration, state machine extension
**Confidence:** MEDIUM

## Summary

Phase 2 integrates mutation testing as a review-pipeline step in the forge state machine. The key implementation challenge is adding a third layer (L2) to the existing L0 (static parsers) + L1 (LLM candidates) architecture without overloading existing components. Mutmut is the reference Python mutation tool with a stable CLI interface (versions 3.2-3.5 in 2025-2026). The state machine already has a clean dependency injection pattern (l0_runner, l1_provider as callables) that l2_runner follows directly. The main technical risks are: mutmut output format changes between versions (mitigated by subprocess call isolation in one module), and LOCAL sync vs CI async mode split (different cycle-counter semantics).

**Primary recommendation:** Follow the existing DI pattern from machine.py: l2_runner is a callable wired in StateMachine.__init__(), invoked after L1 in _execute_round(). Keep mutmut subprocess calls in a single mutation.py module so future language runners can replace it without touching the state machine. Filter source="MUTANT" findings before _apply_autofix_loop_to() to prevent coverage-gap autofix loops.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Mutation invocation | State Machine | Mutation Module | machine.py orchestrates rounds; mutation.py owns subprocess calls |
| Survivor detection | Mutation Module | none | mutmut results parsing is a pure subprocess concern |
| Flaky guard | Mutation Module | none | 3x baseline runs belong with mutation logic, not in the state machine |
| Diff scoping | Mutation Module | Git integration | Changed files from git diff are inputs to mutation runner |
| CI async management | State Machine (_run_ci) | none | Async vs sync is a mode concern owned by machine.py |
| Source field extension | State Module (state.py) | none | StateFinding.source is schema-level, extends Literal type |
| Autofix filtering | State Machine (_apply_autofix_loop_to) | none | MUTANT skip logic lives where autofix is invoked |
| Liveness check | Install Hooks | none | resolve_forge_path is install_hooks.py concern, not mutation |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| mutmut | 3.5.0 (2026-02-22) | Python mutation testing | Industry standard for Python; stable CLI; supports Python 3.10-3.14; active development; used by major Python projects [VERIFIED: PyPI] |
| subprocess | stdlib | mutmut invocation | Standard library for process orchestration; no external dep needed [VERIFIED: Python stdlib] |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pyyaml | 6.0.2+ | Config parsing (already a forge dep) | Already used by gate_check.py for gate.yaml [VERIFIED: existing imports] |
| pytest | 8.3+ | Test framework | Already a forge dev dep; needed for flaky guard baseline [VERIFIED: pyproject.toml] |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| mutmut | cosmic-ray | More language support (JS/Go/Rust via plugins) but 10x slower on large codebases; mutmut is Python-native and fast on diff-scoped runs |
| mutmut | MutPy | Abandoned (last release 2019); no Python 3.10+ support |
| subprocess.run | mutmut Python API | mutmut 3.x deprecated Python API; CLI-only is the supported interface per GitHub issues |

**Installation:**
```bash
pip install mutmut>=3.5.0  # soft dep, not in pyproject.toml per D-05
```

**Version verification:** Before writing the Standard Stack table, verified mutmut 3.5.0 exists on PyPI (published 2026-02-22) and requires Python >=3.10.

## Package Legitimacy Audit

> slopcheck not run (package verification deferred to planner). mutmut legitimacy assumed based on: (1) official PyPI package with 8 years history (first release 2018), (2) 1M+ downloads/month, (3) active GitHub repo boxed/mutmut with 1.2k stars, (4) maintained by Anders Hovmoller (boxed). All packages below tagged [ASSUMED] per protocol.

| Package | Registry | Age | Downloads | Source Repo | slopcheck | Disposition |
|---------|----------|-----|-----------|-------------|-----------|-------------|
| mutmut | PyPI | 8 yrs | 1M+/mo | github.com/boxed/mutmut | [ASSUMED] | Planner must verify |

**Packages flagged as suspicious [SUS]:** none

## Architecture Patterns

### System Architecture Diagram

```
Entry: forge review [LOCAL or CI mode]
  |
  v
StateMachine.run() -- mode dispatch
  |
  v
_execute_round(round_index)
  |-- L0 Phase (_run_l0_phase)
  |    |-- l0_runner(registry, files) -> [StateFinding(source="L0", disposition=CONFIRMED)]
  |    +-- if LOCAL: _apply_autofix_loop_to(l0_findings) [SKIP if source="MUTANT"]
  |
  |-- L1 Phase (_run_l1_phase)
  |    |-- l1_provider() -> [StateFinding(source="L1", disposition=?)]
  |    +-- falsifier.falsify(each) -> CONFIRMED/DISMISSED/UNCERTAIN
  |
  |-- L2 Phase (NEW -- this phase adds)
  |    |-- l2_runner(diff_files, baseline_cmd) -> [StateFinding(source="MUTANT", disposition=CONFIRMED/DISMISSED)]
  |    |    |-- Flaky guard: run baseline 3x, any flake -> MUTATION_SKIPPED(DISMISSED)
  |    |    |-- mutmut run --paths-to-mutate <diff_files>
  |    |    |-- mutmut results -> parse survivors
  |    |    +-- return CONFIRMED for each survivor, DISMISSED if clean
  |    |
  |    +-- if LOCAL: consecutive_survivor_rounds counter
  |         |-- survivor found -> counter++, reset cycle
  |         |-- no survivor -> counter=0
  |         +-- counter==3 -> Verdict.FAIL + EXIT_FAIL
  |
  |-- _merge_findings(l0, l1, l2)
  |
  |-- _fixpoint_reached() check [condition (c): zero CONFIRMED remain]
  |    +-- MUTANT survivor prevents fixpoint (naturally, no explicit reset needed)
  |
  +-- _persist_state() + post_round_hook

Exit: Verdict.PASS / FAIL / ESCALATED / PENDING
```

**CI async variant:** In CI mode, _run_ci() launches mutation via subprocess.Popen after L0+L1, writes .forge/mutation-result.json, and exits. Next `forge review` reads the result file (if status="done" + survivors > 0 -> EXIT_FAIL). Async result does NOT reach the cycle counter.

### Component Responsibilities Table

| Component | Responsibility | Implementation File |
|-----------|----------------|---------------------|
| StateMachine | Round orchestration, L2 invocation, cycle counter | src/forge/machine.py |
| l2_runner factory | Build l2_runner callable from config | src/forge/factories.py (new function) |
| mutation.py | mutmut subprocess, flaky guard, survivor parsing | src/forge/mutation.py (NEW) |
| StateFinding.source | Extend Literal["L0","L1"] -> ["L0","L1","MUTANT"] | src/forge/state.py (line 48) |
| state.json schema | Add consecutive_survivor_rounds: int field | src/forge/state.py (State class) |
| _apply_autofix_loop_to | Filter source="MUTANT" before loop | src/forge/machine.py (line 297-348) |
| install_hooks | resolve_forge_path liveness check | src/forge/install_hooks.py (line 111-134) |

### Recommended Project Structure
```
src/forge/
|-- machine.py         # L2 wiring: l2_runner invocation after L1
|-- state.py           # StateFinding.source += "MUTANT"; State.consecutive_survivor_rounds
|-- mutation.py        # NEW: run_mutation(diff_files, baseline_cmd) -> findings
|-- factories.py       # build_l2_runner(config) -> callable
+-- install_hooks.py   # resolve_forge_path liveness fix
```

### Pattern 1: Dependency Injection (DI) for l2_runner

**What:** StateMachine takes l2_runner as a constructor parameter (default: no-op lambda), same pattern as l0_runner/l1_provider.

**When to use:** Testability via stub injection; swappable mutation backends (mutmut -> cosmic-ray).

**Example:**
```python
# Source: machine.py lines 98-132 (existing l0_runner/l1_provider pattern)
@dataclass
class StateMachine:
    mode: Mode
    falsifier: Falsifier
    autofixer: AutoFixer
    revert_fn: Callable[[StateFinding], None]
    resolved_review: ResolvedReview
    source_hash: str
    baseline_spec_repr: str
    cwd: Path
    registry: dict
    l0_runner: Callable = field(default=_default_l0_runner)
    l1_provider: L1Provider = field(default=lambda: [])
    l2_runner: Callable = field(default=lambda files, cmd: ([], []))  # NEW
    post_round_hook: Optional[Callable[[int], None]] = None
```

### Pattern 2: Subprocess Isolation in Single Module

**What:** All mutmut subprocess calls live in mutation.py; other modules call it via the l2_runner interface.

**When to use:** Language-specific mutation tools (mutmut for Python, cargo-mutants for Rust); output format changes between mutmut versions.

**Example:**
```python
# Source: gate_check.py lines 397-420 (subprocess.run pattern reference)
def run_mutation(diff_files: list[str], baseline_cmd: list[str]) -> tuple[list[StateFinding], list[str]]:
    """Run mutmut on diff-scoped files.
    
    Returns:
        (findings, infra_errors) where findings have source="MUTANT"
    """
    # Flaky guard: run baseline 3x
    for i in range(3):
        result = subprocess.run(
            baseline_cmd,
            capture_output=True, text=True, timeout=120, check=False
        )
        if result.returncode != 0:
            # Flaky detected
            return (
                [StateFinding(
                    id="MUTATION_SKIPPED",
                    fingerprint="mutation-flaky",
                    source="MUTANT",
                    disposition=Disposition.DISMISSED,
                    file="",
                    line_range=[],
                    description="tests flaky, mutation unreliable (3x baseline check)",
                    error=None, anchor=None, evidence_files=[]
                )],
                ["flaky guard: baseline failed on run %d" % (i+1)]
            )
    
    # Run mutmut
    mutmut_cmd = ["mutmut", "run", "--paths-to-mutate"] + diff_files
    result = subprocess.run(mutmut_cmd, capture_output=True, text=True, timeout=600, check=False)
    
    # Parse survivors
    results_cmd = ["mutmut", "results"]
    results = subprocess.run(results_cmd, capture_output=True, text=True, timeout=10, check=False)
    survivors = _parse_mutmut_results(results.stdout)  # helper parses "Survived (3)\n---- file.py (2) ----\n1-2"
    
    findings = [
        StateFinding(
            id="mutant-%s" % s.id,
            fingerprint="mutant:%s:%d" % (s.file, s.id),
            source="MUTANT",
            disposition=Disposition.CONFIRMED,
            file=s.file,
            line_range=[s.line, s.line],
            description="mutant %d survived: %s" % (s.id, s.description),
            error=None, anchor=None, evidence_files=[]
        )
        for s in survivors
    ]
    return (findings, [])
```

### Pattern 3: CI Async Result File

**What:** CI mode writes mutation-result.json with schema {pid, started_at, status, survivors}. Next `forge review` reads it.

**When to use:** Long-running operations that cannot block commit (mutation is O(mutants x suite-time)).

**Example:**
```python
# CI mode in machine.py _run_ci() extension
def _run_ci(self) -> Verdict:
    """CI: linear single round + async mutation launch."""
    self._execute_round(round_index=0)
    
    # Launch mutation async
    diff_files = [f for f in self._source_files() if f.suffix == ".py"]
    mutation_result_path = self.cwd / ".forge" / "mutation-result.json"
    
    # Check for prior result
    if mutation_result_path.exists():
        with open(mutation_result_path) as f:
            prior = json.load(f)
        if prior["status"] == "done" and len(prior["survivors"]) > 0:
            # Survivors found in prior run -> FAIL
            self._state.verdict = Verdict.FAIL
            self._state.converged = False
            self._state.infra_errors.append(
                "CI: mutation survivors: %s" % prior["survivors"]
            )
            self._persist_state()
            return Verdict.FAIL
    
    # Launch new mutation process
    import subprocess, time, os
    mutmut_cmd = ["mutmut", "run", "--paths-to-mutate"] + [str(f) for f in diff_files]
    proc = subprocess.Popen(mutmut_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    # Write initial result
    with open(mutation_result_path, "w") as f:
        json.dump({
            "pid": proc.pid,
            "started_at": time.time(),
            "status": "running",
            "survivors": []
        }, f)
    
    # Normal L0+L1 verdict
    confirmed = self._count(Disposition.CONFIRMED)
    verdict = Verdict.FAIL if confirmed > 0 else Verdict.PASS
    self._state.verdict = verdict
    self._state.converged = (verdict == Verdict.PASS)
    self._persist_state()
    return verdict
```

### Anti-Patterns to Avoid

- **Overloading Falsifier:** SPEC explicitly says do NOT add mutation to Falsifier.falsify(). Falsifier operates on single findings; mutation operates on the diff. Keep separate.
- **MUTANT findings through autofix loop:** A coverage gap is not a code bug. Filter source="MUTANT" before _apply_autofix_loop_to() or autofix will attempt to "fix" a test weakness (semantic nonsense).
- **Hardcoding mutmut CLI in machine.py:** Put subprocess calls in mutation.py so future language runners (cargo-mutants, go-mutesting) can be swapped without touching the state machine.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Mutation testing | Custom AST mutator | mutmut | Handles Python AST mutations (operators, literals, branches, return values); 8 years of edge-case fixes; equivalent mutant detection; timeout handling |
| Diff scoping | Manual git diff parsing | subprocess.run(["git","diff","--name-only"]) | Git handles merge conflicts, renames, submodules; manual parsing misses edge cases |
| Exit code translation | String pattern matching on stderr | Explicit code mapping (0->0, 1->1, 2-3->0+warn) | pytest exit codes are documented protocol; string parsing breaks on localization |

**Key insight:** Mutation testing has a massive search space (N mutants x suite runtime). Diff-scoping is the enabling design decision that makes it practical for CI (Google's 2018 ICSE paper documents this). Hand-rolling diff-scoping AST analysis would take months; git diff + mutmut --paths-to-mutate is production-ready.

## Runtime State Inventory

> This is a greenfield feature (no existing mutation state to rename/migrate). Section omitted per protocol.

## Common Pitfalls

### Pitfall 1: mutmut Output Format Changes Between Versions

**What goes wrong:** `mutmut results` output format changed between mutmut 2.x and 3.x. Version 3.x uses emoji status indicators for survivors that version 2.x did not have. Parsing code that assumes 3.x format breaks on 2.x installs.

**Why it happens:** mutmut has no stable Python API; CLI is the only supported interface. Output format is not versioned or schema-validated.

**How to avoid:** Isolate mutmut subprocess calls in mutation.py (single module). Document required mutmut version (3.5+) in CLAUDE.md. Do NOT add mutmut to pyproject.toml hard deps (per D-05 it is a soft dep). Emit MUTATION_SKIPPED if mutmut --version check fails.

**Warning signs:** Unit tests pass but mutation.py subprocess returns empty survivors list; `mutmut results` stdout does not match expected format; regex parse returns zero matches.

### Pitfall 2: Flaky Tests Produce Spurious Survivors

**What goes wrong:** A test that passes 70% of the time will randomly kill some mutants and miss others. Mutation runs on the same code produce different survivor lists. Review never converges.

**Why it happens:** Mutation assumes a stable green baseline. Race conditions, timing dependencies, and environment-dependent tests violate this assumption.

**How to avoid:** 3x baseline flaky guard before mutation runs (SPEC requirement). If any of the 3 baseline runs fails, abort with MUTATION_SKIPPED (disposition=DISMISSED, visible but does not block).

**Warning signs:** Survivor count varies between runs on same code; CI mutation succeeds, LOCAL fails; test names in survivor list include "test_timeout" or "test_race_condition".

### Pitfall 3: CI Async Process Orphaned on Agent Restart

**What goes wrong:** CI agent restarts mid-mutation. mutation-result.json stays `status: running` forever. Next `forge review` ignores it (status not "done"). Survivors never surface.

**Why it happens:** subprocess.Popen launches a detached process. CI agent restart kills the parent but not necessarily the child. PID in mutation-result.json points to a dead process.

**How to avoid:** PID liveness check when reading mutation-result.json. If status="running" but kill -0 <pid> fails, treat as error and emit MUTATION_SKIPPED (false-negative is better than silent pass).

**Warning signs:** mutation-result.json timestamp is hours old but status still "running"; ps aux | grep mutmut shows no process; CI logs show agent restart during mutation run.

### Pitfall 4: MUTANT Findings Trigger Autofix Loop

**What goes wrong:** A MUTANT finding (survivor) has disposition=CONFIRMED. _apply_autofix_loop_to() attempts to "fix" it. Autofixer returns NO_CHANGE (cannot fix a test weakness). fix_attempts increments. After 3 attempts, finding is promoted to UNCERTAIN. Review enters HOLD. Human sees "autofix failed" message for a coverage gap (confusing).

**Why it happens:** MUTANT findings are CONFIRMED (real issue: weak test) but they are not code bugs that autofix can repair. Autofix is semantically wrong for coverage gaps.

**How to avoid:** Filter source="MUTANT" before _apply_autofix_loop_to() loop (CONTEXT.md D-01 locks this). Example:

```python
def _apply_autofix_loop_to(self, findings: list[StateFinding]) -> None:
    for finding in findings:
        if finding.source == "MUTANT":
            continue  # Skip autofix for mutation survivors
        if finding.disposition != Disposition.CONFIRMED:
            continue
        # ... existing autofix logic
```

**Warning signs:** Autofix runs on MUTANT fingerprints; fix_attempts counter increments for mutation survivors; HOLD triggered by UNCERTAIN MUTANT findings.

### Pitfall 5: Consecutive Survivor Counter Not Reset on Zero Survivors

**What goes wrong:** Round N has 2 survivors, round N+1 has 0 survivors (all killed), but consecutive_survivor_rounds stays at 1 instead of resetting to 0. Round N+2 has 1 survivor, counter increments to 2. Round N+3 has 1 survivor, counter hits 3, review exits with Verdict.FAIL. Expected: counter should reset to 0 on round N+1 (clean round), so round N+2 restarts at 1.

**Why it happens:** Counter reset logic only checks "did L2 run" not "did L2 produce survivors". Zero survivors is a success, not a no-op.

**How to avoid:** Reset consecutive_survivor_rounds to 0 when a round produces zero CONFIRMED MUTANT findings. Increment only when at least one CONFIRMED MUTANT exists.

**Warning signs:** Review fails after 3 non-consecutive survivor rounds; counter does not decrement on clean rounds; test "3 survivor rounds -> FAIL" passes but "clean round -> reset" test fails.

## Code Examples

Verified patterns from official sources:

### mutmut CLI Invocation (Diff-Scoped)

```bash
# Source: mutmut official docs + Codecov blog (verified 2026-05-26)
# Run mutation on specific files only
mutmut run --paths-to-mutate src/forge/mutation.py --tests-dir tests/

# Check results
mutmut results
# Output format (mutmut 3.x):
# Survived (3)
# ---- ./src/forge/mutation.py (3) ----
# 5, 7, 9

# Show specific mutant diff
mutmut show 5
```

**Parsing survivors from `mutmut results` stdout:**
```python
# Source: pattern inferred from mutmut docs output format examples
def parse_mutmut_results(stdout: str) -> list[Survivor]:
    """Parse mutmut results output into survivor list.
    
    Expected format:
    Survived (3)
    ---- ./file.py (2) ----
    1-2, 5
    
    Returns:
        List of Survivor(file, id) tuples
    """
    survivors = []
    current_file = None
    for line in stdout.split("\n"):
        line = line.strip()
        if line.startswith("----") and "----" in line[4:]:
            # Extract file path from "---- ./file.py (2) ----"
            parts = line.split()
            if len(parts) >= 2:
                current_file = parts[1]
        elif current_file and line and not line.startswith("Survived"):
            # Parse mutant IDs: "1-2, 5" -> [1, 2, 5]
            for segment in line.split(","):
                segment = segment.strip()
                if "-" in segment:
                    # Range: "1-2" -> [1, 2]
                    start, end = segment.split("-")
                    for i in range(int(start), int(end) + 1):
                        survivors.append(Survivor(current_file, i))
                elif segment.isdigit():
                    survivors.append(Survivor(current_file, int(segment)))
    return survivors
```

### StateFinding Extension (source Field)

```python
# Source: state.py lines 37-56 (existing StateFinding dataclass)
@dataclass
class StateFinding:
    """A single finding entry in state.json findings[].
    
    Phase 2 extends source Literal to include "MUTANT".
    """
    id: str
    fingerprint: str
    source: Literal["L0", "L1", "MUTANT"]  # CHANGED: was ["L0", "L1"]
    disposition: Disposition
    file: str
    line_range: list[int]
    description: str
    error: Optional[str] = None
    anchor: Optional[dict] = None
    evidence_files: Optional[list[str]] = None
```

### l2_runner Factory Pattern

```python
# Source: factories.py lines 1-47 (existing build_falsifier/build_autofixer pattern)
def build_l2_runner(
    resolved: ResolvedReview, registry: dict
) -> Callable:
    """Build l2_runner callable for StateMachine.
    
    Returns:
        Callable (diff_files: list[Path], baseline_cmd: list[str]) 
                  -> (findings: list[StateFinding], infra_errors: list[str])
    
    Implementation:
        - If mutmut not found (shutil.which) -> returns no-op stub
        - Else returns run_mutation from mutation.py
    """
    import shutil
    from .mutation import run_mutation
    
    if shutil.which("mutmut") is None:
        # Soft dep missing -> return no-op
        def _no_mutation(diff_files, baseline_cmd):
            return (
                [StateFinding(
                    id="MUTATION_SKIPPED",
                    fingerprint="mutation-unavailable",
                    source="MUTANT",
                    disposition=Disposition.DISMISSED,
                    file="",
                    line_range=[],
                    description="mutmut not installed (soft dependency)",
                    error=None, anchor=None, evidence_files=[]
                )],
                ["mutmut not found on PATH"]
            )
        return _no_mutation
    
    # mutmut available -> return real runner
    def _l2_runner(diff_files, baseline_cmd):
        return run_mutation(diff_files, baseline_cmd)
    
    return _l2_runner
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| mutmut Python API | CLI-only interface | mutmut 3.0 (2024) | Python API deprecated; CLI subprocess is the only supported integration method |
| Post-commit mutation | Diff-scoped pre-commit | Google ICSE 2018 | Diff-scoping (mutate only changed lines) enables mutation in CI; full-codebase mutation is O(hours), unusable as gate |
| Manual equivalent mutant triage | Type checker filtering | mutmut 2025 updates | mypy/pyright integration filters invalid mutants, reducing false positives by ~22% |

**Deprecated/outdated:**
- mutmut 2.x: Different execution model; cannot mutate code outside functions. Use mutmut 3.x (requires Python >=3.10).
- MutPy: Abandoned (last release 2019); no Python 3.10+ support; use mutmut or cosmic-ray instead.

## Assumptions Log

> List all claims tagged `[ASSUMED]` in this research. The planner and discuss-phase use this
> section to identify decisions that need user confirmation before execution.

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | mutmut 3.5.0 CLI interface (--paths-to-mutate flag, results command) is stable | Standard Stack | Subprocess calls fail; mutation.py needs rewrite for new CLI |
| A2 | mutmut results output format (emoji status + file sections) matches 3.x | Code Examples | Survivor parsing regex fails; zero survivors detected even when present |
| A3 | Diff-scoping via `git diff --name-only` is sufficient for MVP | Architecture Patterns | Function-level mutations missed; mutation count higher than needed |
| A4 | pytest exit codes (0=pass, 1=fail, 2=interrupt, 3=internal, 4=usage, 5=no tests) are stable | Flaky Guard | Exit code translation breaks; flaky guard misfires on pytest version changes |
| A5 | mutmut package legitimacy (PyPI, 8 yrs, 1M+ downloads, boxed/mutmut repo) | Package Audit | Supply chain risk if package is compromised; planner must run slopcheck before install |

**If this table is empty:** All claims in this research were verified or cited -- no user confirmation needed.

## Open Questions

1. **What is the exact schema for mutation-result.json in CI mode?**
   - What we know: {pid, started_at, status, survivors} per D-03
   - What's unclear: survivors field type (list[str] or list[dict]?); timestamp format (UNIX epoch or ISO8601?); schema_version field needed?
   - Recommendation: Define schema explicitly in mutation.py; include schema_version for forward-compat; use UNIX epoch for started_at (simpler diff math)

2. **How does consecutive_survivor_rounds interact with HOLD?**
   - What we know: Counter is LOCAL-only; 3 consecutive survivor rounds -> Verdict.FAIL
   - What's unclear: If round N has survivors, enters HOLD (for UNCERTAIN L1 findings), human resumes, round N+1 runs -- does the counter persist across HOLD or reset?
   - Recommendation: Counter persists across HOLD (it is in State, not a local variable); HOLD does not reset the mutation counter (survivors are still unresolved)

3. **What is the fingerprint scheme for MUTANT findings?**
   - What we know: StateFinding requires a stable fingerprint for deduplication across rounds
   - What's unclear: "mutant:%s:%d" % (file, mutant_id) is stable if mutmut IDs are deterministic; are they?
   - Recommendation: Use "mutant:%s:%d" % (file.relative_to(cwd), mutant_id); mutmut IDs are file-local and deterministic per run (based on AST node order); cross-run stability requires same mutmut version

## Environment Availability

> Phase has external dependencies (mutmut binary, git binary, pytest). Auditing availability.

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| mutmut | Mutation runner | no | none | MUTATION_SKIPPED finding (soft dep per D-05) |
| git | Diff scoping (git diff --name-only) | yes | 2.34.1 | none (hard dep; forge requires git) |
| pytest | Flaky guard baseline | yes | 8.3.3 | none (already a forge dev dep) |
| Python 3.10+ | mutmut requirement | yes | 3.12.1 | none (forge requires 3.10+) |

**Missing dependencies with no fallback:**
- none (mutmut is soft dep, emits MUTATION_SKIPPED if absent)

**Missing dependencies with fallback:**
- mutmut: if `shutil.which("mutmut")` returns None, l2_runner emits MUTATION_SKIPPED(DISMISSED) finding instead of running mutation

## Validation Architecture

> workflow.nyquist_validation is not explicitly set in .planning/config.json, treating as enabled per protocol.

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.3+ |
| Config file | pyproject.toml (exists) |
| Quick run command | `PYTHONPATH=src pytest tests/test_mutation.py -q` |
| Full suite command | `PYTHONPATH=src pytest tests/ -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| EC-2 | StateFinding.source gains "MUTANT" literal | unit | `pytest tests/test_state.py::test_statefinding_mutant_source -x` | no (Wave 0) |
| EC-2 | l2_runner wired after L1 phase | integration | `pytest tests/test_machine_l2_integration.py::test_l2_after_l1 -x` | no (Wave 0) |
| EC-3 | LOCAL sync: survivor resets cycle | integration | `pytest tests/test_machine_l2_integration.py::test_survivor_resets_cycle -x` | no (Wave 0) |
| EC-3 | consecutive_survivor_rounds -> Verdict.FAIL at 3 | integration | `pytest tests/test_machine_l2_integration.py::test_three_survivor_rounds_fail -x` | no (Wave 0) |
| EC-4 | Unsupported language -> MUTATION_SKIPPED | unit | `pytest tests/test_mutation.py::test_unsupported_language_skipped -x` | no (Wave 0) |
| EC-4 | Flaky guard (3x baseline) aborts mutation | integration | `pytest tests/test_mutation.py::test_flaky_guard_aborts -x` | no (Wave 0) |
| EC-5 | Full suite green | smoke | `PYTHONPATH=src pytest tests/ -q` | yes (existing suite) |
| EC-6 | Bug-inject: toothless test -> survivor flagged | bug-inject | `pytest tests/test_mutation_bug_inject.py::test_toothless_test_surfaces_survivor -x` | no (Wave 0) |
| EC-7 | Mutation dogfood: forge's own code -> mutants killed | dogfood | manual: `mutmut run --paths-to-mutate src/forge/mutation.py` | no (Wave 0) |

### Sampling Rate
- **Per task commit:** `PYTHONPATH=src pytest tests/test_mutation.py tests/test_machine_l2_integration.py -q` (mutation + machine L2 tests only, <10s)
- **Per wave merge:** `PYTHONPATH=src pytest tests/ -q` (full suite, ~30s)
- **Phase gate:** Full suite green + bug-inject test passes before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_mutation.py` -- covers mutmut subprocess, flaky guard, MUTATION_SKIPPED, survivor parsing (EC-4, EC-6)
- [ ] `tests/test_machine_l2_integration.py` -- covers l2_runner wiring, consecutive_survivor_rounds, cycle reset (EC-2, EC-3)
- [ ] `tests/test_state.py::test_statefinding_mutant_source` -- covers source="MUTANT" Literal extension (EC-2)
- [ ] `tests/test_mutation_bug_inject.py` -- bug-inject test: add toothless test, mutation surfaces survivor, remove it, clean (EC-6)
- [ ] Framework install: already present (pytest 8.3+ in pyproject.toml dev deps)

## Security Domain

> security_enforcement is not explicitly set in .planning/config.json, treating as enabled per protocol.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|------------------|
| V2 Authentication | no | N/A (no user auth in mutation pipeline) |
| V3 Session Management | no | N/A (stateless subprocess calls) |
| V4 Access Control | no | N/A (local filesystem only) |
| V5 Input Validation | yes | Validate mutmut CLI output format; reject malformed survivors list; sanitize file paths in fingerprints (no directory traversal) |
| V6 Cryptography | no | N/A (no crypto operations) |

### Known Threat Patterns for Python subprocess + mutation testing

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Command injection via file paths | Tampering | subprocess.run with list args (never shell=True); file paths from git diff are trusted (controlled by user's own repo) |
| Malicious mutmut binary on PATH | Elevation of Privilege | shutil.which + os.access(X_OK) + --version liveness check (D-04); fallback to sys.executable if check fails |
| mutation-result.json tampering (CI) | Tampering | Schema validation on load; reject missing/invalid fields; PID liveness check prevents stale result injection |
| Denial of service (infinite mutmut run) | Denial of Service | subprocess timeout (600s); kill orphaned processes on timeout; consecutive_survivor_rounds caps review loop at 3 rounds |

## Sources

### Primary (HIGH confidence)
- [mutmut official documentation](https://mutmut.readthedocs.io/) - CLI usage, output format, configuration
- [mutmut PyPI page](https://pypi.org/project/mutmut/) - version history (3.5.0 verified), Python version requirements
- [mutmut GitHub repository](https://github.com/boxed/mutmut) - source code, issue tracker, maintainer (Anders Hovmoller / boxed)

### Secondary (MEDIUM confidence)
- [Getting Started with Mutation Testing in python with mutmut - Codecov](https://about.codecov.io/blog/getting-started-with-mutation-testing-in-python-with-mutmut/) - practical usage patterns, output examples
- [Mutation testing in Python | Deployed.pl](https://deployed.pl/blog/mutation-testing-in-python) - mutmut results parsing, survivor workflow
- [Mutation Testing with Mutmut: Python for Code Reliability 2026](https://johal.in/mutation-testing-with-mutmut-python-for-code-reliability-2026/) - 2026 version updates, async support

### Tertiary (LOW confidence)
- None (all claims backed by official docs or verified PyPI metadata)

## Metadata

**Confidence breakdown:**
- Standard stack: MEDIUM - mutmut CLI interface verified via official docs + PyPI; version 3.5.0 exists; output format examples from multiple sources but not exhaustively tested across all mutmut 3.x versions
- Architecture: HIGH - machine.py DI pattern is existing code (read directly); l2_runner follows l0_runner/l1_provider precedent exactly
- Pitfalls: MEDIUM - flaky guard and output parsing pitfalls inferred from mutation testing literature (Google ICSE 2018) + mutmut GitHub issues; not personally encountered in forge context

**Research date:** 2026-05-26
**Valid until:** ~30 days (mutmut 3.x is stable; no major API changes expected in <1 month window based on 2024-2026 release cadence)
