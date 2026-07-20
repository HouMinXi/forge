FORGE -- provider-aware parameter passthrough + streaming (implementation handoff)
For: a forge implementation sub-session. From: main session. Date: 2026-06-29.
Implements: ADR-0005. SUPERSEDES the earlier narrow temperature/top_p version
of this file.

READ FIRST
----------
1. ARCHITECTURE (local-only, on disk, do NOT commit/push):
     ~/code/forge/docs/adr/0005-provider-aware-parameter-passthrough.md
   The WHY + the provider matrix + the design. Read it before coding.
2. REFERENCE IMPLEMENTATION TO PORT (validated on LiveCodeBench):
     ~/code/trinity-router/trinity_router/worker_pool.py
       - WorkerConfig dataclass (L40-95): the typed fields + sentinels.
       - _build_openai_request (L180-211): openai body mapping.
       - _build_vertex_request  (L243-280): vertex body mapping.
       - _read_sse              (L329+):    SSE reassembly for stream:true.
     ~/code/trinity-router/workers.yaml: the per-provider validated values.
   Port this logic; do not reinvent it. trinity is the proven 80% solution.

SCOPE (all Phase 1 -- streaming included per operator decision)
---------------------------------------------------------------
A. Typed config fields (sentinel = "do not send").
B. Generic `params` passthrough dict (long tail + provider-specific keys).
C. Per-format body mapping (openai / anthropic / vertex).
D. SSE streaming (openai path first; see D for anthropic/vertex note).
E. Per-backend timeout_s override.
F. Shipped example configs carrying the validated per-provider values.

A. NEW BackendConfig FIELDS (backend.py, frozen dataclass L58-77)
-----------------------------------------------------------------
Add (all scalars except params; scalars are hashable on a frozen dataclass):
  temperature: float = -1.0          # -1 = omit; >=0 = send temperature
  max_completion_tokens: int = 0     # 0 = fall back to existing max_tokens field
  thinking_type: str = ""            # "enabled"|"adaptive"|"disabled"; ""=omit
  thinking_budget: int = 0           # >0 = add thinking.budget_tokens
  reasoning_effort: str = ""         # ""=omit
  stream: bool = False
  timeout_s: int = 0                 # 0 = use FORGE_LLM_TIMEOUT_S / default
  params: Optional[dict] = field(default=None, compare=False)
    # generic verbatim-merge dict. compare=False keeps BackendConfig hashable
    # despite the dict (the probe cache keys by backend.name, backend.py:413,
    # but compare=False removes the latent hash landmine if a config is ever
    # put in a set). Normalize None->{} at parse, never a mutable default.

B/C. PARSE (_parse_backend_entry, backend.py:97; api branch L119-172, vertex
sub-branch L133-147, cli branch L173-183)
--------------------------------------------------------------------------
- Thread the new fields into the api AND vertex BackendConfig returns. cli
  backends do not take them (raise CliError if present on a cli entry).
- params: validate dict; reject protected/structural keys with a CliError
  naming the key: model, messages, stream, anthropic_version, temperature,
  thinking, reasoning_effort, max_completion_tokens, max_tokens. (Those are
  owned by typed fields or by forge's structure.) Values may be scalars,
  lists, or nested dicts (e.g. response_format {type: json_object}) -- merge
  verbatim, do not over-validate the long tail.
- thinking_type: validate in {"enabled","adaptive","disabled"} or "".
- reasoning_effort: accept any non-empty string (provider validates the value;
  values differ per provider -- see ADR-0005 matrix).

C. BODY MAPPING (llm_invoke.py) -- port trinity _build_*
--------------------------------------------------------
Factor ONE helper that applies the typed fields + params to a body dict, with
a per-format "out-cap key" argument, so the logic is defined once (no
copy-paste across the three _invoke_*). Pseudocode:

  def _apply_params(body, backend, *, outcap_key, allow_thinking, allow_effort):
      cap = backend.max_completion_tokens or backend.max_tokens
      body[outcap_key] = cap
      if allow_thinking and backend.thinking_type:
          th = {"type": backend.thinking_type}
          if backend.thinking_budget > 0:
              th["budget_tokens"] = backend.thinking_budget
          body["thinking"] = th
      if allow_effort and backend.reasoning_effort:
          body["reasoning_effort"] = backend.reasoning_effort
      if backend.temperature >= 0:
          body["temperature"] = backend.temperature
      for k, v in (backend.params or {}).items():
          body[k] = v          # protected keys already rejected at parse

- _invoke_openai (body L686): outcap_key="max_completion_tokens",
  allow_thinking=True, allow_effort=True. REMOVE the hardcoded
  "temperature": 0 from the literal; instead default forge's openai backend
  temperature field to 0 (so behavior is unchanged when unconfigured) -- i.e.
  the openai sentinel is 0 here, NOT -1, for backward compat. Document that
  choice in a comment. (anthropic/vertex keep -1 = omit, matching today.)
  NOTE the existing field today is `max_tokens`; switching the KEY to
  max_completion_tokens is a behavior change for openai -- call it out in the
  commit. DeepSeek/GLM want the `max_tokens` KEY on openai: the customer sets
  `params: {max_tokens: N}` and omits max_completion_tokens for those.
- _invoke_anthropic (body L745): outcap_key="max_tokens", allow_thinking=True,
  allow_effort=False. (MiniMax thinking is server-side; the leading <think>
  strip already exists at L773 -- leave it.)
- _invoke_vertex (body L879): outcap_key="max_tokens", allow_thinking=True,
  allow_effort=True. (trinity validated top-level reasoning_effort on Vertex
  Claude. Native Anthropic may want output_config.effort instead -- if a forge
  user hits a 400 on effort against api.anthropic.com, that is the cause;
  document it, do not auto-detect.)

D. STREAMING (stream:true) -- port trinity _read_sse
----------------------------------------------------
All three _invoke_* do: req = urllib.request.Request(...);
  with urllib.request.urlopen(req, timeout=timeout_s) as response:
      resp_data = json.loads(response.read().decode())   # openai L696-697,
                                                          # anthropic L753-754,
                                                          # vertex L886-887
When backend.stream, the response is text/event-stream (lines "data: {...}",
terminated by "data: [DONE]"), NOT one JSON object. Branch:
  resp_data = _read_sse(response) if backend.stream else json.loads(response.read()...)
_read_sse (port from trinity) reads line by line, parses each "data:" chunk,
concatenates choices[0].delta.content into a single assembled response with the
SAME shape _check_body_error + the extractor expect (choices[0].message.content
for openai). THEN run _check_body_error(resp_data, backend) as today (L716).
Scope: implement openai SSE first (DeepSeek/GLM/MiMo stream on the openai path
-- the providers that NEED streaming behind timeout proxies). anthropic/vertex
SSE: no current provider config streams them (MiniMax uses timeout_s, not
stream); add the anthropic event-shape reader only if a config needs it, and
say so in the report. Do NOT silently claim anthropic/vertex streaming if you
only built+tested openai.

E. PER-BACKEND timeout_s (invoke entry, llm_invoke.py:380, resolve L410-411)
----------------------------------------------------------------------------
Today: if timeout_s is None or <=0 -> _default_timeout_s() (FORGE_LLM_TIMEOUT_S
or DEFAULT_TIMEOUT_S=120). Change: prefer backend.timeout_s when >0, else the
existing resolution. Reasoning models need 1800; 120 truncates them.

F. SHIPPED EXAMPLE CONFIGS (init_template.py + configuration.md)
----------------------------------------------------------------
Add commented example backends carrying the ADR-0005 validated values so the
customer gets "a default they can adjust" (mirror trinity workers.yaml):
  deepseek:  thinking_type: enabled, reasoning_effort: high, max_completion_tokens: 32768
  claude-46: format vertex/anthropic, thinking_type: adaptive, reasoning_effort: high, max_completion_tokens: 32768
  mimo:      stream: true, max_completion_tokens: 65536, timeout_s: 1800, thinking_type: enabled
  kimi:      max_completion_tokens: 32768   (do NOT set thinking/temperature -- always-on, immutable)
  minimax:   format anthropic, timeout_s: 1800   (thinking server-side)
  glm:       stream: true, reasoning_effort: max, max_completion_tokens: 32768, timeout_s: 1800
configuration.md is TRACKED -> edit in the worktree, commit `# docs`.

TESTS (TDD -- RED first; bug-inject each assertion)
---------------------------------------------------
Ground the real test files (tests/test_backend.py for parse; the llm_invoke
body/stream tests -- find them).
  parse:
   - each typed field absent -> sentinel; present -> stored.
   - params with a protected key (each of the 9) -> CliError naming it.
   - params with a benign key (top_p) -> stored; nested value (response_format)
     -> stored verbatim.
   - thinking_type not in the enum -> CliError.
   - any new field on a cli backend -> CliError.
  body (mock urlopen; assert the JSON body per format):
   - unconfigured openai -> body has temperature==0 (forge default) and
     max_completion_tokens (NOT max_tokens) == max_tokens field value; no
     thinking/effort/stream keys.
   - unconfigured anthropic/vertex -> byte-identical to today (no temperature,
     max_tokens key, no thinking).
   - thinking_type=enabled + budget=16000 -> body thinking=={type:enabled,
     budget_tokens:16000}.
   - reasoning_effort=high (openai+vertex) -> present; (anthropic) -> absent.
   - temperature=-1 -> temperature key ABSENT; temperature=0.2 -> ==0.2.
   - params={top_p:0.9} -> body top_p==0.9.
  stream (mock a fake SSE byte stream):
   - stream=True -> _read_sse path; assembled content equals the concatenated
     deltas; _check_body_error still runs.
   - stream=False -> json.loads path unchanged.
  timeout:
   - backend.timeout_s=1800 -> urlopen called with timeout=1800.
Bug-inject proof (each must FAIL on the injected bug, PASS on revert):
   - drop the `temperature>=0` guard -> the temperature=-1-absent test fails.
   - leave openai KEY as max_tokens -> the max_completion_tokens test fails.
   - skip _check_body_error after _read_sse -> a body-error-in-stream test fails.

ZERO-REGRESSION ACCEPTANCE
--------------------------
- Unconfigured anthropic/vertex backend: body byte-identical to today.
- Unconfigured openai backend: still sends temperature 0; only the out-cap KEY
  changes max_tokens->max_completion_tokens (intended; call out in commit).
- BackendConfig stays hashable: hash(some_config) does not raise (params is
  compare=False; all other new fields are scalars).
- Full suite green.

PROCESS (forge rules -- non-negotiable)
---------------------------------------
- Worktree first: git -C ~/code/forge worktree add .worktrees/work-params
  -b feat/provider-params. Never edit the main worktree.
- LOGIC-BEARING: full three-cycle review (9 passes) OR external multi-model
  (aicc) to 0/0/0/0, + Step 0 (ruff, py_compile, non-ASCII), + the bug-inject
  smoke above, + ONE real-API smoke (call a real reasoning backend end-to-end,
  e.g. a deepseek/mimo via the CN proxy, confirm thinking+effort actually shape
  the response and SSE assembles). impl != reviewer (separate sub-sessions).
- Commit: post-review-c3 for code; `# docs` for configuration.md/init_template.
  Two-message commits, WHY in body, NO review vocabulary, NO ADR/plan refs in
  code comments (translate ADR rationale into self-contained comments).
  Signed-off-by: Minxi Hou <houminxi@gmail.com>.
- Report back: branch + SHA + diff --stat + pytest + bug-inject evidence +
  the real-API smoke output. No auto-merge; host ff-merges.

SCOPE NOTE (do less if the diff gets large)
-------------------------------------------
If review surface is too big for one pass, split commits: (1) typed fields +
params + body mapping (no stream); (2) streaming + _read_sse; (3) example
configs/docs. All three still land this phase, but as reviewable units. Do not
ship streaming without the body-mapping commit it depends on.
