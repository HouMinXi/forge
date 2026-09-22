"""Frozen-source excerpt assessment through the real receipt boundary."""

import copy
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from code_forge.receipt import write_receipts
from code_forge.verify import (
    _diff_validation_context,
    parse_diff_files,
    run_verify,
    validate_excerpts_against_diff,
)


def _diff(lines):
    return (
        "diff --git a/mod.py b/mod.py\n--- a/mod.py\n+++ b/mod.py\n"
        f"@@ -1 +1,{len(lines)} @@\n-placeholder\n"
        + "".join(f"+{line}\n" for line in lines)
    )


def _exc(start, end, content):
    return {"file": "mod.py", "start_line": start, "end_line": end, "content": content}


def _assess(diff, exc, cwd=None):
    from code_forge.verify import assess_excerpt_evidence

    post, hunks, exempt = _diff_validation_context(diff, cwd=cwd)
    return assess_excerpt_evidence(exc, hunks, post, exempt)


def _verify(tmp_path, diff, excerpts):
    digest = hashlib.sha256(diff.encode()).hexdigest()
    receipts = tmp_path / ".code-forge" / "receipts"
    receipts.mkdir(parents=True)
    (receipts.parent / "gate.yaml").write_text("verify:\n  required_cycles: 1\n")
    expanded = [dict(exc, pass_name=name) for name in ("qodo", "expert", "adversarial")
                for exc in excerpts]
    write_receipts(receipts, 0, [], digest, [Path("mod.py")], tmp_path,
                   diff_files=parse_diff_files(diff), diff_text=diff,
                   reviewer_excerpts=expanded)
    result = run_verify(tmp_path, digest, parse_diff_files(diff), diff_text=diff,
                        required_cycles=1)
    return result, [json.loads(p.read_text()) for p in sorted(receipts.glob("receipt-*.json"))]


@pytest.mark.parametrize("tail", [")", "# keep this comment", "return answer"])
def test_source_proven_single_tail_survives_real_receipt_writer(tmp_path, tail):
    diff = _diff(["alpha = 1", "beta = 2", tail])
    exc = _exc(1, 3, "alpha = 1\nbeta = 2")
    assert validate_excerpts_against_diff(diff, [exc]) == []
    result, receipts = _verify(tmp_path, diff, [exc])
    assert result.passed, result.reason
    assert len(receipts) == 3
    for receipt in receipts:
        actual = receipt["code_excerpts"][0]
        assert (actual["start_line"], actual["end_line"], actual["content"]) == (1, 3, exc["content"])


@pytest.mark.parametrize("delta", [-2, 2])
def test_constant_offset_is_audited_but_not_a_hard_error(tmp_path, delta):
    lines = [f"v{i} = {i}" for i in range(1, 9)]
    diff = _diff(lines)
    exc = _exc(3 - delta, 8 - delta, "\n".join(lines[2:]))
    assert validate_excerpts_against_diff(diff, [exc]) == []
    result, receipts = _verify(tmp_path, diff, [exc])
    assert result.passed, result.reason
    assert receipts[0]["code_excerpts"][0]["start_line"] == 3 - delta


def test_shifted_claim_cannot_inflate_changed_line_coverage(tmp_path):
    diff = _diff(["context", "value", "tail"])
    exc = _exc(2, 3, "context\nvalue")
    result, _ = _verify(tmp_path, diff, [exc])
    assert result.passed, result.reason
    assessment = _assess(diff, exc)
    assert assessment.status.value == "UNTRUSTED"
    assert (assessment.proven_start, assessment.proven_end) == (1, 2)
    assert assessment.proven_lines == frozenset({1, 2})


def _committed_diff(tmp_path, before, after, context=0):
    # Isolated repo identity/config; Git is the real source of immutable blobs.
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True, timeout=10)

    git("init", "-q")
    git("config", "user.name", "Receipt test")
    git("config", "user.email", "receipt-test@example.invalid")
    git("config", "commit.gpgsign", "false")
    (tmp_path / "mod.py").write_text(before)
    git("add", "mod.py")
    git("-c", "core.hooksPath=/dev/null", "commit", "-qm", "fixture")
    (tmp_path / "mod.py").write_text(after)
    git("add", "mod.py")
    return git("diff", "--cached", "--full-index", f"-U{context}")


def test_missing_tail_cannot_witness_the_only_changed_line(tmp_path):
    before = "alpha\nbeta\ngamma\ndelta\nepsilon\nold\n"
    after = before.replace("old", "changed")
    diff = _committed_diff(tmp_path, before, after)
    exc = _exc(1, 6, "alpha\nbeta\ngamma\ndelta\nepsilon")
    result, _ = _verify(tmp_path, diff, [exc])
    assert not result.passed
    assert "unwitnessed hunk" in result.reason or "outside every hunk" in result.reason
    assert not _assess(diff, exc, tmp_path).proven_lines


def test_shift_into_unchanged_context_cannot_witness_hunk(tmp_path):
    before = "alpha\nbeta\nold\nomega\n"
    after = before.replace("old", "changed")
    diff = _committed_diff(tmp_path, before, after)
    exc = _exc(2, 3, "alpha\nbeta")
    result, _ = _verify(tmp_path, diff, [exc])
    assert not result.passed
    assert "unwitnessed hunk" in result.reason or "outside every hunk" in result.reason


@pytest.mark.parametrize("exc", [
    _exc(1, 4, "alpha\nbeta"),
    _exc(1, 3, "alpha\nwrong"),
    _exc(1, 3, "alpha\ngamma"),
    _exc(1, 3, "alpha\nbeta\nfabricated"),
    _exc(1, 3, "gamma"),
])
def test_incomplete_or_changed_content_stays_invalid(exc):
    assert validate_excerpts_against_diff(_diff(["alpha", "beta", "gamma"]), [exc])


def test_unknown_tail_is_not_source_proven():
    diff = _diff(["alpha", "beta"])
    assert validate_excerpts_against_diff(diff, [_exc(1, 3, "alpha\nbeta")])


def test_assessment_is_immutable_and_keeps_blank_at_claimed_start():
    from dataclasses import FrozenInstanceError

    exc = _exc(1, 3, "\nalpha")
    original = copy.deepcopy(exc)
    result = _assess(_diff(["", "alpha", "omega"]), exc)
    assert result.status.value == "UNTRUSTED"
    assert (result.proven_start, result.proven_end) == (1, 2)
    assert exc == original
    with pytest.raises(FrozenInstanceError):
        result.diagnostic = "changed"


def test_missing_leading_blank_credits_only_carried_body():
    result = _assess(_diff(["", "alpha", "beta"]), _exc(1, 3, "alpha\nbeta"))
    assert result.status.value == "VALID"
    assert result.proven_lines == frozenset({2, 3})


def test_repeated_offset_prefers_nearest_but_is_still_untrusted():
    diff = _diff(["alpha", "beta", "x", "alpha", "beta", "y"])
    result = _assess(diff, _exc(3, 4, "alpha\nbeta"))
    assert result.status.value == "UNTRUSTED"
    assert result.proven_lines == frozenset({4, 5})


def test_offset_requires_every_line_and_two_lines():
    diff = _diff(["alpha", "beta", "gamma"])
    for exc in (_exc(2, 2, "alpha"), _exc(2, 4, "alpha\nbeta\nfake")):
        assert _assess(diff, exc).status.value == "INVALID"


@pytest.mark.parametrize("exc", [
    None,
    {"file": None, "start_line": 1, "end_line": 1, "content": "alpha"},
    _exc(True, 1, "alpha"),
    _exc(1, False, "alpha"),
    _exc("1", 1, "alpha"),
    _exc(0, 1, "alpha"),
    _exc(2, 1, "alpha"),
    _exc(1, 1, ["alpha", 2]),
    _exc(1, 1, 42),
    _exc(1, 1, "   "),
])
def test_model_controlled_shape_is_invalid_without_source_credit(exc):
    from code_forge.verify import ExcerptStatus, assess_excerpt_evidence

    assessment = assess_excerpt_evidence(exc)
    assert assessment.status is ExcerptStatus.INVALID
    assert assessment.diagnostic
    assert not assessment.proven_lines
    assert assessment.proven_start is None
    assert assessment.proven_end is None


def test_shape_only_and_hunk_only_validation_cannot_claim_source_credit():
    from code_forge.verify import ExcerptStatus, assess_excerpt_evidence

    exc = _exc(1, 2, ["alpha", "beta"])
    shape = assess_excerpt_evidence(exc)
    assert shape.status is ExcerptStatus.VALID
    assert shape.diagnostic is None
    assert not shape.proven_lines
    hunk_only = assess_excerpt_evidence(exc, {"mod.py": [{"start": 4, "end": 5}]})
    assert hunk_only.status is ExcerptStatus.INVALID
    assert not hunk_only.proven_lines


def test_production_predicate_does_not_classify_diagnostic_text(monkeypatch):
    import code_forge.verify as verify

    def forbidden(_error):
        pytest.fail("production acceptance parsed diagnostic text")

    monkeypatch.setattr(verify, "is_evidence_quality_fault", forbidden)
    exc = _exc(1, 3, "alpha\nbeta")
    assert validate_excerpts_against_diff(_diff(["alpha", "beta", "gamma"]), [exc]) == []


def test_proven_coverage_unions_distinct_quotes_in_one_cycle():
    from code_forge.verify import _cycle_excerpt_covered, _excerpt_covered

    diff = _diff(["alpha", "beta", "gamma"])
    first = _exc(1, 1, "alpha")
    second = _exc(2, 3, "beta\ngamma")
    other = _exc(3, 3, "gamma")
    assessments = {id(exc): _assess(diff, exc) for exc in (first, second, other)}
    receipts = [
        {"cycle": 1, "code_excerpts": [first]},
        {"cycle": 1, "code_excerpts": [second]},
        {"cycle": 2, "code_excerpts": [other]},
    ]
    assert _excerpt_covered({}, {}) == set()
    assert _cycle_excerpt_covered(receipts, 1, assessments) == {
        ("mod.py", 1), ("mod.py", 2), ("mod.py", 3),
    }
    assert _cycle_excerpt_covered(receipts, 2, assessments) == {("mod.py", 3)}
    assert _cycle_excerpt_covered(receipts, 3, assessments) == set()


@pytest.mark.parametrize("end", [True, "1", None, 1.0])
def test_noninteger_end_is_invalid_even_when_start_is_integer(end):
    from code_forge.verify import ExcerptStatus, assess_excerpt_evidence

    result = assess_excerpt_evidence(_exc(1, end, "alpha"))
    assert result.status is ExcerptStatus.INVALID
    assert result.diagnostic == "excerpt mod.py coordinates must be integers"
    assert result.proven_lines == frozenset()


@pytest.mark.parametrize("exc,diagnostic", [
    (None, "excerpt must be a dictionary"),
    ({"file": "", "start_line": 1, "end_line": 1, "content": "alpha"},
     "excerpt file must be a non-empty string"),
    ({"file": "mod.py", "start_line": 1, "end_line": 1},
     "excerpt mod.py:1 has empty content"),
    ({"start_line": 1, "end_line": 1, "content": "alpha"},
     "excerpt <unknown>:1 not in diff"),
])
def test_invalid_shape_has_actionable_diagnostic(exc, diagnostic):
    from code_forge.verify import ExcerptStatus

    result = _assess(_diff(["alpha"]), exc)
    assert result.status is ExcerptStatus.INVALID
    assert result.diagnostic == diagnostic
    assert result.proven_lines == frozenset()


@pytest.mark.parametrize("content", ["alpha\nbeta", ["alpha", "beta"]])
def test_list_and_string_quotes_prove_identical_lines(content):
    from code_forge.verify import ExcerptStatus

    result = _assess(_diff(["alpha", "beta"]), _exc(1, 2, content))
    assert result.status is ExcerptStatus.VALID
    assert result.diagnostic is None
    assert result.proven_lines == frozenset({1, 2})


@pytest.mark.parametrize("start,end,status,diagnostic", [
    (2, 2, "VALID", None),
    (1, 2, "INVALID", "excerpt mod.py:1-2 declares 2 lines but carries 1"),
    (3, 3, "INVALID", "excerpt mod.py:3-3 is outside every hunk"),
])
def test_hunk_only_validation_has_typed_result(start, end, status, diagnostic):
    from code_forge.verify import ExcerptStatus, assess_excerpt_evidence

    result = assess_excerpt_evidence(
        _exc(start, end, "alpha"), {"mod.py": [{"start": 2, "end": 2}]},
    )
    assert result.status is ExcerptStatus(status)
    assert result.diagnostic == diagnostic
    assert result.proven_lines == frozenset()


def test_exact_context_outside_hunk_remains_typed_invalid():
    from code_forge.verify import ExcerptStatus, assess_excerpt_evidence

    result = assess_excerpt_evidence(
        _exc(1, 2, "alpha\nbeta"), {"mod.py": [{"start": 3, "end": 3}]},
        {"mod.py": {1: "alpha", 2: "beta", 3: "changed"}},
    )
    assert result.status is ExcerptStatus.INVALID
    assert result.diagnostic == (
        "excerpt mod.py:1-2 is outside every hunk; unchanged context belongs in context_quotes"
    )
    assert result.proven_lines == frozenset()


@pytest.mark.parametrize("post,diagnostic", [
    ({}, "excerpt mod.py:1-1 claims line 1 outside the diff post-image; it cannot be verified"),
    ({"mod.py": {2: "beta"}},
     "excerpt mod.py:1-1 claims line 1 outside the diff post-image; it cannot be verified"),
])
def test_unknown_postimage_cannot_claim_valid_status(post, diagnostic):
    from code_forge.verify import ExcerptStatus, assess_excerpt_evidence

    result = assess_excerpt_evidence(
        _exc(1, 1, "alpha"), {"mod.py": [{"start": 1, "end": 1}]}, post,
    )
    assert result.status is ExcerptStatus.INVALID
    assert result.diagnostic == diagnostic
    assert not result.proven_lines


@pytest.mark.parametrize("lines,content,status", [
    (["alpha", "beta", ""], "alpha\nbeta", "VALID"),
    (["  alpha", "  beta", "tail"], "  alpha\n  beta", "UNTRUSTED"),
    (["alpha  ", "beta  ", "tail"], "alpha\nbeta", "UNTRUSTED"),
    (["alpha", "beta", "tail"], "alpha  \nbeta  ", "UNTRUSTED"),
])
def test_tail_omission_preserves_whitespace_and_blank_classification(lines, content, status):
    from code_forge.verify import ExcerptStatus

    result = _assess(_diff(lines), _exc(1, 3, content))
    assert result.status is ExcerptStatus(status)
    assert result.proven_lines == frozenset({1, 2})
    assert result.diagnostic == (None if status == "VALID"
                                 else "excerpt mod.py:1-3 declares 3 lines but carries 2")


@pytest.mark.parametrize("delta,status", [(-65, "INVALID"), (-64, "UNTRUSTED"),
                                          (65, "UNTRUSTED"), (66, "INVALID")])
def test_offset_search_respects_both_inclusive_limits(delta, status):
    from code_forge.verify import ExcerptStatus, assess_excerpt_evidence

    actual_start = 100 + delta
    result = assess_excerpt_evidence(
        _exc(100, 101, "alpha\nbeta"),
        {"mod.py": [{"start": actual_start, "end": actual_start + 1}]},
        {"mod.py": {actual_start: "alpha", actual_start + 1: "beta"}},
    )
    assert result.status is ExcerptStatus(status)
    expected = frozenset({actual_start, actual_start + 1}) if status == "UNTRUSTED" else frozenset()
    assert result.proven_lines == expected
    if status == "UNTRUSTED":
        assert result.diagnostic == (
            f"excerpt misnumbered by {delta:+d} at mod.py:100-101 "
            f"(claims mod.py:100, actually mod.py:{actual_start})"
        )


def test_offset_diagnostic_names_first_actual_mismatch():
    from code_forge.verify import ExcerptStatus, assess_excerpt_evidence

    result = assess_excerpt_evidence(
        _exc(3, 5, "alpha\nbeta\ngamma"),
        {"mod.py": [{"start": 1, "end": 5}]},
        {"mod.py": {1: "alpha", 2: "beta", 3: "gamma", 4: "other", 5: "last"}},
    )
    assert result.status is ExcerptStatus.UNTRUSTED
    assert result.proven_lines == frozenset({1, 2, 3})
    assert result.diagnostic == (
        "excerpt misnumbered by -2 at mod.py:3-5 (claims mod.py:3, actually mod.py:1)"
    )


def test_spent_leading_blank_cannot_hide_a_second_coordinate_error():
    from code_forge.verify import ExcerptStatus

    result = _assess(_diff(["", "wrong", "alpha", "beta"]), _exc(1, 3, "alpha\nbeta"))
    assert result.status is ExcerptStatus.UNTRUSTED
    assert result.proven_lines == frozenset({3, 4})
    assert result.diagnostic == (
        "excerpt misnumbered by +1 at mod.py:1-3 (claims mod.py:2, actually mod.py:3)"
    )


def test_empty_diff_has_no_context_to_validate():
    assert validate_excerpts_against_diff("", [_exc(1, 1, "alpha")]) == []
    assert validate_excerpts_against_diff(_diff(["alpha"]), []) == []


@pytest.mark.parametrize("exc,post,hunk", [
    (_exc(1, 3, "alpha\nbeta"), {1: "alpha", 2: "beta", 3: "changed"}, 3),
    (_exc(2, 3, "alpha\nbeta"), {1: "alpha", 2: "beta", 3: "changed"}, 3),
    (_exc(1, 2, "alpha\nbeta"), {1: "  alpha", 2: "  beta", 3: "changed"}, 3),
])
def test_recovered_quote_outside_hunk_reports_declared_location(exc, post, hunk):
    from code_forge.verify import ExcerptStatus, assess_excerpt_evidence

    result = assess_excerpt_evidence(
        exc, {"mod.py": [{"start": hunk, "end": hunk}]}, {"mod.py": post},
    )
    assert result.status is ExcerptStatus.INVALID
    assert result.proven_lines == frozenset()
    location = f"mod.py:{exc['start_line']}-{exc['end_line']}"
    assert result.diagnostic is not None
    assert location in result.diagnostic
    assert "outside every hunk" in result.diagnostic


def test_offset_diagnostic_skips_lines_that_match_at_declared_position():
    from code_forge.verify import ExcerptStatus, assess_excerpt_evidence

    result = assess_excerpt_evidence(
        _exc(3, 4, "alpha\nbeta"), {"mod.py": [{"start": 1, "end": 4}]},
        {"mod.py": {1: "alpha", 2: "beta", 3: "alpha", 4: "other"}},
    )
    assert result.status is ExcerptStatus.UNTRUSTED
    assert result.proven_lines == frozenset({1, 2})
    assert result.diagnostic is not None
    assert "claims mod.py:4, actually mod.py:2" in result.diagnostic


def test_deleted_only_file_keeps_exemption_without_source_credit():
    from code_forge.verify import ExcerptStatus

    diff = "diff --git a/mod.py b/mod.py\n--- a/mod.py\n+++ b/mod.py\n@@ -1 +0,0 @@\n-alpha\n"
    exc = _exc(1, 1, "alpha")
    assert validate_excerpts_against_diff(diff, [exc]) == []
    result = _assess(diff, exc)
    assert result.status is ExcerptStatus.VALID
    assert result.proven_lines == frozenset()
