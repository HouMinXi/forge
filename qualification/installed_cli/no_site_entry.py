"""Explicit controlled no-site console execution; not ordinary console startup."""

import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import types

FIXED_ARGS = [
    "review",
    "--outlet",
    "inline",
    "--canary",
    "--audit-strict",
    "--backend",
    "fixture",
    "--allow-main",
    "--quiet",
]


def require(value):
    if not value:
        raise ValueError("isolated no-site binding rejected")


def read(path, limit=1024 * 1024):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= limit)
        chunks, size = [], 0
        while True:
            chunk = os.read(fd, min(65536, limit + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            require(size <= limit)
        raw = b"".join(chunks)
        after = os.fstat(fd)
        require(len(raw) == before.st_size)
        for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode"):
            require(getattr(before, field) == getattr(after, field) == getattr(os.lstat(path), field))
        return raw
    finally:
        os.close(fd)


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def prepare(binding, cache, args):
    require(sys.implementation.name == "cpython" and sys.version_info[:2] == (3, 14))
    require(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode)
    require(
        binding.get("backend") == "stock-bwrap" and binding.get("startup_mode") == "isolated-no-site"
    )
    require(args == FIXED_ARGS)
    spec = binding["startup_admission"]
    require(spec["startup_mode"] == "isolated-no-site")
    require(spec["no_site_system_roots"] == binding["target"]["stdlib_paths"])
    require(sys.pycache_prefix == cache and Path(cache).is_absolute())
    require(Path(cache).is_dir() and not any(Path(cache).iterdir()))
    require(sys.executable == binding["target_python"])
    require(
        sys.prefix == binding["target"]["prefix"] == str(Path(binding["target_python"]).parent.parent)
    )
    require(sys.path == binding["target"]["stdlib_paths"])
    require(
        hashlib.sha256(read(Path(sys.executable).resolve(), 16 * 1024 * 1024)).hexdigest()
        == binding["target"]["sha256"]
    )
    worker_path = Path(__file__).with_name("capture_worker.py")
    worker_source = read(worker_path)
    require(hashlib.sha256(worker_source).hexdigest() == binding["capture_hashes"]["capture_worker.py"])
    worker = types.ModuleType("_no_site_admission")
    worker.__file__ = str(worker_path)
    exec(compile(worker_source, str(worker_path), "exec", dont_inherit=True), worker.__dict__)  # noqa: S102 - exact bound helper bytes
    worker.cache_snapshot(cache)
    require(worker.admit_startup(spec) == spec["observed"])
    launcher = binding["launcher"]
    console = read(launcher, 65536)
    require(hashlib.sha256(console).hexdigest() == binding["installed_provenance"]["launcher_sha256"])
    # -S prevents .pth/sitecustomize processing. Append one verified site directly;
    # never call site.main/addsitedir or expose the cwd/console bin to imports.
    sys.path.append(spec["site_packages"])
    fd = os.environ.pop("FORGE_CLI_DIAGNOSTIC_FD", None)
    if binding["mode"] == "diagnostic":
        require(fd is not None and fd.isascii() and fd.isdecimal())
        descriptor = int(fd)
        os.set_inheritable(descriptor, False)
        probe_path = Path(__file__).with_name("diagnostic_probe.py")
        raw = read(probe_path)
        require(hashlib.sha256(raw).hexdigest() == binding["capture_hashes"]["diagnostic_probe.py"])
        probe = types.ModuleType("forge_cli_diagnostic_probe")
        probe.__file__ = str(probe_path)
        exec(compile(raw, str(probe_path), "exec", dont_inherit=True), probe.__dict__)  # noqa: S102 - explicit bound test instrumentation, no startup hook
        probe.install(descriptor)
    else:
        require(binding["mode"] == "baseline" and fd is None)
    return launcher, console


def execute(binding, cache, args):
    launcher, console = prepare(binding, cache, args)
    sys.argv = [launcher, *args]
    module = types.ModuleType("__main__")
    module.__file__ = launcher
    module.__package__ = None
    sys.modules["__main__"] = module
    exec(compile(console, launcher, "exec", dont_inherit=True), module.__dict__)  # noqa: S102 - original verified console bytes; no second path read


def command(binding, binding_path, cache, argv, source):
    require(argv == [binding["launcher"], *FIXED_ARGS])
    require(hashlib.sha256(source).hexdigest() == binding["capture_hashes"]["no_site_entry.py"])
    text = "__file__ = " + repr(str(Path(__file__))) + "\n" + source.decode("utf-8")
    return [
        binding["target_python"],
        "-I",
        "-S",
        "-B",
        "-X",
        "pycache_prefix=" + str(cache),
        "-c",
        text,
        str(binding_path),
        str(cache),
        *FIXED_ARGS,
    ]


if __name__ == "__main__":
    require(len(sys.argv) == 3 + len(FIXED_ARGS))
    value = json.loads(read(sys.argv[1]), object_pairs_hook=unique)
    execute(value, sys.argv[2], sys.argv[3:])
