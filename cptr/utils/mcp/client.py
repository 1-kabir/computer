"""MCP (Model Context Protocol) client for Streamable HTTP transport.

Wraps the `mcp` library's ClientSession to provide a simple interface
for connecting to MCP servers, listing tools, and calling tools.

Usage::

    client = MCPClient()
    await client.connect("https://mcp.example.com/sse", headers={"Authorization": "Bearer ..."})
    specs = await client.list_tool_specs()
    result = await client.call_tool("my_tool", {"arg": "value"})
    await client.disconnect()
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import logging
from contextlib import AsyncExitStack

import anyio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.types import CallToolResult

logger = logging.getLogger(__name__)

_INIT_TIMEOUT = 30  # seconds


class MCPClient:
    """Manages a single MCP server connection over Streamable HTTP or stdio."""

    def __init__(self):
        self.session: ClientSession | None = None
        self._exit_stack: AsyncExitStack | None = None

    async def connect(self, url: str, headers: dict | None = None) -> None:
        """Connect to an MCP server over Streamable HTTP.

        Args:
            url: The server's Streamable HTTP endpoint URL.
            headers: Optional HTTP headers (e.g. Authorization).
        """
        async with AsyncExitStack() as exit_stack:
            try:
                transport = await exit_stack.enter_async_context(
                    streamablehttp_client(url=url, headers=headers)
                )
                read, write, _ = transport

                self.session = await exit_stack.enter_async_context(
                    ClientSession(read_stream=read, write_stream=write)
                )

                with anyio.fail_after(_INIT_TIMEOUT):
                    await self.session.initialize()

                # Transfer ownership — prevent exit_stack.__aexit__ from
                # tearing everything down when we leave this block.
                self._exit_stack = exit_stack.pop_all()
                logger.info("[mcp] Connected to %s", url)
            except Exception:
                await asyncio.shield(self.disconnect())
                raise

    async def connect_stdio(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> None:
        """Connect to an MCP server by spawning a local process.

        Args:
            command: The executable to run (e.g. 'npx', 'python3').
            args: Command line arguments to pass.
            env: Optional environment variables for the process.
            cwd: Optional working directory.
        """
        command = command.strip()
        if len(command) > 1 and command[0] == command[-1] and command[0] in ("'", '"'):
            command = command[1:-1]
        params = StdioServerParameters(
            command=command,
            args=args or [],
            env=env,
            cwd=cwd,
        )
        async with AsyncExitStack() as exit_stack:
            try:
                transport = await exit_stack.enter_async_context(
                    stdio_client(params)
                )
                read, write = transport

                self.session = await exit_stack.enter_async_context(
                    ClientSession(read_stream=read, write_stream=write)
                )

                with anyio.fail_after(_INIT_TIMEOUT):
                    await self.session.initialize()

                self._exit_stack = exit_stack.pop_all()
                logger.info("[mcp] Connected to stdio process: %s %s", command, " ".join(args or []))
            except Exception:
                await asyncio.shield(self.disconnect())
                raise

    async def list_tool_specs(self) -> list[dict]:
        """List tools from the connected server as OpenAI-compatible schemas.

        Returns a list of dicts, each with: name, description, parameters.
        """
        if not self.session:
            raise RuntimeError("MCPClient is not connected")

        result = await self.session.list_tools()
        specs = []

        for tool in result.tools:
            if tool.inputSchema is not None:
                parameters = deepcopy(tool.inputSchema)
                if not isinstance(parameters, dict):
                    parameters = dict(parameters)
                parameters.setdefault("type", "object")
                parameters.setdefault("properties", {})
            else:
                parameters = {
                    "type": "object",
                    "properties": {},
                    "required": [],
                }

            specs.append(
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": parameters,
                }
            )

        return specs

    async def call_tool(self, name: str, function_args: dict | None = None) -> list:
        """Call a tool on the connected server.

        Args:
            name: The tool name.
            function_args: Arguments to pass to the tool.

        Returns:
            A list of content items from the tool result.

        Raises:
            RuntimeError: If the tool returns an error result.
        """
        if not self.session:
            raise RuntimeError("MCPClient is not connected")

        result: CallToolResult = await self.session.call_tool(
            name, arguments=function_args or {}
        )

        content = [item.model_dump() for item in result.content]

        if result.isError:
            raise RuntimeError(f"MCP tool error: {content}")

        return content

    async def disconnect(self) -> None:
        """Disconnect from the MCP server. Idempotent.

        IMPORTANT: Do NOT use asyncio.shield() or anyio.CancelScope here.
        The MCP SDK requires its TaskGroup to be exited in the same task
        that created it. Simply call aclose() directly.
        """
        exit_stack = self._exit_stack
        if exit_stack is None:
            return

        # Prevent double-close from concurrent callers
        self._exit_stack = None
        self.session = None

        try:
            await exit_stack.aclose()
        except TimeoutError:
            logger.warning("[mcp] disconnect timed out")
        except RuntimeError as exc:
            logger.debug("[mcp] disconnect suppressed RuntimeError: %s", exc)
        except Exception:
            logger.debug("[mcp] Error during disconnect", exc_info=True)
