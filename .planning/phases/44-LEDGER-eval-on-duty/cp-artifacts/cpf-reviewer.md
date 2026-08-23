# CP-FINAL Convergence Review -- Phase 44 (reviewer / logic-attack axis)

**Reviewer:** reviewer (kanban t_54fd29f4)
**Date:** 2026-08-22
**Scope:** FINAL convergence check on the Phase 44 plan set (44-CONTEXT.md / 44-01 / 44-02 / 44-03) after CP1 (PASS B=0 W=5) and CP1b (6 blockers). This review verifies each fix's logic, exception isolation, and edge-case soundness from a skeptical/attack perspective. Ground truth re-grepped against main @ 8bd01bc: `src/code_forge/{machine,ledger,gate_check,coverage,advisory,reviewer_json,cli,outlet_resolver}.py` and `eval/{corpus,runner}.py`. All line numbers verified live — none trusted from citation.

---

## Ground-truth re-verification (all 6 blockers)

### 1. kimi B-1 — style findings ejected from data model (D-27)
**CONFIRMED-FIXED**

Live ground truth:
- `advisory.py:33-37`: AdvisoryFinding docstring `Fingerprint` listed under "Fields intentionally excluded" — rerouting would eject style findings from ledger/adjudicate/export permanently. [KNOWN/HIGH]
- `machine.py:526-531`: verdict counts `_count(Disposition.CONFIRMED)` + `_count_coverage_gaps`. Style-downgraded findings with non-blocking disposition (neither CONFIRMED nor COVERAGE) are excluded from the verdict. [KNOWN/HIGH]
- `reviewer_json.py:170-180`: fingerprint recipe `sha256("%s:%d:%s" % (file, line, desc))[:16]`, description carries `[pass_name] ` prefix. [KNOWN/HIGH]

Plan fix (current 44-03-PLAN.md, mtime 22:15, post-CP1):
- Task 2 test (d) lines 228-236, action lines 260-276, acceptance lines 287-293: style findings keep fingerprint + StateFinding + non-blocking disposition. NOT routed to AdvisoryFinding. [KNOWN/HIGH]
- 44-01-PLAN.md Task 2 action lines 259-269 + acceptance lines 315-316: CI writer emits UNADJUDICATED rows for style-downgraded findings separately from CONFIRMED. [KNOWN/HIGH]
- 44-03 frontmatter line 22-23, artifacts (no advisory.py write), key_links lines 43-46: all carry the fixed design. [KNOWN/HIGH]

**Verdict:** CONFIRMED-FIXED.

---

### 2. kimi B-2 — kill-switch via load_gate_config dead in review mode (D-19)
**CONFIRMED-FIXED**

Live ground truth:
- `gate_check.py:64-71`: `load_gate_config` raises `ValueError("gate.yaml needs an active 'test' section...")` when `"test" not in data`. [KNOWN/HIGH]
- `outlet_resolver.py:130-143`: `load_outlet_from_gate` "Does NOT call load_gate_config" — uses `yaml.safe_load + dict.get`. Precedent exists. [KNOWN/HIGH]

Plan fix:
- 44-01-PLAN.md Task 2 test (g) lines 228-234, action lines 278-282, acceptance lines 300-303, 317-318: kill-switch config read is `yaml.safe_load + dict.get`, INSIDE the try/except, with grep acceptance "NO load_gate_config" and bug-injection test. [KNOWN/HIGH]
- 44-03-PLAN.md Task 2 action lines 240-245: pinned_paths/style_downgrade routed through the same tolerant read. [KNOWN/HIGH]

**Verdict:** CONFIRMED-FIXED.

---

### 3. kimi B-3 — mutation-survivor FAIL (:360) skipped ledger write (D-01)
**CONFIRMED-FIXED**

Live ground truth:
- `machine.py:351-360`: `status == "done"` with survivors → `return Verdict.FAIL` (bypasses :541). [KNOWN/HIGH]
- `machine.py:381`: PENDING return (no PID, not stale) — non-terminal, correctly excluded. [KNOWN/HIGH]
- `machine.py:391`: PENDING return (PID alive) — non-terminal, correctly excluded. [KNOWN/HIGH]
- `machine.py:539-542`: normal CI terminal: verdict set, `_persist_state()`, `return verdict` — the insertion point for the ledger write at :541. [KNOWN/HIGH]

Plan fix:
- 44-01-PLAN.md Task 2 test (g2) lines 235-239, action lines 251-256, acceptance lines 296-297: funnel both :360 AND :541 through the single `_write_ci_ledger_rows` call. [KNOWN/HIGH]

**Verdict:** CONFIRMED-FIXED.

---

### 4. gemini B-1 — pinned_paths did not suppress COVERAGE findings (D-26)
**CONFIRMED-FIXED**

Live ground truth:
- `machine.py:1635-1645`: `_count_coverage_gaps` counts `f.source == "COVERAGE" and f.disposition != Disposition.DISMISSED`. [KNOWN/HIGH]
- `coverage.py:100-113`: COVERAGE findings generated as `disposition=Disposition.UNCERTAIN`. [KNOWN/HIGH]
- `disposition.py:29-33`: enum has CONFIRMED, DISMISSED, UNCERTAIN, FIXED — no STYLE member yet. [KNOWN/HIGH]

Plan fix:
- 44-03-PLAN.md Task 2 test (b2) lines 219-222, action lines 256-264 (cites `machine.py:1635-1643` and `coverage.py:100-115`), acceptance lines 284-286: set pinned-path COVERAGE findings to DISMISSED so coverage_gaps drops to 0. [KNOWN/HIGH]

**Verdict:** CONFIRMED-FIXED.

---

### 5. gemini B-2 — ESCAPED base==head -> empty diff false-green (D-02 / D-15)
**CONFIRMED-FIXED**

Live ground truth:
- `cli.py:1619-1622`: when both SHAs omitted, `head_sha = _git_head(cwd); base_sha = head_sha`. [KNOWN/HIGH]

Plan fix:
- 44-02-PLAN.md Task 1 test (f) lines 164-170: extractor skips base_sha==head_sha / empty-diff rows under a dedicated empty-diff counter. [KNOWN/HIGH]
- 44-02-PLAN.md line 24 must_haves enumerates all five skip reasons with precedence; line 174 action carries the fixed mapping. [KNOWN/HIGH]

**Verdict:** CONFIRMED-FIXED.

---

### 6. deepseek H-1 — DUPLICATE mapped to expect-no-catch penalized real bugs (D-02 / D-13)
**CONFIRMED-FIXED**

Live ground truth:
- `ledger.py:34-37`: TerminalState has FIXED/DISPROVED/DUPLICATE/ESCAPED — four states currently. [KNOWN/HIGH]

Plan fix:
- 44-CONTEXT.md D-02 lines 40-47: "DUPLICATE -> EXCLUDED from export ... skipped under their own counter". [KNOWN/HIGH]
- 44-CONTEXT.md D-13 line 126: "DUPLICATE -> excluded from export". [KNOWN/HIGH]
- 44-02-PLAN.md line 21 must_haves: "DUPLICATE rows are EXCLUDED (deepseek H-1 — a real bug, reported twice)". [KNOWN/HIGH]
- 44-02-PLAN.md Task 1 test (a) lines 144-149, action line 176: "DUPLICATE -> excluded". [KNOWN/HIGH]
- Stale-text W-3 (two spots in 44-02-PLAN.md:21 and :174) verified FIXED in current plan text (mtime 22:16, post-CP1). [KNOWN/HIGH]

**Verdict:** CONFIRMED-FIXED.

---

## Residual concerns (logic-attack axis)

### [M-1] Kill-switch except OSError does not cover yaml.YAMLError from malformed gate.yaml

**Location:** 44-01-PLAN.md Task 2 action lines 280-282, acceptance lines 300-303
**Evidence:** Plan says `try/except OSError` wrapping the kill-switch read (`yaml.safe_load + dict.get`, line 278-280). `yaml.YAMLError` (malformed/empty YAML) is a subclass of `Exception`, NOT of `OSError` — verified from the Python YAML library hierarchy. A missing gate.yaml raises `FileNotFoundError` (OSError subclass) → caught OK. A malformed gate.yaml (corrupt file, transient editor write) raises `yaml.YAMLError` → NOT caught → propagates out of `_write_ci_ledger_rows` → protrudes through `_run_ci` → the review returns as a crash instead of a verdict. This violates D-19's invariant: "ledger write failure degrades gracefully ... NEVER changes the review verdict". [KNOWN/HIGH]

**Impact:** A crash (uncaught exception) IS a verdict change — CI treats a process crash as failure, even for a real-PASS run. The invariant is not merely "verdict string unchanged" but "the run returns a correct verdict or degrades, never crashes". [COMPUTED/HIGH]

**Fix:** Include `yaml.YAMLError` (and `AttributeError` for safe_load returning None on empty file, `TypeError` for `.get` on None) in the except tuple, or add an `isinstance(data, dict)` guard before the `.get` call. The acceptance criteria should include a malformed-YAML test case in addition to the missing-YAML test (g). [COMPUTED/MED]

**Severity:** M — real edge case with concrete failure mode in a path the test suite doesn't cover. Cheap to fix (broaden the except tuple). Not blocking — the executor can address during implementation.

---

### [L-1] Style disposition choice not pinned (STYLE vs DISMISSED-with-style-note)

**Location:** 44-03-PLAN.md Task 2 test (d) line 232: "a NON-BLOCKING disposition (a new STYLE disposition or DISMISSED-with-style-note)"
**Evidence:** The plan allows two implementations. If the executor picks DISMISSED for style findings, then 44-01's CI writer must distinguish "style-downgraded DISMISSED" (write UNADJUDICATED row, per D-27) from "suppressed DISMISSED" (don't write, per 44-01 action 259-268). The write-scope sentence in 44-01 Task 2 action (lines 259-269) resolves this at the prose level, but the implementation choice could be cleaner with a dedicated STYLE member. [COMPUTED/MED]

**Impact:** If the executor picks DISMISSED, the CI writer needs a secondary classification (e.g. "was this finding style-downgraded?") to distinguish the two DISMISSED categories. If STYLE is a new enum member, the classification is trivial. [COMPUTED/LOW]

**Severity:** L — the acceptance criteria pin the behavior regardless. The executor can make either choice; both work within the specified tests.

---

### [L-2] 44-03 Task 2 <files> still lists advisory.py (coder L-2, duplicate ref)

**Location:** 44-03-PLAN.md Task 2 line 204 (read-first artifact)
**Evidence:** Same as coder L-2. Under the revised D-27, advisory.py needs NO change. The read-first entry is harmless. [KNOWN/HIGH]

**Severity:** L — no-op artifact. Not blocking.

---

## Verdict

All 6 CP1b blockers are **CONFIRMED-FIXED** with binding-layer evidence (CONTEXT decisions + task behavior/action/acceptance) verified against live ground truth at main @ 8bd01bc. The five CP1 convergence warnings (W-1..W-5) are all resolved in the current plan text (verified by re-read of current plan files, mtimes 22:13-22:16, post-CP1 convergence check).

**Blockers:** 0
**Residual concerns:** 1 medium, 2 low

**NO OBJECTION from reviewer.**

SCORECARD: B=0 H=0 M=1 L=2