"""Phase 57-3: which instances become entries, and which are refused.

Every threshold here is pinned rather than left to the implementer,
because a threshold chosen after seeing the yield is a threshold fitted to
a result. The tests assert the predicates individually so that a later
change to one cannot quietly ride in as a change to the corpus.

The distinction the plan turns on, and which these tests encode:

- Qualification predicates decide whether an instance is a valid test case
  at all. Relaxing one to raise the count is forbidden.
- Allocation parameters distribute instances that have ALREADY qualified.
  The per-repo cap is one, and correcting it takes nothing in or out of
  the qualified pool.
"""


from code_forge.eval.swebench import (
    RejectReason,
    qualifies,
    select_instances,
)


def _inst(
    instance_id="repo__proj-1",
    repo="org/proj",
    patch=None,
    problem_statement="A real defect title that is long enough",
):
    if patch is None:
        patch = (
            "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n"
            "@@ -2,3 +2,3 @@\n ctx\n-bad\n+good\n tail\n"
        )
    return {
        "instance_id": instance_id,
        "repo": repo,
        "patch": patch,
        "problem_statement": problem_statement,
    }


class TestAccepts:
    def test_an_ordinary_instance_qualifies(self):
        assert qualifies(_inst()) is None


class TestQualificationPredicates:
    def test_rejects_a_patch_with_no_source_files(self):
        p = (
            "diff --git a/docs/x.md b/docs/x.md\n--- a/docs/x.md\n+++ b/docs/x.md\n"
            "@@ -1,2 +1,2 @@\n ctx\n-old\n+new\n"
        )
        assert qualifies(_inst(patch=p)) is RejectReason.NO_SOURCE_FILES

    def test_rejects_a_test_only_patch(self):
        p = (
            "diff --git a/tests/test_x.py b/tests/test_x.py\n"
            "--- a/tests/test_x.py\n+++ b/tests/test_x.py\n"
            "@@ -1,2 +1,2 @@\n ctx\n-old\n+new\n"
        )
        assert qualifies(_inst(patch=p)) is RejectReason.NO_SOURCE_FILES

    def test_rejects_more_than_three_files(self):
        p = "".join(
            "diff --git a/f%d.py b/f%d.py\n--- a/f%d.py\n+++ b/f%d.py\n"
            "@@ -1,2 +1,2 @@\n ctx\n-old\n" % (i, i, i, i)
            for i in range(4)
        )
        assert qualifies(_inst(patch=p)) is RejectReason.TOO_MANY_FILES

    def test_rejects_more_than_five_hunks(self):
        head = "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n"
        body = "".join(
            "@@ -%d,2 +%d,2 @@\n ctx\n-old\n" % (i * 10, i * 10) for i in range(6)
        )
        assert qualifies(_inst(patch=head + body)) is RejectReason.TOO_MANY_HUNKS

    def test_rejects_an_oversized_hunk(self):
        head = "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n"
        removed = "".join("-line %d\n" % i for i in range(51))
        p = head + "@@ -1,52 +1,1 @@\n ctx\n" + removed
        assert qualifies(_inst(patch=p)) is RejectReason.HUNK_TOO_LARGE

    def test_rejects_a_short_title(self):
        got = qualifies(_inst(problem_statement="Broken\n\nmore text"))
        assert got is RejectReason.UNUSABLE_STATEMENT

    def test_rejects_an_empty_statement(self):
        assert qualifies(_inst(problem_statement="  \n\n")) is (
            RejectReason.UNUSABLE_STATEMENT
        )

    def test_rejects_a_traceback_as_the_title(self):
        stmt = 'Traceback (most recent call last):\n  File "x.py", line 1\n'
        assert qualifies(_inst(problem_statement=stmt)) is (
            RejectReason.UNUSABLE_STATEMENT
        )

    def test_rejects_a_pure_feature_addition(self):
        """Reversing it removes a working feature, which is not a defect.

        This is the asymmetry the plan keeps out of the corpus: a reviewer
        seeing the reversal reads deliberate scope reduction, not an
        oversight, and scoring it as a missed defect measures nothing.
        """
        p = (
            "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n"
            "@@ -1,1 +1,3 @@\n ctx\n+new one\n+new two\n"
        )
        assert qualifies(_inst(patch=p)) is RejectReason.PURE_ADDITION

    def test_a_guard_removal_is_kept(self):
        """The other asymmetry, which IS a real review task.

        A fix that adds a check reverses into one that deletes it. The
        reviewer sees a deletion rather than an omission -- a different
        cognitive task, but still a defect a reviewer should flag.
        """
        p = (
            "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n"
            "@@ -5,2 +5,3 @@\n ctx\n+    if x is None: raise\n-old\n"
        )
        assert qualifies(_inst(patch=p)) is None


class TestSelection:
    """Allocation, not qualification.

    Nothing here may take an instance in or out of the qualified pool;
    these tests exist to keep that boundary visible.
    """

    def _pool(self, per_repo):
        out = []
        for repo, n in per_repo.items():
            for i in range(n):
                out.append(_inst(instance_id="%s-%d" % (repo, i), repo=repo))
        return out

    def test_caps_each_repo(self):
        picked = select_instances(self._pool({"a/a": 40, "b/b": 40}), cap=8)
        counts = {}
        for inst in picked:
            counts[inst["repo"]] = counts.get(inst["repo"], 0) + 1
        assert counts == {"a/a": 8, "b/b": 8}

    def test_a_small_repo_contributes_all_it_has(self):
        picked = select_instances(self._pool({"a/a": 40, "small/one": 2}), cap=8)
        counts = {}
        for inst in picked:
            counts[inst["repo"]] = counts.get(inst["repo"], 0) + 1
        assert counts["small/one"] == 2

    def test_selection_is_deterministic(self):
        pool = self._pool({"a/a": 40, "b/b": 40})
        first = [i["instance_id"] for i in select_instances(pool, cap=8)]
        second = [i["instance_id"] for i in select_instances(pool, cap=8)]
        assert first == second

    def test_input_order_does_not_change_the_result(self):
        """A seeded sample over an unsorted pool is not reproducible.

        The ids are sorted before sampling for exactly this reason: the
        dataset's iteration order is not a contract, and a corpus that
        depends on it cannot be regenerated.
        """
        pool = self._pool({"a/a": 40, "b/b": 40})
        forward = [i["instance_id"] for i in select_instances(pool, cap=8)]
        backward = [i["instance_id"] for i in select_instances(pool[::-1], cap=8)]
        assert forward == backward

    def test_cap_is_an_allocation_parameter_not_a_filter(self):
        # Raising the cap adds already-qualified instances and never
        # changes which ones qualify.
        pool = self._pool({"a/a": 40, "b/b": 40})
        small = {i["instance_id"] for i in select_instances(pool, cap=5)}
        large = {i["instance_id"] for i in select_instances(pool, cap=8)}
        assert small < large


class TestRejectReasonsAreCountable:
    """Every rejection must be attributable in the provenance record.

    A corpus that reports only its survivors cannot be audited: the
    reader cannot tell a strict filter from a broken one.
    """

    def test_every_reason_is_distinct(self):
        values = [r.value for r in RejectReason]
        assert len(values) == len(set(values))

    def test_reasons_are_stable_strings(self):
        # They are written into the provenance file, so they are a
        # persisted contract rather than an internal label.
        assert RejectReason.PURE_ADDITION.value == "pure_addition"
        assert RejectReason.NO_SOURCE_FILES.value == "no_source_files"
