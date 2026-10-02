"""The builder guard preserves mapping failures and permits qualified hosts."""

import pytest

from code_forge.mutation_engines.adapters.builder_support import (
    BuilderUnavailable,
    require_identity_mapping,
)


@pytest.mark.parametrize(
    "reason",
    [None, "NoNewPrivs is set", "no subordinate uid range", "user namespace mapping failed"],
)
def test_mapping_guard_preserves_the_probe_result(monkeypatch, reason):
    calls = []

    def probe():
        calls.append(True)
        return reason

    monkeypatch.setattr(
        "code_forge.mutation_engines.adapters.builder_support.identity_mapping_error", probe
    )
    if reason is None:
        assert require_identity_mapping() is None
    else:
        with pytest.raises(BuilderUnavailable) as error:
            require_identity_mapping()
        assert str(error.value) == reason
    assert calls == [True]
