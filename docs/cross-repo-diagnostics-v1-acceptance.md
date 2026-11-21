# Persistent diagnostic v1: acceptance disposition

This record separates implemented behavior, directly tested behavior and remaining acceptance decisions. It is not G0/G1 approval, issue closure, a recovered or one-to-one equivalent original 78-test port, or a claim that every T01–T20 negative control has run.

## 2026-11-20 #441 limited G13 recognition

**Maintainer decision (2026-11-20 UTC):** Accept #441 within the existing
limited scope, covering only the diagnostic behavior supported by the
recorded evidence. This decision resolves the pending decision only within
that scope, not the broader obligations in the historical inventory.

- **Reviewed delivery:** [#541](https://github.com/HouMinXi/forge/pull/541)
  at `9ed42a4be978df94e5afc2905bff3cfbf8d5743c`, with independent
  applicability review bound to the [tested merge
  `d36b3c1a04b5fc52bd3395ee70c2ac5b4d6e3e3c`](https://github.com/HouMinXi/forge/commit/d36b3c1a04b5fc52bd3395ee70c2ac5b4d6e3e3c)
  against main `f4b9aabbe977df17268392a1e8a21d5b9334cefb`.
  The prior approved document blob was unchanged by the sync; the reviewed
  diff against that main added only this acceptance document's supplement.
  [Linux](https://github.com/HouMinXi/forge/actions/runs/44697828722),
  [wheel](https://github.com/HouMinXi/forge/actions/runs/44697828737),
  [lint](https://github.com/HouMinXi/forge/actions/runs/44697828800), and
  [CodeQL](https://github.com/HouMinXi/forge/actions/runs/44697828734) passed
  for that delivery revision, not for this subsequent decision record.
- **Evidence boundary:** The provider-enabled three-repository controls and
  the 32-module regression below remain evidence on the recorded Python
  3.12 revision only. The reviewed hosted Linux receipts show collection
  skips for both provider-impact test modules. They do not qualify that
  provider path on Python 3.14.
- **Unchanged limits:** Storage eligibility is not runtime qualification for
  the complete storage domain. T19 leak freedom on a real failed `close()`
  remains unverified. Equivalence to the original 78 tests remains unknown.
  This decision adds no runtime or historical G0/G1 credit, does not
  complete the broader G12 obligations, and does not merge #541 or close
  #441 or #33.

## 2026-11-19 #441 evidence and limited G12 decision

**Owner decision (2026-11-20 UTC):** Accept the regression and controls
pinned below to `a72dc5a990194494c875fac7b783436b30ed272d` as additional
limited evidence for G12. The owner confirmed this scope in the Forge project
chat. The earlier bounded G12 decision remains attached to its original evidence.

- **Fixed local source:** [`a72dc5a990194494c875fac7b783436b30ed272d`](https://github.com/HouMinXi/forge/commit/a72dc5a990194494c875fac7b783436b30ed272d),
  tree `414358023ac07e412cfd413116d2c32da4eada91`. A Git archive of the fixed
  commit was used; all 1,598 tracked blobs, including symlink contents, were
  verified after running. No separate pre-run byte scan was recorded.
- **Selected local regression:** 32 unmodified modules, **1,392 passed** on
  CPython 3.12.14, SQLite 3.53.1, pytest 9.2.0, pytest-asyncio 1.5.0,
  code-review-graph 2.4.0, MCP SDK 1.30.0 and Ruff 0.17.4. Two empty-diff
  controls stopped at toolchain detection on the initial run without Ruff.
  After adding the pinned Ruff to the test environment, both passed
  individually and in the full selected rerun without tracked input edits.
  This is not Python 3.14 qualification or a reattribution of prior runs.
- **Parent #33 direct callers:** real provider build and registration for
  synthetic repositories, followed by the real runner. At 2/2 registered
  siblings completed, only the direct caller was returned. Removing the
  indirect sibling graph retained that result at 1/2; removing the sole
  direct sibling graph instead returned no findings at 1/2, explicitly
  reporting one unavailable in both cases. Restoration recovered 2/2 and
  the direct caller. No graph-freshness or coverage outside the registered
  scope is established.
- **Separate delivery controls:** six CLI/direct-host and four MCP-handler
  fixture controls verified visible partial scope and unchanged verdict and
  receipt behavior. They did not exercise a single provider-to-CLI chain or
  the full MCP wire protocol. The independent reviewer consumed these records
  and assertions; this does not claim an independent re-execution.
- **Separate hosted checks:** [Linux](https://github.com/HouMinXi/forge/actions/runs/44515651203),
  [wheel](https://github.com/HouMinXi/forge/actions/runs/44515651290),
  [lint](https://github.com/HouMinXi/forge/actions/runs/44515651138) and
  [CodeQL](https://github.com/HouMinXi/forge/actions/runs/44515651256)
  passed for the earlier #541 head `6308f6ae855feb81912dede47f2edd569c634b74`.
  The Linux checkout was synthetic merge
  `a40c5dc1b95225ae43155ca4804b27d86a994641` into the fixed main above.
  None of those checks ran this documentation supplement.

**Exit boundary:** This decision does not complete G12 or grant G13. It does
not qualify Python 3.14. Original 78-test equivalence remains unknown; a real failed
`close()` with no leak remains unverified under T19. Preserve existing storage
eligibility versus runtime qualification, and do not extend the support domain.
G13 requires final independent review of the applicable product, test and
document revisions and explicit maintainer acceptance. Neither issue closes;
no G/T checkbox changes.

## 2026-11-18 #529 current-run handoff static applicability

Independent full-blob comparison found the following thirteen paths identical at
`5d524df377244d172f69c90c61877f9810d79337` (existing Linux run 44136528519),
`d1b1c7ac085e438c3da2f0ac537a02ce683d3afb` (recorded independent static review)
and checked main `15d19f955d468dc626a4f609539027d61e333997`:

- `src/code_forge/cli.py`: `c0a1ee2348d93c7115355a415d190f9a64e128a9`.
- `src/code_forge/cross_repo.py`: `6d03d54e0e390ba4afbe2fda2db698bd1bc218ca`.
- `src/code_forge/_diagnostic_completion.py`: `56ee88497a4b046fdb835ddc5ca02e442c8f532c`.
- `src/code_forge/_cross_repo_diagnostics.py`: `ca1aa1be4f3a49299ad73b1cacf715b5d73882b2`.
- `src/code_forge/_advisory_diagnostics.py`: `832097fa0887cfe42310b579f45d91020c0e2d93`.
- `src/code_forge/_local_filesystems.py`: `85029f10e17593f64a1c33cbf0a44e204d198690`.
- `src/code_forge/_diagnostic_history.py`: `17b8664e191068dacb7a139612edddc785514d72`.
- `tests/test_diagnostic_completion.py`: `42d33c3e2a5f48db2912bbc1a17ba82bc11ec132`.
- `tests/test_diagnostic_history_host.py`: `ca11beba547944781c6096007a0d9b8d4efd1162`.
- `tests/test_cross_repo_persistent_diagnostic.py`: `cbbc1678afd996e183f003fbfb82e9fddcb2f3fa`.
- `tests/test_advisory_diagnostics.py`: `162927b2bf277ce2b39495762dfc66581023706a`.
- `tests/test_local_filesystems.py`: `f88f94efc9ad7a1c347099fe1f45eae63fa659fe`.
- `docs/diagnostic-cli-handoff.md`: `bc1edbc67f47f3c0eba539257964fe17ee52bbd0`.

This binds the already recorded #529 CLI/host handoff review, shared reader/storage
source, selected tests/fixture and handoff contract to this main as static evidence.
There is no `src/` diff from `5d524df` to this main. The selected 40-node execution
consumed from Linux run 44136528519 remains at `5d524df`; the older independent
source/execution record and limited owner acceptance at
`d8810a212c79c62f6bce9c1b7e404e33696be8f1` retain their original attribution.
This comparison neither reconsumes their raw receipts nor reruns those tests.

Ruff/dependency declarations, installed-wheel tooling, the public identity manifest
and other test/document files changed outside this set. Blob identity is not
whole-tree or environment identity, and cancelled Linux run 44362931223 at
`15d19f955d468dc626a4f609539027d61e333997` receives no pass credit here.
The controlled fixture still supplies diff, other axes and `_run_ci`; it does not
qualify the natural full CLI chain, installed wheel or all SDK/provider paths.
Publication, capture-close attempt, sealing/event/join, caller strict reread and
notice delivery remain distinct; no real-close-failure leak-freedom or complete
storage-domain/G12/G13 qualification follows. Shared obligations stay with #441.
The existing limited #529 owner acceptance is reused, not replaced by a new
acceptance or exit decision. No support expansion or issue-state change follows.

**2026-11-19 static refresh:** the same thirteen blobs also match merged main
`a72dc5a990194494c875fac7b783436b30ed272d`. The no-`src/`-diff statement
above is specific to the earlier comparison with `15d19f9`; later main
changes files outside the listed set. The 40-node run remains attributed
to `5d524df`; no execution credit is transferred by this refresh.

## 2026-11-18 #526 scope-live applicability at merged main

Independent full-blob comparison binds the existing live registry-occurrence/counts
slice to main `a79d39f5b1224c4888eff93993387db069003426`. All seven files below
have identical blobs at `5d524df377244d172f69c90c61877f9810d79337`,
`d1b1c7ac085e438c3da2f0ac537a02ce683d3afb` and that merged main:

- `src/code_forge/cross_repo_impact.py`: `2f80c2f3c62c8dd07ccdc4165e4face85277f15d`.
- `src/code_forge/_query_scope_receipt.py`: `f3c9c98991b406ca07fecebc042481c3d0e2d26f`.
- `tests/test_cross_repo_query_summary.py`: `378a83b5f94eeee61f25985efeb165659ca355af`.
- `tests/test_scope_receipt_host.py`: `77a15bf81263dc16c2dd8eb104ee0315059ac3c2`.
- `tests/test_cross_repo_scope_snapshot.py`: `6bcf4ded639224a9f8e45e04d289b3b10a36a86e`.
- `tests/test_query_scope_receipt.py`: `da5fc544bb7c4da878575f72e773f1af0f1074e6`.
- `docs/cross-repo-query-scope-v1.md`: `0c83d11287b49215f2304eeded2e35dbd0ddeb8e`.

There is also no `src/` diff from `5d524df` to this main. This establishes static
applicability of the already recorded `d1b1c7ac` count-loop/exit review and the
selected 168-node evidence in Linux run 44136528519 at `5d524df`; it does not
reassign that execution to `a79d39f5` or create new runtime evidence. The earlier
168-node run at `d8810a212c79c62f6bce9c1b7e404e33696be8f1` and limited owner
acceptance keep their separately recorded revisions. No tests or matrix were rerun.

The scope remains live registration occurrences (duplicates included) and counts.
`query_occurrences_accounted` means completed plus unavailable equals the established
total; it is distinct from `processing_completed`, which records normal analysis
return. A helper can finish before later processing fails, as the existing
scope-snapshot assertions record. Neither flag proves complete cross-repository
impact, graph freshness, installed-wheel qualification or storage-domain qualification.
Shared reader/storage and G12/G13 obligations stay with #441, without making every
future #441 capability a new prerequisite for this limited slice. This applicability
record is not a fresh owner acceptance, final limited-exit decision or issue closure.

## 2026-11-18 #527 merged exporter controls: existing receipts consumed

After #532 merged, an independent review consumed existing
[Linux run 44275864512](https://github.com/HouMinXi/forge/actions/runs/44275864512)
at exact main `a79d39f5b1224c4888eff93993387db069003426`.
The downloaded `linux-shard-{0,1,2,3}-evidence` ZIP SHA256 values matched
GitHub's artifact digests, respectively:

- `14415701295`: `763d81b0859332f1e8d24520be0fb944c4d8dbb5df7c05e612bd757f574f2f29`.
- `14414154375`: `a6e89db165ac94f4ab7f6880fa39012ba51d364a3840a50793566779d5a9cd04`.
- `14414816972`: `22c3ebb9098819813815a051a97b4230eb320a3c90080c59a3f940acf93176dd`.
- `14414782557`: `563c630b828764cf8cc7b186698a5a60e6a9d914fa0b7ebf6cf23ddc656740fd`.

Manifest blob `172751fbd028af5e5100aec2fe477bf7bb055e09`, using the
whole-manifest enumeration in `ci_private_transport.py::_identities`, maps
`tests/test_diagnostic_history.py::test_valid_not_published_result_cannot_export_ticket`
to `public-7971` on shard 1 and the same test name in `tests/test_scope_history.py`
to `public-41875` on shard 0. Across all four receipts each node was collected
exactly once, with setup/call/teardown each passed and no skip or xfail. All four
receipts name the exact SHA above, exitstatus 0 and no collection errors.
The assertions validate a legal `not_published` result before requiring export
rejection, no locator and an empty ticket directory. This supplies the two
merged exporter controls; it does not establish all #527 acceptance obligations.
Collection skips elsewhere include cross-repo impact/integration and dead-code
modules; this is not an assertion that the whole run had no skips.

Between `d1b1c7ac085e438c3da2f0ac537a02ce683d3afb` and this main revision,
only this acceptance document, the public identity manifest and those two test
files changed. The prior fixed-source wiring review therefore remains statically
applicable; the older 171/752-node execution evidence retains its original SHA.
This note consumes an existing run: no new tests, probes or matrix were executed.

#441 continues to carry the shared reader/storage obligations: full storage-domain
qualification, leak freedom under real descriptor-close failure, and final-version
G12/G13 review. Existing reader-tail static review and the limited metadata
cap-plus-one allowance are already recorded; neither is missing, and neither is
new runtime proof. This receipt supplement makes no final limited-exit decision,
reopening/closure decision or support expansion for #527, #33 or #441.

## 2026-11-18 #526/#527/#529 entry-source reading completed

An independent static review at fixed product revision
`d1b1c7ac085e438c3da2f0ac537a02ce683d3afb` completed the following specific
source-reading coverage; it found no new blocker in these ranges:

- #526: `src/code_forge/cross_repo_impact.py:617–875` preserves registry
  occurrences (including duplicates), counts attempts before helper invocation
  and completion only after helper return, and separates unavailable queries,
  later processing failures and successful processing completion. The review
  read the matching occurrence/cache, partial-query, control-exception and
  output-failure assertions in `tests/test_cross_repo_scope_snapshot.py:19–264`
  and `tests/test_cross_repo_query_summary.py:56–287`.
- #527: `src/code_forge/cli.py:2973–3015` returns both historical branches
  before review dispatch. `src/code_forge/mcp_server.py:585–631,699–720,1848–1905`
  connects startup authorization, the SDK guard and handler-owned reads;
  `180–229,306–339` drains cancellation and connects shutdown/signal cleanup.
  Both `_mcp_diagnostic_history.py` and `_mcp_scope_history.py` retain startup
  directory authority, recheck identities and return historical public projections.
  Matching SDK, cancellation, AnyIO, shutdown and stdio assertions were read in
  `tests/test_mcp_diagnostic_history.py:145–291` and
  `tests/test_mcp_scope_history.py:154–298`. Controlled release events do not
  establish deadlines for arbitrary hangs; shutdown-order tests replace
  `cleanup_all`, and stdio uses `PYTHONPATH`, not an installed-wheel qualification.
- #529: `src/code_forge/cli.py:3296–3384,3731–3776,5156–5192` and
  `src/code_forge/cross_repo.py:263–737` connect the caller-owned completion
  slot to the real host, capture-close attempt, sealing, event, worker join and
  caller notice consumption, preserving primary-error precedence. The review
  read `tests/test_diagnostic_completion.py:19–350` and the
  `persistent_cli` fixture in `tests/test_cross_repo_persistent_diagnostic.py:90–180`.
  That fixture controls diff acquisition, other advisory axes and `_run_ci`;
  these assertions do not establish natural full-CLI qualification or prove
  leak freedom after a real descriptor-close failure.

All twelve named source/test files are byte-identical between `5d524df377244d172f69c90c61877f9810d79337`
and the fixed revision above. This is static applicability evidence only: no
product tests, probes or matrices were run for this note. The 752-node execution
record below remains attributed to `5d524df377244d172f69c90c61877f9810d79337`.
The existing shared-reader tail review and limited metadata cap-plus-one owner
decision remain recorded evidence, not new execution or fresh owner approval
from this review. This closes only the specified source-reading gaps; shared
reader/storage disposition stays with #441. Final-version binding, G12/G13 and
individual limited exit decisions remain separate; #526/#527/#529/#33/#441 are not
thereby accepted or closed, and no support domain is expanded.

## 2026-11-18 metadata overflow-probe decision

The owner answered “允许” (“allowed”) in project chat to the explicit question
whether the 4096-byte fdinfo and 1 MiB mountinfo limits permit reading one extra
byte to detect overflow, with those limits defined as accepted payload thresholds
rather than absolute physical-read ceilings. This records that limited approval;
it does not increase the accepted payload sizes.

At product revision `5d524df377244d172f69c90c61877f9810d79337`,
`_advisory_diagnostics.py::_bounded_proc` requests `cap + 1` bytes and rejects
a result longer than `cap`. Its callers use 4096 for fdinfo and 1048576 for
mountinfo. The approved probe therefore requests up to 4097 or 1048577 bytes
through the Python stream API. Buffering and kernel I/O are not given a physical
read ceiling by this decision.

The existing
`tests/test_advisory_diagnostics.py::test_proc_upper_bound_plus_one_read`
uses a real 17-byte file with a cap of 16 and requires rejection. That is a
generic overflow control, not an assertion of the stream request length or
a complete metadata-boundary qualification. No test or mutation was rerun for
this documentation decision, and no new runtime credit is claimed.

This decision supersedes the earlier pending owner clarification of the
metadata extra-byte probe in this document, including the G02 limited-decision
paragraph. Earlier dated evidence and its limitations remain historical.
Real-close-failure leak freedom, G13/final applicable-revision review and
maintainer acceptance remain separate; no support expansion, automatic retry,
issue closure, PR approval or merge follows.

## 2026-11-18 selected main execution receipts consumed

An independent receipt review consumed existing
[Linux run 44136528519](https://github.com/HouMinXi/forge/actions/runs/44136528519)
at exact main `5d524df377244d172f69c90c61877f9810d79337`.
This is evidence from that execution, not a local rerun or execution of this
later documentation head. Earlier 168/171/40-node records and owner decisions
retain their original revisions; this adds a separately attributed observation.

The four `linux-shard-{0,1,2,3}-evidence` ZIPs were downloaded and their SHA256
values independently matched the GitHub artifact digests, respectively:

- `14351919990`: `b88be885312861ed5c1bbf26867049ee42574e5f90722b6760ae3861f8944b86`.
- `14352752372`: `6856d99fd460e18163a5515621f7c4471020132deaeeaa096f2fe6266cdb8ff4`.
- `14352832815`: `023bfdfa8acf92d67352f03f362918c400f721481f1446474a790d1f42da3e3b`.
- `14353975707`: `1cbf9828e94201f24f425da5046b5d72c283fa52f4c25a63945aa4da58fce9b7`.

Each receipt names that exact SHA and its shard, has exitstatus 0 and no
collection errors. Public identities were decoded using that revision's manifest
blob `2fb0838411dc914b30f1bcaa17f742369d7a11e6` and the global enumeration in
`ci_private_transport.py::_identities`, not an index reset per module or shard.
The existing #526 four-module slice has 168 nodes, #527 five-module slice 171,
and #529 completion/history-host slice 40. In addition,
`tests/test_advisory_diagnostics.py` has 321 and
`tests/test_local_filesystems.py` has 52. These 752 nodes are non-overlapping;
each occurs exactly once in collected identities and has exactly one passed
setup, call and teardown report (2256 reports), with no target skip or xfail.
The existing entry/node maps remain authoritative; no second matrix is added.

This finite result does not cover #532's two new legal-unpublished exporter
nodes, which are absent from this main manifest. The receipts contain collection
skips for `test_cross_repo_impact_integration.py`, `test_cross_repo_impact.py`
and `test_dead_code.py`; those modules receive no pass credit here. This review
is not installed-wheel evidence, all SDK/provider-path qualification, or native
qualification of every eligible filesystem. It does not rerun mutations, extend
the earlier T06/T16 byte comparison, resolve real-close-failure leak freedom or
metadata cap-plus-one, or grant G13/final #33/#441 acceptance. No issue closes.

## 2026-11-17 shared strict-reader tail static review

The complete-tail reading gap retained in the
[#441 review](https://github.com/HouMinXi/forge/issues/441#issuecomment-6956401294)
was subsequently reviewed at fixed source
`5d524df377244d172f69c90c61877f9810d79337`: `_advisory_diagnostics.py`
[677–769](https://github.com/HouMinXi/forge/blob/5d524df377244d172f69c90c61877f9810d79337/src/code_forge/_advisory_diagnostics.py#L677),
`_read_leaf` (477–510), `_Descriptors` (396–420), and
[`_query_scope_receipt.py` 140–155](https://github.com/HouMinXi/forge/blob/5d524df377244d172f69c90c61877f9810d79337/src/code_forge/_query_scope_receipt.py#L140),
with corresponding existing assertions. No new blocking finding was identified.

Shape validation does not establish publication or ownership. The reader then
requires published status, reopens the retained binding and directory identities,
checks leaf identity, bounded bytes and digest, and applies the namespace-specific
strict decoder against expected context. Existing assertions distinguish pure
validation from real reads, replaced directories/leaves, digest-valid invalid
schema, cross-namespace rejection and foreign context. Descriptor cleanup attempts
all registered closes and preserves primary exceptions; close-after-success
injections do not prove leak freedom after an actual failed close.

This closes only that static reading gap. No tests, mutations or runtime
qualification were performed for this review; earlier execution evidence keeps
its original SHA. Real-close-failure leak freedom remains **UNVERIFIED**.
#531's eligible filesystem names are not native-filesystem qualification.
Metadata cap-plus-one disposition, G13, final applicable-revision review and
maintainer acceptance remain outstanding. No new matrix, support commitment,
whole #33/#441 acceptance or issue closure follows.

## 2026-11-17 delivery refresh after #531

The existing draft deliveries #528 and #532 were recomposed with fixed main
`8e75d8c11c16b4ce94d2756da31de464b1bb294f` by normal merges, retaining their
previous heads `b0d4a629e5d215c8e3e5a2003e9ac72745ee9493` and
`c23e903c8cad001ca3afe758f3ed497c072cbaee` as first-parent history.
This is a delivery-validation update, not another acceptance matrix.

For #532 the validated merged tree is
`6b28523f3bafad6d9c01bb3cb722ce53c4ceacaf`. Its diff against fixed main
remains two legal-unpublished exporter tests and their two public identities
(three files, 22 added lines). On Linux with CPython 3.12.14 and pytest 9.2.0:

- The diagnostic-history, scope-history and query-scope-receipt modules passed
  all 115 tests without skips.
- Advisory diagnostics and local-filesystem modules passed all 373 tests
  without skips, exercising the changed storage admission integration.
- Invocation-audit passed all 1176 tests without skips with `PYTHONPATH=src`,
  inherited by its system-Python child processes. The initial combined run
  had 1660 passes and four child-process failures because that environment
  variable was missing; a direct system-Python import confirmed the missing
  module. The corrected full audit-module run resolves those local failures.
- Manifest validation passed: exact fixed-main inventory plus the two new
  identities, and all 1664 selected collected identities match the manifest.
- Pinned Ruff 0.17.3 full-tree check/format and diff whitespace checks passed
  (1084 files already formatted). No dependencies changed.

Five-lens review found no blocking finding: scope and merge composition;
input validity and exporter rejection semantics; filesystem/trust boundaries;
test-oracle and manifest consistency; evidence provenance and claim limits.
The valid `not_published` inputs pass their independent publication validators
before real exporters reject them, with no locator or ticket produced.
No production behavior is changed by #532 relative to fixed main.

For #528, the merge preserved the complete prior acceptance document verbatim;
only this refresh record is added. Historical evidence and the #526/#527/#529
limited decisions below retain their original attribution. The original
168/171/40-node evidence remains bound to
`d8810a212c79c62f6bce9c1b7e404e33696be8f1`.
The four-file T06/T16 full-byte comparison below applies only between
`f21f2cef6c0907778e843a2289cff9ddfcc0b0e4` and
`5a83797118f96260205942aa72fbad9e60f8510a`; it does not extend to new main.
In particular, #531 changed the advisory storage source and tests. No historical
experiment was rerun or reassigned to this refresh.

This local selected verification is not a full-suite, installed-wheel, hosted
Linux CI or CodeQL result. New-head hosted checks must be evaluated separately;
prior-head green checks do not qualify the new heads. Both PRs remain drafts.
G13, metadata cap-plus-one disposition, and whole #33/#441 acceptance remain
unresolved. No issue closure, PR merge, support expansion or new acceptance
is implied.

## 2026-11-17 bounded CLI handoff evidence and limited owner acceptance for #529

Related to [#529](https://github.com/HouMinXi/forge/issues/529), parent #33
and shared evidence #441. This records acceptance of the existing current-run
CLI diagnostic-ticket opt-in, separate from the #527 historical readers.

The fixed reviewed product/test revision is
`d8810a212c79c62f6bce9c1b7e404e33696be8f1`.
The prior independent source review and CI evidence review recorded 21 nodes in
`tests/test_diagnostic_completion.py` and 19 in
`tests/test_diagnostic_history_host.py` from
[Linux run 43906002270](https://github.com/HouMinXi/forge/actions/runs/43906002270).
All 40 exact manifest nodes passed setup, call and teardown without skips;
collection and aggregate workflow green alone were not the acceptance evidence.
The recorded environment was CPython 3.14.10 on Ubuntu 26.04, editable source.
This supplement consumes that prior recorded review, not a new execution or a
fresh download/revalidation of the raw execution receipts. The original decision
and its 40-node review summary were rechecked for this documentation delivery;
raw phase receipts and environment logs were not reconsumed here. The existing
node maps remain authoritative; this adds no duplicate acceptance matrix.

The [handoff contract](diagnostic-cli-handoff.md) retains the owner-held original
`Attempt(expected, publication)` tuple. The worker exports at its existing
presentation point, then seals the original contexts and publication results
after capture close is attempted and before the completion event is set.
Capture-cleanup failure is recorded separately. After the host returns or raises,
the opt-in CLI caller strictly rereads the original observation using that tuple
before emitting ticket notices. Publication, capture cleanup, worker completion,
strict reread and caller delivery remain distinct facts. Unknown publication
creates no successful locator; worker completion is not total CLI success.

The foreign-ticket replacement control proves that the current expected tuple
is not rebound from the ticket. It does not prove detection or rejection of disk
ticket replacement. Observation-artifact deletion is a separate strict-reread
control; it must not be relabelled as deletion of the exported ticket itself.
Tickets remain caller-trusted selectors, not same-UID authentication. The
controlled fixtures do not qualify an installed wheel, the full natural CLI
flow, all SDK/provider paths, or hard I/O deadlines. Default CLI/MCP and direct
host callers without a completion slot retain their existing contracts.

On 2026-11-17 the owner explicitly accepted this limited current-run handoff
after the independent source/CI review and these limits were presented in the
Forge project chat. There is no public decision permalink; private message
identifiers are not published. This later documentation revision does not
reassign execution evidence to current main. #529, #33 and #441 remain open;
no issue closure, whole G12/G13 qualification, support expansion or automatic
retry authority follows.

## 2026-11-17 bounded T06/T16 static applicability review

The two T06 and three T16 negative-control executions recorded below remain
bound to `f21f2cef6c0907778e843a2289cff9ddfcc0b0e4`.
The independent applicability review compared four complete source/test files
with main `5a83797118f96260205942aa72fbad9e60f8510a` and found byte-identical
contents. This documentation delivery independently repeated that full-byte
comparison and confirmed these Git blob identities:

- `src/code_forge/_advisory_diagnostics.py`:
  `5c140f1d1266c3d7b50d649f392dc06cfb9017d2`.
- `src/code_forge/cross_repo_impact.py`:
  `2f80c2f3c62c8dd07ccdc4165e4face85277f15d`.
- `tests/test_advisory_diagnostics.py`:
  `c6869a1c7a7e90749c210e093b760ec734767790`.
- `tests/test_cross_repo_impact.py`:
  `7ba58b8494977d563ac288c28c7458deec82e4d1`.

T16's `schema_version=True`, `axis="other"` and repository `future=None`
controls still assert rejection through the real decoder. T06's final-cap
removal and extra-hop controls retain their targeted assertions: the output
count equals the existing cap, and the direct-caller fixture has no infrastructure
errors and exactly the expected direct finding. The exact selectors, mutations,
original execution outcomes and retained log hashes remain in the dated records
below; no new mutation or pytest execution is claimed here.

This closes only the concern that these exact source/test targets drifted
between the two named revisions. It does not establish whole-repository,
dependency or environment equivalence, a current-main rerun, exhaustive T06/T16
coverage, overall G12/G13 qualification, or a physical I/O cap. Metadata
cap-plus-one disposition and the other retained acceptance obligations remain
separate. No gate or issue status is changed.

## 2026-11-17 bounded history-reader evidence and limited owner acceptance for #527

Related to [#527](https://github.com/HouMinXi/forge/issues/527), parent #33
and shared evidence #441. This records a limited decision on the existing
CLI/MCP diagnostic and scope history readers, not a new interface or support domain.

The reviewed product/test revision is
`d8810a212c79c62f6bce9c1b7e404e33696be8f1`.
The prior independent review directly consumed execution receipts from
[Linux run 43906002270](https://github.com/HouMinXi/forge/actions/runs/43906002270)
and checked the 171 selected nodes in five history-reader modules against
identity manifest blob `4d72c843bf1bc5cbd3a0c57abacb8ad149cdbc02`.
The recorded nodes executed successfully without skips. This records that
existing CI evidence review, not a new local run, and does not reassign the
result to this later documentation revision.

The existing entry table and the G04/G09 historical-entry node map remain
authoritative. This evidence includes fresh CLI reads, the tested real MCP
stdio restart paths, independent history configuration, and controlled
cancellation/shutdown drain behavior. The tested history transport is not
qualification of all live review SDK/provider paths.

On 2026-11-17 the owner explicitly accepted this bounded historical-read
result after the limits were presented in the Forge project chat. There is
no public decision permalink; private message identifiers are not published.

Tickets and basenames remain caller-trusted selectors, not signatures,
same-UID authentication or independent source identity. Cancellation/drain
evidence does not establish a hard I/O deadline. This decision grants no
all-storage-domain or all-SDK qualification, source/graph freshness,
current-run credit, review recovery or automatic retry authority.

The selected execution-evidence gap and this limited owner decision are
recorded, without promoting whole G/T gates. G13 final acceptance,
metadata cap-plus-one disposition and other retained obligations remain
separate. #33 and #441 remain open; no issue closure is authorized.


## 2026-11-16 scope-live evidence and limited owner acceptance for #526

Related: [#526](https://github.com/HouMinXi/forge/issues/526), parent #33 and
shared evidence #441. This reviews existing behavior and records its limited acceptance; it is not
implementation, support-domain expansion or final acceptance of #33 or #441.

The recorded scope-live decision remains bound to product/test
`2b2ac9fc18285f78acf5503ded830d256b3a99ff` and [#507](https://github.com/HouMinXi/forge/pull/507).
Direct Git blob comparison with `d8810a212c79c62f6bce9c1b7e404e33696be8f1`
found identical bytes for `src/code_forge/cross_repo_impact.py`,
`src/code_forge/_advisory_diagnostics.py`, `src/code_forge/_query_scope_receipt.py`,
the four test modules below and `docs/cross-repo-query-scope-v1.md`.
This is scoped source identity, not whole-repository or environment equivalence.

At that exact later revision, [Linux run 43906002270](https://github.com/HouMinXi/forge/actions/runs/43906002270)
completed successfully. Its published execution receipts were directly read,
not inferred from collection or aggregate green. Public IDs were resolved using
that revision's `scripts/ci_public_test_identities.json` blob
`4d72c843bf1bc5cbd3a0c57abacb8ad149cdbc02` and the actual
`ci_private_transport.py::_identities` mapping:

- `tests/test_cross_repo_query_summary.py`: 39 nodes, shard 0,
  artifact `14246711664` (`linux-shard-0-evidence`).
- `tests/test_scope_receipt_host.py`: 43 nodes, shard 1,
  artifact `14246941253` (`linux-shard-1-evidence`).
- `tests/test_cross_repo_scope_snapshot.py`: 35 nodes, and
  `tests/test_query_scope_receipt.py`: 51 nodes, shard 2,
  artifact `14245553883` (`linux-shard-2-evidence`).

All 168 manifest nodes in these four modules appeared in collected identities and had passed setup,
call and teardown reports, with no missing target node, target skip, xfail or error.
Each receipt named the exact revision above and exit status 0. This includes all
four `test_actual_cli_mcp_child_transports_query_summary` parameterizations
(PASS/FAIL × immediate/background), and all four
`test_named_target_summary_preserves_natural_gate_and_receipt_bytes`
parameterizations (False/True × PASS/FAIL). These eight are included in the 39,
not additional tests. This review consumed existing CI; it did not rerun tests.

This supports the existing bounded distinctions: registration occurrences including
duplicates, unknown/not-reached versus established empty, successful zero-caller
queries, partial failures, independent scope receipt consumption and the tested
text/CLI-child transports. Completed queries are not overall analysis success or
complete impact coverage. Missing registry retains existing empty-scope discovery
semantics; absence of a receipt remains unknown. Historical retrieval, whole live
SDK/provider recovery, graph freshness, multi-hop impact and final G12/G13
acceptance are not established by these 168 controls. Earlier execution and
negative-control records retain their original revisions.

The execution-evidence gap for this selected slice at the fixed reviewed revision
is resolved.
On 2026-11-16, the owner explicitly accepted the existing registered-scope live
summary and counts after the stated limits were presented in the Forge project
chat. Counts include repeated registrations; unknown is not zero, and completed
queries do not establish whole-repository coverage or overall analysis success.
This acceptance excludes multi-hop impact and all-real-SDK/provider qualification.
The reviewed product/test revision remains the exact revision above; this later
decision record does not reassign execution evidence to a new source revision.
There is no public decision permalink; no private message identifier is exposed.

This records the limited acceptance decision for this selected #526 slice, not the
parent roadmap or G13 final acceptance. #33 and #441 remain open. No automatic
issue closure, broader support, metadata cap-plus-one approval, or disposition
of historical #443/#444 candidates is authorized by this decision.

## 2026-11-16 T16 three selector-specific negative controls

Executed revision remains `f21f2cef6c0907778e843a2289cff9ddfcc0b0e4`,
tree `389afe7c10c28ca65c2f1715777024c0ae18e08b`. Each experiment used a
separate exact archive copy and the existing test, with CPython 3.14.10,
pytest 9.2.0 and no added plugin or filesystem-profile substitute. Each phase ran
`PYTHONPATH=src python -m pytest -q <node>` in a fresh process.

- **Version exact-int:**
  `tests/test_advisory_diagnostics.py::test_schema_wrong_types_versions_enums_combinations[schema_version-True]`.
  Only remove `type(data["schema_version"]) is not int or` from the decoder;
  retain version-value and axis checks. JSON `true` then passes the remaining
  `!= 1` comparison. This tests boolean rejection, not every version/type input.
- **Fixed axis:**
  `tests/test_advisory_diagnostics.py::test_schema_wrong_types_versions_enums_combinations[axis-other]`.
  Only remove `or data["axis"] != _AXIS`; retain both version checks. The
  independent literal `"other"` is then accepted by the decoder. This covers
  that fixed-value omission, not all axis encodings or other fields.
- **Repository closed keys:**
  `tests/test_advisory_diagnostics.py::test_extra_fields_rejected_at_each_object_level[repository]`.
  Only remove `or binding.keys() != {"kind", "id"}`; retain binding type,
  kind and id/context checks. The existing fixture adds only `future=None`.
  This tests rejection of an extra key, not missing keys or binding type/kind.

For each: baseline **1 PASS / exit 0**, mutant **1 semantic failure / exit 1**
(`DID NOT RAISE DiagnosticError` at the existing rejection assertion), and
byte-restored **1 PASS / exit 0**. No tests or fixtures changed. Actual mutant
source, diff and hashes were saved before each mutant run; each 1573-regular-file
manifest identified only `_advisory_diagnostics.py` as changed. Independent
review verified the logs and restored contents of all 1575 Git blobs, including
the two symlink targets, against the fixed revision. Symlinks were checked at
restoration, not independently snapshotted during each mutation.

Retained log SHA256 values, in baseline / mutant / restored order:

- Version exact-int: `fa8e3e1f0a4b084326a7027307e5cc3a94f799f9733ea598c3b878fa3c70f37c` /
  `29f22e80ba78543212104379bf6bd06dfcc33170cf39536d3c5fe8ab3c5d9529` /
  `99a24796443a1d07f66a424f8116a06a2079063af758e7d3c87801c191ad2ecd`.

- Fixed axis: `76af71df60bb5f93ea635e8763126ecf98e837672c9980e80aefde989b203765` /
  `fc6049d5eb18d615437ed23b4ffa3648f0e589ef11710cf5d22de38ac7b4392b` /
  `e8c2ebdecc6c68bd31c948de0b0c1dd4c57dd47d093194a6c1d241c65727824d`.

- Repository closed keys: `fa8e3e1f0a4b084326a7027307e5cc3a94f799f9733ea598c3b878fa3c70f37c` /
  `60692175e3fe24a0bd6387ff75705c8643c345addf3bbf174c08b860886f276c` /
  `e8c2ebdecc6c68bd31c948de0b0c1dd4c57dd47d093194a6c1d241c65727824d`.

The hashes identify retained evidence, not public downloadable artifacts. These
three specific omissions are no longer NOT_RUN at this revision; earlier dated
statuses remain historical. Other validators and input variants retain their
separate status. No exhaustive T16 qualification, original78 equivalence, new
support domain, G12/G13 acceptance or issue closure follows.

## 2026-11-16 T06 two bounded negative controls

Executed product/test revision: `f21f2cef6c0907778e843a2289cff9ddfcc0b0e4`,
tree `389afe7c10c28ca65c2f1715777024c0ae18e08b`. These isolated source-tree
experiments reuse existing tests, not new tests or a replacement catalogue.
Environment: cloud Linux, CPython 3.14.10, SQLite 3.53.1, pytest 9.2.0;
no filesystem-profile substitute. Each phase used a fresh pytest process:
`PYTHONPATH=src python -m pytest -q <node>`.

- **Final output cap removal:** node
  `tests/test_cross_repo_impact.py::TestTopNCap::test_cap_at_top_n`.
  The fixture creates 15 real SQLite callers. The sole mutation changes
  `enumerate(hits[:_TOP_N])` to `enumerate(hits)` in `cross_repo_impact.py`.
  Independent replay: baseline 1 PASS, mutant assertion RED (`15 != 10`),
  byte-restored 1 PASS; exit codes 0/1/0. This discriminates removal of the final
  output slice, not every cap implementation or distance-selection property.
- **One extra caller hop:** node
  `tests/test_cross_repo_impact.py::test_direct_caller_chain_excludes_second_hop`.
  After the original caller query, the sole inserted block traverses the fixed
  `tuple(live)` of direct callers once more through unchanged `_live_callers`
  and source resolution. In this fixture, `consumer::direct` supplies
  module/name `consumer`/`direct`; the real query returns indirect caller C.
  SQL filters, fixtures and test assertions remain unchanged. Baseline 1 PASS,
  mutant assertion RED (extra `chain:src/lib/indirect.py` at `(1, 1)` after
  `infra_errors == []` succeeds), byte-restored 1 PASS; exit codes 0/1/0.
  This is one extra traversal, not recursive expansion or arbitrary name grammar.

Actual mutant snapshots, diffs and hashes were saved before execution. The hop
experiment's phase manifests cover 1573 regular files and identify only the
product source change; the two symlinks were checked separately after restoration.
Independent review verified restored contents of all 1575 Git blobs, including
symlink targets, against the executed revision. Neither test was modified.

Retained log SHA256 values, in baseline / mutant / restored order:

- Cap: `3be6ffcaecf85b5bd3eef3693ab214b0e7c9eb4e36d117fcf1825652de5eb298` /
  `4b88cba931e069da5d6ba626f4b808a88474cac49cd76f8945b84f895b2f160d` /
  `e8c2ebdecc6c68bd31c948de0b0c1dd4c57dd47d093194a6c1d241c65727824d`.
- Hop: `3be6ffcaecf85b5bd3eef3693ab214b0e7c9eb4e36d117fcf1825652de5eb298` /
  `2a57e922d72d5d8b6ebe238e96809d9f60096a4e97d7993cd87e56a4a81570ad` /
  `e8c2ebdecc6c68bd31c948de0b0c1dd4c57dd47d093194a6c1d241c65727824d`.

These are retained local logs, not published downloadable artifacts. The two
specific controls are no longer NOT_RUN at this revision; earlier dated statuses
remain historical. This supplies no exhaustive mutation score, T16 validator
coverage, original78 equivalence, installed-wheel qualification, broader support
or final G12/G13 acceptance. No whole G/T gate or issue status changes.

## 2026-11-16 post-#519 selected regression and coverage boundary

Fixed product/test/prior-document revision:
`5d21ad778db31bb65050e3f4a0a9339277ff5bd7` ([#519](https://github.com/HouMinXi/forge/pull/519));
tree `5eaf169b74c56853437cb040d6ee911a6ada2666`. All 1575 tracked
blobs, including symlink contents, matched that Git tree before and after the
completed run. This later documentation supplement is not part of the executed
tree and does not reassign earlier evidence to the new revision.

The same 32-module command listed in the 2026-11-15 R4 record below completed
with **1390 passed, no skips, in 35.88s** (exit 0), without a filesystem-profile
substitute. Cloud Linux environment: CPython 3.14.10, SQLite 3.53.1,
code-review-graph 2.4.0, pytest 9.2.0, pytest-asyncio 1.5.0, MCP 1.30.0,
AnyIO 4.16.0 and Ruff 0.17.3. Dependency consistency checking passed.
The initial environment lacked the optional graph provider and produced
1330 passes plus two module skips; that incomplete run is not the final result.
Installing the provider while retaining MCP 1.30.0 allowed the complete selection.
Ruff check/format-check passed for the same three files named in the earlier R4
record. Retained executor log SHA256:
`edae2a092d76a613769c134448b8aadfe17df25f77ea9042ac274ce646db0703`.
The log is not a published downloadable artifact; its digest identifies retained
evidence, not independent access to its contents.

**G08/G11 boundary:** #519 repairs the invocation-audit recorder's failed-release
identity retention using bounded directory references. Its 64-slot, loss-of-trust,
settlement and fork behavior is documented in
[configuration](configuration.md#forge_scoped_fault_domain).
The diagnostic storage path instead uses `StorageBinding` and publication/reading
in `_advisory_diagnostics.py`, consumed by `_cross_repo_diagnostics.py`.
The audit pin registry is not a replacement for that storage implementation.
Consequently this repair grants no new T15/T18d generation-continuity or filesystem
qualification, no complete T17 host concurrency, and no blanket T18f/T19 cleanup,
real-close-failure leak-freedom or interruption guarantee. Existing support and
exclusions remain unchanged. Audit-specific tests and CI retain their own scope.

**G12/G13 boundary:** the new result supplies current-revision selected source-tree
regression evidence, not installed-wheel evidence, new omission RED credit,
original78 equivalence, or acceptance of every retained obligation. The earlier
limited G12 decision remains limited. T06/T16 outstanding controls, metadata
cap-plus-one clarification, historical candidate disposition and explicit final
maintainer acceptance are not resolved by this run or by merging #519.
No G/T row is promoted to PASS by this supplement; #33 and #441 remain open.

## 2026-11-15 owner decisions: limited G12 evidence and remaining G02 limits

Decision source: the owner in the Forge project chat explicitly accepted the
two stated recommendations on 2026-11-15. The executor read the original question
and affirmative reply together. There is no public decision permalink; this
record does not invent one or expose private message identifiers.

Applicable product/source and prior document:
`f7e2cffbc7379962b62e3be7169643c70ea7ab02`, including the
[#515 evidence record](https://github.com/HouMinXi/forge/pull/515).
The 1390-test run recorded below remains bound to
`1fca1f370f4505788fa65de547af4eb15021efc8`; accepting that evidence does not
relabel it as a run on the later source or this documentation revision.

**G12 limited decision:** accept the existing result assertions and the
same-revision review recorded by #515 for this finite slice. T06's dedicated
cap-removal/extra-hop mutations and T16's per-validator independent negative
controls remain incomplete, are not PASS and receive no corresponding R3
credit. Original78 recovery/equivalence is not claimed. This is not blanket
completion of every G12 obligation or approval of unlisted support domains.

**G02 limited decision:** accept these existing implementation limits:
- Captured/history single strings: at most 4096 characters **and** 4096 strict
  UTF-8 bytes.
- One active-cache error view: at most 258 items, each at most 4096 characters,
  with at most 65536 strict UTF-8 bytes in total. The per-item limit here is
  characters, not a separate 4096-byte cap.
- Excluded temporary roots: at most 256.
- Storage source paths: at most 64 parts including the absolute root anchor,
  4095 UTF-8 bytes overall and 255 bytes per component.
- The metadata proposal quoted fdinfo at 4096 bytes and mountinfo at 1 MiB.
  Source-review qualification: these are the implementation's accepted payload
  thresholds. `_bounded_proc` requests `cap + 1` bytes to detect and reject
  overflow. The earlier proposal's wording as a literal read ceiling was
  imprecise; the extra-byte probe interpretation remains to be clarified with
  the owner. This record does not infer approval of a larger physical-read cap.

The non-metadata limits match the fixed-source
[capture checks](https://github.com/HouMinXi/forge/blob/f7e2cffbc7379962b62e3be7169643c70ea7ab02/src/code_forge/_cross_repo_diagnostics.py#L311-L317),
[cache view checks](https://github.com/HouMinXi/forge/blob/f7e2cffbc7379962b62e3be7169643c70ea7ab02/src/code_forge/cross_repo_impact.py#L605-L615)
and existing bounded storage contract. The decision promises neither a global
memory/disk budget nor independent negative-control qualification of every
boundary. It adds no implementation change or new execution evidence.

This dated decision supersedes only the pending-decision wording for these
specific G12/G02 recommendations in the earlier sections below. Historical
evidence, unexecuted controls and other exclusions retain their stated status.
G13 final maintainer acceptance is still pending; permission to perform its
review is not that acceptance. #443/#444 historical disposition is separate
and remains unapproved. Related to #33 and #441; both remain open.

## 2026-11-15 same-revision R4 evidence, pending acceptance

Fixed product/test/prior-document commit:
`1fca1f370f4505788fa65de547af4eb15021efc8` ([#513](https://github.com/HouMinXi/forge/pull/513));
tree `37adb1ef66a3a7b38496aad2741ad70afb958000`. All 1574 tracked
Git blobs, including symlink contents, matched the remote tree before and after
this run. This supplement is a subsequent documentation-only revision; it does
not retroactively make its own text part of the executed source tree.

Cloud Linux source-tree environment: Python 3.14.0, SQLite 3.50.4, pytest 9.2.0,
pytest-asyncio 1.5.0, MCP 1.30.0, AnyIO 4.16.0 and Ruff 0.17.3.

```sh
PYTHONPATH=src python -m pytest -q \
  tests/test_cross_repo*.py tests/test_diagnostic*.py tests/test_scope_history*.py \
  tests/test_json_diagnostic_contract.py tests/test_mcp_diagnostic_history.py \
  tests/test_mcp_scope_history.py tests/test_native_diagnostics.py \
  tests/test_public_diagnostic_summary.py tests/test_publication_contention.py \
  tests/test_query_scope_receipt.py tests/test_scope_receipt_host.py \
  tests/test_advisory_diagnostics.py
```

Result: **1390 passed in 44.87s**, 32 selected modules, no tracked-source changes.
Retained local log SHA256:
`d6b97aaf8a3a8753222918bca0dcdfccf4d633b9ee0954c6f2d505151a0e0d4d`.
The log is retained by the executor, not a published downloadable artifact;
this digest identifies it but does not independently provide its contents.
Ruff check and format-check passed for
`tests/test_cross_repo_persistent_diagnostic.py`,
`tests/test_mcp_diagnostic_history.py` and `tests/test_mcp_scope_history.py`.
This is source-tree regression evidence, not a new installed-wheel run or a
replay of every historical negative control.

An independent read-only review checked the exact-tree acceptance/contract,
cross-repository and capture implementation, persistent test and CI identity
manifest. It consumed the completed run log and found no new production repair
blocking this limited slice. It did not grant G12/G13 or blanket source approval.

The #513 control adds only the window after successful active capture creation
at the primary machine constructor entry, before the original constructor body
runs: RuntimeError,
KeyboardInterrupt and SystemExit preserve the original exception and establish
capture closing/inactivation, no observation/delivery and worker/cwd cleanup.
Its earlier targeted close-omission RED remains evidence at its original
test-only revision, not a new mutation executed in this run. Constructor-internal
partial side effects, capture-construction failure, real failed-close leak
freedom and every lifecycle timing are not established.

### Remaining decisions, not another experiment inventory

- **G12/R3:** the same-revision selected regression is now available. T06's
  dedicated cap-removal/extra-hop omissions and T16's per-validator independent
  discrimination are not thereby PASS. Recommend deciding whether existing
  result-based controls suffice for the finite slice, with those unexecuted
  controls retained as limitations; no such acceptance or waiver is made here.
- **G13/A08:** after consuming this record, the maintainer must explicitly decide
  finite acceptance. Independent review, CI and PR delivery do not substitute
  for that decision. Existing scope exclusions remain unchanged.
- **G02:** unlisted active-cache, individual string, excluded-root/path and
  metadata-read limits in the subsequent limited G01/G02 freeze section below still require explicit
  disposition. Their implemented values do not themselves grant contract
  approval or complete numerical-boundary qualification.
- **#443/#444:** #444's identical test blob is already in #443; the necessary
  constructor-entry cleanup assertion identified in the candidate review is now
  delivered by #513. Recommend historical/superseded treatment rather than
  wholesale candidate integration. This is not authority to change either PR's
  state and is not a claim of complete original-candidate equivalence.

The accepted T12/T14/T19/T20 dispositions and original78 UNKNOWN boundary remain;
they are not newly reopened implementation tasks. Related to #33 and #441, both
remaining open. No parent-roadmap completion, support-domain expansion or
G12/G13 approval is recorded.

## 2026-11-14 owner decisions (limited scope)

Decision-maker: the owner/user in the Forge project chat. The owner replied
“可以” to three explicit recommendations: the bounded existing entry contracts,
the T14 unsupported/not-applicable disposition, and no promise of stable ordering
among equal-ranked results. Source is that project-chat approval, relayed to the
executor; there is no GitHub decision permalink. This document records the
approval without inventing a public source URL or publishing private chat metadata.

Applicability: product/test and prior acceptance document at
`2b2ac9fc18285f78acf5503ded830d256b3a99ff`; prior document blob
`e3d8660b450f75d9b5a056c4d0379db84398faba`. The resulting documentation commit
records these decisions; it does not reassign historical test evidence to this
product revision. No new execution or negative control accompanied this record.

Accepted entry semantics: default CLI/MCP provide the existing text contract;
CLI diagnostic-ticket opt-in retains its existing caller handoff and original-tuple
reread; historical readers provide historical information without current-run
credit; direct-host without a completion slot promises no caller reread.
This is not blanket acceptance of every detail in the proposed entry table:
whole live SDK/recovery qualification and unapproved cancellation/support
limits remain separately pending; the later bounded storage decision below is separate. Scope-live counts received the separate
limited decision recorded next.

A subsequent explicit owner reply “可以” in the same project chat accepted
**scope-live counts as registered and queried range counts, not complete coverage
or proof of actual execution impact**. This is a separate limited approval, at
the same applicable product/test revision above and prior decision-document head
`92bdf96e2917e59813ebe6946622135da4caee2e`. No public decision URL exists.
The existing counter meanings remain registration occurrences (including duplicate
registrations), not unique repositories or SQL statements. Missing observation is
unknown, not zero; a completed query is not successful overall analysis. The
source/contract basis is `_emit_scope_summary` → `DiagnosticCapture.present_scope`
and [query-scope v1](cross-repo-query-scope-v1.md). This decision does not accept
storage/cancellation domains, original78 replacement or final acceptance, and
supplies no new runtime PASS or scope protocol-freeze approval.

A further explicit owner reply “可以” in the same project chat accepted only
the **G10 publication-result three-state semantics**: a verified commit grants
a locator (`published`); known no commit grants none (`not_published`); uncertain
commit remains `unknown`, grants no success locator, and must not trigger an
automatic retry or fallback to an older record. Decision date: 2026-11-14;
applicable product/test revision remains `2b2ac9fc18285f78acf5503ded830d256b3a99ff`,
with prior decision-document head `942b3575222506b96cf9548e0ef12a8ccfd628d5`.
The decision source is the owner's specific project-chat approval, not a
GitHub comment; no public decision permalink exists.

This accepts the bounded meaning described in
[publication facts](cross-repo-diagnostics-v1.md#storage-support-and-publication-facts),
not the whole storage/concurrency/cleanup domain or complete G10 acceptance.
The existing dated UNKNOWN probe and foreign-final control retain their original
fixed-version evidence and limits; neither is a fresh execution or qualification
of the applicable decision revision. Unexecuted G10 controls remain NOT_RUN.
The subsequent bounded G08/G11 support and catalogue-organization decisions
below are separate; G12/G13 remain pending.

The owner subsequently replied “可以” to the **bounded storage-support package**
in project chat on 2026-11-14. Applicable product/test revision is
`2b2ac9fc18285f78acf5503ded830d256b3a99ff`; prior decision-document head is
`4b52ee6e3f5f508db1b7cfa31e0cd146f62099df`. This is the actual decision source;
there is no public decision permalink.

Accepted scope is local Linux private directories under the existing ownership,
permission, no-follow and ACL admission rules. Overlay/tmpfs claims extend only
to the already recorded configurations, not all mounts of those types. Ext4 is
implementation-eligible but **not qualified by this round**. Existing final names
are neither overwritten nor adopted; strict reading checks independent expected
identity and content, with the previously accepted publication three-state
semantics. Concurrency support here is only the recorded library-level publisher
isolation, not complete concurrent hosts or shared captures. Cleanup is limited
to owned staging and registered cwd cleanup; permission failures may leave residue.
Publication, staging cleanup, cwd deletion, worker completion and caller delivery
remain separate facts.

The accepted exclusions are network/FUSE storage, hostile same-UID interference,
power-loss durability, global atomicity, hard I/O deadlines and universal automatic
cleanup. This does not add inode-generation continuity, cross-restart authority,
unregistered-allocation recovery or whole-host lifecycle guarantees. Existing
source/contract limits remain; no additional platform or recovery feature is approved.

Evidence precision is retained: the publication-contention report is historical
`f4c9214a813cd5604ed63462b3268343ed6f800c` / Python 3.12.14 / overlayfs library
evidence, not final Python 3.14 qualification. The `7ca557a2` Python 3.14.0 tmpfs
UNKNOWN probe covers its named branch only. Complete mount-option/kernel
configuration snapshots are absent from the reviewed records. Selected close
controls close the real FD before raising and contain no live-FD-count oracle;
**no-leak behavior after a real close failure remains unproved**, with any further
FD validation separately undecided. This records a limited support contract, not
complete G08/G10/G11 runtime acceptance, original78 replacement or final G12/G13.

T14: cross-repository HOLD resumption is **UNSUPPORTED**; single-repository HOLD
has no diagnostic axis and that qualification is **NOT APPLICABLE**. Neither is
PASS. New-host identity remains a distinct T10 obligation.

T06: stable ordering among equal-ranked results is **NOT PROMISED**. This removes
that proposed ordering obligation, not the existing top-N or direct-caller
requirements; single-hop/extra-hop evidence remains separately unresolved.

The owner then replied “我觉得可以，整体进度你需要把握” to using the existing
versioned **G/T clause catalogue to organize subsequent acceptance**, retaining
unfinished obligations and the accepted T14 disposition while original78 bytes,
node identities and one-to-one equivalence remain UNKNOWN. Decision date:
2026-11-14; applicable product/test revision remains
`2b2ac9fc18285f78acf5503ded830d256b3a99ff`, with prior catalogue/document head
`f96667b9ef7043fd432cc29fdbee7cbdfffddeac`. Source is the specific project-chat
decision; its limited scope also has a
[public record](https://github.com/HouMinXi/forge/pull/507#issuecomment-6882175331).
That record does not retroactively supply public approval for every earlier
private decision.

Unavailable original78 bytes are no longer a prerequisite to progress. This is
an organizational replacement, not recovered originals, one-to-one equivalence,
complete coverage, catalogue-wide PASS or automatic acceptance of future
revisions. Subsequent catalogue edits must identify their version and net
difference. Existing evidence keeps its own fixed revision. All unfinished
obligations remain, including top-N/single-hop/extra-hop, fork/shared-use,
real-close-failure FD evidence, natural not_run, remaining G01/G02 qualification and final same-revision
Python 3.14 regressions and independent review. Stable tie ordering is not
promised; T14 remains UNSUPPORTED/NOT APPLICABLE, not PASS or a substitute for T10.

The read-only candidate comparison found the worker test at #443 head
`9b3c7dae4598a2c225e1e568da45964599e1d119` and #444 head
`e184e7fea5187aa45daa55bffa63bbd413ac0b75` has identical blob
`ec3cb5306a671cc9897f0c197ce8fffb635c8297`. #444 has no unique test-file delta
relative to #443. #443's review-support candidates are not an equivalent
production implementation and are not to be merged wholesale. Both PR states
remain unchanged; suggested superseded/historical dispositions are not closure
authorization. Any later necessary unique assertion needs its own narrow task.

All other decisions retain their prior status except the later limited G01/G02 decision below, including
other support domains, qualification of the retained catalogue obligations, and final G12/G13.
Original78 remains UNKNOWN. Related to #33 and #441; both remain open.

### Subsequent limited G01/G02 freeze (2026-11-14)

The owner replied “可以” in the Forge project chat to the specific G01/G02
package recorded here, as relayed to the executor. No public decision permalink
exists. Applicable inspected product/test revision:
`6740c44f44211e55d72ac947185d7f8434ea2e67`; prior decision-document head:
`a5440ac3782c998b94d42843f2f7a6f02eb05844`. The prior bounded contract blob is
`602763e37bd831ba17241983878ec58461193a39`; codec and capture source blobs are
`5c140f1d1266c3d7b50d649f392dc06cfb9017d2` and
`ca1aa1be4f3a49299ad73b1cacf715b5d73882b2`. This is a documentation decision,
not new execution or reassignment of earlier evidence to this revision.

**G01 accepted semantics:** current stage-effective capture at actual first use,
reuse of already consumed values and rediscovery by a new host. It promises no
atomic environment/graph snapshot or freshness guarantee. Verdict, gate/credit
rules and the runner's narrow `(diff_text, repo_root)` signature remain unchanged;
the existing CLI private completion handoff is retained. No full State-save or
new HOLD/recovery mechanism is approved.

**G02 accepted package:** the existing nine-field v1 strict decoder, its existing
five state/diagnostic combinations, and no_named_targets as the sole nonempty
fixed diagnostic. Failure/refusal/missing evidence cannot masquerade as not_run.
The explicitly accepted limits are 4096 artifact bytes, container depth 3 including
arrays, decoded strings of at most 256 UTF-8 bytes; 64 finish attempts per host,
including failures; at most 256 entries per registry snapshot and 512 retained
stage slots; aggregate captured configuration/failure text 64 KiB; aggregate
finding/error history text 64 KiB, with at most 10 findings and 258 errors per
observation. These are not global disk or total-memory limits.

The current nine fields and exact types are described in the
[bounded wire contract](cross-repo-diagnostics-v1.md#closed-v1-wire-contract).
This limited approval does not automatically approve every other numerical or
support detail in that older document: the separate active-cache allowance,
individual captured/history string limits, excluded-root/path traversal budgets,
metadata-read budgets and other unlisted limits retain their prior decision
status. No additional implementation guarantee is inferred from their existence.

G01/G02 semantic freeze is not full runtime qualification, G12/G13 acceptance or
approval of other unlisted contract details. Historical codec controls at
`07a194830db454165aec271b209be4b21646bd0c` / Python 3.14.10 retain their own
scope (duplicate keys, 4096/4097 boundary and independent expected context);
they are not a new run or complete reader/storage qualification. Existing
NOT_RUN, natural-not_run, remaining boundary/control and final same-revision
regression obligations remain explicit. No new experiment accompanies this edit.

### Subsequent T12/T19/T20 bounded dispositions (2026-11-14)

The owner replied “可以继续推进” in project chat to the specific four-part
proposal: obtain direct-caller/distance-ranked top-N evidence, and apply the
following three narrow dispositions. This is the decision source relayed to the
executor; there is no public decision permalink. Applicable inspected source is
`6740c44f44211e55d72ac947185d7f8434ea2e67`; prior document head is
`fb9f1aadc2a86d2cd1dce00f7292cda6d0987662`.

- **T12:** inheriting/migrating an existing capture and simultaneously sharing a
  runner are outside the supported scope. Existing owner-thread serial reuse and
  foreign-thread refusal remain bounded evidence; a PID guard is not a fork
  runtime-safety proof. A fresh host retains the separate T10 identity obligation.
- **T19:** normal descriptor closing and exception-priority obligations remain.
  No unconditional no-leak guarantee after a real close failure is established;
  that behavior stays UNVERIFIED, not a demonstrated leak or defect. Existing
  controls that really close before raising do not qualify the failed-close case.
- **T20:** retain not_run as a defensive legal wire state and its controlled
  consumer evidence. No natural entry has been identified at this source;
  natural reachability is not required by creating a new product path. This is
  not a formal proof of unreachability and does not turn controlled fixtures into
  natural execution evidence. Publication/read/empty delivery remain separate.

Static reachability basis: the primary host installs CrossRepoImpactRunner and
constructs ResolvedReview without target_kind (default None); StateMachine.run's
normal path calls the advisory dispatch. Its preceding admission-error return
requires INDEX/COMMIT in mutation_review_admission_error and therefore does not
supply that natural path for this host. Ordinary axis exceptions continue the
loop; control exceptions do not become normal host completion. Relevant source
blobs were matched to the applicable revision, including cross_repo.py
`6d03d54e0e390ba4afbe2fda2db698bd1bc218ca`, machine.py
`72a3654ef2e6527f0e79a7ff0ce62085351a6db8`, baseline.py
`49620ece27d82695ecc416a60dfa41c2f03c1d5f` and mutation.py
`d10425e80f58ed6df6fdb1d2595b843f58d41394`.

These dispositions do not approve full G12/G13, waive ordinary cleanup/direct
caller obligations or grant runtime PASS to excluded/unverified cases.

### T06 bounded direct-caller and distance-selection regression

Two added nodes in the existing tests/test_cross_repo_impact.py use real SQLite,
real live source files and the native runner without production modification:

- `test_direct_caller_chain_excludes_second_hop`: A <- B <- C, with eligible
  imports for both callers, returns exactly B's file/line and no C; no infra error.
- `test_top_n_selects_nearer_identities_without_tie_order`: 5 farther rows are
  inserted before 4 intermediate and 6 nearest rows. Independent expected file
  sets require the 6 nearest first and 4 intermediate next, excluding farther
  callers. No ordering is asserted within either equal-rank group.

Local commands: `python -m pytest -q tests/test_cross_repo_impact.py -k
'direct_caller_chain or top_n_selects'` and `python -m pytest -q
tests/test_cross_repo_impact.py`, using the official-build Python 3.14.0 cloud
venv with SQLite 3.50.4 / pytest 9.2.0. Results were respectively `2 passed,
46 deselected` and `48 passed`, exit 0. The first focused run emitted two relative-venv-prefix RuntimeWarnings; subsequent runs used the absolute interpreter path. Ruff formatting and lint passed.

Environment precision: the first exploratory runs used the historical
7ca557a2 source copy plus the test additions. A subsequent clean archive of exact
PR parent `fb9f1aadc2a86d2cd1dce00f7292cda6d0987662`, with only these two tests
added, was run using `PYTHONPATH=src python -m pytest -q
tests/test_cross_repo_impact.py`: `48 passed`, exit 0. The archived baseline is
not represented as latest main or its synthetic merge. The original test blob
`ad34f689746b77b9b2625dccccfa1897e520774a` and relevant production
cross_repo_impact.py `2f80c2f3c62c8dd07ccdc4165e4face85277f15d` / dead_code.py
`b5499d6567bd00233f71a0b33019d4bdda7f70c6` match current main 6740c44 and the
PR parent. Final exact-head CI remains a separate result. Public test identities
are extended by exactly the two added node IDs so failure diagnostics can name
them; no workflow or runner permission changes are involved.
This is positive fixture-specific regression evidence, not extra-hop insertion,
cap-removal or other mutation RED credit, arbitrary-graph proof, whole-host
acceptance or final same-revision qualification. No new mutation was executed.

## 2026-11-13 entry adjudication and unified evidence gaps (proposal)

This is the first documentation-only response to the [current #441
scope](https://github.com/HouMinXi/forge/issues/441), the [12:58 UTC execution
handoff](https://github.com/HouMinXi/forge/issues/441#issuecomment-6853641063) and
the [#33 coordination comment](https://github.com/HouMinXi/forge/issues/33#issuecomment-6853622908).
It uses the existing A01–A08, G01–G13, T01–T20/T18a–f and R0–R4 identifiers;
it adds no gate or parallel acceptance process. The proposed dispositions below
require maintainer acceptance unless specifically superseded by the limited
2026-11-14 owner decisions above. No production behavior, schema, permission,
workflow, test or issue checkbox changes in this revision.

### R0/R1: fixed inputs and evidence status

- Product and test source inspected: main
  `7ca557a2d6d56d9c84a810b6c6f1da08861aa7db`, tree
  `adb9cf9407aa0b78c855aa11e836f57de6bbe7f3` (includes #491, #490 and #492).
  Main advanced from `903f03f5` during this static inventory: #492 changes only
  capture-observation scripts/tests, qualification README and 14 manifest nodes,
  not the diagnostic/capture/handoff source or tests mapped here. The manifest
  was reread at the new head; no historical execution credit was transferred.
- Baseline document blob: `23900d30e5ca6268ecfb0457cfe3521237a13b85`.
  This documentation delta is a proposal, not a final acceptance evidence
  revision; bind its final reviewed commit before any acceptance run. A future main movement
  requires an explicit composition review, not relabelling old results.
- Node IDs below are checked against the fixed source's public identity manifest
  and their test definitions/assertions. This is static identity inspection, **not
  fresh pytest collection or execution**. The original 78-port bytes, identities,
  adapter and equivalence remain **UNKNOWN**. The 2026-11-14 decision above accepts
  the versioned G/T catalogue as the organization of subsequent acceptance, not
  completed evidence or full qualification. Missing originals are no longer a
  prerequisite; retained obligations still require evidence or approved disposition.
- The initial static inventory pass did not run tests, negative controls, provider operations, installed
  wheels or CI. Current-revision command, runtime/dependency lock, filesystem,
  provider availability, exit code, raw log and log digest are **NOT_RUN / not
  collected** for inventory rows unless explicitly superseded by the bounded
  fixed-head supplement below. Prior reports do not fill those cells.
  Python 3.14 is the intended test contract; pin its actual patch version and
  dependencies when R0 is executed. Source-tree and installed-wheel evidence must
  remain separate.
- Status vocabulary: **contract accepted** applies to the existing limited
  G03/G05/G06/G07 registrations and the specifically recorded 2026-11-14
  owner decisions, not whole-gate qualification; **merged implementation** describes source;
  **reported test** describes a cited author's result; **current reverified** needs
  new fixed-revision execution and is assigned only to the bounded cases below; **decision pending** and
  **unsupported** are not passes. Counts, collection, overlapping selections and
  CI checks are not accumulated into coverage or a completion percentage.

### A02/A03, G04/G09: entry-by-entry proposed disposition

| Entry and actual functions | Expected context and real consumer | Publication / read / delivery and failure meaning | Proposed supported contract and evidence |
| --- | --- | --- | --- |
| Default live CLI; immediate/background MCP review: `cross_repo.run_cross_repo`, `DiagnosticCapture.finish/present`, `CrossRepoImpactRunner.run`; MCP `forge_review`/`forge_job_status` expose the actual CLI child output | Execution UUID issued by the host; primary capture owns repository/source/exact-diff/sequence identity before publication. Worker `present` passes the retained publication and expected context to `read_observation`, then the existing attributed text callback. | Successful publication is not successful read or output. Empty strict consumption can be delivered without calling a sink; nonempty sink failure is unconfirmed; read failure is not delivered and can retain immediate diagnostic plus warning. Controlled `not_run` is not a natural path proof. | **Propose accepting a text-only consumer for this entry**, not requiring a caller locator or a uniform public result. Native child controls below are docs-only deterministic-backend tests, not all live SDK/stdio, mutation workload or recovery qualification. No new public session API. |
| CLI `--diagnostic-ticket-dir`: `cli._cross_repo_verdict_or_none` → `run_cross_repo(_diagnostic_completion=...)` → `DiagnosticCompletion.seal/read/notices` | Real CLI calling thread owns the invocation slot. Worker seals original `ExpectedContext + PublicationResult` after capture close and before completion event; caller uses this tuple, not the ticket's contents, for strict reread. | Reservation precedes publishing. Interrupted publication retains unknown without successful locator and preserves the original exception. File loss is unavailable, not exported success. Ticket export, capture cleanup, worker completion and caller notice are separate facts; worker completion is not overall CLI completion. | **Merged #491, proposed acceptance only for this opt-in.** Existing persistent fixture supplies CI verdict, so this suite alone is not native gate evidence. Keep defaults and public Verdict unchanged. Tests: CLI-owner rows below; [contract](diagnostic-cli-handoff.md). |
| CLI historical diagnostic: `cli.main` diagnostic-history branch → `_diagnostic_history.read_ticket` → `read_observation` | Explicitly selected unsigned caller-trusted ticket retains past context and locator. It is not the current live owner's independent identity. | Read success is one historical observation, `current_run_credit: false`; unavailable input gives bounded error, control exceptions propagate. No scan, rebinding, retry, review resumption or fresh-analysis claim. | **Separate historical contract**, not a substitute for live G04/G09. Legal complete ticket substitution can choose another trusted historical observation; it must not be treated as a live authentication defect. [CLI history](diagnostic-history-tickets-v1.md), #485. |
| MCP diagnostic history: `forge_diagnostic_history` → `_mcp_diagnostic_history.configure/read` | Operator explicitly binds two private roots at startup; client supplies only ticket basename. Saved ticket context remains caller-trusted. | Default off, root/type/identity checks, generic failures; owned read drains on cancellation/shutdown, cancelled requests cannot return success. Stalled local I/O has no hard deadline. | **Separate #487 historical SDK/stdio slice**; its restart tests do not qualify live review transport, graph freshness, same-UID authentication or cross-restart review authority. [MCP history](mcp-diagnostic-history.md). |
| Scope live: runner `_emit_scope_summary` → `DiagnosticCapture.present_scope` → query-scope publisher/reader | Capture issues context before writing and freezes actual native counts independently of file contents; decoded facts are compared before formatting. | Publication, read and text delivery are separate. Storage failure falls back to this invocation's real summary, not empty success. Before enumeration, failed enumeration, known empty scope and completed zero-caller queries differ; no receipt means unknown, not zero. | **Independent counts contract**, not diagnostic-v1 expansion or proof of full coverage/impact. Duplicate registrations count occurrences; warm cache neither queries nor re-exports. [Scope contract](cross-repo-query-scope-v1.md). |
| Scope historical CLI/MCP: `cli.main` scope-history branch → `_scope_history.read_ticket`; `forge_scope_history` → `_mcp_scope_history.configure/read` | CLI selects caller-owned ticket; MCP additionally binds independently configured server roots. Basename is selector, not independent identity/authentication. | Historical counts projection only, `current_run_credit: false`; ordinary errors bounded, cancellation propagated/drained. Scope and diagnostic configurations and formats cannot authorize each other. | **Separate #480/#489 history contract**, not current query or gate credit. [Scope tickets](scope-history-tickets-v1.md), [MCP scope](mcp-scope-history.md). |

#### Entry decision register (G04/G09; no implied approval)

This register responds to the [formal COMMENT review](https://github.com/HouMinXi/forge/pull/493#pullrequestreview-5892176064)
and [#441 execution direction](https://github.com/HouMinXi/forge/issues/441#issuecomment-6854967873).
COMMENT is not APPROVE, contract acceptance, merge authorization or independent
G0 qualification. The specific 2026-11-14 owner approval updates only the
semantics named in its decision record above. Other entry details remain PENDING;
a generic “Continue” is not blanket contract acceptance. The original proposal and
historical evidence use product/test revision `7ca557a2d6d56d9c84a810b6c6f1da08861aa7db`
and starting document `5fc7c78bef68658f37c5aa93295dad963aa3c10c`, subsequently
delivered through [#493](https://github.com/HouMinXi/forge/pull/493).
The recorded 2026-11-14 owner decisions instead apply to product/test revision
`2b2ac9fc18285f78acf5503ded830d256b3a99ff` and the exact prior document identities
listed above. Bind each decision to its author, rationale, revision and actual
source; these decisions came from project chat with no public permalink.
Historical execution is not reassigned to the decision revision.

| Existing entry/subentry | Proposed decision and reason | Decision / applicable revisions / decision-maker / decision link |
| --- | --- | --- |
| Default live CLI | Accept attributed text consumption, not a mandatory locator/result return; production publication/read/delivery remain distinct. | ACCEPTED: existing attributed text contract only; 2026-11-14 owner chat decision above. No new runtime qualification. |
| Immediate/background live MCP | **Accept the bounded text contract; defer whole-SDK/live recovery qualification.** Same default CLI-child text contract; `forge_review` builds its own CLI arguments and has no `diagnostic_ticket_directory` or `--diagnostic-ticket-dir` parameter. Background `forge_job_status` reports the same job output, not a new invocation or slot. Do not infer CLI opt-in handoff from MCP historical tools. | ACCEPTED: existing CLI-child text contract only; 2026-11-14 owner chat decision above. Whole-SDK/recovery and other proposed details remain pending. |
| Direct `run_cross_repo` without completion slot | **Amend the consumer requirement to distinguish these two supported cases.** `_diagnostic_completion=None` is supported by source. With no ticket directory there is no slot/notice guarantee. With a ticket directory but no slot, the primary worker reports ticket export outcomes directly; this is not the owning CLI thread's post-completion original-tuple reread. Preserve this distinct proposal, rather than adding a uniform result API. | ACCEPTED: no caller reread promise without a slot; 2026-11-14 owner chat decision above. Detailed directory/worker-notice proposal is not blanket-approved. |
| CLI diagnostic-ticket opt-in | Accept #491's original tuple handoff only when CLI creates the slot for the explicitly supplied directory; do not promote worker completion to whole-CLI completion. | ACCEPTED: existing caller handoff and original-tuple reread; 2026-11-14 owner chat decision above. No whole-CLI/SDK qualification implied. |
| CLI diagnostic history | Accept explicitly caller-trusted historical selection, never current-run authority. | ACCEPTED: historical information only, no current-run credit; 2026-11-14 owner chat decision above. Other support details remain pending. |
| MCP diagnostic history | Accept default-off server-root-bound historical reader with its bounded cancellation behavior; not live CLI opt-in. | ACCEPTED: historical information only, no current-run credit; 2026-11-14 owner chat decision above. Root/cancellation/support details are not blanket-approved. |
| Scope live | Accept independently captured counts/text only, not impact coverage or diagnostic-v1 extension. | ACCEPTED: registered/queried scope counts, not complete coverage or actual execution impact; separate 2026-11-14 owner chat approval above. Storage has the separately recorded bounded approval above; cancellation and other unapproved support decisions remain pending. |
| Scope historical CLI/MCP | Accept separate historical count projections with no current-run credit. | ACCEPTED only as historical information without current-run credit; 2026-11-14 owner chat decision above. Detailed support decisions remain pending; scope-live has its separate limited approval above. |

The subentries refine the existing six-family table, not a new workflow or new
public API. The real review links above establish requested adjudication, **not**
a positive decision. G03/G05/G06/G07 retain only their previously registered
limited contract acceptance.

For G04/T14 retain the existing no-cross-repository-HOLD contract and
single-repository axis exclusion. A new host identity does not prove resumed
review. For G08/G10/G11 keep the bounded Linux/qualified-filesystem domain:
ext4 eligibility is not evidence it ran on this cloud host. Same-UID attacks,
file-generation/ABA continuity, global atomic capture, graph freshness, power-loss
durability, hard I/O deadlines, automatic global cleanup and universal cleanup are not silently added
closing conditions. Expanding them requires a separate design.

#### Decision rationale, evidence consumption and finite exit

These are the original executor recommendations, superseded only by the
limited 2026-11-14 owner decisions above. The eight existing
entry rows are the decision units; scope live and scope history must not receive
one implicit blanket approval. Accept the bounded default CLI text, explicit CLI
opt-in, caller-trusted CLI history, server-root-bound MCP history, live counts and
historical counts contracts as stated, because each names its actual independent
expected-context holder and consumer rather than promising a uniform return API.
Amend direct-host wording to separate no directory/no notice from a directory's
worker notice; neither promises CLI post-completion rereading. Accept immediate
and background MCP only as the existing CLI-child text contract, with the same job
output retained; defer broader SDK/recovery claims. The fixed source links in the
G03–G07 response and existing T08/T10/T14/T20 node mappings supply the current
basis. Add a mapping only for a specifically demonstrated omission, not because a
connector preview omitted file tail text.

**Review coverage is not execution status.** Nine readable safe log copies and
the unchanged probe source are already delivered below. Full-tail/anchor/node
identity inspection is completed internally; external full-document and evidence
review remains incomplete. A truncated connector response limits that reader's
review coverage, not whether the implementation, log publication or internal
inspection happened. Review the existing safe copies first: compare each phase's
exit, target assertion and restoration statement with the index, while preserving
the distinction between safe-copy text review, private-original comparison and
independent execution. Do not ask for duplicate assets or replay completed probes
merely to compensate for a preview limit. G04 starts with this existing entry
register, fixed source and mappings; specific missing links, not a fresh blanket
map, determine any further static work.

**Original T14 recommendation:** retain the original obligation visibly, mark
cross-repository HOLD resumption **UNSUPPORTED** under the already accepted no-HOLD
contract, and mark single-repository HOLD diagnostic-axis qualification **NOT
APPLICABLE** because that path has no cross-repository axis. Neither is a PASS or
a reason to create a recovery feature. The supported new-host identity obligation
remains T10, with its own evidence; it is not a replacement recovery run. This exact T14 disposition was accepted by the 2026-11-14 owner chat decision
above; it is not execution or recovery PASS.

**Original 78 catalogue decision:** the 2026-11-14 owner decision accepts the
existing versioned *clause-based* catalogue to organize subsequent acceptance,
without requiring inaccessible original node bytes or claiming equivalence. Reuse
this document's G/T subclause records as that catalogue, not a second count-based
table. Known coverage includes publication/strict consumption (T08/T15/T16),
identity/cache/admission (T09–T12), gate isolation (T13), overlap/cleanup/control
signals (T17–T19) and delivery (T20), each only to its recorded scope and revision.
The bounded supplement supplies three warm-input cases, one caller-read case and
one separate library probe; it does not execute all those categories. T03's bounded primary-connect oracle is now recorded in the dated supplement;
remaining limitations include T06's top-N/single-hop qualification, T12's fork/shared-use
scope, T19's FD scope and T20's natural not_run disposition, as indexed below.
Stable equal-rank ordering is NOT PROMISED under the recorded owner decision;
T14's UNSUPPORTED/NOT APPLICABLE disposition is also accepted, not a runtime PASS. Unrecoverable original cases cannot be named, matched one-to-one,
or counted as covered: original identities, bytes and equivalence stay UNKNOWN.
Catalogue organization is approved; original-port recovery, full coverage and
final qualification are not claimed;
#443/#444 unique differences also require their own disposition. Missing originals
are a declared limitation of the replacement, not an endless blocking prerequisite.

**Previously selected runtime candidate (bounded result in dated T09/T11 supplement):** after consuming the
existing evidence and fixing the supported revision/domain, reuse
`tests/test_cross_repo_persistent_diagnostic.py::test_uncached_branch_transitions_get_new_records_and_cache_reuses_publication`
for T09/T11. Its fixed-source wrappers delegate to the real publisher/reader and
record successful returns: 3 publications and 4 reads, sequential payload states,
and identical retained publication/context objects for the last cached read. This
is not merely a file-count oracle, but wrappers append after return: it does not
count attempted calls that raise. The controlled-CI fixture and resolver-call
count (3) are not a natural gate or native SQL-connection oracle. The fixture
stubs the CI verdict/other axes and injects repeated advisory dispatch; SQLite
branch transitions and storage reads/writes are real, not natural scheduling. This addresses stale
observation/empty-success risk not discharged by the three warm-admission refusals
or the UNKNOWN probe. First run this existing node unchanged in the pinned 3.14
source-tree domain; only after GREEN define one isolated omission and its exact
semantic assertion. Do not claim a fresh SQL-connection count, byte-preservation
oracle or whole-host concurrency from it. The later dated supplement records one cache-republication control; other
subclauses remain separate. Baseline/setup/non-target/timeout/escape/restoration
failures stop the row. No production or test change is proposed for this control.

### 2026-11-14 G07 existing isolation and bounded-failure controls

This consumes six already mapped functions (eight cases) at unchanged
product/test `7ca557a2d6d56d9c84a810b6c6f1da08861aa7db`, document reference
`b0d357982f15921966f2f5d29843ea4f55b1218a`, same cloud Python 3.14.0 / SQLite
3.50.4 environment. Collection eight and execution eight PASS, both exit 0;
no new mutation, production/test edit, schema change or support extension.
Source-tree start/end guard found no changed files. The test file SHA256 is
`1239484e7d98d4c2a10f15ed88fcde01786b531fd29da9a51e8f05fdf532afc1`.
The receipt's document field was corrected from a copied preceding-run template
to the actual b0d35798 reference; command/log/test/source identities did not change.

Within `tests/test_cross_repo_capture.py`, the independent oracles are:

- `test_duplicate_occurrences_retain_distinct_first_use_native_fallbacks`:
  after real native findings and a distinct missing-sibling error, clear the
  outward findings/error lists and change an unused later environment value.
  Cached replay restores the expected finding and original error, retains the
  same Record/Observation and per-occurrence stages, performs no additional
  observed URI SQL or selections, and keeps one publication attempt. This
  checks outward container clearing, not mutation inside arbitrary nested objects.
- `test_large_final_cache_errors_do_not_restore_previous_uncached_error`:
  first obtain a missing-primary error without SQL, then restore the graph and
  obtain a final sibling error longer than 4096 characters. Warm empty-finding
  cache preserves the final errors and SQL count, rather than restoring the
  earlier error. This node does not assert persistence status or all budgets.
- `test_registry_stage_returns_defensive_copies`: change the original registry
  dictionary and returned dictionary/list; replay matches the original literal
  registry and forbids another native operation. Only the supported flat
  registry structure is covered, not arbitrary deep object graphs.
- `test_capture_limit_never_repeats_the_native_operation[bytes|entries|stages]`:
  each existing fixture lowers one limit to zero. An operation returns its exact
  object once per explicit stage request, disables capture support, and a second
  distinct request runs exactly once again. This proves no hidden native retry
  for these forced limits, not that native work stops after unsupported capture
  or that the shipped numeric limit and boundary are qualified.
- `test_failure_capture_limit_preserves_original_error_without_retry`: under a
  zero byte limit, the native failure is the same exception object, invoked once;
  support is disabled and no stage retained. This is not a universal cleanup or
  process-control guarantee.
- `test_non_utf8_native_error_disables_only_persistence`: a surrogate-bearing
  registry alias produces the same native empty findings/errors with or without
  capture; the captured record is `not_published` with query summary and warning.
  This does not make empty findings successful analysis or qualify arbitrary
  encodings/history shapes.

No prior admission-rejection control is used to claim G07 isolation. The eight
passes supply these existing oracles only; no new independent omission RED,
current-main/installed-wheel qualification, full G07/G12/G13 acceptance or
original78 equivalence follows. Aggregate/default-limit boundaries and arbitrary
unsupported objects are not silently upgraded. Other mapped rows retain their
own status. Prefer explicit evidence/unsupported disposition over manufacturing
a new failure solely to make every assertion have a separate mutation.

Command uses the same source root, environment and pytest options in the prior
identity supplement, followed by the six function selectors
identified above and fully expanded in the collection log. Parametrized `test_capture_limit_never_repeats_the_native_operation`
is supplied without brackets, collecting exactly its three cases. No manual CI
rerun is part of this local source-tree verification.

Exit condition: this section completes the bounded evidence-delivery item in
#493, not #441 acceptance. #441 separately adjudicates the supported container,
error-view and forced-zero-budget scopes; whether shipped default-budget
boundaries need further evidence is a decision first, not an automatic new
experiment. #33 remains the independent, open capability roadmap.

<details>
<summary>G07 existing controls: g07-collection (exit 0)</summary>

Original/readable-copy SHA256 `68c9fc30e14570ca0b7b9d01154c4342b0466a8c8e88d342520f2923bbeea527`. No redaction or byte transformation needed; output contains public test identities and summary only.

```text
tests/test_cross_repo_capture.py::test_duplicate_occurrences_retain_distinct_first_use_native_fallbacks
tests/test_cross_repo_capture.py::test_large_final_cache_errors_do_not_restore_previous_uncached_error
tests/test_cross_repo_capture.py::test_registry_stage_returns_defensive_copies
tests/test_cross_repo_capture.py::test_capture_limit_never_repeats_the_native_operation[bytes]
tests/test_cross_repo_capture.py::test_capture_limit_never_repeats_the_native_operation[entries]
tests/test_cross_repo_capture.py::test_capture_limit_never_repeats_the_native_operation[stages]
tests/test_cross_repo_capture.py::test_failure_capture_limit_preserves_original_error_without_retry
tests/test_cross_repo_capture.py::test_non_utf8_native_error_disables_only_persistence

8 tests collected in 0.26s
```

</details>

<details>
<summary>G07 existing controls: g07-existing-controls (exit 0)</summary>

Original/readable-copy SHA256 `1aef82cd5e70b2ea4c95d9d557576257100e1d89b53772510facf304c1e874bd`. No redaction or byte transformation needed; output contains public test identities and summary only.

```text
........                                                                 [100%]
8 passed in 0.34s
```

</details>

### 2026-11-14 existing identity controls: consume before adding experiments

Response to [#33's concrete remaining clauses](https://github.com/HouMinXi/forge/issues/33#issuecomment-6874994624)
and [#441's current review](https://github.com/HouMinXi/forge/issues/441#issuecomment-6875035851).
This increment reuses four existing test functions (seven identity cases plus one cache case), without
changing production or tests and without a new omission mutation. It is not a
repeat of the T03, publisher-count or cached-reader-count experiments.

Product/test remains `7ca557a2d6d56d9c84a810b6c6f1da08861aa7db`; prior document
head is `af930499c018a5ed38562744c207937d25198344`. The same cloud source-tree
Python 3.14.0 / SQLite 3.50.4 environment collected seven identity cases, then
passed all seven (both exit 0); a separate one-case cache collection/run also
passed (both exit 0). This is fixed-baseline revalidation, **not** current-main,
installed-wheel, independent external execution, or a new GREEN/RED/GREEN run.
The before/after source-tree digest guard found no changed files.

- `tests/test_cross_repo_persistent_diagnostic.py::test_host_publication_strictly_reads_after_temporary_cwds_are_removed`:
  after registered temporary directories are removed, reads the first genuine
  publication using its retained expected context. A second genuine host creates
  a different execution/repository identity with the same source/diff; its own
  publication reads successfully, while reading the first publication with the
  second host's retained expected raises `DiagnosticError`. This is actual
  strict-reader old-publication rejection, not automatic discovery or adoption
  of a latest record, and it does not establish cached-call object identity.
  The CI verdict is fixture-controlled; it is not a complete native CLI gate.
- `tests/test_advisory_diagnostics.py::test_decoder_rejects_each_valid_foreign_expected_context`:
  five collected parameter cases vary only execution ID, repository ID, source
  hash, diff hash, or observation sequence. An unchanged legal payload succeeds
  against its original expected; each separately validated foreign expected is
  rejected specifically as `context_mismatch`. Direct decode avoids publication
  namespace rejection hiding this comparison. This is codec parameter rejection,
  not proof that every host forwards the right object to the reader.
- `tests/test_advisory_diagnostics.py::test_ambiguous_foreign_final_after_link_error_is_never_adopted`:
  the existing isolated filesystem fault creates a different final file with
  identical payload bytes before raising a link error. Publication is `unknown`,
  has no locator, and those final bytes remain. This rejects foreign-final
  adoption at the publisher; it does not model an older host record or prove
  no fallback in every consumer. No permission or system setting is changed.

Consequently, "old-record adoption NOT_RUN" in the earlier control supplement
means that particular proposed omission experiment was not executed; it is not
absence of all old-identity rejection evidence. Preserve that historical status,
while linking these narrowly applicable existing controls to T08/T10/T15/T16.
Neither the seven passes nor prior reader-count RED provides independent RED
credit for the two cached publication/expected `is` assertions or all T09/T11.
Empty `targets_resolved` publication already has positive file/state/sequence
assertions in the previous transition test; skip-empty omission discrimination
remains distinct and unrun. Do not invent a global missing-coverage finding.

Execution command, from the unchanged source root (`PY314` names the same
recorded official-source virtual environment, not arbitrary system Python):

```text
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$PY314" -B -m pytest -p pytest_asyncio.plugin -p no:cacheprovider -q tests/test_cross_repo_persistent_diagnostic.py::test_host_publication_strictly_reads_after_temporary_cwds_are_removed tests/test_advisory_diagnostics.py::test_decoder_rejects_each_valid_foreign_expected_context tests/test_advisory_diagnostics.py::test_ambiguous_foreign_final_after_link_error_is_never_adopted
```

Collection adds `--collect-only`; complete collected parameter node IDs are in
the first log below. Test SHA256: persistent diagnostic
`f500bcd14e6ebd0d738ff6ba91a9439122cace66eac3f1191bc17df0dd7bb34b`;
advisory diagnostics
`b07a92db819ea8aa1302a58491a9f4c48254a5c3b13697abbab91cb86c7b1e38`.
These are start/end checks, not independently sampled per-phase test digests.

Current-main composition check: GitHub's complete compare from the product
baseline to `45e48cc723142449a301451e4c9d3783b03fc0ce` lists invocation/audit,
associated qualification/tests, baseline diagnostics and the public identity
manifest. It does not change the three selected test files or diagnostic/capture/
completion production files. This is a path-level comparison, not qualification
of the new invocation implementation or permission to relabel these runs as
main. Open parallel drafts #443/#444 and stock-runner #465 remain untouched.

One additional existing control was consumed before proposing a new mutation:
`tests/test_cross_repo_capture.py::test_active_owner_native_cold_and_warm_delivery`.
It produces a real named target/caller finding, holds expected context before
publication, strictly reads the empty `targets_resolved` record, repeats the
same runner and asserts the same record object, one publication attempt, three
read attempts (including its explicit verification read), and exactly unchanged
artifact-path/byte mapping. Its SQLite observer attaches a trace callback to
successful connections opened with `uri=True`; SQL trace count is nonzero on
cold execution and unchanged on warm cache. This is an actual traced-statement
oracle, not merely resolver count. It does not count rejected connection
attempts, non-URI connections, statements before callback installation, or prove
all SQL/provider behavior. The existing storage observers count calls before
delegation, unlike the earlier persistent fixture's normal-return counters.
This one selected test passed; no omission mutation was added, so no new
independent RED credit is claimed. Source/test bytes stayed unchanged.
Test SHA256 `1239484e7d98d4c2a10f15ed88fcde01786b531fd29da9a51e8f05fdf532afc1`.
The command uses the same environment/options above with only this complete
node ID in place of the three identity functions.

Remaining actions are bounded: retain proposed entry/support-domain dispositions
as PENDING for explicit maintainer decisions. Existing narrow evidence now covers
old-publication rejection, valid foreign expected rejection, same-content foreign
final refusal, and this cold/warm record/bytes/SQL case. These are not a general
identity/SQL/gate qualification and not all omission controls. Do not mechanically
add another count or identity experiment just because a later assertion lacks
its own RED. First assess the remaining G07 returned-container/error-view and
budget clauses against their already mapped controls; propose a new control
only for a concrete unsupported inference. No new protocol, production feature,
G12/G13 approval, original78 equivalence, merge or issue closure follows.

<details>
<summary>Existing identity controls: collection (exit 0)</summary>

Original and readable-copy SHA256 `b86bc8c0953bb3f235e4f9a73c147e1f88195c0b479129d597fa0ac394c762ff`. No redaction or byte transformation was needed; reviewed output contains only public test identities and pytest summary.

```text
tests/test_cross_repo_persistent_diagnostic.py::test_host_publication_strictly_reads_after_temporary_cwds_are_removed
tests/test_advisory_diagnostics.py::test_decoder_rejects_each_valid_foreign_expected_context[execution_id-aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa]
tests/test_advisory_diagnostics.py::test_decoder_rejects_each_valid_foreign_expected_context[repository_id-bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb]
tests/test_advisory_diagnostics.py::test_decoder_rejects_each_valid_foreign_expected_context[source_hash-independent-other-source]
tests/test_advisory_diagnostics.py::test_decoder_rejects_each_valid_foreign_expected_context[diff_sha256-ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff]
tests/test_advisory_diagnostics.py::test_decoder_rejects_each_valid_foreign_expected_context[observation_sequence-2]
tests/test_advisory_diagnostics.py::test_ambiguous_foreign_final_after_link_error_is_never_adopted

7 tests collected in 0.40s
```

</details>

<details>
<summary>Existing identity controls: existing-controls (exit 0)</summary>

Original and readable-copy SHA256 `a3f3508bc9164111f39999bba941874f272407a0210feb4f3762e2cf7fb9f5b4`. No redaction or byte transformation was needed; reviewed output contains only public test identities and pytest summary.

```text
.......                                                                  [100%]
7 passed in 0.88s
```

</details>

<details>
<summary>Existing cache control: cache-collection (exit 0)</summary>

Original/readable-copy SHA256 `baf1baa97fd7fab5c072ef66d50d5b60eaacf99f9bbc865e347e78b54ec6f911`. No redaction or transformation needed.

```text
tests/test_cross_repo_capture.py::test_active_owner_native_cold_and_warm_delivery

1 test collected in 0.27s
```

</details>

<details>
<summary>Existing cache control: cache-existing-control (exit 0)</summary>

Original/readable-copy SHA256 `3a6504404ab639ac8fe2940ac1654603c072a999c0ae65ca7ef9701ab97a0954`. No redaction or transformation needed.

```text
.                                                                        [100%]
1 passed in 0.32s
```

</details>

### 2026-11-14 T11 cached formal-reader invocation control

After explicit approval of this single boundary, the unchanged existing node
`tests/test_cross_repo_persistent_diagnostic.py::test_uncached_branch_transitions_get_new_records_and_cache_reuses_publication`
was reused at product/test `7ca557a2d6d56d9c84a810b6c6f1da08861aa7db`, with preceding
document `54b7a2a52ba18396ccad747b7af66ce2d44c85b8`. Cloud CPython 3.14.0,
SQLite 3.50.4 and pytest 9.2.0; same command, explicit environment and 90-second
phase cap as the adjacent cache-publication control. One node collected, exit 0;
controlled baseline 1 PASS, exit 0.

Only in the owned isolated `_cross_repo_diagnostics.py` copy, `present` used the
already-held observation instead of `storage.read_observation` when origin was
cache; actual delivery still invoked the real reader. Owner/context guards,
subsequent reader-return validation, tests and all other branches were unchanged.
This deliberate bypass is not a baseline defect. The target RED was line 467:
`len(storage_calls.reads) == 4`, actual 3, exit 1. Before that assertion, verdict,
message, resolver count, publisher count, file count and sequential payload checks
all passed. The two subsequent publication/expected object-identity assertions
were not reached and receive no independent RED credit. Restoration: 1 PASS,
exit 0, source byte-exact; zero changed files in the final whole-tree guard.

Original/restored production SHA256:
`6a324dd403da9e621dc5a511f2e92bced8fc1c23b1c9fdcbc6a5b1639ae2176d`;
mutant: `b7ce9ba495f2cc353ff4e012e8490b06a7660d5a73d86c1bef415d415bb0bdb8`.
Unchanged test digest, checked before/after and recorded in each phase:
`f500bcd14e6ebd0d738ff6ba91a9439122cace66eac3f1191bc17df0dd7bb34b`.

This independently shows the test detects omission of the cached formal-reader
call, unlike the earlier publisher-count or CLI-caller notices controls. Its
wrapper counts normal reader returns, not raising attempts. It does not prove
wrong-identity rejection, parameter correctness, old-record non-adoption, durable
commit, all cache contracts or native whole-host scheduling. The controlled-CI
fixture limits remain. No production/test changes or contract/gate acceptance
are proposed by this evidence-only addition.

Safe logs use the adjacent supplement's disclosed fixture/object/path/UUID/address
redactions and trailing-whitespace normalization; original and displayed hashes
are distinct. These are retained run evidence, not an independent rerun claim.

<details>
<summary>T11 cached-reader collection: safe log</summary>

Original SHA256: `0a4593465d2a59fc57ab637e11c93356c84b57de296f237c72ff4d079288a1a9`.
Safe-copy SHA256: `0a4593465d2a59fc57ab637e11c93356c84b57de296f237c72ff4d079288a1a9`.

```text
tests/test_cross_repo_persistent_diagnostic.py::test_uncached_branch_transitions_get_new_records_and_cache_reuses_publication

1 test collected in 0.30s
```

</details>

<details>
<summary>T11 cached-reader controlled-baseline: safe log</summary>

Original SHA256: `85a7532b40196fb821e4b3dfb1231181e8e280a2da477abec65674576186c6c1`.
Safe-copy SHA256: `85a7532b40196fb821e4b3dfb1231181e8e280a2da477abec65674576186c6c1`.

```text
.                                                                        [100%]
1 passed in 0.73s
```

</details>

<details>
<summary>T11 cached-reader cache-reader-bypass: safe log</summary>

Original SHA256: `a0eb9c227744f621e075139b466acb5ad22b0e9a743e8bfbd1cbfa974808c5c0`.
Safe-copy SHA256: `8ebf9554e6c1b5c98aa2444d9ad49e629f10ebc95fcc15f3d88bb77d94943ac6`.

```text
F                                                                        [100%]
=================================== FAILURES ===================================
_ test_uncached_branch_transitions_get_new_records_and_cache_reuses_publication _

persistent_cli = <FIXTURE>
storage_calls = <FIXTURE>
monkeypatch = <FIXTURE>

    def test_uncached_branch_transitions_get_new_records_and_cache_reuses_publication(
        persistent_cli, storage_calls, monkeypatch
    ):
        from code_forge import cross_repo_impact as impact
        from code_forge.machine import StateMachine

        observed = persistent_cli
        native_dispatch = StateMachine._run_advisory_axes
        native_resolve = impact.resolve_changed_symbols
        queries = []

        def resolve(*args, **kwargs):
            queries.append(args)
            return native_resolve(*args, **kwargs)

        def repeated_dispatch(machine):
            native_dispatch(machine)
            if machine.baseline_spec_repr == "primary":
                native_dispatch(machine)  # Another genuine uncached zero-target call.
                edit_graph(
                    observed.primary,
                    "UPDATE nodes SET kind='function', name='api', qualified_name='api::api'",
                )
                native_dispatch(machine)  # Genuine resolved-target call with no callers.
                native_dispatch(machine)  # Same runner's cache, no query/publication.

        monkeypatch.setattr(impact, "resolve_changed_symbols", resolve)
        monkeypatch.setattr(StateMachine, "_run_advisory_axes", repeated_dispatch)
        messages = []
        assert observed.run(output=messages.append) is Verdict.PASS
        assert messages.count(ATTRIBUTED_MESSAGE) == 2
        assert len(queries) == 3
        assert len(storage_calls.publications) == 3
        paths = _records(observed.primary)
        assert len(paths) == 3
        for sequence, state in enumerate(("no_named_targets", "no_named_targets", "targets_resolved"), 1):
            _payload(paths[sequence - 1], observed.primary, sequence=sequence, state=state)
>       assert len(storage_calls.reads) == 4
E       AssertionError: assert 3 == 4
E            + <GENERATED_OBJECT_RENDERING>
E            + <GENERATED_OBJECT_RENDERING>

tests/test_cross_repo_persistent_diagnostic.py:467: AssertionError
----------------------------- Captured stderr call -----------------------------
[forge] t+0.0s run start: mode=CI
[forge] t+0.0s run start: mode=CI
[forge] t+0.0s review phase done: verdict=PASS findings=0 confirmed=0

--- Smoke Status ---
smoke: no runtime surfaces detected
[forge] t+0.0s review phase done: verdict=PASS findings=0 confirmed=0
=========================== short test summary info ============================
FAILED tests/test_cross_repo_persistent_diagnostic.py::test_uncached_branch_transitions_get_new_records_and_cache_reuses_publication
1 failed in 0.73s
```

</details>

<details>
<summary>T11 cached-reader restored: safe log</summary>

Original SHA256: `212504876166ec78d6893ba7cb632e14878599d119e4df02390a6a5464c97ea0`.
Safe-copy SHA256: `212504876166ec78d6893ba7cb632e14878599d119e4df02390a6a5464c97ea0`.

```text
.                                                                        [100%]
1 passed in 0.68s
```

</details>

### 2026-11-14 T09/T11 existing cache-publication control

The next bounded run reused the unchanged existing node
`tests/test_cross_repo_persistent_diagnostic.py::test_uncached_branch_transitions_get_new_records_and_cache_reuses_publication`.
Product/test source: `7ca557a2d6d56d9c84a810b6c6f1da08861aa7db`; preceding document
head: `de1c86bd1dec29cd6ac9f507ef6ab418ea3fda1e`. Environment: cloud CPython 3.14.0,
SQLite 3.50.4, pytest 9.2.0, same source-tree environment as the bounded runs above.
No new test, production repair, public identity or support-contract change.

Command from the isolated source: `$PY314 -B -m pytest -p pytest_asyncio.plugin
-p no:cacheprovider -q` followed by that node, with `PYTHONPATH=src`,
`PYTHONDONTWRITEBYTECODE=1`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`; collection additionally
uses `--collect-only`. Each recorded phase has a 90-second cap. One initial unchanged
exploratory baseline also passed; the full controlled sequence is separately
recorded below and does not count it twice.

Collection: one node, exit 0. Controlled baseline: 1 PASS, exit 0. Only in the owned
isolated source, a call to existing `DiagnosticCapture.finish` was inserted before
cached delivery, deliberately republishing the cached observation. This is one
cache-publication boundary injection, not an unmodified-product defect. The
unchanged test failed at line 462, `len(storage_calls.publications) == 3`, with
actual 4; earlier verdict, message and resolver-count assertions passed. Exit 1.
Byte-exact restoration yielded 1 PASS, exit 0. The wrapper delegates the real
publisher then records its normal return; it counts neither calls that raise nor
necessarily successful durable commits. Later payload/read-identity assertions
were not reached in RED and receive no independent negative-control credit.

The fixture controls CI verdict/other axes and repeated advisory dispatch while
using real SQLite transitions and publisher/reader operations. Resolver call
count is not SQL statement/connection count; this is not native whole-host timing,
all cache clauses, G03 completion, full T09/T11 or G12/G13 qualification. Skip-empty
publication and old-record adoption controls remain NOT_RUN in this pass.

Source original/restored SHA256:
`556a329c909a4f14cc7e5b79ae7d1f5c479b8d6e78f48935ece209faa0de3a0f`;
mutant: `abaa2fb4a45200af83b00c7afdfe5ac65d43195be3416c16e0d5a44125e81cd1`.
Unchanged test SHA256 at every phase:
`f500bcd14e6ebd0d738ff6ba91a9439122cace66eac3f1191bc17df0dd7bb34b`.
Harness checked test bytes before/after each phase, restored production in finally,
and found zero changed source-tree files at the end. No source/test change from
this experiment is proposed for the repository.

Safe logs below retain target assertion, phase summary and exit classification.
Fixture/object rendering lines are replaced; UUIDs, memory addresses and private
workspace/temporary paths are de-identified, and trailing whitespace normalized.
Original/private and safe-copy SHA256 are distinct. Safe copies support review,
not an independent execution claim.

<details>
<summary>T09/T11 collection: safe log</summary>

Original SHA256: `eb7800d497c00aaec3fda9ebad485611f329dd1a7dc6e2d064b3415d8e84a1dc`.
Safe-copy SHA256: `eb7800d497c00aaec3fda9ebad485611f329dd1a7dc6e2d064b3415d8e84a1dc`.

```text
tests/test_cross_repo_persistent_diagnostic.py::test_uncached_branch_transitions_get_new_records_and_cache_reuses_publication

1 test collected in 0.25s
```

</details>

<details>
<summary>T09/T11 controlled-baseline: safe log</summary>

Original SHA256: `ac0988b070cfb8585ee6cce3a7e8654f43da14bf7e316bc0b9277c6ad43db855`.
Safe-copy SHA256: `ac0988b070cfb8585ee6cce3a7e8654f43da14bf7e316bc0b9277c6ad43db855`.

```text
.                                                                        [100%]
1 passed in 0.79s
```

</details>

<details>
<summary>T09/T11 cache-republish: safe log</summary>

Original SHA256: `38afcaceb75ed8af382b8530c699425886f79bf0d878d040fcda61c7dbc9f3de`.
Safe-copy SHA256: `b06479b460c1e7f0bcaaa7fa208226b2808c8377b33d82c6aa895cee9e3a0938`.

```text
F                                                                        [100%]
=================================== FAILURES ===================================
_ test_uncached_branch_transitions_get_new_records_and_cache_reuses_publication _

persistent_cli = <FIXTURE>
storage_calls = <FIXTURE>
monkeypatch = <FIXTURE>

    def test_uncached_branch_transitions_get_new_records_and_cache_reuses_publication(
        persistent_cli, storage_calls, monkeypatch
    ):
        from code_forge import cross_repo_impact as impact
        from code_forge.machine import StateMachine

        observed = persistent_cli
        native_dispatch = StateMachine._run_advisory_axes
        native_resolve = impact.resolve_changed_symbols
        queries = []

        def resolve(*args, **kwargs):
            queries.append(args)
            return native_resolve(*args, **kwargs)

        def repeated_dispatch(machine):
            native_dispatch(machine)
            if machine.baseline_spec_repr == "primary":
                native_dispatch(machine)  # Another genuine uncached zero-target call.
                edit_graph(
                    observed.primary,
                    "UPDATE nodes SET kind='function', name='api', qualified_name='api::api'",
                )
                native_dispatch(machine)  # Genuine resolved-target call with no callers.
                native_dispatch(machine)  # Same runner's cache, no query/publication.

        monkeypatch.setattr(impact, "resolve_changed_symbols", resolve)
        monkeypatch.setattr(StateMachine, "_run_advisory_axes", repeated_dispatch)
        messages = []
        assert observed.run(output=messages.append) is Verdict.PASS
        assert messages.count(ATTRIBUTED_MESSAGE) == 2
        assert len(queries) == 3
>       assert len(storage_calls.publications) == 3
E       AssertionError: assert 4 == 3
E            + <GENERATED_OBJECT_RENDERING>
E            + <GENERATED_OBJECT_RENDERING>

tests/test_cross_repo_persistent_diagnostic.py:462: AssertionError
----------------------------- Captured stderr call -----------------------------
[forge] t+0.0s run start: mode=CI
[forge] t+0.0s run start: mode=CI
[forge] t+0.0s review phase done: verdict=PASS findings=0 confirmed=0

--- Smoke Status ---
smoke: no runtime surfaces detected
[forge] t+0.0s review phase done: verdict=PASS findings=0 confirmed=0
=========================== short test summary info ============================
FAILED tests/test_cross_repo_persistent_diagnostic.py::test_uncached_branch_transitions_get_new_records_and_cache_reuses_publication
1 failed in 0.77s
```

</details>

<details>
<summary>T09/T11 restored: safe log</summary>

Original SHA256: `fedceb118a93f8e9bed4f5b262246c28f6bfdace0d13713f5045fa2fb6554486`.
Safe-copy SHA256: `fedceb118a93f8e9bed4f5b262246c28f6bfdace0d13713f5045fa2fb6554486`.

```text
.                                                                        [100%]
1 passed in 0.78s
```

</details>

### 2026-11-14 T03 primary-connection observation (bounded execution)

Following explicit user approval of the two zero-connection observations and one
positive control, the existing immediate-diagnostic test gained an observer after
fixture creation and edits, before the runner call. It delegates the real SQLite
connect and records attempts for the exact primary read-only URI before delegate
return. Existing message, empty result, infra-error, cache and sibling sentinels
are unchanged. No production change is proposed. Existing node identities remain
unchanged, so no manifest addition is needed.

Product source is `7ca557a2d6d56d9c84a810b6c6f1da08861aa7db`; document plan was
`27165d8ab1f0c6f491bc3718bd0cf26f72cce144`. The new test file SHA256 is
`a1cdc0b2d0211fbe03f4a82f00b408a22e34dba56bcb331a6dad4166a6e41f5f`.
Cloud CPython 3.14.0, SQLite 3.50.4 and pytest 9.2.0 were reused, with auto plugin
loading disabled and pytest_asyncio explicitly enabled. Only these three nodes
were in the bounded 90-second-per-phase sequence:

```text
tests/test_cross_repo_immediate_diagnostic.py::test_zero_targets_emit_once_before_sibling_access[no-path]
tests/test_cross_repo_immediate_diagnostic.py::test_zero_targets_emit_once_before_sibling_access[deleted]
tests/test_cross_repo_immediate_diagnostic.py::test_zero_targets_emit_once_before_sibling_access[unnamed]
```

Command: `$PY314 -B -m pytest -p pytest_asyncio.plugin -p no:cacheprovider -q`
followed by the three exact nodes above, from the isolated source tree with
`PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`.
Collection adds `--collect-only` to that command. Collection exit 0: three nodes;
baseline exit 0: three pass. In the owned isolated copy, only the no-path return
was moved from before connect to the start of its existing try/finally, retaining
close. The unchanged new assertion at line 114 failed twice with `1 == 0`;
unnamed remained passing. Mutant exit 1: two target failures, one positive-control
pass. Restored exit 0: three pass. No prior assertion/setup error masked the
connection oracle. This is a deliberate ordering mutant, not a baseline defect.

Original/restored production SHA256:
`556a329c909a4f14cc7e5b79ae7d1f5c479b8d6e78f48935ece209faa0de3a0f`;
mutant SHA256:
`372a9af68b95079ce319078a92c7efd445341f79cc37881dbaeda096eab577fa`.
The harness writes only that production file during the sequence and restores it
in finally. Its end guard found zero changed files relative to the pre-run
isolated tree (which already contained the new test). The test digest is recorded
at sequence start and checked by that end guard, not independently logged for
every phase. Full immediate-diagnostic module regression then passed 44 tests,
exit 0; its relative interpreter spelling emitted sys.prefix/sys.exec_prefix
RuntimeWarnings, retained in the log, not silently represented as warning-free.

Credit is limited to these inputs' primary URI connection attempts, with a real
connection positive control. It is not all SQLite/system I/O, persistent host
publication, provider qualification or complete T03/G12/G13 acceptance. Other
T03 subclauses and other inventory rows keep their separate status. No production
file, support contract, gate checkbox or issue state is changed.

The following safe copies preserve test assertions and summaries. Fixture-value
renderings are replaced, temporary paths and memory addresses de-identified;
trailing whitespace is normalized. Original log hashes identify private originals;
safe-copy hashes identify the displayed UTF-8 bytes including their final newline.
These are reviewable execution logs, not independent reruns.

<details>
<summary>T03 collection: safe log</summary>

Original SHA256: `c690f602e7802b64226a00da356f5b9fafa2ee404c63e576bbf922fb241e83ff`.
Safe-copy SHA256: `c690f602e7802b64226a00da356f5b9fafa2ee404c63e576bbf922fb241e83ff`.

```text
tests/test_cross_repo_immediate_diagnostic.py::test_zero_targets_emit_once_before_sibling_access[no-path]
tests/test_cross_repo_immediate_diagnostic.py::test_zero_targets_emit_once_before_sibling_access[deleted]
tests/test_cross_repo_immediate_diagnostic.py::test_zero_targets_emit_once_before_sibling_access[unnamed]

3 tests collected in 0.18s
```

</details>

<details>
<summary>T03 baseline: safe log</summary>

Original SHA256: `c67e541fd068f1dc781192f208ee5378278d4121a29b58ff089a2b0504263f5c`.
Safe-copy SHA256: `c67e541fd068f1dc781192f208ee5378278d4121a29b58ff089a2b0504263f5c`.

```text
...                                                                      [100%]
3 passed in 0.22s
```

</details>

<details>
<summary>T03 connect-before-return: safe log</summary>

Original SHA256: `bef5254545fdaed1e2b9afacc292a06f9b279977a21810639ff2551e5713f88a`.
Safe-copy SHA256: `20def00fd3665ddfbd498f3541c18658dff4930fc4b7ecc9539825e3bcf4ea9b`.

```text
FF.                                                                      [100%]
=================================== FAILURES ===================================
__________ test_zero_targets_emit_once_before_sibling_access[no-path] __________

graphs = <FIXTURE>
monkeypatch = <FIXTURE>
case = 'no-path'

    @pytest.mark.parametrize("case", ["unnamed", "no-matching-node", "no-path", "deleted"])
    def test_zero_targets_emit_once_before_sibling_access(graphs, monkeypatch, case):
        primary, _, _, _ = graphs
        diff = DIFF
        if case == "no-matching-node":
            edit_graph(primary, "DELETE FROM nodes")
        elif case == "no-path":
            diff = "a nonempty diff without an added-side path\n"
        elif case == "deleted":
            diff = DIFF.replace("+++ b/api.py", "+++ /dev/null")
        # Observe after fixture creation/edits, before the target runner invocation.
        primary_uri = impact.readonly_uri((primary / ".code-review-graph/graph.db").absolute())
        primary_connections = []
        real_connect = sqlite3.connect

        def observe_connect(database, *args, **kwargs):
            if database == primary_uri:
                primary_connections.append(database)
            return real_connect(database, *args, **kwargs)

        monkeypatch.setattr(sqlite3, "connect", observe_connect)
        monkeypatch.setattr(impact, "_registry_path", forbid)
        monkeypatch.setattr(impact, "_find_cross_repo_caller_groups", forbid)
        messages = []
        runner = impact.CrossRepoImpactRunner(output_fn=messages.append)
        assert runner.run(diff, primary) == []
        assert messages == [MESSAGE]
        assert runner.infra_errors == []
        assert runner._cached is None
        if case in ("no-path", "deleted"):
>           assert len(primary_connections) == 0
E           AssertionError: assert 1 == 0
E            +  where 1 = len(['file://<PYTEST_TMP_PATH>'])

tests/test_cross_repo_immediate_diagnostic.py:114: AssertionError
__________ test_zero_targets_emit_once_before_sibling_access[deleted] __________

graphs = <FIXTURE>
monkeypatch = <FIXTURE>
case = 'deleted'

    @pytest.mark.parametrize("case", ["unnamed", "no-matching-node", "no-path", "deleted"])
    def test_zero_targets_emit_once_before_sibling_access(graphs, monkeypatch, case):
        primary, _, _, _ = graphs
        diff = DIFF
        if case == "no-matching-node":
            edit_graph(primary, "DELETE FROM nodes")
        elif case == "no-path":
            diff = "a nonempty diff without an added-side path\n"
        elif case == "deleted":
            diff = DIFF.replace("+++ b/api.py", "+++ /dev/null")
        # Observe after fixture creation/edits, before the target runner invocation.
        primary_uri = impact.readonly_uri((primary / ".code-review-graph/graph.db").absolute())
        primary_connections = []
        real_connect = sqlite3.connect

        def observe_connect(database, *args, **kwargs):
            if database == primary_uri:
                primary_connections.append(database)
            return real_connect(database, *args, **kwargs)

        monkeypatch.setattr(sqlite3, "connect", observe_connect)
        monkeypatch.setattr(impact, "_registry_path", forbid)
        monkeypatch.setattr(impact, "_find_cross_repo_caller_groups", forbid)
        messages = []
        runner = impact.CrossRepoImpactRunner(output_fn=messages.append)
        assert runner.run(diff, primary) == []
        assert messages == [MESSAGE]
        assert runner.infra_errors == []
        assert runner._cached is None
        if case in ("no-path", "deleted"):
>           assert len(primary_connections) == 0
E           AssertionError: assert 1 == 0
E            +  where 1 = len(['file://<PYTEST_TMP_PATH>'])

tests/test_cross_repo_immediate_diagnostic.py:114: AssertionError
=========================== short test summary info ============================
FAILED tests/test_cross_repo_immediate_diagnostic.py::test_zero_targets_emit_once_before_sibling_access[no-path]
FAILED tests/test_cross_repo_immediate_diagnostic.py::test_zero_targets_emit_once_before_sibling_access[deleted]
2 failed, 1 passed in 0.21s
```

</details>

<details>
<summary>T03 restored: safe log</summary>

Original SHA256: `05f7f7d7e74af6e23fe4984046332ed347e22cf50f99a33856b9d2f589167249`.
Safe-copy SHA256: `05f7f7d7e74af6e23fe4984046332ed347e22cf50f99a33856b9d2f589167249`.

```text
...                                                                      [100%]
3 passed in 0.20s
```

</details>

<details>
<summary>T03 module-regression: safe log</summary>

Original SHA256: `1276edcd36078aa11d18c26bdf7f1eaa70f2eb10b581e3408c91cd95726e47e1`.
Safe-copy SHA256: `7d68635d40f7036756d1901f67ee0653761fe97b6a089dcf7f0c1c321410f27b`.

```text
<frozen site>:101: RuntimeWarning: Unexpected value in sys.prefix, expected <WORKSPACE>/python314-build/venv, got <WORKSPACE>/forge441-t03-20261114/source/../../python314-build/venv
<frozen site>:101: RuntimeWarning: Unexpected value in sys.exec_prefix, expected <WORKSPACE>/python314-build/venv, got <WORKSPACE>/forge441-t03-20261114/source/../../python314-build/venv
............................................                             [100%]
44 passed in 2.24s
```

</details>

### Subsequent bounded source-tree verification (local evidence; no gate approval)

The following experiments occurred after the initial documentation-only draft.
They do not change contract acceptance, recover the original 78-port, or execute
other inventory rows. Product/test source remains `7ca557a2d6d56d9c84a810b6c6f1da08861aa7db`;
the initial document proposal is PR #493 at `5fc7c78bef68658f37c5aa93295dad963aa3c10c`.
This supplement still needs a final reviewed documentation revision. Raw local
receipts/logs remain private; safe log copies and the probe source are now folded
below. Original hashes identify private originals, not downloadable raw artifacts.

| Evidence and type | Fixed environment and result | Independent oracle and precise limit |
| --- | --- | --- |
| G10/T15/T18e: `unknown_no_fallback.py`, **new standalone probe, not a committed pytest node** | Official-source CPython 3.14.0, standard library, Linux tmpfs. Original GREEN / one false-success replacement TARGET RED / byte-restored GREEN; exits 0/1/0. | Real link followed by injected verification-read OSError: UNKNOWN/no locator, one link, only current-sequence verification, prior bytes/inode unchanged, prior formal read still valid, no owned staging residue. Replacement sets the unverifiable-commit branch to published/candidate/None; RED is `unexpected_success_status=published`. This is a replacement, not guard omission or an unmodified product defect. Library branch only. |
| G06/T12: existing `tests/test_cross_repo_capture.py::test_changed_input_refuses_before_cache_sql_error_clear_or_publication[exact-diff]` | CPython 3.14.0, SQLite 3.50.4, provider 2.4.0, pytest 9.2.0. Collected; GREEN / TARGET RED / restored GREEN. | After real initial SQLite finding populates cache, exact diff changes. Omitting admission returns the old nonempty finding; second-call refusal assertion detects it. |
| Same existing function `[lexical-root]` | Same fixed environment, collected; GREEN / TARGET RED / restored GREEN. | Same canonical root but different literal path is rejected before cached return. |
| Same existing function `[canonical-root]` | Same fixed environment, collected; GREEN / TARGET RED / restored GREEN. | Same literal alias resolves to another root; old nonempty cache must not be returned. |
| G04/G09/T08: existing `tests/test_diagnostic_completion.py::test_ticket_replacement_does_not_rebind_cli_expected` | Same Python 3.14.0 environment; collected; GREEN / target caller-read omission RED / byte-restored GREEN, exits 0/1/0. | A valid foreign historical ticket replaces the exported bytes, but the actual calling thread must still read the original retained publication/context after the completion event. Omitting that read produces `reads == []` at the exact tuple assertion. Controlled-CI host fixture, not native gate or SDK/stdio qualification. |

The three pytest cases ran together with exits **0/1/0**, respectively 3 passed,
3 failed at the intended second-call return assertion, and 3 passed. The isolated
source archive changed only the unique runner `admit` condition to `if False:`;
all 1543 regular tracked source files matched the fixed Git blob identities after
restoration. The test/oracle was unchanged. These are **warm-cache** controls, not
cold-path, whole-host or installed-wheel qualification. Mutants stop at the first
return-value assertion: later SQL/error/publication assertions do not independently
receive RED credit. SQL traces are not a zero-connection oracle; record/attempt
counts are not an independent publisher-call count.

The caller-read case is a separate one-test sequence. Its oracle records actual
calling-thread reads and delegates the real formal reader; replacement-ticket
verification occurs on the worker. Omitting the single `notices()` read is not
a test of hostile ticket authentication or all identity validation. The production
file and unchanged test were byte-verified after restoration.

Environment preparation initially failed collection with exit 4 because `_ctypes`
was absent. That log is retained as **environment BLOCKED_NOT_RUN**, not product
RED. Official libffi 3.4.6 was built into the private runtime prefix; collection
then found all three exact nodes, with no skipped cases. SQLite and ctypes imports
and real SQLite execution succeed. The resolved environment has 92 compatible
packages, including project core/dev/MCP/vertex requirements; this does not claim
all optional platform modules, tools, external services or full-suite prerequisites.
An earlier Python 3.12 probe remains exploration only and is not part of the 3.14
sequence. No system security setting, production branch or remote implementation
was changed.

Reproduction uses the recorded private CPython executable (`PY314`) and the exact
isolated fixed source (`SOURCE`), not arbitrary current main:

```sh
cd "$SOURCE"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  "$PY314" -B -m pytest -p pytest_asyncio.plugin -p no:cacheprovider -q \
  tests/test_cross_repo_capture.py::test_changed_input_refuses_before_cache_sql_error_clear_or_publication
```

The local receipts record the absolute command and environment; the retained
pytest control harnesses enforce a 90-second cap per phase. The standalone probe used `PY314 -I -B unknown_no_fallback.py <isolated-module>`
with a 30-second cap. Retained SHA256 bindings:

- Standalone probe: `1be6821c0eddbd2719d2b572635ba1fff16941189eea2aa910871029c3213248`;
  receipt: `4acde965086c4c4d414b04c401d652d0e33bb527609a38d3f9774fda8f7b7163`.
- Caller-read receipt: `78e5993b3c8da5b95a6d635f1cf7d1c56e6a17cf9c195e6eb34d5f17e8678d31`;
  source before/after: `43fb554ffe61876bbb50e6e9e631c35acd98a1e488b42d40db464128fce9a308`.
- Three-node receipt: `fb679dc42d8e10bdae57b6e09d3404665abd098411d8eb0ac7c18005bf9c54df`;
  environment freeze: `1390eb3162f3ace80137e08fc5cf46ee2d1968a058679db640a79d8df841258e`.
- Admission source before/after: `556a329c909a4f14cc7e5b79ae7d1f5c479b8d6e78f48935ece209faa0de3a0f`;
  unchanged test file: `1239484e7d98d4c2a10f15ed88fcde01786b531fd29da9a51e8f05fdf532afc1`.

Independent review checked phase logs, target failure locations and restoration
hashes without rerunning. Node expansions not explicitly covered by a dated execution supplement remain
**NOT_RUN** in this local evidence pass. Passing automatic checks on PR #493 is separate CI
information, not a substitute for these clause-specific oracles or G12/G13.

### Safe review index for the already reported executions

This narrowly responds to the [14:56 UTC discussion](https://github.com/HouMinXi/forge/issues/441#issuecomment-6856599660).
It is a transcription and digest check of retained receipts/logs, **not a new
execution** or an external reviewer's independent runtime verification. Product
and tests remain `7ca557a2d6d56d9c84a810b6c6f1da08861aa7db`; the report started
at document `5fc7c78bef68658f37c5aa93295dad963aa3c10c`, was incorporated at
`91c8af79cc098a11276ec1636c693fae6acccf27`, and T19's static audit is at
`e79cf400696f47452973d0089836b2dfbef9d96e`. None is a final acceptance revision. Before publishing this index, main advanced
to `fa4d549be6628a30bbf7fd7a5210d23a4522f1da` via #494: the compared delta adds
`qualification/invocation/T34-PROCESS-RECOVERY.md` and
`tests/test_invocation_t34_process_recovery.py`, and updates the public test identity
manifest. It does not change the fixed diagnostic source/test files above. The
reported evidence is **not rebased or relabelled** to that newer main, and #494's
invocation-lifecycle evidence is not imported into this slice.

The commands below preserve the recorded arguments/environment but substitute
`PY314`, `SOURCE`, `PROBE` and `MODULE` for private absolute paths. The first
command uses the standalone CPython 3.14.0 build; pytest uses the repaired 3.14.0
venv described above. The probe source is now included below; the fixed product module must still be
obtained at its recorded SHA. This is not a standalone environment bundle. No raw log,
receipt, private path, generated ticket identifier or invented download link is
published. The index contains selected assertion text and verified original-log digests;
the folded safe copies and probe source below now make the report text inspectable.
Original-log digests still do not make private raw files independently retrievable.

```sh
"$PY314" -I -B "$PROBE" "$MODULE"
cd "$SOURCE"
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  "$PY314" -B -m pytest -p pytest_asyncio.plugin -p no:cacheprovider -q \
  tests/test_cross_repo_capture.py::test_changed_input_refuses_before_cache_sql_error_clear_or_publication
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  "$PY314" -B -m pytest -p pytest_asyncio.plugin -p no:cacheprovider -q \
  tests/test_diagnostic_completion.py::test_ticket_replacement_does_not_rebind_cli_expected
```

- Probe fixture: a real first record and a second-sequence link followed by a
  verification-read OSError. The false-success replacement fails at
  `AssertionError: ('unexpected_success_status', 'published')`; baseline/restored
  exit 0. This is a library probe, not a committed test or product defect.
- Admission fixture: real SQLite finding seeds a warm cache; exact diff, lexical
  path or canonical target then changes. All three listed parameters fail at
  `tests/test_cross_repo_capture.py:168`, `assert runner.run(diff, root) == []`,
  because the omitted guard returns the previous nonempty finding. Log summaries
  are 3 passed / 3 failed / 3 passed. Later assertions get no separate RED credit.
- Caller fixture: controlled-CI host, valid foreign historical ticket substitution,
  retained original publication/expected tuple. The omitted read fails at
  `tests/test_diagnostic_completion.py:319`, the recorded-read tuple assertion
  with actual `reads == []`. Summaries are 1 passed / 1 failed / 1 passed. This
  does not qualify native gate, general authentication or SDK/stdio behavior.

| Retained execution phase | Recorded exit code | Original log SHA256 (verified in this transcription) |
| --- | --- | --- |
| UNKNOWN probe: `py314-baseline` | 0 | `0b6d67ce77f42485cc86d7be9af07a0a79086050ee6b1ae60dd872b4fa285600` |
| UNKNOWN probe: `py314-false-success` | 1 | `1a6765112aea0ad801886bb2cb8cf47ca5003ca3fba74795821f8ed8ce0032d7` |
| UNKNOWN probe: `py314-restored` | 0 | `0b6d67ce77f42485cc86d7be9af07a0a79086050ee6b1ae60dd872b4fa285600` |
| Warm admission: `controlled-baseline` | 0 | `8bbc4d00d6a321a31d4bf47956a4d6cc557f5dc6fab20a80411dc519ba2e1419` |
| Warm admission: `admission-omitted` | 1 | `beb8f2dab30fe0faf954f605c94fc62a7e8b4a375af41dadd1949989e00abc0b` |
| Warm admission: `restored` | 0 | `71072fac3893e064493feb00efcde52bab79febd5bdf8d1ad5008deaffa54c9c` |
| Caller reread: `controlled-baseline` | 0 | `ac0988b070cfb8585ee6cce3a7e8654f43da14bf7e316bc0b9277c6ad43db855` |
| Caller reread: `caller-read-omitted` | 1 | `6ad8fcc24b7329425aac0dd64f8f705c37fbb1702155c02b1cc94f8aff6524ee` |
| Caller reread: `restored` | 0 | `c952b5c568a02b83362e5cf5a0f46f1c733434977b415fd71a83aa63e7031707` |

The original/restored source hashes are equal in each retained receipt and are
listed in the preceding supplement; mutation hashes are distinct. Both pytest
source guards report 1543 checked regular tracked files and zero mismatches.
These are recorded restoration checks, not a rerun of the tests. The earlier
missing-`_ctypes` collection failure remains environment BLOCKED_NOT_RUN; its
repair does not retroactively convert it to a product RED. Raw artifacts remain local. The safe copies and unchanged probe source below
permit text review; independent execution and full raw-receipt/environment
reconstruction remain separate, unfinished qualification.

#### G03–G07 response and next decision

- **G03:** retain only existing limited acceptance. Warm-input refusal does not
  replace T09/T11's original record/sequence/bytes and independent SQL/publisher
  oracles. Reuse those mapped controls after final revision/domain adjudication.
- **G04:** recommend the existing per-entry text/opt-in/history/scope proposals,
  including separate direct-host no-slot behavior. Each actual acceptance,
  amendment or deferral must still identify the reviewed document commit and
  decision source; this response records none. The caller control is only the
  explicitly reported fixture and node. Fixed source for the applicability claim:
  [direct-host signature](https://github.com/HouMinXi/forge/blob/7ca557a2d6d56d9c84a810b6c6f1da08861aa7db/src/code_forge/cross_repo.py#L279-L310),
  [worker no-slot notice](https://github.com/HouMinXi/forge/blob/7ca557a2d6d56d9c84a810b6c6f1da08861aa7db/src/code_forge/cross_repo.py#L592-L616),
  [MCP review signature/CLI dispatch](https://github.com/HouMinXi/forge/blob/7ca557a2d6d56d9c84a810b6c6f1da08861aa7db/src/code_forge/mcp_server.py#L1520-L1584)
  and [existing-job status](https://github.com/HouMinXi/forge/blob/7ca557a2d6d56d9c84a810b6c6f1da08861aa7db/src/code_forge/mcp_server.py#L1727-L1800).
  These are static source locations, not proof of executed transport qualification.
- **G05:** propose an explicit unsupported/not-applicable disposition for original
  cross-repository HOLD recovery under the accepted no-HOLD contract. Keep the
  original obligation visible; do not manufacture a recovery test or mark that
  disposition accepted without the maintainer's decision.
- **G06:** preserve existing limited stage-effective/admission acceptance. Only
  the three warm-input return-value assertions have the reported targeted RED;
  cold paths, process/fork/shared use and stage discovery remain separate mapped
  obligations, not additional credit from this sequence.
- **G07:** retain support-type/budget-limited isolation. Nested values, error-view,
  returned-container and budget oracles remain in T11/T12/T16; G06 refusal is not
  evidence of G07 isolation. No new implementation defect is inferred.

The complete local document (not a connector preview) was used to check the six
finite T03/T06/T12/T14/T19/T20 tasks, stable evidence anchors and parameter lists.
T19's static assertion audit and T03/T06/T20 selected-node/source audits are finished;
their identified runtime/oracle gaps and unapproved contract decisions remain
explicitly pending, except the limited 2026-11-14 dispositions recorded above. The executor can finish indexing and safe
transcription, but cannot turn these proposed support/port dispositions into
maintainer decisions. Original 78-port status remains UNKNOWN.

A separate internal static reviewer read the complete pre-copy document through
its historical supplements and checked 26 unique evidence anchors, 27 paired
folds and 330 unique manifest node references. This is full-text static checking,
not external G0 qualification or test execution. Historical T06/T19 and proposed
contract wording were tightened to match the current bounded claims; the newly
added evidence-copy folds are additional, not a retroactive change to that count.

### Reviewable copies of retained evidence (not new executions)

Responding to the [16:44 UTC review](https://github.com/HouMinXi/forge/issues/441#issuecomment-6859169876),
the folded blocks below publish safe copies of the **existing** nine phase logs
and the standalone probe source. Original-log hashes remain in the index above;
each copy has its own SHA256 after the disclosed redactions. Private originals
remain local. These copies close the missing-readable-log/source portion, not
independent execution, acceptance, original-port equivalence or environment qualification.

Copy normalization strips line-end whitespace. Redactions: entire pytest fixture-value lines (`invocation`, `monkeypatch`,
`tmp_path`, `native_graphs`, `counted_queries`) and the long rendered PublicationResult
value are replaced with labelled markers; private workspace paths, pytest temp
roots, generated UUIDs and process memory addresses are replaced with markers.
No assertion, target test node, traceback test line, summary, exit code or timing
is removed. The fixed synthetic UUID constants in probe source are fixture data,
not credentials. Probe source is unchanged; invocation needs the fixed
`_advisory_diagnostics.py` source and the source-library environment already
recorded above. The mutation protocol remains the single replacement described
above, not a new experiment executed here.


<details>
<summary>UNKNOWN probe: py314-baseline, recorded exit 0</summary>

Safe-copy SHA256: `0b6d67ce77f42485cc86d7be9af07a0a79086050ee6b1ae60dd872b4fa285600`.

```text
{"commit_calls": 1, "current_sequence_exists": true, "old_bytes_preserved": true, "old_identity_preserved": true, "owned_staging_remaining": false, "platform": "linux", "python": "3.14.0", "result": "PASS", "scope": "isolated source-library probe; not whole-host, G12/G0/G1 or original-port acceptance", "source_sha256": "21fe87ed7da727db1f11bab2d283f3dad0efb2d07b9c23d5444620aea7bc899f", "unknown_locator": null, "verification_reads": ["2.json", "2.json"]}
```

</details>


<details>
<summary>UNKNOWN probe: py314-false-success, recorded exit 1</summary>

Safe-copy SHA256: `3a9ad8ebcad534de997365a81f2874c00731325ad36fb14645e9731147f775b1`.

```text
Traceback (most recent call last):
  File "<WORKSPACE>/forge441-evidence-20261113/unknown_no_fallback.py", line 31, in <module>
    assert result.status == 'unknown', ('unexpected_success_status',result.status)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: ('unexpected_success_status', 'published')
```

</details>


<details>
<summary>UNKNOWN probe: py314-restored, recorded exit 0</summary>

Safe-copy SHA256: `0b6d67ce77f42485cc86d7be9af07a0a79086050ee6b1ae60dd872b4fa285600`.

```text
{"commit_calls": 1, "current_sequence_exists": true, "old_bytes_preserved": true, "old_identity_preserved": true, "owned_staging_remaining": false, "platform": "linux", "python": "3.14.0", "result": "PASS", "scope": "isolated source-library probe; not whole-host, G12/G0/G1 or original-port acceptance", "source_sha256": "21fe87ed7da727db1f11bab2d283f3dad0efb2d07b9c23d5444620aea7bc899f", "unknown_locator": null, "verification_reads": ["2.json", "2.json"]}
```

</details>


<details>
<summary>Warm admission: controlled-baseline, recorded exit 0</summary>

Safe-copy SHA256: `8bbc4d00d6a321a31d4bf47956a4d6cc557f5dc6fab20a80411dc519ba2e1419`.

```text
...                                                                      [100%]
3 passed in 0.34s
```

</details>


<details>
<summary>Warm admission: admission-omitted, recorded exit 1</summary>

Safe-copy SHA256: `552fbd3ff3c03cb0551991b446dfb85345d95ac9f87eab30a18544704051c778`.

```text
FFF                                                                      [100%]
=================================== FAILURES ===================================
_ test_changed_input_refuses_before_cache_sql_error_clear_or_publication[exact-diff] _

native_graphs = <FIXTURE_VALUE_REDACTED>
counted_queries = <FIXTURE_VALUE_REDACTED>
change = 'exact-diff'

    @pytest.mark.parametrize("change", ["exact-diff", "lexical-root", "canonical-root"])
    def test_changed_input_refuses_before_cache_sql_error_clear_or_publication(
        native_graphs, counted_queries, change
    ):
        primary, sibling, _, _ = native_graphs
        add_named_target_and_caller(primary, sibling)
        alias = primary.parent / "primary-alias"
        alias.symlink_to(primary, target_is_directory=True)
        original_root = alias if change == "canonical-root" else primary
        owner = capture(original_root)
        messages = []
        runner = impact.CrossRepoImpactRunner(_diagnostics=owner, output_fn=messages.append)
        findings = runner.run(DIFF, original_root)
        assert len(findings) == 1 and counted_queries
        assert owner.records[0].publication.status == "published"
        query_count = len(counted_queries)
        original_record = owner.records[0]
        original_errors = ["untouched native error view"]
        runner.infra_errors[:] = original_errors
        diff, root = DIFF, original_root
        if change == "exact-diff":
            diff += "\n"
        elif change == "lexical-root":
            root = alias
            assert root != original_root and root.resolve() == original_root.resolve()
        else:
            alias.unlink()
            alias.symlink_to(sibling, target_is_directory=True)
            assert root == original_root and root.resolve() != owner._canonical

>       assert runner.run(diff, root) == []
E       AssertionError: assert [AdvisoryFind...po graph.db')] == []
E
E         Left contains one more item: AdvisoryFinding(id='cross-repo-impact-1', axis='CROSS-REPO-IMPACT', file='consumer:api.py', line_range=(1, 1), description='api used by consumer at api.py:1', attribution='cross-repo graph.db')
E         Use -v to get more diff

tests/test_cross_repo_capture.py:168: AssertionError
_ test_changed_input_refuses_before_cache_sql_error_clear_or_publication[lexical-root] _

native_graphs = <FIXTURE_VALUE_REDACTED>
counted_queries = <FIXTURE_VALUE_REDACTED>
change = 'lexical-root'

    @pytest.mark.parametrize("change", ["exact-diff", "lexical-root", "canonical-root"])
    def test_changed_input_refuses_before_cache_sql_error_clear_or_publication(
        native_graphs, counted_queries, change
    ):
        primary, sibling, _, _ = native_graphs
        add_named_target_and_caller(primary, sibling)
        alias = primary.parent / "primary-alias"
        alias.symlink_to(primary, target_is_directory=True)
        original_root = alias if change == "canonical-root" else primary
        owner = capture(original_root)
        messages = []
        runner = impact.CrossRepoImpactRunner(_diagnostics=owner, output_fn=messages.append)
        findings = runner.run(DIFF, original_root)
        assert len(findings) == 1 and counted_queries
        assert owner.records[0].publication.status == "published"
        query_count = len(counted_queries)
        original_record = owner.records[0]
        original_errors = ["untouched native error view"]
        runner.infra_errors[:] = original_errors
        diff, root = DIFF, original_root
        if change == "exact-diff":
            diff += "\n"
        elif change == "lexical-root":
            root = alias
            assert root != original_root and root.resolve() == original_root.resolve()
        else:
            alias.unlink()
            alias.symlink_to(sibling, target_is_directory=True)
            assert root == original_root and root.resolve() != owner._canonical

>       assert runner.run(diff, root) == []
E       AssertionError: assert [AdvisoryFind...po graph.db')] == []
E
E         Left contains one more item: AdvisoryFinding(id='cross-repo-impact-1', axis='CROSS-REPO-IMPACT', file='consumer:api.py', line_range=(1, 1), description='api used by consumer at api.py:1', attribution='cross-repo graph.db')
E         Use -v to get more diff

tests/test_cross_repo_capture.py:168: AssertionError
_ test_changed_input_refuses_before_cache_sql_error_clear_or_publication[canonical-root] _

native_graphs = <FIXTURE_VALUE_REDACTED>
counted_queries = <FIXTURE_VALUE_REDACTED>
change = 'canonical-root'

    @pytest.mark.parametrize("change", ["exact-diff", "lexical-root", "canonical-root"])
    def test_changed_input_refuses_before_cache_sql_error_clear_or_publication(
        native_graphs, counted_queries, change
    ):
        primary, sibling, _, _ = native_graphs
        add_named_target_and_caller(primary, sibling)
        alias = primary.parent / "primary-alias"
        alias.symlink_to(primary, target_is_directory=True)
        original_root = alias if change == "canonical-root" else primary
        owner = capture(original_root)
        messages = []
        runner = impact.CrossRepoImpactRunner(_diagnostics=owner, output_fn=messages.append)
        findings = runner.run(DIFF, original_root)
        assert len(findings) == 1 and counted_queries
        assert owner.records[0].publication.status == "published"
        query_count = len(counted_queries)
        original_record = owner.records[0]
        original_errors = ["untouched native error view"]
        runner.infra_errors[:] = original_errors
        diff, root = DIFF, original_root
        if change == "exact-diff":
            diff += "\n"
        elif change == "lexical-root":
            root = alias
            assert root != original_root and root.resolve() == original_root.resolve()
        else:
            alias.unlink()
            alias.symlink_to(sibling, target_is_directory=True)
            assert root == original_root and root.resolve() != owner._canonical

>       assert runner.run(diff, root) == []
E       AssertionError: assert [AdvisoryFind...po graph.db')] == []
E
E         Left contains one more item: AdvisoryFinding(id='cross-repo-impact-1', axis='CROSS-REPO-IMPACT', file='consumer:api.py', line_range=(1, 1), description='api used by consumer at api.py:1', attribution='cross-repo graph.db')
E         Use -v to get more diff

tests/test_cross_repo_capture.py:168: AssertionError
=========================== short test summary info ============================
FAILED tests/test_cross_repo_capture.py::test_changed_input_refuses_before_cache_sql_error_clear_or_publication[exact-diff]
FAILED tests/test_cross_repo_capture.py::test_changed_input_refuses_before_cache_sql_error_clear_or_publication[lexical-root]
FAILED tests/test_cross_repo_capture.py::test_changed_input_refuses_before_cache_sql_error_clear_or_publication[canonical-root]
3 failed in 0.39s
```

</details>


<details>
<summary>Warm admission: restored, recorded exit 0</summary>

Safe-copy SHA256: `71072fac3893e064493feb00efcde52bab79febd5bdf8d1ad5008deaffa54c9c`.

```text
...                                                                      [100%]
3 passed in 0.37s
```

</details>


<details>
<summary>Caller reread: controlled-baseline, recorded exit 0</summary>

Safe-copy SHA256: `ac0988b070cfb8585ee6cce3a7e8654f43da14bf7e316bc0b9277c6ad43db855`.

```text
.                                                                        [100%]
1 passed in 0.79s
```

</details>


<details>
<summary>Caller reread: caller-read-omitted, recorded exit 1</summary>

Safe-copy SHA256: `e6519c757db31d1637c3d3f0ca4901518c2fdd5f0c2ef09e061677b1b3c12987`.

```text
F                                                                        [100%]
=================================== FAILURES ===================================
_____________ test_ticket_replacement_does_not_rebind_cli_expected _____________

invocation = <FIXTURE_VALUE_REDACTED>
monkeypatch = <FIXTURE_VALUE_REDACTED>
tmp_path = <FIXTURE_VALUE_REDACTED>

    def test_ticket_replacement_does_not_rebind_cli_expected(invocation, monkeypatch, tmp_path):
        from dataclasses import replace
        from code_forge import _diagnostic_history as history

        host, slots, tickets = invocation
        seal = handoff.DiagnosticCompletion.seal
        read = storage.read_observation
        caller = threading.get_ident()
        reads = []
        foreign_contexts = []

        def seal_and_replace_ticket(self, capture, **kwargs):
            seal(self, capture, **kwargs)
            attempt = self._result.attempts[0]
            foreign = replace(attempt.expected, execution_id=str(uuid4()))
            root = tmp_path / "other"
            root.mkdir(mode=0o700)
            publication = storage.publish_observation(
                storage.bind_storage(root), storage.make_observation(foreign, "targets_resolved")
            )
            other_ticket = history.export_ticket(tickets, publication, foreign)
            original = tickets / self._result.tickets[0][1]
            original.write_bytes(other_ticket.read_bytes())
            other_ticket.unlink()
            assert history.read_ticket(original).context == foreign
            foreign_contexts.append(foreign)

        def track(publication, expected):
            if threading.get_ident() == caller:
                assert slots[0]._completion_event.is_set()
                reads.append((publication, expected))
            return read(publication, expected)

        monkeypatch.setattr(handoff.DiagnosticCompletion, "seal", seal_and_replace_ticket)
        monkeypatch.setattr(storage, "read_observation", track)
        assert host.run(output=lambda message: None) is Verdict.PASS
        attempt = slots[0].read().attempts[0]
>       assert reads == [(attempt.publication, attempt.expected)]
E       AssertionError: assert [] == [(Publication..._sequence=1))]
E
E         Right contains one more item: <PUBLICATION_VALUE_REDACTED>
E         Use -v to get more diff

tests/test_diagnostic_completion.py:319: AssertionError
----------------------------- Captured stdout call -----------------------------
[cross-repo] primary: ADVISORY: diagnostic-ticket: exported <GENERATED_UUID>.diagnostic-ticket.json; one historical observation, not complete review history
----------------------------- Captured stderr call -----------------------------
[forge] t+0.0s run start: mode=CI
[forge] t+0.0s run start: mode=CI
[forge] t+0.0s review phase done: verdict=PASS findings=0 confirmed=0

--- Smoke Status ---
smoke: no runtime surfaces detected
[forge] t+0.0s review phase done: verdict=PASS findings=0 confirmed=0
=========================== short test summary info ============================
FAILED tests/test_diagnostic_completion.py::test_ticket_replacement_does_not_rebind_cli_expected
1 failed in 0.86s
```

</details>


<details>
<summary>Caller reread: restored, recorded exit 0</summary>

Safe-copy SHA256: `c952b5c568a02b83362e5cf5a0f46f1c733434977b415fd71a83aa63e7031707`.

```text
.                                                                        [100%]
1 passed in 0.76s
```

</details>


<details>
<summary>Unchanged standalone probe source (reported library evidence only)</summary>

This is an archival source transcript, displayed as text to preserve the recorded
bytes rather than reformatting historical evidence. Its SHA256 is the probe hash
listed above; it is not a maintained executable example.

```text
import dataclasses, hashlib, importlib.util, json, os, pathlib, platform, sys, tempfile
source = pathlib.Path(sys.argv[1]).resolve()
spec = importlib.util.spec_from_file_location('probe_records', source)
r = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = r
spec.loader.exec_module(r)
with tempfile.TemporaryDirectory(prefix='forge441-owned-') as directory:
    root = pathlib.Path(directory)
    os.chmod(root, 0o700)
    binding = r.bind_storage(root)
    c1 = r.ExpectedContext('11111111-1111-4111-8111-111111111111','22222222-2222-4222-8222-222222222222','fixed-source','a'*64,1)
    c2 = dataclasses.replace(c1,observation_sequence=2)
    old = r.make_observation(c1,'no_named_targets')
    new = r.make_observation(c2,'targets_resolved')
    p1 = r.publish_observation(binding,old)
    assert p1.status == 'published'
    assert r.read_observation(p1,c1) == old
    oldpath=root.joinpath(*(name for name,_ in p1.locator.directories),p1.locator.filename)
    before=oldpath.read_bytes(); identity=(oldpath.stat().st_dev,oldpath.stat().st_ino)
    original_link=r.os.link; original_read=r._read_leaf
    calls=[];reads=[]
    def counted_link(*a,**kw):
        calls.append((a[0],a[1]));return original_link(*a,**kw)
    def unreadable(*a,**kw):
        reads.append(a[1]);raise OSError('synthetic bounded verification-read failure')
    try:
        r.os.link=counted_link;r._read_leaf=unreadable
        result=r.publish_observation(binding,new)
    finally:
        r.os.link=original_link;r._read_leaf=original_read
    assert result.status == 'unknown', ('unexpected_success_status',result.status)
    assert result.locator is None, 'unknown_success_locator'
    assert result.failure == 'commit_uncertain'
    assert len(calls)==1, ('commit_retry',calls)
    assert calls[0][1]=='2.json'
    assert all(name=='2.json' for name in reads),('old_record_fallback',reads)
    assert len(reads)==2,('verification_path_changed',reads)
    assert oldpath.read_bytes()==before, 'old_bytes_overwritten'
    assert (oldpath.stat().st_dev,oldpath.stat().st_ino)==identity,'old_identity_replaced'
    assert r.read_observation(p1,c1)==old
    newpath=oldpath.with_name('2.json')
    payload=json.loads(newpath.read_bytes())
    assert payload['observation_sequence']==2
    assert payload['analysis_state']=='targets_resolved'
    assert len(list(root.rglob('*.json')))==2
    assert not list(root.rglob('.pending-*'))
    try:r.read_observation(result,c2)
    except r.DiagnosticError:pass
    else:raise AssertionError('unknown_read_success')
    print(json.dumps({'result':'PASS','python':platform.python_version(),'platform':sys.platform,'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'commit_calls':len(calls),'verification_reads':reads,'old_bytes_preserved':True,'old_identity_preserved':True,'unknown_locator':None,'current_sequence_exists':True,'owned_staging_remaining':False,'scope':'isolated source-library probe; not whole-host, G12/G0/G1 or original-port acceptance'},sort_keys=True))
```

</details>

### A01/A04/A07, R2: complete obligation inventory and static node expansion

This inventory enumerates every G01–G13, T01–T20 and T18a–f obligation from the
linked original requirements, including later narrowed contracts. Those original
requirements are not all approved: limited acceptance covers G03/G05/G06/G07
and the explicitly recorded owner decisions at the top of this document, not
whole-gate qualification. Each obligation below has identified source/tests or an explicit gap.
An inventory complete at the clause-label level is **not completed R2 evidence,
G12 acceptance, original-port recovery or executed coverage**. The old detailed
T map below remains historical evidence, not the authority for current runtime
status.

For **every row and every expanded node** in this section:
- Product/test source is the fixed head in R0; runtime status is **NOT_RUN**, except
  the exact cases explicitly executed in the bounded supplement above.
- Static node existence was checked in that head's manifest and Python AST only;
  fresh collection, runtime/dependency/provider/filesystem admission remain open
  except for the exact environments and cases in the bounded supplement above.
- Proposed future selector command: the pinned Python 3.14 interpreter with
  `PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -p pytest_asyncio.plugin -p no:cacheprovider -q '<exact nodeid>'`.
  This is a reproduction template, not a command executed here or permission to
  run it. Resolve project dependency pins and platform support before use.
- Except for the exact cases in the bounded supplement above, current command exit
  code, stdout/stderr, log path/digest and negative-control restoration digest are **not produced**. Historical reports cited below cannot
  fill these cells. Source-tree and installed-wheel slots remain separate.
- Each negative-control entry names known report scope or missing work. A test
  containing fault injection is not evidence that removal of its guard has been
  run; all new fault scripts require a row-specific independent semantic oracle.

Each existing T label below is a stable evidence-record anchor. Its obligation,
source, independent oracle, supported gap, R3 status and exact parameter list form
that record; future updates bind individual parameter nodes, not the entire group.
Fixed product/test revision and initial environment disposition are inherited from
R0; the dated execution supplements override only their explicitly named
parameter nodes and separately labelled probe, within each stated environment
and oracle. Existing-control PASS and omission-control RED are separate facts. Per-node command, exit, log/digest and restored
hash are UNKNOWN/NOT_RUN where no executed record is cited. A proposed command is
not a recorded execution. The G-to-record links below map executable subclauses;
contract-only decisions retain the entry decision register or explicit PENDING.

#### G01–G13: clause decisions, source and unresolved evidence

| Gate | All required subclauses and present source | Exact test references / oracle / remaining status | Stable evidence records for executable subclauses |
| --- | --- | --- | --- |
| G01 | One current self-contained contract; replace conflicting v4 origin/State-save/eager-snapshot/HOLD/root proposals; explicit four-P1 and support dispositions. `cross-repo-diagnostics-v1.md`, entry table and original comments. | **Limited G01 semantics ACCEPTED on 2026-11-14**, as recorded above. T08–T12/T14/T18/T19 implement parts; remaining unlisted support/details and runtime qualification are not approved by implication. Preserve original obligations or named unsupported dispositions; no runtime G0 toggle. | Contract decisions: entry decision register; [T14](#evidence-t14), [T18](#evidence-t18), [T19](#evidence-t19) for support dispositions. |
| G02 | Freeze nine exact fields, types/version, five legal state/diagnostic combinations, exact UTF-8/diff, unknown/missing/duplicate/nonfinite rejection, byte/depth/string/sequence/attempt limits and opaque repository alias/privacy. `_advisory_diagnostics` codec plus capture budgets. | T16 expanded families cover baseline syntax/context; T15/T18 cover storage separately. **Limited G02 field/state and expressly listed resource freeze ACCEPTED on 2026-11-14**, as recorded above; other unlisted limits retain prior status. Decoder field omissions and valid-limit boundaries retain their evidence gaps; neither UUID nor digest proves authorization. | Schema/context/limits: [T16](#evidence-t16); storage budget: [T15](#evidence-t15), [T18d](#evidence-t18d). |
| G03 | Original observation separated from delivery; actual/cache metadata; same-input hit reuses sequence/artifact/publication/error view, no SQL/republication/credit; uncached early repeat is actual. Capture Record and runner final cache. | **Limited contract accepted**; bounded cache-republication and cached formal-reader invocation controls are recorded in the dated supplements; other nodes retain their individual status under the dated supplements. The existing cold/warm record/bytes/SQL and G07 controls are not omitted from that record. Original object/context, SQL/publish counters and bytes are oracles; do not infer state from empty findings. | Observation/delivery and cache: [T09](#evidence-t09), [T11](#evidence-t11), [T12](#evidence-t12). |
| G04 | Per-entry issuance before runner/cache, repository/axis/sequence binding, forwarding to publisher/formal reader, explicit caller success/failure/locator and independent expected; same MCP child retains identity; supported new host versus recovery. Host UUID, capture, CLI completion and MCP child. | Entry table plus T08/T10/T14/T20 and history catalogue. **Limited entry semantics accepted on 2026-11-14** as recorded above; remaining per-entry details and whole G04 remain pending. #491 is real opt-in handoff. Do not force a uniform public return or invent authenticated cross-restart state. | Issuance/forwarding/caller: [T08](#evidence-t08), [T10](#evidence-t10); recovery: [T14](#evidence-t14); entry decisions above. |
| G05 | Preserve cross-repo PENDING→FAIL/no HOLD; single-repo HOLD has no axis, actual recovery not fabricated. `run_cross_repo`; CLI dispatch/HOLD routing. | **Limited contract accepted**; T14 includes controlled compatibility node and new-host evidence, not live recovery. Original T14 UNSUPPORTED/NOT APPLICABLE disposition accepted on 2026-11-14; neither is PASS. | Pending/HOLD/axis support: [T14](#evidence-t14). |
| G06 | Admission before cache/error clear/SQL/publication; original installed capture/identity/stage-store/process/thread, lexical/canonical root/exact diff; stage-effective actual first use, untouched stages lazy, same-instance retain/new-host rediscover; no repeated fallback native work on budget error. `DiagnosticCapture.admit`/stage methods, runner. | **Limited contract accepted**; T07/T11/T12 exact nodes/counters. The dated supplements record three changed-input omission controls plus the separately scoped existing stage/cache/budget controls; uncited nodes remain NOT_RUN. Fork/process and simultaneous-use qualification not inferred from foreign-thread tests; no atomic/freshness guarantee. | Discovery stage: [T07](#evidence-t07); admission/cache/error view: [T11](#evidence-t11), [T12](#evidence-t12). |
| G07 | Retain original observation/findings/errors; isolate returned containers and stage dictionaries; old uncached errors do not overwrite cached view; supported-type/budget/encoding failure unavailable, never successful empty history. Capture snapshot/cache. | **Limited contract accepted**; T11/T12 plus bounded/history nodes in their expansions. Arbitrary nested objects are unsupported; current mutation/log coverage not established by frozen types. | Snapshot/cache isolation and unavailable: [T11](#evidence-t11), [T12](#evidence-t12), [T16](#evidence-t16). |
| G08 | Primary persistent root outside every temporary root/alias, namespace ownership, canonical/no-follow/regular file, qualified FS/ACL/path budgets; same-key conflict and concurrent distinct executions; inode-reuse limitations. Storage binder/publisher/reader. | T15–T18d/e and storage-support catalogue. **Bounded storage-support package accepted on 2026-11-14** as recorded above; exact remaining alias/budget/overlap evidence and full G08 qualification are pending. Source/metadata checks are not generation continuity or same-UID authentication; library overlap is not whole-host concurrency. | Storage/identity/concurrency: [T15](#evidence-t15), [T16](#evidence-t16), [T17](#evidence-t17), [T18d](#evidence-t18d), [T18e](#evidence-t18e). |
| G09 | Actual publisher→formal reader→consumer, independent expected and success/failure result; no logger-as-durability proof, no latest scan; publication/read/empty/nonempty delivery separated. Capture.present, default output, opt-in completion, historical readers. | Entry table; T08/T15/T20; **limited entry semantics accepted on 2026-11-14; remaining decisions pending**. A02 empty not_run reachability and unapproved A03 details stay explicit. All channels failing is unconfirmed/not delivered, never caller success. | Actual formal consumer: [T08](#evidence-t08); delivery/failure: [T20](#evidence-t20); per-entry decision above. |
| G10 | Confirmed published/not_published/unknown; verify commit identity/content, no locator for unknown, no overwrite/automatic retry/old fallback; staging, commit, cleanup and return distinct. PublicationResult and capture attempt ledger. | T15/T17/T18e/T19 exact fault/unknown nodes. **Three-state semantics ACCEPTED only**, by the 2026-11-14 owner chat decision above. Full G10 qualification remains pending; storage/concurrency/cleanup has only the separately recorded bounded support approval. The dated supplements distinguish the standalone false-success replacement from the existing foreign-final UNKNOWN control; uncited nodes/omission controls remain NOT_RUN. No journal/exactly-once/power-loss promise. | Three states/commit: [T15](#evidence-t15), [T18e](#evidence-t18e); conflict: [T17](#evidence-t17); controls: [T19](#evidence-t19). |
| G11 | Pre/post commit and primary/sibling/receipt/replay/setup/read/output/callback exits; preserve primary/control exception identity; bounded owned cleanup; event/join/cwd/capture/artifact/caller facts separate. Owned cwd scope, capture close, completion, deferred signal flow. | T18a–f/T19/T20. #452 fixes callback masking only; **Bounded owned-staging/registered-cwd support accepted on 2026-11-14**, including possible permission residue. Remaining asynchronous/source-lock/unregistered-allocation and FD evidence/dispositions are not approved by implication; this is not full G11 qualification or a demand to change shared lifecycle. | Normal/error/cancel/root/staging/cleanup: [T18a](#evidence-t18a), [T18b](#evidence-t18b), [T18c](#evidence-t18c), [T18d](#evidence-t18d), [T18e](#evidence-t18e), [T18f](#evidence-t18f); control identity [T19](#evidence-t19), delivery [T20](#evidence-t20). |
| G12 | Original-port disposition, real file/node identity including params, independent oracle, exact fault location/class, baseline GREEN/semantic RED/byte-restored GREEN, public identity and safe raw logs; no overlap-count credit. | This static inventory and original-port **UNKNOWN** are inputs only. Only the precise cases and probe cited in dated supplements have their recorded collection/execution results; uncited cases remain NOT_RUN, and PASS without a dedicated omission run supplies no new RED credit. No claim to recovered or equivalent 78-port; versioned G/T organization is accepted on 2026-11-14, while retained obligations and full G12 qualification remain unresolved. | All T01–T20 stable records below; their node-specific evidence slots remain separate; catalogue-organization decision ACCEPTED; evidence completion and full G12 qualification PENDING. |
| G13 | One final product/test/document revision, independent G0 plus explicit maintainer acceptance then G1; retained unresolved domains and draft PR disposition; no retrospective grant from CI/merge. | **Decision pending**, no executable substitute. A08/R4 and #443/#444 unique-net-diff disposition still required; parent #33 remains open. | Final same-revision R4 and maintainer decision PENDING; no executable test substitutes for approval. |

#### T01–T20 and T18a–f: every clause, oracle, gap and expanded identity

The expandable lists are exact committed manifest node IDs, not invented test
counts. They include all parameter expansions of the selected test functions.
A function selected by an exact parameter includes only that parameter; any
remaining missing subclause is written explicitly, never inferred covered by
more node names. The source assertion/fixture descriptions do not assert that
all these nodes execute in this environment.

<a id="evidence-t01"></a>

##### T01

- Obligation: Nonempty diff with no indexed target: no_named_targets, exactly one fixed diagnostic and empty findings.
- Actual source: CrossRepoImpactRunner._run_analysis_impl; DiagnosticCapture.finish/present.
- Independent oracle / input: Real SQLite lacks a matching node; exact message list/findings and retained context/real file/read assertions are independent of serialized identity.
- Remaining gap / supported disposition: Immediate branch and controlled-CI persistent fixture are separate; do not combine them into one unexecuted native path.
- R3 status: Dedicated omission of this branch diagnostic at current revision: NOT_RUN. Historical publication omission is described below, not a fresh T01 run.

<details>
<summary>T01: exact static node identities (execution status: exact dated supplements; otherwise NOT_RUN)</summary>

```text
tests/test_cross_repo_immediate_diagnostic.py::test_zero_targets_emit_once_before_sibling_access[no-matching-node]
tests/test_cross_repo_persistent_diagnostic.py::test_real_cli_publishes_before_output_and_survives_worker_cleanup[FAIL]
tests/test_cross_repo_persistent_diagnostic.py::test_real_cli_publishes_before_output_and_survives_worker_cleanup[PASS]
tests/test_cross_repo_persistent_diagnostic.py::test_real_cli_publishes_before_output_and_survives_worker_cleanup[PENDING]
```

</details>

<a id="evidence-t02"></a>

##### T02

- Obligation: Only unnamed/module-level nodes: do not count them as named targets; preserve exact no-target diagnostic.
- Actual source: resolve_changed_symbols through CrossRepoImpactRunner; native advisory dispatch.
- Independent oracle / input: Module-level graph input plus exact diagnostic; native docs-only gate compares State/credit and receipt bytes for PASS/FAIL.
- Remaining gap / supported disposition: Unsupported-language parser generalization is not qualified. Native fixture controls backend responses, not gate Verdict.
- R3 status: Treat-unnamed-as-target omission/replacement at current revision: NOT_RUN; semantic diagnostic/state oracle must fail, not an earlier malformed graph check.

<details>
<summary>T02: exact static node identities (execution status: exact dated supplements; otherwise NOT_RUN)</summary>

```text
tests/test_cross_repo_diagnostic_gate.py::test_docs_only_native_ci_keeps_gate_state_with_diagnostic[publication-fault-FAIL]
tests/test_cross_repo_diagnostic_gate.py::test_docs_only_native_ci_keeps_gate_state_with_diagnostic[publication-fault-PASS]
tests/test_cross_repo_diagnostic_gate.py::test_docs_only_native_ci_keeps_gate_state_with_diagnostic[published-FAIL]
tests/test_cross_repo_diagnostic_gate.py::test_docs_only_native_ci_keeps_gate_state_with_diagnostic[published-PASS]
tests/test_cross_repo_immediate_diagnostic.py::test_zero_targets_emit_once_before_sibling_access[unnamed]
```

</details>

<a id="evidence-t03"></a>

##### T03

- Obligation: No parseable path/deleted added side: primary path prerequisite remains, no primary connection, no_named_targets; no invented target.
- Actual source: `CrossRepoImpactRunner._run_analysis_impl` first resolves/checks the primary database path, then calls `resolve_changed_symbols`; the latter returns on empty parsed added-side paths before its `sqlite3.connect` (fixed source lines 262–268). The early no-connect branch is in the resolver, not the runner itself.
- Independent oracle / input: Actual malformed/deletion diff, exact warning/empty findings; forbid sentinels guard sibling discovery/helper access.
- Remaining gap / supported disposition: The selected no-path/deleted controls now explicitly count primary URI connection attempts, with unnamed as positive control. Other primary path/persistent-chain obligations are not inferred from this observation.
- R3 status: The 2026-11-14 supplement records unchanged-product GREEN, two target connection-count REDs with positive control GREEN, and restored GREEN on the existing nodes. Only this bounded subclause has current execution credit.

<details>
<summary>T03: exact identities (no-path/deleted executed; unnamed positive control in supplement)</summary>

```text
tests/test_cross_repo_immediate_diagnostic.py::test_zero_targets_emit_once_before_sibling_access[deleted]
tests/test_cross_repo_immediate_diagnostic.py::test_zero_targets_emit_once_before_sibling_access[no-path]
```

</details>

<a id="evidence-t04"></a>

##### T04

- Obligation: With paths, prior provider/registry/primary graph errors remain unavailable, original errors retained, no no-target diagnostic.
- Actual source: CrossRepoImpactRunner native prerequisite order and capture finalization.
- Independent oracle / input: Corrupt/missing/identity/provider/registry fixtures compare original error view and absence of specific warning; persistent analysis_state checked separately.
- Remaining gap / supported disposition: Fixture provider default-path helper is isolated; real optional provider integration and every fault priority are not thereby qualified. #488 malformed SQLite value controls supplement rather than replace this map.
- R3 status: Relabel all empty findings as no-target: dedicated current mutation NOT_RUN; use otherwise valid failing-prerequisite inputs.

<details>
<summary>T04: exact static node identities (execution status: exact dated supplements; otherwise NOT_RUN)</summary>

```text
tests/test_cross_repo_immediate_diagnostic.py::test_prior_unavailability_does_not_claim_zero_targets[corrupt]
tests/test_cross_repo_immediate_diagnostic.py::test_prior_unavailability_does_not_claim_zero_targets[identity]
tests/test_cross_repo_immediate_diagnostic.py::test_prior_unavailability_does_not_claim_zero_targets[missing]
tests/test_cross_repo_immediate_diagnostic.py::test_prior_unavailability_does_not_claim_zero_targets[provider]
tests/test_cross_repo_immediate_diagnostic.py::test_prior_unavailability_does_not_claim_zero_targets[registry]
tests/test_cross_repo_immediate_diagnostic.py::test_unusable_enumeration_preserves_primary_branch_priority[corrupt]
tests/test_cross_repo_immediate_diagnostic.py::test_unusable_enumeration_preserves_primary_branch_priority[missing]
tests/test_cross_repo_immediate_diagnostic.py::test_unusable_enumeration_preserves_primary_branch_priority[named]
tests/test_cross_repo_immediate_diagnostic.py::test_unusable_enumeration_preserves_primary_branch_priority[unnamed]
tests/test_cross_repo_persistent_diagnostic.py::test_actual_analysis_states_do_not_invent_no_target_warning[blank]
tests/test_cross_repo_persistent_diagnostic.py::test_actual_analysis_states_do_not_invent_no_target_warning[missing]
tests/test_cross_repo_persistent_diagnostic.py::test_actual_analysis_states_do_not_invent_no_target_warning[targets]
```

</details>

<a id="evidence-t05"></a>

##### T05

- Obligation: Blank diff is not_requested; no graph/discovery/import and no specific warning.
- Actual source: CrossRepoImpactRunner.run blank branch.
- Independent oracle / input: Blank/whitespace inputs, registry/import sentinels, exact empty output and persistent not_requested state.
- Remaining gap / supported disposition: Persistent fixture supplies CI outcome. No proof of graph freshness follows from not_requested.
- R3 status: Blank-warning or eager-discovery omission/replacement at current revision NOT_RUN; require sentinel/message failure.

<details>
<summary>T05: exact static node identities (execution status: exact dated supplements; otherwise NOT_RUN)</summary>

```text
tests/test_cross_repo_immediate_diagnostic.py::test_blank_diff_never_imports_optional_provider[ \t\n]
tests/test_cross_repo_immediate_diagnostic.py::test_blank_diff_never_imports_optional_provider[]
tests/test_cross_repo_immediate_diagnostic.py::test_empty_diff_has_no_diagnostic_or_discovery[ \t\n]
tests/test_cross_repo_immediate_diagnostic.py::test_empty_diff_has_no_diagnostic_or_discovery[]
tests/test_cross_repo_persistent_diagnostic.py::test_actual_analysis_states_do_not_invent_no_target_warning[blank]
```

</details>

<a id="evidence-t06"></a>

##### T06

- Obligation: Resolved zero/real callers: targets_resolved; no no-target warning; preserve golden finding identity, sorting/top-10 and single-hop SQL.
- Actual source: CrossRepoImpactRunner._run_analysis_impl; caller helper and finding selection.
- Independent oracle / input: Real SQLite caller rows and explicit expected findings/count/cap; cache no-warning assertions for empty and nonempty result.
- Remaining gap / supported disposition: Stable equal-rank ordering is NOT PROMISED by the recorded owner decision and is not a remaining acceptance obligation. Existing top-N and single-hop requirements remain; the two added T06 nodes and bounded run above now provide a chain-exclusion and distance-selection oracle. Mutation-control coverage remains a separate gap, not a new multi-hop feature request.
- Static follow-up: `TestTopNCap::test_cap_at_top_n` builds `_TOP_N + 5` callers but asserts only result length. The production ranking uses stable Python sort by proximity, without an explicit tie-break key; the shared `_CALLERS_SQL` has no `ORDER BY`. This does not establish deterministic equal-rank identity selection across SQLite plans. `_CALLERS_SQL` selects direct CALLS edges for the requested target, with an IMPORTS_FROM predicate; `_live_callers` resolves source locations/liveness, not recursive caller expansion. These source facts are not an executed extra-hop rejection oracle. The 2026-11-14 owner decision does not promise stable equal-rank ordering; no new tie-order test or implementation is required by that scope. The later T06 supplement above names the added exact node and its limited positive evidence; no omission/extra-hop mutation credit is inferred.
- R3 status: Blanket empty-result warning, cap removal, extra-hop insertion: dedicated current controls NOT_RUN; historical finding regressions are not per-mutation proof.

<details>
<summary>T06: exact static node identities (execution status: exact dated supplements; otherwise NOT_RUN)</summary>

```text
tests/test_cross_repo_immediate_diagnostic.py::test_resolved_targets_keep_findings_and_cache_without_zero_target_output[False]
tests/test_cross_repo_immediate_diagnostic.py::test_resolved_targets_keep_findings_and_cache_without_zero_target_output[True]
tests/test_cross_repo_impact.py::TestFindCrossRepoCallers::test_matching_caller_found
tests/test_cross_repo_impact.py::TestFindCrossRepoCallers::test_unrelated_symbol_no_callers
tests/test_cross_repo_impact.py::TestGenuineNoCallers::test_no_callers_empty_infra_errors
tests/test_cross_repo_impact.py::TestTopNCap::test_cap_at_top_n
```

</details>

<a id="evidence-t07"></a>

##### T07

- Obligation: Zero targets must not read override registry/connect sibling; allowed default discovery and original priority remain.
- Actual source: CrossRepoImpactRunner branch-local discovery and stage capture.
- Independent oracle / input: Forbid sentinels on enumeration/caller helper and exact native errors/messages; invalid unused override does not become an early error.
- Remaining gap / supported disposition: Sentinels prove selected calls were not made, not all possible filesystem/syscall activity.
- R3 status: Eager override/sibling access at current revision NOT_RUN; expected refusal must arise at intended sentinel, not setup.

<details>
<summary>T07: exact static node identities (execution status: exact dated supplements; otherwise NOT_RUN)</summary>

```text
tests/test_cross_repo_immediate_diagnostic.py::test_registered_primary_data_dir_does_not_interpret_unused_override
tests/test_cross_repo_immediate_diagnostic.py::test_unusable_enumeration_preserves_primary_branch_priority[corrupt]
tests/test_cross_repo_immediate_diagnostic.py::test_unusable_enumeration_preserves_primary_branch_priority[missing]
tests/test_cross_repo_immediate_diagnostic.py::test_unusable_enumeration_preserves_primary_branch_priority[named]
tests/test_cross_repo_immediate_diagnostic.py::test_unusable_enumeration_preserves_primary_branch_priority[unnamed]
tests/test_cross_repo_immediate_diagnostic.py::test_zero_targets_emit_once_before_sibling_access[deleted]
tests/test_cross_repo_immediate_diagnostic.py::test_zero_targets_emit_once_before_sibling_access[no-matching-node]
tests/test_cross_repo_immediate_diagnostic.py::test_zero_targets_emit_once_before_sibling_access[no-path]
tests/test_cross_repo_immediate_diagnostic.py::test_zero_targets_emit_once_before_sibling_access[unnamed]
```

</details>

<a id="evidence-t08"></a>

##### T08

- Obligation: Real runner to production publisher, persistent file and formal reader; independent host expectation; no hand-built substitute.
- Actual source: run_cross_repo primary worker; DiagnosticCapture.finish/present; publish_observation/read_observation.
- Independent oracle / input: Hold context before writing; delegate real publication/read; exact tuples/counts/artifact bytes, marker from strict reader return, real CLI owner thread; native chain checks output/receipts.
- Remaining gap / supported disposition: Controlled-CI persistent/CLI-handoff fixtures and native docs-only MCP-handler cases are separate domains; native live chain is not SDK/stdio qualification.
- R3 status: The caller ticket-replacement node below now has GREEN/target caller-read omission RED/restored GREEN in the bounded supplement. Other publisher/read-consumer runtime evidence is limited to exact nodes in dated supplements; uncited nodes remain NOT_RUN and historical #491 evidence remains separate.

<details>
<summary>T08: exact identities (execution status: exact dated supplements; otherwise NOT_RUN)</summary>

```text
tests/test_cross_repo_native_consumer_chain.py::test_native_mcp_cli_diagnostic[FAIL-background]
tests/test_cross_repo_native_consumer_chain.py::test_native_mcp_cli_diagnostic[FAIL-immediate]
tests/test_cross_repo_native_consumer_chain.py::test_native_mcp_cli_diagnostic[PASS-background]
tests/test_cross_repo_native_consumer_chain.py::test_native_mcp_cli_diagnostic[PASS-immediate]
tests/test_cross_repo_persistent_diagnostic.py::test_actual_strict_reader_return_controls_cli_text
tests/test_cross_repo_persistent_diagnostic.py::test_host_publication_strictly_reads_after_temporary_cwds_are_removed
tests/test_diagnostic_completion.py::test_actual_cli_strict_reader_uses_owner_tuple_on_calling_thread
tests/test_diagnostic_completion.py::test_ticket_replacement_does_not_rebind_cli_expected
```

</details>

<a id="evidence-t09"></a>

##### T09

- Obligation: Uncached transition from unevaluated to resolved publishes current empty diagnostics with new observation; no old-file fallback.
- Actual source: DiagnosticCapture.finish; runner uncached-final cache binding.
- Independent oracle / input: Actual stage transitions, sequence/record identity, diagnostics and cache origin; previously captured state not reconstructed from empty findings.
- Remaining gap / supported disposition: Same-instance transition and separate new-host execution are distinct; T10 supplies new-host evidence.
- R3 status: Skip empty-result publication or adopt old record at current revision NOT_RUN; dedicated targeted semantic assertion/log still required.

<details>
<summary>T09: exact static node identities (execution status: exact dated supplements; otherwise NOT_RUN)</summary>

```text
tests/test_cross_repo_immediate_diagnostic.py::test_zero_targets_remain_uncached_and_later_errors_or_targets_are_not_relabelled
tests/test_cross_repo_persistent_diagnostic.py::test_uncached_branch_transitions_get_new_records_and_cache_reuses_publication
```

</details>

<a id="evidence-t10"></a>

##### T10

- Obligation: Same source hash/diff in two hosts: fresh execution/repository IDs and namespace; reject old publication under new expectation.
- Actual source: run_cross_repo UUID issuance; DiagnosticCapture; strict context validation.
- Independent oracle / input: Retain independently issued first/second contexts, equal source/diff, unequal IDs and real read refusal on old publication.
- Remaining gap / supported disposition: UUID correlation is not authentication or HOLD recovery.
- R3 status: Source-hash-only acceptance omission at current revision NOT_RUN; codec field controls are narrower and cannot replace host issuance mutation proof.

<details>
<summary>T10: exact static node identities (execution status: exact dated supplements; otherwise NOT_RUN)</summary>

```text
tests/test_advisory_diagnostics.py::test_new_execution_with_identical_source_and_diff_cannot_reuse_record
tests/test_cross_repo_persistent_diagnostic.py::test_host_publication_strictly_reads_after_temporary_cwds_are_removed
tests/test_cross_repo_persistent_diagnostic.py::test_new_host_execution_never_reuses_same_source_hash_identity
```

</details>

<a id="evidence-t11"></a>

##### T11

- Obligation: Same-input cache: reuse original observation/sequence/publication/errors; no new SQL/publication; separate cache delivery; uncached early returns stay actual; caller list mutation cannot alter retained data.
- Actual source: runner cache and DiagnosticCapture records/delivery.
- Independent oracle / input: Real SQL counters, original object/byte identity, explicit old/new errors, cleared outward list followed by intact replay, stage dictionaries defensively copied.
- Remaining gap / supported disposition: Supported finding/error/configuration types and budgets only; not arbitrary nested-object immutability or all ambient changes.
- R3 status: Historical cache-origin/admission omissions are separate fixed sources; the dated cache-republication and cached-reader invocation controls are now executed; the rest of the T11 omission/restoration set remains NOT_RUN and is not assumed exhaustive.

<details>
<summary>T11: exact static node identities (only the dated node/control slices executed)</summary>

```text
tests/test_cross_repo_capture.py::test_active_owner_native_cold_and_warm_delivery
tests/test_cross_repo_capture.py::test_duplicate_occurrences_retain_distinct_first_use_native_fallbacks
tests/test_cross_repo_capture.py::test_large_final_cache_errors_do_not_restore_previous_uncached_error
tests/test_cross_repo_capture.py::test_registry_stage_returns_defensive_copies
tests/test_cross_repo_persistent_diagnostic.py::test_uncached_branch_transitions_get_new_records_and_cache_reuses_publication
```

</details>

<a id="evidence-t12"></a>

##### T12

- Obligation: Refuse changed capture/execution/diff/lexical or canonical root before SQL/error clearing/publication; stage-effective consumed config reused, unconsumed inputs lazy, new capture rediscovery, bounded failure never repeats native operation.
- Actual source: DiagnosticCapture.admit/stage retention; CrossRepoImpactRunner.run.
- Independent oracle / input: Held cache/errors/records plus SQL/publication/read counters; real foreign thread with bounded join; stage native-call counters; original context retained before fault.
- Remaining gap / supported disposition: Limited G06 accepted stage-effective rule replaces eager reread; graph freshness, simultaneous shared-runner use and timed-out thread cleanup excluded. Exact process/fork coverage remains separate if not matched by selectors.
- R3 status: Three changed-input warm-cache parameters now have current GREEN/target admission-omission RED/restored GREEN in the bounded supplement above. Other #482 guards/installed-capture cases remain NOT_RUN here. Thread timeout/harness failure never semantic RED.

<details>
<summary>T12: exact identities (execution status: exact dated supplements; otherwise NOT_RUN)</summary>

```text
tests/test_cross_repo_capture.py::test_capture_limit_never_repeats_the_native_operation[bytes]
tests/test_cross_repo_capture.py::test_capture_limit_never_repeats_the_native_operation[entries]
tests/test_cross_repo_capture.py::test_capture_limit_never_repeats_the_native_operation[stages]
tests/test_cross_repo_capture.py::test_changed_capture_refuses_before_cached_findings_queries_or_error_clear[execution]
tests/test_cross_repo_capture.py::test_changed_capture_refuses_before_cached_findings_queries_or_error_clear[remove]
tests/test_cross_repo_capture.py::test_changed_capture_refuses_before_cached_findings_queries_or_error_clear[replace]
tests/test_cross_repo_capture.py::test_changed_capture_refuses_before_cached_findings_queries_or_error_clear[source]
tests/test_cross_repo_capture.py::test_changed_capture_refuses_before_cached_findings_queries_or_error_clear[stage-store]
tests/test_cross_repo_capture.py::test_changed_capture_refuses_before_cached_findings_queries_or_error_clear[stage]
tests/test_cross_repo_capture.py::test_changed_input_refuses_before_cache_sql_error_clear_or_publication[canonical-root]
tests/test_cross_repo_capture.py::test_changed_input_refuses_before_cache_sql_error_clear_or_publication[exact-diff]
tests/test_cross_repo_capture.py::test_changed_input_refuses_before_cache_sql_error_clear_or_publication[lexical-root]
tests/test_cross_repo_capture.py::test_discovery_failure_is_retained_without_exception_graphs_or_native_retry
tests/test_cross_repo_capture.py::test_failure_capture_limit_preserves_original_error_without_retry
tests/test_cross_repo_capture.py::test_foreign_record_cannot_supply_expected_context_or_no_target_presentation
tests/test_cross_repo_capture.py::test_installed_capture_admission_precedes_native_work[closed-installed-cold]
tests/test_cross_repo_capture.py::test_installed_capture_admission_precedes_native_work[closed-installed-warm]
tests/test_cross_repo_capture.py::test_installed_capture_admission_precedes_native_work[foreign-thread-cold]
tests/test_cross_repo_capture.py::test_installed_capture_admission_precedes_native_work[foreign-thread-warm]
tests/test_cross_repo_capture.py::test_literal_registry_alias_keeps_distinct_snapshots_and_uncached_graph_retry
tests/test_scope_admission_inputs.py::test_input_refusal_preserves_scope_and_ticket_evidence[canonical-root-cold]
tests/test_scope_admission_inputs.py::test_input_refusal_preserves_scope_and_ticket_evidence[canonical-root-warm]
tests/test_scope_admission_inputs.py::test_input_refusal_preserves_scope_and_ticket_evidence[exact-diff-cold]
tests/test_scope_admission_inputs.py::test_input_refusal_preserves_scope_and_ticket_evidence[exact-diff-warm]
tests/test_scope_admission_inputs.py::test_input_refusal_preserves_scope_and_ticket_evidence[lexical-root-cold]
tests/test_scope_admission_inputs.py::test_input_refusal_preserves_scope_and_ticket_evidence[lexical-root-warm]
```

</details>

<a id="evidence-t13"></a>

##### T13

- Obligation: Naturally reachable PASS/FAIL with advisory: no diagnostic in infra_errors/findings, unchanged Verdict/credit/receipt decisions and bytes.
- Actual source: StateMachine real CI/advisory path; run_cross_repo; CLI dispatch.
- Independent oracle / input: Native docs-only PASS/FAIL, real State membership/equality and receipt SHA/bytes; deterministic executable backend does not set Verdict.
- Remaining gap / supported disposition: No model-quality, code mutation workload, every gate or live SDK transport claim. Existing controlled persistent fixture is not a substitute for these native gates.
- R3 status: #451 diagnostic-to-State omission reports semantic membership failures; current exact revision/log and restored replay NOT_RUN.

<details>
<summary>T13: exact static node identities (execution status: exact dated supplements; otherwise NOT_RUN)</summary>

```text
tests/test_cross_repo_diagnostic_gate.py::test_docs_only_native_ci_keeps_gate_state_with_diagnostic[publication-fault-FAIL]
tests/test_cross_repo_diagnostic_gate.py::test_docs_only_native_ci_keeps_gate_state_with_diagnostic[publication-fault-PASS]
tests/test_cross_repo_diagnostic_gate.py::test_docs_only_native_ci_keeps_gate_state_with_diagnostic[published-FAIL]
tests/test_cross_repo_diagnostic_gate.py::test_docs_only_native_ci_keeps_gate_state_with_diagnostic[published-PASS]
tests/test_cross_repo_diagnostic_gate.py::test_docs_only_native_ci_tracks_nonempty_delivery[all-fail-FAIL]
tests/test_cross_repo_diagnostic_gate.py::test_docs_only_native_ci_tracks_nonempty_delivery[all-fail-PASS]
tests/test_cross_repo_diagnostic_gate.py::test_docs_only_native_ci_tracks_nonempty_delivery[diagnostic-fails-FAIL]
tests/test_cross_repo_diagnostic_gate.py::test_docs_only_native_ci_tracks_nonempty_delivery[diagnostic-fails-PASS]
tests/test_cross_repo_diagnostic_gate.py::test_docs_only_native_ci_tracks_nonempty_delivery[ok-FAIL]
tests/test_cross_repo_diagnostic_gate.py::test_docs_only_native_ci_tracks_nonempty_delivery[ok-PASS]
tests/test_cross_repo_native_consumer_chain.py::test_native_mcp_cli_diagnostic[FAIL-background]
tests/test_cross_repo_native_consumer_chain.py::test_native_mcp_cli_diagnostic[FAIL-immediate]
tests/test_cross_repo_native_consumer_chain.py::test_native_mcp_cli_diagnostic[PASS-background]
tests/test_cross_repo_native_consumer_chain.py::test_native_mcp_cli_diagnostic[PASS-immediate]
tests/test_cross_repo_native_consumer_chain.py::test_native_mcp_cli_read_loss_is_non_gating[FAIL]
tests/test_cross_repo_native_consumer_chain.py::test_native_mcp_cli_read_loss_is_non_gating[PASS]
```

</details>

<a id="evidence-t14"></a>

##### T14

- Obligation: New host identity and no prior-record reuse; explicitly dispose original HOLD-reentry expectation by supported lifecycle.
- Actual source: run_cross_repo PENDING-to-FAIL; single-repo HOLD path excludes this axis.
- Independent oracle / input: Independent new-host context; compatibility test returns PENDING via controlled machine and checks FAIL/warning.
- Remaining gap / supported disposition: Cross-repository HOLD/recovery UNSUPPORTED by accepted G05. Controlled PENDING is branch compatibility, not natural HOLD execution. The 2026-11-14 owner decision above records this T14 disposition, without recovery credit.
- R3 status: No HOLD mutation scheduled for unsupported path. Identity omission belongs T10; pending current verification NOT_RUN.

<details>
<summary>T14: exact static node identities (execution status: exact dated supplements; otherwise NOT_RUN)</summary>

```text
tests/test_cross_repo.py::test_pending_escalates_to_fail
tests/test_cross_repo_persistent_diagnostic.py::test_new_host_execution_never_reuses_same_source_hash_identity
```

</details>

<a id="evidence-t15"></a>

##### T15

- Obligation: Open/write/partial write/link errors: no successful locator without verified commit, host retains failure, Verdict unchanged; no old fallback/full State save.
- Actual source: publish_observation; DiagnosticCapture.finish; native gate diagnostic error path.
- Independent oracle / input: Controlled exact file-operation faults; actual file identity/bytes; status/locator and no retry; independent native State/receipt oracle.
- Remaining gap / supported disposition: Library fault table and native publish-fault path do not produce every Cartesian host fault × gate combination. No crash journal or exactly-once claim.
- R3 status: Current per-boundary fake-published/fallback/State-save omissions NOT_RUN; identify missing dedicated selector before claiming each prohibited behavior discriminated.

<details>
<summary>T15: exact static node identities (execution status: exact dated supplements; otherwise NOT_RUN)</summary>

```text
tests/test_advisory_diagnostics.py::test_ambiguous_foreign_final_after_link_error_is_never_adopted
tests/test_advisory_diagnostics.py::test_commit_then_unreadable_remains_unknown_without_retry
tests/test_advisory_diagnostics.py::test_incomplete_write_never_publishes_and_removes_owned_temp[partial-error]
tests/test_advisory_diagnostics.py::test_incomplete_write_never_publishes_and_removes_owned_temp[zero]
tests/test_advisory_diagnostics.py::test_link_error_distinguishes_verified_commit_from_unknown[False]
tests/test_advisory_diagnostics.py::test_link_error_distinguishes_verified_commit_from_unknown[True]
tests/test_advisory_diagnostics.py::test_precommit_operation_failure_is_not_published[fstat]
tests/test_advisory_diagnostics.py::test_precommit_operation_failure_is_not_published[getxattr]
tests/test_advisory_diagnostics.py::test_precommit_operation_failure_is_not_published[mkdir]
tests/test_advisory_diagnostics.py::test_precommit_operation_failure_is_not_published[open]
tests/test_advisory_diagnostics.py::test_precommit_operation_failure_is_not_published[write]
tests/test_advisory_diagnostics.py::test_short_write_is_completed_without_republishing
tests/test_cross_repo_diagnostic_gate.py::test_docs_only_native_ci_keeps_gate_state_with_diagnostic[publication-fault-FAIL]
tests/test_cross_repo_diagnostic_gate.py::test_docs_only_native_ci_keeps_gate_state_with_diagnostic[publication-fault-PASS]
tests/test_cross_repo_diagnostic_gate.py::test_docs_only_native_ci_keeps_gate_state_with_diagnostic[published-FAIL]
tests/test_cross_repo_diagnostic_gate.py::test_docs_only_native_ci_keeps_gate_state_with_diagnostic[published-PASS]
```

</details>

<a id="evidence-t16"></a>

##### T16

- Obligation: Strict type/field/version/legal states, full JSON/encoding/duplicate/nonfinite/depth/bytes, external execution/repo/source/diff/sequence, actual file identity/content; never empty-success refusal.
- Actual source: decode_observation/validate_observation/validate_publication/read_observation.
- Independent oracle / input: Otherwise legal baseline plus one field/boundary change, external expected from fixture/host before serialization; real decoder and reader; pre-parser sentinel separates parser-budget check.
- Remaining gap / supported disposition: Codec-vs-reader guards can mask each other; parameter existence is not exclusive guard discrimination. Full original 78-port cannot be inferred from this inventory.
- R3 status: #476 codec duplicate/byte/context reports exist; exhaustive per-validator current omission set UNKNOWN/NOT_RUN. Need selector-specific fault/oracle/log before R3 credit.

<details>
<summary>T16: exact static node identities (execution status: exact dated supplements; otherwise NOT_RUN)</summary>

```text
tests/test_advisory_diagnostics.py::test_decoder_rejects_each_valid_foreign_expected_context[diff_sha256-ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff]
tests/test_advisory_diagnostics.py::test_decoder_rejects_each_valid_foreign_expected_context[execution_id-aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa]
tests/test_advisory_diagnostics.py::test_decoder_rejects_each_valid_foreign_expected_context[observation_sequence-2]
tests/test_advisory_diagnostics.py::test_decoder_rejects_each_valid_foreign_expected_context[repository_id-bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb]
tests/test_advisory_diagnostics.py::test_decoder_rejects_each_valid_foreign_expected_context[source_hash-independent-other-source]
tests/test_advisory_diagnostics.py::test_duplicate_keys_in_otherwise_valid_document_rejected[diagnostic]
tests/test_advisory_diagnostics.py::test_duplicate_keys_in_otherwise_valid_document_rejected[repository]
tests/test_advisory_diagnostics.py::test_duplicate_keys_in_otherwise_valid_document_rejected[root]
tests/test_advisory_diagnostics.py::test_every_closed_state_round_trips_after_publisher_descriptors_close[no_named_targets]
tests/test_advisory_diagnostics.py::test_every_closed_state_round_trips_after_publisher_descriptors_close[not_requested]
tests/test_advisory_diagnostics.py::test_every_closed_state_round_trips_after_publisher_descriptors_close[not_run]
tests/test_advisory_diagnostics.py::test_every_closed_state_round_trips_after_publisher_descriptors_close[targets_resolved]
tests/test_advisory_diagnostics.py::test_every_closed_state_round_trips_after_publisher_descriptors_close[unavailable]
tests/test_advisory_diagnostics.py::test_every_missing_field_is_rejected[analysis_state]
tests/test_advisory_diagnostics.py::test_every_missing_field_is_rejected[axis]
tests/test_advisory_diagnostics.py::test_every_missing_field_is_rejected[diagnostics]
tests/test_advisory_diagnostics.py::test_every_missing_field_is_rejected[diff_sha256]
tests/test_advisory_diagnostics.py::test_every_missing_field_is_rejected[execution_id]
tests/test_advisory_diagnostics.py::test_every_missing_field_is_rejected[observation_sequence]
tests/test_advisory_diagnostics.py::test_every_missing_field_is_rejected[repository_binding]
tests/test_advisory_diagnostics.py::test_every_missing_field_is_rejected[schema_version]
tests/test_advisory_diagnostics.py::test_every_missing_field_is_rejected[source_hash]
tests/test_advisory_diagnostics.py::test_extra_fields_rejected_at_each_object_level[diagnostic]
tests/test_advisory_diagnostics.py::test_extra_fields_rejected_at_each_object_level[repository]
tests/test_advisory_diagnostics.py::test_extra_fields_rejected_at_each_object_level[root]
tests/test_advisory_diagnostics.py::test_fixed_diagnostic_cannot_be_fabricated[code]
tests/test_advisory_diagnostics.py::test_fixed_diagnostic_cannot_be_fabricated[message]
tests/test_advisory_diagnostics.py::test_fixed_diagnostic_cannot_be_fabricated[second]
tests/test_advisory_diagnostics.py::test_large_or_deep_document_rejected_before_json_load[bytes]
tests/test_advisory_diagnostics.py::test_large_or_deep_document_rejected_before_json_load[depth]
tests/test_advisory_diagnostics.py::test_malformed_and_nonfinite_json_rejected[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[]
tests/test_advisory_diagnostics.py::test_malformed_and_nonfinite_json_rejected[[]]
tests/test_advisory_diagnostics.py::test_malformed_and_nonfinite_json_rejected[\xef\xbb\xbf{}]
tests/test_advisory_diagnostics.py::test_malformed_and_nonfinite_json_rejected[\xff]
tests/test_advisory_diagnostics.py::test_malformed_and_nonfinite_json_rejected[null]
tests/test_advisory_diagnostics.py::test_malformed_and_nonfinite_json_rejected[{"x":"\ud800"}]
tests/test_advisory_diagnostics.py::test_malformed_and_nonfinite_json_rejected[{"x":"\udfff"}]
tests/test_advisory_diagnostics.py::test_malformed_and_nonfinite_json_rejected[{"x":-Infinity}]
tests/test_advisory_diagnostics.py::test_malformed_and_nonfinite_json_rejected[{"x":1,"x":2}]
tests/test_advisory_diagnostics.py::test_malformed_and_nonfinite_json_rejected[{"x":12345678901234567890}]
tests/test_advisory_diagnostics.py::test_malformed_and_nonfinite_json_rejected[{"x":1e309}]
tests/test_advisory_diagnostics.py::test_malformed_and_nonfinite_json_rejected[{"x":Infinity}]
tests/test_advisory_diagnostics.py::test_malformed_and_nonfinite_json_rejected[{"x":NaN}]
tests/test_advisory_diagnostics.py::test_malformed_and_nonfinite_json_rejected[{"x":[[[]]]}]
tests/test_advisory_diagnostics.py::test_malformed_and_nonfinite_json_rejected[{"x":{"y":1,"y":2}}]
tests/test_advisory_diagnostics.py::test_malformed_and_nonfinite_json_rejected[{]
tests/test_advisory_diagnostics.py::test_malformed_and_nonfinite_json_rejected[{} trailing]
tests/test_advisory_diagnostics.py::test_only_no_named_targets_may_have_diagnostic[not_requested]
tests/test_advisory_diagnostics.py::test_only_no_named_targets_may_have_diagnostic[not_run]
tests/test_advisory_diagnostics.py::test_only_no_named_targets_may_have_diagnostic[targets_resolved]
tests/test_advisory_diagnostics.py::test_only_no_named_targets_may_have_diagnostic[unavailable]
tests/test_advisory_diagnostics.py::test_reader_calls_actual_strict_codec_not_merely_digest
tests/test_advisory_diagnostics.py::test_reader_rejects_leaf_identity_content_and_nonregular_files[directory]
tests/test_advisory_diagnostics.py::test_reader_rejects_leaf_identity_content_and_nonregular_files[fifo]
tests/test_advisory_diagnostics.py::test_reader_rejects_leaf_identity_content_and_nonregular_files[missing]
tests/test_advisory_diagnostics.py::test_reader_rejects_leaf_identity_content_and_nonregular_files[modify]
tests/test_advisory_diagnostics.py::test_reader_rejects_leaf_identity_content_and_nonregular_files[replace]
tests/test_advisory_diagnostics.py::test_reader_rejects_leaf_identity_content_and_nonregular_files[symlink]
tests/test_advisory_diagnostics.py::test_schema_wrong_types_versions_enums_combinations[analysis_state-None]
tests/test_advisory_diagnostics.py::test_schema_wrong_types_versions_enums_combinations[analysis_state-success]
tests/test_advisory_diagnostics.py::test_schema_wrong_types_versions_enums_combinations[axis-other]
tests/test_advisory_diagnostics.py::test_schema_wrong_types_versions_enums_combinations[diagnostics-None]
tests/test_advisory_diagnostics.py::test_schema_wrong_types_versions_enums_combinations[diagnostics-value10]
tests/test_advisory_diagnostics.py::test_schema_wrong_types_versions_enums_combinations[diagnostics-value9]
tests/test_advisory_diagnostics.py::test_schema_wrong_types_versions_enums_combinations[repository_binding-None]
tests/test_advisory_diagnostics.py::test_schema_wrong_types_versions_enums_combinations[repository_binding-value12]
tests/test_advisory_diagnostics.py::test_schema_wrong_types_versions_enums_combinations[schema_version-1.0]
tests/test_advisory_diagnostics.py::test_schema_wrong_types_versions_enums_combinations[schema_version-1]
tests/test_advisory_diagnostics.py::test_schema_wrong_types_versions_enums_combinations[schema_version-2]
tests/test_advisory_diagnostics.py::test_schema_wrong_types_versions_enums_combinations[schema_version-None]
tests/test_advisory_diagnostics.py::test_schema_wrong_types_versions_enums_combinations[schema_version-True]
tests/test_advisory_diagnostics.py::test_schema_wrong_types_versions_enums_combinations[source_hash-\ud800]
tests/test_advisory_diagnostics.py::test_schema_wrong_types_versions_enums_combinations[source_hash-\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9]
tests/test_advisory_diagnostics.py::test_wire_byte_budget_on_otherwise_valid_document[4096]
tests/test_advisory_diagnostics.py::test_wire_byte_budget_on_otherwise_valid_document[4097]
tests/test_advisory_diagnostics.py::test_wrong_independent_context_never_returns_empty_success[diff_sha256]
tests/test_advisory_diagnostics.py::test_wrong_independent_context_never_returns_empty_success[execution_id]
tests/test_advisory_diagnostics.py::test_wrong_independent_context_never_returns_empty_success[observation_sequence]
tests/test_advisory_diagnostics.py::test_wrong_independent_context_never_returns_empty_success[repository_id]
tests/test_advisory_diagnostics.py::test_wrong_independent_context_never_returns_empty_success[source_hash]
```

</details>

<a id="evidence-t17"></a>

##### T17

- Obligation: Different executions isolated; same observation duplicate rejected without overwrite; foreign staging preserved; no latest authority.
- Actual source: exclusive publisher hard-link commit and owned cleanup.
- Independent oracle / input: Real concurrent publisher calls with controlled scheduling, winner bytes, loser unknown/no locator, foreign pending bytes; harness failures fail test rather than become product unknown.
- Remaining gap / supported disposition: These are library overlap controls, not simultaneous complete host lifecycles; same-key contender is adversarial input, not a natural host UUID collision.
- R3 status: #456 four source-specific fault controls reported in contention doc (two omissions, one replacement and one assignment insertion); current replay NOT_RUN. Full host concurrency remains decision/mapping gap, not automatically required by all future domains.

<details>
<summary>T17: exact static node identities (execution status: exact dated supplements; otherwise NOT_RUN)</summary>

```text
tests/test_advisory_diagnostics.py::test_new_execution_with_identical_source_and_diff_cannot_reuse_record
tests/test_advisory_diagnostics.py::test_same_key_always_refused_without_replacing_original[False]
tests/test_advisory_diagnostics.py::test_same_key_always_refused_without_replacing_original[True]
tests/test_publication_contention.py::test_concurrent_distinct_execution_publications_remain_independent
tests/test_publication_contention.py::test_concurrent_publishers_preserve_foreign_pending_replacement
tests/test_publication_contention.py::test_concurrent_same_key_refuses_contender_without_replacing_winner
tests/test_publication_contention.py::test_contention_harness_reports_failures_instead_of_product_unknown[barrier]
tests/test_publication_contention.py::test_contention_harness_reports_failures_instead_of_product_unknown[worker]
tests/test_publication_contention.py::test_contention_harness_reports_failures_instead_of_product_unknown[wrapper]
```

</details>

<a id="evidence-t18"></a>

##### T18 (umbrella)

T18 is expanded into T18a–f below using the [original P1 supplement](https://github.com/HouMinXi/forge/issues/33#issuecomment-6742850246). Publication, cleanup attempts, actual deletion and post-cleanup readability are separate assertions, not interchangeable evidence. In the repeated pending-body family, primary-* directly maps to T18b; replay/primary-interrupt/primary-exit maps to T18c. Second-start is an adjacent startup boundary; receipt and other listed parameters are companion regressions, not independent coverage of each subclause.

<a id="evidence-t18a"></a>

##### T18a

- Obligation: Normal host cleanup completes; actual per_cwd absent; persistent artifact remains strictly readable.
- Actual source: run_cross_repo owned cwd scope; DiagnosticCapture.close/read.
- Independent oracle / input: Assert each recorded cwd does not exist, worker joins, reread using held context after cleanup; caller slot only after event.
- Remaining gap / supported disposition: Capture cleanup/publisher staging cleanup does not establish cwd deletion or complete CLI success. Persistent handoff fixture has controlled CI; native cleanup test is separate.
- R3 status: Only-per_cwd publication/omitted transfer or delete-boundary controls at current revision NOT_RUN; no claim that checking one cleanup status proves all resources.

<details>
<summary>T18a: exact static node identities (execution status: exact dated supplements; otherwise NOT_RUN)</summary>

```text
tests/test_cross_repo_cleanup_priority.py::test_normal_return_deletes_both_cwds[FAIL]
tests/test_cross_repo_cleanup_priority.py::test_normal_return_deletes_both_cwds[PASS]
tests/test_cross_repo_persistent_diagnostic.py::test_host_publication_strictly_reads_after_temporary_cwds_are_removed
tests/test_diagnostic_completion.py::test_cli_consumes_original_publication_after_worker_cleanup
```

</details>

<a id="evidence-t18b"></a>

##### T18b

- Obligation: Primary error before receipt collection: preserve original exception, actual cleanup attempts and readable prior publication or explicitly absent observation.
- Actual source: primary worker error; coordinator receipt boundary; owned cwd scope.
- Independent oracle / input: Exact exception object, per-callback/join/residue checks; pre-axis no records/not_attempted; prior real artifact preserved after later failure.
- Remaining gap / supported disposition: Controlled pre-axis failure is not evidence every natural primary error reaches same phase; returned worker status is not full coordinator completion.
- R3 status: #452 callback-priority baseline RED reported; current per-exit replay NOT_RUN. Unregistered allocations/source-lock cleanup separate gaps.

<details>
<summary>T18b: exact static node identities (execution status: exact dated supplements; otherwise NOT_RUN)</summary>

```text
tests/test_cross_repo_cleanup_priority.py::test_pending_body_failure_survives_cleanup[primary-error-FAIL]
tests/test_cross_repo_cleanup_priority.py::test_pending_body_failure_survives_cleanup[primary-error-PASS]
tests/test_cross_repo_cleanup_priority.py::test_pending_body_failure_survives_cleanup[primary-exit-FAIL]
tests/test_cross_repo_cleanup_priority.py::test_pending_body_failure_survives_cleanup[primary-exit-PASS]
tests/test_cross_repo_cleanup_priority.py::test_pending_body_failure_survives_cleanup[primary-interrupt-FAIL]
tests/test_cross_repo_cleanup_priority.py::test_pending_body_failure_survives_cleanup[primary-interrupt-PASS]
tests/test_cross_repo_cleanup_priority.py::test_pending_body_failure_survives_cleanup[receipt-FAIL]
tests/test_cross_repo_cleanup_priority.py::test_pending_body_failure_survives_cleanup[receipt-PASS]
tests/test_cross_repo_cleanup_priority.py::test_pending_body_failure_survives_cleanup[replay-FAIL]
tests/test_cross_repo_cleanup_priority.py::test_pending_body_failure_survives_cleanup[replay-PASS]
tests/test_cross_repo_cleanup_priority.py::test_pending_body_failure_survives_cleanup[second-start-FAIL]
tests/test_cross_repo_cleanup_priority.py::test_pending_body_failure_survives_cleanup[second-start-PASS]
tests/test_cross_repo_persistent_diagnostic.py::test_pre_axis_failure_has_no_fabricated_observation[KeyboardInterrupt]
tests/test_cross_repo_persistent_diagnostic.py::test_pre_axis_failure_has_no_fabricated_observation[RuntimeError]
tests/test_cross_repo_persistent_diagnostic.py::test_pre_axis_failure_has_no_fabricated_observation[SystemExit]
tests/test_diagnostic_completion.py::test_prior_publication_survives_worker_error_and_notice_control[error0]
tests/test_diagnostic_completion.py::test_prior_publication_survives_worker_error_and_notice_control[error1]
tests/test_diagnostic_completion.py::test_prior_publication_survives_worker_error_and_notice_control[error2]
```

</details>

<a id="evidence-t18c"></a>

##### T18c

- Obligation: Cancellation/signal replay: same control exception, completed/joined workers where reached, bounded owned cleanup and no fake not_run.
- Actual source: run_cross_repo signal replay and worker completion; capture finalization.
- Independent oracle / input: Replay/input boundary injection and identity comparison; observed real artifact remains readable only when genuinely committed.
- Remaining gap / supported disposition: Replay controls directly invoke the installed signal handler, not asynchronous OS signal delivery. Injected KeyboardInterrupt/SystemExit are not all real signal timings; callback-external interruption can stop further cleanup; forced death unsupported. Persistent output/read controls use a CI-verdict fixture; the read-fault control replaces the reader, so payload preservation is not strict reread.
- R3 status: Current per-stage cancel suppression/primary replacement controls NOT_RUN; old trace-timed owner SIGINT is not complete-host proof.

<details>
<summary>T18c: exact static node identities (execution status: exact dated supplements; otherwise NOT_RUN)</summary>

```text
tests/test_cross_repo_cleanup_priority.py::test_pending_body_failure_survives_cleanup[primary-error-FAIL]
tests/test_cross_repo_cleanup_priority.py::test_pending_body_failure_survives_cleanup[primary-error-PASS]
tests/test_cross_repo_cleanup_priority.py::test_pending_body_failure_survives_cleanup[primary-exit-FAIL]
tests/test_cross_repo_cleanup_priority.py::test_pending_body_failure_survives_cleanup[primary-exit-PASS]
tests/test_cross_repo_cleanup_priority.py::test_pending_body_failure_survives_cleanup[primary-interrupt-FAIL]
tests/test_cross_repo_cleanup_priority.py::test_pending_body_failure_survives_cleanup[primary-interrupt-PASS]
tests/test_cross_repo_cleanup_priority.py::test_pending_body_failure_survives_cleanup[receipt-FAIL]
tests/test_cross_repo_cleanup_priority.py::test_pending_body_failure_survives_cleanup[receipt-PASS]
tests/test_cross_repo_cleanup_priority.py::test_pending_body_failure_survives_cleanup[replay-FAIL]
tests/test_cross_repo_cleanup_priority.py::test_pending_body_failure_survives_cleanup[replay-PASS]
tests/test_cross_repo_cleanup_priority.py::test_pending_body_failure_survives_cleanup[second-start-FAIL]
tests/test_cross_repo_cleanup_priority.py::test_pending_body_failure_survives_cleanup[second-start-PASS]
tests/test_cross_repo_persistent_diagnostic.py::test_output_process_control_preserves_committed_file_and_exception_identity[KeyboardInterrupt]
tests/test_cross_repo_persistent_diagnostic.py::test_output_process_control_preserves_committed_file_and_exception_identity[SystemExit]
tests/test_cross_repo_persistent_diagnostic.py::test_read_process_control_after_commit_preserves_identity[KeyboardInterrupt]
tests/test_cross_repo_persistent_diagnostic.py::test_read_process_control_after_commit_preserves_identity[SystemExit]
tests/test_diagnostic_completion.py::test_cli_reader_control_propagates_or_preserves_primary[None]
tests/test_diagnostic_completion.py::test_cli_reader_control_propagates_or_preserves_primary[primary1]
tests/test_diagnostic_completion.py::test_cli_reader_control_propagates_or_preserves_primary[primary2]
tests/test_diagnostic_completion.py::test_interrupted_publication_is_unknown_and_original_control_propagates[False-error0]
tests/test_diagnostic_completion.py::test_interrupted_publication_is_unknown_and_original_control_propagates[False-error1]
tests/test_diagnostic_completion.py::test_interrupted_publication_is_unknown_and_original_control_propagates[True-error0]
tests/test_diagnostic_completion.py::test_interrupted_publication_is_unknown_and_original_control_propagates[True-error1]
```

</details>

<a id="evidence-t18d"></a>

##### T18d

- Obligation: Reject durable root overlapping any temporary tree or alias before publication; no string-prefix-only claim.
- Actual source: StorageBinding root admission and excluded roots.
- Independent oracle / input: Actual temporary paths/symlink aliases, retained root identity, explicit refusal/no chmod and no artifact in temporary cwd.
- Remaining gap / supported disposition: Qualification and path/identity rules are bounded Linux; device/inode is not a generation ID or same-UID protection.
- R3 status: Current root-exclusion omission with exact surviving-artifact oracle NOT_RUN; no proof of untested filesystem/mount combinations.

<details>
<summary>T18d: exact static node identities (execution status: exact dated supplements; otherwise NOT_RUN)</summary>

```text
tests/test_advisory_diagnostics.py::test_durable_root_cannot_overlap_temporary_cwd[alias]
tests/test_advisory_diagnostics.py::test_durable_root_cannot_overlap_temporary_cwd[descendant]
tests/test_advisory_diagnostics.py::test_durable_root_cannot_overlap_temporary_cwd[source]
tests/test_advisory_diagnostics.py::test_no_record_under_temporary_worker_cwd
tests/test_advisory_diagnostics.py::test_replaced_source_root_cannot_be_rebound_by_existing_binding
tests/test_advisory_diagnostics.py::test_source_symlink_and_shared_permissions_are_rejected_without_chmod
```

</details>

<a id="evidence-t18e"></a>

##### T18e

- Obligation: Destination publication failure/only staging: never successful locator; owned partial cleanup, foreign/committed data retained.
- Actual source: publish_observation commit/reconciliation/cleanup.
- Independent oracle / input: Real file operation faults and explicit status/locator/remaining file checks; unknown is not confirmed absence.
- Remaining gap / supported disposition: Direct persistent-root design does not implement cross-filesystem staging transfer; that old alternative requires explicit not-applicable disposition, not a fake transfer test.
- R3 status: Current premature-locator/foreign-delete omission controls NOT_RUN; historical contention controls limited to named library boundaries.

<details>
<summary>T18e: exact static node identities (execution status: exact dated supplements; otherwise NOT_RUN)</summary>

```text
tests/test_advisory_diagnostics.py::test_cleanup_does_not_remove_foreign_temp_replacement
tests/test_advisory_diagnostics.py::test_commit_then_unreadable_remains_unknown_without_retry
tests/test_advisory_diagnostics.py::test_incomplete_write_never_publishes_and_removes_owned_temp[partial-error]
tests/test_advisory_diagnostics.py::test_incomplete_write_never_publishes_and_removes_owned_temp[zero]
tests/test_advisory_diagnostics.py::test_precommit_operation_failure_is_not_published[fstat]
tests/test_advisory_diagnostics.py::test_precommit_operation_failure_is_not_published[getxattr]
tests/test_advisory_diagnostics.py::test_precommit_operation_failure_is_not_published[mkdir]
tests/test_advisory_diagnostics.py::test_precommit_operation_failure_is_not_published[open]
tests/test_advisory_diagnostics.py::test_precommit_operation_failure_is_not_published[write]
tests/test_diagnostic_completion.py::test_normal_review_retains_publication_failure_status[bind]
tests/test_diagnostic_completion.py::test_normal_review_retains_publication_failure_status[publish]
```

</details>

<a id="evidence-t18f"></a>

##### T18f

- Obligation: Cleanup failure: keep incoming primary exception; no-primary failures retain existing priority; observe attempts and real residue, never infer unconditional deletion.
- Actual source: _PerRepoCwdCleanup.__exit__; publisher/capture cleanup.
- Independent oracle / input: Exact primary/no-primary object and LIFO priority; actual rmdir permission failure leaves observed residue; all registered callbacks attempted when faults occur inside callbacks.
- Remaining gap / supported disposition: Already repaired callback-masking defect (#452), not universal cleanup guarantee. Partial-setup control throws before the second constructor returns; it does not cover allocation inside that constructor before registration. The owner exception matrix is a private-owner unit control, not full host. Permission failure deliberately leaves a directory. Callback-external SIGINT/source-lock/unregistered allocation/forced death remain separately disposed.
- R3 status: Historical unchanged baseline masked-primary RED exists; current byte-restored replay NOT_RUN; cannot relabel old source/setup failures as current product RED.

<details>
<summary>T18f: exact static node identities (execution status: exact dated supplements; otherwise NOT_RUN)</summary>

```text
tests/test_cross_repo_cleanup_priority.py::test_cleanup_only_error_keeps_identity[KeyboardInterrupt-FAIL]
tests/test_cross_repo_cleanup_priority.py::test_cleanup_only_error_keeps_identity[KeyboardInterrupt-PASS]
tests/test_cross_repo_cleanup_priority.py::test_cleanup_only_error_keeps_identity[StopIteration-FAIL]
tests/test_cross_repo_cleanup_priority.py::test_cleanup_only_error_keeps_identity[StopIteration-PASS]
tests/test_cross_repo_cleanup_priority.py::test_cleanup_only_error_keeps_identity[SystemExit-FAIL]
tests/test_cross_repo_cleanup_priority.py::test_cleanup_only_error_keeps_identity[SystemExit-PASS]
tests/test_cross_repo_cleanup_priority.py::test_ignored_permission_error_keeps_residue_and_result[normal-return-FAIL]
tests/test_cross_repo_cleanup_priority.py::test_ignored_permission_error_keeps_residue_and_result[normal-return-PASS]
tests/test_cross_repo_cleanup_priority.py::test_ignored_permission_error_keeps_residue_and_result[pending-body-FAIL]
tests/test_cross_repo_cleanup_priority.py::test_ignored_permission_error_keeps_residue_and_result[pending-body-PASS]
tests/test_cross_repo_cleanup_priority.py::test_multiple_cleanup_failures_keep_priority[cleanup-only-FAIL]
tests/test_cross_repo_cleanup_priority.py::test_multiple_cleanup_failures_keep_priority[cleanup-only-PASS]
tests/test_cross_repo_cleanup_priority.py::test_multiple_cleanup_failures_keep_priority[pending-body-FAIL]
tests/test_cross_repo_cleanup_priority.py::test_multiple_cleanup_failures_keep_priority[pending-body-PASS]
tests/test_cross_repo_cleanup_priority.py::test_partial_setup_cleans_only_registered_cwd[KeyboardInterrupt]
tests/test_cross_repo_cleanup_priority.py::test_partial_setup_cleans_only_registered_cwd[RuntimeError]
tests/test_cross_repo_cleanup_priority.py::test_partial_setup_cleans_only_registered_cwd[SystemExit]
tests/test_cross_repo_cleanup_priority.py::test_pending_body_failure_survives_cleanup[primary-error-FAIL]
tests/test_cross_repo_cleanup_priority.py::test_pending_body_failure_survives_cleanup[primary-error-PASS]
tests/test_cross_repo_cleanup_priority.py::test_pending_body_failure_survives_cleanup[primary-exit-FAIL]
tests/test_cross_repo_cleanup_priority.py::test_pending_body_failure_survives_cleanup[primary-exit-PASS]
tests/test_cross_repo_cleanup_priority.py::test_pending_body_failure_survives_cleanup[primary-interrupt-FAIL]
tests/test_cross_repo_cleanup_priority.py::test_pending_body_failure_survives_cleanup[primary-interrupt-PASS]
tests/test_cross_repo_cleanup_priority.py::test_pending_body_failure_survives_cleanup[receipt-FAIL]
tests/test_cross_repo_cleanup_priority.py::test_pending_body_failure_survives_cleanup[receipt-PASS]
tests/test_cross_repo_cleanup_priority.py::test_pending_body_failure_survives_cleanup[replay-FAIL]
tests/test_cross_repo_cleanup_priority.py::test_pending_body_failure_survives_cleanup[replay-PASS]
tests/test_cross_repo_cleanup_priority.py::test_pending_body_failure_survives_cleanup[second-start-FAIL]
tests/test_cross_repo_cleanup_priority.py::test_pending_body_failure_survives_cleanup[second-start-PASS]
tests/test_cross_repo_cleanup_priority.py::test_sibling_warning_is_not_a_pending_error[cleanup-error-FAIL]
tests/test_cross_repo_cleanup_priority.py::test_sibling_warning_is_not_a_pending_error[cleanup-error-PASS]
tests/test_cross_repo_cleanup_priority.py::test_sibling_warning_is_not_a_pending_error[normal-cleanup-FAIL]
tests/test_cross_repo_cleanup_priority.py::test_sibling_warning_is_not_a_pending_error[normal-cleanup-PASS]
tests/test_diagnostic_completion.py::test_capture_close_failure_is_separate_from_prior_publication[error0]
tests/test_diagnostic_completion.py::test_capture_close_failure_is_separate_from_prior_publication[error1]
```

</details>

<a id="evidence-t19"></a>

##### T19

- Obligation: Partial or unverified publication never grants successful delivery/locator; verified complete records can survive interruption. Preserve original control/primary exception and bounded owned cleanup across write/link/cleanup/read.
- Actual source: publisher/reader descriptor lifecycle; capture/CLI exception propagation.
- Independent oracle / input: Evidence is split, not universal: `test_process_control_propagates_and_preserves_committed_record` checks type/message and final-file presence (not same exception object or strict reread). `test_cleanup_signal_does_not_replace_primary_signal` checks object identity for write/unlink double fault. Completion controls check original control/primary identity and unknown/no locator at their named caller boundary. T18a separately supplies normal post-cleanup strict reread.
- Assertion limitation: Both selected close-fault controls actually close the descriptor before raising; they do not prove behavior when close itself never succeeds or universal FD cleanup. Pending-file cleanup is not asserted in every process-control parameter.
- Remaining gap / supported disposition: Catchable exceptions only; real process death, SIGKILL/power loss, universal I/O timeout not claimed. read/capture/worker/CLI facts remain independent.
- R3 status: Current swallow-control/replace-primary omissions NOT_RUN; #491 terminal-update omission reports only its specific unknown-state oracle.


###### T19 finite assertion audit (static; runtime status follows exact dated supplements)

At product/test `7ca557a2d6d56d9c84a810b6c6f1da08861aa7db`, the
following matrix resolves the requested assertion inventory without a new test or
experiment. Full parameter node IDs remain in the adjacent expansion; family
rows below apply only to those listed parameter instances, not other tests.

| Exact test family (file/function; parameters below) | Assertion actually present | Not established by that assertion |
| --- | --- | --- |
| `tests/test_advisory_diagnostics.py::test_process_control_propagates_and_preserves_committed_record` | `pytest.raises(signal, match="cancel")`; JSON presence iff boundary is not write; absence of pending files only when boundary is not cleanup. | No exception-object identity, decoded payload, strict reread or FD-count assertion. Cleanup parameters deliberately do not assert pending-file absence. |
| `tests/test_advisory_diagnostics.py::test_cleanup_signal_does_not_replace_primary_signal` | `caught.value is primary` for write KeyboardInterrupt plus unlink SystemExit. | No artifact-read, pending-file or FD-count assertion; this narrow pair is not every cleanup boundary. |
| `tests/test_advisory_diagnostics.py::test_close_process_control_does_not_replace_contained_primary_error` | Publication status `not_published`, cleanup `complete`, with write ENOSPC and close wrapper raising SystemExit **after real close**. | No same-exception-object oracle, strict reread or descriptor count. It does not simulate a descriptor that failed to close. |
| `tests/test_advisory_diagnostics.py::test_close_process_control_is_not_hidden_by_an_earlier_ordinary_close_failure` | KeyboardInterrupt type/message, at least two close calls, one JSON file; each wrapper closes its FD before raising. | Close-call count is not live-FD count; file presence is not valid payload or strict-read proof. |
| `tests/test_diagnostic_completion.py::test_interrupted_publication_is_unknown_and_original_control_propagates` | Original exception identity; capture available but worker incomplete; one UNKNOWN attempt with no locator; no tickets and unavailable notice. **Committed=True only:** formal reader on the retained successful publication and original expected returns `no_named_targets`. | Strict reread applies only to the already committed record, not to the UNKNOWN result. No FD-count, complete-worker or whole-CLI-success guarantee. |
| `tests/test_diagnostic_completion.py::test_cli_reader_control_propagates_or_preserves_primary` | If caller read is entered, its wrapper checks the completion event; raised object is pre-existing primary or reader control; retained publication remains `published`. | A confirmed publication is not successful caller delivery. No successful reader payload, ticket integrity or FD-count assertion in this control. |

This finishes the **static T19 assertion-map deliverable**, not T19 execution or
contract acceptance. Descriptor-count evidence in these selected nodes is
**UNKNOWN / absent**; whether a further FD oracle is required belongs to the
bounded support-domain decision. No universal FD cleanup or interrupted-write
readability conclusion is proposed. The exact identity/read controls already
present should be reused before proposing any additional test.

<details>
<summary>T19: exact static node identities (execution status: exact dated supplements; otherwise NOT_RUN)</summary>

```text
tests/test_advisory_diagnostics.py::test_cleanup_signal_does_not_replace_primary_signal
tests/test_advisory_diagnostics.py::test_close_process_control_does_not_replace_contained_primary_error
tests/test_advisory_diagnostics.py::test_close_process_control_is_not_hidden_by_an_earlier_ordinary_close_failure
tests/test_advisory_diagnostics.py::test_process_control_propagates_and_preserves_committed_record[cleanup-KeyboardInterrupt]
tests/test_advisory_diagnostics.py::test_process_control_propagates_and_preserves_committed_record[cleanup-SystemExit]
tests/test_advisory_diagnostics.py::test_process_control_propagates_and_preserves_committed_record[link-KeyboardInterrupt]
tests/test_advisory_diagnostics.py::test_process_control_propagates_and_preserves_committed_record[link-SystemExit]
tests/test_advisory_diagnostics.py::test_process_control_propagates_and_preserves_committed_record[write-KeyboardInterrupt]
tests/test_advisory_diagnostics.py::test_process_control_propagates_and_preserves_committed_record[write-SystemExit]
tests/test_diagnostic_completion.py::test_cli_reader_control_propagates_or_preserves_primary[None]
tests/test_diagnostic_completion.py::test_cli_reader_control_propagates_or_preserves_primary[primary1]
tests/test_diagnostic_completion.py::test_cli_reader_control_propagates_or_preserves_primary[primary2]
tests/test_diagnostic_completion.py::test_interrupted_publication_is_unknown_and_original_control_propagates[False-error0]
tests/test_diagnostic_completion.py::test_interrupted_publication_is_unknown_and_original_control_propagates[False-error1]
tests/test_diagnostic_completion.py::test_interrupted_publication_is_unknown_and_original_control_propagates[True-error0]
tests/test_diagnostic_completion.py::test_interrupted_publication_is_unknown_and_original_control_propagates[True-error1]
```

</details>

<a id="evidence-t20"></a>

##### T20

- Obligation: Compatible normal CLI/MCP output; empty not_run versus no publication distinguished; missing file is unavailable, nonempty sink failure unconfirmed, empty read need not call sink.
- Actual source: runner output fallback; DiagnosticCapture.present; CLI/MCP output and private completion.
- Independent oracle / input: Controlled omitted dispatch yields real empty record; separate native nonempty PASS/FAIL with sink fault; exact messages/structured output, delivery status and untouched gate/receipts.
- Remaining gap / supported disposition: Natural not_run reachability remains unresolved. Empty delivered proves consumption not sink health. Historical SDK tests cannot stand for full live review SDK qualification.
- Static follow-up: `cross_repo.py` calls `complete_not_run()` after `sm.run()` returns; `_cross_repo_diagnostics.py::complete_not_run` emits only when `_called` is false. `test_normal_machine_return_without_axis_call_records_not_run` explicitly replaces `_run_advisory_axes` with a no-op; the selected `test_host_not_run_empty_success_and_unavailable_statuses` fixture likewise supplies dispatch behavior. Thus these nodes test the consumer contract for an injected state, not a supported natural path. Source-condition identification is complete; natural path/test identity remains UNKNOWN, not an assertion that no such path exists.
- R3 status: #454 delivery-guard/disposition omissions and native output omissions reported; current revision reproduction NOT_RUN; default vs opted-in output should be judged by their separate contracts.

<details>
<summary>T20: exact static node identities (execution status: exact dated supplements; otherwise NOT_RUN)</summary>

```text
tests/test_cross_repo_diagnostic_gate.py::test_docs_only_native_ci_tracks_nonempty_delivery[all-fail-FAIL]
tests/test_cross_repo_diagnostic_gate.py::test_docs_only_native_ci_tracks_nonempty_delivery[all-fail-PASS]
tests/test_cross_repo_diagnostic_gate.py::test_docs_only_native_ci_tracks_nonempty_delivery[diagnostic-fails-FAIL]
tests/test_cross_repo_diagnostic_gate.py::test_docs_only_native_ci_tracks_nonempty_delivery[diagnostic-fails-PASS]
tests/test_cross_repo_diagnostic_gate.py::test_docs_only_native_ci_tracks_nonempty_delivery[ok-FAIL]
tests/test_cross_repo_diagnostic_gate.py::test_docs_only_native_ci_tracks_nonempty_delivery[ok-PASS]
tests/test_cross_repo_native_consumer_chain.py::test_native_mcp_cli_diagnostic[FAIL-background]
tests/test_cross_repo_native_consumer_chain.py::test_native_mcp_cli_diagnostic[FAIL-immediate]
tests/test_cross_repo_native_consumer_chain.py::test_native_mcp_cli_diagnostic[PASS-background]
tests/test_cross_repo_native_consumer_chain.py::test_native_mcp_cli_diagnostic[PASS-immediate]
tests/test_cross_repo_native_consumer_chain.py::test_native_mcp_cli_read_loss_is_non_gating[FAIL]
tests/test_cross_repo_native_consumer_chain.py::test_native_mcp_cli_read_loss_is_non_gating[PASS]
tests/test_cross_repo_persistent_diagnostic.py::test_actual_strict_reader_return_controls_cli_text
tests/test_cross_repo_persistent_diagnostic.py::test_host_not_run_empty_success_and_unavailable_statuses[bind-sink-fails]
tests/test_cross_repo_persistent_diagnostic.py::test_host_not_run_empty_success_and_unavailable_statuses[bind-sink-ok]
tests/test_cross_repo_persistent_diagnostic.py::test_host_not_run_empty_success_and_unavailable_statuses[missing-sink-fails]
tests/test_cross_repo_persistent_diagnostic.py::test_host_not_run_empty_success_and_unavailable_statuses[missing-sink-ok]
tests/test_cross_repo_persistent_diagnostic.py::test_host_not_run_empty_success_and_unavailable_statuses[none-sink-fails]
tests/test_cross_repo_persistent_diagnostic.py::test_host_not_run_empty_success_and_unavailable_statuses[none-sink-ok]
tests/test_cross_repo_persistent_diagnostic.py::test_host_not_run_empty_success_and_unavailable_statuses[publish-sink-fails]
tests/test_cross_repo_persistent_diagnostic.py::test_host_not_run_empty_success_and_unavailable_statuses[publish-sink-ok]
tests/test_cross_repo_persistent_diagnostic.py::test_host_not_run_empty_success_and_unavailable_statuses[read-sink-fails]
tests/test_cross_repo_persistent_diagnostic.py::test_host_not_run_empty_success_and_unavailable_statuses[read-sink-ok]
tests/test_cross_repo_persistent_diagnostic.py::test_normal_machine_return_without_axis_call_records_not_run
tests/test_cross_repo_persistent_diagnostic.py::test_persistence_failure_never_fabricates_no_target_state[blank]
tests/test_cross_repo_persistent_diagnostic.py::test_persistence_failure_never_fabricates_no_target_state[not-run]
tests/test_cross_repo_persistent_diagnostic.py::test_persistence_failure_never_fabricates_no_target_state[targets]
tests/test_diagnostic_completion.py::test_removed_artifact_before_cli_consumption_is_unavailable
tests/test_diagnostic_completion.py::test_setup_failure_is_not_successful_empty_history
```

</details>

##### G02/G07/G08 support-budget and history-isolation subclauses

Oracle: independent literal budget/permission/path expectations, real file identity and retained historical error views; fake ACL/mount and inode reuse are injected boundary controls, not proof of all real filesystems or generation continuity. Excess/invalid history disables only persistence, not native warnings. Limits: full supported-type/aggregate-budget and filesystem qualification need row-specific logs; ext4 eligibility is not an ext4 test on this cloud host. Runtime status follows exact dated supplements; uncited runtime and individual guard-omission evidence remain NOT_RUN.

<details>
<summary>Exact static parameter identities (execution status: exact dated supplements; otherwise NOT_RUN)</summary>

```text
tests/test_advisory_diagnostics.py::test_absent_or_unsupported_acl_namespace_on_qualified_mount[61]
tests/test_advisory_diagnostics.py::test_absent_or_unsupported_acl_namespace_on_qualified_mount[95]
tests/test_advisory_diagnostics.py::test_detected_or_uninspectable_acl_fails_closed[1]
tests/test_advisory_diagnostics.py::test_detected_or_uninspectable_acl_fails_closed[5]
tests/test_advisory_diagnostics.py::test_detected_or_uninspectable_acl_fails_closed[extended-acl]
tests/test_advisory_diagnostics.py::test_every_directory_and_leaf_mount_is_requalified
tests/test_advisory_diagnostics.py::test_every_namespace_directory_rejects_symlink[0]
tests/test_advisory_diagnostics.py::test_every_namespace_directory_rejects_symlink[1]
tests/test_advisory_diagnostics.py::test_every_namespace_directory_rejects_symlink[2]
tests/test_advisory_diagnostics.py::test_every_namespace_directory_rejects_symlink[3]
tests/test_advisory_diagnostics.py::test_every_namespace_directory_rejects_symlink[4]
tests/test_advisory_diagnostics.py::test_exact_full_path_byte_limit_is_supported_without_io
tests/test_advisory_diagnostics.py::test_filesystem_qualification_fails_closed[device]
tests/test_advisory_diagnostics.py::test_filesystem_qualification_fails_closed[duplicate]
tests/test_advisory_diagnostics.py::test_filesystem_qualification_fails_closed[fdinfo]
tests/test_advisory_diagnostics.py::test_filesystem_qualification_fails_closed[missing]
tests/test_advisory_diagnostics.py::test_filesystem_qualification_fails_closed[unknown]
tests/test_advisory_diagnostics.py::test_filesystem_qualification_fails_closed[unreadable]
tests/test_advisory_diagnostics.py::test_maximum_utf8_string_and_sequence_remain_within_payload_budget[1]
tests/test_advisory_diagnostics.py::test_maximum_utf8_string_and_sequence_remain_within_payload_budget[64]
tests/test_advisory_diagnostics.py::test_preexisting_shared_directory_is_never_chmodded
tests/test_advisory_diagnostics.py::test_reader_rejects_replaced_directory_even_with_same_leaf_bytes[0]
tests/test_advisory_diagnostics.py::test_reader_rejects_replaced_directory_even_with_same_leaf_bytes[1]
tests/test_advisory_diagnostics.py::test_reader_rejects_replaced_directory_even_with_same_leaf_bytes[2]
tests/test_advisory_diagnostics.py::test_reader_rejects_replaced_directory_even_with_same_leaf_bytes[3]
tests/test_advisory_diagnostics.py::test_reader_rejects_replaced_directory_even_with_same_leaf_bytes[4]
tests/test_advisory_diagnostics.py::test_root_component_budget_counts_anchor_and_rejects_one_more
tests/test_advisory_diagnostics.py::test_simulated_reused_leaf_identity_preserves_content_and_execution_checks[changed-bytes]
tests/test_advisory_diagnostics.py::test_simulated_reused_leaf_identity_preserves_content_and_execution_checks[identical-bytes]
tests/test_advisory_diagnostics.py::test_simulated_reused_leaf_identity_preserves_content_and_execution_checks[stale-execution]
tests/test_advisory_diagnostics.py::test_source_symlink_and_shared_permissions_are_rejected_without_chmod
tests/test_cross_repo_capture.py::test_large_final_cache_errors_do_not_restore_previous_uncached_error
tests/test_cross_repo_capture.py::test_non_utf8_native_error_disables_only_persistence
tests/test_cross_repo_capture.py::test_registry_stage_returns_defensive_copies
tests/test_cross_repo_persistent_diagnostic.py::test_observation_limit_bounds_files_and_attempts_but_keeps_native_warnings
tests/test_cross_repo_persistent_diagnostic.py::test_relative_sibling_binding_limits_only_consumed_persistence[consumed]
tests/test_cross_repo_persistent_diagnostic.py::test_relative_sibling_binding_limits_only_consumed_persistence[unconsumed]
tests/test_cross_repo_persistent_diagnostic.py::test_surrogate_registry_alias_preserves_native_error_without_snapshot_failure
```

</details>

##### G04/G09 separate historical entries

Oracle: explicitly selected caller-trusted saved context, fixed historical projection/current_run_credit=false, actual SDK/stdio child restarts, independently configured roots and exact cancellation/drain ownership. Limits: these additional history entries are not original live T20 or current-review credentials; a ticket basename is not independent origin authentication. The original mapping recorded Current runtime NOT_RUN. Execution evidence and limited owner acceptance for the selected history-reader slice are now recorded in the 2026-11-17 #527 supplement above; unlisted controls and broader guarantees retain their separate status. Scope-live count semantics received separate limited owner approval on 2026-11-14 above, without expanding v1 or granting execution credit.

<details>
<summary>Exact static parameter identities (execution status: exact dated supplements; otherwise NOT_RUN)</summary>

```text
tests/test_diagnostic_history.py::test_all_v1_states_remain_distinct_in_fresh_cli[no_named_targets]
tests/test_diagnostic_history.py::test_all_v1_states_remain_distinct_in_fresh_cli[not_requested]
tests/test_diagnostic_history.py::test_all_v1_states_remain_distinct_in_fresh_cli[not_run]
tests/test_diagnostic_history.py::test_all_v1_states_remain_distinct_in_fresh_cli[targets_resolved]
tests/test_diagnostic_history.py::test_all_v1_states_remain_distinct_in_fresh_cli[unavailable]
tests/test_diagnostic_history.py::test_unsigned_ticket_requires_caller_trust_not_origin_authentication
tests/test_mcp_diagnostic_history.py::test_anyio_cancel_scope_cannot_deliver_success[False]
tests/test_mcp_diagnostic_history.py::test_anyio_cancel_scope_cannot_deliver_success[True]
tests/test_mcp_diagnostic_history.py::test_cancel_drains_owned_worker_before_propagating
tests/test_mcp_diagnostic_history.py::test_real_stdio_default_off
tests/test_mcp_diagnostic_history.py::test_real_stdio_restart_and_job_independence
tests/test_mcp_diagnostic_history.py::test_shutdown_drains_read_before_job_cleanup
tests/test_mcp_scope_history.py::test_anyio_cancel_scope_cannot_deliver_success[False]
tests/test_mcp_scope_history.py::test_anyio_cancel_scope_cannot_deliver_success[True]
tests/test_mcp_scope_history.py::test_cancel_drains_owned_worker_before_propagating
tests/test_mcp_scope_history.py::test_configuration_does_not_enable_other_history
tests/test_mcp_scope_history.py::test_diagnostic_ticket_cannot_masquerade_as_scope
tests/test_mcp_scope_history.py::test_real_stdio_restart_and_job_independence
tests/test_mcp_scope_history.py::test_scope_ticket_cannot_masquerade_as_diagnostic
tests/test_mcp_scope_history.py::test_shutdown_drains_read_before_job_cleanup
tests/test_scope_history_host.py::test_real_host_exports_after_independent_read_and_cli_reads_after_close
```

</details>

#### A01–A08 and R0–R4 exit-state reconciliation

| Existing work | Current document delivery / uncompleted exit condition |
| --- | --- |
| A01 / R0–R2 | All G/T labels and known subclauses now have a static evidence pointer or explicit gap. This is not full runtime evidence: final document commit, environment admission, fresh collection, raw-log recovery and versioned original-port disposition remain open. |
| A02 | Empty strict consumption, persistence/read failure and nonempty sink unconfirmed are mapped under T20. Natural not_run reachability remains a decision/gap; no new false-delivered defect asserted. |
| A03 | Entry table proposes default text-only acceptance and records actual #491 opt-in caller handoff. Each entry requires maintainer disposition; no further interface is invented. |
| A04 | Stage-effective lazy first-use/duplicate/native-error/budget controls mapped in T07/T11/T12. Existing limited G06/G07 acceptance does not replace current runtime evidence. |
| A05 / R3 | Native docs-only PASS/FAIL with State/credit/receipt oracles mapped in T13. Fresh selected semantic omission/restoration not performed; non-target/harness failures stop a row. |
| A06 / R3 | Storage T15–T17 and explicit T18a–f/T19 fault/lifecycle rows expose gaps. No library-to-host concurrency or cleanup-status inference. |
| A07 | Entry/support proposals preserve no HOLD, bounded Linux, actual historical SDK evidence and remaining live-wire qualification limits. Limited G01/G02 semantics and listed budgets, and bounded G08 support, have the later owner decisions above; remaining unlisted details and runtime qualification remain pending. |
| A08 / R4 | One final revision regression, public identity, pinned lint/format/whitespace, reviewed evidence archive, independent G0 and maintainer acceptance remain pending. #443/#444 unique difference/disposition not freshly reviewed in this pass. No issue closed. |

### Finite follow-up dispositions requested by the COMMENT review

The next round is documentation adjudication, not another experiment or a
pre-authorized production repair. These use the existing T records, not new gates.
Each task ends at the stated deliverable; missing exact identities are UNKNOWN,
not a reason to invent a test or keep an unlimited investigation open.

| Existing record | First reuse/map step | Finite deliverable and exit; current status |
| --- | --- | --- |
| [T03](#evidence-t03): primary connection count | Inspect the listed immediate zero-target controls and SQL trace fixtures. Their sibling `forbid` sentinel does not prove no primary connection, and a zero query trace is not a zero connection count. | The original fixed-baseline `no-path`/`deleted` nodes had only sibling registry/helper forbid sentinels, with no primary connection count. The existing nodes now carry the implemented post-setup primary-connection observer, plus unnamed positive control, documented in the 2026-11-14 T03 supplement; original diagnostic and empty-result assertions are preserved. The bounded three-node observation/ordering-control sequence is recorded above; other runtime/support dispositions remain PENDING. |
| [T06](#evidence-t06): equal-rank order/single-hop | Reuse listed `TestTopNCap::test_cap_at_top_n` and caller-query controls for retained top-N and actual SQL edge traversal obligations. A top-N count is not no-second-hop proof; stable tie-order qualification is not required by the recorded owner decision. | Selected-node/source static audit completed in T06: top-N test asserts length only; query is direct-edge and has no ORDER BY, ranking has no explicit tie key. No stable tie-order qualification is required; the later T06 supplement identifies the chain-exclusion node. Owner decision on 2026-11-14: stable tie order NOT PROMISED. The later T06 supplement adds the bounded real-SQLite chain and distance-selection oracles; no product defect or mutation RED is inferred. |
| [T12](#evidence-t12): process/fork/shared-use | Keep executed three warm-input cases separate. Reuse installed-capture closed/foreign-thread cases already indexed; serial foreign-thread refusal/reuse does not qualify fork or simultaneous shared use. | The earlier task requested an explicit unsupported process/fork/shared-use scope or one supported scenario and node; the subsequent bounded disposition above now resolves that choice. Fork/shared-use runtime evidence UNKNOWN; the subsequent owner disposition above excludes inherited/migrated captures and simultaneous sharing, without claiming fork safety. No new concurrency experiment is required by that exclusion. |
| [T14](#evidence-t14): recovery | Reuse controlled PENDING compatibility and new-host identity controls; neither is HOLD resume. Preserve existing no-cross-repository-HOLD limited contract. | Owner accepted the original recovery obligation's UNSUPPORTED/NOT APPLICABLE disposition on 2026-11-14; see the fixed-version chat-source record above. This disposition task is complete, not a recovery PASS. Do not add fake recovery tests or delete the original obligation. |
| [T19](#evidence-t19): identity/read/FD assertions | Reuse listed process-control, close, identity and post-cleanup-reader controls; annotate each actual assertion (type/message, object identity, payload/file presence, formal reread or descriptor count). | Static assertion matrix completed in T19 at the fixed source; runtime status follows exact dated supplements; remaining unapproved support details retain their prior status. Descriptor-count assertions are absent in the selected nodes. Close-after-success faults cannot prove failed-close cleanup. Reuse the mapped identity/read controls; the subsequent owner disposition above keeps real-close-failure no-leak behavior UNVERIFIED, without discarding normal-close or exception-priority obligations. |
| [T20](#evidence-t20): natural not_run | Reuse explicitly controlled omitted-dispatch nodes and current source reachability. Separate empty formal consumption from absence of publication and sink health. | Selected fixture/source audit completed in T20: after normal machine return with no axis call, the completion hook can emit not_run; listed controls deliberately suppress dispatch. Natural entry/path/test remains UNKNOWN. The subsequent owner disposition above retains this as a defensive legal state with controlled consumer evidence; no product path is created and no natural-path PASS or formal unreachability proof is claimed. |

For T14 and the T06 tie-order promise, the owner decision/source is recorded
above; no public decision permalink exists. The later T12/T19/T20 dispositions above are also accepted; other unapproved decisions remain pending.
The [COMMENT review](https://github.com/HouMinXi/forge/pull/493#pullrequestreview-5892176064)
and [follow-up direction](https://github.com/HouMinXi/forge/issues/441#issuecomment-6854967873)
are request sources, not affirmative contract decisions. The documentation PR can
finish once inventory, decision locations and finite follow-ups are reviewable;
it does not require every runtime row to become PASS. Any later execution returns
to fixed-revision R0–R4 and the existing stop conditions.

### Reported results are not this pass's results

Direct GitHub reads verified these PRs merged and their final heads; the following
local results and linked CI statements are **PR-author reports**, not raw-log
reanalysis or fresh CI polling by this documentation pass. Each report remains
bound to its own revision, never promoted to a unified run on `7ca557a2`.

| Source | Final PR head | Reported evidence and retained limits |
| --- | --- | --- |
| [#476](https://github.com/HouMinXi/forge/pull/476) | `1aed2abe0c458054bc4bbbc0460e64b63ff897d3` | 837 related tests, Python 3.14.10; source-specific codec omissions have a separately named older baseline, not a fresh composed-head experiment. |
| [#482](https://github.com/HouMinXi/forge/pull/482) | `3c9acab825eed39b0528cfab260f9848b3b73535` | 587 related tests and three cold/warm admission omissions with byte restoration; reports [Linux CI](https://github.com/HouMinXi/forge/actions/runs/43260507079). Not complete G06/G12 qualification. |
| [#485](https://github.com/HouMinXi/forge/pull/485) | `4da41698d443e46cfe56e39ac50d85604c7867d1` | 1377 passed, 2 skipped, 4 deselected; four storage-profile failures reproduced on its exact main remain local exclusions. Reports [Linux CI](https://github.com/HouMinXi/forge/actions/runs/43273404749); does not repair those cloud qualification limits. |
| [#487](https://github.com/HouMinXi/forge/pull/487) | `f0b030bba06692de25379765fc8fb36d52a7838c` | Final composition 1800 passed, two startup RuntimeWarnings retained; earlier 1734 results belong to the prior head. Reports [merge Linux CI](https://github.com/HouMinXi/forge/actions/runs/43376428131). |
| [#489](https://github.com/HouMinXi/forge/pull/489) | `2f598b261d92430a4522042663fd805af21e4e0f` | Frozen-source 2038 passed before final test-oracle strengthening; final focused 326 passed, 50 new cases. Source-changed-during-run 83 failures are invalid evidence, not silently deleted. Reports [merge Linux CI](https://github.com/HouMinXi/forge/actions/runs/43402076121). |
| [#491](https://github.com/HouMinXi/forge/pull/491) | `b08db66d6e9a4c3ed6672a03c8ce8c2d19de724e` | 2600 passed, 2 skipped; 21 new tests and two targeted omission controls. Reports [merge Linux CI](https://github.com/HouMinXi/forge/actions/runs/43408567850) and separate wheel/CodeQL links. Specified opt-in only, not complete G04/G09. |

Earlier #451/#452/#454/#456 results and limitations remain in the historical
sections and [R0–R4 discussion](https://github.com/HouMinXi/forge/issues/441#issuecomment-6786884934).
Raw local logs for these author reports were not recovered or rehashed here.
The linked CI pages are evidence entry points, not a claim that their archives
were downloaded. Missing raw evidence remains a gap; do not invent an artifact
URL or combine local source-tree and installed-wheel claims.

### A05/A06 then A08: bounded next work and stop rules

1. Consume the existing reviewable evidence and completed internal R0–R2
   inventory; record external review separately. Resolve the concrete entry/support
   proposals and clause-based original-port replacement limits above, adding only
   specifically missing mappings. Do not implement another result/history interface to avoid a
   decision. The table is the first document delivery, not final acceptance.
2. Choose one risk-driven missing oracle at a time: wrong identity/caller reread,
   unknown without locator, no overwrite or historical fallback, admission before
   SQL/error clearing/publication,
   non-gating State/findings/credit/receipt separation, then exception/lifecycle
   stage identity. Reuse adequate existing controls before adding tests.
3. For that row record permitted files/functions, preserved behavior, input,
   independent expected value, command/node/environment and stopping conditions.
   Establish unchanged GREEN, alter only its boundary in an isolated owned copy,
   require the intended semantic RED, restore exact source digest and repeat GREEN.
   Stop on baseline failure, setup/import/timeout/non-target rejection, escaped
   mutant or restoration mismatch; retain and classify the log. A deliberate
   mutant is not evidence of an unchanged-product defect.
4. Production changes require an actual counterexample inside the accepted
   domain. Public API, State/Verdict/receipt, retries, SQL/provider semantics and shared
   #68/#400 lifecycle changes stop for separate design and #43 ownership coordination.
5. R4 uses one final product/test/document revision, applicable regressions,
   identity audit, pinned Ruff check/format and whitespace validation, safe
   evidence artifacts, independent review and explicit maintainer acceptance.
   Resolve the remaining G01/G02/G04/G09/G08/G10/G11 decisions and evidence before G12/G13 completion; the limited G01/G02 semantics/listed budgets and G10 three-state semantics are already accepted, not complete qualification of those gates. Compare the
   unique necessary differences of #443/#444 before retain/extract/supersede;
   neither empty merging nor automatic closing is a disposition.

Both issues remain open. Limited #441 closure does not require every future
capability, and does not check any of #33's five parent capabilities. No G0/G1,
G12/G13 or new runtime credit is granted by this document.

## Historical fixed source and discussion

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
| P1-1: identity issuance and delivery | `cross_repo.run_cross_repo` creates the execution UUID; its primary worker creates `DiagnosticCapture` with a repository UUID, exact diff and source hash. `finish` owns the expected context and publication result; `present` strictly rereads and supplies the existing attributed output callback. CLI uses this host; immediate/background MCP use its CLI child. | New-host/same-hash and foreign-context tests exist. The public return remains `Verdict`, not a new result API. The default internal host consumer obtains the context/status/locator. The current entry table above also records the implemented #491 CLI opt-in handoff; acceptance remains entry-specific. No single-repository diagnostic recovery or cross-repository HOLD was added. |
| P1-2: immutable observations and cache admission | `DiagnosticCapture.admit` runs before cache return/error clearing. Stage results are retained at first actual consumption. The runner's final cache refers to the original record; early returns create new observations. `present(..., origin="cache")` updates delivery metadata only. | Real SQLite tests count queries, inspect original record identity, preserve error history, reject changed contexts and isolate returned lists. Configuration is stage-effective, not simultaneous. Graph changes are not automatically detected. |
| P1-3: publication before exceptional cleanup | Primary source `.code-forge/advisory-diagnostics` is checked against every per_cwd, then written directly; normal receipt collection is not the publication point. Publisher cleanup and capture close preserve primary exceptions and committed records. `_PerRepoCwdCleanup` now preserves an exception already leaving the owned cwd scope while delegating removal callbacks to ExitStack. | Native callback-fault controls cover normal deletion, exact exception priority, all cleanup attempts and actual residue. A single asynchronous interruption outside a callback can still stop remaining attempts. This repairs the demonstrated cwd-boundary masking defect, not every outer lifecycle or directory-deletion failure. Full G11/T18 acceptance remains separate. |
| P1-4: uncertain commit | `_advisory_diagnostics.publish_observation` writes a complete exclusive temporary file and hard-links a final name exclusively. Reconciliation verifies the owned inode and bytes after an ambiguous commit. `PublicationResult` keeps commit and cleanup separate; `read_observation` requires independent context. | Commit-then-raise, unknown without locator, foreign-file refusal, same-key conflict, owned-temp cleanup and post-close strict reading are tested. Unknown never grants a successful locator, retries publication or adopts a historical file. This is not a crash journal or exactly-once guarantee. |

## Contract dispositions and remaining decisions

The earlier issue registration accepts limited G03, G05, G06 and G07 dispositions. The dated owner decisions above additionally record limited G01/G02 semantics and listed budgets, entry semantics, storage support and other explicitly bounded decisions. These are not a completion percentage, full runtime qualification or formal G0/G1 admission; unlisted details and G12/G13 remain open. The accepted clauses retain their T11–T12/G12 evidence obligations and supported-type/resource limits.

| Earlier candidate or ambiguous claim | Implemented choice | Disposition and remaining work |
| --- | --- | --- |
| Capture all discovery inputs as one snapshot | Record only real first-use stage results, preserving lazy errors, lexical equality and duplicate occurrences. | Limited G06 contract accepted. No atomic snapshot, repeated ambient reread or general freshness promise. Complete real-SQL, invalidation and omission evidence still belongs to T11–T12/G12. |
| Put `origin` in the serialized observation | Keep the nine-field observation immutable and record actual/cache only in host delivery metadata. | Limited G03 contract accepted. A cache delivery is not a new graph query or publication; complete runtime acceptance remains separate. |
| Unbounded or arbitrary nested snapshot isolation | Retain supported historical findings/errors and defensive containers within the stated budgets; unavailable history is not an empty successful snapshot. | Limited G07 contract accepted. Frozen/tuple types alone do not prove arbitrary deep immutability; partial-error, nested-isolation and resource controls still require their exact evidence. |
| General recovery/HOLD identity requirement | New supported host calls get new identities. Cross-repository PENDING remains FAIL. Single-repository HOLD does not construct this diagnostic axis. | Limited G05 boundary accepted. G04's unapproved entry details remain open; T14's limited UNSUPPORTED/NOT APPLICABLE disposition was accepted on 2026-11-14. An assigned verdict is not a recovery run. |
| Durable root or return interface unspecified | Use the primary canonical source root and a private host consumer; public runner/CLI/MCP/Verdict APIs remain unchanged. | Proposed/PENDING: accept this concrete consumer and root rather than require an unused public session interface. |
| General filesystem identity or freshness | Independently bound observation identity and content digest; device/inode is not a generation identifier. Local Linux overlay/tmpfs exercised; ext4 eligible but not locally qualified. | Bounded storage domain accepted by the 2026-11-14 owner decision above, not whole-domain runtime qualification. Same-UID hostile replacement, inode continuity, network filesystems, power loss and reboot persistence are not proved. |
| Main exception always wins through all cleanup | Diagnostic cleanup and the owned per_cwd scope preserve their incoming exception. Later source-lock cleanup and failures already replaced inside another scope are outside this repair. | The demonstrated cwd-boundary defect has a bounded fix and public controls below. Complete G11/T18 disposition still requires the remaining boundaries; no global guarantee is granted. |
| Original 78-test port or all T controls already available | The public production tests are real and separately identified. The complete original port has not been recovered and cannot transfer its count or credit. | Versioned G/T organization accepted on 2026-11-14; original recovery/equivalence stays UNKNOWN. Complete the retained T/node/oracle obligations without importing the original count or historical credit. |

## G02 schema and resource evidence

The [wire contract](cross-repo-diagnostics-v1.md#closed-v1-wire-contract) is closed at all object levels: exactly nine root fields, canonical UUID4 contexts, sequence 1–64, five analysis states with fixed diagnostic combinations, 4096 raw bytes, container depth 3 and 256 decoded UTF-8 bytes per schema string. `_payload`, `decode_observation`, `_pairs`, `_integer`, `_scan_depth` and `_validate_context` implement this contract. `read_observation` additionally checks held host context, namespace, descriptor identities, exact byte count and the original digest.

The first post-merge review found a test-oracle defect, not a decoder defect: deleting only the `_pairs` duplicate-key guard still passed all 311 original storage test cases. The old duplicate documents were invalid for other reasons. The added `test_duplicate_keys_in_otherwise_valid_document_rejected[root]`, `[repository]` and `[diagnostic]` start from a valid v1 payload; ordinary dict decoding produces exactly the original legal object, while the actual strict decoder must reject. Removing only duplicate detection produces three semantic assertion failures, and restoration passes. This prevents an unrelated schema rejection from masquerading as duplicate-key coverage.

The contract's retained configuration/history/observation budgets are separate from the 4096-byte decoder budget. Sixty-four payloads imply at most 256 KiB of logical final payload, not total filesystem allocation, temporary-name retention, a global cross-run retention bound or a time limit. No new numerical ceiling or platform expansion is introduced here.

### W1-11: independent decoder byte-boundary oracle

W1-11 is a proposed replacement clause identifier for
[“批准字节上限与加一”](https://github.com/HouMinXi/forge/issues/441#issuecomment-6745701302)
(approved byte limit and one beyond), not a recovered original test node identity.

`tests/test_advisory_diagnostics.py::test_wire_byte_budget_on_otherwise_valid_document[4096]`
and `[4097]` pad a legal v1 payload with JSON whitespace. The unmodified ordinary
JSON parser produces the same document at both lengths; the strict decoder must
return the independently held observation at 4096 bytes and reject 4097 with
`invalid_schema`. These literal limits describe the current implementation, not
formal G02 approval. The existing maximum-string test already accepted 4096
bytes, and the existing pre-parser sentinel already detected omission of the byte
guard. The new cases add a semantic rejection oracle with the real parser; they
do not repair a demonstrated product defect.

Local qualification against production revision
`ceeffa291e85499e81e48886b764d455084f3924` ran the new pair unchanged, then in an
isolated source copy removed only the decoder's length predicate while retaining
its bytes-type check. Only `[4097]` failed, with `DID NOT RAISE`. Separately replacing
that predicate's `>` with `>=` failed only `[4096]` with `invalid_schema` at the
acceptance assertion. Byte-exact restoration passed the same pair after each
control. These are deliberately induced failures, not baseline product failures.

This qualification is codec-only. It does not exercise publisher output,
`read_observation`, its locator limit, `_read_leaf`'s combined identity/size guard,
or bounded streaming. The compact legal publisher payload is below this boundary;
a padded reader fixture would require controlled bytes and matching locator
metadata. No filesystem qualification, post-execution retrieval, full replacement
catalog, G02/G12 acceptance, or recovered original-port credit follows. The
original 78-test node identities and test bytes remain **UNKNOWN**.

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
| T06 zero/real callers | I `test_resolved_targets_keep_findings_and_cache_without_zero_target_output[False]` and `[True]`; `tests/test_cross_repo_impact.py::TestTopNCap::test_cap_at_top_n` | The retained top-10 length regression; stable tie-order is not promised; the later T06 chain/selection regression is bounded evidence, not a new multi-hop or coverage guarantee. |
| T07 sibling isolation | I `test_zero_targets_emit_once_before_sibling_access`; C `test_duplicate_occurrences_retain_distinct_first_use_native_fallbacks` | Native stages and duplicate occurrences are measured, not an atomic environment snapshot. |
| T08 production publication | P `test_host_publication_strictly_reads_after_temporary_cwds_are_removed`; G `test_docs_only_native_ci_keeps_gate_state_with_diagnostic` | Removing the actual publication call yields semantic RED; no hand-built artifact substitutes for this path. |
| T09 state replacement | P `test_uncached_branch_transitions_get_new_records_and_cache_reuses_publication` | Genuine uncached transitions create new sequences; cache retains the original record. |
| T10 same hash, new host | P `test_new_host_execution_never_reuses_same_source_hash_identity`; A `test_new_execution_with_identical_source_and_diff_cannot_reuse_record` | New host execution is supported; unsupported HOLD recovery is not inferred. |
| T11 cache same input | C `test_duplicate_occurrences_retain_distinct_first_use_native_fallbacks`; P `test_uncached_branch_transitions_get_new_records_and_cache_reuses_publication` | Strengthened oracle checks actual/cache metadata, original record identity, unchanged SQL and publication attempts. Removing cache-origin marking now fails. |
| T12 context mismatch | C `test_installed_capture_admission_precedes_native_work` (four cold/warm closed-installed/foreign-thread controls; supplement below); C `test_changed_capture_refuses_before_cached_findings_queries_or_error_clear`; C `test_changed_input_refuses_before_cache_sql_error_clear_or_publication[exact-diff]`, `[lexical-root]`, `[canonical-root]` | Each newly guarded comparison has its own exact omission RED. Same-UID filesystem generation continuity remains excluded. |
| T13 gate isolation | G `test_docs_only_native_ci_keeps_gate_state_with_diagnostic[published-PASS]`, `[published-FAIL]`, `[publication-fault-PASS]`, `[publication-fault-FAIL]` | Reachable docs-only native gate, State and receipt evidence. Injecting the diagnostic into State errors yields four direct membership assertion failures. No code-mutation workload or model-quality claim. |
| T14 recovery | P `test_new_host_execution_never_reuses_same_source_hash_identity` | Only new supported host identity is proved. Cross-repository HOLD is unsupported; single-repository recovery has no axis. T14 scope disposition accepted on 2026-11-14; this is not recovery PASS. |
| T15 write failure | A `test_precommit_operation_failure_is_not_published`; `test_incomplete_write_never_publishes_and_removes_owned_temp`; `test_link_error_distinguishes_verified_commit_from_unknown`; G publication-fault cases | Open/write/partial/commit ambiguity are covered. No automatic retry, crash journal or full-State-save behavior is introduced. |
| T16 strict format/context | A `test_duplicate_keys_in_otherwise_valid_document_rejected`; `test_wrong_independent_context_never_returns_empty_success`; `test_large_or_deep_document_rejected_before_json_load`; P `test_real_strict_read_rejects_lost_corrupt_or_wrong_execution_before_output` | Exact duplicate, context, pre-parser byte/depth and content-digest omissions fail. Exhaustive per-validator omission coverage and the original port remain unqualified. |
| T17 concurrency/duplicate | A `test_same_key_always_refused_without_replacing_original`; P `test_new_host_execution_never_reuses_same_source_hash_identity`; Q `test_concurrent_distinct_execution_publications_remain_independent`, `test_concurrent_same_key_refuses_contender_without_replacing_winner`, `test_concurrent_publishers_preserve_foreign_pending_replacement` | Registered public library controls now overlap production publisher calls with deterministic commit scheduling. Same-key input is adversarial, not a natural host collision. Complete-host concurrency and full T17 admission remain unqualified; see the bounded supplement below. |
| T18 cleanup | P `test_host_publication_strictly_reads_after_temporary_cwds_are_removed`; A `test_durable_root_cannot_overlap_temporary_cwd`; W `test_pending_body_failure_survives_cleanup`, `test_partial_setup_cleans_only_registered_cwd`, `test_multiple_cleanup_failures_keep_priority`, `test_ignored_permission_error_keeps_residue_and_result` | Public native controls now distinguish joined workers, registered cwd removal, exception identity and actual residue from diagnostic staging cleanup. The bounded repair and remaining outer boundaries are described below; this is not an unconditional T18 pass. |
| T19 interruption | A `test_process_control_propagates_and_preserves_committed_record`; `test_cleanup_signal_does_not_replace_primary_signal`; P `test_output_process_control_preserves_committed_file_and_exception_identity` | Selected controls distinguish type/message, object identity, file presence and strict reread as mapped in current T19; not every node asserts all four. No forced process-death, SIGKILL, power-loss or global-cleanup equivalence is claimed. |
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

The host constructs the independent expected identity before publication. `DiagnosticCapture.finish` retains `Record` and `PublicationResult`; the primary worker's runner/host path calls `present`, which passes the retained context to the strict reader. CLI and immediate/background MCP expose Verdict/text, not a new locator/context return. G04/G09 still require a decision on whether this actual private worker consumer satisfies the contract. This paragraph records the pre-#491 default path. The implemented #491 opt-in and its separate caller strict read are recorded in the current entry table above; no public session interface or latest-file scan is implied.

## G01/G02/G13 admission record

| Gate | Current record | What this follow-up does not grant |
| --- | --- | --- |
| G01 | Fixed contract, four-P1 disposition and conflicting-clause/support-domain decisions are now explicit. | A maintainer's disposition of remaining conflicts or an unconditional P1-3/T18f pass. |
| G02 | Concrete closed v1 codec, bounds, actual consumers and stronger strict-decoder controls are identifiable. | This historical follow-up alone granted no formal schema freeze; the subsequent limited G01/G02 owner decision above is separate. No recovery of the old port or exhaustive per-check mutation coverage. |
| G13 | Independent reviews and reproducible tests are evidence for the exact changed files and fixed product source. | Formal G0/G1, retrospective approval of every older proposal, or completion of #441/#33. |

An admission decision should name the product revision and this evidence revision, accepted consistency/storage/recovery domain, treatment of the original port, exact remaining controls and T18f disposition. Record the reviewer conclusion separately from the maintainer's explicit acceptance. A merged PR or successful CI check is not that record.

#33 and #441 remain open. Drafts #443/#444 remain independent review-support artifacts until a verified replacement and explicit disposition exist.

## Bounded T17 publication-contention supplement

The [publication-contention evidence](cross-repo-diagnostics-publication-contention.md) adds six public nodes at production baseline `f4c9214a813cd5604ed63462b3268343ed6f800c`: three overlapping real-publisher cases plus three harness-failure controls. It records distinct-execution isolation, adversarial same-key non-overwrite with an unknown/no-locator loser, foreign staging preservation, and four source-specific RED/restored-GREEN controls. These library calls use controlled commit scheduling, not simultaneous complete hosts. Full T17/A06, G08/G10/G12, G04/G09 and G0/G1 decisions remain open (the limited G10 three-state semantics are separately accepted above); no production or lifecycle behavior changes.

## Native CLI/MCP consumer-chain supplement

Six docs-only cases in `test_cross_repo_native_consumer_chain.py` bridge the
previous native dispatch-helper coverage to public immediate/background MCP
handlers and their real CLI child. The owned launcher invokes the genuine parser
and observes, rather than replaces, CI, L1, real falsification and advisory axes.
The backend is a deterministic local executable; PASS/FAIL is computed by native
CI. Temporary real Git/SQLite inputs, trusted backend configuration, subprocess
outlet, one clean round and the promotion/release budget are controlled. The
optional provider-free setup replaces only its pure registry-path locator.

Each execution retains expected identity at host capture construction, before
publication. Bounded test metadata records exact consumer counts, strict-read
results, gate-state equality and receipt hashes without serializing errors,
tracebacks or backend prompts. The parent compares that independently issued
identity with the reader and stored payload, checks unchanged receipt bytes at
the copied source-root destination, and requires the attributed diagnostic in
both structured MCP output and visible text. This metadata is test
instrumentation, not a production result outlet or post-return retrieval API.

Four cases cover primary native PASS/FAIL through immediate and background
handlers. Two immediate cases remove the newly published record just before its
real strict read: rejection retains the immediate diagnostic and adds exactly
one persistence warning, without changing the native primary decision or receipt
gates. The sibling executes genuine CI too: this docs-only fixture has no sibling
L1, so its one native coverage gap produces FAIL and an explicit sibling warning.
The host's existing primary-authoritative return remains PASS or FAIL as computed
for the primary. These cases do not claim all repositories pass.

Every case checks worker-directory and source-lock cleanup. Background cases
retain and await their own wait tasks; both paths observe and settle the actual
communicate task. Failure cleanup uses the existing native CLI process-group
termination and reaping operation. Native backend invocations create separate
sessions, so the owned no-descendant backend independently records PID/start-time
and normal-exit markers and has a five-second self-watchdog. Tests require those
exact process identities to disappear, separately from CLI reaping. There is no
global job/process cleanup or general detached-process lifecycle claim. Same-source
named-target transitions are deferred
rather than turning a synthetic README node into purported named-code evidence.
This supplement does not qualify mutation workloads, HOLD/recovery, SDK-stdio or
MCP wire transport, provider graph freshness, original-port equivalence, or formal
G04/G09/G0/G1 acceptance. No production or #68/#400 interface changes are made.

Three isolated single-boundary controls discriminate this oracle:
replacing strict consumption with the in-memory observation fails all four normal
cases at the missing-read count; removing the diagnostic only from structured MCP
output fails both immediate cases at the output assertion; omitting the warning
from the read-loss fallback fails both loss cases at the warning assertion. Each
byte-exact source restoration passes all six cases. These intentional semantic
failures are not baseline regressions or additive coverage. Early development
failures corrected harness assumptions about the repository-binding object,
sibling coverage and fallback tuple length; none is presented as a product bug.

## Installed-capture admission supplement

Reconstructed on main baseline `4a356189f968b463777a93b317aa4461630fde08`
after loss of the earlier local workspace, the five public controls below were
retained and revalidated on `9e5c1c62382308bbebb7f1a1e5743bbfd0fda44c`. They
strengthen bounded G06/T12 admission evidence in `tests/test_cross_repo_capture.py`.
Earlier run results are not reused as qualification of the latest composition.

- `test_active_owner_native_cold_and_warm_delivery` checks an independently
  specified caller finding, real cold SQL, publication and strict reading against
  context held before publication. Warm reuse retains the original record and
  bytes, with no new SQL or publication and delivery origin `cache`.
- `test_installed_capture_admission_precedes_native_work` has exact parameters
  `[closed-installed-cold]`, `[closed-installed-warm]`, `[foreign-thread-cold]`
  and `[foreign-thread-warm]`. Closure leaves the same capture installed. Thread
  refusal uses an actual `threading.Thread`, a bounded join and explicit
  harness-failure checks, not a patched thread identifier. The creator remains
  idle during that call. Only completed joins qualify. The daemon and ten-second
  join bound waiting, not cleanup of a timed-out worker; timeout is a harness
  failure, never semantic RED. Disposable omission runs also use an outer
  subprocess watchdog.

Each negative case checks empty return, unchanged sentinel errors, no new SQL,
publication, strict read or record, unchanged bound cache/error/record identities,
`context_unavailable`, `not_delivered`, no delivery origin and one persistence
warning. Cold refusal creates no diagnostic namespace. Warm refusal preserves
original artifact bytes and a subsequent strict read using the independently
held context. Real publisher/reader observers delegate unchanged and assert
counters outside production exception handlers. Targets-resolved observations
have no persisted message payload: successful persistent delivery is established
by strict reading and delivery status. The separate immediate query summary is
expected on legitimate native analysis, not repeated by a warm cache return and
never emitted on refused admission.

Owner-thread reuse succeeds after foreign-thread refusal. Closure recovery uses
a fresh capture/execution and repository identity, never reopens the old capture,
and rejects its foreign record. Historical refusal text need not be cleared by
successful reuse.

Dedicated disposable-source experiments independently remove the active check,
thread-identity check, and complete four-line runner admission consumer branch.
Semantic RED requires the exact returned-finding refusal assertion, including
JUnit failure message, terminal source assertion and traceback location. Exact
source restoration must pass. No setup, collection, skip, worker exception or
timeout counts as semantic RED. Fresh execution results are recorded separately
from this control map.

These controls qualify sequential misuse refusal, not simultaneous runner
sharing, host thread migration, process isolation, a natural host concurrency
bug, all A04/T12/G06 obligations, private consumer acceptance, G0/G1 or issue
closure. Optional-provider fallback replaces only its pure default-path helper;
registry files, SQLite, runner admission, publisher and strict reader remain real.
The admission supplement does not change production admission behavior or make
claims about separately owned #68/#400 work. No hosted CI rerun is included in
its local evidence.
