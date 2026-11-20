import http.client
import json
import ssl
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from transport_cap import ReviewSendCap, SendRefused, capped_api


def test_six_physical_admissions_and_no_retry_or_other_destination():
    cap = ReviewSendCap(hostname="fixture.invalid", port=443, target="/v1/chat/completions")
    sent = []
    right = http.client.HTTPSConnection("fixture.invalid")
    wrong = http.client.HTTPSConnection("other.invalid")

    def fake(connection, method, target, *_args, **_kwargs):
        sent.append((connection.host, method, target))
        raise OSError("synthetic failure")

    with patch.object(http.client.HTTPConnection, "request", fake), cap.installed():
        with pytest.raises(SendRefused):
            right.request("POST", cap.target)
        for arm in ("A", "B"):
            for pass_name in ("qodo", "expert", "adversarial"):
                with cap.pass_slot(arm, pass_name):
                    with pytest.raises(SendRefused):
                        wrong.request("POST", cap.target)
                    with pytest.raises(OSError, match="synthetic failure"):
                        right.request("POST", cap.target)
                    with pytest.raises(SendRefused):
                        right.request("POST", cap.target)
    assert len(sent) == len(cap.used) == 6
    assert cap.blocked == 13


def test_proxy_route_never_reaches_transport():
    cap = ReviewSendCap(hostname="fixture.invalid", port=443, target="/v1/chat/completions")
    connection = http.client.HTTPSConnection("fixture.invalid")
    connection.set_tunnel("other.invalid")
    with cap.pass_slot("A", "qodo"), pytest.raises(SendRefused):
        cap.send(
            lambda *_: pytest.fail("unexpected transport"),
            connection,
            "POST",
            cap.target,
        )
    assert not cap.used


def test_pinned_proxy_only_accepts_verified_tls_to_original_destination():
    import ssl

    cap = ReviewSendCap(
        hostname="fixture.invalid",
        port=443,
        target="/v1/chat/completions",
        proxy=("proxy.invalid", 8080),
    )
    proxy = http.client.HTTPSConnection("proxy.invalid", 8080)
    changed = http.client.HTTPSConnection("proxy.invalid", 8080)
    insecure = http.client.HTTPSConnection(
        "proxy.invalid",
        8080,
        context=ssl._create_unverified_context(),  # noqa: S323 - negative test, no socket I/O.
    )
    direct = http.client.HTTPSConnection("fixture.invalid")
    proxy.set_tunnel("fixture.invalid")
    changed.set_tunnel("different.invalid")
    insecure.set_tunnel("fixture.invalid")

    with cap.pass_slot("A", "qodo"):
        for rejected in (changed, insecure, direct):
            with pytest.raises(SendRefused):
                cap.send(
                    lambda *_: pytest.fail("unexpected transport"),
                    rejected,
                    "POST",
                    cap.target,
                )
        cap.send(lambda *_: None, proxy, "POST", cap.target)

    assert len(cap.used) == 1 and cap.blocked == 3


@pytest.mark.parametrize("field", ["max_tokens", "max_completion_tokens"])
def test_cloud_contract_checks_wire_body_before_spending_slot(field):
    cap = ReviewSendCap(
        hostname="fixture.invalid",
        port=443,
        target="/v1/chat/completions",
        wire_contract=("agnes-cn", 131_072),
    )
    connection = http.client.HTTPSConnection("fixture.invalid")
    payload = {"model": "agnes-cn", field: 131_072}
    other = "max_completion_tokens" if field == "max_tokens" else "max_tokens"
    refused = [
        b"not json",
        b"[]",
        b"{" * 100_000,
        b" " * 262_145,
        b'{"model":"agnes-cn","model":"agnes-cn","max_tokens":131072}',
        *[
            json.dumps(value).encode()
            for value in (
                {"model": "agnes-cn"},
                {**payload, "model": "agnes-cn-3"},
                {**payload, field: 65_536},
                {**payload, field: 131_073},
                {**payload, field: True},
                {**payload, field: "131072"},
                {**payload, other: 131_072},
            )
        ],
    ]
    with cap.pass_slot("A", "expert"):
        for body in refused:
            with pytest.raises(SendRefused):
                cap.send(
                    lambda *_: pytest.fail("unexpected transport"),
                    connection,
                    "POST",
                    cap.target,
                    body,
                )
        assert not cap.used
        cap.send(
            lambda *_: None,
            connection,
            "POST",
            cap.target,
            json.dumps(payload).encode(),
        )
    assert len(cap.used) == 1 and cap.blocked == len(refused)


@pytest.mark.parametrize(
    "base,model",
    [
        ("https://other.invalid/v1", "agnes-cn"),
        ("https://fixture.invalid/v1", "agnes-cn-3"),
    ],
)
def test_cloud_contract_rejects_unapproved_profile(base, model):
    backend = SimpleNamespace(type="api", format="openai", base_url=base, model=model)
    with (
        pytest.raises(ValueError, match="approved"),
        capped_api(
            backend,
            required_base_url="https://fixture.invalid/v1",
            wire_contract=("agnes-cn", 131_072),
        ),
    ):
        pytest.fail("unexpected transport setup")


def test_direct_https_requires_peer_verification():
    cap = ReviewSendCap(hostname="fixture.invalid", port=443, target="/v1/chat/completions")
    unsafe = http.client.HTTPSConnection(
        "fixture.invalid",
        context=ssl._create_unverified_context(),  # noqa: S323 - no socket I/O.
    )
    with cap.pass_slot("A", "qodo"), pytest.raises(SendRefused):
        cap.send(lambda *_: pytest.fail("unexpected transport"), unsafe, "POST", cap.target)
    assert not cap.used


def test_cloud_contract_sees_serialized_urllib_body():
    from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

    target = "/v1/chat/completions"
    cap = ReviewSendCap(
        hostname="fixture.invalid",
        port=443,
        target=target,
        wire_contract=("agnes-cn", 131_072),
    )
    admitted = []

    class StopBeforeSocket(RuntimeError):
        pass

    def fake_request(_connection, method, path, body, *_args, **_kwargs):
        admitted.append((method, path, json.loads(body)))
        raise StopBeforeSocket

    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context()))
    with (
        patch.object(http.client.HTTPConnection, "request", fake_request),
        cap.installed(),
        cap.pass_slot("B", "qodo"),
    ):
        for limit, error_type in ((65_536, SendRefused), (131_072, StopBeforeSocket)):
            request = Request(
                "https://fixture.invalid" + target,
                data=json.dumps({"model": "agnes-cn", "max_tokens": limit}).encode(),
                method="POST",
            )
            with pytest.raises(error_type):
                opener.open(request, timeout=1)
    assert admitted == [("POST", target, {"model": "agnes-cn", "max_tokens": 131_072})]
