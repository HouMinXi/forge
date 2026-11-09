# Cross-repository advisory source-root routing

Baseline: 97a2ce29d2c9ba53adb51caef365ab4b351280a6. Narrow prerequisite to #441; its execution-bound diagnostic protocol remains G0 REQUEST_CHANGES and is not implemented here.

## Problem and scope

run_cross_repo gives each worker a temporary state/config cwd and the real repository as source_root. Only the primary worker installs CrossRepoImpactRunner. StateMachine._run_advisory_axes currently passes cwd to this runner, so default registry lookup misses the registered source repository. Pointing CRG_DATA_DIR at the correct database still fails its repository-root identity check. Direct runner calls with a real root are unaffected.

Change only the root argument for CrossRepoImpactRunner in the existing advisory dispatcher: pass _source_root(). Keep the public runner signature, graph-root validation, registry precedence, SQL, findings, cache, State persistence, receipts and diagnostic/error channels unchanged. Other runners retain their current routing. For subclasses of CrossRepoImpactRunner, the same repo_root contract applies (isinstance dispatch); no method-identity guard is needed because no additional keyword arguments are supplied. Without an explicit source_root, the existing helper falls back to cwd.

No audit/#68 lifecycle changes, generic runner protocol expansion, diagnostic artifact schema or publication changes. This fix may restore previously absent static advisory findings, but does not establish global impact, graph freshness or execution coverage. It does not fix temporary advisory-file persistence.

## Controls and implementation boundary

Production: src/code_forge/machine.py, _run_advisory_axes only. Tests: tests/test_cross_repo_advisory_root.py and public identity manifest. This document records the narrow design. Do not alter shared lifecycle or graph-root checks.

Use a real run_cross_repo orchestration with actual Git repositories and SQLite graphs. Limit the machine's expensive review body through a test subclass whose run calls the real _run_advisory_axes; do not replace dispatch, CrossRepoImpactRunner.run, graph discovery, SQL or identity validation. This is a controlled production advisory path, not whole-review convergence/native verification. Keep only the target axis in this focused fixture, with a separate generic-runner sentinel to verify cwd compatibility.

Controls: primary default registry data_dir and CRG_DATA_DIR; actual matching caller; no named target; wrong graph root still refused; fallback when source_root is absent; inherited runner contract; unrelated runner still receives cwd. Assert real source root and separate temporary state root, no sibling target-axis invocation, and actual cleanup of worker directories. Every test must reach its semantic assertion, with dependency errors/skip excluded from RED evidence.

Baseline RED must be the matching-caller control failing through real orchestration. GREEN follows the narrow dispatcher change. Negative control restores only old argument routing and must fail that same semantic assertion. Separate negative control removes graph-root mismatch rejection in an isolated disposable source overlay and must fail the foreign-root assertion. Restore candidate before final validation.

Run applicable cross-repo/impact/advisory/root-identity regressions, public-test identity checks, complete repository lint/format (including Python Markdown fences), and independent five-lens review. No unapproved CI, push, PR or merge. Local SQLite/Git tests are not native sandbox qualification.

## Gate record

User explicitly authorized implementation of this independent prerequisite while retaining #441's protocol gate. Narrow design independent review: APPROVE, no blockers; explicit user authorization covers implementation of this prerequisite only. Implementation and validation receipts will distinguish RED/GREEN, negative controls, limits and review outcomes; no inherited v2 CLEAN.


## Local verification receipt

Baseline on unchanged product: 6 failed / 2 passed, all semantic assertions reached. Candidate: 8 passed. Disposable-checkout negative controls: restoring only the old cwd argument caused both caller controls to fail; removing only the metadata root-mismatch rejection caused both foreign-root controls to fail. An initial PYTHONPATH-only overlay was shadowed by the root conftest and unexpectedly passed; it is invalid evidence and was replaced by the disposable checkout controls.

Applicable regression selection: tests/test_cross_repo*.py, test_machine_advisory.py, test_trusted_advisory_context.py, test_cli_advisory_wiring.py and test_advisory.py: 361 passed, no skips. Initial attempts had 17 provider-import failures; isolated missing dependencies were supplied before the final complete run. Full scripts/lint_changed.py --all: Ruff check passed, 939 files already formatted. New specification has no Python fences. Eight explicit new public node IDs match actual collection and validate_manifest accepts the complete manifest. git diff --check passed.

Independent final five-lens source/test review: APPROVE; no substantive blockers. The check covers correctness, trust boundary, compatibility, resources/failures and scope/tests. Controlled review bodies do not prove full convergence, native qualification, durable advisory publication or #441 protocol delivery. No CI, push, PR or merge has been performed. #427/#430 touch invocation_host.py and its tests, with only the public identity manifest shared; reconcile additive identity changes against remote before eventual publication.


## PR #442 collection compatibility correction

The first authorized PR CI run (42657199822, attempt 1, head 2f09539188ac98209423b58eb9057cc029bbbcf7) failed during plan collection. Its public diagnostic is collection/REJECTED; raw evidence was not published. Lint, CodeQL and installed-wheel checks passed. This identifies the stage, not every private cause.

A local provider-absent environment with pinned pytest 9.2.0 reproduced a concrete incompatibility: the new module's importorskip created an unapproved collection skip. Collection itself returned exitstatus 0 (13 other nodes plus one skipped module), but the unchanged check_collection rejected it. This is a collection-policy RED, not a semantic test failure or proof that no other CI problem exists.

Remove that optional package collection dependency. The graph_repos fixture now temporarily supplies only code_review_graph.registry.default_registry_path, returning the fixture registry path. Real Git, orchestration, advisory dispatch, registry JSON reads, discovery precedence, SQL and root identity validation remain intact. The fixture does not verify the third-party helper itself. monkeypatch restores the prior package/registry module objects after all workers have joined; provider-present and provider-absent process controls verified restoration.

After correction: the same pinned provider-absent collection yields 21 nodes and no skips, accepted by the unchanged policy; all eight target controls execute and pass. The eight identities remain unchanged. Applicable provider-present regressions: 361 passed, no skips. Both isolated source negative controls still yield two semantic failures each without the optional provider. Full lint/format passes (939 files). Independent five-lens review approves this test-only correction; production code, CI installation, skip policy and manifest are unchanged. No further CI round or push is authorized by this local correction.
