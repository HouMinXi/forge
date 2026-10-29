"""A quote repair that invents a key fails closed (#264).

`_loads_model_json` repairs unescaped inner quotes. When an excerpt quotes a
dict literal such as `{"a": "b", "c": "d"}`, the repair can read `"c"` as a
new key and cut the excerpt short. Callers pass the keys their reply may
carry; a repaired parse with any other key is discarded, and an api backend
spends its JSON-correction retry instead.
"""

import importlib
import inspect
import json
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


def _source(module_name, func_name):
    module = importlib.import_module("code_forge." + module_name)
    return inspect.getsource(getattr(module, func_name))


@pytest.mark.parametrize(
    "module_name, func_name",
    [
        ("factories", "build_l1_provider"),
        ("cli", "_make_subagent_spawn"),
        ("cli", "_run_test_assertion_review"),
    ],
)
def test_review_callers_pass_review_keys(module_name, func_name):
    assert "reply_keys=REVIEW_REPLY_KEYS" in _source(module_name, func_name)


def test_runtime_runner_passes_runtime_keys():
    from code_forge import runtime

    assert "reply_keys=RUNTIME_REPLY_KEYS" in inspect.getsource(runtime.RuntimeRunner)


def test_falsifier_passes_falsify_keys():
    from code_forge import falsify_real

    assert "reply_keys=_FALSIFY_REPLY_KEYS" in inspect.getsource(falsify_real.RealFalsifier)


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
