# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""The ordinary reply budget is not a transport or runtime metadata budget."""

import importlib
import json
from unittest.mock import Mock

import pytest

from tests import test_invocation_send_audit as audit_tests, test_reply_refusals as refusal_tests

backend = audit_tests.backend
host_factory = audit_tests.host_factory
cli_binary = refusal_tests.cli_binary
reply = audit_tests.reply
scripted = audit_tests.scripted
_cli = refusal_tests._cli
_api_backend = refusal_tests._api_backend

invoke = importlib.import_module("code_forge.llm_invoke")


def nested(depth, kind="list", leaf="0"):
    for level in range(depth):
        leaf = (
            '{"child":' + leaf + "}"
            if kind == "dict" or (kind == "mixed" and level % 2)
            else "[" + leaf + "]"
        )
    return leaf


@pytest.mark.parametrize(
    "reader", [invoke._loads_model_json, invoke.loads_reply_json], ids=["repair", "strict"]
)
@pytest.mark.parametrize("kind", ["list", "dict", "mixed"])
@pytest.mark.parametrize("depth", [255, 256, 257])
@pytest.mark.parametrize(
    "leaf", ["0", '"[]{}\\"\\\\"', r'"\u0022\u005b"'], ids=["number", "escaped", "unicode"]
)
def test_ordinary_reply_boundary(reader, kind, depth, leaf):
    text = nested(depth, kind, leaf)
    if depth <= 256:
        assert reader(text) == json.loads(text)
    else:
        with pytest.raises(ValueError, match="nests too deeply"):
            reader(text)


@pytest.mark.parametrize(
    "reader", [invoke._loads_model_json, invoke.loads_reply_json], ids=["repair", "strict"]
)
@pytest.mark.parametrize("leaf", ["[]", "{}"], ids=["array", "object"])
def test_empty_containers_count(reader, leaf):
    reader(nested(255, leaf=leaf))
    with pytest.raises(ValueError, match="nests too deeply"):
        reader(nested(256, leaf=leaf))


@pytest.mark.parametrize(
    "reader", [invoke._loads_model_json, invoke.loads_reply_json], ids=["repair", "strict"]
)
def test_depth_rejection_precedes_decoder(reader, monkeypatch):
    decode = Mock(side_effect=AssertionError("excessive ordinary reply reached decoder"))
    monkeypatch.setattr(invoke.json, "loads", decode)
    with pytest.raises(ValueError, match="nests too deeply"):
        reader(nested(257))
    decode.assert_not_called()


def test_siblings_and_escaped_delimiters_do_not_accumulate_depth():
    value = {"values": [[] for _ in range(1000)], "text": '[]{}\\"' * 300}
    assert invoke._loads_model_json(json.dumps(value)) == value


@pytest.mark.parametrize("prefix", ["", "Note: "])
@pytest.mark.parametrize("cut", [False, True])
def test_fallback_cannot_salvage_overdepth_reply(prefix, cut):
    text = '{"verdict":"DISMISSED","reasoning":' + nested(256)
    if not cut:
        text += "}"
    assert invoke._extract_json_from_text(prefix + text, frozenset({"verdict", "reasoning"})) is None


def test_fallback_depth_guard_precedes_raw_decode(monkeypatch):
    decode = Mock(side_effect=AssertionError("raw decoder reached"))
    monkeypatch.setattr(json.JSONDecoder, "raw_decode", decode)
    assert invoke._extract_json_from_text('{"findings":' + nested(256) + "}") is None
    decode.assert_not_called()


def test_shallow_fallback_and_quote_repair_still_work():
    assert invoke._extract_json_from_text('Note: {"findings":[]}') == {"findings": []}
    assert invoke._loads_model_json('{"description":"the "x" case"}') == {"description": 'the "x" case'}


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("encoding", ["object", "string", "bare"])
@pytest.mark.parametrize("depth", [256, 257])
def test_cli_ordinary_result_depth(cli_binary, stream, encoding, depth):
    raw = nested(depth, "mixed")
    if encoding == "bare":
        stdout = raw
    else:
        result = json.dumps(raw) if encoding == "string" else raw
        stdout = '{"type":"result","result":' + result + "}"
        if stream:
            stdout = '[{"type":"system"},' + stdout + "]"
    if depth <= 256:
        assert _cli(stdout, None).content == json.loads(raw)
    else:
        with pytest.raises(invoke.LLMInvokeError, match="nests too deeply"):
            _cli(stdout, None)


def test_cli_outer_metadata_is_not_an_ordinary_reply(cli_binary):
    stdout = '{"type":"result","result":"{\\"findings\\":[]}","metadata":' + nested(300) + "}"
    assert _cli(stdout, None).content == {"findings": []}
    assert _cli("[" + stdout + "]", None).content == {"findings": []}


def test_strict_runtime_metadata_retains_decoder_contract():
    raw = '{"surfaces":[],"findings":[],"metadata":' + nested(300) + "}"
    assert invoke._decode_reply(raw, None, True) == json.loads(raw)


def test_api_depth_refusal_spends_existing_correction_retry(monkeypatch):
    monkeypatch.setenv("REFUSALS_TEST_KEY", "local-test")
    bad = '{"findings":[],"metadata":' + nested(256) + "}"
    usage = {"prompt_tokens": 1, "completion_tokens": 2, "_forge_finish_reason": "stop"}
    request = Mock(side_effect=[(bad, usage), ('{"findings":[]}', usage)])
    monkeypatch.setattr(invoke, "_invoke_openai", request)
    result = invoke._invoke_api("task", _api_backend(), 7, max_attempts=2)
    assert result.content == {"findings": []}
    assert request.call_count == 2


def test_api_repeated_depth_refusal_fails_closed(monkeypatch):
    monkeypatch.setenv("REFUSALS_TEST_KEY", "local-test")
    bad = '{"findings":[],"metadata":' + nested(256) + "}"
    request = Mock(return_value=(bad, {"_forge_finish_reason": "stop"}))
    monkeypatch.setattr(invoke, "_invoke_openai", request)
    with pytest.raises(invoke.LLMInvokeError):
        invoke._invoke_api("task", _api_backend(), 7, max_attempts=2)
    assert request.call_count == 2


def test_depth_refusal_cannot_fall_back_to_shallow_object_in_array():
    raw = nested(256, leaf='{"findings":[]}')
    with pytest.raises(invoke.ReplyDepthRejected) as caught:
        invoke._loads_model_json(raw)
    assert invoke._fallback_reply(raw, caught.value, None, None) is None


@pytest.mark.parametrize("fail_at", [1, 2])
def test_depth_correction_preserves_strict_audit_blocking(monkeypatch, host_factory, backend, fail_at):
    # Real dispatch/acquisition, simulated host settlement. Not storage admission.
    host = host_factory(strict=True)
    host.fail_finish = fail_at
    raw = '{"findings":[],"metadata":' + nested(256) + "}"
    transport = scripted(monkeypatch, [reply(raw), reply(), reply()])
    with pytest.raises(invoke.AuditPersistenceError):
        invoke.llm_invoke(
            "review", backend, timeout_s=3, max_attempts=3, initial_delay_s=0, host_audit=host
        )
    assert len(transport.requests) == fail_at
    assert [request.cause for request in host.requests] == ["initial", "correction"][:fail_at]


def test_depth_correction_keeps_audited_raw_and_usage(monkeypatch, host_factory, backend):
    host = host_factory()
    raw = '{"findings":[],"metadata":' + nested(256) + "}"
    responses = [reply(raw, tokens=2), reply(tokens=3)]
    expected_raw = [response.raw for response in responses]
    transport = scripted(monkeypatch, responses)
    result = invoke.llm_invoke(
        "review", backend, timeout_s=3, max_attempts=2, initial_delay_s=0, host_audit=host
    )
    assert result.content == {"findings": [], "code_excerpts": []}
    assert len(transport.requests) == 2
    assert result.usage.input_tokens == result.usage.output_tokens == 5
    assert [bytes(attempt.raw) for attempt in host.attempts] == expected_raw
    assert all(attempt.finished and attempt.captured.seal().complete for attempt in host.attempts)


@pytest.mark.parametrize("place", ["leading", "trailing"])
def test_prose_delimiters_do_not_acquire_json_depth(place):
    prose = "Note: " + "[" * 300 + "]" * 300
    raw = (prose + ' {"findings":[]}') if place == "leading" else ('{"findings":[]} ' + prose)
    with pytest.raises(json.JSONDecodeError) as caught:
        invoke._loads_model_json(raw)
    assert invoke._fallback_reply(raw, caught.value, None, None) == {"findings": []}


def test_quote_repair_rechecks_depth_before_decoding(monkeypatch):
    # Closing the quoted content differently exposes structural containers only
    # on a later repair attempt. Stub the repair operation, retaining the real
    # parser loop and real initial syntax failure.
    raw = '{"description":"the "x" case"}'
    overdepth = nested(257)
    decode = Mock(wraps=json.loads)
    monkeypatch.setattr(invoke.json, "loads", decode)
    monkeypatch.setattr(invoke, "_escape_at", lambda *_: overdepth)
    with pytest.raises(invoke.ReplyDepthRejected):
        invoke._loads_model_json(raw)
    assert decode.call_count == 1


def test_transport_decoder_recursion_keeps_original_refusal_route(monkeypatch):
    monkeypatch.setattr(invoke.json, "loads", Mock(side_effect=RecursionError))
    raw = '{"type":"result","result":"plain"}'
    with pytest.raises(invoke.RepairRefused) as caught:
        invoke._parse_model_json(raw)
    assert type(caught.value) is invoke.RepairRefused
    fallback = Mock(return_value=None)
    monkeypatch.setattr(invoke, "_extract_json_from_text", fallback)
    assert invoke._fallback_reply(raw, caught.value, None, None) is None
    fallback.assert_called_once_with(raw, expected_keys=None, leading_only=True)


@pytest.mark.parametrize("count", [255, 256, 257, 1000])
@pytest.mark.parametrize("bracket", ["[", "{"], ids=["array", "object"])
@pytest.mark.parametrize("field", ["description", "content"])
def test_quote_repair_bracket_rich_source_stays_shallow(count, bracket, field):
    raw = '{"' + field + '":"the "' + bracket * count + '" case"}'
    expected, repaired = invoke._parse_model_json(raw)
    assert repaired and expected == {field: 'the "' + bracket * count + '" case'}
    assert invoke._loads_model_json(raw) == expected


@pytest.mark.parametrize(
    "scalar", ["true", "false", "null", "-12.25e+3", "NaN", "Infinity", "-Infinity"]
)
def test_scalar_siblings_cannot_hide_reachable_depth(scalar, monkeypatch):
    raw = '{"scalar":' + scalar + ',"nested":' + nested(256) + "}"
    decode = Mock(side_effect=AssertionError("overdepth scalar sibling reached decoder"))
    monkeypatch.setattr(invoke.json, "loads", decode)
    with pytest.raises(invoke.ReplyDepthRejected):
        invoke._loads_model_json(raw)
    decode.assert_not_called()
