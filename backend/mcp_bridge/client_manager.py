"""
mcp/client_manager.py — Managed MCP client (moved from backend/mcp_client.py).

No functional changes to the MCP session management.
The only change: module path is now backend/mcp/client_manager.py.
"""

import asyncio
import copy
import json
import logging
import os
import sys
from pathlib import Path
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

log = logging.getLogger(__name__)

SERVER_SCRIPT = Path(__file__).parent.parent.parent / "mcp_server" / "server.py"


class MCPManager:
    def __init__(self):
        self.session: ClientSession | None = None
        self.exit_stack = AsyncExitStack()
        self._tools: list = []

    async def start(self) -> None:
        """Spawn the MCP server subprocess and initialize the session."""
        log.info(f"[MCP-CLIENT] Starting MCP server: {SERVER_SCRIPT}")
        server_params = StdioServerParameters(
            command=sys.executable,
            args=[str(SERVER_SCRIPT)],
            env={**os.environ, "MCP_TRANSPORT": "stdio"},
        )
        try:
            read, write = await self.exit_stack.enter_async_context(stdio_client(server_params))
            self.session = await self.exit_stack.enter_async_context(ClientSession(read, write))
            await self.session.initialize()
            log.info("[MCP-CLIENT] Session initialized.")
            await self._refresh_tools()
        except Exception as e:
            log.error(f"[MCP-CLIENT] Failed to start: {e}", exc_info=True)
            await self.stop()
            raise

    async def _refresh_tools(self) -> None:
        if not self.session:
            return
        result = await self.session.list_tools()
        self._tools = []
        for tool in result.tools:
            parameters = copy.deepcopy(tool.inputSchema)
            properties = parameters.get("properties", {})
            properties.pop("user_id", None)
            parameters["properties"] = properties
            parameters["required"] = [
                field for field in parameters.get("required", []) if field != "user_id"
            ]
            self._tools.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": parameters,
                },
            })
        log.info(f"[MCP-CLIENT] Loaded {len(self._tools)} tools.")

    def get_tools(self) -> list:
        return self._tools

    async def call_tool(self, tool_name: str, arguments: dict):
        if not self.session:
            return {"error": "MCP session not initialized"}
        try:
            result = await asyncio.wait_for(
                self.session.call_tool(tool_name, arguments),
                timeout=30,
            )
            if result.isError:
                # A raising tool returns a message that can embed the full SQL
                # statement and bound parameters on later lines. str() of the
                # content list is a single-line pydantic repr with escaped
                # newlines, so read .text and keep only the first line.
                detail = " ".join(
                    getattr(item, "text", "") or "" for item in (result.content or [])
                ).strip()
                if not detail:
                    detail = str(result.content)
                log.error(f"[MCP-CLIENT] Tool '{tool_name}' raised: {detail}")
                first_line = next(
                    (line.strip() for line in detail.splitlines() if line.strip()), ""
                )
                prefix = f"Error executing tool {tool_name}:"
                if first_line.startswith(prefix):
                    first_line = first_line[len(prefix):].strip()
                return {"error": f"Tool '{tool_name}' failed: {first_line[:300]}"}

            parsed = []
            for item in result.content:
                raw = item.text if hasattr(item, "text") else ""
                try:
                    parsed.append(json.loads(raw))
                except (json.JSONDecodeError, TypeError):
                    parsed.append(raw)

            return parsed[0] if len(parsed) == 1 else parsed
        except Exception as e:
            log.error(f"[MCP-CLIENT] Tool '{tool_name}' failed: {e}")
            return {"error": f"Tool call failed: {e}"}

    async def stop(self) -> None:
        log.info("[MCP-CLIENT] Stopping MCP server...")
        await self.exit_stack.aclose()
        self.session = None
        self._tools = []


mcp_manager = MCPManager()
