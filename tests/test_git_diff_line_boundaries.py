# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Literal Git line separators remain filename and source data."""

from pathlib import Path
import os
import shutil
import subprocess

import pytest

from code_forge.diff import annotate_diff_lines, changed_files_in_order, split_diff_for_files
from code_forge.falsify_real import _diff_for_file, _other_changed_files
from code_forge.fixval import _filter_non_test_patch


def _git(root: Path, *arguments: str, packet: bytes | None = None) -> bytes:
    return subprocess.run(
        [
            "/usr/bin/git",
            "--no-optional-locks",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.quotePath=false",
            "-c",
            "user.name=Minxi Hou",
            "-c",
            "user.email=houminxi@gmail.com",
            *arguments,
        ],
        cwd=root,
        input=packet,
        capture_output=True,
        check=True,
        timeout=5,
    ).stdout


def _packet(root: Path, name: str, before: str, after: str) -> tuple[str, str]:
    _git(root, "init", "-q")
    source = root / name
    source.parent.mkdir(parents=True)
    source.write_text(before, encoding="utf-8")
    tests = root / "tests/test_case.py"
    tests.parent.mkdir()
    tests.write_text("assert 1 == 1\n", encoding="utf-8")
    _git(root, "add", "--all", "--", ".")
    _git(root, "commit", "-q", "--no-gpg-sign", "-m", "Establish owned parser fixture")
    source.write_text(after, encoding="utf-8")
    tests.write_text("assert 2 == 2\n", encoding="utf-8")
    _git(root, "add", "--all", "--", ".")
    combined = _git(root, "diff", "--cached", "--binary")
    selected = _git(root, "diff", "--cached", "--binary", "--", name)
    _git(root, "apply", "-R", "--check", "-", packet=combined)
    return combined.decode("utf-8"), selected.decode("utf-8")


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\u0085"], ids=["U2028", "U2029", "U0085"])
@pytest.mark.parametrize("final_lf", [True, False], ids=["final-lf", "no-final-lf"])
def test_literal_filename_retains_projection_and_falsifier_scope(tmp_path, separator, final_lf):
    name = "src/name" + separator + "part.py"
    combined, selected = _packet(tmp_path, name, "value = 1\n", "value = 2\n")
    assert separator in combined
    if not final_lf:
        combined = combined.removesuffix("\n")
    assert split_diff_for_files(combined, [name]) == selected
    assert _filter_non_test_patch(combined) == selected
    assert changed_files_in_order(combined) == [name, "tests/test_case.py"]
    assert _other_changed_files(combined, name) == ["tests/test_case.py"]
    rendered = _diff_for_file(combined, name)
    assert name in rendered
    assert "[+   1] +value = 2\n" in rendered


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\u0085"], ids=["U2028", "U2029", "U0085"])
def test_literal_content_occupies_one_annotated_git_line(tmp_path, separator):
    before = 'value = "before"\n'
    after = 'value = "left' + separator + 'right"\nnext_value = 2\n'
    combined, selected = _packet(tmp_path, "src/module.py", before, after)
    assert split_diff_for_files(combined, ["src/module.py"]) == selected
    rendered = annotate_diff_lines(selected)
    assert '[+   1] +value = "left' + separator + 'right"\n' in rendered
    assert "[+   2] +next_value = 2\n" in rendered


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\u0085"], ids=["U2028", "U2029", "U0085"])
@pytest.mark.parametrize("final_lf", [True, False], ids=["final-lf", "no-final-lf"])
def test_headerless_literal_git_header_runs_actual_hollow_test(
    tmp_path, monkeypatch, separator, final_lf
):
    import sys

    from code_forge.fixval import FixvalCandidate, FixvalStatus, run_fixval

    monkeypatch.setenv(
        "PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
    )
    interpreter = shutil.which("python3")
    assert interpreter is not None
    assert Path(interpreter).resolve() == Path(sys.executable).resolve()

    name = "src/module.py"
    after = 'value = "left' + separator + 'diff --git fake"\n'
    if not final_lf:
        after = after.removesuffix("\n")
    _, selected = _packet(tmp_path, name, 'value = "old"\n', after)
    headerless = selected[selected.index("--- ") :]
    if not final_lf:
        headerless = headerless.removesuffix("\n")
        assert "\\ No newline at end of file" in headerless
    _git(tmp_path, "apply", "-R", "--check", "-", packet=headerless.encode("utf-8"))
    projected = _filter_non_test_patch(headerless)
    assert projected and separator + "diff --git fake" in projected
    assert "+++ b/" + name in projected
    tests = tmp_path / "tests/test_case.py"
    tests.write_text("def test_case():\n    assert 2 == 2\n", encoding="utf-8")
    source = tmp_path / name
    source.chmod(0o751)
    before = source.read_bytes(), source.stat().st_mode, tests.read_bytes()
    index = (tmp_path / ".git/index").read_bytes()
    head = _git(tmp_path, "rev-parse", "HEAD")
    result = run_fixval(
        FixvalCandidate(["tests/test_case.py"], [name]),
        ["python3", "-B", "-m", "pytest", "-p", "no:cacheprovider"],
        tmp_path,
        "Validate owned parser fixture",
        headerless,
    )
    assert result.status is FixvalStatus.BLOCK
    assert [finding.id for finding in result.findings] == ["FIXVAL_HOLLOW"]
    assert "Test(s) did not fail when the fix was reverted." in result.block_message
    assert (source.read_bytes(), source.stat().st_mode, tests.read_bytes()) == before
    assert (tmp_path / ".git/index").read_bytes() == index
    assert _git(tmp_path, "rev-parse", "HEAD") == head
    assert not list(tmp_path.glob(".fixval-recovery-*"))
    assert not list(tmp_path.glob(".fixval-retired-*"))


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\u0085"], ids=["U2028", "U2029", "U0085"])
@pytest.mark.parametrize("suffix", ["@@ malformed @@", "ordinary payload"])
@pytest.mark.parametrize("final_lf", [True, False], ids=["final-lf", "no-final-lf"])
def test_projection_keeps_literal_payload_as_one_git_line(tmp_path, separator, suffix, final_lf):
    after = 'value = "left' + separator + suffix + '"\n'
    _, selected = _packet(tmp_path, "src/module.py", 'value = "old"\n', after)
    if not final_lf:
        selected = selected.removesuffix("\n")
    assert _filter_non_test_patch(selected) == selected


@pytest.mark.parametrize("garbage", ["@@ malformed @@", "ordinary payload"])
def test_projection_rejects_actual_lf_framed_garbage(tmp_path, garbage):
    from code_forge._fixval_transaction import TransactionError
    from unidiff.errors import UnidiffParseError

    _, selected = _packet(tmp_path, "src/module.py", "value = 1\n", "value = 2\n")
    with pytest.raises((TransactionError, UnidiffParseError)):
        _filter_non_test_patch(selected + garbage + "\n")
