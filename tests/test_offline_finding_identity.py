"""Saved cross-repository receipts retain trusted finding attribution."""
import copy
import json

import pytest

from code_forge.source import compute_source_hash
from code_forge.state import Verdict
from code_forge.verify import _load_receipts, run_verify
from tests import test_cross_repo_identity as helper


@pytest.mark.parametrize('case', [
    'baseline', 'empty_findings', 'finding_missing_repo', 'finding_wrong_repo',
    'finding_wrong_source', 'finding_missing_file', 'finding_nonstring_file',
    'excerpt_wrong_repo',
])
def test_saved_receipt_identity(tmp_path, monkeypatch, case):
    captured = {}

    def capture_verify(*args, **kwargs):
        result = run_verify(*args, **kwargs)
        if result.passed and kwargs.get('reviewed_repositories'):
            captured['receipts'] = _load_receipts(args[0] / '.code-forge' / 'receipts')
            captured['repositories'] = kwargs['reviewed_repositories']
        return result

    monkeypatch.setattr(helper, 'run_verify', capture_verify)
    verdict = helper.run_case(tmp_path / 'producer', monkeypatch, same_name=True,
                              with_findings=case != 'empty_findings')
    assert verdict is Verdict.PASS
    originals = captured['receipts']
    repositories = captured['repositories']
    findings = [finding for receipt in originals for finding in receipt['findings']]
    assert len(findings) == (0 if case == 'empty_findings' else 6)
    assert all(finding['disposition'] == 'DISMISSED' for finding in findings)

    cwd = tmp_path / 'offline'
    receipt_dir = cwd / '.code-forge' / 'receipts'
    receipt_dir.mkdir(parents=True)
    (tmp_path / 'trusted-repositories.json').write_text(json.dumps(repositories))

    def write_receipts(receipts):
        for receipt in receipts:
            path = receipt_dir / ('receipt-c%dp%d.json' % (receipt['cycle'], receipt['pass']))
            path.write_text(json.dumps(receipt))

    def verify():
        # Scope must come from the caller, not these deliberately wrong hints.
        return run_verify(cwd, compute_source_hash(git_diff=repositories['primary']),
                          {'forged/main.py': [1]}, diff_text='untrusted hint',
                          required_cycles=1, respect_floor=False,
                          reviewed_repositories=repositories)

    write_receipts(originals)
    baseline = verify()
    assert baseline.passed, baseline.reason
    receipts = copy.deepcopy(originals)
    for receipt in receipts:
        for finding in receipt['findings']:
            if case == 'finding_missing_repo':
                finding['file'] = 'main.py'
            elif case == 'finding_wrong_repo':
                finding['file'] = 'forged@' + finding['file'].split('@', 1)[1]
            elif case == 'finding_wrong_source':
                identity, path = finding['file'].split('/', 1)
                finding['file'] = identity.split('@')[0] + '@' + '0' * 64 + '/' + path
            elif case == 'finding_missing_file':
                del finding['file']
            elif case == 'finding_nonstring_file':
                finding['file'] = []
        if case == 'excerpt_wrong_repo':
            for excerpt in receipt['code_excerpts']:
                excerpt['file'] = 'forged@' + '0' * 64 + '/main.py'
    unchanged = copy.deepcopy(receipts)
    for original, restored in zip(originals, unchanged):
        field = 'code_excerpts' if case == 'excerpt_wrong_repo' else 'findings'
        for before, after in zip(original[field], restored[field]):
            after['file'] = before['file']
    assert unchanged == originals
    write_receipts(receipts)
    result = verify()
    (tmp_path / 'verification.json').write_text(json.dumps({
        'case': case, 'passed': result.passed, 'reason': result.reason,
        'baseline_passed': baseline.passed, 'finding_count': len(findings),
    }, indent=2))
    if case in ('baseline', 'empty_findings'):
        assert result.passed, result.reason
    else:
        assert not result.passed, 'tampered receipt accepted: ' + case
        if case.startswith('finding_'):
            assert 'finding repository/source identity mismatch' in result.reason
