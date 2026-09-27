"""Keep CLI timeout budgets and diagnostics observable to callers."""

import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import code_forge.llm_invoke as invoke
from code_forge.backend import BackendConfig


def test_timeout_preserves_budget_and_diagnostics(monkeypatch):
    backend = BackendConfig(name="timeout-test", type="cli", model="test-model")
    timeout = subprocess.TimeoutExpired(["local-test", "unique-argument"], 7)
    proc = Mock(spec=subprocess.Popen)
    proc.communicate.side_effect = timeout
    popen = Mock(return_value=proc)
    cleanup = Mock()
    clock = Mock(side_effect=[100.0, 107.25])
    monkeypatch.setattr(invoke, "shutil", SimpleNamespace(which=lambda _: "/test/cli"))
    monkeypatch.setattr(
        invoke,
        "subprocess",
        SimpleNamespace(
            Popen=popen,
            PIPE=subprocess.PIPE,
            TimeoutExpired=subprocess.TimeoutExpired,
        ),
    )
    monkeypatch.setattr(invoke, "time", SimpleNamespace(monotonic=clock))
    monkeypatch.setattr(invoke, "_kill_tree", cleanup)
    monkeypatch.setattr(invoke, "_active_proc", None)

    with pytest.raises(invoke.LLMInvokeError) as raised:
        invoke._invoke_cli("prompt", backend, timeout_s=7)

    proc.communicate.assert_called_once_with(timeout=7)
    cleanup.assert_called_once_with(proc)
    assert raised.value.exit_code == -1
    assert raised.value.stderr == str(timeout)
    assert raised.value.duration_s == 7.25
    assert raised.value.is_timeout is True
    assert raised.value.__cause__ is timeout
    assert invoke._active_proc is None


@pytest.mark.skipif(os.name != "posix", reason="Requires POSIX process groups")
def test_real_cli_timeout_reaps_local_child(tmp_path, monkeypatch):
    executable = tmp_path / "slow-cli"
    executable.write_text(
        f"#!{sys.executable}\nimport time\ntime.sleep(3)\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    backend = BackendConfig(
        name="local-slow-cli",
        type="cli",
        model="test-model",
        command=str(executable),
    )
    real_popen = subprocess.Popen
    children = []

    def start_child(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(
        invoke,
        "subprocess",
        SimpleNamespace(
            Popen=start_child,
            PIPE=subprocess.PIPE,
            TimeoutExpired=subprocess.TimeoutExpired,
        ),
    )
    monkeypatch.setattr(invoke, "_active_proc", None)
    try:
        with pytest.raises(invoke.LLMInvokeError) as raised:
            invoke._invoke_cli("local probe", backend, timeout_s=1)
        assert raised.value.is_timeout is True
        assert raised.value.exit_code == -1
        assert isinstance(raised.value.__cause__, subprocess.TimeoutExpired)
        assert raised.value.stderr == str(raised.value.__cause__)
        assert raised.value.duration_s >= 1
        assert len(children) == 1
        assert children[0].returncode is not None
        assert invoke._active_proc is None
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=10)
            for stream in (child.stdout, child.stderr):
                if stream is not None:
                    stream.close()
