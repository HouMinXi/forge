# Phase 14: Outlet C Receipt Gap + Verify Hardening - Research

**Researched:** 2026-06-06
**Domain:** Anti-shirk receipt infrastructure, verify hardening, subagent orchestration
**Confidence:** HIGH

## Summary

Phase 14 closes two anti-shirk gaps: (1) Outlet C (subagent) currently returns `Verdict.PASS` at `cli.py:690-692` without producing any receipts, making `code-forge verify` meaningless for subagent-driven reviews (SHRK-01); (2) verify checks 5 and 6 trust self-reported data (empty `code_excerpts` pass when `findings=0`; `covered_line_ranges` is zero-anchor self-report), allowing zero-cost fabrication (SHRK-04).

The codebase is well-structured for this work. `machine.py` already has the complete StateMachine + write_receipts integration (`machine.py:661-673`). `receipt.py` writes per-pass receipts with `code_excerpts` and `covered_line_ranges`. `verify.py` has 7 checks where checks 5 and 6 need hardening. The `unidiff` library (already a dependency) provides per-hunk parsing with `hunk.target_start`, `hunk.target_length`, `is_binary_file`, `is_rename`, and zero-hunk detection for mode-change diffs. No new dependencies are needed.

**Primary recommendation:** Build `outlet_c.py` as a standalone module that spawns per-pass review sessions, collects structured JSON, feeds findings through `machine.py`'s existing StateMachine (reusing its cycle-reset logic and `write_receipts` call), then harden `verify.py` checks 5 and 6 to anchor on excerpt content against real diff hunks instead of self-reported coverage fields.

<user_constraints>

## User Constraints (from CONTEXT.md)

### Locked Decisions
- SHRK-01: Outlet C routes through StateMachine (machine.py) -- Method B
- Interface: Structured JSON from reviewer, not NL parser
- Mandatory guardrail: fail-closed on schema validation failure
- Architecture: outlet_c.py as separate module (not inlined in cli.py)
- Honest ceiling: prevents process shirking, NOT content fabrication
- SHRK-04 Check 5: Per-hunk excerpt threshold, UNCONDITIONALLY (regardless of findings count)
- SHRK-04 Check 5: verify.py comment must state "per-hunk excerpt is a cost-raiser, not a gate"
- SHRK-04 Check 6: Coverage derived from excerpts touching real diff lines, NOT self-reported covered_line_ranges
- covered_line_ranges: Demote to audit-only annotation or delete entirely
- Check 5 vs Check 6: Same anchor point (excerpts vs real diff), separate responsibilities
- Excerpt comparison baseline: reviewed diff/blob snapshot, not mutable working tree
- outlet_c.py REUSES machine.py cycle-reset (does not reimplement)
- Per-hunk threshold boundary hunks: pure-deletion exempt with explicit flag, binary/rename/mode-change exempt explicitly
- fail-before must be executable (git-checkout or feature-flag), not asserted
- Fabricated receipt test cases: content layer (1-5), mechanical layer (A-B), self-report override (E)

### Claude's Discretion
- None specified -- all decisions locked

### Deferred Ideas (OUT OF SCOPE)
- Content fabrication defense (reviewer lies about findings) -- future phase
- Outlet B (inline) receipt parity -- lower priority

</user_constraints>

<phase_requirements>

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SHRK-01 | Close Outlet C receipt gap -- every review path produces verifiable receipts | machine.py StateMachine API fully mapped (run(), _execute_round(), write_receipts()); cli.py:690-692 stub identified; outlet_c.py architecture defined in CONTEXT.md |
| SHRK-04 | Harden verify ceiling -- check 5 per-hunk excerpt threshold, check 6 excerpt-derived coverage | verify.py checks 5/6 fully analyzed; unidiff per-hunk API verified; parse_diff_files needs per-hunk variant; covered_line_ranges field disposition documented |

</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Outlet C orchestration (spawn loop, JSON parse) | API / Backend (outlet_c.py) | -- | Python controls the spawn loop, JSON schema validation, and fail-closed boundary |
| Receipt writing | API / Backend (receipt.py via machine.py) | -- | machine.py:661-673 already calls write_receipts after each round |
| Verify checks 5/6 | API / Backend (verify.py) | -- | Pure Python validation of receipt JSON against diff structure |
| Per-hunk diff parsing | API / Backend (verify.py or new helper) | diff.py (unidiff wrapper) | unidiff PatchSet provides hunk-level access; verify.py needs a per-hunk variant of parse_diff_files |
| CLI integration (outlet dispatch) | API / Backend (cli.py) | -- | cli.py:690-692 stub replaced with outlet_c.py call |

## Standard Stack

### Core (already in project)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| unidiff | >=0.7.5,<0.8.0 | Per-hunk diff parsing | Already a dependency (pyproject.toml); provides PatchSet -> PatchedFile -> Hunk -> Line with target_start, target_length, is_binary_file, is_rename [VERIFIED: pyproject.toml] |
| pytest | >=8.0 | Test framework | Already in dev dependencies [VERIFIED: pyproject.toml] |

### No New Dependencies Needed

This phase requires no new packages. All functionality builds on existing `unidiff` for per-hunk parsing and standard library `json` for schema validation.

## Architecture Patterns

### System Architecture Diagram

```
User runs `code-forge review --outlet subagent`
     |
     v
cli.py:690 --- outlet == "subagent" ---> outlet_c.py.run_outlet_c()
     |                                        |
     |                                   [For each round 0..N]
     |                                        |
     |                                   [For each pass: qodo, expert, adversarial]
     |                                        |
     |                                   Spawn fresh reviewer session
     |                                        |
     |                                   Collect structured JSON
     |                                        |
     |                                   JSON schema validate (fail-closed)
     |                                        |
     |                                   Convert to StateFinding[]
     |                                        |
     |                                   Feed to StateMachine._execute_round()
     |                                        |  (which calls write_receipts)
     |                                        |
     |                                   StateMachine cycle-reset logic
     |                                        |
     |                                   3 consecutive clean -> Verdict.PASS
     |
     v
code-forge verify
     |
     v
verify.py:run_verify()
     |
     +-- Check 1: 9 receipts, cycle/pass matrix
     +-- Check 2: diff hash
     +-- Check 3: anchor file in diff
     +-- Check 4: monotonic timestamps
     +-- Check 5 (HARDENED): per-hunk excerpt threshold
     |      |
     |      +-- parse diff into per-hunk structure
     |      +-- for each hunk: at least 1 excerpt touching it
     |      +-- pure-deletion/binary/rename hunks: explicit exemption
     |      +-- excerpt content compared against reviewed snapshot
     |
     +-- Check 6 (HARDENED): excerpt-derived coverage >= 60%
     |      |
     |      +-- collect all (file, line) pairs from code_excerpts
     |      +-- intersect with diff lines
     |      +-- coverage = intersection / diff lines
     |      +-- covered_line_ranges IGNORED (audit-only)
     |
     +-- Check 7: Jaccard overlap < 0.8
```

### Recommended Project Structure

```
src/code_forge/
  outlet_c.py          # NEW: Outlet C orchestrator (spawn loop + JSON parse + SM feed)
  machine.py           # EXISTING: StateMachine (reused, not modified for cycle logic)
  receipt.py           # EXISTING: write_receipts (needs per-hunk excerpt generation)
  verify.py            # MODIFIED: checks 5/6 hardened
  diff.py              # EXISTING: may add parse_diff_hunks helper
tests/
  test_outlet_c.py     # NEW: unit tests for outlet_c
  test_verify.py       # MODIFIED: add fabricated receipt test cases
  test_receipt.py      # EXISTING: may need updates if receipt schema changes
```

### Pattern 1: StateMachine Reuse (outlet_c.py feeds machine.py)

**What:** outlet_c.py creates a StateMachine instance with a custom `l1_provider` that returns findings from the structured JSON collected from each reviewer pass.

**When to use:** Whenever Outlet C needs to produce receipts identical to Outlet A.

**How it works:** The key insight from `machine.py:661-673` is that `_execute_round()` calls `write_receipts()` after merging all findings. If `outlet_c.py` constructs a StateMachine with the correct `l1_provider`, the receipt writing happens automatically through the existing code path.

```python
# Source: machine.py:157-167, machine.py:661-673
# StateMachine.run() -> _run_local() -> _execute_round() -> write_receipts()
#
# outlet_c.py would:
# 1. Build an l1_provider that spawns reviewers and returns StateFinding[]
# 2. Construct StateMachine(l1_provider=custom_provider, ...)
# 3. Call sm.run() -- machine.py handles cycle counting + receipt writing

def _build_l1_provider_for_outlet_c(
    diff_text: str,
    pass_prompts: list[str],
    spawn_fn: Callable,
) -> L1Provider:
    """Returns an l1_provider that spawns fresh reviewers per round."""
    pass_index = {"n": 0}

    def provider() -> tuple[list[StateFinding], Usage, float]:
        findings = []
        for pass_name in ["qodo", "expert", "adversarial"]:
            raw_json = spawn_fn(pass_name, diff_text, pass_prompts)
            validated = _validate_reviewer_json(raw_json)  # fail-closed
            findings.extend(_json_to_state_findings(validated, pass_name))
        pass_index["n"] += 1
        return findings, Usage(), 0.0

    return provider
```

### Pattern 2: Per-Hunk Diff Parsing (for verify check 5)

**What:** Parse diff text into per-hunk structure: `{file: [{start, end, added_lines, is_deletion_only}]}`.

**When to use:** Check 5 needs to verify that each hunk has at least 1 excerpt.

```python
# Source: verified with unidiff library (already a dependency)
import unidiff

def parse_diff_hunks(diff_text: str) -> dict[str, list[dict]]:
    """Parse diff into per-hunk structure.

    Returns:
        {file: [{"start": int, "end": int, "added_lines": [int],
                 "is_deletion_only": bool}]}

    Binary files, rename-only, and mode-change files have 0 hunks
    and are handled by the caller (explicit exemption).
    """
    patchset = unidiff.PatchSet(diff_text)
    result = {}
    for pf in patchset:
        if pf.is_removed_file:
            continue
        if getattr(pf, "is_binary_file", False) or (
            pf.is_rename and len(list(pf)) == 0
        ):
            continue
        hunks = []
        for hunk in pf:
            added = [l.target_line_no for l in hunk if l.is_added]
            all_target = [l.target_line_no for l in hunk
                          if l.target_line_no is not None]
            hunks.append({
                "start": hunk.target_start,
                "end": hunk.target_start + hunk.target_length - 1,
                "added_lines": added,
                "is_deletion_only": len(added) == 0,
            })
        if hunks:
            result[pf.path] = hunks
    return result
```

### Pattern 3: Fail-Closed JSON Schema Validation

**What:** Validate reviewer JSON against expected schema; treat any failure as FAIL.

**When to use:** Outlet C JSON parse from reviewer output.

```python
# Based on CONTEXT.md decision: fail-closed guardrail
_REQUIRED_FIELDS = {"findings"}
_FINDING_REQUIRED = {"file", "line", "severity", "description"}
_VALID_SEVERITIES = {"P0", "P1", "P2", "P3"}

def validate_reviewer_json(raw: str) -> dict:
    """Parse and validate reviewer JSON. Raises ValueError on any failure."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError("reviewer output is not valid JSON: %s" % e)

    if not isinstance(data, dict):
        raise ValueError("reviewer output is not a JSON object")

    for field in _REQUIRED_FIELDS:
        if field not in data:
            raise ValueError("missing required field: %s" % field)

    if not isinstance(data["findings"], list):
        raise ValueError("findings must be a list")

    for i, f in enumerate(data["findings"]):
        for field in _FINDING_REQUIRED:
            if field not in f:
                raise ValueError(
                    "finding[%d] missing field: %s" % (i, field))
        if f["severity"] not in _VALID_SEVERITIES:
            raise ValueError(
                "finding[%d] invalid severity: %s" % (i, f["severity"]))

    return data
```

### Pattern 4: Excerpt-Derived Coverage (replacing covered_line_ranges)

**What:** Compute coverage from `code_excerpts` touching real diff lines, not from self-reported `covered_line_ranges`.

```python
# Replaces _covered() and _cycle_covered() in verify.py
def _excerpt_covered(receipt: dict) -> set[tuple[str, int]]:
    """Coverage set derived from code_excerpts (verifiable content)."""
    s = set()
    for exc in receipt.get("code_excerpts", []):
        for ln in range(exc["start_line"], exc["end_line"] + 1):
            s.add((exc["file"], ln))
    return s

def _cycle_excerpt_covered(
    receipts: list[dict], cycle: int
) -> set[tuple[str, int]]:
    u = set()
    for r in receipts:
        if r["cycle"] == cycle:
            u |= _excerpt_covered(r)
    return u
```

### Anti-Patterns to Avoid

- **Reimplementing cycle counting in outlet_c.py:** The CONTEXT.md explicitly locks this. outlet_c.py MUST feed findings to StateMachine and let machine.py handle `consecutive_clean_rounds` reset-on-finding.

- **Coupling check 5 and check 6 on the same mechanism:** The CONTEXT.md explicitly rejected option 2 (60% line coverage for both). Check 5 = "each hunk has a witness" (binary). Check 6 = "witnessed lines cover >= 60% of diff" (quantitative). Same anchor (excerpts vs diff), separate responsibilities.

- **Comparing excerpts against the mutable working tree:** CONTEXT.md decision: compare against the reviewed snapshot. Since `write_receipts()` captures excerpt content at write time, the receipt already stores the snapshot content. Check 5 should compare the stored excerpt content against the stored receipt content (self-consistency) or diff blob reconstruction.

- **Silent fall-through for boundary hunks:** CONTEXT.md requires explicit exemption, not silent pass. Binary diffs, rename-only, and mode-change hunks must be flagged as exempt in the check result, not silently skipped.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Diff hunk parsing | Custom regex parser for @@ headers | `unidiff.PatchSet` -> iterate hunks | Already a dependency; handles edge cases (binary, rename, mode-change) |
| Cycle counting | Custom counter in outlet_c.py | `machine.py` StateMachine.run() | CONTEXT.md locked decision; machine.py already handles consecutive_clean_rounds |
| Receipt writing | Manual JSON construction in outlet_c.py | `receipt.py:write_receipts()` called by machine.py | Already integrated into _execute_round() |
| JSON schema validation | ad-hoc field checking | Centralized validate function with fail-closed semantics | CONTEXT.md requires fail-closed |

## Common Pitfalls

### Pitfall 1: StateMachine Constructor Wiring
**What goes wrong:** outlet_c.py constructs a StateMachine but forgets to pass `resolved_review` with a real `git_diff`, causing `parse_diff_files` to return None and receipts to have empty `covered_line_ranges` / no diff-anchored excerpts.
**Why it happens:** StateMachine requires `resolved_review` (with `git_diff`) and `source_hash` from the same diff computation. These must be threaded from cli.py's diff capture.
**How to avoid:** outlet_c.py receives the pre-computed `resolved_review` and `source_hash` from cli.py (same values Outlet A uses). The StateMachine constructor signature at `machine.py:105-148` documents all required parameters.
**Warning signs:** Receipts have empty `code_excerpts` or `covered_line_ranges` that reference non-diff files.

### Pitfall 2: l1_provider Timing -- All 3 Passes Per Round
**What goes wrong:** The l1_provider is called once per `_execute_round()`. If it only spawns 1 reviewer pass, the receipts will have findings for only 1 of 3 passes, and `_split_by_pass()` in receipt.py will produce 2 empty receipts.
**Why it happens:** machine.py calls `l1_provider()` once per round (line 516). The l1_provider must return findings for all 3 passes in a single call.
**How to avoid:** The l1_provider spawns all 3 reviewer passes (qodo, expert, adversarial) internally and returns the merged list. Each finding's `id` must be prefixed with `l1-<pass_name>-` so `receipt.py:_split_by_pass()` can route them correctly.
**Warning signs:** `receipt-cNpM.json` files where some passes have 0 findings but others have many.

### Pitfall 3: Excerpt Snapshot vs Working Tree Race
**What goes wrong:** Check 5 excerpt verification reads from `cwd / exc["file"]` (working tree). If the file changed between review and verify (fix -> re-review loop), honest excerpts fail verbatim comparison.
**Why it happens:** CONTEXT.md decision: compare against the reviewed snapshot, not the working tree.
**How to avoid:** receipt.py already captures `content` at write time -- check 5 verifies internal consistency (content matches what the reviewer actually saw), trusting that receipt.py read the file correctly.
**Warning signs:** Excerpt mismatch failures on honest reviews after a fix-and-re-review cycle.

### Pitfall 4: Per-Hunk Threshold on Deletion-Only Hunks
**What goes wrong:** A deletion-only hunk has `target_length=0` (no added lines). Requiring an excerpt of added code is impossible.
**Why it happens:** The CONTEXT.md decision says pure-deletion hunks should be satisfied by an excerpt of the deleted region OR explicitly exempt.
**How to avoid:** Flag deletion-only hunks (`is_deletion_only=True`). Check 5 either accepts an excerpt referencing lines near the deletion site, or exempts the hunk with an explicit flag. The exemption must be auditable (not silent).
**Warning signs:** Legitimate reviews failing check 5 on files that only had lines removed.

### Pitfall 5: Verify Hardening Breaks Existing Receipt Flow
**What goes wrong:** Hardened check 5 (per-hunk requirement) or check 6 (excerpt-derived coverage) causes existing Outlet A receipts to fail verify.
**Why it happens:** Current receipt.py `_build_excerpts()` only creates excerpts for findings. All-clean rounds produce receipts with empty `code_excerpts`.
**How to avoid:** receipt.py must produce per-hunk excerpts unconditionally (even for clean passes). This ensures every hunk has at least 1 excerpt. The CONTEXT.md test cases 1+3 assume unconditional excerpts.
**Warning signs:** `test_all_clean_run_passes_verify` and `test_receipt_writer_output_passes_verify` break after verify hardening.

### Pitfall 6: fail-before Test Using git-checkout
**What goes wrong:** Tests that checkout pre-Phase-14 verify.py may break if verify.py imports change.
**Why it happens:** The CONTEXT.md decision requires executable fail-before (pre-hardening verify must be runnable).
**How to avoid:** Use a feature flag approach instead of git-checkout. Add a `hardened` parameter to `run_verify()` (default True). Tests can call with `hardened=False` to get pre-hardening behavior.
**Warning signs:** Import errors in fail-before tests.

## Code Examples

### Current receipt.py write_receipts Signature
```python
# Source: receipt.py:69-77
def write_receipts(
    receipts_dir: Path,
    round_index: int,
    l1_findings: list[StateFinding],
    diff_sha256: str,
    source_files: list[Path],
    cwd: Path,
    diff_files: dict[str, list[int]] | None = None,
) -> list[Path]:
```

### Current receipt JSON Schema (receipt-cNpM.json)
```json
{
  "cycle": 1,
  "pass": 1,
  "skill": "qodo-review",
  "diff_sha256": "sha256hex",
  "timestamp": "2026-05-28T10:04:00+00:00",
  "findings_count": 0,
  "findings": [
    {"file": "src/f.py", "line": 42, "description": "...", "disposition": "CONFIRMED"}
  ],
  "anchors": [
    {"file": "src/f.py", "line": 42, "text": "def f():"}
  ],
  "code_excerpts": [
    {"file": "src/f.py", "start_line": 40, "end_line": 45,
     "content": "...\n", "rationale": "..."}
  ],
  "covered_line_ranges": [
    {"file": "src/f.py", "start": 32, "end": 52}
  ]
}
```

### Current verify.py Check 5 (to be hardened)
```python
# Source: verify.py:128-156
# Current: iterates code_excerpts per receipt, verifies file exists
# and content matches. Does NOT check per-hunk coverage.
# When findings=0, code_excerpts=[] passes silently.
for r in receipts:
    for exc in r.get("code_excerpts", []):
        fp = cwd / exc["file"]
        if not fp.exists():
            return VerifyResult(False, ...)
        actual = "\n".join(lines[start:end]) + "\n"
        if actual != claimed:
            return VerifyResult(False, ...)
```

### Current verify.py Check 6 (to be hardened)
```python
# Source: verify.py:158-165
# Current: uses _covered() which reads covered_line_ranges (self-reported)
all_diff = {(f, ln) for f, lns in diff_files.items() for ln in lns}
if all_diff:
    for c in range(1, 4):
        cov = _cycle_covered(receipts, c) & all_diff
        if len(cov) / len(all_diff) < 0.6:
            return VerifyResult(False, ...)
```

### Current _covered() -- Uses Self-Reported Field
```python
# Source: verify.py:60-65
def _covered(receipt: dict) -> set[tuple[str, int]]:
    s = set()
    for r in receipt.get("covered_line_ranges", []):
        for ln in range(r["start"], r["end"] + 1):
            s.add((r["file"], ln))
    return s
```

### StateMachine._execute_round() Receipt Writing
```python
# Source: machine.py:661-673
from .receipt import write_receipts
from .verify import parse_diff_files
diff_text = self.resolved_review.git_diff
diff_files = parse_diff_files(diff_text) if diff_text else None
write_receipts(
    receipts_dir=self.cwd / ".code-forge" / "receipts",
    round_index=round_index,
    l1_findings=l1_findings,
    diff_sha256=self.source_hash,
    source_files=list(self._source_files()),
    cwd=self.cwd,
    diff_files=diff_files,
)
```

### SKILL.md Outlet C Reviewer JSON Schema
```
// Source: SKILL.md:1409
// Each reviewer pass must return:
{"findings": [{"file": "src/module.py", "line": 42,
  "severity": "P0|P1|P2|P3", "description": "..."}]}
```

### unidiff Per-Hunk API (verified)
```python
# Source: verified via interactive test against unidiff (pyproject.toml dep)
import unidiff
patchset = unidiff.PatchSet(diff_text)
for patched_file in patchset:
    # patched_file.path -> "src/f.py"
    # patched_file.is_binary_file -> True for binary diffs
    # patched_file.is_rename -> True for rename-only
    # len(list(patched_file)) == 0 for binary/rename/mode-change
    for hunk in patched_file:
        # hunk.target_start -> first target line number
        # hunk.target_length -> number of target lines
        added_lines = [l.target_line_no for l in hunk if l.is_added]
        is_deletion_only = len(added_lines) == 0
```

### Existing Test Patterns (for reuse)
```python
# Source: tests/test_consecutive_clean.py -- StateMachine with custom l1_provider
sm = StateMachine(
    mode=Mode.LOCAL, falsifier=StubFalsifier(),
    autofixer=StubAutoFixer(), revert_fn=lambda f: None,
    resolved_review=_resolved(), source_hash="a",
    baseline_spec_repr="HEAD", cwd=tmp_path, registry={},
    l1_provider=lambda: ([], Usage(), 0.0), max_total_rounds=10,
)
assert sm.run() == Verdict.PASS

# Source: tests/test_verify.py -- receipt factory
def _receipt(cycle, pass_n, diff_sha, covered_start=1, covered_end=50):
    return {"cycle": cycle, "pass": pass_n,
        "skill": ["qodo-review","code-review-expert","adversarial-qe"][pass_n-1],
        "diff_sha256": diff_sha,
        "timestamp": "2026-05-28T10:%02d:00Z" % (cycle * 3 + pass_n),
        "findings_count": 0, "findings": [], "anchors": [...],
        "code_excerpts": [...], "covered_line_ranges": [...]}
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| verify check 5 skips empty excerpts when findings=0 | Per-hunk excerpt requirement regardless of findings count | Phase 14 | Closes zero-cost fabrication channel |
| verify check 6 trusts self-reported covered_line_ranges | Excerpt-derived coverage (verifiable content) | Phase 14 | Removes zero-anchor self-report from verification |
| Outlet C returns Verdict.PASS early (cli.py:690-692) | Outlet C routes through StateMachine for receipts | Phase 14 | Every outlet produces verifiable receipts |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Feature flag approach for fail-before tests is simpler than git-checkout | Pitfall 6 | Low -- git-checkout also works but is more fragile; either approach is valid |
| A2 | receipt.py needs to produce per-hunk excerpts unconditionally (not just for findings) to satisfy hardened check 5 | Pitfall 5 | Medium -- if check 5 is gated differently, receipt.py changes differ; but CONTEXT.md test cases 1+3 assume unconditional excerpts |
| A3 | outlet_c.py can reuse StateMachine directly with a custom l1_provider | Pattern 1 | Low -- machine.py constructor is well-documented and test_consecutive_clean.py demonstrates exactly this pattern |

## Open Questions

1. **Outlet C Spawn Mechanism**
   - What we know: SKILL.md says "Spawn a fresh Agent (using the Agent tool)" but outlet_c.py is a Python module. outlet_c.py needs a programmatic way to spawn reviewer sessions.
   - What is unclear: Should outlet_c.py use `subprocess.run(["claude", "-p", ...])` like Outlet A, or use a different mechanism?
   - Recommendation: outlet_c.py likely needs to use the `llm_invoke` module or a new callable injected from cli.py. Two options: (a) outlet_c.py uses llm_invoke with per-pass isolation, or (b) outlet_c.py is a thin wrapper that SKILL.md calls per-pass. Option (b) aligns with the SKILL.md Outlet C protocol.

2. **Excerpt Content for Clean Passes**
   - What we know: Current receipt.py `_build_excerpts()` only produces excerpts from findings. Clean passes (0 findings) have empty code_excerpts.
   - What is unclear: For per-hunk check 5, clean passes need excerpts too.
   - Recommendation: receipt.py should generate one excerpt per diff hunk (reading actual file content at those lines), regardless of whether findings exist. Excerpt `rationale` field = "reviewed (no findings)" for clean passes.

3. **Snapshot Comparison Implementation**
   - What we know: CONTEXT.md says compare excerpt against the reviewed snapshot. The receipt already stores `content`.
   - What is unclear: Should check 5 verify stored content matches the file at review time?
   - Recommendation: For the cost-raiser goal, verify that excerpt content matches the current file content OR the reconstructed blob from the diff. The critical point: excerpts must contain REAL code lines from the diff, not garbage.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest >=8.0 |
| Config file | pyproject.toml (via setuptools) |
| Quick run command | `python -m pytest tests/test_verify.py tests/test_receipt.py -x -q` |
| Full suite command | `python -m pytest -x -q` |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SHRK-01 | Outlet C produces receipts through StateMachine | unit | `pytest tests/test_outlet_c.py -x -q` | Wave 0 |
| SHRK-01 | Outlet C fail-closed on malformed JSON (test case A) | unit | `pytest tests/test_outlet_c.py::test_malformed_json_fail_closed -x -q` | Wave 0 |
| SHRK-01 | Outlet C cycle counting via StateMachine (test case B) | unit | `pytest tests/test_outlet_c.py::test_cycle_counting -x -q` | Wave 0 |
| SHRK-04 | Check 5 per-hunk blocks all-green no excerpt (test case 1) | unit | `pytest tests/test_verify.py::test_all_green_no_excerpt -x -q` | Wave 0 |
| SHRK-04 | Check 5 per-hunk blocks excerpts piled in one hunk (test case 3) | unit | `pytest tests/test_verify.py::test_excerpts_piled_one_hunk -x -q` | Wave 0 |
| SHRK-04 | Check 5 per-hunk blocks findings>0 unwitnessed hunk (test case 5) | unit | `pytest tests/test_verify.py::test_findings_unwitnessed_hunk -x -q` | Wave 0 |
| SHRK-04 | Check 5 excerpt content mismatch (test case 2) | unit | `pytest tests/test_verify.py -x -q` | Exists (partially) |
| SHRK-04 | Check 6 excerpt coverage below 60% (test case 4) | unit | `pytest tests/test_verify.py::test_excerpt_coverage_below_60 -x -q` | Wave 0 |
| SHRK-04 | Check 6 ignores covered_line_ranges (test case E) | unit | `pytest tests/test_verify.py::test_self_report_override -x -q` | Wave 0 |
| SHRK-04 | Existing receipt writer output passes hardened verify | integration | `pytest tests/test_verify.py::TestReceiptVerifyE2E -x -q` | Exists (needs update) |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/test_verify.py tests/test_receipt.py tests/test_outlet_c.py -x -q`
- **Per wave merge:** `python -m pytest -x -q`
- **Phase gate:** Full suite green (1084+ tests) before verify-work

### Wave 0 Gaps
- [ ] `tests/test_outlet_c.py` -- covers SHRK-01 (new file)
- [ ] Fabricated receipt test cases in `tests/test_verify.py` -- covers SHRK-04 test cases 1-5, A, B, E
- [ ] Update `tests/test_verify.py::TestReceiptVerifyE2E` for hardened check 5/6
- [ ] Update `tests/test_consecutive_clean.py::test_all_clean_run_passes_verify` for per-hunk excerpts

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | -- |
| V3 Session Management | no | -- |
| V4 Access Control | no | -- |
| V5 Input Validation | yes | JSON schema validation with fail-closed on reviewer output |
| V6 Cryptography | no | -- |

### Known Threat Patterns for This Stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Malformed JSON injection from reviewer | Tampering | Strict schema validation + fail-closed |
| Self-reported coverage inflation | Elevation of Privilege | Replace covered_line_ranges with excerpt-derived coverage |
| Zero-cost fabrication (empty receipts pass) | Spoofing | Per-hunk excerpt threshold |
| Process shirking (claim N cycles without running) | Spoofing | Python spawn loop + StateMachine cycle counting |

## Sources

### Primary (HIGH confidence)
- `src/code_forge/machine.py` -- StateMachine API, _execute_round(), write_receipts() integration (read directly)
- `src/code_forge/receipt.py` -- write_receipts() signature, receipt JSON schema, _split_by_pass(), _build_excerpts() (read directly)
- `src/code_forge/verify.py` -- run_verify(), _covered(), _cycle_covered(), checks 5/6 (read directly)
- `src/code_forge/diff.py` -- extract_changed_lines(), unidiff usage (read directly)
- `src/code_forge/cli.py:687-693` -- Outlet C stub (read directly)
- `src/code_forge/skills/code-forge/SKILL.md:1393-1431` -- Outlet C protocol (read directly)
- `tests/test_verify.py`, `tests/test_receipt.py`, `tests/test_consecutive_clean.py` -- test patterns (read directly)
- `unidiff` library API -- per-hunk parsing verified via interactive test

### Secondary (MEDIUM confidence)
- `pyproject.toml` -- unidiff dependency constraint >=0.7.5,<0.8.0 (read directly)

### Tertiary (LOW confidence)
- None

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new dependencies; all tools already in project
- Architecture: HIGH -- machine.py StateMachine API fully documented; test patterns confirm reuse approach
- Pitfalls: HIGH -- all identified from direct code reading and CONTEXT.md analysis
- Verify hardening: HIGH -- current checks 5/6 read directly; per-hunk unidiff API verified interactively

**Research date:** 2026-06-06
**Valid until:** 2026-07-06 (stable internal codebase, no external API dependencies)
