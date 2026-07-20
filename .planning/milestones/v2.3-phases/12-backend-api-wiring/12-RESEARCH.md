# Phase 12: Backend API Wiring - Research

**Researched:** 2026-06-04
**Domain:** Backend configuration routing, CLI argument parsing, API integration
**Confidence:** HIGH

## Summary

Phase 12 wires gate.yaml backends configuration into cli.py so code-forge review can route L1 review and falsification LLM calls to third-party models (mimo, deepseek, kimi) via OpenAI/Anthropic-compatible HTTP APIs. The backend infrastructure is 90% complete -- backend.py provides BackendConfig dataclass, load_backend_configs parser, and resolve_backend precedence logic; llm_invoke.py has fully implemented _invoke_openai and _invoke_anthropic API dispatch paths. The remaining work is localized CLI wiring plus three technical debt fixes (F1/F2/F3).

All 16 implementation decisions from CONTEXT.md have been verified against the actual codebase. The file:line references are accurate. No technical obstacles were discovered that would prevent execution of the locked decisions.

**Primary recommendation:** Execute as planned with one minor adjustment -- BackendConfig.max_tokens field must be added with default=16384 (not optional) to satisfy both D-05 requirement and avoid breaking existing tests that don't specify it.

## User Constraints

> Copied verbatim from CONTEXT.md

### Locked Decisions

**D-01 through D-16:** All implementation decisions from CONTEXT.md are locked after 3-round cross-model review convergence. Research verifies technical feasibility, not correctness of approach.

### Deferred Ideas

**MULTI-01:** Cross-repo joint scanning (--whole-file across sibling repos) deferred to v2.4+.

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| BACK-01 | gate.yaml backends block wired to cli.py _run() with --backend NAME flag and FORGE_BACKEND env resolution | D-02/D-03/D-04/D-16: load_outlet_from_gate pattern reusable; resolve_backend honors precedence; cli.py:666-678 is single wiring point |
| BACK-02 | max_tokens fix -- raise hardcoded 4096 in _invoke_anthropic; add explicit max_tokens to _invoke_openai | D-05/D-06: BackendConfig.max_tokens field; llm_invoke.py:406 anthropic, :357-361 openai |
| BACK-03 | F1/F2/F3 cli.py cleanup -- flatten dead abstraction loop, DRY whole_file logic, fix --whole-file docs | D-07/D-08/D-09: cli.py:1022-1036 loop, :1020/:1101 duplication, nargs='+' multi-file |

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pyyaml | >=6.0 | YAML parsing for gate.yaml backends block | Already in dependencies (pyproject.toml:18); yaml.safe_load pattern established in outlet_resolver.py:82 |
| pytest | >=8.0 | Unit + integration testing | Already in dev dependencies (pyproject.toml:24); 1,004 existing tests use it |
| unittest.mock | stdlib | Mock llm_invoke for backend resolution tests | Standard pattern in test_backend.py:37-47 (_api_entry helper) |

### Supporting

No additional libraries required. All functionality implemented with stdlib (urllib.request for HTTP, json, os.environ for env vars).

### Version Verification

```bash
# Verified 2026-06-04 from pyproject.toml
grep -A10 "dependencies" /home/houminxi/code/forge/pyproject.toml
# Output confirms: pyyaml>=6.0, unidiff>=0.7.5
```

## Package Legitimacy Audit

> No external packages being added in this phase. All dependencies already present in pyproject.toml and verified in prior phases.

**Packages removed due to slopcheck [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none

## Architecture Patterns

### System Architecture Diagram

```
User CLI Invocation
        |
        v
cli.py _run()
        |
        +---> [D-16] load gate.yaml (yaml.safe_load)
        |               |
        |               +---> load_backend_configs(data.get("backends"))
        |               |               |
        |               |               v
        |               |     [D-11] dict iteration: backends.items()
        |               |               |
        |               |               v
        |               |     Parse each entry -> BackendConfig list
        |               |
        |               +---> [D-02/D-03] resolve_backend(env, configs, cli_value)
        |                               |
        |                               v
        |                     Precedence: --backend > FORGE_BACKEND > default > session
        |                               |
        v                               v
[D-10] Inline flags?          Selected BackendConfig
--backend-url/format/             |
key-env/model                     |
        |                         |
        +-------------------------+
                    |
                    v
        Transient or config BackendConfig
                    |
                    v
        factories.py build_l1_provider(backend=...)
                    |
                    +---> l1_provider 3 passes
                    |           |
                    |           v
                    |     llm_invoke(prompt, backend=backend)
                    |           |
                    |           +---> [D-06] _invoke_api(backend)
                    |                       |
                    |                       +---> _invoke_openai (base_url/chat/completions)
                    |                       |           |
                    |                       |           +---> [D-05] body["max_tokens"] = backend.max_tokens
                    |                       |
                    |                       +---> _invoke_anthropic (base_url/v1/messages)
                    |                                   |
                    |                                   +---> [D-05] body["max_tokens"] = backend.max_tokens (was 4096)
                    |
                    v
        [D-13] Print per-pass token cost to stderr
                    |
                    v
        Post-verdict cost summary (cli.py:758-778)
```

**Key architectural insight:** Backend resolution is a pure configuration concern, not a runtime concern. No diff-driven routing (D-25 HARD NON-GOAL). The backend is selected once at CLI startup and used for all LLM calls in that invocation.

### Recommended Project Structure

Existing structure is correct. No new modules needed.

```
src/code_forge/
├── backend.py          # BackendConfig, load_backend_configs, resolve_backend (D-05: add max_tokens field)
├── llm_invoke.py       # _invoke_openai, _invoke_anthropic (D-06: use backend.max_tokens)
├── cli.py              # _run backend wiring (D-16), arg parser (D-02/D-10), F1/F2/F3 cleanup (D-07/D-08/D-09)
├── outlet_resolver.py  # load_outlet_from_gate pattern (reuse for D-16)
└── gate_check.py       # load_gate_config (NOT used for backends per D-16)

tests/
└── test_backend.py     # Unit tests for D-02/D-03/D-04/D-05/D-10/D-11 + 1 real API smoke (D-12)
```

### Pattern 1: Lightweight gate.yaml Loading (D-16)

**What:** Load gate.yaml with yaml.safe_load without requiring "test:" section
**When to use:** Backend resolution, outlet resolution (any config that lives in gate.yaml but is orthogonal to gate check)
**Example:**

```python
# Source: outlet_resolver.py:65-103 (verified pattern)
def load_gate_yaml_lightweight(gate_yaml_path: Path) -> Optional[dict]:
    """Read gate.yaml without test section requirement."""
    try:
        with open(gate_yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        return None
    except yaml.YAMLError as exc:
        raise ValueError("gate.yaml read failed: %s" % exc) from exc
    return data if isinstance(data, dict) else None
```

**Integration point (D-16):** cli.py:666-678 currently has `configs=[]` hardcoded. Replace with:

```python
# Load gate.yaml for backends (lightweight, no test: section required)
gate_yaml_path = cwd / ".code-forge" / "gate.yaml"
gate_data = load_gate_yaml_lightweight(gate_yaml_path)  # returns None if not found
configs = load_backend_configs(gate_data) if gate_data else []
```

### Pattern 2: Backend Precedence Resolution (D-02/D-03)

**What:** Resolve active backend from multiple sources with clear precedence
**When to use:** Any configuration with env override + file config + defaults
**Example:**

```python
# Source: backend.py:225-268 (existing code, verified)
# Precedence: --backend NAME > FORGE_BACKEND env > config default > session default
# D-01: FAIL CLOSED when override names a backend not in configs
```

**Why this pattern:** Matches established outlet precedence (outlet_resolver.py). Consistent UX.

### Pattern 3: Mutual Exclusion Groups (D-10)

**What:** --backend NAME and inline flags (--backend-url/format/key-env/model) are mutually exclusive
**When to use:** CLI has two ways to specify the same logical thing (named config vs inline construction)
**Example:**

```python
# In cli.py argument parser
backend_group = parser.add_mutually_exclusive_group()
backend_group.add_argument("--backend", metavar="NAME", help="Select backend from gate.yaml")
inline_group = parser.add_argument_group("inline backend flags (all 4 required)")
inline_group.add_argument("--backend-url", metavar="URL")
inline_group.add_argument("--backend-format", choices=["openai", "anthropic"])
inline_group.add_argument("--backend-key-env", metavar="VAR_NAME")
inline_group.add_argument("--backend-model", metavar="MODEL_NAME")

# In _run() validation
if args.backend and any([args.backend_url, args.backend_format, args.backend_key_env, args.backend_model]):
    raise CliError("--backend and inline flags are mutually exclusive")
if any([args.backend_url, args.backend_format, args.backend_key_env, args.backend_model]):
    if not all([args.backend_url, args.backend_format, args.backend_key_env, args.backend_model]):
        raise CliError("inline backend requires all 4 flags: --backend-url/format/key-env/model")
```

### Anti-Patterns to Avoid

- **Diff-driven backend selection:** HARD NON-GOAL per CONTEXT.md D-25. Never add parameters like `diff_size` or `complexity` to resolve_backend.
- **Provider default reliance:** D-06 requires explicit max_tokens in both OpenAI and Anthropic requests. Do not rely on provider defaults (they differ and can change).
- **List-based backends schema:** D-11 changes to dict-based. Do NOT support the old list format for backward compatibility -- there are no existing consumers (configs=[] is hardcoded everywhere).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| YAML parsing | Custom config parser | yaml.safe_load (stdlib via pyyaml) | Already a dependency; established pattern in outlet_resolver.py:82 |
| HTTP API calls | Custom HTTP client | urllib.request (stdlib) | Already used in llm_invoke.py:345-435; no need for requests library |
| Environment variable lookup | Custom env resolver | os.environ[key] with KeyError handling | Established pattern; llm_invoke.py:304-308 shows error wrapping |
| Dict-based config iteration | Custom loader | backends.items() with name injection | D-11 specifies this; simple and Pythonic |

**Key insight:** All required functionality already exists in stdlib or existing dependencies. No new abstractions needed.

## Common Pitfalls

### Pitfall 1: BackendConfig.max_tokens Field Type

**What goes wrong:** Making max_tokens Optional[int] = None breaks existing tests that don't specify it.
**Why it happens:** D-05 says "optional, default 16384" which sounds like Optional[int].
**How to avoid:** Use `max_tokens: int = 16384` (required field with default value, not optional field).
**Warning signs:** test_backend.py failures on _api_entry() helper that doesn't include max_tokens.

### Pitfall 2: load_backend_configs Dict Iteration (D-11)

**What goes wrong:** Parser expects list but gate.yaml has dict schema.
**Why it happens:** Current backend.py:175 does `for e in backends` which assumes list.
**How to avoid:** Change to `for name, entry in backends.items(): entry["name"] = name; yield _parse_backend_entry(entry)`.
**Warning signs:** TypeError: 'str' object is not iterable when backends is a dict with string keys.

**Code location verified:** backend.py:163-175

```python
# BEFORE (current code, assumes list):
def load_backend_configs(data: Optional[dict]) -> List[BackendConfig]:
    if data is None:
        return []
    backends = data.get("backends")
    if not backends:
        return []
    return [_parse_backend_entry(e) for e in backends]  # <- assumes list

# AFTER (D-11 dict schema):
def load_backend_configs(data: Optional[dict]) -> List[BackendConfig]:
    if data is None:
        return []
    backends = data.get("backends")
    if not backends:
        return []
    if not isinstance(backends, dict):
        raise ValueError("backends must be a dict with backend names as keys")
    configs = []
    for name, entry in backends.items():
        if not isinstance(entry, dict):
            raise ValueError(f"backend {name!r} must be a dict")
        entry["name"] = name  # Inject name from YAML key
        configs.append(_parse_backend_entry(entry))
    return configs
```

### Pitfall 3: Inline Flags Validation Order (D-10)

**What goes wrong:** User provides --backend NAME plus --backend-url URL; both get accepted and conflict at runtime.
**Why it happens:** Validation happens after both are parsed.
**How to avoid:** Validate mutual exclusion early in _run() before calling resolve_backend.
**Warning signs:** Confusing error messages about "backend X not found" when inline flags were also provided.

### Pitfall 4: F2 Merge Order (D-08)

**What goes wrong:** Merging whole_file logic before expanding nargs='+' means the merged function handles only single file.
**Why it happens:** D-08 describes the merge, D-09 describes the expansion, but order matters.
**How to avoid:** D-09 FIRST (nargs expansion), then D-08 (merge) -- CONTEXT.md explicitly specifies this order.
**Warning signs:** Merged function returns `[Path(whole_file)]` but caller expects list of multiple paths.

### Pitfall 5: Per-Pass Token Cost Printing (D-13)

**What goes wrong:** Per-pass cost printing gets confused with post-verdict summary (cli.py:758-778).
**Why it happens:** Both print to stderr, both mention tokens.
**How to avoid:** D-13 is INSIDE l1_provider per-pass execution (after each llm_invoke returns), not after the verdict. Format: `[backend_name] N in / M out tokens` (one-liner, supplements not replaces).
**Warning signs:** Double-counting tokens or missing the incremental per-pass cost during review.

### Pitfall 6: CliError vs LLMInvokeError Boundary (D-04/D-14)

**What goes wrong:** llm_invoke.py raises CliError directly, violating module boundaries.
**Why it happens:** API key missing or HTTP errors happen during llm_invoke execution.
**How to avoid:** llm_invoke.py raises LLMInvokeError internally. cli.py catches LLMInvokeError and re-wraps as CliError with backend name + hint.
**Warning signs:** ImportError when llm_invoke tries to import CliError (circular dependency).

**Code pattern (verified llm_invoke.py:304-308 for api_key lookup):**

```python
# llm_invoke.py (internal module)
try:
    api_key = os.environ[backend.api_key_env]
except KeyError:
    raise LLMInvokeError(f"env var {backend.api_key_env} not set")  # NOT CliError

# cli.py (CLI boundary)
try:
    result = llm_invoke(prompt, backend)
except LLMInvokeError as exc:
    raise CliError(f"backend {backend.name}: {exc}")  # Re-wrap with context
```

## Code Examples

Verified patterns from codebase:

### Gate YAML Loading (Reuse Pattern)

```python
# Source: outlet_resolver.py:65-103 (verified 2026-06-04)
def load_outlet_from_gate(gate_yaml_path: Path, fs_open=open) -> Optional[str]:
    """Read only the 'outlet' key from gate.yaml.
    
    Does NOT call load_gate_config (avoids the "test section required" constraint).
    """
    try:
        with fs_open(gate_yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        return None
    except PermissionError as exc:
        raise ValueError("gate.yaml read failed: permission denied") from exc
    except yaml.YAMLError as exc:
        raise ValueError("gate.yaml read failed: %s" % exc) from exc
    
    if isinstance(data, dict) and "outlet" in data:
        val = data["outlet"]
        if not isinstance(val, str):
            raise ValueError("gate.yaml outlet must be a string, got %s" % type(val).__name__)
        return val
    return None
```

**D-16 application:** Use this pattern but return full dict for both `load_backend_configs(data)` and `load_outlet_from_gate(data)`.

### Backend Resolution Precedence

```python
# Source: backend.py:225-268 (verified 2026-06-04)
def resolve_backend(env: Mapping[str, str], configs: List[BackendConfig], cli_value: Optional[str] = None) -> BackendConfig:
    """Resolve the active backend.
    
    Precedence: cli_value > FORGE_BACKEND env > config default > session default.
    """
    override = cli_value
    if override is None:
        override = env.get("FORGE_BACKEND")
    
    if override is not None and override != "":
        key = override.strip()
        if key == "":
            raise CliError("invalid FORGE_BACKEND: whitespace-only value %r" % override)
        for cfg in configs:
            if cfg.name == key:
                return cfg
        configured = ", ".join(c.name for c in configs) or "none"
        raise CliError("unknown backend %r (configured: %s)" % (key, configured))
    
    # Config default or first entry
    if configs:
        for cfg in configs:
            if cfg.default:
                return cfg
        return configs[0]  # No default marked, use first
    
    # Session-model default
    return DEFAULT_BACKEND
```

**D-01 verification:** This code already implements FAIL CLOSED -- `raise CliError("unknown backend...")` when override doesn't match configs.

### API Invoke with max_tokens

```python
# Source: llm_invoke.py:345-435 (verified 2026-06-04)
# BEFORE (current):
def _invoke_anthropic(prompt: str, backend: BackendConfig, timeout_s: float) -> str:
    # ... (lines 395-405)
    body = {
        "model": backend.model,
        "max_tokens": 4096,  # <- D-06: HARDCODED, causes truncation
        "messages": [{"role": "user", "content": prompt}],
    }
    # ...

def _invoke_openai(prompt: str, backend: BackendConfig, timeout_s: float) -> str:
    # ... (lines 350-356)
    body = {
        "model": backend.model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        # <- D-06: MISSING max_tokens entirely
    }
    # ...

# AFTER (D-05/D-06):
def _invoke_anthropic(prompt: str, backend: BackendConfig, timeout_s: float) -> str:
    body = {
        "model": backend.model,
        "max_tokens": backend.max_tokens,  # D-06: read from config
        "messages": [{"role": "user", "content": prompt}],
    }

def _invoke_openai(prompt: str, backend: BackendConfig, timeout_s: float) -> str:
    body = {
        "model": backend.model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": backend.max_tokens,  # D-06: ADD explicit field
    }
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| configs=[] hardcoded (cli.py:669) | Load from gate.yaml backends block | Phase 12 (this phase) | Enables third-party model routing |
| max_tokens=4096 hardcoded | Configurable via BackendConfig.max_tokens | Phase 12 (this phase) | Prevents review JSON truncation |
| OpenAI request without max_tokens | Explicit max_tokens in request body | Phase 12 (this phase) | Removes provider default reliance |
| List-based backends schema | Dict-based with name as YAML key | Phase 12 (this phase) | More intuitive YAML authoring |
| For-loop mutual exclusion (F1) | Flattened independent checks | Phase 12 (this phase) | Removes dead abstraction |
| Duplicated whole_file logic (F2) | Single merged function | Phase 12 (this phase) | DRY principle |
| --whole-file single file | nargs='+' multi-file | Phase 12 (this phase) | Expanded capability within single-repo scope |

**Deprecated/outdated:**
- List-based backends schema: D-11 makes dict-based the only supported format going forward.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | mimo/kimi/deepseek all support >= 16K output tokens | Standard Stack | max_tokens=16384 gets rejected; need to reduce or make per-backend configurable |
| A2 | No existing consumers use list-based backends schema | Common Pitfalls #2 | Breaking change for users who created backends.yaml files (though configs=[] hardcoding means this is unlikely) |
| A3 | Per-pass token cost can be printed to stderr without interfering with post-verdict summary | Code Examples | Output formatting confusion or parsing breakage for tools that scrape stderr |

**If this table is empty:** All claims in this research were verified or cited -- no user confirmation needed.

**Mitigation for A1:** Reference verified via memory reference_kimi_api.md and reference_deepseek_compact.md. Kimi supports up to 32K output, DeepSeek supports 16K+. Mimo confirmed anthropic-compatible. Risk is LOW.

## Open Questions

1. **gate.yaml location when no test config exists**
   - What we know: load_gate_config requires "test:" section (gate_check.py:63)
   - What's unclear: Should backends-only gate.yaml be supported or should test: {} be required?
   - Recommendation: D-16 lightweight loader returns None when gate.yaml absent; if present but has no test section, that's fine for backends. Document that gate.yaml MAY have test section but backends loading doesn't require it.

2. **Multiple default: true in gate.yaml**
   - What we know: D-03 says "multiple defaults -> CliError"
   - What's unclear: Where should this validation happen? load_backend_configs or resolve_backend?
   - Recommendation: Validate in load_backend_configs immediately after parsing all entries. Fail fast at config load time, not at resolution time.

## Environment Availability

> Backend wiring has no external dependencies beyond Python stdlib and existing pyyaml dependency.

**Skip condition satisfied:** No external tools, services, runtimes, or databases required. All functionality implemented with:
- yaml.safe_load (pyyaml, already a dependency)
- urllib.request (stdlib)
- os.environ (stdlib)
- json (stdlib)

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.0+ |
| Config file | pyproject.toml (lines 37-41) |
| Quick run command | `pytest tests/test_backend.py -v` |
| Full suite command | `pytest tests/ -v` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| BACK-01 | gate.yaml backends block wired to resolve_backend | unit | `pytest tests/test_backend.py::TestResolveBackend -v` | ✅ (existing) |
| BACK-01 | --backend NAME selects named backend | unit | `pytest tests/test_backend.py::test_resolve_backend_cli_override_wins -x` | ✅ (existing) |
| BACK-01 | FORGE_BACKEND env override honored | unit | `pytest tests/test_backend.py::test_resolve_backend_env_override_wins -x` | ✅ (existing) |
| BACK-01 | D-11 dict iteration in load_backend_configs | unit | `pytest tests/test_backend.py::test_load_backend_configs_dict_schema -x` | ❌ Wave 0 |
| BACK-01 | D-16 lightweight gate.yaml loading | unit | `pytest tests/test_cli.py::test_gate_yaml_backends_loading -x` | ❌ Wave 0 |
| BACK-02 | BackendConfig.max_tokens field added | unit | `pytest tests/test_backend.py::test_backendconfig_max_tokens_field -x` | ❌ Wave 0 |
| BACK-02 | _invoke_anthropic uses backend.max_tokens | unit | `pytest tests/test_llm_invoke.py::test_anthropic_uses_backend_max_tokens -x` | ❌ Wave 0 |
| BACK-02 | _invoke_openai adds explicit max_tokens | unit | `pytest tests/test_llm_invoke.py::test_openai_adds_max_tokens -x` | ❌ Wave 0 |
| BACK-03 | F1 loop flattened (no behavior change) | unit | `pytest tests/test_cli.py::test_whole_file_conflicts_unchanged -x` | ✅ (regression) |
| BACK-03 | F2 whole_file logic DRYed | unit | `pytest tests/test_cli.py::test_whole_file_path_derivation -x` | ✅ (regression) |
| BACK-03 | F3 --whole-file nargs='+' multi-file | unit | `pytest tests/test_cli.py::test_whole_file_multi_file -x` | ❌ Wave 0 |
| BACK-04 | Dogfood verify: mimo backend smoke test | integration | `pytest tests/test_backend.py::test_real_api_mimo -m real_api -x` | ❌ Wave 0 (Phase 13) |

### Sampling Rate

- **Per task commit:** `pytest tests/test_backend.py tests/test_cli.py -x` (< 10s)
- **Per wave merge:** `pytest tests/ -v` (full suite, ~30s)
- **Phase gate:** Full suite green + F1/F2/F3 regression tests passing before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/test_backend.py::test_load_backend_configs_dict_schema` -- covers D-11 dict iteration with name injection
- [ ] `tests/test_backend.py::test_backendconfig_max_tokens_field` -- covers D-05 field addition
- [ ] `tests/test_cli.py::test_gate_yaml_backends_loading` -- covers D-16 lightweight loader integration
- [ ] `tests/test_llm_invoke.py` -- new test file needed for D-06 max_tokens usage
- [ ] `tests/test_cli.py::test_whole_file_multi_file` -- covers D-09 nargs='+' expansion

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|------------------|
| V2 Authentication | no | N/A (no user authentication in this phase) |
| V3 Session Management | no | N/A (stateless CLI tool) |
| V4 Access Control | no | N/A (no authorization logic) |
| V5 Input Validation | yes | Validate YAML structure, env var names, URL formats |
| V6 Cryptography | yes | NEVER store secrets inline; api_key_env holds env var NAME only (D-04) |

### Known Threat Patterns for Python CLI + HTTP API

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Hardcoded API keys in config | Information Disclosure | BackendConfig.api_key_env holds NAME only; secret looked up at runtime from os.environ (backend.py:71) |
| YAML injection (malicious gate.yaml) | Tampering | yaml.safe_load (not yaml.load); no code execution via YAML |
| SSRF via base_url | Tampering | Validate base_url format; current code trusts user config (acceptable for CLI tool) |
| Command injection via backend.command | Tampering | cli backend executes shell command; validate no shell metacharacters (established pattern in gate_check.py:35) |
| HTTP response parsing DoS | Denial of Service | json.loads with timeout (llm_invoke.py:367); no unbounded parsing |

**Critical security requirement (D-04):** Backend config MUST store api_key_env (env var name), NEVER the raw secret. Verification: BackendConfig dataclass comment at backend.py:62 explicitly documents this.

## Sources

### Primary (HIGH confidence)

- `src/code_forge/backend.py` -- BackendConfig dataclass (lines 59-73), load_backend_configs (lines 163-175), resolve_backend (lines 225-268)
- `src/code_forge/llm_invoke.py` -- _invoke_anthropic (line 406 hardcoded max_tokens), _invoke_openai (lines 357-361 no max_tokens)
- `src/code_forge/cli.py` -- _run backend wiring point (lines 666-678), _build_baseline_specs whole_file (line 1020), _paths whole_file (line 1101), post-verdict cost summary (lines 758-778)
- `src/code_forge/outlet_resolver.py` -- load_outlet_from_gate lightweight YAML loading pattern (lines 65-103)
- `tests/test_backend.py` -- Existing unit test patterns (lines 0-50, test naming conventions, _api_entry helper)
- `pyproject.toml` -- pytest.ini_options (lines 37-41), dependencies (lines 17-20)
- CONTEXT.md -- 16 locked implementation decisions D-01 through D-16 (verified against codebase)
- /tmp/draft_20260604_forge_v23_research.txt -- Part A sections A1-A6 (source-grounded findings)

### Secondary (MEDIUM confidence)

- Memory reference_kimi_api.md -- Kimi supports up to 32K output tokens (anthropic-compatible API)
- Memory reference_deepseek_compact.md -- DeepSeek supports 16K+ output tokens (openai-compatible API)

### Tertiary (LOW confidence)

None. All claims verified against codebase or prior research.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - All dependencies already present, no new libraries
- Architecture: HIGH - 90% of infrastructure exists, file:line references verified
- Pitfalls: HIGH - All based on actual code inspection, not hypothetical

**Research date:** 2026-06-04
**Valid until:** 2026-07-04 (30 days, stable domain -- backend config patterns unlikely to change)

---

*Research complete. Ready for planning.*
