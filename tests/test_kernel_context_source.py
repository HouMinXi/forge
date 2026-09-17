"""Kernel observations never claim effective build configuration."""
import hashlib
import importlib
import os
from unittest.mock import patch

import pytest


def module():
    return importlib.import_module("code_forge.kernel_context")


def diff(lines, path="driver.c", removed=()):
    return (
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
        f"@@ -1,{len(removed)} +1,{len(lines)} @@\n"
        + "".join("-" + line + "\n" for line in removed)
        + "".join("+" + line + "\n" for line in lines)
    )


def source(tmp_path, **kw):
    return module().KernelContextSource.from_gate(
        tmp_path, {"kernel_context": {"enabled": True, "defconfig": "defconfig", **kw}}
    )


@pytest.mark.parametrize("section", [None, [], False, {"alien": 1}, {"enabled": 1},
    {"enabled": "true"}, {"max_rows": True}, {"max_chars": False},
    {"max_rows": 0}, {"max_rows": 201}, {"max_chars": 511}, {"max_chars": 32001},
    {"defconfig": 1}, {"defconfig": "../outside"}, {"defconfig": "/etc/passwd"},
    {"defconfig": "a/../b"}, {"defconfig": "a\x00b"}, {"enabled": True}])
def test_invalid_config_even_when_disabled(section):
    with pytest.raises(ValueError, match="kernel_context"):
        module().validate_kernel_context(section)


@pytest.mark.parametrize("section", [{}, {"enabled": False}, {"defconfig": "a//./b"},
    {"max_rows": 1, "max_chars": 512}, {"max_rows": 200, "max_chars": 32000}])
def test_disabled_factory_does_not_read(tmp_path, section):
    with patch("os.open", side_effect=AssertionError("unexpected read")):
        assert module().KernelContextSource.from_gate(tmp_path, {"kernel_context": section}) is None


def test_config_normalized_and_frozen():
    cfg = module().validate_kernel_context({"enabled": True, "defconfig": "a//./b"})
    assert cfg.defconfig == "a/b"
    with pytest.raises(AttributeError):
        cfg.enabled = False


@pytest.mark.parametrize(("line", "symbols"), [
    ('default "CONFIG_FOO"', set()),
    ('default "CONFIG_FOO" if X86', {"CONFIG_X86"}),
    ('default "CONFIG_FOO\\\"CONFIG_BAR" if X86', {"CONFIG_X86"}),
    ('default "CONFIG_FOO', set()),
    ('default 64', set()),
    ('default 100 if X86', {"CONFIG_X86"}),
    ('depends on 64BIT || A_SYM || B_SYM', {"CONFIG_64BIT", "CONFIG_A_SYM", "CONFIG_B_SYM"}),
    ('depends on CONFIG_lower || CONFIG_UPPER', {"CONFIG_lower", "CONFIG_UPPER"}),
    ('config lower', {"CONFIG_lower"}),
    ('# CONFIG_FOO', set()), ('  help CONFIG_FOO', set()),
])
def test_kconfig_final_candidates_control_reads(tmp_path, line, symbols):
    (tmp_path / "defconfig").write_text("CONFIG_X86=y\n", encoding="utf-8")
    src = source(tmp_path)
    with patch.object(src, "_read_config", wraps=src._read_config) as read:
        rows = src.facts([], diff([line], "arch/Kconfig"))
    assert {r.entity.removeprefix("config:") for r in rows} == symbols
    assert read.call_count == bool(symbols)
    if not symbols:
        assert src.rendered_text == ""


@pytest.mark.parametrize("filename", ["myKconfig", "Kconfiglib.py", "driver.c"])
def test_non_kconfig_uses_general_scan(tmp_path, filename):
    (tmp_path / "defconfig").write_text("CONFIG_FOO=m\n", encoding="utf-8")
    rows = source(tmp_path).facts([], diff(['default "CONFIG_FOO"'], filename))
    assert rows[0].entity == "config:CONFIG_FOO"
    assert rows[0].dependents == "declared=m; effective=unknown"


def test_snapshot_uses_working_bytes_once(tmp_path):
    data = b"CONFIG_X=y\nCONFIG_SECRET=99\n"
    (tmp_path / "defconfig").write_bytes(data)
    src = source(tmp_path)
    rows = src.facts([], diff(["CONFIG_X"]))
    assert src.snapshot_sha() is None
    assert rows[0].origin_line == 1
    assert rows[0].dependents == "declared=y; effective=unknown"
    assert hashlib.sha256(data).hexdigest() in src.rendered_text
    assert "SECRET" not in src.rendered_text
    (tmp_path / "defconfig").write_text("CONFIG_X=n\n", encoding="utf-8")
    assert src.facts([], diff(["CONFIG_X"])) == rows
    assert source(tmp_path).facts([], diff(["CONFIG_X"]))[0].dependents == "declared=n; effective=unknown"


@pytest.mark.parametrize(("body", "expected"), [
    ("CONFIG_X=y\n", "declared=y; effective=unknown"),
    ("CONFIG_X=m  \n", "declared=m; effective=unknown"),
    ("# CONFIG_X is not set\n", "declared=n; effective=unknown"),
    ("CONFIG_X=123\n", "declared=123; effective=unknown"),
    ("CONFIG_X=0xff\n", "declared=0xff; effective=unknown"),
    ('CONFIG_X="abc"\n', 'declared="abc"; effective=unknown'),
    ("CONFIG_X=y # comment\n", "unknown; reason=ambiguous-declaration"),
    ("CONFIG_X=y\nCONFIG_X=n\n", "unknown; reason=ambiguous-declaration"),
    ("", "unknown; reason=not-declared"),
])
def test_declared_literals_not_effective_values(tmp_path, body, expected):
    (tmp_path / "defconfig").write_text(body, encoding="utf-8")
    assert source(tmp_path).facts([], diff(["CONFIG_X"]))[0].dependents == expected


@pytest.mark.parametrize("kind", ["symlink", "parent-symlink", "fifo", "directory", "large", "encoding", "missing"])
def test_safe_read_failures(tmp_path, kind, monkeypatch):
    cfg = tmp_path / "defconfig"
    name = "defconfig"
    reasons = {"symlink": "symlink-rejected", "parent-symlink": "symlink-rejected",
        "fifo": "not-regular-file", "directory": "not-regular-file", "large": "file-size-exceeded",
        "encoding": "invalid-encoding", "missing": "read-failed"}
    if kind == "symlink":
        cfg.symlink_to("/etc/passwd")
    elif kind == "parent-symlink":
        cfg.symlink_to(tmp_path, target_is_directory=True)
        name += "/other"
    elif kind == "fifo":
        os.mkfifo(cfg)
    elif kind == "directory":
        cfg.mkdir()
    elif kind == "large":
        cfg.write_bytes(b"x" * (1024 * 1024 + 1))
    elif kind == "encoding":
        cfg.write_bytes(b"\xff")
    src = source(tmp_path, defconfig=name)
    if kind == "large":
        def unexpected_read(*args):
            raise AssertionError("oversized file must be rejected before any content read")
        monkeypatch.setattr(os, "read", unexpected_read)
    rows = src.facts([], diff(["CONFIG_X"]))
    assert rows[0].dependents == f"unknown; reason={reasons[kind]}"
    assert " file= sha256=\n" in src.rendered_text
    assert src.warnings == [f"kernel-context: reason={reasons[kind]}"]


def test_character_budget_requires_marker(tmp_path):
    root = tmp_path / "fixture"
    root.mkdir()
    (root / "defconfig").write_text("".join(f"CONFIG_TEST_{i}=y\n" for i in range(1, 6)))
    src = source(root, max_chars=512)
    rows = src.facts([], diff([f"CONFIG_TEST_{i}" for i in range(1, 6)]))
    assert [r.entity for r in rows] == ["config:CONFIG_TEST_1", "truncated"]
    assert rows[-1].dependents == "omitted=4; coverage=partial"
    assert len(src.rendered_text) == 511
    assert src.warnings == ["kernel-context: omitted diagnostics=0 data=4"]


def test_single_row_budget_reserves_marker(tmp_path):
    (tmp_path / "defconfig").write_text("CONFIG_X=y\nCONFIG_Y=n\n")
    rows = source(tmp_path, max_rows=1).facts([], diff(["CONFIG_X CONFIG_Y"]))
    assert [r.entity for r in rows] == ["truncated"]
    assert rows[0].dependents == "omitted=2; coverage=partial"
