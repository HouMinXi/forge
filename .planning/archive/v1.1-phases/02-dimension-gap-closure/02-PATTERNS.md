# Phase 2: Dimension Gap Closure - Pattern Map

**Mapped:** 2026-05-12
**Files analyzed:** 10 new/modified files
**Analogs found:** 10 / 10

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `cli/forge_cli.py` (Step 0b complexity) | controller | transform | `cli/forge_cli.py:run_dry_run()` lines 1070-1216 | exact |
| `cli/forge_cli.py` (custom rule loader) | service | file-I/O | `bootstrap/convert_historical.py:parse_historical_analysis()` lines 98-132 | role-match |
| `cli/forge_cli.py` (co-location analysis) | service | transform | `cli/forge_cli.py:backfill_confidence()` lines 246-325 | exact |
| `cli/forge_cli.py` (shadow mode filter) | utility | transform | `cli/forge_cli.py:evaluate_dimensions()` lines 699-882 | exact |
| `cli/forge_cli.py` (argparse extensions) | controller | request-response | `cli/forge_cli.py:main()` lines 1911-1994 | exact |
| `cli/config.json` (new keys) | config | -- | `cli/config.json` (existing) | exact |
| `skills/forge/SKILL.md` (shadow dims) | config | -- | `skills/forge/SKILL.md` lines 1-5 (frontmatter) + 285-295 (dim list) | exact |
| `skills/adversarial-qe/SKILL.md` (dim 9 expansion) | config | -- | `skills/adversarial-qe/SKILL.md` lines 89-95 (convention adherence) | exact |
| `tests/seed_tests/` (seed test diffs) | test | file-I/O | No test directory exists yet; use `bootstrap/convert_historical.py` structure | partial |
| `tests/test_*.py` (unit tests) | test | request-response | No Python test files exist; use `forge_cli.py` function signatures as contract | partial |

## Pattern Assignments

### `cli/forge_cli.py` -- Step 0b Complexity Check (controller, transform)

**Analog:** `cli/forge_cli.py:run_dry_run()` lines 1070-1216

**Integration point pattern** (lines 1117-1201) -- where to insert Step 0b:
```python
    for filepath in changed_files:
        if not os.path.isfile(filepath):
            continue  # deleted file, skip

        # Step 0a: Syntax check
        if filepath.endswith('.sh') or filepath.endswith('.bash'):
            # bash -n
            r = subprocess.run(
                ['bash', '-n', filepath],
                capture_output=True, text=True, timeout=30, check=False,
            )
            if r.returncode != 0:
                findings.append(
                    ('syntax', filepath, 'bash -n', r.stderr.strip())
                )
                total_issues += 1
            # ... shellcheck ...

        elif filepath.endswith('.py'):
            # python3 -m py_compile
            r = subprocess.run(
                [sys.executable, '-m', 'py_compile', filepath],
                capture_output=True, text=True, timeout=30, check=False,
            )
            # ... pylint/ruff ...

        # Step 0c: Non-ASCII check (all file types)
        # ... grep -P ...
```

**Finding tuple pattern** (lines 1129-1131) -- how Step 0 findings are structured:
```python
findings.append(
    ('syntax', filepath, 'bash -n', r.stderr.strip())
)
total_issues += 1
```
Step 0b complexity findings MUST use the same 4-tuple `(category, filepath, tool, detail)` format. Use `'complexity'` as category.

**Graceful degradation pattern** (lines 1148, 1186, 1200) -- optional tool handling:
```python
except FileNotFoundError:
    pass  # shellcheck not installed
```
Radon import failure MUST use this same silent-skip pattern (try/except ImportError inside the check function, not at module level).

**Report pattern** (lines 1204-1215) -- how findings are displayed:
```python
    if findings:
        print(
            f"\nforge: Step 0 found {total_issues} issue(s):\n"
        )
        for category, fpath, tool, detail in findings:
            print(f"  [{category}] {fpath} ({tool}): {detail}")
        print(
            f"\nforge: FAIL -- fix {total_issues} issue(s) before review"
        )
        sys.exit(1)
    else:
        print("\nforge: Step 0 PASS -- all checks clean")
```

---

### `cli/forge_cli.py` -- Custom Rule Loader (service, file-I/O)

**Analog:** `bootstrap/convert_historical.py` lines 42-132

**File search pattern** (lines 42-52) -- multi-path file discovery:
```python
HISTORICAL_PATHS = [
    '.planning/research/historical_review_analysis.txt',
    '/tmp/draft_20260512_historical_review_analysis.txt',
]

def find_historical_file(explicit_path=None):
    """Find the historical analysis file."""
    if explicit_path and os.path.isfile(explicit_path):
        return explicit_path
    for path in HISTORICAL_PATHS:
        if os.path.isfile(path):
            return path
    return None
```
Custom rule loader should follow this multi-path discovery: single file `forge-rules.md` then directory `.forge/rules/*.md`.

**Text parsing pattern** (lines 55-81) -- structured text extraction:
```python
def _parse_case_block(block):
    """Parse a single case block into a dict."""
    fields = {}
    current_key = None
    for line in block.splitlines():
        m = re.match(r'^(Source|Context|Finding|...):\s*(.*)', line)
        if m:
            current_key = m.group(1).lower()
            fields[current_key] = m.group(2).strip()
        elif current_key and line.startswith('  '):
            fields[current_key] += ' ' + line.strip()
    return fields
```
Custom rule YAML frontmatter extraction uses `re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)$', content, re.DOTALL)` + `yaml.safe_load()` instead.

**Validation pattern** (lines 83-94, 183-202) -- classify/reject invalid input:
```python
VALID_REJECT_REASONS = {
    'HALLUCINATION', 'CONTEXT_MISSING', 'INTENTIONAL',
    'NOT_APPLICABLE', 'STYLE_PREFERENCE', 'ACCEPTABLE_RISK',
}

def map_reject_reason(classification_text):
    """Map classification text to one of 6 valid reject reasons."""
    text_upper = classification_text.upper()
    for reason in VALID_REJECT_REASONS:
        if reason in text_upper:
            return reason
    # Fallback heuristics ...
    return 'HALLUCINATION'
```
Custom rule validation should use the same set-based validation pattern: `VALID_SEVERITIES = {'critical', 'high', 'medium', 'low'}`.

**Error handling pattern** -- stderr + return None (not sys.exit):
```python
# forge_cli.py lines 84-99
if not os.path.isfile(config_path):
    print(
        f"Error: config.json not found at {config_path}",
        file=sys.stderr,
    )
    sys.exit(1)
```
Custom rules should use `print(..., file=sys.stderr)` for warnings but `sys.exit(1)` only for duplicate name conflicts (fatal). Missing frontmatter or invalid YAML should return None (non-fatal skip).

---

### `cli/forge_cli.py` -- Co-Location Analysis (service, transform)

**Analog:** `cli/forge_cli.py:backfill_confidence()` lines 246-325

**Grouping pattern** (lines 276-287) -- group findings by (file, line, dimension):
```python
    # Step 2: Compute pass_agreement per finding by grouping
    # findings by (file, line, dimension) to detect multi-pass agreement
    location_groups = {}
    for f in findings:
        key = (
            f.get('file', ''),
            f.get('line', -1),
            f.get('dimension', ''),
        )
        if key not in location_groups:
            location_groups[key] = set()
        location_groups[key].add(f.get('pass', 1))
```
Co-location analysis reuses the same grouping but with key `(file, line)` and collects `set()` of dimensions instead of passes.

**Per-dimension stats aggregation** (lines 264-274):
```python
    dim_stats = {}
    for f in findings:
        dim = f.get('dimension', 'unknown')
        if dim not in dim_stats:
            dim_stats[dim] = {'decided': 0, 'tool_errors': 0}
        if f.get('outcome') in ('accepted', 'rejected'):
            dim_stats[dim]['decided'] += 1
            if f.get('reject_reason') in TOOL_ERROR_REASONS:
                dim_stats[dim]['tool_errors'] += 1
```

**Data access pattern** (lines 102-113) -- load findings with fallback:
```python
def load_findings():
    """Load .forge/findings.json."""
    try:
        with open(FINDINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, IOError):
        return {'version': 1, 'findings': [], 'runs': []}
```

---

### `cli/forge_cli.py` -- Shadow Mode Filter (utility, transform)

**Analog:** `cli/forge_cli.py:evaluate_dimensions()` lines 699-882

**Finding filter pattern** (lines 727-733) -- group and filter by dimension:
```python
    dims = {}
    for f in findings:
        dim = f.get('dimension', 'unknown')
        if dim not in dims:
            dims[dim] = []
        dims[dim].append(f)
```
Shadow mode filter adds one line before grouping: `findings = [f for f in findings if not f.get('shadow', False)]`.

**Provisional/insufficient-data pattern** (lines 744-757):
```python
        if provisional:
            report[dim] = {
                'total_observations': total_decided,
                'provisional': True,
                'criteria': {
                    'understandable': 'insufficient data',
                    'actionable': 'insufficient data',
                    'fp_rate': 'insufficient data',
                    'significant_impact': 'insufficient data',
                },
                'tool_fp_rate': None,
                'user_fp_rate': None,
            }
            continue
```
Shadow dimension evaluation should report "insufficient data" when shadow finding count < 20.

---

### `cli/forge_cli.py` -- Argparse Extensions (controller, request-response)

**Analog:** `cli/forge_cli.py:main()` lines 1911-1994

**Argument declaration pattern** (lines 1928-1959):
```python
    parser.add_argument(
        '--stats', action='store_true',
        help='Show FP rate dashboard from findings.json',
    )
    parser.add_argument(
        '--json', action='store_true',
        help='Output dashboard in JSON format (use with --stats)',
    )
    # ...
    parser.add_argument(
        '--eval', action='store_true',
        help='Evaluate dimensions against Tricorder 4 criteria (D5)',
    )
```
New flags `--shadow` and `--colocation` follow the same `add_argument(action='store_true')` pattern.

**Dispatch pattern** (lines 1963-1989):
```python
    if args.eval:
        data = load_findings()
        evaluate_dimensions(
            data.get('findings', []), json_format=args.json,
        )
    elif args.recommend:
        show_recommendations(json_format=args.json)
    elif args.stats:
        show_stats(json_format=args.json)
```
New commands added as `elif` branches in the same chain.

---

### `cli/config.json` -- New Configuration Keys (config)

**Analog:** `cli/config.json` (existing, 35 lines)

**Existing structure pattern** (lines 1-35):
```json
{
  "pricing": { ... },
  "default_model": "claude-sonnet-4-6",
  "tier_classification": {
    "critical_patterns": [ ... ],
    "ai_markers": [ ... ],
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
New keys are added as top-level sections following the same nesting pattern. Add:
- `"complexity"` section with `python_cc_threshold`, `shell_line_threshold`
- `"custom_rules"` section with `max_rules`, `max_tokens`
- `"colocation"` section with `min_colocation_findings`, `merge_threshold`

**Config access pattern** from `forge_cli.py` (lines 634-636, 717-719):
```python
    small_threshold = config.get(
        'tier_classification', {},
    ).get('small_diff_threshold', 10)
```
All new config reads MUST use this chained `.get()` with defaults pattern.

---

### `skills/forge/SKILL.md` -- Shadow Mode Dimensions (config)

**Analog:** `skills/forge/SKILL.md` lines 285-295 (adversarial-qe 12 dimensions reference)

**Dimension list pattern** (lines 285-295 in SKILL.md):
```markdown
- 12 attack dimensions:
  1. Correctness and logic
  2. Edge cases and boundaries (including "successful command, empty output" pattern)
  3. Error handling and resilience
  4. Security (injection, auth, secrets, TOCTOU)
  5. Concurrency (races, deadlocks, lifecycle)
  6. API and contract (breaking changes, validation)
  7. Bidirectional correctness (round-trip encode/decode)
  8. Graceful degradation (missing optional dependencies)
  9. Convention adherence (grep FULL FILE, not just diff)
  10. Performance and scalability
  11. Test quality
  12. AI-generated code smells
```
New shadow dimensions (DIM-01 doc completeness, DIM-04 change scope) are appended to this list with explicit `[SHADOW]` annotation.

**Finding validation set pattern** (SKILL.md finding persistence block, lines 343-347):
```python
VALID_DIMENSIONS = {
    'correctness', 'security', 'performance', 'style', 'architecture',
    'concurrency', 'api_contract', 'bidirectional', 'graceful_degradation',
    'convention', 'test_quality', 'ai_code_smell', 'unknown',
}
```
New shadow dimensions must be added to this set: `'doc_completeness'`, `'change_scope'`.

**Shadow finding persistence pattern** -- extends the finding schema (SKILL.md lines 372-393):
```python
data['findings'].append({
    'id': str(uuid.uuid4()),
    # ... standard fields ...
    'outcome': 'pending',
    'reject_reason': None,
    'commit_sha': commit_sha,
    # NEW field for shadow mode:
    'shadow': True,  # DIM-01/DIM-04 findings not shown to user
})
```

---

### `skills/adversarial-qe/SKILL.md` -- Dim 9 Expansion (config)

**Analog:** `skills/adversarial-qe/SKILL.md` lines 89-95 (convention adherence section)

**Existing dim 9 pattern** (lines 89-95):
```markdown
### Convention adherence

- Sibling consistency: does new code follow the same patterns as existing
  code in the same file/module? Check error handling, resource cleanup,
  tool readiness, naming. E.g., new test uses `ovs_wait` like siblings,
  not ad-hoc `sleep 2`.
- Framework idioms: does the code use the project's established
  helpers/utilities instead of ad-hoc reimplementations?
- Style drift: is the new code detectably different in structure from
  its neighbors (different error handling pattern, different logging
  style, different assertion approach)?
- **Cross-function pattern grep**: when new code introduces error messages,
  log strings, or naming conventions, grep the FULL FILE (not just the
  diff) for the same pattern in other functions.
- _Scope note: this dimension focuses on file-local and module-local
  consistency._
```
DIM-02 (naming quality) expands this section by adding naming-specific bullets. DIM-05 (readability) adds readability-specific bullets (nesting depth, function length, control flow clarity). Both are absorbed into the existing section header, not new sections.

**Dimension section structure pattern** (used by all 12 dimensions):
```markdown
### [Dimension Name]

- [Primary check bullet]
- [Secondary check bullet]
- [Edge case bullet]
- [Example where applicable]
```
Keep the same structure -- add new bullets under the existing `### Convention adherence` heading.

---

### `tests/seed_tests/` -- Seed Test Synthetic Diffs (test, file-I/O)

**Analog:** `bootstrap/convert_historical.py` (file structure and data pattern)

**File organization pattern:**
```
bootstrap/
  convert_historical.py    # standalone script with main()
```
Seed tests should follow the same standalone-script pattern:
```
tests/
  seed_tests/
    test_dim_performance.py     # one test per zero-data dimension
    test_dim_concurrency.py
    test_dim_error_handling.py
    ...
    seed_diffs/                  # synthetic diff files
      performance_unbounded_loop.diff
      concurrency_unsynchronized.diff
      ...
```

**Script structure pattern** from `convert_historical.py` (lines 352-380):
```python
def main():
    explicit_path = sys.argv[1] if len(sys.argv) >= 2 else None
    filepath = find_historical_file(explicit_path)
    if filepath is None:
        print("No historical analysis file found.", file=sys.stderr)
        # ... handle gracefully ...
        return
    # ... process ...

if __name__ == '__main__':
    main()
```

**Atomic write pattern** reused from both `forge_cli.py` and `convert_historical.py`:
```python
def atomic_write(filepath, data):
    """Atomically write JSON data using tempfile + os.replace."""
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

---

### `tests/test_*.py` -- Unit Tests (test, request-response)

**Analog:** No existing Python test files in the project.

**Function signature contracts** from `forge_cli.py` to test against:

```python
# Complexity check -- returns list of violation tuples
def check_python_complexity(filepath, threshold=15):
    # Returns: list of (file, line, function, complexity, rank) dicts

# Custom rule loader -- returns list of rule dicts
def load_custom_rules(project_root='.'):
    # Returns: list of {'metadata': dict, 'body': str, 'source': str}

# Co-location analysis -- returns dict of (dim1, dim2) -> count
def compute_colocation_matrix(findings):
    # Returns: dict {(dim1, dim2): int}

# Shadow filter -- returns filtered list
def evaluate_dimensions_filtered(findings, include_shadow=False):
    # Returns: list of finding dicts
```

**Project Python style** (from `forge_cli.py` header):
```python
#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Module docstring."""
```

**Import convention** (from `forge_cli.py` lines 33-44):
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
All stdlib imports sorted alphabetically, one per line. No third-party imports at module level (radon is imported inside functions with try/except).

---

## Shared Patterns

### Atomic JSON Write
**Source:** `cli/forge_cli.py` lines 132-150
**Apply to:** All code that writes to `.forge/findings.json`, `.forge/rules/` validation output, seed test results
```python
def atomic_write(filepath, data):
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

### Chained Config Access with Defaults
**Source:** `cli/forge_cli.py` lines 634-636, 717-719
**Apply to:** All new config reads (complexity thresholds, custom rule limits, co-location thresholds)
```python
threshold = config.get(
    'section_name', {},
).get('key_name', default_value)
```

### Finding Schema Extension
**Source:** `skills/forge/SKILL.md` lines 372-393 (finding persistence heredoc)
**Apply to:** Shadow mode findings (DIM-01, DIM-04)
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
    'description': '...',
    'outcome': 'pending',
    'reject_reason': None,
    'commit_sha': commit_sha,
    'cost_tokens': {'input': 0, 'output': 0},
    'confidence': 0.0,
    'confidence_signals': { ... },
    # Phase 2 addition:
    'shadow': True,  # or False for non-shadow dimensions
})
```

### Error Reporting to stderr
**Source:** `cli/forge_cli.py` lines 86-90 (load_config), `bootstrap/convert_historical.py` lines 357-360
**Apply to:** All new error paths in custom rule loader, co-location analysis
```python
print(
    f"Error: {description}",
    file=sys.stderr,
)
```
Fatal errors use `sys.exit(1)`. Non-fatal warnings use `print(..., file=sys.stderr)` and continue.

### Dimension Grouping Loop
**Source:** `cli/forge_cli.py` lines 727-733 (evaluate_dimensions), lines 264-274 (backfill_confidence)
**Apply to:** Co-location matrix computation, shadow mode dimension statistics
```python
dims = {}
for f in findings:
    dim = f.get('dimension', 'unknown')
    if dim not in dims:
        dims[dim] = []
    dims[dim].append(f)
```

### Terminal Table Output
**Source:** `cli/forge_cli.py` lines 817-876 (evaluate_dimensions table), lines 1620-1668 (show_stats table)
**Apply to:** Co-location matrix display, shadow dimension stats
```python
print("=" * 82)
print("Title")
print("=" * 82)
print()
header = f"{'Col1':<18} {'Col2':>8} {'Col3':>8}"
print(header)
print("-" * 82)
# ... rows ...
print("-" * 82)
print()
print("Legend:")
print("  explanation")
```

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `tests/seed_tests/seed_diffs/*.diff` | test data | -- | Synthetic diff files are a new artifact type. No existing diff files in the project. Content must be designed per D1 seed test requirements (trigger exactly one dimension). |
| `.forge/rules/*.md` | config | -- | New file format (YAML frontmatter + Markdown body). No existing files use this format in the project. Format defined by D4 specification in CONTEXT.md. |
| `forge-rules.md` | config | -- | Same as above -- single-file variant of custom rules. |

## Metadata

**Analog search scope:** `/home/houminxi/code/forge/` (cli/, bootstrap/, skills/, hooks/)
**Files scanned:** 14 (2 Python, 6 shell hooks, 5 SKILL.md files, 1 JSON config)
**Pattern extraction date:** 2026-05-12
