# Explicit caller-held scope history tickets

This opt-in interface provides **historical inspection**, not review resumption,
current-source validation, authentication or clean/gate credit. Existing Verdict,
MCP result/tool schemas and diagnostic v1 are unchanged. #33/#441 and G12 remain
open work; this is not equivalent to the historical 78-test contract.

## Explicit export and inspection

Supply `code-forge review --scope-ticket-dir /absolute/private/directory` for
subprocess cross-repository review. The directory must already exist, be canonical
(no symlink traversal), owned by the caller and mode 0700. Forge does not create it
or change its permissions. Unsupported single-repository or other-outlet requests
are refused before review dispatch; an empty diff explicitly reports no receipt.

Only the primary cross-repository scope consumer exports. After publishing and
strictly reading a scope receipt against the current owner's independently held
context, it writes a uniquely named ticket with mode 0600. The opt-in output reports
its basename; use that exact file within your selected directory. Warm cache does
not re-export. No reached scope summary means no ticket, not proof of zero queries.

Inspect an explicitly selected trusted ticket with:

```bash
code-forge scope-history --ticket /absolute/private/directory/EXACT.scope-ticket.json
```

The output is JSON labelled `historical-query-scope`, with `current_run_credit:
false` and the counts-only snapshot. It contains no ticket path, UUID, source token
or diff digest. Exit 0 means this historical lookup succeeded, **not** that a review
passed. Invalid, inaccessible or untrusted-permissions input returns exit 2 with a
bounded message. Cancellation propagates, rather than becoming a successful empty
result. No `latest`, directory scan or automatic fallback is added. The separately
configured [MCP scope-history reader](mcp-scope-history.md) exposes the same
historical projection without changing this ticket format.

## Trust, disclosure and retention

A ticket is **unsigned and unencrypted**. You explicitly decide its source is
trusted and protect it independently; permission and consistency checks are not
identity authentication. Same-UID processes can replace a complete valid ticket
and matching evidence. This interface does not resist that threat. Do not pass an
untrusted repository's proposed ticket merely because it parses or has mode 0600.
No hidden secret, signing key or account credential is introduced.

The ticket contains private absolute source-root paths, device/inode identities,
execution/repository UUIDs, source token, diff digest, scope sequence and retained
artifact path components/size/digest. It excludes raw diffs, findings, registry
contents, caller names and errors. Avoid sharing it as though it were only an opaque
identifier. Both the selected ticket directory and original receipt/source storage
must be retained. The caller controls backup, protection and deletion; Forge adds
no expiry, automatic cleanup, cross-run quota or retention service.

A new versioned closed format, `forge-scope-history-ticket-v1`, has a 32768-byte raw
limit, strict depth/type/key/integer limits and reuses the exact original scope
validator and filesystem reader. The larger ticket limit accommodates retained
root/locator metadata; scope payload limits remain unchanged. These serialized
fields describe the original physical binding, not a portable relocation format.

## Failure, cancellation and identity

Export uses exclusive no-overwrite publication and verifies the saved bytes.
A failed/cancelled call can leave a committed final ticket; there is no automatic
retry or guarantee that no file exists. Owned temporary cleanup is attempted and
cannot replace the original process-control exception. Artifact publication,
ticket save and output delivery are separate events. No power-loss durability or
atomic multi-file transaction is promised. Ordinary ticket errors leave the native
analysis result and genuine immediate summary intact.

The historical reader retains original expected context and checks the original
root, directory and file identities, size and digest. Replacement, corruption,
missing artifacts and mismatched context are refused. New execution is not old-run
recovery. Moving/copying roots, replacing inodes, restored backups and rebinding are
unsupported; there is no content-only fallback, rollback protection or statement
that the original source/graph is current. Deleting the ticket removes that caller's
lookup reference, not the historical event. No review authority is restored.

## Reproduction

Python 3.14 with the pinned development dependencies:

```bash
PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest \
  -p pytest_asyncio.plugin -p no:cacheprovider -q \
  tests/test_scope_history.py tests/test_scope_history_host.py
```

Tests include a new interpreter reading after the owning capture closes; real
SQLite producer/host path; wrong context/root/inode and damaged bytes; permission,
symlink and FIFO refusal; cancellation before/after commit and cleanup priority;
unchanged default output, warm cache and explicit unsupported-outlet handling.
Controlled substitutions are test oracles, not evidence of natural defects.
