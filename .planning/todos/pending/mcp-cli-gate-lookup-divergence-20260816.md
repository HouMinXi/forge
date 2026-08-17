# CLI vs MCP gate.yaml lookup divergence (filed 2026-08-16)

Found during the MCP e2e smoke: a worktree without its own
.code-forge/gate.yaml resolves differently per entry point.

- CLI: walks up from cwd and finds the main tree's gate.yaml, so a
  review from a fresh worktree works (and inherits the main tree's
  backends/trust state).
- MCP (FORGE_PROJECT_DIR set to the worktree): looks only at the
  exact root and fails with "gate.yaml not found. Run 'code-forge
  init'".

Consequence: the same review runs or fails depending on which entry
point launches it, and when it runs via CLI it silently uses the main
tree's configuration. Fix direction: make both entry points share one
resolution rule (walk-up with HOME skip, FORGE_PROJECT_DIR as the
floor rather than the exact point), or make MCP fall back to walk-up
when the exact root has no gate.yaml.
