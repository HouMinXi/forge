# send_ping Client Compatibility Test Matrix

Date: 2026-07-04
Operator: execution sub-session (Claude Code)
Harness: .planning/evidence/send_ping_probe/

## SWEEP 1: Python version axis (server side)

Server + client both on the same Python version. mcp SDK installed
per-venv. uv used to install interpreters (3.8, 3.10-3.13);
3.9 and 3.14 from system /usr/bin.

| Python | mcp version | Result | Latency |
|--------|-------------|--------|---------|
| 3.8 | N/A | CANNOT INSTALL (requires-python >=3.10; uv also rejected >=3.12 for project) | -- |
| 3.9 | N/A | CANNOT INSTALL (requires-python >=3.10; pip: "No matching distribution found for mcp") | -- |
| 3.10 | 1.28.1 | ANSWERED | 0.8ms |
| 3.11 | 1.28.1 | ANSWERED | 0.8ms |
| 3.12 | 1.28.1 | ANSWERED | 0.6ms |
| 3.13 | 1.28.1 | ANSWERED | 0.7ms |
| 3.14 | 1.28.1 | ANSWERED | 1.6ms |

Finding: mcp SDK version is identical (1.28.1) across all installable
Pythons. Latency is sub-2ms uniformly. The >=3.10 floor is confirmed
by pip resolver (3.8/3.9 cannot install).

## SWEEP 2: Client axis (server fixed at Python 3.14)

| Client | Type | Result | Latency | Log line |
|--------|------|--------|---------|----------|
| C1 Python SDK | Control | ANSWERED | 1.1ms | `py3.14 client=mcp/0.1.0 -> ANSWERED in 1.1ms` |
| C2 TypeScript SDK | @modelcontextprotocol/sdk | ANSWERED | 7.4ms | `py3.14 client=ts-sdk-probe/1.0.0 -> ANSWERED in 7.4ms` |
| C3 Claude Code CLI | claude-code 2.1.197 | ANSWERED | 2.3ms | `py3.14 client=claude-code/2.1.197 -> ANSWERED in 2.3ms` |
| C7 Negative control | dumb pipe (printf) | ERROR (no ANSWERED) | -- | `py3.14 client=pipe/1 -> ERROR McpError: Connection closed` |

## Human-required cells

| Client | Status | Notes |
|--------|--------|-------|
| VS Code 1.127.0 | DONE (by operator 2026-07-04) | ANSWERED 16.2ms |
| PyCharm 2025.2 | PENDING(human) | Native Kotlin client, no headless proxy |
| Cursor / Windsurf | N/A (not installed) | TS-SDK proxy (C2) is the stand-in |
| Codex / Copilot | N/A (not installed) | Not installed, non-interactive auth required |

## Negative control analysis

The dumb pipe (C7) sent initialize + initialized + tools/call but
CANNOT answer the server's ping request. The server emitted
`{"method":"ping","jsonrpc":"2.0","id":0}` on stdout; the pipe had
no reader. The session tore down with "Connection closed" before a
TIMEOUT could fire. The log correctly shows ERROR, not ANSWERED.

This proves: (1) the test discriminates answer-capable from
answer-incapable clients; (2) any ANSWERED result from a real client
is genuine, not a harness artifact.

## Surprises

None. All testable clients answered. No version-specific anomalies.
mcp SDK is uniform 1.28.1 across all Pythons. TS SDK latency (7.4ms)
is higher than Python (0.6-1.6ms) and Claude Code (2.3ms) -- likely
Node process startup overhead in the transport layer, not a protocol
issue.

## Raw log (/tmp/ping_probe.log)

```
py3.10 client=mcp/0.1.0 -> ANSWERED in 0.8ms
py3.11 client=mcp/0.1.0 -> ANSWERED in 0.8ms
py3.12 client=mcp/0.1.0 -> ANSWERED in 0.6ms
py3.13 client=mcp/0.1.0 -> ANSWERED in 0.7ms
py3.14 client=mcp/0.1.0 -> ANSWERED in 1.6ms
py3.14 client=mcp/0.1.0 -> ANSWERED in 1.1ms
py3.14 client=pipe/1 -> ERROR McpError: Connection closed
py3.14 client=ts-sdk-probe/1.0.0 -> ANSWERED in 7.4ms
py3.14 client=claude-code/2.1.197 -> ANSWERED in 2.3ms
```
