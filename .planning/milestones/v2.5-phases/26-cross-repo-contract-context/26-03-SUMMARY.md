---
phase: 26-cross-repo-contract-context
plan: 03
status: complete
completed: 2026-06-21
commits:
  - c2c973e: "cross_repo/contracts: add integration tests for contract spec threading"
  - 20b7605: "cross_repo/contracts: thread contract_spec through primary L1 provider"
files_changed:
  - src/code_forge/cross_repo.py
  - tests/test_cross_repo_contracts.py
tests_added: 8
tests_passed: 8
---

## Summary

Threaded contract_spec through cross-repo orchestration so the primary
thread's L1 reviewer receives contract specs with backend=backend (SF-1).
Delivered the definitive SC-1/SC-2/SC-3 integration tests including the
end-to-end CF-3 test and the DF-2 sibling constraint lock.

## Key Deliverables

- **cross_repo.py**: 10 lines added -- load contracts.yaml from primary_path,
  pass contract_spec=_contract_spec to build_l1_provider with backend=backend
- **8 integration tests**: SC-1 end-to-end (CF-3), SC-2 missing/unreadable
  spec, SC-3 no-optin (capsys), DF-2 siblings no contract_spec, D-06
  primary gets contract, binary spec skip, env var not set skip

## Success Criteria Proven

- SC-1: kernel YNL spec appears in reviewer context (end-to-end CF-3 test)
- SC-2: missing/unreadable spec degrades gracefully to empty
- SC-3: no contracts.yaml produces no spec and no warning
- DF-2: sibling threads receive no contract_spec (test locked)
- SF-1: backend=backend passed to load_contract_digest in cross_repo.py
