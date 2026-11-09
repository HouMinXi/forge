"""Helper/adapter boundary tests; delegate fixture is not the Forge runner."""
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from execution_binding import ExecutionContext, DiscoveryContext, HostBoundRunner, ContextMismatch

class Delegate:
    def __init__(self):
        self.calls = 0
        self.infra_errors = ["existing sibling unavailable"]
        self.findings = [{"description": "original"}]
    def run(self, diff, root):
        self.calls += 1
        return self.findings

class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ctx = ExecutionContext.new_host_execution("source")
        self.disc = DiscoveryContext((("CRG_HOME", None),))
        self.delegate = Delegate()
        self.bound = HostBoundRunner(self.delegate)
    def run_bound(self, diff="A", context=None, root=None, discovery=None):
        return self.bound.run(diff, self.root if root is None else root,
            context=self.ctx if context is None else context,
            discovery=self.disc if discovery is None else discovery)
    def test_same_context_calls_delegate(self):
        self.run_bound(); self.run_bound()
        self.assertEqual(self.delegate.calls, 2)
    def test_new_execution_rejected_before_delegate(self):
        self.run_bound()
        with self.assertRaises(ContextMismatch):
            self.run_bound(context=ExecutionContext.new_host_execution("source"))
        self.assertEqual(self.delegate.calls, 1)
    def test_diff_root_config_source_each_rejected(self):
        self.run_bound()
        variants = ({"diff": "A\n"}, {"root": self.root / "other"},
                    {"discovery": DiscoveryContext((("CRG_HOME", "other"),))},
                    {"context": replace(self.ctx, source_hash="other")})
        for variant in variants:
            with self.subTest(variant=variant), self.assertRaises(ContextMismatch):
                self.run_bound(**variant)
        self.assertEqual(self.delegate.calls, 1)
    def test_caller_nested_mutation_does_not_change_delegate(self):
        result = self.run_bound()
        result[0]["description"] = "changed"
        result.clear()
        self.assertEqual(self.run_bound(), [{"description": "original"}])
    def test_existing_errors_are_immutable_view(self):
        self.assertEqual(self.bound.infra_errors, ("existing sibling unavailable",))
        self.assertIsInstance(self.bound.infra_errors, tuple)
    def test_context_required_each_call(self):
        with self.assertRaises(TypeError):
            self.bound.run("A", self.root, discovery=self.disc)
        self.assertEqual(self.delegate.calls, 0)
    def test_invalid_types_refused(self):
        for values in ([ ("X", None) ], (("X", []),), (("X", None), ("X", "y"))):
            with self.subTest(values=values), self.assertRaises((TypeError, ValueError)):
                DiscoveryContext(values)
    def test_original_binding_survives_rejection(self):
        self.run_bound()
        with self.assertRaises(ContextMismatch):
            self.run_bound(diff="B")
        self.run_bound()
        self.assertEqual(self.delegate.calls, 2)

if __name__ == "__main__":
    unittest.main(verbosity=2)
