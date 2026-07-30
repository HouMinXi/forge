# Phase 42 Summary: CLI key fast-fail + claim_type oracle

**Merged:** 2026-07-29 (via dispatch orders + direct commits)
**Branch:** main (fast-forward)
**Commits:** bf44af5..933032d

## Objective

Extend the CLI fast-fail guard to cover api_key_file and vertex credential
backends, and add a mechanically derived claim_type to every ledger finding.

## Deliverables

### 42-01: CLI key fast-fail (F8)

Extended the fast-fail guard at cli.py to cover api_key_file and vertex
credentials_path backends. The existing guard only checked api_key_env for
non-vertex backends; api_key_file backends failed in per-pass key resolution,
producing 3 identical INFRA findings instead of one clear error at startup.

Key changes:
- `_check_backend_credentials(backend, env)` helper extracted for testability
- Guard covers api_key_file (missing/empty/permission), vertex credentials_path
- CLI backends (type=="cli") skip credential check (authenticate via `claude auth`)
- env parameter threaded from `_run` through to the guard

Commits: 75743e8, eb73c22, 1760be8, d5caecb, f7bd6ad, 1d5b9b4, 67f47b2, 933032d

**Truths verified:**
- [x] api_key_file missing file raises CliError before review pipeline
- [x] api_key_file empty file raises CliError
- [x] vertex credentials_path missing file raises CliError
- [x] existing api_key_env fast-fail guard still works
- [x] guard logic extracted into _check_backend_credentials helper

### 42-02: claim_type oracle (7.1)

Added ClaimType dataclass and derive_claim_type function to mechanically
derive the claim type from a finding's source. Every finding written to the
ledger now has a derived claim_type instead of a hardcoded "review".

Key changes:
- `claim.py`: ClaimType dataclass + derive_claim_type function
- `machine.py`: wired derive_claim_type into _write_ledger_rows
- `ledger.py`: LedgerRow gains version_sensitive field (default False)

Commits: 3be4558, 4ac9a78, d540748

**Truths verified:**
- [x] Every finding has mechanically derived claim_type
- [x] L1 findings get claim_type 'review' with version_sensitive=True
- [x] L0 findings get claim_type 'lint' with version_sensitive=False
- [x] 'manual' override at cli.py:1321 stays literal
- [x] LedgerRow has version_sensitive field (backward compatible)

## Receipt followups (dispatch orders R1-R2-R3)

Separate deliverable within Phase 42, dispatched via PM orders:
- receipt load crash fix (bf44af5)
- excerpt coverage inflation fix
- cross_repo guarded receipt loading
- inverted range schema validation
- _covered dual-shape tolerance
- hook capture-and-replay mechanism
- test rename (work-order IDs -> behaviour descriptions)

9 commits total, all with Signed-off-by. Suite: 3002 passed / 9 skipped.

## Suite

3002 passed, 9 skipped (at merge time).

## Acceptance

Phase 42 plans executed via dispatch orders, not GSD workflow. All must_haves
verified against merged code. Receipt followups R3 verifier: 13 pass / 1 fail
(G false positive, PM-adopted).
