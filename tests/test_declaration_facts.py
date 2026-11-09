# SPDX-License-Identifier: Apache-2.0
"""Atomic syntax facts cannot create whole-finding or runtime authority."""

from dataclasses import FrozenInstanceError, replace
import hashlib

import pytest

from code_forge import declaration_facts as facts

SOURCE = b"def send(x, /, y=1, *args, timeout=2, **kwargs):\n    return x\n"


def goal(source=SOURCE, **changes):
    return replace(
        facts.DeclarationGoal(hashlib.sha256(source).hexdigest(), "send", 1, "timeout", True),
        **changes,
    )


def test_parameter_membership_and_complete_categories():
    for name in ("x", "y", "args", "timeout", "kwargs", "missing"):
        for expected in (True, False):
            result = facts.check_declaration_parameter(
                SOURCE, goal(parameter=name, expected_present=expected)
            )
            assert result.status == (
                facts.FactStatus.SUPPORTED
                if (name != "missing") == expected
                else facts.FactStatus.REFUTED
            )
            assert result.source_sha256 == hashlib.sha256(SOURCE).hexdigest()
            assert result.declaration_span == (1, 2)
            assert [(p.name, p.kind) for p in result.parameters] == [
                ("x", "positional-only"),
                ("y", "positional-or-keyword"),
                ("args", "var-positional"),
                ("timeout", "keyword-only"),
                ("kwargs", "var-keyword"),
            ]
            assert result.goal.parameter == name
            assert result.rule_version == facts.RULE_VERSION


def test_goal_identity_and_source_binding():
    for changes in (
        {"source_sha256": "0" * 64},
        {"function": "other"},
        {"declaration_line": 2},
        {"function": "alias.send"},
        {"parameter": "not a name"},
        {"expected_present": 1},
        {"declaration_line": True},
        {"source_sha256": "bad"},
        {"parameter": "class"},
        {"function": "ｓｅｎｄ"},
    ):
        assert (
            facts.check_declaration_parameter(SOURCE, goal(**changes)).status == facts.FactStatus.UNKNOWN
        )
    assert (
        facts.check_declaration_parameter(SOURCE + b"# new revision\n", goal()).reason
        == "source-mismatch"
    )


def test_complete_source_and_declaration_scope():
    for source, reason in (
        (b"def send(:\n", "source-parse-failed"),
        (b"def send(timeout):\n", "source-parse-failed"),
        (b"@decorator\ndef send(timeout):\n    pass\n", "decorated-declaration"),
        (
            b"def send(timeout):\n    pass\ndef send():\n    pass\n",
            "declaration-unavailable-or-ambiguous",
        ),
        (
            b"class Client:\n    def send(self, timeout):\n        pass\n",
            "declaration-unavailable-or-ambiguous",
        ),
        (b"if enabled:\n    def send(timeout):\n        pass\n", "declaration-unavailable-or-ambiguous"),
        (b"from library import send\n", "declaration-unavailable-or-ambiguous"),
    ):
        line = 2 if source.startswith(b"@") else 1
        result = facts.check_declaration_parameter(source, goal(source, declaration_line=line))
        assert result.reason == reason
        assert result.status in (facts.FactStatus.UNKNOWN, facts.FactStatus.CHECK_FAILED)
        assert result.parameters == ()


def test_syntax_only_never_imports_or_executes_source(tmp_path):
    marker = tmp_path / "must-not-exist"
    source = (
        f"open({str(marker)!r}, 'w').write('ran')\n"
        "def send(timeout=__import__('missing_dependency')):\n    raise RuntimeError('ran')\n"
    ).encode()
    result = facts.check_declaration_parameter(source, goal(source, declaration_line=2))
    assert result.status == facts.FactStatus.SUPPORTED
    assert not marker.exists()
    assert not hasattr(result, "disposition")
    with pytest.raises(FrozenInstanceError):
        result.status = facts.FactStatus.REFUTED

    # A syntax fact is not proof of runtime binding or whole-module validity.
    for suffix in (
        b"send = replacement\n",
        b"if enabled:\n    def send():\n        pass\n",
        b"return 1\n",  # ast.parse accepts it; module compilation rejects it.
    ):
        source = b"def send(timeout):\n    pass\n" + suffix
        assert (
            facts.check_declaration_parameter(source, goal(source)).status == facts.FactStatus.SUPPORTED
        )
    with pytest.raises(SyntaxError):
        compile(source, "<control>", "exec")


def test_async_unicode_and_encoding_cookie():
    for codec in ("base64_codec", "hex_codec", "rot_13", "zlib_codec", "bz2_codec", "no_such_codec"):
        source = (f"# coding: {codec}\ndef send(timeout):\n    pass\n").encode()
        result = facts.check_declaration_parameter(source, goal(source, declaration_line=2))
        assert result.status == facts.FactStatus.CHECK_FAILED
        assert result.reason == "source-parse-failed"
    for source, function, parameter in (
        (b"async def send(timeout):\n    pass\n", "send", "timeout"),
        ("def 发(参数):\n    pass\n".encode(), "发", "参数"),
        (b"# coding: latin-1\n# caf\xe9\ndef send(timeout):\n    pass\n", "send", "timeout"),
    ):
        line = 3 if source.startswith(b"# coding") else 1
        assert (
            facts.check_declaration_parameter(
                source, goal(source, function=function, parameter=parameter, declaration_line=line)
            ).status
            == facts.FactStatus.SUPPORTED
        )


def test_resource_limits_and_interrupts(monkeypatch):
    oversized = b"#" * (facts.MAX_SOURCE_BYTES + 1)
    assert facts.check_declaration_parameter(oversized, goal(oversized)).reason == "source-budget"
    many = b"x = 1\n" * facts.MAX_TOKENS
    assert facts.check_declaration_parameter(many, goal(many)).reason == "token-budget"
    deep = b"x = " + b"(" * 65 + b"1" + b")" * 65 + b"\n"
    assert facts.check_declaration_parameter(deep, goal(deep)).reason == "token-budget"
    monkeypatch.setattr(facts, "MAX_AST_NODES", 1)
    assert facts.check_declaration_parameter(SOURCE, goal()).reason == "ast-budget"
    for exc in (MemoryError(), KeyboardInterrupt(), SystemExit()):

        def fail(*args, exc=exc, **kwargs):
            raise exc

        monkeypatch.setattr(facts.ast, "parse", fail)
        with pytest.raises(type(exc)):
            facts.check_declaration_parameter(SOURCE, goal())


def test_input_types_do_not_accept_model_receipt_shaped_objects():
    for source, request in (({}, goal()), (SOURCE, {"source": "host"}), (bytearray(SOURCE), goal())):
        with pytest.raises(TypeError):
            facts.check_declaration_parameter(source, request)
