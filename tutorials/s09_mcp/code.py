"""用回环上的 MCP 服务检查发现、绑定、执行业务授权和实际调用次数。"""

import asyncio
from argparse import ArgumentParser

from agentscope.message import ToolCallBlock
from agentscope.state import AgentState
from agentscope.tool import Toolkit, ToolResponse
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from biu_agent.agents.mcp import discover
from tests.material_server import serve_local


def make_server(annotations, allow_edition, journal):
    server = FastMCP("展具目录练习", stateless_http=True, log_level="WARNING")
    hint = (
        None
        if annotations == "missing"
        else ToolAnnotations(readOnlyHint=True, destructiveHint=annotations == "conflict")
    )

    @server.tool(annotations=hint)
    def edition() -> str:
        """读取本期材料目录版次。"""
        journal.append("edition")
        if not allow_edition:
            raise ValueError("当前演示身份无权读取目录版次")
        return "春季目录第三版"

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False))
    def submit() -> str:
        """模拟提交目录。"""
        journal.append("submit")
        return "练习用回执"

    return server


async def inspect(url, selected):
    client, names = await discover({"id": "lesson", "url": url}, token=None)
    # 为教学另列一次服务端清单，可与宿主筛选后的名字同时观察。
    descriptions = await client.list_raw_tools()
    print("服务端声明", [entry.name for entry in descriptions])
    print("发现的只读工具", names)
    bound = [name for name in names if name == selected]
    print("绑定到本轮", bound)
    for name in bound:
        remote = await client.get_tool(name)
        toolkit = Toolkit(tools=[remote])
        call = ToolCallBlock(id="lookup-1", name=remote.name, input="{}")
        async for result in toolkit.call_tool(call, AgentState()):
            if isinstance(result, ToolResponse):
                print("远端回执", result.id, remote.name, result.state.value, result.content[0].text)


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--select", choices=["edition", "submit"], default="edition")
    parser.add_argument("--annotations", choices=["readonly", "conflict", "missing"], default="readonly")
    parser.add_argument("--remote-deny", action="store_true", help="让服务在执行时拒绝资料读取")
    args = parser.parse_args()
    journal = []
    server = make_server(args.annotations, not args.remote_deny, journal)
    with serve_local(server.streamable_http_app()) as base:
        asyncio.run(inspect(base + "/mcp", args.select))
    print("服务端实际进入", len(journal), "次", journal)
