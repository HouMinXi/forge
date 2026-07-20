# R3 (Phase 3) Consumer Input: code/kernel/networking

Status: orchestrator input for the Phase 3 (R3 e2e coverage) research. This is
NOT a GSD phase artifact -- it is a design-input note to align the research with
a real Layer 2 consumer. Evidence basis: structural survey of
code/kernel/networking on 2026-05-26 (directory layout + the suite's own
CLAUDE.md), NOT a forge run on it.

## Why this exists

The v2.1 SPEC treats the opt-in `.forge/components.yaml` (R3 Layer 2) as a
deferred, effectively zero-consumer feature. That assessment is now wrong: there
is a real Layer 2 consumer today -- the RHEL kernel networking test suite at
code/kernel/networking. This note records what that consumer looks like and the
three design adjustments R3 needs to actually fit it.

## The consumer: code/kernel/networking

- ~107 top-level subsystem directories (bond, bridge, ipsec, openvswitch, sctp,
  tcp, ipv6, ...). These are the natural "components".
- `common/` is a UNIVERSAL shared-library hub: every test sources
  `common/include.sh`. The suite CLAUDE.md: "Main entry - sources all libs,
  tests must source this"; tests call `. ../../../common/include.sh`.
- Second-level hubs exist too: bonding/common, openvswitch/common, sctp/common,
  bridge/common, and others.
- Integration tests already exist as `<subsystem>/integration/` directories
  (ipv6/integration, sctp/integration, tcp/integration, route/integration,
  misc/integration).

## Adjustment 1: dependency SHAPE is hub-and-spoke, not peer data-paths

- The SPEC R3 model is peer components with a symmetric data path (api <-> db).
- This repo's real shape is one-to-many shared dependency: `common/` is depended
  on by N subsystems (and subsystem-level common/ by their sub-tests).
- The high-value signal here is NOT "A and B share a data path". It is: "a shared
  library (common/, or subsystem/common/) changed -- which dependent subsystems
  were validated?" -- i.e. the IMPACT RADIUS of a shared change.
- Schema implication: `data_paths -> component pairs` (symmetric) cannot cleanly
  express "common is depended on by 100 subsystems". Phase 3 must support a
  one-to-many shared-dependency relation, not only peer pairs (otherwise it
  degenerates into listing common paired with every subsystem).

## Adjustment 2: auto-detection is MORE feasible than the SPEC assumed

- The SPEC justifies opt-in/manual config with "no call graph; shell/C have none".
  That rationale is about FUNCTION-level call graphs.
- This repo's dependencies are EXPLICIT via `source` / `. include.sh` directives.
  Those are greppable, deterministic edges -- a source-dependency graph IS
  extractable (compare the shell source-to-command migration practice of tracing
  source chains).
- Viable path: deterministically extract the `source` graph -> derive the hub
  structure -> have an LLM propose semantic grouping and names -> human ratifies
  the components.yaml. This is "AI-assisted draft, human-ratified", NOT "static
  auto data-flow detection" (function-level), which remains out of reach.
- Keep this distinction explicit in the research: source-dependency graph is
  auto-detectable; function-level data flow is not.

## Adjustment 3: the e2e-artifact pattern must be configurable

- forge's default satisfying-artifact glob is `tests/e2e/*` or `test_*integration*`.
- This repo uses `<subsystem>/integration/`. The default glob does not match it.
- components.yaml (or a sibling config key) must let a project declare what counts
  as the satisfying e2e/integration artifact, per repo convention.

## Adjustment 4: Layer 1 grouping must reuse the Layer 2 component map, not a fixed rule

An initial Area-2 proposal defined a Layer 1 "source directory" as the first TWO
path components (src/forge, cmd/server, packages/auth). A grounded check against
this consumer shows no fixed N-level rule works:

- "first two components" is right for a src-layout multi-package project
  (src/auth and src/api are distinct) but WRONG for code/kernel/networking, where
  the component is the FIRST path segment (bonding, bridge, common, openvswitch).
  "first two" over-splits there and produces false positives:
  - bonding/runtest.sh + bonding/common/setup.sh -> groups bonding + bonding/common
    -> 2 distinct -> fires, but both ARE the bonding subsystem (FP).
  - common/include.sh + common/lib/net.sh -> groups common + common/lib
    -> fires, but both ARE the common hub (FP).
- "first one component" is right for code/kernel/networking but under-groups a
  src-layout multi-package project (src/auth and src/api both collapse to src).

No fixed depth wins -- grouping granularity is project-structure-specific. So:
- Layer 1 grouping granularity is CONFIGURABLE, with a sensible default (first
  path segment is the safer default given the kernel/networking evidence).
- When components.yaml exists, Layer 1 MUST derive its grouping from the Layer 2
  `component -> paths` map. One source of truth for "what is a component"; do not
  let Layer 1 keep a divergent fixed rule while Layer 2 uses the explicit map.
  This also unifies Layer 1 and Layer 2 (ties back to Adjustment 1's schema).
- Noise rationale: a non-blocking checklist that fires on intra-component nested
  changes (the FP cases above) trains the user to ignore it. The plan-forge
  experience -- F3 per-mention false positives made that tool unusable on forge --
  applies here: "non-blocking, so FPs are acceptable" is too glib for a nested
  layout like this consumer.

## Heuristic-detection note (signature change -- Area 2 / D-02a)

A related Area-2 proposal would detect a signature / return-type change by regex
over the diff's ADDED lines only, dropping unidiff's `Hunk.section_header`. That
misses multi-line signature edits, e.g.:

    def foo(
        a: int,
    +   b: str,
        c: float,
    ) -> Result:

The only added line is `+   b: str,`; it matches neither `def foo(` nor `-> type`,
so an added-lines-only scan misses a real signature change. Multi-line signatures
are common in Python/Go/Rust/C. `section_header` is exactly the recovery: git
emits the enclosing-function header even under `-U0` (observed:
`@@ ... @@ def parse_mutmut_results(...)`), so this hunk's section_header is
`def foo(`. Recommend detecting via (added-lines regex for def/func/return-type)
UNION (section_header matching a def-pattern). The union covers new functions,
single-line signature add/modify, and multi-line signature interior edits.

## Residual limit (unchanged -- keep honest)

- R3 checks for the PRESENCE of an e2e/integration artifact, not proof that it
  exercises the changed code. `sctp/integration` existing does NOT mean it runs
  the changed `common/network.sh`. This ceiling is unchanged by the adjustments
  above; do not oversell Layer 2 enforcement as coverage proof.

## Scope guard: stay within-repo

- forge reviews a single repository (its own stated gap is cross-repo impact).
- The richest cross-boundary cases in this workspace are CROSS-REPO and therefore
  OUT of R3 scope: OVS kernel <-> userspace <-> tests; bench-trafficgen <->
  trex-core; brew-build-install downstream <-> brewinstall.sh upstream.
- R3 for this consumer must stay within-repo: e.g. within code/kernel/networking,
  common/ <- subsystems. Do not let the consumer pull R3 into cross-repo
  territory; that is a separate, deferred capability.

## Net effect on Phase 3 scope

- Prior orchestrator lean ("build Layer 1 real, Layer 2 skeleton only") is
  REVISED. With a real consumer, Layer 2 is worth building -- but built around
  the hub-and-spoke shared-dependency shape, source-graph auto-detection, and
  configurable e2e-artifact patterns, NOT the api<->db peer-pair toy model.
- Recommended: use code/kernel/networking as the Layer 2 validation corpus and
  the first real components.yaml.
- bug-inject teeth (both sides): a change to common/X with no covering integration
  artifact -> the finding fires; add the covering artifact -> it clears.
