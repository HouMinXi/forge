# Persistent diagnostic v1: acceptance disposition

This record separates implemented behavior, directly tested behavior and remaining acceptance decisions. It is not G0/G1 approval, issue closure, a replacement for the unpublished original 78-test port, or a claim that every T01–T20 negative control has run.

## Fixed source and discussion

Product source: merged main `1bb73ef172dae05d335c507657f8fba78320aa78`, tree `0203323c37cfb15a816775e9ebebe9150d5b4fff`. This is the same tree as PR #448 head `2161dc1fe064b7aa80fca300b1bcf476896dcea2`. This follow-up changes test evidence and documentation, not production behavior.

Read together:

- [The implemented bounded contract](cross-repo-diagnostics-v1.md).
- [Original v4 and T01–T20](https://github.com/HouMinXi/forge/issues/33#issuecomment-6742713536).
- [Four P1 requirements and T18a–f](https://github.com/HouMinXi/forge/issues/33#issuecomment-6742850246).
- [G01–G13 integration requirements](https://github.com/HouMinXi/forge/issues/441#issuecomment-6745320203).
- [Post-merge G01 synchronization](https://github.com/HouMinXi/forge/issues/441#issuecomment-6761092644).
- [Post-merge G02 synchronization](https://github.com/HouMinXi/forge/issues/441#issuecomment-6761166884).

The two issue discussions were reread at the design and implementation checkpoints, including existing comment bodies. Their latest comments at those checkpoints were #33 comment 6745347662 and #441 comment 6761166884. A later review must refresh these sources rather than treating this dated snapshot as current authorization.

The one authorized post-merge CI round completed successfully: three workflows, nine checks, all attempt 1. Its [Linux run](https://github.com/HouMinXi/forge/actions/runs/42766158477) is evidence about that fixed revision, not acceptance of the specification. The preceding PR round had eleven checks; these are different check sets and must not be combined into one count.

## Four P1 dispositions

| Requirement | Fixed implementation and real consumer | Evidence and remaining limit |
| --- | --- | --- |
| P1-1: identity issuance and delivery | `cross_repo.run_cross_repo` creates the execution UUID; its primary worker creates `DiagnosticCapture` with a repository UUID, exact diff and source hash. `finish` owns the expected context and publication result; `present` strictly rereads and supplies the existing attributed output callback. CLI uses this host; immediate/background MCP use its CLI child. | New-host/same-hash and foreign-context tests exist. The public return remains `Verdict`, not a new result API. The internal host consumer obtains the context/status/locator. No single-repository diagnostic recovery or cross-repository HOLD was added. |
| P1-2: immutable observations and cache admission | `DiagnosticCapture.admit` runs before cache return/error clearing. Stage results are retained at first actual consumption. The runner's final cache refers to the original record; early returns create new observations. `present(..., origin="cache")` updates delivery metadata only. | Real SQLite tests count queries, inspect original record identity, preserve error history, reject changed contexts and isolate returned lists. Configuration is stage-effective, not simultaneous. Graph changes are not automatically detected. |
| P1-3: publication before exceptional cleanup | Primary source `.code-forge/advisory-diagnostics` is checked against every per_cwd, then written directly; normal receipt collection is not the publication point. Publisher cleanup and capture close preserve primary exceptions and committed records. | Normal cleanup, output/read controls and pre-axis failures are covered. Supplemental controls exercise deferred signal replay and ordinary per_cwd cleanup failure. A process-control exception from the outer per_cwd cleanup can replace the primary exception: T18f remains unsatisfied for that boundary. |
| P1-4: uncertain commit | `_advisory_diagnostics.publish_observation` writes a complete exclusive temporary file and hard-links a final name exclusively. Reconciliation verifies the owned inode and bytes after an ambiguous commit. `PublicationResult` keeps commit and cleanup separate; `read_observation` requires independent context. | Commit-then-raise, unknown without locator, foreign-file refusal, same-key conflict, owned-temp cleanup and post-close strict reading are tested. Unknown never grants a successful locator, retries publication or adopts a historical file. This is not a crash journal or exactly-once guarantee. |

## Older clauses requiring explicit disposition

These are the implemented choices to review, not unilateral waivers of the original acceptance requirements.

| Earlier candidate or ambiguous claim | Implemented choice | Decision still needed |
| --- | --- | --- |
| Capture all discovery inputs as one snapshot | Record only real first-use stage results, preserving lazy errors, lexical equality and duplicate occurrences. | Accept stage-effective v1 as the consistency contract, or request a separately designed atomic contract. Do not call sequential capture atomic. |
| Put `origin` in the serialized observation | Keep the nine-field observation immutable and record actual/cache only in host delivery metadata. | Accept the observation/delivery separation; a cache read is not a new graph query. |
| General recovery/HOLD identity requirement | New supported host calls get new identities. Cross-repository PENDING remains FAIL. Single-repository HOLD does not construct this diagnostic axis. | Mark the unsupported recovery portion out of this slice explicitly, or scope a separate supported integration. A stubbed PENDING or assigned verdict is not a recovery run. |
| Durable root or return interface unspecified | Use the primary canonical source root and a private host consumer; public runner/CLI/MCP/Verdict APIs remain unchanged. | Accept this concrete consumer and root rather than require an unused public session interface. |
| General filesystem identity or freshness | Independently bound observation identity and content digest; device/inode is not a generation identifier. Local Linux overlay/tmpfs exercised; ext4 eligible but not locally qualified. | Accept this bounded storage domain. Same-UID hostile replacement, inode continuity, network filesystems, power loss and reboot persistence are not proved. |
| Main exception always wins through all cleanup | It wins through the diagnostic publisher/capture cleanup, but not through a later outer per_cwd SystemExit. | Resolve T18f explicitly. Do not rename this failure into a pass or silently relax the requirement. |
| Original 78-test port or all T controls already available | The public production tests are real and separately identified. The complete original port has not been recovered and cannot transfer its count or credit. | Recover the original source or approve an explicit, versioned replacement with an exact T/node/omission map. |

## G02 schema and resource evidence

The [wire contract](cross-repo-diagnostics-v1.md#closed-v1-wire-contract) is closed at all object levels: exactly nine root fields, canonical UUID4 contexts, sequence 1–64, five analysis states with fixed diagnostic combinations, 4096 raw bytes, container depth 3 and 256 decoded UTF-8 bytes per schema string. `_payload`, `decode_observation`, `_pairs`, `_integer`, `_scan_depth` and `_validate_context` implement this contract. `read_observation` additionally checks held host context, namespace, descriptor identities, exact byte count and the original digest.

The first post-merge review found a test-oracle defect, not a decoder defect: deleting only the `_pairs` duplicate-key guard still passed all 311 original storage test cases. The old duplicate documents were invalid for other reasons. The added `test_duplicate_keys_in_otherwise_valid_document_rejected[root]`, `[repository]` and `[diagnostic]` start from a valid v1 payload; ordinary dict decoding produces exactly the original legal object, while the actual strict decoder must reject. Removing only duplicate detection produces three semantic assertion failures, and restoration passes. This prevents an unrelated schema rejection from masquerading as duplicate-key coverage.

The contract's retained configuration/history/observation budgets are separate from the 4096-byte decoder budget. Sixty-four payloads imply at most 256 KiB of logical final payload, not total filesystem allocation, temporary-name retention, a global cross-run retention bound or a time limit. No new numerical ceiling or platform expansion is introduced here.

## Reachable gate evidence and its limits

The original persistent CLI/MCP fixtures use the real host, runner, SQLite, publisher and reader but replace `StateMachine._run_ci` with a supplied verdict. They prove transport and isolation conditional on that verdict. They do not by themselves prove natural PASS/FAIL reachability or HOLD recovery.

`tests/test_cross_repo_diagnostic_gate.py` adds service-free docs-only reviews through the actual CLI dispatch helper, real Git repositories and SQLite graphs, unchanged `run_cross_repo`, `StateMachine._run_ci`, L1 processing, `RealFalsifier`, CI decision and advisory dispatch. A deterministic executable backend supplies structured model responses; the gate is not replaced. PASS genuinely converges; FAIL genuinely retains confirmed L1 findings. Both are exercised with publication and an injected ordinary publication failure.

The checks observe the real gate return and State before/after advisory execution. All non-infrastructure State fields are compared without normalization, including findings, dispositions, counters, credit and round history. Infrastructure errors are tested directly for diagnostic contamination rather than removed from the live State. Actual round and terminal receipt decisions succeed; all three primary receipt files remain byte-identical across advisory dispatch and the host's final receipt copy. Completed status, finding count and qualified code excerpts are independently checked. The fixed diagnostic and persistence warning remain outside State/findings/receipts, and actual publication/read/delivery facts and worker cleanup are checked.

These cases do not execute a code mutation workload, prove model quality, test the outer CLI argument parser, or add SDK-stdio MCP or recovery coverage. Existing actual-child MCP transport tests remain separate evidence. Combining two tests does not create an unexecuted all-in-one path.

## T control map and remaining work

The following names refer to actual collected pytest nodes, not proposed test counts. Prefixes expand to `tests/test_cross_repo_immediate_diagnostic.py::` (I), `tests/test_cross_repo_capture.py::` (C), `tests/test_cross_repo_persistent_diagnostic.py::` (P), `tests/test_advisory_diagnostics.py::` (A) and `tests/test_cross_repo_diagnostic_gate.py::` (G). A function without a parameter suffix identifies its collected family, not a claim that each family is one case. Exact identities are in `scripts/ci_public_test_identities.json`.

| Original control | Concrete repository evidence | Remaining qualification |
| --- | --- | --- |
| T01 no index hit | I `test_zero_targets_emit_once_before_sibling_access[no-matching-node]`; P `test_real_cli_publishes_before_output_and_survives_worker_cleanup` | Direct branch and persistent path are separate controls; no claim of a newly executed omission for every target branch. |
| T02 unnamed only | I `test_zero_targets_emit_once_before_sibling_access[unnamed]`; G `test_docs_only_native_ci_keeps_gate_state_with_diagnostic` | Real graphs contain only a module-level node for the changed file. This does not prove every unsupported-language parser behavior. |
| T03 no path/deletion | I `test_zero_targets_emit_once_before_sibling_access[no-path]` and `[deleted]` | These immediate-warning controls do not alone prove every connection-count and persistent-state subclause. |
| T04 corrupt graph | I `test_prior_unavailability_does_not_claim_zero_targets[corrupt]`; P `test_actual_analysis_states_do_not_invent_no_target_warning` | Existing error priority is tested; no migration of old infra errors into the artifact. |
| T05 blank diff | I `test_empty_diff_has_no_diagnostic_or_discovery`; P `test_actual_analysis_states_do_not_invent_no_target_warning[blank]` | Blank is not_requested, not a successful graph query. |
| T06 zero/real callers | I `test_resolved_targets_keep_findings_and_cache_without_zero_target_output[False]` and `[True]`; `tests/test_cross_repo_impact.py::TestTopNCap::test_cap_at_top_n` | The retained top-10/ordering regressions are not a new multi-hop or coverage guarantee. |
| T07 sibling isolation | I `test_zero_targets_emit_once_before_sibling_access`; C `test_duplicate_occurrences_retain_distinct_first_use_native_fallbacks` | Native stages and duplicate occurrences are measured, not an atomic environment snapshot. |
| T08 production publication | P `test_host_publication_strictly_reads_after_temporary_cwds_are_removed`; G `test_docs_only_native_ci_keeps_gate_state_with_diagnostic` | Removing the actual publication call yields semantic RED; no hand-built artifact substitutes for this path. |
| T09 state replacement | P `test_uncached_branch_transitions_get_new_records_and_cache_reuses_publication` | Genuine uncached transitions create new sequences; cache retains the original record. |
| T10 same hash, new host | P `test_new_host_execution_never_reuses_same_source_hash_identity`; A `test_new_execution_with_identical_source_and_diff_cannot_reuse_record` | New host execution is supported; unsupported HOLD recovery is not inferred. |
| T11 cache same input | C `test_duplicate_occurrences_retain_distinct_first_use_native_fallbacks`; P `test_uncached_branch_transitions_get_new_records_and_cache_reuses_publication` | Strengthened oracle checks actual/cache metadata, original record identity, unchanged SQL and publication attempts. Removing cache-origin marking now fails. |
| T12 context mismatch | C `test_changed_capture_refuses_before_cached_findings_queries_or_error_clear`; C `test_changed_input_refuses_before_cache_sql_error_clear_or_publication[exact-diff]`, `[lexical-root]`, `[canonical-root]` | Each newly guarded comparison has its own exact omission RED. Same-UID filesystem generation continuity remains excluded. |
| T13 gate isolation | G `test_docs_only_native_ci_keeps_gate_state_with_diagnostic[published-PASS]`, `[published-FAIL]`, `[publication-fault-PASS]`, `[publication-fault-FAIL]` | Reachable docs-only native gate, State and receipt evidence. Injecting the diagnostic into State errors yields four direct membership assertion failures. No code-mutation workload or model-quality claim. |
| T14 recovery | P `test_new_host_execution_never_reuses_same_source_hash_identity` | Only new supported host identity is proved. Cross-repository HOLD is unsupported; single-repository recovery has no axis. Explicit scope disposition is still required. |
| T15 write failure | A `test_precommit_operation_failure_is_not_published`; `test_incomplete_write_never_publishes_and_removes_owned_temp`; `test_link_error_distinguishes_verified_commit_from_unknown`; G publication-fault cases | Open/write/partial/commit ambiguity are covered. No automatic retry, crash journal or full-State-save behavior is introduced. |
| T16 strict format/context | A `test_duplicate_keys_in_otherwise_valid_document_rejected`; `test_wrong_independent_context_never_returns_empty_success`; `test_large_or_deep_document_rejected_before_json_load`; P `test_real_strict_read_rejects_lost_corrupt_or_wrong_execution_before_output` | Exact duplicate, context, pre-parser byte/depth and content-digest omissions fail. Exhaustive per-validator omission coverage and the original port remain unqualified. |
| T17 concurrency/duplicate | A `test_same_key_always_refused_without_replacing_original`; P `test_new_host_execution_never_reuses_same_source_hash_identity` | Public cases are sequential. Supplemental real hard-link barrier controls exercised distinct keys and conflicting same keys; they are not yet public registered tests or concurrent complete hosts. |
| T18 cleanup | P `test_host_publication_strictly_reads_after_temporary_cwds_are_removed`; A `test_durable_root_cannot_overlap_temporary_cwd` | T18a/d/e have repository controls. Supplemental b/c/f distinguish post-publication error, queued signal, staging cleanup and global cwd cleanup. Global T18f has the concrete failure below. |
| T19 interruption | A `test_process_control_propagates_and_preserves_committed_record`; `test_cleanup_signal_does_not_replace_primary_signal`; P `test_output_process_control_preserves_committed_file_and_exception_identity` | Diagnostic controls preserve exact exceptions/records. No forced process-death, SIGKILL, power-loss or global-cleanup equivalence is claimed. |
| T20 output/not_run | P `test_normal_machine_return_without_axis_call_records_not_run`; `test_actual_strict_reader_return_controls_cli_text`; `test_persistence_failure_never_fabricates_no_target_state`; real-child MCP cases in the same module | No-target output is the existing #445 warning; other empty states stay silent. Controlled-verdict MCP fixtures establish transport, not native gate reachability or SDK-stdio coverage. |

This map identifies bounded evidence and gaps. It does not convert partially covered T rows into acceptance passes. Supplemental controls must be integrated and assigned public identities before being described as repository regressions.

## T18f boundary and minimum next step

At fixed `cross_repo.py:396`, the outer `ExitStack` registers `shutil.rmtree(cwd, True)`. After all workers complete, a primary failure is raised at line 591. During stack unwinding, a `SystemExit` from that cleanup callback replaces the primary `RuntimeError`. An owned control checks both exact exception identities and independently rereads the committed artifact. This is a host-level exception-priority gap, not lost publication.

A separate control makes the real `rmtree(..., ignore_errors=True)` encounter `PermissionError` from `os.rmdir`. It preserves the original primary exception, leaves actual directories behind and leaves the diagnostic readable. Publisher `cleanup="complete"` describes only its owned staging file; it must not be presented as worker-directory deletion success.

The minimum proposed follow-up is to decide the host per_cwd cleanup exception-priority contract and ownership before modifying its callback registration. Preserve real cancellation when there is no prior primary failure, preserve the original primary when one exists, attempt remaining owned cleanups, keep thread completion/join behavior unchanged, and report or characterize leftover directories without fabricating success. Check parallel lifecycle changes first. No `invocation_host`, #68 lifecycle, #400 work or global cleanup redesign is included in this test/documentation follow-up.

## G01/G02/G13 admission record

| Gate | Current record | What this follow-up does not grant |
| --- | --- | --- |
| G01 | Fixed contract, four-P1 disposition and conflicting-clause/support-domain decisions are now explicit. | A maintainer's disposition of remaining conflicts or an unconditional P1-3/T18f pass. |
| G02 | Concrete closed v1 codec, bounds, actual consumers and stronger strict-decoder controls are identifiable. | Formal schema freeze, recovery of the old port or exhaustive per-check mutation coverage. |
| G13 | Independent reviews and reproducible tests are evidence for the exact changed files and fixed product source. | Formal G0/G1, retrospective approval of every older proposal, or completion of #441/#33. |

An admission decision should name the product revision and this evidence revision, accepted consistency/storage/recovery domain, treatment of the original port, exact remaining controls and T18f disposition. Record the reviewer conclusion separately from the maintainer's explicit acceptance. A merged PR or successful CI check is not that record.

#33 and #441 remain open. Drafts #443/#444 remain independent review-support artifacts until a verified replacement and explicit disposition exist.
