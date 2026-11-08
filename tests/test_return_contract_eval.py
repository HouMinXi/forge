# SPDX-License-Identifier: Apache-2.0
"""Fresh qualification of the bounded, non-executing return evaluator."""

import pytest

from code_forge import _return_contract_eval as evaluator


def test_concrete_return_and_fallthrough():
    cases = [
        (b"def f():\n    pass\n", {}, None),
        (b"def f():\n    return\n", {}, None),
        (b'def f():\n    "doc"\n    return "ok"\n', {}, "ok"),
        (b"def f(x, *, y):\n    if x < y:\n        return x\n    return y\n", {"x": 3, "y": 7}, 3),
        (b"def f(x):\n    if x:\n        return 5\n", {"x": False}, None),
        (b"def f(x):\n    if x:\n        return 5\n", {"x": True}, 5),
        (b"def f(x):\n    if x == None:\n        return True\n    return False\n", {"x": None}, True),
        (b'"module"\ndef g(x):\n    return x\ndef f():\n    return None\n', {}, None),
    ]
    for source, arguments, expected in cases:
        supported, value, reason = evaluator.evaluate(source, "f", arguments)
        assert supported and reason == "evaluated"
        assert type(value) is type(expected) and value == expected


def test_comparison_branches():
    for operator, expected in [
        ("==", False),
        ("!=", True),
        ("<", True),
        ("<=", True),
        (">", False),
        (">=", False),
    ]:
        source = f"def f(x, y):\n    if x {operator} y:\n        return True\n    else:\n        return False\n".encode()
        assert evaluator.evaluate(source, "f", {"x": 2, "y": 3}) == (True, expected, "evaluated")
    source = b"def f(x):\n    if x:\n        if False:\n            return 1\n    return 2\n"
    assert evaluator.evaluate(source, "f", {"x": True}) == (True, 2, "evaluated")


def test_external_and_dynamic_paths_refused_even_when_unreachable():
    sources = [
        "from library import cache\ndef f():\n    return cache.get()\n",
        "def f():\n    return library.get()\n",
        "def f():\n    return None\nf = other\n",
        "def f():\n    return None\ndef f():\n    return 1\n",
        "@decorate\ndef f():\n    return None\n",
        "class Cache:\n    def f(self):\n        return None\n",
        "async def f():\n    return None\n",
        "def f():\n    yield None\n",
        "def f():\n    if False:\n        return library.get()\n    return None\n",
        "def f():\n    return None\n    library.get()\n",
        "def f():\n    return None\ndef other(x=execute()):\n    pass\n",
        "def f():\n    return None\ndef other(x: execute()):\n    pass\n",
        "def f():\n    return None\n@execute()\ndef other():\n    pass\n",
        "def f():\n    return None\ndef other():\n    return external()\n",
        "def f():\n    return None\nexecute()\n",
        "def f():\n    return 1 + 2\n",
        "def f():\n    123\n    return None\n",
        "def f():\n    return True == False\n",
        "def f():\n    if 1 < 2 < 3:\n        pass\n",
        "def f():\n    if None is None:\n        pass\n",
        "def f():\n    while False:\n        pass\n",
    ]
    for source in sources:
        assert evaluator.evaluate(source.encode(), "f", {}) == (False, None, "unsupported-source")


def test_arguments_and_types_are_exact():
    for source, arguments in [
        (b"def f(x):\n    return x\n", {}),
        (b"def f():\n    return None\n", {"extra": 1}),
    ]:
        assert evaluator.evaluate(source, "f", arguments)[2] == "argument-mismatch"
    for source, arguments in [
        (b"def f(x=1):\n    return x\n", {"x": 1}),
        (b"def f(x, /):\n    return x\n", {"x": 1}),
        (b"def f(*x):\n    return None\n", {}),
        (b"def f(**x):\n    return None\n", {}),
        (b"def f(x):\n    if x:\n        return None\n", {"x": 1}),
        (b"def f(x):\n    if x == True:\n        return None\n", {"x": 1}),
        (b"def f(x):\n    if x < None:\n        return None\n", {"x": None}),
        (b"def f():\n    return absent\n", {}),
        (b"def f():\n    if False:\n        if 1:\n            pass\n", {}),
    ]:
        assert evaluator.evaluate(source, "f", arguments)[2] == "unsupported-source"
    for value in ([], {}, 1.5, 1 << 256, "x" * 4097):
        assert evaluator.evaluate(b"def f(x):\n    return x\n", "f", {"x": value})[2] == "invalid-input"
    for function in ("x" * 129, "not valid", None):
        assert evaluator.evaluate(b"def f(): pass\n", function, {})[2] == "invalid-input"
    assert evaluator.evaluate(b"def other():\n    pass\n", "f", {})[2] == "target-missing"


def test_malformed_and_resource_budgets(monkeypatch):
    for source in (
        b"def f(:",
        b"# coding: unknown-codec\ndef f(): pass\n",
        b"\xff",
        b"def f(x, x):\n    return x\n",
        b"def f():\n    return None\ndef other():\n    nonlocal missing\n",
    ):
        assert evaluator.evaluate(source, "f", {})[2] == "source-invalid"
    assert evaluator.evaluate(b"x" * 32769, "f", {})[2] == "source-budget"
    assert evaluator.evaluate(b"# comment\n" * 1025, "f", {})[2] == "token-budget"
    assert (
        evaluator.evaluate(b"def f():\n    return " + b"(" * 65 + b"0" + b")" * 65, "f", {})[2]
        == "depth-budget"
    )
    assert (
        evaluator.evaluate(b"def f():\n    return " + str(1 << 256).encode(), "f", {})[2]
        == "unsupported-source"
    )
    monkeypatch.setattr(evaluator, "MAX_NODES", 1)
    assert evaluator.evaluate(b"def f(): pass\n", "f", {})[2] == "node-budget"


def test_no_execution_and_interrupt_propagation(monkeypatch, tmp_path):
    sentinel = tmp_path / "must-not-exist"
    effect = f'open({str(sentinel)!r}, "w")'
    for source in (
        f"{effect}\ndef f():\n    return None\n",
        f"def f(x={effect}):\n    return None\n",
        f"def f(x: {effect}):\n    return None\n",
        f"@{effect}\ndef f():\n    return None\n",
        f"def f():\n    if True:\n        {effect}\n    return None\n",
        f"def f():\n    if False:\n        {effect}\n    return None\n",
    ):
        assert evaluator.evaluate(source.encode(), "f", {})[0] is False
        assert not sentinel.exists()
    for error in (MemoryError, KeyboardInterrupt, SystemExit):

        def fail(*args, error=error, **kwargs):
            raise error

        monkeypatch.setattr(evaluator.ast, "parse", fail)
        with pytest.raises(error):
            evaluator.evaluate(b"def f(): pass\n", "f", {})


def test_unexpected_parser_failure_is_not_semantic_uncertainty(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic infrastructure failure")

    monkeypatch.setattr(evaluator.ast, "parse", fail)
    with pytest.raises(RuntimeError, match="synthetic"):
        evaluator.evaluate(b"def f(): pass\n", "f", {})
