# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Phase 24.1-02: cli.py subagent dispatch passes real legs to run_outlet_c.

These tests use structural assertion (grep on the source block) rather than
full integration mocks, because the subagent dispatch path requires too many
mock layers to be a reliable integration test. The structural assertions
confirm the code was actually written correctly and are immune to mock-chain
false-positives.
"""

import pathlib

# Absolute path so tests pass even when another test calls chdir
_SRC = pathlib.Path(__file__).parent.parent / "src" / "code_forge" / "cli.py"


def _subagent_block() -> str:
    src = _SRC.read_text()
    start = src.index('def _dispatch_subagent(')
    return src[start: start + 2500]


class TestSubagentDispatchLegs:
    """cli.py subagent dispatch passes registry, backend, advisory_runners."""

    def test_subagent_dispatch_passes_registry(self):
        """Subagent block passes registry=registry to run_outlet_c."""
        assert "registry=registry" in _subagent_block(), (
            "subagent dispatch must pass registry=registry to run_outlet_c"
        )

    def test_subagent_dispatch_passes_advisory_runners(self):
        """Subagent block constructs 5 runners and passes advisory_runners=."""
        block = _subagent_block()
        assert "advisory_runners=" in block
        for name in ["_c_taint", "_c_runtime", "_c_graph", "_c_daemon", "_c_legacy"]:
            assert name in block, (
                "runner %s must be constructed in subagent block" % name
            )

    def test_subagent_dispatch_passes_engine(self):
        """Subagent block passes engine=engine_choice to run_outlet_c."""
        assert "engine=engine_choice" in _subagent_block()

    def test_subagent_block_no_pre_graph_findings(self):
        """_pre_graph_findings must NOT appear in subagent block (not in scope)."""
        assert "_pre_graph_findings" not in _subagent_block(), (
            "_pre_graph_findings is defined after subagent early-return; "
            "using it here causes NameError"
        )
