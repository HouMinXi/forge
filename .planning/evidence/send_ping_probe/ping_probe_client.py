"""Positive control: drive ping_probe_server with the Python MCP client SDK.

Proves the server's send_ping code works and that a spec-compliant client
(the Python reference client) answers server-initiated pings. This is the
control, NOT the real-client test (Claude Code ships its own TS client).
"""
import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    server_py = sys.argv[1] if len(sys.argv) > 1 else "python3.14"
    here = os.path.dirname(os.path.abspath(__file__))
    server_script = os.path.join(here, "ping_probe_server.py")
    params = StdioServerParameters(
        command=server_py, args=[server_script]
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("probe_ping", {})
            text = result.content[0].text if result.content else str(result)
            print("TOOL RESULT:", text)


if __name__ == "__main__":
    asyncio.run(main())
