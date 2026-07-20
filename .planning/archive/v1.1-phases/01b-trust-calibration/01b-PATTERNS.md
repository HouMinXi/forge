# Phase 1b: Trust Calibration - Pattern Map

**Mapped:** 2026-05-12
**Files analyzed:** 4 (all modifications, no new files)
**Analogs found:** 4 / 4

## File Classification

All Phase 1b work extends existing files. The "analog" for each is the file itself -- the existing code establishes the patterns that new functions must follow.

| Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---------------|------|-----------|----------------|---------------|
| `cli/forge_cli.py` | CLI entry + service | request-response + CRUD | `cli/forge_cli.py` (self) | exact |
| `skills/forge/SKILL.md` | LLM prompt config | transform (finding persistence heredoc) | `skills/forge/SKILL.md` (self) | exact |
| `cli/config.json` | config | static | `cli/config.json` (self) | exact |
| `.forge/findings.json` | data schema | CRUD | `.forge/findings.json` schema in SKILL.md | exact |

## Pattern Assignments

### `cli/forge_cli.py` -- New Functions (service, request-response + CRUD)

**Analog:** `cli/forge_cli.py` itself (922 lines)

Phase 1b adds 5 new functions and extends 2 existing functions. All new code must follow the established patterns below.

#### Imports pattern (lines 1-10, 33-41)

Every new function uses only stdlib. No new imports beyond what exists, except `math` (for Wilson score) and `random` (for audit sampling) and `re` (for pattern matching).

```python
#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
```

```python
import argparse
import glob
import json
import os
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
```

New imports to add (stdlib only, per RESEARCH.md "no new packages"):
```python
import math
import random
import re
```

#### Constants pattern (lines 46-66)

Constants are module-level, ALL_CAPS, with inline doc comments. Group related constants together.

```python
# FP category split (D2 key insight)
# Categories 1-4 = tool wrong (improve the tool)
TOOL_ERROR_REASONS = {
    'HALLUCINATION', 'CONTEXT_MISSING', 'INTENTIONAL', 'NOT_APPLICABLE',
}
# Categories 5-6 = tool right, user won't act (don't count as tool FP)
USER_PREF_REASONS = {'STYLE_PREFERENCE', 'ACCEPTABLE_RISK'}

# Valid reject reasons (union of both sets)
VALID_REJECT_REASONS = TOOL_ERROR_REASONS | USER_PREF_REASONS

# Valid finding outcomes
VALID_OUTCOMES = {'accepted', 'rejected', 'pending'}
```

New constants to add following this pattern:
- `CRITICAL_PATTERNS` -- list of regex patterns for security-critical files (D2)
- `VALID_DIMENSIONS` -- set of known dimension names (already defined in SKILL.md heredoc, extract to module-level)
- `MIN_OBSERVATIONS` -- minimum data points before acting (20, per D3/D4)

#### Pure function pattern -- `calculate_cost()` (lines 147-179)

New functions that compute values (Wilson score, confidence, tier classification) follow this template: docstring with Args/Returns, pure function (no side effects), defensive defaults for missing data.

```python
def calculate_cost(usage, config):
    """Calculate cost in USD from token usage and pricing config.

    Pure function -- no side effects, no platform dependency.

    Args:
        usage: dict with 'input_tokens', 'output_tokens', and optional
               'cache_read_input_tokens', 'cache_creation_input_tokens'.
        config: dict with 'pricing' and 'default_model' keys.

    Returns:
        float: estimated cost in USD.
    """
    model = config.get('default_model', 'claude-sonnet-4-6')
    pricing = config.get('pricing', {}).get(model)
    if pricing is None:
        return 0.0
    # ... computation ...
    return cost
```

**Apply to:** `wilson_score_interval()`, `compute_confidence()`, `classify_change()` -- all are pure functions with no side effects.

#### Subprocess + git pattern -- `run_dry_run()` (lines 202-348)

Functions that shell out to git follow this template: `subprocess.run()` with `capture_output=True, text=True, timeout=10, check=False`, then check `returncode`, strip output.

```python
try:
    result = subprocess.run(
        ['git', 'diff', '--name-only', diff_spec],
        capture_output=True, text=True, timeout=10, check=False,
    )
    if result.returncode != 0:
        # fallback or error
        print(
            f"Error: git diff failed for '{diff_spec}': "
            f"{result.stderr.strip()}",
            file=sys.stderr,
        )
        sys.exit(1)
    changed_files = [
        f.strip()
        for f in result.stdout.strip().split('\n')
        if f.strip()
    ]
except (OSError, subprocess.SubprocessError) as exc:
    print(
        f"Error: failed to get diff files: {exc}",
        file=sys.stderr,
    )
    sys.exit(1)
```

**Apply to:** `_get_changed_files()`, `_count_diff_lines()`, `_detect_change_type()` -- all helper functions for tier classification that call git.

#### Error output pattern (throughout)

All error messages go to `sys.stderr` via `print(..., file=sys.stderr)`. Format: `"Error: <description>"` or `"Warning: <description>"`. Never use `raise SystemExit` directly; use `sys.exit(1)`.

```python
print(
    f"Error: config.json not found at {config_path}",
    file=sys.stderr,
)
sys.exit(1)
```

#### JSON load with fallback pattern -- `load_findings()` (lines 96-107)

Functions that read JSON files catch all failure modes and return an empty structure. Never crash on missing or corrupted data.

```python
def load_findings():
    """Load .forge/findings.json.

    Returns dict with 'version', 'findings', 'runs' keys.
    Returns empty structure if file is missing or corrupted.
    """
    try:
        with open(FINDINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, IOError):
        return {'version': 1, 'findings': [], 'runs': []}
```

**Apply to:** `load_config()` for tier weights (new config sections).

#### Atomic write pattern -- `atomic_write()` (lines 126-144)

All JSON persistence uses tempfile + os.replace. Never write directly to the target file.

```python
def atomic_write(filepath, data):
    """Atomically write JSON data to filepath.

    Uses tempfile.mkstemp + os.replace to avoid corruption on crash
    or concurrent access. Pattern from check_review_tracker.sh.
    """
    dir_name = os.path.dirname(filepath) or '.'
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dir_name, suffix='.json')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, filepath)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
```

**Apply to:** Any new function that writes to config.json or run sidecar files (e.g., `_record_run()` for tier metadata).

#### Dashboard terminal output pattern -- `show_stats()` (lines 575-724)

Terminal tables use fixed-width formatting with `=` and `-` separators, right-aligned numbers, left-aligned labels. Legend at bottom. Supports `json_format` flag for machine-readable output.

```python
def show_stats(json_format=False):
    """Display FP rate dashboard from findings.json (TRUST-05)."""
    # ... load data ...

    if json_format:
        output = { ... }
        print(json.dumps(output, indent=2))
        return

    # Terminal table with split FP rates
    print("=" * 82)
    print("Forge FP Rate Dashboard")
    print("=" * 82)
    print()
    header = (
        f"{'Dimension':<18} {'Accept':>6} {'Reject':>6} {'Pend':>5} "
        f"{'ToolFP':>7} {'UserFP':>7} {'FP%':>5}"
    )
    print(header)
    print("-" * 82)
    # ... rows ...
    print("-" * 82)
    # ... totals ...
    # Legend
    print()
    print("ToolFP = cat 1-4 ...")
```

**Apply to:** `evaluate()` (D5 Tricorder 4 criteria report) and `recommend()` (D3 rule improvement recommendations). Both output terminal tables with optional `--json` support.

#### Argparse extension pattern -- `main()` (lines 867-922)

New CLI flags are added to the existing `ArgumentParser`. Mutually exclusive commands use `if/elif` chain. No subparsers (keeps CLI flat).

```python
parser.add_argument(
    '--stats', action='store_true',
    help='Show FP rate dashboard from findings.json',
)
# ...
if args.stats:
    show_stats(json_format=args.json)
elif args.classify:
    classify_findings()
```

**Apply to:** New flags `--full`, `--step0`, `--eval`, `--recommend`. Same pattern: `add_argument()` with `action='store_true'` or `metavar`, then route in `if/elif` chain.

#### run_forge() modification pattern (lines 441-569)

The existing `run_forge()` is the primary extension point. New code inserts tier classification BEFORE the `claude -p` invocation (line 457). The pattern is: compute tier, adjust prompt text, log tier to run sidecar.

```python
def run_forge(diff_spec):
    """Invoke claude -p with forge SKILL.md as system prompt."""
    skill_path = os.path.realpath(FORGE_SKILL)
    if not os.path.isfile(skill_path):
        # ... error ...

    prompt = (
        f"Run the full forge review pipeline on the git diff: {diff_spec}. "
        "Follow the complete 5-step pipeline in your system prompt."
    )

    cmd = [
        'claude', '-p', prompt,
        '--append-system-prompt-file', skill_path,
        '--output-format', 'json',
        '--allowedTools', 'Bash,Read,Edit,Write,Grep,Glob',
    ]

    # ... invoke, parse result, write run sidecar ...
```

**Phase 1b insertion point:** Between `skill_path` validation and `prompt` construction, insert `classify_change()` call. Adjust `prompt` text based on tier. Add `tier` and `was_audited` fields to `run_record` dict.

---

### `skills/forge/SKILL.md` -- Schema Extension (config, transform)

**Analog:** `skills/forge/SKILL.md` lines 316-410 (Finding Persistence section)

The finding persistence heredoc is the template for schema extensions. New fields (`confidence`, `confidence_signals`) must be added to the `data['findings'].append({...})` dict inside the Python heredoc.

#### Finding schema append pattern (lines 362-376)

```python
data['findings'].append({
    'id': str(uuid.uuid4()),
    'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'file': file_path,
    'line': -1,
    'dimension': dimension,
    'pass': 1,
    'cycle': 1,
    'severity': severity,
    'description': 'REPLACE_WITH_FINDING_TEXT',
    'outcome': 'pending',
    'reject_reason': None,
    'commit_sha': commit_sha,
    'cost_tokens': {'input': 0, 'output': 0}
})
```

**Phase 1b extension:** Add two new fields after `cost_tokens`:
```python
    'confidence': 0.0,           # NEW: computed by CLI post-run
    'confidence_signals': {      # NEW: raw signals for formula
        'dimension_fp_rate': 0.0,
        'pass_agreement': 1.0,
        'evidence_count': 1,
        'llm_self_report': 0.8,
    },
```

The `confidence` value is set to 0.0 at recording time (SKILL.md heredoc cannot compute it -- it needs historical FP data from findings.json). The CLI computes and backfills the actual score post-run. The `confidence_signals` capture raw data from the LLM pass (evidence_count = lines of evidence cited, llm_self_report = LLM's stated confidence).

#### Validation pattern before storage (lines 342-361)

```python
VALID_SEVERITIES = {'P0', 'P1', 'P2', 'P3'}
VALID_DIMENSIONS = {
    'correctness', 'security', 'performance', 'style', 'architecture',
    'concurrency', 'api_contract', 'bidirectional', 'graceful_degradation',
    'convention', 'test_quality', 'ai_code_smell', 'unknown',
}

severity = 'REPLACE_WITH_SEVERITY'
dimension = 'REPLACE_WITH_DIMENSION'

if severity not in VALID_SEVERITIES:
    print(f"[forge-warn] Invalid severity '{severity}', defaulting to P2",
          file=sys.stderr)
    severity = 'P2'
if dimension not in VALID_DIMENSIONS:
    print(f"[forge-warn] Invalid dimension '{dimension}', defaulting to unknown",
          file=sys.stderr)
    dimension = 'unknown'
```

**Apply to:** New fields need validation: `evidence_count` must be int >= 0, `llm_self_report` must be float 0.0-1.0, `pass_agreement` must be float 0.0-1.0.

---

### `cli/config.json` -- Schema Extension (config, static)

**Analog:** `cli/config.json` itself (17 lines)

Current config has one top-level key (`pricing`) plus `default_model`. Phase 1b adds new top-level keys for tier classification and evaluation config.

#### Config structure pattern (lines 1-17)

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
  },
  "default_model": "claude-sonnet-4-6"
}
```

**Phase 1b extension:** Add peer-level keys (not nested under `pricing`):
```json
{
  "pricing": { ... },
  "default_model": "claude-sonnet-4-6",
  "tier_classification": {
    "critical_patterns": [
      "(?:auth|security|crypto|secret|token|password|credential)",
      "(?:hooks/check_)",
      "(?:SKILL\\.md)"
    ],
    "ai_markers": ["Generated by", "Co-Authored-By"],
    "audit_rate": 0.10,
    "small_diff_threshold": 10
  },
  "evaluation": {
    "min_observations": 20,
    "fp_rate_threshold": 0.10,
    "confidence_level": 0.95
  }
}
```

**Pattern rule:** Keep config.json under 100 lines (RESEARCH.md Pitfall 4). Hardcode defaults in Python; config.json provides overrides only. The `load_config()` function in forge_cli.py already handles the read path -- extend it to use `.get()` with defaults for new sections.

---

### `.forge/findings.json` -- Schema Extension (data, CRUD)

**Analog:** Schema defined in `skills/forge/SKILL.md` lines 316-410 and `cli/forge_cli.py` `load_findings()`

The findings.json schema is read by `load_findings()` and written by the SKILL.md heredoc and `atomic_write()`. Phase 1b extends each finding record (backward compatible -- new fields have defaults).

#### Current finding record schema

```json
{
  "id": "uuid-v4",
  "timestamp": "ISO-8601-UTC",
  "file": "path/to/file",
  "line": -1,
  "dimension": "security",
  "pass": 2,
  "cycle": 1,
  "severity": "P2",
  "description": "finding text",
  "outcome": "pending",
  "reject_reason": null,
  "commit_sha": "abc123",
  "cost_tokens": {"input": 0, "output": 0}
}
```

#### Phase 1b extended schema (backward compatible)

```json
{
  "id": "uuid-v4",
  "timestamp": "ISO-8601-UTC",
  "file": "path/to/file",
  "line": -1,
  "dimension": "security",
  "pass": 2,
  "cycle": 1,
  "severity": "P2",
  "description": "finding text",
  "outcome": "pending",
  "reject_reason": null,
  "commit_sha": "abc123",
  "cost_tokens": {"input": 0, "output": 0},
  "confidence": 0.72,
  "confidence_signals": {
    "dimension_fp_rate": 0.15,
    "pass_agreement": 1.0,
    "evidence_count": 3,
    "llm_self_report": 0.8
  }
}
```

**Backward compatibility:** All code that reads findings must use `.get('confidence', 0.0)` and `.get('confidence_signals', {})` with defaults. Historical and bootstrap findings (commit_sha == 'historical') will not have these fields.

#### Run sidecar extension

Current run sidecar (`_record_run` in `run_forge()`, lines 525-538):
```json
{
  "id": "uuid-v4",
  "timestamp": "ISO-8601-UTC",
  "commit_sha": "abc123",
  "diff_spec": "HEAD~1",
  "dry_run": false,
  "total_passes": 9,
  "total_cost_usd": 0.42,
  "total_tokens": {"input": 12000, "output": 4500},
  "outcome": "completed"
}
```

Phase 1b extension -- add `tier` and `was_audited` fields:
```json
{
  "id": "uuid-v4",
  "timestamp": "ISO-8601-UTC",
  "commit_sha": "abc123",
  "diff_spec": "HEAD~1",
  "dry_run": false,
  "tier": "full",
  "was_audited": false,
  "total_passes": 9,
  "total_cost_usd": 0.42,
  "total_tokens": {"input": 12000, "output": 4500},
  "outcome": "completed"
}
```

---

## Shared Patterns

### Atomic JSON Write
**Source:** `cli/forge_cli.py` lines 126-144, `bootstrap/convert_historical.py` lines 326-340, `hooks/check_review_tracker.sh` lines 76-88
**Apply to:** All functions that persist data (run sidecar, findings update, config write)

The pattern is identical across all three files: `tempfile.mkstemp()` -> write -> `os.replace()` -> cleanup on error. The hook uses the same pattern in Python embedded in bash.

```python
dir_name = os.path.dirname(filepath) or '.'
os.makedirs(dir_name, exist_ok=True)
fd, tmp = tempfile.mkstemp(dir=dir_name, suffix='.json')
try:
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, filepath)
except Exception:
    try:
        os.unlink(tmp)
    except OSError:
        pass
    raise
```

### FP Category Split
**Source:** `cli/forge_cli.py` lines 53-62
**Apply to:** All statistical functions (Wilson score, evaluate, recommend)

ToolFP (categories 1-4) vs UserFP (categories 5-6) split is fundamental. D3 rule improvement triggers ONLY on ToolFP > 10%.

```python
TOOL_ERROR_REASONS = {
    'HALLUCINATION', 'CONTEXT_MISSING', 'INTENTIONAL', 'NOT_APPLICABLE',
}
USER_PREF_REASONS = {'STYLE_PREFERENCE', 'ACCEPTABLE_RISK'}
```

### Dimension Aggregation
**Source:** `cli/forge_cli.py` `show_stats()` lines 593-611
**Apply to:** `evaluate_dimensions()`, `generate_recommendation()`, `compute_confidence()`

Findings are grouped by dimension using dict accumulation. Every function that analyzes findings by dimension follows this loop pattern:

```python
dims = {}
for f in findings:
    dim = f.get('dimension', 'unknown')
    if dim not in dims:
        dims[dim] = {
            'accepted': 0, 'rejected': 0, 'pending': 0,
            'tool_error': 0, 'user_pref': 0,
        }
    outcome = f.get('outcome', 'pending')
    dims[dim][outcome] = dims[dim].get(outcome, 0) + 1
    reason = f.get('reject_reason')
    if outcome == 'rejected' and reason:
        if reason in TOOL_ERROR_REASONS:
            dims[dim]['tool_error'] += 1
        elif reason in USER_PREF_REASONS:
            dims[dim]['user_pref'] += 1
```

### Docstring Convention
**Source:** Throughout `cli/forge_cli.py` and `bootstrap/convert_historical.py`
**Apply to:** All new functions

Every function has a docstring. First line is imperative mood summary. Subsequent lines describe Args, Returns, design rationale, or references to decisions/issues.

```python
def bootstrap_historical(filepath):
    """Load historical FP data from analysis file.

    Delegates to bootstrap/convert_historical.py script created in Plan 02.
    """
```

### Comment-Level Section Organization
**Source:** `cli/forge_cli.py` section headers (lines 43-45, 69-71, 198-200, etc.)
**Apply to:** New function groups

Code sections are separated by comment blocks using `# ---...---` pattern with section title.

```python
# ---------------------------------------------------------------------------
# Core: run_dry_run  (review issue #1 -- zero LLM cost)
# ---------------------------------------------------------------------------
```

New sections to add:
```python
# ---------------------------------------------------------------------------
# Tier Classification (D2 -- deterministic, before LLM invocation)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Confidence Scoring (D1 -- progressive multi-signal formula)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Statistical Utilities (Wilson score, data aggregation)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Evaluation and Recommendation (D3, D5 -- rule improvement pipeline)
# ---------------------------------------------------------------------------
```

---

## No Analog Found

No files in Phase 1b lack analogs. All modifications target existing files with well-established patterns.

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| (none) | -- | -- | All Phase 1b code extends existing files |

---

## Metadata

**Analog search scope:** `/home/houminxi/code/forge/` (entire project tree)
**Files scanned:** 34 project files
**Pattern extraction date:** 2026-05-12
**Key finding:** Phase 1b is a pure extension phase -- zero new files, four files modified. All patterns are self-referential (the analog for each file is the file itself). The strongest patterns to enforce are: (1) pure functions for all computation, (2) atomic JSON write for all persistence, (3) subprocess.run for all git calls, (4) ToolFP/UserFP split for all statistical analysis, (5) terminal table + JSON dual output for all reports.
