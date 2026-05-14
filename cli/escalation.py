#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Escalation monitor -- self-sustaining health check for v2 upgrade triggers (LEARN-10).

Computes dedup error rate, edit corruption count, dimension change count,
and feedback volume. When any trigger threshold is crossed, prints a
[forge-escalate] alert with specific recommendation for upgrading to the
next level of progressive complexity (LEARN-03/04/05).

Health check runs after every 50 pipeline runs or monthly (whichever first).
Status persisted to .forge/escalation-status.json.
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta

# Lazy imports -- forge_cli and gap_detector loaded only when needed
# to avoid circular imports at module level.

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ESCALATION_FILE = os.path.join('.forge', 'escalation-status.json')
DEDUP_ERROR_THRESHOLD = 0.20   # 20% -- triggers LEARN-03
EDIT_CORRUPTION_THRESHOLD = 3  # 3+ corrupted edits -- triggers LEARN-04
DIMENSION_CHANGE_THRESHOLD = 10  # 10+ changes -- triggers LEARN-05
CHECK_INTERVAL_RUNS = 50       # every 50 pipeline runs
CHECK_INTERVAL_DAYS = 30       # or monthly
ROLLING_WINDOW_DAYS = 90       # 90-day window for dedup error rate


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_escalation_status():
    """Load .forge/escalation-status.json.

    Returns dict with version, last_check, runs_since_check, metrics,
    and alerts keys. Returns default structure if file is missing or
    corrupted.
    """
    try:
        with open(ESCALATION_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, IOError):
        return {
            'version': 1,
            'last_check': None,
            'runs_since_check': 0,
            'metrics': {},
            'alerts': [],
        }


# ---------------------------------------------------------------------------
# Check scheduling
# ---------------------------------------------------------------------------

def should_run_check(escalation_data, total_runs=0):
    """Determine whether an escalation health check should run.

    Returns True if:
    - last_check is None (never checked), OR
    - runs_since_check >= CHECK_INTERVAL_RUNS, OR
    - (now - last_check) > CHECK_INTERVAL_DAYS days

    Args:
        escalation_data: dict from load_escalation_status().
        total_runs: reserved for future use (e.g., adaptive check
            intervals based on total pipeline run count). Currently
            unused; kept for interface stability.

    Returns:
        bool: True if check should run.
    """
    last_check = escalation_data.get('last_check')
    if last_check is None:
        return True

    runs_since = escalation_data.get('runs_since_check', 0)
    if runs_since >= CHECK_INTERVAL_RUNS:
        return True

    try:
        last_dt = datetime.fromisoformat(last_check)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if (now - last_dt) > timedelta(days=CHECK_INTERVAL_DAYS):
            return True
    except (ValueError, TypeError):
        # Corrupt timestamp -- trigger check to fix it
        return True

    return False


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------

def compute_metrics(findings_data, external_data, config):
    """Compute escalation health metrics.

    Args:
        findings_data: dict from load_findings() with 'findings' list.
        external_data: dict from load_external_findings() with 'findings'.
        config: config dict from load_config().

    Returns:
        dict with dedup_error_rate, edit_corruption_count,
        dimension_change_count, feedback_volume, computed_at.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=ROLLING_WINDOW_DAYS)

    # --- dedup_error_rate ---
    # Count external findings with dedup_of != None where original and
    # duplicate have different validated_dimension values (false match).
    ext_findings = external_data.get('findings', [])
    total_dedup_attempts = 0
    false_matches = 0

    # Build lookup for external findings by id
    ext_by_id = {}
    for ef in ext_findings:
        fid = ef.get('id')
        if fid:
            ext_by_id[fid] = ef

    for ef in ext_findings:
        dedup_of = ef.get('dedup_of')
        if dedup_of is None:
            continue

        # Filter to 90-day rolling window
        created = ef.get('created_at')
        if created is None:
            created = ef.get('timestamp')
        if created is not None:
            try:
                created_dt = datetime.fromisoformat(created)
                if created_dt.tzinfo is None:
                    created_dt = created_dt.replace(tzinfo=timezone.utc)
                if created_dt < cutoff:
                    continue
            except (ValueError, TypeError):
                pass

        total_dedup_attempts += 1

        # Check if validated_dimension differs between dup and original
        original = ext_by_id.get(dedup_of)
        if original is None:
            continue

        dup_dim = ef.get('validated_dimension')
        if dup_dim is None:
            dup_dim = ef.get('dimension')
        orig_dim = original.get('validated_dimension')
        if orig_dim is None:
            orig_dim = original.get('dimension')

        if dup_dim and orig_dim and dup_dim != orig_dim:
            false_matches += 1

    dedup_error_rate = (
        false_matches / total_dedup_attempts
        if total_dedup_attempts > 0
        else 0.0
    )

    # --- edit_corruption_count ---
    # User-maintained metric. Read from existing escalation data.
    existing_status = load_escalation_status()
    edit_corruption_count = (
        existing_status.get('metrics', {})
        .get('edit_corruption_count', 0)
    )

    # --- dimension_change_count ---
    # Count config dimension_states entries where added_at is not None.
    dim_states = config.get('dimension_states', {})
    dimension_change_count = 0
    for _dim_name, state in dim_states.items():
        if state.get('added_at') is not None:
            dimension_change_count += 1

    # --- feedback_volume ---
    # Count findings with outcome in ('accepted', 'rejected').
    internal_findings = findings_data.get('findings', [])
    feedback_volume = sum(
        1 for f in internal_findings
        if f.get('outcome') in ('accepted', 'rejected')
    )

    return {
        'dedup_error_rate': dedup_error_rate,
        'edit_corruption_count': edit_corruption_count,
        'dimension_change_count': dimension_change_count,
        'feedback_volume': feedback_volume,
        'computed_at': now.isoformat(),
    }


# ---------------------------------------------------------------------------
# Trigger checking
# ---------------------------------------------------------------------------

def check_triggers(metrics):
    """Check escalation trigger thresholds.

    Args:
        metrics: dict from compute_metrics().

    Returns:
        list of (trigger_name, metric_value, threshold, recommendation)
        tuples for each crossed threshold.
    """
    alerts = []

    rate = metrics.get('dedup_error_rate', 0.0)
    if rate > DEDUP_ERROR_THRESHOLD:
        alerts.append((
            'LEARN-03',
            rate,
            DEDUP_ERROR_THRESHOLD,
            'Rule-based deduplication is failing. '
            'Recommend upgrading to embedding classification.',
        ))

    corruption = metrics.get('edit_corruption_count', 0)
    if corruption >= EDIT_CORRUPTION_THRESHOLD:
        alerts.append((
            'LEARN-04',
            corruption,
            EDIT_CORRUPTION_THRESHOLD,
            'Line-based SKILL.md edits are producing corrupted '
            'markdown. Recommend upgrading to AST-based edits.',
        ))

    changes = metrics.get('dimension_change_count', 0)
    if changes >= DIMENSION_CHANGE_THRESHOLD:
        alerts.append((
            'LEARN-05',
            changes,
            DIMENSION_CHANGE_THRESHOLD,
            'Enough dimension changes to justify structured '
            'evidence generation. '
            'Recommend structured evidence generator.',
        ))

    return alerts


# ---------------------------------------------------------------------------
# Main check runner
# ---------------------------------------------------------------------------

def run_escalation_check(force=False):
    """Run escalation health check if due (or forced).

    Loads findings, external findings, and config. Computes metrics,
    checks triggers, prints [forge-escalate] alerts, and persists
    status to .forge/escalation-status.json.

    Args:
        force: if True, skip schedule check and run immediately.
    """
    _cli_dir = os.path.dirname(os.path.abspath(__file__))
    if _cli_dir not in sys.path:
        sys.path.insert(0, _cli_dir)
    from forge_cli import load_findings, load_config
    from file_utils import atomic_write
    from gap_detector import load_external_findings

    esc_data = load_escalation_status()

    if not force and not should_run_check(esc_data):
        return

    findings_data = load_findings()
    external_data = load_external_findings()
    config = load_config()

    metrics = compute_metrics(findings_data, external_data, config)
    alerts = check_triggers(metrics)

    for name, val, thresh, rec in alerts:
        print(
            f"[forge-escalate] {name} trigger met: "
            f"{val} > {thresh} threshold.",
            file=sys.stderr,
        )
        print(
            f"  {rec}",
            file=sys.stderr,
        )
        print(
            "  See .forge/escalation-status.json for details.",
            file=sys.stderr,
        )
        print(
            f"  Action: /gsd-plan-phase {name.lower()}-upgrade",
            file=sys.stderr,
        )

    # Persist updated status
    esc_data['last_check'] = datetime.now(timezone.utc).isoformat()
    esc_data['runs_since_check'] = 0
    esc_data['metrics'] = metrics
    esc_data['alerts'] = [
        {
            'trigger': name,
            'value': val,
            'threshold': thresh,
            'recommendation': rec,
        }
        for name, val, thresh, rec in alerts
    ]

    atomic_write(ESCALATION_FILE, esc_data)


# ---------------------------------------------------------------------------
# Run count tracking
# ---------------------------------------------------------------------------

def increment_run_count():
    """Increment runs_since_check counter.

    Called by forge pipeline after each review run to track run count
    without running the full health check.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from file_utils import atomic_write

    esc_data = load_escalation_status()
    esc_data['runs_since_check'] = (
        esc_data.get('runs_since_check', 0) + 1
    )

    atomic_write(ESCALATION_FILE, esc_data)
