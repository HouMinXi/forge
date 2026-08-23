# Phase 44 Post-Fix Consistency Review (scribe/facts)

**Document Identifier:** `FORGE-P44-CPC-SCRIBE-20260822`  
**Reviewer:** 侯敏熙 (Minxi Hou)  
**Baseline:** `main` @ `8bd01bc`  
**Target Plans:** `.planning/phases/44-LEDGER-eval-on-duty/44-01-PLAN.md` (Task 2), `.planning/phases/44-LEDGER-eval-on-duty/44-03-PLAN.md` (Task 2 & Frontmatter)

---

## 1. Check 1: Broadened Exception Handling & Fail-Open Guard

**Target:** `44-01-PLAN.md` Task 2 (lines 280-289, 317-320)

### Findings & Evidence
- **Except Scope [KNOWN HIGH]:** `44-01-PLAN.md` lines 281-282 specify wrapping the raw-YAML read (`yaml.safe_load` + `dict.get`) and write in a single try/except handling `(OSError, yaml.YAMLError, AttributeError, TypeError)`.
  - `yaml.YAMLError` catches all YAML parser/scanner/reader errors (including `yaml.reader.ReaderError` and malformed YAML syntax).
  - `AttributeError` / `TypeError` catches empty YAML files (`yaml.safe_load("")` -> `None`), scalar root YAMLs (`yaml.safe_load("true")` -> `bool`), or un-subscriptable returns.
  - `OSError` catches missing files, permission errors, and I/O failures.
  - `isinstance(data, dict)` guard is explicitly required before `.get()` access (line 289).
- **Fail-Open Invariant [KNOWN HIGH]:** Lines 286-288 specify that any caught exception logs to `infra_errors` + `stderr`, leaves review verdict unchanged, and treats ledger as ENABLED (fail-open).
- **Bug-Injection Verification [KNOWN HIGH]:** Lines 317-320 require test injection of malformed YAML (`yaml.YAMLError`) and empty YAML (`safe_load -> None -> AttributeError`) to verify the review returns its verdict without crashing.

**Verdict: CONFIRMED** — The broadened exception tuple `(OSError, yaml.YAMLError, AttributeError, TypeError)` plus `isinstance(data, dict)` fully covers malformed, empty, scalar, and missing `gate.yaml` conditions without crashing review runs or changing verdicts.

---

## 2. Check 2: Removal of `advisory.py` from 44-03 Scope

**Target:** `44-03-PLAN.md` Frontmatter `files_modified` & Task 2 `<files>`

### Findings & Evidence
- **Frontmatter & Task `<files>` [KNOWN HIGH]:**
  - `44-03-PLAN.md` lines 7-12 (`files_modified`): `src/code_forge/machine.py`, `src/code_forge/ledger.py`, `src/code_forge/gate_check.py`, `tests/test_machine_ledger.py`, `tests/test_convergence.py`. `advisory.py` is absent.
  - Line 203: Task 2 renamed to `"pinned_paths suppression + style-finding downgrade to non-blocking disposition (D-26, D-27)"`.
  - Line 204 (`<files>`): `src/code_forge/gate_check.py, src/code_forge/machine.py, tests/test_convergence.py`. `advisory.py` is absent.
- **Dangling References Check [KNOWN HIGH]:**
  - `44-03-PLAN.md` line 124 explicitly notes: `"advisory.py needs NO change in this plan."`
  - Line 208 keeps `src/code_forge/advisory.py` under `<read_first>` for contract reference only.
  - Lines 233-235, 270-272, and 297-299 reference `advisory.py` exclusively as architectural rationale explaining why style findings must NOT be routed to `AdvisoryFinding` (which structurally lacks fingerprints, `advisory.py:33-37`).
  - No action, acceptance criterion, or behavior item requires modifying `advisory.py`.

**Verdict: CONFIRMED** — `advisory.py` is cleanly removed from modification scope with zero dangling requirements.

---

## 3. Check 3: New Objections or Inconsistencies

**Target:** Full text of `44-01-PLAN.md` and `44-03-PLAN.md` vs codebase invariants

### Findings & Evidence
- No new blockers, high, medium, or low issues introduced.
- Cross-doc alignment between 44-01 and 44-03 remains intact: 44-01 writes `UNADJUDICATED` rows via `resolve_ledger_root`, 44-03 consumes them via `known_terminal_fingerprints(resolve_ledger_root(cwd))`.
- Naming, docstring contract (five-state vocabulary), and fail-open behaviors match across all plans.

**Verdict: CONFIRMED** — No new objections introduced.

---

## Conclusion & Scorecard

**STILL NO OBJECTION from scribe.**

```
SCORECARD: B=0 H=0 M=0 L=0
```
