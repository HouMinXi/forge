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
            "x" * 400,
            "y" * 400,
            "",
            "",
            "",
            "",
            "",
        )
        assert with_post - base == 100

    def test_empty_everything_is_contract_only(self):
        from code_forge.reviewer_json import REVIEW_JSON_CONTRACT

        assert (
            _estimate_l1_prompt_tokens(
                "",
                "",
                "",
                "",
                "",
                "",
                "",
            )
            == len(REVIEW_JSON_CONTRACT) // 4
        )


class TestSplitContextForGroup:
    EDGES = [
        {
            "from": "src/cli.py",
            "from_group": "integration",
            "to": "src/rulepack.py",
            "to_group": "engine:rulepack.py",
            "symbols": ["RulepackRunner"],
        },
        {
            "from": "src/machine.py",
            "from_group": "covered:machine.py",
            "to": "src/state.py",
            "to_group": "integration",
            "symbols": ["State", "Verdict"],
        },
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


# These exercise CLI planning with local inputs only, not actual reviews.
def _coverage_patch(path):
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-old\n+new\n"


def test_grouped_cli_specs_keep_exact_slices_and_provenance(tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace

    import code_forge.cli as cli
    import code_forge.diff_grouping as grouping
    import code_forge.graph_triage as triage
    from code_forge.baseline import ResolvedReview

    diff = _coverage_patch("a.py") + _coverage_patch("config.yaml") + _coverage_patch("included.md")
    monkeypatch.setattr(triage, "_run_sem", lambda *a: SimpleNamespace(completed=True, entities=[{}]))
    groups = grouping.GroupingResult(
        groups=[
            grouping.Group("code", "code", ["a.py"], 3),
            grouping.Group("config", "config", ["config.yaml"], 0),
            grouping.Group("docs", "docs", ["included.md"], 0),
        ]
    )
    def group_diff(*args, changed_files):
        from code_forge.diff import get_changed_files

        assert changed_files == get_changed_files(diff)
        return groups

    monkeypatch.setattr(grouping, "group_diff", group_diff)
    monkeypatch.setattr(cli, "_assemble_post_image", lambda cwd, d: ("post:" + d, "conv"))
    resolved = ResolvedReview(
        baseline_content=None, mode_hint="git", source_files=[Path("a.py")], git_diff=diff
    )
    specs = cli._prepare_grouped_l1_specs(resolved, tmp_path, {}, lambda s: None)
    assert [s["provenance"] for s in specs] == ["semantic", "promoted", "promoted"]
    assert [s["resolved"].source_files for s in specs] == [
        [Path("a.py")],
        [Path("config.yaml")],
        [Path("included.md")],
    ]
    assert [s["resolved"].git_diff for s in specs] == [
        _coverage_patch(p) for p in ("a.py", "config.yaml", "included.md")
    ]
    assert resolved.git_diff == diff and groups.groups[1].passes == 0


def test_grouped_cli_sem_failure_and_empty_are_honest_fallbacks(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import code_forge.cli as cli
    import code_forge.graph_triage as triage
    from code_forge.baseline import ResolvedReview

    monkeypatch.setattr(cli, "_assemble_post_image", lambda *a: ("", ""))
    resolved = ResolvedReview(
        baseline_content=None, mode_hint="git", source_files=[], git_diff=_coverage_patch("a.md")
    )
    for outcome, expected in [
        (
            SimpleNamespace(completed=False, entities=[], status="timeout", diagnostic="deadline hit"),
            "timeout: deadline hit",
        ),
        (SimpleNamespace(completed=True, entities=[]), "sem returned no entities"),
    ]:
        monkeypatch.setattr(triage, "_run_sem", lambda *a, result=outcome: result)
        warnings = []
        specs = cli._prepare_grouped_l1_specs(resolved, tmp_path, {}, warnings.append)
        assert specs is None
        assert any("truncation risk stands" in warning for warning in warnings)
        assert any(expected in warning for warning in warnings)


def test_grouped_cli_invalid_last_group_is_atomic(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import pytest

    import code_forge.cli as cli
    import code_forge.diff_grouping as grouping
    import code_forge.factories as factories
    import code_forge.graph_triage as triage
    from code_forge.baseline import ResolvedReview

    calls = []
    monkeypatch.setattr(triage, "_run_sem", lambda *a: SimpleNamespace(completed=True, entities=[{}]))
    monkeypatch.setattr(
        grouping,
        "group_diff",
        lambda *a, **k: grouping.GroupingResult(
            groups=[
                grouping.Group("code", "code", ["a.py"], 3),
                grouping.Group("bad", "docs", ["unknown.md"], 0),
            ]
        ),
    )
    monkeypatch.setattr(cli, "_assemble_post_image", lambda *a: calls.append("post-image"))
    monkeypatch.setattr(factories, "build_grouped_l1_provider", lambda *a, **k: calls.append("provider"))
    monkeypatch.setattr(factories, "build_l1_provider", lambda *a, **k: calls.append("provider"))
    with pytest.raises(cli.CliError, match="unknown"):
        cli._prepare_grouped_l1_specs(
            ResolvedReview(
                baseline_content=None, mode_hint="git", source_files=[], git_diff=_coverage_patch("a.py")
            ),
            tmp_path,
            {},
            lambda s: None,
        )
    assert calls == []


def _actual_grouped_cli(tmp_path, monkeypatch, diff, groups):
    """Run _run through real branch selection; stop at the hold loop boundary."""
    from pathlib import Path
    from types import SimpleNamespace

    import code_forge.cli as cli
    import code_forge.context_sources as context
    import code_forge.diff_grouping as grouping
    import code_forge.graph_triage as triage
    from code_forge.backend import BackendConfig
    from code_forge.baseline import ResolvedReview
    from code_forge.state import Verdict

    registry = tmp_path / "tools.yaml"
    registry.write_text("tools: {}\n")
    args = cli._build_parser().parse_args(
        ["review", "--allow-main", "--backend", "test", "--registry", str(registry), "a.py"]
    )
    backend = BackendConfig(
        name="test", type="api", format="openai", base_url="https://example.invalid", model="test"
    )
    monkeypatch.setattr(cli, "is_git_repo", lambda *a: False)
    monkeypatch.setattr(
        cli, "resolve_baseline", lambda *a, **k: ResolvedReview([Path("a.py")], None, diff, "git")
    )
    monkeypatch.setattr("code_forge.outlet_resolver.resolve_outlet", lambda *a, **k: "subprocess")
    monkeypatch.setattr("code_forge.backend.resolve_backend", lambda *a, **k: backend)
    monkeypatch.setattr(cli, "_check_backend_credentials", lambda *a, **k: None)
    monkeypatch.setattr("code_forge.user_config.load_user_retry", dict)
    monkeypatch.setattr(cli, "_estimate_l1_prompt_tokens", lambda *a, **k: 100000)
    monkeypatch.setattr(context, "gather", lambda *a, **k: context.GatherResult())
    monkeypatch.setattr(triage, "_run_sem", lambda *a: SimpleNamespace(completed=True, entities=[{}]))
    def group_diff(*args, changed_files):
        from code_forge.diff import get_changed_files

        assert changed_files == get_changed_files(diff)
        return groups

    monkeypatch.setattr(grouping, "group_diff", group_diff)
    monkeypatch.setattr(cli, "_assemble_post_image", lambda *a: ("", ""))
    seen = []

    def factory(*a, **k):
        seen.append((a, k))
        return lambda: (_ for _ in ()).throw(AssertionError("no actual reviews"))

    monkeypatch.setattr(cli, "build_l1_provider", factory)
    monkeypatch.setattr("code_forge.factories.build_grouped_l1_provider", factory)
    monkeypatch.setattr(cli, "_run_hold_loop", lambda **k: Verdict.PENDING)
    monkeypatch.setattr(cli, "_run_test_assertion_review", lambda *a, **k: [])
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user"))
    return cli, args, seen


def test_actual_cli_all_zero_groups_are_promoted(tmp_path, monkeypatch):
    from code_forge.diff_grouping import Group, GroupingResult
    from code_forge.state import Verdict

    diff = _coverage_patch("config.yaml") + _coverage_patch("README.md")
    groups = GroupingResult(
        groups=[Group("config", "config", ["config.yaml"], 0), Group("docs", "docs", ["README.md"], 0)]
    )
    cli, args, seen = _actual_grouped_cli(tmp_path, monkeypatch, diff, groups)
    assert cli._run(args, {"FORGE_PROJECT_DIR": str(tmp_path)}, tmp_path) == Verdict.PENDING
    assert len(seen) == 1
    specs = seen[0][0][1]
    assert isinstance(specs, list) and len(specs) == 2
    assert all(spec["provenance"] == "promoted" for spec in specs)
    assert "".join(spec["resolved"].git_diff for spec in specs) == diff


def test_actual_cli_invalid_group_stops_before_any_review_factory(tmp_path, monkeypatch):
    import pytest

    from code_forge.diff_grouping import Group, GroupingResult

    groups = GroupingResult(
        groups=[Group("code", "code", ["a.py"], 3), Group("bad", "docs", ["missing.md"], 0)]
    )
    cli, args, seen = _actual_grouped_cli(tmp_path, monkeypatch, _coverage_patch("a.py"), groups)
    for name in ("build_falsifier", "build_autofixer", "build_revert_fn"):
        monkeypatch.setattr(cli, name, lambda *a, **k: seen.append("other-provider"))
    with pytest.raises(cli.CliError, match="unknown"):
        cli._run(args, {"FORGE_PROJECT_DIR": str(tmp_path)}, tmp_path)
    assert seen == []


def test_actual_cli_preserves_earlier_ancillary_contract_call(tmp_path, monkeypatch):
    """Planning gates review dispatch, not pre-existing contract summarization."""
    import pytest

    from code_forge.diff_grouping import Group, GroupingResult
    from code_forge.llm_invoke import LLMResult, Usage

    groups = GroupingResult(groups=[Group("bad", "docs", ["missing.md"], 0)])
    cli, args, seen = _actual_grouped_cli(tmp_path, monkeypatch, _coverage_patch("a.py"), groups)
    contract = tmp_path / "contract.txt"
    contract.write_text("Preserve behavior.\n" * 400)
    args.contract = str(contract)
    ancillary = []

    def local_contract(prompt, **kwargs):
        ancillary.append(prompt)
        return LLMResult("Preserve behavior.", Usage(), 0.0)

    monkeypatch.setattr("code_forge.llm_invoke.llm_invoke", local_contract)
    with pytest.raises(cli.CliError, match="unknown"):
        cli._run(args, {"FORGE_PROJECT_DIR": str(tmp_path)}, tmp_path)
    assert len(ancillary) == 1
    assert seen == []  # No review provider constructed or invoked.
