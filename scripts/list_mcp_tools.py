"""Enumerate the tools exposed by the local mcp-grafana server over stdio.

Run this before naming any Grafana tool in agent code, so tool names come from
the running server rather than from documentation that may lag the binary.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

BIN = Path(__file__).resolve().parents[1] / "bin" / "mcp-grafana.exe"


async def main() -> None:
    params = StdioServerParameters(
        command=str(BIN),
        args=["--transport", "stdio"],
        env={
            **os.environ,
            "GRAFANA_URL": os.environ.get("GRAFANA_URL", "https://example.grafana.net"),
            "GRAFANA_SERVICE_ACCOUNT_TOKEN": os.environ.get(
                "GRAFANA_SERVICE_ACCOUNT_TOKEN", "placeholder"
            ),
        },
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            print(f"TOTAL TOOLS: {len(tools)}\n")
            for t in sorted(tools, key=lambda x: x.name):
                first_line = (t.description or "").strip().splitlines()
                print(f"{t.name:38s} {first_line[0][:90] if first_line else ''}")


if __name__ == "__main__":
    if not BIN.exists():
        sys.exit(f"missing binary: {BIN}")
    asyncio.run(main())
