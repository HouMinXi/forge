# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Literal Git line separators remain filename and source data."""

from pathlib import Path
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
def test_headerless_literal_git_header_runs_actual_hollow_test(tmp_path, separator, final_lf):
    import sys

    from code_forge.fixval import FixvalCandidate, FixvalStatus, run_fixval

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
        [sys.executable, "-B", "-m", "pytest", "-p", "no:cacheprovider"],
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


@pytest.mark.parametrize(
    "source_git,test_git,reverse",
    [(False, True, False), (False, True, True), (False, False, False), (True, True, False)],
    ids=["plain-then-git", "git-then-plain", "all-plain", "all-git"],
)
@pytest.mark.parametrize("ending", ["lf", "crlf", "no-final-lf"])
def test_ordered_names_include_traditional_members(tmp_path, source_git, test_git, reverse, ending):
    from code_forge.diff import get_changed_files

    _, source = _packet(tmp_path, "src/value.py", "value = 1\n", "value = 2\n")
    test = _git(tmp_path, "diff", "--cached", "--", "tests/test_case.py").decode("utf-8")
    if not source_git:
        source = source[source.index("--- ") :]
    if not test_git:
        test = test[test.index("--- ") :]
    members = [(source, "src/value.py"), (test, "tests/test_case.py")]
    if reverse:
        members.reverse()
    packet = "".join(text for text, _ in members)
    expected = [name for _, name in members]
    _git(tmp_path, "apply", "-R", "--check", "-", packet=packet.encode("utf-8"))
    if ending == "crlf":
        packet = packet.replace("\n", "\r\n")
    elif ending == "no-final-lf":
        packet = packet.removesuffix("\n")
    assert get_changed_files(packet) == sorted(expected)
    assert changed_files_in_order(packet) == expected
    assert _other_changed_files(packet, "tests/test_case.py") == ["src/value.py"]
    assert changed_files_in_order(packet.rstrip("\n") + "\n" + members[0][0]) == expected


@pytest.mark.parametrize("git_header", [False, True], ids=["plain", "git"])
@pytest.mark.parametrize("complete", [False, True], ids=["truncated", "complete"])
def test_ordered_names_do_not_promote_hunk_body_headers(git_header, complete):
    packet = ("diff --git a/body.txt b/body.txt\n" if git_header else "") + (
        "--- a/body.txt\n+++ b/body.txt\n"
        + ("@@ -1 +1 @@\n" if complete else "@@ -1,3 +1,3 @@\n")
        + "--- a/phantom.py\n+++ b/phantom.py\n"
    )
    expected = ["body.txt"] if complete or git_header else []
    assert changed_files_in_order(packet) == expected


@pytest.mark.parametrize(
    "packet,expected",
    [
        (
            "diff --git a/new.py b/new.py\nnew file mode 100644\n"
            "--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+x\n",
            "new.py",
        ),
        (
            "diff --git a/old.py b/old.py\ndeleted file mode 100644\n"
            "--- a/old.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n",
            "old.py",
        ),
        (
            "diff --git a/old.py b/new.py\nsimilarity index 100%\n"
            "rename from old.py\nrename to new.py\n",
            "new.py",
        ),
        (
            "diff --git a/old.py b/copy.py\nsimilarity index 100%\ncopy from old.py\ncopy to copy.py\n",
            "copy.py",
        ),
        ("diff --git a/mode.py b/mode.py\nold mode 100644\nnew mode 100755\n", "mode.py"),
        (
            "diff --git a/data.bin b/data.bin\nBinary files a/data.bin and b/data.bin differ\n",
            "data.bin",
        ),
        ("diff --git a/partial.py b/partial.py\n", "partial.py"),
        (
            "diff --git a/partial.py b/partial.py\n--- a/partial.py\n+++ b/partial.py\n"
            "@@ -1,3 +1,3 @@\n-old\n+new\n",
            "partial.py",
        ),
    ],
    ids=["addition", "deletion", "rename", "copy", "mode", "binary", "header-only", "partial"],
)
def test_ordered_names_retain_git_metadata_and_partial_frames(packet, expected):
    assert changed_files_in_order(packet) == [expected]


@pytest.mark.parametrize("change", ["mode", "rename", "copy", "binary"])
@pytest.mark.parametrize("reverse", [False, True], ids=["metadata-first", "traditional-first"])
def test_ordered_names_bind_metadata_to_its_own_frame(tmp_path, change, reverse):
    _git(tmp_path, "init", "-q")
    metadata = tmp_path / "metadata.py"
    metadata.write_bytes(b"\x00old binary\n" if change == "binary" else b"value = 0\n")
    plain = tmp_path / "plain.py"
    plain.write_text("value = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "--all", "--", ".")
    _git(tmp_path, "commit", "-q", "--no-gpg-sign", "-m", "Establish owned metadata fixture")
    target = metadata
    if change == "mode":
        metadata.chmod(0o755)
    elif change == "rename":
        target = tmp_path / "renamed.py"
        metadata.rename(target)
    elif change == "copy":
        target = tmp_path / "copied.py"
        target.write_bytes(metadata.read_bytes())
    else:
        metadata.write_bytes(b"\x00new binary\n")
    plain.write_text("value = 2\n", encoding="utf-8")
    _git(tmp_path, "add", "--all", "--", ".")
    options = ["diff", "--cached", "--binary", "--find-renames", "--find-copies-harder"]
    meta_frame = _git(tmp_path, *options, "--", metadata.name, target.name).decode("utf-8")
    assert "@@" not in meta_frame
    plain_frame = _git(tmp_path, *options, "--", plain.name).decode("utf-8")
    plain_frame = "diff -u a/plain.py b/plain.py\n" + plain_frame[plain_frame.index("--- ") :]
    frames = [(meta_frame, target.name), (plain_frame, plain.name)]
    if reverse:
        frames.reverse()
    packet = "".join(frame for frame, _ in frames)
    preimage = tmp_path / "preimage"
    preimage.mkdir()
    _git(preimage, "init", "-q")
    (preimage / metadata.name).write_bytes(b"\x00old binary\n" if change == "binary" else b"value = 0\n")
    (preimage / plain.name).write_text("value = 1\n", encoding="utf-8")
    _git(preimage, "apply", "--check", "-", packet=packet.encode("utf-8"))
    assert changed_files_in_order(packet) == [name for _, name in frames]
    assert _other_changed_files(packet, target.name) == [plain.name]
