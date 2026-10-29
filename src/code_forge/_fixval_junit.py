# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""RED evidence from a reverted-code pytest run's JUnit XML (#28).

A reverted run counts as failing only when the report shows a failed test,
or a collection or setup error that is an import of a name the fix adds:
the fix defines `helper`, the test does `from mod import helper`, and the
reverted code no longer has it. Any other error (a broken fixture, a syntax
error, an unrelated import) did not judge the test.
"""

from __future__ import annotations

import keyword
import re
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass

import unidiff

from .diff import iter_diff_sections, patched_file_path

# Top-level definitions on an added or removed line of a production module.
_DEFINITION = re.compile(r"(?:async\s+def|def|class)\s+([A-Za-z_]\w*)")
_ASSIGNMENT = re.compile(r"([A-Za-z_]\w*)\s*(?::[^=]*)?=(?!=)")
_FROM_IMPORT = re.compile(r"from\s+[\w.]+\s+import\s+\(?([^)#]*)")
_IMPORT = re.compile(r"import\s+([^#]*)")
_ALIAS = re.compile(r"[\w.]+(?:\s+as\s+(\w+))?")

_CANNOT_IMPORT = re.compile(r"cannot import name '(\w+)' from '([\w.]+)'")
_NO_ATTRIBUTE = re.compile(r"module '([\w.]+)' has no attribute '(\w+)'")
_NO_MODULE = re.compile(r"No module named '([\w.]+)'|bad magic number in '([\w.]+)'")


@dataclass(frozen=True)
class AddedNames:
    """Module-level names and modules that exist only with the fix applied."""

    names: frozenset[tuple[str, str]]  # (module, name)
    modules: frozenset[str]


def _module_names(path: str) -> set[str]:
    """Dotted names `path` can be imported as, with and without `src.`."""
    if not path.endswith(".py"):
        return set()
    parts = path[: -len(".py")].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts or not all(part.isidentifier() for part in parts):
        return set()
    names = {".".join(parts)}
    if parts[0] == "src" and len(parts) > 1:
        names.add(".".join(parts[1:]))
    return names


def _imported_names(line: str) -> set[str]:
    match = _FROM_IMPORT.match(line)
    if match:
        clause = match.group(1)
    else:
        match = _IMPORT.match(line)
        if not match:
            return set()
        clause = match.group(1)
    names = set()
    for item in clause.split(","):
        alias = _ALIAS.fullmatch(item.strip())
        if alias:
            names.add(alias.group(1) or item.strip().split(".")[0])
    return names


def _defined(line: str) -> set[str]:
    """Module-level names a line binds; indented lines bind none."""
    for pattern in (_DEFINITION, _ASSIGNMENT):
        match = pattern.match(line)
        if match:
            return set() if keyword.iskeyword(match.group(1)) else {match.group(1)}
    return _imported_names(line)


def added_names(patch: str) -> AddedNames:
    """Names the fix in `patch` adds: bound on an added line and not on a
    removed line of the same file. A file the patch creates adds its module.
    An unreadable patch adds nothing, so no import error counts as RED."""
    names: set[tuple[str, str]] = set()
    modules: set[str] = set()
    try:
        blocks = [block for _path, block in iter_diff_sections(patch)]
        entries = [entry for block in blocks for entry in unidiff.PatchSet(block)]
    except (unidiff.errors.UnidiffParseError, ValueError):
        return AddedNames(frozenset(), frozenset())
    for entry in entries:
        path = patched_file_path(entry)
        dotted = _module_names(path or "")
        if not dotted:
            continue
        if patched_file_path(entry, source=True) is None:
            modules.update(dotted)
        added: set[str] = set()
        removed: set[str] = set()
        for hunk in entry:
            for line in hunk:
                if line.is_added:
                    added |= _defined(line.value)
                elif line.is_removed:
                    removed |= _defined(line.value)
        names.update((module, name) for module in dotted for name in added - removed)
    return AddedNames(frozenset(names), frozenset(modules))


def _is_added_import_failure(text: str, added: AddedNames) -> bool:
    for name, module in _CANNOT_IMPORT.findall(text):
        if (module, name) in added.names:
            return True
    for module, name in _NO_ATTRIBUTE.findall(text):
        if (module, name) in added.names:
            return True
    # A module the fix adds is missing, or only a stale foreign .pyc is left.
    return any(set(modules) & added.modules for modules in _NO_MODULE.findall(text))


def red_witness(junit_xml: str, added: AddedNames) -> bool:
    """Whether the report shows the reverted code failing a test.

    A `<failure>` is a failed test call. An `<error>` counts only when it is
    a collection or setup error whose text is an import of a name in
    `added`; teardown errors never count. A report that does not parse
    shows nothing.
    """
    try:
        # pytest wrote it; ElementTree resolves no external entities and the
        # bundled expat caps entity expansion, so a hostile conftest gains nothing.
        root = ElementTree.fromstring(junit_xml)  # noqa: S314
    except ElementTree.ParseError:
        return False
    for case in root.iter("testcase"):
        if case.find("failure") is not None:
            return True
        for error in case.findall("error"):
            message = error.get("message", "")
            if message.startswith("failed on teardown"):
                continue
            if _is_added_import_failure(message + "\n" + (error.text or ""), added):
                return True
    return False
