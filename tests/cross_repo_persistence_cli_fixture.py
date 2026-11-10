# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Owned subprocess setup for persistent diagnostic integration tests only.

The executable invokes the genuine CLI parser and entrypoint. The expensive
unrelated gate axes are controlled; native cross-repo discovery, publication,
strict consumption, worker ownership and subprocess transport are untouched.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
import types


def _write_completion(control, machine):
    """Retain lifecycle coordinates and the oracle, never error contents."""
    data = {
        "pid": os.getpid(),
        "cwd": str(machine.cwd),
        "source_root": str(machine._source_root()),
        "infra_errors_empty": machine._state.infra_errors == [],
    }
    (control / (machine.baseline_spec_repr + ".json")).write_text(json.dumps(data), encoding="utf-8")


def main():
    # Like the in-process fixture, provider-free CI substitutes only the pure
    # default-path helper. Runs with the optional provider use its real helper.
    try:
        import code_review_graph.registry  # noqa: F401
    except ImportError:
        package = types.ModuleType("code_review_graph")
        package.__path__ = []
        provider = types.ModuleType("code_review_graph.registry")
        provider.default_registry_path = lambda: Path(os.environ["CRG_HOME"]) / "registry.json"
        package.registry = provider
        sys.modules[package.__name__] = package
        sys.modules[provider.__name__] = provider

    from code_forge import cli
    from code_forge.machine import StateMachine
    from code_forge.state import Verdict

    for module, name in (
        ("taint", "TaintRunner"),
        ("runtime", "RuntimeRunner"),
        ("graph_triage", "GraphTriageRunner"),
        ("daemon_state", "DaemonStateRunner"),
        ("legacy", "LegacyRunner"),
        ("rulepack", "RulepackRunner"),
    ):
        imported = __import__("code_forge." + module, fromlist=[name])
        getattr(imported, name).run = lambda *args, **kwargs: []

    def gate(machine):
        verdict = Verdict[os.environ["FORGE_FIXTURE_VERDICT"]]
        machine._state.verdict = verdict
        return verdict

    StateMachine._run_ci = gate
    original_run = StateMachine.run
    control = Path(os.environ["FORGE_FIXTURE_CONTROL"])

    def observed_run(machine):
        verdict = original_run(machine)
        _write_completion(control, machine)
        if machine.baseline_spec_repr == "primary":
            deadline = time.monotonic() + 20
            while not (control / "release").exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError("owned test launcher was not released")
                time.sleep(0.01)
        return verdict

    StateMachine.run = observed_run
    return cli.main()
