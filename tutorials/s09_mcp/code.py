"""用工作台的发现函数连接临时启动的 Streamable HTTP 服务。"""

import asyncio
from argparse import ArgumentParser

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from biu_agent.agents.mcp import discover
from tests.material_server import serve_local

server = FastMCP("展具目录练习", stateless_http=True, log_level="WARNING")


@server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
def edition() -> str:
    """读取本期材料目录版次。"""
    return "春季目录第三版"


@server.tool(annotations=ToolAnnotations(readOnlyHint=False))
def submit() -> str:
    """模拟提交目录。"""
    return "练习用回执"


async def inspect(url, selected):
    client, names = await discover({"id": "lesson", "url": url}, token=None)
    print("发现的只读工具", names)
    bound = [name for name in names if name == selected]
    print("绑定到本轮", bound)
    for name in bound:
        remote = await client.get_tool(name)
        result = await remote.call()
        print(remote.name, result.content[0].text)


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--select", choices=["edition", "submit"], default="edition")
    args = parser.parse_args()
    with serve_local(server.streamable_http_app()) as base:
        asyncio.run(inspect(base + "/mcp", args.select))
