# Phase 25 -- R3 Review (main-session, independent)

**Reviewer:** main session (not the plan author)
**Date:** 2026-06-18
**Verdict:** NOT READY FOR EXECUTE. Re-plan required (F1 blocker + F2/F3).

Ground truth checked at file:line against the live tree, not prose. The
skeleton stands; the items below are corrections, not a teardown.

---

## What is solid (verified, do not redo)

The plans' load-bearing "extracted from codebase" claims are accurate:

- StateMachine has NO `output_fn` param -> the D-12 receipt-glob redesign is
  correctly motivated. (grep: no output_fn in machine.py)
- StateMachine takes `cwd: Path` as a constructor field; no `os.chdir` ->
  concurrent threads are cwd-safe. (machine.py:164)
- R-04 `mutation-result.json` collision is REAL when two StateMachines share
  one cwd. (machine.py:240 write + machine.py:386 daemon thread)
- Verdict has exactly the 5 members used; ResolvedReview has exactly 4 fields;
  StubAutoFixer lives in autofix.py; receipts are `receipt-cNpM.json` with
  finding keys file/line/description; verdict_to_exit / _run -> Verdict /
  _run_hold_loop:1551 all confirmed. (state.py:31, baseline.py:53, autofix.py:55,
  receipt.py:5/94, exit_codes.py:21, cli.py:1014/1106/1551)

Plans 01 (schema + validate_siblings) and 02's pure functions
(get_sibling_diff, build_cross_repo_context) are unaffected by the blockers
below and can stand as-is once the cwd/source_files contract is fixed.

---

## F1 [BLOCKER] -- cross-repo review silently degrades to L1-only

**Rejected. Cross-repo must preserve per-repo L0 grounding.**

### Evidence

Plan 03 runs EVERY repo (primary included) in a bare tmp dir from
`make_per_repo_cwd()` and constructs `ResolvedReview(source_files=[], ...)`
(Plan 03 interfaces, lines 176-183, comment "joint diff carries all context").

Trace what that does inside the StateMachine:

- `_source_files()` returns `resolved_review.source_files` (machine.py:1315).
- `_run_l0_phase` calls `l0_runner(registry, _source_files())` (machine.py:543).
  source_files=[] -> L0 parsers get zero files -> **zero L0 findings**.
- L2 (mutation/R2) reads `cwd/.code-forge/gate.yaml`; the bare tmp dir has none,
  so it degrades to "L2: gate.yaml missing" and returns no findings
  (machine.py:609-619). Coverage (R3) also keys off `_source_files()`
  (machine.py:948).

Net result: cross-repo review = one L1 LLM pass over the concatenated diff.
L0 deterministic parsers, R2 mutation, R3 coverage are all silently off --
for the PRIMARY repo too. That is single-pass / single-perspective review,
the exact failure mode CLAUDE.md uses to distinguish forge from competitors,
and the brief's "one joint verdict + per-repo receipts" hides it.

### The real constraint (why the bare tmp cwd appeared)

`--state-dir` was deprecated in v2.1; state is welded to `cwd/.code-forge`
(cli.py:1254-1262). The only lever to keep forge state out of a reviewed repo
is to change `cwd`. So the sub-session reached for tmp cwds to avoid polluting
the real repos' `.code-forge/state.json` -- a legitimate goal -- but paid for it
by disabling L0/L2.

Also note: R-04's collision only occurs when N StateMachines share ONE cwd.
Cross-repo has N DISTINCT repo paths, which already cannot collide. So the tmp
cwd is not required by R-04; it is required only by the "don't pollute real
state" goal.

### Required outcome (not a locked design)

Cross-repo review must run L0 parsers on each repo's REAL changed files. L2
mutation per repo is desirable but secondary -- decide explicitly whether v1
includes it. L1 must still see the joint context for cross-repo reasoning.
"L1-only" is not an acceptable v1.

### Candidate designs (sub-session picks one in re-plan, with rationale)

- **A. Real repo path as cwd.** L0/L2 work; distinct paths so no collision.
  Cost: writes state.json/receipts into the real repos' `.code-forge/`,
  clobbering the primary's own single-repo forge state. Likely unacceptable
  for the primary; maybe tolerable for read-only siblings. State it if chosen.
- **B. Tmp cwd, but source_files = ABSOLUTE paths into the real repo, and copy
  the repo's real gate.yaml into `tmp_cwd/.code-forge/gate.yaml`.** Keeps state
  in the throwaway dir (no pollution) while L0/L2 operate on real files.
  Viable ONLY if the L0 toolchain reads absolute paths correctly -- VERIFY this
  (run_tools cwd behavior + whether linters accept abs paths + fingerprint path
  stability). This is the design that satisfies both goals if it holds.
- **C. Per-repo single-repo review + a separate joint L1 pass**, verdicts merged.
  Cleaner isolation; changes the meaning of "one joint unit" (L1 cross-repo
  reasoning becomes one extra pass, not N). Heavier.

### F1 Q1/Q2 RESOLVED (main session, 2026-06-18) -- design B is viable

**Q1 (do L0 linters read absolute paths regardless of cwd?) -- YES.**
`run_tool` calls `subprocess.run(cmd, ...)` with NO `cwd=` (runner.py:134); the
linter inherits the forge PROCESS cwd and the file paths are appended verbatim
(`cmd = cmd + files`, runner.py:131). So ABSOLUTE file paths are read correctly
from any StateMachine cwd; RELATIVE paths resolve against the process cwd (one
place, wrong for N repos). Caveat: `working_dir == "cargo_root"` skips files
entirely (Rust/clippy only, irrelevant to v1 same-stack python/shell).

**Q2 (how is source_files derived; absolute or relative?) -- review-target list,
passed THROUGH unchanged, typically repo-RELATIVE.**
`source_files = paths` in every resolve_baseline branch (baseline.py) -- it is
NOT derived from the diff. `paths = _paths(args, cwd)` (cli.py:1976-1982):
`args.paths` if the user gave them, else `get_changed_files(...)` derived from
the git range -- both wrapped as `Path(p)` WITHOUT `.resolve()`, hence relative.
Single-repo works ONLY because the forge process cwd == the repo root, so
relative source_files resolve correctly. That invariant breaks the moment N
repos share one process.

**Concrete F1 fix (now code-grounded):**
- Design B is viable. Each thread: tmp cwd for state isolation (no pollution),
  PLUS source_files = that repo's changed files resolved to ABSOLUTE paths.
- Reuse `get_changed_files(...)` per repo (already exists, cli.py:1982) to derive
  the changed-file list, then make each absolute against that repo's path.
- Seed the repo's real gate.yaml into `tmp_cwd/.code-forge/gate.yaml` to restore
  L2 (this also consumes the F3 `gate_config` param).
- `source_files` MUST NOT be `[]`. That one line is what disabled L0.
- Minor: L0 fingerprints/receipts will then carry absolute paths (stable within a
  run; relativize for display in format_cross_repo_output if desired -- not a
  blocker).

### Still-open design questions for re-plan (sub-session decides, with rationale)

**Q3.** Does L1 review the joint diff ONCE (cross-repo reasoning) or per-repo?
The current "every thread gets git_diff=joint_diff" reviews the same joint diff
N times -- N x L1 cost and N duplicate findings. Resolve before Plan 03.
**Q4.** Is L2/mutation on a sibling meaningful given D-16 (siblings share the
primary's gate config / test command)? If not, scope L2 to primary only and say
so explicitly.

---

## F2 [HIGH] -- cross-repo path does not handle PENDING/HOLD

Single-repo runs go through `_run_hold_loop` (cli.py:1551) -- a HOLD-resume loop.
Cross-repo goes through `run_cross_repo`, which has NO such loop. The primary
thread runs the full `_run_local` (coverage/e2e can yield UNCERTAIN -> PENDING).
If the primary returns `PENDING`, `run_cross_repo` returns it as the joint
verdict; main() then calls `verdict_to_exit(PENDING)`, which RAISES
(exit_codes.py:37 "verdict_to_exit called with PENDING; HOLD-resume loop ...").

Edge case, but reachable. Plan 03/04 must handle PENDING for the primary
(route through the hold loop, or define and document an explicit cross-repo
HOLD behavior). Do not let PENDING reach verdict_to_exit.

---

## F3 [MEDIUM] -- `gate_config` parameter is dead

`run_cross_repo(..., gate_config, ...)` (Plan 03 signature) is never consumed:
the StateMachine reads gate.yaml from `self.cwd/.code-forge/gate.yaml`
(machine.py:612), not from this param. Either drop it, or -- better -- USE it by
writing it into each per_repo_cwd's `.code-forge/gate.yaml` (design B above),
which also restores L2 and resolves part of F1. Pick one; do not ship an unused
load-bearing-looking parameter.

---

## F4 [LOW] -- doc drift (plans are correct; docs trail them)

- 25-CONTEXT D-05 (lines 54-58) still lists shallow-clone remote as in-scope;
  the plans correctly narrowed to local-only v1 (validate_siblings rejects
  https/git@). Update D-05 to match.
- 25-CONTEXT D-19 says `{label}-receipt-rN.json`; actual format is
  `receipt-cNpM.json` (receipt.py:5). Brief already accepted this; align the doc.
- Plan 03 line 128 writes `class Verdict(enum.Enum)`; actual is
  `class Verdict(str, Enum)` (state.py:29). Members correct; cosmetic.

---

## Additional task (user-directed, 2026-06-18) -- prune deprecated CLI args

Separate from cross-repo; bundled here at user request. The sub-session owns the
pruning decision. Inventory of the `review` subcommand args (cli.py:203-301);
two are explicitly DEPRECATED and already replaced:

- `--state-dir` (cli.py:232, help "DEPRECATED: state directory is hardcoded to
  .code-forge"). Accepted-but-warned at cli.py:1254-1262. No replacement needed
  (state is always `cwd/.code-forge`).
- `--staged` (cli.py:253, help "DEPRECATED v2.1: use --head INDEX"). Warned at
  cli.py:1928 and 1953. Replacement: `--head INDEX`.

Task: remove both flags AND their warning-handling code, OR consciously keep
them for back-compat with a stated reason. Scan every subparser
(gate/mutation/e2e/hooks/skill/verify/detect/init/smoke-run/trust/eval) for any
other deprecated flags before declaring the sweep done. This is a SEPARABLE
chore -- it does not block cross-repo and cross-repo does not block it. The
sub-session may run it as its own small phase, or fold a flag removal into
Plan 04 (which already touches the baseline/head arg surface via `--committed`
and the pseudo-ref guard in F2). Do not let it expand Phase 25's cross-repo
scope.

---

## Re-plan scope and ordering

1. F1 Q1/Q2 are ANSWERED above (design B viable: per-repo absolute source_files
   + tmp cwd + seeded gate.yaml). Re-research now only needs Q3 (L1 joint-diff
   once vs per-repo) and Q4 (L2-on-siblings) -- design choices, not unknowns.
   Still a real architecture decision for Plan 02/03, not a one-line patch.
2. Plan 02: `make_per_repo_cwd` and the source_files derivation change per the
   chosen design (likely: per-repo absolute changed-files + gate.yaml seeding).
3. Plan 03: cwd strategy, restore L0 (and decide L2), add PENDING handling,
   resolve gate_config (F2/F3).
4. Plan 04: ensure the cross-repo return path cannot hand PENDING to
   verdict_to_exit.
5. Plans 05/06: ripple -- integration tests must assert L0 actually runs on each
   repo (add a test that a planted L0-detectable defect in a sibling appears in
   the joint output; an L1-only pipeline would miss it). Update VALIDATION map.
6. Plans 01 and 02's pure functions are unaffected -- do not rewrite them.

Do NOT execute Phase 25 until F1 is resolved by an explicit design decision and
F2/F3 are folded into the plans.

*-- main session, R3*
