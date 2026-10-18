"""Review-time ``test.*`` keys come from a source the change cannot edit (#140).

Every machine here uses the real CLI wiring: ``StateMachine(cwd=repo)`` with
no ``source_root``, so the config root *is* the reviewed tree.
"""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from code_forge import mutation
from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.falsify import StubFalsifier
from code_forge.fixval import FixvalResult, FixvalStatus
from code_forge.gate_check import load_trusted_gate_config
from code_forge.machine import StateMachine
from code_forge.state import Mode, Verdict

BASE_CMD = [sys.executable, "-c", "print('baseline')"]
PR_CMD = [sys.executable, "-c", "print('changed by the PR')"]
GATE = Path(".code-forge") / "gate.yaml"


def _git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout.strip()


def _write_gate(repo, command, timeout=111):
    (repo / GATE).parent.mkdir(parents=True, exist_ok=True)
    (repo / GATE).write_text(
        yaml.safe_dump({"test": {"command": command, "timeout_seconds": timeout}}), encoding="utf-8",
    )


def _repo(tmp_path, *, gate_at_base, tracked=True):
    repo = tmp_path / "repo"
    (repo / "src" / "pkg").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    (repo / "src" / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    if gate_at_base:
        _write_gate(repo, BASE_CMD)
        _git(repo, "add", "-A")
    else:
        _git(repo, "add", "src")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "src" / "pkg" / "mod.py").write_text("x = 2\n", encoding="utf-8")
    if not gate_at_base:
        _write_gate(repo, PR_CMD, timeout=999)
        if tracked:
            _git(repo, "add", str(GATE))
    return repo, base


def _machine(repo, base, mode=Mode.LOCAL, files=("src/pkg/mod.py",)):
    return StateMachine(
        mode=mode,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda finding: None,
        resolved_review=ResolvedReview(
            source_files=[repo / f for f in files], baseline_content=None,
            git_diff="--- a/src/pkg/mod.py\n+++ b/src/pkg/mod.py\n", mode_hint="git",
            base_sha=base,
        ),
        source_hash="trusted-test-config",
        baseline_spec_repr="trusted-test-config",
        cwd=repo,  # the CLI wiring: config root == reviewed tree
        registry={},
    )


def _l2_calls(machine):
    calls = []

    def runner(files, baseline_cmd, **kwargs):
        calls.append((baseline_cmd, kwargs["baseline_timeout"]))
        return [], []

    machine.l2_runner = runner
    machine._run_l2_phase()
    return calls


def test_pr_edit_to_test_command_and_timeout_does_not_reach_l2(tmp_path):
    repo, base = _repo(tmp_path, gate_at_base=True)
    _write_gate(repo, PR_CMD, timeout=999)  # the reviewed change edits gate.yaml
    machine = _machine(repo, base)
    assert machine._source_root() == repo  # config root is the reviewed tree
    assert _l2_calls(machine) == [(BASE_CMD, 111)]


def test_first_ever_tracked_gate_added_by_the_change_is_refused(tmp_path):
    repo, base = _repo(tmp_path, gate_at_base=False, tracked=True)
    machine = _machine(repo, base)
    assert _l2_calls(machine) == []
    assert any(
        "no .code-forge/gate.yaml at review baseline" in e and "takes effect once it is on the baseline" in e
        for e in machine._state.infra_errors
    ), machine._state.infra_errors


def test_untracked_gate_is_not_part_of_the_change_and_is_used(tmp_path):
    repo, base = _repo(tmp_path, gate_at_base=False, tracked=False)
    assert _l2_calls(_machine(repo, base)) == [(PR_CMD, 999)]


def test_outside_config_root_is_used_verbatim(tmp_path):
    repo, base = _repo(tmp_path, gate_at_base=True)
    outside = tmp_path / "config"
    _write_gate(outside, [sys.executable, "-c", "print('outside')"], timeout=7)
    machine = _machine(repo, base)
    machine.cwd, machine.source_root = outside, repo
    assert _l2_calls(machine) == [([sys.executable, "-c", "print('outside')"], 7)]


def test_fixval_runs_the_baseline_test_command(tmp_path, monkeypatch):
    repo, base = _repo(tmp_path, gate_at_base=True)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_mod.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    _write_gate(repo, PR_CMD)
    machine = _machine(repo, base, files=("src/pkg/mod.py", "tests/test_mod.py"))
    machine._state.verdict = Verdict.PASS
    # Isolate FIXVAL from the receipt acceptance gate that precedes it.
    monkeypatch.setattr(StateMachine, "_receipt_gate_round_errors", lambda self: [])
    monkeypatch.setattr(StateMachine, "_receipt_gate_terminal_errors", lambda self: [])
    seen = []

    def fake_run_fixval(candidate, test_cmd, *args, **kwargs):
        seen.append(test_cmd)
        return FixvalResult(status=FixvalStatus.PASS, findings=[], advisories=[])

    with patch("code_forge.fixval.run_fixval", fake_run_fixval):
        machine._finalize_local_terminal()
    assert seen == [BASE_CMD], (machine._state.infra_errors, [f.description for f in machine._state.findings])


def test_ci_detached_launch_gets_the_baseline_command(tmp_path, monkeypatch):
    repo, base = _repo(tmp_path, gate_at_base=True)
    _write_gate(repo, PR_CMD, timeout=999)
    machine = _machine(repo, base, mode=Mode.CI)
    monkeypatch.setattr(mutation.shutil, "which", lambda name: "/tools/mutmut")
    monkeypatch.setattr(StateMachine, "_execute_round", lambda self, round_index: None)
    launched = []

    def launch(*args, **kwargs):
        launched.append((args, kwargs))
        raise RuntimeError("stop after capture")

    monkeypatch.setattr("code_forge.machine.launch_detached_mutation", launch, raising=False)
    monkeypatch.setattr(mutation, "launch_detached_mutation", launch, raising=False)
    try:
        machine._run_ci()
    except Exception:  # noqa: BLE001 -- the capture is all this test needs
        pass
    flat = repr(launched)
    assert "print('baseline')" in flat and "changed by the PR" not in flat, flat


def test_admission_reads_the_baseline_not_a_broken_pr_gate(tmp_path, monkeypatch):
    repo, base = _repo(tmp_path, gate_at_base=True)
    (repo / GATE).write_text("test: {command: 5}\n", encoding="utf-8")  # PR breaks it
    monkeypatch.setattr(mutation.shutil, "which", lambda name: "/tools/mutmut")
    error = mutation.mutation_review_admission_error(
        target_kind="INDEX", git_diff="--- a/src/pkg/mod.py\n+++ b/src/pkg/mod.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n",
        source_files=[repo / "src" / "pkg" / "mod.py"], source_root=repo, config_root=repo,
        runner=lambda *a, **k: ([], []), ci_detached=True, base_sha=base,
    )
    assert error and "INDEX source is unsupported" in error


def test_loader_units(tmp_path):
    repo, base = _repo(tmp_path, gate_at_base=True)
    _write_gate(repo, PR_CMD)
    assert load_trusted_gate_config(config_root=repo, source_root=repo, base_sha=base)["test"]["command"] == BASE_CMD
    # invalid baseline content is a ValueError, not a silent fallback
    (repo / GATE).write_text("test: {command: 5}\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "broken gate")
    with pytest.raises(ValueError):
        load_trusted_gate_config(config_root=repo, source_root=repo, base_sha=_git(repo, "rev-parse", "HEAD"))
    # tracked gate with no baseline revision at all: refused
    with pytest.raises(FileNotFoundError):
        load_trusted_gate_config(config_root=repo, source_root=repo, base_sha=None)
    # not a git checkout: the file is the only config there is
    plain = tmp_path / "plain"
    _write_gate(plain, BASE_CMD)
    assert load_trusted_gate_config(config_root=plain, source_root=plain, base_sha=None)["test"]["command"] == BASE_CMD


def test_no_note_when_gate_yaml_is_not_in_the_diff(tmp_path):
    repo, base = _repo(tmp_path, gate_at_base=True)
    machine = _machine(repo, base)
    machine.resolved_review = ResolvedReview(
        source_files=machine.resolved_review.source_files, baseline_content=None,
        git_diff=_git(repo, "diff", base), mode_hint="git", base_sha=base,
    )
    _l2_calls(machine)
    assert not [a for a in machine._advisories if a.id == "GATE_YAML_EDIT_NOT_APPLIED"]


def _write_globs(repo, skip):
    (repo / GATE).parent.mkdir(parents=True, exist_ok=True)
    (repo / GATE).write_text(
        yaml.safe_dump({"test": {"command": BASE_CMD, "mutation_skip_globs": skip}}), encoding="utf-8",
    )


def test_globs_follow_the_test_command_rule_for_an_untracked_gate(tmp_path):
    """One rule: an untracked gate.yaml that supplies test.command supplies the globs too."""
    repo, base = _repo(tmp_path, gate_at_base=False, tracked=False)
    _write_globs(repo, ["src/pkg/mod.py"])
    same = dict(config_root=repo, source_root=repo, base_sha=base)
    assert load_trusted_gate_config(**same)["test"]["command"] == BASE_CMD
    assert mutation.trusted_mutation_globs(**same) == (["src/pkg/mod.py"], None)


def test_globs_follow_the_test_command_rule_for_a_first_ever_tracked_gate(tmp_path):
    repo, base = _repo(tmp_path, gate_at_base=False, tracked=True)
    _write_globs(repo, ["src/pkg/mod.py"])
    _git(repo, "add", str(GATE))
    notes = []
    same = dict(config_root=repo, source_root=repo, base_sha=base)
    assert mutation.trusted_mutation_globs(**same, notes=notes) == (None, None)
    assert notes == []  # no trusted file is not an error: plain defaults


def test_unreadable_baseline_gate_falls_back_to_default_globs_with_a_note(tmp_path):
    repo, _ = _repo(tmp_path, gate_at_base=True)
    (repo / GATE).write_text(": [ not yaml", encoding="utf-8")
    _git(repo, "commit", "-qam", "broken gate")
    bad = _git(repo, "rev-parse", "HEAD")
    notes = []
    assert mutation.trusted_mutation_globs(
        config_root=repo, source_root=repo, base_sha=bad, notes=notes,
    ) == (None, None)
    assert len(notes) == 1 and "using the default test-path globs" in notes[0]
    machine = _machine(repo, bad)
    assert machine._trusted_mutation_globs({}) == (None, None)
    assert any(
        e.startswith("L2: mutation globs:") and "default test-path globs" in e
        for e in machine._state.infra_errors
    ), machine._state.infra_errors


def test_exit_6_docs_row_quotes_the_exact_preflight_message(tmp_path, monkeypatch):
    """#139 forge P3: the docs quote the message the preflight actually raises."""
    from code_forge import _mutation_workspace as workspace
    from code_forge.machine import TimeoutBreaker

    repo, base = _repo(tmp_path, gate_at_base=True)
    machine = _machine(repo, base)
    machine.l2_runner = mutation.run_mutation
    monkeypatch.setattr("code_forge.machine.is_native_mutation_runner", lambda *a, **k: True)
    monkeypatch.setattr(workspace, "find_orphan_container", lambda root: Path("<path>"))
    with pytest.raises(TimeoutBreaker) as raised:
        machine._preflight_mutation_workspace()
    message = str(raised.value)
    documented = message.split("<path>")[0] + "<path>; ...`"
    row = next(
        line for line in (Path(__file__).parents[1] / "docs" / "setup-ci.md").read_text(encoding="utf-8").splitlines()
        if line.startswith("| 6 |")
    )
    assert "`" + documented in row, (documented, row)


# --- PR #142 review: P2-A (symlink/submodule), P2-B (CI untracked), P3s ---

from code_forge.gate_check import UntrustedGateConfigError  # noqa: E402

NOTE_ID = "GATE_YAML_EDIT_NOT_APPLIED"


def _with_diff(machine, repo, base):
    _git(repo, "add", "-A")
    machine.resolved_review = ResolvedReview(
        source_files=machine.resolved_review.source_files, baseline_content=None,
        git_diff=_git(repo, "diff", "--cached", base), mode_hint="git", base_sha=base,
    )
    return machine


def _symlinked_gate_repo(tmp_path):
    repo, base = _repo(tmp_path, gate_at_base=False, tracked=False)
    (repo / GATE).unlink()
    (repo / ".code-forge").rmdir()
    _write_gate(repo / "cfg", PR_CMD, timeout=999)
    (repo / ".code-forge").symlink_to("cfg")
    _git(repo, "add", "-A")
    return repo, base


def test_symlinked_code_forge_dir_does_not_bypass_first_ever_refusal(tmp_path):
    repo, base = _symlinked_gate_repo(tmp_path)
    machine = _machine(repo, base)
    assert _l2_calls(machine) == []
    assert any("is a symlink in the index" in e for e in machine._state.infra_errors), machine._state.infra_errors


def test_untracked_symlinked_code_forge_dir_is_refused(tmp_path):
    repo, base = _symlinked_gate_repo(tmp_path)
    _git(repo, "rm", "-q", "--cached", ".code-forge")
    with pytest.raises(UntrustedGateConfigError, match="symlink in the checkout"):
        load_trusted_gate_config(config_root=repo, source_root=repo, base_sha=base)


def test_symlinked_gate_file_is_refused_even_with_a_baseline_gate(tmp_path):
    repo, base = _repo(tmp_path, gate_at_base=True)
    _write_gate(repo / "cfg", PR_CMD)
    (repo / GATE).unlink()
    (repo / GATE).symlink_to("../cfg/.code-forge/gate.yaml")
    _git(repo, "add", "-A")
    with pytest.raises(UntrustedGateConfigError, match=r"\.code-forge/gate\.yaml is a symlink in the index"):
        load_trusted_gate_config(config_root=repo, source_root=repo, base_sha=base)


def test_submodule_at_code_forge_is_refused(tmp_path):
    repo, base = _repo(tmp_path, gate_at_base=False, tracked=False)
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{base},.code-forge")
    with pytest.raises(UntrustedGateConfigError, match="is a submodule in the index"):
        load_trusted_gate_config(config_root=repo, source_root=repo, base_sha=base)


def test_symlink_in_the_baseline_tree_is_refused(tmp_path):
    repo, _ = _symlinked_gate_repo(tmp_path)
    _git(repo, "commit", "-qm", "symlinked gate dir")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "rm", "-q", "--cached", ".code-forge")  # index no longer shows it
    (repo / ".code-forge").unlink()
    with pytest.raises(UntrustedGateConfigError, match="symlink in the review baseline"):
        load_trusted_gate_config(config_root=repo, source_root=repo, base_sha=head)


def test_ci_refuses_an_untracked_in_tree_gate_for_test_and_globs(tmp_path):
    repo, base = _repo(tmp_path, gate_at_base=False, tracked=False)
    machine = _machine(repo, base, mode=Mode.CI)
    assert _l2_calls(machine) == []
    assert any("CI does not trust an untracked in-tree gate.yaml" in e and "--test-config-root" in e
               for e in machine._state.infra_errors), machine._state.infra_errors
    notes = []
    assert mutation.trusted_mutation_globs(
        config_root=repo, source_root=repo, base_sha=base, notes=notes, allow_untracked=False,
    ) == (None, None)
    assert notes and "CI does not trust" in notes[0]
    # LOCAL keeps the untracked behaviour (#143 tracks the WORKING risk)
    assert _l2_calls(_machine(repo, base)) == [(PR_CMD, 999)]


def test_ci_uses_an_explicit_outside_test_config_root(tmp_path):
    repo, base = _repo(tmp_path, gate_at_base=False, tracked=False)
    outside = tmp_path / "ci-config"
    _write_gate(outside, BASE_CMD, timeout=5)
    machine = _machine(repo, base, mode=Mode.CI)
    machine.test_config_root = outside
    assert _l2_calls(machine) == [(BASE_CMD, 5)]


def _run_until_admission(machine, monkeypatch):
    monkeypatch.setattr("code_forge.machine.mutation_review_admission_error", lambda **k: "stop")
    try:
        machine.run()
    except Exception:  # noqa: BLE001 -- only the advisory matters here
        pass
    return [a for a in machine._advisories if a.id == NOTE_ID]


def test_advisory_for_a_gate_only_edit(tmp_path, monkeypatch):
    repo, base = _repo(tmp_path, gate_at_base=True)
    (repo / "src" / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")  # gate-only diff
    _write_gate(repo, PR_CMD)
    notes = _run_until_admission(_with_diff(_machine(repo, base), repo, base), monkeypatch)
    assert len(notes) == 1
    assert notes[0].description == (
        "test.* and mutation globs from this change's gate.yaml were not applied; other keys: see #141"
    )


def test_advisory_for_a_first_ever_gate_and_the_symlink_case(tmp_path, monkeypatch):
    repo, base = _repo(tmp_path / "a", gate_at_base=False, tracked=True)
    assert len(_run_until_admission(_with_diff(_machine(repo, base), repo, base), monkeypatch)) == 1
    repo, base = _symlinked_gate_repo(tmp_path / "b")
    assert len(_run_until_admission(_with_diff(_machine(repo, base), repo, base), monkeypatch)) == 1


def test_advisory_once_and_not_for_an_outside_config_root(tmp_path, monkeypatch):
    repo, base = _repo(tmp_path, gate_at_base=True)
    _write_gate(repo, PR_CMD)
    machine = _with_diff(_machine(repo, base), repo, base)
    _l2_calls(machine)
    assert len(_run_until_admission(machine, monkeypatch)) == 1  # once across run + L2
    outside = tmp_path / "cfg"
    _write_gate(outside, BASE_CMD)
    machine = _with_diff(_machine(repo, base), repo, base)
    machine.test_config_root = outside
    assert _run_until_admission(machine, monkeypatch) == []


def test_invalid_baseline_never_falls_back_to_a_valid_working_copy(tmp_path):
    repo, _ = _repo(tmp_path, gate_at_base=True)
    (repo / GATE).write_text("test: {command: 5}\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "broken gate")
    bad = _git(repo, "rev-parse", "HEAD")
    _write_gate(repo, PR_CMD)  # valid, differing working copy
    with pytest.raises(ValueError):
        load_trusted_gate_config(config_root=repo, source_root=repo, base_sha=bad)


# --- PR #142 re-review: the outside config root must not lead back into the tree ---

def _evil_outside(tmp_path, repo, kind):
    outside = tmp_path / "evil-config"
    outside.mkdir()
    if kind == "dir":
        (outside / ".code-forge").symlink_to(repo / ".code-forge", target_is_directory=True)
    elif kind == "hardlink":
        (outside / ".code-forge").mkdir()
        os.link(repo / GATE, outside / ".code-forge" / "gate.yaml")
    else:
        (outside / ".code-forge").mkdir()
        (outside / ".code-forge" / "gate.yaml").symlink_to(repo / GATE)
    return outside


@pytest.mark.parametrize("kind", ["dir", "file"])
def test_ci_outside_root_symlinked_into_the_tree_is_refused(tmp_path, kind):
    repo, base = _repo(tmp_path, gate_at_base=False, tracked=False)  # in-tree gate holds PR_CMD
    machine = _machine(repo, base, mode=Mode.CI)
    machine.test_config_root = _evil_outside(tmp_path, repo, kind)
    assert _l2_calls(machine) == []
    assert any("is a symlink; an outside test config root must hold a regular" in e
               for e in machine._state.infra_errors), machine._state.infra_errors


def test_ci_outside_root_hardlinked_to_the_tree_gate_is_refused(tmp_path):
    """#142 re-review: a hardlink shares inode with the in-tree gate, so
    islink/realpath/O_NOFOLLOW all pass -- refuse by (st_dev, st_ino)."""
    repo, base = _repo(tmp_path, gate_at_base=False, tracked=False)
    machine = _machine(repo, base, mode=Mode.CI)
    machine.test_config_root = _evil_outside(tmp_path, repo, "hardlink")
    assert _l2_calls(machine) == []
    assert any("is a hardlink of a file in the reviewed checkout" in e
               for e in machine._state.infra_errors), machine._state.infra_errors
    # Directories cannot be hardlinked (link(2) returns EPERM on Linux), so a
    # .code-forge directory hardlink is not a case: only the file matters.
    with pytest.raises(OSError):
        os.link(repo / ".code-forge", tmp_path / "dir-hardlink")


def test_ci_outside_root_hardlinked_to_a_pr_added_file_is_refused(tmp_path):
    """A PR adds evil.yaml (valid gate content) elsewhere and hardlinks it
    into the outside root -- refuse any checkout inode, not only gate.yaml."""
    repo, base = _repo(tmp_path, gate_at_base=False, tracked=False)
    evil = repo / "evil.yaml"
    evil.write_text(yaml.safe_dump({"test": {"command": PR_CMD, "timeout_seconds": 999}}), encoding="utf-8")
    _git(repo, "add", "evil.yaml")
    outside = tmp_path / "evil-config"
    (outside / ".code-forge").mkdir(parents=True)
    os.link(evil, outside / ".code-forge" / "gate.yaml")
    machine = _machine(repo, base, mode=Mode.CI)
    machine.test_config_root = outside
    assert _l2_calls(machine) == []
    assert any("is a hardlink of a file in the reviewed checkout" in e
               for e in machine._state.infra_errors), machine._state.infra_errors


def test_outside_root_resolving_into_the_tree_is_refused(tmp_path):
    """Pins the outside-root realpath check behind the islink checks: a
    config root path that is itself a symlink to a directory in the tree."""
    repo, base = _repo(tmp_path, gate_at_base=True)
    _write_gate(repo / "cfg", PR_CMD)
    link = tmp_path / "linked-config"
    link.symlink_to(repo / "cfg", target_is_directory=True)
    with pytest.raises(UntrustedGateConfigError, match="resolves into the reviewed checkout"):
        load_trusted_gate_config(config_root=link, source_root=repo, base_sha=base, allow_untracked=False)


def test_outside_read_does_not_follow_a_symlink_swapped_in_after_the_checks(tmp_path, monkeypatch):
    """O_NOFOLLOW closes the check-then-open race: even if islink/realpath
    are fooled, the open itself refuses the symlink."""
    repo, base = _repo(tmp_path, gate_at_base=False, tracked=False)
    outside = _evil_outside(tmp_path, repo, "file")
    monkeypatch.setattr("code_forge.gate_check.os.path.islink", lambda _p: False)
    monkeypatch.setattr("code_forge.gate_check.os.path.realpath", lambda p, *_a, **_k: str(tmp_path / "elsewhere" / Path(p).name))
    with pytest.raises(UntrustedGateConfigError, match="symlink or not a regular path"):
        load_trusted_gate_config(config_root=outside, source_root=repo, base_sha=base, allow_untracked=False)


def test_in_tree_realpath_check_catches_a_link_the_islink_check_missed(tmp_path, monkeypatch):
    """Pins the in-tree realpath check (the surviving re-review mutant)."""
    repo, base = _repo(tmp_path, gate_at_base=False, tracked=False)
    (repo / GATE).unlink()
    _write_gate(repo / "cfg", PR_CMD)
    (repo / GATE).symlink_to(repo / "cfg" / ".code-forge" / "gate.yaml")
    monkeypatch.setattr("code_forge.gate_check.os.path.islink", lambda _p: False)
    with pytest.raises(UntrustedGateConfigError, match="outside its own path in the checkout"):
        load_trusted_gate_config(config_root=repo, source_root=repo, base_sha=base)


def test_in_tree_untracked_read_does_not_follow_a_late_symlink(tmp_path, monkeypatch):
    repo, base = _repo(tmp_path, gate_at_base=False, tracked=False)
    (repo / GATE).unlink()
    _write_gate(repo / "cfg", PR_CMD)
    (repo / GATE).symlink_to(repo / "cfg" / ".code-forge" / "gate.yaml")
    monkeypatch.setattr("code_forge.gate_check.os.path.islink", lambda _p: False)
    monkeypatch.setattr("code_forge.gate_check.os.path.realpath", lambda p, *_a, **_k: os.fspath(p))
    with pytest.raises(UntrustedGateConfigError, match="symlink or not a regular path"):
        load_trusted_gate_config(config_root=repo, source_root=repo, base_sha=base)


def test_outside_gate_that_is_not_a_regular_file_is_refused(tmp_path):
    repo, base = _repo(tmp_path, gate_at_base=True)
    outside = tmp_path / "odd-config"
    (outside / ".code-forge" / "gate.yaml").mkdir(parents=True)
    with pytest.raises(UntrustedGateConfigError, match="is not a regular file"):
        load_trusted_gate_config(config_root=outside, source_root=repo, base_sha=base)
