# Phase 44 EVAL-ON-DUTY: R2 Adversarial Review

## Part A — Resolution Verification

| Finding | Verdict | Evidence / Code Citation |
|---|---|---|
| **B-1** (D-03 manifest/skip contradiction) | **RESOLVED** | D-03 explicitly says "No manifest entry is generated for a skipped row" |
| **B-2** (D-10 adjudication drops metadata) | **RESOLVED** | cli.py:1643-1654 verified, D-10 mandates a new `ledger adjudicate` command that inherits file/line/axis_claim |
| **H-1** (UNADJUDICATED behavior in export undefined) | **RESOLVED** | D-15 requires export-eval to skip UNADJUDICATED rows with counts/hints |
| **H-2** (uncapped evidence breaks PIPE_BUF) | **RESOLVED** | D-07 capped at <=500 chars and added 2048 bytes serialization test |
| **H-3** (Diff polarity inversion) | **RESOLVED (with gap)** | D-13 upgraded diff extraction semantics. *Gap noted below*: `machine.py:1316-1317` gets base/head from `resolved_review`, which is snapshotted BEFORE the local fix loop, providing pre-fix SHAs, not confirmation-time SHAs. |
| **H-4** (Ledger unbound growth) | **RESOLVED** | D-16 documents growth expectations, defers compaction. Appended O(N) scan. |
| **H-5** (Scope > LOC target) | **RESOLVED** | D-18 splits the phase into two plans: 44-01 (write side) and 44-02 (extraction) |
| **M-1** (UNADJUDICATED flooding) | **RESOLVED** | D-16 documents/accepts inherent growth, D-08 dedups |
| **M-2** (repo_root absolute path) | **RESOLVED** | D-09 allows `--repo-root` remap in `export-eval` |
| **M-3** (D-12 CI fingerprint frame wrong) | **RESOLVED** | Corrected frame in D-12: fingersprints already computed in `reviewer_json.py` + `_run_ci` lacks hook. |
| **M-4** (Concurrent dedup race) | **RESOLVED** | D-08 extension: extractor dedups on read, accepting check-then-act race |
| **M-5** (Replay toolchain isolation gap) | **RESOLVED** | D-17 mandates self-contained minimal toolchain config stub per entry |
| **L-1** (TerminalState enum compat) | **RESOLVED** | D-06 accepts `iter_rows` silent skip strategy |
| **L-2** (Absolute path PII) | **RESOLVED** | D-09 requires dropping paths during manifest generation |

## Part B — Attack the NEW Decisions

- [B] **D-15 Double-Count Path in Export Summaries** — D-15 skips both stale-SHA and UNADJUDICATED rows. Since D-03 states stale-SHA check happens during extraction (`git cat-file -e`), and UNADJUDICATED runs skip manifest emission, an UNADJUDICATED row that also has a dead SHA will trigger both filters. If the summary simply increments counters as checks fail sequentially, that single row is double-counted.

- [H] **D-13 SHA Sourcing Defect in `_write_ledger_rows`** — `machine.py:1316-1317` (`base = self.resolved_review.base_sha`, `head = self.resolved_review.head_sha`) sources the SHAs from `resolved_review`, which is captured at the START of a LOCAL run (`machine.py:201, 218`). The SHAs represent the initial diff under review, not the state of the repo "at finding-confirmation time" (post-fix). If a user iterates locally, the fix is applied, but the terminal finding output uses the originally reviewed SHAs (the pre-fix SHAs). D-13 states "LOCAL-mode FIXED rows... SHAs may reference the POST-fix diff", but the code explicitly uses the pre-fix diff SHAs. This means LOCAL-mode FIXED rows actually do extract correctly, and D-13's assertion of inverted semantics is wrong because it misunderstood the `machine.py` architecture.

- [M] **D-16 O(N) Scan Cost Amplification in CI** — `machine.py:1316-1358`, `ledger.py:77-122`. While D-16 accepts the O(N) scan for current CI volume, the CI write pattern described in D-01 emits a row *per PASS run*. If a repo does 20 CI runs a day, it appends 20 rows. In 6 months, that is ~3600 rows per repo. The O(N) scan parses `ledger.jsonl` every single run.

- [M] **D-17 Config Isolation Conflict (Replay vs Toolchain)** — `eval/runner.py:757-768`. Replay creates `.code-forge/gate.yaml` (`_create_gate_yaml`) using the `backend_name` and `backend_config` argument. If D-17's "minimal config stub" is written, it risks overwriting or colliding with the temp repo's own harness `.code-forge/gate.yaml`. The runner needs logic in `_create_gate_yaml` to seamlessly merge or respect the D-17 stub.

- [M] **D-18 Split Coupling Risk** — `export-eval` (Plan 2) depends entirely on `ledger adjudicate` and CI-writes (Plan 1) actually working. If Plan 1 creates malformed rows or fails to inherit fields correctly (D-10), Plan 2's extractor will fail. Both plans touch the same core `ledger.py` and `TerminalState` schemas.

SCORECARD: B=1 H=1 M=3 L=0
