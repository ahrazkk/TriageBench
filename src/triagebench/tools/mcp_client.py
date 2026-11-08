"""Let the agent use its tools *through MCP* instead of calling Python functions directly.

`McpToolbox` has the same `call(name, args)` surface as `Toolbox`, but every call goes over the
MCP protocol to `triagebench mcp` running as a stdio subprocess, exactly like Claude Code would
use it. The MCP client is async; the agent loop is sync, so the client lives on its own event
loop in a background thread and calls are handed to it with run_coroutine_threadsafe.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future
from typing import Any

from mcp import Client, StdioServerParameters

from triagebench.data import CaseStore
from triagebench.tools.impl import ToolError


class McpToolbox:
    def __init__(
        self, store: CaseStore, command: str = "triagebench", args: list[str] | None = None
    ):
        self.store = store  # still used locally to verify evidence quotes against the log
        self.calls: list[str] = []
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._params = StdioServerParameters(command=command, args=args or ["mcp"])
        self._client: Client | None = None
        self._ready: Future[None] = asyncio.run_coroutine_threadsafe(self._open(), self._loop)
        self._ready.result(timeout=60)
        self.tool_names = self._run(self._list())

    async def _open(self) -> None:
        self._client = Client(self._params)
        await self._client.__aenter__()

    async def _list(self) -> list[str]:
        assert self._client is not None
        return [t.name for t in (await self._client.list_tools()).tools]

    async def _call(self, name: str, args: dict[str, Any]) -> tuple[str, bool]:
        assert self._client is not None
        res = await self._client.call_tool(name, args)
        text = "".join(getattr(c, "text", "") for c in res.content)
        return text, bool(res.is_error)

    def _run(self, coro: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=120)

    def call(self, name: str, args: dict[str, Any]) -> str:
        if name not in self.tool_names:
            raise ToolError(f"unknown tool {name!r}; available: {', '.join(self.tool_names)}")
        self.calls.append(name)
        text, is_error = self._run(self._call(name, args))
        text = str(text)
        if is_error:
            raise ToolError(text)
        return text

    def close(self) -> None:
        if self._client is not None:
            self._run(self._client.__aexit__(None, None, None))
        self._loop.call_soon_threadsafe(self._loop.stop)
