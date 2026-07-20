# T01 Findings: MCP Sampling via VSCode Copilot (2026-07-01)

## Bugs Fixed This Session

### BUG-1: ForgeLock signal crash in worker thread (FIXED)
- **File**: `src/code_forge/lock.py:73`
- **Root cause**: `_install_signal_handlers()` calls `signal.signal()` which
  only works from the main thread. `_dispatch_sampling` runs `machine.run()`
  via `asyncio.to_thread` (worker thread), which calls `ForgeLock.__enter__`
- **Fix**: Added `threading.current_thread() is not threading.main_thread()`
  guard -- skip signal handler installation in worker threads. Lock file
  cleanup still works via `release()` in `__exit__`, just without
  signal-interrupt protection.
- **Status**: FIXED, not yet committed

### BUG-2: Flatpak VSCode cannot find host Python packages (FIXED)
- **File**: `home/.local/bin/code-forge-mcp-pass`
- **Root cause**: Flatpak VSCode spawns MCP stdio processes inside the sandbox.
  Sandbox Python has no access to host site-packages (mcp, code_forge).
- **Fix**: mcp.json command changed to `flatpak-spawn --host <wrapper>` so
  the MCP server runs on the host. PYTHONPATH added to wrapper for editable
  install.
- **Status**: FIXED, not yet committed

### BUG-3: Flatpak config path mismatch (FIXED)
- **File**: `~/.var/app/com.visualstudio.code/config/Code/User/mcp.json`
- **Root cause**: Flatpak VSCode uses `~/.var/app/com.visualstudio.code/config/`
  not `~/.config/Code/`. Writing to wrong path = config never read.
- **Status**: FIXED (manual), needs documentation

### BUG-4: Flatpak ${workspaceFolder} unresolvable (FIXED)
- **Root cause**: `${workspaceFolder}` in mcp.json cwd field does not resolve
  inside Flatpak VSCode even with a folder open.
- **Fix**: Use `flatpak-spawn --host --env=FORGE_PROJECT_DIR=<path>` instead
  of `cwd: ${workspaceFolder}`.
- **Status**: FIXED (manual), needs `code-forge setup-vscode` automation

### BUG-5: Flatpak env vars not propagated via flatpak-spawn (FIXED)
- **Root cause**: mcp.json `env` block sets vars in sandbox env, but
  `flatpak-spawn --host` does NOT forward sandbox env to host process.
- **Fix**: Pass env vars as `--env=VAR=value` args to flatpak-spawn.
- **Status**: FIXED (manual), needs documentation

## Bugs To Fix (Next Phase)

### BUG-6: Empty sampling response not actionable
- **File**: `src/code_forge/llm_invoke.py:1134` (partially fixed)
- **Current**: Raises `LLMInvokeError("sampling response is empty")` with
  remediation text. But the 3-pass loop in `build_sampling_l1_provider`
  catches this and converts to INFRA finding, then continues with next pass
  (which also fails). Result: 3 INFRA findings, no useful review.
- **Needed**: Detect empty response on FIRST pass, immediately fall back to
  subprocess if a backend is configured. If no backend: raise ToolError with
  clear message explaining the user needs Copilot Pro or an API key.
- **Detection**: Check `model` field -- if `copilotcli/*`, warn before even
  attempting (these models cannot generate sampling content).

### BUG-7: No auto-fallback from sampling to subprocess
- **File**: `src/code_forge/mcp_server.py:430-438`
- **Current**: `outlet: sampling` is all-or-nothing. If sampling fails
  (empty response, token limit, model error), the tool returns ToolError.
- **Needed**: When sampling fails AND gate.yaml has backends configured,
  automatically fall back to subprocess. Log: "Sampling failed, falling
  back to subprocess backend <name>".
- **Existing pattern**: Truncation fallback already exists at line 368-392
  (LLMInvokeError with "truncated" triggers CLI subprocess fallback). Extend
  to cover empty-response and other sampling failures.

### BUG-8: 128K token limit for large diffs via sampling
- **File**: `src/code_forge/factories.py` (build_sampling_l1_provider)
- **Current**: Full diff sent in prompt. For large diffs (>3000 lines),
  prompt exceeds Copilot's 128K limit (error: "prompt token count of 142054
  exceeds the limit of 128000").
- **Needed**: Token-estimate the prompt before sending. If over limit:
  (a) chunk the diff by file, review each file separately, or
  (b) fall back to subprocess with a larger-context backend.
- **Note**: The subprocess path uses the API directly where models like
  deepseek-chat have 128K context but forge controls the prompt size.

## Documentation Needed

### DOC-1: MCP Sampling Prerequisites (docs/setup-mcp.md)
Add a section explaining:
- Sampling requires a REAL model, not `copilotcli/auto` (free tier)
- VSCode: must configure `chat.mcp.serverSampling` with a Pro-tier model
- Claude Code: sampling uses the session model (works with Pro subscription)
- Cursor: sampling uses Pro-tier model
- If no Pro subscription: use `outlet: subprocess` with an API backend

### DOC-2: Flatpak VSCode Setup (docs/setup-mcp.md)
Add a section for Flatpak-specific setup:
- Config path: `~/.var/app/com.visualstudio.code/config/Code/User/mcp.json`
- Command: `flatpak-spawn --host <wrapper-path>`
- Env vars: pass via `--env=` args, NOT mcp.json env block
- PYTHONPATH: required for editable install

### DOC-3: MCP Sampling Model Compatibility Matrix (docs/setup-mcp.md)
| Client | Free tier | Pro tier | Notes |
|--------|-----------|----------|-------|
| VSCode Copilot | copilotcli/auto (empty) | claude-sonnet/gpt-4.1 (works) | Needs chat.mcp.serverSampling |
| Claude Code | Session model | Session model | Works with Pro |
| Cursor | TBD | TBD | Needs testing |
| PyCharm | TBD | TBD | MCP sampling support unclear |

### DOC-4: code-forge setup-vscode command (future)
Auto-detect:
- VSCode installation type (Flatpak/Snap/native)
- Config path
- Copilot subscription tier
- Generate correct mcp.json + wrapper

## Test Updates Needed

### TEST-1: lock.py threading test
Add test: `ForgeLock` in `asyncio.to_thread` does not crash.
```python
async def test_forgelock_worker_thread():
    with tempfile.TemporaryDirectory() as d:
        lock_path = Path(d) / 'test.lock'
        def run():
            with ForgeLock(lock_path):
                return 'ok'
        result = await asyncio.to_thread(run)
        assert result == 'ok'
```

### TEST-2: invoke_sampling empty response
Add test: empty `TextContent` raises `LLMInvokeError` with "empty" message.

### TEST-3: invoke_sampling copilotcli model detection
Add test: `model='copilotcli/auto'` in response triggers clear error.

### TEST-4: Flatpak wrapper PYTHONPATH
Add integration test or doc-test: wrapper sets PYTHONPATH correctly.

## Diagnostic Code to Remove Before Commit

- `src/code_forge/llm_invoke.py`: 2 debug print lines (raw_text, result.content)
- `src/code_forge/factories.py`: catch-all Exception handler (keep handler,
  replace traceback.print_exc with logging.warning)

## Evidence Links

- microsoft/vscode#299336: empty sampling from copilotcli/auto (CLOSED, fixed 1.112.0)
- microsoft/vscode#299635: fix sampling model selection to exclude CLI models
- microsoft/vscode#267354: sampling broken without model selection
- microsoft/vscode#286648: sampling with tools not supported
