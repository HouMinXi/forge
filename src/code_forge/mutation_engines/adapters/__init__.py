"""Adapter registry, keyed by adapter identifier (spec: five keys).

Adapters not yet implemented register as unavailable stubs so the key
space is complete from the Python slice onward; probe reports
missing_dependency rather than KeyError at selection time.
"""

from __future__ import annotations

from code_forge.mutation_engines.adapters.base import (
    CapabilityReport,
    CapabilityState,
    ExecutionContext,
    MutationAdapter,
)
from code_forge.mutation_engines.adapters.python_mutmut import MutmutAdapter
from code_forge.mutation_engines.schemas import TargetDeclaration, TargetResult

JS_STRYKER = "js-stryker"
GO_GREMLINS = "go-gremlins"
RUST_CARGO_MUTANTS = "rust-cargo-mutants"
PATCH_CORPUS = "patch-corpus"


class UnavailableAdapter:
    """Registered placeholder for an adapter whose slice has not landed."""

    def __init__(self, adapter_id: str) -> None:
        self.id = adapter_id

    def probe(
        self, target: TargetDeclaration, context: ExecutionContext
    ) -> CapabilityReport:
        return CapabilityReport(
            state=CapabilityState.MISSING_DEPENDENCY,
            resolved_tool_version=None,
            evidence=(),
            errors=(),
        )

    def run(self, target, selection, snapshot, context) -> TargetResult:
        raise NotImplementedError(
            "adapter %r is registered but not implemented" % self.id
        )


_REGISTRY: dict[str, MutationAdapter] = {
    "python-mutmut": MutmutAdapter(),
    JS_STRYKER: UnavailableAdapter(JS_STRYKER),
    GO_GREMLINS: UnavailableAdapter(GO_GREMLINS),
    RUST_CARGO_MUTANTS: UnavailableAdapter(RUST_CARGO_MUTANTS),
    PATCH_CORPUS: UnavailableAdapter(PATCH_CORPUS),
}


def get_adapter(adapter_id: str) -> MutationAdapter:
    try:
        return _REGISTRY[adapter_id]
    except KeyError:
        raise KeyError("no adapter registered for %r" % (adapter_id,)) from None


def registered_adapter_ids() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))
