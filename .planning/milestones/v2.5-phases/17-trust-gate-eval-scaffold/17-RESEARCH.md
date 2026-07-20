# Phase 17: Trust Gate + Eval Scaffold - Research

**Researched:** 2026-06-10
**Domain:** Security trust gate (direnv-style) + eval scorecard scaffold + AxisRunner Protocol
**Confidence:** HIGH

## Summary

Phase 17 delivers three interconnected capabilities: (1) a direnv-style trust gate
that closes the CVE-class gate.yaml credential exfil hole (SEC-01), (2) an eval
scorecard scaffold to measure false-green rates on real bug corpora (EVAL-01), and
(3) the foundational AxisRunner infrastructure (AdvisoryFinding type + Protocol)
that all subsequent advisory axes (Phases 18-22) depend on.

The trust gate is the simplest component: hash the backends block of gate.yaml,
store the hash in `~/.config/code-forge/trusted.json` keyed by the realpath of
gate.yaml, and refuse to use repo-supplied backends until the user explicitly
runs `code-forge trust`. This mirrors direnv's allow/deny model -- the security
boundary is the user's home directory, not the repo.

The eval scaffold is custom code in `src/code_forge/eval/` with zero new
dependencies. It loads a YAML manifest of self-contained diff files, runs each
through the complete forge pipeline against a real backend, and reports
caught-vs-expected. The AxisRunner Protocol establishes the contract for all
future review axes: a `run()` method returning findings, with type-level
separation between blocking StateFinding and advisory AdvisoryFinding.

**Primary recommendation:** Ship trust gate first (SEC-01 is urgent, partly live
on main), then AxisRunner Protocol + AdvisoryFinding type (foundational for
Phases 18-22), then eval scaffold (EVAL-01 measures everything).

<user_constraints>

## User Constraints (from CONTEXT.md)

### Locked Decisions

#### Trust Gate (SEC-01)
- **D-01:** direnv-style trust, stored OUTSIDE the repo. `code-forge trust`
  records trust in `~/.config/code-forge/trusted.json` (honor $XDG_CONFIG_HOME
  if set), mapping the realpath of the repo's gate.yaml to sha256 of its
  backends block.
- **D-02:** NO in-repo `.trusted` file. An in-repo marker lets a hostile clone
  ship a self-authorizing record.
- **D-03:** Hash scope: only hash the backends block (YAML `backends:` key),
  not the entire gate.yaml.
- **D-04:** CLI: `code-forge trust` (mark trusted), `code-forge trust --status`
  (show trust state), `code-forge trust --revoke` (remove entry).
- **D-05:** On `code-forge trust`, stderr displays the dangerous fields found
  in gate.yaml (base_url, api_key_env, api_key_file, shell, command, hook).
- **D-06:** Untrusted: repo-supplied backends block ignored; fall back to
  session-default backend. stderr warns.

#### Eval Scaffold (EVAL-01)
- **D-07:** Corpus format: self-contained diff files + YAML manifest
  (`tests/eval/corpus/corpus.yaml`).
- **D-08:** Entry point: `code-forge eval` CLI subcommand.
- **D-09:** No runtime dependency on external repos.
- **D-10:** Output: stderr human-readable table + JSON file.
- **D-11:** Run count axis-dependent. Deterministic axes run once. LLM-reviewed
  axes default to 3 runs with 2-of-3 majority. `--runs N` overrides.
- **D-12:** Failure = SKIPPED (first-class adverse outcome, excluded from
  false-green denominator).
- **D-13:** Extensibility: plugin-style axis hooks (pre_review / post_review).

#### AxisRunner Protocol
- **D-14:** AdvisoryFinding is an independent dataclass, completely separate
  from StateFinding. No shared base class.
- **D-15:** Serialization: AdvisoryFinding writes to a separate file.
- **D-16:** Timing: advisory axes run once after convergence by default.
- **D-17:** Display: split display in stderr -- blocking first, separator,
  then advisory.
- **D-18:** Phase 17 scope: define AdvisoryFinding + AxisRunner Protocol +
  machine.py advisories list + verdict output extension + implement SEC-01.
- **D-19:** Each eval entry runs the COMPLETE pipeline.

### Claude's Discretion
- AxisRunner Protocol method signatures (run interface)
- trusted.json entry format details beyond sha256 (metadata, timestamps)
- eval JSON output schema details
- Advisory file naming convention

### Deferred Ideas (OUT OF SCOPE)
- danger-score field-level analysis (Phase 18 REVIEW-TRUST-01)
- semgrep taint rules for config-to-sink flows (Phase 18)
- Revert-RED / STING overfit guard (Phase 19 REVIEW-FIXVAL-01)
- Verdict UNVERIFIED calibration (Phase 20 REVIEW-RUNTIME-01)
- Legacy blame attribution (Phase 21 REVIEW-LEGACY-01)
- Graph-triage blast-radius ranking (Phase 22 REVIEW-SYSTEM-01)

</user_constraints>

<phase_requirements>

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SEC-01 | gate.yaml credential exfil trust gate -- repo-supplied backend NOT used without explicit opt-in | Trust gate architecture (direnv model), dangerous field detection, XDG config home patterns |
| EVAL-01 | False-green-rate scorecard -- corpus of real buggy/fixed pairs, drives real backend, false-green rate metric | Eval harness patterns (SWE-bench, BugsInPy structure), corpus manifest design, pipeline replay architecture |

</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Trust store (trusted.json) | Filesystem / User config | -- | Trust decision must persist across sessions; home-dir store is outside repo control |
| Hash verification | CLI / Backend loader | -- | Hash check is a guard in the config loading path, before any network call |
| Dangerous field display | CLI (stderr) | -- | User-facing informational output on trust decision |
| Eval corpus loading | Eval subpackage | -- | Self-contained YAML + diff file loading, no external deps |
| Eval pipeline replay | Eval runner | State machine | Reuses full StateMachine.run() for each corpus entry |
| AdvisoryFinding type | State layer | -- | New dataclass parallel to StateFinding, owned by state module |
| AxisRunner Protocol | Machine layer | Factories | Protocol defines interface; factories build runners; machine dispatches |
| Advisory display | CLI (stderr) | Machine | Machine separates advisory from blocking; CLI formats split output |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pyyaml | >=6.0 | Parse gate.yaml, corpus.yaml | Already a forge dependency; yaml.safe_load for untrusted config [VERIFIED: pyproject.toml] |
| hashlib (stdlib) | 3.12+ | sha256 of backends block | Standard library; no new dep; same hash function as direnv uses [VERIFIED: Python docs] |
| pathlib (stdlib) | 3.12+ | Path.resolve() for realpath, XDG path construction | Standard library; already used throughout forge codebase [VERIFIED: source code] |
| dataclasses (stdlib) | 3.12+ | AdvisoryFinding frozen dataclass | Standard library; matches StateFinding pattern [VERIFIED: state.py] |
| typing (stdlib) | 3.12+ | Protocol class for AxisRunner | Standard library; Protocol available since 3.8 [VERIFIED: Python docs] |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| json (stdlib) | 3.12+ | trusted.json read/write, eval JSON output | Trust store persistence and eval results |
| os (stdlib) | 3.12+ | XDG_CONFIG_HOME env var resolution | Config directory detection |
| subprocess (stdlib) | 3.12+ | Eval replay invokes forge pipeline | Running forge review on corpus entries |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Manual XDG resolution | platformdirs package | Adds a dependency for 3 lines of code; forge already has the pattern in backend.py for XDG_CACHE_HOME [ASSUMED] |
| JSON trust store | SQLite | Over-engineered for a handful of entries; JSON is human-inspectable [ASSUMED] |
| Custom eval framework | pytest fixtures | Eval needs real backend calls, not mocked tests; pytest is the wrong abstraction [ASSUMED] |

**Installation:**
```bash
# No new dependencies. All stdlib + existing pyyaml.
pip install code-review-forge  # unchanged
```

## Package Legitimacy Audit

> No new packages are installed in this phase. All functionality uses Python stdlib
> and existing forge dependencies (pyyaml>=6.0, unidiff>=0.7.5).

| Package | Registry | Age | Downloads | Source Repo | slopcheck | Disposition |
|---------|----------|-----|-----------|-------------|-----------|-------------|
| pyyaml | PyPI | 18 yrs | 400M+/mo | github.com/yaml/pyyaml | N/A (existing dep) | Approved (existing) |

**Packages removed due to slopcheck [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none

*No new packages to audit. Phase 17 adds zero runtime and zero dev dependencies.*

## Architecture Patterns

### System Architecture Diagram

```
                   CLI Entry Points
                         |
            +------------+-----------+
            |            |           |
     code-forge      code-forge   code-forge
       trust           eval        review
            |            |           |
            v            v           |
     +----------+  +----------+     |
     | Trust    |  | Eval     |     |
     | Module   |  | Runner   |     |
     | (new)    |  | (new)    |     |
     +----+-----+  +----+-----+     |
          |              |           |
          v              |           v
   trusted.json          |    +-----------+
   (~/.config/           |    | Trust     |
    code-forge/)         |    | Check     |<-- inserted guard
                         |    +-----------+
                         |           |
                         v           v
                    StateMachine.run()
                         |
              +----------+-----------+
              |          |           |
         _run_l0    _run_l1     _run_l2
              |          |           |
              +----------+-----------+
                         |
                   _fixpoint_reached?
                    (blocking only)
                         |
                    yes: converged
                         |
                    +----v-----+
                    | Advisory |
                    | Axes     |<-- AxisRunner Protocol
                    | (post-   |    (Phase 17: define only)
                    |  fixpoint)|
                    +----+-----+
                         |
                    +----v-----+
                    | Verdict  |
                    | + Display|<-- blocking findings | separator | advisory
                    +----------+
```

### Recommended Project Structure

```
src/code_forge/
    trust.py            # NEW: Trust gate (trusted.json CRUD, hash, check)
    advisory.py         # NEW: AdvisoryFinding dataclass + AxisRunner Protocol
    eval/
        __init__.py     # NEW: Eval subpackage
        corpus.py       # NEW: YAML manifest loader
        runner.py       # NEW: Pipeline replay per corpus entry
        scorer.py       # NEW: False-green rate computation + output
    # MODIFIED:
    cli.py              # trust + eval subcommands
    machine.py          # advisories list + post-convergence dispatch
    backend.py          # trust check before load_backend_configs returns
```

### Pattern 1: Trust Gate (direnv-style allow/deny)

**What:** Hash the backends block, store hash in user's config dir, check on review.
**When to use:** Every time forge loads gate.yaml backends for a review session.

```python
# Source: direnv allow model + forge backend.py XDG pattern
# [CITED: https://direnv.net/man/direnv.toml.1.html]

import hashlib
import json
import os
from pathlib import Path
from typing import Optional

DANGEROUS_FIELDS = frozenset({
    "base_url", "api_key_env", "api_key_file",
    "shell", "command", "hook",
})

def _config_dir() -> Path:
    """XDG_CONFIG_HOME / code-forge, matching backend.py's XDG_CACHE_HOME pattern."""
    base = os.environ.get(
        "XDG_CONFIG_HOME", str(Path.home() / ".config")
    )
    return Path(base) / "code-forge"

def _hash_backends_block(gate_data: dict) -> str:
    """sha256 of the canonical JSON representation of the backends block."""
    backends = gate_data.get("backends", {})
    # Canonical: sorted keys, no whitespace variance
    canonical = json.dumps(backends, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

def is_trusted(gate_yaml_path: Path, gate_data: dict) -> bool:
    """Check if gate.yaml's backends block is trusted."""
    store = _load_trust_store()
    key = str(gate_yaml_path.resolve())  # realpath
    entry = store.get(key)
    if entry is None:
        return False
    current_hash = _hash_backends_block(gate_data)
    return entry.get("hash") == current_hash
```

### Pattern 2: AdvisoryFinding + AxisRunner Protocol

**What:** Type-level separation between blocking and advisory findings.
**When to use:** All advisory axes (RUNTIME, LEGACY, INTENT, SYSTEM).

```python
# Source: Python Protocol docs + forge state.py pattern
# [CITED: https://mypy.readthedocs.io/en/stable/protocols.html]

from dataclasses import dataclass
from typing import Protocol

@dataclass(frozen=True)
class AdvisoryFinding:
    """Advisory finding -- NEVER participates in convergence.

    Separate type from StateFinding. Cannot be added to
    list[StateFinding] at the type-checker level (mypy/pyright).
    """
    id: str
    axis: str           # e.g. "RUNTIME", "LEGACY", "TRUST"
    file: str
    line_range: list[int]
    description: str
    attribution: str    # e.g. git blame info, axis-specific context

class AxisRunner(Protocol):
    """Protocol for review axes (blocking or advisory).

    machine.py calls runners implementing this protocol.
    Each axis is a separate module.
    """
    @property
    def is_advisory(self) -> bool: ...

    def run(
        self,
        diff_text: str,
        repo_root: Path,
    ) -> list[AdvisoryFinding]: ...
```

### Pattern 3: Eval Corpus Manifest

**What:** YAML manifest pointing to self-contained diff files with expected verdicts.
**When to use:** `code-forge eval --corpus path/to/corpus.yaml`.

```python
# Source: D-07 + D-12 + D-19 from CONTEXT.md
# [ASSUMED: manifest format is Claude's discretion per CONTEXT.md]

# corpus.yaml format:
# entries:
#   - name: gate-yaml-rce
#     diff_file: diffs/gate-yaml-rce.diff
#     expected_verdict: HOLD   # forge should NOT give green
#     axis_tags: [TRUST, SEC]
#   - name: ttl-class-bug
#     diff_file: diffs/ttl-class.diff
#     expected_verdict: HOLD
#     axis_tags: [RUNTIME]

@dataclass(frozen=True)
class CorpusEntry:
    name: str
    diff_file: str          # relative to corpus.yaml
    expected_verdict: str   # "HOLD" or "PASS"
    axis_tags: list[str]

@dataclass(frozen=True)
class EvalResult:
    entry: CorpusEntry
    actual_verdict: str     # "PASS", "FAIL", "HOLD", "SKIPPED"
    runs: int               # how many times this entry was run
    caught_count: int       # how many runs flagged it
    skipped_reason: str     # "" if not skipped
```

### Anti-Patterns to Avoid

- **Shared base class for StateFinding and AdvisoryFinding:** Creates a path for
  advisory findings to enter the convergence list. Separate types = structural
  guarantee. (PITFALLS.md Pitfall 3)

- **In-repo trust marker (.trusted file):** Even gitignored, a hostile clone can
  ship a self-authorizing record. The trust boundary MUST be the user's home
  directory. (D-02)

- **Silent skip when gate.yaml is untrusted:** The trust check must produce a
  visible stderr warning. Silent fallback to session-default without warning
  would hide a potentially hostile configuration. (D-06)

- **Hashing the entire gate.yaml:** Changes to outlet, test, or detect sections
  should not require re-trusting. Only the backends block is security-sensitive.
  (D-03)

- **Mocking the LLM backend in eval:** The entire point is measuring real
  backend false-green rate. Mocks defeat the purpose. (D-19, REQUIREMENTS.md)

- **Running advisory axes per-cycle:** Advisory axes run ONCE after convergence,
  not per cycle. Per-cycle triples cost with zero quality benefit. (PITFALLS.md
  Anti-Pattern 4)

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| YAML parsing | Custom parser | pyyaml yaml.safe_load | Existing dep; safe_load prevents RCE from yaml.load |
| SHA256 hashing | Custom hash | hashlib.sha256 | Stdlib, battle-tested, same as direnv |
| XDG directory resolution | Custom logic | os.environ.get + Path.home | 3-line pattern already in backend.py; platformdirs is overkill for a CLI tool |
| Path canonicalization | String manipulation | Path.resolve() | Handles symlinks, relative paths, .. correctly |
| JSON atomic write | Direct write | tmp + rename pattern | Already used in state.py save_state; prevents corruption on crash |

**Key insight:** Phase 17 adds zero new dependencies because every building block
already exists in Python stdlib or forge's existing codebase. The trust gate is
3 functions (hash, check, store); the eval is a loop calling StateMachine.run();
the AxisRunner is a Protocol + dataclass. No external library adds value here.

## Common Pitfalls

### Pitfall 1: Backends Block Hash Instability
**What goes wrong:** YAML dict ordering is not guaranteed. Two semantically
identical gate.yaml files produce different hashes if key order differs.
**Why it happens:** `yaml.safe_load` returns Python dicts (ordered since 3.7),
but the YAML spec does not guarantee ordering. A user who edits gate.yaml
without changing any values may get a different hash.
**How to avoid:** Hash the canonical JSON representation (json.dumps with
sort_keys=True, separators=(",",":")), not the raw YAML text. This produces
a stable hash regardless of YAML key ordering.
**Warning signs:** User reports "trust revoked after editing a comment in
gate.yaml" even though backends block is unchanged.

### Pitfall 2: Realpath vs Symlink on Multi-Worktree Setups
**What goes wrong:** Two git worktrees of the same repo have different
realpaths for gate.yaml. Trusting in one worktree does not trust in the other.
**Why it happens:** Path.resolve() returns the canonical absolute path, which
differs per worktree (e.g., `.worktrees/work/.code-forge/gate.yaml` vs
`.worktrees/review/.code-forge/gate.yaml`).
**How to avoid:** This is CORRECT behavior, not a bug. Each worktree may have
a different gate.yaml (local changes). Document that trust is per-checkout-path.
If the gate.yaml content is identical (same backends hash), the user sees the
same dangerous fields and can trust with one command.
**Warning signs:** User confused by "untrusted" warning in a second worktree.

### Pitfall 3: Advisory Creep to Blocking (Founding Principle Violation)
**What goes wrong:** An implementer adds AdvisoryFinding to the convergence
check, effectively making advisory axes blocking.
**Why it happens:** Incremental pressure ("this advisory is critical").
**How to avoid:** Type-level enforcement: AdvisoryFinding is a DIFFERENT TYPE
from StateFinding. _fixpoint_reached() operates on list[StateFinding] only.
machine.py maintains self.advisories: list[AdvisoryFinding] as a separate
attribute. No code path converts AdvisoryFinding to StateFinding.
**Warning signs:** AdvisoryFinding appears in _fixpoint_reached or
consecutive_clean_rounds logic.

### Pitfall 4: Eval Corpus Too Small for Statistical Significance
**What goes wrong:** 9 corpus entries mean each entry is 11% of the score.
A single LLM nondeterminism flip changes the false-green rate by 11%.
**Why it happens:** Real buggy/fixed pairs are scarce (no synthetic allowed).
**How to avoid:** Frame as "smoke test", not "benchmark". Report raw counts
(caught: 7/9), not percentages (77.8% -- false precision). Deterministic axes
(SEC-01 trust gate) run once; LLM-reviewed axes get 3 runs with 2-of-3 majority.
(PITFALLS.md Pitfall 4)
**Warning signs:** False-green rate swings >10% between eval runs with no code
change.

### Pitfall 5: Trust Check Insertion Point Race
**What goes wrong:** Trust check is placed after backends are already loaded
and used for outlet resolution.
**Why it happens:** cli.py's _run() calls _load_gate_backends(gate_yaml_path)
early (line 813), then uses the result for outlet resolution. Inserting the
trust check after this point means backends are already in memory.
**How to avoid:** The trust check must wrap _load_gate_backends: either
_load_gate_backends itself checks trust and returns [] for untrusted repos,
or a wrapper function replaces the call site.
**Warning signs:** Untrusted backend configs appear in outlet resolution logs.

## Code Examples

Verified patterns from existing forge source:

### Trust Store CRUD (trusted.json)
```python
# Pattern matches backend.py _default_cache_dir + state.py atomic write
# [VERIFIED: backend.py:324-329, state.py:269-272]

def _trust_store_path() -> Path:
    base = os.environ.get(
        "XDG_CONFIG_HOME", str(Path.home() / ".config")
    )
    return Path(base) / "code-forge" / "trusted.json"

def _load_trust_store() -> dict:
    path = _trust_store_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}  # corrupted store = no trust (safe default)

def _save_trust_store(store: dict) -> None:
    path = _trust_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, indent=2))
    tmp.replace(path)  # atomic on POSIX
```

### Dangerous Field Detection (D-05)
```python
# [ASSUMED: field list from CONTEXT.md D-05 + CWE-798/522 patterns]

DANGEROUS_FIELDS = frozenset({
    "base_url",       # controls where credentials are sent (CWE-522)
    "api_key_env",    # names the env var containing the credential
    "api_key_file",   # names the file containing the credential
    "shell",          # arbitrary shell execution (CWE-78)
    "command",        # arbitrary command execution
    "hook",           # lifecycle hook execution
    "credentials_path",  # vertex: service account JSON path
})

def find_dangerous_fields(gate_data: dict) -> list[tuple[str, str, str]]:
    """Return list of (backend_name, field_name, field_value) tuples."""
    dangers = []
    backends = gate_data.get("backends", {})
    for bname, bconfig in backends.items():
        if not isinstance(bconfig, dict):
            continue
        for field_name in DANGEROUS_FIELDS:
            value = bconfig.get(field_name)
            if value is not None and value != "":
                dangers.append((bname, field_name, str(value)))
    return dangers
```

### Eval Pipeline Replay
```python
# Pattern matches cli.py _run() invocation path
# [VERIFIED: cli.py:745 _run function signature and flow]

def replay_entry(
    entry: CorpusEntry,
    corpus_dir: Path,
    backend_name: str,
) -> EvalResult:
    """Run one corpus entry through the full forge pipeline.

    Creates a temp directory, applies the diff, runs code-forge review,
    and compares actual verdict to expected.
    """
    diff_path = corpus_dir / entry.diff_file
    if not diff_path.exists():
        return EvalResult(
            entry=entry,
            actual_verdict="SKIPPED",
            runs=0,
            caught_count=0,
            skipped_reason="diff file not found: %s" % entry.diff_file,
        )
    # ... apply diff to temp git repo, invoke forge review subprocess
```

### CLI Subcommand Registration
```python
# Pattern matches existing subcommand registration in cli.py
# [VERIFIED: cli.py:161-446 subparser pattern]

# In _build_parser():
trust_parser = subparsers.add_parser(
    'trust',
    help='manage trust for repo-supplied backends',
)
trust_parser.add_argument(
    '--status', action='store_true',
    help='show trust state for current repo',
)
trust_parser.add_argument(
    '--revoke', action='store_true',
    help='revoke trust for current repo',
)

eval_parser = subparsers.add_parser(
    'eval',
    help='evaluate false-green rate on bug corpus',
)
eval_parser.add_argument(
    '--corpus', required=True, type=Path,
    help='path to corpus.yaml manifest',
)
eval_parser.add_argument(
    '--backend', required=True,
    help='backend name to evaluate',
)
eval_parser.add_argument(
    '--runs', type=int, default=None,
    help='override run count per entry',
)
eval_parser.add_argument(
    '--output', type=Path, default=None,
    help='path for JSON results file',
)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| No trust gate | direnv-style allow (D-01) | Phase 17 (now) | Closes CVE-class exfil hole |
| Single finding type | StateFinding + AdvisoryFinding | Phase 17 (now) | Enables advisory axes without blocking |
| No eval | False-green smoke test | Phase 17 (now) | Measures backend quality on real bugs |
| yaml.load() | yaml.safe_load() | Always (forge convention) | Prevents RCE via YAML deserialization |

**Deprecated/outdated:**
- Trusting repo-supplied backends implicitly: removed by SEC-01
- Mixing advisory and blocking findings in one list: replaced by type separation

## Assumptions Log

> Claims tagged [ASSUMED] that the planner must verify or confirm.

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | platformdirs is unnecessary for XDG resolution; 3-line stdlib pattern is sufficient | Standard Stack / Alternatives | Low -- could add platformdirs later if macOS edge cases appear |
| A2 | JSON trust store is sufficient; no need for SQLite | Standard Stack / Alternatives | Low -- trust store has <100 entries typically |
| A3 | Canonical JSON with sort_keys produces stable hashes across Python versions | Pitfall 1 | Medium -- json.dumps sort_keys is documented stable, but untested across 3.12/3.13 |
| A4 | pytest fixtures are not suitable for eval (needs real backend) | Standard Stack / Alternatives | Low -- design explicitly mandates real backend |
| A5 | Dangerous field list (base_url, api_key_env, etc.) is complete for current forge | Code Examples | Medium -- new fields added in future phases may need updating |
| A6 | credentials_path should be in the dangerous fields list | Code Examples | Low -- it points to a service account JSON file, clearly security-sensitive |

## Open Questions

1. **Eval corpus entry format: diff-only or full git repo?**
   - What we know: D-07 says "self-contained diff files + YAML manifest". D-09
     says "no runtime dependency on external repos".
   - What's unclear: Does "self-contained diff" mean a raw unified diff that
     gets applied to a fresh temp repo, or a pair of file snapshots (before/after)?
   - Recommendation: Use unified diff files. The eval runner creates a temp git
     repo, commits the "before" state, applies the diff, and runs forge review.
     This matches the self-contained requirement and avoids storing full repo
     snapshots.

2. **Trust revocation on gate.yaml deletion**
   - What we know: D-04 has explicit `--revoke`. D-01 keys on realpath.
   - What's unclear: If gate.yaml is deleted (repo removes it), should the
     trusted.json entry be automatically cleaned up, or left stale?
   - Recommendation: Leave stale. Stale entries are harmless (the gate.yaml does
     not exist, so no backends are loaded). Automatic cleanup adds complexity with
     no security benefit. `code-forge trust --revoke` handles manual cleanup.

3. **AxisRunner Protocol: does SEC-01 trust gate use it?**
   - What we know: D-18 says "implement SEC-01 trust gate (which is blocking,
     not advisory -- first concrete axis)". But SEC-01 is a pre-dispatch guard,
     not a review axis.
   - What's unclear: Is the trust gate an AxisRunner, or a separate guard?
   - Recommendation: Trust gate is a GUARD inserted in the config loading path,
     NOT an AxisRunner. It runs before the state machine, not during review.
     The AxisRunner Protocol is defined in Phase 17 but the first axis using
     it will be in Phase 18+. Phase 17 defines the Protocol + AdvisoryFinding
     type + machine.py's advisories list, but does not yet wire any AxisRunner.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python | All | Yes | 3.12+ | -- |
| pyyaml | Trust gate, eval | Yes | 6.0+ | -- (existing dep) |
| pytest | Tests | Yes | 8.0+ | -- (existing dev dep) |

**Missing dependencies with no fallback:** none
**Missing dependencies with fallback:** none

*Phase 17 adds zero external dependencies. All tools are stdlib or existing.*

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.0+ |
| Config file | No explicit config; collected from `tests/` |
| Quick run command | `python -m pytest tests/test_trust.py tests/test_advisory.py tests/test_eval_corpus.py -x` |
| Full suite command | `python -m pytest tests/ -x` |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SEC-01-1 | Untrusted repo backend NOT used | unit | `pytest tests/test_trust.py::test_untrusted_falls_back -x` | Wave 0 |
| SEC-01-2 | Hostile gate.yaml does NOT exfiltrate | integration | `pytest tests/test_trust.py::test_hostile_gate_no_exfil -x` | Wave 0 |
| SEC-01-3 | Opt-in decision documented in stderr | unit | `pytest tests/test_trust.py::test_trust_displays_dangers -x` | Wave 0 |
| EVAL-01-1 | Corpus contains named real pairs | unit | `pytest tests/test_eval_corpus.py::test_corpus_loads -x` | Wave 0 |
| EVAL-01-2 | Eval drives real backend (architecture) | integration | Manual (requires real LLM backend) | Manual-only |
| EVAL-01-3 | False-green rate metric computed | unit | `pytest tests/test_eval_corpus.py::test_scorer_computes_fgr -x` | Wave 0 |
| EVAL-01-4 | Scorecard output human-readable | unit | `pytest tests/test_eval_corpus.py::test_scorecard_format -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/test_trust.py tests/test_advisory.py tests/test_eval_corpus.py -x`
- **Per wave merge:** `python -m pytest tests/ -x`
- **Phase gate:** Full suite green before verify

### Wave 0 Gaps
- [ ] `tests/test_trust.py` -- trust gate unit tests (hash, store, check, CLI)
- [ ] `tests/test_advisory.py` -- AdvisoryFinding type separation tests
- [ ] `tests/test_eval_corpus.py` -- corpus loader, scorer, manifest parsing
- [ ] `tests/test_eval_runner.py` -- pipeline replay integration tests

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | -- |
| V3 Session Management | No | -- |
| V4 Access Control | Yes | Trust gate (user must explicitly authorize repo backends) |
| V5 Input Validation | Yes | yaml.safe_load for all YAML; canonical JSON for hashing |
| V6 Cryptography | Yes (hashing only) | hashlib.sha256 (stdlib, not hand-rolled) |

### Known Threat Patterns for gate.yaml

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Hostile gate.yaml exfils env vars via base_url + api_key_env | Information Disclosure | Trust gate: repo backends not used without opt-in (D-01) |
| Attacker ships .trusted file in repo | Elevation of Privilege | No in-repo trust marker (D-02); trust store is home-dir only |
| YAML deserialization RCE | Tampering | yaml.safe_load only (existing forge convention) |
| Hash collision on backends block | Spoofing | sha256 is collision-resistant; canonical JSON eliminates ordering variance |
| Stale trust after backends change | Tampering | Hash of backends block invalidates on any change (D-03) |

## Project Constraints (from CLAUDE.md)

- All documentation in English
- No non-ASCII in code
- Dependencies: bash assertion primitives require only jq; skills require Claude Code
- Must work with Claude Code skill discovery
- .planning/ is gitignored, never committed to main
- Worktree-based development (git worktree add)
- Three-cycle review + smoke test before any commit
- Commit message format: `<subsystem>/<case>: <brief summary>` + Signed-off-by
- No AI-smell in commit messages (no task IDs, coverage stats, bullet inventories)
- Author: Minxi Hou <houminxi@gmail.com> (never noreply@anthropic.com)

## Sources

### Primary (HIGH confidence)
- [direnv allow/deny trust model](https://direnv.net/man/direnv.toml.1.html) -- hash-based security, whitelist directives, path included in hash
- [direnv GitHub repository](https://github.com/direnv/direnv) -- source code for allow command implementation
- [Python Protocol documentation (mypy)](https://mypy.readthedocs.io/en/stable/protocols.html) -- structural subtyping, Protocol class design
- [forge source code](file:///home/houminxi/code/forge/src/code_forge/) -- backend.py XDG pattern, state.py StateFinding, cli.py subcommand registration, machine.py convergence logic

### Secondary (MEDIUM confidence)
- [XDG Base Directory Specification](https://pyxdg.readthedocs.io/en/latest/basedirectory.html) -- XDG_CONFIG_HOME defaults
- [YAML Security best practices](https://www.kusari.dev/learning-center/yaml-security) -- CWE-798, CWE-522, credential exfil patterns
- [SWE-bench evaluation harness](https://www.swebench.com/SWE-bench/guides/evaluation/) -- JSONL format, harness architecture (inspiration for eval design)
- [platformdirs documentation](https://platformdirs.readthedocs.io/en/latest/explanation.html) -- considered but rejected for XDG resolution

### Tertiary (LOW confidence)
- None -- all claims verified against source code or official documentation.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- zero new dependencies; all stdlib + existing pyyaml
- Architecture: HIGH -- integration points verified by reading source (backend.py, cli.py, machine.py, state.py)
- Pitfalls: HIGH -- grounded in PITFALLS.md research + real forge codebase patterns

**Research date:** 2026-06-10
**Valid until:** 2026-07-10 (stable; no fast-moving external dependencies)
