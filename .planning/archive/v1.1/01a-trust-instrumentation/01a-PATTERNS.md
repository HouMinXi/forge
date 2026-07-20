# Phase 1a: Trust Instrumentation - Pattern Map

**Mapped:** 2026-05-12
**Files analyzed:** 6 new/modified files
**Analogs found:** 6 / 6

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `skills/forge/SKILL.md` | skill | instruction | `skills/forge/SKILL.md` | exact (self-modification) |
| `hooks/check_review_tracker.sh` | hook | event-driven | `hooks/check_review_tracker.sh` | exact (self-modification) |
| `cli/forge_cli.py` | CLI wrapper | request-response | `/home/houminxi/.local/bin/vba_extract.py` | utility-match |
| `cli/config.json` | config | file-I/O | `.gitignore` | config-analog |
| `.forge/findings.json` | data store | file-I/O | `~/.local/state/claude/review_tracker_*.json` | state-file |
| `bootstrap/convert_historical.py` | utility | batch transform | `hooks/check_review_tracker.sh` (embedded Python) | transform-match |

## Pattern Assignments

### `skills/forge/SKILL.md` (skill, instruction)

**Analog:** `skills/forge/SKILL.md` (lines 1-417)

**Existing structure pattern** (lines 1-60):
```markdown
---
name: forge
description: "5-step code review pipeline..."
---

# Forge -- Code Review Pipeline

## When to Use
## When NOT to Use
## Arguments
## Prerequisites

# Pipeline Overview

[ASCII diagram]

# Step 0: Pre-Review Gate
# Steps 1-3: Static Review Cycles
```

**State machine pattern** (lines 113-175):
```markdown
## Cycle Counter State Machine

Current state: cycle_counter = 0

After each pass:
  - If findings exist: fix -> reset counter -> loop
  - If clean: increment counter
  - If counter reaches 3: exit to Step 3.5

Hard reset on ANY finding (current behavior - will be modified per D3 severity-gated reset)
```

**Instruction format pattern** (lines 201-250):
```markdown
## Pass 1: qodo-review

**What to check:** [dimensions]
**How to invoke:** Bash tool to call qodo skill
**Output interpretation:** Parse for findings, classify severity

After Pass 1:
  - Record findings to state
  - If findings: [action]
  - If clean: [action]
```

**Tool invocation pattern** (lines 300-350):
```bash
# Use Bash tool to execute checks
claude --skill qodo-review --context <file>
```

**Additions for Phase 1a:**
- Severity mapping table (P0/P1/P2/P3 normalization from qodo/code-review-expert/adversarial-qe)
- Findings persistence instructions (call Python script to append to .forge/findings.json)
- Auto-continue protocol (TRUST-06: zero findings -> immediate proceed)
- Step 0 context fusion block (FUSE-01: serialize Step 0 findings as markdown table)

---

### `hooks/check_review_tracker.sh` (hook, event-driven)

**Analog:** `hooks/check_review_tracker.sh` (lines 1-295)

**Shebang + metadata pattern** (lines 1-16):
```bash
#!/bin/bash
# Review tracker hook (B2+C1)
#
# PostToolUse on Bash  -> detect real qodo execution, parse findings, update state
# PreToolUse  on Edit/Write -> count modifications, enforce 3-round budget
#
# State machine:
#   - qodo_runs: total qodo executions detected
#   - last_qodo_has_findings: boolean
#   - rounds_with_findings: counter (target: <= 3)
#   - review_passed: boolean gate
```

**Embedded Python pattern** (lines 18-294):
```bash
export CLAUDE_SESSION_PID=${CLAUDE_SESSION_PID:-$PPID}

INPUT=$(cat)
PYFILE=$(mktemp /tmp/hook_py.XXXXXX) || exit 0
trap 'rm -f "$PYFILE"' EXIT
cat >"$PYFILE" <<'PYEOF'
import sys, json, os, re, fcntl, tempfile

# [Python logic here]

PYEOF
printf '%s' "$INPUT" | python3 "$PYFILE"
```

**State file atomic write pattern** (lines 69-81):
```python
def _save_state(st):
    dir_name = os.path.dirname(STATE) or '/tmp'
    fd, tmp = tempfile.mkstemp(dir=dir_name, suffix='.json')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(st, f)
        os.replace(tmp, STATE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
```

**State load pattern** (lines 54-66):
```python
def _load_state():
    try:
        with open(STATE, 'r') as f:
            return json.load(f)
    except Exception:
        return {
            'qodo_runs': 0,
            'last_qodo_has_findings': False,
            'mod_since_last_qodo': 0,
            'rounds_with_findings': 0,
            'review_passed': False,
            'hard_stopped': False,
        }
```

**Finding detection pattern** (lines 116-191):
```python
def _has_findings(output):
    """Parse qodo output to determine if it contains P0/P1/P2 findings."""
    # Strong finding signals - if any match, definitely has findings
    finding_signals = [
        r'\bP0\b', r'\bP1\b', r'\bP2\b',
        r'REQUEST_CHANGES',
        r'\bmust\s+fix\b',
        # Chinese patterns exist in actual code (lines 128-142)
    ]
    if any(re.search(s, output, re.IGNORECASE) for s in finding_signals):
        return True

    # Clean signals
    clean_signals = [
        r'APPROVE',
        r'no\s+(?:new\s+)?issues?',
        # Chinese patterns exist in actual code (lines 157-168)
    ]
    if any(re.search(s, output, re.IGNORECASE) for s in clean_signals):
        return False

    # Conservative: unknown = has findings
    return True
```

**Additions for Phase 1a:**
- Modify `_has_findings()` to return severity level (P0/P1/P2/P3) instead of boolean
- Add severity-gated reset logic per D3 (P0/P1 = full reset, P2 = cycle restart, P3 = accumulate)
- Read from `.forge/current_session.json` sidecar file for severity data

---

### `cli/forge_cli.py` (CLI wrapper, request-response)

**Analog:** `/home/houminxi/.local/bin/vba_extract.py` (lines 1-80)

**Shebang + imports pattern** (lines 1-14):
```python
#!/usr/bin/python3

# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>

import sys
from zipfile import BadZipFile, ZipFile
```

**CLI argument parsing pattern** (lines 32-45):
```python
# Get the xlsm file name from the commandline.
if len(sys.argv) > 1:
    xlsm_file = sys.argv[1]
else:
    print(
        "\nUtility to extract a vbaProject.bin binary from an Excel 2007+ "
        "xlsm macro file for insertion into an XlsxWriter file.\n"
        "\n"
        "Usage: vba_extract file.xlsm\n"
    )
    sys.exit()
```

**Error handling pattern** (lines 60-79):
```python
except IOError as e:
    print(f"File error: {str(e)}")
    sys.exit()

except KeyError as e:
    print(f"File error: {str(e)}")
    print(f"File may not be an Excel xlsm macro file: '{xlsm_file}'")
    sys.exit()

except Exception as e:
    # Catch any other exceptions.
    print(f"File error: {str(e)}")
    sys.exit()
```

**Core CLI wrapper pattern for forge (new, based on vba_extract.py structure):**
```python
#!/usr/bin/python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>

import argparse
import json
import os
import subprocess
import sys

def run_forge(diff_spec, dry_run=False):
    """Invoke claude -p with forge SKILL.md as system prompt."""
    # [Implementation per RESEARCH.md Pattern 5]
    pass

def show_stats():
    """Display FP rate dashboard from findings.json."""
    # [Implementation per RESEARCH.md Pattern 5]
    pass

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Forge code review CLI wrapper')
    parser.add_argument('diff_spec', nargs='?', help='git diff spec to review')
    parser.add_argument('--dry-run', action='store_true', help='Step 0 only, no LLM cost')
    parser.add_argument('--stats', action='store_true', help='Show FP rate dashboard')
    parser.add_argument('--stats-json', dest='stats_json', action='store_true', help='Machine-readable dashboard')
    parser.add_argument('--bootstrap', help='Load historical FP data from file')
    args = parser.parse_args()

    if args.stats or args.stats_json:
        show_stats(json_format=args.stats_json)
    elif args.bootstrap:
        bootstrap_historical(args.bootstrap)
    elif args.diff_spec:
        run_forge(args.diff_spec, dry_run=args.dry_run)
    else:
        parser.print_help()
```

**Additions for forge CLI:**
- `argparse` for subcommands (--stats, --dry-run, --bootstrap)
- `subprocess.run()` to invoke `claude -p --append-system-prompt-file` with timeout (600s)
- JSON parsing of `--output-format json` output to extract token counts and cost
- Dashboard rendering with terminal table (string format, 65-char width per RESEARCH.md)
- Atomic file write to .forge/findings.json using tempfile.mkstemp + os.replace

---

### `cli/config.json` (config, file-I/O)

**Analog:** `.gitignore` (simple structured config file)

**Format pattern:**
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

**No analog code to copy** - this is a pure data file. Structure defined in RESEARCH.md Code Examples section (lines 567-588).

---

### `.forge/findings.json` (data store, file-I/O)

**Analog:** `~/.local/state/claude/review_tracker_*.json` (state persistence pattern from check_review_tracker.sh)

**Schema pattern** (based on check_review_tracker.sh state structure):
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

**Atomic write pattern** - copied from check_review_tracker.sh lines 69-81 (see hook section above)

**Load pattern** - copied from check_review_tracker.sh lines 54-66 (see hook section above)

**Storage location:** `.forge/` directory (gitignored)

---

### `bootstrap/convert_historical.py` (utility, batch transform)

**Analog:** `hooks/check_review_tracker.sh` (embedded Python pattern for JSON manipulation)

**Embedded Python pattern from hook** (lines 23-293):
```python
import sys, json, os, re, fcntl, tempfile

# State manipulation logic
def _load_state():
    try:
        with open(STATE, 'r') as f:
            return json.load(f)
    except Exception:
        return { /* defaults */ }

def _save_state(st):
    fd, tmp = tempfile.mkstemp(dir=dir_name, suffix='.json')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(st, f)
        os.replace(tmp, STATE)
    except Exception:
        # cleanup
        raise
```

**Bootstrap script pattern (new, based on hook's JSON manipulation):**
```python
#!/usr/bin/python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>

import json
import sys
import uuid
import datetime
import os
import tempfile

def parse_historical_analysis(filepath):
    """Parse /tmp/draft_20260512_historical_review_analysis.txt into findings."""
    # Read structured sections (15 FP instances)
    # Extract: file, dimension, severity, description, category
    # Fill sentinel values: line=-1, cost_tokens={input:0, output:0}
    pass

def convert_to_schema(instances):
    """Convert parsed instances to D1 schema."""
    findings = []
    for inst in instances:
        findings.append({
            'id': str(uuid.uuid4()),
            'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'file': inst.get('file', 'unknown'),
            'line': inst.get('line', -1),  # Sentinel: historical data has no line nums
            'dimension': inst.get('dimension', 'unknown'),
            'pass': inst.get('pass', 0),
            'cycle': inst.get('cycle', 0),
            'severity': inst.get('severity', 'P3'),
            'description': inst.get('description', ''),
            'outcome': 'rejected',  # Historical FPs are all rejected
            'reject_reason': inst.get('category', 'HALLUCINATION'),
            'commit_sha': 'historical',
            'cost_tokens': {'input': 0, 'output': 0},
        })
    return {'version': 1, 'findings': findings, 'runs': []}

def atomic_write(filepath, data):
    """Atomic JSON write using tempfile + os.replace."""
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

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: convert_historical.py <analysis_file>")
        sys.exit(1)
    
    instances = parse_historical_analysis(sys.argv[1])
    data = convert_to_schema(instances)
    atomic_write('.forge/findings.json', data)
    print(f"Bootstrapped {len(data['findings'])} historical findings to .forge/findings.json")
```

---

## Shared Patterns

### JSON State Persistence (Atomic Write)
**Source:** `hooks/check_review_tracker.sh` lines 69-81
**Apply to:** All code that writes to .forge/findings.json, cli/config.json, or state files

```python
import tempfile, os, json

def atomic_write(filepath, data):
    """Atomic JSON write to avoid corruption on crash/concurrent access."""
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

### Subprocess Invocation with Timeout
**Source:** Standard pattern from RESEARCH.md Pattern 5
**Apply to:** CLI wrapper invoking `claude -p`

```python
import subprocess

result = subprocess.run(
    ['claude', '-p', prompt,
     '--append-system-prompt-file', skill_path,
     '--output-format', 'json',
     '--allowedTools', 'Bash,Read,Edit,Write,Grep,Glob'],
    capture_output=True,
    text=True,
    timeout=600  # 10 minutes
)
if result.returncode != 0:
    print(f"Error: claude exited with code {result.returncode}", file=sys.stderr)
    print(result.stderr, file=sys.stderr)
    sys.exit(1)
```

### Argparse CLI Pattern
**Source:** Standard Python argparse pattern (vba_extract.py uses simple sys.argv, but forge needs subcommands)
**Apply to:** CLI wrapper

```python
import argparse

parser = argparse.ArgumentParser(description='Forge code review CLI wrapper')
subparsers = parser.add_subparsers(dest='command')

# forge <diff-spec>
review_parser = subparsers.add_parser('review')
review_parser.add_argument('diff_spec', help='git diff spec')
review_parser.add_argument('--dry-run', action='store_true')

# forge --stats
stats_parser = subparsers.add_parser('stats')
stats_parser.add_argument('--json', action='store_true')

# forge --bootstrap
bootstrap_parser = subparsers.add_parser('bootstrap')
bootstrap_parser.add_argument('file', help='historical analysis file')

args = parser.parse_args()
```

### Severity Normalization Table
**Source:** RESEARCH.md Pitfall 4 (lines 395-406)
**Apply to:** SKILL.md additions, hook modifications

| qodo-review | code-review-expert | adversarial-qe | Normalized |
|-------------|-------------------|----------------|------------|
| Red (must fix) | P0 Critical | Critical | P0 |
| Red (must fix) | P1 High | High | P1 |
| Yellow (problematic) | P2 Medium | Medium | P2 |
| Green (minor) | P3 Low | Low/Nit | P3 |

### ISO-8601 Timestamp Pattern
**Source:** Standard Python datetime pattern
**Apply to:** All finding records, run metadata

```python
import datetime

timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
# Result: "2026-05-12T10:30:00+00:00"
```

### UUID Generation Pattern
**Source:** Standard Python uuid pattern
**Apply to:** Finding IDs, run IDs

```python
import uuid

finding_id = str(uuid.uuid4())
# Result: "550e8400-e29b-41d4-a716-446655440000"
```

---

## No Analog Found

No files in this phase lack a close analog. All patterns have either exact self-modification analogs (SKILL.md, hook) or well-matched utility/config analogs.

---

## Metadata

**Analog search scope:** /home/houminxi/code/forge/, /home/houminxi/.local/bin/
**Files scanned:** 15 (skills, hooks, reference CLI scripts)
**Pattern extraction date:** 2026-05-12
