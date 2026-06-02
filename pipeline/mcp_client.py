"""
FahMai MCP Client — async adapter between agent.py and mcp_server.py.

Provides discovery, invocation, and OpenAI function-calling format translation
for all 5 FahMai tools served over MCP stdio.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


class FahMaiMCPClient:
    """Async MCP client that connects to the FahMai MCP server via stdio.

    Usage:
        async with FahMaiMCPClient() as client:
            tools = await client.list_tools()
            result = await client.call_tool("query_sql", {"sql": "SELECT 1"})
    """

    def __init__(self, server_path: str | Path | None = None):
        mcp_dir = Path(__file__).parent
        self._server_path = str(server_path or mcp_dir / "mcp_server.py")
        self._session: ClientSession | None = None
        self._read = None
        self._write = None
        self._process = None
        self._transport_ctx = None
        self._tool_cache: list[dict[str, Any]] | None = None

    async def connect(self) -> None:
        server_params = StdioServerParameters(
            command=sys.executable,
            args=[self._server_path],
        )
        self._transport_ctx = stdio_client(server_params)
        self._read, self._write = await self._transport_ctx.__aenter__()
        self._session = ClientSession(self._read, self._write)
        await self._session.initialize()

    async def list_tools(self) -> list[dict[str, Any]]:
        if self._session is None:
            raise RuntimeError("Not connected — call connect() first")
        result = await self._session.list_tools()
        self._tool_cache = [
            {
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.inputSchema,
            }
            for tool in result.tools
        ]
        return self._tool_cache

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        if self._session is None:
            raise RuntimeError("Not connected — call connect() first")
        result = await self._session.call_tool(name, arguments or {})
        parts = []
        for block in result.content:
            if hasattr(block, "text"):
                parts.append(block.text)
        content = "\n".join(parts)
        if result.isError:
            content = f"Tool Error: {content}"
        return content

    async def to_openai_definitions(self) -> list[dict[str, Any]]:
        """Convert MCP tool list to OpenAI function-calling format."""
        tools = await self.list_tools()
        definitions = []
        for tool in tools:
            schema = tool["input_schema"] or {"type": "object", "properties": {}}
            definitions.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": schema,
                },
            })
        return definitions

    async def close(self) -> None:
        if self._session is not None:
            await self._session.__aexit__(None, None, None)
            self._session = None
        if hasattr(self, '_transport_ctx') and self._transport_ctx is not None:
            await self._transport_ctx.__aexit__(None, None, None)
            self._transport_ctx = None
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            self._process.wait(timeout=5)
        self._session = None
        self._read = None
        self._write = None
        self._process = None

    async def __aenter__(self) -> "FahMaiMCPClient":
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()


# ---------------------------------------------------------------------------
# In-process client (for testing — avoids subprocess overhead)
# ---------------------------------------------------------------------------

class FahMaiMCPClientInProcess:
    """In-process MCP client for testing — uses direct memory transport.

    This bypasses subprocess spawning and connects directly to the FastMCP
    server instance via ClientSession with in-memory streams.
    """

    def __init__(self):
        self._session: ClientSession | None = None
        self._tool_cache: list[dict[str, Any]] | None = None

    async def connect(self) -> None:
        from mcp.server.fastmcp import FastMCP
        from mcp.client.stdio import stdio_client
        import mcp_server

        # Use in-memory transport: connect via client to the server instance
        server = mcp_server.mcp
        transport = await stdio_client(
            StdioServerParameters(
                command=sys.executable,
                args=[mcp_server.__file__],
            )
        )
        read, write = transport
        self._session = ClientSession(read, write)
        await self._session.initialize()

    async def list_tools(self) -> list[dict[str, Any]]:
        if self._session is None:
            raise RuntimeError("Not connected")
        result = await self._session.list_tools()
        self._tool_cache = [
            {
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.inputSchema,
            }
            for tool in result.tools
        ]
        return self._tool_cache

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        if self._session is None:
            raise RuntimeError("Not connected")
        result = await self._session.call_tool(name, arguments or {})
        parts = []
        for block in result.content:
            if hasattr(block, "text"):
                parts.append(block.text)
        content = "\n".join(parts)
        if result.isError:
            content = f"Tool Error: {content}"
        return content

    async def to_openai_definitions(self) -> list[dict[str, Any]]:
        tools = await self.list_tools()
        definitions = []
        for tool in tools:
            schema = tool["input_schema"] or {"type": "object", "properties": {}}
            definitions.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": schema,
                },
            })
        return definitions

    async def close(self) -> None:
        self._session = None

    async def __aenter__(self) -> "FahMaiMCPClientInProcess":
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
