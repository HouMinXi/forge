CRITICAL -- READ THIS FIRST. You are a code REVIEWER, NOT an implementer.

Your ENTIRE output must be a review verdict, and NOTHING else. Required format:
  SUMMARY: B=<n> H=<n> M=<n> L=<n>
  (then, per finding: severity, file:line, description, required fix)

Do NOT offer to implement the plan. Do NOT set up worktrees or branches. Do NOT
create tests. Do NOT say "ready to execute" or ask how the user wants to
proceed. You are inspecting a DOCUMENT for defects; you are NOT executing it.

If the plan is clean, output exactly:
  SUMMARY: B=0 H=0 M=0 L=0
followed by ONE paragraph naming the specific file:line references you checked
against the real source. A clean verdict is valid and wanted -- but it must be
backed by cited verification, not a bare "looks good."

The actual review task and the plan follow below.
============================================================

# forge Phase 41 -- CP1b ROUND-6 plan review

You are reviewing an IMPLEMENTATION PLAN (not code yet). The plan is the
document that follows this prompt. It describes changes to a real Python
codebase at /home/houminxi/code/forge/src/code_forge/ -- you have that repo;
VERIFY every plan claim against the real source (cite file:line), do not trust
the plan's own line numbers.

Report only findings you verified against real code. This is a convergence
round: a clean 0/0/0/0 is the expected result if no NEW defect exists, and it is
a valid, wanted outcome -- do not manufacture findings to look thorough.

## What this feature is

Phase 41 adds a `review_focus` prompt mechanism at parity with `--contract`:
gate.yaml `review_focus:` (with its OWN trust hash, independent of backend
trust), a `--focus FILE` CLI flag, and a `focus` MCP param, all merging into a
"## Review Focus" prompt section on all 3 builders / 4 review paths. It also
renames "## Contract Reference" -> "## Design Intent" and adds a committer date
to git-blame.

## Disposition through round-5 (non-convergence protocol -- READ FIRST)

This plan has iterated through 5 review rounds. Rounds 1-4 surfaced 33 findings,
ALL fixed and independently re-verified. Round-5 (gm repo-grounded + lc) found
exactly ONE, now FIXED:

- Task 3a-1's size-guard rationale mislabeled cli.py:1861 as a `len()` char-count
  guard. Real code is `len(effective_content.encode("utf-8")) > 4096` -- a UTF-8
  BYTE count. FIXED: 3a-1 now specifies `len(merged.encode("utf-8")) > 8192`
  (byte count, mirroring cli.py:1861's byte mechanism at a larger warn-only
  threshold; focus never summarizes, so 8192 > contract's 4096 is intentional),
  and the former internal contradiction with 41-PLAN.md:936 ("contract body
  <=4096 bytes") is resolved.

In round-5, lc independently re-verified these round-4 fixes CLEAN against real
source -- do NOT re-open them unless you find the fix itself is now wrong:
- REPLAN(a) dual-tmpfile lifecycle: `.name` captured before write; every exit
  path (creation-fail, dispatch-raise, inline-return, timeout transfer,
  start_job-raise) unlinks or transfers ownership; no leak / no double-unlink.
- REPLAN(e) tests: the `_evict_stale` recipe is falsifiable (inject a stale
  `status="failed"` entry into `mcp_jobs._jobs` with a backdated `created_at`,
  call `_evict_stale()` directly -- NOT via `max_lifetime_s`); the inline-path
  delete assertion is present.
- 3b(d): `raw_focus = focus_spec` saved before the merge; sampling-scope
  variables correct.
- Cross-refs: contracts.yaml digest already at mcp_server.py:837-842,
  `build_sampling_l1_provider` at mcp_server.py:853-857, trust-assert updates,
  test_legacy.py:262 date docstring -- all correct.

Do NOT resurrect the round-2 false-green: H1 was NOT "fully resolved" by a
backwards read of `_load_gate_backends` (it returns `([], {})` when untrusted,
cli.py:160, NOT `(cfgs, gd)`).

## Review this hardest

A. The round-5 fix itself (Task 3a-1 size guard). Against cli.py:1861, confirm
   the guard is now correctly a UTF-8 byte count, that
   `len(merged.encode("utf-8")) > 8192` is the right mirror of
   `len(effective_content.encode("utf-8")) > 4096`, and that NO other plan
   reference to :1861 or the focus size threshold still claims char-count or
   contradicts 41-PLAN.md:936.
B. Residual sweep. This is a convergence round: read the whole plan for any
   remaining stale file:line, internal contradiction, or claim that does not
   match real source. If you find nothing, say 0/0/0/0 plainly.

## Output format

First line: `SUMMARY: B=<n> H=<n> M=<n> L=<n>` (Blocker/High/Medium/Low).
Then per finding: severity, location (plan section + real file:line),
description, required fix. Verify each against real source before reporting.

--- PLAN FOLLOWS ---
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

> **RECONCILE 2026-07-23 against main @ 89bdb4d (PM, was ground vs 8e18aa0/
> 7d871a5). READ BEFORE PLANNING/EXECUTING.** The sampling-contract work
> that this plan listed as in-scope was split into a separate phase
> (.planning/phases/41-sampling-fix, ACCEPTANCE present) and MERGED to main
> as 2edb9d4 + 5c8e001. Two consequences:
>
> ALREADY DONE -- do NOT rebuild (verified in main, not inferred):
> - **D5.7 / Task 3b-5 (sampling contract_spec wiring)**: merged (2edb9d4).
>   `_dispatch_sampling` (now mcp_server.py:800, was :735) has a
>   `contract_spec: str = ""` param (:806), merges via `cli._merge_contract_spec`
>   (:845), and passes it to `build_sampling_l1_provider` (:853/:857). Tests
>   exist: test_mcp_server.py:2146+ ("Phase 41: contract_spec wiring"). The
>   entire 3b-5 sub-task is obsolete.
> - **M3 tmpfile-leak fix + CLI-dispatch centralization**: merged (5c8e001,
>   "centralize CLI dispatch to close tmpfile leak and two pre-existing
>   siblings"). Contract tmpfile lifecycle now lives in one place,
>   `_dispatch_cli` (mcp_server.py:647-697): materialize at :669-672, unlink
>   on `_run_cli_budgeted` raise at :679/:684, transfer to `start_job` at
>   :690-692, unlink on `start_job` raise at :697. The plan's "M3 fix" (3b-3)
>   and its scattered forge_review cleanup sites (old :936-977) are obsolete.
>
> IMPLEMENTATION-SHAPE CHANGE for Task 3b (the reason this is not a pure
> line-number touch-up): focus tmpfile wiring must now MIRROR the contract_tmp
> lifecycle INSIDE `_dispatch_cli` (:664-697), not the old scattered
> forge_review sites. This is simpler than the plan's 3b-3/3b-4 text but the
> text is written against the pre-centralization architecture and must be
> re-grounded. 3b-4 (start_job dual-file ownership) is still live -- start_job
> still takes a single `tempfile_path` (mcp_jobs.py:83) -- but the two tmpfiles
> now both flow through `_dispatch_cli`, so the dual-file handling localizes
> there. **Task 3b (3b-1..3b-4) needs a focused re-plan against _dispatch_cli
> BEFORE CP1; this note is the scope of that remaining reconcile, not its
> completion.**
>
> STILL VALID (re-verified unchanged on 89bdb4d): Task 1 header sites
> (cli.py:780, factories.py:281, factories.py:576 -- all still "## Contract
> Reference"); Task 2 (git.py:358 git_blame, legacy.py:~232-243 attribution);
> cli._merge_contract_spec (:1828), cli._load_gate_backends (:118), SEC-02
> (:1028); trust anchors (hash_backends_block :99, is_trusted :125,
> hash_contracts_content :243, contracts_hash pattern :286+); cross-module
> `cli._load_gate_backends` call pattern in mcp_server.py (:243/:292). Task 1,
> Task 2, Task 3a, Task 3c, Task 4 scope is unaffected by the merge.

**Wave structure:**
- Wave 1: Task 1 (header rename), Task 2 (blame date), Task 3a (focus plumbing) -- all independent
- Wave 2: Task 3b (builder wiring + sampling fix) -- depends on 3a (merge helper exists) + Task 1 (renamed header anchor)
- Wave 3: Task 3c (schema + template + tests), Task 4 (blame degradation) -- depends on Wave 1+2
- Wave 4: Task 5 (full suite) -- depends on all

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
   "Blast Radius < Contract Reference < Diff", and in docstrings/comments that
   describe the section) with "Design Intent" via replace_all. All 24 current
   occurrences are prompt-output assertions, docstrings, or assert messages that
   must track the rename -- none are historical notes to keep -- so the verify
   grep below (zero "Contract Reference" left in src+tests) must pass cleanly.

3. Coverage floor (diff-coverage gate): each of the 3 renamed sites needs >=1 test
   asserting the new "## Design Intent" header. test_contract_wiring.py covers the
   cli.py path; verify whether the build_l1_provider and build_sampling_l1_provider
   prompt paths are asserted anywhere -- add a prompt-content assertion for any
   uncovered builder, else the factories renames ship unverified.
   Caveat for the third site: post-2edb9d4 (the sampling contract_spec merge -- see the
   RECONCILE note above), `build_sampling_l1_provider`'s caller `_dispatch_sampling`
   (mcp_server.py:800) DOES pass `contract_spec`, so factories.py:576 is reachable
   end-to-end on the sampling path when a contract is provided; it is also unit-testable
   on the builder directly. Prefer the direct builder unit test for the header assertion
   (no LLM sampling round-trip needed); an e2e sampling assertion is additionally possible
   if desired -- state which one the test is.

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
Add `"date": "2023-11-14"` to mock blame_entry. Also update that test's docstring
(test_legacy.py:262), which spells out the OLD format
`'git-blame: {author} {sha[:8]} {subject}'` -- add the date field so it matches the
new assertion (kimi r4 L4; Task 2 Step 8 set the docstring-tracks-behavior standard).

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
**Wave:** 1 | **Depends on:** none (parallel with Task 1 and Task 2)

Adds the core focus plumbing: merge helper, trust hash, gate.yaml extraction, and CLI flag.
Injected spec is NOT yet wired to builders (Task 3b does that).
No dependency on Task 1: the anchor sites (cli.py:1828, trust.py) are not affected by
the header rename at cli.py:780.

All line numbers below are verified against main @ 8e18aa0 (2026-07-20). They WILL drift
once Task 1 and Task 2 land -- re-grep the anchor symbol before each edit, never trust
the number alone.

**files:** src/code_forge/cli.py, src/code_forge/trust.py

**action:**

**3a-1. Merge helper** -- new `_merge_focus_spec(yaml_focus: str, file_content: str,
warn_fn) -> str` in cli.py, placed next to `_merge_contract_spec` (cli.py:1828).
Mirrors its shape but is deliberately simpler (D5.2): join yaml_focus then file_content
with a blank-line separator EXACTLY as `_merge_contract_spec` does (cli.py:1889) --
start `merged = yaml_focus`, then when file_content is present
`merged = (merged + "\n\n" if merged else "") + file_content` -- so a newline-less yaml
value and a file body never fuse into one run-on line (`"short textFile body"`); NO LLM
summarization (summarizing focus areas destroys the specific areas, which is the whole
feature); NO "## Do NOT Flag" split; NO confirmation-bias directive (both are
contract-specific). Size guard: if the merged string exceeds 8192 BYTES
(`len(merged.encode("utf-8")) > 8192`, mirroring `_merge_contract_spec`'s byte-count
guard `len(effective_content.encode("utf-8")) > 4096` at cli.py:1861 -- same UTF-8 byte
mechanism, but a larger 8192-byte threshold because focus never summarizes, so this
guard only warns whereas contract's 4096 is a summarization trigger), call warn_fn once
and pass the text through UN-truncated -- silently dropping focus areas is worse than a
warned-large prompt. Empty + empty returns "".

**3a-2. Trust (D5.6)** -- in trust.py, mirroring the contracts pattern at trust.py:243-286:
- `hash_focus_text(gate_data: dict) -> str`: sha256 of the canonical JSON of
  the `review_focus` value; returns "" when the field is absent or empty (this is what
  keeps every existing trust record valid -- see migration note below). Signature
  uses `dict` (not `Optional[dict]`); callers must never pass None -- the function
  would crash on `gate_data.get(...)`.
- `is_trusted_focus(gate_yaml_path: Path, gate_data: dict) -> bool`:
  ```
  current = hash_focus_text(gate_data)
  if not current:        # absent or empty -> nothing to authorize
      return True
  store = _load_trust_store()   # same loader is_trusted uses (trust.py:131)
  entry = store.get(str(gate_yaml_path.resolve()))  # MUST match is_trusted key format
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
  **Implementation note:** `record_trust` currently replaces the entire dict
  (`store[key] = {"hash": current_hash}`). The new version writes both keys
  merge-first, so any other key already on THIS gate.yaml entry survives
  (defensive only -- today nothing else lives here; `contracts_hash` is stored
  under the separate contracts.yaml path key, trust.py:302, a different entry):
  `store[key] = {**store.get(key, {}), "hash": current_hash,
  "focus_hash": focus_hash}`. Do NOT attempt `store[key]["focus_hash"] = ...`
  — that would KeyError on fresh entries. The existing `is_trusted` reads only
  `entry.get("hash")` and ignores extra keys, so adding `focus_hash` to the
  dict is safe.
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

**3a-3. gate.yaml source read (H1 fix -- focus trust is INDEPENDENT of backend trust).**
Do NOT read `review_focus` from `_load_gate_backends`'s `gate_data`: that function
returns `([], {})` when the backends block is untrusted (cli.py:160), which would
silently drop a legitimately-trusted `review_focus` and never fire its warning --
coupling focus to backend trust and defeating the independent focus-trust design
(D5.6). Read the raw YAML independently and gate ONLY on `is_trusted_focus`.

Add a standalone helper next to `_load_gate_backends` (do NOT fold it in -- that
function's trust-gated `{}`-on-untrust return is relied on by 24+ callers; leave its
body and contract untouched):
```python
def _load_gate_yaml_raw(gate_yaml_path: Path) -> dict:
    """Best-effort parse of gate.yaml with NO trust gating.
    Returns the parsed dict, or {} if absent / empty / not a dict.
    Mirrors _load_gate_backends's parse prefix (same YAMLError -> CliError)
    so both readers agree on syntax errors."""
    import yaml as _y
    try:
        with open(gate_yaml_path, "r", encoding="utf-8") as _f:
            gd = _y.safe_load(_f)
    except FileNotFoundError:
        return {}
    except _y.YAMLError as exc:
        raise CliError(
            "gate.yaml parse error: %s" % exc,
            remediation="Check gate.yaml syntax. "
            "Run 'code-forge init --force' to regenerate.",
        ) from exc
    return gd if isinstance(gd, dict) else {}
```
Then wrap extract + trust-gate into ONE shared helper next to `_load_gate_yaml_raw`, so
the CLI path and the sampling path cannot drift (GR4 -- the extract/gate/warn logic is
this feature's security boundary and must not be hand-copied into two call sites):
```python
def _load_trusted_yaml_focus(gate_yaml_path: Path, warn_fn) -> str:
    """Return the trusted review_focus string from gate.yaml, or "".

    Reads gate.yaml with NO backend-trust gating (via _load_gate_yaml_raw),
    then authorizes review_focus ONLY through is_trusted_focus -- so focus
    trust is independent of backend trust (H1/D5.6). Warns and returns ""
    when review_focus is present-but-untrusted or a non-string value."""
    focus_gd = _load_gate_yaml_raw(gate_yaml_path)  # NOT backend-trust-gated
    raw = focus_gd.get("review_focus", "")
    if isinstance(raw, str):
        if not raw.strip():
            return ""  # absent / empty / whitespace-only: nothing to inject
        if is_trusted_focus(gate_yaml_path, focus_gd):
            return raw
        warn_fn("gate.yaml review_focus ignored: not trusted. "
                "Run 'code-forge trust'.")
        return ""
    if raw is not None:  # list / dict / int from hand-edited YAML
        warn_fn("gate.yaml review_focus ignored: not a string (got %s). "
                "Use a YAML string value." % type(raw).__name__)
    return ""
```
Branch shape is deliberate -- it fixes two edge bugs a naive `if raw.strip(): ... elif
raw:` has: (1) a whitespace-only value IS a string, so it takes the `isinstance` branch
and returns "" silently -- it must NOT fall through to the "not a string" warning;
(2) a falsy NON-string (`[]`, `{}`, `0`) is caught by `raw is not None` and DOES warn,
matching the 3c-2 test row "list, dict, int -> ignored with warning" (a bare `elif raw:`
silently drops these and fails that test).

Both focus-reading paths call this ONE helper as `yaml_focus =
_load_trusted_yaml_focus(gate_yaml_path, warn)`: (1) the CLI `_run` path (which also
serves the CLI-subprocess outlet -- the fork re-enters `_run` and reads gate.yaml
there); and (2) the sampling path, as `cli._load_trusted_yaml_focus(gate_yaml_path,
warn)` inside `_dispatch_sampling` (mcp_server.py:800), gated
`if not staged and gate_yaml_path.is_file():` -- parallel to the existing contracts.yaml
load at mcp_server.py:837-839, and shown as an explicit call site in Task 3b (d).
Backend loading still flows through `_load_gate_backends` unchanged. (The sampling
path's contracts.yaml digest load already exists at mcp_server.py:837-842 post-2edb9d4
-- do NOT re-add it, kimi r4 L2; see Task 3b(d) for the full sampling data flow.)

**Invariant-comment carve-out (do this in the SAME edit as the CLI read):** the H1
read adds a SECOND raw gate.yaml read inside `_run`, which the comment at
cli.py:2182-2184 ("Never re-read gate.yaml raw after this point -- a second read
bypasses the trust check") forbids. Place the `_load_trusted_yaml_focus(gate_yaml_path,
warn)` call next to the contract file read at cli.py:2195-2200, and revise that comment
to carve out the focus exception, e.g.: "...never re-read gate.yaml raw for BACKEND
consumption; the review_focus read below is gated separately by is_trusted_focus (D5.6),
independent of backend trust." Without the revision an implementer either leaves a
self-contradicting comment or -- worse -- obeys it and reverts focus to the trust-gated
`gate_data`, silently re-introducing H1.

**Bug-inject (H1 regression guard, Golden Rule 2):** set up a repo, run `code-forge
trust`, THEN edit a DANGEROUS_FIELDS backend field (e.g. change `base_url:` in gate.yaml
-- the hashed set is trust.py:23-31) so `is_trusted` actually flips to False and
`_load_gate_backends` returns `([], {})`. Leave `review_focus` unchanged (still trusted).
The test asserts BOTH, in order: (1) backends were dropped -- stderr contains "Untrusted
repo backends ignored" (emitted by `_load_gate_backends`); (2) `## Review Focus` STILL
appears. Then revert the focus read to `_load_gate_backends`'s `gate_data` -> step (2)
must FAIL; restore -> PASS. CRITICAL: the inject MUST target a dangerous field. Editing
`model`/`temperature` does NOT flip `is_trusted` (`hash_backends_block` hashes only
DANGEROUS_FIELDS, trust.py:99-122), so `gate_data` stays non-empty and the reverted-code
test would still PASS -- a hollow guard that proves nothing.

**3a-4. CLI flag** -- add `--focus FILE` next to `--contract` (cli.py:350-355), same
FILE/stdin convention (`-` = stdin). Define `_load_focus_file(path_or_dash: str) -> str`
mirroring `_load_contract_file` (cli.py:1666): read file content from path, or stdin if
`-`. All guards live in `_load_focus_file`, mirroring `_load_contract_file`'s CliError
behavior -- `--contract` (cli.py:351) has NO `type=argparse.FileType`, so argparse does
no validation; missing-file, binary-content, oversize, and stdin handling all raise
CliError inside the loader. Do NOT add `type=argparse.FileType` to `--focus`: it would
hand `_load_focus_file` an open file object and break the `-`=stdin convention. Then
`focus_spec = _merge_focus_spec(yaml_focus, file_content, warn)` at the two existing
merge sites, mirroring `_contract_spec_c` (cli.py:2063) and `_contract_spec_a` (cli.py:2422).

**IMPORTANT -- raw passthrough for outlet C:** The `_run` function passes RAW
`_focus_file_content` (from `--focus FILE`) to `_dispatch_subagent`, NOT a
pre-merged `focus_spec`. The merge happens exactly once inside
`_dispatch_subagent` via `_merge_focus_spec(yaml_focus, _focus_file_content, warn)`,
matching how `_contract_file_content` is threaded raw to `_dispatch_subagent`
and merged once inside at cli.py:2068. Pre-merging in `_run` and passing the
merged value would cause `_dispatch_subagent` to re-merge with `yaml_focus`,
duplicating yaml focus in outlet C's prompt.

**M2 fix:** `_dispatch_subagent` (cli.py:2043) wraps the outlet_c merge site
(cli.py:2063). Its current params are `(outlet, warn, _contract_file_content, backend,
resolved, source_hash, registry, engine_choice, _clean_threshold, cwd)` -- no
`yaml_focus`, no `gate_data`, no `focus_spec`. Add `yaml_focus=""` and
`_focus_file_content=""` to its signature (mirroring `_contract_file_content`) and
thread both through to the merge site: `_merge_focus_spec(yaml_focus,
_focus_file_content, warn)`. Without this, outlet_c hits a NameError or silently
merges file-only content -- a per-outlet prompt divergence.

**verify:** `python3 -B -m pytest tests/test_trust.py -v -k focus` plus CLI module tests
for the new flag.

**done:** merge helper exists; trust hash functions exist with short-circuit logic;
gate.yaml review_focus extracted with trust gate; --focus FILE flag wired at both merge
sites; sampling-only trust works; existing trust records survive.

---

### Task 3b: Focus mechanism -- builder injection + MCP wiring + sampling fix + tempfile
**Wave:** 2 | **Depends on:** Task 3a (merge helper + trust must exist)

Wires the focus spec into all 3 builders and all 4 review paths. (The sampling
contract_spec gap D5.7 is ALREADY fixed in main via 2edb9d4 -- see RECONCILE; not
re-done here, kimi r4 L3.) Extends tempfile ownership for dual-file tracking.

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
| `build_sampling_l1_provider` (MCP sampling) | factories.py:507 | factories.py:514 | factories.py:575-576 | the `build_sampling_l1_provider(...)` call in `_dispatch_sampling`, mcp_server.py:853-857 post-2edb9d4 -- NOT :765 (stale pre-merge, kimi/gm r4) |

**3b-2. Cross-repo focus data flow (MM #4, verified against 8e18aa0):** the current
call chain has NO focus parameter anywhere:
- `_dispatch_cross_repo` (cli.py:1897) -- no focus param
- `_cross_repo_verdict_or_none` (cli.py:1613) -- no focus param
- `run_cross_repo` (cross_repo.py:170) -- no focus param
- `build_l1_provider` call (cross_repo.py:304-308) -- only `contract_spec`

**Pre-existing gap disclosed (same defect class as D5.7):** `--contract FILE` is
already a silent no-op on the cross-repo path. `run_cross_repo` loads its contract
**internally** (cross_repo.py:250-256: digest only, no `--contract` file, no
`_merge_contract_spec`); `_cross_repo_verdict_or_none` (cli.py:1649-1661) passes no
contract at all. This is the same "missing parameter" class as D5.7. Out of scope for
Phase 41 — follow-up work. The focus threading below deliberately does NOT mirror the
contract mechanism on this path (it threads the merged `focus_spec` directly to
`build_l1_provider`), avoiding replicating the gap.

Thread `focus_spec` through the full chain:
1. `_run` (cli.py main entry) computes `focus_spec` from yaml_focus + --focus
   for the cross-repo path only; for outlet C, passes RAW `_focus_file_content`
   + `yaml_focus` separately (see Task 3a-4 M2 fix for the raw passthrough rule)
2. `_dispatch_cross_repo` (cli.py:1897): add `focus_spec=""` param, pass through
3. `_cross_repo_verdict_or_none` (cli.py:1613): add `focus_spec=""` param, pass through
4. `run_cross_repo` (cross_repo.py:170): add `focus_spec=""` param, pass through
5. `build_l1_provider` call (cross_repo.py:304-308): add `focus_spec=focus_spec`

Verify: `grep -n "focus_spec" src/code_forge/cli.py src/code_forge/cross_repo.py`
must show the param at every level.

> **3b-3..3b-5 REPLAN 2026-07-23 (graph-grounded vs main @ ca0d860).
> IMPLEMENT THIS BLOCK. The 3b-3 / 3b-4 / 3b-5 text below it (down to the
> "### Task 3c" header) is SUPERSEDED -- kept only for design-decision
> history; do NOT implement it.** Why: the sampling-fix merge (2edb9d4 +
> 5c8e001) centralized all CLI-subprocess dispatch into one function and
> already closed D5.7 + the M3 leak. The old sub-tasks target a
> pre-centralization architecture that no longer exists.
>
> ALREADY DONE in main -- do NOT rebuild (verified, not inferred):
> - **D5.7 (old 3b-5)**: `_dispatch_sampling` (mcp_server.py:800) has a
>   `contract_spec` param, merges via `cli._merge_contract_spec`, and passes
>   it into `build_sampling_l1_provider`. Tests at test_mcp_server.py:2146+.
> - **M3 tmpfile leak (old 3b-3 "M3 fix")**: `_dispatch_cli`
>   (mcp_server.py:647-700) owns the whole tmpfile lifecycle -- unlink on
>   `_run_cli_budgeted` raise, unlink on inline return, transfer to
>   `start_job` on timeout, unlink both on `start_job` raise. Do NOT re-add
>   scattered forge_review cleanup.
>
> GRAPH GROUND TRUTH (code-review-graph, 2026-07-23): `_dispatch_cli` has
> exactly THREE production callers -- `forge_review` (call at mcp_server.py
> :1025), `forge_gate_check` (:1078), and `_dispatch_sampling`'s CLI-fallback
> branch (:917). All CLI dispatch is centralized there; focus is threaded
> from each caller and materialized ONCE inside `_dispatch_cli`.
>
> **(a) CLI outlet -- focus tmpfile inside `_dispatch_cli`** (replaces old
> 3b-3 CLI bullet + M3 fix). Add one param `focus: str | None = None` after
> `contract` in the `_dispatch_cli` signature. Do for `focus` EXACTLY what
> the function already does for `contract` -- this "mirror the contract_tmp
> lifecycle" instruction is deliberately symbol-anchored, not line-anchored,
> so it survives edit drift.
>
> LEAK TRAP (gm r3): `contract_tmp` is created BEFORE the dispatch `try`
> (mcp_server.py:664-672; the `try` starts :674 and its `except BaseException:
> _unlink(contract_tmp)` only covers the dispatch call, not creation). Adding
> a SECOND pre-try creation for `focus_tmp` widens this: an OSError while
> creating/writing focus_tmp would leak the already-created contract_tmp,
> because that except never runs. So initialize BOTH to None and wrap BOTH
> creations in one guard that unlinks whichever exists on failure -- and
> capture `.name` BEFORE the write, so a write/close failure (the trap's own
> motivating case) still leaves a path for the except to unlink (kimi/gm r4):
> ```
> contract_tmp: str | None = None
> focus_tmp: str | None = None
> try:
>     if contract:
>         tmp = tempfile.NamedTemporaryFile(
>             mode="w", suffix=".md", delete=False, encoding="utf-8"
>         )
>         contract_tmp = tmp.name   # BEFORE write: file already on disk post-ctor
>         tmp.write(contract); tmp.close()
>         cli_args.extend(["--contract", contract_tmp])
>     if focus:
>         ftmp = tempfile.NamedTemporaryFile(
>             mode="w", suffix=".md", delete=False, encoding="utf-8"
>         )
>         focus_tmp = ftmp.name   # BEFORE write (same reason)
>         ftmp.write(focus); ftmp.close()
>         cli_args.extend(["--focus", focus_tmp])
> except BaseException:
>     _unlink(contract_tmp); _unlink(focus_tmp)
>     raise
> ```
> (`_unlink` already no-ops on None -- the current code calls
> `_unlink(contract_tmp)` with `contract_tmp=None` whenever `contract` is
> absent, so passing an un-created sibling is safe.) Then add `_unlink(
> focus_tmp)` beside every existing `_unlink(contract_tmp)` in the DISPATCH
> try (the `except BaseException` path, the inline-result path, and the
> `start_job` `except Exception` path), and pass `focus_tempfile_path=
> focus_tmp` in the `start_job(...)` call. Also update the `_dispatch_cli`
> docstring (mcp_server.py:654-663), which today documents a contract-only
> tmpfile lifecycle, to describe the dual contract+focus lifecycle -- Task 2
> Step 8 set the "change behavior -> update docstring" standard (kimi r4 L5).
>
> **(b) start_job dual-file** (replaces old 3b-4). The current code is
> KEY-based, not list-based: `start_job` (mcp_jobs.py:80) stores each tmpfile
> under a named dict key, and three consumers iterate the fixed tuple
> `("tempfile_path", "stderr_log_path")`. Minimal, convention-matching change:
> add a `focus_tempfile_path: str | None = None` param to `start_job`, store
> it under key `"focus_tempfile_path"`, and add that key to the iteration
> tuple in ALL THREE consumers -- `snapshot_tempfile_paths`, `_wait_for_job`'s
> finally block, and `_evict_stale`. Intentional: a `list[str]` param (old
> 3b-4's shape) would diverge from the existing key convention and force a
> rewrite of the three iteration sites; adding one named key is the smaller,
> consistent change. (INVERSION: forgetting one of the three consumer tuples
> silently leaks the focus tmpfile on that path -- the bug-injection below
> targets exactly this.)
>
> **(c) Thread focus from the 3 callers** (mirrors how `contract` is passed):
> - `forge_review` (:1025 call): add a `focus: str = ""` MCP param. Pass it
>   RAW to `_dispatch_cli` (`focus=focus or None`) -- do NOT pre-merge with
>   gate.yaml `review_focus`. The CLI subprocess handles the merge exactly
>   once via `_merge_focus_spec` (mirror `contract=contract or None` where
>   raw value crosses the MCP/CLI boundary and merge happens downstream).
> - `forge_gate_check` (:1078 call): pass nothing, leave `focus` default --
>   gate-check has no focus concept, exactly as it passes no `contract`.
> - `_dispatch_sampling` fallback (:917 call): pass `focus=raw_focus`,
>   mirroring the existing `contract=raw_contract`. The fallback passes
>   the RAW MCP value, not a merged value.
>
> **(d) Sampling in-process outlet -- focus_spec** (parallel to the
> already-merged contract_spec): add `focus_spec: str = ""` to
> `_dispatch_sampling` (:800). The caller passes the RAW MCP focus value
> (not merged); `_dispatch_sampling` saves `raw_focus = focus_spec` before
> merge (mirror `raw_contract = contract_spec` at :832 -- the fallback in
> (c) needs the RAW value), then merges via `_merge_focus_spec`. Pass
> `focus_spec=focus_spec` into `build_sampling_l1_provider`. Add
> `focus_spec: str = ""` to `build_sampling_l1_provider` (factories.py:507)
> and inject the `## Review Focus` section via `_format_focus_section`
> (Task 3b-1) after the contract injection, guarded `if focus_spec:`. The
> forge_review sampling call (mcp_server.py:997-1004, which already passes
> `contract_spec=contract`) also passes RAW `focus` (NOT pre-merged --
> mirror `contract_spec=contract`).
>
> **IMPORTANT: gate-check isolation.** `_dispatch_sampling` serves both
> `forge_review` (needs yaml_focus) and `forge_gate_check` (no focus).
> The yaml_focus extraction must be conditional: only load and merge
> `review_focus` from gate.yaml when `staged=False` (review path), NOT
> when `staged=True` (gate-check path). This matches the existing pattern
> where `contracts_yaml` loading at :837-839 is already gated on
> `not staged`. The `forge_gate_check` call passes `focus=""` (default),
> and the yaml_focus extraction is skipped for gate-check, so its prompt
> never contains "## Review Focus".
>
> Explicit load block (mm r3 -- show the call, not just the policy), parallel
> to the contracts.yaml load at mcp_server.py:837-839. `_dispatch_sampling`
> has `workspace` and `staged` in scope but NO `gate_yaml_path` / `warn`
> local, so compute the path and mirror the real inline warn lambda (:847):
> ```
> raw_focus = focus_spec   # save raw MCP value BEFORE merge, mirror
>                          # raw_contract = contract_spec at mcp_server.py:832
> gate_yaml_path = workspace / ".code-forge" / "gate.yaml"
> yaml_focus = ""
> if not staged and gate_yaml_path.is_file():
>     yaml_focus = cli._load_trusted_yaml_focus(
>         gate_yaml_path,
>         lambda msg: (sys.stderr.write(msg + "\n"), sys.stderr.flush()),
>     )
> focus_spec = cli._merge_focus_spec(yaml_focus, raw_focus, warn_fn=...)
> ```
> The `raw_focus = focus_spec` save is REQUIRED and must stay INSIDE this block
> (lc r4): the merge line reassigns `focus_spec`, and the (c) fallback consumes
> the raw value, so an implementer who copies the block without it re-introduces
> the double-merge the (c)/(d) fixes exist to prevent. Reading focus via the
> shared `_load_trusted_yaml_focus` (Task 3a-3) -- NOT `_load_gate_backends`'s
> trust-gated dict -- is what keeps the sampling outlet's focus trust identical
> to the CLI outlet's; the wrong loader here silently re-introduces H1.
>
> **(e) Tests** -- mirror the FIVE existing `_dispatch_cli` contract tests in
> test_mcp_server.py one-for-one for focus: `..._job_success_keeps_focus`,
> `..._no_focus_no_tmpfile`, `..._run_raises_unlinks_focus`,
> `..._run_raises_cancelled_unlinks_focus`, and extend `..._start_job_raises`
> to assert all THREE tmpfiles (contract + focus + stderr) are unlinked. Plus
> a tmpfile CONTENT assertion: in fallback and timeout scenarios, assert that
> `_dispatch_cli` receives (or its tmpfile contains) the RAW MCP focus input,
> NOT a merged value. This catches the double-merge regression that the (c)/(d)
> fixes address. Plus the cross-outlet parity test: one MCP `focus` input yields
> identical `## Review Focus` text on the CLI outlet and the sampling outlet.
> Plus a SIXTH test the existing five omit (kimi r4): all five drive the
> job/raise branches, and the one inline test
> (`test_forge_review_with_contract_writes_tempfile`, test_mcp_server.py:595)
> only asserts the path was PASSED (`written_path is not None`), never that it
> was DELETED -- so the inline-result `_unlink` site (mcp_server.py:684) has no
> failing test and (f)'s per-site inject cannot prove it. Add a focus test that
> drives the inline path (`_run_cli_budgeted` returns a str tuple
> `("out", 0, 1.0, "")`), captures the `--focus` tmpfile path from the cli_args,
> and asserts `not os.path.exists(path)` after the call returns.
>
> Per-consumer leak tests (gm/ds r3): the FIVE tests above all exercise the
> `_dispatch_cli` scenarios, but (b) adds `"focus_tempfile_path"` to THREE
> consumer tuples and only one (`_wait_for_job`'s finally) is hit by those
> tests -- so a dropped tuple entry in the other two ships silently, and (f)'s
> per-consumer inject would have no test to fail against. Add one test each for
> the two uncovered consumers: (i) `_evict_stale` cleanup -- WRONG-KNOB WARNING
> (ds+kimi r4, independently converged): do NOT use a small `max_lifetime_s`.
> That is the `_wait_for_job` `asyncio.wait_for` cap (mcp_jobs.py:227), and on
> timeout the `_wait_for_job` finally (mcp_jobs.py:307-314) unlinks the tmpfile
> FIRST -- a DIFFERENT consumer -- so the assertion passes even if
> `"focus_tempfile_path"` was never added to `_evict_stale`'s tuple (a
> false-green that also disarms (f)'s inject for this consumer). `_evict_stale`
> gates ONLY on the module global `_JOB_TTL_SECONDS` (mcp_jobs.py:74/:345) and
> only for terminal (`completed`/`failed`) entries. Build the scenario in
> isolation: inject a `status="failed"` entry directly into `mcp_jobs._jobs`
> with a backdated `created_at` (or monkeypatch `_JOB_TTL_SECONDS` to a small
> value), carrying a real focus tmpfile, then call `_evict_stale()` directly
> (or `get_job()`, which invokes it at mcp_jobs.py:116) and assert the focus
> tmpfile is unlinked -- never route through a real job lifecycle, or the
> finally preempts and the test proves nothing. (ii) assert
> `snapshot_tempfile_paths` returns the job's `focus_tempfile_path` (this feeds
> the lifespan pre-shutdown cleanup at mcp_server.py:136). With all three
> consumers individually covered, (f)'s per-consumer inject targets a test that
> actually fails.
>
> **(f) Bug-injection (Golden Rule 2, at each fix site)**: delete one
> `_unlink(focus_tmp)` line -> the matching leak test must FAIL; restore ->
> PASS. Delete `"focus_tempfile_path"` from ONE consumer's iteration tuple ->
> that consumer's leak test must FAIL. Inject at each site separately; equal-
> looking coverage collapses the moment you do.

> ---
> **SUPERSEDED TEXT BELOW (kept for history, do NOT implement)**
> The following 3b-3, 3b-4, 3b-5 sections are obsolete -- they target a
> pre-centralization architecture that no longer exists. All three are
> replaced by the REPLAN block above.

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

**M3 fix (tmpfile leak on dispatch-error):** In `forge_review`, `_run_cli_budgeted`
(mcp_server.py:950-952) is called **outside** any try; the error cleanup at 972-977
wraps only `start_job`. A raise from `_run_cli_budgeted` leaks the contract tmpfile
today and will leak the focus tmpfile too. Same hole in the sampling-fallback branch:
`c_tmp`/`f_tmp` are created inside `_dispatch_sampling`, outside `forge_review`'s
cleanup scope. Fix: wrap tmpfile creation + dispatch in try/except with unlink-on-error
on both paths. Narrow the Acceptance claim to "start_job and dispatch-error paths" (not
all possible exceptions).

**3b-4. Tempfile dual-file ownership (MM #5 + GLM #6, verified against 8e18aa0):**
`start_job` (mcp_jobs.py:80) accepts a single `tempfile_path: str | None`. When both
contract and focus tmpfiles exist, only one can be job-transferred on timeout; the other
leaks. Fix: extend `start_job` to accept `tempfile_paths: list[str] | None = None` (new
param, old `tempfile_path` deprecated but accepted for backward compat). Update all three
cleanup sites in `forge_review`:
1. Inline success (mcp_server.py:957-961): unlink both contract_tmp and focus_tmp
   (filter None first: `[p for p in [contract_tmp, focus_tmp] if p]`)
2. Timeout job-transfer (mcp_server.py:968): pass `tempfile_paths=[p for p in [contract_tmp, focus_tmp] if p]`
3. Error cleanup (mcp_server.py:972-977): unlink both paths (same None filter)

Update `mcp_jobs.py` eviction/timeout handlers to iterate the list. When iterating,
skip None/empty paths: `for p in paths: if p: os.unlink(p)`. **All three consumers**
of `tempfile_path` in mcp_jobs.py must be updated: `_wait_for_job`'s finally block,
`_evict_stale`, **and `snapshot_tempfile_paths`** (mcp_jobs.py:120-128, which feeds
the lifespan pre-shutdown cleanup at mcp_server.py:136). If `snapshot_tempfile_paths`
is not updated, server shutdown with a running job leaks the focus tmpfile.

The migration snippet must match the actual `_jobs` dict structure (`entry.get(...)`),
not attribute access:
```python
# In _wait_for_job finally, _evict_stale, and snapshot_tempfile_paths:
paths_to_clean = [
    p for p in (
        [entry.get("tempfile_path")]
        + (entry.get("tempfile_paths") or [])
    )
    if p
]
for p in paths_to_clean:
    try:
        os.unlink(p)
    except OSError:
        pass
```

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
`_dispatch_sampling` must also load the contracts.yaml digest. Use the same safe loader
as CLI paths:
```python
# Inside _dispatch_sampling, after _build_review_context:
contracts_yaml = workspace / ".code-forge" / "contracts.yaml"
yaml_digest = ""
if contracts_yaml.is_file():
    yaml_digest = cli._safe_load_contract_digest(contracts_yaml, workspace, backend=None)
```
**M4 fix:** `load_contract_digest` already trust-gates internally (contract_loader.py:375:
untrusted → `""`), so the explicit `is_trusted_contracts` pre-check is defense-in-depth,
not the primary gate. The earlier rationale ("skipping would be a prompt-injection bypass")
is factually wrong. More importantly, the pseudocode must call `cli._safe_load_contract_digest`
(not raw `load_contract_digest`) to match CLI behavior -- `_safe_load_contract_digest`
degrades unexpected loader exceptions to `""`, while raw `load_contract_digest` would
raise through `_dispatch_sampling` where CLI would degrade -- a parity break.

**Sampling fallback preserves contract+focus (MM #4b, verified against 8e18aa0):**
On recoverable sampling failure, `_dispatch_sampling` constructs fallback CLI args
(mcp_server.py:822-823) containing only `--backend`/`--outlet`/`--committed`. The
`contract` and `focus` values are lost. Fix: thread the **raw, pre-merge** MCP
`contract` and `focus` strings into `_dispatch_sampling` as params (they are already
in scope at the `forge_review` call site, mcp_server.py:890/891), and on fallback
create tmpfiles for both before constructing `cli_args`. **Critical:** the tmpfiles
must contain the RAW MCP input values, NOT the merged `contract_spec`/`focus_spec`.
The fallback subprocess re-runs `_merge_contract_spec` / `_merge_focus_spec` inside
its own CLI path — writing merged values would cause double-merge (digest prepended
twice, confirmation-bias directive appended twice), producing a third divergent
prompt. The test must assert tmpfile **content** equals the raw params, not just flag
presence:
```python
# Inside _dispatch_sampling fallback branch (mcp_server.py:822):
if contract_raw:
    c_tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False)
    c_tmp.write(contract_raw); c_tmp.close()   # RAW, not merged contract_spec
    cli_args.extend(["--contract", c_tmp.name])
if focus_raw:
    f_tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False)
    f_tmp.write(focus_raw); f_tmp.close()       # RAW, not merged focus_spec
    cli_args.extend(["--focus", f_tmp.name])
```
Transfer both tmpfiles to the background job (use the extended `start_job` from 3b-4)
or unlink on inline success. Add a test: mock sampling LLMInvokeError(kind="truncated")
with both contract+focus present, assert CLI fallback args contain both `--contract`
and `--focus`, AND assert the tmpfile content matches the raw input (not merged).

**Tempfile lifecycle note:** The fallback path's tmpfiles (c_tmp, f_tmp) are created
inline and NOT tracked by `start_job` unless explicitly passed via the extended
`tempfile_paths` param. On the inline-success path (`_run_cli_budgeted` returns a
string, not a background task), these tmpfiles must be explicitly unlinked after the
result is returned — otherwise they leak on every sampling-fallback invocation. On the
background-job path (result is a tuple), pass them to `start_job` for eviction-managed
cleanup. The test should cover BOTH paths: inline success (assert tmpfiles unlinked)
and background job (assert tmpfiles transferred to job).

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
- **M1 fix:** `_merge_contract_spec`'s size branch is
  `if len(...) > 4096 and backend is not None:` (cli.py:1861). With `backend=None` there
  is no warning path at all -- the contract passes **silently**. Add a
  `elif len(...) > 4096 and backend is None and warn_fn:` branch that calls
  `warn_fn(...)` once. This also fixes the CLI path when no backend is configured.
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
- Existing-assert update (kimi r4 M4): 3a-2 changes the rejection message from
  "No backends configured in this gate.yaml." to "No backends or review_focus configured
  in this gate.yaml. Configure at least one." Two existing tests assert the OLD substring
  -- test_trust_empty_backends.py:51 ("No backends configured in this gate.yaml") and
  :64 ("No backends configured") -- both broken by the new message (it inserts "or
  review_focus" between "backends" and "configured"). Update both asserts to the new
  message, else Wave 3/5 goes red on an unlisted pre-existing test.
- No trust record (or a post-trust-edited / untrusted `review_focus`): focus is dropped
  with a warning by `is_trusted_focus` (NOT by `_load_gate_backends` returning `{}` --
  that coupling was the H1 bug). Backend loading is unaffected. This is a SEPARATE
  fixture from the H1 row above (untrusted BACKENDS + still-trusted focus -> focus STILL
  injects): the two assert independent trust domains and must not share a fixture.
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
   Implementation strategy: patch `code_forge.git.git_blame` to return a
   blame_map with entries lacking the 'date' key, then assert the attribution
   format doesn't crash and produces output without a date segment.

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
  warning while backends keep loading; untrusted BACKENDS with a still-trusted
  `review_focus` STILL inject focus (focus trust is independent of backend trust -- H1);
  a repo without `review_focus` keeps its existing trust record valid;
  `is_trusted_focus` short-circuits True for empty/absent focus
- Non-string `review_focus` is ignored with a warning, never coerced into the prompt
- gate.schema.json documents `review_focus`; the schema-corpus test covers it; the init
  template documents it including the re-trust requirement
- MCP sampling outlet passes contract_spec (merged 2edb9d4, confirmed via
  _dispatch_sampling :844-848), and passes MERGED specs via the same merge
  helpers as CLI-subprocess; sampling loads contracts.yaml digest with trust
  check; sampling fallback preserves contract+focus via RAW values
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
