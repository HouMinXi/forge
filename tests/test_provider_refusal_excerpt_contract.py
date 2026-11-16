"""Optional excerpt requests retain the original reply after provider refusal."""

import copy
import io
import json

from code_forge import llm_invoke as invoke
from code_forge.backend import BackendConfig
from code_forge.reviewer_json import REVIEW_REPLY_KEYS


DIFF = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,3 +1,3 @@\n one\n-old.\n+new.\n three\n"
FINDING = {
    "file": "a.py",
    "line": 2,
    "severity": "P2",
    "description": "original finding",
    "excerpt": "new.",
}
PRIVATE_REFUSAL = "private-provider-refusal-body"
INITIAL_USAGE = {"prompt_tokens": 2, "completion_tokens": 3}
REFUSAL_USAGE = {"prompt_tokens": 5, "completion_tokens": 7}


def _scripted_api(monkeypatch, original):
    monkeypatch.setenv("EXCERPT_REFUSAL_KEY", "local-fixture")
    backend = BackendConfig(
        name="excerpt-refusal-test",
        type="api",
        format="openai",
        model="fixture",
        base_url="https://example.invalid",
        api_key_env="EXCERPT_REFUSAL_KEY",
        timeout_s=10,
    )
    bodies = iter(
        [
            {
                "choices": [{"message": {"content": original}, "finish_reason": "stop"}],
                "usage": INITIAL_USAGE,
            },
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"findings": [], "code_excerpts": []}',
                            "refusal": PRIVATE_REFUSAL,
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": REFUSAL_USAGE,
            },
        ]
    )
    requests = []

    def open_response(request, timeout):
        requests.append(json.loads(request.data))
        assert len(requests) <= 2, "refusal triggered another physical request"
        return io.BytesIO(json.dumps(next(bodies)).encode())

    monkeypatch.setattr(invoke, "_open_url", open_response)
    actual_api = invoke._invoke_api
    initial_results = []
    refusals = []

    def record_refusal(*args, **kwargs):
        try:
            result = actual_api(*args, **kwargs)
        except invoke.ProviderRefusalError as error:
            refusals.append(error)
            raise
        initial_results.append(result)
        return result

    monkeypatch.setattr(invoke, "_invoke_api", record_refusal)
    return backend, requests, initial_results, refusals


def _assert_private(refusals, result, caplog, capsys):
    assert len(refusals) == 1
    error = refusals[0]
    assert error.kind == "refusal"
    assert error.retryable is False
    assert error.observed_usage == invoke.Usage(5, 7, 0)
    captured = capsys.readouterr()
    visible = str(error) + error.stderr + repr(result) + caplog.text + captured.out + captured.err
    assert PRIVATE_REFUSAL not in visible
    assert PRIVATE_REFUSAL not in json.dumps(result.excerpt_diagnostics)
    assert result.usage == invoke.Usage(7, 10, 0)


def test_missing_excerpt_refusal_keeps_original_findings_and_usage(monkeypatch, caplog, capsys):
    caplog.set_level("DEBUG")
    original = {"findings": [copy.deepcopy(FINDING)]}
    before = copy.deepcopy(original)
    backend, requests, initial_results, refusals = _scripted_api(monkeypatch, json.dumps(original))

    result = invoke.llm_invoke("review a.py", backend=backend, reply_keys=REVIEW_REPLY_KEYS)

    assert len(requests) == 2
    assert len(initial_results) == 1
    assert result.content is initial_results[0].content
    assert result.content == before
    assert "code_excerpts" not in result.content
    assert result.excerpt_diagnostics == ()
    _assert_private(refusals, result, caplog, capsys)


def test_literal_excerpt_refusal_keeps_original_raw_and_safe_diagnostic(monkeypatch, caplog, capsys):
    caplog.set_level("DEBUG")
    original = {
        "findings": [copy.deepcopy(FINDING)],
        "code_excerpts": [
            {"file": "a.py", "start_line": 1, "end_line": 3, "content": "one\nnew!\nthree"}
        ],
    }
    before = copy.deepcopy(original)
    raw = json.dumps(original, indent=2)
    backend, requests, initial_results, refusals = _scripted_api(monkeypatch, raw)

    result = invoke.llm_invoke(
        "review a.py", backend=backend, reply_keys=REVIEW_REPLY_KEYS, review_diff=DIFF
    )

    assert len(requests) == 2
    assert len(initial_results) == 1
    assert result.content is initial_results[0].content
    assert result.content == before
    assert result.raw_response == raw
    assert len(result.excerpt_diagnostics) == 1
    diagnostic = result.excerpt_diagnostics[0]
    assert diagnostic["reason"] == "provider_refusal"
    assert diagnostic["selected"] is False
    assert diagnostic["indexes"] == [0]
    assert diagnostic["initial_raw"] == raw
    assert diagnostic["correction_raw"] is None
    _assert_private(refusals, result, caplog, capsys)
