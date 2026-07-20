# Forge MCP Test Matrix -- IDE x Model x Boundary

> ORACLE CORRECTION 2026-07-01 (verified against merged Phase 36, HEAD 2976934):
> The original M-dimension assumed sampling is auto-selected from a subscription.
> It is NOT. Sampling is chosen ONLY when outlet == "sampling" is set explicitly
> (FORGE_OUTLET=sampling OR gate.yaml `outlet: sampling`). There is a LOCKED
> invariant: no model-capability / subscription auto-detection anywhere
> (outlet_resolver.py:24,160; mcp_server.py:424-441,502-519). Corrected model
> below. Original T01/T04/T05/T08/B11 oracles were wrong and are fixed.

## Outlet selection truth (the oracle every M-test depends on)

MCP path (mcp_server.py:424-441 for forge_review, :502-519 for forge_gate_check):
```
1. outlet = FORGE_OUTLET env
2. if unset: outlet = gate.yaml "outlet:" key (if gate.yaml exists)
3. if outlet == "sampling":
     - client has NO sampling capability -> ToolError
       "Client does not support sampling capability."
     - else -> _dispatch_sampling (in-process, borrows client model)
4. else (any other value OR unset) -> _check_backend() (ToolError if none)
                                     -> subprocess CLI path
```
CLI path (outlet_resolver.py resolve_outlet):
```
--outlet > FORGE_OUTLET > gate.yaml outlet > zero-config guard (CliError)
        > backend reachability probe (reachable = subprocess; unreachable = CliError, FAIL CLOSED)
```
Key truths:
- Sampling is NEVER auto-selected. Requires explicit outlet: sampling.
- If outlet: sampling is set, sampling wins EVEN WITH backends configured
  (outlet key is checked BEFORE _check_backend). gate.yaml outlet beats backend.
- CLI resolver NEVER returns "sampling" (LOCKED); sampling is an MCP-only outlet.
- No-backend + no sampling outlet: MCP -> ToolError; CLI -> CliError, exit 2
  (EXIT_CLI_ERROR=2, exit_codes.py:15).
- Trap (mcp_server.py:370-371): the sampling path, if it needs a subprocess,
  MUST force --outlet subprocess, else gate.yaml outlet: sampling recurses.

## Dimension 1: IDE Client

| ID | Client | MCP Transport | Sampling capability | Notes |
|----|--------|--------------|--------------------|-------|
| C1 | VSCode + Copilot | stdio | Yes (Copilot Pro) | Most common target |
| C2 | Cursor | stdio | Yes (Cursor Pro) | Built-in AI, MCP via settings |
| C3 | PyCharm + AI Assistant | stdio | Unclear | JetBrains AI plugin, verify MCP spec |
| C4 | Claude Code | stdio | Yes | Native MCP, primary dev environment |

## Dimension 2: Outlet Configuration (CORRECTED -- was "Model Source")

Selection is driven by the outlet config, NOT by which subscription is present.

| ID | outlet config | backends in gate.yaml | Expected MCP outcome |
|----|---------------|----------------------|---------------------|
| M1 | none set | deepseek (with api_key_env) | subprocess (falls through to _check_backend) |
| M2 | `outlet: sampling` (or FORGE_OUTLET=sampling) | none | sampling IF client supports it, else ToolError |
| M3 | `outlet: sampling` | deepseek ALSO present | sampling WINS (outlet checked before backend) |
| M3b | none set | deepseek present | subprocess (no sampling contender exists) |
| M4 | none set | none | ToolError (MCP) / CliError exit 2 (CLI) -- FAIL CLOSED |

## Dimension 3: Boundary Conditions

| ID | Condition | What to verify |
|----|-----------|---------------|
| B1 | FORGE_PROJECT_DIR set correctly | MCP resolves workspace from env var |
| B2 | FORGE_PROJECT_DIR points to wrong dir (no gate.yaml) | ToolError with remediation message |
| B3 | FORGE_PROJECT_DIR not set, cwd = ~ | Walkup finds project OR clear error |
| B4 | gate.yaml exists but not trusted | ToolError: "run code-forge trust" hint |
| B5 | gate.yaml trusted, then model name changed | Trust NOT invalidated (benign field) |
| B6 | gate.yaml trusted, then base_url changed | Trust IS invalidated (dangerous field) |
| B7 | Legacy trust hash (pre-v2.7) | Auto-migrate to new hash, no re-ceremony |
| B8 | --allow-main in main worktree | Warning printed, review proceeds |
| B9 | No --allow-main in main worktree | CliError with bypass instructions |
| B10 | MCP reconnect (/mcp) | No zombie processes left |
| B11 | outlet: sampling BUT client lacks sampling capability | Hard ToolError (NO fallback); only fires when sampling requested |
| B12 | Large diff (>3000 lines) | No JSON parse error / timeout handled |
| B13 | Empty diff (no changes) | Clean exit, not false-green |
| B14 | Timeout during review | infra_errors surfaced, not silent hang |

---

## Test Cases

### T01: VSCode/Copilot + sampling outlet (C1 x M2) [CORRECTED]
**Setup:** VSCode logged into Copilot Pro; gate.yaml has `outlet: sampling`, no backends
**Steps:**
1. Set FORGE_PROJECT_DIR=~/code/forge in MCP server env
2. Ensure gate.yaml has `outlet: sampling` (this is REQUIRED -- subscription alone does not select sampling)
3. In VSCode, invoke forge_review via MCP tool
4. Check: _dispatch_sampling path taken (borrows Copilot model, no user API key)
5. Check: real findings returned (not DELEGATED/PASS)
**Expected:** Review completes via MCP sampling. If `outlet: sampling` is NOT set, it will NOT use sampling -- it will hit _check_backend and error (no backend).

### T02: VSCode/Copilot + No Config (C1 x M4)
**Setup:** VSCode logged into Copilot Pro, but NO gate.yaml at all
**Steps:**
1. Set FORGE_PROJECT_DIR=~/code/some-random-project (no .code-forge/)
2. Invoke forge_review
**Expected:** ToolError containing "gate.yaml not found" and "Run 'code-forge init'" (mcp_server.py:108). Assert on substring "code-forge init", not the whole sentence.

### T03: VSCode/Copilot + Custom Backend (C1 x M1)
**Setup:** VSCode logged in, gate.yaml has deepseek backend with api_key_env, no outlet key
**Steps:**
1. Invoke forge_review
2. Check: subprocess path (no outlet: sampling -> falls to _check_backend -> CLI subprocess)
3. Check: deepseek API key consumed (not Copilot quota)
**Expected:** Review via subprocess, uses deepseek

### T04: VSCode/Copilot + sampling outlet AND backend (C1 x M3) [CORRECTED]
**Setup:** VSCode Copilot Pro; gate.yaml has BOTH `outlet: sampling` AND a deepseek backend
**Steps:**
1. Invoke forge_review (no flags)
2. Check: which path is taken
**Expected:** SAMPLING wins, NOT subprocess. The outlet key is checked before _check_backend (mcp_server.py:430). To force subprocess despite the sampling outlet, set FORGE_OUTLET=subprocess (env beats gate.yaml). (Original matrix claimed subprocess wins -- that was backwards.)

### T05: Cursor + sampling outlet (C2 x M2) [CORRECTED]
**Setup:** Cursor Pro logged in; gate.yaml `outlet: sampling`, no backends
**Steps:**
1. Configure MCP server in Cursor settings
2. Ensure `outlet: sampling` is set
3. Invoke forge_review
4. Check: sampling path uses Cursor's model
**Expected:** Review via MCP sampling. Without `outlet: sampling`, no sampling.

### T06: Cursor + No config (C2 x M4)
**Setup:** Cursor (Pro or free), no gate.yaml / no backends, no outlet
**Steps:**
1. Invoke forge_review
**Expected:** FAIL CLOSED -- ToolError (no backend). Note: subscription presence is irrelevant; only explicit outlet: sampling would change this.

### T07: Claude Code + Custom Backend (C4 x M1)
**Setup:** Claude Code session, gate.yaml with mimo-pro backend, no outlet key
**Steps:**
1. code-forge review via CLI (not MCP) -> CLI resolver: probe reachable -> subprocess
2. Check: subprocess outlet, mimo-pro API key used
3. Via MCP: invoke forge_review -> no outlet: sampling -> subprocess CLI path
4. Check: same backend used
**Expected:** Both CLI and MCP use the gate.yaml backend via subprocess

### T08: Claude Code + sampling outlet (C4 x M2) [CORRECTED]
**Setup:** Claude Code; gate.yaml `outlet: sampling`, no backends
**Steps:**
1. Invoke forge_review via MCP
2. Check: _dispatch_sampling uses the client (Claude Code) model
3. Check: no API key needed from user
**Expected:** MCP sampling via client model. Requires `outlet: sampling`.

### T09: PyCharm + Custom Backend (C3 x M1)
**Setup:** PyCharm with MCP plugin, gate.yaml with custom backend, no outlet
**Steps:**
1. Configure MCP server in PyCharm
2. Invoke forge_review
3. Check: subprocess outlet works
**Expected:** Review via subprocess; MCP transport works in PyCharm
**Note:** Sampling (M2) testable only if PyCharm's MCP client advertises sampling capability; verify capability first.

### T10: PyCharm + No MCP Support (C3 x M4)
**Setup:** PyCharm without MCP plugin
**Steps:**
1. Attempt to configure forge MCP
**Expected:** MCP not available; user directed to CLI workflow

---

## Trust Boundary Tests (any client) -- oracles VERIFIED against trust.py

### T11: Trust Hash Migration (B7)
**Setup:** Existing trust store with old all-fields hash
**Steps:**
1. Run code-forge trust --status with old trust store
2. Check: auto-migrates to dangerous-fields-only hash (trust.py:140-143,198-202)
3. Run again: still trusted (no re-ceremony)
**Expected:** Silent one-time migration

### T12: Benign Config Change (B5)
**Setup:** Trusted gate.yaml
**Steps:**
1. Change model name in gate.yaml
2. Run code-forge trust --status
**Expected:** Still trusted -- model is NOT in DANGEROUS_FIELDS (trust.py:103)

### T13: Dangerous Config Change (B6)
**Setup:** Trusted gate.yaml
**Steps:**
1. Change base_url in gate.yaml
2. Run code-forge trust --status
**Expected:** Trust invalidated -- base_url IS in DANGEROUS_FIELDS (trust.py:23). VERIFIED full set (7 fields): {api_key_env, api_key_file, base_url, command, credentials_path, hook, shell}. Worth adding targeted cases for command/hook/shell too -- those are command-injection surface, higher-risk than base_url. Benign (NOT dangerous, trust survives): model, temperature, max_tokens, format.

### T14: Worktree Guard (B8 + B9) -- VERIFIED cli.py:341,1463-1466,1493-1494
**Steps:**
1. In main tree: code-forge review -> CliError with bypass instructions
2. In main tree: code-forge review --allow-main -> warning, proceeds
3. In main tree: FORGE_ALLOW_MAIN=1 code-forge review -> same (cli.py:1465)
4. In main tree: FORGE_SKIP_WORKTREE_CHECK=1 -> same (legacy alias, cli.py:1466)
5. In linked worktree: code-forge review -> proceeds normally
**Expected:** Guard blocks main tree; --allow-main / either env var bypasses with warning

---

## Error Handling Tests (any client)

### T15: MCP Zombie Cleanup (B10)
**Steps:**
1. Connect MCP in IDE
2. Run /mcp reconnect
3. Check: pgrep -f code-forge shows only 1 process
**Expected:** No zombie processes after reconnect

### T16: Large Diff Review (B12)
**Steps:**
1. Stage a large diff (>3000 lines)
2. Run forge_review
**Expected:** Completes or times out with clear message (not JSON parse error)

### T17: Empty Diff (B13)
**Steps:**
1. No uncommitted changes
2. Run forge_review
**Expected:** Clean exit, message about no changes (assert: not a false-green verdict)

### T18: Timeout Handling (B14) [ORACLE FIXED]
**Steps:**
1. Set --timeout 5 (very short) on a CLI review of a non-trivial diff
2. Run review
**Expected:** exit code 6 (EXIT_TIMEOUT, exit_codes.py:19). Assert on the EXIT CODE, not the exact string: the review path prints TimeoutBreaker's message (cli.py:1123-1124), which differs from smoke-run's "timed out after N seconds" (cli.py:831).

### T19: FORGE_PROJECT_DIR Not Set, CWD = ~ (B3)
**Steps:**
1. Unset FORGE_PROJECT_DIR
2. MCP server starts with cwd = ~
3. Invoke forge_review
**Expected:** Walkup finds gate.yaml OR clear ToolError

### T20: forge_resolve_outlet API Key Diagnostic
**Steps:**
1. Configure gate.yaml with deepseek backend
2. Unset DEEPSEEK_API_KEY
3. Run forge_resolve_outlet
**Expected:** exit 1, message names the key and backend. VERIFIED real string (2026-07-01, CLI resolve-outlet): "No review backend configured..." when no backend; with a backend configured but key unset: "Configure a review backend or set FORGE_OUTLET=inline. Reachability: DEEPSEEK_API_KEY not set. Export the API key for backend 'deepseek'." Assert on substring "DEEPSEEK_API_KEY not set" (NOT the original matrix guess "API key: DEEPSEEK_API_KEY (NOT SET)", which does not match).

---

## Automatable here vs needs-real-IDE (triage before running)

AUTOMATABLE on this machine (pure CLI, no IDE/subscription):
- T02 (via CLI: point at a dir with no gate.yaml), T03/T07 (CLI subprocess with a real backend key),
  T11, T12, T13, T14, T17, T18, T19, T20.
- M-dimension sampling logic CAN be unit-probed without a real IDE by calling the
  resolver / MCP handler with a stub session that advertises sampling capability.

NEEDS REAL IDE + REAL SUBSCRIPTION (manual, not automatable here):
- T01, T04, T05, T08 (real client sampling: VSCode Copilot / Cursor / Claude Code),
  T09, T10 (PyCharm), T15 (real /mcp reconnect in an IDE), B11 end-to-end.

Do NOT put the manual IDE tests in the P0 lane -- they are a multi-hour human lift.

## Coverage Matrix (outlet config axis)

|      | M1 backend-only | M2 sampling-outlet | M3 sampling+backend | M4 none |
|------|-----------------|--------------------|--------------------|---------|
| C1 VSCode | T03 | T01 | T04 | T02 |
| C2 Cursor | -- | T05 | -- | T06 |
| C3 PyCharm | T09 | -- | -- | T10 |
| C4 Claude Code | T07 | T08 | -- | -- |

## Priority

**Must pass (P0) -- oracle-verified, mostly automatable:**
T02, T03, T11, T12, T13, T14, T17, T18

**Should pass (P1):**
T07, T19, T20, T16

**Sampling path (P1, needs real client or stub session):**
T01, T04, T05, T08 -- confirm outlet: sampling is set; verify sampling actually
selected per the corrected oracle.

**Nice to verify (P2):**
T06, T09, T10, T15

---

## Test Execution Results (2026-07-01)

### T01: PARTIAL — sampling path detected correctly, execution thread mismatch
- sampling_capable=True ✓
- _build_review_context built ✓
- machine.run() started ✓
- FAILED: "requires the main interpreter thread" — asyncio.to_thread
  worker thread + MCP sampling callback = thread model conflict
- Root cause: machine.run() runs in worker thread (asyncio.to_thread),
  sampling L1 provider calls back to event loop via run_coroutine_threadsafe,
  but Copilot's MCP sampling handler may not support cross-thread callbacks
- BUG: forge issue — _dispatch_sampling needs to either run on the event
  loop directly or use a thread-safe sampling provider

### Flatpak VSCode Setup Issues (resolved during testing)
1. ModuleNotFoundError — Flatpak sandbox Python != host Python.
   Fix: PYTHONPATH in wrapper
2. ${workspaceFolder} unresolvable — Flatpak VSCode limitation.
   Fix: flatpak-spawn --host + --env= flags
3. Config path mismatch — Flatpak uses ~/.var/app/.../config/ not ~/.config/.
   Fix: write to correct path
