# Phase 41: Sampling contract_spec wiring (D5.7)

## Goal

Fix a pre-existing bug: `build_sampling_l1_provider` (factories.py:507) accepts
`contract_spec` and injects it (factories.py:575-576), but its only caller
`_dispatch_sampling` (mcp_server.py:765) passes no `contract_spec`. So `--contract`
/ MCP `contract` is a silent no-op on the MCP sampling outlet. This also blocks
Phase 41b (review focus), which needs the sampling path to pass merged specs for
parity testing.

Disclosed per CLAUDE.md pre-existing-bug rule: this is NOT a new bug introduced
by Phase 41; it ships with its own explanation commit.

## Must-Haves

- `_dispatch_sampling` accepts and threads `contract_spec` to
  `build_sampling_l1_provider`
- NO `focus_spec` parameter is added in this phase (D7) — Phase 41b adds it
  together with its consumer
- Sampling path loads contracts.yaml digest (same safe loader as CLI paths)
- Sampling fallback on LLMInvokeError preserves contract in CLI args
- `forge_gate_check` sampling dispatch passes no contract (assert it stays
  empty at the MCP parameter level; the sampling prompt will NOT contain the
  contracts.yaml digest — staged=True skips digest loading, matching CLI
  gate-check behavior where `run_gate_check()` does not load contracts.yaml)
- Full test coverage: direct builder test + end-to-end sampling path + fallback

## Ground-truth (verified 2026-07-20 against main @ 8e18aa0)

| What | Where | Evidence |
|---|---|---|
| `build_sampling_l1_provider` accepts `contract_spec` | factories.py:514 | param exists, defaults to "" |
| `build_sampling_l1_provider` injects it | factories.py:575-576 | `if contract_spec: prompt += "\n## Contract Reference\n" + contract_spec + "\n"` |
| `_dispatch_sampling` calls it without contract | mcp_server.py:765-769 | `build_sampling_l1_provider(session=session, loop=loop, resolved=resolved)` — no contract kwarg |
| `_dispatch_sampling` signature | mcp_server.py:735-741 | params: session, committed, workspace, backend_name, staged — no contract/focus |
| `forge_review` has `contract` in scope | mcp_server.py:890 | `contract: str = ""` param |
| `forge_review` calls `_dispatch_sampling` without it | mcp_server.py:914-920 | no contract kwarg passed |
| `forge_gate_check` calls `_dispatch_sampling` | mcp_server.py:1009-1015 | staged=True, no contract/focus — correct |
| `_build_review_context` loads no contracts.yaml | mcp_server.py:648-678 | only baseline/diff/source_hash |
| CLI-subprocess merge helper | cli.py:1828 | `_merge_contract_spec(yaml_digest, file_content, backend, warn_fn)` |
| Sampling fallback drops contract+focus | mcp_server.py:822-823 | fallback cli_args: only `--backend`/`--outlet`/`--committed` |
| mcp_server already calls cli privates | mcp_server.py:243 | `cli._load_gate_backends(gate_yaml_path)` — established pattern |
| `import sys` already at module level | mcp_server.py:23 | no need for redundant function-level import |
| Existing lazy `from code_forge import cli` | mcp_server.py:237, 283, 404 | per-file convention in `_backend_names_for` and siblings |
| `_safe_load_contract_digest` exists | cli.py:1801-1825 | `(contracts_yaml, cwd, backend)` — 3 params, no warn_fn. Degrades MOST exceptions to "" internally, but **re-raises MemoryError by design** (cli.py:1817-1819, commit 8e18aa0: "a PASS reached without contract context is worse than a hard failure"). See D6. |
| `load_outlet_from_gate` exists | outlet_resolver.py:126 | returns `"sampling"` / `"inline"` / `"subprocess"` based on gate.yaml |
| CLI gate-check does NOT load contracts.yaml | cli.py:1452-1457, gate_check.py | `gate-check` routes to `run_gate_check()`, not `_run`; `grep contract/digest gate_check.py` = 0 hits |
| CLI review loads contracts.yaml | cli.py:2416-2424 | inside `_run()` — review path only, NOT gate-check path |

## Design decisions

### D1: Fix in this phase, not deferred
The sampling contract gap blocks Phase 41b's wiring-parity test (sampling must
pass merged specs via the same helpers as CLI-subprocess). Fixing it now means
41b's acceptance criteria are testable from day one.

### D2: Sampling path calls same merge helpers as CLI
Raw pass-through of the MCP `contract` string would skip `_merge_contract_spec`
(digest merge, `## Do NOT Flag` split, >4KB summarization, confirmation-bias
directive), producing two different prompts for the same MCP input depending on
outlet. LOCKED: the sampling path calls `cli._merge_contract_spec` and
(when Phase 41b lands) `cli._merge_focus_spec` with `backend=None`.

### D3: backend=None on sampling is deliberate
Sampling exists because the client has no API key. With `backend=None`,
`_merge_contract_spec`'s size branch (`len > 4096 and backend is not None`) is
not taken. Add a `elif len > 4096 and backend is None and warn_fn` branch to
emit a warning. This also fixes the CLI path when no backend is configured.

### D4: Separate commit, not folded into focus
Per CLAUDE.md pre-existing-bug rule: "fix it in this PR with a clearly separate
commit and explanation." The sampling contract fix lands first; Phase 41b's focus
wiring lands after.

### D5: Merge location — raw-in, merge-inside
`forge_review` passes the RAW MCP `contract` string to `_dispatch_sampling`.
The merge (digest loading + `_merge_contract_spec`) happens INSIDE
`_dispatch_sampling`, which saves `raw_contract = contract_spec` before the
merge for fallback tmpfile use. This architecture is:
- Self-contained: digest loading only needs `workspace` (already in scope)
- Fallback-safe: raw value preserved for tmpfile without threading two values
- Forward-compatible: 41b adds `focus_spec` with the same pattern

41b's written shapes ("pass the MERGED focus spec down through
`_dispatch_sampling`") are superseded by this decision — 41b passes raw focus,
merge happens inside `_dispatch_sampling`. See "Supersedes in 41b" section.

### D7: NO speculative `focus_spec` parameter in this phase
Added by PM arbitration 2026-07-21, reversing the earlier "thread focus_spec
through the signature only, 41b wires it to the builder" shape.

That shape reproduces the exact defect this phase exists to remove. D5.7 is:
the builder HAS a `contract_spec` param, the caller never passes it, so input
is silently discarded. Pre-adding `focus_spec` to `_dispatch_sampling` without
forwarding it to the builder gives: the dispatcher HAS a `focus_spec` param,
the dispatcher never forwards it, so input is silently discarded. Same class,
one layer up.

It also produces a green check on a no-op: Task 1's original verify step
(`grep -n "contract_spec\|focus_spec"` must show the param at all 3 sites)
PASSES while focus is inert, which is the false-green shape forge exists to
prevent.

LOCKED: Phase 41 touches `contract_spec` only. Phase 41b adds `focus_spec` to
`_dispatch_sampling` AND to `build_sampling_l1_provider` in one change, so the
parameter and its consumer land together and the bug-inject proof is meaningful.
Cost of the reversal: 41b touches one extra signature line. Cost of keeping it:
a parameter whose only observable behavior is discarding its input.

### D6: MemoryError propagates on the sampling path — no new handler
Added by PM arbitration 2026-07-21. An earlier ground-truth row correctly noted
that `_safe_load_contract_digest` re-raises MemoryError; a mid-review correction
to that row's parameter list deleted the caveat and replaced it with an
unqualified "errors degraded to ''". That is wrong: cli.py:1817-1819 is
`except MemoryError: raise`, added deliberately by commit 8e18aa0.

Where it lands: Task 2 inserts the digest load in the `_dispatch_sampling`
prologue (between mcp_server.py:760 and :765), which has NO try/except — the
only handler in the function catches `LLMInvokeError` further down (~:799),
after the insertion point. So a MemoryError propagates out of the MCP tool
handler.

LOCKED: that is CORRECT and gets no new code. The property 8e18aa0 protects is
"never return a verdict without contract context you were supposed to have".
Propagation preserves it — control never reaches the merge or the builder, so
no review runs and no PASS is emitted. Wrapping it in a `ToolError` was
considered and REJECTED: it buys a nicer message while doing string formatting
and allocation on a path that just ran out of memory, and the safety property is
already held. What this phase owes is not a handler but a TEST that pins the
behavior, so a future "let's make this robust" refactor cannot quietly convert
it into `yaml_digest = ""` + a green review (Task 5 case 7).

## Tasks

### Task 1: Thread contract_spec through _dispatch_sampling
**files:** src/code_forge/mcp_server.py

**action:**

1. Add `contract_spec: str = ""` to `_dispatch_sampling` signature
   (mcp_server.py:735-741), after `staged`. Do NOT add `focus_spec` here — see D7:

   ```python
   async def _dispatch_sampling(
       session,
       committed: bool,
       workspace: Path,
       backend_name: str | None = None,
       staged: bool = False,
       contract_spec: str = "",   # NEW
   ) -> CallToolResult:
   ```

2. Pass `contract_spec` to `build_sampling_l1_provider` (mcp_server.py:765-769):

   ```python
   l1_provider = build_sampling_l1_provider(
       session=session,
       loop=loop,
       resolved=resolved,
       contract_spec=contract_spec,
   )
   ```

   Note: focus is entirely out of scope here. Phase 41b adds `focus_spec` to
   BOTH `_dispatch_sampling` and `build_sampling_l1_provider` in one change,
   so the parameter and its consumer land together (D7).

3. In `forge_review` (mcp_server.py:888-920), pass `contract` to
   `_dispatch_sampling`:

   ```python
   return await _dispatch_sampling(
       session=ctx.session,
       committed=committed,
       workspace=workspace,
       backend_name=backend,
       staged=False,
       contract_spec=contract,   # NEW
   )
   ```

4. In `forge_gate_check` (mcp_server.py:1009-1015), leave both at default "".
   Add an assertion comment:

   ```python
   # gate-check has no contract concept — contract_spec stays empty.
   # Asserted by test_gate_check_no_contract.
   return await _dispatch_sampling(
       session=ctx.session,
       committed=False,
       workspace=workspace,
       backend_name=backend,
       staged=True,
       # contract_spec intentionally omitted
   )
   ```

**verify:** `grep -n "contract_spec" src/code_forge/mcp_server.py` shows the
param at the signature, the builder call, and the `forge_review` call site.
Then `grep -n "focus" src/code_forge/mcp_server.py` must return NOTHING new
from this phase (D7 — focus is 41b's).

Note: grep alone cannot prove the value ARRIVES; it only proves the token is
present. The arriving-value proof is Task 5 case 2 (end-to-end prompt content)
plus its bug-inject: delete the `contract_spec=contract` kwarg at the
`forge_review` call site, watch case 2 FAIL, restore, watch it PASS. Injecting
inside the builder instead would leave the wiring untested — the wiring IS the
bug being fixed.

**done:** contract_spec threaded from forge_review → _dispatch_sampling → build_sampling_l1_provider, proven by an end-to-end assertion, not by grep.

---

### Task 2: Load contracts.yaml digest in _dispatch_sampling
**files:** src/code_forge/mcp_server.py

**action:**

After `_build_review_context` (mcp_server.py:760) and before the
`build_sampling_l1_provider` call (mcp_server.py:765), load the
contracts.yaml digest using the same safe loader as CLI paths.

**CRITICAL: save raw value first.** The raw MCP `contract_spec` must be
preserved for Task 3's fallback tmpfile (which writes raw, not merged —
the fallback subprocess re-runs its own merge). Add `raw_contract = contract_spec`
BEFORE the merge block.

**CRITICAL: staged guard.** Digest loading is conditional on `not staged`.
When `staged=True` (gate-check), skip digest loading entirely — CLI
gate-check (`run_gate_check()` at cli.py:1452-1457) does NOT load
contracts.yaml (grep in gate_check.py = 0 hits). Unconditional loading
would create outlet divergence: sampling gate-check would have contract
context while CLI gate-check would not. This matches D2's intent.

```python
# Lazy import per file convention (see _backend_names_for at line 237).
from code_forge import cli

# Save raw MCP value before merge (Task 3 fallback writes raw, not merged)
raw_contract = contract_spec

# Load contracts.yaml digest — review path only (not gate-check).
# CLI gate-check (run_gate_check) does NOT load contracts.yaml;
# unconditional loading would create outlet divergence (D2).
contracts_yaml = workspace / ".code-forge" / "contracts.yaml"
yaml_digest = ""
if not staged and contracts_yaml.is_file():
    yaml_digest = cli._safe_load_contract_digest(
        contracts_yaml, workspace, backend=None
    )
```

Note: `sys` is already imported at module level (mcp_server.py:23); no
function-level `import sys` needed.

Then merge with the MCP `contract` param:

```python
if contract_spec or yaml_digest:
    contract_spec = cli._merge_contract_spec(
        yaml_digest, contract_spec, backend=None,
        warn_fn=lambda msg: print(msg, file=sys.stderr)
    )
```

`_safe_load_contract_digest` degrades MOST loader exceptions to "" (matching
CLI behavior) but re-raises MemoryError (cli.py:1817-1819). Do NOT wrap this
call in a try/except: the propagation is the designed behavior and D6 explains
why the sampling path keeps it. Verified: `load_contract_digest` trust-gates
internally (contract_loader.py:369-377, `if not is_trusted_contracts(...):
return ""`) against resolved spec CONTENT, independent of `backend`, so
`backend=None` does NOT bypass trust verification.

Digest loading is conditional on `not staged`. Gate-check (`staged=True`)
skips digest loading, matching CLI gate-check behavior (run_gate_check at
cli.py:1452 does NOT load contracts.yaml). Review (`staged=False`) loads
the digest, matching CLI review behavior (cli.py:2416-2424 inside `_run`).
This is NOT a behavior change — it is parity with the existing CLI paths.

`warn_fn` uses `print(..., file=sys.stderr)` instead of `warnings.warn`
because `warnings` is not imported in mcp_server.py and the existing
pattern (e.g. `_safe_load_contract_digest`) uses stderr output.

**verify:** unit test with a mock contracts.yaml in workspace/.code-forge/
verifies the digest appears in the sampling prompt.

**done:** sampling path loads and merges contracts.yaml digest via same helpers as CLI.

---

### Task 3: Sampling fallback preserves contract
**files:** src/code_forge/mcp_server.py

**action:**

On recoverable sampling failure (mcp_server.py:801-836), the fallback CLI args
(mcp_server.py:822-823) currently contain only `--backend`/`--outlet`/`--committed`.
The `contract_spec` value is lost.

Fix: thread the **raw, pre-merge** MCP `contract` string (saved as `raw_contract`
in Task 2) into the fallback as a temp file, following the exact pattern from
`forge_review` (mcp_server.py:935-978):

```python
# Inside _dispatch_sampling fallback branch, after cli_args construction:
raw_contract_tmp: str | None = None
if raw_contract:
    # Write raw MCP contract to tmpfile for CLI subprocess
    # (CLI subprocess will run its own _merge_contract_spec)
    c_tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    )
    c_tmp.write(raw_contract)  # RAW, not merged
    c_tmp.close()
    raw_contract_tmp = c_tmp.name
    cli_args.extend(["--contract", raw_contract_tmp])
```

**Critical:** the tmpfile must contain the RAW MCP `contract` value (`raw_contract`),
NOT the merged `contract_spec`. Task 2 saves `raw_contract = contract_spec` before
the merge for this purpose. The fallback subprocess re-runs `_merge_contract_spec`
inside its own CLI path — writing merged values would cause double-merge.

Focus has no fallback story here because focus does not exist in this phase
(D7). Phase 41b adds the `--focus` CLI flag, the merge helper, and the
fallback tmpfile together — and must also extend `start_job`, which today
accepts a SINGLE `tempfile_path` (mcp_jobs.py:80-85), verified.

**Inline success path** — mirror `forge_review` (mcp_server.py:957-961):

```python
if isinstance(result[0], str):
    stdout, exit_code, elapsed, stderr = result
    if raw_contract_tmp:
        try:
            os.unlink(raw_contract_tmp)
        except FileNotFoundError:
            pass
    return _make_result(stdout, exit_code, elapsed, stderr)
```

**Timeout path** — transfer tmpfile ownership to job, mirror
`forge_review` (mcp_server.py:967-978):

```python
else:
    inner_task, proc, stderr_path = result
    cap = _job_cap_s(workspace, backend_name or "")
    try:
        job_id = start_job(inner_task, proc,
                           tempfile_path=raw_contract_tmp,
                           stderr_log_path=stderr_path,
                           max_lifetime_s=cap)
    except Exception:
        for p in (raw_contract_tmp, stderr_path):
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass
        raise
    return _make_job_ref(job_id)
```

Note: `start_job` accepts `tempfile_path` (mcp_jobs.py:80-83) and transfers
ownership — `_wait_for_job` finally block (mcp_jobs.py:307-314) cleans up on
normal termination. The try/except above handles the case where `start_job`
itself throws before the job is registered (file would never enter the job
dictionary, so `_wait_for_job` cleanup would be unreachable).

**verify:** mock sampling LLMInvokeError(kind="truncated") with contract present,
assert CLI fallback args contain `--contract` AND the tmpfile content equals
the raw input (not merged). Also mock `start_job` raising an exception, assert
tmpfile and stderr log are cleaned up.

**done:** sampling fallback preserves contract in CLI args; tmpfile lifecycle correct on all paths.

---

### Task 4: _merge_contract_spec warning for backend=None + >4KB
**files:** src/code_forge/cli.py

**action:**

Add a warning branch to `_merge_contract_spec` (cli.py:1861) for the case
when `backend is None` and the merged content exceeds 4KB:

```python
if len(effective_content.encode("utf-8")) > 4096 and backend is not None:
    # existing summarization path
    ...
elif len(effective_content.encode("utf-8")) > 4096 and backend is None and warn_fn:
    warn_fn(
        "contract: content exceeds 4KB but no backend available "
        "for summarization; injecting raw content"
    )
```

This ensures the sampling path (and the CLI path without a backend) gets a
warning instead of silently passing a large contract.

**verify:** unit test: `_merge_contract_spec("", "x" * 5000, backend=None,
warn_fn=mock)` → warning called, full content returned (not truncated).

**done:** backend=None + >4KB emits warning; content not truncated.

---

### Task 5: Tests
**files:** tests/test_mcp_server.py (or new test file)

**action:**

1. **Direct builder test:** call `build_sampling_l1_provider` with
   `contract_spec="Test contract body"` and verify the returned
   prompt contains `"\n## Contract Reference\nTest contract body"` (the
   injection at factories.py:575-576). Use neutral content — "## Design
   Intent" is not yet renamed (41b Task 1 does that).

2. **End-to-end sampling path:** mock `code_forge.llm_invoke.invoke_sampling` to
   capture the prompt, call `forge_review(contract="test contract")` with sampling
   outlet, assert the captured prompt contains "## Contract Reference" +
   the contract text.

   **Outlet setup:** patch `load_outlet_from_gate` to return `"sampling"` in this
   test (cleanest — no environment variable residue, matches the pattern used by
   existing `_sampling_dispatch_patches` helper which patches `_dispatch_sampling`
   directly). Alternatively, set `FORGE_OUTLET=sampling` in the test environment
   and clean up afterward.

3. **forge_gate_check no-contract:** call `forge_gate_check()` with sampling
   outlet, assert no contract MCP parameter is passed to
   `_dispatch_sampling` AND no contracts.yaml digest is loaded (staged=True
   skips digest loading — matching CLI gate-check behavior where
   `run_gate_check()` does NOT load contracts.yaml).

4. **Sampling fallback:** mock `LLMInvokeError(kind="truncated")` with
   contract present, assert fallback CLI args contain `--contract` and
   the tmpfile content equals the raw MCP input.

5. **Contracts.yaml digest:** create workspace with `.code-forge/contracts.yaml`,
   call sampling path, assert digest appears in prompt.

6. **Warning for >4KB + no backend:** `_merge_contract_spec("", "x" * 5000,
   backend=None, warn_fn=mock)` → warning called, full content returned
   (not truncated). Assert both: `warn_fn.assert_called_once()` AND
   `result.startswith("x" * 5000)` to verify content is not truncated.

7. **MemoryError is not swallowed (D6):** patch
   `cli._safe_load_contract_digest` to raise `MemoryError`, call the sampling
   path, and assert the MemoryError PROPAGATES — i.e. `pytest.raises(MemoryError)`
   — and that no review result is produced. This pins 8e18aa0's property on the
   new path: the failure mode a later refactor would introduce is
   `yaml_digest = ""` plus a green review, which this test makes impossible to
   land silently. Assert propagation, NOT a specific message.

**verify:** `python3 -B -m pytest tests/test_mcp_server.py -v -k sampling`

**done:** all 7 test cases pass.

## Acceptance

- `build_sampling_l1_provider` receives `contract_spec` from `_dispatch_sampling`
  (not the empty default)
- Sampling path loads contracts.yaml digest via `cli._safe_load_contract_digest`
  and merges via `cli._merge_contract_spec` (review path only — staged=False)
- Sampling fallback creates a tmpfile with RAW contract content and passes
  `--contract <path>` to the CLI subprocess
- Tmpfile lifecycle correct on all 3 paths: inline success (unlink),
  timeout (start_job ownership transfer), start_job failure (try/except cleanup)
- `forge_gate_check` sampling dispatch passes no contract MCP parameter
  AND no contracts.yaml digest (staged=True skips digest loading — matching
  CLI gate-check behavior where `run_gate_check()` does not load contracts.yaml)
- `_merge_contract_spec` with `backend=None` + >4KB content emits a warning
- NO `focus_spec` parameter exists anywhere in this phase's diff (D7):
  `git diff | grep focus` returns nothing
- MemoryError from the digest loader propagates rather than degrading to an
  empty digest + green review (D6), pinned by a test
- Wiring proven end-to-end by bug-inject at the `forge_review` call site, not
  by grep presence
- All 7 test cases pass
- Full test suite: `python3 -B -m pytest tests/ -q` — zero regressions

## Supersedes in 41b

Phase 41 completes most of 41b Task 3b-5's scope. The following items
are DONE by Phase 41 and should be removed/reworded in 41b:

| 41b 3b-5 item | Status in Phase 41 | Action needed in 41b |
|---|---|---|
| `_dispatch_sampling` gains `contract_spec` | DONE Task 1 (contract only) | Remove from 41b; 41b still adds `focus_spec` per D7 |
| contracts.yaml digest loading | ✅ Task 2 (with staged guard) | Remove from 41b |
| `_safe_load_contract_digest` usage | ✅ Task 2 | Remove from 41b |
| contract merge via `_merge_contract_spec` | ✅ Task 2 | Remove from 41b |
| fallback contract tmpfile | ✅ Task 3 | Remove from 41b |
| M1 >4KB backend=None warning | ✅ Task 4 | Remove from 41b |
| separate commit requirement | ✅ D4 (Phase 41 IS that commit) | Remove from 41b |
| focus_spec threading through signature | NOT done — reversed by D7 | 41b adds the param AND its consumer in one change |

**Remaining for 41b:** add `focus_spec` to `_dispatch_sampling` AND to
`build_sampling_l1_provider` in the same change (D7), add the `_merge_focus_spec`
helper, wire the MCP `focus` param, extend `start_job` for dual-file tempfile
ownership (it accepts a single `tempfile_path` today, mcp_jobs.py:80-85), and
rename "## Contract Reference" to "## Design Intent" in all 3 builders.

**Also inherited by 41b:** the `review_focus` gate.yaml trust boundary (41b
CONTEXT D5.6). Phase 41 does not touch it — the contract half is already
trust-gated inside `load_contract_digest` (contract_loader.py:369-377), but
focus has no equivalent gate until 41b builds one.

**Merge location (D5):** Phase 41 establishes raw-in/merge-inside architecture.
41b's written shapes ("pass the MERGED focus spec down through
`_dispatch_sampling`") are superseded — 41b passes raw focus,
merge happens inside `_dispatch_sampling`.

## Depends on

- Phase 40 (merged 25b063e) — no file conflict (Phase 40 touched
  state.py/sarif.py/receipt.py/outlet_c.py, not mcp_server.py factories.py)
