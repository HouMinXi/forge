# Phase 46: doctor: registry-vs-executed tool audit

## Goal
Add a tool-audit check to `forge doctor` that verifies every tool in
the loaded registry actually resolves and runs in the pipeline. Catches
the resolve-command false-green class (Phase 45 S1 disclosure) before
it silently degrades L0.

## Context
Phase 45 revealed that main's `_resolve_command` ran `shutil.which` on
the WHOLE command string, so every registry entry whose command carries
flags (ruff, pylint, cppcheck, pmd, eslint) NEVER resolved. The fix
(first-word extraction, ab0387e) landed, but there is no standing guard
against future regressions of this class. A doctor check that compares
"what the registry declares" vs "what the pipeline actually executes"
closes the gap permanently.

## Review History

### R1 Internal (0B/0H/0M/0L after fixes)
9 findings resolved: bug-inject proof made non-hollow (calls
`capture_tool_version` which wraps `_resolve_command`), `--help`
replaced with `--version`, tools.yaml path explicit, run_doctor
integration specified, relative-path handled by `_resolve_command`,
exception handling added, cargo_root skipped, relative-path test added.

### R2 Gemini (1B/2H/1M/1L, all fixed)
BLOCKER: `ws` vs `workspace` variable name + null check -> wiring
snippet uses `workspace` inside `if ok_ws:` block.
HIGH: ordering error (empty check before load) -> moved after load.
HIGH: timeout=10 -> marked as LOW, audit uses same timeout as pipeline.
MEDIUM: SKIP should return None -> fixed to `Optional[bool]`.
LOW: redundant `_resolve_command` call -> removed, use only
`capture_tool_version`.

### R3 DeepSeek (2B/2H/2M/2L, all fixed below)
See findings table below.

### R4 Internal /plan-review PBR 8-pass (0B/1H/3M/1L, all fixed)
All claims verified against live code (runner.py, doctor.py,
registry.py, workspace.py, test_doctor.py).
HIGH: Task 4 rc==1 not attributable to tool-audit (bare-tools.yaml
project fails on missing gate.yaml alone; deleting has_fail wiring
would stay green) -> rebuilt on the all-PASS recipe from
test_doctor.py:380-393, audit becomes the only FAIL source.
MEDIUM: registry iteration must be `registry.values()` (bare dict
iteration yields name strings); wiring snippet showed bare
`_check_handshake()` while real code assigns `ok_h, msg_h` ->
snippet now mirrors the real line; relative-path test needs
`monkeypatch.chdir(tmp_path)` (isfile is CWD-relative).
LOW: bug-inject mock shape pinned to `return_value=None` for all
commands; tools.yaml missing check pinned to `not tools_yaml.exists()`.
Verified-true claims (no change needed): whitespace command passes
load_registry validation; empty registry returns {} (SKIP reachable);
FileNotFoundError subclass of OSError; lazy-import convention real
(doctor.py:108/124/166); _check_workspace failure returns None
workspace; all-PASS recipe exists at test_doctor.py:380-393.

### R5 External kimi + mimo (deduped 1H/3M/1L, all fixed)
Both models INDEPENDENTLY hit the same HIGH: wiring tool-audit
breaks the existing test_smoke_all_green (its project has no
tools.yaml -> new "SKIP" line -> `assert "SKIP" not in out` at
test_doctor.py:393 fails; the plan had no task updating it).
PM-verified: :393 is the only SKIP assertion in the file. Fix =
new Task 5: give the smoke project a minimal valid tools.yaml
(command: python3) so the audit line is PASS; assertions stay
unweakened. Both models also hit the fixture-format MEDIUM: tests
never specified the tools.yaml structure while load_registry
enforces _REQUIRED_FIELDS (command, output_format, file_patterns)
and output_format must be a PARSER_DISPATCH key -- fix = exact YAML
templates in Task 4 step 1 and Task 5. kimi-only findings, all
confirmed: lazy-import ambiguity between Task 1 steps 1 and 7 (step
7 now says inside _audit_tools, not module level); relative-path
test must create + chmod the script and the bug-inject mock is
pinned to a with-block; stale "flagged commands" wording survived
in Acceptance after R4 fixed the same phrase in Task 2 (mirror now
fixed). mimo explicitly reported 0 additional findings beyond the
two shared hits.

### R6-R9 Executor-side cross-review (lc/gm/ds/mimo, 2026-07-11)
R6 lc (2M/3L, all applied): whitespace-command IndexError -> Task 0;
per-tool fault isolation -> Task 1 step 7d; cargo_root SKIP-line
semantics; `.code-forge/` subdir path in Task 4; lazy imports.
R7 gm (1H/1M/2L): HIGH relative-path false-FAIL when doctor runs
from a subdirectory -> chdir(workspace) wrap in Task 1 step 6;
MEDIUM tool-audit row silently omitted on workspace failure ->
label added to the skip tuple at doctor.py:272; LOW double
"tool-audit:" prefix -> stripped from early-return messages. LOW
ws-vs-workspace naming in Task 4 DISMISSED: the integration test
is newly written code, not a verbatim recipe copy, so the variable
name is the implementer's own binding.
R8 ds (3M/1L): cargo_root branch untested -> Task 2 test 8;
python3-on-PATH assumption -> sys.executable fallback note in
Task 5; negative-only smoke assertions pass vacuously -> positive
`assert "tool-audit:" in out` added to Task 5 step 3. LOW forward
reference (Task 2 uses Task 4's fixture template) KEPT: the
template constraints are restated inline in Task 2.
R8 lc (1L, applied): Blind Spot Audit claimed run_tool is the ONLY
`_resolve_command` caller while `capture_tool_version` is an
existing second caller -> section reworded. R8 mimo: 0/0/0/0
(20 signature/contract cross-references all match source).
R9 gm (1H/1M): HIGH unguarded `os.getcwd()`/`os.chdir()` crashes
run_doctor on deleted-CWD or unenterable workspace, contradicting
step 7d's never-crash invariant -> guarded shape in Task 1 step 6
(PM-applied post-round). MEDIUM shebang-less test script passes
hollowly via ENOEXEC suppression in `capture_tool_version` ->
Task 2 test 4 pins `#!/bin/sh` + version echo.
PM parity audit (post-R9): the pipeline never chdirs (grep src/
clean; run_tool's subprocess.run has no cwd=), so the R7 chdir fix
anchors the audit differently than a subdirectory-invoked review
resolves -> recorded as Known gap #4 rather than reverted (the
audit reports the registry contract, which is
workspace-root-relative by construction).

### R10 Confirmation round (ds/gm/lc in parallel, 2026-07-11)
ds 0/0/0/0: independently re-verified the guarded chdir block on
all four criteria, Known gap #4 with its own grep, the three
workspace-status wiring paths, and load_registry's exception
surface (ValueError wraps yaml errors; OSError covers the rest --
the plan's except tuple is complete).
lc 0/0/0/0: re-verified every ground-truth claim line-by-line,
including no-UnboundLocalError in the guarded chdir (finally only
reachable after the first try completes) and _check_workspace's
(False, msg, None) failure shape.
gm 0/0/0/2L, both applied:
- L1 double SKIP word: _line renders the SKIP tag from ok=None
  (doctor.py:325-330), so a "SKIP (...)" message payload would
  print the word twice on one row -> payloads reworded ("no
  tools.yaml", "no tools configured", "%s: cargo_root"); Task 5's
  quoted output literal aligned to the real rendering. Same
  pattern family as R7's double-label LOW, different instance.
- L2 stale monkeypatch justification: after R7's chdir moved into
  _audit_tools, Task 2 test 4's monkeypatch.chdir would MASK a
  dropped-chdir regression; the test now drives
  _audit_tools(tmp_path) from pytest's default CWD and doubles as
  the chdir regression guard. SUPERSEDES the R4 history line
  "relative-path test needs monkeypatch.chdir(tmp_path)".
R11 gm 0/0/0/0: both L fixes confirmed by the finder; payload
sweep for remaining _line tag duplication found nothing new.
CONVERGED 2026-07-11: ds/lc clean in R10, gm clean in R11 on the
final text. Plan frozen for execution.

## DeepSeek R3 Findings (all resolved)

| # | Severity | Finding | Fix |
|---|----------|---------|-----|
| 1 | BLOCKER | timeout=10 not implemented | Marked LOW: audit uses same timeout as pipeline (`capture_tool_version` timeout=5). Consistent behavior. |
| 2 | BLOCKER | Wiring snippet relocates `_check_handshake` | Corrected: snippet shows insertion point only, `_check_handshake` stays where doctor.py has it (line 307). |
| 3 | HIGH | Bug-inject modifies source, contaminates suite | Use `mock.patch("code_forge.runner._resolve_command")` for targeted commands, not source mutation. |
| 4 | HIGH | `has_fail` is local variable | Use `run_doctor(cwd=ws, env={}) == 1` (matches existing test_doctor.py pattern). |
| 5 | MEDIUM | Stale R1 fix description | Updated R1 #1 to "Call `capture_tool_version` (wraps `_resolve_command`)". |
| 6 | MEDIUM | `bool \| None` vs `Optional[bool]` | Use `Optional[bool]` to match doctor.py convention. |
| 7 | LOW | `UnicodeDecodeError` subclass of `ValueError` | Removed from except tuple. |
| 8 | LOW | DONE condition stale | Updated to "calls `capture_tool_version`". |

## Plan

### Task 0: Harden `_resolve_command` against whitespace commands

**files:** src/code_forge/runner.py
**action:** Add guard at line 51:
```python
if not command or not command.strip():
    return None
```
This prevents `IndexError` when `command` is whitespace-only (e.g.
`"  "` from tools.yaml passes `load_registry` validation but
`"  ".split()` returns `[]`).

**verify:** `python3 -m py_compile src/code_forge/runner.py`
**done:** Whitespace-only commands return None

### Task 1: Add `_audit_tools` to doctor.py

**files:** src/code_forge/doctor.py
**action:**
1. Add function (use lazy imports inside, matching doctor.py convention):
   ```python
   def _audit_tools(workspace: Path) -> list[tuple[Optional[bool], str]]:
   ```
   Returns `Optional[bool]` because doctor's `_line()` expects
   `ok=None` for SKIP tags (doctor.py:326-327).
2. Derive path: `tools_yaml = workspace / ".code-forge" / "tools.yaml"`
3. If `not tools_yaml.exists()`, return
   `[(None, "no tools.yaml")]` -- no "SKIP" word in the message:
   `_line` already renders the SKIP tag from `ok=None`
   (doctor.py:325-330), a "SKIP (...)" payload would print the
   word twice on one row
4. Load registry with try/except:
   ```python
   try:
       registry = load_registry(str(tools_yaml))
   except (OSError, ValueError) as exc:
       return [(False, "tools.yaml error: %s" % exc)]
   ```
5. If registry is empty (no tools configured), return
   `[(None, "no tools configured")]` (same no-"SKIP"-word rule)
6. Wrap the audit loop in `os.chdir(workspace)` with BOTH failure
   paths guarded (doctor must never crash on a bad environment --
   same invariant as step 7d). `_resolve_command` checks
   `os.path.isfile` relative to process CWD, not workspace; the
   chdir anchors relative commands to the workspace root (the
   registry contract -- see Known gaps #4 for the pipeline
   divergence this creates). doctor.py already imports `os` at
   module level (line 10), no new import needed:
   ```python
   try:
       original_cwd = os.getcwd()
       os.chdir(workspace)
   except OSError as exc:
       return [(False, "audit error: CWD failure: %s" % exc)]
   try:
       for tc in registry.values(): ...
   finally:
       try:
           os.chdir(original_cwd)
       except OSError:
           pass
   ```
7. For each `tc` in `registry.values()` (load_registry returns
   `dict[str, ToolConfig]` — iterating the dict bare yields name
   strings, not configs; it already filters enabled=False),
   wrapped in per-tool try/except for fault isolation:
   a. Skip tools with `working_dir == "cargo_root"` -> emit
      `(None, "%s: cargo_root" % tc.name)` (no "SKIP" word; the
      tag comes from `ok=None`)
   b. Call `capture_tool_version(tc.command)` — this function already
      calls `_resolve_command` internally and returns:
      - "not_installed" if binary not found -> report FAIL
      - "unknown" on timeout/error -> report PASS (tool IS installed)
      - version string -> report PASS
   c. Report with tool name in message:
      - FAIL: `"%s: not_installed" % tc.name`
      - PASS: `"%s: %s" % (tc.name, version)`
   d. On any exception: report `(False, "%s: audit error: %s" % (tc.name, exc))`
      and continue to next tool (never crash doctor)
8. Inside `_audit_tools` (lazy, per step 1's convention), import
   `capture_tool_version` from `code_forge.runner` and
   `load_registry` from `code_forge.registry` -- NOT at module level
9. Wire into `run_doctor()` — insert the tool-audit loop after
   the outlet check block, before `_check_handshake`. Use the local
   variable `workspace`. Guard against `workspace is None` (workspace
   resolution failure). Also add `"tool-audit"` to the skip-labels
   tuple at doctor.py:272 so workspace failures emit a SKIP line
   instead of silently omitting the audit row. `_check_handshake()`
   stays at its current position (doctor.py line 307, takes no args):
   ```python
   # ... existing outlet check ends around line 303 ...
   if workspace is not None:
       for ok, msg in _audit_tools(workspace):
           _line("tool-audit", msg, ok)
           if ok is False:
               has_fail = True
   ok_h, msg_h = _check_handshake()  # existing line 307, unchanged
   ```
   Also at line 272:
   ```python
   for label in ("gate.yaml", "trust", "backend", "outlet", "tool-audit"):
       _line(label, "", None)
   ```
   Note: `ok is False` (not `not ok`) because `None` is SKIP, not FAIL.

**verify:** `python3 -m py_compile src/code_forge/doctor.py`
**done:** `_audit_tools` calls `capture_tool_version` (which wraps `_resolve_command`)

### Task 2: Add doctor tool-audit tests

**files:** tests/test_doctor_tool_audit.py (new)
**action:**
1. Test: `capture_tool_version` returns "1.0.0" -> PASS (ok=True)
2. Test: `capture_tool_version` returns "not_installed" -> FAIL (ok=False)
3. Test: `capture_tool_version` returns "unknown" (timeout) -> PASS (ok=True)
4. Test: relative-path tool (command="scripts/checkpatch.pl") -> PASS
   when file exists and is executable. The test CREATES the script:
   write `tmp_path/scripts/checkpatch.pl` with content
   `#!/bin/sh\necho "1.0.0"` and `chmod(0o755)`. Do NOT chdir in
   the test setup: drive `_audit_tools(tmp_path)` from pytest's
   default CWD. The audit's own chdir (Task 1 step 6) must anchor
   resolution to the workspace, so this test doubles as the
   regression guard for that behavior -- adding
   `monkeypatch.chdir(tmp_path)` would mask a dropped-chdir
   regression by making resolution succeed from the test's CWD.
   The shebang ensures `subprocess.run` actually executes (without
   it, ENOEXEC is caught by `capture_tool_version`'s OSError handler
   and returns "unknown" — a hollow PASS via error suppression)
5. Test: tools.yaml missing -> SKIP (ok=None)
6. Test: tools.yaml malformed -> FAIL (ok=False) with error message
7. Test: empty registry (no tools configured) -> SKIP (ok=None)
8. Test: tool with `working_dir: cargo_root` in tools.yaml ->
   SKIP (ok=None) with tool name in message
9. Bug-inject: `with mock.patch("code_forge.runner._resolve_command",
   return_value=None):` (context manager, auto-unpatch) — None for
   ALL commands (simplest shape, sufficient to prove the FAIL path)
   -> `capture_tool_version` returns "not_installed" -> `_audit_tools`
   reports FAIL. Outside the with-block -> PASS. This proves the
   audit catches `_resolve_command` regressions.
10. All tests that need a tools.yaml use the fixture template from
    Task 4 (all three required fields; output_format must be a
    PARSER_DISPATCH key or load_registry rejects the file before the
    audit logic ever runs).

**verify:** `python3 -B -m pytest tests/test_doctor_tool_audit.py -v`
**done:** All tests pass, bug-inject proof verified

### Task 3: Code-review-graph blind spot audit (DONE)

**files:** None (analysis complete, findings in Context section)
**status:** COMPLETE — findings recorded below

### Task 4: Integration test — doctor catches missing tool

**files:** tests/test_doctor_tool_audit.py (append to Task 2 file)
**action:**
1. Build the temp project on the all-PASS recipe from test_doctor.py
   lines 380-393: valid minimal gate.yaml + `DEMO_API_KEY` env +
   patch `_check_handshake`, `_check_registries`, `trust_status` so
   every non-audit check PASSes. Then add `.code-forge/tools.yaml`
   (not bare `tools.yaml` — step 2 reads
   `workspace / ".code-forge" / "tools.yaml"`) with EXACTLY this
   content (all three required fields; output_format must be a
   PARSER_DISPATCH key or load_registry raises ValueError and the
   audit logic never runs):
   ```yaml
   tools:
     bad-tool:
       command: nonexistent-binary-xyz
       output_format: grep_line
       file_patterns: ["*.py"]
   ```
2. Run `run_doctor(cwd=workspace, env=env)`, verify return value == 1.
   With every other check green, the exit code is attributable ONLY
   to the tool-audit FAIL — deleting the `has_fail` wiring flips this
   test red. (A bare-tools.yaml project would return 1 from the
   missing gate.yaml alone, proving nothing about the wiring.)
3. Verify output contains tool-audit FAIL line naming the tool

**verify:** `python3 -B -m pytest tests/test_doctor_tool_audit.py::TestDoctorIntegration -v`
**done:** Integration test passes

### Task 5: Update test_smoke_all_green for the new tool-audit line

**files:** tests/test_doctor.py
**action:**
The all-PASS smoke test (test_doctor.py around lines 365-393) builds
a project with gate.yaml but NO tools.yaml. Once tool-audit is wired
in, doctor prints a "tool-audit:  no tools.yaml  SKIP" row there
(the SKIP tag comes from `_line` with ok=None), and the
existing assertion `assert "SKIP" not in out` (line 393) fails.
Fix by making the smoke project genuinely all-green rather than by
weakening the assertion:
1. In test_smoke_all_green's setup, add `.code-forge/tools.yaml`:
   ```yaml
   tools:
     pyver:
       command: python3
       output_format: grep_line
       file_patterns: ["*.py"]
   ```
   `python3` is always on PATH in the test environment (tests run
   under Python). If the CI uses a different binary name, use
   `sys.executable` instead.
2. Keep BOTH existing assertions unchanged (`"FAIL" not in out`,
   `"SKIP" not in out`).
3. ADD one positive assertion: `assert "tool-audit:" in out` —
   verifies the audit actually ran (negative-only checks pass
   vacuously if the wiring never executes).
4. Touch NO other test in the file.

**verify:** `python3 -B -m pytest tests/test_doctor.py -v`
**done:** test_smoke_all_green passes with assertions unweakened

## Blind Spot Audit (Task 3 findings, complete)

### _resolve_command callers
- `runner.py:run_tool` (line 119) — primary caller.
- `runner.py:capture_tool_version` (line 77) — second existing
  caller (not introduced by this phase).
- After this phase: `_audit_tools` calls `capture_tool_version`,
  which calls `_resolve_command` — gap closed (audit coverage
  flows through the same resolution path as the pipeline).

### run_tool callers
- `machine.py:_default_l0_runner` — calls `run_tools` (plural)
- `runner.py:run_tools` — iterates registry, calls `run_tool` per entry
- No direct subprocess calls bypass `run_tool`

### Known gaps (documented, not in scope)
1. **MCP server binary path**: `mcp_server.py:_run_cli_budgeted` spawns
   `code-forge` directly via PATH, not through `_resolve_command`.
   Correct behavior. Not auditable by this phase.
2. **cargo_root tools**: Skipped in audit (clippy works only inside
   Cargo projects; --version probe would pass but runtime may fail).
3. **Paths with spaces**: `_resolve_command` uses `str.split()`, not
   `shlex.split()`. No current registry entry has spaces in binary
   paths. Documented limitation.
4. **Pipeline does not chdir**: `run_tool` resolves and executes in
   the invoking process CWD (`subprocess.run` at runner.py:141 has
   no `cwd=`; grep finds no `chdir` anywhere in src/). The audit
   anchors relative commands to the workspace root -- the contract
   a relative registry entry encodes -- so a `forge review` invoked
   from a subdirectory would silently skip relative-path tools the
   audit reports PASS (optional tools log-and-skip at
   runner.py:126-129). Divergence documented, not fixed here;
   anchoring the pipeline CWD is its own phase.

## Acceptance
- `forge doctor` reports per-tool PASS/FAIL/SKIP with tool name
- Missing tools set `has_fail=True` (non-zero exit via
  `run_doctor() == 1`). Tools that resolve but fail exec are
  reported PASS (documented limitation: `capture_tool_version`
  returns "unknown" on OSError, which is truthy).
- Bug-inject proof: `mock.patch` on `_resolve_command` returning
  None for all commands -> `capture_tool_version` returns
  "not_installed" -> `_audit_tools` reports FAIL -> doctor exits
  non-zero. Unpatch -> PASS.
- Existing test_smoke_all_green stays green with its assertions
  unweakened (its project gains a minimal tools.yaml).
- Full suite passes
