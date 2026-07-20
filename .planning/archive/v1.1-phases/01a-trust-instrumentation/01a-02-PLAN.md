---
phase: 01a-trust-instrumentation
plan: 02
type: execute
wave: 1
depends_on: []
files_modified:
  - .gitignore
  - cli/config.json
  - bootstrap/convert_historical.py
autonomous: true
requirements:
  - TRUST-01
  - TRUST-05

must_haves:
  truths:
    - ".forge/ directory is gitignored so findings.json is never accidentally committed"
    - "Model pricing config exists so CLI wrapper can calculate cost estimates"
    - "Historical FP data (15 instances) is bootstrapped into findings.json schema so Phase 1b calibration has a non-empty starting dataset"
    - "Historical analysis file is copied to .planning/research/ for persistence (not dependent on /tmp)"
  artifacts:
    - path: ".gitignore"
      provides: "Gitignore entry for .forge/ directory"
      contains: ".forge/"
    - path: "cli/config.json"
      provides: "Model pricing configuration for cost estimation"
      contains: "input_per_mtok"
    - path: "bootstrap/convert_historical.py"
      provides: "One-time script to convert historical FP analysis to findings.json schema"
      contains: "def parse_historical_analysis"
  key_links:
    - from: "bootstrap/convert_historical.py"
      to: ".forge/findings.json"
      via: "atomic_write function"
      pattern: "os\\.replace"
---

<objective>
Create supporting infrastructure for Phase 1a: gitignore for .forge/ directory, model pricing config for cost estimation, historical FP data bootstrap script, and persist historical data file from /tmp.

Purpose: Plan 01 defines the findings.json schema in SKILL.md instructions. This plan creates the surrounding infrastructure that makes the schema usable -- preventing accidental commits of findings data, providing pricing data for cost calculation, and bootstrapping the findings database with historical data so the dashboard is not empty on day one.

Output: .gitignore updated, cli/config.json created, bootstrap/convert_historical.py created, historical data persisted.

Review fixes addressed:
- Issue #11 (MEDIUM): Historical data file copied from /tmp to .planning/research/ for persistence
- DeepSeek LOW #9: Bootstrap script filters out non-FP entries (Cases 9-12)
- DeepSeek LOW #11: Mixed classifications split into individual finding records
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/phases/01a-trust-instrumentation/01a-CONTEXT.md
@.planning/phases/01a-trust-instrumentation/01a-RESEARCH.md
@.planning/phases/01a-trust-instrumentation/01a-PATTERNS.md
</context>

<tasks>

<task type="auto">
  <name>Task 1: Add .forge/ to .gitignore, create model pricing config, and persist historical data</name>
  <files>.gitignore, cli/config.json</files>
  <read_first>
    - .gitignore (current content: *.swp, *.swo, *~, .DS_Store)
    - .planning/phases/01a-trust-instrumentation/01a-CONTEXT.md (D8: cost metering decision, model pricing as config)
    - .planning/phases/01a-trust-instrumentation/01a-RESEARCH.md (Model Pricing Config section, lines 567-588)
  </read_first>
  <action>
**Step 1: Update .gitignore**

Append the following lines to the existing .gitignore file (which currently has: *.swp, *.swo, *~, .DS_Store):

```
# Forge per-project data (findings, review state)
.forge/
```

Do NOT remove any existing entries. Append after the existing content with a blank line separator.

**Step 2: Create cli/config.json**

Create the directory `cli/` if it does not exist. Create `cli/config.json` with the following content:

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

Per D8: model pricing is stored as config, updated manually when prices change.

**Step 3: Persist historical data file (addresses review issue #11)**

Copy `/tmp/draft_20260512_historical_review_analysis.txt` to `.planning/research/historical_review_analysis.txt` so it survives /tmp cleanup. Create the directory if needed.

```bash
mkdir -p .planning/research
cp /tmp/draft_20260512_historical_review_analysis.txt .planning/research/historical_review_analysis.txt 2>/dev/null || echo "Note: /tmp source not found, check .planning/research/ for existing copy"
```

The bootstrap script (Task 2) should try the persistent path first, then fall back to /tmp.
  </action>
  <verify>
    <automated>grep -q ".forge/" .gitignore && echo "gitignore OK" && python3 -c "import json; d=json.load(open('cli/config.json')); assert 'pricing' in d; assert 'claude-opus-4-6' in d['pricing']; assert d['pricing']['claude-opus-4-6']['input_per_mtok'] == 15.0; print('config OK')" && (test -f .planning/research/historical_review_analysis.txt && echo "historical data persisted" || echo "historical data not found in /tmp, OK if already persisted")</automated>
  </verify>
  <acceptance_criteria>
    - .gitignore contains the line ".forge/" (exact match, not ".forge" without trailing slash)
    - .gitignore retains all previous entries (*.swp, *.swo, *~, .DS_Store)
    - cli/config.json exists and is valid JSON
    - cli/config.json contains "pricing" key with "claude-opus-4-6" and "claude-sonnet-4-6" sub-keys
    - Each model entry has exactly 4 keys: input_per_mtok, output_per_mtok, cache_read_per_mtok, cache_creation_per_mtok
    - cli/config.json contains "default_model" key set to "claude-sonnet-4-6"
    - .planning/research/historical_review_analysis.txt exists (or graceful note if /tmp source gone)
    - No non-ASCII characters in .gitignore or cli/config.json
  </acceptance_criteria>
  <done>.gitignore prevents .forge/ directory from being committed. cli/config.json provides model pricing for cost estimation. Historical data file is persisted to .planning/research/ for durability.</done>
</task>

<task type="auto">
  <name>Task 2: Create historical FP data bootstrap script</name>
  <files>bootstrap/convert_historical.py</files>
  <read_first>
    - .planning/research/historical_review_analysis.txt OR /tmp/draft_20260512_historical_review_analysis.txt (historical FP analysis -- source data with 15 cases)
    - .planning/phases/01a-trust-instrumentation/01a-CONTEXT.md (D1 schema, D2 taxonomy categories)
    - .planning/phases/01a-trust-instrumentation/01a-PATTERNS.md (bootstrap script pattern, atomic write pattern)
    - .planning/phases/01a-trust-instrumentation/01a-RESEARCH.md (findings.json schema, Pitfall 5: Historical Bootstrap Data Format Mismatch)
    - hooks/check_review_tracker.sh (analog: atomic JSON write pattern via tempfile.mkstemp + os.replace)
  </read_first>
  <action>
Create the directory `bootstrap/` if it does not exist. Create `bootstrap/convert_historical.py`.

The script must:

1. **Parse the historical analysis file.** Try `.planning/research/historical_review_analysis.txt` first, then fall back to `/tmp/draft_20260512_historical_review_analysis.txt` (addresses review issue #11 -- file persistence). If neither exists, create an empty findings.json with the correct schema and print a note.

2. **Filter out non-FP entries** (addresses DeepSeek LOW #9). Cases 9-11 are FN (false negatives), Case 12 is a code bug, Case 13 is a process failure. Only Cases 1-8, 14-15 are true FPs. The parser must check the Classification line: if it contains "FN", "false negative", "code bug", or "process failure", skip the case.

3. **Split mixed classifications** (addresses DeepSeek LOW #11). If a case Classification says "Mix of CONTEXT_MISSING (3) and HALLUCINATION (2)", generate 5 separate finding records: 3 with reject_reason=CONTEXT_MISSING and 2 with reject_reason=HALLUCINATION. Parse the pattern "Mix of X (N) and Y (M)" to extract categories and counts.

4. **Convert each valid case to D1 schema** using these rules:
   - `id`: uuid.uuid4()
   - `timestamp`: datetime.datetime.now(datetime.timezone.utc).isoformat()
   - `file`: extract from Context line if a file path is mentioned, otherwise "unknown"
   - `line`: -1 (sentinel -- historical data has no line numbers)
   - `dimension`: map from Context/Finding text
   - `pass`: extract from Source line if pass number mentioned, otherwise 0
   - `cycle`: extract from Source line if cycle mentioned, otherwise 0
   - `severity`: extract from Finding/Classification. Map "HIGH"/"must fix" to "P1", "MEDIUM" to "P2", "LOW"/"minor" to "P3". Default "P2"
   - `description`: combine Finding + Outcome lines
   - `outcome`: "rejected" (all historical cases are confirmed FPs)
   - `reject_reason`: from Classification. For "Mix of X and Y", use the first category for the first N records and second for the last M records
   - `commit_sha`: "historical"
   - `cost_tokens`: {"input": 0, "output": 0}

5. **Write output** using atomic write pattern (tempfile.mkstemp + os.replace):
   - Output file: `.forge/findings.json`
   - If the file already exists, MERGE new findings with existing ones (do not overwrite)
   - Schema: `{"version": 1, "findings": [...], "runs": []}`

6. **Report results** to stdout: "Bootstrapped N historical findings to .forge/findings.json"

**Script structure:**

```python
#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Convert historical FP analysis to findings.json schema (D1).

One-time bootstrap script for Phase 1a. Reads the structured analysis
file produced by the historical review data mining session and converts
each FP case to the findings.json schema defined in 01a-CONTEXT.md D1.

Filters out non-FP entries (FN, code bugs, process failures).
Splits mixed classifications into individual finding records.
"""

import json
import os
import re
import sys
import tempfile
import uuid
from datetime import datetime, timezone

FINDINGS_FILE = '.forge/findings.json'

# Persistent path first, /tmp fallback (addresses review issue #11)
HISTORICAL_PATHS = [
    '.planning/research/historical_review_analysis.txt',
    '/tmp/draft_20260512_historical_review_analysis.txt',
]

VALID_REJECT_REASONS = {
    'HALLUCINATION', 'CONTEXT_MISSING', 'INTENTIONAL',
    'NOT_APPLICABLE', 'STYLE_PREFERENCE', 'ACCEPTABLE_RISK',
}

# Patterns that indicate non-FP cases (skip these)
NON_FP_PATTERNS = [
    r'\bFN\b', r'false.negative', r'code.bug',
    r'process.failure', r'missed.finding',
]


def find_historical_file(explicit_path=None):
    """Find the historical analysis file.

    Try explicit path first, then persistent path, then /tmp fallback.
    """
    if explicit_path and os.path.isfile(explicit_path):
        return explicit_path
    for path in HISTORICAL_PATHS:
        if os.path.isfile(path):
            return path
    return None


def parse_historical_analysis(filepath):
    """Parse case blocks from the historical analysis file.

    Returns list of dicts with keys: source, context, finding,
    outcome, classification, evidence, notes.
    Filters out non-FP entries (FN, code bugs, process failures).
    """
    # Implementation: read file, split on "--- Case N:" delimiter,
    # extract structured fields from each block,
    # filter out cases where classification matches NON_FP_PATTERNS
    pass


def split_mixed_classification(classification_text):
    """Split 'Mix of X (N) and Y (M)' into individual (reason, count) pairs.

    Returns list of (reason, count) tuples.
    E.g., 'Mix of CONTEXT_MISSING (3) and HALLUCINATION (2)' returns
    [('CONTEXT_MISSING', 3), ('HALLUCINATION', 2)].

    If not a mixed classification, returns [(mapped_reason, 1)].
    """
    pass


def map_reject_reason(classification_text):
    """Map classification text to one of 6 valid reject reasons."""
    # Check for exact category names in the text
    # If "Mix of X and Y", return X (first mentioned) -- caller
    # should use split_mixed_classification for detailed breakdown
    pass


def map_dimension(context_text, finding_text):
    """Map context/finding text to a review dimension name."""
    pass


def map_severity(finding_text, classification_text):
    """Map finding/classification text to P0/P1/P2/P3."""
    pass


def convert_to_schema(cases):
    """Convert parsed cases to D1 findings schema.

    For cases with mixed classifications (e.g., "Mix of X (3) and Y (2)"),
    generate multiple finding records -- one per sub-classification count.
    """
    pass


def atomic_write(filepath, data):
    """Atomically write JSON data using tempfile + os.replace."""
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


def load_existing():
    """Load existing findings.json or return empty structure."""
    try:
        with open(FINDINGS_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {'version': 1, 'findings': [], 'runs': []}


def main():
    explicit_path = sys.argv[1] if len(sys.argv) >= 2 else None
    filepath = find_historical_file(explicit_path)

    if filepath is None:
        print("No historical analysis file found. Creating empty findings.json.",
              file=sys.stderr)
        existing = load_existing()
        if not existing['findings']:
            atomic_write(FINDINGS_FILE, existing)
            print(f"Created empty {FINDINGS_FILE}")
        else:
            print(f"{FINDINGS_FILE} already has {len(existing['findings'])} findings")
        return

    print(f"Reading from: {filepath}")
    cases = parse_historical_analysis(filepath)
    new_findings = convert_to_schema(cases)

    existing = load_existing()
    existing['findings'].extend(new_findings)
    atomic_write(FINDINGS_FILE, existing)

    print(f"Bootstrapped {len(new_findings)} historical findings to {FINDINGS_FILE}")


if __name__ == '__main__':
    main()
```

The executor MUST implement the `parse_historical_analysis`, `split_mixed_classification`, `map_reject_reason`, `map_dimension`, `map_severity`, and `convert_to_schema` functions by reading the actual structure of the historical analysis file.

Per CLAUDE.md: use `#!/usr/bin/env python3` shebang. All strings must be ASCII. Script must exit 0 on success, 1 on error.
  </action>
  <verify>
    <automated>python3 -m py_compile bootstrap/convert_historical.py && echo "syntax OK" && python3 -c "
import ast, sys
with open('bootstrap/convert_historical.py') as f:
    tree = ast.parse(f.read())
funcs = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
required = ['parse_historical_analysis', 'split_mixed_classification', 'map_reject_reason', 'convert_to_schema', 'atomic_write', 'load_existing', 'find_historical_file', 'main']
missing = [r for r in required if r not in funcs]
if missing:
    print(f'FAIL: missing functions: {missing}', file=sys.stderr)
    sys.exit(1)
print('functions OK')
" && grep -q "NON_FP_PATTERNS" bootstrap/convert_historical.py && echo "filter OK"</automated>
  </verify>
  <acceptance_criteria>
    - bootstrap/convert_historical.py exists and passes `python3 -m py_compile`
    - File contains `#!/usr/bin/env python3` shebang on line 1
    - File contains SPDX license header and copyright "Minxi Hou"
    - Contains function `find_historical_file` that tries persistent path before /tmp (addresses review issue #11)
    - Contains function `parse_historical_analysis` that filters out non-FP cases (addresses DeepSeek LOW #9)
    - Contains NON_FP_PATTERNS list with 'FN', 'false.negative', 'code.bug', 'process.failure'
    - Contains function `split_mixed_classification` that parses "Mix of X (N) and Y (M)" (addresses DeepSeek LOW #11)
    - Contains function `convert_to_schema` that generates multiple records for mixed classifications
    - Contains function `atomic_write` using tempfile.mkstemp + os.replace
    - Contains function `load_existing` that merges with existing findings
    - main() handles missing file gracefully (creates empty findings.json, does not crash)
    - All 6 reject reason constants appear in VALID_REJECT_REASONS
    - Uses only stdlib imports
    - No non-ASCII characters: `grep -P '[^\x00-\x7F]' bootstrap/convert_historical.py` returns nothing
  </acceptance_criteria>
  <done>bootstrap/convert_historical.py parses the historical FP analysis, filters non-FP entries, splits mixed classifications into individual records, and writes to .forge/findings.json. File lookup tries persistent .planning/research/ path before /tmp. Review issues #11, DeepSeek LOW #9, DeepSeek LOW #11 addressed.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| Historical analysis file -> findings.json | Untrusted text parsed and converted to JSON |
| User-editable config.json -> cost calculation | User can modify pricing, affecting cost estimates |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-01a-04 | T (Tampering) | bootstrap/convert_historical.py | accept | One-time script; input file is locally generated, not user-facing |
| T-01a-05 | T (Tampering) | cli/config.json | accept | User-editable by design (D8); incorrect pricing only affects cost estimates |
| T-01a-06 | I (Information Disclosure) | .gitignore | mitigate | .forge/ added to .gitignore to prevent accidental commit of review data |
</threat_model>

<verification>
1. `grep ".forge/" .gitignore` returns the gitignore entry
2. `python3 -c "import json; json.load(open('cli/config.json'))"` validates config JSON
3. `python3 -m py_compile bootstrap/convert_historical.py` validates Python syntax
4. If historical analysis file exists: `python3 bootstrap/convert_historical.py && python3 -c "import json; d=json.load(open('.forge/findings.json')); print(f'{len(d[\"findings\"])} findings loaded')"` should print findings count (filtered, may be less than 15 due to non-FP removal)
5. If no historical file: `python3 bootstrap/convert_historical.py` prints graceful message and creates empty findings.json
</verification>

<success_criteria>
The forge project has .forge/ gitignored, model pricing configured, historical data persisted from /tmp, and a working bootstrap script that can seed the findings database with filtered historical FP data. Non-FP entries are excluded and mixed classifications are split into individual records.
</success_criteria>

<output>
After completion, create `.planning/phases/01a-trust-instrumentation/01a-02-SUMMARY.md`
</output>
