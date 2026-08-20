# Review assignment (Kimi): cross-boundary data flow + requirements compliance

Read the shared briefing first:
/home/houminxi/code/forge/.planning/phases/54-router-onboarding-compat/cp-artifacts/cp1b-r1-briefing.md

Then review the plan:
/home/houminxi/code/forge/.planning/phases/54-router-onboarding-compat/54-01-PLAN.md

Your angle (play to your strength, ignore the rest):
1. CROSS-BOUNDARY DATA FLOW — trace each value across module boundaries and
   find where it deforms:
   a. kind= strings born at llm_invoke.py raise sites -> consumed by
      backend.py's _classify_live_failure -> rendered into doctor.py rows ->
      gated by the mcp_server.py:958-960 fallback whitelist. Does every kind
      the plan creates have a defined fate at EVERY consumer? Does any
      consumer see a kind it mishandles (including kind="" and unknown)?
   b. resolved workspace path: resolve_workspace(cwd, os.environ) ->
      gate_yaml_path / contracts_yaml_path / resolve_contract_specs(., ws) ->
      record_trust/revoke_trust hashing. Does a subdirectory invocation
      produce byte-identical trust-store entries to a root invocation?
   c. env flow: FORGE_PROJECT_DIR in os.environ -> resolve_workspace priority
      1. Any path where the env var leaks past the plan's delenv hygiene?
   d. replace() config copy -> llm_invoke's timeout/cap/truncation machinery:
      which fields survive the copy unchanged and which of those can still
      deform the probe (base_url, api_key env names, stream, headers)?
2. REQUIREMENTS COMPLIANCE — REQUIREMENTS.md ROUTER-02..05 acceptance text vs
   the plan's tasks, line by line. Any acceptance wording the plan weakens
   or narrows is a finding.

Read the real sources under /home/houminxi/code/forge/src/code_forge/
(workspace.py, cli.py, backend.py, llm_invoke.py, doctor.py, mcp_server.py,
contract_loader.py, trust.py, user_config.py) to verify every hop before
reporting it. The plan's own line citations are mostly current but treat
them as hints, not evidence.

Follow the briefing's output contract exactly, ending with
`SCORECARD: B=<n> H=<n> M=<n> L=<n>`.
