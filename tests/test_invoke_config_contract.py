"""Config mistakes in llm_invoke are permanent, not retried.

A missing backend and a cli backend carrying HTTP fields both raise
before any subprocess or request, and both say the failure will not
change on a retry. Nothing else in the suite reads those two flags, so
a mutant that flips either one used to pass.
"""

import pytest

from code_forge.backend import BackendConfig
from code_forge.llm_invoke import LLMInvokeError, llm_invoke

_NO_BACKEND = "llm_invoke called with no backend; an implicit claude -p fallthrough is disabled"
_CLI_FIELD = (
    "backend %r: type 'cli' spawns a subprocess and sends no HTTP request, "
    "so its %s could not be applied. Remove them, or make this an api backend."
)


def _cli(**overrides):
    fields = dict(name="local", type="cli", model="", command="true")
    fields.update(overrides)
    return BackendConfig(**fields)


def test_missing_backend_names_itself_and_is_not_retried():
    with pytest.raises(LLMInvokeError) as caught:
        llm_invoke("prompt", backend=None)

    error = caught.value
    assert str(error) == _NO_BACKEND
    assert error.retryable is False


@pytest.mark.parametrize("field", ["headers", "params"])
def test_cli_backend_refuses_an_http_field_without_retrying(field):
    backend = _cli(**{field: {"k": "v"}})

    with pytest.raises(LLMInvokeError) as caught:
        llm_invoke("prompt", backend=backend)

    error = caught.value
    assert str(error) == _CLI_FIELD % ("local", field)
    assert error.retryable is False


def test_cli_backend_without_http_fields_reaches_the_subprocess(monkeypatch):
    """The refusal above must not fire on a backend that carries no HTTP fields."""
    seen = {}

    def fake_cli(prompt, backend, timeout_s):
        seen["prompt"] = prompt
        raise LLMInvokeError("sentinel from cli", retryable=True)

    monkeypatch.setattr("code_forge.llm_invoke._invoke_cli", fake_cli)

    with pytest.raises(LLMInvokeError) as caught:
        llm_invoke("prompt", backend=_cli())

    assert seen["prompt"] == "prompt"
    assert str(caught.value) == "sentinel from cli"
