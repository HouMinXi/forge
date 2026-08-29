from unittest.mock import patch
import pytest
from code_forge.disposition import Disposition
from code_forge.falsify_real import RealFalsifier
from code_forge.llm_invoke import LLMInvokeError, LLMResult, Usage
from code_forge.state import StateFinding


def _make_finding(fp="fp-1"):
    return StateFinding(
        id=fp, fingerprint=fp, source="L1",
        disposition=Disposition.CONFIRMED,
        file="src/foo.py", line_range=[42, 45],
        description="potential null dereference",
    )


def _make_llm_result(content):
    """Build a LLMResult wrapping the given content dict."""
    return LLMResult(content=content, usage=Usage(), duration_s=0.1)


class TestRealFalsifier:
    def test_confirmed_on_real_finding(self):
        resp = _make_llm_result({"verdict": "CONFIRMED", "reasoning": "path reachable"})
        with patch("code_forge.falsify_real.llm_invoke", return_value=resp):
            assert RealFalsifier().falsify(_make_finding()) == Disposition.CONFIRMED

    def test_dismissed_on_false_positive(self):
        resp = _make_llm_result({"verdict": "DISMISSED", "reasoning": "caller validates"})
        with patch("code_forge.falsify_real.llm_invoke", return_value=resp):
            assert RealFalsifier().falsify(_make_finding()) == Disposition.DISMISSED

    def test_uncertain_on_ambiguous(self):
        resp = _make_llm_result({"verdict": "UNCERTAIN", "reasoning": "depends on config"})
        with patch("code_forge.falsify_real.llm_invoke", return_value=resp):
            assert RealFalsifier().falsify(_make_finding()) == Disposition.UNCERTAIN

    def test_invoke_error_propagates(self):
        """Backend failure is not a verdict.

        This assertion is INVERTED from its original form (241ae61,
        "Subprocess failure returns UNCERTAIN"). That design made an
        unreachable backend indistinguishable from a finding the
        verifier genuinely could not decide, and the fixpoint check
        resets the clean-round counter on any UNCERTAIN -- so a dead
        backend reset the counter every round until max_total_rounds,
        at 30-180s per call. Measured 2026-08-27: three hours of a
        review that could not converge.

        The caller (machine.py falsify loop) now catches this, records
        it to infra_errors, and stops the run after three consecutive
        rounds of it. llm_invoke already owns the retry budget;
        exhausting it is an infrastructure outcome, not a judgement.
        """
        with patch(
            "code_forge.falsify_real.llm_invoke",
            side_effect=LLMInvokeError("timeout"),
        ):
            with pytest.raises(LLMInvokeError):
                RealFalsifier().falsify(_make_finding())

    def test_rejects_fixed_verdict(self):
        resp = _make_llm_result({"verdict": "FIXED", "reasoning": "n/a"})
        with patch("code_forge.falsify_real.llm_invoke", return_value=resp):
            with pytest.raises(ValueError, match="FIXED"):
                RealFalsifier().falsify(_make_finding())

    def test_uncertain_on_unknown_verdict(self):
        resp = _make_llm_result({"verdict": "BOGUS", "reasoning": "n/a"})
        with patch("code_forge.falsify_real.llm_invoke", return_value=resp):
            assert RealFalsifier().falsify(_make_finding()) == Disposition.UNCERTAIN


class TestVerdictSurvivesTruncatedReasoning:
    """A verdict must not be lost because the reasoning string ran long.

    The falsify prompt asks for {"verdict", "reasoning"}.  "verdict" is
    the only field any caller reads -- grep for .reasoning outside the
    prompt text finds nothing.  But "reasoning" is free-form prose that
    a thinking model happily runs for thousands of tokens, and it sits
    AFTER the verdict in the emitted object.  When the output budget
    ends mid-string the JSON is unparseable and the whole call is
    retried, even though the verdict was already complete on the wire.

    Observed live on 2026-08-29 (rulepack smoke, mimo-direct): four
    retries in one falsify phase, each dying inside the reasoning
    string, each eventually succeeding on a later attempt.  Nothing was
    lost, but every retry paid for the whole call again.
    """

    def test_extractor_recovers_verdict_from_truncated_reasoning(self):
        """Byte-exact replay of a truncation seen in the smoke log."""
        from code_forge.llm_invoke import _extract_json_from_text
        truncated = (
            '{\n "verdict": "UNCERTAIN",\n "reasoning": "The finding '
            'concerns a potential misconfiguration of the '
            '`version_sensitive` flag for a RULEPACK in `src/code_for'
        )
        got = _extract_json_from_text(
            truncated, expected_keys=frozenset({"verdict", "reasoning"}),
        )
        assert got is not None, "truncated reasoning must not void the verdict"
        assert got["verdict"] == "UNCERTAIN"

    def test_extractor_recovers_second_observed_truncation(self):
        """The other shape from the same log: cut at a nested quote."""
        from code_forge.llm_invoke import _extract_json_from_text
        truncated = (
            '{\n "verdict": "CONFIRMED",\n "reasoning": "The code at line '
            '1855 in src/code_forge/machine.py calls `runner.run('
            'self.resolved_review.git_diff or "", self.cwd)` without a '
            'surrounding try-except block. This path is reachable during'
        )
        got = _extract_json_from_text(
            truncated, expected_keys=frozenset({"verdict", "reasoning"}),
        )
        assert got is not None
        assert got["verdict"] == "CONFIRMED"

    def test_complete_json_is_untouched_by_the_salvage_path(self):
        """Salvage must not alter a well-formed response."""
        from code_forge.llm_invoke import _extract_json_from_text
        good = '{"verdict": "DISMISSED", "reasoning": "caller validates"}'
        got = _extract_json_from_text(
            good, expected_keys=frozenset({"verdict", "reasoning"}),
        )
        assert got == {"verdict": "DISMISSED", "reasoning": "caller validates"}

    def test_truncation_before_the_verdict_is_not_salvaged(self):
        """No verdict on the wire means nothing to recover -- must fail.

        Guards against a salvage so eager it invents a verdict.
        """
        from code_forge.llm_invoke import _extract_json_from_text
        truncated = '{\n "verd'
        got = _extract_json_from_text(
            truncated, expected_keys=frozenset({"verdict", "reasoning"}),
        )
        assert got is None

    def test_a_bogus_verdict_value_is_not_salvaged_into_validity(self):
        """Salvage recovers the field; it does not vouch for the value."""
        from code_forge.llm_invoke import _extract_json_from_text
        truncated = '{\n "verdict": "MAYBE",\n "reasoning": "half a th'
        got = _extract_json_from_text(
            truncated, expected_keys=frozenset({"verdict", "reasoning"}),
        )
        # Recovery is allowed; RealFalsifier maps unknown -> UNCERTAIN.
        if got is not None:
            assert got["verdict"] == "MAYBE"

    def test_falsifier_maps_a_salvaged_bogus_verdict_to_uncertain(self):
        resp = _make_llm_result({"verdict": "MAYBE", "reasoning": ""})
        with patch("code_forge.falsify_real.llm_invoke", return_value=resp):
            assert RealFalsifier().falsify(_make_finding()) is Disposition.UNCERTAIN


class TestSalvageDoesNotFakeACleanReview:
    """Salvage must never turn a cut-off review into a clean verdict.

    The L1 envelope is {"findings": [...], "code_excerpts": {...}}.  A
    response truncated AFTER an empty findings array but before the
    model had finished is indistinguishable, once salvaged, from a
    genuine "no findings" answer -- and forge's whole convergence
    counter is driven by that emptiness.  Recovering a partial verdict
    is worth a retry saved; recovering a partial CLEAN REVIEW is a
    false green, which is the exact failure class the pipeline exists
    to prevent.  So the salvage path is restricted to callers whose
    envelope has no such all-clear reading.
    """

    def test_truncated_l1_envelope_is_not_salvaged(self):
        from code_forge.llm_invoke import _extract_json_from_text
        truncated = (
            '{"findings": [], "code_excerpts": {"a.py": "def f(): pass"}, '
            '"note": "still thinking about the sec'
        )
        got = _extract_json_from_text(truncated)
        assert got is None, "a cut-off review must not read as clean"

    def test_truncated_runtime_envelope_is_not_salvaged(self):
        from code_forge.llm_invoke import _extract_json_from_text
        truncated = '{"surfaces": [], "findings": [], "trailing": "cut'
        got = _extract_json_from_text(truncated)
        assert got is None

    def test_complete_l1_envelope_still_parses(self):
        from code_forge.llm_invoke import _extract_json_from_text
        good = '{"findings": [], "code_excerpts": {}}'
        got = _extract_json_from_text(good)
        assert got == {"findings": [], "code_excerpts": {}}

    def test_falsify_envelope_is_still_salvaged(self):
        """The narrow case the fix exists for stays fixed."""
        from code_forge.llm_invoke import _extract_json_from_text
        truncated = '{"verdict": "CONFIRMED", "reasoning": "long prose cut'
        got = _extract_json_from_text(
            truncated, expected_keys=frozenset({"verdict", "reasoning"}),
        )
        assert got is not None and got["verdict"] == "CONFIRMED"


class TestSalvageInternals:
    """Pin the two guards inside _salvage_truncated_object.

    Both were unprotected when first written: injecting a fault into
    either left the suite green.  Added after that bug-injection run.
    """

    def test_a_malformed_but_closed_object_is_not_salvaged(self):
        """depth-0 early return: a closed object is a syntax error, not a cut.

        Without the guard, salvage would trim a genuinely malformed
        response back to its last comma and return a "successful" parse
        of something the model got wrong -- silently changing a hard
        failure into a partial answer.
        """
        from code_forge.llm_invoke import _extract_json_from_text
        # Closed object, but the second value is invalid JSON.
        malformed = '{"verdict": "CONFIRMED", "reasoning": undefined_token}'
        got = _extract_json_from_text(
            malformed, expected_keys=frozenset({"verdict", "reasoning"}),
        )
        assert got is None

    def test_a_comma_inside_a_string_is_not_a_boundary(self):
        """in_string tracking: prose commas must not split the object.

        The reasoning field is English prose and full of commas.  If the
        scanner treated those as structural, it would cut mid-sentence
        and hand back a truncated string as if it were complete.
        """
        from code_forge.llm_invoke import _extract_json_from_text
        truncated = (
            '{"verdict": "DISMISSED", '
            '"reasoning": "First, the caller validates. Second, the path '
            'is guarded. Third, the sym'
        )
        got = _extract_json_from_text(
            truncated, expected_keys=frozenset({"verdict", "reasoning"}),
        )
        assert got is not None
        assert got["verdict"] == "DISMISSED"
        # The clipped prose is dropped whole, never handed back partial.
        assert "reasoning" not in got

    def test_a_clipped_nested_value_forfeits_the_whole_salvage(self):
        """depth==1 (not >=1): commas inside a nested value are not cuts.

        A pair whose value is an unterminated object leaves the scanner
        below the top level for the rest of the text, so no top-level
        boundary is ever recorded after it and salvage declines --
        giving up the boundary AFTER it.  The leading verdict still
        survives, because the outer scan reaches the top-level comma
        before descending: salvage cuts there and drops the clipped
        nested pair whole.  Recorded as measured, not assumed -- an
        earlier version of this test asserted None and was wrong.
        """
        from code_forge.llm_invoke import _extract_json_from_text
        truncated = (
            '{"verdict": "CONFIRMED", '
            '"evidence": {"file": "a.py", "line": 42, "note": "cut'
        )
        got = _extract_json_from_text(
            truncated, expected_keys=frozenset({"verdict", "reasoning"}),
        )
        assert got == {"verdict": "CONFIRMED"}

    def test_escaped_quote_does_not_end_the_string(self):
        """Prose containing escaped quotes still salvages correctly.

        Note on coverage honesty: disabling the escape branch does NOT
        fail this test, and no valid-JSON prefix can make it fail.
        Escaped quotes arrive in pairs, so a scanner that ignores the
        backslash opens and closes the string an even number of extra
        times and lands in the same state.  Only an odd count would
        diverge, and that is not JSON any backend can emit.  The branch
        is kept because json.JSONDecoder's own grammar has it and
        removing it would make the scanner subtly non-conformant, not
        because a test pins it.
        """
        from code_forge.llm_invoke import _extract_json_from_text
        truncated = (
            '{"verdict": "UNCERTAIN", '
            '"reasoning": "The docstring says \\"safe\\", but, notably, the'
        )
        got = _extract_json_from_text(
            truncated, expected_keys=frozenset({"verdict", "reasoning"}),
        )
        assert got is not None
        assert got["verdict"] == "UNCERTAIN"
        assert "reasoning" not in got
