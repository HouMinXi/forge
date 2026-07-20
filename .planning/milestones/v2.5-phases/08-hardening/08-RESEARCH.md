# Phase 8: Hardening - Research

**Date:** 2026-06-02
**Confidence:** HIGH

## CLI-07: Subprocess Lifecycle

### Process Group Isolation
- `subprocess.Popen(start_new_session=True)` creates new session (setsid on Unix)
- `os.killpg(proc.pid, signal.SIGTERM)` kills entire group including grandchildren
- `proc.communicate(timeout=N)` raises `TimeoutExpired` -- proc still alive, must kill manually
- After killpg, call `proc.wait()` to reap zombie

### Signal Handler Safety
- Python signal handlers run between bytecodes, not mid-C-extension
- Safe in handler: set flag, kill subprocess, raise exception
- NOT safe: I/O, locks, malloc-heavy operations
- `os.killpg` is safe (thin syscall wrapper)
- Chain pattern from lock.py (lines 73-98): save previous handler, call it after cleanup

### Escalation Pattern
```python
def _kill_tree(proc):
    os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
```

### Pitfalls
- `preexec_fn` is NOT fork-safe in multithreaded programs (Python docs warning)
- `start_new_session=True` avoids this (handled by subprocess module internally)
- If child calls `setsid()` itself, it leaves parent's process group -- killpg misses it
- `claude -p` is unlikely to call setsid, but worth noting

## CLI-08: Cost Data Sources

### OpenAI API Response
```json
{
  "usage": {
    "prompt_tokens": 1234,
    "completion_tokens": 567,
    "total_tokens": 1801
  },
  "choices": [{"message": {"content": "..."}}]
}
```
Access: `resp_data["usage"]["prompt_tokens"]`, `resp_data["usage"]["completion_tokens"]`

### Anthropic API Response
```json
{
  "usage": {
    "input_tokens": 1234,
    "output_tokens": 567
  },
  "content": [{"text": "..."}]
}
```
Access: `resp_data["usage"]["input_tokens"]`, `resp_data["usage"]["output_tokens"]`

### CLI Backend (claude -p --output-format json)
- stdout is the LLM content directly (JSON-formatted)
- No usage/token fields in stdout
- Token data may be available via `claude usage` command but not per-invocation
- Decision D-07: report Usage(0, 0) for cli backends

### LLMResult Design
```python
@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

@dataclass(frozen=True)
class LLMResult:
    content: Any
    usage: Usage = Usage()
    duration_s: float = 0.0
```

### Callers to Update (breaking change)
- `factories.py:227` -- `llm_invoke(prompt)` result used as dict -> needs `.content`
- `falsify_real.py:44` -- same pattern
- All test mocks returning raw dicts -> return LLMResult(content=dict)

## BOTH-02: Editor Configuration

### VS Code
- Claude Code extension reads env from terminal profile or `.env`
- `settings.json`: `"terminal.integrated.env.linux"` for env vars
- Tasks: `tasks.json` `options.env` for task-specific env

### Cursor
- Built-in AI uses its own config, not env vars
- For forge CLI: same terminal env as VS Code
- Cursor-specific: `.cursorrules` for AI behavior

### PyCharm
- Run/Debug Configurations: Environment variables field
- `.env` file support via EnvFile plugin
- Terminal: inherits system env or configured in Settings > Tools > Terminal

### Documentation Structure
```
README.md          # Quick Start section (3 env vars + basic usage)
docs/
  configuration.md # Full env var reference + backends.yaml format
  setup-vscode.md  # VS Code + Claude Code extension setup
  setup-cursor.md  # Cursor editor setup
  setup-pycharm.md # PyCharm setup
```
