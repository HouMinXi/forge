# CP1b R1 review — Kimi angle: cross-boundary data flow + requirements compliance

All file:line evidence below was re-verified against the working tree at main @ 4087b05, not the plan's citations.

## FINDINGS

### F-1 (HIGH) — Plan miscites the HTTP error formatter: the ~200-byte body excerpt is DISCARDED on the openai/anthropic HTTP-error path, so D-04's excerpt component silently goes missing for the phase's headline failure class

Plan Task 4 (`54-01-PLAN.md` action, Step 2) asserts: *"detail = str(exc) (it already embeds the ~200-byte body excerpt for body-level failures via :1549 and the HTTP error formatter :691-715)"*. The second half is false. Verified in `src/code_forge/llm_invoke.py`:

- `_format_error_message` (:691-715) takes `body_excerpt` as a parameter and **never uses it** — the returned string is built only from `provider_name / problem / http_code / tip`.
- The openai HTTPError handler (:1596-1606) does `body_bytes = exc.read()`, computes `body_excerpt` (:1597-1598), passes it into the formatter (:1601), and it is dropped. The anthropic handler (:1736-1746, formatter call :1741) is identical.
- Only the vertex path embeds the excerpt, inline: `"HTTP %d from vertex backend: %s" % (exc.code, body_excerpt)` (:1913-1917).

Why this matters beyond a stale citation:

1. The wrong-/v1 router misconfiguration — the exact F2/ROUTER-02 pain this phase exists to debug — surfaces as an HTTP error (404/400/405) whose **body** is what names the problem. Under the plan as written, `doctor --live` renders that failure as `... HTTP error (404). Check provider documentation.` with no excerpt, classified into the plan's own fallback class.
2. D-04 (54-CONTEXT.md:34) makes "first ~200 bytes of the response body" a component of the failure diagnostics. The must_have truth hedges with "where one exists" — but here the body **does** exist and is thrown away at the raise site, so the probe cannot recover it downstream.
3. None of the planned tests close this: the Task 4 taxonomy tests assert class labels and non-empty suggestions, and no test asserts excerpt presence on the HTTP branch. The gap ships green.

Fix direction (planner's choice): extend `_format_error_message` to append the excerpt, or mirror the vertex inline pattern at :1601/:1741 — either is a review-path-visible message change the plan should explicitly own, and the interface claim in Task 4 must be corrected regardless.

### F-2 (LOW) — `detail = str(exc)` can carry embedded newlines into a one-line doctor row

Task 5's row format concatenates `error_class + " -- " + detail + "; " + suggestion` into a single `_line` row. Two detail sources are multi-line: `_parse_response_body` embeds raw `body_text[:200]` (llm_invoke.py:1547-1550 — an HTML proxy error page contains newlines), and the `no_json` raise embeds `"JSONDecodeError: %s\ncontent[:500]: %r"` (:1466-1473). Exit-code plumbing is unaffected (has_fail is tuple-driven, doctor.py:466-469), but the rendered row wraps and the fixed-width format (:513-520) breaks. A one-line normalization at the boundary (the retry-path precedent already does `" ".join(str(exc).split())` at llm_invoke.py:1518) closes it.

### F-3 (LOW) — Vertex credential failures classify to the fallback class, not `credential-rejected`

`kind="credentials"` is added only at the non-vertex credential block (llm_invoke.py:1327-1351, four raise sites verified). Vertex credential acquisition raises LLMInvokeError without a kind (:1837-1852 area — `Failed to load GCP credentials`, `No GCP credentials found`), so a vertex backend with bad credentials lands in the plan's unknown-kind fallback class with a generic suggestion instead of `credential-rejected`. Imprecision only; the failure still surfaces, exits 1, and carries a suggestion.

## Declared positions — adjudication

- **A (D-08 warn condition `workspace != cwd.resolve()`) — ACCEPT.** Verified `resolve_workspace` (workspace.py:19-51): walk-up returns a resolved ancestor (:45-50), env override returns `Path(explicit).expanduser().resolve()` (:39-41), fallback returns `start = cwd.resolve()` (:45, :51). So `workspace != cwd.resolve()` is true exactly when walk-up climbed or FORGE_PROJECT_DIR redirected — a strict superset of D-08's literal "not a git repo root" surface, never a subset; the over-warn case (nested git root without its own gate.yaml) still prints the resolved path as disambiguator. The "cwd not in any project" case cannot proceed (no gate.yaml exists to trust), so the existing not-found error (cli.py:1319-1324) is the only viable behavior, not a D-08 violation. ROUTER-03's "$HOME policy" half is satisfied by construction: resolve_workspace skips $HOME (:47-48).
- **B (cap=32, truncation continuation before attempt check) — ACCEPT.** Verified `_TruncatedResponse` is caught and `_continue_truncated` runs at llm_invoke.py:1485-1496, *before* `if not exc.retryable or attempt == max_attempts - 1: raise` (:1497-1499) — a truncating cap does multiply requests at max_attempts=1, so the literal-1-token reading of D-01 was correctly rejected. The zeroed fields are true omit-sentinels: thinking block guarded by `if ... backend.thinking_type` (:283-287), effort by `if ... backend.reasoning_effort` (:290-296), and cap resolution `cap = backend.max_completion_tokens or backend.max_tokens` with `output_ceiling > 0` override (:269-271) — so zeroing both lands the cap on max_tokens=32 exactly as the plan claims.
- **C (api-only probe; cli informational skip) — ACCEPT.** `_probe_api` docstring "No subprocess, no network call" (:898-902) and the cli bypass (:811-813) verified; a live row for cli would duplicate what the offline path already executes.
- **D (six tasks) — ACCEPT** per D-12; packaging decision, no technical exposure found.
- **E (no .git probe) — ACCEPT.** The always-printed resolved path (D-07) is the disambiguator; a git subprocess inside trust would be a new dependency for marginal gain.

## Cross-boundary traces verified clean (no finding)

- **Trust-store byte-identity, subdir vs root (Question B):** `resolve_workspace` always returns a resolved path, so `gate_yaml_path` is identical from root or any subdirectory; `record_trust`/`revoke_trust` key on `str(gate_yaml_path.resolve())` (trust.py:168, :183). Contracts: passing the resolved workspace as the base to both `resolve_contract_specs` call sites makes relative repo paths resolve identically (`_resolve_repo_path`, contract_loader.py:160-179; resolution at :308-315), and the contracts key is likewise `.resolve()`d (trust.py:302, :311). Byte-identical entries confirmed by construction.
- **FORGE_PROJECT_DIR (Question C):** priority 1 in resolve_workspace (workspace.py:39-41) is the intended runtime override, and the warn condition catches the redirect case; the plan's autouse `delenv` fixtures in both trust test files are justified — existing `_run_trust(args, tmp_path)` tests (test_contract_wiring.py:244-290 pattern verified) become host-env-sensitive the moment walk-up reads os.environ.
- **replace() copy survivors (Question D):** `params` cannot re-arm the cap, thinking, effort, or stream — `check_params` rejects all of them via PROTECTED_PARAM_KEYS (backend.py:45-56, :337-345), and the passthrough assignment (llm_invoke.py:339-346) runs only after that check. `headers` survive but are operator-configured gateway auth — intended. `outcap_key` renames the key but the value stays 32. The 60s bound is real: backend.timeout_s wins the priority chain (:578-581), the read deadline is anchored *before* urlopen (:1908-1910), and `_read_with_deadline` clamps the 900s idle constant to the remaining deadline (:394).
- **kind consumers (Question A):** the sole consumer is the mcp_server whitelist (:958-960, verified); `kind=""` already falls outside it, so the four new kinds are additive-safe there. In `_classify_live_failure` every planned kind plus `""`/unknown has a defined fate (explicit fallback class with non-empty suggestion). The grep-count baseline of 16 `kind="` lines in llm_invoke.py reproduced exactly.

## Requirements compliance (ROUTER-02..05 vs plan)

- **ROUTER-02** — compliant. Task 1 edits only the base_url description (:269-272, single-line convention confirmed), guard test reuses the corpus loader (test_schema_corpus.py:39), build/lib staleness warning verified real (`.gitignore:8` = `build/`; the stale copy exists on disk).
- **ROUTER-03** — compliant under position A (accepted above). Print-before-mutate sites exist (cli.py:1371, :1408); --status already prints the path (:1347); no-ancestor regression preserved via the :1319 error.
- **ROUTER-04** — compliant except **F-1**. D-02/D-03/D-05/D-06 mechanics all verified against real code (parser :670-674 arg-less today, dispatch :1870-1871, has_fail :466-469, exit :509).
- **ROUTER-05** — compliant; the plan's doctor line exceeds the docs-only requirement text but is locked by D-11 (CONTEXT :64-66), and the README target section exists (:190).

SCORECARD: B=0 H=1 M=0 L=2
