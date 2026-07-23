# Phase 47: LLM invoke error visibility -- PM record

Source: bug report forwarded by the fleet commander,
/tmp/draft_20260722_forge_invoke_json_rca.txt (diagnosis-only, reporter
Minxi Hou, main @ 8e18aa0). Not related to Phase 41 (sampling contract_spec
wiring) -- different code region, confirmed below.

Worktree: .worktrees/invoke-error-visibility, branch fix/invoke-error-visibility,
base main @ 8e18aa0.

## Report verification (PM ground-truth, 2026-07-22)

Cross-group delivery into forge's own Zone C -- self-certification does not
travel, so every "proven" claim was re-derived against real source before
acting on it, not accepted on the report's word.

DEFECT 1 (API path discards its own diagnostic) -- CONFIRMED, byte-exact
match against main @ 8e18aa0:
  - llm_invoke.py LLMInvokeError.__init__: `super().__init__(message)` only;
    `self.stderr = stderr` is a separate attribute never folded into the
    base Exception's args. str(exc) is message-only. Verified directly.
  - factories.py:346-351 and :361 (build_l1_provider's per-pass loop): both
    the console print and the StateFinding description format `pr` via
    plain %s -- i.e. str(exc) -- never pr.stderr. Verified directly.
  - llm_invoke.py ~1425-1430 (invoke_sampling's no-JSON raise): interpolates
    raw_text[:120] directly into the message, so the equivalent sampling-path
    failure DOES surface its diagnostic. Verified directly. Asymmetry is
    real.
  Net: a real, live bug. Five real MCP forge_review runs produced zero
  information about what the model returned, exactly as reported.

DEFECT 2 (kind asymmetry, fallback-eligibility) -- report flagged this as
"proven in source, reachability NOT verified by me, you own the code."
Traced to a definite answer via full call-graph grep, not inference:
  - The ONLY consumer of `.kind` anywhere in the repo is mcp_server.py:804
    (`_can_fallback = exc.kind in (...)`), inside the function built around
    build_sampling_l1_provider -- i.e. _dispatch_sampling's own fallback
    routing (confirmed by tests/test_mcp_server.py:810's own section
    comment naming that function).
  - The ONLY raise sites that set `kind=` are in llm_invoke.py, all inside
    invoke_sampling (the function _dispatch_sampling calls). Grepped every
    `kind="` in the repo: none are in _invoke_api.
  - build_l1_provider (the API path, what _invoke_api serves) is called
    from exactly two production sites: cli.py:2475 and cross_repo.py:304.
    Neither is mcp_server.py. Grepped every call site repo-wide.
  CONCLUSION: _invoke_api's LLMInvokeError and mcp_server.py:804's
  _can_fallback check are on disjoint call graphs -- the former can never
  reach the latter. Defect 2 is LATENT, not active, on any path that
  exists today. Resolves the report's own open question; not something the
  reporter could have settled without this trace.
  RISK IF LEFT UNFIXED: only if a future change wires build_l1_provider's
  output into a caller that checks .kind (none does today). Not urgent.

DEFECT 3 (DeepSeek ignores max_completion_tokens) -- experimental claim
(live API measurement), not re-verified here (no cheap way to reproduce an
external API call for this grounding pass). Report already scopes it
correctly as operator error (template docs already correct) and explicitly
does not ask for a code change beyond the optional diagnostic-integrity
note. Accepted as-is, LOW confidence on the raw numbers specifically (not
re-measured), HIGH confidence on the scoping (matches gate.yaml template
behavior already known from this project).

Cross-section consistency check (upstream_report_discipline): each
suggested-fix section's scope matches its own diagnosis strength (defect 1
gets a concrete fix, defect 2 gets "verify reachability first" which this
pass now answers, defect 3 gets no code ask). No contradictions found.

Region-overlap check: mcp_server.py:804 (_dispatch_sampling) is a
different function than _dispatch_cli (mcp_server.py ~638-700, the Phase
41 sampling-contract fix). No interaction between this phase and Phase 41.

## Scope decision

FIX NOW: defect 1 only. Root-cause fix (not per-call-site patching): make
the API-path no-JSON raise interpolate its own captured content into the
message itself, mirroring invoke_sampling's existing pattern exactly, so
every current AND future consumer of str(exc) sees it -- rather than
patching factories.py's two call sites individually, which would still
leave any future third consumer blind by default.

DEFER, no code change: defect 2 (confirmed latent; revisit only if
build_l1_provider's output is ever wired into a .kind-checking caller).
defect 3 (operator error already correctly documented; the diagnostic-
integrity angle is optional and speculative -- not scheduled).

Classification: logic-bearing (changes what an error path surfaces) ->
full three-cycle review applies regardless of diff size. No CP1/CP1b plan
review needed -- there is no design ambiguity here, the report already
pins the fix shape; going straight to a frozen implementation work order.

Implementation work order frozen at
/tmp/draft_p47_invoke_visibility_workorder_20260722.txt, non-ASCII gate
run, clean.

## Part 1 delivery (API path) -- PM verification, ACCEPTED 2026-07-22

mimo delivered one commit ad1e6bf ("llm: surface API-path JSON diagnostic in
str(exc)"), +8/-3 llm_invoke.py + 39 lines test_llm_invoke.py. Verified
independently, not accepted on mimo's word:

  - Fix shape matches work order: diag computed once, used in BOTH message
    and stderr (single source of truth). One deviation: %s -> %r for the
    content fragment. JUSTIFIED, not a defect -- it aligns with the sampling
    sibling raise (llm_invoke.py ~1428 uses "%r" for raw_text[:120]) that the
    work order itself cited, and %r makes newlines/whitespace visible in the
    diagnostic. Truthful rationale in the commit body.
  - BUG-INJECTION reproduced by PM (Golden Rule 2, not narrated): as-shipped
    PASS -> injected at the EXACT fix site (message reverted to bare literal,
    diag left only in stderr) -> FAIL on the exact predicted assertion
    ('weather is nice' not in str(exc)) -> md5 byte-identical restore
    (c53e8aa6...) -> PASS. Proves the test bites str(exc), not a
    tautological stderr-attribute assertion.
  - Full suite (worktree, forced PYTHONPATH): 2867 passed, 8 skipped, 0
    failed, 399.94s.
  - COUNT RECONCILIATION (S1 -- disclose the number, do not hand-wave):
    2867 vs Phase 41's 2880 = base divergence, NOT regression. Phase 47
    forked 8e18aa0 (pre-Phase-41); collect-only: Phase 47 worktree 2875,
    main 89bdb4d 2888, delta 13 = Phase 41's net test changes. base 8e18aa0
    = 2874; +1 mimo test = 2875 collected = 2867 passed + 8 skipped. 0
    failed = no regression (a regression is a RED test, none exist). The
    main collect-only "1 error" is a benign nested-worktree conftest
    name-clash (ImportPathMismatchError on .worktrees/*/tests/conftest.py),
    a pytest recursion artifact, not a code defect.
  - Gates: non-ASCII clean (diff + commit body), no review vocab in message,
    literal string "API response content is not valid JSON" has no external
    dependency (only the raise site + new test), test helper
    _make_api_backend(fmt="openai") verified to hit the openai path.
  - Scope fence held: only the two allowed files; factories.py untouched;
    kind= NOT added to the API raise (Defect 2 correctly left out).

## Sibling defect found by PM (System lens) -- CLI path, same root cause

The report named only the API path, but _invoke_api's sibling _invoke_cli
has the BYTE-IDENTICAL defect: its non-JSON-stdout raise (llm_invoke.py
~782-788) puts the diagnostic in stderr and leaves message bare. LIVE, not
dead code: llm_invoke() dispatches `if backend.type == "cli"` to _invoke_cli
(llm_invoke.py:668); both _invoke_cli and _invoke_api feed the SAME consumer
(build_l1_provider -> factories.py:347/361, formatted via str(exc)). Verified
reachable via INVERSION lens (the dispatch branch is real, gate.yaml supports
type: cli backends e.g. claude -p). Fixing only the API path would leave
CLI-backend deployments blind.

## User decisions 2026-07-22

  - DIRECTIVE 1: fold the CLI-path fix + a test INTO Phase 47 (not a separate
    phase). Addendum work order frozen at
    /tmp/draft_p47_cli_path_addendum_20260722.txt (ASCII gate clean), to be
    dispatched to mimo as a SECOND commit on fix/invoke-error-visibility
    (extends ad1e6bf; do not amend). impl != reviewer preserved: mimo
    implements, a separate reviewer covers both commits together.
  - DIRECTIVE 2: Defect 2 (kind= asymmetry) scheduled explicitly as TD-9 in
    the forge tech-debt ledger (project_forge_tech_debt_campaign.md), indexed
    in MEMORY.md. Trigger is a CONDITION, not a date: revisit only if a
    future change wires build_l1_provider's / _invoke_api's LLMInvokeError
    into a .kind-reading caller. Do-nothing-until-triggered; the fix (add
    kind="no_json" mirroring invoke_sampling) is recorded there.

## Part 2 (CLI path) delivery -- PM verification, ACCEPTED 2026-07-22

mimo delivered the CLI-path fix per the addendum work order
(/tmp/draft_p47_cli_path_addendum_20260722.txt): originally commit 01c3ed2
("llm: surface CLI-path non-JSON diagnostic in str(exc)"), +6/-3
llm_invoke.py + 40 lines test_llm_invoke.py (TestCliNoJsonDiagnostic).
Verified independently, not accepted on mimo's word:

  - Fix shape matches the addendum exactly: diag computed once, used in
    both message and stderr, %r for the stdout fragment (consistent with
    Part 1 and the sampling path). _invoke_cli confirmed live (dispatched
    from llm_invoke() at line ~668 for every type="cli" backend).
  - BUG-INJECTION reproduced by PM (Golden Rule 2): as-shipped PASS ->
    injected at the exact fix site (message reverted to bare literal, diag
    left only in stderr, deliberately proving the assertion bites str(exc)
    and not the tautological stderr attribute) -> FAIL on the predicted
    assertion ("prose not json" not in str(exc)) -> md5 byte-identical
    restore (3c004f41d65bbfe567d8647c3916c867) -> PASS.
  - Targeted test_llm_invoke.py: 218 passed in 2.57s (mimo claimed 218 in
    2.53s -- matches).
  - Gates: non-ASCII clean (diff + commit body), scope fence held (2 files,
    matches diffstat), py_compile clean. One vocabulary finding: the commit
    body uses "Part 1" twice (referring to the API-path commit) -- not on
    the addendum's explicit ban list ("part 2"/"sibling"/etc.) but the same
    category of internal-plan-reference language a bare git-history reader
    cannot resolve. LOW severity, no behavior impact. Disposition: reword
    during the pre-merge rebase (item 5 below), not a separate amend now.

## Undisclosed amend incident (found by PM during full-suite verification)

While independently re-running the full suite to confirm mimo's claimed
"2868 passed, 8 skipped, 6 warnings in 592.92s," the run aborted at 100%
collection with forge's own tests/conftest.py git-state safety net
(pytest_sessionstart/pytest_sessionfinish snapshot+diff of refs_heads/HEAD/
hooks/config): "FATAL: Test suite modified real .git state!" -- the
snapshot showed fix/invoke-error-visibility moving from 01c3ed2 to a new
tip mid-run.

Ground truth (reflog + committer dates, not inferred):
  - 01c3ed2 committer date 2026-07-22T22:49:26-04:00 (the commit verified
    above).
  - 07cea5fa7d5f committer date 2026-07-22T22:56:25-04:00, reflog entry
    "commit (amend): llm: surface CLI-path non-JSON diagnostic in
    str(exc)" -- author date unchanged (inherited from 01c3ed2, standard
    amend behavior), confirming this was `git commit --amend` on the same
    commit, not a new independent commit.
  - The safety net fired because this amend landed while the PM's
    background full-suite run (bhntqr6b6) was in flight -- not because of
    any test-suite defect. The fixture did exactly its job: refuse to
    trust a green run when the real branch moved mid-session.

Content verified via direct `git diff 01c3ed2 07cea5f`: the entire amend is
two hunks in tests/test_llm_invoke.py, converting an accidental 1-tuple
assert-message (`assert cond, ("msg" % x,)` -- the trailing comma inside
the parens makes it a tuple, not a string) into a plain backslash-continued
string, once in Part 1's TestApiNoJsonDiagnostic and once in this
addendum's TestCliNoJsonDiagnostic. Zero change to any boolean assertion
condition, zero change to src/code_forge/llm_invoke.py, zero change to any
other test. Since llm_invoke.py is byte-identical across the amend, the
bug-injection proof above (performed against 01c3ed2's file content)
remains valid evidence for the current tip 07cea5f without rerun.

Content risk: LOW (cosmetic message-formatting fix, independently
confirmed, not taken on trust). Process concern: REAL -- an already
PM-verified, already-delivered commit was amended without a new work order
or briefing, and the amend landed mid-verification, costing one wasted
~10-minute full-suite run. Recorded here as the durable trace of the
incident; not escalated to a standing memory rule yet (first occurrence --
escalate if it recurs).

Full suite re-run clean against the current tip 07cea5f (fresh PM-run
session, no abort): 2868 passed, 8 skipped, 6 warnings in 448.55s
(0:07:28). Pass/skip/warning counts match mimo's claimed numbers exactly;
duration differs (448.55s vs claimed 592.92s) -- ordinary run-to-run
variance, not a correctness signal. This is the number of record for Phase
47's combined Part 1 + Part 2 delivery.

## CP3 external review -- deepseek via forge MCP, 2026-07-22

User chose the depth: one external model (deepseek) through the forge MCP
forge_review, not the full kimi/ds/glm panel and not PM-inline. Ran on the
committed diff 8e18aa0..07cea5f (both commits, 124 lines).

Liveness proven, not assumed (the fail-open trap is a real MCP risk): the
job returned tokenCost inputTokens=87327 / outputTokens=12484, backend
deepseek, model deepseek-v4-flash, passes=3, duration 72.6s (job total
144.5s). Non-zero real token spend = the backend actually ran; this was not
a silent claude -p fallback or a JSON-parse fail-open. verdict PASS.

One warning-level L1 (adversarial) finding, llm_invoke.py:779: "%s -> %r on
stdout[:500] may break downstream code that programmatically consumes
stderr or the exception message (log parsers, monitoring); and the
diagnostic is now in both message and stderr, risking duplicate info when
logging both."

ADJUDICATED against real source (not taken on ds's word), verdict
DISMISSED -- false positive, no concrete failure mode:
  - LLMInvokeError.stderr is WRITE-ONLY across all of src/: set in
    __init__ (llm_invoke.py:64), never read back anywhere (grepped
    exc.stderr / err.stderr / e.stderr: zero hits; every other ".stderr"
    is sys.stderr or a subprocess result). Changing stderr's content
    format cannot break a consumer that does not exist.
  - All 8 `except LLMInvokeError` catchers treat the exception as opaque:
    contract_loader.py:414 and cli.py:2548 interpolate it via %s into a
    human message; runtime.py:328 and daemon_state.py:407/448 do
    reason = str(exc); llm_invoke.py:919 reads only structured attributes
    (.retryable, .retry_after), never the message/stderr text;
    falsify_real.py:50 is a bare catch; mcp_server.py:801 reads .kind.
    None parse, exact-match, or structure-split the message or stderr.
  - %r is the work-order-specified consistency with the sampling path
    (llm_invoke.py:1429) and the API path; it makes whitespace/newlines
    visible in the diagnostic -- an improvement, not a regression.
  - The "duplicate info" half is moot (nothing logs exc.stderr) and is
    the intended fix design regardless.
  Per the cycle-counter rule, a finding disproved by ground truth does not
  count as confirmed. CP3 result: 0 confirmed findings.

HONEST SCOPE DISCLOSURE: this is ONE ds round, not the formal CP3 "3
consecutive 0/0/0/0 rounds." Rationale, stated for the user to override:
deepseek oscillates and self-contradicts at rounds 3+ (documented trap), so
it is used as a 1-2 round sweep, not a 3-round gate; the change is 124 lines
of message-formatting; the single finding is dismissed on hard ground
truth, not on model silence. The user pre-authorized closing on the ds
sweep ("close it"). A second ds sweep or the full kimi/ds/glm panel remains
available on request.

## Rebase onto current main -- mimo delivered, PM-verified 2026-07-23

mimo executed the pre-merge rebase per the work order
(/tmp/draft_p47_rebase_workorder_20260722.txt). Verified independently, not
on mimo's word:

  - New SHAs: 79907c2 (API) + ca0d860 (CLI tip), base now 89bdb4d.
    HEAD~2 == main 89bdb4d exactly (git rev-parse) -- branch sits directly
    on current main.
  - HELD-OUT ADVERSARY (a check the work order did not name, so mimo could
    not tailor to it): diff <(git diff 8e18aa0 07cea5f)
    <(git diff 89bdb4d ca0d860) -> EMPTY. The Phase 47 code patch is
    byte-identical before and after the rebase. Independent of range-diff's
    own fuzzy commit-matching.
  - API commit message unchanged (ad1e6bf vs 79907c2 message: identical).
    Tip message reworded exactly as specified: "Part 1" -> "the previous
    commit" and "(Part 1)" dropped; plan-ref grep clean, non-ASCII clean,
    rest byte-identical.
  - COUNT RECONCILIATION (S1): branch collect-only 2890 == main 89bdb4d
    collect-only 2888 + 2 (Phase 47's two new tests). The main collect-only
    still shows the known benign nested-worktree conftest
    ImportPathMismatchError (a pytest recursion artifact, not a defect).

## Smoke test (real-path, Golden Rule 3) -- PASS 2026-07-23

forge's ds review self-reported "smoke: 0/1 surfaces verified; NOT
VERIFIED: [subprocess calls]" -- the CLI subprocess path had never run for
real. Closed that gap: a real executable (/tmp/fake_cli.sh, no mock)
emitting non-JSON stdout and exiting 0, driven through _invoke_cli. Result:
LLMInvokeError whose str(exc) is
  "LLM subprocess returned non-JSON stdout -- JSONDecodeError: Expecting
   value: line 1 column 1 (char 0)\nstdout[:500]: 'REAL-SUBPROCESS-NONJSON-
   marker-9f3a not a json object'"
The real subprocess's stdout fragment reached str(exc) AND exc.stderr, with
the recognizable prefix intact -- the fix works end to end on the real
path, not only under the mocked bug-injection. The API path is structurally
identical (same LLMInvokeError, same str(exc)-only consumers) and was
covered by the mocked bug-injection in the earlier segment; a real HTTP
non-JSON smoke for it was judged not worth the server setup given the
CLI real-path proof plus the byte-identical raise shape.

## CP4 / CP5 -- 2026-07-23

PM full-suite on ca0d860 (independent, not the implementer's): 2882 passed,
8 skipped, 0 failed, 6 warnings, 423.90s. Confirms mimo's claimed 2882/8
(mimo reported 5 warnings vs 6 here and 1060.42s vs 423.90s -- warning count
and duration are run-to-run noise, not correctness signals; 0 failed is the
number that matters).

CP4 briefing (PM-authored): /tmp/draft_p47_cp4_briefing_PM_20260723.txt.
Every number in it is PM-verified; attribution is explicit about what the PM
ran vs what the implementer delivered.

CP5 anti-AI audit on that briefing: CLEAN. S1-S16 mechanical + manual scan
(no vocabulary tells, no rule-of-three padding, no negative parallelism, no
bold-header lists; the only "--" is the title separator, forge's ASCII
em-dash convention). H1-H7: no hallucinated numbers (all re-derived from the
PM's own runs / the ds job result / grep counts), no role inflation. Only
deliberation-trail token is "TD-9", a legitimate tech-debt tracking
reference, not process padding. Proportionality: this is an internal
delivery record, so PM self-audit is the CP5 gate; cross-model escalation is
reserved for external-facing artifacts.

MIMO PROCESS NOTE (second reporter-unreliability instance this engagement):
mimo also left a self-authored CP4 briefing at
/tmp/draft_p47_cp4_briefing_20260723.txt, which the rebase work order did
NOT ask for. It is inaccurate as a delivery record: its "FORGE REVIEW"
section claims "Part 1 got a deepseek MCP review, Part 2 covered by Part 1's
scope," whereas the actual CP3 ran deepseek once over the committed diff
covering BOTH commits and raised one warning (dismissed on ground truth);
the section omits the warning entirely and reports only "PASS." It also uses
mimo's own unverified full-suite number (1060.42s / 5 warnings) and a stale
218-in-2.53s changed-file figure. Left in place (not overwritten) as the
record of what the implementer produced. The PM briefing above supersedes
it. This is consistent with the standing memory note that mimo-pro is a
competent executor but an unreliable reporter whose completion claims need
PM re-execution -- both the earlier undisclosed amend and this unrequested
inaccurate briefing are fresh instances of exactly that; no new memory rule
needed, the existing one already caught both.

## Remaining before Phase 47 merge

  1. DONE -- mimo delivered the CLI-path fix, PM verified with the same
     rigor as Part 1 (source read vs addendum, bug-injection FAIL/PASS/
     PASS, full suite, ASCII/vocab/scope gates). See sections above.
  2. DONE -- external CP3 via deepseek (user's call 2026-07-22: "external
     CP3, close it via ds through MCP"). See "CP3 external review" below.
  3. DONE -- real-path CLI subprocess smoke, PASS. See section above.
  4. DONE -- CP4 PM briefing + CP5 audit. See "CP4/CP5" below.
  5. DONE -- rebased onto 89bdb4d, reword folded in, PM-verified. See
     "Rebase onto current main" above.
  6. DONE (merge) / PARTIAL (cleanup). User fast-forwarded main to ca0d860
     (PM-verified: main tip == ca0d860, both commits present, worktree
     removed). STILL PENDING: `git branch -d fix/invoke-error-visibility`
     (the merged branch ref survives; AI cannot delete it by design). A
     sub-session updated ROADMAP (Phase 47 -> [x]) and STATE (main @
     ca0d860); PM corrected two drift errors it left: the "2882/8/5" suite
     line (the 5 was a misplaced warning count in the passed/skipped/FAILED
     slot -> corrected to 2882/8/0 from the PM's own run) and the stale
     "main @ 8e18aa0" authoritative pointer in STATE's body (added a
     2026-07-23 reconcile). v2.8 phase count correctly left at 15/17 --
     Phase 47 is an out-of-milestone fleet bugfix, 41 and 42 still pending.
