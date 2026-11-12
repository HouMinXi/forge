# Explicit v1 diagnostic history (opt-in CLI)

This optional CLI capability preserves one existing diagnostic v1 observation
per ticket. It does not expand the v1 schema, Verdict, scope receipts or MCP.
It is not a complete review log, findings archive, fresh analysis, recovery
permission, clean credit or dismissal authority.

## Caller contract

```sh
forge review --diagnostic-ticket-dir /absolute/private/tickets
forge diagnostic-history --ticket /absolute/private/tickets/selected.diagnostic-ticket.json
```

The caller supplies an existing canonical, caller-owned mode 0700 directory.
Forge creates mode 0600 tickets there, without creating directories, changing
permissions or overwriting files. Only subprocess cross-repository review is
supported. Other outlets and single-repository review explicitly refuse the
option; empty-diff review explicitly reports no receipt. Defaults are unchanged.

Each observed v1 record is independently strictly read before export. The ticket
records the live host's independently generated expected context and actual
published locator. Unknown or failed publications do not get synthetic locators.
Each observation's export success/failure is reported separately; a success is
not a guarantee that every review stage or observation was saved. Cache replay
never retries export. Ordinary export errors do not change native review output
or Verdict. Cancellation retains its original identity and propagates.

Tickets contain **private absolute storage paths, execution/repository identities,
source and diff identifiers, filesystem identities and digests**. Keep them off
public logs, issue comments and shared artifacts. Filenames are independent
random UUIDs; review output lists only those basenames, not stored identities or
the supplied directory. The caller owns retention and deletion of tickets and
the underlying diagnostic records. A ticket is not a copy of those records.
Export failure/cancellation may leave a committed ticket: do not blindly retry
or infer that no file exists. Low-level repeated explicit exports may create
additional tickets; the live host limits attempts to once per observation.

An explicitly selected ticket is a caller-trusted, **unsigned credential**, not
origin authentication. Mode, hash and inode checks do not protect against an
actor with the same UID who can replace both the ticket and its evidence. No
latest scan, record-derived expected identity, relocation recovery, root rebinding
or content-only fallback is performed. The original storage must remain intact.

## Historical output

Success produces JSON containing `kind: historical-advisory-diagnostic`,
`current_run_credit: false`, `analysis_state`, and bounded `diagnostics`.
All five old v1 analysis states remain distinct. The historical record does not
include the in-memory findings/errors of a whole review. The output strips ticket
paths, locator details, execution/repository IDs and source/diff identifiers.
A refusal produces only a bounded error, never raw storage exceptions or paths.

MCP remains unchanged. A future MCP reader needs an explicitly authorized private
root and selected ticket; job IDs, captured output, or arbitrary client paths
are not durable history authority. No MCP support is claimed by this prototype.

## Verification limits

Focused local tests include actual fresh-process CLI reads, five v1 states,
root/file identity and context mismatch, corrupt/oversized tickets, permission,
symlink/FIFO refusal with subprocess timeout, cancellation/cleanup priority,
closed/foreign-owner export, per-observation partial failures, and cross-type
scope/diagnostic ticket refusal. Default and existing scope/v1 regressions are
retained. Composed on main `78cf88e6092c6d48ffabd5a69782728eaec773ee` after #482, the
focused suite ran on Python 3.14.10 (1197 passed, two optional provider-module
collection skips; one pytest-asyncio configuration deprecation warning). The real code_review_graph provider is absent;
its integration coverage, the full product suite and CI are not claimed.

This interface does not close #33, #441, or their remaining acceptance gates.
It does not replace the original G12 test port or authorize scope expansion.
