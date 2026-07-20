---
phase: 01b-trust-calibration
plan: 03
type: execute
wave: 3
depends_on:
  - 01b-01
  - 01b-02
files_modified:
  - cli/forge_cli.py
  - .planning/ROADMAP.md
autonomous: true
requirements:
  - LEARN-07-FULL
  - TRUST-02
  - TRUST-03
must_haves:
  truths:
    - "forge --eval produces per-dimension evaluation against Tricorder 4 criteria"
    - "Dimensions with <20 observations are marked provisional"
    - "Dimensions exceeding 10% ToolFP trigger rule improvement recommendation"
    - "forge --recommend generates specific SKILL.md improvement suggestions with evidence"
    - "Recommendations distinguish tool-wrong (improve detection) from user-won't-act (adjust scope)"
    - "INTENTIONAL reason routes to improve_detection, not adjust_scope"
    - "show_stats displays confidence distribution and tier cost breakdown"
    - "Cost report shows average cost per tier from run sidecar data"
    - "ROADMAP.md Phase 2 success criteria updated from 50% effectiveness to 10% ToolFP rate"
  artifacts:
    - path: "cli/forge_cli.py"
      provides: "evaluate_dimensions(), generate_recommendation(), extended show_stats()"
      contains: "def evaluate_dimensions"
    - path: "cli/forge_cli.py"
      provides: "CLI wiring for --eval and --recommend flags"
      contains: "'--eval'"
    - path: ".planning/ROADMAP.md"
      provides: "Updated Phase 2 success criteria per D5"
      contains: "10% ToolFP"
  key_links:
    - from: "cli/forge_cli.py:evaluate_dimensions"
      to: "cli/forge_cli.py:wilson_score_interval"
      via: "evaluation uses Wilson score for FP rate CI"
      pattern: "wilson_score_interval"
    - from: "cli/forge_cli.py:generate_recommendation"
      to: "cli/forge_cli.py:evaluate_dimensions"
      via: "recommendations flow from evaluation failures"
      pattern: "tool_fp_rate"
    - from: "cli/forge_cli.py:show_stats"
      to: "cli/forge_cli.py:compute_confidence"
      via: "stats display includes confidence distribution"
      pattern: "confidence"
---

<objective>
Add dimension evaluation (D5), rule improvement recommendations (D3/LEARN-07-FULL), extended dashboard, and ROADMAP update to forge CLI.

Purpose: This plan completes Phase 1b by adding the analysis layer on top of the statistical and classification foundations from Plans 01 and 02. The evaluate_dimensions() function assesses each review dimension against Tricorder's 4 quality criteria. The generate_recommendation() function analyzes FP data to produce specific SKILL.md improvement suggestions when ToolFP exceeds 10%. The show_stats() extension adds confidence distribution and cost-per-tier breakdown. The ROADMAP update (H4) aligns Phase 2 success criteria with D5's 10% ToolFP threshold. Together, these fulfill the LEARN-07-FULL requirement: "FP data drives rule improvement."

Output: Extended forge_cli.py with evaluate_dimensions(), generate_recommendation(), extended show_stats(), CLI wiring for --eval and --recommend flags, and updated ROADMAP.md.
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
@.planning/phases/01b-trust-calibration/01b-01-SUMMARY.md
@.planning/phases/01b-trust-calibration/01b-02-SUMMARY.md

<interfaces>
<!-- Key types and contracts from Plans 01 and 02. -->

From cli/forge_cli.py (Plan 01 additions):
```python
MIN_OBSERVATIONS = 20

def wilson_score_interval(successes, total, confidence=0.95):
    """Compute Wilson score confidence interval for a proportion.
    Returns: (lower, upper) bounds of the FP rate estimate"""

def compute_confidence(dimension_fp_rate, pass_agreement=1.0,
                       evidence_count=1, llm_self_report=0.8,
                       total_findings=0):
    """Compute confidence score for a finding. Progressive stages.
    total_findings is per-dimension decided count (M5)."""

def backfill_confidence(findings_data):
    """Compute confidence scores for all findings based on dimension FP rates.
    Computes pass_agreement from (file, line, dimension) grouping (M1)."""
```

From cli/forge_cli.py (Plan 02 additions):
```python
def classify_change(diff_spec, override=None, config=None):
    """Classify a change into full/light/step0 tier."""

# run_forge now includes tier and was_audited in run_record
# run_forge calls backfill_confidence() at end (H1)
# run sidecar schema: {..., 'tier': 'full'|'light'|'step0', 'was_audited': bool}
```

From cli/forge_cli.py (existing functions):
```python
TOOL_ERROR_REASONS = {
    'HALLUCINATION', 'CONTEXT_MISSING', 'INTENTIONAL', 'NOT_APPLICABLE',
}
USER_PREF_REASONS = {'STYLE_PREFERENCE', 'ACCEPTABLE_RISK'}

def load_findings():
    """Load .forge/findings.json. Returns dict with 'findings' list."""

def load_all_runs():
    """Load all run records from .forge/runs/*.json sidecar files."""

def show_stats(json_format=False):
    """Display FP rate dashboard from findings.json (TRUST-05)."""

def load_config():
    """Load CLI configuration from config.json."""
```

From cli/config.json (Plan 01 additions):
```json
"evaluation": {
    "min_observations": 20,
    "fp_rate_threshold": 0.10,
    "confidence_level": 0.95
}
```
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Add evaluate_dimensions(), generate_recommendation(), and extend show_stats()</name>
  <files>cli/forge_cli.py</files>
  <read_first>
    - cli/forge_cli.py (full file -- post Plan 01 and Plan 02: look for wilson_score_interval, compute_confidence, classify_change, show_stats, load_findings, load_all_runs, TOOL_ERROR_REASONS, USER_PREF_REASONS, MIN_OBSERVATIONS)
    - .planning/phases/01b-trust-calibration/01b-CONTEXT.md (D3: rule improvement flow, ToolFP vs UserFP, minimum 20 observations; D5: Tricorder 4 criteria, evaluation process)
    - .planning/phases/01b-trust-calibration/01b-RESEARCH.md (Pattern 4: generate_recommendation, Code Examples: evaluate_dimensions)
    - .planning/phases/01b-trust-calibration/01b-PATTERNS.md (dashboard terminal output pattern, dimension aggregation pattern, docstring convention)
  </read_first>
  <action>
Add a new section AFTER the "Tier Classification" section (from Plan 02) and BEFORE "Core: run_dry_run":

Section header:
```python
# ---------------------------------------------------------------------------
# Evaluation and Recommendation (D3, D5 -- rule improvement pipeline)
# ---------------------------------------------------------------------------
```

**Function 1 -- `evaluate_dimensions(findings, config=None, json_format=False)`:**

This function evaluates all 12 dimensions against Tricorder's 4 quality criteria (D5).

Implementation:
1. If config is None, call load_config()
2. Get min_observations from config: `config.get('evaluation', {}).get('min_observations', MIN_OBSERVATIONS)`
3. Get fp_threshold from config: `config.get('evaluation', {}).get('fp_rate_threshold', 0.10)`
4. Get confidence_level from config: `config.get('evaluation', {}).get('confidence_level', 0.95)`
5. Group findings by dimension using the dimension aggregation pattern (see PATTERNS.md):
```python
dims = {}
for f in findings:
    dim = f.get('dimension', 'unknown')
    if dim not in dims:
        dims[dim] = []
    dims[dim].append(f)
```
6. For each dimension, compute:
   - decided = [f for f in dim_findings if f.get('outcome') in ('accepted', 'rejected')]
   - total_decided = len(decided)
   - provisional = total_decided < min_observations
   - If provisional: all 4 criteria = 'insufficient data'
   - Else:
     - tool_errors = count where reject_reason in TOOL_ERROR_REASONS
     - tool_fp_rate = tool_errors / total_decided
     - lower, upper = wilson_score_interval(tool_errors, total_decided, confidence_level)
     - accepted_count = count where outcome == 'accepted'
     - acceptance_rate = accepted_count / total_decided
     - user_prefs = count where reject_reason in USER_PREF_REASONS
     - user_fp_rate = user_prefs / total_decided
   - Build criteria dict:
     - 'understandable': 'manual review required' (cannot automate -- RESEARCH.md Open Question 2)
     - 'actionable': 'manual review required'
     - 'fp_rate': dict with 'pass' (bool: tool_fp_rate <= fp_threshold), 'rate' (float), 'ci_lower', 'ci_upper', 'action' ('trigger D3 rule improvement' or 'none')
     - 'significant_impact': dict with 'pass' (bool: acceptance_rate >= 0.50), 'acceptance_rate', 'action' ('review dimension scope' or 'none')
7. Build report dict: {dimension: {total_observations, provisional, criteria, tool_fp_rate, user_fp_rate}}

Output format (terminal, unless json_format):
```
================================================================================
Forge Dimension Evaluation (Tricorder 4 Criteria)
================================================================================

Dimension          Obs  Prov  ToolFP%  CI[95%]       Impact%  Status
----------------------------------------------------------------------------------
correctness         45  no      8.9%  [ 3.1%, 19.1%]  73.3%  PASS
security            12  YES      --         --          --    provisional
performance         32  no     15.6%  [ 7.2%, 29.0%]  62.5%  FAIL: FP rate
...
----------------------------------------------------------------------------------

Legend:
  Obs = decided findings | Prov = provisional (<20 obs)
  ToolFP% = categories 1-4 rate | CI = Wilson 95% confidence interval
  Impact% = acceptance rate | Status = PASS / FAIL / provisional
  
Criteria 1 (Understandable) and 2 (Actionable): manual review required
```

If json_format: output the report dict as JSON via print(json.dumps(report, indent=2)).

**Function 2 -- `generate_recommendation(dimension, findings, config=None)`:**

This function generates rule improvement recommendations when ToolFP > 10% (D3).

Implementation:
1. If config is None, call load_config()
2. Get min_observations and fp_threshold from config (same as evaluate_dimensions)
3. Filter to decided findings for this dimension
4. If len(decided) < min_observations, return None
5. Compute tool_errors (reject_reason in TOOL_ERROR_REASONS)
6. tool_fp_rate = len(tool_errors) / len(decided)
7. If tool_fp_rate <= fp_threshold, return None (within threshold)
8. Compute reason_counts: count each reject_reason in tool_errors
9. Find dominant_reason = max(reason_counts, key=reason_counts.get)
10. Build recommendation dict:
    - 'dimension': dimension name
    - 'tool_fp_rate': rate as float
    - 'total_decided': count
    - 'dominant_reason': reason string
    - 'reason_breakdown': dict of reason->count
    - **(Addresses review H3: INTENTIONAL correctly classified as improve_detection)** 'action': `'improve_detection'` if dominant_reason in `('HALLUCINATION', 'CONTEXT_MISSING', 'NOT_APPLICABLE', 'INTENTIONAL')` else `'adjust_scope'`. INTENTIONAL is a tool-wrong category (categories 1-4 per D3) -- the tool flagged something the developer did intentionally, meaning detection needs to be smarter, not that scope needs narrowing.
    - 'suggestion': specific text per D3:
      - improve_detection: "Modify SKILL.md prompt for '{dimension}' dimension: dominant FP cause is {dominant_reason} ({count}/{total_errors} tool errors). Add explicit negative examples or context requirements to the dimension definition."
      - adjust_scope: "Consider narrowing scope of '{dimension}' dimension: dominant FP cause is {dominant_reason}. Users consistently reject findings in this category."
11. Return the recommendation dict

**Function 3 -- `show_recommendations(json_format=False)`:**

Orchestrator function that calls generate_recommendation for every dimension and displays results.

Implementation:
1. Load findings via load_findings()
2. Group by dimension (same aggregation pattern)
3. For each dimension, call generate_recommendation()
4. Collect non-None results
5. If no recommendations: print "No dimensions exceed the 10% ToolFP threshold (or insufficient data)."
6. If recommendations exist, display terminal table:
```
================================================================================
Forge Rule Improvement Recommendations (D3)
================================================================================

Dimension          ToolFP%  Dominant Reason    Action            Decided
----------------------------------------------------------------------------------
performance         15.6%  HALLUCINATION      improve_detection  32
convention          22.2%  STYLE_PREFERENCE   adjust_scope       27
----------------------------------------------------------------------------------

Detailed Recommendations:
----------------------------------------------------------------------------------

1. performance (ToolFP: 15.6%, 32 decided findings)

   Dominant cause: HALLUCINATION (5/7 tool errors)
   Breakdown: HALLUCINATION=5, CONTEXT_MISSING=2

   Suggestion: Modify SKILL.md prompt for 'performance' dimension:
   dominant FP cause is HALLUCINATION (5/7 tool errors). Add explicit
   negative examples or context requirements to the dimension definition.

----------------------------------------------------------------------------------
```
7. If json_format: output list of recommendation dicts as JSON.

Now extend show_stats() to add confidence distribution and cost-per-tier sections:

After the existing FP rate table in show_stats() (around line 724), add two new sections:

**Confidence distribution section:**
1. Load all findings
2. Compute confidence for each using backfill_confidence()
3. Bucket into ranges: [0.0-0.2), [0.2-0.4), [0.4-0.6), [0.6-0.8), [0.8-1.0]
4. Display bar chart (text-based):
```
Confidence Distribution:
  0.0-0.2  |###          |   3
  0.2-0.4  |#####        |   5
  0.4-0.6  |########     |   8
  0.6-0.8  |############|  12
  0.8-1.0  |##########   |  10
```

**Cost-per-tier section:**
1. Load all runs via load_all_runs()
2. Group by tier (using .get('tier', 'full') for backward compat with pre-Phase-1b runs)
3. For each tier: count, total cost, average cost
4. Display:
```
Cost by Tier:
  Tier       Runs  Total Cost   Avg Cost
  full         15    $6.3000    $0.4200
  light         8    $1.2000    $0.1500
  step0         5    $0.0000    $0.0000
```

If json_format is True, add both sections to the JSON output dict.

Finally, wire new CLI flags in main():

Add to argument parser:
```python
parser.add_argument(
    '--eval', action='store_true',
    help='Evaluate dimensions against Tricorder 4 criteria (D5)',
)
parser.add_argument(
    '--recommend', action='store_true',
    help='Generate rule improvement recommendations (D3)',
)
```

Add to if/elif routing (before the existing args.stats check):
```python
if args.eval:
    data = load_findings()
    evaluate_dimensions(data.get('findings', []), json_format=args.json)
elif args.recommend:
    show_recommendations(json_format=args.json)
elif args.stats:
    show_stats(json_format=args.json)
```
  </action>
  <verify>
    <automated>cd /home/houminxi/code/forge && python3 -m py_compile cli/forge_cli.py && python3 -c "
import sys; sys.path.insert(0, 'cli')
from forge_cli import evaluate_dimensions, generate_recommendation, show_recommendations
# Test with empty findings
report = evaluate_dimensions([], json_format=False)
# Should not crash

# Test generate_recommendation with insufficient data
result = generate_recommendation('security', [])
assert result is None, 'should return None for empty findings'

# Test with mock decided findings (>20 obs, >10% ToolFP)
findings = []
for i in range(25):
    f = {'dimension': 'test_dim', 'outcome': 'accepted', 'reject_reason': None}
    findings.append(f)
# Add some tool errors
for i in range(5):
    f = {'dimension': 'test_dim', 'outcome': 'rejected', 'reject_reason': 'HALLUCINATION'}
    findings.append(f)
result = generate_recommendation('test_dim', findings)
assert result is not None, 'should generate recommendation for >10% ToolFP'
assert result['action'] == 'improve_detection'
assert result['dominant_reason'] == 'HALLUCINATION'

# H3: Verify INTENTIONAL routes to improve_detection, not adjust_scope
findings_intentional = []
for i in range(20):
    findings_intentional.append({'dimension': 'test2', 'outcome': 'accepted', 'reject_reason': None})
for i in range(5):
    findings_intentional.append({'dimension': 'test2', 'outcome': 'rejected', 'reject_reason': 'INTENTIONAL'})
result2 = generate_recommendation('test2', findings_intentional)
assert result2 is not None, 'should generate recommendation'
assert result2['action'] == 'improve_detection', f'H3: INTENTIONAL should be improve_detection, got {result2[\"action\"]}'
assert result2['dominant_reason'] == 'INTENTIONAL'
print('evaluate + recommend: ALL CHECKS PASSED (including H3)')
"</automated>
  </verify>
  <acceptance_criteria>
    - cli/forge_cli.py contains `def evaluate_dimensions(`
    - cli/forge_cli.py contains `def generate_recommendation(`
    - cli/forge_cli.py contains `def show_recommendations(`
    - cli/forge_cli.py contains `# Evaluation and Recommendation (D3, D5`
    - cli/forge_cli.py contains `'--eval'`
    - cli/forge_cli.py contains `'--recommend'`
    - evaluate_dimensions handles empty findings list without error
    - evaluate_dimensions marks dimensions with <20 observations as provisional
    - evaluate_dimensions computes Wilson score CI for non-provisional dimensions
    - evaluate_dimensions checks ToolFP rate against 10% threshold (not total FP)
    - generate_recommendation returns None for <20 observations
    - generate_recommendation returns None when ToolFP <= 10%
    - generate_recommendation returns 'improve_detection' when dominant reason is HALLUCINATION, CONTEXT_MISSING, NOT_APPLICABLE, or INTENTIONAL (H3)
    - generate_recommendation returns 'adjust_scope' only when dominant reason is STYLE_PREFERENCE or ACCEPTABLE_RISK (H3)
    - show_stats includes confidence distribution section
    - show_stats includes cost-per-tier section using run sidecar data
    - main() routes --eval to evaluate_dimensions
    - main() routes --recommend to show_recommendations
    - python3 -m py_compile cli/forge_cli.py exits 0
  </acceptance_criteria>
  <done>Dimension evaluation (Tricorder 4 criteria), rule improvement recommendations, confidence distribution, and cost-per-tier breakdown are all implemented. CLI wired with --eval and --recommend flags. FP data drives rule improvement (D3/LEARN-07-FULL) -- not numerical suppression thresholds. INTENTIONAL correctly routes to improve_detection (H3).</done>
</task>

<task type="auto">
  <name>Task 2: Update ROADMAP.md Phase 2 success criteria per D5</name>
  <files>.planning/ROADMAP.md</files>
  <read_first>
    - .planning/ROADMAP.md (Phase 2 section, specifically success criterion 1 about "50% effectiveness")
    - .planning/phases/01b-trust-calibration/01b-CONTEXT.md (D5: "ROADMAP update required: Change success criterion from '50% effectiveness' to '10% ToolFP rate'")
  </read_first>
  <action>
**(Addresses review H4: Missing ROADMAP update)**

In `.planning/ROADMAP.md`, find the Phase 2 success criteria section. Locate criterion 1:

```
1. Existing dimensions with below-50% effectiveness are merged or retired before any new dimensions are added
```

Replace with:

```
1. Existing dimensions exceeding 10% ToolFP rate (per Phase 1b evaluation) are improved via D3 rule improvement flow, merged, or retired before any new dimensions are added
```

This aligns Phase 2's entry gate with D5's Tricorder 4 criteria (10% ToolFP threshold) instead of the original vague "50% effectiveness" metric. The change was explicitly required by D5 in CONTEXT.md: "ROADMAP update required: Change success criterion from 'dimensions below 50% effectiveness are flagged' to 'dimensions exceeding 10% ToolFP rate trigger rule improvement.'"
  </action>
  <verify>
    <automated>cd /home/houminxi/code/forge && grep -q '10% ToolFP' .planning/ROADMAP.md && echo "ROADMAP 10% ToolFP: FOUND" && ! grep -q 'below-50% effectiveness' .planning/ROADMAP.md && echo "ROADMAP old 50% criterion: REMOVED"</automated>
  </verify>
  <acceptance_criteria>
    - .planning/ROADMAP.md Phase 2 success criterion 1 references "10% ToolFP rate" instead of "50% effectiveness"
    - .planning/ROADMAP.md Phase 2 success criterion 1 mentions D3 rule improvement flow
    - The old "below-50% effectiveness" text no longer appears in Phase 2
    - All other ROADMAP content is unchanged
  </acceptance_criteria>
  <done>ROADMAP.md Phase 2 success criteria updated from "50% effectiveness" to "10% ToolFP rate" per D5 decision. Phase 2 entry gate now aligned with Tricorder 4 criteria established in Phase 1b.</done>
</task>

</tasks>

<!-- Integration verification note (addresses review M8):
The executor should verify the full end-to-end chain works after completing this plan:
1. Finding persistence (Plan 01 SKILL.md schema) -> confidence scoring (Plan 01 backfill_confidence)
2. Confidence scoring -> tier classification (Plan 02 classify_change) 
3. Tier classification -> evaluation (Plan 03 evaluate_dimensions) -> recommendation (Plan 03 generate_recommendation)
Run this integration check after Plan 03 completes:
  python3 -c "
  import sys; sys.path.insert(0, 'cli')
  from forge_cli import (load_findings, backfill_confidence, classify_change, 
                          evaluate_dimensions, generate_recommendation)
  # Chain: load -> backfill -> evaluate -> recommend
  data = load_findings()
  data = backfill_confidence(data)
  findings = data.get('findings', [])
  evaluate_dimensions(findings, json_format=True)
  # Check recommendation pipeline
  dims = {}
  for f in findings:
      d = f.get('dimension', 'unknown')
      if d not in dims: dims[d] = []
      dims[d].append(f)
  for dim, fs in dims.items():
      r = generate_recommendation(dim, fs)
      if r: print(f'Recommendation for {dim}: {r[\"action\"]}')
  print('END-TO-END CHAIN: OK')
  "
-->

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| findings.json -> evaluation | FP data could be poisoned by mass rejections |
| evaluation -> recommendation | FP rate drives SKILL.md change suggestions |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-01b-09 | Tampering | FP data poisoning | mitigate | minimum 20 observations + Wilson CI width shows uncertainty; recommendations are suggestions not auto-applied (D3 requires user PR approval) |
| T-01b-10 | Information Disclosure | recommendation output | accept | recommendations contain dimension names and FP rates -- developer-facing diagnostic data |
| T-01b-11 | Denial of Service | large findings.json | mitigate | all aggregation is O(n) single-pass; no nested loops over findings |
</threat_model>

<verification>
- python3 -m py_compile cli/forge_cli.py exits 0
- grep -q 'def evaluate_dimensions' cli/forge_cli.py
- grep -q 'def generate_recommendation' cli/forge_cli.py
- grep -q 'def show_recommendations' cli/forge_cli.py
- grep -q "'--eval'" cli/forge_cli.py
- grep -q "'--recommend'" cli/forge_cli.py
- grep -q '10% ToolFP' .planning/ROADMAP.md
- evaluate_dimensions produces terminal output without crash
- generate_recommendation returns None for insufficient data
- generate_recommendation returns recommendation dict for high ToolFP
- generate_recommendation returns 'improve_detection' for INTENTIONAL (H3)
</verification>

<success_criteria>
1. forge --eval shows per-dimension evaluation with Tricorder 4 criteria
2. Provisional dimensions (<20 obs) clearly marked, not evaluated
3. forge --recommend generates actionable SKILL.md improvement suggestions
4. Recommendations distinguish tool-wrong from user-won't-act
5. INTENTIONAL routes to improve_detection, not adjust_scope (H3)
6. forge --stats now includes confidence distribution and cost-per-tier
7. All new functions handle empty/missing data gracefully
8. No numerical suppression thresholds -- FP data drives rule improvement only (D3)
9. ROADMAP.md Phase 2 success criterion updated from "50% effectiveness" to "10% ToolFP rate" (H4)
10. End-to-end chain verifiable: finding -> confidence -> tier -> evaluation -> recommendation (M8)
</success_criteria>

<output>
After completion, create `.planning/phases/01b-trust-calibration/01b-03-SUMMARY.md`
</output>
