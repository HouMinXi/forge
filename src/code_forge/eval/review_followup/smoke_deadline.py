"""Monotonic dispatch and socket-progress limit for the direct smoke."""

import http.client
import io
import time
from dataclasses import dataclass


class SmokeDeadlineExpired(TimeoutError):
    """The run elapsed budget is spent; never continue or retransmit."""


@dataclass(frozen=True, slots=True)
class SmokeDeadline:
    expires_at: float

    def remaining(self) -> float:
        remaining = self.expires_at - time.monotonic()
        if remaining <= 0:
            raise SmokeDeadlineExpired("smoke_deadline_exceeded")
        return remaining


class _TimedRead(io.RawIOBase):
    def __init__(self, sock, deadline: SmokeDeadline, idle: float):
        self._sock = sock
        self._raw = sock.makefile("rb", buffering=0)
        self._deadline = deadline
        self._idle = idle

    def readable(self) -> bool:
        return True

    def readinto(self, buffer):
        self._sock.settimeout(min(self._idle, self._deadline.remaining()))
        count = self._raw.readinto(buffer)
        self._deadline.remaining()
        return count

    def close(self) -> None:
        try:
            self._raw.close()
        finally:
            super().close()


class _ResponseSocket:
    """Adapt the stdlib response's binary file interface, not HTTP framing."""

    def __init__(self, sock, deadline: SmokeDeadline, idle: float):
        self._sock, self._deadline, self._idle = sock, deadline, idle

    def makefile(self, mode: str) -> io.BufferedReader:
        if mode != "rb":
            raise ValueError("smoke deadline response requires binary reading")
        return io.BufferedReader(_TimedRead(self._sock, self._deadline, self._idle))


class DeadlineHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, *, deadline: SmokeDeadline, idle: float, context):
        super().__init__(host, timeout=min(idle, deadline.remaining()), context=context)
        self._deadline, self._idle = deadline, idle
        self.response_class = self._response

    def send(self, data) -> None:
        if self.sock is None:
            self.timeout = min(self._idle, self._deadline.remaining())
            self.connect()
        # DNS/connect/TLS may return late. Do not let the stdlib send after that.
        self.sock.settimeout(min(self._idle, self._deadline.remaining()))
        super().send(data)
        self._deadline.remaining()

    def _response(self, sock, *args, **kwargs):
        return http.client.HTTPResponse(
            _ResponseSocket(sock, self._deadline, self._idle), *args, **kwargs
        )
