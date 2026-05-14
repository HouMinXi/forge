#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Seed test runner for zero-data dimensions (D1).

Runs each synthetic diff through forge review and verifies the target
dimension produces a finding. Dimensions that fail indicate SKILL.md
prompt gaps that need improvement before adding new dimensions.

Usage:
    python3 tests/seed_tests/run_seed_tests.py [--dry-run]
    python3 tests/seed_tests/run_seed_tests.py --dimension DIM --diff PATH

    --dry-run: validate diff files exist and are parseable without
               invoking LLM (zero cost).
    --dimension DIM --diff PATH: run seed test for a specific
               proposal-generated dimension.
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import tempfile

# Note: UUID4 collision risk is negligible for CLI use.

# Map diff filename stem to expected target dimension
# R5 fix: target_dimension names match VALID_DIMENSIONS in SKILL.md
SEED_TESTS = {
    'performance_unbounded_loop': {
        'target_dimension': 'performance',
        'description': (
            'N+1 query, removed LIMIT, unbounded memory'
        ),
    },
    'concurrency_unsynchronized': {
        'target_dimension': 'concurrency',
        'description': (
            'Dict mutation from background thread without lock'
        ),
    },
    'error_handling_missing': {
        'target_dimension': 'error_handling',
        'description': (
            'File I/O without try/except, no resource cleanup'
        ),
    },
    'api_contract_break': {
        'target_dimension': 'api_contract',
        'description': (
            'Renamed response fields without API versioning'
        ),
    },
    'graceful_degradation_crash': {
        'target_dimension': 'graceful_degradation',
        'description': (
            'Hard dependency on optional library, no ImportError'
        ),
    },
    'test_quality_mock_only': {
        'target_dimension': 'test_quality',
        'description': (
            'Tests assert only on mock return values'
        ),
    },
    'ai_code_smell_drift': {
        'target_dimension': 'ai_code_smell',
        'description': (
            'Repeated identical validation pattern drift'
        ),
    },
}

# R8 fix: path to forge_cli.py relative to this script
FORGE_CLI = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        '..', '..', 'cli', 'forge_cli.py',
    )
)


def find_seed_diffs(seed_dir):
    """Discover seed diff files in the seed_diffs directory."""
    pattern = os.path.join(seed_dir, 'seed_diffs', '*.diff')
    return sorted(glob.glob(pattern))


def validate_diff(filepath):
    """Check that a diff file looks like valid unified diff."""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    if '--- a/' not in content or '+++ b/' not in content:
        return False, 'Not a valid unified diff format'
    if '@@' not in content:
        return False, 'No hunk headers found'
    # R2 fix: check for before-state block
    if '---BEGIN BEFORE---' not in content:
        return False, 'Missing ---BEGIN BEFORE--- block (R2 fix)'
    if '---END BEFORE---' not in content:
        return False, 'Missing ---END BEFORE--- block (R2 fix)'
    return True, 'OK'


def _parse_before_state(diff_content):
    """R2 fix: extract before-state files from diff content.

    Returns list of (filepath, content) tuples.

    TODO: _parse_before_state and _parse_after_state need dedicated
    unit tests (H14). Add tests covering: empty diff, multi-file
    diffs, missing BEFORE block, malformed hunk headers.
    """
    before_files = []
    m = re.search(
        r'---BEGIN BEFORE---\n(.*?)---END BEFORE---',
        diff_content, re.DOTALL,
    )
    if not m:
        return before_files
    block = m.group(1)
    # First non-empty line is the file path
    lines = block.split('\n')
    filepath = None
    content_lines = []
    for line in lines:
        if filepath is None:
            if line.strip():
                filepath = line.strip()
        else:
            content_lines.append(line)
    if filepath:
        content = '\n'.join(content_lines)
        # Strip trailing newline added by split
        if content.endswith('\n\n'):
            content = content[:-1]
        before_files.append((filepath, content))
    return before_files


def _parse_after_state(diff_content, before_files):
    """R2 fix: apply diff to before-state to produce after-state.

    Parses unified diff hunks and applies changes to produce
    the after-state content.
    """
    after_files = []
    for filepath, before_content in before_files:
        before_lines = before_content.split('\n')

        # Extract hunks for this file
        diff_section = re.search(
            r'---\s+a/' + re.escape(filepath) + r'.*?'
            r'(?=---\s+a/|\Z)',
            diff_content, re.DOTALL,
        )
        if not diff_section:
            after_files.append((filepath, before_content))
            continue

        # Parse hunks
        hunk_pattern = re.compile(
            r'@@ -(\d+),?\d* \+(\d+),?\d* @@.*'
        )
        hunks = []
        current_hunk = None
        for line in diff_section.group().split('\n'):
            hm = hunk_pattern.match(line)
            if hm:
                if current_hunk:
                    hunks.append(current_hunk)
                current_hunk = {
                    'old_start': int(hm.group(1)),
                    'lines': [],
                }
            elif current_hunk is not None:
                if (line.startswith('+')
                        or line.startswith('-')
                        or line.startswith(' ')):
                    current_hunk['lines'].append(line)
        if current_hunk:
            hunks.append(current_hunk)

        # Apply hunks in reverse order to preserve line numbers
        result = list(before_lines)
        for hunk in reversed(hunks):
            old_start = hunk['old_start'] - 1  # 0-indexed
            # Count lines to remove (context + removed)
            remove_count = sum(
                1 for ln in hunk['lines']
                if ln.startswith('-') or ln.startswith(' ')
            )
            # Build new lines (context + added)
            new_lines = []
            for ln in hunk['lines']:
                if ln.startswith('+'):
                    new_lines.append(ln[1:])
                elif ln.startswith(' '):
                    new_lines.append(ln[1:])
            result[old_start:old_start + remove_count] = new_lines

        after_files.append((filepath, '\n'.join(result)))
    return after_files


def run_seed_test(diff_path, target_dim, description):
    """R2 fix: run a single seed test using before/after reconstruction.

    Instead of git apply (which fails on placeholder files), this:
    1. Parses before-state from the diff file
    2. Commits the before-state
    3. Parses the diff to construct after-state
    4. Writes after-state and commits
    5. Runs forge review on HEAD~1

    Returns (passed, details) tuple.
    """
    with open(diff_path, 'r', encoding='utf-8') as f:
        diff_content = f.read()

    before_files = _parse_before_state(diff_content)
    if not before_files:
        return (
            False,
            'Failed to parse before-state from diff file',
        )

    after_files = _parse_after_state(diff_content, before_files)

    with tempfile.TemporaryDirectory() as tmpdir:
        # Initialize git repo (no git config writes -- use -c flags
        # on commit commands to avoid polluting user/system config)
        subprocess.run(
            ['git', 'init'], cwd=tmpdir,
            capture_output=True, check=True,
        )

        # R2 fix: write BEFORE-STATE files (not placeholders)
        for fpath, content in before_files:
            full = os.path.join(tmpdir, fpath)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, 'w', encoding='utf-8') as f:
                f.write(content)

        # Commit before-state
        subprocess.run(
            ['git', 'add', '.'], cwd=tmpdir,
            capture_output=True, check=True,
        )
        subprocess.run(
            ['git', '-c', 'user.email=test@test.com',
             '-c', 'user.name=Test',
             'commit', '-m', 'before state'],
            cwd=tmpdir, capture_output=True, check=True,
        )

        # R2 fix: write AFTER-STATE files
        for fpath, content in after_files:
            full = os.path.join(tmpdir, fpath)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, 'w', encoding='utf-8') as f:
                f.write(content)

        # Commit after-state
        subprocess.run(
            ['git', 'add', '.'], cwd=tmpdir,
            capture_output=True, check=True,
        )
        subprocess.run(
            ['git', '-c', 'user.email=test@test.com',
             '-c', 'user.name=Test',
             'commit', '-m',
             'seed test: %s' % target_dim],
            cwd=tmpdir, capture_output=True, check=True,
        )

        # R8 fix: use sys.executable + absolute path to forge_cli.py
        if not os.path.isfile(FORGE_CLI):
            return (
                None,
                'forge_cli.py not found at %s' % FORGE_CLI,
            )

        # Timeout is configurable via SEED_TEST_TIMEOUT env var.
        # Default 600s accounts for LLM API latency (variable by
        # provider), diff parsing, and git operations in tmpdir.
        timeout = int(os.getenv('SEED_TEST_TIMEOUT', '600'))
        try:
            result = subprocess.run(
                [sys.executable, FORGE_CLI, 'HEAD~1'],
                cwd=tmpdir, capture_output=True, text=True,
                timeout=timeout, check=False,
            )
            output = result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            return (
                None,
                'forge review timed out (%ds). Set '
                'SEED_TEST_TIMEOUT env var to increase.'
                % timeout,
            )

        # Check if target dimension was flagged
        findings_path = os.path.join(
            tmpdir, '.forge', 'findings.json',
        )
        if os.path.isfile(findings_path):
            with open(findings_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            dims_found = set(
                finding.get('dimension', '') for finding in
                data.get('findings', [])
            )
            if target_dim in dims_found:
                return (
                    True,
                    'Target dimension %s detected' % target_dim,
                )
            return (
                False,
                'Target %s NOT detected. '
                'Found: %s. '
                'SKILL.md prompt may need improvement.'
                % (target_dim, dims_found),
            )
        else:
            if target_dim.lower() in output.lower():
                return (
                    True,
                    'Target dimension mentioned in output',
                )
            return (
                False,
                'No findings.json created. Output: '
                '%s' % output[:200],
            )


def _run_single_dimension_test(dim_name, diff_path,
                               config_path=None):
    """Run seed test for a single proposal-generated dimension.

    Writes result to config.json dimension_states[dim].seed_test_status.
    Returns exit code (0 for pass, 1 for fail).

    Args:
        dim_name: Dimension name to test.
        diff_path: Path to the seed diff file.
        config_path: Path to config.json. Pass explicitly in
            automated tests to avoid writing to production config.
    """
    if not os.path.isfile(diff_path):
        print(
            'Error: diff file not found: %s' % diff_path,
            file=sys.stderr,
        )
        return 1

    valid, msg = validate_diff(diff_path)
    if not valid:
        print(
            'FAIL %s: %s' % (dim_name, msg),
        )
        _write_seed_test_status(
            dim_name, 'fail', config_path=config_path,
        )
        return 1

    print('  RUN  %s...' % dim_name)
    passed, details = run_seed_test(
        diff_path, dim_name,
        'proposal-generated seed test',
    )
    if passed is True:
        status = 'PASS'
        result_str = 'pass'
    else:
        status = 'FAIL'
        result_str = 'fail'
    print('  %s %s: %s' % (status, dim_name, details))

    _write_seed_test_status(
        dim_name, result_str, config_path=config_path,
    )
    return 0 if passed else 1


def _write_seed_test_status(dim_name, status_value, config_path=None):
    """Write seed_test_status to config.json dimension_states.

    Loads config, updates dimension_states[dim].seed_test_status,
    writes back atomically.

    Args:
        dim_name: Dimension name to update.
        status_value: 'pass' or 'fail'.
        config_path: Path to config.json. Defaults to the
            production config in cli/. Pass a temp file path
            in test mode to avoid writing to live config.
    """
    if config_path is None:
        config_path = os.path.normpath(
            os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                '..', '..', 'cli', 'config.json',
            ),
        )
    if not os.path.isfile(config_path):
        print(
            'Warning: config.json not found at %s, '
            'cannot write seed_test_status' % config_path,
            file=sys.stderr,
        )
        return

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except (json.JSONDecodeError, IOError) as exc:
        print(
            'Warning: failed to load config.json: %s' % exc,
            file=sys.stderr,
        )
        return

    dim_states = config.get('dimension_states', {})
    if dim_name not in dim_states:
        print(
            'Warning: dimension %s not in dimension_states, '
            'cannot write seed_test_status' % dim_name,
            file=sys.stderr,
        )
        return

    dim_states[dim_name]['seed_test_status'] = status_value

    # Atomic write (inline to avoid circular import)
    import tempfile as _tmpmod
    dir_name = os.path.dirname(config_path) or '.'
    fd, tmp = _tmpmod.mkstemp(dir=dir_name, suffix='.json')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        os.replace(tmp, config_path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main():
    parser = argparse.ArgumentParser(
        description='Run seed tests for forge dimensions',
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Validate diff format without running forge',
    )
    parser.add_argument(
        '--dimension', metavar='DIM',
        help=(
            'Run seed test for a specific dimension '
            '(proposal-generated)'
        ),
    )
    parser.add_argument(
        '--diff', metavar='PATH',
        help=(
            'Path to diff file for --dimension '
            '(required with --dimension)'
        ),
    )
    args = parser.parse_args()

    # --dimension mode: single dimension test
    if args.dimension and args.diff:
        sys.exit(
            _run_single_dimension_test(
                args.dimension, args.diff,
            ),
        )
    elif args.dimension and not args.diff:
        parser.error('--dimension requires --diff')
    elif args.diff and not args.dimension:
        parser.error('--diff requires --dimension')

    # Default mode: run all SEED_TESTS
    script_dir = os.path.dirname(os.path.abspath(__file__))
    diff_files = find_seed_diffs(script_dir)

    if not diff_files:
        print(
            'Error: no seed diffs found in '
            '%s' % os.path.join(script_dir, 'seed_diffs'),
            file=sys.stderr,
        )
        sys.exit(1)

    # Validate that all .diff files have SEED_TESTS entries
    diff_stems = set(
        os.path.splitext(os.path.basename(p))[0]
        for p in diff_files
    )
    unmapped = diff_stems - set(SEED_TESTS.keys())
    if unmapped:
        print(
            'Warning: .diff files without SEED_TESTS entries: '
            '%s' % ', '.join(sorted(unmapped)),
            file=sys.stderr,
        )

    print('Seed Tests: %d diffs found' % len(diff_files))
    if args.dry_run:
        print('Mode: dry-run (validation only)')
    else:
        print('Mode: full (LLM invocation)')
    print()

    results = []
    for diff_path in diff_files:
        stem = os.path.splitext(
            os.path.basename(diff_path),
        )[0]
        test_info = SEED_TESTS.get(stem)
        if not test_info:
            print('  SKIP %s: not in SEED_TESTS mapping' % stem)
            continue

        target = test_info['target_dimension']
        desc = test_info['description']

        valid, msg = validate_diff(diff_path)
        if not valid:
            print('  FAIL %s: %s' % (stem, msg))
            results.append((stem, target, False, msg))
            continue

        if args.dry_run:
            print('  OK   %s -> %s (%s)' % (stem, target, desc))
            results.append(
                (stem, target, True, 'validated'),
            )
            continue

        print('  RUN  %s -> %s...' % (stem, target))
        passed, details = run_seed_test(
            diff_path, target, desc,
        )
        if passed is True:
            status = 'PASS'
        elif passed is None:
            status = 'SKIP'
        else:
            status = 'FAIL'
        print('  %s %s: %s' % (status, stem, details))
        results.append((stem, target, passed, details))

    # Summary
    print()
    print('=' * 60)
    print('Seed Test Summary')
    print('=' * 60)
    passed_count = sum(1 for r in results if r[2] is True)
    failed_count = sum(1 for r in results if r[2] is False)
    skipped_count = sum(1 for r in results if r[2] is None)
    print('  Passed:  %d' % passed_count)
    print('  Failed:  %d' % failed_count)
    print('  Skipped: %d' % skipped_count)
    print()

    if failed_count > 0:
        print(
            'Failed dimensions need SKILL.md '
            'prompt improvement:'
        )
        for stem, target, p, detail in results:
            if p is False:
                print('  - %s: %s' % (target, detail))
        print()

    sys.exit(1 if failed_count > 0 else 0)


if __name__ == '__main__':
    main()
