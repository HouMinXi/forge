"""Deadline-read failure hygiene: transport, guards, and degradation.

`_read_with_deadline` reads the body in a daemon thread and re-raises
whatever the worker captured, so the blind catch at the thread boundary
is a transport, not a swallow.  The two best-effort layers around the
socket (capture and idle-timeout install) must still let MemoryError
abort the call instead of silently dropping the hardening.
"""

import time

import pytest

from code_forge.llm_invoke import _read_with_deadline

DEGRADED_ERRORS = [AttributeError, OSError, RuntimeError]


class _Sock:
    def __init__(self, settimeout_error=None):
        self._settimeout_error = settimeout_error
        self.timeouts = []

    def settimeout(self, value):
        if self._settimeout_error is not None:
            raise self._settimeout_error
        self.timeouts.append(value)

    def shutdown(self, how):
        pass


class _Raw:
    def __init__(self, sock):
        self._sock = sock


class _Fp:
    def __init__(self, sock):
        self.raw = _Raw(sock)


class _BoomFp:
    @property
    def raw(self):
        raise MemoryError("simulated exhaustion in fp.raw")


class _Resp:
    def __init__(self, payload=b"data", read_error=None, fp=None):
        self._payload = payload
        self._read_error = read_error
        if fp is None:
            self.fp = _Fp(_Sock())
        elif fp == "boom":
            self.fp = _BoomFp()
        else:
            self.fp = fp
        self.closed = False

    def read(self):
        if self._read_error is not None:
            raise self._read_error
        return self._payload

    def close(self):
        self.closed = True


def _deadline(seconds=30):
    return time.monotonic() + seconds


# --- worker capture is a transport: every worker error must re-raise -------

def test_worker_memory_error_is_transported_and_reraised():
    """MemoryError in the worker thread must surface at the caller."""
    resp = _Resp(read_error=MemoryError("simulated exhaustion in read"))
    with pytest.raises(MemoryError):
        _read_with_deadline(resp, _deadline(), "probe")


@pytest.mark.parametrize("error_type", [ValueError, OSError])
def test_worker_ordinary_errors_still_transport(error_type):
    resp = _Resp(read_error=error_type("boom"))
    with pytest.raises(error_type):
        _read_with_deadline(resp, _deadline(), "probe")


# --- socket capture: best-effort for missing sockets, honest about OOM -----

@pytest.mark.parametrize("error_type", DEGRADED_ERRORS)
def test_socket_capture_degrades_on_ordinary_errors(error_type):
    """A response without a usable socket chain just skips the idle bound."""

    class OddFp:
        @property
        def raw(self):
            raise error_type("no socket here")

    resp = _Resp(fp=OddFp())
    assert _read_with_deadline(resp, _deadline(), "probe") == b"data"


def test_socket_capture_propagates_memory_error():
    resp = _Resp(fp="boom")
    with pytest.raises(MemoryError):
        _read_with_deadline(resp, _deadline(), "probe")


# --- idle-timeout install: warning on failure, propagation on OOM ----------

@pytest.mark.parametrize("error_type", [OSError, RuntimeError])
def test_settimeout_degrades_on_ordinary_errors(error_type):
    sock = _Sock(settimeout_error=error_type("cannot set"))
    resp = _Resp(fp=_Fp(sock))
    assert _read_with_deadline(resp, _deadline(), "probe") == b"data"


def test_settimeout_propagates_memory_error():
    sock = _Sock(settimeout_error=MemoryError("simulated exhaustion"))
    resp = _Resp(fp=_Fp(sock))
    with pytest.raises(MemoryError):
        _read_with_deadline(resp, _deadline(), "probe")


# --- happy path: idle timeout installed, body returned ----------------------

def test_happy_path_installs_idle_timeout_and_reads():
    sock = _Sock()
    resp = _Resp(fp=_Fp(sock))
    assert _read_with_deadline(resp, _deadline(), "probe") == b"data"
    assert len(sock.timeouts) == 1
    assert sock.timeouts[0] > 0
