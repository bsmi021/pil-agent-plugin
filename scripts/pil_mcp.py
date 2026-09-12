"""Optional stdio MCP adapter over the same public CLI dispatcher."""

import argparse
import asyncio
import json
import sys
from pil_capabilities import catalog, invoke

TOOL_VERSION = "0.9.1"


async def serve():
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, ToolAnnotations, CallToolResult, TextContent

    server = Server("pil-agent-plugin", version=TOOL_VERSION)
    inventory = catalog()

    @server.list_tools()
    async def list_tools():
        return [
            Tool(
                name=t["name"],
                description=t["description"]
                + "\nCLI contract: "
                + json.dumps(t["cli_schema"]),
                inputSchema=t["inputSchema"],
                outputSchema=t["outputSchema"],
                annotations=ToolAnnotations(
                    readOnlyHint=not t["mutates"],
                    destructiveHint=t["mutates"],
                    openWorldHint=t["name"] == "pil_bootstrap",
                ),
            )
            for t in inventory["tools"]
        ]

    @server.call_tool()
    async def call_tool(name, arguments):
        result = await asyncio.to_thread(
            invoke, name, arguments.get("argv"), arguments.get("timeout", 300)
        )
        return CallToolResult(
            content=[
                TextContent(type="text", text=json.dumps(result, allow_nan=False))
            ],
            structuredContent=result,
            isError=not result["ok"],
        )

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    try:
        asyncio.run(serve())
        return 0
    except ImportError as exc:
        print(f"pil_mcp: install the mcp extra: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
