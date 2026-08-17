---
phase: 48-stream-ttft-truncation
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - src/code_forge/llm_invoke.py
  - src/code_forge/cli.py
  - src/code_forge/factories.py
  - tests/test_llm_invoke.py
autonomous: true
requirements:
  - STREAM-VISIBLE
  - TRUNCATION-RECOVER

must_haves:
  truths:
    - "A streaming openai-format call emits exactly one first-token progress event naming the backend, before the stream finishes"
    - "A truncated openai/anthropic/vertex response with usable partial JSON is recovered by bounded continuation and returns the parsed envelope"
    - "A truncated response with zero usable content still raises kind=truncated after exactly one HTTP call"
    - "Continuation exhaustion raises kind=truncated, retryable=False, with the distinguishing message"
    - "The run-level breaker trips after threshold truncation events and later calls fail fast before any network call"
    - "A recovered result reports summed usage and is_truncated=True"
  artifacts:
    - path: "src/code_forge/llm_invoke.py"
      provides: "Truncation carrier, recovery helper, thread-safe breaker with pre-dispatch check, TTFT emit"
      contains: "_TruncatedResponse"
      contains: "TruncationBreaker"
      contains: "check_tripped"
      contains: "_continue_truncated"
      contains: "progress.emit"
    - path: "src/code_forge/cli.py"
      provides: "Per-run TruncationBreaker construction"
      contains: "TruncationBreaker"
    - path: "src/code_forge/factories.py"
      provides: "Breaker threading into llm_invoke via build_l1_provider"
      contains: "continuation_breaker"
    - path: "tests/test_llm_invoke.py"
      provides: "TestTruncationCarrier, TestTruncationRecover, TestTruncationBreaker, TestTruncationBreakerWiring, TestReadSSE.test_first_token_emit"
      min_lines: 280
  key_links:
    - from: "src/code_forge/llm_invoke.py _invoke_api except handler"
      to: "_continue_truncated"
      via: "isinstance(exc, _TruncatedResponse) branch placed BEFORE the retryable gate"
      pattern: "_continue_truncated"
    - from: "src/code_forge/llm_invoke.py _invoke_api loop entry"
      to: "TruncationBreaker.check_tripped"
      via: "pre-dispatch fail-fast check before any network call"
      pattern: "check_tripped"
    - from: "src/code_forge/llm_invoke.py _read_sse"
      to: "code_forge.progress.emit"
      via: "first non-empty delta.content chunk, flag-guarded"
      pattern: "progress\\.emit"
    - from: "src/code_forge/cli.py (breaker construction)"
      to: "src/code_forge/llm_invoke.py llm_invoke"
      via: "build_l1_provider continuation_breaker parameter"
      pattern: "continuation_breaker"
---

<objective>
Add TTFT visibility to the SSE stream path and bounded continuation recovery
for truncated LLM responses in src/code_forge/llm_invoke.py.

Phase goal (ROADMAP.md Phase 48): streaming passes emit a first-token
progress event (TTFT visibility), and passes truncated by provider
max_tokens caps are recovered by bounded continuation instead of dying on
incomplete JSON.

Requirements: STREAM-VISIBLE, TRUNCATION-RECOVER.

Purpose: two defects, one design pass.

(1) TTFT is a LIVE production defect, not future-proofing: bonsai
(gate.yaml:434) runs stream: true (openai format, model
Ternary-Bonsai-27B-Q2_0.gguf, timeout_s 3600). _read_sse assembles the
SSE stream into a response dict but emits nothing while bytes arrive
(llm_invoke.py:375-437), so every bonsai pass today is a silent stall
with zero progress events until the whole body is assembled. Post-T1,
bonsai emits one "backend bonsai: first token" line per call -- that
stderr line is the intended behavior, not a regression.

(2) finish_reason=length is already detected and already raises
LLMInvokeError(kind="truncated", retryable=False) at all three format
sites, but the raise discards the partial content, so a truncated JSON
dies immediately as an INFRA finding with no recovery (factories.py
fold). The default backend model sn-deepseek-flash clamps at ~16384
output tokens regardless of the configured 65536 (gate.yaml:141-165,
measured finish_reason=length 2026-08-11 per the gate.yaml comment), so
this hits production.

Output: carrier exception + continuation helper + thread-safe breaker
with pre-dispatch fail-fast + TTFT emit, all TDD with named tests;
cli.py/factories.py wiring; no new dependencies (stdlib only).
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/ROADMAP.md (Phase 48 entry, lines 389-396)
@.planning/phases/phase-48/RESEARCH.md (source of truth for design decisions 0-5; its
  Open Questions section has been back-annotated with RESOLVED -> D-N markers and
  its stream:false claim corrected to name bonsai, this revision)
@.planning/todos/pending/stream-ttft-truncation-continuation-20260816.md (design doc)

Code (all line refs re-verified against disk this session, 2026-08-16):
@src/code_forge/llm_invoke.py
  - _read_sse 375-437: assembles stream, emits NOTHING; finish_reason from
    final chunk 420-421; error-only chunks returned as-is 424-425.
  - llm_invoke public signature 785-792.
  - _invoke_api 1015-1022 signature; retry loop 1065-1197; the except
    handler at 1168-1170 re-raises non-retryable errors BEFORE any recovery
    could run; empty-content check 1114-1128; parse pipeline
    _strip_fences + json.loads + _extract_json_from_text 1140-1158
    (commit f91605b); usage key mapping per format 1072-1091.
  - LLMResult 42-48: frozen dataclass; is_truncated exists, never set
    True in src.
  - LLMInvokeError 51-77: kind field documented for dispatch decisions.
  - Truncation detection sites: openai 1310-1356 (three raise variants),
    anthropic 1443-1456, vertex 1604-1618. All raise kind="truncated",
    retryable=False, and all discard content/usage_data.
  - _invoke_openai 1226-1231, _invoke_anthropic 1361-1366, _invoke_vertex
    1475-1479 (vertex takes no api_key).
  - _strip_fences 666-684; _extract_json_from_text 687-730 scans "{" only.
@src/code_forge/progress.py
  - emit() 56-72: lock-protected, flushes, swallows errors; _elapsed uses
    time.monotonic() 52-53. Importing progress from llm_invoke is
    cycle-free (progress imports only stdlib). progress.py itself needs NO
    change this phase.
@src/code_forge/machine.py
  - TimeoutCircuitBreaker 75-99: threshold default 5, NO lock,
    record_timeout raises TimeoutBreaker; record_success resets.
@src/code_forge/factories.py
  - build_l1_provider 203: has breaker=None param (timeout breaker);
    LAZY import at 232: "from .llm_invoke import LLMInvokeError,
    llm_invoke" INSIDE the function body -- binds a LOCAL name; the
    provider body 240-289 reads only resolved.git_diff (plus optional
    prompt-context params that default to empty strings).
  - _run_pass 294-310: calls llm_invoke(prompts[idx], backend=backend,
    max_attempts=..., initial_delay_s=...) via the bare local name
    (closure cell, NOT the module attribute); does NOT pass any breaker.
  - Parallel pool 334-345: _run_pass runs in ThreadPoolExecutor worker
    threads when backend.type != "cli" (the lock requirement for the new
    breaker); with backend=None the run is serial in the main thread.
  - Fold 347-425: isinstance(pr, LLMInvokeError) branch records INFRA
    findings and calls breaker.record_timeout()/record_other_error()/
    record_success(); an unknown Exception lands in the UNEXPECTED branch.
@src/code_forge/cli.py
  - 3004: breaker = TimeoutCircuitBreaker(threshold=5), passed into
    build_l1_provider(... breaker=breaker ...).
  - 1649: catches TimeoutBreaker at run level (abort semantics).
@src/code_forge/cross_repo.py
  - 284-330: SECOND run-orchestration site -- inside _thread_fn (nested
    in run_cross_repo, line 170), the if is_primary: branch constructs
    TimeoutCircuitBreaker(threshold=5) and calls build_l1_provider.
    Explicitly OUT of scope this phase (see Contract non-goals).
@src/code_forge/mcp_server.py
  - 959-961: fallback whitelist exc.kind in ("truncated", "empty",
    "stub_model", "no_json").
@.code-forge/gate.yaml
  - 141-165: default backend "deepseek", model sn-deepseek-flash,
    max_tokens 65536, stream false, reasoning_effort low, base_url
    https://192.168.100.10:20128, api_key_env OMNIROUTE_API_KEY. Comment
    records: truncates at ~16384 output, finish_reason=length, measured
    2026-08-11 on the verify-threshold round-7 review.
  - 426-434: backend "bonsai", stream: true (the live streaming backend;
    openai format so its stream path runs through _invoke_openai ->
    _read_sse at llm_invoke.py:1256-1261).
@tests/test_llm_invoke.py
  - TestTruncationDetection 1401+; SenseNova clamp fixture 1505-1510.
  - _mock_body/_openai_body 1657-1669; side_effect multi-call pattern
    1715-1724; zero-output regression
    test_null_content_with_length_still_reports_truncated 1726-1743
    (asserts call_count == 1).
  - _make_api_backend 2410-2414; _mock_ok_response 2417-2429.
  - no_json retry tests patching code_forge.llm_invoke._invoke_openai
    2673-2740.
  - _sse_lines 3276-3282; TestReadSSE 3285+.
  - is_truncated sampling assertion 3436-3448.
</context>

<contract>
Inputs (read-only): gate.yaml backend configs; existing detection sites;
existing test fixtures.

Outputs (files touched):
- src/code_forge/llm_invoke.py
  - New: class _TruncatedResponse(LLMInvokeError) carrying content,
    usage_data, resolved_cap.
  - New: class TruncationBreaker (thread-safe; tripped property,
    check_tripped(), record_truncation(), record_success()) and its raise
    type TruncationBreakerError(LLMInvokeError, kind="truncated").
  - New: helper _continue_truncated(prompt, backend, api_key, timeout_s,
    truncated, expected_keys, budget=2, breaker=None) -> tuple[dict,
    Usage] | None (None = zero-output guard; caller re-raises).
  - Modified: the three raise sites construct _TruncatedResponse; the
    _invoke_api except handler runs continuation before the retryable
    gate; _invoke_api loop entry calls breaker.check_tripped();
    llm_invoke/_invoke_api gain continuation_breaker=None; _read_sse
    emits first-token progress.
- src/code_forge/cli.py: construct TruncationBreaker beside
  TimeoutCircuitBreaker; pass into build_l1_provider.
- src/code_forge/factories.py: build_l1_provider gains
  continuation_breaker=None; _run_pass passes it into llm_invoke.
- tests/test_llm_invoke.py: new fixtures + test classes (see Test plan).
- .planning/phases/phase-48/a1_probe.py: T0 diagnostic script (kept on
  disk after the phase).

Explicit non-goals (do NOT build, do NOT modify):
- progress.py: NO change. emit() is used as-is.
- Do NOT change any backend stream flag, raise any provider cap, or touch
  gate.yaml.
- Do NOT build JSON auto-repair beyond the existing _strip_fences +
  json.loads + _extract_json_from_text pipeline.
- Do NOT add continuation to invoke_sampling (the sampling path keeps its
  current kind="truncated" raise, llm_invoke.py:1688-1694).
- Do NOT add per-chunk finish_reason detection inside _read_sse.
- Do NOT implement the no_json-exhaustion extension point (undetected
  truncation where the provider reports "stop" on cut JSON). Known gap,
  documented; a follow-up phase can classify "{"-prefixed content as
  truncation-suspect after no_json retries exhaust.
- Do NOT add a pass-name label parameter to llm_invoke for TTFT
  (factories.py:296 already emits the pass-name line immediately before
  the call).
- Do NOT wire cross_repo.py (W-3 decision). cross_repo.py:302-309 is a
  second run-orchestration site, but (a) its L1 passes flow through the
  SAME build_l1_provider/_run_pass/llm_invoke path, so per-call recovery
  -- this phase's core mechanism -- applies there automatically via the
  fresh-per-call default breaker (continuation_breaker=None); (b) the
  site lives inside _thread_fn, a per-primary-repo thread closure, so a
  breaker constructed there has per-primary-repo lifetime, not per-run
  lifetime -- a semantic difference from cli.py's per-run breaker that
  deserves its own decision rather than a copy-paste; (c) wiring it would
  need a heavy run_cross_repo integration test (threads + ExitStack +
  per-repo cwds) for a 3-line change. Follow-up candidate: decide
  per-run vs per-primary-repo breaker lifetime for cross-repo mode, then
  wire with a test. Recorded in 48-01-SUMMARY.md.
- VALIDATION.md: none. Repo convention -- current phases omit
  VALIDATION.md (phase-45, the most recent multi-plan phase, ships only
  PLAN/SUMMARY files; verification evidence lives in each plan's
  SUMMARY.md). This phase follows that convention.
</contract>

<decisions>

## D-1: Breaker counts ALL truncation events, threshold 5 (researcher open question 1 / A3)

Chosen: TruncationBreaker.record_truncation() fires on every truncation
event -- recovered or not; record_success() (a clean, non-truncated call)
resets the count to 0. Threshold 5, mirroring TimeoutCircuitBreaker
(machine.py:75-99).

Rejected: counting only failed recoveries. A backend that always
truncates but always recovers would never trip, while silently costing
2-3x tokens per pass forever. The breaker exists to detect a
systematically under-capped backend; that is visible in the truncation
rate, not only in recovery failures (RESEARCH.md Decision 4).
Threshold 5 (not 3): with continuation active most truncations recover,
and a round with 3 recovering passes is a successful round -- tripping at
3 would abort healthy runs. 5 events means "this backend + diff
combination needs operator action", symmetric with the timeout breaker's
"review cannot converge" semantics.

## D-2: Exhaustion keeps kind="truncated" with a distinguishing message (open question 2)

Chosen: on continuation exhaustion raise LLMInvokeError with
kind="truncated", retryable=False, message exactly:
"output truncated at provider cap; continuation exhausted after N
attempts" where N = the number of continuation attempts actually made,
which on exhaustion equals the configured budget (the loop runs exactly
`budget` attempts and every one failed). This N definition is shared by
the T3 GREEN spec -- no divergence.

Rejected: a new kind="truncation_exhausted". (a) mcp_server.py:959-961
fallback whitelist includes "truncated"; truncation is precisely the case
where falling back to a different backend helps, so a new kind would
silently remove fallback eligibility where it is most wanted. (b) The CLI
fold (factories.py:347-369) branches only on is_timeout, not kind, so no
consumer would read the new kind. (c) kind stays a dispatch class, the
message is what humans read; the message is distinguishing. The existing
message-substring assertions in TestTruncationDetection stay green
because the distinguishing message still contains "truncated".

## D-3: One-call diagnostic probe included as T0, NON-BLOCKING (open question 3)

Chosen: include T0 -- one real llm_invoke call against the default
deepseek backend (sn-deepseek-flash) with a prompt engineered to exceed
16384 output tokens, recording the raised kind.

Reason: researcher assumption A1 (sn-deepseek-flash reports
finish_reason=length on its clamp) is the load-bearing assumption for the
whole recovery path. It is grounded -- gate.yaml:141-165 already records
"finish_reason=length, measured 2026-08-11" -- but RESEARCH.md's validity
note warns gateway caps can change without notice. The probe is a drift
check, not the source of truth: if kind=truncated, A1 confirmed; if
kind=no_json, the recovery ships anyway (correct for any backend that
reports length) and the gap is recorded for the no_json extension point;
if the probe cannot run (missing OMNIROUTE_API_KEY, gateway down), that
is recorded honestly and execution proceeds. The probe never blocks a
task; its result goes into 48-01-SUMMARY.md.

## D-4: Continuation covers all three formats via one shared helper (open question 5)

Chosen: _continue_truncated is format-agnostic and re-invokes the same
per-format function (_invoke_openai/_invoke_anthropic/_invoke_vertex)
with a fresh short user-role prompt. The carrier is raised at all three
sites, so all three formats recover through the same code path.

Rejected: openai-only first. The raise sites exist in all three helpers;
the marginal cost is one fixture per format. openai-only would leave
anthropic/vertex users on the dead-end raise this phase exists to remove.
Note _invoke_vertex takes no api_key (llm_invoke.py:1475-1479); the
helper passes api_key only to the two formats that take it.

## D-5: Layer 1 (raise request max_tokens) dropped (A4)

Chosen: drop Layer 1 entirely, per RESEARCH.md Decision 2. Every current
backend pins max_tokens at or above its provider's real cap (gate.yaml:
deepseek 65536 vs 16384 clamp; sn-67-flash-lite 65536, no clamp), and
llm_invoke.py:1319-1331 already detects the clamped-below case and tells
the operator raising will not help.

Rejected: keeping a heuristic raise. It could only fire when the operator
deliberately set a low cap -- in which case raising it contradicts the
operator. YAGNI; continuation is the real fix for the observed case.

## D-6: TTFT message without a pass-name label (A5)

Chosen: _read_sse emits progress.emit("backend %s: first token" %
backend_name) exactly once, flag-guarded, on the first chunk whose
delta.content is non-empty. This is a live fix for bonsai
(gate.yaml:434, stream: true); post-T1 every bonsai pass gains exactly
one stderr line, which is intended.

Rejected: threading a pass-name parameter into llm_invoke for a "pass
<name>:" label. factories.py:296 already emits "pass %s: calling %s"
immediately before the call, so the preceding stderr line supplies the
pass context and events interleave readably. A label parameter would
touch every llm_invoke call site (cli.py:900,993,2127; factories.py:300;
falsify_real.py:44; daemon_state.py:409,450; contract_loader.py:279).
Periodic byte counters (design doc "optional") deferred -- more stderr
noise, low value.

## D-7: Continuation request is a fresh short request, not multi-turn replay

Chosen: continuation prompt = instruction + fenced tail of the partial,
tail bounded to the last 2000 chars, inserted verbatim. Prompt text
exactly (ASCII):

  The JSON output below was cut off by an output token limit.
  Continue the JSON output from where it was cut off.
  Emit ONLY the continuation; no recap; no preamble.
  <partial>
  %s
  </partial>

Same model + backend; request sent through the same per-format function.

Rejected: multi-turn replay (messages = [original, assistant partial,
"continue"]). It resends the full original prompt (a review diff can be
100KB+), whose affordability depends on provider prefix caching that
forge's gateway routes do not guarantee; models also tend to explain
rather than continue when the assistant turn ends mid-JSON. The
short-request shape bounds continuation input tokens to the tail.

## D-8: Zero-output guard before any continuation (None-safe)

Chosen: the partial is usable iff the expression
  not truncated.content or not truncated.content.strip() or "{" not in truncated.content
is FALSE -- i.e., content is a non-empty string, has non-whitespace
text, and contains "{". The None-safe form matters: _invoke_openai can
raise with content=None (tests:1726-1743 fixture uses
_openai_body(None, finish="length")), and `None.strip()` would
AttributeError before the guard can decide. Forge envelopes are dicts;
_extract_json_from_text scans "{" only (llm_invoke.py:711-712).
Non-usable partial -> helper returns None, caller re-raises the original
_TruncatedResponse unchanged, no continuation issued (the entry
record_truncation event still counts per D-1).

Rejected: also accepting "[". Bare arrays never validate as forge
envelopes (dicts with expected_keys), so a "["-shaped partial cannot
recover toward a valid result. Continuing a nothing-output truncation
replays the whole chain and truncates again (OpenCode lesson; design doc
lines 32-39). Regression anchor: existing
test_null_content_with_length_still_reports_truncated asserts
call_count == 1.

## D-9: Detection stays at the assembled-dict level; the change is a carrier, not new detection

Chosen: keep the three detection sites where they are (llm_invoke.py
1310-1356 openai, 1443-1456 anthropic, 1604-1618 vertex); convert each
raise into _TruncatedResponse carrying content, usage_data, resolved_cap.
The stream path already normalizes into the same dict shape
(llm_invoke.py:427-437), so it flows through the same detection.

Rejected: per-chunk finish_reason detection inside _read_sse.
finish_reason arrives only on the final chunk; per-chunk logic duplicates
what the assembled dict already yields and would split the raise into two
paths (RESEARCH.md Decision 1).

## D-10: Breaker raise is an LLMInvokeError subclass, plus a pre-dispatch fail-fast check

Chosen:
- TruncationBreakerError subclasses LLMInvokeError with kind="truncated",
  retryable=False.
- TruncationBreaker exposes a `tripped` property (count >= threshold,
  read under the lock) and a `check_tripped()` method that raises
  TruncationBreakerError when tripped, WITHOUT incrementing.
- record_truncation() increments under the lock and then performs the
  same trip check, so the threshold-crossing event raises immediately.
- _invoke_api calls breaker.check_tripped() at the TOP of the retry
  loop body, before any dispatch: once tripped, subsequent calls fail
  fast with zero network calls (this is what makes truth 5 true -- the
  checker's blocker). The raise inside _continue_truncated propagates
  out of the except handler (a raise inside a handler body is not
  re-caught by that same handler), so the call fails with the breaker
  error and no continuation request is issued.
- The error flows through the fold's existing isinstance(pr,
  LLMInvokeError) INFRA branch (factories.py:347-369) -- call-abort
  semantics: the run records an INFRA finding with the actionable
  message, no cli.py catch site needed.

Rejected: mirroring TimeoutBreaker (a plain Exception caught at
cli.py:1649 for run-abort). A plain exception raised inside _run_pass
would land in the fold's UNEXPECTED branch (factories.py:376-395) and
read as a bug, not a diagnosis. Also rejected: reusing TimeoutCircuitBreaker
itself -- its hardcoded timeout message is misleading for truncation, it
is main-thread-only (no lock), and its run-abort semantics are owned by
machine.py.

## D-11: Recovered results set is_truncated=True and sum usage with per-format keys

Chosen: _continue_truncated returns (parsed_content, Usage) where Usage
is the field-wise sum of the original response's usage_data plus every
continuation call's usage_data, mapped with the SAME per-format key pair
_invoke_api already uses (llm_invoke.py:1072-1091): openai format sums
prompt_tokens/completion_tokens; anthropic and vertex sum
input_tokens/output_tokens. _invoke_api wraps the result as
LLMResult(content=parsed, usage=summed_usage,
duration_s=time.monotonic() - start, is_truncated=True) -- duration is
the real elapsed time (LLMResult is frozen, so the helper cannot stamp it
honestly; _invoke_api has `start` from line 1059). factories.py:302-311
already prints the summed usage; no consumer change needed.
is_truncated currently has no reader in src (grep: only default + test
assertions), so setting it is honest metadata, not new behavior; the
existing sampling test asserting is_truncated is False (tests:3436-3448)
is unaffected because sampling never reaches this path.
</decisions>

<tasks>

Wave structure (W-5): T0 is a wave-0 diagnostic -- it produces
a1_probe.py plus a verdict line and makes NO source commit. T1-T4 are
four sequential, separately committed steps: one atomic commit per task,
each with its own RED -> GREEN -> REFACTOR cycle and its own bug-
injection proof. Dependencies: T1 standalone; T2 needs T1 (uses the
carrier); T3 needs T2 (uses carrier + breaker); T4 needs T3 (wires the
parameter T3 added). T1 is placed first because STREAM-VISIBLE precedes
TRUNCATION-RECOVER in the ROADMAP requirement list; it has no dependency
on the truncation work.

<task type="auto">
  <name>T0: A1 probe -- does sn-deepseek-flash still report finish_reason=length?</name>
  <files>.planning/phases/phase-48/a1_probe.py</files>
  <facts>
    - FACT: gate.yaml default backend "deepseek" uses model
      sn-deepseek-flash, base_url https://192.168.100.10:20128,
      api_key_env OMNIROUTE_API_KEY, max_tokens 65536, reasoning_effort
      low (gate.yaml:141-165).
    - FACT: gate.yaml comments record finish_reason=length truncation at
      ~16384 output tokens for this route, measured 2026-08-11. A1 is
      therefore repo-recorded FACT; the probe checks for drift.
  </facts>
  <assumptions>
    - ASSUMPTION: OMNIROUTE_API_KEY is set in the execution environment
      and the gateway at 192.168.100.10:20128 is reachable. If not, the
      probe records "not run" and the phase proceeds -- the unit tests
      never depend on it.
  </assumptions>
  <action>
    Write .planning/phases/phase-48/a1_probe.py (inside the phase dir;
    nothing outside it). The script:
    - Builds a BackendConfig matching the gate.yaml deepseek entry
      (name="deepseek", type="api", format="openai",
      base_url="https://192.168.100.10:20128",
      api_key_env="OMNIROUTE_API_KEY", model="sn-deepseek-flash",
      max_tokens=65536, reasoning_effort="low", timeout_s=600).
      If the BackendConfig constructor does not accept a field you used,
      drop that field and note the difference in the output.
    - Calls llm_invoke(prompt, backend=..., max_attempts=1,
      timeout_s=600) with a prompt engineered to exceed 16384 output
      tokens: "Return a JSON object with a 'findings' array of 1500
      entries. Each entry must be an object with id, severity, file,
      line, and a description field of at least 150 characters of varied
      prose. Emit nothing but the JSON."
    - Catches LLMInvokeError and prints exactly one verdict line of the
      form "A1_PROBE kind=<kind> msg_first_200=<message[:200]>" plus the
      exception's other fields; on success prints "A1_PROBE
      unexpected_success output_tokens=<n>". On any non-LLMInvokeError
      failure prints "A1_PROBE not_run reason=<exception text>".
    - Never prints the API key, never prints more than 200 chars of any
      message (messages can contain prompt echoes).
    Run it: python3 .planning/phases/phase-48/a1_probe.py (from repo
    root so code_forge imports). Record the verdict line verbatim in
    48-01-SUMMARY.md.
    Interpretation: kind=truncated -> A1 confirmed; kind=no_json -> A1
    refuted for the current gateway state, record it and note the no_json
    extension point as a follow-up candidate; not_run -> record honestly.
    All three outcomes leave the plan's tasks unchanged.
  </action>
  <verify>
    <automated>cd /home/houminxi/code/forge && python3 .planning/phases/phase-48/a1_probe.py</automated>
  </verify>
  <done>
    a1_probe.py exists in phase-48/, runs once, and prints one A1_PROBE
    verdict line (truncated / no_json / not_run); verdict recorded in
    48-01-SUMMARY.md. One bounded call only (max_attempts=1, 600s cap).
  </done>
</task>

<task type="auto" tdd="true">
  <name>T1: TTFT emit in _read_sse (STREAM-VISIBLE)</name>
  <files>src/code_forge/llm_invoke.py, tests/test_llm_invoke.py</files>
  <read_first>
    - src/code_forge/llm_invoke.py lines 375-437 (_read_sse)
    - src/code_forge/progress.py lines 52-72 (emit: monotonic clock,
      lock, flush, swallow-errors)
    - tests/test_llm_invoke.py lines 3270-3351 (_sse_lines fixture,
      TestReadSSE)
    - .code-forge/gate.yaml lines 426-434 (bonsai: the production
      stream:true backend this task un-stalls)
  </read_first>
  <facts>
    - FACT: _read_sse currently emits nothing; grep "progress" in
      llm_invoke.py returns zero hits.
    - FACT: bonsai (gate.yaml:434) runs stream: true, so the silent
      stall is live today; bonsai is openai-format, so its stream path
      is _invoke_openai -> _read_sse (llm_invoke.py:1256-1261).
    - FACT: progress.emit is thread-safe and non-fatal (progress.py:56-72);
      importing it from llm_invoke cannot create an import cycle
      (progress.py imports only stdlib).
  </facts>
  <assumptions>
    - ASSUMPTION: the first chunk whose delta.get("content") is non-empty
      is the first user-visible token. Standard OpenAI SSE semantics; if
      a gateway sends an empty-string content delta first, the guard
      still waits for the first non-empty one.
  </assumptions>
  <behavior>
    - Test 1 (test_first_token_emit): a stream of
      {role delta}, {content "Hello"}, {content " world"} chunks with
      backend_name="test" emits exactly one progress event whose message
      contains "backend test: first token", and no emit occurs before the
      first content-bearing chunk (assert emit call order via a
      patch("code_forge.llm_invoke.progress.emit") spy: zero calls while
      only role/reasoning chunks have been consumed).
    - Test 2 (test_no_emit_without_content): a stream of reasoning-only
      chunks followed by an error chunk emits zero progress events and
      still returns the error dict for _check_body_error.
    - Test 3 (existing): all current TestReadSSE tests stay green
      (test_assembles_content, test_message_shape_not_delta,
      test_reasoning_content_discarded, test_empty_delta_no_crash,
      test_error_only_chunk_no_crash, test_stream_on_anthropic_raises,
      test_stream_on_vertex_raises).
  </behavior>
  <action>
    RED: add test_first_token_emit and test_no_emit_without_content to
    TestReadSSE (tests/test_llm_invoke.py). Both use the existing
    _sse_lines fixture and patch("code_forge.llm_invoke.progress.emit")
    as a spy (import progress at module level in llm_invoke.py as
    "from . import progress" so the patch path is stable; call
    progress.emit(...), never "from .progress import emit" which defeats
    the patch). Run them; both must FAIL (no emit exists).

    GREEN: in _read_sse (llm_invoke.py:375-437), before the loop set
    first_emitted = False; inside the delta walk, when delta.get("content")
    is non-empty and not first_emitted, set first_emitted = True and call
    progress.emit("backend %s: first token" % backend_name) BEFORE
    appending to content_parts. Nothing else in the assembly loop changes:
    no per-chunk timer, no byte counter, no timestamp in the message (emit
    already stamps t+Ns, progress.py:69).

    REFACTOR (if needed): none expected; keep the diff minimal.

    Bug-injection proof (I3): delete the first_emitted flag guard (emit
    unconditionally on every content chunk) -> test_first_token_emit must
    FAIL (more than one emit). Revert -> PASS. Record the inject/FAIL/
    revert/PASS result in 48-01-SUMMARY.md.

    Note: after this task merges, bonsai passes show one extra stderr
    line per call. That is the fix working, not noise -- do not "fix" it.
  </action>
  <verify>
    <automated>cd /home/houminxi/code/forge && python3 -m pytest tests/test_llm_invoke.py::TestReadSSE -x -q</automated>
  </verify>
  <acceptance_criteria>
    - test_first_token_emit and test_no_emit_without_content pass
    - All pre-existing TestReadSSE tests pass unchanged
    - grep -n "progress" src/code_forge/llm_invoke.py shows the module
      import and the emit call (smoke check only; the tests are the gate)
    - Injection I3 executed and recorded
  </acceptance_criteria>
  <done>
    _read_sse emits exactly one first-token progress event per streamed
    call, named by backend, on the first non-empty content delta; no
    event when only reasoning/error chunks arrive. Non-stream path
    untouched (it never calls _read_sse).
  </done>
</task>

<task type="auto" tdd="true">
  <name>T2: _TruncatedResponse carrier + TruncationBreaker classes</name>
  <files>src/code_forge/llm_invoke.py, tests/test_llm_invoke.py</files>
  <read_first>
    - src/code_forge/llm_invoke.py lines 51-77 (LLMInvokeError), 42-48
      (LLMResult), 1310-1356 / 1443-1456 / 1604-1618 (the three raise
      sites)
    - src/code_forge/machine.py lines 64-99 (TimeoutCircuitBreaker
      pattern to mirror, plus its lack of a lock)
    - src/code_forge/factories.py lines 334-345 (worker-thread pool --
      why the new breaker needs a threading.Lock)
    - tests/test_llm_invoke.py lines 1401-1646 (TestTruncationDetection)
    - tests/test_machine_local.py lines 704-788 (breaker unit test style)
  </read_first>
  <facts>
    - FACT: the three raise sites currently construct plain
      LLMInvokeError and discard content/usage_data/resolved_cap.
    - FACT: TestTruncationDetection asserts kind, retryable, and message
      substrings on those raises; subclassing LLMInvokeError with the
      same kwargs keeps every assertion passing.
    - FACT: TimeoutCircuitBreaker has no lock because it is touched only
      in the main-thread fold (factories.py:370-425); the truncation
      breaker is touched inside ThreadPoolExecutor worker threads
      (factories.py:334-345), so the lock is a real requirement, not a
      copy of the pattern.
  </facts>
  <assumptions>
    - ASSUMPTION (A4): no current backend needs a "raise the cap" layer;
      gate.yaml pins every max_tokens at or above provider caps. If a
      future backend configures below its provider cap, Layer 1 is a
      possible later addition, not this phase.
  </assumptions>
  <behavior>
    - Test 1 (test_openai_truncation_carries_partial): an openai response
      with content='{"findings": [{"fil', finish_reason="length" raises
      _TruncatedResponse; exc.content == the partial string;
      exc.usage_data == the usage dict; exc.resolved_cap == the resolved
      cap; and kind == "truncated", retryable is False (inherited
      behavior unchanged).
    - Test 2 (test_anthropic_truncation_carries_partial): anthropic
      stop_reason="max_tokens" raises _TruncatedResponse with the same
      field guarantees.
    - Test 3 (test_vertex_truncation_carries_partial): vertex
      stop_reason="max_tokens" raises _TruncatedResponse with the same
      field guarantees.
    - Test 4 (test_breaker_records_and_resets): TruncationBreaker(5):
      record_truncation() x4 does not raise and count == 4;
      record_success() resets count to 0.
    - Test 5 (test_breaker_trips_and_check_tripped): the 5th
      record_truncation() raises TruncationBreakerError; the error is an
      LLMInvokeError with kind="truncated", retryable=False, and a
      message naming the truncation breaker (not the timeout wording).
      After the trip: count stays 5, tripped property is True, and
      check_tripped() raises again WITHOUT incrementing.
    - Test 6 (test_breaker_thread_safe_increments): 8 threads each
      calling record_truncation() 10 times on a high-threshold breaker
      ends with count == 80 (the lock makes increments atomic).
    - Test 7 (existing): TestTruncationDetection in full stays green.
  </behavior>
  <action>
    RED: add class TestTruncationCarrier (tests 1-3) and class
    TestTruncationBreaker (tests 4-6) to tests/test_llm_invoke.py.
    Fixture: extend the _mock_body/_openai_body pattern
    (tests:1657-1669) -- a truncated body helper
    _truncated_openai_body(partial) with finish="length" and
    completion_tokens=16384 mirroring the SenseNova clamp fixture at
    tests:1505-1510. Run; all six must FAIL (symbols do not exist).

    GREEN: in llm_invoke.py, after LLMInvokeError (line 77):
    - class _TruncatedResponse(LLMInvokeError): subclass with
      __init__(self, message, content, usage_data, resolved_cap, **kw)
      forwarding kw to super; store the three fields. The three raise
      sites (openai 1310-1356, anthropic 1443-1456, vertex 1604-1618)
      construct _TruncatedResponse with the SAME message/kwargs they use
      today plus content=content, usage_data=usage_data,
      resolved_cap=resolved_cap. Do not change any message string, kind,
      or retryable value.
    - class TruncationBreakerError(LLMInvokeError): constructed with
      kind="truncated", retryable=False, and a message of the form
      "backend hit %d truncations (>=%d) this run; review output keeps
      hitting the provider cap. Raise output_ceiling or switch backends."
      (% (count, threshold)).
    - class TruncationBreaker: __init__(threshold=5) with
      self.threshold, self._count, self._lock = threading.Lock();
      record_truncation() increments under the lock, then performs the
      trip check (raise TruncationBreakerError when count >= threshold);
      record_success() resets to 0 under the lock; `count` property reads
      under the lock; `tripped` property returns count >= threshold under
      the lock; check_tripped() performs the trip check under the lock
      WITHOUT incrementing. A record_truncation() that trips keeps the
      count (it does not reset), so every later record or check also
      raises -- fail-fast. Do NOT subclass or reuse TimeoutCircuitBreaker.

    REFACTOR: none expected.

    Bug-injection proof (I4): remove the raise from record_truncation
    (or remove the lock) -> test_breaker_trips_and_check_tripped (or
    test_breaker_thread_safe_increments) must FAIL. Revert -> PASS.
    Record results in 48-01-SUMMARY.md.
  </action>
  <verify>
    <automated>cd /home/houminxi/code/forge && python3 -m pytest tests/test_llm_invoke.py::TestTruncationCarrier tests/test_llm_invoke.py::TestTruncationBreaker tests/test_llm_invoke.py::TestTruncationDetection -x -q</automated>
  </verify>
  <acceptance_criteria>
    - All 6 new tests pass; TestTruncationDetection unchanged and green
    - grep -n "_TruncatedResponse" src/code_forge/llm_invoke.py shows the
      class plus three raise sites
    - grep -n "threading.Lock" src/code_forge/llm_invoke.py shows the
      lock in TruncationBreaker.__init__
    - grep -n "check_tripped" src/code_forge/llm_invoke.py shows the
      method definition
    - Injection I4 executed and recorded
  </acceptance_criteria>
  <done>
    The three format helpers raise a payload-carrying _TruncatedResponse
    (content, usage_data, resolved_cap) with kind/retryable/messages
    unchanged; TruncationBreaker is a locked, threshold-5 counter with a
    tripped property and a check_tripped() fail-fast method, whose trip
    raises an LLMInvokeError-subclass with kind="truncated".
  </done>
</task>

<task type="auto" tdd="true">
  <name>T3: _continue_truncated helper + _invoke_api integration (TRUNCATION-RECOVER core)</name>
  <files>src/code_forge/llm_invoke.py, tests/test_llm_invoke.py</files>
  <read_first>
    - src/code_forge/llm_invoke.py lines 1015-1201 (_invoke_api: retry
      loop, empty check, parse pipeline, except handler at 1168-1170)
    - src/code_forge/llm_invoke.py lines 666-730 (_strip_fences,
      _extract_json_from_text)
    - src/code_forge/llm_invoke.py lines 785-865 (llm_invoke dispatcher)
    - tests/test_llm_invoke.py lines 2673-2740 (the
      patch("code_forge.llm_invoke._invoke_openai", side_effect=...)
      seam for call counting without HTTP mocks)
  </read_first>
  <facts>
    - FACT: the except handler checks "if not exc.retryable or attempt ==
      max_attempts - 1: raise" (1169-1170) BEFORE anything else; a
      _TruncatedResponse (retryable=False) would re-raise before recovery
      runs. The continuation branch must be checked first (RESEARCH.md
      Pitfall 1).
    - FACT: max_attempts retries are for transient failures (429/5xx/
      empty/no_json); truncation is deterministic per prompt, so
      continuation must NOT consume max_attempts (RESEARCH.md Decision 2).
    - FACT: _invoke_api resolves api_key before the loop (1025-1050);
      vertex runs with api_key = "".
    - FACT: LLMResult is frozen (llm_invoke.py:41), so duration_s cannot
      be stamped after construction -- _invoke_api wraps the helper's
      return with the real elapsed time.
  </facts>
  <assumptions>
    - ASSUMPTION: a model instructed to emit only the continuation of its
      own cut-off JSON can usually do so; failure is bounded and
      observable (budget 2, then exhaustion raise). No similarity
      detection -- fixed counter only (oscillation is bounded, not
      detected).
  </assumptions>
  <behavior>
    - Fixture strings (used by tests 1-6 and reused in T4): partial =
      '{"findings": [{"file": "a.c",' and tail =
      '"line": 1, "severity": "LOW"}]}' so that partial + tail
      concatenates to valid JSON. Continuation usage dicts:
      {"prompt_tokens": 5, "completion_tokens": 20}.
    - Test 1 (test_continuation_success): patch
      code_forge.llm_invoke._invoke_openai with side_effect = [raise
      _TruncatedResponse(partial), return (tail, usage2)]. llm_invoke
      returns content == json.loads(partial + tail) (via the
      _strip_fences + json.loads pipeline), usage == the summed Usage,
      and is_truncated is True. Total call count == 2.
    - Test 2 (test_continuation_exhausted): side_effect = [raise
      _TruncatedResponse(partial), raise _TruncatedResponse(partial2),
      raise _TruncatedResponse(partial3)] -- the two continuation calls
      both truncate again (budget 2 spent). Final raise is
      LLMInvokeError, kind="truncated", retryable=False, message matches
      "continuation exhausted after 2 attempts"; call count == 3.
    - Test 3 (test_zero_partial_raises_no_continuation): side_effect =
      [raise _TruncatedResponse with content=None] -- raises the original
      error unchanged, call count == 1 (mirrors the existing regression
      at tests:1726-1743, now exercising the new helper path; the
      None-safe guard must not AttributeError).
    - Test 4 (test_no_brace_partial_raises_no_continuation):
      _TruncatedResponse with content="prose with no JSON" raises, call
      count == 1.
    - Test 5 (test_combined_parse_failure_counts_as_attempt): first
      response truncated partial; continuation returns prose that makes
      partial + tail unparseable and with no "{"-extractable envelope.
      That counts as a failed continuation (budget decrement); a second
      same-shape attempt then raises "continuation exhausted after 2
      attempts"; call count == 3.
    - Test 6 (test_continuation_does_not_consume_max_attempts): run with
      max_attempts=2 where the first attempt truncates and the
      continuation succeeds; the call succeeds (not exhausted) -- proving
      continuation runs on its own budget.
    - Test 7 (test_pre_tripped_breaker_raises_before_dispatch): trip a
      TruncationBreaker(5) upfront (5 records, catching the 5th raise),
      pass it as continuation_breaker into llm_invoke; patch
      _invoke_openai as a spy. llm_invoke raises TruncationBreakerError
      (kind="truncated") and spy.call_count == 0 -- the pre-dispatch
      check_tripped fired before any network call.
    - Test 8 (existing): test_null_content_with_length_still_reports_
      truncated (tests:1726-1743) stays green with call_count == 1.
  </behavior>
  <action>
    RED: add class TestTruncationRecover with tests 1-7. Fixtures:
    _truncated_openai_body(partial) and _continuation_body(tail) built on
    _mock_body/_openai_body (tests:1657-1669). For the patch seam, mirror
    tests:2673-2740: patch("code_forge.llm_invoke._invoke_openai",
    side_effect=<list of raises/returns>) -- _invoke_api and
    _continue_truncated both call the module-level name, so this patches
    the continuation dispatch too. Run; all seven must FAIL.

    GREEN: in llm_invoke.py:
    - Add module constant CONTINUE_PROMPT with the exact text from
      Decision D-7.
    - Add helper _continue_truncated(prompt, backend, api_key, timeout_s,
      truncated, expected_keys, budget=2, breaker=None) ->
      tuple[dict, Usage] | None. Logic: (a) record the event: if breaker
      is not None, breaker.record_truncation() -- this covers the
      zero-output case too (D-1 counts every truncation event); a trip
      raises here and propagates out of the call (a raise inside the
      except-handler body is not re-caught by that handler; no
      continuation request is issued); (b) zero-output guard (D-8,
      None-safe): if not truncated.content or not
      truncated.content.strip() or "{" not in truncated.content: return
      None (caller re-raises the original); (c) loop up to budget times:
      build tail = truncated.content[-2000:] inserted VERBATIM into
      CONTINUE_PROMPT (no re-encoding, no mid-escape truncation --
      RESEARCH.md Pitfall 4); dispatch by backend.format calling
      _invoke_openai(prompt_c, backend, api_key, timeout_s) /
      _invoke_anthropic(prompt_c, backend, api_key, timeout_s) /
      _invoke_vertex(prompt_c, backend, timeout_s); a _TruncatedResponse
      raised by the continuation counts as a failed attempt
      (record_truncation again, budget decrement, continue); on
      (cont, usage_c): combined = truncated.content + cont, run
      _strip_fences + json.loads + _extract_json_from_text(combined,
      expected_keys); parse failure is a failed attempt (budget
      decrement, continue); parse success sums usage_data field-wise
      with the format's key pair (openai: prompt_tokens/completion_tokens;
      anthropic/vertex: input_tokens/output_tokens -- same mapping as
      llm_invoke.py:1072-1091) over truncated.usage_data and every
      usage_c, and returns (parsed, Usage). After budget exhausted:
      raise LLMInvokeError with the D-2 message ("output truncated at
      provider cap; continuation exhausted after N attempts" where N =
      the number of continuation attempts actually made, equal to the
      configured budget on exhaustion: the loop ran exactly `budget`
      failed attempts), kind="truncated", retryable=False.
      No time.sleep between continuation attempts (bounded latency;
      oscillation is handled by the fixed counter).
    - In _invoke_api: add parameter continuation_breaker=None to its
      signature (1015-1022) and a local default: breaker =
      continuation_breaker if continuation_breaker is not None else
      TruncationBreaker() (fresh per-call instance keeps direct callers
      stateless). At the TOP of the "for attempt in
      range(max_attempts):" loop body, BEFORE the inner try:
      breaker.check_tripped() (pre-dispatch fail-fast, D-10). In the
      except LLMInvokeError handler (1168), insert BEFORE the retryable
      gate:
        if isinstance(exc, _TruncatedResponse):
            recovered = _continue_truncated(prompt, backend, api_key,
                timeout_s, exc, expected_keys, breaker=breaker)
            if recovered is not None:
                parsed, usage = recovered
                return LLMResult(content=parsed, usage=usage,
                    duration_s=time.monotonic() - start,
                    is_truncated=True)
      When recovered is None (zero-output guard), fall through so the
      original exc re-raises at the retryable gate exactly as today.
    - In llm_invoke (785-792): add continuation_breaker=None parameter,
      forward to _invoke_api. cli backends ignore it (continuation is
      api-only).

    REFACTOR: none expected.

    Bug-injection proofs: (I1) move the isinstance(_TruncatedResponse)
    branch to AFTER the retryable gate -> test_continuation_success must
    FAIL (truncated raises, no second call). (I2) delete the zero-output
    guard ("{" check) -> test_zero_partial_raises_no_continuation must
    FAIL (a continuation is issued). (I5) drop the usage summation ->
    usage assertion in test_continuation_success must FAIL. (I6) replace
    the exhaustion message with a generic "truncated" message ->
    test_continuation_exhausted must FAIL on the message match. (I8)
    delete the breaker.check_tripped() call at the loop entry ->
    test_pre_tripped_breaker_raises_before_dispatch must FAIL (dispatch
    proceeds, spy called). Execute each inject/FAIL/revert/PASS; record
    all in 48-01-SUMMARY.md.
  </action>
  <verify>
    <automated>cd /home/houminxi/code/forge && python3 -m pytest tests/test_llm_invoke.py::TestTruncationRecover tests/test_llm_invoke.py::TestEmptyContentDetection -x -q</automated>
  </verify>
  <acceptance_criteria>
    - All 7 new tests pass; TestEmptyContentDetection fully green
      (especially test_null_content_with_length_still_reports_truncated)
    - grep -n "_continue_truncated" src/code_forge/llm_invoke.py shows
      the helper definition and its call site inside the except handler
      before the retryable gate (read the except block to confirm
      ordering)
    - grep -n "check_tripped" src/code_forge/llm_invoke.py shows the
      method definition AND the loop-entry call site
    - grep -n "continuation exhausted" src/code_forge/llm_invoke.py >= 1
    - Injections I1, I2, I5, I6, I8 executed and recorded
  </acceptance_criteria>
  <done>
    Truncated responses with usable partial JSON are recovered via up to
    2 continuation calls and return a parsed, usage-summed,
    is_truncated=True result; zero-output and no-brace partials (None
    included) raise the original error with exactly one HTTP call;
    exhaustion raises the distinguishing message with kind="truncated";
    a pre-tripped breaker fails the call before any network request.
  </done>
</task>

<task type="auto" tdd="true">
  <name>T4: Run-level breaker wiring (cli.py + factories.py)</name>
  <files>src/code_forge/cli.py, src/code_forge/factories.py, tests/test_llm_invoke.py</files>
  <read_first>
    - src/code_forge/cli.py line 3004 (breaker construction site) and the
      build_l1_provider call immediately after it
    - src/code_forge/factories.py lines 203-243 (build_l1_provider
      signature, lazy from-import at 232), 240-289 (provider body: reads
      only resolved.git_diff plus optional prompt-context params),
      294-310 (_run_pass llm_invoke call), 334-345 (worker pool; serial
      when backend is None or backend.type == "cli")
    - src/code_forge/llm_invoke.py lines 785-865 (llm_invoke with
      continuation_breaker from T3)
  </read_first>
  <facts>
    - FACT: cli.py:3004 constructs TimeoutCircuitBreaker(threshold=5) and
      passes it to build_l1_provider(breaker=breaker); the truncation
      breaker follows the same route.
    - FACT: _run_pass calls llm_invoke WITHOUT any breaker today; the
      fold applies breaker.record_* AFTER the call returns or raises
      (factories.py:347-425). The truncation breaker must be passed INTO
      llm_invoke so record_truncation fires at event time.
    - FACT: build_l1_provider already accepts breaker=None; adding a
      second optional param cannot break existing call sites.
    - FACT (W-4 seam): build_l1_provider does "from .llm_invoke import
      LLMInvokeError, llm_invoke" at line 232 -- a LAZY from-import that
      binds a LOCAL name; _run_pass closes over that local. Patching
      "code_forge.factories.llm_invoke" (the module attribute) does NOT
      intercept the provider's calls: the closure cell holds the object
      bound at build time and the module attribute is never consulted.
      The intercepting seam is the SOURCE name
      "code_forge.llm_invoke.llm_invoke", patched BEFORE calling
      build_l1_provider so the from-import binds the patched object.
    - FACT: the provider body (240-289) reads only resolved.git_diff
      (plus optional params defaulting to empty), so a fake resolved
      needs exactly one attribute: git_diff (non-empty). With
      backend=None the run executes serially in the main thread
      (is_cli branch, 334-345), keeping the spy deterministic.
  </facts>
  <assumptions>
    - ASSUMPTION: cli.py is the only run-orchestration site that needs a
      shared breaker; cross_repo.py is an explicit non-goal (Contract).
      Other llm_invoke callers (daemon_state.py, contract_loader.py,
      falsify_real.py) stay stateless via the None default. If an MCP job
      path needs run-level memory later, it gets the same one-line
      construction.
  </assumptions>
  <behavior>
    - Test 1 (test_breaker_trips_across_calls): shared
      TruncationBreaker(5) passed as continuation_breaker; patch
      code_forge.llm_invoke._invoke_openai with a callable side_effect
      that alternates: odd invocations raise
      _TruncatedResponse(partial='{"findings": [{"file": "a.c",'),
      even invocations return ('"line": 1, "severity": "LOW"}]}',
      {"prompt_tokens": 5, "completion_tokens": 20}). Run 5 sequential
      llm_invoke calls: calls 1-4 succeed as recovered results
      (is_truncated=True; one truncation event each, count 1..4), call 5
      raises TruncationBreakerError (the record inside the helper trips
      after the initial truncated response; no continuation is issued).
      Assert mock.call_count == 9. Then a 6th call raises
      TruncationBreakerError with mock.call_count STILL == 9 -- the
      pre-dispatch check_tripped fired before any network call (zero new
      network calls after the trip).
    - Test 2 (test_breaker_default_fresh_per_call): two calls WITHOUT
      continuation_breaker each survive a truncation+continuation (no
      cross-call accumulation; the default fresh instance resets every
      call).
    - Test 3 (test_provider_passes_breaker): build the provider with a
      fake resolved (only git_diff="x"; backend=None so execution is
      serial), with the source name ALREADY patched:
        with patch("code_forge.llm_invoke.llm_invoke") as mock_invoke:
            mock_invoke.return_value = LLMResult(
                content={"findings": [], "code_excerpts": []},
                usage=Usage(10, 5))
            provider = build_l1_provider("auto", fake_resolved,
                backend=None, continuation_breaker=breaker_obj)
            provider()
      Order matters (W-4): patch the SOURCE name BEFORE calling
      build_l1_provider so the lazy from-import at factories.py:232
      binds the Mock as the closure value. Assert mock_invoke.call_count
      == 3 (qodo/expert/adversarial passes) and every call in
      call_args_list carries continuation_breaker=<the same breaker_obj>.
      The Mock result is a valid empty envelope, so the fold validates
      and the provider returns cleanly. Do NOT patch
      "code_forge.factories.llm_invoke" -- that name does not intercept
      (see facts).
  </behavior>
  <action>
    RED: add class TestTruncationBreakerWiring with tests 1-3 to
    tests/test_llm_invoke.py (import build_l1_provider from
    code_forge.factories for test 3). Run; all three must FAIL.

    GREEN:
    - cli.py (at the 3004 site): from .llm_invoke import TruncationBreaker
      (or import the module); construct truncation_breaker =
      TruncationBreaker(threshold=5) beside the TimeoutCircuitBreaker;
      pass truncation_breaker=truncation_breaker into build_l1_provider.
    - factories.py: build_l1_provider gains continuation_breaker=None;
      in _run_pass, extend the llm_invoke call to
      llm_invoke(prompts[idx], backend=backend, max_attempts=...,
      initial_delay_s=..., continuation_breaker=continuation_breaker).
    - Nothing else in the fold changes: the tripped
      TruncationBreakerError is an LLMInvokeError and lands in the
      existing INFRA branch (factories.py:347-369), which is the designed
      call-abort semantics (D-10).

    REFACTOR: none expected.

    Bug-injection proof (I7): revert the _run_pass llm_invoke call to not
    pass continuation_breaker -> test_provider_passes_breaker must FAIL.
    Revert -> PASS. Record in 48-01-SUMMARY.md.
  </action>
  <verify>
    <automated>cd /home/houminxi/code/forge && python3 -m pytest tests/test_llm_invoke.py::TestTruncationBreakerWiring -x -q</automated>
  </verify>
  <acceptance_criteria>
    - All 3 new tests pass
    - grep -n "TruncationBreaker" src/code_forge/cli.py >= 1
    - grep -n "continuation_breaker" src/code_forge/factories.py >= 1
    - Injection I7 executed and recorded
    - No existing test regressed (run the module file, see Test plan)
  </acceptance_criteria>
  <done>
    The review run constructs one TruncationBreaker, threads it through
    build_l1_provider into every pass's llm_invoke call; after 5
    truncation events later calls fail fast before any network call, as
    INFRA findings with the truncation message; direct callers without
    the parameter stay stateless.
  </done>
</task>

</tasks>

<test_plan>

## New tests (name -> what it asserts)

| Test | Class | Asserts |
|------|-------|---------|
| test_first_token_emit | TestReadSSE | exactly one progress event, contains "backend test: first token", zero emits before the first content delta |
| test_no_emit_without_content | TestReadSSE | reasoning-only + error stream emits nothing, error dict still returned |
| test_openai_truncation_carries_partial | TestTruncationCarrier | _TruncatedResponse exposes content/usage_data/resolved_cap; kind/retryable unchanged |
| test_anthropic_truncation_carries_partial | TestTruncationCarrier | same for stop_reason=max_tokens |
| test_vertex_truncation_carries_partial | TestTruncationCarrier | same for vertex |
| test_breaker_records_and_resets | TestTruncationBreaker | count semantics + record_success reset |
| test_breaker_trips_and_check_tripped | TestTruncationBreaker | 5th event raises TruncationBreakerError(LLMInvokeError, kind="truncated", retryable=False); count stays; tripped True; check_tripped re-raises without incrementing |
| test_breaker_thread_safe_increments | TestTruncationBreaker | 8 threads x 10 increments == 80 (lock) |
| test_continuation_success | TestTruncationRecover | 2 calls, parsed combined envelope, summed usage, is_truncated=True |
| test_continuation_exhausted | TestTruncationRecover | 3 calls, kind="truncated", message "continuation exhausted after 2 attempts" |
| test_zero_partial_raises_no_continuation | TestTruncationRecover | content=None -> original raise, call_count == 1 (None-safe guard, no AttributeError) |
| test_no_brace_partial_raises_no_continuation | TestTruncationRecover | prose partial -> raise, call_count == 1 |
| test_combined_parse_failure_counts_as_attempt | TestTruncationRecover | unparseable combined -> budget--, then exhaustion |
| test_continuation_does_not_consume_max_attempts | TestTruncationRecover | max_attempts=2 + successful continuation still succeeds |
| test_pre_tripped_breaker_raises_before_dispatch | TestTruncationRecover | pre-tripped breaker -> raise before any _invoke_openai call (spy.call_count == 0) |
| test_breaker_trips_across_calls | TestTruncationBreakerWiring | 4 recovered calls (8 invocations) + 5th trips on record (no continuation); 6th fails fast pre-dispatch; mock.call_count == 9 throughout |
| test_breaker_default_fresh_per_call | TestTruncationBreakerWiring | no param -> no cross-call state |
| test_provider_passes_breaker | TestTruncationBreakerWiring | source-name patch BEFORE build_l1_provider; 3 calls all carry continuation_breaker=<same object> |

## Existing tests that must stay green (regression list)

- tests/test_llm_invoke.py::TestTruncationDetection (all; the carrier
  subclass must not change kind/retryable/message assertions)
- tests/test_llm_invoke.py::TestEmptyContentDetection (all; especially
  test_null_content_with_length_still_reports_truncated, call_count == 1)
- tests/test_llm_invoke.py::TestReadSSE (all pre-existing)
- The no_json retry tests at 2673-2740 (f91605b behavior unchanged:
  detected truncations raise before the parse, so they never enter the
  no_json retry)
- tests/test_llm_invoke.py::TestInvokeSampling truncation/is_truncated
  tests (3436-3448) -- sampling path untouched
- Full suite: python3 -m pytest -q --ignore=.worktrees
  --ignore=.claude/worktrees (ROADMAP.md:674 double-collection note)

## Injection points (all must be executed: inject -> FAIL -> revert -> PASS)

| ID | Injection | Proving test |
|----|-----------|--------------|
| I1 | move the _TruncatedResponse branch after the retryable gate | test_continuation_success |
| I2 | delete the zero-output "{" guard | test_zero_partial_raises_no_continuation |
| I3 | remove the first_emitted flag (emit every chunk) | test_first_token_emit |
| I4 | remove the raise (or the lock) in record_truncation | test_breaker_trips_and_check_tripped / test_breaker_thread_safe_increments |
| I5 | drop usage summation in the continuation return | test_continuation_success (usage assert) |
| I6 | replace the exhaustion message with a generic one | test_continuation_exhausted (message match) |
| I7 | revert _run_pass to not pass continuation_breaker | test_provider_passes_breaker |
| I8 | delete the check_tripped() call at the loop entry | test_pre_tripped_breaker_raises_before_dispatch |

Injection records go into 48-01-SUMMARY.md (per house discipline, the
injection is executed AT the fix site -- deleting the exact guard line or
the exact wiring argument -- never a distant proxy).

## Verification cadence

- Per task commit: python3 -m pytest tests/test_llm_invoke.py -x -q
- After T4: the full regression list above, then the full suite command
- Forge review (separate obligation, per project CLAUDE.md): after all
  tasks are green, a SEPARATE reviewer agent runs the 3-cycle review on
  the diff; the implementer must not self-review. Review evidence to
  .planning/reviews/ per house rules.
</test_plan>

<risks>

1. Cost blowup bound. Continuation input = short instruction + bounded
   2000-char tail, never the original diff; budget 2 caps a pass at 3
   calls (~3x a clean pass worst case); the breaker caps run-level
   repeats at 5 events and its pre-dispatch check makes the trip
   fail-fast with zero further network calls; usage is summed so the
   cost is visible in the existing token printout (factories.py:302-311).
   A recovered pass still costs up to ~2x and that is exactly what D-1
   makes the breaker visible -- a run that recovers every pass trips and
   tells the operator to raise the cap.

2. Prompt-cache interaction. The short-request shape does not resend the
   original prompt, so prefix caching is not a dependency. If a future
   change adopts a multi-turn shape, two rules bind: keep the original
   prompt byte-identical (any reformat kills prefix-cache hits) and
   insert the partial verbatim (no re-encoding) so the fragment the model
   must continue is not corrupted.

3. Oscillation. A continuation whose output again truncates counts
   against the same fixed counter; no similarity-based doom-loop
   detection (OpenCode #18108 lesson: per-attempt output varies, so
   similarity detectors fail; a fixed counter does not). The budget of 2
   plus the breaker bound the worst case deterministically.

4. Interplay with the f91605b json-parse retry. Detected truncations
   raise before the parse (helper level), so they never reach the no_json
   retry and never consume max_attempts. Undetected truncation (provider
   reports "stop" on cut JSON) still burns max_attempts re-truncating
   retries and dies as no_json -- continuation never fires because
   detection never fired. Known gap, explicitly out of scope (Contract
   non-goals); the extension point is: after no_json retries exhaust,
   classify "{"-prefixed content as truncation-suspect and attempt
   continuation. A continuation's combined-parse failure counts against
   the CONTINUATION budget, not max_attempts.

5. Except-handler ordering (RESEARCH.md Pitfall 1). The continuation
   branch must sit before the retryable gate in _invoke_api's except
   handler; I1 proves it.

6. Zero-output replay (Pitfall 2). The D-8 None-safe guard runs before
   any continuation call; the existing call_count == 1 regression plus
   the new zero/no-brace tests anchor it.

7. Breaker races (Pitfall 3). The breaker is touched from
   ThreadPoolExecutor worker threads; the dedicated class takes a
   threading.Lock for every mutation, the tripped property reads under
   the lock, and check_tripped checks under the lock. I4 includes a
   lock-specific injection.

8. Partial-tail corruption (Pitfall 4). The tail is partial JSON with
   live escapes; it must be inserted verbatim and fenced as data.

9. Prompt injection via partial content. The continuation prompt re-feeds
   model-generated text into a model. Mitigation: the <partial> fenced
   delimiter plus the explicit "emit ONLY the continuation" instruction;
   parsing stays on the existing _extract_json_from_text path (see
   threat model T-48-01).

10. TTFT stderr change on bonsai. Exactly one new line per streamed
    bonsai pass ("backend bonsai: first token") -- intended behavior,
    flag-guarded, no periodic counters (D-6). If an MCP job-status
    consumer misparses the new line, that is a consumer bug to fix, not
    a reason to remove the event.
</risks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| provider response -> llm_invoke | Provider output (SSE chunks, response envelopes, partial JSON) is untrusted input crossing into forge |
| partial content -> continuation prompt | Model-generated text re-enters a prompt as data; injection vector if the review target text influenced the partial |
| breaker state across threads | Counter shared across parallel-pass worker threads |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation |
|-----------|----------|-----------|-------------|------------|
| T-48-01 | Spoofing / Elevation | _continue_truncated prompt assembly | mitigate | Partial fenced as <partial>...</partial> data; instruction constrains output ("emit ONLY the continuation; no recap; no preamble"); parse result still goes through _strip_fences + json.loads + _extract_json_from_text with expected_keys (a malicious "continuation" cannot smuggle an invalid envelope through the validator) |
| T-48-02 | DoS | continuation loop | mitigate | Tail bounded to last 2000 chars; budget 2 per call; breaker threshold 5 per run with pre-dispatch fail-fast; no sleep loops; None-safe zero-output guard prevents nothing-output replays |
| T-48-03 | Tampering | _read_sse chunk parsing | accept | Unchanged existing guards (errors="replace" decode, JSONDecodeError skip, llm_invoke.py:395-404); the TTFT change adds one flag and one emit, no new parsing |
| T-48-04 | Information Disclosure | TTFT progress event | accept | Event carries backend name and t+Ns only; no tokens, no content, no keys; goes to stderr which is already the channel for pass-level messages |
| T-48-05 | DoS (race) | TruncationBreaker counter | mitigate | threading.Lock around all mutations, tripped reads, and check_tripped; proven by test_breaker_thread_safe_increments |
| T-48-SC | Tampering | package installs | n/a | No new packages this phase (stdlib only); Package Legitimacy Audit not applicable per RESEARCH.md |
</threat_model>

<exit_criteria>

Run these in order; all must pass with real output recorded in
48-01-SUMMARY.md.

STREAM-VISIBLE proof:
1. cd /home/houminxi/code/forge && python3 -m pytest
   tests/test_llm_invoke.py::TestReadSSE -x -q
2. grep -n "progress" src/code_forge/llm_invoke.py (smoke: shows the
   module import and the emit call; the tests are the gate)

TRUNCATION-RECOVER proof:
3. python3 -m pytest
   tests/test_llm_invoke.py::TestTruncationCarrier
   tests/test_llm_invoke.py::TestTruncationBreaker
   tests/test_llm_invoke.py::TestTruncationRecover
   tests/test_llm_invoke.py::TestTruncationBreakerWiring
   tests/test_llm_invoke.py::TestTruncationDetection
   tests/test_llm_invoke.py::TestEmptyContentDetection -x -q
4. grep -n "_continue_truncated" src/code_forge/llm_invoke.py (definition
   + call site inside the except handler, BEFORE the retryable gate --
   read the block to confirm ordering)
5. grep -n "check_tripped" src/code_forge/llm_invoke.py (method
   definition + the loop-entry pre-dispatch call site)
6. grep -n "continuation exhausted" src/code_forge/llm_invoke.py
7. grep -n "TruncationBreaker" src/code_forge/cli.py src/code_forge/factories.py

Regression gate:
8. python3 -m pytest tests/test_llm_invoke.py -x -q (whole module)
9. python3 -m pytest -q --ignore=.worktrees --ignore=.claude/worktrees
   (full suite from repo root)

Injection matrix:
10. I1-I8 executed (inject -> FAIL -> revert -> PASS), each recorded.

T0 probe:
11. a1_probe.py ran once; verdict line (A1_PROBE kind=...) recorded
    verbatim; one bounded call only.

Optional real-path smoke (non-blocking, record result either way):
12. If BONSAI_API_KEY is set and the bonsai box is reachable, run one
    forge pass against bonsai and confirm stderr shows
    "backend bonsai: first token". Record "confirmed" or "not run
    (reason)" in 48-01-SUMMARY.md.

Post-implementation obligations (not tasks of this plan, but required
before the phase is declared done, per project CLAUDE.md):
13. Separate reviewer agent runs the forge 3-cycle review on the diff
    (implementer must not self-review); review evidence persisted under
    .planning/reviews/.
14. Commit messages follow the forge format (<subsystem>/<case>: WHY,
    Signed-off-by, no plan IDs, no review vocabulary). One commit per
    task T1-T4.
</exit_criteria>

<success_criteria>
- _read_sse emits exactly one first-token progress event per streamed
  call; the event names the backend and reaches stderr through the
  existing lock-protected, flush-on-emit path. Live on bonsai.
- All three API formats raise a payload-carrying truncation error and
  recover usable partials through bounded continuation (budget 2) with
  summed usage and is_truncated=True.
- Zero-output (including content=None) and no-brace truncations raise
  unchanged after exactly one HTTP call.
- Exhaustion raises kind="truncated" with the distinguishing message,
  preserving MCP fallback eligibility (mcp_server.py:959-961).
- The run-level TruncationBreaker (threshold 5, locked, tripped/
  check_tripped) trips across calls; once tripped, later calls fail fast
  before any network call and land as INFRA findings; direct callers stay
  stateless via the None default.
- Every new test plus the full regression list is green; injections
  I1-I8 executed; T0 probe verdict recorded; no new dependencies.
</success_criteria>

<output>
Create .planning/phases/phase-48/48-01-SUMMARY.md when done, containing:
the T0 probe verdict line verbatim, the optional bonsai smoke result, the
injection matrix results (I1-I8), the exact pytest commands with their
final lines, the forge review status, and the cross_repo.py follow-up
note (Contract non-goals). Also leave
.planning/phases/phase-48/a1_probe.py in place.
</output>

---

## CP1B-R1 AMENDMENTS (2026-08-16, panel kimi + mimo, 12 findings all accepted)

These amend the tasks above; where an amendment contradicts the
earlier text, the amendment wins.

A-1 [kimi#1] fold wiring: `continuation_breaker.record_success()` is
   called in factories fold ONLY when the result is not a recovered
   truncation (`result.is_truncated is False`). A recovered call must
   NOT reset the count, or T4 Test 1's accumulation (calls 1-4 count
   1..4, trip on call 5) is unachievable. Add a fold-level test.
A-2 [kimi#2] `_continue_truncated`: normalize each continuation's
   content (`None -> ""`); an empty/None continuation is a parse
   failure counted against the budget, never a TypeError.
A-3 [kimi#3] T0 probe: disable continuation explicitly (breaker with
   threshold=0 so the first truncation trips) so the probe observes
   the raw truncation kind instead of a masked recovery.
A-4 [kimi#4] `_continue_truncated` catches LLMInvokeError: a
   non-truncation LLMInvokeError from a continuation request is a
   failed attempt (budget decrement); it must NOT escape to the outer
   max_attempts retry loop. TruncationBreakerError propagates
   immediately.
A-5 [kimi#5] usage summation: `(usage_data or {})` normalization on
   the truncated payload and every continuation usage before summing
   per-format keys.
A-6 [mimo#1+#2] D-4 corrected: `_continue_truncated` is ONE entry
   point whose body dispatches by `backend.format`; the vertex call
   passes NO api_key (`_invoke_vertex(prompt, backend, timeout_s)`).
   The openai/anthropic calls pass api_key. A vertex-path
   continuation test is required (T3 Test 8).
A-7 [mimo#3] `breaker.record_truncation()` runs BEFORE the budget
   loop entry in `_continue_truncated`, so a trip propagates before
   any further continuation request is issued.
A-8 [mimo#4+#5] T3 Test 2/5 descriptions must carry the call_count
   derivation: "initial truncation + N continuation attempts
   (budget=N exhausted) = 1 + N total `_invoke_<format>` calls".
A-9 [mimo#6] T3 Test 8 (vertex format) added per A-6; an anthropic
   continuation test is optional.
A-10 [mimo#7] D-8 zero-output guard reads:
    `not isinstance(truncated.content, str) or not truncated.content.strip() or "{" not in truncated.content`.

---

## CP1B-R1 AMENDMENTS ROUND 2 (2026-08-16, internal checker + PBR 8-pass)

A-11 [supersedes A-3]: the probe breaker threshold is 1, not 0 --
    threshold=0 trips check_tripped() before any dispatch (0 >= 0),
    making the probe vacuous. threshold=1 dispatches, trips on the
    first real truncation, and never issues a continuation. The
    existing T0-PROBE.md record (unexpected_success output_tokens=11590)
    stands as the honest pre-T3 probe; the re-run happens ONLY after
    T3 lands, with threshold=1 and the a1_probe.py updated to
    construct the breaker. Exit criterion 11 is reworded: "one
    bounded call per attempt, no continuation".
A-12 [supersedes A-1's contradiction]: T4 GREEN "nothing else in the
    fold changes" is amended -- the fold DOES change: beside the
    existing timeout-breaker record_success at factories.py:424-425,
    add `if continuation_breaker is not None and not
    result.is_truncated: continuation_breaker.record_success()`.
    Test-plan table gains the fold-level test
    TestTruncationBreakerWiring.test_fold_records_success_only_for_non_truncated
    (asserts: recovered truncation does NOT reset the count; a clean
    result DOES). T4 acceptance becomes "All 4 new tests pass".
    A-1's rationale is corrected: the reason is production run-level
    trip semantics (a backend that always truncates but always
    recovers must still trip the run breaker), not T4 Test 1 (which
    bypasses the fold).
A-13 [supersedes A-9 numbering]: the vertex continuation test is
    "T3 Test 8"; the existing regression
    test_null_content_with_length_still_reports_truncated is
    renumbered "T3 Test 9". T3 acceptance becomes "All 9 new tests
    pass". Test-plan table gains the vertex row.
A-14 [supersedes the T3 GREEN (b) inline guard]: the inline guard
    text is the A-10 form
    `not isinstance(truncated.content, str) or not truncated.content.strip() or "{" not in truncated.content`.
    A test covers a truthy non-str partial (content=123).
A-15 [extends A-2]: continuation content normalization uses
    isinstance: non-str content (any type) is replaced with "" and
    the attempt counts as a parse failure against the budget --
    matching the codebase's own isinstance empty-checks
    (llm_invoke.py:1114).
A-16 [extends A-4]: the except ordering is explicit and load-bearing:
    `except TruncationBreakerError: raise` comes BEFORE
    `except LLMInvokeError`. TruncationBreakerError IS an
    LLMInvokeError; the wrong order swallows the trip as a budgeted
    failure and issues post-trip network calls. Injection I9: swap
    the two except clauses at the helper -> the trip-swallow test
    must FAIL.

Line-ref corrections: cli.py:3004 -> 3005; cross_repo.py:302-309 ->
305-310.

---

## CP1B-R1 AMENDMENTS ROUND 3 (2026-08-16, internal checker delta re-verify)

A-17 [supersedes A-16's I9]: the injection is re-specified against the
    actual hazard. I9: in the helper's broad `except LLMInvokeError`
    handler, DELETE the `except TruncationBreakerError: raise` clause
    that precedes it -> the trip-swallow test (T3 Test 10,
    TestTruncationRecover.test_trip_propagates_not_budgeted: a
    continuation-request truncation trips the breaker and NO further
    network call is issued) must FAIL. Rationale: except-clause order
    is behaviorally inert for the reachable trip sites (Python does
    not re-catch raises from sibling handlers; both trip sites bypass
    the clauses), so swapping the clauses proves nothing; deleting
    the re-raise branch proves the swallow.
    T3 acceptance becomes "All 10 new tests pass" (7 original +
    vertex Test 8 + non-str Test 9 + trip-swallow Test 10).

A-18 [closes the round-2 unscored disclosures]: two defensive
    branches get named tests even though they are unreachable by
    construction (the plan's own discipline: every branch has a test):
    - T3 Test 11 TestTruncationRecover.test_usage_none_normalized:
      _TruncatedResponse with usage_data=None plus a continuation
      with usage=None sums to (0,0) with no AttributeError.
    - T3 Test 12 TestTruncationRecover.test_non_str_continuation_normalized:
      a continuation returning content=123 counts as a failed attempt
      (budget decrement) and never raises TypeError.
    T3 acceptance becomes "All 12 new tests pass".

A-19 [closes the final internal L]: the helper's try shape is pinned in
    T3 GREEN: an OUTER try (clauses: `except TruncationBreakerError:
    raise` BEFORE broad `except LLMInvokeError`) encloses the entry
    record_truncation() AND the budget loop; each dispatch has an
    INNER try whose handler-body record_truncation() raise propagates
    out of the inner try and re-enters the OUTER clauses. A-17's
    rationale is corrected: deletion (not swapping) is the injected
    form because a re-raise clause placed after a broad clause is
    permanently dead, not because sibling-handler raises bypass
    anything -- under the two-level shape both trip sites reach the
    outer clauses. Numbering cleanup: the pre-existing regression
    test_null_content_with_length_still_reports_truncated is
    explicitly renumbered "T3 Test 13" (A-13's "regression = 9" is
    superseded; non-str stays Test 9).

A-20 [closes mimo-R2#1+#2]: the injection matrix gains the row
    `I9 | DELETE the except TruncationBreakerError: raise clause in
    the helper's outer try | test_trip_propagates_not_budgeted`;
    exit criterion 10 becomes "I1-I9 executed and recorded".
    The test-plan tables are superseded by the amendment-defined test
    set: the T4 table gains test_fold_records_success_only_for_non_truncated
    (assertions: recovered truncation does NOT reset the count; a
    clean result DOES); the T3 table gains Test 8 (vertex), Test 9
    (non-str partial), Test 10 (trip-swallow), Test 11 (usage None),
    Test 12 (non-str continuation), and Test 13 (the pre-existing
    regression renumber).
A-21 [closes mimo-R2#3]: A-17's rationale gains the reconciling
    sentence: "Under the A-19 two-level try shape, both trip sites
    reach the outer except clauses; deleting the specific clause lets
    the broad handler catch the trip, which is the swallow being
    proven."
A-22 [closes mimo-R2#4, option (a)]: an anthropic-format continuation
    test is REQUIRED, not optional: T3 Test 14
    TestTruncationRecover.test_anthropic_continuation_passes_api_key
    (mock _invoke_anthropic side_effect; asserts the dispatch passes
    api_key). T3 acceptance becomes "All 14 new tests pass". D-4's
    "one fixture per format" promise is thereby honored literally.
A-23 [closes mimo-R2#5]: the T0 probe prints the exception's
    __cause__ chain when available, and the probe script comments
    that "kind=truncated confirms finish_reason=length detection; the
    message text is from TruncationBreakerError, not the raw
    detection site".
