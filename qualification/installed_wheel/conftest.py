# SPDX-License-Identifier: Apache-2.0
"""Standalone wheel controls; never load the source suite's conftest."""

import base64
import hashlib
from importlib import metadata
import json
import os
import shutil
from pathlib import Path
import sys
import zipfile

import pytest

CONSOLE_BOOTSTRAP = """import runpy,sys
ns=runpy.run_path(sys.argv.pop(1))
sys.argv.pop(0)
ns['install_guard'](console=False)
try:
    runpy.run_path(sys.argv[0],run_name='__main__')
finally:
    assert not ns['VIOLATIONS'], ns['VIOLATIONS']
    import json,os,pathlib
    proof={'modules':ns['verify_install'](),'allowed_processes':ns['CALLS'],'violations':ns['VIOLATIONS']}
    pathlib.Path(os.environ['WHEEL_CONSOLE_PROOF']).write_text(json.dumps(proof))
"""
GIT_ARGV = {
    ("ls-files", "-s", "--", ".code-forge", ".code-forge/gate.yaml"),
    ("rev-parse", "--path-format=absolute", "--git-common-dir"),
    ("rev-parse", "--show-toplevel"),
    ("rev-parse", "--git-dir"),
    ("ls-tree", os.environ["WHEEL_FIXTURE_REV"], "--", ".code-forge", ".code-forge/gate.yaml"),
    ("show", os.environ["WHEEL_FIXTURE_REV"] + ":./.code-forge/gate.yaml"),
}
CALLS = []
CONSOLE_PROOFS = []
VIOLATIONS = []


def audit_event(event, args, *, console):
    if event.startswith("socket."):
        raise AssertionError(f"network forbidden: {event}")
    if event in {"os.system", "os.posix_spawn", "os.fork", "os.forkpty", "os.exec"}:
        raise AssertionError(f"process primitive forbidden: {event}")
    if event != "subprocess.Popen":
        return
    executable, argv, cwd, env = args
    assert isinstance(argv, (list, tuple)) and argv
    root = Path(os.environ["WHEEL_SANDBOX"]).resolve()
    actual_cwd = Path(cwd or os.getcwd()).resolve()
    assert actual_cwd.is_relative_to(root) and (actual_cwd / ".git").is_dir(), (argv, cwd)
    child_env = os.environ if env is None else env
    for name in child_env:
        assert not name.startswith(("FORGE_", "CODE_FORGE_", "LD_", "DYLD_")), name
        if name.startswith("GIT_"):
            assert name in {
                "GIT_CONFIG_NOSYSTEM",
                "GIT_CONFIG_GLOBAL",
                "GIT_CONFIG_SYSTEM",
                "GIT_TERMINAL_PROMPT",
                "GIT_CEILING_DIRECTORIES",
            }, name
    assert child_env.get("PATH") == os.environ["WHEEL_SAFE_PATH"]
    for name in ("PYTHONPATH", "PYTHONHOME"):
        assert name not in child_env, name
    for name, expected in {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
    }.items():
        assert child_env.get(name) == expected, name
    git = Path(os.environ["WHEEL_GIT"]).resolve()
    if argv[0] == "git" or Path(argv[0]).resolve() == git:
        assert tuple(argv[1:]) in GIT_ARGV, argv
        assert (
            hashlib.sha256((actual_cwd / ".git/config").read_bytes()).hexdigest()
            == os.environ["WHEEL_GIT_CONFIG_SHA256"]
        )
        assert not (actual_cwd / ".git/hooks").exists()
        assert executable == argv[0]
        assert Path(shutil.which(argv[0], path=child_env["PATH"])).resolve() == git
    elif list(argv) == ["python3", "--version"]:
        assert executable == "python3"
        assert Path(shutil.which("python3", path=child_env["PATH"])) == Path(sys.prefix) / "bin/python3"
    else:
        assert console, argv
        assert Path(executable).resolve() == Path(sys.executable).resolve()
        prefix = [
            sys.executable,
            "-I",
            "-c",
            CONSOLE_BOOTSTRAP,
            str(Path(__file__).resolve()),
            str(Path(sys.prefix) / "bin/code-forge"),
        ]
        assert list(argv[: len(prefix)]) == prefix, argv
        assert list(argv[len(prefix) :]) in [
            ["--help"],
            ["ledger", "list", "--json"],
        ], argv
    CALLS.append({"argv": list(argv), "cwd": str(cwd)})


def install_guard(*, console=True):
    def guard(event, args):
        try:
            audit_event(event, args, console=console)
        except AssertionError as exc:
            VIOLATIONS.append(str(exc))
            raise

    sys.addaudithook(guard)


def verify_install():
    import code_forge
    import code_forge_launcher

    distribution = metadata.distribution("code-review-forge")
    prefix = Path(sys.prefix).resolve()
    package = Path(code_forge.__file__).resolve().parent
    assert package.is_relative_to(prefix), package
    assert package == Path(distribution.locate_file("code_forge")).resolve(), package
    direct = json.loads(distribution.read_text("direct_url.json"))
    assert not direct.get("dir_info", {}).get("editable", False), direct
    expected_wheel = Path(os.environ["WHEEL_ARTIFACT"])
    digest = hashlib.sha256(expected_wheel.read_bytes()).hexdigest()
    assert direct["archive_info"]["hashes"]["sha256"] == digest
    records = {str(item): item for item in distribution.files}
    modules = {}
    for name, module in tuple(sys.modules.items()):
        if name != "code_forge_launcher" and name != "code_forge" and not name.startswith("code_forge."):
            continue
        origin = Path(module.__file__).resolve()
        assert origin.is_relative_to(prefix), (name, origin)
        relative = str(origin.relative_to(package.parent))
        entry = records[relative]
        assert Path(distribution.locate_file(entry)).resolve() == origin, (name, origin)
        assert entry.hash.mode == "sha256"
        actual = (
            base64.urlsafe_b64encode(hashlib.sha256(origin.read_bytes()).digest()).rstrip(b"=").decode()
        )
        assert entry.hash.value == actual, (name, origin)
        with zipfile.ZipFile(expected_wheel) as artifact:
            assert (
                hashlib.sha256(artifact.read(relative)).digest()
                == hashlib.sha256(origin.read_bytes()).digest()
            )
        modules[name] = {
            "path": str(origin),
            "sha256": hashlib.sha256(origin.read_bytes()).hexdigest(),
            "record_sha256_urlsafe": entry.hash.value,
        }
    for entry in sys.path:
        if entry:
            for forbidden in json.loads(os.environ["WHEEL_FORBIDDEN_PATHS"]):
                assert not Path(entry).resolve().is_relative_to(Path(forbidden).resolve()), entry
    assert Path(code_forge_launcher.__file__).resolve().is_relative_to(prefix)
    return modules


@pytest.fixture(scope="session", autouse=True)
def wheel_session():
    assert sys.prefix != sys.base_prefix
    assert "include-system-site-packages = false" in (Path(sys.prefix) / "pyvenv.cfg").read_text()
    verify_install()
    yield
    assert not VIOLATIONS, VIOLATIONS
    output = {
        "modules": verify_install(),
        "allowed_processes": CALLS,
        "violations": VIOLATIONS,
        "console": CONSOLE_PROOFS,
    }
    Path(os.environ["WHEEL_RUNTIME_PROOF"]).write_text(json.dumps(output, indent=2) + "\n")


@pytest.fixture
def project(tmp_path):
    import shutil

    root = tmp_path / "project"
    shutil.copytree(Path(os.environ["WHEEL_SANDBOX"]) / "seed", root)
    (root / ".code-forge").mkdir()
    return root


# Install before collection imports any business-package module. The console
# bootstrap explicitly installs its stricter child guard after run_path.
if __name__ != "<run_path>":
    install_guard()
