"""R03 fixed-backend retry, not runtime cross-backend failover or native storage."""

import json

import pytest

from code_forge.llm_invoke import AuditPersistenceError, llm_invoke
from tests.test_invocation_host import MockRecorder
from tests.test_invocation_r03_host_dispatch import backend, inject  # noqa: F401
from tests.test_invocation_r03_review_routes import assert_refusal_persists
from tests.test_invocation_send_audit import http_error, reply, scripted

pytest_plugins = ["tests.test_invocation_host"]


@pytest.mark.parametrize("strict", [False, True], ids=["default", "strict"])
@pytest.mark.parametrize("fault", [False, True], ids=["healthy", "post-ack-diagnostic-failure"])
def test_same_backend_retry_keeps_audit_refusal(setup, monkeypatch, backend, strict, fault):  # noqa: F811
    host, notices = setup[0](strict=strict, control_timeout_s=2)
    owner = inject(host, monkeypatch, "lease_list_after", True) if fault else None
    transport = scripted(monkeypatch, [http_error(), reply()])

    def invoke():
        return llm_invoke(
            "synthetic fixed-backend retry",
            backend,
            timeout_s=5,
            max_attempts=2,
            initial_delay_s=0,
            host_audit=host.scope(),
        )

    if strict and fault:
        with pytest.raises(AuditPersistenceError):
            invoke()
        assert transport.requests == []
        assert_refusal_persists(host, owner)
    else:
        assert invoke().content["findings"] == []
        assert len(transport.requests) == 2
        assert [r.full_url for r, _ in transport.requests] == [
            "https://provider.invalid/v1/chat/completions"
        ] * 2
        assert [json.loads(r.data)["model"] for r, _ in transport.requests] == [backend.model] * 2
        if fault:
            assert_refusal_persists(host, owner)
            assert notices == ["invocation_audit_unavailable"]
        else:
            assert host._started == 2 and not host.failed
            assert sum(name == "start" for name, _ in MockRecorder.calls) == 2
