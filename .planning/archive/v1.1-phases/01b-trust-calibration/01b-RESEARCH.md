# Phase 1b: Trust Calibration - Research

**Researched:** 2026-05-12
**Domain:** Statistical calibration, tiered code review, rule improvement pipelines
**Confidence:** HIGH

## Summary

Phase 1b transforms forge from "run everything the same way" to "invest review effort where it matters." The phase adds three capabilities to the existing Phase 1a foundation: (1) confidence scoring per finding using a progressive multi-signal formula, (2) deterministic tier classification that routes changes to full/light/step0-only review, and (3) a data-driven rule improvement loop that fixes SKILL.md prompts when FP rates exceed 10% -- replacing numerical threshold tuning with the approach validated by SonarQube's 3.2% FP rate across 137M issues.

The existing codebase provides strong foundations: `forge_cli.py` (922 lines) already has `show_stats()` with ToolFP/UserFP split, `classify_findings()` with 6-category taxonomy, `run_dry_run()` with file-type detection, and `atomic_write()` for safe JSON persistence. `check_review_tracker.sh` (442 lines) has `_max_severity()` for severity classification. The finding persistence schema in SKILL.md records all necessary fields. Phase 1b extends these existing functions rather than building from scratch.

**Primary recommendation:** Implement tier classification as a pure Python function in `forge_cli.py` that runs before `claude -p` invocation. Confidence scoring lives in the finding persistence path (SKILL.md heredoc). Rule improvement recommendations are generated as markdown reports, not automatic changes -- user approves via PR.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D1: Confidence Scoring** -- Multi-signal schema (dimension_fp_rate, pass_agreement, evidence_count, llm_self_report). Progressive calculation in 3 stages based on data volume. Low-confidence findings shown with label, not suppressed.
- **D2: Tiered Review Depth** -- Composite scoring with 4 signals (file criticality, change type, diff size, AI-generated flag). 3 tiers: full/light/step0-only. Classification is deterministic Python, never LLM. Override only escalates. 10% audit sampling.
- **D3: Threshold Tuning** -- NO numerical suppression thresholds. FP data drives RULE IMPROVEMENT (modify SKILL.md prompts). Generate recommendations as PRs. Separate ToolFP vs UserFP tracking. Minimum 20 observations per dimension.
- **D4: Data Sufficiency** -- NO 30-day wait. Tricorder mode: deploy immediately, all dimensions active, "provisional" label until 20+ data points. Bootstrap data (~53 points) treated equally.
- **D5: Dimension Evaluation** -- Tricorder 4 criteria: Understandable, Actionable, <10% FP rate, Significant Impact. Replaces ROADMAP's 50% effectiveness threshold.

### Claude's Discretion
- Finding persistence schema extensions (exact field names, validation rules)
- CLI flag naming (`--full`, `--light`, `--step0` vs alternatives)
- Audit sampling implementation details (random seed, logging format)
- Evaluation report output format (terminal table vs markdown vs JSON)
- Wilson score confidence interval implementation (scipy vs manual formula)

### Deferred Ideas (OUT OF SCOPE)
- Outdated rate tracking (passive signal from git history correlation) -- Phase 2+
- Semgrep Memories-style pattern matching -- Phase 3
- Cross-project dimension transfer -- v2 ADV-02
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| TRUST-02 | Confidence scoring -- each finding gets a confidence score (0-1) based on evidence strength | D1 progressive formula, Wilson score intervals for FP rate estimation, 3-stage rollout |
| TRUST-03 | Tiered review depth -- auto-classify changes into full/light/step0-only based on risk signals | D2 composite scoring, deterministic Python classification, anti-gaming design |
| LEARN-07-FULL | Per-dimension FP thresholds tuned based on accumulated feedback data; dimensions with stable FP rates get automatic suppression thresholds | D3 redefines this: FP data drives rule improvement (SKILL.md prompt modification), not suppression. D4/D5 set 10% ToolFP threshold with Tricorder 4 criteria |
</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Tier classification | CLI (Python) | -- | Must run before LLM invocation; deterministic Python code in forge_cli.py |
| Confidence scoring (formula) | CLI (Python) | SKILL.md (recording) | Formula computed in Python; SKILL.md heredoc records the raw signals per finding |
| Confidence signal recording | SKILL.md (LLM passes) | -- | LLM passes produce evidence_count and llm_self_report; recorded during finding persistence |
| FP rate computation | CLI (Python) | -- | Pure data aggregation from findings.json; extends existing show_stats() |
| Wilson score intervals | CLI (Python) | -- | Statistical computation, no external dependency needed |
| Rule improvement recommendations | CLI (Python) | -- | Analyze findings.json, generate markdown report with suggested SKILL.md changes |
| Evaluation report (Tricorder 4) | CLI (Python) | -- | Extends show_stats() with per-dimension evaluation against 4 criteria |
| Audit sampling | CLI (Python) | -- | Random selection logic in tier classification function |
| Cost report by tier | CLI (Python) | -- | Aggregate run sidecar data by tier classification |
| Schema extensions | findings.json | -- | Add confidence and confidence_signals fields to existing schema |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python | 3.14.4 | All Phase 1b logic | Already used for forge_cli.py [VERIFIED: `python3 --version`] |
| math (stdlib) | -- | Wilson score computation (sqrt) | Zero dependency; scipy/statsmodels NOT installed [VERIFIED: `pip3 show scipy` returns nothing] |
| json (stdlib) | -- | findings.json read/write | Already used throughout codebase [VERIFIED: forge_cli.py imports] |
| subprocess (stdlib) | -- | git diff parsing for tier classification | Already used in run_dry_run() [VERIFIED: forge_cli.py imports] |
| uuid (stdlib) | -- | Finding IDs | Already used [VERIFIED: forge_cli.py imports] |
| random (stdlib) | -- | Audit sampling (10% random selection) | Zero dependency alternative to external RNG |
| argparse (stdlib) | -- | CLI extensions (--full, --eval, --recommend) | Already used [VERIFIED: forge_cli.py imports] |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| jq | 1.8.1 | JSON processing in shell scripts | For hook-level JSON queries [VERIFIED: `jq --version`] |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Manual Wilson score | scipy.stats or statsmodels | Adds ~50MB dependency for a 15-line formula; not justified for single-user CLI tool |
| unidiff (Python pkg) | subprocess + git diff | unidiff not installed; git diff parsing sufficient for change type detection |
| gitpython | subprocess + git | gitpython not installed; subprocess calls to git are simpler and already used in codebase |

**Installation:**
```bash
# No new packages required. All Phase 1b code uses Python stdlib only.
```

**Version verification:** No new packages to verify. Python 3.14.4 confirmed available. [VERIFIED: local environment probe]

## Architecture Patterns

### System Architecture Diagram

```
                    forge <diff-spec> [--full|--step0]
                              |
                              v
                   +---------------------+
                   | Tier Classification  |  (NEW - deterministic Python)
                   | classify_change()    |
                   +---------------------+
                   |  file_criticality()  |
                   |  detect_change_type()|
                   |  measure_diff_size() |
                   |  check_ai_flag()     |
                   +---------------------+
                              |
                     tier = full|light|step0
                              |
           +------------------+------------------+
           |                  |                  |
           v                  v                  v
      [step0-only]       [light]            [full]
      run_dry_run()    Step 0 + 1 cycle   Step 0 + 3 cycles
      exit              of Steps 1-3       Steps 1-3 + 3.5
                        no Step 4          + Step 4
                              |                  |
                              v                  v
                   +---------------------+
                   | Finding Persistence  |  (EXTENDED)
                   | + confidence_signals |
                   | + confidence score   |
                   +---------------------+
                              |
                              v
                   .forge/findings.json
                              |
              +---------------+---------------+
              |               |               |
              v               v               v
        show_stats()    evaluate()      recommend()
        (EXTENDED)      (NEW - D5)      (NEW - D3)
        + confidence    Tricorder 4     Rule improvement
        + tier costs    criteria        suggestions
                        per dimension
```

### Recommended Project Structure

No new files or directories. All Phase 1b code extends existing files:

```
cli/
  forge_cli.py          # EXTEND: add classify_change(), evaluate(),
                        #         recommend(), Wilson score, tier routing
  config.json           # EXTEND: add tier weights, criticality patterns,
                        #         evaluation thresholds
skills/
  forge/
    SKILL.md            # EXTEND: finding persistence adds confidence_signals
hooks/
  check_review_tracker.sh  # NO CHANGE (already records severity data)
.forge/
  findings.json         # SCHEMA EXTEND: add confidence, confidence_signals
  runs/*.json           # SCHEMA EXTEND: add tier field
```

### Pattern 1: Deterministic Tier Classification (D2)

**What:** Pure Python function that determines review depth before LLM invocation.
**When to use:** Every `forge <diff-spec>` invocation (unless `--full` override).

```python
# Pattern: tier classification as pure function
# Source: D2 decision in CONTEXT.md + Chromium CQ tiered model

# Criticality patterns stored in config.json (not hardcoded)
CRITICAL_PATTERNS = [
    r'(?:auth|security|crypto|secret|token|password|credential)',
    r'(?:hooks/check_)',  # forge's own enforcement hooks
    r'(?:SKILL\.md)',     # review pipeline definitions
]

def classify_change(diff_spec, override=None):
    """Classify a change into full/light/step0-only tier.

    Returns: 'full', 'light', or 'step0'

    Classification is deterministic Python -- LLM never sees tier options.
    Override only escalates (--full always accepted, --step0 rejected
    for critical files).
    """
    if override == 'full':
        return 'full'

    files = _get_changed_files(diff_spec)
    diff_lines = _count_diff_lines(diff_spec)
    change_type = _detect_change_type(diff_spec, files)
    is_critical = _has_critical_files(files)
    is_ai = _detect_ai_generated(diff_spec)

    # Critical files: always full (cannot downgrade)
    if is_critical:
        return 'full'

    # AI-generated code: minimum light (29-45% contain vulns)
    if is_ai:
        if override == 'step0':
            return 'light'  # reject downgrade, enforce minimum
        return 'full' if diff_lines > 50 else 'light'

    # Comment-only / whitespace-only: step0-only
    if change_type in ('comment_only', 'whitespace_only'):
        return 'step0'

    # Small non-critical changes: bias toward light
    if diff_lines < 10 and not is_critical:
        return 'light'

    # Default: full (conservative until audit data validates)
    return 'full'
```

### Pattern 2: Wilson Score Confidence Interval (no external deps)

**What:** Pure-math implementation of Wilson score interval for FP rate estimation with small samples.
**When to use:** Whenever computing FP rate for a dimension with <100 observations.

```python
# Source: Wilson 1927, verified against statsmodels formula
# [CITED: https://www.statsmodels.org/stable/generated/statsmodels.stats.proportion.proportion_confint.html]
import math

def wilson_score_interval(successes, total, confidence=0.95):
    """Compute Wilson score confidence interval for a proportion.

    Args:
        successes: number of FP findings (tool-error rejections)
        total: total decided findings
        confidence: confidence level (default 0.95)

    Returns:
        (lower, upper) bounds of the FP rate estimate
    """
    if total == 0:
        return (0.0, 1.0)

    p = successes / total
    # z-score for 95% CI = 1.96 (hardcoded for common case)
    z = 1.96 if confidence == 0.95 else _z_score(confidence)
    z2 = z * z

    denominator = 1 + z2 / total
    centre = (p + z2 / (2 * total)) / denominator
    margin = z * math.sqrt(
        (p * (1 - p) + z2 / (4 * total)) / total
    ) / denominator

    return (max(0.0, centre - margin), min(1.0, centre + margin))


def _z_score(confidence):
    """Approximate z-score for common confidence levels."""
    # Exact values for common levels; avoids scipy dependency
    table = {0.90: 1.645, 0.95: 1.96, 0.99: 2.576}
    return table.get(confidence, 1.96)
```

### Pattern 3: Progressive Confidence Scoring (D1)

**What:** Multi-stage confidence formula that gains precision as data accumulates.
**When to use:** At finding persistence time (SKILL.md heredoc) and in show_stats().

```python
# Source: D1 decision in CONTEXT.md

def compute_confidence(dimension_fp_rate, pass_agreement=1.0,
                       evidence_count=1, llm_self_report=0.8,
                       total_findings=0):
    """Compute confidence score for a finding.

    Progressive: uses more signals as data volume grows.
    Stage 1 (<100): dimension_fp_rate only
    Stage 2 (100-300): add pass_agreement
    Stage 3 (300+): full composite with all 4 signals
    """
    if total_findings < 100:
        # Stage 1: only reliable signal is dimension FP rate
        return max(0.0, min(1.0, 1.0 - dimension_fp_rate))

    if total_findings < 300:
        # Stage 2: add pass_agreement weight
        w_fp = 0.6
        w_agree = 0.4
        return max(0.0, min(1.0,
            w_fp * (1.0 - dimension_fp_rate) +
            w_agree * pass_agreement
        ))

    # Stage 3: full composite
    w_fp = 0.35
    w_agree = 0.25
    w_evidence = 0.20
    w_llm = 0.20
    evidence_score = min(1.0, evidence_count / 5.0)
    return max(0.0, min(1.0,
        w_fp * (1.0 - dimension_fp_rate) +
        w_agree * pass_agreement +
        w_evidence * evidence_score +
        w_llm * llm_self_report
    ))
```

### Pattern 4: Rule Improvement Recommendation (D3)

**What:** Analyze FP data, generate actionable SKILL.md improvement suggestions.
**When to use:** When a dimension exceeds 10% ToolFP rate with 20+ observations.

```python
# Source: D3 decision in CONTEXT.md + SonarQube "fix rules not thresholds"

def generate_recommendation(dimension, findings):
    """Generate rule improvement recommendation for a dimension.

    Analyzes reject_reason breakdown to determine if the problem
    is tool-wrong (improve detection) or user-won't-act (adjust scope).
    """
    decided = [f for f in findings
               if f['outcome'] in ('accepted', 'rejected')]
    if len(decided) < 20:
        return None  # insufficient data

    tool_errors = [f for f in decided
                   if f.get('reject_reason') in TOOL_ERROR_REASONS]
    user_prefs = [f for f in decided
                  if f.get('reject_reason') in USER_PREF_REASONS]

    tool_fp_rate = len(tool_errors) / len(decided)
    if tool_fp_rate <= 0.10:
        return None  # within threshold

    # Analyze reject_reason breakdown
    reason_counts = {}
    for f in tool_errors:
        reason = f.get('reject_reason', 'UNKNOWN')
        reason_counts[reason] = reason_counts.get(reason, 0) + 1

    dominant_reason = max(reason_counts, key=reason_counts.get)

    recommendation = {
        'dimension': dimension,
        'tool_fp_rate': tool_fp_rate,
        'total_decided': len(decided),
        'dominant_reason': dominant_reason,
        'reason_breakdown': reason_counts,
    }

    if dominant_reason in ('HALLUCINATION', 'CONTEXT_MISSING',
                           'NOT_APPLICABLE'):
        recommendation['action'] = 'improve_detection'
        recommendation['suggestion'] = (
            f"Modify SKILL.md prompt for '{dimension}' dimension: "
            f"dominant FP cause is {dominant_reason} "
            f"({reason_counts[dominant_reason]}/{len(tool_errors)} "
            f"tool errors). Add explicit negative examples or "
            f"context requirements to the dimension definition."
        )
    else:
        recommendation['action'] = 'adjust_scope'
        recommendation['suggestion'] = (
            f"Consider narrowing scope of '{dimension}' dimension: "
            f"dominant FP cause is {dominant_reason}. "
            f"Users consistently reject findings in this category."
        )

    return recommendation
```

### Anti-Patterns to Avoid

- **LLM choosing its own workload:** The LLM must never know about tier options. Classification happens in Python before `claude -p` is invoked. The LLM receives "execute full review" or "execute light review" -- never "choose between full/light/step0." [CITED: D2 anti-gaming design in CONTEXT.md]
- **Threshold tuning instead of rule improvement:** When FP rate is high, do NOT adjust a numerical threshold to suppress findings. Fix the rules (SKILL.md prompts). SonarQube achieved 3.2% FP rate at 137M issues with this approach. [CITED: sonarsource.com/blog/how-sonarqube-minimizes-false-positives]
- **Suppressing low-confidence findings:** D1 explicitly states low-confidence findings are shown with a confidence label, not suppressed. Suppression is deferred to a separate decision gated by D3.
- **Using scipy/statsmodels for Wilson score:** These packages are not installed (verified) and add ~50MB dependency for a 15-line formula. Use the pure-math implementation.
- **Waiting for 30 days of data:** D4 eliminates the waiting period. Deploy immediately, mark dimensions "provisional" until 20+ data points.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Wilson score CI | scipy.stats.proportion_confint | 15-line pure-math formula (see Pattern 2) | scipy not installed; formula is well-known and trivial to implement [VERIFIED: scipy not available] |
| Git diff parsing | Custom diff parser | `git diff --name-only` + `git diff --stat` + `git diff -w` via subprocess | Already used in run_dry_run(); git provides all needed signals |
| Change type detection | AST-based analysis | `git diff -w` (whitespace) + regex on diff hunks for comment detection | Language-specific AST parsers are overkill; regex on diff output catches comment-only/whitespace-only reliably |
| JSON atomic write | Custom file locking | Existing `atomic_write()` in forge_cli.py | Already battle-tested pattern using tempfile + os.replace |
| Cost computation | Custom pricing model | Existing `calculate_cost()` in forge_cli.py | Already handles cache tokens and model-specific pricing |

**Key insight:** Phase 1b adds no external dependencies. Every computation uses Python stdlib + existing forge utilities. This is a calibration and routing phase, not a new infrastructure build.

## Common Pitfalls

### Pitfall 1: Small Sample Overconfidence
**What goes wrong:** Computing FP rate from 5 observations and treating it as reliable. Example: 1 FP in 5 findings = 20% FP rate -- but the true rate could be anywhere from 1% to 62% (Wilson 95% CI).
**Why it happens:** Temptation to act on early data.
**How to avoid:** Wilson score confidence intervals on all rate computations. D4's "provisional" label until 20+ data points. Never trigger rule improvement (D3) with fewer than 20 observations.
**Warning signs:** A dimension with <20 decided findings showing extreme FP rates (0% or 50%+). These are noise, not signal.

### Pitfall 2: Tier Classification Gaming
**What goes wrong:** LLM learns to perform shallower analysis when given lighter tiers, or users learn to force step0-only to skip review.
**Why it happens:** LLM sees "light review" and cuts corners; users discover `--step0` bypasses meaningful review.
**How to avoid:** D2's anti-gaming design: (1) Classification in Python before LLM invocation, (2) LLM has no tier awareness, (3) 10% audit sampling validates lighter tiers. `--step0` rejected for critical file patterns.
**Warning signs:** Light-tier runs missing P0/P1 findings that full-tier audit catches. Track this in audit results.

### Pitfall 3: ToolFP vs UserFP Conflation
**What goes wrong:** Treating all rejections as FP and concluding the tool is broken. Categories 5-6 (STYLE_PREFERENCE, ACCEPTABLE_RISK) are not tool errors -- the tool correctly identified an issue that the user chose not to fix.
**Why it happens:** Aggregate FP rate combines both types. Historical bootstrap data has ~28% global FP rate, but much of that may be UserFP.
**How to avoid:** Always compute and display ToolFP and UserFP separately. D3's rule improvement flow only triggers on ToolFP > 10%. The existing `show_stats()` already splits these (verified in codebase).
**Warning signs:** High overall FP rate but low ToolFP rate. This means the tool is accurate but users disagree on severity -- different intervention needed.

### Pitfall 4: Config.json Schema Bloat
**What goes wrong:** Adding every tier weight, criticality pattern, and evaluation threshold to config.json, making it unmanageable.
**Why it happens:** config.json is currently 17 lines (pricing only). Phase 1b adds tier weights, criticality patterns, and evaluation config.
**How to avoid:** Group related config under clear top-level keys: `tier_classification`, `evaluation`, `confidence`. Document each field. Keep config minimal -- hardcode defaults, config overrides only.
**Warning signs:** config.json exceeding 100 lines. Consider splitting into separate config files if this happens (but don't pre-optimize).

### Pitfall 5: Comment Detection False Positives
**What goes wrong:** Classifying a change as "comment-only" when it modifies a docstring that affects runtime behavior (e.g., Python `__doc__` used programmatically, or structured comments like `@param` in Javadoc).
**Why it happens:** Naive regex treats all `#` and `//` lines as comments.
**How to avoid:** Conservative classification: when in doubt, classify as "code change" not "comment only." Only classify as comment-only when ALL changed lines match comment patterns AND no code lines are modified. For Python, exclude docstrings (triple-quoted strings) from comment-only classification.
**Warning signs:** step0-only tier applied to a change that modifies docstrings or structured comments.

### Pitfall 6: Audit Sampling Bias
**What goes wrong:** 10% audit sampling produces biased results because the sample is not representative.
**Why it happens:** Random seed not set, or audit only runs on certain days/types of changes.
**How to avoid:** Use `random.random() < 0.10` per invocation with no persistent seed. Log every audit decision (was_audited: true/false) in run sidecar. Periodically check that audit rate is actually ~10%.
**Warning signs:** Audit findings rate significantly different from baseline (e.g., 0% missed findings in audits -- either tier classification is perfect or audits are not exercising the right cases).

## Code Examples

Verified patterns from existing codebase:

### Extending findings.json Schema (confidence_signals)

```python
# Source: D1 decision + existing finding persistence in SKILL.md
# The finding persistence heredoc in SKILL.md needs these NEW fields:

# In the finding append block, add after 'cost_tokens':
data['findings'].append({
    # ... existing fields unchanged ...
    'confidence': 0.72,           # NEW: computed score (0-1)
    'confidence_signals': {       # NEW: raw signal data
        'dimension_fp_rate': 0.15,
        'pass_agreement': 1.0,    # 1.0 = all passes agree
        'evidence_count': 3,      # lines of evidence cited
        'llm_self_report': 0.8,   # LLM's own confidence
    },
})
```

### Change Type Detection via git diff

```python
# Source: existing run_dry_run() pattern in forge_cli.py

def _detect_change_type(diff_spec, files):
    """Detect if change is comment-only, whitespace-only, or code.

    Uses git diff flags to separate whitespace from code changes,
    then regex on diff hunks for comment detection.
    """
    import subprocess

    # Check whitespace-only: git diff -w shows diff ignoring whitespace
    # If -w produces empty diff, all changes are whitespace-only
    r = subprocess.run(
        ['git', 'diff', '-w', '--stat', diff_spec],
        capture_output=True, text=True, timeout=10, check=False,
    )
    if r.returncode == 0 and not r.stdout.strip():
        return 'whitespace_only'

    # Check comment-only: get actual diff hunks, check if all
    # added/removed lines are comments
    r = subprocess.run(
        ['git', 'diff', '-U0', diff_spec],
        capture_output=True, text=True, timeout=10, check=False,
    )
    if r.returncode != 0:
        return 'code'  # conservative fallback

    # Language-aware comment patterns
    comment_patterns = {
        '.py': r'^\s*#',
        '.sh': r'^\s*#',
        '.bash': r'^\s*#',
        '.js': r'^\s*//',
        '.ts': r'^\s*//',
        '.c': r'^\s*(?://|\*)',
        '.go': r'^\s*//',
    }

    for line in r.stdout.splitlines():
        if not line.startswith('+') and not line.startswith('-'):
            continue
        if line.startswith('+++') or line.startswith('---'):
            continue
        content = line[1:]  # strip +/- prefix
        if not content.strip():
            continue  # blank line change

        # Check against comment patterns for relevant file types
        is_comment = False
        for ext, pattern in comment_patterns.items():
            if any(f.endswith(ext) for f in files):
                if re.match(pattern, content):
                    is_comment = True
                    break
        if not is_comment:
            return 'code'

    return 'comment_only'
```

### Evaluation Report (Tricorder 4 Criteria -- D5)

```python
# Source: D5 decision in CONTEXT.md

def evaluate_dimensions(findings, min_observations=20):
    """Evaluate all dimensions against Tricorder's 4 quality criteria.

    Returns dict of {dimension: {criterion: pass/fail, details}}.
    """
    dims = _aggregate_by_dimension(findings)
    report = {}

    for dim, dim_findings in dims.items():
        decided = [f for f in dim_findings
                   if f['outcome'] in ('accepted', 'rejected')]
        total = len(decided)

        report[dim] = {
            'total_observations': total,
            'provisional': total < min_observations,
        }

        if total < min_observations:
            report[dim]['criteria'] = {
                'understandable': 'insufficient data',
                'actionable': 'insufficient data',
                'fp_rate': 'insufficient data',
                'significant_impact': 'insufficient data',
            }
            continue

        # Criterion 3: <10% ToolFP rate
        tool_errors = len([f for f in decided
                          if f.get('reject_reason') in TOOL_ERROR_REASONS])
        tool_fp_rate = tool_errors / total
        lower, upper = wilson_score_interval(tool_errors, total)

        # Criterion 4: Significant impact (accepted / decided)
        accepted = len([f for f in decided if f['outcome'] == 'accepted'])
        acceptance_rate = accepted / total

        report[dim]['criteria'] = {
            'understandable': 'manual review required',
            'actionable': 'manual review required',
            'fp_rate': {
                'pass': tool_fp_rate <= 0.10,
                'rate': tool_fp_rate,
                'ci_lower': lower,
                'ci_upper': upper,
                'action': 'trigger D3 rule improvement'
                          if tool_fp_rate > 0.10 else 'none',
            },
            'significant_impact': {
                'pass': acceptance_rate >= 0.50,
                'acceptance_rate': acceptance_rate,
                'action': 'review dimension scope'
                          if acceptance_rate < 0.50 else 'none',
            },
        }

    return report
```

### Tier Routing in run_forge() (D2)

```python
# Source: D2 decision + existing run_forge() in forge_cli.py

def run_forge(diff_spec, override_tier=None):
    """Invoke claude -p with tier-appropriate scope.

    Tier classification happens HERE, before claude invocation.
    LLM receives "execute full review" or "execute light review",
    never knows other tiers exist.
    """
    tier = classify_change(diff_spec, override=override_tier)

    # Audit sampling: 10% chance of upgrading light -> full
    was_audited = False
    if tier == 'light' and random.random() < 0.10:
        was_audited = True
        tier = 'full'  # silently upgrade for comparison

    if tier == 'step0':
        run_dry_run(diff_spec)
        # Record run with tier metadata
        _record_run(diff_spec, tier='step0', cost=0.0)
        return

    # Adjust prompt based on tier
    if tier == 'light':
        prompt = (
            f"Run a focused forge review on the git diff: {diff_spec}. "
            "Execute Step 0 checks, then run one cycle of three passes "
            "(qodo-review, code-review-expert, adversarial-qe). "
            "Skip Step 4 smoke test."
        )
    else:  # full
        prompt = (
            f"Run the full forge review pipeline on the git diff: "
            f"{diff_spec}. Follow the complete 5-step pipeline "
            f"in your system prompt."
        )

    # ... rest of existing run_forge() logic ...
    # Record run with tier and audit metadata
    _record_run(diff_spec, tier=tier, was_audited=was_audited)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Threshold-based FP suppression | Rule improvement (fix prompts, not thresholds) | SonarQube 15-year evolution, FSE 2025 | 3.2% FP rate at 137M issues; suppressions accumulate debt (50.8% useless per FSE 2025) |
| One-size-fits-all review depth | Tiered review (full/light/step0) | Chromium CQ model, 2024+ commercial tools | Cost reduction on trivial changes without sacrificing security review |
| Manual FP triage only | Pattern-based FP learning (Semgrep Memories) | Semgrep 2025 | 60% automated triage, 96% researcher agreement -- but too complex for forge v1.1 (deferred) |
| Simple accept/reject count | Wilson score confidence intervals | Statistical standard since 1927 | Honest uncertainty quantification with small samples |
| Fixed severity model | Progressive confidence scoring | BitsAI-CR two-stage model (2025) | Confidence grows with data; avoids false precision from uncalibrated weights |

**Deprecated/outdated:**
- ROADMAP's "50% effectiveness" threshold: Replaced by D5's 10% ToolFP threshold with Tricorder 4 criteria. 50% was too permissive -- industry standard is 10%.
- ROADMAP's "30-day data collection prerequisite": Replaced by D4's Tricorder mode. Deploy immediately, accumulate data in production.
- `LEARN-07-FULL` as "automatic suppression thresholds": Redefined by D3 to mean "FP data drives rule improvement," not suppression.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Stage 3 composite weights (0.35/0.25/0.20/0.20) will produce useful confidence scores | Architecture Patterns - Pattern 3 | Low -- weights are initial values explicitly designed to be tuned with data regression at 300+ findings |
| A2 | Comment-only detection via regex on diff hunks is sufficient (no AST needed) | Code Examples - Change Type Detection | Medium -- edge cases with docstrings or structured comments could misclassify; mitigated by conservative default to 'code' |
| A3 | 10% audit sampling rate is sufficient to validate lighter tiers | Architecture Patterns - Pattern 1 | Low -- standard audit sampling rate; can be adjusted via config.json |
| A4 | AI-generated flag can be detected from diff content (comment markers, patterns) | Architecture Patterns - Pattern 1 | Medium -- no reliable automated detection exists; may need to rely on CLI flag (--ai-generated) or CODEOWNERS-style metadata |
| A5 | Python 3.14.4 math.sqrt is sufficient precision for Wilson score computation | Standard Stack | Negligible -- double-precision float is standard for statistical computations at this scale |

## Open Questions

1. **AI-Generated Code Detection**
   - What we know: D2 specifies AI-generated flag as a classification signal with high weight. 29-45% of AI-generated code contains security vulnerabilities. [CITED: Veracode GenAI Code Security Report]
   - What's unclear: How to reliably detect whether a diff contains AI-generated code automatically. No standardized markers exist.
   - Recommendation: Start with a CLI flag (`--ai-generated`). Consider heuristics later (e.g., presence of AI-assistant comment markers like `// Generated by Copilot`, commit messages with Co-Authored-By AI lines). Do not attempt automated detection in Phase 1b.

2. **Tricorder Criteria 1 and 2 (Understandable, Actionable)**
   - What we know: These are qualitative criteria that Tricorder assessed through developer surveys and "Not Useful" click analysis.
   - What's unclear: How to automate assessment. Current findings.json does not capture "time to understand" or "fix guidance quality."
   - Recommendation: Mark criteria 1 and 2 as "manual review required" in the evaluation report. They can be assessed during `forge --classify` by adding optional quick-feedback fields, but this is Claude's discretion territory.

3. **Bootstrap Data Quality**
   - What we know: ~53 historical data points from Phase 1a bootstrap. D4 says treat equally with structured data. Manual extraction quality was verified.
   - What's unclear: Whether historical data has the same reject_reason distribution as structured data will have.
   - Recommendation: Include bootstrap data in all calculations. Track `commit_sha == 'historical'` separately in evaluation reports to detect if bootstrap data skews results.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3 | All Phase 1b code | Yes | 3.14.4 | -- |
| git | Diff parsing, tier classification | Yes | (system) | -- |
| jq | Hook JSON processing | Yes | 1.8.1 | -- |
| shellcheck | Step 0 checks (existing) | Yes | 0.11.0 | -- |
| pylint | Step 0 checks (existing) | Yes | 4.0.5 | -- |
| scipy | Wilson score (optional) | No | -- | Pure-math implementation (Pattern 2) |
| statsmodels | proportion_confint (optional) | No | -- | Pure-math implementation (Pattern 2) |

**Missing dependencies with no fallback:** None.

**Missing dependencies with fallback:**
- scipy/statsmodels: Not needed. Wilson score formula is 15 lines of Python with `math.sqrt`. [VERIFIED: pip3 show returns nothing for both]

## Project Constraints (from CLAUDE.md)

The following CLAUDE.md directives apply to Phase 1b implementation:

- **All .md files in English** -- applies to any SKILL.md modifications for rule improvement (D3)
- **No non-ASCII in code** -- Step 0c check applies to all new Python code in forge_cli.py
- **Worktree for all edits** -- Phase 0 worktree creation is mandatory before any changes
- **Three-cycle review before commit** -- all code changes to forge_cli.py, SKILL.md, config.json must pass the forge pipeline itself
- **Author: Minxi Hou <houminxi@gmail.com>** -- no AI co-author lines
- **Commit format** -- `<subsystem>/<case>: <brief summary>` with Signed-off-by
- **List alternatives before implementing** -- applies to each Phase 1b design decision (tier weight values, config schema, etc.)
- **No background git push**
- **Verify no out-of-scope files modified** after automated tools

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | -- |
| V3 Session Management | No | -- |
| V4 Access Control | Yes (tier classification) | Deterministic Python, override only escalates |
| V5 Input Validation | Yes (diff_spec, config) | Validate diff_spec format; validate config.json schema |
| V6 Cryptography | No | -- |

### Known Threat Patterns for Phase 1b

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| LLM gaming tier classification | Tampering | D2: classification in Python before LLM invocation; LLM has no tier awareness |
| User downgrading security-critical review | Elevation of Privilege | D2: --step0 rejected for critical file patterns; override only escalates |
| FP data poisoning (mass reject to inflate FP rate) | Tampering | D3: minimum 20 observations + Wilson CI width check |
| Config.json tampering to lower criticality patterns | Tampering | Config changes go through forge review pipeline (dogfooding) |

## Sources

### Primary (HIGH confidence)
- forge_cli.py source code -- verified line counts, function signatures, existing patterns (922 lines) [VERIFIED: local codebase read]
- SKILL.md source code -- verified finding persistence schema, state machine, severity normalization (770 lines) [VERIFIED: local codebase read]
- check_review_tracker.sh -- verified _max_severity(), _has_findings(), session state (442 lines) [VERIFIED: local codebase read]
- config.json -- verified pricing config structure (17 lines) [VERIFIED: local codebase read]
- convert_historical.py -- verified bootstrap data conversion logic (381 lines) [VERIFIED: local codebase read]
- 01b-CONTEXT.md -- D1-D5 decisions, all 5 validated against research [VERIFIED: local file read]
- 01a-CONTEXT.md -- D1-D10 Phase 1a decisions that Phase 1b builds on [VERIFIED: local file read]
- P3-THRESHOLD-RESEARCH.md -- density-based escalation research [VERIFIED: local file read]
- Python 3.14.4 availability [VERIFIED: `python3 --version`]
- scipy/statsmodels NOT installed [VERIFIED: `pip3 show` returns empty]

### Secondary (MEDIUM confidence)
- [SonarQube FP minimization](https://www.sonarsource.com/blog/how-sonarqube-minimizes-false-positives) -- 3.2% FP rate at 137M issues, rule improvement approach [CITED: WebSearch verified against official Sonar blog]
- [Wilson score interval](https://www.statsmodels.org/stable/generated/statsmodels.stats.proportion.proportion_confint.html) -- formula verified against statsmodels documentation [CITED: statsmodels official docs]
- [Veracode GenAI Code Security Report](https://www.veracode.com/blog/genai-code-security-report/) -- 45% AI-generated code contains vulnerabilities, Java worst at 72% [CITED: WebSearch verified against Veracode official blog]
- [Google Tricorder ICSE 2015](https://www.cs.umd.edu/class/spring2019/cmsc414/papers/tricorder-building-a-program-analysis-ecosystem.pdf) -- 4 quality criteria, Not Useful rate monitoring, 93K findings/day [CITED: academic paper]
- [BitsAI-CR FSE 2025](https://arxiv.org/abs/2501.15134) -- Two-stage RuleChecker+ReviewFilter, Outdated Rate metric, 12K WAU [CITED: academic paper]
- [FSE 2025 Suppression Study](https://www.researchgate.net/publication/388422164_BitsAI-CR_Automated_Code_Review_via_LLM_in_Practice) -- 50.8% useless suppressions [CITED: academic paper]
- [Semgrep Memories](https://semgrep.dev/blog/2025/making-zero-false-positive-sast-a-reality-with-ai-powered-memory/) -- pattern-based FP learning, 60% automated triage [CITED: Semgrep official blog]

### Tertiary (LOW confidence)
- Stage 3 composite weights (0.35/0.25/0.20/0.20) -- initial values based on training knowledge of multi-signal scoring systems; no specific source validates these exact numbers [ASSUMED]
- AI-generated code detection heuristics -- no standardized approach exists; CLI flag is safest starting point [ASSUMED]

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all stdlib, no new dependencies, verified against environment
- Architecture: HIGH -- extends existing codebase with well-understood patterns; all decisions locked
- Pitfalls: HIGH -- grounded in codebase analysis, research literature, and existing P3-THRESHOLD-RESEARCH.md

**Research date:** 2026-05-12
**Valid until:** 2026-06-12 (30 days -- stable domain, no fast-moving dependencies)
