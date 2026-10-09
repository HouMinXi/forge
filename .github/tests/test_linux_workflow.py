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
    (package / 'setup_policy.py').write_bytes((ROOT / '.github/scripts/forge_ci/setup_policy.py').read_bytes())
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
    assert all('sudo' not in s.get('run', '') for s in steps[checkout:] if s.get('id') != 'credential_cleanup')
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
        for marker in ('PYSTAGE', 'PYSETUP', 'PYBOOT', 'PYVERIFYCHUNK', 'PYPREFLIGHT', 'PYENTRY', 'PYCREDENTIALCLEANUP'):
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


def _heredoc(run, marker):
    return run.split("<<'" + marker + "'", 1)[1].split('\n', 1)[1].rsplit('\n' + marker, 1)[0]


def test_native_secret_reference_exists_only_on_trusted_precheckout_step(tmp_path):
    result = workflow(fake_repo(tmp_path))
    job = result['jobs']['linux-tests']
    steps = job['steps']
    setup_index = next(i for i, step in enumerate(steps) if step.get('id') == 'policy_setup')
    checkout_index = next(i for i, step in enumerate(steps) if step.get('uses') == renderer.CHECKOUT)
    assert setup_index < checkout_index
    assert result['permissions'] == {'contents': 'read'}
    assert job['env'].get('FORGE_CI_METADATA_TOKEN') is None
    secret_steps = [step for step in steps if '${{ github.token }}' in str(step)]
    assert secret_steps == [steps[setup_index]]
    setup = steps[setup_index]
    assert setup['env'] == {'FORGE_CI_METADATA_TOKEN': '${{ github.token }}'}
    run = setup['run']
    assert run.startswith('set +x\nexport -n FORGE_CI_METADATA_TOKEN\n')
    assert "trap 'unset FORGE_CI_METADATA_TOKEN' EXIT" in run
    assert "builtin printf '%s' \"$FORGE_CI_METADATA_TOKEN\" | /usr/bin/sudo" in run
    privileged = run.split('| /usr/bin/sudo', 1)[1]
    assert 'FORGE_CI_METADATA_TOKEN' not in privileged
    assert "<<'" not in run  # Secret stdin is not overwritten by a script heredoc.
    assert '-B -I -S "/var/lib/forge-ci-bootstrap/${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}/bootstrap-entry.py"' in run
    for step in steps:
        if step is not setup:
            assert 'FORGE_CI_METADATA_TOKEN' not in str(step)


def test_entrypoint_literal_is_independently_hashed_and_inventory_exact(tmp_path):
    repo = fake_repo(tmp_path)
    steps = workflow(repo)['jobs']['linux-tests']['steps']
    staging = next(step for step in steps if step['name'] == 'Stage fixed bootstrap entrypoint')
    code = _heredoc(staging['run'], 'PYENTRY')
    tree = ast.parse(code)
    assignments = {node.targets[0].id: node.value for node in tree.body
                   if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)}
    entry = ast.literal_eval(assignments['raw'].func.value)
    record = ast.literal_eval(assignments['expected'])
    assert record == {'name': 'bootstrap-entry.py', 'bytes': len(entry.encode()),
                      'sha256': hashlib.sha256(entry.encode()).hexdigest()}
    ast.parse(entry)
    assert "== {p['name'] for p in parts}\n" in code
    assert code.rstrip().endswith("assert {p.name for p in stage.iterdir()} == {p['name'] for p in parts} | {'bootstrap-entry.py'}")
    assert "== {p['name'] for p in parts} | {'bootstrap-entry.py'}" in entry
    assert "credential_layout={'parts': parts}" in entry
    assert "os.O_EXCL | os.O_NOFOLLOW" in code
    assert "hashlib.sha256(raw).hexdigest()" in code and "stat.S_IMODE(entry_info.st_mode) == 0o400" in entry
    assert '${{' not in entry and 'FORGE_CI_METADATA_TOKEN' not in entry
    source = (repo / '.github/scripts/forge_ci/setup_policy.py').read_bytes()
    assert repr(hashlib.sha256(source).hexdigest()) in entry


def test_cleanup_is_always_fixed_root_literal_before_upload(tmp_path):
    steps = workflow(fake_repo(tmp_path))['jobs']['linux-tests']['steps']
    cleanup = next(step for step in steps if step.get('id') == 'credential_cleanup')
    assert cleanup['if'] == '${{ always() }}' and cleanup['timeout-minutes'] == 1
    assert steps.index(cleanup) == len(steps) - 2
    assert 'continue-on-error' not in cleanup and 'env' not in cleanup
    code = _heredoc(cleanup['run'], 'PYCREDENTIALCLEANUP')
    tree = ast.parse(code)
    definitions = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    assert 'cleanup_metadata_credential' in definitions
    assert not {'bootstrap', 'stage_metadata_credential', '_read_metadata_credential', 'MetadataReader',
                'install_vendor', 'live_identity'} & definitions
    assert 'Authorization' not in code and 'FORGE_CI_METADATA_TOKEN' not in code
    assert 'forge_ci' not in code and 'sys.path' not in code
    assert 'raise SystemExit(1)' in code
    assert "'token_absent': False" in code
    assert 'credential-cleanup.json' in cleanup['run']
    # GitHub's bash shell runs with pipefail; a failing root cleanup cannot be
    # converted into success by the nonsecret receipt tee.
    result = subprocess.run(['/bin/bash', '-e', '-o', 'pipefail', '-c', 'false | cat'], capture_output=True)
    assert result.returncode != 0


def test_rendered_cleanup_main_propagates_failure_without_exception_payload(tmp_path, monkeypatch, capsys):
    import json
    steps = workflow(fake_repo(tmp_path))['jobs']['linux-tests']['steps']
    cleanup = next(step for step in steps if step.get('id') == 'credential_cleanup')
    tree = ast.parse(_heredoc(cleanup['run'], 'PYCREDENTIALCLEANUP'))
    main = next(node for node in tree.body if isinstance(node, ast.Try))
    monkeypatch.setenv('GITHUB_RUN_ID', '123')
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT', '1')
    def fail(*args, **kwargs):
        raise RuntimeError('TEST_SENTINEL_MUST_NOT_APPEAR')
    namespace = {'os': __import__('os'), 'json': json, 'layout': {},
                 '_id': lambda value, *args, **kw: int(value), 'trusted_boot_id': lambda: 'boot',
                 'cleanup_metadata_credential': fail}
    with pytest.raises(SystemExit) as stopped:
        exec(compile(ast.Module(body=[main], type_ignores=[]), '<fixed-cleanup-main>', 'exec'), namespace)  # noqa: S102 - fixed generated control with mocked cleanup only
    assert stopped.value.code == 1
    output = capsys.readouterr()
    assert json.loads(output.out) == {'schema_version': 1, 'status': 'STOP', 'token_absent': False,
                                     'error': 'credential cleanup failed'}
    assert output.err == '' and 'TEST_SENTINEL' not in output.out


def test_auth_shell_unexports_secret_before_every_external_child(tmp_path):
    import json
    import os
    import shlex
    import sys

    steps = workflow(fake_repo(tmp_path / 'repo'))['jobs']['linux-tests']['steps']
    original = next(step['run'] for step in steps if step.get('id') == 'policy_setup')
    sentinel = 'TEST_ONLY_FAKE_METADATA_SENTINEL'
    log = tmp_path / 'children.jsonl'
    child = tmp_path / 'fake-external'
    child.write_text('#!' + sys.executable + '\n' + r'''
import json, os, sys
kind = sys.argv[1]
data = sys.stdin.buffer.read() if kind != 'id' else b''
record = {'kind': kind, 'token_key': 'FORGE_CI_METADATA_TOKEN' in os.environ,
          'token_in_env': any('TEST_ONLY_FAKE_METADATA_SENTINEL' in value for value in os.environ.values()),
          'token_in_argv': any('TEST_ONLY_FAKE_METADATA_SENTINEL' in value for value in sys.argv),
          'stdin_token': data == b'TEST_ONLY_FAKE_METADATA_SENTINEL'}
with open(os.environ['CHILD_LOG'], 'a') as stream:
    stream.write(json.dumps(record) + '\n')
if kind == 'id':
    print('1001')
elif kind == 'sudo':
    assert data == b'TEST_ONLY_FAKE_METADATA_SENTINEL'
    print('PASS')
elif kind == 'tee':
    assert data == b'PASS\n'
    sys.stdout.buffer.write(data)
''', encoding='utf-8')
    child.chmod(0o700)
    executable = shlex.quote(str(child))
    script = original.replace('/usr/bin/id', executable + ' id').replace(
        '/usr/bin/sudo', executable + ' sudo').replace('/usr/bin/tee', executable + ' tee')
    result = subprocess.run(['/bin/bash', '-e', '-o', 'pipefail', '-c', script],
                            env={**os.environ, 'FORGE_CI_METADATA_TOKEN': sentinel, 'CHILD_LOG': str(log)},
                            text=True, capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert sentinel not in result.stdout + result.stderr
    records = [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()]
    assert sorted(row['kind'] for row in records) == ['id', 'id', 'sudo', 'tee']
    assert all(not row['token_key'] and not row['token_in_env'] and not row['token_in_argv'] for row in records)
    assert [row['kind'] for row in records if row['stdin_token']] == ['sudo']


def test_staged_source_corruption_fails_before_entrypoint_creation(tmp_path):
    import os
    import stat
    from types import SimpleNamespace

    repo = fake_repo(tmp_path / 'repo')
    steps = workflow(repo)['jobs']['linux-tests']['steps']
    staging = next(step for step in steps if step['name'] == 'Stage fixed bootstrap entrypoint')
    tree = ast.parse(_heredoc(staging['run'], 'PYENTRY'))
    start = next(i for i, node in enumerate(tree.body) if isinstance(node, ast.Assign)
                 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'chunks')
    end = next(i for i, node in enumerate(tree.body[start:], start) if isinstance(node, ast.Assign)
               and isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'raw'
               and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute)
               and node.value.func.attr == 'encode')
    code = compile(ast.Module(body=tree.body[start:end], type_ignores=[]), '<fixed-part-verifier>', 'exec')
    parts = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'parts')
    source = (repo / '.github/scripts/forge_ci/setup_policy.py').read_text(encoding='utf-8')
    stage = tmp_path / 'ordinary-user-stage'
    stage.mkdir()
    for part, raw in zip(parts, [source[i:i + 8000].encode() for i in range(0, len(source), 8000)], strict=True):
        (stage / part['name']).write_bytes(raw)
        (stage / part['name']).chmod(0o400)
    def modeled_root_stat(fd):
        real = os.fstat(fd)
        return SimpleNamespace(st_mode=real.st_mode, st_uid=0, st_gid=0,
                               st_nlink=real.st_nlink, st_size=real.st_size)
    modeled_os = SimpleNamespace(open=os.open, fdopen=os.fdopen, fstat=modeled_root_stat,
                                 O_RDONLY=os.O_RDONLY, O_NOFOLLOW=os.O_NOFOLLOW, O_NONBLOCK=os.O_NONBLOCK)
    namespace = {'os': modeled_os, 'stat': stat, 'hashlib': hashlib, 'stage': stage, 'parts': parts}
    exec(code, namespace)  # noqa: S102 - fixed literal verifier with ordinary temporary files and modeled ownership.
    first = stage / parts[0]['name']
    raw = first.read_bytes()
    first.chmod(0o600)
    first.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
    first.chmod(0o400)
    with pytest.raises(AssertionError):
        exec(code, namespace)  # noqa: S102 - exact preceding fixed verifier, negative changed-part input.
    assert not (stage / 'bootstrap-entry.py').exists()
