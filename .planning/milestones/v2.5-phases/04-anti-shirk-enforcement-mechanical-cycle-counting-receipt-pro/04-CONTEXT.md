# Forge Anti-Shirk Milestone 1: Connect Extension Points

**Status:** Draft
**Date:** 2026-05-28
**Scope:** Phase 5 first milestone
**Supersedes:** N/A

---

## Problem

Opus 4.7 in VSCode skips review cycles 2 and 3 by self-declaring them
clean.  The root cause is structural: the cycle counter and convergence
logic live entirely inside the LLM context (SKILL.md), with no external
verification.  The model is simultaneously author, reviewer, and
scorekeeper.

### Evidence

- 3/3 observed instances where impl+review shared context: review was
  self-trimmed once context exceeded 80%.
- Auto-continue (TRUST-06) lets "clean" flow silently.  A model that
  declares "Cycle 2/3 Pass 1/3: qodo-review -- CLEAN" without invoking
  the skill has no barrier.
- findings.json write operations depend on LLM execution.  A model can
  skip the write entirely and the pipeline cannot detect the omission.

### Existing infrastructure (key insight)

The CLI already ships a deterministic state machine in `machine.py`:

- `L0` (linters) -> `L1` (LLM candidates + falsification) -> `L2`
  (mutation) -> E2E (cross-component heuristic)
- Loop-until-fixpoint with round budgets.
- State persisted to `.code-forge/state.json`.
- Fixpoint determined by Python, not LLM self-report.

Two extension points were designed for Phase 4 but never connected:

| Interface | Current state | Designed for |
|---|---|---|
| `l1_provider` | `lambda: []` (no LLM candidates) | Returns L1 candidate findings from LLM passes |
| `Falsifier` | `StubFalsifier` only; `falsify_real.py` does not exist | Verifies each candidate (anti-hallucination) |

`--falsification-engine=real` raises `NotImplementedError("requires
Phase 4 (not shipped in v2.0)")`.

This milestone connects these two interfaces.  It does not redesign
the state machine.

---

## Architecture Decision

### Core principle

**The model has no interface to declare convergence.**  Convergence is
a mechanical property of the finding stream: three consecutive rounds
where the falsifier confirms zero new findings and zero regressions.
Python counts rounds; the model produces candidate findings; Python
decides when to stop.

### Two operating modes (shared state format)

```
              code-forge review  (unified entry point)
                     |
        CLI present? -+-- yes -> Path A: Python loop, one subprocess per pass
                      |              cycle counted by machine.py
                      |              shirking is physically impossible
                      +-- no  -> Path C: SKILL.md self-drives, writes receipts
                                    code-forge verify validates completeness
                      |
              [pre-commit hook]  no fresh valid attestation -> block commit
```

When the CLI is present in the editor environment, SKILL.md becomes a
thin shim that invokes `code-forge review` (Path A).  Path C is the
fallback when the CLI binary is genuinely absent.

### Path A: CLI as orchestrator (strongest guarantee)

Each review pass runs as a **fresh-context subprocess** (`claude -p
--model <pinned>` or API call).  The subprocess receives only:

- The diff under review
- Step 0 context (FUSE-01 block)
- Previous round's findings (for delta awareness)
- The pass-specific prompt (qodo / expert / adversarial)

It returns structured JSON (candidate findings).  It has no access to
the cycle counter, no ability to declare convergence, and no impl
context to compress away.

The existing `machine.py` loop orchestrates:

1. `l1_provider()` spawns 3 pass subprocesses, pools their candidates.
2. `falsifier.falsify()` verifies each candidate (anti-hallucination
   10-step protocol, also a subprocess).
3. Confirmed findings feed into autofix / revert / L2 / E2E (all
   existing).
4. `consecutive_clean_rounds` increments if the round produced zero
   new findings with disposition CONFIRMED or UNCERTAIN (see Glossary).
   Resets to 0 otherwise.
5. When `consecutive_clean_rounds == 3`, fixpoint reached.

Model provenance is pinned by CLI configuration (not by Agent tool
dispatch), aligning with the established preference against
uncontrolled Agent-spawn model selection.

### Path C: editor receipt + verify (degraded guarantee)

When SKILL.md self-drives (no CLI), each pass must write a **receipt**
to `.code-forge/receipts/`:

```json
{
  "cycle": 2,
  "pass": 1,
  "skill": "qodo-review",
  "diff_sha256": "a1b2c3...",
  "timestamp": "2026-05-28T10:00:00Z",
  "findings_count": 0,
  "findings": [],
  "anchors": [
    {"file": "src/foo.py", "line": 42, "text": "def bar(...)"}
  ],
  "code_excerpts": [
    {
      "file": "src/foo.py",
      "start_line": 40,
      "end_line": 44,
      "content": "    def bar(x):\n        return x + 1",
      "rationale": "inspected for off-by-one; clean"
    }
  ],
  "covered_line_ranges": [
    {"file": "src/foo.py", "start": 38, "end": 50}
  ]
}
```

`code_excerpts` requires the reviewer to copy the actual code at each anchor
(3-5 lines verbatim).  `verify` checks excerpt content against the real file
with `sed`.  A reviewer that skips reading must fabricate code -- fabricated
excerpts will not match.  `covered_line_ranges` tracks which diff lines each
pass examined; `verify` computes coverage percentage across the full diff.

`code-forge verify` mechanically validates (8 checks -- the original
byte-identical check was removed as a no-op, then progressive
obligation was added as the new check #8):

1. **Completeness**: 9 receipts exist (3 cycles x 3 passes).  Each
   cycle has passes {1,2,3} with no duplicates or gaps.
   `findings_count == len(findings)`.
2. **Diff hash**: `diff_sha256` matches the current working diff.
   All three components (receipt writer, verify, pre-commit hook)
   use `compute_source_hash()` from `source.py` as the single hash
   computation path.  The hook delegates to `code-forge verify`
   rather than computing its own hash in shell.
3. **Anchor reality**: every anchor's `file:line` exists in the
   current diff.
4. **Timestamp plausibility**: monotonically increasing, with minimum
   30s between consecutive receipts (a pass that completes in <30s
   on a non-trivial diff is suspect).
5. **Excerpt verification**: for each `code_excerpts` entry, read the
   actual file at `start_line:end_line` and compare verbatim.
   Mismatch = FAIL.  **Missing file = FAIL** (not silently skipped;
   the previous `except OSError: pass` was a bypass).
6. **Anchor coverage quota**: the union of `covered_line_ranges`
   across all 3 passes within one cycle must cover >= 60% of changed
   lines.  Quota is per-cycle (3 passes contribute to one union).
7. **Cross-cycle Jaccard overlap**: compute Jaccard similarity of
   `(file, line_range)` pairs between **all cycle pairs** (c1-c2,
   c2-c3, AND c1-c3).  Similarity > 0.8 on any pair flags rubber-
   stamping.  Comparing only consecutive cycles misses the case where
   c1 and c3 are identical stamps with c2 artificially different.
8. **Progressive obligation**: when all cycles are clean (zero
   findings throughout), each cycle's `covered_line_ranges` must
   differ from every other cycle by >= 20% (Jaccard distance >= 0.2).
   This ensures the reviewer examines different code regions rather
   than re-reading the same lines.  When prior cycles had findings
   that were fixed, this check is not enforced (the coverage shifts
   naturally due to fix lines).

On success, `verify` writes an attestation file:

```json
{
  "verified_at": "2026-05-28T10:30:00Z",
  "diff_sha256": "a1b2c3...",
  "receipt_sha256": "d4e5f6...",
  "result": "PASS"
}
```

The pre-commit hook checks for a fresh, matching attestation.

### Honest limitation (must be documented)

Path C catches **skipping** (missing receipts) and **blatant
fabrication** (stale diff hash, identical cycles, fabricated excerpts,
nonexistent anchors).  The 8 verify checks raise the cost of cheating
but do not eliminate it.  A reviewer that reads one line per anchor,
copies a real excerpt, writes one plausible sentence, and covers 61%
of lines satisfies all checks while doing no meaningful analysis.

**Path C alone creates false security.**  Three independent model
reviews (DeepSeek, Kimi, Mimo) converged on this: any verification
the reviewer implements, it can also satisfy superficially.  The
correct use of Path C mechanisms is as a **verification layer within
Path A**, not as a standalone fallback:

- In Path A the CLI subprocess writes receipts; `verify` checks them
  before advancing the cycle counter; failure restarts the pass.
- Path C mechanisms (receipts + hooks + Jaccard) add detection depth
  to an already-external state machine; they do not substitute for it.

If the CLI is truly absent (rare; the package ships as a wheel with a
console_scripts entry point), Path C operates with the honest caveat
that the cycle guarantee is weakened.  Diff size threshold: for diffs
< 50 changed lines, editor-only mode is acceptable (context-degradation
risk is low).  For diffs >= 50 lines, Path C without Path A should
surface a visible warning.

**Progressive obligation** (additional structure that raises cheating
cost, does not eliminate it):

When prior cycles had findings that were fixed:
- Cycle 2 must either find at least one issue Cycle 1 missed, or
  explicitly invalidate a Cycle 1 finding with cited evidence.  A
  receipt that does not mention Cycle 1 findings fails verify.
- Cycle 3 must examine edge cases of the fixes applied after Cycles
  1-2.  Its `covered_line_ranges` must overlap with lines changed by
  those fixes.

When all prior cycles were clean (zero findings throughout):
- Progressive obligation is satisfied if each cycle's
  `covered_line_ranges` differs from the prior cycle by at least 20%
  (Jaccard distance >= 0.2).  This ensures the reviewer examines
  different code regions rather than re-reading the same lines.

These obligations make each cycle's output obligation distinct, so
a model cannot copy-paste the same "all clean" narrative three times.

---

## Component Inventory (net new)

All components are small; the design reuses existing infrastructure.

### 1. `falsify_real.py` (~150 LOC)

Implements the existing `Falsifier` ABC.  `RealFalsifier.falsify()`
invokes an LLM subprocess with the 10-step anti-hallucination protocol
(already specified in kernel-fp-verify SKILL.md) and maps the output
to a `Disposition` value (see Glossary).

Prompt construction must use string concatenation (`+`), NOT
`.format()` or `%s`.  `finding.description` is untrusted LLM output
that may contain `{key}` or `%s` literals (common in Python code
snippets), which crash `.format()` with `KeyError` and `%` with
`TypeError`.  Similarly, `l1_provider` prompts that embed `diff_text`
must use `+` concatenation because diffs routinely contain `%s` and
`{...}` in Python source.

Makes `--falsification-engine=real` operational (currently raises
`NotImplementedError`).

### 2. `l1_provider` wiring in `_run_hold_loop` (~50 LOC)

When constructing `StateMachine` (`cli.py:550-562`), pass a provider
function.  The provider is built by `build_l1_provider(engine, resolved)`
in `factories.py`.  The `engine` parameter controls behavior:

- `engine == "stub"`: returns `lambda: []` (no LLM calls).  This
  preserves the current instant-return behavior for users who
  explicitly choose stub mode.
- `engine == "auto"` or `"real"`: returns a provider that spawns 3
  subprocess calls (qodo, expert, adversarial prompts), parses stdout
  as candidate finding JSON, pools and deduplicates by SHA256
  fingerprint, returns `list[StateFinding]`.

The `_run_hold_loop` signature (`cli.py:542`) must be extended with a
`l1_provider` parameter.  The `_run` function (`cli.py:516+`) passes
it through:

```python
l1_provider = build_l1_provider(engine_choice, resolved)
# ... later in _run_hold_loop call:
sm = StateMachine(..., l1_provider=l1_provider, ...)
```

Initial disposition for L1 candidates is `Disposition.UNCERTAIN` (not
CONFIRMED), per the state machine invariant in `disposition.py:21-22`:
"(new) -> CONFIRMED | DISMISSED | UNCERTAIN (set by falsify())".
The falsifier sets the final disposition.

### 3. `llm_invoke(prompt, model)` shim (~80 LOC)

Encapsulates LLM subprocess invocation:

- Default: `claude -p --model <pinned-model> --output-format json`
- Fallback: direct API call via `anthropic` SDK (if installed).
- Model configurable via env var `FORGE_LLM_MODEL` (default:
  `claude-sonnet-4-6`).  Provider selection (`FORGE_LLM_PROVIDER`)
  and `.code-forge/config.yaml` support deferred to M2.
- Timeout handling (default 120s per pass).
- Structured error on failure (exit code, stderr, duration).

**Large prompt handling**: `claude -p` passes the prompt as a CLI
argument, subject to OS `ARG_MAX` (~2MB Linux, lower on macOS).
Full git diffs for large PRs can exceed this.  `llm_invoke` must
detect prompt size and fall back to writing a temp file + passing
`-p "$(<tmpfile)"` when the prompt exceeds 1MB.  Note: `claude -p`
reads from `/dev/tty`, not stdin -- piping via `input=` does NOT
work (confirmed by strace, see memory `reference_aicc_tool`).

**Response validation**: `llm_invoke` returns `Any` (parsed JSON).
Each consumer (RealFalsifier, l1_provider) must validate the response
is a `dict` with the expected keys before calling `.get()`.  If the
LLM returns a JSON array or scalar, `.get()` raises `AttributeError`.
Add `isinstance(response, dict)` guard; return empty/UNCERTAIN on
type mismatch.  LLMs also frequently wrap JSON in markdown fences
(`\`\`\`json ... \`\`\``); strip fences before `json.loads()`.

Aligns with the "provider swap is config-level" vision.

### 4. `code-forge verify` subcommand (~200 LOC)

New subparser in `cli.py`.  Reads receipts from `.code-forge/receipts/`,
runs the 7 validation checks (completeness + hash + anchors + timestamps
+ excerpt + coverage + Jaccard), writes attestation on success.

Exit codes: 0 = PASS, 1 = FAIL (with specific failure reason),
2 = CLI_ERROR.

The CLI handler computes `diff_sha256` via `compute_source_hash()`
from `source.py` (single code path, Option A).  It also parses
the git diff output for `diff_files: dict[str, list[int]]` by
extracting `+++ b/` headers and `@@ +start,count @@` hunks.
This parsing logic is ~30 LOC within the handler.

In Path A, the CLI calls `verify` internally after each round
completes (inside `_run_local`, after `_execute_round`) and before
evaluating `consecutive_clean_rounds`.  In Path C (editor fallback),
the user runs `verify` manually before committing.

### 5. Receipt writer in `_execute_round` (~50 LOC)

The current `_execute_round` (`machine.py:544-568`) writes round data
to `round_history` (in-memory list) and `state.json` (disk), but does
NOT produce individual receipt files.  `verify` (component #4) expects
per-pass receipt JSON files in `.code-forge/receipts/`.

In Path A (CLI orchestrator), the receipt writer runs inside
`_execute_round`, after `_append_round_snapshot` (line 563).  It
writes one receipt per L1 pass (3 per round) with the schema defined
in the spec: cycle, pass, skill, diff_sha256, timestamp, findings,
code_excerpts, covered_line_ranges.  The excerpts and covered ranges
come from the L1 subprocess output (the pass prompt must request them;
the receipt writer extracts and validates them).

In Path C (editor fallback), the SKILL.md instructs the LLM to write
receipts directly.  The receipt writer does not run (no CLI).

This is a new component, not a SKILL.md documentation change.

### 6. Receipt schema in SKILL.md (~40 lines of spec)

Added to the code-forge SKILL.md under a new "Receipt Protocol
(Path C)" section.  Specifies the JSON schema, where to write, and
the validation contract.  This is the Path C counterpart: the LLM
writes receipts that `verify` will check.

### 7. Pre-commit attestation check (extend `install-hooks`, ~20 LOC)

Extend the existing pre-commit hook (written by `install-hooks`) to
delegate to `code-forge verify --quiet` rather than computing a
hash in shell.  This ensures a single hash code path (Option A:
all components use `compute_source_hash` from `source.py`).  If
`code-forge` is not installed, the hook fails closed ("command not
found").  Runs alongside the existing `gate-check`.

### 8. Replace single-fixpoint convergence in `machine.py` (~40 LOC)

The current `_run_local()` loop (`machine.py:423-425`) converges on
the **first** round where `_fixpoint_reached()` returns True:

```python
if self._fixpoint_reached():          # line 423
    self._finalize_local_terminal()   # line 424 -- sets PASS
    return self._state.verdict        # line 425 -- exits immediately
```

This must be **replaced**, not extended.  A counter added after line
425 is unreachable (the method has already returned).  The new behavior:

```python
# REPLACE lines 423-425 with:
if self._fixpoint_reached():
    self._state.consecutive_clean_rounds += 1
else:
    self._state.consecutive_clean_rounds = 0

if self._state.consecutive_clean_rounds >= 3:
    self._finalize_local_terminal()
    return self._state.verdict
# (fall through to HOLD check at line 426)
```

This changes `_run_local()` from "first fixpoint = PASS" to "third
consecutive fixpoint = PASS".  Rounds 1-2 where fixpoint holds still
loop (L0/L1/L2/E2E re-run on unchanged code; LLM gets another
chance to find issues from a fresh context).

`consecutive_clean_rounds` is a new field on the `State` dataclass
(`state.py:96`), serialized in `save_state` (line 241 pattern) and
loaded in `load_state` (line 192 pattern).  Default 0.

The `consecutive_survivor_rounds` pattern (`machine.py:402-421`) is
a structural reference but NOT a template -- survivors count
CONFIRMED mutants (a fail signal), while clean rounds count fixpoints
(a pass signal).  The control flow position differs: survivors are
checked BEFORE fixpoint (line 402), clean rounds REPLACE the fixpoint
early-return (line 423).

Configurable threshold: `FORGE_CLEAN_ROUND_THRESHOLD` env var,
default 3.  Value 1 recovers the current single-fixpoint behavior
for backward compatibility.

---

## Data Flow (Path A, one round)

```
round N:
  L0  linters (existing) -> deterministic CONFIRMED findings
  l1_provider():
    spawn subprocess 1: qodo prompt + diff + Step0 context + prev findings
    spawn subprocess 2: expert prompt + diff + Step0 context + prev findings
    spawn subprocess 3: adversarial prompt + diff + Step0 context + prev findings
    -> pool candidate findings (deduplicate by file:line:description hash)
  falsifier.falsify(each candidate):
    spawn verification subprocess (10-step anti-hallucination)
    -> Disposition (see Glossary)
  merge with L0 findings
  autofix CONFIRMED (existing)
  L2 mutation check (existing)
  E2E coverage heuristic (existing)
  compute round result:
    zero new CONFIRMED/UNCERTAIN (see Glossary) -> consecutive_clean += 1
    otherwise -> consecutive_clean = 0
  consecutive_clean == 3 -> converge (PASS)
  otherwise -> next round (re-review on post-fix code)
```

Each subprocess: real token cost, real timestamp, real duration.
All recorded in `state.json` for post-hoc audit.

---

## Error Handling

| Scenario | Behavior |
|---|---|
| LLM subprocess timeout (>120s) | Record `infra_error`; round does NOT count as clean |
| LLM subprocess crash (nonzero exit) | Record `infra_error`; round does NOT count as clean |
| All 3 L1 passes fail in one round | Record 3 `infra_error`s; l1_provider returns `[]`; log warning "L1 unavailable this round"; round still runs L0/L2/E2E but L1 gap is visible |
| `claude` binary not found | Fall back to API if `ANTHROPIC_API_KEY` set; else ESCALATED |
| `--falsification-engine=stub` | `l1_provider` returns `lambda: []` (no LLM calls); review runs L0/L2/E2E only, same as current behavior |
| Prompt exceeds 1MB | `llm_invoke` writes to temp file, passes via `-p "$(<tmpfile)"` |
| LLM returns non-dict JSON | `llm_invoke` consumer validates `isinstance(response, dict)`; returns UNCERTAIN / empty on mismatch |
| LLM wraps JSON in markdown fences | `llm_invoke` strips fences before `json.loads()` |
| Path C: receipt write fails | SKILL.md reports error; `verify` will fail on missing receipt |
| Path C: attestation stale | Pre-commit hook blocks; user re-runs pipeline or `verify` |
| Path C: excerpt file missing | `verify` check #5 returns FAIL (not silently skipped) |
| Max rounds exhausted | Existing ESCALATED verdict; human intervention required |

Subprocess failure is never silent success.  All 3 L1 passes failing
is never silent bypass -- the round proceeds without L1 but the gap
is logged and visible in `state.json` infra_errors.

---

## Testing Strategy

| Component | Method | Key assertions |
|---|---|---|
| `falsify_real` | Mock `llm_invoke` with fixed output | Disposition mapping correct (see Glossary for values) |
| `l1_provider` | Mock subprocess with fixed candidate JSON | Candidates pooled, deduplicated, returned as `list[StateFinding]` |
| `verify` | Construct 8 receipt sets | (1) complete -> PASS, (2) missing receipt -> FAIL, (3) stale diff hash -> FAIL, (4) timestamp < 30s gap -> FAIL, (5) fabricated excerpt (content mismatch) -> FAIL, (6) excerpt file missing -> FAIL (not silent skip), (7) coverage < 60% -> FAIL, (8) Jaccard > 0.8 on any cycle pair (including c1-c3) -> FAIL |
| convergence replacement | Modified `test_machine_local.py` pattern | 3 consecutive fixpoint rounds -> PASS; finding in round 2 resets counter to 0; threshold=1 env var recovers single-fixpoint behavior |
| receipt writer | `_execute_round` extension | Receipt files appear in `.code-forge/receipts/` after each round; excerpts match real file content |
| End-to-end smoke | Real `claude -p` on minimal diff | Single pass produces parseable candidate JSON; round completes |

---

## Scope Challenge (per CLAUDE.md)

### (a) Does this need to exist?

Yes.  The core product promise ("3 consecutive clean review cycles
before commit") is currently unenforceable in the primary usage
environment (editor).  Shipping with a known-broken enforcement
mechanism erodes trust in the entire pipeline.

### (b) Three real consumers

1. Author's daily VSCode workflow with code-forge (primary trigger
   for this work).
2. Users of the published `code-review-forge` PyPI package running
   reviews in editors.
3. Kernel networking pre-commit review workflow (enforced by
   CLAUDE.md).

### (c) Cost of "do nothing + document"

Document that editor-mode cycles 2-3 are unverified; recommend
CLI-only usage.  This is a viable stopgap (and should be documented
regardless), but it abandons the editor as primary environment and
leaves the published package with a known-broken core promise.

### Explicitly deferred (YAGNI)

- **Risk-graded review depth** (Milestone 2): high-risk files get
  full 3x3, low-risk gets 1 round.  Addresses the "15-line change
  runs 1h23min" pain.
- **Product repositioning** (Milestone 3): deterministic gates as
  commit blockers, LLM findings as advisory.  Direction shift, not
  incremental.
- **Multi-provider voting**: identified as scaffolding to retire, not
  build.
- **New review dimensions**: no new dimensions in this milestone.
- **Sandbox execution for autofixer**: Phase 4 hook, `--sandbox` remains
  a no-op with warning.

---

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Default `consecutive_clean_rounds` | 3 (configurable) | Faithful to existing 3x3 semantic; cost optimization deferred to M2 |
| Subprocess model | `claude -p` (default) / API (fallback) | CLI binary is present in most environments; API covers headless CI |
| Receipt validation strictness | 8 checks (completeness + hash + anchors + timestamps + excerpt + coverage + Jaccard + progressive obligation) | Conservative; false positives (legitimate identical output) are rare and can be overridden |
| Convergence change | Replace single-fixpoint with 3-consecutive-fixpoint (threshold configurable via env) | Behavioral change to `_run_local`; threshold=1 recovers current behavior |
| Diff hash standardization | `compute_source_hash()` from `source.py` everywhere (Option A) | Single code path; receipt writer uses `self.source_hash`, verify imports and calls `compute_source_hash`, hook delegates to `code-forge verify`. No shell hash computation. Three-model consensus (DS/Kimi/Mimo): eliminates silent divergence from newline/encoding differences between Python subprocess and shell pipe |
| Attestation scope | Pipeline completeness, not code correctness | Attestation proves "review ran to completion"; smoke test proves "code works" |
| Falsification subprocess | Separate from pass subprocesses | Reviewer and verifier should be independent contexts |

---

## Migration / Backward Compatibility

- `--falsification-engine=auto` (default) tries `RealFalsifier`,
  falls back to `StubFalsifier`.  Existing behavior preserved.
- `--falsification-engine=stub` continues to work unchanged.
- `code-forge review` without the `--falsification-engine` flag gets
  the new behavior only if `falsify_real.py` is importable (which it
  will be after this milestone ships).
- SKILL.md adds a receipt protocol section; existing SKILL.md
  behavior (self-drive without receipts) continues to work but
  `verify` will fail -- this is intentional.
- Pre-commit hook extension is opt-in via `code-forge install-hooks`.

---

## Glossary

| Term | Definition |
|---|---|
| `Disposition` | Enum in `src/code_forge/disposition.py`. Values: CONFIRMED (real finding), DISMISSED (false positive), UNCERTAIN (needs human judgment). All finding lifecycle transitions use this type. |
| `StateMachine` | Class in `src/code_forge/machine.py`. Drives the L0-L1-L2-E2E loop-until-fixpoint. Holds round state, finding accumulator, and convergence logic. |
| `StateFinding` | Dataclass in `src/code_forge/state.py`. One candidate finding with file, line, severity, dimension, and disposition. |
| `RealFalsifier` | To be implemented in `src/code_forge/falsify_real.py`. Subclass of `Falsifier` ABC (`src/code_forge/falsify.py`). Invokes LLM to verify each L1 candidate, returns a `Disposition`. |
| `NotImplementedError` | Python built-in exception. Currently raised by `build_falsifier("real")` in `src/code_forge/factories.py` because `falsify_real.py` does not exist yet. This milestone removes that exception. |
| `KeyError` / `TypeError` / `AttributeError` | Python built-in exceptions. Referenced in the prompt-construction safety note: `.format()` on untrusted strings containing `{key}` raises `KeyError`; `%d` on `None` raises `TypeError`; `.get()` on non-dict raises `AttributeError`. <!-- plan-forge: p1-ok (Python built-in exceptions, not project identifiers) --> |
| `State` | Dataclass in `src/code_forge/state.py:59`. Holds all persistent review state (findings, dispositions, round_history, consecutive counters). Extended with `consecutive_clean_rounds` in this milestone. |
| Path A | CLI-orchestrated mode: `code-forge review` drives LLM passes as subprocesses; state machine counts cycles mechanically. |
| Path C | Editor-fallback mode: SKILL.md self-drives review; each pass writes a receipt; `code-forge verify` validates completeness post-hoc. |

## References

| Identifier | Location | Description |
|---|---|---|
| Phase 4 | `.planning/MILESTONES.md` | The original milestone that designed `l1_provider` and `Falsifier` extension points but deferred `RealFalsifier` implementation. This spec connects those Phase 4 extension points. |
| Phase 5 | `.planning/MILESTONES.md` | Current milestone. Anti-shirk enforcement: mechanical cycle counting, receipt protocol, verify command, pre-commit attestation. |
| `05-28` | This document header | Publication date of this spec (2026-05-28). Not a version identifier. <!-- plan-forge: p1-ok (date literal in document header) --> |

## Non-Goals for This Document

This spec defines **what to build and why**.  The implementation plan
(task breakdown, file-level changes, dependency ordering, worktree
setup) will be produced separately via the writing-plans process after
this spec is approved.
