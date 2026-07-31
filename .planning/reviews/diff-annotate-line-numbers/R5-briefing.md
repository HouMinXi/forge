# Delivery Briefing: diff-annotate-line-numbers

Dispatch: `.planning/dispatch/dispatch_prompt_line_numbers_20260730.txt`
Branch: `diff-annotate-line-numbers`
Worktree: `.worktrees/diff-annotate`

## Answers to (a), (b), (c)

### (a) Annotation format

```
[+  82] +added line       (added: bracket tag + space + content)
[   79]  context line      (context: bracket tag + content)
[----] -removed line       (removed: bracket tag + content)
[    ]  special marker     (e.g. "\ No newline at end of file")
```

Deleted lines show `[----]` because they have NO post-image line number
(unidiff returns `target_line_no=None`). Showing a number would be a lie.
The model still sees `-` so it knows the line was removed.

### (b) Which sites

**4 sites annotated** (output carries `code_excerpts[].start_line`):
- `factories.py:291` -- L1 passes
- `factories.py:591` -- sampling L1 passes
- `cli.py:795` -- spawned L1 passes
- `cli.py:883` -- test-assertion pass

**5 sites deliberately skipped:**
- `cli.py:2088` -- canary mutation, `"line"` is snippet-relative
- `runtime.py:65` -- RUNTIME axis, no code_excerpts output
- `daemon_state.py:55, :74` -- DAEMON-STATE, no line numbers in output
- `canary_gen.py:347` -- sends `modified_diff`, NOT original (TRAP)

Single shared helper `annotate_diff_lines()` in `src/code_forge/diff.py`.

### (c) Token cost

Measured on staged diff (4 files, 6325 bytes original):
- Annotated: 7086 bytes
- Overhead: +12%

## Forge review history

| Round | Verdict | Findings | Action |
|-------|---------|----------|--------|
| R1 | PASS | 3 warnings | Fixed None guard + file headers |
| R2 | FAIL | 6 (3 L0, 3 L1) | Fixed f-strings, rename handling, test specificity |
| R3 | FAIL | 6 (3 L0, 3 L1) | Fixed ambiguous `l` var, `/dev/null` headers, dedup hunk loop |
| R4 | PASS | 3 warnings | Fixed loop optimization + file mode extraction |
| R5 | PASS | 5 warnings | Fixed `_extract_file_mode` search direction; 3 prefix warnings = false positive |

**False positive: duplicate prefix.** Verified with unidiff that `line.value`
does NOT include the `+`/`-`/` ` prefix. Tests confirm output is correct:
`[+   2] +new line` not `[+   2] ++new line`.

## Files changed

| File | Lines | What |
|------|-------|------|
| `src/code_forge/diff.py` | +120 | `annotate_diff_lines()` + `_annotate_hunks()` + `_extract_file_mode()` |
| `src/code_forge/factories.py` | +8/-2 | 2 sites: annotate diff before prompt |
| `src/code_forge/cli.py` | +6/-2 | 2 sites: annotate diff before prompt |
| `tests/test_diff.py` | +141 | 12 new tests in `TestAnnotateDiffLines` |

## Evidence obligations

| # | Obligation | Status |
|---|------------|--------|
| a | Injection test (FAIL before, PASS after) | Not measured -- requires full review run with receipts |
| b | Excerpt accuracy before/after | Not measured -- same reason |
| c | `code-forge verify` end-to-end | Not run -- needs 9 receipts |
| d | Token cost | +12% measured |
| e | Step 0 | py_compile OK, ruff clean, non-ASCII clean, 2994 tests (floor 2978) |

## What was NOT done

- **Not committed.** Pre-commit hook requires 9 receipts; MCP review is
  pinned to CI mode (1 round only). Awaiting main session.
- **Excerpt accuracy not measured.** Requires a full LOCAL-mode review
  generating receipts with the annotated diff, then comparing excerpts
  against real files. The before-number from PM's run was 14/17.
- **`code-forge verify` not run.** Depends on receipts from (b).
