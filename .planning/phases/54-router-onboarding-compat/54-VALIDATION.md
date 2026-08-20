---
phase: 54
slug: router-onboarding-compat
status: approved
nyquist_compliant: true
wave_0_complete: true
created: 2026-08-18
---

# Phase 54 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Source: 54-RESEARCH.md PART 4 (Validation Architecture).

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (config: pyproject.toml [tool.pytest.ini_options]) |
| **Config file** | pyproject.toml |
| **Quick run command** | `python -m pytest tests/test_doctor.py tests/test_cli_trust.py tests/test_contract_wiring.py tests/test_user_config.py tests/test_mcp_server.py -x -q` |
| **Full suite command** | `python -m pytest` |
| **Estimated runtime** | quick ~60s; full ~780s (baseline 3483 passed / 8 skipped @ 4087b05) |

---

## Sampling Rate

- **After every task commit:** Run the quick-run subset above
- **After every plan wave:** Run full `python -m pytest`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 60s (quick subset)
- **Hard rule:** no graph.db-dependent tests may be added (gitignored in
  fresh worktrees; existing pattern test_dead_code.py:24/:603)

---

## Per-Requirement Verification Map

| Req | Plan | Behavior | Test Type | Automated Command | Status |
|-----|------|----------|-----------|-------------------|--------|
| ROUTER-02 | 54-01 | gate.schema.json base_url description covers /v1 semantics | schema-validation suite stays green + optional string assertion | `python -m pytest tests/test_backend.py -q` | ⬜ pending |
| ROUTER-03 | 54-01 | trust walk-up + pre-mutation path print + off-root warn | unit (tmp project tree, patch record_trust, capsys order) | `python -m pytest tests/test_cli_trust.py -q` | ⬜ pending |
| ROUTER-04 | 54-01 | doctor --live probe: off=never called; success=PASS row; timeout=FAIL row+exit 1; 8 pinned taxonomy labels (5 D-04 + truncated-output + http-error + unclassified); timeout_s=60 override; max_attempts=1; cache bypass; whitelist-negative via test_mcp_server.py | unit (mock llm_invoke / live helper) | `python -m pytest tests/test_doctor.py tests/test_llm_invoke.py tests/test_backend.py tests/test_mcp_server.py -q` | ⬜ pending |
| ROUTER-05 | 54-01 | doctor surfaces user-config state via user_config_path patch (NOT load_user_backends — conftest autouse isolation trap) | unit | `python -m pytest tests/test_doctor.py tests/test_user_config.py -q` | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

Bug-injection points are enumerated per requirement in 54-RESEARCH.md
PART 4 ("Injection point" column) — inject AT the call site per house rule.

---

## Wave 0 Requirements

None — existing pytest infrastructure (fixtures, conftest isolation,
mocking patterns) covers all four requirements.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Real `doctor --live` against a configured backend + one deliberately broken base_url | ROUTER-04 | Golden Rule 3 real-path smoke; needs live network/backend | run `code-forge doctor --live`; then a wrong-/v1 base_url and watch the F2/F4 failure class surface |
| Trust from a real project subdirectory | ROUTER-03 | Needs a real configured project tree | `cd` into a subdir, `code-forge trust`, confirm printed abs path + warn, then `--revoke` |
| SSE-always router class probe | ROUTER-04 | Optional witness; OmniRoute [IP_REDACTED]:20128 availability UNVERIFIED — NOT a gate | point a backend at it, `doctor --live`, expect SSE-mixed class |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies (T1-T5 automated; T6 is a declared human checkpoint by design)
- [x] Sampling continuity: no 3 consecutive tasks without automated verify (single wave, every code task verified)
- [x] Wave 0 covers all MISSING references (N/A — no Wave 0)
- [x] No watch-mode flags
- [x] Feedback latency < 60s (quick subset)
- [x] `nyquist_compliant: true` set in frontmatter (planner task mapping complete)

**Approval:** approved 2026-08-18
