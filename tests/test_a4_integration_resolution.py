"""Cross-repository constructor contracts on distinct real git inputs."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from code_forge import cross_repo, factories, machine
from code_forge.state import Verdict
from tests.test_cross_repo import _make_repo


@pytest.mark.parametrize("engine,primary_changed", [("real", True), ("stub", True), ("real", False)])
def test_cross_repo_constructor_contracts(tmp_path, monkeypatch, engine, primary_changed):
    primary = _make_repo(tmp_path, monkeypatch, "primary", filename="primary.py")
    sibling = _make_repo(
        tmp_path, monkeypatch, "sibling", filename="sibling.py",
        content_v1="sibling = 10\n", content_v2="sibling = 20\n",
    )
    primary_ref = "main..feature" if primary_changed else "main..main"
    expected = {
        "primary.py": cross_repo.get_sibling_diff(primary, primary_ref),
        "sibling.py": cross_repo.get_sibling_diff(sibling, "main..feature"),
    }
    captured = []
    falsifier_diffs = []
    l1_diffs = []
    backend = object()

    def make_falsifier(engine_choice, *, backend, diff_text=None, **kwargs):
        instance = object()
        falsifier_diffs.append((instance, engine_choice, backend, diff_text))
        return instance

    def make_l1(engine_choice, resolved, **kwargs):
        l1_diffs.append(resolved.git_diff)
        return lambda: ([], None, [])

    def make_machine(**kwargs):
        captured.append(kwargs)
        instance = MagicMock()
        instance.run.return_value = Verdict.PASS
        return instance

    monkeypatch.setattr(factories, "build_falsifier", make_falsifier)
    monkeypatch.setattr(factories, "build_l1_provider", make_l1)
    monkeypatch.setattr(machine, "StateMachine", make_machine)
    verdict = cross_repo.run_cross_repo(
        primary_path=primary, primary_label="primary", primary_ref=primary_ref,
        siblings=[{"label": "sibling", "repo": str(sibling), "ref": "main..feature"}],
        gate_config={"test": {"command": ["echo", "ok"]}}, mode="local", engine_choice=engine, backend=backend,
        max_rounds=3, max_fix_attempts=1, clean_round_threshold=1,
        output_fn=lambda _: None,
    )
    assert verdict is Verdict.PASS
    assert len(captured) == len(falsifier_diffs) == 2
    for kwargs in captured:
        own_diff = kwargs["resolved_review"].git_diff
        is_primary = own_diff == expected["primary.py"]
        expected_diff = expected["primary.py" if is_primary else "sibling.py"]
        actual_falsifier = kwargs["falsifier"]
        if is_primary:
            assert isinstance(actual_falsifier, cross_repo.RepositoryFalsifier)
            actual_falsifier = actual_falsifier.primary
        call = next(call for call in falsifier_diffs if call[0] is actual_falsifier)
        assert call[1:3] == (engine, backend)
        assert call[3] == expected_diff, "falsifier must receive its own repository diff"
        assert kwargs.get("coverage_l1_active") is bool(
            is_primary and any(expected.values()) and engine != "stub"
        ), "coverage must be explicit for the primary joint review"
    assert len(l1_diffs) == 1
    from code_forge.receipt_scope import repository_scope
    assert repository_scope({'sibling': expected['sibling.py']})[0] in l1_diffs[0]
    if primary_changed:
        assert repository_scope({'primary': expected['primary.py']})[0] in l1_diffs[0]
        assert expected["primary.py"] != expected["sibling.py"]


def test_scheduler_helper_closes_real_coroutine():
    import gc
    import inspect

    from tests.test_factories import _close_unrun_coro

    async def unused():
        return None

    future = object()
    coro = unused()
    assert _close_unrun_coro(future)(coro, object()) is future
    state = inspect.getcoroutinestate(coro)
    del coro
    gc.collect()
    assert state == inspect.CORO_CLOSED
