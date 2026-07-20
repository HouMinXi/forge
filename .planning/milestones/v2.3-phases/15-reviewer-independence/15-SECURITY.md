---
phase: 15
slug: reviewer-independence
status: verified
threats_open: 1
asvs_level: 1
created: 2026-06-08
---

# Phase 15 — Security

> Per-phase security contract: threat register, accepted risks, and audit trail.

---

## Trust Boundaries

| Boundary | Description | Data Crossing |
|----------|-------------|---------------|
| Local filesystem | AST scan reads .py files under cwd/src via os.walk | Public symbol names only; no execution of scanned code |
| llm_invoke API | Reviewer prompt sent to backend (LLM) per pass | Diff text + post-image content + conventions digest (no secrets) |
| .code-forge/conventions-cache/ | Sibling repo extraction cached as JSON | Public symbol names; commit hash as cache key |

---

## Threat Register

| Threat ID | Category | Component | Disposition | Mitigation | Status |
|-----------|----------|-----------|-------------|------------|--------|
| T-15-01 | Information Disclosure | _spawn prompt construction | mitigate | Prompt built from diff+post_image+role+schema+conv_digest only. No implementer session context. `_make_subagent_spawn` at cli.py:454; TestCriteriaPayload::test_prompt_has_no_session_context PASSED. | closed |
| T-15-02 | Tampering | Reviewer JSON from C-leg | mitigate | `validate_reviewer_json` raises ValueError on schema violation (fail-closed at parse layer) and the outlet callers convert it to a CONFIRMED infra finding. DEFECT (F3): `_run_l1_phase` routes that finding to the falsifier, which DISMISSES it -> false-green, not a dirty round. Mitigation pending Phase 16 (`source=INFRA` + skip falsifier). | open |
| T-15-03 | Tampering | Conventions digest derivation | mitigate | Digest derived via `ast.parse` on local .py files (conventions.py:94). `tree.body`-only traversal — no implementer session data injected. | closed |
| T-15-04 | Spoofing | Test-assertion gate bypass | accept | Gate is advisory/fail-open per D8 exception. Primary 3-cycle + R1/R2/R3 gates unaffected. Documented in `_run_test_assertion_review` docstring at cli.py:542. | closed |
| T-15-05 | Tampering | Symlink traversal in AST scan | mitigate | `Path.parents` containment check (NOT str.startswith) at conventions.py:88 and conventions_resolver.py:77. Rejects prefix-collision paths (/tmp/repo_evil vs /tmp/repo). Regression: test_symlink_prefix_collision_rejected PASSED. | closed |
| T-15-06 | Tampering | Cache poisoning (.code-forge/conventions-cache/) | accept | .code-forge/ is user-writable; same trust level as modifying source code directly. No incremental risk above baseline. | closed |
| T-15-07 | Information Disclosure | Sibling repo contents in digest | accept | Digest contains only public symbol names — same data visible via any import statement. No secrets, no implementation details. | closed |
| T-15-08 | Denial of Service | Large file in AST scan / post-image / sibling scan | mitigate | 100KB size cap at conventions.py:75 and conventions_resolver.py:568. Post-image: stat pre-check + 50KB cap per file + binary detection. Sibling scan: 50 names cap per language + _SKIP_DIRS pruning. | closed |
| T-15-SC | Tampering | Dependency supply chain | mitigate | No new packages added. pyyaml already a project dependency. pyproject.toml unchanged since Phase 14 baseline (b030bd4). | closed |

*Status: open · closed*

---

## Accepted Risks Log

| Risk ID | Threat Ref | Rationale | Accepted By | Date |
|---------|------------|-----------|-------------|------|
| AR-15-01 | T-15-04 | Test-assertion gate is advisory-only (fail-open). Primary review gates (3-cycle + R1/R2/R3) are not bypassed. D8 exception documented in code. | Minxi Hou | 2026-06-08 |
| AR-15-02 | T-15-06 | Cache dir is user-writable. Attacker with local write access can already modify source code directly. No incremental risk. | Minxi Hou | 2026-06-08 |
| AR-15-03 | T-15-07 | Public symbol names only — no secrets or internal implementation details exposed in sibling repo digest. | Minxi Hou | 2026-06-08 |

---

## Security Audit Trail

| Audit Date | Threats Total | Closed | Open | Run By |
|------------|---------------|--------|------|--------|
| 2026-06-08 | 9 | 8 | 1 | gsd-secure-phase (inline); threats_open revised to 1 after F3 root cause verified (T-15-02 open) |

---

## Sign-Off

- [x] All threats have a disposition (mitigate / accept / transfer)
- [x] Accepted risks documented in Accepted Risks Log
- [ ] `threats_open: 0` -- 1 open (T-15-02), tracked to Phase 16 F3 fix
- [x] `status: verified` set in frontmatter (1 open threat deferred to Phase 16)

**Approval:** verified 2026-06-08
