"""Local planning regressions; no model calls or actual review evidence."""

from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest

from code_forge.diff_grouping import Group, GroupingResult
from code_forge.grouped_coverage import reconcile_grouped_coverage


def patch(path, old="old", new="new"):
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-{old}\n+{new}\n"


def group(name, members, passes=3):
    return Group(name, name, members, passes)


def test_promotes_only_required_groups_and_preserves_inputs():
    deleted = (
        "diff --git a/empty.md b/empty.md\n--- a/empty.md\n+++ b/empty.md\n@@ -1,2 +1 @@\n keep\n-drop\n"
    )
    diff = patch("src/a.py") + patch("README.md") + deleted
    grouping = GroupingResult(
        groups=[
            group("code", ["src/a.py"]),
            group("docs", ["README.md"], 0),
            group("exempt", ["empty.md"], 0),
        ]
    )
    before = deepcopy(grouping)
    plan = reconcile_grouped_coverage(diff, grouping)
    assert [(p.name, p.passes, p.provenance) for p in plan] == [
        ("code", 3, "semantic"),
        ("docs", 3, "promoted"),
    ]
    assert grouping == before
    grouping.groups[0].members.append("later.py")
    assert plan[0].members == ("src/a.py",)
    with pytest.raises(FrozenInstanceError):
        plan[0].name = "changed"


def test_all_zero_groups_and_absent_sem_get_real_plans():
    diff = patch("z.yaml") + patch("a.md") + patch("src/c.py")
    plan = reconcile_grouped_coverage(diff, GroupingResult(groups=[group("config", ["z.yaml"], 0)]))
    assert [p.members for p in plan] == [("z.yaml",), ("a.md",), ("src/c.py",)]
    assert [p.provenance for p in plan] == ["promoted", "non_semantic_fallback", "non_semantic_fallback"]
    assert all(p.passes == 3 and p.members[0] in p.name for p in plan[1:])
    assert len(reconcile_grouped_coverage(diff, GroupingResult())) == 3


@pytest.mark.parametrize("passes", [True, False, "3", 1, 2, -1, 3.0, None])
def test_invalid_pass_budget_rejected(passes):
    with pytest.raises(ValueError, match="passes"):
        reconcile_grouped_coverage(patch("a.py"), GroupingResult(groups=[group("a", ["a.py"], passes)]))


@pytest.mark.parametrize(
    "groups",
    [
        [group("one", ["a.py", "a.py"])],
        [group("one", ["a.py"]), group("two", ["a.py"], 0)],
        [group("one", ["unknown.py"], 0)],
        [group("one", [""])],
        [group("one", [None])],
        [group("one", "a.py")],
        [group("one", [])],
        [group("", ["a.py"])],
        [object()],
    ],
)
def test_invalid_membership_rejected_including_zero_pass(groups):
    with pytest.raises(ValueError):
        reconcile_grouped_coverage(patch("a.py"), GroupingResult(groups=groups))


@pytest.mark.parametrize(
    "diff",
    [
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,4 +1,4 @@\n-old\n+new\n",
        "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n",
    ],
)
def test_malformed_or_unsliceable_required_diff_rejected(diff):
    with pytest.raises(ValueError):
        reconcile_grouped_coverage(diff, GroupingResult())


@pytest.mark.parametrize(
    "transform", [lambda s: "", lambda s: s.replace("+new", "+bad"), lambda s: s.split("@@ -9")[0]]
)
def test_corrupt_or_truncated_splitter_rejected(monkeypatch, transform):
    import code_forge.grouped_coverage as coverage

    diff = patch("a.py") + "@@ -9 +9 @@\n-old\n+second\n"
    real = coverage.split_diff_for_files
    monkeypatch.setattr(coverage, "split_diff_for_files", lambda d, m: transform(real(d, m)))
    with pytest.raises(ValueError):
        reconcile_grouped_coverage(diff, GroupingResult())


def test_multiple_hunks_mixed_deletion_and_same_path_frames_preserved():
    diff = patch("a.py") + "@@ -9,2 +9 @@\n keep\n-drop\n@@ -20 +19 @@\n-old\n+new\n"
    assert reconcile_grouped_coverage(diff, GroupingResult())[0].diff_text == diff
    # Existing parser is last-frame-wins; preserve bytes without claiming to fix it.
    repeated = patch("a.py") + patch("a.py", "old2", "new2")
    assert reconcile_grouped_coverage(repeated, GroupingResult())[0].diff_text == repeated


@pytest.mark.parametrize("path", ["with spaces.py", "a/real.py", "目录.py"])
def test_exact_path_identity(path):
    diff = patch(path)
    plan = reconcile_grouped_coverage(diff, GroupingResult())
    assert plan[0].members == (path,)
    assert plan[0].diff_text == diff


def test_rename_target_and_quoted_path():
    diff = 'diff --git "a/old file.py" "b/new file.py"\nsimilarity index 80%\nrename from old file.py\nrename to new file.py\n--- "a/old file.py"\n+++ "b/new file.py"\n@@ -1 +1 @@\n-old\n+new\n'
    plan = reconcile_grouped_coverage(diff, GroupingResult())
    assert plan[0].members == ("new file.py",)
    assert plan[0].diff_text == diff
    with pytest.raises(ValueError, match="unknown"):
        reconcile_grouped_coverage(diff, GroupingResult(groups=[group("old", ["old file.py"])]))


@pytest.mark.parametrize(
    "diff,path",
    [
        ("diff --git a/a.bin b/a.bin\nBinary files a/a.bin and b/a.bin differ\n", "a.bin"),
        ("diff --git a/a.py b/a.py\nold mode 100644\nnew mode 100755\n", "a.py"),
        (
            "diff --git a/old.py b/new.py\nsimilarity index 100%\nrename from old.py\nrename to new.py\n",
            "new.py",
        ),
        (
            "diff --git a/a.py b/a.py\ndeleted file mode 100644\nindex aaaa..0000\n--- a/a.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n",
            "a.py",
        ),
        ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,2 +1 @@\n keep\n-drop\n", "a.py"),
    ],
)
def test_exempt_omissions_and_zero_groups_stay_exempt_but_positive_preserved(diff, path):
    assert reconcile_grouped_coverage(diff, GroupingResult()) == ()
    assert reconcile_grouped_coverage(diff, GroupingResult(groups=[group("exempt", [path], 0)])) == ()
    plan = reconcile_grouped_coverage(diff, GroupingResult(groups=[group("positive", [path])]))
    assert len(plan) == 1 and plan[0].diff_text == diff


def test_type_replacement_retains_deleted_and_added_frames():
    deleted = "diff --git a/a.py b/a.py\ndeleted file mode 100644\nindex aaaa..0000\n--- a/a.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n"
    added = "diff --git a/a.py b/a.py\nnew file mode 120000\nindex 0000..bbbb\n--- /dev/null\n+++ b/a.py\n@@ -0,0 +1 @@\n+target\n"
    plan = reconcile_grouped_coverage(deleted + added, GroupingResult())
    assert len(plan) == 1 and plan[0].diff_text == deleted + added


def test_repeated_hunk_coordinates_preserve_occurrence_counts():
    import code_forge.grouped_coverage as coverage

    diff = patch("a.py") + "@@ -1 +1 @@\n-old\n+new\n"
    original = coverage._obligations(diff)
    assert list(original.values()) == [2]
    assert reconcile_grouped_coverage(diff, GroupingResult())[0].diff_text == diff


@pytest.mark.parametrize("invalid", [None, False, 0, [], {}])
def test_public_original_validation_rejects_nontext(invalid):
    from code_forge.grouped_coverage import GroupedCoverageError, validate_grouped_diff

    with pytest.raises(GroupedCoverageError, match="diff must be text"):
        validate_grouped_diff(invalid)


def test_public_original_validation_rejects_malformed_required_hunk():
    from code_forge.grouped_coverage import GroupedCoverageError, validate_grouped_diff

    malformed = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,4 +1,4 @@\n-old\n+new\n"
    with pytest.raises(GroupedCoverageError, match="diff parse failed"):
        validate_grouped_diff(malformed)


@pytest.mark.parametrize(
    "diff",
    [
        "",
        " \n",
        "diff --git a/README.md b/README.md\nold mode 100644\nnew mode 100755\n",
        "diff --git a/a.bin b/a.bin\nBinary files a/a.bin and b/a.bin differ\n",
        "diff --git a/old.py b/new.py\nsimilarity index 100%\nrename from old.py\nrename to new.py\n",
        "diff --git a/a.py b/a.py\ndeleted file mode 100644\nindex aaaa..0000\n"
        "--- a/a.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n",
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,2 +1 @@\n keep\n-drop\n",
    ],
)
def test_public_original_validation_preserves_exemptions(diff):
    from collections import Counter
    from code_forge.grouped_coverage import validate_grouped_diff

    assert validate_grouped_diff(diff) == Counter()


@pytest.mark.parametrize("path", ["options.yaml", "README.md"])
def test_public_original_validation_requires_added_config_and_docs(path):
    from collections import Counter
    from code_forge.grouped_coverage import validate_grouped_diff

    diff = f"diff --git a/{path} b/{path}\n--- /dev/null\n+++ b/{path}\n@@ -0,0 +1 @@\n+new\n"
    assert validate_grouped_diff(diff) == Counter({(path, 1, 1, (1,)): 1})
