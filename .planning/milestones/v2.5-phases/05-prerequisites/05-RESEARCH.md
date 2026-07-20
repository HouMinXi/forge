# Phase 5: Prerequisites - Research

**Researched:** 2026-05-31
**Confidence:** HIGH
**Agent:** gsd-phase-researcher (sonnet)

## Standard Stack

All Phase 5 modules use stdlib or existing forge dependencies only:
- `tomllib` (stdlib 3.12+) for pyproject.toml parsing
- `shutil.which()` (stdlib) for PATH tool detection
- `subprocess` (stdlib) for claude -p probe
- `yaml` (existing dep) for tools.yaml/gate.yaml generation
- `pathlib` (stdlib) for file operations
- No new dependencies required

## Architecture Patterns

### Pattern 1: Precedence Resolver (from mode_resolver.py)

`outlet_resolver.py` copies this pattern exactly:
- Pure function: `resolve_outlet(env_value, gate_config, auth_result) -> str`
- Precedence: explicit override (env/gate.yaml) > auth probe > error
- Edge cases: empty string falls through, whitespace raises ValueError, case-insensitive
- Source attribution in errors: "invalid outlet 'foo' from FORGE_OUTLET env"
- **Key difference from mode_resolver:** auth probe failure is NOT a default value -- it raises CliError

### Pattern 2: Env Resolver (from env_resolver.py)

Auth timeout follows this pattern:
- `resolve_auth_timeout(cli_value, env) -> int`
- Precedence: cli > FORGE_AUTH_TIMEOUT env > default(20)
- Validation: >=1, sanity cap, CliError on invalid

### Pattern 3: Registry Loader (from registry.py)

Auto-detect generates YAML that must round-trip through `load_registry()`:
- tools.yaml schema: `tools:` mapping with name -> {command, output_format, file_patterns}
- ToolConfig dataclass fields: name, command, args, output_format, file_patterns, required, timeout, exclude_patterns, working_dir, enabled
- Empty tools (`tools: []`, `tools: null`, `tools: {}`) returns `{}` -- detect must prevent this (D-02/D-03)

## Toolchain Detection Research

### pyproject.toml Tool Sections (Python)

| Tool | pyproject.toml key | PATH binary | Default command for tools.yaml |
|------|--------------------|-------------|-------------------------------|
| ruff | `[tool.ruff]` | `ruff` | `ruff check --output-format=json` |
| pylint | `[tool.pylint]` | `pylint` | `pylint --output-format=json` |
| flake8 | `[tool.flake8]` | `flake8` | `flake8 --format=json` (via flake8-json) |
| pytest | `[tool.pytest.ini_options]` | `pytest` | `pytest` (gate.yaml test runner) |
| mypy | `[tool.mypy]` | `mypy` | `mypy --output=json` |
| black | `[tool.black]` | `black` | N/A (formatter, not linter) |

**Note:** Tool commands use standard invocations. Tools read their own config from pyproject.toml natively -- detect does NOT pass config flags (D-01 post-review).

### Fallback Detection (no pyproject.toml)

1. Scan for `*.py` files in project root (max depth 2)
2. Check for `setup.py`, `setup.cfg`, `requirements.txt`
3. If Python inferred: check PATH for known tools
4. If nothing detected: D-02 error stop

### Output Format for tools.yaml

```yaml
tools:
  ruff:
    command: "ruff check --output-format=json"
    output_format: ruff_json
    file_patterns: ["*.py"]
  shellcheck:
    command: "shellcheck --format=json1"
    output_format: shellcheck_json
    file_patterns: ["*.sh", "*.bash"]
```


**NOTE (R2-5 fix):** `command` values are strings, not lists. `ToolConfig.command` is type `str` (registry.py:38). The V1 fix established this -- list format would fail `load_registry()` validation.
This format passes `load_registry()` validation (verified empirically in discuss-phase).

## Auth Probe Research

### claude -p Behavior (empirically tested)

| Scenario | Exit Code | Stderr | Duration |
|----------|-----------|--------|----------|
| Valid auth | 0 | (none) | ~12s cold, ~3s warm |
| Invalid model | 1 | "There's an issue with the selected model..." | <1s |
| Binary not found | 127 | "command not found" | <1s |
| Timeout | N/A | subprocess.TimeoutExpired | = timeout value |
| No auth configured | 1 | (varies by auth method) | <3s |

### Error Discrimination (D-21)

```python
# Approach: check binary first, then subprocess with timeout
if shutil.which("claude") is None:
    return AuthResult(ok=False, error="claude binary not found in PATH")

try:
    result = subprocess.run(
        ["claude", "-p", "ack"]  # NOTE: --max-tokens removed per R3-1 -- claude CLI does not support it,
        capture_output=True, text=True, timeout=timeout
    )
    if result.returncode == 0:
        return AuthResult(ok=True)
    else:
        # Parse stderr for auth-specific errors
        return AuthResult(ok=False, error=classify_error(result.stderr))
except subprocess.TimeoutExpired:
    return AuthResult(ok=False, error="auth probe timed out after {timeout}s")
```

### Caching Strategy

- Location: `~/.cache/code-forge/auth_cache.json` (user-level, not project)
- Schema: `{"ok": true, "timestamp": 1717100000, "ttl_seconds": 300}`
- Success: cache with 5-min TTL
- Failure: do NOT cache (immediate retry on next review)
- Write-through: subprocess auth failure invalidates cache immediately
- Use `platformdirs.user_cache_dir("code-forge")` or `os.environ.get("XDG_CACHE_HOME", "~/.cache")`

## Outlet Resolver Research

### gate.yaml Integration (D-22)

Current `load_gate_config()` (gate_check.py:63-64):
```python
if not isinstance(data, dict) or "test" not in data:
    raise ValueError("gate.yaml must have a 'test' section")
```

**Options for reading outlet:**
- **(a) Relax validation:** Remove `test` requirement -- impacts 51 existing tests
- **(b) Separate reader:** `load_outlet_from_gate(path) -> str | None` -- zero risk to existing code

**Recommendation:** Option (b). Read only `outlet:` key via lightweight YAML parse. Leave `load_gate_config()` unchanged.

### resolve-outlet Subcommand

- Register via argparse subparsers (cli.py ~line 170 pattern)
- Output: `cli` or `inline` to stdout, exit 0
- Error: diagnostic to stderr, exit 1
- No flags needed (reads env + gate.yaml + auth probe internally)

## Common Pitfalls

### P-1: Silent degradation to self-drive (ROOT CAUSE of v2.2)
When CLI dispatch fails, LLM "helps" by self-driving the review. This recreates the fake-APPROVED bug. Mitigation: D-10/D-13 FAIL CLOSED + D-15 exit contract.

### P-2: `tools: []` bypass
`load_registry()` returns `{}` for empty list. Mitigation: D-03 emptiness defined by `load_registry()` result, not file content.

### P-3: gate.yaml `test:` requirement
Auto-generated gate.yaml without `test:` crashes `load_gate_config()`. Mitigation: D-23 requires minimal valid template OR relaxed validation.

### P-4: Auth probe token cost
Each probe consumes API tokens. Mitigation: D-06 minimal "ack" prompt (~5 tokens; claude CLI has no --max-tokens flag), D-08 5-min cache.

### P-5: Cross-process cache
Session-level cache useless across Phase 7 subprocesses. Mitigation: D-08 file-based only.

### P-6: Stale success cache
Cached "OK" can go stale (token expires). Mitigation: D-08 short TTL + write-through invalidation.

### P-7: FORGE_OUTLET whitespace
`FORGE_OUTLET="  "` must raise, not fall through. Mitigation: D-14 copies mode_resolver edge cases.

### P-8: Mixed-language repos
Multiple config files (pyproject.toml + package.json). Mitigation: v2.2 Python-first; framework extensible.

## Validation Architecture

### Test Matrix (18 minimum tests across 3 modules)

**test_detect.py (~8 tests):**
1. Python project with pyproject.toml + ruff config -> generates tools.yaml with ruff
2. Python project without pyproject.toml, has *.py files -> fallback detection
3. Empty project (no Python indicators) -> D-02 error stop
4. Existing tools.yaml with valid tools -> skip (idempotent)
5. Existing tools.yaml with `tools: []` -> treat as missing, regenerate
6. --force flag -> overwrite existing
7. Generated YAML round-trips through load_registry()
8. Detection report format matches D-04 (Detected/Missing)

**test_auth.py (~6 tests):**
1. Successful probe -> AuthResult(ok=True)
2. Timeout -> AuthResult(ok=False, error mentions timeout)
3. Binary not found -> AuthResult(ok=False, error mentions PATH)
4. Auth failure -> AuthResult(ok=False, error mentions auth)
5. Cache hit within TTL -> returns cached result without subprocess
6. Cache invalidation on failure -> next call re-probes

**test_outlet_resolver.py (~8 tests):**
1. FORGE_OUTLET=cli -> returns "cli" (override wins)
2. FORGE_OUTLET=inline -> returns "inline"
3. FORGE_OUTLET="" -> falls through to gate.yaml
4. FORGE_OUTLET="  " -> raises ValueError
5. gate.yaml outlet: cli -> returns "cli"
6. No override + auth OK -> returns "cli" (fail-safe)
7. No override + auth fail -> raises CliError
8. Case-insensitive: FORGE_OUTLET=CLI -> returns "cli"

## Security Domain

### ASVS V5 (Input Validation)
- FORGE_OUTLET validated against allow-list {cli, inline}
- FORGE_AUTH_TIMEOUT validated: int >= 1, sanity cap
- gate.yaml outlet field: same allow-list validation
- pyproject.toml: parsed with stdlib tomllib (safe), no eval

### STRIDE Analysis (minimal -- Phase 5 is infrastructure)
- **Tampering:** Auth cache file in user dir could be tampered to skip probe. Mitigated: cache is convenience only, subprocess failure invalidates.
- **Info Disclosure:** Auth cache reveals "auth works" but not credentials. Acceptable risk.
- **Denial of Service:** Timeout + TTL prevent repeated slow probes.

---

*Research complete. Ready for planning.*
