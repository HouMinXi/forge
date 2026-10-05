# SPDX-License-Identifier: Apache-2.0
"""Private file/Git controls for the external launcher, without installing Forge."""

from __future__ import annotations

import csv
from contextlib import contextmanager, nullcontext
import importlib.resources
import importlib.util
from importlib import metadata
from importlib.machinery import ExtensionFileLoader, EXTENSION_SUFFIXES
import json
import marshal
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import subprocess
import sys
import struct
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock
import runpy
import signal
import stat
import time

SOURCE = Path(
    os.environ.get(
        "FORGE_LAUNCHER_UNIT_SOURCE",
        str(Path(__file__).resolve().parents[1] / "src/code_forge_launcher.py"),
    )
)


def _unit_venv_probe():
    import site

    if any(name == "code_forge" or name.startswith("code_forge.") for name in sys.modules):
        raise RuntimeError("private interpreter probe refuses preloaded Forge")
    try:
        import fixture_dependency

        dependency = fixture_dependency.VALUE
    except ModuleNotFoundError:
        dependency = None
    sites = site.getsitepackages()
    print(
        json.dumps(
            {
                "executable": sys.executable,
                "prefix": sys.prefix,
                "base_prefix": sys.base_prefix,
                "sites": sites,
                "forge_count": sum(
                    dist.metadata.get("Name") == "code-review-forge"
                    for dist in metadata.distributions(path=sites)
                ),
                "dependency": dependency,
            }
        )
    )


if __name__ == "__main__" and sys.argv[1:] == ["--unit-venv-probe"]:
    _unit_venv_probe()
    raise SystemExit(0)

pytest = importlib.import_module("pytest")


def load_launcher():
    name = "_forge_launcher_unit"
    spec = importlib.util.spec_from_file_location(name, SOURCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_launcher_with_parent_transition():
    import builtins

    real_import = builtins.__import__
    real_getppid = os.getppid
    initial = real_getppid()
    parent = [initial]

    def import_with_transition(name, *args, **kwargs):
        if name == "urllib.request":
            parent[0] = initial + 1
        return real_import(name, *args, **kwargs)

    builtins.__import__ = import_with_transition
    os.getppid = lambda: parent[0]
    try:
        module = load_launcher()
    finally:
        builtins.__import__ = real_import
        os.getppid = real_getppid
    assert parent[0] != initial
    module.os = SimpleNamespace(**{**vars(os), "getppid": lambda: parent[0]})
    return module, [os.getpid(), initial]


@pytest.fixture
def launcher():
    return load_launcher()


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@contextmanager
def windows_metadata_surface(module, *, damage=None, replacement=None):
    """Exercise the documented Windows ABI using owned Linux descriptors, not a Windows kernel."""
    import ctypes

    previous_os = module.os
    native_os = vars(previous_os).copy()
    for name in ("O_NOFOLLOW", "O_NONBLOCK"):
        native_os.pop(name)
    native_os.update(name="nt", O_BINARY=0x8000, O_NOINHERIT=0x80)
    surface = SimpleNamespace(calls=[], raw_handles=[], closed=[], transfers=[], reads=[])

    def create_file(spelling, access, sharing, security, disposition, flags, template):
        surface.calls.append(
            ("create", spelling, access, sharing, security, disposition, flags, template)
        )
        if damage in {"open", "open-missing"}:
            if damage == "open-missing":
                Path(spelling).unlink()
            return ctypes.c_void_p(-1).value
        path = Path(spelling)
        if damage == "replacement-symlink":
            path.unlink()
            path.symlink_to(replacement)
        posix_flags = os.O_RDONLY | os.O_NONBLOCK
        if flags & 0x00200000:
            posix_flags |= os.O_NOFOLLOW
            if path.is_symlink():
                posix_flags = os.O_PATH | os.O_NOFOLLOW
        handle = os.open(path, posix_flags)
        surface.raw_handles.append(handle)
        return handle

    def file_type(handle):
        surface.calls.append(("type", handle))
        mode = os.fstat(handle).st_mode
        return 0 if damage == "type" else (3 if stat.S_ISFIFO(mode) else 2 if stat.S_ISCHR(mode) else 1)

    def attributes(handle, information_class, output, size):
        surface.calls.append(("attributes", handle, information_class, size))
        assert information_class == 9 and size == 8
        if damage == "attributes":
            return 0
        mode = os.fstat(handle).st_mode
        output._obj.FileAttributes = (0x10 if stat.S_ISDIR(mode) else 0) | (
            0x400 if stat.S_ISLNK(mode) else 0
        )
        output._obj.ReparseTag = 0
        return 1

    def close_handle(handle):
        os.close(handle)
        surface.closed.append(handle)
        return 0 if damage == "close" else 1

    def transfer(handle, flags):
        if damage == "transfer":
            raise OSError("owned CRT transfer failed")
        assert flags == 0x8080
        surface.transfers.append(handle)
        return handle

    def read(fd, count):
        surface.reads.append(fd)
        return os.read(fd, count)

    library = SimpleNamespace(
        CreateFileW=create_file,
        GetFileType=file_type,
        GetFileInformationByHandleEx=attributes,
        CloseHandle=close_handle,
    )

    def win_dll(name, *, use_last_error):
        assert name == "kernel32" and use_last_error is True
        return library

    def last_error():
        return 2 if damage == "open-missing" else 13

    def win_error(error):
        assert error in {2, 13}
        return (
            FileNotFoundError("owned native file vanished")
            if error == 2
            else PermissionError("owned native call failed")
        )

    replacements = {"WinDLL": win_dll, "get_last_error": last_error, "WinError": win_error}
    prior = {name: getattr(ctypes, name, None) for name in replacements}
    previous_crt = sys.modules.get("msvcrt")
    crt = ModuleType("msvcrt")
    crt.open_osfhandle = transfer
    sys.modules["msvcrt"] = crt
    for name, value in replacements.items():
        setattr(ctypes, name, value)
    native_os["read"] = read
    module.os = SimpleNamespace(**native_os)
    try:
        yield surface
    finally:
        module.os = previous_os
        if previous_crt is None:
            sys.modules.pop("msvcrt")
        else:
            sys.modules["msvcrt"] = previous_crt
        for name, value in prior.items():
            if value is None:
                delattr(ctypes, name)
            else:
                setattr(ctypes, name, value)


class MetadataReadDeadline(Exception):
    pass


@contextmanager
def metadata_read_deadline(seconds=0.25):
    initial = signal.getsignal(signal.SIGALRM)
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)

    def expire(_signal, _frame):
        raise MetadataReadDeadline("private metadata read exceeded its deadline")

    signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, initial)


def test_metadata_deadline_interrupts_and_restores_owned_timer():
    before = signal.getsignal(signal.SIGALRM)
    with pytest.raises(MetadataReadDeadline), metadata_read_deadline(0.01):
        time.sleep(0.1)
    assert signal.getsignal(signal.SIGALRM) == before
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)


@pytest.mark.parametrize(
    "filename", ["METADATA", "PKG-INFO", "RECORD", "installed-files.txt", "SOURCES.txt", ""]
)
def test_physical_distribution_matches_stdlib_text(launcher, tmp_path, filename):
    root = tmp_path / ("standalone.egg-info" if not filename else "example.dist-info")
    path = root / filename
    write(path, "first\r\nlast\r")
    expected = metadata.PathDistribution(root).read_text(filename)
    assert (
        launcher._physical_distribution(metadata.PathDistribution(root)).read_text(filename) == expected
    )
    assert expected == "first\nlast\n"


@pytest.mark.parametrize(
    "filename", ["METADATA", "PKG-INFO", "RECORD", "installed-files.txt", "SOURCES.txt", ""]
)
def test_physical_distribution_fifo_reader_refuses(launcher, tmp_path, filename):
    root = tmp_path / ("standalone.egg-info" if not filename else "example.dist-info")
    path = root / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(path)
    with metadata_read_deadline(), pytest.raises(launcher.SourceError, match="regular file"):
        launcher._physical_distribution(metadata.PathDistribution(root)).read_text(filename)


def test_metadata_open_replacement_fifo_refuses_without_blocking(launcher, tmp_path, monkeypatch):
    path = tmp_path / "METADATA"
    write(path, "Name: original\n")
    original = os.open

    def replace_before_open(spelling, flags, *args, **kwargs):
        if Path(spelling) == path:
            path.unlink()
            os.mkfifo(path)
        return original(spelling, flags, *args, **kwargs)

    monkeypatch.setattr(launcher.os, "open", replace_before_open)
    with metadata_read_deadline(), pytest.raises(launcher.SourceError, match="regular file"):
        launcher._metadata_bytes(path, within=tmp_path)


def test_metadata_open_replacement_symlink_refuses_before_read(launcher, tmp_path, monkeypatch):
    owner = tmp_path / "metadata"
    path = owner / "METADATA"
    outside = tmp_path / "outside"
    write(path, "Name: original\n")
    write(outside, "foreign physical bytes\n")
    original_open, original_read = os.open, os.read
    reads = []

    def replaced(target, flags):
        if target == path:
            path.unlink()
            path.symlink_to(outside)
        return original_open(target, flags)

    def read(fd, count):
        reads.append(fd)
        return original_read(fd, count)

    monkeypatch.setattr(launcher.os, "read", read)
    assert launcher._metadata_bytes(path, within=owner)[1] == b"Name: original\n"
    assert reads
    reads.clear()
    monkeypatch.setattr(launcher.os, "open", replaced)
    with pytest.raises(launcher.SourceError):
        launcher._metadata_bytes(path, within=owner)
    assert reads == []


@pytest.mark.parametrize("change", ["append", "replace", "rewrite", "symlink"])
def test_metadata_reader_refuses_file_changes(launcher, tmp_path, monkeypatch, change):
    path = tmp_path / "METADATA"
    write(path, "original\n")
    original = os.read
    changed = False

    def change_after_read(descriptor, count):
        nonlocal changed
        data = original(descriptor, count)
        if not changed:
            changed = True
            if change == "append":
                with path.open("ab") as stream:
                    stream.write(b"growth\n")
            elif change == "rewrite":
                path.write_bytes(b"modified\n")
            else:
                replacement = tmp_path / "replacement"
                write(replacement, "modified\n")
                if change == "replace":
                    replacement.replace(path)
                else:
                    path.unlink()
                    path.symlink_to(replacement)
        return data

    monkeypatch.setattr(launcher.os, "read", change_after_read)
    with pytest.raises(launcher.SourceError, match="changed while being read"):
        launcher._metadata_bytes(path, within=tmp_path)


@pytest.mark.parametrize("damage", ["unknown-name", "unknown-provider", "owner-escape", "invalid-utf8"])
def test_physical_metadata_reader_refuses_unbound_inputs(launcher, tmp_path, damage):
    root = tmp_path / "example.dist-info"
    write(root / "METADATA", "Name: example\n")
    dist = metadata.PathDistribution(root)
    with pytest.raises(launcher.SourceError):
        if damage == "unknown-provider":
            launcher._physical_distribution(SimpleNamespace(metadata={}))
        elif damage == "owner-escape":
            launcher._physical_distribution(dist, tmp_path / "another-site")
        elif damage == "unknown-name":
            launcher._physical_distribution(dist).read_text("unexpected.json")
        else:
            (root / "METADATA").write_bytes(b"\xff")
            launcher._physical_distribution(dist).read_text("METADATA")


@pytest.mark.parametrize("damage", ["missing", "directory", "not-directory"])
def test_metadata_reader_keeps_missing_input_semantics(launcher, tmp_path, damage):
    root = tmp_path / "example.dist-info"
    root.mkdir()
    path = root / "METADATA"
    if damage == "directory":
        path.mkdir()
    elif damage == "not-directory":
        write(path, "regular parent\n")
        path = path / "child"
    assert launcher._metadata_bytes(path, within=root) is None


@pytest.mark.parametrize("operation", ["open", "fstat", "read", "none"])
def test_metadata_reader_errors_close_owned_descriptor(launcher, tmp_path, monkeypatch, operation):
    path = tmp_path / "METADATA"
    write(path, "Name: example\n")
    original_open, original_fstat, original_read = os.open, os.fstat, os.read
    descriptor = None

    def opened(target, flags):
        nonlocal descriptor
        if target == path and operation == "open":
            raise PermissionError("owned open refused")
        descriptor = original_open(target, flags)
        return descriptor

    def inspected(fd):
        if fd == descriptor and operation == "fstat":
            raise OSError("owned fstat refused")
        return original_fstat(fd)

    def read(fd, count):
        if fd == descriptor and operation == "read":
            raise OSError("owned read refused")
        return original_read(fd, count)

    monkeypatch.setattr(launcher.os, "open", opened)
    monkeypatch.setattr(launcher.os, "fstat", inspected)
    monkeypatch.setattr(launcher.os, "read", read)
    if operation == "none":
        assert launcher._metadata_bytes(path, within=tmp_path) == (path, b"Name: example\n")
    else:
        with pytest.raises(launcher.SourceError, match="cannot be read safely"):
            launcher._metadata_bytes(path, within=tmp_path)
    if descriptor is not None:
        with pytest.raises(OSError, match="Bad file descriptor"):
            original_fstat(descriptor)


def test_physical_distribution_retains_bound_adapter_and_missing_text(launcher, tmp_path):
    root = tmp_path / "empty.dist-info"
    root.mkdir()
    adapter = launcher._physical_distribution(metadata.PathDistribution(root), tmp_path)
    assert launcher._physical_distribution(adapter, tmp_path) is adapter
    assert adapter.read_text("METADATA") is None
    assert adapter.metadata.get("Name") is None
    assert adapter.files is None


def test_metadata_reader_accepts_contained_symlink_and_large_regular_file(launcher, tmp_path):
    original = tmp_path / "physical"
    original.write_bytes(b"x" * 65537)
    link = tmp_path / "METADATA"
    link.symlink_to(original)
    assert launcher._metadata_bytes(link, within=tmp_path) == (original, b"x" * 65537)


def test_metadata_reader_refuses_owner_escape_and_symlink_cycle(launcher, tmp_path):
    owner = tmp_path / "owner"
    owner.mkdir()
    outside = tmp_path / "METADATA"
    write(outside, "Name: example\n")
    with pytest.raises(launcher.SourceError, match="escapes its owner"):
        launcher._metadata_bytes(outside, within=owner)
    cycle = owner / "METADATA"
    cycle.symlink_to(cycle)
    with pytest.raises(launcher.SourceError, match="cannot be read safely"):
        launcher._metadata_bytes(cycle, within=owner)


def test_physical_distribution_missing_owner_refuses(launcher, tmp_path):
    with pytest.raises(launcher.SourceError, match="owner cannot be resolved"):
        launcher._physical_distribution(metadata.PathDistribution(tmp_path / "missing"), tmp_path)


@pytest.mark.parametrize(
    "point",
    [
        "manifest-metadata",
        "manifest-record",
        "parse-metadata",
        "direct-url",
        "digest-metadata",
        "digest-record",
    ],
)
def test_metadata_sibling_reads_refuse_fifo(launcher, tmp_path, monkeypatch, point):
    standard_site, root, info = wheel(tmp_path)
    use_site(launcher, monkeypatch, standard_site, standard_site / "code_forge_launcher.py")

    def replace(name):
        path = info / name
        if path.exists():
            path.unlink()
        os.mkfifo(path)

    if point.startswith("manifest-"):
        original = launcher._distribution_files

        def after_files(*args, **kwargs):
            result = original(*args, **kwargs)
            replace("METADATA" if point == "manifest-metadata" else "RECORD")
            return result

        monkeypatch.setattr(launcher, "_distribution_files", after_files)

        def action():
            return launcher._manifest(metadata.PathDistribution(info), standard_site)
    elif point in {"parse-metadata", "direct-url"}:
        original = launcher._manifest

        def after_manifest(*args):
            result = original(*args)
            replace("METADATA" if point == "parse-metadata" else "direct_url.json")
            return result

        monkeypatch.setattr(launcher, "_manifest", after_manifest)
        action = launcher.discover_install
    else:
        installed = launcher.discover_install()
        replace("METADATA" if point == "digest-metadata" else "RECORD")

        def action():
            return launcher._digest(installed, root, None)

    with metadata_read_deadline(), pytest.raises(launcher.SourceError, match="regular file"):
        action()


@pytest.mark.parametrize(
    "point",
    ["manifest-metadata", "manifest-record", "parse-metadata", "digest-metadata", "digest-record"],
)
def test_metadata_siblings_refuse_missing_required_input(launcher, tmp_path, monkeypatch, point):
    standard_site, root, info = wheel(tmp_path)
    use_site(launcher, monkeypatch, standard_site, standard_site / "code_forge_launcher.py")
    if point.startswith("manifest-"):
        original = launcher._distribution_files

        def after_files(*args, **kwargs):
            names = original(*args, **kwargs)
            (info / ("METADATA" if point == "manifest-metadata" else "RECORD")).unlink()
            return names

        monkeypatch.setattr(launcher, "_distribution_files", after_files)

        def action():
            return launcher._manifest(metadata.PathDistribution(info), standard_site)

    elif point == "parse-metadata":
        original = launcher._manifest

        def after_manifest(*args):
            manifest = original(*args)
            (info / "METADATA").unlink()
            return manifest

        monkeypatch.setattr(launcher, "_manifest", after_manifest)
        action = launcher.discover_install
    else:
        installed = launcher.discover_install()
        (info / ("METADATA" if point == "digest-metadata" else "RECORD")).unlink()

        def action():
            return launcher._digest(installed, root, None)

    with pytest.raises(launcher.SourceError, match="missing"):
        action()


@pytest.mark.parametrize("damage", ["invalid-utf8", "unfinished-csv"])
def test_manifest_changed_record_decoder_errors_refuse(launcher, tmp_path, monkeypatch, damage):
    standard_site, _, info = wheel(tmp_path)
    original = launcher._distribution_files

    def after_files(*args, **kwargs):
        names = original(*args, **kwargs)
        (info / "RECORD").write_bytes(b"\xff" if damage == "invalid-utf8" else b'"unfinished')
        return names

    monkeypatch.setattr(launcher, "_distribution_files", after_files)
    with pytest.raises(launcher.SourceError, match="RECORD cannot be read"):
        launcher._manifest(metadata.PathDistribution(info), standard_site)


def test_manifest_wrong_interpreter_site_refuses(launcher, tmp_path):
    _, _, info = wheel(tmp_path)
    with pytest.raises(launcher.SourceError, match="outside its interpreter site"):
        launcher._manifest(metadata.PathDistribution(info), tmp_path / "another-site")


def package(root, version="2.9.0"):
    write(root / "__init__.py", f"__version__ = {version!r}\n")
    write(
        root / "cli.py",
        "import sys\nARGV = None\ndef main():\n    global ARGV\n    ARGV = list(sys.argv[1:])\n    return 7\n",
    )
    write(
        root / "mcp_server.py", "STARTED = False\ndef main():\n    global STARTED\n    STARTED = True\n"
    )
    write(root / "data.txt", "selected resource\n")


def record(site_root, dist_name, version, names):
    info = site_root / f"{dist_name}-{version}.dist-info"
    write(
        info / "METADATA",
        f"Metadata-Version: 2.1\nName: {dist_name.replace('_', '-')}\nVersion: {version}\n",
    )
    rows = [
        *names,
        str((info / "METADATA").relative_to(site_root)),
        str((info / "RECORD").relative_to(site_root)),
    ]
    with (info / "RECORD").open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerows((name, "", "") for name in rows)
    return info


def wheel(tmp_path):
    standard_site = tmp_path / "site"
    root = standard_site / "code_forge"
    package(root)
    write(standard_site / "code_forge_launcher.py", SOURCE.read_text(encoding="utf-8"))
    names = [
        "code_forge/__init__.py",
        "code_forge/cli.py",
        "code_forge/mcp_server.py",
        "code_forge/data.txt",
        "code_forge_launcher.py",
    ]
    info = record(standard_site, "code_review_forge", "2.9.0", names)
    return standard_site, root, info


def use_site(launcher, monkeypatch, standard_site, launcher_file):
    monkeypatch.setattr(launcher.site, "getsitepackages", lambda: [str(standard_site)])
    monkeypatch.setattr(launcher.site, "ENABLE_USER_SITE", False)
    monkeypatch.setattr(launcher, "__file__", str(launcher_file))


@pytest.mark.parametrize("operation", ["cli", "stdio"])
def test_windows_metadata_api_surface_preserves_wheel_entries(tmp_path, operation):
    standard_site, root, _ = wheel(tmp_path)
    result, stderr = probe(
        tmp_path,
        operation,
        SOURCE,
        standard_site,
        standard_site / "code_forge_launcher.py",
        tmp_path,
        windows_metadata=True,
    )
    assert result["origin"] == str(root / "__init__.py") and stderr == ""
    assert result["exit"] == (7 if operation == "cli" else 0)
    assert result["business_main_called"] is (operation == "cli")
    assert result["started"] is (operation == "stdio")


@pytest.mark.parametrize("data", [b"", b"first\r\nlast\r"])
def test_windows_metadata_surface_transfers_regular_handle_once(launcher, tmp_path, data):
    path = tmp_path / "METADATA"
    path.write_bytes(data)
    with windows_metadata_surface(launcher) as surface:
        assert launcher._metadata_bytes(path, within=tmp_path) == (path, data)
    assert len(surface.raw_handles) == len(surface.transfers) == 1
    assert surface.closed == []
    for fd in surface.raw_handles:
        with pytest.raises(OSError, match="Bad file descriptor"):
            os.fstat(fd)
    assert surface.calls[0][2:] == (0x80000000, 7, None, 3, 0x02200000, None)


@pytest.mark.parametrize("damage", ["directory", "missing", "open-missing"])
def test_windows_metadata_surface_preserves_missing_and_directory(launcher, tmp_path, damage):
    path = tmp_path / "METADATA"
    if damage == "directory":
        path.mkdir()
    elif damage == "open-missing":
        write(path, "vanishing regular file\n")
    with windows_metadata_surface(launcher, damage=damage) as surface:
        assert launcher._metadata_bytes(path, within=tmp_path) is None
    assert surface.transfers == surface.reads == []
    assert surface.closed == surface.raw_handles


@pytest.mark.parametrize("damage", ["open", "type", "attributes", "transfer", "close"])
def test_windows_metadata_surface_errors_retire_raw_handles(launcher, tmp_path, damage):
    path = tmp_path / "METADATA"
    path.mkdir() if damage == "close" else write(path, "Name: example\n")
    with windows_metadata_surface(launcher, damage=damage) as surface:
        with pytest.raises(launcher.SourceError):
            launcher._metadata_bytes(path, within=tmp_path)
    assert surface.transfers == surface.reads == []
    assert surface.closed == surface.raw_handles
    for fd in surface.raw_handles:
        with pytest.raises(OSError, match="Bad file descriptor"):
            os.fstat(fd)


def test_windows_metadata_surface_reparse_replacement_refuses_before_transfer(launcher, tmp_path):
    owner = tmp_path / "metadata"
    path = owner / "METADATA"
    outside = tmp_path / "outside"
    write(path, "initial owned input\n")
    write(outside, "foreign physical bytes\n")
    with windows_metadata_surface(
        launcher, damage="replacement-symlink", replacement=outside
    ) as surface:
        with pytest.raises(launcher.SourceError):
            launcher._metadata_bytes(path, within=owner)
    assert surface.transfers == surface.reads == []
    assert surface.closed == surface.raw_handles and len(surface.closed) == 1


@pytest.mark.parametrize("kind", ["fifo", "character"])
def test_windows_metadata_surface_nondisk_refuses_before_transfer(launcher, tmp_path, kind):
    path = tmp_path / "METADATA" if kind == "fifo" else Path("/dev/null")
    if kind == "fifo":
        os.mkfifo(path)
    with metadata_read_deadline(), windows_metadata_surface(launcher) as surface:
        with pytest.raises(launcher.SourceError, match="disk file"):
            launcher._metadata_bytes(path, within=path.parent)
    assert surface.transfers == surface.reads == []
    assert surface.closed == surface.raw_handles and len(surface.closed) == 1


def test_windows_metadata_surface_restores_preexisting_adapters(launcher, tmp_path, monkeypatch):
    import ctypes

    previous = ModuleType("msvcrt")
    monkeypatch.setitem(sys.modules, "msvcrt", previous)
    sentinel = object()
    for name in ("WinDLL", "get_last_error", "WinError"):
        monkeypatch.setattr(ctypes, name, sentinel, raising=False)
    path = tmp_path / "METADATA"
    write(path, "owned regular\n")
    previous_os = launcher.os
    with windows_metadata_surface(launcher):
        assert launcher._metadata_bytes(path, within=tmp_path)[1] == b"owned regular\n"
    assert launcher.os is previous_os and sys.modules["msvcrt"] is previous
    assert all(getattr(ctypes, name) is sentinel for name in ("WinDLL", "get_last_error", "WinError"))


def git(root, *args):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null", GIT_TERMINAL_PROMPT="0")
    return subprocess.run(
        ["git", *args],
        cwd=root,
        env=env,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=2,
        check=True,
    ).stdout


def editable(tmp_path, layout="static", percent=False, *, nested=False, installed_linked=False):
    checkout = tmp_path / ("anchor%2Fname" if percent else "anchor")
    checkout.mkdir()
    git(checkout, "init", "--initial-branch=main")
    package(checkout / "src/code_forge")
    write(checkout / "src/code_forge_launcher.py", SOURCE.read_text(encoding="utf-8"))
    write(
        checkout / "pyproject.toml",
        '[project]\nname="code-review-forge"\nversion="2.9.0"\nrequires-python=">=3.12"\ndependencies=[]\n',
    )
    git(checkout, "add", "src", "pyproject.toml")
    git(
        checkout,
        "-c",
        "user.name=Forge Fixture",
        "-c",
        "user.email=fixture@localhost",
        "commit",
        "--no-gpg-sign",
        "-m",
        "Create private launcher fixture",
    )
    linked = checkout / ".worktrees/review" if nested else tmp_path / "linked"
    git(checkout, "worktree", "add", "-b", "fixture-worktree", str(linked), "HEAD")
    installed_checkout = linked if installed_linked else checkout
    standard_site = tmp_path / "site"
    standard_site.mkdir()
    if layout == "finder":
        pth = "import fixture_editable_finder\n"
    elif layout == "link-tree":
        links = installed_checkout / "build/links"
        links.mkdir(parents=True)
        (links / "code_forge").symlink_to(
            installed_checkout / "src/code_forge", target_is_directory=True
        )
        (links / "code_forge_launcher.py").symlink_to(installed_checkout / "src/code_forge_launcher.py")
        pth = str(links) + "\n"
    else:
        pth = str(installed_checkout / "src") + "\n"
    write(standard_site / "forge.pth", pth)
    info = record(
        standard_site,
        "code_review_forge",
        "2.9.0",
        ["forge.pth", "code_review_forge-2.9.0.dist-info/direct_url.json"],
    )
    write(
        info / "direct_url.json",
        json.dumps({"url": installed_checkout.as_uri(), "dir_info": {"editable": True}}),
    )
    return standard_site, checkout, linked, info


def probe(
    tmp_path,
    operation,
    source,
    site_root,
    launcher_file,
    workspace,
    *,
    timeout=5,
    windows_metadata=False,
    parent_transition=False,
    empty_environment=False,
):
    request = tmp_path / "probe.json"
    request.write_text(
        json.dumps(
            {
                "operation": operation,
                "site": str(site_root),
                "launcher": str(launcher_file),
                "workspace": str(workspace),
                "windows_metadata": windows_metadata,
                "parent_transition": parent_transition,
                "empty_environment": empty_environment,
            }
        ),
        encoding="utf-8",
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_") and key not in {"PYTHONPATH", "PYTHONHOME"}
    }
    env.update(FORGE_LAUNCHER_UNIT_SOURCE=str(source), PYTHONDONTWRITEBYTECODE="1")
    args = [sys.executable, "-P", str(Path(__file__).resolve()), "--unit-probe", str(request)]
    result = subprocess.run(
        args,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
    )
    for name, content in (("probe.stdout", result.stdout), ("probe.stderr", result.stderr)):
        with (tmp_path / name).open("x", encoding="utf-8") as stream:
            stream.write(content)
    with (tmp_path / "probe.result.json").open("x") as stream:
        json.dump({"argv": args, "cwd": str(tmp_path), "returncode": result.returncode}, stream)
    result.check_returncode()
    return json.loads(result.stdout), result.stderr


def test_import_is_inert(monkeypatch):
    before_path, before_finders = list(sys.path), list(sys.meta_path)
    monkeypatch.setattr(
        subprocess, "Popen", lambda *a, **k: pytest.fail("launcher import spawned a process")
    )
    module = load_launcher()
    assert module.SourceError.__bases__ == (RuntimeError,)
    assert sys.path == before_path and sys.meta_path == before_finders


@pytest.mark.parametrize("kind", ["none", "string", "missing", "file", "permission", "resolution"])
def test_workspace_contract_refuses_invalid_inputs(launcher, tmp_path, monkeypatch, kind):
    path = tmp_path
    if kind == "none":
        path = None
    elif kind == "string":
        path = str(tmp_path)
    elif kind == "missing":
        path = tmp_path / "missing"
    elif kind == "file":
        path = tmp_path / "file"
        write(path, "data")
    elif kind == "permission":
        monkeypatch.setattr(launcher.os, "access", lambda *a: False)
    else:
        monkeypatch.setattr(
            Path, "resolve", lambda *a, **k: (_ for _ in ()).throw(OSError("unit path failure"))
        )
    with pytest.raises(launcher.SourceError):
        launcher.select_source(path)


def test_required_source_file_must_be_regular(launcher, tmp_path):
    with pytest.raises(launcher.SourceError):
        launcher._file(tmp_path)


@pytest.mark.parametrize("kind", ["name", "version", "package-escape"])
def test_project_identity_and_layout_refuse(launcher, tmp_path, kind):
    root = tmp_path / "project"
    package(root / "src/code_forge")
    name = "ordinary" if kind == "name" else "code-review-forge"
    version = "" if kind == "version" else "2.9.0"
    write(root / "pyproject.toml", f"[project]\nname={name!r}\nversion={version!r}\n")
    if kind == "package-escape":
        (root / "src/code_forge").rename(root / "saved-package")
        outside = tmp_path / "outside-package"
        outside.mkdir()
        (outside / "__init__.py").symlink_to(root / "saved-package/__init__.py")
        (root / "src/code_forge").symlink_to(outside, target_is_directory=True)
    with pytest.raises(launcher.SourceError):
        launcher._project(root)


@pytest.mark.parametrize("kind", ["timeout", "missing-git", "error", "empty"])
def test_discovery_git_failure_is_explicit(launcher, tmp_path, monkeypatch, kind):
    def fail(*args, **kwargs):
        if kind == "timeout":
            raise subprocess.TimeoutExpired(args[0], 2.0)
        if kind == "missing-git":
            raise FileNotFoundError("unit missing Git")
        return subprocess.CompletedProcess(args[0], 1 if kind == "error" else 0, b"", b"unit failure")

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(launcher.SourceError):
        launcher._git(tmp_path, "rev-parse", "--show-toplevel")


@pytest.mark.parametrize("raw", [b"", b"first\nsecond\n", b"bad\0path", b"\xff"])
def test_git_path_protocol_refuses_malformed_bytes(launcher, tmp_path, raw):
    with pytest.raises(launcher.SourceError):
        launcher._git_path(raw, tmp_path)


@pytest.mark.parametrize(
    "kind", ["terminator", "entry", "relative", "unicode", "duplicate", "field", "missing-anchor"]
)
def test_registry_protocol_requires_exact_physical_roots(launcher, tmp_path, monkeypatch, kind):
    root = str(tmp_path).encode()
    normal = b"worktree " + root + b"\0HEAD unit\0\0"
    variants = {
        "terminator": normal[:-1],
        "entry": b"bad\0\0",
        "relative": b"worktree relative\0\0",
        "unicode": b"worktree \xff\0\0",
        "duplicate": normal + normal,
        "field": b"worktree " + root + b"\0unknown field\0\0",
        "missing-anchor": b"worktree /owned-missing-registry-root\0\0",
    }
    monkeypatch.setattr(launcher, "_git", lambda *a, **k: variants[kind])
    with pytest.raises(launcher.SourceError):
        launcher._registered(tmp_path)


@pytest.mark.parametrize("as_list", [False, True])
def test_user_sites_are_physical_and_deduplicated(launcher, tmp_path, monkeypatch, as_list):
    monkeypatch.setattr(launcher.site, "getsitepackages", lambda: [str(tmp_path)])
    monkeypatch.setattr(launcher.site, "ENABLE_USER_SITE", True)
    monkeypatch.setattr(
        launcher.site, "getusersitepackages", lambda: [str(tmp_path)] if as_list else str(tmp_path)
    )
    assert launcher._sites() == (tmp_path,)


def test_environment_is_copied_and_routing_removed(launcher):
    env = {
        "PATH": "/private/bin",
        "BACKEND_KEY": "unit-value",
        "PYTHONPATH": "/hostile",
        "PYTHONHOME": "/hostile",
        "GIT_DIR": "/foreign",
        "GIT_CONFIG_KEY_0": "foreign",
        "GIT_CONFIG_VALUE_0": "foreign",
        "GIT_OPTIONAL_LOCKS": "1",
    }
    result = launcher.sanitized_env(env)
    assert result == {"PATH": "/private/bin", "BACKEND_KEY": "unit-value", "GIT_OPTIONAL_LOCKS": "1"}
    result["PATH"] = "changed"
    assert env["PATH"] == "/private/bin"


@pytest.mark.parametrize("env", [{}, {"PATH": 1}, {1: "path"}, []])
def test_environment_rejects_invalid_explicit_values(launcher, env):
    with pytest.raises(ValueError):
        launcher.sanitized_env(env)


def test_empty_inherited_environment_is_distinct(launcher, monkeypatch):
    monkeypatch.setattr(launcher.os, "environ", {})
    assert launcher.sanitized_env() == {}


def test_wheel_discovery_and_selection_need_no_git(launcher, tmp_path, monkeypatch):
    standard_site, root, info = wheel(tmp_path)
    use_site(launcher, monkeypatch, standard_site, standard_site / "code_forge_launcher.py")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("wheel consulted Git"))
    selected = launcher.select_source(tmp_path)
    assert selected.mode == "installed" and selected.package_root == root.resolve()
    assert selected.checkout_root is None and selected.installed.common_dir is None
    assert selected.installed.metadata_root == info.resolve()
    assert selected.declared_version == "2.9.0" and len(selected.source_sha256) == 64


def test_standard_site_aliases_are_deduplicated(launcher, tmp_path, monkeypatch):
    standard_site, _, _ = wheel(tmp_path)
    alias = tmp_path / "site-alias"
    alias.symlink_to(standard_site, target_is_directory=True)
    use_site(launcher, monkeypatch, standard_site, standard_site / "code_forge_launcher.py")
    monkeypatch.setattr(launcher.site, "getsitepackages", lambda: [str(standard_site), str(alias)])
    assert launcher.discover_install().editable_layout == "wheel"


def test_ambient_metadata_does_not_supply_installation(launcher, tmp_path, monkeypatch):
    standard_site, _, _ = wheel(tmp_path)
    empty = tmp_path / "empty-site"
    empty.mkdir()
    use_site(launcher, monkeypatch, empty, standard_site / "code_forge_launcher.py")
    monkeypatch.syspath_prepend(str(standard_site))
    with pytest.raises(launcher.SourceError):
        launcher.discover_install()


@pytest.mark.parametrize(
    "damage",
    [
        "duplicate",
        "record-missing",
        "record-malformed",
        "init-missing",
        "launcher-foreign",
        "conflict",
        "escape",
        "metadata-version",
    ],
)
def test_wheel_ownership_damage_refuses(launcher, tmp_path, monkeypatch, damage):
    standard_site, root, info = wheel(tmp_path)
    use_site(launcher, monkeypatch, standard_site, standard_site / "code_forge_launcher.py")
    if damage == "duplicate":
        record(standard_site, "code-review-forge", "9.0", [])
    elif damage == "record-missing":
        (info / "RECORD").unlink()
    elif damage == "record-malformed":
        (info / "RECORD").write_text("broken\n", encoding="utf-8")
    elif damage == "init-missing":
        (root / "__init__.py").unlink()
    elif damage == "launcher-foreign":
        monkeypatch.setattr(launcher, "__file__", str(SOURCE))
    elif damage == "conflict":
        record(standard_site, "other-package", "1.0", ["code_forge/__init__.py"])
    elif damage == "escape":
        (root / "data.txt").unlink()
        write(tmp_path / "outside.txt", "outside")
        (root / "data.txt").symlink_to(tmp_path / "outside.txt")
    else:
        with (info / "METADATA").open("a", encoding="utf-8") as stream:
            stream.write("Version: 9.0\n")
    with pytest.raises(launcher.SourceError):
        launcher.discover_install()


@pytest.mark.parametrize("claimed", ["cli.py", "data.txt"])
@pytest.mark.parametrize("operation", ["cli", "stdio"])
def test_wheel_unrecorded_foreign_claim_refuses_public_entry(tmp_path, claimed, operation):
    standard_site, root, _ = wheel(tmp_path)
    names = [str(path.relative_to(standard_site)) for path in root.iterdir() if path.name != claimed]
    record(standard_site, "code_review_forge", "2.9.0", [*names, "code_forge_launcher.py"])
    record(standard_site, "foreign_owner", "1.0", ["code_forge/" + claimed])
    result, stderr = probe(
        tmp_path,
        operation,
        SOURCE,
        standard_site,
        standard_site / "code_forge_launcher.py",
        tmp_path,
    )
    assert result["exit"] == 2 and result["origin"] is None
    assert result["business_main_called"] is False and result["started"] is False
    assert "another distribution claims Forge-owned files" in stderr


@pytest.mark.parametrize("owner", ["forge", "unrelated"])
@pytest.mark.parametrize("metadata_file", ["METADATA", "RECORD"])
@pytest.mark.parametrize("operation", ["cli", "stdio"])
def test_nonregular_distribution_metadata_refuses_public_entry(
    tmp_path, owner, metadata_file, operation
):
    standard_site, _, info = wheel(tmp_path)
    if owner == "unrelated":
        info = record(standard_site, "unrelated_package", "1.0", [])
    damaged = info / metadata_file
    damaged.unlink()
    os.mkfifo(damaged)
    result, stderr = probe(
        tmp_path,
        operation,
        SOURCE,
        standard_site,
        standard_site / "code_forge_launcher.py",
        tmp_path,
        timeout=1,
    )
    assert result["exit"] == 2 and result["origin"] is None
    assert result["business_main_called"] is False and result["started"] is False
    assert "Forge source error:" in stderr


@pytest.mark.parametrize(
    "claimed", ["cli.py", "data.txt", "launcher", "unrecorded.txt", "nested/data.txt", "cache-alias"]
)
@pytest.mark.parametrize("suffix", [".py", ".pyc"])
@pytest.mark.parametrize("operation", ["cli", "stdio"])
def test_wheel_foreign_hardlink_claim_refuses_public_entry(tmp_path, claimed, suffix, operation):
    standard_site, root, _ = wheel(tmp_path)
    if claimed == "launcher":
        owned = standard_site / "code_forge_launcher.py"
    elif claimed == "cache-alias":
        target = root / "__pycache__/owned.txt"
        write(target, "admitted resource through logical alias\n")
        owned = root / "logical-resource.txt"
        owned.symlink_to(target)
    else:
        owned = root / claimed
        if not owned.exists():
            write(owned, "unrecorded admitted resource\n")
    alias = standard_site / ("foreign-alias" + suffix)
    os.link(owned.resolve(), alias)
    assert owned.samefile(alias)
    record(standard_site, "foreign_owner", "1.0", [alias.name])
    result, stderr = probe(
        tmp_path,
        operation,
        SOURCE,
        standard_site,
        standard_site / "code_forge_launcher.py",
        tmp_path,
    )
    assert result["exit"] == 2 and result["origin"] is None
    assert result["business_main_called"] is False and result["started"] is False
    assert "another distribution claims Forge-owned files" in stderr


@pytest.mark.parametrize("present", [False, True])
@pytest.mark.parametrize("operation", ["cli", "stdio"])
def test_wheel_disposable_foreign_cache_preserves_public_entry(tmp_path, present, operation):
    standard_site, _, _ = wheel(tmp_path)
    cached = standard_site / "foreign/__pycache__/module.pyc"
    if present:
        write(cached, "unrelated disposable cache\n")
    record(standard_site, "foreign_owner", "1.0", [str(cached.relative_to(standard_site))])
    result, stderr = probe(
        tmp_path,
        operation,
        SOURCE,
        standard_site,
        standard_site / "code_forge_launcher.py",
        tmp_path,
    )
    assert result["exit"] == (7 if operation == "cli" else 0)
    assert result["business_main_called"] is (operation == "cli")
    assert result["started"] is (operation == "stdio")
    assert stderr == ""


@pytest.mark.parametrize("suffix", [".py", ".pyc"])
def test_wheel_removed_foreign_claim_snapshot_policy(launcher, tmp_path, monkeypatch, suffix):
    standard_site, _, _ = wheel(tmp_path)
    use_site(launcher, monkeypatch, standard_site, standard_site / "code_forge_launcher.py")
    path = standard_site / ("foreign/missing" + suffix)
    write(path, "foreign file before ownership enumeration\n")
    record(standard_site, "foreign_owner", "1.0", [str(path.relative_to(standard_site))])
    distributions = list(metadata.distributions(path=[str(standard_site)]))
    foreign = next(dist for dist in distributions if dist.metadata["Name"] == "foreign-owner")
    snapshot = [str(entry) for entry in foreign.files]
    path.unlink()
    original_files = launcher._distribution_files

    def snapshotted_files(dist, **kwargs):
        return snapshot if dist._path == foreign._path else original_files(dist, **kwargs)

    monkeypatch.setattr(launcher, "_distribution_files", snapshotted_files)
    if suffix == ".pyc":
        assert launcher.discover_install().editable_layout == "wheel"
    else:
        with pytest.raises(launcher.SourceError, match="installed file ownership cannot be resolved"):
            launcher.discover_install()


@pytest.mark.parametrize("damage", ["nested-metadata", "row-shape", "names", "csv", "unicode"])
def test_manifest_requires_owned_consistent_record(launcher, tmp_path, damage):
    site_root = tmp_path / "site"
    prefix = "nested/" if damage == "nested-metadata" else ""
    names = [prefix + "forge.dist-info/METADATA", prefix + "forge.dist-info/RECORD"]
    write(site_root / names[0], "Name: code-review-forge\nVersion: 2.9.0\n")
    contents = "".join(name + ",,\n" for name in names)
    if damage == "row-shape":
        contents = names[0] + ",\n"
    elif damage == "names":
        contents = "different,,\n"
    elif damage == "csv":
        contents = '"unterminated'
    write(site_root / names[1], contents)
    if damage == "unicode":
        (site_root / names[1]).write_bytes(b"\xff")
    dist = metadata.PathDistribution(site_root / (prefix + "forge.dist-info"))
    with pytest.raises(launcher.SourceError):
        launcher._manifest(dist, site_root)


@pytest.mark.parametrize("damage", ["init-not-owned", "parent-escape", "other-provider-cycle"])
def test_wheel_manifest_claims_refuse_ambiguous_ownership(launcher, tmp_path, monkeypatch, damage):
    standard_site, _, info = wheel(tmp_path)
    use_site(launcher, monkeypatch, standard_site, standard_site / "code_forge_launcher.py")
    if damage == "init-not-owned":
        lines = (info / "RECORD").read_text().splitlines(keepends=True)
        (info / "RECORD").write_text(
            "".join(line for line in lines if not line.startswith("code_forge/__init__.py,"))
        )
    elif damage == "parent-escape":
        write(standard_site / "outside.txt", "outside package owner\n")
        with (info / "RECORD").open("a") as stream:
            stream.write("code_forge/../outside.txt,,\n")
    else:
        record(standard_site, "other-package", "1.0", ["cycle"])
        (standard_site / "cycle").symlink_to("cycle")
        distributions = list(launcher.metadata.distributions(path=[str(standard_site)]))
        providers = [
            SimpleNamespace(metadata=dist.metadata, files=["cycle"], locate_file=dist.locate_file)
            if dist.metadata["Name"] == "other-package"
            else dist
            for dist in distributions
        ]
        monkeypatch.setattr(launcher.metadata, "distributions", lambda **kwargs: providers)
    with pytest.raises(launcher.SourceError):
        launcher.discover_install()


@pytest.mark.parametrize(
    "kind", ["missing", "comments", "multiple", "unicode", "broken-links", "other-links"]
)
def test_editable_artifact_variants_require_static_identity(launcher, tmp_path, kind):
    checkout = tmp_path / "checkout"
    package(checkout / "src/code_forge")
    write(checkout / "src/code_forge_launcher.py", "anchor launcher\n")
    standard_site = tmp_path / "site"
    standard_site.mkdir()
    names = [] if kind == "missing" else ["forge.pth"]
    text = "# comment\n\n" if kind == "comments" else str(checkout / "src") + "\n"
    if kind == "multiple":
        text += str(checkout) + "\n"
    elif kind in {"broken-links", "other-links"}:
        links = checkout / "links"
        links.mkdir()
        if kind == "other-links":
            package(links / "code_forge")
            write(links / "code_forge_launcher.py", "different launcher\n")
        text = str(links) + "\n"
    if names:
        write(standard_site / names[0], text)
        if kind == "unicode":
            (standard_site / names[0]).write_bytes(b"\xff")
    dist = SimpleNamespace(locate_file=lambda name: standard_site / name)
    if kind == "unicode":
        with pytest.raises(launcher.SourceError):
            launcher._editable_layout(dist, names, standard_site, checkout)
    else:
        assert launcher._editable_layout(dist, names, standard_site, checkout) == "finder"


@pytest.mark.parametrize(
    "damage", ["json", "list", "empty-url", "nonfile", "authority", "invalid-url", "foreign-launcher"]
)
def test_editable_install_identity_refuses_damage(launcher, tmp_path, monkeypatch, damage):
    standard_site, anchor, _, info = editable(tmp_path)
    use_site(launcher, monkeypatch, standard_site, anchor / "src/code_forge_launcher.py")
    urls = {
        "empty-url": "",
        "nonfile": "https://example.invalid/forge",
        "authority": "file://foreign/forge",
        "invalid-url": "file://[broken",
    }
    if damage == "json":
        write(info / "direct_url.json", "{broken")
    elif damage == "list":
        write(info / "direct_url.json", "[]")
    elif damage == "foreign-launcher":
        monkeypatch.setattr(launcher, "__file__", str(SOURCE))
    else:
        write(
            info / "direct_url.json", json.dumps({"url": urls[damage], "dir_info": {"editable": True}})
        )
    with pytest.raises(launcher.SourceError):
        launcher.discover_install()


@pytest.mark.parametrize("kind", ["project", "malformed-git"])
def test_expected_forge_marker_without_package_is_retained(launcher, tmp_path, kind):
    if kind == "project":
        write(tmp_path / "pyproject.toml", '[project]\nname="code-review-forge"\n')
    else:
        write(tmp_path / "pyproject.toml", "malformed TOML")
        (tmp_path / ".git").mkdir()
    assert launcher._has_forge_marker(tmp_path, walk_parents=False) is True


def test_digest_resources_metadata_launcher_and_cache_boundaries(launcher, tmp_path, monkeypatch):
    standard_site, root, info = wheel(tmp_path)
    use_site(launcher, monkeypatch, standard_site, standard_site / "code_forge_launcher.py")
    first = launcher.select_source(tmp_path).source_sha256
    write(root / "__pycache__/ignored.pyc", "cache")
    write(root / "ignored.pyc", "cache")
    with (standard_site / "code_forge_launcher.py").open("a", encoding="utf-8") as stream:
        stream.write("# unrelated bootstrap bytes\n")
    assert launcher.select_source(tmp_path).source_sha256 == first
    write(root / "new-resource.txt", "not in RECORD")
    second = launcher.select_source(tmp_path).source_sha256
    assert second != first
    with (info / "METADATA").open("a", encoding="utf-8") as stream:
        stream.write("Summary: changed\n")
    assert launcher.select_source(tmp_path).source_sha256 != second


def test_digest_includes_nested_resources_and_record(launcher, tmp_path, monkeypatch):
    standard_site, root, info = wheel(tmp_path)
    use_site(launcher, monkeypatch, standard_site, standard_site / "code_forge_launcher.py")
    before = launcher.select_source(tmp_path).source_sha256
    write(root / "nested/resource.txt", "nested bytes\n")
    nested = launcher.select_source(tmp_path).source_sha256
    assert nested != before
    with (info / "RECORD").open("a") as stream:
        stream.write("code_forge/nested/resource.txt,,\n")
    assert launcher.select_source(tmp_path).source_sha256 != nested


def test_digest_refuses_repeated_directory_object(launcher, tmp_path, monkeypatch):
    standard_site, root, _ = wheel(tmp_path)
    use_site(launcher, monkeypatch, standard_site, standard_site / "code_forge_launcher.py")
    installed = launcher.discover_install()
    repeated = root / "repeated"
    repeated.mkdir()
    real_stat = Path.stat
    root_stat = real_stat(root)

    def repeated_stat(path, *args, **kwargs):
        return root_stat if path == repeated else real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", repeated_stat)
    with pytest.raises(launcher.SourceError, match="directory cycle"):
        launcher._digest(installed, root, None)


def test_digest_root_identity_failure_is_explicit(launcher, tmp_path, monkeypatch):
    standard_site, root, _ = wheel(tmp_path)
    use_site(launcher, monkeypatch, standard_site, standard_site / "code_forge_launcher.py")
    installed = launcher.discover_install()
    real_stat = Path.stat

    def unavailable_stat(path, *args, **kwargs):
        if path == root:
            raise OSError("unit source root disappeared")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", unavailable_stat)
    assert unavailable_stat(standard_site) == real_stat(standard_site)
    with pytest.raises(launcher.SourceError):
        launcher._digest(installed, root, None)


@pytest.mark.parametrize(
    "kind", ["absent-parent", "missing-parent", "escaped-parent", "namespace-escape", "origin-escape"]
)
def test_finder_refuses_invalid_parent_and_namespace(launcher, tmp_path, monkeypatch, kind):
    root = tmp_path / "code_forge"
    package(root)
    finder = launcher._SelectedPackageFinder(root)
    if kind in {"namespace-escape", "origin-escape"}:
        spec = launcher.ModuleSpec("code_forge", None, is_package=True)
        if kind == "namespace-escape":
            spec.submodule_search_locations = [str(tmp_path)]
        else:
            write(tmp_path / "outside.py", "VALUE = 1\n")
            spec.origin = str(tmp_path / "outside.py")
            spec.loader = launcher.SourceFileLoader("code_forge", spec.origin)
            spec.submodule_search_locations = [str(root)]
        monkeypatch.setattr(launcher, "spec_from_file_location", lambda *a, **k: spec)
        with pytest.raises(launcher.SourceError):
            finder.find_spec("code_forge")
    else:
        if kind == "escaped-parent":
            write(tmp_path / "child.py", "VALUE = 1\n")
            parents = [str(tmp_path)]
        else:
            parents = None if kind == "absent-parent" else [str(tmp_path / "missing")]
        with pytest.raises(ModuleNotFoundError):
            finder.find_spec("code_forge.child", parents)


def test_digest_is_independent_of_host_path(launcher, tmp_path, monkeypatch):
    first = tmp_path / "first"
    second = tmp_path / "second"
    site_a, _, _ = wheel(first)
    site_b, _, _ = wheel(second)
    use_site(launcher, monkeypatch, site_a, site_a / "code_forge_launcher.py")
    a = launcher.select_source(tmp_path).source_sha256
    use_site(launcher, monkeypatch, site_b, site_b / "code_forge_launcher.py")
    assert launcher.select_source(tmp_path).source_sha256 == a


def test_digest_exact_framed_vector(launcher, tmp_path):
    root = tmp_path / "code_forge"
    write(root / "__init__.py", "__version__ = '2.9.0'\n")
    write(root / "nested/data.bin", "opaque\0resource\n")
    (root / "alias.bin").symlink_to("nested/data.bin")
    info = tmp_path / "forge.dist-info"
    write(info / "METADATA", "Name: code-review-forge\nVersion: 2.9.0\n")
    write(info / "RECORD", "owned,,\n")
    installed = launcher.InstalledSource(root, None, None, SOURCE, info, "wheel", "2.9.0")
    assert (
        launcher._digest(installed, root, None)
        == "02764fa290c89e35cbbf18b01d8aeb300438ef1edda3ca5e9c2ff6e40273e1eb"
    )


@pytest.mark.parametrize("damage", ["escape", "cycle", "directory", "fifo"])
def test_digest_rejects_unsupported_package_entries(launcher, tmp_path, monkeypatch, damage):
    standard_site, root, _ = wheel(tmp_path)
    use_site(launcher, monkeypatch, standard_site, standard_site / "code_forge_launcher.py")
    entry = root / "extra"
    if damage == "escape":
        write(tmp_path / "external", "foreign")
        entry.symlink_to(tmp_path / "external")
    elif damage == "cycle":
        entry.symlink_to(entry)
    elif damage == "directory":
        entry.symlink_to(root, target_is_directory=True)
    else:
        os.mkfifo(entry)
    with pytest.raises(launcher.SourceError):
        launcher.select_source(tmp_path)


def test_internal_package_link_changes_hash(launcher, tmp_path, monkeypatch):
    standard_site, root, _ = wheel(tmp_path)
    use_site(launcher, monkeypatch, standard_site, standard_site / "code_forge_launcher.py")
    before = launcher.select_source(tmp_path).source_sha256
    (root / "link.txt").symlink_to("data.txt")
    assert launcher.select_source(tmp_path).source_sha256 != before


@pytest.mark.parametrize("layout", ["static", "link-tree", "finder"])
def test_editable_layout_and_same_head_selection(launcher, tmp_path, monkeypatch, layout):
    standard_site, anchor, linked, _ = editable(tmp_path, layout)
    use_site(launcher, monkeypatch, standard_site, anchor / "src/code_forge_launcher.py")
    assert launcher.discover_install().editable_layout == layout
    assert launcher.select_source(anchor / "src").mode == "installed"
    if layout == "finder":
        with pytest.raises(launcher.SourceError):
            launcher.select_source(linked)
    else:
        selected = launcher.select_source(linked)
        assert selected.mode == "worktree" and selected.package_root == linked / "src/code_forge"


@pytest.mark.parametrize("damage", ["trailing", "blank", "bom", "missing", "duplicate"])
@pytest.mark.parametrize("layout", ["static", "link-tree"])
@pytest.mark.parametrize("operation", ["cli", "stdio"])
def test_editable_pth_interpreter_rules_reach_public_entries(tmp_path, damage, layout, operation):
    import site

    standard_site, anchor, linked, _ = editable(tmp_path, layout)
    pth = standard_site / "forge.pth"
    line = pth.read_text(encoding="utf-8").rstrip()
    text = {
        "trailing": line + " \t\n",
        "blank": " \t\n" + line + "\n",
        "bom": "\ufeff" + line + "\n",
        "missing": "absent-private-directory\n" + line + "\n",
        "duplicate": line + "\n" + line + "\n",
    }[damage]
    pth.write_text(text, encoding="utf-8")
    before = list(sys.path)
    try:
        site.addpackage(str(standard_site), "forge.pth", set(before))
        interpreter_admits_path = line in sys.path
    finally:
        sys.path[:] = before
    result, stderr = probe(
        tmp_path, operation, SOURCE, standard_site, anchor / "src/code_forge_launcher.py", linked
    )
    expected_exit = (7 if operation == "cli" else 0) if interpreter_admits_path else 2
    assert result["exit"] == expected_exit
    assert result["business_main_called"] is (interpreter_admits_path and operation == "cli")
    assert result["started"] is (interpreter_admits_path and operation == "stdio")
    assert ("Forge source:" in stderr) is interpreter_admits_path


@pytest.mark.parametrize("decoder", ["legacy", "locale-fallback"])
def test_editable_pth_decoder_branches(launcher, tmp_path, monkeypatch, decoder):
    standard_site = tmp_path / "site"
    checkout = tmp_path / "anchor"
    (checkout / "src").mkdir(parents=True)
    data = ("# caf\u00e9\n" + str(checkout / "src") + " \t\n").encode(
        "utf-8" if decoder == "legacy" else "latin-1"
    )
    write(standard_site / "forge.pth", "")
    (standard_site / "forge.pth").write_bytes(data)
    monkeypatch.setattr(
        launcher, "sys", SimpleNamespace(version_info=(3, 12) if decoder == "legacy" else (3, 14))
    )
    monkeypatch.setattr(launcher.locale, "getencoding", lambda: "latin-1")
    dist = SimpleNamespace(locate_file=lambda name: standard_site / name)
    assert launcher._editable_layout(dist, ["forge.pth"], standard_site, checkout) == "static"


def test_editable_percent_url_is_decoded_once(launcher, tmp_path, monkeypatch):
    standard_site, anchor, _, _ = editable(tmp_path, percent=True)
    use_site(launcher, monkeypatch, standard_site, anchor / "src/code_forge_launcher.py")
    assert launcher.discover_install().checkout_root == anchor


def test_editable_manifest_bytes_are_bound(launcher, tmp_path, monkeypatch):
    standard_site, anchor, linked, _ = editable(tmp_path)
    use_site(launcher, monkeypatch, standard_site, anchor / "src/code_forge_launcher.py")
    before = launcher.select_source(linked).source_sha256
    with (linked / "pyproject.toml").open("a") as stream:
        stream.write("# source declaration bytes changed\n")
    assert launcher.select_source(linked).source_sha256 != before


def test_dirty_alias_worktree_and_declared_version(launcher, tmp_path, monkeypatch):
    standard_site, anchor, linked, _ = editable(tmp_path)
    use_site(launcher, monkeypatch, standard_site, anchor / "src/code_forge_launcher.py")
    project = linked / "pyproject.toml"
    project.write_text(project.read_text().replace("2.9.0", "3.0.0"), encoding="utf-8")
    write(linked / "src/code_forge/new.py", "VALUE = 1\n")
    alias = tmp_path / "linked-alias"
    alias.symlink_to(linked, target_is_directory=True)
    selected = launcher.select_source(alias / "src")
    assert selected.mode == "worktree" and selected.checkout_root == linked
    assert selected.declared_version == "3.0.0"


@pytest.mark.parametrize("layout", ["static", "link-tree", "finder"])
@pytest.mark.parametrize("missing_git", [False, True], ids=["intact", "missing-git"])
@pytest.mark.parametrize("operation", ["discover", "select", "cli", "stdio"])
def test_nested_installed_worktree_requires_own_git_identity(
    launcher, tmp_path, monkeypatch, layout, missing_git, operation
):
    standard_site, anchor, installed, _ = editable(tmp_path, layout, nested=True, installed_linked=True)
    use_site(launcher, monkeypatch, standard_site, installed / "src/code_forge_launcher.py")
    assert installed in launcher._registered(installed)
    if missing_git:
        (installed / ".git").unlink()
        assert installed in launcher._registered(installed)
        raw = launcher._git(installed, "rev-parse", "--path-format=absolute", "--show-toplevel")
        assert launcher._git_path(raw, installed) == anchor
    if operation in {"cli", "stdio"}:
        result, stderr = probe(
            tmp_path,
            operation,
            SOURCE,
            standard_site,
            installed / "src/code_forge_launcher.py",
            tmp_path,
        )
        assert result["exit"] == (2 if missing_git else 7 if operation == "cli" else 0)
        assert result["business_main_called"] is (not missing_git and operation == "cli")
        assert result["started"] is (not missing_git and operation == "stdio")
        assert ("broken Git identity" in stderr) is missing_git
    elif missing_git:
        with pytest.raises(launcher.SourceError, match="broken Git identity"):
            launcher.discover_install() if operation == "discover" else launcher.select_source(tmp_path)
    else:
        selected = (
            launcher.discover_install() if operation == "discover" else launcher.select_source(tmp_path)
        )
        assert selected.checkout_root == installed
        assert selected.package_root == installed / "src/code_forge"


@pytest.mark.parametrize("layout", ["static", "link-tree", "finder"])
@pytest.mark.parametrize("operation", ["select", "cli", "stdio"])
def test_installed_git_identity_is_revalidated_before_import(
    launcher, tmp_path, monkeypatch, capsys, layout, operation
):
    standard_site, anchor, installed, _ = editable(tmp_path, layout, nested=True, installed_linked=True)
    use_site(launcher, monkeypatch, standard_site, installed / "src/code_forge_launcher.py")
    discover = launcher.discover_install

    def lose_git_identity():
        selected = discover()
        assert selected.checkout_root == installed
        (installed / ".git").unlink()
        assert installed in launcher._registered(installed)
        raw = launcher._git(installed, "rev-parse", "--path-format=absolute", "--show-toplevel")
        assert launcher._git_path(raw, installed) == anchor
        return selected

    monkeypatch.setattr(launcher, "discover_install", lose_git_identity)
    if operation == "select":
        with pytest.raises(launcher.SourceError, match="broken Git identity"):
            launcher.select_source(tmp_path)
    else:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            launcher,
            "_managed_package",
            lambda source: pytest.fail("broken installed checkout reached business import"),
        )
        entry = launcher.cli_main if operation == "cli" else launcher.stdio_main
        assert entry() == 2
        captured = capsys.readouterr()
        assert captured.out == "" and "broken Git identity" in captured.err


def test_anchor_identity_drift_is_refused(launcher, tmp_path, monkeypatch):
    standard_site, anchor, linked, _ = editable(tmp_path)
    use_site(launcher, monkeypatch, standard_site, anchor / "src/code_forge_launcher.py")
    real_git = launcher._git
    common_queries = [0]

    def drifting_git(root, *args, **kwargs):
        raw = real_git(root, *args, **kwargs)
        if root == anchor and args[-1] == "--git-common-dir":
            common_queries[0] += 1
            if common_queries[0] == 2:
                return str(tmp_path).encode() + b"\n"
        return raw

    monkeypatch.setattr(launcher, "_git", drifting_git)
    with pytest.raises(launcher.SourceError, match="changed during selection"):
        launcher.select_source(linked)
    assert common_queries[0] == 2


def test_unregistered_tree_with_same_git_directory_is_refused(launcher, tmp_path, monkeypatch):
    standard_site, anchor, _, _ = editable(tmp_path)
    use_site(launcher, monkeypatch, standard_site, anchor / "src/code_forge_launcher.py")
    unregistered = tmp_path / "unregistered"
    package(unregistered / "src/code_forge")
    write(unregistered / "pyproject.toml", (anchor / "pyproject.toml").read_text())
    write(unregistered / ".git", f"gitdir: {anchor / '.git'}\n")
    raw = launcher._git(unregistered, "rev-parse", "--path-format=absolute", "--show-toplevel")
    assert launcher._git_path(raw, unregistered) == unregistered
    with pytest.raises(launcher.SourceError, match="exactly registered"):
        launcher.select_source(unregistered)


@pytest.mark.parametrize(
    "damage", ["dependencies", "init", "project", "git-link", "foreign", "nested-forge"]
)
def test_expected_and_foreign_forge_refuse_fallback(launcher, tmp_path, monkeypatch, damage):
    standard_site, anchor, linked, _ = editable(tmp_path)
    use_site(launcher, monkeypatch, standard_site, anchor / "src/code_forge_launcher.py")
    workspace = linked
    if damage == "dependencies":
        p = linked / "pyproject.toml"
        p.write_text(
            p.read_text().replace("dependencies=[]", 'dependencies=["different"]'), encoding="utf-8"
        )
    elif damage == "init":
        (linked / "src/code_forge/__init__.py").unlink()
    elif damage == "project":
        (linked / "pyproject.toml").write_text("broken TOML", encoding="utf-8")
    elif damage == "git-link":
        (linked / ".git").write_text("gitdir: /missing-owned-fixture\n", encoding="utf-8")
    else:
        workspace = linked / "nested" if damage == "nested-forge" else tmp_path / "foreign"
        workspace.mkdir()
        git(workspace, "init", "--initial-branch=main")
        package(workspace / "src/code_forge")
    with pytest.raises(launcher.SourceError):
        launcher.select_source(workspace)


def test_nested_ordinary_repository_uses_installed(launcher, tmp_path, monkeypatch):
    standard_site, anchor, linked, _ = editable(tmp_path)
    use_site(launcher, monkeypatch, standard_site, anchor / "src/code_forge_launcher.py")
    nested = linked / "ordinary"
    nested.mkdir()
    git(nested, "init", "--initial-branch=main")
    selected = launcher.select_source(nested)
    assert selected.mode == "installed" and selected.package_root == anchor / "src/code_forge"


@pytest.mark.parametrize("layout", ["static", "link-tree", "finder"])
@pytest.mark.parametrize("subdirectory", [False, True], ids=["root", "src"])
@pytest.mark.parametrize("operation", ["select", "cli", "stdio"])
def test_nested_registered_worktree_missing_git_refuses_ancestor(
    launcher, tmp_path, monkeypatch, capsys, layout, subdirectory, operation
):
    standard_site, anchor, linked, _ = editable(tmp_path, layout, nested=True)
    use_site(launcher, monkeypatch, standard_site, anchor / "src/code_forge_launcher.py")
    assert linked in launcher._registered(anchor)
    (linked / ".git").unlink()
    workspace = linked / "src" if subdirectory else linked
    assert linked in launcher._registered(anchor)
    discovered = launcher._git(workspace, "rev-parse", "--path-format=absolute", "--show-toplevel")
    assert launcher._git_path(discovered, workspace) == anchor
    if operation == "select":
        with pytest.raises(launcher.SourceError, match="broken Git identity"):
            launcher.select_source(workspace)
    else:
        monkeypatch.chdir(workspace)
        monkeypatch.setattr(
            launcher,
            "_managed_package",
            lambda source: pytest.fail("broken registered tree reached business import"),
        )
        entry = launcher.cli_main if operation == "cli" else launcher.stdio_main
        assert entry() == 2
        captured = capsys.readouterr()
        assert captured.out == "" and "broken Git identity" in captured.err


@pytest.mark.parametrize("layout", ["static", "link-tree"])
@pytest.mark.parametrize("subdirectory", [False, True], ids=["root", "src"])
def test_nested_registered_worktree_uses_closest_registered_root(
    launcher, tmp_path, monkeypatch, layout, subdirectory
):
    standard_site, anchor, linked, _ = editable(tmp_path, layout, nested=True)
    use_site(launcher, monkeypatch, standard_site, anchor / "src/code_forge_launcher.py")
    selected = launcher.select_source(linked / "src" if subdirectory else linked)
    assert selected.mode == "worktree" and selected.checkout_root == linked
    assert selected.package_root == linked / "src/code_forge"


@pytest.mark.parametrize("missing_git", [False, True], ids=["intact", "missing-git"])
def test_ordinary_repository_inside_nested_registered_tree_keeps_fallback(
    launcher, tmp_path, monkeypatch, missing_git
):
    standard_site, anchor, linked, _ = editable(tmp_path, nested=True)
    use_site(launcher, monkeypatch, standard_site, anchor / "src/code_forge_launcher.py")
    if missing_git:
        (linked / ".git").unlink()
    ordinary = linked / "ordinary"
    ordinary.mkdir()
    git(ordinary, "init", "--initial-branch=main")
    selected = launcher.select_source(ordinary)
    assert selected.mode == "installed" and selected.package_root == anchor / "src/code_forge"


def test_ordinary_nongit_directory_uses_installed(launcher, tmp_path, monkeypatch):
    standard_site, anchor, _, _ = editable(tmp_path)
    use_site(launcher, monkeypatch, standard_site, anchor / "src/code_forge_launcher.py")
    ordinary = tmp_path / "ordinary"
    ordinary.mkdir()
    assert launcher.select_source(ordinary).mode == "installed"


def test_prepare_cli_is_resident_safe_and_copies_env(launcher, tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "discover_install", lambda: pytest.fail("resident discovered source"))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("resident queried Git"))
    env = {"PATH": "/unchanged/baseline", "PYTHONPATH": "/foreign", "GIT_WORK_TREE": "/foreign"}
    result = launcher.prepare_cli(tmp_path, env=env)
    assert result.argv_prefix == (sys.executable, "-P", str(SOURCE.resolve()), "--cli", "--")
    assert result.cwd == tmp_path and result.env == {
        "PATH": "/unchanged/baseline",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    assert env["PATH"] == "/unchanged/baseline" and "PYTHONPATH" in env


@pytest.mark.parametrize(
    "operation", ["cli", "stdio", "resources", "missing", "preloaded", "invalid-parent"]
)
def test_fresh_managed_entry_and_scoped_binding(tmp_path, operation):
    standard_site, _, _ = wheel(tmp_path)
    result, stderr = probe(
        tmp_path, operation, SOURCE, standard_site, standard_site / "code_forge_launcher.py", tmp_path
    )
    assert result["operation"] == operation
    if operation == "cli":
        assert result["exit"] == 7 and result["argv"] == ["--retained", "literal argument"]
    elif operation == "stdio":
        assert result["exit"] == 0 and result["started"] is True and stderr == ""
    elif operation == "resources":
        assert result["resource"] == "selected resource\n" and result["version"] == "2.9.0"
        assert result["foreign_lookup"] is None and result["bytecode"] is False
    else:
        assert result["refused"] is True


def test_managed_entry_rejects_version_mismatch(tmp_path):
    standard_site, root, _ = wheel(tmp_path)
    write(root / "__init__.py", '__version__ = "wrong"\n')
    result, stderr = probe(
        tmp_path, "cli", SOURCE, standard_site, standard_site / "code_forge_launcher.py", tmp_path
    )
    assert result["exit"] == 2 and "version" in stderr and result["business_main_called"] is False


@pytest.mark.parametrize("damage", ["file-type", "path-type", "spec-missing", "origin-mismatch"])
def test_managed_entry_refuses_malformed_package_origin(tmp_path, damage):
    standard_site, root, _ = wheel(tmp_path)
    changes = {
        "file-type": "__file__ = None\n",
        "path-type": "__path__ = None\n",
        "spec-missing": "__spec__ = None\n",
        "origin-mismatch": '__spec__.origin = __file__ + ".absent"\n',
    }
    with (root / "__init__.py").open("a") as stream:
        stream.write(changes[damage])
    result, stderr = probe(
        tmp_path, "cli", SOURCE, standard_site, standard_site / "code_forge_launcher.py", tmp_path
    )
    assert (
        result["exit"] == 2
        and "Forge source error:" in stderr
        and result["business_main_called"] is False
    )


@pytest.mark.parametrize("damage", ["missing-main", "string-exit", "boolean-exit"])
def test_cli_entry_refuses_invalid_entry_contract(tmp_path, damage):
    standard_site, root, _ = wheel(tmp_path)
    if damage == "missing-main":
        text = "main = None\n"
    else:
        value = "True" if damage == "boolean-exit" else '"invalid"'
        text = f"ARGV = None\ndef main():\n    return {value}\n"
    write(root / "cli.py", text)
    result, stderr = probe(
        tmp_path, "cli", SOURCE, standard_site, standard_site / "code_forge_launcher.py", tmp_path
    )
    assert result["exit"] == 2 and "Forge source error:" in stderr


def test_private_worker_retains_exact_arguments(launcher, monkeypatch):
    observed = []
    monkeypatch.setattr(launcher.sys, "argv", ["launcher", "--cli", "--", "--flag", "literal argument"])
    monkeypatch.setattr(launcher, "cli_main", lambda: observed.append(list(sys.argv)) or 7)
    assert launcher._worker_main() == 7
    assert observed == [["launcher", "--flag", "literal argument"]]


def test_private_worker_refuses_unsupported_protocol(launcher, monkeypatch, capsys):
    monkeypatch.setattr(launcher.sys, "argv", ["launcher", "--foreign"])
    monkeypatch.setattr(
        launcher, "cli_main", lambda: pytest.fail("invalid private protocol entered CLI")
    )
    assert launcher._worker_main() == 2
    assert "unsupported private" in capsys.readouterr().err


def test_module_main_refuses_unsupported_private_protocol(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [str(SOURCE), "--foreign"])
    with pytest.raises(SystemExit) as exit_result:
        runpy.run_path(str(SOURCE), run_name="__main__")
    assert exit_result.value.code == 2 and "unsupported private" in capsys.readouterr().err


def test_worktree_entry_has_stderr_only_provenance(tmp_path):
    standard_site, anchor, linked, _ = editable(tmp_path)
    result, stderr = probe(
        tmp_path, "cli", SOURCE, standard_site, anchor / "src/code_forge_launcher.py", linked
    )
    assert result["exit"] == 7 and result["argv"] == ["--retained", "literal argument"]
    assert stderr.count("Forge source:") == 1 and str(linked) in stderr


def test_worktree_package_alias_inside_checkout_uses_selected_source(tmp_path):
    standard_site, anchor, linked, _ = editable(tmp_path)
    physical = linked / "source_bundle"
    (linked / "src/code_forge").rename(physical)
    (linked / "src/code_forge").symlink_to(physical, target_is_directory=True)
    result, stderr = probe(
        tmp_path, "cli", SOURCE, standard_site, anchor / "src/code_forge_launcher.py", linked
    )
    assert result["exit"] == 7 and result["origin"] == str(physical / "__init__.py")
    assert result["argv"] == ["--retained", "literal argument"]
    assert stderr.count("Forge source:") == 1


def cached_code(source, text, mode="timestamp"):
    code = compile(text, str(source), "exec")
    if mode == "timestamp":
        info = source.stat()
        header = struct.pack("<III", 0, int(info.st_mtime), info.st_size)
    else:
        flags = 1 if mode == "unchecked-hash" else 3
        header = struct.pack("<I", flags) + importlib.util.source_hash(source.read_bytes())
    return importlib.util.MAGIC_NUMBER + header + marshal.dumps(code)


@pytest.mark.parametrize("target", ["init", "cli", "nested-init", "nested-leaf"])
@pytest.mark.parametrize("mode", ["timestamp", "unchecked-hash", "checked-hash"])
def test_managed_source_ignores_matching_cached_code(launcher, tmp_path, monkeypatch, target, mode):
    standard_site, root, _ = wheel(tmp_path)
    write(root / "nested/__init__.py", "from .leaf import VALUE\n")
    write(root / "nested/leaf.py", "VALUE = 7\n")
    write(
        root / "cli.py",
        "import sys\nfrom .nested import VALUE\nARGV = None\ndef main():\n    global ARGV\n    ARGV = list(sys.argv[1:])\n    return VALUE\n",
    )
    paths = {
        "init": root / "__init__.py",
        "cli": root / "cli.py",
        "nested-init": root / "nested/__init__.py",
        "nested-leaf": root / "nested/leaf.py",
    }
    damage = {
        "init": '__version__ = "cached-version"\n',
        "cli": "ARGV = None\ndef main():\n    return 99\n",
        "nested-init": "VALUE = 99\n",
        "nested-leaf": "VALUE = 99\n",
    }
    use_site(launcher, monkeypatch, standard_site, standard_site / "code_forge_launcher.py")
    before = launcher.select_source(tmp_path).source_sha256
    source = paths[target]
    cache = Path(importlib.util.cache_from_source(str(source)))
    cache.parent.mkdir(exist_ok=True)
    payload = cached_code(source, damage[target], mode)
    cache.write_bytes(payload)
    assert launcher.select_source(tmp_path).source_sha256 == before
    result, stderr = probe(
        tmp_path, "cli", SOURCE, standard_site, standard_site / "code_forge_launcher.py", tmp_path
    )
    assert result["exit"] == 7 and result["argv"] == ["--retained", "literal argument"]
    assert result["origin"] == str(root / "__init__.py") and stderr == ""
    assert cache.read_bytes() == payload


def test_managed_sourceless_child_refuses_excluded_code(tmp_path):
    standard_site, root, _ = wheel(tmp_path)
    write(root / "only_cached.py", "VALUE = 7\n")
    source = root / "only_cached.py"
    (root / "only_cached.pyc").write_bytes(cached_code(source, "VALUE = 99\n"))
    source.unlink()
    result, stderr = probe(
        tmp_path, "sourceless", SOURCE, standard_site, standard_site / "code_forge_launcher.py", tmp_path
    )
    assert result["refused"] is True and "value" not in result and stderr == ""


@pytest.mark.parametrize(
    "damage",
    ["record-unicode", "record-size", "record-columns", "metadata-unicode", "other-record-unicode"],
)
@pytest.mark.parametrize("operation", ["cli", "stdio"])
def test_real_distribution_read_failures_are_public_refusals(tmp_path, damage, operation):
    standard_site, _, info = wheel(tmp_path)
    if damage == "record-unicode":
        (info / "RECORD").write_bytes(b"\xff")
    elif damage == "record-size":
        with (info / "RECORD").open("a") as stream:
            stream.write("code_forge/cli.py,,not-an-integer\n")
    elif damage == "record-columns":
        with (info / "RECORD").open("a") as stream:
            stream.write("code_forge/cli.py,,,,\n")
    elif damage == "metadata-unicode":
        (info / "METADATA").write_bytes(b"\xff")
    else:
        other = record(standard_site, "other_package", "1.0", [])
        (other / "RECORD").write_bytes(b"\xff")
    result, stderr = probe(
        tmp_path, operation, SOURCE, standard_site, standard_site / "code_forge_launcher.py", tmp_path
    )
    assert result["exit"] == 2 and result["origin"] is None
    assert result["business_main_called"] is False and result["started"] is False
    assert stderr.count("Forge source error:") == 1


def test_real_manifest_property_preserves_cause(launcher, tmp_path):
    standard_site, _, info = wheel(tmp_path)
    (info / "RECORD").write_bytes(b"\xff")
    dist = metadata.PathDistribution(info)
    with pytest.raises(launcher.SourceError) as refused:
        launcher._manifest(dist, standard_site)
    assert isinstance(refused.value.__cause__, UnicodeDecodeError)


@pytest.mark.parametrize("alias", ["package", "launcher", "both"])
def test_link_tree_physical_aliases_allow_registered_worktree(launcher, tmp_path, monkeypatch, alias):
    standard_site, anchor, linked, _ = editable(tmp_path, "link-tree")
    if alias in {"package", "both"}:
        physical = anchor / "source_bundle"
        (anchor / "src/code_forge").rename(physical)
        (anchor / "src/code_forge").symlink_to(physical, target_is_directory=True)
    if alias in {"launcher", "both"}:
        physical = anchor / "entry_bundle.py"
        (anchor / "src/code_forge_launcher.py").rename(physical)
        (anchor / "src/code_forge_launcher.py").symlink_to(physical)
    use_site(launcher, monkeypatch, standard_site, anchor / "src/code_forge_launcher.py")
    assert launcher.discover_install().editable_layout == "link-tree"
    assert launcher.select_source(linked).mode == "worktree"
    result, stderr = probe(
        tmp_path, "cli", SOURCE, standard_site, anchor / "src/code_forge_launcher.py", linked
    )
    assert result["exit"] == 7 and result["origin"] == str(linked / "src/code_forge/__init__.py")
    assert stderr.count("Forge source:") == 1


@pytest.mark.parametrize("alias", ["package", "launcher", "both"])
@pytest.mark.parametrize("operation", ["cli", "stdio"])
def test_strict_editable_hardlinks_reach_public_entries(tmp_path, alias, operation):
    standard_site, anchor, linked, _ = editable(tmp_path, "link-tree")
    links = anchor / "build/links"
    if alias in {"package", "both"}:
        (links / "code_forge").unlink()
        (links / "code_forge").mkdir()
        for source in (anchor / "src/code_forge").iterdir():
            os.link(source, links / "code_forge" / source.name)
    if alias in {"launcher", "both"}:
        (links / "code_forge_launcher.py").unlink()
        os.link(anchor / "src/code_forge_launcher.py", links / "code_forge_launcher.py")
    result, stderr = probe(
        tmp_path, operation, SOURCE, standard_site, links / "code_forge_launcher.py", linked
    )
    assert result["exit"] == (7 if operation == "cli" else 0)
    assert result["business_main_called"] is (operation == "cli")
    assert result["started"] is (operation == "stdio")
    assert result["origin"] == str(linked / "src/code_forge/__init__.py")
    assert stderr.count("Forge source:") == 1


@pytest.mark.parametrize("damage", ["equal-bytes-copy", "outside-hardlink"])
@pytest.mark.parametrize("operation", ["cli", "stdio"])
def test_strict_editable_launcher_aliases_require_checkout_identity(tmp_path, damage, operation):
    standard_site, anchor, linked, _ = editable(tmp_path, "link-tree")
    source = anchor / "src/code_forge_launcher.py"
    if damage == "equal-bytes-copy":
        entry = anchor / "build/links/code_forge_launcher.py"
        entry.unlink()
        entry.write_bytes(source.read_bytes())
        assert not entry.samefile(source)
    else:
        entry = tmp_path / "outside_launcher.py"
        os.link(source, entry)
        assert entry.samefile(source)
    result, stderr = probe(tmp_path, operation, SOURCE, standard_site, entry, linked)
    assert result["exit"] == 2 and not result["business_main_called"] and not result["started"]
    assert "Forge source:" not in stderr


def test_same_file_identity_errors_are_source_refusals(launcher, tmp_path, monkeypatch):
    first = tmp_path / "source.py"
    first.write_bytes(b"source bytes\n")
    second = tmp_path / "linked.py"
    os.link(first, second)
    assert launcher._same_file(first, second)

    def missing_identity(*args):
        raise OSError("private identity disappeared")

    monkeypatch.setattr(Path, "samefile", missing_identity)
    with pytest.raises(launcher.SourceError, match="identity cannot be compared"):
        launcher._same_file(first, second)


def test_extension_finder_keeps_bound_binary_loader_without_execution(launcher, tmp_path):
    root = tmp_path / "package"
    package(root)
    binary = root / ("native" + EXTENSION_SUFFIXES[0])
    binary.write_bytes(b"private binary ownership fixture")
    finder = launcher._SelectedPackageFinder(root)
    selected = finder.find_spec("code_forge.native", [str(root)])
    assert isinstance(selected.loader, ExtensionFileLoader)
    assert Path(selected.origin).resolve() == binary
    selected.loader.exec_module = lambda _module: pytest.fail("native binary fixture must never execute")


def test_extension_bytes_are_included_in_source_binding(launcher, tmp_path, monkeypatch):
    standard_site, root, _ = wheel(tmp_path)
    use_site(launcher, monkeypatch, standard_site, standard_site / "code_forge_launcher.py")
    binary = root / ("native" + EXTENSION_SUFFIXES[0])
    binary.write_bytes(b"private binary ownership fixture")
    before = launcher.select_source(tmp_path).source_sha256
    binary.write_bytes(b"changed private binary ownership fixture")
    assert launcher.select_source(tmp_path).source_sha256 != before


def test_source_loader_reads_only_selected_python_bytes(launcher, tmp_path):
    root = tmp_path / "package"
    package(root)
    selected = launcher._SelectedPackageFinder(root).find_spec("code_forge.cli", [str(root)])
    original = selected.loader.get_data
    reads = []

    def read(path):
        reads.append(path)
        return original(path)

    selected.loader.get_data = read
    compiled = selected.loader.get_code("code_forge.cli")
    assert compiled.co_filename == str(root / "cli.py")
    assert reads == [str(root / "cli.py")]
    plain = launcher._SelectedSourceLoader("code_forge", str(root / "__init__.py"))
    assert plain.get_resource_reader("code_forge").files() == root


def test_source_loader_missing_file_is_source_refusal(launcher, tmp_path):
    root = tmp_path / "package"
    package(root)
    selected = launcher._SelectedPackageFinder(root).find_spec("code_forge.cli", [str(root)])
    (root / "cli.py").unlink()
    with pytest.raises(launcher.SourceError) as refused:
        selected.loader.get_code("code_forge.cli")
    assert isinstance(refused.value.__cause__, FileNotFoundError)


@pytest.mark.parametrize("damage", ["unsupported-origin", "unsupported-no-origin", "extension-escape"])
def test_selected_loader_refuses_unbound_execution(launcher, tmp_path, monkeypatch, damage):
    root = tmp_path / "package"
    package(root)
    finder = launcher._SelectedPackageFinder(root)
    if damage == "extension-escape":
        outside = tmp_path / ("outside" + EXTENSION_SUFFIXES[0])
        outside.write_bytes(b"outside private binary fixture")
        (root / ("child" + EXTENSION_SUFFIXES[0])).symlink_to(outside)
    else:
        spec = launcher.ModuleSpec("code_forge.child", object())
        if damage == "unsupported-origin":
            spec.origin = str(root / "cli.py")
        monkeypatch.setattr(launcher.PathFinder, "find_spec", lambda *args: spec)
    with pytest.raises(launcher.SourceError):
        finder.find_spec("code_forge.child", [str(root)])


def test_managed_namespace_uses_only_selected_paths_and_resources(tmp_path):
    standard_site, root, _ = wheel(tmp_path)
    write(root / "namespace/child.py", "VALUE = 7\n")
    write(root / "namespace/data.txt", "selected namespace resource\n")
    result, stderr = probe(
        tmp_path, "namespace", SOURCE, standard_site, standard_site / "code_forge_launcher.py", tmp_path
    )
    assert result["paths"] == [str(root / "namespace")] and result["value"] == 7
    assert result["resource"] == "selected namespace resource\n" and stderr == ""


def test_excluded_cache_directory_cannot_be_imported_as_namespace(tmp_path):
    standard_site, root, _ = wheel(tmp_path)
    write(root / "__pycache__/hidden.py", "VALUE = 99\n")
    result, stderr = probe(
        tmp_path,
        "cache-directory",
        SOURCE,
        standard_site,
        standard_site / "code_forge_launcher.py",
        tmp_path,
    )
    assert result["refused"] is True and "value" not in result and stderr == ""


def test_included_file_alias_can_target_internal_cache_directory(launcher, tmp_path, monkeypatch):
    standard_site, root, _ = wheel(tmp_path)
    physical = root / "__pycache__/selected_cli.py"
    physical.parent.mkdir()
    (root / "cli.py").rename(physical)
    (root / "cli.py").symlink_to("__pycache__/selected_cli.py")
    use_site(launcher, monkeypatch, standard_site, standard_site / "code_forge_launcher.py")
    before = launcher.select_source(tmp_path).source_sha256
    result, stderr = probe(
        tmp_path, "cli", SOURCE, standard_site, standard_site / "code_forge_launcher.py", tmp_path
    )
    assert result["exit"] == 7 and stderr == ""
    physical.write_text(physical.read_text().replace("return 7", "return 8"))
    assert launcher.select_source(tmp_path).source_sha256 != before


@pytest.mark.parametrize("directory", ["__pycache__", "included/__pycache__", "cached.pyc", "included"])
def test_redirected_parent_import_respects_digest_directories(
    launcher, tmp_path, monkeypatch, directory
):
    standard_site, root, _ = wheel(tmp_path)
    write(
        root / "redirected/__init__.py",
        "from pathlib import Path\n"
        f"__path__ = [str(Path(__file__).resolve().parents[1] / {directory!r})]\n",
    )
    hidden = root / directory / "hidden.py"
    write(hidden, "VALUE = 99\n")
    use_site(launcher, monkeypatch, standard_site, standard_site / "code_forge_launcher.py")
    before = launcher.select_source(tmp_path).source_sha256
    write(hidden, "VALUE = 100\n")
    excluded = any(
        part == "__pycache__" or Path(part).suffix == ".pyc" for part in Path(directory).parts
    )
    assert (launcher.select_source(tmp_path).source_sha256 == before) is excluded
    result, stderr = probe(
        tmp_path,
        "redirected-parent",
        SOURCE,
        standard_site,
        standard_site / "code_forge_launcher.py",
        tmp_path,
    )
    assert result["refused"] is excluded and stderr == ""
    if excluded:
        assert "value" not in result
    else:
        assert result["value"] == 100


@pytest.mark.parametrize("layout", ["static", "link-tree"])
def test_editable_initializer_alias_preserves_logical_resources(tmp_path, layout):
    standard_site, anchor, linked, _ = editable(tmp_path, layout=layout)
    root = linked / "src/code_forge"
    implementation = root / "impl/__init__.py"
    implementation.parent.mkdir()
    (root / "__init__.py").rename(implementation)
    (root / "__init__.py").symlink_to("impl/__init__.py")
    write(root / "gate.schema.json", "selected schema\n")
    write(root / "skills/example.md", "selected skill\n")
    result, stderr = probe(
        tmp_path, "alias-resources", SOURCE, standard_site, anchor / "src/code_forge_launcher.py", linked
    )
    assert result["resource_root"] == str(root)
    assert result["paths"] == [str(root)]
    assert result["resource"] == "selected resource\n"
    assert result["schema"] == "selected schema\n" and result["skill"] == "selected skill\n"
    assert result["code_filename"] == str(implementation)
    assert stderr.count("Forge source:") == 1


@pytest.mark.parametrize("operation", ["alias-resources", "cli", "stdio"])
def test_wheel_initializer_alias_preserves_package_root(tmp_path, operation):
    standard_site, root, _ = wheel(tmp_path)
    implementation = root / "impl/__init__.py"
    implementation.parent.mkdir()
    (root / "__init__.py").rename(implementation)
    (root / "__init__.py").symlink_to("impl/__init__.py")
    write(root / "gate.schema.json", "selected schema\n")
    write(root / "skills/example.md", "selected skill\n")
    result, stderr = probe(
        tmp_path,
        operation,
        SOURCE,
        standard_site,
        standard_site / "code_forge_launcher.py",
        tmp_path,
    )
    assert stderr == "" and result["refused"] is False
    if operation == "alias-resources":
        assert result["resource_root"] == str(root) and result["paths"] == [str(root)]
        assert result["resource"] == "selected resource\n"
        assert result["schema"] == "selected schema\n" and result["skill"] == "selected skill\n"
        assert result["code_filename"] == str(implementation)
    else:
        assert result["origin"] == str(implementation)
        assert result["exit"] == (7 if operation == "cli" else 0)
        assert result["business_main_called"] is (operation == "cli")
        assert result["started"] is (operation == "stdio")
        if operation == "cli":
            assert result["argv"] == ["--retained", "literal argument"]


def test_nested_initializer_alias_preserves_logical_resources(tmp_path):
    standard_site, root, _ = wheel(tmp_path)
    nested = root / "nested"
    write(nested / "impl/__init__.py", "VALUE = 1\n")
    (nested / "__init__.py").symlink_to("impl/__init__.py")
    write(nested / "data.txt", "selected nested resource\n")
    write(nested / "gate.schema.json", "selected schema\n")
    write(nested / "skills/example.md", "selected skill\n")
    result, stderr = probe(
        tmp_path,
        "nested-resources",
        SOURCE,
        standard_site,
        standard_site / "code_forge_launcher.py",
        tmp_path,
    )
    assert result["resource_root"] == str(nested) and result["paths"] == [str(nested)]
    assert result["resource"] == "selected nested resource\n"
    assert result["schema"] == "selected schema\n" and result["skill"] == "selected skill\n"
    assert result["code_filename"] == str(nested / "impl/__init__.py") and stderr == ""


def cache_record_fixture(tmp_path, cache):
    standard_site, root, info = wheel(tmp_path)
    names = [
        "code_forge/__pycache__/cli." + sys.implementation.cache_tag + ".pyc",
        "__pycache__/code_forge_launcher." + sys.implementation.cache_tag + ".pyc",
    ]
    for name in names:
        path = standard_site / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"disposable bytecode must never execute")
        if cache == "cleared":
            path.unlink()
    with (info / "RECORD").open("a", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerows((name, "", "") for name in names)
    return standard_site, root, info, names


@pytest.mark.parametrize(
    ("operation", "cache"),
    [("cli", "present"), ("cli", "cleared"), ("stdio", "present"), ("stdio", "cleared")],
    ids=["cli-present", "cli-cleared", "stdio-present", "stdio-cleared"],
)
def test_wheel_cache_rows_are_disposable_at_public_entries(tmp_path, operation, cache):
    standard_site, root, _, names = cache_record_fixture(tmp_path, cache)
    assert all((standard_site / name).exists() == (cache == "present") for name in names)
    result, stderr = probe(
        tmp_path, operation, SOURCE, standard_site, standard_site / "code_forge_launcher.py", tmp_path
    )
    assert result["exit"] == (7 if operation == "cli" else 0), (result, stderr)
    assert result["origin"] == str(root / "__init__.py")
    assert result["business_main_called"] if operation == "cli" else result["started"]
    assert "Forge source error:" not in stderr


@pytest.mark.parametrize(
    "damage", ["missing-source", "missing-resource", "duplicate-cache", "provider-mismatch"]
)
def test_cache_projection_preserves_required_ownership_refusals(launcher, tmp_path, monkeypatch, damage):
    standard_site, root, info, names = cache_record_fixture(tmp_path, "cleared")
    use_site(launcher, monkeypatch, standard_site, standard_site / "code_forge_launcher.py")
    if damage == "missing-source":
        (root / "cli.py").unlink()
    elif damage == "missing-resource":
        (root / "data.txt").unlink()
    elif damage == "duplicate-cache":
        with (info / "RECORD").open("a", encoding="utf-8", newline="") as stream:
            csv.writer(stream).writerow((names[0], "", ""))
    else:
        dist = metadata.PathDistribution(info)
        files = [entry for entry in dist.files if str(entry) != "code_forge/data.txt"]
        provider = SimpleNamespace(metadata=dist.metadata, files=files, locate_file=dist.locate_file)
        monkeypatch.setattr(launcher.metadata, "distributions", lambda **kwargs: [provider])
    outcome = {"refused": False}
    try:
        launcher.discover_install()
    except launcher.SourceError as exc:
        outcome.update(refused=True, error=str(exc))
    assert outcome["refused"], outcome


@pytest.mark.parametrize(
    ("caller_locale", "language"),
    [("C", "en"), ("fr_FR.UTF-8", "fr"), ("de_DE.UTF-8", "de")],
    ids=["C", "fr", "de"],
)
def test_ordinary_fallback_git_queries_use_c_locale(
    launcher, tmp_path, monkeypatch, caller_locale, language
):
    standard_site, anchor, _, _ = editable(tmp_path)
    use_site(launcher, monkeypatch, standard_site, anchor / "src/code_forge_launcher.py")
    ordinary = tmp_path / "ordinary"
    ordinary.mkdir()
    supplied = {"PATH": os.environ["PATH"], "LC_ALL": caller_locale, "LANGUAGE": language}
    original_env = dict(supplied)
    ambient = dict(os.environ)
    observed = []
    real_run = subprocess.run

    def record_query(argv, **kwargs):
        result = real_run(argv, **kwargs)
        if kwargs.get("cwd") == ordinary:
            observed.append(
                {
                    "LC_ALL": kwargs["env"].get("LC_ALL"),
                    "LANGUAGE": kwargs["env"].get("LANGUAGE"),
                    "exit": result.returncode,
                    "stdout": result.stdout.decode(),
                    "stderr": result.stderr.decode(),
                }
            )
        return result

    monkeypatch.setattr(subprocess, "run", record_query)
    outcome = {"mode": None}
    selected = launcher.select_source(ordinary, env=supplied)
    outcome.update(mode=selected.mode, package=str(selected.package_root))
    write(tmp_path / "locale-observation.json", json.dumps({"outcome": outcome, "queries": observed}))
    assert outcome["mode"] == "installed", (outcome, observed)
    assert outcome["package"] == str(anchor / "src/code_forge")
    assert len(observed) == 1 and observed[0]["exit"] == 128 and observed[0]["stdout"] == ""
    assert observed[0]["LC_ALL"] == "C" and "not a git repository" in observed[0]["stderr"].lower()
    assert supplied == original_env and dict(os.environ) == ambient


@pytest.mark.parametrize("caller_locale", ["fr_FR.UTF-8", "de_DE.UTF-8"], ids=["fr", "de"])
def test_translated_locale_keeps_broken_registered_tree_refusal(
    launcher, tmp_path, monkeypatch, caller_locale
):
    standard_site, anchor, linked, _ = editable(tmp_path)
    use_site(launcher, monkeypatch, standard_site, anchor / "src/code_forge_launcher.py")
    (linked / ".git").write_text("gitdir: /missing-owned-fixture\n", encoding="utf-8")
    with pytest.raises(launcher.SourceError):
        launcher.select_source(linked, env={"PATH": os.environ["PATH"], "LC_ALL": caller_locale})


@pytest.mark.parametrize("ordinary", [False, True], ids=["required", "ordinary"])
def test_git_128_other_than_nonrepository_still_refuses(launcher, tmp_path, monkeypatch, ordinary):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=128, stdout=b"", stderr=b"fatal: invalid gitfile format\n"
        ),
    )
    with pytest.raises(launcher.SourceError):
        launcher._git(
            tmp_path, "rev-parse", "--path-format=absolute", "--show-toplevel", ordinary=ordinary
        )


def test_prepare_cli_preserves_real_virtualenv_prefix_and_dependencies(launcher, tmp_path, monkeypatch):
    venv = tmp_path / "private-venv"
    executable = venv / "bin/python"
    executable.parent.mkdir(parents=True)
    host = Path(sys.executable).resolve()
    executable.symlink_to(host)
    write(venv / "pyvenv.cfg", f"home = {host.parent}\ninclude-system-site-packages = false\n")
    standard_site = (
        venv / sys.platlibdir / f"python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    )
    write(standard_site / "fixture_dependency.py", 'VALUE = "venv-only"\n')
    write(
        standard_site / "code_review_forge-2.9.0.dist-info/METADATA",
        "Name: code-review-forge\nVersion: 2.9.0\n",
    )
    monkeypatch.setattr(launcher.sys, "executable", str(executable))
    invocation = launcher.prepare_cli(tmp_path)
    observations = {}
    for label, binary in [("resident", str(executable)), ("prepared", invocation.argv_prefix[0])]:
        result = subprocess.run(
            [binary, "-P", str(Path(__file__).resolve()), "--unit-venv-probe"],
            cwd=tmp_path,
            env=invocation.env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        write(tmp_path / (label + ".stdout"), result.stdout)
        write(tmp_path / (label + ".stderr"), result.stderr)
        observations[label] = json.loads(result.stdout)
    assert observations["resident"]["prefix"] == str(venv)
    assert (
        observations["resident"]["dependency"] == "venv-only"
        and observations["resident"]["forge_count"] == 1
    )
    assert observations["prepared"] == observations["resident"]
    assert invocation.argv_prefix[0] == str(executable)


@pytest.mark.parametrize("damage", ["relative", "missing", "directory"])
def test_prepare_cli_refuses_invalid_interpreter_identity(launcher, tmp_path, monkeypatch, damage):
    values = {"relative": "python", "missing": str(tmp_path / "absent"), "directory": str(tmp_path)}
    if damage == "relative":
        (tmp_path / "python").symlink_to(Path(sys.executable).resolve())
        monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(launcher.sys, "executable", values[damage])
    with pytest.raises(launcher.SourceError):
        launcher.prepare_cli(tmp_path)


@pytest.fixture
def private_interpreter_state(monkeypatch):
    """Exercise reporting even when ordinary collection already loaded Forge."""
    monkeypatch.setitem(sys.modules, "code_forge.helper_existing", object())
    for name in tuple(sys.modules):
        if name == "code_forge" or name.startswith("code_forge."):
            monkeypatch.delitem(sys.modules, name)


@pytest.mark.parametrize("dependency", [None, "private dependency"])
def test_private_interpreter_helper_reports_dependency(
    monkeypatch, capsys, dependency, private_interpreter_state
):
    import builtins
    import site

    original_import = builtins.__import__

    def private_import(name, *args, **kwargs):
        if name == "fixture_dependency":
            if dependency is None:
                raise ModuleNotFoundError(name)
            return SimpleNamespace(VALUE=dependency)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", private_import)
    monkeypatch.setattr(site, "getsitepackages", lambda: [])
    _unit_venv_probe()
    result = json.loads(capsys.readouterr().out)
    assert result["dependency"] == dependency and result["sites"] == []
    assert result["forge_count"] == 0 and result["executable"] == sys.executable


def test_private_interpreter_helper_refuses_preloaded_package(monkeypatch):
    monkeypatch.setitem(sys.modules, "code_forge.helper_probe", object())
    with pytest.raises(RuntimeError, match="refuses preloaded Forge"):
        _unit_venv_probe()


@pytest.mark.parametrize("arguments", [["--unit-venv-probe"], [], ["--invalid-probe"]])
def test_private_helper_entry_dispatch(monkeypatch, capsys, arguments, private_interpreter_state):
    import site

    path = str(Path(__file__).resolve())
    monkeypatch.setattr(sys, "argv", [path, *arguments])
    monkeypatch.setattr(site, "getsitepackages", lambda: [])
    monkeypatch.setitem(sys.modules, "fixture_dependency", SimpleNamespace(VALUE="entry dependency"))
    with pytest.raises(SystemExit) as raised:
        runpy.run_path(path, run_name="__main__")
    if arguments == ["--unit-venv-probe"]:
        assert raised.value.code == 0
        assert json.loads(capsys.readouterr().out)["dependency"] == "entry dependency"
    else:
        assert raised.value.code == "unsupported unit probe invocation"
        assert capsys.readouterr().out == ""


def test_stdio_parent_identity_precedes_launcher_imports(tmp_path):
    standard_site, root, _ = wheel(tmp_path)
    result, _ = probe(
        tmp_path,
        "stdio",
        SOURCE,
        standard_site,
        standard_site / "code_forge_launcher.py",
        tmp_path,
        parent_transition=True,
    )
    assert result["exit"] == 0 and result["started"]
    assert result["loaded_parent_pair"] == result["original_parent_pair"]


@pytest.mark.parametrize("forked", [False, True])
def test_early_stdio_identity_preserves_actual_parent_guard(tmp_path, monkeypatch, forked):
    import ast
    import ctypes
    import ctypes.util

    package_state = SimpleNamespace(STARTUP_PID=456, STARTUP_PPID=123)
    calls = []

    class ParentExit(Exception):
        pass

    def parent_exit(code):
        assert code == 1
        raise ParentExit()

    fake_os = SimpleNamespace(
        getpid=lambda: 987 if forked else 456, getppid=lambda: 789, _exit=parent_exit
    )
    monkeypatch.setitem(sys.modules, "code_forge", package_state)
    monkeypatch.setattr(ctypes.util, "find_library", lambda name: "owned-libc-fixture")
    monkeypatch.setattr(
        ctypes, "CDLL", lambda *a, **k: SimpleNamespace(prctl=lambda *a: calls.append(a) or 0)
    )
    path = SOURCE.parent / "code_forge" / "mcp_server.py"
    tree = ast.parse(path.read_bytes())
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_install_pdeathsig"
    )
    namespace = dict(
        sys=SimpleNamespace(platform="linux"),
        os=fake_os,
        signal=SimpleNamespace(SIGTERM=15),
        log=SimpleNamespace(warning=lambda *a: None),
    )
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)  # noqa: S102
    if forked:
        namespace["_install_pdeathsig"]()
    else:
        with pytest.raises(ParentExit):
            namespace["_install_pdeathsig"]()
    assert calls == [(1, 15, 0, 0, 0)]


def test_stdio_parent_identity_precedes_source_selection(launcher, monkeypatch):
    parent = [123]
    package_state = SimpleNamespace(STARTUP_PID=456, STARTUP_PPID=789)

    def select(workspace):
        parent[0] = 789
        return object()

    def main():
        assert package_state.STARTUP_PID == 456
        assert package_state.STARTUP_PPID == 123
        assert package_state.STARTUP_PPID != parent[0]

    monkeypatch.setattr(launcher, "_STARTUP_PID", 456)
    monkeypatch.setattr(launcher, "_STARTUP_PPID", 123)
    monkeypatch.setattr(launcher, "os", SimpleNamespace(getpid=lambda: 456, getppid=lambda: parent[0]))
    monkeypatch.setattr(launcher, "select_source", select)
    monkeypatch.setattr(launcher, "_managed_package", lambda source: package_state)
    monkeypatch.setattr(launcher.importlib, "import_module", lambda name: SimpleNamespace(main=main))
    assert launcher.stdio_main() == 0


@pytest.fixture
def private_probe_platform(monkeypatch, tmp_path):
    """Exercise the helper's contracts without installing a business-package hook."""
    hooks = []
    probe_sys = SimpleNamespace(modules={}, meta_path=[], addaudithook=hooks.append)
    state = SimpleNamespace(code_filename=str(tmp_path / "fixture.py"), import_error=False)

    def private_import(name, *args, **kwargs):
        for hook in hooks:
            hook("exec", (compile("VALUE = 23", state.code_filename, "exec"),))
        if state.import_error:
            raise ModuleNotFoundError(name)
        return SimpleNamespace(VALUE=23)

    probe_importlib = SimpleNamespace(import_module=private_import)

    class Finder:
        pass

    finder = Finder()
    module = SimpleNamespace(
        site=SimpleNamespace(),
        SourceError=ValueError,
        select_source=lambda workspace: workspace,
        _SelectedPackageFinder=Finder,
        _managed_package=lambda selected: probe_sys.meta_path.insert(0, finder),
    )
    monkeypatch.setitem(globals(), "sys", probe_sys)
    monkeypatch.setitem(globals(), "importlib", probe_importlib)
    monkeypatch.setitem(globals(), "load_launcher", lambda: module)
    monkeypatch.setattr(subprocess, "run", subprocess.run)
    monkeypatch.setattr(os, "chdir", lambda workspace: None)
    monkeypatch.delenv("FORGE_UNIT_AUDITOR", raising=False)
    request = tmp_path / "request.json"

    def invoke(operation):
        write(
            request,
            json.dumps(
                {
                    "site": str(tmp_path / "site"),
                    "launcher": str(tmp_path / "launcher.py"),
                    "workspace": str(tmp_path),
                    "operation": operation,
                }
            ),
        )
        _probe_main(str(request))

    return SimpleNamespace(
        invoke=invoke, hooks=hooks, state=state, sys=probe_sys, importlib=probe_importlib
    )


def test_private_probe_refuses_preloaded_business_package(private_probe_platform):
    probe = private_probe_platform
    probe.sys.modules["code_forge"] = object()
    with pytest.raises(RuntimeError, match="requires a fresh business-package process"):
        probe.invoke("sourceless")
    assert probe.hooks == []


@pytest.mark.parametrize("operation", ["sourceless", "cache-directory"])
def test_private_probe_records_unexpected_import_success(private_probe_platform, capsys, operation):
    probe = private_probe_platform
    probe.invoke(operation)
    result = json.loads(capsys.readouterr().out)
    assert result == {"operation": operation, "refused": False, "value": 23}
    assert len(probe.hooks) == 1 and probe.hooks[0].__cantrace__ is True


@pytest.mark.parametrize("filename", ["<dynamic business>", "outside"])
def test_private_probe_refuses_external_execution(private_probe_platform, tmp_path, filename):
    probe = private_probe_platform
    probe.state.code_filename = filename if filename.startswith("<") else str(tmp_path.parent / "bad.py")
    with pytest.raises(RuntimeError, match="refused business execution outside private fixtures"):
        probe.invoke("sourceless")
    # A failed business import must unwind the counter: unrelated execution is allowed afterward.
    probe.hooks[0]("exec", (compile("VALUE = 1", "<unrelated>", "exec"),))


def test_private_probe_import_error_unwinds_execution_guard(private_probe_platform):
    probe = private_probe_platform
    probe.state.import_error = True
    with pytest.raises(ModuleNotFoundError, match="code_forge.only_cached"):
        probe.invoke("sourceless")
    probe.hooks[0]("exec", (compile("VALUE = 1", "<unrelated>", "exec"),))


def test_private_probe_boundary_reports_unmanaged_import(private_probe_platform, monkeypatch, capsys):
    import builtins

    probe = private_probe_platform
    original_import = builtins.__import__

    def refused_import(name, *args, **kwargs):
        if name == "code_forge.absent":
            return probe.sys.meta_path[-1].find_spec(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refused_import)
    assert builtins.__import__("json") is json
    probe.invoke("missing")
    assert json.loads(capsys.readouterr().out)["unit_boundary_refused"] is True
    assert probe.sys.meta_path[-1].find_spec("unrelated") is None


def test_private_probe_refuses_unknown_operation(private_probe_platform):
    with pytest.raises(ValueError, match="unsupported unit probe"):
        private_probe_platform.invoke("unknown")


def _probe_main(request_file):
    context = json.loads(Path(request_file).read_text(encoding="utf-8"))
    if "FORGE_UNIT_AUDITOR" in os.environ:
        audit_spec = importlib.util.spec_from_file_location(
            "_forge_unit_auditor", os.environ["FORGE_UNIT_AUDITOR"]
        )
        auditor = importlib.util.module_from_spec(audit_spec)
        audit_spec.loader.exec_module(auditor)
        auditor.install()
    if any(name == "code_forge" or name.startswith("code_forge.") for name in sys.modules):
        raise RuntimeError("unit probe requires a fresh business-package process")

    class UnitBoundaryError(ModuleNotFoundError):
        pass

    class UnitBoundary:
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "code_forge" or fullname.startswith("code_forge."):
                raise UnitBoundaryError(
                    "unit boundary refused a business import outside the selected finder"
                )
            return None

    sys.meta_path.insert(0, UnitBoundary())
    fixture_root = Path(request_file).resolve().parent
    active_business_imports = [0]
    real_import_module = importlib.import_module

    def fixture_execution(event, arguments):
        if event == "exec" and active_business_imports[0]:
            spelling = arguments[0].co_filename
            if spelling.startswith("<") or not Path(spelling).resolve().is_relative_to(fixture_root):
                raise RuntimeError("unit probe refused business execution outside private fixtures")

    def fixture_import(name, *args, **kwargs):
        business = name == "code_forge" or name.startswith("code_forge.")
        if business:
            active_business_imports[0] += 1
        try:
            return real_import_module(name, *args, **kwargs)
        finally:
            if business:
                active_business_imports[0] -= 1

    fixture_execution.__cantrace__ = True
    sys.addaudithook(fixture_execution)
    importlib.import_module = fixture_import
    if context.get("parent_transition"):
        module, original_parent_pair = load_launcher_with_parent_transition()
    else:
        module = load_launcher()
    module.site.getsitepackages = lambda: [context["site"]]
    module.site.ENABLE_USER_SITE = False
    module.__file__ = context["launcher"]
    if context.get("empty_environment"):
        os.environ.clear()
    os.chdir(context["workspace"])
    operation = context["operation"]
    result = {"operation": operation, "refused": False}
    if context.get("empty_environment"):
        result["inherited_environment"] = dict(os.environ)
    if operation in {"cli", "stdio"}:
        sys.argv = ["code-forge", "--retained", "literal argument"]
        surface = windows_metadata_surface(module) if context.get("windows_metadata") else nullcontext()
        with surface:
            result["exit"] = module.cli_main() if operation == "cli" else module.stdio_main()
        cli = sys.modules.get("code_forge.cli")
        result["business_main_called"] = cli is not None and getattr(cli, "ARGV", None) is not None
        if operation == "cli" and result["business_main_called"]:
            result["argv"] = cli.ARGV
        server = sys.modules.get("code_forge.mcp_server")
        result["started"] = server is not None and server.STARTED
        loaded = sys.modules.get("code_forge")
        if context.get("parent_transition"):
            result["original_parent_pair"] = original_parent_pair
            result["loaded_parent_pair"] = [loaded.STARTUP_PID, loaded.STARTUP_PPID]
        origin = getattr(loaded, "__file__", None)
        result["origin"] = str(Path(origin).resolve()) if isinstance(origin, str) else None
    else:
        selected = module.select_source(Path.cwd())
        if operation == "preloaded":
            sys.modules["code_forge.existing"] = object()
            try:
                module.bind_source(selected)
            except module.SourceError:
                result["refused"] = True
        else:
            module._managed_package(selected)
            subprocess.run = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("per-module Git query"))
            finder = next(
                item for item in sys.meta_path if isinstance(item, module._SelectedPackageFinder)
            )
            if operation == "resources":
                result.update(
                    resource=importlib.resources.files("code_forge").joinpath("data.txt").read_text(),
                    version=sys.modules["code_forge"].__version__,
                    foreign_lookup=finder.find_spec("unrelated"),
                    bytecode=not sys.dont_write_bytecode,
                )
            elif operation == "missing":
                try:
                    __import__("code_forge.absent")
                except ModuleNotFoundError as exc:
                    if isinstance(exc, UnitBoundaryError):
                        result["unit_boundary_refused"] = True
                    else:
                        result["refused"] = True
            elif operation == "invalid-parent":
                try:
                    finder.find_spec("code_forge.child", [str(Path.cwd())])
                except ModuleNotFoundError:
                    result["refused"] = True
            elif operation == "sourceless":
                try:
                    imported = importlib.import_module("code_forge.only_cached")
                    result["value"] = imported.VALUE
                except module.SourceError:
                    result["refused"] = True
            elif operation == "namespace":
                namespace = importlib.import_module("code_forge.namespace")
                child = importlib.import_module("code_forge.namespace.child")
                result.update(
                    paths=list(namespace.__path__),
                    value=child.VALUE,
                    resource=importlib.resources.files(namespace).joinpath("data.txt").read_text(),
                )
            elif operation == "cache-directory":
                try:
                    imported = importlib.import_module("code_forge.__pycache__.hidden")
                    result["value"] = imported.VALUE
                except module.SourceError:
                    result["refused"] = True
            elif operation == "redirected-parent":
                try:
                    imported = importlib.import_module("code_forge.redirected.hidden")
                    result["value"] = imported.VALUE
                except module.SourceError:
                    result["refused"] = True
            elif operation in {"alias-resources", "nested-resources"}:
                name = "code_forge" if operation == "alias-resources" else "code_forge.nested"
                loaded = importlib.import_module(name)
                resources = importlib.resources.files(loaded)

                def read_resource(relative):
                    path = resources.joinpath(relative)
                    return path.read_text() if path.is_file() else None

                result.update(
                    resource_root=str(resources),
                    paths=list(loaded.__path__),
                    resource=read_resource("data.txt"),
                    schema=read_resource("gate.schema.json"),
                    skill=read_resource("skills/example.md"),
                    code_filename=loaded.__loader__.get_code(name).co_filename,
                )
            else:
                raise ValueError("unsupported unit probe")
    print(json.dumps(result))


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--unit-probe":
        raise SystemExit("unsupported unit probe invocation")
    _probe_main(sys.argv[2])


@pytest.mark.parametrize(
    "layout,selected",
    [
        ("static", "anchor"),
        ("link-tree", "anchor"),
        ("finder", "anchor"),
        ("static", "linked"),
        ("link-tree", "linked"),
    ],
)
@pytest.mark.parametrize("claimed", ["cli.py", "data.txt", "launcher", "hardlink"])
@pytest.mark.parametrize("operation", ["cli", "stdio"])
def test_editable_competing_owner_refuses_public_entry(tmp_path, layout, selected, claimed, operation):
    standard_site, anchor, linked, _ = editable(tmp_path, layout)
    checkout = anchor if selected == "anchor" else linked
    if claimed == "launcher":
        owned = checkout / "src/code_forge_launcher.py"
    else:
        owned = checkout / "src/code_forge" / ("cli.py" if claimed == "hardlink" else claimed)
    if claimed == "hardlink":
        alias = standard_site / "foreign-owned.pyc"
        os.link(owned, alias)
        assert alias.samefile(owned)
        ownership = alias.name
    else:
        ownership = os.path.relpath(owned, standard_site)
    record(standard_site, "foreign_owner", "1.0", [ownership])
    result, stderr = probe(
        tmp_path, operation, SOURCE, standard_site, anchor / "src/code_forge_launcher.py", checkout
    )
    assert result["exit"] == 2 and result["origin"] is None
    assert result["business_main_called"] is False and result["started"] is False
    assert "another distribution claims Forge-owned files" in stderr


@pytest.mark.parametrize("selected", ["anchor", "linked"])
@pytest.mark.parametrize("operation", ["cli", "stdio"])
def test_editable_unrelated_owner_preserves_public_entry(tmp_path, selected, operation):
    standard_site, anchor, linked, _ = editable(tmp_path)
    checkout = anchor if selected == "anchor" else linked
    owned = checkout / "src/code_forge/cli.py"
    unrelated = standard_site / "unrelated.py"
    unrelated.write_bytes(owned.read_bytes())
    assert not unrelated.samefile(owned)
    record(standard_site, "foreign_owner", "1.0", [unrelated.name])
    result, stderr = probe(
        tmp_path, operation, SOURCE, standard_site, anchor / "src/code_forge_launcher.py", checkout
    )
    assert result["exit"] == (7 if operation == "cli" else 0)
    assert result["business_main_called"] is (operation == "cli")
    assert result["started"] is (operation == "stdio")
    assert "another distribution claims Forge-owned files" not in stderr


@pytest.mark.parametrize("source_env", ["inherited", "routing-only"])
@pytest.mark.parametrize("selected", ["anchor", "linked", "ordinary"])
def test_empty_environment_git_forwarding(launcher, tmp_path, monkeypatch, source_env, selected):
    standard_site, anchor, linked, _ = editable(tmp_path)
    use_site(launcher, monkeypatch, standard_site, anchor / "src/code_forge_launcher.py")
    ordinary = tmp_path / "ordinary"
    ordinary.mkdir()
    workspace = {"anchor": anchor, "linked": linked, "ordinary": ordinary}[selected]
    supplied = None if source_env == "inherited" else {"GIT_DIR": "/foreign", "PYTHONPATH": "/foreign"}
    original = None if supplied is None else dict(supplied)
    real_run = subprocess.run
    queries = []

    def record_query(argv, **kwargs):
        queries.append((argv, dict(kwargs["env"])))
        return real_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", record_query)
    monkeypatch.setattr(launcher.os, "environ", {})
    result = launcher.select_source(workspace, env=supplied)
    assert result.mode == ("worktree" if selected == "linked" else "installed")
    assert result.package_root == (linked if selected == "linked" else anchor) / "src/code_forge"
    assert len(queries) >= 7
    assert any(argv[1:3] == ["worktree", "list"] for argv, _ in queries)
    assert any("--git-common-dir" in argv for argv, _ in queries)
    assert any("--show-toplevel" in argv for argv, _ in queries)
    assert all(env == {"GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"} for _, env in queries)
    assert dict(launcher.os.environ) == {} and supplied == original


@pytest.mark.parametrize("selected", ["anchor", "linked", "ordinary"])
@pytest.mark.parametrize("operation", ["cli", "stdio"])
def test_empty_inherited_environment_keeps_editable_public_entry(tmp_path, selected, operation):
    standard_site, anchor, linked, _ = editable(tmp_path)
    ordinary = tmp_path / "ordinary"
    ordinary.mkdir()
    workspace = {"anchor": anchor, "linked": linked, "ordinary": ordinary}[selected]
    result, stderr = probe(
        tmp_path,
        operation,
        SOURCE,
        standard_site,
        anchor / "src/code_forge_launcher.py",
        workspace,
        empty_environment=True,
    )
    assert result["inherited_environment"] == {}
    assert result["exit"] == (7 if operation == "cli" else 0)
    assert result["origin"] == str(
        (linked if selected == "linked" else anchor) / "src/code_forge/__init__.py"
    )
    assert result["business_main_called"] is (operation == "cli")
    assert result["started"] is (operation == "stdio")
    assert "Forge source error" not in stderr


@pytest.mark.parametrize("operation", ["select", "prepare"])
@pytest.mark.parametrize("env", [{}, {"PATH": 1}, {1: "path"}, []])
def test_explicit_invalid_environment_keeps_public_boundary(
    launcher, tmp_path, monkeypatch, operation, env
):
    monkeypatch.setattr(
        launcher, "discover_install", lambda: pytest.fail("invalid env reached discovery")
    )
    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: pytest.fail("invalid env reached Git")
    )
    entry = launcher.select_source if operation == "select" else launcher.prepare_cli
    with pytest.raises(ValueError):
        entry(tmp_path, env=env)


def containment_ownership_fixture(tmp_path, *, claims=1):
    standard_site = tmp_path / "site"
    root = standard_site / "code_forge"
    package(root)
    selected_launcher = standard_site / "code_forge_launcher.py"
    write(selected_launcher, "selected launcher bytes\n")
    info = record(standard_site, "code_review_forge", "2.9.0", [])
    paths = []
    for number in range(claims):
        path = standard_site / "unrelated" / f"claim{number}.py"
        write(path, "unrelated source bytes\n")
        paths.append(path)
    foreign = record(
        standard_site, "foreign_owner", "1.0", [str(p.relative_to(standard_site)) for p in paths]
    )
    distributions = [
        (metadata.PathDistribution(info), standard_site),
        (metadata.PathDistribution(foreign), standard_site),
    ]
    return root, selected_launcher, info, distributions, paths


@pytest.mark.skipif(os.name != "posix", reason="native POSIX containment optimization")
def test_ownership_containment_avoids_ancestors_without_snapshot(launcher, tmp_path, monkeypatch):
    root, selected_launcher, info, distributions, claims = containment_ownership_fixture(
        tmp_path, claims=20
    )
    original_parents = Path.parents.fget
    original_parts = Path.parts.fget
    original_resolve, original_stat, original_samefile = Path.resolve, Path.stat, Path.samefile
    counts = {path: {"resolve": 0, "stat": 0, "samefile": 0} for path in claims}
    ancestor_reads, package_parts = [], []

    def ancestors(path):
        if path in counts:
            ancestor_reads.append(str(path))
        return original_parents(path)

    def parts(path):
        if path == root and sys._getframe(1).f_code is launcher._competing_ownership.__code__:
            package_parts.append(str(path))
        return original_parts(path)

    def resolved(path, *args, **kwargs):
        if path in counts and sys._getframe(1).f_code is launcher._competing_ownership.__code__:
            assert kwargs == {"strict": True}
            counts[path]["resolve"] += 1
        return original_resolve(path, *args, **kwargs)

    def inspected(path, *args, **kwargs):
        if path in counts and sys._getframe(1).f_code in (
            launcher._competing_ownership.__code__,
            original_samefile.__code__,
        ):
            counts[path]["stat"] += 1
        return original_stat(path, *args, **kwargs)

    def compared(path, other):
        if path in counts and sys._getframe(1).f_code is launcher._competing_ownership.__code__:
            assert other == selected_launcher
            counts[path]["samefile"] += 1
        return original_samefile(path, other)

    monkeypatch.setattr(Path, "parents", property(ancestors))
    monkeypatch.setattr(Path, "parts", property(parts))
    monkeypatch.setattr(Path, "resolve", resolved)
    monkeypatch.setattr(Path, "stat", inspected)
    monkeypatch.setattr(Path, "samefile", compared)
    # Calibrate the allocation observer against the real pathlib property.
    assert tuple(claims[0].parents) == tuple(original_parents(claims[0]))
    assert ancestor_reads == [str(claims[0])]
    ancestor_reads.clear()
    launcher._competing_ownership(root, selected_launcher, info, distributions)
    assert all(count == {"resolve": 1, "stat": 2, "samefile": 1} for count in counts.values())
    assert ancestor_reads == []
    assert package_parts == [str(root)]


@pytest.mark.parametrize("changed_path", ["launcher", "claimant"])
@pytest.mark.parametrize("interrupt", [False, True])
def test_ownership_transient_alias_is_still_refused(
    launcher, tmp_path, monkeypatch, changed_path, interrupt
):
    root, selected_launcher, info, distributions, claims = containment_ownership_fixture(tmp_path)
    selected = selected_launcher if changed_path == "launcher" else claims[0]
    target = claims[0] if changed_path == "launcher" else selected_launcher
    saved = tmp_path / "original-object"
    os.link(selected, saved)
    identity = selected.stat()
    original_files = launcher._distribution_files
    original_stat, original_samefile = Path.stat, Path.samefile
    state = {"armed": False, "changed": False, "restored": False, "compared": False}

    def restore():
        replacement = selected.with_name("restore-object")
        os.link(saved, replacement)
        os.replace(replacement, selected)
        state["restored"] = True

    def files(dist, **kwargs):
        result = original_files(dist, **kwargs)
        if dist._path == distributions[1][0]._path:
            state["armed"] = True
        return result

    def inspected(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if state["armed"] and path == claims[0] and not state["changed"]:
            replacement = selected.with_name("transient-object")
            os.link(target, replacement)
            os.replace(replacement, selected)
            state["changed"] = True
            if interrupt:
                raise OSError("owned alias witness interrupted")
        return result

    def compared(path, other):
        try:
            state["compared"] = True
            return original_samefile(path, other)
        finally:
            if state["changed"] and not state["restored"]:
                restore()

    monkeypatch.setattr(launcher, "_distribution_files", files)
    monkeypatch.setattr(Path, "stat", inspected)
    monkeypatch.setattr(Path, "samefile", compared)
    try:
        expected = "installed file ownership" if interrupt else "another distribution claims"
        with pytest.raises(launcher.SourceError, match=expected) as raised:
            launcher._competing_ownership(root, selected_launcher, info, distributions)
    finally:
        if state["changed"] and not state["restored"]:
            restore()
    assert state == {"armed": True, "changed": True, "restored": True, "compared": not interrupt}
    if interrupt:
        assert isinstance(raised.value.__cause__, OSError)
        assert str(raised.value.__cause__) == "owned alias witness interrupted"
    assert selected.samefile(saved)
    current = selected.stat()
    assert (identity.st_dev, identity.st_ino) == (current.st_dev, current.st_ino)


@pytest.mark.parametrize(
    ("claimed", "owner", "precomputed", "fallback"),
    [
        (Path("/pkg"), Path("/pkg"), True, False),
        (Path("/pkg/a"), Path("/pkg"), True, False),
        (Path("/pkg2/a"), Path("/pkg"), True, False),
        (Path("/pkg-long/a"), Path("/pkg"), True, False),
        (Path("/Pkg/a"), Path("/pkg"), True, False),
        (Path("/pkg/a"), Path("/"), True, False),
        (Path("//pkg/a"), Path("/pkg"), True, False),
        (Path("//pkg/a"), Path("//pkg"), True, False),
        (Path("/pkg/../a"), Path("/pkg"), True, False),
        (Path("pkg/a"), Path("/pkg"), True, True),
        (Path("pkg/a"), Path("pkg"), False, True),
        (Path("/pkg/a"), Path("."), False, True),
        (PurePosixPath("/pkg/a"), Path("/pkg"), True, True),
        (PureWindowsPath("C:/PKG/a"), PureWindowsPath("c:/pkg"), False, True),
        (PureWindowsPath("D:/pkg/a"), PureWindowsPath("C:/pkg"), False, True),
        (PureWindowsPath("//HOST/share/pkg/a"), PureWindowsPath("//host/SHARE/pkg"), False, True),
        (Path("/pkg/a"), "/pkg", False, True),
        (Path("/pkg/a"), None, False, True),
        (Path("/pkg/a"), 7, False, True),
        (Path("/pkg/a"), b"/pkg", False, True),
    ],
)
def test_claimed_containment_preserves_values_errors_and_fallbacks(
    launcher, monkeypatch, claimed, owner, precomputed, fallback
):
    components = owner.parts if precomputed else None
    fallback = fallback or os.name == "nt"
    try:
        expected = claimed.is_relative_to(owner)
    except TypeError as exc:
        expected = type(exc), str(exc)
    original = Mock(wraps=claimed.is_relative_to)
    monkeypatch.setattr(type(claimed), "is_relative_to", original)
    if isinstance(expected, tuple):
        error, message = expected
        with pytest.raises(error) as raised:
            launcher._claimed_within_package(claimed, owner, components)
        assert str(raised.value) == message
    else:
        assert launcher._claimed_within_package(claimed, owner, components) is expected
    if fallback:
        original.assert_called_once_with(owner)
    else:
        original.assert_not_called()


def test_claimed_containment_keeps_custom_path_failure(launcher):
    class CustomPath(type(Path())):
        def is_relative_to(self, other):
            raise RuntimeError("custom path comparison")

    claimed = CustomPath("/pkg/a")
    with pytest.raises(RuntimeError, match="custom path comparison"):
        launcher._claimed_within_package(claimed, Path("/pkg"), Path("/pkg").parts)


def test_ownership_keeps_custom_package_parts_unobserved(launcher, tmp_path):
    root, selected_launcher, info, distributions, _claims = containment_ownership_fixture(tmp_path)

    class OwnerPath(type(root)):
        @property
        def parts(self):
            raise RuntimeError("custom components must remain unobserved")

    custom = OwnerPath(root)
    with pytest.raises(RuntimeError, match="custom components must remain unobserved"):
        _ = custom.parts
    launcher._competing_ownership(custom, selected_launcher, info, distributions)


def test_ownership_relative_empty_package_keeps_original_containment(launcher, tmp_path, monkeypatch):
    _root, selected_launcher, info, distributions, _claims = containment_ownership_fixture(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    launcher._competing_ownership(Path("."), selected_launcher, info, distributions)
