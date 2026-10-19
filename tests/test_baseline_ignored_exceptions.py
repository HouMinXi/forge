"""Interpreter "Exception ignored" reports are never the baseline cause."""

import pytest

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
    real = "ModuleNotFoundError: No module named 'pkg'\n" + "x" * 300 + "\n"
    detail = _output_detail(noise + real, None)
    assert _clean(detail) and detail.startswith("; stderr: ModuleNotFoundError"), detail


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


# Real Python 3.12 unraisable output for raise RuntimeError("line1\nline2 continues")
_MULTILINE = (
    "Exception ignored in: <function C.__del__ at 0x7f876e444e00>\n"
    "Traceback (most recent call last):\n"
    '  File "/tmp/u2.py", line 7, in __del__\n'
    '    def __del__(self): raise RuntimeError("line1\\nline2 continues")\n'
    "                       ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n"
    "RuntimeError: line1\n"
    "line2 continues\n"
    "line3 also the message\n"
)


def test_multiline_ignored_message_continuation_is_dropped():
    """#182 falsification P3: a message spanning lines leaked its continuation."""
    detail = _output_detail(_MULTILINE, None)
    assert "line2" not in detail and "line3" not in detail and "line1" not in detail


@pytest.mark.parametrize(
    "real",
    [
        "/usr/bin/python3: No module named pytest",
        "No module named pytest",
        "ModuleNotFoundError: No module named 'pkg'",
        "E   assert 1 == 2",
        "FAILED tests/test_mod.py::test_x - assert 1 == 2",
        "ERROR tests/test_mod.py - ImportError",
        "= 1 failed in 0.1s =",
    ],
)
def test_output_after_a_multiline_ignored_message_is_kept(real):
    detail = _output_detail(_MULTILINE + real + "\n", None)
    assert " ".join(real.split()) in detail and "line2" not in detail


def test_unrecognized_prose_right_after_an_ignored_report_is_dropped():
    """Known limit: Python prints nothing after the message, so a plain line
    that doesn't look like output is read as message continuation until a
    line that does (blank, indented, ``prog: msg``, pytest markers)."""
    noise = "Exception ignored in: <x>\nChildProcessError: [Errno 10] No child processes\n"
    detail = _output_detail(noise + "plain prose\nE   assert 0\n", None)
    assert "plain prose" not in detail and "E assert 0" in detail


def test_next_ignored_report_ends_a_multiline_message():
    detail = _output_detail(_MULTILINE + _ECHILD + "No module named pytest\n", None)
    assert "No module named pytest" in detail
    assert "line2" not in detail and "ChildProcessError" not in detail


def test_blank_line_ends_a_multiline_ignored_message():
    detail = _output_detail(_MULTILINE + "\nplain cause text\n", None)
    assert "plain cause text" in detail and "line2" not in detail


_ECHILD_SHORT = "Exception ignored in: <x>\nChildProcessError: [Errno 10] No child processes\n"


@pytest.mark.parametrize(
    "real",
    [
        "INTERNALERROR> Traceback (most recent call last):",
        "Fatal Python error: Segmentation fault",
        "Killed",
        "Segmentation fault (core dumped)",
        "Aborted (core dumped)",
        "ImportError while loading conftest '/repo/tests/conftest.py'.",
        "npm ERR! code ELIFECYCLE",
    ],
)
def test_crash_and_tool_lines_after_an_ignored_report_are_kept(real):
    """#182 review 6261799979: these end a message; 2dad69c kept them all."""
    for noise in (_ECHILD_SHORT, _MULTILINE):
        detail = _output_detail(noise + real + "\n", None)
        assert " ".join(real.split()) in detail, detail
        assert "line2" not in detail


def test_dropped_continuation_never_leaves_a_silently_empty_cause():
    detail = _output_detail(_MULTILINE, None)
    assert detail == "; stderr: (2 lines after an ignored-exception report omitted)"
    noise_then_prose = _ECHILD_SHORT + "plain prose\nmore prose\nlast\n"
    assert _output_detail(noise_then_prose, None) == (
        "; stderr: (3 lines after an ignored-exception report omitted)"
    )


def test_no_marker_when_nothing_was_dropped_or_a_cause_remains():
    assert _output_detail(_ECHILD_SHORT, None) == ""
    detail = _output_detail(_MULTILINE + "E   assert 0\n", None)
    assert "omitted" not in detail and "E assert 0" in detail
