# send_ping client-compat probe harness

Rescued from /tmp (tmpfs, wiped on reboot) on 2026-07-04. Purpose:
answer "does MCP client X answer a server-initiated send_ping" for
the Phase 38.4 GATE B question. See .planning/dispatch/draft_20260704_phase38.4_send_ping.txt
for the full decision context and results collected so far.

## Files

- `ping_probe_server.py` -- minimal FastMCP server; its one tool
  (`probe_ping`) calls `ctx.session.send_ping()` and logs the
  client's name/version + answered-or-timeout + latency to
  `/tmp/ping_probe.log` (recreate this path after reboot; tmpfs).
- `ping_probe_client.py` -- Python SDK positive control (drives the
  server directly, proves the harness itself works).

## Results so far (2026-07-04, all py3.14)

- Claude Code 2.1.197: ANSWERED 2.4ms (real, headless `claude -p`)
- VS Code 1.127.0: ANSWERED 16.2ms (REAL direct GUI, confirmed
  post-reboot after the --directory fix below)
- TS reference SDK 1.29.0: ANSWERED 2.5ms (proxy for Cursor/
  Windsurf; VS Code now confirmed directly)
- Python reference SDK 1.27.0: ANSWERED 1.0ms (control)
- PyCharm Gemini Code Assist (Gemini 3.1 Pro Preview): ANSWERED
  0.8ms. Raw log client=aiplugin-mcp-client-ping-probe-stdio/0.0.1
  (JetBrains "aiplugin" MCP host, one client instance per server).
  Configured at Settings > Tools > Gemini > MCP Servers, then
  /probe_ping in Gemini Agent mode invoked it directly.
- PyCharm GitHub Copilot (ACP agent): BLOCKED -- not a ping result.
  In the Copilot agent, /mcp list -> "No MCP servers configured".
  The AI Assistant MCP registry (green ping-probe) plus the "pass
  custom MCP servers" toggle do NOT reach the Copilot ACP agent, so
  probe_ping is unreachable and send_ping is never exercised. The
  Copilot MCP data point stands from VS Code above (16.2ms).
- KEY: PyCharm has THREE independent MCP registries -- AI Assistant
  (~/.ai/mcp/mcp.json), each ACP agent (Copilot/Claude/Codex, per
  agent), and Gemini Code Assist (Tools > Gemini > MCP Servers).
  Registering in one does NOT expose the server to the others. That
  is why every earlier attempt was "silently not found": the server
  was live in one registry while the acting agent read a different,
  empty one.

## How to re-run after reboot

### Positive control (Python SDK, sanity check the harness still works)
```
python3.14 /home/houminxi/code/forge/.planning/evidence/send_ping_probe/ping_probe_client.py
cat /tmp/ping_probe.log
```

### PyCharm (native, direct python, no sandbox)

CORRECTION (2026-07-04, from a live screenshot): PyCharm 2026.1 has
TWO different MCP settings pages, in OPPOSITE directions -- do not
confuse them:
- `工具 (Tools) > MCP 服务器 (MCP Server)` = PyCharm AS a server
  (exposes 127.0.0.1:64342 /sse + /stream, and auto-configures
  Cursor/Claude Code/Windsurf/Codex to connect INTO PyCharm). This
  is NOT what the send_ping test needs.
- `工具 (Tools) > AI Assistant > Model Context Protocol` = the
  CLIENT side, where you add an EXTERNAL stdio MCP server for the
  in-IDE assistant to consume. THIS is where the probe goes.

Steps (confirm exact add-flow inside that panel; PyCharm is NOT
sandboxed, so direct python works -- no flatpak-spawn/--directory):
1. Settings > 工具 (Tools) > AI Assistant > Model Context Protocol
   > add an MCP server (stdio): command `python3.14`, args
   `/home/houminxi/code/forge/.planning/evidence/send_ping_probe/ping_probe_server.py`
2. Open the assistant chat in agent/tool mode and prompt:
   "Call the probe_ping tool and paste its output."
   NOTE the active chat provider matters: if it is GitHub Copilot
   (not JetBrains AI Assistant), the MCP client under test is
   Copilot's, not JetBrains'. The server log's `client=NAME/VER`
   field records which one actually answered -- read it, do not
   assume.
3. `cat /tmp/ping_probe.log`

### VS Code (Flatpak) -- needs --directory, see the cwd bug below
Scratch workspace (does not touch the real forge mcp.json):
```
mkdir -p /tmp/ping-ws/.vscode
cat > /tmp/ping-ws/.vscode/mcp.json <<'JSON'
{
  "servers": {
    "ping-probe": {
      "type": "stdio",
      "command": "flatpak-spawn",
      "args": ["--host", "--directory=/tmp", "python3.14",
        "/home/houminxi/code/forge/.planning/evidence/send_ping_probe/ping_probe_server.py"]
    }
  }
}
JSON
```
Open `/tmp/ping-ws` in VS Code, start the ping-probe MCP server,
Agent chat prompt: "Call the probe_ping tool and paste its output."
`cat /tmp/ping_probe.log`.

## Bonus finding: Flatpak VS Code spawn cwd bug (fixed, generalizable)

`flatpak-spawn --host` inherits the caller's cwd. VS Code exposes
the open workspace as a document-portal path
(`/run/flatpak/doc/<hash>/<folder>`), a sandbox-only FUSE mount.
`flatpak-spawn --host` then tries to `chdir` there ON THE HOST,
which doesn't exist there -> "Failed to change to directory ...
(No such file or directory)", child exits 1, zero output, zero
error surfaced to the MCP server log (looks like a silent hang).
Fix: pass `--directory=<a real host path>` explicitly. Applies to
ANY Flatpak app spawning a host MCP server this way, not just
forge -- e.g. this is why forge's own VS Code entry also needed
`--directory=<host project root>` added (see main session's
2026-07-04 STATE.md entry / forge memory for the applied fix).
