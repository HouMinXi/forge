#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Config migration -- promoted_dimensions to dimension_states (D3 spec)."""

import json
import os
import sys
from datetime import datetime, timezone

SHADOW_DIMENSIONS = {'doc_completeness', 'change_scope'}

DIMENSION_RENAME_MAP = {
    'convention_adherence': 'convention',
    'bidirectional_correctness': 'bidirectional',
    'state_management': 'concurrency',
    'input_validation': 'edge_cases',
    'ai_code_smells': 'ai_code_smell',
}

SEED_KEYWORD_DICTIONARIES = {
    "correctness": ["off-by-one", "wrong comparison", "inverted condition", "null", "uninitialized"],
    "security": ["injection", "SSRF", "traversal", "authentication", "authorization", "IDOR", "secrets", "credentials"],
    "concurrency": ["race condition", "deadlock", "lock ordering", "unsynchronized", "thread safety"],
    "edge_cases": ["empty", "zero", "negative", "maximum", "unicode", "encoding", "timezone"],
    "error_handling": ["swallowed", "ignored error", "missing rollback", "catch-all", "timeout", "retry"],
    "api_contract": ["breaking change", "wire format", "precondition", "postcondition", "validation"],
    "bidirectional": ["round-trip", "serialize", "deserialize", "parse", "format", "encode", "decode"],
    "graceful_degradation": ["missing dependency", "optional tool", "feature absence", "skip gracefully"],
    "convention": ["naming", "style drift", "helper", "pattern", "consistency", "nesting depth"],
    "performance": ["unbounded", "N+1", "O(n^2)", "hot path", "blocking", "memory leak", "pagination"],
    "test_quality": ["mock", "flaky", "shared state", "negative case", "boundary test", "coverage"],
    "ai_code_smell": ["hallucinated", "over-engineering", "plausible-but-wrong", "TODO", "FIXME", "repetition"],
    "doc_completeness": ["docstring", "changelog", "README", "documentation", "undocumented"],
    "change_scope": ["unrelated", "mixed concerns", "unfocused", "scope"],
}


def _migrate_findings_renames(findings_data, kw_dicts):
    """Rename legacy dimension names in findings data in place.

    Applies DIMENSION_RENAME_MAP to each finding's dimension field.
    Warns if the target dimension has no keyword dictionary entry.

    Args:
        findings_data: dict with 'findings' list (mutated in place).
        kw_dicts: keyword_dictionaries dict for validation.
    """
    for finding in findings_data.get('findings', []):
        dim = finding.get('dimension', '')
        if dim in DIMENSION_RENAME_MAP:
            new_dim = DIMENSION_RENAME_MAP[dim]
            finding['dimension'] = new_dim
            # M14: warn if target dimension has no keyword dictionary
            if new_dim not in kw_dicts:
                print(
                    f"Warning: renamed dimension '{dim}' -> "
                    f"'{new_dim}' but '{new_dim}' has no entry in "
                    f"keyword_dictionaries",
                    file=sys.stderr,
                )


def _build_dimension_states(config, findings_data):
    """Build dimension_states map from keyword_dictionaries and findings.

    Creates a dimension_states entry for each keyword dictionary key.
    Shadow dimensions are hardcoded per SKILL.md lines 468-473.
    Promoted dimensions override shadow status to active.

    Args:
        config: config dict (mutated: dimension_states added,
                promoted_dimensions removed).
        findings_data: dict with 'findings' list.
    """
    shadow_dimensions = SHADOW_DIMENSIONS
    now = datetime.now(timezone.utc).isoformat()
    config['dimension_states'] = {}

    for dim in config['keyword_dictionaries']:
        count = sum(
            1 for f in findings_data.get('findings', [])
            if f.get('dimension') == dim
        )
        status = "shadow" if dim in shadow_dimensions else "active"
        config['dimension_states'][dim] = {
            "status": status,
            "last_seen": now,
            "finding_count": count,
            "added_at": now if status == "shadow" else None,
            "consecutive_eval_failures": None,
            "seed_test_status": None,
        }

    # Override status for promoted dimensions
    for dim in config.pop('promoted_dimensions', []):
        if dim in config['dimension_states']:
            config['dimension_states'][dim]['status'] = 'active'


def migrate_to_dimension_states(config, findings_data, skill_md_path):
    """Migrate legacy promoted_dimensions list to dimension_states map.

    Mutates config and findings_data in place. Caller must atomic_write both.
    Caller (run_migration_if_needed) checks idempotency before calling.
    """
    # Step 0: seed keyword_dictionaries from module constant if not in config
    if 'keyword_dictionaries' not in config:
        config['keyword_dictionaries'] = dict(SEED_KEYWORD_DICTIONARIES)

    # Step 1: rename legacy dimension names in findings
    kw_dicts = config.get('keyword_dictionaries', {})
    _migrate_findings_renames(findings_data, kw_dicts)

    # Step 2: build dimension_states from keyword_dictionaries
    _build_dimension_states(config, findings_data)


def ensure_dimension_state(config, dim):
    """Auto-create dimension_states entry for dim in keyword_dictionaries.

    Implements the fallback rule from CONTEXT.md D3: if a dimension is in
    keyword_dictionaries but missing from dimension_states, create it.
    """
    if 'dimension_states' not in config:
        config['dimension_states'] = {}

    if dim not in config['dimension_states']:
        now = datetime.now(timezone.utc).isoformat()
        config['dimension_states'][dim] = {
            "status": "active",
            "last_seen": now,
            "finding_count": 0,
            "added_at": None,
            "consecutive_eval_failures": None,
            "seed_test_status": None,
        }

    return config['dimension_states'][dim]


def run_migration_if_needed(config_path, findings_path, skill_md_path):
    """Load config and findings, migrate if needed, write both back.

    Single-process assumption: this CLI tool runs as a single process,
    so the check-then-write pattern is safe without file locking.
    Concurrent CLI invocations on the same config are not supported.
    """
    from file_utils import atomic_write

    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)

    try:
        with open(findings_path, 'r', encoding='utf-8') as f:
            findings_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        findings_data = {'version': 1, 'findings': [], 'runs': []}

    if 'dimension_states' in config:
        print("forge: dimension_states already present, skipping migration")
        return

    migrate_to_dimension_states(config, findings_data, skill_md_path)

    atomic_write(findings_path, findings_data)
    atomic_write(config_path, config)

    n_dims = len(config.get('dimension_states', {}))
    print(f"forge: migrated to dimension_states ({n_dims} dimensions)")
