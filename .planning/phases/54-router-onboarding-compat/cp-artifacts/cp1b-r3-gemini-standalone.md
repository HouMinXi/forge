# Review assignment (Gemini) — R3, standalone text-only review

IMPORTANT: you have NO filesystem or tool access in this call. You cannot
open the real repository source files. Everything you need is embedded
below: the shared review briefing (with full R1/R2 history from two other
reviewers who DID have repo access), and the complete current plan text.
Because you cannot independently verify plan citations against real source
files, your review is necessarily scoped to TEXT-LEVEL analysis only:

1. INTERNAL CONSISTENCY — does the plan contradict itself? (e.g. a
   behavior/test description that doesn't match the action section it's
   supposed to implement; a acceptance criterion that references a count or
   label not defined elsewhere in the text; a task's <files> list missing a
   file its own action/behavior section edits.)
2. COMPLETENESS OF THE STATED LOGIC — given ONLY what the plan asserts about
   the code (its own cited line numbers, signatures, and mechanisms — taken
   as given, not independently verified), does the described change achieve
   what the objective/success_criteria claim? Any step in the plan's own
   narrative that doesn't follow from its own prior steps is a finding.
3. REQUIREMENTS COMPLIANCE — read REQUIREMENTS ROUTER-02..05 (summarized in
   the briefing below) against the plan's six tasks. Any requirement text
   the plan's OWN action prose appears to narrow or skip is a finding.

Do NOT report a finding of the form "I cannot verify this line number exists
in the real file" — that is expected and out of scope for this pass; only
report actual textual contradictions, gaps in the plan's own stated logic,
or requirements the plan's own prose fails to address.

## Shared briefing (identical to what deepseek and kimi-k2.7 reviewed)

<<<BRIEFING START>>>
# CP1b R1 briefing — Phase 54 router-onboarding-compat (shared context for all panel models)

You are reviewing a PLAN, not writing code.

## What this phase is

Four router-onboarding fixes for the forge review tool, one plan, six tasks:
- T1 (F2/ROUTER-02): base_url /v1 semantics text in gate.schema.json only.
- T2 (F3/ROUTER-03): `code-forge trust` gains walk-up resolution (reusing
  workspace.resolve_workspace), prints the resolved absolute gate.yaml path
  before mutating trust ops, warns-but-proceeds when resolved != cwd, and
  passes the resolved workspace as the contracts-base to both
  resolve_contract_specs calls.
- T3 (F5/ROUTER-05): doctor prints the user-level config path (or would-be
  location + hint); README pointer; tests patch user_config_path, never
  load_user_backends (conftest autouse trap).
- T4 (F4 core/ROUTER-04): additive kind= taxonomy on LLMInvokeError
  (conn/credentials/sse_body/bad_body) at existing raise sites in
  llm_invoke.py + new probe_backend_live helper in backend.py riding
  unmodified llm_invoke with max_attempts=1 and a frozen-config replace()
  copy (timeout_s=60, max_tokens=32, thinking/effort fields zeroed).
- T5 (F4 wiring/ROUTER-04): doctor --live flag threading; direct helper call
  bypassing the 5-min probe cache; failures flow through the existing
  has_fail -> exit 1 pipeline; cli-type backends get an informational skip.
- T6: human real-path smoke checkpoint.

Locked user decisions (D-01..D-12, summarized): D-08 warn-condition is
`resolved_workspace != cwd.resolve()`; D-12 locks six tasks in one plan;
D-03 narrowed to type=api backends only for live probing; taxonomy strings
are implementer's discretion within the phase's own consistency needs.

## Prior review history (do NOT re-report these; if you find one independently, mark it CONFIRMING, not new)

Internal round 1 (two independent reviewers) found and the plan fixed:
1. Whitelist-negative test homelessness -> now rides
   test_dispatch_sampling_unknown_kind_never_falls_back (tests/test_mcp_server.py:919),
   file added to Task 4 files + frontmatter.
2. Task 2 was silent on the contracts-base argument of resolve_contract_specs
   -> now passes the resolved workspace at both call sites.
3. FORGE_PROJECT_DIR env hijack of trust tests -> autouse delenv fixture
   mandated in BOTH existing trust test files.
4. `dataclasses` module name unbound in backend.py -> plan adds `replace` to
   the existing from-import (backend.py:30).
5. Thinking-burn residual -> probe copy zeroes thinking_type/thinking_budget/
   reasoning_effort.
6. Task 5 patch target ambiguity -> code_forge.backend.probe_backend_live.
7. grep-count criterion ambiguous -> explicit 16 -> 28 with the two-raise-arm
   shape clause.
8. VALIDATION.md frontmatter/sign-off stale -> flipped; quick-run now
   includes tests/test_mcp_server.py.

Internal round 2 residuals (same reviewers) also fixed: the five above items'
fix-scope gaps (existing-test exposure, verify-command omission, ternary
shape hole, positive-harness miscitation, reasoning_effort residual).

Internal round 3 (exit round) residuals also fixed: autouse delenv fixture
widened to BOTH existing trust test files; Task 4 verify + VALIDATION
quick-run gained tests/test_mcp_server.py; grep-criterion shape clause;
whitelist-negative re-pointed to the :919 negative test; reasoning_effort=""
added to the probe copy; Task 4 llm_invoke patch target named as the source
module; file manifests synced (tests/test_contract_wiring.py +
tests/test_mcp_server.py in Task 2/4 files and frontmatter).

## CP1b R1 outcomes and what changed since (R2 candidates read this)

**deepseek R1: B=0 H=0 M=1 L=3 — all four confirmed against ground truth and fixed:**
- M-1: the dispatch line `live=args.live` had no executing test -> Task 5
  behavior (g) now mandates a main()-level dispatch test (pattern
  tests/test_cli_integration.py:181; patch code_forge.doctor.run_doctor)
  plus injection (4): delete live=args.live -> (g) FAILS.
- L-1: build/lib guard was vacuous (gitignored) -> mtime check instead.
- L-2: cli-skip row text claimed "already executed by the offline probe"
  (false: bypass entirely, backend.py:792-793/:812-813) -> reworded to
  "trusted as configured; no live probe applies".
- L-3: test_backend count 153 -> 162 with re-collect caveat.

**kimi k2.7 R1: B=0 H=1 M=0 L=2 — all three confirmed against ground truth and fixed:**
- F-1 HIGH: _format_error_message (llm_invoke.py:691-715) took body_excerpt
  but never used it; openai/anthropic HTTP errors dropped the body, and the
  wrong-/v1 misconfiguration (the phase's headline debug case) surfaces as
  exactly such an HTTP error. Fixed as new Task 4 Step 1.5: the shared
  formatter now appends the excerpt for openai+anthropic (vertex already
  embedded inline), with an excerpt-presence test and injection (5).
- F-2: detail=str(exc) could carry newlines into the one-line doctor row ->
  detail is whitespace-normalized at the helper boundary (precedent
  llm_invoke.py:1518), with a no-newline test.
- F-3: vertex credential raises (:1857/:1862/:1871) lacked kind= -> added
  kind="credentials" (the google-auth ImportError stays kindless by design);
  grep-count target updated 28 -> 31.

**deepseek R2 (on the R1-fixed plan): B=0 H=0 M=0 L=2 — both confirmed and fixed:**
- L-1: the Task 2 warn was placed at the shared post-resolution prefix,
  which would leak it into --status (contradicting test (f) and the locked
  --status-stays-as-is decision). Fixed: warn scoped to the two mutating
  paths only; test (f) now asserts no warn from --status after walk-up.
- L-2: the fallback taxonomy label was unpinned ("Claude's Discretion")
  while the headline wrong-/v1 404 case (exit_code set, kind="") lands
  exactly there. Fixed: two more pinned labels — "http-error" (exit_code
  >= 400, no kind) and "unclassified" (true unknowns) — propagated to the
  objective line, Task 5 test (d) (now seven classes), and the T6 smoke
  expectation.

**kimi R2 (on the kimi-R1-fixed plan): B=0 H=0 M=0 L=3 — one already fixed, two confirmed and fixed:**
- L-1: "60s total" was per-REQUEST, not total — the probe passed no
  continuation_breaker, so a fresh threshold-5 breaker plus
  _continue_truncated's budget=2 allowed ~3x60s on a persistently-truncating
  backend. Fixed: the probe call now passes
  `continuation_breaker=TruncationBreaker(threshold=1)` — the first
  truncation raises kind="truncated" BEFORE any continuation request; hard
  60s bound restored; new pinned label "truncated-output" (eight classes
  total, all propagated).
- L-2: Task 5 injection (2) cited a helper-call-assertion guard test that no
  behavior mandated. Fixed: behavior (b) now asserts the helper was called
  exactly once per api backend and never via probe_backend.
- L-3: T6 smoke text omitted the fallback class for the headline 404 —
  already fixed by the ds-R2 L-2 edit.

## Declared positions (adjudicate these EXPLICITLY — say accept or refute with evidence, do not silently rediscover them)

A. D-08 interpretation: warn condition is `resolved_workspace != cwd.resolve()`,
   not a literal "not a git repo root" probe. A non-git directory that IS the
   workspace root gets no warning. Research-endorsed as the implementable form.
B. Probe cap is max_tokens=32, not a literal 1-token: the truncation
   continuation runs BEFORE the attempt check, so a truncating cap
   multiplies requests even at max_attempts=1. Boundary of the "snapshot of
   a real review" framing: thinking_type/thinking_budget/reasoning_effort
   are zeroed on the probe copy (deliberate — protects the 32-token cap);
   `stream` stays configured and remains the F1 regression witness.
C. Live probe covers type=api backends only; cli-type backends get an
   informational skip row (the offline probe already executes them).
D. Six tasks in one plan exceeds the generic 2-3 target; locked by user
   decision D-12.
E. The plan adds no .git probe to trust; the always-printed resolved path is
   the disambiguator.
F. Task 4 Step 1.5 deliberately changes the REVIEW-PATH HTTP error message
   shape (excerpt appended for openai/anthropic) — an owned decision, not a
   side effect. Existing formatter tests are substring-style and verified
   unaffected.

## Output contract

- Findings with severity BLOCKER/HIGH/MEDIUM/LOW, each citing the EXACT plan
  line/section your finding is about (you cannot cite real source files —
  cite the plan text location instead).
- If a finding's asserted-wrong value and proposed-correct value are
  identical strings, discard it before reporting (degenerate-output
  detector).
- End with exactly: `SCORECARD: B=<n> H=<n> M=<n> L=<n>`
- A CLEAN verdict (0/0/0/0) is a valued outcome — report it if that is what
  you find. A manufactured finding costs more than a missed nit.
<<<BRIEFING END>>>

## The current plan text (54-01-PLAN.md, full contents)

<<<PLAN START>>>
---
phase: 54-router-onboarding-compat
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - src/code_forge/gate.schema.json
  - src/code_forge/cli.py
  - src/code_forge/doctor.py
  - src/code_forge/backend.py
  - src/code_forge/llm_invoke.py
  - README.md
  - tests/test_schema_corpus.py
  - tests/test_cli_trust.py
  - tests/test_contract_wiring.py
  - tests/test_doctor.py
  - tests/test_backend.py
  - tests/test_llm_invoke.py
  - tests/test_mcp_server.py
autonomous: false
requirements: [ROUTER-02, ROUTER-03, ROUTER-04, ROUTER-05]

must_haves:
  truths:
    - "A user reading the base_url field description (schema hover or file) learns that forge appends /chat/completions verbatim and that owning the /v1 prefix is their responsibility"
    - "code-forge trust run from a subdirectory of a configured project resolves the ancestor .code-forge/gate.yaml instead of erroring"
    - "Bare trust and trust --revoke print the resolved absolute gate.yaml path on stderr before any trust-store mutation"
    - "A mutating trust op (bare trust / --revoke) run where the resolved workspace differs from cwd prints a stderr warning and still proceeds; --status stays silent-as-before"
    - "code-forge doctor --live performs one real chat completion per configured api backend, bounded at 60s with zero retries"
    - "A live-probe failure is reported with an error class, the response-body excerpt where one exists, and a suggested action, and makes doctor exit 1"
    - "code-forge doctor without --live makes no network calls (offline guarantee of the default path preserved)"
    - "code-forge doctor prints the resolved user-level config path, or its would-be location with a hint when absent"
    - "README points users at ~/.config/code-forge/config.yaml inheritance (set once, project wins by name)"
  artifacts:
    - path: "src/code_forge/gate.schema.json"
      provides: "base_url /v1 semantics description"
      contains: "/chat/completions"
    - path: "src/code_forge/cli.py"
      provides: "trust walk-up + path printing; doctor --live flag"
      contains: "resolve_workspace"
    - path: "src/code_forge/doctor.py"
      provides: "live probe rows + user-config info line"
      contains: "probe_backend_live"
    - path: "src/code_forge/backend.py"
      provides: "probe_backend_live helper + LiveProbeResult"
      contains: "def probe_backend_live"
    - path: "src/code_forge/llm_invoke.py"
      provides: "kind= classification at connection/parse/credential raise sites"
      contains: "kind=\"conn\""
    - path: "README.md"
      provides: "user-level config pointer"
      contains: ".config/code-forge/config.yaml"
  key_links:
    - from: "src/code_forge/cli.py"
      to: "src/code_forge/workspace.py resolve_workspace"
      via: "_run_trust walk-up resolution"
      pattern: "resolve_workspace"
    - from: "src/code_forge/cli.py doctor dispatch"
      to: "doctor.run_doctor(live=...)"
      via: "args.live threading"
      pattern: "--live"
    - from: "src/code_forge/doctor.py _check_backends"
      to: "src/code_forge/backend.py probe_backend_live"
      via: "direct call outside the cached probe_backend path"
      pattern: "probe_backend_live"
    - from: "src/code_forge/backend.py probe_backend_live"
      to: "llm_invoke.llm_invoke"
      via: "dataclasses replace() timeout_s=60 copy + max_attempts=1"
      pattern: "timeout_s=60"
    - from: "src/code_forge/llm_invoke.py new kind= values"
      to: "src/code_forge/mcp_server.py:958 kind whitelist"
      via: "additive-only: new kinds stay outside the fallback whitelist"
      pattern: "kind=\"(conn|credentials|sse_body|bad_body)\""
---

<objective>
Close the remaining four OmniRoute-class router onboarding friction items in one
batch, in the locked internal order F2 (schema text) -> F3 (trust path) ->
F5 (user-config discoverability) -> F4 (doctor --live network probe).

Purpose: ROUTER-02..05, the v2.8 tail rolled into v2.9. F1 already shipped by
prevention (explicit stream flag); the shared-parse SSE tolerance stays deferred
on its stated trigger and is OUT of scope.
Output: schema description fix, trust walk-up + visibility, doctor user-config
line + README pointer, and an opt-in live backend probe on doctor with a
eight-class error taxonomy (five D-04 classes + truncated-output + http-error + unclassified).
</objective>

Base: main @ 4087b05, tree clean.

<interfaces>
workspace.py:19-51:
  resolve_workspace(cwd: Path, env: Mapping[str,str], home=None) -> Path
  Priority: FORGE_PROJECT_DIR > nearest ancestor with .code-forge/gate.yaml
  (skipping $HOME) > cwd as-is (resolved). Used today by doctor.py:79 and
  mcp_server.py:160-162. The CLI review path has NO walk-up (cli.py:2779
  anchors at cwd) -- the walk-up rule lives here, per ADR-0006/0009.

llm_invoke.py:
  class LLMInvokeError(Exception) :52 -- fields include exit_code, is_timeout,
    retryable, retry_after, and `kind: str = ""` (:62). kind docstring :71-77:
    "Matching on kind (not message text) keeps the MCP fallback routing immune
    to message rewording." Existing kinds: truncated/empty/stub_model/no_json.
  llm_invoke(prompt, backend, timeout_s=None, expected_keys=None,
    max_attempts=5, ...) :886. max_attempts=1 = exactly one attempt, zero
    retries (loop :1377, re-raise :1497).
  effective_invoke_timeout_s :558-592: backend.timeout_s OVERRIDES caller
  timeout_s (priority 1 :580-581) -- the probe MUST override on the config copy.
  _parse_response_body(raw, backend_name) :1532-1551 raises LLMInvokeError
    embedding body_text[:200] (:1549) -- the existing ~200-byte excerpt.
  URL assembly :1561: url = backend.base_url + "/chat/completions" (verbatim).
  Credential block in _invoke_api :1326-1350 -- four raise statements
    (:1332 cannot read api_key_file, :1337 api_key_file empty, :1343 env var
    not set, :1347 neither configured).
  URLError handlers :1606 (openai), :1746 (anthropic), :1918 (vertex);
  OSError handlers :1613, :1753, :1925.
  Truncation: _TruncatedResponse is caught (:1485) and _continue_truncated
  runs BEFORE the attempt check -- a cap small enough to truncate costs extra
  requests even with max_attempts=1. Size prompt+cap so a healthy backend
  finishes with stop (tens of tokens, not literal 1).

backend.py:
  @dataclass(frozen=True) ProbeResult :134-139 (ok, error) -- too thin for
    the live probe; add a sibling result type.
  @dataclass(frozen=True) BackendConfig :142+ -- dataclasses.replace is legal.
    Fields: max_tokens (:159, default 16384), output_ceiling (:160),
    stream (:171), timeout_s (:172).
  probe_backend(backend, env, cache_dir, time_fn, timeout) :777-825 -- checks a
    5-minute success cache FIRST (:805), bypasses configured cli backends
    (:812), dispatches type=api to _probe_api (:816-817).
  _probe_api :896-927 -- offline only, docstring "No subprocess, no network
    call". This guarantee must remain true for the default doctor path.
  IMPORT DIRECTION: llm_invoke.py:29 does `from .backend import ...`. A
    module-level import of llm_invoke inside backend.py would be circular.
    The live helper must import llm_invoke FUNCTION-LOCALLY (precedent:
    doctor.py :110/:126/:128/:168).

doctor.py:
  _check_backends(workspace, gate_data, env) :118-162 -- builds (ok, msg)
    rows, calls probe_backend per config (:151), provenance + SHADOWED notes.
  run_doctor(cwd, env) :437-509; backend rows set has_fail :466-469; exit
    `return 1 if has_fail else 0` :509. _line :512-520 (ok=None => SKIP).
  Free-form info-line precedent: registries block :500-503 (plain print,
  no PASS/FAIL tag, never affects exit code).

cli.py:
  _run_trust(args, cwd) :1297; resolution :1315 (`cwd / ".code-forge" /
    "gate.yaml"`, no walk-up); contracts path :1337; --status :1339-1368;
    --revoke :1370-1382 (revoke_trust :1371); empty-config guard :1384-1397;
    dangerous-fields display :1400-1407; record_trust :1408. Trust functions
    are imported inside the function body (:1305-1313) -- tests patch
    code_forge.trust.record_trust.
  doctor parser :670-674 (no args today); dispatch :1870-1871
    `run_doctor(cwd=Path.cwd(), env=os.environ)`.

user_config.py:
  user_config_dir() :22-34 (FORGE_CONFIG_DIR > XDG > ~/.config/code-forge);
  user_config_path() :37-63 (resolved config.yaml path or None).

mcp_server.py:958 -- the ONLY kind consumer: `_can_fallback = exc.kind in
  (truncated/empty/stub_model/no_json)`. New kind values are additive-safe:
  kind="" already falls outside the whitelist, so new kinds change nothing
  there.

tests/conftest.py:19-30 -- autouse _isolate_user_config patches
  code_forge.user_config.load_user_backends to `lambda: {}` for ALL tests.
  Tests for the doctor user-config line MUST patch user_config_path (or the
  symbol as imported into doctor.py), never load_user_backends.
</interfaces>

<tasks>

<task type="auto">
  <name>Task 1: F2 -- base_url /v1 semantics in gate.schema.json</name>
  <files>src/code_forge/gate.schema.json, tests/test_schema_corpus.py</files>
  <action>
    Replace ONLY the base_url "description" string (:269-272) with a longer
    single-line JSON string (file convention: one line, qualifiers inline, no
    embedded newlines). Content requirements, per the locked decision: forge
    concatenates base_url + "/chat/completions" verbatim with no /v1
    normalization; whether the path needs /v1 is the operator's
    responsibility; show both shapes (an OpenAI-style `https://host/v1` and a
    router root). Do not edit any other schema field. Do NOT touch
    build/lib/code_forge/gate.schema.json -- stale gitignored artifact
    (.gitignore:8). Do NOT add README copy of these semantics (the locked
    decision bans a second wording that can drift; README gets only the F5
    pointer in Task 3).
    Add one string-guard test in tests/test_schema_corpus.py: load the schema
    the same way the existing tests load it, read the base_url description,
    assert it contains "/v1" and "/chat/completions".
    This is schema text: commit with the docs marker. No forge review cycle
    for this task.
  </action>
  <verify>
    <automated>python -m pytest tests/test_schema_corpus.py tests/test_backend.py -q</automated>
  </verify>
  <acceptance_criteria>
    - git diff touches exactly src/code_forge/gate.schema.json and
      tests/test_schema_corpus.py; build/lib untouched -- verify with
      an mtime check instead of git status (build/ is gitignored)
    - The new base_url description is a single JSON string containing the
      substrings "/v1" and "/chat/completions", and the schema still parses
    - The full schema-validation suite stays green (test_backend.py loads/
      validates against this schema)
    - Bug-injection proof: revert the description edit, watch the new guard
      test FAIL, restore, watch it PASS
    - No non-ASCII introduced
  </acceptance_criteria>
  <done>Schema describes /v1 ownership; guard test green; injection proven.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: F3 -- trust walk-up + resolved-path print + off-root warn</name>
  <files>src/code_forge/cli.py, tests/test_cli_trust.py, tests/test_contract_wiring.py</files>
  <behavior>
    - Test (a) walk-up: gate.yaml in tmp_path/.code-forge; call _run_trust
      with cwd=tmp_path/"sub"/"dir" (mkdir parents); record_trust (patched)
      receives tmp_path/.code-forge/gate.yaml
    - Test (b) print-before-mutate: bare trust prints the resolved absolute
      gate.yaml path on stderr BEFORE record_trust is called
    - Test (c) warn: when resolved workspace != cwd (walk-up climbed), stderr
      carries a warning naming both the cwd and the resolved workspace; exit
      still 0 (EXIT_PASS)
    - Test (d) revoke path: --revoke also prints the resolved absolute path
      before revoke_trust is called
    - Test (e) no-ancestor: cwd with no ancestor gate.yaml keeps the existing
      "gate.yaml not found" error (resolve_workspace falls back to cwd) --
      regression, unchanged behavior
    - Test (f) --status output unchanged (it already prints the path) --
      including from a subdirectory: --status emits NO warn line even when
      the resolution climbed (pins the warn to mutating paths)
    - Test hygiene: add an autouse fixture doing
      monkeypatch.delenv("FORGE_PROJECT_DIR", raising=False) to BOTH
      tests/test_cli_trust.py and tests/test_contract_wiring.py
  </behavior>
  <action>
    In _run_trust (cli.py:1297): replace the direct join at :1315 with
    `workspace = resolve_workspace(cwd, os.environ)` then
    `gate_yaml_path = workspace / ".code-forge" / "gate.yaml"`. Add
    `from .workspace import resolve_workspace` to the function-local import
    block. contracts_yaml_path (:1337) derives from the same workspace, not
    from cwd. The two `resolve_contract_specs(contracts_yaml_path, cwd)`
    calls (--status branch and bare-trust branch) pass the resolved
    workspace as the second argument too.
    Warn (per the locked warn-and-proceed decision): on the two MUTATING
    paths only (bare trust and --revoke), if `workspace != cwd.resolve()`,
    print a one-line warning to stderr naming both paths. --status is a
    read-only probe and stays exactly as-is (the locked --status-stays-as-is
    decision): no warn line from --status even when run from a
    subdirectory. Do NOT place the warn at the shared post-resolution
    prefix (resolution precedes the branch point, so an unscoped warn would
    leak into --status) -- gate it on the mutating branches. No subprocess
    `git rev-parse`; do not add a .git probe.
    Print (per the locked print-before-mutate decision): print the resolved
    absolute gate_yaml_path to stderr immediately before record_trust (:1408)
    and immediately before revoke_trust (:1371). Leave the existing
    post-mutation "Trusted:"/"Trust revoked" prints in place. --status
    (:1339-1368) unchanged.
    Behavior note to preserve: running bare trust in a directory with no
    ancestor gate.yaml must still hit the existing not-found EXIT_CLI_ERROR
    (resolve_workspace falls back to cwd).
  </action>
  <verify>
    <automated>python -m pytest tests/test_cli_trust.py tests/test_contract_wiring.py -q</automated>
  </verify>
  <acceptance_criteria>
    - All six behavior tests above exist and pass
    - Bug-injection (at the call site, per house rule -- three separate
      injections, each proven then reverted):
      (1) delete the resolve_workspace call and restore the cwd-join ->
      test (a) FAILS; restore -> PASS
      (2) delete the pre-record_trust print -> test (b) FAILS; restore -> PASS
      (3) delete the warn block -> test (c) FAILS; restore -> PASS
    - Existing test_cli_trust.py and test_contract_wiring.py suites green
    - python -m ruff check src/code_forge/cli.py (line length 105)
  </acceptance_criteria>
  <done>Trust from a subdirectory resolves the ancestor gate.yaml, announces
  the absolute path before mutating, warns when it walked, and every
  injection point is proven caught.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 3: F5 -- doctor user-config line + README pointer</name>
  <files>src/code_forge/doctor.py, tests/test_doctor.py, README.md</files>
  <behavior>
    - Test (a): with user_config_path patched to return a tmp path, doctor
      output contains that path string
    - Test (b): with user_config_path patched to return None, doctor output
      contains the would-be location (user_config_dir() / "config.yaml",
      dir also patched) plus a hint that shared backends can live there
    - Test (c): the line never affects the exit code (ok-path stays exit 0)
    - Test (d): default run (no patching beyond conftest) does not crash --
      user_config_path returning None is handled
  </behavior>
  <action>
    Doctor line (code): in run_doctor, adjacent to the registries free-form
    block (doctor.py:500-503 -- plain print, no PASS/FAIL tag), add one
    informational line. Function-local import:
    `from code_forge.user_config import user_config_dir, user_config_path`.
    Present: print the resolved path. Absent: print the would-be location
    `user_config_dir() / "config.yaml"` with a short hint that shared
    backends can be set there once. Never a FAIL/SKIP row, never touches
    has_fail. Placement must run regardless of workspace state, i.e. in the
    always-run tail, not inside a workspace-gated block.
    README pointer (docs): under "## Backend configuration", add 2-4 lines:
    backends can be set once in ~/.config/code-forge/config.yaml, project
    gate.yaml wins by name, FORGE_CONFIG_DIR overrides the location. Do NOT
    restate the base_url /v1 semantics here (banned duplicate wording).
    Two commits from this task: the doctor line rides the code commit
    (logic, forge review scope); the README edit is a separate docs-marker
    commit.
  </action>
  <verify>
    <automated>python -m pytest tests/test_doctor.py tests/test_user_config.py -q</automated>
  </verify>
  <acceptance_criteria>
    - Tests (a)-(d) pass; the tests patch code_forge.user_config.user_config_path
      (and user_config_dir for (b)), NEVER load_user_backends
    - Bug-injection: delete the new print -> tests (a) and (b) FAIL;
      restore -> PASS
    - Doctor exit code unchanged on a healthy workspace
    - README diff is confined to the Backend configuration section and
      contains no /v1 wording
  </acceptance_criteria>
  <done>Doctor surfaces the user-level config location every run; README
  points at the inheritance that already ships; injection proven.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 4: F4 core -- kind= taxonomy + probe_backend_live helper</name>
  <files>src/code_forge/llm_invoke.py, src/code_forge/backend.py, tests/test_llm_invoke.py, tests/test_backend.py, tests/test_mcp_server.py</files>
  <behavior>
    llm_invoke kind tests (tests/test_llm_invoke.py):
    - _parse_response_body given a body starting with "data: " raises
      LLMInvokeError with kind="sse_body"; given plain non-JSON raises
      kind="bad_body"; both keep the existing body[:200] excerpt in the
      message and retryable=True
    - URLError from each provider path raises kind="conn" (openai at minimum)
    - OSError from the api path raises kind="conn"
    - Missing env key / empty api_key_file / neither configured raise
      kind="credentials"
    - Vertex credential failures raise kind="credentials"; the google-auth
      ImportError stays kindless
    - HTTP error messages embed the body excerpt on ALL provider paths after
      the Step 1.5 formatter change; vertex already embeds it (regression
      guard)
    - Row-boundary normalization: _classify_live_failure's detail has no
      newlines even when the excerpt contains them
    - mcp fallback whitelist unaffected: extend
      test_dispatch_sampling_unknown_kind_never_falls_back
      (tests/test_mcp_server.py:919) with kind="conn" asserting NO fallback
    Probe helper tests (tests/test_backend.py), patching
    code_forge.llm_invoke.llm_invoke at the SOURCE module:
    - Success: probe_backend_live returns ok=True
    - Config copy: the backend object passed to llm_invoke has timeout_s=60
      even when the source config sets timeout_s=1800, and max_tokens at the
      small probe cap, with max_completion_tokens/output_ceiling zeroed
    - max_attempts=1 and expected_keys containing the probe key are passed
    - Each taxonomy branch, pinned strings: is_timeout -> "timeout";
      exit_code 401/403 or kind="credentials" -> "credential-rejected";
      kind="conn" -> "connection-refused"; kind="sse_body" -> "SSE-mixed";
      kind="bad_body" -> "JSON-malformed"; kind="truncated" (the probe's
      threshold-1 breaker fired) -> "truncated-output"; exit_code >= 400
      with no kind (the wrong-/v1 headline case) -> "http-error"; anything
      else -> "unclassified". Each carries a non-empty suggested action.
      All eight labels are pinned by tests.
  </behavior>
  <action>
    Step 1 -- additive kind= classification in llm_invoke.py (no behavior
    change beyond the new field):
    - _parse_response_body raise (:1547): set kind="sse_body" when the
      decoded body (lstripped) starts with "data: ", else kind="bad_body".
      Classification ONLY -- parsing behavior unchanged.
    - URLError handlers (:1606, :1746, :1918) and OSError handlers (:1613,
      :1753, :1925): add kind="conn".
    - Credential block raises (:1332, :1337, :1343, :1347): add
      kind="credentials".
    - Vertex credential raises (:1857, :1862, :1871): add kind="credentials".
      The google-auth ImportError (:1839) is a missing-dependency error, NOT
      a credential failure -- leave it kindless.
    Add the new kind values to the kind docstring list so the enumeration
    stays complete.
    Step 1.5 -- make the HTTP excerpt survive on ALL provider paths:
    - _format_error_message (:691-715) takes body_excerpt but never uses it
      -- the openai (:1601) and anthropic (:1741) HTTP handlers compute and
      pass the excerpt and it is dropped; only vertex embeds it inline
      (:1915). Extend the formatter to append the excerpt when non-empty
      (e.g. `; body: <excerpt>`), covering openai+anthropic through the one
      shared point. Message shape changes on the review path too -- that is
      intended and owned by this plan.
    Step 2 -- live probe helper in backend.py beside _probe_api:
    - New frozen dataclass LiveProbeResult: ok: bool, error_class:
      Optional[str] = None, detail: Optional[str] = None, suggestion:
      Optional[str] = None.
    - New function probe_backend_live(cfg: BackendConfig) -> LiveProbeResult.
      FUNCTION-LOCAL `from .llm_invoke import LLMInvokeError, llm_invoke`
      (module-level would be circular).
    - Build the probe config: add `replace` to the existing
      `from dataclasses import dataclass, field` import, then
      `replace(cfg, timeout_s=60, max_tokens=32, max_completion_tokens=0,
      output_ceiling=0, thinking_type="", thinking_budget=0,
      reasoning_effort="")`. The timeout_s override on the COPY is mandatory
      -- backend.timeout_s beats the caller argument. The thinking fields
      are zeroed because thinking burn can consume the 32-token cap before
      the JSON key arrives, re-arming the truncation continuation that
      max_attempts=1 is supposed to exclude. Keep `stream` as configured.
    - Call llm_invoke with a tiny JSON-demanding prompt, max_attempts=1,
      expected_keys=frozenset({"ok"}), AND
      `continuation_breaker=TruncationBreaker(threshold=1)`. The breaker is
      mandatory: without it llm_invoke builds a fresh threshold-5 breaker
      per call and _continue_truncated (budget=2) can issue up to 2 extra
      requests, each with its own fresh 60s deadline -- a
      persistently-truncating backend would cost ~3x60s, breaking the
      60s-total bound. With threshold=1 the first truncation's
      record_truncation raises TruncationBreakerError (kind="truncated",
      retryable=False) BEFORE any continuation request. 32 tokens is
      deliberate: a literal 1-token cap triggers the truncation-continuation
      machinery which runs BEFORE the attempt check, which would multiply
      requests; and a prose reply raises kind="no_json", so the prompt must
      demand JSON.
    - Success -> LiveProbeResult(ok=True). LLMInvokeError -> classify in a
      small helper (_classify_live_failure) into error_class + suggestion;
      detail = " ".join(str(exc).split()) -- whitespace-normalized at this
      boundary, because embedded excerpts can carry newlines that would wrap
      the single-line doctor row.
    - Do NOT route through probe_backend: its 5-minute success cache would
      swallow the live call.
  </action>
  <verify>
    <automated>python -m pytest tests/test_llm_invoke.py tests/test_backend.py tests/test_mcp_server.py -q</automated>
  </verify>
  <acceptance_criteria>
    - grep -c 'kind="' src/code_forge/llm_invoke.py goes from 16 (baseline)
      to 31 -- exactly 15 added occurrences: 6 conn + 7 credentials (4
      shared + 3 vertex) + 2 at the conditional parse pair; no existing
      raise loses or changes its kind. Shape note: write the parse-site
      classification so both kind= spellings appear (two raise arms) -- a
      single ternary yields one occurrence (30 total) and fails this
      criterion
    - mcp_server.py source untouched by this task
    - Bug-injection (each proven FAIL then reverted to PASS): five
      injections listed (timeout_s drop, max_attempts change, taxonomy
      branch deletion, kind= addition reversion, excerpt-append drop)
    - _probe_api docstring/guarantee untouched: default offline probe makes
      no network calls
    - python -m ruff check
  </acceptance_criteria>
  <done>Eight-class taxonomy distinguishable via structured fields; helper
  bounded at 60s/one attempt; cache bypassed; every injection proven.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 5: F4 wiring -- doctor --live end to end</name>
  <files>src/code_forge/cli.py, src/code_forge/doctor.py, tests/test_doctor.py</files>
  <behavior>
    With the live helper patched at code_forge.backend.probe_backend_live:
    - (a) Default path: live flag off -> the live helper is NEVER called and
      no live rows appear (the offline no-network guarantee)
    - (b) live on + helper returns ok -> a PASS row mentioning live appears
      per api backend; assert the helper mock was called exactly once per
      api backend (and never via probe_backend)
    - (c) live on + helper returns a failure -> FAIL row, doctor exits 1 via
      the existing has_fail pipeline
    - (d) each of the eight error classes renders its pinned class label
      plus suggestion in the row text
    - (e) cli-type backend under live -> informational row (ok=True), helper
      not called for it
    - (f) the offline probe_backend row still appears alongside
    - (g) CLI dispatch end-to-end: a main()-level test invoking
      `code-forge doctor --live` and asserting run_doctor receives
      live=True. Patch target: code_forge.doctor.run_doctor
  </behavior>
  <action>
    Parser (cli.py:670-674): capture the doctor add_parser result and add
    --live (store_true) with help text stating it performs one real
    completion per api backend with a 60s budget and no retries.
    Dispatch (cli.py:1870-1871): pass live=args.live into run_doctor.
    run_doctor: signature gains live: bool = False; thread it into
    _check_backends(workspace, gate_data, env, live=live).
    _check_backends: after the existing offline row per config, when live is
    set and cfg.type == "api", call probe_backend_live(cfg) DIRECTLY (never
    through probe_backend) and append a row: ok ->
    (True, name + provenance + " live: ok"); failure ->
    (False, name + provenance + " live: " + error_class + " -- " + detail +
    "; " + suggestion). cli-type backends get an informational
    (True, "... live: skipped (cli backends are trusted as configured; no
    live probe applies)") row. Serial loop only -- no concurrency machinery.
    Failures flow through the existing has_fail accumulation to the exit-1
    pipeline. Zero new exit-code plumbing. Default (no --live) adds no rows
    and no network.
    Wrap the live call in the same try/except style the offline loop uses
    so an unexpected exception becomes a FAIL row, never a crash.
  </action>
  <verify>
    <automated>python -m pytest tests/test_doctor.py -q && python -m pytest tests/test_doctor.py tests/test_cli_trust.py tests/test_contract_wiring.py tests/test_user_config.py tests/test_backend.py tests/test_llm_invoke.py tests/test_schema_corpus.py -q</automated>
  </verify>
  <acceptance_criteria>
    - Behaviors (a)-(g) all pass
    - Bug-injection: (1) call the live helper unconditionally (drop the live
      guard) -> test (a) FAILS -- the most important injection in the phase;
      (2) route the live call through probe_backend instead of the direct
      helper -> a test asserting the helper (not probe_backend) received
      the call FAILS; (3) swallow the failure row (always ok=True) -> test
      (c) FAILS and exit code stays 0; (4) delete `live=args.live` from the
      dispatch -> the main()-level test (g) FAILS
    - Full quick subset green; then full `python -m pytest` green
    - No graph.db-dependent tests added
    - python -m ruff check
  </acceptance_criteria>
  <done>doctor --live probes every api backend serially, classifies failures
  with action lines, exits 1 on failure, and the default path is proven
  network-free by injection.</done>
</task>

<task type="checkpoint:human-verify" gate="blocking">
  <name>Task 6: Real-path smoke (Golden Rule 3)</name>
  <action>Human runs five real-path checks against the merged work.</action>
  <how-to-verify>
    1. F3 real path: cd into a SUBDIRECTORY of a real configured project,
       run `code-forge trust`. Expect: stderr warning naming cwd and the
       resolved workspace, the absolute gate.yaml path printed before the
       trust record, exit 0. Then `code-forge trust --revoke` to restore.
    2. F4 real path: on a host with a configured api backend, run
       `code-forge doctor --live`. Expect: one live row per api backend,
       PASS for healthy ones, wall time bounded near 60s for a hung one.
    3. Failure-class witness: point a scratch backend's base_url at a wrong
       path (missing/extra /v1) and re-run `doctor --live` -- expect an
       "http-error" FAIL row whose detail shows the response-body excerpt,
       plus a suggested action, and doctor exit 1. Restore after.
    4. Optional: SSE-always router witness if reachable.
    5. Confirm plain `code-forge doctor` (no --live) completes with no live
       rows.
  </how-to-verify>
</task>

</tasks>

<threat_model>
| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-54-01 | Tampering | _run_trust walk-up | mitigate | Resolved absolute path printed to stderr BEFORE every trust-store mutation; mutating paths warn when workspace != cwd (--status exempt); --revoke restores |
| T-54-02 | Info disclosure | probe_backend_live | accept | Probe sends only a fixed JSON-asking prompt to backends the operator already configured for review traffic; no repo content, no env values in the payload |
| T-54-03 | Elevation | doctor --live | mitigate | Opt-in flag only; default path proven network-free by test (a) + injection (1); 60s bound + zero retries cap blast radius |
| T-54-04 | DoS | live probe hang | mitigate | replace() timeout_s=60 on the config copy defeats backend.timeout_s overrides |
| T-54-SC | Tampering | package installs | accept | Zero new dependencies |
</threat_model>

<success_criteria>
1. gate.schema.json base_url description covers verbatim concatenation and
   /v1 ownership; guard test green; build/lib untouched (ROUTER-02)
2. Trust from a subdirectory resolves the ancestor gate.yaml; bare trust and
   --revoke print the absolute path before mutating; off-root run warns on
   stderr and proceeds; no-ancestor case still errors (ROUTER-03)
3. doctor --live makes one bounded (60s, zero-retry) real completion per api
   backend, classifies failures into the eight pinned classes with excerpt +
   suggested action, and exits 1 on failure; default doctor makes no network
   calls (ROUTER-04)
4. doctor prints the user-level config path (or its would-be location) every
   run; README points at ~/.config/code-forge/config.yaml inheritance
   (ROUTER-05)
5. Every bug-injection point in Tasks 1-5 proven FAIL-then-PASS
6. Full suite green; Task 6 real-path smoke approved
</success_criteria>
<<<PLAN END>>>

## Your task

This is the FINAL exit round for this plan (an internal goal-backward
reviewer and an internal 8-pass PBR reviewer, both WITH real repo access,
have already independently re-verified this exact text against the live
repository and both report zero findings; two prior external reviewers WITH
repo access, deepseek and kimi-k2.7, converged to 0 BLOCKER/0 HIGH/0 MEDIUM
across two rounds each, with all LOW findings fixed as shown above).

Given you cannot verify against real source, your pass is a text-consistency
safety net: catch anything the other reviewers, who focused on source-code
ground-truth, might have missed purely at the level of "does this document
make internal sense and cover what it claims to cover." Follow the output
contract above exactly, ending with `SCORECARD: B=<n> H=<n> M=<n> L=<n>`.
