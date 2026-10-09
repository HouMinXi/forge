"""Render/check fixed current-task Linux CI; never publishes or runs setup.

The workflow literals are independently reviewed before root publication. Their
hashes detect drift; candidate self-hashes do not confer privileged authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import yaml

BRANCH = "ci/baseline-b-4dd7214cf1a24483a42c7e19cfc8ec28"
WORKFLOW_PATH = ".github/workflows/linux-tests.yml"
STAGING_ROOT = "/var/lib/forge-ci-bootstrap"
HELPERS = (
    "__init__.py", "facts.py", "launch.py", "admission.py", "setup_policy.py",
    "controller.py", "outcomes.py", "payload.py", "probes.py", "pytest_observer.py", "user_service.py", "baseline_measurement.py",
    "python_prefix.py",
)
CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
SETUP_PYTHON = "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97"
UPLOAD = "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9"


def literal(value):
    for quote in ('"""', "'''"):
        if quote not in value and not value.endswith(("\\", quote[0])):
            return "r" + quote + value + quote
    return "(\n" + "".join("    " + repr(line) + "\n" for line in value.splitlines(keepends=True)) + ")"


class WorkflowDumper(yaml.SafeDumper):
    pass


def readable_string(dumper, value):
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|" if "\n" in value else None)


WorkflowDumper.add_representer(str, readable_string)

STAGE_CODE = r'''import hashlib
import os
from pathlib import Path
import stat

assert os.getuid() == os.geteuid() == os.getgid() == os.getegid() == 0
values = [os.environ[key] for key in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT')]
assert all(value.isascii() and value.isdecimal() and 0 < int(value) < 2**63 and str(int(value)) == value for value in values)
assert values[1] == '1'
base = Path(STAGING_ROOT_LITERAL)
stage = base / '-'.join(values)
for path in (base, stage):
    assert path.is_absolute() and path.resolve(strict=False) == path
    if FIRST_PART:
        path.mkdir(mode=0o700, exist_ok=path != stage)
    info = path.lstat()
    assert stat.S_ISDIR(info.st_mode) and info.st_uid == info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o700
raw = SOURCE_PART.encode('utf-8')
expected = PART_RECORD
assert len(raw) == expected['bytes'] and hashlib.sha256(raw).hexdigest() == expected['sha256']
fd = os.open(stage / expected['name'], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
with os.fdopen(fd, 'wb') as stream:
    stream.write(raw)
    stream.flush()
    os.fsync(stream.fileno())
'''

BOOT_CODE = r'''import hashlib
import os
from pathlib import Path
import stat

assert os.getuid() == os.geteuid() == os.getgid() == os.getegid() == 0
values = [os.environ[key] for key in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT')]
assert all(value.isascii() and value.isdecimal() and 0 < int(value) < 2**63 and str(int(value)) == value for value in values)
assert values[1] == '1'
base = Path(STAGING_ROOT_LITERAL)
stage = base / '-'.join(values)
for path in (base, stage):
    info = path.lstat()
    assert path.resolve(strict=True) == path and stat.S_ISDIR(info.st_mode)
    assert info.st_uid == info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o700
parts = SOURCE_PART_RECORDS
assert {p.name for p in stage.iterdir()} == {p['name'] for p in parts} | {'bootstrap-entry.py'}
entry_info = (stage / 'bootstrap-entry.py').lstat()
assert stat.S_ISREG(entry_info.st_mode) and entry_info.st_uid == entry_info.st_gid == 0
assert stat.S_IMODE(entry_info.st_mode) == 0o400 and entry_info.st_nlink == 1
chunks = []
for part in parts:
    fd = os.open(stage / part['name'], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        assert stat.S_ISREG(info.st_mode) and info.st_uid == info.st_gid == 0
        assert stat.S_IMODE(info.st_mode) == 0o400 and info.st_nlink == 1 and info.st_size == part['bytes']
        raw = stream.read(part['bytes'] + 1)
    assert len(raw) == part['bytes'] and hashlib.sha256(raw).hexdigest() == part['sha256']
    chunks.append(raw)
raw = b''.join(chunks)
assert len(raw) == SOURCE_BYTE_COUNT and hashlib.sha256(raw).hexdigest() == SOURCE_SHA256
source = raw.decode('utf-8')
namespace = {'__name__': 'fixed_setup_bootstrap'}
exec(compile(source, '<fixed-workflow-policy-setup>', 'exec'), namespace)
result = namespace['bootstrap'](namespace['CONFIG'], source, credential_layout={'parts': parts})
assert type(result) is dict and result.get('status') == 'PASS'
assert result.get('load_attempted') is True and result.get('positive_passed') is True
'''

# These ordinary-user fixed literals run after checkout but before any candidate import.
VERIFIER_STAGE_CODE = STAGE_CODE.replace(
    'assert os.getuid() == os.geteuid() == os.getgid() == os.getegid() == 0',
    'assert os.getuid() == os.geteuid() > 0 and os.getgid() == os.getegid() > 0',
).replace('base = Path(STAGING_ROOT_LITERAL)', "base = Path(os.environ['RUNNER_TEMP'])").replace(
    "stage = base / '-'.join(values)", "stage = base / ('forge-ci-verifier-' + '-'.join(values))"
).replace('for path in (base, stage):', 'for path in (stage,):').replace(
    'info.st_uid == info.st_gid == 0', 'info.st_uid == os.getuid() and info.st_gid == os.getgid()'
)

VERIFY_CODE = r'''import hashlib
import os
from pathlib import Path
import stat
import sys

values = [os.environ[key] for key in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT')]
assert all(value.isascii() and value.isdecimal() and 0 < int(value) < 2**63 and str(int(value)) == value for value in values)
assert values[1] == '1'
stage = Path(os.environ['RUNNER_TEMP']) / ('forge-ci-verifier-' + '-'.join(values))
info = stage.lstat()
assert stage.resolve(strict=True) == stage and stat.S_ISDIR(info.st_mode)
assert info.st_uid == os.getuid() and info.st_gid == os.getgid() and stat.S_IMODE(info.st_mode) == 0o700
parts = VERIFIER_PART_RECORDS
assert {p.name for p in stage.iterdir()} == {p['name'] for p in parts}
chunks = []
for part in parts:
    fd = os.open(stage / part['name'], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        assert stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_gid == os.getgid()
        assert stat.S_IMODE(info.st_mode) == 0o400 and info.st_nlink == 1 and info.st_size == part['bytes']
        raw = stream.read(part['bytes'] + 1)
    assert len(raw) == part['bytes'] and hashlib.sha256(raw).hexdigest() == part['sha256']
    chunks.append(raw)
raw = b''.join(chunks)
assert len(raw) == VERIFIER_BYTE_COUNT and hashlib.sha256(raw).hexdigest() == VERIFIER_SHA256
source = raw.decode('utf-8')
namespace = {'__name__': 'fixed_checkout_verifier'}
exec(compile(source, '<fixed-workflow-checkout-verifier>', 'exec'), namespace)
root = Path(os.environ['GITHUB_WORKSPACE']).resolve(strict=True)
checkout = namespace['verify_initial_checkout'](root, EXPECTED_HELPERS)
parent = root / '.github/scripts'
sys.dont_write_bytecode = True
sys.path.insert(0, str(parent))
from forge_ci import launch
event = launch.parse_json(launch.read_regular(Path(os.environ['GITHUB_EVENT_PATH']), limit=launch.MAX_API), limit=launch.MAX_API)
record = launch.validate_launch(os.environ, event, checkout, evidence_dir=Path(os.environ['EVIDENCE']))
launch.write_receipt(Path(os.environ['EVIDENCE']) / 'launch-bootstrap.json', record)
'''

PREFLIGHT_PYTHON = r'''import json
import os
from pathlib import Path
import resource
import select
import stat
import subprocess
import sys
import tempfile


def disk_profile(fd):
    info = Path(f'/proc/self/fdinfo/{fd}').read_text(encoding='ascii')
    assert len(info) <= 16384, 'fdinfo over bound'
    ids = [line.split(':', 1)[1].strip() for line in info.splitlines() if line.startswith('mnt_id:')]
    assert len(ids) == 1 and ids[0].isascii() and ids[0].isdecimal(), 'missing/ambiguous fd mount identity'
    mount_id = int(ids[0])
    raw = Path('/proc/self/mountinfo').read_text(encoding='ascii')
    assert len(raw) <= 1024 * 1024, 'mountinfo over bound'
    matches = []
    for line in raw.splitlines():
        parts = line.split(' - ')
        assert len(parts) == 2, 'malformed mountinfo'
        left, right = parts[0].split(), parts[1].split()
        assert len(left) >= 6 and len(right) >= 3 and left[0].isascii() and left[0].isdecimal()
        if int(left[0]) == mount_id:
            matches.append(right[0])
    assert len(matches) == 1 and matches[0] in ('ext4', 'btrfs'), 'unsupported real invocation_audit filesystem'
    return {'filesystem_type': matches[0], 'mount_id': mount_id}


assert sys.platform == 'linux' and sys.implementation.name == 'cpython'
assert sys.version_info[:3] == (3, 12, 14), sys.version
assert hasattr(os, 'pidfd_open'), 'Linux pidfd_open is required'
assert resource.getrlimit(resource.RLIMIT_NOFILE)[1] > 1024, 'High-FD tests must run'
children_path = Path(f'/proc/{os.getpid()}/task/{os.getpid()}/children')
children_path.read_bytes()
child = subprocess.Popen([sys.executable, '-B', '-I', '-S', '-c', 'import time; time.sleep(30)'])
try:
    children = {int(pid) for pid in children_path.read_bytes().split()}
    assert child.pid in children, 'live child absent from task children'
    fd = os.pidfd_open(child.pid)
    try:
        assert not select.select([fd], [], [], 0)[0], 'live-child pidfd unexpectedly ready'
    finally:
        os.close(fd)
finally:
    child.terminate()
    child.wait(timeout=5)
root = Path(os.environ['TMPDIR'])
assert root.is_absolute() and root.resolve(strict=True) == root
with tempfile.TemporaryDirectory(prefix='durability-', dir=root) as temporary:
    directory = Path(temporary)
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        profile = disk_profile(fd)
        before = os.fstat(fd)
        assert stat.S_ISDIR(before.st_mode) and before.st_uid == os.getuid()
        file_fd = os.open('before', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            assert os.write(file_fd, b'forge-ci-durability\n') == 20
            os.fsync(file_fd)
        finally:
            os.close(file_fd)
        os.fsync(fd)
        os.rename('before', 'after', src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
        assert (directory / 'after').read_bytes() == b'forge-ci-durability\n'
        assert disk_profile(fd) == profile and (os.fstat(fd).st_dev, os.fstat(fd).st_ino) == (before.st_dev, before.st_ino)
        os.unlink('after', dir_fd=fd)
        os.fsync(fd)
        print(json.dumps({'disk': profile, 'device': before.st_dev, 'inode': before.st_ino,
                          'mount_namespace': os.readlink('/proc/self/ns/mnt'), 'file_and_directory_fsync': True}))
    finally:
        os.close(fd)
print('PASS: real proc/pidfd and ext4/btrfs durability primitives; full invocation_audit tests remain mandatory')
print(sys.version)
'''

PROFILE_PATH = "/opt/hostedtoolcache/Python/3.12.14/x64/bin:/usr/bin:/bin"
NODE_PATH_SHELL = "/opt/hostedtoolcache/Python/3.12.14/x64/bin:${RUNNER_TEMP}/forge-b-node-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}/expose:/usr/bin:/bin"
PROFILE_NATIVE_KEYS = tuple("""RUNNER_TEMP RUNNER_OS RUNNER_ARCH GITHUB_WORKSPACE GITHUB_EVENT_PATH
GITHUB_EVENT_NAME GITHUB_REF_TYPE GITHUB_REF GITHUB_REPOSITORY GITHUB_REPOSITORY_OWNER
GITHUB_REPOSITORY_ID GITHUB_REPOSITORY_OWNER_ID GITHUB_ACTOR GITHUB_ACTOR_ID GITHUB_TRIGGERING_ACTOR
GITHUB_WORKFLOW_REF GITHUB_RUN_NUMBER GITHUB_SERVER_URL GITHUB_API_URL GITHUB_SHA GITHUB_WORKFLOW_SHA
GITHUB_JOB GITHUB_RUN_ID GITHUB_RUN_ATTEMPT RUNNER_ENVIRONMENT ImageOS""".split())


def clean_profile_shell(script):
    """A fixed explicit allowlist, shared by installs, preflight and launcher."""
    fields = [key + '="${' + key + '}"' for key in PROFILE_NATIVE_KEYS]
    fields += [
        'PATH="' + NODE_PATH_SHELL + '"', 'LANG=C.UTF-8', 'LC_ALL=C.UTF-8', 'CI=true', 'GITHUB_ACTIONS=true',
        'HOME="$RUNNER_TEMP/forge-b-home-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"',
        'XDG_CONFIG_HOME="$RUNNER_TEMP/forge-b-home-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT/.config"',
        'XDG_CACHE_HOME="$RUNNER_TEMP/forge-b-home-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT/.cache"',
        'XDG_DATA_HOME="$RUNNER_TEMP/forge-b-home-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT/.local/share"',
        'TMPDIR="$RUNNER_TEMP/forge-tests"', 'EVIDENCE="$RUNNER_TEMP/forge-evidence"',
        'PYTHONPATH=.github/scripts:src', 'PYTHONDONTWRITEBYTECODE=1', 'NODE_DISABLE_COMPILE_CACHE=1',
        'SEMGREP_SEND_METRICS=off', 'SEMGREP_ENABLE_VERSION_CHECK=0', 'OTEL_SDK_DISABLED=true',
    ]
    return ('umask 077\n/usr/bin/env -i \\\n  ' + ' \\\n  '.join(fields)
            + " \\\n  /bin/bash --noprofile --norc -e -o pipefail -s <<'PYCLEANPROFILE'\numask 077\n"
            + script + '\nPYCLEANPROFILE\n')


PRIVATE_HOME_PYTHON = r'''import os
from pathlib import Path
import stat

assert os.getuid() == os.geteuid() > 0 and os.getgid() == os.getegid() > 0
os.umask(0o077)
runner = Path(os.environ['RUNNER_TEMP'])
assert runner.is_absolute() and runner.resolve(strict=True) == runner
assert ':' not in str(runner) and all(character.isprintable() for character in str(runner))
for path in (runner, *runner.parents):
    info = path.lstat()
    assert stat.S_ISDIR(info.st_mode) and info.st_uid in (0, os.getuid())
    assert not info.st_mode & 0o022 or (info.st_uid == 0 and info.st_mode & stat.S_ISVTX)
run, attempt = os.environ['GITHUB_RUN_ID'], os.environ['GITHUB_RUN_ATTEMPT']
assert run.isascii() and run.isdecimal() and 0 < int(run) < 2**63 and str(int(run)) == run and attempt == '1'
home = runner / ('forge-b-home-' + run + '-' + attempt)
assert os.environ['HOME'] == str(home) and os.environ['TMPDIR'] == str(runner / 'forge-tests')
paths = (home, home / '.config', home / '.cache', home / '.local', home / '.local/share', runner / 'forge-tests')
assert not home.exists() and not home.is_symlink()
assert not paths[-1].exists() and not paths[-1].is_symlink()
for path in paths:
    assert path.resolve(strict=False) == path and not path.exists() and not path.is_symlink()
    path.mkdir(mode=0o700)
    info = path.lstat()
    assert stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and info.st_gid == os.getgid()
    assert stat.S_IMODE(info.st_mode) == 0o700
for key, relative in (('XDG_CONFIG_HOME', '.config'), ('XDG_CACHE_HOME', '.cache'), ('XDG_DATA_HOME', '.local/share')):
    assert os.environ[key] == str(home / relative)
node_root = runner / ('forge-b-node-' + run + '-' + attempt)
assert len(str(node_root / 'expose').encode('utf-8')) <= 4096
for path in (node_root, node_root / 'expose'):
    assert path.resolve(strict=False) == path and not path.exists() and not path.is_symlink()
    path.mkdir(mode=0o700)
    info = path.lstat()
    assert stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and info.st_gid == os.getgid()
    assert stat.S_IMODE(info.st_mode) == 0o700
assert not any((node_root / 'expose').iterdir())
'''

INSTALL = r'''install_deadline_ns="$(/opt/hostedtoolcache/Python/3.12.14/x64/bin/python -B -I -S -c 'import time; print(time.monotonic_ns()+600*1000000000)')"
BASE_HELPER profile-check --repo "$GITHUB_WORKSPACE" --evidence "$EVIDENCE" --deadline-ns "$install_deadline_ns" --helper-map-sha256 HELPER_MAP_SHA256
BASE_HELPER node-install --repo "$GITHUB_WORKSPACE" --evidence "$EVIDENCE" --deadline-ns "$install_deadline_ns" --helper-map-sha256 HELPER_MAP_SHA256
BASE_HELPER python-install --deadline-ns "$install_deadline_ns" --helper-map-sha256 HELPER_MAP_SHA256
BASE_HELPER python-checkpoint --stage pip --deadline-ns "$install_deadline_ns" --helper-map-sha256 HELPER_MAP_SHA256
BASE_HELPER install-observe --stage 0 --deadline-ns "$install_deadline_ns" --shell-umask "$(umask)" --helper-map-sha256 HELPER_MAP_SHA256
private_python="$RUNNER_TEMP/forge-b-python-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT/bin/python"
/usr/bin/env -u PYTHONPATH PIP_CONFIG_FILE=/dev/null "$private_python" -B -I -m pip --isolated --disable-pip-version-check --no-input --no-cache-dir install --index-url https://pypi.org/simple -e '.[dev,mcp,semgrep,vertex]' 'pytest==9.1.1' \
  2>&1 | tee "$EVIDENCE/install.log"
BASE_HELPER python-checkpoint --stage extras --deadline-ns "$install_deadline_ns" --helper-map-sha256 HELPER_MAP_SHA256
BASE_HELPER install-observe --stage 1 --deadline-ns "$install_deadline_ns" --shell-umask "$(umask)" --helper-map-sha256 HELPER_MAP_SHA256
# The passive extras checkpoint validates the exact private target before discovery.
system_site="$(/usr/bin/python3 -m site --user-site)"
/opt/hostedtoolcache/Python/3.12.14/x64/bin/python -B -I -S - "$system_site" <<'PYSYSTEMSITE'
import os
from pathlib import Path
import sys
home, target = Path(os.environ['HOME']), Path(sys.argv[1])
assert home.is_absolute() and home.resolve(strict=True) == home
assert target == home / '.local/lib/python3.12/site-packages'
assert target.resolve(strict=False) == target and target.is_relative_to(home)
PYSYSTEMSITE
BASE_HELPER python-checkpoint --stage extras --deadline-ns "$install_deadline_ns" --helper-map-sha256 HELPER_MAP_SHA256
/usr/bin/env -u PYTHONPATH PIP_CONFIG_FILE=/dev/null "$private_python" -B -I -m pip --isolated --disable-pip-version-check --no-input --no-cache-dir install --index-url https://pypi.org/simple --target "$system_site" 'pytest==9.1.1' \
  2>&1 | tee "$EVIDENCE/system-pytest-install.log"
BASE_HELPER python-checks --deadline-ns "$install_deadline_ns" --helper-map-sha256 HELPER_MAP_SHA256
BASE_HELPER install-observe --stage 2 --deadline-ns "$install_deadline_ns" --shell-umask "$(umask)" --helper-map-sha256 HELPER_MAP_SHA256
BASE_HELPER preflight --repo "$GITHUB_WORKSPACE" --evidence "$EVIDENCE" --deadline-ns "$install_deadline_ns" --helper-map-sha256 HELPER_MAP_SHA256
'''


def native_keys(source):
    """Read literal names without importing candidate code while rendering."""
    import ast
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'NATIVE_KEYS' for t in node.targets):
            value = ast.literal_eval(node.value)
            assert isinstance(value, (tuple, list, set)) and all(isinstance(k, str) and k.isidentifier() for k in value)
            return tuple(k for k in value if k not in {'FORGE_RUNNER_UID', 'FORGE_RUNNER_GID'})
    raise ValueError('setup_policy must expose literal NATIVE_KEYS')



CLEANUP_NAMES = frozenset({
    "BOOTSTRAP_ROOT", "MAX_ID", "MAX_CREDENTIAL", "MAX_FILE", "_SHA256", "_BOOT", "SetupError",
    "need", "keys", "_id", "sha", "read_regular", "trusted_boot_id", "_credential_stage",
    "_credential_parts", "_credential_directory", "_credential_identity", "_credential_stage_fd",
    "_credential_file_info", "_credential_file_fd", "_credential_read_fd", "_credential_inventory",
    "cleanup_metadata_credential",
})


def cleanup_source(source, records):
    """Copy only fixed read/unlink dependencies; never import candidate code."""
    import ast
    selected, found = [], set()
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names = {node.name}
        elif isinstance(node, ast.Assign):
            names = {target.id for target in node.targets if isinstance(target, ast.Name)}
        else:
            continue
        if names & CLEANUP_NAMES:
            assert names <= CLEANUP_NAMES and not found & names
            selected.append(node)
            found.update(names)
    assert found == CLEANUP_NAMES, 'fixed credential cleanup dependencies changed'
    prefix = ("from __future__ import annotations\nimport os, stat, hashlib, re, json\n"
              "from pathlib import Path\nfrom contextlib import contextmanager\nfrom typing import Any\n")
    code = prefix + ast.unparse(ast.Module(body=selected, type_ignores=[])) + "\n"
    code += "layout = " + repr({'parts': records}) + "\n"
    code += r'''try:
    run_id = _id(os.environ.get('GITHUB_RUN_ID'), 'cleanup run id', native=True)
    attempt = _id(os.environ.get('GITHUB_RUN_ATTEMPT'), 'cleanup attempt', native=True)
    result = cleanup_metadata_credential(run_id, attempt, trusted_boot_id(), credential_layout=layout)
except BaseException:
    print(json.dumps({'schema_version': 1, 'status': 'STOP', 'token_absent': False,
                      'error': 'credential cleanup failed'}, sort_keys=True, separators=(',', ':')))
    raise SystemExit(1)
print(json.dumps(result, sort_keys=True, separators=(',', ':')))
'''
    return code


def render(repo):
    repo = Path(repo).resolve(strict=True)
    helpers = {'.github/scripts/forge_ci/' + name: (repo / '.github/scripts/forge_ci' / name).read_bytes() for name in HELPERS}
    expected = {path: hashlib.sha256(raw).hexdigest() for path, raw in helpers.items()}
    helper_map_sha256 = hashlib.sha256((json.dumps(expected, sort_keys=True, separators=(",", ":"),
                                                 ensure_ascii=True, allow_nan=False) + "\n").encode()).hexdigest()
    base_helper = '/opt/hostedtoolcache/Python/3.12.14/x64/bin/python -B -I -S "$GITHUB_WORKSPACE/.github/scripts/forge_ci/user_service.py"'
    install_script = INSTALL.replace('BASE_HELPER', base_helper).replace('HELPER_MAP_SHA256', helper_map_sha256)
    source = helpers['.github/scripts/forge_ci/setup_policy.py'].decode()
    verifier = helpers['.github/scripts/forge_ci/launch.py'].decode()
    assert 0 < len(source.encode()) <= 256 * 1024 and 0 < len(verifier.encode()) <= 256 * 1024
    assert '${{' not in source and '${{' not in verifier
    for marker in ('PYSTAGE', 'PYSETUP', 'PYBOOT', 'PYVERIFYCHUNK', 'PYENTRY', 'PYCREDENTIALCLEANUP'):
        assert '\n' + marker + '\n' not in source + verifier
    chunks = [source[index:index + 8000] for index in range(0, len(source), 8000)]
    records = [{'name': f'part-{i:02d}.txt', 'bytes': len(part.encode()), 'sha256': hashlib.sha256(part.encode()).hexdigest()}
               for i, part in enumerate(chunks)]
    assert 1 <= len(chunks) <= 32, 'fixed root source chunk count exceeded'
    steps = []
    for index, (part, record) in enumerate(zip(chunks, records, strict=True)):
        header = r'''umask 077
export EVIDENCE="$RUNNER_TEMP/forge-evidence"
test ! -e "$EVIDENCE"
mkdir "$EVIDENCE"
printf 'EVIDENCE=%s\n' "$EVIDENCE" >> "$GITHUB_ENV"
''' if index == 0 else 'umask 077\n'
        code = (STAGE_CODE.replace('STAGING_ROOT_LITERAL', repr(STAGING_ROOT)).replace('FIRST_PART', repr(index == 0))
                .replace('SOURCE_PART', literal(part)).replace('PART_RECORD', repr(record)))
        script = header + r'''/usr/bin/sudo -n -- /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C LANG=C HOME=/nonexistent \
  GITHUB_RUN_ID="$GITHUB_RUN_ID" GITHUB_RUN_ATTEMPT="$GITHUB_RUN_ATTEMPT" /usr/bin/python3 -B -I -S - <<'PYSTAGE' 2>&1 | /usr/bin/tee "$EVIDENCE/policy-stage-INDEX.log"
'''.replace('INDEX', f'{index:02d}') + code + '\nPYSTAGE\n'
        steps.append({'name': f'Stage fixed policy source {index + 1}/{len(chunks)}', 'timeout-minutes': 1, 'run': script})
    env_lines = ' \\\n  '.join(key + '="${' + key + '}"' for key in native_keys(source))
    code = (BOOT_CODE.replace('STAGING_ROOT_LITERAL', repr(STAGING_ROOT)).replace('SOURCE_PART_RECORDS', repr(records))
            .replace('SOURCE_BYTE_COUNT', str(len(source.encode())))
            .replace('SOURCE_SHA256', repr(expected['.github/scripts/forge_ci/setup_policy.py'])))
    entry_code = code
    entry_record = {'name': 'bootstrap-entry.py', 'bytes': len(entry_code.encode()),
                    'sha256': hashlib.sha256(entry_code.encode()).hexdigest()}
    stage_prefix = code.split('chunks = []', 1)[0]
    stage_prefix = stage_prefix[:stage_prefix.index("assert {p.name for p in stage.iterdir()}")]
    stage_prefix += "assert {p.name for p in stage.iterdir()} == {p['name'] for p in parts}\n"
    stage_prefix += code[code.index('chunks = []'):code.index("source = raw.decode('utf-8')")]
    entry_stage_code = stage_prefix + ("raw = " + literal(entry_code) + ".encode('utf-8')\n"
        + "expected = " + repr(entry_record) + "\n"
        + "assert len(raw) == expected['bytes'] and hashlib.sha256(raw).hexdigest() == expected['sha256']\n"
        + "fd = os.open(stage / expected['name'], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)\n"
        + "with os.fdopen(fd, 'wb') as stream:\n    stream.write(raw)\n    stream.flush()\n    os.fsync(stream.fileno())\n"
        + "fd = os.open(stage / expected['name'], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)\n"
        + "with os.fdopen(fd, 'rb') as stream:\n    info = os.fstat(stream.fileno())\n"
        + "    assert stat.S_ISREG(info.st_mode) and info.st_uid == info.st_gid == 0\n"
        + "    assert stat.S_IMODE(info.st_mode) == 0o400 and info.st_nlink == 1 and info.st_size == expected['bytes']\n"
        + "    written = stream.read(expected['bytes'] + 1)\n"
        + "assert len(written) == expected['bytes'] and hashlib.sha256(written).hexdigest() == expected['sha256']\n"
        + "assert {p.name for p in stage.iterdir()} == {p['name'] for p in parts} | {'bootstrap-entry.py'}\n")
    entry_stage = r'''/usr/bin/sudo -n -- /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C LANG=C HOME=/nonexistent \
  GITHUB_RUN_ID="$GITHUB_RUN_ID" GITHUB_RUN_ATTEMPT="$GITHUB_RUN_ATTEMPT" /usr/bin/python3 -B -I -S - <<'PYENTRY'
''' + entry_stage_code + '\nPYENTRY\n'
    bootstrap = r'''set +x
export -n FORGE_CI_METADATA_TOKEN
umask 077
trap 'unset FORGE_CI_METADATA_TOKEN' EXIT
runner_uid="$(/usr/bin/id -u)"
runner_gid="$(/usr/bin/id -g)"
builtin printf '%s' "$FORGE_CI_METADATA_TOKEN" | /usr/bin/sudo -n -- /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C LANG=C HOME=/nonexistent \
  FORGE_RUNNER_UID="$runner_uid" FORGE_RUNNER_GID="$runner_gid" \
  NATIVE_ENV \
  /usr/bin/python3 -B -I -S "/var/lib/forge-ci-bootstrap/${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}/bootstrap-entry.py" 2>&1 | /usr/bin/tee "$EVIDENCE/policy-setup.log"
'''.replace('NATIVE_ENV', env_lines)
    verifier_chunks = [verifier[index:index + 8000] for index in range(0, len(verifier), 8000)]
    verifier_records = [{'name': f'part-{i:02d}.txt', 'bytes': len(part.encode()),
                         'sha256': hashlib.sha256(part.encode()).hexdigest()}
                        for i, part in enumerate(verifier_chunks)]
    verifier_steps = []
    for index, (part, record) in enumerate(zip(verifier_chunks, verifier_records, strict=True)):
        code = (VERIFIER_STAGE_CODE.replace('FIRST_PART', repr(index == 0))
                .replace('SOURCE_PART', literal(part)).replace('PART_RECORD', repr(record)))
        script = "umask 077\n/usr/bin/python3 -B -I -S - <<'PYVERIFYCHUNK'\n" + code + '\nPYVERIFYCHUNK\n'
        verifier_steps.append({'name': f'Stage fixed source verifier {index + 1}/{len(verifier_chunks)}',
                               'timeout-minutes': 1, 'run': script})
    code = (VERIFY_CODE.replace('VERIFIER_PART_RECORDS', repr(verifier_records))
            .replace('VERIFIER_BYTE_COUNT', str(len(verifier.encode())))
            .replace('VERIFIER_SHA256', repr(expected['.github/scripts/forge_ci/launch.py']))
            .replace('EXPECTED_HELPERS', repr(expected)))
    verify = "/usr/bin/python3 -B -I -S - <<'PYBOOT' 2>&1 | tee \"$EVIDENCE/launch-bootstrap.log\"\n" + code + '\nPYBOOT\n'
    prepare = clean_profile_shell("/opt/hostedtoolcache/Python/3.12.14/x64/bin/python -B -I -S - <<'PYPRIVATEHOME'\n" + PRIVATE_HOME_PYTHON + "\nPYPRIVATEHOME\n"
                                  + base_helper + ' profile-check --repo "$GITHUB_WORKSPACE" --evidence "$EVIDENCE" --helper-map-sha256 ' + helper_map_sha256 + '\n')
    preflight = clean_profile_shell("/opt/hostedtoolcache/Python/3.12.14/x64/bin/python -B -I -S - <<'PYPREFLIGHT' 2>&1 | tee \"$EVIDENCE/preflight.log\"\n"
                                    + PREFLIGHT_PYTHON + '\nPYPREFLIGHT\ndf -h "$TMPDIR" | tee "$EVIDENCE/disk.log"\n')
    cleanup = r'''/usr/bin/sudo -n -- /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C LANG=C HOME=/nonexistent \
  GITHUB_RUN_ID="$GITHUB_RUN_ID" GITHUB_RUN_ATTEMPT="$GITHUB_RUN_ATTEMPT" /usr/bin/python3 -B -I -S - <<'PYCREDENTIALCLEANUP' | /usr/bin/tee "$RUNNER_TEMP/forge-evidence/credential-cleanup.json"
''' + cleanup_source(source, records) + '\nPYCREDENTIALCLEANUP\n'
    steps.extend([
        {'name': 'Stage fixed bootstrap entrypoint', 'timeout-minutes': 1, 'run': entry_stage},
        {'name': 'Authenticate and establish fixed vendor policy before actions', 'id': 'policy_setup', 'timeout-minutes': 10,
         'env': {'FORGE_CI_METADATA_TOKEN': '${{ github.token }}'}, 'run': bootstrap},
        {'name': 'Check out exact admitted source', 'uses': CHECKOUT,
         'with': {'ref': '${{ github.sha }}', 'fetch-depth': 0, 'persist-credentials': False}},
        *verifier_steps,
        {'name': 'Verify complete source and live identity before imports', 'timeout-minutes': 2, 'run': verify},
        {'name': 'Set up qualified Python', 'uses': SETUP_PYTHON, 'with': {'python-version': '3.12.14'}},
        {'name': 'Establish fresh private diagnostic HOME before all phases', 'timeout-minutes': 2, 'run': prepare},
        {'name': 'Verify real Linux ownership and durable disk prerequisites', 'timeout-minutes': 2, 'run': preflight},
        {'name': 'Install declared extras and qualified pytest', 'timeout-minutes': 10, 'run': clean_profile_shell(install_script)},
        {'name': 'Verify production boundary and run complete test phases', 'timeout-minutes': 120,
         'run': clean_profile_shell(base_helper + r''' launch \
  --receipt "$EVIDENCE/launch-bootstrap.json" --repo "$GITHUB_WORKSPACE" \
  --evidence "$EVIDENCE/qualification" --helper-map-sha256 HELPER_MAP_SHA256 2>&1 | tee "$EVIDENCE/qualification-controller.log"
'''.replace('HELPER_MAP_SHA256', helper_map_sha256))},
        {'name': 'Remove private metadata credential', 'id': 'credential_cleanup', 'if': '${{ always() }}',
         'timeout-minutes': 1, 'run': cleanup},
        {'name': 'Preserve logs and JUnit reports', 'if': '${{ always() }}', 'timeout-minutes': 5, 'uses': UPLOAD,
         'with': {'name': 'linux-test-evidence-${{ github.run_id }}-${{ github.run_attempt }}',
                  'path': '${{ runner.temp }}/forge-evidence/', 'if-no-files-found': 'error', 'retention-days': 14}},
    ])
    workflow = {
        'name': 'Linux tests', 'on': {'push': {'branches': [BRANCH]}}, 'permissions': {'contents': 'read'},
        'concurrency': {'group': 'forge-linux-admitted-${{ github.run_id }}', 'cancel-in-progress': False},
        'jobs': {'linux-tests': {'name': 'linux-tests', 'runs-on': 'ubuntu-24.04', 'timeout-minutes': 150,
                               'defaults': {'run': {'shell': 'bash'}},
                               'env': {'PYTHONDONTWRITEBYTECODE': '1', 'SEMGREP_SEND_METRICS': 'off',
                                       'SEMGREP_ENABLE_VERSION_CHECK': '0', 'OTEL_SDK_DISABLED': 'true'}, 'steps': steps}},
    }
    assert all(len(step.get('run', '')) <= 20000 for step in steps), 'GitHub run step character bound'
    return yaml.dump(workflow, Dumper=WorkflowDumper, sort_keys=False, width=120)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    target = args.repo / WORKFLOW_PATH
    content = render(args.repo)
    if args.check:
        if target.read_text() != content:
            parser.exit(1, 'linux-tests.yml differs from the reviewed source rendering\n')
    else:
        target.write_text(content)


if __name__ == '__main__':
    main()
