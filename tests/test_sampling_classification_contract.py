"""Contract tests for how ``invoke_sampling`` classifies failed sampling responses.

``_dispatch_sampling`` falls back to the subprocess backend only when the
error kind is one of ``truncated``, ``empty``, ``stub_model`` or ``no_json``,
so a misspelled kind turns a recoverable failure into a hard error. Inside
``invoke_sampling`` the retry loop keys on ``retryable``: an empty response is
retried because some MCP clients advertise sampling but return empty text on
free models, while the Copilot CLI stub model and a truncated response never
improve and are raised on the first attempt. The messages name the remedy and
quote the model and stop reason, so they are checked character for character.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.types import CreateMessageResult, ImageContent, TextContent

from code_forge import llm_invoke
from code_forge.llm_invoke import LLMInvokeError, invoke_sampling

START = 1000.0
ELAPSED = 4.25
VALID_JSON = '{"findings": []}'

EMPTY_REMEDY = (
    "The MCP client may not fully implement createMessage. "
    "Set outlet: subprocess in gate.yaml and configure an API backend."
)


class _Overlay:
    """A stdlib module as seen by ``llm_invoke`` alone, with a few names replaced.

    ``llm_invoke.time`` is the ``time`` module itself, so patching
    ``llm_invoke.time.time`` would script the clock for logging, pytest and the
    event loop too, and they would consume the readings meant for the code
    under test.
    """

    def __init__(self, module, **overrides):
        self._module = module
        self.__dict__.update(overrides)

    def __getattr__(self, name):
        return getattr(self._module, name)


def _text_result(
    text: str, *, model: str = "claude-sonnet", stop_reason: str | None = "endTurn"
) -> CreateMessageResult:
    return CreateMessageResult(
        role="assistant",
        content=TextContent(type="text", text=text),
        model=model,
        stopReason=stop_reason,
    )


def _session(*results):
    session = MagicMock()
    session.create_message = AsyncMock(side_effect=list(results))
    return session


@pytest.fixture
def clock(monkeypatch):
    """Script the two ``time.time()`` reads that bracket a single attempt.

    Yielding the list lets the caller prove both reads happened. The guard
    inside the reader only fires when the function asks for a third one, so
    without the check after the call a function that reads the clock once
    still passes every test that does not look at ``duration_s``.
    """
    readings = [START, START + ELAPSED]

    def scripted_time():
        assert readings, "invoke_sampling read time.time() more often than scripted"
        return readings.pop(0)

    monkeypatch.setattr(llm_invoke, "time", _Overlay(time, time=scripted_time))
    yield readings
    if readings:
        raise AssertionError("invoke_sampling read time.time() fewer times than scripted")


@pytest.fixture
def sleep(monkeypatch):
    """Record the retry backoff without waiting, visible to ``llm_invoke`` only."""
    fake = AsyncMock()
    monkeypatch.setattr(llm_invoke, "asyncio", _Overlay(asyncio, sleep=fake))
    return fake


async def _classify(result):
    with pytest.raises(LLMInvokeError) as caught:
        await invoke_sampling(_session(result), "prompt", "system", max_attempts=1)
    return caught.value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "expected"),
    [
        pytest.param(
            _text_result("", model="claude-sonnet", stop_reason="endTurn"),
            "sampling response is empty (model=claude-sonnet, stopReason=endTurn). " + EMPTY_REMEDY,
            id="names-model-and-stop-reason",
        ),
        pytest.param(
            _text_result("", model="", stop_reason="endTurn"),
            "sampling response is empty (model=?, stopReason=endTurn). " + EMPTY_REMEDY,
            id="blank-model-shown-as-question-mark",
        ),
        pytest.param(
            _text_result("   \n", model="claude-sonnet", stop_reason=None),
            "sampling response is empty (model=claude-sonnet, stopReason=None). " + EMPTY_REMEDY,
            id="whitespace-only-and-no-stop-reason",
        ),
    ],
)
async def test_empty_response_is_retryable_with_exact_diagnosis(clock, result, expected):
    error = await _classify(result)

    assert str(error) == expected
    assert error.kind == "empty"
    assert error.retryable is True
    assert error.duration_s == ELAPSED


@pytest.mark.asyncio
async def test_non_text_content_is_diagnosed_from_its_repr(clock):
    image = ImageContent(type="image", data="AAAA", mimeType="image/png")
    result = CreateMessageResult(
        role="assistant", content=image, model="claude-sonnet", stopReason="endTurn"
    )

    error = await _classify(result)

    assert str(error) == (
        "sampling response contains no valid JSON (first 120 chars: %r)" % str(image)[:120]
    )
    assert error.kind == "no_json"


@pytest.mark.asyncio
async def test_copilot_cli_stub_model_is_permanent(clock):
    # Well-formed JSON on purpose: the rejection must come from the model name,
    # not from anything wrong with the payload.
    error = await _classify(_text_result(VALID_JSON, model="copilotcli/auto"))

    assert str(error) == (
        "sampling model 'copilotcli/auto' is a Copilot CLI stub that cannot "
        "generate review content. Upgrade to Copilot Pro or set "
        "outlet: subprocess with an API backend."
    )
    assert error.kind == "stub_model"
    assert error.retryable is False
    assert error.duration_s == ELAPSED


@pytest.mark.asyncio
async def test_model_merely_mentioning_copilotcli_is_not_a_stub():
    session = _session(_text_result(VALID_JSON, model="proxy/copilotcli/auto"))

    result = await invoke_sampling(session, "prompt", "system", max_attempts=1)

    assert result.content == {"findings": []}


@pytest.mark.asyncio
async def test_truncated_response_is_permanent(clock):
    error = await _classify(_text_result('{"findings": [', stop_reason="maxTokens"))

    assert str(error) == "sampling response truncated (stopReason == maxTokens)"
    assert error.kind == "truncated"
    assert error.retryable is False
    assert error.duration_s == ELAPSED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "kind"),
    [
        pytest.param(_text_result(VALID_JSON, model="copilotcli/auto"), "stub_model", id="stub-model"),
        pytest.param(
            _text_result('{"findings": [', stop_reason="maxTokens"),
            "truncated",
            id="truncated",
        ),
    ],
)
async def test_permanent_failures_are_not_retried(sleep, result, kind):
    session = _session(result, _text_result(VALID_JSON))

    with pytest.raises(LLMInvokeError) as caught:
        await invoke_sampling(session, "prompt", "system", max_attempts=3)

    assert caught.value.kind == kind
    assert session.create_message.await_count == 1
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_response_is_retried_until_content_arrives(sleep):
    session = _session(_text_result(""), _text_result(VALID_JSON))

    result = await invoke_sampling(session, "prompt", "system", max_attempts=3)

    assert result.content == {"findings": []}
    assert session.create_message.await_count == 2
    assert sleep.await_count == 1
