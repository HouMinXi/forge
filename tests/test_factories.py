# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""STATE-10 factory + AutoFixer + revert_fn tests."""

import inspect
from pathlib import Path
from unittest.mock import patch

import pytest

from code_forge.autofix import AutoFixer, FixOutcome, StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.factories import (
    _NonGitSafeAutoFixer,
    build_autofixer,
    build_falsifier,
    build_revert_fn,
)
from code_forge.falsify import StubFalsifier
from code_forge.state import Disposition, StateFinding


def _make_resolved(mode_hint: str) -> ResolvedReview:
    """Create a minimal ResolvedReview for testing."""
    return ResolvedReview(
        source_files=[Path("a.py")],
        baseline_content=None,
        git_diff="diff --git a/a.py b/a.py" if mode_hint == "git"
        else None,
        mode_hint=mode_hint,
    )


def _make_finding(fp: str = "fp-test") -> StateFinding:
    """Create a minimal StateFinding for testing."""
    return StateFinding(
        id="f-1",
        fingerprint=fp,
        source="L0",
        disposition=Disposition.CONFIRMED,
        file="a.py",
        line_range=[1, 5],
        description="test finding",
    )


class TestBuildFalsifier:
    """STATE-10 engine factory."""

    def test_auto_returns_real_falsifier(self):
        """SC-7(a): auto + Phase 4 importable -> RealFalsifier."""
        from code_forge.falsify_real import RealFalsifier
        f = build_falsifier("auto")
        assert isinstance(f, RealFalsifier)

    def test_stub_returns_stub(self):
        """SC-7(b): stub -> StubFalsifier always."""
        f = build_falsifier("stub")
        assert isinstance(f, StubFalsifier)

    def test_real_returns_real_falsifier(self):
        """SC-7(c): real + Phase 4 present -> RealFalsifier."""
        from code_forge.falsify_real import RealFalsifier
        f = build_falsifier("real")
        assert isinstance(f, RealFalsifier)

    def test_unknown_engine_raises(self):
        """Unknown engine -> ValueError."""
        with pytest.raises(ValueError, match="unknown engine"):
            build_falsifier("bogus")


class TestBuildL1Provider:
    """L1 provider factory."""

    def test_stub_returns_empty(self):
        from code_forge.factories import build_l1_provider
        from code_forge.llm_invoke import Usage
        p = build_l1_provider("stub", None)
        findings, excerpts, usage, duration = p()
        assert findings == []
        assert usage == Usage()
        assert duration == 0.0

    def test_real_returns_callable(self):
        from code_forge.factories import build_l1_provider
        resolved = _make_resolved("git")
        p = build_l1_provider("real", resolved)
        assert callable(p)

    def test_auto_returns_callable(self):
        from code_forge.factories import build_l1_provider
        resolved = _make_resolved("git")
        p = build_l1_provider("auto", resolved)
        assert callable(p)

    def test_stub_never_calls_llm(self):
        from code_forge.factories import build_l1_provider
        from code_forge.llm_invoke import Usage
        from unittest.mock import patch
        p = build_l1_provider("stub", None)
        with patch("code_forge.llm_invoke.llm_invoke") as mock:
            findings, excerpts, usage, duration = p()
        assert findings == []
        assert usage == Usage()
        assert duration == 0.0
        mock.assert_not_called()


class TestBuildAutofixer:
    """AutoFixer factory with non-git wrapper."""

    def test_git_mode_returns_plain_stub(self):
        """SC-9(d): git mode -> StubAutoFixer (no wrapper)."""
        resolved = _make_resolved("git")
        af = build_autofixer(resolved)
        assert isinstance(af, StubAutoFixer)
        assert not isinstance(af, _NonGitSafeAutoFixer)

    def test_non_git_mode_returns_wrapper(self):
        """SC-9(h) R3-6: non-git -> _NonGitSafeAutoFixer."""
        resolved = _make_resolved("non-git")
        af = build_autofixer(resolved)
        assert isinstance(af, _NonGitSafeAutoFixer)


class TestNonGitSafeAutoFixer:
    """R2-M1 wrapper behavior."""

    def test_parse_fail_converted_to_no_change(self):
        """SC-44 R3-6: PARSE_FAIL -> NO_CHANGE."""
        inner = StubAutoFixer()
        # StubAutoFixer default is SUCCESS; we need PARSE_FAIL
        inner._default = FixOutcome.PARSE_FAIL
        wrapper = _NonGitSafeAutoFixer(inner)
        finding = _make_finding()
        result = wrapper.fix(finding, "non-git")
        assert result == FixOutcome.NO_CHANGE

    def test_success_passes_through(self):
        """SUCCESS passes through identity."""
        inner = StubAutoFixer()
        inner._default = FixOutcome.SUCCESS
        wrapper = _NonGitSafeAutoFixer(inner)
        finding = _make_finding()
        result = wrapper.fix(finding, "non-git")
        assert result == FixOutcome.SUCCESS

    def test_no_change_passes_through(self):
        """NO_CHANGE passes through identity."""
        inner = StubAutoFixer()
        inner._default = FixOutcome.NO_CHANGE
        wrapper = _NonGitSafeAutoFixer(inner)
        finding = _make_finding()
        result = wrapper.fix(finding, "non-git")
        assert result == FixOutcome.NO_CHANGE

    def test_exception_passes_through(self):
        """EXCEPTION passes through identity."""
        inner = StubAutoFixer()
        inner._default = FixOutcome.EXCEPTION
        wrapper = _NonGitSafeAutoFixer(inner)
        finding = _make_finding()
        result = wrapper.fix(finding, "non-git")
        assert result == FixOutcome.EXCEPTION

    def test_abc_signature_match(self):
        """SC-45 R3-7: fix signature matches AutoFixer ABC."""
        abc_sig = inspect.signature(AutoFixer.fix)
        wrapper_sig = inspect.signature(_NonGitSafeAutoFixer.fix)
        # Compare parameter names and count (annotation string
        # format differs due to __future__ annotations).
        assert list(abc_sig.parameters.keys()) == \
            list(wrapper_sig.parameters.keys())
        # Verify return annotation resolves to same type.
        import typing
        abc_hints = typing.get_type_hints(AutoFixer.fix)
        wrapper_hints = typing.get_type_hints(_NonGitSafeAutoFixer.fix)
        assert abc_hints == wrapper_hints


class TestBuildRevertFn:
    """revert_fn factory dispatch."""

    def test_git_mode_returns_callable(self):
        """SC-8(e): git mode -> callable."""
        resolved = _make_resolved("git")
        fn = build_revert_fn(resolved, Path("/tmp"))
        assert callable(fn)

    def test_non_git_mode_raises_not_implemented(self):
        """SC-26 B1: non-git mode -> raises NotImplementedError."""
        resolved = _make_resolved("non-git")
        fn = build_revert_fn(resolved, Path("/tmp"))
        assert callable(fn)
        finding = _make_finding()
        with pytest.raises(NotImplementedError, match="v2.0"):
            fn(finding)

    def test_unknown_mode_hint_raises(self):
        """SC-8(g): unknown mode_hint -> ValueError."""
        resolved = ResolvedReview(
            source_files=[],
            baseline_content=None,
            git_diff=None,
            mode_hint="unknown",
        )
        with pytest.raises(ValueError, match="unknown mode_hint"):
            build_revert_fn(resolved, Path("/tmp"))

    def test_git_restore_calls_subprocess(self, tmp_path):
        """SC-8(e): git restore invoked with correct args."""
        resolved = _make_resolved("git")
        fn = build_revert_fn(resolved, tmp_path)
        finding = _make_finding()
        with patch("code_forge.factories.subprocess.run") as mock_run:
            fn(finding)
            mock_run.assert_called_once()
            args = mock_run.call_args
            cmd = args[0][0]
            assert cmd == ["git", "restore", "--", "a.py"]
            assert args[1]["cwd"] == str(tmp_path)
            assert args[1]["check"] is True


class TestBuildL2Runner:
    """Test 15-16: build_l2_runner factory."""

    def test_mutmut_on_path_returns_run_mutation(self):
        """Test 15: mutmut on PATH -> returns callable that delegates to run_mutation."""
        from unittest.mock import MagicMock, patch

        mock_which = MagicMock(return_value="/usr/bin/mutmut")

        with patch("code_forge.factories.shutil.which", mock_which):
            from code_forge.factories import build_l2_runner

            l2_runner = build_l2_runner()
            assert callable(l2_runner)
            # Verify it's the actual run_mutation function
            from code_forge.mutation import run_mutation

            assert l2_runner is run_mutation

    def test_mutmut_not_on_path_returns_no_op(self):
        """Test 16: mutmut not on PATH -> returns callable that returns MUTATION_SKIPPED."""
        from unittest.mock import MagicMock, patch

        mock_which = MagicMock(return_value=None)

        with patch("code_forge.factories.shutil.which", mock_which):
            from code_forge.factories import build_l2_runner

            l2_runner = build_l2_runner()
            assert callable(l2_runner)

            # Call it and verify it returns MUTATION_SKIPPED
            findings, infra = l2_runner(["test.py"], ["pytest"])
            assert len(findings) == 1
            assert findings[0].id == "MUTATION_SKIPPED"
            assert findings[0].source == "MUTANT"
            assert findings[0].disposition == Disposition.DISMISSED
            assert "not installed" in findings[0].description
            assert len(infra) == 1
            assert "not found" in infra[0]


class TestBuildE2eChecker:
    """Factory test for build_e2e_checker."""

    def test_returns_callable_with_correct_signature(self):
        """build_e2e_checker() returns a callable (diff_text, repo_root) -> (list, list)."""
        from code_forge.factories import build_e2e_checker

        fn = build_e2e_checker()
        assert callable(fn)
        out = fn("", Path("."))
        assert isinstance(out, tuple)
        assert len(out) == 2
        findings, errors = out
        assert isinstance(findings, list)
        assert isinstance(errors, list)


class TestInfraSourceTagging:
    """F3: error-path findings tagged source=INFRA."""

    def test_factories_invoke_fail_tagged_infra(self):
        """invoke-fail finding has source=INFRA and disposition=CONFIRMED."""
        from unittest.mock import patch as _patch
        from code_forge.factories import build_l1_provider
        from code_forge.llm_invoke import LLMInvokeError

        resolved = _make_resolved("git")

        with _patch(
            "code_forge.llm_invoke.llm_invoke"
        ) as mock_invoke:
            mock_invoke.side_effect = LLMInvokeError("timeout")
            provider = build_l1_provider("real", resolved)
            findings, excerpts, usage, duration = provider()

        infra = [f for f in findings if f.source == "INFRA"]
        assert len(infra) >= 1
        for f in infra:
            assert f.disposition == Disposition.CONFIRMED
            assert "invoke-fail" in f.fingerprint

    def test_factories_schema_fail_tagged_infra(self):
        """schema-fail finding has source=INFRA and disposition=CONFIRMED."""
        from unittest.mock import patch as _patch
        from code_forge.factories import build_l1_provider
        from code_forge.llm_invoke import LLMResult, Usage as LLMUsage

        resolved = _make_resolved("git")

        with _patch(
            "code_forge.llm_invoke.llm_invoke"
        ) as mock_invoke:
            mock_invoke.return_value = LLMResult(
                content="not json at all",
                usage=LLMUsage(input_tokens=0, output_tokens=0),
                duration_s=0.0,
            )
            provider = build_l1_provider("real", resolved)
            findings, excerpts, usage, duration = provider()

        infra = [f for f in findings if f.source == "INFRA"]
        assert len(infra) >= 1
        for f in infra:
            assert f.disposition == Disposition.CONFIRMED
            assert "schema-fail" in f.fingerprint
