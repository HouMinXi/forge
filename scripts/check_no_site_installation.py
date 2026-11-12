"""Read-only real-wheel startup/dependency control, not isolated capture acceptance."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

CHILD = r"""
import hashlib, json, os, pathlib, resource, sys, types
for key, bound in ((resource.RLIMIT_FSIZE, 8*1024*1024), (resource.RLIMIT_CPU, 12), (resource.RLIMIT_AS, 1024**3)):
    _, hard = resource.getrlimit(key)
    limit = bound if hard == resource.RLIM_INFINITY else min(bound, hard)
    resource.setrlimit(key, (limit,limit))
binding = json.loads(sys.argv[1])
helper = pathlib.Path(sys.argv[3])
raw = helper.read_bytes()
assert hashlib.sha256(raw).hexdigest() == binding['capture_hashes']['no_site_entry.py']
entry = types.ModuleType('_read_only_no_site')
entry.__file__ = str(helper)
exec(compile(raw,str(helper),'exec'), entry.__dict__)
case = sys.argv[4]
if case == 'diagnostic':
    binding['mode'] = 'diagnostic'
    fd = os.open('private-events-diagnostic', os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    os.set_inheritable(fd, True)
    os.environ['FORGE_CLI_DIAGNOSTIC_FD'] = str(fd)
if case not in {'positive', 'diagnostic'}:
    if case == 'console_hash':
        binding['installed_provenance']['launcher_sha256'] = '0' * 64
    elif case == 'prefix':
        binding['target']['prefix'] = '/not-the-venv'
    elif case == 'roots':
        binding['startup_admission']['no_site_system_roots'] = []
    elif case == 'probe_hash':
        binding['mode'] = 'diagnostic'
        binding['capture_hashes']['diagnostic_probe.py'] = '0' * 64
        fd = os.open('private-events-negative', os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.environ['FORGE_CLI_DIAGNOSTIC_FD'] = str(fd)
    else:
        raise ValueError('unknown control')
    try:
        entry.prepare(binding, sys.argv[2], entry.FIXED_ARGS)
    except ValueError:
        print('READ_ONLY_NEGATIVE_REJECTED')
        raise SystemExit(0)
    raise RuntimeError('negative control was admitted')
launcher_path, console_bytes = entry.prepare(binding, sys.argv[2], entry.FIXED_ARGS)
if case == 'diagnostic':
    assert not os.get_inheritable(fd) and 'FORGE_CLI_DIAGNOSTIC_FD' not in os.environ
    assert sys.gettrace() is not None
assert hashlib.sha256(console_bytes).hexdigest() == binding['installed_provenance']['launcher_sha256']
import code_forge_launcher as launcher
source = launcher.select_source(pathlib.Path.cwd(), env={'PATH':os.defpath})
site = binding['startup_admission']['site_packages']
assert source.mode == 'installed'
assert source.package_root == pathlib.Path(site) / 'code_forge'
assert pathlib.Path(launcher.__file__) == pathlib.Path(site) / 'code_forge_launcher.py'
launcher.bind_source(source)
import code_forge, yaml, unidiff
for module in (code_forge, yaml, unidiff):
    assert pathlib.Path(module.__file__).resolve().is_relative_to(site)
assert 'sitecustomize' not in sys.modules and 'usercustomize' not in sys.modules
assert str(pathlib.Path.cwd()) not in sys.path and '' not in sys.path
print('READ_ONLY_DEPENDENCY_CONTROL_COMPLETE')
"""


def check_probe_receipt(raw):
    if not raw or len(raw) > 8 * 1024 * 1024 or not raw.endswith(b"\n"):
        raise ValueError("private probe framing invalid")
    lines = raw.splitlines()
    if not 2 <= len(lines) <= 200000 or any(len(line) > 4096 for line in lines):
        raise ValueError("private probe framing invalid")
    rows = [json.loads(line) for line in lines]
    if any(
        type(row) is not dict
        or row.get("schema") != 1
        or type(row.get("seq")) is not int
        or row["seq"] != i
        for i, row in enumerate(rows, 1)
    ):
        raise ValueError("private probe sequence invalid")
    if (
        rows[0].get("event") != "diagnostic_start"
        or rows[-1].get("event") != "diagnostic_footer"
        or type(rows[-1].get("complete")) is not bool
    ):
        raise ValueError("private probe terminal receipt absent")
    if any(row.get("event") == "diagnostic_error" for row in rows):
        raise ValueError("private probe error observed")
    # Footer presence is an installation/framing check only; complete need not be
    # true because this read-only control never exercises a native invocation.


def check(evidence, expected_source_sha):
    if sys.version_info[:2] != (3, 14):
        raise ValueError("required runtime unavailable")
    spec = importlib.util.spec_from_file_location(
        "_capture_verifier", Path(__file__).with_name("capture_installed_cli.py")
    )
    controller = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(controller)
    binding, _, _ = controller.verify_installation(evidence, startup_mode="isolated-no-site")
    if binding["installed_provenance"]["source_commit"] != expected_source_sha:
        raise ValueError("wheel does not match expected source")
    helper = Path(__file__).resolve().parents[1] / "qualification/installed_cli/no_site_entry.py"
    binding.update(backend="stock-bwrap", startup_mode="isolated-no-site", mode="baseline")
    binding["capture_hashes"] = {
        name: controller.sha256(controller.regular_bytes(helper.with_name(name)))
        for name in ("no_site_entry.py", "capture_worker.py", "diagnostic_probe.py")
    }
    with tempfile.TemporaryDirectory(prefix="forge-no-site-control-") as temporary:
        root = Path(temporary)
        marker = root / "HOOK_EXECUTED"
        hook = "from pathlib import Path; Path(" + repr(str(marker)) + ").touch()\n"
        (root / "sitecustomize.py").write_text(hook)
        (root / "usercustomize.py").write_text(hook)
        (root / "code_forge_launcher.py").write_text("raise RuntimeError('CWD_SHADOW')\n")
        cache = root / "cache"
        cache.mkdir(mode=0o700)
        for case in ("positive", "diagnostic", "console_hash", "prefix", "roots", "probe_hash"):
            with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
                result = subprocess.run(
                    [
                        binding["target_python"],
                        "-I",
                        "-S",
                        "-B",
                        "-X",
                        "pycache_prefix=" + str(cache),
                        "-c",
                        CHILD,
                        json.dumps(binding),
                        str(cache),
                        str(helper),
                        case,
                    ],
                    cwd=root,
                    env={"PATH": os.defpath, "LC_ALL": "C.UTF-8", "PYTHONPATH": str(root)},
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    timeout=15,
                    check=False,
                )
                if result.returncode or stdout.tell() > 4096 or stderr.tell() != 0 or marker.exists():
                    raise ValueError("read-only dependency control unavailable")
                stdout.seek(0)
                expected = (
                    b"READ_ONLY_DEPENDENCY_CONTROL_COMPLETE\n"
                    if case in {"positive", "diagnostic"}
                    else b"READ_ONLY_NEGATIVE_REJECTED\n"
                )
                if stdout.read(4097) != expected:
                    raise ValueError("read-only dependency control receipt invalid")
            if case == "diagnostic":
                check_probe_receipt(
                    controller.regular_bytes(root / "private-events-diagnostic", 8 * 1024 * 1024)
                )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel-evidence", required=True, type=Path)
    parser.add_argument("--expected-source-sha", required=True)
    args = parser.parse_args()
    try:
        check(args.wheel_evidence, args.expected_source_sha)
    except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired):
        return 73
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
