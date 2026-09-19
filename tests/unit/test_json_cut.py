"""Unit tests for the JSON cut detector.

The detector decides whether a model reply is a cut-off prefix worth a
continuation request.  Both directions cost something when wrong: a
missed cut throws away a finished pass, a false cut spends a real
request on garbage.
"""

from code_forge.json_cut import _is_number_prefix, is_truncated

BS = chr(92)


class TestCutPrefixes:
    """Every prefix of a valid document must read as continuable.

    Bug-injection proof: return `bool(literal or stack)` without the
    unterminated-string branch and the string cases below FAIL.
    """

    def _all_prefixes_continue(self, doc):
        return [i for i in range(1, len(doc)) if not is_truncated(doc[:i])]

    def test_findings_array(self):
        doc = ('[{"id":"f1","sev":"P2","file":"hw/x.c","line":42,'
               '"desc":"quote ' + BS + '" and slash ' + BS + BS + ' inside"}]')
        assert self._all_prefixes_continue(doc) == []

    def test_envelope_numbers_and_nulls(self):
        doc = ('{"findings":[{"id":"a","score":0.5,"seen":null,"ok":true}],'
               '"code_excerpts":[{"file":"z.c","start":1,"end":2}]}')
        assert self._all_prefixes_continue(doc) == []

    def test_unicode_escapes(self):
        doc = '[{"cn":"' + BS + 'u4e2d' + BS + 'u6587","n":1}]'
        assert self._all_prefixes_continue(doc) == []

    def test_numbers(self):
        doc = '[{"a":-1.5e-3,"b":0.25,"c":42}]'
        assert self._all_prefixes_continue(doc) == []

    def test_deep_nesting(self):
        doc = '{"a":{"b":[{"c":[1,[2,{"d":"e"}]]}]}}'
        assert self._all_prefixes_continue(doc) == []

    def test_empty_containers(self):
        doc = '{"findings":[],"code_excerpts":[{}]}'
        assert self._all_prefixes_continue(doc) == []

    def test_trailing_backslash_is_a_cut(self):
        assert is_truncated('"' + BS) is True

    def test_partial_unicode_escape_is_a_cut(self):
        assert is_truncated('"' + BS + 'u12') is True


class TestFinishedOutput:
    """Finished or broken replies must not buy a continuation.

    Bug-injection proof: `return True` unconditionally and every case
    below FAILS.
    """

    def test_complete_array(self):
        assert is_truncated('[{"id":"a"}]') is False

    def test_complete_object(self):
        assert is_truncated('{"findings":[]}') is False

    def test_top_level_number(self):
        assert is_truncated("42") is False

    def test_prose_after_json(self):
        assert is_truncated('[{"id":"a"}] This seems robust.') is False

    def test_prose_only(self):
        assert is_truncated("I will report the precedence as P2.") is False

    def test_mismatched_bracket(self):
        assert is_truncated('{"a":[1,2}') is False

    def test_double_comma(self):
        assert is_truncated("[1,,2") is False

    def test_leading_comma(self):
        assert is_truncated("[,1") is False

    def test_colon_outside_object(self):
        assert is_truncated("[1:2") is False

    def test_bad_literal(self):
        assert is_truncated('{"a":tru3') is False

    def test_unquoted_key(self):
        assert is_truncated("{a:1") is False

    def test_bad_unicode_escape(self):
        assert is_truncated('"' + BS + 'uZZ') is False

    def test_bad_escape_char(self):
        assert is_truncated('"' + BS + 'q') is False

    def test_raw_newline_keeps_the_string_open(self):
        """RFC 8259 bars a raw newline, forge's own loader allows it.

        unescaped = %x20-21 / %x23-5B / %x5D-10FFFF puts %x0A out of
        range, but _loads_model_json passes strict=False, so a reply
        carrying one still parses once the rest arrives. Judge by what
        the downstream loader accepts, not by the grammar alone.
        """
        assert is_truncated('"line' + chr(10)) is True

    def test_empty_text(self):
        assert is_truncated("") is False

    def test_whitespace_only(self):
        assert is_truncated("   ") is False


class TestNumberPrefix:
    """_is_number_prefix decides whether more digits can still arrive.

    Bug-injection proof: drop the `text[-1] in "+-.eE"` branch and the
    exponent and trailing-dot cases FAIL.
    """

    def test_bare_sign_grows(self):
        assert _is_number_prefix("-") is True

    def test_trailing_dot_grows(self):
        assert _is_number_prefix("1.") is True

    def test_trailing_exponent_grows(self):
        assert _is_number_prefix("1e") is True

    def test_trailing_exponent_sign_grows(self):
        assert _is_number_prefix("1e-") is True

    def test_plain_integer_is_parseable(self):
        assert _is_number_prefix("42") is True

    def test_double_dot_is_not_a_number(self):
        assert _is_number_prefix("1..2") is False


class TestMutationHardening:
    """Samples that pin branches a mutation run found unguarded.

    Every assertion here corresponds to a mutant that survived the first
    run: a return False flipped to True, a continue swapped for break,
    or a hex guard inverted. They exist to make those edits fail.
    """

    def test_bad_hex_after_partial_run_is_rejected(self):
        """Two good hex digits then an illegal one is malformed, not cut."""
        assert is_truncated(chr(34) + chr(92) + "uAB!") is False

    def test_short_hex_run_at_eof_is_a_cut(self):
        """Same shape, but every digit seen so far is legal."""
        assert is_truncated(chr(34) + chr(92) + "uAB") is True

    def test_number_accumulates_every_digit(self):
        """number += ch degraded to number = ch keeps only the last char."""
        assert is_truncated("[123e") is True
        assert is_truncated("[123") is True

    def test_whitespace_between_tokens_is_skipped(self):
        """continue turned into break abandons the scan mid-array."""
        assert is_truncated("[ 1 , 2 ") is True
        assert is_truncated("[ 1 , 2 ]") is False

    def test_comma_needs_an_open_container(self):
        assert is_truncated("1,2") is False

    def test_colon_outside_an_object_is_rejected(self):
        assert is_truncated("[1:2") is False

    def test_colon_inside_an_object_is_accepted(self):
        assert is_truncated('{"k":1') is True

    def test_literal_keywords_scan_to_the_end(self):
        assert is_truncated("[tru") is True
        assert is_truncated("[true") is True
        assert is_truncated("[true]") is False
        assert is_truncated("[nul") is True
        assert is_truncated("[fals") is True

    def test_bare_sign_starts_a_number(self):
        """A lone sign can still grow into a number."""
        assert _is_number_prefix("-") is True
        assert is_truncated("[-") is True

    def test_trailing_exponent_marker_can_grow(self):
        assert _is_number_prefix("1e") is True
        assert _is_number_prefix("1E") is True
        assert _is_number_prefix("1") is True

    def test_non_numeric_text_is_not_a_number_prefix(self):
        """Guards the float() fallback: junk must not read as a number."""
        assert _is_number_prefix("abc") is False
        assert _is_number_prefix("1.2.3") is False


class TestTopLevelNumberTail:
    """Bare numbers leave the stack empty, so the tail check really runs.

    Inside a container bool(stack) short-circuits the whole expression,
    which is why every earlier number sample missed these mutants. A
    top-level number is the only shape that reaches number[-1].
    """

    def test_uppercase_exponent_marker_is_unfinished(self):
        """Kills the "+-.eE" -> "+-.ee" edit: uppercase must still match."""
        assert is_truncated("1E") is True

    def test_lowercase_exponent_marker_is_unfinished(self):
        assert is_truncated("1e") is True

    def test_single_digit_is_complete(self):
        """Kills number[+1]: one char has no index 1 to read."""
        assert is_truncated("1") is False
        assert is_truncated("9") is False

    def test_two_digits_are_complete(self):
        """Kills number[-2]: the second-to-last char is a digit, not a marker."""
        assert is_truncated("12") is False

    def test_trailing_decimal_point_is_unfinished(self):
        assert is_truncated("1.") is True

    def test_finished_exponent_is_complete(self):
        assert is_truncated("1E5") is False

    def test_lone_sign_is_unfinished(self):
        assert is_truncated("-") is True


class TestStructuralRejects:
    """Malformed structure is not a cut, so every branch returns False.

    A cut prefix can always be completed by appending text. None of the
    samples below can: the damage is already in the bytes received, so
    spending a continuation on them would burn budget for nothing.
    """

    def test_container_where_no_value_is_expected(self):
        assert is_truncated("[1[") is False

    def test_mismatched_closer(self):
        assert is_truncated("[1}") is False
        assert is_truncated('{"a":1]') is False

    def test_closer_with_empty_stack(self):
        assert is_truncated("]") is False
        assert is_truncated("}") is False

    def test_literal_where_no_value_is_expected(self):
        assert is_truncated("[1t") is False

    def test_number_where_no_value_is_expected(self):
        assert is_truncated("[1 2") is False

    def test_character_that_starts_no_json_token(self):
        assert is_truncated("[@") is False

    def test_number_that_can_never_parse(self):
        assert is_truncated("[1.2.3") is False


class TestStringPositionAndEscapes:
    """Cover the string branch: placement, escape runs, hex length."""

    def test_string_where_no_value_is_expected(self):
        assert is_truncated('[1"a"') is False

    def test_colon_inside_an_array_is_rejected(self):
        assert is_truncated('["a":') is False

    def test_escape_then_more_text_keeps_scanning(self):
        """A break here would stop at the escape and misjudge the tail."""
        assert is_truncated(chr(34) + chr(92) + "nmore") is True

    def test_escape_inside_a_closed_string(self):
        assert is_truncated(chr(34) + chr(92) + "nabc" + chr(34)) is False
        assert is_truncated("[" + chr(34) + chr(92) + "nabc" + chr(34)) is True

    def test_hex_run_of_every_partial_length(self):
        """Each length must read as a cut, not as a bad escape."""
        for digits in ("", "A", "AB", "ABC"):
            assert is_truncated(chr(34) + chr(92) + "u" + digits) is True

    def test_full_hex_run_leaves_the_string_open(self):
        assert is_truncated(chr(34) + chr(92) + "uABCD") is True

    def test_unknown_escape_letter_is_malformed(self):
        assert is_truncated(chr(34) + chr(92) + "q") is False

    def test_complete_hex_escape_in_a_closed_string(self):
        """A finished \\uXXXX must not keep the reply marked as cut.

        Inverting the "ran out of digits" guard only shows up once the
        run is complete AND the string closes; every shorter sample is
        caught by the unclosed-string fallback instead.
        """
        assert is_truncated(chr(34) + chr(92) + "uABCD" + chr(34)) is False


class TestEveryRejectBranch:
    """One witness per reject branch, found by exhaustive search.

    Each input below is the shortest string whose verdict changes when
    that branch flips to True, so the branch cannot be quietly weakened.
    Three further branches have no such witness and are unreachable by
    construction.
    """

    def test_whitespace_only(self):
        assert is_truncated(" ") is False

    def test_string_after_a_number(self):
        assert is_truncated('1"') is False

    def test_container_opener_inside_a_string_escape(self):
        assert is_truncated(chr(34) + chr(92) + "{") is False

    def test_container_after_a_literal(self):
        assert is_truncated("t{") is False

    def test_container_after_a_number(self):
        assert is_truncated("1{") is False

    def test_bare_closer(self):
        assert is_truncated("}") is False

    def test_bare_comma(self):
        assert is_truncated(",") is False

    def test_bare_colon(self):
        assert is_truncated(":") is False

    def test_colon_straight_after_an_object_opener(self):
        assert is_truncated("{:") is False

    def test_literal_after_a_number(self):
        assert is_truncated("1t") is False

    def test_number_after_a_finished_object(self):
        assert is_truncated("{}1") is False

    def test_bare_backslash(self):
        assert is_truncated(chr(92)) is False

    def test_number_with_two_dots(self):
        assert is_truncated("1..1") is False


class TestObjectKeyPosition:
    """An object alternates key and value; one flag could not hold both."""

    def test_second_colon_in_one_pair_is_not_a_cut(self):
        assert is_truncated('{"a":"b":') is False

    def test_second_colon_after_a_number_value(self):
        assert is_truncated('{"a":1:') is False

    def test_ordinary_pair_sequence_is_still_a_cut(self):
        assert is_truncated('{"a":"b","c":1') is True

    def test_value_finished_without_closing_is_a_cut(self):
        assert is_truncated('{"a":"b"') is True

    def test_number_key_is_rejected(self):
        assert is_truncated("{1") is False

    def test_container_key_is_rejected(self):
        assert is_truncated("{[") is False
        assert is_truncated("{{") is False

    def test_literal_key_is_rejected(self):
        assert is_truncated("{t") is False

    def test_empty_containers_are_complete(self):
        assert is_truncated("{}") is False
        assert is_truncated("[]") is False

    def test_nested_empty_object_still_open(self):
        assert is_truncated('{"a":{}') is True

    def test_colon_outside_an_object_is_rejected(self):
        assert is_truncated("[1:") is False

    def test_dangling_comma_before_close_is_rejected(self):
        assert is_truncated("[1,]") is False
        assert is_truncated('{"a":1,}') is False
