# Phase 44 CP1 — Plan-Check Round 2 (re-verification of revised 44-01/44-02)

**Checked:** 2026-08-22, after round-1 FAIL (B=3 W=7) and planner revisions
per 44-DISCUSSION-LOG "CP1 Plan-Check Round 1" section. Stance: FORCE —
revisions assumed flawed until code proved otherwise. Every file:line anchor
re-grepped against main @ 8bd01bc, never trusted from the plan text.

Ground truth re-verified this round:
- `eval/corpus.py` `load_corpus` :81-130 — reads ONLY
  name/diff_file/expected_verdict/axis_tags/expected_advisory/
  expected_findings (:110-128); RAISES ValueError on missing/invalid
  expected_verdict (:111-116). Unknown keys are never touched — ignored.
  `axis_tags: list[str]` free-text (:76) — no enum. CorpusEntry has NO
  gate-config field.
- `eval/runner.py` `_create_gate_yaml` :522-563 — reads an existing
  gate.yaml (:537-541) and merges the harness backend under
  `existing["backends"][backend_name]` (:553-557). Non-backend top-level
  keys of the EXISTING dict survive untouched — a foreign `test:` section
  (with a hostile `test.command`) is preserved verbatim and written back
  (:559-562). Replay applies the diff first (`git apply`, :747-750) and
  only then calls `_create_gate_yaml` (:757), so an unstripped foreign
  gate.yaml in the diff WOULD reach the temp repo and its test.command
  WOULD be honored downstream. The hazard is real.
- `ledger.py` — `append_row(cwd, row)` :62, `iter_rows(cwd)` :77 both take
  a path and are importable anywhere. `iter_rows` KeyError-skips rows
  lacking any required field, including `fingerprint` (:101-116), so a
  fingerprint-less clean row would be silently invisible — diff-scoped
  clean-row fingerprint is genuinely required. No `resolve_ledger_root`
  exists yet (grep: 0 hits — greenfield, no collision).
- `machine.py` `_run_ci` terminal at :539-542
  (`verdict/converged/_persist_state/return verdict`) — the documented
  insertion point (after `_persist_state`, before `return`) is real.
  `_write_ledger_rows` :1300-1359 writes `repo_root=str(self.cwd.resolve())`
  (:1345) = the disposable worktree path — the round-1 WARNING was real and
  the Task 2 fix (field = `resolve_ledger_root(cwd)`) is aimed at the right
  line.
- `reviewer_json.py` fingerprint recipe sha256("file:line:desc")[:16] at
  :170-171 inside the shared `_json_to_state_findings` (Outlet A/C) —
  D-12 "no recompute" satisfiable.
- `gate_check.py` `load_gate_config` :39-80 — RAISES FileNotFoundError on
  a missing gate.yaml (:59-60) and ValueError when the `test` section is
  absent (:64-71). Both are reachable in foreign CI repos; the kill-switch
  read placement genuinely matters. FileNotFoundError is an OSError
  subclass; ValueError is NOT — so the round-1 demand to put the read
  inside the same try AND catch ValueError explicitly is substantiated.
- `cli.py` — `_run_ledger` :1458; `ledger_subs` :785; `mark --evidence`
  writes `args.evidence` raw into `evidence_class` (:1652, default
  "manual" :801-804) — the uncapped-evidence path the revision now caps.
  `mark` drops file/line/axis_claim on existing fingerprints
  (:1642-1654) — the D-10 gap adjudicate closes.

## PART A — Round-1 blocker resolutions

| # | Blocker | Verdict | Evidence |
|---|---------|---------|----------|
| B-1 | D-17 transport: no implementable mechanism for the toolchain stub | **RESOLVED** | 44-02 Task 2 now specifies the transport concretely: the extractor STRIPS any `.code-forge/gate.yaml` the materialized diff introduces BEFORE replay, so `_create_gate_yaml` starts from a clean slate and writes only the harness backend. Precedence rule stated ("harness backend is the ONLY backend config; foreign gate config discarded"). Testable: hostile `test.command` fixture with a marker file that must be ABSENT after replay (Task 2 behavior d). This directly kills the verified merge hazard (runner.py:537-557: existing non-backend keys survive; foreign `test:` section would be written back and executed). The mechanism is implementable (the extractor owns diff materialization — `git diff base..head` — so it can filter path entries before writing `diffs/<name>.diff`) and testable end-to-end via `git apply` + replay. |
| B-2 | `expected_verdict` never specified; two conflicting corpus formats | **RESOLVED** | 44-02 Task 1 behavior (a) derives `expected_verdict`: expect-catch → HOLD, expect-no-catch → PASS, citing corpus.py:106-111. Task 2 behavior (a) emits the load_corpus shape and demotes eval-bank v1 to REFERENCE-only (also fixed in the Task 1 read_first: "NOT the load target"). Verified against corpus.py: load_corpus raises ValueError when expected_verdict is missing/not HOLD/PASS (:111-116) and never reads unknown keys (:110-128 touch only the six known fields), so eval-bank-compat extras (diff_state, pre_fix_source, answers_file) ride harmlessly. The emitted format passes load_corpus. The format conflict is gone — one emitted format, stated explicitly ("ONE emitted format... not two 'target formats'"). |
| B-3 | main-repo path resolution not wired into read-side subcommands | **RESOLVED** | 44-01 Task 1 adds shared `resolve_ledger_root(cwd)` IN ledger.py (importable by machine.py, cli.py, eval/export.py — all three consumers can import from the same module as `iter_rows`/`append_row`). Read-side routing specified in all three consumers: 44-01 Task 3 (adjudicate + list --unadjudicated read via `iter_rows(resolve_ledger_root(cwd))`), 44-02 Task 3 (`export_eval(resolve_ledger_root(cwd), ...)`), plus the write side (44-01 Task 2 file location). Worktree read-side proof tests present in all three tasks: 44-01 Task 3 test (g), 44-02 Task 3 test (f), and the write-side proof 44-01 Task 2 test (h). The silent-ship hole (worktree review mandated, temp-dir tests pass while real worktree reads hit an empty worktree-local ledger) is closed end-to-end. |

## PART B — Full 6-dimension re-check

### 1. Requirement coverage
- All D-01..D-22 decisions have a landing site. D-16 now lands in the
  `_write_ci_ledger_rows` docstring (44-01 Task 2) — round-1 W-4 closed.
- D-07 evidence cap now covers the mark path too (44-01 Task 3 test h +
  action citing cli.py:1652) — round-1 W-5 closed.
- No requirement lost in the revision.

### 2. Interface accuracy
- `scripts/run_tests.sh` purged — every verify/verification block now runs
  `python -m pytest` (44-01:187,268,351,372; 44-02:173,247,308,326-327).
  Round-1 W-1 closed.
- axis "enum" misstatement corrected — 44-02 <interfaces> and Task 2 now
  state `axis_tags is list[str] free-text (:76)`. Verified accurate
  (corpus.py:76). Round-1 W-2 closed.
- corpus.py anchors (:81-128 loader, :106-111 verdict raise) — the raise
  is actually at :111-116 and the loader body at :81-130; plan line
  numbers are within honest drift and the cited behavior is exact.
- runner.py anchors (:522 create, :537-556 merge, :748/757 apply-then-
  gate) all verified accurate.

### 3. Scope reduction
- No "v1/simplified/placeholder" reduction of a locked decision. The D-17
  redesign (strip instead of carried stub) is a mechanism change, not a
  scope cut — D-17's ratified text ("toolchain-self-contained... must not
  require the foreign project's toolchain") is still delivered in full.
- Clean-row fingerprint now specified: sha256("clean:<base>:<head>")[:16]
  (44-01 Task 2 behavior b + action). Round-1 W-6 closed. The derivation
  is diff-scoped, satisfying the D-08 dedup key (fingerprint, base, head)
  and the iter_rows fingerprint requirement (ledger.py:103).

### 4. Dependency / wiring
- 44-02 → 44-01 coupling intact: fixture built via the real CI-write
  path with a hand-written-JSONL ban (44-02 Task 1 fixture + acceptance
  grep), D-18 coupling proof in the phase verification.
- Kill-switch read placement fixed: 44-01 Task 2 test (g) + action put the
  `load_gate_config` read INSIDE the same try/except as the write, with
  FileNotFoundError + ValueError explicitly isolated, and an acceptance
  grep ("load_gate_config call site is within the try block"). Round-1
  W-7 closed. Verified the underlying raises are real (gate_check.py:59-71;
  ValueError is NOT an OSError, so the explicit dual-catch is necessary,
  not decorative).
- repo_root field fixed: 44-01 Task 2 test (h) + action set the field to
  `resolve_ledger_root(cwd)` (main root), with the exact downstream
  consequence named (44-02's `git -C <row.repo_root> cat-file -e`).
  Round-1 W-8 closed. Verified the hazard line (machine.py:1345 writes the
  worktree path today).

### 5. Decision contradictions
- None found. The strip-before-replay mechanism is consistent with D-17's
  ratified intent (no foreign toolchain at replay; merge/defer to the
  runner's own generation — stripping IS the strongest form of "defer to
  the harness generation"). D-13 polarity, D-08 dedup key, D-15 counter
  precedence, D-19 two-layer kill-switch all still match CONTEXT.

### 6. Context budget
- 44-01 Task 2 remains the heavy task (CI write + persistence + dedup +
  2-layer kill-switch + failure isolation + 8 behavior tests a-h) — grew
  by one test (g, kill-switch read isolation) vs round 1. Still feasible
  for one executor; the shared-row-build extraction keeps LOC bounded.
- 44-02 Task 3 still spans two test files after the wave-2 executor
  authored the extractor in Tasks 1-2. Monitor stands (round-1 W-9): if
  the executor exceeds ~50% context before Task 3, split.

## PART C — New problems introduced by the revisions

- **WARNING — D-17 strip is specified as an outcome, not an algorithm.**
  44-02 Task 2 says "removes any `.code-forge/gate.yaml` the materialized
  diff would introduce" but never says HOW: the materialized artifact is a
  raw `git diff base..head` patch, so the executor must choose between
  (a) filtering the patch text to drop the `.code-forge/gate.yaml` file
  section (hunk-header parsing — the plan gives no parsing rule), or
  (b) deleting the file after `git apply` in the replay repo — but replay
  lives in runner.py, which the extractor does not control, and the plan's
  own acceptance is stated at the REPLAY level (marker absent after
  replay), not the extractor level. The clean reading is (a) — strip at
  emission so the corpus diff itself is toolchain-free — and the
  acceptance test (hostile test.command, marker absent) IS implementable
  under (a) via a hand-authored fixture diff. But an executor could
  legitimately pick (b), discover it requires touching runner.py (out of
  files_modified scope), and stall. One line naming the emission-side
  strip ("filter the `.code-forge/gate.yaml` file section out of the
  emitted patch text") would close it. Not a blocker: the test pins the
  required behavior and (a) is the only in-scope reading.
- **WARNING — strip-before-replay has no false-positive carve-out, and
  none is needed today — but the corpus shape now silently forbids a
  legit class of entries.** If a future ledger row's reviewed diff
  LEGITIMATELY adds/changes `.code-forge/gate.yaml` as the code under
  review (i.e., the gate config IS the change being reviewed), stripping
  it changes what the reviewer sees at replay vs what the original CI
  review saw — the replayed review no longer reviews the same diff. D-17
  as ratified accepts this (toolchain self-containment wins), so this is
  a documented-tradeoff gap, not a contradiction: the plan should note in
  export.py's docstring that gate-config-bearing diffs are exported with
  the gate section removed and the replay verdict applies to the stripped
  diff. Minor doc note; the module-docstring requirement already exists.
- **WARNING — kill-switch try-block scope vs env-var ordering unstated.**
  Task 2 wraps "the kill-switch config read AND the write in ONE
  try/except" and separately checks the env var. If the executor puts the
  env-var check inside the same try, an unexpected exception type from
  `os.environ` handling is impossible (no realistic failure mode), so this
  is cosmetic — but the acceptance grep only pins the `load_gate_config`
  call site, leaving the env check placement to taste. Harmless; noted
  for the reviewer pass.
- **No new blockers.** The clean-row fingerprint recipe
  (sha256("clean:<base>:<head>")[:16]) is consistent with the D-08 key
  and introduces no collision with finding fingerprints (different input
  namespace — "clean:..." prefix cannot collide with "file:line:desc"
  recipes since real recipes never start with "clean:" followed by two
  40-hex SHAs... a pathological file named "clean:<base>:<head>" with
  line 0 and empty desc COULD theoretically collide, probability
  negligible and requires a hostile filename; not worth a plan change).
  repo_root = resolve_ledger_root for BOTH the file location and the row
  field keeps the two consistent (44-02 resolves SHAs against the same
  root the CI wrote from). No contradiction found in any revision.

## Residual warnings carried forward (non-blocking)

- W (44-02 Task 3 test-file load): monitor at execution, split if the
  wave-2 executor exceeds ~50% context before Task 3. Unchanged.

VERDICT: PASS
SCORECARD: B=0 W=3
