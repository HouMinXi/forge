# P44 post-fix consistency confirmation — devops

**Date:** 2026-08-22
**Task:** t_51da6f0a
**Reviewer profile:** devops
**Code base:** main @ 8bd01bc (plan-level consistency check)

---

## Check 1: Broadened except & isinstance guard (reviewer M-1 fix)

**Source:** `.planning/phases/44-LEDGER-eval-on-duty/44-01-PLAN.md` Task 2 (lines 281-289, 316-320)

**Evaluation & Evidence:**
- **Crash Prevention:** `yaml.YAMLError` (malformed YAML), `AttributeError` (`None.get` from `yaml.safe_load` on empty file), and `TypeError` are wrapped alongside `OSError` in a single try/except block. `isinstance(data, dict)` explicitly guards `data.get("ledger")`.
- **Fail-Open Semantics:** Caught exceptions degrade to `infra_errors` + stderr warning, leaving the review verdict intact and ledger treated as ENABLED (fail-open).
- **Operability / Exception Completeness:**
  - `FileNotFoundError` (missing gate.yaml) is an `OSError` subclass -> covered.
  - `yaml.YAMLError` (syntax error in gate.yaml) -> covered.
  - Empty file returning `None` -> guarded by `isinstance(data, dict)` + `AttributeError` -> covered.
  - *Note on UnicodeDecodeError:* If gate.yaml contains non-UTF-8 bytes or a stray binary payload, `open(..., encoding='utf-8').read()` raises `UnicodeDecodeError` (`ValueError` subclass). In the repo (e.g. `doctor.py:94`, `install_hooks.py:822`), config loaders catch `(yaml.YAMLError, OSError, UnicodeDecodeError)`. Adding `UnicodeDecodeError` (or `ValueError`) to the except tuple during execution is recommended for completeness, but this does not block the architectural consistency of the fix.

**Verdict:** CONFIRMED — Broadened except and isinstance guard prevent malformed/empty `gate.yaml` crashes and maintain fail-open isolation.

---

## Check 2: advisory.py removal from 44-03 scope (scribe L-1 fix)

**Source:** `.planning/phases/44-LEDGER-eval-on-duty/44-03-PLAN.md` (frontmatter `files_modified`, Task 2 `<files>`, Task 2 title)

**Evaluation & Evidence:**
- Frontmatter `files_modified` (lines 7-12) contains only `machine.py`, `ledger.py`, `gate_check.py`, `test_machine_ledger.py`, `test_convergence.py`. `advisory.py` is absent.
- Task 2 `<files>` (line 204) lists `gate_check.py`, `machine.py`, `tests/test_convergence.py`. `advisory.py` is absent.
- Task 2 name (line 203) correctly reflects `"pinned_paths suppression + style-finding downgrade to non-blocking disposition"`.
- `44-03-PLAN.md:119-124` interfaces section explicitly states `"advisory.py needs NO change in this plan"`.
- All remaining mentions of `advisory.py` are explanatory context citing `AdvisoryFinding` contract (`advisory.py:26-36`) to document why style findings stay fingerprinted `StateFinding` instances with non-blocking disposition rather than being routed to `AdvisoryFinding` (which intentionally excludes fingerprints).
- No dangling edits or incomplete refactor artifacts exist.

**Verdict:** CONFIRMED — `advisory.py` cleanly removed from 44-03 modification scope without dangling requirements.

---

## Check 3: New objections from DevOps / Operations perspective

**Evaluation & Evidence:**
- No new operational, deployment, CI runtime, or failure-isolation risks introduced by these two fixes.
- Fail-open fallback on config read errors guarantees CI stability.
- Scope reduction on `advisory.py` eliminates unnecessary file modifications and prevents data model ejection.

**Verdict:** CONFIRMED — No new objections.

---

## Conclusion

STILL NO OBJECTION from devops.

SCORECARD: B=0 H=0 M=0 L=0
