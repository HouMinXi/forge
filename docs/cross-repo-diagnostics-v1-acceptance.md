# Persistent diagnostic v1: acceptance disposition

This record separates implemented behavior, directly tested behavior and remaining acceptance decisions. It is not G0/G1 approval, issue closure, a replacement for the unpublished original 78-test port, or a claim that every T01–T20 negative control has run.

## Fixed source and discussion

Original product baseline: merged main `1bb73ef172dae05d335c507657f8fba78320aa78`, tree `0203323c37cfb15a816775e9ebebe9150d5b4fff`, matching PR #448 head `2161dc1fe064b7aa80fca300b1bcf476896dcea2`. The evidence-only revision in PR #451, merged as `5e2d924e896e5e658f6f88f6b37e80192398e3fa`, did not change that production behavior. PR #452, merged as `382a2ea4ecddfb55ff414ef16c73f5e15d2df08b`, changes only the exception priority at the owned per_cwd scope as described below. The subsequent A02 delivery-status supplement changes tests and evidence, not production behavior.

Read together:

- [The implemented bounded contract](cross-repo-diagnostics-v1.md).
- [Original v4 and T01–T20](https://github.com/HouMinXi/forge/issues/33#issuecomment-6742713536).
- [Four P1 requirements and T18a–f](https://github.com/HouMinXi/forge/issues/33#issuecomment-6742850246).
- [G01–G13 integration requirements](https://github.com/HouMinXi/forge/issues/441#issuecomment-6745320203).
- [Post-merge G01 synchronization](https://github.com/HouMinXi/forge/issues/441#issuecomment-6761092644).
- [Post-merge G02 synchronization](https://github.com/HouMinXi/forge/issues/441#issuecomment-6761166884).
- [Limited G03/G05/G06/G07 contract registration](https://github.com/HouMinXi/forge/issues/441#issuecomment-6763271541).
- [Parent-roadmap closing checklist](https://github.com/HouMinXi/forge/issues/33#issuecomment-6762043754).

The original design and implementation checkpoints read through #33 comment 6745347662 and #441 comment 6761166884. The cwd-cleanup follow-up reread both current issue bodies and all comments through #33 comment 6762043754 and #441 comment 6763271541, including comparison of existing comment bodies. Later reviews must refresh these sources rather than treat this dated record as current authorization.

The one authorized post-merge CI round completed successfully: three workflows, nine checks, all attempt 1. Its [Linux run](https://github.com/HouMinXi/forge/actions/runs/42766158477) is evidence about that fixed revision, not acceptance of the specification. The preceding PR round had eleven checks; these are different check sets and must not be combined into one count.

## Four P1 dispositions

| Requirement | Fixed implementation and real consumer | Evidence and remaining limit |
| --- | --- | --- |
| P1-1: identity issuance and delivery | `cross_repo.run_cross_repo` creates the execution UUID; its primary worker creates `DiagnosticCapture` with a repository UUID, exact diff and source hash. `finish` owns the expected context and publication result; `present` strictly rereads and supplies the existing attributed output callback. CLI uses this host; immediate/background MCP use its CLI child. | New-host/same-hash and foreign-context tests exist. The public return remains `Verdict`, not a new result API. The internal host consumer obtains the context/status/locator; its sufficiency under G04/G09 still needs an explicit decision. No single-repository diagnostic recovery or cross-repository HOLD was added. |
| P1-2: immutable observations and cache admission | `DiagnosticCapture.admit` runs before cache return/error clearing. Stage results are retained at first actual consumption. The runner's final cache refers to the original record; early returns create new observations. `present(..., origin="cache")` updates delivery metadata only. | Real SQLite tests count queries, inspect original record identity, preserve error history, reject changed contexts and isolate returned lists. Configuration is stage-effective, not simultaneous. Graph changes are not automatically detected. |
| P1-3: publication before exceptional cleanup | Primary source `.code-forge/advisory-diagnostics` is checked against every per_cwd, then written directly; normal receipt collection is not the publication point. Publisher cleanup and capture close preserve primary exceptions and committed records. `_PerRepoCwdCleanup` now preserves an exception already leaving the owned cwd scope while delegating removal callbacks to ExitStack. | Native callback-fault controls cover normal deletion, exact exception priority, all cleanup attempts and actual residue. A single asynchronous interruption outside a callback can still stop remaining attempts. This repairs the demonstrated cwd-boundary masking defect, not every outer lifecycle or directory-deletion failure. Full G11/T18 acceptance remains separate. |
| P1-4: uncertain commit | `_advisory_diagnostics.publish_observation` writes a complete exclusive temporary file and hard-links a final name exclusively. Reconciliation verifies the owned inode and bytes after an ambiguous commit. `PublicationResult` keeps commit and cleanup separate; `read_observation` requires independent context. | Commit-then-raise, unknown without locator, foreign-file refusal, same-key conflict, owned-temp cleanup and post-close strict reading are tested. Unknown never grants a successful locator, retries publication or adopts a historical file. This is not a crash journal or exactly-once guarantee. |

## Contract dispositions and remaining decisions

The issue now registers limited acceptance of G03, G05, G06 and G07. This is four contract dispositions, not a completion percentage, full runtime qualification or formal G0/G1 admission. G01, G02, G04 and G08–G13 remain open. The accepted clauses retain their T11–T12/G12 evidence obligations and supported-type/resource limits.

| Earlier candidate or ambiguous claim | Implemented choice | Disposition and remaining work |
| --- | --- | --- |
| Capture all discovery inputs as one snapshot | Record only real first-use stage results, preserving lazy errors, lexical equality and duplicate occurrences. | Limited G06 contract accepted. No atomic snapshot, repeated ambient reread or general freshness promise. Complete real-SQL, invalidation and omission evidence still belongs to T11–T12/G12. |
| Put `origin` in the serialized observation | Keep the nine-field observation immutable and record actual/cache only in host delivery metadata. | Limited G03 contract accepted. A cache delivery is not a new graph query or publication; complete runtime acceptance remains separate. |
| Unbounded or arbitrary nested snapshot isolation | Retain supported historical findings/errors and defensive containers within the stated budgets; unavailable history is not an empty successful snapshot. | Limited G07 contract accepted. Frozen/tuple types alone do not prove arbitrary deep immutability; partial-error, nested-isolation and resource controls still require their exact evidence. |
| General recovery/HOLD identity requirement | New supported host calls get new identities. Cross-repository PENDING remains FAIL. Single-repository HOLD does not construct this diagnostic axis. | Limited G05 boundary accepted. G04's entry/consumer decision and the original T14 disposition remain open; an assigned verdict is not a recovery run. |
| Durable root or return interface unspecified | Use the primary canonical source root and a private host consumer; public runner/CLI/MCP/Verdict APIs remain unchanged. | Accept this concrete consumer and root rather than require an unused public session interface. |
| General filesystem identity or freshness | Independently bound observation identity and content digest; device/inode is not a generation identifier. Local Linux overlay/tmpfs exercised; ext4 eligible but not locally qualified. | Accept this bounded storage domain. Same-UID hostile replacement, inode continuity, network filesystems, power loss and reboot persistence are not proved. |
| Main exception always wins through all cleanup | Diagnostic cleanup and the owned per_cwd scope preserve their incoming exception. Later source-lock cleanup and failures already replaced inside another scope are outside this repair. | The demonstrated cwd-boundary defect has a bounded fix and public controls below. Complete G11/T18 disposition still requires the remaining boundaries; no global guarantee is granted. |
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

The following names refer to actual collected pytest nodes, not proposed test counts. Prefixes expand to `tests/test_cross_repo_immediate_diagnostic.py::` (I), `tests/test_cross_repo_capture.py::` (C), `tests/test_cross_repo_persistent_diagnostic.py::` (P), `tests/test_advisory_diagnostics.py::` (A), `tests/test_cross_repo_diagnostic_gate.py::` (G), `tests/test_cross_repo_cleanup_priority.py::` (W) and `tests/test_publication_contention.py::` (Q). A function without a parameter suffix identifies its collected family, not a claim that each family is one case. Exact identities are in `scripts/ci_public_test_identities.json`.

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
| T17 concurrency/duplicate | A `test_same_key_always_refused_without_replacing_original`; P `test_new_host_execution_never_reuses_same_source_hash_identity`; Q `test_concurrent_distinct_execution_publications_remain_independent`, `test_concurrent_same_key_refuses_contender_without_replacing_winner`, `test_concurrent_publishers_preserve_foreign_pending_replacement` | Registered public library controls now overlap production publisher calls with deterministic commit scheduling. Same-key input is adversarial, not a natural host collision. Complete-host concurrency and full T17 admission remain unqualified; see the bounded supplement below. |
| T18 cleanup | P `test_host_publication_strictly_reads_after_temporary_cwds_are_removed`; A `test_durable_root_cannot_overlap_temporary_cwd`; W `test_pending_body_failure_survives_cleanup`, `test_partial_setup_cleans_only_registered_cwd`, `test_multiple_cleanup_failures_keep_priority`, `test_ignored_permission_error_keeps_residue_and_result` | Public native controls now distinguish joined workers, registered cwd removal, exception identity and actual residue from diagnostic staging cleanup. The bounded repair and remaining outer boundaries are described below; this is not an unconditional T18 pass. |
| T19 interruption | A `test_process_control_propagates_and_preserves_committed_record`; `test_cleanup_signal_does_not_replace_primary_signal`; P `test_output_process_control_preserves_committed_file_and_exception_identity` | Diagnostic controls preserve exact exceptions/records. No forced process-death, SIGKILL, power-loss or global-cleanup equivalence is claimed. |
| T20 output/not_run | P `test_normal_machine_return_without_axis_call_records_not_run`; `test_host_not_run_empty_success_and_unavailable_statuses`; `test_actual_strict_reader_return_controls_cli_text`; `test_persistence_failure_never_fabricates_no_target_state`; G `test_docs_only_native_ci_tracks_nonempty_delivery`; real-child MCP cases in P | The A02 supplement below distinguishes empty consumption, unavailable persistence and nonempty sink failure. Controlled not_run/MCP fixtures do not establish natural not_run reachability or SDK-stdio coverage. |

This map identifies bounded evidence and gaps. It does not convert partially covered T rows into acceptance passes. Supplemental controls must be integrated and assigned public identities before being described as repository regressions.

## A02 delivery-status evidence

The added controls address a public assertion gap in the existing implementation. No naturally reachable false-delivered defect has been demonstrated, and no production delivery code changes in this supplement.

P `test_host_not_run_empty_success_and_unavailable_statuses` covers five persistence conditions with both working and failing diagnostic-notice sinks. Its existing `persistent_cli` fixture supplies the CI verdict and omits advisory dispatch. The actual host, capture, publisher and strict reader still execute. These are consumer-boundary controls; they do not prove natural not_run reachability or invent a nonempty not_run observation.

G `test_docs_only_native_ci_tracks_nonempty_delivery` reaches the real docs-only PASS and FAIL gates with a working sink, a failing diagnostic sink, or failing diagnostic and persistence-notice sinks. It uses real Git/SQLite, the existing deterministic local backend, CLI dispatch helper, host, StateMachine CI round and advisory dispatch. Only the output callback is under the sink fault. Existing State, credit and receipt oracles remain active. This does not exercise outer CLI argument parsing, a code-mutation workload, model quality or SDK-stdio.

| Actual boundary | Publication result | Delivery result and output meaning |
| --- | --- | --- |
| Successful strict consumption of empty not_run | published | delivered; no message or sink call is required, so sink health is not established |
| Storage binding failure | not_published | not_delivered, including when the separate warning sink also fails |
| Started publisher raises an ordinary exception | unknown | not_delivered, with no successful locator; the raised call alone does not prove nonpublication |
| Published record cannot be read, including a removed file | published | not_delivered; prior publication is distinct from current readability or caller delivery |
| Nonempty no_named_targets is strictly read and its sink succeeds | published | delivered, with the existing attributed diagnostic |
| Nonempty diagnostic sink fails | published | unconfirmed; the committed record remains readable, whether or not the subsequent persistence warning is delivered |

The observers retain expected execution/repository/source/diff identity at capture construction, before publication, and compare the real strict-reader result and owned record against it. Assertions inspect publication status/locator, delivery origin, actual output attempts and persisted bytes after worker cleanup. They do not derive expected identity from file contents or replace the consumer's return.

Dedicated controls use isolated copies of the production source and these same 16 public cases. Bypassing only the host's `delivery_status == "read"` guard produces eight intended not_delivered-versus-delivered assertion failures. Forcing only the runner's delivered/unconfirmed selection to delivered produces four intended unconfirmed-versus-delivered assertion failures. Each byte-exact restoration passes all 16 cases. These deliberate omissions establish oracle discrimination; they are not failures of the unchanged baseline, and the two runs are not additive coverage.

These controls remain within the existing Linux and optional-provider fixture domains. The separate scratch PermissionError/admit-latch probe is not promoted by this supplement. G04/G09 still require a decision on the worker-owned Record/PublicationResult-to-reader consumer; the outer return remains Verdict. Complete A02, T20, G0/G1 and the original-port disposition remain separate acceptance decisions.

## Bounded T18f cwd-cleanup repair

At baseline `5e2d924`, `run_cross_repo` registered `shutil.rmtree(cwd, True)` on an ordinary `ExitStack`. A removal `SystemExit` could replace the pending host exception even after a native gate completed and the diagnostic was published. The permanent native controls fail on that baseline at exact exception-identity assertions, not at setup or import.

The private `_PerRepoCwdCleanup` delegates callbacks to the unchanged `ExitStack` algorithm, which continues remaining callbacks after a callback raises. If an exception was already escaping its body, a cleanup failure cannot replace it: returning false from the exit method preserves that original exception. If the body had no exception, cleanup failures still propagate exactly as before, including `StopIteration`; with multiple callback failures, the last failing callback in LIFO execution remains authoritative. A normal FAIL verdict and an ordinary sibling warning are not pending exceptions. No new cleanup result channel, retry, warning or exception note is introduced.

The native controls retain real Git/SQLite, CI decisions and advisory dispatch, then inject failure at the specific worker, receipt-copy, signal-replay, setup, start or root-rmdir boundary. Every started worker must be joined before cleanup. Every registered root is attempted once in LIFO order; normal roots are absent, while faulted roots are explicitly observed before test teardown. Reached publications are reread with the retained host context after unwinding. Normal receipt copies remain byte-identical. Partial setup before workers is not counted as an executed gate.

`test_cleanup_only_error_keeps_identity` protects the no-body-error contract; `test_sibling_warning_is_not_a_pending_error` protects advisory behavior. `test_ignored_permission_error_keeps_residue_and_result` faults the real `os.rmdir`: existing `ignore_errors=True` still permits residue without inventing an error or changing a normal verdict. Publisher `cleanup="complete"` continues to describe only its own staging file, not worker-directory deletion.

The all-callback attempt evidence above is limited to failures raised inside those callbacks. ExitStack can itself be interrupted outside its callback try block: a trace-timed real SIGINT control, with real rmtree callbacks, showed that one interruption before the second pop leaves the remaining registered cwd untouched in both baseline and repaired owners. The repaired owner preserves an incoming body exception; without one, it still propagates the interruption. This supplementary owner control is not a full-host signal test or a new directory-cleanup regression. No uninterrupted-unwind or universal deletion guarantee follows.

Only this cwd scope and its exception priority change. Source locks, worker completion/join, diagnostic capture close, signal deferral, receipt copying, public Verdict and #68/#400 interfaces are unchanged. The scope preserves the exception that actually reaches it; it does not reorder a recorded worker failure against earlier signal replay. Later source-lock failure, allocation that never returned a registered cwd, asynchronous interruption outside callbacks, uncatchable process death and hostile exception-object mutation remain outside this repair. Normal Linux docs-only controls do not establish whole-host concurrency, SDK-stdio, mutation-workload, recovery or all-platform qualification.

## A02/A03 delivery and consumer decisions

Successful `present(not_run)` strictly rereads an empty diagnostic tuple, so no sink invocation is required before the host marks it delivered. Reader or publication failure leaves it not_delivered; genuine nonempty diagnostic output failure is unconfirmed. No production delivery change is made here. The existing not_run test deliberately omits advisory dispatch and is controlled branch coverage, not a naturally reached input defect or a substituted nonempty consumer. A02 still needs its natural-reachability and public-control disposition.

The host constructs the independent expected identity before publication. `DiagnosticCapture.finish` retains `Record` and `PublicationResult`; the primary worker's runner/host path calls `present`, which passes the retained context to the strict reader. CLI and immediate/background MCP expose Verdict/text, not a new locator/context return. G04/G09 still require a decision on whether this actual private worker consumer satisfies the contract. Any additional private handoff must be designed and approved first; no public session interface or latest-file scan is implied.

## G01/G02/G13 admission record

| Gate | Current record | What this follow-up does not grant |
| --- | --- | --- |
| G01 | Fixed contract, four-P1 disposition and conflicting-clause/support-domain decisions are now explicit. | A maintainer's disposition of remaining conflicts or an unconditional P1-3/T18f pass. |
| G02 | Concrete closed v1 codec, bounds, actual consumers and stronger strict-decoder controls are identifiable. | Formal schema freeze, recovery of the old port or exhaustive per-check mutation coverage. |
| G13 | Independent reviews and reproducible tests are evidence for the exact changed files and fixed product source. | Formal G0/G1, retrospective approval of every older proposal, or completion of #441/#33. |

An admission decision should name the product revision and this evidence revision, accepted consistency/storage/recovery domain, treatment of the original port, exact remaining controls and T18f disposition. Record the reviewer conclusion separately from the maintainer's explicit acceptance. A merged PR or successful CI check is not that record.

#33 and #441 remain open. Drafts #443/#444 remain independent review-support artifacts until a verified replacement and explicit disposition exist.

## Bounded T17 publication-contention supplement

The [publication-contention evidence](cross-repo-diagnostics-publication-contention.md) adds six public nodes at production baseline `f4c9214a813cd5604ed63462b3268343ed6f800c`: three overlapping real-publisher cases plus three harness-failure controls. It records distinct-execution isolation, adversarial same-key non-overwrite with an unknown/no-locator loser, foreign staging preservation, and four source-specific RED/restored-GREEN controls. These library calls use controlled commit scheduling, not simultaneous complete hosts. Full T17/A06, G08/G10/G12, G04/G09 and G0/G1 decisions remain open; no production or lifecycle behavior changes.
