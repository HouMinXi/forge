# Phase 03: Adaptive Learning MVP - Pattern Map

**Mapped:** 2026-05-13
**Files analyzed:** 15 new/modified files
**Analogs found:** 15 / 15

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `cli/forge_cli.py` (extend argparse + main) | controller | request-response | `cli/forge_cli.py` main() lines 2509-2614 | exact (self) |
| `cli/adapters/__init__.py` | config | -- | (new package, no analog needed) | n/a |
| `cli/adapters/base.py` | model | transform | `bootstrap/convert_historical.py` lines 1-20 | role-match |
| `cli/adapters/github_pr.py` | service | request-response | `cli/forge_cli.py` _invoke_claude() lines 1800-1880 | role-match |
| `cli/adapters/git_log.py` | service | request-response | `cli/forge_cli.py` _get_changed_files() lines 337-371 | exact |
| `cli/adapters/ci_log.py` | service | file-I/O | `cli/forge_cli.py` load_findings() lines 107-118 | role-match |
| `cli/llm_parser.py` | service | request-response | `cli/forge_cli.py` _invoke_claude() lines 1800-1880 | role-match |
| `cli/gap_detector.py` | service | transform | `cli/forge_cli.py` evaluate_dimensions() lines 704-896 | role-match |
| `cli/gap_manager.py` | controller | CRUD + interactive | `cli/forge_cli.py` classify_findings() lines 2399-2503 | exact |
| `cli/dimension_manager.py` | controller | CRUD | `cli/forge_cli.py` promote_shadow_dimension() lines 1279-1322 | exact |
| `cli/migration.py` | utility | transform | `bootstrap/convert_historical.py` lines 205-323 | exact |
| `cli/escalation.py` | service | transform | `cli/forge_cli.py` evaluate_dimensions() lines 704-896 | role-match |
| `cli/config.json` (extend) | config | -- | `cli/config.json` (self) | exact (self) |
| `tests/seed_tests/run_seed_tests.py` (extend) | test | batch | `tests/seed_tests/run_seed_tests.py` (self) | exact (self) |
| `tests/fixtures/` (new test data) | test | file-I/O | `tests/seed_tests/seed_diffs/` | role-match |

## Pattern Assignments

### `cli/forge_cli.py` (controller, request-response) -- EXTEND

**Analog:** Self -- lines 2509-2614

This file is extended, not created. New CLI arguments are added to main() and new elif branches dispatch to new modules.

**Argparse extension pattern** (lines 2509-2570):
```python
parser = argparse.ArgumentParser(
    prog='forge',
    description=(
        'Forge code review CLI '
        '-- standalone wrapper for Claude Code'
    ),
)
# ... existing args ...
parser.add_argument(
    '--promote', metavar='DIM',
    help='Promote shadow dimension to active (R6)',
)
```

New arguments to add follow the same style: `parser.add_argument('--learn', ...)`, `parser.add_argument('--gaps', ...)`, `parser.add_argument('--propose', ...)`, `parser.add_argument('--add-dimension', ...)`, `parser.add_argument('--retire', ...)`, `parser.add_argument('--reclassify', ...)`, `parser.add_argument('--ci-file', ...)`, `parser.add_argument('--pr', ...)`, `parser.add_argument('--branch', ...)`, `parser.add_argument('--keywords-file', ...)`, `parser.add_argument('--include-archived', ...)`, `parser.add_argument('--external', ...)`, `parser.add_argument('--approve-expansion', ...)`.

**Dispatch pattern** (lines 2571-2610):
```python
args = parser.parse_args()

if args.promote:
    promote_shadow_dimension(args.promote)
elif args.colocation:
    show_colocation(json_format=args.json)
elif args.eval:
    data = load_findings()
    evaluate_dimensions(
        data.get('findings', []),
        config=None,
        json_format=args.json,
        include_shadow=getattr(args, 'shadow', False),
    )
```

New dispatches follow the same elif chain. Import new modules at top of file alongside existing imports.

---

### `cli/adapters/base.py` (model, transform)

**Analog:** `bootstrap/convert_historical.py` lines 1-20 (header/structure) + `cli/forge_cli.py` lines 1-50 (imports)

**File header pattern** (`bootstrap/convert_historical.py` lines 1-5):
```python
#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Module docstring describing purpose (D1).

Longer description of what this module does and why.
"""
```

**Import pattern** (`cli/forge_cli.py` lines 36-49):
```python
import argparse
import glob
import json
import math
import os
import random
import re
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
```

**Data model pattern** -- use dataclass (no existing analog in codebase; RESEARCH.md Pattern 1 defines this):
```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class CanonicalFinding:
    """Pre-LLM canonical schema from adapter."""
    source: str           # "github_pr" | "git_log" | "ci_log"
    source_tool: str      # "human" | "qodo" | "coderabbit" | etc.
    source_id: str        # GitHub comment ID, commit SHA, etc.
    timestamp: str        # ISO-8601
    raw_source: str       # Original text
    context: dict         # diff_hunk, pr_url, etc.
```

No ABC base class exists in codebase. This is the first abstract base class. Use Python stdlib `abc.ABC` and `abc.abstractmethod`.

---

### `cli/adapters/github_pr.py` (service, request-response)

**Analog:** `cli/forge_cli.py` _invoke_claude() lines 1800-1880 (subprocess pattern) + `cli/forge_cli.py` _get_changed_files() lines 337-371 (git subprocess)

**Subprocess invocation pattern** (`cli/forge_cli.py` lines 350-367):
```python
try:
    result = subprocess.run(
        ['git', 'diff', '--name-only', diff_spec],
        capture_output=True, text=True, timeout=10, check=False,
    )
    if result.returncode != 0:
        print(
            f"Error: git diff --name-only failed for "
            f"'{diff_spec}': {result.stderr.strip()}",
            file=sys.stderr,
        )
        return None
    return [
        f.strip()
        for f in result.stdout.strip().split('\n')
        if f.strip()
    ]
except (OSError, subprocess.SubprocessError) as exc:
    print(
        f"Error: failed to get changed files: {exc}",
        file=sys.stderr,
    )
    return None
```

The github_pr adapter calls `gh api` via subprocess following this same pattern: `subprocess.run(['gh', 'api', ...], capture_output=True, text=True, timeout=30, check=False)`.

**Error handling pattern** (`cli/forge_cli.py` lines 1862-1880):
```python
if result.returncode != 0:
    print(
        f"Error: claude exited with code {result.returncode}",
        file=sys.stderr,
    )
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return None

try:
    return json.loads(result.stdout)
except json.JSONDecodeError:
    print(
        "Error: failed to parse claude output as JSON",
        file=sys.stderr,
    )
    return None
```

---

### `cli/adapters/git_log.py` (service, request-response)

**Analog:** `cli/forge_cli.py` _get_changed_files() lines 337-371 and _detect_ai_generated() lines 535-590

**Git log subprocess pattern** (`cli/forge_cli.py` lines 576-590):
```python
try:
    result = subprocess.run(
        ['git', 'log', '-1', '--format=%B'],
        capture_output=True, text=True, timeout=10, check=False,
    )
    if result.returncode == 0:
        msg = result.stdout.lower()
        for marker in markers:
            if marker.lower() in msg:
                return True
except (OSError, subprocess.SubprocessError):
    pass
```

The git_log adapter will use `git log --grep="^Revert "` and similar patterns, following the same subprocess.run + capture_output + timeout + check=False convention.

---

### `cli/adapters/ci_log.py` (service, file-I/O)

**Analog:** `cli/forge_cli.py` load_findings() lines 107-118 (file loading with graceful error)

**File loading pattern** (`cli/forge_cli.py` lines 107-118):
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

The ci_log adapter reads a local file (user-provided path via `--ci-file <path>`). Same encoding, same error handling pattern. Uses `open(path, 'r', encoding='utf-8')`.

---

### `cli/llm_parser.py` (service, request-response)

**Analog:** `cli/forge_cli.py` _invoke_claude() lines 1800-1880 (LLM invocation pattern)

This is the first Anthropic SDK integration in the codebase. The existing codebase uses `subprocess.run(['claude', '-p', ...])` for LLM interaction. The new module uses `anthropic.Anthropic().messages.create()` directly.

**Existing LLM pattern (subprocess)** (`cli/forge_cli.py` lines 1817-1832):
```python
try:
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=600, check=False,
    )
except subprocess.TimeoutExpired:
    print(
        "Warning: --append-system-prompt-file timed out after 600s. "
        "Falling back to --system-prompt inline.",
        file=sys.stderr,
    )
```

**New SDK pattern (from RESEARCH.md)** -- no codebase analog, use research example:
```python
import anthropic

client = anthropic.Anthropic()
response = client.messages.create(
    model="claude-haiku-3.5",
    max_tokens=300,
    messages=[{"role": "user", "content": prompt}],
)
text = response.content[0].text
return json.loads(text)
```

**Error handling:** wrap in try/except for `anthropic.APIError`, `json.JSONDecodeError`, follow same stderr print pattern as _invoke_claude.

---

### `cli/gap_detector.py` (service, transform)

**Analog:** `cli/forge_cli.py` evaluate_dimensions() lines 704-896

**Dimension iteration pattern** (`cli/forge_cli.py` lines 742-748):
```python
# Group findings by dimension
dims = {}
for f in findings:
    dim = f.get('dimension', 'unknown')
    if dim not in dims:
        dims[dim] = []
    dims[dim].append(f)
```

**Per-dimension computation pattern** (`cli/forge_cli.py` lines 750-824):
```python
report = {}
for dim in sorted(dims.keys()):
    dim_findings = dims[dim]
    decided = [
        f for f in dim_findings
        if f.get('outcome') in ('accepted', 'rejected')
    ]
    total_decided = len(decided)
    provisional = total_decided < min_obs
    # ... compute metrics ...
    report[dim] = {
        'total_observations': total_decided,
        'provisional': provisional,
        # ...
    }
```

The gap_detector uses keyword_dictionaries from config.json (same `load_config()` call) and iterates dimensions similarly. The D4 three-outcome algorithm is a new transform with no exact analog, but follows the same dict-iteration pattern.

---

### `cli/gap_manager.py` (controller, CRUD + interactive)

**Analog:** `cli/forge_cli.py` classify_findings() lines 2399-2503

**Interactive CLI pattern** (`cli/forge_cli.py` lines 2424-2496):
```python
for seq, (idx, finding) in enumerate(pending, 1):
    print(
        f"--- Finding {seq}/{len(pending)} ---"
    )
    print(f"  File:      {finding.get('file', 'unknown')}")
    # ... display fields ...
    print()

    while True:
        try:
            choice = input(
                "  Classify [a]ccept / [r]eject / [s]kip / [q]uit: "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            if modified:
                atomic_write(FINDINGS_FILE, findings_data)
                print(f"forge: saved {seq} classification(s)")
            return

        if choice in ('a', 'accept'):
            findings[idx]['outcome'] = 'accepted'
            modified = True
            print("  -> accepted\n")
            break
        if choice in ('r', 'reject'):
            # ... nested selection ...
            break
        if choice in ('s', 'skip'):
            print("  -> skipped\n")
            break
        if choice in ('q', 'quit'):
            if modified:
                atomic_write(FINDINGS_FILE, findings_data)
            return
        print("  Invalid choice. Use a/r/s/q.")
```

The gap_manager's `--gaps` interactive flow follows this exact pattern: present items, prompt for action (approve/reject/skip/quit), handle EOFError/KeyboardInterrupt, atomic_write on modification.

**Atomic write on modification pattern** (`cli/forge_cli.py` lines 2448-2450):
```python
if modified:
    atomic_write(FINDINGS_FILE, findings_data)
    print(f"forge: saved {seq} classification(s)")
```

---

### `cli/dimension_manager.py` (controller, CRUD)

**Analog:** `cli/forge_cli.py` promote_shadow_dimension() lines 1279-1322

**Config read-modify-write pattern** (`cli/forge_cli.py` lines 1290-1322):
```python
# (a) Update existing findings
data = load_findings()
findings = data.get('findings', [])
promoted = 0
for f in findings:
    if (f.get('dimension') == dimension_name
            and f.get('shadow', False)):
        f['shadow'] = False
        promoted += 1
if promoted > 0:
    atomic_write(FINDINGS_FILE, data)

# (b) N4 fix: Persist to config so future findings are not shadow
config = load_config()
promoted_list = config.get('promoted_dimensions', [])
if dimension_name not in promoted_list:
    promoted_list.append(dimension_name)
    config['promoted_dimensions'] = promoted_list
    atomic_write(
        os.path.join('cli', 'config.json'), config,
    )

if promoted == 0 and dimension_name in promoted_list:
    print(
        f"Dimension '{dimension_name}' added to promoted "
        f"list (no existing shadow findings to update).",
    )
else:
    print(
        f"Promoted {promoted} findings for "
        f"'{dimension_name}' from shadow to active. "
        f"Future findings will also be active.",
    )
```

This is the primary pattern for dimension lifecycle operations (--add-dimension, --promote rewrite, --retire, --eval extensions). Load config -> mutate dict -> atomic_write -> print status.

**Evaluation display pattern** (`cli/forge_cli.py` lines 830-895):
```python
# Terminal table
print("=" * 82)
print("Forge Dimension Evaluation (Tricorder 4 Criteria)")
print("=" * 82)
print()
header = (
    f"{'Dimension':<18} {'Obs':>4} {'Prov':>5} "
    f"{'ToolFP%':>8} {'CI[95%]':>16} "
    f"{'Impact%':>8} {'Status':>14}"
)
print(header)
print("-" * 82)
# ... rows ...
print("-" * 82)
print()
print("Legend:")
```

The --eval --shadow and --eval --external displays follow this same table format pattern.

---

### `cli/migration.py` (utility, transform)

**Analog:** `bootstrap/convert_historical.py` lines 205-323

**Dimension name mapping** (`bootstrap/convert_historical.py` lines 205-233):
```python
def map_dimension(context_text, finding_text):
    """Map context/finding text to a review dimension name."""
    combined = (context_text + ' ' + finding_text).lower()

    dimension_keywords = [
        ('security', ['security', 'injection', 'vulnerability', 'cve']),
        ('error_handling', ['error', 'exception', 'return code']),
        ('state_management', ['thread', 'state', 'concurren', 'lock']),
        # ...
    ]

    for dimension, keywords in dimension_keywords:
        for kw in keywords:
            if re.search(kw, combined):
                return dimension

    return 'unknown'
```

The migration module uses a RENAME_MAP (not keyword search), but the structure is similar: iterate a dict of old->new names and apply transforms to findings.json entries.

**Data conversion pattern** (`bootstrap/convert_historical.py` lines 275-322):
```python
def convert_to_schema(cases):
    """Convert parsed cases to D1 findings schema."""
    now = datetime.now(timezone.utc).isoformat()
    findings = []
    for case in cases:
        # ... extract fields ...
        findings.append({
            'id': str(uuid.uuid4()),
            'timestamp': now,
            # ... fields ...
        })
    return findings
```

**Atomic write at end pattern** (`bootstrap/convert_historical.py` lines 352-376):
```python
def main():
    # ... load data ...
    existing = load_existing()
    existing['findings'].extend(new_findings)
    atomic_write(FINDINGS_FILE, existing)
    print(f"Bootstrapped {len(new_findings)} historical findings")
```

---

### `cli/escalation.py` (service, transform)

**Analog:** `cli/forge_cli.py` evaluate_dimensions() lines 704-896

Uses the same per-dimension aggregation and report generation pattern. Reads findings.json + external_findings.json, computes metrics, writes to .forge/escalation-status.json.

**Report generation pattern** -- same as evaluate_dimensions (group by dimension, compute metrics, output report dict).

---

### `cli/config.json` (config) -- EXTEND

**Analog:** Self

**Current structure** (`cli/config.json` lines 1-47):
```json
{
  "pricing": { ... },
  "default_model": "claude-sonnet-4-6",
  "tier_classification": { ... },
  "evaluation": { ... },
  "complexity": { ... },
  "custom_rules": { ... },
  "colocation": { ... }
}
```

New keys added at top level: `keyword_dictionaries` (dict of dim -> keyword list), `dimension_states` (dict of dim -> state object). Also add Haiku pricing entry under `pricing`.

---

### `tests/seed_tests/run_seed_tests.py` (test, batch) -- EXTEND

**Analog:** Self -- lines 1-421

**Test mapping pattern** (`tests/seed_tests/run_seed_tests.py` lines 27-70):
```python
SEED_TESTS = {
    'performance_unbounded_loop': {
        'target_dimension': 'performance',
        'description': (
            'N+1 query, removed LIMIT, unbounded memory'
        ),
    },
    # ...
}
```

**CLI argument pattern** (`tests/seed_tests/run_seed_tests.py` lines 334-337):
```python
def main():
    dry_run = '--dry-run' in sys.argv
    script_dir = os.path.dirname(os.path.abspath(__file__))
```

Extend to accept `--dimension <dim> --diff <path>` for proposal-generated seed tests. Use argparse (replacing manual sys.argv parsing) following forge_cli.py's argparse pattern.

---

### `tests/fixtures/` (test data, file-I/O)

**Analog:** `tests/seed_tests/seed_diffs/`

Seed diffs are `.diff` files with `---BEGIN BEFORE---` blocks. Test fixtures will be JSON files (GitHub API responses, git log output, CI log files) organized by adapter type. Follow the same directory convention: `tests/fixtures/github_pr/`, `tests/fixtures/git_log/`, `tests/fixtures/ci_log/`, `tests/fixtures/cross_adapter/`, `tests/fixtures/sashiko_replay/`.

---

## Shared Patterns

### Atomic File Write
**Source:** `cli/forge_cli.py` lines 137-155
**Apply to:** All modules that write .forge/ JSON files (gap_detector, gap_manager, dimension_manager, migration, escalation, llm_parser indirectly)
```python
def atomic_write(filepath, data):
    """Atomically write JSON data to filepath."""
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

### JSON File Loading with Graceful Default
**Source:** `cli/forge_cli.py` lines 107-118
**Apply to:** All modules that read .forge/ JSON files (external_findings, gap_candidates, keyword_expansion_queue, gap_groups)
```python
def load_findings():
    try:
        with open(FINDINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, IOError):
        return {'version': 1, 'findings': [], 'runs': []}
```

Each new JSON file gets a parallel loader function: `load_external_findings()`, `load_gap_candidates()`, `load_keyword_expansion_queue()`, `load_gap_groups()`.

### Config Loading
**Source:** `cli/forge_cli.py` lines 83-104
**Apply to:** All modules that need keyword_dictionaries or dimension_states
```python
def load_config():
    config_path = os.path.realpath(CONFIG_FILE)
    if not os.path.isfile(config_path):
        print(
            f"Error: config.json not found at {config_path}",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as exc:
        print(
            f"Error: failed to load config.json: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)
```

### Subprocess Invocation (git/gh)
**Source:** `cli/forge_cli.py` lines 350-371
**Apply to:** github_pr adapter, git_log adapter
```python
try:
    result = subprocess.run(
        ['git', 'diff', '--name-only', diff_spec],
        capture_output=True, text=True, timeout=10, check=False,
    )
    if result.returncode != 0:
        print(f"Error: ...", file=sys.stderr)
        return None
except (OSError, subprocess.SubprocessError) as exc:
    print(f"Error: ...", file=sys.stderr)
    return None
```

### Error Reporting to stderr
**Source:** `cli/forge_cli.py` lines 92-104
**Apply to:** All modules
```python
print(
    f"Error: config.json not found at {config_path}",
    file=sys.stderr,
)
sys.exit(1)
```

All error messages go to stderr via `file=sys.stderr`. Non-fatal warnings use `print("Warning: ...", file=sys.stderr)`. Fatal errors call `sys.exit(1)`.

### File Header Convention
**Source:** `cli/forge_cli.py` lines 1-31 and `bootstrap/convert_historical.py` lines 1-12
**Apply to:** All new Python files
```python
#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Module docstring -- brief one-line summary.

Longer description explaining purpose, usage, and design decisions.
"""
```

### UUID Generation
**Source:** `cli/forge_cli.py` line 43 (import) and line 2043 (`str(uuid.uuid4())`)
**Apply to:** All modules that create finding IDs, gap candidate IDs, group IDs, expansion IDs
```python
import uuid
# ...
finding_id = f"ext-{uuid.uuid4()}"
gap_id = f"gap-{uuid.uuid4()}"
group_id = f"grp-{uuid.uuid4()}"
exp_id = f"exp-{uuid.uuid4()}"
```

### Timestamp Generation
**Source:** `cli/forge_cli.py` line 44 (import) and various usage sites
**Apply to:** All modules that set timestamps
```python
from datetime import datetime, timezone
now = datetime.now(timezone.utc).isoformat()
```

### Interactive Input with EOFError/KeyboardInterrupt Guard
**Source:** `cli/forge_cli.py` lines 2441-2450
**Apply to:** gap_manager (--gaps interactive mode)
```python
while True:
    try:
        choice = input(
            "  Classify [a]ccept / [r]eject / [s]kip / [q]uit: "
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        if modified:
            atomic_write(FINDINGS_FILE, findings_data)
        return
```

## No Analog Found

Files with no close match in the codebase (planner should use RESEARCH.md patterns instead):

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `cli/llm_parser.py` (Anthropic SDK usage) | service | request-response | No Anthropic SDK usage in codebase; existing LLM integration is subprocess-based `claude -p`. Use RESEARCH.md Pattern 2 (LLM Parser) as reference. |
| `cli/adapters/base.py` (ABC pattern) | model | -- | No abstract base classes in codebase. Use Python stdlib `abc.ABC` + `@abstractmethod`. |
| `.forge/proposals/<dim>/` generation | utility | file-I/O | No directory-as-output pattern exists. Use `os.makedirs` + `atomic_write` per file, with `.tmp-<dim>/` staging + `os.rename`. |

## Metadata

**Analog search scope:** `cli/`, `bootstrap/`, `tests/`, `skills/`, project root
**Files scanned:** 12 source files (excluding worktrees, planning, evidence, hooks)
**Pattern extraction date:** 2026-05-13
