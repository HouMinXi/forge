"""Offline control-rendering and real-capability prerequisite contracts."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / '.github/scripts/render_linux_workflow.py'
SPEC = importlib.util.spec_from_file_location('linux_renderer_under_test', SCRIPT)
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)


def fake_repo(tmp_path):
    package = tmp_path / '.github/scripts/forge_ci'
    package.mkdir(parents=True)
    for name in renderer.HELPERS:
        (package / name).write_text('"""fixed test source"""\n')
    (package / 'setup_policy.py').write_text("NATIVE_KEYS = ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'FORGE_RUNNER_UID', 'FORGE_RUNNER_GID')\nCONFIG = {}\n")
    (package / 'launch.py').write_text('"""fixed test verifier"""\n')
    return tmp_path


def workflow(repo):
    return yaml.safe_load(renderer.render(repo))


def test_literal_branch_and_non_skipped_fixed_job(tmp_path):
    result = workflow(fake_repo(tmp_path))
    assert result['on'] == {'push': {'branches': ['fix/review-correctness-linux-ci']}}
    assert result['permissions'] == {'contents': 'read'}
    assert result['concurrency']['cancel-in-progress'] is False
    job = result['jobs']['linux-tests']
    assert job['name'] == 'linux-tests'
    assert 'if' not in job and 'strategy' not in job
    assert job['timeout-minutes'] == 90 and job['runs-on'] == 'ubuntu-24.04'
    assert job['env'] == {'PYTHONDONTWRITEBYTECODE': '1', 'SEMGREP_SEND_METRICS': 'off',
                          'SEMGREP_ENABLE_VERSION_CHECK': '0', 'OTEL_SDK_DISABLED': 'true'}


def test_setup_precedes_actions_and_no_late_privileged_install(tmp_path):
    result = workflow(fake_repo(tmp_path))
    steps = result['jobs']['linux-tests']['steps']
    setup = next(i for i, s in enumerate(steps) if s.get('id') == 'policy_setup')
    checkout = next(i for i, s in enumerate(steps) if s.get('uses') == renderer.CHECKOUT)
    assert setup < checkout
    assert all('uses' not in s for s in steps[:setup + 1])
    assert all('sudo' not in s.get('run', '') for s in steps[checkout:])
    assert all('apt-get' not in s.get('run', '') and 'dpkg --install' not in s.get('run', '') for s in steps[checkout:])
    assert steps[checkout]['with'] == {'ref': '${{ github.sha }}', 'fetch-depth': 0, 'persist-credentials': False}
    assert 'PYTHONPATH=' not in steps[setup]['run']
    assert '/usr/bin/python3 -B -I -S' in steps[setup]['run']
    verify = next(s['run'] for s in steps if s['name'] == 'Verify complete source and live identity before imports')
    assert verify.index('verify_initial_checkout') < verify.index('from forge_ci import launch')
    assert 'EXPECTED_HELPERS' not in verify
    assert "launch.read_regular(Path(os.environ['GITHUB_EVENT_PATH']), limit=launch.MAX_API)" in verify
    assert "Path(os.environ['GITHUB_EVENT_PATH']).read_bytes()" not in verify


def test_source_chunks_reassemble_exactly_and_are_byte_bound(tmp_path):
    repo = fake_repo(tmp_path)
    path = repo / '.github/scripts/forge_ci/setup_policy.py'
    path.write_text(path.read_text() + '# payload\n' * 3200)
    steps = workflow(repo)['jobs']['linux-tests']['steps']
    parts = []
    for step in steps:
        if not step['name'].startswith('Stage fixed policy source'):
            continue
        run = step['run']
        code = run.split("<<'PYSTAGE'", 1)[1].split('\n', 1)[1].rsplit('\nPYSTAGE', 1)[0]
        tree = ast.parse(code)
        raw = None
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'raw' for t in node.targets):
                raw = ast.literal_eval(node.value.func.value).encode()
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'expected' for t in node.targets):
                record = ast.literal_eval(node.value)
        assert raw is not None and len(raw) == record['bytes']
        assert hashlib.sha256(raw).hexdigest() == record['sha256']
        parts.append(raw)
        assert 'os.O_EXCL | os.O_NOFOLLOW' in code
        assert len(run) <= 20000
    assert b''.join(parts) == path.read_bytes() and len(parts) > 1


def test_heredoc_and_workflow_expression_source_rejected(tmp_path):
    repo = fake_repo(tmp_path)
    path = repo / '.github/scripts/forge_ci/setup_policy.py'
    for hostile in ('\nPYSETUP\n', '${{ github.token }}'):
        path.write_text("NATIVE_KEYS = ('GITHUB_RUN_ID',)\n# " + hostile)
        with pytest.raises(AssertionError):
            renderer.render(repo)


def test_generated_scripts_are_shell_and_python_parseable(tmp_path):
    steps = workflow(fake_repo(tmp_path))['jobs']['linux-tests']['steps']
    for step in steps:
        if 'run' not in step:
            continue
        completed = subprocess.run(['/bin/bash', '-n'], input=step['run'], text=True, capture_output=True)
        assert completed.returncode == 0, completed.stderr
        for marker in ('PYSTAGE', 'PYSETUP', 'PYBOOT', 'PYVERIFYCHUNK', 'PYPREFLIGHT'):
            token = "<<'" + marker + "'"
            if token in step['run']:
                code = step['run'].split(token, 1)[1].split('\n', 1)[1].rsplit('\n' + marker, 1)[0]
                ast.parse(code)


def test_fixed_install_and_lifecycle_bounds(tmp_path):
    steps = workflow(fake_repo(tmp_path))['jobs']['linux-tests']['steps']
    install = next(s for s in steps if s['name'] == 'Install declared extras and qualified pytest')
    assert "'.[dev,mcp,semgrep,vertex]' 'pytest==9.1.1'" in install['run']
    assert 'sudo' not in install['run']
    run = next(s for s in steps if s['name'] == 'Verify production boundary and run complete test phases')
    assert run['timeout-minutes'] == 75 and '--receipt "$EVIDENCE/launch-bootstrap.json"' in run['run']
    assert '--manifest' not in run['run']
    upload = steps[-1]
    assert upload['if'] == '${{ always() }}' and upload['timeout-minutes'] == 5
    assert upload['with']['if-no-files-found'] == 'error'


def profile_function(fdinfo, mountinfo):
    tree = ast.parse(renderer.PREFLIGHT_PYTHON)
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'disk_profile')
    class FakePath:
        def __init__(self, path):
            self.path = path
        def read_text(self, **kwargs):
            return fdinfo if self.path.startswith('/proc/self/fdinfo/') else mountinfo
    namespace = {'Path': FakePath}
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<disk-profile-test>', 'exec'), namespace)  # noqa: S102 - execute only this fixed function with synthetic read-only inputs
    return namespace['disk_profile']


@pytest.mark.parametrize('kind', ['ext4', 'btrfs'])
def test_real_disk_profile_follows_fd_mount_identity(kind):
    function = profile_function('pos:\t0\nmnt_id:\t56\n', f'55 1 0:1 / /ignored rw - overlay overlay rw\n56 1 8:1 / /tmp rw - {kind} /dev/sda1 rw\n')
    assert function(7) == {'filesystem_type': kind, 'mount_id': 56}


@pytest.mark.parametrize('kind', ['overlay', 'tmpfs', 'ramfs', 'nfs', 'unknown'])
def test_no_fake_overlay_or_memory_durability(kind):
    function = profile_function('mnt_id:\t56\n', f'56 1 8:1 / /tmp rw - {kind} disk rw\n')
    with pytest.raises(AssertionError, match='unsupported'):
        function(7)


@pytest.mark.parametrize('fdinfo,mountinfo', [
    ('pos:0\n', ''), ('mnt_id:56\nmnt_id:56\n', ''), ('mnt_id:56\n', 'bad\n'),
    ('mnt_id:56\n', '55 1 8:1 / /tmp rw - ext4 disk rw\n'),
    ('mnt_id:56\n', '56 1 8:1 / /tmp rw - ext4 disk rw\n' * 2),
])
def test_ambiguous_missing_mount_facts_fail(fdinfo, mountinfo):
    with pytest.raises(AssertionError):
        profile_function(fdinfo, mountinfo)(7)


def test_durability_probe_exercises_real_operations_without_product_patch():
    code = renderer.PREFLIGHT_PYTHON
    assert 'os.fsync(file_fd)' in code and code.count('os.fsync(fd)') >= 3
    assert "os.rename('before', 'after', src_dir_fd=fd, dst_dir_fd=fd)" in code
    assert "os.readlink('/proc/self/ns/mnt')" in code
    assert 'full invocation_audit tests remain mandatory' in code
    assert 'monkeypatch' not in code and 'setattr' not in code
    assert len(b'forge-ci-durability\n') == 20


def test_check_mode_rejects_modified_render(tmp_path):
    repo = fake_repo(tmp_path)
    (repo / '.github/workflows').mkdir()
    renderer.main(['--repo', str(repo)])
    renderer.main(['--repo', str(repo), '--check'])
    (repo / renderer.WORKFLOW_PATH).write_text('name: wrong\n')
    with pytest.raises(SystemExit) as failure:
        renderer.main(['--repo', str(repo), '--check'])
    assert failure.value.code == 1


def test_literal_source_helper_hash_changes_render(tmp_path):
    repo = fake_repo(tmp_path)
    before = renderer.render(repo)
    (repo / '.github/scripts/forge_ci/controller.py').write_text('# reviewed different helper\n')
    assert renderer.render(repo) != before


def test_long_verifier_staged_as_verified_ordinary_literals(tmp_path):
    repo = fake_repo(tmp_path)
    path = repo / '.github/scripts/forge_ci/launch.py'
    path.write_text('# closed fixed verifier\n' * 1600)
    steps = workflow(repo)['jobs']['linux-tests']['steps']
    parts = []
    for step in steps:
        assert len(step.get('run', '')) <= 20000
        if not step['name'].startswith('Stage fixed source verifier'):
            continue
        run = step['run']
        assert 'sudo' not in run and '/usr/bin/python3 -B -I -S' in run
        code = run.split("<<'PYVERIFYCHUNK'", 1)[1].split('\n', 1)[1].rsplit('\nPYVERIFYCHUNK', 1)[0]
        tree = ast.parse(code)
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'raw' for t in node.targets):
                raw = ast.literal_eval(node.value.func.value).encode()
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'expected' for t in node.targets):
                record = ast.literal_eval(node.value)
        assert len(raw) == record['bytes'] and hashlib.sha256(raw).hexdigest() == record['sha256']
        assert 'os.O_EXCL | os.O_NOFOLLOW' in code
        parts.append(raw)
    assert len(parts) > 1 and b''.join(parts) == path.read_bytes()
    final = next(s['run'] for s in steps if s['name'] == 'Verify complete source and live identity before imports')
    assert final.index('hashlib.sha256(raw).hexdigest()') < final.index('exec(compile')
    assert 'VERIFIER_SOURCE' not in final and 'VERIFIER_PART_RECORDS' not in final
