FORGE PHASE BRIEF -- provider-aware params + streaming + cli-env (GSD plan-phase input)
From: main session. Date: 2026-06-29. Feed this to /gsd-plan-phase; it is the
wave decomposition, NOT the PLAN.md (plan-phase generates that in .planning/).

PHASE GOAL
----------
Implement ADR-0004 (cli-backend env field) + ADR-0005 (provider-aware sampling
/reasoning parameter passthrough + SSE streaming) as ONE phase in ONE worktree,
sequenced so the two ADRs do not collide on shared files.

RESEARCH INPUTS (already done -- do NOT re-research; these ARE the research)
----------------------------------------------------------------------------
- Decisions (accepted, local-only): docs/adr/0003, 0004, 0005 (esp. 0005's
  provider matrix + per-format body mapping).
- Implementation specs (the per-ADR HOW, with grounded line anchors):
    /tmp/draft_20260629_forge_param_passthrough_spec.txt   (ADR-0005)
    /tmp/draft_20260629_forge_cli_env_field_spec.txt        (ADR-0004)
- Reference impl to PORT: ~/code/trinity-router/trinity_router/worker_pool.py
  (_build_openai_request, _build_vertex_request, _read_sse) + workers.yaml.
Scope-challenge is satisfied by the ADRs (consumers named: forge's own CN +
Vertex review backends; reasoning models underperform/error without these).

WHY ONE WORKTREE, DATACLASS-FIRST (the collision constraint)
------------------------------------------------------------
Both ADRs edit the SAME three surfaces: BackendConfig dataclass (backend.py
L58-77), _parse_backend_entry (backend.py:97), and llm_invoke.py _invoke_*.
The ONLY hard textual collision is the dataclass (both append fields). So add
ALL fields in Wave 1, then later waves touch disjoint code (api/vertex parse
vs cli parse; api invoke bodies vs _invoke_cli). Two parallel worktrees would
conflict; one worktree with ordered atomic commits does not.

WAVES (each: files touched + done-condition provable by command/re-read; TDD
RED->GREEN with bug-inject on every new assertion)
--------------------------------------------------------------------------
Wave 0 -- Worktree
  Files: none. git -C ~/code/forge worktree add .worktrees/work-provider-params
    -b feat/provider-params. Done: .git is a FILE in the worktree; on feat branch.

Wave 1 -- BackendConfig fields (BOTH ADRs together; resolves the collision once)
  Files: src/code_forge/backend.py (dataclass ONLY), tests/test_backend.py.
  Add 0005 typed fields (temperature=-1.0, max_completion_tokens=0,
    thinking_type="", thinking_budget=0, reasoning_effort="", stream=False,
    timeout_s=0, params=field(default=None, compare=False)) + 0004 fields
    (env_unset: tuple=(), env_set: tuple=()).
  Done: every field present with its sentinel default; hash(some_config) does
    NOT raise (params compare=False; rest scalars/tuples); default-value tests
    green; full suite green.

Wave 2 -- Parse (_parse_backend_entry)
  Files: backend.py (parse), tests/test_backend.py.
  api+vertex branches: read/validate 0005 typed fields + params (reject the 9
    protected keys with CliError). cli branch: read env:{unset,set}->tuples;
    reject env on api/vertex; reject 0005 fields on cli.
  Done: parse tests green incl protected-key + wrong-branch CliErrors;
    bug-inject (remove a rejection -> that test FAILS -> revert -> PASS).
  Depends: Wave 1.

Wave 3 -- API body mapping + per-backend timeout (ADR-0005)
  Files: src/code_forge/llm_invoke.py (_apply_params helper + _invoke_openai
    L686, _invoke_anthropic L745, _invoke_vertex L879, invoke() timeout L410-411),
    the llm_invoke body tests.
  Factor ONE _apply_params(body, backend, *, outcap_key, allow_thinking,
    allow_effort) (no copy-paste). openai: outcap_key=max_completion_tokens,
    REMOVE hardcoded temperature:0 (default the openai temperature sentinel to 0
    for back-compat); anthropic: max_tokens, no effort; vertex: max_tokens,
    thinking, effort. invoke(): prefer backend.timeout_s>0.
  Done: per-format body tests green; UNCONFIGURED anthropic/vertex byte-identical
    to today; unconfigured openai sends temperature 0 + max_completion_tokens KEY
    (the ONE intended behavior change -- flag in commit); temperature=-1 omits
    the key; bug-inject (drop temp>=0 guard -> fail; leave openai key as
    max_tokens -> fail).
  Depends: Wave 1 (fields). Tests may build BackendConfig directly.

Wave 4 -- SSE streaming (ADR-0005, the risky new subsystem)
  Files: llm_invoke.py (_read_sse ported from trinity + stream branch at the
    urlopen in _invoke_openai L696), stream tests (fake SSE byte stream).
  stream=True: read line-by-line, assemble deltas into the same resp_data shape,
    THEN run _check_body_error (L716) + the normal extractor. openai SSE FIRST
    (DeepSeek/GLM/MiMo stream on openai). anthropic/vertex SSE: only if a config
    needs it; if not built, SAY SO -- do not claim coverage you did not build.
  Done: stream=True assembled content == concatenated deltas; _check_body_error
    still runs; stream=False unchanged; bug-inject (skip _check_body_error after
    SSE -> a body-error-in-stream test FAILS).
  Depends: Wave 3 (body emits stream:true).

Wave 5 -- cli-backend env (ADR-0004)
  Files: llm_invoke.py (_invoke_cli, Popen L462), cli invoke tests (mock Popen).
  child_env = dict(os.environ); pop env_unset; update env_set; pass env=child_env;
    env=None when both empty (byte-identical no-op).
  Done: no env field -> Popen env=None; env_unset removes the key, PATH survives;
    env_set forces the var; bug-inject (else-branch env={} -> PATH-present test
    FAILS).
  Depends: Wave 1, Wave 2. Disjoint from Waves 3-4 (different function) but same
    file -> sequential commit.

Wave 6 -- Shipped example configs + docs
  Files: src/code_forge/init_template.py, docs/configuration.md.
  Add commented example backends carrying ADR-0005 validated values (deepseek
    thinking+effort high; claude-46 adaptive+effort high; mimo stream+65536+1800;
    kimi 32768 no temp/thinking; minimax anthropic+1800; glm stream+effort max).
    Add a cli `env:` example; update the account-auth env paragraph to the
    declarative env form. NOTE: GPT-5.5 is the GA openai flagship; GPT-5.6 is
    limited-preview (do NOT ship it as a default example).
  Done: examples match the implemented fields; `# docs` commit; no GPT-5.6 default.
  Depends: all prior (docs describe what shipped).

PLAN-REVIEW (mandatory BEFORE execute-phase -- folds in the external review Q)
------------------------------------------------------------------------------
1. Internal: /plan-forge + gsd-plan-checker on each wave plan -> 0/0/0/0.
2. External multi-model via aicc, with PER-PROVIDER ROUTING for the 0005 matrix
   facts (each model is the authority on its OWN API):
     aicc ds   -> verify the DeepSeek V4 row (thinking/effort/max_tokens/temp-inert)
     aicc mimo -> verify the MiMo V2.5 row (temp forced 1.0, max_completion_tokens, stream)
     aicc kimi -> verify the Kimi K2.7 row (thinking always-on, temp immutable)
     aicc mm   -> verify the MiniMax M3 row (thinking adaptive/disabled, out-cap key)
     aicc gm   -> overall design critique + the Claude 4.6 / GPT-5.5 rows
   Timeouts >=600s; model-specific session names (r1-ds, r1-mimo, ...). This is
   where the 0005 API facts get authoritatively checked -- do NOT separately
   pre-review the ADR; fold the fact-check here. (0001-0004 are strategic =
   human-reviewed, NOT sent to multi-model.)
3. Iterate to external 0/0/0/0, then user approves before execute.

POST-WAVE GATE (execute-phase does NOT run forge review -- bolt it on)
----------------------------------------------------------------------
- SEPARATE reviewer subagent (cold context, impl != reviewer): forge three-cycle
  9-pass on the FULL diff. Any finding resets.
- Step 0 each touched file: ruff, py_compile, non-ASCII grep, no plan/ADR refs
  in code comments (translate ADR rationale into self-contained comments).
- REAL-API smoke (not mocked): call a real reasoning backend (ds/mimo via the CN
  proxy) end-to-end; confirm thinking+effort actually shape the response AND SSE
  assembles to clean text. One real path, per forge "run the real path once".
- Wrap-up: ROADMAP/STATE update, snapshot-planning, report branch+SHA+diff stat.

NON-NEGOTIABLES (forge)
-----------------------
- No auto-merge; sub-session reports branch+SHA+diff stat; host ff-merges after
  topology-verify (git merge-base main <branch>; git diff main...<branch> --stat).
- Commit markers: post-review-c3 for code waves; `# docs` for Wave 6.
  Two-message commits, WHY in body, NO review vocabulary, Signed-off-by: Minxi
  Hou <houminxi@gmail.com>.
- After each wave, main session verifies (pytest, diff stat, acceptance grep)
  before dispatching the next.

SPLIT/MERGE GUIDANCE
--------------------
Waves 1-2 are small and could merge. Wave 4 (streaming) MUST stay isolated for
focused review (highest-risk new code). If any single wave's diff is too large
for one review pass, split further -- but never ship streaming without Wave 3,
nor any wave without its tests.
