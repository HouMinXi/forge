#!/bin/bash
# smoke-test primitives  --  reusable assertion functions for shell scripts
# Source this file: source ~/.claude/skills/smoke-test/test-library/shell/primitives.sh

# ============================================================
# Helper: run_and_capture  --  execute command, capture stdout/stderr
# ============================================================
run_and_capture() {
    SMOKE_LAST_STDOUT=$(mktemp /tmp/smoke-fw-stdout.XXXXXX)
    export SMOKE_LAST_STDOUT
    SMOKE_LAST_STDERR=$(mktemp /tmp/smoke-fw-stderr.XXXXXX)
    export SMOKE_LAST_STDERR
    "$@" > "$SMOKE_LAST_STDOUT" 2> "$SMOKE_LAST_STDERR"
    SMOKE_LAST_STATUS=$?
    export SMOKE_LAST_STATUS
    return $SMOKE_LAST_STATUS
}

# ============================================================
# Helper: run_concurrent  --  launch N background instances
# ============================================================
run_concurrent() {
    CONCURRENT_PIDS=()
    CONCURRENT_RESULT_DIR=$(mktemp -d /tmp/smoke-fw-concurrent.XXXXXX)
    export CONCURRENT_RESULT_DIR
    local n=$1; shift
    for ((i=0; i<n; i++)); do
        (
            run_and_capture "$@"
            cp "$SMOKE_LAST_STDOUT" "$CONCURRENT_RESULT_DIR/$i.out"
            cp "$SMOKE_LAST_STDERR" "$CONCURRENT_RESULT_DIR/$i.err"
            echo "$SMOKE_LAST_STATUS" > "$CONCURRENT_RESULT_DIR/$i.status"
        ) &
        CONCURRENT_PIDS+=($!)
    done
}

# ============================================================
# Helper: concurrent_wait  --  reap all background instances
# ============================================================
concurrent_wait() {
    local failed=0
    for pid in "${CONCURRENT_PIDS[@]}"; do
        wait "$pid" || failed=1
    done
    return $failed
}

# ============================================================
# Shared: dump SMOKE_LAST_* debug context on FAIL
# ============================================================
_smoke_dump_context() {
    if [[ -n "$SMOKE_LAST_STDOUT" && -f "$SMOKE_LAST_STDOUT" ]]; then
        echo "  stdout: $(head -5 "$SMOKE_LAST_STDOUT" | sed 's/sk-[A-Za-z0-9]\{20,\}/\*\*\*/g')"
    fi
    if [[ -n "$SMOKE_LAST_STDERR" && -f "$SMOKE_LAST_STDERR" ]]; then
        echo "  stderr: $(head -5 "$SMOKE_LAST_STDERR" | sed 's/sk-[A-Za-z0-9]\{20,\}/\*\*\*/g')"
    fi
}

# ============================================================
# Execution primitives
# ============================================================

assert_success() {
    local status=$1
    local description=${2:-"<command>"}
    if [[ $status -eq 0 ]]; then
        echo "PASS: assert_success $description"
        return 0
    else
        echo "FAIL: assert_success $description -- expected 0, got $status"
        _smoke_dump_context
        return 1
    fi
}

assert_failure() {
    local status=$1
    local description=${2:-"<command>"}
    if [[ $status -ne 0 ]]; then
        echo "PASS: assert_failure $description"
        return 0
    else
        echo "FAIL: assert_failure $description -- expected non-zero, got 0"
        _smoke_dump_context
        return 1
    fi
}

assert_exit_code() {
    local expected=$1
    local actual=$2
    local description=${3:-"<command>"}
    if [[ $actual -eq $expected ]]; then
        echo "PASS: assert_exit_code $expected $description"
        return 0
    else
        echo "FAIL: assert_exit_code $expected $description -- expected $expected, got $actual"
        _smoke_dump_context
        return 1
    fi
}

# ============================================================
# Output primitives
# ============================================================

assert_output_contains() {
    local output=$1
    local pattern=$2
    local description=${3:-"output"}
    if echo "$output" | grep -qF "$pattern" 2>/dev/null; then
        echo "PASS: assert_output_contains '$pattern' $description"
        return 0
    else
        echo "FAIL: assert_output_contains '$pattern' $description -- pattern not found"
        echo "  actual output: $(echo "$output" | head -3)"
        return 1
    fi
}

assert_output_not_contains() {
    local output=$1
    local pattern=$2
    local description=${3:-"output"}
    if echo "$output" | grep -qF "$pattern" 2>/dev/null; then
        echo "FAIL: assert_output_not_contains '$pattern' $description -- pattern found"
        echo "  matched line: $(echo "$output" | grep -F "$pattern" | head -1)"
        return 1
    else
        echo "PASS: assert_output_not_contains '$pattern' $description"
        return 0
    fi
}

assert_stderr_empty() {
    local description=${1:-"stderr"}
    if [[ -n "$SMOKE_LAST_STDERR" && -f "$SMOKE_LAST_STDERR" && -s "$SMOKE_LAST_STDERR" ]]; then
        echo "FAIL: assert_stderr_empty $description -- stderr not empty"
        echo "  stderr: $(head -3 "$SMOKE_LAST_STDERR")"
        return 1
    else
        echo "PASS: assert_stderr_empty $description"
        return 0
    fi
}

assert_stderr_contains() {
    local stderr_output=$1
    local pattern=$2
    local description=${3:-"stderr"}
    if echo "$stderr_output" | grep -qF "$pattern" 2>/dev/null; then
        echo "PASS: assert_stderr_contains '$pattern' $description"
        return 0
    else
        echo "FAIL: assert_stderr_contains '$pattern' $description -- pattern not found in stderr"
        echo "  actual stderr: $(echo "$stderr_output" | head -3)"
        return 1
    fi
}

# ============================================================
# File/State primitives
# ============================================================

assert_file_exists() {
    local file=$1
    local description=${2:-"$file"}
    if [[ -f "$file" ]]; then
        echo "PASS: assert_file_exists $description"
        return 0
    else
        echo "FAIL: assert_file_exists $description -- file not found"
        return 1
    fi
}

assert_file_not_exists() {
    local file=$1
    local description=${2:-"$file"}
    if [[ ! -f "$file" ]]; then
        echo "PASS: assert_file_not_exists $description"
        return 0
    else
        echo "FAIL: assert_file_not_exists $description -- file exists"
        return 1
    fi
}

assert_file_contains() {
    local file=$1
    local pattern=$2
    local description=${3:-"$file"}
    if [[ -f "$file" ]] && grep -qF "$pattern" "$file" 2>/dev/null; then
        echo "PASS: assert_file_contains '$pattern' $description"
        return 0
    else
        echo "FAIL: assert_file_contains '$pattern' $description -- pattern not found in file"
        return 1
    fi
}

# ============================================================
# Security primitives
# ============================================================

assert_no_command_exec() {
    local input=$1
    local context=${2:-"input"}
    local failed=0
    # shellcheck disable=SC2016  # $(), backticks, <() are literal patterns
    if echo "$input" | grep -qE '\$\(.*\)|`[^`]+`|<\(' 2>/dev/null; then
        echo "FAIL: assert_no_command_exec $context -- command execution/substitution detected in: '$input'"
        failed=1
    fi
    if echo "$input" | grep -qE '[;&|]' 2>/dev/null; then
        echo "FAIL: assert_no_command_exec $context -- command chaining/pipe detected in: '$input'"
        failed=1
    fi
    [[ $failed -eq 0 ]] && echo "PASS: assert_no_command_exec $context"
    return $failed
}

# JSON/URL-safe variant: only checks command execution/substitution, skips ;&|
# Use when input legitimately contains & (query strings) or | (regex)
assert_no_command_exec_json() {
    local input=$1
    local context=${2:-"input"}
    # shellcheck disable=SC2016  # $(), backticks, <() are literal patterns
    if echo "$input" | grep -qE '\$\(.*\)|`[^`]+`|<\(' 2>/dev/null; then
        echo "FAIL: assert_no_command_exec_json $context -- command execution/substitution detected in: '$input'"
        return 1
    fi
    echo "PASS: assert_no_command_exec_json $context"
    return 0
}

assert_no_path_traversal() {
    local input=$1
    local context=${2:-"path"}
    local failed=0
    if echo "$input" | grep -qE '\.\.[/\\]' 2>/dev/null; then
        echo "FAIL: assert_no_path_traversal $context -- path traversal detected in: '$input'"
        failed=1
    fi
    if echo "$input" | grep -qiE '%2e%2e[%/]' 2>/dev/null; then
        echo "FAIL: assert_no_path_traversal $context -- encoded path traversal in: '$input'"
        failed=1
    fi
    [[ $failed -eq 0 ]] && echo "PASS: assert_no_path_traversal $context"
    return $failed
}

# ============================================================
# Process primitives
# ============================================================

assert_no_zombie() {
    local root_pid=${1:-$$}
    local description=${2:-"process tree"}
    local max_depth=${3:-100}
    local zombies=0
    _smoke_scan_children() {
        local ppid=$1
        local depth=${2:-0}
        if (( depth > max_depth )); then
            echo "  WARNING: max depth $max_depth reached at PID=$ppid"
            return 0
        fi
        local children_file="/proc/$ppid/task/$ppid/children"
        [[ -f "$children_file" ]] || return 0
        local child
        # shellcheck disable=SC2013  # children file is space-separated PIDs
        for child in $(cat "$children_file" 2>/dev/null); do
            local stat="/proc/$child/stat"
            local raw
            raw=$(cat "$stat" 2>/dev/null) || continue
            local after_comm="${raw#*) }"
            local state="${after_comm%% *}"
            if [[ "$state" == "Z" ]]; then
                ((zombies++))
                echo "  ZOMBIE: PID=$child"
            fi
            _smoke_scan_children "$child" $((depth + 1))
        done
    }
    _smoke_scan_children "$root_pid" 0
    if [[ $zombies -eq 0 ]]; then
        echo "PASS: assert_no_zombie $description"
        return 0
    else
        echo "FAIL: assert_no_zombie $description -- $zombies zombie process(es) in tree"
        return 1
    fi
}

assert_temp_clean() {
    local pattern=${1:-"/tmp/smoke-test-*"}
    local description=${2:-"temp files"}
    local leftovers
    leftovers=$(find /tmp -name "$(basename "$pattern")" \
        -newer /proc/$$/cmdline 2>/dev/null | wc -l)
    if [[ $leftovers -eq 0 ]]; then
        echo "PASS: assert_temp_clean $description"
        return 0
    else
        echo "FAIL: assert_temp_clean $description -- $leftovers leftover file(s)"
        find /tmp -name "$(basename "$pattern")" \
            -newer /proc/$$/cmdline 2>/dev/null | head -5
        return 1
    fi
}

# ============================================================
# Data primitives
# ============================================================

assert_json_valid() {
    local file_or_str=$1
    local description=${2:-"json"}
    local schema=${3:-}
    if ! command -v jq &>/dev/null; then
        echo "SKIP: assert_json_valid $description -- jq not installed"
        return 0
    fi
    if [[ -f "$file_or_str" ]]; then
        jq empty "$file_or_str" 2>/dev/null || {
            echo "FAIL: assert_json_valid $description -- invalid JSON syntax"
            return 1
        }
        if [[ -n "$schema" ]]; then
            jq -e "$schema" "$file_or_str" >/dev/null 2>&1 || {
                echo "FAIL: assert_json_valid $description -- schema mismatch: $schema"
                return 1
            }
        fi
    else
        echo "$file_or_str" | jq empty 2>/dev/null || {
            echo "FAIL: assert_json_valid $description -- invalid JSON syntax"
            return 1
        }
        if [[ -n "$schema" ]]; then
            echo "$file_or_str" | jq -e "$schema" >/dev/null 2>&1 || {
                echo "FAIL: assert_json_valid $description -- schema mismatch: $schema"
                return 1
            }
        fi
    fi
    echo "PASS: assert_json_valid $description"
    return 0
}
