"""Candidate-only host capture tests; no production provider or entry wiring."""

import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from execution_binding import CacheAdmission, ContextMismatch
from host_discovery_handoff_candidate import HostInvocation, capture_discovery


class HostCaptureTests(unittest.TestCase):
    def capture(self, env):
        return capture_discovery(
            environment=env,
            cwd=Path("/work"),
            resolve_locator=lambda values, cwd: cwd / (values.get("REG") or "registry"),
        )

    def test_new_execution_same_source_rejected(self):
        first, second = HostInvocation.issue(), HostInvocation.issue()
        self.assertNotEqual(first.execution_id, second.execution_id)
        admission = CacheAdmission()
        discovery = self.capture({}).binding
        admission.admit(first.for_source("source"), "diff", "/repo", discovery)
        with self.assertRaises(ContextMismatch):
            admission.admit(second.for_source("source"), "diff", "/repo", discovery)

    def test_one_invocation_multiple_source_bindings(self):
        invocation = HostInvocation.issue()
        a, b = invocation.for_source("a"), invocation.for_source("b")
        self.assertEqual(a.execution_id, b.execution_id)
        self.assertNotEqual(a.source_hash, b.source_hash)
        with self.assertRaises(ValueError):
            invocation.for_source("")

    def test_locator_consumes_copied_configuration(self):
        env = {"REG": "before"}

        def resolve(values, cwd):
            env["REG"] = "after"
            with self.assertRaises(TypeError):
                values["REG"] = "mutated"
            return cwd / values["REG"]

        captured = capture_discovery(environment=env, cwd="/work", resolve_locator=resolve)
        self.assertEqual(captured.locator, "/work/before")
        self.assertEqual(dict(captured.environment)["REG"], "before")

    def test_delivery_uses_same_capture(self):
        env = {"REG": "a"}
        captured = self.capture(env)
        env["REG"] = "b"

        def consume(values, cwd, locator):
            with self.assertRaises(TypeError):
                values["REG"] = "c"
            return values["REG"], cwd, locator

        self.assertEqual(captured.consume(consume), ("a", Path("/work"), Path("/work/a")))
        with self.assertRaises(FrozenInstanceError):
            captured.cwd = "/elsewhere"

    def test_changed_capture_rejected_before_reuse(self):
        context = HostInvocation.issue().for_source("source")
        admission = CacheAdmission()
        a, b = self.capture({"REG": "a"}), self.capture({"REG": "b"})
        admission.admit(context, "diff", "/repo", a.binding)
        with self.assertRaises(ContextMismatch):
            admission.admit(context, "diff", "/repo", b.binding)
        admission.admit(context, "diff", "/repo", a.binding)

    def test_invalid_configuration_fails_before_locator(self):
        calls = []
        for env in ({"REG": 3}, {3: "x"}):
            with self.assertRaises(TypeError):
                capture_discovery(
                    environment=env, cwd="/work", resolve_locator=lambda *a: calls.append(a)
                )
        self.assertEqual(calls, [])

    def test_relative_paths_rejected(self):
        with self.assertRaises(ValueError):
            capture_discovery(
                environment={}, cwd="relative", resolve_locator=lambda *a: Path("/ok")
            )
        with self.assertRaises(ValueError):
            capture_discovery(
                environment={}, cwd="/work", resolve_locator=lambda *a: Path("relative")
            )

    def test_absent_empty_and_literal_none_are_distinct(self):
        captures = [self.capture(env).binding for env in ({}, {"X": None}, {"X": ""}, {"X": "None"})]
        self.assertEqual(len(set(captures)), 4)

    def test_non_path_locator_raises_type_error(self):
        with self.assertRaises(TypeError):
            capture_discovery(environment={}, cwd="/work", resolve_locator=lambda *a: "/work/file")
