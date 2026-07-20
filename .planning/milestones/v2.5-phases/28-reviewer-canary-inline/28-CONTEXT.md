================================================================
PHASE 28 CONTEXT -- Reviewer Canary for the Inline Outlet (M2: LLM half)
Scope-locked input, 2026-06-17. Main session (Opus) = scope owner +
acceptance + locked design. Sub-session (Sonnet 4.6) = plan + execute.
================================================================

0. HOW TO USE THIS
   - Scope-locked input for `/gsd-plan-phase 28` then
     `/gsd-execute-phase 28`.
   - Section 2 ground truth is verified at file:line -- do NOT
     re-discover. Section 4 decisions are LOCKED. Section 7 gray areas
     are the ONLY open questions for planning.
   - This is a NEW integer phase (28), parallel to and independent of
     the v2.5 cross-repo phases (25/26/27) a parallel sub-session is
     working. It touches DIFFERENT files (canary.py / cli.py inline
     branch), so it does not collide with cross-repo work.
   - M1 (the deterministic harness half) is ALREADY BUILT AND COMMITTED
     on branch forge/near-perfect-inline @ c515db7. This phase is M2.
     Do NOT rebuild M1; build ON it.

1. GOAL + WHY IT EXISTS
   Goal: the inline review outlet (no backend, "lazy user" path) gains
   an OPT-IN objective laziness check. Today the inline outlet returns a
   bare Verdict.DELEGATED (cli.py ~line 1197) that rests entirely on the
   reviewer's honesty -- a reviewer that rubber-stamps an empty findings
   list passes unnoticed. M1 built the harness-side core that makes a
   rubber-stamp detectable: plant defects the reviewer cannot distinguish
   from real ones, then check how many it caught. M2 wires the LLM half:
   generate the planted defects, inject them into an isolated review copy,
   dispatch a fresh-context review, and gate on the result.

   Consumer: the user (self-named: "I'll use it this way myself, I'm
   lazy"). Smallest viable release = OPT-IN only (--canary flag /
   gate.yaml canary: block); the default inline path is UNCHANGED. Zero
   traffic when not opted in => near-zero maintenance cost. This passes
   the scope challenge on the named-consumer + opt-in basis.

2. VERIFIED GROUND TRUTH (file:line -- do NOT re-discover)

   M1 ALREADY BUILT (branch forge/near-perfect-inline @ c515db7):
   - src/code_forge/findings.py: finding_line(finding: Mapping) -> int.
     Returns the finding's 1-based line, or 0 when absent/unparseable
     (0 = "no specific line claim", a file-level finding).
   - src/code_forge/canary.py:
       Canary(canary_id: str, file: str, line: int, sha256: str,
              description: str = "")  -- frozen dataclass; the
              reviewer-invisible manifest entry. sha256 is audit-only.
       CanaryGateResult(total, threshold, caught: tuple[str,...],
              missed: tuple[str,...]); .passed = total>0 and
              len(caught)>=threshold (EMPTY MANIFEST FAILS CLOSED).
       CanaryPartition(real: tuple, canary: tuple).
       evaluate_canary_coverage(findings, manifest, *, threshold,
              line_window=2) -> CanaryGateResult. Raises ValueError when
              threshold<1 or threshold>len(manifest).
       partition_canary_findings(findings, manifest, *, line_window=2)
              -> CanaryPartition. Drops canary findings from real.
       Match rule (_matches): finding file normpath-equals canary file
              AND finding line>0 AND |line - canary.line| <= line_window.
              A line<=0 (file-level) finding NEVER matches.
   - src/code_forge/evidence.py:
       reverify_finding_cites(findings, source_lookup: Callable[[str],
              Sequence[str]|None]) -> CiteVerification(verified,
              unverified). File absent (lookup None) or line>len(lines)
              => unverified. line<=0 + file present => verified.
   - 31 tests green: tests/test_canary.py, test_evidence.py,
     test_findings.py.

   INLINE OUTLET (the wiring target):
   - cli.py: `if outlet == "inline":` (around line 1197) writes a stderr
     DELEGATED notice and `return Verdict.DELEGATED` (exit 5). This is the
     branch M2 augments when --canary is opted in. The DEFAULT (no opt-in)
     behavior of this branch MUST be unchanged.
   - Outlet resolution precedence (verify before editing): --outlet flag >
     FORGE_OUTLET env > gate.yaml outlet: > zero-config guard >
     reachability probe. Canary is orthogonal to outlet selection.

   BACKEND INVOCATION (for the fresh-context review dispatch):
   - src/code_forge/llm_invoke.py: invoke path dispatches by
     BackendConfig.type ("cli" => subprocess claude/custom binary;
     "api" => HTTP openai/anthropic). DEFAULT_TIMEOUT_S=120;
     FORGE_LLM_TIMEOUT_S overrides per call (resolved at call time).
     Public: Usage, LLMResult, LLMInvokeError.
   - reviewer_json.py: validate_reviewer_json(raw) -> dict. Finding
     REQUIRED keys: {"file","line","severity","description"}. This is the
     contract the fresh-context reviewer's output must satisfy before it
     reaches evaluate_canary_coverage.

   LOCKED DESIGN ANCHORS (from docs/design/reviewer-canary-spec.md sec 2;
   these are NON-NEGOTIABLE and pre-date M1):
   - D-16 / BOTH-04: a canary result MUST NOT drive outlet/model
     selection. A miss = "this round unreliable", never "switch model".
   - D-25: injection happens ABOVE the backend layer (prompt/diff level);
     identical for api and cli backends. No backend-specific code.
   - D-26: canary validates reviewer ATTENTION, not model capability.

3. RELATIONSHIP TO THE LOCKED SPEC-01 (docs/design/reviewer-canary-spec.md)
   SPEC-01 (Phase 9, validated 2026-06-03) designed canary for Outlet A /
   the StateMachine: synthetic _canary_NNN.py file appended to the diff,
   template library, prefix-match, single canary, affecting
   consecutive_clean_rounds. The inline outlet does NOT run the
   StateMachine (it returns DELEGATED before the machine), so SPEC-01's
   mechanism does not reach it. Phase 28 builds the INLINE variant:
   in-place semantic mutation in an isolated copy, multi-canary, file +
   line-window match. The two are COMPLEMENTARY, not duplicate.
   ACTION: Phase 28 appends a SUPERSEDE/EXTENDS note to SPEC-01 (do NOT
   silently rewrite it); preserve D-16/D-25/D-26/BOTH-04 fidelity. SPEC-01
   sec 10 item 7 already defers "Outlet B enforcement mode" to a feedback
   signal -- Phase 28 IS that signal.

4. LOCKED DECISIONS (do NOT re-litigate)

   D-28-01  Opt-in only. Canary runs on the inline outlet ONLY when the
            user opts in via `--canary` flag OR a gate.yaml `canary:`
            block. With no opt-in, cli.py inline branch returns the same
            bare Verdict.DELEGATED it does today. No behavior change for
            non-opted-in users. (Mirrors SPEC-01 FORGE_CANARY_RATE opt-out
            but inverted to opt-IN for the inline path's first release.)

   D-28-02  Canary generation = in-place SEMANTIC mutation of the real
            diff, NOT a synthetic appended file (that is SPEC-01's Outlet-A
            mechanism). Generate via the configured backend if one is
            reachable; otherwise fall back to a small built-in template
            library (reuse SPEC-01 sec 4 categories: hardcoded secret,
            None deref, off-by-one, SQL injection, resource leak, silent
            except). Each generated canary MUST be verified NON-EQUIVALENT
            before use (it must change behavior). An unverified/equivalent
            canary is DISCARDED, never planted -- an equivalent canary
            would unfairly fail a genuine reviewer. The non-equivalence
            VERIFY is pure logic over the mutation (testable without an
            LLM); the GENERATION may call an LLM behind an injected
            provider seam.

   D-28-03  N and threshold: plant N=3..5 verified canaries per review,
            gate threshold = catch >= ceil(0.6 * N) (so N=5 => >=3,
            N=3 => >=2). These are evaluate_canary_coverage's existing
            params; do not invent a second gate. If fewer than the minimum
            (2) verified canaries can be generated, the canary check is
            SKIPPED with an explicit notice (never silently passes, never
            hard-fails the user's review for a generator shortfall).

   D-28-04  Isolation: canaries are planted into an ISOLATED review COPY
            of the diff/working set. The real working tree is NEVER
            mutated, NEVER staged, git history NEVER touched (SPEC-01 sec
            9). After the round, only the FILTERED findings survive;
            mutated code is discarded. partition_canary_findings IS the
            strip -- no separate heavy SHA-provenance step is needed for
            the inline flow (M1 note).

   D-28-05  Fresh-context dispatch: the canary review runs as an
            INDEPENDENT pass with NO author narrative (anti-anchoring) --
            a cold `claude -p` / subprocess via llm_invoke, using the
            user's own model (zero extra config). This is an INDEPENDENT
            VERIFIER complementary to the hot-context deep review, NOT a
            replacement (see memory feedback_forge_review_inline_only:
            that rule forbids farming the 9-pass deep review to a cold
            subagent; the canary pass is a different thing -- an objective
            attention probe, so it is allowed and in fact required to be
            fresh-context).

   D-28-06  Gate consequence (honors D-16): on a canary MISS (caught <
            threshold), the inline review is declared UNRELIABLE for this
            round -- the verdict becomes a FAIL/UNRELIABLE signal with an
            infra notice naming the missed canaries. It MUST NOT switch
            outlet, MUST NOT switch model, MUST NOT auto-retry forever.
            On a canary PASS, the real (partitioned, cite-reverified)
            findings proceed to the verdict as normal.

   D-28-07  Backend-agnostic (honors D-25): generation + injection happen
            at the prompt/diff layer above llm_invoke. api and cli
            backends behave identically. No response-header or
            subprocess-env dependence.

   D-28-08  Anti-fabrication: the fresh-context reviewer's findings pass
            through reverify_finding_cites (M1) before the gate trusts
            them. A finding citing a nonexistent file/line is dropped as
            unverified. (Catches the reviewer that fabricates a plausible
            finding to look diligent.)

   D-28-09  Provider seam for testability: every LLM/subprocess call
            (generation, fresh-context review) goes through an INJECTED
            callable (dependency-injection, same pattern as machine.py
            l1_provider default no-op lambda). This lets the bulk of M2 be
            unit-tested with a stub provider; only a thin smoke test hits a
            real model. NO real network call in the unit suite.

5. SCOPE BOUNDARY (what Phase 28 is NOT)
   - NOT the Outlet-A / StateMachine canary (that is SPEC-01, separate).
   - NOT canary for L0 deterministic parsers (SPEC-01 deferred item 8).
   - NOT adaptive difficulty, telemetry dashboard, custom gate.yaml
     templates beyond the opt-in block, or cross-language template
     library (SPEC-01 deferred items 2-6). Python defects only for v1.
   - NOT a change to outlet selection logic (D-16). Canary reads the
     already-resolved inline outlet; it never reselects.

6. DELIVERABLES (the planner turns these into PLAN waves)
   a. canary generation module (backed by injected provider; built-in
      template fallback) + non-equivalence verify (pure logic, TDD).
   b. isolated-copy injection (prompt/diff-level; never touches tree).
   c. fresh-context review dispatch via llm_invoke (injected seam).
   d. inline-outlet wiring in cli.py behind --canary / gate.yaml canary:
      block (opt-in; default path unchanged; honor D-16 consequence).
   e. SPEC-01 supersede/extends note (append, preserve anchor fidelity).
   f. docs: a short "canary on the inline outlet" section in the manual
      (docs/manual.md exists) + configuration.md canary: block reference.
   g. tests: unit (stub provider, >=80% on new code) + one real-model
      smoke (gated, not in the unit suite).

7. GRAY AREAS (the ONLY open questions for the planner)
   - (a) gate.yaml `canary:` schema: minimal surface. Proposal: enabled
     bool, n int (default 5), threshold_ratio float (default 0.6),
     templates optional. Planner finalizes field names + defaults +
     validation, consistent with existing gate.yaml loader.
   - (b) Generation prompt + non-equivalence verification strategy: how to
     cheaply confirm a generated mutation is behavior-changing without a
     full test run. Proposal: structural check (the mutation must alter a
     control-flow / data-flow token, not a comment/whitespace) + optional
     test-kill when a fast test exists. Planner picks the concrete bar.
   - (c) Where the FAIL/UNRELIABLE consequence surfaces: a new Verdict
     member vs reuse of an existing one + infra_error. Verify state.py
     Verdict members before deciding; honor exit-code uniqueness.
   - (d) Wiring strategy in cli.py: wrapper around the inline branch vs a
     dedicated run_inline_canary() helper. Keep the default branch
     readable; prefer a helper that the branch calls only when opted in.

8. ACCEPTANCE (main session re-verifies before declaring phase done)
   1. With NO opt-in, `code-forge review --outlet inline` is byte-for-byte
      unchanged from today (same DELEGATED, same exit 5).
   2. With --canary, a rubber-stamp reviewer (empty findings) is gated
      FAIL/UNRELIABLE; a genuine reviewer that flags the planted defects
      passes; the planted defects never appear in the user-facing findings.
   3. No canary code is ever written to the working tree or git history.
   4. Canary result never alters outlet/model selection (D-16).
   5. Unit suite (stub provider) >=80% on new code; real-model smoke
      passes; full Step-0 (syntax/lint/non-ASCII) clean; three-cycle
      review + smoke complete before any commit.

9. SESSION HANDOFF NOTES (updated 2026-06-24, post-Phase 27)

   BRANCH STRATEGY (CHANGED from original):
   - M1 branch forge/near-perfect-inline @ c515db7 is 46 commits behind
     current main (f359a36). The gap is Phase 24.1 through 27 -- work
     that landed on main independently with different SHAs.
   - The M1-unique commit (c515db7) is PURE ADDITIVE: 6 new files,
     614 insertions, 0 modifications. None of these files exist on main.
   - STRATEGY: cherry-pick c515db7 onto a new feature branch from current
     main. Do NOT rebase the full forge/near-perfect-inline branch (the
     Phase 24.1 duplicates would cause merge conflicts for no value).
     Sequence:
       1. git checkout -b feat/phase28-canary-inline main
       2. git cherry-pick c515db7
       3. Verify 31 M1 tests pass on the rebased base
       4. git worktree add .worktrees/phase28 feat/phase28-canary-inline
       5. M2 work proceeds in the worktree
   - The old forge/near-perfect-inline branch is RETIRED after cherry-pick
     verification (delete local + remote).

   WORKTREE:
   - .worktrees/near-perfect-review does NOT exist (cleaned up).
   - Create .worktrees/phase28 on the new feature branch (step 4 above).

   EXIT CODE MAP (for gray area c -- FAIL/UNRELIABLE verdict):
   - 0=PASS, 1=FAIL, 2=CLI_ERROR, 3=BUSY, 5=DELEGATED, 6=TIMEOUT
   - Codes 4 and 7 are available. Planner picks one for UNRELIABLE.

   MAIN STATE (Phase 27 complete):
   - main @ f359a36 (11 Phase 27 commits, 29 tests, pushed to origin)
   - AdvisoryFinding pattern established (cross_repo_impact.py as reference)
   - CrossRepoImpactRunner wired into primary advisory_runners list
   - Full test suite: all passing

   STALE LOCAL BRANCHES (user should clean before M2):
   - feat/relpath-display, feat/relpath-regression-test (Phase 27 leftovers)
   - phase27-dispatch-evidence (Phase 27 dispatch)
   - worktree-agent-* (orphaned worktree branches)

   Tests: run TARGETED only (PYTHONPATH=src python -m pytest
     tests/test_canary.py ...). NEVER full pytest in a forge worktree
     (pollutes the real .git; see Phase 18.1).
   - Design memory: project_forge_near_perfect_inline_review (full design
     + prior-art + evidence URLs + the 3-spike empirical findings). Read it
     before planning M2.
   - Canary calibration spike (READY FIXTURES): spikes/canary_fence/ on the
     OLD branch (committed @ 5d9b1dc) has planted-defect fixtures + a run
     protocol (genuine vs overloaded haiku, feed findings to the gate,
     separation = genuine PASS / overloaded FAIL). EMPIRICAL FINDING that
     reshapes 6a/7b: the gate logic is trivial; the binding risk is canary
     GENERATION. A canary must be (1) non-equivalent AND (2) subtle with NO
     LOCAL TELL -- a docstring/comment stating the violated contract next to
     the bug makes it catchable even under overload (proven: ledger.py both
     caught). Operator flips are too trivial (widget.py both caught). And
     N=1 signal is variance-dominated (the same pagination bug was missed
     once, caught once), so use SEVERAL canaries. Validate any generated
     canary set against the spike protocol BEFORE trusting it in the inline
     gate. NOTE: cherry-pick the spike fixtures from the old branch if they
     are not included in c515db7 (check git show --stat 5d9b1dc).
   - Commit discipline: forge commit rules (WHY-not-WHAT, no plan-refs,
     no severity labels, no review vocabulary), Signed-off-by Minxi Hou
     <houminxi@gmail.com>, marker at command end outside quotes.
