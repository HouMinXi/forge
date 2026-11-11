# Cross-repository diagnostic v1: bounded implementation contract

This slice implements stage-effective diagnostic capture for the existing primary cross-repository runner. It does not implement a global configuration snapshot, graph freshness, deeper impact analysis, or the other capabilities tracked in [#33](https://github.com/HouMinXi/forge/issues/33). Implementation and test evidence do not by themselves close [#441](https://github.com/HouMinXi/forge/issues/441), grant G0/G1, or supersede the two review-support drafts.

The stage-effective contract was selected for implementation after independent design review. Earlier alternative proposals are retained in Git history. This document replaces their conflicting eager-capture algorithms and numerical proposals for this slice. Source baseline: e5ecb784. Sources: [v4](https://github.com/HouMinXi/forge/issues/33#issuecomment-6742713536), [independent review](https://github.com/HouMinXi/forge/issues/33#issuecomment-6742850246), [work packages](https://github.com/HouMinXi/forge/issues/441#issuecomment-6757925547).

The [limited contract registration](https://github.com/HouMinXi/forge/issues/441#issuecomment-6763271541) accepts G03's observation/delivery separation, G05's no-new-HOLD boundary, G06's stage-effective admission and G07's supported history/return isolation. These four dispositions do not grant full T-item acceptance, schema/storage freeze or formal G0/G1. The private result-consumer decision under G04/G09 remains open.

## Real production path and unchanged behavior

CLI `_cross_repo_verdict_or_none` → `run_cross_repo` → primary worker → `StateMachine.run` → native `CrossRepoImpactRunner.run` → host recorder/publisher → strict reader → existing attributed `output_fn`. Immediate and background MCP retain that same CLI subprocess and its stdout. No public session API, new CLI callback parameter, or structured public return was added. `run_cross_repo` still returns Verdict; the runner still accepts only `(diff_text, repo_root)`.

Only primary cross-repository execution is supported. Single-repository HOLD does not construct this axis, and no cross-repository recovery/HOLD interface was added. PENDING still becomes FAIL. The other six advisory axes, source-root routing, source locks, worker joins/completion and deferred signals, receipts, State schema, diagnose, gate and credit rules remain unchanged. #68 and #400 are not reused or modified.

The existing no-named-target warning is retained. A successful strict read supplies its text. Ordinary recorder/publisher/reader/consumer failures fall back to that genuine warning and attempt one separate fixed persistence warning per execution. Other states never acquire a fabricated no-target warning. Empty successful v1 diagnostics remain silent. A separate [immediate named-target query summary](cross-repo-query-summary.md) can report native registered-scope query counts without entering this protocol. Failure of every output channel means delivery is unconfirmed, not successful. Persistent artifacts are not findings or State.infra_errors.

## Identity and admission

Every new host call issues a new UUID4; the primary capture receives a distinct repository UUID4. Moving the same MCP subprocess into the background does not issue another identity. Each record also binds axis, sequence, original source_hash and SHA256 of the exact UTF-8 diff. The existing source hash is not replaced by the exact-diff digest.

The primary worker constructs and closes a private capture actually used by its runner. Before cache return or error clearing, admission verifies the originally installed capture, its unchanged identity fields and stage store, active process/thread, lexical and canonical root, and exact diff. A replaced/closed/wrong-context capture refuses analysis/cache reuse, retains an internal refusal, and attempts the bounded persistence warning without adding a diagnostic-only State error. It does not fabricate a successful empty observation.

Repository IDs correlate host-held roots; they are not authentication. Raw paths, environment values and infrastructure-error text are not serialized. Reader expected context comes from the owning host, never the payload or a latest-file scan. A foreign record cannot provide its own expected context.

## Stage-effective discovery, not an atomic snapshot

The actual provider helper accepts no captured-environment argument. It reads live CRG_HOME and otherwise uses an import-bound fallback. This implementation therefore retains actual results at their original first-use points:

1. Provider import outcome and real default-registry-path return.
2. Default registry's immutable parsed configuration snapshot and primary database selection.
3. Only after named targets resolve: enumeration selector and, when necessary, enumeration registry snapshot.
4. Primary and each enumeration occurrence's resolved root at the native filtering point, then each remaining occurrence's selected database path.

Preserve registration precedence, entry order and duplicates. Compare the original Path spellings when deciding whether enumeration reuses the default snapshot. Two lexical names for one physical registry may legitimately produce two distinct stage snapshots. Stage keys use original enumeration occurrence indices, not filtered positions or deduplicated repository names.

No provider call, HOME/account lookup, path normalization, registry read or sibling access moves before its native branch. Blank diff does not import the provider. Invalid unused CRG_DATA_DIR remains irrelevant when registration wins. Primary missing/corrupt/identity failures and zero named targets precede sibling enumeration. A capture budget failure returns the already obtained native value; it never repeats the operation to fall back.

Retained outcomes are immutable and consumed on later uncached calls. Ordinary stage failures are retained as bounded failure kind/message data, never exception objects, tracebacks, contexts, causes or frames. The original first exception reaches its existing native catch; a later replay reconstructs only the compatible failure for that same catch. A new host retries discovery. This deliberately differs from legacy repeated ambient discovery. Unbound runner callers retain their legacy behavior.

Registry fields actually used by native analysis (path, alias, data_dir) are copied field-by-field; unknown unused fields are not retained. Consumers receive fresh dictionaries. Earlier observations retain their own immutable stage history when later stages are filled. Graph is_file/SQL/metadata/source reads remain live: no filesystem-content, graph-freshness, ABA or same-instant acquisition guarantee is made.

### Supported input boundary

Actually consumed graph roots and selected database paths must be absolute for persistence. Relative data_dir inputs are acceptable when the native selector already returns an absolute resolved result. Relative registry filenames are acceptable because their parsed result is retained and their pathname is not replayed. A consumed relative repository root is unsupported even if native filtering discards it as the primary, or later database selection fails. An unconsumed relative enumeration setting does not block no_named_targets.

Unsupported capture leaves existing analysis and immediate output intact but grants no successful persistent delivery. It does not relabel targets_resolved as unavailable. Target resolution is latched when proved; publication is finalized only after the actually reached stages establish capture support. An exception may leave only a partial set of stages; empty diagnostics never imply every sibling succeeded.

## Observation, delivery and bounded retention

Five analysis states:

| State | Actual evidence | Diagnostics |
| --- | --- | --- |
| not_requested | Blank-diff branch | Empty |
| not_run | Host call returned normally without an admitted axis call | Empty |
| no_named_targets | Nonblank target resolution yielded zero named targets after existing prerequisites | Exactly the fixed diagnostic |
| targets_resolved | At least one named target, even if later work fails | Empty |
| unavailable | Existing target-stage discovery/graph failure | Empty |

An exception before an observable state leaves the explicit host delivery status not_attempted and no observation. Admission refusal, missing file, unsupported capture or publication failure is not invented as not_run or successful empty diagnostics. Publication, strict reading and caller delivery remain separate facts (not_delivered/read/delivered/unconfirmed), including output failure after a committed record. The fixed diagnostic is code `no-named-changed-symbols`, message `cross-repo-impact: no named changed symbols resolved; direct-caller impact not evaluated`.

Actual early returns remain uncached and receive new observation sequences. Final query cache hits reuse the original immutable observation, publication result, finding/error snapshot and identity; they do no new graph SQL or publication. Delivery uses separate actual/cache metadata without rewriting original observation state. Bound outward finding lists are fresh; unbound cache list identity is unchanged. A failed snapshot is explicitly unavailable, not an empty historical error tuple. An earlier uncached error snapshot must never overwrite a later cached result.

Budgets, reserved before retention/publication:

- At most 64 actual observation/publication attempts per execution, including failed or uncertain attempts. Normal native host dispatch is once; bounded headroom permits verified repeated-call behavior without unbounded histories.
- At most 4096 raw bytes per artifact: 256 KiB maximum logical final payload across 64 attempts, not a bound on disk blocks, directories or retained staging names. The maximum compact legal payload under the string limits is approximately 2 KiB.
- Closed JSON container depth 3, including arrays; decoded strings at most 256 strict UTF-8 bytes.
- At most 256 registry entries and 512 retained stage slots; aggregate captured configuration string/failure bytes at most 64 KiB. Each retained path/alias/failure string is at most 4096 bytes.
- Aggregate retained finding/error history text at most 64 KiB, at most 10 findings and 258 errors per observation. One active cache error view may retain another 64 KiB. Immutable object/reference overhead is bounded by the record/stage/entry counts, not included in these text-byte ceilings.
- At most 256 excluded temporary roots. Storage source paths have at most 64 components, at most 4095 UTF-8 bytes overall and 255 bytes per component. This bounds descriptor traversal and temporary-root checking; there is no universal filesystem wall-clock bound.

Overflow disables new capture/delivery without suppressing legacy analysis. It does not retry a native stage, wrap a sequence, retain unbounded unpublished histories, or fabricate a record. The fixed persistence warning is attempted once. Previous immutable observations remain valid. A storage/publication failure latches unavailable storage for the remaining execution; cache never republishes. No automatic global retention/deletion policy was added across separate executions.

## Closed v1 wire contract

Schema version 1 means stage-effective capture for every record; no other consistency mode is admitted. There are exactly nine required fields:

| Field | Exact type/constraint |
| --- | --- |
| schema_version | Exact JSON integer 1 |
| execution_id | Canonical lowercase UUID4, host-matched |
| repository_binding | Exactly `{kind: "invocation-repository", id: <canonical UUID4>}` |
| axis | Exactly `CROSS-REPO-IMPACT` |
| observation_sequence | Exact integer 1–64 |
| source_hash | Nonempty string ≤256 UTF-8 bytes; no invented fixed digest length |
| diff_sha256 | Exactly 64 lowercase hexadecimal characters |
| analysis_state | One of the five states above |
| diagnostics | Exactly the prescribed empty list or single code/message object |

Null, missing, empty, bool, float and string forms are not interchangeable. Unknown/extra fields are rejected at every object level. Decoder reads at most limit+1, uses strict UTF-8, scans quote/escape-aware nesting before JSON parsing, rejects duplicate keys, nonfinite numbers, oversized integer strings, trailing data, escaped lone surrogates, unknown versions/enums and invalid state combinations. Host expected context and publication/locator return shapes are independently validated. Failure is never downgraded to empty success.

## Storage, support and publication facts

Durable root: canonical primary repository `.code-forge/advisory-diagnostics`; namespace execution/repository/axis/sequence.json. The complete root is checked against every temporary per_cwd and its resolved aliases. Writes do not depend on normal receipt collection, so files survive temporary-worker deletion and subsequent output cancellation.

The implementation is Linux-only and admits local overlay, tmpfs and ext4 candidates using the held descriptor's mount identity. fdinfo reads are capped at 4096 bytes; mountinfo at 1 MiB. Every accepted storage descriptor is checked, including descendants, to reject nested unsupported mounts. Actual cloud overlay and tmpfs configurations have been exercised; ext4 is eligible but not locally runtime-qualified. An allowlist is not a universal filesystem qualification. macOS, Windows, network/FUSE filesystems, unreadable/unsupported metadata, deep/long paths and unsafe ownership fail persistent delivery without changing the review.

Directory traversal is descriptor-relative and no-follow. The source root and diagnostic descendants must belong to the effective UID and have no group/world write bits. Existing permissions are never changed. New directories/files request 0700/0600. Fixed fd-based access/default POSIX ACL queries reject detected ACLs; ENODATA means absent, while ENOTSUP/EOPNOTSUPP on an admitted local filesystem means that ACL functionality is unsupported, not that an ACL was successfully read. Other errors fail closed. Ancestors above the source root need not be private but are traversed no-follow.

The integrity promise excludes malicious or uncooperative same-effective-UID namespace tampering. Those processes can already modify owned payloads. Temporary cleanup checks the owned inode before relative unlink, but POSIX conditional inode-matching unlink is not atomic. No authentication, hostile same-UID protection, exactly-once execution, journal, universal time bound or power-loss/host-reboot durability is claimed; tmpfs is volatile across reboot.

Directory and file identities here are device/inode observations, not filesystem generation identifiers. A filesystem may reuse those numbers after removal. After publisher descriptors close, deletion and recreation with reused identities and identical expected bytes can be indistinguishable, including for directory chains. This limitation was reproduced on an actual overlay filesystem; it is not specific to ext4. Strict reading still checks the independently held execution/repository/sequence/input context and the original content digest. It does not prove uninterrupted existence, original physical-file provenance, or absence of an intervening delete/recreate cycle. The freshness guarantee concerns the expected observation and its content, not filesystem-object continuity. Timestamps are not treated as generation identifiers.

A complete bounded file is written exclusively inside the destination directory, then hard-linked exclusively to its final name. Existing names are never replaced or adopted, even with matching bytes. The publisher verifies its own inode/content before granting a locator. The locator retains source/directory/file identities, byte count and digest; the reader reopens no-follow/nonblocking, refuses nonregular files, checks identities and content, and decodes against independent expected context after publisher descriptors are closed.

Publication facts remain separate:

- published: commit verified, locator granted.
- not_published: known no commit, no locator.
- unknown: commit not established, no locator, retry or older-file fallback.

A commit-call exception is not proof of nonpublication. Exact owned-inode/content verification may establish a commit; otherwise it stays unknown. An unexpected publisher exception or malformed return is conservatively unknown. Cleanup is independently not_needed/complete/failed; failed temporary cleanup does not erase a verified commit. Only owned temporary names are removed within the stated domain.

Ordinary diagnostic failures never enter State.infra_errors or replace native results. Process-control exceptions propagate. An existing native primary exception wins over secondary diagnostic recording and owned diagnostic cleanup failures. Worker scope invalidation and completion signaling remain in finally. #445 immediate diagnostics and #447 original worker-exception transport are preserved.

At the original 1bb73ef/5e2d924 baseline, a SystemExit from the owned cwd ExitStack's shutil.rmtree callback could replace a pending primary RuntimeError. The bounded repair uses a private `_PerRepoCwdCleanup` owner for that scope only: delegate cleanup to the original ExitStack algorithm and preserve the exception already leaving the body. For failures raised inside callbacks, that algorithm continues the remaining registered callbacks in LIFO order. Without a pending body exception, cleanup errors still propagate, including the original last-error priority for multiple failing callbacks. A normal FAIL verdict or sibling advisory is not a pending exception.

The existing ignore_errors=True behavior remains: an ordinary rmtree permission failure can leave an actual worker directory behind. Publisher cleanup status describes only diagnostic staging, not cwd deletion. Even one asynchronous interruption outside callback execution can stop ExitStack before remaining callbacks are attempted; this inherited boundary is not repaired. Source-lock release, worker completion/join, deferred signals, capture close and receipt handling are unchanged. The repair does not decide exception priority inside those other scopes, repair unreturned/unregistered cwd allocation, or promise cleanup through asynchronous interruption or uncatchable process death. Native controls inspect residue and strictly reread reached publication after unwinding; full G11/T18 acceptance still requires its remaining scope dispositions. See the [acceptance disposition](cross-repo-diagnostics-v1-acceptance.md#bounded-t18f-cwd-cleanup-repair).

## Verification boundaries and follow-up

Tests exercise native SQLite branches; same-hash new executions; early-return and final-empty cache behavior; mutable-return isolation; stage limits and native single evaluation; malformed/context/deep/duplicate/resource decoding; namespace/identity/no-overwrite faults; uncertain commit and cleanup; post-temporary-cleanup strict reading; real CLI host presentation controlled by reader return; original process-control identity; and public immediate/background MCP handlers with an actual CLI child. The MCP test is not SDK-stdio protocol coverage.

Independent reviews found and corrected actual context-replacement, relative-root, stale-error, surrogate-error and malformed-consumer defects. Their semantic RED/restored GREEN evidence is separate from generic regression totals. No skipped dependency, setup/import error or source-presence assertion counts as a meaningful omission control. Final source checks and public node identities must be recorded before a PR is described as ready; the implementation alone does not imply complete T01–T20 acceptance or issue closure.
