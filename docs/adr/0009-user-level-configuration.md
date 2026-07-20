# ADR-0009: User-level configuration layer

**Date**: 2026-07-02
**Status**: accepted
**Deciders**: Minxi Hou
**Related**: ADR-0006 (MCP workspace resolution)

## Context

forge's walkup resolution (ADR-0006) searches ancestors for
`.code-forge/gate.yaml` to identify the project root.  A previous
`forge_init` at `$HOME` left a `.code-forge/` directory there, making
`~` act as a walkup magnet: every subdirectory resolved to `~` as the
workspace root, breaking all MCP reviews.

The root cause is that forge uses a single namespace (`.code-forge/`)
for both project markers and user-level configuration.

### Prior art: fmf/tmt

fmf uses three namespaces (not two): (1) `.fmf/version` as tree root
markers discovered by walkup, (2) `~/.config/tmt/` for user-level
config (templates, links -- itself an fmf tree but in a separate
domain), (3) policy layers (`TMT_POLICY_FILE`/`TMT_POLICY_ROOT`)
that modify test metadata via explicit env vars only, never via
walkup discovery.

Key principle from tmt: mechanisms that can change project behavior
from outside must either be explicitly specified (env var) or live in
a fixed configuration domain -- never be discovered by walkup.

fmf's walkup does NOT skip `$HOME` (walks all the way to `/`).  It
avoids the magnet problem because `fmf init` has no `$HOME` guard
either -- it simply never had an incident.  forge has already written
a marker at `$HOME`, so forge needs an extra guard that fmf lacks.

## Decision

Five changes (D1-D5):

**D1 -- Namespace separation.**  User-level backend defaults live at
`~/.config/code-forge/config.yaml` (respects `$XDG_CONFIG_HOME`).
Explicit env override via `FORGE_CONFIG_DIR` (mirrors `TMT_CONFIG_DIR`).
Priority: `FORGE_CONFIG_DIR` > `$XDG_CONFIG_HOME/code-forge` >
`~/.config/code-forge`.  This path is structurally outside the walkup
search domain.

**D2 -- Walkup guard.**  `_resolve_workspace()` skips `$HOME` itself
when walking up ancestors.  This defuses any stale `.code-forge/` at
`~` without requiring the user to delete it.

**D3 -- Legacy compat.**  If `~/.code-forge/gate.yaml` exists but the
XDG path does not, it is read as user-level config with a deprecation
warning.  It never acts as a workspace root.

**D4 -- Backend merge.**  User-level backends merge under project
backends (project wins by name).  Only `backends:` is merged; `test:`
remains project-only.  The user-level loader is lenient (no `test:`
required; warns on malformed YAML, never crashes).

Note: this merge behavior is forge's own extension, not borrowed from
tmt.  tmt's user config does not merge defaults into tree metadata;
its "external modifies metadata" mechanism is the policy layer, which
requires explicit env var pointing.  forge's merge is driven by a
practical need (M1b: set `max_tokens` once for all projects) and
borrows tmt's domain-separation principle, not its merge semantics.

**D5 -- Regeneration guard.**  `forge_init` refuses to create project
markers at `$HOME`, directing the user to user-level config instead.
This goes beyond fmf's prior art (fmf.Tree.init has no `$HOME` guard)
as an intentional hardening after the magnet incident.

## Alternatives Considered

### Skip $HOME only (no user-level config)
- **Pros**: one-line fix
- **Cons**: no mechanism for shared backend defaults across projects;
  users must copy gate.yaml to every project
- **Why not**: solves the symptom but not the missing feature

### Full override hierarchy (gate.yaml inheritance)
- **Pros**: maximum flexibility
- **Cons**: complex merge semantics, hard to debug; `test:` inheritance
  would be a footgun (wrong test command runs in wrong project)
- **Why not**: YAGNI; D4's minimal merge (backends only) covers the
  real need (shared API keys and model defaults)

## Consequences

### Positive
- Stale `~/.code-forge/` is architecturally defused, not just deleted
- User-level backends are usable end to end: the MCP server advertises
  them and the CLI subprocess resolves them through the shared
  `user_config` loader, so both ends apply the same merge semantics
- `forge_init` cannot regenerate the magnet at `$HOME`

### Negative
- Two config paths to document and maintain
- Legacy compat (D3) adds a deprecation code path to eventually remove

### Risks
- User-level config with only `backends:` and no `test:` must not be
  fed to `load_gate_config()` (gate_check.py), which requires `test:`.
  The user-level loader is a separate function.

### Limits
- The MCP preflight (`_check_backend`) still reads only the project
  gate.yaml: a project whose gate.yaml has zero `backends:` plus
  user-level backends is refused by the MCP tools even though the CLI
  can run that topology, and a missing API key for a user-level backend
  surfaces from the CLI rather than the preflight.  Teaching the
  preflight the merged view is a setup-mcp acceptance item.
- ADR-0006's "umbrella workspace" case (editor at `~/code/`, multiple
  projects) is still unsolved.
- `$HOME` is the only skipped directory.  A stale `.code-forge/` at
  `/home` or `/` would still match, but these are unlikely in practice.
