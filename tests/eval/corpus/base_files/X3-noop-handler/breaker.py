"""Circuit breaker for a review loop that talks to an LLM backend."""


class BreakerTripped(Exception):
    """Raised when the failure threshold is reached."""


class CircuitBreaker:
    """Counts consecutive backend failures; trips at the threshold."""

    def __init__(self, threshold: int = 5) -> None:
        self.threshold = threshold
        self._consecutive = 0

    def record_failure(self) -> None:
        self._consecutive += 1
        if self._consecutive >= self.threshold:
            raise BreakerTripped(
                "backend produced %d consecutive failures (>=%d); "
                "the loop cannot converge"
                % (self._consecutive, self.threshold)
            )

    def record_success(self) -> None:
        self._consecutive = 0

    @property
    def count(self) -> int:
        return self._consecutive
