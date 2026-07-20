# Charter: code-forge doctor (CANDIDATE -- not yet scheduled)

One-command MCP + backend health self-check. Born from the 2026-07-04
PyCharm send_ping debug: a 5-layer silent chain where a break at step 3
looked identical to a break at step 5, and the only ground truth was an
out-of-band probe log. Full pain-point catalog: memory
project_forge_38x_mcp_hardening_2026-07-04 (Debug session pain points).

## Scope-challenge (answer BEFORE any design)

(a) Does this need to exist?
    Yes. The silent-chain failure is real and recurring -- the `Forge
    failed` reports (memory reference_forge_adoption_state) are almost
    certainly the same class: a server registered in a registry the
    acting agent does not read, with zero error surfaced. A tool that
    turns "something is broken somewhere" into "this specific link is
    broken" is high-leverage. But scope stays narrow -- see boundary.

(b) 3 real consumers:
    1. New forge users onboarding via setup-mcp who hit the same silent
       chain this session hit (per-agent registry, cold gpg, stale
       FORGE_PROJECT_DIR, flatpak cwd) with no self-diagnosis.
    2. `Forge failed` reporters -- today they cannot tell forge's half
       from their IDE's half; support burden lands on the author.
    3. setup-mcp itself as a post-write verification step: setup-mcp
       writes config + trust + prints registration; doctor confirms that
       config actually resolves (closes the write-then-unverified gap).

(c) Cost of do-nothing + document:
    Every new user re-walks the 5-layer chain by hand; the author
    re-diagnoses each `Forge failed` from scratch; trust erodes because
    the newest, least-familiar component (forge) gets blamed for IDE-side
    breaks it cannot even see. A doctor that goes green on forge's half
    shifts the blame to where it belongs, provably.

## Capability boundary (HONEST -- 3-layer model, ground-truthed 2026-07-04)

The chain client->agent->server breaks at three layers; doctor reaches
the first two, not the third. (Corrects the earlier "server half only /
physically unavailable" claim, which was too absolute: L1 file registries
ARE readable.)

  L1 config files (READABLE): file-based registries doctor can open and
  diff for forge's presence -- AI Assistant ~/.ai/mcp/mcp.json, VS Code
  mcp.json, Claude Code ~/.claude.json. If forge is absent here, doctor
  says so.
  L2 server self-handshake (DOABLE): doctor spawns forge's MCP server and
  completes one initialize handshake -- proves binary + config + backend
  resolve, independent of any client.
  L3 live agent session / ACP passthrough (UNREACHABLE): a standalone CLI
  cannot see a running agent's in-memory tool set. Ground-truthed this
  session -- forge is registered in FIVE file registries on this machine
  (~/.ai/mcp/mcp.json, ~/.config/JetBrains/PyCharm2026.1/mcp.json,
  .../options/llm.mcpServers.xml, .../workspace/*.xml, ~/.claude.json),
  yet Copilot ACP `/mcp list` is empty even on a fresh session (timing
  hypothesis falsified), and the documented ACP passthrough file
  ~/.jetbrains/acp.json is ABSENT on PyCharm 2026.1. So the ACP passthrough
  is version-unstable and empirically decoupled from every file
  registration -- NOT reliably file-diagnosable. doctor MUST NOT claim to
  diagnose ACP agents; it names JetBrains ACP as the known-unstable culprit
  and hands the `/mcp list` pointer.

So doctor covers L1 + L2 (forge's half + file registries), then for L3
prints the proven pointer: "forge server + backend + trust are healthy;
if a tool is still missing, run `/mcp list` in your agent -- an empty
list is your IDE's per-agent passthrough, not forge." The Copilot finding
IS the proof this pointer is the right design rather than a live-ACP
probe: the break is real, forge-cannot-fix, and one command surfaces it.

## L1 registry map + proactive report (ground-truthed 2026-07-04)

The user requirement: doctor must PROACTIVELY say where the break is, not
just green-light forge's half. The mechanism is L1 enumeration of the
public per-client registry map (EXA-sourced; prior art fenil210/mcp-doctor
client-matrix.md, mcporter, Loadout). Paths are GLOB-based because the
JetBrains version sits in the path -- hardcoding breaks on upgrade:

  Claude Code    ~/.claude.json, .mcp.json, .claude/settings*.json   key mcpServers
  Claude Desktop ~/.config/Claude/claude_desktop_config.json          key mcpServers
  Cursor         ~/.cursor/mcp.json, .cursor/mcp.json                 key mcpServers
  VS Code        ~/.config/Code*/User/mcp.json, .vscode/mcp.json      key servers
  Windsurf       ~/.codeium/windsurf/mcp_config.json                  key mcpServers
  JetBrains AIA  ~/.ai/mcp/mcp.json, ~/.config/JetBrains/*/mcp.json,
                 ~/.config/JetBrains/*/options/llm.mcpServers.xml     (glob)
  Junie          ~/.junie/mcp/mcp.json, .junie/mcp/mcp.json           key mcpServers

Proactive report: per registry, PRESENT/ABSENT for the forge server, then
name the mismatch -- "forge is registered in [AIA, Claude Code,
PyCharm-instance]; ABSENT from [Cursor, VS Code]. If your agent reads an
ABSENT registry, that is the break." This turns the silent chain into a
pinpoint. The registry map is data, not logic -- one dict, add rows as new
clients appear; do NOT reimplement fenil210's matrix, cite the same public
sources.

Honest edge (the ACP case that started this): file-conventional clients
above are fully diagnosable. The JetBrains ACP passthrough is NOT -- five
present registrations still left Copilot ACP empty and its passthrough file
is absent (see L3). doctor reports the five as PRESENT, then explicitly:
"all registries show forge, but your ACP agent still can't see it -> the
break is JetBrains ACP passthrough (version-unstable), run `/mcp list`."
That is the maximally-proactive honest answer: pinpoint everything readable,
name the one unstable layer instead of guessing it.

## Alternatives (weigh all, including do-less / do-nothing)

1. Do nothing + document: a troubleshooting doc (per-agent registry
   table + `/mcp list`). Cheap, but the silent chain persists -- docs
   must be actively found; the failure gives no pointer to them.
2. Do less -- expose resolve_outlet as CLI: surface the existing
   resolve_outlet + trust_status as `code-forge resolve-outlet`. Covers
   the backend half, not the MCP transport half. Small, real, half the
   value.
3. RECOMMENDED -- doctor as aggregator + server-side MCP self-check: a
   new CLI command that REUSES resolve_outlet + trust_status + setup_mcp
   config checks (ladder: reuse what is here, do not rewrite), PLUS one
   new check -- can the MCP server spawn and complete an initialize
   handshake against itself. Ends with the honest client-side pointer.
   Minimal net-new logic.
4. Full -- doctor + live-ACP probe: rejected. L3 live-agent state is
   unreachable and ACP passthrough is unconfirmed as file vs protocol
   (see boundary). L1 file registries ARE in scope; a live probe is not.

## Minimal viable release (if scheduled)

`code-forge doctor` prints a checklist, each line PASS/FAIL/pointer:
- backend config resolves (reuse resolve_outlet)
- trust granted for the active gate.yaml (reuse trust_status)
- FORGE_PROJECT_DIR / gate.yaml walk-up resolves to a real path
  (this is 38.3 T1 territory -- see dependency)
- MCP server spawns + self-initialize handshake succeeds (L2, new, small)
- L1 present/absent report across the registry map (see L1 section):
  name which registries have forge and which lack it -- the proactive
  pinpoint; never claim to cover ACP agents
- final line: registries + server half green -> "if a tool is still
  missing, run your agent's `/mcp list`; if empty despite the registries
  above, the break is your IDE's ACP passthrough, not forge."
No client probing. No new config. Aggregation + one handshake check.

## Dependencies / sequencing

MUST follow 38.3 (MCP credential UX). 38.3 retires the gpg wrapper and
reworks the credential paths (sampling / vertex / api_key_path) plus the
FORGE_PROJECT_DIR validate+walkup (T1). doctor aggregates exactly those
surfaces -- building doctor first means rebuilding it the moment 38.3
lands. Order: 38.3 -> doctor.

## Not doing (explicit)

- No L3 live-agent / ACP-passthrough inspection (unreachable); L1 file
  registries ARE inspected, ACP agents get the `/mcp list` pointer only.
- No new diagnostic engine -- doctor orchestrates existing checks.
- No auto-fix -- doctor reports; the user acts. (Auto-fix is a separate
  scope-challenge if ever demanded.)
