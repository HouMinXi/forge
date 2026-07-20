---
phase: 01b-trust-calibration
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - cli/forge_cli.py
  - cli/config.json
  - skills/forge/SKILL.md
autonomous: true
requirements:
  - TRUST-02
must_haves:
  truths:
    - "Every new finding persisted to findings.json includes confidence and confidence_signals fields"
    - "Wilson score confidence intervals are computed for per-dimension FP rates with honest uncertainty bounds"
    - "Confidence score progressively uses more signals as data volume grows (3 stages)"
    - "Config.json contains evaluation thresholds and tier classification defaults"
    - "Existing findings without confidence fields load without error (backward compatible)"
  artifacts:
    - path: "cli/forge_cli.py"
      provides: "wilson_score_interval(), compute_confidence(), two new section headers"
      contains: "def wilson_score_interval"
    - path: "cli/config.json"
      provides: "tier_classification and evaluation config sections"
      contains: "tier_classification"
    - path: "skills/forge/SKILL.md"
      provides: "Extended finding schema with confidence_signals"
      contains: "confidence_signals"
  key_links:
    - from: "skills/forge/SKILL.md"
      to: "cli/forge_cli.py"
      via: "SKILL.md records raw signals; CLI computes confidence post-run"
      pattern: "compute_confidence"
    - from: "cli/forge_cli.py"
      to: "cli/config.json"
      via: "load_config reads evaluation thresholds"
      pattern: "evaluation.*min_observations"
---

<objective>
Add confidence scoring infrastructure (D1/TRUST-02) and config schema extensions for Phase 1b.

Purpose: Every finding needs a confidence score (0-1) based on evidence strength. This plan adds the statistical utilities (Wilson score CI), the progressive confidence formula (3-stage based on data volume), the finding schema extension in SKILL.md, and the config.json sections for evaluation thresholds and tier classification defaults. These are the computational foundations that Plan 02 (tier classification) and Plan 03 (evaluation/recommendation) depend on.

Output: Extended forge_cli.py with statistical functions, extended SKILL.md heredoc with confidence_signals fields, extended config.json with tier_classification and evaluation sections.
</objective>

<execution_context>
@/home/houminxi/.claude/get-shit-done/workflows/execute-plan.md
@/home/houminxi/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/phases/01b-trust-calibration/01b-CONTEXT.md
@.planning/phases/01b-trust-calibration/01b-RESEARCH.md
@.planning/phases/01b-trust-calibration/01b-PATTERNS.md

<interfaces>
<!-- Key types and contracts the executor needs. Extracted from codebase. -->

From cli/forge_cli.py (current constants, lines 43-65):
```python
TOOL_ERROR_REASONS = {
    'HALLUCINATION', 'CONTEXT_MISSING', 'INTENTIONAL', 'NOT_APPLICABLE',
}
USER_PREF_REASONS = {'STYLE_PREFERENCE', 'ACCEPTABLE_RISK'}
VALID_REJECT_REASONS = TOOL_ERROR_REASONS | USER_PREF_REASONS
VALID_OUTCOMES = {'accepted', 'rejected', 'pending'}
```

From cli/forge_cli.py (load_config, line 72):
```python
def load_config():
    """Load CLI configuration from config.json."""
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, IOError):
        return {}
```

From cli/forge_cli.py (load_findings, line 96):
```python
def load_findings():
    """Load .forge/findings.json."""
    try:
        with open(FINDINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, IOError):
        return {'version': 1, 'findings': [], 'runs': []}
```

From cli/forge_cli.py (atomic_write, line 126):
```python
def atomic_write(filepath, data):
    """Atomically write JSON data to filepath."""
```

From cli/forge_cli.py (calculate_cost, line 147):
```python
def calculate_cost(usage, config):
    """Calculate cost in USD from token usage and pricing config."""
```

From skills/forge/SKILL.md (finding append, lines 362-376):
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

From cli/config.json (full content, 17 lines):
```json
{
  "pricing": {
    "claude-opus-4-6": { ... },
    "claude-sonnet-4-6": { ... }
  },
  "default_model": "claude-sonnet-4-6"
}
```
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Add statistical utilities and confidence scoring to forge_cli.py</name>
  <files>cli/forge_cli.py</files>
  <read_first>
    - cli/forge_cli.py (current state -- 922 lines, section headers, import block, constant definitions)
    - .planning/phases/01b-trust-calibration/01b-RESEARCH.md (Pattern 2: Wilson score, Pattern 3: progressive confidence)
    - .planning/phases/01b-trust-calibration/01b-PATTERNS.md (pure function pattern, section header pattern, imports pattern)
    - .planning/phases/01b-trust-calibration/01b-CONTEXT.md (D1 decision: schema fields, progressive stages, low-confidence handling)
  </read_first>
  <action>
Add three new imports to the import block (after line 41, before the Constants section):
```python
import math
import random
import re
```

Add a new constant after VALID_OUTCOMES (after line 65):
```python
# Minimum observations before acting on FP rate (D3/D4)
MIN_OBSERVATIONS = 20
```

Add two new section headers and functions AFTER the "Utility functions" section (after `atomic_write` ends around line 144) and BEFORE `calculate_cost` (line 147). Insert these in a new section:

Section header:
```python
# ---------------------------------------------------------------------------
# Statistical Utilities (Wilson score, data aggregation)
# ---------------------------------------------------------------------------
```

Function 1 -- `wilson_score_interval(successes, total, confidence=0.95)`:
- Pure function, no side effects
- Docstring: "Compute Wilson score confidence interval for a proportion."
- Args: successes (int, number of FP findings), total (int, total decided), confidence (float, default 0.95)
- Returns: tuple (lower, upper) bounds of the FP rate estimate
- If total == 0, return (0.0, 1.0)
- Compute p = successes / total
- z = 1.96 for confidence==0.95, else look up from table {0.90: 1.645, 0.95: 1.96, 0.99: 2.576}, fallback to 1.96
- denominator = 1 + z*z / total
- centre = (p + z*z / (2*total)) / denominator
- margin = z * math.sqrt((p*(1-p) + z*z/(4*total)) / total) / denominator
- Return (max(0.0, centre - margin), min(1.0, centre + margin))

Section header:
```python
# ---------------------------------------------------------------------------
# Confidence Scoring (D1 -- progressive multi-signal formula)
# ---------------------------------------------------------------------------
```

Function 2 -- `compute_confidence(dimension_fp_rate, pass_agreement=1.0, evidence_count=1, llm_self_report=0.8, total_findings=0)`:
- Pure function, no side effects
- Docstring: "Compute confidence score for a finding. Progressive: uses more signals as data volume grows."
- **Stage determination uses total_findings which is the per-dimension decided count, NOT global.** (Addresses review M5: per-dimension count ensures a dimension with 50 findings uses Stage 1, even if global count is 500.)
- Stage 1 (total_findings < 100): return max(0.0, min(1.0, 1.0 - dimension_fp_rate))
- Stage 2 (total_findings < 300): w_fp=0.6, w_agree=0.4; return max(0.0, min(1.0, w_fp*(1.0-dimension_fp_rate) + w_agree*pass_agreement))
- Stage 3 (total_findings >= 300): w_fp=0.35, w_agree=0.25, w_evidence=0.20, w_llm=0.20; evidence_score=min(1.0, evidence_count/5.0); return max(0.0, min(1.0, w_fp*(1.0-dimension_fp_rate) + w_agree*pass_agreement + w_evidence*evidence_score + w_llm*llm_self_report))

Function 3 -- `backfill_confidence(findings_data)`:
- Takes the full findings_data dict (as returned by load_findings())
- Iterates all findings, computes per-dimension FP rates from decided findings
- **Step 1: Compute per-dimension decided counts and FP rates:**
  ```python
  dim_stats = {}
  for f in findings_data.get('findings', []):
      dim = f.get('dimension', 'unknown')
      if dim not in dim_stats:
          dim_stats[dim] = {'decided': 0, 'tool_errors': 0}
      if f.get('outcome') in ('accepted', 'rejected'):
          dim_stats[dim]['decided'] += 1
          if f.get('reject_reason') in TOOL_ERROR_REASONS:
              dim_stats[dim]['tool_errors'] += 1
  ```
- **Step 2: Compute pass_agreement per finding by grouping findings by (file, line, dimension).** (Addresses review M1: pass_agreement should reflect multi-pass consensus, not be always 1.0.)
  ```python
  # Group findings by (file, line, dimension) to detect multi-pass agreement
  location_groups = {}
  for f in findings_data.get('findings', []):
      key = (f.get('file', ''), f.get('line', -1), f.get('dimension', ''))
      if key not in location_groups:
          location_groups[key] = set()
      location_groups[key].add(f.get('pass', 1))
  ```
  For each finding, look up its (file, line, dimension) key in location_groups. If multiple passes flagged the same location, pass_agreement = number_of_distinct_passes / total_passes_in_run (use 3 as default total, from the 3-pass pipeline). If only 1 pass flagged it, pass_agreement = 1/3. If the finding already has a non-default pass_agreement in confidence_signals, prefer that value.
- **Step 3: For each finding, compute confidence:**
  - Get dimension FP rate from dim_stats
  - Get per-dimension decided count from dim_stats (this is total_findings for compute_confidence -- per-dimension, NOT global)
  - Get signals from .get('confidence_signals', {}) with defaults
  - Use computed pass_agreement from Step 2 (unless confidence_signals already has a non-default value)
  - Call compute_confidence(dimension_fp_rate, pass_agreement, evidence_count, llm_self_report, per_dimension_decided_count)
  - Set finding['confidence'] = computed value
- Returns the modified findings_data (mutates in place for efficiency)

This function is the bridge between SKILL.md (which records raw signals at finding time with confidence=0.0) and the CLI (which computes actual confidence post-run using historical FP data).
  </action>
  <verify>
    <automated>cd /home/houminxi/code/forge && python3 -c "
import sys; sys.path.insert(0, 'cli')
from forge_cli import wilson_score_interval, compute_confidence, backfill_confidence
# Wilson score basic checks
lo, hi = wilson_score_interval(0, 0)
assert (lo, hi) == (0.0, 1.0), f'empty: {lo},{hi}'
lo, hi = wilson_score_interval(1, 10)
assert 0.0 <= lo < hi <= 1.0, f'bounds: {lo},{hi}'
# Confidence stages -- total_findings is per-dimension decided count (M5)
c1 = compute_confidence(0.15, total_findings=50)
assert abs(c1 - 0.85) < 0.01, f'stage1: {c1}'
c2 = compute_confidence(0.15, pass_agreement=0.8, total_findings=200)
assert 0.0 <= c2 <= 1.0, f'stage2: {c2}'
c3 = compute_confidence(0.15, 0.8, 3, 0.7, 400)
assert 0.0 <= c3 <= 1.0, f'stage3: {c3}'
# Backfill with empty data
data = {'version': 1, 'findings': [], 'runs': []}
result = backfill_confidence(data)
assert result['findings'] == []
# Backfill computes pass_agreement from multi-pass data (M1)
data2 = {'version': 1, 'findings': [
    {'file': 'a.py', 'line': 10, 'dimension': 'correctness', 'pass': 1, 'outcome': 'accepted', 'reject_reason': None},
    {'file': 'a.py', 'line': 10, 'dimension': 'correctness', 'pass': 2, 'outcome': 'accepted', 'reject_reason': None},
], 'runs': []}
result2 = backfill_confidence(data2)
# Both findings at same location should have pass_agreement > 1/3
for f in result2['findings']:
    assert f.get('confidence', 0) > 0, f'confidence not set: {f}'
print('ALL CHECKS PASSED')
"</automated>
  </verify>
  <acceptance_criteria>
    - cli/forge_cli.py contains `import math`
    - cli/forge_cli.py contains `import random`
    - cli/forge_cli.py contains `import re`
    - cli/forge_cli.py contains `MIN_OBSERVATIONS = 20`
    - cli/forge_cli.py contains `def wilson_score_interval(`
    - cli/forge_cli.py contains `def compute_confidence(`
    - cli/forge_cli.py contains `def backfill_confidence(`
    - cli/forge_cli.py contains `# Statistical Utilities`
    - cli/forge_cli.py contains `# Confidence Scoring (D1`
    - wilson_score_interval(0, 0) returns (0.0, 1.0)
    - wilson_score_interval(1, 10) returns values where 0 < lower < upper < 1
    - compute_confidence(0.15, total_findings=50) returns approximately 0.85 (Stage 1)
    - compute_confidence with total_findings=200 uses both fp_rate and pass_agreement (Stage 2)
    - compute_confidence with total_findings=400 uses all 4 signals (Stage 3)
    - compute_confidence total_findings parameter represents per-dimension decided count, not global (M5)
    - backfill_confidence handles empty findings list without error
    - backfill_confidence computes pass_agreement from (file, line, dimension) grouping, not hardcoded 1.0 (M1)
    - backfill_confidence passes per-dimension decided count to compute_confidence, not global count (M5)
    - python3 -m py_compile cli/forge_cli.py exits 0
  </acceptance_criteria>
  <done>Wilson score interval, progressive confidence scoring (3 stages), and backfill function exist in forge_cli.py. All pure functions with no side effects. Stage boundaries match D1: <100, 100-300, 300+. Stage determination uses per-dimension decided count (M5). pass_agreement computed from multi-pass location grouping (M1).</done>
</task>

<task type="auto">
  <name>Task 2: Extend config.json with tier and evaluation defaults, extend SKILL.md finding schema</name>
  <files>cli/config.json, skills/forge/SKILL.md</files>
  <read_first>
    - cli/config.json (current state -- 17 lines, pricing + default_model only)
    - skills/forge/SKILL.md (lines 316-410: finding persistence section, heredoc template, schema docs)
    - .planning/phases/01b-trust-calibration/01b-PATTERNS.md (config structure pattern, finding schema append pattern, validation pattern)
    - .planning/phases/01b-trust-calibration/01b-CONTEXT.md (D1 confidence_signals fields, D2 tier classification signals)
  </read_first>
  <action>
**config.json extension:** Rewrite config.json to add two new top-level sections as peers of `pricing`. Keep the existing pricing and default_model exactly as they are. Add:

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
  "default_model": "claude-sonnet-4-6",
  "tier_classification": {
    "critical_patterns": [
      "(?:auth|security|crypto|secret|token|password|credential)",
      "(?:hooks/check_)",
      "(?:SKILL\\.md)"
    ],
    "ai_markers": [
      "Generated by",
      "Co-Authored-By"
    ],
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

This keeps config.json under 40 lines (well under the 100-line limit from Pitfall 4). Python code hardcodes these as defaults and uses config.json as overrides via `.get()`.

**SKILL.md finding schema extension:** In the finding persistence heredoc (around line 362-376), add two new fields AFTER `'cost_tokens': {'input': 0, 'output': 0}`:

```python
    'confidence': 0.0,
    'confidence_signals': {
        'dimension_fp_rate': 0.0,
        'pass_agreement': 1.0,
        'evidence_count': 1,
        'llm_self_report': 0.8,
    },
```

The `confidence` field is set to 0.0 at recording time because the SKILL.md heredoc cannot compute it (needs historical FP data). The CLI's `backfill_confidence()` computes the actual score post-run.

The `confidence_signals` fields capture raw data from the LLM pass:
- `dimension_fp_rate`: 0.0 (placeholder, computed by CLI)
- `pass_agreement`: 1.0 (1.0 = finding from single pass; set to fraction of agreeing passes when multi-pass data available)
- `evidence_count`: 1 (number of evidence lines cited -- LLM MUST update this to actual count)
- `llm_self_report`: 0.8 (LLM's stated confidence -- LLM MUST update this per finding)

**SKILL.md LLM field instructions (addresses review M4 and M6):** Add explicit instructions in the Finding Persistence section (near the heredoc template, before or after the validation block) telling the LLM how to fill the new fields:

```
## Confidence Signal Instructions

When recording a finding, you MUST set these fields to actual values, not defaults:

- `evidence_count`: Set to the number of distinct code locations (lines, functions, or
  files) you examined to support this finding. Count only locations you actually read
  and cite in the finding description. Minimum 1, typical range 1-10.

- `llm_self_report`: Set to your genuine confidence that this finding is a true positive,
  as a float from 0.0 to 1.0. Consider:
  - 0.9-1.0: You are certain this is a real issue (clear bug, obvious vulnerability)
  - 0.7-0.8: High confidence but some ambiguity (pattern match, context-dependent)
  - 0.4-0.6: Uncertain (could be intentional, might be a style choice)
  - 0.1-0.3: Low confidence (speculative, may be a false positive)
  Do NOT default to 0.8 -- assess each finding individually.
```

Also add validation for the new fields in the validation block (around lines 342-361), AFTER the dimension validation:

```python
evidence_count = 1  # REPLACE_WITH_EVIDENCE_COUNT
llm_self_report = 0.8  # REPLACE_WITH_LLM_CONFIDENCE

if not isinstance(evidence_count, int) or evidence_count < 0:
    print("[forge-warn] Invalid evidence_count, defaulting to 1", file=sys.stderr)
    evidence_count = 1
if not isinstance(llm_self_report, (int, float)) or not (0.0 <= llm_self_report <= 1.0):
    print("[forge-warn] Invalid llm_self_report, defaulting to 0.8", file=sys.stderr)
    llm_self_report = 0.8
```

Update the "Finding schema fields (D1)" doc block (around line 396-409) to add:
- `confidence`: float 0.0-1.0, computed by CLI post-run via backfill_confidence()
- `confidence_signals`: dict with dimension_fp_rate, pass_agreement, evidence_count, llm_self_report

Use the exact field names above. These are the new fields in the schema. The `REPLACE_WITH_*` placeholders follow the same pattern as existing `REPLACE_WITH_SEVERITY` etc.
  </action>
  <verify>
    <automated>cd /home/houminxi/code/forge && python3 -c "
import json
# Validate config.json
with open('cli/config.json') as f:
    cfg = json.load(f)
assert 'tier_classification' in cfg, 'missing tier_classification'
assert 'evaluation' in cfg, 'missing evaluation'
assert cfg['tier_classification']['audit_rate'] == 0.10
assert cfg['evaluation']['min_observations'] == 20
assert cfg['evaluation']['fp_rate_threshold'] == 0.10
assert len(cfg['tier_classification']['critical_patterns']) == 3
assert cfg['default_model'] == 'claude-sonnet-4-6'
print('config.json: VALID')
" && grep -q 'confidence_signals' skills/forge/SKILL.md && echo "SKILL.md confidence_signals: FOUND" && grep -q 'evidence_count' skills/forge/SKILL.md && echo "SKILL.md evidence_count: FOUND" && grep -q 'llm_self_report' skills/forge/SKILL.md && echo "SKILL.md llm_self_report: FOUND" && grep -q 'Set to your genuine confidence' skills/forge/SKILL.md && echo "SKILL.md llm_self_report instruction: FOUND (M4)" && grep -q 'number of distinct code locations' skills/forge/SKILL.md && echo "SKILL.md evidence_count instruction: FOUND (M6)"</automated>
  </verify>
  <acceptance_criteria>
    - cli/config.json is valid JSON (python3 -c "import json; json.load(open('cli/config.json'))" exits 0)
    - cli/config.json contains key "tier_classification" with "critical_patterns", "ai_markers", "audit_rate", "small_diff_threshold"
    - cli/config.json contains key "evaluation" with "min_observations" (20), "fp_rate_threshold" (0.10), "confidence_level" (0.95)
    - cli/config.json retains all existing pricing data unchanged
    - cli/config.json is under 40 lines
    - skills/forge/SKILL.md heredoc contains `'confidence': 0.0`
    - skills/forge/SKILL.md heredoc contains `'confidence_signals': {`
    - skills/forge/SKILL.md heredoc contains `'dimension_fp_rate': 0.0`
    - skills/forge/SKILL.md heredoc contains `'pass_agreement': 1.0`
    - skills/forge/SKILL.md heredoc contains `'evidence_count': 1`
    - skills/forge/SKILL.md heredoc contains `'llm_self_report': 0.8`
    - skills/forge/SKILL.md contains validation for evidence_count and llm_self_report
    - skills/forge/SKILL.md schema docs list confidence and confidence_signals fields
    - skills/forge/SKILL.md contains explicit LLM instruction for evidence_count: "number of distinct code locations" (M6)
    - skills/forge/SKILL.md contains explicit LLM instruction for llm_self_report: "Set to your genuine confidence" with 0.0-1.0 scale guidance (M4)
    - skills/forge/SKILL.md instructs LLM "Do NOT default to 0.8" (M4)
  </acceptance_criteria>
  <done>Config.json extended with tier_classification and evaluation sections. SKILL.md finding schema extended with confidence and confidence_signals fields, including validation, schema documentation, and explicit LLM instructions for evidence_count (M6) and llm_self_report (M4). Backward compatible -- .get() with defaults handles old findings.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| config.json -> forge_cli.py | Config values parsed as JSON; untrusted if user-edited |
| findings.json -> forge_cli.py | Historical findings loaded; may have missing fields |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-01b-01 | Tampering | config.json | mitigate | validate all config values with .get() defaults; reject negative audit_rate or min_observations |
| T-01b-02 | Information Disclosure | confidence scores | accept | confidence scores are developer-facing diagnostic data, not secrets |
| T-01b-03 | Tampering | findings.json missing fields | mitigate | .get('confidence', 0.0) and .get('confidence_signals', {}) with defaults for backward compatibility |
</threat_model>

<verification>
- python3 -m py_compile cli/forge_cli.py exits 0
- python3 -c "import json; json.load(open('cli/config.json'))" exits 0
- grep -q 'def wilson_score_interval' cli/forge_cli.py
- grep -q 'def compute_confidence' cli/forge_cli.py
- grep -q 'confidence_signals' skills/forge/SKILL.md
- grep -q 'tier_classification' cli/config.json
- grep -q 'evaluation' cli/config.json
</verification>

<success_criteria>
1. Wilson score interval function produces correct bounds for edge cases (0/0, 1/10, 5/5)
2. Confidence scoring has 3 progressive stages matching D1 thresholds (<100, 100-300, 300+)
3. Stage determination uses per-dimension decided count, not global count (M5)
4. pass_agreement computed from multi-pass location grouping in backfill_confidence (M1)
5. Config.json has tier_classification and evaluation sections with all specified defaults
6. SKILL.md finding heredoc includes confidence and confidence_signals fields
7. SKILL.md includes explicit LLM instructions for evidence_count and llm_self_report (M4, M6)
8. All existing functionality unchanged (no regressions)
</success_criteria>

<output>
After completion, create `.planning/phases/01b-trust-calibration/01b-01-SUMMARY.md`
</output>
