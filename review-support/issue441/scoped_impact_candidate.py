"""W2 review-support candidate. Not production wiring or schema approval."""

from copy import deepcopy
from dataclasses import dataclass
from execution_binding import CacheAdmission, DiscoveryContext, ExecutionContext


@dataclass(frozen=True, slots=True)
class HostInputs:
    context: ExecutionContext
    discovery: DiscoveryContext

    def __post_init__(self):
        if type(self.context) is not ExecutionContext or type(self.discovery) is not DiscoveryContext:
            raise TypeError("exact immutable host context and discovery required")


def make_scoped_impact_type(impact_type):
    """Trusted composition; subclass preserves source-root isinstance routing.

    current_inputs must be a host-owned provider returning ONE coherent snapshot
    for the current invocation. A constant closure over an old context is invalid.
    This factory neither issues authoritative IDs nor freezes ambient config.
    Thread-confined; no observation/publication or cache-origin claims.
    """

    class ScopedImpact(impact_type):
        def __init__(self, *, current_inputs):
            super().__init__()
            self._scope_admission = CacheAdmission()
            self._current_inputs = current_inputs

        def run(self, diff_text, repo_root):
            inputs = self._current_inputs()
            if type(inputs) is not HostInputs:
                raise TypeError("host must return one HostInputs snapshot")
            self._scope_admission.admit(inputs.context, diff_text, repo_root, inputs.discovery)
            return deepcopy(super().run(diff_text, repo_root))

    return ScopedImpact
