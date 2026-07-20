FORGE -- cli-backend `env` field (implementation handoff)
For: a forge implementation sub-session. From: main session. Date: 2026-06-29.
Implements: ADR-0004 open item ("Declarative env on cli backends").

READ FIRST (architecture design -- local-only, on disk, do NOT commit/push):
  ~/code/forge/docs/adr/0004-account-authenticated-backends.md
That ADR is the WHY. It is gitignored (docs/adr/ is local-only, snapshot-backed
via .git/snapshot-planning.sh). Read it before coding; do not add it to git.

GOAL
----
Let a `type: cli` backend declare environment changes applied to the spawned
CLI child, so a user can replicate an account-pinning shell wrapper (e.g. a
bashrc `claude-me` that runs `env -u ANTHROPIC_BASE_URL -u ANTHROPIC_API_KEY
-u CLAUDE_CODE_USE_VERTEX ... command claude`) declaratively in gate.yaml
instead of via an external launcher script.

Today `_invoke_cli` spawns the child with NO `env=` (llm_invoke.py:462), so the
child inherits forge's full environment unchanged. If forge's own environment
carries ANTHROPIC_BASE_URL / CLAUDE_CODE_USE_VERTEX, the spawned `claude` goes
to that endpoint instead of the intended account. This field fixes that.

DESIGN (hashable, frozen-dataclass-safe)
----------------------------------------
BackendConfig is `@dataclass(frozen=True)` (backend.py:58). Do NOT add a raw
`dict` field -- a dict is unhashable and, although nothing hashes BackendConfig
today (the probe cache keys by backend.name, backend.py:413/431), a dict field
is a latent landmine the first time someone puts a config in a set. Use tuples:

  env_unset: tuple[str, ...] = ()                 # var NAMES to remove
  env_set:   tuple[tuple[str, str], ...] = ()     # (NAME, VALUE) pairs to set

YAML surface (ergonomic; parse converts to the tuples above):
  backends:
    pinned-claude:
      type: cli
      command: claude
      model: opus
      env:
        unset: [ANTHROPIC_BASE_URL, ANTHROPIC_API_KEY, CLAUDE_CODE_USE_VERTEX]
        set:
          SOME_VAR: forced-value
  - `env` absent       -> env_unset=(), env_set=()  (behaves EXACTLY as today)
  - `env.unset` absent -> env_unset=()
  - `env.set` absent   -> env_set=()

CRITICAL FOOTGUN (acceptance depends on this)
---------------------------------------------
In _invoke_cli, build the child environment from a COPY of os.environ, then
apply unset/set. NEVER pass a minimal dict as env= (that wipes PATH, HOME, etc.
and breaks the CLI and its own subprocesses):

  if backend.env_unset or backend.env_set:
      child_env = dict(os.environ)
      for k in backend.env_unset:
          child_env.pop(k, None)          # pop, not del -- absent var is fine
      child_env.update(dict(backend.env_set))
  else:
      child_env = None                    # byte-identical to today (no env=)
  ...
  proc = subprocess.Popen(cmd, ..., env=child_env)

Passing env=None to Popen means "inherit parent env" -- this is what makes the
no-field path a true no-op. The two cmd shapes (large-prompt `sh -c` form at
llm_invoke.py:450 and the normal form at :455) BOTH route through the single
Popen at :462, so env= is applied once and covers both. Note `binary` is
already resolved to an absolute path via shutil.which BEFORE Popen
(llm_invoke.py:434), so unsetting PATH does not break launching `claude` itself
-- but it may break subprocesses the CLI spawns; document that in the YAML
comment and configuration.md (unset PATH at your own risk).

WHERE (grounded anchors -- re-verify before editing)
-----------------------------------------------------
- src/code_forge/backend.py
  - BackendConfig dataclass (frozen, L58-77): add the two tuple fields with
    `()` defaults (immutable default is safe on a frozen dataclass).
  - _parse_backend_entry (L97): the cli branch is L173-183 (`# type == "cli"`,
    returns BackendConfig at L175). Read entry.get("env"); if present:
      * validate it is a dict; reject otherwise with CliError naming the
        backend.
      * env.get("unset"): validate list[str]; -> tuple(unset).
      * env.get("set"): validate dict[str, scalar]; coerce values to str;
        -> tuple(sorted(set.items())) (sorted = deterministic/hashable).
      * reject any key other than unset/set with a clear CliError.
    Thread env_unset/env_set into the returned BackendConfig. The api and
    vertex branches (L119-172) do NOT get this field -- env is cli-only;
    if `env` appears on an api backend, raise CliError (it would silently do
    nothing otherwise).
- src/code_forge/llm_invoke.py
  - _invoke_cli (L426): build child_env as above; add env=child_env to the
    Popen at L462. No other call site.

TESTS (TDD -- RED first, then GREEN; bug-inject each assertion)
---------------------------------------------------------------
Ground the real test files first (tests/test_backend.py for parse; the
llm_invoke cli tests -- find them, do not assume the filename).
  parse:
   - env absent              -> env_unset==(), env_set==().
   - env.unset=[A,B]         -> env_unset==("A","B").
   - env.set={X:1}           -> env_set==(("X","1")) with value coerced to str.
   - env.unset and env.set both -> both populated.
   - env on an api backend   -> CliError.
   - env not a dict          -> CliError.
   - unknown key in env       -> CliError.
  invoke (mock subprocess.Popen, assert the env= kwarg):
   - no env field            -> Popen called with env=None (NOT a dict).
   - env_unset=("ANTHROPIC_BASE_URL",) and that var present in os.environ
     (monkeypatch os.environ) -> Popen env= is a dict that COPIES os.environ
     MINUS that key (assert PATH still present, assert the key absent).
   - env_set=(("X","1"),)    -> Popen env= has X==1.
Bug-inject proof:
  - for the no-regression test: temporarily make the else-branch pass env={}
    -> the "PATH still present" / env=None test must FAIL -> restore -> PASS.
  - for unset: temporarily skip the pop -> the "key absent" test must FAIL.

DOCS (keep in sync -- Cross-Scan rule)
--------------------------------------
configuration.md already documents the account-auth env nuance in prose
("start the code-forge / MCP process with the same scrubbed environment",
under "### Example: Local Claude CLI"). When this field lands, UPDATE that
paragraph to show the declarative `env:` form, and add an `env:` row to the
CLI-backend reference / gate.schema.json if one exists. Run `code-forge init`
schema generation if the schema enumerates backend fields. configuration.md is
TRACKED -- edit it in the worktree and commit with `# docs`.

PROCESS (forge rules -- non-negotiable)
---------------------------------------
- Worktree first: git -C ~/code/forge worktree add .worktrees/work-clienv
  -b feat/cli-env-field. Never edit the main worktree.
- LOGIC-BEARING: full three-cycle review (9 passes) OR external multi-model
  (aicc) review to 0/0/0/0, + Step 0 (ruff, py_compile, non-ASCII), + the
  bug-inject smoke above. impl != reviewer (separate sub-sessions).
- Commit marker: post-review-c3 (logic) for the code; `# docs` for the
  configuration.md change (separate commit). WHY in the body, no review
  vocabulary, no plan/ADR refs in code comments. Translate the ADR rationale
  into a self-contained comment at the env= site.
  Signed-off-by: Minxi Hou <houminxi@gmail.com>.
- Report back: branch + SHA + diff --stat + pytest + the bug-inject evidence.
  No auto-merge; host ff-merges.

ACCEPTANCE (done-conditions)
----------------------------
- A cli backend with no `env` behaves byte-identically to today: Popen gets
  env=None, full suite green.
- `env.unset` removes exactly those vars from the child's copied environment;
  PATH/HOME survive unless explicitly unset.
- `env.set` forces those vars in the child.
- `env` on an api/vertex backend is a config error (fail fast at parse).
- BackendConfig stays hashable (env fields are tuples): `hash(some_config)`
  does not raise.

SCOPE NOTE (do less, if you prefer)
-----------------------------------
The essential operation for the claude-me use case is `unset` (claude-me only
clears vars; it sets nothing auth-relevant). `set` is the "open passthrough"
half. If you want the smaller diff, ship `env_unset` alone and leave `env_set`
for a follow-up -- say so in the report. Either is acceptable; do not ship
`set` without `unset`.
