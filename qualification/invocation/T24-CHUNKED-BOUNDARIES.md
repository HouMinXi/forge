# T24: chunked entity capture and framing truncation

Requirement: [#68 T24](https://github.com/HouMinXi/forge/issues/68#issuecomment-6599263807).
This slice adds real owned-loopback chunked HTTP observations, not a claim of
native recorder durability, arbitrary transports, or full T24 completion.
HostAuditSession, owner, worker, urllib and sockets are real; storage is the
existing synthetic MockRecorder. Authentication and payloads are synthetic.

## Observed defect and narrow repair

On CPython 3.13, standard `HTTPResponse.read1` can first return a complete chunk's
entity bytes, then raise `IncompleteRead(partial=b"\r")` while consuming its
truncated CRLF delimiter on the next call. The old acquisition adapter appended
that delimiter to its body capture. A real loopback response with a 161-byte JSON
entity and a truncated chunk delimiter produced 162 retained bytes.

The repair excludes this exception partial only when all of these hold:

- the response has exactly the standard `HTTPResponse` type;
- the selected method is its bound standard `read1`;
- chunked decoding is active;
- `chunk_left` was zero **before** the read.

The exception is re-raised unchanged. The corrected capture has the original
161 bytes and their hash, remains partial, and does not become a successful JSON
result. This is decoded entity-body capture, not TCP bytes or transfer framing.
No byte-content heuristic, exception-chain mining, budget change or fallback is
introduced. Unknown subclasses, custom readers and ordinary `read` retain their
existing exception-partial contract.

CPython implementation reference:
[http.client in Python 3.13](https://github.com/python/cpython/blob/3.13/Lib/http/client.py).
This behavior is version-sensitive; the tests execute the local implementation.

## Real transport matrix

`tests/test_invocation_t24_chunked_boundaries.py` checks five scenarios:

| Scenario | Expected retained entity | Integrity and outcome |
| --- | --- | --- |
| Complete two chunks and zero terminator | Full 161 bytes | Complete; successful result |
| EOF in second chunk | First 87 entity bytes | Partial; transport failure |
| EOF in first chunk | First 7 entity bytes | Partial; transport failure |
| Complete JSON but missing zero terminator | Full 161 bytes | Still partial; must not parse as success |
| Complete JSON plus truncated chunk CRLF | Full 161 bytes, no appended CR | Partial; same IncompleteRead instance as public cause |

Before the server exposes EOF in truncated cases, this matrix waits for actual supervisor APPEND
completion after owner/sink validation, with `ack` and accumulated accepted bytes
equal to the fixed expected prefix, with publication uncertainty false. Under the
supervisor condition it reads committed bytes plus remaining completed-ticket
ACK bytes, verifies the stage bytes and unsealed state, then releases the condition
before allowing server EOF. The healthy case needs no artificial ACK gate.
This observes real ticket outcomes; it does
not manufacture ACKs, invoke reconciliation, seal, drain, or extend the invocation
budget. It deliberately controls EOF timing and qualifies only that ACKed prefix.

The initial unsynchronized probe produced legitimate cancellation races: one
combined run had 276 passes and one empty-prefix failure, and a separate run had
four passes and one empty-prefix failure. Interrupted seal intentionally does not
wait for pending writes; bytes observed by a reader are not necessarily committed.
Those failures are not erased by the synchronized tests. The existing
`test_interruption_does_not_wait_for_drain` remains a separate cancellation control.
No guarantee that every read prefix survives arbitrary interruption is claimed.

All failures retain the exact read observer's exception as the public error's
cause. They have no invented usage. All five attempts settle through the real
owner path into the synthetic recorder; failure records identify the same attempt,
`failed`, `LLMInvokeError`, and `entered_api_send`. The observer delegates at
`_read1_chunked`, leaving the standard `read1` method identity intact.

## Negative controls and cleanup

Ten unit cases protect native `read1` versus `read` fallback, genuine entity data
ending in CR, custom `read`/`read1` partial CR or CRLF, and subclasses overriding
`read1` or inheriting it while overriding `_read1_chunked`.

An in-memory run of the original production method fails the new real delimiter
case and both native-read1 delimiter cases: **3 failed, 2 passed**. No checkout
rollback was required. The fixed implementation passes those controls.

The server half-closes its write direction to signal EOF while retaining its
read direction until it sees the client's EOF. Exact loopback destination guards,
bounded request reads, finite response scripts, server thread joins, actual client
FD closure and response close observations avoid substituting fixture shutdown
for application cleanup. Host/worker cleanup is explicit. Missing close-observer
evidence is rejected. No external service or credentials are used.

## Scope

This corrects and tests the identified standard-HTTPResponse framing boundary.
It does not claim to qualify every malformed chunk syntax, trailers, DNS, TLS,
HTTP/2, unknown custom transports, native persistence, or all fixed-length/EOF
cases from the broader T24 requirement.
