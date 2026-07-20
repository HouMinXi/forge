# Technology Stack: v2.2 Path A Editor Orchestration

**Project:** code-review-forge (code-forge CLI)
**Researched:** 2026-05-30
**Scope:** Stack additions for Path A (editor dispatches to CLI instead of self-driving)

## Executive Summary

v2.2 needs NO new Python dependencies. The existing stdlib modules
(`subprocess`, `shutil`, `os`, `json`, `pathlib`) already handle every
requirement: subprocess management, tool discovery, auth detection,
and cross-platform concerns. Adding dependencies would widen the attack
surface for zero benefit.

The work is architectural (new modules using existing primitives), not
library-adoption. The research below maps each v2.2 requirement to
specific stdlib capabilities, identifies integration points with the
existing codebase, and flags cross-platform pitfalls that need code-level
mitigation.

---

## 1. SKILL.md -> CLI Dispatch (Bash Tool Interface)

### How Claude Code Skills Invoke Shell Commands

**Confidence: HIGH** (official docs + Context7 verified)

Claude Code's SKILL.md is a markdown file with YAML frontmatter. When a
user invokes the skill (e.g., `/code-forge`), Claude reads the SKILL.md
and follows its instructions. The skill instructs Claude to use the
**Bash tool** to execute shell commands.

Critical constraint (from official docs): **User-invoked skills like
`/code-review` and built-in commands are only available in interactive
mode. In `-p` mode, describe the task you want to accomplish instead.**

This means the SKILL.md role flip works as follows:

```
CURRENT (Path C - self-drive):
  User invokes /code-forge in Claude Code editor
  -> Claude reads SKILL.md
  -> Claude self-drives the 9-pass pipeline via its own reasoning
  -> No mechanical enforcement, cycle counting is honor-system

NEW (Path A - dispatch):
  User invokes /code-forge in Claude Code editor
  -> Claude reads SKILL.md
  -> SKILL.md says: "Use Bash tool to run: code-forge review"
  -> CLI subprocess handles cycle counting mechanically
  -> Claude's role is executor/dispatcher, not driver
```

### SKILL.md Frontmatter for Dispatch

The SKILL.md needs `allowed-tools` to include Bash:

```yaml
---
name: code-forge
description: "Dispatch code review to the CLI orchestrator."
allowed-tools: Bash(code-forge:*), Read
---
```

### What the Bash Tool Provides

| Capability | Detail |
|---|---|
| Command execution | `subprocess.Popen` under the hood, captures stdout/stderr |
| Output capture | Truncated at ~30,000 chars |
| Timeout | Default 120s, configurable to 600s (10 min) |
| Exit code | Available to Claude for pass/fail decisions |
| Working directory | Inherits from the session |
| Environment | Inherits from the session |

**Integration point:** The existing `llm_invoke.py` already does
`subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)`.
The Bash tool in Claude Code works the same way but from the other
direction -- Claude is invoking `code-forge review` as a subprocess.

### No New Dependencies Needed

The SKILL.md rewrite is pure markdown. The CLI side (`code-forge review`)
already exists as a subcommand in `cli.py`. The dispatch pattern
requires no new Python code for the invocation itself.

---

## 2. Zero-Config Toolchain Auto-Detection

### Requirement

`code-forge review` must auto-detect syntax checkers, linters, and test
runners without requiring `.code-forge/tools.yaml` on first run.

### Recommended Approach: `shutil.which()` Cascade

**Confidence: HIGH** (stdlib, well-documented, already used in codebase)

`shutil.which()` is the correct tool. It is:
- In the stdlib since Python 3.3
- Cross-platform (respects `PATHEXT` on Windows)
- Already used in 5 places in the codebase (`llm_invoke.py`,
  `runner.py`, `factories.py`, `machine.py`, `git.py`)

**New module:** `src/code_forge/toolchain.py`

The auto-detection cascade per language:

```python
import shutil
from pathlib import Path

def _detect_python_toolchain(cwd: Path) -> dict:
    """Detect Python syntax/lint/test tools. Returns ToolConfig-ready dict."""
    tools = {}

    # Syntax: always available (stdlib)
    tools["py_compile"] = {
        "command": "python3",
        "args": ["-m", "py_compile"],
        "available": True,  # guaranteed
    }

    # Lint: prefer ruff > pylint > flake8
    for linter in ("ruff", "pylint", "flake8"):
        if shutil.which(linter) is not None:
            tools["lint"] = {"command": linter, "available": True}
            break
    # else: tools["lint"]["available"] = False

    # Test: prefer pytest > unittest
    if shutil.which("pytest") is not None:
        tools["test"] = {"command": "pytest", "available": True}
    else:
        tools["test"] = {
            "command": "python3",
            "args": ["-m", "unittest", "discover"],
            "available": True,  # stdlib fallback
        }

    return tools
```

### Project-Indicator Heuristics

Beyond PATH discovery, detect project type from filesystem signals:

| Indicator File | Language | Tools to Probe |
|---|---|---|
| `pyproject.toml`, `setup.py`, `setup.cfg` | Python | py_compile, ruff/pylint, pytest |
| `Cargo.toml` | Rust | cargo check, cargo clippy, cargo test |
| `go.mod` | Go | go vet, golangci-lint, go test |
| `package.json` | JS/TS | node --check, eslint, jest/vitest |
| `Makefile` (kernel) | C | make, checkpatch.pl |
| `*.sh` in diff | Shell | bash -n, shellcheck, bats |

**Important:** Project indicators tell us WHAT to look for. `shutil.which()`
tells us WHETHER it is installed. Both checks needed.

### Config Generation (auto-init)

When `.code-forge/tools.yaml` is missing:

1. Detect project type from indicator files
2. Probe tools with `shutil.which()`
3. Generate minimal `tools.yaml` with discovered tools
4. Log which tools were found and which were missing
5. Emit `tool_missing: <name>` findings for absent optional tools

**No new dependencies.** `shutil.which()`, `pathlib.Path`, `os.path.exists()`
are all stdlib. YAML writing uses the already-depended `pyyaml>=6.0`.

### What NOT to Build

Do NOT use:
- `importlib.metadata` to check installed pip packages (unreliable
  for system-installed tools like `shellcheck`)
- Third-party tool-discovery libraries (unnecessary abstraction)
- `subprocess.run([tool, "--version"])` as a detection method (slow,
  side-effect-prone). Use `shutil.which()` for existence, capture
  version only once at pipeline start (already done in `runner.py`
  `capture_tool_version()`)

---

## 3. Fail-Fast Auth Detection for `claude -p`

### Authentication Priority Order

**Confidence: HIGH** (official docs at code.claude.com/docs/en/authentication)

Claude Code uses this precedence (highest to lowest):

1. Cloud provider (`CLAUDE_CODE_USE_BEDROCK` / `_VERTEX` / `_FOUNDRY`)
2. `ANTHROPIC_AUTH_TOKEN` (bearer token for LLM gateways)
3. `ANTHROPIC_API_KEY` (direct API key from Console)
4. `apiKeyHelper` script (dynamic/rotating credentials)
5. `CLAUDE_CODE_OAUTH_TOKEN` (long-lived OAuth for CI)
6. Subscription OAuth credentials (Pro/Max/Teams/Enterprise default)

### Auth Detection Strategy

**Confidence: HIGH** (stdlib + existing pattern in `llm_invoke.py`)

The existing `_resolve_claude_binary()` in `llm_invoke.py` already does
`shutil.which("claude")`. Extend this with an auth probe:

```python
def check_claude_auth(binary: str) -> AuthStatus:
    """Fail-fast auth check. Returns status before expensive review."""
    # Step 1: binary exists?
    if binary is None:
        return AuthStatus.NO_BINARY

    # Step 2: can it respond? Run minimal prompt.
    result = subprocess.run(
        [binary, "-p", "echo ok", "--output-format", "json",
         "--model", "claude-haiku-4-5"],
        capture_output=True, text=True, timeout=30,
    )

    if result.returncode != 0:
        stderr = result.stderr.lower()
        if "authentication" in stderr or "api key" in stderr:
            return AuthStatus.AUTH_FAILURE
        if "rate_limit" in stderr:
            return AuthStatus.RATE_LIMITED
        return AuthStatus.UNKNOWN_ERROR

    return AuthStatus.OK
```

### Why a Minimal Probe, Not Env-Var Inspection

Checking `ANTHROPIC_API_KEY` in the environment would only detect one of
six auth methods. The user might have subscription auth (no env var at
all) or cloud provider auth. The only reliable check is to invoke
`claude -p` with a cheap model and see if it succeeds.

Cost: one Haiku call (~0.001 USD). Time: ~2-5s. Worth it to prevent
a 20-minute pipeline that fails on the first LLM pass.

### Error Categories from `claude -p` Stream Events

When `--output-format stream-json` is used, auth failures emit
`system/api_retry` events with structured error categories:

| `error` field | Meaning |
|---|---|
| `authentication_failed` | Invalid or expired credentials |
| `oauth_org_not_allowed` | Org not authorized for Claude Code |
| `billing_error` | Payment issue |
| `rate_limit` | Rate limited |
| `model_not_found` | Model not available |
| `server_error` | Anthropic infrastructure issue |

For the fail-fast check, `--output-format json` (non-streaming) is
simpler: check exit code + stderr text.

### Billing Warning

**Critical:** Starting June 15, 2026, `claude -p` usage on subscription
plans draws from a separate "Agent SDK credit" pool. Document this in
the configuration guide so users know auth probe + 3 review passes
consume Agent SDK credits, not interactive credits.

### What to Detect and Report

| Scenario | Detection | User Action |
|---|---|---|
| `claude` not on PATH | `shutil.which("claude") is None` | Install Claude Code CLI |
| Auth failure | nonzero exit + "authentication" in stderr | Set ANTHROPIC_API_KEY or log in |
| Rate limited | nonzero exit + "rate_limit" in stderr | Wait or switch model |
| Wrong model | nonzero exit + "model_not_found" in stderr | Set FORGE_LLM_MODEL |
| Bare mode + no key | Exit fail, bare skips OAuth/keychain | Set ANTHROPIC_API_KEY explicitly |

### No New Dependencies

All of this uses `subprocess.run()`, `shutil.which()`, and `json.loads()`.
No new packages needed.

---

## 4. Subprocess Management (Fresh Context Per Pass)

### Requirement

Each review pass (qodo/expert/adversarial) must run in a fresh
subprocess to prevent context leakage between passes. The existing
`llm_invoke.py` already does this correctly.

### Why stdlib `subprocess.run()` Is Sufficient

**Confidence: HIGH** (official Python docs, verified against codebase)

`subprocess.run()` spawns a fresh OS process for each call. State
(environment variables, working directory, open file descriptors) does
NOT leak between calls by design. This is exactly what PEP 517 codified:
"frontends should call each hook in a fresh subprocess, so that backends
are free to change process global state."

The existing `llm_invoke()` function already provides:
- Fresh process per call (inherent to `subprocess.run()`)
- Timeout handling (`timeout=timeout_s`)
- Exit code checking (`result.returncode != 0`)
- Structured output parsing (JSON from `--output-format json`)
- Temp file for large prompts (>1MB, avoids ARG_MAX)

### Environment Isolation

**Current state:** `llm_invoke.py` does NOT pass an explicit `env=`
parameter to `subprocess.run()`. This means it inherits `os.environ`.

**Recommendation:** This is correct for the auth detection use case.
The subprocess needs access to `ANTHROPIC_API_KEY`, `FORGE_LLM_MODEL`,
and `PATH`. Filtering to a minimal env would break cloud provider auth
(`CLAUDE_CODE_USE_BEDROCK` etc.) and `apiKeyHelper` scripts.

If stricter isolation is ever needed:

```python
# Safe copy pattern (recommended)
env = os.environ.copy()
env["FORGE_PASS_NAME"] = pass_name  # inject pass metadata
# Do NOT use env={} -- breaks auth

subprocess.run(cmd, env=env, ...)
```

### What NOT to Use

| Library | Why Not |
|---|---|
| `subprocess-monitor` | Overkill; adds WebSocket/async for a synchronous pipeline |
| `process_isolation` | Requires superuser for chroot; wrong threat model |
| `psutil` | Process monitoring, not management; adds dependency |
| `multiprocessing` | For parallel Python workers, not for calling external CLIs |

### Integration with Existing Code

The `l1_provider` in `factories.py` already calls `llm_invoke()` three
times in sequence (once per pass). Each call spawns a fresh `claude -p`
subprocess. The v2.2 change is structural:

```
CURRENT: l1_provider calls llm_invoke() 3x with different prompts
         (all in the same Python process, no skill invocation)

NEW:     same pattern, but prompts become fuller review instructions
         that guide the LLM to "systematically cover the whole diff
         risk surface" instead of a one-line role description
```

The subprocess isolation is already correct. The improvement is in
prompt quality, not subprocess management.

---

## 5. Cross-Platform Concerns

### Platform Support Matrix

**Confidence: MEDIUM** (official docs verified for Linux/macOS; Windows
patterns from community reports + npm deprecation notices)

| Platform | Claude CLI Install | Binary Name | `shutil.which()` |
|---|---|---|---|
| Linux | Native (`~/.local/bin/claude`) | `claude` | Works |
| macOS | Native (`~/.local/bin/claude`) | `claude` | Works |
| Windows | Native (`%USERPROFILE%\.local\bin\claude.exe`) | `claude.exe` | Works (PATHEXT) |
| Windows (npm, deprecated) | npm global (`claude.cmd`) | `claude.cmd` | Works (PATHEXT) |
| WSL | Either native or npm | `claude` | Works |

### Known Cross-Platform Pitfalls

**Pitfall 1: Windows `.cmd` files and `shell=False`**

`shutil.which("claude")` on Windows correctly finds `claude.cmd` thanks
to the `PATHEXT` environment variable. Python 3.12 `subprocess.run()`
on Windows CAN launch `.cmd` files directly via `CreateProcess` without
`shell=True`.

The `sh -c` large-prompt codepath in `llm_invoke.py` (line 72-75) needs
a Windows alternative:

```python
if os.name == "nt":
    # Windows: use cmd /c for shell expansion
    cmd = ["cmd", "/c", ...]
else:
    cmd = ["sh", "-c", ...]
```

**Pitfall 2: PATH differences across shells**

On macOS, `shutil.which("ruff")` may return `None` if ruff was installed
via Homebrew into `/opt/homebrew/bin/` but the Python process was
launched from a context where Homebrew's PATH is not set (e.g., a
system Python invoked by a pre-commit hook).

**Mitigation:** Document in the auto-init output: "If a tool is
installed but not detected, ensure it is on the PATH used by the
process invoking `code-forge`."

**Pitfall 3: `ARG_MAX` limits**

The existing `llm_invoke.py` already handles this: prompts >1MB are
written to a temp file. Linux `ARG_MAX` is ~2MB. macOS is ~256KB.
Windows has a ~32KB command line limit but environment block is separate.
No change needed.

**Pitfall 4: Windows subprocess flags**

Do NOT use `subprocess.CREATE_NEW_PROCESS_GROUP` -- it breaks stdin
piping. Do NOT use `subprocess.DETACHED_PROCESS` -- it is Windows-only
and causes `AttributeError` on Linux/macOS. The existing code correctly
avoids all platform-specific subprocess flags.

---

## Recommended Stack (Additions Only)

### New Modules (zero new dependencies)

| Module | Purpose | Lines (est.) | Dependencies |
|---|---|---|---|
| `src/code_forge/toolchain.py` | Zero-config tool auto-detection | ~200 | stdlib only |
| `src/code_forge/auth_check.py` | Fail-fast claude auth probe | ~80 | stdlib only |

### Modified Modules

| Module | Change | Why |
|---|---|---|
| `llm_invoke.py` | Windows `sh -c` -> `cmd /c` fallback | Cross-platform large prompt |
| `cli.py` | Wire auto-init into `review` subcommand | Zero-config entry point |
| `SKILL.md` | Rewrite: self-drive -> dispatch | Core v2.2 feature |
| `env_resolver.py` | Add `FORGE_LLM_MODEL` doc reference | Configuration guide |

### No New Dependencies

The `pyproject.toml` dependencies remain unchanged:

```toml
dependencies = [
    "pyyaml>=6.0",
    "unidiff>=0.7.5,<0.8.0",
]
```

---

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|---|---|---|---|
| Tool discovery | `shutil.which()` | `importlib.metadata` | Only finds pip packages, misses system tools |
| Subprocess mgmt | `subprocess.run()` | `subprocess-monitor` | Adds dependency for zero benefit |
| Auth detection | Minimal `claude -p` probe | Env-var inspection | Only catches 1 of 6 auth methods |
| Config format | YAML (tools.yaml) | TOML (pyproject.toml section) | Existing convention; pyyaml already depended |
| Process isolation | Inherent in subprocess | `process_isolation` lib | Requires superuser; wrong threat model |
| Prompt delivery | Temp file for >1MB | stdin pipe | `claude -p` reads /dev/tty, NOT stdin |

---

## Version Compatibility

| Component | Min Version | Current | Notes |
|---|---|---|---|
| Python | 3.12 | 3.12+ | `pyproject.toml` already specifies |
| Claude Code CLI | v2.1.x | v2.1.128+ | `--output-format json`, `--bare` |
| pyyaml | 6.0 | 6.0.2 | Already in dependencies |
| unidiff | 0.7.5 | 0.7.5 | Already in dependencies |

---

## Sources

- [Claude Code Authentication](https://code.claude.com/docs/en/authentication)
- [Run Claude Code Programmatically](https://code.claude.com/docs/en/headless)
- [Claude Code Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview)
- [Python subprocess docs](https://docs.python.org/3/library/subprocess.html)
- [Python shutil.which docs](https://docs.python.org/3/library/shutil.html#shutil.which)
- [PEP 517 subprocess discussion](https://discuss.python.org/t/pep-517-do-not-enforce-fresh-subprocess-calls/6156)
- [Claude Code slash command frontmatter (Context7)](/anthropics/claude-code)
- [Ruff auto-discovery](https://docs.astral.sh/ruff/configuration/)
- [Claude Code skill invocation in -p mode (Issue #38505)](https://github.com/anthropics/claude-code/issues/38505)
