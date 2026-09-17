"""Run the real command path up to the external review-loop boundary."""
from unittest.mock import Mock

import pytest
import yaml

from code_forge import cli, factories, trust
from code_forge.backend import BackendConfig
from code_forge.baseline import ResolvedReview
from code_forge.context_sources import FactRow, GraphTriageSource, RemovedSymbolReaders
from code_forge.kernel_context import KernelContextSource, validate_kernel_context
from code_forge.state import Verdict
from tests.test_kernel_context_source import diff


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user-config"))
    monkeypatch.setenv("FORGE_PROJECT_DIR", str(tmp_path))
    gate = tmp_path / ".code-forge" / "gate.yaml"
    gate.parent.mkdir()
    config = {"kernel_context": {"enabled": True, "defconfig": "defconfig"}, "siblings": []}
    gate.write_text(yaml.safe_dump(config))
    trust.record_trust(gate, config)
    trust.record_kernel_context_trust(gate, tmp_path, validate_kernel_context(config["kernel_context"]))
    (tmp_path / "defconfig").write_text("CONFIG_X=m\n")
    (tmp_path / "driver.c").write_text("#ifdef CONFIG_X\n")
    patch_text = diff(["#ifdef CONFIG_X"])
    args = cli._build_parser().parse_args(["review", "--allow-main", "--backend", "test"])
    backend = BackendConfig(name="test", type="api", format="openai", base_url="https://example.invalid",
                            model="test", max_tokens=4096)
    monkeypatch.setattr("code_forge.outlet_resolver.resolve_outlet", lambda *a, **kw: "subprocess")
    monkeypatch.setattr("code_forge.backend.resolve_backend", lambda *a, **kw: backend)
    monkeypatch.setattr(cli, "_merge_user_into", lambda configs, data: configs)
    monkeypatch.setattr(cli, "_check_backend_credentials", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "load_registry", lambda *a: {"ruff": {"type": "linter"}})
    monkeypatch.setattr(cli, "resolve_baseline", lambda *a: ResolvedReview(
        source_files=[tmp_path / "driver.c"], baseline_content=None, git_diff=patch_text, mode_hint="git"))
    monkeypatch.setattr(GraphTriageSource, "facts", lambda *a: [])
    monkeypatch.setattr(GraphTriageSource, "snapshot_sha", lambda *a: None)
    monkeypatch.setattr(RemovedSymbolReaders, "facts", lambda *a: [])
    monkeypatch.setattr("code_forge.user_config.load_user_retry", dict)
    captured = []

    def provider(*a, **kw):
        captured.append(kw)
        return Mock()

    monkeypatch.setattr(cli, "build_l1_provider", provider)
    monkeypatch.setattr(factories, "build_grouped_l1_provider", provider)
    monkeypatch.setattr(cli, "build_falsifier", Mock())
    monkeypatch.setattr(cli, "_run_hold_loop", lambda *a, **kw: Verdict.PASS)
    return tmp_path, gate, config, args, captured


def test_enabled_source_reaches_real_single_provider(pipeline, monkeypatch):
    root, _, _, args, captured = pipeline
    cross = Mock(side_effect=AssertionError("must not reload siblings"))
    monkeypatch.setattr(cli, "_dispatch_cross_repo", cross)
    estimate = Mock(wraps=cli._estimate_l1_prompt_tokens)
    monkeypatch.setattr(cli, "_estimate_l1_prompt_tokens", estimate)
    cli._run(args, {"FORGE_PROJECT_DIR": str(root)}, root)
    assert estimate.call_args.args[-1] == captured[0]["context_sources_text"]
    assert len(captured) == 1
    assert "config:CONFIG_X" in captured[0]["context_sources_text"]
    assert "declared=m" in captured[0]["context_sources_text"]
    assert not any(r.source == "kernel" for r in cli.build_falsifier.call_args.kwargs["context_rows"])
    cross.assert_not_called()


@pytest.mark.parametrize("failure", ["source", "outer", "renderer"])
def test_failure_notice_reaches_real_provider_without_exception_payload(pipeline, monkeypatch, capsys, failure):
    root, _, _, args, captured = pipeline
    def fail(*a, **kw):
        raise RuntimeError("DO_NOT_LOG_SECRET")
    if failure == "source":
        monkeypatch.setattr(KernelContextSource, "facts", fail)
    elif failure == "outer":
        from code_forge import diff as diff_module
        original = diff_module.get_changed_files
        calls = 0
        def fail_collection(*a, **kw):
            nonlocal calls
            calls += 1
            if calls == 3:
                fail()
            return original(*a, **kw)
        monkeypatch.setattr(diff_module, "get_changed_files", fail_collection)
    else:
        original = KernelContextSource._render
        monkeypatch.setattr(KernelContextSource, "_render", fail)
        assert original
    cli._run(args, {"FORGE_PROJECT_DIR": str(root)}, root)
    assert "kernel-context: unavailable; see warnings" in captured[0]["context_sources_text"]
    assert "DO_NOT_LOG_SECRET" not in capsys.readouterr().err


def test_early_snapshot_survives_real_gate_rewrite(pipeline, monkeypatch, capsys):
    root, gate, data, args, captured = pipeline
    assemble = cli._assemble_post_image
    def rewrite(*a, **kw):
        data["siblings"] = [{"name": "later", "path": "/another"}]
        gate.write_text(yaml.safe_dump(data))
        return assemble(*a, **kw)
    monkeypatch.setattr(cli, "_assemble_post_image", rewrite)
    monkeypatch.setattr(cli, "_dispatch_cross_repo", Mock(side_effect=AssertionError("late reload")))
    cli._run(args, {"FORGE_PROJECT_DIR": str(root)}, root)
    assert "declared=m" in captured[0]["context_sources_text"]
    assert capsys.readouterr().err.count("empty siblings: []") == 1


@pytest.mark.parametrize("section", [{"enabled": False}, None])
def test_disabled_keeps_cross_repo_and_empty_extra_estimate(pipeline, monkeypatch, section):
    root, gate, data, args, captured = pipeline
    if section is None:
        data.pop("kernel_context")
    else:
        data["kernel_context"] = section
    data["siblings"] = [{"name": "peer", "path": "../peer"}]
    gate.write_text(yaml.safe_dump(data))
    cross = Mock(return_value=Verdict.PASS)
    monkeypatch.setattr(cli, "_dispatch_cross_repo", cross)
    estimate = Mock(wraps=cli._estimate_l1_prompt_tokens)
    monkeypatch.setattr(cli, "_estimate_l1_prompt_tokens", estimate)
    monkeypatch.setattr(KernelContextSource, "facts", Mock(side_effect=AssertionError("disabled read")))
    cli._run(args, {"FORGE_PROJECT_DIR": str(root)}, root)
    cross.assert_called_once()
    assert estimate.call_args.args[-1] == ""
    assert captured[0]["context_sources_text"] == ""


@pytest.mark.parametrize("grouped", [False, True])
def test_grouped_and_fallback_reuse_one_snapshot(pipeline, monkeypatch, grouped):
    from types import SimpleNamespace
    root, _, _, args, captured = pipeline
    monkeypatch.setattr("code_forge.diff_grouping.max_prompt_tokens_from_gate_config", lambda data: 1)
    monkeypatch.setattr("code_forge.graph_triage._run_sem", lambda *a: [])
    groups = [SimpleNamespace(name="one", passes=3, members=["driver.c"]),
              SimpleNamespace(name="two", passes=3, members=["driver.c"])] if grouped else []
    monkeypatch.setattr("code_forge.diff_grouping.group_diff", lambda *a: SimpleNamespace(
        groups=groups, cross_group_edges=[]))
    original = KernelContextSource._read_config
    reads = []
    def observe(src):
        reads.append(src)
        return original(src)
    monkeypatch.setattr(KernelContextSource, "_read_config", observe)
    assemble = cli._assemble_post_image
    calls = []
    def rewrite(*a, **kw):
        calls.append(1)
        if len(calls) > 1:
            (root / "defconfig").write_text("CONFIG_X=n\n")
        return assemble(*a, **kw)
    monkeypatch.setattr(cli, "_assemble_post_image", rewrite)
    cli._run(args, {"FORGE_PROJECT_DIR": str(root)}, root)
    assert len(reads) == 1
    assert "declared=m" in captured[0]["context_sources_text"]
    assert "declared=n" not in captured[0]["context_sources_text"]
    assert len(calls) == (3 if grouped else 1)


@pytest.mark.parametrize("enabled", [False, True])
def test_context_cost_flips_grouping_only_when_enabled(pipeline, monkeypatch, enabled):
    from types import SimpleNamespace
    root, gate, data, args, captured = pipeline
    data["kernel_context"]["enabled"] = enabled
    gate.write_text(yaml.safe_dump(data))
    if enabled:
        trust.record_kernel_context_trust(gate, root, validate_kernel_context(data["kernel_context"]))
    original = cli._estimate_l1_prompt_tokens
    threshold = []
    def estimate(*values):
        threshold.append(original(*values[:-1]) + 1)
        return original(*values)
    monkeypatch.setattr(cli, "_estimate_l1_prompt_tokens", estimate)
    class Boundary:
        def __ge__(self, value):
            return value <= threshold[0]

        def __int__(self):
            return threshold[0]
    monkeypatch.setattr("code_forge.diff_grouping.max_prompt_tokens_from_gate_config", lambda data: Boundary())
    grouping = Mock(return_value=SimpleNamespace(groups=[], cross_group_edges=[]))
    monkeypatch.setattr("code_forge.diff_grouping.group_diff", grouping)
    monkeypatch.setattr("code_forge.graph_triage._run_sem", lambda *a: [])
    monkeypatch.setattr(GraphTriageSource, "facts", lambda *a: [FactRow("other", "x", "", "kept", "other")])
    cli._run(args, {"FORGE_PROJECT_DIR": str(root)}, root)
    assert grouping.call_count == int(enabled)
    assert "kept" in captured[0]["context_sources_text"]


@pytest.mark.parametrize("kernel,other", [(False, False), (False, True), (True, False), (True, True)])
def test_exact_context_concatenation(pipeline, monkeypatch, kernel, other):
    from code_forge.context_sources import GatherResult, render_context_sources
    root, gate, data, args, captured = pipeline
    data["kernel_context"]["enabled"] = kernel
    gate.write_text(yaml.safe_dump(data))
    other_rows = [FactRow("other", "x", "", "kept", "other")] if other else []
    monkeypatch.setattr(GraphTriageSource, "facts", lambda *a: other_rows)
    instances = []
    real = KernelContextSource.facts
    def observe(src, *a):
        instances.append(src)
        return real(src, *a)
    monkeypatch.setattr(KernelContextSource, "facts", observe)
    cli._run(args, {"FORGE_PROJECT_DIR": str(root)}, root)
    a = render_context_sources(GatherResult(rows=other_rows))
    b = instances[0].rendered_text if kernel else ""
    expected = a + "\n\n" + b if kernel and other else b if kernel else a
    assert captured[0]["context_sources_text"] == expected
    if kernel:
        assert captured[0]["context_sources_text"].count("config:CONFIG_X") == 1


def test_next_request_rechecks_changed_siblings(pipeline, monkeypatch):
    root, gate, data, args, captured = pipeline
    cli._run(args, {"FORGE_PROJECT_DIR": str(root)}, root)
    data["siblings"] = ["peer"]
    gate.write_text(yaml.safe_dump(data))
    with pytest.raises(cli.CliError, match="siblings are not supported"):
        cli._run(args, {"FORGE_PROJECT_DIR": str(root)}, root)
    assert len(captured) == 1


def test_non_kernel_rows_survive_kernel_source_error(pipeline, monkeypatch):
    root, _, _, args, captured = pipeline
    monkeypatch.setattr(GraphTriageSource, "facts", lambda *a: [FactRow("other", "x", "", "kept", "other")])
    monkeypatch.setattr(KernelContextSource, "facts", Mock(side_effect=RuntimeError("bad")))
    cli._run(args, {"FORGE_PROJECT_DIR": str(root)}, root)
    assert "kept" in captured[0]["context_sources_text"]
    assert "unavailable" in captured[0]["context_sources_text"]
