# Phase 44 CP1 — Plan-Check Verdict (44-01-PLAN, 44-02-PLAN)

**Checked:** 2026-08-22, goal-backward against 44-CONTEXT.md (D-01..D-22),
44-RESEARCH.md (R-1..R-8), and live code on main @ 8bd01bc.
**Stance:** FORCE — plans assumed flawed until code proved otherwise. All
cited file:line anchors re-grepped, not trusted.

## Verified-accurate interface anchors (no finding)

- `machine.py:_run_ci` :310; CI terminal `verdict/converged/_persist_state/
  return` at :540-542 — insertion point real (44-01 <interfaces>).
- `machine.py:_write_ledger_rows` :1300; SHA guard :1318-1319; LOCAL-only
  call sites in `_finalize_local_terminal` (:1206, calls :1240/:1260/:1283/
  :1297). D-12 extension target exists.
- `reviewer_json.py` fingerprint recipe sha256("file:line:desc")[:16] at
  :171 inside `_json_to_state_findings` (:155-181), shared Outlet A/C — D-12
  "no recompute" is satisfiable.
- `cli.py:_run_ledger` :1458; mark branch :1474; list branch :1661;
  unknown-subcommand :1691; argparse ledger_parser :781 / mark_parser :789.
  Re-ruling drops file/line/axis_claim (writes ""/0/"manual",
  :1643-1654) — the D-10/B-2 gap is real.
- `gate_check.py:load_gate_config` :39-100; no `ledger:` section today — D-19
  config kill-switch is greenfield.
- `runner.py:_create_gate_yaml` :522 — merge semantics confirmed (reads
  existing, merges harness backend, never writes test.command by default).
- `runner.py` replay uses `git apply <diff_file>` (:748); only the extractor
  needs live SHAs — R-5 reframe of D-03 confirmed.
- D-13 polarity: `resolved_review.base_sha/head_sha` consumed at
  machine.py:1316-1317, resolved via `resolve_baseline` before the run
  (cli.py:2985+) — pre-fix SHAs, no write-path change needed. Confirmed.

## Dimension findings

### 1. Requirement coverage

- **BLOCKER — D-17 (toolchain stub) has no implementable mechanism.**
  44-02 Task 2 requires each entry to "carry a review-only minimal gate
  config stub that MERGES with the runner's _create_gate_yaml output", and
  D-17 explicitly demands "Planner must specify the merge/precedence rule."
  The plan never says WHERE the stub lives or HOW it reaches replay:
  `CorpusEntry` (corpus.py:55-78) has no gate-config field, `load_corpus`
  (:81-128) reads only name/diff_file/expected_verdict/axis_tags/
  expected_advisory/expected_findings, and `replay_entry`
  (runner.py:566, :748-757) consumes only the diff file before calling
  `_create_gate_yaml`. Injecting the stub into the materialized
  `base..head` diff would corrupt the foreign diff. Worse, the actual D-17
  hazard is unaddressed: a foreign diff that itself adds
  `.code-forge/gate.yaml` with a `test.command` gets merged by
  `_create_gate_yaml` (existing dict wins for non-backend keys,
  runner.py:537-556) and replay WOULD invoke the foreign toolchain —
  the plan's "no test.command" stub does nothing to strip it. Acceptance
  criterion "Toolchain stub merges without colliding with _create_gate_yaml"
  is untestable as specified. An executor cannot deliver D-17 from this text.
- **BLOCKER — 44-02 never specifies `expected_verdict` for emitted entries,
  and the two referenced corpus formats conflict.** Task 2's acceptance
  demands "Emitted manifest validates via the real load_corpus loader", but
  `load_corpus` RAISES ValueError when `expected_verdict` is missing or not
  HOLD/PASS (corpus.py:106-111). The plan's classification spec (D-02/D-13:
  expect-catch vs expect-no-catch) maps to `expected_findings`, and no task
  or behavior line derives `expected_verdict` (catch→HOLD, no-catch→PASS).
  Simultaneously the plan's <context> and read_first cite
  `.planning/eval-bank/v1/manifest.yaml` as "target corpus format" — that
  legacy shape (`repo/commit/answers_file/axis/diff_state`, no
  `expected_verdict`) does NOT load via `load_corpus` at all. An executor
  following the plan hits a ValueError wall at the acceptance test.
- **BLOCKER — main-repo path resolution is not wired into the read-side
  subcommands (D-05/D-10/D-20b/R-4 broken end-to-end).** 44-01 Task 2
  delivers main-repo ledger resolution (git-common-dir) inside the machine.py
  CI write path only. R-4 explicitly requires "the same path function is
  reused by adjudicate/list/export so all subcommands read the SAME ledger
  the CI wrote", but Task 3 (adjudicate / list --unadjudicated) specifies
  `iter_rows(cwd)` semantics with no mention of the main-repo resolution, and
  no shared resolver is added to ledger.py or cli.py in any task's
  <files>/<action>. Since forge mandates linked-worktree reviews
  (cli.py:2774-2780 refuses main-tree review), CI rows land in the MAIN repo
  ledger while `ledger adjudicate`/`list` run from a worktree would read the
  worktree-local ledger and find nothing. Task 3's temp-dir tests still pass
  (cwd == main repo there), so this ships broken silently. Same hole in
  44-02: `export_eval(ledger_root, ...)` — the CLI branch never specifies
  passing the resolved main-repo root.
- **WARNING — D-16 is name-dropped only.** Task 2's name cites D-16 but no
  action, file, or acceptance criterion documents the growth expectations /
  compaction-deferred trigger (>10k rows). Documentation decisions still
  need a landing site (docstring, commit body, or summary).
- **WARNING — D-07 evidence cap left open on the existing `mark` path.**
  Task 1 adds `_truncate_evidence` and Tasks 2/3 apply it to the CI writer
  and adjudicate, but the pre-existing `ledger mark --evidence` path
  (cli.py:1647) writes args.evidence raw. A >PIPE_BUF row remains reachable
  via mark, contradicting "Write path must truncate evidence" read broadly.

### 2. Interface accuracy

- **WARNING — `scripts/run_tests.sh` does not exist.** Every <verify> and
  <verification> block in both plans invokes it (44-01:175,236,298,317-318;
  44-02:156,206,256,273-274). Repo has no `scripts/` dir; prior-phase plans
  (e.g. 30-01-PLAN.md:226) use `python -m pytest` directly. Executors will
  substitute, but as written every verify command fails immediately.
- **WARNING — 44-02 <interfaces> misstates the corpus axis model.** "Corpus
  axis is an enum (verify/llm/machine)" is false: `CorpusEntry.axis_tags` is
  `list[str]` (corpus.py:76), no enum anywhere in corpus.py; eval-bank v1
  uses free strings incl. "review-hardening". The D-14 mapping task is still
  valid, but the executor is handed a wrong target type.
- Line anchors otherwise accurate (see verified list above). Drift warnings
  in the plans are honest.

### 3. Scope reduction

- No "v1/simplified/placeholder" reduction of a locked decision found.
  Planner-exercised options stay within ratified bounds: D-06 option (b)
  docstring-rewrite (44-01 Task 1) and D-14 skip-with-warning fallback
  (44-02 Task 2) are both explicitly offered by CONTEXT.
- Silent omissions = the BLOCKERs in Dimension 1 (D-17 mechanism,
  expected_verdict, read-side path resolution) plus:
- **WARNING — clean-row fingerprint unspecified (D-07).** The
  axis_claim="clean" run-level row must still carry a `fingerprint`
  (iter_rows KeyError-skips rows lacking one, ledger.py:103) and the D-08
  dedup key (fingerprint, base, head) implies it must be diff-scoped, else
  successive clean runs on different diffs collapse or cross-collide. No
  task specifies its derivation.

### 4. Dependency / wiring

- 44-02 depends_on 44-01 honored: Task 1 fixture builds the ledger via the
  real CI-write path with a ban on hand-written JSONL (acceptance grep),
  and the phase verification includes the D-18 coupling proof (break 44-01's
  writer, watch 44-02 FAIL). Good.
- key_links reference real functions (`_write_ledger_rows`,
  `_create_gate_yaml`, `iter_rows`, `append_row`) — all exist at or near the
  cited lines.
- **WARNING — kill-switch read failure modes unspecified (D-19).**
  `load_gate_config` raises FileNotFoundError (no gate.yaml) and ValueError
  (no `test` section, gate_check.py:64-70) — both reachable in foreign CI
  repos. Task 2 says the kill-switch is "checked first" and only the write
  is wrapped in try/except OSError. FileNotFoundError is an OSError
  subclass, but if the config read sits before the try block, a missing or
  ledger-less-but-valid gate.yaml (ValueError) propagates and can change the
  review verdict — exactly what D-19 forbids. Placement and exception scope
  must be specified.
- **WARNING — repo_root FIELD value unspecified for worktree writes.**
  44-01 specifies the ledger FILE location (main repo) but not the row's
  `repo_root` field. Existing `_write_ledger_rows` writes
  `str(self.cwd.resolve())` (machine.py:1342) = the disposable worktree
  path. Post-merge, 44-02's `git -C <row.repo_root> cat-file -e` fails on
  every such row → mass stale-skips. `--repo-root` (D-09) is a manual
  workaround, not a fix; the shared row-build extraction should define the
  field as the main-repo root.

### 5. Decision contradictions

- None found. D-13 polarity (FIXED/ESCAPED→expect-catch,
  DISPROVED/DUPLICATE→expect-no-catch, diff_state=pre-fix), D-08 dedup key
  (fingerprint, base_sha, head_sha), D-15 counter precedence
  (unadjudicated > stale-sha > dedup-collapse, sums to total), D-19 two
  layers, D-06 naming honesty, D-10 inheritance, D-22 hygiene semantics —
  all plan text matches CONTEXT. (The D-17 failure above is omission, not
  contradiction.)

### 6. Context budget

- 44-01 Task 2 is the heavy task (CI write + persistence + dedup + 2-layer
  kill-switch + failure isolation + 7 behavior tests, est. ~250 LOC of the
  ~450) — tight but feasible for one executor. 44-02 Tasks 1-2 similar.
- **WARNING — 44-02 Task 3 test file load.** Task 3 adds CLI coverage to
  both tests/test_export_eval.py and tests/test_cli_ledger.py on top of the
  wave-2 executor having authored the whole extractor in Tasks 1-2 of the
  same plan; combined with the BLOCKER fixes (expected_verdict, D-17
  mechanism) this task grows. Monitor; split if the executor exceeds ~50%
  context before Task 3.

## Required planner fixes (all BLOCKERs)

1. **D-17 mechanism (44-02 Task 2):** name the transport for the review-only
   gate stub (e.g. strip/override `test.command` from any gate.yaml the
   applied foreign diff introduces, or extend CorpusEntry/manifest with a
   per-entry gate-stub field replay honors) and state the merge/precedence
   rule against `_create_gate_yaml` (runner.py:522-564), including a test
   where the foreign diff carries a hostile `test.command`.
2. **expected_verdict (44-02 Task 2):** specify the derivation
   (expect-catch → HOLD, expect-no-catch → PASS) so the emitted manifest
   passes `load_corpus` (corpus.py:106-111), and resolve the format conflict
   — either emit the load_corpus shape (expected_findings inline) or the
   eval-bank shape, not both as "the target format". If eval-bank-compat
   fields (diff_state, answers_file) are desired, state that load_corpus
   ignores unknown keys and keep expected_verdict present.
3. **Shared ledger path resolution (44-01 Tasks 2-3, 44-02 Task 3):** put
   the git-common-dir main-repo resolver in a shared, importable location
   (e.g. ledger.py), and specify that adjudicate / list --unadjudicated /
   export-eval all resolve the ledger through it, so worktree-invoked
   subcommands read the ledger the CI write path wrote.

VERDICT: FAIL
SCORECARD: B=3 W=7
