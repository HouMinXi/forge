"""Review-time gate.yaml keys outside ``test.*`` come from the trusted gate (#141).

``ledger.enabled``, ``rulepacks`` / ``rulepacks_blocking``, ``pinned_paths`` and
``style_downgrade`` are read from the review baseline (or an outside config
root), never from the reviewed tree. Every machine uses the real CLI wiring:
``StateMachine(cwd=repo)`` with no ``source_root``.
"""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from code_forge import machine as machine_mod
from code_forge.advisory import AdvisoryFinding
from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.falsify import StubFalsifier
from code_forge.gate_check import load_trusted_gate_data
from code_forge.machine import StateMachine
from code_forge.state import Mode, StateFinding

GATE = Path(".code-forge") / "gate.yaml"
TEST = {"command": [sys.executable, "-c", "pass"], "timeout_seconds": 60}


def _git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout.strip()


def _write(repo, data):
    (repo / GATE).parent.mkdir(parents=True, exist_ok=True)
    (repo / GATE).write_text(yaml.safe_dump(data), encoding="utf-8")


def _repo(tmp_path, base, pr, *, base_has_test=True):
    """Baseline commits ``base`` (or no gate when None); the reviewed tree
    carries ``pr`` (tracked edit), the change under review."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    (repo / "src" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    if base is not None:
        _write(repo, ({"test": TEST} if base_has_test else {}) | base)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    sha = _git(repo, "rev-parse", "HEAD")
    (repo / "src" / "mod.py").write_text("x = 2\n", encoding="utf-8")
    if pr is not None:
        _write(repo, {"test": TEST} | pr)
        _git(repo, "add", str(GATE))
    return repo, sha


def _machine(repo, base, mode=Mode.CI):
    return StateMachine(
        mode=mode,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda finding: None,
        resolved_review=ResolvedReview(
            source_files=[repo / "src" / "mod.py"], baseline_content=None,
            git_diff="--- a/src/mod.py\n+++ b/src/mod.py\n", mode_hint="git",
            base_sha=base, head_sha="b" * 40,
        ),
        source_hash="h",
        baseline_spec_repr="h",
        cwd=repo,  # CLI wiring: config root == reviewed tree
        registry={},
    )


def _ledger_written(machine, monkeypatch):
    """True when the ledger write path ran past the kill-switch."""
    calls = []

    def fake_root(root):
        calls.append(root)
        raise OSError("stop after the kill-switch")

    monkeypatch.setattr(machine_mod, "resolve_ledger_root", fake_root)
    machine._write_ci_ledger_rows()
    return bool(calls)


# --- ledger.enabled ---------------------------------------------------------

def test_pr_cannot_switch_off_its_own_ci_ledger(tmp_path, monkeypatch):
    repo, base = _repo(tmp_path, {}, {"ledger": {"enabled": False}})
    assert _ledger_written(_machine(repo, base), monkeypatch)


def test_baseline_ledger_switch_still_applies(tmp_path, monkeypatch):
    repo, base = _repo(tmp_path, {"ledger": {"enabled": False}}, {"ledger": {"enabled": True}})
    assert not _ledger_written(_machine(repo, base), monkeypatch)


def test_review_only_baseline_gate_without_test_section_is_read(tmp_path, monkeypatch):
    repo, base = _repo(tmp_path, {"ledger": {"enabled": False}}, None, base_has_test=False)
    assert not _ledger_written(_machine(repo, base), monkeypatch)


def test_ci_untracked_in_tree_switch_is_ignored_but_local_uses_it(tmp_path, monkeypatch):
    repo, base = _repo(tmp_path, None, None)
    _write(repo, {"ledger": {"enabled": False}})  # untracked
    assert _ledger_written(_machine(repo, base, Mode.CI), monkeypatch)
    assert not _ledger_written(_machine(repo, base, Mode.LOCAL), monkeypatch)


def test_outside_config_root_switch_applies(tmp_path, monkeypatch):
    repo, base = _repo(tmp_path, {}, {})
    outside = tmp_path / "ci-config"
    _write(outside, {"ledger": {"enabled": False}})
    m = _machine(repo, base)
    m.test_config_root = outside
    assert not _ledger_written(m, monkeypatch)


def test_malformed_trusted_gate_fails_open_with_an_infra_error(tmp_path, monkeypatch):
    repo, base = _repo(tmp_path, None, None)
    (repo / GATE).parent.mkdir()
    (repo / GATE).write_text("ledger: [unclosed\n", encoding="utf-8")
    _git(repo, "add", str(GATE))
    _git(repo, "commit", "-qm", "bad gate")
    base = _git(repo, "rev-parse", "HEAD")
    m = _machine(repo, base)
    assert _ledger_written(m, monkeypatch)
    assert any("kill-switch read failed" in e for e in m._state.infra_errors)


def test_raw_loader_rejects_a_non_mapping(tmp_path):
    repo, base = _repo(tmp_path, None, None)
    (repo / GATE).parent.mkdir()
    (repo / GATE).write_text("- a\n- b\n", encoding="utf-8")
    _git(repo, "add", str(GATE))
    _git(repo, "commit", "-qm", "list gate")
    with pytest.raises(ValueError, match="must be a mapping"):
        load_trusted_gate_data(config_root=repo, source_root=repo,
                               base_sha=_git(repo, "rev-parse", "HEAD"), allow_untracked=False)


# --- pinned_paths / style_downgrade -------------------------------------------

def _confirmed(file="src/mod.py", source="L0", description="real bug"):
    return StateFinding(
        id="f1", fingerprint="f1", source=source, disposition=Disposition.CONFIRMED,
        file=file, line_range=[1, 1], description=description,
    )


@pytest.mark.parametrize("key", [
    {"pinned_paths": ["src/*.py"]},
    {"style_downgrade": {"pass_names": ["L0"]}},
    {"style_downgrade": {"keywords": ["bug"]}},
])
def test_pr_added_suppression_keys_are_not_applied(tmp_path, key):
    repo, base = _repo(tmp_path, {}, key)
    m = _machine(repo, base)
    m._state.findings = [_confirmed()]
    m._suppress_known_findings()
    assert m._state.findings[0].disposition == Disposition.CONFIRMED


@pytest.mark.parametrize(("key", "expected"), [
    ({"pinned_paths": ["src/*.py"]}, Disposition.DISMISSED),
    ({"style_downgrade": {"keywords": ["bug"]}}, Disposition.STYLE),
])
def test_baseline_suppression_keys_still_apply(tmp_path, key, expected):
    repo, base = _repo(tmp_path, key, {})
    m = _machine(repo, base)
    m._state.findings = [_confirmed()]
    m._suppress_known_findings()
    assert m._state.findings[0].disposition == expected


# --- rulepacks / rulepacks_blocking -----------------------------------------

class _FakeRunner:
    def __init__(self):
        self.infra_errors = []
        self.source_files = None
        self.seen = None

    def run(self, diff_text, repo_root, *, execution_root=None, gate_config=None):
        self.seen = gate_config
        if "testpack" not in (gate_config or {}).get("rulepacks", []):
            return []
        return [AdvisoryFinding(
            id="rulepack:testpack:src/mod.py:1:rule-a", axis="rulepack:testpack",
            file="src/mod.py", line_range=(1, 1), description="rule-a hit",
            attribution="semgrep-ce/rulepack",
        )]


@pytest.mark.parametrize("pr", [
    {"rulepacks": ["testpack"]},  # drops rulepacks_blocking
    {"rulepacks": [], "rulepacks_blocking": ["rule-a"]},  # drops the pack
    {},
])
def test_pr_cannot_unblock_its_own_rulepack_findings(tmp_path, pr):
    base_gate = {"rulepacks": ["testpack"], "rulepacks_blocking": ["rule-a"]}
    repo, base = _repo(tmp_path, base_gate, pr)
    runner = _FakeRunner()
    with patch("code_forge.rulepack.RulepackRunner", lambda: runner):
        findings = _machine(repo, base)._run_rulepack_blocking_phase()
    assert runner.seen["rulepacks_blocking"] == ["rule-a"]
    assert [f.disposition for f in findings] == [Disposition.CONFIRMED]


def test_pr_cannot_promote_rulepack_blocking_before_baseline(tmp_path):
    repo, base = _repo(tmp_path, {"rulepacks": ["testpack"]}, {"rulepacks": ["testpack"], "rulepacks_blocking": ["rule-a"]})
    runner = _FakeRunner()
    with patch("code_forge.rulepack.RulepackRunner", lambda: runner):
        assert _machine(repo, base)._run_rulepack_blocking_phase() == []


def test_real_runner_uses_the_passed_gate_config(tmp_path):
    """RulepackRunner.run(gate_config=...) does not reread the tree's gate.yaml."""
    from code_forge.rulepack import RulepackRunner

    _write(tmp_path, {"test": TEST, "rulepacks": ["no-such-pack"]})
    runner = RulepackRunner()
    assert runner.run("", tmp_path, gate_config={"rulepacks": []}) == []
    assert runner.infra_errors == []
    runner.run("", tmp_path)
    assert any("no-such-pack" in e for e in runner.infra_errors)


def test_source_root_wiring_passes_the_trusted_gate_to_the_runner(tmp_path):
    repo, base = _repo(tmp_path, {"rulepacks": ["testpack"], "rulepacks_blocking": ["rule-a"]}, {})
    m = _machine(repo, base)
    m.source_root = repo
    runner = _FakeRunner()
    with patch("code_forge.rulepack.RulepackRunner", lambda: runner):
        findings = m._run_rulepack_blocking_phase()
    assert runner.seen["rulepacks_blocking"] == ["rule-a"]
    assert len(findings) == 1


@pytest.mark.parametrize("base_t,pr_t,expected", [
    (900, 900, 900),  # #175: the trusted timeout reaches the runner
    (900, 5, 900),  # a PR-only edit does not
    (None, 5, None),  # nor does a PR-added one
])
def test_trusted_rulepack_timeout_reaches_the_runner(tmp_path, base_t, pr_t, expected):
    def gate(t):
        return {"rulepacks": ["testpack"], "rulepacks_blocking": ["rule-a"]} | (
            {"rulepack_timeout_seconds": t} if t is not None else {}
        )

    repo, base = _repo(tmp_path, gate(base_t), gate(pr_t))
    runner = _FakeRunner()
    with patch("code_forge.rulepack.RulepackRunner", lambda: runner):
        _machine(repo, base)._run_rulepack_blocking_phase()
    assert runner.seen.get("rulepack_timeout_seconds") == expected
