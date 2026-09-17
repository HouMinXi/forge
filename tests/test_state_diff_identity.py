# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""A prior review's state must not be inherited by a different diff.

`.code-forge/state.json` lives per workspace, not per diff. Two reviews of
different ranges in the same worktree -- concurrent sessions, or the same
person re-pointing a review -- both land on that one file. Findings,
dispositions and the clean-round counter carried across meant one diff's
verdict was assembled partly from another diff's evidence.
"""
from code_forge.machine import Mode, StateMachine
from code_forge.state import Disposition, State, StateFinding, save_state
from tests.test_machine_ci import StubAutoFixer, StubFalsifier, _make_resolved


def _local_machine(tmp_path, source_hash):
    return StateMachine(
        mode=Mode.LOCAL,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda f: None,
        resolved_review=_make_resolved(),
        source_hash=source_hash,
        baseline_spec_repr="empty",
        cwd=tmp_path,
        registry={},
    )


def _seed_state(tmp_path, source_hash):
    """Write a state.json as if a previous review had finished a round."""
    state_dir = tmp_path / ".code-forge"
    state_dir.mkdir(parents=True, exist_ok=True)
    prior = State(
        mode=Mode.LOCAL,
        source_hash=source_hash,
        round=2,
        consecutive_clean_rounds=2,
    )
    prior.findings = [
        StateFinding(
            id="l1-expert-from-another-diff",
            fingerprint="fp-from-another-diff",
            source="l1",
            disposition=Disposition.CONFIRMED,
            file="other/file.ts",
            line_range=[1, 1],
            description="finding that belongs to a different review",
        )
    ]
    prior.dispositions = {"l1-expert-from-another-diff": Disposition.CONFIRMED}
    save_state(prior, state_dir / "state.json")
    return prior


def test_a_different_diff_does_not_inherit_prior_findings(tmp_path):
    _seed_state(tmp_path, source_hash="diff-one")

    machine = _local_machine(tmp_path, source_hash="diff-two")
    machine._maybe_load_prior_state()

    assert machine._state.findings == []
    assert machine._state.dispositions == {}
    assert machine._state.consecutive_clean_rounds == 0


def test_the_same_diff_still_resumes_where_it_left_off(tmp_path):
    _seed_state(tmp_path, source_hash="diff-one")

    machine = _local_machine(tmp_path, source_hash="diff-one")
    machine._maybe_load_prior_state()

    assert [f.id for f in machine._state.findings] == [
        "l1-expert-from-another-diff"
    ]
    assert machine._state.consecutive_clean_rounds == 2


def test_state_without_source_hash_is_not_loaded(tmp_path):
    """A file with no identity cannot prove it belongs to this diff."""
    state_dir = tmp_path / ".code-forge"
    state_dir.mkdir(parents=True, exist_ok=True)
    prior = State(
        mode=Mode.LOCAL,
        source_hash=None,
        round=1,
        consecutive_clean_rounds=3,
    )
    save_state(prior, state_dir / "state.json")

    machine = _local_machine(tmp_path, source_hash="diff-two")
    machine._maybe_load_prior_state()

    assert machine._state.round == 0
    assert machine._state.consecutive_clean_rounds == 0
    assert machine._state.findings == []
    assert (state_dir / "state.json").exists()


def test_current_review_without_hash_does_not_inherit_or_crash(tmp_path):
    """A run with no identity of its own must not crash or inherit."""
    _seed_state(tmp_path, source_hash="diff-one")

    machine = _local_machine(tmp_path, source_hash=None)
    machine._maybe_load_prior_state()

    assert machine._state.findings == []
    assert machine._state.consecutive_clean_rounds == 0


def test_both_hashes_missing_does_not_inherit(tmp_path):
    """Two unidentified files are not proof they are the same review."""
    state_dir = tmp_path / ".code-forge"
    state_dir.mkdir(parents=True, exist_ok=True)
    prior = State(
        mode=Mode.LOCAL,
        source_hash=None,
        round=1,
        consecutive_clean_rounds=3,
    )
    save_state(prior, state_dir / "state.json")

    machine = _local_machine(tmp_path, source_hash=None)
    machine._maybe_load_prior_state()

    assert machine._state.round == 0
    assert machine._state.consecutive_clean_rounds == 0
