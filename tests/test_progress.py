"""Progress event stream tests.

The stream exists so a stuck review stage is visible on every entry
point: each emit carries a wall clock since run start, so the last
line before a silence says what was running and how old the run was.
The flush matters because Python block-buffers stderr into a file,
which is exactly where a hung stage hides its own trace.
"""
from unittest.mock import patch

from code_forge import progress


class TestProgressEmit:
    def test_line_has_clock_and_message(self, capsys):
        progress.emit("pass qodo: calling deepseek-nocache")
        err = capsys.readouterr().err
        assert err.startswith("[forge] t+")
        assert " pass qodo: calling deepseek-nocache\n" in err
        assert "\n" in err and not err.endswith("\n\n")

    def test_flushes_after_every_emit(self):
        with patch("code_forge.progress.sys.stderr") as stderr:
            progress.emit("x")
            stderr.flush.assert_called_once_with()

    def test_two_emits_stay_on_separate_lines(self, capsys):
        progress.emit("round 0 start")
        progress.emit("falsify 1/2: src/x.py:10 (fp1)")
        lines = capsys.readouterr().err.splitlines()
        assert len(lines) == 2
        assert all(line.startswith("[forge] t+") for line in lines)


class TestProgressResetAndFaultTolerance:
    def test_reset_restarts_the_clock(self, capsys):
        with patch("code_forge.progress.time.monotonic",
                   side_effect=[1000.0, 1000.5, 1050.0, 1052.0]):
            progress.reset()
            progress.emit("a")
            progress.reset()
            progress.emit("b")
        lines = capsys.readouterr().err.splitlines()
        # after the second reset the clock starts near zero again
        assert "t+0.5s a" in lines[0]
        assert "t+2.0s b" in lines[1]

    def test_broken_stderr_does_not_raise(self):
        with patch("code_forge.progress.sys.stderr") as stderr:
            stderr.write.side_effect = OSError("stream closed")
            progress.emit("x")  # must not raise

    def test_none_stderr_does_not_raise(self):
        with patch("code_forge.progress.sys.stderr", None):
            progress.emit("x")  # must not raise
