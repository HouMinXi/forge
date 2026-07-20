# Phase 20: Verdict Honesty - Research

**Researched:** 2026-06-12
**Domain:** Advisory review axis + smoke evidence receipt + verdict display
**Confidence:** HIGH

## Summary

Phase 20 delivers REVIEW-RUNTIME-01: a dedicated RUNTIME advisory axis that
makes forge's verdict honest by declaring what it did NOT verify at runtime.
The implementation has three concrete forms: (a) a `code-forge smoke-run`
wrapper that produces machine-verifiable receipts keyed by diff content-hash,
(b) a RuntimeRunner advisory axis that asks a fixed lifecycle/side-effect
question via LLM and enumerates runtime surfaces, and (c) a "NOT VERIFIED"
display block in the verdict output listing unexercised surfaces.

The codebase already provides all infrastructure needed. The advisory.py
AxisRunner Protocol, machine.py advisory dispatch (lines 969-995), and eval
runner AxisHook seam (runner.py lines 45-69) are the three primary extension
points. TaintRunner (taint.py lines 182-283) is the exact structural model --
RuntimeRunner follows the same pattern: implement AxisRunner Protocol,
`is_advisory=True`, register via `advisory_runners` on the StateMachine
constructor. The receipt.py / verify.py machinery from Phase 4 provides the
anti-shirk receipt model; smoke-run extends it with a content-hash-keyed
receipt for smoke evidence. The eval corpus already tags E1-E6 entries with
`axis_tags: [RUNTIME]` and the runner already classifies RUNTIME as an
LLM-reviewed axis (3 runs, 2-of-3 majority per D-11).

**Primary recommendation:** Implement as 4-5 new/modified files following
the TaintRunner model exactly: `runtime.py` (RuntimeRunner + receipt wrapper),
wire into `cli.py` (smoke-run subcommand) and `machine.py` (advisory_runners),
update `SKILL.md` mirror + drift test, extend eval corpus schema with
`expected_advisory` and fix E1-E6 expected_verdict per D-06.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Mechanical evidence gate. Smoke claim counts ONLY with
  machine-verifiable receipt (command transcript + exit code). No receipt or
  failed validation = UNVERIFIED.
- **D-02:** UNVERIFIED is the smoke-AXIS status, not a new top-level verdict.
  Verdict values stay PASS/FAIL/HOLD; exit codes unchanged. When UNVERIFIED,
  verdict output appends a "NOT VERIFIED: <surfaces>" block.
- **D-03:** v1 = LLM enumeration. RUNTIME axis LLM call answers the lifecycle
  question AND enumerates runtime surfaces. 3-run 2-of-3 majority applies.
- **D-04:** Dedicated RUNTIME axis call, post-convergence, via AxisRunner seam.
  BOTH outlets carry the question (CLI via machine.py, inline via SKILL.md
  mirror). Always-on: no gate.yaml opt-out. If axis LLM call fails, record
  SKIPPED with reason (never-silent-skip).
- **D-05:** One fixed standard lifecycle/side-effect question (with diff
  context slots), not per-diff generated. Deterministic, auditable, testable.
- **D-07:** Receipt producer is forge-owned wrapper: `code-forge smoke-run
  [--surface "<name>"] -- <cmd>`. Keys receipt by diff content-hash.
- **D-08:** Default state is UNVERIFIED (fail-closed). Exactly two states:
  VERIFIED (valid receipt present) or UNVERIFIED. No third state.
- **D-09:** Smoke-axis status line ALWAYS printed (VERIFIED with fingerprint,
  or UNVERIFIED). Silence must never read as "verified." Dual output:
  stderr human block + receipts/advisory JSON.
- **D-10:** Anti-drift: canonical question text lives as a constant in
  src/code_forge; SKILL.md carries a mirror copy; one test asserts verbatim
  equality.
- **D-11:** Per-surface accounting. `smoke-run --surface "<name>"` declares
  which surface. NOT-VERIFIED list = (LLM-enumerated surfaces) minus
  (receipt-declared surfaces).
- **D-12:** Matching mechanism = case-insensitive keyword substring against
  advisory text; any keyword hit = caught. No LLM judge, no regex.

### Claude's Discretion
- Receipt JSON schema details (field names, timestamps, fingerprint format).
- Exact wording of the fixed lifecycle question.
- Per-entry `expected_advisory` keyword lists.
- Surface-name alignment (normalization / fuzzy-match mechanics).
- Surfaces-list display cap / noise control.
- What each E1-E6 expected_verdict becomes (must verify per entry).
- SARIF inclusion of the advisory block.

### Deferred Ideas (OUT OF SCOPE)
- Mechanical surface catalog (static diff-signal table)
- Re-execution verification (forge re-runs smoke)
- LLM judge for eval advisory matching
- install-skill generation-time injection of question text
- PARTIAL smoke state (no third state; per-surface counts convey coverage)
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| REVIEW-RUNTIME-01 | Runtime-contract/lifecycle lens (ADVISORY). Three forms: (a) simulated smoke reports UNVERIFIED, (b) lifecycle/side-effect question wired into review, (c) verdict declares unverified runtime surface | RuntimeRunner as AxisRunner plug-in (TaintRunner model); smoke-run CLI subcommand with receipt; verdict display extension; eval corpus expected_advisory |
</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Smoke receipt production | CLI (smoke-run subcommand) | -- | Wraps user command, captures transcript+exit code, writes receipt keyed by diff hash |
| RUNTIME axis LLM call | API/Backend (llm_invoke) | -- | Same backend path as other LLM axes (taint, fixval); uses configured review backend |
| Surface enumeration | API/Backend (LLM response) | CLI (receipt surfaces) | LLM enumerates from diff; receipt declares from --surface flag; set difference yields NOT VERIFIED |
| Verdict NOT VERIFIED display | CLI (machine.py stderr + JSON) | Inline (SKILL.md mirror) | machine.py _display_advisories extended; SKILL.md mirrors the question text |
| Eval scoring | CLI (eval subcommand) | -- | RuntimeAxisHook + expected_advisory field extension |
| Drift test | Test suite | -- | pytest asserts constant == SKILL.md mirror text |

## Standard Stack

### Core

No new external dependencies. This phase uses only existing forge infrastructure:

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| PyYAML | (existing) | corpus.yaml parsing | Already in use for gate.yaml, corpus |
| unidiff | (existing) | diff parsing for surface enumeration | Already in fixval.py |
| pytest | 9.0.3 (existing) | Test framework | Already 1477 tests on this version |

### Supporting

No new packages needed. All functionality builds on existing forge modules.

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| LLM surface enumeration (D-03) | Static diff-signal catalog | Deferred by decision -- add only if eval shows LLM misses |
| Keyword substring matching (D-12) | LLM judge | Rejected v1 -- stochastic ruler cannot measure honesty |
| Keyword substring matching (D-12) | Regex matching | Rejected -- overfit risk |

## Package Legitimacy Audit

No new packages to install. Phase 20 uses only existing project dependencies.

**Packages removed due to slopcheck [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none

## Architecture Patterns

### System Architecture Diagram

```
  User runs smoke test
         |
         v
  code-forge smoke-run --surface "nftables" -- pytest tests/test_rules.py
         |
         +---> Execute command, capture transcript + exit code
         |
         +---> Compute diff content-hash (source.compute_source_hash)
         |
         +---> Write smoke-receipt.json keyed by diff-hash + surface name
         |
         v
  code-forge review (normal pipeline)
         |
         v
  [convergence reached -- PASS or HOLD]
         |
         v
  _run_advisory_axes()
         |
         +---> TaintRunner.run() (existing)
         |
         +---> RuntimeRunner.run()
         |         |
         |         +---> LLM call: lifecycle question + surface enumeration
         |         |     (structured JSON response)
         |         |
         |         +---> Read smoke receipts from .code-forge/smoke-receipts/
         |         |
         |         +---> Compute: VERIFIED surfaces (receipt-declared)
         |         |     vs ENUMERATED surfaces (LLM-enumerated)
         |         |
         |         +---> NOT VERIFIED = enumerated - verified
         |         |
         |         +---> Return AdvisoryFinding list + smoke status
         |
         v
  _display_advisories() -- extended
         |
         +---> "--- Advisory ---" separator (existing)
         +---> [TAINT] findings (existing)
         +---> [RUNTIME] lifecycle findings
         +---> "--- Smoke Status ---"
         +---> "smoke: N/M surfaces verified; NOT VERIFIED: [x, y]"
         |
         v
  _serialize_advisories() -- advisory-findings.json (existing)
         |
         v
  Verdict output (PASS/FAIL/HOLD -- unchanged exit codes)
```

### Recommended Project Structure

New and modified files:

```
src/code_forge/
  runtime.py              # NEW: RuntimeRunner (AxisRunner) + smoke receipt logic
  cli.py                  # MOD: add smoke-run subcommand
  machine.py              # MOD: wire RuntimeRunner into advisory_runners
  eval/runner.py          # MOD: RuntimeAxisHook registration
  eval/corpus.py          # MOD: expected_advisory field on CorpusEntry
  eval/scorer.py          # MOD: advisory content-match scoring for RUNTIME
  skills/code-forge/SKILL.md  # MOD: add RUNTIME mirror section
  skills/smoke-test/SKILL.md  # MOD: add smoke-run usage + UNVERIFIED contract

tests/
  test_runtime.py         # NEW: RuntimeRunner unit tests
  test_smoke_receipt.py   # NEW: smoke-run receipt tests
  test_runtime_machine.py # NEW: machine.py RUNTIME wiring integration
  test_runtime_eval.py    # NEW: eval scoring for RUNTIME axis
  test_runtime_drift.py   # NEW: drift test (constant == SKILL.md mirror)

tests/eval/corpus/
  corpus.yaml             # MOD: expected_advisory field + E1-E6 expected_verdict fix
```

### Pattern 1: AxisRunner Plug-in (TaintRunner Model)

**What:** A new advisory axis implements the AxisRunner Protocol from
advisory.py, returns AdvisoryFinding list, and is registered via
`advisory_runners` on the StateMachine constructor.

**When to use:** For any new advisory review axis (RUNTIME, LEGACY, INTENT).

**Example:**
```python
# Source: src/code_forge/taint.py (TaintRunner as model) [VERIFIED: codebase read]
class RuntimeRunner:
    """Advisory axis: lifecycle/side-effect review + smoke evidence."""

    def __init__(self) -> None:
        self.source_files: list[Path] | None = None
        self.infra_errors: list[str] = []

    @property
    def is_advisory(self) -> bool:
        return True

    def run(
        self,
        diff_text: str,
        repo_root: Path,
    ) -> list[AdvisoryFinding]:
        self.infra_errors.clear()
        # 1. LLM call with fixed lifecycle question + diff context
        # 2. Parse structured response (surfaces + findings)
        # 3. Read smoke receipts from .code-forge/smoke-receipts/
        # 4. Compute NOT VERIFIED = enumerated - verified
        # 5. Return AdvisoryFinding list
        ...
```

### Pattern 2: Smoke Receipt (receipt.py Model)

**What:** A JSON file keyed by diff content-hash that proves a smoke test
was executed. The `smoke-run` wrapper writes it; RuntimeRunner reads it.

**When to use:** When forge needs machine-verifiable evidence that a user
command actually ran (not just self-reported).

**Example:**
```python
# Source: src/code_forge/receipt.py (write_receipts as model) [VERIFIED: codebase read]
# Smoke receipt schema (Claude's discretion, recommended):
{
    "diff_sha256": "abc123...",          # content-hash from source.compute_source_hash
    "surface": "nftables",               # --surface value or "default"
    "command": "pytest tests/test_rules.py",
    "exit_code": 0,
    "transcript_sha256": "def456...",    # sha256 of stdout+stderr
    "timestamp": "2026-06-12T10:00:00Z",
    "status": "VERIFIED"                 # VERIFIED if exit_code == 0
}
```

### Pattern 3: Eval Advisory Content-Match (D-12)

**What:** For RUNTIME corpus entries, "caught" means case-insensitive keyword
substring match against the advisory text. No LLM judge, no regex.

**When to use:** Scoring RUNTIME axis in the eval scorecard.

**Example:**
```python
# Corpus entry with expected_advisory:
# - name: E1-stale-nftables
#   expected_advisory: ["nftables", "stale", "reload"]
#   # Any keyword hit in advisory text = caught

def _advisory_caught(advisory_text: str, keywords: list[str]) -> bool:
    lower = advisory_text.lower()
    return any(kw.lower() in lower for kw in keywords)
```

### Pattern 4: Anti-Drift Test (D-10, Phase 19.1 Model)

**What:** A test asserts that the canonical question text constant in
`src/code_forge/runtime.py` is verbatim identical to the mirror copy in
`SKILL.md`. Prevents the two outlets from diverging.

**When to use:** Any time forge has a canonical constant that is mirrored
in SKILL.md or other outlet.

**Example:**
```python
# Source: Phase 19.1 dual-copy divergence lesson [VERIFIED: CONTEXT.md D-10]
def test_runtime_question_drift():
    from code_forge.runtime import RUNTIME_LIFECYCLE_QUESTION
    skill_path = Path(__file__).parent.parent / "src" / "code_forge" / \
        "skills" / "code-forge" / "SKILL.md"
    skill_text = skill_path.read_text()
    assert RUNTIME_LIFECYCLE_QUESTION in skill_text, \
        "SKILL.md must contain the canonical lifecycle question verbatim"
```

### Anti-Patterns to Avoid

- **RUNTIME axis blocking the cycle counter:** AdvisoryFinding is structurally
  separate from StateFinding. RuntimeRunner returns `list[AdvisoryFinding]`,
  never `list[StateFinding]`. The `_fixpoint_reached()` method operates ONLY
  on `self._state.findings` (StateFinding list), never on `self._advisories`.
  [VERIFIED: advisory.py founding principle, machine.py lines 766-811]

- **Gate.yaml opt-out for RUNTIME:** D-04 specifies always-on; no gate.yaml
  knob. Unlike semgrep (TaintRunner fails gracefully when absent), the
  RUNTIME LLM call uses the same backend as L1 review -- it is always
  available when forge runs with a backend.

- **Silent skip on LLM failure:** D-04 requires SKIPPED with reason on
  failure. Follow the TaintRunner pattern: record to `self.infra_errors`,
  return empty list. Never silently swallow.

- **Modifying verdict values or exit codes:** D-02 locks PASS/FAIL/HOLD
  as the only verdict values. UNVERIFIED is axis status (display only),
  not a verdict value. Exit codes unchanged per SC4.

- **Expected_verdict HOLD for E1-E6:** D-06 explicitly states this is WRONG
  under the advisory-never-blocks principle. Each E1-E6 entry's
  expected_verdict must reflect what the real pipeline actually produces
  (likely PASS, since RUNTIME is advisory and cannot block).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Receipt JSON writing | Custom JSON serialization | Extend receipt.py atomic write pattern (tmp+replace) | Existing battle-tested pattern |
| Diff content-hash | New hash function | `source.compute_source_hash(git_diff=...)` | Already used for receipt keying; 19.1 P-05/P-07 precedent |
| LLM invocation | Custom HTTP/subprocess call | `llm_invoke.llm_invoke(prompt, backend=backend)` | Handles cli/api/vertex formats, timeout, signal cleanup |
| Advisory finding construction | Custom dict | `AdvisoryFinding` frozen dataclass from advisory.py | Type safety, structural incompatibility with StateFinding enforced |
| Eval run count selection | Custom logic | `_default_runs(entry)` in runner.py | Already classifies RUNTIME as LLM axis (3 runs) |
| CLI subcommand registration | Custom argparse | Follow `install-hooks` pattern in cli.py `_build_parser()` | Consistent with 10+ existing subcommands |

**Key insight:** Every building block for Phase 20 already exists in the
codebase. The phase is primarily a wiring exercise: RuntimeRunner plugs
into the advisory axis seam, smoke-run plugs into the CLI subcommand
registry, and the eval extension plugs into the AxisHook seam. No new
external dependencies, no new architectural patterns.

## Common Pitfalls

### Pitfall 1: Advisory Finding Leaks into Convergence Logic

**What goes wrong:** RuntimeRunner returns StateFinding instead of
AdvisoryFinding, or findings are accidentally added to `self._state.findings`
instead of `self._advisories`.

**Why it happens:** Copy-paste from blocking axis code (fixval.py returns
StateFinding).

**How to avoid:** RuntimeRunner.run() return type is
`list[AdvisoryFinding]`. `is_advisory` property returns True. machine.py
`_run_advisory_axes()` extends `self._advisories`, never
`self._state.findings`. Test: advisory finding present + fixpoint_reached()
still True.

**Warning signs:** Cycle counter resets when RUNTIME findings are present;
verdict changes to HOLD/FAIL from RUNTIME findings.

### Pitfall 2: E1-E6 expected_verdict Set to HOLD

**What goes wrong:** D-06 correction is applied blindly: all E1-E6 set to
PASS without verifying what the real pipeline produces.

**Why it happens:** RUNTIME is advisory and cannot block, so the intuition
is "must be PASS." But the pipeline might HOLD or FAIL on the E1-E6 diffs
for OTHER reasons (L0 lint findings, TRUST gate hits, etc.).

**How to avoid:** D-06 says "verify per-entry, do not assume PASS." Run
each E1-E6 diff through the existing pipeline (without the RUNTIME axis)
to observe the actual verdict. Set expected_verdict to whatever the
pipeline actually produces.

**Warning signs:** Eval scorecard shows unexpected OVER-BLOCK or MISSED
for entries that should be clean.

### Pitfall 3: Smoke Receipt TOCTOU

**What goes wrong:** Smoke receipt is written with one diff-hash, but the
diff changes before the review runs, so the receipt's hash no longer
matches the current diff.

**Why it happens:** User modifies code between running `smoke-run` and
running `code-forge review`.

**How to avoid:** Receipt is keyed by diff content-hash (D-07 / 19.1
P-05/P-07 pattern). RuntimeRunner reads the receipt and compares
receipt.diff_sha256 against the current diff hash. Mismatch = receipt
invalid = UNVERIFIED. This is correct behavior, not a bug.

**Warning signs:** Receipts always showing UNVERIFIED despite smoke-run
having been run.

### Pitfall 4: SKILL.md Mirror Drifts from Constant

**What goes wrong:** Someone updates the lifecycle question in runtime.py
but forgets to update the SKILL.md mirror copy (or vice versa).

**Why it happens:** Two copies of the same text in different files. The
19.1 dual-copy divergence is the historical lesson.

**How to avoid:** D-10 requires a drift test. One pytest asserts the
constant from runtime.py appears verbatim in SKILL.md. CI catches drift
immediately.

**Warning signs:** Inline-outlet users get a different lifecycle question
than CLI-outlet users.

### Pitfall 5: Smoke-run Receipt Bypass via Exit Code Manipulation

**What goes wrong:** The executor runs `code-forge smoke-run -- true`
(a command that always exits 0) to get a VERIFIED receipt without
actually testing anything.

**Why it happens:** D-01/D-07 acknowledge this: "The executor can only
choose to run or not run; it cannot forge receipt content." But running
a trivially-true command produces a valid receipt.

**How to avoid:** This is by design. The trust model is asymmetric:
the executor can game "did not run" but cannot game "ran AND here is
the transcript." The --surface flag (D-11) limits what each receipt
covers. A single trivial command does not wash the whole surface list
green. The NOT VERIFIED display exposes unexercised surfaces.

**Warning signs:** All surfaces show VERIFIED but the transcript content
is trivially short (a future enhancement could flag suspiciously short
transcripts, but this is deferred).

## Code Examples

### RuntimeRunner Skeleton

```python
# Source: TaintRunner model [VERIFIED: src/code_forge/taint.py lines 182-283]
# + advisory.py AxisRunner Protocol [VERIFIED: src/code_forge/advisory.py]

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .advisory import AdvisoryFinding
from .llm_invoke import llm_invoke, LLMInvokeError
from .source import compute_source_hash

# D-05: fixed lifecycle question (canonical source)
RUNTIME_LIFECYCLE_QUESTION = (
    "Given this diff, identify:\n"
    "1. What runtime surfaces does this change affect? "
    "(e.g., systemd units, nftables rules, network sockets, "
    "subprocess calls, file side effects, cron/timer jobs)\n"
    "2. For each surface: what lifecycle/side-effect could break "
    "at runtime even if the code is syntactically correct?\n"
    "3. What would a smoke test need to exercise to verify "
    "these surfaces actually work?\n\n"
    "Return JSON:\n"
    '{"surfaces": ["surface1", "surface2"], '
    '"findings": [{"file": "...", "line": N, '
    '"surface": "...", "description": "..."}]}\n\n'
    "Diff:\n{diff_text}"
)


class RuntimeRunner:
    """Advisory axis: lifecycle/side-effect + smoke evidence."""

    def __init__(self, backend=None) -> None:
        self.source_files: Optional[list[Path]] = None
        self.infra_errors: list[str] = []
        self._backend = backend

    @property
    def is_advisory(self) -> bool:
        return True

    def run(
        self,
        diff_text: str,
        repo_root: Path,
    ) -> list[AdvisoryFinding]:
        self.infra_errors.clear()
        if not diff_text or not diff_text.strip():
            return []

        # 1. LLM call with lifecycle question
        prompt = RUNTIME_LIFECYCLE_QUESTION.format(diff_text=diff_text)
        try:
            result = llm_invoke(prompt, backend=self._backend)
        except LLMInvokeError as exc:
            # D-04: SKIPPED with reason, never silent
            self.infra_errors.append(
                "RUNTIME axis LLM call failed: %s" % exc
            )
            return [AdvisoryFinding(
                id="runtime-skipped",
                axis="RUNTIME",
                file="",
                line_range=[0, 0],
                description="RUNTIME axis SKIPPED: %s" % exc,
                attribution="runtime-axis/infra-error",
            )]

        # 2. Parse response
        # 3. Read smoke receipts
        # 4. Compute NOT VERIFIED
        # 5. Return findings
        ...
```

### Smoke-Run CLI Subcommand

```python
# Source: cli.py install-hooks pattern [VERIFIED: src/code_forge/cli.py line 362]

# In _build_parser():
smoke_parser = subparsers.add_parser(
    'smoke-run',
    help='run a smoke test and record a receipt',
    description=(
        'Execute a command and record a smoke-test receipt '
        'keyed by the current diff content-hash. '
        'Exit codes: command exit code (passthrough).'
    ),
)
smoke_parser.add_argument(
    "--surface", default="default",
    help="runtime surface this test exercises (default: 'default')",
)
smoke_parser.add_argument(
    "command", nargs=argparse.REMAINDER,
    help="command to execute (after '--')",
)
```

### Advisory Display Extension for Smoke Status

```python
# Source: machine.py _display_advisories [VERIFIED: lines 1015-1032]
# Extension: always-print smoke status line (D-09)

def _display_smoke_status(self) -> None:
    """Print smoke-axis status unconditionally (D-09).

    VERIFIED with fingerprint, or UNVERIFIED with surface list.
    Silence must never read as verified.
    """
    # Read from RuntimeRunner results
    runtime_advisories = [
        f for f in self._advisories if f.axis == "RUNTIME"
    ]
    # ... compute verified/unverified surfaces ...
    # Always print (D-09):
    print("--- Smoke Status ---", file=sys.stderr)
    if unverified_surfaces:
        print(
            "smoke: %d/%d surfaces verified; NOT VERIFIED: [%s]"
            % (verified_count, total_count,
               ", ".join(unverified_surfaces)),
            file=sys.stderr,
        )
    else:
        print("smoke: all surfaces verified", file=sys.stderr)
```

### Eval Corpus Schema Extension

```yaml
# Source: tests/eval/corpus/corpus.yaml [VERIFIED: codebase read]
# Extension: expected_advisory field for RUNTIME entries

entries:
  - name: E1-stale-nftables
    diff_file: diffs/E1-stale-nftables.diff
    expected_verdict: PASS  # D-06 fix: was HOLD, but RUNTIME is advisory
    axis_tags: [RUNTIME]
    expected_advisory: ["nftables", "stale", "reload"]
    # D-12: case-insensitive keyword substring match
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| E1-E6 expected_verdict: HOLD | expected_verdict: per-entry actual (likely PASS) | Phase 20 (D-06) | Eval no longer falsely claims advisory axes should block |
| Silence = no runtime concern | Always-print smoke status | Phase 20 (D-09) | "No news = good news" eliminated |
| Self-reported smoke results | Machine-verifiable smoke receipts | Phase 20 (D-01/D-07) | Anti-shirk extends to smoke evidence |

**Deprecated/outdated:**
- E1-E6 `expected_verdict: HOLD` in corpus.yaml is WRONG under the advisory-
  never-blocks principle. Must be corrected per D-06 before eval scoring works.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | E1-E6 expected_verdict should be PASS (advisory cannot block) | Code Examples / Eval Corpus | If other axes (L0/TRUST) trigger on E1-E6 diffs, expected_verdict should match the actual pipeline output. D-06 requires verifying per-entry. |
| A2 | `unidiff` suffices for surface enumeration diff parsing | Standard Stack | If the LLM does all enumeration (D-03), no additional diff parsing library is needed; unidiff is fallback only |
| A3 | The lifecycle question wording (A3 in Code Examples) is adequate | Code Examples | Eval will measure it; iterate wording only if eval shows underperformance (D-05) |

## Open Questions

1. **E1-E6 actual pipeline verdicts**
   - What we know: RUNTIME is advisory and cannot block. E1-E6 are tagged
     RUNTIME in corpus.yaml.
   - What's unclear: Do the E1-E6 diffs trigger L0 lint findings, TRUST
     gate hits, or other blocking axes? If so, expected_verdict should be
     HOLD/FAIL, not PASS.
   - Recommendation: Run each E1-E6 diff through the current pipeline
     (without RUNTIME axis) during implementation. Record the actual verdict
     per-entry. This is D-06's explicit requirement.

2. **Surface-name normalization between LLM and --surface**
   - What we know: LLM enumerates surfaces in free-text (D-03). --surface
     declares in user-chosen strings (D-11).
   - What's unclear: How to match "nftables rules" (LLM) to "nftables"
     (user --surface flag). Case-insensitive substring is a start.
   - Recommendation: v1 uses case-insensitive substring containment
     (either direction). Exact matching can be tightened later based on
     eval evidence. Claude's discretion per CONTEXT.md.

3. **Smoke receipt storage location**
   - What we know: Review receipts go to `.code-forge/receipts/`.
   - What's unclear: Should smoke receipts share that directory or use a
     separate `.code-forge/smoke-receipts/` directory?
   - Recommendation: Separate directory (`smoke-receipts/`) to avoid
     confusion with review receipts and simplify cleanup. The receipt.py
     atomic write pattern applies to either location.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.0.3 |
| Config file | pyproject.toml (existing) |
| Quick run command | `pytest tests/test_runtime.py tests/test_smoke_receipt.py -x -q` |
| Full suite command | `pytest -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| RUNTIME-01a | Simulated smoke reports UNVERIFIED | unit | `pytest tests/test_runtime.py::test_no_receipt_means_unverified -x` | Wave 0 |
| RUNTIME-01b | Lifecycle question in review prompt | unit | `pytest tests/test_runtime.py::test_lifecycle_question_present -x` | Wave 0 |
| RUNTIME-01c | Verdict declares unverified surface | unit | `pytest tests/test_runtime_machine.py::test_not_verified_display -x` | Wave 0 |
| RUNTIME-01-SC4 | Advisory only, never blocks | unit | `pytest tests/test_runtime.py::test_advisory_never_blocks -x` | Wave 0 |
| RUNTIME-01-SC5 | Eval scores RUNTIME axis | unit | `pytest tests/test_runtime_eval.py::test_advisory_keyword_match -x` | Wave 0 |
| D-10 drift | Constant == SKILL.md mirror | unit | `pytest tests/test_runtime_drift.py -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/test_runtime.py tests/test_smoke_receipt.py tests/test_runtime_drift.py -x -q`
- **Per wave merge:** `pytest -q`
- **Phase gate:** Full suite green before /gsd:verify-work

### Wave 0 Gaps
- [ ] `tests/test_runtime.py` -- RuntimeRunner unit tests (covers RUNTIME-01a/b/SC4)
- [ ] `tests/test_smoke_receipt.py` -- smoke-run receipt write/read tests
- [ ] `tests/test_runtime_machine.py` -- machine.py RUNTIME wiring (covers RUNTIME-01c)
- [ ] `tests/test_runtime_eval.py` -- eval advisory matching (covers SC5)
- [ ] `tests/test_runtime_drift.py` -- drift test (covers D-10)

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | -- |
| V3 Session Management | no | -- |
| V4 Access Control | no | -- |
| V5 Input Validation | yes | Validate --surface flag (no shell metacharacters in receipt); validate smoke-run command args (REMAINDER parsing) |
| V6 Cryptography | no | sha256 for content-hash (existing compute_source_hash) |

### Known Threat Patterns for this phase

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Receipt forgery (fake VERIFIED) | Tampering | Receipt keyed by diff content-hash; RuntimeRunner re-validates hash at read time |
| Command injection via --surface | Tampering | Sanitize surface name in receipt (no shell metacharacters); surface stored as data, never executed |
| LLM prompt injection via diff | Tampering | Same risk as existing L1 review; no new attack surface (LLM is reviewer, not executor) |

## Sources

### Primary (HIGH confidence)
- `src/code_forge/advisory.py` -- AxisRunner Protocol, AdvisoryFinding dataclass
- `src/code_forge/machine.py` -- advisory dispatch (lines 969-995), _display_advisories (lines 1015-1032), advisory_runners injection (line 154)
- `src/code_forge/taint.py` -- TaintRunner as structural model (lines 182-283)
- `src/code_forge/eval/runner.py` -- AxisHook seam, DETERMINISTIC_TAGS, _DEFAULT_LLM_RUNS
- `src/code_forge/receipt.py` -- receipt write pattern (atomic tmp+replace)
- `src/code_forge/verify.py` -- receipt validation, content-hash checking
- `src/code_forge/cli.py` -- subcommand registration pattern
- `src/code_forge/source.py` -- compute_source_hash
- `tests/eval/corpus/corpus.yaml` -- E1-E6 entries with RUNTIME tag
- `tests/test_machine_advisory.py` -- advisory wiring test pattern
- `tests/test_advisory.py` -- AxisRunner conformance tests

### Secondary (MEDIUM confidence)
- `.planning/phases/20-verdict-honesty/20-CONTEXT.md` -- D-01 through D-12 locked decisions
- `.planning/REQUIREMENTS.md` -- REVIEW-RUNTIME-01 definition and success criteria
- `.planning/ROADMAP.md` -- Phase 20 dependencies and success criteria

### Tertiary (LOW confidence)
- None -- all claims verified from codebase or locked decisions

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new dependencies, all existing infrastructure
- Architecture: HIGH -- exact structural model (TaintRunner) already exists and is tested
- Pitfalls: HIGH -- lessons from Phase 17/18/19 dual-copy divergence, anti-shirk receipt, advisory founding principle
- Eval extension: HIGH -- corpus schema and runner hook seam already support the extension

**Research date:** 2026-06-12
**Valid until:** 2026-07-12 (stable internal infrastructure, no fast-moving externals)
