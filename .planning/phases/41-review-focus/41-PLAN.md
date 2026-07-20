# Phase 41: Review focus -- design-intent header + review-focus emphasis param + git-blame date

## Goal

Improve review prompt quality: rename the contract header to design-intent (in ALL 3
prompt builders), add a review-focus emphasis mechanism at parity with --contract
(P4, folded 2026-07-20 -- two sources, all 4 review paths, own trust hash), add
committer date to blame attribution, and update existing tests for the new behavior.

## Must-Haves

- "## Contract Reference" renamed to "## Design Intent" in ALL 3 prompt builders
  (cli.py:780, factories.py:281, factories.py:576)
- test_contract_wiring.py + factories prompt tests: all "Contract Reference"
  occurrences updated
- NEW review-focus mechanism (P4) at parity with --contract: gate.yaml `review_focus:`
  (trust-gated) + `--focus FILE` CLI flag + `focus` MCP param merge into a
  "## Review Focus" section on all 3 builders, distinct from the design-intent section
- `review_focus` has its own trust hash: untrusted or post-trust-edited focus is dropped
  with a warning, and dropping it must not break backend loading
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
   Caveat for the third site: factories.py:576 is currently UNREACHABLE in production --
   its only caller (mcp_server.py:765) passes no contract_spec (see 41-CONTEXT D5.7). It
   can therefore only be covered by a direct unit test of the builder, never end-to-end.
   Do not write a test that claims e2e coverage of that path. Task 3g fixes the caller;
   if 3g lands first, an e2e assertion becomes possible -- state which one the test is.

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

### Task 3: Review focus emphasis parameter (P4) -- FULL mechanism

Adds a review-focus mechanism at parity with --contract (see 41-CONTEXT D5.1-D5.7).
Two merged sources (persistent gate.yaml `review_focus:` + per-call `--focus FILE`),
injected as a "## Review Focus" section into ALL 3 builders across ALL 4 review paths,
with its own trust hash, schema entry, and per-path bug-inject proof.

All line numbers below are verified against main @ 8e18aa0 (2026-07-20). They WILL drift
once Task 1 and Task 2 land -- re-grep the anchor symbol before each edit, never trust
the number alone.

**files:** src/code_forge/cli.py, src/code_forge/factories.py,
src/code_forge/mcp_server.py, src/code_forge/cross_repo.py, src/code_forge/trust.py,
src/code_forge/gate.schema.json, src/code_forge/init_template.py,
tests/test_factories.py, tests/test_trust.py, tests/test_schema_corpus.py
(+ the CLI and MCP test modules -- confirm exact module names by grep before writing)

**action:**

**3a. Merge helper** -- new `_merge_focus_spec(yaml_focus: str, file_content: str,
warn_fn) -> str` in cli.py, placed next to `_merge_contract_spec` (cli.py:1828).
Mirrors its shape but is deliberately simpler (D5.2): concatenate yaml_focus then
file_content; NO LLM summarization (summarizing focus areas destroys the specific areas,
which is the whole feature); NO "## Do NOT Flag" split; NO confirmation-bias directive
(both are contract-specific). Size guard: if the merged string exceeds 8192 bytes, call
warn_fn once and pass the text through UN-truncated -- silently dropping focus areas is
worse than a warned-large prompt. Empty + empty returns "".

**3b. Trust (D5.6)** -- in trust.py, mirroring the contracts pattern at trust.py:243-286:
- `hash_focus_text(gate_data: Optional[dict]) -> str`: sha256 of the canonical JSON of
  the `review_focus` value; returns "" when the field is absent or empty (this is what
  keeps every existing trust record valid -- see migration note below).
- `is_trusted_focus(gate_yaml_path: Path, gate_data: dict) -> bool`: True when the
  stored entry's `focus_hash` equals `hash_focus_text(gate_data)`. A `review_focus` that
  hashes to "" (absent/empty) is trivially trusted -- there is nothing to inject.
- `record_trust` (trust.py:161) additionally writes `focus_hash` alongside the existing
  `hash` key, in the SAME store entry keyed by the gate.yaml path. One `code-forge trust`
  run authorizes both.
- Do NOT touch `hash_backends_block` (24 call sites; conflates credential trust with
  prompt trust -- rejected in D5.6).

**3c. gate.yaml source read** -- the review path already holds `gate_data`; do not add a
new read. Extract `review_focus` from the dict returned by `_load_gate_backends`
(cli.py:118, which already returns `{}` for an untrusted repo) and gate it:
```
yaml_focus = ""
raw = gate_data.get("review_focus", "")
if isinstance(raw, str) and raw.strip():
    if is_trusted_focus(gate_yaml_path, gate_data):
        yaml_focus = raw
    else:
        warn("gate.yaml review_focus ignored: not trusted. Run 'code-forge trust'.")
```
A non-string `review_focus` (list/dict from a hand-edited file) is ignored with a
warning, never coerced -- `str(["a"])` would inject Python repr into the prompt.
Same extraction on the MCP side, where `gate_data` is already in hand at
mcp_server.py:243 and mcp_server.py:292.

**3d. CLI flag** -- add `--focus FILE` next to `--contract` (cli.py:350-355), same
FILE/stdin convention (`-` = stdin), reusing `_load_contract_file`'s read path as
`_load_focus_file`. Then `focus_spec = _merge_focus_spec(yaml_focus, file_content, warn)`
at the two existing merge sites, mirroring `_contract_spec_c` (cli.py:2063) and
`_contract_spec_a` (cli.py:2422).

**3e. Builder injection** -- add `focus_spec: str = ""` to each builder and inject
immediately AFTER the design-intent block that Task 1 renames:
```python
if focus_spec:
    prompt += (
        "\n## Review Focus\n" + focus_spec
        + "\nPrioritize findings in these areas; in your response, "
        + "state whether each area was checked.\n"
    )
```
| Builder | def | param | inject after | call site(s) to pass focus_spec |
|---|---|---|---|---|
| `_make_subagent_spawn` (outlet_c) | cli.py:730 | cli.py:731 | cli.py:778-782 | cli.py:2069 |
| `build_l1_provider` (outlet_a + cross-repo) | factories.py:202 | factories.py:209 | factories.py:279-283 | cli.py:2480, cross_repo.py:307 |
| `build_sampling_l1_provider` (MCP sampling) | factories.py:507 | factories.py:514 | factories.py:575-576 | mcp_server.py:765 |

**3f. MCP param** -- add `focus: str = ""` to forge_review (next to `contract`,
mcp_server.py:890). Two outlets, both required (D5.5):
- CLI-subprocess: mirror the contract temp-file wiring (mcp_server.py:936-943) -- write
  focus to a temp .md, `cli_args.extend(["--focus", tmp_path])`, clean up in the same
  place the contract temp file is cleaned.
- Sampling: pass `focus_spec=focus` into `build_sampling_l1_provider` (mcp_server.py:765).

**3g. Pre-existing bug, SEPARATE commit (D5.7)** -- that same call site passes no
`contract_spec` today, so `--contract` is already a silent no-op on the MCP sampling
outlet and factories.py:576 is unreachable in production. Wiring focus there while
contract stays broken reproduces D5.5's own failure mode. Fix it in its own commit,
before the focus commit, with a message explaining the gap. Do NOT fold it into the
focus change.

**3h. Schema + template (D5.3)** -- add `review_focus: {"type": "string"}` to
gate.schema.json properties; add a documented commented-out `review_focus:` entry to
GATE_YAML_TEMPLATE (init_template.py) noting that changing it requires re-running
`code-forge trust`; add a `review_focus` case to the tests/test_schema_corpus.py valid
corpus so the schema and the real loader cannot drift.

**3i. Tests** -- every row below is required; a missing row is an un-wired path that
ships as a silent no-op:
- Per builder x2: prompt CONTAINS "## Review Focus" and the focus text when focus_spec is
  non-empty; ABSENT when focus_spec == "" (3 builders x 2 = 6 minimum).
- `_merge_focus_spec`: yaml-only, file-only, both (order: yaml then file), both empty
  -> ""; >8KB warns exactly once AND returns the full untruncated text (assert length,
  not just the warning -- a truncating implementation would otherwise pass).
- Trust: trusted focus injects; post-trust edit of `review_focus` -> `is_trusted_focus`
  False -> focus dropped with warning BUT backends still load (the independent-failure-
  domain property of D5.6); absent/empty review_focus keeps an existing trust record
  valid (the migration guarantee -- assert an existing record still verifies).
- Untrusted repo: `_load_gate_backends` returns `{}` -> no focus injected.
- Non-string `review_focus` (list, dict, int) -> ignored with warning, never coerced.
- MCP: focus writes a temp file and adds `--focus <path>` to cli_args (CLI outlet); focus
  reaches the sampling builder (sampling outlet).
- Schema corpus: `review_focus` snippet passes both jsonschema and the real loader.
- Bug-inject (Golden Rule 2), PER PATH: delete ONE builder's focus-injection block ->
  that builder's test FAILS -> restore -> PASSES; repeat for all 3. Then invert
  `is_trusted_focus` to always-True -> the post-trust-edit test FAILS -> restore ->
  PASSES. A single inject proves one path, not the mechanism.

**verify:** `python3 -B -m pytest tests/test_factories.py tests/test_trust.py
tests/test_schema_corpus.py -v` plus the CLI and MCP test modules holding the new
flag/param tests.

**done:** gate.yaml `review_focus` (trust-gated) and `--focus`/MCP `focus` (per-call)
merge into one "## Review Focus" section injected on all 3 builders across all 4 review
paths; untrusted and post-trust-edited focus is dropped with a warning while backends
keep working; existing trust records survive; per-path bug-inject verified; empty focus
is a no-op; the sampling contract_spec gap is fixed in its own commit.
Efficacy (does the model actually emphasize?) stays an ASSUMPTION closed later by a
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
- "## Review Focus" appears when gate.yaml `review_focus`, --focus (CLI), or focus (MCP)
  is provided; absent when none is; both sources merge (yaml then file)
- focus wired on all 3 builders and all 4 review paths (outlet_a, outlet_c, cross-repo,
  MCP CLI-subprocess + MCP sampling)
- Untrusted repo injects no focus; a post-trust `review_focus` edit drops focus with a
  warning while backends keep loading; a repo without `review_focus` keeps its existing
  trust record valid
- Non-string `review_focus` is ignored with a warning, never coerced into the prompt
- gate.schema.json documents `review_focus`; the schema-corpus test covers it; the init
  template documents it including the re-trust requirement
- MCP sampling outlet passes contract_spec (pre-existing gap, separate commit)
- git_blame() returns "date" key (UTC ISO YYYY-MM-DD)
- Blame attribution includes date
- test_legacy.py updated for date field + degradation tests
- Per-path bug-inject proof: header rename (3 sites) + focus injection (3 builders)
  + trust check (invert is_trusted_focus -> the post-trust-edit test must fail)
- Full test suite passes with zero regressions

## Depends on

- Phase 40 (merged 25b063e) -- nominal queue order only. Phase 40's landed code touched
  state.py / sarif.py / receipt.py / outlet_c.py, NOT the cli.py / factories.py prompt
  builders Phase 41 edits -- no file-level conflict; the phases are independent at the
  code level (verified 2026-07-20).
