# Phase 3: R3 (e2e coverage) - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md -- this log preserves the alternatives considered.

**Date:** 2026-05-26
**Phase:** 03-r3-e2e-coverage
**Areas discussed:** L3 runner integration, heuristic detection logic, components.yaml schema, R4 docs update

---

## L3 Runner Integration (D-01)

| Option | Description | Selected |
|--------|-------------|----------|
| A: Independent L3 runner | New l3_runner DI, source="E2E", _run_l3_phase() after L2 | |
| B: L0 virtual parser | As L0 tool, source="L0". Zero pipeline change but semantic mismatch | |
| C: Lightweight independent module | New e2e_check.py, source="E2E_CHECK", skip autofix/falsify | |

**User's choice:** Option C, grounded by main session code analysis.
**Notes:** Main session read machine.py and state.py to verify: L0 enters
autofix loop (wrong for coverage gaps), L1 enters falsifier (irrelevant).
MUTANT (L2) precedent is the exact shape needed -- independent phase, direct
append, skip autofix/falsify. Option B rejected: "zero pipeline change" is
the CLAUDE.md "simpler/easier" trap. Option A rejected: unnecessary new DI
slot when the MUTANT pattern already exists. Decision grounded in code evidence,
not first-listed preference.

---

## Heuristic Detection Logic (D-02a, D-02b)

### D-02a: Signature change detection

| Option | Description | Selected |
|--------|-------------|----------|
| Added-lines regex only | Scan hunk added lines for def/func patterns | |
| Hunk header only | Use git section_header for function context | |
| Added-lines UNION section_header | Both: regex for new/modified, section_header for multi-line interior edits | |

**User's choice:** Added-lines UNION section_header.
**Notes:** Initial proposal was added-lines only. Main session identified the
multi-line signature gap: `+   b: str,` inside a multi-line def matches
neither `def` nor `->`. section_header recovers this case (verified:
`unidiff.Hunk.section_header` contains enclosing function context even under
`-U0`). Correction documented in R3-CONSUMER-INPUT.md "Heuristic-detection
note."

### D-02b: Source directory grouping

| Option | Description | Selected |
|--------|-------------|----------|
| First two path components | src/forge, cmd/server, packages/auth | |
| First path segment (configurable) | Default first segment, configurable, reuse components.yaml | |

**User's choice:** First path segment (configurable) + components.yaml reuse.
**Notes:** Initial proposal was "first two components." Main session grounded
against code/kernel/networking: bonding + bonding/common -> 2 groups -> FP
(both are bonding subsystem). common + common/lib -> FP (both are common hub).
No fixed depth works across project types. Correction: configurable default +
when components.yaml exists, Layer 1 MUST derive grouping from Layer 2 map.
Single source of truth. Documented in R3-CONSUMER-INPUT.md "Adjustment 4."

---

## components.yaml Schema (D-03)

| Option | Description | Selected |
|--------|-------------|----------|
| SPEC original (data_paths pairs only) | Peer-pair symmetric model | |
| shared_deps with explicit dependents list | Hub with enumerated dependents | |
| shared:true marker + co-occurrence trigger | Hub marker, dependents from source-graph, trigger on hub+dependent co-occurrence | |

**User's choice:** shared:true + co-occurrence trigger.
**Notes:** Initial proposal included shared_deps with enumerated dependents
(76 subsystems for common/). Main session identified two problems:
1. Trigger semantics too broad: hub change -> all components need e2e = noise.
   Correct: hub+dependent=P2 (specific pair), hub-only=L1 nudge (advisory).
2. "All other components" wastes the source-graph that Adjustment 2 builds.
   Dependents determined by source-graph, not enumeration.
Auto-detect framing corrected from "deterministic" to "mostly deterministic +
variable-interpolation best-effort tail" (three source-line forms: relative,
absolute deployment, variable interpolation).

---

## R4 Docs Update (D-04)

| Option | Description | Selected |
|--------|-------------|----------|
| Phase 3 end only | Update after R3 code merges | |
| R2 early + R3 at end | R2 PLANNED->LIVE now (debt), R3 after Phase 3 | |

**User's choice:** R2 early + R3 at end.
**Notes:** R2 already merged (Phase 2, 4a160ac) but CLAUDE.md still lists it
as PLANNED. This is outstanding debt from Phase 1 D-03. Scope: CLAUDE.md
three sections only. Separate docs commit.

---

## Claude's Discretion

- Internal module layout for e2e_check.py
- E2E_CHECK finding fingerprint scheme
- Test file organization
- Exact regex patterns per language
- Whether Layer 1 and Layer 2 logic live in one module or two

## Deferred Ideas

- R5 test layering (forge-code phase)
- Source-graph auto-detection tooling (Phase 3 ships manual authoring)
- Function-level diff-scoping for mutation
- Cross-repo impact detection
