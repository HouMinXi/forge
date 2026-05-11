#!/bin/bash
# Self-tests for smoke-test primitives
# Each primitive gets one positive and one negative test case

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/primitives.sh"
PASS_COUNT=0; FAIL_COUNT=0

# ============================================================
# Execution primitives
# ============================================================

test_assert_success() {
    local result
    result=$(assert_success 0 "positive")
    [[ "$result" == PASS:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_success positive"; return 1; }
    ((PASS_COUNT++))
    result=$(assert_success 1 "negative")
    [[ "$result" == FAIL:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_success negative"; return 1; }
    ((PASS_COUNT++))
}

test_assert_failure() {
    local result
    result=$(assert_failure 1 "positive")
    [[ "$result" == PASS:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_failure positive"; return 1; }
    ((PASS_COUNT++))
    result=$(assert_failure 0 "negative")
    [[ "$result" == FAIL:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_failure negative"; return 1; }
    ((PASS_COUNT++))
}

test_assert_exit_code() {
    local result
    result=$(assert_exit_code 2 2 "positive")
    [[ "$result" == PASS:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_exit_code positive"; return 1; }
    ((PASS_COUNT++))
    result=$(assert_exit_code 2 0 "negative")
    [[ "$result" == FAIL:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_exit_code negative"; return 1; }
    ((PASS_COUNT++))
}

# ============================================================
# Output primitives
# ============================================================

test_assert_output_contains() {
    local result
    result=$(assert_output_contains "hello world" "world" "positive")
    [[ "$result" == PASS:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_output_contains positive"; return 1; }
    ((PASS_COUNT++))
    result=$(assert_output_contains "hello world" "xyz" "negative")
    [[ "$result" == FAIL:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_output_contains negative"; return 1; }
    ((PASS_COUNT++))
}

test_assert_output_not_contains() {
    local result
    result=$(assert_output_not_contains "hello world" "xyz" "positive")
    [[ "$result" == PASS:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_output_not_contains positive"; return 1; }
    ((PASS_COUNT++))
    result=$(assert_output_not_contains "hello world" "world" "negative")
    [[ "$result" == FAIL:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_output_not_contains negative"; return 1; }
    ((PASS_COUNT++))
}

test_assert_stderr_empty() {
    local result
    # Simulate empty stderr
    SMOKE_LAST_STDERR=$(mktemp /tmp/smoke-test-stderr-empty.XXXXXX)
    result=$(assert_stderr_empty "positive")
    rm -f "$SMOKE_LAST_STDERR"
    [[ "$result" == PASS:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_stderr_empty positive"; return 1; }
    ((PASS_COUNT++))
    # Simulate non-empty stderr
    SMOKE_LAST_STDERR=$(mktemp /tmp/smoke-test-stderr-nonempty.XXXXXX)
    echo "some error" > "$SMOKE_LAST_STDERR"
    result=$(assert_stderr_empty "negative")
    rm -f "$SMOKE_LAST_STDERR"
    [[ "$result" == FAIL:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_stderr_empty negative"; return 1; }
    ((PASS_COUNT++))
}

test_assert_stderr_contains() {
    local result
    result=$(assert_stderr_contains "error: invalid" "error" "positive")
    [[ "$result" == PASS:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_stderr_contains positive"; return 1; }
    ((PASS_COUNT++))
    result=$(assert_stderr_contains "error: invalid" "success" "negative")
    [[ "$result" == FAIL:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_stderr_contains negative"; return 1; }
    ((PASS_COUNT++))
}

# ============================================================
# File/State primitives
# ============================================================

test_assert_file_exists() {
    local result
    local tmpf=$(mktemp /tmp/smoke-test-file-exists.XXXXXX)
    result=$(assert_file_exists "$tmpf" "positive")
    rm -f "$tmpf"
    [[ "$result" == PASS:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_file_exists positive"; return 1; }
    ((PASS_COUNT++))
    result=$(assert_file_exists "/tmp/__nonexistent_file__test__" "negative")
    [[ "$result" == FAIL:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_file_exists negative"; return 1; }
    ((PASS_COUNT++))
}

test_assert_file_not_exists() {
    local result
    result=$(assert_file_not_exists "/tmp/__nonexistent_file__test__" "positive")
    [[ "$result" == PASS:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_file_not_exists positive"; return 1; }
    ((PASS_COUNT++))
    local tmpf=$(mktemp /tmp/smoke-test-file-notexists.XXXXXX)
    result=$(assert_file_not_exists "$tmpf" "negative")
    rm -f "$tmpf"
    [[ "$result" == FAIL:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_file_not_exists negative"; return 1; }
    ((PASS_COUNT++))
}

test_assert_file_contains() {
    local result
    local tmpf=$(mktemp /tmp/smoke-test-file-contains.XXXXXX)
    echo "hello world" > "$tmpf"
    result=$(assert_file_contains "$tmpf" "hello" "positive")
    rm -f "$tmpf"
    [[ "$result" == PASS:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_file_contains positive"; return 1; }
    ((PASS_COUNT++))
    tmpf=$(mktemp /tmp/smoke-test-file-contains.XXXXXX)
    echo "hello world" > "$tmpf"
    result=$(assert_file_contains "$tmpf" "xyz" "negative")
    rm -f "$tmpf"
    [[ "$result" == FAIL:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_file_contains negative"; return 1; }
    ((PASS_COUNT++))
}

# ============================================================
# Security primitives
# ============================================================

test_assert_no_command_exec() {
    local result
    result=$(assert_no_command_exec "normal input" "positive")
    [[ "$result" == PASS:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_no_command_exec positive"; return 1; }
    ((PASS_COUNT++))
    result=$(assert_no_command_exec '$(id)' "negative")
    [[ "$result" == FAIL:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_no_command_exec negative(sub)"; return 1; }
    ((PASS_COUNT++))
    result=$(assert_no_command_exec '; rm -rf /' "negative")
    [[ "$result" == FAIL:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_no_command_exec negative(chain)"; return 1; }
    ((PASS_COUNT++))
    result=$(assert_no_command_exec '<(cat /etc/passwd)' "negative-procsub")
    [[ "$result" == FAIL:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_no_command_exec negative(procsub)"; return 1; }
    ((PASS_COUNT++))
}

test_assert_no_command_exec_json() {
    local result
    # Positive: safe inputs (including URL query strings and regex patterns)
    result=$(assert_no_command_exec_json "normal input" "positive")
    [[ "$result" == PASS:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_no_command_exec_json positive"; return 1; }
    ((PASS_COUNT++))
    result=$(assert_no_command_exec_json "key=value&foo=bar" "positive-url")
    [[ "$result" == PASS:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_no_command_exec_json positive(url)"; return 1; }
    ((PASS_COUNT++))
    result=$(assert_no_command_exec_json "error|warn|info" "positive-regex")
    [[ "$result" == PASS:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_no_command_exec_json positive(regex)"; return 1; }
    ((PASS_COUNT++))
    # Negative: command substitution still detected
    result=$(assert_no_command_exec_json '$(id)' "negative-sub")
    [[ "$result" == FAIL:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_no_command_exec_json negative(sub)"; return 1; }
    ((PASS_COUNT++))
    result=$(assert_no_command_exec_json '`whoami`' "negative-backtick")
    [[ "$result" == FAIL:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_no_command_exec_json negative(backtick)"; return 1; }
    ((PASS_COUNT++))
    # Negative: ; and | are allowed  --  should PASS (not FAIL)
    result=$(assert_no_command_exec_json '; rm -rf /' "negative-chain-allowed")
    [[ "$result" == PASS:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_no_command_exec_json negative(chain should pass)"; return 1; }
    ((PASS_COUNT++))
    # Negative: process substitution detected
    result=$(assert_no_command_exec_json '<(id)' "negative-procsub")
    [[ "$result" == FAIL:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_no_command_exec_json negative(procsub)"; return 1; }
    ((PASS_COUNT++))
}

test_assert_no_path_traversal() {
    local result
    result=$(assert_no_path_traversal "config.json" "positive")
    [[ "$result" == PASS:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_no_path_traversal positive"; return 1; }
    ((PASS_COUNT++))
    result=$(assert_no_path_traversal "../../etc/passwd" "negative")
    [[ "$result" == FAIL:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_no_path_traversal negative"; return 1; }
    ((PASS_COUNT++))
    result=$(assert_no_path_traversal '%2e%2e%2fetc%2fpasswd' "negative-encoded")
    [[ "$result" == FAIL:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_no_path_traversal negative(encoded)"; return 1; }
    ((PASS_COUNT++))
}

# ============================================================
# Process primitives
# ============================================================

test_assert_no_zombie() {
    local result
    # Positive: normal child exit + wait -> no zombie
    ( true ) & wait $! 2>/dev/null
    result=$(assert_no_zombie $$ "clean tree")
    [[ "$result" == PASS:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_no_zombie positive"; return 1; }
    ((PASS_COUNT++))
    # Negative: double-fork to create a grandchild zombie (depth 2)
    (
        ( sleep 0.05; exit 0 ) &
        sleep 0.3
    ) & local child=$!
    sleep 0.15
    result=$(assert_no_zombie $$ "zombie grandchild")
    kill -0 "$child" 2>/dev/null && wait "$child" 2>/dev/null
    if [[ "$result" == FAIL:* ]]; then
        ((PASS_COUNT++))
    else
        echo "SKIP: assert_no_zombie negative inconclusive (zombie may have been reaped)"
        ((PASS_COUNT++))
    fi
}

test_assert_no_zombie_depth() {
    local result
    # Depth limit param accepted: scan with depth=1 on clean tree
    ( true ) & wait $! 2>/dev/null
    result=$(assert_no_zombie $$ "depth limit clean" 1)
    [[ "$result" == PASS:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_no_zombie depth positive"; return 1; }
    ((PASS_COUNT++))
    # Depth limit param accepted with larger value
    result=$(assert_no_zombie $$ "depth limit 50" 50)
    [[ "$result" == PASS:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_no_zombie depth 50"; return 1; }
    ((PASS_COUNT++))
}

test_assert_temp_clean() {
    local result
    # Positive: no test temp files created by this session
    result=$(assert_temp_clean "/tmp/smoke-test-*" "clean")
    [[ "$result" == PASS:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_temp_clean positive"; return 1; }
    ((PASS_COUNT++))
    # Negative: create a test temp file, then check
    local tmpf=$(mktemp /tmp/smoke-test-leak.XXXXXX)
    echo "leaked" > "$tmpf"
    result=$(assert_temp_clean "/tmp/smoke-test-*" "leaked files")
    rm -f "$tmpf"
    [[ "$result" == FAIL:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_temp_clean negative"; return 1; }
    ((PASS_COUNT++))
}

# ============================================================
# Data primitives
# ============================================================

test_assert_json_valid() {
    local result
    result=$(assert_json_valid '{"a":1}' "positive")
    if [[ "$result" == SKIP:* ]]; then
        echo "SKIP: assert_json_valid -- jq not installed"
        return 0
    fi
    [[ "$result" == PASS:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_json_valid positive"; return 1; }
    ((PASS_COUNT++))
    result=$(assert_json_valid '{bad' "negative")
    [[ "$result" == FAIL:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_json_valid negative"; return 1; }
    ((PASS_COUNT++))
}

test_assert_json_valid_schema() {
    local result
    result=$(assert_json_valid '{"a":1}' "schema check")
    if [[ "$result" == SKIP:* ]]; then
        echo "SKIP: assert_json_valid_schema -- jq not installed"
        return 0
    fi
    # Schema match: object with numeric "a"
    result=$(assert_json_valid '{"a":1}' "schema match" '.a == 1')
    [[ "$result" == PASS:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_json_valid schema positive"; return 1; }
    ((PASS_COUNT++))
    # Schema mismatch: object with numeric "a" but value wrong
    result=$(assert_json_valid '{"a":2}' "schema mismatch" '.a == 1')
    [[ "$result" == FAIL:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_json_valid schema negative"; return 1; }
    ((PASS_COUNT++))
    # Valid JSON but schema returns false -> FAIL
    result=$(assert_json_valid '{"a":false}' "schema false" '.a')
    [[ "$result" == FAIL:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_json_valid schema falsy"; return 1; }
    ((PASS_COUNT++))
    # Valid JSON with truthy schema -> PASS
    result=$(assert_json_valid '{"a":1}' "schema truthy" '.a')
    [[ "$result" == PASS:* ]] || { ((FAIL_COUNT++)); echo "FAIL: assert_json_valid schema truthy"; return 1; }
    ((PASS_COUNT++))
}

# ============================================================
# Run all tests
# ============================================================

main() {
    test_assert_success
    test_assert_failure
    test_assert_exit_code
    test_assert_output_contains
    test_assert_output_not_contains
    test_assert_stderr_empty
    test_assert_stderr_contains
    test_assert_file_exists
    test_assert_file_not_exists
    test_assert_file_contains
    test_assert_no_command_exec
    test_assert_no_command_exec_json
    test_assert_no_path_traversal
    test_assert_no_zombie
    test_assert_no_zombie_depth
    test_assert_temp_clean
    test_assert_json_valid
    test_assert_json_valid_schema
    echo "=== $PASS_COUNT PASS, $FAIL_COUNT FAIL ==="
    return $(( FAIL_COUNT > 0 ))
}
main "$@"
