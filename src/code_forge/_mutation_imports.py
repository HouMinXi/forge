# SPDX-License-Identifier: Apache-2.0
"""Named installed imports for an isolated, genuinely selected mutation owner."""

from __future__ import annotations

import ast
import csv
import email
import hashlib
import importlib.abc
import importlib.machinery
import importlib.metadata
import importlib.util
import io
import os
import re
import stat
import sys
from pathlib import Path


def _pin_file(path: Path) -> tuple[dict, bytes]:
    path = path.absolute()
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        first = os.fstat(descriptor)
        if not stat.S_ISREG(first.st_mode):
            raise ValueError("mutation import origin is not a regular file")
        blocks = []
        while block := os.read(descriptor, 1024 * 1024):
            blocks.append(block)
        raw = b"".join(blocks)
        last, named = os.fstat(descriptor), path.lstat()

        def identity(item):
            return (
                item.st_dev,
                item.st_ino,
                item.st_mode,
                item.st_size,
                item.st_mtime_ns,
                item.st_ctime_ns,
            )

        if identity(first) != identity(last) or identity(last) != identity(named):
            raise ValueError("mutation import origin changed while reading")
        return dict(
            path=str(path),
            dev=last.st_dev,
            ino=last.st_ino,
            fullmode=last.st_mode,
            sha256=hashlib.sha256(raw).hexdigest(),
        ), raw
    finally:
        os.close(descriptor)


def _normal_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _distribution(name: str) -> tuple[dict, dict, list[str]]:
    distribution = importlib.metadata.distribution(name)
    if not isinstance(distribution._path, Path):
        raise TypeError("mutation distribution must use physical metadata")
    root = Path(distribution.locate_file("")).resolve()
    metadata_path = Path(distribution._path) / "METADATA"
    if not metadata_path.is_relative_to(root):
        raise ValueError("mutation distribution metadata has a foreign origin")
    metadata, raw = _pin_file(metadata_path)
    parsed = email.message_from_bytes(raw)
    if _normal_name(parsed.get("Name", "")) != _normal_name(name) or not parsed.get("Version"):
        raise ValueError("mutation distribution metadata name/version mismatch")
    record, raw_record = _pin_file(Path(distribution._path) / "RECORD")
    modules = {}
    for entry in csv.reader(io.StringIO(raw_record.decode("utf-8"))):
        if not entry:
            continue
        parts = Path(entry[0]).parts
        if not parts or ".." in parts:
            continue
        leaf = parts[-1]
        kind = None
        if leaf.endswith(".py"):
            package = leaf == "__init__.py"
            tail = parts[:-1] if package else (*parts[:-1], leaf[:-3])
            kind = "package" if package else "source"
        else:
            for suffix in importlib.machinery.EXTENSION_SUFFIXES:
                if leaf.endswith(suffix):
                    tail = (*parts[:-1], leaf[: -len(suffix)])
                    kind = "extension"
                    break
        if kind is None or not tail or not all(part.isidentifier() for part in tail):
            continue
        path = Path(distribution.locate_file(entry[0])).absolute()
        if not path.is_relative_to(root):
            raise ValueError("mutation dependency has a foreign origin")
        row, _ = _pin_file(path)
        modules[".".join(tail)] = dict(row, kind=kind, distribution=_normal_name(name))
    return (
        dict(root=str(root), metadata=metadata, record=record, version=parsed["Version"]),
        modules,
        parsed.get_all("Requires-Dist", []),
    )


def _requirements_api():
    """Load only pinned installed packaging, without trusting a shadow package."""
    _, origins, _ = _distribution("packaging")
    for name, module in tuple(sys.modules.items()):
        if name == "packaging" or name.startswith("packaging."):
            row = origins.get(name)
            if row is None or getattr(module, "__file__", None) != row["path"]:
                raise ValueError("foreign parent packaging module")
            pin, _ = _pin_file(Path(row["path"]))
            if any(pin[key] != row[key] for key in pin):
                raise ValueError("parent packaging origin changed")

    class Source(importlib.machinery.SourceFileLoader):
        def get_code(self, fullname):
            pin, raw = _pin_file(Path(origins[fullname]["path"]))
            if any(pin[key] != origins[fullname][key] for key in pin):
                raise ValueError("parent packaging origin changed")
            return compile(raw, self.path, "exec", dont_inherit=True)

    class Finder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname != "packaging" and not fullname.startswith("packaging."):
                return None
            row = origins.get(fullname)
            if row is None or row["kind"] == "extension":
                raise ModuleNotFoundError("unlisted parent packaging module")
            return importlib.util.spec_from_file_location(
                fullname,
                row["path"],
                loader=Source(fullname, row["path"]),
                submodule_search_locations=[str(Path(row["path"]).parent)]
                if row["kind"] == "package"
                else None,
            )

    finder = Finder()
    sys.meta_path.insert(0, finder)
    try:
        from packaging.requirements import Requirement
    finally:
        sys.meta_path.remove(finder)
    return Requirement


def prepare_owner(path: str, module_name: str) -> dict:
    """Pin actual owner bytes; ordinary owners do not load optional packages."""
    owner, raw = _pin_file(Path(path))
    authority = dict(
        owner=owner,
        module_name=module_name,
        selected_mutant=os.environ.get("MUTANT_UNDER_TEST", ""),
        distributions={},
        modules={},
    )
    imports_mutmut = any(
        isinstance(node, ast.ImportFrom)
        and node.module
        and node.module.partition(".")[0] == "mutmut"
        or isinstance(node, ast.Import)
        and any(alias.name.partition(".")[0] == "mutmut" for alias in node.names)
        for node in ast.walk(ast.parse(raw))
    )
    if not imports_mutmut:
        return authority
    Requirement = _requirements_api()
    requested = {"mutmut": set()}
    evaluated = {}
    pending = ["mutmut"]
    while pending:
        name = pending.pop()
        extras = requested[name]
        if name in evaluated and evaluated[name] == extras:
            continue
        evaluated[name] = set(extras)
        row, modules, requirements = _distribution(name)
        authority["distributions"][name] = row
        for module, origin in modules.items():
            if module in authority["modules"] and authority["modules"][module] != origin:
                raise ValueError("mutation dependencies declare conflicting module origins")
            authority["modules"][module] = origin
        for literal in requirements:
            requirement = Requirement(literal)
            if requirement.marker is not None and not any(
                requirement.marker.evaluate({"extra": extra}) for extra in extras | {""}
            ):
                continue
            dependency = _normal_name(requirement.name)
            merged = requested.get(dependency, set()) | requirement.extras
            if dependency not in requested or requested[dependency] != merged:
                requested[dependency] = merged
                pending.append(dependency)
    return authority


# Only import glue is emitted. The owner, selected mutant and all process
# supervision algorithms remain ordinary source in the pinned selected file.
ISOLATED_BOOTSTRAP = r"""
import hashlib
import importlib.abc
import importlib.machinery
import importlib.metadata
import importlib.util
import json
import os
import re
import stat
import sys
import types
from pathlib import Path
if not sys.flags.isolated or not sys.flags.no_site:
    raise ValueError("mutation bootstrap requires -I -S")
packet = json.load(sys.stdin)
authority = packet["authority"]
modules, distributions = authority["modules"], authority["distributions"]
stdlib = list(sys.path)
def read(row):
    if (not isinstance(row, dict) or any(type(row.get(key)) is not int for key in ("dev", "ino", "fullmode"))
        or not isinstance(row.get("path"), str) or not isinstance(row.get("sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) is None):
        raise ValueError("malformed mutation import pin")
    path = Path(row["path"])
    if not path.is_absolute() or str(path) != os.path.normpath(path):
        raise ValueError("mutation import path must be absolute")
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        first = os.fstat(fd)
        if not stat.S_ISREG(first.st_mode) or (first.st_dev, first.st_ino, first.st_mode) != (row["dev"], row["ino"], row["fullmode"]):
            raise ValueError("mutation import identity changed")
        blocks = []
        while block := os.read(fd, 1024 * 1024):
            blocks.append(block)
        raw = b"".join(blocks)
        last, named = os.fstat(fd), path.lstat()
        def identity(s):
            return (s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if identity(first) != identity(last) or identity(last) != identity(named) or hashlib.sha256(raw).hexdigest() != row["sha256"]:
            raise ValueError("mutation import bytes changed")
        return raw
    finally:
        os.close(fd)
if not isinstance(modules, dict) or not isinstance(distributions, dict):
    raise TypeError("malformed mutation dependency authority")
for name, row in distributions.items():
    if not isinstance(name, str) or not isinstance(row, dict) or not isinstance(row.get("root"), str):
        raise TypeError("malformed mutation distribution authority")
    root = Path(row["root"])
    if not root.is_absolute() or not Path(row["metadata"]["path"]).is_relative_to(root):
        raise ValueError("foreign mutation metadata origin")
    read(row["metadata"])
    if not Path(row["record"]["path"]).is_relative_to(root):
        raise ValueError("foreign mutation inventory origin")
    read(row["record"])
for name, row in modules.items():
    if not isinstance(name, str) or not all(part.isidentifier() for part in name.split(".")) or not isinstance(row, dict):
        raise ValueError("malformed mutation module authority")
    if row.get("kind") not in ("source", "package", "extension") or row.get("distribution") not in distributions:
        raise ValueError("unbound mutation module authority")
    if not Path(row["path"]).is_relative_to(Path(distributions[row["distribution"]]["root"])):
        raise ValueError("foreign mutation module origin")
class Source(importlib.machinery.SourceFileLoader):
    def get_code(self, fullname):
        return compile(read(modules[fullname]), self.path, "exec", dont_inherit=True)
class Distribution(importlib.metadata.Distribution):
    def __init__(self, row):
        self.row = row
    def read_text(self, filename):
        return read(self.row["metadata"]).decode("utf-8") if filename == "METADATA" else None
    def locate_file(self, path):
        return Path(self.row["root"]) / path
class Finder(importlib.abc.MetaPathFinder):
    def find_distributions(self, context):
        requested = re.sub(r"[-_.]+", "-", context.name).lower() if context.name else None
        for name, row in distributions.items():
            if requested is None or requested == name:
                yield Distribution(row)
    def find_spec(self, fullname, path=None, target=None):
        row = modules.get(fullname)
        if row is not None:
            if row["kind"] == "extension":
                read(row)
                loader = importlib.machinery.ExtensionFileLoader(fullname, row["path"])
            else:
                loader = Source(fullname, row["path"])
            return importlib.util.spec_from_file_location(fullname, row["path"], loader=loader,
                submodule_search_locations=[str(Path(row["path"]).parent)] if row["kind"] == "package" else None)
        if fullname.partition(".")[0] not in sys.stdlib_module_names:
            raise ModuleNotFoundError("unlisted mutation owner import: " + fullname)
        spec = importlib.machinery.PathFinder.find_spec(fullname, stdlib if path is None else path)
        if spec is not None and spec.origin not in ("built-in", "frozen", None):
            origin = Path(spec.origin).absolute()
            if not any(origin.is_relative_to(Path(p)) for p in stdlib if Path(p).is_dir()):
                raise ModuleNotFoundError("foreign standard-library origin")
        return spec
sys.meta_path = [importlib.machinery.BuiltinImporter, importlib.machinery.FrozenImporter, Finder()]
name = authority["module_name"]
if not isinstance(name, str) or not all(part.isidentifier() for part in name.split(".")):
    raise ValueError("invalid selected mutation owner name")
owner = types.ModuleType(name)
owner.__file__ = authority["owner"]["path"]
owner.__package__ = name.rpartition(".")[0]
sys.modules[name] = owner
os.environ["MUTANT_UNDER_TEST"] = authority["selected_mutant"]
exec(compile(read(authority["owner"]), owner.__file__, "exec", dont_inherit=True), owner.__dict__)  # noqa: S102 - only verified selected owner bytes
print(json.dumps(owner._supervise(packet["request"])))
"""
