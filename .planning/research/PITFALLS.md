# Domain Pitfalls: Forge v2.4 "Honest Green"

**Domain:** Adding 6 review axes + eval scorecard to existing 12K LOC review pipeline
**Researched:** 2026-06-09
**Mode:** Targeted pitfall analysis for v2.4 milestone (subsequent to v2.3)

---

## Critical Pitfalls

### Pitfall 1: Semgrep CE Intraprocedural Ceiling (REVIEW-TRUST-01)

**What goes wrong:** Semgrep Community Edition is intraprocedural-only. The
gate.yaml exfil pattern (config value loaded in `load_gate_config()` -> flows
through `_resolve_backend()` -> reaches `subprocess`/`urlopen` in
`llm_invoke.py`) crosses 3+ function boundaries. CE cannot track this flow.
All function calls are opaque in CE -- it assumes tainted data propagates
through any call, generating false positives on safe wrappers AND missing
real sinks behind indirection. The Pro engine (`--pro-intrafile` or `--pro`)
adds cross-function/cross-file taint but requires a paid license.

**Warning signs:**
- Semgrep rule fires on direct `subprocess.run(user_input)` fixtures but
  misses the actual gate.yaml -> base_url -> urlopen chain in forge
- High false positive rate on safe wrapper functions (CE treats all calls
  as taint-propagating by default)
- Rule works in tests but not on real code paths

**Silent-skip trap:** When semgrep is not on PATH, `shutil.which("semgrep")`
returns None. If the taint gate silently skips (as existing detect.py does
for missing linters -- adds to `missing` list, continues), the security axis
reports no findings. This is the coverage-gate false-green failure mode: the
gate is absent, verdict says PASS, code ships with the vuln. The seed brief
mandates loud-fail, never silent-skip.

**Rule maintenance:** Custom taint rules are YAML that resemble source code.
Each new config->sink pattern (e.g., adding `api_key_file` reads) needs a
rule update. Stale rules = stale coverage. Unlike ruff/shellcheck rules
(upstream-maintained), taint rules are forge-authored and forge-maintained.

**Prevention:**
1. CE rules catch DIRECT source->sink (same-function). Write rules for the
   patterns CE can see. Do not promise cross-function coverage from CE.
2. The adversarial provenance question ("who controls this input, worst
   attacker value?") is the un-tooled fallback that catches what CE misses.
   It is a prompt addition, not a tool dependency.
3. When semgrep is absent: HARD FAIL the taint gate (exit code 2, not skip).
   Add `semgrep` to detect.py's registry with `required_for_security: True`
   flag distinct from optional linters.
4. Taint rules live in `.code-forge/rules/` with a test fixture that
   exercises each rule. CI runs the fixture; rule rot = test failure.

**Phase mapping:** Phase 17 (Trust Gate). The CE limitation shapes the
architecture: CE for direct patterns + prompt for cross-function + loud-fail
on absent binary.


### Pitfall 2: Revert-RED Fragility (REVIEW-FIXVAL-01)

**What goes wrong:** FIXVAL reverses non-test hunks from a bug-fix diff,
runs the test suite, expects RED (test fails = test actually covers the fix).
Three fragility modes:

**(a) Hunk revert fails mechanically.** `git apply --reverse` is atomic --
if ANY hunk fails, the entire patch is rejected. Dependent hunks (e.g., a
function rename in hunk 1 used by hunk 2) make selective revert impossible.
Merge conflicts arise when the reversed patch context does not match the
working tree (common after rebases or with overlapping diffs). The `--reject`
flag applies partial hunks but leaves `.rej` files that need manual cleanup.

**(b) Nondeterministic tests mask the signal.** A flaky test may go RED on
revert due to timing/env, not because it covers the fix. Or it may stay GREEN
on revert because the flaky path happened to exercise the fixed code path
this time. Research shows mutation scores vary up to 5% from nondeterminism
alone. The revert-RED signal is only trustworthy with deterministic tests.

**(c) STING transforms break tests for the wrong reason.** Behavior-preserving
transforms (identifier rename, operand swap) validate that the test checks
semantics, not syntax. But "behavior-preserving" assumes the transform does
not break the build. An identifier rename that collides with an existing name,
or an operand swap on a non-commutative operation misclassified as commutative,
produces a test failure that looks like an overfit detection but is actually a
transform bug.

**Warning signs:**
- FIXVAL reports "revert failed" on >30% of bug-fix diffs
- FIXVAL reports RED but manual inspection shows the test failure is unrelated
  to the fix (timing, import error, env dependency)
- STING transform produces a build error (not a test failure)

**Prevention:**
1. Hunk classification: separate test hunks from non-test hunks using file
   path patterns (configurable in gate.yaml `test_patterns`). If separation
   fails (e.g., test and impl in same file), skip FIXVAL with an explicit
   UNVERIFIED notice, never a silent pass.
2. Revert strategy: try `git apply --reverse` first. On failure, fall back
   to `git stash` + `git checkout HEAD~1 -- <non-test-files>`. On second
   failure, report UNVERIFIED (not PASS, not FAIL). This reuses the verdict
   calibration principle from RUNTIME-01.
3. Nondeterministic waiver: allow `# fixval-waiver: nondeterministic` in
   the test file or a gate.yaml flag. The waiver is recorded in the verdict
   (transparent), not silent. Mirror the kernel-C Beaker exception model.
4. STING transforms: run the transform, check build FIRST. Build failure =
   invalid transform (discard it, try next). Only test failures after a
   successful build count as overfit detection.

**Phase mapping:** Phase 17 (FIXVAL). The revert fragility is the core risk.
Build the happy path first (clean hunk separation), then the fallback chain.


### Pitfall 3: Advisory Creep to Blocking

**What goes wrong:** Advisory axes (RUNTIME, LEGACY, INTENT, SYSTEM) are
defined as NEVER blocking by the founding principle. Over time, pressure
creeps in: "this advisory finding is critical, it should block." An
implementer adds a `severity: P0` to an advisory finding. The state machine
treats P0 as cycle-resetting. The advisory finding now blocks commits. The
founding principle is violated incrementally, not by a single decision.

The creep path: advisory finding -> high severity label -> state machine
treats severity as blocking signal -> cycle counter resets on advisory ->
advisory becomes a de facto gate.

**Warning signs:**
- Advisory findings appear in `consecutive_clean_rounds` reset logic
- A code change adds severity filtering to `_fixpoint_reached()` that
  includes advisory-sourced findings
- PR review asks "why doesn't this critical advisory finding block?"
- New axis code calls `StateFinding()` with `disposition=CONFIRMED` instead
  of `DISMISSED` (advisory's only valid disposition)

**Prevention:**
1. Type-level enforcement: advisory findings use a distinct type or flag
   (`advisory: bool` on StateFinding). The state machine's
   `_fixpoint_reached()` filters them out structurally:
   `[f for f in findings if not f.advisory]`. This is a compile-time (type
   check) guarantee, not a runtime convention.
2. Disposition constraint: advisory findings MUST use `DISMISSED` disposition.
   The `StateFinding` constructor validates this: advisory=True +
   disposition=CONFIRMED raises ValueError. This makes the creep path a
   crash, not a silent drift.
3. Test invariant: a test that creates an advisory P0 finding and runs the
   state machine, asserting the cycle counter does NOT reset. This test
   catches any future change that breaks the boundary.
4. Receipt segregation: advisory findings are recorded in a separate section
   of the receipt (`advisory_findings`), never in the main `findings` array
   that drives convergence. Receipt verification (code-forge verify) checks
   only the main array.

**Phase mapping:** All phases that add advisory axes (RUNTIME in Phase 17,
LEGACY/INTENT in Phase 18, SYSTEM in Phase 19). The type-level enforcement
must ship BEFORE the first advisory axis.


### Pitfall 4: Eval Corpus Too Small for Signal (EVAL-01)

**What goes wrong:** The eval corpus is 9 named real bugs: E1-E6 (runtime
escapes), gate.yaml RCE, BUG-P12-01 (hollow test), ttl_class. With 9 data
points, any metric has high variance. A single test flipping between runs
changes the false-green rate by 11%. Statistical significance requires
corpus-level improvements of >5 points (per code-generation benchmark
research) -- with 9 items, every single item is 11 points. The scorecard
cannot distinguish real improvement from noise.

**Mock vs real divergence:** The seed brief mandates "eval drives the REAL
backend, never mocks." But the real backend (LLM call) is nondeterministic.
Running the same buggy/fixed pair twice may produce different verdicts. The
eval needs multiple runs per pair to measure rates, not single pass/fail.

**Regression blind spot:** Testing only the new axes (RUNTIME, FIXVAL, TRUST)
on the corpus misses whether existing axes regressed. The corpus must be run
against the FULL pipeline (all axes), not just the new ones. Otherwise a new
axis that interferes with L0/L1 convergence goes undetected.

**Warning signs:**
- False-green rate swings >10% between eval runs with no code change
- Eval passes on new axes but full pipeline time/cost doubles
- A bug pair (e.g., ttl_class) flips verdict based on LLM temperature

**Prevention:**
1. Accept the ceiling honestly: 9 items is a SMOKE TEST, not a benchmark.
   Call it "false-green smoke" in the scorecard, not "false-green rate."
   Report raw counts (caught: 7/9) not percentages (77.8% -- false
   precision).
2. Multiple runs: each eval pair gets 3 runs. A pair is "caught" only if
   2/3 runs flag it. This dampens LLM nondeterminism at the cost of 3x
   eval time.
3. Full-pipeline regression: every eval run exercises the COMPLETE pipeline
   (L0 + L1 + L2 + new axes), not just the new axis under test. Record
   per-axis contributions so regressions are attributable.
4. Corpus growth plan: document that the corpus grows as new escapes are
   dogfooded. Each dogfood escape becomes a new eval pair. The scorecard
   design must handle a growing corpus (use counts, not hardcoded arrays).
5. Deterministic-where-possible: for axes that do not use LLM (FIXVAL,
   TRUST-taint), the eval is deterministic. Only RUNTIME/LEGACY/INTENT
   (LLM-reviewed) need multi-run averaging.

**Phase mapping:** Phase 17 (eval scaffold built alongside P0 axes). The
smoke-not-benchmark framing must be established in the scaffold design.


## Moderate Pitfalls

### Pitfall 5: Graph/Dependency Licensing and Resource Trap (REVIEW-SYSTEM-01)

**What goes wrong:** Two distinct traps:

**(a) Licensing confusion.** sem-core (entity extraction) is MIT/Apache-2.0.
inspect-core (entity-level code review) is FSL-1.1-ALv2. Forge IS a code
review tool -- using inspect-core is a direct competing-use violation.
The names are similar ("sem" and "inspect" are both Ataraxy Labs products),
the repos are in the same GitHub org, and sem-core documentation references
inspect as a consumer. An implementer who reads "use sem-core" may grab
inspect-core by mistake, especially since inspect's API is more convenient
for review use cases.

**(b) Memory blowup.** Tree-sitter parsing + graph construction + blast-radius
computation scale with repo size. code-review-graph benchmarks show efficiency
gains on 1K-5K file repos but notes that small repos (<20 files) see metadata
overhead exceeding raw file size. The inverse concern: very large repos (50K+
nodes) crash `get_architecture_overview` (per CLAUDE.md memory feedback).
Forge's own codebase is small (~90 .py files), but users' repos vary.

**(c) Dynamic language accuracy.** Tree-sitter provides syntactic parsing, not
semantic analysis. Python's duck typing, metaprogramming (setattr, __getattr__),
and dynamic dispatch mean the call graph is incomplete. A function called via
`getattr(obj, method_name)()` is invisible to tree-sitter. The blast-radius
ranking underestimates the true impact of changes to dynamically-dispatched
code.

**Warning signs:**
- PR imports from `inspect-core` or `inspect` crate/package
- Graph build takes >30s on a user's repo (expected: <5s for <5K files)
- SYSTEM-01 advisory misses a cross-component dependency because the call
  was dynamic (string-based dispatch, decorator registration)

**Prevention:**
1. Hard gate in CI: a test that imports from `sem` (not `inspect`) and
   verifies the license file contains "MIT" or "Apache." Document the
   boundary in CONTRIBUTING.md.
2. Graph is opt-in (P2, not P0). Ship RUNTIME/FIXVAL/TRUST first. If
   SYSTEM-01 ships, default to OFF; enable via gate.yaml `graph: true`.
3. Resource budget: cap graph build at 30s / 500MB RSS. On timeout, skip
   graph with UNVERIFIED, never OOM the user's machine.
4. Dynamic-language disclaimer: SYSTEM-01 advisory includes "call graph is
   syntactic; dynamic dispatch (getattr, decorators) is not tracked." The
   honest ceiling.

**Phase mapping:** Phase 19 (SYSTEM-01 is P2 -- last to ship, first to cut
if milestone must shrink).


### Pitfall 6: Verdict Calibration Drift (REVIEW-RUNTIME-01)

**What goes wrong:** RUNTIME-01 introduces UNVERIFIED as a verdict qualifier:
"this code depends on N external components; runtime contract NOT verified."
Three drift modes:

**(a) UNVERIFIED fatigue.** If every review says "5 surfaces UNVERIFIED,"
users stop reading. The signal becomes noise. The advisory is technically
honest but practically useless.

**(b) Real vs simulated smoke confusion.** Forge step-4 already runs smoke
tests. RUNTIME-01 says a simulated smoke (bash logic check) must report
UNVERIFIED, not PASS. But distinguishing "real smoke" from "simulated smoke"
is subjective. An LLM reviewer calling `subprocess.run(["python", "test.py"])`
and checking exit code -- is that real or simulated? The boundary is fuzzy.

**(c) Wrong unverified surface list.** The LLM enumerates external
dependencies (systemd, nftables, curl) but misses one (e.g., DNS resolution
timing). Or it lists dependencies that are not actually external (a pure
function misclassified as having side effects). The list is advisory but
wrong lists erode trust in the axis.

**Warning signs:**
- User adds `# pragma: no-runtime-check` annotations to suppress UNVERIFIED
- Smoke test reports PASS but the code depends on systemd restart behavior
  (the exact E1 escape pattern)
- UNVERIFIED surface list includes items that are clearly internal (e.g.,
  "depends on: dict.get() return value")

**Prevention:**
1. Surface count threshold: only emit the UNVERIFIED notice when the count
   of external dependencies exceeds a minimum (e.g., >=1 subprocess, file I/O,
   network call, or process lifecycle operation). Pure-logic diffs get no
   UNVERIFIED noise.
2. Real-smoke definition: a smoke is "real" if and only if it invokes the
   actual artifact binary/script and observes a side effect (exit code, output,
   state change). Checking a variable in a subshell is simulated. Document
   this binary distinction, do not leave it to LLM judgment.
3. Surface list review: the LLM lifecycle question asks for external
   side-effects. The answer goes through the existing L1 falsification path
   (check: does the code actually call subprocess/open/socket?). This
   grounds the list in AST evidence, not pure LLM reasoning.
4. Progressive disclosure: the verdict line says "UNVERIFIED: 3 runtime
   surfaces" (short). Full details in a collapsible section of the receipt,
   not in the main verdict output.

**Phase mapping:** Phase 17 (RUNTIME-01 verdict calibration). The threshold
and real-smoke definition must be decided during planning, not deferred to
implementation.


### Pitfall 7: Integration Complexity Explosion

**What goes wrong:** 6 new axes + 3-cycle convergence = combinatorial
growth in test surface and review loop duration.

**(a) Test explosion.** Currently 1,195 tests across 71 files. Each new axis
needs: unit tests for the axis logic, integration tests for axis + state
machine interaction, and fixture pairs (buggy/fixed). 6 axes x 3 test
categories = 18 new test groups minimum. Cross-axis interaction tests (does
FIXVAL interfere with TRUST? does LEGACY's advisory interact with RUNTIME's
UNVERIFIED?) multiply further. Realistic estimate: 200-400 new tests.

**(b) Review loop slowdown.** Each advisory axis adds LLM calls or tool
invocations to every review round. Current 3-cycle review: 9 LLM calls.
Adding RUNTIME lifecycle question + TRUST provenance question + LEGACY
blame lookup + INTENT classification = 4 additional calls per round = 12
additional calls per review (3 cycles x 4). Total goes from 9 to 21 LLM
calls. Review time roughly doubles. Cost roughly doubles.

**(c) State machine complexity creep.** machine.py is 915 lines. It
currently manages L0 (deterministic), L1 (LLM), L2 (mutation), and e2e.
Adding 6 axis-specific phases with distinct blocking/advisory semantics,
different disposition rules, and different convergence participation
pushes machine.py toward 1500+ lines. The state machine becomes the
hardest-to-test module.

**Warning signs:**
- Test suite runtime exceeds 5 minutes (currently ~90s)
- machine.py exceeds 1000 lines
- A change to one axis unexpectedly resets the cycle counter (cross-axis
  interference)
- Cost per review exceeds $15 for a medium diff

**Prevention:**
1. Axis runner interface: define `AxisRunner(Protocol)` with `run() ->
   list[StateFinding]` and `is_advisory: bool`. Each axis is a separate
   module implementing this protocol. machine.py calls runners in sequence;
   it does not contain axis logic. This caps machine.py growth.
2. Advisory axes run ONCE per review, not per cycle. The 3-cycle convergence
   applies only to blocking axes (L0, L1, FIXVAL, TRUST). Advisory axes
   (RUNTIME, LEGACY, INTENT, SYSTEM) run after the final clean cycle, adding
   their findings to the receipt but not participating in convergence. This
   caps the LLM call increase at 4 (not 12).
3. Phased testing: each axis gets its own test file (`test_axis_runtime.py`,
   `test_axis_fixval.py`). Cross-axis tests go in `test_axis_integration.py`.
   Run axis tests in parallel (pytest-xdist). Keep total suite under 3
   minutes.
4. Cost projection: estimate cost impact per axis BEFORE implementation.
   If advisory axes exceed $3/review, investigate caching (same LEGACY
   findings across cycles) or batching (one LLM call for all advisory
   questions).

**Phase mapping:** Spans all phases. The AxisRunner protocol must ship in
Phase 17 (first axis) to prevent machine.py bloat. Advisory-runs-once
decision must be made during Phase 17 planning.


## Minor Pitfalls

### Pitfall 8: STING Transform Corpus Maintenance

**What goes wrong:** STING overfit guards use behavior-preserving transforms
(identifier rename, operand swap). The transform set must be language-specific
(Python identifier rules differ from shell). Forge currently supports Python
and shell. Each new language needs a transform corpus. Without maintenance,
STING degrades to Python-only.

**Prevention:** Define transforms as data (YAML/JSON), not code. One file
per language. Start with Python (the forge codebase language). Shell and
others are YAGNI until demanded. Document the transform format so users can
contribute language support.

**Phase mapping:** Phase 17 (FIXVAL). Python-only MVP.


### Pitfall 9: SEC-01 Opt-In UX Friction

**What goes wrong:** SEC-01 requires explicit opt-in for repo-supplied
backends (mirroring direnv allow). If the opt-in is too noisy (prompt on
every `code-forge review`), users disable it. If too quiet (one-time flag
buried in config), users forget they opted in and run hostile repos' backends.

**Prevention:** Use `code-forge trust` (one-time per repo, stored in
`~/.config/code-forge/trusted.json`). First review of an untrusted repo
shows a clear warning with the backends it wants to use. No prompt on
subsequent runs. Revoke with `code-forge trust --revoke`.

**Phase mapping:** Phase 17 (SEC-01). Ship alongside TRUST-01 taint gate.


### Pitfall 10: Diff-Size Tiering Interaction with New Axes

**What goes wrong:** v2.3 introduced diff-size adaptive tiering (<50 lines=2
cycles, 50-199=3, >=200=4). New blocking axes (FIXVAL, TRUST) that reset
the cycle counter interact with tiering. A small diff (2-cycle tier) that
triggers a FIXVAL finding resets to 0 and needs 2 more clean cycles. If
FIXVAL is flaky, the small diff hits MAX_TOTAL_ROUNDS faster than expected.

**Prevention:** FIXVAL and TRUST findings follow the same reset logic as L0/L1
(this is by design -- they are about the committed diff). But their false
positive rate must be monitored during dogfood. If FIXVAL resets >50% of
reviews, the revert strategy is too fragile (see Pitfall 2).

**Phase mapping:** Phase 17 (FIXVAL). Monitor during dogfood.


## Phase-Specific Warnings

| Phase | Likely Pitfall | Mitigation |
|-------|---------------|------------|
| 17 (Trust Gate + Eval Scaffold) | Semgrep CE ceiling (P1) | CE for direct, prompt for cross-function, loud-fail on absent |
| 17 (Trust Gate + Eval Scaffold) | Revert-RED fragility (P2) | Fallback chain: apply --reverse -> checkout -> UNVERIFIED |
| 17 (Trust Gate + Eval Scaffold) | Eval corpus too small (P4) | Smoke-not-benchmark framing, raw counts not percentages |
| 17 (Trust Gate + Eval Scaffold) | Advisory type enforcement (P3) | Ship advisory flag + disposition constraint BEFORE first axis |
| 17 (Trust Gate + Eval Scaffold) | SEC-01 opt-in UX (P9) | `code-forge trust` one-time per repo |
| 18 (Legacy + Intent) | Advisory creep (P3) | Test invariant: advisory P0 does NOT reset cycle counter |
| 18 (Legacy + Intent) | UNVERIFIED fatigue (P6) | Surface count threshold, progressive disclosure |
| 19 (System Graph) | Licensing confusion (P5a) | CI test: import from sem, verify MIT license |
| 19 (System Graph) | Memory blowup (P5b) | 30s/500MB cap, skip with UNVERIFIED on timeout |
| 19 (System Graph) | Dynamic language accuracy (P5c) | Honest disclaimer in advisory output |
| All phases | Integration complexity (P7) | AxisRunner protocol, advisory-runs-once, parallel test |
| All phases | Review loop cost (P7b) | Cost projection per axis before implementation |
| All phases | Tiering interaction (P10) | Monitor FIXVAL reset rate during dogfood |

---

## Anti-Patterns to Explicitly Avoid

### Anti-Pattern 1: Treating CE Semgrep as Cross-Function Capable

**What:** Writing taint rules that assume cross-function tracking and
trusting them to catch the gate.yaml exfil chain.

**Why bad:** CE is intraprocedural by design. The rules will not fire on
indirect flows. False confidence in the gate is worse than no gate.

**Instead:** CE rules for direct patterns. Adversarial prompt for indirect.
Document the boundary.

### Anti-Pattern 2: Promoting Advisory to Blocking "Just This Once"

**What:** Adding a special case where a RUNTIME or LEGACY advisory finding
blocks because it looks critical.

**Why bad:** Every special case erodes the founding principle. The next
"just this once" is easier. Within 3 milestones, advisory axes are de facto
gates, and forge has lost its "deterministic pipeline is the sole gate"
differentiator.

**Instead:** If a finding is truly about the committed diff, it belongs in
a BLOCKING axis (FIXVAL, TRUST). If it is about runtime/legacy/system, it
is advisory by construction. No exceptions.

### Anti-Pattern 3: Synthetic Eval Bugs

**What:** Generating artificial bugs to pad the eval corpus past 9 items.

**Why bad:** The seed brief mandates real bugs only. Synthetic bugs are
easier to catch than real ones (they follow mutation patterns, not human
error patterns). A high score on synthetic bugs gives false confidence.

**Instead:** Grow the corpus organically from dogfood escapes. Each new
escape is a gift -- it is a free eval pair.

### Anti-Pattern 4: Running Advisory Axes Per-Cycle

**What:** Including advisory axes in every round of the 3-cycle loop.

**Why bad:** Triples the cost of advisory axes with zero quality benefit
(advisory findings do not participate in convergence). Each additional
LLM call is ~$0.50-2.00.

**Instead:** Advisory axes run ONCE after convergence (post-fixpoint).
Their findings are appended to the receipt but do not affect the verdict.

---

## Sources

### Semgrep CE Limitations
- [Semgrep Taint Analysis Overview](https://semgrep.dev/docs/writing-rules/data-flow/taint-mode/overview)
- [Semgrep Cross-File Analysis (Pro)](https://semgrep.dev/docs/semgrep-code/semgrep-pro-engine-intro)
- [Semgrep CE Join Mode Workaround](https://jdsalaro.com/snippet/semgrep/general/join-mode-interfile-interprocedural/)

### Revert / Mutation Testing Fragility
- [git-apply Documentation](https://git-scm.com/docs/git-apply)
- [Mitigating Flaky Tests in Mutation Testing (Shi et al., ISSTA 2019)](https://mir.cs.illinois.edu/marinov/publications/ShiETAL19FlakyMutation.pdf)
- [Eradicating Non-Determinism in Tests (Fowler)](https://martinfowler.com/articles/nonDeterminism.html)

### Licensing
- [sem-core MIT License](https://github.com/Ataraxy-Labs/sem/blob/main/LICENSE-MIT)
- [Ataraxy-Labs GitHub Org](https://github.com/Ataraxy-Labs)
- [FSL License](https://fsl.software/)
- Prior research: STACK.md confirms inspect-core is FSL-1.1-ALv2, competing-use violation for forge

### Eval / Benchmarking
- [Benchmarks and Metrics for Code Generation (arXiv 2406.12655)](https://arxiv.org/html/2406.12655v1)
- [G-Eval Framework](https://deepeval.com/docs/metrics-llm-evals)

### Graph / Code Intelligence
- [code-review-graph GitHub](https://github.com/tirth8205/code-review-graph)
- [Codebase-Memory: Tree-Sitter Knowledge Graphs (arXiv 2603.27277)](https://arxiv.org/html/2603.27277v1)
- CLAUDE.md memory: graph crashes on 50K+ node repos, 0 callers for shell
