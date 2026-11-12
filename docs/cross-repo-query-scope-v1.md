# Cross-repository query scope: current-execution receipts

This private auxiliary channel records counts from the native cross-repository
runner and strictly reads its own persisted record before formatting the existing
immediate summary. It does not add a public API, change Verdict, CLI arguments,
MCP protocol, findings, State, gate/credit or receipt decisions. Existing diagnostic
v1 remains a separate closed format. This is a narrow step under #33, not completion
of #33, #441, G04/G09 acceptance, G12 or the historical 78-test contract.

## Observation and consumer

`CrossRepoImpactRunner._run_analysis_impl` observes native registry/helper boundaries;
`_run_analysis` retains an immutable snapshot. The original summary points call
`_emit_scope_summary` → `DiagnosticCapture.present_scope` → the independent publisher
and strict reader. Expected context is issued by the owning capture before writing,
not reconstructed from file contents. The consumer compares the decoded snapshot
with the independently frozen current facts before using it. Legacy summary text,
ordering and error priority remain unchanged. A transport/read/validation failure
uses this invocation's real in-memory summary, never an empty substitute.

Registered, attempted, completed, unavailable and with-callers count **registration
occurrences**, not distinct repositories, symbols, findings or SQL statements.
Duplicate rows therefore count repeatedly. Completed means a sibling helper returned
fully, including successful zero-caller queries. A later path/sort/output failure
does not erase that query fact, but does not count as successful analysis.
`query_occurrences_accounted` means completed + unavailable equals the established
registration total; it is not a completion, freshness or no-impact certificate.

Before enumeration scope is `not_reached`; failed enumeration is `unavailable`, with
unknown counts represented by null. Established empty scope has zero counts. Missing
registry currently means empty under existing discovery semantics, not proof that a
registry file existed. No-target exits do not perform extra discovery just to fill
scope. Failure/cancellation before an original summary point can produce no scope
receipt at all: absence is unknown, not zero or completed.

## Independent format and budgets

Namespace: `.code-forge/query-scope-receipts/<execution>/<repository>/CROSS-REPO-IMPACT/`.
Envelope: `forge-cross-repo-query-scope-v1`. The closed context contains execution and
repository UUIDs, current source token, exact UTF-8 diff SHA-256 and an observation
sequence. The closed snapshot contains `scope`, the five counters above and
`query_occurrences_accounted`. The implementation is the authoritative exact-key,
exact-type codec; bools are not integers, extra/missing fields and wrong contexts
are rejected. Changes to this shape require a new explicit version decision.

Internal auxiliary-storage limits are 4096 raw bytes per file, 256 registration
occurrences per snapshot and 64 reserved scope attempts per owning capture. These
are **not analysis or supported-repository limits**: exceeding them disables scope
storage and preserves the original factual summary. Scope sequences have their own
budget and namespace; they never consume diagnostic-v1 attempts. Scope and v1 are
not an atomic transaction and matching sequence numbers have no linking meaning.

The writer/reader reuse qualified descriptor-relative storage, nonblocking ordinary
file checks, no-overwrite publication, retained identity/digest verification and
bounded strict parsing. Namespace validation runs before retaining callback results
and again before reading. Malformed failure results and locators are not retained;
validated published paths are rebuilt from the owner-held normalized binding.
Publication states remain published / not_published / unknown, with cleanup separate.
Unknown has no successful locator, no automatic retry and no historical fallback.

## Lifetime, privacy and support

Only the current owning process/thread and active execution may consume these
receipts. Diff/root/source/execution checks occur before consumption. Warm-cache
reuse does not rerun queries, republish, emit another scope summary or gain new query
credit. New executions get new identity; this is not HOLD/resume or cross-run replay.
Delivery is separate: read, unconfirmed while a sink is called, and delivered only
after that call succeeds. No sink does not mean delivered. Cancellation propagates
unchanged and clears scope binding rather than being converted to ordinary failure.

Counts-only describes the snapshot, not the whole file: the context includes UUIDs,
source token and diff digest. No registry paths, caller names, raw error messages,
credentials or environment values are added. Hashes/UUIDs are correlators, not
proof of authenticity or permission. Existing storage-root exclusions and qualified
filesystem support apply. Unsupported storage degrades only this auxiliary channel.

Files can remain on disk after execution. There is **no new automatic cleanup,
cross-run total quota, user-facing historical retrieval or durable recovery API**.
No power-loss durability, atomic environment/graph snapshot or graph freshness is
promised. Existing cleanup limits and uncertainty remain visible.

## Reproduction and evidence limits

Use Python 3.14 and the repository's pinned development dependencies:

```bash
PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest \
  -p pytest_asyncio.plugin -p no:cacheprovider -q \
  tests/test_cross_repo_scope_snapshot.py tests/test_scope_receipt_host.py \
  tests/test_query_scope_receipt.py tests/test_advisory_diagnostics.py
```

These include real SQLite normal, missing, corrupt, duplicate and cached queries;
helper-return-then-failure; format/identity/replay/replacement controls; bounded
publication retention, cancellation and unchanged-v1 regressions. Mocked boundary
faults are labelled controls, not natural production failures. Public node identities
are registered in `scripts/ci_public_test_identities.json`.

Broader regression includes `tests/test_cross_repo*.py`; optional-provider skips
remain missing coverage, never passes. Tests are not a replacement for original
78-port recovery or a versioned equivalence decision. Historical negative-control
runs against earlier prototypes do not qualify a different final revision.

### Candidate qualification (2026-11-12)

Composed on main `1401b75ff299a6d84a83d149ad46434f2ca360ea`, preserving
all upstream test identities. Three new modules add 129 collected identities,
with no removed identities. Python 3.14.10 / pytest 9.2.0 / Ruff 0.17.3.
The command above independently passed 450 tests. Broader local selection
`tests/test_cross_repo*.py tests/test_advisory_diagnostics.py
 tests/test_query_scope_receipt.py tests/test_scope_receipt_host.py
 tests/test_ci_private_transport.py tests/test_ci_test_shards.py`
passed 1257, skipped two optional `code_review_graph` provider modules, and produced
one existing `record_property`/JUnit xunit2 warning. This is not the full suite,
installed-wheel qualification, remote CI, or proof for the skipped provider.

Three isolated omission controls were rerun against this candidate, each with
baseline GREEN, the listed semantic failures (zero setup errors), and byte-exact
restoration GREEN. These are test-discrimination experiments, not natural defects:

| Removed boundary in disposable copy | Target test | Baseline / RED failures / restored |
|---|---|---|
| `actual = scope.read(publication, expected)` replaced by `actual = wanted` in `present_scope` | `test_scope_receipt_host.py::test_actual_scope_publisher_file_strict_reader_and_original_text` | 7 / 7 / 7 |
| `publication.locator.binding != binding` comparison replaced by false in `present_scope` | `test_scope_receipt_host.py::test_current_context_cannot_adopt_foreign_storage_root` | 1 / 1 / 1 |
| decoder `context != expected` rejection removed | `test_query_scope_receipt.py::test_context_replay_refused_by_decoder_and_real_reader` | 5 / 5 / 5 |

Reproduce only in an owned disposable copy: first run each exact target with the
same pytest invocation above, change exactly the listed boundary once, require
assertion failures in the selected cases (not collection/import/setup errors),
restore the source bytes and rerun the same target. Never mutate the working
production checkout or interpret deliberate failures as newly found product bugs.
