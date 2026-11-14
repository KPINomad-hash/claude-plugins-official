"""Each server starts over stdio, lists its tools and answers without the program running."""

import json
import os
import sys
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SRC = str(Path(__file__).parent.parent / "src")
COMMON = {"get_capabilities", "connect", "get_model_info", "list_elements"}


@pytest.mark.parametrize("program", ["sap2000", "staad", "lira", "sapfir"])
def test_server_over_stdio(program, tmp_path):
    async def go():
        env = {**os.environ, "PYTHONPATH": SRC, "STRUCT_MCP_WORKDIR": str(tmp_path)}
        params = StdioServerParameters(command=sys.executable, args=["-m", "structural_mcp.cli", program], env=env)
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name: t for t in (await session.list_tools()).tools}
            assert {f"{program}_{n}" for n in COMMON} <= set(tools)
            assert all(t.description and t.annotations is not None for t in tools.values())
            caps = await session.call_tool(f"{program}_get_capabilities", {})
            assert not caps.is_error
            data = json.loads(caps.content[0].text)
            assert data["connected"] is False and data["access_mode"] == "read-only"
            assert data["operations"]["modify_model"] is False
            if program == "sapfir":  # its model info also reports the (absent) connection instead of failing
                failed = await session.call_tool("sapfir_list_elements", {"element_type": "column"})
            else:
                failed = await session.call_tool(f"{program}_get_model_info", {})
            assert failed.is_error and "Hint:" in failed.content[0].text
            return sorted(tools)

    names = anyio.run(go)
    print(program, names)
