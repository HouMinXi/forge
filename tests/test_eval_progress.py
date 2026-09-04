"""Progress lines for a 30-hour concurrent eval run.

Phase 58 runs three depth arms at once for roughly thirty hours. Two things
about the progress output are load-bearing at that duration rather than
cosmetic: which arm a line belongs to, and how much of that arm is left.

Without the arm label, three interleaved streams of "[7/150]" on one terminal
cannot be told apart. Without the estimate, a wedged pool is indistinguishable
from a slow one until the per-entry timeout fires hours later.
"""
import textwrap
import time
from types import SimpleNamespace

import pytest


def _progress_from_cli(arm_depth, arm_engine, t_start):
    """Build the real callback out of cli.py's own source.

    An earlier version of this file re-implemented the callback by hand. That
    made every arm-label assertion pass against a cli.py whose label had been
    replaced with a constant: the tests were checking the copy, not the code.
    Measured with a fault injection, only the source-text guard caught it.

    So extract the actual callback body from cli.py and execute it. The label
    expression and the estimate arithmetic under test are then the shipped
    ones, and replacing either in cli.py fails these tests.
    """
    from pathlib import Path

    import code_forge.cli as cli_mod

    src = Path(cli_mod.__file__).read_text().splitlines()

    start = next(
        i for i, ln in enumerate(src)
        if ln.strip().startswith("_run_label = ")
    )
    end = next(
        i for i, ln in enumerate(src[start:], start)
        if ln.strip().startswith("pool_results = run_pool(")
    )
    block = textwrap.dedent("\n".join(src[start:end]))

    lines = []

    class _Recorder:
        @staticmethod
        def write(text):
            if text.strip():
                lines.append(text.rstrip("\n"))

        @staticmethod
        def flush():
            pass

    ns = {
        "args": SimpleNamespace(arm_depth=arm_depth, arm_engine=arm_engine),
        "time": time,
        "sys": SimpleNamespace(stderr=_Recorder),
        "print": print,
    }
    ns["_t_start"] = t_start
    exec(block, ns)  # noqa: S102 - executing our own shipped source
    # The block re-reads time.monotonic() for _t_start; pin it to the value
    # the caller chose so the estimate arithmetic is deterministic.
    ns["_t_start"] = t_start
    return ns["_progress"], lines


class TestArmLabel:
    """A line has to name the arm it came from."""

    def test_label_carries_both_arm_coordinates(self):
        cb, lines = _progress_from_cli(2, "stub", time.monotonic())
        cb(1, 150, "entry-a", 91.2)
        assert "[d2/stub]" in lines[0]

    def test_three_arms_produce_distinguishable_lines(self):
        # The actual failure this prevents: three concurrent arms writing to
        # one terminal, with no way to attribute a line to a run.
        seen = set()
        for depth, engine in ((1, "real"), (2, "real"), (3, "real")):
            cb, lines = _progress_from_cli(depth, engine, time.monotonic())
            cb(1, 150, "entry-a", 90.0)
            seen.add(lines[0])
        assert len(seen) == 3, "arms must not produce identical progress lines"

    def test_label_matches_the_ledger_coordinates(self):
        # A progress line and a ledger row have to name the same arm, or
        # correlating a stall with its recorded outcomes means guessing.
        cb, lines = _progress_from_cli(3, "stub", time.monotonic())
        cb(1, 10, "e", 1.0)
        assert "d3/stub" in lines[0]


class TestRemainingEstimate:
    """The estimate is what separates a wedged pool from a slow one."""

    def test_estimate_uses_mean_not_last_entry(self, monkeypatch):
        # Entry cost varies several-fold, so extrapolating from the most
        # recent entry gives a figure that swings wildly run to run. Nine
        # entries at 100s each, then a tenth reported at 1s: the estimate
        # must follow the ~100s mean, not the 1s outlier.
        clock = {"t": 1000.0}
        monkeypatch.setattr(time, "monotonic", lambda: clock["t"])
        t0 = 1000.0
        cb, lines = _progress_from_cli(1, "real", t0)
        for i in range(1, 10):
            clock["t"] = t0 + i * 100.0
            cb(i, 20, "e%d" % i, 100.0)
        clock["t"] = t0 + 901.0
        cb(10, 20, "e10", 1.0)  # one fast entry
        # 901s for 10 entries, 10 left: ~901s ~= 0.25h.
        assert "~0.3h left" in lines[-1]

    def test_no_estimate_before_any_entry_completes(self):
        # done=0 would divide by zero. The first line legitimately has no
        # basis for an estimate and must simply omit it.
        cb, lines = _progress_from_cli(1, "real", time.monotonic())
        cb(0, 150, "starting", 0.0)
        assert "left" not in lines[0]

    def test_estimate_reaches_zero_on_the_final_entry(self, monkeypatch):
        clock = {"t": 500.0}
        monkeypatch.setattr(time, "monotonic", lambda: clock["t"])
        cb, lines = _progress_from_cli(1, "real", 500.0)
        clock["t"] = 500.0 + 3600.0
        cb(150, 150, "last", 24.0)
        assert "~0.0h left" in lines[-1]

    def test_estimate_scales_with_observed_rate(self, monkeypatch):
        # Halving throughput must roughly double the estimate; a figure that
        # ignores elapsed time would report the same number for both.
        out = []
        for elapsed in (3600.0, 7200.0):
            clock = {"t": 0.0}
            monkeypatch.setattr(time, "monotonic", lambda: clock["t"])
            cb, lines = _progress_from_cli(1, "real", 0.0)
            clock["t"] = elapsed
            cb(10, 20, "e", 1.0)
            out.append(lines[-1])
        assert out[0] != out[1]
        assert "~1.0h left" in out[0]
        assert "~2.0h left" in out[1]


class TestSourceStaysInStep:
    """Guard against this file drifting from the callback it mirrors."""

    def test_cli_source_matches_this_shape(self):
        from pathlib import Path

        import code_forge.cli as cli_mod

        src = Path(cli_mod.__file__).read_text()
        # The label expression and the estimate must both still be there. If
        # someone rewrites the callback, this fails and points here.
        assert '_run_label = "d%s/%s" % (args.arm_depth, args.arm_engine)' in src
        assert 'remaining = "  ~%.1fh left" % (eta_s / 3600.0)' in src
        assert "eta_s = (elapsed / done) * (total - done)" in src

    def test_arm_coordinates_are_real_cli_arguments(self):
        # The label reads args.arm_depth / args.arm_engine. If either stops
        # being a parser argument, the callback raises AttributeError mid-run
        # rather than at startup. There is no build_parser to import, so this
        # drives the real entry point and reads the parsed namespace back out
        # of the eval handler.
        import code_forge.cli as cli_mod

        seen = {}

        def _capture(args):
            seen["args"] = args
            return 0

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(cli_mod, "_run_eval", _capture)
            mp.setattr(
                "sys.argv",
                ["code-forge", "eval", "--corpus", "/tmp/x", "--backend", "b"],
            )
            cli_mod.main()

        assert hasattr(seen["args"], "arm_depth")
        assert hasattr(seen["args"], "arm_engine")
