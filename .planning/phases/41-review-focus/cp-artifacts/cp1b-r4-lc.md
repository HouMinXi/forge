I've now verified all four focus areas against the real codebase. Let me compile my findings.

SUMMARY: B=0 H=0 M=1 L=0

The plan is substantially correct — all 11 round-3 fixes check out against the real source, and the four focus areas (A–D) are sound. I found one minor finding.

---

**Finding 1 (Medium): `_load_trusted_yaml_focus` is not importable from `cli` at the point `_dispatch_sampling` calls it**

- **Location:** Plan 3b(d) "Explicit load block" + real `mcp_server.py:840-848`
- **Description:** The plan's 3b(d) block has `_dispatch_sampling` call `cli._load_trusted_yaml_focus(gate_yaml_path, ...)`. The helper is defined in `cli.py` (3a-3 places it next to `_load_gate_backends` at cli.py:118). At mcp_server.py:829 the code does `from code_forge import cli`, so `cli._load_trusted_yaml_focus` is reachable — **this is fine**.

  However, the plan's 3b(d) block computes `gate_yaml_path = workspace / ".code-forge" / "gate.yaml"` and then calls `cli._load_trusted_yaml_focus(gate_yaml_path, lambda msg: ...)`. The inline warn lambda at mcp_server.py:847 is `lambda msg: (sys.stderr.write(msg + "\n"), sys.stderr.flush())` — the plan mirrors this exactly. **This is fine.**

  The actual issue: the plan's 3b(d) shows the yaml_focus load block but **omits the `raw_focus = focus_spec` save** that it describes in prose ("saves `raw_focus = focus_spec` before merge, mirror `raw_contract = contract_spec` at :832"). The code block only shows the yaml_focus load and then says "Then merge." An implementer copying the code block verbatim would skip the `raw_focus` save, and the merge would use the already-merged `focus_spec` (or the param, depending on placement), re-introducing the double-merge the (c)/(d) fixes are meant to prevent. The prose is correct; the code block is incomplete.

- **Required fix:** Add `raw_focus = focus_spec` explicitly to the 3b(d) code block, before the yaml_focus load, mirroring `raw_contract = contract_spec` at mcp_server.py:832. Then the merge reads `raw_focus` (raw), not `focus_spec` (which by then may be reassigned).

---

**Verified clean (no findings):**

- **A (keystone helper scope):** `_dispatch_sampling` (mcp_server.py:800) has `workspace` and `staged` in scope; `gate_yaml_path` and `warn` are not — the plan correctly computes the path and mirrors the mcp_server.py:847 warn lambda. ✓
- **B (dual-tmpfile exit paths):** Traced all five exits. Creation-failure unlinks both (one may be None → `_unlink` no-ops, as the plan notes). Dispatch-raise, inline-return, and start_job-raise each unlink both. Timeout job-transfer passes both to `start_job` (3b(b) adds `focus_tempfile_path`) and returns without unlinking — correct, ownership transferred. No double-unlink, no leak, no unlink of a live job's file. ✓
- **C (invariant carve-out):** The second raw read (`_load_gate_yaml_raw`, cli.py proposed ~:118) is gated by `is_trusted_focus` only. Backends still flow through `_load_gate_backends` (cli.py:2185) unchanged. The carve-out does not weaken backend trust. ✓
- **D (cross-ref integrity):** All 11 round-3 fixes verified against real source — `_merge_contract_spec` at cli.py:1828/:1889, `is_trusted` trust.py:125, `record_trust` trust.py:161, `hash_contracts_content` trust.py:243, `DANGEROUS_FIELDS` trust.py:23-31, `_dispatch_cli` mcp_server.py:647-700, `start_job` mcp_jobs.py:80, the three tempfile consumers (mcp_jobs.py:120/307/353), `git_blame` git.py:358, trust command cli.py:1169. Cross-repo chain (`_dispatch_cross_repo` cli.py:1897 → `_cross_repo_verdict_or_none` cli.py:1613 → `run_cross_repo` cross_repo.py:170 → `build_l1_provider` cross_repo.py:304) is consistent. The pre-existing `--contract` no-op on cross-repo (cross_repo.py:250-256 loads from `contracts.yaml` digest, not `--contract FILE`) is correctly disclosed. ✓
