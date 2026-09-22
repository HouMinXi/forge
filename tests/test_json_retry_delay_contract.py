"""Contract tests for retry delay calculation in code_forge.llm_invoke.

Covers:
- src/code_forge/llm_invoke.py::_retry_delay_s
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from code_forge.llm_invoke import MAX_BACKOFF_S, _retry_delay_s


class TestRetryDelayExponentialBackoffContract:
    """Contract tests for exponential backoff and base calculations."""

    @pytest.mark.parametrize(
        ("attempt", "initial_delay_s", "fixed_jitter", "expected_delay"),
        [
            # attempt=0: 2.0 * (2 ** 0) = 2.0; delay = 2.0 + 0.25 = 2.25
            (0, 2.0, 0.25, 2.25),
            # attempt=1: 2.0 * (2 ** 1) = 4.0; delay = 4.0 + 0.25 = 4.25
            (1, 2.0, 0.25, 4.25),
            # attempt=3: 2.0 * (2 ** 3) = 16.0; delay = 16.0 + 0.25 = 16.25
            (3, 2.0, 0.25, 16.25),
            # initial_delay_s=1.0, attempt=0: 1.0 * (2 ** 0) = 1.0; delay = 1.0 + 0.25 = 1.25
            (0, 1.0, 0.25, 1.25),
            # initial_delay_s=1.0, attempt=2: 1.0 * (2 ** 2) = 4.0; delay = 4.0 + 0.25 = 4.25
            (2, 1.0, 0.25, 4.25),
        ],
    )
    def test_exponential_backoff_with_fixed_jitter(
        self,
        attempt: int,
        initial_delay_s: float,
        fixed_jitter: float,
        expected_delay: float,
    ) -> None:
        with patch("random.uniform", return_value=fixed_jitter) as mock_uniform:
            delay = _retry_delay_s(
                attempt=attempt,
                initial_delay_s=initial_delay_s,
                retry_after=None,
            )
            mock_uniform.assert_called_once_with(0, 0.5)
            assert delay == pytest.approx(expected_delay)

    def test_backoff_capped_at_max_backoff_before_jitter(self) -> None:
        # Before reaching MAX_BACKOFF_S (60.0): attempt=4 -> 2.0 * 16 = 32.0 (< 60.0)
        with patch("random.uniform", return_value=0.25):
            delay_before = _retry_delay_s(
                attempt=4,
                initial_delay_s=2.0,
                retry_after=None,
            )
            assert delay_before == pytest.approx(32.25)

        # Exceeding MAX_BACKOFF_S: attempt=5 -> 2.0 * 32 = 64.0 -> base capped at 60.0
        # Jitter (0.25) is added after base cap, so delay = 60.0 + 0.25 = 60.25
        with patch("random.uniform", return_value=0.25):
            delay_capped = _retry_delay_s(
                attempt=5,
                initial_delay_s=2.0,
                retry_after=None,
            )
            assert delay_capped == pytest.approx(60.25)

        # High attempt=10: 2.0 * 1024 = 2048.0 -> capped at 60.0; delay = 60.25
        with patch("random.uniform", return_value=0.25):
            delay_high = _retry_delay_s(
                attempt=10,
                initial_delay_s=2.0,
                retry_after=None,
            )
            assert delay_high == pytest.approx(60.25)


class TestRetryDelayZeroInitialDelayContract:
    """Contract tests for zero initial delay behavior."""

    def test_zero_initial_delay_never_calls_random_uniform(self) -> None:
        def _exploding_random(*args: object, **kwargs: object) -> float:
            raise AssertionError("random.uniform must not be called when initial_delay_s == 0")

        with patch("random.uniform", side_effect=_exploding_random):
            # attempt=0 with zero initial delay
            delay_0 = _retry_delay_s(
                attempt=0,
                initial_delay_s=0.0,
                retry_after=None,
            )
            assert delay_0 == 0.0

            # attempt=3 with zero initial delay integer 0
            delay_3 = _retry_delay_s(
                attempt=3,
                initial_delay_s=0,
                retry_after=None,
            )
            assert delay_3 == 0.0

    def test_zero_initial_delay_with_mock_returning_nonzero(self) -> None:
        # Even if random.uniform is mocked to return 0.25, zero delay must return 0.0
        with patch("random.uniform", return_value=0.25) as mock_uniform:
            delay = _retry_delay_s(
                attempt=0,
                initial_delay_s=0.0,
                retry_after=None,
            )
            assert delay == 0.0
            mock_uniform.assert_not_called()

    def test_initial_delay_of_one_calls_random_uniform(self) -> None:
        # initial_delay_s=1.0 is non-zero, so it MUST call random.uniform and add jitter
        with patch("random.uniform", return_value=0.25) as mock_uniform:
            delay = _retry_delay_s(
                attempt=0,
                initial_delay_s=1.0,
                retry_after=None,
            )
            mock_uniform.assert_called_once_with(0, 0.5)
            # base = 1.0 * (2 ** 0) = 1.0; jitter = 0.25; delay = 1.25
            assert delay == pytest.approx(1.25)


class TestRetryDelayRetryAfterContract:
    """Contract tests for retry_after precedence and bounding."""

    @pytest.mark.parametrize(
        ("retry_after", "expected_delay"),
        [
            # None: uses computed backoff (base 4.0 + jitter 0.25 = 4.25)
            (None, 4.25),
            # 0 / 0.0: smaller than computed delay, so delay remains 4.25
            (0, 4.25),
            (0.0, 4.25),
            # smaller than current delay (3.0 < 4.25): delay remains 4.25
            (3.0, 4.25),
            # greater than current delay (10.0 > 4.25): delay taken as 10.0
            (10.0, 10.0),
            # greater than MAX_BACKOFF_S (120.0 > 60.0): delay taken as 120.0
            (120.0, 120.0),
        ],
    )
    def test_retry_after_precedence(
        self,
        retry_after: float | None,
        expected_delay: float,
    ) -> None:
        with patch("random.uniform", return_value=0.25):
            delay = _retry_delay_s(
                attempt=1,
                initial_delay_s=2.0,
                retry_after=retry_after,
            )
            assert delay == pytest.approx(expected_delay)

    def test_retry_after_with_zero_initial_delay(self) -> None:
        # When initial_delay_s == 0, base is 0.0 and jitter is 0.0.
        # If retry_after is provided, max(0.0, retry_after) gives retry_after without random calls.
        def _exploding_random(*args: object, **kwargs: object) -> float:
            raise AssertionError("random.uniform must not be called when initial_delay_s == 0")

        with patch("random.uniform", side_effect=_exploding_random):
            delay = _retry_delay_s(
                attempt=0,
                initial_delay_s=0.0,
                retry_after=7.5,
            )
            assert delay == 7.5


class TestRetryDelayRealRandomAndFileInputContract:
    """Contract tests using real random source and filesystem I/O."""

    def test_real_random_source_stays_within_bounds(self) -> None:
        # Real random source without mocking:
        # attempt=1, initial_delay_s=2.0 -> base = 4.0
        # random.uniform(0, 0.5) is in [0, 0.5], so delay must be in [4.0, 4.5]
        for _ in range(20):
            delay = _retry_delay_s(
                attempt=1,
                initial_delay_s=2.0,
                retry_after=None,
            )
            assert 4.0 <= delay <= 4.5

        # attempt=0, initial_delay_s=0.0 -> strictly 0.0
        for _ in range(5):
            delay_zero = _retry_delay_s(
                attempt=0,
                initial_delay_s=0.0,
                retry_after=None,
            )
            assert delay_zero == 0.0

    def test_retry_delay_from_real_file_input(self, tmp_path: Path) -> None:
        # Real filesystem round-trip: write parameters as JSON, read back, invoke real function
        config_file = tmp_path / "retry_config.json"
        payload = {
            "attempt": 3,
            "initial_delay_s": 2.0,
            "retry_after": 100.0,
            "max_backoff_s": MAX_BACKOFF_S,
        }
        config_file.write_text(json.dumps(payload), encoding="utf-8")

        raw_data = config_file.read_text(encoding="utf-8")
        loaded = json.loads(raw_data)

        assert loaded["attempt"] == 3
        assert loaded["initial_delay_s"] == 2.0
        assert loaded["retry_after"] == 100.0
        assert loaded["max_backoff_s"] == 60.0

        with patch("random.uniform", return_value=0.25):
            delay = _retry_delay_s(
                attempt=loaded["attempt"],
                initial_delay_s=loaded["initial_delay_s"],
                retry_after=loaded["retry_after"],
            )
            # base = min(2.0 * (2 ** 3), 60.0) = 16.0
            # delay = max(16.0 + 0.25, 100.0) = 100.0
            assert delay == 100.0
