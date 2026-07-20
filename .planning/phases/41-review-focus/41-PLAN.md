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

**Wave structure:**
- Wave 1: Task 1 (header rename), Task 2 (blame date) -- independent, parallel
- Wave 2: Task 3a, 3b, 3c (review-focus mechanism) -- depends on Task 1 for renamed header
- Wave 3: Task 4 (blame degradation), Task 5 (full suite) -- depends on Wave 1+2

### Task 1: Rename contract header (all 3 builders) + update/extend tests
**Wave:** 1 | **Depends on:** none

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
**Wave:** 1 | **Depends on:** none

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

### Task 3a: Focus mechanism -- merge helper + trust + gate.yaml source + CLI flag
**Wave:** 2 | **Depends on:** Task 1 (header rename must land first for anchor sites)

Adds the core focus plumbing: merge helper, trust hash, gate.yaml extraction, and CLI flag.
Injected spec is NOT yet wired to builders (Task 3b does that).

All line numbers below are verified against main @ 8e18aa0 (2026-07-20). They WILL drift
once Task 1 and Task 2 land -- re-grep the anchor symbol before each edit, never trust
the number alone.

**files:** src/code_forge/cli.py, src/code_forge/trust.py

**action:**

**3a-1. Merge helper** -- new `_merge_focus_spec(yaml_focus: str, file_content: str,
warn_fn) -> str` in cli.py, placed next to `_merge_contract_spec` (cli.py:1828).
Mirrors its shape but is deliberately simpler (D5.2): concatenate yaml_focus then
file_content; NO LLM summarization (summarizing focus areas destroys the specific areas,
which is the whole feature); NO "## Do NOT Flag" split; NO confirmation-bias directive
(both are contract-specific). Size guard: if the merged string exceeds 8192 bytes, call
warn_fn once and pass the text through UN-truncated -- silently dropping focus areas is
worse than a warned-large prompt. Empty + empty returns "".

**3a-2. Trust (D5.6)** -- in trust.py, mirroring the contracts pattern at trust.py:243-286:
- `hash_focus_text(gate_data: Optional[dict]) -> str`: sha256 of the canonical JSON of
  the `review_focus` value; returns "" when the field is absent or empty (this is what
  keeps every existing trust record valid -- see migration note below).
- `is_trusted_focus(gate_yaml_path: Path, gate_data: dict) -> bool`:
  ```
  current = hash_focus_text(gate_data)
  if not current:        # absent or empty -> nothing to authorize
      return True
  entry = store.get(str(gate_yaml_path))
  if entry is None:      # repo never ran trust -> not trusted
      return False
  return entry.get("focus_hash") == current
  ```
  A `review_focus` that hashes to "" (absent/empty) is trivially trusted -- there is
  nothing to inject. The explicit short-circuit before reading the store is REQUIRED:
  pre-Phase-41 records have no `focus_hash` key, so `entry.get("focus_hash")` returns
  None; without the short-circuit, `None != ""` would False-positive on every repo that
  adds `review_focus` without re-running trust. The short-circuit makes the migration
  guarantee work: absent focus = trivially trusted = existing record survives.
- `record_trust` (trust.py:161) additionally writes `focus_hash` alongside the existing
  `hash` key, in the SAME store entry keyed by the gate.yaml path. One `code-forge trust`
  run authorizes both. When `review_focus` is absent or empty, `focus_hash` is written
  as "" (not omitted) so the store entry explicitly records "no focus authorized."
- Migration guarantee: existing trust records (no `focus_hash` key) continue to work for
  backends. Adding `review_focus` to an already-trusted gate.yaml requires a
  **re-run of `code-forge trust`** to authorize the new field -- the plan's earlier
  wording ("nothing is invalidated") was misleading; it applies to backends only, not
  to focus. Document this in the init_template.py comment for `review_focus:`.
- Sampling-only trust (MM #1): `code-forge trust` (cli.py:1169-1180) refuses to record
  trust when `backends` is absent. Extend to accept gate.yaml with EITHER backends OR
  a non-empty `review_focus`:
  ```python
  has_backends = backends_raw and not (
      isinstance(backends_raw, dict) and all(v is None for v in backends_raw.values())
  )
  has_focus = isinstance(gd.get("review_focus"), str) and gd["review_focus"].strip()
  if not has_backends and not has_focus:
      print("No backends or review_focus configured in this gate.yaml. "
            "Configure at least one.", file=sys.stderr)
      return EXIT_CLI_ERROR
  ```
- Do NOT touch `hash_backends_block` (24 call sites; conflates credential trust with
  prompt trust -- rejected in D5.6).

**3a-3. gate.yaml source read** -- the review path already holds `gate_data`; do not add a
new read. Extract `review_focus` from the dict returned by `_load_gate_backends`
(cli.py:118, which already returns `{}` for an untrusted repo) and gate it:
```python
yaml_focus = ""
raw = gate_data.get("review_focus", "")
if isinstance(raw, str) and raw.strip():
    if is_trusted_focus(gate_yaml_path, gate_data):
        yaml_focus = raw
    else:
        warn("gate.yaml review_focus ignored: not trusted. Run 'code-forge trust'.")
elif raw:  # non-string, non-None: list/dict/int from hand-edited YAML
    warn("gate.yaml review_focus ignored: not a string (got %s). "
         "Use a YAML string value." % type(raw).__name__)
```
Same extraction on the MCP side, where `gate_data` is already in hand at
mcp_server.py:243 and mcp_server.py:292. For the sampling path, also load
the contracts.yaml digest (see Task 3b for the full data flow).

**3a-4. CLI flag** -- add `--focus FILE` next to `--contract` (cli.py:350-355), same
FILE/stdin convention (`-` = stdin), reusing `_load_contract_file`'s read path as
`_load_focus_file`. Then `focus_spec = _merge_focus_spec(yaml_focus, file_content, warn)`
at the two existing merge sites, mirroring `_contract_spec_c` (cli.py:2063) and
`_contract_spec_a` (cli.py:2422).

**verify:** `python3 -B -m pytest tests/test_trust.py -v -k focus` plus CLI module tests
for the new flag.

**done:** merge helper exists; trust hash functions exist with short-circuit logic;
gate.yaml review_focus extracted with trust gate; --focus FILE flag wired at both merge
sites; sampling-only trust works; existing trust records survive.

---

### Task 3b: Focus mechanism -- builder injection + MCP wiring + sampling fix + tempfile
**Wave:** 2 | **Depends on:** Task 3a (merge helper + trust must exist)

Wires the focus spec into all 3 builders and all 4 review paths. Fixes the pre-existing
sampling contract_spec gap. Extends tempfile ownership for dual-file tracking.

**files:** src/code_forge/factories.py, src/code_forge/mcp_server.py,
src/code_forge/cross_repo.py, src/code_forge/mcp_jobs.py

**action:**

**3b-1. Builder injection** -- add `focus_spec: str = ""` to each builder and inject
immediately AFTER the design-intent block that Task 1 renames. Extract the injection
text into a shared helper `_format_focus_section(focus_spec: str) -> str` (in
factories.py or a small prompt module) to satisfy GR4 -- the advisory prose is
non-trivial and three copies invite drift:
```python
def _format_focus_section(focus_spec: str) -> str:
    """Format the ## Review Focus prompt section."""
    return (
        "\n## Review Focus\n" + focus_spec
        + "\nPrioritize findings in these areas; in your response, "
        + "state whether each area was checked.\n"
    )
```
Each builder calls it: `if focus_spec: prompt += _format_focus_section(focus_spec)`.
| Builder | def | param | inject after | call site(s) to pass focus_spec |
|---|---|---|---|---|
| `_make_subagent_spawn` (outlet_c) | cli.py:730 | cli.py:731 | cli.py:778-782 | cli.py:2069 |
| `build_l1_provider` (outlet_a + cross-repo) | factories.py:202 | factories.py:209 | factories.py:279-283 | cli.py:2480, cross_repo.py:307 |
| `build_sampling_l1_provider` (MCP sampling) | factories.py:507 | factories.py:514 | factories.py:575-576 | mcp_server.py:765 |

**3b-2. Cross-repo focus data flow (MM #4, verified against 8e18aa0):** the current
call chain has NO focus parameter anywhere:
- `_dispatch_cross_repo` (cli.py:1892) -- no focus param
- `_cross_repo_verdict_or_none` (cli.py:1613) -- no focus param
- `run_cross_repo` (cross_repo.py:170) -- no focus param
- `build_l1_provider` call (cross_repo.py:304-308) -- only `contract_spec`

Thread `focus_spec` through the full chain:
1. `_run` (cli.py main entry) computes `focus_spec` from yaml_focus + --focus
2. `_dispatch_cross_repo` (cli.py:1892): add `focus_spec=""` param, pass through
3. `_cross_repo_verdict_or_none` (cli.py:1613): add `focus_spec=""` param, pass through
4. `run_cross_repo` (cross_repo.py:170): add `focus_spec=""` param, pass through
5. `build_l1_provider` call (cross_repo.py:304-308): add `focus_spec=focus_spec`

Verify: `grep -n "focus_spec" src/code_forge/cli.py src/code_forge/cross_repo.py`
must show the param at every level.

**3b-3. MCP param** -- add `focus: str = ""` to forge_review (next to `contract`,
mcp_server.py:890). Two outlets, both required (D5.5):
- CLI-subprocess: mirror the contract temp-file wiring (mcp_server.py:936-943) -- write
  focus to a temp .md, `cli_args.extend(["--focus", focus_tmp_path])`, clean up in the
  SAME THREE places the contract temp file is cleaned (inline success at 957-961,
  job-transfer at 968, error cleanup at 972-977).
- Sampling: pass the MERGED focus spec (not the raw `focus` param) down through
  `_dispatch_sampling` into `build_sampling_l1_provider` (mcp_server.py:765). Raw
  pass-through would skip the gate.yaml `review_focus` merge and make the sampling
  outlet's prompt differ from the CLI outlet's for identical input -- see 3b-5 MERGE
  PARITY for the locked shape and the wiring-parity test.

**3b-4. Tempfile dual-file ownership (MM #5 + GLM #6, verified against 8e18aa0):**
`start_job` (mcp_jobs.py:80) accepts a single `tempfile_path: str | None`. When both
contract and focus tmpfiles exist, only one can be job-transferred on timeout; the other
leaks. Fix: extend `start_job` to accept `tempfile_paths: list[str] | None = None` (new
param, old `tempfile_path` deprecated but accepted for backward compat). Update all three
cleanup sites in `forge_review`:
1. Inline success (mcp_server.py:957-961): unlink both contract_tmp and focus_tmp
2. Timeout job-transfer (mcp_server.py:968): pass `tempfile_paths=[contract_tmp, focus_tmp]`
3. Error cleanup (mcp_server.py:972-977): unlink both paths
Update `mcp_jobs.py` eviction/timeout handlers to iterate the list.

**3b-5. Pre-existing bug, SEPARATE commit (D5.7)** -- that same call site passes no
`contract_spec` today, so `--contract` is already a silent no-op on the MCP sampling
outlet and factories.py:576 is unreachable in production. Wiring focus there while
contract stays broken reproduces D5.5's own failure mode. Fix it in its own commit,
BEFORE the focus commit, with a message explaining the gap. Do NOT fold it into the
focus change.

Fix shape (verified against 8e18aa0 -- contract never reaches the sampling path because
`_dispatch_sampling` has no such parameter):
- `_dispatch_sampling` (mcp_server.py:735): add `contract_spec: str = ""` and
  `focus_spec: str = ""` params; pass both into `build_sampling_l1_provider`
  (mcp_server.py:765-769).
- `forge_review` call site (mcp_server.py:914): pass the merged specs. `contract` is in
  scope (mcp_server.py:890).
- `forge_gate_check` call site (mcp_server.py:1009): leave both at default "". That
  handler has no contract/focus param and gate-check has no contract concept -- this is
  correct, not a second gap. Assert it stays empty so a future edit cannot leak
  review-only prompt content into the gate-check path.

**Sampling contracts.yaml digest (GLM #1, verified against 8e18aa0):**
`_build_review_context` (mcp_server.py:648-678) loads only baseline/diff/source_hash --
no contracts.yaml. The CLI-subprocess path loads the digest inside the forked subprocess
(cli.py:2063/2422); the MCP sampling path has no equivalent. For the merge to work,
`_dispatch_sampling` must also load the contracts.yaml digest:
```python
# Inside _dispatch_sampling, after _build_review_context:
contracts_yaml = workspace / ".code-forge" / "contracts.yaml"
yaml_digest = ""
if contracts_yaml.is_file():
    from .contract_loader import load_contract_digest
    yaml_digest = load_contract_digest(contracts_yaml, workspace, backend=None)
```
This follows the same `backend=None` pattern as the trust-gated path. The
`is_trusted_contracts` check (trust.py:271) must also be applied -- without it, loading
the digest on sampling would be a contracts.yaml prompt-injection bypass, since the
CLI path gates it via trust but the sampling path would not. Add the trust check:
```python
if contracts_yaml.is_file() and is_trusted_contracts(contracts_yaml, workspace):
    yaml_digest = load_contract_digest(contracts_yaml, workspace, backend=None)
```

**Sampling fallback preserves contract+focus (MM #4b, verified against 8e18aa0):**
On recoverable sampling failure, `_dispatch_sampling` constructs fallback CLI args
(mcp_server.py:822-823) containing only `--backend`/`--outlet`/`--committed`. The
`contract` and `focus` values are lost. Fix: thread the raw `contract` and `focus`
strings into `_dispatch_sampling` as params (they are already in scope at the
`forge_review` call site, mcp_server.py:890/891), and on fallback create tmpfiles
for both before constructing `cli_args`:
```python
# Inside _dispatch_sampling fallback branch (mcp_server.py:822):
if contract_spec:
    c_tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False)
    c_tmp.write(contract_spec); c_tmp.close()
    cli_args.extend(["--contract", c_tmp.name])
if focus_spec:
    f_tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False)
    f_tmp.write(focus_spec); f_tmp.close()
    cli_args.extend(["--focus", f_tmp.name])
```
Transfer both tmpfiles to the background job (use the extended `start_job` from 3b-4)
or unlink on inline success. Add a test: mock sampling LLMInvokeError(kind="truncated")
with both contract+focus present, assert CLI fallback args contain both `--contract`
and `--focus`.

MERGE PARITY (locked -- the naive 4-line fix is NOT sufficient): on the CLI-subprocess
outlet the MCP `contract` string reaches the prompt only after `_merge_contract_spec`
(cli.py:1828) merges the contracts.yaml digest, splits `## Do NOT Flag`, summarizes
bodies >4KB, and appends the confirmation-bias directive. Passing the raw string to the
sampling builder skips all of it, producing two different prompts for the same MCP input
depending on outlet -- the exact inconsistency class Task 1 exists to remove. So the
sampling path MUST call the same merge helpers before building the provider:
`cli._merge_contract_spec(yaml_digest, contract, backend=None, warn_fn=...)` and
`cli._merge_focus_spec(yaml_focus, focus, warn_fn=...)`.
- `backend=None` on the sampling path is deliberate: summarization would need an API
  backend, and sampling exists precisely because the client has no API key. A >4KB
  contract therefore passes through unsummarized -- warn, do not truncate (same rule as
  3a-1).
- No refactor needed to reach the helpers: mcp_server already calls cli's underscore
  privates (`cli._load_gate_backends` at mcp_server.py:243 and 292), so this follows an
  established in-repo pattern rather than introducing cross-module reach-in.
- This supersedes the naive wording in 3b-3: 3b-3 passes MERGED specs, never the raw MCP
  param.

**verify:** `python3 -B -m pytest tests/test_factories.py tests/test_cross_repo.py -v`
plus MCP module tests.

**done:** focus_spec wired on all 3 builders and all 4 review paths; cross-repo focus
data flow complete; MCP sampling passes merged contract+focus via same helpers as CLI;
fallback preserves both; tempfile dual-file ownership works.

---

### Task 3c: Focus mechanism -- schema + template + comprehensive tests
**Wave:** 2 | **Depends on:** Task 3a + Task 3b (all wiring must exist before tests)

Schema, template, and the full test matrix. Runs AFTER 3a+3b so tests exercise real code.

**files:** src/code_forge/gate.schema.json, src/code_forge/init_template.py,
tests/test_factories.py, tests/test_trust.py, tests/test_schema_corpus.py,
tests/test_cross_repo.py (+ CLI and MCP test modules)

**action:**

**3c-1. Schema + template (D5.3)** -- add `review_focus: {"type": "string"}` to
gate.schema.json properties; add a documented commented-out `review_focus:` entry to
GATE_YAML_TEMPLATE (init_template.py) noting that changing it requires re-running
`code-forge trust`; add a `review_focus` case to the tests/test_schema_corpus.py valid
corpus so the schema and the real loader cannot drift.

**3c-2. Tests** -- every row below is required; a missing row is an un-wired path that
ships as a silent no-op:
- Per builder x2: prompt CONTAINS "## Review Focus" and the focus text when focus_spec is
  non-empty; ABSENT when focus_spec == "" (3 builders x 2 = 6 minimum).
- `_merge_focus_spec`: yaml-only, file-only, both (order: yaml then file), both empty
  -> ""; >8KB warns exactly once AND returns the full untruncated text (assert length,
  not just the warning -- a truncating implementation would otherwise pass).
- Trust: trusted focus injects; post-trust edit of `review_focus` -> `is_trusted_focus`
  False -> focus dropped with warning BUT backends still load (the independent-failure-
  domain property of D5.6); absent/empty review_focus keeps an existing trust record
  valid (the migration guarantee -- assert an existing record still verifies);
  `is_trusted_focus` returns True for empty/absent focus (short-circuit before store
  read); returns False for missing store entry.
- Trust command: sampling-only gate.yaml (no backends, has review_focus) -> trust
  succeeds and records focus_hash; gate.yaml with neither backends nor review_focus
  -> trust fails with error.
- Untrusted repo: `_load_gate_backends` returns `{}` -> no focus injected.
- Non-string `review_focus` (list, dict, int) -> ignored with warning, never coerced.
- MCP: focus writes a temp file and adds `--focus <path>` to cli_args (CLI outlet);
  focus reaches the sampling builder (sampling outlet); sampling fallback on
  LLMInvokeError preserves both --contract and --focus tmpfiles in CLI fallback args.
- Cross-repo: `run_cross_repo` receives `focus_spec` and passes it to
  `build_l1_provider`; assert via spy/mock that the builder receives the param.
- Wiring-parity test: `_dispatch_sampling` calls `cli._merge_contract_spec` and
  `cli._merge_focus_spec`; CLI-subprocess path writes both `--contract` and `--focus`
  tmpfiles; contract body <=4096 bytes (avoids summarization divergence).
- >4KB divergence test: CLI outlet summarizes, sampling outlet passes raw with warning
  -- assert this is intentional, not accidental.
- Schema corpus: `review_focus` snippet passes both jsonschema and the real loader.
- Bug-inject (Golden Rule 2), PER PATH: delete ONE builder's focus-injection block ->
  that builder's test FAILS -> restore -> PASSES; repeat for all 3. Then invert
  `is_trusted_focus` to always-True -> the post-trust-edit test FAILS -> restore ->
  PASSES. A single inject proves one path, not the mechanism.

**verify:** `python3 -B -m pytest tests/test_factories.py tests/test_trust.py
tests/test_schema_corpus.py tests/test_cross_repo.py -v` plus CLI and MCP test modules.

**done:** full test matrix passes; schema and loader drift guard in place; init template
documents review_focus with re-trust note; per-path bug-inject verified; wiring-parity
test confirms both outlets call same helpers; >4KB divergence documented.

---

### Task 4: Add untracked-file blame degradation test
**Wave:** 3 | **Depends on:** Task 2

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
**Wave:** 3 | **Depends on:** Task 1 + 2 + 3a + 3b + 3c + 4

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
  trust record valid; `is_trusted_focus` short-circuits True for empty/absent focus
- Non-string `review_focus` is ignored with a warning, never coerced into the prompt
- gate.schema.json documents `review_focus`; the schema-corpus test covers it; the init
  template documents it including the re-trust requirement
- MCP sampling outlet passes contract_spec (pre-existing gap, separate commit), and
  passes MERGED specs via the same merge helpers as CLI-subprocess; sampling loads
  contracts.yaml digest with trust check; sampling fallback preserves contract+focus
- Wiring-parity test confirms both outlets call the same merge helpers; >4KB divergence
  documented as intentional (CLI summarizes, sampling passes raw)
- forge_gate_check's sampling dispatch passes no contract/focus, asserted
- sampling-only gate.yaml (no backends, has review_focus) can be trusted
- cross-repo path threads focus_spec through full call chain
- start_job accepts multiple tempfile_paths; both contract and focus tmpfiles are
  tracked and cleaned on inline success, timeout, and error
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
