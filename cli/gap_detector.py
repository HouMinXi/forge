#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Gap detection pipeline -- dedup, D4 three-outcome classification, storage (D3/D4)."""

import os
import sys
import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Tuple

from file_utils import atomic_write, load_json_file

# ---------------------------------------------------------------------------
# File path constants
# ---------------------------------------------------------------------------

EXTERNAL_FINDINGS_FILE = os.path.join('.forge', 'external_findings.json')
GAP_CANDIDATES_FILE = os.path.join('.forge', 'gap_candidates.json')
KEYWORD_EXPANSION_FILE = os.path.join(
    '.forge', 'keyword_expansion_queue.json',
)


# ---------------------------------------------------------------------------
# Loader functions
# ---------------------------------------------------------------------------

def load_external_findings():
    """Load .forge/external_findings.json.

    Returns dict with 'version' and 'findings' keys.
    Returns empty structure if file is missing or corrupted.
    """
    return load_json_file(
        EXTERNAL_FINDINGS_FILE, {'version': 1, 'findings': []},
    )


def load_gap_candidates():
    """Load .forge/gap_candidates.json.

    Returns dict with 'version' and 'candidates' keys.
    Returns empty structure if file is missing or corrupted.
    """
    return load_json_file(
        GAP_CANDIDATES_FILE, {'version': 1, 'candidates': []},
    )


def load_keyword_expansion_queue():
    """Load .forge/keyword_expansion_queue.json.

    Returns dict with 'version' and 'expansions' keys.
    Returns empty structure if file is missing or corrupted.
    """
    return load_json_file(
        KEYWORD_EXPANSION_FILE, {'version': 1, 'expansions': []},
    )


# ---------------------------------------------------------------------------
# Dedup functions
# ---------------------------------------------------------------------------

def is_exact_dup(source, source_id, existing_findings):
    """Check exact dedup via (source, source_id) match.

    Per D3 exact dedup spec: if any existing finding matches
    both source and source_id, skip entirely (no audit trail).

    Args:
        source: Data source type string.
        source_id: Unique ID within the source.
        existing_findings: List of existing finding dicts.

    Returns:
        bool: True if duplicate found.
    """
    for finding in existing_findings:
        if (finding.get('source') == source
                and finding.get('source_id') == source_id):
            return True
    return False


# Source priority for cross-source dedup tiebreaker.
# Lower number = higher priority.
_SOURCE_PRIORITY = {
    'github_pr': 0,
    'git_log': 1,
    'ci_log': 2,
}


def find_cross_source_dup(
    file_val, line_val, text_hash, timestamp_str, existing_findings,
):
    """Find cross-source duplicate via (file, line, text_hash) in 7-day window.

    Looks for any existing finding within 7 days before timestamp_str
    where file, line, and text_hash all match. Returns earliest-timestamp
    match; ties broken by source priority (github_pr > git_log > ci_log).

    Args:
        file_val: File path (str or None).
        line_val: Line number (int or None).
        text_hash: SHA-256 hash of finding text.
        timestamp_str: ISO-8601 timestamp of the new finding.
        existing_findings: List of existing finding dicts.

    Returns:
        str or None: ID of the original finding if dup found.
    """
    if file_val is None or line_val is None or text_hash is None:
        return None

    try:
        new_ts = datetime.fromisoformat(timestamp_str)
    except (ValueError, TypeError):
        print(
            "forge: warning: failed to parse new finding "
            f"timestamp: {timestamp_str!r}",
            file=sys.stderr,
        )
        return None
    if new_ts.tzinfo is None:
        new_ts = new_ts.replace(tzinfo=timezone.utc)

    window_start = new_ts - timedelta(days=7)
    best_match = None
    best_ts = None
    best_priority = 999

    for finding in existing_findings:
        if (finding.get('file') != file_val
                or finding.get('line') != line_val
                or finding.get('text_hash') != text_hash):
            continue

        try:
            f_ts = datetime.fromisoformat(finding.get('timestamp', ''))
        except (ValueError, TypeError):
            print(
                "forge: warning: failed to parse existing finding "
                f"timestamp for id={finding.get('id', '?')!r}",
                file=sys.stderr,
            )
            continue
        if f_ts.tzinfo is None:
            f_ts = f_ts.replace(tzinfo=timezone.utc)

        if f_ts < window_start or f_ts > new_ts:
            continue

        f_priority = _SOURCE_PRIORITY.get(
            finding.get('source', ''), 999,
        )

        # Pick earliest timestamp; ties broken by source priority
        if best_match is None:
            best_match = finding.get('id')
            best_ts = f_ts
            best_priority = f_priority
        elif f_ts < best_ts:
            best_match = finding.get('id')
            best_ts = f_ts
            best_priority = f_priority
        elif f_ts == best_ts and f_priority < best_priority:
            best_match = finding.get('id')
            best_ts = f_ts
            best_priority = f_priority

    return best_match


# ---------------------------------------------------------------------------
# D4 Classification
# ---------------------------------------------------------------------------

def classify_finding(finding_dict, keyword_dicts, dimension_states):
    """Classify a finding via D4 three-outcome algorithm.

    Steps 1-5:
    1. For each non-archived dim: count distinct keyword substring
       matches in search_text.
    2. If no non-archived dims exist: outcome_3.
    3. If max(count) > 0: outcome_1 with highest-count dim (ties
       broken alphabetically).
    4. If max(count) == 0 and dimension_raw exactly matches a
       non-archived dim key: outcome_2.
    5. Otherwise: outcome_3.

    Args:
        finding_dict: Dict with 'dimension_raw' and 'text' keys.
        keyword_dicts: Dict mapping dimension names to keyword lists.
        dimension_states: Dict mapping dimension names to state dicts.

    Returns:
        Tuple[str, Optional[str]]: (outcome, matched_dim).
        outcome is 'outcome_1', 'outcome_2', or 'outcome_3'.
        matched_dim is the dimension name or None.
    """
    dim_raw = (finding_dict.get('dimension_raw') or '').strip()
    text = finding_dict.get('text') or ''
    search_text = (dim_raw + ' ' + text).lower()

    # Filter to non-archived dimensions
    active_dims = []
    for dim in keyword_dicts:
        state = dimension_states.get(dim, {})
        status = state.get('status', 'active')
        if status != 'archived':
            active_dims.append(dim)

    # Step 2: no non-archived dims
    if not active_dims:
        return ('outcome_3', None)

    # Step 1: count distinct keyword matches per dimension
    dim_counts = {}
    for dim in active_dims:
        keywords = keyword_dicts.get(dim, [])
        if not keywords:
            continue
        count = 0
        for kw in keywords:
            if kw.lower() in search_text:
                count += 1
        dim_counts[dim] = count

    # All active dims have empty keyword lists -- no classification
    if not dim_counts:
        return ('outcome_3', None)

    max_count = max(dim_counts.values())

    # Step 3: keyword match found
    if max_count > 0:
        # Ties broken alphabetically
        best_dim = sorted(
            [d for d, c in dim_counts.items() if c == max_count],
        )[0]
        return ('outcome_1', best_dim)

    # Step 4: dimension_raw name match
    dim_raw_lower = dim_raw.lower()
    for dim in sorted(active_dims):
        if dim_raw_lower == dim.lower():
            return ('outcome_2', dim)

    # Step 5: unrecognized
    return ('outcome_3', None)


# ---------------------------------------------------------------------------
# Main processing function
# ---------------------------------------------------------------------------

def process_learn(adapter_findings, model=None):
    """Process external findings through dedup and D4 classification.

    Full pipeline: dedup -> classify -> store. Loads all storage
    files, processes each finding, writes results atomically.

    Args:
        adapter_findings: List[Tuple[CanonicalFinding, ExtractedFinding]]
            from llm_parser.extract_findings().
        model: Unused (reserved for future LLM-based classification).
    """
    # Lazy imports to avoid circular dependencies
    from forge_cli import load_config, CONFIG_FILE
    from llm_parser import compute_text_hash
    from migration import ensure_dimension_state, run_migration_if_needed

    # Load all storage files
    ext_data = load_external_findings()
    gap_data = load_gap_candidates()
    exp_data = load_keyword_expansion_queue()
    config = load_config()

    # Run migration if needed
    if 'dimension_states' not in config:
        skill_md = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            '..', 'skills', 'forge', 'SKILL.md',
        )
        run_migration_if_needed(
            CONFIG_FILE,
            os.path.join('.forge', 'findings.json'),
            skill_md,
        )
        # Reload config after migration
        config = load_config()

    keyword_dicts = config.get('keyword_dictionaries', {})
    dim_states = config.get('dimension_states', {})

    now = datetime.now(timezone.utc).isoformat()

    # Counters for summary
    n_total = 0
    n_outcome_1 = 0
    n_outcome_2 = 0
    n_outcome_3 = 0
    n_dup = 0

    for canonical, extracted in adapter_findings:
        n_total += 1

        # Build finding dict
        text_hash = compute_text_hash(extracted.text)
        finding = {
            'id': f"ext-{uuid.uuid4()}",
            'timestamp': canonical.timestamp,
            'source': canonical.source,
            'source_tool': canonical.source_tool,
            'source_id': canonical.source_id,
            'file': extracted.file,
            'line': extracted.line,
            'text': extracted.text,
            'dimension_raw': extracted.dimension_raw,
            'validated_dimension': None,
            'confidence': extracted.confidence,
            'gap': False,
            'suggested_keywords': extracted.suggested_keywords,
            'text_hash': text_hash,
            'dedup_of': None,
            'context': canonical.context,
            'raw_source': canonical.raw_source,
        }

        # Exact dedup: skip entirely
        if is_exact_dup(
            canonical.source,
            canonical.source_id,
            ext_data['findings'],
        ):
            n_dup += 1
            continue

        # Store finding in external_findings
        ext_data['findings'].append(finding)

        # Cross-source dedup
        dup_id = find_cross_source_dup(
            extracted.file,
            extracted.line,
            text_hash,
            canonical.timestamp,
            ext_data['findings'],
        )
        if dup_id and dup_id != finding['id']:
            finding['dedup_of'] = dup_id
            n_dup += 1
            continue

        # D4 classification
        outcome, matched_dim = classify_finding(
            finding, keyword_dicts, dim_states,
        )

        if outcome == 'outcome_1':
            # Keyword match: validate and update dimension_states
            finding['validated_dimension'] = matched_dim
            finding['gap'] = False
            n_outcome_1 += 1

            # Update dimension_states
            ensure_dimension_state(config, matched_dim)
            state = config['dimension_states'][matched_dim]
            state['finding_count'] = state.get('finding_count', 0) + 1
            state['last_seen'] = now

        elif outcome == 'outcome_2':
            # Name match but keywords don't match
            finding['validated_dimension'] = None
            finding['gap'] = False
            n_outcome_2 += 1

            # Create keyword expansion queue entry
            exp_entry = {
                'id': f"exp-{uuid.uuid4()}",
                'finding_id': finding['id'],
                'created_at': now,
                'proposed_dimension': matched_dim,
                'unmatched_text': finding['text'],
                'text_hash': finding['text_hash'],
                'suggested_keywords': finding['suggested_keywords'],
                'status': 'pending',
                'reclassified_to': None,
            }
            exp_data['expansions'].append(exp_entry)

        else:
            # outcome_3: unrecognized
            finding['validated_dimension'] = 'unknown'
            finding['gap'] = True
            n_outcome_3 += 1

            # Create gap candidate
            gap_entry = {
                'id': f"gap-{uuid.uuid4()}",
                'finding_id': finding['id'],
                'timestamp': finding['timestamp'],
                'created_at': now,
                'dimension_raw': finding['dimension_raw'],
                'text': finding['text'],
                'text_hash': finding['text_hash'],
                'file': finding['file'],
                'line': finding['line'],
                'source': finding['source'],
                'status': 'pending',
                'group_id': None,
                'reclassified_from': None,
            }
            gap_data['candidates'].append(gap_entry)

    # Atomic write all modified files
    atomic_write(EXTERNAL_FINDINGS_FILE, ext_data)
    atomic_write(GAP_CANDIDATES_FILE, gap_data)
    atomic_write(KEYWORD_EXPANSION_FILE, exp_data)
    atomic_write(CONFIG_FILE, config)

    print(
        f"forge: learned {n_total} findings "
        f"({n_outcome_1} matched, {n_outcome_2} expansion, "
        f"{n_outcome_3} gap, {n_dup} duplicates)"
    )
