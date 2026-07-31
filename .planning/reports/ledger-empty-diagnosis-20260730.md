# Ledger empty diagnosis -- 2026-07-30

Diagnosis only, per dispatch `.planning/dispatch/dispatch_ledger_empty_diagnosis_20260730.txt`.
No file under `src/` or `tests/` was changed. HEAD left at `695f739`. The
staged `src/code_forge/lock.py` / `tests/test_lock.py` change in the
index was not touched. All experiments ran in scratch directories under
`/tmp`, outside this repo; no `.code-forge/ledger.jsonl` was created
inside the forge repo at any point (verified after every experiment).

Evidence directory: `.planning/reports/ledger-diagnosis-evidence/`
(8 evidence files + the real scripts/transcripts/artifacts they cite).

## 1. Verdict

- **H1 MODE -- TRUE, and the dominant cause.** The way forge is
  actually invoked in this project (the MCP `forge_review` sampling
  path, and any non-interactive CLI subprocess) resolves to `Mode.CI`,
  and `Mode.CI`'s execution path never calls the ledger writer, at all,
  under any branch. Evidence: evidence-01 (source grounding),
  evidence-02 (real StateMachine run, LOCAL writes / CI does not),
  evidence-03 (real `resolve_mode()` call), evidence-04 (real
  `code-forge` CLI subprocess, `state.json` literally reads
  `mode: CI`), evidence-06 (this repo's own last real self-review
  recorded `mode: CI`).

- **H2 WORKTREE -- TRUE, a real and confirmed compounding mechanism,
  not the dominant one on its own.** `.code-forge/` is gitignored and
  untracked; a fresh linked worktree does not inherit it, and a row
  written inside a worktree is destroyed the moment the worktree is
  removed -- exactly this project's mandated workflow. Evidence:
  evidence-01 (40+ existing `.code-forge/` dirs across this machine's
  projects, none with a ledger.jsonl), evidence-05 (real
  `git worktree add` / `git worktree remove` demonstration). This bites
  only the rare runs that do reach LOCAL mode; it cannot by itself
  explain an empty ledger in CI-mode runs, because there was never a
  row to lose there.

- **H3 EARLY RETURN -- PARTIALLY TRUE, narrow, and moot for the
  observed failure.** None SHAs are real and reproducible (a missing
  snapshot-baseline file, or a genuinely non-git directory), but the
  DEFAULT `code-forge review` invocation and `--whole-file` mode, in a
  git repo, never hit it -- both always populate real SHAs. Evidence:
  evidence-08 (real `resolve_baseline()` calls across all four
  CLI-reachable configurations). Moot because CI mode never reaches the
  SHA check in the first place (H1), and the mainstream git-mode
  invocation doesn't trigger it even when it does.

- **H4 DISPOSITION -- TRUE as a structural fact, moot for the observed
  failure.** In CI mode, `Disposition.FIXED` is unreachable: the only
  code path that sets it, the autofix loop, is gated
  `if self.mode == Mode.LOCAL` (`machine.py:832`). Measured directly in
  evidence-02: an identical CONFIRMED finding became FIXED under LOCAL
  and stayed CONFIRMED under CI. Moot because it doesn't matter which
  dispositions CI mode can or cannot reach -- `_write_ledger_rows` is
  never called from the CI path regardless (H1).

- **H5 (combination) -- TRUE.** The empty ledger is the product of H1
  (structural, unconditional for the way forge is actually invoked) and
  H2 (a confirmed, independent, compounding mechanism for the rare
  LOCAL-mode case), with H3 as a real but narrow secondary defect
  surface unrelated to the mainstream path. No single hypothesis alone
  is a complete account; H1 is necessary and, on its own, already
  sufficient to explain the observed zero rows.

Nothing here could not be separated -- each hypothesis produced a clean,
distinguishing experiment. No hypothesis is asserted from code-reading
alone; each has a real command transcript.

## 2. Experiments, one per verdict line

**H1 (mode):**
- `evidence-01-ground-truth-reverify.md` -- source grounding: G1-G6
  re-verified, plus `mcp_server.py:900-901` (`mode=Mode.CI` hardcoded)
  and `mode_resolver.py:45-50` (TTY-based default), plus the full read
  of `_run_ci` (`machine.py:289-535`) showing it never calls the ledger
  writer.
- `evidence-02-ci-vs-local-experiment.md` (+ `exp1_run_ci_vs_local.py`,
  `exp1_transcript_raw.txt`) -- real `StateMachine` run, identical
  finding: LOCAL writes 2 rows and creates `ledger.jsonl`; CI writes 0
  rows, no file.
- `evidence-03-mode-resolver-tty-default.md` (+
  `exp2_check_mode_resolver.py`, `exp2_transcript_raw.txt`) -- real
  `resolve_mode()` calls: bare/default + non-TTY stdout -> `Mode.CI`.
- `evidence-04-real-cli-subprocess.md` (+ `exp3_run2_state.json`,
  `exp3_run2_stderr.log`) -- the actual installed `code-forge` CLI, run
  as a real subprocess with piped stdout and no `--mode` flag:
  `state.json` records `mode: CI`; no ledger file created.
- `evidence-06-repo-live-state.md` -- this repo's own real
  `.code-forge/state.json` (last real self-review, 2026-07-27): `mode:
  CI`.

**H2 (worktree):**
- `evidence-01-ground-truth-reverify.md`, item 3 -- filesystem survey:
  40+ `.code-forge/` dirs across this machine's projects and worktrees,
  none with a ledger.jsonl.
- `evidence-05-worktree-loss-mechanism.md` -- real `git worktree add` /
  `git worktree remove`: a row written inside a worktree is
  unrecoverably gone after removal; a fresh worktree never inherits the
  main tree's `.code-forge/` in the first place.

**H3 (None SHAs):**
- `evidence-08-h3-none-sha-conditions.md` (+
  `exp5_check_h3_none_shas.py`, `exp5_check_h3_none_shas_v2.py`,
  `exp5_transcript_raw.txt`) -- real `resolve_baseline()` calls across
  default-review, `--whole-file`, missing-snapshot, and true-non-git
  configurations. Includes a caught-and-corrected bug in my own first
  script (a nested tempdir made a "non-git" case register as git) --
  left in the evidence file rather than silently fixed, per "re-verify
  rather than reconcile away."

**H4 (disposition):**
- `evidence-02-ci-vs-local-experiment.md` -- same run as H1: CI mode
  leaves the finding CONFIRMED (never FIXED) because the autofix loop
  is LOCAL-only (`machine.py:832`).

**H5 (combination):** synthesis of the above; no separate experiment.

## 3. Defect or working as designed

**This is a defect**, specifically an integration/verification gap
between two features that are each internally consistent on their own,
plus a process-compliance gap in how Phase 43 closed itself out.

- `_finalize_local_terminal`'s own docstring names its scope
  explicitly: "terminal state writer for LOCAL fixpoint exit"
  (`machine.py:1069-1076`). The ledger writer was never meant to be
  reached from every path -- only from LOCAL's fixpoint-exit. That much
  is arguably "designed," in isolation.
- But Phase 43's own dispatch order named the exact failure being
  diagnosed here as its #1 named risk before writing any code:
  "components built, ledger never fed" -- hence its own "dogfood hard
  gate" requirement (`.planning/dispatch/draft_20260704_phase43_ledger_dispatch.txt:12-17`,
  quoted in full in `evidence-07-phase43-dispatch-and-completion.md`).
  The wiring instruction (T2) named only the LOCAL exit point and never
  asked which mode the project's actual invocations resolve to -- so
  the one cross-check that would have caught this (does our own usage
  ever reach the function I'm hooking?) was never in the dispatch's
  scope to begin with.
- The phase's REAL-PATH acceptance test
  (`tests/test_realpath_ledger.py`, confirmed passing in evidence-02)
  is real in every sense that matters for the writer mechanism -- real
  git repo, real SHAs, real production code -- but it manually
  constructs `mode=Mode.LOCAL`, so passing it never exercised the mode
  the project's real invocations actually use. A green real-path test
  provided assurance for a path that isn't the one that runs in
  practice.
- The phase's "dogfood hard gate" required a real manual ruling (via
  `code-forge ledger mark`) to exist before the phase could be declared
  done. G1 (re-verified in evidence-01: zero ledger rows anywhere on
  this machine) means that row does not exist today. Phase 43 is
  nonetheless recorded as complete and merged
  (`.planning/STATE.md:248`, `14328bb`; see evidence-07). Either the
  gate step was not actually verified before closure, or it was
  produced once and then lost -- plausibly to the worktree mechanism
  (H2), since the dispatch's own Phase-0 instruction was to do the work
  in `.worktrees/ledger`. This diagnosis cannot tell which from
  artifacts alone (see section 5).

**Which layer owns it:** the seam between the Mode-selection layer
(`mode_resolver.py`, and the MCP sampling path's hardcoded `Mode.CI` in
`mcp_server.py`) and the Ledger-writer layer wired into machine.py's
LOCAL-only fixpoint-exit function, plus the Phase 43 acceptance
process that closed the phase without a verification step that would
have caught the mismatch. No fix is proposed here, per the scope fence.

## 4. What Phase 44 inherits

**Phase 44 (EVAL-ON-DUTY) has no real input source today.** It is
recorded in `ROADMAP.md` as "PLANNED, not started" and is described
there, in the project's own words, as re-extracting diffs from exactly
this ledger. Zero rows exist anywhere on this machine (G1, re-verified
in evidence-01) and this diagnosis has shown why: the sole automatic
writer is wired into a code path the project's actual invocation
surface does not reach. Phases 51, 52, and 53a are staged behind 44 in
the same roadmap arc and inherit the same gap transitively -- none of
them have real ledger data to consume as of 2026-07-30. This is a
statement of current input availability, not a proposal for how to fix
it.

## 5. What this diagnosis did NOT determine

- **Whether the Phase 43 "dogfood hard gate" ruling was ever actually
  performed.** The dispatch required it before the phase could be
  called done; the phase is recorded as done; no row exists today. I
  cannot tell, from artifacts alone, whether the step was skipped and
  the closure was unverified, or whether it was performed once and the
  row was later destroyed (most plausibly by the worktree mechanism,
  H2, if the ruling was made inside `.worktrees/ledger` before that
  worktree was removed). Distinguishing these would need either a
  human's memory of that specific session or forensic reconstruction
  from old shell history / reflog, neither of which was in scope here.
- **A full per-project scan of every `.code-forge/` directory found on
  this machine.** The filesystem survey (evidence-01) found 40+ such
  directories across many projects and confirmed none currently
  contains a ledger.jsonl, but I did not open each one's `state.json`
  to check its recorded mode -- so I cannot say, project by project,
  how many of those represent "a LOCAL run happened and the row was
  lost to worktree removal" (H2 firing) versus "no LOCAL run ever
  happened there at all" (H1 alone, nothing to lose). The forge repo's
  own history (evidence-06) is the one case checked in this depth.
- **Whether any human has ever run `code-forge review` interactively
  at a real TTY against this repo since Phase 43 shipped.** State.json
  and the receipts directory show the LAST run was CI-mode
  (2026-07-27) and an older LOCAL-mode multi-round sequence exists from
  2026-06-01 (before the ledger existed) -- but there is no artifact
  that would positively confirm or rule out an interactive LOCAL run
  in between that happened not to produce a terminal FIXED/DISMISSED
  finding, or that happened inside a since-deleted worktree.
- **Real-LLM-backend behavior.** All experiments used stub
  falsifiers/autofixers or a deliberately-unreachable mock backend, per
  the dispatch's own instruction not to spend real tokens proving a
  control-flow question. Nothing about actual model-driven disposition
  assignment (as opposed to the mechanical CONFIRMED/FIXED/DISMISSED
  plumbing) was exercised end-to-end with a live model.
- **Whether `~/.config/code-forge/config.yaml`'s user-level backend
  defaults (`gemini-omniroute`, `deepseek`, surfaced unexpectedly during
  evidence-04's first attempt) route to a mode selection different from
  what this diagnosis measured.** They use the same `outlet:
  subprocess` / CLI-subprocess path forge's own gate.yaml uses, so I
  have no reason to expect a different mode outcome, but I did not
  execute a real round-trip through that specific configured backend
  to confirm.
