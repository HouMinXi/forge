# Roadmap: Forge

## Milestones

- [x] **v2.0 Foundation** -- Phases 0-11 (shipped 2026-05-20)
- [x] **v2.1 Dynamic Gate** -- Phase R1/R2/R3 gates (shipped 2026-05-27)
- [x] **v2.2 Path A** -- Phases 4B-11 outlet/backend/hardening (shipped 2026-06-04)
- [x] **v2.3 Backend Wiring + Anti-Shirk** -- Phases 12-16 (shipped 2026-06-09)
- [x] **v2.4 Honest Green** -- Phases 17-23 (shipped 2026-06-15)
- [x] **v2.5 Releasable + Cross-Repo** -- Phases 24-29 (shipped 2026-06-26)
- [x] **v2.6 Adoption** -- Phases 30-33: Switch-On+Dogfood / CN Robustness / Contract / MCP (shipped 2026-06-29)
- [x] **v2.7 Provider Capability** -- Phases 34-36: Provider params / SSE / MCP sampling / Usability hardening (shipped 2026-07-01)
- [ ] **v2.8 Onboarding + Throughput** -- Phases 37-42: User config / setup-mcp / stale-process / parallelization / convergence / design intent / router compat
- [ ] **v2.9 ENV-GROUNDING** -- Phase 44 + epistemics lane 51/52/53a/53b: eval-on-duty / basis-disclose / env-manifest / exec-falsify (native + container opt-in)
- [ ] **Review-pipeline self-attestation gaps (Phase 43.1)** -- charter ratified 2026-08-08, placed in v2.8 tail. 10 items + process change, 3 execution chains. Does not overlap v2.9 or the v3.x sketch lane. See .planning/charter_review_pipeline_gaps.md and .planning/phases/43.1-review-pipeline-gaps/.

## Phases

<details>
<summary>v2.8 Onboarding + Throughput (Phases 37-42) -- IN PROGRESS</summary>

Pre-phase work (merged to main before formal GSD tracking):
- [x] Phase 37: User-level config + $HOME walkup defuse (D1-D5, ADR-0009) -- merged 2026-07-03 (6fb427e)
- [x] Phase 37.1: F5 backend passthrough + F1 truncation retryable -- merged 2026-07-04 (965c247)

Tracked phases:
- [x] Phase 38: setup-mcp -- one-command MCP onboarding -- merged 2026-07-03 (07d0381)
- [x] Phase 38.1: Stale-process/workspace guard (P5) -- merged 2026-07-04 (0a85662)
- [x] Phase 38.1-5/6: Contract heading matcher + duration inflation fix -- merged 2026-07-09 (14b3985)
- [x] Phase 38.2: PDEATHSIG orphan guard -- merged 2026-07-04 (9f96fd5)
- [x] Phase 43: LEDGER append-only outcome record -- merged 2026-07-04 (14328bb)
- [x] Phase 39: L1 pass parallelization -- 3x wall-clock via ThreadPoolExecutor/gather, CLI serial guard, fixed-order fold -- merged 2026-07-05 (6abb6fb)
- [x] Phase 38.3: MCP out-of-box UX -- workspace from client roots (T1), api_key_file credential (T2), findings visibility (T3) -- LANDED IN MAIN via the usability-onramp + surflare-consumer-pain work, NOT via mcp-oobe. Ground truth: roots resolution mcp_server.py:173-216 (T1), api_key_file backend.py:89/309-326/636 (T2), _truncate limit<4 guard mcp_server.py:568. mcp-oobe branch superseded + deleted; 98f1ddd/6bf1682 dangling duplicates (safe to GC). Reconciled 2026-07-08.
- [x] Usability on-ramp batch (db4b51c..89a091f) -- getting-started walkthrough, actionable gate-check YAML snippet, first-time receipt-gate guidance, doctor self-consistency, infra-count in summary, "Dangerous"->"Sensitive" reword -- merged 2026-07-07 (89a091f). PM L4 verified (round-trip test caught a real snippet indent bug, fixed).
- [x] surflare consumer-pain fixes (89a091f..e50b375) -- MCP project_dir param on all 5 workspace tools, API timeout cap 600s, error messages name backend not format, advisory findings surfaced in SARIF properties + summary line -- merged 2026-07-08 (e50b375). PM L4 verified over 4 rounds; W3 hollow-test + W4 emit-seam lock closed with proven teeth.
- [x] Phase 40: Honest partial results + convergence (mechanical half) -- merged 2026-07-16 (25b063e; dd6d40f is the pre-rebase orphan, see 40-SUMMARY.md). Landed: PassOutcome enum + derive_pass_outcomes (state.py), passes=N/M suffix in format_summary (sarif.py), pass_status in receipt JSON (receipt.py, backward-compat), large-diff file-based chunking with fingerprint dedup (outlet_c.py). 31 new tests, full suite 2798/0 at merge. Deferred to the semantic half: convergence plateau (7.2), prior-round memory (7.3), cross-file findings under chunking, chunk bin-packing, single-file hunk-based fallback, per-pass retry.
- [x] Phase 41: Review focus -- design-intent header rename (3 builders: cli.py:786, factories.py:282/584) + review-focus emphasis param (gate.yaml review_focus + --focus FILE + MCP focus, merged into a "## Review Focus" section on all 3 builders with its own trust hash independent of backend trust) + git-blame committer date -- merged 2026-07-25 (74adbf2, fast-forward, 7 commits on ca0d860, +565/-60, new tests/test_focus.py). PM-verified independently: full suite 2903/8/0 on the real main path (582s); per-site bug-injection on all 3 header sites, each proven caught (cli.py:786 + factories.py:282 -> test_contract_wiring; factories.py:584 -> test_mcp_server); post-trust-edit adversary proved focus trust independent of backend trust; degenerate --focus (empty/missing) graceful. F1 (plan-ref "D5.6" in 3 comments + 1 commit body, missed by the repo's own [Dd]-[0-9] self-check for lack of a hyphen) fixed pre-merge via rebase; range-diff confirmed comment/message-only, zero logic touched. Acceptance: 41-review-focus/41-ACCEPTANCE.md + cp-artifacts/cp3-impl-pm-verification.md. NUMBER COLLISION (2026-07-23): the sibling .planning/phases/41-sampling-fix was split out and merged earlier as part of 89bdb4d -- 2edb9d4 (wire contract_spec through sampling dispatch = the D5.7 gap) + 5c8e001 (centralize CLI dispatch, close tmpfile leak); that portion was out-of-milestone and explicitly NOT rebuilt by this phase (verified as ancestors; diff did not re-touch their hunks). Both dirs kept.
- [x] Phase 42: CLI key fast-fail (F8) + claim_type oracle (7.1)
  **Plans:** 2 plans (Wave 1, parallel)
  Plans:
  - [ ] 42-01-PLAN.md -- Extend fast-fail guard to api_key_file + vertex
  - [ ] 42-02-PLAN.md -- claim_type oracle: derive_claim_type + wire into ledger
- [ ] Router onboarding compat (unplanned, evidence-driven) -- scheduled 2026-07-25 after Phase 42, from the OmniRoute/gemini default-router RCA. PM-verified all 5 findings vs main @ 74adbf2 (triage + evidence: reports/router-friction-triage-20260725.md). CODE: F1 LANDED 2026-07-30 (695f739) BY A DIFFERENT MECHANISM THAN SCOPED -- a47d888 sends the stream flag explicitly (llm_invoke.py:152 `body["stream"] = bool(backend.stream)`), so a router that picks its own default when the field is absent never picks SSE. Prevention at the request, not tolerance at the parse. PM-verified on the real path against OmniRoute/onmi-gemini3.6, with bug-injection at the fix site reproducing the exact error and a byte-identical restore. STILL DEFERRED, and the reason to keep this entry open: the three non-streaming parse sites are untouched, so a router that returns SSE despite being told not to still fails. If that router appears, the original scope is still the right fix -- SSE auto-detect in the SHARED _parse_response_body (3 non-streaming parse sites; a one-site fix leaves 2 siblings broken). Re-grepped 2026-08-09 at main c72ff06: the function is llm_invoke.py:1135, call sites :1197 openai / :1295 anthropic / :1469 vertex. The pre-08-09 record read 1023/1121/1295 -- do not reuse it, and note WHY it is worse than a plain stale number: 1295 is in both lists and names a different provider in each (vertex then, anthropic now), so it still resolves to a real call of the same function and reads as correct. Re-grep again rather than trusting these; F3 trust prints resolved gate.yaml path + warns when cwd is not a project (cli.py:1112, follow ADR-0009 $HOME policy, do not reinvent); F4 LIVE backend probe extending doctor/_probe_api (currently env-check-only, backend.py:558-562). F4's "doubles as F1's acceptance test" rationale is now spent -- F1 shipped with its own real-path acceptance check, so F4 must justify itself on debug-loop value alone or shrink. DOCS: F2 base_url /v1 semantics in gate.schema.json; F5 point users at the existing ~/.config/code-forge/config.yaml inheritance. NOT BUILT (verified already ships): F5 user-level backend inheritance exists via _merge_user_into (cli.py:2303/1046/3321, Phase 37.1); F4 is not a from-scratch command (doctor already probes).
- [x] Phase 45: Multi-language support -- merged 2026-07-10 (c0f2b3d, 11 commits, ff). Landed end-to-end: Go, C/C++ (cppcheck stderr-swap), Java (PMD positional args), JS/TS (ESLint json + parse_eslint); deferred with recorded blockers + Phase 47+ multi-language upgrade paths: C# (45-04), Ruby (45-07), Swift (45-08), PHP (45-09). Also: ALL_REGISTRIES refactor, MCP allow_main per-call env, SARIF trailing-noise tolerance, L0 crash guard, _resolve_command first-word fix (ended main's flake8-only L0 false-green -- memory feedback_resolve_command_false_green.md). Full suite 2695/7/0.
- [x] Phase 46: doctor registry-vs-executed tool audit -- merged 2026-07-11 (main a18844a; worktree SHA was f53bf84, patch-id identical). Closes the resolve-false-green class via `forge doctor` tool-audit rows. Detail section below.
- [x] Phase 47: invoke-error-visibility -- merged 2026-07-23 (ca0d860, fast-forward, 2 commits on 89bdb4d). API-path + CLI-path LLMInvokeError diagnostic surfaced in str(exc) (compute diag once, interpolate into message). Bug-injection proof at both sites. Full suite 2882/8/0 (passed/skipped/failed; PM-verified independent run, 423.90s). Forge review (deepseek, 3 internal passes, one warning dismissed on ground truth). Real-path smoke: real subprocess emits non-JSON stdout, diagnostic confirmed in str(exc).
- [x] Windows MCP support wave 1 (unplanned, evidence-driven) -- merged 2026-07-11 (4b060bd). lifespan add_signal_handler guarded with try/except NotImplementedError; unguarded call killed MCP server + doctor self-check + CLI review on Windows at startup. Verified on gpu-win: T1b initialize exit 0, doctor exit 0, T4 real review E2E (H:\forge-test\t4_review_summary.txt). Wave 2 backlog (evidence-gated): lock.py os.kill probe kills live holder on Windows (G1 HIGH -- never run two forge processes on one workspace there), graph_triage cp1252 encoding (G2), llm_invoke killpg cli-outlet (G3). Memory: project_forge_windows_support.md.

</details>

<details>
<summary>v2.9 ENV-GROUNDING (Phase 44 + lane 51/52/53a/53b) -- IN PROGRESS (Phase 48 shipped 2026-08-16; 44/51/52/53a/53b remain planned)</summary>

Source: v2.9-V3-GROUNDTRUTH-SCHEDULE.md AMENDMENT 1 (rev 2). Externally
certified: ds+lc adversarial review, R2 double 0/0/0/0, ledger at
dispatch/forge-env-r1-adjudication.txt. Data-plane epistemics lane, PARALLEL to
the learning-loop flywheel (sketched as 45-49; numbering stale, see the
v3.x sketch collision note below); does NOT change the 40 -> 41 -> 42
in-flight queue.

- [ ] Phase 44: EVAL-ON-DUTY -- case generation re-extracts diffs from the
  LEDGER (prereq Phase 43, merged 14328bb); ~300-450 LOC. Root of the v2.9 lane.
- [x] Phase 48: LLM stream TTFT + truncation continuation -- stream-mode
  passes get a first-token progress event (TTFT visibility, Codex-style);
  finish_reason=length is detected as truncation (never normal completion)
  and recovered by bounded continuation (partial JSON preserved, Claude
  Code query.ts layered-recovery model, OpenCode doom-loop guards).
  User order 2026-08-16. Prereq: none (llm_invoke-local). Design:
  todos/pending/stream-ttft-truncation-continuation-20260816.md.
  MERGED 2026-08-16 (59c1c51, 7 commits). First v2.9 lane phase to
  land; six follow-ups in phases/phase-48/48-FOLLOWUPS.md.
- [ ] Phase 51: BASIS-DISCLOSE -- add falsification_survived + convergence_rounds
  sub-fields to the basis (pipeline already computes both). Prereq: Phase 43
  (provenance). No prompt change. Pull-forward to post-43 PERMITTED (ledger Q1);
  the post-44 slot is queue hygiene, not a dependency.
- [ ] Phase 52: ENV-MANIFEST -- manifest tiers (declared > observed > absent;
  absent is a first-class verdict-header state) + version-sensitivity trigger
  (claim_type attribute OR symbol absent from the declared-version set; never
  model-self-reported) + distinct SARIF level for capped findings. Prereq:
  Phase 44 + 51. IS a prompt change.
  Plan-author carry-forward (NOTED, ledger R2): when manifest state is absent,
  the second version-sensitivity condition is vacuously false -> only the
  claim_type attribute triggers; already covered by the verdict-header
  absent-state disclosure.
- [ ] Phase 53a: EXEC-FALSIFY v1 -- native venv/build-tree subprocess,
  synchronous-with-budget (timeout degrades to an explicit
  exec-evidence-unavailable disclosure). Reviewed-diff exec + fail-before are
  verdict inputs; pass-after is receipt-level ONLY (a passing fix must not
  confirm a finding, a failing fix must not demote one). Kernel C delegated to
  Beaker. Prereq: Phase 52 only; ~100-200 LOC.
- [ ] Phase 53b: EXEC-FALSIFY v2 -- container + driver surface, opt-in, bought
  only on demonstrated need. Shares falsify_real.py with Phase 50 and inherits
  its hard boundary. Prereq: Phase 53a + Phase 50 charter.

Dependency graph:

    44 -> 51 -> 52 -> 53a -> 53b
                             +-- 53b also requires the Phase 50 charter

Fix-delivery constitution (three authorities): diagnose / propose / apply. A fix
proposal ships only with fail-before/pass-after evidence produced in the
declared environment (Phase 53a). DELIVERY IS a patch ARTIFACT (a diff in the
receipt/SARIF or the forge artifact dir) + evidence bundle -- NEVER applied
state; landing power stays with the caller. forge never writes the reviewed
tree's CODE (.code-forge/ is carved out). AutoFixer stays a stub-for-delivery
until 53a; the 53a execution organ runs code but never drafts, and a delivering
AutoFixer (post-53a, if ever) consumes the organ (one-way dependency).

v3.x sketch (NOT planned, prereqs pinned in schedule, not externally certified):
Phase 45 ESCAPE intake / 46 SYNTHESIS / 47 REGISTRY promotion / 48 SCOUT+adapter
/ 49 COMPILATION mining (the learning-loop lane) + Phase 50 agent driver
surface. Schedule-only until firmed. NUMBER COLLISION (2026-07-10): the
real Phase 45 is Multi-language (in flight) and 46 is earmarked for its
upgrade path; these sketch numbers predate that and are stale labels --
read as sketch-L1..L6, renumber when the lane is firmed.

</details>

<details>
<summary>v2.0 Foundation (Phases 0-11) -- SHIPPED 2026-05-20</summary>

See .planning/milestones/ for archived phase details.

</details>

<details>
<summary>v2.1 Dynamic Gate -- SHIPPED 2026-05-27</summary>

See .planning/milestones/v2.1-dynamic-gate/ for archived phase details.

</details>

<details>
<summary>v2.2 Path A -- SHIPPED 2026-06-04</summary>

See .planning/milestones/v2.2-ROADMAP.md for archived phase details.

</details>

<details>
<summary>v2.3 Backend Wiring + Anti-Shirk (Phases 12-16) -- SHIPPED 2026-06-09</summary>

- [x] Phase 12: Backend API Wiring (4 plans) -- completed 2026-06-04
- [x] Phase 13: Backend Dogfood Verification (2 plans) -- completed 2026-06-05
- [x] Phase 13.1: Root-fix: Vertex backend + outlet ergonomics (5 plans) -- completed 2026-06-06
- [x] Phase 14: Outlet C Receipt Gap + Verify Hardening (3 plans) -- completed 2026-06-07
- [x] Phase 15: Reviewer Independence (2 plans) -- completed 2026-06-08
- [x] Phase 16: Relief Mechanisms (3 plans) -- completed 2026-06-09

See .planning/milestones/v2.3-ROADMAP.md for full phase details.

</details>

<details>
<summary>v2.4 Honest Green (Phases 17-23) -- SHIPPED 2026-06-15</summary>

- [x] Phase 17: Trust Gate + Eval Scaffold -- completed 2026-06-10
- [x] Phase 18: Taint + Provenance -- completed 2026-06-11
- [x] Phase 18.1: Test Isolation + R1 Re-arm (INSERTED, P0) -- completed 2026-06-11
- [x] Phase 19: Fix Validation -- completed 2026-06-12
- [x] Phase 19.1: Presubmit Gate Hardening (INSERTED) -- completed 2026-06-12
- [x] Phase 20: Verdict Honesty -- completed 2026-06-12
- [x] Phase 21: Legacy + Intent -- completed 2026-06-13
- [x] Phase 22: Graph Triage -- completed 2026-06-14
- [x] Phase 23: Daemon State -- completed 2026-06-15

See Phase Details section below for full criteria.

</details>

<details>
<summary>v2.5 Releasable + Cross-Repo (Phases 24-29) -- SHIPPED 2026-06-26</summary>

- [x] **Phase 24: Config Legibility** - gate.yaml self-documents via inline comments + gate.schema.json; corpus round-trip test proves schema/loader agreement (parallel-ok) -- completed 2026-06-15
- [x] **Phase 24.1: Outlet Alignment** (INSERTED) - A/B/C outlets run same flow contract: C gets real legs (registry+falsifier+5 advisories), tiered reset lands in machine.py, inline gets DELEGATED marker, single-source drift guard enforced -- completed 2026-06-16
- [x] **Phase 25: Cross-Repo Merge Review** - a logical change spanning >=2 repos is reviewed as one joint unit with both diffs in a single context -- completed 2026-06-20
- [x] **Phase 25.1: Backend Robustness** (INSERTED) - consecutive-L1-timeout circuit breaker (exit 6), L1 truncation false-green coverage guard, and backend/troubleshooting docs -- completed 2026-06-21
- [x] **Phase 26: Cross-Repo Contract Context** - opt-in recipe pulls sibling repo spec into reviewer context as read-only reference -- completed 2026-06-21
- [x] **Phase 27: Cross-Repo Impact via register** - advisory finding surfaces sibling call sites when a symbol changes, via code-review-graph register -- completed 2026-06-24

## Phase Details

### Phase 17: Trust Gate + Eval Scaffold

**Goal**: Repo-supplied config cannot exfiltrate credentials; eval scorecard exists to measure each axis as it ships
**Depends on**: Phase 16 (v2.3 complete)
**Requirements**: SEC-01, EVAL-01
**Success Criteria** (what must be TRUE):

  1. Repo-supplied gate.yaml backends are NOT used without explicit user opt-in (direnv-style trust file or flag)
  2. A hostile gate.yaml fixture (base_url pointing to attacker endpoint + api_key_env set to a real env var) does NOT exfiltrate when `code-forge review` runs in the cloned repo
  3. Eval corpus contains at least the named real buggy/fixed pairs (E1-E6, gate.yaml RCE, BUG-P12-01, ttl_class)
  4. Eval harness drives a real backend (never mocks) and computes false-green rate per backend
  5. Scorecard output is human-readable (table or structured report, not raw JSON)

**Plans**: 4 plans
Plans:

- [x] 17-01-PLAN.md -- Trust gate module + AdvisoryFinding/AxisRunner types (TDD)
- [x] 17-02-PLAN.md -- Trust CLI integration + machine.py advisory wiring
- [x] 17-03-PLAN.md -- Eval scaffold core (corpus loader + scorer + runner)
- [x] 17-04-PLAN.md -- Eval CLI subcommand + full suite regression

### Phase 18: Taint + Provenance

**Goal**: Config-to-sink data flows are flagged; reviewers ask who controls each external input
**Depends on**: Phase 17 (trust gate landed -- taint extends it)
**Requirements**: REVIEW-TRUST-01
**Success Criteria** (what must be TRUE):

  1. Danger-score fires on a gate.yaml fixture containing base_url/api_key_file/shell fields from an untrusted repo-local config
  2. Semgrep taint rule detects config/file/env source flowing into subprocess/shell/urlopen/network sink (open write-mode sink deferred per D-12 self-loop constraint)
  3. When semgrep is absent, the taint gate loud-fails (logs a clear warning, never silently skips)
  4. Adversarial provenance question ("for each external input, who controls the source, worst attacker value?") appears in the adversarial review pass
  5. Regression fixture (gate.yaml exfil pattern from Phase 17) stays caught after taint integration

**Plans**: 2 plans
Plans:

- [x] 18-01-PLAN.md -- Taint module: danger_score_from_diff + TaintRunner (TDD)
- [x] 18-02-PLAN.md -- Pipeline wiring + provenance prompt + taint rule file

### Phase 18.1: Test Isolation + R1 Re-arm (INSERTED, P0)

**Goal**: forge's own test suite can never touch the real repo's .git, and the R1 pre-commit gate is re-armed without deadlocking non-code commits
**Depends on**: Phase 18
**Requirements**: infra integrity (no REVIEW-* axis)
**Context (2026-06-11 incident)**: running the suite polluted the real repo: core.hooksPath=/custom/hooks (ALL git hooks dead, including the .planning pre-push guard on a public repo), user identity overwritten to Test/test@test.com (misattributed commits), stub hooks written into .git/hooks/, 4 fixture commits stacked onto live branch forge/p18-ai-smell. Prime suspect tests/test_install_hooks.py (/custom/hooks appears only there) -- must be reproduced before fixing. Manual cleanup done 2026-06-11; root cause remains.
**Success Criteria** (what must be TRUE):

  1. Pollution reproduced in a scratch clone and the mechanism documented (which test, which code path writes outside tmp)
  2. Full `pytest -q` leaves the real .git untouched: config, hooks dir, and all branch refs identical before/after, asserted by a session-level regression guard that fails loudly on drift
  3. Generated pre-commit passes a docs/config-only commit without --no-verify (non-code carve-out) while a code commit still runs verify + gate-check
  4. R1 re-armed end-to-end: install-hooks succeeds, a code commit triggers the suite and blocks on a NEW failure, and the .planning pre-push guard stays live
  5. Hard ordering: isolation fix (SC 1-2) lands BEFORE the hook is armed (SC 4) -- the armed hook runs the full suite on every commit

**Plans:** 2 plans

Plans:

- [x] 18.1-01-PLAN.md -- Test isolation: GIT_CEILING_DIRECTORIES fixture + per-test env + regression guard
- [x] 18.1-02-PLAN.md -- Non-code carve-out in generate_hook_content + R1 end-to-end test

### Phase 19: Fix Validation

**Goal**: Bug-fix diffs prove their tests are not hollow by demonstrating RED on revert and GREEN on restore
**Depends on**: Phase 17 (eval scaffold scores FIXVAL axis)
  (EVAL-CORPUS-REPAIR resolved 2026-06-10 -- BUG-P12-01 now REPLAYs)
**Requirements**: REVIEW-FIXVAL-01
**Success Criteria** (what must be TRUE):

  1. For a bug-fix fixture: reverting the non-test hunk makes the new test go RED; restoring makes it GREEN
  2. STING overfit guard runs at least one behavior-preserving transform on the new test and the test still passes
  3. Written waiver path exists for nondeterministic bugs (explicit opt-out, never silent-skip)
  4. FIXVAL blocks only the diff's own hollow test (advisory=false, can block the cycle)
  5. Eval scorecard records FIXVAL axis results (false-green rate on BUG-P12-01 fixture)

**Plans**: 2 plans
Plans:

- [x] 19-01-PLAN.md -- FIXVAL core module: structural trigger, revert mutant, overfit guard, waiver (TDD)
- [x] 19-02-PLAN.md -- Pipeline wiring into machine.py + StateFinding extension + integration tests

### Phase 19.1: Presubmit Gate Hardening (INSERTED)

**Goal:** Ship a generic presubmit external-linter framework in forge: gate.yaml
`presubmit:` section drives the generated pre-commit hook to RE-RUN configured
linters (fail-closed on missing binary or error); built-in non-ASCII + AI-vocab
check always runs on staged diff; non-endorsed doc-snippet presets for common
linters. Kernel-specific pieces (checkpatch instance, vng receipt) are personal
dotfiles, outside this phase.
**Requirements**: PRESUBMIT-SCHEMA, PRESUBMIT-RUNNER, BUILTIN-D12, PRESUBMIT-PRESETS, NONCODE-CARVEOUT, SKILLMD-FIX, DNN-STRIP
**Depends on:** Phase 19
**Success Criteria** (what must be TRUE):

  1. gate.yaml `presubmit:` section parsed and validated (command, applies_to, on, when_exists); shell metacharacters rejected
  2. Generated pre-commit hook iterates presubmit entries and runs each linter; missing binary or error = FAIL (never silent pass)
  3. Built-in non-ASCII + AI-vocab check runs on staged diff for every code commit (no gate.yaml entry needed)
  4. Non-code commits (`# docs/chore/wip`) skip presubmit linters via existing carve-out
  5. docs/presubmit-presets.md contains non-endorsed snippets for checkpatch, go-vet, clippy, eslint
  6. SKILL.md line 176 narrowed to "code, comments, and commit messages"
  7. test_fixval.py has zero D-NN plan-reference labels

**Plans:** 3 plans

Plans:

- [x] 19.1-01-PLAN.md -- Presubmit schema validation in gate_check.py + SKILL.md fix + D-NN strip
- [x] 19.1-02-PLAN.md -- Presubmit runner + D-12 built-in in install_hooks.py
- [x] 19.1-03-PLAN.md -- Non-endorsed presubmit preset documentation

### Phase 20: Verdict Honesty

**Goal**: The forge verdict declares the runtime surface it did NOT verify, making green honest
**Depends on**: Phase 17 (eval scaffold scores RUNTIME axis)
  (EVAL-CORPUS-REPAIR resolved 2026-06-10 -- E1-E6 now REPLAY)
**Requirements**: REVIEW-RUNTIME-01
**Success Criteria** (what must be TRUE):

  1. A simulated smoke that cannot run real tests reports UNVERIFIED, never PASS
  2. At least one lifecycle/side-effect question is wired into the review prompt for every review
  3. The verdict output includes a "not verified" section listing the runtime surfaces forge did not exercise
  4. RUNTIME-01 is strictly advisory -- it never blocks a cycle, never gates a commit
  5. Eval scorecard records RUNTIME axis results (E1-E6 escape detection rate)

**Plans**: 3 plans
Plans:
**Wave 1**

- [x] 20-01-PLAN.md -- RuntimeRunner core module + smoke receipt infrastructure (TDD)

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 20-02-PLAN.md -- CLI smoke-run + machine.py wiring + SKILL.md mirror + drift test
- [x] 20-03-PLAN.md -- Eval corpus schema extension + RuntimeAxisHook + E1-E6 correction

### Phase 21: Legacy + Intent

**Goal**: Pre-existing issues in code the diff touches are surfaced (not dropped, not blocked) with blame attribution and intent classification
**Depends on**: Phase 20 (P0 axes complete; P1 begins)
**Requirements**: REVIEW-LEGACY-01, REVIEW-INTENT-01
**Success Criteria** (what must be TRUE):

  1. When forge finds an issue in unchanged code that the diff touches or depends on, it emits an ADVISORY finding tagged "pre-existing / inherited" with git-blame attribution (author + commit)
  2. Legacy findings are never auto-suppressed and never block a cycle
  3. Legacy detection reuses the R1 baseline primitive (NEW vs baseline delta)
  4. Intent discriminator classifies legacy findings as "intended" (workaround/SATD) vs "unintended" (bug) using commit/PR text as signal
  5. Intent labels never auto-suppress and never auto-block -- they annotate, nothing more

**Plans**: 3 plans
Plans:
**Wave 1**

- [x] 21-01-PLAN.md -- git_blame() porcelain parser in git.py + unit tests (TDD)

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 21-02-PLAN.md -- LegacyRunner advisory axis: filter_delta inversion + blame + intent (TDD)

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 21-03-PLAN.md -- machine.py registry injection + cli.py wiring + integration test

### Phase 22: Graph Triage

**Goal**: Opt-in system-level blast-radius ranking surfaces cross-file impact as advisory findings
**Depends on**: Phase 21 (P1 complete; P2 begins)
**Requirements**: REVIEW-SYSTEM-01
**Success Criteria** (what must be TRUE):

  1. sem-core (MIT/Apache license) is integrated; inspect-core (FSL) is NOT vendored
  2. Entity extraction produces a cross-file dependency graph for the changed files
  3. Blast-radius ranking orders changed entities by downstream impact count
  4. System-level findings are ADVISORY only -- never block, never auto-suppress
  5. Graph triage is opt-in (off by default; enabled via gate.yaml or CLI flag)

**Plans**: 2 plans
Plans:
**Wave 1**

- [x] 22-01-PLAN.md -- GraphTriageRunner core module + gate_check graph_triage validation + find_entity_dependents utility (TDD)

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 22-02-PLAN.md -- Pipeline wiring (cli.py + factories.py) + E-corpus entry E8

### Phase 23: Daemon State

**Goal**: Cross-subsystem state-conflict advisory axis detects when daemon/service code mutates shared external state (nftables marks, routing rules, locks) that another concurrently-active subsystem depends on
**Depends on**: Phase 21 (advisory infrastructure); Phase 22 NOT a hard dependency (grep-only substrate ships independently)
**Requirements**: REVIEW-STATE-01
**Success Criteria** (what must be TRUE):

  1. Daemon/service detection strategy activates the axis conditionally (not for stateless CLI/library code)
  2. State-compatibility matrix enumerates "subsystem A installs X, conflicts with anything needing Y"
  3. Three mechanical questions with exact wording cover: concurrent subsystems, mutual interference, gate-variable enumeration
  4. Context substrate pulls in the OTHER subsystem's state-mutating code (grep-based, graph optional enhancement)
  5. Advisory contract: never block, never suppress (same as all P2 axes)
  6. Scope honesty: ONE confirmed daemon consumer (surflare-watchdog); forge itself does not consume (stateless CLI); public demand unknown

**Plans**: 2 plans
Plans:
**Wave 1**

- [x] 23-01-PLAN.md -- DaemonStateRunner core module + RuntimeRunner.last_surfaces + gate_check daemon_state validation (TDD)

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 23-02-PLAN.md -- Pipeline wiring (machine.py + cli.py) + SKILL.md mirror + drift test + E8 eval corpus

### Phase 48: LLM stream TTFT + truncation continuation

**Goal:** Streaming passes emit a first-token progress event (TTFT visibility), and passes truncated by provider max_tokens caps are recovered by bounded continuation instead of dying on incomplete JSON.
**Requirements**: STREAM-VISIBLE, TRUNCATION-RECOVER
**Depends on:** none (llm_invoke-local)
**Type:** logic-bearing code -- TDD + forge review per house discipline
**Design:** .planning/todos/pending/stream-ttft-truncation-continuation-20260816.md
**User order:** 2026-08-16
**Note:** MERGED 2026-08-16 as 59c1c51 (7 commits, branch
fix/stream-ttft-continuation; branch deletion owed by user). Landed:
first-token progress emit in _read_sse, _TruncatedResponse carrier,
TruncationBreaker (threshold 5, sticky, monotonic), _continue_truncated
(budget 2, full-envelope requirement, fence-stripping, untrusted-data
instruction, two-level trip propagation). Plan D-1..D-11 + CP1b
amendments A-1..A-23 (exit: external unanimous zero findings); forge
code review 3 rounds with fix batches b2a7a3b / 7b0ddcf / 2d2c932.
Full suite 3415/9/0 at merge; deployed to yinhe-laptop and verified
live. Full record: .planning/phases/phase-48/ (SUMMARY.md,
EXIT-CHECKLIST.md, INJECTIONS.md, cp-artifacts/). Follow-ups:
phases/phase-48/48-FOLLOWUPS.md.

**Goal:** Add a tool-audit check to `forge doctor` that verifies every tool in the loaded registry actually resolves and runs in the pipeline, closing the resolve-command false-green class permanently.
**Requirements**: PREVENT-RESOLVE-FALSE-GREEN
**Depends on:** Phase 45
**Type:** logic-bearing code -- reviewed (CP3 waived by user; 12-pass inline + injection matrix)

Plans:
- [x] 6 tasks (Task 0-5): whitespace guard, _audit_tools, unit tests, blind-spot audit, integration test, smoke test update

**Note:** merged to main 2026-07-11 as a18844a (worktree commit was f53bf84 on feat/doctor-tool-audit; SHA changed on merge, patch-id verified identical 47d8c647, branch deleted). 4 files, +311/-3. 11 rounds plan review (R1-R11) converged 0/0/0/0 x3 models. Injection matrix (I1-I4) verified bidirectional. PM applied 4 acceptance fixes (F1 integration assertion, F2 plan-ref cleanup, F3 whitespace test, P3 docstring). Full suite 2710/7/0. Backend config: deleted mimo-pro from user backends (401 expired), unified on OmniRoute (review-route). Windows ground truth: tool-audit verified working on gpu-win 2026-07-11 (ruff 0.15.21 PASS, ghost not_installed).

---

### Phase 24: Config Legibility -- completed 2026-06-15

**Goal**: gate.yaml self-documents so any user or LLM can fill it correctly without reading source code
**Depends on**: Nothing (parallel-ok; independent of Phases 25-27)
**Requirements**: CONFIG-01, CONFIG-02
**Success Criteria** (what must be TRUE):

  1. A user can open gate.yaml and fill every field correctly from the inline comments alone, without consulting source code or external docs
  2. An IDE with yaml-language-server installed shows field hints, valid values, and descriptions on hover (driven by gate.schema.json schemaStore directive)
  3. gate.schema.json validates against JSON Schema draft-2020-12 without errors
  4. The corpus round-trip test passes: every valid snippet passes both schema validation and the real loader; every invalid snippet fails the loader (schema may pass on loader-only constraints, which are listed in $comment and tested against the loader separately)

**Plans**: 3 plans

- [x] 24-01-PLAN.md -- Self-documenting gate.yaml template + gate.schema.json (draft-2020-12) + purge fiction from prose files
- [x] 24-02-PLAN.md -- code-forge init writes gate.schema.json alongside gate.yaml (offline schema resolution)
- [x] 24-03-PLAN.md -- tests/test_schema_corpus.py corpus round-trip: every snippet validated against schema AND real loader

**UI hint**: no

### Phase 24.1: Outlet Alignment (INSERTED)

**Goal**: A/B/C review outlets execute the same flow contract; only L1 transport differs
**Depends on**: Phase 24 (decimal insert; does not renumber 25-27)
**Requirements**: None mapped (decimal insert)
**Success Criteria** (what must be TRUE):

  1. Same diff + same gate.yaml through Outlet A and Outlet C yields identical verdict
     and identical finding set (modulo L1 model nondeterminism); receipts present for both
  2. Outlet C run shows real L0 findings, advisory findings (5 axes), and L1 findings
     that passed a backend-backed falsifier; proven from artifacts not self-report
  3. Steady P2 finding stream converges under machine.py tiered reset (does not reset
     every round as under binary reset)
  4. Inline outlet returns Verdict.DELEGATED (exit 5), not Verdict.PASS (exit 0)
  5. Drift guard test fails when SKILL.md P3 thresholds and flow_contract.py constants disagree

**Plans**: 4 plans

Plans:
- [x] 24.1-01-PLAN.md -- Verdict.DELEGATED + EXIT_DELEGATED + inline honesty + outlet-alignment.md (Wave 0, gates v2.5) -- completed 2026-06-15
- [x] 24.1-02-PLAN.md -- Outlet C full-leg: real registry + backend falsifier + 5 advisory runners (Wave 1) -- completed 2026-06-15
- [x] 24.1-03-PLAN.md -- Tiered reset in machine.py: _FixpointResult + _severity_tier + _fixpoint_reached upgrade (Wave 2) -- completed 2026-06-16
- [x] 24.1-04-PLAN.md -- Single-source drift guard: flow_contract.py constants + SKILL.md drift test (Wave 2) -- completed 2026-06-16


### Phase 25: Cross-Repo Merge Review -- completed 2026-06-20

**Goal**: A single logical change that physically spans >=2 repos is reviewed as one joint unit with both diffs in a single review context
**Depends on**: Phase 24 recommended (schema covers cross-repo config); no hard gate
**Requirements**: CROSS-01
**Success Criteria** (what must be TRUE):

  1. Given two repos each with a baseline..head diff declared as siblings, forge produces exactly one review whose context contains both diffs, not two isolated single-repo reviews
  2. The joint verdict reflects findings from both repos; a finding in either repo can appear in the output
  3. A single-repo invocation (no sibling declared) produces output identical to pre-v2.5 behavior -- the single-repo path is unchanged

**Plans**: 7 plans

Plans:
**Wave 1** *(parallel)*

- [x] 25-01-PLAN.md -- gate.schema.json siblings: section + validate_siblings() in gate_check.py + corpus tests (TDD) -- merged 8953d54
- [x] 25-02-PLAN.md -- cross_repo.py: get_sibling_diff + build_cross_repo_context + make_per_repo_cwd (TDD) -- merged a191718

**Wave 2** *(blocked on Wave 1)*

- [x] 25-03-PLAN.md -- run_cross_repo() orchestrator: threading + per-repo cwd isolation + verdict merge + receipts -- merged 39c3a39

**Wave 3** *(blocked on Wave 2)*

- [x] 25-04-PLAN.md -- cli.py cross-repo dispatch wiring + single-repo zero-drift regression test -- merged a7c58d2 + 4d707e7 (fail-open fix)
- [x] 25-05-PLAN.md -- D-20 integration tests: two real tmp git repos end-to-end (sequential after 25-04, shared test file) -- merged 32bb8f6 + 4708691
- [x] 25-06-PLAN.md -- D-12 grouped verdict output + D-13 finding attribution via format_cross_repo_output() (sequential after 25-05, shared test file) -- merged f9bf18c
- [x] 25-07-PLAN.md -- CLI chore: remove deprecated --state-dir and --staged flags (sequential after 25-04, separable) -- merged af378f9

**UI hint**: no

### Phase 25.1: Backend Robustness -- completed 2026-06-21

**Milestone**: v2.5
**Requirements**: None (pre-req for reliable cross-repo; Bug 5 timeout circuit breaker blocks all review including cross-repo)
**Success Criteria**:

  1. Consecutive L1 timeout circuit breaker: N consecutive timeouts -> fail-fast with actionable error
  2. L1 truncation false-green guard: findings==0 + incomplete excerpt coverage -> INFRA finding (D-4, Option B)
  3. L1 truncation signal: stop_reason plumbed through LLMResult for adapter-routed calls (D-4, Option A)
  4. Docs batch: installation, URL, model guide, third-party usage, troubleshooting matrix
  5. Explicitly not done: Bug 4 (lock, no defect), Bug 7 (doctor subcommand, scope-challenge failed)

**Plans**: 3 plans

Plans:
**Wave 1** *(parallel)*

- [x] 25.1-01-PLAN.md -- D-1 timeout circuit breaker: is_timeout flag + EXIT_TIMEOUT + breaker counter + cli catch -- merged f56350b
- [x] 25.1-03-PLAN.md -- D-2 docs sweep: configuration.md Bugs 1/2/3/6/8 + troubleshooting table -- merged 6e34dbc

**Wave 2** *(blocked on Wave 1)*

- [x] 25.1-02-PLAN.md -- D-4 L1 truncation false-green guard: coverage backstop in factories.py -- merged 59ab53b

**Rationale**: Bug 5 blocks all review paths including cross-repo (N StateMachines multiply timeout probability). Cross-repo landing requires this as a practical pre-req.

**UI hint**: no

### Phase 26: Cross-Repo Contract Context -- completed 2026-06-21

**Goal**: An opt-in recipe injects a sibling repo's contract spec into reviewer context as a read-only reference without reviewing that sibling
**Depends on**: Phase 25 (resolve_sources convention from conventions_resolver.py is available; no hard gate -- can land before Phase 25)
**Requirements**: CROSS-02
**Success Criteria** (what must be TRUE):

  1. When reviewing a repo B diff with the kernel-spec opt-in declared, the kernel YNL spec appears in the reviewer context as a read-only reference (not reviewed, not gated)
  2. A declared spec whose path does not exist or is unreadable produces a graceful empty digest in context -- never an error, never a crash
  3. In a single-repo review with no opt-in declared, no spec appears in the reviewer context (opt-in only, never automatic)

**Plans**: 3 plans (replanned 2026-06-21 with 5-model cross-review findings)

Plans:
**Wave 1**

- [x] 26-01-PLAN.md -- contract_loader.py core module + trust extension with spec-content hashing (TDD)

**Wave 2** *(blocked on Wave 1)*

- [x] 26-02-PLAN.md -- Outlet A + Outlet C prompt injection wiring + trust CLI revoke/status

**Wave 3** *(blocked on Wave 1 + Wave 2)*

- [x] 26-03-PLAN.md -- Cross-repo threading + SC-1/SC-2/SC-3 integration tests + end-to-end CF-3
**UI hint**: no

### Phase 27: Cross-Repo Impact via register

**Goal**: An advisory finding surfaces sibling call sites when a changed symbol is used by a registered sibling repo, computed over a cross-repo graph
**Depends on**: Phase 25 (resolve_sources convention; no hard gate -- can land before Phase 25)
**Requirements**: CROSS-03
**Success Criteria** (what must be TRUE):

  1. With two repos registered in a cross-repo graph, changing a symbol used by the sibling repo surfaces an ADVISORY finding that names the sibling call site (file + line) and the changed symbol
  2. When the code-review-graph register/db is absent or the symbol is not found, the axis emits SKIP -- never a crash, never a silent pass
  3. The advisory contract holds: the cross-repo impact finding never blocks the verdict and never suppresses other findings from the pipeline

**Scope (discuss 2026-06-23):** v1 = S2, R0 direct-caller cross-repo impact only (reuse graph.db CALLS edges via the registry). R1/R1-rev + SCIP demand-gated to a second cut. See 27-CONTEXT.md.
**Plans**: 2 plans
Plans:
- [x] 27-01-PLAN.md -- CrossRepoImpactRunner R0 module + unit tests (SC-2 SKIP behavior)
- [x] 27-02-PLAN.md -- Wire into cross_repo.py + hermetic two-repo integration (SC-1, SC-3)
**UI hint**: no

### Phase 28: Reviewer Canary for the Inline Outlet

**Goal**: The inline review outlet gains an opt-in objective laziness check -- planted defects the reviewer cannot distinguish from real ones, gated on how many it catches, so a rubber-stamp is detectable instead of trusted
**Depends on**: M1 harness core already built on branch forge/near-perfect-inline @ c515db7 (canary.py / evidence.py / findings.py, 31 tests). Independent of Phases 25/26/27 (touches different files)
**Requirements**: SPEC-01 (inline variant; extends the locked Outlet-A spec)
**Success Criteria** (what must be TRUE):

  1. With no opt-in, `code-forge review --outlet inline` is byte-for-byte unchanged from today (same DELEGATED, same exit 5)
  2. With --canary opted in, a rubber-stamp reviewer (empty findings) is gated FAIL/UNRELIABLE; a genuine reviewer that flags the planted defects passes; the planted defects never appear in the user-facing findings
  3. No canary code is ever written to the working tree or git history; the canary result never alters outlet or model selection (D-16)

**Plans**: 4 plans
Plans:
**Wave 1a** *(no deps)*

- [x] 28-02-PLAN.md -- Verdict.UNRELIABLE + EXIT_UNRELIABLE=7 + gate.yaml canary: validation + init template (TDD)

**Wave 1b** *(depends on 28-02)*

- [x] 28-01-PLAN.md -- canary_gen.py core: template generation + non-equiv verify + injection + dispatch orchestrator (TDD)

**Wave 2** *(depends on Wave 1a + 1b)*

- [x] 28-03-PLAN.md -- CLI wiring: --canary flag + _load_canary_config + inline branch augmentation + integration tests
- [x] 28-04-PLAN.md -- SPEC-01 extends note + docs/configuration.md canary: block documentation

**Wave 3** *(depends on Wave 1a + 1b + Wave 2)*

- [x] 28-05-PLAN.md -- Gated real-model smoke (mimo-pro): spike-protocol discrimination test + end-to-end run_inline_canary smoke (FORGE_SMOKE_MIMO=1, never in CI)

**UI hint**: no

### Phase 29: Dead-Code False-Positive Filter -- completed 2026-06-26

**Goal**: The cross-repo and graph-triage advisory axes stop surfacing callers inside statically-dead code (if False:, if TYPE_CHECKING:, #if 0) as findings, eliminating the most common class of false positives
**Depends on**: Phase 28 (LANDED 2026-06-25, main ce12a0e); affects cross_repo_impact.py (Phase 27) and graph_triage.py (Phase 22). NOT gated on upstream: graph.db already gives forge each caller's file_path + line (nodes table, resolved today at cross_repo_impact.py:150), so a forge-side filter can read that one source line and run a cheap lexical or scoped tree-sitter ancestor scan (~15-50 lines, not a parser reimplementation) to drop callers under if False: / if TYPE_CHECKING:. Upstream code-review-graph #576 (a reachability field on edges) is a forward-compatible optimization, not a prerequisite: a json_extract filter on edges.extra is inert today (extra defaults to '{}') and auto-activates if #576 lands.
**Requirements**: SPEC-01 (advisory honesty -- forge's thesis is honest signal; a false positive in the anti-noise tool is a defect)
**Success Criteria** (what must be TRUE):

  1. find_cross_repo_callers (and the shared helper) returns live_caller but NOT dead_caller for the reproduction fixture (Python if False: + C #if 0)
  2. Bug-inject proof: neutralizing the liveness filter causes dead_caller to reappear (test FAILS); restoring it -> only live_caller (test PASSES)
  3. If CALLS+IMPORTS_FROM query is duplicated between cross_repo_impact and graph_triage, the shared query + liveness filter is extracted into ONE helper both axes call (no copy-paste)
  4. Honest ceiling documented: filter catches common cheap-to-detect idioms only, not general reachability

**Backend survey (2026-06-25, main session)**: surveyed OSS graph backends as alternatives to consuming an upstream flag; none adoptable. Lightweight condition-tagged-edge graphs (cartograph, decoder; both MIT) prove the cheap AST-walk technique (tag each CALLS edge with its guarding condition) but are Python-only, immature (1-6 stars, solo dev), and would replace code-review-graph while still missing C #if 0 -- value is as prior art for the #576 design, not as a backend. Heavyweight CPG engines (Joern, fraunhofer-aisec/cpg, CodeQL) model C/C++ reachability but are JVM + per-language frontends (uneven maturity) + non-SQL stores, and exceed Success Criterion #4's "cheap idioms only, not general reachability" ceiling. General reachability is undecidable (Rice/halting), and even Joern's high-maturity C frontend handles incomplete code without running the real preprocessor, so C #if 0 stays heuristic everywhere -- Criterion #4 is the correct response, not a compromise. Decision (revised 2026-06-25 after a 4-model panel + ground-truth recheck): keep code-review-graph, but build the filter forge-side now rather than waiting on #576. The earlier "gated on #576" read was wrong -- it mistook forge's current no-source-parsing implementation for an architectural constraint, when forge already runs shellcheck/pylint/ruff on source and graph.db hands it each caller's file:line. Ship one shared _is_dead_call_site helper (Criterion #3) covering Python if False:/TYPE_CHECKING: now plus C #if 0 (which #576 defers), keep the lexical scan as the fallback for sibling version skew, and add the inert json_extract(edges.extra) filter as forward-compat; forge author drives the #576 PR rather than blocking on it. Full survey + reversal: forge memory project_forge_phase29_backend_survey.
**Plans**: 2 plans
Plans:
**Wave 1**

- [x] 29-01-PLAN.md -- dead_code.py shared module + test_dead_code.py (unit + bug-inject + fail-safe)

**Wave 2** *(blocked on Wave 1)*

- [x] 29-02-PLAN.md -- Wire cross_repo_impact.py + graph_triage.py through _live_callers
**UI hint**: no

## Progress

**Execution Order:**
v2.4: Phases 17 -> 18 -> 18.1 -> 19 -> 19.1 -> 20 -> 21 -> 22 -> 23 (all complete)
v2.5: Phase 24 (parallel-ok) | Phases 26 and 27 can land before Phase 25 | Phase 25 is LARGE

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 17. Trust Gate + Eval Scaffold | v2.4 | 4/4 | Complete   | 2026-06-10 |
| 18. Taint + Provenance | v2.4 | 2/2 | Complete | 2026-06-11 |
| 18.1. Test Isolation + R1 Re-arm | v2.4 | 2/2 | Complete | 2026-06-11 |
| 19. Fix Validation | v2.4 | 2/2 | Complete | 2026-06-12 |
| 19.1. Presubmit Gate Hardening | v2.4 | 3/3 | Complete | 2026-06-12 |
| 20. Verdict Honesty | v2.4 | 3/3 | Complete   | 2026-06-12 |
| 21. Legacy + Intent | v2.4 | 3/3 | Complete    | 2026-06-13 |
| 22. Graph Triage | v2.4 | 2/2 | Complete   | 2026-06-14 |
| 23. Daemon State | v2.4 | 2/2 | Complete | 2026-06-15 |
| 24. Config Legibility | v2.5 | 3/3 | Complete | 2026-06-15 |
| 24.1. Outlet Alignment | v2.5 | 4/4 | Complete | 2026-06-16 |
| 25. Cross-Repo Merge Review | v2.5 | 7/7 | Complete | 2026-06-20 |
| 25.1. Backend Robustness | v2.5 | 3/3 | Complete | 2026-06-21 |
| 26. Cross-Repo Contract Context | v2.5 | 3/3 | Complete | 2026-06-21 |
| 27. Cross-Repo Impact via register | v2.5 | 2/2 | Complete | 2026-06-24 |
| 28. Reviewer Canary (Inline Outlet) | v2.5 | 5/5 | Complete   | 2026-06-25 |
| 29. Dead-Code False-Positive Filter | v2.5 | 2/2 | Complete    | 2026-06-26 |

See .planning/milestones/v2.5-ROADMAP.md for full phase details.

</details>

### Phase 30: Switch-On + Dogfood -- completed 2026-06-27

**Milestone**: v2.6
**Goal**: forge actually gates real changes on this machine through its CN backend, auto-fires via pre-commit hook, and has reviewed itself end to end
**Depends on**: v2.5 complete
**Requirements**: ADOPT-01, ADOPT-02, ADOPT-03, ADOPT-04, ADOPT-05
**Type**: config / wiring -- no new engine code
**Success Criteria** (what must be TRUE):

  1. `code-forge resolve-outlet` names a real backend (not "no backend") (ADOPT-01) -- VERIFIED this session
  2. One `code-forge review` returns real CN-API findings (not DELEGATED/PASS) (ADOPT-02) -- VERIFIED this session
  3. `code-forge install-hooks` in >=1 target repo creates a pre-commit hook that blocks a commit introducing a new test failure (ADOPT-03) -- VERIFIED 2026-06-27 (dogfood)
  4. With no backend configured, `code-forge review` fails closed (exit 2), never silent PASS (ADOPT-04) -- VERIFIED this session
  5. Forge dogfoods itself: an injected new-failure change is blocked by forge's own gate end to end (ADOPT-05) -- VERIFIED 2026-06-27 (dogfood)

**Plans**: 2 plans

Plans:
**Wave 1**

- [x] 30-01-PLAN.md -- Extend generate_hook_content() with planning-leak guard + LLM review block -- merged 15bff3a, d79ef45

**Wave 2** *(blocked on Wave 1)*

- [x] 30-02-PLAN.md -- Wire planning_leak_guard into run_install_hooks + repo survey + dogfood regression test + manual proof -- merged 19b58a2, 7ba18fe; Task 2 manual dogfood VERIFIED 2026-06-27 (gate-check named tests/test_dogfood_proof.py::test_dogfood_proof_fail as NEW failure, exit 1; passed when fixed; planning-leak guard blocked .planning staging exit 1)

**Note**: ADOPT-01/02/04 verified this session; 30-01 + 30-02 Task 1 code merged to main (7ba18fe, 2026-06-27; full suite 2119 passed, 70 hook/dogfood tests). ADOPT-03/05 VERIFIED 2026-06-27 by manual dogfood (gate-check named a NEW test failure, exit 1, passed when fixed; leak guard blocked .planning staging). Phase 30 COMPLETE. NOTE: editable install resolves to main src (verified: import code_forge -> src/code_forge), so the CLI already uses the merged code. Bare `pytest` from repo root double-collects the nested worktrees -- use `pytest --ignore=.worktrees --ignore=.claude/worktrees`, or remove stale worktrees first.

### Phase 31: CN Backend Robustness -- completed 2026-06-28

**Milestone**: v2.6
**Goal**: forge handles the error diversity of all five CN LLM providers (MiMo, DeepSeek, Zhipu, MiniMax, Kimi) robustly -- retries transient failures, fast-fails on quota exhaustion, and controls L1 pass concurrency for rate-limited backends
**Depends on**: Phase 30 (needs a working backend to test against)
**Requirements**: ROBUST-01, ROBUST-02, ROBUST-03, ROBUST-04, ROBUST-05
**Type**: logic-bearing code -- 3-cycle review required
**Success Criteria** (what must be TRUE):

  1. An HTTP 429 response triggers exponential backoff + jitter retry (not a silent pass drop); after retry the pass completes (ROBUST-01) -- VERIFIED (llm_invoke.py retry loop + 129 tests)
  2. When the provider returns a Retry-After header (DeepSeek, Kimi), the backoff respects that value instead of the computed delay (ROBUST-02) -- VERIFIED (_parse_retry_after + max(computed, header))
  3. Provider-specific error codes (Zhipu 1302/1305/1308, MiniMax 1002/1039/1041/2045) are classified as retryable or non-retryable; non-retryable triggers fast-fail with a clear error message (ROBUST-03) -- VERIFIED (PROVIDER_ERROR_CODES + _check_body_error; codes UNCONFIRMED vs live docs)
  4. L1 pass dispatch for a rate-limited backend (e.g. mimo-pro) completes 3/3 passes without 429-dropped passes (ROBUST-04) -- VERIFIED (factories.py pass-level retry + serial dispatch)
  5. HTTP 402 (balance exhaustion) or 403 (forbidden) produces a non-retryable fast-fail with an actionable error message naming the provider and the issue (ROBUST-05) -- VERIFIED (RETRYABLE_HTTP_STATUSES excludes 4xx)

**Plans**: 3 plans

Plans:
**Wave 1** *(parallel)*

- [x] 31-01-PLAN.md -- HTTP retry loop + provider error map + body-based detection in llm_invoke.py (ROBUST-01/02/03/05) -- merged 9a2acdc, 5805a5f
- [x] 31-02-PLAN.md -- gate.yaml retry config schema + validation in gate_check.py (ROBUST-01 config) -- merged 0ae6730

**Wave 2** *(blocked on Wave 1)*

- [x] 31-03-PLAN.md -- Pass-level retry in factories.py + retry config threading + full regression (ROBUST-01/04) -- merged b76983e, 8383a9f

**Note**: 13 commits, +1185/-91, 10 files. 2189 tests pass. Cold review 2 rounds, 0 real findings. Regression fix: breaker tests set retryable=False to isolate from pass-level retry (dc2f54b). B1 remediation: c053fb2 (leaked 31-03-SUMMARY.md) scrubbed via filter-repo. Zhipu/MiniMax error codes UNCONFIRMED vs live docs (scrape redirected to platform intro).

### Phase 32: Per-Change Intent Contract -- completed 2026-06-29

**Milestone**: v2.6
**Goal**: `code-forge review --contract FILE` feeds per-change intent through the existing contract_spec slot, so the reviewer checks code against stated invariants instead of against itself
**Depends on**: Phase 30 (needs working review pipeline); Phase 31 recommended (reliable backend)
**Requirements**: CONTRACT-01
**Type**: logic-bearing code -- 3-cycle review required
**Success Criteria** (what must be TRUE):

  1. `--contract FILE` flag parsed by cli.py, file content injected into the existing contract_spec param (cli.py:614-617, factories.py:208/281)
  2. A planted contract-violating change is caught WITH --contract that the no-contract run misses (bug-inject proof)
  3. Contract content follows the confirmation-bias rule: states invariants-to-verify + residual risks, NEVER "this is correct/safe" (arXiv 2603.18740: framing-as-safe drops detection 16-93pp)

**Plans**: 2 plans

Plans:
**Wave 1**

- [x] 32-01-PLAN.md -- --contract flag parsing, file reading, merge, bias directive, summarization, init template

**Wave 2**

- [x] 32-02-PLAN.md -- Full test suite: guard bug-inject proof, merge tests, directive assertion, SC1 injection test

**Ground truth**: contract_loader.py + contracts.yaml already exist (cli.py:948-961, :1609-1617, :1686-1746). This phase reuses, not rebuilds.

**Note**: 3 commits, +417/-4, 3 files. 2218 tests pass (29 new). Cold review 1 round (5 false positives); hot inline 9-pass review 3 consecutive clean cycles. Post-review fix: stdin closed-fd guard (d87125a). 26 CONTEXT decisions, 4-model external plan review.

### Phase 33: MCP Server -- completed 2026-06-29

**Milestone**: v2.6
**Goal**: `code-forge-mcp` stdio server exposes review and gate-check as MCP tools callable from any IDE (Claude Code, VS Code, Cursor)
**Depends on**: Phase 30 (trusted backend); Phase 32 recommended (so MCP also exposes --contract)
**Requirements**: MCP-01, MCP-02
**Type**: logic-bearing code (net-new ~200-300 LOC) -- 3-cycle review required
**Success Criteria** (what must be TRUE):

  1. `code-forge-mcp` starts as an MCP stdio server; review and gate-check tools appear in the tool list from any MCP client
  2. MCP review tool routes to the resolved trusted backend; a finding returns via CN API, not a DELEGATED self-review by the calling model
  3. MCP server fails closed when no backend is configured (same contract as CLI)

**Plans**: 2 plans

Plans:
**Wave 1**

- [x] 33-01-PLAN.md -- mcp_server.py + mcp_jobs.py + pyproject.toml entry point (MCP-01, MCP-02)

**Wave 2** *(blocked on Wave 1)*

- [x] 33-02-PLAN.md -- test_mcp_server.py + test_mcp_jobs.py (MCP-01, MCP-02)

**Note**: 5 commits, +1400 LOC, 4 files (mcp_server.py, mcp_jobs.py, test_mcp_server.py, test_mcp_jobs.py). 50/50 MCP tests pass, 125/125 targeted regression pass. Cold-agent forge review: 2 findings fixed (F1 gate_check timeout test coverage, F2 tempfile leak guard). README updated with MCP setup docs (+55 lines). pyproject.toml: `mcp` optional dep + `code-forge-mcp` entry point.

### Phase 34: Provider-Aware Params + SSE Streaming + CLI Env -- completed 2026-06-30

**Goal**: Let forge backends declare per-provider reasoning/sampling parameters and cli env overrides so customers configure one gate.yaml entry per provider
**Implements**: ADR-0004 (cli-backend env field) + ADR-0005 (provider-aware parameter passthrough + SSE streaming)
**Depends on**: Phase 33 (MCP server) -- v2.6 complete
**Type**: logic-bearing code -- 3-cycle review required

Plans:
- [x] 34-PLAN.md -- 7 waves: BackendConfig fields, parse validation, _apply_params body mapping, SSE _read_sse, CLI env Popen, init_template examples

**Note**: 7 commits, +1090 LOC, 5 files (backend.py, llm_invoke.py, init_template.py, test_backend.py, test_llm_invoke.py). 75 new tests. key-follows-field design (host-decided post-execution): openai wire key derived from populated field, anthropic/vertex pinned to max_tokens. 7-round external plan review (ds/gm/mimo/mm) converged to 0/0/0/0. Real-API smoke: DeepSeek single max_tokens PASS, Vertex output_config.effort PASS (top-level 400). Cold reviewer 0/0/0/0.

### Phase 35: MCP Sampling Review Backend -- completed 2026-07-01

**Goal**: Add MCP sampling as a new outlet type so forge can use the client's model (Copilot/Claude Max subscription) instead of requiring a separate API key for review
**Implements**: ADR-0007 (MCP sampling as review backend)
**Depends on**: Phase 34 (provider-aware params), ADR-0006 (workspace resolution)
**Type**: logic-bearing code -- 3-cycle review required

Plans:
- [x] 35-01-PLAN.md -- Sampling outlet + invoke_sampling() + capability detection

**Note**: 2 commits (73b54e3 + 9b9bdb5), +794 LOC, 9 files. 16 new tests (15 sampling + 1 timeout-cancel). 9-round external plan review converged 0/0/0/0. 3-round code review (4 models x 3 rounds) found 9 real bugs, all fixed. User human review found 4 defects (finishing-pass), all fixed with bug-inject proof. Full regression 2372 passed / 7 skipped. 5-round usability audit (55 findings) followed.

## Progress

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 30. Switch-On + Dogfood | v2.6 | 2/2 | Complete   | 2026-07-01 |
| 31. CN Backend Robustness | v2.6 | 3/3 | Complete | 2026-06-28 |
| 32. Per-Change Intent Contract | v2.6 | 2/2 | Complete   | 2026-07-01 |
| 33. MCP Server | v2.6 | 2/2 | Complete   | 2026-07-01 |
| 34. Provider Params + Streaming + CLI Env | v2.7 | 1/1 | Complete   | 2026-07-01 |
| 35. MCP Sampling Review Backend | v2.7 | 1/1 | Complete | 2026-07-01 |
| 36. Usability Hardening | v2.7 | 7/7 | Complete   | 2026-07-01 |
| 37. User-Level Config | v2.8 | -- | Complete | 2026-07-03 |
| 37.1 F5 Backend Passthrough | v2.8 | -- | Complete | 2026-07-04 |
| 38. setup-mcp | v2.8 | -- | Complete | 2026-07-03 |
| 38.1. Stale-process guard | v2.8 | -- | Complete | 2026-07-04 |
| 38.1-5/6. Contract heading + duration fix | v2.8 | -- | Complete | 2026-07-09 |
| 38.2. PDEATHSIG orphan guard | v2.8 | -- | Complete | 2026-07-04 |
| 43. LEDGER append-only record | v2.8 | -- | Complete | 2026-07-04 |
| 39. L1 pass parallelization | v2.8 | -- | Complete | 2026-07-05 |
| 38.3. MCP out-of-box UX | v2.8 | -- | Complete (superseded in main) | 2026-07-08 |
| Usability on-ramp batch | v2.8 | -- | Complete | 2026-07-07 |
| surflare consumer-pain fixes | v2.8 | -- | Complete | 2026-07-08 |
| 46. doctor tool-audit | v2.8 | -- | Complete | 2026-07-11 |
| Windows MCP wave 1 | v2.8 | -- | Complete (gpu-win verified) | 2026-07-11 |
| 48. LLM stream TTFT + truncation continuation | v2.9 | 1/1 | Complete | 2026-08-16 |

### Phase 36: Usability Hardening -- completed 2026-07-01
**Goal:** Fix the 55 usability findings (MCP-01..55) from the 5-round exhaustive
audit so forge is shippable to users who are not the author. Onboarding (the
original Phase 36 scope) is one of 7 fix patterns, not the whole phase.
**Input:** .planning/phases/36-api-backend-onboarding/36-MCP-USABILITY-FINDINGS.md
**Depends on:** Phase 35 (MCP sampling)
**Type:** mixed (code fixes + docs fixes + config) -- classify per-pattern

**7 fix patterns (55 findings -> 7 workstreams):**

Pattern A -- MCP-to-CLI flag alignment (MCP-10, 11, 13, 14): MCP server passes
flags (--no-color, --baseline, --backend, --output, --version) that CLI argparse
does not define. Every MCP subprocess call hitting these silently exits 2.
Fix: align argparse definitions with mcp_server.py cli_args.

Pattern B -- Error remediation (MCP-09, 17, 18, 26, 27, 34, 38, 43, 44):
CliError has no hint field; 36 of 37 raise sites give no fix step; hooks and
subcommands give opaque errors. Fix: add optional remediation field to CliError,
backfill operational errors with one-line hints.

Pattern C -- Docs vs reality (MCP-12, 15, 28, 29, 30, 31, 32, 33, 49, 50, 51,
52): phantom CLI flags documented, stale status lines, wrong model IDs, missing
exit codes, outdated pass names. Fix: one docs audit pass reconciling every
command/flag/status against code.

Pattern D -- Silent failures (MCP-16, 21, 22, 35, 36, 37, 54): stderr
discarded, tool timeouts invisible, malformed data swallowed, parser exceptions
discard prior findings, sqlite3 connections leak. Fix: surface errors through
infra_errors/stderr/logging instead of bare pass.

Pattern E -- Onboarding friction (MCP-01, 02, 03, 04, 05, 06, 07, 08, 42):
the original Phase 36 scope. Workspace resolution, trust ceremony, worktree
guard, key provenance, reconnect zombies. Fix: two-step init + trust-on-first-
use + --allow-main + forge doctor.

Pattern F -- Edge-path crashes (MCP-19, 20, 23, 39, 40, 47, 48): CliError not
imported, _run_ci returns None, missing FileNotFoundError catch, ValueError
traceback, fixval deletes recovery file, hold.py IndexError, diagnose masks
reason. Fix: per-site 3-5 line fixes.

Pattern G -- Packaging and hygiene (MCP-41, 45, 46, 53, 55): version desync
(pyproject.toml/init/CLAUDE.md), baseline delta pytest-only, type annotation
wrong, dead code, job pop-on-read contradicts idempotentHint. Fix: single-source
version, per-site cleanup.

**Severity summary:** 2 BLOCKER, 13 HIGH, 21 MEDIUM, 19 LOW.
**Main-path safe:** BLOCKERs (MCP-01, 19) do not affect CLI-direct review +
API backend happy path (verified R3 Impact phase).

**Plans:** 7/7 plans complete

Plans:
- [x] 36-01-PLAN.md -- Fix edge-path crashes (Pattern F: MCP-19,20,23,39,40,47,48)
- [x] 36-02-PLAN.md -- Fix packaging and hygiene (Pattern G: MCP-41,42,53,54,55)
- [x] 36-03-PLAN.md -- Fix docs vs reality (Pattern C: MCP-12..15,28..34,49..52)
- [x] 36-04-PLAN.md -- Align MCP-to-CLI flags (Pattern A: MCP-10,11,13,14)
- [x] 36-05-PLAN.md -- Surface silent failures (Pattern D: MCP-16,21,22,24,25,35..37,43..45)
- [x] 36-06-PLAN.md -- Add error remediation (Pattern B: MCP-09,17,18,26,27,34,38)
- [x] 36-07-PLAN.md -- Fix onboarding friction (Pattern E: MCP-01..08)
