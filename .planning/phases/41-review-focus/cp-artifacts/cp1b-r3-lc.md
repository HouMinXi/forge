I have all the evidence I need. Let me write the report.

SUMMARY: B=0 H=0 M=0 L=1

---

**Finding:**

[L] Task 1: factories.py:576 reachability claim is stale and contradicts the plan's own RECONCILE section
  Location: plan Task 1, item 3 (Coverage floor caveat) + RECONCILE section; real code `mcp_server.py:853-857`, `mcp_server.py:997-1004`
  Description: Task 1 claims `factories.py:576` is "currently UNREACHABLE in production -- its only caller (mcp_server.py:765) passes no contract_spec," and therefore "It can therefore only be covered by a direct unit test of the builder, never end-to-end. Do not write a test that claims e2e coverage of that path." It further references a "Task 3g" that fixes the caller.

  Both parts are wrong against the current tree and against the plan's own RECONCILE section:
  - RECONCILE (added 2026-07-23) states D5.7 / Task 3b-5 (sampling contract_spec wiring) was merged at 2edb9d4, and that `_dispatch_sampling` (mcp_server.py:800) "has a `contract_spec: str = ""` param (:806), merges via `cli._merge_contract_spec` (:845), and passes it to `build_sampling_l1_provider` (:853/:857)."
  - The actual code confirms it: `mcp_server.py:853-857` calls `build_sampling_l1_provider(..., contract_spec=contract_spec)`, and `forge_review` routes to `_dispatch_sampling` with `contract_spec=contract` at `mcp_server.py:1003`. So when a contract is supplied on the sampling path, `factories.py:576` IS reached end-to-end — the exact opposite of the Task 1 claim.
  - There is no "Task 3g" anywhere in the plan (tasks are 1, 2, 3a, 3b, 3c, 4, 5). The reference is a dangling token left from a pre-merge draft.

  The header rename itself is correct and still required; the bug-inject and the unit-test-of-the-builder fallback still cover it. The harm is purely that an implementer is (a) told a reachable path is unreachable, (b) told not to write an e2e assertion that is in fact possible, and (c) sent looking for a "Task 3g" that does not exist.

  Required fix: Rewrite the Task 1 caveat. State that post-2edb9d4 the sampling caller passes `contract_spec`, so `factories.py:576` is reachable end-to-end on the sampling path when a contract is provided (and still unit-testable on the builder otherwise). Delete the "Task 3g" sentence.

---

All 6 H1 verification points pass against the real code:

1. **Decoupling holds.** `_load_gate_yaml_raw` (`cli.py` new helper) parses gate.yaml with NO trust gating; `review_focus` is read from that raw dict and gated only by `is_trusted_focus`. In the untrusted-backends + trusted-focus case, `_load_gate_backends` returns `([], {})` (`cli.py:159`) but the raw loader still returns the real dict with `review_focus`, so `## Review Focus` is still injected. Confirmed against `cli.py:118-161`.
2. **`is_trusted_focus` gets the real dict.** The plan passes `focus_gd` (the raw-loader dict) to `is_trusted_focus`, so `hash_focus_text` hashes the genuine `review_focus`, not `{}`. The short-circuit (`if not current: return True`) is reached before any store read, matching the contracts pattern at `trust.py:271-286`.
3. **Error behavior mirrors `_load_gate_backends`.** `_load_gate_yaml_raw` returns `{}` for absent/empty/non-dict and raises the same `CliError("gate.yaml parse error: ...")` on `YAMLError` (`cli.py:142-146`). No crash, no divergence.
4. **`_load_gate_backends` untouched.** The plan explicitly adds a standalone helper and leaves `_load_gate_backends`'s `([], {})`-on-untrust contract intact for its 24+ callers (`cli.py:1033, 2185, 3190`, plus `mcp_server.py:243, 292`).
5. **All paths use the raw loader.** CLI `_run`, CLI-subprocess fork (reads gate.yaml inside `_run`), and sampling `_dispatch_sampling` (`mcp_server.py:800`) all route focus through `_load_gate_yaml_raw`; backend loading still uses `_load_gate_backends`.
6. **Bug-inject guard is real.** Untrusted backends (edit after `code-forge trust`) + trusted `review_focus` → focus still appears; reverting the focus read to `_load_gate_backends`'s `gate_data` (which is `{}` when untrusted) → the test fails.

Prior-round items were correctly fixed and were not re-litigated.
