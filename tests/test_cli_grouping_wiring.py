# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi1990@gmail.com>
"""Tests for the grouped-review wiring in the cli review path."""

from code_forge.cli import (
    _estimate_l1_prompt_tokens,
    _split_context_for_group,
)


class TestEstimateL1PromptTokens:
    def test_counts_every_block(self):
        base = _estimate_l1_prompt_tokens("x" * 400, "", "", "", "", "", "")
        with_post = _estimate_l1_prompt_tokens(
            "x" * 400, "y" * 400, "", "", "", "", "",
        )
        assert with_post - base == 100

    def test_empty_everything_is_contract_only(self):
        from code_forge.reviewer_json import REVIEW_JSON_CONTRACT
        assert _estimate_l1_prompt_tokens(
            "", "", "", "", "", "", "",
        ) == len(REVIEW_JSON_CONTRACT) // 4


class TestSplitContextForGroup:
    EDGES = [
        {"from": "src/cli.py", "from_group": "integration",
         "to": "src/rulepack.py", "to_group": "engine:rulepack.py",
         "symbols": ["RulepackRunner"]},
        {"from": "src/machine.py", "from_group": "covered:machine.py",
         "to": "src/state.py", "to_group": "integration",
         "symbols": ["State", "Verdict"]},
    ]

    def test_group_sees_only_its_own_edges(self):
        out = _split_context_for_group("integration", self.EDGES)
        assert "RulepackRunner" in out
        assert "State, Verdict" in out

    def test_unrelated_group_gets_empty(self):
        assert _split_context_for_group("engine:rulepack.py", []) == ""
        only_far = [dict(self.EDGES[1])]
        only_far[0]["from_group"] = "covered:machine.py"
        only_far[0]["to_group"] = "integration"
        assert _split_context_for_group("engine:rulepack.py", only_far) == ""

    def test_edge_visible_from_both_sides(self):
        a = _split_context_for_group("integration", self.EDGES)
        b = _split_context_for_group("covered:machine.py", self.EDGES)
        assert "State, Verdict" in a
        assert "State, Verdict" in b
