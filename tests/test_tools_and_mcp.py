"""Tools against the fixture store, the GitHub client against mocked HTTP, and the MCP contract:
every tool called over MCP returns exactly what the direct call returns."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from triagebench.github.client import GitHubClient
from triagebench.github.collect import norm_job, parse_run_url
from triagebench.tools.impl import TOOL_SPECS, Toolbox, ToolError


def test_overview_and_excerpt(box: Toolbox) -> None:
    ov = box.get_run_overview("101")
    assert "acme/widgets" in ov and "job_id=101" in ov and "PR #42" in ov
    ex = box.get_log_excerpt(101)
    assert "AssertionError" in ex and ex.startswith("[excerpt strategy=hybrid")


def test_search_read_changes_history(box: Toolbox) -> None:
    assert "1 matching lines" in box.search_log(101, r"test_parse_dates FAILED")
    assert "no lines match" in box.search_log(101, "segfault")
    assert "[lines 1-3 of" in box.read_log_lines(101, 1, 3)
    assert "src/dates.py" in box.get_change_summary(101)
    assert "1 success" in box.get_run_history(101)


def test_tool_errors_are_helpful(box: Toolbox) -> None:
    with pytest.raises(ToolError, match="get_run_overview"):
        box.get_log_excerpt(999)
    with pytest.raises(ToolError, match="retrieval isn't enabled"):
        box.search_similar_failures(101, "x")
    with pytest.raises(ToolError, match="live fetching is disabled"):
        box.resolve("https://github.com/acme/other/actions/runs/5")


def test_parse_run_url_and_job_names() -> None:
    assert parse_run_url("https://github.com/o/r/actions/runs/12/job/34") == ("o/r", 12, 34)
    assert parse_run_url("https://github.com/o/r/actions/runs/12") == ("o/r", 12, None)
    assert norm_job("test (3.12, ubuntu-latest)") == "test"


@respx.mock
def test_github_client_backs_off_on_rate_limit_and_uses_etags(tmp_path: Path) -> None:
    route = respx.get("https://api.github.com/repos/o/r").mock(
        side_effect=[
            httpx.Response(403, headers={"x-ratelimit-remaining": "0", "retry-after": "0"}),
            httpx.Response(200, json={"name": "r"}, headers={"etag": '"v1"'}),
            httpx.Response(304),
        ]
    )
    gh = GitHubClient("t", cache_dir=tmp_path)
    assert gh.repo_info("o/r") == {"name": "r"}
    assert gh.repo_info("o/r") == {"name": "r"}  # 304 -> served from the disk cache
    assert route.calls[-1].request.headers["If-None-Match"] == '"v1"'


@pytest.mark.anyio
async def test_mcp_contract(box: Toolbox, monkeypatch: pytest.MonkeyPatch) -> None:
    from mcp import Client

    import triagebench.mcp_server as ms

    monkeypatch.setattr(ms, "box", lambda: box)
    calls = {
        "get_run_overview": {"target": "101"},
        "get_log_excerpt": {"job_id": 101},
        "search_log": {"job_id": 101, "pattern": "FAILED"},
        "read_log_lines": {"job_id": 101, "start": 1, "end": 5},
        "get_change_summary": {"job_id": 101},
        "get_run_history": {"job_id": 101},
    }
    async with Client(ms.server) as client:
        listed = await client.list_tools()
        names = {t.name for t in listed.tools}
        assert names == {t["name"] for t in TOOL_SPECS}
        for t in listed.tools:
            assert t.description  # descriptions are part of the prompt
        for name, args in calls.items():
            res = await client.call_tool(name, args)
            assert not res.is_error, name
            text = "".join(getattr(c, "text", "") for c in res.content)
            assert text == box.call(name, args), name
        bad = await client.call_tool("get_log_excerpt", {"job_id": 999})
        assert bad.is_error
