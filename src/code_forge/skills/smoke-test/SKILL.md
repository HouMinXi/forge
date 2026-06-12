---
name: smoke-test
description: Post-review smoke testing  --  verify code works by assembling assertions from a reusable shell primitive library
allowed-tools:
  - Shell
  - ReadFile
  - WriteFile
---

# Smoke Test (Step 4 of Review Pipeline)

## Purpose

Execute AFTER three-cycle static review (coder -> code-review-expert -> adversarial-qe) with zero findings.
Verify code actually works at runtime using a reusable assertion primitive library.

Static analysis catches design issues; smoke tests catch runtime issues.

## When to Use

- **Mandatory**: After three-cycle review shows zero findings and before final commit
- **Optional**: When user asks "确定没问题么？" or similar verification questions
- **Skip**: Documentation-only changes (pure markdown/comments with no executable code)

## Workflow (4 Steps)

### Step A: Analyze the Change

Read the code diff. Identify:
- What changed (new CLI param? error handling? file I/O? concurrency? security fix?)
- The primary execution path (what command to run)
- Expected outputs, side effects, and edge cases

### Step B: Select Primitives

Source the library and consult the decision table:

```bash
source ~/.claude/skills/smoke-test/test-library/shell/primitives.sh
```

| Change Type | Required Primitives | Optional |
|------------|-------------------|----------|
| New CLI parameter | `assert_success`, `assert_output_contains` | `assert_stderr_empty` |
| Error handling | `assert_failure`, `assert_stderr_contains` | `assert_exit_code` |
| File operations | `assert_file_exists`, `assert_file_contains` | `assert_file_not_exists` |
| Concurrency | `assert_success` (xN), `assert_no_zombie` | `assert_temp_clean`, `assert_file_contains` (race check) |
| Security patch | `assert_no_command_exec`, `assert_no_path_traversal` | `assert_output_not_contains` |
| API response | `assert_json_valid`, `assert_output_contains` |  --  |
| Config parsing | `assert_success`, `assert_file_contains` |  --  |
| Log output | `assert_output_contains` | `assert_stderr_empty` |
| Cleanup logic | `assert_file_not_exists` |  --  |

Full primitive reference: `test-library/shell/README.md`  --  includes signatures, safety usage guide, combination rules, and zombie race constraints.

### Step C: Assemble Test Script

Act before Assert. One assertion per line. Values through parameters.

**Standard pattern**:

```bash
run_and_capture ./script.sh --flag value
output=$(cat "$SMOKE_LAST_STDOUT")
assert_success "$SMOKE_LAST_STATUS" "script.sh --flag"
assert_output_contains "$output" "expected text" "flag output"
```

**Error handling pattern**:

```bash
run_and_capture ./script.sh --bad-flag
assert_failure "$SMOKE_LAST_STATUS" "script.sh --bad-flag"
assert_stderr_contains "$(cat "$SMOKE_LAST_STDERR")" "error:" "bad flag error"
```

**Concurrency pattern (zombie detection before reap)**:

```bash
run_concurrent 5 ./script.sh
sleep 0.5                              # wait for child exits, zombie forms
assert_no_zombie $$ "concurrent run"    # detect BEFORE reap
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

**Security pattern**:

```bash
# Test injection payloads
for payload in '$(id)' '`whoami`' '; rm -rf /' '../../etc/passwd'; do
    assert_no_command_exec "$payload" "payload: $payload"
    assert_no_path_traversal "$payload" "payload: $payload"
done

# Test actual script input handling
run_and_capture ./script.sh '$(id)'
output=$(cat "$SMOKE_LAST_STDOUT")
assert_output_not_contains "$output" "uid=" "injection not executed"
```

### Step D: Execute and Record

Run the assembled test script. Count results:

```bash
PASS_COUNT=$(grep -c '^PASS: ' test_output.txt)
FAIL_COUNT=$(grep -c '^FAIL: ' test_output.txt)
```

**Exit criteria**:
- `FAIL_COUNT == 0`, all test categories covered (normal + boundary + security + concurrency if applicable)
- Any FAIL -> fix the code, restart from Step 0 (syntax pre-check) of the review pipeline

Record results in `REVIEW.md` Section 5 (Smoke Test Results):
- The assembled test script
- PASS/FAIL counts
- Any FAIL details with debug context

## Assembly Rules

1. **One assertion per expected behavior**  --  don't combine checks into one `[[ ... ]]`
2. **Act before Assert**  --  run the command first (via `run_and_capture`), then assert on the captured state
3. **Values through parameters**  --  captured stdout/stderr/status go directly into assertion parameters, not global regex
4. **Every test starts with `source primitives.sh`**  --  one line, all 19 functions (16 primitives + 3 helpers) available
5. **No gaps in coverage**  --  decision table required primitives are non-negotiable

## Prohibited Patterns

- Do NOT modify tested code  --  smoke test is read-only verification
- Do NOT depend on network  --  tests must pass offline
- Do NOT include syntax checks (`bash -n`, `shellcheck`, `py_compile`, `go vet`)  --  these belong in Step 0 (syntax pre-check), before review cycles begin

## Non-Shell File Strategy

Shell primitives exist because bash has no standard test framework. Python, Go, and C already have mature frameworks  --  use them.

| Language | Smoke Test Tool | Pattern |
|----------|----------------|---------|
| Shell | `source primitives.sh` | Decision table (Step B), standard patterns (Step C) |
| Python | `pytest` | `subprocess.run(cmd, capture_output=True, text=True)` + `assert r.returncode == 0` |
| Go | `go test` / `testing.T` | `exec.Command(...).CombinedOutput()` + `if err != nil { t.Fatal(...) }` |
| Kernel C | `primitives.sh` (build) + `runtest -n` (job XML) | See "Kernel C Changes" below |

Rationale: Python's `pytest` and Go's `testing` are industry standards that AI already knows. Replacing them with custom assertion functions provides no benefit and adds learning cost. Shell is the exception  --  it has no native test framework, so `primitives.sh` fills that gap. Kernel C is shell-adjacent: the verification targets (kbuild, runtest) are shell processes, so `primitives.sh` applies even though the source language is C.

### Kernel C Changes (CentOS Stream / RHEL)

Kernel smoke tests cannot achieve full functional verification within the 5-minute pre-commit window (Beaker runs take 30 min - 2 hr). Step 4 validates what's reachable locally; Step 5 handles Beaker.

For kernel C commits, the CLAUDE.md commit marker rule has an explicit exception: `# post-review-c3` in kernel context means Step 4 complete (build passes + test plan exists). Step 5 (Beaker) is a separate pre-merge gate, not a pre-commit requirement.

**Step 4 (pre-commit)**:

```bash
source ~/.claude/skills/smoke-test/test-library/shell/primitives.sh

# 1. Build affected subsystem
# In-tree (from kernel source dir):
run_and_capture make M=net/openvswitch -j$(nproc)
assert_success "$SMOKE_LAST_STATUS" "kbuild net/openvswitch"
# Out-of-tree (external module):
# run_and_capture make -C /lib/modules/$(uname -r)/build M=$PWD -j$(nproc)
# assert_success "$SMOKE_LAST_STATUS" "kbuild external module"

# 2. Identify corresponding kernel-qe test
ls code/kernel/networking/openvswitch/ovs_kernel_socketmap/
# If no existing test: note in REVIEW.md, plan follow-up

# 3. Verify Beaker job XML can be generated (dry-run, do NOT submit)
run_and_capture runtest -n --task=code/kernel/networking/openvswitch/ovs_kernel_socketmap
assert_success "$SMOKE_LAST_STATUS" "runtest dry-run  --  job XML generated"
```

**Step 5 (pre-merge, separate from Step 4)**:

```bash
runtest -B <build-id> --task=code/kernel/networking/openvswitch/ovs_kernel_socketmap
# Results on Beaker job page.
```

**Bug reproducer rules** (per CLAUDE.md):
- Reproducer MUST trigger on buggy kernels and NOT trigger on fixed kernels
- Manual reproduction insufficient -> escalate to kprobe/kretprobe kernel module
- IBT constraint (`CONFIG_X86_KERNEL_IBT=y`): function-pointer calls to static symbols fail CET  --  trigger through kernel's own code paths

## Output Format

Smoke test results go into `REVIEW.md` Section 5:

```
## 5. Smoke Test Results

**Date**: YYYY-MM-DD HH:MM
**Files tested**: <list>
**Primitives sourced**: ~/.claude/skills/smoke-test/test-library/shell/primitives.sh

### Test Script
\```bash
source ~/.claude/skills/smoke-test/test-library/shell/primitives.sh
run_and_capture ./script.sh --flag
output=$(cat "$SMOKE_LAST_STDOUT")
assert_success "$SMOKE_LAST_STATUS" "script.sh --flag"
assert_output_contains "$output" "expected" "output check"
\```

### Results
PASS: N  FAIL: 0

### Categories
- [x] Normal Path: PASS
- [x] Boundary: PASS
- [x] Security: PASS
- [x] Concurrency: PASS (or N/A)
```

## Integration with Three-Cycle Review

```
Code Change -> Step 0: Syntax Pre-Check -> Cycle 1 (coder/expert/adversarial)
  -> Any finding? -> Fix -> Cycle 1 restart
  -> Zero findings? -> Cycle 2 (repeat 3 passes)
  -> Any finding? -> Fix -> Cycle 1 restart
  -> Zero findings? -> Cycle 3 (repeat 3 passes)
  -> Any finding? -> Fix -> Cycle 1 restart
  -> Zero findings? -> Step 4: Smoke Test (THIS SKILL)
  -> Any FAIL? -> Fix -> Step 0 restart
  -> All PASS? -> Commit with # post-review-c3
```

## Common Pitfalls

1. **Skipping Act and going straight to Assert**: primitives need captured state  --  always `run_and_capture` first
2. **Checking zombie AFTER wait**: `assert_no_zombie` must run before `concurrent_wait` reaps processes
3. **Using `$?` instead of `$SMOKE_LAST_STATUS`**: `$?` changes on every command; `$SMOKE_LAST_STATUS` is stable
4. **Not sourcing primitives.sh**: every test script must start with `source primitives.sh`
5. **Writing custom assertion libraries for Python/Go/C**: use the language's standard test framework (pytest, go test). Shell is the only language that needs `primitives.sh`

## code-forge smoke-run: Recording Verified Receipts

`code-forge smoke-run` records a machine-verifiable receipt after running a smoke
test command. The receipt is keyed by diff content-hash, so it is invalidated
when the diff changes.

```bash
code-forge smoke-run [--surface "<surface-name>"] [--target <git-ref>] -- <cmd>
```

**Arguments:**
- `--surface NAME`: runtime surface label (default: `default`). Use descriptive
  names matching the LLM-enumerated surfaces (e.g. `nftables`, `systemd`, `socket`).
  Case-insensitive substring match: `nftables` matches `nftables-rules`.
- `--target REF`: git ref for diff-hash keying (default: `HEAD`).
- `-- CMD [ARGS...]`: the command to execute. The `--` separator is optional.

**UNVERIFIED reporting contract (D-09):**
When no valid receipt exists for the current diff, the RUNTIME advisory axis
reports the surface as NOT VERIFIED. Silence never reads as verified -- the
Smoke Status line always prints on stderr after every review run.

**Examples:**
```bash
# Record that nftables rules load correctly after the change
code-forge smoke-run --surface nftables -- nft -f rules.nft

# Record that the systemd unit file passes validation
code-forge smoke-run --surface systemd -- systemctl --no-pager cat myservice.service

# Record that the test suite passes (surface: pytest)
code-forge smoke-run --surface pytest -- pytest tests/ -x -q

# Default surface (no --surface flag)
code-forge smoke-run -- ./scripts/integration-test.sh
```

**Receipt location:** `.code-forge/smoke-receipts/smoke-receipt-<surface>.json`

**Status values:**
- `VERIFIED`: command exited with code 0
- `FAILED`: command exited with non-zero code (receipt still written; RUNTIME axis reports UNVERIFIED)

## References

- `test-library/shell/primitives.sh`  --  15 assertion primitives + 3 helper functions
- `test-library/shell/primitives_test.sh`  --  self-tests for every primitive
- `test-library/shell/README.md`  --  decision table, safety guide, combination rules, dependencies
- `references/injection-payloads.md`  --  common injection attack vectors
- `references/boundary-cases.md`  --  edge cases by data type
- `references/concurrency-patterns.md`  --  race condition test scenarios
