I have enough grounding. Let me write up the review.

**Verified claims from the plan against real code:**
- `cli.py:118-161` — `_load_gate_backends` returns `([], {})` when untrusted ✓
- `cli.py:1078` — `forge_gate_check` does call `_dispatch_cli` (non-sampling path) ✓
- `mcp_server.py:647-700` — `_dispatch_cli` has 4 cleanup sites (raise / inline / transfer / start_job-raise) ✓
- `mcp_server.py:917` — `_dispatch_sampling` fallback calls `_dispatch_cli` with `contract=raw_contract` ✓
- `mcp_server.py:837-839` — `contracts_yaml` loading gated on `not staged` ✓
- `mcp_jobs.py:80, 120, 307, 349` — `start_job` + 3 consumers iterate `("tempfile_path", "stderr_log_path")` ✓
- `trust.py:125, 132, 156-171` — `is_trusted` keyed by `str(path.resolve())`, `record_trust` does plain replace ✓
- `trust.py:243, 271, 289, 302` — contracts hash pattern at separate key `contracts_hash` ✓
- `cli.py:2063, 2422` — the two contract merge sites (outlet c, outlet a) ✓
- `cli.py:2185` — `_run` calls `_load_gate_backends` once ✓

**H1 fix verification:**

1. **Decouples focus from backend trust** — YES. `_load_gate_yaml_raw` does no trust check; `is_trusted_focus` gates only on focus_hash.
2. **`is_trusted_focus` receives real parsed dict** — YES. `focus_gd = _load_gate_yaml_raw(gate_yaml_path)` then `is_trusted_focus(gate_yaml_path, focus_gd)`; `hash_focus_text(focus_gd)` extracts `review_focus` for the hash.
3. **Absent / empty / non-dict / corrupt behavior** — Mirrors `_load_gate_backends`'s parse prefix: `FileNotFoundError → {}`, `YAMLError → CliError`, non-dict `→ {}`. Diverges from the contract loader's "safe" pattern (which returns "" on exception) — by design, since the plan explicitly says "Mirrors _load_gate_backends's parse prefix (same YAMLError → CliError) so both readers agree on syntax errors." Worth noting: this means an MCP sampling path with a corrupt gate.yaml will propagate CliError rather than degrade — but `load_outlet_from_gate` already raises ValueError on YAMLError at mcp_server.py:989, so this is consistent with existing MCP behavior.
4. **`_load_gate_backends` untouched** — Per plan ("do NOT fold it in").
5. **All focus-reading paths use `_load_gate_yaml_raw`** — Plan claims CLI `_run`, CLI-subprocess fork (same code), and `_dispatch_sampling`. The plan text DOES NOT show the explicit `_load_gate_yaml_raw` call inside `_dispatch_sampling`; only the IMPORTANT note says "the yaml_focus extraction must be conditional: only load and merge `review_focus` from gate.yaml when `staged=False`". An implementer must infer the load block parallel to the contracts.yaml loading at mcp_server.py:837-839.
6. **Bug-inject guard** — Correctly specified: untrusted backends + trusted focus → focus must still appear; reverting to `gate_data` from `_load_gate_backends` makes the test fail.

**H1 fix is correct.**

---

**Going through the rest of the plan for NEW issues:**

Task 1 header rename — 3 sites identified correctly (`cli.py:780`, `factories.py:281`, `factories.py:576`); per-site bug-inject required.

Task 2 git_blame date — parser change detailed with 8 sub-steps; tests updated.

Task 3a — H1 fix verified above. `record_trust` change to merge-first is well-bounded. `code-forge trust` extension to accept `review_focus`-only gate.yaml is straightforward.

Task 3b — REPLAN block correctly addresses the post-centralization architecture. The key constants `_dispatch_cli`, `_dispatch_sampling`, `start_job`, and the 3 tempfile consumers all verified.

Task 3c — Test matrix is exhaustive; per-path bug-inject required.

Task 4 — Trivial.

---

**One thing worth flagging that I'd call a Low (spec gap, not a bug):**

The plan's H1 fix text says "All three focus paths use `_load_gate_yaml_raw`" but the explicit `_load_gate_yaml_raw` call site is shown ONLY for the CLI merge sites (`focus_spec = _merge_focus_spec(yaml_focus, file_content, warn)` at `cli.py:2063` and `cli.py:2422`). For `_dispatch_sampling`, the plan only states the IMPORTANT note about gate-check isolation and the staged guard, but never shows the actual `_load_gate_yaml_raw(gate_yaml_path)` call inside `_dispatch_sampling` — just like the contracts.yaml load at mcp_server.py:837-839, the implementer is expected to mirror the pattern. This is a documentation gap, not a code bug — but it's worth calling out so the implementer doesn't miss the load block.

---

SUMMARY: B=0 H=0 M=0 L=1

[L] Task 3a-3: sampling in-process yaml_focus load is implicit, not shown
  Location: plan 41-PLAN.md:301-355 (H1 fix), esp. the "All three focus paths use `_load_gate_yaml_raw`" sentence
  Description: The H1 fix text describes `_load_gate_yaml_raw(gate_yaml_path)` and the extraction block, but only shows the call shape for the CLI merge sites (Task 3a-4). For `_dispatch_sampling`, the plan never shows the parallel `focus_gd = cli._load_gate_yaml_raw(gate_yaml_path)` block that must mirror the existing `yaml_digest` load at mcp_server.py:837-839 (gated on `not staged`, calling `cli._is_trusted_focus(gate_yaml_path, focus_gd)`). The IMPORTANT gate-check isolation note states the policy but not the code. An implementer following only the plan text could ship a sampling path that still reads focus from `_load_gate_backends`'s trust-gated dict — exactly the bug H1 was written to fix.
  Required fix: Add an explicit yaml_focus load block to the plan's H1 fix section, parallel to the mcp_server.py:837-839 contracts.yaml block, calling `cli._load_gate_yaml_raw(gate_yaml_path)` inside `if not staged and gate_yaml_path.is_file():` and gating via `cli._is_trusted_focus(gate_yaml_path, focus_gd)`.

Otherwise the plan is clean and ready for CP1.
