"""Internal failures must not be swallowed by fail-open degradation paths.

Several command-line paths degrade gracefully when a helper fails: the review
returns no findings, a replay entry is recorded as skipped, an oversized
contract is injected raw, a packaging lookup reports a friendly error.

A bare ``except Exception`` also catches interpreter-level failures such as
``MemoryError``. The degraded result is then indistinguishable from a genuinely
clean one -- an empty review reads as "nothing to report", a skipped entry reads
as "backend refused". These tests pin the boundary: environmental faults stay
degraded, ``MemoryError`` propagates.

``KeyboardInterrupt`` and ``SystemExit`` derive from ``BaseException`` and were
never caught here, so they are not covered -- asserting on them would test the
language, not this change.

Helpers imported inside a function are patched at their source module.
"""
import argparse

import pytest

from code_forge import cli

DEGRADED_ERRORS = [RuntimeError, ValueError, OSError]

# Must name a test file, otherwise the function returns before the model call.
DIFF_WITH_TEST_FILE = """diff --git a/tests/test_example.py b/tests/test_example.py
index 1111111..2222222 100644
--- a/tests/test_example.py
+++ b/tests/test_example.py
@@ -1,2 +1,3 @@
 def test_example():
     assert True
+    assert 1 == 1
"""


def _raiser(error_type):
    def fail(*args, **kwargs):
        raise error_type("injected failure")

    return fail


# --- assertion review: empty findings must not mask a crash ----------------

@pytest.mark.parametrize("error_type", DEGRADED_ERRORS)
def test_assertion_review_degrades_on_environment_failure(monkeypatch, error_type):
    """Network-class faults keep the documented fail-open behaviour."""
    monkeypatch.setattr("code_forge.llm_invoke.llm_invoke", _raiser(error_type))
    assert cli._run_test_assertion_review(DIFF_WITH_TEST_FILE, backend=None) == []


def test_assertion_review_propagates_internal_failure(monkeypatch):
    """Interpreter-level faults must not be reported as an empty review."""
    monkeypatch.setattr("code_forge.llm_invoke.llm_invoke", _raiser(MemoryError))
    with pytest.raises(MemoryError):
        cli._run_test_assertion_review(DIFF_WITH_TEST_FILE, backend=None)


# --- contract summarisation: raw-content fallback --------------------------

def _oversized_contract():
    return "invariant: " + ("x" * 5000)


@pytest.mark.parametrize("error_type", DEGRADED_ERRORS)
def test_contract_summary_degrades_on_environment_failure(monkeypatch, error_type):
    """A failed summarisation injects the raw contract and warns."""
    monkeypatch.setattr("code_forge.llm_invoke.llm_invoke", _raiser(error_type))
    warnings = []
    out = cli._merge_contract_spec(
        "",
        _oversized_contract(),
        backend=object(),
        warn_fn=warnings.append,
    )
    assert "invariant:" in out
    assert any("injecting raw content" in w for w in warnings)


def test_contract_summary_propagates_internal_failure(monkeypatch):
    """Memory exhaustion must not be reported as a summarisation failure."""
    monkeypatch.setattr("code_forge.llm_invoke.llm_invoke", _raiser(MemoryError))
    with pytest.raises(MemoryError):
        cli._merge_contract_spec(
            "", _oversized_contract(), backend=object(), warn_fn=lambda _m: None
        )


# --- bundled skill lookup: packaging faults --------------------------------

def _install_args(dest):
    return argparse.Namespace(
        dest=str(dest), target="universal", skill=None, quiet=True
    )


@pytest.mark.parametrize("error_type", DEGRADED_ERRORS)
def test_install_skill_degrades_on_packaging_failure(monkeypatch, tmp_path, error_type):
    """A broken package layout exits with the CLI error code, not a traceback."""
    monkeypatch.setattr("importlib.resources.files", _raiser(error_type))
    rc = cli._run_install_skill(_install_args(tmp_path), cwd=tmp_path)
    assert rc == cli.EXIT_CLI_ERROR


def test_install_skill_propagates_internal_failure(monkeypatch, tmp_path):
    """Memory exhaustion must not be reported as a packaging fault."""
    monkeypatch.setattr("importlib.resources.files", _raiser(MemoryError))
    with pytest.raises(MemoryError):
        cli._run_install_skill(_install_args(tmp_path), cwd=tmp_path)


# --- bundled skill listing: directory scan faults --------------------------

def _failing_scandir(error_type, real_root):
    """Fail only when listing the bundled skill root, so lookup still works."""
    from pathlib import Path as _Path

    # Capture the attribute being patched, so the passthrough calls the real
    # implementation rather than recursing into the replacement.
    original = _Path.iterdir
    target = _Path(real_root).resolve()

    def iterdir(self):
        if _Path(self).resolve() == target:
            raise error_type("injected failure")
        return original(self)

    return iterdir


@pytest.mark.parametrize("error_type", DEGRADED_ERRORS)
def test_skill_listing_degrades_on_scan_failure(monkeypatch, tmp_path, error_type):
    """An unreadable bundle directory exits with the CLI error code."""
    from pathlib import Path as _Path

    root = _Path(cli.__file__).parent / "skills"
    monkeypatch.setattr(_Path, "iterdir", _failing_scandir(error_type, root))
    rc = cli._run_install_skill(_install_args(tmp_path), cwd=tmp_path)
    assert rc == cli.EXIT_CLI_ERROR


def test_skill_listing_propagates_internal_failure(monkeypatch, tmp_path):
    """Memory exhaustion must not be reported as an unreadable bundle."""
    from pathlib import Path as _Path

    root = _Path(cli.__file__).parent / "skills"
    monkeypatch.setattr(_Path, "iterdir", _failing_scandir(MemoryError, root))
    with pytest.raises(MemoryError):
        cli._run_install_skill(_install_args(tmp_path), cwd=tmp_path)


# --- advisory listing: shown when a named skill is missing -----------------

def _missing_skill_args(dest):
    return argparse.Namespace(
        dest=str(dest), target="universal", skill="no-such-skill", quiet=True
    )


@pytest.mark.parametrize("error_type", DEGRADED_ERRORS)
def test_advisory_listing_degrades_on_scan_failure(monkeypatch, tmp_path, error_type):
    """A failed advisory listing still reports the missing skill."""
    from pathlib import Path as _Path

    root = _Path(cli.__file__).parent / "skills"
    monkeypatch.setattr(_Path, "iterdir", _failing_scandir(error_type, root))
    rc = cli._run_install_skill(_missing_skill_args(tmp_path), cwd=tmp_path)
    assert rc == cli.EXIT_CLI_ERROR


def test_advisory_listing_propagates_internal_failure(monkeypatch, tmp_path):
    """Memory exhaustion must not be hidden behind an advisory listing."""
    from pathlib import Path as _Path

    root = _Path(cli.__file__).parent / "skills"
    monkeypatch.setattr(_Path, "iterdir", _failing_scandir(MemoryError, root))
    with pytest.raises(MemoryError):
        cli._run_install_skill(_missing_skill_args(tmp_path), cwd=tmp_path)


# --- inline canary: degrades to DELEGATED on failure -----------------------

def _canary_args():
    return argparse.Namespace(canary=True, git_diff=None)


def _dispatch_canary(monkeypatch, error_type):
    """Drive the inline canary path with resolve_backend raising."""
    import code_forge.backend as backend_mod

    def boom(*a, **k):
        raise error_type("injected failure")

    monkeypatch.setattr(backend_mod, "resolve_backend", boom)
    return cli._dispatch_inline_canary(
        "inline", _canary_args(), {}, {}, {}, "."
    )


@pytest.mark.parametrize("error_type", DEGRADED_ERRORS)
def test_inline_canary_degrades_on_backend_failure(monkeypatch, error_type):
    """A backend fault leaves the canary check delegated, not crashed."""
    from code_forge.state import Verdict

    verdict = _dispatch_canary(monkeypatch, error_type)
    assert verdict is Verdict.DELEGATED


def test_inline_canary_propagates_internal_failure(monkeypatch):
    """Memory exhaustion must abort rather than silently delegate."""
    with pytest.raises(MemoryError):
        _dispatch_canary(monkeypatch, MemoryError)


# --- canary generation: falls back to templates ----------------------------

def _drive_canary_provider(monkeypatch, error_type):
    """Run the inline canary with a live backend and a failing model call."""
    import code_forge.backend as backend_mod
    import code_forge.canary_gen as canary_gen_mod
    import code_forge.llm_invoke as llm_mod

    monkeypatch.setattr(
        backend_mod, "resolve_backend", lambda *a, **k: object()
    )

    def boom(*a, **k):
        raise error_type("injected failure")

    monkeypatch.setattr(llm_mod, "llm_invoke", boom)

    seen = {}

    def fake_run(**kwargs):
        # Exercise the provider closure the way the canary engine would.
        seen["result"] = kwargs["canary_provider"]("diff")
        return _CANARY_VERDICT, []

    monkeypatch.setattr(canary_gen_mod, "run_inline_canary", fake_run)
    verdict = cli._dispatch_inline_canary(
        "inline", _canary_args(), {}, {}, {}, "."
    )
    return verdict, seen


_CANARY_VERDICT = object()


@pytest.mark.parametrize("error_type", DEGRADED_ERRORS)
def test_canary_generation_degrades_to_templates(monkeypatch, error_type):
    """A failed generation falls back instead of aborting the review."""
    verdict, seen = _drive_canary_provider(monkeypatch, error_type)
    assert verdict is _CANARY_VERDICT
    assert seen["result"] == []


def test_canary_generation_propagates_internal_failure(monkeypatch):
    """Memory exhaustion must not be masked by the template fallback."""
    with pytest.raises(MemoryError):
        _drive_canary_provider(monkeypatch, MemoryError)


# --- contract digest: defense-in-depth wrapper -----------------------------

@pytest.mark.parametrize("error_type", DEGRADED_ERRORS)
def test_contract_digest_degrades_on_loader_failure(monkeypatch, tmp_path, error_type):
    """A broken contract loader yields an empty digest, not an abort."""
    monkeypatch.setattr(
        "code_forge.contract_loader.load_contract_digest", _raiser(error_type)
    )
    out = cli._safe_load_contract_digest(tmp_path / "contracts.yaml", tmp_path)
    assert out == ""


def test_contract_digest_propagates_internal_failure(monkeypatch, tmp_path):
    """Memory exhaustion must not be reported as a missing contract digest."""
    monkeypatch.setattr(
        "code_forge.contract_loader.load_contract_digest", _raiser(MemoryError)
    )
    with pytest.raises(MemoryError):
        cli._safe_load_contract_digest(tmp_path / "contracts.yaml", tmp_path)
