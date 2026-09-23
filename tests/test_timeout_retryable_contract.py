"""Contract tests pinning timeout-resolution and retry-classification boundaries.

Each test names the mutant boundary it pins: historical mutation survivors that
the wider suite does not distinguish. See
.planning/quick/260922-parallel-debt/historical-replay-ledger.json.
"""

from code_forge.backend import BackendConfig
from code_forge.llm_invoke import (
    DEFAULT_TIMEOUT_S,
    _API_TIMEOUT_CAP_S,
    _default_timeout_s,
    _is_body_code_retryable,
    effective_invoke_timeout_s,
)


class TestBodyCodeRetryableTerminalMessage:
    """_is_body_code_retryable treats both 'exceed' and 'requested' phrasings
    of the maximum-context-length message as terminal (not retryable)."""

    def test_exceed_without_requested_is_terminal(self):
        # Pins mutmut_19/20/21/22: dropping or corrupting the 'exceed' arm
        # must not make the message retryable.
        assert _is_body_code_retryable(
            "unknown-provider", "unknown_code",
            "maximum context length exceeded for this model",
        ) is False

    def test_requested_without_exceed_is_terminal(self):
        # Pins mutmut_23/24/25: dropping or corrupting the 'requested' arm
        # must not make the message retryable.
        assert _is_body_code_retryable(
            "unknown-provider", "unknown_code",
            "maximum context length requested: 4000 tokens",
        ) is False


class TestDefaultTimeoutS:
    """_default_timeout_s honors FORGE_LLM_TIMEOUT_S only for positive ints."""

    def test_negative_env_falls_back(self, monkeypatch):
        # Pins mutmut_9: 'value > 0 or True' must not let negatives through.
        monkeypatch.setenv("FORGE_LLM_TIMEOUT_S", "-5")
        assert _default_timeout_s() == DEFAULT_TIMEOUT_S

    def test_zero_env_falls_back(self, monkeypatch):
        # Pins mutmut_10: '>=' must not accept zero.
        monkeypatch.setenv("FORGE_LLM_TIMEOUT_S", "0")
        assert _default_timeout_s() == DEFAULT_TIMEOUT_S

    def test_one_second_is_honored(self, monkeypatch):
        # Pins mutmut_11: '> 1' must not reject the minimum positive value.
        monkeypatch.setenv("FORGE_LLM_TIMEOUT_S", "1")
        assert _default_timeout_s() == 1


class TestEffectiveInvokeTimeoutSBoundaries:
    """effective_invoke_timeout_s boundary pins for caller/backend sentinels."""

    @staticmethod
    def _api_backend(timeout_s=0):
        return BackendConfig(
            name="t", type="api", model="x", timeout_s=timeout_s, format=None,
        )

    def test_caller_zero_falls_through_to_cap(self, monkeypatch):
        # Pins mutmut_4: timeout_s=0 means 'not configured', not an explicit
        # zero-second timeout.
        monkeypatch.delenv("FORGE_LLM_TIMEOUT_S", raising=False)
        assert effective_invoke_timeout_s(self._api_backend(), timeout_s=0) == _API_TIMEOUT_CAP_S

    def test_caller_one_second_is_honored(self, monkeypatch):
        # Pins mutmut_5: the minimum positive caller timeout wins.
        monkeypatch.delenv("FORGE_LLM_TIMEOUT_S", raising=False)
        assert effective_invoke_timeout_s(self._api_backend(), timeout_s=1) == 1

    def test_none_backend_timeout_resolves_to_capped_default(self, monkeypatch):
        # Pins mutmut_8 (None must not propagate into the comparison and crash)
        # and mutmut_10 (None must not become a one-second timeout).
        monkeypatch.delenv("FORGE_LLM_TIMEOUT_S", raising=False)
        be = self._api_backend(timeout_s=None)  # type: ignore[arg-type]
        assert effective_invoke_timeout_s(be) == _API_TIMEOUT_CAP_S

    def test_backend_one_second_wins(self, monkeypatch):
        # Pins mutmut_12: backend.timeout_s=1 is a real setting, not 'unset'.
        monkeypatch.delenv("FORGE_LLM_TIMEOUT_S", raising=False)
        assert effective_invoke_timeout_s(self._api_backend(timeout_s=1)) == 1
