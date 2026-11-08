# SPDX-License-Identifier: Apache-2.0
"""Non-executing evaluator for explicit, bounded primitive return contracts.

Successful evaluation establishes only a concrete return value in this subset,
not a defect or authority over an arbitrary natural-language finding.
"""

from __future__ import annotations

import ast
import io
import tokenize

MAX_SOURCE_BYTES = 32768
MAX_TOKENS = 1024
MAX_DEPTH = 64
MAX_NODES = 4096
MAX_STRING = 4096
MAX_INTEGER_BITS = 256


class _Unsupported(Exception):
    pass


def _primitive(value):
    return (
        value is None
        or type(value) is bool
        or (type(value) is int and value.bit_length() <= MAX_INTEGER_BITS)
        or (type(value) is str and len(value) <= MAX_STRING)
    )


def _identifier(value):
    return type(value) is str and len(value) <= 128 and value.isidentifier()


def _docstring(node):
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and type(node.value.value) is str
        and len(node.value.value) <= MAX_STRING
    )


def _statements(body):
    return body[1:] if body and _docstring(body[0]) else body


def _signature(node):
    args = node.args
    if (
        node.decorator_list
        or node.returns is not None
        or getattr(node, "type_params", ())
        or args.posonlyargs
        or args.vararg
        or args.kwarg
        or args.defaults
        or any(value is not None for value in args.kw_defaults)
    ):
        raise _Unsupported
    parameters = args.args + args.kwonlyargs
    names = [arg.arg for arg in parameters]
    if len(set(names)) != len(names) or any(arg.annotation is not None for arg in parameters):
        raise _Unsupported
    return names


def _value_shape(node, names):
    if isinstance(node, ast.Constant) and _primitive(node.value):
        return
    if isinstance(node, ast.Name) and node.id in names:
        return
    raise _Unsupported


def _condition_shape(node, names):
    if isinstance(node, ast.Compare):
        if len(node.ops) != 1 or len(node.comparators) != 1:
            raise _Unsupported
        if not isinstance(node.ops[0], (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
            raise _Unsupported
        _value_shape(node.left, names)
        _value_shape(node.comparators[0], names)
    else:
        _value_shape(node, names)


def _validate_shape(body, names):
    # Inspect every statement, including unreachable branches and statements
    # following return. A known outcome cannot hide an unsupported dependency.
    for node in body:
        if isinstance(node, ast.Pass):
            continue
        if isinstance(node, ast.Return):
            if node.value is not None:
                _value_shape(node.value, names)
        elif isinstance(node, ast.If):
            _condition_shape(node.test, names)
            _validate_shape(node.body, names)
            _validate_shape(node.orelse, names)
        else:
            raise _Unsupported


def _value(node, arguments):
    if isinstance(node, ast.Constant):
        return node.value
    return arguments[node.id]


def _condition(node, arguments):
    if isinstance(node, ast.Compare):
        left = _value(node.left, arguments)
        right = _value(node.comparators[0], arguments)
        if type(left) is not type(right):
            raise _Unsupported
        op = node.ops[0]
        if isinstance(op, ast.Eq):
            return left == right
        if isinstance(op, ast.NotEq):
            return left != right
        if type(left) not in (int, str):
            raise _Unsupported
        if isinstance(op, ast.Lt):
            return left < right
        if isinstance(op, ast.LtE):
            return left <= right
        if isinstance(op, ast.Gt):
            return left > right
        return left >= right
    value = _value(node, arguments)
    if type(value) is not bool:
        raise _Unsupported
    return value


def _validate_conditions(body, arguments):
    for node in body:
        if isinstance(node, ast.If):
            _condition(node.test, arguments)
            _validate_conditions(node.body, arguments)
            _validate_conditions(node.orelse, arguments)


def _run_body(body, arguments):
    for node in body:
        if isinstance(node, ast.Return):
            return True, None if node.value is None else _value(node.value, arguments)
        if isinstance(node, ast.If):
            returned, value = _run_body(
                node.body if _condition(node.test, arguments) else node.orelse, arguments
            )
            if returned:
                return True, value
    return False, None


def evaluate(source: bytes, function: str, arguments: dict) -> tuple[bool, object, str]:
    """Return (supported, concrete primitive value, bounded diagnostic code).

    No imports, eval, exec or calls into reviewed code. Compilation validates
    syntax only. MemoryError and process interruption deliberately propagate.
    """
    if (
        type(source) is not bytes
        or not _identifier(function)
        or type(arguments) is not dict
        or len(arguments) > 128
        or any(not _identifier(k) or not _primitive(v) for k, v in arguments.items())
    ):
        return False, None, "invalid-input"
    if len(source) > MAX_SOURCE_BYTES:
        return False, None, "source-budget"
    try:
        depth = indentation = 0
        for index, token in enumerate(tokenize.tokenize(io.BytesIO(source).readline)):
            if index >= MAX_TOKENS:
                return False, None, "token-budget"
            if token.type == tokenize.OP:
                if token.string in ("(", "[", "{"):
                    depth += 1
                elif token.string in (")", "]", "}"):
                    depth -= 1
            elif token.type == tokenize.INDENT:
                indentation += 1
            elif token.type == tokenize.DEDENT:
                indentation -= 1
            if depth > MAX_DEPTH or indentation > MAX_DEPTH:
                return False, None, "depth-budget"
        tree = ast.parse(source)
    except RecursionError:
        return False, None, "parser-budget"
    except (SyntaxError, UnicodeError, LookupError, ValueError, tokenize.TokenError):
        return False, None, "source-invalid"
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        return False, None, "node-budget"
    try:
        # Parsing alone permits unresolved nonlocal declarations, for example.
        compile(tree, "<return-contract>", "exec", dont_inherit=True)
    except (SyntaxError, ValueError):
        return False, None, "source-invalid"
    except RecursionError:
        return False, None, "parser-budget"
    try:
        definitions = {}
        for node in _statements(tree.body):
            if not isinstance(node, ast.FunctionDef) or node.name in definitions:
                raise _Unsupported
            names = _signature(node)
            _validate_shape(_statements(node.body), names)
            definitions[node.name] = node
        target = definitions.get(function)
        if target is None:
            return False, None, "target-missing"
        if set(_signature(target)) != set(arguments):
            return False, None, "argument-mismatch"
        statements = _statements(target.body)
        _validate_conditions(statements, arguments)
        _, value = _run_body(statements, arguments)
    except _Unsupported:
        return False, None, "unsupported-source"
    except RecursionError:
        return False, None, "evaluation-budget"
    return True, value, "evaluated"
