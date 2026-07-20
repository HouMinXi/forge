# ADR-0002: MCP as turnkey delivery path with real backend

**Date**: 2026-06-29
**Status**: accepted
**Deciders**: Minxi Hou
**Supersedes**: [ADR-0001](0001-mcp-not-the-adoption-path.md)

## Context

ADR-0001 concluded MCP is not the adoption path and prioritized G1-G5
substance gates before surface integration. That analysis separated
substance from surface correctly but drew the wrong conclusion: treating
MCP as optional while the industry standardized on it.

Research (2026-06-29, exa) shows:

1. **MCP is the universal IDE integration standard.** VS Code, PyCharm,
   IntelliJ, Cursor, Windsurf, and Codex all support MCP natively with
   the same JSON config format. VS Code has an MCP server gallery and
   one-click install. JetBrains IDEs have Auto-Configure buttons. Codex
   uses config.toml but the same stdio/http transport.

2. **Competing code review MCP servers already ship turnkey.** At least 5
   open-source code review MCP servers exist (code-review-mcp,
   mcp-code-review, mcp-pr, multi_mcp, opencodereview/mcp-server). They
   all follow the same pattern: npm/pip install, one `claude mcp add`
   command with an API key env var, done. Users review code in the next
   session.

3. **forge already has the MCP server code.** Phase 33 shipped
   mcp_server.py (402 lines, 6 tools, 50 tests) with FastMCP stdio
   transport. The entry point exists in pyproject.toml. The code works.
   It was never wired.

4. **The customer expectation is turnkey.** Install the package, configure
   a backend model URL and API key, start reviewing. No trust ceremony,
   no baseline ceremony, no gate.yaml hand-editing, no worktree symlink
   dance. Those are internal machinery the customer should never see.

5. **G1-G5 are not a gate BEFORE MCP -- they are work to make MCP
   deliver real value.** MCP is the delivery vehicle. G1-G5 (real backend
   default, false-green traps closed, hot-session external review,
   dogfood parity, dimension coverage) are what goes INTO the vehicle.
   Building the vehicle is not blocked on filling it -- but shipping the
   vehicle empty (return Verdict.PASS) is shipping a lie.

## Decision

MCP is the primary delivery path for forge. The product ships as a pip
package with an MCP entry point that any IDE can launch. The customer
experience is:

```bash
pip install code-review-forge[mcp]

# Claude Code
claude mcp add forge-review -e REVIEW_API_KEY=sk-xxx -- code-forge-mcp

# VS Code (.vscode/mcp.json)
{"servers":{"forge":{"command":"code-forge-mcp","env":{"REVIEW_API_KEY":"sk-xxx"}}}}

# Codex (~/.codex/config.toml)
[mcp_servers.forge]
command = "code-forge-mcp"
env = { REVIEW_API_KEY = "sk-xxx" }

# PyCharm: Settings > Tools > AI Assistant > MCP > Add > paste same JSON
```

The MCP server handles all internal machinery (gate.yaml generation, trust,
baseline, backend resolution) without exposing it to the customer. The
customer configures exactly two things:

1. **Backend model endpoint** (env var or first-run prompt, with a sensible
   default like deepseek-chat or a free tier).
2. **API key** for that endpoint.

### G1-G5 closure plan (substance inside the vehicle)

G1-G5 are now scoped as work items to make the MCP path deliver real
reviews, not as prerequisites that block MCP wiring:

| Gate | What | How | Priority |
|------|------|-----|----------|
| G1 | Real backend is default | MCP server resolves backend from env var at startup, never falls through to inline PASS | P0 |
| G2 | False-green traps closed | MCP server returns error (not PASS) on parse failure, empty diff, no backend | P0 |
| G3 | Hot-session or reliable external | MCP subprocess calls code-forge review with configured backend; cold context acceptable because diff is passed directly | P1 |
| G4 | Dogfood parity | Run MCP-path review on 3 real forge changes, compare findings with trio | P1 |
| G5 | Dimension coverage | Audit pass_configs prompts vs trio skill prompts, port gaps | P2 |

### Trio retirement sequencing

1. Ship MCP with real backend (G1+G2 closed).
2. Dogfood MCP-path on forge itself (G4).
3. Port missing dimensions (G5).
4. Retire trio skills, route all review through MCP/CLI.

## Alternatives Considered

### Alternative 1: Keep MCP as optional, prioritize Skill path (ADR-0001)
- **Pros**: No new work, Skill path works in Claude Code hot context.
- **Cons**: Ignores industry direction, no cross-IDE reach, no turnkey
  onboarding, forge stays invisible outside Claude Code.
- **Why not**: The market has moved. Every competitor ships MCP. Staying
  Skill-only means forge is invisible in VS Code, Cursor, PyCharm, Codex.

### Alternative 2: Ship MCP now with inline PASS outlet
- **Pros**: Immediate cross-IDE visibility.
- **Cons**: Ships a lie. Customer installs, gets PASS on everything.
  Destroys trust worse than not shipping.
- **Why not**: G1+G2 must close first. The code exists but the backend
  resolution must work turnkey before the MCP server is advertised.

### Alternative 3: Rewrite MCP server to embed review logic in-process
- **Pros**: No subprocess overhead.
- **Cons**: Duplicates entire CLI pipeline, doubles maintenance surface.
- **Why not**: Subprocess pattern (MCP calls code-forge review) is what
  every competitor uses. Diff is passed directly, not re-derived.

## Consequences

### Positive
- forge becomes installable and usable in every major IDE with one command.
- Customer onboarding matches industry standard (pip install + API key).
- Internal machinery (trust, baseline, gate.yaml) hidden behind MCP server.
- G1-G5 work is scoped with clear done-conditions and priorities.
- Trio retirement has a concrete sequencing plan.

### Negative
- G1+G2 must close before advertising (shipping empty is worse than not
  shipping).
- MCP subprocess adds ~1-2s startup overhead per review invocation.
- MCP server becomes primary support surface (errors must be clear).

### Risks
- Backend API rate limits (mimo-pro 429). Mitigation: default to
  deepseek-chat (reliable), add retry with backoff in MCP layer.
- MCP Python SDK v2 breaking changes (beta 2026-06-30, stable 2026-07-27).
  Mitigation: pin mcp>=1.27,<2 until v2 stabilizes.
- Customer misconfigures API key. Mitigation: MCP server validates key on
  startup with probe request, returns human-readable error.
