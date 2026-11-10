# Cross-repository diagnostic protocol v1: proposed implementation contract

Status: unapproved candidate revision of the #441 v4 implementation choices. It does not replace operative issue requirements before explicit acceptance. This document records concrete choices for review; it is not a claim of production wiring, G0 approval, G1 admission or T01–T20 completion. Source baseline: `e5ecb784fa5672aa787735d18b8fbf3ed780e2be`.

References: [#441](https://github.com/HouMinXi/forge/issues/441), [v4](https://github.com/HouMinXi/forge/issues/33#issuecomment-6742713536), [independent review](https://github.com/HouMinXi/forge/issues/33#issuecomment-6742850246), [current schema proposal](https://github.com/HouMinXi/forge/pull/443), [work-package update](https://github.com/HouMinXi/forge/issues/441#issuecomment-6757925547).

## 1. Supported product path and retained boundaries

V1 supports the existing cross-repository primary axis, reached through CLI and its immediate/background MCP subprocess. It also supports direct calls to the existing `run_cross_repo` host. Single-repository HOLD does not construct this axis; no new single-repository or cross-repository recovery interface is introduced. Every new host call has a new execution identity. Moving the same MCP subprocess into a background job retains its identity.

The real path is CLI `_cross_repo_verdict_or_none` → `run_cross_repo` → primary `_thread_fn` → `StateMachine.run` → `_run_advisory_axes` → native `CrossRepoImpactRunner.run` → host recorder/publisher → CLI consumer → strict reader → existing immediate output. The proposed CLI consumer would be installed by production code, not only tests. Direct host callers would get the same strict-reader consumer by default. Neither exists yet.

The public return remains Verdict. `run(diff_text, repo_root)` stays narrow. The six other advisory axes, source-root routing, source locks, worker completion and deferred signals, receipts, State schema, diagnose and credit rules stay unchanged. Cross-repository PENDING still becomes FAIL. #33, #441 and the two review-support drafts remain open while acceptance is incomplete. #68 and #400 protocols are not reused or changed.

The consumer receives a host-held expected context and a publication result independently of file contents. The strict reader's result supplies the diagnostic text used by the existing output branch. A successful empty diagnostic stays silent. For no_named_targets only, ordinary publication/read/consumer failures retain the fixed immediate warning exactly once and add a separate bounded persistence warning. Other states may receive that persistence warning but must never acquire a fabricated no-target warning. They never change the gate. This provides same-execution consumption and durable files; it does not introduce a later-invocation browsing/retrieval interface.

## 2. Discovery capture decision and proven priority constraints

The effective-input approach below requires an explicit revision of the earlier requirement that the arbitrary provider locator consume one caller-supplied read-only environment mapping. The installed provider's `default_registry_path()` takes no mapping. It reads `CRG_HOME` live and otherwise uses an import-bound default. Calling it while separately copying Forge settings is not atomic, even if both results enter one frozen dataclass.

Two alternatives remain open:

- **Retain the original single-snapshot requirement.** A compatible provider interface that accepts captured configuration is needed, or a separately reviewed isolated evaluation mechanism. No such mechanism exists in this slice. Copying provider rules or temporarily rewriting process-global environment/cwd is excluded. Do not expand #68 lifecycle work to obtain isolation.
- **Accept effective-input capture instead.** Capture the actual provider result and Forge-owned inputs once, then use only the resulting immutable outcomes. This deliberately permits acquisition across different instants. It makes no atomic-acquisition, ABA, registry/database-content, authentication or graph-freshness claim. The parent/maintainer has not accepted this weaker contract.

The second alternative must obey these branch rules, demonstrated by current native code and the proposed characterization tests:

| Original consumption point | Captured input/outcome permitted to become an error |
| --- | --- |
| Blank diff | None. No provider import, locator call, registry or graph access |
| Provider import/default locator | Only the actual provider import/locator outcome |
| Default registry read | Only the selected default registry read outcome; this precedes graph selection |
| Primary graph selection | A matching registration's nonempty data_dir wins. An unused CRG_DATA_DIR failure stays dormant |
| Primary graph open/target resolution | Missing/corrupt/identity failures precede enumeration errors |
| Nonblank zero named targets | Fixed no-target output; enumeration is neither selected nor read |
| Named targets resolved | Select the captured enumeration override or reuse default registry snapshot, then enumerate siblings |
| Each sibling graph selection | Registration precedence applies independently; a captured fallback error becomes relevant only if fallback is selected |

Do not eagerly normalize all paths and raise the first error. Capture raw Forge-owned values without interpreting irrelevant branches. Any eager normalization that is needed to prevent a later ambient read must retain its result **or its exception as data**, with the exception surfaced only at the original consumption point above. Enumeration overrides remain literal (no tilde expansion). Registered relative data paths retain literal tilde behavior and resolve against captured cwd. CRG_DATA_DIR retains strip/empty/tilde behavior.

**Unresolved implementation detail:** settling CRG_DATA_DIR tilde expansion without later HOME/account-database reads, while preserving irrelevant-input laziness and error priority. One candidate is a captured expanduser outcome with deferred failure, then cwd-relative resolution only when selected. This still needs exact dependency and side-effect characterization, especially `~user`, HOME absence and import-bound provider fallback. It is not frozen by this document.

The internal discovery state must explicitly distinguish not_needed (blank diff), captured (immutable inputs plus deferred field outcomes), and capture_failed (provider-level failure). Proposed failure policy is one retained provider-capture outcome per execution; uncached graph analysis may repeat, but capture is not silently retried against new ambient settings. This differs from current fresh early-return discovery and needs acceptance. A new execution retries capture.

A changed host-held captured object before cache use is an admission mismatch and must reject before returning cached data. Ambient changes after accepted capture are intentionally ignored; they are not proof of mismatch. Missing capture on a nonblank call is unavailable, never an empty success. Direct unbound runner calls retain legacy configuration behavior; proposed production host calls use the bound path. No production capture code has been added.

## 3. Execution, roots and immutable observations

One UUID4 is issued by `run_cross_repo` per new call. The primary source receives a distinct UUID4 repository token. Repository paths and raw environment values are not serialized. The host binds that token to both the lexical absolute and canonical reviewed root; alternate spellings are not automatically admitted during runner reuse.

Inside the worker, a thread-confined active scope holds execution/repository identity, source hash, exact diff digest and effective discovery object. Every bound runner call validates the current active scope, both root spellings and exact diff before reading `_cached` or clearing errors. Closing the actual worker scope invalidates subsequent use. A new host always constructs a fresh runner and scope.

`source_hash` retains the existing project value. `diff_sha256` hashes the exact UTF-8 diff without text or newline normalization. Neither hash replaces execution identity or proves authenticity.

Host-assigned observation sequence starts at 1 and advances only for actual observations. Actual early returns remain uncached and receive new sequence numbers. Final query cache hits reuse the original observation and publication result, with separate delivery metadata `origin=cache`. New actual observations use `origin=actual`. No cache hit creates query credit, a new observation or a new file.

Original observation, diagnostic tuple, finding snapshot and original infrastructure-error tuple are immutable. Bound outward finding lists are defensive copies. Direct unbound runner cache identity remains compatible. Existing infrastructure errors still use their original channel; they are not serialized as arbitrary diagnostic text.

## 4. Closed v1 payload and resource choices

These are proposed explicit v1 values for approval, rather than claims that the old draft already froze them.

| Field | Exact contract |
| --- | --- |
| schema_version | JSON integer 1; bool, float and string rejected |
| execution_id | canonical lowercase UUID4 string, equal to host expectation |
| repository_binding | exactly `{kind: "invocation-repository", id: <canonical UUID4>}` |
| axis | exactly `CROSS-REPO-IMPACT` |
| observation_sequence | exact integer 1 through 1024 |
| source_hash | nonempty string, at most 256 UTF-8 bytes; no new fixed digest-length rule |
| diff_sha256 | exactly 64 lowercase hexadecimal characters |
| analysis_state | one of the five states below |
| diagnostics | prescribed empty list or one closed code/message object |

Every field is required. Null, missing and empty are different. Extra fields are rejected at every object level. The repository binding is correlation, not authentication. Context/configuration ownership is established by the host, not a persisted public path hash. The reader does not independently prove graph configuration or freshness from this payload.

Fixed diagnostic:
- code: `no-named-changed-symbols`
- message: `cross-repo-impact: no named changed symbols resolved; direct-caller impact not evaluated`

| State | Evidence | Diagnostics |
| --- | --- | --- |
| not_requested | actual blank-diff branch | empty |
| not_run | host positively establishes successful completion without an axis call | empty |
| no_named_targets | actual nonblank zero-target branch after existing prerequisites | exactly the fixed diagnostic |
| targets_resolved | at least one named target, even if later sibling work fails | empty |
| unavailable | existing target-stage discovery/graph failure | empty |

An exception before an observation is not synthesized into not_run or unavailable. Findings emptiness is never used to derive state.

Limits: 16,384 raw file bytes, maximum JSON container nesting depth 3 (top-level object is depth 1; arrays also count), maximum decoded string size 256 UTF-8 bytes, at most 1024 actual observations per execution's single supported primary axis. The current source hash is 64 ASCII bytes and all other variable fields are fixed-size identifiers. Depth 3 follows the deepest legal shape (root object → diagnostics array → diagnostic object). The byte and string caps are candidate budgets, not previously approved requirements. A compact ensure_ascii=False JSON fixture at all candidate maxima is 2,050 bytes (256 decoded NUL bytes in source_hash become 1,536 escaped bytes); 16,384 permits at most 14,334 additional whitespace bytes for that shape. Other serializer spellings remain subject to the same raw cap, not a requirement to use canonical bytes. The 1024-observation choice is unqualified: native StateMachine.run currently dispatches these axes once after convergence, and this document has no workload evidence requiring 1024. A retained-record and publication-attempt ceiling must be selected together; unlimited unpublished objects must not be accumulated. Repeated cache deliveries do not consume observation slots. Proposed overflow behavior: retain one host-level limit_exhausted delivery failure, publish no invented observation, and emit at most one persistence warning per host. Continue underlying analysis and any genuine no-target warning. Do not silently wrap or reuse a sequence. This policy and its ceiling need approval.

The reader performs an upper-bound-plus-one read, strict UTF-8 decoding and a quote/escape-aware depth scan before JSON decoding. Duplicate keys, nonfinite constants, malformed JSON, trailing nonwhitespace, unknown versions/enums, invalid combinations, oversized decoded strings and escaped lone surrogates are rejected. String sizes count strict UTF-8 bytes after JSON decoding, not escape spelling. The entire parse is bounded by the byte cap. Expected context types and identity are validated independently; missing/corrupt/wrong-context files never mean empty success.

## 5. Persistent namespace and exclusive publication

Default durable root: the canonical primary repository's `.code-forge/advisory-diagnostics`. The host validates this root against every temporary per_cwd and its resolved aliases. The namespace is execution UUID / repository UUID / fixed axis / sequence JSON filename. IDs are validated before path construction; no path comes from decoded file contents.

Proposed first support domain: qualified Linux local filesystems with directory-descriptor-relative operations and exclusive hard-link publication. POSIX alone is not a qualification. Current cloud filesystem probes are still pending; macOS, Windows, network filesystems and exotic FUSE filesystems are unqualified. Unsupported operations produce explicit not-published results while preserving existing review/output behavior. No supported-platform claim is made yet.

Candidate ownership rule: open the canonical source root through a no-follow directory chain, verify its retained device/inode identity against the host, then anchor all writes to held descriptors. From the source root through every diagnostic component, require effective-UID ownership and no group/world write bits; a pre-existing .code-forge is checked, never chmodded. Create directories mode 0700 and files mode 0600 (allowing only further restriction by umask). Reject nonregular files, symlink components and detected extended access/default ACLs. ACL inspection that cannot establish the rule is unsupported, not assumed safe. These Linux-specific rules need filesystem qualification and compatibility review for shared/group-writable checkouts; they are not a universal filesystem policy. Ancestors above the source root need not be owned or private, but are opened no-follow and never used again through an unchecked pathname. Preliminary checks followed by unconstrained ordinary pathname writes are insufficient.

Write a complete bounded temporary file exclusively within the destination directory. Publish with an exclusive hard link to the final name; never replace an existing name. Verify the final file's identity and expected bytes before granting a locator. A host-held locator retains validated relative components, directory/file device+inode identities, byte count and digest, not an open descriptor or a path obtained from the payload. After worker descriptors close, the reader must reopen the canonical root and each component no-follow, check the retained identities and file bytes, then decode against the independent expected context. A moved/replaced tree is unavailable, not accepted through a new alias. Same-key publication, whether contents match or differ, is refused; legitimate cache delivery reuses the prior publication result without retrying. No global latest file is authoritative.

This provides complete-file visibility and no-overwrite behavior, not exactly-once execution, a journal, power-loss durability, or a universal filesystem time bound. No automatic retention/deletion policy is added.

## 6. Publication, reading and failure facts

`PublicationResult` is host-held and separate from the payload:
- published: commit and expected file identity/content verified; carries a successful locator and digest.
- not_published: this attempt is known not to have committed; no successful locator.
- unknown: commit cannot be established; no successful locator, retry or fallback to an older record.

An exception from the commit operation is not enough to infer nonpublication. The implementation may verify its own exact retained temporary/final identity to settle a known commit; otherwise it preserves unknown. Existing foreign names never become successful publication merely because their bytes match.

Cleanup result is independent. A known commit can remain published when removal of an owned temporary name fails. Cleanup never removes a foreign name or a committed record. A pre-existing primary exception wins over cleanup/diagnostic failures; absent a primary, process-control exceptions propagate. Ordinary recorder/publisher/reader/consumer failures, including malformed callback returns, are contained before `machine._run_advisory_axes` can turn them into State.infra_errors.

Actual observations publish before the fixed immediate output, so output cancellation cannot erase the observation. They go directly to the durable root rather than being copied only beside normal receipt collection. Before-axis failure retains a separate host-level delivery status not_attempted and no locator/observation, not successful not_run. Its original exception remains primary. A limit failure likewise carries no fabricated observation. Closing the host scope does not depend on publication success.

## 7. Implementation map and decisive acceptance controls

| Area | Intended responsibility |
| --- | --- |
| new advisory_diagnostics module | closed types/decoder, strict reader, exclusive publisher, result/cleanup facts, host-owned scope/delivery |
| cross_repo_impact constructor/run | bound configuration consumption, pre-cache admission, actual branch observations, immutable cache snapshot and delivery distinction |
| cross_repo primary worker | issue/bind/close scope, durable-root exclusion, production recorder and caller delivery; no source-lock/signal rewrite |
| cli._cross_repo_verdict_or_none | install the actual strict-reader consumer; return its validated presentation to the existing output path |
| machine advisory dispatch | retain native source-root routing and original gate behavior; avoid a broad State save |
| tests/manifest/docs | real path, semantic omission controls, exact collected identities and explicit support boundaries |

Required controls include real SQLite target/error/zero-caller cases; real CLI → host → machine → runner → publisher → caller → reader; post-per_cwd-deletion reading; same-hash/new-execution rejection; cache SQL counts and early-return re-execution; root/config changes before cache; returned-list/error snapshot isolation; missing/corrupt/deep/duplicate/unknown-version/wrong-context files; exclusive same-key conflict; commit-then-error unknown; each write/open/link/read/cleanup boundary; primary errors and output KeyboardInterrupt/SystemExit; non-gating consumer failure and normal PASS/FAIL/PENDING behavior; immediate and background MCP inheritance through the actual CLI process.

Negative controls remove one corresponding production behavior at a time. Reader omission must affect a dedicated actual-consumer assertion, not merely a spy call count. Tests may use a controlled presentation marker after actual strict reading to prove the returned presentation reaches output; malformed production payloads remain rejected. Test fixture/setup/import failures do not count as semantic RED. T01–T20 and T18a–f remain planned until real node IDs, executed results and omission locations are recorded.

## 8. Old-clause disposition and current blocking decisions

| Old or ambiguous clause | Proposed disposition | Current status |
| --- | --- | --- |
| Put the new warning into State.infra_errors / use State save as durable acceptance | Reject; separate non-gating artifact and independently contained failure result | Consistent with current #441, not implemented |
| Source hash or a context label is execution/config identity | Reject; host-issued identity plus actual consumed immutable inputs | Production wiring absent |
| One atomic selected-environment snapshot for arbitrary provider | Keep as blocker or explicitly replace by section 2's weaker effective-input contract | Maintainer decision required |
| Cache hit creates a new actual observation or new query credit | Reject; reuse original observation/publication with separate cache delivery | Immutable cache and delivery not implemented |
| Copy artifact only in normal receipt-collection step | Reject; direct durable publication before immediate output | Writer absent |
| Commit exception means no artifact / identical foreign bytes mean success | Reject; three publication states and own-identity proof | Publisher absent |
| Cleanup failure erases commit or replaces primary exception | Reject; retain independent cleanup result and primary failure | Exception-boundary implementation absent |
| All CLI/MCP/recovery entry points are supported | Restrict to existing cross-repo CLI and the same MCP subprocess, plus existing direct host calls; defer nonexistent cross-repo HOLD/recovery | Needs accepted support matrix and actual path tests |
| Prototype tests, CI or an issue checkbox establish acceptance | Reject; exact final-source tests, semantic controls and independent review are separate | G0 REQUEST_CHANGES; G1 not granted |

The independent review of the first draft found a real eager-normalization priority defect in the design; section 2 withdraws that algorithm. No production code had been changed. The current deliverable is a proposal plus minimal regression/characterization evidence. Outstanding decisions are the capture contract and failure retry semantics, supported filesystem/ownership compatibility, exact resource budgets, schema/delivery choices, and ownership approval for eventual production functions. Existing #443/#444 are retained. No persistence feature, formal consumer, G0/G1 passage or T01–T20 success is claimed.

## 9. Executed preparation evidence (not protocol acceptance)

Baseline: main e5ecb784. The only executable repository changes in this deliverable add seven native-runner/real-file regression cases (the optional provider locator is stubbed in these existing fixtures) to tests/test_cross_repo_immediate_diagnostic.py and register their exact collected identities. There are no src changes.

| Exact test function (parameter identities are in the public manifest) | Executed evidence |
| --- | --- |
| test_blank_diff_never_imports_optional_provider | Two blank inputs; no provider import, warning or infrastructure error |
| test_registered_primary_data_dir_does_not_interpret_unused_override | Real registered graph wins over a verified unknown-user tilde error; removing registration activates that same real error |
| test_unusable_enumeration_preserves_primary_branch_priority | Unnamed, missing and corrupt primary paths preserve their outcomes with a real unreadable enumeration symlink; named targets activate enumeration failure |

Focused module: 44 passed on Python 3.12.14 / pytest 9.2.0. The affected cross-repo/advisory/public-test transport regression selection with actual provider 2.3.9 installed passed 594 tests with no skips. The earlier provider-free run passed 536 and skipped two optional-provider modules; those counts are not added together. Full-tree pinned Ruff check and format passed (941 Python files), and git diff --check passed. Independent isolated semantic controls: premature provider import fails two cases; premature fallback expansion fails one; moving actual enumeration reads before primary checks fails three while the named positive control passes. Restored module: 44 passed. No fixture/import/collection failure is counted as a negative-control result.

Independent review found the first test draft assumed non-strict Path.resolve raises on a symlink loop; direct Python 3.13.5 disproved that assumption. The corrected tests use actual unknown-user expansion and actual loop reading instead, both directly checked on 3.13.5. Full 3.13 pytest and 3.14 runtime qualification were not performed.

Separate actual-provider characterization on code-review-graph 2.3.9 reran three controls: import-bound fallback versus live CRG_HOME; candidate configuration labels failing to freeze native data-directory use; relative registered directories depending on cwd. These are existing preparation controls, not seven additional new tests, not a production fix, and not T01–T20 credit. The label-only candidate is not imported by the production package.
