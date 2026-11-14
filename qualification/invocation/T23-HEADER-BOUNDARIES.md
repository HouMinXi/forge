# T23: bounded status/header observations and real transport cleanup

Requirement: [#68 T23](https://github.com/HouMinXi/forge/issues/68#issuecomment-6599263807).

This slice exercises real audited OpenAI invocation, urllib, TCP loopback, and
HostAuditSession/owner/worker. Storage uses the existing synthetic MockRecorder.
It is not native persistence, external-provider, DNS, CLI, or power-loss qualification.

## Existing contract

`open_with_redirects` passes the remaining exchange budget as the socket timeout;
it explicitly does not promise a universal wall-clock limit for DNS or acquiring
response headers. Python documents `urlopen(timeout=...)` as a timeout for blocking
operations, and socket timeouts as limits on blocking socket operations:
[urllib.request](https://docs.python.org/3.13/library/urllib.request.html#urllib.request.urlopen),
[socket.settimeout](https://docs.python.org/3.13/library/socket.html#socket.socket.settimeout).
No production budget or specification changes are made here.

## Executable matrix

`tests/test_invocation_t23_header_boundaries.py` configures one attempt with a
one-second invocation timeout and an owned IPv4 loopback server. Exact destination
checks guard HTTPConnection, socket.connect, and getaddrinfo. All supplied headers,
body, request and authentication material are synthetic.

| Case | Script | Pre-cleanup audit observation |
| --- | --- | --- |
| Healthy | Complete status, headers, JSON body | One settled send, one wire reference, exact body/hash |
| Stall status | No status bytes; server awaits client EOF | TimeoutError FINISH call and one synthetic recorder FINISH; no capture/reference |
| Stall headers | Status sent, no remaining headers; await client EOF | Same as status stall |
| Drip status | Twelve bytes spaced 0.15 seconds apart before header completion | Exchange returns after more than 1.5 seconds, not within the configured one second; status-200 capture attempted, no FINISH call/reference, incomplete and uncertain host |
| Drip headers | Complete status, then identical finite header drip | Same as status drip |

The drip is finite and advances socket reads more frequently than the inactivity
timeout. It demonstrates the existing whole-exchange limitation, not an absolute
deadline guarantee. Expired capture admission disables the audit scope, so drip
cases must not be described as having acknowledged a terminal FINISH. They emit
`invocation_audit_unavailable`; default-mode behavior remains unchanged.

## Cleanup and bounds

Delegating observers call the real HTTPConnection/HTTPResponse implementations.
Before fixture shutdown, the server must observe client EOF, the retained client
socket has `fileno() == -1`, and HTTPResponse has `fp is None` with an observed real
close. No GC-triggered cleanup is used as evidence. Negative controls reject missing
close-observer evidence, missing peer EOF, and a genuinely open socket descriptor.
These are oracle-sensitivity controls, not production mutations.

The server has a total eight-second receive deadline and 64 KiB request bounds,
rejects premature request EOF, and uses finite drip writes. Its non-daemon thread
is joined and checked on exit. Host/worker cleanup is explicit after observing the
pre-cleanup audit state. The host's cleanup is not counted as a prior FINISH ACK.
EOF does not certify connection reuse or arbitrary platform/socket behavior.

## Local validation

On Python 3.13.12 with the repository's official dev/vertex dependencies, all five
matrix cases pass. This is a local test result, not a claim that CI or native
recorder qualification has completed.
