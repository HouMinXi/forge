"""Pin the body-error sentences, defaults and exit code.

The older tests only check that an error raises and whether it is
retryable. A rewritten sentence, a dropped default, or a changed exit
code still passes them.
"""

import pytest

from code_forge.backend import BackendConfig
from code_forge.llm_invoke import LLMInvokeError, _check_body_error


def _backend(name: str) -> BackendConfig:
    return BackendConfig(
        name=name, type="api", model="m", format="openai",
        base_url="http://x", api_key_env="K",
    )


class TestCheckBodyErrorContract:
    def test_coded_error_keeps_message_code_and_exit(self):
        with pytest.raises(LLMInvokeError) as exc:
            _check_body_error(
                {"error": {"code": "1302", "message": "rate limited"}},
                _backend("zhipu"),
            )
        assert str(exc.value) == (
            "code-forge: zhipu backend: rate limited (code 1302). "
            "Retry after a short wait or reduce request rate"
        )
        assert exc.value.exit_code == 0
        assert exc.value.retryable is True

    def test_missing_message_stays_empty(self):
        with pytest.raises(LLMInvokeError) as exc:
            _check_body_error(
                {"error": {"code": "1302"}},
                _backend("zhipu"),
            )
        assert str(exc.value).startswith(
            "code-forge: zhipu backend:  (code 1302). "
        )

    def test_dict_without_code_uses_the_message(self):
        with pytest.raises(LLMInvokeError) as exc:
            _check_body_error(
                {"error": {"message": "rate limited"}},
                _backend("relay"),
            )
        assert str(exc.value) == (
            "code-forge: relay backend: rate limited. "
            "Check provider status page"
        )
        assert exc.value.exit_code == 0

    def test_bare_string_uses_the_string(self):
        with pytest.raises(LLMInvokeError) as exc:
            _check_body_error(
                {"error": "rate limited"},
                _backend("relay"),
            )
        assert str(exc.value) == (
            "code-forge: relay backend: rate limited. "
            "Check provider status page"
        )
        assert exc.value.exit_code == 0

    def test_minimax_status_keeps_message_and_code(self):
        with pytest.raises(LLMInvokeError) as exc:
            _check_body_error(
                {"base_resp": {"status_code": 1008, "status_msg": "no balance"}},
                _backend("minimax"),
            )
        assert str(exc.value) == (
            "code-forge: minimax backend: no balance (code 1008). "
            "Top up at platform.minimaxi.com"
        )
        assert exc.value.exit_code == 0

    def test_minimax_missing_message_stays_empty(self):
        with pytest.raises(LLMInvokeError) as exc:
            _check_body_error(
                {"base_resp": {"status_code": 1008}},
                _backend("minimax"),
            )
        assert str(exc.value).startswith(
            "code-forge: minimax backend:  (code 1008). "
        )

    def test_minimax_zero_status_is_not_an_error(self):
        assert _check_body_error(
            {"base_resp": {"status_code": 0, "status_msg": "ok"}},
            _backend("minimax"),
        ) is None

    def test_minimax_missing_status_is_not_an_error(self):
        assert _check_body_error(
            {"base_resp": {"status_msg": "ok"}},
            _backend("minimax"),
        ) is None
