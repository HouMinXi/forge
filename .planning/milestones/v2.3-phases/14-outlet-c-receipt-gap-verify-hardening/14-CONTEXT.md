---
phase: 14
name: Outlet C Receipt Gap + Verify Hardening
date: 2026-06-06
requirements:
  - SHRK-01
  - SHRK-04
---

# Phase 14 Context: Outlet C Receipt Gap + Verify Hardening

<domain>
Every review path (subprocess/inline/subagent) must produce verifiable
receipts that `code-forge verify` can validate. Phase 14 closes the
Outlet C receipt gap and hardens verify checks 5 and 6 against
zero-cost fabrication.
</domain>

<decisions>

## SHRK-01: Outlet C Receipt Gap -- Method B (StateMachine)

**Decision:** Outlet C (subagent) routes through StateMachine (machine.py)
instead of returning `Verdict.PASS` early.

**Rationale:** The mechanical guarantee comes from the Python spawn loop
controlling per-pass fan-out, not from parsing method. The orchestrator
(outlet_c.py) spawns N fresh Agents, collects structured JSON from each,
feeds it to StateMachine which writes standard receipts and counts
consecutive-clean cycles. The reviewer cannot lie about "I ran 3 cycles"
because Python controls the loop.

### Interface: Structured JSON from reviewer (not NL parser)

**Decision:** SKILL.md requires each reviewer pass to output a JSON block
conforming to receipt schema. Orchestrator JSON.parse's it directly.

**Why not NL parser:** Same anti-shirk strength (spawn loop is the
guarantee, not parsing method), but NL extraction is fragile (severity
wording, file:line format variations). DRY-reusing a fragile layer does
not outweigh reliability.

### Mandatory guardrail: fail-closed

**Decision:** If reviewer output fails JSON schema validation (missing
fields, malformed, no JSON block found), orchestrator treats that pass
as FAIL. Never skip, never treat as clean.

**Why:** Without fail-closed, reviewer learns "output garbage -> parser
fails -> fail-open -> treated as clean". This moves the hole from receipt
layer to parse layer. Schema validation provides a clean fail-closed
boundary that NL parsing cannot.

### Architecture: outlet_c.py separate module

**Decision:** New `src/code_forge/outlet_c.py` contains spawn loop +
JSON parse + StateMachine feed. Not inlined in cli.py.

### Honest ceiling

This prevents process shirking (N passes really ran, cycles really
counted). It does NOT prevent content fabrication (reviewer sees bug but
writes findings: []). Content fabrication is a different concern
addressed by adversarial verify / multi-reviewer voting in future phases.

## SHRK-04: Check 5 Hardening -- Per-Hunk Excerpt Threshold

**Decision:** Each diff hunk must have at least 1 code_excerpt,
UNCONDITIONALLY regardless of findings count. Empty excerpts on any
hunk = FAIL. When findings>0, each finding must ALSO carry an excerpt
anchored to its file:line. Reporting a finding can never relax check 5.

**Bypass closed:** The original "when findings=0" condition let a
fabricator report one throwaway finding to switch the per-hunk gate off,
leaving other hunks unwitnessed.

**Qualitative framing:** Check 5 is a cost-raiser, NOT a real gate.
LLM can produce receipts satisfying any static threshold. The realistic
goal is: plug the zero-cost free channel (empty receipts pass today)
and raise fabrication cost. Real gate is R1/R2/R3 dynamic doors.

**Why per-hunk (not flat K):** Flat K=3 can pile in one hunk, leaving
other changed blocks unwitnessed. Per-hunk forces coverage across all
modification sites.

**Why not 60% line coverage (option 2):** Couples check 5 and check 6
on the same "line coverage" metric -- single point of failure. Two checks
testing the same indicator via the same mechanism both fail when the
mechanism is defeated.

**verify.py documentation requirement:** Comment must explicitly state
"per-hunk excerpt is a cost-raiser, not a gate. Real anti-fabrication
is R1/R2/R3 dynamic verification." Do not let future readers mistake
check 5 for a security boundary.

## SHRK-04: Check 6 Hardening -- Coverage Anchored to Excerpts

**Decision:** Coverage derived from excerpts touching real diff lines,
NOT from self-reported covered_line_ranges field.

**Rationale:** covered_line_ranges is zero-anchor self-report -- reviewer
writes any ranges and claims 100% coverage. Excerpts have content that
can be compared against real code (verifiable, falsifiable).

**covered_line_ranges disposition:** Demote to audit-only annotation
(marked "self-reported, not measured"). If no downstream consumer exists,
delete the field entirely. Removing an unverifiable false-security field
is better than leaving it to mislead.

**Check 5 vs check 6 separation (not coupling):** Both checks anchor to
the same external objective signal (excerpts vs real diff), but measure
different things. Check 5 = "each hunk has a witness" (binary). Check 6 =
"witnessed lines cover >= 60% of diff" (quantitative). Same anchor point,
separate responsibilities. This is NOT the rejected option 1 coupling
(two checks depending on each other's self-reported output).

**Real coverage gate:** Dynamic test coverage (coverage.py, R1/R2/R3
objective measurement) is where "coverage" should come from as an
objective signal. Excerpt-derived coverage only proves "referenced real
lines", not "reviewed them".

## Fabricated Receipt Test Cases

All tests follow fail-before/pass-after pattern: prove the fabrication
passes pre-hardening verify, then fails post-hardening verify.

### Content layer (1-4)

1. **All-green no excerpt:** 9 receipts, findings=0, code_excerpts=[] --
   check 5 per-hunk threshold blocks
2. **Excerpt content mismatch:** excerpt file:line exists but content
   differs from actual code -- check 5 verbatim comparison blocks
3. **Excerpts piled in one hunk:** 3 excerpts all in hunk A, hunks B/C
   have zero -- check 5 per-hunk threshold blocks
4. **Excerpt coverage <60%:** all excerpts touch only 40% of diff lines --
   check 6 excerpt-derived coverage blocks
5. **findings>0 with unwitnessed hunk:** report one finding (findings!=0),
   leave hunk B with zero excerpts -- unconditional per-hunk threshold must
   still block (proves the findings>0 bypass is closed)

### Mechanical guarantee layer (A-B, mandatory)

A. **Malformed/missing-field JSON:** reviewer outputs unparseable garbage
   or JSON missing required fields -- fail-closed guardrail blocks. Core
   SHRK-04 interface test. Not testing this = fail-open naked.
B. **Pass count / non-consecutive clean:** only 5 receipts claiming 3
   cycle clean; or clean-dirty-clean claiming convergence --
   StateMachine cycle counting blocks. Core SHRK-01 b test.

### Self-report override (E, recommended)

E. **covered_line_ranges=100% but excerpts insufficient:** verify must
   ignore self-reported field, use excerpt-derived coverage only.
   Confirms check 6 reform actually works.


### Excerpt comparison baseline: the reviewed snapshot, not the working tree

**Decision:** check 5 verbatim comparison and check 6 excerpt-derived
coverage compare excerpt content against the REVIEWED diff/blob snapshot
(code as of review time), NOT the mutable working tree. Rationale: in the
review -> fix -> re-review loop the working tree changes; comparing a
review-time excerpt against a post-fix working tree would false-fail an
honest reviewer. Anchor to the snapshot the receipt was produced against.

### outlet_c.py REUSES machine.py cycle-reset (does not reimplement)

**Decision:** forge core invariant is "any finding resets the
consecutive-clean counter". outlet_c.py's spawn loop drives machine.py and
reuses its existing cycle-reset-on-finding logic; it must NOT reimplement
cycle counting. Outlet C cycle semantics must be identical to Outlet A.

### Per-hunk threshold: boundary hunks (explicit, not fall-through)

**Decision:** define behavior for hunks that cannot carry a new-code excerpt:
- Pure-deletion hunk (no added lines): satisfied by an excerpt of the
  deleted region, OR explicitly exempt (decide in plan). Do not silently pass.
- Binary diff / rename-only / mode-change hunk: exempt from the excerpt
  requirement (no reviewable line content), but the exemption is EXPLICIT,
  not a silent fall-through a fabricator can exploit.

### fail-before must be executable, not asserted

**Decision:** the pre-hardening verify must be runnable so fail-before is
PROVABLE -- via git-checkout of the pre-Phase-14 verify.py, or a hardening
feature-flag the test toggles. A fabrication test that cannot demonstrate
PASS on pre-hardening verify is testing nothing.

### Scope: Outlet C consumer justification

**Answer:** SKILL.md already defines Outlet C's complete protocol
(SKILL.md:1393-1431): per-pass Agent spawn, JSON schema, fail-closed,
severity-gated state machine. cli.py:690 `return Verdict.PASS` is an
unfinished stub -- it accepts `--outlet subagent` from users but does
nothing. This is not speculative building (YAGNI); it is closing the gap
between a user-facing option and its unimplemented backend. Disabling
Outlet C instead of implementing it is a valid alternative but a RETREAT
from the v2.3 anti-shirk thesis (every outlet produces receipts).

</decisions>

<deferred>
- Content fabrication defense (reviewer lies about findings) -- future
  phase, requires adversarial verify or multi-reviewer voting
- Outlet B (inline) receipt parity -- inline currently has same gap but
  lower priority (trusted terminal sessions)
</deferred>

<canonical_refs>
- src/code_forge/verify.py -- current check 5/6 implementation
- src/code_forge/machine.py -- StateMachine, write_receipts call
- src/code_forge/receipt.py -- receipt write/read, covered_line_ranges
- src/code_forge/cli.py:690 -- Outlet C early return (to be replaced)
- src/code_forge/skills/code-forge/SKILL.md -- Outlet C dispatch rules
- .planning/REQUIREMENTS.md -- SHRK-01, SHRK-04 definitions
</canonical_refs>

<code_context>
- machine.py:661-666 -- write_receipts() call site, receipt format
- verify.py:127-165 -- check 5 (excerpt verification) and check 6
  (coverage) current implementation
- verify.py:68-76 -- _covered() and _cycle_covered() helpers using
  covered_line_ranges (to be replaced by excerpt-derived coverage)
- cli.py:688-692 -- Outlet C early return Verdict.PASS (to be replaced
  with outlet_c.py call)
- receipt.py:69 -- write_receipts() signature and receipt JSON schema
</code_context>

### CRITICAL: Excerpt source = reviewer-provided, forge-verify-only

**Decision:** Excerpts in receipts come FROM THE REVIEWER's structured
JSON output. Forge (receipt.py) ONLY validates them (content matches real
code, per-hunk coverage). Forge does NOT auto-generate excerpts from diff.

**Why this is non-negotiable:** If forge auto-generates per-hunk excerpts,
check 5 ("every hunk has an excerpt") is a tautology -- forge creates the
condition it then checks. The reviewer is completely uninvolved. A fabricator
does nothing and passes. Check 5 anti-shirk value = zero. Entire SHRK-04
is self-deception.

**Integration chain:** outlet_c.py (collects reviewer JSON with excerpts
field per finding + per-hunk witness excerpts) -> receipt.py (assembles
receipt from reviewer-provided excerpts, validates content against real
code, does NOT generate) -> verify.py (checks per-hunk coverage + content
match against diff snapshot).

**_build_excerpts() direction change:** Current implementation "generates
excerpts from findings" -- this is the WRONG direction for hardened verify.
Hardened receipt.py reads reviewer-provided excerpts and validates them.
It does not invent excerpts the reviewer did not provide.

### Cycle-reset-on-finding: confirm comes with StateMachine reuse

**Decision:** outlet_c.py reuses StateMachine which includes cycle-reset
semantics (any confirmed finding resets consecutive_clean_rounds to 0).
This is the forge core invariant. Plan must VERIFY this comes automatically
with StateMachine, not just receipt writing. Do not assume -- grep for
the reset call in machine.py and confirm outlet_c.py's l1_provider return
format triggers it.

### Test failure triage rule

**Decision:** When verify hardening breaks existing tests, each failure
must be classified before updating:
- "Guards old vulnerability" (test encoded old lenient behavior) -> UPDATE
  the test to match hardened behavior. This is expected and correct.
- "Catches new bug in hardening code" -> INVESTIGATE. Do not update the
  test to make it pass -- that hides the bug.

Ask for each failing test: "Is it defending the old hole, or catching
a new one?" (per feedback_test_infra_ground_truth)
