"""Validate surrounding context against immutable Git blobs, not live files."""

import json
import subprocess

import pytest

from code_forge.diff import parse_diff_hunks
from code_forge.source import compute_source_hash
from code_forge.verify import (
    _diff_validation_context,
    _reconstruct_post_image,
    parse_diff_files,
    run_verify,
    validate_excerpts_against_diff,
)


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, text=True)


@pytest.fixture
def candidate(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "commit.gpgsign", "false")
    file = tmp_path / "CHANGELOG.md"
    lines = [f"context {i}" for i in range(1, 50)]
    lines[43] = ""
    file.write_text("\n".join(lines) + "\n")
    git(tmp_path, "add", "CHANGELOG.md")
    git(tmp_path, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "base")
    lines[39] = "Document the new retention setting."
    file.write_text("\n".join(lines) + "\n")
    git(tmp_path, "add", "CHANGELOG.md")
    diff = git(tmp_path, "diff", "--cached", "--full-index")
    excerpt = {
        "file": "CHANGELOG.md",
        "start_line": 37,
        "end_line": 44,
        "content": "\n".join(lines[35:43]),
        "rationale": "context",
    }
    return tmp_path, diff, excerpt


@pytest.fixture
def unstaged_gap(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "commit.gpgsign", "false")
    before = [f"line {n}" for n in range(1, 31)]
    path = tmp_path / "changed.py"
    path.write_text("\n".join(before) + "\n")
    git(tmp_path, "add", "changed.py")
    git(tmp_path, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "base")
    after = before.copy()
    after[4] = "changed five"
    after[19] = "changed twenty"
    path.write_text("\n".join(after) + "\n")
    diff = git(tmp_path, "diff", "-U3", "HEAD", "--", "changed.py")
    excerpt = {
        "file": "changed.py",
        "start_line": 5,
        "end_line": 10,
        "content": "\n".join(after[4:10]),
    }
    other = {
        "file": "changed.py",
        "start_line": 20,
        "end_line": 20,
        "content": after[19],
    }
    full = {
        "file": "changed.py",
        "start_line": 1,
        "end_line": 26,
        "content": "\n".join(after[:26]),
    }
    return tmp_path, diff, excerpt, other, full


def test_unstaged_zero_context_insert_reconstructs_without_shifting_base():
    base = "base 1\nbase 2\nbase 3\n"
    first = "a/f b/f\nindex 0000000..1111111 100644\n--- a/f\n+++ b/f\n@@ -0,0 +1,1 @@\n+new start\n"
    middle = "a/f b/f\nindex 0000000..1111111 100644\n--- a/f\n+++ b/f\n@@ -2,0 +3,1 @@\n+new middle\n"
    assert _reconstruct_post_image(base, first) == [
        "new start",
        "base 1",
        "base 2",
        "base 3",
    ]
    assert _reconstruct_post_image(base, middle) == [
        "base 1",
        "base 2",
        "new middle",
        "base 3",
    ]


def test_unstaged_zero_context_git_diff_recovers_gap(unstaged_gap):
    root, _, excerpt, _, _ = unstaged_gap
    diff = git(root, "diff", "-U0", "HEAD", "--", "changed.py")
    assert "@@ -5 +5 @@" in diff
    assert validate_excerpts_against_diff(diff, [excerpt], cwd=root) == []


def test_unstaged_crlf_diff_keeps_immutable_gap(unstaged_gap):
    root, diff, excerpt, _, _ = unstaged_gap
    crlf_diff = diff.replace("\n", "\r\n")
    assert validate_excerpts_against_diff(crlf_diff, [excerpt], cwd=root) == []


@pytest.mark.parametrize("staged", [False, True])
def test_unstaged_gap_with_crlf_file_keeps_context(tmp_path, staged):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "commit.gpgsign", "false")
    git(tmp_path, "config", "core.autocrlf", "false")
    path = tmp_path / "changed.py"
    before = [f"line {n}" for n in range(1, 31)]
    path.write_bytes(("\r\n".join(before) + "\r\n").encode())
    git(tmp_path, "add", "changed.py")
    git(tmp_path, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "base")
    after = before.copy()
    after[4] = "changed five"
    after[20] = "changed twenty-one"
    path.write_bytes(("\r\n".join(after) + "\r\n").encode())
    git(tmp_path, "config", "core.autocrlf", "false")
    if staged:
        git(tmp_path, "add", "changed.py")
    args = ["--cached"] if staged else []
    diff = subprocess.check_output(
        ["git", "diff", *args, "HEAD", "--", "changed.py"], cwd=tmp_path
    ).decode()
    assert " line 2\r\n" in diff
    assert path.read_bytes().count(b"\r\n") == len(after)
    excerpt = {"file": "changed.py", "start_line": 5, "end_line": 10, "content": "\n".join(after[4:10])}
    post, _, _ = _diff_validation_context(diff, cwd=tmp_path)
    assert post["changed.py"][10] == "line 10"
    assert validate_excerpts_against_diff(diff, [excerpt], cwd=tmp_path) == []


@pytest.mark.parametrize("special_line", [6, 10])
def test_unstaged_gap_preserves_embedded_carriage_return(tmp_path, special_line):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "commit.gpgsign", "false")
    path = tmp_path / "changed.py"
    before = [f"line {n}" for n in range(1, 31)]
    before[special_line - 1] = f"line {special_line}\rsegment"
    git(tmp_path, "config", "core.autocrlf", "false")
    path.write_bytes(("\n".join(before) + "\n").encode())
    git(tmp_path, "add", "changed.py")
    git(tmp_path, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "base")
    after = before.copy()
    after[4] = "changed five"
    after[20] = "changed twenty-one"
    path.write_bytes(("\n".join(after) + "\n").encode())
    diff = subprocess.check_output(["git", "diff", "HEAD", "--", "changed.py"], cwd=tmp_path).decode()
    special = before[special_line - 1]
    assert (special in diff) == (special_line == 6)
    post, _, _ = _diff_validation_context(diff, cwd=tmp_path)
    assert post["changed.py"][special_line] == special
    excerpt = {"file": "changed.py", "start_line": 5, "end_line": 10, "content": "\n".join(after[4:10])}
    assert validate_excerpts_against_diff(diff, [excerpt], cwd=tmp_path) == []
    git(tmp_path, "add", "changed.py")
    staged = subprocess.check_output(
        ["git", "diff", "--cached", "HEAD", "--", "changed.py"],
        cwd=tmp_path,
    ).decode()
    staged_post, _, _ = _diff_validation_context(staged, cwd=tmp_path)
    assert staged_post["changed.py"][special_line] == special
    assert validate_excerpts_against_diff(staged, [excerpt], cwd=tmp_path) == []
    excerpt["content"] = excerpt["content"].replace("segment", "forged")
    mismatch = [f"excerpt content mismatch at changed.py:5-10 (line {special_line})"]
    assert validate_excerpts_against_diff(diff, [excerpt], cwd=tmp_path) == mismatch
    assert validate_excerpts_against_diff(staged, [excerpt], cwd=tmp_path) == mismatch


@pytest.mark.parametrize("staged", [False, True])
@pytest.mark.parametrize("eol", ["\n", "\r\n"])
def test_final_bare_cr_is_preserved(tmp_path, staged, eol):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "core.autocrlf", "false")
    before = [f"line {n}" for n in range(1, 31)]
    before[-1] += "\r"
    path = tmp_path / "changed.py"
    path.write_bytes(eol.join(before).encode())
    git(tmp_path, "add", "changed.py")
    git(
        tmp_path, "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", "commit", "-qm", "base"
    )
    after = before.copy()
    after[4] = "changed five"
    after[27] = "changed twenty-eight"
    path.write_bytes(eol.join(after).encode())
    if staged:
        git(tmp_path, "add", "changed.py")
    args = ["--cached"] if staged else []
    diff = subprocess.check_output(["git", "diff", *args, "HEAD"], cwd=tmp_path).decode()
    post, _, _ = _diff_validation_context(diff, cwd=tmp_path)
    assert post["changed.py"][30] == "line 30\r"
    assert post["changed.py"][10] == "line 10"
    section = diff.split("diff --git ", 1)[1]
    assert _reconstruct_post_image(eol.join(before), section) == after
    forged = section.replace(" line 30\r\n", " line 30\n")
    assert forged != section
    assert _reconstruct_post_image(eol.join(before), forged) is None


def test_unstaged_no_final_newline_keeps_immutable_gap(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "commit.gpgsign", "false")
    path = tmp_path / "changed.py"
    before = [f"line {n}" for n in range(1, 31)]
    path.write_text("\n".join(before))
    git(tmp_path, "add", "changed.py")
    git(tmp_path, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "base")
    after = before.copy()
    after[4] = "changed five"
    after[-1] = "changed thirty"
    path.write_text("\n".join(after))
    diff = git(tmp_path, "diff", "-U3", "HEAD", "--", "changed.py")
    assert "\\ No newline at end of file" in diff
    excerpt = {
        "file": "changed.py",
        "start_line": 5,
        "end_line": 10,
        "content": "\n".join(after[4:10]),
    }
    assert validate_excerpts_against_diff(diff, [excerpt], cwd=tmp_path) == []


def _verify_receipt_gap(root, diff, excerpts):
    from code_forge.receipt import write_receipts

    receipts_dir = root / ".code-forge" / "receipts"
    files = write_receipts(
        receipts_dir,
        0,
        [],
        compute_source_hash(git_diff=diff),
        [root / "changed.py"],
        root,
        diff_text=diff,
        diff_files=parse_diff_files(diff),
        reviewer_excerpts=[
            dict(exc, pass_name=name) for name in ("qodo", "expert", "adversarial") for exc in excerpts
        ],
    )
    result = run_verify(
        root,
        compute_source_hash(git_diff=diff),
        parse_diff_files(diff),
        diff_text=diff,
        required_cycles=1,
        respect_floor=False,
    )
    return result, [json.loads(path.read_text()) for path in files]


def test_unstaged_gap_matches_immutable_base(unstaged_gap):
    root, diff, excerpt, other, full = unstaged_gap
    assert len([line for line in diff.splitlines() if line.startswith("@@ ")]) == 2
    assert validate_excerpts_against_diff(diff, [excerpt], cwd=root) == []
    result, receipts = _verify_receipt_gap(root, diff, [excerpt, other, full])
    assert result.passed, result.reason
    assert all(receipt["pass_status"] == "completed" for receipt in receipts)
    post, hunks, _ = _diff_validation_context(diff, cwd=root)
    assert post["changed.py"][10] == "line 10"
    assert hunks["changed.py"][0]["end"] < 10 < hunks["changed.py"][1]["start"]
    (root / "changed.py").write_text("tampered working tree\n")
    assert validate_excerpts_against_diff(diff, [excerpt], cwd=root) == []


def test_unstaged_gap_refuses_fabricated_or_unproven_content(unstaged_gap):
    root, diff, excerpt, _, _ = unstaged_gap
    excerpt["content"] = excerpt["content"].replace("line 10", "forged line")
    errors = validate_excerpts_against_diff(diff, [excerpt], cwd=root)
    assert errors == ["excerpt content mismatch at changed.py:5-10 (line 10)"]
    excerpt["content"] = excerpt["content"].replace("forged line", "line 10")
    # An unresolvable base object is not evidence for an omitted line.
    import re

    broken = re.sub(r"(?<=index )[0-9a-f]+", "f" * 40, diff)
    errors = validate_excerpts_against_diff(broken, [excerpt], cwd=root)
    assert errors == [
        "excerpt changed.py:5-10 claims line 9 outside the diff post-image; it cannot be verified"
    ]
    # The base blob must also agree with every old-side hunk line.
    forged = diff.replace("-line 5", "-invented")
    errors = validate_excerpts_against_diff(forged, [excerpt], cwd=root)
    assert errors == [
        "excerpt changed.py:5-10 claims line 9 outside the diff post-image; it cannot be verified"
    ]


def test_unstaged_gap_receipt_preflight_matches_gate(unstaged_gap, caplog):
    from code_forge.receipt import _warn_on_fabricated_excerpts

    root, diff, excerpt, _, _ = unstaged_gap
    _warn_on_fabricated_excerpts(diff, [excerpt], cwd=root)
    assert "pre-flight" not in caplog.text
    # Missing immutable objects cannot be silently treated as verified gaps.
    import re

    broken = re.sub(r"(?<=index )[0-9a-f]+", "f" * 40, diff)
    _warn_on_fabricated_excerpts(broken, [excerpt], cwd=root)
    assert "references lines 9, 10 not in diff post-image" in caplog.text
    excerpt["content"] = excerpt["content"].replace("line 10", "forged")
    assert validate_excerpts_against_diff(diff, [excerpt], cwd=root)


def test_frozen_blob_supplies_missing_boundary(candidate):
    root, diff, excerpt = candidate
    assert validate_excerpts_against_diff(diff, [excerpt])
    assert validate_excerpts_against_diff(diff, [excerpt], cwd=root) == []
    (root / "CHANGELOG.md").write_text("unrelated live content\n")
    assert validate_excerpts_against_diff(diff, [excerpt], cwd=root) == []


def test_fabricated_boundary_stays_rejected(candidate):
    root, diff, excerpt = candidate
    excerpt["content"] = excerpt["content"].replace("context 36", "fabricated()")
    errs = validate_excerpts_against_diff(diff, [excerpt], cwd=root)
    assert any("content mismatch" in e for e in errs)


def test_crlf_diff_context_enrichment(candidate):
    root, diff, excerpt = candidate
    diff_crlf = diff.replace("\n", "\r\n")
    assert validate_excerpts_against_diff(diff_crlf, [excerpt], cwd=root) == []
    diff_files = parse_diff_files(diff_crlf)
    assert "CHANGELOG.md" in diff_files
    assert not any("\r" in k for k in diff_files)
    hunks, _ = parse_diff_hunks(diff_crlf)
    assert "CHANGELOG.md" in hunks
    assert not any("\r" in k for k in hunks)


def test_whitespace_path_boundary_context(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "commit.gpgsign", "false")
    file = tmp_path / "spaced name.py"
    lines = [f"line {i} = {i}" for i in range(1, 50)]
    file.write_text("\n".join(lines) + "\n")
    git(tmp_path, "add", "spaced name.py")
    git(tmp_path, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "base")
    lines[39] = "line 40 = 'updated'"
    file.write_text("\n".join(lines) + "\n")
    git(tmp_path, "add", "spaced name.py")
    diff = git(tmp_path, "diff", "--cached", "--full-index")
    excerpt = {
        "file": "spaced name.py",
        "start_line": 37,
        "end_line": 44,
        "content": "\n".join(lines[36:44]),
        "rationale": "context",
    }
    assert validate_excerpts_against_diff(diff, [excerpt], cwd=tmp_path) == []
    post, _, _ = _diff_validation_context(diff, cwd=tmp_path)
    assert "spaced name.py" in post
    assert not any("\t" in k for k in post)
    diff_files = parse_diff_files(diff)
    assert "spaced name.py" in diff_files
    assert not any("\t" in k for k in diff_files)
    hunks, _ = parse_diff_hunks(diff)
    assert "spaced name.py" in hunks

    receipts = tmp_path / ".code-forge" / "receipts"
    receipts.mkdir(parents=True)
    sha = compute_source_hash(git_diff=diff)
    for cycle in range(1, 4):
        for pass_n, skill in enumerate(["qodo-review", "code-review-expert", "adversarial-qe"], 1):
            receipt = {
                "cycle": cycle,
                "pass": pass_n,
                "skill": skill,
                "diff_sha256": sha,
                "timestamp": f"2026-09-16T10:{cycle * 3 + pass_n:02d}:00Z",
                "pass_status": "completed",
                "findings_count": 0,
                "findings": [],
                "anchors": [{"file": "spaced name.py", "line": 40}],
                "code_excerpts": [excerpt],
                "covered_line_ranges": [],
            }
            p = receipts / f"receipt-c{cycle}p{pass_n}.json"
            p.write_text(json.dumps(receipt))
    gate = tmp_path / ".code-forge" / "gate.yaml"
    gate.write_text("verify:\n  required_cycles: 3\n")
    res = run_verify(tmp_path, sha, diff_files, diff_text=diff)
    assert res.passed, res.reason


def test_quoted_non_ascii_path_attests_through_run_verify(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "commit.gpgsign", "false")
    name = "café.py"
    file = tmp_path / name
    lines = [f"line {i}" for i in range(1, 20)]
    file.write_text("\n".join(lines) + "\n")
    git(tmp_path, "add", name)
    git(tmp_path, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "base")
    lines[9] = "line 10 changed"
    file.write_text("\n".join(lines) + "\n")
    git(tmp_path, "add", name)
    diff = git(tmp_path, "diff", "--cached", "--full-index")
    assert '+++ "b/' in diff
    excerpt = {
        "file": name,
        "start_line": 8,
        "end_line": 12,
        "content": "\n".join(lines[7:12]),
        "rationale": "context",
    }
    assert validate_excerpts_against_diff(diff, [excerpt], cwd=tmp_path) == []
    diff_files = parse_diff_files(diff)
    hunks, _ = parse_diff_hunks(diff)
    post, _, _ = _diff_validation_context(diff, cwd=tmp_path)
    assert list(diff_files) == [name]
    assert list(hunks) == [name]
    assert list(post) == [name]
    receipts = tmp_path / ".code-forge" / "receipts"
    receipts.mkdir(parents=True)
    sha = compute_source_hash(git_diff=diff)
    for cycle in range(1, 4):
        for pass_n, skill in enumerate(["qodo-review", "code-review-expert", "adversarial-qe"], 1):
            receipt = {
                "cycle": cycle,
                "pass": pass_n,
                "skill": skill,
                "diff_sha256": sha,
                "timestamp": f"2026-09-16T10:{cycle * 3 + pass_n:02d}:00Z",
                "pass_status": "completed",
                "findings_count": 0,
                "findings": [],
                "anchors": [{"file": name, "line": 10}],
                "code_excerpts": [excerpt],
                "covered_line_ranges": [],
            }
            (receipts / f"receipt-c{cycle}p{pass_n}.json").write_text(json.dumps(receipt))
    gate = tmp_path / ".code-forge" / "gate.yaml"
    gate.write_text("verify:\n  required_cycles: 3\n")
    res = run_verify(tmp_path, sha, diff_files, diff_text=diff)
    assert res.passed, res.reason


def test_directory_named_a_keeps_prefix_through_hunks():
    from unidiff import PatchSet

    from code_forge.diff import extract_changed_lines, normalize_diff_path

    diff = (
        "diff --git a/a/x.py b/a/x.py\n"
        "index 1111111..2222222 100644\n"
        "--- a/a/x.py\n"
        "+++ b/a/x.py\n"
        "@@ -1,2 +1,3 @@\n"
        " def f():\n"
        "+    return 1\n"
        "     pass\n"
    )
    unidiff_key = next(iter(PatchSet(diff))).path
    assert unidiff_key == "a/x.py"
    assert normalize_diff_path(unidiff_key) == "a/x.py"
    assert list(parse_diff_files(diff)) == ["a/x.py"]
    assert list(parse_diff_hunks(diff)[0]) == ["a/x.py"]
    assert list(extract_changed_lines(diff)) == ["a/x.py"]


def test_added_plus_plus_line_is_not_a_new_file():
    from code_forge.diff import path_from_plus_header

    assert path_from_plus_header("+++ i;") is None
    assert path_from_plus_header("+++ b/inc.cpp") == "inc.cpp"
    diff = (
        "diff --git a/inc.cpp b/inc.cpp\n"
        "index 1111111..2222222 100644\n"
        "--- a/inc.cpp\n"
        "+++ b/inc.cpp\n"
        "@@ -1,3 +1,4 @@\n"
        " int main() {\n"
        "-return 0;\n"
        "+++ i;\n"
        "+return 0;\n"
        " }\n"
        "@@ -20,3 +21,4 @@\n"
        " void other() {\n"
        "     return;\n"
        "+}\n"
        " }\n"
    )
    files = parse_diff_files(diff)
    assert list(files) == ["inc.cpp"]
    post, hunks, _ = _diff_validation_context(diff)
    assert list(post) == ["inc.cpp"]
    assert list(hunks) == ["inc.cpp"]


def test_latin1_c_quoted_octal_does_not_raise():
    from code_forge.diff import path_from_plus_header, unquote_git_path

    decoded = unquote_git_path(r"caf\351.py")
    assert decoded.encode("utf-8", "surrogateescape")[3] == 0xE9
    assert path_from_plus_header('+++ "b/caf\\351.py"') == decoded
    diff = (
        'diff --git "a/caf\\351.py" "b/caf\\351.py"\n'
        "index 1111111..2222222 100644\n"
        '--- "a/caf\\351.py"\n'
        '+++ "b/caf\\351.py"\n'
        "@@ -1 +1,2 @@\n"
        " old\n"
        "+new\n"
    )
    assert list(parse_diff_files(diff)) == [decoded]
    assert list(parse_diff_hunks(diff)[0]) == [decoded]


def test_quoted_path_under_dir_a_keeps_prefix(tmp_path):
    from code_forge.diff import extract_changed_lines

    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "commit.gpgsign", "false")
    rel = "a/café x.py"
    path = tmp_path / "a"
    path.mkdir()
    file = path / "café x.py"
    lines = [f"line {i}" for i in range(1, 20)]
    file.write_text("\n".join(lines) + "\n")
    git(tmp_path, "add", rel)
    git(tmp_path, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "base")
    lines[9] = "line 10 changed"
    file.write_text("\n".join(lines) + "\n")
    git(tmp_path, "add", rel)
    diff = git(tmp_path, "diff", "--cached", "--full-index")
    assert '+++ "b/' in diff
    keys = {
        "files": list(parse_diff_files(diff)),
        "hunks": list(parse_diff_hunks(diff)[0]),
        "changed": list(extract_changed_lines(diff)),
        "dvc": list(_diff_validation_context(diff)[0]),
    }
    assert keys["files"] == [rel]
    assert keys["hunks"] == [rel]
    assert keys["changed"] == [rel]
    assert keys["dvc"] == [rel]
    excerpt = {
        "file": rel,
        "start_line": 8,
        "end_line": 12,
        "content": "\n".join(lines[7:12]),
        "rationale": "context",
    }
    assert validate_excerpts_against_diff(diff, [excerpt], cwd=tmp_path) == []
    receipts = tmp_path / ".code-forge" / "receipts"
    receipts.mkdir(parents=True)
    sha = compute_source_hash(git_diff=diff)
    for cycle in range(1, 4):
        for pass_n, skill in enumerate(["qodo-review", "code-review-expert", "adversarial-qe"], 1):
            receipt = {
                "cycle": cycle,
                "pass": pass_n,
                "skill": skill,
                "diff_sha256": sha,
                "timestamp": f"2026-09-16T10:{cycle * 3 + pass_n:02d}:00Z",
                "pass_status": "completed",
                "findings_count": 0,
                "findings": [],
                "anchors": [{"file": rel, "line": 10}],
                "code_excerpts": [excerpt],
                "covered_line_ranges": [],
            }
            (receipts / f"receipt-c{cycle}p{pass_n}.json").write_text(json.dumps(receipt))
    (tmp_path / ".code-forge" / "gate.yaml").write_text("verify:\n  required_cycles: 3\n")
    res = run_verify(tmp_path, sha, parse_diff_files(diff), diff_text=diff)
    assert res.passed, res.reason


def test_hunk_body_plus_plus_b_is_not_a_new_file(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "commit.gpgsign", "false")
    file = tmp_path / "doc.md"
    lines = [f"line{i}" for i in range(1, 30)]
    file.write_text("\n".join(lines) + "\n")
    git(tmp_path, "add", "doc.md")
    git(tmp_path, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "base")
    lines[1] = "++ b/evil.py"
    lines[20] = "second hunk change"
    file.write_text("\n".join(lines) + "\n")
    git(tmp_path, "add", "doc.md")
    diff = git(tmp_path, "diff", "--cached", "--full-index", "-U2")
    assert list(parse_diff_files(diff)) == ["doc.md"]
    post, hunks, _ = _diff_validation_context(diff)
    assert list(post) == ["doc.md"]
    assert list(hunks) == ["doc.md"]
    assert list(parse_diff_hunks(diff)[0]) == ["doc.md"]
    assert post["doc.md"][2] == "++ b/evil.py"


def test_unquoted_path_with_b_slash_directory(tmp_path):
    from code_forge.diff import extract_changed_lines, path_from_git_header

    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "commit.gpgsign", "false")
    rel = "foo b/bar.py"
    path = tmp_path / "foo b"
    path.mkdir()
    file = path / "bar.py"
    lines = [f"line {i}" for i in range(1, 20)]
    file.write_text("\n".join(lines) + "\n")
    git(tmp_path, "add", rel)
    git(tmp_path, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "base")
    lines[9] = "changed"
    file.write_text("\n".join(lines) + "\n")
    git(tmp_path, "add", rel)
    diff = git(tmp_path, "diff", "--cached", "--full-index")
    gitline = next(ln for ln in diff.splitlines() if ln.startswith("diff --git"))
    assert path_from_git_header(gitline) == rel
    assert list(parse_diff_files(diff)) == [rel]
    assert list(parse_diff_hunks(diff)[0]) == [rel]
    assert list(extract_changed_lines(diff)) == [rel]
    assert list(_diff_validation_context(diff)[0]) == [rel]
    excerpt = {
        "file": rel,
        "start_line": 8,
        "end_line": 12,
        "content": "\n".join(lines[7:12]),
        "rationale": "context",
    }
    assert validate_excerpts_against_diff(diff, [excerpt], cwd=tmp_path) == []
    receipts = tmp_path / ".code-forge" / "receipts"
    receipts.mkdir(parents=True)
    sha = compute_source_hash(git_diff=diff)
    for cycle in range(1, 4):
        for pass_n, skill in enumerate(["qodo-review", "code-review-expert", "adversarial-qe"], 1):
            receipt = {
                "cycle": cycle,
                "pass": pass_n,
                "skill": skill,
                "diff_sha256": sha,
                "timestamp": f"2026-09-16T10:{cycle * 3 + pass_n:02d}:00Z",
                "pass_status": "completed",
                "findings_count": 0,
                "findings": [],
                "anchors": [{"file": rel, "line": 10}],
                "code_excerpts": [excerpt],
                "covered_line_ranges": [],
            }
            (receipts / f"receipt-c{cycle}p{pass_n}.json").write_text(json.dumps(receipt))
    (tmp_path / ".code-forge" / "gate.yaml").write_text("verify:\n  required_cycles: 3\n")
    res = run_verify(tmp_path, sha, parse_diff_files(diff), diff_text=diff)
    assert res.passed, res.reason


def test_unquoted_b_slash_path_rejects_fabricated_excerpt(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "commit.gpgsign", "false")
    rel = "foo b/bar.py"
    path = tmp_path / "foo b"
    path.mkdir()
    file = path / "bar.py"
    lines = [f"line {i}" for i in range(1, 20)]
    file.write_text("\n".join(lines) + "\n")
    git(tmp_path, "add", rel)
    git(tmp_path, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "base")
    lines[9] = "changed"
    file.write_text("\n".join(lines) + "\n")
    git(tmp_path, "add", rel)
    diff = git(tmp_path, "diff", "--cached", "--full-index")
    hunks, exempt = parse_diff_hunks(diff)
    assert rel in hunks
    assert rel not in exempt
    fake = {
        "file": rel,
        "start_line": 8,
        "end_line": 12,
        "content": "TOTALLY FAKE\n" * 5,
        "rationale": "probe",
    }
    assert validate_excerpts_against_diff(diff, [fake], cwd=tmp_path)
    receipts = tmp_path / ".code-forge" / "receipts"
    receipts.mkdir(parents=True)
    sha = compute_source_hash(git_diff=diff)
    for cycle in range(1, 4):
        for pass_n, skill in enumerate(["qodo-review", "code-review-expert", "adversarial-qe"], 1):
            receipt = {
                "cycle": cycle,
                "pass": pass_n,
                "skill": skill,
                "diff_sha256": sha,
                "timestamp": f"2026-09-16T10:{cycle * 3 + pass_n:02d}:00Z",
                "pass_status": "completed",
                "findings_count": 0,
                "findings": [],
                "anchors": [{"file": rel, "line": 10}],
                "code_excerpts": [fake],
                "covered_line_ranges": [],
            }
            (receipts / f"receipt-c{cycle}p{pass_n}.json").write_text(json.dumps(receipt))
    (tmp_path / ".code-forge" / "gate.yaml").write_text("verify:\n  required_cycles: 3\n")
    res = run_verify(tmp_path, sha, parse_diff_files(diff), diff_text=diff)
    assert not res.passed
    assert "mismatch" in res.reason or "excerpt" in res.reason


def test_added_line_starting_with_plus_stays_in_post_image(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "commit.gpgsign", "false")
    file = tmp_path / "u.py"
    lines = [f"line {i}" for i in range(1, 20)]
    file.write_text("\n".join(lines) + "\n")
    git(tmp_path, "add", "u.py")
    git(tmp_path, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "base")
    lines[5] = "+unary"
    file.write_text("\n".join(lines) + "\n")
    git(tmp_path, "add", "u.py")
    diff = git(tmp_path, "diff", "--cached", "--full-index")
    excerpt = {
        "file": "u.py",
        "start_line": 5,
        "end_line": 8,
        "content": "\n".join(lines[4:8]),
        "rationale": "context",
    }
    assert validate_excerpts_against_diff(diff, [excerpt], cwd=tmp_path) == []
    post, _, _ = _diff_validation_context(diff, cwd=tmp_path)
    assert post["u.py"][6] == "+unary"


def test_unavailable_blob_does_not_invent_context(candidate):
    root, diff, excerpt = candidate
    import re

    diff = re.sub(r"(?<=index )[0-9a-f]{40}", "e" * 40, diff)
    diff = re.sub(r"(?<=\.\.)[0-9a-f]{40}", "f" * 40, diff)
    assert validate_excerpts_against_diff(diff, [excerpt], cwd=root)


def test_blob_must_agree_with_frozen_hunk(candidate):
    root, diff, excerpt = candidate
    diff = diff.replace("+Document the new retention setting.", "+different change")
    assert validate_excerpts_against_diff(diff, [excerpt], cwd=root)


def test_context_does_not_expand_hunk_witness_scope(candidate):
    root, diff, _excerpt = candidate
    before = _diff_validation_context(diff)
    after = _diff_validation_context(diff, cwd=root)
    assert after[1:] == before[1:]
    assert 36 not in before[0]["CHANGELOG.md"]
    assert after[0]["CHANGELOG.md"][36] == "context 36"


def test_terminal_gate_uses_same_frozen_context(candidate):
    root, diff, excerpt = candidate
    receipts = root / ".code-forge" / "receipts"
    receipts.mkdir(parents=True)
    sha = compute_source_hash(git_diff=diff)
    for cycle in range(1, 4):
        for pass_n, skill in enumerate(["qodo-review", "code-review-expert", "adversarial-qe"], 1):
            receipt = {
                "cycle": cycle,
                "pass": pass_n,
                "skill": skill,
                "diff_sha256": sha,
                "timestamp": f"2026-09-16T10:{cycle * 3 + pass_n:02d}:00Z",
                "pass_status": "completed",
                "findings_count": 0,
                "findings": [],
                "anchors": [],
                "code_excerpts": [excerpt],
                "covered_line_ranges": [],
            }
            (receipts / f"receipt-c{cycle}p{pass_n}.json").write_text(json.dumps(receipt))
    result = run_verify(root, sha, parse_diff_files(diff), diff_text=diff)
    assert result.passed, result.reason


def test_receipt_producer_accepts_frozen_context(candidate):
    from code_forge.receipt import write_receipts

    root, diff, excerpt = candidate
    files = write_receipts(
        root / ".code-forge" / "receipts",
        0,
        [],
        compute_source_hash(git_diff=diff),
        [],
        root,
        diff_text=diff,
        reviewer_excerpts=[dict(excerpt, pass_name=p) for p in ("qodo", "expert", "adversarial")],
    )
    assert len(files) == 3
    assert all(json.loads(f.read_text())["pass_status"] == "completed" for f in files)


def test_machine_round_and_quality_use_frozen_context(candidate):
    from code_forge.machine import StateMachine

    root, diff, excerpt = candidate
    machine = object.__new__(StateMachine)
    machine.cwd = root
    machine._receipt_diff = lambda: diff
    machine._last_receipt_write_errors = []
    machine._attempted_last_round = []
    machine._unavailable_rejected_passes_last_round = set()
    machine._excerpts_last_round = [excerpt]
    assert machine._receipt_gate_round_errors() == []
    assert machine._downgrade_one_line_slips([], [excerpt]) == ([], [excerpt])
    # No blank separator means a quality warning, not an infrastructure error.
    lines = (root / "CHANGELOG.md").read_text().splitlines()
    lines[43] = "nonblank boundary"
    (root / "CHANGELOG.md").write_text("\n".join(lines) + "\n")
    git(root, "add", "CHANGELOG.md")
    diff = git(root, "diff", "--cached", "--full-index")
    findings, kept = machine._downgrade_one_line_slips([], [excerpt])
    assert kept == [excerpt]
    assert len(findings) == 1
    assert findings[0].id == "RECEIPT_UNTRUSTED"


def test_hunk_deleted_line_starting_with_dashes_is_not_a_file_header():
    """A deleted '-- target' must not be skipped as a '--- ' file header."""
    from code_forge.verify import _diff_validation_context

    diff = (
        "diff --git a/Makefile b/Makefile\n"
        "--- a/Makefile\n"
        "+++ b/Makefile\n"
        "@@ -1,3 +1,2 @@\n"
        " keep\n"
        "--- target\n"
        " stay\n"
    )
    post, hunks, _ = _diff_validation_context(diff)
    assert list(post) == ["Makefile"]
    assert post["Makefile"][1] == "keep"
    assert post["Makefile"][2] == "stay"
    assert hunks["Makefile"][0]["start"] == 1
    assert hunks["Makefile"][0]["end"] == 2


def test_blob_reader_rejects_non_string_oid(candidate):
    from code_forge.git import read_diff_blob

    root, _diff, _excerpt = candidate
    assert read_diff_blob(None, root) is None
    assert read_diff_blob(123, root) is None


@pytest.mark.parametrize("oid", ["HEAD:CHANGELOG.md", "--help", "0" * 40, "zzz"])
def test_blob_selector_rejects_non_object_syntax(candidate, oid):
    from code_forge.git import read_diff_blob

    root, _diff, _excerpt = candidate
    assert read_diff_blob(oid, root) is None


@pytest.mark.parametrize(
    "content",
    [b"binary\x00payload", b"\xff", b"a" * 2_000_001],
    ids=["binary", "invalid-utf8", "oversized"],
)
def test_blob_reader_rejects_non_text_or_oversized(candidate, content):
    from code_forge.git import read_diff_blob

    root, _diff, _excerpt = candidate
    result = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"], cwd=root, input=content, capture_output=True, check=True
    )
    assert read_diff_blob(result.stdout.decode().strip(), root) is None


def test_non_blob_object_is_not_source(candidate):
    from code_forge.git import read_diff_blob

    root, _diff, _excerpt = candidate
    assert read_diff_blob(git(root, "rev-parse", "HEAD").strip(), root) is None


def test_git_replacement_does_not_substitute_source(candidate):
    from code_forge.git import read_diff_blob

    root, _diff, _excerpt = candidate
    original = git(root, "rev-parse", ":CHANGELOG.md").strip()
    replacement = git(root, "rev-parse", "HEAD:CHANGELOG.md").strip()
    git(root, "replace", original, replacement)
    assert "new retention setting" in read_diff_blob(original, root)


def test_missing_git_falls_back(candidate, monkeypatch):
    from code_forge.git import read_diff_blob

    root, _diff, _excerpt = candidate
    oid = git(root, "rev-parse", ":CHANGELOG.md").strip()
    monkeypatch.setenv("PATH", "")
    assert read_diff_blob(oid, root) is None


def test_deleted_file_does_not_load_post_image(candidate):
    from code_forge.verify import _diff_validation_context

    root, _diff, _excerpt = candidate
    git(root, "reset", "--", "CHANGELOG.md")
    git(root, "rm", "-f", "CHANGELOG.md")
    diff = git(root, "diff", "--cached", "--full-index")
    assert _diff_validation_context(diff, cwd=root) == ({}, {}, [])
    # A deletion-only hunk in a surviving file has no literal post-image.
    diff = diff.replace("+++ /dev/null", "+++ b/CHANGELOG.md")
    post, _hunks, exempt = _diff_validation_context(diff, cwd=root)
    assert post == {"CHANGELOG.md": {}}
    assert exempt == ["CHANGELOG.md"]
