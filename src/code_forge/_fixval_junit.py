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

import ast
from collections.abc import Mapping
import re
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass

import unidiff

from .diff import iter_diff_sections, patched_file_path

_CANNOT_IMPORT = re.compile(r"cannot import name '(\w+)' from '([\w.]+)'(?: \([^\r\n]*\))?")
_NO_ATTRIBUTE = re.compile(r"module '([\w.]+)' has no attribute '(\w+)'")
_NO_MODULE = re.compile(
    r"No module named '([\w.]+)'(?:; '[\w.]+' is not a package)?"
    r"|bad magic number in '([\w.]+)'(?:: b[^\r\n]+)?"
)

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


def _bindings(node: ast.AST) -> set[str]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return {alias.asname or alias.name.split(".")[0] for alias in node.names if alias.name != "*"}
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        if isinstance(node, ast.AnnAssign) and node.value is None:
            return set()
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return {
            child.id
            for target in targets
            for child in ast.walk(target)
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)
        }
    return set()


def _possible_bindings(tree: ast.AST) -> set[str] | None:
    """Conservatively include every syntactic binding in the old image."""
    if any(
        isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names)
        for node in ast.walk(tree)
    ):
        return None
    names = {name for node in ast.walk(tree) for name in _bindings(node)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names.add(node.rest)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
    return names


def added_names(
    patch: str,
    *,
    before: Mapping[str, bytes | None] | None = None,
    after: Mapping[str, bytes | None] | None = None,
) -> AddedNames:
    """Compare complete transaction-owned Python images, never isolated hunk lines.

    Missing images, invalid Python, and ambiguous bindings earn no import
    witness. Static bindings are evidence of a change, not proof of execution.
    Only direct module statements introduce candidates; all possible prior
    bindings (including nested/conditional ones) conservatively exclude them.
    """
    names: set[tuple[str, str]] = set()
    modules: set[str] = set()
    if before is None or after is None:
        return AddedNames(frozenset(), frozenset())
    try:
        entries = [
            entry for _path, block in iter_diff_sections(patch) for entry in unidiff.PatchSet(block)
        ]
    except (unidiff.errors.UnidiffParseError, ValueError):
        return AddedNames(frozenset(), frozenset())
    for entry in entries:
        path = patched_file_path(entry)
        dotted = _module_names(path or "")
        source = patched_file_path(entry, source=True)
        if not dotted or path not in before or path not in after:
            continue
        old, new = before[path], after[path]
        if new is None:
            continue
        try:
            new_tree = ast.parse(new)
            old_tree = ast.parse(old) if old is not None else ast.Module(body=[], type_ignores=[])
        except (SyntaxError, ValueError, RecursionError):
            continue
        if source is None or source != path:
            if old is None:
                modules.update(dotted - _module_names(source or ""))
        previous = _possible_bindings(old_tree)
        if previous is None:
            continue
        candidates = {name for node in new_tree.body for name in _bindings(node)}
        deleted = {
            node.id
            for node in ast.walk(new_tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Del)
        }
        for module in dotted:
            prior = previous
            if source != path and module in _module_names(source or ""):
                # Moving src/mod.py to mod.py does not create a new binding
                # under the shared import name. Its old image must be known.
                if source not in before or before[source] is None:
                    continue
                try:
                    source_tree = ast.parse(before[source])
                except (SyntaxError, ValueError, RecursionError):
                    continue
                source_bindings = _possible_bindings(source_tree)
                if source_bindings is None:
                    continue
                prior = prior | source_bindings
            names.update((module, name) for name in candidates - prior - deleted)
    return AddedNames(frozenset(names), frozenset(modules))


def _terminal_error(message: str, text: str) -> str | None:
    """Read pytest's terminal exception, not source lines or earlier chained errors.

    Multiline exception messages/notes, exception groups and unknown renderings
    are deliberately inconclusive. JUnit remains evidence from the test process,
    not an authenticated boundary against a plugin that forges the XML itself.
    """
    prefix = 'failed on setup with "'
    if message.startswith(prefix) and message.endswith('"'):
        diagnostic = message[len(prefix) : -1]
        return diagnostic if "\n" not in diagnostic and "\r" not in diagnostic else None
    if message != "collection failure":
        return None
    lines = text.rstrip().splitlines()
    if not lines:
        return None
    # The terminal E block must contain one complete exception line. Earlier
    # blocks may describe a chained exception; only the final block counts.
    last = len(lines) - 1
    if not re.match(r"^E\s+", lines[last]):
        return None
    if last and re.match(r"^E(?:\s|$)", lines[last - 1]):
        return None
    return re.sub(r"^E\s+", "", lines[last])


def _is_added_import_failure(text: str, added: AddedNames) -> bool:
    kind, separator, detail = text.partition(": ")
    if not separator:
        return False
    # Only one anchored terminal diagnostic supplies the name/module pair.
    match = _CANNOT_IMPORT.fullmatch(detail) if kind == "ImportError" else None
    if match is not None:
        name, module = match.groups()
        return (module, name) in added.names or "%s.%s" % (module, name) in added.modules
    match = _NO_ATTRIBUTE.fullmatch(detail) if kind == "AttributeError" else None
    if match is not None:
        module, name = match.groups()
        return (module, name) in added.names or "%s.%s" % (module, name) in added.modules
    # A missing parent is not evidence that the exact created module is absent.
    match = _NO_MODULE.fullmatch(detail) if kind in {"ImportError", "ModuleNotFoundError"} else None
    return match is not None and bool(set(match.groups()) & added.modules)


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
            diagnostic = _terminal_error(message, error.text or "")
            if diagnostic is not None and _is_added_import_failure(diagnostic, added):
                return True
    return False
