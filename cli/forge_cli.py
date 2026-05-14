#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Forge CLI wrapper -- standalone code review outside Claude Code.

Invokes Claude Code in headless mode (-p) with forge SKILL.md as system prompt.
Supports full review, dry-run (Step 0 only, zero LLM cost), FP dashboard, and
data bootstrap.

Usage:
    forge <diff-spec>               # Full review
    forge --dry-run <diff-spec>     # Step 0 only, zero LLM cost (direct Python)
    forge --stats                   # FP rate dashboard
    forge --stats --json            # Machine-readable dashboard
    forge --bootstrap <file>        # Load historical FP data
    forge --classify                # Classify pending findings

Design decisions:
- --dry-run runs Step 0 checks directly in Python (bash -n, shellcheck,
  pylint, non-ASCII grep). Does NOT invoke claude -p. Zero LLM cost.
  (Addresses review issue #1)
- Run metadata written to .forge/runs/<uuid>.json sidecar, NOT to
  findings.json directly. SKILL.md writes findings during review;
  CLI reads findings.json read-only after claude -p finishes.
  (Addresses review issue #2)
- --append-system-prompt-file has timeout fallback to --system-prompt
  inline if hanging detected. (Addresses review issue #3)

Per D7: This is a wrapper that invokes 'claude -p', NOT a standalone
reimplementation. The review value is in Claude's multi-pass convergence.

Architecture: forge_cli.py is the CLI entry point. Functions are grouped by
step in the review pipeline (Step 0: lint, Step 1-3: review, Step 4: smoke).
Subcommand handlers (cmd_*) are dispatched from _dispatch().
Future: extract TierClassifier, ReviewOrchestrator, StatsReporter classes.
"""

import argparse
import glob
import json
import math
import os
import random
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone

from file_utils import atomic_write, validate_diff_spec

try:
    import yaml
except ImportError:
    yaml = None  # Custom rules disabled if PyYAML missing

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FORGE_SKILL = os.path.join(SCRIPT_DIR, '..', 'skills', 'forge', 'SKILL.md')
CONFIG_FILE = os.path.join(SCRIPT_DIR, 'config.json')
FINDINGS_FILE = '.forge/findings.json'
RUNS_DIR = '.forge/runs'

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

# Default minimum observations before acting on FP rate (D3/D4).
# Prefer config.get('evaluation', {}).get('min_observations', 20)
# over this constant. Kept for backward compatibility only.
_DEFAULT_MIN_OBSERVATIONS = 20


# ---------------------------------------------------------------------------
# Subprocess wrappers (dependency inversion for testability)
# ---------------------------------------------------------------------------
# Error handling policy: all subprocess calls use check=False and inspect
# returncode manually. Timeouts are set per-call (10s for git, 30s for
# linters, 600s for claude). Exceptions caught: OSError (binary not found),
# subprocess.TimeoutExpired, subprocess.SubprocessError. Never shell=True.

def _run_git(args, timeout=30):
    """Run git command, return CompletedProcess or None on failure."""
    try:
        return subprocess.run(
            ['git'] + args,
            capture_output=True, text=True, timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired,
            subprocess.SubprocessError):
        return None


def _run_tool(args, timeout=30):
    """Run an optional external tool, return CompletedProcess or None.

    Unlike _run_git, also catches FileNotFoundError so callers do
    not need to handle missing optional tools (shellcheck, ruff, etc.).
    """
    try:
        return subprocess.run(
            args,
            capture_output=True, text=True, timeout=timeout,
            check=False,
        )
    except (OSError, FileNotFoundError,
            subprocess.TimeoutExpired,
            subprocess.SubprocessError):
        return None


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

_config_cache = None


def load_config():
    """Load cli/config.json pricing config (cached).

    Returns dict with 'pricing' and 'default_model' keys.
    Exits with error if config file is missing.
    Result is cached at module level; call reload_config() to clear.
    """
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    config_path = os.path.realpath(CONFIG_FILE)
    if not os.path.isfile(config_path):
        print(
            f"Error: config.json not found at {config_path}",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError) as exc:
        print(
            f"Error: failed to load config.json: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)
    # M12: validate required keys
    for key in ('pricing', 'default_model'):
        if key not in data:
            print(
                f"Error: config.json missing required key '{key}'",
                file=sys.stderr,
            )
            sys.exit(1)
    _config_cache = data
    return data


def reload_config():
    """Clear config cache so next load_config() re-reads from disk."""
    global _config_cache
    _config_cache = None


def load_findings():
    """Load .forge/findings.json.

    Returns dict with 'version', 'findings', 'runs' keys.
    Returns empty structure if file is missing or corrupted.
    """
    try:
        with open(FINDINGS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, IOError):
        return {'version': 1, 'findings': [], 'runs': []}
    # M13: validate structure
    if not isinstance(data, dict):
        return {'version': 1, 'findings': [], 'runs': []}
    if not isinstance(data.get('findings'), list):
        data['findings'] = []
    return data


def load_all_runs():
    """Load all run records from .forge/runs/*.json sidecar files.

    Returns list of run dicts sorted by filename (chronological by UUID
    generation order). Creates RUNS_DIR if it does not exist.
    """
    os.makedirs(RUNS_DIR, exist_ok=True)
    runs = []
    for run_file in sorted(glob.glob(os.path.join(RUNS_DIR, '*.json'))):
        try:
            with open(run_file, 'r', encoding='utf-8') as f:
                runs.append(json.load(f))
        except (json.JSONDecodeError, IOError):
            continue
    return runs


# ---------------------------------------------------------------------------
# Statistical Utilities (Wilson score, data aggregation)
# ---------------------------------------------------------------------------

def wilson_score_interval(successes, total, confidence=0.95):
    """Compute Wilson score confidence interval for a proportion.

    Pure function -- no side effects.

    Args:
        successes: number of FP findings (tool-error rejections).
        total: total decided findings.
        confidence: confidence level (default 0.95).

    Returns:
        tuple: (lower, upper) bounds of the FP rate estimate.
    """
    if total == 0:
        return (0.0, 1.0)
    if successes < 0 or total < 0:
        return (0.0, 1.0)
    if successes > total:
        successes = total

    p = successes / total
    # z-score lookup for common confidence levels
    z_table = {0.90: 1.645, 0.95: 1.96, 0.99: 2.576}
    z = z_table.get(confidence, 1.96)
    z2 = z * z

    denominator = 1 + z2 / total
    centre = (p + z2 / (2 * total)) / denominator
    margin = z * math.sqrt(
        (p * (1 - p) + z2 / (4 * total)) / total
    ) / denominator

    return (max(0.0, centre - margin), min(1.0, centre + margin))


# ---------------------------------------------------------------------------
# Confidence Scoring (D1 -- progressive multi-signal formula)
# ---------------------------------------------------------------------------

def compute_confidence(dimension_fp_rate, pass_agreement=1.0,
                       evidence_count=1, llm_self_report=0.8,
                       total_findings=0):
    """Compute confidence score for a finding.

    Progressive: uses more signals as data volume grows.
    Stage determination uses total_findings which is the per-dimension
    decided count, NOT global (M5).

    Args:
        dimension_fp_rate: FP rate for the finding's dimension (0-1).
        pass_agreement: fraction of passes that flagged the same location.
        evidence_count: number of evidence locations cited.
        llm_self_report: LLM's stated confidence (0-1).
        total_findings: per-dimension decided count (NOT global).

    Returns:
        float: confidence score clamped to [0.0, 1.0].
    """
    for val in (dimension_fp_rate, pass_agreement, llm_self_report):
        if not math.isfinite(val):
            return 0.0

    if total_findings < 100:
        # Stage 1: only reliable signal is dimension FP rate
        return max(0.0, min(1.0, 1.0 - dimension_fp_rate))

    if total_findings < 300:
        # Stage 2: add pass_agreement weight
        w_fp = 0.6
        w_agree = 0.4
        return max(0.0, min(1.0,
            w_fp * (1.0 - dimension_fp_rate)
            + w_agree * pass_agreement
        ))

    # Stage 3: full composite with all 4 signals
    w_fp = 0.35
    w_agree = 0.25
    w_evidence = 0.20
    w_llm = 0.20
    evidence_score = min(1.0, evidence_count / 5.0)
    return max(0.0, min(1.0,
        w_fp * (1.0 - dimension_fp_rate)
        + w_agree * pass_agreement
        + w_evidence * evidence_score
        + w_llm * llm_self_report
    ))


def backfill_confidence(findings_data):
    """Backfill confidence scores for all findings.

    Computes per-dimension FP rates from decided findings, derives
    pass_agreement from multi-pass location grouping (M1), and calls
    compute_confidence with per-dimension decided count (M5).

    Args:
        findings_data: dict as returned by load_findings().

    Returns:
        dict: the same findings_data, mutated in place with confidence
        fields set on each finding.
    """
    findings = findings_data.get('findings', [])
    if not findings:
        return findings_data

    # Step 1: Compute per-dimension decided counts and FP rates
    dim_stats = {}
    for f in findings:
        dim = f.get('dimension', 'unknown')
        if dim not in dim_stats:
            dim_stats[dim] = {'decided': 0, 'tool_errors': 0}
        if f.get('outcome') in ('accepted', 'rejected'):
            dim_stats[dim]['decided'] += 1
            if f.get('reject_reason') in TOOL_ERROR_REASONS:
                dim_stats[dim]['tool_errors'] += 1

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

    # Step 3: For each finding, compute confidence
    for f in findings:
        dim = f.get('dimension', 'unknown')
        stats = dim_stats.get(dim, {'decided': 0, 'tool_errors': 0})
        decided = stats['decided']
        if decided == 0:
            f['confidence'] = 0.0
            continue
        dim_fp_rate = stats['tool_errors'] / decided

        # Compute pass_agreement from location group
        loc_key = (
            f.get('file', ''),
            f.get('line', -1),
            f.get('dimension', ''),
        )
        passes_at_loc = location_groups.get(loc_key, {1})
        num_passes = len(passes_at_loc)
        # Default total passes in the 3-pass pipeline
        total_passes_in_run = 3
        pass_agreement = num_passes / total_passes_in_run

        # Use existing confidence_signals if present and non-default
        signals = f.get('confidence_signals', {})
        existing_agreement = signals.get('pass_agreement')
        if (existing_agreement is not None
                and existing_agreement != 1.0):
            pass_agreement = existing_agreement

        evidence_count = signals.get('evidence_count', 1)
        llm_self_report = signals.get('llm_self_report', 0.8)

        f['confidence'] = compute_confidence(
            dim_fp_rate, pass_agreement, evidence_count,
            llm_self_report, decided,
        )

    return findings_data


# ---------------------------------------------------------------------------
# Tier Classification (D2 -- deterministic, before LLM invocation)
# ---------------------------------------------------------------------------

def _get_changed_files(diff_spec):
    """Get list of changed file paths from a git diff spec.

    Runs git diff --name-only via _run_git. Returns None on error
    so classify_change can distinguish "no files" from "git failed".

    Args:
        diff_spec: git diff specification (e.g., HEAD~1, main..feature).

    Returns:
        list or None: file paths (stripped, non-empty), None on error.
    """
    result = _run_git(['diff', '--name-only', diff_spec], timeout=10)
    if result is None:
        print(
            f"Error: failed to get changed files for "
            f"'{diff_spec}'",
            file=sys.stderr,
        )
        return None
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


def _count_diff_lines(diff_spec):
    """Count total changed lines (added + deleted) from a git diff spec.

    Uses git diff --numstat for fixed-format, locale-independent output
    (M7). Skips binary files (added/deleted shown as '-').

    Args:
        diff_spec: git diff specification.

    Returns:
        int or None: total changed lines. Returns None on error.
        Caller should treat None as conservative (full tier).
    """
    result = _run_git(['diff', '--numstat', diff_spec], timeout=10)
    if result is None or result.returncode != 0:
        return None
    total = 0
    for line in result.stdout.strip().split('\n'):
        if not line.strip():
            continue
        parts = line.split('\t')
        if len(parts) < 3:
            continue
        added, deleted = parts[0], parts[1]
        # Skip binary files (shown as '-')
        if added == '-' or deleted == '-':
            continue
        try:
            total += int(added) + int(deleted)
        except ValueError:
            continue
    return total


def _detect_change_type(diff_spec, files):
    """Detect if change is whitespace-only, comment-only, or code.

    Step 1: git diff -w --stat detects whitespace-only changes.
    Step 2: regex on diff hunks detects comment-only changes.
    Conservative: any non-comment changed line returns 'code'.
    Python docstrings (triple-quoted strings) are NOT comments.

    Args:
        diff_spec: git diff specification.
        files: list of changed file paths.

    Returns:
        str: one of 'whitespace_only', 'comment_only', 'code'.
    """
    # Step 1: Check whitespace-only
    r = _run_git(['diff', '-w', '--stat', diff_spec], timeout=10)
    if r is None:
        return 'code'
    if r.returncode == 0 and not r.stdout.strip():
        return 'whitespace_only'

    # Step 2: Check comment-only via diff hunks
    r = _run_git(['diff', '-U0', diff_spec], timeout=10)
    if r is None or r.returncode != 0:
        return 'code'

    # Language-aware comment patterns
    comment_patterns = {
        '.py': r'^\s*#',
        '.sh': r'^\s*#',
        '.bash': r'^\s*#',
        '.js': r'^\s*//',
        '.ts': r'^\s*//',
        '.go': r'^\s*//',
        '.c': r'^\s*(?://|\*)',
        '.h': r'^\s*(?://|\*)',
        '.md': r'^\s*(?:<!--|-->|$)',
    }

    # Build set of relevant extensions from changed files
    relevant_exts = set()
    for fpath in files:
        _, ext = os.path.splitext(fpath)
        if ext in comment_patterns:
            relevant_exts.add(ext)

    has_content_lines = False
    for line in r.stdout.splitlines():
        if not line.startswith('+') and not line.startswith('-'):
            continue
        if line.startswith('+++') or line.startswith('---'):
            continue
        content = line[1:]  # strip +/- prefix
        if not content.strip():
            continue  # blank line change

        has_content_lines = True

        # Check against comment patterns for relevant file types
        is_comment = False
        for ext in relevant_exts:
            pattern = comment_patterns[ext]
            if re.match(pattern, content):
                is_comment = True
                break

        if not is_comment:
            return 'code'

    if not has_content_lines:
        return 'whitespace_only'

    return 'comment_only'


def _has_critical_files(files, config=None):
    """Check if any changed files match critical file patterns.

    Critical files always route to full review regardless of override
    (D2 anti-gaming). Uses patterns from config with hardcoded defaults.

    Args:
        files: list of changed file paths.
        config: config dict (optional, uses defaults if missing).

    Returns:
        bool: True if any file matches a critical pattern.
    """
    if config is None:
        config = {}

    default_patterns = [
        r'(?:auth|security|crypto|secret|token|password|credential)',
        r'(?:hooks/check_)',
        r'(?:SKILL\.md)',
    ]
    patterns = config.get(
        'tier_classification', {},
    ).get('critical_patterns', default_patterns)

    for fpath in files:
        for pattern in patterns:
            try:
                if re.search(pattern, fpath, re.IGNORECASE):
                    return True
            except re.error:
                continue
    return False


def _detect_ai_generated(diff_spec, config=None):
    """Detect if a change contains AI-generated code markers.

    Searches both diff content (added lines) and commit message for
    AI markers (M3). Heuristic detection -- users can also use --full
    for known AI code (Open Question 1).

    Args:
        diff_spec: git diff specification.
        config: config dict (optional, uses defaults if missing).

    Returns:
        bool: True if any AI marker found.
    """
    if config is None:
        config = {}

    default_markers = ['Generated by', 'Co-Authored-By']
    markers = config.get(
        'tier_classification', {},
    ).get('ai_markers', default_markers)

    # Search diff content (added lines only)
    result = _run_git(['diff', '-U0', diff_spec], timeout=10)
    if result is not None and result.returncode == 0:
        for line in result.stdout.splitlines():
            if not line.startswith('+'):
                continue
            if line.startswith('+++'):
                continue
            normalized = re.sub(r'\s+', ' ', line.lower())
            for marker in markers:
                if marker.lower() in normalized:
                    return True

    # Search commit message (M3)
    result = _run_git(['log', '-1', '--format=%B'], timeout=10)
    if result is not None and result.returncode == 0:
        msg = result.stdout.lower()
        for marker in markers:
            if marker.lower() in msg:
                return True

    return False


def classify_change(diff_spec, override=None, config=None):
    """Classify a change into full/light/step0 tier.

    Deterministic Python -- LLM never sees tier options. Override
    only escalates (per D2). Classification runs before LLM invocation.

    Args:
        diff_spec: git diff specification.
        override: 'full', 'step0', or None. 'full' always accepted;
            'step0' rejected for critical files (returns 'full').
        config: config dict (optional, loads from disk if None).

    Returns:
        str: one of 'full', 'light', 'step0'.
    """
    diff_spec = validate_diff_spec(diff_spec)

    if override == 'full':
        return 'full'

    if config is None:
        config = load_config()

    files = _get_changed_files(diff_spec)
    if files is None:
        return 'full'
    diff_lines = _count_diff_lines(diff_spec)
    if diff_lines is None:
        return 'full'  # conservative fallback on error
    change_type = _detect_change_type(diff_spec, files)
    is_critical = _has_critical_files(files, config)
    is_ai = _detect_ai_generated(diff_spec, config)

    # Priority 1: Critical files -- always full (cannot downgrade)
    if is_critical:
        return 'full'

    # Priority 2: AI-generated code -- minimum light tier
    if is_ai:
        if override == 'step0':
            return 'light'  # reject downgrade, enforce minimum
        if diff_lines > 50:
            return 'full'
        return 'light'

    # Priority 3: Comment-only or whitespace-only -- step0
    if change_type in ('comment_only', 'whitespace_only'):
        return 'step0'

    # Priority 4: Small non-critical changes -- light
    small_threshold = config.get(
        'tier_classification', {},
    ).get('small_diff_threshold', 10)
    if diff_lines < small_threshold:
        return 'light'

    # Default: full (conservative until audit data validates)
    return 'full'


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

    input_tokens = usage.get('input_tokens', 0)
    output_tokens = usage.get('output_tokens', 0)
    cache_read = usage.get('cache_read_input_tokens', 0)
    cache_creation = usage.get('cache_creation_input_tokens', 0)

    # Pricing is per million tokens
    cost = (
        input_tokens * pricing.get('input_per_mtok', 0) / 1_000_000
        + output_tokens * pricing.get('output_per_mtok', 0) / 1_000_000
        + cache_read * pricing.get('cache_read_per_mtok', 0) / 1_000_000
        + cache_creation
        * pricing.get('cache_creation_per_mtok', 0)
        / 1_000_000
    )
    return cost


def _get_commit_sha():
    """Get short git SHA of HEAD via _run_git.

    Uses _run_git (NOT shell substitution) per
    SKILL.md finding persistence pattern.
    """
    result = _run_git(
        ['rev-parse', '--short', 'HEAD'], timeout=10,
    )
    if result is not None and result.returncode == 0:
        return result.stdout.strip()
    return 'unknown'


# ---------------------------------------------------------------------------
# Evaluation and Recommendation (D3, D5 -- rule improvement pipeline)
# ---------------------------------------------------------------------------

def evaluate_dimensions(findings, config_override=None, json_format=False,
                        include_shadow=False):
    """Evaluate all 12 dimensions against Tricorder 4 quality criteria (D5).

    Computes per-dimension ToolFP rate with Wilson score confidence
    intervals. Dimensions with <20 observations are marked provisional.
    Criteria 1 (Understandable) and 2 (Actionable) require manual review.

    Args:
        findings: list of finding dicts from findings.json.
        config_override: config dict (optional, loads from disk if None).
        json_format: if True, output JSON instead of terminal table.
        include_shadow: if True, include shadow dimension findings.

    Returns:
        dict: report keyed by dimension name.
    """
    # R3 fix: preserve config param. Shadow filter per R7/Pitfall 3
    if not include_shadow:
        findings = [
            f for f in findings
            if not f.get('shadow', False)
        ]

    config = config_override if config_override is not None else load_config()

    min_obs = config.get(
        'evaluation', {},
    ).get('min_observations', _DEFAULT_MIN_OBSERVATIONS)
    fp_threshold = config.get(
        'evaluation', {},
    ).get('fp_rate_threshold', 0.10)
    confidence_level = config.get(
        'evaluation', {},
    ).get('confidence_level', 0.95)

    # Group findings by dimension
    dims = {}
    for f in findings:
        dim = f.get('dimension', 'unknown')
        if dim not in dims:
            dims[dim] = []
        dims[dim].append(f)

    report = {}
    for dim in sorted(dims.keys()):
        dim_findings = dims[dim]
        decided = [
            f for f in dim_findings
            if f.get('outcome') in ('accepted', 'rejected')
        ]
        total_decided = len(decided)
        provisional = total_decided < min_obs

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

        tool_errors = sum(
            1 for f in decided
            if f.get('reject_reason') in TOOL_ERROR_REASONS
        )
        tool_fp_rate = tool_errors / total_decided
        lower, upper = wilson_score_interval(
            tool_errors, total_decided, confidence_level,
        )

        accepted_count = sum(
            1 for f in decided if f.get('outcome') == 'accepted'
        )
        acceptance_rate = accepted_count / total_decided

        user_prefs = sum(
            1 for f in decided
            if f.get('reject_reason') in USER_PREF_REASONS
        )
        user_fp_rate = user_prefs / total_decided

        fp_pass = tool_fp_rate <= fp_threshold
        impact_pass = acceptance_rate >= 0.50

        report[dim] = {
            'total_observations': total_decided,
            'provisional': False,
            'criteria': {
                'understandable': 'manual review required',
                'actionable': 'manual review required',
                'fp_rate': {
                    'pass': fp_pass,
                    'rate': tool_fp_rate,
                    'ci_lower': lower,
                    'ci_upper': upper,
                    'action': (
                        'trigger D3 rule improvement'
                        if not fp_pass else 'none'
                    ),
                },
                'significant_impact': {
                    'pass': impact_pass,
                    'acceptance_rate': acceptance_rate,
                    'action': (
                        'review dimension scope'
                        if not impact_pass else 'none'
                    ),
                },
            },
            'tool_fp_rate': tool_fp_rate,
            'user_fp_rate': user_fp_rate,
        }

    if json_format:
        print(json.dumps(report, indent=2))
        return report

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

    for dim in sorted(report.keys()):
        r = report[dim]
        obs = r['total_observations']
        if r['provisional']:
            print(
                f"{dim:<18} {obs:>4} {'YES':>5} "
                f"{'--':>8} {'--':>16} "
                f"{'--':>8} {'provisional':>14}"
            )
        else:
            fp_info = r['criteria']['fp_rate']
            impact_info = r['criteria']['significant_impact']
            fp_pct = f"{fp_info['rate'] * 100:.1f}%"
            ci_str = (
                f"[{fp_info['ci_lower'] * 100:4.1f}%,"
                f"{fp_info['ci_upper'] * 100:5.1f}%]"
            )
            impact_pct = (
                f"{impact_info['acceptance_rate'] * 100:.1f}%"
            )
            if not fp_info['pass']:
                status = 'FAIL: FP rate'
            elif not impact_info['pass']:
                status = 'FAIL: impact'
            else:
                status = 'PASS'
            print(
                f"{dim:<18} {obs:>4} {'no':>5} "
                f"{fp_pct:>8} {ci_str:>16} "
                f"{impact_pct:>8} {status:>14}"
            )

    print("-" * 82)
    print()
    print("Legend:")
    print(
        "  Obs = decided findings | "
        "Prov = provisional (<20 obs)"
    )
    print(
        "  ToolFP% = categories 1-4 rate | "
        "CI = Wilson 95% confidence interval"
    )
    print(
        "  Impact% = acceptance rate | "
        "Status = PASS / FAIL / provisional"
    )
    print()
    print(
        "Criteria 1 (Understandable) and "
        "2 (Actionable): manual review required"
    )

    return report


def generate_recommendation(dimension, findings, config=None):
    """Generate rule improvement recommendation for a dimension (D3).

    Analyzes ToolFP data to produce specific SKILL.md improvement
    suggestions when ToolFP exceeds 10%. INTENTIONAL routes to
    improve_detection (H3 fix), not adjust_scope.

    Args:
        dimension: dimension name string.
        findings: list of all finding dicts (will be filtered).
        config: config dict (optional, loads from disk if None).

    Returns:
        dict: recommendation with action and suggestion, or None
        if insufficient data or within threshold.
    """
    if config is None:
        config = load_config()

    min_obs = config.get(
        'evaluation', {},
    ).get('min_observations', _DEFAULT_MIN_OBSERVATIONS)
    fp_threshold = config.get(
        'evaluation', {},
    ).get('fp_rate_threshold', 0.10)

    decided = [
        f for f in findings
        if (f.get('dimension') == dimension
            and f.get('outcome') in ('accepted', 'rejected'))
    ]
    if len(decided) < min_obs:
        return None

    tool_errors = [
        f for f in decided
        if f.get('reject_reason') in TOOL_ERROR_REASONS
    ]
    tool_fp_rate = len(tool_errors) / len(decided)
    if tool_fp_rate <= fp_threshold:
        return None

    # Count each reject reason among tool errors
    reason_counts = {}
    for f in tool_errors:
        reason = f.get('reject_reason', 'UNKNOWN')
        reason_counts[reason] = reason_counts.get(reason, 0) + 1

    dominant_reason = max(reason_counts, key=reason_counts.get)

    # H3: INTENTIONAL is a tool-wrong category (cat 1-4).
    # Tool flagged something done intentionally -- detection
    # needs to be smarter, not scope narrowing.
    improve_reasons = {
        'HALLUCINATION', 'CONTEXT_MISSING',
        'NOT_APPLICABLE', 'INTENTIONAL',
    }
    if dominant_reason in improve_reasons:
        action = 'improve_detection'
        total_errors = len(tool_errors)
        count = reason_counts[dominant_reason]
        suggestion = (
            f"Modify SKILL.md prompt for '{dimension}' dimension: "
            f"dominant FP cause is {dominant_reason} "
            f"({count}/{total_errors} tool errors). "
            f"Add explicit negative examples or context "
            f"requirements to the dimension definition."
        )
    else:
        action = 'adjust_scope'
        suggestion = (
            f"Consider narrowing scope of '{dimension}' "
            f"dimension: dominant FP cause is "
            f"{dominant_reason}. Users consistently reject "
            f"findings in this category."
        )

    return {
        'dimension': dimension,
        'tool_fp_rate': tool_fp_rate,
        'total_decided': len(decided),
        'dominant_reason': dominant_reason,
        'reason_breakdown': reason_counts,
        'action': action,
        'suggestion': suggestion,
    }


def show_recommendations(json_format=False):
    """Display rule improvement recommendations for all dimensions.

    Orchestrator that calls generate_recommendation for every
    dimension and displays results in terminal table or JSON.

    Args:
        json_format: if True, output JSON instead of terminal table.
    """
    data = load_findings()
    findings = data.get('findings', [])
    # R7 fix: exclude shadow findings from recommendations
    findings = [
        f for f in findings
        if not f.get('shadow', False)
    ]

    # Group by dimension
    dims = {}
    for f in findings:
        dim = f.get('dimension', 'unknown')
        if dim not in dims:
            dims[dim] = []
        dims[dim].append(f)

    recommendations = []
    for dim in sorted(dims.keys()):
        rec = generate_recommendation(dim, findings)
        if rec is not None:
            recommendations.append(rec)

    if json_format:
        print(json.dumps(recommendations, indent=2))
        return

    if not recommendations:
        print(
            "No dimensions exceed the 10% ToolFP threshold "
            "(or insufficient data)."
        )
        return

    print("=" * 82)
    print("Forge Rule Improvement Recommendations (D3)")
    print("=" * 82)
    print()
    header = (
        f"{'Dimension':<18} {'ToolFP%':>8} "
        f"{'Dominant Reason':<20} {'Action':<20} {'Decided':>7}"
    )
    print(header)
    print("-" * 82)

    for rec in recommendations:
        fp_pct = f"{rec['tool_fp_rate'] * 100:.1f}%"
        print(
            f"{rec['dimension']:<18} {fp_pct:>8} "
            f"{rec['dominant_reason']:<20} "
            f"{rec['action']:<20} "
            f"{rec['total_decided']:>7}"
        )

    print("-" * 82)
    print()
    print("Detailed Recommendations:")
    print("-" * 82)

    for i, rec in enumerate(recommendations, 1):
        fp_pct = f"{rec['tool_fp_rate'] * 100:.1f}%"
        print()
        print(
            f"{i}. {rec['dimension']} "
            f"(ToolFP: {fp_pct}, "
            f"{rec['total_decided']} decided findings)"
        )
        print()

        # Dominant cause line
        total_errors = sum(rec['reason_breakdown'].values())
        dom_count = rec['reason_breakdown'][rec['dominant_reason']]
        print(
            f"   Dominant cause: {rec['dominant_reason']} "
            f"({dom_count}/{total_errors} tool errors)"
        )

        # Breakdown
        breakdown_parts = [
            f"{r}={c}"
            for r, c in sorted(rec['reason_breakdown'].items())
        ]
        print(f"   Breakdown: {', '.join(breakdown_parts)}")
        print()
        print(f"   Suggestion: {rec['suggestion']}")

    print()
    print("-" * 82)


# ---------------------------------------------------------------------------
# Co-location Analysis  (DIM-06 data-driven merging)
# ---------------------------------------------------------------------------


def compute_colocation_matrix(findings):
    """Build co-location matrix from findings data.

    Groups findings by (file, line) and counts dimension pairs that
    share the same location. Returns dict of {(dim1, dim2): count}
    with dimensions in sorted order (dim1 < dim2).

    Per D3: only findings with valid file and line coordinates are
    included. Shadow findings are excluded.
    """
    from itertools import combinations

    # Filter: exclude shadow, require valid coordinates
    valid = [
        f for f in findings
        if not f.get('shadow', False)
        and f.get('file') not in (None, '', 'unknown')
        and f.get('line', -1) != -1
    ]

    # Group by (file, line) -> set of dimensions
    locations = {}
    for f in valid:
        key = (f['file'], f['line'])
        if key not in locations:
            locations[key] = set()
        locations[key].add(f.get('dimension', 'unknown'))

    # Count co-location pairs
    colocation = {}
    for loc, dims in locations.items():
        if len(dims) < 2:
            continue
        for d1, d2 in combinations(sorted(dims), 2):
            pair = (d1, d2)
            colocation[pair] = colocation.get(pair, 0) + 1

    return colocation


def _dimension_finding_counts(findings):
    """Count total findings per dimension (non-shadow only)."""
    counts = {}
    for f in findings:
        if f.get('shadow', False):
            continue
        dim = f.get('dimension', 'unknown')
        counts[dim] = counts.get(dim, 0) + 1
    return counts


def show_colocation(json_format=False):
    """Display co-location analysis and merge recommendations.

    R12 fix: reports BOTH directional rates (d1->d2 and d2->d1).
    Merge candidate requires BOTH rates above threshold, not just min().
    Per D3: merge candidates when co-location rate > 30% AND
    20+ co-located findings per pair.
    """
    data = load_findings()
    findings = data.get('findings', [])
    config = load_config()
    min_coloc = config.get(
        'colocation', {},
    ).get('min_colocation_findings', 20)
    merge_threshold = config.get(
        'colocation', {},
    ).get('merge_threshold', 0.30)

    matrix = compute_colocation_matrix(findings)
    dim_counts = _dimension_finding_counts(findings)

    if json_format:
        result = {
            'colocation_pairs': {},
            'merge_candidates': [],
            'insufficient_data': [],
        }
        for pair, count in sorted(
            matrix.items(), key=lambda x: -x[1],
        ):
            d1, d2 = pair
            d1_count = dim_counts.get(d1, 0)
            d2_count = dim_counts.get(d2, 0)
            # R12 fix: compute BOTH directional rates
            rate_d1 = count / d1_count if d1_count > 0 else 0
            rate_d2 = count / d2_count if d2_count > 0 else 0
            pair_key = f"{d1} + {d2}"
            result['colocation_pairs'][pair_key] = {
                'count': count,
                'rate_d1_to_d2': round(rate_d1, 3),
                'rate_d2_to_d1': round(rate_d2, 3),
            }
            # R12 fix: require BOTH rates above threshold
            if (count >= min_coloc
                    and rate_d1 >= merge_threshold
                    and rate_d2 >= merge_threshold):
                result['merge_candidates'].append(pair_key)
            elif count < min_coloc:
                result['insufficient_data'].append(pair_key)
        print(json.dumps(result, indent=2))
        return

    # Terminal display
    print("=" * 90)
    print("Dimension Co-location Analysis (DIM-06)")
    print("=" * 90)

    if not matrix:
        print()
        print(
            "  No co-located findings found. Co-location analysis "
            "requires findings with valid (file, line) coordinates."
        )
        print(
            "  Current findings may lack coordinates "
            "(historical bootstrap data)."
        )
        print()
        return

    print()
    # R12 fix: show both directional rates
    header = (
        f"{'Dimension Pair':<32} "
        f"{'Co-loc':>6} "
        f"{'d1->d2':>7} "
        f"{'d2->d1':>7} "
        f"{'Status':>18}"
    )
    print(header)
    print("-" * 90)

    merge_candidates = []
    for pair, count in sorted(
        matrix.items(), key=lambda x: -x[1],
    ):
        d1, d2 = pair
        d1_count = dim_counts.get(d1, 0)
        d2_count = dim_counts.get(d2, 0)
        # R12 fix: both directional rates
        rate_d1 = count / d1_count if d1_count > 0 else 0
        rate_d2 = count / d2_count if d2_count > 0 else 0
        pair_label = f"{d1} + {d2}"

        if count < min_coloc:
            status = "insufficient data"
        elif (rate_d1 >= merge_threshold
                and rate_d2 >= merge_threshold):
            status = "MERGE CANDIDATE"
            merge_candidates.append(
                (pair_label, count, rate_d1, rate_d2),
            )
        else:
            status = "below threshold"

        print(
            f"  {pair_label:<30} "
            f"{count:>6} "
            f"{rate_d1:>6.1%} "
            f"{rate_d2:>6.1%} "
            f"{status:>18}"
        )

    print("-" * 90)
    print()

    if merge_candidates:
        print("Merge Recommendations:")
        for label, count, r1, r2 in merge_candidates:
            print(
                f"  * {label}: {count} co-located "
                f"({r1:.1%} / {r2:.1%}) -- review for merge"
            )
        print()
        print(
            "  To merge: update SKILL.md dimension list "
            "and submit PR for review."
        )
    else:
        print(
            "  No merge candidates. Either insufficient data "
            f"(need {min_coloc}+ co-located findings per pair) "
            f"or co-location rate below {merge_threshold:.0%} "
            f"in both directions (R12)."
        )
    print()


def promote_shadow_dimension(dimension_name):
    """Deprecated: use dimension_manager.promote_dimension() instead.

    This stub exists only for backward compatibility. All promote
    logic now lives in cli/dimension_manager.py which uses
    dimension_states instead of the legacy promoted_dimensions list.
    """
    from dimension_manager import promote_dimension
    promote_dimension(dimension_name)


# ---------------------------------------------------------------------------
# Step 0b: Complexity checks  (DIM-03 deterministic)
# ---------------------------------------------------------------------------


def _check_python_complexity(filepath, threshold, findings_list):
    """Check Python function cyclomatic complexity via radon.

    Gracefully skips if radon is not installed (ImportError).
    Uses same 4-tuple finding format as run_dry_run().
    """
    try:
        from radon.complexity import cc_visit, cc_rank
    except ImportError:
        return 0  # radon not installed, skip silently
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            code = f.read()
    except (IOError, UnicodeDecodeError):
        return 0
    count = 0
    for block in cc_visit(code):
        if block.complexity >= threshold:
            findings_list.append((
                'complexity', filepath, 'radon',
                f'{block.name}() CC={block.complexity} '
                f'(rank {cc_rank(block.complexity)}, '
                f'threshold {threshold})',
            ))
            count += 1
    return count


def _check_shell_function_length(filepath, threshold, findings_list):
    """Check shell function lengths against line-count threshold.

    R4 fix: detects both 'name() {' and 'function name {' (no parens).
    R13 fix: skips brace counting inside heredoc bodies.
    Uses brace-depth tracking for nested blocks.
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except (IOError, UnicodeDecodeError):
        return 0
    # R4 fix: two patterns for both bash function syntaxes
    # Pattern 1: name() { ... (with optional 'function' keyword)
    func_paren = re.compile(
        r'^\s*(?:function\s+)?(\w[\w-]*)\s*\(\s*\)\s*\{?\s*$'
    )
    # Pattern 2: function name { ... (no parens, requires 'function')
    func_keyword = re.compile(
        r'^\s*function\s+(\w[\w-]*)\s*\{?\s*$'
    )
    # R13 fix: heredoc delimiter detection
    heredoc_pattern = re.compile(
        r'<<-?\s*[\\]?[\'"]?(\w+)[\'"]?\s*$'
    )
    brace_depth = 0
    current_func = None
    func_start = 0
    count = 0
    heredoc_delim = None  # R13: tracks active heredoc delimiter
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        # R13 fix: if inside heredoc, skip until delimiter
        if heredoc_delim is not None:
            if stripped == heredoc_delim:
                heredoc_delim = None
            continue
        # R13 fix: detect heredoc start
        hm = heredoc_pattern.search(stripped)
        if hm:
            heredoc_delim = hm.group(1)
            # Do not count braces on this line either
            continue
        # Detect function start (try both patterns)
        m = func_paren.match(stripped)
        if not m:
            m = func_keyword.match(stripped)  # R4 fix
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
                        f'(threshold {threshold})',
                    ))
                    count += 1
                current_func = None
                brace_depth = 0
    return count


# ---------------------------------------------------------------------------
# Custom Rules Loader  (DIM-07)
# ---------------------------------------------------------------------------

VALID_SEVERITIES = {'critical', 'high', 'medium', 'low'}


def _parse_rule_file(filepath):
    """Parse a single rule file with YAML frontmatter + Markdown body.

    Returns dict with 'metadata' and 'body' keys, or None if invalid.
    Format per CONTEXT.md D4: YAML between --- delimiters, Markdown after.
    """
    if yaml is None:
        return None
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except (IOError, UnicodeDecodeError) as exc:
        print(
            f"Warning: cannot read rule file {filepath}: {exc}",
            file=sys.stderr,
        )
        return None
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
    except yaml.YAMLError as exc:
        print(
            f"Error: invalid YAML in {filepath}: {exc}",
            file=sys.stderr,
        )
        return None
    if not isinstance(metadata, dict):
        print(
            f"Warning: {filepath} frontmatter is not a mapping, skipping",
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
    sev = str(metadata['severity']).lower()
    if sev not in VALID_SEVERITIES:
        print(
            f"Warning: rule '{metadata['name']}' has unknown "
            f"severity '{sev}', defaulting to 'medium'",
            file=sys.stderr,
        )
        metadata['severity'] = 'medium'
    else:
        metadata['severity'] = sev
    # R11 fix: normalize scope to list if user wrote a string
    if 'scope' in metadata:
        scope = metadata['scope']
        if isinstance(scope, str):
            metadata['scope'] = [scope]
    return {'metadata': metadata, 'body': body, 'source': filepath}


def load_custom_rules(project_root='.', config=None):
    """Load custom rules from forge-rules.md and .forge/rules/*.md.

    Loading order per D4:
    1. forge-rules.md (single file at project root)
    2. .forge/rules/*.md (multi-file directory)
    Both can coexist; duplicate names rejected with fatal error.

    Returns list of active (enabled) rule dicts sorted by severity
    (critical first). Caps total chars at config limit.
    """
    if yaml is None:
        return []
    if config is None:
        config = {}
    max_rules = config.get(
        'custom_rules', {},
    ).get('max_rules', 20)
    max_chars = config.get(
        'custom_rules', {},
    ).get('max_total_chars', 15000)

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
        for path in sorted(glob.glob(
            os.path.join(rules_dir, '*.md'),
        )):
            rule = _parse_rule_file(path)
            if rule:
                name = rule['metadata']['name']
                if name in seen_names:
                    print(
                        f"Error: duplicate rule name '{name}' "
                        f"in {path} (already loaded from "
                        f"another file)",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                rules.append(rule)
                seen_names.add(name)

    # 3. Filter disabled rules
    active = [
        r for r in rules
        if r['metadata'].get('enabled', True)
    ]

    # 4. Sort by severity (critical first)
    severity_order = {
        'critical': 0, 'high': 1, 'medium': 2, 'low': 3,
    }
    active.sort(
        key=lambda r: severity_order.get(
            r['metadata']['severity'], 99,
        ),
    )

    # 5. Cap total injection size
    total_chars = 0
    capped = []
    for r in active[:max_rules]:
        total_chars += len(r['body'])
        if total_chars > max_chars:
            print(
                f"Warning: custom rules truncated at "
                f"{len(capped)} rules ({max_chars} char limit)",
                file=sys.stderr,
            )
            break
        capped.append(r)

    return capped


def format_rules_for_prompt(rules):
    """Format loaded rules as a prompt section for LLM injection.

    Returns a string to append to the LLM review prompt, or empty
    string if no rules.
    """
    if not rules:
        return ''
    lines = [
        '',
        '## Project-Specific Review Rules',
        '',
        'The following project-specific rules MUST be checked '
        'in addition to the standard dimensions:',
        '',
    ]
    for r in rules:
        meta = r['metadata']
        lines.append(
            f"### Rule: {meta['name']} "
            f"(severity: {meta['severity']})"
        )
        if meta.get('dimension'):
            lines.append(
                f"Dimension: {meta['dimension']}"
            )
        if meta.get('scope'):
            # R11 fix: scope is guaranteed list by _parse_rule_file
            scope_str = ', '.join(meta['scope'])
            lines.append(f"Applies to: {scope_str}")
        lines.append('')
        lines.append(r['body'])
        lines.append('')
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Core: run_dry_run  (review issue #1 -- zero LLM cost)
# ---------------------------------------------------------------------------

def run_dry_run(diff_spec):
    """Run Step 0 checks directly in Python. Zero LLM cost.

    Addresses review issue #1: --dry-run must not invoke claude -p.
    Runs: bash -n, shellcheck, pylint/ruff, non-ASCII grep.
    """
    diff_spec = validate_diff_spec(diff_spec)
    print("forge: dry-run mode (Step 0 only -- syntax + complexity + non-ASCII, zero LLM cost)")
    print(f"forge: diff spec: {diff_spec}")

    # Get list of changed files from diff spec
    result = _run_git(
        ['diff', '--name-only', diff_spec], timeout=10,
    )
    if result is None or result.returncode != 0:
        result = _run_git(
            ['diff', '--name-only', diff_spec, '--'], timeout=10,
        )
    if result is None:
        print(
            f"Error: failed to get diff files for '{diff_spec}'",
            file=sys.stderr,
        )
        sys.exit(1)
    if result.returncode != 0:
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

    if not changed_files:
        print("forge: no changed files found")
        return

    print(f"forge: {len(changed_files)} files to check")
    findings = []
    total_issues = 0
    config = load_config()  # R10 fix: hoist outside loop

    for filepath in changed_files:
        if not os.path.isfile(filepath):
            continue  # deleted file, skip

        # Step 0a: Syntax check
        if filepath.endswith('.sh') or filepath.endswith('.bash'):
            # bash -n
            r = _run_tool(
                ['bash', '-n', filepath], timeout=30,
            )
            if r is not None and r.returncode != 0:
                findings.append(
                    ('syntax', filepath, 'bash -n', r.stderr.strip())
                )
                total_issues += 1

            # shellcheck (optional tool)
            r = _run_tool(
                ['shellcheck', filepath], timeout=30,
            )
            if r is not None and r.returncode != 0:
                for line in r.stdout.strip().split('\n'):
                    if line.strip():
                        findings.append(
                            ('lint', filepath, 'shellcheck',
                             line.strip())
                        )
                        total_issues += 1

        elif filepath.endswith('.py'):
            # python3 -m py_compile
            r = _run_tool(
                [sys.executable, '-m', 'py_compile', filepath],
                timeout=30,
            )
            if r is not None and r.returncode != 0:
                findings.append(
                    ('syntax', filepath, 'py_compile', r.stderr.strip())
                )
                total_issues += 1

            # pylint or ruff (use first available)
            for linter in ['ruff check', 'pylint --enable=W,C']:
                linter_parts = linter.split()
                r = _run_tool(
                    linter_parts + [filepath], timeout=60,
                )
                if r is None:
                    continue  # tool not installed, try next
                if r.returncode != 0 and r.stdout.strip():
                    lines = r.stdout.strip().split('\n')
                    total_issues += len(lines)
                    for line in lines[:5]:
                        findings.append(
                            ('lint', filepath, linter_parts[0],
                             line.strip())
                        )
                    if len(lines) > 5:
                        findings.append(
                            ('lint', filepath, linter_parts[0],
                             f'... and {len(lines) - 5} more issues')
                        )
                break  # use first available linter

        # Step 0b: Complexity check (DIM-03 deterministic)
        if filepath.endswith('.py'):
            cc_threshold = config.get(
                'complexity', {},
            ).get('python_cc_threshold', 15)
            total_issues += _check_python_complexity(
                filepath, cc_threshold, findings,
            )
        elif filepath.endswith('.sh') or filepath.endswith('.bash'):
            shell_threshold = config.get(
                'complexity', {},
            ).get('shell_line_threshold', 80)
            total_issues += _check_shell_function_length(
                filepath, shell_threshold, findings,
            )

        # Step 0c: Non-ASCII check (all file types)
        r = _run_tool(
            ['grep', '-Pn', '[^\\x00-\\x7F]', filepath],
            timeout=10,
        )
        if r is not None and r.returncode == 0 and r.stdout.strip():
            for line in r.stdout.strip().split('\n')[:3]:
                findings.append(
                    ('non-ascii', filepath, 'grep', line.strip())
                )
                total_issues += 1

    # Report results
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


# ---------------------------------------------------------------------------
# Core: _invoke_claude  (review issue #3 -- fallback)
# ---------------------------------------------------------------------------

def _invoke_claude(cmd, skill_path, prompt):
    """Invoke claude -p with timeout and fallback.

    First tries --append-system-prompt-file. If it times out (hangs),
    falls back to --system-prompt with SKILL.md content inline.
    Addresses review issue #3.

    Args:
        cmd: list of command-line arguments for first attempt.
        skill_path: absolute path to SKILL.md.
        prompt: the review prompt string.

    Returns:
        Parsed JSON list from claude output, or None on failure.
    """
    # First attempt: --append-system-prompt-file
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
        # Fallback: read SKILL.md and pass as --system-prompt
        try:
            with open(skill_path, 'r', encoding='utf-8') as f:
                skill_content = f.read()
        except IOError as exc:
            print(
                f"Error: cannot read SKILL.md for fallback: {exc}",
                file=sys.stderr,
            )
            return None

        fallback_cmd = [
            'claude', '-p', prompt,
            '--system-prompt', skill_content,
            '--output-format', 'json',
            '--allowedTools', 'Bash,Read,Edit,Write,Grep,Glob',
        ]
        try:
            result = subprocess.run(
                fallback_cmd, capture_output=True, text=True, timeout=600,
                check=False,
            )
        except subprocess.TimeoutExpired:
            print(
                "Error: fallback also timed out after 600s",
                file=sys.stderr,
            )
            return None
    except FileNotFoundError:
        print(
            "Error: 'claude' command not found. "
            "Install Claude Code CLI first.",
            file=sys.stderr,
        )
        return None

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
        print("Raw output (first 500 chars):", file=sys.stderr)
        print(result.stdout[:500], file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Core: run_forge  (review issues #2, #3, #13, #14)
# ---------------------------------------------------------------------------

def run_forge(diff_spec, override_tier=None):
    """Invoke claude -p with tier-appropriate forge review scope.

    Tier classification happens HERE, before claude invocation. LLM
    receives tier-appropriate prompt text without knowing other tiers
    exist (D2 anti-gaming). 10% audit sampling silently upgrades
    light to full for validation.

    Per D7: wrapper invokes 'claude -p', not standalone reimplementation.
    Writes run metadata to .forge/runs/<uuid>.json sidecar (review issue #2).
    Falls back to --system-prompt inline if --append-system-prompt-file
    hangs (review issue #3).

    Args:
        diff_spec: git diff specification (e.g., HEAD~1, main..feature).
        override_tier: 'full', 'step0', or None. Passed to classify_change.
    """
    diff_spec = validate_diff_spec(diff_spec)

    skill_path = os.path.realpath(FORGE_SKILL)
    if not os.path.isfile(skill_path):
        print(
            f"Error: SKILL.md not found at {skill_path}",
            file=sys.stderr,
        )
        sys.exit(1)

    config = load_config()
    tier = classify_change(diff_spec, override=override_tier, config=config)

    # Audit sampling: 10% chance of upgrading light -> full (D2)
    was_audited = False
    audit_rate = config.get(
        'tier_classification', {},
    ).get('audit_rate', 0.10)
    if tier == 'light' and random.random() < audit_rate:
        was_audited = True
        tier = 'full'

    # step0-only: delegate to run_dry_run and return
    if tier == 'step0':
        print("forge: tier classification: step0-only")
        run_dry_run(diff_spec)
        run_id = str(uuid.uuid4())
        run_record = {
            'id': run_id,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'commit_sha': _get_commit_sha(),
            'diff_spec': diff_spec,
            'dry_run': True,
            'tier': 'step0',
            'was_audited': False,
            'total_passes': 0,
            'total_cost_usd': 0.0,
            'total_tokens': {'input': 0, 'output': 0},
            'outcome': 'completed',
        }
        os.makedirs(RUNS_DIR, exist_ok=True)
        run_file = os.path.join(RUNS_DIR, f'{run_id}.json')
        atomic_write(run_file, run_record)
        return

    print(
        f"forge: tier classification: {tier}"
        + (" (audit)" if was_audited else "")
    )

    # Tier-aware prompt (M2: light says what to do, never what is skipped)
    if tier == 'light':
        prompt = (
            f"Run a focused forge review on the git diff: {diff_spec}. "
            "Run Step 0 checks, then run one cycle of passes 1-3 "
            "(qodo-review, code-review-expert, adversarial-qe)."
        )
    else:
        prompt = (
            f"Run the full forge review pipeline on the git diff: "
            f"{diff_spec}. Follow the complete 5-step pipeline "
            f"in your system prompt."
        )

    # R1 fix: Load and inject custom rules BEFORE cmd construction.
    # Python strings are immutable -- cmd captures prompt by value,
    # so rules must be appended to prompt BEFORE cmd = [...].
    # N3 note: `config` is already loaded at line 1334 (tier classification).
    # No need to call load_config() again here.
    custom_rules = load_custom_rules(
        project_root='.', config=config,
    )
    rules_prompt = format_rules_for_prompt(custom_rules)
    if rules_prompt:
        prompt = prompt + rules_prompt
        print(
            f"forge: {len(custom_rules)} custom rule(s) loaded",
        )

    # Try --append-system-prompt-file first (review issue #3: with fallback)
    cmd = [
        'claude', '-p', prompt,
        '--append-system-prompt-file', skill_path,
        '--output-format', 'json',
        '--allowedTools', 'Bash,Read,Edit,Write,Grep,Glob',
    ]

    print("forge: invoking claude -p (full review)...")
    print(f"forge: diff spec: {diff_spec}")

    result_data = _invoke_claude(cmd, skill_path, prompt)
    if result_data is None:
        sys.exit(1)

    if not isinstance(result_data, list):
        print(
            "Error: claude output is not a JSON array",
            file=sys.stderr,
        )
        sys.exit(1)

    # Extract result item with cost data
    result_item = None
    for item in result_data:
        if isinstance(item, dict) and item.get('type') == 'result':
            result_item = item
            break

    if result_item is None:
        print(
            "Warning: no result item found in claude output",
            file=sys.stderr,
        )
        return

    # Extract token usage and cost
    usage = result_item.get('usage', {})
    cost_usd = result_item.get('total_cost_usd')
    input_tokens = usage.get('input_tokens', 0)
    output_tokens = usage.get('output_tokens', 0)

    # Calculate cost (review issue #13: 'is not None', not truthiness)
    calculated_cost = calculate_cost(usage, config)
    final_cost = cost_usd if cost_usd is not None else calculated_cost

    # Count actual passes from findings.json (review issue #14)
    findings_data = load_findings()
    findings = findings_data.get('findings', [])
    commit_sha = _get_commit_sha()
    # Count findings from this commit as proxy for passes completed
    this_run_findings = [
        f for f in findings if f.get('commit_sha') == commit_sha
    ]
    # Count unique (cycle, pass) pairs as actual passes
    pass_set = set()
    for f in this_run_findings:
        pass_set.add((f.get('cycle', 0), f.get('pass', 0)))
    actual_passes = len(pass_set) if pass_set else 0

    # Write run metadata to sidecar file (review issue #2)
    run_id = str(uuid.uuid4())
    run_record = {
        'id': run_id,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'commit_sha': commit_sha,
        'diff_spec': diff_spec,
        'dry_run': False,
        'tier': tier,
        'was_audited': was_audited,
        'total_passes': actual_passes,
        'total_cost_usd': final_cost,
        'total_tokens': {
            'input': input_tokens,
            'output': output_tokens,
        },
        'outcome': 'completed',
    }

    os.makedirs(RUNS_DIR, exist_ok=True)
    run_file = os.path.join(RUNS_DIR, f'{run_id}.json')
    atomic_write(run_file, run_record)

    # Backfill confidence scores for all findings using updated FP data (H1)
    findings_data = backfill_confidence(findings_data)
    atomic_write(FINDINGS_FILE, findings_data)

    # Print cost summary
    print("\nforge: run complete")
    print(f"forge: passes detected: {actual_passes}")
    print(
        f"forge: tokens -- input: {input_tokens:,}, output: {output_tokens:,}"
    )
    if cost_usd is not None:
        reported = f"${cost_usd:.4f}"
    else:
        reported = "N/A"
    print(
        f"forge: cost -- {reported} (reported) / ${calculated_cost:.4f} (calculated)"
    )

    # Print assistant content
    for item in result_data:
        if isinstance(item, dict) and item.get('type') == 'assistant':
            content = item.get('content', '')
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        if block.get('type') == 'text':
                            print(block.get('text', ''))
            elif isinstance(content, str):
                print(content)

    # LEARN-10: increment run count and check escalation
    try:
        from escalation import increment_run_count, run_escalation_check
        increment_run_count()
        run_escalation_check()
    except Exception as exc:
        print(
            f"Warning: escalation check failed: {exc}",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# Core: show_stats  (review issue #8 -- split FP rates)
# ---------------------------------------------------------------------------

def show_stats(json_format=False, include_shadow=False):
    """Display FP rate dashboard from findings.json (TRUST-05).

    Addresses review issue #8: split into tool-error FP (categories 1-4)
    and user-preference FP (categories 5-6).

    Tool-error FP rate = rejected with cat 1-4 / total decided
    User-preference rate = rejected with cat 5-6 / total decided
    """
    findings_data = load_findings()
    findings = findings_data.get('findings', [])
    if not include_shadow:
        findings = [
            f for f in findings
            if not f.get('shadow', False)
        ]
    runs = load_all_runs()

    if not findings and not runs:
        print("No findings data yet. Run forge first.")
        return

    # Aggregate findings by dimension
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

        # Split rejections by category
        reason = f.get('reject_reason')
        if outcome == 'rejected' and reason:
            if reason in TOOL_ERROR_REASONS:
                dims[dim]['tool_error'] += 1
            elif reason in USER_PREF_REASONS:
                dims[dim]['user_pref'] += 1

    # Aggregate runs for cost summary
    total_cost = sum(r.get('total_cost_usd', 0) or 0 for r in runs)
    total_input = sum(
        r.get('total_tokens', {}).get('input', 0) for r in runs
    )
    total_output = sum(
        r.get('total_tokens', {}).get('output', 0) for r in runs
    )
    run_count = len(runs)

    if json_format:
        # Confidence distribution for JSON output
        conf_buckets = {
            '0.0-0.2': 0, '0.2-0.4': 0, '0.4-0.6': 0,
            '0.6-0.8': 0, '0.8-1.0': 0,
        }
        if findings:
            backfilled_j = backfill_confidence(
                {'findings': list(findings)},
            )
            for f in backfilled_j.get('findings', []):
                c = f.get('confidence', 0.0)
                if c < 0.2:
                    conf_buckets['0.0-0.2'] += 1
                elif c < 0.4:
                    conf_buckets['0.2-0.4'] += 1
                elif c < 0.6:
                    conf_buckets['0.4-0.6'] += 1
                elif c < 0.8:
                    conf_buckets['0.6-0.8'] += 1
                else:
                    conf_buckets['0.8-1.0'] += 1

        # Cost by tier for JSON output
        tier_summary = {}
        for r in runs:
            tier = r.get('tier', 'full')
            if tier not in tier_summary:
                tier_summary[tier] = {
                    'count': 0, 'total_cost': 0.0,
                }
            tier_summary[tier]['count'] += 1
            tier_summary[tier]['total_cost'] += (
                r.get('total_cost_usd', 0) or 0
            )
        for tier in tier_summary:
            td = tier_summary[tier]
            td['avg_cost'] = (
                td['total_cost'] / td['count']
                if td['count'] else 0
            )

        output = {
            'dimensions': dims,
            'runs': {
                'count': run_count,
                'total_cost_usd': total_cost,
                'total_tokens': {
                    'input': total_input,
                    'output': total_output,
                },
                'avg_cost_per_run': (
                    total_cost / run_count if run_count else 0
                ),
            },
            'findings_total': len(findings),
            'confidence_distribution': conf_buckets,
            'cost_by_tier': tier_summary,
        }
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

    t_accepted = 0
    t_rejected = 0
    t_pending = 0
    t_tool = 0
    t_user = 0

    for dim in sorted(dims.keys()):
        c = dims[dim]
        accepted = c.get('accepted', 0)
        rejected = c.get('rejected', 0)
        pending = c.get('pending', 0)
        tool_err = c.get('tool_error', 0)
        user_prf = c.get('user_pref', 0)

        t_accepted += accepted
        t_rejected += rejected
        t_pending += pending
        t_tool += tool_err
        t_user += user_prf

        decided = accepted + rejected
        # Tool-error FP rate (the actionable one for improving forge)
        if decided > 0:
            fp_pct = f"{tool_err / decided * 100:.0f}%"
        else:
            fp_pct = "N/A"
        print(
            f"{dim:<18} {accepted:>6} {rejected:>6} {pending:>5} "
            f"{tool_err:>7} {user_prf:>7} {fp_pct:>5}"
        )

    print("-" * 82)
    total_decided = t_accepted + t_rejected
    if total_decided > 0:
        total_fp = f"{t_tool / total_decided * 100:.0f}%"
    else:
        total_fp = "N/A"
    print(
        f"{'TOTAL':<18} {t_accepted:>6} {t_rejected:>6} {t_pending:>5} "
        f"{t_tool:>7} {t_user:>7} {total_fp:>5}"
    )

    # Legend
    print()
    print(
        "ToolFP = cat 1-4 "
        "(HALLUCINATION, CONTEXT_MISSING, INTENTIONAL, NOT_APPLICABLE)"
    )
    print("UserFP = cat 5-6 (STYLE_PREFERENCE, ACCEPTABLE_RISK)")
    print(
        "FP%    = ToolFP / (Accept + Reject) "
        "-- rate that measures tool quality"
    )

    # Cost summary
    print()
    print(f"{'Cost Summary':<22}")
    print("-" * 42)
    print(f"{'Total runs:':<22} {run_count:8d}")
    print(f"{'Total cost:':<22} ${total_cost:8.4f}")
    if run_count:
        avg = f"${total_cost / run_count:.4f}"
    else:
        avg = "N/A"
    print(f"{'Avg cost/run:':<22} {avg:>8}")
    print(
        f"{'Total input tokens:':<22} {total_input:>8,}"
    )
    print(
        f"{'Total output tokens:':<22} {total_output:>8,}"
    )

    # Confidence distribution (Plan 03 extension)
    if findings:
        backfilled = backfill_confidence(
            {'findings': list(findings)},
        )
        conf_findings = backfilled.get('findings', [])
        buckets = [0, 0, 0, 0, 0]
        bucket_labels = [
            '0.0-0.2', '0.2-0.4', '0.4-0.6',
            '0.6-0.8', '0.8-1.0',
        ]
        for f in conf_findings:
            c = f.get('confidence', 0.0)
            if c < 0.2:
                buckets[0] += 1
            elif c < 0.4:
                buckets[1] += 1
            elif c < 0.6:
                buckets[2] += 1
            elif c < 0.8:
                buckets[3] += 1
            else:
                buckets[4] += 1

        max_count = max(buckets) if buckets else 1
        bar_width = 12
        print()
        print("Confidence Distribution:")
        for label, count in zip(bucket_labels, buckets):
            if max_count > 0:
                bar_len = int(count / max_count * bar_width)
            else:
                bar_len = 0
            bar = '#' * bar_len
            print(
                f"  {label}  |{bar:<{bar_width}}| {count:>3}"
            )

    # Cost by tier (Plan 03 extension)
    if runs:
        tier_data = {}
        for r in runs:
            tier = r.get('tier', 'full')
            if tier not in tier_data:
                tier_data[tier] = {
                    'count': 0, 'total_cost': 0.0,
                }
            tier_data[tier]['count'] += 1
            tier_data[tier]['total_cost'] += (
                r.get('total_cost_usd', 0) or 0
            )

        print()
        print("Cost by Tier:")
        print(
            f"  {'Tier':<10} {'Runs':>5} "
            f"{'Total Cost':>12} {'Avg Cost':>10}"
        )
        for tier in sorted(tier_data.keys()):
            td = tier_data[tier]
            t_cost = td['total_cost']
            t_count = td['count']
            avg_cost = t_cost / t_count if t_count else 0
            print(
                f"  {tier:<10} {t_count:>5} "
                f"${t_cost:>10.4f} ${avg_cost:>9.4f}"
            )

    print("=" * 82)


# ---------------------------------------------------------------------------
# Core: bootstrap_historical
# ---------------------------------------------------------------------------

def bootstrap_historical(filepath):
    """Load historical FP data from analysis file.

    Delegates to bootstrap/convert_historical.py script created in Plan 02.
    """
    script = os.path.join(
        SCRIPT_DIR, '..', 'bootstrap', 'convert_historical.py',
    )
    script = os.path.realpath(script)
    if not os.path.isfile(script):
        print(
            f"Error: bootstrap script not found at {script}",
            file=sys.stderr,
        )
        sys.exit(1)
    result = subprocess.run(
        [sys.executable, script, filepath],
        capture_output=False, timeout=30, check=False,
    )
    sys.exit(result.returncode)


# ---------------------------------------------------------------------------
# Core: classify_findings
# ---------------------------------------------------------------------------

def classify_findings():
    """Interactively classify pending findings (accept/reject).

    Presents each pending finding and asks user to accept or reject.
    If rejected, asks for a reject reason from the 6-category taxonomy.
    Updates findings.json with the classification.
    """
    findings_data = load_findings()
    findings = findings_data.get('findings', [])
    pending = [
        (i, f) for i, f in enumerate(findings)
        if f.get('outcome') == 'pending'
    ]

    if not pending:
        print("No pending findings to classify.")
        return

    print(
        f"forge: {len(pending)} pending finding(s) to classify\n"
    )

    reasons_list = sorted(VALID_REJECT_REASONS)
    modified = False

    for seq, (idx, finding) in enumerate(pending, 1):
        print(
            f"--- Finding {seq}/{len(pending)} ---"
        )
        print(f"  File:      {finding.get('file', 'unknown')}")
        print(f"  Line:      {finding.get('line', -1)}")
        print(f"  Dimension: {finding.get('dimension', 'unknown')}")
        print(f"  Severity:  {finding.get('severity', 'unknown')}")
        print(f"  Pass:      {finding.get('pass', 0)}")
        print(f"  Cycle:     {finding.get('cycle', 0)}")
        print("  Description:")
        desc = finding.get('description', '')
        # Wrap long descriptions
        for line in desc.split('\n'):
            print(f"    {line}")
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
                findings[idx]['reject_reason'] = None
                modified = True
                print("  -> accepted\n")
                break
            if choice in ('r', 'reject'):
                print("  Reject reason:")
                for j, reason in enumerate(reasons_list, 1):
                    print(f"    {j}. {reason}")
                while True:
                    try:
                        num = input(f"  Select (1-{len(reasons_list)}): ")
                        num = int(num.strip())
                        if 1 <= num <= len(reasons_list):
                            findings[idx]['outcome'] = 'rejected'
                            findings[idx]['reject_reason'] = (
                                reasons_list[num - 1]
                            )
                            modified = True
                            print(
                                f"  -> rejected ({reasons_list[num - 1]})\n"
                            )
                            break
                        print("  Invalid selection.")
                    except (ValueError, EOFError, KeyboardInterrupt):
                        print()
                        break
                break
            if choice in ('s', 'skip'):
                print("  -> skipped\n")
                break
            if choice in ('q', 'quit'):
                if modified:
                    atomic_write(FINDINGS_FILE, findings_data)
                    count = sum(
                        1 for f in findings
                        if f.get('outcome') != 'pending'
                    )
                    print(
                        f"forge: saved classifications ({count} total decided)"
                    )
                return
            print("  Invalid choice. Use a/r/s/q.")

    if modified:
        atomic_write(FINDINGS_FILE, findings_data)
        print(
            "forge: classification complete -- all pending findings reviewed"
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    """Argparse entry point for forge CLI."""
    parser = argparse.ArgumentParser(
        prog='forge',
        description=(
            'Forge code review CLI '
            '-- standalone wrapper for Claude Code'
        ),
    )
    parser.add_argument(
        'diff_spec', nargs='?', default=None,
        help='git diff spec to review (e.g., HEAD~1, branch..main)',
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Run Step 0 only (syntax + lint + non-ASCII), zero LLM cost',
    )
    parser.add_argument(
        '--stats', action='store_true',
        help='Show FP rate dashboard from findings.json',
    )
    parser.add_argument(
        '--json', action='store_true',
        help='Output dashboard in JSON format (use with --stats)',
    )
    parser.add_argument(
        '--bootstrap', metavar='FILE',
        help='Load historical FP data from analysis file',
    )
    parser.add_argument(
        '--classify', action='store_true',
        help='Interactively classify pending findings (accept/reject)',
    )
    parser.add_argument(
        '--eval', action='store_true',
        help='Evaluate dimensions against Tricorder 4 criteria (D5)',
    )
    parser.add_argument(
        '--recommend', action='store_true',
        help='Generate rule improvement recommendations (D3)',
    )
    parser.add_argument(
        '--full', action='store_true',
        help='Force full review (override tier classification)',
    )
    parser.add_argument(
        '--step0', action='store_true',
        help='Force Step 0 only (rejected for critical files)',
    )
    parser.add_argument(
        '--colocation', action='store_true',
        help='Show dimension co-location analysis (DIM-06)',
    )
    parser.add_argument(
        '--shadow', action='store_true',
        help='Include shadow dimensions in --eval and --stats output',
    )
    parser.add_argument(
        '--promote', metavar='DIM',
        help='Promote shadow dimension to active (R6)',
    )
    parser.add_argument(
        '--learn', action='store_true',
        help=(
            'Ingest external review feedback '
            '(requires --pr, --branch, or --ci-file)'
        ),
    )
    parser.add_argument(
        '--pr', metavar='OWNER/REPO#N',
        help='GitHub PR to learn from (e.g., owner/repo#123)',
    )
    parser.add_argument(
        '--branch', metavar='NAME',
        help='Branch to scan for reverts/fixup!/squash!',
    )
    parser.add_argument(
        '--ci-file', metavar='PATH',
        help='CI log file to learn from',
    )
    parser.add_argument(
        '--gaps', action='store_true',
        help=(
            'Interactive gap management: staleness sweeps, '
            'expansion review, grouping'
        ),
    )
    parser.add_argument(
        '--approve-expansion', metavar='ID',
        help=(
            'Non-interactive: approve a single pending '
            'keyword expansion'
        ),
    )
    parser.add_argument(
        '--reclassify', nargs=2, metavar=('FINDING_ID', 'DIM'),
        help='Correct misclassification of an external finding',
    )
    parser.add_argument(
        '--propose', metavar='GROUP_ID',
        help='Generate proposal bundle from a gap group',
    )
    parser.add_argument(
        '--add-dimension', metavar='DIM',
        help='Register a new shadow dimension',
    )
    parser.add_argument(
        '--keywords-file', metavar='PATH',
        help='JSON file with keyword list (use with --add-dimension)',
    )
    parser.add_argument(
        '--retire', metavar='DIM',
        help='Archive a dimension (active or shadow)',
    )
    parser.add_argument(
        '--external', action='store_true',
        help='Show external findings (use with --eval)',
    )
    parser.add_argument(
        '--include-archived', action='store_true',
        help='Include archived dimensions in eval output',
    )

    args = parser.parse_args()
    _dispatch(args, parser)


# ---------------------------------------------------------------------------
# Subcommand handlers (M11: extracted from main)
# ---------------------------------------------------------------------------

def cmd_promote(args, _parser):
    """Handle --promote subcommand."""
    from dimension_manager import promote_dimension
    promote_dimension(args.promote)


def cmd_colocation(args, _parser):
    """Handle --colocation subcommand."""
    show_colocation(json_format=args.json)


def cmd_eval(args, parser):
    """Handle --eval subcommand."""
    if args.shadow and args.external:
        parser.error('--shadow and --external are mutually exclusive')
    if args.external:
        from dimension_manager import eval_external
        eval_external(
            include_archived=args.include_archived,
            json_format=args.json,
        )
    elif args.shadow:
        from dimension_manager import eval_shadow
        eval_shadow(include_archived=args.include_archived)
    else:
        data = load_findings()
        evaluate_dimensions(
            data.get('findings', []),
            config_override=None,
            json_format=args.json,
            include_shadow=args.shadow,
        )
        _show_escalation_status()


def _show_escalation_status():
    """Display escalation health metrics if available."""
    try:
        from escalation import (
            load_escalation_status, check_triggers,
        )
        esc = load_escalation_status()
        if esc.get('metrics'):
            print()
            print("Escalation Health:")
            metrics = esc['metrics']
            print(
                "  Dedup error rate: "
                f"{metrics.get('dedup_error_rate', 0):.1%}"
            )
            print(
                "  Edit corruption count: "
                f"{metrics.get('edit_corruption_count', 0)}"
            )
            print(
                "  Dimension change count: "
                f"{metrics.get('dimension_change_count', 0)}"
            )
            print(
                "  Feedback volume: "
                f"{metrics.get('feedback_volume', 0)}"
            )
            alerts = check_triggers(metrics)
            if alerts:
                print()
                for name, val, thresh, rec in alerts:
                    print(
                        f"  [!] {name}: {val:.2f} "
                        f"(threshold: {thresh}) "
                        f"-- {rec}"
                    )
            else:
                print(
                    "  All escalation metrics "
                    "within thresholds."
                )
            if esc.get('last_check'):
                print(
                    "  Last check: "
                    f"{esc['last_check']}"
                )
    except (ImportError, FileNotFoundError,
            json.JSONDecodeError, KeyError):
        pass  # Non-critical


def cmd_learn(args, parser):
    """Handle --learn subcommand."""
    if not any([args.pr, args.branch, args.ci_file]):
        parser.error(
            '--learn requires exactly one of: '
            '--pr, --branch, --ci-file'
        )
    source_count = sum(
        bool(x) for x in [args.pr, args.branch, args.ci_file]
    )
    if source_count > 1:
        parser.error(
            '--learn requires exactly one of: '
            '--pr, --branch, --ci-file'
        )
    from adapters.github_pr import GitHubPRAdapter
    from adapters.git_log import GitLogAdapter
    from adapters.ci_log import CILogAdapter
    from llm_parser import extract_findings
    from gap_detector import process_learn

    if args.pr:
        adapter = GitHubPRAdapter()
        canonical = adapter.fetch(args.pr)
    elif args.branch:
        adapter = GitLogAdapter()
        canonical = adapter.fetch(args.branch)
    else:
        adapter = CILogAdapter()
        canonical = adapter.fetch(args.ci_file)

    if not canonical:
        print(
            "forge: no findings to process from source",
            file=sys.stderr,
        )
        sys.exit(0)

    parsed = extract_findings(canonical)
    if not parsed:
        print(
            "forge: LLM parsing produced no results",
            file=sys.stderr,
        )
        sys.exit(0)

    process_learn(parsed)
    # Check shadow timeouts (D7)
    try:
        from dimension_manager import check_shadow_timeouts
        config = load_config()
        check_shadow_timeouts(config)
    except (ImportError, FileNotFoundError,
            json.JSONDecodeError, KeyError):
        pass  # Non-critical


def cmd_gaps(args, _parser):
    """Handle --gaps subcommand."""
    from gap_manager import run_gaps
    run_gaps(
        approve_expansion_id=args.approve_expansion,
    )


def cmd_reclassify(args, _parser):
    """Handle --reclassify subcommand."""
    from gap_manager import run_reclassify
    run_reclassify(args.reclassify[0], args.reclassify[1])


def cmd_propose(args, _parser):
    """Handle --propose subcommand."""
    from dimension_manager import run_propose
    run_propose(args.propose)


def cmd_add_dimension(args, _parser):
    """Handle --add-dimension subcommand."""
    from dimension_manager import add_dimension
    add_dimension(
        args.add_dimension,
        keywords_file=args.keywords_file,
    )


def cmd_retire(args, _parser):
    """Handle --retire subcommand."""
    from dimension_manager import retire_dimension
    retire_dimension(args.retire)


def _dispatch(args, parser):
    """Route parsed args to the appropriate subcommand handler."""
    if args.promote:
        cmd_promote(args, parser)
    elif args.colocation:
        cmd_colocation(args, parser)
    elif args.eval:
        cmd_eval(args, parser)
    elif args.recommend:
        show_recommendations(json_format=args.json)
    elif args.stats:
        show_stats(
            json_format=args.json,
            include_shadow=getattr(args, 'shadow', False),
        )
    elif args.classify:
        classify_findings()
    elif args.bootstrap:
        bootstrap_historical(args.bootstrap)
    elif args.learn:
        cmd_learn(args, parser)
    elif args.gaps:
        cmd_gaps(args, parser)
    elif args.approve_expansion and not args.gaps:
        from gap_manager import approve_expansion_noninteractive
        approve_expansion_noninteractive(args.approve_expansion)
    elif args.reclassify:
        cmd_reclassify(args, parser)
    elif args.propose:
        cmd_propose(args, parser)
    elif args.add_dimension:
        cmd_add_dimension(args, parser)
    elif args.retire:
        cmd_retire(args, parser)
    elif args.diff_spec:
        if args.dry_run:
            run_dry_run(args.diff_spec)
        else:
            override = None
            if args.full:
                override = 'full'
            elif args.step0:
                override = 'step0'
            run_forge(args.diff_spec, override_tier=override)
    elif args.dry_run:
        parser.error('--dry-run requires a diff_spec argument')
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
