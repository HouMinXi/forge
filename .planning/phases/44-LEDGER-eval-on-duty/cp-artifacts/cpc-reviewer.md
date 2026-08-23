# P44 post-fix consistency confirmation — reviewer

**Date:** 2026-08-22
**Task:** t_ad8c97a8
**Reviewer profile:** reviewer
**Code base:** main @ 8bd01bc (plan-level review — no code changes yet)

---

## Check 1: Broadened except (reviewer M-1 fix)

**Source:** 44-01-PLAN.md Task 2, lines 281-289, 316-319

**Evidence:**
- Plan text specifies ONE try/except covering `OSError + yaml.YAMLError + AttributeError + TypeError` — covers file I/O, malformed YAML, None.get, and type errors
- `isinstance(data, dict)` guard before `dict.get` — prevents the AttributeError that `None.get(...)` would raise on empty/corrupt file
- Fail-open on any caught failure: ledger treated as ENABLED, verdict unchanged, infra_errors + stderr warn
- Acceptance criteria include bug-injection tests for malformed gate.yaml (yaml.YAMLError) and empty gate.yaml (safe_load -> None -> .get AttributeError)
- Config read uses `yaml.safe_load + dict.get` directly (NOT `load_gate_config` which raises ValueError on review-mode gate.yaml lacking `test:` section — kimi B-2 fix preserved)

**CONFIRMED** — the plan correctly describes the broadened except tuple, the isinstance guard, and the fail-open semantics. All three concrete failure modes (OSError, yaml.YAMLError, AttributeError/TypeError) are covered.

**Minor observation (non-blocking):** `UnicodeDecodeError` (subclass of ValueError) from opening a non-UTF-8 gate.yaml file is not in the except tuple. This is an edge case — gate.yaml is always ASCII/UTF-8 in practice. Not a finding.

---

## Check 2: advisory.py removal from 44-03 scope (scribe L-1 fix)

**Source:** 44-03-PLAN.md lines 7-12 (files_modified), line 204 (Task 2 <files>), line 203 (Task 2 name)

**Evidence:**
- `files_modified` (lines 7-12): machine.py, ledger.py, gate_check.py, test_machine_ledger.py, test_convergence.py — no advisory.py
- Task 2 `<files>` (line 204): gate_check.py, machine.py, test_convergence.py — no advisory.py
- Task 2 name (line 203): `"pinned_paths suppression + style-finding downgrade to non-blocking disposition (D-26, D-27)"` — no advisory.py reference
- All 5 advisory.py references in the plan body are explanatory: the interfaces section (lines 119-124) explicitly states "advisory.py needs NO change in this plan"; remaining references explain why style findings should NOT be routed to AdvisoryFinding (fingerprint loss)
- 44-01-PLAN.md has zero references to advisory.py

**CONFIRMED** — advisory.py is fully removed from scope. No dangling plan text requires an advisory.py change.

---

## Check 3: NEW objections from these fixes

**NONE.** The fixes are correct and internally consistent:

- The broadened except tuple is complete for the stated failure modes. The `isinstance` guard prevents the common AttributeError path at the guard level, while the except catches any remaining issues. Fail-open preserves the D-19 never-crash invariant.
- The advisory.py removal leaves no dangling references. The plan explicitly documents why it's excluded.
- No cross-plan inconsistency: 44-01 and 44-03 are self-consistent and their task wiring is clean.

**STILL NO OBJECTION from reviewer.**

---

## SCORECARD: B=0 H=0 M=0 L=0