"""Pin the log line a failed wider retry leaves behind.

The wider attempt raises LLMInvokeError when the format helper fails. The
function has to record which error it was, on the code_forge logger at
warning, and then return None so the caller raises the exhaustion error it
already holds.
"""

import logging

from code_forge.backend import BackendConfig

import code_forge.llm_invoke as invoke


def test_failed_wider_retry_logs_the_error_and_returns_none(monkeypatch, caplog):
    backend = BackendConfig(name="test", type="api", model="m", format="openai", max_tokens=10)

    def fail(*args, **kwargs):
        raise invoke.LLMInvokeError("backend down")

    monkeypatch.setattr(invoke, "_invoke_openai", fail)

    with caplog.at_level(logging.WARNING, logger="code_forge"):
        result = invoke._retry_with_more_headroom("prompt", backend, "key", 3, None)

    assert result is None
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.name == "code_forge"
    assert record.levelno == logging.WARNING
    assert record.getMessage() == "wider retry failed: LLMInvokeError: backend down"
