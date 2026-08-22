# CP1b External Adversarial Review -- Phase 44 EVAL-ON-DUTY (kimi)

**Reviewer:** kimi-k3 (external, fresh context; no credit given to CP1 PASS B=0)
**Base verified against:** main @ 8bd01bc (re-grepped every cited anchor)
**Scope:** 44-CONTEXT.md (D-01..D-27), 44-RESEARCH.md (R-1..R-8),
44-01-PLAN.md, 44-02-PLAN.md, 44-03-PLAN.md, vs real source
(ledger.py, machine.py, cli.py, reviewer_json.py, gate_check.py,
eval/{corpus,runner,scorer}.py, claim.py, advisory.py).

Notation: [B]=blocker [H]=high [M]=medium [L]=low. Hypotheses without
code evidence are labeled as such.

---

## Axis 1 -- Design correctness vs real code (anchors re-grepped)

Verified TRUE (no credit, just confirming what survived re-grep):
- `machine.py:541` is the real CI terminal (`_persist_state(); return
  verdict`); insertion point after `_persist_state` is real.
- `reviewer_json.py:170-171` computes fingerprints at parse time
  (`sha256("file:line:desc")[:16]`) -- D-12's "no recompute" premise holds.
- `machine.py:1318` has the `base is None or head is None: return 0`
  guard; `cli.py:1642-1654` really drops file/line/axis_claim on re-ruling
  an existing fingerprint (metadata-free rows; also drops base/head to the
  HEAD/HEAD default and drops `version_sensitive` entirely -- the plan
  mentions the first two but not version_sensitive).
- `corpus.py:111-116` raises ValueError on missing/invalid
  expected_verdict; unknown keys are ignored; axis_tags is free-text
  list[str] (the 44-02 interfaces misstatement "enum" is corrected in the
  plan itself).
- `runner.py:537-556` merges an existing gate.yaml with existing
  non-backend keys winning -- the D-17 hostile-test.command hazard is real.
- `advisory.py:26-36` AdvisoryFinding has NO fingerprint field, no
  disposition -- confirmed load-bearing for finding B-1 below.
- `load_gate_config` (gate_check.py:39-108) REQUIRES a `test:` section
  with `command` list and raises ValueError otherwise. Confirmed
  load-bearing for finding B-2 below.
- `_run_ci` has FOUR return statements: :360 (mutation survivors FAIL,
  before the normal terminal), :381/:391 (PENDING), :542 (normal
  terminal). Confirmed load-bearing for B-3 below.
- `iter_rows` (ledger.py:77+) really is generator-with-early-return; a
  mis-shaped call returns None silently instead of raising.

### Findings

- **[B-1] D-27 unblocks style findings without recording them in the
  ledger, silently defeating the D-23/D-25 convergence mechanism and
  losing the eval-corpus data they were supposed to become.**
  AdvisoryFinding is *structurally* ledger-incompatible (advisory.py:33-37:
  "Fields intentionally excluded (structural incompatibility): fingerprint,
  disposition, source"). The 44-01 write path writes rows only from
  `self._state.findings` (CONFIRMED StateFindings; 44-01 Task 2 behavior
  (a)/(b)). Once D-27 reroutes a style finding from
  `self._state.findings` to `self._advisories` (44-03 Task 2 action: "is
  routed to self._advisories ... instead of self._state.findings"), that
  finding (a) is never written to the ledger by CI, so it never acquires a
  terminal-state row, so D-23 suppression can never apply to it -- but it
  never needs suppression because it never blocks again; (b) is invisible
  to `ledger adjudicate` (nothing to upgrade) and to 44-02's extractor
  (D-02's false-positive-corpus pipeline). Net effect: a whole class of
  findings leaves the ledger/extractor data model entirely, with no
  decision in D-01..D-27 acknowledging the tradeoff, and 44-03's artifacts
  table says "advisory.py ... (if classifier lives here)" -- the design
  has not decided where the classifier lives or how advisory findings
  retain identity (AdvisoryFinding has no fingerprint, so even *counting*
  repeats of the same style finding across runs is impossible). Either
  keep style findings as StateFindings with a non-blocking disposition, or
  give AdvisoryFinding a fingerprint + a write path; "route to advisories
  and forget" contradicts the phase goal ("corpus grows from real reviewed
  work"). Evidence: advisory.py:26-45, 44-03-PLAN.md Task 2
  action/behavior (c), 44-01-PLAN.md Task 2 behavior (a).

- **[B-2] The D-19 kill-switch and D-26 pinned_paths reads are specified
  against `load_gate_config`, which refuses to load the common review-mode
  gate.yaml (no `test:` section) -- the plan's own test (g) bakes in the
  wrong contract.**
  gate_check.py:64-71 raises ValueError unless gate.yaml carries
  `test.command`. The review pipeline's own loader comment at
  outlet_resolver.py:132 says it "Does NOT call load_gate_config (avoids
  the 'test section' [requirement])", and machine.py:461-469 comments that
  "A worktree carries its own gitignored gate.yaml, so an incomplete one
  is the common case rather than a rare misconfiguration." 44-01 Task 2
  test (g) then asserts "a ledger-less-but-valid gate.yaml (ValueError
  from load_gate_config) must NOT change the verdict" -- i.e. the plan
  ACCEPTS that in the common case (review-mode gate.yaml without a test
  section) the kill-switch read always raises and the config layer of the
  two-layer kill-switch is DEAD: `ledger.enabled=false` is never honored
  because the config is never successfully parsed. Same for D-26
  pinned_paths (44-03: "Read via the same gate config load as the D-19
  kill-switch"). The repo-level kill-switch and pinned_paths only work in
  repos whose gate.yaml carries a test command -- precisely not the repos
  where forge runs in review-only mode. D-19/D-26 need a tolerant raw-YAML
  read (yaml.safe_load + dict.get, no test-section requirement), not
  load_gate_config. Evidence: gate_check.py:64-83, outlet_resolver.py:132,
  44-01-PLAN.md Task 2 test (g) + action ("via load_gate_config"),
  44-CONTEXT.md D-19 ("verified: no ledger gate in gate_check.py:39-100"
  -- true but the wrong loader), 44-03-PLAN.md interfaces gate_check.py.

- **[B-3] The CI write does not fire on the mutation-survivor terminal
  (machine.py:353-360): a whole class of CI FAILs (arguably the most
  valuable "tests are weak" signal) never reaches the ledger.**
  `_run_ci` returns Verdict.FAIL at :360 -- after `_persist_state()` but
  BEFORE the :526-542 terminal block where 44-01 inserts
  `_write_ci_ledger_rows` (plan key_links: "called after _persist_state,
  before return verdict" -- only the :541-542 return). A mutation-survivor
  FAIL run therefore writes zero rows; its CONFIRMED MUTANT findings are
  never recorded, never adjudicable, never exportable, and (post-44-03)
  never suppressible. D-01 says "CI review terminal states (PASS/FAIL/
  ESCALATED) append UNADJUDICATED rows" -- the FAIL at :360 is a CI
  terminal state the plan does not cover. (PENDING returns at :381/:391
  are non-terminal, correctly excluded.) Fix: funnel all terminal exits
  through a single ledger-write call, or add the call before the :360
  return as well. Evidence: machine.py:353-360 vs 44-01-PLAN.md
  key_links/behavior (a); state.py:56-63 Verdict enum.

- **[H-1] `resolve_ledger_root(cwd)` resolves the ledger location from the
  INVOCATION cwd, but the CI writer's cwd can be a subdirectory of the
  worktree while the CLI readers resolve from their own cwd -- the
  "subcommands read the SAME ledger" contract holds only for worktree-root
  invocations.**
  `--git-common-dir` returns the main repo's .git from anywhere inside any
  worktree, so writes always land in the main repo -- that part is sound.
  But the plan's Test (g) B-3 proofs (44-01 Task 3, 44-02 Task 3, both
  phrased "from a LINKED WORKTREE") only exercise worktree-ROOT cwd. A
  `code-forge ledger adjudicate` run from a deep subdirectory still works
  (same common-dir), so this is not a correctness break for git repos --
  the real gap is the D-11 fallback branch: from a NON-git cwd, "fall back
  to cwd-local" (44-01 Task 1) means the ledger location is
  invocation-cwd-dependent, and the same command run from two different
  non-git directories reads/writes two different ledgers with no warning.
  D-11/D-20b do not state whether the fallback should walk up to a
  `.code-forge/` ancestor or emit a warning that the fallback fired.
  Downgrade-able to [M] if a documented "non-git = cwd-local, caveat
  operator" note is accepted; flagged [H] because R-4 calls the resolver
  "THE ledger location contract" and the contract is silent on its weakest
  branch. Evidence: 44-RESEARCH.md R-4, 44-01-PLAN.md Task 1
  resolver spec, D-11/D-20b in 44-CONTEXT.md.

- **[H-2] D-02/D-13 map ESCAPED rows to expect-catch-at-row.file:line
  entries, but ESCAPED enters via `ledger mark --new` where base/head
  default to HEAD==HEAD -- the extractor silently skips or garbage-exports
  exactly the rows D-02 was written for.**
  cli.py:1616-1629: `mark` without --base-sha/--head-sha sets
  `base_sha = head_sha = _git_head(cwd)`. `git diff base..head` for equal
  SHAs is empty; more subtly, the SHAs are post-escape-discovery HEAD, not
  the diff where the bug escaped. 44-02's extractor validates resolvability
  (cat-file -e passes -- HEAD exists) and materializes an empty diff,
  emitting a manifest entry whose replay trivially cannot catch anything.
  D-03 only skips UNRESOLVABLE SHAs; resolvable-but-meaningless (base==head)
  is not handled anywhere. D-02 says ESCAPED -> missed-bug entry; in
  practice mark-created ESCAPED rows carry no usable diff. Needs an
  explicit rule: skip entries whose base_sha==head_sha (or require --base/
  --head-sha for ESCAPED at mark time), and the skip must get its own
  precedence slot in D-15's counters. Evidence: cli.py:1616-1629,
  44-CONTEXT.md D-02/D-03, 44-02-PLAN.md Task 1 behavior (a)/(c).

- **[H-3] 44-03's fingerprint-suppression silently scopes across repos and
  diffs: `known_terminal_fingerprints` keys on fingerprint alone while the
  write side (D-08) deliberately keys dedup on (fingerprint, base, head).**
  The write path treats the same fingerprint on a different diff as a
  DIFFERENT row worth keeping (44-01 Task 2 behavior (b): diff-scoped
  clean-row fingerprint "so successive clean runs on DIFFERENT diffs do
  not collapse or cross-collide"). The read side then suppresses a
  finding on diff B because it was FIXED on diff A -- with no
  base/head or repo_root match in `known_terminal_fingerprints(root)`
  (44-03 Task 1 action: "the fingerprints whose LATEST row ... is FIXED,
  DISPROVED, or DUPLICATE"; no SHA filter). Fingerprint =
  sha256(file:line:desc) (reviewer_json.py:170-171): the same file:line
  with the same reviewer wording on a fresh diff is suppressed even though
  the code between the lines may have changed completely. This is a
  false-green vector that D-25's "wording drift -> new fingerprint ->
  blocks" defense does NOT cover (drift in the *code*, not the wording).
  At minimum the suppression set should be filtered by the current run's
  repo_root; whether to also scope by base/head is a design question the
  plans never pose. Evidence: 44-03-PLAN.md Task 1 action, 44-01-PLAN.md
  Task 2 behavior (b), reviewer_json.py:170-171, D-08/D-23.

- **[M-1] The per-fingerprint evidence cap is mis-specified: `mark
  --evidence` is a REQUIRED argument (cli.py:1552, "--evidence required
  with --new"), yet the write-side cap is framed around `f.error` stack
  traces; neither the plan nor D-07 caps `description`/`axis_claim`/
  `file` length, so the 2048-byte row test's "maximal evidence + all long
  fields" fixture has no defined bound for the non-evidence fields.**
  A 400-char file path plus 500-char evidence plus long axis_claim can
  still approach the bound; the test constructs "maximal" fields without
  the plan defining what maximal means for file/axis_claim. Hypothesis
  that this overflows 2048: unproven (fingerprint 16 + SHAs 80 + ts 20 +
  small fields ~ 700 fixed bytes; evidence 515; file/axis_claim would each
  need ~400 chars to threaten it -- possible with deep paths). The
  acceptance criterion "Every serialized ledger row is under 2048 bytes"
  is unverifiable without a defined bound on every free-text field, not
  just evidence. Evidence: 44-CONTEXT.md D-07/D-21, 44-01-PLAN.md Task 1
  behavior, ledger.py:40-55, cli.py:1548-1556.

- **[M-2] `resolve_ledger_root` spec says "on any failure ... return cwd
  unchanged" but D-20b wants the row's `repo_root` field to record the
  MAIN repo root "so 44-02's cat-file resolves post-merge" -- in the
  non-git fallback case the field records a transient cwd with no git
  history, making every such row permanently stale at export time, and
  nothing counts or surfaces this class.**
  D-09's --repo-root override can remap, but the export summary (D-15)
  lumps these into "stale-SHA skipped" which mis-describes them (the SHAs
  may be fine elsewhere; the repo_root is what died). Minor accounting
  dishonesty in the mutually exclusive counters. Evidence: 44-01-PLAN.md
  Task 2 behavior (h), 44-CONTEXT.md D-11/D-20b, 44-02-PLAN.md Task 1
  behavior (c).

- **[M-3] The D-17 emission-side strip drops the gate.yaml section by
  text surgery on `diff --git a/.code-forge/gate.yaml` headers -- the
  spec names exactly one path spelling; a foreign diff touching
  `.code-forge/gate.yml`, a renamed gate config, or a git diff with
  quoted/escaped paths slips through unstripped and the hostile
  test.command executes at replay.**
  runner.py:533-535 reads only `.code-forge/gate.yaml`, so the .yml
  variant is harmless at replay TODAY, but the strip rule as specified is
  a string match on one literal path with no rename/copy-header handling
  (`diff --git` with `rename from/to` or `copy to .code-forge/gate.yaml`
  lands the file without a `a/.code-forge/gate.yaml` source header). The
  plan's acceptance (fixture with a hostile test.command in a plain add)
  passes while the rename/copy case is untested. Label: hypothesis with
  mechanism identified; the runner-side defense (strip AFTER apply, i.e.
  delete gate.yaml from the temp repo before _create_gate_yaml) is
  simpler and would make the emission-side text surgery unnecessary, but
  replay lives outside 44-02's files_modified -- the plan chose the weaker
  in-scope option and should say so explicitly with the rename-hole
  documented. Evidence: 44-02-PLAN.md Task 2 action D-17 block,
  runner.py:533-541, 44-02-PLAN.md Task 2 test (d).

- **[M-4] LOCAL and CI writers still diverge in disposition coverage:
  LOCAL's `_write_ledger_rows` only rows FIXED/DISMISSED
  (machine.py:1329-1334) and the plan's "extract the shared
  row-construction into a common private method" (44-01 Task 2 action)
  shares row-BUILDING but not row-SELECTION -- the acceptance grep "no
  duplicated LedgerRow( construction" does not verify the two writers
  agree on WHICH findings become rows, so the golden-rule claim ("no two
  drifting writers") is only half-enforced.**
  CI writes UNADJUDICATED for CONFIRMED; LOCAL writes terminal FIXED/
  DISPROVED; nothing stops a future edit changing one selection rule and
  not the other, and no test asserts parity of the selection contract.
  Evidence: machine.py:1328-1336, 44-01-PLAN.md Task 2 action +
  acceptance criteria.

- **[M-5] `ledger adjudicate` error path "fingerprint whose latest row is
  already terminal -> EXIT_CLI_ERROR" (44-01 Task 3 test (c)) contradicts
  the pre-existing `mark` semantics that allow RE-ruling an existing
  (terminal) fingerprint (cli.py:1500-1508 only requires the fingerprint
  exist) -- two subcommands now encode two different adjudication models,
  and the plan never says whether `mark` keeps its re-ruling ability or
  is superseded.**
  If both live, an operator can mark-over-terminal (dropping metadata,
  the B-2 gap D-10 was created to close) while adjudicate refuses the
  same operation: the metadata-losing path stays open. Evidence:
  cli.py:1499-1508 + :1642-1654, 44-01-PLAN.md Task 3 behavior (c),
  44-CONTEXT.md D-10.

- **[M-6] 44-02 Task 3 "track the managed set in the manifest or a
  sidecar" (D-22 re-export hygiene) is a coin flip the plan doesn't
  settle; a manifest-only managed set breaks when the user hand-edits the
  manifest between runs (managed-set reads the NEW manifest and
  "foreign-preserves" files the OLD manifest managed), while a sidecar
  adds an untracked-file class neither corpus.py nor the hygiene tests
  model.**
  Unverifiable acceptance criterion "overwrites only manifest-managed
  files" until the mechanism is picked and the stale-sidecar case is
  specified. Evidence: 44-02-PLAN.md Task 3 action + test (c), D-22.

- **[L-1] R-2's "take max-ts row per fingerprint" uses the ts STRING
  (ISO-8601 "%Y-%m-%dT%H:%M:%SZ", machine.py:1326 / cli.py:1641) --
  second-granularity; two rows for one fingerprint written within the
  same second (parallel CI, or write + immediate adjudicate) order
  arbitrarily, making "latest row wins" nondeterministic at second
  boundaries. Low probability, but the deterministic tiebreak (row file
  order) is free and unspecified. Evidence: 44-RESEARCH.md R-2,
  machine.py:1326.

- **[L-2] The CONTEXT header says "22 decisions total" but the decisions
  block runs D-01..D-27 (27 entries). Stale count -- the very class of
  cross-artifact inconsistency CP1b exists to catch; zero functional
  impact. Evidence: 44-CONTEXT.md:8 vs :202-234.

- **[L-3] 44-03 depends_on [44-01] and 44-02 depends_on [44-01]; 44-02
  and 44-03 both touch machine.py-adjacent surface (44-03 modifies
  machine.py + gate_check.py, 44-02 modifies cli.py) with no declared
  ordering between them, and both must merge after 44-01 -- if 44-03
  lands first, 44-02's D-18 coupling fixtures (real CI-write path) run
  against a CI writer whose findings are post-suppression, changing the
  fixture semantics silently. Merge order should be pinned (44-02 before
  44-03) or the coupling test must assert which generation of writer it
  consumed. Hypothesis about process, not code. Evidence: 44-02-PLAN.md
  frontmatter depends_on, 44-03-PLAN.md frontmatter, D-18 coupling rule.

## Axis 2 -- Concurrency/atomicity

- Single-write O_APPEND under PIPE_BUF: verified append_row does one
  `fh.write(line)` per row under `open("a")` (ledger.py:62-74). Sound on
  POSIX local fs; R-4's network-mount caveat is documented. Clean.
- Dedup check-then-act TOCTOU accepted by D-08 with the extractor-side
  read dedup as backstop -- honest and explicit. Clean.
- But see H-1 (non-git fallback makes "the ledger" cwd-dependent) and
  L-1 (ts tiebreak) -- counted there.
- One residual: D-08 dedup scans `iter_rows` on EVERY CI terminal write
  (O(N) full parse per run, D-16 accepts ~3600 rows/6mo). Fine as
  documented; the >10k trigger is stated. Clean, except the dedup scan
  runs inside the same try/except as the write per D-19 -- a malformed
  ledger line just warns (iter_rows tolerance), so no new failure mode.
  Axis 2: clean modulo H-1/L-1.

## Axis 3 -- CI-write insertion point

- The :541-542 insertion point is real and correctly placed (after
  _persist_state, before return). But B-3: the mutation-survivor FAIL
  return at :360 bypasses it. Additionally the PENDING returns (:381/:391)
  are correctly NOT terminal -- no row should be written; the plan does
  not state this explicitly, leaving it to the implementer to not write
  "partial" rows on PENDING.
- Consumers of state.json: `_persist_state` precedes the write, and the
  ledger write appends infra_errors AFTER persisting (per the plan's
  ordering, a write failure's infra_errors note lands in memory after
  state.json was already saved -- the persisted state.json does NOT
  contain the ledger-failure note; a later reader of state.json sees a
  clean run whose ledger write actually failed). Minor: D-19 says "degrades
  to a stderr warning" + infra_errors, but with the write after
  _persist_state, the infra_errors entry never reaches disk in CI (CI
  discards state.json next run anyway per STATE-09 -- so arguably moot,
  but the ordering deserves one sentence). Counted as part of B-3's
  insertion-point finding, not separately scored.
- Axis 3: NOT clean (B-3).

## Axis 4 -- Adjudicate inheritance model

- Append-only upgrade with inheritance from the source UNADJUDICATED row
  is sound against cli.py:1643-1654 (the gap it closes is real --
  including the unmentioned version_sensitive loss).
- Error paths (absent fp / already-terminal / invalid state) are
  specified and testable. Echo requirement (D-20a) present.
- M-5: the already-terminal error contradicts mark's re-ruling semantics;
  the metadata-losing mark path stays open.
- One unhandled edge: adjudicating a fingerprint whose latest row is
  UNADJUDICATED but whose base/head are BOTH empty strings (rows from a
  non-git CI run where the writer skipped per the :1318 guard -- wait,
  the guard means no such rows exist; but `mark` can create
  base==head==HEAD rows per H-2, and adjudicating THOSE inherits
  meaningless SHAs into a terminal row that 44-02 will
  empty-diff-export). Overlaps H-2; the adjudicate path needs no separate
  guard if H-2 is fixed at export or mark time.
- Axis 4: clean modulo M-5/H-2.

## Axis 5 -- export-eval extractor

- Dead-SHA handling via cat-file -e on both SHAs, skip+warn+count+list,
  no manifest entry: correct against runner.py:747-755 (replay needs only
  the diff file, not live SHAs) -- the R-5 reframe is verified TRUE.
- expected_verdict derivation catch->HOLD/no-catch->PASS satisfies
  corpus.py:111-116 (load raises otherwise). Verified.
- H-2: base==head ESCAPED rows produce resolvable-but-empty diffs; not
  covered by D-03.
- M-3: D-17 strip is single-path string surgery; rename/copy hole.
- D-15 counter precedence (unadjudicated > stale-sha > dedup-collapse) is
  documented and sums to total -- verified as specified; but "dedup-
  collapse" needs a defined key (fingerprint+terminal_state per D-08's
  read-side note, latest wins) and the plan does not state which row
  survives when two terminal rows for one fingerprint disagree in state
  (FIXED then DISPROVED): "latest row wins" is implied by R-2 but not
  restated for the extractor's collapse. Minor spec gap, folded into M-6
  family but not scored separately.
- PII guard (basename provenance, no absolute paths) verified against the
  load_corpus contract (unknown keys ignored, so pre_fix_source riding as
  an extra is safe). Clean on that point.
- Axis 5: NOT clean (H-2, M-3).

## Axis 6 -- Operability

- Two-layer kill-switch: env var layer is sound; config layer is DEAD in
  review-only repos per B-2 (load_gate_config refuses test-less
  gate.yaml). This is the operability finding that matters most: the
  repo-level switch fails exactly where it would be used.
- Failure isolation: single try/except around config-read + write is
  correctly specified (44-01 Task 2 test (g)); the OSError-only catch on
  the write side plus FileNotFoundError/ValueError on the config side is
  spelled out. Sound modulo B-2.
- Output hygiene (D-22): default out dir, force gate, foreign-file
  preservation -- specified; M-6 (managed-set mechanism unsettled).
- survives CI: the write runs synchronously at the terminal; worst case
  it adds an O(N) ledger scan + one write per finding to every CI run --
  bounded, documented (D-16). No new subprocess, no network. Clean.
- Axis 6: NOT clean (B-2, M-6).

## Axis 7 -- What everyone missed

- B-1 (D-27's ledger/data-model amputation) is the phase's biggest
  internal contradiction: the read-side convergence decisions D-23..D-25
  assume every blocking finding class can acquire a terminal row; D-27
  removes a whole class from that universe.
- B-3 (mutation-FAIL terminal writes nothing) contradicts D-01's
  "all three terminal states" claim.
- Contradictory decision: D-01 claims ESCALATED is a CI terminal state
  that writes rows; ESCALATED is only reachable from _run_local
  (machine.py:612,629,693,699 -- all in _run_local or its helpers);
  _run_ci can NEVER return ESCALATED (verified: the only Verdict.ESCALATED
  sites are under _run_local). Either D-01's parenthetical is wrong for CI
  or the plan must define CI-ESCALATED semantics. Not scored separately
  (folds into B-3's "which exits write rows" question) but flagged.
- Scope creep: 44-03 (D-23..D-27) is a behavioral change to every CI
  review (suppression changes verdicts) riding a phase titled
  "eval-on-duty" whose stated goal is corpus growth; the phase boundary
  in CONTEXT <domain> mentions only write+extract. The scope extension is
  user-ratified (documented), so not a violation -- but the phase's
  must-pass review bar should acknowledge the verdict-changing blast
  radius. Not scored.
- Unverifiable acceptance criteria: M-1 (unbounded non-evidence fields),
  M-6 (managed-set mechanism), and 44-03 Task 2's "classification rule ...
  is defined in the 44-03 plan" -- the 44-03 plan says the rule "is defined
  conservatively" but never defines it (which axes/keywords downgrade is
  left to the implementer; the acceptance test "ambiguous stays CONFIRMED"
  is untestable without the list). Scored: M-7 below.

- **[M-7] D-27's style classifier is a decision-shaped hole: neither
  CONTEXT D-27 nor 44-03 Task 2 enumerates which axes/keywords/sources
  downgrade to advisory -- "only findings the review pipeline already
  tags as style-adjacent" cites no such tagging mechanism in the real
  code (grep: StateFinding carries source L0/L1/MUTANT/E2E_CHECK/
  COVERAGE/INFRA/FIXVAL per claim.py:24-32 -- none of these is a style
  tag; the only style-adjacent signal is axis_claim free text, which
  D-14 already flagged as unmappable). The implementer must invent the
  rule, which is exactly what CP1 plan-gates exist to prevent.**
  Evidence: 44-CONTEXT.md D-27, 44-03-PLAN.md Task 2 action,
  claim.py:24-32, D-14.

---

SCORECARD: B=3 H=3 M=7 L=3
