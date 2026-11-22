"""Tests for scripts/noqa_suppressions.py.

Each test pins a way the comparison could lie: a directive the tokenizer must
not mistake for a comment, an anchor that is not unique, a path inventory that
changed under the comparison, or a Git object that cannot be read. The
controlled samples are built in place so the expectations never depend on this
repository's own history.
"""

from __future__ import annotations

import unittest

import importlib.util
import pathlib
import sys
from hashlib import sha256


def _module():
    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "noqa_suppressions.py"
    # Load under the repo-relative dotted name so a mutant keyed as
    # scripts.noqa_suppressions.<fn> can match the trampoline hits this module
    # records; a bare name never matches a mutant key.
    name = "scripts.noqa_suppressions"
    if not path.exists():
        for parent in pathlib.Path(__file__).resolve().parents:
            candidate = parent / "scripts" / "noqa_suppressions.py"
            if parent.name != "mutants" and candidate.exists():
                path = candidate
                break
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    # dataclass reads its own module out of sys.modules while building the
    # class, so the module has to be registered before it runs.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


suppressions = _module()
classify = suppressions.classify
compare = suppressions.compare
measure = suppressions.measure


class Controls(unittest.TestCase):
    def test_fake_comments(self):
        self.assertFalse(classify(b'"""# noqa: F401"""\nx = "# noqa"  # not a noqa directive\n')[0])

    def test_explicit_unique_site_and_codes(self):
        c, r = classify(b"x = 1  # noqa: BLE001, S110, S110 - reviewed\n# noqa: BLE001  another\n")
        self.assertEqual(c["explicit_sites"], 2)
        self.assertEqual(r, {"BLE001": 2, "S110": 1})

    def test_blanket_file_and_unclassified_separate(self):
        c, r = classify(b"# noqa\n# ruff: noqa\n# noqa: invalid\n# noqax\n")
        self.assertEqual(c, {"blanket_sites": 1, "excluded_file_directives": 1, "unclassified_sites": 1})
        self.assertFalse(r)

    def test_encoding_and_crlf(self):
        self.assertEqual(
            classify(b'# coding: latin-1\r\nx="\xe9" # noqa: F401\r\n')[0]["explicit_sites"], 1
        )

    def test_invalid_python(self):
        with self.assertRaises(SyntaxError):
            classify(b"if as:  # noqa\n")

    def test_a_failed_read_reaps_the_git_child(self):
        from unittest.mock import MagicMock, patch

        fake = MagicMock()
        fake.communicate.side_effect = MemoryError()
        fake.pid = 4242
        fake.stdout = MagicMock()
        with (
            patch.object(suppressions.subprocess, "Popen", return_value=fake),
            patch.object(suppressions.os, "killpg") as killpg,
        ):
            with self.assertRaises(MemoryError):
                suppressions.git("/tmp", "cat-file", "blob", "abc")
        killpg.assert_called_once_with(4242, suppressions.signal.SIGKILL)
        fake.wait.assert_called_once()
        fake.stdout.close.assert_called_once()

    def test_missing_commit(self):
        with self.assertRaises(ValueError):
            measure(".", "f" * 40)

    def test_visible_unavailable(self):
        self.assertEqual(
            compare({"status": "unavailable"}, {"status": "measured"}), {"status": "unavailable"}
        )

    def test_scope_drift(self):
        a = dict(
            status="measured",
            definition="v1",
            scope="source",
            python="3.12.14",
            path_inventory_sha256="A",
        )
        self.assertEqual(
            compare(a, dict(a, path_inventory_sha256="B")),
            {"status": "incomparable", "mismatched_fields": ["path_inventory_sha256"]},
        )

    def test_every_fingerprint_field_refuses_a_comparison(self):
        # The definition, the scope, the interpreter and the path inventory each
        # decide whether two reports describe the same measurement. Dropping one
        # from the fingerprint would silently subtract reports that are not
        # comparable, and nothing else in the suite would notice.
        base = dict(
            status="measured",
            definition="v1",
            scope="source",
            python="3.12.14",
            path_inventory_sha256="A",
            counts={"explicit_sites": 1},
        )
        for field, value in (
            ("definition", "v2"),
            ("scope", "installed"),
            ("python", "3.13.0"),
            ("path_inventory_sha256", "B"),
        ):
            with self.subTest(field=field):
                self.assertEqual(
                    compare(base, dict(base, **{field: value})),
                    {"status": "incomparable", "mismatched_fields": [field]},
                )

    def test_summary_names_the_mismatched_fingerprint_field(self):
        base = dict(
            status="measured",
            revision="a" * 40,
            definition=suppressions.DEFINITION,
            scope="source",
            python="3.12.14",
            files=1,
            path_inventory_sha256="A",
            tree_inventory_sha256="T",
            counts={"explicit_sites": 1},
            rule_site_counts={},
            file_counts=[],
        )
        detail = {
            "path": "src/code_forge/a.py",
            "confirmed_changes": {"added": [], "removed": [], "changed": []},
            "unmatched": [],
        }
        document = {
            "comparison": {
                "status": "incomparable",
                "mismatched_fields": ["python"],
                "paths_added": [],
                "paths_removed": [],
                "file_details": [detail],
            },
            "before": base,
            "after": dict(base, python="3.13.0"),
        }
        lines = suppressions.summary(document).splitlines()
        self.assertIn("Mismatched fields: python", lines)

    def test_summary_of_two_empty_scopes_is_not_a_zero(self):
        empty = dict(
            status="measured",
            revision="a" * 40,
            definition=suppressions.DEFINITION,
            scope="source",
            python="3.12.14",
            files=0,
            path_inventory_sha256="A",
            tree_inventory_sha256="T",
            counts=dict.fromkeys(suppressions.COUNT_KEYS, 0),
            rule_site_counts={},
            file_counts=[],
        )
        document = {
            "comparison": {"status": "unavailable", "reason": "empty_scope_on_both_sides"},
            "before": empty,
            "after": dict(empty, revision="b" * 40),
        }
        self.assertEqual(
            suppressions.summary(document).splitlines(),
            ["Status: unavailable", "Reason: empty_scope_on_both_sides"],
        )

    def test_definition_is_coupled_to_the_scan_algorithm(self):
        # A golden scan: if the algorithm changes what it counts, this fails and
        # forces a DEFINITION bump. Without that, two different definitions
        # compare as "comparable" and the fingerprint guarantees nothing.
        counts, rules = classify(
            b"x = 1  # noqa: BLE001, S110\n"
            b"# noqa\n"
            b"# ruff: noqa\n"
            b"# noqa: mystery word\n"
            b"y = 2  # noqa: F401 - justified\n"
        )
        self.assertEqual(suppressions.DEFINITION, "forge-noqa-comment-sites-v3")
        self.assertEqual(
            dict(counts),
            {
                "explicit_sites": 2,
                "blanket_sites": 1,
                "excluded_file_directives": 1,
                "unclassified_sites": 1,
            },
        )
        self.assertEqual(dict(rules), {"BLE001": 1, "S110": 1, "F401": 1})

    def test_compare_refuses_two_empty_scopes(self):
        empty = dict(
            status="measured",
            revision="a" * 40,
            definition=suppressions.DEFINITION,
            scope="source",
            python="3.12.14",
            files=0,
            path_inventory_sha256=sha256(b"").hexdigest(),
            tree_inventory_sha256=sha256(b"").hexdigest(),
            counts=dict.fromkeys(suppressions.COUNT_KEYS, 0),
            rule_site_counts={},
            file_counts=[],
        )
        self.assertEqual(
            compare(empty, dict(empty, revision="b" * 40)),
            {"status": "unavailable", "reason": "empty_scope_on_both_sides"},
        )

    def test_definition_covers_the_pairing_rule_too(self):
        # The definition is claimed to fingerprint the whole algorithm. Pin one
        # pairing decision as well as the scan counts: changing what counts as an
        # addition without bumping DEFINITION has to fail somewhere.
        old = suppressions.scan(b"a = 1  # noqa: F401\n")
        new = suppressions.scan(b"a = 2  # noqa: S110\n")
        result = suppressions.detail("src/code_forge/a.py", old, new)
        self.assertEqual(
            [(s["line"], s["rules"]) for s in result["confirmed_changes"]["removed"]],
            [(1, ["F401"])],
        )
        self.assertEqual(
            [(s["line"], s["rules"]) for s in result["confirmed_changes"]["added"]],
            [(1, ["S110"])],
        )
        self.assertEqual(result["confirmed_changes"]["changed"], [])
        self.assertEqual(result["unmatched"], [])

    def test_controlled_delta(self):
        a = dict(
            status="measured",
            definition="v1",
            scope="source",
            python="3.12.14",
            path_inventory_sha256="A",
            counts={"explicit_sites": 1},
        )
        self.assertEqual(
            compare(a, dict(a, counts={"explicit_sites": 2})),
            {"status": "comparable", "delta": {"explicit_sites": 1}, "rule_site_delta": {}},
        )


class GitControls(unittest.TestCase):
    def setUp(self):
        import pathlib
        import tempfile

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = pathlib.Path(temp.name)
        self.git("init", "-q", "--object-format=sha1")
        self.src = self.repo / "src/code_forge"
        self.src.mkdir(parents=True)
        self.base = self.commit(b"a = 1  # noqa: F401\n")

    def git(self, *args):
        import subprocess

        return (
            subprocess.check_output(
                [
                    "git",
                    "-C",
                    str(self.repo),
                    "-c",
                    "user.name=Fixture",
                    "-c",
                    "user.email=test@example.invalid",
                    "-c",
                    "commit.gpgsign=false",
                    *args,
                ],
                stderr=subprocess.PIPE,
            )
            .strip()
            .decode()
        )

    def commit(self, source):
        (self.src / "a.py").write_bytes(source)
        self.git("add", "src")
        self.git("commit", "-qm", "fixture")
        return self.git("rev-parse", "HEAD")

    def report(self, before, after):
        import contextlib
        import io
        import json

        main = suppressions.main
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = main(["--repo", str(self.repo), before, after])
        return code, json.loads(stream.getvalue())

    def test_lone_cr_site_reports_its_physical_line(self):
        # Both sides flattening the same way hides the defect; the row number in
        # the detail is what proves the CR was counted as a line break.
        (self.src / "a.py").write_bytes(b"x = 0\ry = 1  # noqa: F401\r")
        self.git("add", "src")
        self.git("commit", "-qm", "cr-one")
        before = self.git("rev-parse", "HEAD")
        (self.src / "a.py").write_bytes(b"x = 0\ry = 1  # noqa: F401\rw = 5  # noqa: S110\r")
        self.git("add", "src")
        self.git("commit", "-qm", "cr-two")
        after = self.git("rev-parse", "HEAD")
        code, doc = self.report(before, after)
        self.assertEqual(code, 0)
        detail = doc["comparison"]["file_details"][0]
        self.assertEqual(
            [(s["line"], s["rules"]) for s in detail["confirmed_changes"]["added"]],
            [(3, ["S110"])],
        )
        self.assertEqual(detail["confirmed_changes"]["removed"], [])

    def test_summary_reports_an_unavailable_side_with_its_paths(self):
        from unittest.mock import patch

        with patch.object(suppressions, "scan", side_effect=ValueError("nope")):
            code, doc = self.report(self.base, self.commit(b"a = 2  # noqa: F401\n"))
        self.assertEqual(code, 2)
        lines = suppressions.summary(doc).splitlines()
        self.assertEqual(lines[0], "Status: unavailable")
        self.assertIn("before: unavailable (source_not_parseable)", lines)
        self.assertIn("  'src/code_forge/a.py': source_not_parseable", lines)

    def test_summary_states_a_missing_delta_and_the_path_lists(self):
        (self.src / "b.py").write_bytes(b"z = 1  # noqa: F401\n")
        self.git("add", "src")
        self.git("commit", "-qm", "new-path")
        after = self.git("rev-parse", "HEAD")
        code, doc = self.report(self.base, after)
        self.assertEqual(code, 3)
        lines = suppressions.summary(doc).splitlines()
        self.assertIn("No whole-scope delta.", lines)
        self.assertIn("Path added: 'src/code_forge/b.py'", lines)

    def test_summary_renders_a_changed_pair(self):
        after = self.commit(b"a = 1  # noqa: F841\n")
        code, doc = self.report(self.base, after)
        self.assertEqual(code, 0)
        lines = suppressions.summary(doc).splitlines()
        self.assertIn(
            "Confirmed: 0 added, 0 removed, 1 changed. Unmatched: 0.",
            lines,
        )
        self.assertIn(
            "  Confirmed changed: 'src/code_forge/a.py':1: explicit_sites (F401) -> "
            "1: explicit_sites (F841)",
            lines,
        )

    def test_new_function_carries_its_suppression_as_added(self):
        after = self.commit(b"a = 1  # noqa: F401\n\n\ndef f():\n    return 2  # noqa: BLE001\n")
        code, doc = self.report(self.base, after)
        self.assertEqual(code, 0)
        detail = doc["comparison"]["file_details"][0]
        self.assertEqual([s["rules"] for s in detail["confirmed_changes"]["added"]], [["BLE001"]])
        self.assertEqual(detail["confirmed_changes"]["removed"], [])
        self.assertEqual(detail["unmatched"], [])

    def test_deleted_function_takes_its_suppression_with_it(self):
        before = self.commit(b"a = 1  # noqa: F401\n\n\ndef f():\n    return 2  # noqa: BLE001\n")
        after = self.commit(b"a = 1  # noqa: F401\n")
        code, doc = self.report(before, after)
        self.assertEqual(code, 0)
        detail = doc["comparison"]["file_details"][0]
        self.assertEqual([s["rules"] for s in detail["confirmed_changes"]["removed"]], [["BLE001"]])

    def test_lone_cr_source_keeps_rows_aligned(self):
        # A lone CR is a newline to the tokenizer. If the physical lines are
        # split only on LF, the suppression on row 2 has no context and the site
        # is reported as unmatched instead of paired across the inserted line.
        (self.src / "a.py").write_bytes(b"x = 0\ry = 1  # noqa: F401\r")
        self.git("add", "src")
        self.git("commit", "-qm", "cr-before")
        before = self.git("rev-parse", "HEAD")
        (self.src / "a.py").write_bytes(b"x = 0\rz = 9\ry = 1  # noqa: F401\r")
        self.git("add", "src")
        self.git("commit", "-qm", "cr-after")
        after = self.git("rev-parse", "HEAD")
        code, doc = self.report(before, after)
        self.assertEqual(code, 0)
        detail = doc["comparison"]["file_details"][0]
        self.assertEqual(detail["confirmed_changes"], {"added": [], "removed": [], "changed": []})
        self.assertEqual(detail["unmatched"], [])
        self.assertEqual(doc["comparison"]["delta"]["explicit_sites"], 0)

    def test_cli_rejects_head_and_short_revisions(self):
        for revision in ("HEAD", self.base[:7]):
            with self.subTest(revision=revision):
                code, doc = self.report(revision, self.base)
                self.assertEqual(code, 2)
                self.assertEqual(doc["before"]["reason"], "invalid_commit_id")

    def test_unknown_commit_is_unavailable_not_a_crash(self):
        code, doc = self.report(self.base, "0" * 40)
        self.assertEqual(code, 2)
        self.assertEqual(doc["after"]["status"], "unavailable")

    def test_memory_error_becomes_unavailable(self):
        from unittest.mock import patch

        with patch.object(suppressions, "scan", side_effect=MemoryError()):
            code, doc = self.report(self.base, self.commit(b"a = 2  # noqa: F401\n"))
        self.assertEqual(code, 2)
        self.assertEqual(doc["before"]["reason"], "blob_too_large")

    def test_memory_error_while_reading_the_blob_is_unavailable(self):
        from unittest.mock import patch

        real_git = suppressions.git

        def exploding(repo, *args):
            if args[:2] == ("cat-file", "blob"):
                raise MemoryError()
            return real_git(repo, *args)

        with patch.object(suppressions, "git", side_effect=exploding):
            code, doc = self.report(self.base, self.commit(b"a = 2  # noqa: F401\n"))
        self.assertEqual(code, 2)
        self.assertEqual(doc["before"]["reason"], "blob_too_large")

    def test_new_file_suppression_counts_as_added(self):
        (self.src / "b.py").write_bytes(b"z = 1  # noqa: BLE001\n")
        self.git("add", "src")
        self.git("commit", "-qm", "new-file")
        after = self.git("rev-parse", "HEAD")
        code, doc = self.report(self.base, after)
        # A new path changes the inventory, so the comparison is incomparable and
        # carries no net delta -- but the common-path detail is still reported.
        self.assertEqual(code, 3)
        self.assertEqual(doc["comparison"]["status"], "incomparable")
        detail = {d["path"]: d for d in doc["comparison"]["file_details"]}["src/code_forge/b.py"]
        self.assertEqual([s["rules"] for s in detail["confirmed_changes"]["added"]], [["BLE001"]])
        self.assertEqual(detail["confirmed_changes"]["removed"], [])
        self.assertEqual(doc["comparison"]["paths_added"], ["src/code_forge/b.py"])

    def test_summary_keeps_repeated_anchor_unmatched(self):
        # A repeated anchor cannot be paired, and the summary has to say so
        # instead of dropping the site from every bucket.
        before = self.commit(b"a = 1  # noqa: F401\na = 1  # noqa: F401\n")
        after = self.commit(b"a = 1  # noqa: F401\na = 1  # noqa: F401\nb = 3  # noqa: S110\n")
        code, doc = self.report(before, after)
        self.assertEqual(code, 0)
        text = suppressions.summary(doc).splitlines()
        self.assertTrue(any("Unmatched" in line for line in text), text)
        self.assertIn("Confirmed: 1 added, 0 removed, 0 changed. Unmatched: 2.", text)

    def test_summary_names_the_reason_when_detail_is_attached(self):
        after = self.commit(b"a = 1  # noqa: mystery word\n")
        code, doc = self.report(self.base, after)
        self.assertEqual(code, 3)
        lines = suppressions.summary(doc).splitlines()
        self.assertIn("Reason: unclassified_directives", lines)
        self.assertLess(
            lines.index("Status: incomparable"), lines.index("Reason: unclassified_directives")
        )

    def test_summary_lists_removed_paths(self):
        (self.src / "b.py").write_bytes(b"z = 1\n")
        self.git("add", "src")
        self.git("commit", "-qm", "add-b")
        middle = self.git("rev-parse", "HEAD")
        (self.src / "b.py").unlink()
        self.git("add", "src")
        self.git("commit", "-qm", "drop-b")
        after = self.git("rev-parse", "HEAD")
        code, doc = self.report(middle, after)
        self.assertEqual(code, 3)
        self.assertIn("Path removed: 'src/code_forge/b.py'", suppressions.summary(doc).splitlines())

    def test_summary_totals_sum_across_files(self):
        # Two files, two buckets: a per-file counter reset or "=" instead of "+="
        # would print a total that matches neither the detail nor the aggregate.
        (self.src / "b.py").write_bytes(b"z = 1  # noqa: F401\n")
        self.git("add", "src")
        self.git("commit", "-qm", "add-b")
        before = self.git("rev-parse", "HEAD")
        (self.src / "a.py").write_bytes(b"a = 1  # noqa: F841\n")
        (self.src / "b.py").write_bytes(b"z = 1  # noqa: F401\nw = 2  # noqa: BLE001\n")
        self.git("add", "src")
        self.git("commit", "-qm", "both-files")
        after = self.git("rev-parse", "HEAD")
        code, doc = self.report(before, after)
        self.assertEqual(code, 0)
        self.assertEqual(len(doc["comparison"]["file_details"]), 2)
        lines = suppressions.summary(doc).splitlines()
        self.assertIn("Confirmed: 1 added, 0 removed, 1 changed. Unmatched: 0.", lines)

    def test_summary_prints_each_unmatched_site(self):
        before = self.commit(b"a = 1  # noqa: F401\na = 1  # noqa: F401\n")
        after = self.commit(b"a = 1  # noqa: F401\na = 1  # noqa: F401\nb = 3  # noqa: S110\n")
        code, doc = self.report(before, after)
        self.assertEqual(code, 0)
        lines = suppressions.summary(doc).splitlines()
        self.assertIn(
            "  Unmatched before: 'src/code_forge/a.py':2: explicit_sites (F401) (repeated_source_anchor)",
            lines,
        )

    def test_summary_renders_a_site_without_rules(self):
        before = self.commit(b"a = 1\n# noqa\n")
        after = self.commit(b"a = 2\n# noqa\n")
        code, doc = self.report(before, after)
        self.assertEqual(code, 0)
        lines = suppressions.summary(doc).splitlines()
        self.assertIn(
            "  Unmatched before: 'src/code_forge/a.py':2: blanket_sites (-) (no_source_anchor)", lines
        )

    def test_summary_orders_the_blocks(self):
        after = self.commit(b"a = 1  # noqa: F841\n")
        code, doc = self.report(self.base, after)
        self.assertEqual(code, 0)
        lines = suppressions.summary(doc).splitlines()
        self.assertEqual(lines[0], "Status: comparable")
        self.assertEqual(lines[1], "before: 1 explicit sites across 1 paths")
        self.assertEqual(lines[2], "after: 1 explicit sites across 1 paths")
        self.assertTrue(lines[3].startswith("Confirmed: "))
        self.assertTrue(lines[4].startswith("  Confirmed changed: "))

    def test_a_timed_out_read_reaps_the_git_child(self):
        from unittest.mock import MagicMock, patch

        fake = MagicMock()
        fake.communicate.side_effect = __import__("subprocess").TimeoutExpired("git", 8)
        fake.pid = 4243
        fake.stdout = MagicMock()
        with (
            patch.object(suppressions.subprocess, "Popen", return_value=fake),
            patch.object(suppressions.os, "killpg") as killpg,
        ):
            with self.assertRaises(__import__("subprocess").TimeoutExpired):
                suppressions.git("/tmp", "cat-file", "blob", "abc")
        killpg.assert_called_once_with(4243, suppressions.signal.SIGKILL)
        fake.wait.assert_called_once()
        fake.stdout.close.assert_called_once()

    def test_revision_pinned_and_equal_total_rule_swap(self):
        import os
        from unittest.mock import patch

        after = self.commit(b"a = 1  # noqa: S110\n")
        (self.src / "a.py").write_text("broken !")
        (self.src / "untracked.py").write_text("# noqa")
        self.git("replace", self.base, after)
        with patch.dict(os.environ, {"GIT_DIR": "/does-not-exist"}):
            code, doc = self.report(self.base, after)
        self.assertEqual(code, 0)
        self.assertEqual(doc["before"]["files"], 1)
        self.assertEqual(doc["comparison"]["delta"]["explicit_sites"], 0)
        self.assertEqual(doc["comparison"]["rule_site_delta"], {"F401": -1, "S110": 1})

    def test_incomparable_path_added(self):
        (self.src / "extra.py").write_text("# noqa: S110\n")
        after = self.commit(b"a = 2\n")
        code, doc = self.report(self.base, after)
        self.assertEqual(code, 3)
        self.assertEqual(doc["comparison"]["mismatched_fields"], ["path_inventory_sha256"])
        self.assertNotIn("delta", doc["comparison"])

    def test_missing_object_is_unavailable(self):
        digest = self.git("rev-parse", f"{self.base}:src/code_forge/a.py")
        (self.repo / ".git/objects" / digest[:2] / digest[2:]).unlink()
        code, doc = self.report(self.base, self.base)
        self.assertEqual(code, 2)
        self.assertEqual(doc["comparison"], {"status": "unavailable"})
        self.assertNotIn("counts", doc["before"])
        self.assertEqual(
            doc["before"]["failures"], [{"path": "src/code_forge/a.py", "reason": "blob_unreadable"}]
        )

    def test_unparseable_is_unavailable(self):
        after = self.commit(b"invalid !\n")
        code, doc = self.report(self.base, after)
        self.assertEqual(code, 2)
        self.assertEqual(doc["after"]["reason"], "source_not_parseable")
        self.assertNotIn("counts", doc["after"])

    def test_symlink_is_unavailable(self):
        (self.src / "link.py").symlink_to("a.py")
        after = self.commit(b"a = 2\n")
        code, doc = self.report(self.base, after)
        self.assertEqual(code, 2)
        self.assertEqual(doc["after"]["reason"], "unsupported_source_mode")

    def test_ambiguous_directive_cannot_produce_delta(self):
        after = self.commit(b"a = 1 # noqa: F401  S110\n")
        code, doc = self.report(self.base, after)
        self.assertEqual(code, 3)
        self.assertEqual(doc["after"]["counts"]["unclassified_sites"], 1)
        self.assertFalse(doc["after"]["rule_site_counts"])
        self.assertNotIn("delta", doc["comparison"])

    def test_cross_file_move_cancels_aggregate(self):
        (self.src / "b.py").write_bytes(b"b = 2\n")
        before = self.commit(b"a = 1 # noqa: F401\n")
        (self.src / "b.py").write_bytes(b"b = 2 # noqa: F401\n")
        after = self.commit(b"a = 1\n")
        code, doc = self.report(before, after)
        diff = doc["comparison"]
        self.assertEqual(code, 0)
        self.assertEqual(diff["delta"]["explicit_sites"], 0)
        self.assertEqual(diff["rule_site_delta"], {})
        a, b = diff["file_details"]
        self.assertEqual([a["path"], b["path"]], ["src/code_forge/a.py", "src/code_forge/b.py"])
        self.assertEqual(
            a["confirmed_changes"]["removed"],
            [{"line": 1, "category": "explicit_sites", "rules": ["F401"]}],
        )
        self.assertEqual(b["confirmed_changes"]["added"], a["confirmed_changes"]["removed"])

    def test_line_insertion_and_normalized_rule_order(self):
        before = self.commit("é = 1 # noqa: S110, F401\n".encode())
        after = self.commit("# heading\né = 1 # noqa: F401, S110, S110\n".encode())
        _, doc = self.report(before, after)
        detail = doc["comparison"]["file_details"][0]
        self.assertFalse(any(detail["confirmed_changes"].values()) or detail["unmatched"])

    def test_unique_anchor_rule_and_category_changes(self):
        after = self.commit(b"# header\na = 1 # noqa\n")
        _, doc = self.report(self.base, after)
        a = doc["comparison"]["file_details"][0]
        self.assertEqual(
            a["confirmed_changes"]["changed"],
            [
                {
                    "before": {"line": 1, "category": "explicit_sites", "rules": ["F401"]},
                    "after": {"line": 2, "category": "blanket_sites", "rules": []},
                }
            ],
        )
        after = self.commit(b"a = 1 # noqa: F841\n")
        a = self.report(self.base, after)[1]["comparison"]["file_details"][0]
        self.assertEqual(
            a["confirmed_changes"]["changed"],
            [
                {
                    "before": {"line": 1, "category": "explicit_sites", "rules": ["F401"]},
                    "after": {"line": 1, "category": "explicit_sites", "rules": ["F841"]},
                }
            ],
        )
        self.assertFalse(a["confirmed_changes"]["added"] or a["confirmed_changes"]["removed"])

    def test_repeated_anchor_including_unannotated_copy(self):
        after = self.commit(b"a = 1 # noqa: S110\na = 1\n")
        a = self.report(self.base, after)[1]["comparison"]["file_details"][0]
        self.assertFalse(a["confirmed_changes"]["changed"])
        self.assertFalse(any(a["confirmed_changes"].values()))
        self.assertEqual(
            [(e["side"], e["line"], e["rules"]) for e in a["unmatched"]],
            [("before", 1, ["F401"]), ("after", 1, ["S110"])],
        )
        self.assertEqual([e["reason"] for e in a["unmatched"]], ["repeated_source_anchor"] * 2)

    def test_rewritten_and_standalone_anchors_are_not_paired(self):
        before = self.commit(b"# noqa\na = 1 # noqa: F401\n")
        after = self.commit(b"# noqa: S110\na = 2 # noqa: S110\n")
        a = self.report(before, after)[1]["comparison"]["file_details"][0]
        # A rewritten line is one removal plus one addition: there is no anchor
        # to pair them, and calling it "changed" would need one.
        self.assertFalse(a["confirmed_changes"]["changed"])
        self.assertEqual(len(a["confirmed_changes"]["added"]), 1)
        self.assertEqual(len(a["confirmed_changes"]["removed"]), 1)
        # The standalone directives still have no code anchor and stay unmatched.
        self.assertEqual(len(a["unmatched"]), 2)
        self.assertEqual({e["reason"] for e in a["unmatched"]}, {"no_source_anchor"})

    def test_path_drift_still_shows_common_detail(self):
        (self.src / "empty.py").write_bytes(b"x = 0\n")
        after = self.commit(b"a = 1 # noqa: S110\n")
        code, doc = self.report(self.base, after)
        diff = doc["comparison"]
        self.assertEqual(code, 3)
        self.assertNotIn("delta", diff)
        self.assertEqual(diff["paths_added"], ["src/code_forge/empty.py"])
        self.assertTrue(diff["file_details"][0]["confirmed_changes"]["changed"])

    def test_report_all_failed_paths_without_net_difference(self):
        (self.src / "new.py").write_bytes(b"bad !\n")
        after = self.commit(b"bad !\n")
        code, doc = self.report(self.base, after)
        self.assertEqual(code, 2)
        self.assertEqual(doc["comparison"], {"status": "unavailable"})
        self.assertEqual(
            [e["path"] for e in doc["after"]["failures"]],
            ["src/code_forge/a.py", "src/code_forge/new.py"],
        )
        self.assertNotIn("counts", doc["after"])

    def test_remove_last_source_paths(self):
        (self.src / "a.py").unlink()
        self.git("add", "-u")
        self.git("commit", "-qm", "remove")
        after = self.git("rev-parse", "HEAD")
        code, doc = self.report(self.base, after)
        self.assertEqual(code, 3)
        self.assertEqual(doc["comparison"]["paths_removed"], ["src/code_forge/a.py"])
        self.assertTrue(doc["comparison"]["file_details"][0]["confirmed_changes"]["removed"])

    def test_identical_blob_no_detail(self):
        code, doc = self.report(self.base, self.base)
        self.assertEqual(code, 0)
        self.assertEqual(doc["comparison"]["file_details"], [])

    def test_repeat_with_distinct_context_can_pair_after_line_shift(self):
        source = (
            b"# first\na = 1 # noqa: F401\n# end first\n# second\na = 1 # noqa: F401\n# end second\n"
        )
        before = self.commit(source)
        after = self.commit(b"# heading\n" + source.replace(b"F401", b"S110", 1))
        a = self.report(before, after)[1]["comparison"]["file_details"][0]
        self.assertEqual(len(a["confirmed_changes"]["changed"]), 1)
        self.assertEqual(
            (
                a["confirmed_changes"]["changed"][0]["before"]["line"],
                a["confirmed_changes"]["changed"][0]["after"]["line"],
            ),
            (2, 3),
        )
        self.assertFalse(
            a["confirmed_changes"]["added"] or a["confirmed_changes"]["removed"] or a["unmatched"]
        )

    def test_duplicate_context_still_refuses_pair(self):
        block = b"# same\n# same\na = 1 # noqa: F401\n# same\n# same\n"
        before = self.commit(block * 2)
        after = self.commit(block.replace(b"F401", b"S110") + block)
        a = self.report(before, after)[1]["comparison"]["file_details"][0]
        self.assertFalse(any(a["confirmed_changes"].values()))
        self.assertEqual(len(a["unmatched"]), 4)

    def test_multiline_token_closing_line_can_anchor(self):
        before = self.commit(b'x = """start\nend""" # noqa: F401\n')
        after = self.commit(b'x = """start\nend""" # noqa: S110\n')
        a = self.report(before, after)[1]["comparison"]["file_details"][0]
        self.assertEqual(len(a["confirmed_changes"]["changed"]), 1)
        self.assertFalse(a["unmatched"])

    def test_string_interior_is_not_a_counterpart_anchor(self):
        after = self.commit(b'x = """\na = 1\n"""\n')
        detail = self.report(self.base, after)[1]["comparison"]["file_details"][0]
        self.assertFalse(any(detail["confirmed_changes"].values()))
        self.assertEqual(
            detail["unmatched"],
            [
                {
                    "path": "src/code_forge/a.py",
                    "side": "before",
                    "line": 1,
                    "category": "explicit_sites",
                    "rules": ["F401"],
                    "reason": "no_code_anchor_on_other_side",
                }
            ],
        )

    def test_human_summary_reports_a_rewrite_as_add_and_remove(self):
        summary = suppressions.summary

        after = self.commit(b"a = 2 # noqa: F841\n")
        code, doc = self.report(self.base, after)
        self.assertEqual(code, 0)
        self.assertEqual(
            summary(doc).splitlines(),
            [
                "Status: comparable",
                "before: 1 explicit sites across 1 paths",
                "after: 1 explicit sites across 1 paths",
                "Confirmed: 1 added, 1 removed, 0 changed. Unmatched: 0.",
                "  Confirmed added: 'src/code_forge/a.py':1: explicit_sites (F841)",
                "  Confirmed removed: 'src/code_forge/a.py':1: explicit_sites (F401)",
            ],
        )
