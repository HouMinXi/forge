"""Temporary read-only system startup observation, never target qualification."""

import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import types

LIMIT = 20000


def initial():
    return dict(
        schema="forge-startup-probe-v1",
        required_runtime="CPYTHON_314",
        runtime="UNSUPPORTED",
        system_candidates="UNAVAILABLE",
        system_admission="NOT_RUN",
        synthetic_empty="NOT_RUN",
        synthetic_hook="NOT_RUN",
        probe_status="UNSUPPORTED",
        historical_cause="NOT_ASSESSED",
        raw="NOT_PUBLISHED",
    )


def entries(path):
    with os.scandir(path) as iterator:
        names = []
        for item in iterator:
            if len(names) >= LIMIT:
                raise ValueError("entry bound")
            names.append(item.name)
    return names


def candidate_presence(roots, suffixes):
    """Names/stat only: never read, import or execute candidate contents."""
    source = cache = False
    for root in roots:
        path = Path(root)
        if not path.exists() and path.suffix == ".zip":
            continue
        if not path.is_dir() or path.is_symlink():
            raise ValueError("unsupported root")
        names = entries(path)
        for module in ("sitecustomize", "usercustomize"):
            source |= any(module + suffix in names for suffix in ("", ".py", ".pyc", *suffixes))
        cached = path / "__pycache__"
        if cached.exists() or cached.is_symlink():
            if not cached.is_dir() or cached.is_symlink():
                raise ValueError("unsupported cache")
            cache |= any(
                name.startswith(("sitecustomize.", "usercustomize.")) for name in entries(cached)
            )
    return (
        "SOURCE_AND_CACHE" if source and cache else "SOURCE" if source else "CACHE" if cache else "NONE"
    )


def load_validator():
    path = Path(__file__).resolve().parents[1] / "qualification/installed_cli/capture_worker.py"
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 1024 * 1024:
            raise ValueError("unsupported validator")
        raw = os.read(fd, 1024 * 1024 + 1)
        after = os.fstat(fd)

        def fingerprint(row):
            return (
                row.st_dev,
                row.st_ino,
                row.st_size,
                row.st_mtime_ns,
                row.st_ctime_ns,
                row.st_mode,
            )

        if (
            len(raw) != before.st_size
            or fingerprint(before) != fingerprint(after)
            or fingerprint(after) != fingerprint(path.lstat())
        ):
            raise ValueError("changed validator")
    finally:
        os.close(fd)
    module = types.ModuleType("_temporary_startup_validator")
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec", dont_inherit=True), module.__dict__)  # noqa: S102 - exact checked-out validator, never candidate source
    return module


def admission(validator, spec):
    try:
        validator.admit_startup(spec)
    except ValueError:
        return "REJECTED"
    except (OSError, TypeError, KeyError):
        return "UNAVAILABLE"
    return "ACCEPTED"


def observe(validator, roots, suffixes):
    value = initial()
    value.update(runtime="MATCHED", probe_status="UNAVAILABLE")
    if not 1 <= len(roots) <= 13 or any(not Path(p).is_absolute() for p in roots):
        return value
    try:
        value["system_candidates"] = candidate_presence(roots, suffixes)
    except (OSError, ValueError):
        return value
    with tempfile.TemporaryDirectory(prefix="forge-startup-probe-") as temporary:
        base = Path(temporary)
        site, binary, empty = (base / name for name in ("site", "bin", "stdlib"))
        for path in (site, binary, empty):
            path.mkdir(mode=0o700)
        raw = b"# synthetic launcher; never imported\n"
        (site / "code_forge_launcher.py").write_bytes(raw)
        config = base / "pyvenv.cfg"
        config.write_bytes(b"include-system-site-packages = false\n")
        common = dict(
            venv_config_path=str(config),
            venv_config_sha256=hashlib.sha256(config.read_bytes()).hexdigest(),
            policy="private_empty_bytecode_cache",
            distutils_mode="stdlib",
            extension_suffixes=suffixes,
            site_packages=str(site),
            launcher_source_sha256=hashlib.sha256(raw).hexdigest(),
        )
        system = common | {"roots": [str(binary), *roots, str(site)]}
        synthetic = common | {"roots": [str(binary), str(empty), str(site)]}
        value["system_admission"] = admission(validator, system)
        value["synthetic_empty"] = admission(validator, synthetic)
        (empty / "sitecustomize.py").write_bytes(b"# fixed inert synthetic candidate; never imported\n")
        value["synthetic_hook"] = admission(validator, synthetic)
        if (
            value["system_admission"] in {"ACCEPTED", "REJECTED"}
            and value["synthetic_empty"] == "ACCEPTED"
            and value["synthetic_hook"] == "REJECTED"
        ):
            value["probe_status"] = "COMPLETE"
    return value


def main():
    value = initial()
    try:
        if (
            sys.version_info[:2] != (3, 14)
            or sys.implementation.name != "cpython"
            or not sys.flags.isolated
            or not sys.flags.no_site
            or not sys.dont_write_bytecode
        ):
            return value, 78
        import _imp

        value = observe(load_validator(), list(sys.path), _imp.extension_suffixes())
        return value, 0 if value["probe_status"] == "COMPLETE" else 73
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        value.update(runtime="MATCHED", probe_status="UNAVAILABLE")
        return value, 73


if __name__ == "__main__":
    try:
        result, code = main()
    except KeyboardInterrupt:
        result, code = initial(), 130
        result["probe_status"] = "UNAVAILABLE"
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    raise SystemExit(code)
