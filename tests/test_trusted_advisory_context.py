"""Advisory and context gate.yaml keys come from the trusted gate (#150).

graph_triage.*, knowledge.enabled, the advisory rule-pack list and the
contents of repo-local packs are read from the review baseline (or an outside
config root), never from the reviewed tree. An existing trusted gate that
cannot be read is passed on as the error, not as {} (defaults could re-enable
an axis the operator disabled). CLI wiring: ``StateMachine(cwd=repo)``.
"""

import dataclasses
import sys

import pytest
import yaml

from code_forge import gate_check
from code_forge.context_sources import GraphTriageSource, knowledge_enabled
from code_forge.graph_triage import GraphTriageRunner
from code_forge.rulepack import RulepackRunner
from code_forge.state import Mode
from tests.test_sem_acquisition_contract import cli_pipeline, sem_controls  # noqa: F401 - fixtures
from tests.test_trusted_blocking_packs import (
    BASE_RULES,
    PACK,
    PR_RULES,
    _git,
    _machine,
    _repo,
    _write_pack,
    semgrep,  # noqa: F401 - fixture
)

TEST = {"command": [sys.executable, "-c", "pass"]}
DISABLED = {"test": TEST, "graph_triage": {"enabled": False}}
ENABLED_BY_PR = {"test": TEST, "graph_triage": {"enabled": True}, "knowledge": {"enabled": True}}
DIFF = "--- a/src/mod.py\n+++ b/src/mod.py\n@@ -1 +1 @@\n-x = 1\n+eval('x')\n"


def _gate_repo(tmp_path, base_gate):
    repo, base = _repo(tmp_path, pack_at_base=False, gate=base_gate)
    # The change under review edits its own gate.yaml.
    (repo / ".code-forge" / "gate.yaml").write_text(yaml.safe_dump(ENABLED_BY_PR), encoding="utf-8")
    return repo, base


def _trusted(repo, base, mode=Mode.LOCAL):
    return gate_check.trusted_gate_data_or_error(
        config_root=repo,
        source_root=repo,
        base_sha=base,
        allow_untracked=mode is not Mode.CI,
    )


# --- the shared loader -------------------------------------------------------


def test_trusted_data_is_the_baseline_copy_not_the_pr_edit(tmp_path):
    repo, base = _gate_repo(tmp_path, DISABLED)
    data = _trusted(repo, base)
    assert data["graph_triage"] == {"enabled": False}
    assert "knowledge" not in data


def test_no_trusted_gate_is_defaults(tmp_path):
    repo, base = _repo(tmp_path, pack_at_base=False, gate=DISABLED)
    _git(repo, "rm", "-q", "--cached", ".code-forge/gate.yaml")
    _git(repo, "commit", "-qm", "drop gate")
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "add", ".code-forge/gate.yaml")  # PR re-adds it (staged): not trusted
    assert _trusted(repo, base) == {}


def test_unreadable_trusted_gate_is_an_error_not_defaults(tmp_path):
    repo, base = _repo(tmp_path, pack_at_base=False, gate=DISABLED)
    (repo / ".code-forge" / "gate.yaml").write_text("graph_triage: [\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "broken gate")
    base = _git(repo, "rev-parse", "HEAD")
    assert isinstance(_trusted(repo, base), ValueError)


# --- knowledge.enabled -------------------------------------------------------


def test_pr_cannot_enable_knowledge_for_its_own_review(tmp_path):
    repo, base = _gate_repo(tmp_path, DISABLED)
    assert knowledge_enabled(repo) is True  # the legacy tree read would have
    assert knowledge_enabled(repo, gate_data=_trusted(repo, base)) is False


def test_knowledge_follows_the_trusted_gate(tmp_path):
    assert knowledge_enabled(tmp_path, gate_data={"knowledge": {"enabled": True}}) is True
    assert knowledge_enabled(tmp_path, gate_data={}) is False
    assert knowledge_enabled(tmp_path, gate_data=ValueError("unreadable")) is False


# --- graph_triage context source and advisory axis --------------------------


def test_context_source_uses_the_trusted_graph_policy(tmp_path, monkeypatch):
    repo, base = _gate_repo(tmp_path, DISABLED)
    monkeypatch.setattr("code_forge.graph_triage.shutil.which", lambda _: "/fixture/sem")
    source = GraphTriageSource(repo, gate_config=_trusted(repo, base))
    assert source.snapshot_sha() is None
    assert source._backend_selection[0] is None  # disabled: no backend selected
    source.facts(["src/mod.py"], DIFF)
    assert source.acquisition_outcome.status == "disabled"


def test_context_source_refuses_on_an_unreadable_trusted_gate(tmp_path):
    source = GraphTriageSource(tmp_path, gate_config=ValueError("trusted gate unreadable"))
    with pytest.raises(ValueError, match="trusted gate unreadable"):
        source.snapshot_sha()


def test_machine_graph_axis_uses_the_trusted_policy(tmp_path, monkeypatch):
    repo, base = _gate_repo(tmp_path, DISABLED)
    monkeypatch.setattr("code_forge.graph_triage.shutil.which", lambda _: "/fixture/sem")
    runner = GraphTriageRunner()
    m = _machine(repo, base, mode=Mode.LOCAL)
    m.resolved_review = dataclasses.replace(m.resolved_review, git_diff=DIFF)
    m.advisory_runners = [runner]
    m._run_advisory_axes()
    assert runner.acquisition_outcome.status == "disabled"


def test_machine_graph_axis_reports_an_invalid_trusted_policy(tmp_path):
    gate = {"test": TEST, "graph_triage": {"enabled": "yes"}}
    repo, base = _repo(tmp_path, pack_at_base=False, gate=gate)
    runner = GraphTriageRunner()
    m = _machine(repo, base, mode=Mode.LOCAL)
    m.resolved_review = dataclasses.replace(m.resolved_review, git_diff=DIFF)
    m.advisory_runners = [runner]
    m._run_advisory_axes()
    assert runner.acquisition_outcome.status == "configuration_error"


# --- advisory rule packs -----------------------------------------------------


@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
def test_advisory_rulepack_uses_the_trusted_pack_contents(tmp_path, semgrep, mode):  # noqa: F811
    gate = {"test": TEST, "rulepacks": ["mypack"]}
    repo, base = _repo(tmp_path, pack_at_base=True, gate=gate)
    _write_pack(repo, rules=PR_RULES)  # the change rewrites its pack's rule
    m = _machine(repo, base, mode=mode)
    m.advisory_runners = [RulepackRunner()]
    m._run_advisory_axes()
    assert semgrep and all("BASELINE RULE" in rules for rules in semgrep)
    assert not any("PR RULE" in rules for rules in semgrep)
    assert any(a.id.startswith("rulepack:mypack:") for a in m._advisories)


def test_advisory_rulepack_list_is_not_taken_from_the_pr(tmp_path, semgrep):  # noqa: F811
    repo, base = _repo(tmp_path, pack_at_base=True, gate={"test": TEST})
    gate = {"test": TEST, "rulepacks": ["mypack"]}
    (repo / ".code-forge" / "gate.yaml").write_text(yaml.safe_dump(gate), encoding="utf-8")
    m = _machine(repo, base, mode=Mode.CI)
    m.advisory_runners = [RulepackRunner()]
    m._run_advisory_axes()
    assert semgrep == []


def test_refused_advisory_pack_copy_is_a_note_not_a_ci_failure(tmp_path, semgrep, monkeypatch):  # noqa: F811
    gate = {"test": TEST, "rulepacks": ["mypack"]}
    repo, base = _repo(tmp_path, pack_at_base=True, gate=gate)

    def refuse(**_kw):
        raise gate_check.OutsideConfigRootRefused("controlled refusal")

    monkeypatch.setattr(gate_check, "materialize_trusted_packs", refuse)
    m = _machine(repo, base, mode=Mode.CI)
    m.advisory_runners = [RulepackRunner()]
    m._run_advisory_axes()
    assert any("trusted packs refused" in e for e in m._state.infra_errors)
    assert getattr(m, "_trusted_config_refusal", None) is None


def test_advisory_rulepack_with_unreadable_trusted_gate_is_an_infra_note(tmp_path, semgrep):  # noqa: F811
    repo, base = _repo(tmp_path, pack_at_base=True, gate={"test": TEST, "rulepacks": ["mypack"]})
    m = _machine(repo, base, mode=Mode.LOCAL)
    m._trusted_gate_cache = ValueError("trusted gate unreadable")
    m.advisory_runners = [RulepackRunner()]
    m._run_advisory_axes()
    assert semgrep == []
    assert any("failed to load trusted gate.yaml" in e for e in m._state.infra_errors)
    assert PACK and BASE_RULES  # fixtures shared with the blocking-pack tests


# --- CLI wiring --------------------------------------------------------------


@pytest.mark.parametrize("trusted", [False, True])
def test_cli_knowledge_source_follows_the_trusted_gate(sem_controls, cli_pipeline, monkeypatch, trusted):  # noqa: F811
    """CI, non-git checkout: the tree's own gate.yaml enabling knowledge is not
    trusted; the same switch in an outside --test-config-root is."""
    from code_forge import cli, context_sources

    root, args, seen = cli_pipeline
    (root / ".code-forge").mkdir(exist_ok=True)
    (root / ".code-forge/gate.yaml").write_text(yaml.safe_dump(ENABLED_BY_PR), encoding="utf-8")
    if trusted:
        outside = root.parent / f"{root.name}-forge-config"
        (outside / ".code-forge").mkdir(parents=True)
        (outside / ".code-forge/gate.yaml").write_text(
            yaml.safe_dump({"test": TEST, "knowledge": {"enabled": True}}), encoding="utf-8"
        )
        args.test_config_root = str(outside)
    args.mode = "ci"
    names = []
    inner = context_sources.gather

    def gather(sources, *a, **kw):
        names.extend(type(s).__name__ for s in sources)
        return inner(sources, *a, **kw)

    monkeypatch.setattr(context_sources, "gather", gather)
    monkeypatch.setattr("code_forge.graph_triage.shutil.which", lambda _: None)
    monkeypatch.setattr(cli, "_estimate_l1_prompt_tokens", lambda *a, **kw: 0)
    cli._run(args, {"FORGE_PROJECT_DIR": str(root)}, root)
    assert ("KnowledgeSource" in names) is trusted
