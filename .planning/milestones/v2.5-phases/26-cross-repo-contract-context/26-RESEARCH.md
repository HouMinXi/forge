# Phase 26: Cross-Repo Contract Context - Research

**Researched:** 2026-06-21
**Domain:** YAML config loading, LLM summarization, file-based caching, prompt injection
**Confidence:** HIGH

## Summary

Phase 26 adds an opt-in mechanism that injects a sibling repository's contract
spec (e.g., kernel YNL YAML spec) into the L1 reviewer's prompt as a read-only
reference. The implementation is narrow in scope: a new `contracts.yaml` config
file, a loader with dataclass validation, a file reader with LLM summarization
for large specs, a sha256-keyed cache, and a new `contract_spec` parameter
threaded through `build_l1_provider` into the prompt before the diff section.

The codebase already has every building block needed. The conventions_resolver.py
module demonstrates the exact pattern (YAML config -> resolve paths -> extract
content -> cache by hash -> inject into prompt via `build_l1_provider`). The
trust.py module provides the `is_trusted` / `record_trust` mechanism that D-03
reuses. The factories.py prompt assembly uses string concatenation with named
section headers -- adding `## Contract Reference` follows the same pattern as
`## Conventions Digest` and `## Blast Radius Context`. No new dependencies are
required.

**Primary recommendation:** Model the implementation directly on the
conventions_resolver.py pattern: YAML load + path resolution + content read +
cache + digest string. Thread the digest through `build_l1_provider` as a new
`contract_spec` kwarg, injected between Blast Radius Context and Diff.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- D-01: Standalone `.code-forge/contracts.yaml` (not gate.yaml)
- D-02: `repo: $ENV_VAR` + `path:` relative resolution
- D-03: Reuse gate.yaml trust mechanism (`record_trust` / `is_trusted`)
- D-04: LLM summary for large specs, raw for small (configurable `max_raw_size`)
- D-05: New `## Contract Reference` prompt section before diff
- D-06: Inject to threads running L1 (primary-only today; amended 2026-06-21)
- D-07: Graceful empty + stderr warning on error
- D-08: Repos-grouped YAML schema with dataclass validation
- D-09: sha256 file hash cache in `.code-forge/cache/contracts/`
- D-10: Boundary tests (env missing, binary, symlink)
- D-11: Cache storage location -- repo-local
- D-12: Schema validation -- dataclass (not pydantic)
- D-13: Trust implementation -- shared with gate.yaml

### Claude's Discretion
- Default `max_raw_size` value
- Summary prompt wording
- Exact dataclass field names and validation error messages
- Cache directory structure inside `.code-forge/cache/contracts/`

### Deferred Ideas (OUT OF SCOPE)
- Per-spec summarizer backend config
- Automatic spec discovery
- Spec diff detection
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| CROSS-02 | When reviewing repo B, an opt-in recipe pulls repo A's contract/spec into reviewer context as read-only reference. Missing spec yields graceful empty digest. | All 13 decisions (D-01 through D-13) directly implement this. Prompt injection via `build_l1_provider` contract_spec param (factories.py:201). Trust via trust.py `is_trusted`. Cache via sha256 file hash (conventions_resolver.py pattern). Error handling via try/except -> empty string + stderr warning. |
</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| contracts.yaml loading + validation | Config layer (gate_check.py peer) | -- | YAML config loading follows gate_check.py pattern; dataclass validation is a config concern |
| Env var expansion + path resolution | Config layer | -- | `$ENV_VAR` expansion and relative path resolution are config-time operations |
| Trust enforcement | Trust layer (trust.py) | -- | D-03 reuses existing `is_trusted` -- no new trust code needed |
| Spec file reading + size gating | New contract_loader module | -- | Reads external files, applies max_raw_size gate, returns raw or triggers summarization |
| LLM summarization | LLM layer (llm_invoke.py) | -- | Reuses existing `llm_invoke` with a summarization prompt; no new LLM infrastructure |
| Summary caching | New contract_loader module | -- | sha256 file hash -> cached summary text in `.code-forge/cache/contracts/` |
| Prompt injection | Factories layer (factories.py) | cli.py (_make_subagent_spawn) | New `contract_spec` param in `build_l1_provider`, injected before diff in prompt |
| Cross-repo threading | Cross-repo layer (cross_repo.py) | -- | D-06 (amended): contract spec flows through `_thread_fn` to `build_l1_provider` for threads running L1 (primary-only today) |

## Standard Stack

### Core (no new dependencies)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pyyaml | 6.0.3 | contracts.yaml loading | Already a project dependency; `yaml.safe_load` used throughout |
| hashlib (stdlib) | -- | sha256 file content hashing | Used identically in conventions_resolver.py for cache keys |
| dataclasses (stdlib) | -- | ContractSpec / ContractRepo schema validation | D-12: dataclass, not pydantic; matches project convention |
| os (stdlib) | -- | `os.path.expandvars` for `$ENV_VAR` expansion | Standard mechanism for env var substitution in paths |
| pathlib (stdlib) | -- | Path resolution, file I/O | Used throughout the codebase |

[VERIFIED: codebase grep] All dependencies are already present in the project.

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| dataclass validation | pydantic | D-12 locks dataclass; pydantic adds a dependency for no benefit here |
| `os.path.expandvars` | manual `$VAR` regex | expandvars handles `$VAR`, `${VAR}`, and missing vars (returns literal `$VAR`); regex is error-prone |
| sha256 file hash cache | mtime-based cache | sha256 is content-addressed (survives touch/rsync); mtime is fragile across filesystems |

**Installation:** None required. All dependencies are already present.

## Architecture Patterns

### System Architecture Diagram

```
contracts.yaml (reviewed repo)
        |
        v
  load_contracts_config()  -- yaml.safe_load + dataclass validation
        |
        v
  trust check (is_trusted on contracts.yaml's parent .code-forge/)
        |
        +--[untrusted]--> empty digest + stderr warning
        |
        v  [trusted]
  for each spec entry:
        |
        v
  resolve repo path ($ENV_VAR expansion + Path.resolve)
        |
        +--[env missing / path missing]--> skip + stderr warning
        |
        v
  read spec file content
        |
        +--[unreadable / binary]--> skip + stderr warning
        |
        v
  size gate: len(content) vs max_raw_size
        |
        +--[small]--> raw content as digest
        |
        +--[large]--> check sha256 cache
                         |
                         +--[hit]--> cached summary
                         |
                         +--[miss]--> llm_invoke(summarize prompt)
                                         |
                                         v
                                     write cache file
                                         |
                                         v
                                     summary as digest
        |
        v
  assemble "## Contract: {name}\n{digest}" sections
        |
        v
  build_l1_provider(contract_spec=assembled_text)
        |
        v
  prompt: ... ## Contract Reference ... ## Diff ...
```

### Recommended Project Structure

```
src/code_forge/
    contract_loader.py     # NEW: config loading, spec reading, caching, digest assembly
    factories.py           # MODIFY: add contract_spec param to build_l1_provider
    cli.py                 # MODIFY: load contracts, pass to build_l1_provider
    cross_repo.py          # MODIFY: pass contract_spec through _thread_fn
tests/
    test_contract_loader.py  # NEW: unit + boundary tests
```

### Pattern 1: Config Loading (follows gate_check.py)

**What:** YAML loading with dataclass validation and CliError on schema violations.
**When to use:** Any `.code-forge/*.yaml` config file.

```python
# Source: gate_check.py load_gate_config pattern (line 39-134)
@dataclass
class ContractSpec:
    path: str
    max_raw_size: int = 16384  # default: 16KB

@dataclass
class ContractRepo:
    path: str          # supports $ENV_VAR
    specs: list[ContractSpec]

@dataclass
class ContractsConfig:
    repos: dict[str, ContractRepo]

def load_contracts_config(config_path: Path) -> ContractsConfig:
    """Load and validate contracts.yaml.

    Raises CliError on schema violations (not ValueError --
    contracts.yaml is a user-facing config file like gate.yaml).
    """
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    # validate structure, construct dataclasses
    # raise CliError on type errors or missing required fields
```

### Pattern 2: Env Var Expansion (D-02)

**What:** `$ENV_VAR` and `${ENV_VAR}` expansion in repo path fields.
**When to use:** Any config field that references an external path by env var.

```python
# Source: os.path.expandvars (stdlib)
import os

raw_path = "$KERNEL_REPO"
expanded = os.path.expandvars(raw_path)
# If KERNEL_REPO is set: expanded = "/home/user/code/linux-net-next"
# If KERNEL_REPO is NOT set: expanded = "$KERNEL_REPO" (literal)

# Detection of unresolved env vars:
if "$" in expanded:
    # env var not set -- graceful skip + warning
```

[VERIFIED: codebase grep] `os.path.expandvars` is not currently used in the
codebase, but it is the standard stdlib mechanism. The `$` prefix detection
after expansion catches unresolved variables cleanly.

### Pattern 3: Prompt Injection (follows conventions_digest)

**What:** Add a new string parameter to `build_l1_provider`, concatenate into prompt.
**When to use:** Any new read-only context for the reviewer.

```python
# Source: factories.py build_l1_provider (line 201-279)
# Current prompt assembly order:
#   1. Role instructions (line 252-262)
#   2. ## Post-Image (line 264-268)
#   3. ## Conventions Digest (line 269-273)
#   4. ## Blast Radius Context (line 274-278)
#   5. Diff (line 279)
#
# D-05 inserts ## Contract Reference BEFORE Diff:
#   1. Role instructions
#   2. ## Post-Image
#   3. ## Conventions Digest
#   4. ## Blast Radius Context
#   5. ## Contract Reference  <-- NEW (D-05)
#   6. Diff

if contract_spec:
    prompt += (
        "\n## Contract Reference\n"
        + contract_spec + "\n"
    )
prompt += "\nDiff:\n" + diff_text
```

### Pattern 4: File Content Caching (follows conventions_resolver.py)

**What:** sha256 of file content -> cached derived artifact.
**When to use:** When a derived artifact (summary) is expensive to compute and
the source file changes infrequently.

```python
# Source: conventions_resolver.py _write_cache / _read_cache pattern
import hashlib
import json

def _content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()

def _cache_path(cache_dir: Path, spec_hash: str) -> Path:
    return cache_dir / (spec_hash + ".json")

# Cache format: {"summary": str, "source_path": str, "hash": str}
```

### Anti-Patterns to Avoid

- **Reading arbitrary files from env-var paths without trust gate:** The trust
  gate (D-03) MUST be checked before reading any external file. A malicious
  contracts.yaml could point to `/etc/shadow` or a multi-GB file.
- **Raising exceptions on missing specs:** D-07 mandates graceful empty + stderr
  warning. Never let a missing spec crash the review pipeline.
- **Caching by mtime instead of content hash:** mtime is unreliable across
  filesystems and `git checkout` operations. Content hash is deterministic.
- **Using `yaml.safe_load` on the spec file itself:** The spec file is the
  contract document (e.g., YNL YAML spec), not a forge config. Read it as raw
  text, not parsed YAML. The spec content goes into the prompt as-is (raw) or
  as an LLM summary (large).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Env var expansion | Custom `$VAR` regex parser | `os.path.expandvars()` | Handles `$VAR`, `${VAR}`, edge cases; tested for decades |
| YAML loading | Custom parser | `yaml.safe_load()` | Already used throughout; `safe_load` prevents code execution |
| Content hashing | Custom hash function | `hashlib.sha256()` | Standard, collision-resistant, used in conventions_resolver.py |
| Trust enforcement | Custom trust mechanism | `trust.py is_trusted()` | D-03 locks this; already handles XDG, hash stability, CRUD |
| LLM invocation | Custom HTTP call | `llm_invoke()` | Handles cli/api dispatch, timeout, retry, JSON extraction |

## Common Pitfalls

### Pitfall 1: Env Var Not Set Produces Literal `$VAR` in Path

**What goes wrong:** `os.path.expandvars("$KERNEL_REPO")` returns the literal
string `"$KERNEL_REPO"` when the env var is unset. `Path("$KERNEL_REPO").resolve()`
then resolves to `cwd / "$KERNEL_REPO"` -- a directory that does not exist but
does not raise an error.
**Why it happens:** `expandvars` does not raise on missing vars; it returns the
literal.
**How to avoid:** After expansion, check if `"$"` is still in the string. If so,
the env var is not set -- emit a stderr warning and skip.
**Warning signs:** Tests pass locally (env var set) but fail in CI (env var not
set). The graceful-skip path is not exercised.

### Pitfall 2: Binary Files Injected as Raw Text

**What goes wrong:** A spec path points to a binary file (compiled protobuf,
`.pyc`, image). Reading it as UTF-8 produces garbage in the prompt, wasting
context tokens.
**Why it happens:** No binary detection before raw injection.
**How to avoid:** Check for null bytes in the first 1KB of content (same pattern
as `_assemble_post_image` in cli.py line 606-608). If binary, skip with warning.
**Warning signs:** LLM returns gibberish findings or schema validation fails.

### Pitfall 3: Summarization Failure Treated as Hard Error

**What goes wrong:** LLM summarization fails (timeout, rate limit, API error).
If the error propagates, the entire review crashes.
**Why it happens:** `llm_invoke` raises `LLMInvokeError` on failure.
**How to avoid:** Wrap `llm_invoke` in try/except. On failure, return empty
digest + stderr warning. D-07 mandates this: "never an error, never a crash."
**Warning signs:** Review fails with `LLMInvokeError` when the backend is slow.

### Pitfall 4: Cache Collision Across Repos

**What goes wrong:** Two different specs with the same sha256 content hash
(theoretically possible but practically irrelevant) overwrite each other's cache.
**Why it happens:** Cache key is content hash only.
**How to avoid:** Include the source path (or a hash of it) in the cache
filename: `{path_hash}_{content_hash}.json` (same pattern as
conventions_resolver.py line 726-731).
**Warning signs:** Cached summary for spec A appears when reviewing with spec B.

### Pitfall 5: Symlink Traversal to Sensitive Files

**What goes wrong:** A contracts.yaml spec path uses symlinks to escape the repo
root, reading files outside the project boundary.
**Why it happens:** `Path.resolve()` follows symlinks transparently.
**How to avoid:** D-10 specifies `Path.resolve()` which follows symlinks. The
trust gate (D-03) is the primary defense: the user explicitly trusts the
contracts.yaml. Additionally, use the symlink guard from
`conventions_resolver._symlink_guard_passes()` on the resolved spec path as a
defense-in-depth measure.
**Warning signs:** Spec content contains unexpected data from outside the repo.

### Pitfall 6: Prompt Section Order Incorrect in Subagent Path

**What goes wrong:** The `## Contract Reference` section is injected in
`build_l1_provider` (Outlet A) but NOT in `_make_subagent_spawn` (Outlet C).
**Why it happens:** cli.py `_make_subagent_spawn` (line 532-584) builds its own
prompt independently of `build_l1_provider`. It already handles `post_image` and
`conv_digest` but would miss `contract_spec` unless explicitly added.
**How to avoid:** Thread `contract_spec` through both `build_l1_provider` AND
`_make_subagent_spawn`. The same digest string is passed to both.
**Warning signs:** Contract reference appears in Outlet A reviews but not Outlet
C reviews.

## Code Examples

### contracts.yaml Schema (D-08)

```yaml
# .code-forge/contracts.yaml
repos:
  kernel:
    path: $KERNEL_REPO
    specs:
      - path: net/ovs_flow.yaml
        max_raw_size: 16384
      - path: net/tc_flower.yaml
  pnfs:
    path: $PNFS_REPO
    specs:
      - path: docs/pnfs.rst
```

### Dataclass Validation (D-12)

```python
# Source: project convention (frozen dataclasses throughout codebase)
from dataclasses import dataclass, field
from pathlib import Path

@dataclass(frozen=True)
class ContractSpec:
    """A single spec file within a contract repo."""
    path: str                    # relative to repo root
    max_raw_size: int = 16384    # bytes; specs larger than this are summarized

@dataclass(frozen=True)
class ContractRepo:
    """A repository containing one or more contract specs."""
    path: str                    # supports $ENV_VAR expansion
    specs: list[ContractSpec]    # at least one spec required

@dataclass(frozen=True)
class ContractsConfig:
    """Top-level contracts.yaml schema."""
    repos: dict[str, ContractRepo]
```

### Graceful Error Handling (D-07)

```python
# Source: forge convention (detect.py detect_and_init fallback pattern)
import sys

def _warn(msg: str) -> None:
    sys.stderr.write("code-forge: contract: %s\n" % msg)

def load_contract_spec(config_path: Path) -> str:
    """Load and assemble contract spec digest.

    Returns empty string on any error (D-07: never crash).
    """
    try:
        config = _load_contracts_config(config_path)
    except FileNotFoundError:
        return ""  # no contracts.yaml = no spec (SC-3)
    except Exception as exc:
        _warn("contracts.yaml error: %s" % exc)
        return ""
    # ... resolve, read, summarize ...
```

### Cache Read/Write

```python
# Source: conventions_resolver.py _read_cache / _write_cache pattern
import hashlib
import json

def _spec_cache_path(cache_dir: Path, repo_name: str, spec_path: str, content_hash: str) -> Path:
    """Build cache file path: {repo}_{spec_hash}_{content_hash}.json"""
    spec_hash = hashlib.sha256(spec_path.encode()).hexdigest()[:8]
    return cache_dir / ("%s_%s_%s.json" % (repo_name, spec_hash, content_hash))

def _read_spec_cache(cache_path: Path) -> str | None:
    if not cache_path.is_file():
        return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        return data.get("summary")
    except Exception:
        return None

def _write_spec_cache(cache_path: Path, summary: str, source_path: str, content_hash: str) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"summary": summary, "source": source_path, "hash": content_hash}
        cache_path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
    except OSError:
        pass  # cache write failure is non-fatal
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| No external spec injection | conventions_digest (same-repo naming) | Phase 25 (conventions_resolver.py) | Established the pattern for injecting external context into L1 prompt |
| Trust per gate.yaml field | `is_trusted` on entire `.code-forge/` dir hash | Phase 17 (trust.py) | D-13 (amended): independent hashes, unified CLI; contracts hash covers resolved spec file contents |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Default `max_raw_size` of 16384 bytes (16KB) is practical for typical YNL specs | Code Examples | If typical specs are larger, most will trigger summarization unnecessarily; planner should verify against real kernel YNL spec sizes |
| A2 | `os.path.expandvars` handles all env var formats the user needs (`$VAR`, `${VAR}`) | Architecture Patterns | If Windows `%VAR%` format is needed, expandvars handles it on Windows but not on Unix; Unix-only is acceptable per project constraints |
| A3 | LLM summarization prompt wording ("Summarize this spec for a code reviewer") produces useful summaries | Claude's Discretion | Poor prompt wording could produce summaries that omit critical field names or type constraints; planner should test with a real YNL spec |
| A4 | The trust gate covers contracts.yaml via the existing `.code-forge/` directory trust | Architecture Patterns | If `is_trusted` checks gate.yaml specifically (not the directory), contracts.yaml may need its own trust hash; verified below that `is_trusted` takes gate_yaml_path and gates on backends hash -- contracts.yaml needs its own trust path or a shared directory-level check |

**A4 resolution (from source):** `trust.py:is_trusted` (line 99-107) takes
`gate_yaml_path` and hashes the `backends` block specifically. It does NOT
provide directory-level trust. D-13 (amended) uses independent hashes; the current
mechanism is backends-block-specific. Two implementation options:
  1. **Extend `is_trusted` to accept contracts.yaml as a separate entry** -- hash
     the contracts content, store alongside the gate.yaml entry in trusted.json.
     This is the cleanest approach: `code-forge trust` records both hashes.
  2. **Gate contracts.yaml on gate.yaml trust** -- if gate.yaml is trusted, allow
     contracts.yaml too. Simpler but weaker: a user could trust gate.yaml then
     have contracts.yaml modified maliciously without re-trusting.

Recommendation for planner: Option 1 (extend `is_trusted` with a
`contracts_hash` field) is safer and aligns with D-13's intent. The planner
should lock this.

## Open Questions (RESOLVED)

1. **Trust mechanism granularity** (RESOLVED: Plan 01 implements separate contracts_hash field in trusted.json via hash_contracts_content / is_trusted_contracts / record_trust_contracts -- option 1 from A4 resolution.)
   - What we know: `is_trusted` currently hashes only the `backends` block of
     gate.yaml. D-13 says "shared trust."
   - What's unclear: Should `code-forge trust` hash contracts.yaml separately,
     or should gate.yaml trust imply contracts.yaml trust?
   - Recommendation: Hash contracts.yaml content as a separate field in
     trusted.json (option 1 from A4 resolution). Planner should lock this.

2. **Real YNL spec sizes** (RESOLVED: Planner checked real specs -- ovs_flow.yaml 23KB, tc.yaml 80KB, ethtool.yaml 58KB. Default max_raw_size set to 32KB to keep the primary use case (ovs_flow) raw while summarizing the larger specs.)
   - What we know: D-04 says configurable `max_raw_size` per spec.
   - What's unclear: How large are real kernel YNL specs? If most are under 16KB,
     summarization is rare and cache is rarely used. If most are 50KB+, cache
     and summarization are critical path.
   - Recommendation: Planner should check a few real specs
     (`linux-net-next/Documentation/netlink/specs/`) and set default accordingly.

3. **Prompt token budget** (RESOLVED: 32KB raw spec text is roughly 8K tokens -- acceptable for all supported backends with 128K+ context windows. Summarization reduces large specs further. The max_raw_size gate caps the worst case.)
   - What we know: Contract specs add to the prompt before the diff. Context
     window limits apply.
   - What's unclear: With a 16KB raw spec + a large diff + post-image +
     conventions + blast radius, could the prompt exceed backend context limits?
   - Recommendation: The `max_raw_size` default should account for the prompt
     token budget. 16KB of raw spec text is roughly 4K tokens -- acceptable for
     most backends (128K+ context). Summarization further reduces this.

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | -- |
| V3 Session Management | no | -- |
| V4 Access Control | yes | Trust gate (D-03/D-13): contracts.yaml must be trusted before use |
| V5 Input Validation | yes | Dataclass validation (D-12) rejects malformed YAML; env var expansion checked for unresolved `$` |
| V6 Cryptography | no | sha256 is used for caching, not security |

### Known Threat Patterns for This Phase

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Malicious contracts.yaml points to sensitive file | Information Disclosure | Trust gate (D-03): user must explicitly trust `.code-forge/` |
| Context blowup via giant spec file | Denial of Service | `max_raw_size` gate (D-04): specs over limit are summarized, not injected raw |
| Env var exfiltration via crafted `$VAR` path | Information Disclosure | `os.path.expandvars` only reads env vars, does not execute; trust gate is primary defense |
| Symlink traversal to `/etc/shadow` | Information Disclosure | `Path.resolve()` follows symlinks (D-10); trust gate is primary defense; optional symlink guard as defense-in-depth |
| Cache poisoning with malicious summary | Tampering | Cache is repo-local (D-11) and keyed on content hash; attacker would need write access to `.code-forge/cache/` which implies repo access |

## Sources

### Primary (HIGH confidence)
- `src/code_forge/factories.py` lines 201-279 -- build_l1_provider signature and prompt assembly order
- `src/code_forge/trust.py` lines 86-107 -- `hash_backends_block` and `is_trusted` implementation
- `src/code_forge/conventions_resolver.py` lines 84-131, 672-808 -- resolve_sources + caching pattern
- `src/code_forge/cli.py` lines 532-584, 587-617, 1497-1555 -- subagent spawn, post_image assembly, build_l1_provider call site
- `src/code_forge/cross_repo.py` lines 169-411 -- run_cross_repo and _thread_fn
- `src/code_forge/gate_check.py` lines 39-134 -- load_gate_config YAML validation pattern
- `src/code_forge/errors.py` -- CliError definition
- `src/code_forge/llm_invoke.py` lines 229-268 -- llm_invoke signature and dispatch

### Secondary (MEDIUM confidence)
- `26-CONTEXT.md` -- all 13 decisions and canonical references
- `.planning/REQUIREMENTS.md` -- CROSS-02 requirement definition
- `.planning/ROADMAP.md` -- SC-1/SC-2/SC-3 success criteria

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - no new dependencies; all building blocks verified in codebase
- Architecture: HIGH - pattern directly mirrors conventions_resolver.py + build_l1_provider
- Pitfalls: HIGH - all 6 pitfalls derived from reading actual source code paths
- Trust mechanism: MEDIUM - A4 reveals a gap between D-13's "shared trust" intent and the current backends-only hash; planner must lock the approach

**Research date:** 2026-06-21
**Valid until:** 2026-07-21 (stable; no external dependency churn)
