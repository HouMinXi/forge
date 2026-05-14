#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Gap management -- interactive gap review, grouping, reclassification (D4/D5)."""

import contextlib
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from file_utils import atomic_write

# ---------------------------------------------------------------------------
# File path constants
# ---------------------------------------------------------------------------

GAP_GROUPS_FILE = os.path.join('.forge', 'gap_groups.json')

# Terminal state constants (D5 spec)
GAP_TERMINAL = {'proposed', 'dismissed', 'reclassified', 'auto_dismissed'}
EXPANSION_TERMINAL = {
    'approved', 'rejected', 'auto_dismissed', 'reclassified',
}

# Staleness thresholds (days)
_EXPANSION_STALE_DAYS = 90
_CANDIDATE_STALE_DAYS = 180

# Minimum candidates for a proposal-ready group.
# Value of 3 is the D5 spec threshold: fewer than 3 candidates
# means insufficient evidence to propose a new dimension.
_MIN_GROUP_SIZE = 3

# Maximum gap candidates included in LLM grouping prompt to
# prevent unbounded prompt construction.
MAX_CANDIDATES_FOR_LLM = 500

_LOCK_FILE = os.path.join('.forge', '.gaps.lock')


# ---------------------------------------------------------------------------
# File-based lock for load-modify-save cycles
# ---------------------------------------------------------------------------

_LOCK_STALE_SECONDS = 3600


@contextlib.contextmanager
def _file_lock(lockfile):
    """Simple file-based lock context manager.

    Creates lockfile on entry, removes on exit. Not reentrant.
    Prevents concurrent gap management from corrupting JSON files.
    Automatically removes stale locks older than _LOCK_STALE_SECONDS.
    """
    if os.path.exists(lockfile):
        try:
            age = time.time() - os.path.getmtime(lockfile)
            if age > _LOCK_STALE_SECONDS:
                os.unlink(lockfile)
                print(
                    f"forge: warning: removed stale lock "
                    f"({age:.0f}s old)",
                    file=sys.stderr,
                )
        except OSError:
            pass
    try:
        fd = os.open(lockfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        print(
            "forge: error: gap management lock held "
            f"({lockfile}). Another process may be running. "
            "Remove manually if stale.",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        yield
    finally:
        try:
            os.unlink(lockfile)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Interactive choice helper
# ---------------------------------------------------------------------------

def _get_user_choice(prompt, valid_choices):
    """Prompt user for a choice from valid_choices.

    Args:
        prompt: Prompt string to display.
        valid_choices: Dict mapping short codes to full names,
            e.g. {'a': 'approve', 'r': 'reject', 's': 'skip',
                   'q': 'quit'}.

    Returns:
        str or None: Normalized full choice name, or None on
            quit/interrupt.
    """
    while True:
        try:
            raw = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return None

        if raw in valid_choices:
            return valid_choices[raw]
        if raw in valid_choices.values():
            return raw
        valid_keys = '/'.join(
            f"[{k}]{v[1:]}" for k, v in valid_choices.items()
        )
        print(f"  Invalid choice. Use {valid_keys}.")


# ---------------------------------------------------------------------------
# Loader functions
# ---------------------------------------------------------------------------

def load_gap_groups():
    """Load .forge/gap_groups.json.

    Returns dict with 'version', 'generated_at', and 'groups' keys.
    Returns empty structure if file is missing or corrupted.
    """
    try:
        with open(GAP_GROUPS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, IOError):
        return {'version': 1, 'generated_at': None, 'groups': []}


# ---------------------------------------------------------------------------
# Staleness sweeps
# ---------------------------------------------------------------------------

def _sweep_stale_expansions(exp_data, ext_data, now):
    """Auto-dismiss keyword expansions pending > 90 days.

    Per D5 spec: stale expansions are too old for actionable gap
    analysis. Sets expansion status to auto_dismissed. For the
    associated external finding, sets gap=True and
    validated_dimension='unknown' if validated_dimension is None.

    Args:
        exp_data: Keyword expansion queue data dict.
        ext_data: External findings data dict.
        now: Current UTC datetime object.

    Returns:
        int: Count of auto-dismissed expansions.
    """
    count = 0
    ext_by_id = {
        f['id']: f for f in ext_data.get('findings', [])
    }

    for exp in exp_data.get('expansions', []):
        if exp.get('status') != 'pending':
            continue

        created_at = exp.get('created_at', '')
        try:
            created_dt = datetime.fromisoformat(created_at)
        except (ValueError, TypeError):
            print(
                "forge: warning: failed to parse expansion "
                f"created_at for id={exp.get('id', '?')!r}",
                file=sys.stderr,
            )
            continue

        age = now - created_dt
        if age.days <= _EXPANSION_STALE_DAYS:
            continue

        exp['status'] = 'auto_dismissed'
        count += 1

        # Update associated external finding
        finding_id = exp.get('finding_id')
        if finding_id and finding_id in ext_by_id:
            ext_finding = ext_by_id[finding_id]
            if ext_finding.get('validated_dimension') is None:
                ext_finding['gap'] = True
                ext_finding['validated_dimension'] = 'unknown'

    if count > 0:
        print(
            f"forge: auto-dismissed {count} stale expansion(s) "
            f"(>{_EXPANSION_STALE_DAYS} days)"
        )
    return count


def _sweep_stale_candidates(gap_data, now):
    """Auto-dismiss gap candidates pending/grouped > 180 days.

    Per D5 spec: candidates that have been pending or grouped
    for too long are auto-dismissed.

    Args:
        gap_data: Gap candidates data dict.
        now: Current UTC datetime object.

    Returns:
        int: Count of auto-dismissed candidates.
    """
    count = 0
    for candidate in gap_data.get('candidates', []):
        status = candidate.get('status', '')
        if status not in ('pending', 'grouped'):
            continue

        created_at = candidate.get('created_at', '')
        try:
            created_dt = datetime.fromisoformat(created_at)
        except (ValueError, TypeError):
            print(
                "forge: warning: failed to parse candidate "
                f"created_at for id={candidate.get('id', '?')!r}",
                file=sys.stderr,
            )
            continue

        age = now - created_dt
        if age.days <= _CANDIDATE_STALE_DAYS:
            continue

        candidate['status'] = 'auto_dismissed'
        count += 1

    if count > 0:
        print(
            f"forge: auto-dismissed {count} stale candidate(s) "
            f"(>{_CANDIDATE_STALE_DAYS} days)"
        )
    return count


# ---------------------------------------------------------------------------
# Keyword expansion interactive review
# ---------------------------------------------------------------------------

def _review_expansions(exp_data, ext_data, gap_data, config):
    """Interactively review pending keyword expansions.

    Displays each pending expansion and prompts user to approve,
    reject, skip, or quit. Follows the classify_findings pattern
    from forge_cli.py.

    Args:
        exp_data: Keyword expansion queue data dict.
        ext_data: External findings data dict.
        gap_data: Gap candidates data dict (for rejected -> gap).
        config: Config dict with keyword_dictionaries and
            dimension_states.

    Returns:
        bool: True if any modifications were made. Callers should
        check the return value to decide whether to persist changes.
    """
    # Lazy import to avoid circular dependency
    from migration import ensure_dimension_state

    pending = [
        exp for exp in exp_data.get('expansions', [])
        if exp.get('status') == 'pending'
    ]

    if not pending:
        print("No pending keyword expansions to review.")
        return False

    print(f"\nforge: {len(pending)} pending keyword expansion(s)\n")

    ext_by_id = {
        f['id']: f for f in ext_data.get('findings', [])
    }
    modified = False
    now = datetime.now(timezone.utc).isoformat()

    for seq, exp in enumerate(pending, 1):
        print(f"--- Expansion {seq}/{len(pending)} ---")
        print(
            f"  Dimension:  {exp.get('proposed_dimension', 'unknown')}"
        )
        print(f"  Text:       {exp.get('unmatched_text', '')}")
        print(
            f"  Keywords:   "
            f"{', '.join(exp.get('suggested_keywords', []))}"
        )
        print()

        choice = _get_user_choice(
            "  [a]pprove / [r]eject / [s]kip / [q]uit: ",
            {'a': 'approve', 'r': 'reject', 's': 'skip', 'q': 'quit'},
        )

        if choice is None or choice == 'quit':
            return modified

        if choice == 'approve':
            exp['status'] = 'approved'
            modified = True

            # Merge keywords into config
            dim = exp.get('proposed_dimension', '')
            kw_dicts = config.setdefault(
                'keyword_dictionaries', {},
            )
            existing_kw = set(kw_dicts.get(dim, []))
            for kw in exp.get('suggested_keywords', []):
                existing_kw.add(kw)
            kw_dicts[dim] = sorted(existing_kw)

            # Update external finding
            finding_id = exp.get('finding_id')
            if finding_id and finding_id in ext_by_id:
                ext_by_id[finding_id]['validated_dimension'] = dim
                ext_by_id[finding_id]['gap'] = False

            # Update dimension_states
            ensure_dimension_state(config, dim)
            state = config['dimension_states'][dim]
            state['finding_count'] = (
                state.get('finding_count', 0) + 1
            )
            state['last_seen'] = now

            print("  -> approved\n")

        elif choice == 'reject':
            exp['status'] = 'rejected'
            modified = True

            # Create gap candidate from rejected expansion
            gap_id = f"gap-{uuid.uuid4()}"
            exp['reclassified_to'] = gap_id

            # Update external finding
            finding_id = exp.get('finding_id')
            if finding_id and finding_id in ext_by_id:
                ext_finding = ext_by_id[finding_id]
                ext_finding['gap'] = True
                ext_finding['validated_dimension'] = 'unknown'

                # M9: dedup check before creating gap candidate
                existing_gap_fids = {
                    c.get('finding_id')
                    for c in gap_data.get('candidates', [])
                }
                if finding_id not in existing_gap_fids:
                    gap_entry = {
                        'id': gap_id,
                        'finding_id': finding_id,
                        'timestamp': ext_finding.get(
                            'timestamp', '',
                        ),
                        'created_at': now,
                        'dimension_raw': ext_finding.get(
                            'dimension_raw', '',
                        ),
                        'text': ext_finding.get('text', ''),
                        'text_hash': ext_finding.get(
                            'text_hash', '',
                        ),
                        'file': ext_finding.get('file'),
                        'line': ext_finding.get('line'),
                        'source': ext_finding.get('source', ''),
                        'status': 'pending',
                        'group_id': None,
                        'reclassified_from': exp.get('id'),
                    }
                    gap_data.setdefault('candidates', []).append(
                        gap_entry,
                    )
                else:
                    print(
                        "forge: warning: gap candidate already "
                        f"exists for finding {finding_id}",
                        file=sys.stderr,
                    )

            print("  -> rejected (created gap candidate)\n")

        elif choice == 'skip':
            print("  -> skipped\n")

    return modified


# ---------------------------------------------------------------------------
# LLM grouping
# ---------------------------------------------------------------------------

def _group_candidates(gap_data, ext_data):
    """Group pending gap candidates via LLM.

    Step 4 (D5): Reset ALL non-terminal candidates to
    status='pending', group_id=None. Then collect all pending
    candidates and ask LLM to group them by review concern type.
    Step 5: assign group IDs and statuses.

    Args:
        gap_data: Gap candidates data dict.
        ext_data: External findings data dict.

    Returns:
        dict or None: Gap groups data dict, or None on failure.
    """
    # Step 4: reset non-terminal candidates
    for candidate in gap_data.get('candidates', []):
        if candidate.get('status') not in GAP_TERMINAL:
            candidate['status'] = 'pending'
            candidate['group_id'] = None

    # Collect all pending candidates
    pending = [
        c for c in gap_data.get('candidates', [])
        if c.get('status') == 'pending'
    ]

    if not pending:
        print("No gap candidates to group.")
        return None

    # M7: truncate to avoid unbounded LLM prompt
    if len(pending) > MAX_CANDIDATES_FOR_LLM:
        print(
            f"forge: warning: truncating {len(pending)} candidates "
            f"to {MAX_CANDIDATES_FOR_LLM} for LLM grouping",
            file=sys.stderr,
        )
        pending = pending[:MAX_CANDIDATES_FOR_LLM]

    print(f"\nforge: grouping {len(pending)} gap candidate(s) via LLM...")
    print("forge: waiting for LLM response...")

    # Build LLM prompt
    candidate_texts = []
    for i, c in enumerate(pending, 1):
        candidate_texts.append(
            f"{i}. [id={c['id']}] "
            f"dimension_raw={c.get('dimension_raw', '')}, "
            f"text={c.get('text', '')}"
        )
    candidate_block = "\n".join(candidate_texts)

    prompt = (
        "Given these unclassified code review findings, group them "
        "by the type of review concern they represent. For each "
        "group, propose a dimension name (lowercase, underscores, "
        "no spaces) and a one-sentence description.\n\n"
        "Findings:\n"
        f"{candidate_block}\n\n"
        "Return a JSON array of groups. Each group must have:\n"
        '- "proposed_dimension": string (lowercase, [a-z0-9_])\n'
        '- "description": string (one sentence)\n'
        '- "candidate_ids": array of id strings from above\n\n'
        "Return ONLY the JSON array, no other text."
    )

    # Call LLM
    try:
        from llm_parser import _get_client, _parse_json_response
        client = _get_client()
        if client is None:
            print(
                "forge: error: LLM not available for gap grouping",
                file=sys.stderr,
            )
            return None

        response = client.messages.create(
            model="claude-haiku-3.5",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )

        resp_text = ""
        for block in response.content:
            if hasattr(block, 'text'):
                resp_text += block.text

        groups_raw = _parse_json_response(resp_text)
        if groups_raw is None:
            print(
                "forge: error: LLM returned unparseable "
                "response for gap grouping",
                file=sys.stderr,
            )
            return None

        if not isinstance(groups_raw, list):
            print(
                "forge: error: LLM returned non-array "
                "response for gap grouping",
                file=sys.stderr,
            )
            return None

    except Exception as exc:
        print(
            f"forge: error: LLM gap grouping failed: {exc}",
            file=sys.stderr,
        )
        return None

    # Build candidate ID lookup for validation
    valid_ids = {c['id'] for c in pending}

    # Step 5: create gap_groups structure
    now = datetime.now(timezone.utc).isoformat()
    groups = []

    for raw_group in groups_raw:
        if not isinstance(raw_group, dict):
            continue

        proposed_dim = str(
            raw_group.get('proposed_dimension', ''),
        )
        # Sanitize to [a-z0-9_] per T-03-12
        proposed_dim = re.sub(r'[^a-z0-9_]', '_', proposed_dim.lower())
        proposed_dim = proposed_dim.strip('_')
        if not proposed_dim:
            continue

        description = str(raw_group.get('description', ''))

        candidate_ids = raw_group.get('candidate_ids', [])
        # M6: validate candidate_ids is actually a list
        if not isinstance(candidate_ids, list):
            continue
        # Validate candidate_ids against existing candidates
        valid_cids = [
            cid for cid in candidate_ids
            if isinstance(cid, str) and cid in valid_ids
        ]
        if not valid_cids:
            continue

        group_id = f"grp-{uuid.uuid4()}"

        # Update candidates with group assignment
        for candidate in gap_data.get('candidates', []):
            if candidate['id'] in valid_cids:
                candidate['status'] = 'grouped'
                candidate['group_id'] = group_id

        groups.append({
            'group_id': group_id,
            'proposed_dimension': proposed_dim,
            'description': description,
            'candidate_ids': valid_cids,
            'count': len(valid_cids),
            'status': 'pending',
        })

    gap_groups = {
        'version': 1,
        'generated_at': now,
        'groups': groups,
    }

    print(f"forge: created {len(groups)} gap group(s)")
    return gap_groups


# ---------------------------------------------------------------------------
# Group decision interaction
# ---------------------------------------------------------------------------

def _process_groups(gap_data, ext_data, exp_data, config, gap_groups):
    """Interactively process proposal-ready gap groups.

    For each group with >= 3 non-terminal candidates, prompt user
    to propose, reclassify, or dismiss. Groups with < 3 live
    candidates shown as insufficient evidence.

    Args:
        gap_data: Gap candidates data dict.
        ext_data: External findings data dict.
        exp_data: Keyword expansion queue data dict.
        config: Config dict.
        gap_groups: Gap groups data dict.

    Returns:
        bool: True if any modifications were made. Callers should
        check the return value to decide whether to persist changes.
    """
    from migration import ensure_dimension_state

    if gap_groups is None:
        return False

    groups = gap_groups.get('groups', [])
    pending_groups = [
        g for g in groups if g.get('status') == 'pending'
    ]

    if not pending_groups:
        print("No pending gap groups to process.")
        return False

    print(f"\nforge: {len(pending_groups)} pending gap group(s)\n")

    # Build lookups
    cand_by_id = {
        c['id']: c for c in gap_data.get('candidates', [])
    }
    ext_by_id = {
        f['id']: f for f in ext_data.get('findings', [])
    }

    modified = False
    now = datetime.now(timezone.utc).isoformat()

    for seq, group in enumerate(pending_groups, 1):
        # Compute live non-terminal candidate count
        live_cids = [
            cid for cid in group.get('candidate_ids', [])
            if cid in cand_by_id
            and cand_by_id[cid].get('status') not in GAP_TERMINAL
        ]
        live_count = len(live_cids)

        print(f"--- Group {seq}/{len(pending_groups)} ---")
        print(
            f"  Dimension: {group.get('proposed_dimension', '')}"
        )
        print(
            f"  Description: {group.get('description', '')}"
        )
        print(f"  Candidates: {live_count} live")

        # Show candidate details
        for cid in live_cids[:5]:
            c = cand_by_id.get(cid, {})
            print(f"    - {c.get('text', '')[:80]}")
        if live_count > 5:
            print(f"    ... and {live_count - 5} more")
        print()

        if live_count < _MIN_GROUP_SIZE:
            print(
                "  (insufficient evidence -- "
                f"need {_MIN_GROUP_SIZE}+ candidates)\n"
            )
            continue

        choice = _get_user_choice(
            "  [p]ropose / [r]eclassify to dim / "
            "[d]ismiss / [s]kip / [q]uit: ",
            {
                'p': 'propose', 'r': 'reclassify',
                'd': 'dismiss', 's': 'skip', 'q': 'quit',
            },
        )

        if choice is None or choice == 'quit':
            return modified

        if choice == 'propose':
            # Set all live candidates to proposed
            for cid in live_cids:
                if cid in cand_by_id:
                    cand_by_id[cid]['status'] = 'proposed'
            group['status'] = 'proposed'
            modified = True
            print(
                "  -> proposed (use --propose to "
                "generate bundle)\n"
            )

        elif choice == 'reclassify':
            try:
                target_dim = input(
                    "  Target dimension: "
                ).strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                return modified

            # Validate target dimension
            kw_dicts = config.get('keyword_dictionaries', {})
            dim_states = config.get('dimension_states', {})
            if target_dim not in kw_dicts:
                print(
                    f"  forge: error: dimension '{target_dim}' "
                    "not in keyword_dictionaries",
                    file=sys.stderr,
                )
                continue
            state = dim_states.get(target_dim, {})
            if state.get('status') == 'archived':
                print(
                    f"  forge: error: dimension '{target_dim}' "
                    "is archived",
                    file=sys.stderr,
                )
                continue

            # Reclassify all live candidates
            reclassified_count = 0
            all_keywords = set()
            for cid in live_cids:
                c = cand_by_id.get(cid)
                if c is None:
                    continue

                # Update external finding
                fid = c.get('finding_id')
                if fid and fid in ext_by_id:
                    ext_by_id[fid]['validated_dimension'] = (
                        target_dim
                    )
                    ext_by_id[fid]['gap'] = False

                # Collect keywords from the finding
                if fid and fid in ext_by_id:
                    kws = ext_by_id[fid].get(
                        'suggested_keywords', [],
                    )
                    all_keywords.update(kws)

                c['status'] = 'reclassified'
                reclassified_count += 1

                # Audit record in expansion queue
                exp_data.setdefault('expansions', []).append({
                    'id': f"exp-{uuid.uuid4()}",
                    'finding_id': c.get('finding_id'),
                    'created_at': now,
                    'proposed_dimension': target_dim,
                    'unmatched_text': c.get('text', ''),
                    'text_hash': c.get('text_hash', ''),
                    'suggested_keywords': list(all_keywords),
                    'status': 'approved',
                    'reclassified_to': None,
                })

            # Merge keywords
            existing_kw = set(kw_dicts.get(target_dim, []))
            existing_kw.update(all_keywords)
            kw_dicts[target_dim] = sorted(existing_kw)

            # Update dimension_states
            ensure_dimension_state(config, target_dim)
            ds = config['dimension_states'][target_dim]
            ds['finding_count'] = (
                ds.get('finding_count', 0) + reclassified_count
            )
            ds['last_seen'] = now

            # Update group
            remaining_cids = [
                cid for cid in group.get('candidate_ids', [])
                if cid in cand_by_id
                and cand_by_id[cid].get('status')
                not in GAP_TERMINAL
            ]
            group['candidate_ids'] = remaining_cids
            group['count'] = len(remaining_cids)
            if group['count'] == 0:
                group['status'] = 'dismissed'

            modified = True
            print(
                f"  -> reclassified {reclassified_count} "
                f"candidate(s) to '{target_dim}'\n"
            )

        elif choice == 'dismiss':
            # Dismiss all live candidates
            for cid in live_cids:
                if cid in cand_by_id:
                    cand_by_id[cid]['status'] = 'dismissed'
            group['status'] = 'dismissed'
            modified = True
            print("  -> dismissed\n")

        elif choice == 'skip':
            print("  -> skipped\n")

    return modified


# ---------------------------------------------------------------------------
# Main entry points
# ---------------------------------------------------------------------------

def run_gaps(approve_expansion_id=None):
    """Run the full --gaps interactive workflow.

    Orchestrates D5 steps 1-5:
    1. Sweep stale keyword expansions (>90 days)
    2. Sweep stale gap candidates (>180 days)
    3. Interactive keyword expansion review
    4-5. LLM gap grouping + group decisions

    If approve_expansion_id is provided, delegates to
    approve_expansion_noninteractive instead.

    Args:
        approve_expansion_id: Optional expansion ID for
            non-interactive approval.
    """
    if approve_expansion_id:
        approve_expansion_noninteractive(approve_expansion_id)
        return

    # Lazy imports to avoid circular dependencies
    from gap_detector import (
        load_external_findings,
        load_gap_candidates,
        load_keyword_expansion_queue,
        EXTERNAL_FINDINGS_FILE,
        GAP_CANDIDATES_FILE,
        KEYWORD_EXPANSION_FILE,
    )
    from forge_cli import load_config, CONFIG_FILE

    with _file_lock(_LOCK_FILE):
        # Load all data files
        ext_data = load_external_findings()
        gap_data = load_gap_candidates()
        exp_data = load_keyword_expansion_queue()
        config = load_config()

        now = datetime.now(timezone.utc)

        # Step 1: sweep stale expansions
        _sweep_stale_expansions(exp_data, ext_data, now)

        # Step 2: sweep stale candidates
        _sweep_stale_candidates(gap_data, now)

        # Step 3: interactive expansion review
        _review_expansions(exp_data, ext_data, gap_data, config)

        # Steps 4-5: LLM grouping
        gap_groups = _group_candidates(gap_data, ext_data)

        # Process groups (interactive)
        if gap_groups is not None:
            _process_groups(
                gap_data, ext_data, exp_data, config, gap_groups,
            )
            # Write gap groups
            atomic_write(GAP_GROUPS_FILE, gap_groups)

        # Write all modified data files
        atomic_write(EXTERNAL_FINDINGS_FILE, ext_data)
        atomic_write(GAP_CANDIDATES_FILE, gap_data)
        atomic_write(KEYWORD_EXPANSION_FILE, exp_data)
        atomic_write(CONFIG_FILE, config)

    print("forge: gap management complete")


def approve_expansion_noninteractive(expansion_id):
    """Non-interactively approve a single pending expansion.

    Same side effects as interactive approve: keywords added to
    config, external finding updated, dimension_states updated.

    Args:
        expansion_id: ID of the expansion to approve.
    """
    from gap_detector import (
        load_external_findings,
        load_keyword_expansion_queue,
        EXTERNAL_FINDINGS_FILE,
        KEYWORD_EXPANSION_FILE,
    )
    from forge_cli import load_config, CONFIG_FILE
    from migration import ensure_dimension_state

    ext_data = load_external_findings()
    exp_data = load_keyword_expansion_queue()
    config = load_config()

    # Find expansion by ID
    target = None
    for exp in exp_data.get('expansions', []):
        if exp.get('id') == expansion_id:
            target = exp
            break

    if target is None:
        print(
            f"forge: error: expansion '{expansion_id}' not found",
            file=sys.stderr,
        )
        sys.exit(1)

    if target.get('status') != 'pending':
        print(
            f"forge: error: expansion '{expansion_id}' is not "
            f"pending (status: {target.get('status')})",
            file=sys.stderr,
        )
        sys.exit(1)

    now = datetime.now(timezone.utc).isoformat()

    # Apply same side effects as interactive approve
    target['status'] = 'approved'

    dim = target.get('proposed_dimension', '')
    kw_dicts = config.setdefault('keyword_dictionaries', {})
    existing_kw = set(kw_dicts.get(dim, []))
    for kw in target.get('suggested_keywords', []):
        existing_kw.add(kw)
    kw_dicts[dim] = sorted(existing_kw)

    # Update external finding
    ext_by_id = {
        f['id']: f for f in ext_data.get('findings', [])
    }
    finding_id = target.get('finding_id')
    if finding_id and finding_id in ext_by_id:
        ext_by_id[finding_id]['validated_dimension'] = dim
        ext_by_id[finding_id]['gap'] = False

    # Update dimension_states
    ensure_dimension_state(config, dim)
    state = config['dimension_states'][dim]
    state['finding_count'] = state.get('finding_count', 0) + 1
    state['last_seen'] = now

    # Atomic write all
    atomic_write(EXTERNAL_FINDINGS_FILE, ext_data)
    atomic_write(KEYWORD_EXPANSION_FILE, exp_data)
    atomic_write(CONFIG_FILE, config)

    print(
        f"forge: approved expansion '{expansion_id}' "
        f"-> dimension '{dim}'"
    )


def run_reclassify(finding_id, target_dim):
    """Correct misclassification of an external finding.

    Implements all 5 side effects from D4 --reclassify spec:
    1. External finding: validated_dimension = target_dim, gap = False
    2. Derived entries (gap_candidates, keyword_expansion_queue):
       non-terminal entries with matching finding_id set to
       status='reclassified'. Terminal entries unchanged.
    3. suggested_keywords merged into target_dim's
       keyword_dictionaries (deduplicated)
    4. dimension_states[target_dim]: finding_count++, last_seen=now
       (auto-create if missing). dimension_states[old_dim]:
       finding_count-- (clamped to 0, skip if old_dim is
       'unknown' or None).
    5. Audit: write keyword_expansion_queue entry with
       status='approved'. Dedup key (proposed_dimension, text_hash):
       collision updates existing entry to 'approved'.

    Args:
        finding_id: ID of the external finding to reclassify.
        target_dim: Target dimension name.
    """
    from gap_detector import (
        load_external_findings,
        load_gap_candidates,
        load_keyword_expansion_queue,
        EXTERNAL_FINDINGS_FILE,
        GAP_CANDIDATES_FILE,
        KEYWORD_EXPANSION_FILE,
    )
    from forge_cli import load_config, CONFIG_FILE
    from migration import ensure_dimension_state

    ext_data = load_external_findings()
    gap_data = load_gap_candidates()
    exp_data = load_keyword_expansion_queue()
    config = load_config()

    # Validate target_dim
    kw_dicts = config.get('keyword_dictionaries', {})
    dim_states = config.get('dimension_states', {})

    if target_dim not in kw_dicts:
        print(
            f"forge: error: dimension '{target_dim}' "
            "not in keyword_dictionaries",
            file=sys.stderr,
        )
        sys.exit(1)

    state = dim_states.get(target_dim, {})
    if state.get('status') == 'archived':
        print(
            f"forge: error: dimension '{target_dim}' is archived",
            file=sys.stderr,
        )
        sys.exit(1)

    # Find external finding
    ext_finding = None
    for f in ext_data.get('findings', []):
        if f.get('id') == finding_id:
            ext_finding = f
            break

    if ext_finding is None:
        print(
            f"forge: error: finding '{finding_id}' not found",
            file=sys.stderr,
        )
        sys.exit(1)

    now = datetime.now(timezone.utc).isoformat()

    # Side effect 1: update external finding
    old_dim = ext_finding.get('validated_dimension')
    ext_finding['validated_dimension'] = target_dim
    ext_finding['gap'] = False

    # Side effect 2: update derived entries
    derived_found = False
    for candidate in gap_data.get('candidates', []):
        if (candidate.get('finding_id') == finding_id
                and candidate.get('status') not in GAP_TERMINAL):
            candidate['status'] = 'reclassified'
            derived_found = True

    for exp in exp_data.get('expansions', []):
        if (exp.get('finding_id') == finding_id
                and exp.get('status') not in EXPANSION_TERMINAL):
            exp['status'] = 'reclassified'
            derived_found = True

    if not derived_found:
        print(
            f"forge: warning: finding '{finding_id}' has no "
            "non-terminal derived entries in gap_candidates or "
            "keyword_expansion_queue",
            file=sys.stderr,
        )

    # Side effect 3: merge keywords
    suggested_kw = ext_finding.get('suggested_keywords', [])
    existing_kw = set(kw_dicts.get(target_dim, []))
    existing_kw.update(suggested_kw)
    kw_dicts[target_dim] = sorted(existing_kw)

    # Side effect 4: update dimension_states
    ensure_dimension_state(config, target_dim)
    target_state = config['dimension_states'][target_dim]
    target_state['finding_count'] = (
        target_state.get('finding_count', 0) + 1
    )
    target_state['last_seen'] = now

    # Decrement old dimension (skip if unknown/None)
    if old_dim and old_dim != 'unknown':
        if old_dim in config.get('dimension_states', {}):
            old_state = config['dimension_states'][old_dim]
            old_count = old_state.get('finding_count', 0)
            if old_count <= 0:
                print(
                    f"forge: warning: dimension '{old_dim}' "
                    "finding_count is already 0, cannot decrement",
                    file=sys.stderr,
                )
            else:
                old_state['finding_count'] = old_count - 1

    # Side effect 5: audit entry in expansion queue
    text_hash = ext_finding.get('text_hash', '')

    # Check for dedup collision
    collision = None
    for exp in exp_data.get('expansions', []):
        if (exp.get('proposed_dimension') == target_dim
                and exp.get('text_hash') == text_hash):
            collision = exp
            break

    if collision is not None:
        collision['status'] = 'approved'
    else:
        exp_data.setdefault('expansions', []).append({
            'id': f"exp-{uuid.uuid4()}",
            'finding_id': finding_id,
            'created_at': now,
            'proposed_dimension': target_dim,
            'unmatched_text': ext_finding.get('text', ''),
            'text_hash': text_hash,
            'suggested_keywords': suggested_kw,
            'status': 'approved',
            'reclassified_to': None,
        })

    # Atomic write all
    atomic_write(EXTERNAL_FINDINGS_FILE, ext_data)
    atomic_write(GAP_CANDIDATES_FILE, gap_data)
    atomic_write(KEYWORD_EXPANSION_FILE, exp_data)
    atomic_write(CONFIG_FILE, config)

    print(
        f"forge: reclassified '{finding_id}' "
        f"-> dimension '{target_dim}'"
    )
