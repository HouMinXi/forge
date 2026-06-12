"""Structural kill tests: _run_hold_loop must propagate backend to RuntimeRunner.

Without this propagation RuntimeRunner falls back to the CLI backend regardless
of the --backend flag, so RUNTIME advisory findings reflect the wrong LLM.
"""
from pathlib import Path


def _cli_source() -> str:
    return (
        Path(__file__).parent.parent
        / "src" / "code_forge" / "cli.py"
    ).read_text(encoding="utf-8")


def test_runtime_runner_receives_backend_kwarg():
    """_run_hold_loop must instantiate RuntimeRunner with backend=backend."""
    src = _cli_source()
    assert "RuntimeRunner(backend=backend)" in src, (
        "RuntimeRunner must be constructed with backend=backend so the configured "
        "review backend is used for RUNTIME advisory axis, not the CLI default"
    )


def test_run_hold_loop_has_backend_param():
    """_run_hold_loop signature must declare backend=None."""
    src = _cli_source()
    assert "backend=None," in src, (
        "_run_hold_loop must accept backend= so the caller can propagate "
        "the resolved BackendConfig to advisory runners"
    )


def test_call_site_passes_backend():
    """Call site must forward backend=backend to _run_hold_loop."""
    src = _cli_source()
    assert "backend=backend," in src, (
        "_run_hold_loop call site must pass backend=backend, not omit it"
    )
