# Phase 36 Input: MCP Backend Onboarding Usability Findings

Source: live debugging session 2026-06-30 while trying to run `forge_review`
via MCP on the user's OWN configured deepseek backend (the happy path -- not an
edge case). The user's verdict: "even running my own model backend through MCP,
I find it hard to use." These findings are the evidence behind that verdict.
All reproduced firsthand this session, not theorized.

Context: this happened while trying to dogfood `forge_review` on the Phase 35
diff. Every finding below blocked that happy path.

## Findings (ordered by severity)

### MCP-01 (BLOCKER) -- MCP server resolves workspace from `~`, never reaches the repo
The forge MCP server processes all run with `cwd=/home/houminxi` (verified via
`/proc/<pid>/cwd` on 7 live processes). Workspace resolution (changed today by
commits 8c34897 "resolve workspace from env var or cwd walkup" and 785ef4b)
does a cwd-walkup. A walkup from `~` goes UP toward `/`, so it can never reach
`~/code/forge` (which is BELOW `~`). Result: the server resolves
`~/.code-forge/gate.yaml` (which exists, 8234 bytes) instead of the repo's
`~/code/forge/.code-forge/gate.yaml`. Trust granted on the repo gate.yaml is
invisible to the server. No amount of restarting fixes this while cwd=`~`.
Impact: MCP forge_review is unusable for any repo nested under the launch cwd.
Repro: `forge_review` -> "No trusted review backend configured" even though
`code-forge trust --status` in the repo shows Trusted: True.

### MCP-02 (HIGH) -- `forge_resolve_outlet` and `forge_review` disagree
In the same MCP server, `forge_resolve_outlet` returns `subprocess` (looks
healthy) while `forge_review` raises "No trusted review backend configured".
The two code paths resolve workspace/trust differently, so the diagnostic tool
reports a state the action tool does not honor. A diagnostic that lies is worse
than no diagnostic -- it sent debugging down the wrong path first.

### MCP-03 (HIGH) -- trust is cached at server launch; every trust change needs a restart
`code-forge trust` updates the on-disk trust store, but a running MCP server
keeps its launch-time trust state. CLI re-reads trust each invocation and has
no such problem. So MCP uniquely couples "any trust change" to "restart the
server". Combined with MCP-04, a routine gate.yaml edit becomes: re-trust +
restart + hope the restart actually re-spawns.

### MCP-04 (HIGH) -- `/mcp` reconnect does not re-spawn; zombie processes accumulate
After the user ran `/mcp` reconnect, `forge_review` still failed with the old
trust error. Process listing showed 7+ live `code-forge-mcp` processes, oldest
15836s old (since the dev session start), newest 162s. Reconnect attaches to or
spawns processes without reaping old ones, and the routed process did not pick
up the re-granted trust. The user cannot tell from the UI whether a restart
"took". This is the single most confusing behavior of the session.

### MCP-05 (MEDIUM) -- editing gate.yaml silently invalidates trust
Bumping the model (deepseek-chat -> deepseek-v4-flash) changed the gate.yaml
hash, which auto-revoked trust (stored hash a42edd... != current 395613...).
Security-sound, but with zero proactive signal: the next review just fails with
"no trusted backend". A version bump should not feel like re-clearing security.
Consider: warn at edit time, or a lighter re-confirm for benign field changes.

### MCP-06 (MEDIUM) -- key provenance is invisible and multi-hop
MCP config shows `env_keys=[]`; the key is injected by the `code-forge-mcp-pass`
wrapper via `pass show api/deepseek`, requiring gpg-agent unlocked. When it
fails, "no backend" gives no hint that the cause is a locked gpg-agent or a
wrong pass path. (This session: the key path is `api/deepseek`, not the
`deepseek`/`deepseek-api-key` names one would guess.) Onboarding has no single
"why is my backend not reachable" answer -- it took 5+ read-only probes.

## Cross-cutting observation

Phase 35 (MCP sampling, "requires no API key", objective: "Remove onboarding
friction for users who already have a model subscription") is the RIGHT
direction -- it deletes the gate.yaml + key + trust ceremony entirely by
borrowing the client's model. But sampling cannot self-host this review yet
(needs the new code live + a sampling-capable client), so the api-backend path
above is still the only one available today, and it is exactly the friction
Phase 35 set out to remove. Phase 36 ("Two-Step API Backend Onboarding") should
treat MCP-01 as a correctness blocker (workspace resolution is just wrong for
nested repos), and MCP-02/03/04 as the usability core: one obvious diagnostic,
trust that does not require a restart dance, and a reconnect that actually
re-spawns + reaps.

### MCP-07 (HIGH) -- worktree guard is an unconditional hard block, no override
`code-forge review` refuses to run in the main worktree: cli.py:1423-1432
detects `git-dir == git-common-dir` and raises exit 2 ("must run inside a
linked git worktree, not the main tree"). An escape hatch EXISTS:
FORGE_SKIP_WORKTREE_CHECK=1 (cli.py:1403), but the original finding grepped for
the wrong names (FORGE_ALLOW*/--allow-main) and missed it. The real gap is that
the escape hatch is undocumented and the guard error message does not mention it.
This blocked the Phase 35 forge-on-forge review outright: the Phase 35 changes
live in the main tree (a deliberate, user-approved placement for this task), and
forge flatly refused to review them. The worktree-isolation policy is sound as a
DEFAULT, but a hard block with no opt-out is wrong: legitimately reviewing
uncommitted main-tree changes is a real workflow (exactly what was happening
here). It should warn / require an explicit `--allow-main` (or env), not exit 2.
User verdict: "needs to change, cannot be forced to not-in-main-tree."

### MCP-08 (HIGH) -- trust mechanism is too heavy; should degrade to something lighter
trust.py (273 lines) implements a direnv-style all-or-nothing model: a backends-
block SHA-256 that is either trusted or not, with only trust / revoke / status
and NO lighter tier and NO env escape hatch (no `FORGE_TRUST=...`). Combined with
MCP-03 (cached at launch) and MCP-05 (any gate.yaml edit silently invalidates),
the lived experience is: edit a model name -> silently untrusted -> review fails
-> re-run `trust` (which re-prints the scary "Dangerous fields found" list every
time) -> restart the MCP server. For a single-user tool reviewing the user's OWN
gate.yaml that they wrote, this is ceremony with little payoff. User verdict:
"trust should degrade -- it's hard to use." Phase 36 should consider: a trust-
on-first-use or env-opt-in lightweight path, re-confirm only when a DANGEROUS
field (credential target: base_url / api_key_env / credentials_path) actually
changes rather than on ANY backends-block byte, and/or a global "I trust my own
config" switch -- so a benign model bump does not feel like clearing security.

### MCP-09 (HIGH) -- CLI errors state the problem but not the fix, and assume insider vocabulary
Ground truth: cli.py has 37 `raise CliError(...)` sites; errors.py:26
`class CliError(Exception)` carries NO remediation/hint field, so any "how to
fix" must be hand-written into each message string. A scan for remediation
language (run / create / try / use / set / add) across the 37 sites surfaced
exactly ONE actionable fix -- the worktree guard (cli.py:1426 "Create one: git
worktree add .worktrees/work <branch>"). The other 36 are bare problem
statements: "registry load failed: %s", "baseline resolution failed: %s",
"contract path is empty", "malformed gate.yaml at %s". Two compounding issues:
(1) no remediation -- a user who hits "baseline resolution failed" has no next
step; (2) insider vocabulary -- "contract", "registry", "baseline", "outlet"
are forge-internal terms with no inline gloss, so the error is opaque to exactly
the new user that onboarding targets. The argument-validation errors are the
counter-example done right ("--whole-file cannot be combined with --committed"
-- precise and self-evidently actionable). Fix direction: give CliError an
optional remediation field rendered on a second line ("Hint: run `forge init`
to regenerate gate.yaml"), and backfill the operational errors (gate.yaml /
contract / registry / baseline / trust / outlet) with one-line fixes. Forge
already proves it can (the worktree guard); it is just inconsistent.

### MCP-10 (HIGH) -- MCP server passes --no-color to CLI but CLI has no such flag
mcp_server.py:438 and :516 build CLI args with '--no-color'. cli.py review
parser (lines 213-337) and gate-check parser (lines 340-348) define no
--no-color argument. argparse uses parse_args() (line 1065), not
parse_known_args(), so every MCP forge_review and forge_gate_check call that
routes through CLI subprocess silently fails with exit 2 (CLI_ERROR). Tests
mock create_subprocess_exec so the mismatch is invisible. Fix: add --no-color
to review and gate-check parsers, or remove it from mcp_server.py cli_args.

### MCP-11 (HIGH) -- forge_gate_check passes --baseline/--backend to a parser that rejects them
mcp_server.py:489-530 forge_gate_check accepts baseline and backend params and
builds cli_args with --baseline and --backend (lines 517-520). cli.py
gate-check parser (lines 340-348) only defines --quiet. argparse rejects the
unknown flags with exit 2. The MCP tool's baseline/backend params are dead code
that always produce a CLI error when non-None. Fix: add --baseline and
--backend to the gate-check parser, or remove these params from forge_gate_check.

### MCP-12 (MEDIUM) -- docs claim --auth-timeout CLI flag exists but it does not
docs/configuration.md:95 documents '--auth-timeout CLI flag' in the precedence
chain, and line 453 shows 'code-forge review --auth-timeout 60'. backend.py:378
resolve_auth_timeout() accepts a cli_value param (line 387 validates it as
--auth-timeout). But cli.py review parser has zero --auth-timeout add_argument
calls. Furthermore resolve_auth_timeout is never called anywhere in the codebase
(grep returns only its definition) -- dead code. Fix: either add --auth-timeout
to the review parser and wire it, or remove from docs/configuration.md.

### MCP-13 (MEDIUM) -- CI docs show --output flag on review but it only exists on eval
docs/setup-ci.md:79 shows 'code-forge review --mode ci --committed --output
review.sarif'. --output is only defined on the eval subparser (cli.py:565), not
on review. Running 'code-forge review --output X' fails with 'unrecognized
arguments'. Fix: add --output to review parser, or change docs to redirect
stdout ('code-forge review --mode ci --committed > review.sarif').

### MCP-14 (LOW) -- docs say 'code-forge review --version' but --version only on root parser
docs/setup-cursor.md:48 and docs/setup-pycharm.md:145 suggest 'code-forge
review --version' as a verify command. --version is only on the root parser
(cli.py:200-203). The backward-compat logic (cli.py:1058) passes 'review' as
subcommand and --version reaches the review parser which rejects it. Fix:
change to 'code-forge --version' in both docs.

### MCP-15 (LOW) -- setup-claude-code.md omits commit-msg hook from install-hooks
docs/setup-claude-code.md:145-146 says install-hooks 'writes
.git/hooks/pre-commit: verify + gate-check'. But install_hooks.py (lines 615,
759-790) installs BOTH pre-commit AND commit-msg hooks. setup-vscode.md:166-167
correctly documents both. Fix: update setup-claude-code.md:145 to mention both.

### MCP-16 (MEDIUM) -- forge_init/forge_trust output to stderr but MCP captures only stdout
CLI init writes user-facing messages to stderr (cli.py:1168 'gate.yaml already
exists', 1173 'Created %s'). CLI trust also writes to stderr (cli.py:938-964).
MCP _run_cli_simple (mcp_server.py:127-141) captures both but
_make_simple_result (line 543-544, 554-555) only passes stdout to
CallToolResult. The MCP client sees an empty string for successful
init/trust operations. Fix: combine stdout+stderr in _make_simple_result, or
switch CLI init/trust success messages to stdout.

### MCP-17 (MEDIUM) -- cross-repo error gives no fix hint
cli.py:1244 raises CliError("cross-repo review requires committed refs, not
%s" % head_spec.ref). User sees 'WORKING' or 'INDEX' but has no next step.
Fix: append "Commit your changes first, or use --committed to review the last
commit."

### MCP-18 (MEDIUM) -- registry-not-found error gives no auto-detect hint
cli.py:1680 raises CliError("registry load failed: %s not found" %
args.registry). Fires for non-default --registry path without suggesting
alternatives. Fix: append "Verify the path exists. Omit --registry to use the
default (.code-forge/tools.yaml, auto-generated if absent)."

### MCP-19 (BLOCKER) -- CliError used but never imported in llm_invoke.py
llm_invoke.py:889 and :978 both raise CliError(...) but CliError (errors.py:26)
is never imported. Any Anthropic or Vertex backend with stream: true crashes
with NameError instead of the intended error message. Fix: add
`from .errors import CliError` or replace with LLMInvokeError (already imported,
fits the module's error contract better).

### MCP-20 (HIGH) -- _run_ci returns None instead of Verdict when mutation PID alive
machine.py:304 has a bare `return` (returns None) inside _run_ci which declares
-> Verdict. When mutation-result.json has status='running' and PID is alive, the
method returns None. CLI path degrades (broad except -> exit 1); MCP path
crashes (verdict.value on None -> AttributeError). Fix: return Verdict.PENDING.

### MCP-21 (MEDIUM) -- runner.py tool timeout returns None with no user signal
runner.py:142-155 returns None on TimeoutExpired/OSError with only
logger.warning (invisible -- forge sets no logging handler). run_tools lumps
this with "no matching files" in tools_skipped. machine.py:107 discards
_skipped entirely. A timed-out shellcheck silently produces zero findings.
Fix: append to infra_errors instead of returning None (~3 lines).

### MCP-22 (LOW) -- runtime.py read_smoke_receipts silently skips malformed receipts
runtime.py:157-159 catches JSONDecodeError and OSError with bare pass. Corrupt
receipt -> silently fewer receipts -> unnecessary smoke reruns. Fix: log a
warning with the receipt path.

### MCP-23 (MEDIUM) -- verify subcommand crashes with traceback when git absent
cli.py:1127 subprocess.run(["git", ...]) has no try/except FileNotFoundError.
mutation-check (L2166) and e2e-check (L2270) both catch it properly. Fix: wrap
in try/except FileNotFoundError, print clear message, return EXIT_CLI_ERROR.

### MCP-24 (MEDIUM) -- smoke-run has no timeout, hangs forever on stuck processes
cli.py:792 subprocess.run(cmd_args, ...) has no timeout parameter. If the user
command hangs, forge hangs with no recovery. mutation-check uses timeout=120.
Fix: add --timeout flag (default 300s), catch TimeoutExpired.

### MCP-25 (LOW) -- mutation-check hardcodes 'pytest' as baseline command
No override for projects using other test runners (unittest, nose, cargo test).
Fix: add --test-command flag or read from gate.yaml test section.

### MCP-26 (LOW) -- install-hooks backup collision gives no recovery guidance
When .git/hooks/pre-commit.bak already exists, install aborts with 'remove one
manually'. Fix: suggest the exact rm command, or use timestamped backup names.

### MCP-27 (LOW) -- install-skill --skill nonexistent gives unhelpful error
No list of available skills shown. Fix: on not-found, print available skills
from the registry (same as --list output).

### MCP-28 (MEDIUM) -- setup-ci.md claims gate-check 'fails open' but it blocks on missing test section
Docs say gate-check is safe to add before configuring tests. Reality: missing
test section in gate.yaml -> CliError -> exit 2 -> CI blocks. Fix: update docs
to document the prerequisite, or make gate-check truly fail-open on missing
test section.

### MCP-29 (MEDIUM) -- setup-ci.md exit code table omits codes 4, 5, and 7
Table only shows 0/1/2/3/6. Missing: 4 (ESCALATED), 5 (DELEGATED),
7 (UNRELIABLE). Fix: add the missing codes.

### MCP-30 (LOW, re-rated from HIGH) -- outlet-alignment.md Wave 0 note is stale
Actual text: "Outlet C still runs with stub legs (registry={}, no advisory runners,
falsifier without backend)". Since Phase 24.1, Outlet C has real legs. Stale doc
note, not a code defect. Fix: update the Wave 0 note to reflect current state.

### MCP-31 (LOW) -- outlet-alignment.md exit code table omits codes 6 and 7
Same gap as MCP-29 but different doc. Fix: add TIMEOUT (6) and UNRELIABLE (7).

### MCP-32 (LOW) -- setup-cursor.md FORGE_LLM_MODEL example shows wrong scope
Example shows project-level env var but the variable is resolved at
backend/provider level. Fix: correct the scope description.

### MCP-33 (LOW) -- check_git_push_review.sh cites wrong/outdated pass sequence
Review reminder message references an old pass naming convention. Fix: update
to match current pass names (qodo/expert/adversarial).

### MCP-34 (LOW) -- check_non_ascii.sh jq-missing message not actionable
When jq is absent, message says 'jq required' without install hint. Fix: add
'Install: apt install jq / brew install jq'.

### MCP-35 (LOW) -- All parsers: ToolError.stderr is always empty string
Parser ToolError objects carry stderr='' even when the underlying tool wrote to
stderr. Diagnostic context is lost. Fix: pass the captured stderr through.

### MCP-36 (MEDIUM) -- _sarif.py per-item exception discards all prior valid findings
When one SARIF result fails to parse, the exception handler discards results
already parsed in the same batch. Fix: catch per-item and continue, collecting
partial results.

### MCP-37 (MEDIUM) -- Corpus loader accepts invalid expected_verdict without validation
eval/corpus.py:74 stores expected_verdict as-is from YAML. scorer.py:161-168
only checks 'HOLD' and 'PASS'; any other value (e.g. 'BANANA') falls through
all branches silently -- counted in total but contributes to no quadrant, so
total != sum(quadrants). Fix: validate expected_verdict in load_corpus, raise
ValueError on anything other than 'HOLD' or 'PASS'.

### MCP-38 (LOW) -- init crashes with traceback when .code-forge is a regular file
cli.py:1163 gate_dir.mkdir(parents=True, exist_ok=True) raises FileExistsError
when .code-forge exists as a regular file. No try/except around the call. Fix:
catch FileExistsError and print '.code-forge exists but is not a directory'.

### MCP-39 (MEDIUM) -- invalid outlet string crashes review with traceback instead of clean error
outlet_resolver.py:80 _parse_outlet_string raises ValueError for invalid
outlet (e.g. FORGE_OUTLET=bogus). The diagnostic path (cli.py:2604) catches
ValueError, but the main review path (cli.py:1494) does not -- ValueError falls
through to generic except Exception (L1086), producing a full traceback. Fix:
catch ValueError alongside CliError in cli.py:1077, or change _parse_outlet_string
to raise CliError.

### MCP-40 (HIGH) -- fixval tells user to git apply a file that is immediately deleted
fixval.py:408-412 logs 'Run git apply <patch_path> to restore manually' on
restore failure. But the outer finally (lines 415-420) unconditionally
os.unlink(patch_path). The remediation command references a file that no longer
exists. Fix: skip os.unlink when restore fails (set a _keep_patch flag).

### MCP-41 (HIGH) -- three-way version desync: pyproject.toml / __init__.py / actual
pyproject.toml:9 says 2.4.0. src/code_forge/__init__.py:5 says 2.0.0a1.
code-forge --version reports 2.0.0a1. importlib.metadata reports 2.4.0.
CLAUDE.md says v2.7. Three different answers. Fix: set both to 2.7.0 (or
current), consider setuptools-scm for single source of truth.

### MCP-42 (LOW) -- install.sh exits 0 after skipping all skills
install.sh:20-22 prints SKIP per missing skill but exits 0 at line 48 having
installed nothing. Fix: count installs and exit nonzero if zero skills linked.

### MCP-43 (LOW) -- gate_check: negative exit codes become BLOCK with no diagnostic
gate_check.py:834-860 translate_exit_code() documents codes 0-5 and '>5' but
says nothing about negative values (signal kills: -9, -11, -15). A segfaulting
test runner returns -11; this falls through to return 1 (BLOCK) with no hint
the runner crashed vs tests failed. Fix: check for negative returncode, print
'test runner killed by signal %d' % abs(returncode).

### MCP-44 (MEDIUM) -- gate_check silently swallows test stderr on failure
gate_check.py:967-975 captures stderr (capture_output=True) but only
test_result.stdout is used (line 977). stderr is never printed or logged. For
exit codes 4 (usage error) and 5 (no tests collected), stderr contains the only
diagnostic and is silently discarded. Fix: print test_stderr before the failure
message.

### MCP-45 (LOW) -- baseline delta parser only handles pytest output format
gate_check.py:815-829 compute_baseline_delta hardcodes 'FAILED ' prefix parsing
(pytest -q format). For non-pytest runners (cargo, go, make, npm -- all in
KNOWN_RUNNERS), every failure is invisible to baseline delta, silently allowing
all failures through when a baseline exists. Fix: document limitation or add a
format field to test config.

### MCP-46 (MEDIUM) -- _run_l1_phase return type annotation wrong
machine.py:603 declares -> list[StateFinding] but line 632 returns a 2-tuple
(l1_findings, l1_excerpts). Caller at line 728 destructures correctly so no
runtime failure, but annotation misleads static analysis. Fix: change to
tuple[list[StateFinding], list[dict]].

### MCP-47 (MEDIUM) -- hold.py _prompt_one crashes on empty line_range
hold.py:80-85 formats finding.line_range[0], line_range[1]. Several code paths
create StateFinding with line_range=[] (machine.py:310, :985-992, e2e_check).
If such a finding reaches UNCERTAIN and enters HOLD, IndexError crashes the UI
and leaves the state machine stuck. Fix: guard with len(line_range) >= 2.

### MCP-48 (MEDIUM) -- diagnose_non_convergence masks real reason when infra_errors present
machine.py diagnose_non_convergence() always returns 'D' (DELEGATED) when
infra_errors is non-empty, regardless of other conditions. This masks the real
non-convergence reason (e.g., oscillating findings). Fix: report both infra
errors and the actual convergence diagnosis.

### MCP-49 (HIGH) -- adoption-survey.md references nonexistent subcommand
docs/adoption-survey.md references 'code-forge baseline' which does not exist
in cli.py argparse. Fix: remove or replace with the actual command.

### MCP-50 (LOW) -- gate.schema.json runner list omits 'python'
gate.schema.json lists known runners but omits 'python' (bare python test
runner). Fix: add 'python' to the runners enum.

### MCP-51 (LOW) -- manual.md recommends deprecated deepseek-chat model ID
docs/manual.md section 3 shows 'deepseek-chat' as example model. This was
replaced by deepseek-v4-flash. Fix: update to current model ID.

### MCP-52 (DROPPED) -- reviewer-canary-spec.md status is correct
Finding misquoted the status ("proposed"). Actual text: "Design complete, not
implemented". canary.py exists as infrastructure but the reviewer canary feature
is NOT shipping per spec and project memory. Status is correct. Finding is FALSE.

### MCP-53 (LOW) -- graph_triage.py dead try/except around _get_sem_impact
graph_triage.py:493-496 catches TimeoutExpired around _get_sem_impact, but
_get_sem_impact (line 203-216) already catches it internally and returns
fallback. Outer try/except is dead code. Fix: remove the outer try/except.

### MCP-54 (MEDIUM) -- graph_triage.py sqlite3 connection leak on exception
graph_triage.py:372-382 find_entity_dependents opens conn (line 376), closes
at line 381, but if _live_callers raises sqlite3.Error the except (line 383)
does bare pass without closing conn. Fix: use contextlib.closing(conn) or
try/finally. Same pattern at _run_graphdb (lines 239-276).

### MCP-55 (MEDIUM) -- mcp_jobs.py get_job pops on first read, second poll errors
mcp_jobs.py:106-107 pops terminal jobs on first get_job call. Second poll
returns None -> ToolError("Unknown job_id"). Contradicts idempotentHint=True
declared at mcp_server.py:579. Fix: do not pop on read (let TTL evict), or
return a tombstone {"status": "expired"} for recently-collected jobs.

## Convergence Report (R1-R5)

78/78 .py files in src/code_forge/ read across 5 rounds (157+44+30+33+41 = 305
agents, ~36M tokens). R5 confirmed 4 findings (0 BLOCKER, 0 HIGH). File-level
convergence confirmed. Findings list MCP-01..55 is exhaustive.

| Round | Agents | Found | Confirmed | Severity peak |
|-------|--------|-------|-----------|---------------|
| R1    | 83     | 76    | 9         | HIGH          |
| R2    | 44     | 39    | 18        | BLOCKER       |
| R3    | 30     | 25    | 6         | HIGH          |
| R4    | 33     | 28    | 10        | HIGH          |
| R5    | 41     | 35    | 4         | MEDIUM        |

Main-path safe: MCP-19/20/10 do not affect `code-forge review` + API backend
happy path (verified R3 Impact phase).

## Main-Path Impact Assessment (R3)

The three most severe findings (MCP-19 BLOCKER, MCP-20 HIGH, MCP-10 HIGH)
were traced against the main review path: `code-forge review` CLI-direct with
trusted gate.yaml and configured API backend (deepseek/mimo, format=openai,
stream=false default).

**Result: main path safe. No blockers on the happy path.**

- MCP-19 (CliError not imported): only reachable via _invoke_anthropic or
  _invoke_vertex with stream=true. Main path uses _invoke_openai. Latent bug.
- MCP-20 (_run_ci returns None): only entered in Mode.CI. CLI-direct defaults
  to Mode.LOCAL. Race condition in CI mode only.
- MCP-10 (--no-color): only passed by mcp_server.py subprocess spawn. CLI-direct
  never passes it. MCP subprocess path breaks, CLI-direct path unaffected.

## Suggested Phase 36 acceptance signals
- A single `forge_resolve_outlet` (or new `forge_doctor`) call explains exactly
  why a backend is / is not usable, and its answer MATCHES what `forge_review`
  will do (kills MCP-02).
- MCP server resolves workspace from the review target / an explicit env var,
  not a cwd-walkup that cannot descend (kills MCP-01).
- Trust re-read per request OR a clear "trust changed, reconnect needed" signal
  surfaced to the client (mitigates MCP-03).
- gate.yaml edit warns about trust invalidation at edit time (mitigates MCP-05).
- `code-forge review` can run against main-tree changes via an explicit opt-in
  (`--allow-main` or env), instead of an unconditional exit 2 (fixes MCP-07).
- Trust degrades to a lighter model: re-confirm only on dangerous-field changes
  (credential targets), with a trust-on-first-use or env opt-in for the rest,
  so reviewing one's own config is not all-or-nothing ceremony (fixes MCP-08).
- CliError carries a remediation field, and the operational errors (gate.yaml,
  contract, registry, baseline, trust, outlet) each render a one-line fix that
  matches the worktree guard's quality (fixes MCP-09, MCP-17, MCP-18).
- MCP CLI subprocess args match CLI argparse: --no-color accepted by review and
  gate-check parsers; --baseline/--backend either wired into gate-check or
  removed from MCP tool params (fixes MCP-10, MCP-11).
- No phantom CLI flags in docs: --auth-timeout either exists or is removed from
  configuration.md; --output either added to review or CI docs use stdout
  redirect; 'code-forge --version' (not 'review --version') in setup docs
  (fixes MCP-12, MCP-13, MCP-14).
- setup-claude-code.md documents both pre-commit and commit-msg hooks, matching
  setup-vscode.md (fixes MCP-15).
- MCP init/trust tool results include stderr so the client sees confirmation
  messages, not empty string (fixes MCP-16).
- llm_invoke.py imports CliError (or uses LLMInvokeError); streaming Anthropic/
  Vertex no longer crashes with NameError (fixes MCP-19).
- _run_ci returns Verdict.PENDING (not None) when mutation PID alive; MCP path
  no longer crashes on verdict.value (fixes MCP-20).
- Tool timeout/OS errors surface as infra_errors, not silent zero-findings
  (fixes MCP-21). Malformed smoke receipts log a warning (fixes MCP-22).
- All subcommands catch FileNotFoundError for git/tools; smoke-run has --timeout
  (fixes MCP-23, MCP-24). install-skill shows available skills on not-found
  (fixes MCP-27).
- Docs exit code tables complete (all 8 codes); outlet-alignment.md Wave 0
  updated; setup-ci.md gate-check prerequisites documented (fixes MCP-28..32).
- Hook error messages actionable; parser ToolError carries stderr; _sarif.py
  per-item resilient (fixes MCP-33..36).
- Corpus loader validates expected_verdict on load (fixes MCP-37).
- init handles .code-forge-as-file gracefully (fixes MCP-38).
- Invalid outlet string produces clean CliError, not ValueError traceback
  (fixes MCP-39).
- fixval keeps patch file on disk when restore fails (fixes MCP-40).
- Version string single-sourced across pyproject.toml and __init__.py
  (fixes MCP-41).
- install.sh exits nonzero when zero skills installed (fixes MCP-42).
