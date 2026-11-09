# SPDX-License-Identifier: Apache-2.0
"""Experimental, syntax-only parameter facts. Never a verdict authority.

The caller supplies complete source bytes and a fixed atomic goal. This checker
binds its result to those bytes, not to a repository's provenance or runtime
symbol identity. It does not import source, run model commands, interpret prose,
resolve aliases, or change any Forge disposition. A matching digest does not
prove that the caller obtained the right source.
"""

from __future__ import annotations

import ast
import hashlib
import io
import keyword
import re
import sys
import tokenize
import unicodedata
from dataclasses import dataclass
from enum import Enum

RULE_VERSION = "python-declaration-parameter-v1"
MAX_SOURCE_BYTES = 32 * 1024
MAX_TOKENS = 1024
MAX_DELIMITER_DEPTH = 64
MAX_AST_NODES = 4096


class FactStatus(str, Enum):
    SUPPORTED = "SUPPORTED"
    REFUTED = "REFUTED"
    UNKNOWN = "UNKNOWN"
    CHECK_FAILED = "CHECK_FAILED"


@dataclass(frozen=True)
class DeclarationGoal:
    """One syntax fact, not a model-selected substitute for a whole finding."""

    source_sha256: str
    function: str
    declaration_line: int
    parameter: str
    expected_present: bool


@dataclass(frozen=True)
class Parameter:
    name: str
    kind: str


@dataclass(frozen=True)
class DeclarationFact:
    """Diagnostic value only; constructing/deserializing one grants no authority."""

    status: FactStatus
    reason: str
    goal: DeclarationGoal
    source_sha256: str | None = None
    declaration_span: tuple[int, int] | None = None
    parameters: tuple[Parameter, ...] = ()
    rule_version: str = RULE_VERSION
    parser: tuple[str, tuple[int, int, int]] = (sys.implementation.name, sys.version_info[:3])


def _identifier(value: object) -> bool:
    return (
        type(value) is str
        and value.isidentifier()
        and not keyword.iskeyword(value)
        and unicodedata.normalize("NFKC", value) == value
    )


def _valid_goal(goal: DeclarationGoal) -> bool:
    return (
        type(goal.source_sha256) is str
        and re.fullmatch(r"[0-9a-f]{64}", goal.source_sha256) is not None
        and _identifier(goal.function)
        and _identifier(goal.parameter)
        and type(goal.declaration_line) is int
        and goal.declaration_line > 0
        and type(goal.expected_present) is bool
    )


def _within_token_budget(source: bytes) -> bool:
    # Only a preflight resource filter, not a substitute for Python's parser or
    # a sandbox. Invalid tokenization is never used as proof of a source fact.
    depth = 0
    for count, token in enumerate(tokenize.tokenize(io.BytesIO(source).readline), 1):
        if count > MAX_TOKENS:
            return False
        if token.type == tokenize.OP:
            if token.string in ("(", "[", "{"):
                depth += 1
            elif token.string in (")", "]", "}"):
                depth -= 1
            if depth > MAX_DELIMITER_DEPTH:
                return False
    return True


def check_declaration_parameter(source: bytes, goal: DeclarationGoal) -> DeclarationFact:
    """Inspect a unique undecorated top-level declaration in complete source.

    SUPPORTED/REFUTED concern only the presence of a declared parameter name.
    In particular, **kwargs does not declare every possible keyword name, and
    positional-only presence does not imply keyword-call acceptance. The token,
    byte and AST limits are bounded-input defenses, not CPU/memory isolation.
    Process interruption and MemoryError deliberately propagate.
    """
    if type(source) is not bytes or type(goal) is not DeclarationGoal:
        raise TypeError("source bytes and DeclarationGoal are required")
    if not _valid_goal(goal):
        return DeclarationFact(FactStatus.UNKNOWN, "invalid-goal", goal)
    if len(source) > MAX_SOURCE_BYTES:
        return DeclarationFact(FactStatus.UNKNOWN, "source-budget", goal)
    digest = hashlib.sha256(source).hexdigest()

    def result(status, reason, *, span=None, parameters=()):
        return DeclarationFact(status, reason, goal, digest, span, parameters)

    if digest != goal.source_sha256:
        return result(FactStatus.UNKNOWN, "source-mismatch")
    try:
        if not _within_token_budget(source):
            return result(FactStatus.UNKNOWN, "token-budget")
        tree = ast.parse(source, feature_version=(3, 12))
    except (SyntaxError, ValueError, UnicodeError, LookupError, tokenize.TokenError, RecursionError):
        return result(FactStatus.CHECK_FAILED, "source-parse-failed")
    if sum(1 for _ in ast.walk(tree)) > MAX_AST_NODES:
        return result(FactStatus.UNKNOWN, "ast-budget")
    definitions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == goal.function
    ]
    if len(definitions) != 1:
        return result(FactStatus.UNKNOWN, "declaration-unavailable-or-ambiguous")
    definition = definitions[0]
    if definition.lineno != goal.declaration_line:
        return result(FactStatus.UNKNOWN, "declaration-location-mismatch")
    if definition.decorator_list:
        return result(FactStatus.UNKNOWN, "decorated-declaration")
    args = definition.args
    parameters = tuple(
        [Parameter(arg.arg, "positional-only") for arg in args.posonlyargs]
        + [Parameter(arg.arg, "positional-or-keyword") for arg in args.args]
        + ([Parameter(args.vararg.arg, "var-positional")] if args.vararg else [])
        + [Parameter(arg.arg, "keyword-only") for arg in args.kwonlyargs]
        + ([Parameter(args.kwarg.arg, "var-keyword")] if args.kwarg else [])
    )
    present = any(parameter.name == goal.parameter for parameter in parameters)
    status = FactStatus.SUPPORTED if present == goal.expected_present else FactStatus.REFUTED
    return result(
        status,
        "declaration-parameter-membership",
        span=(definition.lineno, definition.end_lineno),
        parameters=parameters,
    )
