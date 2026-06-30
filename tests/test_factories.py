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


# -- Helpers for coverage guard tests --

_TWO_FILE_DIFF = (
    "diff --git a/src/a.py b/src/a.py\n"
    "--- a/src/a.py\n"
    "+++ b/src/a.py\n"
    "@@ -1,3 +1,4 @@\n"
    " line1\n"
    "+added\n"
    " line2\n"
    "diff --git a/src/b.py b/src/b.py\n"
    "--- a/src/b.py\n"
    "+++ b/src/b.py\n"
    "@@ -5,3 +5,4 @@\n"
    " line5\n"
    "+added2\n"
    " line6\n"
)

_ONE_FILE_DIFF = (
    "diff --git a/src/a.py b/src/a.py\n"
    "--- a/src/a.py\n"
    "+++ b/src/a.py\n"
    "@@ -1,3 +1,4 @@\n"
    " line1\n"
    "+added\n"
    " line2\n"
)


def _stub_llm_response(findings_json, excerpts_json):
    """Build a mock llm_invoke return value from JSON dicts."""
    import json
    from code_forge.llm_invoke import LLMResult, Usage as LLMUsage
    content = json.dumps({
        "findings": findings_json,
        "code_excerpts": excerpts_json,
    })
    return LLMResult(
        content=content,
        usage=LLMUsage(input_tokens=10, output_tokens=10),
        duration_s=0.1,
    )


def _make_resolved_with_diff(diff_text):
    """Create a ResolvedReview with a specific diff_text."""
    return ResolvedReview(
        source_files=[Path("src/a.py"), Path("src/b.py")],
        baseline_content=None,
        git_diff=diff_text,
        mode_hint="git",
    )


class TestCoverageGuard:
    """Coverage guard: detect truncated clean passes."""

    def test_incomplete_coverage_produces_infra_finding(self):
        """(a) Excerpts cover 1 of 2 files -> INFRA finding."""
        from code_forge.factories import build_l1_provider

        resolved = _make_resolved_with_diff(_TWO_FILE_DIFF)
        resp = _stub_llm_response(
            findings_json=[],
            excerpts_json=[
                {"file": "src/a.py", "start_line": 1,
                 "end_line": 4, "content": "line1\nadded\nline2"},
            ],
        )

        with patch("code_forge.llm_invoke.llm_invoke", return_value=resp):
            provider = build_l1_provider("real", resolved)
            findings, excerpts, usage, duration = provider()

        infra = [
            f for f in findings
            if "incomplete-coverage" in f.id
        ]
        assert len(infra) >= 1
        assert infra[0].source == "INFRA"
        assert infra[0].disposition == Disposition.CONFIRMED
        assert "src/b.py" in infra[0].description

    def test_full_coverage_no_infra_finding(self):
        """(b) Excerpts cover all files -> no INFRA finding."""
        from code_forge.factories import build_l1_provider

        resolved = _make_resolved_with_diff(_TWO_FILE_DIFF)
        resp = _stub_llm_response(
            findings_json=[],
            excerpts_json=[
                {"file": "src/a.py", "start_line": 1,
                 "end_line": 4, "content": "line1\nadded\nline2"},
                {"file": "src/b.py", "start_line": 5,
                 "end_line": 8, "content": "line5\nadded2\nline6"},
            ],
        )

        with patch("code_forge.llm_invoke.llm_invoke", return_value=resp):
            provider = build_l1_provider("real", resolved)
            findings, excerpts, usage, duration = provider()

        infra = [
            f for f in findings
            if "incomplete-coverage" in f.id
        ]
        assert len(infra) == 0

    def test_guard_skips_when_findings_present(self):
        """(c) findings > 0 -> guard does not fire, even with partial coverage."""
        from code_forge.factories import build_l1_provider

        resolved = _make_resolved_with_diff(_TWO_FILE_DIFF)
        resp = _stub_llm_response(
            findings_json=[
                {"file": "src/a.py", "line": 2,
                 "severity": "P1", "description": "bug"},
            ],
            excerpts_json=[
                {"file": "src/a.py", "start_line": 1,
                 "end_line": 4, "content": "line1\nadded\nline2"},
            ],
        )

        with patch("code_forge.llm_invoke.llm_invoke", return_value=resp):
            provider = build_l1_provider("real", resolved)
            findings, excerpts, usage, duration = provider()

        infra = [
            f for f in findings
            if "incomplete-coverage" in f.id
        ]
        assert len(infra) == 0

    def test_excerpt_suffix_match_for_absolute_paths(self):
        """(d) Excerpt uses absolute path, basename ambiguous -> suffix match."""
        from code_forge.factories import build_l1_provider

        # Two changed files share basename "a.py" in different dirs,
        # so basename fallback is ambiguous (2 matches). The excerpts
        # use absolute paths that are suffixes of the changed paths.
        # Only suffix match resolves this correctly.
        diff = (
            "diff --git a/src/a.py b/src/a.py\n"
            "--- a/src/a.py\n"
            "+++ b/src/a.py\n"
            "@@ -1,3 +1,4 @@\n"
            " line1\n"
            "+added\n"
            " line2\n"
            "diff --git a/lib/a.py b/lib/a.py\n"
            "--- a/lib/a.py\n"
            "+++ b/lib/a.py\n"
            "@@ -1,3 +1,4 @@\n"
            " x\n"
            "+y\n"
            " z\n"
        )
        resolved = _make_resolved_with_diff(diff)
        resp = _stub_llm_response(
            findings_json=[],
            excerpts_json=[
                {"file": "/home/user/repo/src/a.py", "start_line": 1,
                 "end_line": 4, "content": "line1\nadded\nline2"},
                {"file": "/home/user/repo/lib/a.py", "start_line": 1,
                 "end_line": 4, "content": "x\ny\nz"},
            ],
        )

        with patch("code_forge.llm_invoke.llm_invoke", return_value=resp):
            provider = build_l1_provider("real", resolved)
            findings, excerpts, usage, duration = provider()

        infra = [
            f for f in findings
            if "incomplete-coverage" in f.id
        ]
        assert len(infra) == 0

    def test_basename_fallback_for_bare_filenames(self):
        """(e) Excerpt in different dir, same basename -> basename fallback."""
        from code_forge.factories import build_l1_provider

        # Changed file is "a/foo.py", excerpt says "b/foo.py".
        # Exact match fails, suffix match fails (neither is a suffix
        # of the other), but basename fallback matches ("foo.py" ==
        # "foo.py" and only one such match exists -> unambiguous).
        diff = (
            "diff --git a/a/foo.py b/a/foo.py\n"
            "--- a/a/foo.py\n"
            "+++ b/a/foo.py\n"
            "@@ -1,3 +1,4 @@\n"
            " line1\n"
            "+added\n"
            " line2\n"
        )
        resolved = _make_resolved_with_diff(diff)
        resp = _stub_llm_response(
            findings_json=[],
            excerpts_json=[
                {"file": "b/foo.py", "start_line": 1,
                 "end_line": 4, "content": "line1\nadded\nline2"},
            ],
        )

        with patch("code_forge.llm_invoke.llm_invoke", return_value=resp):
            provider = build_l1_provider("real", resolved)
            findings, excerpts, usage, duration = provider()

        infra = [
            f for f in findings
            if "incomplete-coverage" in f.id
        ]
        assert len(infra) == 0

    def test_deleted_file_not_in_changed_set(self):
        """(f) Regression: parse_diff_files excludes /dev/null by construction."""
        from code_forge.verify import parse_diff_files

        diff_with_delete = (
            "diff --git a/src/a.py b/src/a.py\n"
            "--- a/src/a.py\n"
            "+++ b/src/a.py\n"
            "@@ -1,3 +1,4 @@\n"
            " line1\n"
            "+added\n"
            " line2\n"
            "diff --git a/src/deleted.py b/src/deleted.py\n"
            "--- a/src/deleted.py\n"
            "+++ /dev/null\n"
            "@@ -1,5 +0,0 @@\n"
            "-removed1\n"
            "-removed2\n"
        )
        result = parse_diff_files(diff_with_delete)
        assert "src/a.py" in result
        assert "/dev/null" not in result
        assert "src/deleted.py" not in result

    def test_bug_injection_guard_removed(self):
        """(g) Monkeypatch proves the guard is the mechanism."""
        from code_forge.factories import build_l1_provider

        resolved = _make_resolved_with_diff(_TWO_FILE_DIFF)
        resp = _stub_llm_response(
            findings_json=[],
            excerpts_json=[
                {"file": "src/a.py", "start_line": 1,
                 "end_line": 4, "content": "line1\nadded\nline2"},
            ],
        )

        # Guard active: INFRA finding produced
        with patch("code_forge.llm_invoke.llm_invoke", return_value=resp):
            provider = build_l1_provider("real", resolved)
            findings, _, _, _ = provider()
        infra = [
            f for f in findings
            if "incomplete-coverage" in f.id
        ]
        assert len(infra) >= 1, "guard must produce INFRA finding"

        # Guard bypassed: monkeypatch parse_diff_files to return empty
        # (no changed files -> guard has nothing to check -> no INFRA)
        with patch("code_forge.llm_invoke.llm_invoke", return_value=resp):
            with patch(
                "code_forge.verify.parse_diff_files",
                return_value={},
            ):
                provider2 = build_l1_provider("real", resolved)
                findings2, _, _, _ = provider2()
        infra2 = [
            f for f in findings2
            if "incomplete-coverage" in f.id
        ]
        assert len(infra2) == 0, (
            "with guard bypassed, no INFRA finding expected"
        )


class TestInvokeFailureHandling:
    """LLM invoke failure creates INFRA finding and feeds breaker."""

    def test_invoke_failure_creates_infra(self):
        """LLMInvokeError produces one INFRA finding per failed pass."""
        from code_forge.factories import build_l1_provider
        from code_forge.llm_invoke import LLMInvokeError

        resolved = _make_resolved("git")

        call_count = [0]
        def _side_effect(*a, **kw):
            call_count[0] += 1
            raise LLMInvokeError("auth failed", retryable=False)

        with patch("code_forge.llm_invoke.llm_invoke", side_effect=_side_effect):
            provider = build_l1_provider("real", resolved)
            findings, _, _, _ = provider()

        infra = [f for f in findings if f.source == "INFRA"]
        assert len(infra) == 3, "each of 3 passes should produce INFRA"
        assert call_count[0] == 3

    def test_retry_config_forwarded_to_llm_invoke(self):
        """max_attempts and initial_delay_s forwarded to llm_invoke."""
        from code_forge.factories import build_l1_provider

        resolved = _make_resolved("git")
        good_resp = _stub_llm_response(
            findings_json=[],
            excerpts_json=[
                {"file": "a.py", "start_line": 1,
                 "end_line": 4, "content": "line1\nadded\nline2"},
            ],
        )

        with patch("code_forge.llm_invoke.llm_invoke", return_value=good_resp) as mock:
            provider = build_l1_provider(
                "real", resolved,
                max_attempts=3, initial_delay_s=1.0,
            )
            provider()

        # Each of the 3 passes should forward the retry config
        for call in mock.call_args_list:
            assert call.kwargs.get("max_attempts") == 3
            assert call.kwargs.get("initial_delay_s") == 1.0

    def test_default_retry_config_uses_defaults(self):
        """build_l1_provider with no retry kwargs uses defaults."""
        from code_forge.factories import build_l1_provider

        resolved = _make_resolved("git")
        good_resp = _stub_llm_response(
            findings_json=[],
            excerpts_json=[
                {"file": "a.py", "start_line": 1,
                 "end_line": 4, "content": "line1\nadded\nline2"},
            ],
        )

        with patch("code_forge.llm_invoke.llm_invoke", return_value=good_resp) as mock:
            provider = build_l1_provider("real", resolved)
            provider()

        for call in mock.call_args_list:
            assert call.kwargs.get("max_attempts") == 5
            assert call.kwargs.get("initial_delay_s") == 2.0


class TestBuildSamplingL1Provider:
    def test_build_sampling_l1_provider_success(self):
        from code_forge.factories import build_sampling_l1_provider
        from code_forge.llm_invoke import LLMResult, Usage
        from unittest.mock import patch, MagicMock
        import concurrent.futures

        resolved = _make_resolved_with_diff(_TWO_FILE_DIFF)
        session = MagicMock()
        loop = MagicMock()

        good_resp = LLMResult(
            content={
                "findings": [],
                "code_excerpts": [
                    {"file": "src/a.py", "start_line": 1, "end_line": 4, "content": "line1\nadded\nline2"},
                    {"file": "src/b.py", "start_line": 5, "end_line": 8, "content": "line5\nadded2\nline6"},
                ]
            },
            usage=Usage(0, 0),
            duration_s=0.1,
            is_truncated=False,
        )

        future = concurrent.futures.Future()
        future.set_result(good_resp)

        with patch("code_forge.llm_invoke.invoke_sampling", new_callable=MagicMock), \
             patch("asyncio.run_coroutine_threadsafe", return_value=future):
            provider = build_sampling_l1_provider(session, loop, resolved)
            findings, excerpts, usage, duration = provider()

            assert len(findings) == 0
            assert usage == Usage(0, 0)
            assert len(excerpts) == 6

    def test_build_sampling_l1_provider_empty_diff(self):
        from code_forge.factories import build_sampling_l1_provider
        from code_forge.llm_invoke import Usage
        
        resolved = _make_resolved_with_diff("")
        provider = build_sampling_l1_provider(None, None, resolved)
        findings, excerpts, usage, duration = provider()
        assert findings == []
        assert excerpts == []
        assert usage == Usage(0, 0)
        assert duration == 0.0

    def test_build_sampling_l1_provider_truncation_raises(self):
        from code_forge.factories import build_sampling_l1_provider
        from code_forge.llm_invoke import LLMInvokeError
        from unittest.mock import patch, MagicMock
        import concurrent.futures

        resolved = _make_resolved_with_diff(_TWO_FILE_DIFF)
        session = MagicMock()
        loop = MagicMock()

        # invoke_sampling raises LLMInvokeError on truncation before
        # returning, so the future must propagate the exception.
        future = concurrent.futures.Future()
        future.set_exception(LLMInvokeError(
            "sampling response truncated (stopReason == maxTokens)"
        ))

        with patch("code_forge.llm_invoke.invoke_sampling", new_callable=MagicMock), \
             patch("asyncio.run_coroutine_threadsafe", return_value=future):
            provider = build_sampling_l1_provider(session, loop, resolved)
            with pytest.raises(LLMInvokeError, match="truncated"):
                provider()
