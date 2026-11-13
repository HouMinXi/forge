# Explicit MCP historical diagnostics

`forge_diagnostic_history(ticket)` reads a single saved diagnostic v1 observation.
It is independent of the server's job registry: restart and job expiry do not
turn historical observations into current results. No findings archive, review
recovery, freshness, dismissal, permission, or clean credit is provided.
`current_run_credit` is always false. Existing v1, scope-history, and Verdict
contracts are unchanged. This does not complete issues #33 or #441.

## Startup authorization

Disabled by default. The server operator must explicitly configure both:

- `FORGE_DIAGNOSTIC_TICKET_DIR`: the existing private directory containing
  caller-retained tickets exported by `review --diagnostic-ticket-dir`.
- `FORGE_DIAGNOSTIC_STORAGE_ROOT`: the existing source storage root referenced
  by those tickets, not its nested `advisory-diagnostics` directory.

Both must be canonical absolute directories, owned by the server user, mode
0700, on the strict reader's supported local Linux filesystem. Startup rejects
partial or invalid configuration. No directory creation, chmod, scanning,
credential generation or actual service configuration is performed by this tool.
Startup captures each directory's device/inode identity. Replacing either root
requires intentional reconfiguration and restart, not automatic rebinding.
The configuration is fixed for the server lifetime, not re-read from a client.

The client supplies only an exact random UUID ticket basename ending in
`.diagnostic-ticket.json`. Arbitrary paths, aliases and latest lookups are not
accepted. Tickets are bounded mode0600 regular files. Before accessing an
observation, its locator must match the configured storage root and identity;
the existing strict v1 reader checks its independently saved expected context.
Only analysis state and bounded diagnostics are returned, never roots, source
hashes, execution identifiers or raw exception details.

Tickets are unsigned, unencrypted caller-owned private state. Directory checks
are not same-UID authentication, and saved context is not a proof of current
source freshness. Retention remains the operator's responsibility. Do not put
secrets into ticket names. The authorized roots must not be attacker-managed.

Reads run off the event loop. Cancellation and shutdown drain an owned read
before relinquishing it, and cancelled requests cannot report a successful
historical delivery. This is not a hard filesystem deadline: a stalled local
filesystem may delay draining. No remote/NFS or universal timeout support is
claimed.
