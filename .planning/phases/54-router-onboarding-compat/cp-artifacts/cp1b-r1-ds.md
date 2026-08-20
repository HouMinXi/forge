All verifications against the real sources are complete. Here is my review.

---

# CP1b R1 review — Phase 54 plan (DeepSeek, implementer-readiness + acceptance-checkability + coverage)

**Verdict: no BLOCKERs, no HIGHs. The plan is implementable verbatim; four verifiable defects remain, one of them a real verification hole.**

## Adjudication of declared positions A–E

- **A (warn = `resolved_workspace != cwd.resolve()`): ACCEPT.** Verified `workspace.py:46-52`: `resolve_workspace` returns `start = cwd.resolve()` when no ancestor holds `.code-forge/gate.yaml`, and skips `$HOME` (`workspace.py:40-41`). The warn fires exactly on walk-up/env-override; the truly projectless case keeps the stronger existing `EXIT_CLI_ERROR` (cli.py:1319-1324). ROUTER-03's observable behavior is delivered — the recoverable wrong-dir case warns, the no-project case errors.
- **B (32-token cap, not literal 1): ACCEPT.** Verified the mechanism at `llm_invoke.py:1484-1497`: the `_TruncatedResponse` catch runs `_continue_truncated` (1485-1496) **before** the attempt check (`if not exc.retryable or attempt == max_attempts - 1: raise`, :1497). A literal 1-token cap re-arms the continuation and issues a second request even at `max_attempts=1`, breaking D-05's zero-retry snapshot. All zeroed fields exist on `BackendConfig` (`backend.py:167-172`), so the `replace()` call is legal; cap resolution `cap = backend.max_completion_tokens or backend.max_tokens` (`llm_invoke.py:269`) confirms 0/32 resolves to 32.
- **C (api-only live probe, cli skip): ACCEPT, with one wording correction** — see finding L-2. Verified `backend.py:792-793, 812-813`: explicitly configured cli backends bypass the probe and return `ok=True` **without executing anything**, so the plan's suggested skip-row text asserts a falsehood.
- **D (six tasks, one plan): ACCEPT** — locked by D-12.
- **E (no .git probe; printed path disambiguates): ACCEPT** — verified `resolve_workspace`'s domain is gate.yaml ancestry; the always-printed absolute path plus warn line suffices.

## Coverage walk (ROUTER-02..05, D-01..D-12)

Every requirement maps to a task with real action text, and every source anchor the plan cites checked out (with the drift the plan itself warns about): credential block 4 raises `llm_invoke.py:1332/1337/1343/1347`; six URLError/OSError sites `:1606/1613, :1746/1753, :1918/1925` (none currently set `kind=`); `_parse_response_body` raise embeds `body_text[:200]` `:1547-1551`; whitelist literal `mcp_server.py:958-960`; negative dispatch test at exactly `tests/test_mcp_server.py:919`, positive harness at `:883`; `_sampling_dispatch_patches` helper `:855`; conftest trap patches only `load_user_backends` (`tests/conftest.py:29-31`); doctor parser `cli.py:670` discards its `add_parser` result; dispatch `cli.py:1871`; registries block in the always-run tail `doctor.py:500-503`; `has_fail` pipeline `:466-469`/`:509`; `_build_parser` exists at `cli.py:304` (behavior (g)'s parser-only fallback is implementable); `resolve_contract_specs(config_path, cwd)` second arg is the `_resolve_repo_path` base (`contract_loader.py:294-304`) — Task 2's workspace-passing is correct; baseline `grep -c 'kind="'` = **16 confirmed today**, 16+12=28 arithmetic holds with the two-arm shape clause; full-suite baseline 3483+8=3491 matches today's `--collect-only` count of 3491.

## Findings

**M-1 — Task 5: the dispatch wiring `live=args.live` (cli.py:1871) has no executing test; the phase's own injection-at-call-site rule is not applied to it.**
- Plan text: `54-01-PLAN.md` behavior (g) ("dispatch-level test if the file has that pattern; otherwise assert parser accepts the flag") + Task 5 action ("Dispatch (cli.py:1870-1871): pass live=args.live into run_doctor").
- Verified: `tests/test_doctor.py` contains **no** dispatch/main/argv pattern (grep for `main(`, `argv`, `parse_args` returns nothing), so per the plan's own conditional the implementer stops at a parser-accepts-flag assertion. But a main()-driven pattern **does exist in the repo** at `tests/test_cli_integration.py:181` (`test_main_returns_int`), so a dispatch-level test was feasible without new machinery. Deleting or typo'ing the `live=args.live` line keeps **every mandated test green** — including a direct `run_doctor(live=True)` unit test — which is precisely the failure class the plan itself guards against at every other call site (Task 2 injections 1–3, Task 4 injections 1–4, Task 5 injections 1–3 all target call sites; none covers this one). Consequence if it slipped: `code-forge doctor --live` silently runs the offline doctor — the exact user experience this phase exists to fix. Suggested fix: mandate one main()-level test (pattern: `tests/test_cli_integration.py:181`) asserting `--live` reaches `run_doctor` with `live=True` (patch `code_forge.doctor.run_doctor` at the dispatch import site), and add injection (4) to Task 5: delete `live=args.live` → that test FAILS.

**L-1 — Task 1 acceptance: "build/lib untouched, verified by git status --short" is a vacuous check.**
- Plan text: Task 1 acceptance criterion ("build/lib untouched, verified by git status --short").
- Verified: `build/` is gitignored (`.gitignore:8`), so `git status --short` can never report a modification there regardless of what the implementer did. The guard cannot detect the violation it names. Suggested fix: reword to the actionable part ("do not touch build/lib — stale gitignored artifact") or verify with a non-git check (e.g. mtime comparison).

**L-2 — Task 5: the cli-skip row message asserts a falsehood.**
- Plan text: Task 5 action, `"... live: skipped (cli backend already executed by the offline probe)"`.
- Verified: `backend.py:792-793` docstring + `:812-813` — explicitly configured cli backends "bypass the probe entirely and return ProbeResult(ok=True) immediately". Nothing is executed for them. A user reading the row would be told a validation that never happened. Suggested fix: "skipped (cli backends are trusted as configured; no live probe applies)".

**L-3 — Task 1 acceptance: "test_backend.py's 153 tests" is a stale count.**
- Plan text: Task 1 acceptance ("test_backend.py's 153 tests load/validate against this schema").
- Verified by running the suite today at HEAD 4087b05 (clean tree): `162 passed in 12.13s`, zero skipped. The operative criterion ("suite stays green") is fine, but a verify-report writer copying the number would propagate a wrong fact. Suggested fix: correct to 162 or drop the count.

## Implementer-readiness summary (lens 1)

Every task's action text is executable without questions: all signatures verified (`_run_trust(args, cwd) -> int`; `_check_backends(workspace, gate_data, env)` with `live=False` default backward-compatible against all 6 existing `run_doctor(cwd=, env=)` call sites; `llm_invoke(prompt, backend, timeout_s=None, expected_keys: frozenset|None, max_attempts=5)`; `replace()` field set complete). The only free-choice points (pre-mutation print wording, probe prompt, fallback class name) are either constrained by test assertions or explicitly delegated in CONTEXT.md's "Claude's Discretion". The grep-count criterion is self-enforcing: any deviation in docstring spelling or ternary shape flips the count away from 28 and fails loudly.

SCORECARD: B=0 H=0 M=1 L=3
