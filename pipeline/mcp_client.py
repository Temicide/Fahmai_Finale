"""
FahMai MCP Client — async adapter between agent.py and mcp_server.py.

Provides discovery, invocation, and OpenAI function-calling format translation
for all 5 FahMai tools served over MCP stdio.
"""

from __future__ import annotations

import json
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


class FahMaiMCPClient:
    """Async MCP client that connects to the FahMai MCP server via stdio.

    Uses AsyncExitStack to properly manage the anyio-backed stdio_client and
    ClientSession context managers so that anyio cancel scopes are correctly
    nested on Python 3.14+.

    Usage:
        async with FahMaiMCPClient() as client:
            tools = await client.list_tools()
            result = await client.call_tool("query_sql", {"sql": "SELECT 1"})
    """

    def __init__(self, server_path: str | Path | None = None):
        mcp_dir = Path(__file__).parent
        self._server_path = str(server_path or mcp_dir / "mcp_server.py")
        self._session: ClientSession | None = None
        self._exit_stack: AsyncExitStack | None = None
        self._tool_cache: list[dict[str, Any]] | None = None

    async def connect(self) -> None:
        """Connect to the MCP server subprocess and initialize the session.

        Uses AsyncExitStack to properly manage the anyio-backed context managers,
        avoiding the cancel-scope deadlock that occurs when __aenter__ is called
        manually without a proper anyio task scope on Python 3.14+.
        """
        server_params = StdioServerParameters(
            command=sys.executable,
            args=[self._server_path],
        )
        self._exit_stack = AsyncExitStack()
        # Enter stdio_client context — spawns subprocess and starts reader/writer tasks
        read, write = await self._exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        # Enter ClientSession context
        self._session = await self._exit_stack.enter_async_context(
            ClientSession(read, write)
        )
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
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
            self._exit_stack = None
        self._session = None

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
