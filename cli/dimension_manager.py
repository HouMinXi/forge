#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Dimension lifecycle -- proposals, add/promote/retire, eval extensions (D6/D7/D8/D9).

State machine for dimension status:

    pending -> proposed  (via run_propose)
    proposed -> shadow   (via add_dimension after PR merge)
    shadow -> active     (via promote_dimension)
    shadow -> archived   (via retire_dimension)
    active -> archived   (via retire_dimension)
    proposed -> archived (via reject -- TODO: not yet implemented)
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PROPOSAL_DIR = os.path.join('.forge', 'proposals')
_DIM_NAME_RE = re.compile(r'^[a-z0-9_]+$')
_VALID_STATUSES = {'pending', 'proposed', 'shadow', 'active', 'archived'}


def _set_status(state: dict, new_status: str) -> None:
    """Set dimension status with validation."""
    if new_status not in _VALID_STATUSES:
        raise ValueError("Invalid dimension status: '%s'" % new_status)
    state['status'] = new_status
_DIM_SANITIZE_RE = re.compile(r'[^a-z0-9_]')
_MAX_DIM_NAME_LEN = 40
_MAX_ACTIVE_DIMENSIONS = 20
_SHADOW_TIMEOUT_DAYS = 180
_PATCH_MIN_LENGTH_RATIO = 0.8
_SUBPROCESS_TIMEOUT_LOCAL = 30
_SUBPROCESS_TIMEOUT_NETWORK = 300
_LLM_MAX_RETRIES = 3
_LLM_RETRY_BASE_DELAY = 2
_TRICORDER_CRITERIA = [
    'Understandable',
    'Actionable',
    '<10% ToolFP',
    'Significant Impact',
]


# ---------------------------------------------------------------------------
# Proposal generation (D6)
# ---------------------------------------------------------------------------

def _sanitize_dim_name(raw_name, group_id):
    """Sanitize dimension name for directory naming.

    Lowercases, replaces non-[a-z0-9_] with underscore,
    truncates to 40 chars. Falls back to dim_{group_id[:8]}
    if empty after sanitization.
    """
    name = raw_name.lower()
    name = _DIM_SANITIZE_RE.sub('_', name)
    name = name.strip('_')
    name = name[:_MAX_DIM_NAME_LEN]
    if not name:
        name = 'dim_%s' % group_id[:8]
    return name


def _build_keywords(candidates, external_data):
    """Build deterministic keywords.json from candidate findings.

    Flat array from suggested_keywords of external findings
    referenced by candidates (via finding_id), deduplicated, sorted.
    """
    keywords = set()
    ext_map = {
        f['id']: f for f in external_data.get('findings', [])
    }
    for candidate in candidates:
        finding_id = candidate.get('finding_id')
        if finding_id and finding_id in ext_map:
            for kw in ext_map[finding_id].get(
                'suggested_keywords', [],
            ):
                keywords.add(str(kw).lower())
    return sorted(keywords)


def _validate_patch(patch_path, skill_md_path='skills/forge/SKILL.md'):
    """Post-edit validation of SKILL.md.patch (two-step).

    Step 1: git apply --check to verify patch applies cleanly.
    Step 2: Apply to temp copy, check headings and length.

    Returns (valid, error_message).
    """
    if not os.path.isfile(skill_md_path):
        return False, 'SKILL.md not found at %s' % skill_md_path

    # Step 1: git apply --check
    result = subprocess.run(
        ['git', 'apply', '--check', patch_path],
        capture_output=True, text=True, check=False,
        timeout=_SUBPROCESS_TIMEOUT_LOCAL,
    )
    if result.returncode != 0:
        return (
            False,
            'Patch does not apply cleanly: %s'
            % result.stderr.strip(),
        )

    # Step 2: apply to temp copy, validate result
    with open(skill_md_path, 'r', encoding='utf-8') as f:
        original_content = f.read()
    original_lines = original_content.split('\n')

    tmpdir = tempfile.mkdtemp()
    try:
        # Create matching directory structure
        skill_dir = os.path.join(tmpdir, 'skills', 'forge')
        os.makedirs(skill_dir, exist_ok=True)
        tmp_skill = os.path.join(skill_dir, 'SKILL.md')
        with open(tmp_skill, 'w', encoding='utf-8') as f:
            f.write(original_content)

        abs_patch = os.path.abspath(patch_path)
        result = subprocess.run(
            ['git', 'apply', abs_patch],
            cwd=tmpdir,
            capture_output=True, text=True, check=False,
            timeout=_SUBPROCESS_TIMEOUT_LOCAL,
        )
        if result.returncode != 0:
            return (
                False,
                'Patch apply failed in temp dir: %s'
                % result.stderr.strip(),
            )

        with open(tmp_skill, 'r', encoding='utf-8') as f:
            patched_content = f.read()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    # Check: at least one heading
    if '#' not in patched_content:
        return False, 'Patched SKILL.md has no headings'

    # Check: not shorter than minimum ratio
    patched_lines = patched_content.split('\n')
    if len(original_lines) > 0:
        ratio = len(patched_lines) / len(original_lines)
        if ratio < _PATCH_MIN_LENGTH_RATIO:
            return (
                False,
                'Patched SKILL.md is %.0f%% shorter than original '
                '(%.0f%% threshold)' % (
                    (1 - ratio) * 100,
                    (1 - _PATCH_MIN_LENGTH_RATIO) * 100,
                ),
            )

    return True, 'OK'


def _increment_edit_corruption():
    """Try to increment edit_corruption_count in escalation-status.json."""
    try:
        from escalation import load_escalation_status, ESCALATION_FILE
        from file_utils import atomic_write
        status = load_escalation_status()
        metrics = status.get('metrics', {})
        metrics['edit_corruption_count'] = (
            metrics.get('edit_corruption_count', 0) + 1
        )
        status['metrics'] = metrics
        atomic_write(ESCALATION_FILE, status)
    except ImportError as exc:
        msg = str(exc)
        if 'escalation' not in msg and 'file_utils' not in msg:
            print(
                "forge: warning: unexpected import error in "
                "_increment_edit_corruption: %s" % exc,
                file=sys.stderr,
            )


@contextmanager
def _git_branch_state():
    """Context manager that saves and restores git branch + stash state.

    On entry: saves current branch and stashes uncommitted changes.
    On exit (normal or exception): restores original branch and pops stash.
    """
    stashed = False
    original_branch = None
    try:
        result = subprocess.run(
            ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
            capture_output=True, text=True, check=True,
            timeout=_SUBPROCESS_TIMEOUT_LOCAL,
        )
        original_branch = result.stdout.strip()
        if original_branch == 'HEAD':
            result = subprocess.run(
                ['git', 'rev-parse', 'HEAD'],
                capture_output=True, text=True, check=True,
                timeout=_SUBPROCESS_TIMEOUT_LOCAL,
            )
            original_branch = result.stdout.strip()

        result = subprocess.run(
            ['git', 'status', '--porcelain'],
            capture_output=True, text=True, check=True,
            timeout=_SUBPROCESS_TIMEOUT_LOCAL,
        )
        if result.stdout.strip():
            print(
                "forge: warning: uncommitted changes detected, "
                "stashing before branch creation",
                file=sys.stderr,
            )
            subprocess.run(
                ['git', 'stash', '--include-untracked'],
                check=True,
                timeout=_SUBPROCESS_TIMEOUT_LOCAL,
            )
            stashed = True

        yield original_branch
    finally:
        if original_branch:
            subprocess.run(
                ['git', 'checkout', original_branch],
                capture_output=True, check=False,
                timeout=_SUBPROCESS_TIMEOUT_LOCAL,
            )
        if stashed:
            result = subprocess.run(
                ['git', 'stash', 'pop'],
                capture_output=True, text=True, check=False,
                timeout=_SUBPROCESS_TIMEOUT_LOCAL,
            )
            if result.returncode != 0:
                print(
                    "forge: warning: git stash pop failed: %s"
                    % result.stderr,
                    file=sys.stderr,
                )


def _run_pr_pipeline(
    dim_name: str,
    candidate_ids: List[str],
    description: str,
    keywords: List[str],
) -> bool:
    """PR pipeline (LEARN-06): branch, commit, push, PR.

    Returns True on success, False on failure (proposal files
    remain on disk for manual retry).
    """
    try:
        with _git_branch_state() as original_branch:
            # Step 1: branch
            branch_name = 'forge/dim-%s' % dim_name
            result = subprocess.run(
                ['git', 'rev-parse', '--verify', branch_name],
                capture_output=True, text=True, check=False,
                timeout=_SUBPROCESS_TIMEOUT_LOCAL,
            )
            if result.returncode == 0:
                subprocess.run(
                    ['git', 'checkout', branch_name],
                    check=True,
                    timeout=_SUBPROCESS_TIMEOUT_LOCAL,
                )
            else:
                subprocess.run(
                    ['git', 'checkout', '-b', branch_name],
                    check=True,
                    timeout=_SUBPROCESS_TIMEOUT_LOCAL,
                )

            # Step 2: stage proposal files
            proposal_dir = os.path.join(_PROPOSAL_DIR, dim_name)
            subprocess.run(
                ['git', 'add', '-f', '%s/' % proposal_dir],
                check=True,
                timeout=_SUBPROCESS_TIMEOUT_LOCAL,
            )

            # Step 3: check for staged changes
            result = subprocess.run(
                ['git', 'diff', '--cached', '--quiet'],
                capture_output=True, check=False,
                timeout=_SUBPROCESS_TIMEOUT_LOCAL,
            )
            if result.returncode != 0:
                commit_msg = (
                    'forge: propose new dimension %s\n\n'
                    'Based on %d external review findings '
                    'suggesting a gap in %s coverage.'
                    % (dim_name, len(candidate_ids), dim_name)
                )
                subprocess.run(
                    ['git', 'commit', '-m', commit_msg],
                    check=True,
                    timeout=_SUBPROCESS_TIMEOUT_LOCAL,
                )

            # Step 4: push
            subprocess.run(
                ['git', 'push', '-u', 'origin',
                 'forge/dim-%s' % dim_name],
                check=True,
                timeout=_SUBPROCESS_TIMEOUT_NETWORK,
            )

            # Step 5: create PR
            body_text = (
                "## Proposed Dimension: %s\n\n"
                "**Source:** %d gap candidates from external "
                "review feedback\n"
                "**Description:** %s\n\n"
                "### Proposal Contents\n"
                "- `SKILL.md.patch` -- adds dimension to review "
                "checklist\n"
                "- `evidence.md` -- gap analysis with source "
                "findings\n"
                "- `seed_test.diff` -- synthetic test for the "
                "dimension\n"
                "- `keywords.json` -- keyword dictionary "
                "(%d keywords)\n\n"
                "### After Merge\n"
                "```\nforge --add-dimension %s "
                "--keywords-file .forge/proposals/%s/"
                "keywords.json\n"
                "```\n"
                % (
                    dim_name, len(candidate_ids), description,
                    len(keywords), dim_name, dim_name,
                )
            )
            subprocess.run(
                ['gh', 'pr', 'create',
                 '--title',
                 'forge: add dimension %s' % dim_name,
                 '--body', body_text],
                check=True,
                timeout=_SUBPROCESS_TIMEOUT_NETWORK,
            )

            return True

    except FileNotFoundError as exc:
        tool = str(exc).split("'")[-2] if "'" in str(exc) else ''
        if 'gh' in tool:
            print(
                "forge: warning: gh CLI not found -- proposal "
                "written locally, create PR manually",
                file=sys.stderr,
            )
            return True
        if 'git' in tool:
            print(
                "forge: warning: git not found -- proposal "
                "written locally only",
                file=sys.stderr,
            )
            return True
        print(
            "forge: error: PR pipeline failed: %s" % exc,
            file=sys.stderr,
        )
        return False

    except subprocess.CalledProcessError as exc:
        step_name = 'unknown'
        cmd = getattr(exc, 'cmd', [])
        if isinstance(cmd, list) and len(cmd) > 0:
            step_name = cmd[0]
        print(
            "forge: error: PR pipeline failed at step %s: %s"
            % (step_name, exc),
            file=sys.stderr,
        )
        return False


def _validate_group_for_proposal(
    group_id: str,
) -> Tuple[dict, dict, List[dict]]:
    """Validate group exists and is pending for proposal.

    Returns (groups_data, group, groups_list).
    Exits with error if validation fails.
    """
    from gap_manager import load_gap_groups

    groups_data = load_gap_groups()
    groups = groups_data.get('groups', [])

    group = None
    for g in groups:
        if g.get('group_id') == group_id:
            group = g
            break
    if group is None:
        print(
            "forge: error: group '%s' not found in "
            "gap_groups.json" % group_id,
            file=sys.stderr,
        )
        sys.exit(1)

    if group.get('status') != 'pending':
        print(
            "forge: error: group '%s' has status '%s' "
            "(expected 'pending')"
            % (group_id, group.get('status')),
            file=sys.stderr,
        )
        sys.exit(1)

    return groups_data, group, groups


def _build_proposal_prompt(
    dim_name: str,
    description: str,
    source_findings: List[dict],
) -> str:
    """Build LLM prompt for proposal generation."""
    return (
        "Generate a proposal bundle for a new code review "
        "dimension.\n\n"
        "Dimension name: %s\n"
        "Description: %s\n\n"
        "Source findings:\n%s\n\n"
        "Return ONLY valid JSON with these 4 keys:\n"
        "- skill_md_patch: unified diff "
        "(--- a/skills/forge/SKILL.md / "
        "+++ b/skills/forge/SKILL.md) "
        "adding the dimension as a new numbered checklist "
        "item under the appropriate section\n"
        "- evidence_md: markdown document with # Evidence "
        "heading and ## Finding N subsections\n"
        "- seed_test_diff: unified diff "
        "(--- /dev/null / "
        "+++ b/tests/seed_tests/seed_diffs/%s.diff) "
        "adding a synthetic test case\n"
        "- readme_md: markdown with # Proposed Dimension "
        "heading, description, keyword list, and usage "
        "instructions\n\n"
        "Return ONLY valid JSON. No markdown fences, "
        "no explanation."
        % (
            dim_name, description,
            json.dumps(source_findings, indent=2),
            dim_name,
        )
    )


def _write_proposal_bundle(
    tmp_dir: str,
    proposal_data: dict,
    keywords: List[str],
) -> None:
    """Write proposal files to tmp_dir.

    Writes SKILL.md.patch, evidence.md, seed_test.diff,
    README.md (from proposal_data), and keywords.json.
    """
    os.makedirs(tmp_dir, exist_ok=True)

    file_map = {
        'SKILL.md.patch': proposal_data.get('skill_md_patch'),
        'evidence.md': proposal_data.get('evidence_md'),
        'seed_test.diff': proposal_data.get('seed_test_diff'),
        'README.md': proposal_data.get('readme_md'),
    }
    for filename, content in file_map.items():
        if content is None:
            print(
                "forge: warning: LLM response missing '%s' key"
                % filename,
                file=sys.stderr,
            )
            continue
        fpath = os.path.join(tmp_dir, filename)
        with open(fpath, 'w', encoding='utf-8') as f:
            f.write(content)

    kw_path = os.path.join(tmp_dir, 'keywords.json')
    with open(kw_path, 'w', encoding='utf-8') as f:
        json.dump(keywords, f, indent=2, ensure_ascii=False)


def _finalize_proposal_state(
    candidates_data: dict,
    groups_data: dict,
    candidate_ids: List[str],
    group: dict,
) -> None:
    """Update candidates and group status to proposed."""
    from gap_manager import GAP_GROUPS_FILE
    from gap_detector import GAP_CANDIDATES_FILE
    from file_utils import atomic_write

    candidates = candidates_data.get('candidates', [])
    for c in candidates:
        if c.get('id') in candidate_ids:
            c['status'] = 'proposed'
    atomic_write(GAP_CANDIDATES_FILE, candidates_data)

    group['status'] = 'proposed'
    atomic_write(GAP_GROUPS_FILE, groups_data)


def run_propose(group_id: str) -> None:
    """Generate proposal bundle from a gap group (D6).

    Validates group exists, is pending, and has >= 3 non-terminal
    candidates. Calls LLM for proposal generation, writes to
    .forge/proposals/<dim>/, runs PR pipeline, then marks
    candidates and group as proposed.
    """
    from gap_manager import GAP_TERMINAL
    from gap_detector import (
        load_external_findings, load_gap_candidates,
    )
    from forge_cli import load_config

    # Validate group
    groups_data, group, _groups = _validate_group_for_proposal(
        group_id,
    )

    # Load candidates and re-check live count
    candidates_data = load_gap_candidates()
    candidates = candidates_data.get('candidates', [])
    candidate_ids = group.get('candidate_ids', [])

    live_candidates = [
        c for c in candidates
        if c.get('id') in candidate_ids
        and c.get('status') not in GAP_TERMINAL
    ]
    if len(live_candidates) < 3:
        print(
            "forge: error: group '%s' has %d non-terminal "
            "candidates (need >= 3)"
            % (group_id, len(live_candidates)),
            file=sys.stderr,
        )
        sys.exit(1)

    # Load external findings for candidate data
    external_data = load_external_findings()
    ext_map = {
        f['id']: f for f in external_data.get('findings', [])
    }

    # Validate finding_ids exist, skip missing
    valid_candidates = []
    for c in live_candidates:
        fid = c.get('finding_id')
        if fid and fid in ext_map:
            valid_candidates.append(c)
        else:
            print(
                "forge: warning: candidate '%s' has missing "
                "finding_id '%s', skipping"
                % (c.get('id'), fid),
                file=sys.stderr,
            )
    if len(valid_candidates) < 3:
        print(
            "forge: error: only %d candidates have valid "
            "finding_ids (need >= 3)"
            % len(valid_candidates),
            file=sys.stderr,
        )
        sys.exit(1)

    proposed_dimension = group.get(
        'proposed_dimension', 'unknown',
    )
    dim_name = _sanitize_dim_name(proposed_dimension, group_id)
    description = group.get('description', '')

    # Build source findings for LLM prompt
    source_findings = []
    for c in valid_candidates:
        ext = ext_map[c['finding_id']]
        source_findings.append({
            'text': ext.get('text', ''),
            'dimension_raw': ext.get('dimension_raw', ''),
            'suggested_keywords': ext.get(
                'suggested_keywords', [],
            ),
            'file': ext.get('file'),
            'line': ext.get('line'),
        })

    keywords = _build_keywords(valid_candidates, external_data)

    config = load_config()

    # Build prompt
    prompt = _build_proposal_prompt(
        proposed_dimension, description, source_findings,
    )

    # Call LLM with retry on transient failures
    proposal_data = None
    from llm_parser import _get_client, _parse_json_response
    client = _get_client()
    if client is None:
        print(
            "forge: error: LLM client not available for "
            "proposal generation",
            file=sys.stderr,
        )
        sys.exit(1)

    model = config.get('default_model', 'claude-haiku-3.5')
    last_exc = None
    for attempt in range(_LLM_MAX_RETRIES):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=40960,
                messages=[
                    {"role": "user", "content": prompt},
                ],
            )
            response_text = response.content[0].text
            proposal_data = _parse_json_response(response_text)
            if proposal_data is not None:
                break
        except Exception as exc:
            last_exc = exc
            if attempt < _LLM_MAX_RETRIES - 1:
                delay = _LLM_RETRY_BASE_DELAY * (2 ** attempt)
                print(
                    "forge: warning: LLM call attempt %d/%d "
                    "failed: %s, retrying in %ds"
                    % (
                        attempt + 1, _LLM_MAX_RETRIES,
                        exc, delay,
                    ),
                    file=sys.stderr,
                )
                time.sleep(delay)

    if proposal_data is None:
        msg = str(last_exc) if last_exc else 'parse failure'
        print(
            "forge: error: LLM proposal generation failed "
            "after %d attempts: %s"
            % (_LLM_MAX_RETRIES, msg),
            file=sys.stderr,
        )
        sys.exit(1)

    # Write proposal files with crash safety
    tmp_dir = os.path.join(
        _PROPOSAL_DIR, '.tmp-%s' % dim_name,
    )
    final_dir = os.path.join(_PROPOSAL_DIR, dim_name)

    _write_proposal_bundle(tmp_dir, proposal_data, keywords)

    # Post-edit validation of SKILL.md.patch
    patch_path = os.path.join(tmp_dir, 'SKILL.md.patch')
    if os.path.isfile(patch_path):
        valid, err_msg = _validate_patch(patch_path)
        if not valid:
            _increment_edit_corruption()
            shutil.rmtree(tmp_dir, ignore_errors=True)
            print(
                "forge: error: SKILL.md.patch validation "
                "failed: %s" % err_msg,
                file=sys.stderr,
            )
            sys.exit(1)

    # Crash-safe rename: remove existing, rename tmp
    try:
        shutil.rmtree(final_dir)
    except FileNotFoundError:
        pass
    try:
        os.rename(tmp_dir, final_dir)
    except OSError:
        shutil.rmtree(final_dir, ignore_errors=True)
        try:
            os.rename(tmp_dir, final_dir)
        except OSError as exc:
            print(
                "forge: error: failed to rename proposal dir "
                f"after retry: {exc}",
                file=sys.stderr,
            )
            sys.exit(1)

    # PR pipeline (LEARN-06)
    pr_success = _run_pr_pipeline(
        dim_name, candidate_ids, description, keywords,
    )

    # Mark candidates and group as proposed
    _finalize_proposal_state(
        candidates_data, groups_data, candidate_ids, group,
    )

    if pr_success:
        print(
            "forge: proposal written to .forge/proposals/%s/ "
            "and PR created" % dim_name,
        )
    else:
        print(
            "forge: proposal written to .forge/proposals/%s/ "
            "(PR creation failed -- retry manually)" % dim_name,
        )


# ---------------------------------------------------------------------------
# Dimension lifecycle (D7/D8)
# ---------------------------------------------------------------------------

def add_dimension(
    dim_name: str,
    keywords_file: Optional[str] = None,
) -> None:
    """Register a new shadow dimension (D7).

    Validates name format, checks for conflicts (archived or
    existing), reads keywords from file, creates dimension_states
    entry, and optionally runs seed test.
    """
    from forge_cli import load_config, CONFIG_FILE
    from file_utils import atomic_write

    if not _DIM_NAME_RE.match(dim_name):
        print(
            "forge: error: dimension name must match "
            "^[a-z0-9_]+$ (got '%s')" % dim_name,
            file=sys.stderr,
        )
        sys.exit(1)

    config = load_config()

    # Check conflicts
    dim_states = config.get('dimension_states', {})
    kw_dicts = config.get('keyword_dictionaries', {})

    if dim_name in kw_dicts:
        state = dim_states.get(dim_name, {})
        if state.get('status') == 'archived':
            print(
                "forge: error: dimension '%s' is archived; "
                "remove manually from config.json to reuse name"
                % dim_name,
                file=sys.stderr,
            )
            sys.exit(1)
        print(
            "forge: error: dimension '%s' already exists"
            % dim_name,
            file=sys.stderr,
        )
        sys.exit(1)

    # Read keywords from file
    keywords = []
    if keywords_file:
        try:
            with open(keywords_file, 'r', encoding='utf-8') as f:
                keywords = json.load(f)
            if not isinstance(keywords, list):
                print(
                    "forge: error: keywords file must contain a "
                    "JSON array (got %s)"
                    % type(keywords).__name__,
                    file=sys.stderr,
                )
                sys.exit(1)
        except FileNotFoundError:
            print(
                "forge: error: keywords file not found: %s"
                % keywords_file,
                file=sys.stderr,
            )
            sys.exit(1)
        except json.JSONDecodeError as exc:
            print(
                "forge: error: invalid JSON in keywords "
                "file %s: %s" % (keywords_file, exc),
                file=sys.stderr,
            )
            sys.exit(1)

    # Add to keyword_dictionaries
    if 'keyword_dictionaries' not in config:
        config['keyword_dictionaries'] = {}
    config['keyword_dictionaries'][dim_name] = keywords

    # Create dimension_states entry
    now = datetime.now(timezone.utc).isoformat()
    if 'dimension_states' not in config:
        config['dimension_states'] = {}
    config['dimension_states'][dim_name] = {
        "status": "shadow",
        "last_seen": None,
        "finding_count": 0,
        "added_at": now,
        "consecutive_eval_failures": None,
        "seed_test_status": None,
    }

    # Write config FIRST (before seed test subprocess)
    atomic_write(CONFIG_FILE, config)

    # Run seed test if proposal seed_test.diff exists
    seed_diff = os.path.join(
        _PROPOSAL_DIR, dim_name, 'seed_test.diff',
    )
    if os.path.isfile(seed_diff):
        seed_tests_path = os.path.join(
            'tests', 'seed_tests', 'run_seed_tests.py',
        )
        try:
            subprocess.run(
                [
                    sys.executable, seed_tests_path,
                    '--dimension', dim_name,
                    '--diff', seed_diff,
                ],
                check=False,
                timeout=_SUBPROCESS_TIMEOUT_LOCAL,
            )
        except KeyboardInterrupt:
            raise
        except (OSError, subprocess.SubprocessError) as exc:
            print(
                "forge: warning: seed test failed: %s" % exc,
                file=sys.stderr,
            )

    print(
        "forge: added shadow dimension '%s' (%d keywords)"
        % (dim_name, len(keywords)),
    )


def _get_permanent_shadow_dims() -> set:
    """Return the set of permanent shadow dimension names.

    Imports SHADOW_DIMENSIONS from migration module as the
    single source; falls back to empty set on import failure.
    """
    try:
        from migration import SHADOW_DIMENSIONS
        return set(SHADOW_DIMENSIONS)
    except (ImportError, AttributeError):
        return set()


def promote_dimension(dim_name: str) -> None:
    """Promote shadow dimension to active (D7/D8).

    Validates dimension exists in dimension_states, is shadow
    status, not a permanent shadow, and active count < 20.
    Updates findings.json shadow flags and config.
    """
    from forge_cli import (
        load_config, CONFIG_FILE,
        load_findings, FINDINGS_FILE,
    )
    from file_utils import atomic_write

    config = load_config()
    dim_states = config.get('dimension_states', {})

    if dim_name not in dim_states:
        print(
            "forge: error: dimension '%s' not found in "
            "dimension_states" % dim_name,
            file=sys.stderr,
        )
        sys.exit(1)

    # Check permanent shadow dimensions (single source)
    permanent_shadow = _get_permanent_shadow_dims()

    if dim_name in permanent_shadow:
        print(
            "forge: error: '%s' is a permanent shadow dimension "
            "and cannot be promoted" % dim_name,
            file=sys.stderr,
        )
        sys.exit(1)

    state = dim_states[dim_name]
    status = state.get('status')

    if status == 'active':
        print(
            "forge: error: dimension '%s' is already active"
            % dim_name,
            file=sys.stderr,
        )
        sys.exit(1)
    if status == 'archived':
        print(
            "forge: error: dimension '%s' is archived and "
            "cannot be promoted" % dim_name,
            file=sys.stderr,
        )
        sys.exit(1)
    if status != 'shadow':
        print(
            "forge: error: dimension '%s' has unexpected "
            "status '%s'" % (dim_name, status),
            file=sys.stderr,
        )
        sys.exit(1)

    # Check 20-cap
    active_count = sum(
        1 for s in dim_states.values()
        if s.get('status') == 'active'
    )
    if active_count >= _MAX_ACTIVE_DIMENSIONS:
        print(
            "forge: error: active dimension cap reached "
            "(%d/%d); review with 'forge --eval' and retire "
            "underperforming dimensions first"
            % (active_count, _MAX_ACTIVE_DIMENSIONS),
            file=sys.stderr,
        )
        print("\nActive dimensions:", file=sys.stderr)
        for name, s in sorted(dim_states.items()):
            if s.get('status') == 'active':
                print("  - %s" % name, file=sys.stderr)
        sys.exit(1)

    # Set status to active
    dim_states[dim_name]['status'] = 'active'

    # Update findings.json: shadow=False for promoted dim
    data = load_findings()
    findings = data.get('findings', [])
    promoted = 0
    for f in findings:
        if (f.get('dimension') == dim_name
                and f.get('shadow', False)):
            f['shadow'] = False
            promoted += 1
    if promoted > 0:
        atomic_write(FINDINGS_FILE, data)

    # Write config
    atomic_write(CONFIG_FILE, config)

    print(
        "forge: promoted '%s' from shadow to active "
        "(%d findings updated)" % (dim_name, promoted),
    )


def retire_dimension(dim_name: str) -> None:
    """Archive a dimension -- active or shadow (D7/D9).

    Sets status to archived. No-op if already archived.
    """
    from forge_cli import load_config, CONFIG_FILE
    from file_utils import atomic_write

    config = load_config()
    dim_states = config.get('dimension_states', {})

    if dim_name not in dim_states:
        print(
            "forge: error: dimension '%s' not found in "
            "dimension_states" % dim_name,
            file=sys.stderr,
        )
        sys.exit(1)

    state = dim_states[dim_name]
    if state.get('status') == 'archived':
        print(
            "forge: dimension '%s' is already archived"
            % dim_name,
        )
        return

    state['status'] = 'archived'
    atomic_write(CONFIG_FILE, config)

    print("forge: retired dimension '%s' (archived)" % dim_name)


# ---------------------------------------------------------------------------
# Eval extensions (D7/D9)
# ---------------------------------------------------------------------------

def eval_shadow(include_archived=False):
    """Interactive shadow dimension evaluation (Tricorder 4 criteria).

    For each shadow dimension: presents findings, prompts user
    for pass/fail on each criterion. Auto-archives at 2
    consecutive failures.
    """
    from forge_cli import (
        load_config, load_findings, CONFIG_FILE,
    )
    from file_utils import atomic_write
    from gap_detector import load_external_findings

    config = load_config()
    dim_states = config.get('dimension_states', {})
    findings_data = load_findings()
    internal_findings = findings_data.get('findings', [])
    external_data = load_external_findings()
    external_findings = external_data.get('findings', [])

    modified = False

    # Filter dimensions to evaluate
    dims_to_eval = []
    for dim_name, state in sorted(dim_states.items()):
        status = state.get('status')
        if status == 'shadow':
            dims_to_eval.append((dim_name, state, False))
        elif (include_archived and status == 'archived'
              and state.get('added_at') is not None):
            dims_to_eval.append((dim_name, state, True))

    if not dims_to_eval:
        print("forge: no shadow dimensions to evaluate")
        return

    print("=" * 70)
    print("Forge Shadow Dimension Evaluation (Tricorder 4 Criteria)")
    print("=" * 70)
    print()

    for dim_name, state, read_only in dims_to_eval:
        # Gather findings
        int_finds = [
            f for f in internal_findings
            if f.get('dimension') == dim_name
        ]
        ext_finds = [
            f for f in external_findings
            if f.get('validated_dimension') == dim_name
        ]

        total = len(int_finds) + len(ext_finds)
        print(
            "--- %s (status: %s, findings: %d) ---"
            % (dim_name, state.get('status'), total),
        )

        if int_finds:
            print("  Internal findings: %d" % len(int_finds))
            for f in int_finds[:5]:
                print(
                    "    - [%s] %s"
                    % (f.get('file', '?'), f.get('text', '')[:60]),
                )
            if len(int_finds) > 5:
                print("    ... and %d more" % (len(int_finds) - 5))

        if ext_finds:
            print("  External findings: %d" % len(ext_finds))
            for f in ext_finds[:5]:
                print(
                    "    - [%s] %s"
                    % (f.get('file', '?'), f.get('text', '')[:60]),
                )
            if len(ext_finds) > 5:
                print("    ... and %d more" % (len(ext_finds) - 5))

        print()

        if read_only:
            print("  (archived -- read-only, no evaluation)")
            print()
            continue

        # Interactive Tricorder evaluation
        all_passed = True
        for criterion in _TRICORDER_CRITERIA:
            while True:
                try:
                    choice = input(
                        "  %s? [p]ass / [f]ail: " % criterion,
                    ).strip().lower()
                except (EOFError, KeyboardInterrupt):
                    print()
                    if modified:
                        atomic_write(CONFIG_FILE, config)
                        print(
                            "forge: saved evaluation state",
                        )
                    return

                if choice in ('p', 'pass'):
                    print("    -> passed")
                    break
                if choice in ('f', 'fail'):
                    all_passed = False
                    print("    -> failed")
                    break
                print("    Invalid choice. Use p/f.")

        # Update state
        if all_passed:
            state['consecutive_eval_failures'] = 0
            print(
                "  Passed -- run --promote %s to activate."
                % dim_name,
            )
        else:
            failures = state.get('consecutive_eval_failures')
            if failures is None:
                failures = 1
            else:
                failures += 1
            state['consecutive_eval_failures'] = failures

            if failures >= 2:
                state['status'] = 'archived'
                print(
                    "  Auto-archived: %s (2 consecutive "
                    "evaluation failures)" % dim_name,
                )
            else:
                print(
                    "  Failed (%d consecutive failure(s))"
                    % failures,
                )
        modified = True
        print()

    if modified:
        atomic_write(CONFIG_FILE, config)
        print("forge: evaluation complete")


def eval_external(include_archived=False, json_format=False):
    """Show external findings (D9).

    By default excludes findings whose validated_dimension maps
    to an archived dimension. Sorts by timestamp descending.
    """
    from forge_cli import load_config
    from gap_detector import load_external_findings

    config = load_config()
    dim_states = config.get('dimension_states', {})
    external_data = load_external_findings()
    findings = external_data.get('findings', [])

    # Filter archived if needed
    if not include_archived:
        filtered = []
        for f in findings:
            vd = f.get('validated_dimension')
            if vd and vd in dim_states:
                if dim_states[vd].get('status') == 'archived':
                    continue
            filtered.append(f)
        findings = filtered

    # Sort by timestamp descending
    findings.sort(
        key=lambda f: f.get('timestamp', ''),
        reverse=True,
    )

    if json_format:
        print(json.dumps(findings, indent=2, ensure_ascii=False))
        return

    if not findings:
        print("forge: no external findings to display")
        return

    # Terminal table display
    print("=" * 100)
    print("External Findings")
    print("=" * 100)
    print()
    header = (
        "%-12s %-20s %-12s %-12s %-15s %-4s %s"
        % (
            'ID', 'Timestamp', 'Source', 'Tool',
            'Dimension', 'Gap', 'Text',
        )
    )
    print(header)
    print("-" * 100)

    for f in findings:
        fid = f.get('id', '')[:10]
        ts = f.get('timestamp', '')[:19]
        src = f.get('source', '')[:10]
        tool = f.get('source_tool', '')[:10]
        dim = (f.get('validated_dimension') or 'unknown')[:13]
        gap = 'Y' if f.get('gap') else 'N'
        text = (f.get('text') or '')[:40]
        print(
            "%-12s %-20s %-12s %-12s %-15s %-4s %s"
            % (fid, ts, src, tool, dim, gap, text),
        )

    print("-" * 100)
    print("Total: %d findings" % len(findings))
    print()


# ---------------------------------------------------------------------------
# Shadow timeout check (D7)
# ---------------------------------------------------------------------------

def check_shadow_timeouts(config):
    """Check shadow dimension timeouts (180 days from added_at).

    Called at end of --learn and --eval. Auto-archives zero-finding
    shadow dimensions past timeout. Returns list of auto-archived
    dimension names.
    """
    # Permanent shadow dimensions are never auto-archived
    permanent_shadow = _get_permanent_shadow_dims()

    dim_states = config.get('dimension_states', {})
    now = datetime.now(timezone.utc)
    archived = []

    for dim_name, state in list(dim_states.items()):
        if state.get('status') != 'shadow':
            continue

        if dim_name in permanent_shadow:
            continue

        added_at = state.get('added_at')
        if added_at is None:
            continue

        try:
            added_at_dt = datetime.fromisoformat(added_at)
        except (ValueError, TypeError):
            continue

        # Make timezone-aware if needed
        if added_at_dt.tzinfo is None:
            added_at_dt = added_at_dt.replace(tzinfo=timezone.utc)

        if (now - added_at_dt) <= timedelta(
            days=_SHADOW_TIMEOUT_DAYS,
        ):
            continue

        finding_count = state.get('finding_count', 0)
        failures = state.get('consecutive_eval_failures')

        if finding_count >= 20:
            print(
                "forge: warning: shadow dimension '%s' is "
                "past 180-day timeout with %d findings -- "
                "run 'forge --eval --shadow' to evaluate"
                % (dim_name, finding_count),
                file=sys.stderr,
            )
        elif finding_count > 0 and failures is None:
            print(
                "forge: warning: shadow dimension '%s' is "
                "past 180-day timeout with %d findings "
                "(never evaluated) -- "
                "run 'forge --eval --shadow' for early "
                "evaluation"
                % (dim_name, finding_count),
                file=sys.stderr,
            )
        elif finding_count == 0:
            state['status'] = 'archived'
            archived.append(dim_name)

    return archived
