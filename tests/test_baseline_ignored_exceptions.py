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


_LONG_PATH = "/home/box/.local/share/uv/python/cpython-3.12.15-linux-x86_64-gnu/lib/python3.12"


def _clean(detail):
    return not any(word in detail for word in ("ChildProcessError", "ResourceTracker", "Exception ignored"))


def test_echild_shutdown_noise_is_not_the_cause():
    detail = _output_detail(_ECHILD, _PAD)
    assert _clean(detail) and "stderr" not in detail, detail


def test_echild_noise_with_real_paths_is_dropped_from_the_fallback_excerpt():
    """#185: the fallback excerpt is built from the filtered stream too."""
    noise = _ECHILD.replace("/py", _LONG_PATH)
    detail = _output_detail(noise, _PAD)
    assert _clean(detail) and "stderr" not in detail, detail


def test_short_output_is_filtered():
    detail = _output_detail("Exception ignored in: <x>\nChildProcessError: [Errno 10] No child processes\n", None)
    assert detail == "", detail


def test_report_without_traceback_drops_its_exception_line():
    """#185 P3: the unraisable-hook format without a traceback."""
    noise = (
        "Exception ignored while calling deallocator <function ResourceTracker.__del__>:\n"
        "ChildProcessError: [Errno 10] No child processes\n"
    )
    detail = _output_detail(noise + "x" * 300 + "\n", None)
    assert _clean(detail) and detail.startswith("; stderr: xxx"), detail


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
