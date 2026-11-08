"""Start the MCP server as a real stdio subprocess (as Claude Code does), list its tools, call a few
on a real case, and time each call vs the direct function call (the MCP overhead number for M6).

    bash scripts/dev.sh python scripts/mcp_smoke.py
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time

from mcp import Client, StdioServerParameters

from triagebench.data import load_cases
from triagebench.tools.impl import Toolbox


async def main() -> None:
    case = next(c for c in load_cases() if c.split == "dev")
    calls = [
        ("get_run_overview", {"target": str(case.job_id)}),
        ("get_log_excerpt", {"job_id": case.job_id}),
        ("search_log", {"job_id": case.job_id, "pattern": "error"}),
        ("get_run_history", {"job_id": case.job_id}),
    ]
    box = Toolbox.default(with_retrieval=False, allow_live=False)
    params = StdioServerParameters(command="triagebench", args=["mcp"])
    t0 = time.perf_counter()
    async with Client(params) as client:
        startup = time.perf_counter() - t0
        tools = await client.list_tools()
        print(f"server up in {startup:.2f}s; tools: {[t.name for t in tools.tools]}")
        mcp_ms, direct_ms = [], []
        for _ in range(5):
            for name, args in calls:
                t = time.perf_counter()
                res = await client.call_tool(name, args)
                mcp_ms.append((time.perf_counter() - t) * 1000)
                t = time.perf_counter()
                direct = box.call(name, dict(args))
                direct_ms.append((time.perf_counter() - t) * 1000)
                text = "".join(getattr(c, "text", "") for c in res.content)
                assert not res.is_error and text == direct, name
    out = {
        "case": case.id,
        "calls": len(mcp_ms),
        "mcp_ms_p50": round(statistics.median(mcp_ms), 2),
        "direct_ms_p50": round(statistics.median(direct_ms), 2),
        "server_startup_s": round(startup, 2),
    }
    print(json.dumps(out))


if __name__ == "__main__":
    asyncio.run(main())
