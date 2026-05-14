#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Phase 3 automated tests -- D4 classification, dedup, migration, Sashiko replay (LEARN-09)."""

import hashlib
import json
import os
import sys
import unittest


def setUpModule():
    """Add project paths to sys.path for module imports."""
    cli_path = os.path.join(os.path.dirname(__file__), '..', 'cli')
    root_path = os.path.join(os.path.dirname(__file__), '..')
    if cli_path not in sys.path:
        sys.path.insert(0, cli_path)
    if root_path not in sys.path:
        sys.path.insert(0, root_path)


# Ensure paths are available at import time for module-level imports
setUpModule()

from gap_detector import classify_finding, is_exact_dup, find_cross_source_dup
from migration import DIMENSION_RENAME_MAP, SEED_KEYWORD_DICTIONARIES
from llm_parser import compute_text_hash
from escalation import check_triggers
from cli.adapters.github_pr import _detect_source_tool

# Import path for seed test parse functions
_seed_path = os.path.join(
    os.path.dirname(__file__), 'seed_tests',
)
if _seed_path not in sys.path:
    sys.path.insert(0, _seed_path)
from run_seed_tests import _parse_before_state, _parse_after_state


FIXTURES_DIR = os.path.join(os.path.dirname(__file__), 'fixtures')


class TestFindingClassification(unittest.TestCase):
    """Test D4 three-outcome classification algorithm."""

    def test_keyword_match_outcome_1(self):
        """Keyword match returns outcome_1 with correct dim."""
        keyword_dicts = {'security': ['injection', 'SSRF']}
        finding = {
            'dimension_raw': 'SQL injection risk',
            'text': 'query not parameterized',
        }
        outcome, dim = classify_finding(finding, keyword_dicts, {})
        self.assertEqual(outcome, 'outcome_1')
        self.assertEqual(dim, 'security')

    def test_name_match_outcome_2(self):
        """Name match (no keyword hit) returns outcome_2."""
        keyword_dicts = {'security': ['SSRF', 'traversal']}
        finding = {
            'dimension_raw': 'security',
            'text': 'unrelated text about fonts',
        }
        outcome, dim = classify_finding(finding, keyword_dicts, {})
        self.assertEqual(outcome, 'outcome_2')
        self.assertEqual(dim, 'security')

    def test_unrecognized_outcome_3(self):
        """Unrecognized finding returns outcome_3."""
        keyword_dicts = {'security': ['injection']}
        finding = {
            'dimension_raw': 'supply chain',
            'text': 'dependency confusion attack',
        }
        outcome, dim = classify_finding(finding, keyword_dicts, {})
        self.assertEqual(outcome, 'outcome_3')
        self.assertIsNone(dim)

    def test_archived_dims_skipped(self):
        """Archived dims are skipped during classification."""
        keyword_dicts = {'security': ['injection']}
        dim_states = {'security': {'status': 'archived'}}
        finding = {
            'dimension_raw': 'SQL injection',
            'text': 'injection attack vector',
        }
        outcome, dim = classify_finding(
            finding, keyword_dicts, dim_states,
        )
        self.assertEqual(outcome, 'outcome_3')
        self.assertIsNone(dim)

    def test_alphabetical_tiebreaker(self):
        """Alphabetically first dim wins on tie."""
        keyword_dicts = {
            'beta_dim': ['shared keyword'],
            'alpha_dim': ['shared keyword'],
        }
        finding = {
            'dimension_raw': 'test',
            'text': 'shared keyword appears here',
        }
        outcome, dim = classify_finding(finding, keyword_dicts, {})
        self.assertEqual(outcome, 'outcome_1')
        self.assertEqual(dim, 'alpha_dim')

    def test_missing_dimension_states_treated_as_active(self):
        """Dims not in dimension_states are treated as active."""
        keyword_dicts = {'security': ['injection']}
        # dimension_states does not contain 'security'
        dim_states = {'other_dim': {'status': 'active'}}
        finding = {
            'dimension_raw': 'test',
            'text': 'injection detected',
        }
        outcome, dim = classify_finding(
            finding, keyword_dicts, dim_states,
        )
        self.assertEqual(outcome, 'outcome_1')
        self.assertEqual(dim, 'security')

    def test_classify_none_text(self):
        """None text should be handled gracefully."""
        keyword_dicts = {'security': ['injection']}
        result = classify_finding(
            {'dimension_raw': 'security', 'text': None},
            keyword_dicts, {},
        )
        # Should not crash -- return a valid outcome tuple
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)


class TestDuplicateDetection(unittest.TestCase):
    """Test exact and cross-source dedup functions."""

    def test_exact_dup_detected(self):
        """Exact dup found by (source, source_id)."""
        existing = [
            {'source': 'github_pr', 'source_id': '100001'},
        ]
        result = is_exact_dup('github_pr', '100001', existing)
        self.assertEqual(result, True)

    def test_exact_dup_different_source(self):
        """Same source_id, different source is not a dup."""
        existing = [
            {'source': 'github_pr', 'source_id': '100001'},
        ]
        result = is_exact_dup('git_log', '100001', existing)
        self.assertEqual(result, False)

    def test_cross_source_dup_within_7_days(self):
        """Cross-source dup detected within 7-day window."""
        text_hash = compute_text_hash(
            'Race condition in shared dictionary access',
        )
        existing = [
            {
                'id': 'ext-001',
                'source': 'github_pr',
                'source_id': '200001',
                'file': 'src/worker.py',
                'line': 42,
                'text_hash': text_hash,
                'timestamp': '2026-05-10T10:00:00+00:00',
            },
        ]
        result = find_cross_source_dup(
            'src/worker.py', 42, text_hash,
            '2026-05-12T10:00:00+00:00', existing,
        )
        self.assertEqual(result, 'ext-001')

    def test_cross_source_dup_outside_window(self):
        """Cross-source dup NOT detected outside 7-day window."""
        text_hash = compute_text_hash(
            'Race condition in shared dictionary access',
        )
        existing = [
            {
                'id': 'ext-001',
                'source': 'github_pr',
                'source_id': '200001',
                'file': 'src/worker.py',
                'line': 42,
                'text_hash': text_hash,
                'timestamp': '2026-05-01T10:00:00+00:00',
            },
        ]
        # 10 days later -- outside 7-day window
        result = find_cross_source_dup(
            'src/worker.py', 42, text_hash,
            '2026-05-12T10:00:00+00:00', existing,
        )
        self.assertIsNone(result)

    def test_cross_source_dup_different_file(self):
        """Same (line, text_hash) but different file is not a dup."""
        text_hash = compute_text_hash(
            'Race condition in shared dictionary access',
        )
        existing = [
            {
                'id': 'ext-001',
                'source': 'github_pr',
                'source_id': '200001',
                'file': 'src/worker.py',
                'line': 42,
                'text_hash': text_hash,
                'timestamp': '2026-05-10T10:00:00+00:00',
            },
        ]
        result = find_cross_source_dup(
            'src/other.py', 42, text_hash,
            '2026-05-12T10:00:00+00:00', existing,
        )
        self.assertIsNone(result)

    def test_cross_source_dup_exactly_7_days(self):
        """Test the exact 7-day boundary."""
        text_hash = compute_text_hash('boundary test')
        existing = [{
            'id': 'ext-001', 'file': 'src/a.py', 'line': 1,
            'text_hash': text_hash,
            'timestamp': '2026-05-10T10:00:00+00:00',
        }]
        # At exactly 7 days -- should still match
        result = find_cross_source_dup(
            'src/a.py', 1, text_hash,
            '2026-05-17T10:00:00+00:00', existing,
        )
        self.assertEqual(result, 'ext-001')

    def test_cross_source_dup_corrupted_timestamp(self):
        """Corrupted timestamp should not crash."""
        text_hash = compute_text_hash('test')
        existing = [{'id': 'ext-001', 'file': 'a.py', 'line': 1,
                     'text_hash': text_hash,
                     'timestamp': 'not-a-date'}]
        result = find_cross_source_dup(
            'a.py', 1, text_hash,
            '2026-05-12T10:00:00+00:00', existing,
        )
        self.assertIsNone(result)


class TestDimensionMapping(unittest.TestCase):
    """Test migration constants and dimension rename map."""

    def test_rename_map_entries(self):
        """DIMENSION_RENAME_MAP has exactly 5 entries."""
        self.assertEqual(len(DIMENSION_RENAME_MAP), 5)

    def test_rename_convention_adherence(self):
        """convention_adherence maps to convention."""
        self.assertEqual(
            DIMENSION_RENAME_MAP['convention_adherence'],
            'convention',
        )

    def test_rename_state_management(self):
        """state_management maps to concurrency."""
        self.assertEqual(
            DIMENSION_RENAME_MAP['state_management'],
            'concurrency',
        )

    def test_seed_keywords_has_required_dimensions(self):
        """SEED_KEYWORD_DICTIONARIES contains all required dimensions."""
        required = {
            'correctness', 'security', 'concurrency', 'edge_cases',
            'error_handling', 'api_contract', 'bidirectional',
            'graceful_degradation', 'convention', 'performance',
            'test_quality', 'ai_code_smell', 'doc_completeness',
            'change_scope',
        }
        self.assertTrue(
            required.issubset(set(SEED_KEYWORD_DICTIONARIES.keys())),
            'Missing required dimensions: %s'
            % (required - set(SEED_KEYWORD_DICTIONARIES.keys())),
        )


class TestSourceToolDetection(unittest.TestCase):
    """Test source_tool detection from GitHub comment user field."""

    def test_human_user(self):
        """User type returns human."""
        comment = {
            'user': {'login': 'reviewer1', 'type': 'User'},
        }
        self.assertEqual(_detect_source_tool(comment), 'human')

    def test_qodo_bot(self):
        """Bot with qodo in login returns qodo."""
        comment = {
            'user': {
                'login': 'qodo-merge-pro[bot]',
                'type': 'Bot',
            },
        }
        self.assertEqual(_detect_source_tool(comment), 'qodo')

    def test_coderabbit_bot(self):
        """Bot with coderabbit in login returns coderabbit."""
        comment = {
            'user': {
                'login': 'coderabbitai[bot]',
                'type': 'Bot',
            },
        }
        self.assertEqual(_detect_source_tool(comment), 'coderabbit')

    def test_unknown_bot(self):
        """Bot with unknown login returns unknown."""
        comment = {
            'user': {
                'login': 'somebot[bot]',
                'type': 'Bot',
            },
        }
        self.assertEqual(_detect_source_tool(comment), 'unknown')


class TestEscalationMonitoring(unittest.TestCase):
    """Test escalation trigger threshold checking."""

    def test_dedup_trigger(self):
        """Dedup error rate above threshold returns LEARN-03 alert."""
        metrics = {
            'dedup_error_rate': 0.25,
            'edit_corruption_count': 0,
            'dimension_change_count': 0,
        }
        alerts = check_triggers(metrics)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0][0], 'LEARN-03')

    def test_no_triggers(self):
        """All metrics within thresholds returns empty list."""
        metrics = {
            'dedup_error_rate': 0.10,
            'edit_corruption_count': 1,
            'dimension_change_count': 5,
        }
        alerts = check_triggers(metrics)
        self.assertEqual(len(alerts), 0)

    def test_multiple_triggers(self):
        """All metrics above thresholds returns 3 alerts."""
        metrics = {
            'dedup_error_rate': 0.30,
            'edit_corruption_count': 5,
            'dimension_change_count': 15,
        }
        alerts = check_triggers(metrics)
        self.assertEqual(len(alerts), 3)
        trigger_names = [a[0] for a in alerts]
        self.assertIn('LEARN-03', trigger_names)
        self.assertIn('LEARN-04', trigger_names)
        self.assertIn('LEARN-05', trigger_names)


class TestSashikoReplay(unittest.TestCase):
    """Test Sashiko incident replay validates all 3 dimensions (LEARN-09)."""

    def test_sashiko_all_three_dimensions(self):
        """All 3 Sashiko findings classify to outcome_1 with correct dimensions."""
        fixture_path = os.path.join(
            FIXTURES_DIR, 'sashiko_replay', 'sashiko_findings.json',
        )
        with open(fixture_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        for finding_data in data['findings']:
            finding = {
                'dimension_raw': finding_data['dimension_raw'],
                'text': finding_data['text'],
            }
            outcome, dim = classify_finding(
                finding, SEED_KEYWORD_DICTIONARIES, {},
            )
            self.assertEqual(
                outcome,
                finding_data['expected_outcome'],
                f"Finding '{finding_data['dimension_raw']}' "
                f"expected {finding_data['expected_outcome']}, "
                f"got {outcome}",
            )
            self.assertEqual(
                dim,
                finding_data['expected_dimension'],
                f"Finding '{finding_data['dimension_raw']}' "
                f"expected dim {finding_data['expected_dimension']}, "
                f"got {dim}",
            )


class TestTextHash(unittest.TestCase):
    """Test compute_text_hash determinism and uniqueness."""

    def test_deterministic_hash(self):
        """compute_text_hash produces the known SHA-256 value."""
        expected = hashlib.sha256(
            'test'.encode('utf-8'),
        ).hexdigest()
        self.assertEqual(compute_text_hash('test'), expected)

    def test_different_text_different_hash(self):
        """Two different texts produce different hashes."""
        h1 = compute_text_hash('hello')
        h2 = compute_text_hash('world')
        self.assertNotEqual(h1, h2)


class TestParseFunctions(unittest.TestCase):
    """Test _parse_before_state and _parse_after_state diff parsers."""

    def test_parse_empty_diff(self):
        """Empty diff content returns no before-state files."""
        result = _parse_before_state('')
        self.assertEqual(result, [])

    def test_parse_missing_before_block(self):
        """Diff without BEFORE block returns empty list."""
        diff = (
            '--- a/src/app.py\n'
            '+++ b/src/app.py\n'
            '@@ -1,3 +1,3 @@\n'
            ' line1\n'
            '-old\n'
            '+new\n'
        )
        result = _parse_before_state(diff)
        self.assertEqual(result, [])

    def test_parse_single_file_add(self):
        """Before-state with a single file is parsed correctly."""
        diff = (
            '---BEGIN BEFORE---\n'
            'src/app.py\n'
            'def hello():\n'
            '    return "hi"\n'
            '---END BEFORE---\n'
            '--- a/src/app.py\n'
            '+++ b/src/app.py\n'
            '@@ -1,2 +1,2 @@\n'
            ' def hello():\n'
            '-    return "hi"\n'
            '+    return "hello"\n'
        )
        before = _parse_before_state(diff)
        self.assertEqual(len(before), 1)
        self.assertEqual(before[0][0], 'src/app.py')
        self.assertIn('def hello():', before[0][1])
        self.assertIn('return "hi"', before[0][1])

    def test_parse_single_file_modify(self):
        """After-state correctly applies a single-hunk modification."""
        diff = (
            '---BEGIN BEFORE---\n'
            'src/app.py\n'
            'def hello():\n'
            '    return "hi"\n'
            '---END BEFORE---\n'
            '--- a/src/app.py\n'
            '+++ b/src/app.py\n'
            '@@ -1,2 +1,2 @@\n'
            ' def hello():\n'
            '-    return "hi"\n'
            '+    return "hello"\n'
        )
        before = _parse_before_state(diff)
        after = _parse_after_state(diff, before)
        self.assertEqual(len(after), 1)
        self.assertEqual(after[0][0], 'src/app.py')
        self.assertIn('return "hello"', after[0][1])
        self.assertNotIn('return "hi"', after[0][1])

    def test_parse_after_no_diff_section(self):
        """File with no matching diff section keeps before content."""
        before = [('src/other.py', 'x = 1\n')]
        diff = (
            '--- a/src/app.py\n'
            '+++ b/src/app.py\n'
            '@@ -1,1 +1,1 @@\n'
            '-old\n'
            '+new\n'
        )
        after = _parse_after_state(diff, before)
        self.assertEqual(len(after), 1)
        self.assertEqual(after[0][1], 'x = 1\n')


class TestStorageResilience(unittest.TestCase):
    """Test production functions handle empty/degenerate storage."""

    def test_classify_with_empty_keyword_dicts(self):
        """classify_finding with empty keyword dicts returns outcome_3."""
        finding = {
            'dimension_raw': 'security',
            'text': 'injection risk detected',
        }
        outcome, dim = classify_finding(finding, {}, {})
        self.assertEqual(outcome, 'outcome_3')
        self.assertIsNone(dim)

    def test_dedup_with_empty_findings_list(self):
        """is_exact_dup with empty existing list returns False."""
        result = is_exact_dup('github_pr', '100001', [])
        self.assertEqual(result, False)

    def test_cross_source_dedup_empty_list(self):
        """find_cross_source_dup with empty list returns None."""
        text_hash = compute_text_hash('test')
        result = find_cross_source_dup(
            'src/a.py', 1, text_hash,
            '2026-05-12T10:00:00+00:00', [],
        )
        self.assertIsNone(result)


if __name__ == '__main__':
    unittest.main()
