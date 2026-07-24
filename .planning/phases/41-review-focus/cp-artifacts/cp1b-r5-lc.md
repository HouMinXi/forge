I now have all the evidence I need. Let me write up the findings.

SUMMARY: B=0 H=0 M=1 L=0

---

## Finding 1 — MEDIUM: Plan 3a-1 misreads `_merge_contract_spec`'s size guard (false parity claim)

**Location:** Plan Task 3a-1 "Merge helper" — the size-guard rationale. Real code: `src/code_forge/cli.py:1861`.

**What the plan says:**
> "Size guard: if the merged string exceeds 8192 CHARACTERS (`len(merged) > 8192`, matching `_merge_contract_spec`'s `len()` char-count guard at cli.py:1861 -- NOT bytes; 'bytes' would diverge from `len()` on non-ASCII focus text, gm r4)…"

**What the real code does** (`cli.py:1861`):
```python
if len(effective_content.encode("utf-8")) > 4096 and backend is not None:
```

The actual guard is a **byte** count (`.encode("utf-8")`) at threshold **4096** — not a `len()` char-count at 8192. I verified this directly; it is unchanged on current main.

**Why it's wrong:** The plan's stated basis for the 8192-char threshold — "matching `_merge_contract_spec`'s `len()` char-count guard at cli.py:1861" — is factually false on two axes: the unit (bytes, not chars) and the threshold (4096, not 8192). The round-4 "fix #6" entrenched this misreading: it recast "8192 bytes" → "8192 CHARACTERS (matching contract's char-count)" based on the belief that `:1861` is a char-count, when it is in fact a byte-count. So the plan now promises a parity with contract that does not exist — focus warns at 8192 chars, contract at 4096 bytes, and for non-ASCII text these diverge sharply.

**Impact:** The feature still works (warn + pass-through is safe), so this is not a leak or crash. But the rationale is a verified falsehood in a cross-reference the plan leans on to justify a design decision, and it creates a real, undocumented parity gap that future reviewers will inherit. An implementer who trusts the "matching contract" claim will not test the divergence.

**Required fix:** Re-ground the threshold rationale against the real guard. Either (a) match contract exactly — `len(merged.encode("utf-8")) > 4096` — and drop the "8192 chars" wording, or (b) keep 8192 chars but state explicitly that this is a *deliberate* divergence from contract's 4096-byte guard (and say why). Delete the claim that `:1861` is a `len()` char-count.

---

## Areas reviewed clean (no findings)

**A. REPLAN(a) dual-tmpfile lifecycle** — Traced every exit path against `mcp_server.py:664-700`. Creation try captures `.name` before write (fix #1); the dispatch try, inline-result path, and `start_job` try each unlink both `contract_tmp` and `focus_tmp`; on `start_job` success ownership transfers via the new `focus_tempfile_path=` param (REPLAN(b)). No double-unlink, no leak, no path where a created tmpfile is neither unlinked nor transferred. The docstring-update instruction (#2) accurately matches the code the plan specifies (current docstring at `mcp_server.py:654-663` is contract-only and does need the dual-lifecycle rewrite).

**B. REPLAN(e) test recipes** — `#5` (`_evict_stale`): injecting a `status="failed"` entry with a backdated `created_at` into `mcp_jobs._jobs` and calling `_evict_stale()` directly (`mcp_jobs.py:334-359`) is correctly falsifiable — `_evict_stale` only unlinks keys in its iteration tuple, so omitting `"focus_tempfile_path"` fails the assertion. Calling it directly (not via a real job) correctly avoids the `_wait_for_job` finally (`mcp_jobs.py:307-314`) pre-emption the plan warns about. `#4` (inline delete): `_run_cli_budgeted` returning `("out", 0, 1.0, "")` takes the `isinstance(result[0], str)` branch at `mcp_server.py:682`, which unlinks — the test reaches the inline path and asserts deletion.

**C. 3b(d) sampling block** — `raw_focus = focus_spec` is saved before the merge reassigns `focus_spec` (fix #3); `workspace`/`staged` are in scope at `mcp_server.py:800`; `gate_yaml_path` and the warn lambda are computed inline, mirroring the real `:847` lambda. The `warn_fn=...` in the plan is a placeholder referencing `:847` — acceptable, not a defect.

**D. Cross-reference integrity (#6–#11)** — Verified each: `#7` contracts.yaml load exists at `mcp_server.py:837-842` (correct, don't re-add); `#8` `build_sampling_l1_provider` call is at `mcp_server.py:853-857` (correct, not :765); `#9` `test_trust_empty_backends.py:51` and `:64` both assert the old message and need updating (correct); `#10` D5.7 is confirmed merged (`_dispatch_sampling` has `contract_spec` param at :806, merges at :845); `#11` `test_legacy.py:262` docstring is contract-date-only and needs the date field. All correct **except** #6, which is Finding 1 above.

The three header-rename sites (`cli.py:780`, `factories.py:281`, `factories.py:576`), the blame parser (`git.py:358-456`, fixtures at `test_git.py:272/288/309` all carry `committer-time 1700000000` → 2023-11-14), the trust anchors (`trust.py:99-122` DANGEROUS_FIELDS, `:125` is_trusted, `:161` record_trust), and the 27 "Contract Reference" occurrences (3 src + 24 tests) all check out against the plan's claims.
