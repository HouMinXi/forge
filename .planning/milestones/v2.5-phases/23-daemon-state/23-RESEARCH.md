# Phase 23: Daemon State - Research

**Researched:** 2026-06-14
**Domain:** Cross-subsystem state-conflict detection (advisory axis for code review)
**Confidence:** HIGH

## Summary

Phase 23 implements REVIEW-STATE-01: a DaemonStateRunner advisory axis that detects
when daemon/service code mutates shared external state (nftables marks, routing rules,
locks, PID files) that another concurrently-active subsystem depends on. The canonical
consumer is the surflare-watchdog killswitch self-lock bug where `activate_killswitch`
sets nft mark 0xff blocking all outbound, then immediately calls `check_vpn_health`
which needs outbound connectivity.

The codebase already has a mature advisory axis infrastructure: AdvisoryFinding
dataclass, AxisRunner Protocol, machine.py generic dispatch loop, and three working
axes (TaintRunner, RuntimeRunner, LegacyRunner). DaemonStateRunner follows the
RuntimeRunner structural pattern (LLM-based analysis, constant question mirrored in
SKILL.md, drift test) with two key additions: (1) a two-step LLM invocation (Q1 state
enumeration, then grep, then Q2+Q3 conflict analysis), and (2) gate.yaml schema
extension for opt-in daemon_state configuration with static conflict rules.

**Primary recommendation:** Follow RuntimeRunner's structural pattern exactly. The
only genuinely new engineering is the two-step LLM call with grep bridge, the
gate.yaml daemon_state schema validation, and the RuntimeRunner.last_surfaces
cross-axis data sharing field.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** Hybrid detection -- gate.yaml explicit declaration takes priority;
  diff content heuristic (keyword match) serves as fallback.
- **D-01a:** Narrow + extensible keyword set. Default keywords: nft, iptables,
  ip-route, systemctl, firewall-cmd, tc. gate.yaml `daemon_state.patterns` allows
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
- **D-03:** Analytical chain-breaking style. Three questions decompose the conflict
  chain: enumerate state -> find shared readers -> describe failure scenario.
- **D-03a:** Exact wording locked (hardcoded as constant).
- **D-03b (revised):** Two-step LLM call. Step 1: Q1 alone (enumerate state) ->
  extract keywords -> grep repo. Step 2: Q2+Q3 with grep context injected.
- **D-04:** grep priority + graph optional enhancement. Default: grep repo for
  Q1 output keywords. If Phase 22 graph is available, use graph results instead
  (more precise). Phase 22 is NOT a hard dependency -- Phase 23 can ship
  independently with grep-only substrate.
- **D-04a:** grep keywords from LLM Q1 output. Two-step flow: Q1 returns state
  items -> extract identifiers (e.g., "mark 0xff", "nft add rule inet filter") ->
  grep these in repo.
- **D-04b:** Relevance-ranked grep results. Sort by hit count per file, take
  top-K files (K=5), extract full function context around each match. Inject into
  Q2+Q3 prompt.
- **D-05:** SKILL.md mirror + D-10 drift test. Follow Phase 20 RUNTIME pattern:
  DaemonState questions hardcoded as a constant, mirrored verbatim in code-forge
  SKILL.md, drift test asserts equality.
- **D-06:** E8 eval corpus entry. Add after implementation, once output format is
  confirmed by smoke test. Killswitch+mark scenario, expect axis to activate and
  report nftables mark conflict.
- **D-07:** Q1 envelope key = `frozenset({"external_state"})` for expected_keys.
- **D-08:** grep input sanitization. All grep subprocess calls must use
  `subprocess.run` with list arguments (no `shell=True`). LLM output keywords
  go directly into the argument list, never through shell expansion.

### Claude's Discretion
None specified -- all implementation areas are locked.

### Deferred Ideas (OUT OF SCOPE)
- Phase 22 graph integration (deferred to when/if graph triage ships)
- Daemon process monitoring (operational monitoring, not code review)
- Multi-repo daemon state analysis (requires cross-repo registry)

</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| REVIEW-STATE-01 | Cross-subsystem state-conflict advisory axis detects daemon/service code mutating shared external state that another subsystem depends on | All research findings below: AxisRunner Protocol, two-step LLM pattern, gate.yaml extension, grep substrate, RuntimeRunner.last_surfaces cross-axis data |

</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Daemon detection (heuristic) | Code Review Pipeline | -- | Diff keyword matching is a pure string operation on the diff text |
| gate.yaml daemon_state parsing | Code Review Pipeline | -- | YAML schema validation, same tier as existing gate_check.py |
| Two-step LLM invocation | Code Review Pipeline | LLM Backend | Pipeline owns orchestration; backend is the LLM service |
| grep context extraction | Code Review Pipeline | OS/Filesystem | subprocess.run grep against repo files |
| Static conflict rule matching | Code Review Pipeline | -- | In-memory triplet matching against gate.yaml declarations |
| Cross-axis data (last_surfaces) | Code Review Pipeline | -- | In-process attribute sharing between advisory runners |
| SKILL.md mirror | Documentation | -- | Verbatim text copy for inline-outlet users |
| E8 eval corpus | Test Infrastructure | -- | Test fixture + corpus manifest entry |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| PyYAML | 6.0.2 | gate.yaml parsing | Already a project dependency (gate_check.py uses yaml.safe_load) [VERIFIED: pyproject.toml] |
| subprocess (stdlib) | N/A | grep invocation | D-08 mandates subprocess.run with list args; already used in taint.py and gate_check.py [VERIFIED: codebase] |
| json (stdlib) | N/A | LLM response parsing | Already used by llm_invoke.py, runtime.py [VERIFIED: codebase] |
| re (stdlib) | N/A | Keyword extraction from LLM Q1 output | Already used by taint.py for pattern matching [VERIFIED: codebase] |

### Supporting
No new external dependencies required. All needed libraries are already in the project.

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| subprocess grep | ripgrep (rg) | Faster but adds external binary dependency; grep is universally available |
| Manual YAML validation | jsonschema/pydantic | Over-engineering for 5 fields; existing gate_check.py uses manual validation |

**Installation:**
No new packages to install. All dependencies are already present.

## Architecture Patterns

### System Architecture Diagram

```
  gate.yaml (daemon_state section)
       |
       v
  +---------------------------+
  | DaemonStateRunner.run()   |
  |                           |
  |  1. Check gate.yaml       |---> gate_check.py validates daemon_state schema
  |     daemon_state enabled?  |
  |     |                     |
  |     +-- NO: heuristic     |---> Keyword scan diff -> one-line advisory if match
  |     |       fallback only  |
  |     +-- YES: full axis    |
  |                           |
  |  2. Read RuntimeRunner    |<--- machine.py injection (hasattr pattern)
  |     .last_surfaces        |     RuntimeRunner runs FIRST in advisory_runners
  |     |                     |
  |     +-- available: enrich |
  |     +-- skipped: fallback |---> gate.yaml subsystems + diff keywords
  |                           |
  |  3. Static conflict rules |<--- gate.yaml daemon_state.conflicts[]
  |     {subsystem, mutates,  |     triplet matching against diff content
  |      interferes_with}     |
  |                           |
  |  4. LLM Step 1: Q1        |---> llm_invoke(Q1_prompt, expected_keys=
  |     "enumerate external   |     frozenset({"external_state"}))
  |      state in diff"       |
  |            |              |
  |            v              |
  |  5. Extract keywords      |---> Parse LLM JSON response
  |     from Q1 output        |     Extract identifiers from external_state list
  |            |              |
  |            v              |
  |  6. grep repo for         |---> subprocess.run(["grep","-rn",kw,repo])
  |     Q1 keywords           |     Sort by hit count, top-K=5 files
  |     (sanitized, no shell) |     Extract function context around matches
  |            |              |
  |            v              |
  |  7. LLM Step 2: Q2+Q3    |---> llm_invoke(Q2Q3_prompt + grep_context,
  |     "find shared readers" |     expected_keys=frozenset({"conflicts"}))
  |     "describe failure"    |
  |            |              |
  |            v              |
  |  8. Merge: static rules   |
  |     + LLM-discovered      |
  |     conflicts              |
  |            |              |
  |            v              |
  |  9. Emit AdvisoryFinding  |---> axis="DAEMON-STATE", never blocks
  |     list                  |
  +---------------------------+
       |
       v
  machine.py._run_advisory_axes() collects findings
  machine.py._serialize_advisories() writes to advisory-findings.json
```

### Recommended Project Structure

```
src/code_forge/
+-- daemon_state.py          # DaemonStateRunner + constants + helpers
+-- runtime.py               # (MODIFIED: add self.last_surfaces field)
+-- gate_check.py            # (MODIFIED: validate daemon_state section)
+-- skills/code-forge/SKILL.md  # (MODIFIED: add Daemon State Axis section)
+-- cli.py                   # (MODIFIED: add DaemonStateRunner to advisory_runners)

tests/
+-- test_daemon_state.py     # Unit tests for DaemonStateRunner
+-- test_daemon_state_drift.py  # D-10 drift test (constant vs SKILL.md)
+-- eval/corpus/
    +-- corpus.yaml           # (MODIFIED: add E8 entry)
    +-- diffs/E8-killswitch-mark-conflict.diff  # E8 diff fixture
```

### Pattern 1: AxisRunner Protocol Implementation (follow RuntimeRunner)

**What:** Each advisory axis is a class with `is_advisory` property and
`run(diff_text, repo_root) -> list[AdvisoryFinding]` method.

**When to use:** Every new advisory axis.

**Example:**
```python
# Source: src/code_forge/runtime.py (verified from codebase read)
class DaemonStateRunner:
    """Advisory axis: cross-subsystem state-conflict detection."""

    def __init__(self, backend=None) -> None:
        self.source_files: Optional[list[Path]] = None
        self.infra_errors: list[str] = []
        self._backend = backend
        # Cross-axis data: set by machine.py injection
        self._runtime_runner: Optional[object] = None

    @property
    def is_advisory(self) -> bool:
        return True

    def run(self, diff_text: str, repo_root: Path) -> list[AdvisoryFinding]:
        self.infra_errors.clear()
        if not diff_text or not diff_text.strip():
            return []
        # ... axis logic ...
```

### Pattern 2: machine.py hasattr Injection

**What:** machine.py `_run_advisory_axes` uses `hasattr` to inject data into
runners that support optional fields. This is the established pattern for
passing source_files, registry, and (new) runtime_runner references.

**When to use:** When a runner needs data from the StateMachine or from
another runner.

**Example:**
```python
# Source: src/code_forge/machine.py L979-985 (verified from codebase read)
for runner in self.advisory_runners:
    if hasattr(runner, "source_files"):
        runner.source_files = list(self.resolved_review.source_files)
    if hasattr(runner, "registry"):
        runner.registry = self.registry
# NEW for Phase 23: inject runtime_runner reference
# DaemonStateRunner reads RuntimeRunner.last_surfaces (D-01d)
```

### Pattern 3: Two-Step LLM with grep Bridge

**What:** First LLM call extracts structured metadata (state items). Keywords
from the metadata drive a grep search. Second LLM call uses grep results as
context for deeper analysis.

**When to use:** When the LLM needs context beyond the diff to answer a
question, but the relevant files are unknown until the LLM identifies what
to look for.

**Example:**
```python
# Step 1: Q1 enumerates state
result1 = llm_invoke(
    q1_prompt,
    backend=self._backend,
    expected_keys=frozenset({"external_state"}),
)
state_items = result1.content.get("external_state", [])

# Extract grep keywords from state items
keywords = _extract_grep_keywords(state_items)

# grep repo for each keyword (D-08: list args, no shell=True)
grep_context = _grep_repo(keywords, repo_root, top_k=5)

# Step 2: Q2+Q3 with grep context
q2q3_prompt = Q2Q3_TEMPLATE.replace("{grep_context}", grep_context)
                            .replace("{diff_text}", diff_text)
result2 = llm_invoke(
    q2q3_prompt,
    backend=self._backend,
    expected_keys=frozenset({"conflicts"}),
)
```

### Pattern 4: gate.yaml Schema Extension

**What:** Extend `load_gate_config` in gate_check.py to validate a new
optional top-level section. Follow the existing `presubmit` and `non_ascii`
validation patterns.

**When to use:** Adding new opt-in features configured via gate.yaml.

**Example:**
```python
# Source: src/code_forge/gate_check.py L96-117 (verified from codebase read)
# Existing pattern for optional section validation:
if "non_ascii" in data:
    if data["non_ascii"] not in ("ai-smell", "strict"):
        raise ValueError(...)

# New for daemon_state:
if "daemon_state" in data:
    validate_daemon_state(data["daemon_state"])
```

### Pattern 5: Constant + SKILL.md Mirror + Drift Test

**What:** Hardcode the question constant in the module. Copy verbatim into
SKILL.md. A drift test asserts the constant appears as a substring in SKILL.md.

**When to use:** Any axis with a canonical question that inline-outlet users
must ask identically.

**Example:**
```python
# Source: tests/test_runtime_drift.py (verified from codebase read)
def test_daemon_state_question_in_skill_md() -> None:
    skill_md = _skill_md_path()
    assert skill_md.exists()
    content = skill_md.read_text(encoding="utf-8")
    assert DAEMON_STATE_Q1 in content, (
        "DAEMON_STATE_Q1 not found verbatim in %s\n"
        "D-10 drift detected." % skill_md
    )
```

### Anti-Patterns to Avoid

- **Widening AxisRunner.run() signature:** The protocol is intentionally narrow
  (diff_text, repo_root only). Cross-axis data goes through hasattr injection,
  not additional parameters. [VERIFIED: advisory.py docstring L12-16]
- **shell=True in grep calls:** D-08 mandates subprocess.run with list args.
  LLM output could contain shell metacharacters. [VERIFIED: CONTEXT.md D-08]
- **Blocking on advisory findings:** Advisory axes NEVER block verdict, NEVER
  reset cycle counter. This is a founding principle. [VERIFIED: advisory.py L1-4]
- **Modifying _REVIEW_ENVELOPE_KEYS:** DaemonStateRunner uses its own
  expected_keys (`frozenset({"external_state"})` for Q1, separate keys for Q2Q3).
  Do NOT add these to the global default. [VERIFIED: llm_invoke.py L64-74 comment]

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| YAML config parsing | Custom parser | yaml.safe_load + manual validation (existing pattern) | gate_check.py already does this; consistency matters more than elegance |
| JSON envelope extraction | Custom JSON scanner | llm_invoke with expected_keys parameter | F1/F2 fix handles edge cases (prose before JSON, array wrapping) |
| Advisory finding construction | New dataclass | AdvisoryFinding(id, axis, file, line_range, description, attribution) | D-02b: no dataclass extension, use existing fields |
| Cross-runner data sharing | Custom event system | hasattr injection in machine.py._run_advisory_axes | Established pattern; machine.py L979-985 already does this for source_files/registry |
| Diff keyword matching | AST parsing | Simple string containment on diff lines | Default keywords (nft, iptables, etc.) are unambiguous enough for heuristic detection |

**Key insight:** The advisory axis infrastructure is mature. DaemonStateRunner's
novelty is the two-step LLM call with grep bridge and the gate.yaml schema
extension -- everything else is direct pattern reuse from RuntimeRunner.

## Common Pitfalls

### Pitfall 1: RuntimeRunner.last_surfaces Not Stored

**What goes wrong:** DaemonStateRunner reads `runtime_runner.last_surfaces` but
RuntimeRunner.run() currently uses surfaces as a local variable (line 332) and
never stores it on `self`.
**Why it happens:** RuntimeRunner was designed before cross-axis data sharing
was needed.
**How to avoid:** Add `self.last_surfaces: list[str] = []` to RuntimeRunner.__init__
and `self.last_surfaces = surfaces` after `_parse_llm_response()` in run().
**Warning signs:** DaemonStateRunner always falls back to heuristic because
`last_surfaces` is empty or missing.

### Pitfall 2: Advisory Runner Ordering

**What goes wrong:** DaemonStateRunner reads RuntimeRunner.last_surfaces, but
machine.py iterates advisory_runners sequentially. If DaemonStateRunner runs
BEFORE RuntimeRunner, last_surfaces is empty.
**Why it happens:** cli.py L1516 constructs the list. Order matters.
**How to avoid:** DaemonStateRunner MUST appear AFTER RuntimeRunner in the
`advisory_runners` list in cli.py. Current order is [taint, runtime, legacy].
DaemonState goes after runtime: [taint, runtime, daemon_state, legacy].
**Warning signs:** last_surfaces always empty despite RUNTIME axis producing
surfaces.

### Pitfall 3: LLM Output as grep Input (Injection Risk)

**What goes wrong:** LLM Q1 returns free-text state items. If these are passed
through shell expansion, LLM-crafted strings could execute arbitrary commands.
**Why it happens:** Naive `os.system("grep " + keyword)` or `shell=True`.
**How to avoid:** D-08: always use `subprocess.run(["grep", "-rn", keyword, path])`.
The keyword goes into the argument list, not through shell parsing.
**Warning signs:** Any use of shell=True, os.system, or string concatenation
for subprocess commands.

### Pitfall 4: Q1 expected_keys Collision with Default

**What goes wrong:** Q1 returns `{"external_state": [...]}`. If expected_keys
is not set, `_extract_json_from_text` uses the default
`_REVIEW_ENVELOPE_KEYS = {"findings", "code_excerpts", "surfaces"}` which
does NOT overlap with `{"external_state"}`. The extractor returns None.
**Why it happens:** Missing explicit expected_keys parameter.
**How to avoid:** D-07: pass `expected_keys=frozenset({"external_state"})` to
llm_invoke for the Q1 call. Smoke test already verified this works.
**Warning signs:** Q1 always returns None / parse error despite valid LLM output.

### Pitfall 5: gate.yaml Validation Order

**What goes wrong:** daemon_state validation runs but the `daemon_state` section
references a `conflicts_file` that doesn't exist. Validation passes (file
existence is not checked at parse time), but runtime fails.
**Why it happens:** gate_check.py validates schema, not file existence.
**How to avoid:** Validate conflicts_file existence at axis runtime (in
DaemonStateRunner.run()), not at gate.yaml parse time. Log a warning if the
file is referenced but missing, and continue with inline rules only.
**Warning signs:** FileNotFoundError during axis execution, not during config load.

### Pitfall 6: grep Result Token Budget

**What goes wrong:** grep on a large repo returns thousands of lines for common
keywords like "mark" or "rule". Injecting all of this into the Q2+Q3 prompt
blows the LLM context window.
**Why it happens:** No truncation strategy for grep results.
**How to avoid:** D-04b: sort by hit count per file, take top-K=5 files, extract
function context (surrounding lines) around each match. Cap total injected
context at a reasonable token budget (e.g., 2000 lines).
**Warning signs:** LLM timeout or truncated response on Q2+Q3 call.

## Code Examples

Verified patterns from the codebase:

### RuntimeRunner.last_surfaces Addition

```python
# Source: src/code_forge/runtime.py (MODIFICATION needed)
# In RuntimeRunner.__init__:
def __init__(self, backend=None) -> None:
    self.source_files: Optional[list[Path]] = None
    self.infra_errors: list[str] = []
    self._backend = backend
    self.last_surfaces: list[str] = []  # NEW: D-01d cross-axis data

# In RuntimeRunner.run(), after _parse_llm_response():
    surfaces, llm_findings = _parse_llm_response(result.content)
    self.last_surfaces = surfaces  # NEW: store for DaemonStateRunner
```

### gate_check.py daemon_state Validation

```python
# Source: pattern from gate_check.py L96-117 (extend with new section)
def validate_daemon_state(section: object) -> None:
    """Validate daemon_state section of gate.yaml."""
    if not isinstance(section, dict):
        raise ValueError("'daemon_state' must be a mapping")
    if "enabled" in section and not isinstance(section["enabled"], bool):
        raise ValueError("'daemon_state.enabled' must be a boolean")
    if "subsystems" in section:
        if not isinstance(section["subsystems"], list):
            raise ValueError("'daemon_state.subsystems' must be a list")
        for s in section["subsystems"]:
            if not isinstance(s, str):
                raise ValueError("'daemon_state.subsystems' elements must be strings")
    if "patterns" in section:
        if not isinstance(section["patterns"], list):
            raise ValueError("'daemon_state.patterns' elements must be a list")
    if "conflicts" in section:
        if not isinstance(section["conflicts"], list):
            raise ValueError("'daemon_state.conflicts' must be a list")
        for idx, c in enumerate(section["conflicts"]):
            if not isinstance(c, dict):
                raise ValueError("daemon_state.conflicts[%d] must be a mapping" % idx)
            for req_key in ("subsystem", "mutates", "interferes_with"):
                if req_key not in c:
                    raise ValueError(
                        "daemon_state.conflicts[%d] missing required '%s'" % (idx, req_key)
                    )
                if not isinstance(c[req_key], str):
                    raise ValueError(
                        "daemon_state.conflicts[%d].%s must be a string" % (idx, req_key)
                    )
    if "conflicts_file" in section:
        if not isinstance(section["conflicts_file"], str):
            raise ValueError("'daemon_state.conflicts_file' must be a string")
```

### grep with Sanitized Input (D-08)

```python
# Source: pattern from taint.py subprocess usage + D-08 decision
def _grep_repo(
    keywords: list[str],
    repo_root: Path,
    top_k: int = 5,
    context_lines: int = 5,
) -> str:
    """Grep repo for keywords, return relevance-ranked context.

    D-08: subprocess.run with list args, no shell=True.
    D-04b: sort by hit count, take top-K files, extract context.
    """
    file_hits: dict[str, int] = {}
    for keyword in keywords:
        if not keyword or not keyword.strip():
            continue
        try:
            result = subprocess.run(
                ["grep", "-rn", "--include=*.py", "--include=*.sh",
                 "--include=*.yaml", "--include=*.yml",
                 keyword.strip(), str(repo_root)],
                capture_output=True, text=True, timeout=10,
            )
            for line in result.stdout.splitlines():
                # Extract file path (before first colon)
                parts = line.split(":", 2)
                if len(parts) >= 2:
                    fpath = parts[0]
                    file_hits[fpath] = file_hits.get(fpath, 0) + 1
        except (subprocess.TimeoutExpired, OSError):
            continue

    # Sort by hit count, take top-K
    ranked = sorted(file_hits.items(), key=lambda x: x[1], reverse=True)[:top_k]

    # Extract context around matches
    context_parts: list[str] = []
    for fpath, _ in ranked:
        for keyword in keywords:
            if not keyword or not keyword.strip():
                continue
            try:
                result = subprocess.run(
                    ["grep", "-n", "-C", str(context_lines),
                     keyword.strip(), fpath],
                    capture_output=True, text=True, timeout=5,
                )
                if result.stdout.strip():
                    context_parts.append("--- %s ---\n%s" % (fpath, result.stdout))
            except (subprocess.TimeoutExpired, OSError):
                continue

    return "\n".join(context_parts)
```

### machine.py Injection for Cross-Axis Reference

```python
# Source: src/code_forge/machine.py L979-985 (extend existing pattern)
# In _run_advisory_axes, AFTER existing hasattr injections:
for runner in self.advisory_runners:
    if hasattr(runner, "source_files"):
        runner.source_files = list(self.resolved_review.source_files)
    if hasattr(runner, "registry"):
        runner.registry = self.registry
    # NEW: inject runtime_runner reference for DaemonStateRunner
    if hasattr(runner, "_runtime_runner"):
        # Find the RuntimeRunner in the list
        for other in self.advisory_runners:
            if type(other).__name__ == "RuntimeRunner":
                runner._runtime_runner = other
                break
```

### cli.py Advisory Runner Registration (D-01d ordering)

```python
# Source: src/code_forge/cli.py L1493-1516 (extend)
from .taint import TaintRunner
from .runtime import RuntimeRunner
from .legacy import LegacyRunner
from .daemon_state import DaemonStateRunner  # NEW

_taint_runner = TaintRunner()
_runtime_runner = RuntimeRunner(backend=backend)
_daemon_state_runner = DaemonStateRunner(backend=backend)  # NEW
_legacy_runner = LegacyRunner()

# Order matters: RuntimeRunner BEFORE DaemonStateRunner (D-01d)
advisory_runners=[_taint_runner, _runtime_runner, _daemon_state_runner, _legacy_runner],
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Single-pass LLM review | Multi-step LLM with tool-use bridge | 2025-2026 | Agentic code review tools (CodeRabbit, Greptile) use multi-step analysis where first pass identifies areas of concern, then subsequent passes dive deeper with context [ASSUMED] |
| Monolithic review question | Decomposed analytical questions | Phase 20 (2026-06) | RUNTIME Q4 was the first decomposed question in forge; DaemonState extends this to a full two-step pattern [VERIFIED: codebase] |
| Advisory axes independent | Cross-axis data sharing | Phase 23 (new) | RuntimeRunner.last_surfaces is the first cross-axis data channel; establishes the hasattr injection precedent for future axes [VERIFIED: CONTEXT.md D-01d] |

**Deprecated/outdated:**
- None relevant to this phase. The advisory infrastructure is recent (Phase 20-21,
  June 2026) and stable.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | grep -C (context lines) is sufficient to extract function-level context around matches | Code Examples (grep) | grep context may cross function boundaries or miss relevant code; planner may need to add smarter context extraction |
| A2 | Top-K=5 files is a reasonable default for grep relevance ranking | Architecture Patterns | Too few files may miss relevant subsystems; too many blows token budget. User can tune via gate.yaml patterns |
| A3 | Q2+Q3 combined in a single LLM call (step 2) is sufficient; no need for Q2 and Q3 as separate calls | Architecture Patterns | Splitting Q2 and Q3 would add latency and cost; combining is the locked decision from D-03b |
| A4 | `--include=*.py --include=*.sh --include=*.yaml --include=*.yml` covers the relevant file types for daemon state detection | Code Examples (grep) | Projects using other languages (Go, Rust, C) would miss matches; could be extended via gate.yaml patterns |
| A5 | context_lines=5 for grep -C provides enough surrounding code | Code Examples (grep) | May need 10-15 lines for large functions; configurable parameter |

## Open Questions (RESOLVED)

1. **Q2+Q3 expected_keys value**
   - What we know: Q1 uses `frozenset({"external_state"})` (D-07). Q2+Q3 returns conflict analysis.
   - What's unclear: The exact envelope key for Q2+Q3 response (e.g., `{"conflicts"}` or `{"findings"}`)
   - Recommendation: Use `frozenset({"conflicts"})` for Q2+Q3 to avoid collision with the default `_REVIEW_ENVELOPE_KEYS`. Update the caller map comment in llm_invoke.py.

2. **Injecting RuntimeRunner reference: isinstance vs type name check**
   - What we know: machine.py uses `isinstance(r, _RuntimeRunner)` in _display_smoke_status (line 1034) via a local import.
   - What's unclear: Whether the injection loop should also do isinstance or the simpler hasattr pattern.
   - Recommendation: Use isinstance with a local import (matching existing pattern at L1030-1034). This is type-safe and avoids string-based type checking.

3. **E8 diff fixture content**
   - What we know: E7 is the killswitch-reprobe diff. E8 is for DaemonState axis specifically.
   - What's unclear: Whether E8 should reuse the E7 diff or create a distinct scenario.
   - Recommendation: Create a distinct E8 diff that specifically demonstrates cross-subsystem state conflict (e.g., nft mark + health check, as described in CONTEXT.md specifics). E7 tests RUNTIME Q4; E8 tests DaemonState axis.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.x |
| Config file | pyproject.toml [tool.pytest.ini_options] |
| Quick run command | `python3 -m pytest tests/test_daemon_state.py tests/test_daemon_state_drift.py -x` |
| Full suite command | `python3 -m pytest -x` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| STATE-01a | AxisRunner Protocol conformance | unit | `pytest tests/test_daemon_state.py::test_is_advisory -x` | Wave 0 |
| STATE-01b | Empty diff returns [] | unit | `pytest tests/test_daemon_state.py::test_empty_diff -x` | Wave 0 |
| STATE-01c | Heuristic fallback (no gate.yaml) | unit | `pytest tests/test_daemon_state.py::test_heuristic_fallback -x` | Wave 0 |
| STATE-01d | Two-step LLM call (Q1 -> grep -> Q2Q3) | unit | `pytest tests/test_daemon_state.py::test_two_step_llm -x` | Wave 0 |
| STATE-01e | Static conflict rule matching | unit | `pytest tests/test_daemon_state.py::test_static_rules -x` | Wave 0 |
| STATE-01f | RuntimeRunner.last_surfaces storage | unit | `pytest tests/test_runtime.py::test_last_surfaces_stored -x` | Wave 0 |
| STATE-01g | gate.yaml daemon_state validation | unit | `pytest tests/test_gate_check.py::test_daemon_state_validation -x` | Wave 0 |
| STATE-01h | grep sanitization (D-08) | unit | `pytest tests/test_daemon_state.py::test_grep_sanitization -x` | Wave 0 |
| STATE-01i | SKILL.md drift test | unit | `pytest tests/test_daemon_state_drift.py -x` | Wave 0 |
| STATE-01j | LLM failure -> SKIPPED finding | unit | `pytest tests/test_daemon_state.py::test_llm_failure_skipped -x` | Wave 0 |
| STATE-01k | Ordering: DaemonState after Runtime | unit | `pytest tests/test_daemon_state.py::test_ordering -x` | Wave 0 |
| STATE-01l | E8 eval corpus entry | integration | `pytest tests/test_eval_runner.py -x -k E8` | Wave 0 |

### Sampling Rate
- **Per task commit:** `python3 -m pytest tests/test_daemon_state.py tests/test_daemon_state_drift.py -x`
- **Per wave merge:** `python3 -m pytest -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_daemon_state.py` -- covers STATE-01a through STATE-01k
- [ ] `tests/test_daemon_state_drift.py` -- covers STATE-01i
- [ ] New test in `tests/test_runtime.py` -- covers STATE-01f (last_surfaces stored)
- [ ] New test in `tests/test_gate_check.py` -- covers STATE-01g (daemon_state validation)
- [ ] `tests/eval/corpus/diffs/E8-killswitch-mark-conflict.diff` -- covers STATE-01l

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | -- |
| V3 Session Management | no | -- |
| V4 Access Control | no | -- |
| V5 Input Validation | yes | subprocess.run list args (D-08); no shell=True; LLM output treated as untrusted input |
| V6 Cryptography | no | -- |

### Known Threat Patterns for This Phase

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| LLM output -> shell injection via grep | Elevation of Privilege | D-08: subprocess.run with list args, never shell=True [VERIFIED: CONTEXT.md] |
| Malicious gate.yaml daemon_state content | Tampering | Schema validation in gate_check.py; conflicts_file path validated at runtime [VERIFIED: gate_check.py pattern] |
| Token budget exhaustion via grep flood | Denial of Service | D-04b: top-K=5 cap + timeout on subprocess.run [VERIFIED: CONTEXT.md] |

## Cross-Phase Finding (from Phase 22 Research, 2026-06-14)

Phase 22 smoke testing revealed that code-review-graph's graph.db has a
**short-name edge resolution problem**: `edges.target_qualified` stores bare
symbol names (e.g., `run`) rather than fully qualified names. Common daemon
function names (start, stop, restart, reload, run) would produce massive
false positives.

**Impact on D-04 (graph optional enhancement):**
- sem CLI (`sem impact`): PRECISE, fully qualified entity IDs. Phase 23 CAN
  use sem output for cross-subsystem dependency identification if sem is
  installed. Better than grep.
- graph.db: Phase 23 should STICK WITH GREP as the default substrate.
  graph.db short-name edges would produce false matches for common daemon
  functions.

This confirms D-04's original design: grep-first, graph-optional. The
"optional" path should be sem-based (when available), not graph.db-based.

## Sources

### Primary (HIGH confidence)
- `src/code_forge/runtime.py` -- RuntimeRunner pattern, RUNTIME_LIFECYCLE_QUESTION constant
- `src/code_forge/legacy.py` -- LegacyRunner pattern, machine.py injection usage
- `src/code_forge/advisory.py` -- AdvisoryFinding dataclass, AxisRunner Protocol
- `src/code_forge/machine.py` L969-996 -- _run_advisory_axes dispatch loop, hasattr injection
- `src/code_forge/gate_check.py` -- gate.yaml schema validation patterns
- `src/code_forge/llm_invoke.py` L62-74, 107-151 -- expected_keys envelope contract
- `src/code_forge/cli.py` L1493-1516 -- advisory runner registration order
- `src/code_forge/taint.py` -- subprocess.run usage pattern
- `tests/test_runtime_drift.py` -- D-10 drift test pattern
- `.planning/phases/23-daemon-state/23-CONTEXT.md` -- all locked decisions D-01 through D-08

### Secondary (MEDIUM confidence)
- `.planning/REQUIREMENTS.md` -- REVIEW-STATE-01 requirement definition
- `.planning/ROADMAP.md` L242-256 -- Phase 23 success criteria
- `tests/eval/corpus/corpus.yaml` -- eval corpus manifest structure

### Tertiary (LOW confidence)
- None. All findings are derived from direct codebase reads.

## Project Constraints (from CLAUDE.md)

- Language: All documentation and skill files in English
- No non-ASCII in code: typographic characters (em dash, smart quotes) must be ASCII equivalents
- Dependencies: bash assertion primitives require only jq
- Compatibility: Must work with Claude Code skill discovery (SKILL.md in ~/.claude/skills/)
- Planning files are gitignored -- never committed to main
- Author info: Minxi Hou <houminxi@gmail.com> -- never use AI co-author lines
- Worktree-based workflow required for implementation

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new dependencies, all patterns verified from codebase
- Architecture: HIGH -- direct extension of established RuntimeRunner pattern with 21 locked decisions
- Pitfalls: HIGH -- derived from smoke test results documented in CONTEXT.md and codebase reads

**Research date:** 2026-06-14
**Valid until:** 2026-07-14 (stable; advisory infrastructure is recent and actively maintained)
