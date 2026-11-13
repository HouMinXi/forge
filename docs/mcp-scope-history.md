# Explicit MCP historical query scope

`forge_scope_history(ticket)` reads the counts-only snapshot from an existing
caller-held scope ticket. It complements `forge_diagnostic_history`: diagnostic
v1 and query-scope v1 stay separate and unchanged. No latest search, review
resumption, graph freshness, finding history or current-run/clean credit is added.

Disabled by default. The server operator must explicitly configure both existing
canonical absolute mode0700 directories at startup:

- `FORGE_SCOPE_TICKET_DIR`: directory holding tickets exported by
  `review --scope-ticket-dir`.
- `FORGE_SCOPE_STORAGE_ROOT`: the original receipt/source storage root.

The client supplies only the exact exported basename, containing execution UUID,
repository UUID and sequence, ending `.scope-ticket.json`. It cannot choose paths
or storage roots. The basename is a selector, not authentication: its UUID/sequence
text is not an independent expected-context source. Strict validation uses the
caller-held ticket context and server-authorized storage binding. Startup captures directory device/inode identities; replacement
requires intentional restart/reconfiguration. No directories, credentials or
permissions are created or changed by this interface. Scope and diagnostic history
configuration is independent; configuring one does not enable the other.

Output matches CLI scope-history's historical projection: `kind` is
`historical-query-scope`, `current_run_credit` is false, and `snapshot` holds only
scope/count/accounted fields. No private locator, source token, UUID or raw error
text is returned. Counts describe registration occurrences, not unique repositories
or proven impact. Partial/unavailable/not-reached snapshots remain distinguishable.

Unsigned tickets are private caller-owned state, not same-UID authentication.
The operator trusts their source and maintains retention. Root/locator checks and
strict bounded decoding do not prove freshness or provide portable relocation.
Existing local Linux filesystem and ticket support limits apply.

Reads run off the event loop and are drained on cancellation/shutdown before
ownership is released. This is not a hard filesystem deadline; a stalled filesystem
can delay draining. No new worker/sandbox or broader cleanup guarantee is claimed.
