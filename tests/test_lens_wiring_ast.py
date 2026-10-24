# SPDX-License-Identifier: Apache-2.0
"""Phase 59.1 Task 4: the review_lenses switch reaches every outlet through _run.

T19 checks the wiring at value level on the AST. B1 and B2 run the code: B1
drives the real ``_run`` and reads what it hands to ``_dispatch_subagent``; B2
drives ``_dispatch_subagent`` and reads the prompt its spawn function builds.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from code_forge import cli, cross_repo
from code_forge.lenses import LENS_BLOCK, selection_line


def _tree() -> ast.Module:
    return ast.parse(inspect.getsource(cli))


def _fn(tree: ast.AST, name: str) -> ast.FunctionDef:
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


def _calls(fn: ast.AST, *callees: str) -> list[ast.Call]:
    return [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in callees
    ]


def _kw(call: ast.Call, name: str) -> ast.expr | None:
    return next((k.value for k in call.keywords if k.arg == name), None)


def _is_name(node: ast.expr | None, ident: str) -> bool:
    return isinstance(node, ast.Name) and node.id == ident


def test_dispatch_subagent_takes_review_lenses_and_forwards_the_name():
    disp = _fn(_tree(), "_dispatch_subagent")
    names = [a.arg for a in disp.args.args + disp.args.kwonlyargs]
    assert "review_lenses" in names
    spawns = _calls(disp, "_make_subagent_spawn")
    assert len(spawns) == 1
    assert _is_name(_kw(spawns[0], "review_lenses"), "review_lenses")


def test_run_assigns_review_lenses_only_from_the_trusted_loader():
    run = _fn(_tree(), "_run")
    assigns = [
        n
        for n in ast.walk(run)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "review_lenses" for t in n.targets)
    ]
    assert assigns
    for a in assigns:
        assert isinstance(a.value, ast.Call)
        assert isinstance(a.value.func, ast.Name)
        assert a.value.func.id == "_load_trusted_review_lenses"


def test_run_forwards_the_name_to_every_consumer():
    run = _fn(_tree(), "_run")
    disp = _calls(run, "_dispatch_subagent")
    assert len(disp) == 1
    assert _is_name(_kw(disp[0], "review_lenses"), "review_lenses")
    builders = _calls(run, "build_l1_provider", "build_grouped_l1_provider")
    assert len(builders) == 3
    for call in builders:
        assert _is_name(_kw(call, "review_lenses"), "review_lenses"), call.func.id
    est = _calls(run, "_estimate_l1_prompt_tokens")
    assert len(est) == 1
    assert _kw(est[0], "lens_text") is not None
    assert len(est[0].args) == 8  # lens_text is keyword-only, never positional


def test_cross_repo_never_passes_review_lenses():
    tree = ast.parse(inspect.getsource(cross_repo))
    calls = _calls(tree, "build_l1_provider")
    assert calls
    for call in calls:
        assert _kw(call, "review_lenses") is None


def _args() -> MagicMock:
    args = MagicMock()
    args.quiet = True
    args.backend = None
    args.contract = None
    args.registry = ".code-forge/tools.yaml"
    args.mode = "auto"
    args.outlet = "subagent"
    args.baseline = None
    args.head = None
    args.sandbox = False
    args.allow_main = True
    args.whole_file = None
    args.diff_tool = None
    args.status = False
    args.canary = False
    args.max_total_rounds = 3
    args.max_fix_attempts = 1
    args.falsification_engine = "stub"
    args.backend_url = None
    args.backend_format = None
    args.backend_key_env = None
    args.backend_model = None
    args.focus = None
    return args


def _drive_run(tmp_path: Path, trusted: bool) -> MagicMock:
    """Run the real _run up to the Outlet C dispatch; return the dispatch mock."""
    gate = tmp_path / ".code-forge" / "gate.yaml"
    gate.parent.mkdir(parents=True)
    gate.write_text(yaml.safe_dump({"review_lenses": True}), encoding="utf-8")

    fake_backend = MagicMock()
    fake_backend.name = "test"
    fake_backend.format = "openai"
    fake_backend.api_key_env = "FAKE_KEY_FOR_TEST"
    fake_backend.api_key_file = None
    fake_backend.credentials_path = None
    resolved = MagicMock()
    resolved.git_diff = "diff --git a/x b/x\n--- a/x\n+++ b/x\n"
    resolved.source_files = ["x"]
    resolved.mode_hint = "git"

    from code_forge.mode_resolver import Mode

    with (
        patch("code_forge.cli._load_gate_backends", return_value=({}, {})),
        patch("code_forge.cli.resolve_mode", return_value=Mode.LOCAL),
        patch("code_forge.outlet_resolver.resolve_outlet", return_value="subagent"),
        patch("code_forge.backend.resolve_backend", return_value=fake_backend),
        patch("code_forge.cli.load_registry", return_value={"ruff": {"type": "linter"}}),
        patch("code_forge.cli._build_baseline_specs", return_value=("b", "h")),
        patch("code_forge.cli.resolve_baseline", return_value=resolved),
        patch("code_forge.cli.serialize_baseline_spec", return_value="fake-baseline"),
        patch("code_forge.cli._assemble_post_image", return_value=("", "")),
        patch("code_forge.cli._dispatch_inline_canary", return_value=None),
        patch("code_forge.trust.is_trusted_lenses", return_value=trusted),
        patch("code_forge.cli._dispatch_subagent", return_value=None) as mock_disp,
    ):
        try:
            cli._run(_args(), {"FAKE_KEY_FOR_TEST": "sk-test"}, tmp_path)
        except Exception:  # noqa: BLE001
            pass  # downstream code needs real objects; the dispatch already happened
    return mock_disp


def test_b1_run_hands_the_trusted_switch_to_the_subagent_dispatch(tmp_path):
    mock_disp = _drive_run(tmp_path, trusted=True)
    assert mock_disp.called
    assert mock_disp.call_args.kwargs["review_lenses"] is True


def test_b1_run_hands_false_when_the_switch_is_untrusted(tmp_path):
    mock_disp = _drive_run(tmp_path, trusted=False)
    assert mock_disp.called
    assert mock_disp.call_args.kwargs["review_lenses"] is False


def _spawn_prompt(tmp_path: Path, review_lenses: bool) -> str:
    resolved = MagicMock()
    resolved.git_diff = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"
    resolved.source_files = []
    backend = MagicMock()
    backend.name = "test"
    with (
        patch("code_forge.cli._assemble_post_image", return_value=("", "")),
        patch("code_forge.outlet_c.run_outlet_c", return_value=MagicMock(verdict="PASS")) as run_c,
    ):
        cli._dispatch_subagent(
            "subagent",
            lambda m: None,
            "",
            backend,
            resolved,
            "hash",
            {},
            "stub",
            3,
            tmp_path,
            review_lenses=review_lenses,
        )
    spawn = run_c.call_args.kwargs["spawn_fn"]
    seen: list[str] = []

    def fake(prompt, **kw):
        seen.append(prompt)
        from code_forge.llm_invoke import LLMResult, Usage

        return LLMResult(content={"findings": []}, usage=Usage())

    with patch("code_forge.llm_invoke.llm_invoke", side_effect=fake):
        spawn("qodo", resolved.git_diff)
    assert len(seen) == 1
    return seen[0]


@pytest.mark.parametrize("flag", [True, False])
def test_b2_dispatch_builds_the_spawn_prompt_from_its_argument(tmp_path, flag):
    prompt = _spawn_prompt(tmp_path, flag)
    assert (LENS_BLOCK in prompt) is flag
    assert (selection_line("qodo") in prompt) is flag
