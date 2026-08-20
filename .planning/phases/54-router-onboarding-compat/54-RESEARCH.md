# Phase 54: Router onboarding compat remainder - Research

**Researched:** 2026-08-18
**Domain:** forge CLI/doctor/trust internals (no new external dependencies)
**Confidence:** HIGH (every file:line re-grepped on main @ 4087b05 this session)
**Base:** main @ 4087b05, tree clean. Branch from main (CONTEXT: headers branch merged; 43.1 warning stale).

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** F4 probe = real 1-token chat completion (minimal max_tokens), end-to-end URL assembly + response parsing. Connectivity-only probing rejected.
- **D-02:** UX is `code-forge doctor --live`, a flag on the existing doctor backend check, NOT a new command.
- **D-03:** `--live` probes ALL configured backends, serially.
- **D-04:** Failure diagnostics = error class (SSE-mixed / JSON-malformed / timeout / connection-refused / credential-rejected) + first ~200 bytes of response body + one suggested-action line.
- **D-05:** 60s total timeout, ZERO retries.
- **D-06:** Live-probe failure => doctor exit 1 via the existing has_fail pipeline (doctor.py:509). `--live` opt-in.
- **D-07:** Mutating trust ops (bare `trust`, `--revoke`) print the resolved absolute gate.yaml path BEFORE acting. `--status` unchanged.
- **D-08:** cwd not a git repo root => warn on stderr, proceed. No hard block.
- **D-09:** Add walk-up resolution to trust (currently `cwd / ".code-forge" / "gate.yaml"` directly), matching the review path's rule, following ADR-0009.
- **D-10:** F2 = gate.schema.json base_url description only (~5 lines; verbatim concatenation + operator owns /v1). No README copy.
- **D-11:** F5 = doc pointer to ~/.config/code-forge/config.yaml inheritance PLUS a doctor output line surfacing it.
- **D-12:** One plan (54-01) covering F2+F3+F5+F4.
- Review protocol: CP1 internal to 0/0/0/0, CP1b external (deepseek + kimi + gemini) iterated, final external 0/0/0/0 is the last word.

### Claude's Discretion
- Probe prompt content, exact excerpt length (~200 bytes), error taxonomy strings.
- Wave/task ordering inside 54-01 (F2/F3/F5 before F4 recommended).

### Deferred Ideas (OUT OF SCOPE)
- Shared-parse SSE auto-detect tolerance (original F1 shape) -- deferred on trigger.
- `mcp-cli-gate-lookup-divergence` todo -- separate defect, not folded.
- F1 (shipped v2.8, a47d888) -- out of scope entirely.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| ROUTER-02 | gate.schema.json documents base_url /v1 semantics (docs only) | F2 section: exact schema location, description conventions, verbatim-concat evidence |
| ROUTER-03 | `forge trust` prints resolved path, warns off-project, ADR-0009 walk-up | F3 section: resolve_workspace reuse, _run_trust mutation points, test pattern |
| ROUTER-04 | doctor probes a backend live | F4 section: llm_invoke one-shot recipe, timeout/retry landmines, taxonomy mapping |
| ROUTER-05 | Point users at existing ~/.config inheritance (docs + doctor line) | F5 section: user_config_path() reuse, doctor info-line precedent, conftest trap |
</phase_requirements>

## Summary

All four items are small extensions of existing machinery; nothing needs to be
built from scratch, and the triage's DO-NOT-rebuild constraints hold on current
main. F4 is the only item with real design content: the probe can ride the
unmodified `llm_invoke` (one call, `max_attempts=1`, a `dataclasses.replace`
backend copy with `timeout_s=60` and a small output cap), but three landmines
in the timeout/retry/truncation machinery must be designed around, and D-04's
five-class error taxonomy is NOT fully distinguishable through
`LLMInvokeError`'s structured fields today -- the clean fix is adding `kind=`
values at two raise sites in llm_invoke.py (additive, safe against the one
whitelist consumer). F3's walk-up reuses `resolve_workspace` verbatim; note
CONTEXT D-09's phrase "the review path's rule" is imprecise -- the CLI review
path has no walk-up; the rule lives in the MCP/doctor path
(ADR-0006/0009). F2 and F5 are confined edits with established in-repo
conventions.

**Primary recommendation:** F4 probe = `doctor --live` flag threading to a new
live-probe helper in backend.py that calls `llm_invoke(prompt,
backend=replace(cfg, timeout_s=60, <small cap>), max_attempts=1,
expected_keys=...)` per configured api backend, classifying failures from
`LLMInvokeError` structured fields (+ two new `kind=` values); F3 trust uses
`resolve_workspace(cwd, os.environ)`; F2 edits only
`src/code_forge/gate.schema.json`; F5 adds one doctor info line via
`user_config_path()` plus a README pointer.

---

## PART 1 -- What exists today (main @ 4087b05, re-grepped 2026-08-18)

### F4 area: doctor plumbing

| Fact | Evidence |
|------|----------|
| `_check_backends(workspace, gate_data, env)` builds diag rows as `(ok, msg)` tuples; calls `probe_backend(cfg, env=env)` per config; marks `(project)`/`(user)` provenance and SHADOWED notes | doctor.py:118-161 (`probe_backend` call :151, provenance :145/:153, SHADOWED :147-149) |
| `run_doctor(cwd, env)` orchestrates; backend rows set `has_fail` at :466-469; exit `return 1 if has_fail else 0` | doctor.py:437-509 (exit :509) |
| `_line(label, msg, ok)` prints `  %-13s  %-40s %s` with PASS/FAIL/SKIP tags (ok=None => SKIP) | doctor.py:512-520 |
| doctor parser has NO arguments today; dispatch is `run_doctor(cwd=Path.cwd(), env=os.environ)` | cli.py:670-674 (parser), :1869-1871 (dispatch) |
| `run_doctor` has exactly one caller (the CLI dispatch) | grep: cli.py:1871 only |
| `probe_backend(backend, ..., env, cache_dir, time_fn, timeout)` checks a 5-min success cache FIRST (:805), bypasses explicitly-configured cli backends (:812), dispatches type=api to `_probe_api` (:816-817) | backend.py:777-825 |
| `_probe_api` is offline: docstring "No subprocess, no network call"; validates credentials via `credential_error` + vertex ADC fallback | backend.py:896-927 (docstring :900-904) |

### F4 area: minimal LLM invocation (reuse, do NOT rebuild)

| Fact | Evidence |
|------|----------|
| `llm_invoke(prompt, backend, timeout_s=None, expected_keys=None, max_attempts=5, initial_delay_s=2.0, continuation_breaker=None)` is the single public entry; None backend raises (no implicit fallthrough) | llm_invoke.py:886-971 |
| One-shot precedent: `RealFalsifier.falsify` calls `llm_invoke(prompt, backend=self._backend, expected_keys=frozenset({"verdict","reasoning"}))` | falsify_real.py:44-48 |
| Canary paths call `llm_invoke(prompt, backend=backend)` directly | cli.py:900, cli.py:1091 |
| `max_attempts=1` yields exactly one attempt, zero retries: `for attempt in range(max_attempts)` (:1377) + `if not exc.retryable or attempt == max_attempts - 1: raise` (:1497) | llm_invoke.py:1354, :1377, :1497 |
| Timeout chain `effective_invoke_timeout_s`: priority backend.timeout_s > caller timeout_s > FORGE_LLM_TIMEOUT_S > DEFAULT(1800); the 300/600 caps apply ONLY when the value came from env/default | llm_invoke.py:558-592; constants :165-167 |
| `BackendConfig` is `@dataclass(frozen=True)` -- `dataclasses.replace(cfg, timeout_s=60, max_tokens=N)` is legal; relevant fields: max_tokens:159 (default 16384), output_ceiling:160, stream:171, timeout_s:172 | backend.py:142-199 |
| Output-cap resolution: `cap = backend.max_completion_tokens or backend.max_tokens`; openai path sends key `max_tokens` when only max_tokens set | llm_invoke.py:269-280 |
| F1 prevention: `body["stream"] = bool(backend.stream)` sent explicitly on every api request | llm_invoke.py:310 (drifted from :226 cited in STATE.md -- re-grep proof) |
| URL assembly (F2's subject): `url = backend.base_url + "/chat/completions"` -- verbatim concatenation, no /v1 normalization | llm_invoke.py:1561 |

### F4 area: response classification (existing taxonomy machinery)

| Fact | Evidence |
|------|----------|
| `LLMInvokeError` carries structured fields: exit_code, stderr, duration_s, is_timeout, retryable, retry_after, kind | llm_invoke.py:52-78 |
| `kind` is the machine-readable class; docstring: "Matching on kind (not message text) keeps the MCP fallback routing immune to message rewording" | llm_invoke.py:71-77 |
| Existing kind values: "truncated", "empty", "stub_model", "no_json" | llm_invoke.py:71-74 |
| HTTP error path: HTTPError => exit_code=HTTP status, message via `_format_error_message` ("unauthorized (401). Check API key configuration" etc.), retryable from RETRYABLE_HTTP_STATUSES | llm_invoke.py:1595-1605, formatter :691-715 |
| `_parse_response_body(raw, backend_name)` raises LLMInvokeError embedding `body_text[:200]` -- ALREADY D-04's ~200-byte excerpt shape; kind="" today, retryable=True | llm_invoke.py:1532-1551; call sites :1594 (openai), :1734 (anthropic), :1911 (vertex) |
| URLError => "URLError from %s backend: %s", exit_code=-1, kind="" | llm_invoke.py:1606-1610 |
| OSError => "connection error from %s backend: %s", exit_code=-1, kind="" | llm_invoke.py:1613-1617 |
| TimeoutError => converted to LLMInvokeError is_timeout=True, retryable=False | llm_invoke.py:1475-1483 |
| Body-level provider errors (Zhipu/MiniMax): `_check_body_error` + `_suggestion` give per-code suggested actions | llm_invoke.py:718-751, :672-688 |
| Only `kind` consumer outside llm_invoke: mcp_server.py:958 whitelist (`_can_fallback = exc.kind in (...)`) -- NEW kind values are additive-safe (kind="" already falls outside the whitelist; behavior unchanged) | mcp_server.py:860, :958, :974 |

### F3 area: trust

| Fact | Evidence |
|------|----------|
| `_run_trust(args, cwd)`; resolution `gate_yaml_path = cwd / ".code-forge" / "gate.yaml"` (no walk-up, no $HOME guard) | cli.py:1297, :1315 |
| `--status` branch :1339-1368 (prints Path already); `--revoke` :1370-1382 (revoke_trust :1371, prints path AFTER at :1372-1374); bare trust: empty-config guard :1384-1397, dangerous-fields display :1400-1407, `record_trust` :1408, "Trusted:" print AFTER at :1409 | cli.py |
| trust parser: `trust_parser` with mutually-exclusive --status/--revoke group | cli.py:739-750 |
| Dispatch: `_run_trust(args, cwd=Path.cwd())` | cli.py:1874 |
| Walk-up helper EXISTS: `resolve_workspace(cwd, env, home=None)` -- FORGE_PROJECT_DIR > nearest ancestor with .code-forge/gate.yaml (skipping $HOME) > cwd as-is | workspace.py:19-51 |
| resolve_workspace consumers today: doctor.py:79, mcp_server.py:160-162 | grep |
| **The CLI review path has NO walk-up**: `_run` anchors `gate_yaml_path = cwd / ".code-forge" / "gate.yaml"` (:2779); eval path :1245 identical; resolve-outlet :3877 identical | cli.py:2779, :1245, :3877 |
| Git-root detection precedent: `git rev-parse --show-toplevel` subprocess | cli.py:1137-1140, :2600 |
| ADR-0009: D1 user config at ~/.config/code-forge (XDG, FORGE_CONFIG_DIR override); D2 walk-up skips $HOME; D5 forge_init refuses $HOME markers | docs/adr/0009-user-level-configuration.md |
| Test pattern for _run_trust: `_run_trust(SimpleNamespace(status=False, revoke=False), tmp_path)` + `patch("code_forge.trust.record_trust")` (works because _run_trust imports trust functions inside the function body, cli.py:1305) | test_contract_wiring.py:244-290 |
| Trust fixtures: `gate_dir` (tmp_path gate.yaml), `trust_home` (monkeypatch XDG_CONFIG_HOME), capsys for stderr | test_cli_trust.py:14-74 |

### F2 area: schema

| Fact | Evidence |
|------|----------|
| Live schema is `src/code_forge/gate.schema.json` (package data per pyproject); base_url entry at :269-272: "API base URL. Required for type=api with format=anthropic or format=openai." | gate.schema.json:269-272 |
| `build/lib/code_forge/gate.schema.json` is a STALE BUILD ARTIFACT -- `build/` is gitignored (.gitignore:8). Do NOT edit. | filesystem + .gitignore:8 |
| Description convention: single-line JSON strings (no embedded newlines observed); longer entries carry qualifiers + defaults + constraints in one string, e.g. api_key_env :273-276, max_completion_tokens :297-300 | gate.schema.json |
| No existing description mentions /v1 or path concatenation | grep base_url: only :269 and :386 (required list) |

### F5 area: user-level inheritance

| Fact | Evidence |
|------|----------|
| `user_config_dir()` -- FORGE_CONFIG_DIR > $XDG_CONFIG_HOME/code-forge > ~/.config/code-forge | user_config.py:22-34 |
| `user_config_path()` returns the resolved config.yaml path or None (legacy ~/.code-forge/gate.yaml fallback with deprecation warning) | user_config.py:37-63 |
| `load_user_backends()` lenient loader (warns, never crashes); `merge_backends(project, user)` project-wins-by-name | user_config.py:66-111 |
| `_merge_user_into` + call sites: eval :1249, review :2786, resolve-outlet :3879 (matches CONTEXT) | cli.py:171, :1249, :2786, :3879 |
| doctor ALREADY merges user backends and shows `(user)` provenance + SHADOWED notes, but NEVER prints the user config file path -- the discoverability gap is real | doctor.py:128-161 |
| Doctor info-line precedent without PASS/FAIL: the registries block prints free-form lines | doctor.py:500-503 |
| README has "### 6. Diagnostics" (:183) and "## Backend configuration" (:190); ZERO mentions of user-level config anywhere in README/docs outside ADR-0009 | grep |
| conftest autouse `_isolate_user_config` monkeypatches `code_forge.user_config.load_user_backends` to `lambda: {}` for ALL tests | tests/conftest.py:19-30 |

### Test infrastructure

| Fact | Evidence |
|------|----------|
| pytest; config pyproject.toml [tool.pytest.ini_options] (:65); pythonpath=["src"]; markers: real_api ("opt-in tests that call real backends"), integration | pyproject.toml:65-72 |
| real-api house pattern: `@pytest.mark.real_api` + `@pytest.mark.skipif(not os.environ.get("X_API_KEY"), ...)` | test_cli_integration.py:803-808, :850, :881 |
| graph.db-dependent tests use `pytest.importorskip("code_review_graph")` / skipif / `pytest.skip("no suitable CALLS target in graph.db")` -- confirms the fresh-worktree trap: new tests must not depend on graph.db | test_dead_code.py:24, :603, :628, :654 |
| conftest autouse fixtures: `_skip_worktree_check` (FORGE_SKIP_WORKTREE_CHECK=1), `_isolate_user_config`, `_git_isolation` (GIT_CEILING_DIRECTORIES) | tests/conftest.py:12-40 |
| doctor tests patch at module attribute level: `patch("code_forge.backend.probe_backend", ...)`, `patch("code_forge.user_config.load_user_backends", ...)` | test_doctor.py:129-135 etc. |
| ruff line-length = 105 | pyproject.toml:63 |
| Full suite baseline: 3483 passed / 8 skipped at 4087b05 (STATE.md) | STATE.md frontmatter |
| Relevant existing test files: test_doctor.py (32 tests), test_backend.py (153), test_cli_trust.py (15), test_user_config.py, test_contract_wiring.py (_run_trust direct-call tests) | grep counts |

---

## PART 2 -- Recommended implementation approach

### F2 (ROUTER-02) -- schema text only

Edit `src/code_forge/gate.schema.json:269-272` only. Replace the base_url
description string with a longer single-line JSON string following the file's
own convention (one line, qualifiers + default inline, no embedded newlines).
Content per D-10: forge concatenates `base_url + "/chat/completions"` verbatim
(cite llm_invoke.py:1561 behavior in prose, NOT as a code comment); whether the
path needs `/v1` is the operator's responsibility; example both shapes
(`https://host/v1` vs router root). Docs-marker commit, no forge review.

### F3 (ROUTER-03) -- trust path + walk-up

1. In `_run_trust` (cli.py:1297), replace :1315's direct join with
   `workspace = resolve_workspace(cwd, os.environ)` then
   `gate_yaml_path = workspace / ".code-forge" / "gate.yaml"`. Reuse
   `code_forge.workspace.resolve_workspace` (workspace.py:19) -- the SAME
   function doctor and the MCP server use; no duplicate logic (house rule).
   `_run_trust` has no `env` param today; call with `os.environ` exactly as
   mcp_server.py:162 does, and keep the function-injected `cwd` for tests.
   contracts_yaml_path (:1337) derives from the same workspace.
2. Warn condition (D-08): emit the stderr warning when the resolved workspace
   differs from `cwd.resolve()` (i.e., walk-up climbed or FORGE_PROJECT_DIR
   redirected). This is the implementable form of "cwd is not a git repo
   root": resolve_workspace's domain is gate.yaml ancestry, not git roots,
   and a subprocess `git rev-parse` adds a failure mode for no gain -- D-07's
   always-printed absolute path is what disambiguates (D-09's own note says
   this). Planner may alternatively add a `.git` existence check at the
   resolved root; if so, keep it a pure `Path.is_dir()` probe, not a
   subprocess.
3. Print (D-07): print the resolved absolute gate_yaml path to stderr BEFORE
   `record_trust` (:1408) and before `revoke_trust` (:1371). `--status`
   (:1339) unchanged -- it already prints Path at :1347.
4. Behavior note: with walk-up, running `trust` in a subdirectory of a
   configured project now SUCCEEDS (previously EXIT_CLI_ERROR "not found").
   The warn line covers the surprise. Running in a directory with no
   ancestor gate.yaml keeps the existing not-found error (resolve_workspace
   falls back to cwd).

### F4 (ROUTER-04) -- doctor --live

Threading: cli.py:670 doctor parser gains `--live` (store_true) => dispatch
:1869-1871 passes `live=args.live` => `run_doctor(cwd, env, live=False)` =>
`_check_backends(workspace, gate_data, env, live=False)`.

New helper (home: backend.py beside `_probe_api`, e.g.
`probe_backend_live(cfg) -> ProbeResult` or a small result type carrying the
D-04 fields):

1. Build the probe backend: `dataclasses.replace(cfg, timeout_s=60,
   max_tokens=<small>, max_completion_tokens=0, output_ceiling=0)`. The
   timeout override is MANDATORY, not optional -- see Landmine L1. Keep
   `stream` as configured: the probe is a snapshot of what a real review
   will experience on this backend (CONTEXT specifics: F1 regression
   witness). cli-type backends: skip with an informational row (their probe
   is already a real subprocess check; D-03's "all configured backends"
   means all api backends get the live call).
2. Call: `llm_invoke(<tiny JSON-asking prompt>, backend=probe_cfg,
   max_attempts=1, expected_keys=frozenset({<key>}))`. max_attempts=1 gives
   D-05's zero retries through the unmodified retry loop. The prompt must
   ask for a small JSON object that fits the cap with finish_reason=stop --
   see Landmines L2/L3. Exact prompt/keys are Claude's discretion per
   CONTEXT.
3. Classify failures from LLMInvokeError structured fields:
   - credential-rejected: `exit_code in (401, 403)` (HTTP path) or the
     pre-network key-missing raise (message-only today -- see L4 option A)
   - timeout: `is_timeout is True`
   - connection-refused: new `kind` from the URLError/OSError raise sites
     (L4 option A), else message-prefix (option B, discouraged)
   - JSON-malformed: new `kind` at the `_parse_response_body` raise site
   - SSE-mixed: same raise site, when the body excerpt starts with `data: `
     give it a distinct kind (classification ONLY -- parsing behavior
     unchanged; the deferred F1 tolerance stays deferred)
   Suggested-action line: reuse the existing house style from
   `_format_error_message` (:691-715) / `_suggestion` (:672-688) -- short
   imperative sentences.
4. `--live` must BYPASS `probe_backend`'s 5-min success cache (Landmine L5):
   call the live helper directly from `_check_backends`, after/alongside the
   offline `probe_backend` row, not through the cached path.
5. Rows: append live rows to the diag list from `_check_backends`; failures
   flow through the existing `has_fail` accumulation at doctor.py:466-469 =>
   exit 1 at :509 (D-06, zero new exit-code plumbing). Default (no --live)
   path adds no rows and no network -- `_probe_api`'s "No subprocess, no
   network call" guarantee (backend.py:900-904) stays intact for CI.
6. Serial execution (D-03) is the natural loop; no concurrency machinery.

### F5 (ROUTER-05) -- discoverability

1. Doctor line: in `run_doctor` (or the tail of `_check_backends`), call
   `user_config_path()` (user_config.py:37) and print one informational
   line -- resolved path when present, or the would-be location
   (`user_config_dir() / "config.yaml"`) with a hint when absent. Follow the
   free-form registries-block precedent (doctor.py:500-503) rather than a
   PASS/FAIL row: inheritance is informational, never a failure. This is
   code -- rides with the F3/F4 forge review.
2. Doc pointer: 2-4 lines in README.md under "## Backend configuration"
   (:190) -- set shared backends once in ~/.config/code-forge/config.yaml,
   project wins by name, FORGE_CONFIG_DIR override. Docs marker. D-10 bans
   duplicating F2's /v1 wording here -- pointer only, no second copy of the
   base_url semantics.

---

## PART 3 -- Risks / landmines

- **L1 -- backend.timeout_s silently defeats the 60s budget.**
  `effective_invoke_timeout_s` priority 1 is `backend.timeout_s`
  (llm_invoke.py:580-581): a gate.yaml that sets timeout_s=1800 OVERRIDES the
  caller's `timeout_s=60`. The probe MUST build a `dataclasses.replace` copy
  with `timeout_s=60`; passing timeout_s=60 to llm_invoke alone is not
  sufficient. Bug-injection target: probe with a backend whose timeout_s is
  large must still bound at 60s.
- **L2 -- truncation fires continuation requests even with max_attempts=1.**
  `_TruncatedResponse` is caught at :1485 and `_continue_truncated` runs
  BEFORE the retry/attempt check, issuing up to budget-2 extra requests. A
  probe cap so small the reply truncates (finish_reason=length) breaks D-05's
  "deterministic snapshot" and triples request count. Design the prompt+cap
  so a healthy backend answers with finish_reason=stop (ask for a tiny JSON
  object; cap in the tens of tokens, not 1 -- D-01's "1-token" is about
  minimal cost, and the truncation machinery makes literal 1-token the wrong
  implementation).
- **L3 -- probe reply must parse as JSON.** `_invoke_api` json-loads the
  content and raises kind="no_json" otherwise (:1456-1474). A prose-replying
  model on a HEALTHY backend would read as probe failure. Prompt must demand
  a JSON object and the call must pass matching `expected_keys`
  (falsify_real.py:47 pattern).
- **L4 -- D-04's five classes are not all distinguishable via structured
  fields today.** connection-refused (URLError/OSError, :1606-1617) and
  JSON-malformed (:1547) both surface as (exit_code=-1, kind="",
  is_timeout=False); the key-missing pre-network raise (:1340-1345) is
  message-only. The file's own design rule says match `kind`, not message
  text (:71-77). Option A (recommended): add `kind=` at the three raise
  sites (e.g. "conn", "bad_body"/"sse_body", "credentials") -- additive and
  safe: the only kind consumer is the mcp_server.py:958 whitelist, and ""
  already falls outside it, so nothing changes disposition. Option B:
  probe-side message inspection -- violates the house convention; do not.
- **L5 -- the 5-minute probe cache would swallow the live call.**
  `probe_backend` checks the success cache first (backend.py:805). If --live
  rides through probe_backend, a cached offline PASS suppresses the network
  call and doctor lies about liveness. The live helper must be invoked
  outside the cached path.
- **L6 -- conftest's `_isolate_user_config` patches `load_user_backends`
  globally.** F5's doctor-line tests cannot observe real user-config
  behavior through that function; patch `code_forge.user_config.user_config_path`
  (or the symbol as imported into doctor.py) instead, or use
  monkeypatch.undo scoped to the test. Forgetting this yields a test that
  passes while testing nothing (house rule: prove by bug-injection).
- **L7 -- D-09's "review path's rule" is a misnomer.** The CLI review path
  anchors gate.yaml at cwd (cli.py:2779) with no walk-up; the walk-up rule
  lives in resolve_workspace (MCP server + doctor, ADR-0006/0009). Plan must
  say "reuse resolve_workspace", not "match the review path", or a reviewer
  will grep cli.py:2779 and conclude the plan is wrong.
- **L8 -- stale schema copy.** Edit only `src/code_forge/gate.schema.json`;
  `build/lib/...` is a gitignored artifact (.gitignore:8) that must not be
  touched or committed.
- **L9 -- plan-ref vocabulary ban.** No `# D-04:`/`# F3:` comments in code,
  no F-numbers/severity/wave labels in commit messages (CLAUDE.md commit
  rules; self-check `grep -rnE '#.*(F[0-9]+:|[Dd]-[0-9])' src/ tests/`).
  Error-taxonomy strings are user-facing diagnostics -- write them as such.
- **L10 -- line-number drift is real in this repo.** STATE.md cited
  `body["stream"]` at llm_invoke.py:226 (08-09); it is :310 today.
  `_parse_response_body` call sites moved :1197/:1295/:1469 =>
  :1594/:1734/:1911. The plan must re-anchor every citation at plan time.
- **L11 -- --live stays opt-in.** Default doctor must make no network calls
  (_probe_api docstring guarantee). Tests must prove the default path never
  reaches the live helper.
- **L12 -- review discipline split.** F3/F4 logic-bearing => forge review +
  3 clean cycles + per-site bug-injection. F2 schema text => docs marker.
  F5 doctor line is code => rides the code review; README pointer => docs
  marker. `.planning/` is gitignored; RESEARCH/PLAN live on disk only.

---

## PART 4 -- Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (config: pyproject.toml [tool.pytest.ini_options], line 65) |
| Quick run | `python -m pytest tests/test_doctor.py tests/test_cli_trust.py tests/test_contract_wiring.py tests/test_user_config.py -x -q` |
| Full suite | `python -m pytest` (baseline 3483 passed / 8 skipped @ 4087b05, ~780s) |

### Phase Requirements -> Test Map

| Req | Behavior | New tests (house patterns) | Injection point |
|-----|----------|----------------------------|-----------------|
| ROUTER-02 | base_url /v1 doc | Schema-text change; guard = existing schema-validation tests in test_backend.py stay green (153 tests load/validate against it). Optional: assert description mentions "/v1" (string test, docs-marker class) | Revert the description edit -> string test fails |
| ROUTER-03 | walk-up + print + warn | In test_cli_trust.py / test_contract_wiring.py style: (a) trust from SUBDIRECTORY of tmp project resolves parent gate.yaml (walk-up); (b) bare trust prints resolved abs path BEFORE record_trust (patch code_forge.trust.record_trust, assert call + capsys order); (c) resolved != cwd => stderr warn, exit still 0; (d) --status output unchanged; (e) no ancestor gate.yaml => existing not-found error preserved | Delete the resolve_workspace call (restore cwd-join) -> test (a) fails. Delete the pre-mutation print -> test (b) fails. Inject at the call site, per house rule |
| ROUTER-04 | live probe | In test_doctor.py style, patch the live helper (or code_forge.backend.llm_invoke as imported): (a) --live off => live helper NEVER called (L11); (b) --live on, mock success => PASS row; (c) mock LLMInvokeError(is_timeout=True) => FAIL row + exit 1 via has_fail; (d) each of the 5 taxonomy classes maps to its row text; (e) probe cfg has timeout_s=60 even when source cfg sets timeout_s=1800 (L1); (f) max_attempts=1 asserted on the llm_invoke call (D-05); (g) live path bypasses probe cache (L5) | Delete the dataclasses.replace timeout override -> (e) fails. Change max_attempts -> (f) fails. Delete a taxonomy branch -> its (d) case fails |
| ROUTER-05 | doctor line + doc pointer | Doctor-line test patching user_config_path (NOT load_user_backends -- L6): present => path printed; absent => hint printed; never affects exit code. README pointer: docs-marker, no test | Delete the print -> test fails |

### Sampling rate
- Per task commit: the quick-run subset above.
- Per wave merge / phase gate: full `python -m pytest` green before
  /gsd:verify-work. No graph.db-dependent tests may be added (fresh-worktree
  trap; existing pattern test_dead_code.py:24/:603).

### Real-path smoke (post-review, house Golden Rule 3)
- F4: `code-forge doctor --live` against a real configured backend on this
  host, plus one deliberately-broken base_url (wrong /v1) to watch the F2/F4
  failure class surface. Marker pattern if automated:
  `@pytest.mark.real_api` + env-key skipif (test_cli_integration.py:803).
  Known SSE-always router for the SSE-mixed class: OmniRoute at
  [IP_REDACTED]:20128 [CITED: STATE.md F1 block; availability at execution
  time UNVERIFIED -- treat as optional, not a gate].
- F3: `cd` into a subdirectory of a real configured project, run
  `code-forge trust`, confirm the printed absolute path + warn, then
  `--revoke` to restore.

### Wave 0 gaps
None -- test infrastructure (pytest config, fixtures, conftest isolation,
mocking patterns) already covers all four requirements. New tests go into
existing files per the map above.

## Sources

All findings [VERIFIED: direct file read / grep on main @ 4087b05,
2026-08-18]. No external packages, no web sources -- this phase extends
in-repo machinery only. Package Legitimacy Audit: N/A (zero new
dependencies). Environment Availability: N/A (pure code/config changes;
python3 + pytest already the project toolchain).

Key files read this session: src/code_forge/doctor.py (whole),
src/code_forge/backend.py:130-210 + 770-980, src/code_forge/cli.py
(parser/dispatch/trust/review anchors), src/code_forge/llm_invoke.py
:52-320, :541-760, :886-975, :1316-1700, src/code_forge/workspace.py
(whole), src/code_forge/user_config.py (whole),
src/code_forge/gate.schema.json:255-305, docs/adr/0009, tests/conftest.py,
tests/test_doctor.py (head), tests/test_cli_trust.py, tests/test_contract_wiring.py:244-290,
tests/test_cli_integration.py:795-815, pyproject.toml, README.md (headings).

## RESEARCH COMPLETE
