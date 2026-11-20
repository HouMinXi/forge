"""Experiment-local admission control for Forge's stdlib HTTPS sends."""

import http.client
import json
import ssl
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock
from unittest.mock import patch


class SendRefused(RuntimeError):
    """No network send was admitted for the request."""


class ReviewSendCap:
    """One admitted HTTPS model POST per arm/pass, six maximum.

    Only intended for a prevalidated HTTP API backend whose Forge
    implementation uses http.client. It is not an operating-system egress cap
    and must not be used for subprocess/CLI or SDK-based backends.
    """

    def __init__(self, *, hostname, port, target, proxy=None, wire_contract=None):
        self.hostname = hostname.casefold()
        self.port = port
        self.target = target
        self.proxy = proxy
        self.wire_contract = wire_contract
        self.slot = ContextVar("forge537_slot", default=None)
        self.used = set()
        self.usage = {}
        self.validated = set()
        self.prompts = {}
        self.blocked = 0
        self.lock = Lock()

    @contextmanager
    def pass_slot(self, arm, pass_name):
        if arm not in ("A", "B") or pass_name not in ("qodo", "expert", "adversarial"):
            raise ValueError("unexpected experiment slot")
        token = self.slot.set((arm, pass_name))
        try:
            yield
        finally:
            self.slot.reset(token)

    def _matches_route(self, connection):
        context = connection._context
        if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
            return False
        authority = (connection.host.casefold(), connection.port)
        origin = (self.hostname, self.port)
        tunnel = getattr(connection, "_tunnel_host", None)
        if self.proxy is None:
            return authority == origin and tunnel is None
        if tunnel is None or authority != self.proxy:
            return False
        return (tunnel.casefold(), connection._tunnel_port) == origin

    def _matches_body(self, body):
        if self.wire_contract is None:
            return True
        if not isinstance(body, bytes) or len(body) > 262_144:
            return False

        def unique_pairs(pairs):
            result = dict(pairs)
            if len(result) != len(pairs):
                raise ValueError("duplicate request key")
            return result

        try:
            data = json.loads(body.decode("utf-8"), object_pairs_hook=unique_pairs)
        except (ValueError, RecursionError):
            return False
        if not isinstance(data, dict):
            return False
        model, limit = self.wire_contract
        caps = [data[k] for k in ("max_tokens", "max_completion_tokens") if k in data]
        return (
            data.get("model") == model and len(caps) == 1 and type(caps[0]) is int and caps[0] == limit
        )

    def send(self, forward, connection, method, target, *args, **kwargs):
        slot = self.slot.get()
        with self.lock:
            if (
                slot is None
                or slot in self.used
                or not isinstance(connection, http.client.HTTPSConnection)
                or not self._matches_route(connection)
                or method != "POST"
                or target != self.target
                or not self._matches_body(args[0] if args else kwargs.get("body"))
            ):
                self.blocked += 1
                raise SendRefused("experiment refused a request before send")
            # Count even if the transport later fails. No retries or redirects
            # receive a new reservation under the same pass slot.
            self.used.add(slot)
        return forward(connection, method, target, *args, **kwargs)

    @contextmanager
    def installed(self):
        request = http.client.HTTPConnection.request
        if http.client.HTTPSConnection.request is not request:
            raise RuntimeError("unexpected HTTPS transport; cap not installed")

        def guarded(connection, method, target, *args, **kwargs):
            return self.send(request, connection, method, target, *args, **kwargs)

        with patch.object(http.client.HTTPConnection, "request", guarded):
            yield self


# The disposable factory overlay consults this binding only when an experiment
# installs it. No other Forge caller, including the falsifier, gets a slot.
ACTIVE_CAP = ContextVar("forge537_active_cap", default=None)


def check_profile(backend):
    active = ACTIVE_CAP.get()
    if active is not None:
        _, selected = active
        # BackendConfig's equality deliberately omits these two fields.
        if (
            backend != selected
            or backend.params != selected.params
            or backend.headers != selected.headers
        ):
            raise SendRefused("unexpected backend for experiment")


@contextmanager
def experiment_pass(arm, pass_name, backend):
    active = ACTIVE_CAP.get()
    if active is None:
        yield
        return
    check_profile(backend)
    with active[0].pass_slot(arm, pass_name):
        yield


@contextmanager
def capped_api(backend, *, match_system_proxy=False, required_base_url=None, wire_contract=None):
    from urllib.parse import urlsplit

    if backend.type != "api" or backend.format != "openai" or not backend.base_url:
        raise ValueError("experiment needs an OpenAI-format API profile")
    if required_base_url is not None and backend.base_url != required_base_url:
        raise ValueError("experiment base URL does not match approved route")
    if wire_contract is not None and backend.model != wire_contract[0]:
        raise ValueError("experiment model does not match approved model")
    parsed = urlsplit(backend.base_url + "/chat/completions")
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.startswith("//")
    ):
        raise ValueError("experiment needs a direct HTTPS endpoint")
    proxy = None
    if match_system_proxy:
        from urllib.request import getproxies, proxy_bypass

        configured = getproxies().get("https")
        if configured and not proxy_bypass(parsed.netloc):
            route = urlsplit(configured)
            # Fail closed for richer configurations rather than reinterpret
            # urllib's implicit ports or print proxy credentials.
            if (
                route.scheme != "http"
                or not route.hostname
                or not route.port
                or route.username is not None
                or route.password is not None
                or route.path not in ("", "/")
                or route.query
                or route.fragment
            ):
                raise ValueError("experiment cannot pin the configured proxy route")
            proxy = (route.hostname.casefold(), route.port)
    cap = ReviewSendCap(
        hostname=parsed.hostname,
        port=parsed.port or 443,
        target=parsed.path,
        proxy=proxy,
        wire_contract=wire_contract,
    )
    if ACTIVE_CAP.get() is not None:
        raise RuntimeError("experiment cap already active")
    with cap.installed():
        token = ACTIVE_CAP.set((cap, backend))
        try:
            yield cap
        finally:
            ACTIVE_CAP.reset(token)


def record_usage(arm, pass_name, usage):
    active = ACTIVE_CAP.get()
    if active is None:
        return
    cap, _ = active
    fields = ("input_tokens", "output_tokens", "cached_input_tokens")
    with cap.lock:
        slot = (arm, pass_name)
        if slot not in cap.used or slot in cap.usage:
            raise SendRefused("unexpected usage record")
        cap.usage[slot] = {
            name: getattr(usage, name) if name in usage.known_fields else None for name in fields
        }


def record_validated(arm, pass_name):
    active = ACTIVE_CAP.get()
    if active is None:
        return
    cap, _ = active
    with cap.lock:
        slot = (arm, pass_name)
        if slot not in cap.usage or slot in cap.validated:
            raise SendRefused("unexpected validation record")
        cap.validated.add(slot)


def check_prompt(arm, pass_name, prompt):
    active = ACTIVE_CAP.get()
    if active is None:
        return
    cap, _ = active
    from code_forge.lenses import LENS_BLOCK, PASS_LENSES

    if len(prompt.encode("utf-8")) > 32_768:
        raise SendRefused("experiment prompt exceeded byte bound")
    with cap.lock:
        if arm == "A":
            if pass_name in cap.prompts or prompt.count(LENS_BLOCK) != 1:
                raise SendRefused("experiment baseline prompt not unique")
            cap.prompts[pass_name] = prompt
        else:
            reference = cap.prompts.get(pass_name)
            header, cards = LENS_BLOCK.split("L1 ", 1)
            trimmed = header + "".join(
                card
                for card in ("L1 " + cards).splitlines(keepends=True)
                if card[:2] in PASS_LENSES[pass_name]
            )
            if reference is None or prompt != reference.replace(LENS_BLOCK, trimmed, 1):
                raise SendRefused("experiment B differs outside unassigned lens cards")
