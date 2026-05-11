# Shell Assertion Footguns

Shell-specific bugs that evade `bash -n` and `shellcheck` when writing assertion/test functions. All five hit during the development of this library across multiple rounds of multi-model review.

## The Five Footguns

1. **Bash auto-reaps direct children**  --  `(cmd) & wait` leaves no zombie process. A non-bash intermediate (python3 `subprocess.Popen`, perl `fork`) is required to produce zombies for detection testing.

2. **`local` only valid inside functions**  --  Using `local` at script top level or inside `if`/`for` blocks without a wrapping function causes "local: can only be used in a function" on bash 4.x.

3. **`((x++))` returns old value**  --  Post-increment expression evaluates to 0 (0 = false in bash arithmetic context), so `cmd && ((PASS++)) || ((FAIL++))` executes both branches. Use explicit `if`/`else` instead.

4. **`$(...)` strips trailing newlines**  --  When an assertion function echoes debug lines (ZOMBIE/PASS/FAIL), prefix matching `[[ "$result" == PASS:* ]]` fails on multi-line output. Use `grep -q '^PASS:'` or redirect debug output to stderr.

5. **`jq -e` prints to stdout**  --  `jq -e '.a == 1'` outputs `true`/`false` to stdout, which pollutes `$(capture)`. Always add `>/dev/null 2>&1` when only exit status matters.

## Why Static Analysis Misses Them

Each footgun is syntactically valid:
- Runtime behaviors (auto-reap, arithmetic return values) are invisible to parsers
- Scope rules (`local`) are context-dependent, not syntactic errors
- Data-dependent behavior (`$(...)` newline stripping, `jq -e` output) varies by input

## Recommendation

When writing or reviewing shell assertion/test code, explicitly check for these five patterns. Add test cases for multi-line output capture and arithmetic return-value side effects.
