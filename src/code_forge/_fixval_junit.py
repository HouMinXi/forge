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
# `from x import (` with the closing parenthesis on a later line.
_OPEN_FROM_IMPORT = re.compile(r"from\s+[\w.]+\s+import\s*\([^)#]*(?:#.*)?$")

_CANNOT_IMPORT = re.compile(r"cannot import name '(\w+)' from '([\w.]+)'")
_NO_ATTRIBUTE = re.compile(r"module '([\w.]+)' has no attribute '(\w+)'")
_NO_MODULE = re.compile(r"No module named '([\w.]+)'|bad magic number in '([\w.]+)'")

# A <failure> with one of these messages is a test that passed when it was
# expected to fail: a strict xfail that passed, or a unittest expectedFailure
# that succeeded. #28 counts neither as a RED witness.
_UNEXPECTED_PASS = ("[XPASS(strict)]", "Failed: Unexpected success")


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
    # Known limit: only `src/` is stripped. A module under another root
    # (`lib/pkg/mod.py` imported as `pkg.mod`) is matched by its full dotted
    # path alone, so an import error that names the shorter form is not RED
    # and the run stays inconclusive. Guessing roots would widen RED.
    if parts[0] == "src" and len(parts) > 1:
        names.add(".".join(parts[1:]))
    return names


def _clause_names(clause: str) -> set[str]:
    names = set()
    for item in clause.split(","):
        alias = _ALIAS.fullmatch(item.strip())
        if alias:
            names.add(alias.group(1) or item.strip().split(".")[0])
    return names


def _imported_names(line: str) -> set[str]:
    match = _FROM_IMPORT.match(line) or _IMPORT.match(line)
    return _clause_names(match.group(1)) if match else set()


class _Side:
    """One side (pre- or post-image) of a hunk, read in order, so the
    lines inside a multi-line `from x import (...)` bind their names.

    Known limit: a hunk that starts inside such an import does not see its
    opening line, so the names there are not read (no RED from them)."""

    def __init__(self):
        self.open = False

    def names(self, line: str) -> set[str]:
        if self.open:
            inside, closed, _rest = line.split("#", 1)[0].partition(")")
            self.open = not closed
            return _clause_names(inside)
        if _OPEN_FROM_IMPORT.match(line):
            self.open = True
        return _defined(line)


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
        source = patched_file_path(entry, source=True)
        if source is None or source != path:
            # A created, renamed or copied file: its module exists only
            # with the fix (a rename's old name is gone, not added).
            modules.update(dotted - _module_names(source or ""))
        added: set[str] = set()
        removed: set[str] = set()
        for hunk in entry:
            new, old = _Side(), _Side()
            for line in hunk:
                if line.is_added:
                    added |= new.names(line.value)
                elif line.is_removed:
                    removed |= old.names(line.value)
                else:
                    new.names(line.value)
                    old.names(line.value)
        names.update((module, name) for module in dotted for name in added - removed)
    return AddedNames(frozenset(names), frozenset(modules))


def _is_added_import_failure(text: str, added: AddedNames) -> bool:
    # `from pkg import name` names a binding or a submodule the fix adds.
    for name, module in _CANNOT_IMPORT.findall(text):
        if (module, name) in added.names or "%s.%s" % (module, name) in added.modules:
            return True
    for module, name in _NO_ATTRIBUTE.findall(text):
        if (module, name) in added.names or "%s.%s" % (module, name) in added.modules:
            return True
    # A module the fix adds is missing, or only a stale foreign .pyc is left.
    # Known limit: a missing parent (`No module named 'pkg.new'` when the fix
    # creates `pkg/new/mod.py` as a namespace package) is not RED, because
    # the patch alone does not show whether `pkg/new/` existed before.
    return any(set(modules) & added.modules for modules in _NO_MODULE.findall(text))


def red_witness(junit_xml: str, added: AddedNames) -> bool:
    """Whether the report shows the reverted code failing a test.

    A `<failure>` is a failed test call, unless it reports an unexpected
    pass (a strict XPASS or a unittest unexpected success). An `<error>`
    counts only when it is a collection or setup error whose text is an
    import of a name in `added`; teardown errors never count. A report that does not parse
    shows nothing.
    """
    try:
        # pytest wrote it into a private directory. ElementTree resolves no
        # external entities. Entity expansion (billion laughs) is capped only
        # by expat 2.4.0 and later (pyexpat.EXPAT_VERSION); with an older
        # expat a crafted report could use much memory. Accepted: writing
        # one takes code that already runs in the test process (conftest).
        root = ElementTree.fromstring(junit_xml)  # noqa: S314
    except ElementTree.ParseError:
        return False
    for case in root.iter("testcase"):
        for failure in case.findall("failure"):
            if not failure.get("message", "").startswith(_UNEXPECTED_PASS):
                return True
        for error in case.findall("error"):
            message = error.get("message", "")
            if message.startswith("failed on teardown"):
                continue
            if _is_added_import_failure(message + "\n" + (error.text or ""), added):
                return True
    return False
