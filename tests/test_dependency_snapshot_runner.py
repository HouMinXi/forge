"""Offline dispatch contracts; never invoke the native probe or a manager."""

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / "scripts/dependency_snapshot_runner.py"
spec = importlib.util.spec_from_file_location("runner", PATH)
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


def environment():
    values = dict(
        REPOSITORY="HouMinXi/forge",
        EVENT="workflow_dispatch",
        JOB="ordinary-dependency",
        REF="refs/heads/" + p.BRANCH,
        RUN_ID="123",
        RUN_ATTEMPT="1",
        SOURCE_SHA="a" * 40,
        EXPECTED_SHA="a" * 40,
        WORKFLOW_SHA="a" * 40,
        WORKFLOW_REF="HouMinXi/forge/" + p.WORKFLOW + "@refs/heads/" + p.BRANCH,
        ORDINARY="true",
        FIXED="false",
    )
    return {"CONTEXT_" + key: value for key, value in values.items()}


class Contracts(unittest.TestCase):
    def test_probe_pin_matches_reviewed_source(self):
        source = PATH.parents[1] / p.PROBE
        self.assertEqual(p.hashlib.sha256(source.read_bytes()).hexdigest(), p.PROBE_SHA256)

    def test_context(self):
        with patch.dict(os.environ, environment(), clear=True):
            self.assertEqual(p.context()["SOURCE_SHA"], "a" * 40)

    def test_reject_context_changes(self):
        for key, value in dict(
            REPOSITORY="fork/forge",
            EVENT="pull_request",
            JOB="fixed-metadata",
            REF="refs/heads/main",
            RUN_ID="0",
            RUN_ATTEMPT="2",
            SOURCE_SHA="b" * 40,
            EXPECTED_SHA="",
            WORKFLOW_SHA="b" * 40,
            WORKFLOW_REF="elsewhere",
            ORDINARY="false",
            FIXED="true",
        ).items():
            with self.subTest(key=key), patch.dict(os.environ, environment(), clear=True):
                os.environ["CONTEXT_" + key] = value
                with self.assertRaises(p.Stop):
                    p.context()

    def test_source_read_bounds_and_symlink(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(p, "ROOT", Path(tmp)):
            path = Path(tmp) / "source"
            path.write_bytes(b"ok")
            self.assertEqual(p.read_source("source"), b"ok")
            (Path(tmp) / "link").symlink_to(path)
            with self.assertRaises(OSError):
                p.read_source("link")
            path.write_bytes(b"x" * 65537)
            with self.assertRaises(p.Stop):
                p.read_source("source")

    def test_source_binding_covers_four_files(self):
        calls = []

        def capture(argv, env, deadline, **kwargs):
            calls.append(argv)
            self.assertEqual(env["GIT_CONFIG_NOSYSTEM"], "1")
            self.assertEqual(env["GIT_CONFIG_GLOBAL"], "/dev/null")
            self.assertEqual(env["GIT_NO_REPLACE_OBJECTS"], "1")
            blob = p.hashlib.sha1(b"blob 6\0source", usedforsecurity=False).hexdigest()
            raw = b"".join(f"100644 blob {blob}\t{name}\0".encode() for name in p.FILES)
            return dict(
                stdout=b"a" * 40 + b"\n" if argv[-2:] == ["rev-parse", "HEAD"] else raw,
                reason=None,
                exit_code=0,
                stderr_bytes=0,
            )

        base = types.SimpleNamespace(capture=capture, ENV={})
        with patch.object(p, "read_source", return_value=b"source"):
            self.assertEqual(set(p.source_binding(base, "a" * 40)), set(p.FILES))
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(call[0] == "/usr/bin/git" for call in calls))
        with patch.object(p, "read_source", return_value=b"changed"), self.assertRaises(p.Stop):
            p.source_binding(base, "a" * 40)

    def test_source_binding_rejects_bad_head_and_tree(self):
        blob = p.hashlib.sha1(b"blob 6\0source", usedforsecurity=False).hexdigest()
        records = [f"100644 blob {blob}\t{name}\0".encode() for name in p.FILES]
        valid = b"".join(records)
        for head, tree in (
            (b"b" * 40 + b"\n", valid),
            (b"a" * 40 + b"\n", valid.replace(blob.encode(), b"0" * 40, 1)),
            (b"a" * 40 + b"\n", valid.replace(b"100644", b"120000", 1)),
            (b"a" * 40 + b"\n", b"".join(records[:-1])),
            (b"a" * 40 + b"\n", valid + records[0]),
            (b"a" * 40 + b"\n", valid[:-1]),
        ):

            def capture(argv, *args, head=head, tree=tree, **kwargs):
                return dict(
                    stdout=head if argv[-2:] == ["rev-parse", "HEAD"] else tree,
                    reason=None,
                    exit_code=0,
                    stderr_bytes=0,
                )

            base = types.SimpleNamespace(capture=capture, ENV={})
            with (
                self.subTest(head=head, tree=tree),
                patch.object(p, "read_source", return_value=b"source"),
                self.assertRaises(p.Stop),
            ):
                p.source_binding(base, "a" * 40)

    def test_identity_blocks_before_loading(self):
        with (
            patch.object(sys, "argv", ["runner"]),
            patch.object(os, "getuid", return_value=0),
            patch.object(p, "read_source") as read,
        ):
            value = p.observe()
        read.assert_not_called()
        self.assertEqual(
            (value["status"], value["native"], value["reason"]), ("STOP", "NOT_RUN", "identity")
        )

    def run_fake(self, result, code, source_ok=True, reason=None, stderr=0):
        output = dict(
            stdout=json.dumps(result).encode(), reason=reason, exit_code=code, stderr_bytes=stderr
        )
        base = types.SimpleNamespace(ENV={}, capture=lambda *a, **kw: output)
        source = types.SimpleNamespace(metadata=lambda: base, MAX_RECEIPT=262144)
        patches = [
            patch.object(sys, "argv", ["runner"]),
            patch.object(os, "getuid", return_value=1000),
            patch.object(os, "geteuid", return_value=1000),
            patch.object(os, "getgid", return_value=1000),
            patch.object(os, "getegid", return_value=1000),
            patch.dict(os.environ, environment(), clear=True),
            patch.object(p, "read_source", return_value=b""),
            patch.object(p, "PROBE_SHA256", p.hashlib.sha256(b"").hexdigest()),
            patch.object(p.types, "ModuleType", return_value=source),
            patch.object(
                p,
                "source_binding",
                side_effect=[{"hash": "ok"}, {"hash": "ok" if source_ok else "changed"}],
            ),
        ]
        from contextlib import ExitStack

        with ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            return p.observe()

    def test_observed_and_stop(self):
        for status, code in (("OBSERVED", 0), ("STOP", 2)):
            value = self.run_fake(
                dict(
                    schema="forge-dependency-snapshot-v1",
                    status=status,
                    complete=code == 0,
                    qualification="NOT_RUN",
                ),
                code,
            )
            self.assertEqual((value["status"], value["native"]), (status, "RETURNED"))
            self.assertEqual(value["qualification"], "NOT_RUN")

    def test_failed_output_never_retained(self):
        valid = dict(
            schema="forge-dependency-snapshot-v1",
            status="OBSERVED",
            complete=True,
            qualification="NOT_RUN",
        )
        for kwargs in (
            dict(code=2),
            dict(code=0, source_ok=False),
            dict(code=0, reason="timeout"),
            dict(code=0, stderr=1),
        ):
            value = self.run_fake(valid, **kwargs)
            self.assertEqual(value["status"], "STOP")
            self.assertIsNone(value["probe"])
        for value in ([], {**valid, "qualification": "PASSED"}, {**valid, "complete": 1}):
            self.assertEqual(self.run_fake(value, 0)["status"], "STOP")

    def test_workflow_guards(self):
        text = (PATH.parents[1] / p.WORKFLOW).read_text()
        self.assertIn(
            "&& (inputs.fixed_metadata_read || inputs.ordinary_dependency_snapshot || inputs.dependency_view_read)) }}",
            text,
        )
        self.assertIn(
            "&& inputs.fixed_metadata_read && !inputs.ordinary_dependency_snapshot && !inputs.dependency_view_read }}",
            text,
        )
        self.assertIn(
            "&& inputs.ordinary_dependency_snapshot && !inputs.fixed_metadata_read && !inputs.dependency_view_read }}",
            text,
        )
        ordinary = text.split("  ordinary-dependency:\n")[1].split("  dependency-view:\n")[0]
        self.assertNotIn("sudo", ordinary)
        self.assertNotIn("dependency_view_runner", ordinary)
        self.assertNotIn("pip install", ordinary)
        conflict = text.split("  conflicting-modes:\n")[1].split("  ordinary-dependency:\n")[0]
        self.assertNotIn("actions/checkout", conflict)
        self.assertIn("actions/upload-artifact", conflict)


if __name__ == "__main__":
    unittest.main()
