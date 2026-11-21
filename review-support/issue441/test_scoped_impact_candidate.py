"""Candidate scope tests with caching fixture; NOT Forge/SQLite E2E."""

import inspect
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from execution_binding import ExecutionContext, DiscoveryContext, ContextMismatch
from scoped_impact_candidate import HostInputs, make_scoped_impact_type


class CachingFixture:
    def __init__(self):
        self.calls = 0
        self.queries = 0
        self.infra_errors = ["old sibling error"]
        self._cached = None

    def run(self, diff_text, repo_root):
        self.calls += 1
        if self._cached is None:
            self.queries += 1
            self._cached = [{"description": "original"}]
        return self._cached


class ScopedTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.host = HostInputs(
            ExecutionContext.new_host_execution("source"), DiscoveryContext((("CRG_HOME", None),))
        )
        self.reads = 0
        self.scope_type = make_scoped_impact_type(CachingFixture)
        self.runner = self.scope_type(current_inputs=self.current)

    def current(self):
        self.reads += 1
        return self.host

    def test_narrow_signature_and_routing_identity(self):
        self.assertEqual(list(inspect.signature(self.runner.run).parameters), ["diff_text", "repo_root"])
        self.assertIsInstance(self.runner, CachingFixture)

    def test_same_input_preserves_fixture_cache_and_reads_current_each_time(self):
        self.runner.run("A", self.root)
        self.runner.run("A", self.root)
        self.assertEqual((self.reads, self.runner.calls, self.runner.queries), (2, 2, 1))

    def test_new_execution_rejected_before_fixture_cache(self):
        self.runner.run("A", self.root)
        self.host = HostInputs(ExecutionContext.new_host_execution("source"), self.host.discovery)
        with self.assertRaises(ContextMismatch):
            self.runner.run("A", self.root)
        self.assertEqual((self.runner.calls, self.runner.queries), (1, 1))

    def test_diff_root_config_changes_rejected(self):
        self.runner.run("A", self.root)
        for diff, root in (("A\n", self.root), ("A", self.root / "other")):
            with self.subTest(diff=diff, root=root), self.assertRaises(ContextMismatch):
                self.runner.run(diff, root)
        old = self.host
        self.host = HostInputs(old.context, DiscoveryContext((("CRG_HOME", "changed"),)))
        with self.assertRaises(ContextMismatch):
            self.runner.run("A", self.root)
        self.host = old
        self.runner.run("A", self.root)
        self.assertEqual(self.runner.queries, 1)

    def test_return_nested_mutation_does_not_change_cache(self):
        self.runner.run("A", self.root)[0]["description"] = "changed"
        self.assertEqual(self.runner.run("A", self.root), [{"description": "original"}])
        self.assertEqual(self.runner.infra_errors, ["old sibling error"])

    def test_wrong_host_snapshot_fails_before_base(self):
        runner = self.scope_type(current_inputs=lambda: None)
        with self.assertRaises(TypeError):
            runner.run("A", self.root)
        self.assertEqual(runner.calls, 0)

    def test_two_scopes_do_not_share_binding(self):
        self.runner.run("A", self.root)
        other = self.scope_type(current_inputs=self.current)
        other.run("B", self.root)
        self.assertEqual(self.runner.run("A", self.root), [{"description": "original"}])


if __name__ == "__main__":
    unittest.main(verbosity=2)
