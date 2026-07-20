# Phase 23: Daemon State - Context

**Gathered:** 2026-06-14
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 23 delivers REVIEW-STATE-01: a cross-subsystem state-conflict advisory axis
that detects when daemon/service code mutates shared external state (nftables marks,
routing rules, locks, PID files) that another concurrently-active subsystem depends
on. The axis is opt-in (gate.yaml `daemon_state` section), with a heuristic
soft-prompt fallback for unconfigured projects.

In scope: DaemonStateRunner advisory axis; gate.yaml `daemon_state` schema +
validation; two-step LLM call (Q1 state enumeration -> grep -> Q2+Q3 conflict
analysis); static conflict rules + LLM supplement; RuntimeRunner.last_surfaces
storage for cross-axis data sharing; SKILL.md mirror + D-10 drift test; E9 eval
corpus entry.

Out of scope: blocking behavior of any kind; runtime execution of detected
conflicts; Phase 22 graph triage (separate axis, not a hard dependency); daemon
process monitoring or management; any change to RUNTIME Q4 (already shipped in
Bucket 1).

## Scope Honesty (mandatory, per brief #6)

ONE confirmed daemon consumer: surflare-watchdog (the killswitch self-lock bug
that motivated this axis). Forge itself is a stateless CLI tool -- it CANNOT dogfood
this axis. Public-package demand for daemon-state review is unknown. This phase
pre-builds a capability on 1 consumer + prediction that daemon/service code is a
common review target. If adoption evidence does not materialize (stars, issues, PRs
requesting this feature), the axis should remain opt-in indefinitely rather than
being promoted to always-on.

</domain>

<decisions>
## Implementation Decisions

### Area 1: Daemon Detection Strategy

- **D-01:** Hybrid detection -- gate.yaml explicit declaration takes priority;
  diff content heuristic (keyword match) serves as fallback.

- **D-01a:** Narrow + extensible keyword set. Default keywords: nft, iptables,
  "ip route", systemctl, firewall-cmd, tc. gate.yaml `daemon_state.patterns` allows
  user-defined additions (e.g., "flock", "pidfile").

- **D-01b:** gate.yaml schema: `daemon_state: { enabled: true, subsystems: [...],
  patterns: [...] }`. `subsystems` tells the axis which external state domains to
  focus on; `patterns` extends the keyword set.

- **D-01c:** Q4 = screening, this axis = deep dive. RUNTIME Q4 (always-on) does
  initial screening ("does another subsystem exist?"). This axis goes deeper when
  activated: pull in code, build conflict matrix, generate specific findings.

- **D-01d:** Read RuntimeRunner.last_surfaces output. DaemonStateRunner reads
  RuntimeRunner's `last_surfaces: list[str]` attribute (set during `run()`) to
  get the RUNTIME axis's surface enumeration. machine.py must guarantee RUNTIME
  executes before DaemonState in `advisory_runners` list order.
  **Implementation note (from smoke test):** RuntimeRunner currently does NOT store
  surfaces -- `run()` uses them as a local variable. Must add `self.last_surfaces`
  field and store after `_parse_llm_response()`.

- **D-01e:** RUNTIME failure fallback. If RuntimeRunner skipped (LLM timeout),
  DaemonStateRunner falls back to pure heuristic (gate.yaml subsystems + diff keyword
  match). Logs degradation reason.

- **D-01f:** Unconfigured behavior. No `daemon_state` in gate.yaml: heuristic
  match only outputs a one-line advisory prompt ("Detected stateful subsystem
  keywords; enable daemon_state in gate.yaml for deeper analysis"). Full axis does
  NOT run without explicit opt-in.

### Area 2: State Compatibility Matrix

- **D-02:** Hybrid matrix -- gate.yaml can declare known static conflict rules;
  LLM supplements with newly-discovered conflicts not covered by static rules.

- **D-02a:** Structured triplet format: `{subsystem, mutates, interferes_with}`.
  Semantically unambiguous, machine-matchable, LLM-readable.

- **D-02b:** Reuse AdvisoryFinding existing fields. Conflict info encoded in
  `description` field: "[subsystemA] mutates X; [subsystemB] depends on Y ->
  conflict scenario". No dataclass extension, consistent with existing axes.

- **D-02c:** Dual storage: gate.yaml inline rules and external `conflicts_file`
  reference both supported. Few rules go inline; many rules in separate YAML file.

- **D-02d:** Static rules first, LLM only supplements new. Static rule matches
  are output first; known conflicts injected into LLM prompt so LLM only reports
  new findings not covered by static rules (FUSE-01 pattern).

- **D-02e:** Strict schema validation in gate_check.py. `daemon_state` section
  validated: `subsystem`/`mutates`/`interferes_with` required in each conflict
  triplet. Typos cause validation error. Consistent with existing backend validation.

### Area 3: Mechanical Questions

- **D-03:** Analytical chain-breaking style. Three questions decompose the conflict
  chain: enumerate state -> find shared readers -> describe failure scenario.

- **D-03a:** Exact wording locked (hardcoded as constant):
  Q1: "Enumerate every piece of external state (nftables marks, routing rules,
  locks, PID files, shared sockets) this diff creates, modifies, or deletes.
  For each, list ALL possible values at call time."
  Q2: "For each external state item above, which OTHER subsystem or function
  (not in this diff) reads, writes, or depends on that same state? Name the
  subsystem and the specific operation."
  Q3: "For each (state, subsystem) pair with a conflict: describe the concrete
  failure scenario -- what happens when both subsystems run concurrently and the
  state values collide or are consumed out of order?"

- **D-03b (revised):** Two-step LLM call. Step 1: Q1 alone (enumerate state) ->
  extract keywords -> grep repo. Step 2: Q2+Q3 with grep context injected.
  Revised from original single-call decision to support grep-based substrate.

### Area 4: Context Substrate

- **D-04:** grep priority + graph optional supplement. Default: grep repo for
  Q1 output keywords. If Phase 22 graph is available, use it as a SUPPLEMENT
  to grep, not a replacement: the graph models code-dependency edges
  (call/import), whereas this axis needs shared-external-state co-occurrence
  (two subsystems touching the same nft mark / route / lock), which is
  typically NOT a call edge. Grep over the Q1 state strings stays the primary
  substrate; the graph can add caller/callee context but will miss conflicting
  subsystems that share state without a code dependency. Phase 22 is NOT a
  hard dependency -- Phase 23 can ship independently with grep-only substrate.

- **D-04a:** grep keywords from LLM Q1 output. Two-step flow: Q1 returns state
  items -> extract identifiers (e.g., "mark 0xff", "nft add rule inet filter") ->
  grep these in repo.

- **D-04b:** Relevance-ranked grep results. Sort by hit count per file, take
  top-K files (K=5), extract full function context around each match. Inject into
  Q2+Q3 prompt.

### Implementation Constraints (from smoke test, not user decisions)

- **D-07:** Q1 envelope key = `frozenset({"external_state"})`. DaemonStateRunner
  must pass `expected_keys=frozenset({"external_state"})` to `llm_invoke` for the
  first call. Per the F1/F2 fix (commit 652cbd6 + 83b8c36), envelope contract
  enforcement requires explicit keys for non-review callers.
  **Smoke test verified:** `_extract_json_from_text` correctly extracts
  `{"external_state": [...]}` with this key set; returns None with default keys
  (F1 safety preserved).

- **D-08:** grep input sanitization. All grep subprocess calls must use
  `subprocess.run` with list arguments (no `shell=True`). LLM output keywords
  go directly into the argument list, never through shell expansion.
  **Smoke test verified:** `subprocess.run(["grep", "-rn", keyword, path])`
  works correctly with special characters in keywords.

### Axis Metadata

- **D-05:** SKILL.md mirror + D-10 drift test. Follow Phase 20 RUNTIME pattern:
  DaemonState questions hardcoded as a constant, mirrored verbatim in code-forge
  SKILL.md, drift test asserts equality.

- **D-06:** E9 eval corpus entry (renamed from E8 to avoid collision with Phase 22). Add after implementation, once output format is
  confirmed by smoke test. Killswitch+mark scenario, expect axis to activate and
  report nftables mark conflict.

### Advisory Contract (pre-answered from Phase 20/21)

Advisory only -- never blocks verdict, never resets cycle counter, never gates
commit. Same contract as RUNTIME (Phase 20 D-04/SC4) and LEGACY (Phase 21).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Advisory axis infrastructure
- `src/code_forge/advisory.py` -- AdvisoryFinding dataclass + AxisRunner Protocol
- `src/code_forge/machine.py` L969-996 -- `_run_advisory_axes()` dispatch loop
- `src/code_forge/cli.py` L1493-1516 -- advisory runner registration order

### RUNTIME axis (pattern to follow)
- `src/code_forge/runtime.py` -- RUNTIME_LIFECYCLE_QUESTION constant, RuntimeRunner,
  _parse_llm_response. **Must add `last_surfaces` field to this file.**
- `tests/test_runtime_drift.py` -- D-10 drift test (replicate for DaemonState)

### LLM invocation (F1/F2 envelope contract)
- `src/code_forge/llm_invoke.py` L62-74 -- `_REVIEW_ENVELOPE_KEYS` and caller map
- `src/code_forge/llm_invoke.py` L107-151 -- `_extract_json_from_text` with
  `expected_keys` parameter

### gate.yaml schema validation
- `src/code_forge/gate_check.py` -- existing schema validation (extend for daemon_state)

### Eval corpus
- `tests/eval/corpus/corpus.yaml` -- manifest (add E9 entry)
- `tests/eval/corpus/diffs/E7-killswitch-reprobe.diff` -- Bucket 1 RUNTIME Q4 diff
  (different from E9: E7 tests Q4, E9 tests DaemonState axis)

### Brief (origin of this phase)
- `/tmp/draft_20260613_bug3_bucket1_bucket3_execution.md` -- main-session brief with
  6 mandatory decisions and scope honesty requirement

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `AdvisoryFinding` dataclass: id, axis, file, line_range, description, attribution --
  sufficient for all daemon state findings (D-02b confirmed)
- `AxisRunner` Protocol: is_advisory, run(diff_text, repo_root), infra_errors --
  DaemonStateRunner implements this directly
- `llm_invoke` with `expected_keys`: F1/F2 fix already supports per-caller envelope
  contract (D-07 confirmed by smoke test)

### Established Patterns
- Advisory runner registration: cli.py L1516 `advisory_runners=[..., runner]` --
  DaemonStateRunner inserts AFTER RuntimeRunner (ordering guarantee for D-01d)
- machine.py `hasattr` injection: `source_files` and `registry` injected at L979-985 --
  same pattern for injecting `runtime_runner` reference into DaemonStateRunner
- RUNTIME constant + SKILL.md mirror + drift test (D-10 pattern): replicate for
  DaemonState (D-05)

### Integration Points
- `RuntimeRunner.last_surfaces` (NEW): must be added to runtime.py; DaemonStateRunner
  reads this via machine.py injection
- `gate_check.py`: extend validate_gate_yaml to parse `daemon_state` section
- `cli.py L1493-1516`: add DaemonStateRunner to advisory_runners list after RuntimeRunner
- `machine.py _run_advisory_axes`: no changes needed (generic loop handles new runners)

</code_context>

<specifics>
## Specific Ideas

- The killswitch self-lock bug (surflare-watchdog) is the canonical example:
  `activate_killswitch` sets nftables mark 0xff on all outbound, then immediately
  calls `check_vpn_health` which needs outbound connectivity for probes. The mark
  blocks the probes -> VPN stays down -> killswitch stays active -> permanent lockout.
- Static conflict rule for this case: `{subsystem: "killswitch", mutates:
  "inet filter output mark", interferes_with: "check_vpn_health outbound probes"}`
- The axis should produce findings like: "[killswitch] sets nft mark 0xff on
  inet filter output; [check_vpn_health] sends outbound probe packets -> probes
  blocked by mark -> permanent lockout"

</specifics>

<deferred>
## Deferred Ideas

- Phase 22 graph integration: when graph triage ships, DaemonStateRunner could use
  graph queries to SUPPLEMENT grep with caller/callee context. The graph models
  code-dependency edges, not shared-external-state co-occurrence, so it complements
  rather than replaces the grep-over-state-strings substrate. Not a hard dependency
  -- grep works independently.
- Daemon process monitoring: detecting whether a daemon is actually running (not just
  reviewing its code) is out of scope -- that's operational monitoring, not code review.
- Multi-repo daemon state analysis: when two repos contain interacting daemons (e.g.,
  OVS kernel + userspace), the axis only sees one repo at a time. Cross-repo support
  would need code-review-graph's cross-repo registry.

</deferred>

---

*Phase: 23-daemon-state*
*Context gathered: 2026-06-14*
