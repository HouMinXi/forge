"""An untracked in-tree gate.yaml used by a LOCAL review is pinned (#143).

It is in no reviewed diff, so an agent working in the same worktree could
edit it unseen. The review pins its digest at start, refuses it if it changes
mid-review, always says it is in use, and flags a change since the previous
review of the checkout. CLI wiring throughout: ``StateMachine(cwd=repo)``.
"""

import json
import subprocess

import pytest

from code_forge import gate_check
from code_forge import machine as machine_mod
from code_forge.gate_check import UntrustedGateConfigError
from code_forge.state import Mode
from tests.test_trusted_test_config import (
    BASE_CMD,
    GATE,
    PR_CMD,
    _git,
    _l2_calls,
    _machine,
    _repo,
    _write_gate,
)


@pytest.fixture
def untracked(tmp_path):
    """LOCAL review whose gate.yaml git does not track (PR_CMD, timeout 999)."""
    return _repo(tmp_path, gate_at_base=False, tracked=False)


def _notes(machine):
    return [a for a in machine._advisories if a.id.startswith("UNTRACKED_GATE")]


def _started(repo, base, mode=Mode.LOCAL):
    machine = _machine(repo, base, mode=mode)
    machine._pin_untracked_gate()
    return machine


def test_untracked_gate_in_use_is_noted_and_still_used(untracked):
    repo, base = untracked
    m = _started(repo, base)
    notes = _notes(m)
    assert [n.id for n in notes] == ["UNTRACKED_GATE_IN_USE"]
    assert "supplies test.* for this review" in notes[0].description
    assert _l2_calls(m) == [(PR_CMD, 999)]


def test_mid_review_edit_is_refused(untracked):
    repo, base = untracked
    m = _started(repo, base)
    _write_gate(repo, BASE_CMD, timeout=1)  # the agent edits it between rounds
    assert _l2_calls(m) == []
    assert any(
        "untracked .code-forge/gate.yaml changed during this review" in e for e in m._state.infra_errors
    ), m._state.infra_errors


def test_mid_review_delete_is_refused(untracked):
    repo, base = untracked
    m = _started(repo, base)
    (repo / GATE).unlink()
    with pytest.raises(UntrustedGateConfigError, match="now unreadable"):
        m._load_trusted_gate_config()


def test_edit_while_loading_is_refused(untracked, monkeypatch):
    repo, base = untracked
    m = _started(repo, base)
    real = gate_check.load_trusted_gate_config

    def editing_loader(**kw):
        config = real(**kw)
        _write_gate(repo, BASE_CMD, timeout=1)
        return config

    monkeypatch.setattr(gate_check, "load_trusted_gate_config", editing_loader)
    with pytest.raises(UntrustedGateConfigError, match="changed during this review"):
        m._load_trusted_gate_config()


def test_change_since_previous_review_is_flagged(untracked):
    repo, base = untracked
    assert [n.id for n in _notes(_started(repo, base))] == ["UNTRACKED_GATE_IN_USE"]
    assert [n.id for n in _notes(_started(repo, base))] == ["UNTRACKED_GATE_IN_USE"]
    _write_gate(repo, BASE_CMD, timeout=1)  # edited between reviews
    notes = _notes(_started(repo, base))
    assert [n.id for n in notes] == ["UNTRACKED_GATE_CHANGED"]
    assert "CHANGED since the previous review" in notes[0].description
    assert [n.id for n in _notes(_started(repo, base))] == ["UNTRACKED_GATE_IN_USE"]


def test_record_lives_in_the_user_config_dir_keyed_by_realpath(untracked, tmp_path, monkeypatch):
    repo, base = untracked
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    m = _started(repo, base)
    store = json.loads((tmp_path / "cfg" / "code-forge" / "untracked-gates.json").read_text())
    assert store == {str((repo / GATE).resolve()): m._untracked_gate_pin[1]}


def test_record_key_is_the_realpath_when_reviewed_through_a_symlink(untracked, tmp_path, monkeypatch):
    repo, base = untracked
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    link = tmp_path / "link"
    link.symlink_to(repo, target_is_directory=True)
    _started(link, base)
    store = json.loads((tmp_path / "cfg" / "code-forge" / "untracked-gates.json").read_text())
    assert list(store) == [str((repo / GATE).resolve())]


def test_corrupt_record_is_tolerated(untracked, tmp_path, monkeypatch):
    repo, base = untracked
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    (tmp_path / "cfg" / "code-forge").mkdir(parents=True)
    (tmp_path / "cfg" / "code-forge" / "untracked-gates.json").write_text("[1, 2")
    assert [n.id for n in _notes(_started(repo, base))] == ["UNTRACKED_GATE_IN_USE"]


def test_non_git_tree_is_pinned_too(tmp_path):
    tree = tmp_path / "plain"
    (tree / "src" / "pkg").mkdir(parents=True)
    (tree / "src" / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    _write_gate(tree, PR_CMD)
    m = _started(tree, None)
    assert [n.id for n in _notes(m)] == ["UNTRACKED_GATE_IN_USE"]
    _write_gate(tree, BASE_CMD)
    with pytest.raises(UntrustedGateConfigError):
        m._load_trusted_gate_config()


@pytest.mark.parametrize("case", ["baseline", "baseline-untracked", "staged", "ci", "outside"])
def test_no_pin_when_the_tree_copy_is_not_used_as_is(tmp_path, case):
    if case.startswith("baseline"):
        repo, base = _repo(tmp_path, gate_at_base=True)
        _write_gate(repo, PR_CMD)
        if case == "baseline-untracked":
            _git(repo, "rm", "-q", "--cached", str(GATE))  # untracked now, baseline copy used
    elif case == "staged":
        repo, base = _repo(tmp_path, gate_at_base=False, tracked=True)
    else:
        repo, base = _repo(tmp_path, gate_at_base=False, tracked=False)
    m = _machine(repo, base, mode=Mode.CI if case == "ci" else Mode.LOCAL)
    if case == "outside":
        outside = tmp_path / "cfg-root"
        _write_gate(outside, BASE_CMD)
        m.test_config_root = outside
    m._pin_untracked_gate()
    assert _notes(m) == [] and m._untracked_gate_pin is None
    _write_gate(repo, BASE_CMD, timeout=3)  # later edits are not this check's business
    m._check_untracked_gate_pin()


def test_gitignored_gate_is_pinned(tmp_path):
    repo, base = _repo(tmp_path, gate_at_base=False, tracked=False)
    (repo / ".gitignore").write_text(".code-forge/\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    assert [n.id for n in _notes(_started(repo, base))] == ["UNTRACKED_GATE_IN_USE"]


def test_first_gate_load_pins_and_a_later_edit_is_refused(untracked):
    """The pin is taken on the run's first gate load (round 0 L2), before any
    autofix round can let the agent touch the file."""
    repo, base = untracked
    m = _machine(repo, base)
    assert _l2_calls(m) == [(PR_CMD, 999)]
    assert [n.id for n in _notes(m)] == ["UNTRACKED_GATE_IN_USE"]
    _write_gate(repo, BASE_CMD, timeout=1)
    assert _l2_calls(m) == []
    assert any("changed during this review" in e for e in m._state.infra_errors)


def test_run_resets_the_pin_without_spawning_anything(untracked, monkeypatch):
    repo, base = untracked
    m = _started(repo, base)
    _write_gate(repo, BASE_CMD, timeout=1)  # edited between reviews

    class Stop(Exception):
        pass

    def stop(**_k):
        raise Stop

    monkeypatch.setattr(machine_mod, "mutation_review_admission_error", stop)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail(f"spawned {a}"))
    with pytest.raises(Stop):
        m.run()
    assert m._untracked_gate_pin is None and m._untracked_gate_pinned is False


def test_missing_git_binary_still_pins_an_existing_gate(untracked, monkeypatch):
    repo, base = untracked

    def no_git(argv, **kw):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", no_git)
    assert _machine(repo, base)._untracked_gate_path() == repo / GATE


# --- #166: a gate that becomes "untracked, used as-is" after the first load ---


def test_gate_created_mid_review_is_refused(tmp_path):
    repo, base = _repo(tmp_path, gate_at_base=False, tracked=False)
    (repo / GATE).unlink()  # no gate when the review starts
    m = _machine(repo, base)
    assert _l2_calls(m) == []
    _write_gate(repo, PR_CMD, timeout=999)  # the agent writes one between rounds
    assert _l2_calls(m) == []
    assert any("appeared during this review" in e for e in m._state.infra_errors), m._state.infra_errors


def test_gate_unstaged_mid_review_is_refused(tmp_path):
    repo, base = _repo(tmp_path, gate_at_base=False, tracked=True)  # staged: refused
    m = _machine(repo, base)
    assert _l2_calls(m) == []
    _git(repo, "rm", "-q", "--cached", str(GATE))  # the agent unstages it
    assert _l2_calls(m) == []
    assert any("appeared during this review" in e for e in m._state.infra_errors), m._state.infra_errors


def test_pinned_gate_that_becomes_tracked_is_refused(untracked):
    repo, base = untracked
    m = _started(repo, base)
    _git(repo, "add", str(GATE))  # no longer used as-is
    with pytest.raises(UntrustedGateConfigError, match="changed during this review"):
        m._check_untracked_gate_pin()


def test_no_gate_throughout_is_not_refused(tmp_path):
    repo, base = _repo(tmp_path, gate_at_base=False, tracked=False)
    (repo / GATE).unlink()
    m = _machine(repo, base)
    _l2_calls(m)
    _l2_calls(m)
    assert not any("during this review" in e for e in m._state.infra_errors)


# --- #167: the bytes parsed are the bytes pinned; globs are not re-read ---


def test_swap_during_load_restored_afterwards_is_refused(untracked, monkeypatch):
    repo, base = untracked
    m = _started(repo, base)
    original = (repo / GATE).read_bytes()
    real = gate_check.load_trusted_gate_config

    def swapping(**kw):
        _write_gate(repo, BASE_CMD, timeout=1)
        try:
            return real(**kw)
        finally:
            (repo / GATE).write_bytes(original)

    monkeypatch.setattr(gate_check, "load_trusted_gate_config", swapping)
    with pytest.raises(UntrustedGateConfigError, match="when parsed"):
        m._load_trusted_gate_config()


def test_mutation_globs_come_from_the_loaded_block_not_a_reread(untracked, monkeypatch):
    import yaml

    repo, base = untracked
    m = _started(repo, base)
    config = m._load_trusted_gate_config()
    (repo / GATE).write_text(
        yaml.safe_dump(
            {
                "test": {
                    "command": PR_CMD,
                    "timeout_seconds": 999,
                    "mutation_skip_globs": ["src/**"],
                }
            }
        )
    )  # edited after the post-load check
    reads = []
    monkeypatch.setattr(gate_check, "load_trusted_gate_config", lambda **kw: reads.append(kw) or {})
    assert m._trusted_mutation_globs(config["test"]) == (None, None)
    assert reads == []


def test_pinned_globs_are_taken_from_the_loaded_block(untracked):
    import yaml

    repo, base = untracked
    (repo / GATE).write_text(
        yaml.safe_dump(
            {
                "test": {
                    "command": PR_CMD,
                    "timeout_seconds": 999,
                    "mutation_skip_globs": ["a/**"],
                    "mutation_include_globs": ["b/**"],
                }
            }
        )
    )
    m = _started(repo, base)
    config = m._load_trusted_gate_config()
    assert m._trusted_mutation_globs(config["test"]) == (["a/**"], ["b/**"])
