"""Interpreter "Exception ignored" reports are never the baseline cause."""

from code_forge.baseline_guard import _output_detail

_ECHILD = (
    "Exception ignored in: <function ResourceTracker.__del__ at 0x7fd1d4c95120>\n"
    "Traceback (most recent call last):\n"
    '  File "/py/multiprocessing/resource_tracker.py", line 77, in __del__\n'
    '  File "/py/multiprocessing/resource_tracker.py", line 111, in _stop_locked\n'
    "ChildProcessError: [Errno 10] No child processes\n"
)
_PAD = "." * 300 + "\n"


def test_echild_shutdown_noise_is_not_the_cause():
    detail = _output_detail(_ECHILD, _PAD)
    assert "ChildProcessError" not in detail


def test_real_cause_after_ignored_report_is_kept():
    real = (
        "Traceback (most recent call last):\n"
        '  File "x.py", line 1, in <module>\n'
        "ImportError: no module named foo\n"
    )
    detail = _output_detail(_ECHILD + real + _PAD, None)
    assert "ImportError: no module named foo" in detail
    assert "ChildProcessError" not in detail


def test_real_cause_before_ignored_report_is_kept():
    real = (
        "Traceback (most recent call last):\n"
        '  File "x.py", line 1, in <module>\n'
        "ValueError: bad\n"
    )
    detail = _output_detail(real + _ECHILD + _PAD, None)
    assert "ValueError: bad" in detail
