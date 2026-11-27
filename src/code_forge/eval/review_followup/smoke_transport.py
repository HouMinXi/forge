"""Direct, non-streaming HTTPS for the opt-in evaluation smoke only."""

import http.client
import math
import re
import ssl
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Literal

from .fixtures import InputError, digest, parse, require

_HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.ASCII)
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", re.ASCII)
_REQUEST_ID = re.compile(r"[a-f0-9]{32}", re.ASCII)
_KEY = re.compile(r"[A-Za-z0-9._~+/=-]{1,512}", re.ASCII)
_FINISH_REASONS = {"stop", "length", "content_filter", "tool_calls", "function_call"}
_CAP_FIELDS = {"max_tokens", "max_completion_tokens"}
_REQUEST_FIELDS = {"model", "messages", "stream", "temperature", "top_p", "seed", "n"} | _CAP_FIELDS


class SmokeWireError(RuntimeError):
    """No complete result is available; never treat this as permission to resend."""


@dataclass(frozen=True, slots=True)
class WireReceipt:
    status: int
    outcome: Literal[
        "accepted",
        "http_status",
        "unsupported_response",
        "response_over_limit",
        "malformed_response",
        "model_mismatch",
        "output_over_limit",
        "unfinished_response",
        "no_usable_reply",
        "private_reply_rejected",
    ]
    response_sha256: str | None = None
    reported_model: str | None = None
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    server_request_id_sha256: str | None = None
    finish_reason: str | None = None


def _count(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= 2**53 - 1 else None


def _has_key(doc: object, key: str) -> bool:
    # Examine decoded strings too: an echo can be JSON-escaped on the wire.
    pending = [iter((doc,))]
    while pending:
        for value in pending[-1]:
            if type(value) is str and key in value:
                return True
            if type(value) is list:
                pending.append(iter(value))
                break
            if type(value) is dict:
                pending.extend((iter(value), iter(value.values())))
                break
        else:
            pending.pop()
    return False


@dataclass(frozen=True, slots=True)
class DirectSmokeTransport:
    """Pin one named HTTPS host (port 443) and one model, without an SDK or proxy.

    The timeout applies to individual blocking socket operations, not the
    entire wall-clock duration. The caller must separately verify provider
    routing and billing before any real run; a model echo cannot verify them.
    """

    host: str
    model: str
    api_key: str = field(repr=False, compare=False)
    output_cap: int = 1024
    response_cap: int = 1024 * 1024
    timeout: float = 30.0

    def __post_init__(self) -> None:
        labels = self.host.split(".") if type(self.host) is str else []
        require(
            bool(labels)
            and len(self.host) <= 253
            and len(labels) >= 2
            and all(_HOST.fullmatch(label) for label in labels)
            and not labels[-1].isdigit(),
            "smoke_wire.host",
        )
        require(type(self.model) is str and bool(_MODEL.fullmatch(self.model)), "smoke_wire.model")
        require(type(self.api_key) is str and bool(_KEY.fullmatch(self.api_key)), "smoke_wire.api_key")
        require(type(self.output_cap) is int and 1 <= self.output_cap <= 32768, "smoke_wire.output_cap")
        require(
            type(self.response_cap) is int and 1 <= self.response_cap <= 4 * 1024 * 1024,
            "smoke_wire.response_cap",
        )
        require(
            type(self.timeout) in (int, float)
            and 0 < self.timeout <= 120
            and math.isfinite(self.timeout),
            "smoke_wire.timeout",
        )

    def check_body(self, body: bytes, *, retaining: bool = False) -> int:
        """Validate an already serialized request; never transform its bytes."""
        require(type(body) is bytes and 0 < len(body) <= 4 * 1024 * 1024, "smoke_wire.body")
        try:
            doc = parse(body, "smoke_wire.body")
        except InputError:
            doc = None
        require(type(doc) is dict and doc.keys() <= _REQUEST_FIELDS, "smoke_wire.body")
        require(doc.get("model") == self.model, "smoke_wire.model")
        require(doc.get("stream", False) is False, "smoke_wire.stream")
        n = doc.get("n", 1)
        require(type(n) is int and n == 1, "smoke_wire.n")
        messages = doc.get("messages")
        require(
            type(messages) is list
            and bool(messages)
            and all(
                type(m) is dict
                and m.keys() == {"role", "content"}
                and m["role"] in ("system", "developer", "user", "assistant")
                and type(m["content"]) is str
                for m in messages
            ),
            "smoke_wire.messages",
        )
        caps = doc.keys() & _CAP_FIELDS
        cap = doc[next(iter(caps))] if len(caps) == 1 else None
        require(type(cap) is int and 1 <= cap <= self.output_cap, "smoke_wire.output_cap")
        if retaining:
            require(
                self.api_key.encode("ascii") not in body and not _has_key(doc, self.api_key),
                "smoke_artifacts.credential_in_body",
            )
        return cap

    def __call__(self, body: bytes) -> WireReceipt:
        return self.send(body)

    def send(
        self,
        body: bytes,
        *,
        request_id: str | None = None,
        retain_reply: Callable[[bytes], None] | None = None,
    ) -> WireReceipt:
        require(
            request_id is None or (type(request_id) is str and bool(_REQUEST_ID.fullmatch(request_id))),
            "smoke_wire.request_id",
        )
        cap = self.check_body(body, retaining=retain_reply is not None)
        failure = "wire_io_unknown"
        try:
            # The default context factory can honor SSLKEYLOGFILE. Do not
            # opt this credential-bearing connection into TLS key logging.
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.load_default_certs()
            connection = http.client.HTTPSConnection(self.host, timeout=self.timeout, context=context)
            try:
                connection.request(
                    "POST",
                    "/v1/chat/completions",
                    body=body,
                    headers={
                        "Authorization": "Bearer " + self.api_key,
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "Accept-Encoding": "identity",
                        "Connection": "close",
                        **({"X-Client-Request-Id": request_id} if request_id is not None else {}),
                    },
                )
                with connection.getresponse() as response:
                    receipt = self._read(response, cap, retain_reply)
                    ids = response.headers.get_all("X-Request-Id", [])
                    if len(ids) == 1 and 0 < len(ids[0]) <= 512:
                        receipt = replace(
                            receipt, server_request_id_sha256=digest(ids[0].encode("latin-1"))
                        )
                    return receipt
            finally:
                connection.close()
        except TimeoutError:
            failure = "wire_timeout_unknown"
        except (OSError, http.client.HTTPException, ValueError):
            # Do not propagate server headers, low-level exceptions or secrets.
            pass
        raise SmokeWireError(failure)

    def _read(
        self,
        response: http.client.HTTPResponse,
        cap: int,
        retain_reply: Callable[[bytes], None] | None,
    ) -> WireReceipt:
        status = response.status
        if not 100 <= status <= 599:
            raise SmokeWireError("wire_protocol_unknown")
        if status != 200:
            return WireReceipt(status, "http_status")
        lengths = response.headers.get_all("Content-Length", [])
        encodings = response.headers.get_all("Transfer-Encoding", [])
        if (
            response.headers.get_content_type() != "application/json"
            or response.getheader("Content-Encoding", "identity").lower() != "identity"
            or len(lengths) > 1
            or (encodings and not (len(encodings) == 1 and response.chunked and not lengths))
            or (not response.chunked and response.length is None)
        ):
            return WireReceipt(status, "unsupported_response")
        if response.length is not None and response.length > self.response_cap:
            return WireReceipt(status, "response_over_limit")
        raw = response.read(self.response_cap + 1)
        if len(raw) > self.response_cap:
            return WireReceipt(status, "response_over_limit")
        if not response.chunked and response.length != 0:
            raise SmokeWireError("wire_truncated_unknown")
        response_hash = digest(raw)
        try:
            doc = parse(raw, "smoke_wire.response")
        except InputError:
            doc = None
        if type(doc) is not dict or "error" in doc:
            return WireReceipt(status, "malformed_response", response_hash)
        if doc.get("model") != self.model:
            return WireReceipt(status, "model_mismatch", response_hash)
        choices = doc.get("choices")
        if type(choices) is not list or len(choices) != 1 or type(choices[0]) is not dict:
            return WireReceipt(status, "malformed_response", response_hash, self.model)
        choice = choices[0]
        reason = choice.get("finish_reason")
        reason = reason if type(reason) is str and reason in _FINISH_REASONS else None
        usage = doc.get("usage")
        usage = usage if type(usage) is dict else {}
        input_tokens = _count(usage.get("prompt_tokens"))
        output_tokens = _count(usage.get("completion_tokens"))
        details = usage.get("prompt_tokens_details")
        cached = _count(details.get("cached_tokens")) if type(details) is dict else None
        if input_tokens is not None and cached is not None and cached > input_tokens:
            cached = None
        message = choice.get("message")
        if reason != "stop":
            outcome = "unfinished_response"
        elif (
            type(message) is not dict
            or message.get("role") != "assistant"
            or "tool_calls" in message
            or "function_call" in message
        ):
            outcome = "malformed_response"
        elif message.get("refusal") is not None:
            # A refusal is not an answer even if content is present. Do not
            # persist the provider's refusal text.
            outcome = "no_usable_reply"
        elif type(content := message.get("content")) is not str:
            outcome = "malformed_response"
        elif not content.strip():
            outcome = "no_usable_reply"
        elif output_tokens is not None and output_tokens > cap:
            outcome = "output_over_limit"
        else:
            outcome = "accepted"
        if outcome == "accepted" and retain_reply is not None:
            if self.api_key.encode("ascii") in raw or _has_key(doc, self.api_key):
                outcome = "private_reply_rejected"
            else:
                retain_reply(raw)
        return WireReceipt(
            status,
            outcome,
            response_hash,
            self.model,
            input_tokens,
            cached,
            output_tokens,
            finish_reason=reason,
        )
