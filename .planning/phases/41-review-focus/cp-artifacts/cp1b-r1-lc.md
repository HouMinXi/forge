I have all the verification I need. Let me compile my findings.

All 11 key claims about current code are **confirmed accurate**. The discrepancies I found are in the plan's *proposed* changes and stale-line-number context. Here are my findings:

```
SUMMARY: B=0 H=1 M=2 L=1
```

---

```
[HIGH] Task 3b (superseded 3b-5 block): M1 fix claims a missing elif branch that already exists
  Plan claims: "_merge_contract_spec's size branch is `if len(...) > 4096 and backend
    is not None:` (cli.py:1861). With `backend=None` there is no warning path at all --
    the contract passes silently. Add a `elif len(...) > 4096 and backend is None and
    warn_fn:` branch that calls `warn_fn(...)` once."
  Actual code: cli.py:1884 ALREADY contains the exact elif branch the plan says to add:
    `elif len(effective_content.encode("utf-8")) > 4096 and backend is None and warn_fn:
     warn_fn("contract: content exceeds 4KB but no backend available for
     summarization; injecting raw content")`. The warning path exists and fires.
  Impact: If an implementer misses the "SUPERSEDED / do NOT implement" note and applies
    the M1 fix, they would add a duplicate elif branch (SyntaxError / unreachable code)
    or, believing the warning silent, add a redundant warn. The active REPLAN block
    (d) does NOT include the M1 fix, so the live implementation path is unaffected — but
    the plan document asserts a falsehood about the current code that could mislead a
    reader into "fixing" something already correct.
  Suggestion: Delete the M1 fix paragraph, or rewrite it as historical commentary
    noting the elif was already present on main @ ca0d860 (it predates the reconcile).
```

```
[MEDIUM] Task 1.3: "factories.py:576 is UNREACHABLE in production" is stale
  Plan claims: "factories.py:576 is currently UNREACHABLE in production -- its only
    caller (mcp_server.py:765) passes no contract_spec (see 41-CONTEXT D5.7). It can
    therefore only be covered by a direct unit test of the builder, never end-to-end."
  Actual code: The plan's own RECONCILE header states D5.7 was MERGED (2edb9d4). On
    main @ ca0d860, `_dispatch_sampling` DOES pass contract_spec into
    `build_sampling_l1_provider` (mcp_server.py:853-858, `contract_spec=contract_spec`),
    and `forge_review` passes `contract_spec=contract` into `_dispatch_sampling`
    (:1003). So factories.py:576 IS reachable: forge_review(contract=..., outlet=sampling)
    -> _dispatch_sampling -> build_sampling_l1_provider emits "## Contract Reference".
  Impact: The plan's "never end-to-end / direct unit test only" guidance is wrong.
    An e2e assertion over the sampling path IS now possible (and test_mcp_server.py
    already has one at :2235, "forge_review(contract=...) passes contract_spec").
    Following the stale guidance would leave the rename at factories.py:576
    under-tested relative to what the suite actually supports.
  Suggestion: Drop the "unreachable" caveat. State that factories.py:576 is reachable
    via the sampling outlet (post-D5.7 merge) and may be covered e2e; point at the
    existing test_mcp_server.py:2235 pattern. Keep a direct-unit-test fallback.
```

```
[MEDIUM] Task 3a-3: "_dispatch_sampling (mcp_server.py:735) does NOT currently hold
  gate_data" is stale (line number + a misleading implication)
  Plan claims: "For the sampling path, `_dispatch_sampling` (mcp_server.py:735) does
    NOT currently hold `gate_data` -- it must call `cli._load_gate_backends` itself to
    extract `review_focus`."
  Actual code: `_dispatch_sampling` is now at mcp_server.py:800 (was :735), confirmed.
    It does not currently hold gate_data, so the substance is correct. But the line
    number :735 is stale, and more importantly the plan frames this as a NEW read the
    sampling path "must" add, while the merged code already calls
    `cli._safe_load_contract_digest` (:840) and `cli._merge_contract_spec` (:845) inside
    `_dispatch_sampling` -- i.e. the sampling path ALREADY reaches into cli.py privates
    and already does per-call YAML/digest work. The "must call _load_gate_backends
    itself" instruction is consistent with an existing pattern, not a novel reach-in,
    but the plan's prose ("the MCP sampling path has no equivalent", Task 3b-5 GLM #1)
    overstates the gap.
  Impact: Low implementation risk (the instruction is still valid), but an implementer
    may be puzzled about whether to add a second `_load_gate_backends` call or reuse
    the existing digest block. The existing :837-848 block is the natural insertion
    point for the review_focus extraction, not a fresh standalone read.
  Suggestion: Update line number to :800. Clarify that review_focus extraction should
    be added alongside the existing contracts_yaml/digest block at :837-848, reusing
    the same `workspace / ".code-forge"` resolution, not as a separate gate_data load.
```

```
[LOW] Task 3b-2: cross-repo contract "gap" framing vs. the merged state
  Plan claims: "`run_cross_repo` loads its contract internally (cross_repo.py:250-256:
    digest only, no `--contract` file, no `_merge_contract_spec`)" and calls this a
    "pre-existing gap" / "silent no-op" for `--contract FILE`, explicitly out of scope.
  Actual code: cross_repo.py:250-257 confirms `_contract_spec` is loaded via
    `load_contract_digest(...)` only; `--contract FILE` is indeed not threaded into
    cross-repo. The claim is accurate. The minor inaccuracy: the plan lists this under
    "3b-2" (live) but the same "D5.7 gap" language appears in the SUPERSEDED 3b-5
    block, creating slight confusion about whether cross-repo focus-threading is
    deliberately avoiding the gap (it is) or accidentally replicating it.
  Impact: None to implementation -- the plan correctly scopes cross-repo contract work
    as out-of-scope and threads focus_spec directly to build_l1_provider. Only the
    cross-referencing between the live 3b-2 and the superseded 3b-5 is muddled.
  Suggestion: Add one line to 3b-2: "Cross-repo contract loading (digest-only at
    cross_repo.py:250) is unchanged and out of scope; focus threads via the same
    build_l1_provider param the contract already uses." This closes the loop.
```

---

**Summary of verification of the 11 key claims:** all 11 are accurate against main @ ca0d860. Specifically confirmed by reading the actual source: `_dispatch_cli` contract param + 3 unlink paths (mcp_server.py:647-700); `start_job` keyed tempfile storage + the exact 3 consumers iterating `("tempfile_path", "stderr_log_path")` (mcp_jobs.py:120/308/353); `_dispatch_sampling` `contract_spec` + `_merge_contract_spec` merge (mcp_server.py:800-858); `forge_review` dual dispatch (mcp_server.py:971-1027); `forge_gate_check` omitting contract (mcp_server.py:1059); `_merge_contract_spec` at cli.py:1828; `_make_subagent_spawn` + both builders' `contract_spec` params; the three cross-repo functions; and the sampling fallback `contract=raw_contract` at mcp_server.py:917. Test-file claims (fixtures with `committer-time 1700000000`, existing contract-wiring tests at test_mcp_server.py:2146+, the 5 `_dispatch_cli` tmpfile tests) and the timestamp→2023-11-14 conversion all check out.

The four findings above are all in the plan's *proposed-change / prose* layer, not in its reading of the current code. The active REPLAN block (a)-(f) is internally consistent with the codebase; only the superseded 3b-5 history block contains a definitively false claim about the code (the missing-elif M1 fix).
