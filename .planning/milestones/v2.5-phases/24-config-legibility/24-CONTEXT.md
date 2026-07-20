# Phase 24: Config Legibility - Context

**Gathered:** 2026-06-15
**Status:** Ready for planning
**Source:** PRD Express Path -- /tmp/draft_20260614_forge_step1_REVISED_schema.txt

## Phase Boundary

Make gate.yaml self-documenting so a user or LLM can fill it correctly without
reading source code. Ships gate.schema.json for IDE assist. A corpus round-trip
test proves schema and loader stay in sync.

## Locked Decisions (do not re-litigate)

**A1.** Backend EXAMPLES in the shipped template use GENERIC names
  (local-claude / claude-api / openai-compatible / vertex), NOT the CN paid
  stack (mimo/deepseek/kimi/glm/minimax).

**A2.** Every commented optional field carries a "Defaults to X" / "omit =
  <behavior>" line (the k8s kubectl-explain habit).

**A3.** gate.schema.json + a `# yaml-language-server: $schema=...` directive
  are IN SCOPE for Phase 24.

**A4.** The schema must NOT become a second drift source. A corpus round-trip
  test (schema vs real loader) is REQUIRED -- the anti-drift teeth that justify A3.

**Schema delivery (F -- sub-session decides, must note the decision):**
  RECOMMENDED: `code-forge init` writes BOTH .code-forge/gate.yaml (with
  `# yaml-language-server: $schema=./gate.schema.json`) AND a copy of
  .code-forge/gate.schema.json. Self-contained, offline, versioned alongside
  the config. ALT: reference a remote URL pinned to a release tag. Do NOT
  point $schema at `main`.

## Ground Truth (verified from loader source)

- backends live ONLY in .code-forge/gate.yaml under `backends:` MAP keyed by
  name. No reader for ~/.config/code-forge/backends.yaml (doc fiction).
- backends MUST be a dict (backend.py raises CliError on list).
- test.command MUST be a list (gate_check.py raises ValueError on string).
- test real keys: command(list), timeout_seconds(int>0, default 120), env(dict),
  cwd(str), source_patterns(list). `pattern` is NOT read (no-op).
- outlet canonical = subprocess | inline | subagent. `cli` is a DEPRECATED
  ALIAS for subprocess (stderr warning).
- backend api fields: format in {anthropic, openai, vertex}; anthropic/openai
  need base_url + api_key_env; model optional; max_tokens optional (IS read).
  vertex needs project_id; region default "global"; credentials_path optional.
  cli backend: command optional (default claude), model optional.
- at most one backend may set `default: true`.
- non_ascii in {ai-smell(default), strict}. presubmit is a LIST, each entry:
  command(list), applies_to(glob str), on(diff|patch), when_exists(str optional).
- graph_triage / daemon_state / components schemas already verified (carry over).

## Deliverables (in scope)

**C1-C3 (docs/config, no 9-pass, # config commit):**
- src/code_forge/init_template.py (GATE_YAML_TEMPLATE rewrite)
- docs/configuration.md (heaviest rewrite -- kill backends.yaml fiction)
- README.md (Backend config section)
- docs/setup-vscode.md (naming only; IDE rendering claims -> Step 3)
- docs/setup-cursor.md (naming + see-also link)
- docs/setup-pycharm.md (see-also link)
- NEW: src/code_forge/gate.schema.json (JSON Schema draft-2020-12)
  Fix in all: kill ~/.config/code-forge/backends.yaml fiction; backends LIST
  -> DICT; FORGE_OUTLET=cli -> subprocess; add max_tokens + vertex to tables.

**C4 (logic-bearing test, three-cycle review, # post-review-c3 commit):**
- NEW: tests/test_schema_corpus.py -- round-trip test: valid/invalid snippets
  judged by BOTH jsonschema (gate.schema.json) AND real loader.

## Verification (prove before commit)

E1. non-ASCII on every changed file (git diff | grep -P '[^\x00-\x7F]').
E2. Shipped template parses clean via load_backend_configs() + gate_check
    test validator. All-commented form = valid no-op.
E3. gate.schema.json validates as JSON Schema draft-2020-12.
E4. C4 corpus test passes: schema and loader agree on all covered cases.
E5. Grep 6 files: zero `backends.yaml`, zero `- name:` under backends, zero
    `FORGE_OUTLET=cli`.
E6. $schema directive resolves OFFLINE to shipped schema file.

## Out of Scope (do NOT do, do NOT claim)

G1. Real IDE hover/autocomplete/redline verification -> Step 3.
    Must NOT write "hover works in VS Code" -- unverified until Step 3.
G2. /code-forge-setup guided skill -> Step 2.
G3. Generating schema FROM Python validators -> out of scope.

## Delivery Rules (non-negotiable)

- Phase 0: git worktree add .worktrees/onboarding-step1 off verified main.
- English + ASCII only in .md/config. No plan-ref/task-id/marker in code.
- Commit messages human-voiced; # marker at command END, outside quotes.
- Main-tree merge + push require OWNER authorization.
