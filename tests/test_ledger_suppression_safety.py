# SPDX-License-Identifier: Apache-2.0
"""A historical disposition is not proof that a fresh candidate is harmless."""
from dataclasses import replace

import pytest

from code_forge.disposition import Disposition
from code_forge.reviewer_json import _location_fingerprint
from tests.test_machine_ledger import (
    _build_ci_machine,
    _build_machine,
    _make_finding,
    _resolved_with_shas,
    _write_ledger_line,
)

DIFF = 'diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n'


def _record(tmp_path, disposition=Disposition.DISMISSED):
    resolved = replace(_resolved_with_shas(), git_diff=DIFF)
    fp = _location_fingerprint('a.py', 10, 'L0')
    finding = _make_finding(fp, disposition, source='L0', line=10, description='unsafe call')
    machine = _build_machine(tmp_path, resolved)
    machine._state.findings = [finding]
    assert machine._write_ledger_rows() == 1
    return resolved, replace(finding, disposition=Disposition.CONFIRMED)


def _suppressed(tmp_path, resolved, finding):
    machine = _build_ci_machine(tmp_path, resolved)
    machine._state.findings = [finding]
    machine._suppress_known_findings()
    return finding.disposition == Disposition.DISMISSED


def test_fixed_recurrence_must_not_be_suppressed(tmp_path):
    resolved, finding = _record(tmp_path, Disposition.FIXED)
    assert not _suppressed(tmp_path, resolved, finding)


@pytest.mark.parametrize('change', ['head', 'base', 'diff', 'whitespace', 'claim', 'line', 'pass', 'file'])
def test_disproved_does_not_suppress_changed_candidate(tmp_path, change):
    resolved, finding = _record(tmp_path)
    if change in {'head', 'base'}:
        resolved = replace(resolved, **{change + '_sha': 'c' * 40})
    elif change in {'diff', 'whitespace'}:
        resolved = replace(resolved, git_diff=DIFF.replace('+new', '+different' if change == 'diff' else '+new '))
    elif change == 'claim':
        finding.description = 'a different defect at the same location'
    elif change == 'line':
        finding.line_range = [11, 11]
        assert _location_fingerprint('a.py', 11, 'L0') == finding.fingerprint
    elif change == 'pass':
        finding.source = 'other-review'
    else:
        finding.file = 'b.py'
    assert not _suppressed(tmp_path, resolved, finding)


def test_identical_disproved_candidate_can_be_suppressed(tmp_path):
    resolved, finding = _record(tmp_path)
    assert _suppressed(tmp_path, resolved, finding)


def test_legacy_disproved_row_cannot_suppress_without_snapshot_identity(tmp_path):
    _write_ledger_line(tmp_path, 'old', 'DISPROVED')
    assert not _suppressed(tmp_path, _resolved_with_shas(), _make_finding('old'))


@pytest.mark.parametrize('state', ['FIXED', 'ESCAPED', 'UNADJUDICATED'])
def test_latest_exact_decision_can_revoke_suppression(tmp_path, state):
    from code_forge.ledger import TerminalState, append_row, iter_rows

    resolved, finding = _record(tmp_path)
    row = next(iter_rows(tmp_path))
    append_row(tmp_path, replace(row, terminal_state=TerminalState(state)))
    assert not _suppressed(tmp_path, resolved, finding)


@pytest.mark.parametrize('state', ['DISPROVED', 'DUPLICATE'])
@pytest.mark.parametrize('override_shas', [False, True])
def test_ci_adjudication_preserves_only_original_snapshot(tmp_path, state, override_shas):
    from code_forge.ledger import iter_rows
    from tests.test_cli_ledger import _run

    resolved = replace(_resolved_with_shas(), git_diff=DIFF)
    finding = _make_finding('ci-candidate')
    machine = _build_ci_machine(tmp_path, resolved)
    machine._state.findings = [finding]
    assert machine._write_ci_ledger_rows() == 1
    args = ['--base-sha', 'c' * 40, '--head-sha', 'd' * 40] if override_shas else []
    result = _run(tmp_path, 'ledger', 'adjudicate', finding.fingerprint, state, *args)
    assert result.returncode == 0, result.stderr
    row = list(iter_rows(tmp_path))[-1]
    assert bool(row.suppression_key) is not override_shas
    assert _suppressed(tmp_path, resolved, finding) is not override_shas


@pytest.mark.parametrize('mode', ['local', 'ci'])
def test_changed_dirty_diff_is_not_deduplicated_out_of_ledger(tmp_path, mode):
    from code_forge.ledger import iter_rows

    resolved = replace(_resolved_with_shas(), git_diff=DIFF)
    build = _build_machine if mode == 'local' else _build_ci_machine
    disposition = Disposition.DISMISSED if mode == 'local' else Disposition.CONFIRMED
    for diff in [DIFF, DIFF.replace('+new', '+different')]:
        machine = build(tmp_path, replace(resolved, git_diff=diff))
        machine._state.findings = [_make_finding('same-location', disposition)]
        write = machine._write_ledger_rows if mode == 'local' else machine._write_ci_ledger_rows
        assert write() == 1
        assert write() == 0
    rows = list(iter_rows(tmp_path))
    assert len(rows) == 2
    assert rows[0].suppression_key != rows[1].suppression_key


@pytest.mark.parametrize('missing', ['base_sha', 'head_sha', 'git_diff'])
def test_missing_snapshot_provenance_never_suppresses(tmp_path, missing):
    resolved, finding = _record(tmp_path)
    assert not _suppressed(tmp_path, replace(resolved, **{missing: None}), finding)


@pytest.mark.parametrize('state', ['ESCAPED', 'FIXED', 'DISPROVED', 'DUPLICATE'])
def test_unscoped_cli_reruling_revokes_older_keyed_suppression(tmp_path, state):
    from tests.test_cli_ledger import _run

    resolved, finding = _record(tmp_path)
    result = _run(
        tmp_path, 'ledger', 'mark', finding.fingerprint, state,
        '--base-sha', resolved.base_sha, '--head-sha', resolved.head_sha,
    )
    assert result.returncode == 0, result.stderr
    assert not _suppressed(tmp_path, resolved, finding)
