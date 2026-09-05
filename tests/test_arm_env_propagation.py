"""Does the depth arm actually reach the process that runs the review?

Phase 58-3 sweeps FORGE_CLEAN_ROUND_THRESHOLD across three arms. The value is
set in the parent, but reviews run in ProcessPoolExecutor children, and on
Python 3.14 those start through forkserver rather than fork. Forkserver
children are forked from a server process spawned early, so a parent
environment mutation made AFTER that server starts is not guaranteed to be
visible.

If the value silently fails to reach the child, all three arms run at the
same depth, the sweep produces three near-identical result sets, and the
conclusion drawn from it is meaningless. That failure is invisible in the
output: there is no error, just three arms that agree.

This measures the propagation rather than assuming it.
"""
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest


class _StopPool(Exception):
    """Cut run_pool's parallel path short once submit has been observed."""


def _FakeEntry(name):
    """A minimal real CorpusEntry; run_pool only reads .name here."""
    from code_forge.eval.runner import CorpusEntry

    return CorpusEntry(
        name=name,
        diff_file="unused.diff",
        expected_verdict="HOLD",
        axis_tags=[],
    )


def _stub_replay(entry, **kwargs):
    """Stand in for replay_entry, reporting the env it was called under.

    The arm depth is what the review loop reads out of the environment, so
    recording it at the point replay_entry would run is what proves the
    override arrived. It rides back in skipped_reason because that field is
    a free-text string that survives the pool's result plumbing unchanged.

    Must be module-level: ProcessPoolExecutor pickles it by qualified name.
    """
    from code_forge.eval.runner import EvalResult

    return EvalResult(
        entry=entry,
        actual_verdict="HOLD",
        runs=1,
        caught_count=0,
        skipped_reason=os.environ.get("FORGE_CLEAN_ROUND_THRESHOLD") or "",
    )


def _apply_and_read(env_overrides):
    """Apply overrides the way pool._worker does, then read one back.

    Mirrors the two lines at the top of pool._worker rather than calling it,
    because _worker goes on to run a whole review. The mirror is kept honest
    by test_worker_applies_overrides_the_same_way below.
    """
    if env_overrides:
        os.environ.update({k: str(v) for k, v in env_overrides.items()})
    return os.environ.get("FORGE_CLEAN_ROUND_THRESHOLD")


def _apply_and_resolve(env_overrides):
    """Apply overrides, then resolve the threshold the way cli.py does.

    cli.py:3596-3605 reads the env var, coerces it to int, and passes it to
    tier_threshold as env_override, which returns max(1, override) ahead of
    any tier logic. 500 lines is a large diff whose tier default is above 1,
    so an override that failed to arrive resolves to the tier value and the
    assertion catches it.
    """
    from code_forge.diff import tier_threshold

    if env_overrides:
        os.environ.update({k: str(v) for k, v in env_overrides.items()})
    env_threshold = None
    try:
        raw = os.environ.get("FORGE_CLEAN_ROUND_THRESHOLD")
        if raw is not None:
            env_threshold = int(raw)
    except (ValueError, TypeError):
        pass
    return tier_threshold(500, False, env_threshold)


class TestEnvReachesPoolChild:
    """The arm value has to survive the parent/child boundary.

    Measured first, then fixed. With the value inherited rather than passed,
    depth 1 reached its children and depths 2 and 3 did not, and unsetting it
    in the parent still left children reading "1": forkserver snapshots the
    environment when its server process starts. Three arms would have run at
    one depth and reported three near-identical result sets with no error.

    These tests drive pool._worker's env_overrides argument, which is the
    mechanism that replaced the inheritance.
    """

    @pytest.mark.parametrize("depth", ["1", "2", "3"])
    def test_each_arm_reaches_its_child(self, depth):
        from code_forge.eval import pool as pool_mod

        with ProcessPoolExecutor(max_workers=1) as ex:
            got = ex.submit(
                _apply_and_read, {"FORGE_CLEAN_ROUND_THRESHOLD": depth}
            ).result()
        assert got == depth, (
            "arm depth %s did not reach the pool child (got %r). Every arm "
            "would run at the same depth and the sweep would be void."
            % (depth, got)
        )
        assert "env_overrides" in pool_mod._worker.__code__.co_varnames

    def test_a_later_arm_is_not_shadowed_by_an_earlier_one(self):
        # The exact failure the inherited version had: arm 1's value stuck
        # and arm 3 silently replayed it.
        seen = []
        with ProcessPoolExecutor(max_workers=1) as ex:
            for depth in ("1", "3"):
                seen.append(
                    ex.submit(
                        _apply_and_read,
                        {"FORGE_CLEAN_ROUND_THRESHOLD": depth},
                    ).result()
                )
        assert seen == ["1", "3"], (
            "second arm saw %r; a stale value means arms are not "
            "independent" % seen
        )

    def test_forge_resolution_sees_the_arm_value_in_a_child(self):
        # Transport is not enough: the value has to win where the review
        # loop reads it. tier_threshold(500, ...) defaults above 1, so an
        # override that fails to arrive resolves to the tier value.
        with ProcessPoolExecutor(max_workers=1) as ex:
            got = ex.submit(
                _apply_and_resolve, {"FORGE_CLEAN_ROUND_THRESHOLD": "3"}
            ).result()
        assert got == 3, (
            "forge resolved %r in the child despite the arm setting 3" % (got,)
        )

    def test_worker_without_overrides_changes_nothing(self):
        # The control. If a child reports a value with no overrides passed,
        # the tests above are measuring inheritance rather than the argument.
        with ProcessPoolExecutor(max_workers=1) as ex:
            got = ex.submit(_apply_and_read, None).result()
        assert got is None


class TestResolutionPathStaysReal:
    """The mirrors above have to keep matching the shipped code."""

    def test_worker_applies_overrides_the_same_way(self):
        # _apply_and_read mirrors the top of pool._worker. If _worker stops
        # applying overrides, or applies them differently, this fails and
        # points at the mirror.
        import inspect

        from code_forge.eval import pool as pool_mod

        src = inspect.getsource(pool_mod._worker)
        # c79308c moved the apply into _apply_env_overrides so it can be
        # undone in finally; the mirror now points at that helper.
        assert "_apply_env_overrides(env_overrides)" in src
        helper = inspect.getsource(pool_mod._apply_env_overrides)
        assert "os.environ[k] = str(v)" in helper
        assert "_restore_env(saved_env)" in src

    def test_run_pool_forwards_overrides_on_both_paths(self):
        # An earlier version of this test counted occurrences of the word
        # env_overrides in run_pool's source. Deleting the forward to submit
        # -- the exact bug that would make all three arms run at one depth --
        # left three occurrences and the test stayed green.
        #
        # The two paths need different instruments. jobs=1 runs in this
        # process, so a stubbed replay_entry can report the env it saw.
        # jobs>1 runs in children that never see a monkeypatch, so this
        # captures what run_pool hands to submit instead.
        from code_forge.eval import pool as pool_mod

        entries = [_FakeEntry("e1")]

        with pytest.MonkeyPatch.context() as mp:
            mp.delenv("FORGE_CLEAN_ROUND_THRESHOLD", raising=False)
            mp.setattr(pool_mod, "replay_entry", _stub_replay)
            out = pool_mod.run_pool(
                entries,
                corpus_dir=Path("/tmp"),
                backend_name="b",
                runs=1,
                backend_config=None,
                jobs=1,
                env_overrides={"FORGE_CLEAN_ROUND_THRESHOLD": "3"},
            )
        assert out[0].result is not None, out[0].error
        assert out[0].result.skipped_reason == "3", (
            "the serial path ran the review with "
            "FORGE_CLEAN_ROUND_THRESHOLD=%r instead of 3"
            % out[0].result.skipped_reason
        )

        submitted = []

        class _RecordingExecutor:
            def __init__(self, max_workers=None):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def submit(self, fn, *args, **kwargs):
                submitted.append(args)
                raise _StopPool()

            def shutdown(self, **kwargs):
                pass

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(pool_mod, "ProcessPoolExecutor", _RecordingExecutor)
            try:
                pool_mod.run_pool(
                    entries,
                    corpus_dir=Path("/tmp"),
                    backend_name="b",
                    runs=1,
                    backend_config=None,
                    jobs=2,
                    env_overrides={"FORGE_CLEAN_ROUND_THRESHOLD": "3"},
                )
            except _StopPool:
                pass

        assert submitted, "run_pool never submitted anything on jobs=2"
        assert {"FORGE_CLEAN_ROUND_THRESHOLD": "3"} in submitted[0], (
            "the parallel path submitted %r without the arm overrides; "
            "every worker would review at the inherited depth"
            % (submitted[0],)
        )

    def test_cli_passes_the_arm_depth_as_an_override(self):
        # Behavioural: the arm builder must put the depth in the overrides
        # dict that reaches run_pool. An earlier version matched a literal
        # in cli.py and broke when the same behaviour moved into
        # _arm_env_overrides, which is a move this test should not notice.
        from code_forge.cli import _arm_env_overrides

        class _Args:
            arm_depth = 3
            arm_engine = "real"

        assert _arm_env_overrides(_Args())["FORGE_CLEAN_ROUND_THRESHOLD"] == "3"

    def test_run_pool_receives_the_arm_overrides(self):
        # The other half: whatever the builder produces has to be what the
        # CLI hands to run_pool, not a separately constructed dict.
        from pathlib import Path

        import code_forge.cli as cli_mod

        src = Path(cli_mod.__file__).read_text()
        assert "env_overrides=_arm_env_overrides(args)" in src

    def test_cli_resolution_shape_unchanged(self):
        from pathlib import Path

        import code_forge.cli as cli_mod

        src = Path(cli_mod.__file__).read_text()
        assert 'os.environ.get("FORGE_CLEAN_ROUND_THRESHOLD")' in src
        assert "_clean_threshold = tier_threshold(" in src

    def test_env_override_beats_the_tier_default(self):
        # The property the sweep depends on: a 500-line diff has a tier
        # default above 1, and the override has to win anyway. If tier logic
        # ever outranks the override, arm 1 would silently run deeper.
        from code_forge.diff import tier_threshold

        assert tier_threshold(500, False, None) > 1
        assert tier_threshold(500, False, 1) == 1
