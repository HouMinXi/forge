# Phase 41: Review focus -- design-intent header + review-focus emphasis param + git-blame date

## Goal

Improve review prompt quality: rename the contract header to design-intent (in ALL 3
prompt builders), add a per-call review-focus emphasis param (P4, folded 2026-07-20),
add committer date to blame attribution, and update existing tests for the new behavior.

## Must-Haves

- "## Contract Reference" renamed to "## Design Intent" in ALL 3 prompt builders
  (cli.py:780, factories.py:281, factories.py:576)
- test_contract_wiring.py + factories prompt tests: all "Contract Reference"
  occurrences updated
- NEW `--focus FILE` CLI flag + `focus` MCP param inject a "## Review Focus"
  section into all 3 builders (P4), distinct from the design-intent section
- git_blame() parses committer-time -> "date" key in blame_entry (UTC)
- Blame attribution includes ISO date (YYYY-MM-DD)
- test_legacy.py updated for date field + untracked degradation
- Full test suite passes with zero regressions

## Tasks

### Task 1: Rename contract header (all 3 builders) + update/extend tests

**files:** src/code_forge/cli.py, src/code_forge/factories.py,
tests/test_contract_wiring.py (+ factories prompt tests)

**action:**

1. Change `"## Contract Reference\n"` to `"## Design Intent\n"` at ALL 3 live sites
   (grep-verified 2026-07-20 against main @ 8e18aa0 -- renaming only cli.py:780, the
   original plan scope, leaves the other two outlets emitting the old header):
   - cli.py:780 (_make_subagent_spawn, outlet_c CLI path)
   - factories.py:281 (build_l1_provider, outlet_a + cross-repo path)
   - factories.py:576 (build_sampling_l1_provider, MCP sampling path)
   Grep first (`grep -rn "Contract Reference" src/`); the count must be 0 after edit.

2. Update tests: `grep -rn "Contract Reference" tests/`, replace ALL occurrences
   (including bare "Contract Reference" in assert messages such as
   "Blast Radius < Contract Reference < Diff") with "Design Intent" via replace_all.

3. Coverage floor (diff-coverage gate): each of the 3 renamed sites needs >=1 test
   asserting the new "## Design Intent" header. test_contract_wiring.py covers the
   cli.py path; verify whether the build_l1_provider and build_sampling_l1_provider
   prompt paths are asserted anywhere -- add a prompt-content assertion for any
   uncovered builder, else the factories renames ship unverified.

4. Bug-inject (Golden Rule 2): revert ONE of the 3 sites to "Contract Reference" ->
   its covering test FAILS -> restore -> PASSES. Repeat PER SITE; a single-site inject
   only proves one of three.

**verify:** `grep -rn "Contract Reference" src/ tests/` returns nothing, then
`python3 -B -m pytest tests/test_contract_wiring.py tests/test_factories.py -v`

**done:** Header renamed at all 3 sites; zero "Contract Reference" left in src+tests;
each site test-covered; per-site bug-inject verified.

---

### Task 2: Add date to git_blame() parser and blame attribution

**files:** src/code_forge/git.py, src/code_forge/legacy.py

**action:**

**2a. git.py parser change** (git.py:358-456):

Step 1: Import datetime at top of git.py:
```python
from datetime import datetime, timezone
```

Step 2: Add block variable init alongside current_block_subject (line 399):
```python
current_block_date: str = ""
```

Step 3: Add elif handler in the parsing chain (between summary and filename):
```python
elif raw_line.startswith("committer-time ") and current_sha not in sha_cache:
    try:
        ts = int(raw_line.split(" ", 1)[1])
        current_block_date = datetime.fromtimestamp(
            ts, tz=timezone.utc
        ).strftime("%Y-%m-%d")
    except (ValueError, OSError, OverflowError):
        pass
```

Step 4: Update fallback dict at line 409 to include `"date": ""`.

Step 5: Update blame_map construction (lines 411-415) to include date:
```python
blame_map[current_final_line] = {
    "sha": current_sha,
    "author": entry.get("author", "unknown"),
    "subject": entry.get("subject", ""),
    "date": entry.get("date", ""),
}
```

Step 6: Add "date" to sha_cache construction (line 450-454):
```python
sha_cache[current_sha] = {
    "sha": current_sha,
    "author": current_block_author,
    "subject": current_block_subject,
    "date": current_block_date,
}
```

Step 7: Reset `current_block_date = ""` in SHA header block (line 432-434),
alongside existing `current_block_author = ""` and `current_block_subject = ""`.

Step 8: Update git_blame() docstring (line 361) from:
`Returns {line_number: {"author": str, "sha": str, "subject": str}}.`
to:
`Returns {line_number: {"author": str, "sha": str, "date": str, "subject": str}}.`

**2b. legacy.py format change** (legacy.py:230-245):

```python
parts = [
    blame_entry.get("author", "unknown"),
    sha[:8],
    blame_entry.get("date", ""),
    blame_entry.get("subject", ""),
]
```

**2c. Test updates** (test_legacy.py + test_git.py):

Update `test_legacy.py:test_attribution_format` (line 261-279):
expected output includes date: `"git-blame: Alice abc12345 2023-11-14 fix: null"`.
Add `"date": "2023-11-14"` to mock blame_entry.

Update `test_git.py:test_git_blame_parses_simple` (line 324-330):
add `"date": "2023-11-14"` to the expected dict (fixture contains
`committer-time 1700000000` which parses to 2023-11-14 UTC).

Update `test_git.py:test_git_blame_dedup_sha` (line 340-347):
add `assert result[1]["date"] == "2023-11-14"` (DEDUP_PORCELAIN
fixture also has committer-time 1700000000).

Update `test_git.py:test_git_blame_staged_line` (line 351-358):
add date assertion (STAGED_PORCELAIN fixture also has committer-time
1700000000).

**verify:** `python3 -B -m pytest tests/test_legacy.py tests/test_git.py -v`

**done:** git_blame() returns "date" key (UTC ISO); ADVISORY findings include date.

---

### Task 3: Review focus emphasis parameter (P4)

Adds a per-call review-focus mechanism distinct from --contract (see 41-CONTEXT D5).
Design: a "## Review Focus" prompt section with imperative wording, driven by a
`--focus FILE` CLI flag and a `focus` MCP param, injected into ALL 3 builders. Scope
reduction vs --contract: NO gate.yaml source, NO merge/summarize helper -- per-call only.

**files:** src/code_forge/cli.py, src/code_forge/factories.py,
src/code_forge/mcp_server.py, tests/test_factories.py (+ CLI + MCP test modules)

**action:**

1. cli.py flag: add `--focus FILE` argparse arg next to `--contract` (cli.py:350-355),
   same FILE/stdin convention (`-` = stdin). Read file/stdin content into a `focus_spec`
   string, reusing the same read path `--contract`'s file_content uses.

2. Thread focus_spec + inject "## Review Focus" into all 3 builders, mirroring the
   existing contract_spec block, placed immediately AFTER the design-intent block:
   ```python
   if focus_spec:
       prompt += (
           "\n## Review Focus\n" + focus_spec
           + "\nPrioritize findings in these areas; in your response, "
           + "state whether each area was checked.\n"
       )
   ```
   - _make_subagent_spawn (cli.py:730): add `focus_spec: str = ""` param; inject after
     the contract block (cli.py:778-782); pass focus_spec at call site cli.py:2067.
   - build_l1_provider (factories.py:202): add `focus_spec: str = ""` param; inject
     after the contract block (factories.py:279-283); pass at the 2 call sites
     cli.py:2475 and cross_repo.py:304.
   - build_sampling_l1_provider (factories.py:507): add `focus_spec: str = ""` param;
     inject after the contract block (factories.py:575-576); pass at call site
     mcp_server.py:765.

3. mcp_server.py: add `focus: str = ""` param to forge_review. Mirror the --contract
   temp-file wiring (mcp_server.py:938-943): if focus, write to a temp .md file and
   `cli_args.extend(["--focus", tmp_path])` for the CLI-subprocess outlet; AND pass
   focus_spec=focus into build_sampling_l1_provider (mcp_server.py:765) for the sampling
   outlet. Clean up the temp file in the same path contract's temp file is cleaned.

4. Tests:
   - Per builder: prompt CONTAINS "## Review Focus" + the focus text when focus_spec is
     non-empty; ABSENT when focus_spec == "" (3 builders x 2 = 6 assertions minimum).
   - MCP: focus param writes a temp file and adds `--focus <path>` to cli_args (CLI
     outlet), and passes focus into the sampling builder (sampling outlet).
   - Bug-inject (Golden Rule 2): delete ONE builder's focus-injection block -> that
     builder's "contains Review Focus" test FAILS -> restore -> PASSES. Repeat PER
     builder; a single inject only proves one path is wired.

**verify:** `python3 -B -m pytest tests/test_factories.py -v` plus the CLI + MCP test
modules that hold the new flag/param tests (confirm module names by grep before running).

**done:** --focus (CLI) and focus (MCP) inject a distinct "## Review Focus" section on
all 3 builders + both MCP outlets; per-builder bug-inject verified; empty focus is a
no-op. Efficacy (does the model actually emphasize?) is an ASSUMPTION closed later by a
real-model smoke, not a unit test (D5 ceiling).

---

### Task 4: Add untracked-file blame degradation test

**files:** tests/test_legacy.py

**action:**

1. Unit test: run LegacyRunner on a diff where blame fails (untracked file).
   Assert ADVISORY findings have "git-blame: unavailable".

2. Unit test: run LegacyRunner on a diff where date is missing from blame_entry.
   Assert blame attribution still works without date (graceful degradation).

**verify:** `python3 -B -m pytest tests/test_legacy.py -v`

**done:** Degradation tests pass.

---

### Task 5: Full suite verification

**files:** None (verification only)

**action:**

1. Run full test suite: `python3 -B -m pytest tests/ -q`
2. Verify no regressions
3. Verify new tests all pass

**verify:** `python3 -B -m pytest tests/ -q`

**done:** Full suite green.

## Acceptance

- "## Design Intent" appears (replacing "## Contract Reference") when --contract
  provided, on ALL 3 outlets (outlet_c CLI, outlet_a/cross-repo, MCP sampling)
- Zero "Contract Reference" left in src/ and tests/
- "## Review Focus" appears when --focus (CLI) or focus (MCP) provided; absent when not
- --focus wired on all 3 builders; focus MCP param wired on both MCP outlets
- git_blame() returns "date" key (UTC ISO YYYY-MM-DD)
- Blame attribution includes date
- test_legacy.py updated for date field + degradation tests
- Per-site bug-inject proof: header rename (3 sites) + focus injection (3 builders)
- Full test suite passes with zero regressions

## Depends on

- Phase 40 (merged 25b063e) -- nominal queue order only. Phase 40's landed code touched
  state.py / sarif.py / receipt.py / outlet_c.py, NOT the cli.py / factories.py prompt
  builders Phase 41 edits -- no file-level conflict; the phases are independent at the
  code level (verified 2026-07-20).
