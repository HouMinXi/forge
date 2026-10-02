"""Exercise import-time signal setup in a neutral, isolated interpreter."""

import json
from pathlib import Path
import subprocess
import sys

import pytest


_CHILD = """
import json
from pathlib import Path
import signal
import sys
from types import FrameType

source = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(source))
events = []

def previous(signum, frame):
    assert isinstance(frame, FrameType)
    events.append(["previous", signum, True])

signal.signal(signal.SIGINT, previous)
signal.signal(signal.SIGTERM, previous)
import code_forge.llm_invoke as invoke
assert Path(invoke.__file__).resolve() == source / "code_forge" / "llm_invoke.py"
handlers = [signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)]
assert invoke._handlers_installed is True
assert all(callable(handler) and handler is not previous for handler in handlers)

scenario = sys.argv[2]
if scenario != "import":
    signum = int(sys.argv[3])
    invoke._active_proc = "active-child" if scenario != "idle" else None

    def kill_tree(proc):
        events.append(["kill", proc])
        if scenario == "cleanup-error":
            raise OSError("cleanup transport failed")

    invoke._kill_tree = kill_tree
    signal.raise_signal(signum)

print(json.dumps({"installed": invoke._handlers_installed, "events": events}))
"""


def _run_child(tmp_path, scenario, signum=0, *, script=_CHILD, expected_stderr=None):
    source = Path(__file__).resolve().parents[1] / "src"
    command = [
        sys.executable,
        "-I",
        "-B",
        "-X",
        f"pycache_prefix={tmp_path / 'isolated-bytecode'}",
        "-c",
        script,
        str(source),
        scenario,
        str(signum),
    ]
    result = subprocess.run(
        command,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    if expected_stderr is not None:
        assert expected_stderr in result.stderr
    return json.loads(result.stdout)


def test_fresh_import_installs_both_chained_handlers(tmp_path):
    assert _run_child(tmp_path, "import") == {"installed": True, "events": []}


def test_fresh_import_tolerates_a_nonfatal_warning(tmp_path):
    noisy_child = (
        "import warnings\n"
        "warnings.simplefilter('always', RuntimeWarning)\n"
        "warnings.warn('nonfatal import warning', RuntimeWarning)\n" + _CHILD
    )
    assert _run_child(
        tmp_path,
        "import",
        script=noisy_child,
        expected_stderr="RuntimeWarning: nonfatal import warning",
    ) == {"installed": True, "events": []}


def test_child_script_override_propagates_failure(tmp_path):
    with pytest.raises(AssertionError):
        _run_child(tmp_path, "import", script="raise SystemExit(9)")


@pytest.mark.parametrize("signal_name", ["SIGINT", "SIGTERM"])
@pytest.mark.parametrize("scenario", ["active", "cleanup-error", "idle"])
def test_installed_handler_preserves_cleanup_and_chaining(tmp_path, signal_name, scenario):
    import signal

    signum = int(getattr(signal, signal_name))
    expected = [["previous", signum, True]]
    if scenario != "idle":
        expected.insert(0, ["kill", "active-child"])
    assert _run_child(tmp_path, scenario, signum) == {
        "installed": True,
        "events": expected,
    }
