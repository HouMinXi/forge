# ADR-0006: MCP server workspace resolution

**Status**: accepted
**Date**: 2026-06-30
**Supersedes**: none
**Related**: ADR-0004 (account-auth backend), docs/setup-mcp.md

## Context

The forge MCP server (`src/code_forge/mcp_server.py`) locates the workspace
through `Path.cwd()` at three points, two named and one load-bearing that the
original draft missed:

  - L45  lifespan preload:   `Path.cwd() / ".code-forge" / "gate.yaml"`
  - L81  `_check_backend`:   `Path.cwd() / ".code-forge" / "gate.yaml"`
  - L105 `_run_cli_simple`   and L128 `_run_cli_budgeted` call
         `create_subprocess_exec("code-forge", ...)` with NO `cwd=`, so the
         real `code-forge` subprocess inherits the server's cwd and resolves
         gate.yaml / trust / the worktree check from there.

Failure mode: when the client launches the server with cwd != project root
(started in a subdirectory, or above the project, or the editor does not set
cwd to the project), `.code-forge/gate.yaml` is not found and every tool fails.

Real consumer (not hypothetical): one user, three clients -- Claude Code,
VS Code, PyCharm -- and three forge-configured projects under ~/code:
surflare-watchdog, forge, nixnote2-csdn. The need: open or enter any project
in any client and have forge MCP review that project.

### Ground facts verified against the installed SDK and source

  - mcp SDK 1.27.0. `list_roots` is `async def` on `ServerSession`
    (session.py:350), reached via `ctx.session.list_roots()`. The fastmcp
    `Context` does NOT expose `list_roots` directly. Roots require client
    capability negotiation (session.py:127-130): a client that does not
    advertise the `roots` capability cannot be queried. Availability across
    the three target clients is NOT verified.
  - The CLI resolves `.code-forge` strictly relative to its own cwd
    (cli.py L863, L932, L1459, L1650). There is NO ancestor walkup anywhere
    in the CLI. Therefore the subprocess must be handed the EXACT project
    root as cwd; a subdirectory will not resolve.
  - trust is keyed by gate.yaml path + content hash and is an INDEPENDENT
    layer. Workspace resolution does not touch it. A correctly resolved
    workspace still fails review until `code-forge trust` is run in that
    project. (Observed today: surflare-watchdog Trusted=True works;
    forge and nixnote2-csdn Trusted=False fail even at the exact root.)

## Decision

Resolve the workspace IN THE SERVER, then pass the resolved root as `cwd` to
the CLI subprocess. The CLI is unchanged.

Precedence:

    Priority  Layer        Mechanism                                  Phase
    --------  -----------  -----------------------------------------  -----
    1         env          FORGE_PROJECT_DIR (explicit, exact root)   1
    2         cwd-walkup   nearest ancestor with .code-forge/gate     1
    3         roots        ctx.session.list_roots(), single match     2
    4         cwd-as-is    fall through; existing clear error fires   1

### Phase 1 (ship now)

A single sync helper plus five edit sites. No `Context`, no async, the
lifespan preload stays intact (one server process per editor window, so the
workspace is fixed for the process; resolve once).

    def _resolve_workspace() -> Path:
        """Forge workspace root: explicit env, else walk up from cwd."""
        env = os.environ.get("FORGE_PROJECT_DIR")
        if env:
            return Path(env).expanduser().resolve()
        start = Path.cwd().resolve()
        for d in (start, *start.parents):
            if (d / ".code-forge" / "gate.yaml").is_file():
                return d
        return start   # nothing found; _check_backend emits the clear error

    _WORKSPACE = _resolve_workspace()

Apply:
  - L45, L81  -> `_WORKSPACE / ".code-forge" / "gate.yaml"`
  - L105, L128 -> add `cwd=str(_WORKSPACE)` to `create_subprocess_exec`
  - L3-12 module docstring -> remove "expects cwd = workspace root"; describe
    env + walkup resolution
  - L61-68 FastMCP `instructions` string -> update to describe
    FORGE_PROJECT_DIR + auto-walkup; remove "expects cwd = workspace root"
  - L54 lifespan yield -> remove dead `gate_yaml_path` from context dict
    (no tool handler consumes it)

This solves: started in a subdirectory (walkup), started at the project root
(walkup no-op), explicit binding (env), and VS Code `${workspaceFolder}` /
PyCharm working-directory (they already set cwd to the project root, so
walkup is a no-op there). Roots is not needed for any of these.

### Phase 2 (only if a real client provably needs zero-config AND is verified to send roots)

  - Precedence becomes env > roots (per-call, async) > cwd-walkup.
  - Roots resolution picks the single root whose directory contains
    `.code-forge/gate.yaml`. If MULTIPLE roots match -> raise ToolError
    telling the user to set FORGE_PROJECT_DIR. Never guess among roots.
  - `_backend_names` preload (L37/L50) must move to per-call lazy loading,
    because roots cannot be queried at lifespan time (no request context).
  - The six tool handlers gain `ctx: Context`.

## Why env > roots (correction to the original draft)

The original draft placed Roots at priority 1 while also stating that
multi-project binding must NOT rely on roots (separate MCP entries, each with
its own FORGE_PROJECT_DIR). Those two are in conflict: a priority-1 roots
layer resolves BEFORE the env the design declares authoritative.

env-first removes the conflict and is the more defensible boundary:

  - env is the most explicit declaration. A value pushed by the client must
    not override what the user stated.
  - The resolved directory is fed to YAML parsing BEFORE the trust gate. A
    user-declared path is a safer input than a passively received one.
  - Roots availability across the three target clients is unverified;
    making the primary mechanism the least-guaranteed layer is fragile.
  - In the "umbrella folder" case (editor opened at ~/code, many projects
    inside, one server), roots returns the umbrella, not the sub-project --
    roots does not solve the hard case anyway.

The original draft's four downsides of Roots (multi-root ambiguity, state
consistency, client fragmentation, trust boundary) are correct. They are
exactly why roots is demoted to Phase 2 and placed below env, not the core
mechanism.

## Consequences

Positive:
  - All three clients work today via env + walkup. Zero per-project config
    for the common "open or enter a project" workflow.
  - CLI untouched; "CLI zero changes" holds precisely because the server
    now hands the subprocess the exact root via cwd.
  - Smallest diff (~25 lines in one file); no async or Context churn in
    Phase 1; lifespan preload unchanged.
  - MCP instructions string accurately describes the new resolution,
    so LLM clients stop attempting cwd workarounds.

Limits (documented, not solved):
  - `$HOME` exception: walkup now skips `$HOME` itself to prevent stale
    `.code-forge/` from acting as a magnet (ADR-0009).  This means a
    legitimate project rooted at `$HOME` requires FORGE_PROJECT_DIR.
  - The "umbrella workspace" case (one editor window rooted above many
    projects, one shared server) is NOT solved by any of the three layers --
    roots returns the umbrella too. Solving it needs per-reviewed-file
    resolution (resolve from the diff target's path). Out of scope here.
  - trust is independent. A resolved, correct workspace still fails review
    until `code-forge trust` is run in that project. docs/setup-mcp.md must
    state this next to the worktree caveat.
  - `forge_init` bootstrap: walkup searches for `.code-forge/gate.yaml`,
    but init's purpose is to CREATE `.code-forge/`. Before init, the marker
    does not exist, so walkup cannot find it. If init is called from a
    subdirectory without FORGE_PROJECT_DIR, it creates `.code-forge/` in
    that subdirectory rather than the project root. This is not a regression
    (current behavior is the same), but users must run init from the project
    root or set FORGE_PROJECT_DIR.

## Scope challenge

  - Does this need to exist? Yes. Three real clients x three real projects,
    currently broken outside an exact-root cwd.
  - Three real consumers: Claude Code, VS Code, PyCharm sessions of the one
    user, across surflare-watchdog / forge / nixnote2-csdn.
  - Do-nothing cost: MCP review works only when cwd is exactly the project
    root; unusable from subdirectories or multi-project launchers. The
    shipped MCP onboarding (docs/setup-mcp.md) then works only by luck.

## Implementation checklist (Phase 1)

  - Phase 0: create a linked worktree; do not edit the main worktree.
  - Add `_resolve_workspace()` + `_WORKSPACE`; update the five edit sites
    (L45, L81, L105, L128, plus instructions/docstring).
  - Remove dead `gate_yaml_path` from lifespan yield dict.
  - Step 0: py_compile; ruff / pylint; non-ASCII check on the diff.
  - Tests (bug-inject each before trusting it):
      * walkup finds the root from a subdirectory
      * FORGE_PROJECT_DIR overrides walkup
      * no marker anywhere -> falls through, _check_backend clear error
      * subprocess receives cwd=root (inject wrong cwd, watch it fail, fix,
        watch it pass)
      * forge_init without .code-forge/ -> uses cwd-as-is (bootstrap limit)
  - Three-cycle static review + smoke test. Real-path smoke: call
    resolve-outlet through the server started from a subdirectory of a
    trusted project (surflare-watchdog) and confirm it names a backend.
