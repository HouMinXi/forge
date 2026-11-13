"""#441 candidate: explicit frozen discovery handoff, NOT production wiring.

The environment roster, locator adapter and invocation granularity need approval.
No ambient provider import or global environment mutation is performed here.
"""

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from uuid import UUID, uuid4

from execution_binding import DiscoveryContext, ExecutionContext


@dataclass(frozen=True, slots=True)
class HostInvocation:
    execution_id: str

    def __post_init__(self):
        if type(self.execution_id) is not str:
            raise TypeError("plain string execution_id required")
        if str(UUID(self.execution_id)) != self.execution_id:
            raise ValueError("canonical UUID required")

    @classmethod
    def issue(cls):
        """Proposed: once per new host invocation, never per runner.run call."""
        return cls(str(uuid4()))

    def for_source(self, source_hash):
        return ExecutionContext(self.execution_id, source_hash)


@dataclass(frozen=True, slots=True)
class CapturedDiscovery:
    environment: tuple[tuple[str, str | None], ...]
    cwd: str
    locator: str

    @property
    def binding(self):
        values = [("env." + key, value) for key, value in self.environment]
        values.extend((("cwd", self.cwd), ("locator", self.locator)))
        return DiscoveryContext(tuple(sorted(values)))

    def consume(self, consumer):
        """Consumer must use these values, not re-read ambient configuration."""
        return consumer(
            MappingProxyType(dict(self.environment)), Path(self.cwd), Path(self.locator)
        )


def capture_discovery(*, environment, cwd, resolve_locator):
    """Copy selected configuration before passing it to a locator adapter.

    Caller must supply all provider dependencies. This cannot prove that an
    arbitrary callback avoids globals, nor atomic acquisition of ambient state.
    Relative paths are not resolved using a possibly different current cwd.
    """
    copied = dict(environment)
    for key, value in copied.items():
        if type(key) is not str or (value is not None and type(value) is not str):
            raise TypeError("plain string keys and string-or-None values required")
    captured_cwd = Path(cwd)
    if not captured_cwd.is_absolute():
        raise ValueError("absolute captured cwd required")
    pairs = tuple(sorted(copied.items()))
    locator = resolve_locator(MappingProxyType(dict(pairs)), captured_cwd)
    if not isinstance(locator, Path):
        raise TypeError("locator adapter must return a Path")
    if not locator.is_absolute():
        raise ValueError("locator adapter must return an absolute Path")
    return CapturedDiscovery(pairs, str(captured_cwd), str(locator))
