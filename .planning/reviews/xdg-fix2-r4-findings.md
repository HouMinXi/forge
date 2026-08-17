# xdg-fix2 R4 review -- findings and disposition (2026-08-14)

Backend: deepseek-direct. Diff: working tree on fix/xdg-backend-fallback2
vs main (P2b set, post-P1). Result: FAIL, 6 confirmed findings + advisory
items, all fixed and bug-injected.

## Confirmed findings and fixes

### A. Continuation dual-track returns the weaker value (machine.py)

`_continuation_round_index` returned `max_cycle_this_diff` when that track
was non-empty, ignoring a foreign diff's higher cycle on disk. Diff A
writing cycles 1-2, diff B cycle 3, then A re-run: resuming A's own
sequence (3) overwrites B's receipt-c3p*.json, since filenames carry no
diff identity. Confirmed by 3 review votes.

Fix: `return max(max_cycle_this_diff, max_cycle_any_diff)` with a comment
stating the collision argument. Tests: test_machine_ci.py renamed
`test_changed_hash_restarts_at_zero` ->
`test_changed_hash_continues_past_the_highest_cycle_on_disk`, added
`test_same_diff_resumes_above_a_foreign_higher_cycle`. 13/13 pass.
Bug-injection: reverting to the pre-fix return makes both renamed tests
fail (changed hash expects ==1, foreign-cycle expects ==2).

### B. List-content excerpts launder non-string elements (receipt.py)

`_build_excerpts` joined any list with `str(ln)`, turning `None` into the
string "None" -- a fabricated excerpt nobody wrote that passes the
downstream isinstance(content, str) schema gate.

Fix: join only an all-string list; a list with any non-string element
stays a list, which the schema check rejects. Tests:
`test_list_with_non_string_lines_left_unconverted` (content == [1, None,
"x"]). 10/10 pass. Bug-injection: `str(ln)` join restored -> new test
fails.

### C. STEP B accepts excerpt content longer than the declared range
   (verify.py)

An excerpt declaring lines N..N but carrying 2+ lines never compares the
tail against anything -- a fabricated tail rides along unchecked.

Fix: in the STEP B loop, reject when len(content.splitlines()) exceeds
end_line - start_line + 1 (positive declared range only). Tests:
`test_excerpt_content_beyond_the_declared_range_is_rejected` ("declares 1
lines but carries 2"). 137/137 pass. Bug-injection: guard removed -> new
test fails.

### D. STEP C post-image exit (verify.py)

Excerpt lines mapping outside the diff post-image were silently skipped.
Fix rejects with "outside the diff post-image; it cannot be verified".
Test: `test_excerpt_tail_outside_the_post_image_is_rejected`.

### E. runtime.py no-backend early return keeps stale last_surfaces
   (expert warning d7a0cfb6446fada8)

The backend=None early return (and the empty-diff / LLM-failure / parse-
failure paths) left the previous run's surfaces behind; daemon_state
reads RuntimeRunner.last_surfaces for cross-axis data and would inject
stale surfaces into its prompt.

Fix: `self.last_surfaces = []` at the top of run(); the successful path
overwrites it with the parsed surfaces. Tests (test_runtime.py,
TestRuntimeRunnerLastSurfaces): cleared_on_empty_diff, cleared_on_llm_
failure, cleared_when_backend_missing. 51/51 pass. Bug-injection: fix
line removed -> the three new tests fail with `['nftables rules'] == []`.

## Test-assertion findings

- canary cite-reverify regex (test_canary_gen.py) made the "[+ N] "
  annotation prefix optional; production always annotates. Tightened to
  mandatory `\[\+\s*\d+\] ` prefix. Experiment: old regex matches a bare
  hunk (True), new one rejects it (False); both accept the annotated
  hunk. 29/29 pass.
- No test covered the _constant_offset single-line guard. Added
  `test_single_line_excerpt_never_reports_misnumbered` (single line
  matching at +1 must fail as content mismatch, not "misnumbered by").
  Bug-injection: the guard alone removed is still caught by the
  `compared >= 2` floor -- removing both makes the test fail with
  "misnumbered by +1 ... actually src/f.py:3", proving it pins the real
  defense. 138/138 pass.
- TestCIContinuation carried a duplicated class docstring and a class-
  local `import json as _json`. Cleaned: module-level `import json`,
  `self._json.dumps` -> `json.dumps`. 13/13 pass.
- test_cli_hold_resume.py imported Path, pytest, MAX_HOLD_CYCLES unused
  (the monkeypatch uses the string "code_forge.cli.MAX_HOLD_CYCLES").
  Removed. 4/4 pass.
- cli.py `_make_subagent_spawn` docstring claimed "or None for default";
  backend resolution happens in _run with credentials check before
  dispatch. Docstring corrected to fail-closed semantics.

## Full-suite state after R4 fixes

3286 passed, 9 skipped (605s). ruff clean on all changed files; py_compile
clean; non-ASCII scan clean.
