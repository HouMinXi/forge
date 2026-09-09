# SPDX-License-Identifier: Apache-2.0
"""A truncated chunked body is a transport failure, not a crash.

Measured 2026-09-06 on a 20-item calibration run: item 5 of 20 raised
http.client.IncompleteRead(178 bytes read) out of response.read() inside
_invoke_anthropic. IncompleteRead subclasses HTTPException, not OSError,
so none of the three transport except-arms caught it; it escaped
llm_invoke as a bare exception, past the retry loop, past
machine.py's LLMInvokeError arms, and killed the run at 4/20 with no
retry. The same shape from the same backend one hour earlier (a 504
at the gateway) was handled: HTTPError is caught. An HTTP response
that stops mid-body is the same event as a connection reset, and must
be classified the same way: LLMInvokeError, retryable, kind="conn".
"""
from __future__ import annotations

import http.client
import json
import os
from unittest.mock import Mock, patch

import pytest

from code_forge.backend import BackendConfig
from code_forge.llm_invoke import LLMInvokeError, llm_invoke


def _backend(fmt: str) -> BackendConfig:
    return BackendConfig(
        name="b", type="api", model="m", format=fmt,
        base_url="https://x.invalid/v1", api_key_env="X_KEY",
    )


def _response_that_dies_mid_body():
    r = Mock()
    r.read.side_effect = http.client.IncompleteRead(b"x" * 178)
    r.__enter__ = Mock(return_value=r)
    r.__exit__ = Mock(return_value=False)
    r.headers = {}
    return r


@pytest.mark.parametrize("fmt", ["openai", "anthropic"])
def test_incomplete_read_is_a_retryable_conn_error(fmt):
    with patch.dict(os.environ, {"X_KEY": "k"}), \
         patch("urllib.request.urlopen", return_value=_response_that_dies_mid_body()):
        with pytest.raises(LLMInvokeError) as ei:
            llm_invoke("p", backend=_backend(fmt), max_attempts=1,
                       initial_delay_s=0)
    assert ei.value.retryable is True
    assert ei.value.kind == "conn"
    assert "IncompleteRead" in str(ei.value) or "178" in str(ei.value)


def test_incomplete_read_is_retried_then_succeeds():
    good = Mock()
    good.read.return_value = json.dumps({
        "choices": [{"message": {"content": '{"result": "ok"}'}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }).encode()
    good.__enter__ = Mock(return_value=good)
    good.__exit__ = Mock(return_value=False)
    good.headers = {}
    with patch.dict(os.environ, {"X_KEY": "k"}), \
         patch("urllib.request.urlopen",
               side_effect=[_response_that_dies_mid_body(), good]):
        result = llm_invoke("p", backend=_backend("openai"), max_attempts=2,
                            initial_delay_s=0)
    assert result.content == {"result": "ok"}
