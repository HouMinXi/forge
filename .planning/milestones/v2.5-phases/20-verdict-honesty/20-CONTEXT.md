# Phase 20: Verdict Honesty - Context

**Gathered:** 2026-06-12
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 20 delivers REVIEW-RUNTIME-01: the forge verdict declares the runtime
surface it did NOT verify, making green honest. Three concrete forms:
(a) a smoke that cannot be proven to have really run reports UNVERIFIED,
never PASS; (b) one fixed lifecycle/side-effect question is wired into every
review via a dedicated RUNTIME axis; (c) the verdict output carries a
"NOT VERIFIED" section listing unexercised runtime surfaces. RUNTIME is
strictly ADVISORY -- it never blocks a cycle, never gates a commit (SC4).
The eval scorecard records the RUNTIME axis against the E1-E6 escape corpus
(forge green -> runtime broke; surflare-watchdog evidence).

In scope: smoke-evidence receipt wrapper (`code-forge smoke-run`); smoke-axis
VERIFIED/UNVERIFIED status; RUNTIME AxisRunner (LLM call, fixed lifecycle
question, surface enumeration); verdict "NOT VERIFIED" display block;
eval RUNTIME-axis scoring (corpus schema extension + E1-E6 expectation fix);
both-outlet coverage (CLI machine.py + inline SKILL.md mirror).

Out of scope: any blocking behavior for RUNTIME (founding principle);
mechanical surface catalog (deferred pending eval evidence); legacy/intent
axes (Phase 21); graph triage (Phase 22); re-running the smoke itself to
verify (evidence validation only, no re-execution).

</domain>

<decisions>
## Implementation Decisions

### Trust model (SC1 -- what makes a smoke claim count)
- **D-01:** Mechanical evidence gate. A smoke claim counts ONLY with a
  machine-verifiable receipt (command transcript + exit code), modeled on the
  Phase 4 anti-shirk receipt machinery (`receipt.py` / `verify.py`). Executor
  self-report is never the trust root -- that is the same hole that produced
  the fake 9-pass incident. No receipt or failed validation = UNVERIFIED.
- **D-07:** Receipt producer is a forge-owned wrapper:
  `code-forge smoke-run [--surface "<name>"] -- <cmd>` executes the command
  itself, tees transcript + exit code into the receipt, and keys the receipt
  by diff content-hash (the 19.1 P-05/P-07 pattern: a code change invalidates
  the receipt). The executor can only choose to run or not run; it cannot
  forge receipt content.
- **D-08:** Default state is UNVERIFIED (fail-closed). Absence of evidence =
  unverified. Exactly two states: VERIFIED (valid receipt present) or
  UNVERIFIED. No third/partial state; partial coverage is conveyed by
  per-surface counts (D-11).

### UNVERIFIED placement (SC1/SC3)
- **D-02:** UNVERIFIED is the smoke-AXIS status, not a new top-level verdict.
  Verdict values stay PASS/FAIL/HOLD; exit codes unchanged (SC4: advisory
  never blocks). When UNVERIFIED, the verdict output appends a
  "NOT VERIFIED: <surfaces>" block.
- **D-11:** Per-surface accounting. `smoke-run --surface "<name>"` declares
  which runtime surface a run exercises. The NOT-VERIFIED list = (LLM-
  enumerated surface set) minus (receipt-declared surface set). Output reads
  like "smoke: 1/3 surfaces verified; NOT VERIFIED: [x, y]". A single
  marginal command must not wash the whole list green.

### Surface derivation (SC3)
- **D-03:** v1 = LLM enumeration. The RUNTIME axis LLM call answers the
  lifecycle question AND enumerates the diff's runtime surfaces in one
  structured response. Eval's 3-run 2-of-3 majority (Phase 17 D-11, already
  locked for LLM axes) tames stochasticity. A mechanical diff-signal catalog
  is deferred until eval proves LLM enumeration misses (see Deferred).

### Lifecycle question wiring (SC2)
- **D-04:** Dedicated RUNTIME axis call, post-convergence, via the existing
  AxisRunner seam (Phase 17 D-16: advisory axes run once after convergence;
  both PASS and HOLD trigger them). Do not pollute L1 falsify prompts.
  BOTH outlets must carry the question: CLI outlet via the machine.py axis;
  inline outlet via a mirror section in the code-forge SKILL.md flow.
  Always-on: SC2 says every review -- no gate.yaml opt-out. If the axis LLM
  call fails, record SKIPPED with reason (never-silent-skip taxonomy).
- **D-05:** One fixed standard lifecycle/side-effect question (with diff
  context slots), not per-diff generated. Deterministic, auditable, testable;
  iterate the wording only if eval shows it underperforms.
- **D-10:** Anti-drift for the two outlets: the canonical question text lives
  as a constant in src/code_forge; SKILL.md carries a mirror copy; one test
  asserts verbatim equality (lesson from the 19.1 dual-copy divergence).

### Verdict display (SC3)
- **D-09:** The smoke-axis status line is ALWAYS printed (VERIFIED with
  receipt fingerprint, or UNVERIFIED) -- silence must never read as
  "verified". The surfaces list expands only when non-empty. Dual output:
  stderr human block + receipts/advisory JSON (Phase 17 D-10 precedent).

### Eval scoring (SC5)
- **D-06:** "Caught" for an advisory axis = content match, and the corpus is
  corrected to be honest. corpus.yaml schema gains `expected_advisory`
  (escape-surface keywords) for RUNTIME entries. E1-E6 `expected_verdict:
  HOLD` is WRONG under the structural advisory-never-blocks principle
  (Phase 17 D-14); change each entry's expected_verdict to reflect what the
  real pipeline actually produces on that diff (verify per-entry, do not
  assume PASS).
- **D-12:** Matching mechanism = case-insensitive keyword substring against
  the advisory text; any keyword hit = caught. No LLM judge (a stochastic
  ruler cannot measure honesty); no regex (overfit risk). Planner picks
  per-entry keyword lists, wide-but-fair.

### Claude's Discretion
- Receipt JSON schema details (field names, timestamps, fingerprint format).
- Exact wording of the fixed lifecycle question (draft from the E1-E6
  escape catalog; eval will measure it).
- Per-entry `expected_advisory` keyword lists.
- Surface-name alignment between LLM enumeration and `--surface`
  declarations (normalization / fuzzy-match mechanics).
- Surfaces-list display cap / noise control.
- What each E1-E6 expected_verdict becomes (must reflect actual pipeline
  behavior per entry -- run and observe, do not assume).
- SARIF inclusion of the advisory block.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements and roadmap
- `.planning/REQUIREMENTS.md` -- REVIEW-RUNTIME-01 (P0, ADVISORY): three
  forms (a/b/c) + success criteria 1-4.
- `.planning/ROADMAP.md` -- Phase 20 Goal + SC 1-5; depends on Phase 17
  (eval scaffold scores RUNTIME axis; EVAL-CORPUS-REPAIR resolved, E1-E6
  are REPLAY).
- `.planning/PROJECT.md` -- founding principle: advisory axes NEVER block,
  structurally (not conventionally).

### Evidence (why this phase exists)
- `/tmp/draft_20260609_forge_v24_runtime_escape_catalog.md` -- E1-E6 escape
  catalog (verified present 2026-06-12). If gone, the corpus diffs are the
  ground truth.
- `tests/eval/corpus/corpus.yaml` -- E1-E6 + ttl_class entries tagged
  RUNTIME; diffs under `tests/eval/corpus/diffs/`. expected_verdict needs
  the D-06 correction.

### Code to reuse or extend (full relative paths)
- `src/code_forge/advisory.py` -- AdvisoryFinding dataclass + AxisRunner
  Protocol; RUNTIME implements `is_advisory()` = True.
- `src/code_forge/machine.py` -- `advisory_runners` list (line 154),
  `_run_advisory_axes` / `_serialize_advisories` / `_display_advisories`
  (lines 180-182): the RUNTIME axis registers here.
- `src/code_forge/receipt.py` + `src/code_forge/verify.py` -- Phase 4
  receipt/attestation machinery; the smoke-run receipt follows this model
  (D-01, D-07).
- `src/code_forge/eval/runner.py` -- AxisHook seam + `register_axis_hook`;
  `FixvalAxisHook` (line 72) is the model for RuntimeAxisHook;
  DETERMINISTIC_TAGS excludes RUNTIME so 3-run majority already applies.
- `src/code_forge/verdict.py` -- pure PASS/FAIL today; D-02 extends DISPLAY
  output, not verdict values.
- `src/code_forge/cli.py` -- subcommand registration pattern (install-hooks
  at line 362 as model); `smoke-run` joins as a new subcommand.
- `src/code_forge/llm_invoke.py` -- backend invocation path for the axis
  LLM call (uses the same configured backend as review).
- `src/code_forge/skills/code-forge/SKILL.md` -- inline-outlet flow; gains
  the RUNTIME mirror section (D-04) with drift-tested question text (D-10).
- `src/code_forge/skills/smoke-test/SKILL.md` -- smoke execution contract;
  gains `code-forge smoke-run` usage + the UNVERIFIED reporting contract.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `advisory.py` AxisRunner/AdvisoryFinding (Phase 17 D-14..D-18): complete
  advisory infrastructure -- separate list, separate serialization, split
  display, structural never-block. RUNTIME is a pure plug-in.
- `machine.py` advisory dispatch (180-182): runs once after convergence on
  both PASS and HOLD paths -- exactly where RUNTIME belongs.
- `eval/runner.py` FixvalAxisHook: the registration + scoring template;
  RUNTIME already classified as an LLM axis (3 runs, 2-of-3).
- `receipt.py`/`verify.py`: receipt write/validate machinery to extend for
  smoke receipts (content-hash keying precedent in 19.1 P-05).

### Established Patterns
- never-silent-skip: any axis that cannot run records SKIPPED + reason.
- Dual output stderr + JSON (Phase 17 D-10).
- Anti-shirk: trust mechanical evidence, never executor self-report.
- Drift test for code-constant vs SKILL.md mirror text (new in this phase,
  motivated by the 19.1 dual-copy divergence).

### Integration Points
- `machine.py` advisory_runners registration (CLI outlet).
- `cli.py` new `smoke-run` subcommand.
- `eval/runner.py` RuntimeAxisHook + corpus.yaml `expected_advisory` schema
  extension.
- `skills/code-forge/SKILL.md` + `skills/smoke-test/SKILL.md` mirror updates
  (inline outlet).

</code_context>

<specifics>
## Specific Ideas

- E1-E6 are the named evidence: forge went green and the runtime broke
  (stale nftables, pcap suffix, transit probe, curl tproxy, fast 502,
  reprobe blackout). The axis exists to make that class of escape VISIBLE,
  not to block it.
- The executor can game "did not run" but can never game "ran and here is
  the receipt" -- that asymmetry is the whole design (D-01/D-07).
- Silence must never read as verified: the status line is unconditional
  (D-09). The misread "no news = good news" is precisely what this phase
  kills.
- SC2 is always-on by definition: no opt-out knob for the lifecycle
  question.

</specifics>

<deferred>
## Deferred Ideas

- Mechanical surface catalog (static diff-signal table: systemd / nftables /
  sockets / subprocess / file side effects) -- add only if eval shows LLM
  enumeration misses real surfaces.
- Re-execution verification (forge re-runs the smoke itself) -- rejected for
  v1: side effects and cost; evidence validation only.
- LLM judge for eval advisory matching -- rejected v1: a stochastic ruler
  cannot measure honesty.
- `install-skill` generation-time injection of the question text -- the
  stronger single-source form if mirror copies multiply beyond two.
- PARTIAL smoke state -- no third state (D-08); per-surface counts already
  convey partial coverage.

</deferred>

---

*Phase: 20-Verdict Honesty*
*Context gathered: 2026-06-12*
