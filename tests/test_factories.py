# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""STATE-10 factory + AutoFixer + revert_fn tests."""

import inspect
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from code_forge.autofix import (
    AutoFixer,
    FixOutcome,
    NoChangeAutoFixer,
    StubAutoFixer,
)
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
        """SC-9(d): git mode -> NoChangeAutoFixer (no wrapper)."""
        resolved = _make_resolved("git")
        af = build_autofixer(resolved)
        assert isinstance(af, NoChangeAutoFixer)
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
        future.set_result([good_resp, good_resp, good_resp])

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

    def test_build_sampling_l1_provider_timeout_cancels_future(self):
        from code_forge.factories import build_sampling_l1_provider
        from unittest.mock import patch, MagicMock
        import concurrent.futures

        resolved = _make_resolved_with_diff(_TWO_FILE_DIFF)
        session = MagicMock()
        loop = MagicMock()

        future = MagicMock(spec=concurrent.futures.Future)
        future.result.side_effect = concurrent.futures.TimeoutError()

        with patch("code_forge.llm_invoke.invoke_sampling", new_callable=MagicMock), \
             patch("asyncio.run_coroutine_threadsafe", return_value=future):
            provider = build_sampling_l1_provider(session, loop, resolved)
            with pytest.raises(concurrent.futures.TimeoutError):
                provider()

        # Outer gather timeout cancels the single future
        assert future.cancel.call_count == 1

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

    def test_build_sampling_l1_provider_empty_response_propagates(self):
        """Non-truncation LLM failures (empty response, stub model, no
        valid JSON) must propagate to the caller, not be swallowed.

        A non-truncation LLMInvokeError used to be swallowed into a
        per-pass INFRA finding and the loop continued to the next pass,
        so the provider returned normally instead of raising -- the MCP
        layer never saw the failure and could not fall back to a
        subprocess backend.
        """
        from code_forge.factories import build_sampling_l1_provider
        from code_forge.llm_invoke import LLMInvokeError
        from unittest.mock import patch, MagicMock
        import concurrent.futures

        resolved = _make_resolved_with_diff(_TWO_FILE_DIFF)
        session = MagicMock()
        loop = MagicMock()

        future = concurrent.futures.Future()
        future.set_exception(LLMInvokeError(
            "sampling response is empty (model=?, stopReason=?)"
        ))

        with patch("code_forge.llm_invoke.invoke_sampling", new_callable=MagicMock), \
             patch("asyncio.run_coroutine_threadsafe", return_value=future):
            provider = build_sampling_l1_provider(session, loop, resolved)
            with pytest.raises(LLMInvokeError, match="empty"):
                provider()

    def test_build_sampling_l1_provider_cancelled_error_propagates(self):
        """asyncio.CancelledError (BaseException) must propagate, not be folded into INFRA finding."""
        import asyncio
        import concurrent.futures
        from unittest.mock import MagicMock, patch
        from code_forge.factories import build_sampling_l1_provider

        resolved = _make_resolved_with_diff(_TWO_FILE_DIFF)
        session = MagicMock()
        loop = MagicMock()

        future = concurrent.futures.Future()
        future.set_result([
            asyncio.CancelledError(),
            asyncio.CancelledError(),
            asyncio.CancelledError(),
        ])

        with patch("code_forge.llm_invoke.invoke_sampling", new_callable=MagicMock), \
             patch("asyncio.run_coroutine_threadsafe", return_value=future):
            provider = build_sampling_l1_provider(session, loop, resolved)
            with pytest.raises(asyncio.CancelledError):
                provider()


class TestParallelL1:
    """Parallel execution: determinism, no-lost-work, failure isolation."""

    _EXCERPTS = [
        {"file": "src/a.py", "start_line": 1, "end_line": 4,
         "content": "line1\nadded\nline2"},
        {"file": "src/b.py", "start_line": 5, "end_line": 8,
         "content": "line5\nadded2\nline6"},
    ]

    @staticmethod
    def _api_backend():
        from code_forge.backend import BackendConfig
        return BackendConfig(
            name="test-api", type="api", model="test",
            format="openai", base_url="http://test")

    def test_cli_backend_stays_serial(self):
        """CLI backend must not enter ThreadPoolExecutor path."""
        from code_forge.backend import BackendConfig
        from code_forge.factories import build_l1_provider
        from unittest.mock import patch

        cli_backend = BackendConfig(
            name="test-cli", type="cli", model="test",
            format=None, base_url=None)

        def mock_invoke(prompt, **kw):
            if "structural code reviewer" in prompt:
                line = 1
            elif "senior engineer" in prompt:
                line = 2
            else:
                line = 3
            return _stub_llm_response(
                [{"file": "src/a.py", "line": line, "severity": "P2",
                  "description": "cli-finding"}], self._EXCERPTS)

        resolved = _make_resolved_with_diff(_TWO_FILE_DIFF)
        with patch("code_forge.llm_invoke.llm_invoke",
                   side_effect=mock_invoke), \
             patch("concurrent.futures.ThreadPoolExecutor",
                   side_effect=AssertionError(
                       "ThreadPoolExecutor must not be used")):
            provider = build_l1_provider(
                "auto", resolved, backend=cli_backend)
            findings, _, _, _ = provider()

        assert len(findings) == 3

    def test_deterministic_fold_order(self):
        """Direction 1: dedup keeps qodo's version of a shared finding."""
        from code_forge.factories import build_l1_provider
        from unittest.mock import patch

        shared = {"file": "src/a.py", "line": 1, "severity": "P2",
                  "description": "shared issue"}

        def mock_invoke(prompt, **kw):
            if "structural code reviewer" in prompt:
                return _stub_llm_response([shared], self._EXCERPTS)
            elif "senior engineer" in prompt:
                return _stub_llm_response([shared], self._EXCERPTS)
            return _stub_llm_response(
                [{"file": "src/a.py", "line": 3, "severity": "P3",
                  "description": "unique-adv"}], self._EXCERPTS)

        resolved = _make_resolved_with_diff(_TWO_FILE_DIFF)
        with patch("code_forge.llm_invoke.llm_invoke",
                   side_effect=mock_invoke):
            provider = build_l1_provider(
                "auto", resolved, backend=self._api_backend())
            findings, _, _, _ = provider()

        assert len(findings) == 2
        shared_f = [f for f in findings if "shared" in f.description]
        assert len(shared_f) == 1
        assert shared_f[0].description.startswith("[qodo]")

    def test_no_lost_work(self):
        """Direction 2: all 3 passes' distinct findings present."""
        from code_forge.factories import build_l1_provider
        from unittest.mock import patch

        def mock_invoke(prompt, **kw):
            if "structural code reviewer" in prompt:
                return _stub_llm_response(
                    [{"file": "src/a.py", "line": 1, "severity": "P2",
                      "description": "from-qodo"}], self._EXCERPTS)
            elif "senior engineer" in prompt:
                return _stub_llm_response(
                    [{"file": "src/a.py", "line": 2, "severity": "P1",
                      "description": "from-expert"}], self._EXCERPTS)
            return _stub_llm_response(
                [{"file": "src/a.py", "line": 3, "severity": "P3",
                  "description": "from-adversarial"}], self._EXCERPTS)

        resolved = _make_resolved_with_diff(_TWO_FILE_DIFF)
        with patch("code_forge.llm_invoke.llm_invoke",
                   side_effect=mock_invoke):
            provider = build_l1_provider(
                "auto", resolved, backend=self._api_backend())
            findings, _, usage, _ = provider()

        assert len(findings) == 3
        descs = {f.description for f in findings}
        assert any("from-qodo" in d for d in descs)
        assert any("from-expert" in d for d in descs)
        assert any("from-adversarial" in d for d in descs)
        assert usage.input_tokens == 30

    def test_failure_isolation_api(self):
        """Direction 3: one pass fails, other two still produce findings."""
        from code_forge.factories import build_l1_provider
        from code_forge.llm_invoke import LLMInvokeError
        from unittest.mock import patch, MagicMock

        def mock_invoke(prompt, **kw):
            if "senior engineer" in prompt:
                raise LLMInvokeError("expert timeout", is_timeout=True)
            if "structural code reviewer" in prompt:
                return _stub_llm_response(
                    [{"file": "src/a.py", "line": 1, "severity": "P2",
                      "description": "qodo-finding"}], self._EXCERPTS)
            return _stub_llm_response(
                [{"file": "src/a.py", "line": 3, "severity": "P2",
                  "description": "adv-finding"}], self._EXCERPTS)

        resolved = _make_resolved_with_diff(_TWO_FILE_DIFF)
        breaker = MagicMock()

        with patch("code_forge.llm_invoke.llm_invoke",
                   side_effect=mock_invoke):
            provider = build_l1_provider(
                "auto", resolved, backend=self._api_backend(),
                breaker=breaker)
            findings, _, _, _ = provider()

        infra = [f for f in findings if f.source == "INFRA"]
        assert len(infra) == 1
        assert "expert" in infra[0].id
        l1 = [f for f in findings if f.source == "L1"]
        assert len(l1) == 2
        breaker.record_timeout.assert_called_once()

    def test_unexpected_exception_isolation_api(self):
        """Non-LLM exception in one pass does not lose other passes."""
        from code_forge.factories import build_l1_provider
        from unittest.mock import patch, MagicMock

        def mock_invoke(prompt, **kw):
            if "senior engineer" in prompt:
                raise RuntimeError("unexpected crash")
            if "structural code reviewer" in prompt:
                return _stub_llm_response(
                    [{"file": "src/a.py", "line": 1, "severity": "P2",
                      "description": "qodo-finding"}], self._EXCERPTS)
            return _stub_llm_response(
                [{"file": "src/a.py", "line": 3, "severity": "P2",
                  "description": "adv-finding"}], self._EXCERPTS)

        resolved = _make_resolved_with_diff(_TWO_FILE_DIFF)
        breaker = MagicMock()

        with patch("code_forge.llm_invoke.llm_invoke",
                   side_effect=mock_invoke):
            provider = build_l1_provider(
                "auto", resolved, backend=self._api_backend(),
                breaker=breaker)
            findings, _, _, _ = provider()

        infra = [f for f in findings if f.source == "INFRA"]
        assert len(infra) == 1
        assert "expert" in infra[0].id
        assert "RuntimeError" in infra[0].description
        l1 = [f for f in findings if f.source == "L1"]
        assert len(l1) == 2
        breaker.record_other_error.assert_called_once()

    def test_per_coroutine_timeout_sampling(self):
        """Per-coroutine timeout produces INFRA finding, others survive."""
        import asyncio
        from code_forge.factories import build_sampling_l1_provider
        from code_forge.llm_invoke import LLMResult, Usage
        from unittest.mock import patch, MagicMock
        import concurrent.futures

        def _good(desc):
            return LLMResult(
                content={"findings": [
                    {"file": "src/a.py", "line": 1, "severity": "P2",
                     "description": desc}],
                    "code_excerpts": self._EXCERPTS},
                usage=Usage(0, 0), duration_s=0.1, is_truncated=False)

        # Simulate: pass 0 OK, pass 1 timed out via wait_for, pass 2 OK
        future = concurrent.futures.Future()
        future.set_result([
            _good("qodo-f"),
            asyncio.TimeoutError("per-coroutine timeout"),
            _good("adv-f")])

        resolved = _make_resolved_with_diff(_TWO_FILE_DIFF)
        with patch("code_forge.llm_invoke.invoke_sampling",
                   new_callable=MagicMock), \
             patch("asyncio.run_coroutine_threadsafe",
                   return_value=future):
            provider = build_sampling_l1_provider(
                MagicMock(), MagicMock(), resolved)
            findings, _, _, _ = provider()

        infra = [f for f in findings if f.source == "INFRA"]
        assert len(infra) == 1
        assert "expert" in infra[0].id
        l1 = [f for f in findings if f.source == "L1"]
        assert len(l1) == 2

    def test_failure_isolation_sampling(self):
        """Direction 3 (sampling): one exception, others still produce."""
        from code_forge.factories import build_sampling_l1_provider
        from code_forge.llm_invoke import LLMResult, Usage
        from unittest.mock import patch, MagicMock
        import concurrent.futures

        def _good(desc):
            return LLMResult(
                content={"findings": [
                    {"file": "src/a.py", "line": 1, "severity": "P2",
                     "description": desc}],
                    "code_excerpts": self._EXCERPTS},
                usage=Usage(0, 0), duration_s=0.1, is_truncated=False)

        future = concurrent.futures.Future()
        future.set_result([
            _good("qodo-f"), RuntimeError("boom"), _good("adv-f")])

        resolved = _make_resolved_with_diff(_TWO_FILE_DIFF)
        with patch("code_forge.llm_invoke.invoke_sampling",
                   new_callable=MagicMock), \
             patch("asyncio.run_coroutine_threadsafe",
                   return_value=future):
            provider = build_sampling_l1_provider(
                MagicMock(), MagicMock(), resolved)
            findings, _, _, _ = provider()

        infra = [f for f in findings if f.source == "INFRA"]
        assert len(infra) == 1
        assert "expert" in infra[0].id
        l1 = [f for f in findings if f.source == "L1"]
        assert len(l1) == 2


class TestDurationWallClock:
    """38.1-6: parallel paths report wall-clock, not sum of durations."""

    def test_parallel_duration_is_wall_clock(self):
        """ThreadPoolExecutor path: duration is wall-clock, not sum."""
        from code_forge.factories import build_l1_provider
        from code_forge.llm_invoke import LLMResult, Usage as LLMUsage

        resolved = _make_resolved_with_diff(_TWO_FILE_DIFF)
        backend = SimpleNamespace(type="api", name="test")

        good_resp = LLMResult(
            content=_stub_llm_response([], [
                {"file": "src/a.py", "start_line": 1,
                 "end_line": 4, "content": "line1\nadded\nline2"},
            ]).content,
            usage=LLMUsage(input_tokens=10, output_tokens=10),
            duration_s=0.1,
        )

        # Mock time.monotonic: two calls per parallel path
        # (_t0 and _parallel_wall). Returns 100.0 then 100.05,
        # so _parallel_wall = 0.05.
        clock = iter([100.0, 100.05])
        with patch("code_forge.llm_invoke.llm_invoke",
                    return_value=good_resp), \
             patch("code_forge.factories.time.monotonic",
                    side_effect=clock), \
             patch("code_forge.factories.progress.emit"):
            provider = build_l1_provider("real", resolved,
                                         backend=backend)
            _, _, _, duration = provider()

        # Wall-clock override: 0.05s, NOT 0.3s (sum of 3x0.1s).
        assert duration == pytest.approx(0.05, abs=0.001), (
            "parallel duration should be wall-clock; got %.3f"
            % duration
        )

    def test_serial_duration_is_sum(self):
        """CLI backend (serial): duration is sum of individual passes."""
        import time as _time
        from code_forge.factories import build_l1_provider
        from code_forge.llm_invoke import LLMResult, Usage as LLMUsage

        resolved = _make_resolved_with_diff(_TWO_FILE_DIFF)
        backend = SimpleNamespace(type="cli", name="cli")

        def _slow_invoke(*a, **kw):
            _time.sleep(0.1)
            return LLMResult(
                content=_stub_llm_response([], [
                    {"file": "src/a.py", "start_line": 1,
                     "end_line": 4, "content": "line1\nadded\nline2"},
                ]).content,
                usage=LLMUsage(input_tokens=10, output_tokens=10),
                duration_s=0.1,
            )

        with patch("code_forge.llm_invoke.llm_invoke",
                    side_effect=_slow_invoke):
            provider = build_l1_provider("real", resolved,
                                         backend=backend)
            _, _, _, duration = provider()

        # 3 passes * 0.1s = ~0.3s sum
        assert duration > 0.28, (
            "serial duration should be sum; got %.3f" % duration
        )

    def test_sampling_parallel_duration_is_wall_clock(self):
        """Sampling asyncio.gather path: wall-clock, not sum."""
        from code_forge.factories import build_sampling_l1_provider
        from code_forge.llm_invoke import LLMResult, Usage as LLMUsage
        from unittest.mock import MagicMock
        import concurrent.futures

        resolved = _make_resolved_with_diff(_TWO_FILE_DIFF)

        good_resp = LLMResult(
            content={"findings": [], "code_excerpts": [
                {"file": "src/a.py", "start_line": 1,
                 "end_line": 4, "content": "line1\nadded\nline2"},
            ]},
            usage=LLMUsage(0, 0),
            duration_s=0.1,
        )

        future = concurrent.futures.Future()
        future.set_result([good_resp, good_resp, good_resp])

        # Mock time.monotonic: two calls per sampling path.
        clock = iter([100.0, 100.05])
        with patch("code_forge.llm_invoke.invoke_sampling",
                   new_callable=MagicMock), \
             patch("asyncio.run_coroutine_threadsafe",
                   return_value=future), \
             patch("code_forge.factories.time.monotonic",
                    side_effect=clock):
            provider = build_sampling_l1_provider(
                MagicMock(), MagicMock(), resolved)
            _, _, _, duration = provider()

        # Wall-clock override: 0.05s, NOT 0.3s (sum of 3x0.1s).
        assert duration == pytest.approx(0.05, abs=0.001), (
            "sampling duration should be wall-clock; got %.3f"
            % duration
        )

    def test_failed_pass_duration_counted(self):
        """LLMInvokeError duration_s is accumulated (serial path)."""
        from code_forge.factories import build_l1_provider
        from code_forge.llm_invoke import (
            LLMInvokeError, LLMResult, Usage as LLMUsage,
        )

        resolved = _make_resolved_with_diff(_TWO_FILE_DIFF)
        backend = SimpleNamespace(type="cli", name="cli")

        call_count = [0]
        def _first_fails(*a, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                raise LLMInvokeError(
                    "timeout", is_timeout=True,
                    retryable=False, duration_s=0.15,
                )
            return LLMResult(
                content=_stub_llm_response([], [
                    {"file": "src/a.py", "start_line": 1,
                     "end_line": 4, "content": "line1\nadded\nline2"},
                ]).content,
                usage=LLMUsage(input_tokens=10, output_tokens=10),
                duration_s=0.1,
            )

        with patch("code_forge.llm_invoke.llm_invoke",
                    side_effect=_first_fails):
            provider = build_l1_provider("real", resolved,
                                         backend=backend)
            _, _, _, duration = provider()

        # Failed pass contributes 0.15s, successful passes 0.1s each
        # (2 passes). Total = 0.15 + 0.1 + 0.1 = 0.35s.
        # Without the accumulation line, total = 0.2s (only successes).
        assert duration >= 0.30, (
            "failed pass duration should be counted; got %.3f"
            % duration
        )


class TestPassTokenLine:
    """The per-pass progress line must carry the cached count.

    A caching backend reports input_tokens as the uncached delta only;
    without the cached count on the line, a full cache hit (input ~30)
    reads as a near-empty prompt in the log.
    """

    def test_cached_present_when_positive(self):
        from code_forge.factories import _pass_token_line
        from code_forge.llm_invoke import Usage
        line = _pass_token_line("mimo-pro", "qodo",
                                Usage(31, 16, 6720))
        assert line == "[mimo-pro:qodo] 31 in / 16 out tokens (6720 cached)\n"

    def test_cached_absent_when_zero(self):
        from code_forge.factories import _pass_token_line
        from code_forge.llm_invoke import Usage
        line = _pass_token_line("deepseek", "expert",
                                Usage(16974, 8605, 0))
        assert line == "[deepseek:expert] 16974 in / 8605 out tokens\n"


ROLE_NAMES = (
    "structural code reviewer: correctness and logic errors",
    "senior engineer: SOLID, architecture, security",
    "adversarial QE: assume bugs exist",
)


class TestSharedPromptPrefix:
    """The three passes must share a byte-identical leading prefix.

    Each pass differs from its siblings by one role sentence out of a prompt
    that is otherwise identical -- measured at 99.98 percent shared bytes on a
    real 14-file diff. Backends cache on a common leading prefix, so putting
    the role sentence first breaks the cache at character zero and every pass
    pays full price for the same tokens.

    These tests pin the ordering property, not the wording: they read the
    prompts the provider actually builds and compare them to each other.
    """

    @staticmethod
    def _captured_prompts(resolved, **kwargs):
        """Run the provider against a stubbed backend, return the prompts."""
        from code_forge.factories import build_l1_provider

        seen = []

        def _capture(prompt, *a, **kw):
            seen.append(prompt)
            return _stub_llm_response(findings_json=[], excerpts_json=[])

        with patch("code_forge.llm_invoke.llm_invoke", side_effect=_capture):
            provider = build_l1_provider("real", resolved, **kwargs)
            provider()
        return seen

    def test_passes_share_a_leading_prefix(self):
        """The common prefix must cover nearly the whole prompt."""
        resolved = _make_resolved_with_diff(_TWO_FILE_DIFF)
        prompts = self._captured_prompts(
            resolved,
            post_image="## src/a.py\nline1\nadded\nline2\n" * 40,
            conventions_digest="snake_case for functions\n" * 10,
        )
        assert len(prompts) == 3

        common = os.path.commonprefix(prompts)
        shortest = min(len(p) for p in prompts)
        # The role sentences are the only intended difference, so the shared
        # prefix should be nearly everything. A prefix under half the prompt
        # means something non-shared was emitted early.
        assert len(common) > shortest * 0.9, (
            "shared prefix is %d chars of a %d char prompt; the passes are "
            "diverging early and no backend can cache across them"
            % (len(common), shortest)
        )

    def test_role_sentence_is_not_at_the_start(self):
        """Nothing pass-specific may be emitted before the shared body."""
        resolved = _make_resolved_with_diff(_TWO_FILE_DIFF)
        prompts = self._captured_prompts(resolved)

        common = os.path.commonprefix(prompts)
        # The role NAMES are what differ; each must fall past the shared
        # prefix. ("You are a " itself is common to all three and may sit
        # inside the prefix -- only the role that follows it is per-pass.)
        for prompt, role in zip(prompts, ROLE_NAMES):
            assert role not in common, (
                "role %r appears in the shared prefix" % role
            )
            assert role in prompt[len(common):], (
                "role %r is not in this pass's divergent tail" % role
            )
        tails = [p[len(common):] for p in prompts]
        assert all(t.strip() for t in tails), "a pass has an empty tail"
        assert len(set(tails)) == 3, "the three tails are not distinct"

    def test_diff_body_is_inside_the_shared_prefix(self):
        """The expensive part -- the diff -- must be shared, not repeated late."""
        resolved = _make_resolved_with_diff(_TWO_FILE_DIFF)
        prompts = self._captured_prompts(resolved)

        common = os.path.commonprefix(prompts)
        assert "src/a.py" in common, (
            "the diff body falls outside the shared prefix, so each pass "
            "re-sends it uncached"
        )


class TestSamplingSharedPromptPrefix:
    """Same prefix contract, on the MCP sampling provider.

    This path builds its own prompts (build_sampling_l1_provider) and a test
    that only exercises the subprocess provider stays green with this one
    reverted -- verified by injecting the old ordering here alone.
    """

    @staticmethod
    def _captured_prompts(resolved):
        import asyncio
        import threading
        from code_forge.factories import build_sampling_l1_provider
        from code_forge.llm_invoke import LLMResult, Usage as LLMUsage

        seen = []

        async def _fake_sampling(session, prompt, **kw):
            seen.append(prompt)
            return LLMResult(
                content='{"findings": [], "code_excerpts": []}',
                usage=LLMUsage(input_tokens=10, output_tokens=10),
                duration_s=0.1,
            )

        loop = asyncio.new_event_loop()
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()
        try:
            with patch("code_forge.llm_invoke.invoke_sampling",
                       side_effect=_fake_sampling):
                provider = build_sampling_l1_provider(
                    session=SimpleNamespace(), loop=loop, resolved=resolved,
                )
                provider()
        finally:
            loop.call_soon_threadsafe(loop.stop)
            t.join(timeout=5)
        return seen

    def test_sampling_passes_share_a_leading_prefix(self):
        resolved = _make_resolved_with_diff(_TWO_FILE_DIFF)
        prompts = self._captured_prompts(resolved)
        assert len(prompts) == 3

        common = os.path.commonprefix(prompts)
        shortest = min(len(p) for p in prompts)
        assert len(common) > shortest * 0.9, (
            "sampling: shared prefix is %d chars of a %d char prompt"
            % (len(common), shortest)
        )
        assert "src/a.py" in common, (
            "sampling: the diff body falls outside the shared prefix"
        )


class TestSplitContextInPrompt:
    """build_l1_provider carries a split-context block for grouped review."""

    def test_split_context_lands_in_shared_body(self):
        from code_forge.factories import build_l1_provider

        seen = []

        def _capture(prompt, *a, **kw):
            seen.append(prompt)
            return _stub_llm_response(findings_json=[], excerpts_json=[
                {"file": "src/a.py", "start_line": 1,
                 "end_line": 4, "content": "line1\nadded\nline2"},
            ])

        resolved = _make_resolved_with_diff(_ONE_FILE_DIFF)
        with patch("code_forge.llm_invoke.llm_invoke", side_effect=_capture):
            build_l1_provider(
                "real", resolved,
                split_context="cli.py (integration) uses RulepackRunner",
            )()
        assert seen
        for prompt in seen:
            assert "## Split Review Context" in prompt
            assert "cli.py (integration) uses RulepackRunner" in prompt

    def test_empty_split_context_adds_nothing(self):
        from code_forge.factories import build_l1_provider

        seen = []

        def _capture(prompt, *a, **kw):
            seen.append(prompt)
            return _stub_llm_response(findings_json=[], excerpts_json=[
                {"file": "src/a.py", "start_line": 1,
                 "end_line": 4, "content": "line1\nadded\nline2"},
            ])

        resolved = _make_resolved_with_diff(_ONE_FILE_DIFF)
        with patch("code_forge.llm_invoke.llm_invoke", side_effect=_capture):
            build_l1_provider("real", resolved)()
        assert seen
        for prompt in seen:
            assert "## Split Review Context" not in prompt


class TestGroupedL1Provider:
    """The composite provider fans one review out over group providers.

    machine.py must not change: the composite returns one merged result, so
    cycle semantics are untouched. Groups run sequentially -- CLI backends
    share a module-global _active_proc and parallel groups would corrupt it.
    """

    @staticmethod
    def _spec(name, diff_text, split_context=""):
        return {
            "name": name,
            "resolved": _make_resolved_with_diff(diff_text),
            "post_image": "",
            "conventions_digest": "",
            "split_context": split_context,
        }

    def _run(self, specs, reply_per_call):
        from code_forge.factories import build_grouped_l1_provider

        prompts = []

        def _capture(prompt, *a, **kw):
            prompts.append(prompt)
            return reply_per_call(len(prompts) - 1)

        with patch("code_forge.llm_invoke.llm_invoke", side_effect=_capture):
            provider = build_grouped_l1_provider("real", specs)
            result = provider()
        return result, prompts

    _B_ONLY_DIFF = (
        "diff --git a/src/b.py b/src/b.py\n"
        "--- a/src/b.py\n"
        "+++ b/src/b.py\n"
        "@@ -1 +1,2 @@\n"
        " keep\n"
        "+new\n"
    )

    def test_each_group_gets_three_passes_with_its_own_diff(self):
        def reply(i):
            return _stub_llm_response(findings_json=[{
                "file": "src/a.py", "line": 2,
                "description": "finding-%d" % i, "severity": "P3",
            }], excerpts_json=[])

        specs = [
            self._spec("engine:a.py", _ONE_FILE_DIFF, "ctx-A"),
            self._spec("covered:b.py", self._B_ONLY_DIFF, "ctx-B"),
        ]
        (findings, _ex, usage, _dur), prompts = self._run(specs, reply)
        assert len(prompts) == 6, "2 groups x 3 passes"
        a_prompts = [p for p in prompts[:3]]
        b_prompts = [p for p in prompts[3:]]
        assert all("ctx-A" in p and "ctx-B" not in p for p in a_prompts)
        assert all("ctx-B" in p and "ctx-A" not in p for p in b_prompts)
        # group A's diff section never leaks into group B's prompt
        assert all("src/a.py" in p for p in a_prompts)
        assert all("src/a.py" not in p for p in b_prompts)
        assert len(findings) == 6  # distinct descriptions -> distinct fps

    def test_identical_finding_across_groups_dedupes(self):
        same = [{
            "file": "src/a.py", "line": 2,
            "description": "same defect seen twice", "severity": "P2",
        }]

        def reply(i):
            return _stub_llm_response(findings_json=same, excerpts_json=[])

        specs = [
            self._spec("g1", _ONE_FILE_DIFF),
            self._spec("g2", _ONE_FILE_DIFF),
        ]
        (findings, _ex, _u, _d), _ = self._run(specs, reply)
        matches = [f for f in findings if "same defect seen twice" in f.description]
        assert len(matches) == 1, "boundary finding must be reported once"

    def test_usage_sums_across_groups(self):
        def reply(i):
            return _stub_llm_response(findings_json=[{
                "file": "src/a.py", "line": 2,
                "description": "f%d" % i, "severity": "P3",
            }], excerpts_json=[])

        specs = [self._spec("g1", _ONE_FILE_DIFF),
                 self._spec("g2", _TWO_FILE_DIFF)]
        (_f, _e, usage, _d), _ = self._run(specs, reply)
        # _stub_llm_response returns Usage(10, 10) per call, 6 calls total
        assert usage.input_tokens == 60
        assert usage.output_tokens == 60
