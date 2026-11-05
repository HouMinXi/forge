"""Same-origin urllib redirects with an explicit intermediate-body capture seam.

Only the OpenAI invocation opts into this opener. The supplied ``capture_hop``
MUST consume each intermediate response using bounded reads and the supplied
absolute deadline, and seal a fresh capture before returning. It should discard
chunks after recording, rather than accumulating the entire redirect body.
The final response (including a refused redirect HTTPError) remains unread and
belongs to the caller's independent final-response capture.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import string
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any


@dataclass(frozen=True)
class RedirectHop:
    """Safe metadata only: never request headers, userinfo, query or fragment."""

    index: int
    status: int
    source: str
    target: str
    decision: str


def _origin(url: str) -> tuple[str, str, int]:
    if any(ord(char) < 32 or ord(char) == 127 for char in url):
        raise ValueError("invalid redirect address")
    parts = urllib.parse.urlsplit(url)
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or "\\" in parts.netloc
        or any(char.isspace() for char in parts.netloc)
    ):
        raise ValueError("invalid redirect address")
    port = parts.port
    return (
        parts.scheme,
        parts.hostname.lower(),
        (port if port is not None else (443 if parts.scheme == "https" else 80)),
    )


def safe_address(url: str) -> str:
    """Remove URL credential fields from diagnostic metadata."""
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname
        if parts.scheme not in ("http", "https") or not host:
            return "[invalid address]"
        if any(ord(char) < 32 or ord(char) == 127 for char in url):
            return "[invalid address]"
        host = f"[{host}]" if ":" in host else host
        port = parts.port
        netloc = host if port is None else f"{host}:{port}"
        return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, "", ""))
    except ValueError:
        return "[invalid address]"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

    # Do not enter the builtin handler: it can transform exceptions and its
    # successful redirect branch calls unbounded fp.read().
    def http_error_302(self, req, fp, code, msg, headers):
        return None

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def open_with_redirects(
    request: urllib.request.Request,
    *,
    deadline: float,
    capture_hop: Callable[[Any, RedirectHop, float], None],
    opener=None,
    max_hops: int = 10,
):
    """Open with bounded hop count, a shared budget and urllib POST semantics.

    ``opener`` is a test seam and MUST NOT follow redirects itself. Each open
    receives the remaining budget as its socket timeout. This does not promise
    a universal wall-clock limit for urllib DNS or response-header acquisition;
    the callback must independently enforce its body-read wall-clock deadline.
    Refused redirects retain the original HTTPError object, code, headers and
    body. ``redirect_hop`` provides safe refusal diagnostics to its caller.
    """
    if type(max_hops) is not int or max_hops < 0:
        raise ValueError("max_hops must be a nonnegative integer")
    if not callable(capture_hop):
        raise TypeError("capture_hop must be callable")
    if not math.isfinite(deadline):
        raise ValueError("deadline must be finite")
    origin = _origin(request.full_url)
    transport = opener if opener is not None else urllib.request.build_opener(_NoRedirect())
    policy = urllib.request.HTTPRedirectHandler()
    followed = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("redirect chain exceeded total deadline")
        try:
            return transport.open(request, timeout=remaining)
        except urllib.error.HTTPError as error:
            if error.code not in (301, 302, 303, 307, 308):
                raise
            location = error.headers.get("location", error.headers.get("uri"))
            target = ""
            decision = "redirect_missing_location"
            new_request = None
            if location is not None:
                try:
                    # Match urllib's latin-1 header decoding and URL quoting.
                    # Validate before quote/urljoin can hide control characters.
                    if any(ord(char) < 32 or ord(char) == 127 for char in location):
                        raise ValueError("invalid redirect address")
                    target = urllib.parse.urljoin(
                        request.full_url,
                        urllib.parse.quote(location, encoding="iso-8859-1", safe=string.punctuation),
                    )
                    target_origin = _origin(target)
                    if origin[0] == "https" and target_origin[0] == "http":
                        decision = "redirect_downgrade_refused"
                    elif target_origin != origin:
                        decision = "redirect_cross_origin_refused"
                    else:
                        decision = "follow"
                except (ValueError, UnicodeError):
                    decision = "redirect_invalid_target"
            if decision == "follow":
                try:
                    # A rejected method constructs a temporary HTTPError. Do not
                    # give that object ownership of the original response body.
                    new_request = policy.redirect_request(
                        request, None, error.code, error.msg, error.headers, target
                    )
                except urllib.error.HTTPError:
                    decision = "redirect_method_refused"
                if decision == "follow" and followed >= max_hops:
                    decision = "redirect_limit_reached"
            hop = RedirectHop(
                followed + 1,
                error.code,
                safe_address(request.full_url),
                safe_address(target),
                decision,
            )
            if decision != "follow":
                error.redirect_hop = hop
                raise
            try:
                capture_hop(error, hop, deadline)
            except BaseException:
                # Cleanup is secondary to an active acquisition failure, even
                # when either failure is fatal. Preserve the primary object and
                # its cause rather than letting close() replace it.
                try:
                    error.close()
                except BaseException:  # noqa: BLE001, S110 - preserve active primary
                    pass
                raise
            else:
                # With no acquisition failure, cleanup errors remain visible.
                error.close()
            followed += 1
            request = new_request
