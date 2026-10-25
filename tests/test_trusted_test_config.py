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

UNTRACKED_REASON = (
    "CI: mutation not measured -- untracked gate.yaml in checkout is not trusted; "
    "commit it or use --test-config-root"
)
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
        seen.append((test_cmd, kwargs.get("baseline_timeout")))
        return FixvalResult(status=FixvalStatus.PASS, findings=[], advisories=[])

    with patch("code_forge.fixval.run_fixval", fake_run_fixval):
        machine._finalize_local_terminal()
    # The trusted baseline gate's command AND its timeout (111), never the
    # PR's (999) and never the 120s default that cut off #142's FIXVAL.
    assert seen == [(BASE_CMD, 111)], (machine._state.infra_errors, [f.description for f in machine._state.findings])


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
    assert machine._mutation_bound_failure is True  # #160: unmeasured => CI FAIL
    assert UNTRACKED_REASON in machine._state.infra_errors, machine._state.infra_errors
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
    assert any("hard links; an outside test config root must hold its own single-link" in e
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
    assert any("hard links; an outside test config root must hold its own single-link" in e
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


# --- #144: git listings fail closed; #145: untracked/ignored hardlinks; H5 ---

from code_forge import gate_check  # noqa: E402


def _fail_git(monkeypatch, *prefix):
    real = gate_check._git_out

    def fake(source_root, *args):
        if args[: len(prefix)] == prefix:
            return subprocess.CompletedProcess(["git", *args], 128, "", "fatal: detected dubious ownership\n")
        return real(source_root, *args)

    monkeypatch.setattr(gate_check, "_git_out", fake)


def _tracked_evil_hardlink(tmp_path):
    repo, base = _repo(tmp_path, gate_at_base=False, tracked=False)
    evil = repo / "evil.yaml"
    evil.write_text(yaml.safe_dump({"test": {"command": PR_CMD}}), encoding="utf-8")
    _git(repo, "add", "evil.yaml")
    outside = tmp_path / "evil-config"
    (outside / ".code-forge").mkdir(parents=True)
    os.link(evil, outside / ".code-forge" / "gate.yaml")
    return repo, base, outside


def test_index_listing_failure_refuses_a_staged_first_ever_gate_in_local(tmp_path, monkeypatch):
    repo, base = _repo(tmp_path, gate_at_base=False, tracked=True)
    _fail_git(monkeypatch, "ls-files", "-s")
    with pytest.raises(UntrustedGateConfigError, match=r"git ls-files -s \.code-forge \.code-forge/gate\.yaml failed \(exit 128\): fatal: detected dubious ownership"):
        load_trusted_gate_config(config_root=repo, source_root=repo, base_sha=base)


def test_baseline_tree_listing_failure_refuses(tmp_path, monkeypatch):
    repo, base = _repo(tmp_path, gate_at_base=True)
    _fail_git(monkeypatch, "ls-tree")
    with pytest.raises(UntrustedGateConfigError, match=r"git ls-tree [0-9a-f]{40} \.code-forge \.code-forge/gate\.yaml failed \(exit 128\)"):
        load_trusted_gate_config(config_root=repo, source_root=repo, base_sha=base)


@pytest.mark.parametrize("ignored", [False, True])
def test_ci_outside_root_hardlinked_to_an_untracked_file_is_refused(tmp_path, ignored):
    """#145: untracked and gitignored checkout files are in the refuse set too."""
    repo, base = _repo(tmp_path, gate_at_base=False, tracked=False)
    evil = repo / "evil.yaml"
    evil.write_text(yaml.safe_dump({"test": {"command": PR_CMD}}), encoding="utf-8")
    if ignored:
        (repo / ".gitignore").write_text("evil.yaml\n", encoding="utf-8")
    outside = tmp_path / "evil-config"
    (outside / ".code-forge").mkdir(parents=True)
    os.link(evil, outside / ".code-forge" / "gate.yaml")
    machine = _machine(repo, base, mode=Mode.CI)
    machine.test_config_root = outside
    assert _l2_calls(machine) == []
    assert any("hard links; an outside test config root must hold its own single-link" in e
               for e in machine._state.infra_errors), machine._state.infra_errors


@pytest.mark.parametrize("where", ["root-untracked", "root-tracked", "code-forge-dir"])
def test_checkout_symlink_to_a_legit_outside_gate_does_not_taint_it(tmp_path, where):
    """H5/H6: inodes are taken without following symlinks, so a checkout
    symlink pointing at the operator's outside gate does not refuse it."""
    repo, base = _repo(tmp_path, gate_at_base=True)
    outside = tmp_path / "ci-config"
    _write_gate(outside, BASE_CMD, timeout=5)
    target = outside / ".code-forge" / "gate.yaml"
    link = repo / (".code-forge/outside-link.yaml" if where == "code-forge-dir" else "outside-link.yaml")
    link.symlink_to(target)
    if where == "root-tracked":
        _git(repo, "add", "outside-link.yaml")
    machine = _machine(repo, base, mode=Mode.CI)
    machine.test_config_root = outside
    assert _l2_calls(machine) == [(BASE_CMD, 5)], machine._state.infra_errors


# --- #147/#148/#149: nlink refusal, no checkout scan, full git args ---

def _ci_with_outside_link(repo, base, source):
    outside = repo.parent / "evil-config"
    (outside / ".code-forge").mkdir(parents=True)
    os.link(source, outside / ".code-forge" / "gate.yaml")
    machine = _machine(repo, base, mode=Mode.CI)
    machine.test_config_root = outside
    return machine


def _evil_yaml(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"test": {"command": PR_CMD}}), encoding="utf-8")
    return path


def _assert_hardlink_refused(machine):
    assert _l2_calls(machine) == []
    assert any("hard links; an outside test config root must hold its own single-link" in e
               for e in machine._state.infra_errors), machine._state.infra_errors


def test_ci_outside_root_hardlinked_into_a_nested_untracked_repo_is_refused(tmp_path):
    repo, base = _repo(tmp_path, gate_at_base=False, tracked=False)
    sub = repo / "sub"
    sub.mkdir()
    _git(sub, "init", "-q")
    _assert_hardlink_refused(_ci_with_outside_link(repo, base, _evil_yaml(sub / "e.yaml")))


def test_ci_outside_root_hardlinked_into_a_submodule_work_tree_is_refused(tmp_path):
    repo, base = _repo(tmp_path, gate_at_base=False, tracked=False)
    vend = repo / "vend"
    vend.mkdir()
    _git(vend, "init", "-q")
    _git(vend, "config", "user.email", "t@example.invalid")
    _git(vend, "config", "user.name", "t")
    _evil_yaml(vend / "e.yaml")
    _git(vend, "add", "e.yaml")
    _git(vend, "commit", "-qm", "v")
    head = _git(vend, "rev-parse", "HEAD")
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{head},vend")
    assert "160000" in _git(repo, "ls-files", "-s", "vend")
    _assert_hardlink_refused(_ci_with_outside_link(repo, base, vend / "e.yaml"))


def test_ci_outside_gate_with_a_second_link_outside_the_checkout_is_refused(tmp_path):
    repo, base = _repo(tmp_path, gate_at_base=False, tracked=False)
    _assert_hardlink_refused(_ci_with_outside_link(repo, base, _evil_yaml(tmp_path / "elsewhere" / "g.yaml")))


def test_single_link_outside_gate_loads_without_any_checkout_listing(tmp_path, monkeypatch):
    """#149: the outside root needs no git listing of the checkout at all."""
    repo, base = _repo(tmp_path, gate_at_base=False, tracked=False)
    outside = tmp_path / "ci-config"
    _write_gate(outside, BASE_CMD, timeout=7)
    monkeypatch.setattr(gate_check, "_git_out", lambda *a, **k: pytest.fail(f"git called: {a}"))
    assert load_trusted_gate_config(
        config_root=outside, source_root=repo, base_sha=base, allow_untracked=False,
    )["test"]["command"] == BASE_CMD


def test_non_utf8_untracked_name_does_not_refuse_a_legit_outside_root(tmp_path):
    """#148: a non-UTF-8 untracked file name in the checkout is harmless."""
    repo, base = _repo(tmp_path, gate_at_base=False, tracked=False)
    with open(os.path.join(os.fsencode(repo), b"caf\xe9.txt"), "wb") as handle:
        handle.write(b"x")
    outside = tmp_path / "ci-config"
    _write_gate(outside, BASE_CMD, timeout=5)
    machine = _machine(repo, base, mode=Mode.CI)
    machine.test_config_root = outside
    assert _l2_calls(machine) == [(BASE_CMD, 5)], machine._state.infra_errors


def test_git_output_with_a_non_utf8_path_does_not_raise(tmp_path):
    """#148: _git_out decodes with surrogateescape."""
    repo, _ = _repo(tmp_path, gate_at_base=False, tracked=False)
    with open(os.path.join(os.fsencode(repo), b"caf\xe9.txt"), "wb") as handle:
        handle.write(b"x")
    out = gate_check._git_out(repo, "ls-files", "-z", "-o")
    assert out.returncode == 0 and os.fsencode("caf\udce9.txt") in os.fsencode(out.stdout)


# --- #153: bind-mount aliases of the checkout ---------------------------------

_ALIAS_SCRIPT = r"""
import os, subprocess, sys
from pathlib import Path
from code_forge.gate_check import UntrustedGateConfigError, load_trusted_gate_config
case, repo, base, scratch = sys.argv[1], Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4])
source = repo
if case.endswith("-via-symlink"):  # #155: a symlinked source_root (programmatic callers)
    source = scratch / "checkout-link"
    source.symlink_to(repo, target_is_directory=True)
    case = case[: -len("-via-symlink")]
if case == "root-alias":
    alias = scratch / "alias"
    alias.mkdir()
    subprocess.run(["mount", "--bind", str(repo), str(alias)], check=True)
    config_root = alias / "cfg"
elif case == "forge-alias":
    config_root = scratch / "outside"
    (config_root / ".code-forge").mkdir(parents=True)
    subprocess.run(["mount", "--bind", str(repo / ".code-forge"), str(config_root / ".code-forge")], check=True)
else:  # control: a legitimate outside root, same namespace
    config_root = scratch / "legit"
    (config_root / ".code-forge").mkdir(parents=True)
    (config_root / ".code-forge" / "gate.yaml").write_text(
        "test:\n  command: [legit]\n", encoding="utf-8")
try:
    cfg = load_trusted_gate_config(config_root=config_root, source_root=source, base_sha=base,
                                   allow_untracked=False)
except UntrustedGateConfigError as exc:
    print("REFUSED", exc)
else:
    print("LOADED", cfg["test"]["command"])
"""


def _unshare_available():
    import shutil

    if shutil.which("unshare") is None:
        return False
    probe = subprocess.run(["unshare", "-rm", "true"], capture_output=True, check=False)
    return probe.returncode == 0


@pytest.mark.skipif(not _unshare_available(), reason="unprivileged user/mount namespaces unavailable")
@pytest.mark.parametrize("case", [
    "root-alias", "forge-alias", "control",
    "root-alias-via-symlink", "forge-alias-via-symlink", "control-via-symlink",
])
def test_bind_mount_alias_of_the_checkout_is_refused(tmp_path, case):
    repo, base = _repo(tmp_path, gate_at_base=False, tracked=False)  # PR-controlled tree gate
    (repo / "cfg" / ".code-forge").mkdir(parents=True)
    (repo / "cfg" / ".code-forge" / "gate.yaml").write_text(
        yaml.safe_dump({"test": {"command": PR_CMD}}), encoding="utf-8",
    )
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    out = subprocess.run(
        ["unshare", "-rm", sys.executable, "-c", _ALIAS_SCRIPT, case, str(repo), base, str(scratch)],
        capture_output=True, text=True, check=False,
        env={**os.environ, "PYTHONPATH": str(Path(gate_check.__file__).parents[1])},
    )
    assert out.returncode == 0, out.stderr
    if case.startswith("control"):
        assert out.stdout.startswith("LOADED ['legit']"), out.stdout
    else:
        assert out.stdout.startswith("REFUSED"), out.stdout
        assert "bind mount alias" in out.stdout


# --- #157: a PR-committed .code-forge symlink must not make the outside root an alias;
#     a refused trusted config in CI must FAIL, not PASS with mutation skipped ----

OUTSIDE_CMD = [sys.executable, "-c", "print('outside')"]


def _pr_symlinks_code_forge_to(repo, target):
    import shutil

    shutil.rmtree(repo / ".code-forge")
    (repo / ".code-forge").symlink_to(target, target_is_directory=True)


def _outside_root(tmp_path):
    outside = tmp_path / "forge-config"
    _write_gate(outside, OUTSIDE_CMD, timeout=7)
    return outside


def test_checkout_code_forge_symlinked_to_the_outside_root_is_not_an_alias(tmp_path):
    repo, base = _repo(tmp_path, gate_at_base=True)
    outside = _outside_root(tmp_path)
    _pr_symlinks_code_forge_to(repo, outside / ".code-forge")
    cfg = load_trusted_gate_config(config_root=outside, source_root=repo, base_sha=base, allow_untracked=False)
    assert cfg["test"]["command"] == OUTSIDE_CMD


def _ci_run(machine, monkeypatch):
    monkeypatch.setattr(mutation.shutil, "which", lambda name: "/tools/mutmut")
    monkeypatch.setattr(StateMachine, "_execute_round", lambda self, round_index: None)
    launched = []

    def launch(*args, **kwargs):
        launched.append((args, kwargs))
        return True

    monkeypatch.setattr("code_forge.machine.launch_detached_mutation", launch, raising=False)
    monkeypatch.setattr(mutation, "launch_detached_mutation", launch, raising=False)
    try:
        verdict = machine._run_ci()
    except Exception as exc:  # noqa: BLE001
        verdict = exc
    return verdict, launched


def test_ci_symlinked_checkout_code_forge_still_launches_with_the_outside_root(tmp_path, monkeypatch):
    repo, base = _repo(tmp_path, gate_at_base=True)
    outside = _outside_root(tmp_path)
    _pr_symlinks_code_forge_to(repo, outside / ".code-forge")
    machine = _machine(repo, base, mode=Mode.CI)
    machine.cwd, machine.source_root = outside, repo
    _verdict, launched = _ci_run(machine, monkeypatch)
    assert "print('outside')" in repr(launched), (launched, machine._state.infra_errors)
    assert not [e for e in machine._state.infra_errors if "refused" in e or "alias" in e]


@pytest.mark.parametrize("refusal", ["symlinked-root-code-forge", "hardlinked-gate"])
def test_ci_refused_outside_config_root_fails_with_the_reason(tmp_path, monkeypatch, refusal):
    repo, base = _repo(tmp_path, gate_at_base=True)
    outside = tmp_path / "forge-config"
    outside.mkdir()
    if refusal == "symlinked-root-code-forge":
        elsewhere = _outside_root(tmp_path / "elsewhere")
        (outside / ".code-forge").symlink_to(elsewhere / ".code-forge", target_is_directory=True)
    else:
        (outside / ".code-forge").mkdir()
        os.link(repo / GATE, outside / GATE)
    machine = _machine(repo, base, mode=Mode.CI)
    machine.cwd, machine.source_root = outside, repo
    verdict, launched = _ci_run(machine, monkeypatch)
    assert launched == []
    assert verdict == Verdict.FAIL, (verdict, machine._state.infra_errors)
    assert any("CI: mutation not measured -- trusted test config refused:" in e
               for e in machine._state.infra_errors), machine._state.infra_errors


@pytest.mark.parametrize("mode,expected", [(Mode.CI, True), (Mode.LOCAL, False)])
def test_l2_refused_trusted_config_is_a_bound_failure_in_ci(tmp_path, mode, expected):
    repo, base = _repo(tmp_path, gate_at_base=True)
    outside = tmp_path / "forge-config"
    (outside / ".code-forge").mkdir(parents=True)
    os.link(repo / GATE, outside / GATE)  # refused: st_nlink > 1
    machine = _machine(repo, base, mode=mode)
    machine.cwd, machine.source_root = outside, repo
    assert _l2_calls(machine) == []
    assert machine._mutation_bound_failure is expected
    assert any("L2: mutation not measured -- trusted test config refused:" in e
               for e in machine._state.infra_errors), machine._state.infra_errors


def test_outside_refusal_is_the_subclass_and_in_tree_refusal_is_not(tmp_path):
    from code_forge.gate_check import OutsideConfigRootRefused, UntrustedGateConfigError

    repo, base = _repo(tmp_path, gate_at_base=False, tracked=False)
    with pytest.raises(UntrustedGateConfigError) as in_tree:
        load_trusted_gate_config(config_root=repo, source_root=repo, base_sha=base, allow_untracked=False)
    assert not isinstance(in_tree.value, OutsideConfigRootRefused)
    outside = tmp_path / "forge-config"
    (outside / ".code-forge").mkdir(parents=True)
    os.link(repo / GATE, outside / GATE)
    with pytest.raises(OutsideConfigRootRefused):
        load_trusted_gate_config(config_root=outside, source_root=repo, base_sha=base, allow_untracked=False)


def test_ci_untracked_in_tree_gate_is_a_bound_failure(tmp_path):
    """#160: an untracked in-tree gate in CI FAILs (mutation unmeasured)."""
    repo, base = _repo(tmp_path, gate_at_base=False, tracked=False)
    machine = _machine(repo, base, mode=Mode.CI)
    assert _l2_calls(machine) == []
    assert machine._mutation_bound_failure is True
    assert UNTRACKED_REASON in machine._state.infra_errors, machine._state.infra_errors


@pytest.mark.parametrize("git", [True, False], ids=["git", "non-git"])
def test_ci_detached_untracked_gate_fails_with_the_reason(tmp_path, monkeypatch, git):
    if git:
        repo, base = _repo(tmp_path, gate_at_base=False, tracked=False)
    else:
        repo, base = tmp_path / "plain", None
        (repo / "src" / "pkg").mkdir(parents=True)
        (repo / "src" / "pkg" / "mod.py").write_text("x = 2\n", encoding="utf-8")
        _write_gate(repo, PR_CMD)
    machine = _machine(repo, base, mode=Mode.CI)
    verdict, launched = _ci_run(machine, monkeypatch)
    assert launched == []
    assert verdict == Verdict.FAIL, (verdict, machine._state.infra_errors)
    assert UNTRACKED_REASON in machine._state.infra_errors


NO_GATE_REASON = (
    "CI: mutation not measured -- no gate.yaml found; add .code-forge/gate.yaml "
    "or use --test-config-root"
)


def _no_gate_repo(tmp_path, git):
    if git:
        repo, base = _repo(tmp_path, gate_at_base=False, tracked=False)
        os.unlink(repo / GATE)
    else:
        repo, base = tmp_path / "plain", None
        (repo / "src" / "pkg").mkdir(parents=True)
        (repo / "src" / "pkg" / "mod.py").write_text("x = 2\n", encoding="utf-8")
    return repo, base


@pytest.mark.parametrize("git", [True, False], ids=["git", "non-git"])
def test_ci_no_gate_anywhere_is_gate_not_found(tmp_path, git):
    from code_forge.gate_check import GateNotFound, UntrackedGateRefused

    repo, base = _no_gate_repo(tmp_path, git)
    with pytest.raises(GateNotFound):
        load_trusted_gate_config(config_root=repo, source_root=repo, base_sha=base, allow_untracked=False)
    _write_gate(repo, PR_CMD)  # present but untracked: the other refusal
    with pytest.raises(UntrackedGateRefused):
        load_trusted_gate_config(config_root=repo, source_root=repo, base_sha=base, allow_untracked=False)


@pytest.mark.parametrize("git", [True, False], ids=["git", "non-git"])
def test_ci_no_gate_fails_with_the_reason(tmp_path, monkeypatch, git):
    repo, base = _no_gate_repo(tmp_path, git)
    machine = _machine(repo, base, mode=Mode.CI)
    assert _l2_calls(machine) == []
    assert machine._mutation_bound_failure is True
    assert NO_GATE_REASON in machine._state.infra_errors, machine._state.infra_errors
    machine = _machine(repo, base, mode=Mode.CI)
    verdict, launched = _ci_run(machine, monkeypatch)
    assert launched == [] and verdict == Verdict.FAIL, (verdict, machine._state.infra_errors)
    assert NO_GATE_REASON in machine._state.infra_errors


def test_ci_outside_root_without_a_gate_fails_with_the_reason(tmp_path, monkeypatch):
    from code_forge.gate_check import GateNotFound

    repo, base = _repo(tmp_path, gate_at_base=True)
    outside = tmp_path / "forge-config"
    (outside / ".code-forge").mkdir(parents=True)
    with pytest.raises(GateNotFound):
        load_trusted_gate_config(config_root=outside, source_root=repo, base_sha=base, allow_untracked=False)
    machine = _machine(repo, base, mode=Mode.CI)
    machine.cwd, machine.source_root = outside, repo
    verdict, launched = _ci_run(machine, monkeypatch)
    assert launched == [] and verdict == Verdict.FAIL
    assert NO_GATE_REASON in machine._state.infra_errors


@pytest.mark.parametrize("git", [True, False], ids=["git", "non-git"])
def test_local_no_gate_is_unchanged(tmp_path, git):
    from code_forge.gate_check import GateNotFound

    repo, base = _no_gate_repo(tmp_path, git)
    with pytest.raises(FileNotFoundError) as missing:
        load_trusted_gate_config(config_root=repo, source_root=repo, base_sha=base, allow_untracked=True)
    assert not isinstance(missing.value, GateNotFound)
    machine = _machine(repo, base)
    assert _l2_calls(machine) == []
    assert machine._mutation_bound_failure is False
    assert NO_GATE_REASON not in machine._state.infra_errors
    assert any("L2: gate.yaml missing or test.command not configured" in e for e in machine._state.infra_errors)


def test_local_outside_root_without_a_gate_keeps_the_old_note(tmp_path):
    repo, base = _repo(tmp_path, gate_at_base=True)
    outside = tmp_path / "forge-config"
    (outside / ".code-forge").mkdir(parents=True)
    machine = _machine(repo, base)
    machine.cwd, machine.source_root = outside, repo
    assert _l2_calls(machine) == []
    assert machine._mutation_bound_failure is False
    assert any("L2: gate.yaml missing or test.command not configured" in e for e in machine._state.infra_errors)


def test_ci_docs_only_diff_never_loads_the_gate(tmp_path, monkeypatch):
    """Docs-only policy (CI_DOCS_ONLY_MUTATION_SKIP_PASSES) wins: no mutation
    adapter covers a docs-only diff, so neither the L2 phase nor the detached
    launch loads the gate, and the no-gate FAIL cannot fire. (The docs-only
    PASS itself is covered by the existing #127/#132 tests.)"""
    from code_forge import machine as machine_mod

    assert machine_mod.CI_DOCS_ONLY_MUTATION_SKIP_PASSES is True
    repo, base = _no_gate_repo(tmp_path, True)
    (repo / "docs").mkdir()
    (repo / "docs" / "guide.md").write_text("# hi\n", encoding="utf-8")
    monkeypatch.setattr(
        StateMachine, "_load_trusted_gate_config",
        lambda self: pytest.fail("gate loaded for a docs-only diff"),
    )

    def docs_machine():
        machine = _machine(repo, base, mode=Mode.CI, files=("docs/guide.md",))
        machine.resolved_review = ResolvedReview(
            source_files=[repo / "docs" / "guide.md"], baseline_content=None,
            git_diff="--- a/docs/guide.md\n+++ b/docs/guide.md\n@@ -0,0 +1 @@\n+# hi\n",
            mode_hint="git", base_sha=base,
        )
        return machine

    machine = docs_machine()
    _l2_calls(machine)
    assert NO_GATE_REASON not in machine._state.infra_errors
    assert machine._mutation_bound_failure is False
    machine = docs_machine()
    verdict, launched = _ci_run(machine, monkeypatch)
    assert launched == [] and verdict == Verdict.PASS, (verdict, machine._state.infra_errors)
    assert NO_GATE_REASON not in machine._state.infra_errors


NOT_ON_BASELINE_REASON = (
    "CI: mutation not measured -- gate.yaml is not on the review baseline yet (added by "
    "this change); it takes effect once it is on the baseline, or use --test-config-root"
)


def test_ci_first_ever_tracked_gate_fails_with_its_own_reason(tmp_path, monkeypatch):
    """#178: committing a first-ever gate.yaml must not turn CI's no-gate FAIL into PASS."""
    from code_forge.gate_check import GateNotFound, GateNotOnBaseline

    repo, base = _repo(tmp_path, gate_at_base=False, tracked=True)
    with pytest.raises(GateNotOnBaseline) as caught:
        load_trusted_gate_config(config_root=repo, source_root=repo, base_sha=base, allow_untracked=False)
    assert isinstance(caught.value, GateNotFound)
    machine = _machine(repo, base, mode=Mode.CI)
    assert _l2_calls(machine) == []
    assert machine._mutation_bound_failure is True
    assert NOT_ON_BASELINE_REASON in machine._state.infra_errors, machine._state.infra_errors
    machine = _machine(repo, base, mode=Mode.CI)
    verdict, launched = _ci_run(machine, monkeypatch)
    assert launched == [] and verdict == Verdict.FAIL, (verdict, machine._state.infra_errors)
    assert NOT_ON_BASELINE_REASON in machine._state.infra_errors


def test_local_first_ever_tracked_gate_keeps_the_not_configured_note(tmp_path):
    repo, base = _repo(tmp_path, gate_at_base=False, tracked=True)
    machine = _machine(repo, base)
    assert _l2_calls(machine) == []
    assert machine._mutation_bound_failure is False
    assert NOT_ON_BASELINE_REASON not in machine._state.infra_errors


@pytest.mark.parametrize("content", [
    "test: [unclosed\n",  # not YAML
    "- just\n- a list\n",  # not a mapping
    "test: {command: 5}\n",  # invalid test section
], ids=["yaml", "not-mapping", "bad-command"])
def test_ci_invalid_outside_gate_fails_with_the_reason(tmp_path, monkeypatch, content):
    """User policy: a malformed gate.yaml in an outside config root is a CI FAIL."""
    from code_forge.gate_check import OutsideGateInvalid

    repo, base = _repo(tmp_path, gate_at_base=True)
    outside = tmp_path / "forge-config"
    (outside / ".code-forge").mkdir(parents=True)
    (outside / GATE).write_text(content, encoding="utf-8")
    with pytest.raises(OutsideGateInvalid):
        load_trusted_gate_config(config_root=outside, source_root=repo, base_sha=base, allow_untracked=False)
    for run in ("inline", "detached"):
        machine = _machine(repo, base, mode=Mode.CI)
        machine.cwd, machine.source_root = outside, repo
        if run == "inline":
            assert _l2_calls(machine) == []
            assert machine._mutation_bound_failure is True
        else:
            verdict, launched = _ci_run(machine, monkeypatch)
            assert launched == [] and verdict == Verdict.FAIL, (verdict, machine._state.infra_errors)
        assert any(
            e.startswith("CI: mutation not measured -- invalid .code-forge/gate.yaml in the test config root")
            for e in machine._state.infra_errors
        ), machine._state.infra_errors


def test_local_invalid_outside_gate_keeps_the_old_note(tmp_path):
    repo, base = _repo(tmp_path, gate_at_base=True)
    outside = tmp_path / "forge-config"
    (outside / ".code-forge").mkdir(parents=True)
    (outside / GATE).write_text("test: [unclosed\n", encoding="utf-8")
    machine = _machine(repo, base)
    machine.cwd, machine.source_root = outside, repo
    assert _l2_calls(machine) == []
    assert machine._mutation_bound_failure is False
    assert any("L2: gate.yaml missing or test.command not configured" in e for e in machine._state.infra_errors)


def test_ci_outside_gate_without_a_test_section_is_not_malformed(tmp_path):
    """A well-formed review-keys-only outside gate is 'mutation not configured',
    not a malformed file: it keeps the existing not-configured note."""
    from code_forge.gate_check import GateWithoutTestSection

    repo, base = _repo(tmp_path, gate_at_base=True)
    outside = tmp_path / "forge-config"
    (outside / ".code-forge").mkdir(parents=True)
    (outside / GATE).write_text("rulepacks: []\n", encoding="utf-8")
    with pytest.raises(GateWithoutTestSection):
        load_trusted_gate_config(config_root=outside, source_root=repo, base_sha=base, allow_untracked=False)
