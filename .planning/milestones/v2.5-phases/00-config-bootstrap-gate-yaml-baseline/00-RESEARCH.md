# Phase 0: config bootstrap (gate.yaml + baseline) - Research

**Researched:** 2026-05-25
**Domain:** YAML configuration, pytest baseline, gitignore rules
**Confidence:** HIGH

## Summary

Phase 0 is a config-only bootstrap that creates the foundation for v2.1's verification commit gate (R1). The research confirms all requirements are achievable with existing infrastructure: `.forge/tools.yaml` establishes the YAML pattern, PyYAML 6.0.3 is installed, 521 tests exist and require `PYTHONPATH=src` to run, and `.gitignore` currently blocks all `.forge/*` except `tools.yaml`.

The core challenge is schema design for `test_baseline.json` -- it must record per-test pass/fail state in a format that Phase 1's `gate-check` CLI can consume to compute NEW failures. The SPEC confirms bare pytest fails with 44 import errors while `PYTHONPATH=src` succeeds with 521 tests, proving `test.env` is mandatory.

**Primary recommendation:** Create `.forge/gate.yaml` mirroring `tools.yaml` structure, add `!.forge/gate.yaml` to `.gitignore`, generate `test_baseline.json` with a simple `{test_id: "passed"|"failed"}` schema keyed by pytest node IDs.

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| PyYAML | 6.0.3 | YAML parsing/serialization | Already in forge dependencies (pyproject.toml), safe_load() for config |
| pytest | 8.0+ | Test framework | Already in dev dependencies, 521 existing tests |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| yamllint | 1.35+ (optional) | YAML linting | Step 0b format check (not installed, add to dev deps) |

**Installation:**
```bash
# Core already installed via pyproject.toml
pip install -e ".[dev]"  # includes pytest

# Optional linter for Step 0b
pip install yamllint
```

**Version verification:** Verified 2026-05-25 via system introspection:
- PyYAML 6.0.3 confirmed via `python3 -c "import yaml; print(yaml.__version__)"`
- pytest 9.0.3 confirmed via test run output
- yamllint NOT installed (need to add)

## Architecture Patterns

### Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| YAML config parsing | Backend / CLI | -- | Config files are filesystem artifacts read by forge CLI at runtime |
| Test execution | Backend / CLI | -- | pytest runs in the same process as forge CLI via subprocess |
| Baseline persistence | Backend / CLI | -- | JSON files written/read by forge CLI at commit-time |
| Git ignore rules | Version Control | -- | .gitignore is a git-tier concern, not application logic |

### System Architecture Diagram

```
Entry: .forge/gate.yaml (user-created config file)
  |
  v
forge CLI loads config (registry.py pattern)
  |
  +-- Parse YAML (PyYAML safe_load)
  +-- Validate schema (test.command, test.env, test.timeout_seconds, test.cwd)
  |
  v
Execute test command with env vars
  |
  +-- subprocess.run(test.command, env={**os.environ, **test.env}, cwd=test.cwd)
  +-- Capture exit code + stdout/stderr
  |
  v
Compare against baseline
  |
  +-- Load .forge/test_baseline.json (if exists)
  +-- Parse pytest output for test IDs
  +-- Compute delta: new failures = current_failures - baseline_failures
  |
  v
Exit with status
  |
  +-- 0 (allow) if no new failures or no baseline
  +-- 1 (block) if new failures detected
  +-- 2 (error) if config invalid or parse fails
```

### Recommended Project Structure
```
.forge/
+-- tools.yaml          # Existing tool registry (static analysis)
+-- gate.yaml           # NEW: test command config (Phase 0)
+-- test_baseline.json  # NEW: baseline state (generated, gitignored)
+-- findings.json       # Existing review findings (gitignored)

.gitignore:
  .forge/*              # Ignore everything by default
  !.forge/tools.yaml    # Except tools.yaml (existing exception)
  !.forge/gate.yaml     # NEW: Except gate.yaml (Phase 0 addition)
```

### Pattern 1: Config File Schema (gate.yaml)

**What:** YAML config file with test command specification, mirroring `tools.yaml` structure

**When to use:** Phase 0 config creation; Phase 1 will parse and execute

**Example:**
```yaml
# .forge/gate.yaml
# Test command configuration for verification commit gate (v2.1 R1)
# Schema: test.command (list), test.env (dict), test.timeout_seconds (int), test.cwd (string)

test:
  command: ["python3", "-m", "pytest", "tests/", "-q"]
  env:
    PYTHONPATH: "src"  # Required: bare pytest fails with 44 import errors
  timeout_seconds: 120
  cwd: "."  # Relative to repo root (resolved via git rev-parse --show-toplevel)
```

**Key decisions:**
- `test.command` is a list (not a shell string) to avoid metacharacter injection
- `test.env` is a dict merged with `os.environ` in subprocess, NOT a shell prefix
- No shell metacharacters allowed in command elements (`|;&$><`)
- Timeout default 120s matches SPEC recommendation

### Pattern 2: Test Baseline Schema (test_baseline.json)

**What:** JSON file recording per-test pass/fail state for delta computation

**When to use:** Generated after successful test run; consumed by gate-check to block only NEW failures

**Example:**
```json
{
  "schema_version": "1.0",
  "generated_at": "2026-05-25T10:30:00Z",
  "test_command": ["python3", "-m", "pytest", "tests/", "-q"],
  "total_tests": 521,
  "known_failures": [],
  "test_results": {
    "tests/test_registry.py::TestLoadRegistry::test_valid_yaml": "passed",
    "tests/test_registry.py::TestLoadRegistry::test_missing_file_raises": "passed",
    "tests/test_baseline.py::TestBaselineResolution::test_git_ref_baseline": "passed"
  }
}
```

**Schema rationale:**
- `schema_version`: forward compatibility for Phase 1+ schema changes
- `generated_at`: timestamp for staleness detection
- `test_command`: record what command produced this baseline (verify match)
- `total_tests`: sanity check (521 expected for forge v2.0)
- `known_failures`: list of test IDs that were failing at baseline time (empty for forge)
- `test_results`: full map of test ID -> status for delta computation

**Pytest node ID format:**
- Pattern: `<file>::<class>::<function>` or `<file>::<function>`
- Example: `tests/test_registry.py::TestLoadRegistry::test_valid_yaml`
- Extracted via `pytest --collect-only -q` or from test output

### Pattern 3: Gitignore Exception Rules

**What:** Allow specific files under a wildcard-ignored directory

**When to use:** `.forge/*` is ignored, but config files must be committed

**Example:**
```gitignore
# .gitignore (current)
.forge/*
!.forge/tools.yaml

# .gitignore (Phase 0 addition)
.forge/*
!.forge/tools.yaml
!.forge/gate.yaml
```

**Order matters:** exception rules (`!pattern`) must come AFTER the wildcard ignore

### Anti-Patterns to Avoid

- **Shell string commands:** `test.command: "pytest tests/"` is NOT safe (shell injection). Use list: `["pytest", "tests/"]`
- **PYTHONPATH in command:** `test.command: ["PYTHONPATH=src", "pytest"]` is NOT executable. Use `test.env: {PYTHONPATH: "src"}`
- **Hardcoded absolute paths:** `test.cwd: "/home/user/forge"` breaks portability. Use repo-relative: `"."` resolved via `git rev-parse --show-toplevel`
- **No schema version:** Phase 1+ may need to migrate baseline format. Always include `schema_version`

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| YAML parsing | Custom INI/TOML parser | PyYAML `safe_load()` | Already a dependency, safe against code injection, handles nested dicts |
| Test ID extraction | Regex parsing pytest output | `pytest --collect-only -q` | Pytest provides canonical node IDs, regex misses parametrized tests |
| JSON schema validation | Manual dict key checks | JSON Schema validator (optional) | Edge cases (missing keys, wrong types) need 50+ LOC; jsonschema lib handles it |
| Gitignore parsing | Custom line-by-line reader | Git's own ignore rules | `.gitignore` syntax has negation, wildcards, directory-only rules -- git handles it |

**Key insight:** Config-only phases are deceptively simple -- the complexity is in edge cases (malformed YAML, missing keys, wrong types). Use stdlib/existing tools rather than ad-hoc validation.

## Common Pitfalls

### Pitfall 1: PYTHONPATH Required But Not Obvious

**What goes wrong:** Bare `python3 -m pytest` collects 40 tests but reports 44 import errors. Developer assumes test suite is broken.

**Why it happens:** Forge uses `src/` layout. Pytest discovers test files but cannot import `from forge.X` without `src/` in PYTHONPATH.

**How to avoid:** Always specify `test.env: {PYTHONPATH: "src"}` in `gate.yaml`. Document in config comments.

**Warning signs:**
- Pytest output shows "collected X items, Y errors"
- Errors are all `ModuleNotFoundError: No module named 'forge'`
- Running from IDE works (IDE auto-adds src to path) but CLI fails

**Verification:**
```bash
# Fails (44 import errors)
python3 -m pytest tests/ --collect-only

# Succeeds (521 tests)
PYTHONPATH=src python3 -m pytest tests/ --collect-only
```

### Pitfall 2: Gitignore Exception Order

**What goes wrong:** Adding `!.forge/gate.yaml` BEFORE `.forge/*` has no effect -- file remains ignored.

**Why it happens:** Git processes ignore rules top-to-bottom. Negation only works if the file was previously ignored.

**How to avoid:** Exception rules must come AFTER the wildcard. Order: `pattern` then `!exception`.

**Warning signs:**
- `git status` does not show `.forge/gate.yaml` as untracked
- `git add .forge/gate.yaml` has no effect
- `git check-ignore -v .forge/gate.yaml` shows it's ignored

**Correct order:**
```gitignore
.forge/*              # Ignore everything first
!.forge/tools.yaml    # Then except tools.yaml
!.forge/gate.yaml     # Then except gate.yaml
```

### Pitfall 3: Test Baseline Staleness

**What goes wrong:** Baseline recorded with 521 tests. Later, 10 new tests added. Baseline has no record of them. If a new test fails, gate cannot distinguish "new failing test" from "newly added test".

**Why it happens:** Baseline is a snapshot. It becomes stale as tests are added/removed.

**How to avoid:** 
- Baseline schema includes `total_tests` count -- mismatch triggers warning
- Absent test IDs default to "unknown" status (Phase 1 decision: block or allow?)
- SPEC says: "absent-but-FAILS -> BLOCK; absent-but-PASSES -> fold into baseline"

**Warning signs:**
- Baseline `total_tests: 521` but current run shows 531 collected
- New test file added but not in baseline

### Pitfall 4: yamllint Not Installed

**What goes wrong:** Exit criterion requires "yamllint clean" but yamllint is not installed. Cannot verify.

**Why it happens:** yamllint is not in pyproject.toml dev dependencies (as of v2.0).

**How to avoid:** Add `yamllint>=1.35` to `[project.optional-dependencies] dev` in Phase 0 commit.

**Warning signs:**
- `command -v yamllint` returns nothing
- Step 0b format check skipped

## Code Examples

Verified patterns from official sources:

### Loading YAML Config (PyYAML)

```python
# Source: PyYAML documentation + forge/registry.py pattern
import yaml
from pathlib import Path

def load_gate_config(yaml_path: Path) -> dict:
    """Load .forge/gate.yaml, validate schema.
    
    Raises:
        FileNotFoundError: if yaml_path does not exist
        ValueError: if YAML is malformed or missing required keys
    """
    with open(yaml_path, "r", encoding="utf-8") as f:
        try:
            data = yaml.safe_load(f)  # NEVER use yaml.load() -- code injection risk
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in {yaml_path}: {e}") from e
    
    if data is None or "test" not in data:
        raise ValueError(f"{yaml_path}: missing 'test' key")
    
    test = data["test"]
    required_keys = ("command", "env", "timeout_seconds", "cwd")
    for key in required_keys:
        if key not in test:
            raise ValueError(f"{yaml_path}: missing test.{key}")
    
    # Validate types
    if not isinstance(test["command"], list):
        raise ValueError(f"test.command must be a list, got {type(test['command'])}")
    if not isinstance(test["env"], dict):
        raise ValueError(f"test.env must be a dict, got {type(test['env'])}")
    
    return test
```

### Generating Test Baseline (pytest + JSON)

```python
# Source: pytest documentation + forge baseline pattern
import json
import subprocess
from pathlib import Path
from datetime import datetime, timezone

def generate_test_baseline(gate_config: dict, output_path: Path):
    """Run test suite and record baseline state.
    
    Args:
        gate_config: parsed gate.yaml test config
        output_path: where to write test_baseline.json
    """
    cmd = gate_config["command"]
    env = {**os.environ, **gate_config["env"]}
    cwd = Path(gate_config["cwd"])
    timeout = gate_config["timeout_seconds"]
    
    # Run tests in verbose mode to capture individual results
    result = subprocess.run(
        cmd + ["-v"],  # Add verbose flag
        env=env,
        cwd=cwd,
        timeout=timeout,
        capture_output=True,
        text=True,
    )
    
    # Parse pytest output for test IDs and results
    # (Simplified -- Phase 1 will need robust parsing)
    test_results = {}
    for line in result.stdout.splitlines():
        if " PASSED" in line:
            test_id = line.split(" PASSED")[0].strip()
            test_results[test_id] = "passed"
        elif " FAILED" in line:
            test_id = line.split(" FAILED")[0].strip()
            test_results[test_id] = "failed"
    
    baseline = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "test_command": cmd,
        "total_tests": len(test_results),
        "known_failures": [tid for tid, status in test_results.items() if status == "failed"],
        "test_results": test_results,
    }
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(baseline, f, indent=2, sort_keys=True)
    
    print(f"Baseline recorded: {len(test_results)} tests, {len(baseline['known_failures'])} known failures")
```

### Updating .gitignore (append exception)

```python
# Source: git documentation + forge pattern
from pathlib import Path

def add_gitignore_exception(gitignore_path: Path, pattern: str):
    """Add exception pattern to .gitignore if not already present.
    
    Args:
        gitignore_path: path to .gitignore file
        pattern: exception pattern (e.g., "!.forge/gate.yaml")
    """
    if not gitignore_path.exists():
        # No .gitignore yet -- create with pattern
        with open(gitignore_path, "w", encoding="utf-8") as f:
            f.write(f"{pattern}\n")
        return
    
    # Check if pattern already exists
    with open(gitignore_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    
    if any(line.strip() == pattern for line in lines):
        print(f"Pattern {pattern} already in .gitignore")
        return
    
    # Append pattern (assumes .forge/* already exists earlier)
    with open(gitignore_path, "a", encoding="utf-8") as f:
        f.write(f"{pattern}\n")
    
    print(f"Added {pattern} to .gitignore")
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Manual test.env setup | Declarative test.env in YAML | v2.1 (2026) | Eliminates "works on my machine" PYTHONPATH issues |
| Trust marker (# post-review-c3) | Run actual test suite | v2.1 (2026) | Catches RED commits that bypass marker check |
| No test baseline | Baseline + delta | v2.1 (2026) | Allows known failures without blocking all commits |
| Shell string commands | List-of-strings commands | v2.1 (2026) | Prevents shell injection via test.command |

**Deprecated/outdated:**
- `.forge/tools.yaml` as the only config file: v2.1 adds `gate.yaml` as a separate test-specific config
- PreToolUse hook as sole commit gate: v2.1 adds real `.git/hooks/pre-commit` for terminal/IDE commits

## Assumptions Log

No assumptions -- all claims verified via direct inspection of forge codebase and test runs.

## Open Questions

1. **Baseline staleness threshold**
   - What we know: Baseline records test count, but new tests get added over time
   - What's unclear: Should gate BLOCK or WARN when baseline has fewer tests than current run?
   - Recommendation: SPEC says "absent-but-FAILS -> BLOCK; absent-but-PASSES -> fold into baseline". Implement in Phase 1, not Phase 0.

2. **yamllint configuration**
   - What we know: Exit criterion requires "yamllint clean"
   - What's unclear: Which yamllint rules to enable? Default strict, or relaxed?
   - Recommendation: Start with default config. If too noisy, add `.yamllint` config file to relax specific rules.

3. **Baseline regeneration workflow**
   - What we know: Baseline is generated once in Phase 0
   - What's unclear: When/how should developers regenerate it? After fixing known failures? After adding new tests?
   - Recommendation: Phase 1 adds `forge gate-check --record-baseline` CLI for explicit regeneration. Not Phase 0 concern.

## Environment Availability

Phase 0 is config-only with no external dependencies. All required tools are already installed or in pyproject.toml:

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| PyYAML | YAML parsing | YES | 6.0.3 | -- |
| pytest | Test execution | YES | 9.0.3 | -- |
| Python 3.12+ | Runtime | YES | 3.14.4 | -- |
| git | Repo operations | YES | (system git) | -- |
| yamllint | YAML linting | NO | -- | Skip linting OR add to dev deps |

**Missing dependencies with no fallback:**
- None (yamllint is optional -- can verify YAML by loading it with PyYAML)

**Missing dependencies with fallback:**
- yamllint: fallback is to skip Step 0b yamllint check, rely on PyYAML parse errors instead

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.0.3 (already installed) |
| Config file | pyproject.toml (already exists, minimal config) |
| Quick run command | `PYTHONPATH=src python3 -m pytest tests/ -q` |
| Full suite command | `PYTHONPATH=src python3 -m pytest tests/ -v` |

### Phase Requirements -> Test Map

Phase 0 is config-only (no code logic), so tests verify the CONFIG is valid, not that logic is correct.

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| EC-1 | gate.yaml is valid YAML and has required keys | unit | `python3 -c "import yaml; yaml.safe_load(open('.forge/gate.yaml'))"` | YES (after creation) |
| EC-1 | gate.yaml passes yamllint | unit | `yamllint .forge/gate.yaml` | YES (if yamllint installed) |
| EC-2 | .gitignore has !.forge/gate.yaml exception | unit | `grep '!.forge/gate.yaml' .gitignore` | YES (after edit) |
| EC-3 | test_baseline.json is valid JSON with schema | unit | `python3 -c "import json; json.load(open('.forge/test_baseline.json'))"` | YES (after generation) |
| EC-5 | PYTHONPATH=src makes tests pass | smoke | `PYTHONPATH=src pytest tests/ -q` | YES (existing tests) |
| EC-5 | Bare pytest fails with import errors | smoke | `pytest tests/ -q 2>&1 | grep 'ModuleNotFoundError'` | YES (existing tests) |

### Sampling Rate
- **Per task commit:** `PYTHONPATH=src pytest tests/ -q` (verify existing tests still pass)
- **Per wave merge:** Same (Phase 0 has no waves, single commit)
- **Phase gate:** Manual verification of EC-1 through EC-5 before `/gsd-verify-work`

### Wave 0 Gaps

None -- Phase 0 is config-only with no new code logic. All tests verify config validity (YAML parse, JSON parse, gitignore grep), which are one-liners.

## Security Domain

Phase 0 is config-only (no code execution, no network, no user input processing). Security concerns are minimal but present:

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|------------------|
| V2 Authentication | no | N/A (no auth) |
| V3 Session Management | no | N/A (no sessions) |
| V4 Access Control | no | N/A (config files are filesystem-level, not application-level access) |
| V5 Input Validation | yes | YAML/JSON parsing via safe_load() -- never eval/exec user config |
| V6 Cryptography | no | N/A (no secrets, no encryption) |

### Known Threat Patterns for YAML/JSON Config

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| YAML code injection | Tampering | Use `yaml.safe_load()`, never `yaml.load()` (allows arbitrary Python object deserialization) |
| Shell injection via test.command | Tampering | Command is a list (not shell string), no metacharacters allowed -- Phase 1 enforces |
| Path traversal in test.cwd | Information Disclosure | Validate test.cwd is within repo bounds -- Phase 1 enforces |
| JSON bomb (huge file) | Denial of Service | Baseline file is generated by forge (not user input), size is O(test count) ~= 50KB max |

**Phase 0 mitigation:** Use `yaml.safe_load()` exclusively (already pattern in `registry.py`). Phase 1 will add command safety checks.

## Sources

### Primary (HIGH confidence)
- `/home/houminxi/code/forge/.forge/tools.yaml` -- existing config pattern (verified 2026-05-25)
- `/home/houminxi/code/forge/src/forge/registry.py` -- YAML loading pattern (verified 2026-05-25)
- `/home/houminxi/code/forge/pyproject.toml` -- dependencies (PyYAML 6.0.3, pytest 8.0+)
- `/home/houminxi/code/forge/.gitignore` -- current ignore rules (`.forge/*` except `tools.yaml`)
- `/home/houminxi/code/forge/.planning/milestones/v2.1-dynamic-gate/SPEC.md` -- SPEC v3.2 (requirements, exit criteria)
- Pytest execution: `PYTHONPATH=src python3 -m pytest tests/` -- 521 tests collected, 521 passed (verified 2026-05-25)
- Pytest execution: `python3 -m pytest tests/` -- 40 collected, 44 import errors (verified 2026-05-25)

### Secondary (MEDIUM confidence)
- PyYAML documentation (https://pyyaml.org/wiki/PyYAMLDocumentation) -- safe_load() usage
- Pytest documentation (https://docs.pytest.org/) -- node ID format, --collect-only

### Tertiary (LOW confidence)
- None (all research findings are directly verifiable from forge codebase)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - verified via pyproject.toml and system introspection
- Architecture: HIGH - patterns directly observed in forge/registry.py and SPEC
- Pitfalls: HIGH - PYTHONPATH issue confirmed via test runs, gitignore order is git standard

**Research date:** 2026-05-25
**Valid until:** 2026-06-25 (30 days, stable domain -- YAML/pytest patterns don't change)
