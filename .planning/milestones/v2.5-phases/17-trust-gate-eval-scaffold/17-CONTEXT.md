# Phase 17: Trust Gate + Eval Scaffold - Context

**Gathered:** 2026-06-09
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 17 delivers two things: (1) close the CVE-class gate.yaml credential
exfil hole with a direnv-style trust gate (SEC-01), and (2) stand up the eval
scorecard scaffold so each subsequent axis can be measured (EVAL-01). It also
lays the foundational AxisRunner infrastructure (AdvisoryFinding type + Protocol)
that all subsequent advisory axes (Phases 18-22) depend on.

</domain>

<decisions>
## Implementation Decisions

### Trust Gate (SEC-01)

- **D-01:** direnv-style trust, stored OUTSIDE the repo. `code-forge trust`
  records trust in `~/.config/code-forge/trusted.json` (honor $XDG_CONFIG_HOME
  if set), mapping the realpath of the repo's gate.yaml to sha256 of its
  backends block. A repo cannot carry its own trust record: the store lives in
  the user's home config, keyed by the absolute checkout path the repo author
  cannot write or predict. Matches PITFALLS.md Pitfall 9 and direnv's allow
  model. gate.yaml backends changes invalidate the stored hash; user re-trusts.
- **D-02:** Per-repo granularity is achieved by KEYING the home-dir store on
  the gate.yaml realpath, NOT by placing a file inside the repo. There is NO
  in-repo `.trusted` file. An in-repo marker (even gitignored) lets a hostile
  clone ship a self-authorizing record; sha256(backends) is attacker-computable
  and .gitignore is commit hygiene, not a security boundary. SC#2 (no exfil on
  clone+review) is satisfiable only with an out-of-repo store.
- **D-03:** Hash scope: only hash the backends block (YAML `backends:` key),
  not the entire gate.yaml. Changes to outlet/test/detect sections do not
  require re-trusting.
- **D-04:** CLI: `code-forge trust` (mark trusted), `code-forge trust --status`
  (show trust state), `code-forge trust --revoke` (remove the repo's entry from
  ~/.config/code-forge/trusted.json).
- **D-05:** On `code-forge trust`, stderr displays the dangerous fields found
  in gate.yaml (base_url, api_key_env, api_key_file, shell, command, hook) so
  the user knows what they are trusting. Informational, not a gate.
- **D-06:** Behavior when untrusted: repo-supplied backends block is ignored;
  forge falls back to session-default backend (same as no gate.yaml). stderr
  warns: "Untrusted repo backends ignored. Run `code-forge trust` to enable."

### Eval Scaffold (EVAL-01)

- **D-07:** Corpus format: self-contained diff files + YAML manifest
  (`tests/eval/corpus/corpus.yaml`). Each entry: name, diff_file (relative
  path to .diff), expected_verdict (HOLD/PASS), axis_tags (list).
- **D-08:** Entry point: `code-forge eval` CLI subcommand.
  `code-forge eval --corpus path/to/corpus.yaml --backend mimo`.
- **D-09:** Cross-repo handling: diff files extracted once from source repos
  (surflare-watchdog, forge itself) and stored self-contained in
  tests/eval/corpus/. No runtime dependency on external repos.
- **D-10:** Output: stderr human-readable table (per-entry expected vs actual)
  + JSON file (programmatic). Dual output like forge review's SARIF mode.
- **D-11:** Run count is axis-dependent, not a flat 1. Deterministic axes
  (FIXVAL revert-RED, TRUST taint) run once. LLM-reviewed axes (RUNTIME,
  LEGACY, INTENT) default to 3 runs with 2-of-3 majority (PITFALLS.md Pitfall 4
  P2 + P5). `--runs N` overrides. A single LLM-axis run must NOT be reported as
  a false-green rate -- it swings >10% on one stochastic draw.
- **D-12:** corpus entry failure (diff apply error, backend timeout) = SKIPPED,
  continue. SKIPPED is a first-class adverse outcome: (a) reported with count
  and per-entry reason, (b) excluded from the false-green denominator (never
  counted as PASS/caught), (c) skip rate shown beside the catch count so a
  backend cannot improve its score by timing out on hard entries. The tool that
  measures honesty must be honest about what it did not run.
- **D-13:** Extensibility: plugin-style axis hooks (pre_review / post_review).
  Each axis registers its own hook. New axes add hooks without changing eval
  core code.

### AxisRunner Protocol (foundational for Phases 18-22)

- **D-14:** AdvisoryFinding is an independent dataclass, completely separate
  from StateFinding. No shared base class. machine.py maintains
  `self.advisories: list[AdvisoryFinding]` independent of `self.findings`.
  Type-level unreachable: AdvisoryFinding cannot participate in cycle dirty
  determination because it is never in the findings list.
- **D-15:** Serialization: AdvisoryFinding writes to a separate file
  (not mixed into review-state.json). Complete type separation.
- **D-16:** Timing: advisory axes run once after convergence by default;
  configurable to run per-cycle. Both PASS and HOLD verdicts trigger advisory
  axes (HOLD needs to know unverified surfaces too).
- **D-17:** Display: split display in stderr -- blocking findings first
  (HOLD reason), separator line, then advisory findings (reference info).
  Never mixed.
- **D-18:** Phase 17 scope: define AdvisoryFinding dataclass + AxisRunner
  Protocol + machine.py advisories list + verdict output extension + implement
  SEC-01 trust gate (which is blocking, not advisory -- first concrete axis).
- **D-19:** Each eval entry runs the COMPLETE pipeline (L0 + L1 + L2 + all
  enabled axes), not just the axis under test (PITFALLS.md Pitfall 4 P3).
  Per-axis contribution is recorded so a new axis regressing L0/L1 convergence
  is attributable. Testing only the new axis misses cross-axis interference.

### Claude's Discretion

- AxisRunner Protocol method signatures (run interface)
- trusted.json entry format details beyond sha256 (metadata, timestamps)
- eval JSON output schema details
- Advisory file naming convention

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Seed Brief (v2.4 scope + guardrails)
- `/tmp/draft_20260609_forge_v24_milestone_seed.md` -- 6 axes, priority rules,
  DO NOT list, Definition of Done per axis, founding principle

### Trust/Security Evidence
- `/tmp/draft_20260605_forge_trust_boundary_requirement.txt` -- SEC-01 + TRUST-01
  success criteria, CVE references, direnv-style precedent

### Runtime Escape Catalog
- `/tmp/draft_20260609_forge_v24_runtime_escape_catalog.md` -- E1-E6 escape
  catalog, eval corpus entries, gap analysis vs 06-05 corpus

### Research
- `.planning/research/STACK.md` -- zero new deps, semgrep/sem-cli integration
- `.planning/research/FEATURES.md` -- table stakes/differentiators per axis
- `.planning/research/ARCHITECTURE.md` -- integration points with file:line
- `.planning/research/PITFALLS.md` -- 10 pitfalls with prevention strategies

### Existing Code
- `src/code_forge/backend.py` -- load_backend_configs, gate.yaml loading
- `src/code_forge/machine.py` -- StateMachine, _fixpoint_reached, l2_runner
- `src/code_forge/state.py` -- StateFinding dataclass (model for AdvisoryFinding)
- `src/code_forge/factories.py` -- build_* pattern for injectable components
- `src/code_forge/verdict.py` -- determine_verdict (PASS/FAIL only currently)
- `src/code_forge/cli.py` -- subcommand registration pattern

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `backend.py:load_backend_configs()`: parses gate.yaml backends block -- trust
  gate wraps this with hash check before allowing use
- `factories.py:build_*` pattern: injectable callables for falsifier, l2_runner,
  e2e_checker -- AxisRunner follows the same factory pattern
- `cli.py` subcommand registration: init, detect, resolve-outlet, verify, review
  -- trust and eval join as new subcommands
- `state.py:StateFinding` dataclass: model for AdvisoryFinding's structure
  (but NOT its base class -- separate types)

### Established Patterns
- SARIF output (cli.py): dual stderr/stdout output -- eval follows same pattern
- shutil.which for tool detection (detect.py): semgrep follows same convention
- yaml.safe_load for gate.yaml (outlet_resolver.py, cli.py): trust reuses same
  loader, adds hash verification layer

### Integration Points
- `backend.py` load path: trust gate inserted before load_backend_configs returns
- `cli.py _run()`: trust check before backend dispatch (~line 690)
- `machine.py StateMachine`: new advisories list + post-convergence axis dispatch
- `verdict.py determine_verdict()`: extend return to include advisory findings

</code_context>

<specifics>
## Specific Ideas

- Trust gate modeled after direnv's allow/deny workflow (user explicitly trusts
  each repo's gate.yaml before backends are used)
- Eval as "smoke test" framing, not "benchmark" (9 entries too small for stats)
- AdvisoryFinding must have NO path to blocking -- the founding principle says
  advisory axes NEVER block, and this must be structural, not conventional

</specifics>

<deferred>
## Deferred Ideas

- danger-score field-level analysis (Phase 18 REVIEW-TRUST-01)
- semgrep taint rules for config-to-sink flows (Phase 18)
- Revert-RED / STING overfit guard (Phase 19 REVIEW-FIXVAL-01)
- Verdict UNVERIFIED calibration (Phase 20 REVIEW-RUNTIME-01)
- Legacy blame attribution (Phase 21 REVIEW-LEGACY-01)
- Graph-triage blast-radius ranking (Phase 22 REVIEW-SYSTEM-01)

</deferred>

---

*Phase: 17-Trust Gate + Eval Scaffold*
*Context gathered: 2026-06-09*
