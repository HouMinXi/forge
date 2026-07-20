"""Minimal MCP server that pings its client on a tool call and logs the result.

Purpose: determine empirically whether an MCP CLIENT answers a
SERVER-initiated ping (the send_ping liveness question). Logs to
/tmp/ping_probe.log so the result survives across client processes.
"""
import asyncio
import os
import sys
import time

from mcp.server.fastmcp import Context, FastMCP

LOG = "/tmp/ping_probe.log"
mcp = FastMCP("ping-probe")


def log(msg: str) -> None:
    with open(LOG, "a") as f:
        f.write(msg + "\n")
        f.flush()


@mcp.tool()
async def probe_ping(ctx: Context) -> str:
    """Send a ping to the connected client; report answered/timeout + latency."""
    client = "unknown"
    try:
        ci = ctx.session.client_params
        if ci and ci.clientInfo:
            client = "%s/%s" % (ci.clientInfo.name, ci.clientInfo.version)
    except Exception as e:  # noqa: BLE001
        client = "clientinfo-err:%s" % e
    t0 = time.monotonic()
    try:
        await asyncio.wait_for(ctx.session.send_ping(), timeout=5.0)
        dt = (time.monotonic() - t0) * 1000.0
        result = "ANSWERED in %.1fms" % dt
    except asyncio.TimeoutError:
        result = "TIMEOUT (no answer in 5s)"
    except Exception as e:  # noqa: BLE001
        result = "ERROR %s: %s" % (type(e).__name__, e)
    line = "py%d.%d client=%s -> %s" % (
        sys.version_info[0], sys.version_info[1], client, result,
    )
    log(line)
    return line


if __name__ == "__main__":
    # Transport is stdio by default (client spawns us). Set
    # PING_PROBE_TRANSPORT=streamable-http or sse to run as a
    # long-lived HTTP server the client connects to by URL;
    # PING_PROBE_PORT overrides the port (default 8765).
    transport = os.environ.get("PING_PROBE_TRANSPORT", "stdio")
    if transport != "stdio":
        mcp.settings.host = "127.0.0.1"
        mcp.settings.port = int(os.environ.get("PING_PROBE_PORT", "8765"))
    log("--- server start pid=%d py%d.%d transport=%s ---" % (
        os.getpid(), sys.version_info[0], sys.version_info[1], transport))
    mcp.run(transport=transport)
