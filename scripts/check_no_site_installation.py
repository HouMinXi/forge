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
import json, sys
stage = 'CHILD_SETUP'
def control():
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
    global stage
    stage = {'positive':'BASELINE_PREPARE','diagnostic':'DIAGNOSTIC_PREPARE','console_hash':'NEGATIVE_CONSOLE','prefix':'NEGATIVE_PREFIX','roots':'NEGATIVE_ROOTS','probe_hash':'NEGATIVE_PROBE'}[case]
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
            return
        raise RuntimeError('negative control was admitted')
    launcher_path, console_bytes = entry.prepare(binding, sys.argv[2], entry.FIXED_ARGS)
    if case == 'diagnostic':
        assert not os.get_inheritable(fd) and 'FORGE_CLI_DIAGNOSTIC_FD' not in os.environ
        assert sys.gettrace() is not None
    assert hashlib.sha256(console_bytes).hexdigest() == binding['installed_provenance']['launcher_sha256']
    stage = 'DIAGNOSTIC_IMPORT' if case == 'diagnostic' else 'BASELINE_IMPORT'
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
    return
try:
    control()
except Exception as error:  # noqa: BLE001 - closed classification; no exception text is published
    category = 'INVALID_DATA' if isinstance(error, (ValueError, TypeError, KeyError, AssertionError)) else 'OS_ERROR' if isinstance(error, OSError) else 'INTERNAL_ERROR'
    print(json.dumps({'schema':1,'stage':stage,'category':category}))
    raise SystemExit(1)
print(json.dumps({'schema':1,'stage':stage,'category':'NONE'}))
"""


CASE_STAGES = {
    "positive": {"BASELINE_PREPARE", "BASELINE_IMPORT"},
    "diagnostic": {"DIAGNOSTIC_PREPARE", "DIAGNOSTIC_IMPORT"},
    "console_hash": {"NEGATIVE_CONSOLE"},
    "prefix": {"NEGATIVE_PREFIX"},
    "roots": {"NEGATIVE_ROOTS"},
    "probe_hash": {"NEGATIVE_PROBE"},
}
CATEGORIES = {"NONE", "INVALID_DATA", "OS_ERROR", "INTERNAL_ERROR"}


def error_category(error, stage=None):
    # Exact source-defined literals only; unknown messages remain coarse.
    known = {
        "target interpreter metadata unavailable": "VERIFY_TARGET_METADATA",
        "target interpreter identity mismatch": "VERIFY_TARGET_METADATA",
        "installed launcher shebang mismatch": "VERIFY_CONSOLE_BINDING",
        "installed launcher entrypoint mismatch": "VERIFY_CONSOLE_BINDING",
        "installed RECORD digest mismatch": "VERIFY_SOURCE_BINDING",
        "source archive/wheel mismatch": "VERIFY_SOURCE_BINDING",
        "installed product source mismatch": "VERIFY_SOURCE_BINDING",
        "source archive digest mismatch": "VERIFY_SOURCE_BINDING",
        "wheel digest mismatch": "VERIFY_SOURCE_BINDING",
        "unrecognized site path hook": "VERIFY_SITE_PATH_HOOK",
        "competing launcher or startup customization": "VERIFY_STARTUP_ADMISSION",
        "startup customization cache rejected": "VERIFY_STARTUP_ADMISSION",
        "startup candidate in standard-library ZIP": "VERIFY_STARTUP_ADMISSION",
        "symlink path rejected": "VERIFY_STARTUP_ADMISSION",
        "no-site target prefix/runtime": "VERIFY_RUNTIME_PREFIX",
    }
    if (
        stage == "VERIFY_INSTALLATION"
        and type(error) is ValueError
        and type(error.args) is tuple
        and len(error.args) == 1
        and type(error.args[0]) is str
    ):
        category = known.get(error.args[0])
        if category is not None:
            return category
    if isinstance(error, subprocess.TimeoutExpired):
        return "TIMEOUT"
    if isinstance(error, (ValueError, TypeError, KeyError, AssertionError)):
        return "INVALID_DATA"
    if isinstance(error, OSError):
        return "OS_ERROR"
    return "INTERNAL_ERROR"


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate receipt field")
        result[key] = value
    return result


def child_observation(raw, case, code):
    if type(code) is not int or not -255 <= code <= 255:
        raise ValueError("child status outside public domain")
    if not raw or len(raw) > 1024 or not raw.endswith(b"\n"):
        raise ValueError("child receipt unavailable")
    row = json.loads(raw, object_pairs_hook=unique)
    if (
        type(row) is not dict
        or set(row) != {"schema", "stage", "category"}
        or type(row["schema"]) is not int
        or row["schema"] != 1
        or type(row["stage"]) is not str
        or row["stage"] not in CASE_STAGES[case] | {"CHILD_SETUP"}
        or type(row["category"]) is not str
        or row["category"] not in CATEGORIES
        or (code == 0) != (row["category"] == "NONE")
        or (
            code == 0
            and row["stage"]
            != {"positive": "BASELINE_IMPORT", "diagnostic": "DIAGNOSTIC_IMPORT"}.get(
                case, next(iter(CASE_STAGES[case]))
            )
        )
    ):
        raise ValueError("invalid child receipt")
    return row


def initial_observation():
    return {
        "schema": "forge-no-site-control-v1",
        "stage": "INITIALIZE",
        "category": "NO_DELIVERY",
        "child_exit": None,
    }


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


def check(evidence, expected_source_sha, observation=None):
    observation = initial_observation() if observation is None else observation
    observation.update(stage="RUNTIME", category="NONE")
    if sys.version_info[:2] != (3, 14):
        raise ValueError("required runtime unavailable")
    spec = importlib.util.spec_from_file_location(
        "_capture_verifier", Path(__file__).with_name("capture_installed_cli.py")
    )
    controller = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(controller)
    observation["stage"] = "VERIFY_INSTALLATION"
    binding, _, _ = controller.verify_installation(evidence, startup_mode="isolated-no-site")
    observation["stage"] = "SOURCE_IDENTITY"
    if binding["installed_provenance"]["source_commit"] != expected_source_sha:
        raise ValueError("wheel does not match expected source")
    observation["stage"] = "CONTROL_SETUP"
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
            observation.update(stage="CHILD_SETUP", child_exit=None)
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
                observation["child_exit"] = (
                    result.returncode if -255 <= result.returncode <= 255 else None
                )
                observation["category"] = "INVALID_RECEIPT"
                stdout.seek(0)
                row = child_observation(stdout.read(1025), case, result.returncode)
                observation.update(stage=row["stage"], category=row["category"])
                if result.returncode:
                    raise ValueError("read-only child rejected")
                if stdout.read(1) or stderr.tell() != 0 or marker.exists():
                    observation["category"] = "INVALID_RECEIPT"
                    raise ValueError("read-only dependency control unavailable")

            if case == "diagnostic":
                observation["stage"] = "DIAGNOSTIC_PROBE_RECEIPT"
                check_probe_receipt(
                    controller.regular_bytes(root / "private-events-diagnostic", 8 * 1024 * 1024)
                )

    observation.update(stage="COMPLETE", category="NONE", child_exit=0)


def write_status(path, observation):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        raw = (json.dumps(observation, sort_keys=True) + "\n").encode()
        while raw:
            count = os.write(fd, raw)
            if count <= 0:
                raise OSError("status delivery incomplete")
            raw = raw[count:]
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel-evidence", required=True, type=Path)
    parser.add_argument("--expected-source-sha", required=True)
    parser.add_argument("--status-output", type=Path)
    args = parser.parse_args()
    observation = initial_observation()
    code = 0
    try:
        check(args.wheel_evidence, args.expected_source_sha, observation)
    except Exception as error:  # noqa: BLE001 - closed classification; no exception text is published
        if observation["category"] in {"NONE", "NO_DELIVERY"}:
            observation["category"] = error_category(error, observation["stage"])
        code = 73
    if args.status_output is not None:
        try:
            write_status(args.status_output, observation)
        except Exception:  # noqa: BLE001, S110 - publication cannot replace the original control exit
            pass
    return code


if __name__ == "__main__":
    raise SystemExit(main())
