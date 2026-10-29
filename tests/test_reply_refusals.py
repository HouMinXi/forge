"""Round 9 review fixes for #264: refusals reach every reader of a reply.

A duplicate key is refused wherever reply text is parsed, a refused CLI
reply fails the call, and the fallback scan is bounded. Each test names the
review finding it pins (sec F1-F7, N2; cr1, cr2, cr3).
"""

import importlib
import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from code_forge.backend import BackendConfig
from code_forge.llm_invoke import (
    DuplicateReplyKey,
    LLMInvokeError,
    RepairRefused,
    ReplyKeyRejected,
    _extract_json_from_text,
    _loads_model_json,
    llm_invoke,
    loads_reply_json,
)
from code_forge.reviewer_json import REVIEW_REPLY_KEYS

invoke = importlib.import_module("code_forge.llm_invoke")

P1 = {"file": "a.py", "line": 3, "severity": "P1", "description": "d"}
EXCERPT = {"file": "a.py", "start_line": 1, "end_line": 3, "content": "x"}
GOOD = {"findings": [P1], "code_excerpts": [EXCERPT]}
# Valid JSON: the second "findings" empties the first.
DUPLICATE = '{"findings": [%s], "code_excerpts": [%s], "findings": []}' % (
    json.dumps(P1),
    json.dumps(EXCERPT),
)
STOP = {"prompt_tokens": 1, "completion_tokens": 2, "_forge_finish_reason": "stop"}


def _api_backend():
    return BackendConfig(
        name="refusals-test",
        type="api",
        format="openai",
        base_url="http://127.0.0.1:1/v1",
        api_key_env="REFUSALS_TEST_KEY",
        model="test",
        max_tokens=1024,
    )


@pytest.fixture
def api_env(monkeypatch):
    monkeypatch.setenv("REFUSALS_TEST_KEY", "local-test")
    monkeypatch.setattr(invoke.time, "sleep", Mock())


@pytest.fixture
def cli_binary():
    with patch("shutil.which", return_value="/usr/bin/claude"):
        yield


def _proc(stdout):
    proc = MagicMock()
    proc.returncode = 0
    proc.pid = 12345
    proc.communicate.return_value = (stdout, "")
    return proc


def _cli(stdout, keys):
    backend = BackendConfig(name="cli-test", type="cli", model="m", command="")
    with patch("code_forge.llm_invoke.subprocess.Popen", return_value=_proc(stdout)):
        return llm_invoke("prompt", backend=backend, reply_keys=keys)


# --- sec F1 / cr3 P2 / cr2 P3-3: a CLI reply with a duplicate key ----------

# cr3's repro: the envelope needs a quote repair, and its object result then
# repeats "findings".
_REPAIRED_ENVELOPE = (
    '{"type": "result", "subtype": "success", "result": {"findings": [{"file": "a.py", '
    '"line": 3, "severity": "P1", "description": "the "x" case"}], "code_excerpts": [], '
    '"findings": []}}'
)
_CLI_DUPLICATES = {
    "repaired_envelope": _REPAIRED_ENVELOPE,
    "repaired_stream": '[{"type": "system"}, ' + _REPAIRED_ENVELOPE + "]",
    "object_result": '{"type": "result", "result": ' + DUPLICATE + "}",
    "string_result": json.dumps({"type": "result", "result": DUPLICATE}),
    "string_result_in_stream": '[{"type": "system"}, '
    + json.dumps({"type": "result", "result": DUPLICATE})
    + "]",
    "bare": DUPLICATE,
}


@pytest.mark.parametrize("keys", [None, REVIEW_REPLY_KEYS], ids=["no_keys", "keys"])
@pytest.mark.parametrize("stdout", list(_CLI_DUPLICATES.values()), ids=list(_CLI_DUPLICATES))
def test_cli_duplicate_key_fails_the_call(cli_binary, stdout, keys):
    with pytest.raises(
        LLMInvokeError, match='reply refused -- DuplicateReplyKey: Duplicate key "findings"'
    ):
        _cli(stdout, keys)


def test_cli_repaired_envelope_without_a_duplicate_is_read(cli_binary):
    stdout = _REPAIRED_ENVELOPE.replace(', "findings": []', "")
    result = _cli(stdout, REVIEW_REPLY_KEYS)
    assert result.content["findings"][0]["description"] == 'the "x" case'


def test_cli_parses_stdout_once(cli_binary):
    # sec F4: the envelope is parsed once, not again for the bare-reply check.
    calls = []
    real = invoke._parse_model_json

    def counted(text, reply_keys=None):
        calls.append(text)
        return real(text, reply_keys)

    with patch.object(invoke, "_parse_model_json", counted):
        _cli(json.dumps(GOOD), REVIEW_REPLY_KEYS)
    assert len(calls) == 1


# --- sec F1: the API path ---------------------------------------------------


def test_api_duplicate_key_spends_the_correction_retry(api_env, monkeypatch):
    request = Mock(side_effect=[(DUPLICATE, STOP), (json.dumps(GOOD), STOP)])
    monkeypatch.setattr(invoke, "_invoke_openai", request)
    result = invoke._invoke_api("task", _api_backend(), 7, max_attempts=2, reply_keys=REVIEW_REPLY_KEYS)
    assert result.content == GOOD
    assert request.call_count == 2
    # cr3 P3-2: the correction names the refusal, not a syntax position.
    second = request.call_args_list[1].args[0]
    assert (
        'Your previous response was refused: Duplicate key "findings" in reply. '
        "Each key may appear only once in an object. "
    ) in second
    assert "JSONDecodeError at line" not in second


@pytest.mark.parametrize("keys", [None, REVIEW_REPLY_KEYS], ids=["no_keys", "keys"])
def test_api_repeated_duplicate_fails_closed(api_env, monkeypatch, keys):
    request = Mock(return_value=(DUPLICATE, STOP))
    monkeypatch.setattr(invoke, "_invoke_openai", request)
    with pytest.raises(LLMInvokeError):
        invoke._invoke_api("task", _api_backend(), 7, max_attempts=2, reply_keys=keys)
    assert request.call_count == 2


# --- sec F1 / N1 / N2: every later parse of reply text ----------------------


def test_loads_reply_json_refuses_a_duplicate():
    assert json.loads(DUPLICATE)["findings"] == []
    with pytest.raises(ValueError, match='Duplicate key "findings"'):
        loads_reply_json(DUPLICATE)
    assert loads_reply_json(json.dumps(GOOD)) == GOOD


def test_loads_reply_json_refuses_deep_nesting():
    with pytest.raises(ValueError, match="nests too deeply"):
        loads_reply_json("[" * 100_000 + "]" * 100_000)


def test_validate_reviewer_json_refuses_a_duplicate():
    from code_forge.reviewer_json import validate_reviewer_json

    with pytest.raises(ValueError, match='Duplicate key "findings"'):
        validate_reviewer_json(DUPLICATE)
    with pytest.raises(ValueError, match='Duplicate key "findings"'):
        validate_reviewer_json("```json\n" + DUPLICATE + "\n```")


def test_canary_review_refuses_a_duplicate(capsys):
    # sec N2: json.loads kept the last "findings" and lost the canary's finding.
    from code_forge.canary_gen import dispatch_canary_review

    finding = {"file": "a.py", "line": 1, "severity": "P1", "description": "off by one"}
    reply = json.dumps({"findings": [finding]})[:-1] + ', "findings": []}'
    assert dispatch_canary_review("diff", provider=lambda prompt: reply) == []
    assert "cannot parse reviewer response" in capsys.readouterr().err


def test_raw_response_audit_refuses_a_duplicate():
    from code_forge.factories import _assess_raw_response

    assert _assess_raw_response(DUPLICATE) == (None, False)
    assert _assess_raw_response(json.dumps(GOOD)) == (GOOD, True)


def test_receipt_canonical_form_refuses_a_duplicate():
    from code_forge.receipt import _canonical_response

    # Not read as JSON, so the empty "findings" does not replace the P1.
    assert _canonical_response(DUPLICATE) == " ".join(DUPLICATE.split())
    assert _canonical_response(json.dumps(GOOD)).startswith('{"code_excerpts":')


def test_test_assertion_review_refuses_a_duplicate():
    # sec N1: the test-assertion review re-parses the reply text.
    from code_forge.assertion_outcome import AssertionStage, AssertionStatus
    from code_forge.cli import _run_test_assertion_review

    diff = "diff --git a/tests/test_a.py b/tests/test_a.py\n@@ -1 +1,2 @@\n x\n+y\n"
    outcomes = []
    with patch(
        "code_forge.llm_invoke.llm_invoke", Mock(return_value=SimpleNamespace(content=DUPLICATE))
    ):
        findings = _run_test_assertion_review(diff, backend=None, on_result=outcomes.append)
    assert findings == []
    assert outcomes[0].status is AssertionStatus.FAILED
    assert outcomes[0].stage is AssertionStage.VALIDATION


# --- sec F2: with reply keys only a leading or list object is taken --------

_PROSE_LED = "Here is my review: " + json.dumps(GOOD)


def test_prose_led_reply_is_not_taken_with_keys():
    err = pytest.raises(json.JSONDecodeError, json.loads, _PROSE_LED).value
    assert invoke._fallback_reply(_PROSE_LED, err, None, REVIEW_REPLY_KEYS) is None
    assert invoke._fallback_reply(_PROSE_LED, err, None, None) == GOOD
    listed = "[" + json.dumps(GOOD) + "] trailing words"
    err = pytest.raises(json.JSONDecodeError, json.loads, listed).value
    assert invoke._fallback_reply(listed, err, None, REVIEW_REPLY_KEYS) == GOOD


def test_prose_led_reply_costs_the_correction_retry(api_env, monkeypatch):
    request = Mock(side_effect=[(_PROSE_LED, STOP), (json.dumps(GOOD), STOP)])
    monkeypatch.setattr(invoke, "_invoke_openai", request)
    result = invoke._invoke_api("task", _api_backend(), 7, max_attempts=2, reply_keys=REVIEW_REPLY_KEYS)
    assert result.content == GOOD
    assert request.call_count == 2


# --- sec F3: what may follow the reply -------------------------------------

_REPLY = '{"findings": ["first"]}'


@pytest.mark.parametrize(
    "after",
    [
        "\u200b done",  # zero-width space
        "\ufeff done",  # BOM
        "\uff09 done",  # fullwidth )
        "\uff5d",  # fullwidth }
        "\uff3d done",  # fullwidth ]
        "\u00b7 x",  # middle dot
        "\u300cx\u300d",  # corner quotes
        "\u201cx",  # smart quote
        "\uff1b x",  # fullwidth ;
        "\uff0c x",  # fullwidth ,
    ],
)
def test_non_ascii_code_marks_are_refused(after):
    assert _extract_json_from_text(_REPLY + " " + after) is None


@pytest.mark.parametrize(
    "after",
    ["\u597d\u7684", "\u3002", "\u3002\u5b8c", "\u2014 done", "\u00e9t\u00e9", "\u2026"],
    ids=["cjk_word", "cjk_stop_at_end", "cjk_stop_then_word", "em_dash", "accented", "ellipsis"],
)
def test_non_ascii_prose_is_kept(after):
    assert _extract_json_from_text(_REPLY + " " + after) == {"findings": ["first"]}


def test_brace_inside_a_literal_string_keeps_the_literal_open():
    # The '}' in the string does not close the literal, so the object
    # after it sits inside the literal and is not the reply.
    text = 'Note {key: "}", v: {"findings": [], "code_excerpts": []}} done'
    assert _extract_json_from_text(text) is None
    # A literal that really closes before the object does not hold it.
    closed = 'Note {key: "}"} then {"findings": [], "code_excerpts": []} done'
    assert _extract_json_from_text(closed) == {"findings": [], "code_excerpts": []}


# --- sec F4: bounds on the repair and the scan -----------------------------


def _needs_repairs(n):
    # Each `say "hi".` holds two unescaped quotes, so n of them need 2n repairs.
    return '{"findings": [{"description": "' + 'say "hi". ' * n + '"}]}'


def test_repair_count_is_capped(monkeypatch):
    half = invoke._MAX_REPAIRS // 2
    assert _loads_model_json(_needs_repairs(half))["findings"][0]["description"].count('"hi"') == half
    with pytest.raises(RepairRefused):
        _loads_model_json(_needs_repairs(half + 1))
    monkeypatch.setattr(invoke, "_MAX_REPAIRS", invoke._MAX_REPAIRS + 2)
    assert len(_loads_model_json(_needs_repairs(half + 1))["findings"]) == 1


def test_repair_length_is_capped(monkeypatch):
    pad = " " * invoke._MAX_REPAIR_CHARS
    assert len(_loads_model_json(_needs_repairs(1))["findings"]) == 1
    with pytest.raises(RepairRefused):
        _loads_model_json(_needs_repairs(1) + pad)
    monkeypatch.setattr(invoke, "_MAX_REPAIR_CHARS", 2 * len(pad))
    assert len(_loads_model_json(_needs_repairs(1) + pad)["findings"]) == 1
    # A long text that needs no repair still parses.
    assert _loads_model_json(json.dumps(GOOD) + pad) == GOOD


def test_scan_length_is_capped():
    pad = " " * invoke._MAX_REPAIR_CHARS
    assert _extract_json_from_text("Note: " + json.dumps(GOOD)) == GOOD
    assert _extract_json_from_text("Note: " + json.dumps(GOOD) + pad) is None


_SLOW_SHAPES = {
    "object_literals": "Note: " + "{a:" * 87_000,
    "open_objects": "Note: " + '{"a":' * 52_000,
    "open_lists": "Note: " + '{"a":[' * 43_000,
    "objects_after_reply": '{"findings": []} ' + '{"x": 1} ' * 29_000,
    "string_literals": "x {k: " + '"}" ' * 65_000,
    "replies_after_prose": "Note " + '{"findings": [], "x": 1} ' * 10_000,
}


@pytest.mark.parametrize("text", list(_SLOW_SHAPES.values()), ids=list(_SLOW_SHAPES))
def test_large_adversarial_texts_are_fast(text):
    start = time.monotonic()
    for leading_only in (False, True):
        _extract_json_from_text(text, leading_only=leading_only)
    try:
        _loads_model_json(text, REVIEW_REPLY_KEYS)
    except json.JSONDecodeError:
        pass
    assert time.monotonic() - start < 5


# --- sec F7: deep nesting fails closed --------------------------------------


def test_deep_nesting_fails_closed():
    with pytest.raises(RepairRefused, match="nests too deeply"):
        _loads_model_json("[" * 100_000)
    with pytest.raises(RepairRefused, match="nests too deeply"):
        _loads_model_json("[" * 100_000 + "]" * 100_000)
    assert _extract_json_from_text('{"findings": ' + "[" * 100_000) is None
    assert _extract_json_from_text("Note: " + '{"findings": ' + "[" * 100_000) is None


# --- cr1 P2-1 / cr2 P3-2: a continuation that adds nothing ------------------


def _truncated(content):
    return invoke._TruncatedResponse(
        "cut", content=content, usage_data={}, resolved_cap=1024, kind="truncated", retryable=False
    )


@pytest.mark.parametrize(
    "cont",
    ["", "}", "\n}", "]}", "```", "\n```\n", json.dumps(GOOD), "```json\n" + json.dumps(GOOD) + "\n```"],
    ids=[
        "empty",
        "brace",
        "newline_brace",
        "bracket_brace",
        "fence",
        "fence_lines",
        "restated",
        "fenced",
    ],
)
def test_echo_after_a_whole_partial_keeps_the_p1(api_env, monkeypatch, cont):
    # The reply was whole when the output cap stopped it; the continuation
    # echoes its end or repeats it. One continuation call, no more.
    request = Mock(side_effect=[_truncated(json.dumps(GOOD)), (cont, STOP)])
    monkeypatch.setattr(invoke, "_invoke_openai", request)
    result = invoke._invoke_api("task", _api_backend(), 7, max_attempts=2, reply_keys=REVIEW_REPLY_KEYS)
    assert result.content["findings"] == [P1]
    assert request.call_count == 2


def test_echo_does_not_rescue_a_cut_partial(api_env, monkeypatch):
    # A partial that is not whole is not read alone after an echo.
    cut = json.dumps(GOOD)[:-12]
    request = Mock(side_effect=[_truncated(cut), ("}", STOP), ("}", STOP), ("}", STOP)])
    monkeypatch.setattr(invoke, "_invoke_openai", request)
    with pytest.raises(LLMInvokeError):
        invoke._invoke_api("task", _api_backend(), 7, max_attempts=1, reply_keys=REVIEW_REPLY_KEYS)


def test_restated_reply_with_a_duplicate_is_refused():
    partial = json.dumps(GOOD)[:-12]
    assert isinstance(invoke._continued_reply(partial, DUPLICATE, None, REVIEW_REPLY_KEYS), str)


# --- cr1 P3-1 / P3-3: the review reply keys --------------------------------


def test_excerpt_aliases_are_reply_keys():
    text = (
        '{"findings": [{"file": "a.py", "line": 1, "severity": "P1", "description": "uses "x" here"}], '
        '"code_excerpts": [{"file": "a.py", "line_start": 1, "line_end": 1, "code": "x"}]}'
    )
    assert _loads_model_json(text)["findings"][0]["severity"] == "P1"
    parsed = _loads_model_json(text, REVIEW_REPLY_KEYS)
    assert parsed["findings"][0]["description"] == 'uses "x" here'
    assert parsed["code_excerpts"][0]["code"] == "x"


def test_review_reply_top_keys_are_pinned():
    assert REVIEW_REPLY_KEYS.top == frozenset({"findings", "code_excerpts", "context_quotes"})


def test_repaired_review_with_an_extra_top_key_is_refused():
    text = '{"findings": [{"file": "a.py", "line": 1, "description": "uses "x" here"}], "c": "d"}'
    with pytest.raises(ReplyKeyRejected):
        _loads_model_json(text, REVIEW_REPLY_KEYS)


# --- cr3 P3-3: list and prose-led objects with text after them -------------


@pytest.mark.parametrize(
    "text",
    [
        "[" + _REPLY + '], {"x": 1}',
        "[" + _REPLY + "]] trailing",
        "Here: " + _REPLY + '], {"x": 1}',
        "[" + _REPLY + "], [1]",
    ],
    ids=["list_then_object", "list_closed_twice", "prose_then_object", "list_then_list"],
)
def test_text_after_a_closed_list_is_refused(text):
    assert _extract_json_from_text(text) is None


# --- cr2 P3-1: a sentence end must end the text ----------------------------


@pytest.mark.parametrize("place", ["", "["], ids=["leading", "list"])
@pytest.mark.parametrize("tail", [". Then", ".keys()", "!x", "?y"])
def test_sentence_end_must_end_the_text(place, tail):
    assert _extract_json_from_text(place + _REPLY + tail) is None


@pytest.mark.parametrize("tail", [".", "!", "?!", "..."])
def test_sentence_end_at_the_end_is_kept(tail):
    assert _extract_json_from_text(_REPLY + tail) == {"findings": ["first"]}


def test_duplicate_reply_key_is_a_refusal():
    with pytest.raises(DuplicateReplyKey) as caught:
        _loads_model_json(DUPLICATE)
    assert isinstance(caught.value, ReplyKeyRejected)
    assert caught.value.msg == 'Duplicate key "findings" in reply'


# cr3 P3-4: a cut reply that opened a list leaves the list unclosed.
@pytest.mark.parametrize("tail", ["", "\n", "  ", " done", "\n- note", " or y"])
def test_object_in_an_unclosed_list_is_refused(tail):
    text = '[{"findings": [], "code_excerpts": []}' + tail
    assert _extract_json_from_text(text, leading_only=True) is None
    assert _extract_json_from_text(text) is None


@pytest.mark.parametrize(
    "after",
    ["]", "]\n\nI found one issue.", ', {"x": 1}]', ", [1]]", "\n]"],
    ids=["closed", "closed_then_prose", "next_object", "next_list", "closed_on_a_line"],
)
def test_object_in_a_closed_list_or_before_another_item_is_kept(after):
    text = '[{"findings": [], "code_excerpts": []}' + after
    assert _extract_json_from_text(text, leading_only=True) == {"findings": [], "code_excerpts": []}


_EMPTY_REVIEW = '{"findings": [], "code_excerpts": []}'


@pytest.mark.parametrize("tail", [" d.keys()", " e.g", " a.b", " cfg.get(k)", " x.y z"])
def test_dotted_name_after_the_reply_is_refused(tail):
    # cr2 P3-1: a '.' ends a prose word only before whitespace or the end.
    assert _extract_json_from_text(_EMPTY_REVIEW + tail, ["findings"]) is None
    assert _extract_json_from_text("Note: " + _EMPTY_REVIEW + tail, ["findings"]) is None


@pytest.mark.parametrize("tail", [" Done.", " see a.py."])
def test_sentence_after_the_reply_is_kept(tail):
    assert _extract_json_from_text(_EMPTY_REVIEW + tail, ["findings"]) == json.loads(_EMPTY_REVIEW)


def test_open_literal_skips_braces_in_strings():
    # sec F3: the '}' inside "}" does not close the failed literal, so the
    # keyed object after it is still inside the literal.
    text = 'Example: {key: "}", inner: ' + _EMPTY_REVIEW
    literal = invoke._OpenLiteral(text)
    literal.open_at(text.index("{"))
    assert literal.holds(text.index('{"findings"'))
    assert _extract_json_from_text(text, ["findings"]) is None


def test_canary_generation_refuses_a_duplicate(tmp_path, capsys):
    # sec N2 / F1: the canary provider re-parses a string reply. json.loads
    # would keep the second "mutations" and return a mutation the model
    # never gave first; the provider falls back to templates instead.
    from code_forge.cli import _dispatch_inline_canary
    from code_forge.state import Verdict

    mutation = {"file": "a.py", "line": 1, "original": "a", "code": "b", "description": "d"}
    reply = '{"mutations": [], "mutations": [%s]}' % json.dumps(mutation)
    got = []

    def fake_canary(*, canary_provider, **_):
        got.append(canary_provider("diff"))
        return Verdict.DELEGATED, []

    mock = Mock(return_value=SimpleNamespace(content=reply))
    with (
        patch("code_forge.cli._load_canary_config", return_value={"n": 5}),
        patch("code_forge.backend.resolve_backend", return_value=MagicMock()),
        patch("code_forge.canary_gen.run_inline_canary", side_effect=fake_canary),
        patch("code_forge.llm_invoke.llm_invoke", mock),
    ):
        _dispatch_inline_canary("inline", MagicMock(), {}, None, {}, tmp_path)
    assert got == [[]]
    assert 'Duplicate key "mutations"' in capsys.readouterr().err
