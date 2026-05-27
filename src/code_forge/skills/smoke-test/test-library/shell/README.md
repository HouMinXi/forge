# Shell Smoke-Test Primitives

## Quick Start

```bash
source ~/.claude/skills/smoke-test/test-library/shell/primitives.sh
```

## Primitives Index

### Helpers

| Function | Parameters | Purpose |
|----------|-----------|---------|
| `run_and_capture <cmd...>` | command + args | Execute command, capture stdout/stderr/status to `$SMOKE_LAST_STDOUT`/`$SMOKE_LAST_STDERR`/`$SMOKE_LAST_STATUS` |
| `run_concurrent <N> <cmd...>` | N=count, then command | Launch N background instances, store results in `$CONCURRENT_RESULT_DIR`/`$CONCURRENT_PIDS` |
| `concurrent_wait` |  --  | Reap all background PIDs from `run_concurrent` |

### Execution

| Function | Parameters | Purpose |
|----------|-----------|---------|
| `assert_success <status> [desc]` | exit code, description | PASS if status == 0 |
| `assert_failure <status> [desc]` | exit code, description | PASS if status != 0 |
| `assert_exit_code <expected> <actual> [desc]` | expected, actual, description | PASS if actual == expected |

### Output

| Function | Parameters | Purpose |
|----------|-----------|---------|
| `assert_output_contains <output> <pattern> [desc]` | text, substring, description | PASS if output contains pattern (fixed string) |
| `assert_output_not_contains <output> <pattern> [desc]` | text, substring, description | PASS if output does NOT contain pattern |
| `assert_stderr_empty [desc]` | description | PASS if `$SMOKE_LAST_STDERR` file is empty |
| `assert_stderr_contains <stderr> <pattern> [desc]` | text, substring, description | PASS if stderr text contains pattern |

### File/State

| Function | Parameters | Purpose |
|----------|-----------|---------|
| `assert_file_exists <file> [desc]` | path, description | PASS if regular file exists |
| `assert_file_not_exists <file> [desc]` | path, description | PASS if file does NOT exist |
| `assert_file_contains <file> <pattern> [desc]` | path, substring, description | PASS if file contains pattern |

### Security

| Function | Parameters | Purpose |
|----------|-----------|---------|
| `assert_no_command_exec <input> [context]` | string, context | PASS if no `$()`, backticks, `<()`, `;&\|` in input |
| `assert_no_command_exec_json <input> [context]` | string, context | JSON/URL-safe: only checks `$()`, backticks, `<()`, skips `;&\|` |
| `assert_no_path_traversal <input> [context]` | string, context | PASS if no `../`, `..\`, URL-encoded traversal |

### Process

| Function | Parameters | Purpose |
|----------|-----------|---------|
| `assert_no_zombie [pid] [desc] [max_depth]` | root PID, description, max depth (default: 100) | PASS if no zombie processes in process tree (recursive `/proc` scan, depth-limited) |
| `assert_temp_clean [pattern] [desc]` | glob pattern, description | PASS if no matching temp files (default: `/tmp/smoke-test-*`) |

### Data

| Function | Parameters | Purpose |
|----------|-----------|---------|
| `assert_json_valid <file-or-str> [desc] [schema]` | path or JSON string, description, optional jq schema expression | PASS if valid JSON; optional `schema` validates structure via `jq -e`; SKIP if jq not installed |

## Decision Table

| Change Type | Required | Optional |
|------------|----------|----------|
| New CLI parameter | `assert_success`, `assert_output_contains` | `assert_stderr_empty` |
| Error handling | `assert_failure`, `assert_stderr_contains` | `assert_exit_code` |
| File operations | `assert_file_exists`, `assert_file_contains` | `assert_file_not_exists` |
| Concurrency | `assert_success` (xN), `assert_no_zombie` | `assert_temp_clean`, `assert_file_contains` (race check) |
| Security patch | `assert_no_command_exec`, `assert_no_path_traversal` | `assert_output_not_contains` |
| API response | `assert_json_valid`, `assert_output_contains` | `assert_no_command_exec_json` (if response passed to shell) |
| Config parsing | `assert_success`, `assert_file_contains` |  --  |
| Log output | `assert_output_contains` | `assert_stderr_empty` |
| Cleanup logic | `assert_file_not_exists` |  --  |

## Act Pattern

### Standard (single command)

```bash
run_and_capture ./script.sh --flag value
output=$(cat "$SMOKE_LAST_STDOUT")
assert_success "$SMOKE_LAST_STATUS" "script.sh --flag"
assert_output_contains "$output" "expected substring" "flag output"
```

### Concurrent (N instances, zombie-safe)

```bash
run_concurrent 5 ./kimi-next-key.sh
sleep 0.5                              # wait for child exits, zombie forms
assert_no_zombie $$ "kimi-next-key"     # detect BEFORE reap
concurrent_wait; cw_status=$?
assert_success "$cw_status" "concurrent execution"
# Per-instance diagnostics on failure
if [[ $cw_status -ne 0 ]]; then
    for ((i=0; i<5; i++)); do
        s=$(cat "$CONCURRENT_RESULT_DIR/$i.status" 2>/dev/null || echo unknown)
        (( s != 0 )) && echo "  instance $i FAIL: exit=$s"
    done
fi
```

## Safety Primitive Usage Guide

### `assert_no_command_exec`

**Applicable**: input passed to `eval`/`sh -c`, constructing shell commands, `source $input`.

**NOT applicable**: JSON/XML/YAML data, URL query strings (contain `&`), regex patterns (contain `|`). For these, use `assert_no_command_exec_json` instead.

### `assert_no_command_exec_json`

JSON/URL-safe variant. Only checks for command execution/substitution (`$()`, backticks, `<()`), skips `;&|` which appear legitimately in URL query strings and regex patterns.

| Input | `assert_no_command_exec` | `assert_no_command_exec_json` |
|-------|--------------------------|------------------------------|
| `$(id)` | FAIL | FAIL |
| `` `whoami` `` | FAIL | FAIL |
| `<(cat /etc/passwd)` | FAIL | FAIL |
| `; rm -rf /` | FAIL | PASS (false positive avoided) |
| `key=value&foo=bar` | FAIL | PASS (URL-safe) |
| `error\|warn\|info` | FAIL | PASS (regex-safe) |

**Use when**: input contains structured data (JSON, URL params, regex), but still want to catch command execution via `$()`, backticks, or `<()`.

### Known Boundaries

These attack vectors are intentionally not covered. They require semantic parsing beyond smoke-test scope:

| Vector | Reason |
|--------|--------|
| `${IFS}` / `${var:-cmd}` variable expansion | `${}` is ubiquitous in legitimate configs, templates, and sed patterns. Distinguishing malicious expansion from benign usage requires shell-semantic parsing. Cover via semgrep in Step 0. |
| Nested obfuscation (`$($(echo id))`) | Detected (outer `$()` fires). Inner payload is not de-obfuscated  --  that's the job of a static analyzer. |

### `assert_no_path_traversal`

Detects `../` and URL-encoded `%2e%2e%2f`. Absolute path detection (`^/`) is commented out by default  --  enable per context.

### `assert_no_zombie` Depth Limit

The optional `max_depth` parameter (default: 100) prevents infinite recursion in abnormally deep process trees. On depth exceeded, emits a WARNING and returns without counting as FAIL:

```bash
# Default: max depth 100
assert_no_zombie $$ "default depth"

# Shallow scan: only check depth <= 10
assert_no_zombie $$ "shallow check" 10

# Deep scan for known deep process trees
assert_no_zombie $$ "deep check" 500
```

### `assert_json_valid` Schema Validation

The optional `schema` parameter accepts a jq boolean expression. After format validation passes, runs `jq -e "$schema"` for structural validation:

```bash
# Format-only validation (backward compatible)
assert_json_valid '{"key":"value"}' "basic check"

# Schema: assert a specific value
assert_json_valid '{"status":"ok"}' "status check" '.status == "ok"'

# Schema: assert field exists and is truthy
assert_json_valid '{"count":5}' "has count" '.count'

# Schema: assert numeric range
assert_json_valid '{"code":200}' "http 200" '.code >= 200 and .code < 300'

# From file with schema
echo '{"items":["a","b"]}' > /tmp/resp.json
assert_json_valid /tmp/resp.json "has items array" '.items | length > 0'
```

Schema validation uses `jq -e` (exit 0 = truthy, exit 1 = false/null). A schema that returns `false` or `null` means FAIL.

## Combination Rules

### Contradictions (using both guarantees a FAIL, not a tool bug)

| Pair | Why |
|------|-----|
| `assert_success` + `assert_failure` | Cannot both pass on same exit code |
| `assert_exit_code 0` + `assert_exit_code 1` | Cannot both pass |
| `assert_stderr_empty` + `assert_stderr_contains` | Cannot both pass on same stderr |

### Recommended Combinations

- Command success + output: `assert_success` + `assert_output_contains`
- Command failure + error: `assert_failure` + `assert_stderr_contains`
- Security: `assert_no_command_exec` + `assert_no_path_traversal`
- File write: `run_and_capture cmd` -> `assert_success` + `assert_file_exists` + `assert_file_contains`

### Zombie Detection Race Window

`assert_no_zombie` must be called AFTER the target script's children have exited but BEFORE they are reaped. With `run_concurrent`:

1. `run_concurrent N cmd`  --  launches N background instances
2. `sleep D`  --  wait for inner children to exit (D depends on the script's internal runtime)
3. `assert_no_zombie $$`  --  scan while zombies are still present
4. `concurrent_wait`  --  reap after detection

The `sleep` duration is script-specific. If the negative test in `primitives_test.sh` produces SKIP, the zombie window was too short  --  the test is not counted as a failure.

The optional `max_depth` parameter (positional arg 3) limits recursion depth to prevent infinite loops in abnormally deep process trees. Default is 100. On exceeding the limit, a WARNING is emitted and the scan continues without counting as FAIL.

Reliable zombie test fixtures require a non-bash intermediate (bash auto-reaps direct children). The P3 verification uses `python3 subprocess.Popen` for this purpose  --  see `test_case2_concurrency.sh` for the reference pattern.

## Concurrency Safety

### SMOKE_LAST_* Variable Overwrite

`run_and_capture` exports global variables (`SMOKE_LAST_STDOUT`, `SMOKE_LAST_STDERR`, `SMOKE_LAST_STATUS`). Nested or sequential calls overwrite previous values:

```bash
# WRONG: using $SMOKE_LAST_STDOUT after a second run_and_capture
run_and_capture ./outer.sh
outer_status=$SMOKE_LAST_STATUS
run_and_capture ./inner.sh            # overwrites $SMOKE_LAST_STDOUT
assert_output_contains "$(cat "$SMOKE_LAST_STDOUT")" "outer text"  # FAIL: reads inner.sh output

# CORRECT: save immediately after each run_and_capture
run_and_capture ./outer.sh
outer_stdout=$(cat "$SMOKE_LAST_STDOUT")
outer_status=$SMOKE_LAST_STATUS
run_and_capture ./inner.sh
assert_output_contains "$outer_stdout" "outer text"  # PASS
```

### run_concurrent Isolation

Each background instance creates its own temp files in `$CONCURRENT_RESULT_DIR/{i}.{out,err,status}`. No variable conflicts between instances.

### Multi-Line Primitive Output

Some primitives (e.g. `assert_no_zombie`) output debug details before the PASS/FAIL line. When capturing with `$(...)`, use `grep -q '^FAIL:'` rather than `[[ "$result" == FAIL:* ]]`:

```bash
result=$(assert_no_zombie "$pid" "check")
# WRONG: prefix match fails when ZOMBIE lines precede FAIL:
[[ "$result" == FAIL:* ]]
# CORRECT: grep for the line of interest:
echo "$result" | grep -q '^FAIL:'
```

## Dependencies

| Tool | Required By | Install |
|------|------------|---------|
| `jq` | `assert_json_valid` | `dnf install jq` (RHEL/Fedora), `apt install jq` (Debian/Ubuntu) |
| `python3` | P3 verification fixtures only | pre-installed on RHEL/Fedora |

Without `jq`, `assert_json_valid` returns SKIP (exit 0, never FAIL).
`python3` is NOT required by `primitives.sh` itself  --  only by the P3 verification fixtures that test zombie detection with a non-bash intermediate.

## Secret Security

- FAIL debug output masks `sk-*` API keys: `sed 's/sk-[A-Za-z0-9]\{20,\}/\*\*\*/g'`
- Tests should use fake credentials (`test-key-12345`), never real API keys

## Prefix Isolation

| Prefix | Owner | Scanned by `assert_temp_clean` |
|--------|-------|-------------------------------|
| `/tmp/smoke-fw-*` | Framework (run_and_capture, run_concurrent) | No |
| `/tmp/smoke-test-*` | Tests (script-generated temp files) | Yes (default) |
