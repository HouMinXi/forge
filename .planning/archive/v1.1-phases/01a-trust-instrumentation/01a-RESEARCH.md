# Phase 1a: Trust Instrumentation - Research

**Researched:** 2026-05-12
**Domain:** State machine modification, JSON persistence, CLI wrapper, token metering
**Confidence:** HIGH

## Summary

Phase 1a transforms forge from a review pipeline with no memory into one that tracks every finding, its outcome, and the cost of producing it. The core technical challenge is modifying a 417-line SKILL.md state machine (which currently uses ephemeral `cycle_counter` state in Claude's conversation context) to persist findings to `.forge/findings.json`, gate cycle resets on severity, auto-continue clean passes, and inject Step 0 findings into LLM pass prompts.

The secondary challenge is building a Python CLI wrapper that invokes `claude -p` with the forge SKILL.md loaded as a system prompt. Claude Code's `--output-format json` mode already reports `total_cost_usd`, `input_tokens`, `output_tokens`, `cache_read_input_tokens`, and `cache_creation_input_tokens` per invocation -- this is the token metering data source. The wrapper does not need to parse LLM output for token counts; it reads them from the CLI's structured JSON result.

The tertiary challenge is bootstrapping the findings database with 15 classified FP instances from historical analysis (already extracted to `/tmp/draft_20260512_historical_review_analysis.txt`), converting them to the D1 schema.

**Primary recommendation:** Implement in three waves: (1) findings.json schema + SKILL.md state machine changes (TRUST-01, TRUST-06, TRUST-07, LEARN-07-LITE), (2) Step 0 -> LLM context fusion (FUSE-01), (3) CLI wrapper + cost metering (CLI-01, TRUST-05).

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- D1: JSON file at `.forge/findings.json`, permanent retention, no TTL. Schema defined with minimum viable fields (id, timestamp, file, line, dimension, pass, cycle, severity, description, outcome, reject_reason, commit_sha, cost_tokens).
- D2: 6-category FP taxonomy (HALLUCINATION, CONTEXT_MISSING, INTENTIONAL, NOT_APPLICABLE, STYLE_PREFERENCE, ACCEPTABLE_RISK).
- D3: Severity-gated cycle reset -- P0/P1 full reset, P2 current cycle restart, P3 accumulate without interrupt.
- D4: Auto-continue on clean pass -- zero-finding passes are silent transitions, only pause when findings exist.
- D5: Binary accept/reject per finding with 6-category reject_reason taxonomy.
- D6: Step 0 findings serialized as context block appended to LLM pass prompts (Steps 1-3).
- D7: Python CLI wrapper invoking `claude -p` with forge SKILL.md as system prompt. Not a standalone reimplementation.
- D8: Track token count (input + output) per pass and estimated cost per run. Model pricing as config.
- D9: Per-dimension escalation is a design principle, not a Phase 1a deliverable. Phase 1a records per-dimension data.
- D10: Outdated rate deferred to Phase 1b+.

### Claude's Discretion
- Dashboard format (terminal table, markdown report, or JSON dump)
- Finding ID generation strategy (UUID v4, sequential, or hash-based)
- Step 0 finding serialization format for FUSE-01
- Implementation details of auto-continue flow within SKILL.md

### Deferred Ideas (OUT OF SCOPE)
- D10: Outdated rate tracking (git history correlation) -- deferred to Phase 1b+
- LEARN-07-FULL: Threshold tuning via RLHF
- Automatic FP rate-based suppression
- CI/CD integration mode (CLI-02)
- GitHub Actions workflow
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| TRUST-01 | FP tracking -- record every finding with accept/reject outcome, persist across sessions | D1 schema verified, JSON file I/O patterns documented, historical bootstrap data available (15 instances) |
| TRUST-05 | FP rate dashboard -- report per-dimension FP rate | Dashboard can be computed from findings.json aggregation; recommend terminal table via Python `format()` |
| TRUST-06 | Auto-continue on clean pass | SKILL.md state machine modification documented; requires explicit "zero findings -> proceed" instruction in pipeline protocol |
| TRUST-07 | Severity-gated cycle reset | D3 decision mapped to state machine transitions; requires severity field on every finding |
| LEARN-07-LITE | Binary feedback collection with 6-category reject_reason | D5 decision mapped to findings.json schema; accept/reject flow in SKILL.md |
| FUSE-01 | Step 0 -> LLM context fusion | Serialization format designed; `--append-system-prompt` pattern verified for CLI mode |
| CLI-01 | Standalone CLI wrapper | `claude -p --append-system-prompt-file` verified as the mechanism; JSON output provides token/cost data |
</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Finding persistence (.forge/findings.json) | Local filesystem | -- | Single-user CLI tool, JSON file is the only storage |
| State machine (cycle counter, severity gates) | SKILL.md (LLM instruction) | -- | Claude interprets SKILL.md; no runtime code |
| Auto-continue logic | SKILL.md (LLM instruction) | -- | Instructions to LLM, not executable code |
| Step 0 finding serialization (FUSE-01) | SKILL.md (LLM instruction) | -- | Forge skill reads Step 0 output and formats for context injection |
| CLI wrapper | Python script | Claude Code CLI | Python invokes `claude -p`; Claude Code does the review |
| Token metering | Claude Code CLI (--output-format json) | Python CLI wrapper | CLI provides raw data; wrapper parses and stores |
| FP dashboard | Python CLI wrapper | -- | Aggregation logic in Python; display in terminal |
| Historical data bootstrap | One-time Python script | -- | Convert analysis file to findings.json schema |

## Standard Stack

### Core
| Component | Version/Tech | Purpose | Why Standard |
|-----------|-------------|---------|--------------|
| SKILL.md | Markdown (Claude Code skill format) | Review pipeline state machine | Only format Claude Code skills support [VERIFIED: `--help` output confirms `--disable-slash-commands` and skill resolution] |
| Python 3 | 3.14.4 (installed) | CLI wrapper, dashboard, bootstrap | Available on target machine [VERIFIED: `python3 --version`] |
| Claude Code CLI | 2.1.139 | Headless invocation of forge | Installed on target [VERIFIED: `claude --version`] |
| JSON | stdlib `json` module | findings.json I/O | No external dependencies [VERIFIED: import test] |
| UUID | stdlib `uuid` module | Finding ID generation | No external dependencies [VERIFIED: import test] |

### Supporting
| Component | Version | Purpose | When to Use |
|-----------|---------|---------|-------------|
| `argparse` | stdlib | CLI argument parsing | In the Python wrapper |
| `subprocess` | stdlib | Invoke `claude -p` | In the Python wrapper |
| `os.path` / `pathlib` | stdlib | Path resolution for .forge/ directory | In all Python code |
| `datetime` | stdlib | ISO-8601 timestamps | In finding records |
| `textwrap` / string formatting | stdlib | Terminal table rendering | In dashboard output |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| JSON file | SQLite | Overkill for single-user; D1 explicitly chose JSON |
| UUID v4 | Sequential counter | UUID survives file corruption/truncation; no collision risk |
| UUID v4 | Content hash (SHA-256 of description+file+line) | Hash enables dedup but collides on identical findings across cycles |
| Python wrapper | Shell wrapper | Python handles JSON parsing, subprocess management, error handling better |
| `format()` tables | `rich` / `tabulate` | External dependency; stdlib is sufficient for simple tables |

**Recommendation:** Use UUID v4 for finding IDs. It requires no coordination, survives append-only file corruption, and is trivial to generate with `uuid.uuid4()`. [ASSUMED]

## Architecture Patterns

### System Architecture Diagram

```
User invokes forge (interactive or CLI)
         |
         v
  [Step 0: Deterministic]
  bash -n, shellcheck, pylint, semgrep, non-ASCII
         |
         +---> findings serialized to JSON context block
         |
         v
  [Steps 1-3: LLM Review Cycles]     <-- receives Step 0 context (FUSE-01)
  cycle_counter state machine:
    Pass 1 (qodo-review) ---> findings recorded
    Pass 2 (code-review-expert) ---> findings recorded
    Pass 3 (adversarial-qe) ---> findings recorded
         |
         +---> Per-finding: severity classification (P0/P1/P2/P3)
         |
         +---> If zero findings: auto-continue (TRUST-06)
         |     If P0/P1 finding: cycle_counter = 0 (full reset)
         |     If P2 finding: restart current cycle only
         |     If P3 only: accumulate, no interrupt
         |
         v
  [Step 3.5: FP Verification]  (if findings were fixed)
         |
         v
  [Step 4: Smoke Test]
         |
         v
  [User Feedback Collection]
  For each finding: accept or reject?
  If reject: which category? (6 options)
         |
         v
  [Persist to .forge/findings.json]   <-- append-only writes
         |
         v
  [Dashboard: forge --stats]
  Per-dimension FP rate table
  Cost per run summary
```

### Data Flow: findings.json

```
Finding Created              Finding Classified            Finding Persisted
(during review pass)   --->  (severity + outcome)    --->  (.forge/findings.json)
                                                               |
                                                               v
                                                          Dashboard Query
                                                          (forge --stats)
```

### Recommended Project Structure

```
forge/
  skills/
    forge/
      SKILL.md              # Modified: state machine + finding tracking + FUSE-01
  hooks/
    check_review_tracker.sh  # Modified: severity-aware state tracking
  cli/
    forge_cli.py             # NEW: Python CLI wrapper
    config.json              # NEW: model pricing config
  .forge/                    # NEW: per-project data directory (gitignored)
    findings.json            # NEW: finding persistence (D1 schema)
  bootstrap/
    convert_historical.py    # NEW: one-time historical data bootstrap script
```

### Pattern 1: Finding Recording in SKILL.md

**What:** After each pass reports findings, forge writes each finding to `.forge/findings.json` using shell commands embedded in the SKILL.md instructions.

**When to use:** Every time a review pass produces findings (including zero findings -- record the pass metadata).

**Implementation approach:** The SKILL.md instructs Claude to use `Bash` tool calls to append findings to `.forge/findings.json`. Since Claude can execute bash commands and write files, this works within the existing Claude Code tool model. [VERIFIED: SKILL.md already instructs Claude to use Read/Bash tools for Step 0 checks]

```bash
# Pattern for recording a finding (executed by Claude via Bash tool)
python3 -c "
import json, uuid, datetime, os
findings_file = '.forge/findings.json'
os.makedirs('.forge', exist_ok=True)
try:
    with open(findings_file, 'r') as f:
        data = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    data = {'findings': [], 'runs': []}
data['findings'].append({
    'id': str(uuid.uuid4()),
    'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'file': 'path/to/file.py',
    'line': 42,
    'dimension': 'security',
    'pass': 2,
    'cycle': 1,
    'severity': 'P2',
    'description': 'finding text here',
    'outcome': 'pending',
    'reject_reason': None,
    'commit_sha': '$(git rev-parse --short HEAD 2>/dev/null || echo unknown)',
    'cost_tokens': {'input': 0, 'output': 0}
})
with open(findings_file, 'w') as f:
    json.dump(data, f, indent=2)
"
```

**Why embedded Python in Bash:** JSON manipulation with proper error handling is fragile in pure bash. Python one-liners via `python3 -c` are the established pattern in forge hooks (see `check_review_tracker.sh` which uses the same pattern). However, for multi-line logic, prefer heredoc (`python3 << 'PYEOF'`) per CLAUDE.md rules. [VERIFIED: check_review_tracker.sh uses this exact pattern]

### Pattern 2: Severity-Gated Cycle Reset

**What:** Replace the current "any finding resets cycle_counter to 0" with severity-dependent behavior.

**When to use:** After each pass when findings are reported.

**Current state machine (SKILL.md line 113-128):**
```
if ANY pass reports findings:
    fix ALL findings immediately
    cycle_counter = 0
    goto loop
```

**New state machine (D3):**
```
After each pass:
  classify each finding as P0/P1/P2/P3
  if any P0 or P1:
    cycle_counter = 0  (full reset)
    fix all findings
    restart from Cycle 1
  else if any P2:
    restart current cycle (do not reset counter)
    fix P2 findings
  else if only P3:
    accumulate P3 findings (record but do not interrupt)
    continue to next pass/cycle
  else (zero findings):
    auto-continue to next pass/cycle (TRUST-06)
```

**Impact on hooks:** `check_review_tracker.sh` tracks `rounds_with_findings` and `qodo_runs`. The severity-gated reset means the hook needs to understand severity levels, not just "has findings vs clean". The hook's `_has_findings()` function (line 117-191) currently returns boolean; it needs to return the highest severity found. [VERIFIED: check_review_tracker.sh source code]

### Pattern 3: Auto-Continue on Clean Pass

**What:** When a pass reports zero findings, forge automatically proceeds to the next pass/cycle without waiting for user input.

**Current behavior:** After each pass, forge reports results and implicitly waits for user to say "continue" or direct the next action.

**New behavior (D4):** SKILL.md explicitly instructs: "If a pass reports zero findings, immediately proceed to the next pass. Do not wait for user input. Only pause when findings exist and require user decision."

**Implementation:** This is purely a SKILL.md instruction change. No code or hook changes needed. The key instruction to add:

```markdown
## Auto-Continue Protocol (TRUST-06)

After each pass completes:
- If **zero findings**: immediately invoke the next pass. Do not output
  "waiting for input" or "how would you like to proceed?" prompts.
  Report the clean result in one line and move on:
  `[forge] Cycle 2/3, Pass 1/3: qodo-review -- CLEAN`
- If **findings exist**: pause and present findings for user decision
  (accept/reject/fix). Only proceed after user responds.
```

### Pattern 4: Step 0 Context Fusion (FUSE-01)

**What:** After Step 0 completes, serialize its findings into a context block that is prepended to each LLM pass prompt.

**Implementation:** The SKILL.md instructions tell Claude to:
1. Run Step 0 checks (bash -n, shellcheck, pylint, etc.)
2. Collect any findings from Step 0 into a structured list
3. Before invoking each LLM pass (Steps 1-3), prepend the Step 0 findings as context

**Serialization format (recommended):**
```markdown
## Step 0 Findings (deterministic, already addressed)

The following issues were detected by Step 0 deterministic checks.
They have been fixed by the author. Do NOT re-flag these specific issues.
If you find NEW instances of the same pattern elsewhere, report them.

| # | File | Line | Tool | Issue |
|---|------|------|------|-------|
| 1 | path/to/file.py | 42 | pylint W0707 | raise-missing-from |
| 2 | path/to/file.sh | 15 | shellcheck SC2086 | unquoted variable |
```

**Why markdown table:** LLM passes already expect markdown-formatted instructions. A table is compact and unambiguous. JSON would work but adds parsing overhead for the LLM. [ASSUMED]

### Pattern 5: CLI Wrapper Architecture

**What:** Python script that invokes `claude -p` with forge SKILL.md as system prompt.

**How `claude -p` works (verified):**
- `claude -p "prompt"` runs Claude non-interactively [VERIFIED: `claude --help`]
- `--append-system-prompt-file path/to/SKILL.md` loads skill as system prompt [VERIFIED: Claude Code docs]
- `--output-format json` returns JSON array with `total_cost_usd`, `usage` (input/output/cache tokens), `modelUsage` (per-model breakdown) [VERIFIED: actual CLI test output]
- `--allowedTools "Bash,Read,Edit,Write,Grep,Glob"` auto-approves tools [VERIFIED: docs]
- Skills/slash commands are interactive-only; cannot invoke `/forge` in `-p` mode [VERIFIED: GitHub issue #38505 closed as not planned]

**Critical constraint:** The `--append-system-prompt-file` workaround was reported to hang in some cases. The wrapper must handle timeouts. [CITED: github.com/anthropics/claude-code/issues/38505]

**CLI interface design:**
```
forge <git-diff-spec>           # Full review (Step 0 + Steps 1-3 + Step 4)
forge --dry-run <git-diff-spec> # Step 0 only, no LLM cost
forge --stats                   # FP rate dashboard from findings.json
forge --stats --json            # Machine-readable dashboard
forge --bootstrap <file>        # Load historical FP data
```

**Token/cost data extraction from CLI output:**
```python
# Parse the result object from claude -p --output-format json
import json, subprocess

result = subprocess.run(
    ['claude', '-p', prompt, '--output-format', 'json',
     '--append-system-prompt-file', skill_path,
     '--allowedTools', 'Bash,Read,Edit,Write,Grep,Glob'],
    capture_output=True, text=True, timeout=600
)
# Output is a JSON array; last element with type=result has cost data
data = json.loads(result.stdout)
for item in data:
    if item.get('type') == 'result':
        cost_usd = item['total_cost_usd']
        usage = item['usage']
        input_tokens = usage['input_tokens']
        output_tokens = usage['output_tokens']
        cache_read = usage.get('cache_read_input_tokens', 0)
        cache_creation = usage.get('cache_creation_input_tokens', 0)
```

[VERIFIED: actual `claude -p --output-format json` output contains these exact fields]

### Anti-Patterns to Avoid

- **Do not build a standalone LLM review engine.** D7 explicitly chose wrapper-over-reimplementation. The value is in Claude's multi-pass convergence, not in a Python script calling the Anthropic API directly.
- **Do not use SQLite.** D1 explicitly chose JSON. Phase 1b will re-evaluate if data volume demands it.
- **Do not parse LLM text output for token counts.** The CLI's `--output-format json` already provides structured cost data. Parsing text output is fragile and unnecessary.
- **Do not make Step 0 findings blocking for LLM passes.** FUSE-01 passes them as context, not as a gate. Step 0 gate is separate (0a+0b+0c must pass).
- **Do not implement per-dimension escalation logic.** D9 says it is a design principle only -- Phase 1a just records per-dimension data.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| UUID generation | Custom ID scheme | `uuid.uuid4()` | Collision-free, no coordination needed |
| JSON file I/O with atomicity | Custom write-rename | `tempfile.mkstemp()` + `os.replace()` | Atomic file replacement pattern (already used in check_review_tracker.sh) |
| CLI argument parsing | Manual sys.argv parsing | `argparse` | Standard, handles --help, validation, subcommands |
| Terminal table formatting | Custom column alignment | String `format()` with fixed widths | Sufficient for simple tables; no external deps |
| ISO-8601 timestamps | String formatting | `datetime.now(timezone.utc).isoformat()` | Timezone-aware, standard format |
| Process invocation | `os.system()` | `subprocess.run()` | Captures stdout/stderr, timeout support, exit code |

**Key insight:** Every component in Phase 1a uses Python stdlib only. No `pip install` required. This aligns with forge's constraint that dependencies are minimal (bash + jq for assertions; Python stdlib for CLI).

## Common Pitfalls

### Pitfall 1: findings.json Corruption on Concurrent Access

**What goes wrong:** Two concurrent forge runs (or a crash mid-write) can corrupt findings.json.
**Why it happens:** JSON files are not atomic -- a half-written file is invalid JSON.
**How to avoid:** Use the atomic write pattern already established in `check_review_tracker.sh`: write to a tempfile, then `os.replace()` to atomically swap. [VERIFIED: check_review_tracker.sh lines 70-81 use this exact pattern]
**Warning signs:** `json.JSONDecodeError` on load; missing findings from a session.

### Pitfall 2: SKILL.md Instructions Ignored by Model

**What goes wrong:** Adding complex instructions to SKILL.md does not guarantee the model follows them perfectly every time.
**Why it happens:** LLM instruction following degrades with instruction length and complexity. SKILL.md is already 417 lines.
**How to avoid:** (1) Keep new instructions concise and structured (tables, numbered steps). (2) Use the existing "protocol" format that SKILL.md already uses. (3) Add enforcement via hooks where critical (severity-gated reset can be partially enforced by modifying `check_review_tracker.sh`). (4) The auto-continue instruction is particularly reliable because it tells the model to DO something (proceed) rather than NOT do something (don't ask).
**Warning signs:** Model asking "how would you like to proceed?" after clean passes despite TRUST-06 instructions.

### Pitfall 3: CLI Wrapper Timeout

**What goes wrong:** `claude -p` hangs or takes very long, especially with `--append-system-prompt-file`.
**Why it happens:** Loading a 417-line SKILL.md as system prompt plus the diff content can exceed context limits or trigger slow processing. GitHub issue #38505 reports hanging. [CITED: github.com/anthropics/claude-code/issues/38505]
**How to avoid:** (1) Set explicit `timeout=600` on subprocess call. (2) Use `--bare` mode to skip auto-discovery overhead. (3) Consider passing the diff as a file reference rather than piping stdin for large diffs.
**Warning signs:** CLI wrapper appearing to hang with no output; subprocess timeout exceptions.

### Pitfall 4: Severity Classification Inconsistency Across Passes

**What goes wrong:** Pass 1 (qodo-review) uses red/yellow/green severity; Pass 2 (code-review-expert) uses P0/P1/P2/P3; Pass 3 (adversarial-qe) uses Critical/High/Medium/Low/Nit.
**Why it happens:** Each sub-skill was developed independently with its own severity taxonomy.
**How to avoid:** The SKILL.md must include a severity mapping table that normalizes all three taxonomies to P0/P1/P2/P3 for the state machine:

| qodo-review | code-review-expert | adversarial-qe | Normalized |
|-------------|-------------------|----------------|------------|
| Red (must fix) | P0 Critical | Critical | P0 |
| Red (must fix) | P1 High | High | P1 |
| Yellow (problematic) | P2 Medium | Medium | P2 |
| Green (minor) | P3 Low | Low/Nit | P3 |

**Warning signs:** A "Green/Low" finding triggering a full cycle reset because severity was not normalized.

### Pitfall 5: Historical Bootstrap Data Format Mismatch

**What goes wrong:** The 15 historical FP instances in `/tmp/draft_20260512_historical_review_analysis.txt` are in free-text format, not the D1 JSON schema.
**Why it happens:** The historical analysis was done before the schema was defined.
**How to avoid:** Write a `bootstrap/convert_historical.py` script that parses the structured sections of the analysis file and converts to D1 schema. Fields that cannot be extracted (exact line numbers, token counts) should be filled with sentinel values (`line: -1`, `cost_tokens: {"input": 0, "output": 0}`).
**Warning signs:** Missing or malformed fields in bootstrapped records breaking dashboard queries.

### Pitfall 6: .forge/ Directory Not Gitignored

**What goes wrong:** findings.json (containing per-project review data) gets committed to the forge repository.
**Why it happens:** `.forge/` is a new directory with no gitignore entry.
**How to avoid:** Add `.forge/` to `.gitignore` in the forge repository. For consumer projects, the `.forge/` directory should also be in their `.gitignore`.
**Warning signs:** `git status` showing `.forge/findings.json` as untracked.

### Pitfall 7: User Feedback Loop Blocks Pipeline

**What goes wrong:** The accept/reject feedback step for LEARN-07-LITE blocks the pipeline, forcing the user to classify every finding before proceeding.
**Why it happens:** Naive implementation would gate pipeline completion on feedback.
**How to avoid:** Findings are recorded with `outcome: "pending"` immediately. Feedback collection happens after the pipeline completes (at the commit gate or via `forge --classify`). Users can defer classification to later sessions.
**Warning signs:** User complaining that forge takes longer after instrumentation was added.

## Code Examples

### findings.json Schema (D1)

```json
{
  "version": 1,
  "findings": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "timestamp": "2026-05-12T10:30:00+00:00",
      "file": "hooks/check_review_tracker.sh",
      "line": 42,
      "dimension": "security",
      "pass": 2,
      "cycle": 1,
      "severity": "P2",
      "description": "Missing input validation on user-supplied path",
      "outcome": "accepted",
      "reject_reason": null,
      "commit_sha": "abc1234",
      "cost_tokens": {"input": 15000, "output": 800}
    }
  ],
  "runs": [
    {
      "id": "run-uuid",
      "timestamp": "2026-05-12T10:25:00+00:00",
      "commit_sha": "abc1234",
      "diff_files": 3,
      "diff_lines": 150,
      "total_passes": 9,
      "total_cost_usd": 0.35,
      "total_tokens": {"input": 45000, "output": 2400},
      "outcome": "passed"
    }
  ]
}
```

[ASSUMED: `runs` array is not in D1 minimum schema but is needed for TRUST-05 cost dashboard. Adding it here as a recommendation.]

### CLI Wrapper Core Logic

```python
#!/usr/bin/env python3
# Source: stdlib only, no external dependencies
import argparse
import json
import os
import subprocess
import sys

FORGE_SKILL = os.path.join(os.path.dirname(__file__), '..', 'skills', 'forge', 'SKILL.md')
FINDINGS_FILE = '.forge/findings.json'

def run_forge(diff_spec, dry_run=False):
    """Invoke claude -p with forge SKILL.md as system prompt."""
    prompt = f"Review the following git diff: {diff_spec}"
    if dry_run:
        prompt = f"Run Step 0 only (syntax + lint + non-ASCII) on: {diff_spec}"

    cmd = [
        'claude', '-p', prompt,
        '--append-system-prompt-file', FORGE_SKILL,
        '--output-format', 'json',
        '--allowedTools', 'Bash,Read,Edit,Write,Grep,Glob',
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        print(f"Error: claude exited with code {result.returncode}", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        return None

    # Parse JSON array output
    data = json.loads(result.stdout)
    for item in data:
        if item.get('type') == 'result':
            return item
    return None

def show_stats():
    """Display FP rate dashboard from findings.json."""
    try:
        with open(FINDINGS_FILE) as f:
            data = json.load(f)
    except FileNotFoundError:
        print("No findings data yet. Run forge first.")
        return

    findings = data.get('findings', [])
    # Aggregate by dimension
    dims = {}
    for f in findings:
        dim = f.get('dimension', 'unknown')
        if dim not in dims:
            dims[dim] = {'accepted': 0, 'rejected': 0, 'pending': 0}
        outcome = f.get('outcome', 'pending')
        dims[dim][outcome] = dims[dim].get(outcome, 0) + 1

    # Print table
    print(f"{'Dimension':<25} {'Accepted':>8} {'Rejected':>8} {'Pending':>8} {'FP Rate':>8}")
    print("-" * 65)
    for dim, counts in sorted(dims.items()):
        total_decided = counts['accepted'] + counts['rejected']
        fp_rate = f"{counts['rejected']/total_decided*100:.0f}%" if total_decided > 0 else "N/A"
        print(f"{dim:<25} {counts['accepted']:>8} {counts['rejected']:>8} {counts['pending']:>8} {fp_rate:>8}")
```

[VERIFIED: `subprocess.run`, `json.loads`, `argparse` are all stdlib. `claude -p --output-format json` output structure verified from actual test.]

### Atomic JSON Write Pattern

```python
# Source: pattern from check_review_tracker.sh (hooks/check_review_tracker.sh lines 70-81)
import json
import os
import tempfile

def save_findings(filepath, data):
    """Atomically write findings JSON to avoid corruption."""
    dir_name = os.path.dirname(filepath) or '.'
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dir_name, suffix='.json')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, filepath)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
```

[VERIFIED: this is the exact pattern from check_review_tracker.sh `_save_state()` function]

### Model Pricing Config

```json
{
  "pricing": {
    "claude-opus-4-6": {
      "input_per_mtok": 15.00,
      "output_per_mtok": 75.00,
      "cache_read_per_mtok": 1.50,
      "cache_creation_per_mtok": 18.75
    },
    "claude-sonnet-4-6": {
      "input_per_mtok": 3.00,
      "output_per_mtok": 15.00,
      "cache_read_per_mtok": 0.30,
      "cache_creation_per_mtok": 3.75
    }
  }
}
```

[ASSUMED: Pricing figures based on training data. Must be verified against current Anthropic pricing page before implementation. The config is user-editable by design (D8) so exact values are not critical for the schema.]

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `cycle_counter` in conversation memory | Severity-gated `cycle_counter` with JSON persistence | Phase 1a | Eliminates P3-induced reset waste (estimated 60%+ pass reduction per GAP-ANALYSIS-DEEP) |
| "Type continue after clean pass" | Auto-continue on zero findings | Phase 1a | Eliminates UX friction |
| No finding persistence | `.forge/findings.json` with permanent retention | Phase 1a | Enables Phase 1b calibration |
| Step 0 and LLM passes independent | Step 0 findings injected as LLM context | Phase 1a | Reduces redundant flagging (Semgrep multimodal achieved 8x TP / 50% noise reduction with similar fusion) |
| No cost visibility | Token count + USD estimate per run | Phase 1a | Enables economic viability assessment |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | UUID v4 is the best choice for finding IDs | Standard Stack / Alternatives | Low -- any unique ID scheme works; UUID is the simplest |
| A2 | Markdown table is the best serialization format for Step 0 -> LLM context injection | Pattern 4 | Low -- JSON or plain text would also work; format is consumed by LLM |
| A3 | `runs` array should be added to findings.json schema alongside `findings` | Code Examples | Medium -- if runs are tracked separately (e.g., in a run log file), the schema would differ |
| A4 | Model pricing figures are approximately correct | Code Examples | Low -- config is user-editable; stale prices only affect cost estimates, not functionality |
| A5 | `--append-system-prompt-file` is reliable enough for production CLI wrapper | Pattern 5 | HIGH -- GitHub issue #38505 reports hanging; must implement timeout and fallback |
| A6 | Feedback collection can be deferred (not blocking pipeline) | Pitfall 7 | Medium -- if user expects inline feedback, UX may surprise |

## Open Questions (RESOLVED)

1. **Should the hook (`check_review_tracker.sh`) be modified or replaced?** RESOLVED
   - What we know: The hook currently tracks qodo runs and has a boolean `_has_findings()`. Severity-gated reset needs it to understand severity levels.
   - What's unclear: Whether to modify the existing hook or have SKILL.md handle all state management (making the hook a simple gate checker).
   - Resolution: Modify the hook to read severity from a sidecar file (`.forge/current_session.json`) that SKILL.md writes. The hook remains a gate; SKILL.md is the state machine. Implemented in Plan 01a-03 Task 2.

2. **Should finding feedback be collected inline or post-pipeline?** RESOLVED
   - What we know: D5 says binary accept/reject per finding. TRUST-06 says auto-continue on clean pass.
   - What's unclear: When exactly the user classifies findings -- during the fix cycle, or after the pipeline completes.
   - Resolution: Findings are recorded as `pending` during the pipeline. After pipeline completes (at commit gate), forge presents a summary of findings for classification. Users can also run `forge classify` later to review unclassified findings. Implemented in Plan 01a-01 Task 1 and Plan 01a-04.

3. **How to handle `--append-system-prompt-file` reliability for CLI wrapper?** RESOLVED
   - What we know: GitHub issue #38505 was closed as "not planned". The workaround (`--append-system-prompt-file`) was reported to hang.
   - What's unclear: Whether the hanging was fixed in v2.1.139 or is still an issue.
   - Resolution: Implement with timeout (600 seconds). If hanging is reproducible, fall back to `--system-prompt` with the SKILL.md content inline. Test early in Phase 1a. Implemented in Plan 01a-04 Task 1.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3 | CLI wrapper, bootstrap | Yes | 3.14.4 | -- |
| Claude Code CLI | CLI wrapper | Yes | 2.1.139 | -- |
| jq | Existing hooks | Yes (assumed) | -- | Python json module |
| git | Diff source, commit SHA | Yes | -- | -- |
| shellcheck | Step 0 checks | Yes (assumed) | -- | Skip lint |
| pylint / ruff | Step 0 checks | Yes (assumed) | -- | Skip lint |

**Missing dependencies with no fallback:** None identified.

**Missing dependencies with fallback:** None identified.

## Project Constraints (from CLAUDE.md)

The following CLAUDE.md directives constrain implementation:

1. **All documentation and skill files in English** -- findings.json field names, SKILL.md additions, CLI output must be English.
2. **Dependencies: bash + jq only for assertions; skills require Claude Code** -- CLI wrapper must use Python stdlib only, no pip install.
3. **Must work with Claude Code skill discovery (SKILL.md in ~/.claude/skills/)** -- modified SKILL.md must remain a valid Claude Code skill.
4. **No non-ASCII in code** -- typographic characters must be ASCII equivalents.
5. **Git worktree requirement** -- all code changes must happen in a worktree, not the main tree.
6. **Three-cycle review before commit** -- all changes to SKILL.md, hooks, and CLI wrapper require full forge pipeline review.
7. **Author: Minxi Hou <houminxi@gmail.com>** -- never use AI co-author lines.
8. **Commit format** -- `<subsystem>/<case>: <brief summary>` with Signed-off-by.
9. **Multi-language embedding: prefer heredoc** -- Python code in bash should use `<< 'PYEOF'` not `python3 -c "..."` for complex logic.
10. **No AI traces in git history** -- zero AI markers in commit messages.

## Sources

### Primary (HIGH confidence)
- Claude Code CLI `--help` output -- verified all flags, confirmed `--append-system-prompt-file`, `--output-format json`, `--bare`, `--allowedTools` [VERIFIED: local CLI test 2026-05-12]
- Claude Code CLI JSON output test -- verified `total_cost_usd`, `usage`, `modelUsage` fields in result object [VERIFIED: actual `claude -p --output-format json` invocation]
- `skills/forge/SKILL.md` (417 lines) -- verified state machine structure, pass order, severity taxonomies [VERIFIED: Read tool]
- `hooks/check_review_tracker.sh` (294 lines) -- verified state file pattern, atomic write, finding detection logic [VERIFIED: Read tool]
- `hooks/check_git_commit_review.sh` (105 lines) -- verified commit gate, bypass markers [VERIFIED: Read tool]
- `/tmp/draft_20260512_historical_review_analysis.txt` (686 lines) -- verified 15 FP instances, 6-category classification [VERIFIED: Read tool]

### Secondary (MEDIUM confidence)
- [Claude Code headless mode documentation](https://code.claude.com/docs/en/headless) -- verified CLI patterns, `--bare` mode, `--append-system-prompt-file` usage [CITED: docs.code.claude.com]
- [GitHub issue #38505: CLI skill invocation](https://github.com/anthropics/claude-code/issues/38505) -- verified that slash commands are interactive-only, `--append-system-prompt-file` is the workaround, hanging reported [CITED: github.com/anthropics/claude-code/issues/38505]

### Tertiary (LOW confidence)
- Model pricing figures -- based on training data, may be outdated. Config is user-editable so exact values are non-critical. [ASSUMED]

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all components are Python stdlib + existing Claude Code CLI, verified locally
- Architecture: HIGH -- patterns are extensions of existing forge patterns (hooks, SKILL.md, bash tools), verified against source code
- Pitfalls: HIGH -- identified from actual failure modes in historical data (FP analysis) and verified GitHub issues
- CLI wrapper: MEDIUM -- `--append-system-prompt-file` reliability unverified at scale; GitHub issue reports hanging

**Research date:** 2026-05-12
**Valid until:** 2026-06-12 (stable domain -- JSON, Python stdlib, Claude Code CLI)
