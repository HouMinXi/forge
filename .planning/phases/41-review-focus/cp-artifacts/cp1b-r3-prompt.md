You are reviewing a PLAN (Round 3) for forge Phase 41 "review-focus".
The plan adds a review-focus emphasis mechanism at parity with --contract
across 3 prompt builders and 4 review paths (outlet_a, outlet_c, cross-repo,
MCP CLI-subprocess + MCP sampling), with an independent trust hash, plus a
git-blame committer-date field. Your job: verify the prior-round fixes are
correct in the CURRENT plan, and HARD-REVIEW the one new fix (H1) below.
Find NEW issues only. Do not re-report items already correctly fixed.

Ground rule: every finding must cite the plan section/line AND the real code
it contradicts (file:line in src/code_forge/). Verify against the actual code,
not against the plan's own claims. If you cannot point to real code, do not
report the finding.

## Prior-round disposition (do NOT re-litigate these)

Round-1 findings -- all FIXED and verified in the current plan:
- [B1] MCP focus double-merge -> raw passthrough: _dispatch_cli /
  _dispatch_sampling receive RAW focus, merge exactly once; outlet_c
  (_dispatch_subagent) takes raw _focus_file_content + yaml_focus, merges once.
- [H1-superseded] old 3b-3/3b-4/3b-5 boundary -> "SUPERSEDED, do NOT
  implement" banner added.
- [H2] Acceptance D5.7 contradiction -> updated to merged state (2edb9d4).
- [H3] staged guard missing for yaml_focus (sampling) -> `if not staged:`
  guard, mirrors contracts_yaml gating at mcp_server.py:839.
- [M1] record_trust dict replace -> merge-first {**store.get(key,{}), ...}
  (defensive only; contracts_hash lives under a different store key,
  trust.py:302).
- [M2] hash_focus_text Optional[dict] vs dict -> `dict` (matches is_trusted,
  trust.py:125); None is moot after the H1 fix (raw loader always returns a
  dict). Whitespace-only review_focus is dropped at extraction via raw.strip().
- [M3] trust store key -> str(path.resolve()), matches is_trusted (trust.py:132).
- [M4] blame date test -> GIT_AUTHOR_DATE / GIT_COMMITTER_DATE env test.

Round-2 items -- dispositioned (do NOT re-report):
- H-claim "gate-check does not go through _dispatch_cli, parity is wrong" ->
  DISPROVED. forge_gate_check calls _dispatch_cli at mcp_server.py:1078.
- M1-wording "raw-in/merge-inside (Phase 41) vs merged-down (cross-repo)" ->
  documented intentional at plan 41-PLAN.md:403-404.
- L1 "superseded list missing old 3b-5 tmpfile-content==RAW assertion" ->
  addressed, plan replan (e) at 41-PLAN.md:520-522.

## NEW this round -- REVIEW THIS HARDEST: the [H1] fix (Task 3a-3)

Background: gm round-1 found that Task 3a-3 read `review_focus` from the dict
returned by `_load_gate_backends`, which returns `([], {})` when the backends
block is UNTRUSTED (cli.py:160). That silently dropped a legitimately-trusted
`review_focus` and never fired the focus warning -- coupling focus to backend
trust and defeating the independent focus-trust design (D5.6). gm round-2 then
INCORRECTLY marked this "resolved" on a false claim that _load_gate_backends
"returns (cfgs, gd)"; the code returns ([], {}) when untrusted. So the fix was
authored fresh (not by gm) and needs independent verification.

The fix (plan 41-PLAN.md:301-355): a standalone `_load_gate_yaml_raw(path) ->
dict` (best-effort parse, NO trust gating, YAMLError -> CliError like
_load_gate_backends); `review_focus` is read from THAT dict and gated ONLY on
`is_trusted_focus(gate_yaml_path, focus_gd)`; `_load_gate_backends` is left
untouched; all 3 focus paths use the raw loader.

Verify specifically:
1. Does the fix actually decouple focus from backend trust? Trace the untrusted-
   backends + trusted-review_focus case: does `## Review Focus` still get
   injected?
2. Does `is_trusted_focus` now receive the real parsed dict (focus_gd), so
   `hash_focus_text` computes the true hash (not a hash of {})? Check
   is_trusted_focus/hash_focus_text as specified in Task 3a-2 against
   trust.py:243-286 (the contracts pattern it mirrors).
3. Absent / empty / non-dict / corrupt gate.yaml through `_load_gate_yaml_raw`:
   any crash, or divergence from _load_gate_backends's error behavior?
4. Is `_load_gate_backends` genuinely untouched (its 24+ callers rely on the
   trust-gated {}-on-untrust contract)? Any place the fix accidentally changes
   backend behavior?
5. Do ALL focus-reading paths use `_load_gate_yaml_raw` (CLI _run,
   CLI-subprocess fork, sampling _dispatch_sampling at mcp_server.py:800)? Any
   path still reading focus from the backend gate_data?
6. Is the H1 bug-inject guard real (untrusted backends + trusted focus ->
   focus still appears; revert to gate_data -> test FAILS)?

## Severity scale
- B (Blocker): will crash, corrupt data, silently no-op, or bypass trust at runtime.
- H (High): logic error that causes incorrect behavior or defeats a design goal.
- M (Medium): significant gap that will bite during implementation.
- L (Low): minor / robustness / wording.

## Output format
SUMMARY: B=X H=X M=X L=X
Then, only if any > 0, list each NEW finding as:
[SEVERITY] Task X-Y: short title
  Location: plan line + real code file:line
  Description: what is wrong, traced through the code
  Required fix: concrete change
Report `SUMMARY: B=0 H=0 M=0 L=0` if the plan (incl. the H1 fix) is clean.

The plan follows below.
======================================================================
