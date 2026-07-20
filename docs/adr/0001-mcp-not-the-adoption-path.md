# ADR-0001: MCP is not the adoption path for Claude Code

**Date**: 2026-06-29
**Status**: superseded by ADR-0002
**Deciders**: Minxi Hou

## Context

forge v2.6 shipped a Phase 33 MCP server (mcp_server.py, 402 lines, 6 tools,
50 tests) intended to provide IDE-native integration for forge review. The
code exists and works in isolation, but after shipping:

1. **Zero MCP config exists.** `~/.mcp.json` has no forge entry. Claude Code
   settings have zero forge MCP references. The MCP server has never been
   registered or used in a real review session.

2. **Claude Code uses the Skill path, not MCP.** The daily review pipeline
   runs the inline trio (qodo-review / code-review-expert / adversarial-qe)
   as Claude Code Skills. The `/code-forge` Skill (1709 lines) is also a
   Skill, not an MCP tool. Claude Code's native workflow is: user prompt ->
   Skill invocation -> model-driven review in hot context. MCP adds a
   subprocess boundary that breaks this hot-context advantage.

3. **MCP adoption in mainstream IDEs is nascent.** VS Code, PyCharm, and
   Codex have varying levels of MCP support, but none have standardized on
   MCP as the primary tool integration path for code review workflows. MCP
   is a transport protocol, not a product integration -- the onboarding
   friction (config, trust, process discovery) is per-IDE, per-user.

4. **G1-G5 trio retirement gates remain open.** The real blocker to forge
   adoption is not invocation surface (Skill vs MCP vs CLI) but review
   substance: the inline outlet is `return Verdict.PASS`, hot-session
   external review is unproven (G3), parallel dogfood comparison is undone
   (G4), and dimension coverage is unverified (G5). MCP solves none of
   these.

5. **Two orthogonal axes were identified but only one was built.** The
   adoption state analysis (2026-06-26) correctly identified invocation
   surface and review substance as independent axes. Phase 33 built
   surface (MCP) while substance gaps (G1-G5) stayed open. The MCP server
   is a solution to a problem that is not currently the bottleneck.

## Decision

MCP is not the adoption path for forge in Claude Code. The forge review
pipeline integrates via the existing Skill mechanism (`/code-forge`) and
the CLI (`code-forge review`). The MCP server code remains in the codebase
as an available option for future IDE integrations, but is not actively
wired, promoted, or maintained as a primary path.

The adoption priority is closing G1-G5 substance gaps so the trio can be
retired and `/code-forge` becomes the single review entry point with real
external backend verification. Specifically:

- **G3 (hot-session external review)**: make `code-forge review` runnable
  in the hot session via the CN backend, matching what the trio does today.
- **G4 (dogfood parity)**: run trio and code-forge in parallel on >= 3 real
  changes, prove no detection regression.
- **G5 (dimension coverage)**: audit forge's pass_configs against the trio's
  prompt coverage, port any missing dimensions.

## Alternatives Considered

### Alternative 1: Wire MCP as the primary integration
- **Pros**: IDE-native tool surface, protocol-level interop, future-proof
  if MCP becomes the standard tool transport.
- **Cons**: Adds subprocess boundary (breaks hot context), requires per-IDE
  config onboarding, solves surface not substance, MCP ecosystem immature
  for code review workflows, cold-agent review truncates at ~65K context.
- **Why not**: The bottleneck is review substance (G1-G5), not invocation
  surface. Wiring MCP without closing substance gaps delivers a tool that
  looks integrated but reviews nothing -- the same false-green problem the
  inline outlet already has.

### Alternative 2: Retire trio now, force all review through code-forge
- **Pros**: Eliminates redundancy, forces adoption.
- **Cons**: code-forge inline outlet is `return Verdict.PASS` (false-green),
  hot-session external review unproven (G3), no parallel dogfood data (G4),
  dimension coverage unverified (G5). Retiring the battle-tested trio for
  an unproven pipeline risks silent review regression.
- **Why not**: All five gates must pass first. Premature retirement is a
  downgrade disguised as consolidation.

### Alternative 3: Do nothing, keep the status quo indefinitely
- **Pros**: Zero risk, trio works.
- **Cons**: Permanent redundancy, 17 phases of forge engineering unused in
  daily workflow, MCP server code rots.
- **Why not**: The status quo is acceptable short-term but the goal is
  still to close G1-G5 and retire the trio. This ADR is not "never"; it
  is "substance before surface."

## Consequences

### Positive
- Focus shifts to the real bottleneck (G1-G5 substance gaps) instead of
  invocation surface plumbing.
- No wasted effort maintaining MCP config across IDE versions and
  user machines while the review engine itself has open false-green traps.
- The Skill path (`/code-forge`) already works in Claude Code's hot context,
  which is where review quality is highest.

### Negative
- Phase 33 MCP server (402 lines, 50 tests) sits unused until either G1-G5
  close and MCP becomes the natural next step, or an external IDE integration
  demand appears.
- No IDE-native tool discovery (MCP's main UX advantage) -- users must know
  to invoke `/code-forge` or `code-forge review` by name.

### Risks
- MCP ecosystem matures faster than expected and becomes the standard tool
  integration path. Mitigation: the server code exists and is tested; wiring
  it is a config change, not a rebuild.
- G1-G5 closure stalls and the trio is never retired. Mitigation: G3 and G4
  are concrete, testable gates with clear done-conditions -- not open-ended
  research.
