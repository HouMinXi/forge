# SPDX-License-Identifier: Apache-2.0
"""Private, preenabled capture of one synthetic installed CLI invocation.

This is neither an installed qualification runner nor a CI verdict. It reads an
existing build evidence tree; it never installs, rebuilds, or imports the target
package into the controller. The existing installed process owner is unchanged.
"""

from __future__ import annotations

import argparse
import ast
import base64
import configparser
import csv
import email
import errno
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import resource
import select
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import types
from urllib.parse import unquote, urlsplit
import zipfile

FILE_LIMIT = 8 * 1024 * 1024
INPUT_LIMIT = 64 * 1024 * 1024
MEMORY_LIMIT = 1024**3
OUTER_SECONDS = 35
HARNESS_FAILURE = 73
CAPTURE_BASE = "209d8ae47ca51a8fd93673fdedcbb394151da638"
OWNER_PACKAGE = "_forge_capture_bound_owner"
PROOF_KEYS = {
    "source_commit",
    "source_archive_sha256",
    "wheel",
    "wheel_sha256",
    "harness_sha256",
    "controls",
    "dependency_wheels",
    "scope",
    "expected_roster",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def regular_bytes(path, limit=INPUT_LIMIT):
    """Bounded no-follow stable regular-file read, including the named identity."""
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_size <= limit, "unbounded input")
        raw = bytearray()
        while len(raw) <= limit:
            block = os.read(fd, min(65536, limit + 1 - len(raw)))
            if not block:
                break
            raw.extend(block)
        after, named = os.fstat(fd), path.lstat()

        def identity(row):
            return row.st_dev, row.st_ino, row.st_mode, row.st_size, row.st_mtime_ns, row.st_ctime_ns

        require(
            identity(before) == identity(after) == identity(named)
            and len(raw) == before.st_size <= limit,
            "input changed while reading",
        )
        return bytes(raw)
    finally:
        os.close(fd)


def no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON field")
        result[key] = value
    return result


def read_json(path, limit=FILE_LIMIT):
    raw = regular_bytes(path, limit)
    row = Path(path).lstat()
    require(row.st_uid == os.getuid() and stat.S_IMODE(row.st_mode) == 0o600, "non-private receipt")
    require(raw.endswith(b"\n"), "truncated JSON receipt")
    return json.loads(raw, object_pairs_hook=no_duplicate_keys)


def checked_directory(path, *, private=False):
    path = Path(path)
    require(
        path.is_absolute() and str(path) == os.path.normpath(path), "absolute normalized path required"
    )
    for part in [*reversed(path.parents), path]:
        row = part.lstat()
        require(stat.S_ISDIR(row.st_mode), "directory symlink or non-directory rejected")
    if private:
        row = path.stat()
        require(
            row.st_uid == os.getuid() and stat.S_IMODE(row.st_mode) == 0o700,
            "output parent must be owned and private mode0700",
        )
    return path


def new_private_directory(path):
    path = Path(path)
    checked_directory(path.parent, private=True)
    require(path.is_absolute() and path.name not in {"", ".", ".."}, "invalid output")
    path.mkdir(mode=0o700, exist_ok=False)
    return path


def private_bytes(path, raw):
    require(len(raw) <= FILE_LIMIT, "private record exceeds per-file bound")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        view = memoryview(raw)
        while view:
            count = os.write(fd, view)
            require(count > 0, "short private write")
            view = view[count:]
    finally:
        os.close(fd)


def save_json(path, value):
    pending = path.with_name("." + path.name + ".pending")
    private_bytes(pending, (json.dumps(value, sort_keys=True, indent=2) + "\n").encode())
    os.link(pending, path, follow_symlinks=False)
    pending.unlink()


def digest_text(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def product_member(name):
    return name.startswith("code_forge/") or name == "code_forge_launcher.py"


def safe_member(name):
    path = PurePosixPath(name)
    require(
        name
        and not path.is_absolute()
        and ".." not in path.parts
        and "\\" not in name
        and str(path) == name,
        "unsafe archive member",
    )


def source_members(raw, commit):
    result = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        require(archive.pax_headers.get("comment") == commit, "archive recorded commit mismatch")
        seen = set()
        for member in archive:
            name = member.name.rstrip("/")
            safe_member(name)
            require(name not in seen, "duplicate source member")
            seen.add(name)
            if name.startswith("src/") and product_member(name[4:]) or name == "pyproject.toml":
                if member.isdir():
                    continue
                require(member.isfile() and member.size <= FILE_LIMIT, "unsafe product source member")
                stream = archive.extractfile(member)
                require(stream is not None, "source member unavailable")
                with stream:
                    value = stream.read(FILE_LIMIT + 1)
                require(len(value) == member.size, "source member truncated")
                result[name] = value
    require("pyproject.toml" in result, "missing project source")
    return result


def wheel_members(raw):
    result = {}
    expanded = 0
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        require(len(archive.infolist()) <= 4096, "wheel roster exceeds bound")
        for member in archive.infolist():
            name = member.filename.rstrip("/")
            safe_member(name)
            require(name not in result, "duplicate wheel member")
            require(not stat.S_ISLNK(member.external_attr >> 16), "wheel symlink rejected")
            if member.is_dir():
                continue
            require(member.file_size <= FILE_LIMIT, "wheel member exceeds bound")
            expanded += member.file_size
            require(expanded <= INPUT_LIMIT, "wheel expansion exceeds bound")
            result[name] = archive.read(member)
    return result


def parse_record(raw):
    rows = {}
    for row in csv.reader(io.StringIO(raw.decode("utf-8"))):
        require(len(row) == 3 and row[0] not in rows, "malformed or duplicate RECORD")
        rows[row[0]] = row[1:]
    return rows


def check_record(row, raw):
    expected = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode()
    require(row == [expected, str(len(raw))], "installed RECORD digest mismatch")


def launcher_template(target, *, modern=False):
    body = "import sys\nfrom code_forge_launcher import cli_main\nif __name__ == '__main__':\n"
    if modern:
        body += (
            "    if sys.argv[0].endswith('-script.pyw'):\n"
            "        sys.argv[0] = sys.argv[0][:-11]\n"
            "    elif sys.argv[0].endswith('.exe'):\n"
            "        sys.argv[0] = sys.argv[0][:-4]\n"
        )
    else:
        body = "import re\n" + body
        body += "    sys.argv[0] = re.sub(r'(-script\\.pyw|\\.exe)?$', '', sys.argv[0])\n"
    return f"#!{target}\n" + body + "    sys.exit(cli_main())\n"


def verify_launcher(raw, target):
    require(raw.splitlines()[0] == f"#!{target}".encode(), "installed launcher shebang mismatch")
    tree = ast.dump(ast.parse(raw), include_attributes=False)
    require(
        any(
            tree
            == ast.dump(ast.parse(launcher_template(target, modern=modern)), include_attributes=False)
            for modern in (False, True)
        ),
        "installed launcher entrypoint mismatch",
    )


def interpreter_identity(executable, *, target=False):
    physical = Path(executable).resolve(strict=True)
    require(os.access(executable, os.X_OK), "interpreter is not executable")
    identity = {
        "executable": str(executable),
        "physical": str(physical),
        "sha256": sha256(regular_bytes(physical)),
    }
    if target:
        # -I -S suppresses target site initialization; this imports only sys/json.
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            completed = subprocess.run(
                [
                    str(executable),
                    "-I",
                    "-S",
                    "-B",
                    "-c",
                    "import _imp,json,sys;print(json.dumps({'version':sys.version,'executable':sys.executable,"
                    "'implementation':sys.implementation.name,'version_info':list(sys.version_info[:2]),"
                    "'runtime_version':list(sys.version_info[:3]),'base_prefix':sys.base_prefix,'base_exec_prefix':sys.base_exec_prefix,"
                    "'platlibdir':sys.platlibdir,'stdlib_paths':sys.path,"
                    "'extension_suffixes':_imp.extension_suffixes(),'cache_prefix_supported':hasattr(sys,'pycache_prefix')}))",
                ],
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                env={"PATH": os.defpath, "LC_ALL": "C.UTF-8"},
                timeout=5,
                check=False,
            )
            require(
                completed.returncode == 0 and stdout.tell() < 16384 and stderr.tell() == 0,
                "target interpreter metadata unavailable",
            )
            stdout.seek(0)
            metadata_raw = stdout.read(16384)
        value = json.loads(metadata_raw, object_pairs_hook=no_duplicate_keys)
        require(
            value.get("executable") == str(executable) and type(value.get("version")) is str,
            "target interpreter identity mismatch",
        )
        require(
            value.get("implementation") == "cpython" and value.get("cache_prefix_supported") is True,
            "unsupported target cache-prefix implementation",
        )
        require(
            type(value.get("version_info")) is list
            and len(value["version_info"]) == 2
            and all(type(part) is int for part in value["version_info"])
            and value["version_info"] >= [3, 12],
            "unsupported target interpreter version",
        )
        require(
            value.get("platlibdir") in ("lib", "lib64")
            and type(value.get("stdlib_paths")) is list
            and value["stdlib_paths"]
            and all(type(path) is str and Path(path).is_absolute() for path in value["stdlib_paths"]),
            "unsupported target startup path metadata",
        )
        require(
            type(value.get("runtime_version")) is list
            and len(value["runtime_version"]) == 3
            and all(type(part) is int and part >= 0 for part in value["runtime_version"])
            and value["runtime_version"][:2] == value["version_info"]
            and all(
                type(value.get(key)) is str and Path(value[key]).is_absolute()
                for key in ("base_prefix", "base_exec_prefix")
            ),
            "unsupported target base-prefix metadata",
        )
        identity.update(value)
    else:
        identity["version"] = sys.version
    return identity


def capture_worker_module():
    path = Path(__file__).resolve().parents[1] / "qualification/installed_cli/capture_worker.py"
    raw = regular_bytes(path, 1024 * 1024)
    module = types.ModuleType("_forge_capture_startup_admission")
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec", dont_inherit=True), module.__dict__)  # noqa: S102 - capture helper source, never installed product imports
    return module, sha256(raw)


def parse_pyvenv(raw):
    """Match CPython's single-key normalization; reject ambiguous duplicates."""
    config = {}
    for line in io.StringIO(raw.decode("utf-8"), newline=None):
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().lower()
        require(key not in config, "duplicate normalized pyvenv key")
        config[key] = value.strip()
    require(
        config.get("include-system-site-packages", "").lower() == "false",
        "target interpreter permits system site packages",
    )
    return config


def read_pyvenv_config(prefix):
    """The one supported config is prefix/pyvenv.cfg, never a bin override."""
    checked_directory(prefix / "bin")
    require(not os.path.lexists(prefix / "bin/pyvenv.cfg"), "higher-priority bin/pyvenv.cfg rejected")
    raw = regular_bytes(prefix / "pyvenv.cfg", 65536)
    return raw, parse_pyvenv(raw)


def startup_policy(prefix, site, target, launcher_source_sha256):
    """Derive normal console roots from isolated target metadata, not controller paths."""
    version = "python" + ".".join(map(str, target["version_info"]))
    sites = list(
        dict.fromkeys(
            prefix / folder / version / "site-packages" for folder in (target["platlibdir"], "lib")
        )
    )
    require(
        site in sites and all(candidate == site or not candidate.exists() for candidate in sites),
        "unsupported target site-packages layout",
    )
    roots = list(dict.fromkeys([str(prefix / "bin"), *target["stdlib_paths"], str(site)]))
    policy = {
        "policy": "private_empty_bytecode_cache",
        "distutils_mode": "stdlib",
        "roots": roots,
        "extension_suffixes": target["extension_suffixes"],
        "site_packages": str(site),
        "launcher_source_sha256": launcher_source_sha256,
    }
    module, helper_hash = capture_worker_module()
    config_path = prefix / "pyvenv.cfg"
    config_raw, config = read_pyvenv_config(prefix)
    policy.update(venv_config_path=str(config_path), venv_config_sha256=sha256(config_raw))
    alias = module.observe_stdlib_alias(target, config.get("home"))
    if alias is not None:
        policy.update(
            stdlib_alias=alias,
            stdlib_alias_controller_ids={"uid": os.geteuid(), "gid": os.getegid()},
            stdlib_roots=target["stdlib_paths"],
        )
    policy["observed"] = module.admit_startup(policy)
    return policy, helper_hash


def verify_installation(evidence):
    """Read physical archive/wheel/installed bytes; no target sys.path mutation."""
    root = checked_directory(evidence)
    proof_raw = regular_bytes(root / "build-proof.json", FILE_LIMIT)
    proof = json.loads(proof_raw, object_pairs_hook=no_duplicate_keys)
    require(type(proof) is dict and set(proof) == PROOF_KEYS, "unsupported build proof fields")
    require(
        type(proof["source_commit"]) is str and re.fullmatch(r"[0-9a-f]{40}", proof["source_commit"]),
        "invalid recorded source commit",
    )
    for key in ("source_archive_sha256", "wheel_sha256", "harness_sha256"):
        require(digest_text(proof[key]), "invalid build digest")
    require(
        type(proof["wheel"]) is str
        and Path(proof["wheel"]).name == proof["wheel"]
        and proof["wheel"].endswith(".whl"),
        "invalid wheel name",
    )
    for key in ("controls", "dependency_wheels"):
        require(
            type(proof[key]) is dict
            and all(type(k) is str and digest_text(v) for k, v in proof[key].items()),
            "invalid recorded roster",
        )
    require(
        type(proof["scope"]) is str
        and type(proof["expected_roster"]) is list
        and all(type(v) is str for v in proof["expected_roster"]),
        "invalid build scope",
    )
    archive_raw = regular_bytes(root / "source.tar")
    wheel_path = root / "dist" / proof["wheel"]
    checked_directory(wheel_path.parent)
    wheel_raw = regular_bytes(wheel_path)
    require(sha256(archive_raw) == proof["source_archive_sha256"], "source archive digest mismatch")
    require(sha256(wheel_raw) == proof["wheel_sha256"], "wheel digest mismatch")
    source = source_members(archive_raw, proof["source_commit"])
    wheel = wheel_members(wheel_raw)
    dist_names = {name.split("/")[0] for name in wheel if name.endswith(".dist-info/METADATA")}
    require(len(dist_names) == 1, "ambiguous wheel metadata")
    dist_name = dist_names.pop()
    metadata_raw = wheel[dist_name + "/METADATA"]
    metadata = email.message_from_bytes(metadata_raw)
    require(
        re.sub(r"[-_.]+", "-", metadata.get("Name", "")).lower() == "code-review-forge"
        and metadata.get("Version"),
        "wrong wheel distribution",
    )
    project = tomllib.loads(source["pyproject.toml"].decode())["project"]
    require(
        project.get("name") == "code-review-forge"
        and project.get("version") == metadata["Version"]
        and project.get("scripts", {}).get("code-forge") == "code_forge_launcher:cli_main",
        "source project metadata mismatch",
    )
    entry_raw = wheel.get(dist_name + "/entry_points.txt", b"")
    entries = configparser.ConfigParser(interpolation=None)
    entries.read_string(entry_raw.decode())
    require(
        entries.get("console_scripts", "code-forge", fallback=None) == "code_forge_launcher:cli_main",
        "wrong wheel entrypoint",
    )
    wheel_record = parse_record(wheel[dist_name + "/RECORD"])
    prefix = checked_directory(root / "venv")
    read_pyvenv_config(prefix)
    sites = list((prefix / "lib").glob("python*/site-packages"))
    require(len(sites) == 1, "ambiguous installed site-packages")
    site = checked_directory(sites[0])
    require(
        not any(site.glob("*.egg-link")) and not any(site.glob("__editable__*")),
        "editable/source-shadowing installation rejected",
    )
    # Python .pth executable/path entries can redirect ordinary installed imports.
    # The standard setuptools distutils helper is the sole known non-product entry.
    for pth in site.glob("*.pth"):
        raw = regular_bytes(pth, 65536)
        allowed = (
            b"import os; var = 'SETUPTOOLS_USE_DISTUTILS'; enabled = os.environ.get(var, 'local') == 'local'; "
            b"enabled and __import__('_distutils_hack').add_shim(); \n"
        )
        require(pth.name == "distutils-precedence.pth" and raw == allowed, "unrecognized site path hook")
    installed_dist = checked_directory(site / dist_name)
    require(
        regular_bytes(installed_dist / "METADATA") == metadata_raw
        and regular_bytes(installed_dist / "entry_points.txt") == entry_raw,
        "installed metadata mismatch",
    )
    direct = json.loads(
        regular_bytes(installed_dist / "direct_url.json"), object_pairs_hook=no_duplicate_keys
    )
    require(
        type(direct) is dict and "dir_info" not in direct and "vcs_info" not in direct,
        "editable or non-wheel provenance rejected",
    )
    require(
        direct.get("archive_info", {}).get("hashes", {}).get("sha256") == proof["wheel_sha256"],
        "installed direct URL hash mismatch",
    )
    url = urlsplit(direct.get("url", ""))
    require(
        url.scheme == "file"
        and not url.netloc
        and not url.query
        and not url.fragment
        and unquote(url.path) == str(wheel_path),
        "installed direct URL destination mismatch",
    )
    installed_record = parse_record(regular_bytes(installed_dist / "RECORD"))
    product = {name: raw for name, raw in wheel.items() if product_member(name)}
    require(
        "code_forge/_mutation_process.py" in product and "code_forge/_mutation_imports.py" in product,
        "installed owner unavailable",
    )
    for name, raw in product.items():
        require(source.get("src/" + name) == raw, "source archive/wheel mismatch")
        require(name in wheel_record and name in installed_record, "unrecorded product member")
        check_record(wheel_record[name], raw)
        checked_directory((site / name).parent)
        require(regular_bytes(site / name) == raw, "installed product source mismatch")
        check_record(installed_record[name], raw)
    physical_product = {
        str(p.relative_to(site))
        for p in (site / "code_forge").rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }
    require(
        physical_product == {name for name in product if name.startswith("code_forge/")},
        "unrecorded installed product/source shadow",
    )
    target = prefix / "bin/python"
    launcher = prefix / "bin/code-forge"
    checked_directory(launcher.parent)
    raw = regular_bytes(launcher, 65536)
    verify_launcher(raw, target)
    require(os.access(launcher, os.X_OK), "installed launcher not executable")
    record_launcher = os.path.relpath(launcher, site)
    require(record_launcher in installed_record, "launcher missing from installed RECORD")
    check_record(installed_record[record_launcher], raw)
    owner_sources = {
        name: product["code_forge/" + name] for name in ("_mutation_imports.py", "_mutation_process.py")
    }
    provenance = {
        "source_commit": proof["source_commit"],
        "source_commit_basis": "RECORDED_BUILD_PROVENANCE",
        "git_object_independently_verified": False,
        "source_archive_sha256": sha256(archive_raw),
        "wheel": str(wheel_path),
        "wheel_sha256": sha256(wheel_raw),
        "build_proof_sha256": sha256(proof_raw),
        "distribution_version": metadata["Version"],
        "dist_info": str(installed_dist),
        "installed_record_sha256": sha256(regular_bytes(installed_dist / "RECORD")),
        "launcher_sha256": sha256(raw),
        "product_members": len(product),
        "owner_hashes": {name: sha256(value) for name, value in owner_sources.items()},
    }
    target_identity = interpreter_identity(target, target=True)
    startup, helper_hash = startup_policy(
        prefix, site, target_identity, sha256(product["code_forge_launcher.py"])
    )
    return (
        {
            "launcher": str(launcher),
            "target_python": str(target),
            "installed_provenance": provenance,
            "target": target_identity,
            "startup_admission": startup,
            "startup_helper_sha256": helper_hash,
        },
        owner_sources,
        site / "code_forge",
    )


def load_owner(sources, origin):
    """Execute only two exact verified installed sources in a private package."""
    require(
        not any(name == OWNER_PACKAGE or name.startswith(OWNER_PACKAGE + ".") for name in sys.modules),
        "private owner package occupied",
    )
    package = types.ModuleType(OWNER_PACKAGE)
    package.__path__ = []
    sys.modules[OWNER_PACKAGE] = package
    for leaf in ("_mutation_imports.py", "_mutation_process.py"):
        name = OWNER_PACKAGE + "." + leaf[:-3]
        spec = importlib.util.spec_from_file_location(name, origin / leaf)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        exec(compile(sources[leaf], str(origin / leaf), "exec", dont_inherit=True), module.__dict__)  # noqa: S102 - exact verified installed source, never target package import
    return sys.modules[OWNER_PACKAGE + "._mutation_process"].run_owned_command


def live_children_admission():
    """One exact finite handshake child, not a replacement process supervisor."""
    child = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            "-c",
            "import sys;sys.stdout.write('ready\\n');sys.stdout.flush();sys.stdin.read(1)",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env={"PATH": os.defpath, "LC_ALL": "C.UTF-8"},
    )
    pidfd = None
    try:
        pidfd = os.pidfd_open(child.pid)
        require(select.select([child.stdout], [], [], 3)[0], "admission child readiness timeout")
        require(
            os.read(child.stdout.fileno(), 6) == b"ready\n" and child.poll() is None,
            "admission child failed before observation",
        )
        signal.pidfd_send_signal(pidfd, 0)
        raw = Path(f"/proc/{child.pid}/stat").read_bytes()
        require(
            int(raw[raw.rfind(b")") + 2 :].split()[1]) == os.getpid(),
            "admission child ownership changed",
        )
        path = Path(f"/proc/{child.pid}/task/{child.pid}/children")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            require(os.read(fd, 65537) == b"", "unexpected admission child descendants")
        finally:
            os.close(fd)
        require(child.poll() is None, "admission child exited during observation")
    finally:
        primary = sys.exc_info()[1]
        failures = []
        try:
            child.stdin.close()
        except BaseException as error:  # noqa: BLE001 - still wait even if closing stdin fails
            failures.append(error)
        try:
            try:
                code = child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                if pidfd is None:
                    child.kill()
                else:
                    signal.pidfd_send_signal(pidfd, signal.SIGKILL)
                code = child.wait(timeout=2)
            require(code == 0, "admission child teardown was not clean")
        except BaseException as error:  # noqa: BLE001 - admission cleanup cannot hide its first failure
            failures.append(error)
        for stream in (child.stdout, child.stdin):
            try:
                stream.close()
            except OSError as error:
                failures.append(error)
        if pidfd is not None:
            try:
                os.close(pidfd)
            except OSError as error:
                failures.append(error)
        if failures:
            if primary is not None:
                primary.add_note("admission cleanup: " + str(failures[0]))
            else:
                raise failures[0]


def preflight():
    require(
        sys.platform.startswith("linux") and sys.flags.isolated, "isolated Linux controller required"
    )
    require(
        hasattr(os, "pidfd_open") and hasattr(os, "P_PIDFD") and hasattr(signal, "pidfd_send_signal"),
        "controller lacks required pidfd bindings",
    )
    # Fail before launching anything on kernels/containers lacking actual children views.
    children = Path(f"/proc/{os.getpid()}/task/{os.getpid()}/children")
    fd = os.open(children, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        require(len(os.read(fd, 65537)) <= 65536, "unbounded process children view")
    finally:
        os.close(fd)
    fd = os.pidfd_open(os.getpid())
    os.close(fd)
    live_children_admission()
    return os.readlink("/proc/self/ns/net")


def utility(name):
    search = os.defpath + os.pathsep + "/usr/sbin:/sbin" if name == "ip" else os.defpath
    path = shutil.which(name, path=search)
    require(path is not None, f"required utility unavailable: {name}")
    physical = Path(path).resolve(strict=True)
    raw = regular_bytes(physical)
    return {"path": str(physical), "sha256": sha256(raw)}


def worker_command(binding, mode, output, worker, binding_path):
    return [
        binding["unshare"]["path"],
        "--user",
        "--map-root-user",
        "--net",
        "--",
        sys.executable,
        "-I",
        "-B",
        str(worker),
        "--binding",
        str(binding_path),
        "--mode",
        mode,
        "--output",
        str(output),
    ]


def safe_environment(output):
    return {
        "PATH": os.defpath,
        "HOME": str(output),
        "TMPDIR": str(output),
        "LC_ALL": "C.UTF-8",
        "XDG_CONFIG_HOME": str(output),
        "XDG_CACHE_HOME": str(output),
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def exception_record(error):
    return {
        "type": type(error).__name__,
        "message": str(error),
        "notes": list(getattr(error, "__notes__", [])),
    }


def sanitize_controller(output):
    # The unchanged owner also spawns a helper without an explicit env argument.
    # Sanitize this dedicated controller before any helper or target is launched.
    os.environ.clear()
    os.environ.update(safe_environment(output))


def valid_worker(receipt, mode):
    require(
        type(receipt) is dict
        and receipt.get("schema") == 1
        and receipt.get("mode") == mode
        and receipt.get("transport_status") == "receipt_written",
        "missing/malformed worker receipt",
    )
    require(
        "child_returncode_before_cleanup" in receipt and "primary_exception" in receipt,
        "incomplete primary observation",
    )
    code = receipt.get("child_returncode")
    require(code is None or type(code) is int and -127 <= code <= 255, "invalid signed child returncode")
    require(
        type(receipt.get("cleanup_induced_termination")) is bool
        and type(receipt.get("cleanup_errors")) is list
        and type(receipt.get("collector_health")) is dict,
        "incomplete worker receipt",
    )
    before = receipt["child_returncode_before_cleanup"]
    require(
        before is None or type(before) is int and -127 <= before <= 255, "invalid pre-cleanup status"
    )
    require(not receipt["cleanup_induced_termination"] or before is None, "contradictory cleanup status")
    require(before is None or before == code, "child status changed after observed exit")
    require(
        code is None or type(receipt.get("child_pid")) is int and receipt["child_pid"] > 0,
        "missing child identity",
    )
    peer = receipt.get("peer_transport")
    require(
        type(peer) is dict
        and type(peer.get("requests")) is int
        and type(peer.get("exchanges")) is int
        and type(peer.get("errors")) is list,
        "missing peer transport observation",
    )
    primary = receipt.get("primary_exception")
    require(
        primary is None or type(primary) is dict and type(primary.get("type")) is str,
        "invalid primary exception",
    )
    require(code is not None or primary is not None, "false success with no child observation")
    return receipt


def shell_code(returncode):
    require(type(returncode) is int and -127 <= returncode <= 255, "invalid process status")
    return returncode if returncode >= 0 else 128 - returncode


def final_delivery(binding, worker_source, output, record):
    """Repeat cache and delivery checks after owner-confirmed quiescence."""
    ownership = record.get("ownership") or {}
    require(
        ownership.get("cleanup_complete") is True
        and type(ownership.get("owned")) is list
        and not any(row.get("remaining") for row in ownership["owned"]),
        "final framing requires owner-confirmed descendant quiescence",
    )
    raw = regular_bytes(worker_source, 1024 * 1024)
    require(
        sha256(raw) == binding["capture_hashes"]["capture_worker.py"],
        "capture worker changed before final framing",
    )
    module = types.ModuleType("_forge_capture_final_delivery")
    module.__file__ = str(worker_source)
    exec(compile(raw, str(worker_source), "exec", dont_inherit=True), module.__dict__)  # noqa: S102 - previously hash-bound capture helper, no target imports
    observed = record["worker"]
    cache = observed.get("cache_before_launch")
    require(
        type(cache) is dict and cache.get("prefix") == str(output / "worker/pycache"),
        "cache prefix is not bound to the new worker output",
    )
    cache_health = module.check_cache_snapshot(cache)
    module.check_stdlib_alias(binding.get("startup_admission", {}))
    if record["mode"] == "baseline":
        health = {"status": "not_applicable", "scope": "delivery_framing_only", "errors": []}
    else:
        health = module._collector_health(
            output / "worker/diagnostic", observed["child_pid"], observed["child_returncode"]
        )
    return health | {"stage": "after_owner_quiescence", "cache_health": cache_health}


def select_exit(record):
    """Primary body failure, original exit, outer failure, then capture health.

    A cleanup kill is never substituted for an earlier timeout or cancellation.
    A later metadata/owner cleanup failure cannot erase an already observed7.
    """
    outer = record.get("outer_exception")
    worker = record.get("worker")
    primary = worker.get("primary_exception") if worker else None
    if primary:
        kind = primary["type"]
        return (
            124 if kind == "TimeoutExpired" else 130 if kind == "KeyboardInterrupt" else HARNESS_FAILURE
        )
    before = worker.get("child_returncode_before_cleanup") if worker else None
    if before not in (None, 0):
        return shell_code(before)
    if outer:
        kind = outer["type"]
        return (
            124 if kind == "TimeoutExpired" else 130 if kind == "KeyboardInterrupt" else HARNESS_FAILURE
        )
    if (
        worker
        and worker.get("child_returncode") not in (None, 0)
        and not worker["cleanup_induced_termination"]
    ):
        return shell_code(worker["child_returncode"])
    ownership = record.get("ownership") or {}
    healthy = (
        worker
        and worker.get("child_returncode") == 0
        and not worker.get("cleanup_errors")
        and worker.get("peer_transport") == {"requests": 2, "exchanges": 2, "errors": []}
        and (record.get("final_collector_health") or {}).get("status")
        == ("healthy" if record["mode"] == "diagnostic" else "not_applicable")
    )
    healthy = (
        healthy
        and record.get("worker_returncode") == 0
        and not record.get("record_errors")
        and (record.get("final_collector_health") or {}).get("cache_health", {}).get("status")
        == "healthy"
        and ownership.get("cleanup_complete") is True
        and not any(row.get("remaining") for row in ownership.get("owned", []))
    )
    return 0 if healthy else HARNESS_FAILURE


def apply_limits():
    for kind, bound in (
        (resource.RLIMIT_AS, MEMORY_LIMIT),
        (resource.RLIMIT_FSIZE, FILE_LIMIT),
        (resource.RLIMIT_CPU, 120),
        (resource.RLIMIT_CORE, 0),
    ):
        _soft, hard = resource.getrlimit(kind)
        if hard != resource.RLIM_INFINITY:
            bound = min(bound, hard)
        resource.setrlimit(kind, (bound, bound))


def controller_failure(record, stage, error):
    """Record the first observed controller failure, never its private message.

    This locates a failed controller operation, not the cause of worker failure.
    Later receipt/cleanup errors must not overwrite an earlier owner failure.
    """
    if record["controller_error_category"] != "NONE":
        return
    if isinstance(error, (TimeoutError, subprocess.TimeoutExpired)):
        category = "TIMEOUT"
    elif isinstance(error, KeyboardInterrupt):
        category = "INTERRUPTED"
    elif isinstance(error, OSError):
        number = error.errno
        category = (
            {
                errno.EACCES: "PERMISSION_DENIED",
                errno.EPERM: "PERMISSION_DENIED",
                errno.ENOENT: "NOT_FOUND",
                errno.ENOTDIR: "NOT_DIRECTORY",
            }.get(number, "OS_ERROR")
            if type(number) is int
            else "OS_ERROR"
        )
    elif isinstance(error, (ValueError, TypeError, KeyError)):
        category = "INVALID_DATA"
    else:
        category = "OTHER_ERROR"
    record["controller_stage"] = stage
    record["controller_error_category"] = category


def capture(evidence, mode, output):
    os.umask(0o077)
    output = new_private_directory(output)
    record = {
        "schema": 1,
        "controller_stage": "INITIALIZE",
        "controller_error_category": "NONE",
        "mode": mode,
        "qualification": "NOT_ASSESSED",
        "historical_run08": "UNKNOWN",
        "worker": None,
        "final_collector_health": None,
        "worker_returncode": None,
        "outer_exception": None,
        "ownership": None,
        "record_errors": [],
        "capture_source_base": CAPTURE_BASE,
        "capture_scope": "delivery_framing_only",
        "startup_policy": "private_empty_bytecode_cache",
        "timing_equivalent_to_historical_warm_cache": False,
        "setuptools_use_distutils": "stdlib",
        "stream_correlation": "NOT_ASSESSED",
        "ack_consistency": "NOT_ASSESSED",
        "bounds": {
            "worker_wall_seconds": OUTER_SECONDS,
            "cli_wait_seconds": 20,
            "address_space_bytes_per_process": MEMORY_LIMIT,
            "file_bytes_each": FILE_LIMIT,
            "controller_cpu_seconds": 120,
            "worker_cpu_seconds": 25,
            "aggregate_memory_or_disk_quota": False,
        },
    }
    stage = "SANITIZE"
    try:
        sanitize_controller(output)
        stage = "PREFLIGHT"
        host_netns = preflight()
        stage = "LIMITS"
        apply_limits()
        stage = "INSTALLATION"
        binding, sources, origin = verify_installation(evidence)
        binding.update(schema=1, host_netns=host_netns, controller=interpreter_identity(sys.executable))
        stage = "UTILITY_BINDING"
        for name in ("unshare", "ip", "git"):
            identity = utility(name)
            binding[name] = identity if name == "unshare" else identity["path"]
            binding.setdefault("utility_identities", {})[name] = identity
        if mode == "diagnostic":
            binding["utility_identities"]["strace"] = utility("strace")
        stage = "WORKER_BINDING"
        home = Path(__file__).resolve().parents[1]
        worker = home / "qualification/installed_cli/capture_worker.py"
        binding["capture_hashes"] = {
            name: sha256(regular_bytes(worker.with_name(name)))
            for name in ("capture_worker.py", "diagnostic_run.py", "diagnostic_probe.py")
        }
        require(
            binding["startup_helper_sha256"] == binding["capture_hashes"]["capture_worker.py"],
            "startup admission helper changed during binding",
        )
        binding["entrypoint_sha256"] = sha256(regular_bytes(Path(__file__).resolve()))
        save_json(output / "binding.json", binding)
        stage = "OWNER_LOAD"
        owner = load_owner(sources, origin)
        stage = "WORKER_EXECUTION"
        try:
            completed = owner(
                worker_command(binding, mode, output / "worker", worker, output / "binding.json"),
                timeout=OUTER_SECONDS,
                cwd=str(output),
                env=safe_environment(output),
                memory_limit_bytes=MEMORY_LIMIT,
            )
            record.update(worker_returncode=completed.returncode, ownership=completed.ownership)
        except BaseException as error:  # noqa: BLE001 - original cancellation and owner failure remain primary
            controller_failure(record, stage, error)
            record.update(
                outer_exception=exception_record(error),
                ownership=getattr(error, "ownership", getattr(error, "report", record["ownership"])),
            )
        else:
            stage = "WORKER_OUTPUT"
            for leaf, raw in (("worker.stdout", completed.stdout), ("worker.stderr", completed.stderr)):
                try:
                    private_bytes(output / leaf, raw)
                except (OSError, ValueError) as error:
                    controller_failure(record, stage, error)
                    record["record_errors"].append(exception_record(error))
        stage = "WORKER_RECEIPT"
        try:
            record["worker"] = valid_worker(read_json(output / "worker/outcome.json"), mode)
            stage = "FINAL_DELIVERY"
            record["final_collector_health"] = final_delivery(binding, worker, output, record)
        except (OSError, ValueError, TypeError, KeyError) as error:
            controller_failure(record, stage, error)
            record["record_errors"].append(exception_record(error))
    except BaseException as error:  # noqa: BLE001 - retain partial private evidence on setup/cancellation failure
        controller_failure(record, stage, error)
        if record["outer_exception"] is None:
            record["outer_exception"] = exception_record(error)
        else:
            record["record_errors"].append(exception_record(error))
    if record["controller_error_category"] == "NONE":
        record["controller_stage"] = "FINALIZE"
    code = select_exit(record)
    record["shell_exit"] = code
    record["shell_signal_mapping"] = "negative Popen returncode -> 128 + signal number"
    try:
        save_json(output / "result.json", record)
    except (OSError, ValueError, TypeError):
        # A captured failing CLI remains the failing CLI; missing metadata cannot
        # manufacture success when the child itself succeeded.
        return code or HARNESS_FAILURE
    return code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel-evidence", required=True, type=Path)
    parser.add_argument("--mode", required=True, choices=("baseline", "diagnostic"))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        return capture(args.wheel_evidence, args.mode, args.output)
    except (OSError, ValueError):
        # Never print private paths or runtime records into the invoking terminal.
        print("capture setup failed; retain the private output if created", file=sys.stderr)
        return HARNESS_FAILURE


if __name__ == "__main__":
    raise SystemExit(main())
