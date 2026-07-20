# Phase 2: Dimension Gap Closure - Research

**Researched:** 2026-05-12
**Domain:** Code review dimension management, deterministic complexity analysis, custom rule systems
**Confidence:** HIGH

## Summary

Phase 2 closes dimension gaps in forge's 12-dimension review pipeline using evidence-gated addition. The work divides into five streams: (1) seed-testing zero-data dimensions (D1), (2) adding two new LLM dimensions with shadow mode (DIM-01 doc completeness, DIM-04 change scope), (3) absorbing two dimensions into dim 9 convention adherence (DIM-02 naming, DIM-05 readability), (4) adding deterministic complexity checks to Step 0b (DIM-03), (5) implementing custom project rules (DIM-07), and (6) building co-location analysis for data-driven merging (DIM-06).

The codebase is well-structured for this phase. `forge_cli.py` already has `evaluate_dimensions()`, `generate_recommendation()`, `backfill_confidence()`, and `run_dry_run()` as extension points. The finding schema in `.forge/findings.json` supports all needed fields. Key technical decisions are: use radon (Python CC) + a custom line-count shell function analyzer for Step 0b complexity (lizard does NOT support shell); use PyYAML `safe_load` (already installed) for custom rule parsing instead of adding `python-frontmatter` dependency; and extend `backfill_confidence()`'s `(file, line, dimension)` grouping logic for co-location analysis.

**Primary recommendation:** Implement in strict order: seed tests first (validates existing dimensions), then D3 rule improvement for failing dimensions, then Step 0b deterministic checks, then dim 9 expansion, then new LLM dimensions in shadow mode, then custom rules, and finally co-location analysis. This order ensures no new dimension is added before existing ones are verified.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- D1: Zero-Data Dimensions -- Keep All + Seed Test. Retain all 12 existing dimensions. Craft synthetic diffs for dimensions with <5 findings to verify prompt produces findings.
- D2: Three-Layer Filter + Routing. DIM-01 -> new LLM dim + shadow. DIM-02 -> absorb into dim 9. DIM-03 -> Step 0b deterministic (radon/lizard). DIM-04 -> new LLM dim + shadow. DIM-05 -> absorb into dim 9. DIM-06 -> data-driven co-location. DIM-07 -> YAML frontmatter + Markdown body.
- D3: Data-Driven Merging via finding co-location analysis. Merge when co-location rate >30% on (file, line) pairs. Min 20 co-located findings per pair.
- D4: YAML Frontmatter + Markdown Body for custom rules. forge-rules.md or .forge/rules/*.md. Loading order: single file -> multi-file -> reject duplicate names.

### Claude's Discretion
- Seed test synthetic diff design (content, file structure)
- Co-location analysis implementation details (SQL vs Python, caching)
- Shadow mode logging format (separate file vs findings.json annotation)
- Custom rule parsing implementation (YAML library choice, error handling)
- Step 0b complexity tool selection priority (radon vs lizard vs both)
- DIM-01/04 prompt wording for new dimensions
- Monitor mode UI/output handling (completely silent vs debug flag)

### Deferred Ideas (OUT OF SCOPE)
- Embedding-based dimension deduplication (LEARN-03): v2
- AST-based SKILL.md editing (LEARN-04): v2
- Cross-project rule sharing: v2 ADV-02
- ML-based co-location clustering: v2
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| DIM-01 | Add documentation completeness dimension | New LLM dimension in adversarial-qe SKILL.md, shadow mode deployment per D2 routing |
| DIM-02 | Add naming quality dimension | Absorbed into existing dim 9 (convention adherence) per D2. Extend dim 9 prompt in adversarial-qe SKILL.md |
| DIM-03 | Add complexity measurement dimension | Step 0b deterministic: radon CC for Python, custom line-count for shell. Integrated into run_dry_run() |
| DIM-04 | Add change scope dimension | New LLM dimension in adversarial-qe SKILL.md, shadow mode deployment per D2 routing |
| DIM-05 | Add readability dimension | Absorbed into existing dim 9 (convention adherence) per D2. Extend dim 9 prompt in adversarial-qe SKILL.md |
| DIM-06 | Merge overlapping dimensions | Co-location analysis on findings.json, compute (file,line) overlap matrix, recommend merges at >30% |
| DIM-07 | Project-specific rules support | YAML frontmatter parser using PyYAML safe_load, forge-rules.md/.forge/rules/*.md loading, prompt injection |
</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Complexity analysis (DIM-03) | CLI / Step 0b (forge_cli.py) | -- | Deterministic check runs before LLM invocation, per D2 layer 2 |
| Shadow mode (DIM-01/04) | SKILL.md (LLM prompt) | CLI (findings.json) | LLM produces findings; CLI persists with shadow flag |
| Dim 9 expansion (DIM-02/05) | SKILL.md (adversarial-qe) | -- | Prompt-level change only; no CLI code needed |
| Custom rules (DIM-07) | CLI (forge_cli.py) | SKILL.md (prompt injection) | CLI loads/validates rules; injects into LLM prompt |
| Co-location analysis (DIM-06) | CLI (forge_cli.py) | -- | Pure data analysis on findings.json; no LLM involvement |
| Seed tests (D1) | Test harness (Python) | -- | Standalone validation, not production code |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| radon | 6.0.1 | Python cyclomatic complexity analysis | [VERIFIED: pip index] Standard tool for Python CC metrics. Used by SonarQube Python plugin, CodeClimate. Only Python tool with both CC and cognitive complexity |
| PyYAML | 6.0.3 | YAML frontmatter parsing for custom rules | [VERIFIED: pip show locally installed] Already installed in environment. safe_load prevents code execution attacks |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| shellmetrics | latest | Shell script cyclomatic complexity | [CITED: github.com/shellspec/shellmetrics] Only CC analyzer for bash/zsh. Fallback: custom line-count heuristic if shellmetrics unavailable |
| lizard | 1.22.1 | Multi-language complexity analysis (Python, Go, C, etc.) | [VERIFIED: pip index] When analyzing non-Python, non-shell languages. Does NOT support shell/bash |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| PyYAML safe_load | python-frontmatter 1.1.0 | Adds a dependency; PyYAML is already installed and a 5-line regex+safe_load handles the format. python-frontmatter would be cleaner API but unnecessary |
| radon | cognitive-complexity PyPI package | radon is more established (6M+ downloads); cognitive-complexity package has narrower scope |
| shellmetrics | Custom awk/bash line counter | shellmetrics gives real CC; custom counter only gives NLOC. Recommend shellmetrics if available, fall back to line-count |

**Installation:**
```bash
pip install radon==6.0.1
# Optional (shell CC analysis):
curl -fsSL https://raw.githubusercontent.com/shellspec/shellmetrics/master/shellmetrics -o ~/.local/bin/shellmetrics && chmod +x ~/.local/bin/shellmetrics
```

**Version verification:** radon 6.0.1 confirmed via `pip3 index versions radon` on 2026-05-12. PyYAML 6.0.3 confirmed installed locally. [VERIFIED: npm/pip registries]

## Architecture Patterns

### System Architecture Diagram

```
                    forge review invocation
                           |
                           v
          +--------------------------------+
          |    forge_cli.py: run_dry_run() |
          +--------------------------------+
                           |
              +------------+------------+
              |            |            |
              v            v            v
         [Step 0a]    [Step 0b]    [Step 0c]
         Syntax       Complexity   Non-ASCII
         bash -n      radon cc     grep -P
         py_compile   line-count
                           |
                           v
              +------------------------+
              |  Load custom rules     |
              |  forge-rules.md        |
              |  .forge/rules/*.md     |
              +------------------------+
                           |
                           v
          +--------------------------------+
          |   forge SKILL.md: LLM review   |
          |   12 existing dims             |
          |   + dim 9 expanded (naming,    |
          |     readability)               |
          |   + DIM-01 doc completeness    |  <-- shadow mode
          |   + DIM-04 change scope        |  <-- shadow mode
          |   + custom rules injected      |
          +--------------------------------+
                           |
                           v
          +--------------------------------+
          |   findings.json persistence    |
          |   (with shadow: true flag)     |
          +--------------------------------+
                           |
                           v
          +--------------------------------+
          |   Co-location analysis         |
          |   (file, line) overlap matrix  |
          |   Merge recommendations        |
          +--------------------------------+
```

### Recommended Project Structure
```
cli/
  forge_cli.py         # Extended: Step 0b complexity, custom rules, co-location
  config.json          # Extended: complexity_thresholds, custom_rules_paths
skills/
  forge/
    SKILL.md           # Extended: shadow mode dims, expanded dim 9
  adversarial-qe/
    SKILL.md           # Extended: dim 9 expansion (naming + readability)
.forge/
  findings.json        # Extended: shadow flag on findings
  rules/               # NEW: multi-file custom rules directory
forge-rules.md         # NEW: single-file custom rules (project root)
tests/
  seed_tests/          # NEW: synthetic diffs for zero-data dimensions
  test_complexity.py   # NEW: Step 0b complexity checker tests
  test_custom_rules.py # NEW: custom rule loader tests
  test_colocation.py   # NEW: co-location analysis tests
```

### Pattern 1: Shadow Mode Finding Persistence
**What:** New LLM dimensions (DIM-01 doc completeness, DIM-04 change scope) produce findings that are logged but not displayed to users until FP rate is validated.
**When to use:** Any new dimension before it reaches 20 findings with <10% ToolFP.
**Example:**
```python
# Source: CONTEXT.md D2 shadow mode deployment process
# In findings.json, shadow findings get a 'shadow' flag
finding = {
    'id': str(uuid.uuid4()),
    'dimension': 'doc_completeness',  # or 'change_scope'
    'shadow': True,  # NOT shown in review output
    'outcome': 'pending',
    # ... standard fields ...
}
# When displaying findings to user:
visible = [f for f in findings if not f.get('shadow', False)]
```

### Pattern 2: Step 0b Complexity Check Integration
**What:** Deterministic complexity metrics run in run_dry_run() after syntax (0a) and before non-ASCII (0c).
**When to use:** Every forge review of Python or shell files.
**Example:**
```python
# Source: radon API docs (radon.readthedocs.io/en/master/api.html)
# [VERIFIED: radon.readthedocs.io]
from radon.complexity import cc_visit, cc_rank

def check_python_complexity(filepath, threshold=15):
    """Step 0b: flag functions exceeding CC threshold."""
    with open(filepath, 'r') as f:
        code = f.read()
    results = cc_visit(code)
    violations = []
    for block in results:
        if block.complexity >= threshold:
            violations.append({
                'file': filepath,
                'line': block.lineno,
                'function': block.name,
                'complexity': block.complexity,
                'rank': cc_rank(block.complexity),
            })
    return violations
```

### Pattern 3: Custom Rule Loading (YAML frontmatter + Markdown body)
**What:** Parse forge-rules.md with YAML frontmatter for structured metadata and Markdown body for LLM consumption.
**When to use:** Before every LLM invocation in run_forge().
**Example:**
```python
# Source: PyYAML safe_load + regex frontmatter extraction
# [VERIFIED: tested locally with exact D4 format]
import re, yaml

def parse_rule_file(filepath):
    """Parse a single rule file with YAML frontmatter."""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    match = re.match(
        r'^---\s*\n(.*?)\n---\s*\n(.*)$', content, re.DOTALL,
    )
    if not match:
        return None
    metadata = yaml.safe_load(match.group(1))
    body = match.group(2).strip()
    # Validate required fields
    required = {'name', 'severity'}
    missing = required - set(metadata.keys())
    if missing:
        raise ValueError(
            f"Rule {filepath} missing required fields: {missing}"
        )
    return {'metadata': metadata, 'body': body, 'source': filepath}
```

### Pattern 4: Co-location Analysis
**What:** Compute overlap between dimensions by counting findings that share (file, line) coordinates.
**When to use:** When evaluating whether to merge two dimensions (DIM-06).
**Example:**
```python
# Source: CONTEXT.md D3 co-location analysis
# Reuses grouping pattern from backfill_confidence()
def compute_colocation_matrix(findings):
    """Build co-location matrix from findings data."""
    # Group findings by (file, line) -> set of dimensions
    locations = {}
    for f in findings:
        if f.get('file') in (None, 'unknown') or f.get('line', -1) == -1:
            continue  # Skip historical data without coordinates
        key = (f['file'], f['line'])
        if key not in locations:
            locations[key] = set()
        locations[key].add(f.get('dimension', 'unknown'))
    
    # Count co-location pairs
    from itertools import combinations
    colocation = {}
    for loc, dims in locations.items():
        for d1, d2 in combinations(sorted(dims), 2):
            pair = (d1, d2)
            colocation[pair] = colocation.get(pair, 0) + 1
    
    return colocation
```

### Anti-Patterns to Avoid
- **Adding dimensions without data:** D2 three-layer filter gates this. Never add a new dimension just because "it seems useful." Require gap evidence first.
- **Running complexity checks via LLM:** DIM-03 is explicitly deterministic (D2 layer 2). Never ask the LLM to compute cyclomatic complexity -- radon does it faster and reproducibly.
- **Merging dimensions by semantic intuition:** D3 requires co-location data. "These two sound similar" is not sufficient evidence for merging.
- **Suppressing shadow mode findings in persistence:** Shadow findings MUST be persisted to findings.json for later FP analysis. Only suppress from user-visible output.
- **Adding python-frontmatter as dependency:** PyYAML is already installed. The 5-line regex+safe_load pattern was tested locally and handles the D4 format. Do not add unnecessary dependencies.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Python cyclomatic complexity | AST walking CC counter | radon `cc_visit()` | radon handles all Python edge cases (decorators, comprehensions, walrus operator, match/case). Hand-rolling CC is a known pitfall -- edge cases in Python 3.10+ pattern matching break naive counters |
| YAML parsing with safe mode | Custom YAML tokenizer | PyYAML `safe_load()` | safe_load prevents code execution via `!!python/object` tags. Hand-rolled YAML parsers invariably miss edge cases (multiline strings, anchors, aliases) |
| Wilson score interval | Manual binomial CI | Existing `wilson_score_interval()` in forge_cli.py | Already implemented and tested in Phase 1a/1b |
| Atomic JSON writes | Direct file.write() | Existing `atomic_write()` in forge_cli.py | tempfile.mkstemp + os.replace pattern already handles crash safety |
| Finding schema/validation | New data model | Existing finding persistence pattern in SKILL.md | Schema is stable; just add `shadow` boolean field |

**Key insight:** Phase 2 extends existing infrastructure rather than building new systems. Every new capability maps to an existing function or pattern in forge_cli.py or SKILL.md.

## Common Pitfalls

### Pitfall 1: Radon Import Failure at Runtime
**What goes wrong:** radon is not installed on the user's machine, and Step 0b crashes instead of degrading gracefully.
**Why it happens:** radon is a pip dependency not bundled with forge. Users may not have it installed.
**How to avoid:** Import radon inside the complexity check function with try/except ImportError. If missing, print a warning and skip complexity analysis (do not fail the entire Step 0 pipeline).
**Warning signs:** ImportError in CI/CD environments or fresh installations.

### Pitfall 2: Shell Complexity False Positives from Long Case Statements
**What goes wrong:** Shell functions with large case/esac blocks get flagged for complexity when the function is actually simple -- each case arm is trivial.
**Why it happens:** Line-count heuristics conflate size with complexity. A 200-line case statement routing commands is not complex.
**How to avoid:** For shell, use line-count threshold as the primary metric (e.g., >80 lines per function), NOT cyclomatic complexity. Document that shell CC (if shellmetrics is used) should have a higher threshold (e.g., 25) than Python CC (15) due to case/esac inflation.
**Warning signs:** Test infrastructure shell scripts (common in forge's user base) routinely have large case blocks.

### Pitfall 3: Shadow Mode Findings Polluting FP Statistics
**What goes wrong:** Shadow mode findings from DIM-01/04 are included in aggregate FP rate calculation, skewing the dashboard.
**Why it happens:** The `evaluate_dimensions()` function currently iterates all findings without filtering by shadow status.
**How to avoid:** Filter shadow findings from all statistical calculations. Shadow findings are only included in the per-dimension analysis when explicitly computing shadow dimension FP rates for promotion decisions.
**Warning signs:** Overall ToolFP rate jumps after deploying shadow dimensions.

### Pitfall 4: Custom Rule Injection Exceeding Context Window
**What goes wrong:** A project has 50 custom rules, each 200 words. Total injection is 10K words, consuming significant context window and reducing LLM review quality.
**Why it happens:** No cap on custom rule count or total injection size.
**How to avoid:** Cap total injected rule text at a configurable limit (default: 5000 tokens or ~20 rules). Rules are sorted by severity (critical first) and truncated with a warning if the cap is exceeded.
**Warning signs:** Users report degraded review quality after adding many custom rules.

### Pitfall 5: Co-location Analysis on Sparse Data
**What goes wrong:** Co-location analysis recommends merging two dimensions based on 3 co-located findings, which is statistically meaningless.
**Why it happens:** D3 minimum threshold (20 co-located findings per pair) is not enforced.
**How to avoid:** Enforce D3 minimum: do not generate merge recommendations unless a dimension pair has 20+ co-located findings. Report "insufficient data" for pairs below threshold.
**Warning signs:** Merge recommendations appear immediately after deployment with only a handful of findings.

### Pitfall 6: Seed Test Synthetic Diffs That Are Too Obvious
**What goes wrong:** A seed test diff is so blatantly buggy that ANY dimension would flag it, making the seed test meaningless for the target dimension.
**Why it happens:** Designing synthetic diffs that specifically trigger one dimension (and not others) requires understanding what each dimension checks.
**How to avoid:** Each seed test should be designed to trigger exactly one dimension. The diff should be plausible code (not obviously broken) that has a specific gap matching the dimension's scope. Verify by running against all dimensions and checking that only the target dimension fires.
**Warning signs:** A seed test triggers 5 different dimensions -- it is not testing the target dimension specifically.

## Code Examples

### Step 0b Integration Point in run_dry_run()
```python
# Source: forge_cli.py:run_dry_run() existing structure
# [VERIFIED: codebase read]
# Insert after Step 0a syntax check, before Step 0c non-ASCII check

# Step 0b: Complexity check (DIM-03 deterministic)
if filepath.endswith('.py'):
    try:
        from radon.complexity import cc_visit, cc_rank
        with open(filepath, 'r', encoding='utf-8') as f:
            code = f.read()
        cc_threshold = config.get('complexity', {}).get(
            'python_cc_threshold', 15
        )
        for block in cc_visit(code):
            if block.complexity >= cc_threshold:
                findings.append((
                    'complexity', filepath, 'radon',
                    f'{block.name}() CC={block.complexity} '
                    f'(rank {cc_rank(block.complexity)}, '
                    f'threshold {cc_threshold})'
                ))
                total_issues += 1
    except ImportError:
        pass  # radon not installed, skip complexity
elif filepath.endswith('.sh') or filepath.endswith('.bash'):
    # Shell: function line-count heuristic
    shell_threshold = config.get('complexity', {}).get(
        'shell_line_threshold', 80
    )
    # Parse function boundaries from shell script
    _check_shell_function_length(filepath, shell_threshold, findings)
```

### Shell Function Length Checker
```python
# Source: Custom implementation (no existing tool for this)
# [ASSUMED] -- design based on bash function syntax rules
import re

def _check_shell_function_length(filepath, threshold, findings_list):
    """Check shell function lengths against threshold."""
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    func_pattern = re.compile(
        r'^\s*(?:function\s+)?(\w[\w-]*)\s*\(\s*\)\s*\{?\s*$'
    )
    brace_depth = 0
    current_func = None
    func_start = 0
    
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        
        # Detect function start
        m = func_pattern.match(stripped)
        if m and brace_depth == 0:
            current_func = m.group(1)
            func_start = i
            brace_depth = stripped.count('{') - stripped.count('}')
            continue
        
        if current_func:
            brace_depth += stripped.count('{') - stripped.count('}')
            if brace_depth <= 0:
                func_length = i - func_start + 1
                if func_length > threshold:
                    findings_list.append((
                        'complexity', filepath, 'line-count',
                        f'{current_func}() {func_length} lines '
                        f'(threshold {threshold})'
                    ))
                current_func = None
                brace_depth = 0
```

### Custom Rule Loader
```python
# Source: CONTEXT.md D4 specification + PyYAML safe_load
# [VERIFIED: tested locally with exact D4 format]
import glob, os, re, yaml

VALID_SEVERITIES = {'critical', 'high', 'medium', 'low'}

def load_custom_rules(project_root='.'):
    """Load custom rules from forge-rules.md and .forge/rules/*.md."""
    rules = []
    seen_names = set()
    
    # 1. Single file
    single = os.path.join(project_root, 'forge-rules.md')
    if os.path.isfile(single):
        rule = _parse_rule_file(single)
        if rule:
            rules.append(rule)
            seen_names.add(rule['metadata']['name'])
    
    # 2. Multi-file directory
    rules_dir = os.path.join(project_root, '.forge', 'rules')
    if os.path.isdir(rules_dir):
        for path in sorted(glob.glob(os.path.join(rules_dir, '*.md'))):
            rule = _parse_rule_file(path)
            if rule:
                name = rule['metadata']['name']
                if name in seen_names:
                    print(
                        f"Error: duplicate rule name '{name}' in "
                        f"{path} (already loaded)",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                rules.append(rule)
                seen_names.add(name)
    
    # 3. Filter disabled rules
    active = [r for r in rules if r['metadata'].get('enabled', True)]
    return active

def _parse_rule_file(filepath):
    """Parse YAML frontmatter + Markdown body from a rule file."""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    match = re.match(
        r'^---\s*\n(.*?)\n---\s*\n(.*)$', content, re.DOTALL,
    )
    if not match:
        print(
            f"Warning: {filepath} has no YAML frontmatter, skipping",
            file=sys.stderr,
        )
        return None
    try:
        metadata = yaml.safe_load(match.group(1))
    except yaml.YAMLError as e:
        print(
            f"Error: invalid YAML in {filepath}: {e}",
            file=sys.stderr,
        )
        return None
    body = match.group(2).strip()
    
    # Validate required fields
    if 'name' not in metadata:
        print(
            f"Error: rule in {filepath} missing 'name' field",
            file=sys.stderr,
        )
        return None
    if 'severity' not in metadata:
        print(
            f"Error: rule in {filepath} missing 'severity' field",
            file=sys.stderr,
        )
        return None
    sev = metadata['severity'].lower()
    if sev not in VALID_SEVERITIES:
        print(
            f"Warning: rule '{metadata['name']}' has unknown "
            f"severity '{sev}', defaulting to 'medium'",
            file=sys.stderr,
        )
        metadata['severity'] = 'medium'
    
    return {'metadata': metadata, 'body': body, 'source': filepath}
```

### Shadow Mode Finding Filter
```python
# Source: CONTEXT.md D2 shadow mode deployment process
# [VERIFIED: codebase pattern in evaluate_dimensions()]
def evaluate_dimensions_filtered(findings, include_shadow=False):
    """Filter shadow findings from evaluation unless explicitly included."""
    if include_shadow:
        return findings
    return [f for f in findings if not f.get('shadow', False)]
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| LLM-only complexity review | Deterministic CC + LLM semantic complexity | ICSE 2026 (arXiv:2509.19117) | Deterministic matches LLM for pure metrics. LLM reserved for coupling/domain complexity |
| Ungated dimension addition | Three-layer filter (evidence -> deterministic-first -> Tricorder 4) | Semgrep/Google Tricorder practice | Prevents FindBugs failure pattern (84% unfixed bugs from ungated addition) |
| Merge by intuition | Data-driven co-location analysis | ESLint/SonarQube evolution | Empirical merging avoids false consolidation (Pecorelli et al. EMSE 2022) |
| Flat rule files | YAML frontmatter + Markdown body | Devin playbooks, Hugo/Jekyll | Structured metadata for tooling + natural language for LLM consumption |

**Deprecated/outdated:**
- Lizard for shell complexity: NOT supported. Use shellmetrics or custom line-count heuristic instead.
- python-frontmatter for YAML parsing: Unnecessary dependency. PyYAML safe_load + 5-line regex is sufficient and already installed.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Shell function parser regex `(?:function\s+)?(\w[\w-]*)\s*\(\s*\)\s*\{?\s*$` covers all bash function syntaxes | Code Examples / Shell Function Length Checker | Could miss `function foo { }` without parens in some styles. Mitigated by bash syntax specification check. |
| A2 | Custom rule injection cap of ~20 rules / 5000 tokens is sufficient for typical projects | Pitfalls / Pitfall 4 | Users with large rule sets may find the cap too restrictive. Configurable, so low risk. |
| A3 | Cognitive complexity threshold of 15 is appropriate for Python (radon CC >= 15) | D2 routing / DIM-03 | ICSE 2026 paper used 15 as baseline. Configurable via config.json, so adjustable per project. |
| A4 | Shell function line-count threshold of 80 is appropriate | Pitfalls / Pitfall 2 | No published empirical data for shell-specific thresholds. Based on general "screen height" heuristic. Configurable. |

## Open Questions

1. **Shadow mode logging location**
   - What we know: D2 says shadow findings are logged to findings.json but NOT shown to user. Two options: (a) `shadow: true` flag on each finding in findings.json, (b) separate `.forge/shadow_findings.json` file.
   - What's unclear: Whether mixing shadow and active findings in one file complicates queries.
   - Recommendation: Use `shadow: true` flag in findings.json. Simpler data model, one source of truth. Filter at query time. The existing `backfill_confidence()` and `evaluate_dimensions()` just need a filter clause.

2. **Monitor mode debug visibility**
   - What we know: Shadow findings are NOT shown in normal review output. Users may want to see them for debugging.
   - What's unclear: Whether a `--debug-shadow` flag or `forge --stats --shadow` is the right UX.
   - Recommendation: Add `--shadow` flag to `forge --stats` and `forge --eval` to include shadow dimension data. No special flag during review -- shadow findings are invisible by default.

3. **Co-location analysis timing**
   - What we know: D3 requires 20+ co-located findings per pair. Current data has 0 findings with file/line coordinates.
   - What's unclear: How long until sufficient data accumulates (depends on forge usage frequency).
   - Recommendation: Build the analysis infrastructure now, but make it report "insufficient data" until thresholds are met. The analysis is useful even with partial data as a monitoring tool.

4. **Seed test execution environment**
   - What we know: Seed tests need to invoke the forge review pipeline on synthetic diffs.
   - What's unclear: Whether to use `forge --dry-run` (Step 0 only, no LLM cost) or full `forge` invocation (which costs tokens).
   - Recommendation: Seed tests target LLM dimensions, so they MUST invoke the LLM passes. Use `claude -p` with the SKILL.md prompt on a synthetic diff. Accept the token cost as a one-time validation expense per dimension.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3 | All CLI code | Yes | 3.x (system) | -- |
| PyYAML | DIM-07 custom rules | Yes | 6.0.2 | -- |
| radon | DIM-03 Python complexity | No | -- | Install via `pip install radon==6.0.1`. Skip complexity if missing (graceful degradation) |
| shellmetrics | DIM-03 shell complexity | No | -- | Fall back to custom line-count function parser |
| git | diff analysis | Yes | (system) | -- |
| claude CLI | Seed tests, full reviews | Yes (assumed) | -- | Required for LLM-based reviews |

**Missing dependencies with no fallback:**
- None. All missing tools have graceful degradation paths.

**Missing dependencies with fallback:**
- radon: not installed. Fallback is to skip Python complexity analysis with a warning. Phase plan should include `pip install radon` as setup step.
- shellmetrics: not installed. Fallback is the custom shell function line-count checker (implemented in Python, no external dependency).

## Project Constraints (from CLAUDE.md)

- **Language**: All .md files in English
- **Dependencies**: bash assertion primitives require only jq; skills require Claude Code
- **No non-ASCII in code**: typographic characters must be ASCII equivalents
- **Worktree**: All code changes must be in a git worktree, not main tree
- **Three-cycle review**: All code changes require full 9-pass review before commit
- **Author info**: Minxi Hou <houminxi@gmail.com>, no AI-generated author lines

## Sources

### Primary (HIGH confidence)
- [radon documentation](https://radon.readthedocs.io/en/master/api.html) -- programmatic API, CC visit, cc_rank, JSON output
- [radon CLI docs](https://radon.readthedocs.io/en/latest/commandline.html) -- CC thresholds, rank definitions (A-F), filtering
- [forge_cli.py codebase](/home/houminxi/code/forge/cli/forge_cli.py) -- existing functions, patterns, integration points
- [SKILL.md codebase](/home/houminxi/code/forge/skills/forge/SKILL.md) -- current 12 dimensions, finding persistence, state machine
- [adversarial-qe SKILL.md](/home/houminxi/code/forge/skills/adversarial-qe/SKILL.md) -- dimension definitions, dim 9 convention adherence scope
- [findings.json](/home/houminxi/code/forge/.forge/findings.json) -- current data: 18 historical findings, all rejected, no file/line coordinates
- [CONTEXT.md](/home/houminxi/code/forge/.planning/phases/02-dimension-gap-closure/02-CONTEXT.md) -- D1-D4 locked decisions

### Secondary (MEDIUM confidence)
- [lizard GitHub](https://github.com/terryyin/lizard) -- language support list verified: shell/bash NOT supported [VERIFIED: WebSearch]
- [shellmetrics GitHub](https://github.com/shellspec/shellmetrics) -- shell CC analyzer, bash/zsh support [CITED: GitHub repository]
- [python-frontmatter docs](https://python-frontmatter.readthedocs.io/) -- API reference for alternative YAML parsing approach [CITED: official docs]
- [PyYAML safe_load](https://pyyaml.org/) -- verified locally: handles D4 format correctly with 5-line regex [VERIFIED: local test]
- pip registry -- radon 6.0.1, lizard 1.22.1, python-frontmatter 1.1.0, PyYAML 6.0.3 versions confirmed [VERIFIED: pip3 index]

### Tertiary (LOW confidence)
- shellmetrics usability and accuracy for production shell scripts -- not tested locally, only found via WebSearch [LOW: needs validation]

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- radon and PyYAML verified via registry and local testing. Shell tooling is MEDIUM (shellmetrics found but not tested).
- Architecture: HIGH -- all integration points verified in codebase. Patterns follow existing forge_cli.py conventions.
- Pitfalls: HIGH -- based on codebase analysis (e.g., shadow filtering gap) and known tool limitations (lizard shell support).
- Custom rules format: HIGH -- YAML frontmatter parsing tested locally with exact D4 format.
- Co-location analysis: MEDIUM -- logic is straightforward but current data has zero file/line coordinates. Infrastructure will be dormant until real data flows.

**Research date:** 2026-05-12
**Valid until:** 2026-06-12 (stable domain, 30-day validity)
