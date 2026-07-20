# Phase 15: Reviewer Independence - Research

**Researched:** 2026-06-07
**Domain:** Anti-fabrication structural guarantee for code review pipeline
**Confidence:** HIGH

## Summary

Phase 15 enforces reviewer-not-implementer independence on both Outlet A (subprocess) and Outlet C (subagent) legs of the forge review pipeline. The core mechanism is a fresh `llm_invoke` call per review pass, where each invocation receives only the diff + criteria payload (never the implementer's session context). This is already how Outlet A works via `factories.build_l1_provider()` -- Phase 15 must replicate this for Outlet C by replacing the `NotImplementedError` ceiling at `cli.py:783-784` with a real `llm_invoke`-based `spawn_fn`.

The phase splits into two waves: 15a (core independence contract + conventions-digest slot + same-repo digest + test-assertion review gate + human backstop) and 15b (cross-repo conventions-seed resolver with AGENTS.md parsing, sibling extraction, and caching). No new external packages are needed -- the implementation uses existing `llm_invoke`, `reviewer_json`, `StateMachine`, and `verify` infrastructure. The critical design constraint is D9: A-leg and C-leg CONVERGE on the independence axis, using the same per-pass fresh-invoke mechanism (not cold-Agent-per-pass dispatch, which is a known failure mode per project memories).

**Primary recommendation:** Implement `_subagent_spawn` in `cli.py` as a thin wrapper around `llm_invoke` (same as `build_l1_provider` inner loop), add the conventions-digest slot to the criteria prompt, wire the test-assertion review gate before R1, and add the human backstop checklist to SKILL.md.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Spine invariants:**
- D1: Two legs kept (Outlet A + Outlet C). Phase 15 does NOT pick one. Independence is one contract on both legs.
- D8: Every Phase 15 mechanism feeds the same Phase 14 receipt/verify gate. No new side-channel verify.
- D6: Human at the top. The gate proves coverage, never judgment.

**Architecture:**
- D2: Split orchestration from judgment. Orchestration = deterministic code. Judgment = fresh per-pass model calls.
- D3: Independence = reviewer sees only diff + criteria. Never the implementer's conversation.
- D4: Prefer the failure the gate can catch. Whole-flow rubber-stamp is catchable; cold-agent amok is not.
- D5: Capability floor. Both independence and capability are required.
- D7: Outlet A already independent by construction (build_l1_provider).
- D10: Independence = context-separation (fresh llm_invoke). Model-separation = CONFIG OPTION, not mandate.

**C-leg mechanism:**
- D9: C-leg = fresh llm_invoke per pass through spawn_fn. Same mechanism as A-leg. REJECTED: cold-Agent-per-pass.

**Criteria payload:**
- D11: criteria = diff + post-image + role + dimensions + conventions-digest SLOT.

**Conventions resolver:**
- D12: Pluggable resolver, two modes (dependency + peer/sibling). AGENTS.md preferred curated source.

**Deliverables:**
- D13: Human backstop = explicit one-round hot verify + checklist after independent reviewer completes.
- D14: Test-assertion review = separate gate step BEFORE R1, fresh llm_invoke, not 4th pass.

**Scope:**
- D15: Pass parity OUT OF SCOPE.
- D16: Execution split 15a (core) / 15b (cross-repo resolver).
- D17: v1 custom mapping carries extraction recipe, no universal extractor.

### Claude's Discretion

No discretion areas identified -- all design decisions were locked in the discuss phase.

### Deferred Ideas (OUT OF SCOPE)

- Engine/skill pass count alignment (G6/D15): future phase.
- Cross-repo IMPACT judgment: different axis from naming/idiom.
- Content fabrication defense: reviewer lies about findings. Future phase.
- Feedback learning: forge does not learn from dismissed findings.
- Universal cross-repo extractor: v1 uses custom-mapping recipes (O2).
- Agentic-as-gate: v2.4 thesis boundary.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SHRK-02 | Enforce reviewer-not-implementer at outlet level -- fresh agent per pass with orchestrator-only diff+criteria handoff | SC1 via D9 (spawn_fn -> llm_invoke), SC2 via D11 (criteria payload), SC3 via D3/D10 (context isolation), SC4 via D14 (test-assertion review gate) |
</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Per-pass fresh llm_invoke (SC1) | API / Backend (llm_invoke.py) | CLI (cli.py spawn_fn) | llm_invoke owns the HTTP/subprocess call; cli.py wires it into spawn_fn |
| Criteria payload assembly (SC2) | API / Backend (factories.py) | -- | Prompt construction is backend-tier; reviewer_json validates the response |
| Context isolation enforcement (SC3) | API / Backend (outlet_c.py) | -- | outlet_c.py loop structure guarantees no session state carries between passes |
| Test-assertion review gate (SC4) | CLI (cli.py) | -- | Gate runs before R1 in the CLI dispatch path; produces findings not receipts |
| Conventions-digest slot (D11) | API / Backend (new module) | -- | New conventions.py module; injected into prompt at factories + outlet_c level |
| Cross-repo resolver (D12) | API / Backend (new module) | -- | conventions_resolver.py; reads AGENTS.md, manifests, .code-forge/conventions.yaml |
| Human backstop (D13) | SKILL.md (documentation) | -- | SKILL.md section; no engine code, just protocol specification |
| Receipt/verify integration (D8) | API / Backend (verify.py) | -- | Phase 14 gate already exists; Phase 15 mechanisms feed it unchanged |

## Standard Stack

### Core

No new external libraries required. Phase 15 uses the existing project stack.

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pytest | 9.0.3 | Test framework | Already in use; 1096 tests [VERIFIED: local environment] |
| pyyaml | (installed) | YAML parsing for conventions.yaml + AGENTS.md | Already a project dependency (gate.yaml parsing) [VERIFIED: cli.py imports] |

### Supporting

No new supporting libraries needed.

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| llm_invoke for C-leg spawn | Agent tool (cold-agent-per-pass) | REJECTED by D9: known 65K truncation + hang failure (memories: feedback_forge_review_inline_only, feedback_subagent_hangs) |
| pyyaml for AGENTS.md | Custom regex parser | pyyaml already installed; AGENTS.md will have YAML frontmatter |

## Package Legitimacy Audit

No new packages to install. Phase 15 uses only existing project dependencies.

| Package | Registry | Age | Downloads | Source Repo | slopcheck | Disposition |
|---------|----------|-----|-----------|-------------|-----------|-------------|
| (none) | -- | -- | -- | -- | -- | N/A |

**Packages removed due to slopcheck [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none

## Architecture Patterns

### System Architecture Diagram

```
Diff + Post-Image + Role + Dimensions + Conventions-Digest
     |
     v
[Criteria Payload Assembly]  <--- conventions_resolver.py (15b)
     |                             |
     |                        [Source Resolver]
     |                          1. .code-forge/conventions.yaml
     |                          2. AGENTS.md
     |                          3. Other agent-context files
     |                          4. Package manifests
     |
     +---> [Outlet A: build_l1_provider]
     |         |
     |         +---> llm_invoke(prompt, backend) ---> per pass (qodo/expert/adversarial)
     |         |         (fresh call, no session state)
     |         +---> validate_reviewer_json(response)
     |         +---> (findings, excerpts, Usage, duration) 4-tuple
     |
     +---> [Outlet C: outlet_c.py]
               |
               +---> spawn_fn(pass_name, diff_text) ---> llm_invoke(prompt, backend)
               |         (fresh call, no session state, REPLACES NotImplementedError)
               +---> validate_reviewer_json(raw)
               +---> (findings, excerpts, Usage, duration) 4-tuple
     |
     v
[StateMachine]  (cycle counting, receipt production -- unchanged)
     |
     v
[Test-Assertion Review Gate]  <--- NEW, BEFORE R1
     |    fresh llm_invoke(diff + test files + role=test-assertion-reviewer)
     |    structural guarantee: never the impl/test author
     v
[R1 Test Gate] ---> [R2 Mutation] ---> [R3 E2E]
     |
     v
[Human Backstop]  <--- NEW explicit checklist in SKILL.md
     |    one-round hot verify after independent reviewer
     |    focused by gate's coverage proof (human reviews judgment, not coverage)
     v
[Commit Gate]
```

### Recommended Project Structure

```
src/code_forge/
  factories.py          # MODIFY: add conventions_digest param to prompt in build_l1_provider
  outlet_c.py           # MODIFY: spawn_fn wired to llm_invoke; conventions_digest injected
  cli.py                # MODIFY: replace _subagent_spawn NotImplementedError with real impl;
                        #         add test-assertion review gate before R1
  reviewer_json.py      # UNCHANGED: shared validation for both legs
  machine.py            # UNCHANGED: cycle counting, receipt production
  verify.py             # UNCHANGED: hardened verify gate (Phase 14)
  receipt.py            # UNCHANGED: receipt writer
  llm_invoke.py         # UNCHANGED: per-call fresh invocation
  conventions.py        # NEW (15a): conventions-digest slot + same-repo extractor
  conventions_resolver.py  # NEW (15b): cross-repo source resolver + sibling extraction
skills/code-forge/
  SKILL.md              # MODIFY: add human backstop section (D13)
```

### Pattern 1: Per-Pass Fresh Invocation (Reference Implementation)

**What:** Each review pass gets a fresh `llm_invoke` call with only the criteria payload. No session state carries between passes.

**When to use:** Every review pass on both legs (A and C).

**Example (A-leg reference, from factories.py:236-309):**
```python
# Source: src/code_forge/factories.py (verified by reading the file)
for pass_name, role in pass_configs:
    prompt = (
        "You are a " + role + ". Review this diff.\n"
        'Return JSON: {"findings": [...], "code_excerpts": [...]}\n'
        "\nDiff:\n" + diff_text
    )
    # Each call is a fresh llm_invoke -- no shared session
    result = llm_invoke(prompt, backend=backend)
    response = result.content
    validated = validate_reviewer_json(response)
```

**C-leg implementation must follow this exact pattern:**
```python
# Phase 15 replacement for cli.py:783-784 _subagent_spawn
def _subagent_spawn(pass_name: str, diff_text: str) -> str:
    """Fresh llm_invoke per pass -- D9 convergence with A-leg."""
    role = _PASS_ROLES[pass_name]
    prompt = (
        "You are a " + role + ". Review this diff.\n"
        'Return JSON: {"findings": [...], "code_excerpts": [...]}\n'
        # conventions_digest injected here when available (D11 slot)
        "\nDiff:\n" + diff_text
    )
    result = llm_invoke(prompt, backend=backend)
    # llm_invoke returns LLMResult; extract the JSON content
    content = result.content
    if isinstance(content, dict):
        return json.dumps(content)
    return str(content)
```

### Pattern 2: Conventions-Digest Slot Injection

**What:** The criteria payload includes a slot for a conventions digest. Empty when no resolver source is configured; filled by same-repo (15a) or cross-repo (15b) extractor.

**When to use:** Every review pass prompt on both legs.

**Example:**
```python
# Source: D11 specification from CONTEXT.md
digest = conventions_resolver.get_digest(cwd, backend=backend)
if digest:
    prompt += "\n## Conventions Digest\n" + digest + "\n"
```

### Pattern 3: Test-Assertion Review Gate

**What:** A separate gate step BEFORE R1 that reviews test assertions with a fresh llm_invoke. Structurally guaranteed to never be the impl/test author (D14).

**When to use:** Before R1 test gate in cli.py, when test files are in the diff.

**Example:**
```python
# New gate step in cli.py, after verdict from run_outlet_c / _run_hold_loop
# but before gate-check (R1)
def _run_test_assertion_review(diff_text, test_files, backend):
    prompt = (
        "You are a test-assertion reviewer. "
        "Review these test files for assertion quality.\n"
        "Check: assertion completeness, edge case coverage, "
        "mock accuracy, assertion specificity.\n"
        'Return JSON: {"findings": [...], "code_excerpts": [...]}\n'
        "\nDiff:\n" + diff_text
    )
    result = llm_invoke(prompt, backend=backend)
    return validate_reviewer_json(result.content)
```

### Anti-Patterns to Avoid

- **Judge-and-athlete:** One agent both implements and reviews. Phase 15 makes this structurally impossible by construction. Never pass the implementer's session context to the reviewer.
- **Cold-Agent-per-pass dispatch:** Spawning a separate Claude Code Agent tool per pass. Known failure mode: 65K context truncation, hang at skill loading, cannot orchestrate multi-step review. REJECTED by D9.
- **Shared session across passes:** Passing findings from pass 1 to pass 2. Each pass must see only the diff + criteria, never prior pass results. This is the "cross-pass contamination" anti-pattern.
- **New verify side-channel:** Any independence verification that bypasses the Phase 14 receipt/verify gate. D8 forbids this.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| JSON validation of reviewer output | Custom parser per leg | `reviewer_json.validate_reviewer_json()` | Shared A/C validation; fail-closed on malformed JSON; already handles edge cases (empty findings+excerpts rejection) |
| Cycle counting | Custom counter in outlet_c | `machine.StateMachine` | Reuse via l1_provider 4-tuple; receipt production integrated |
| Receipt writing | Custom receipt format | `receipt.write_receipts()` | Hardened verify (Phase 14) expects this exact format |
| LLM invocation | Raw HTTP calls or subprocess | `llm_invoke.llm_invoke()` | Handles cli/api/vertex dispatch, timeout, signal cleanup, token tracking |
| Fingerprint computation | Custom hash | `reviewer_json._json_to_state_findings()` | Identical fingerprint across both legs ensures dedup works |
| YAML parsing | Custom parser | `pyyaml` (yaml.safe_load) | Already a project dependency; battle-tested |

**Key insight:** Phase 15's power comes from REUSING the existing infrastructure (llm_invoke, reviewer_json, StateMachine, receipt, verify) rather than building parallel systems. The independence contract is realized by how these pieces are WIRED (fresh invocation per pass), not by new verification mechanisms.

## Common Pitfalls

### Pitfall 1: spawn_fn Returning Wrong Type
**What goes wrong:** `outlet_c.py:54` calls `spawn_fn(pass_name, diff_text)` and expects a `str` return (raw JSON). If `llm_invoke` returns `LLMResult` with `content` as a `dict` (parsed JSON), the `str` assertion in `validate_reviewer_json` gets a dict input.
**Why it happens:** `llm_invoke` for `api` backends returns parsed JSON in `content`; `cli` backends return parsed JSON too (the envelope is unwrapped). The raw string is never directly available.
**How to avoid:** `validate_reviewer_json` already accepts both `str` and `dict` (line 18-26 of reviewer_json.py). The spawn_fn should return `json.dumps(content)` if `content` is a dict, or pass the dict directly. Since `validate_reviewer_json` handles both, the cleanest approach is to have spawn_fn return the raw JSON string or dict and let the validator handle it.
**Warning signs:** `TypeError: expected str, got dict` in validate_reviewer_json calls.

### Pitfall 2: Usage/Duration Not Propagated from C-leg
**What goes wrong:** `outlet_c.py:82` returns `Usage()` (zero tokens) and `0.0` duration. The current implementation has no token tracking for C-leg because spawn_fn returns only the response string.
**Why it happens:** The spawn_fn signature `(str, str) -> str` does not carry Usage metadata. The A-leg inner loop accumulates tokens inside `build_l1_provider` because `llm_invoke` is called directly.
**How to avoid:** Option A: Expand spawn_fn return to include usage (breaking change). Option B: Accumulate usage inside `_l1_provider` closure in outlet_c.py by capturing the llm_invoke result before extracting the JSON string. Option B is cleaner because it keeps spawn_fn's signature simple and matches the A-leg pattern where token tracking happens inside the provider closure.
**Warning signs:** C-leg reviews show "0 tokens" in cost output despite making real API calls.

### Pitfall 3: Conventions Digest Leaking Implementer Context
**What goes wrong:** If the conventions digest is derived by asking the current session (which IS the implementer), it inherits the implementer's blind spots.
**Why it happens:** D3 forbids implementer rationale but allows codebase facts. The boundary is: read authoritative code directly, never ask the implementer what conventions are.
**How to avoid:** The digest derivation (conventions_resolver) must run independently: deterministic extraction (grep, AST, symbol table) or a fresh AI pass. Never pass the implementer's session messages to the extractor.
**Warning signs:** Digest contains implementer-specific terminology not found in the sibling codebase.

### Pitfall 4: Test-Assertion Gate Conflated with Static Review Passes
**What goes wrong:** The test-assertion review (D14) is treated as a 4th pass in the three-cycle review, causing cycle counter confusion.
**Why it happens:** Both produce findings via llm_invoke + validate_reviewer_json. The temptation is to reuse the same l1_provider machinery.
**How to avoid:** D14 specifies this is a SEPARATE gate step BEFORE R1, not part of the three-cycle static review. It runs once after the review pipeline completes, produces advisory findings, and does NOT reset the cycle counter.
**Warning signs:** Cycle counter jumps to 4, or test-assertion findings trigger cycle restart.

### Pitfall 5: AGENTS.md Format Assumptions
**What goes wrong:** Parser assumes a specific AGENTS.md format that does not exist as a standard yet.
**Why it happens:** AGENTS.md is described as a "2026 cross-tool standard" but the actual format is not yet widely standardized.
**How to avoid:** Parse conservatively. Look for YAML frontmatter with `related_repos` or `siblings` keys. Fall back to regex extraction of repo paths/URLs from markdown content. The parser must degrade gracefully when AGENTS.md has an unexpected format -- emit empty digest, not crash.
**Warning signs:** `KeyError` or `yaml.YAMLError` on AGENTS.md files from different projects.

## Code Examples

### _subagent_spawn Replacement (cli.py:783-784)

```python
# Source: derived from factories.py:236-284 (A-leg reference) + D9 lock
# This replaces the NotImplementedError at cli.py:783-784

_PASS_ROLES = {
    "qodo": "structural code reviewer: correctness and logic errors",
    "expert": "senior engineer: SOLID, architecture, security",
    "adversarial": "adversarial QE: assume bugs exist",
}

def _subagent_spawn(pass_name: str, diff_text: str) -> str:
    """Fresh llm_invoke per pass. D9 convergence with A-leg."""
    from .llm_invoke import llm_invoke
    role = _PASS_ROLES.get(pass_name, "code reviewer")
    prompt = (
        "You are a " + role + ". Review this diff.\n"
        'Return JSON: {"findings": [{"file": "...", "line": N, '
        '"severity": "P0"|"P1"|"P2"|"P3", '
        '"description": "..."}], '
        '"code_excerpts": [{"file": "...", "start_line": N, '
        '"end_line": M, "content": "..."}]}\n'
        "Each diff hunk MUST have at least one code_excerpt.\n"
        "Even if findings is empty, provide code_excerpts "
        "covering each changed hunk.\n"
        "code_excerpts content must be actual source code lines, "
        "not diff format -- no +/- prefixes, no @@ headers.\n"
        "\nDiff:\n" + diff_text
    )
    result = llm_invoke(prompt, backend=backend)
    content = result.content
    if isinstance(content, dict):
        return json.dumps(content)
    return str(content)
```

### Conventions-Digest Slot (conventions.py -- new module)

```python
# Source: D11 specification + D12 SPEC from CONTEXT.md

def get_same_repo_digest(cwd: Path) -> str:
    """Extract same-repo naming conventions (15a).

    Deterministic extraction: public function/class names,
    naming patterns (prefix frequency), exported API names.
    """
    # Scan Python files for public names
    public_names = []
    for py_file in cwd.rglob("*.py"):
        # ... extract def/class names not starting with _
        pass
    if not public_names:
        return ""
    # Summarize naming patterns
    return "Same-repo conventions:\n" + "\n".join(
        "- %s" % name for name in sorted(set(public_names))[:50]
    )


def get_digest(cwd: Path, backend=None) -> str:
    """Build conventions digest for criteria payload (D11 slot).

    Returns empty string when no resolver source is configured.
    """
    parts = []
    same_repo = get_same_repo_digest(cwd)
    if same_repo:
        parts.append(same_repo)
    # 15b: cross-repo digest injected here
    return "\n\n".join(parts)
```

### Test-Assertion Review Gate (D14)

```python
# Source: D14 specification from CONTEXT.md
# Placement: cli.py, after review pipeline, before R1 gate-check

def _run_test_assertion_review(
    diff_text: str,
    cwd: Path,
    backend=None,
) -> list:
    """SC4: test-assertion review by independent reviewer.

    Fresh llm_invoke, never the impl/test author. Runs BEFORE R1.
    Returns list of advisory findings (do not reset cycle counter).
    """
    from .llm_invoke import llm_invoke
    from .reviewer_json import validate_reviewer_json, _json_to_state_findings

    # Only review if test files are in the diff
    from .diff import get_changed_files
    changed = get_changed_files(diff_text)
    test_files = [f for f in changed if "test" in f.lower()]
    if not test_files:
        return []

    prompt = (
        "You are a test-assertion reviewer. Your ONLY job is to review "
        "test code for assertion quality. You are NOT the implementation "
        "author.\n\n"
        "Check:\n"
        "- Assertion completeness: do tests check all relevant outputs?\n"
        "- Edge case coverage: are boundary conditions tested?\n"
        "- Mock accuracy: do mocks reflect real behavior?\n"
        "- Assertion specificity: are assertions too broad?\n\n"
        'Return JSON: {"findings": [...], "code_excerpts": [...]}\n'
        "\nDiff:\n" + diff_text
    )
    result = llm_invoke(prompt, backend=backend)
    try:
        validated = validate_reviewer_json(result.content)
        return _json_to_state_findings(validated, "test-assertion")
    except (ValueError, Exception):
        return []  # fail-open for advisory gate
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| cli.py:783 NotImplementedError | Phase 15: fresh llm_invoke per pass | Phase 15 (this phase) | C-leg independence realized |
| No conventions digest in criteria | D11 conventions-digest slot | Phase 15 (this phase) | Reviewer sees naming conventions without implementer bias |
| No test-assertion review gate | D14 separate gate before R1 | Phase 15 (this phase) | Test quality independently verified |
| Implicit human review expectation | D13 explicit human backstop checklist | Phase 15 (this phase) | Human review focused by gate's coverage proof |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | AGENTS.md will use YAML frontmatter with `related_repos` or `siblings` keys | Architecture Patterns | Parser fails gracefully (empty digest), low risk |
| A2 | Same-repo convention extraction via Python AST public-name scan is sufficient for v1 | Code Examples | Best-effort digest; custom mapping covers precision cases |
| A3 | Test-assertion review gate should fail-open (advisory findings, not blocking) | Code Examples | If fail-closed, false positives from assertion review would block commits |

All claims about existing code (factories.py, outlet_c.py, cli.py, verify.py, reviewer_json.py signatures and behavior) are [VERIFIED: codebase read] -- confirmed by reading the actual source files during this research session.

## Open Questions

1. **spawn_fn Usage tracking**
   - What we know: The current spawn_fn signature `(str, str) -> str` cannot carry Usage metadata. The A-leg tracks tokens inside the build_l1_provider closure.
   - What's unclear: Should outlet_c.py's _l1_provider be restructured to call llm_invoke directly (bypassing spawn_fn) for token tracking, or should spawn_fn's return type be expanded?
   - Recommendation: Restructure _l1_provider in outlet_c.py to call llm_invoke directly inside its closure (same pattern as build_l1_provider), making spawn_fn internal rather than a parameter. This keeps the A/C convergence (D9) cleanest. The spawn_fn parameter was a Phase 14 bridge; Phase 15 can internalize it.

2. **Post-image content in criteria payload**
   - What we know: D11 specifies "post-image content of the changed files" as part of criteria. The A-leg prompt (factories.py:238-249) currently includes only the diff, not post-image.
   - What's unclear: Whether adding post-image content is part of Phase 15 or a separate enhancement.
   - Recommendation: Include post-image in the criteria prompt for both legs. It is specified in D11 and required for excerpt verification (the reviewer needs to see the code as it stands to produce valid code_excerpts).

3. **Conventions digest caching key**
   - What we know: D12 SPEC says "cache digest keyed on sibling repo's commit hash; refresh only when stale."
   - What's unclear: Where to store the cache (in-memory per session, on-disk in .code-forge/, or both).
   - Recommendation: On-disk in `.code-forge/conventions-cache/` keyed by repo path hash + commit hash. Simple JSON file per sibling. This survives process restarts and aligns with other .code-forge/ artifacts.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.0.3 |
| Config file | pyproject.toml (pytest section) |
| Quick run command | `python -m pytest tests/test_outlet_c.py tests/test_factories.py -x -q` |
| Full suite command | `python -m pytest tests/ -x -q` |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SHRK-02-SC1 | Fresh llm_invoke per review pass (C-leg) | unit | `pytest tests/test_outlet_c.py::TestIndependence -x` | Wave 0 |
| SHRK-02-SC2 | Only diff+criteria passed to review agent | unit | `pytest tests/test_outlet_c.py::TestCriteriaPayload -x` | Wave 0 |
| SHRK-02-SC3 | No implementation context leakage | unit | `pytest tests/test_outlet_c.py::TestContextIsolation -x` | Wave 0 |
| SHRK-02-SC4 | Test assertion review != implementation agent | unit | `pytest tests/test_cli_integration.py::TestAssertionGate -x` | Wave 0 |
| D11 | Conventions-digest slot in criteria | unit | `pytest tests/test_conventions.py -x` | Wave 0 |
| D12 | Cross-repo resolver sources | unit | `pytest tests/test_conventions_resolver.py -x` | Wave 0 (15b) |
| D13 | Human backstop checklist in SKILL.md | manual | Verify SKILL.md section exists | Wave 0 |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/test_outlet_c.py tests/test_factories.py tests/test_conventions.py -x -q`
- **Per wave merge:** `python -m pytest tests/ -x -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_outlet_c.py::TestIndependence` -- SC1: verify spawn_fn calls llm_invoke per pass (mock llm_invoke, assert call count = 3 per round)
- [ ] `tests/test_outlet_c.py::TestCriteriaPayload` -- SC2: verify prompt contains only diff+criteria, no session context
- [ ] `tests/test_outlet_c.py::TestContextIsolation` -- SC3: verify no state carries between passes (mock records call args, verify each call is independent)
- [ ] `tests/test_conventions.py` -- D11: conventions-digest slot (empty when unconfigured, populated when extractor returns data)
- [ ] `tests/test_conventions_resolver.py` -- D12: source resolver priority, AGENTS.md parsing, custom mapping
- [ ] `tests/test_cli_integration.py::TestAssertionGate` -- SC4: test-assertion review separate from impl agent

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | -- |
| V3 Session Management | no | -- |
| V4 Access Control | no | -- |
| V5 Input Validation | yes | reviewer_json.validate_reviewer_json (fail-closed on malformed JSON) |
| V6 Cryptography | no | -- |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Implementer context injected into reviewer prompt | Information Disclosure | D3: criteria payload contains only diff+post-image+role+dimensions+digest; never implementer session |
| Malformed reviewer JSON bypasses validation | Tampering | reviewer_json.validate_reviewer_json fail-closed (CONFIRMED finding on schema error) |
| Conventions digest tainted by implementer | Tampering | D12 SPEC: derive from authoritative code, never from implementer session |
| Cross-repo conventions.yaml path traversal | Tampering | Validate paths are relative and under repo root (same pattern as cli.py --whole-file validation) |

## Project Constraints (from CLAUDE.md)

- Language: All documentation and skill files in English
- No non-ASCII in code: typographic characters must be ASCII equivalents
- Dependencies: bash assertion primitives require only jq; skills require Claude Code or compatible AI coding assistant
- Compatibility: Must work with Claude Code skill discovery (SKILL.md in ~/.claude/skills/name/)
- .planning/ is gitignored, never committed to main (snapshots on orphan branch planning-local)
- Full 3-cycle review + smoke test required before any commit
- Worktree required for all code changes (Phase 0 in all plans)

## Sources

### Primary (HIGH confidence)
- `src/code_forge/factories.py` -- A-leg reference implementation (build_l1_provider inner loop, lines 200-311) [VERIFIED: codebase read]
- `src/code_forge/outlet_c.py` -- C-leg orchestrator with spawn_fn parameter (lines 34-101) [VERIFIED: codebase read]
- `src/code_forge/cli.py:780-794` -- NotImplementedError ceiling and outlet dispatch [VERIFIED: codebase read]
- `src/code_forge/reviewer_json.py` -- shared validation, accepts str|dict (lines 18-75) [VERIFIED: codebase read]
- `src/code_forge/machine.py` -- StateMachine, l1_provider 4-tuple, cycle counting [VERIFIED: codebase read]
- `src/code_forge/verify.py` -- hardened verify gate (STEP A/B/C + checks 5/6/7) [VERIFIED: codebase read]
- `src/code_forge/llm_invoke.py` -- llm_invoke signature and LLMResult dataclass [VERIFIED: codebase read]
- `tests/test_outlet_c.py` -- existing test patterns for spawn_fn and C-leg [VERIFIED: codebase read]
- `.planning/phases/15-reviewer-independence/15-CONTEXT.md` -- all D1-D17 decisions [VERIFIED: codebase read]
- `/tmp/draft_20260607_p15_discuss_input_v2.md` -- design rationale (282 lines) [VERIFIED: codebase read]

### Secondary (MEDIUM confidence)
- `.planning/REQUIREMENTS.md` -- SHRK-02 definition [VERIFIED: codebase read]
- `.planning/ROADMAP.md` -- Phase 15 SC1-SC4 [VERIFIED: codebase read]
- `.planning/STATE.md` -- project state, 1090 tests passing [VERIFIED: codebase read]

### Tertiary (LOW confidence)
- AGENTS.md format assumptions [ASSUMED] -- no authoritative standard found; format will need user confirmation

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new packages; all existing infrastructure verified by reading source
- Architecture: HIGH -- A-leg reference implementation fully understood; C-leg gap clearly bounded (single function replacement)
- Pitfalls: HIGH -- all pitfalls derived from reading actual code and project memories
- Conventions resolver (15b): MEDIUM -- AGENTS.md format is assumed; extraction recipes are domain-specific

**Research date:** 2026-06-07
**Valid until:** 2026-06-21 (14 days -- codebase is fast-moving, Phase 14 just completed)
