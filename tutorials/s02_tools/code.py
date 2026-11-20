"""将同一份工具声明交给模型和实际执行入口。"""

import asyncio
from argparse import ArgumentParser

from agentscope.tool import Toolkit

from biu_agent.tools import tool
from examples.tickets.corpus import Passage, excerpt


async def read_material(id, section):
    return excerpt(id, section, {"receiving"})


async def main(section, extra):
    recorded = []
    reader = tool(read_material, Passage, "按资料 ID 与段号读取原文", on_result=recorded.append)
    schemas = await Toolkit(tools=[reader]).get_tool_schemas()
    schema = next(item["function"] for item in schemas if item["function"]["name"] == reader.name)
    print("工具", schema["name"], "必填参数", schema["parameters"]["required"])
    arguments = {"id": "receiving", "section": section}
    if extra:
        arguments["all"] = True
    chunk = await reader.call(**arguments)
    print("工具回传", chunk.content[0].text)
    print("登记的出处", [row["source"] for row in recorded])


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--section", default="R2")
    parser.add_argument("--extra", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.section, args.extra))
