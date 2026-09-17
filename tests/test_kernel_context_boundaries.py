"""Boundary fixtures exercise production extraction and rendering."""
import hashlib
import os
from pathlib import Path

import pytest

from code_forge import kernel_context as k
from code_forge.context_sources import FactRow
from tests.test_kernel_context_source import diff, source


@pytest.mark.parametrize(("text", "expected"), [
    ("\\\n\r\t\0\x1f\x7f|`<>", "\\\\\\n\\r\\t\\x00\\x1f\\x7f\\|\\`&lt;&gt;"),
    ("abc", "abc"),
])
def test_escape_single_pass(text, expected):
    assert k.escape(text) == expected


def test_display_paths_and_label_limits():
    assert k.display_path("<" * 80) == ".../" + "&lt;" * 19
    assert k.display_path("x" * 80) == "x" * 80
    assert k.display_path("x" * 81) == ".../" + "x" * 76
    assert "workspace=" + "&lt;" * 8 + " file=" in k.source_line(Path("<" * 80))


@pytest.mark.parametrize("label,short,unread", [("fixture", 410, 266), ("a" * 32, 435, 291)])
def test_frozen_lengths(label, short, unread):
    prefix = k.KERNEL_CONTEXT_SCOPE_NOTICE
    assert len(prefix + k.source_line(Path(label), "x" * 80, "a" * 64)
               + k.KERNEL_CONTEXT_SHORT_DIAGNOSTIC) == short
    assert len(prefix + k.source_line(Path(label)) + k.KERNEL_CONTEXT_SHORT_DIAGNOSTIC) == unread


@pytest.mark.parametrize("label,expected", [("fixture", 453), ("a" * 32, 478)])
def test_read_failed_keeps_diagnostic_table(tmp_path, label, expected):
    root = tmp_path / label
    root.mkdir()
    src = source(root, defconfig="x" * 211, max_chars=512)
    rows = src.facts([], diff(["CONFIG_X"]))
    assert len(rows) == 1 and rows[0].entity == "context-status:read-failed"
    assert len(src.rendered_text) == expected
    assert "x" * 211 not in src.rendered_text


def diagnostic(reason):
    return FactRow("context-status:" + reason, "", "", "unknown; reason=" + reason, "kernel")


@pytest.mark.parametrize("limit", [1, 2])
def test_diagnostic_sorting_and_counts(tmp_path, limit):
    src = source(tmp_path, max_rows=limit)
    rows = sorted([diagnostic("no-config"), diagnostic("input-limit")], key=k._rank)
    result = src._render(rows)
    assert [r.entity for r in result] == ["context-status:input-limit", "context-status:no-config"][:limit]
    assert src.warnings == (["kernel-context: omitted diagnostics=1 data=0"] if limit == 1 else [])


def test_two_diagnostics_take_priority_over_data(tmp_path):
    src = source(tmp_path, max_rows=2)
    data = FactRow("guard:new:1:if:unparsed", "x.c", "", "expr=#if X", "kernel", 1)
    result = src._render([diagnostic("input-limit"), diagnostic("no-config"), data])
    assert len(result) == 2 and all(r.entity.startswith("context-status:") for r in result)
    assert src.warnings == ["kernel-context: omitted diagnostics=0 data=1"]


def test_diagnostic_not_sacrificed_for_marker(tmp_path):
    root = tmp_path / "fixture"
    root.mkdir()
    src = source(root, max_chars=512)
    src.snapshot_digest = "a" * 64
    rows = [diagnostic("input-limit"), FactRow("guard:new:1:if:unparsed", "x.c", "", "x" * 800, "kernel", 1)]
    rows[0] = FactRow("context-status:input-limit", "", "", "unknown; reason=input-limit; coverage=unknown", "kernel")
    kept = src._render(rows)
    assert [r.entity for r in kept] == ["context-status:input-limit"]
    assert len(src.rendered_text) == 464
    assert src.warnings == ["kernel-context: omitted diagnostics=0 data=1"]


def test_long_path_actual_read_then_short_diagnostic(tmp_path):
    root = tmp_path / ("a" * 32)
    root.mkdir()
    name = "<" * 80
    (root / name).write_text("CONFIG_X=y\n")
    src = source(root, defconfig=name, max_chars=512)
    src._read_config()
    kept = src._render([FactRow("context-status:input-limit", "", "", "unknown; reason=input-limit; coverage=unknown", "kernel")])
    assert kept == []
    assert len(src.rendered_text) == 435
    assert ".../" + "&lt;" * 19 in src.rendered_text


def test_same_descriptor_survives_replacement(tmp_path, monkeypatch):
    cfg = tmp_path / "defconfig"
    cfg.write_bytes(b"CONFIG_X=y\n")
    fstat = os.fstat
    swapped = False
    def replace_on_stat(fd):
        nonlocal swapped
        result = fstat(fd)
        if not swapped:
            cfg.rename(tmp_path / "old")
            cfg.write_bytes(b"CONFIG_X=n\n")
            swapped = True
        return result
    monkeypatch.setattr(os, "fstat", replace_on_stat)
    assert k.read_config_bytes(tmp_path, "defconfig") == b"CONFIG_X=y\n"


def test_descriptor_cleanup_on_read_error(tmp_path, monkeypatch):
    (tmp_path / "defconfig").write_bytes(b"data")
    before = len(os.listdir("/proc/self/fd"))
    def fail(*args):
        raise OSError("secret-error")
    monkeypatch.setattr(os, "read", fail)
    with pytest.raises(k.ReadFailure, match="^read-failed$"):
        k.read_config_bytes(tmp_path, "defconfig")
    assert len(os.listdir("/proc/self/fd")) == before


def test_unsupported_platform(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "supports_dir_fd", set())
    src = source(tmp_path)
    assert src.facts([], diff(["CONFIG_X"]))[0].entity == "context-status:unsafe-open-unsupported"
    assert src.warnings == ["kernel-context: reason=unsafe-open-unsupported"]


@pytest.mark.parametrize("path", ["/etc/passwd", "../bad", "x\0y"])
def test_low_level_path_reject(tmp_path, path):
    with pytest.raises(k.ReadFailure, match="^invalid-path$"):
        k.read_config_bytes(tmp_path, path)


def test_growth_after_stat_is_bounded(tmp_path, monkeypatch):
    (tmp_path / "defconfig").write_text("small")
    monkeypatch.setattr(os, "read", lambda fd, size: b"x" * size)
    with pytest.raises(k.ReadFailure, match="^file-size-exceeded$"):
        k.read_config_bytes(tmp_path, "defconfig")


def test_deleted_file_and_rename_sides(tmp_path):
    (tmp_path / "defconfig").write_text("CONFIG_X=m\n")
    patch = diff([], "old.c", ["#ifdef CONFIG_X"]).replace("--- a/old.c", "deleted file mode 100644\n--- a/old.c").replace("+++ b/old.c", "+++ /dev/null")
    rows = source(tmp_path).facts([], patch)
    assert any(r.entity == "guard:old:1:ifdef:CONFIG_X" and r.file == "old.c" for r in rows)
    patch = diff(["#ifdef CONFIG_X"], "old.c", ["#ifndef CONFIG_X"]).replace("b/old.c", "b/new.c")
    rows = source(tmp_path).facts([], patch)
    assert {(r.file, r.entity.split(":")[1]) for r in rows if r.entity.startswith("guard:")} == {("old.c", "old"), ("new.c", "new")}


@pytest.mark.parametrize("name", ["IS_ENABLED", "IS_BUILTIN", "IS_MODULE", "IS_REACHABLE"])
def test_guard_locations_distinct(tmp_path, name):
    rows = source(tmp_path).facts([], diff([f"if ({name}(CONFIG_X))", f"if ({name}(CONFIG_X))"]))
    guards = [r for r in rows if r.entity.startswith("guard:")]
    assert len(guards) == 2
    assert {r.origin_line for r in guards} == {1, 2}


def test_unique_candidate_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(k, "MAX_CANDIDATES", 2)
    (tmp_path / "defconfig").write_text("CONFIG_A=y\nCONFIG_B=m\nCONFIG_C=n\n")
    rows = source(tmp_path).facts([], diff(["CONFIG_A", "CONFIG_B", "CONFIG_C"]))
    assert {r.entity for r in rows} == {"config:CONFIG_A", "config:CONFIG_B", "context-status:input-limit"}


def test_diff_size_cap_preserves_completed_observations(tmp_path, monkeypatch):
    first = diff(["#if FIRST"], "first.c")
    second = diff(["x" * 100], "second.c")
    monkeypatch.setattr(k, "MAX_DIFF_BYTES", len(first.encode()) + 30)
    rows = source(tmp_path).facts([], first + second)
    assert {r.entity for r in rows} == {"guard:new:1:if:unparsed", "context-status:input-limit"}


def test_binding_path_display_does_not_leak_full_identity(tmp_path):
    path = "Documentation/devicetree/bindings/" + "a" * 100 + ".yaml"
    src = source(tmp_path)
    rows = src.facts([], diff(['compatible: "vendor,a"'], path))
    assert path not in src.rendered_text
    assert rows[0].entity == "binding:new:" + k.display_path(path)


def test_binding_aggregates_all_changed_fragments(tmp_path):
    rows = source(tmp_path).facts([], diff(['compatible: vendor,a', 'required: [first]', 'required: [second]'],
                                           "Documentation/devicetree/bindings/demo.yaml"))
    bindings = [r for r in rows if r.entity.startswith("binding:")]
    assert len(bindings) == 1
    assert all(value in bindings[0].dependents for value in ("vendor,a", "first", "second"))


def test_dt_compatible_line_is_property(tmp_path):
    rows = source(tmp_path).facts([], diff(['compatible = "vendor,device";'], "board.dts"))
    assert rows[0].entity == "dt:new:1:prop:compatible"
    assert rows[0].dependents == 'role=prop; value="vendor,device"'


@pytest.mark.parametrize("line,entities", [
    ('uart0: serial@1000 {', {"dt:new:1:node:uart0: serial@1000"}),
    ('&uart0 {', {"dt:new:1:node:&uart0"}),
    ('clocks = <&clk &{/soc/clock}>;', {"dt:new:1:ref:&clk", "dt:new:1:ref:&{/soc/clock}"}),
    ('/* &ignored */', {"dt:new:1:unparsed:unparsed"}),
    ('#include "board.dtsi"', {"dt:new:1:unparsed:unparsed"}),
    ('status = "okay";', {"dt:new:1:unparsed:unparsed"}),
    ('', set()),
])
def test_device_tree_classification(tmp_path, line, entities):
    rows = source(tmp_path).facts([], diff([line], "board.dtsi"))
    assert {r.entity for r in rows} == entities


def test_nested_read_and_normalized_dot(tmp_path):
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "defconfig").write_bytes(b"CONFIG_X=y\n")
    assert k.read_config_bytes(tmp_path, "nested/defconfig") == b"CONFIG_X=y\n"
    assert k.validate_kernel_context({"defconfig": "."}).defconfig == ""
    with pytest.raises(k.ReadFailure, match="invalid-path"):
        k.read_config_bytes(tmp_path, ".")


def test_observation_candidate_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(k, "MAX_CANDIDATES", 2)
    rows = source(tmp_path).facts([], diff(["#if FIRST", "#if SECOND", "#if THIRD"]))
    assert {r.entity for r in rows} == {
        "guard:new:1:if:unparsed", "guard:new:2:if:unparsed", "context-status:input-limit"}


def test_context_lines_do_not_become_candidates(tmp_path):
    patch_text = diff(["#if NEW"]).replace("@@ -1,0 +1,1 @@", "@@ -1,1 +1,2 @@")
    patch_text = patch_text.replace("+#if NEW", " CONFIG_CONTEXT\n+#if NEW")
    rows = source(tmp_path).facts([], patch_text)
    assert {r.entity for r in rows} == {"guard:new:2:if:unparsed"}


def test_read_byte_fingerprint(tmp_path):
    (tmp_path / "defconfig").write_text("CONFIG_X=y\n# other\n")
    src = source(tmp_path)
    src.facts([], diff(["CONFIG_X"]))
    assert src.snapshot_digest == hashlib.sha256(src.snapshot_bytes).hexdigest()
