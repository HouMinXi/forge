"""Pin the one-versus-zero boundaries in request body assembly.

output_ceiling, max_completion_tokens and thinking_budget all treat a
positive value as configured. Zero is unset. One is configured. A mutant
that moves the comparison from "> 0" to "> 1" keeps every existing case
that uses a large number, and drops the one-token request.
"""
import pytest

from code_forge.backend import BackendConfig
from code_forge.llm_invoke import LLMInvokeError, _apply_params


def _backend(
    output_ceiling: int = 0,
    max_completion_tokens: int = 0,
    thinking_type: str = "",
    thinking_budget: int = 0,
    temperature: float = -1.0,
) -> BackendConfig:
    return BackendConfig(
        name="gw",
        type="api",
        model="m",
        format="openai",
        base_url="https://x/v1",
        api_key_env="K",
        max_tokens=100,
        output_ceiling=output_ceiling,
        max_completion_tokens=max_completion_tokens,
        thinking_type=thinking_type,
        thinking_budget=thinking_budget,
        temperature=temperature,
    )


def _apply(
    backend: BackendConfig,
    *,
    allow_thinking: bool = False,
    field_selects_key: bool = False,
    default_temperature: float = -1.0,
) -> tuple[dict, int]:
    body = {"model": "m"}
    cap = _apply_params(
        body,
        backend,
        outcap_key="max_tokens",
        allow_thinking=allow_thinking,
        allow_effort=False,
        field_selects_key=field_selects_key,
        default_temperature=default_temperature,
    )
    return body, cap


class TestApplyParamsBoundaries:
    def test_output_ceiling_of_one_replaces_the_cap(self):
        body, cap = _apply(_backend(output_ceiling=1))
        assert cap == 1
        assert body["max_tokens"] == 1

    def test_output_ceiling_of_zero_leaves_the_cap(self):
        body, cap = _apply(_backend(output_ceiling=0))
        assert cap == 100
        assert body["max_tokens"] == 100

    def test_one_completion_token_selects_its_own_key(self):
        body, cap = _apply(
            _backend(max_completion_tokens=1),
            field_selects_key=True,
        )
        assert cap == 1
        assert body["max_completion_tokens"] == 1
        assert "max_tokens" not in body

    def test_one_thinking_token_is_sent(self):
        body, _cap = _apply(
            _backend(thinking_type="enabled", thinking_budget=1),
            allow_thinking=True,
        )
        assert body["thinking"] == {"type": "enabled", "budget_tokens": 1}

    def test_zero_thinking_budget_sends_type_only(self):
        body, _cap = _apply(
            _backend(thinking_type="enabled", thinking_budget=0),
            allow_thinking=True,
        )
        assert body["thinking"] == {"type": "enabled"}

    def test_zero_temperature_is_sent(self):
        body, _cap = _apply(_backend(temperature=0.0))
        assert body["temperature"] == 0.0

    def test_unset_temperature_stays_out_of_the_body(self):
        body, _cap = _apply(_backend(temperature=-1.0))
        assert "temperature" not in body

    def test_param_check_names_the_backend(self):
        backend = BackendConfig(
            name="gw", type="api", model="m", format="openai",
            base_url="https://x/v1", api_key_env="K", max_tokens=100,
            params=[("a", "b")],  # type: ignore[arg-type]
        )
        with pytest.raises(LLMInvokeError) as exc:
            _apply(backend)
        assert str(exc.value).startswith("backend 'gw':")
