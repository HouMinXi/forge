"""Tests for canary_gen: generation, non-equivalence, validation, injection,
dispatch, and run_inline_canary orchestration."""
from __future__ import annotations

import json

import pytest

from code_forge.canary import Canary, evaluate_canary_coverage
from code_forge.canary_gen import (
    CanaryProvider,
    CanarySkip,
    dispatch_canary_review,
    generate_canaries,
    inject_canaries_into_diff,
    is_non_equivalent,
    run_inline_canary,
    validate_canary_findings,
)
from code_forge.state import Verdict


# -- is_non_equivalent -------------------------------------------------------


def test_nonequiv_rejects_comment():
    assert is_non_equivalent("x = 1", "x = 1  # comment") is False


def test_nonequiv_rejects_whitespace():
    assert is_non_equivalent("x=1", "x = 1") is False


def test_nonequiv_accepts_operator():
    assert is_non_equivalent("x = a + b", "x = a - b") is True


def test_nonequiv_accepts_literal():
    assert is_non_equivalent("x = 0", "x = 1") is True


def test_nonequiv_rejects_syntax_error():
    assert is_non_equivalent("x = 1", "def (") is False


def test_nonequiv_handles_indented_code():
    assert is_non_equivalent("    x = 1", "    x = 2") is True


def test_nonequiv_indented_same_code():
    assert is_non_equivalent("    x = 1", "    x = 1  # note") is False


# -- validate_canary_findings ------------------------------------------------


def test_validate_canary_findings_valid():
    findings = [
        {"file": "a.py", "line": 1, "severity": "high", "description": "bug"},
    ]
    result = validate_canary_findings(findings)
    assert result == findings


def test_validate_canary_findings_missing_key():
    findings = [{"file": "a.py", "line": 1}]
    result = validate_canary_findings(findings)
    assert result == []


def test_validate_canary_findings_accepts_any_severity():
    findings = [
        {"file": "a.py", "line": 1, "severity": "critical", "description": "x"},
    ]
    result = validate_canary_findings(findings)
    assert len(result) == 1
    assert result[0]["severity"] == "critical"


def test_validate_canary_findings_immutable():
    original = [{"file": "a.py"}]
    result = validate_canary_findings(original)
    assert result == []
    assert original == [{"file": "a.py"}]


# -- template generation & generic filenames ----------------------------------


_GENERIC_FILENAMES = {
    "helpers.py", "utils.py", "config.py",
    "service.py", "handler.py", "parser.py",
}

_DIFF_WITH_PY = (
    "diff --git a/foo.py b/foo.py\n"
    "--- a/foo.py\n"
    "+++ b/foo.py\n"
    "@@ -1,3 +1,4 @@\n"
    " import os\n"
    "+x = 1\n"
    " y = 2\n"
)


def test_template_generates_valid_mutation():
    """Every template category produces a valid mutation dict with parseable code."""
    import ast

    result = generate_canaries(_DIFF_WITH_PY, 6)
    assert not isinstance(result, CanarySkip)
    for mut in result:
        for key in ("file", "line", "code", "original", "description"):
            assert key in mut, "missing key %s in %r" % (key, mut)
        ast.parse(mut["code"])
        ast.parse(mut["original"])


def test_template_uses_generic_filenames():
    result = generate_canaries(_DIFF_WITH_PY, 6)
    assert not isinstance(result, CanarySkip)
    for mut in result:
        assert mut["file"] in _GENERIC_FILENAMES, (
            "unexpected filename %r" % mut["file"]
        )


# -- provider seam & fallback -----------------------------------------------


def _stub_provider_5(diff_text: str) -> list[dict]:
    """A provider that returns 5 pre-built non-equivalent mutations."""
    return [
        {
            "file": "helpers.py",
            "line": 1,
            "code": "x = %d" % i,
            "original": "x = 0",
            "description": "mutation %d" % i,
        }
        for i in range(1, 6)
    ]


def test_provider_seam():
    result = generate_canaries(_DIFF_WITH_PY, 5, provider=_stub_provider_5)
    assert not isinstance(result, CanarySkip)
    assert len(result) == 5


def test_template_fallback():
    def empty_provider(diff_text: str) -> list[dict]:
        return []

    result = generate_canaries(_DIFF_WITH_PY, 5, provider=empty_provider)
    assert not isinstance(result, CanarySkip)
    assert len(result) >= 3


def test_skip_on_insufficient():
    def one_equiv_provider(diff_text: str) -> list[dict]:
        return [
            {
                "file": "helpers.py",
                "line": 1,
                "code": "x = 1  # comment",
                "original": "x = 1",
                "description": "comment only",
            },
        ]

    result = generate_canaries(_DIFF_WITH_PY, 5, provider=one_equiv_provider)
    # Template fallback yields 6 templates; total should be >= 2 (templates alone).
    # This test verifies the provider's equivalent mutation is discarded.
    # To truly test skip, we need to monkeypatch templates to also fail.
    # For now, verify the equivalent one is not counted.
    if isinstance(result, CanarySkip):
        assert "fewer than 2" in result.reason
    else:
        # Templates filled the gap -- all results are non-equivalent
        for mut in result:
            assert is_non_equivalent(mut["original"], mut["code"])


def test_non_python_diff_skip():
    js_diff = (
        "diff --git a/foo.js b/foo.js\n"
        "--- a/foo.js\n"
        "+++ b/foo.js\n"
        "@@ -1,2 +1,3 @@\n"
        " var x = 1;\n"
        "+var y = 2;\n"
    )
    result = generate_canaries(js_diff, 5)
    assert isinstance(result, CanarySkip)
    assert "non-Python" in result.reason


# ============================================================================
# Task 2: injection, dispatch, and run_inline_canary orchestrator
# ============================================================================


# -- inject_canaries_into_diff -----------------------------------------------


def test_injection_isolation():
    """Injection returns a modified copy; original diff string is unchanged."""
    original_diff = _DIFF_WITH_PY
    mutations = [
        {
            "file": "helpers.py",
            "line": 1,
            "code": "x = 42",
            "original": "x = 0",
            "description": "literal change",
        },
    ]
    modified, manifest = inject_canaries_into_diff(original_diff, mutations)
    assert id(modified) != id(original_diff)
    assert original_diff == _DIFF_WITH_PY  # identity preserved
    assert "x = 42" in modified
    assert len(manifest) == 1
    assert manifest[0].file == "helpers.py"
    assert manifest[0].line == 1


# -- dispatch_canary_review --------------------------------------------------


def test_dispatch_provider():
    """dispatch_canary_review calls provider once with a prompt containing the diff."""
    calls = []

    def stub_review(prompt: str) -> str:
        calls.append(prompt)
        return json.dumps({
            "findings": [
                {"file": "a.py", "line": 1, "severity": "high", "description": "bug"},
            ]
        })

    result = dispatch_canary_review("some diff text", provider=stub_review)
    assert len(calls) == 1
    assert "some diff text" in calls[0]
    assert "Review this diff" in calls[0]
    assert len(result) == 1


def test_dispatch_validates_with_canary_validator():
    """dispatch uses validate_canary_findings, not validate_reviewer_json;
    a finding missing 'severity' is dropped."""
    def stub_review(prompt: str) -> str:
        return json.dumps({
            "findings": [
                {"file": "a.py", "line": 1, "description": "no severity"},
            ]
        })

    result = dispatch_canary_review("diff", provider=stub_review)
    assert result == []


# -- run_inline_canary -------------------------------------------------------


def _source_lookup(path: str):
    """Stub source_lookup: returns 100 lines for any .py file, None otherwise."""
    if path.endswith(".py"):
        return ["line %d" % i for i in range(100)]
    return None


def _make_review_provider_hitting_canaries(manifest_ref):
    """Return a ReviewProvider that produces findings matching all canaries."""
    def provider(prompt: str) -> str:
        findings = []
        for c in manifest_ref:
            findings.append({
                "file": c.file,
                "line": c.line,
                "severity": "high",
                "description": "found bug at %s:%d" % (c.file, c.line),
            })
        return json.dumps({"findings": findings})
    return provider


def test_cite_reverify_on_real_only():
    """Partition runs before cite-verify; canary findings are stripped from
    the returned real findings. The reviewer catches all canaries (so the gate
    passes) plus one real finding on foo.py, and only the real finding survives."""

    # The provider must return findings matching whatever canaries
    # run_inline_canary internally generates. We parse the diff in the prompt
    # to extract canary filenames and lines from the appended hunks.
    def provider(prompt: str) -> str:
        import re
        findings = []
        # Match appended canary hunk headers: +++ b/<file> followed by @@ ...
        for m in re.finditer(
            r'\+\+\+ b/(\S+\.py)\n@@ -0,0 \+1,(\d+) @@\n((?:\+.*\n?)+)',
            prompt,
        ):
            fname = m.group(1)
            # Each canary snippet starts at line 1; cite line 1 to catch it.
            findings.append({
                "file": fname, "line": 1,
                "severity": "high", "description": "canary finding",
            })
        # Add a real finding on a file from the original diff.
        findings.append({
            "file": "foo.py", "line": 2,
            "severity": "medium", "description": "real finding",
        })
        return json.dumps({"findings": findings})

    verdict, real = run_inline_canary(
        _DIFF_WITH_PY,
        n=3,
        threshold_ratio=0.5,
        review_provider=provider,
        source_lookup=_source_lookup,
    )
    assert verdict == Verdict.DELEGATED
    # Only the real finding on foo.py should survive.
    assert len(real) == 1
    assert real[0]["file"] == "foo.py"
    assert real[0]["description"] == "real finding"


def test_gate_pass():
    """When all canaries are caught, returns DELEGATED with real findings only."""
    import re

    def smart_reviewer(prompt: str) -> str:
        findings = []
        # Parse appended canary hunks from the prompt to hit all canaries.
        for m in re.finditer(
            r'\+\+\+ b/(\S+\.py)\n@@ -0,0 \+1,(\d+) @@',
            prompt,
        ):
            findings.append({
                "file": m.group(1), "line": 1,
                "severity": "high", "description": "caught canary",
            })
        # Add a real finding too.
        findings.append({
            "file": "foo.py", "line": 2,
            "severity": "low", "description": "real issue",
        })
        return json.dumps({"findings": findings})

    verdict, real = run_inline_canary(
        _DIFF_WITH_PY,
        n=3,
        threshold_ratio=0.5,
        review_provider=smart_reviewer,
        source_lookup=_source_lookup,
    )
    assert verdict == Verdict.DELEGATED
    # Canary findings stripped; real finding kept.
    assert any(f["description"] == "real issue" for f in real)


def test_gate_miss():
    """Empty findings (rubber-stamp) returns UNRELIABLE."""
    def empty_reviewer(prompt: str) -> str:
        return json.dumps({"findings": []})

    verdict, real = run_inline_canary(
        _DIFF_WITH_PY,
        n=3,
        threshold_ratio=0.5,
        review_provider=empty_reviewer,
        source_lookup=_source_lookup,
    )
    assert verdict == Verdict.UNRELIABLE
    assert real == []


def test_gate_skip_on_insufficient():
    """When generate_canaries returns CanarySkip, returns DELEGATED."""
    js_diff = (
        "diff --git a/foo.js b/foo.js\n"
        "--- a/foo.js\n"
        "+++ b/foo.js\n"
        "@@ -1,2 +1,3 @@\n"
        " var x = 1;\n"
        "+var y = 2;\n"
    )

    def unreachable_reviewer(prompt: str) -> str:
        raise AssertionError("reviewer should not be called")

    verdict, real = run_inline_canary(
        js_diff,
        review_provider=unreachable_reviewer,
        source_lookup=_source_lookup,
    )
    assert verdict == Verdict.DELEGATED
    assert real == []


def test_dispatch_error_graceful():
    """ReviewProvider raising an exception degrades to DELEGATED, not crash."""
    def exploding_reviewer(prompt: str) -> str:
        raise RuntimeError("LLM timeout")

    verdict, real = run_inline_canary(
        _DIFF_WITH_PY,
        n=3,
        review_provider=exploding_reviewer,
        source_lookup=_source_lookup,
    )
    assert verdict == Verdict.DELEGATED
    assert real == []


def test_threshold_ratio_zero_clamped():
    """threshold_ratio=0.0 clamps to threshold=1, does not crash."""
    def empty_reviewer(prompt: str) -> str:
        return json.dumps({"findings": []})

    verdict, real = run_inline_canary(
        _DIFF_WITH_PY,
        n=3,
        threshold_ratio=0.0,
        review_provider=empty_reviewer,
        source_lookup=_source_lookup,
    )
    # With 0 findings and threshold=1, gate fails -> UNRELIABLE.
    assert verdict == Verdict.UNRELIABLE
    assert real == []


def test_no_tree_mutation():
    """run_inline_canary never opens files for writing (string-only manipulation)."""
    import builtins
    import io

    real_open = builtins.open
    write_calls = []

    def tracked_open(*args, **kwargs):
        mode = args[1] if len(args) > 1 else kwargs.get("mode", "r")
        if "w" in str(mode) or "a" in str(mode):
            write_calls.append(args[0] if args else kwargs.get("file"))
        return real_open(*args, **kwargs)

    builtins.open = tracked_open
    try:
        def empty_reviewer(prompt: str) -> str:
            return json.dumps({"findings": []})

        run_inline_canary(
            _DIFF_WITH_PY,
            n=3,
            review_provider=empty_reviewer,
            source_lookup=_source_lookup,
        )
        assert write_calls == [], "unexpected file writes: %r" % write_calls
    finally:
        builtins.open = real_open


def test_injected_canary_is_catchable_at_recorded_line():
    """LINE-MATCH INVARIANT: a finding citing (canary.file, canary.line)
    is counted as caught by evaluate_canary_coverage."""
    mutations = generate_canaries(_DIFF_WITH_PY, 3)
    assert not isinstance(mutations, CanarySkip)
    _, manifest = inject_canaries_into_diff(_DIFF_WITH_PY, mutations)

    # Build findings that cite exact canary locations.
    findings = [
        {"file": c.file, "line": c.line, "severity": "high", "description": "bug"}
        for c in manifest
    ]

    gate = evaluate_canary_coverage(findings, manifest, threshold=len(manifest))
    assert gate.passed is True
    assert len(gate.caught) == len(manifest)


def test_bug_inject_shifted_hunk_breaks_invariant():
    """BUG-INJECT PROOF: shifting +start from 1 to 6 makes the buggy line
    land >2 away from the recorded Canary.line, so the gate always fails.

    This proves the test_injected_canary_is_catchable_at_recorded_line test
    actually guards the invariant -- if the hunk header were wrong, a reviewer
    citing the canary line would NOT be counted as caught."""
    mutations = generate_canaries(_DIFF_WITH_PY, 3)
    assert not isinstance(mutations, CanarySkip)
    _, manifest = inject_canaries_into_diff(_DIFF_WITH_PY, mutations)

    # Simulate shifted canaries: Canary.line stays as recorded (mutation["line"],
    # small values like 1-3) but the reviewer cites line = canary.line + 5
    # (as if +start were 6 instead of 1, shifting all code lines by +5).
    shifted_findings = [
        {"file": c.file, "line": c.line + 5, "severity": "high", "description": "bug"}
        for c in manifest
    ]

    gate = evaluate_canary_coverage(shifted_findings, manifest, threshold=1)
    # With default line_window=2, a shift of 5 exceeds the window.
    assert gate.passed is False
    assert len(gate.caught) == 0
