"""A quote repair that invents a key fails closed (#264).

`_loads_model_json` repairs unescaped inner quotes. When an excerpt quotes a
dict literal such as `{"a": "b", "c": "d"}`, the repair can read `"c"` as a
new key and cut the excerpt short. Callers pass the keys their reply may
carry; a repaired parse with any other key is discarded, and an api backend
spends its JSON-correction retry instead.
"""

import importlib
import json
from functools import partial
from unittest.mock import MagicMock, Mock, patch

import pytest

from code_forge.backend import BackendConfig
from code_forge.falsify_real import _FALSIFY_REPLY_KEYS
from code_forge.llm_invoke import ReplyKeys, _loads_model_json, _unknown_reply_key, llm_invoke
from code_forge.reviewer_json import REVIEW_REPLY_KEYS
from code_forge.runtime import RUNTIME_REPLY_KEYS

invoke = importlib.import_module("code_forge.llm_invoke")

DICT_LITERAL = 'cfg = {"a": "b", "c": "d"}'
# The #264 reproduction: the excerpt quotes a dict literal unescaped.
REVIEW_REPRO = (
    '{"findings": [{"file": "a.py", "line": 1, "severity": "P2", "description": "d", '
    '"excerpt": "' + DICT_LITERAL + '"}], "code_excerpts": []}'
)
GOOD_REVIEW = {
    "findings": [
        {"file": "a.py", "line": 1, "severity": "P2", "description": "d", "excerpt": DICT_LITERAL}
    ],
    "code_excerpts": [],
}


def test_review_repro_repairs_into_a_cut_excerpt_without_keys():
    # Pins the hazard: unchecked, the repair succeeds and changes the excerpt.
    parsed = _loads_model_json(REVIEW_REPRO)
    finding = parsed["findings"][0]
    assert finding["excerpt"] == 'cfg = {"a": "b'
    assert finding["c"] == 'd"}'


def test_review_repro_fails_closed_with_review_keys():
    with pytest.raises(json.JSONDecodeError):
        _loads_model_json(REVIEW_REPRO, REVIEW_REPLY_KEYS)


@pytest.mark.parametrize(
    "text, keys",
    [
        ('{"verdict": "DISMISSED", "reasoning": "' + DICT_LITERAL + '"}', _FALSIFY_REPLY_KEYS),
        (
            '{"verdict": "DISMISSED", "reasoning": "r", '
            '"receipt": {"command": "echo ' + DICT_LITERAL + '", "output": "o"}}',
            _FALSIFY_REPLY_KEYS,
        ),
        (
            '{"surfaces": [], "findings": [{"file": "a", "line": 1, "surface": "s", '
            '"description": "' + DICT_LITERAL + '"}]}',
            RUNTIME_REPLY_KEYS,
        ),
    ],
    ids=["falsify-top", "falsify-receipt", "runtime-finding"],
)
def test_caller_schemas_reject_invented_keys(text, keys):
    assert _loads_model_json(text) is not None
    with pytest.raises(json.JSONDecodeError):
        _loads_model_json(text, keys)


def test_unrepaired_reply_with_unknown_key_still_parses():
    # A reply that needs no repair is never checked: models add keys and
    # validators decide what to do with them.
    text = '{"findings": [{"file": "a.py", "line": 1, "note": "x"}], "code_excerpts": [], "extra": 1}'
    assert _loads_model_json(text, REVIEW_REPLY_KEYS) == json.loads(text)


def test_repaired_reply_with_only_allowed_keys_still_parses():
    text = '{"verdict": "DISMISSED", "reasoning": "it says "hi" there"}'
    assert _loads_model_json(text, _FALSIFY_REPLY_KEYS) == {
        "verdict": "DISMISSED",
        "reasoning": 'it says "hi" there',
    }


def test_repaired_review_with_allowed_nested_keys_still_parses():
    text = (
        '{"findings": [{"file": "a.py", "line": 1, "severity": "P2", "description": "say "x"", '
        '"excerpt": "e", '
        '"code_excerpts": [{"file": "a.py", "start_line": 1, "end_line": 1, "content": "c"}]}], '
        '"code_excerpts": [{"file": "a.py", "start_line": 1, "end_line": 2, "content": "c"}], '
        '"context_quotes": [{"file": "a.py", "content": "q"}]}'
    )
    parsed = _loads_model_json(text, REVIEW_REPLY_KEYS)
    assert parsed["findings"][0]["description"] == 'say "x"'


KEYS = ReplyKeys(top=frozenset({"a", "b", "c"}), items={"a": frozenset({"x"}), "b": frozenset({"y"})})


@pytest.mark.parametrize(
    "parsed, unknown",
    [
        ({"a": [{"x": 1}], "b": {"y": 2}}, None),
        ({"z": 1}, "z"),
        ({"a": [{"x": 1}, {"x": 1, "w": 2}]}, "w"),
        ({"b": {"y": 1, "v": 2}}, "v"),
        ({"a": [{"x": 1, "q": 1, "p": 2}]}, "p"),
        # Members missing from `items` and non-dict items are not checked.
        ({"c": [{"anything": 1}], "a": ["text", 3]}, None),
        # Deeper levels are not checked.
        ({"a": [{"x": {"deep": 1}}]}, None),
        (["not", "a", "dict"], None),
    ],
)
def test_unknown_reply_key_levels(parsed, unknown):
    assert _unknown_reply_key(parsed, KEYS) == unknown


def test_default_items_is_empty():
    assert _unknown_reply_key({"a": [{"zz": 1}]}, ReplyKeys(top=frozenset({"a"}))) is None


def _api_backend():
    return BackendConfig(
        name="keys-test",
        type="api",
        format="openai",
        base_url="http://127.0.0.1:1/v1",
        api_key_env="KEYS_TEST_KEY",
        model="test",
        max_tokens=1024,
    )


def test_api_spends_json_correction_retry(monkeypatch):
    monkeypatch.setenv("KEYS_TEST_KEY", "local-test")
    request = Mock(
        side_effect=[
            (REVIEW_REPRO, {"prompt_tokens": 1, "completion_tokens": 2, "_forge_finish_reason": "stop"}),
            (json.dumps(GOOD_REVIEW), {"prompt_tokens": 1, "completion_tokens": 2}),
        ]
    )
    monkeypatch.setattr(invoke, "_invoke_openai", request)
    result = invoke._invoke_api("task", _api_backend(), 7, max_attempts=2, reply_keys=REVIEW_REPLY_KEYS)
    assert result.content == GOOD_REVIEW
    assert request.call_count == 2
    assert "JSON" in request.call_args_list[1].args[0]


def test_llm_invoke_passes_keys_to_api_backend(monkeypatch):
    monkeypatch.setenv("KEYS_TEST_KEY", "local-test")
    request = Mock(
        side_effect=[
            (REVIEW_REPRO, {"prompt_tokens": 1, "completion_tokens": 2, "_forge_finish_reason": "stop"}),
            (json.dumps(GOOD_REVIEW), {"prompt_tokens": 1, "completion_tokens": 2}),
        ]
    )
    monkeypatch.setattr(invoke, "_invoke_openai", request)
    monkeypatch.setattr(invoke.time, "sleep", Mock())
    result = llm_invoke("task", backend=_api_backend(), reply_keys=REVIEW_REPLY_KEYS)
    assert result.content == GOOD_REVIEW
    assert request.call_count == 2


def test_api_without_keys_keeps_the_repair(monkeypatch):
    monkeypatch.setenv("KEYS_TEST_KEY", "local-test")
    request = Mock(
        return_value=(
            REVIEW_REPRO,
            {"prompt_tokens": 1, "completion_tokens": 2, "_forge_finish_reason": "stop"},
        )
    )
    monkeypatch.setattr(invoke, "_invoke_openai", request)
    result = invoke._invoke_api("task", _api_backend(), 7, max_attempts=2)
    assert result.content["findings"][0]["c"] == 'd"}'
    assert request.call_count == 1


@pytest.fixture
def cli_binary():
    with patch("shutil.which", return_value="/usr/bin/claude"):
        yield


def _cli_proc(inner):
    proc = MagicMock()
    proc.returncode = 0
    proc.communicate.return_value = (
        json.dumps({"type": "result", "subtype": "success", "result": inner}),
        "",
    )
    proc.pid = 12345
    return proc


def test_cli_inner_result_falls_back_to_raw_text(cli_binary):
    backend = BackendConfig(name="cli-test", type="cli", model="m", command="")
    with patch("code_forge.llm_invoke.subprocess.Popen", return_value=_cli_proc(REVIEW_REPRO)):
        result = llm_invoke("prompt", backend=backend, reply_keys=REVIEW_REPLY_KEYS)
    assert result.content == REVIEW_REPRO


def test_cli_inner_result_without_keys_keeps_the_repair(cli_binary):
    backend = BackendConfig(name="cli-test", type="cli", model="m", command="")
    with patch("code_forge.llm_invoke.subprocess.Popen", return_value=_cli_proc(REVIEW_REPRO)):
        result = llm_invoke("prompt", backend=backend)
    assert result.content["findings"][0]["c"] == 'd"}'


_REVIEW_REPLY = {
    "findings": [],
    "code_excerpts": [{"file": "src/a.py", "start_line": 1, "end_line": 1, "content": "x"}],
}
_TEST_DIFF = "diff --git a/tests/test_a.py b/tests/test_a.py\n@@ -1 +1,2 @@\n x\n+y\n"


def _keys_of_every_call(mock):
    assert mock.call_count >= 1
    return {id(call.kwargs.get("reply_keys")) for call in mock.call_args_list}


def _invoke_mock(content):
    from code_forge.llm_invoke import LLMResult, Usage

    return Mock(return_value=LLMResult(content=content, usage=Usage(), duration_s=0.1))


def test_l1_provider_passes_review_keys():
    from pathlib import Path

    from code_forge.factories import build_l1_provider
    from code_forge.baseline import ResolvedReview

    resolved = ResolvedReview(
        source_files=[Path("src/a.py")], baseline_content=None, git_diff=_TEST_DIFF, mode_hint="git"
    )
    mock = _invoke_mock(json.dumps(_REVIEW_REPLY))
    with patch("code_forge.llm_invoke.llm_invoke", mock):
        build_l1_provider("real", resolved)()
    assert _keys_of_every_call(mock) == {id(REVIEW_REPLY_KEYS)}


def test_subagent_spawn_passes_review_keys():
    from code_forge.cli import _make_subagent_spawn

    mock = _invoke_mock(_REVIEW_REPLY)
    with patch("code_forge.llm_invoke.llm_invoke", mock):
        _make_subagent_spawn(backend=None, conv_digest="", post_image="")("qodo", _TEST_DIFF)
    assert _keys_of_every_call(mock) == {id(REVIEW_REPLY_KEYS)}


def test_test_assertion_review_passes_review_keys():
    from code_forge.cli import _run_test_assertion_review

    mock = _invoke_mock(_REVIEW_REPLY)
    with patch("code_forge.llm_invoke.llm_invoke", mock):
        _run_test_assertion_review(_TEST_DIFF, backend=None)
    assert _keys_of_every_call(mock) == {id(REVIEW_REPLY_KEYS)}


def test_runtime_runner_passes_runtime_keys(tmp_path):
    from code_forge.runtime import RuntimeRunner

    mock = _invoke_mock({"surfaces": [], "findings": []})
    with patch("code_forge.runtime.llm_invoke", mock):
        RuntimeRunner(backend=MagicMock()).run("diff --git a/r.sh b/r.sh\n+nft add table", tmp_path)
    assert _keys_of_every_call(mock) == {id(RUNTIME_REPLY_KEYS)}


def test_falsifier_passes_falsify_keys():
    from code_forge.disposition import Disposition
    from code_forge.falsify_real import RealFalsifier
    from code_forge.state import StateFinding

    finding = StateFinding(
        id="fp-1",
        fingerprint="fp-1",
        source="L1",
        disposition=Disposition.CONFIRMED,
        file="src/foo.py",
        line_range=[1, 2],
        description="d",
    )
    mock = _invoke_mock({"verdict": "CONFIRMED", "reasoning": "r"})
    with patch("code_forge.falsify_real.llm_invoke", mock):
        RealFalsifier().falsify(finding)
    assert _keys_of_every_call(mock) == {id(_FALSIFY_REPLY_KEYS)}


def _bare_cli_proc(stdout):
    proc = MagicMock()
    proc.returncode = 0
    proc.communicate.return_value = (stdout, "")
    proc.pid = 12345
    return proc


def test_cli_bare_stdout_falls_back_to_raw_text(cli_binary):
    # A CLI or proxy that prints the model's JSON with no result envelope.
    backend = BackendConfig(name="cli-test", type="cli", model="m", command="")
    with patch("code_forge.llm_invoke.subprocess.Popen", return_value=_bare_cli_proc(REVIEW_REPRO)):
        result = llm_invoke("prompt", backend=backend, reply_keys=REVIEW_REPLY_KEYS)
    assert result.content == REVIEW_REPRO


def test_cli_bare_stdout_without_keys_keeps_the_repair(cli_binary):
    backend = BackendConfig(name="cli-test", type="cli", model="m", command="")
    with patch("code_forge.llm_invoke.subprocess.Popen", return_value=_bare_cli_proc(REVIEW_REPRO)):
        result = llm_invoke("prompt", backend=backend)
    assert result.content["findings"][0]["c"] == 'd"}'


@pytest.mark.parametrize("keys", [None, REVIEW_REPLY_KEYS], ids=["no_keys", "review_keys"])
def test_cli_stream_result_event_without_result_is_kept(cli_binary, keys):
    # A result event with no "result" member is CLI output, as on main: the
    # bare-stdout re-parse must not swap it for the whole stream array.
    backend = BackendConfig(name="cli-test", type="cli", model="m", command="")
    stdout = '[{"type": "system"}, {"type": "result", "subtype": "success"}]'
    with patch("code_forge.llm_invoke.subprocess.Popen", return_value=_bare_cli_proc(stdout)):
        result = llm_invoke("prompt", backend=backend, reply_keys=keys)
    assert result.content == {"type": "result", "subtype": "success"}


def test_cli_bare_stdout_with_allowed_keys_still_parses(cli_binary):
    backend = BackendConfig(name="cli-test", type="cli", model="m", command="")
    stdout = '{"verdict": "DISMISSED", "reasoning": "it says "hi" there"}'
    with patch("code_forge.llm_invoke.subprocess.Popen", return_value=_bare_cli_proc(stdout)):
        result = llm_invoke("prompt", backend=backend, reply_keys=_FALSIFY_REPLY_KEYS)
    assert result.content == {"verdict": "DISMISSED", "reasoning": 'it says "hi" there'}


# Each item list in REVIEW_REPLY_KEYS rejects a repair that lands in it.
@pytest.mark.parametrize(
    "text",
    [
        '{"findings": [], "code_excerpts": [{"file": "a.py", "start_line": 1, "end_line": 1, '
        '"content": "' + DICT_LITERAL + '"}]}',
        '{"findings": [], "code_excerpts": [], "context_quotes": [{"file": "a.py", '
        '"content": "' + DICT_LITERAL + '"}]}',
    ],
    ids=["code_excerpts", "context_quotes"],
)
def test_review_item_lists_reject_invented_keys(text):
    assert _loads_model_json(text)
    with pytest.raises(json.JSONDecodeError):
        _loads_model_json(text, REVIEW_REPLY_KEYS)


def test_repaired_falsify_reply_may_carry_a_receipt():
    text = (
        '{"verdict": "CONFIRMED", "reasoning": "it says "hi" there", '
        '"receipt": {"command": "pytest", "output": "1 failed"}}'
    )
    assert _loads_model_json(text, _FALSIFY_REPLY_KEYS)["receipt"] == {
        "command": "pytest",
        "output": "1 failed",
    }


def test_repaired_runtime_reply_may_carry_surfaces():
    text = (
        '{"surfaces": ["cli"], "findings": [{"file": "a", "line": 1, "surface": "cli", '
        '"description": "say "x""}]}'
    )
    assert _loads_model_json(text, RUNTIME_REPLY_KEYS)["surfaces"] == ["cli"]


# reply_keys must reach every parse in a call. Each test below sends REVIEW_REPRO
# through one site. With the keys, the repair is refused; without them at that
# site, the repaired dict with the invented key would come back as the result.

STOP = {"prompt_tokens": 1, "completion_tokens": 2, "_forge_finish_reason": "stop"}


def _truncated(content):
    return invoke._TruncatedResponse(
        "cut", content=content, usage_data={}, resolved_cap=1024, kind="truncated", retryable=False
    )


def _wide_backend():
    return BackendConfig(
        name="keys-test",
        type="api",
        format="openai",
        base_url="http://127.0.0.1:1/v1",
        api_key_env="KEYS_TEST_KEY",
        model="test",
        max_tokens=1024,
        output_token_limit=4096,
    )


@pytest.fixture
def api_env(monkeypatch):
    monkeypatch.setenv("KEYS_TEST_KEY", "local-test")
    monkeypatch.setattr(invoke.time, "sleep", Mock())


def test_continue_partial_parse_uses_keys(api_env):
    with pytest.raises(invoke.LLMInvokeError):
        invoke._continue_truncated(
            "p",
            _api_backend(),
            "k",
            7,
            _truncated(REVIEW_REPRO),
            None,
            budget=0,
            reply_keys=REVIEW_REPLY_KEYS,
        )


def test_continue_combined_parse_uses_keys(api_env, monkeypatch):
    cut = 40
    monkeypatch.setattr(invoke, "_invoke_openai", Mock(return_value=(REVIEW_REPRO[cut:], STOP)))
    with pytest.raises(invoke.LLMInvokeError):
        invoke._continue_truncated(
            "p",
            _api_backend(),
            "k",
            7,
            _truncated(REVIEW_REPRO[:cut]),
            None,
            budget=1,
            reply_keys=REVIEW_REPLY_KEYS,
        )


def test_wider_retry_parse_uses_keys(api_env, monkeypatch):
    request = Mock(return_value=(REVIEW_REPRO, STOP))
    monkeypatch.setattr(invoke, "_invoke_openai", request)
    assert (
        invoke._retry_with_more_headroom("p", _wide_backend(), "k", 7, None, REVIEW_REPLY_KEYS) is None
    )
    assert request.call_count == 1


def test_api_passes_keys_to_continuation(api_env, monkeypatch):
    request = Mock(side_effect=[_truncated(REVIEW_REPRO), ("", STOP), ("", STOP)])
    monkeypatch.setattr(invoke, "_invoke_openai", request)
    with pytest.raises(invoke.LLMInvokeError):
        invoke._invoke_api("task", _api_backend(), 7, max_attempts=1, reply_keys=REVIEW_REPLY_KEYS)
    assert request.call_count == 3


def test_api_passes_keys_to_wider_retry(api_env, monkeypatch):
    cut = 40
    request = Mock(
        side_effect=[_truncated(REVIEW_REPRO[:cut]), ("", STOP), ("", STOP), (REVIEW_REPRO, STOP)]
    )
    monkeypatch.setattr(invoke, "_invoke_openai", request)
    with pytest.raises(invoke.LLMInvokeError):
        invoke._invoke_api("task", _wide_backend(), 7, max_attempts=1, reply_keys=REVIEW_REPLY_KEYS)
    assert request.call_count == 4


# The follow-up quotes a dict literal in its excerpt; the repair would cut it
# and add a key `c` to the excerpt object.
EXCERPT_REPRO = (
    '{"code_excerpts": [{"file": "a.py", "start_line": 1, "end_line": 1, '
    '"content": "' + DICT_LITERAL + '"}]}'
)
NO_EXCERPTS = {"findings": [{"file": "a.py", "line": 1, "severity": "P2", "description": "d"}]}


def test_excerpt_repair_api_follow_up_uses_keys(api_env, monkeypatch):
    monkeypatch.setattr(invoke, "_invoke_openai", Mock(return_value=(EXCERPT_REPRO, STOP)))
    repaired, _usage, _duration = invoke._repair_missing_excerpts(
        NO_EXCERPTS, "task", _api_backend(), 7, reply_keys=REVIEW_REPLY_KEYS
    )
    assert repaired is NO_EXCERPTS


def test_llm_invoke_passes_keys_to_excerpt_repair(cli_binary):
    backend = BackendConfig(name="cli-test", type="cli", model="m", command="")
    procs = [_cli_proc(json.dumps(NO_EXCERPTS)), _cli_proc(EXCERPT_REPRO)]
    with patch("code_forge.llm_invoke.subprocess.Popen", side_effect=procs):
        result = llm_invoke("prompt", backend=backend, reply_keys=REVIEW_REPLY_KEYS)
    assert result.content == NO_EXCERPTS


def test_refused_repair_raises_reply_key_rejected_at_the_original_error():
    with pytest.raises(json.JSONDecodeError) as plain:
        json.loads(REVIEW_REPRO, strict=False)
    with pytest.raises(invoke.ReplyKeyRejected) as refused:
        _loads_model_json(REVIEW_REPRO, REVIEW_REPLY_KEYS)
    assert (refused.value.msg, refused.value.pos) == (plain.value.msg, plain.value.pos)


# A real P1 whose excerpt quotes, unescaped, an object of the reply's own shape
# and then a dict literal that makes the repair invent a key. Scanning the
# refused text for an embedded object finds the quoted empty review.
FRAGMENT = 'x = {"findings": [], "code_excerpts": []}; y = {"a": "b", "c": "d"}'
FRAGMENT_REPRO = (
    '{"findings": [{"file": "a.py", "line": 3, "severity": "P1", "description": "d", '
    '"excerpt": "' + FRAGMENT + '"}], "code_excerpts": []}'
)
REAL_P1 = {
    "findings": [{"file": "a.py", "line": 3, "severity": "P1", "description": "d", "excerpt": FRAGMENT}],
    "code_excerpts": [],
}


def test_fragment_repro_hides_an_empty_review():
    with pytest.raises(json.JSONDecodeError):
        _loads_model_json(FRAGMENT_REPRO, REVIEW_REPLY_KEYS)
    # The hazard: the quoted object decodes on its own, but the scan skips it.
    quoted = invoke._model_json_decoder().raw_decode(FRAGMENT_REPRO, FRAGMENT_REPRO.index("x = {") + 4)
    assert quoted[0] == {"findings": [], "code_excerpts": []}
    assert invoke._extract_json_from_text(FRAGMENT_REPRO) is None


def test_api_retries_instead_of_returning_a_quoted_fragment(api_env, monkeypatch):
    request = Mock(side_effect=[(FRAGMENT_REPRO, STOP), (json.dumps(REAL_P1), STOP)])
    monkeypatch.setattr(invoke, "_invoke_openai", request)
    result = invoke._invoke_api("task", _api_backend(), 7, max_attempts=2, reply_keys=REVIEW_REPLY_KEYS)
    assert result.content == REAL_P1
    assert request.call_count == 2


def test_wider_retry_does_not_return_a_quoted_fragment(api_env, monkeypatch):
    monkeypatch.setattr(invoke, "_invoke_openai", Mock(return_value=(FRAGMENT_REPRO, STOP)))
    assert (
        invoke._retry_with_more_headroom("p", _wide_backend(), "k", 7, None, REVIEW_REPLY_KEYS) is None
    )


def test_continue_partial_does_not_return_a_quoted_fragment(api_env):
    with pytest.raises(invoke.LLMInvokeError):
        invoke._continue_truncated(
            "p",
            _api_backend(),
            "k",
            7,
            _truncated(FRAGMENT_REPRO),
            None,
            budget=0,
            reply_keys=REVIEW_REPLY_KEYS,
        )


def test_continue_combined_does_not_return_a_quoted_fragment(api_env, monkeypatch):
    cut = 40
    monkeypatch.setattr(invoke, "_invoke_openai", Mock(return_value=(FRAGMENT_REPRO[cut:], STOP)))
    with pytest.raises(invoke.LLMInvokeError):
        invoke._continue_truncated(
            "p",
            _api_backend(),
            "k",
            7,
            _truncated(FRAGMENT_REPRO[:cut]),
            None,
            budget=1,
            reply_keys=REVIEW_REPLY_KEYS,
        )


# A CLI envelope whose result is the model's object, not a string. When the
# model text inside it is unescaped, the envelope parse itself is the repair.
REPAIRED_ENVELOPE = '{"type": "result", "subtype": "success", "result": ' + REVIEW_REPRO + "}"
REPAIRED_STREAM = '[{"type": "system"}, ' + REPAIRED_ENVELOPE + "]"


@pytest.mark.parametrize("stdout", [REPAIRED_ENVELOPE, REPAIRED_STREAM], ids=["envelope", "stream"])
def test_cli_repaired_object_result_falls_back_to_raw_text(cli_binary, stdout):
    backend = BackendConfig(name="cli-test", type="cli", model="m", command="")
    with patch("code_forge.llm_invoke.subprocess.Popen", return_value=_bare_cli_proc(stdout)):
        result = llm_invoke("prompt", backend=backend, reply_keys=REVIEW_REPLY_KEYS)
    assert result.content == stdout
    with patch("code_forge.llm_invoke.subprocess.Popen", return_value=_bare_cli_proc(stdout)):
        result = llm_invoke("prompt", backend=backend)
    assert result.content["findings"][0]["c"] == 'd"}'


def test_cli_unrepaired_object_result_is_not_checked(cli_binary):
    # Valid JSON from the CLI needed no repair; extra keys are the caller's to judge.
    reply = {"findings": [], "code_excerpts": [], "note": "n"}
    stdout = json.dumps({"type": "result", "subtype": "success", "result": reply})
    backend = BackendConfig(name="cli-test", type="cli", model="m", command="")
    with patch("code_forge.llm_invoke.subprocess.Popen", return_value=_bare_cli_proc(stdout)):
        result = llm_invoke("prompt", backend=backend, reply_keys=REVIEW_REPLY_KEYS)
    assert result.content == reply


# runtime._parse_llm_response unwraps a one-object list, so the list is checked too.
RUNTIME_LIST_REPRO = (
    '[{"surfaces": [], "findings": [{"file": "a", "line": 1, "description": "' + DICT_LITERAL + '"}]}]'
)
GOOD_RUNTIME = {"surfaces": [], "findings": [{"file": "a", "line": 1, "description": DICT_LITERAL}]}


def test_top_level_list_items_are_checked():
    assert _unknown_reply_key([{"surfaces": []}, "s", {"x": 1}], RUNTIME_REPLY_KEYS) == "x"
    assert _unknown_reply_key([{"surfaces": [], "findings": []}], RUNTIME_REPLY_KEYS) is None
    with pytest.raises(json.JSONDecodeError):
        _loads_model_json(RUNTIME_LIST_REPRO, RUNTIME_REPLY_KEYS)


def test_cli_runtime_list_reply_is_refused(cli_binary):
    from code_forge.runtime import _parse_llm_response

    backend = BackendConfig(name="cli-test", type="cli", model="m", command="")
    with patch(
        "code_forge.llm_invoke.subprocess.Popen", return_value=_bare_cli_proc(RUNTIME_LIST_REPRO)
    ):
        result = llm_invoke("prompt", backend=backend, reply_keys=RUNTIME_REPLY_KEYS)
    assert result.content == RUNTIME_LIST_REPRO
    with pytest.raises(ValueError):
        _parse_llm_response(result.content)


def test_api_runtime_list_reply_spends_correction_retry(api_env, monkeypatch):
    request = Mock(side_effect=[(RUNTIME_LIST_REPRO, STOP), (json.dumps(GOOD_RUNTIME), STOP)])
    monkeypatch.setattr(invoke, "_invoke_openai", request)
    result = invoke._invoke_api("task", _api_backend(), 7, max_attempts=2, reply_keys=RUNTIME_REPLY_KEYS)
    assert result.content == GOOD_RUNTIME
    assert request.call_count == 2


def test_llm_invoke_keeps_the_real_finding(api_env, monkeypatch):
    request = Mock(side_effect=[(FRAGMENT_REPRO, STOP), (json.dumps(REAL_P1), STOP)])
    monkeypatch.setattr(invoke, "_invoke_openai", request)
    result = llm_invoke("task", backend=_api_backend(), reply_keys=REVIEW_REPLY_KEYS)
    assert result.content == REAL_P1
    assert request.call_count == 2


# Cross-check repros for a repair that fails without a key refusal. The excerpt
# quotes an object of the reply's shape; the text after it, or a quoted key,
# makes the repair fail. Scanning the text then found the quoted empty review,
# and the real P1 came back as a clean review after one call, with or without
# keys.
QUOTED_REVIEW = (
    'x = {"findings": [], "code_excerpts": [{"file": "a.py", "start_line": 1, '
    '"end_line": 1, "content": "c"}]}'
)
TAILS = {"call": '; f("a", "b")', "list": '; y = ["a","b"]', "print": '; print("], ", "x")'}


def _p1(excerpt, description="d"):
    finding = {"file": "a.py", "line": 3, "severity": "P1", "description": description}
    return {"findings": [dict(finding, excerpt=excerpt)], "code_excerpts": []}


def _unescaped(reply):
    # The model's text: the excerpt and description quotes are left unescaped.
    finding = reply["findings"][0]
    return (
        '{"findings": [{"file": "a.py", "line": 3, "severity": "P1", "description": "'
        + finding["description"]
        + '", "excerpt": "'
        + finding["excerpt"]
        + '"}], "code_excerpts": []}'
    )


REFUSED = {name: _p1(QUOTED_REVIEW + tail) for name, tail in TAILS.items()}
REFUSED["quoted_key"] = _p1(QUOTED_REVIEW, description='returns, ",", ok, empty')


@pytest.mark.parametrize("name", sorted(REFUSED))
def test_failed_repair_raises_repair_refused(name):
    text = _unescaped(REFUSED[name])
    for keys in (None, REVIEW_REPLY_KEYS):
        with pytest.raises(invoke.RepairRefused) as refused:
            _loads_model_json(text, keys)
        assert not isinstance(refused.value, invoke.ReplyKeyRejected)
    # The hazard: the quoted object decodes on its own, but the full scan skips it.
    quoted, _ = invoke._model_json_decoder().raw_decode(text, text.index("x = {") + 4)
    assert quoted["findings"] == []
    assert invoke._extract_json_from_text(text) is None


def test_quoted_key_refusal_keeps_the_original_error():
    text = _unescaped(REFUSED["quoted_key"])
    with pytest.raises(json.JSONDecodeError) as plain:
        json.loads(text, strict=False)
    with pytest.raises(invoke.RepairRefused) as refused:
        _loads_model_json(text)
    assert (refused.value.msg, refused.value.pos) == (plain.value.msg, plain.value.pos)


def test_failed_repair_keeps_the_later_error():
    # Same message and position as before the subclass: the error after the repair.
    text = _unescaped(REFUSED["call"])
    with pytest.raises(json.JSONDecodeError) as plain:
        json.loads(text, strict=False)
    with pytest.raises(invoke.RepairRefused) as refused:
        _loads_model_json(text)
    assert refused.value.pos > plain.value.pos
    assert refused.value.msg == "Expecting ',' delimiter"


def test_unrepaired_syntax_error_stays_plain():
    with pytest.raises(json.JSONDecodeError) as plain:
        _loads_model_json('Here: {"findings": []}')
    assert type(plain.value) is json.JSONDecodeError


@pytest.mark.parametrize("keys", [None, REVIEW_REPLY_KEYS], ids=["no_keys", "keys"])
@pytest.mark.parametrize("name", sorted(REFUSED))
def test_api_retries_after_a_failed_repair(api_env, monkeypatch, name, keys):
    real = REFUSED[name]
    request = Mock(side_effect=[(_unescaped(real), STOP), (json.dumps(real), STOP)])
    monkeypatch.setattr(invoke, "_invoke_openai", request)
    result = invoke._invoke_api("task", _api_backend(), 7, max_attempts=2, reply_keys=keys)
    assert result.content == real
    assert request.call_count == 2


@pytest.mark.parametrize("keys", [None, REVIEW_REPLY_KEYS], ids=["no_keys", "keys"])
@pytest.mark.parametrize("name", sorted(REFUSED))
def test_llm_invoke_keeps_the_p1_after_a_failed_repair(api_env, monkeypatch, name, keys):
    real = REFUSED[name]
    request = Mock(side_effect=[(_unescaped(real), STOP), (json.dumps(real), STOP)])
    monkeypatch.setattr(invoke, "_invoke_openai", request)
    result = llm_invoke("task", backend=_api_backend(), reply_keys=keys)
    assert result.content == real
    assert request.call_count == 2


def test_wider_retry_does_not_scan_a_failed_repair(api_env, monkeypatch):
    text = _unescaped(REFUSED["call"])
    monkeypatch.setattr(invoke, "_invoke_openai", Mock(return_value=(text, STOP)))
    assert invoke._retry_with_more_headroom("p", _wide_backend(), "k", 7, None, None) is None


def test_continue_partial_does_not_scan_a_failed_repair(api_env):
    text = _unescaped(REFUSED["call"])
    with pytest.raises(invoke.LLMInvokeError):
        invoke._continue_truncated("p", _api_backend(), "k", 7, _truncated(text), None, budget=0)


def test_continue_combined_does_not_scan_a_failed_repair(api_env, monkeypatch):
    text = _unescaped(REFUSED["call"])
    cut = 40
    monkeypatch.setattr(invoke, "_invoke_openai", Mock(return_value=(text[cut:], STOP)))
    with pytest.raises(invoke.LLMInvokeError):
        invoke._continue_truncated("p", _api_backend(), "k", 7, _truncated(text[:cut]), None, budget=1)


FALSIFY_KEYS = frozenset({"verdict", "reasoning"})
CUT_FALSIFY = '{"verdict": "DISMISSED", "reasoning": "uses "x" and'


def test_failed_repair_still_salvages_the_leading_object():
    # A cut falsify reply with an unescaped quote is still the reply's own object.
    with pytest.raises(invoke.RepairRefused) as refused:
        _loads_model_json(CUT_FALSIFY)
    assert invoke._fallback_reply(CUT_FALSIFY, refused.value, FALSIFY_KEYS) == {"verdict": "DISMISSED"}


def test_leading_only_skips_a_later_object():
    text = 'note {"verdict": "DISMISSED", "reasoning": "r"}'
    assert invoke._extract_json_from_text(text, FALSIFY_KEYS)["verdict"] == "DISMISSED"
    assert invoke._extract_json_from_text(text, FALSIFY_KEYS, leading_only=True) is None
    leading = ' [{"verdict": "DISMISSED", "reasoning": "r"}]'
    found = invoke._extract_json_from_text(leading, FALSIFY_KEYS, leading_only=True)
    assert found["verdict"] == "DISMISSED"
    other = '{"note": 1} {"verdict": "DISMISSED", "reasoning": "r"}'
    assert invoke._extract_json_from_text(other, FALSIFY_KEYS, leading_only=True) is None


def test_plain_error_still_scans_the_text(api_env, monkeypatch):
    # Prose before the reply fails at the first character, with no repair tried.
    good = json.dumps(GOOD_REVIEW)
    request = Mock(return_value=("Here is the review: " + good, STOP))
    monkeypatch.setattr(invoke, "_invoke_openai", request)
    result = invoke._invoke_api("task", _api_backend(), 7, max_attempts=2, reply_keys=REVIEW_REPLY_KEYS)
    assert result.content == GOOD_REVIEW
    assert request.call_count == 1


def test_cli_repaired_object_result_with_known_keys_is_kept(cli_binary):
    # The envelope needs quote repair, but the repaired result has only reply keys.
    result_text = (
        '{"findings": [{"file": "a.py", "line": 1, "severity": "P2", "description": "say "hi" now"}], '
        '"code_excerpts": []}'
    )
    stdout = '{"type": "result", "subtype": "success", "result": ' + result_text + "}"
    backend = BackendConfig(name="cli-test", type="cli", model="m", command="")
    with patch("code_forge.llm_invoke.subprocess.Popen", return_value=_bare_cli_proc(stdout)):
        result = llm_invoke("prompt", backend=backend, reply_keys=REVIEW_REPLY_KEYS)
    expected = {
        "findings": [{"file": "a.py", "line": 1, "severity": "P2", "description": 'say "hi" now'}],
        "code_excerpts": [],
    }
    assert result.content == expected


def test_cli_raw_newline_envelope_is_not_a_repair(cli_binary):
    # A raw newline is not a quote repair, so the model's own extra key is kept.
    stdout = (
        '{"type": "result", "subtype": "success", '
        '"result": {"findings": [], "code_excerpts": [], "note": "a\nb"}}'
    )
    backend = BackendConfig(name="cli-test", type="cli", model="m", command="")
    with patch("code_forge.llm_invoke.subprocess.Popen", return_value=_bare_cli_proc(stdout)):
        result = llm_invoke("prompt", backend=backend, reply_keys=REVIEW_REPLY_KEYS)
    assert result.content == {"findings": [], "code_excerpts": [], "note": "a\nb"}


def test_key_refusal_tries_no_object_at_all():
    # The leading object would salvage a verdict, but a key refusal goes to the retry.
    text = '{"verdict": "DISMISSED", "reasoning": "a "b", "c": "d"}'
    with pytest.raises(invoke.ReplyKeyRejected) as refused:
        _loads_model_json(text, _FALSIFY_REPLY_KEYS)
    salvaged = invoke._extract_json_from_text(text, FALSIFY_KEYS, leading_only=True)
    assert salvaged == {"verdict": "DISMISSED"}
    assert invoke._fallback_reply(text, refused.value, FALSIFY_KEYS) is None


def test_repair_refused_is_the_base_of_reply_key_rejected():
    assert issubclass(invoke.ReplyKeyRejected, invoke.RepairRefused)
    assert issubclass(invoke.RepairRefused, json.JSONDecodeError)


def test_budget_exhaustion_raises_repair_refused():
    with pytest.raises(invoke.RepairRefused):
        _loads_model_json('{"a"}')
    noise = '"}{"findings": []}},]]['
    with pytest.raises(invoke.RepairRefused) as refused:
        _loads_model_json(noise)
    assert invoke._fallback_reply(noise, refused.value, None) is None


def test_budget_exhaustion_tries_only_the_leading_object():
    # The full scan would return the object after the stray quote.
    text = '"{"findings": []}'
    with pytest.raises(invoke.RepairRefused) as refused:
        _loads_model_json(text)
    assert invoke._extract_json_from_text(text) == {"findings": []}
    assert invoke._fallback_reply(text, refused.value, None) is None


def test_object_followed_by_prose_is_recovered(api_env, monkeypatch):
    good = json.dumps(GOOD_REVIEW)
    text = good + "\n\nThe excerpt quotes {the config}; nothing else stands out."
    with pytest.raises(json.JSONDecodeError) as plain:
        _loads_model_json(text)
    assert invoke._fallback_reply(text, plain.value, None) == GOOD_REVIEW
    request = Mock(return_value=(text, STOP))
    monkeypatch.setattr(invoke, "_invoke_openai", request)
    result = invoke._invoke_api("task", _api_backend(), 7, max_attempts=2, reply_keys=REVIEW_REPLY_KEYS)
    assert result.content == GOOD_REVIEW
    assert request.call_count == 1


def _model_text(reply):
    # The model's text: every quote inside a string value is left unescaped.
    return json.dumps(reply).replace('\\"', '"')


_8C_EXCERPTS = [{"file": "a.py", "start_line": 1, "end_line": 1, "content": 'x"}], "findings": []}'}]
_8C_REAL = {"code_excerpts": _8C_EXCERPTS, "findings": _p1("y")["findings"]}
_TRAILING = {
    "findings": [
        {"file": "a.py", "line": 2, "severity": "P3", "description": "z"},
        *_p1(QUOTED_REVIEW)["findings"],
    ],
    "code_excerpts": [],
}
# Replies whose parse fails before any quote repair, so the error stays plain
# and the full scan runs. Each holds a quoted object of the reply's own shape.
PLAIN_ERROR = {
    "prose_led": ("Here is the review: " + _model_text(_p1(QUOTED_REVIEW)), _p1(QUOTED_REVIEW)),
    "prose_led_call": (
        "Here is the review: " + _model_text(_p1(QUOTED_REVIEW + TAILS["call"])),
        _p1(QUOTED_REVIEW + TAILS["call"]),
    ),
    "python_true": (
        _model_text(_p1(QUOTED_REVIEW)).replace('"line": 3', '"line": True'),
        _p1(QUOTED_REVIEW),
    ),
    "trailing_comma": (
        _model_text(_TRAILING).replace('"description": "z"}', '"description": "z",}'),
        _TRAILING,
    ),
    "excerpts_first": (_model_text(_8C_REAL), _8C_REAL),
}
# Code that opens a brace after the quoted object balances the '}' that
# closes the excerpt, so a brace count cannot tell the object was quoted.
for _n in (1, 2, 3):
    _opens = _p1("if (cfg == '" + QUOTED_REVIEW[len("x = ") :] + "') " + "{" * _n)
    PLAIN_ERROR["prose_led_opens_%d" % _n] = ("Here is the review: " + _model_text(_opens), _opens)


@pytest.mark.parametrize("name", sorted(PLAIN_ERROR))
def test_plain_error_skips_an_enclosed_object(name):
    text, _ = PLAIN_ERROR[name]
    with pytest.raises(json.JSONDecodeError) as plain:
        _loads_model_json(text, REVIEW_REPLY_KEYS)
    assert type(plain.value) is json.JSONDecodeError
    assert invoke._fallback_reply(text, plain.value, None) is None


def test_excerpts_first_leading_object_holds_an_empty_review():
    # The hazard: the leading object decodes, closed early by the quoted "}]".
    text, _ = PLAIN_ERROR["excerpts_first"]
    leading, end = invoke._model_json_decoder().raw_decode(text, 0)
    assert leading["findings"] == []
    assert end < len(text)


def _p1_or_fail_closed(call, real, request):
    # A reply that opens with '{' gets the JSON-correction retry; prose before
    # it does not, and the call fails closed. Neither returns a clean review.
    try:
        result = call()
    except invoke.InvalidJSONResponseError:
        assert request.call_count == 1
        return "failed_closed"
    assert result.content == real
    assert request.call_count == 2
    return "retried"


@pytest.mark.parametrize("keys", [None, REVIEW_REPLY_KEYS], ids=["no_keys", "keys"])
@pytest.mark.parametrize("name", sorted(PLAIN_ERROR))
def test_api_does_not_return_an_enclosed_object(api_env, monkeypatch, name, keys):
    text, real = PLAIN_ERROR[name]
    request = Mock(side_effect=[(text, STOP), (json.dumps(real), STOP)])
    monkeypatch.setattr(invoke, "_invoke_openai", request)
    call = partial(invoke._invoke_api, "task", _api_backend(), 7, max_attempts=2, reply_keys=keys)
    outcome = _p1_or_fail_closed(call, real, request)
    assert outcome == ("failed_closed" if name.startswith("prose_led") else "retried")


@pytest.mark.parametrize("keys", [None, REVIEW_REPLY_KEYS], ids=["no_keys", "keys"])
@pytest.mark.parametrize("name", sorted(PLAIN_ERROR))
def test_llm_invoke_does_not_return_an_enclosed_object(api_env, monkeypatch, name, keys):
    text, real = PLAIN_ERROR[name]
    request = Mock(side_effect=[(text, STOP), (json.dumps(real), STOP)])
    monkeypatch.setattr(invoke, "_invoke_openai", request)
    call = partial(llm_invoke, "task", backend=_api_backend(), reply_keys=keys)
    outcome = _p1_or_fail_closed(call, real, request)
    assert outcome == ("failed_closed" if name.startswith("prose_led") else "retried")


SALVAGE_INJECTED = {
    "duplicate_verdict": (
        '{"verdict": "CONFIRMED", "reasoning": "the stub returns a", '
        '"verdict": "DISMISSED", "reasoning": "x and'
    ),
    "reasoning_first": '{"reasoning": "the stub returns a", "verdict": "DISMISSED", "reasoning": "x and',
    "reasoning_first_once": '{"reasoning": "the stub returns a", "verdict": "DISMISSED", "x": "y and',
}


@pytest.mark.parametrize("name", sorted(SALVAGE_INJECTED))
def test_salvage_refuses_a_verdict_from_quoted_content(name):
    text = SALVAGE_INJECTED[name]
    with pytest.raises(json.JSONDecodeError) as error:
        _loads_model_json(text, _FALSIFY_REPLY_KEYS)
    assert invoke._fallback_reply(text, error.value, FALSIFY_KEYS) is None
    assert invoke._extract_json_from_text(text, FALSIFY_KEYS) is None


def test_salvage_refuses_a_duplicate_key():
    text = '{"verdict": "CONFIRMED", "reasoning": "a", "verdict": "DISMISSED", "reasoning": "x and'
    decoder = invoke._model_json_decoder()
    assert invoke._salvage_truncated_object(text, 0, decoder) is None
    once = '{"verdict": "CONFIRMED", "reasoning": "a", "note": "x and'
    kept = {"verdict": "CONFIRMED", "reasoning": "a"}
    assert invoke._salvage_truncated_object(once, 0, decoder) == kept


def test_salvage_still_reads_a_leading_verdict():
    text = '{"verdict": "CONFIRMED", "reasoning": "the stub", "receipt": {"command": "x'
    assert invoke._extract_json_from_text(text, FALSIFY_KEYS) == {
        "verdict": "CONFIRMED",
        "reasoning": "the stub",
    }


@pytest.mark.parametrize(
    "after",
    [" }", " } {", ' "}', '"} {"a": 1}'],
    ids=["one_close", "close_then_open", "quote_close", "close_then_object"],
)
def test_object_before_an_unopened_close_is_skipped(after):
    # The '}' closes something the object sat inside; a later '{' cannot open it.
    assert invoke._extract_json_from_text('{"findings": []}' + after) is None


def test_object_before_balanced_braces_is_kept():
    text = '{"findings": []} then {"a": 1} and {b}'
    assert invoke._extract_json_from_text(text) == {"findings": []}


_VALID = {
    "findings": [{"file": "t.py", "line": 3, "severity": "P1", "description": "bad"}],
    "code_excerpts": [{"file": "t.py", "start_line": 3, "end_line": 3, "content": "if (a) { b(); }"}],
}
_STRAY = "\nThe stray `}` on line 9 closes the class early."
VALID_THEN_PROSE = {
    "prose_before_and_after": "Here is my review:\n" + json.dumps(_VALID) + _STRAY,
    "prose_after": json.dumps(_VALID) + _STRAY,
    "fenced": "Here is my review:\n```json\n" + json.dumps(_VALID) + "\n```\nNote: `}` at line 9.",
}


@pytest.mark.parametrize("keys", [None, REVIEW_REPLY_KEYS], ids=["no_keys", "keys"])
@pytest.mark.parametrize("name", sorted(VALID_THEN_PROSE))
def test_valid_reply_then_prose_with_a_brace_is_kept(api_env, monkeypatch, name, keys):
    # As on main: prose after the reply's own object does not hide it.
    request = Mock(return_value=(VALID_THEN_PROSE[name], STOP))
    monkeypatch.setattr(invoke, "_invoke_openai", request)
    result = llm_invoke("task", backend=_api_backend(), reply_keys=keys)
    assert result.content == _VALID
    assert request.call_count == 1


_QUOTED_VERDICT = '{"verdict": "DISMISSED", "reasoning": "ok"}'
FALSIFY_QUOTES_A_VERDICT = {
    "prose_reasoning_first": (
        'Ok:\n{"reasoning": "it returns ' + _QUOTED_VERDICT + ' so", "verdict": "CONFIRMED", "x": "cu'
    ),
    "prose_verdict_first_cut": 'Ok:\n{"verdict": "CONFIRMED", "reasoning": "it returns '
    + _QUOTED_VERDICT
    + "} so the",
    "verdict_first_cut": '{"verdict": "CONFIRMED", "reasoning": "it returns '
    + _QUOTED_VERDICT
    + "} so the",
    "prose_in_brackets": (
        'Ok:\n{"verdict": "CONFIRMED", "reasoning": "it returns [' + _QUOTED_VERDICT + '] and x"} so the'
    ),
    "prose_complete": 'Ok:\n{"verdict": "CONFIRMED", "reasoning": "it returns '
    + _QUOTED_VERDICT
    + ' so"}',
}


@pytest.mark.parametrize("name", sorted(FALSIFY_QUOTES_A_VERDICT))
def test_falsify_reply_never_takes_a_quoted_verdict(name):
    text = FALSIFY_QUOTES_A_VERDICT[name]
    with pytest.raises(json.JSONDecodeError) as error:
        _loads_model_json(text, _FALSIFY_REPLY_KEYS)
    for got in (
        invoke._fallback_reply(text, error.value, FALSIFY_KEYS),
        invoke._extract_json_from_text(text, FALSIFY_KEYS),
    ):
        assert got is None or got.get("verdict") == "CONFIRMED"


@pytest.mark.parametrize("wrap", [False, True], ids=["object", "list"])
def test_repair_that_invents_an_empty_key_is_refused(wrap):
    reply = '{"findings": [], "": "q "x" r"}'
    text = "[" + reply + "]" if wrap else reply
    assert _loads_model_json(text) == (
        [{"findings": [], "": 'q "x" r'}] if wrap else {"findings": [], "": 'q "x" r'}
    )
    with pytest.raises(invoke.ReplyKeyRejected):
        _loads_model_json(text, REVIEW_REPLY_KEYS)
    assert _unknown_reply_key([{"findings": [], "": 1, "zz": 2}], REVIEW_REPLY_KEYS) == ""


def test_reply_keys_are_hashable_and_read_only():
    assert hash(REVIEW_REPLY_KEYS) == hash(REVIEW_REPLY_KEYS)
    with pytest.raises(TypeError):
        REVIEW_REPLY_KEYS.items["findings"] = frozenset()
    built = ReplyKeys(top=frozenset({"a"}), items={"a": frozenset({"b"})})
    assert built == ReplyKeys(top=frozenset({"a"}), items={"a": frozenset({"b"})})
    assert hash(built) == hash(ReplyKeys(top=frozenset({"a"}), items={"a": frozenset({"b"})}))


@pytest.mark.parametrize(
    "text, comma",
    [
        ('{"findings": [{"file": "a.py", "description": "z",}]}', ",}"),
        ('{"findings": [{"file": "a.py", "description": "z"},]}', ",]"),
        ('{"findings": [], "context_quotes": ["a", "b",]}', ",]"),
        ('{"findings": [{"file": "a.py", "description": "z" ,\n }]}', ",\n }"),
    ],
    ids=["object", "list", "string_list", "spaced"],
)
@pytest.mark.parametrize(
    "message, at_close",
    [
        ("Expecting property name enclosed in double quotes", True),  # Python 3.12, ",}"
        ("Expecting value", True),  # Python 3.12, ",]"
        ("Illegal trailing comma before end of object", False),  # Python 3.13+
    ],
    ids=["py312_name", "py312_value", "py313"],
)
def test_trailing_comma_is_not_repaired_on_any_python(text, comma, message, at_close):
    pos = text.index(comma) + (len(comma) - 1 if at_close else 0)
    assert invoke._error_is_at_quote(text, json.JSONDecodeError(message, text, pos)) is None


def test_close_after_a_quote_is_still_repaired():
    # Without a comma before it, the error at '}' follows a stray quote.
    text = '{"findings": [{"file": "a.py", "description": "say "hi" }]}'
    pos = text.index(" }") + 1
    error = json.JSONDecodeError("Expecting property name enclosed in double quotes", text, pos)
    assert invoke._error_is_at_quote(text, error) is not None


_P2_AFTER_TRAILING_COMMA = (
    '{"findings": [{"file": "t.py", "line": 3, "severity": "P1", "description": "z",}, '
    '{"file": "t.py", "line": 4, "severity": "P2", "description": "w"}], "code_excerpts": []}'
)


@pytest.mark.parametrize("keys", [None, REVIEW_REPLY_KEYS], ids=["no_keys", "keys"])
def test_trailing_comma_does_not_drop_a_finding(api_env, monkeypatch, keys):
    with pytest.raises(json.JSONDecodeError) as error:
        _loads_model_json(_P2_AFTER_TRAILING_COMMA, keys)
    assert not isinstance(error.value, invoke.RepairRefused)
    request = Mock(return_value=(_P2_AFTER_TRAILING_COMMA, STOP))
    monkeypatch.setattr(invoke, "_invoke_openai", request)
    with pytest.raises(invoke.InvalidJSONResponseError):
        llm_invoke("task", backend=_api_backend(), reply_keys=keys)


_EMBEDDED = (
    '{"findings": [], "code_excerpts": [{"file": "t.py", "start_line": 3, '
    '"end_line": 3, "content": "y"}]}'
)
_OPEN_BRACES = " func f() { for {"


def _review_text(line, lead=""):
    return (
        '{"findings": [{"file": "t.py", "line": 3, "severity": "P1", ' + lead + '"description": "bad", '
        '"excerpt": "' + line + '"}], "code_excerpts": [{"file": "t.py", "start_line": 3, '
        '"end_line": 3, "content": "' + line + '"}]}'
    )


_EXCERPTS_FIRST_OPEN = (
    '{"code_excerpts": [{"file": "a.py", "start_line": 1, "end_line": 1, "content": "x"}], '
    '"findings": []}' + _OPEN_BRACES + '"}], "findings": [{"file": "a.py", "line": 3, '
    '"severity": "P1", "description": "d"}]}'
)
QUOTED_REVIEW_REPLIES = {
    "open_braces_prose_led": "Here is my review:\n" + _review_text("x = " + _EMBEDDED + _OPEN_BRACES),
    "open_braces_true_led": _review_text("x = " + _EMBEDDED + _OPEN_BRACES, '"x": True, '),
    "open_braces_excerpts_first": _EXCERPTS_FIRST_OPEN,
    "cut_prose_led": "Here is my review:\n"
    + _review_text("x = " + _EMBEDDED).split(_EMBEDDED)[0]
    + _EMBEDDED,
    "cut_true_led": _review_text("x = " + _EMBEDDED, '"x": True, ').split(_EMBEDDED)[0] + _EMBEDDED,
}


@pytest.mark.parametrize("keys", [None, REVIEW_REPLY_KEYS], ids=["no_keys", "keys"])
@pytest.mark.parametrize(
    "name, finish",
    [(name, "stop") for name in sorted(QUOTED_REVIEW_REPLIES)]
    + [(name, "length") for name in sorted(QUOTED_REVIEW_REPLIES) if name.startswith("cut_")],
)
def test_quoted_review_is_never_the_reply(api_env, monkeypatch, name, keys, finish):
    text = QUOTED_REVIEW_REPLIES[name]
    usage = dict(STOP, _forge_finish_reason=finish)
    request = Mock(return_value=(text, usage))
    monkeypatch.setattr(invoke, "_invoke_openai", request)
    try:
        result = llm_invoke("task", backend=_api_backend(), reply_keys=keys)
    except invoke.LLMInvokeError:
        return
    severities = [f.get("severity") for f in result.content.get("findings", [])]
    assert "P1" in severities


_QUOTED_DISMISSAL = '{"verdict": "DISMISSED", "reasoning": "ok"}'


@pytest.mark.parametrize(
    "text",
    [
        'Ok:\n{"verdict": "CONFIRMED", "reasoning": "it returns '
        + _QUOTED_DISMISSAL
        + _OPEN_BRACES
        + '", "x": 1}',
        '{"verdict": "CONFIRMED", "reasoning": "it returns ' + _QUOTED_DISMISSAL + _OPEN_BRACES + '"}',
    ],
    ids=["prose_led", "bare"],
)
def test_open_braces_do_not_flip_a_verdict(api_env, monkeypatch, text):
    request = Mock(return_value=(text, STOP))
    monkeypatch.setattr(invoke, "_invoke_openai", request)
    try:
        result = llm_invoke(
            "task", backend=_api_backend(), expected_keys=FALSIFY_KEYS, reply_keys=_FALSIFY_REPLY_KEYS
        )
    except invoke.LLMInvokeError:
        return
    assert result.content.get("verdict") == "CONFIRMED"


def test_duplicate_verdict_is_refused_with_keys():
    text = '{"verdict": "CONFIRMED", "reasoning": "a", "verdict": "DISMISSED", "reasoning": "b"}'
    assert _loads_model_json(text)["verdict"] == "DISMISSED"  # json keeps the last key
    with pytest.raises(invoke.ReplyKeyRejected):
        _loads_model_json(text, _FALSIFY_REPLY_KEYS)
    assert invoke._fallback_reply(text, invoke.ReplyKeyRejected("m", text, 0), FALSIFY_KEYS) is None


def test_repair_that_repeats_an_item_key_is_refused():
    # A cut excerpts-first reply: the repair reads the quoted object's keys
    # into the excerpt item, repeating file, start_line, end_line and content.
    text = (
        '{"code_excerpts": [{"file": "a.py", "start_line": 1, "end_line": 1, "content": "x = '
        + _EMBEDDED
    )
    with pytest.raises(invoke.ReplyKeyRejected):
        _loads_model_json(text, REVIEW_REPLY_KEYS)


def test_duplicate_key_after_a_repair_keeps_the_first_error():
    text = '{"verdict": "CONFIRMED", "reasoning": "say "hi"", "verdict": "DISMISSED"}'
    with pytest.raises(invoke.ReplyKeyRejected) as error:
        _loads_model_json(text, _FALSIFY_REPLY_KEYS)
    with pytest.raises(json.JSONDecodeError) as plain:
        json.loads(text)
    assert error.value.pos == plain.value.pos


def test_object_right_before_a_close_is_not_the_reply():
    assert invoke._extract_json_from_text('{"x": 1, "ok": True} {"findings": []}}') is None
    assert invoke._extract_json_from_text('{"findings": []}}') is None


@pytest.mark.parametrize(
    "after, kept",
    [
        (' {"findings": ["second"]}', False),
        (' {"x": "second"}', True),
        ("\nThe stray `}` on line 9.", True),
        (', {"x": "y"}]', False),
        (" func f() { for {" + '"}]', False),
        (' and "q" then ]', False),
        (' and "q" then }', False),
        ('" and the reply was cut', False),
        ("}", False),
    ],
    ids=[
        "second_reply",
        "other_object",
        "prose_brace",
        "comma_after_leading",
        "open_braces_then_quote",
        "quote_then_bracket",
        "quote_then_brace",
        "quote_first",
        "close_first",
    ],
)
def test_text_after_the_reply(after, kept):
    text = '{"findings": ["first"]}' + after
    assert (invoke._extract_json_from_text(text) is not None) is kept


@pytest.mark.parametrize(
    "text",
    [
        '[{"findings": ["first"]}, {"x": "y"}]',
        'Here: {"findings": ["first"]}, then more.',
        'Here: {"findings": ["first"]}; done.',
        'Here: {"findings": ["first"]} // note',
    ],
    ids=["list_rest", "prose_led_comma", "prose_led_semicolon", "prose_led_comment"],
)
def test_code_after_an_object_that_does_not_open_the_reply_is_kept(text):
    assert invoke._extract_json_from_text(text) == {"findings": ["first"]}


def test_cli_repaired_object_result_with_an_empty_key_falls_back(cli_binary):
    stdout = '{"type": "result", "subtype": "success", "result": {"findings": [], "": "q "x" r"}}'
    backend = BackendConfig(name="cli-test", type="cli", model="m", command="")
    with patch("code_forge.llm_invoke.subprocess.Popen", return_value=_bare_cli_proc(stdout)):
        result = llm_invoke("prompt", backend=backend, reply_keys=REVIEW_REPLY_KEYS)
    assert result.content == stdout
    with patch("code_forge.llm_invoke.subprocess.Popen", return_value=_bare_cli_proc(stdout)):
        result = llm_invoke("prompt", backend=backend)
    assert "" in result.content


# A reply that opens with code_excerpts, cut right after an excerpt quoting
# `"}], "findings": []}`: the quote closes the reply's object early, and the
# P1 finding after it is lost if that object is taken as the reply.
_CUT_EXCERPTS_FIRST = (
    '{"code_excerpts": [{"file": "a.py", "start_line": 1, "end_line": 1, "content": "x"}], '
    '"findings": []}'
)
_CUT_REST = '"}], "findings": [{"file": "a.py", "line": 3, "severity": "P1", "description": "d"}]}'
_REFUSED_TAILS = {
    "comma_close": ",}",
    "bracket_close": "]}",
    "comment": " // done",
    "call": "; f(a)",
    "escape": "\\u007d",
    "smart_quote": " \u201cx",
    "right_smart_quote": " \u201dx",
    "close_paren": ")",
    "assign": " = 1",
}
# Text that starts with a word or a digit reads as prose after a valid reply
# (the P2-2 C cases), so these cut tails cannot be refused on a stop.
_PROSE_LIKE_TAILS = {"number": " 42", "word": " done", "open_braces": _OPEN_BRACES}
_ALL_TAILS = {**_REFUSED_TAILS, **_PROSE_LIKE_TAILS}


def _keeps_the_p1(call):
    try:
        result = call()
    except invoke.LLMInvokeError:
        return True
    return "P1" in [f.get("severity") for f in result.content.get("findings", [])]


@pytest.mark.parametrize("keys", [None, REVIEW_REPLY_KEYS], ids=["no_keys", "keys"])
@pytest.mark.parametrize("tail", sorted(_REFUSED_TAILS))
def test_stop_cut_code_tail_is_refused(api_env, monkeypatch, tail, keys):
    text = _CUT_EXCERPTS_FIRST + _REFUSED_TAILS[tail]
    assert invoke._extract_json_from_text(text) is None
    monkeypatch.setattr(invoke, "_invoke_openai", Mock(return_value=(text, STOP)))
    with pytest.raises(invoke.LLMInvokeError):
        llm_invoke("task", backend=_api_backend(), reply_keys=keys)


@pytest.mark.parametrize("tail", sorted(_PROSE_LIKE_TAILS))
def test_stop_cut_prose_like_tail_is_a_known_limit(api_env, monkeypatch, tail):
    text = _CUT_EXCERPTS_FIRST + _PROSE_LIKE_TAILS[tail]
    monkeypatch.setattr(invoke, "_invoke_openai", Mock(return_value=(text, STOP)))
    result = llm_invoke("task", backend=_api_backend(), reply_keys=REVIEW_REPLY_KEYS)
    assert result.content["findings"] == []


@pytest.mark.parametrize("keys", [None, REVIEW_REPLY_KEYS], ids=["no_keys", "keys"])
@pytest.mark.parametrize("tail", sorted(_ALL_TAILS))
def test_length_cut_tail_asks_for_the_rest(api_env, monkeypatch, tail, keys):
    text = _CUT_EXCERPTS_FIRST + _ALL_TAILS[tail]
    request = Mock(side_effect=[_truncated(text), (_CUT_REST, STOP)] + [(text, STOP)] * 6)
    monkeypatch.setattr(invoke, "_invoke_openai", request)
    assert _keeps_the_p1(lambda: llm_invoke("task", backend=_api_backend(), reply_keys=keys))
    assert request.call_count >= 2  # the cut reply was continued, not taken


def test_continue_partial_needs_a_whole_parse(api_env, monkeypatch):
    # The fallback scan reads this cut partial as an empty review followed by
    # prose (a known limit on a stop). Cut by length it must be continued.
    partial = _CUT_EXCERPTS_FIRST + _PROSE_LIKE_TAILS["number"]
    assert invoke._extract_json_from_text(partial) is not None
    request = Mock(return_value=(" more", STOP))
    monkeypatch.setattr(invoke, "_invoke_openai", request)
    try:
        invoke._continue_truncated(
            "p", _api_backend(), "k", 7, _truncated(partial), None, reply_keys=REVIEW_REPLY_KEYS
        )
    except invoke.LLMInvokeError:
        pass
    assert request.called


_PY_DICT_REPLY = (
    "{'findings': [{'file': 'a.py', 'line': 3, 'severity': 'P1', 'description': 'd', "
    "'excerpt': 'x = " + '{"findings": []}' + "'}]}"
)
_JS_REPLY = (
    "{findings: [{file: 'a.py', line: 3, severity: 'P1', description: 'd', "
    "excerpt: 'x = " + '{"findings": []}' + "'}]}"
)


@pytest.mark.parametrize("text", [_PY_DICT_REPLY, _JS_REPLY], ids=["python_dict", "js_object"])
def test_non_json_reply_does_not_yield_its_quoted_review(api_env, monkeypatch, text):
    assert invoke._extract_json_from_text(text) is None
    monkeypatch.setattr(invoke, "_invoke_openai", Mock(return_value=(text, STOP)))
    with pytest.raises(invoke.LLMInvokeError):
        llm_invoke("task", backend=_api_backend(), reply_keys=REVIEW_REPLY_KEYS)


def test_failed_brace_after_prose_does_not_end_the_scan():
    text = "Note {a b} first. " + '{"findings": ["real"]}'
    assert invoke._extract_json_from_text(text) == {"findings": ["real"]}


_EXAMPLE_THEN_REPLY = (
    'The format is {"findings": []}. My review:\n'
    '{"findings": [{"file": "a.py", "line": 3, "severity": "P1", "description": "d"}]}'
)


def test_example_envelope_before_the_reply_is_not_the_reply(api_env, monkeypatch):
    assert invoke._extract_json_from_text(_EXAMPLE_THEN_REPLY) is None
    assert _keeps_the_p1(
        lambda: _llm_with(monkeypatch, _EXAMPLE_THEN_REPLY, reply_keys=REVIEW_REPLY_KEYS)
    )


def _llm_with(monkeypatch, text, **kwargs):
    monkeypatch.setattr(invoke, "_invoke_openai", Mock(return_value=(text, STOP)))
    return llm_invoke("task", backend=_api_backend(), **kwargs)


def test_canary_review_passes_review_keys(tmp_path):
    from code_forge.cli import _dispatch_inline_canary
    from code_forge.state import Verdict

    def fake_canary(*, review_provider, **_):
        review_provider("review prompt")
        return Verdict.DELEGATED, []

    mock = _invoke_mock(_REVIEW_REPLY)
    with (
        patch("code_forge.cli._load_canary_config", return_value={"n": 5}),
        patch("code_forge.backend.resolve_backend", return_value=MagicMock()),
        patch("code_forge.canary_gen.run_inline_canary", side_effect=fake_canary),
        patch("code_forge.llm_invoke.llm_invoke", mock),
    ):
        _dispatch_inline_canary("inline", MagicMock(), {}, None, {}, tmp_path)
    assert _keys_of_every_call(mock) == {id(REVIEW_REPLY_KEYS)}


def test_daemon_state_passes_its_keys(tmp_path):
    import yaml

    from code_forge import daemon_state

    gate = tmp_path / ".code-forge"
    gate.mkdir()
    (gate / "gate.yaml").write_text(
        yaml.safe_dump({"daemon_state": {"enabled": True, "subsystems": ["nftables"]}})
    )
    q1 = _invoke_mock({"external_state": ["nft mark"]}).return_value
    q2q3 = _invoke_mock({"conflicts": []}).return_value
    with (
        patch("code_forge.daemon_state.llm_invoke", side_effect=[q1, q2q3]) as mock,
        patch("code_forge.daemon_state._grep_repo", return_value=""),
    ):
        daemon_state.DaemonStateRunner(backend=MagicMock()).run("+nft add rule", tmp_path)
    assert [c.kwargs.get("reply_keys") for c in mock.call_args_list] == [
        daemon_state.Q1_REPLY_KEYS,
        daemon_state.Q2Q3_REPLY_KEYS,
    ]


def test_daemon_duplicate_conflicts_is_refused():
    from code_forge.daemon_state import Q2Q3_REPLY_KEYS

    conflict = '{"subsystem": "s", "mutates": "m", "interferes_with": "i", "scenario": "c"}'
    text = '{"conflicts": [' + conflict + '], "conflicts": []}'
    assert _loads_model_json(text)["conflicts"] == []  # json keeps the last key
    with pytest.raises(invoke.ReplyKeyRejected):
        _loads_model_json(text, Q2Q3_REPLY_KEYS)


def test_daemon_conflict_item_with_a_repaired_key_is_refused():
    from code_forge.daemon_state import Q2Q3_REPLY_KEYS

    text = (
        '{"conflicts": [{"subsystem": "s", "mutates": "m", "interferes_with": "i", '
        '"scenario": "cfg = {"a": "b", "c": "d"}"}]}'
    )
    assert "c" in _loads_model_json(text)["conflicts"][0]  # the repair invents `c`
    with pytest.raises(invoke.ReplyKeyRejected):
        _loads_model_json(text, Q2Q3_REPLY_KEYS)


def test_daemon_q1_repaired_reply_with_an_unknown_key_is_refused():
    from code_forge.daemon_state import Q1_REPLY_KEYS

    text = '{"external_state": ["m"], "x": ["say "hi" now"]}'
    assert "x" in _loads_model_json(text)  # the repair keeps `x`
    with pytest.raises(invoke.ReplyKeyRejected):
        _loads_model_json(text, Q1_REPLY_KEYS)
