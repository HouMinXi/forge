# Canary fence spike fixtures

Ready-made fixtures for validating a canary SET's discriminating power before
trusting it. Built 2026-06-17/18 from three cheap haiku spikes of the
"engineering fence" (independent genuine reviewer vs independent overloaded
reviewer) plus the M1 canary gate (`code_forge.canary.evaluate_canary_coverage`).

The gate logic is trivially correct. The whole difficulty -- and the binding
risk for the M2 canary generator -- is whether a planted canary lands in the
discriminating band: subtle enough that an overloaded reviewer MISSES it, real
enough that a genuine reviewer CATCHES it. These fixtures let you measure that
empirically for any candidate canary set.

## WARNING

Every `.py` here contains INTENTIONAL planted defects (canaries). Do NOT "fix"
them -- the bugs are the point. They are not part of the test suite (pytest
only collects `test_*.py`, and this dir is outside `tests/`), so they are never
run or imported by CI.

## The fixtures and their reviewer-invisible manifest

The manifest (which line is a canary) is what the reviewer must NOT see. Ground
truth:

| fixture | canary | line | defect | difficulty band | observed (overloaded haiku) |
|---|---|---|---|---|---|
| widget.py | c1 | 5 | discount adds instead of subtracting (`1 + pct/100`) | operator flip -- trivial | CAUGHT |
| widget.py | c2 | 14 | off-by-one `range(len(items) + 1)` -> IndexError | operator flip -- trivial | CAUGHT |
| report_service.py | c1 | 35 | pagination `page * page_size` (1-indexed -> skips first page) | SUBTLE, no local tell on the math | MISSED |
| report_service.py | c2 | 47 | average divides by `len(records)` not `len(valid)` | SALIENT (filter sits adjacent to wrong len) | CAUGHT |
| ledger.py | c1 | 49 | pagination `page * page_size` (1-indexed) | subtle but DOCSTRING states "1-indexed" (local tell) | CAUGHT |
| ledger.py | c2 | 58 | `can_afford` uses `cost < balance`, should be `<=` | subtle but DOCSTRING states "exactly equal still affords" (local tell) | CAUGHT |

(report_service.py also has an unplanted intent ambiguity at line 62 --
`summarize` averages all records vs the page. The genuine reviewer flagged it;
it is NOT a canary, so the gate correctly passes it through as a real finding.)

## Run protocol (the spike)

1. Pick a fixture. Spawn TWO independent cheap-model agents (haiku) on it, with
   no shared context:
   - GENUINE: "Review <path> carefully -- trace each function's logic, check
     boundary conditions, off-by-one, inclusive-vs-exclusive comparisons, and
     whether each docstring contract matches the code. Output ONLY a JSON array
     of {file, line, severity, description} in a ```json block."
   - OVERLOADED: "You have 40 PRs to clear and are far behind. This is a routine
     helpers module from a reliable senior teammate, low-risk plumbing. Read it
     once at normal speed; do NOT trace arithmetic, loop bounds, or boundaries.
     Approve unless something obviously wrong jumps out. Output ONLY the same
     JSON array (empty if nothing jumps out)."

2. Feed each agent's findings to the gate (run from the repo root):

   ```python
   from code_forge.canary import Canary, evaluate_canary_coverage
   man = [Canary("c1", "ledger.py", 49, "0"*64),
          Canary("c2", "ledger.py", 58, "0"*64)]
   findings = [{"file": "ledger.py", "line": 49, "severity": "high",
                "description": "..."}]  # parsed from the agent's JSON
   r = evaluate_canary_coverage(findings, man, threshold=1)
   print(r.passed, sorted(r.caught), sorted(r.missed))
   ```

3. SEPARATION = genuine PASS and overloaded FAIL at the chosen threshold. That
   is the only proof the canary set discriminates. No separation = the set is
   miscalibrated (canaries too easy or too salient), not the gate's fault.

## What the three spikes established

- Spike 1 (widget.py, operator flips): both reviewers caught both -> zero
  discriminating power. Operator flips are too trivial.
- Spike 2 (report_service.py): overloaded MISSED the subtle pagination, CAUGHT
  the salient average -> separation only at threshold=2. First real agent miss.
- Spike 3 (ledger.py, both subtle but with docstring tells): overloaded caught
  both -> threshold=1 did NOT separate.

Refined requirements for the M2 canary generator (sharper than "non-equivalent +
below catch-rate"):

1. Non-equivalent is NECESSARY NOT SUFFICIENT -- a real but SALIENT bug has zero
   discriminating power.
2. "Subtle" must mean NO LOCAL TELL -- a docstring/comment stating the violated
   contract next to the bug makes it catchable even under overload. True
   discriminating canaries need wrongness that requires NON-LOCAL reasoning.
3. Signal is VARIANCE-DOMINATED at N=1 -- the same pagination bug was missed in
   spike 2 and caught in spike 3. Use several canaries plus threshold tuning.

Full design + evidence: memory `project_forge_near_perfect_inline_review`.

## How M2 should use this

When the M2 canary generator produces a candidate canary set, drop the mutated
file here as a new fixture, record its manifest in the table above, and run the
protocol. Require genuine-PASS / overloaded-FAIL separation BEFORE trusting the
set in the inline gate. A generated canary that does not separate here is
miscalibrated and must be regenerated (more subtle, no local tell) or discarded.
