"""#441 review-support candidate, NOT a production schema or publisher."""

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4


class ContextMismatch(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    execution_id: str
    source_hash: str

    def __post_init__(self):
        if type(self.execution_id) is not str or type(self.source_hash) is not str:
            raise TypeError("identity fields must be plain strings")
        if str(UUID(self.execution_id)) != self.execution_id or not self.source_hash:
            raise ValueError("canonical UUID and nonempty source identity required")

    @classmethod
    def new_host_execution(cls, source_hash):
        return cls(str(uuid4()), source_hash)


@dataclass(frozen=True, slots=True)
class DiscoveryContext:
    values: tuple[tuple[str, str | None], ...]

    def __post_init__(self):
        if type(self.values) is not tuple:
            raise TypeError("discovery values must be tuple")
        names = []
        for pair in self.values:
            if type(pair) is not tuple or len(pair) != 2:
                raise TypeError("discovery entries must be exact tuple pairs")
            key, value = pair
            if type(key) is not str or (value is not None and type(value) is not str):
                raise TypeError("discovery entries must contain plain strings or None")
            names.append(key)
        if names != sorted(set(names)):
            raise ValueError("discovery names must be unique and sorted")


@dataclass(frozen=True, slots=True)
class InputBinding:
    context: ExecutionContext
    lexical_root: str
    canonical_root: str
    diff_sha256: str
    discovery: DiscoveryContext


class CacheAdmission:
    def __init__(self):
        self._binding = None

    def admit(self, context, diff_text, root, discovery):
        if type(context) is not ExecutionContext or type(discovery) is not DiscoveryContext:
            raise TypeError("exact context types required")
        if type(diff_text) is not str:
            raise TypeError("diff_text must be a plain string")
        path = Path(root)
        binding = InputBinding(
            context,
            str(path.absolute()),
            str(path.resolve()),
            sha256(diff_text.encode("utf-8")).hexdigest(),
            discovery,
        )
        if self._binding is not None and self._binding != binding:
            raise ContextMismatch("execution/input/discovery changed")
        self._binding = binding
        return binding


class HostBoundRunner:
    """Candidate internal adapter; caller supplies CURRENT context on each run.

    Owns one fresh delegate. Does not issue IDs, publish observations, claim cache
    hits or make arbitrary Python callbacks safe. Thread-confined, no concurrency.
    The discovery snapshot must describe the actual delegate configuration; this
    adapter cannot prove an ambient environment stayed unchanged during analysis.
    """

    def __init__(self, fresh_runner):
        self._runner = fresh_runner
        self._admission = CacheAdmission()

    def run(self, diff_text, repo_root, *, context, discovery):
        self._admission.admit(context, diff_text, repo_root, discovery)
        findings = self._runner.run(diff_text, repo_root)
        return deepcopy(findings)

    @property
    def infra_errors(self):
        return tuple(self._runner.infra_errors)
