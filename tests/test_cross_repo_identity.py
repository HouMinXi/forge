"""Real repository/provider/receipt identity regression tests."""
import json

import pytest

from code_forge import cross_repo, llm_invoke, machine
from code_forge.source import compute_source_hash
from code_forge.llm_invoke import LLMResult, Usage
from code_forge.state import Mode, Verdict
from code_forge.verify import _load_receipts, parse_diff_files, run_verify
from tests.test_cross_repo import _make_repo


def run_case(tmp_path, monkeypatch, same_name=False, corrupt=None, empty=False,
             with_findings=False, primary_empty=False):
    primary = _make_repo(tmp_path, monkeypatch, 'primary', filename='main.py')
    filename = 'main.py' if same_name else 'sibling.py'
    sibling = _make_repo(tmp_path, monkeypatch, 'sibling', filename=filename,
                         content_v1='sibling = 10\n', content_v2='sibling = 20\n')
    primary_ref = 'main..main' if primary_empty else 'main..feature'
    own = cross_repo.get_sibling_diff(primary, primary_ref)
    other = cross_repo.get_sibling_diff(sibling, 'main..main' if empty else 'main..feature')
    repositories = {'primary': own, 'sibling': other}
    def path(label, file):
        return '%s@%s/%s' % (label, compute_source_hash(git_diff=repositories[label]), file)
    excerpts = [{'file': path('primary', 'main.py'), 'start_line': 1,
                 'end_line': 1, 'content': 'x = 2'}]
    if primary_empty:
        excerpts = []
    if not empty:
        excerpts.append({'file': path('sibling', filename), 'start_line': 1,
                         'end_line': 1, 'content': 'sibling = 20'})
    if corrupt == 'revision':
        excerpts[-1]['file'] = excerpts[-1]['file'].replace(compute_source_hash(git_diff=other), '0' * 64)
    elif corrupt == 'repository':
        excerpts[-1]['file'] = excerpts[-1]['file'].replace('sibling@', 'forged@')
    elif corrupt == 'missing':
        excerpts[-1]['file'] = filename
    elif corrupt == 'content':
        excerpts[-1]['content'] = 'x = 2'
    elif corrupt == 'range':
        excerpts[-1]['end_line'] = 2
    elif corrupt == 'omit':
        excerpts.pop()
    prompts = []
    falsifier_prompts = []
    def invoke(*args, **kwargs):
        prompt = kwargs.get('prompt', args[0] if args else '')
        if prompt.startswith('You are a code review verifier.'):
            falsifier_prompts.append(prompt)
            assert 'File: main.py\n' in prompt
            assert ('+x = 2' in prompt) != ('+sibling = 20' in prompt)
            assert ('+sibling = 20' in prompt) == ('sibling assignment' in prompt)
            return LLMResult({'verdict': 'DISMISSED'}, Usage(), 0.01)
        prompts.append(prompt)
        findings = [dict(file=e['file'], line=1,
                         description=('sibling assignment' if e['file'].startswith('sibling@') else 'primary assignment'),
                         severity='P1') for e in excerpts] if with_findings else []
        if corrupt == 'finding':
            findings = [dict(file='forged@' + '0' * 64 + '/main.py', line=1,
                             description='Wrong repo', severity='P1')]
        return LLMResult({'findings': findings, 'code_excerpts': excerpts}, Usage(), 0.01)
    monkeypatch.setattr(llm_invoke, 'llm_invoke', invoke)
    monkeypatch.setattr('code_forge.falsify_real.llm_invoke', invoke)
    for module, cls in [('taint', 'TaintRunner'), ('runtime', 'RuntimeRunner'),
                        ('graph_triage', 'GraphTriageRunner'), ('daemon_state', 'DaemonStateRunner'),
                        ('legacy', 'LegacyRunner'), ('cross_repo_impact', 'CrossRepoImpactRunner')]:
        monkeypatch.setattr(f'code_forge.{module}.{cls}.run', lambda *a, **kw: [])
    observed = []
    real_run = machine.StateMachine.run
    def observe(sm):
        verdict = real_run(sm)
        if sm.coverage_l1_active:
            args = (sm.cwd, sm.source_hash, parse_diff_files(own))
            options = dict(diff_text=own, required_cycles=1, respect_floor=False)
            receipts = _load_receipts(sm.cwd / '.code-forge' / 'receipts')
            observed.append((verdict, receipts, list(sm._state.infra_errors)))
            if corrupt == 'finding':
                assert any(f.source == 'UNTRUSTED' for f in sm._state.findings)
                assert not falsifier_prompts
            # Existing single-repository verifier must NOT authorize joint evidence.
            assert not run_verify(*args, **options).passed
            if corrupt is None:
                assert verdict == Verdict.PASS, sm._state.infra_errors
                verified = run_verify(*args, **options, reviewed_repositories=repositories)
                assert verified.passed, verified.reason
                stale = dict(repositories, sibling=other + 'different source\n')
                assert not run_verify(*args, **options, reviewed_repositories=stale).passed
                receipt_path = sm.cwd / '.code-forge' / 'receipts' / 'receipt-c1p1.json'
                original = receipt_path.read_bytes()
                data = json.loads(original)
                data['reviewed_repositories']['sibling'] = '0' * 64
                receipt_path.write_text(json.dumps(data))
                assert not run_verify(*args, **options, reviewed_repositories=repositories).passed
                receipt_path.write_bytes(original)
                assert run_verify(*args, **options, reviewed_repositories=repositories).passed
        assert sm.falsifier._diff_text == (own if sm.coverage_l1_active else other)
        return verdict
    monkeypatch.setattr(machine.StateMachine, 'run', observe)
    output = []
    result = cross_repo.run_cross_repo(
        primary_path=primary, primary_label='primary', primary_ref=primary_ref,
        siblings=[{'label': 'sibling', 'repo': str(sibling),
                   'ref': 'main..main' if empty else 'main..feature'}],
        mode=Mode.CI, engine_choice='real', backend=None, max_rounds=3,
        clean_round_threshold=3, max_fix_attempts=5, output_fn=output.append,
        gate_config={'test': {'command': ['true']}, 'verify': {'required_cycles': 1}},
    )
    assert len(observed) == 1
    verdict, receipts, infra = observed[0]
    if corrupt:
        assert result == Verdict.FAIL
        assert verdict == Verdict.FAIL
        assert infra or any(r['pass_status'] != 'COMPLETED' for r in receipts)
    else:
        assert result == Verdict.PASS
        assert verdict == Verdict.PASS
        assert len(receipts) == 3
        for receipt in receipts:
            assert receipt['reviewed_repositories'] == {
                k: compute_source_hash(git_diff=v) for k, v in repositories.items()}
            assert len(receipt['code_excerpts']) == (1 if empty or primary_empty else 2)
            assert {e['file'] for e in receipt['code_excerpts']} == {e['file'] for e in excerpts}
        if not primary_empty:
            assert all(path('primary', 'main.py') in p for p in prompts)
        if not empty:
            assert all(path('sibling', filename) in p for p in prompts)
    if with_findings:
        assert any('[sibling] sibling@' in line for line in output)
        assert not any('[primary] sibling@' in line for line in output)
        assert len(falsifier_prompts) == 6
        for receipt in observed[0][1]:
            assert {f['file'] for f in receipt['findings']} == {e['file'] for e in excerpts}
    return result


@pytest.mark.parametrize('same_name', [False, True])
def test_joint_identity_round_trip(tmp_path, monkeypatch, same_name):
    run_case(tmp_path, monkeypatch, same_name=same_name)


@pytest.mark.parametrize('corrupt', ['revision', 'repository', 'missing', 'content', 'range', 'omit', 'finding'])
def test_joint_identity_rejects_bad_evidence(tmp_path, monkeypatch, corrupt):
    run_case(tmp_path, monkeypatch, same_name=True, corrupt=corrupt)


def test_empty_sibling(tmp_path, monkeypatch):
    run_case(tmp_path, monkeypatch, empty=True)


def test_empty_primary_still_attests_sibling(tmp_path, monkeypatch):
    run_case(tmp_path, monkeypatch, primary_empty=True)


def test_findings_keep_repository_and_falsifier_diff(tmp_path, monkeypatch):
    run_case(tmp_path, monkeypatch, same_name=True, with_findings=True)
