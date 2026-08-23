# P44 post-fix consistency confirmation -- CODER

Date: 2026-08-22
Scope: verify the two post-convergence fixes (reviewer M-1 kill-switch
broadening; scribe L-1 advisory.py removal from 44-03 scope) are correct
and introduce no new problem.

---

## CHECK 1: Broadened except + isinstance guard (44-01 Task 2)

**Fix on file** (44-01-PLAN.md:281-289): ONE try/except covering the
kill-switch read AND the write, catching `OSError AND yaml.YAMLError AND
AttributeError/TypeError`; `isinstance(data, dict)` guard on the
`safe_load` result before `.get`; on any caught failure -> infra_errors +
stderr warn, verdict unchanged, ledger treated as ENABLED (fail-open).

**Verification:**

- `yaml.YAMLError` (malformed YAML) -- now covered. Before the fix only
  `OSError` was caught, which a malformed gate.yaml would blow through
  (verified: `yaml.YAMLError` is NOT an OSError subclass; confirmed
  against `gate_check.py:61` where it's caught separately). [KNOWN/HIGH]
- Empty gate.yaml -> `yaml.safe_load` returns `None` -> `.get` raises
  `AttributeError` -- now covered. [KNOWN/HIGH]
- `safe_load` returning a non-dict (e.g. a YAML scalar or list) -> the
  `isinstance(data, dict)` guard prevents the `.get` AttributeError
  class entirely, and `TypeError` covers any residual `.get` misuse.
  [KNOWN/HIGH]
- Fail-open semantics preserved: any read failure -> ledger treated
  enabled + infra_errors note, verdict untouched. Matches D-19's
  never-crash invariant. [KNOWN/HIGH]
- Missing gate.yaml -> `FileNotFoundError` (OSError subclass) -> still
  caught. [KNOWN/HIGH]

**Residual gap found (NOT covered by the fix):**

`UnicodeDecodeError` on non-UTF-8 bytes in gate.yaml escapes the
broadened except. Verified live:

    python3: yaml.safe_load(open(p, 'r', encoding='utf-8')) on a file
    containing b'\xff\xfe...' raises UnicodeDecodeError
    ('utf-8' codec can't decode byte 0xff ...).

`UnicodeDecodeError` is a subclass of `ValueError`, NOT of `OSError`,
`yaml.YAMLError`, `AttributeError`, or `TypeError`. The established
repo precedent (doctor.py:94, install_hooks.py:822, snapshot.py:195)
catches `(yaml.YAMLError, OSError, UnicodeDecodeError)` for exactly this
reason. An on-disk gate.yaml with a BOM, a stray non-UTF-8 byte, or a
transient binary editor write would therefore STILL crash the CI review
out of `_write_ci_ledger_rows` -- the exact class of failure reviewer
M-1 set out to kill, reachable via a slightly different trigger than
malformed/empty YAML.

Severity assessment: same trigger class (gate.yaml content outside the
happy path), same blast radius (uncaught exception -> CI crash ->
verdict change, violating D-19's never-crash invariant), but a narrower
real-world frequency than malformed YAML. Ranks as M -- not a blocker,
but the fix is INCOMPLETE for the failure family it targets.

**Verdict: ISSUE [M]** -- broaden the except tuple by one member:
`except (OSError, yaml.YAMLError, AttributeError, TypeError,
UnicodeDecodeError)` (or `except (OSError, yaml.YAMLError, ValueError)`
since UnicodeDecodeError derives from ValueError, though that also
catches YAML-construction ValueErrors -- acceptable for fail-open).
The 44-01 Task 2 bug-injection list should gain: "feed a gate.yaml
with non-UTF-8 bytes and watch the review still return its verdict".

## CHECK 2: advisory.py removed from 44-03 scope

**Fix on file:** frontmatter `files_modified` (44-03-PLAN.md:7-13)
contains only machine.py, ledger.py, gate_check.py,
test_machine_ledger.py, test_convergence.py -- advisory.py absent.
Task 2 `<files>` (44-03-PLAN.md:204) lists gate_check.py, machine.py,
test_convergence.py -- advisory.py absent. Task 2 renamed to
"pinned_paths suppression + style-finding downgrade to non-blocking
disposition".

**Dangling-reference sweep:**

- Interfaces section (44-03-PLAN.md:119-124) explicitly states
  "advisory.py needs NO change in this plan" -- consistent with the
  scope removal, self-explanatory for the executor. [KNOWN/HIGH]
- Remaining advisory.py references in 44-03 are all READ-side or
  design-rationale: the read-first at :208 (read-only, per reviewer
  L-2 explicitly ruled harmless), the AdvisoryFinding contract
  citations at :233/:271/:297 (explaining WHY style findings stay
  StateFindings -- the core of the revised D-27), and the truths/artifacts
  blocks. None requires a modification. [KNOWN/HIGH]
- Verified `src/code_forge/advisory.py:26-37` live: AdvisoryFinding
  structurally excludes `fingerprint` (docstring lines 33-37), so
  rerouting style findings there would indeed eject them from the
  ledger/extractor -- the design decision to keep them as fingerprinted
  StateFindings with a non-blocking disposition stands, and no
  advisory.py edit is needed for it. [KNOWN/HIGH]
- 44-SCOPE-EXTENSION-ANALYSIS.md S5 row still speaks of "降为
  advisory" (lines 28/39/54), but that document is an analysis
  artifact predating the revised D-27, not an execution plan; 44-03 is
  the execution authority and it is internally consistent. Not
  dangling for execution purposes. [INFERRED/MED]

**Verdict: CONFIRMED** -- no dangling requirement to modify
advisory.py anywhere in the execution path of 44-03.

## CHECK 3: New objections introduced by these fixes?

The scribe L-1 fix is a pure scope-correction; it changes no behavior
and cannot introduce a defect. The reviewer M-1 fix is directionally
correct and closes the named triggers (malformed + empty gate.yaml),
but leaves the UnicodeDecodeError hole from CHECK 1 -- a residual M
inside the fix itself rather than a new class of problem. No B or H
issues: the fix's control flow, fail-open polarity, isinstance guard
placement, and the ONE-try/except structure are sound; test coverage
(bug-injection on malformed/empty) exists for the closed triggers.

STILL NO OBJECTION from coder, with ONE residual M finding on CHECK 1
(broaden the except tuple to cover UnicodeDecodeError) that the
architect should sweep before execution.

SCORECARD: B=0 H=0 M=1 L=0
