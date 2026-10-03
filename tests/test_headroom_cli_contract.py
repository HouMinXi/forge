"""Boundary contracts for wider retries and CLI result envelopes."""

import dataclasses
import importlib
import json
import os
import sys

import pytest

from code_forge.backend import BackendConfig
from code_forge.llm_invoke import Usage

invoke = importlib.import_module("code_forge.llm_invoke")


@pytest.mark.parametrize(
    ("max_tokens", "completion_tokens", "expected_cap"),
    [(0, 0, None), (-1, 0, None), (1, 0, 2), (10, 6, 12)],
)
def test_wider_retry_cap_selection(monkeypatch, max_tokens, completion_tokens, expected_cap):
    backend = BackendConfig(
        name="test",
        type="api",
        model="test-model",
        format="openai",
        max_tokens=max_tokens,
        max_completion_tokens=completion_tokens,
        output_token_limit=20,
    )
    before = dataclasses.asdict(backend)
    calls = []

    def provider(prompt, widened, api_key, timeout_s):
        calls.append((prompt, widened, api_key, timeout_s))
        return '{"findings":[],"code_excerpts":[]}', {}

    monkeypatch.setattr(invoke, "_invoke_openai", provider)
    result = invoke._retry_with_more_headroom("prompt", backend, "key", 3, None)
    assert dataclasses.asdict(backend) == before
    if expected_cap is None:
        assert result is None
        assert calls == []
    else:
        assert result == ({"findings": [], "code_excerpts": []}, Usage())
        assert len(calls) == 1
        prompt, widened, api_key, timeout_s = calls[0]
        assert (prompt, api_key, timeout_s) == ("prompt", "key", 3)
        assert widened is not backend
        expected = dict(before)
        expected["max_completion_tokens" if completion_tokens else "max_tokens"] = expected_cap
        assert dataclasses.asdict(widened) == expected


@pytest.mark.parametrize(
    ("format_name", "provider_name", "expected"),
    [
        ("openai", "_invoke_openai", Usage(7, 11, 5)),
        ("anthropic", "_invoke_anthropic", Usage(13, 17, 5)),
        ("vertex", "_invoke_vertex", Usage(13, 17, 5)),
    ],
)
@pytest.mark.parametrize("usage_kind", ["populated", "empty", "null"])
def test_wider_retry_usage(monkeypatch, format_name, provider_name, expected, usage_kind):
    usage = {
        "prompt_tokens": 7,
        "completion_tokens": 11,
        "input_tokens": 13,
        "output_tokens": 17,
        "prompt_tokens_details": {"cached_tokens": 5},
        "cache_read_input_tokens": 5,
    }
    if usage_kind != "populated":
        usage = {} if usage_kind == "empty" else None
        expected = Usage(0, 0, 0)
    backend = BackendConfig(
        name="test",
        type="api",
        model="test-model",
        format=format_name,
        max_tokens=10,
        output_token_limit=20,
    )
    calls = []

    def provider(*args):
        calls.append(args)
        return '{"findings":[],"code_excerpts":[]}', usage

    monkeypatch.setattr(invoke, provider_name, provider)
    result = invoke._retry_with_more_headroom("prompt", backend, "key", 3, None)
    assert result == ({"findings": [], "code_excerpts": []}, expected)
    assert len(calls) == 1
    args = calls[0]
    assert args[0] == "prompt"
    assert args[1] == dataclasses.replace(backend, max_tokens=20)
    assert args[1] is not backend
    assert args[2:] == ((3,) if format_name == "vertex" else ("key", 3))


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (
            'prefix {"findings":[]} then {"verdict":"yes","reasoning":"ok"}',
            ({"verdict": "yes", "reasoning": "ok"}, Usage()),
        ),
        ('{"findings":[],"code_excerpts":[]}', None),
        ('prefix {"findings":[]}', None),
    ],
)
def test_wider_retry_keeps_requested_envelope(monkeypatch, content, expected):
    def provider(*_args):
        return content, {}

    monkeypatch.setattr(invoke, "_invoke_openai", provider)
    backend = BackendConfig(
        name="test",
        type="api",
        model="test-model",
        format="openai",
        max_tokens=10,
        output_token_limit=20,
    )
    result = invoke._retry_with_more_headroom(
        "prompt",
        backend,
        "key",
        3,
        frozenset({"verdict", "reasoning"}),
    )
    assert result == expected


@pytest.mark.parametrize(
    ("payload", "expected_content", "expected_usage"),
    [
        (
            {"type": "other", "result": '{"ok":true}', "usage": {"input_tokens": 4}},
            {"type": "other", "result": '{"ok":true}', "usage": {"input_tokens": 4}},
            Usage(),
        ),
        ({"type": "result", "result": "not-json", "usage": {}}, "not-json", Usage()),
        ({"type": "result", "result": {"ok": True}, "usage": {}}, {"ok": True}, Usage()),
        (
            {
                "type": "result",
                "result": '{"ok":true}',
                "usage": {"input_tokens": 4, "output_tokens": 8, "cache_read_input_tokens": 3},
            },
            {"ok": True},
            Usage(4, 8, 3),
        ),
        (
            [
                {"type": "result", "result": '{"ok":false}'},
                {"type": "result", "result": '{"ok":true}'},
            ],
            {"ok": True},
            Usage(),
        ),
    ],
    ids=["non-result", "plain-text", "object", "usage", "last-event"],
)
@pytest.mark.skipif(os.name == "nt", reason="CLI contract uses a POSIX executable script")
def test_cli_envelope_real_process(tmp_path, payload, expected_content, expected_usage):
    """Only the external CLI is controlled; parsing and duration use real code."""
    command = tmp_path / "model-cli"
    command.write_text(
        f"#!{sys.executable}\nimport sys\n"
        "assert sys.argv[1:] == ['-p', 'prompt', '--model', 'test-model', '--output-format', 'json']\n"
        f"print({json.dumps(payload)!r})\n",
        encoding="utf-8",
    )
    command.chmod(0o700)
    backend = BackendConfig(name="test", type="cli", command=str(command), model="test-model")
    result = invoke._invoke_cli("prompt", backend, 3)
    assert result.content == expected_content
    assert result.usage == expected_usage
    assert result.duration_s > 0
    assert invoke._active_proc is None
