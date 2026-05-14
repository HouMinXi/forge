#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Convert historical FP analysis to findings.json schema (D1).

One-time bootstrap script for Phase 1a. Reads the structured analysis
file produced by the historical review data mining session and converts
each FP case to the findings.json schema defined in 01a-CONTEXT.md D1.

Filters out non-FP entries (FN, code bugs, process failures).
Splits mixed classifications into individual finding records.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import uuid
from datetime import datetime, timezone

# Import keyword dictionaries from migration.py (single source of truth)
sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), '..', 'cli'),
)
from migration import SEED_KEYWORD_DICTIONARIES

FINDINGS_FILE = '.forge/findings.json'

# Only project-relative path. If not found, print error and exit.
HISTORICAL_PATH = '.planning/research/historical_review_analysis.txt'

VALID_REJECT_REASONS = {
    'HALLUCINATION', 'CONTEXT_MISSING', 'INTENTIONAL',
    'NOT_APPLICABLE', 'STYLE_PREFERENCE', 'ACCEPTABLE_RISK',
}

# Patterns that indicate non-FP cases (skip these)
NON_FP_PATTERNS = [
    r'\bFN\b', r'false.negative', r'code.bug',
    r'process.failure', r'missed.finding',
]


def find_historical_file(explicit_path=None):
    """Find the historical analysis file.

    Try explicit path first, then project-relative path.
    No /tmp fallback -- bootstrap data must be in the project tree.
    """
    if explicit_path and os.path.isfile(explicit_path):
        return explicit_path
    if os.path.isfile(HISTORICAL_PATH):
        return HISTORICAL_PATH
    return None


def _parse_case_block(block):
    """Parse a single case block into a dict of structured fields.

    Expected format per case block (lines separated by newlines):
      --- Case N: Title ---
      Source: <review tool / pass / cycle info>
      Context: <file path or code area under review>
      Finding: <what the reviewer flagged>
      Outcome: <what happened -- accepted / rejected / ignored>
      Classification: <FP reason or N/A>
      Evidence: <supporting detail>
      Notes: <optional commentary>

    Multi-line values are continued on indented lines (2+ spaces).

    Returns dict with lowercase keys. Missing required fields
    (source, context, finding, classification) cause a warning.
    """
    fields = {}
    current_key = None
    for line in block.splitlines():
        # Match field lines: "Key: value"
        m = re.match(r'^(Source|Context|Finding|Outcome|Classification'
                     r'|Evidence|Notes):\s*(.*)', line)
        if m:
            current_key = m.group(1).lower()
            fields[current_key] = m.group(2).strip()
        elif current_key and line.startswith('  '):
            # Continuation line
            fields[current_key] += ' ' + line.strip()

    # Validate required fields are present
    required = {'source', 'context', 'finding', 'classification'}
    missing = required - set(fields.keys())
    if missing:
        print(
            'Warning: case block missing required fields: %s'
            % ', '.join(sorted(missing)),
            file=sys.stderr,
        )

    return fields


def _is_non_fp(classification_text):
    """Check if a classification indicates a non-FP entry."""
    for pattern in NON_FP_PATTERNS:
        if re.search(pattern, classification_text, re.IGNORECASE):
            return True
    # Check if entire classification is "N/A" (not substring match,
    # to avoid false positives on text that merely contains "N/A")
    if classification_text.strip() == 'N/A':
        return True
    if re.search(r'CODE\s+BUG', classification_text, re.IGNORECASE):
        return True
    return False


def parse_historical_analysis(filepath):
    """Parse case blocks from the historical analysis file.

    Returns list of dicts with keys: source, context, finding,
    outcome, classification, evidence, notes, case_number, title.
    Filters out non-FP entries (FN, code bugs, process failures).

    Post-parse validation:
    - Prints SHA-256 of input file for audit trail
    - Counts total case blocks found vs FP cases retained
    - Warns on cases with empty classification
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Audit trail: SHA-256 of input file
    input_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()
    print('Input file SHA-256: %s' % input_hash)

    # Split on case delimiter: "--- Case N: ... ---"
    case_pattern = r'^--- Case (\d+):\s*(.*?)\s*---\s*$'
    cases = []
    blocks = re.split(case_pattern, content, flags=re.MULTILINE)

    # blocks[0] is header text before first case
    # Then groups of 3: (case_number, title, block_text)
    total_blocks = 0
    filtered_count = 0
    empty_classification_count = 0
    i = 1
    while i + 2 < len(blocks):
        case_num = int(blocks[i])
        title = blocks[i + 1]
        block_text = blocks[i + 2]
        i += 3
        total_blocks += 1

        fields = _parse_case_block(block_text)
        fields['case_number'] = case_num
        fields['title'] = title

        # Validate non-empty classification
        classification = fields.get('classification', '')
        if not classification.strip():
            empty_classification_count += 1
            print(
                'Warning: Case %d has empty classification'
                % case_num,
                file=sys.stderr,
            )

        # Filter out non-FP entries
        if _is_non_fp(classification):
            filtered_count += 1
            continue

        cases.append(fields)

    # Post-parse summary
    print(
        'Parsed %d case blocks: %d FP retained, '
        '%d non-FP filtered'
        % (total_blocks, len(cases), filtered_count),
    )
    if empty_classification_count:
        print(
            'Warning: %d cases had empty classification'
            % empty_classification_count,
            file=sys.stderr,
        )

    return cases


def split_mixed_classification(classification_text):
    """Split 'Mix of X (N) and Y (M)' into individual (reason, count) pairs.

    Returns list of (reason, count) tuples.
    E.g., 'Mix of CONTEXT_MISSING (3) and HALLUCINATION (2)' returns
    [('CONTEXT_MISSING', 3), ('HALLUCINATION', 2)].

    If not a mixed classification, returns [(mapped_reason, 1)].
    """
    # Check for "Mix of X (N) and Y (M)" pattern
    mix_match = re.search(
        r'Mix\s+of\s+(\w+)\s*\((\d+)\)\s+and\s+(\w+)\s*\((\d+)\)',
        classification_text
    )
    if mix_match:
        reason1 = mix_match.group(1)
        count1 = int(mix_match.group(2))
        reason2 = mix_match.group(3)
        count2 = int(mix_match.group(4))
        result = []
        if reason1 in VALID_REJECT_REASONS:
            result.append((reason1, count1))
        if reason2 in VALID_REJECT_REASONS:
            result.append((reason2, count2))
        if result:
            return result

    # Check for "(N instances)" count pattern -- no trailing \)
    # because text may continue: "(3 instances -- explanation)"
    count_match = re.search(r'\((\d+)\s+instances?', classification_text)
    count = int(count_match.group(1)) if count_match else 1

    # Check for multi-sub-finding: "(a) X, (b) Y" pattern
    sub_match = re.findall(
        r'\(([a-z])\)\s+(\w+)', classification_text
    )
    if sub_match:
        result = []
        for _, reason in sub_match:
            if reason in VALID_REJECT_REASONS:
                result.append((reason, 1))
        if result:
            return result

    reason = map_reject_reason(classification_text)
    return [(reason, count)]


def map_reject_reason(classification_text):
    """Map classification text to one of 6 valid reject reasons."""
    text_upper = classification_text.upper()
    for reason in VALID_REJECT_REASONS:
        if reason in text_upper:
            return reason
    # Fallback heuristics
    if re.search(r'invent|fabricat|phantom', text_upper):
        return 'HALLUCINATION'
    if re.search(r'context|cross.file|unknown', text_upper):
        return 'CONTEXT_MISSING'
    if re.search(r'intentional|design|tradeoff|defensive', text_upper):
        return 'INTENTIONAL'
    if re.search(r'stale|misparse|unreachable|inapplicable', text_upper):
        return 'NOT_APPLICABLE'
    if re.search(r'style|subjective|preference|naming', text_upper):
        return 'STYLE_PREFERENCE'
    if re.search(r'accept.*risk|won.*fix|documented', text_upper):
        return 'ACCEPTABLE_RISK'
    return 'HALLUCINATION'


def map_dimension(context_text, finding_text):
    """Map context/finding text to a review dimension name.

    Uses SEED_KEYWORD_DICTIONARIES from migration.py as single
    source of truth for dimension-to-keyword mapping (H20 fix).
    """
    combined = (context_text + ' ' + finding_text).lower()

    for dimension, keywords in SEED_KEYWORD_DICTIONARIES.items():
        for kw in keywords:
            if kw.lower() in combined:
                return dimension

    return 'unknown'


def map_severity(finding_text, classification_text):
    """Map finding/classification text to P0/P1/P2/P3.

    Severity levels follow the forge review severity policy:
      P0 = critical (security breach, data loss)
      P1 = high (must fix before merge)
      P2 = medium (should fix, default when unspecified)
      P3 = low (nit, style, informational)
    """
    combined = (finding_text + ' ' + classification_text).lower()

    if re.search(r'\bp0\b|critical', combined):
        return 'P0'
    if re.search(r'\bp1\b|\bhigh\b|must.fix', combined):
        return 'P1'
    if re.search(r'\bp2\b|\bmedium\b', combined):
        return 'P2'
    if re.search(r'\bp3\b|\blow\b|\bminor\b|\binformational\b'
                 r'|\bnit\b|\bstyle\b', combined):
        return 'P3'
    return 'P2'


def _extract_pass_info(source_text):
    """Extract pass and cycle numbers from source text."""
    pass_num = 0
    cycle_num = 0
    m = re.search(r'Pass\s+(\d+)', source_text, re.IGNORECASE)
    if m:
        pass_num = int(m.group(1))
    m = re.search(r'Cycle\s+(\d+)', source_text, re.IGNORECASE)
    if m:
        cycle_num = int(m.group(1))
    return pass_num, cycle_num


def _extract_file_path(context_text):
    """Extract a file path from context text if mentioned."""
    # Look for explicit file paths
    m = re.search(r'[\w/.-]+\.(?:py|sh|c|h|md|json|yaml|yml)',
                  context_text)
    if m:
        return m.group(0)
    return 'unknown'


def convert_to_schema(cases):
    """Convert parsed cases to D1 findings schema.

    For cases with mixed classifications (e.g., "Mix of X (3) and Y (2)"),
    generate multiple finding records -- one per sub-classification count.
    """
    now = datetime.now(timezone.utc).isoformat()
    findings = []

    for case in cases:
        classification = case.get('classification', '')
        source = case.get('source', '')
        context = case.get('context', '')
        finding_text = case.get('finding', '')
        outcome_text = case.get('outcome', '')
        title = case.get('title', '')

        pass_num, cycle_num = _extract_pass_info(source)
        file_path = _extract_file_path(context)
        severity = map_severity(finding_text + ' ' + title,
                                classification)
        dimension = map_dimension(context, finding_text)
        description = finding_text
        if outcome_text:
            description += ' -- ' + outcome_text

        # Split mixed/multi-count classifications
        reason_counts = split_mixed_classification(classification)

        for reason, count in reason_counts:
            for _ in range(count):
                findings.append({
                    'id': str(uuid.uuid4()),
                    'timestamp': now,
                    'file': file_path,
                    'line': -1,
                    'dimension': dimension,
                    'pass': pass_num,
                    'cycle': cycle_num,
                    'severity': severity,
                    'description': description,
                    'outcome': 'rejected',
                    'reject_reason': reason,
                    'commit_sha': 'historical',
                    'cost_tokens': {'input': 0, 'output': 0},
                })

    return findings


def atomic_write(filepath, data):
    """Atomically write JSON data using tempfile + os.replace."""
    dir_name = os.path.dirname(filepath) or '.'
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dir_name, suffix='.json')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write('\n')
        os.replace(tmp, filepath)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_existing():
    """Load existing findings.json or return empty structure."""
    try:
        with open(FINDINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {'version': 1, 'findings': [], 'runs': []}


def _print_dry_run_summary(cases, findings):
    """Print dry-run summary without writing to disk."""
    print()
    print('=== DRY RUN (no files written) ===')
    print()

    # Sample conversions (first 3)
    sample_count = min(3, len(findings))
    if sample_count > 0:
        print('Sample conversions (%d of %d):' % (
            sample_count, len(findings),
        ))
        for finding in findings[:sample_count]:
            print(
                '  id=%s dim=%s reason=%s severity=%s'
                % (
                    finding['id'][:8] + '...',
                    finding['dimension'],
                    finding['reject_reason'],
                    finding['severity'],
                ),
            )
        print()

    # Dimension distribution
    dim_counts = {}
    for f in findings:
        dim = f.get('dimension', 'unknown')
        dim_counts[dim] = dim_counts.get(dim, 0) + 1
    print('Stats:')
    print('  Total cases parsed: %d' % len(cases))
    print('  Total findings generated: %d' % len(findings))
    print('  Dimension distribution:')
    for dim in sorted(dim_counts):
        print('    %s: %d' % (dim, dim_counts[dim]))


def main():
    parser = argparse.ArgumentParser(
        description=(
            'Convert historical FP analysis to '
            'findings.json schema'
        ),
    )
    parser.add_argument(
        'filepath', nargs='?', default=None,
        help='Path to historical analysis file',
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help=(
            'Parse and validate input, print sample '
            'conversions and stats, but do not write '
            'to findings.json'
        ),
    )
    args = parser.parse_args()

    filepath = find_historical_file(args.filepath)

    if filepath is None:
        print(
            'No historical analysis file found. '
            'Creating empty findings.json.',
            file=sys.stderr,
        )
        if args.dry_run:
            print('DRY RUN: would create empty findings.json')
            return
        existing = load_existing()
        if not existing['findings']:
            atomic_write(FINDINGS_FILE, existing)
            print('Created empty %s' % FINDINGS_FILE)
        else:
            print(
                '%s already has %d findings'
                % (FINDINGS_FILE, len(existing['findings'])),
            )
        return

    print('Reading from: %s' % filepath)
    cases = parse_historical_analysis(filepath)
    new_findings = convert_to_schema(cases)

    if args.dry_run:
        _print_dry_run_summary(cases, new_findings)
        return

    existing = load_existing()
    existing['findings'].extend(new_findings)
    atomic_write(FINDINGS_FILE, existing)

    print(
        'Bootstrapped %d historical findings to %s'
        % (len(new_findings), FINDINGS_FILE),
    )


if __name__ == '__main__':
    main()
