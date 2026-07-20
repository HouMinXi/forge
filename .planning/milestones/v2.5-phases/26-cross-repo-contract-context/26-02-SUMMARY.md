---
phase: 26-cross-repo-contract-context
plan: 02
status: complete
completed: 2026-06-21
commits:
  - 2fd6c37: "factories/cli: wire contract_spec into L1 reviewer prompt"
  - 73ea2c3: "cli/trust: extend trust operations for contracts.yaml"
files_changed:
  - src/code_forge/factories.py
  - src/code_forge/cli.py
  - tests/test_contract_wiring.py
  - .gitignore
tests_added: 8
tests_passed: 8
---

## Summary

Wired contract_spec into both review outlets (Outlet A build_l1_provider,
Outlet C _make_subagent_spawn) so contract specs appear in the L1 reviewer
prompt. Extended _run_trust to handle contracts.yaml alongside gate.yaml
for all three trust operations (record/revoke/status). Added .code-forge/cache/
to .gitignore.

## Key Deliverables

- **factories.py**: contract_spec: str = "" kwarg in build_l1_provider,
  injected as "## Contract Reference" between Blast Radius Context and Diff
- **cli.py**: Outlet A/C wiring via load_contract_digest; _run_trust extended
  for contracts record/revoke/status using resolve_contract_specs 5-tuple
- **.gitignore**: .code-forge/cache/ entry (SF-6)
- **8 integration tests**: outlet injection (A and C), prompt section order,
  trust CLI (record/status/revoke/no-contracts backward compat)

## Review Findings Addressed

D-05 (prompt section order), DF-1 GAP 2 (trust CLI revoke/status for
contracts), SF-6 (.gitignore cache), gate.yaml dependency documented
