"""Repo-local rule packs that can block come from the trusted root (#150).

The blocking rulepack phase reads ``.code-forge/packs/<name>/`` from the
review baseline (or an outside config root), never from the reviewed tree.
Machines use the CLI wiring: ``StateMachine(cwd=repo)``, no ``source_root``.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from code_forge import gate_check
from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.falsify import StubFalsifier
from code_forge.machine import StateMachine
from code_forge.state import Mode

PACK = Path(".code-forge") / "packs" / "mypack"
GATE = {
    "test": {"command": [sys.executable, "-c", "pass"]},
    "rulepacks": ["mypack"],
    "rulepacks_blocking": ["no-eval"],
}
META = {
    "name": "mypack",
    "rules": [{
        "id": "no-eval", "title": "t", "category": "security", "impact_tier": "high",
        "languages": ["python"], "source": "fixture",
    }],
}
BASE_RULES = "rules:\n- id: no-eval\n  pattern: eval(...)\n  message: BASELINE RULE\n  languages: [python]\n  severity: ERROR\n"
PR_RULES = "rules:\n- id: no-eval\n  pattern: never_matches_anything(...)\n  message: PR RULE\n  languages: [python]\n  severity: ERROR\n"


def _git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout.strip()


def _write_pack(root, rules=BASE_RULES, rel=PACK):
    (root / rel).mkdir(parents=True, exist_ok=True)
    (root / rel / "meta.yaml").write_text(yaml.safe_dump(META), encoding="utf-8")
    (root / rel / "rules.yaml").write_text(rules, encoding="utf-8")


def _repo(tmp_path, *, pack_at_base=True, gate=GATE):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    (repo / "src" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (repo / ".code-forge").mkdir()
    (repo / ".code-forge" / "gate.yaml").write_text(yaml.safe_dump(gate), encoding="utf-8")
    if pack_at_base:
        _write_pack(repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "src" / "mod.py").write_text("eval('x')\n", encoding="utf-8")
    return repo, base


def _machine(repo, base, mode=Mode.CI):
    return StateMachine(
        mode=mode,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda finding: None,
        resolved_review=ResolvedReview(
            source_files=[repo / "src" / "mod.py"], baseline_content=None,
            git_diff="--- a/src/mod.py\n+++ b/src/mod.py\n", mode_hint="git", base_sha=base,
        ),
        source_hash="h",
        baseline_spec_repr="h",
        cwd=repo,
        registry={},
    )


@pytest.fixture
def semgrep(monkeypatch):
    """Stub semgrep: record the rules text it was handed; report one hit per
    run whose rules carry the baseline message."""
    seen = []
    real_run = subprocess.run

    def run(argv, **kwargs):
        if argv[:2] != ["semgrep", "scan"]:
            return real_run(argv, **kwargs)  # noqa: PLW1510
        rules = Path(argv[argv.index("--config") + 1]).read_text(encoding="utf-8")
        seen.append(rules)
        hits = [] if "BASELINE RULE" not in rules else [{
            "ruleId": "no-eval", "level": "error", "message": {"text": "eval"},
            "locations": [{"physicalLocation": {
                "artifactLocation": {"uri": "src/mod.py"}, "region": {"startLine": 1}}}],
        }]
        sarif = {"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "semgrep"}}, "results": hits}]}
        return subprocess.CompletedProcess(argv, 1 if hits else 0, json.dumps(sarif), "")

    monkeypatch.setattr("code_forge.rulepack.subprocess.run", run)
    monkeypatch.setattr("code_forge.rulepack.shutil.which", lambda _: "/fixture/semgrep")
    monkeypatch.setattr("code_forge.rulepack.RulepackRunner._print_summary", lambda _self: None)
    return seen


def _blocking(machine):
    return [f for f in machine._run_rulepack_blocking_phase() if f.disposition == Disposition.CONFIRMED]


@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
def test_pr_edit_to_a_blocking_pack_is_not_applied(tmp_path, semgrep, mode):
    repo, base = _repo(tmp_path)
    (repo / PACK / "rules.yaml").write_text(PR_RULES, encoding="utf-8")
    m = _machine(repo, base, mode)
    assert len(_blocking(m)) == 1, m._state.infra_errors
    assert semgrep and all("BASELINE RULE" in r for r in semgrep)


def test_pr_deleting_the_pack_does_not_remove_it(tmp_path, semgrep):
    repo, base = _repo(tmp_path)
    for f in ("meta.yaml", "rules.yaml"):
        (repo / PACK / f).unlink()
    assert len(_blocking(_machine(repo, base))) == 1


@pytest.mark.parametrize("staged", [False, True])
def test_pr_added_pack_is_not_used_in_ci(tmp_path, semgrep, staged):
    repo, base = _repo(tmp_path, pack_at_base=False)
    _write_pack(repo, rules=PR_RULES)
    if staged:
        _git(repo, "add", str(PACK))
    m = _machine(repo, base)
    assert _blocking(m) == [] and semgrep == []
    assert any("rulepack mypack: no trusted copy" in e for e in m._state.infra_errors), m._state.infra_errors


def test_local_staged_pack_is_not_used_but_a_never_tracked_one_is(tmp_path, semgrep):
    repo, base = _repo(tmp_path, pack_at_base=False)
    _write_pack(repo)  # untracked: the user's own setup
    assert len(_blocking(_machine(repo, base, Mode.LOCAL))) == 1
    _git(repo, "add", str(PACK))  # now part of the change under review
    semgrep.clear()
    assert _blocking(_machine(repo, base, Mode.LOCAL)) == [] and semgrep == []


def test_outside_config_root_pack_is_used(tmp_path, semgrep):
    repo, base = _repo(tmp_path, pack_at_base=False)
    _write_pack(repo, rules=PR_RULES)
    outside = tmp_path / "ci-config"
    _write_pack(outside)
    (outside / ".code-forge" / "gate.yaml").write_text(yaml.safe_dump(GATE), encoding="utf-8")
    m = _machine(repo, base)
    m.test_config_root = outside
    assert len(_blocking(m)) == 1, m._state.infra_errors
    assert all("BASELINE RULE" in r for r in semgrep)


def _outside_with(tmp_path, link):
    repo, base = _repo(tmp_path, pack_at_base=False)
    _write_pack(repo, rules=PR_RULES)
    outside = tmp_path / "ci-config"
    (outside / ".code-forge").mkdir(parents=True)
    (outside / ".code-forge" / "gate.yaml").write_text(yaml.safe_dump(GATE), encoding="utf-8")
    link(repo, outside)
    m = _machine(repo, base)
    m.test_config_root = outside
    return m


def _hardlink_rules(repo, outside):
    _write_pack(outside)
    os.unlink(outside / PACK / "rules.yaml")
    os.link(repo / PACK / "rules.yaml", outside / PACK / "rules.yaml")


def _symlink_pack_dir(repo, outside):
    (outside / ".code-forge" / "packs").mkdir()
    (outside / PACK).symlink_to(repo / PACK, target_is_directory=True)


def _symlink_packs_parent(repo, outside):
    (outside / ".code-forge" / "packs").symlink_to(repo / ".code-forge" / "packs", target_is_directory=True)


def _symlink_pack_dir_elsewhere(repo, outside):
    elsewhere = outside.parent / "elsewhere"
    _write_pack(elsewhere, rules=PR_RULES)
    (outside / ".code-forge" / "packs").mkdir()
    (outside / PACK).symlink_to(elsewhere / PACK, target_is_directory=True)


def _symlink_rules_elsewhere(repo, outside):
    _write_pack(outside)
    other = outside.parent / "other.yaml"
    other.write_text(PR_RULES, encoding="utf-8")
    os.unlink(outside / PACK / "rules.yaml")
    (outside / PACK / "rules.yaml").symlink_to(other)


@pytest.mark.parametrize("link", [
    _hardlink_rules, _symlink_pack_dir, _symlink_packs_parent, _symlink_pack_dir_elsewhere,
    _symlink_rules_elsewhere,
])
def test_outside_pack_leading_elsewhere_is_refused(tmp_path, semgrep, link):
    m = _outside_with(tmp_path, link)
    assert _blocking(m) == [] and semgrep == []
    assert any("rulepack: trusted packs refused" in e for e in m._state.infra_errors), m._state.infra_errors
    assert not any("Unknown rulepack" in e for e in m._state.infra_errors)


def test_baseline_pack_symlink_is_refused(tmp_path, semgrep):
    repo, base = _repo(tmp_path, pack_at_base=False)
    elsewhere = tmp_path / "elsewhere"
    _write_pack(elsewhere)
    (repo / ".code-forge" / "packs").mkdir()
    (repo / PACK).symlink_to(elsewhere / PACK, target_is_directory=True)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "link")
    m = _machine(repo, _git(repo, "rev-parse", "HEAD"))
    assert _blocking(m) == [] and semgrep == []
    assert any("is a symlink in the review baseline" in e for e in m._state.infra_errors), m._state.infra_errors


def test_builtin_only_runs_no_git_and_ignores_tree_packs(tmp_path, semgrep, monkeypatch):
    gate = dict(GATE, rulepacks=["vercel-react"], rulepacks_blocking=["no-document-write"])
    repo, base = _repo(tmp_path, gate=gate)
    m = _machine(repo, base)
    m._trusted_gate_cache = {k: gate[k] for k in ("rulepacks", "rulepacks_blocking")}
    monkeypatch.setattr(gate_check, "_git_out", lambda *a, **k: pytest.fail(f"git called: {a}"))
    monkeypatch.setattr(gate_check.subprocess, "run", lambda *a, **k: pytest.fail(f"process: {a}"))
    m._run_rulepack_blocking_phase()
    assert not any("mypack" in e for e in m._state.infra_errors)


def test_invalid_pack_name_is_not_resolved_as_a_path(tmp_path, semgrep):
    gate = dict(GATE, rulepacks=["../../evil"])
    repo, base = _repo(tmp_path, gate=gate)
    m = _machine(repo, base)
    assert _blocking(m) == [] and semgrep == []
    assert any("not a valid pack name" in e for e in m._state.infra_errors), m._state.infra_errors


def test_baseline_show_failure_refuses(tmp_path, semgrep, monkeypatch):
    repo, base = _repo(tmp_path)
    real = gate_check.subprocess.run

    def run(argv, **kw):
        if argv[:2] == ["git", "show"] and "packs" in argv[2]:
            return subprocess.CompletedProcess(argv, 128, b"", b"fatal")
        return real(argv, **kw)

    monkeypatch.setattr(gate_check.subprocess, "run", run)
    m = _machine(repo, base)
    assert _blocking(m) == [] and semgrep == []
    assert any("refusing pack mypack" in e for e in m._state.infra_errors), m._state.infra_errors


_ALIAS = r"""
import subprocess, sys, tempfile
from pathlib import Path
from code_forge.gate_check import UntrustedGateConfigError, materialize_trusted_packs
repo, base, scratch = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
alias = scratch / "alias"
alias.mkdir()
subprocess.run(["mount", "--bind", str(repo), str(alias)], check=True)
try:
    materialize_trusted_packs(config_root=alias / "cfg", source_root=repo, base_sha=base,
                              allow_untracked=False, names=["mypack"], dest=Path(tempfile.mkdtemp()))
except UntrustedGateConfigError as exc:
    print("REFUSED", exc)
else:
    print("LOADED")
"""


def _unshare_ok():
    import shutil

    return shutil.which("unshare") is not None and subprocess.run(
        ["unshare", "-rm", "true"], capture_output=True, check=False,
    ).returncode == 0


@pytest.mark.skipif(not _unshare_ok(), reason="unprivileged user/mount namespaces unavailable")
def test_outside_packs_root_that_is_a_bind_mount_alias_is_refused(tmp_path):
    repo, base = _repo(tmp_path, pack_at_base=False)
    _write_pack(repo / "cfg", rules=PR_RULES)  # PR-authored packs under the checkout
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    out = subprocess.run(
        ["unshare", "-rm", sys.executable, "-c", _ALIAS, str(repo), base, str(scratch)],
        capture_output=True, text=True, check=False,
        env={**os.environ, "PYTHONPATH": str(Path(gate_check.__file__).parents[1])},
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.startswith("REFUSED") and "bind mount alias" in out.stdout, out.stdout


@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
def test_ci_refused_trusted_packs_fail_the_verdict(tmp_path, semgrep, monkeypatch, mode):
    """#157 on #150: a refused pack root leaves blocking rules unmeasured; CI must
    FAIL with the reason rather than PASS with the phase skipped."""
    m = _outside_with(tmp_path, _hardlink_rules)
    m.mode = mode
    m._trusted_gate_cache = {k: GATE[k] for k in ("rulepacks", "rulepacks_blocking")}
    assert _blocking(m) == []
    if mode is Mode.LOCAL:
        assert m._trusted_config_refusal is None
        return
    assert m._trusted_config_refusal.startswith("trusted rule packs refused:")
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(StateMachine, "_execute_round", lambda self, round_index: None)
    assert m._run_ci().name == "FAIL", m._state.infra_errors
    assert any(e.startswith("CI: trusted rule packs refused:") for e in m._state.infra_errors)


def test_ci_refused_baseline_pack_keeps_the_infra_note_only(tmp_path, semgrep):
    """Scope of #157: only an operator-configured outside root FAILs CI; a
    baseline pack refusal (symlink in the baseline) keeps its infra note."""
    repo, base = _repo(tmp_path, pack_at_base=False)
    elsewhere = tmp_path / "elsewhere"
    _write_pack(elsewhere)
    (repo / ".code-forge" / "packs").mkdir()
    (repo / PACK).symlink_to(elsewhere / PACK, target_is_directory=True)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "link")
    m = _machine(repo, _git(repo, "rev-parse", "HEAD"))
    assert _blocking(m) == []
    assert m._trusted_config_refusal is None


def test_duplicated_local_pack_name_still_blocks(tmp_path, semgrep):
    # #177: rulepacks: [mypack, mypack] used to refuse every trusted pack.
    repo, base = _repo(tmp_path, gate=dict(GATE, rulepacks=["mypack", "mypack"]))
    m = _machine(repo, base)
    assert _blocking(m), m._state.infra_errors
    assert not any("refused" in e for e in m._state.infra_errors), m._state.infra_errors


def test_baseline_show_timeout_is_a_reported_refusal(tmp_path, semgrep, monkeypatch):
    # #177: a git show timeout while reading a pack is an infra error, not a crash.
    repo, base = _repo(tmp_path)
    real = gate_check.subprocess.run

    def run(argv, **kw):
        if argv[:2] == ["git", "show"] and "packs" in argv[2]:
            raise subprocess.TimeoutExpired(argv, 10)
        return real(argv, **kw)

    monkeypatch.setattr(gate_check.subprocess, "run", run)
    m = _machine(repo, base)
    assert _blocking(m) == [] and semgrep == []
    assert any("timed out" in e and "refusing pack mypack" in e for e in m._state.infra_errors), m._state.infra_errors


@pytest.mark.parametrize("failure", ["timeout", "exit"])
def test_ci_baseline_pack_read_failure_fails_the_verdict(tmp_path, semgrep, monkeypatch, failure):
    """#189: a git show timeout or failure on a baseline pack leaves
    rulepacks_blocking unmeasured; CI FAILs with that reason, never PASS."""
    repo, base = _repo(tmp_path)
    real = gate_check.subprocess.run

    def run(argv, **kw):
        if argv[:2] == ["git", "show"] and "packs" in argv[2]:
            if failure == "timeout":
                raise subprocess.TimeoutExpired(argv, 10)
            return subprocess.CompletedProcess(argv, 128, b"", b"fatal: transient")
        return real(argv, **kw)

    monkeypatch.setattr(gate_check.subprocess, "run", run)
    m = _machine(repo, base)
    assert _blocking(m) == []
    assert m._trusted_config_refusal.startswith("trusted rule packs refused:")
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(StateMachine, "_execute_round", lambda self, round_index: None)
    assert m._run_ci().name == "FAIL", m._state.infra_errors
    assert any(e.startswith("CI: trusted rule packs refused:") for e in m._state.infra_errors)
