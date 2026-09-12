import asyncio
import importlib.util
import sys
from pathlib import Path
import pytest
from PIL import Image

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("mcp") is None, reason="optional mcp extra"
)


def test_stdio_handshake_discovery_success_and_refusal(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    image = tmp_path / "a.png"
    Image.new("RGB", (20, 20), "red").save(image)

    async def check():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[str(Path(__file__).parents[1] / "scripts/pil_mcp.py")],
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert any(tool.name == "pil_mask" for tool in tools.tools)
                result = await session.call_tool(
                    "pil_image_info", {"argv": [str(image)]}
                )
                assert not result.isError
                assert result.structuredContent["ok"]
                refused = await session.call_tool("pil_mask", {"argv": []})
                assert refused.isError
                assert refused.structuredContent["exit_code"] == 2

    async def bounded():
        await asyncio.wait_for(check(), timeout=45)

    asyncio.run(bounded())
