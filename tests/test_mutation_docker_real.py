"""Dockerfile fixture on a host that cannot map identities.

The qualified builder needs a user namespace. This session has
NoNewPrivs set, so the fixture must refuse with a reason. A skip or a
pass would hide that the image was never built.
"""

from code_forge.mutation_engines.adapters.builder_support import (
    BuilderUnavailable,
    identity_mapping_error,
    require_identity_mapping,
)


def test_this_session_refuses_instead_of_passing():
    reason = identity_mapping_error()
    assert reason is not None
    try:
        require_identity_mapping()
    except BuilderUnavailable as exc:
        assert str(exc)
    else:
        raise AssertionError("a host that cannot map identities was allowed to build")
