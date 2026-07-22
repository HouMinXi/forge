# Phase 47: LLM invoke error visibility -- PM record

Source: bug report forwarded by the fleet commander,
/tmp/draft_20260722_forge_invoke_json_rca.txt (diagnosis-only, reporter
Minxi Hou, main @ 8e18aa0). Not related to Phase 41 (sampling contract_spec
wiring) -- different code region, confirmed below.

Worktree: .worktrees/invoke-error-visibility, branch fix/invoke-error-visibility,
base main @ 8e18aa0.

## Report verification (PM ground-truth, 2026-07-22)

Cross-group delivery into forge's own Zone C -- self-certification does not
travel, so every "proven" claim was re-derived against real source before
acting on it, not accepted on the report's word.

DEFECT 1 (API path discards its own diagnostic) -- CONFIRMED, byte-exact
match against main @ 8e18aa0:
  - llm_invoke.py LLMInvokeError.__init__: `super().__init__(message)` only;
    `self.stderr = stderr` is a separate attribute never folded into the
    base Exception's args. str(exc) is message-only. Verified directly.
  - factories.py:346-351 and :361 (build_l1_provider's per-pass loop): both
    the console print and the StateFinding description format `pr` via
    plain %s -- i.e. str(exc) -- never pr.stderr. Verified directly.
  - llm_invoke.py ~1425-1430 (invoke_sampling's no-JSON raise): interpolates
    raw_text[:120] directly into the message, so the equivalent sampling-path
    failure DOES surface its diagnostic. Verified directly. Asymmetry is
    real.
  Net: a real, live bug. Five real MCP forge_review runs produced zero
  information about what the model returned, exactly as reported.

DEFECT 2 (kind asymmetry, fallback-eligibility) -- report flagged this as
"proven in source, reachability NOT verified by me, you own the code."
Traced to a definite answer via full call-graph grep, not inference:
  - The ONLY consumer of `.kind` anywhere in the repo is mcp_server.py:804
    (`_can_fallback = exc.kind in (...)`), inside the function built around
    build_sampling_l1_provider -- i.e. _dispatch_sampling's own fallback
    routing (confirmed by tests/test_mcp_server.py:810's own section
    comment naming that function).
  - The ONLY raise sites that set `kind=` are in llm_invoke.py, all inside
    invoke_sampling (the function _dispatch_sampling calls). Grepped every
    `kind="` in the repo: none are in _invoke_api.
  - build_l1_provider (the API path, what _invoke_api serves) is called
    from exactly two production sites: cli.py:2475 and cross_repo.py:304.
    Neither is mcp_server.py. Grepped every call site repo-wide.
  CONCLUSION: _invoke_api's LLMInvokeError and mcp_server.py:804's
  _can_fallback check are on disjoint call graphs -- the former can never
  reach the latter. Defect 2 is LATENT, not active, on any path that
  exists today. Resolves the report's own open question; not something the
  reporter could have settled without this trace.
  RISK IF LEFT UNFIXED: only if a future change wires build_l1_provider's
  output into a caller that checks .kind (none does today). Not urgent.

DEFECT 3 (DeepSeek ignores max_completion_tokens) -- experimental claim
(live API measurement), not re-verified here (no cheap way to reproduce an
external API call for this grounding pass). Report already scopes it
correctly as operator error (template docs already correct) and explicitly
does not ask for a code change beyond the optional diagnostic-integrity
note. Accepted as-is, LOW confidence on the raw numbers specifically (not
re-measured), HIGH confidence on the scoping (matches gate.yaml template
behavior already known from this project).

Cross-section consistency check (upstream_report_discipline): each
suggested-fix section's scope matches its own diagnosis strength (defect 1
gets a concrete fix, defect 2 gets "verify reachability first" which this
pass now answers, defect 3 gets no code ask). No contradictions found.

Region-overlap check: mcp_server.py:804 (_dispatch_sampling) is a
different function than _dispatch_cli (mcp_server.py ~638-700, the Phase
41 sampling-contract fix). No interaction between this phase and Phase 41.

## Scope decision

FIX NOW: defect 1 only. Root-cause fix (not per-call-site patching): make
the API-path no-JSON raise interpolate its own captured content into the
message itself, mirroring invoke_sampling's existing pattern exactly, so
every current AND future consumer of str(exc) sees it -- rather than
patching factories.py's two call sites individually, which would still
leave any future third consumer blind by default.

DEFER, no code change: defect 2 (confirmed latent; revisit only if
build_l1_provider's output is ever wired into a .kind-checking caller).
defect 3 (operator error already correctly documented; the diagnostic-
integrity angle is optional and speculative -- not scheduled).

Classification: logic-bearing (changes what an error path surfaces) ->
full three-cycle review applies regardless of diff size. No CP1/CP1b plan
review needed -- there is no design ambiguity here, the report already
pins the fix shape; going straight to a frozen implementation work order.

Implementation work order frozen at
/tmp/draft_p47_invoke_visibility_workorder_20260722.txt, non-ASCII gate
run, clean.
