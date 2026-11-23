"""用真实 Toolkit 对照技能目录、正文、智能体绑定和资料回执。"""

import asyncio
import json
from argparse import ArgumentParser
from importlib.resources import files

from agentscope.message import ToolCallBlock
from agentscope.skill import LocalSkillLoader
from agentscope.state import AgentState
from agentscope.tool import Toolkit, ToolResponse

from biu_agent.agents.skills import bind, unpack
from biu_agent.tools import tool
from examples.tickets.corpus import Passage, excerpt


def load_skills(folder, binding):
    if binding == "file":
        return [LocalSkillLoader(str(folder))]
    slug, row = unpack(folder.joinpath("SKILL.md").read_text(encoding="utf-8"))
    row.update(revision=1, enabled=binding != "disabled")
    selected = [] if binding == "unbound" else [slug]
    return bind({slug: row}, selected)


async def invoke(toolkit, state, call_id, name, args):
    call = ToolCallBlock(id=call_id, name=name, input=json.dumps(args, ensure_ascii=False))
    async for event in toolkit.call_tool(call, state):
        if isinstance(event, ToolResponse):
            return event
    raise RuntimeError("工具流缺少终态回执")


async def main(name, binding, brief, verify):
    folder = files("biu_agent.agents").joinpath("skills", "source-review")
    evidence = []
    grants = {"receiving"} if verify == "granted" else set()

    async def read_material(id, section):
        return excerpt(id, section, grants)

    def record(row):
        evidence.append(row["source"])

    template = (
        "{% for s in skills %}{{ s.name }}{% endfor %}"
        if brief
        else ("{% for s in skills %}{{ s.name }}：{{ s.description }}{% endfor %}")
    )
    toolkit = Toolkit(
        skills_or_loaders=load_skills(folder, binding),
        skill_instruction_template=template,
        tools=[tool(read_material, Passage, "按当前账号读取原文。", on_result=record)] if verify else [],
    )
    state = AgentState()
    catalog = await toolkit.get_skill_instructions()
    schemas = await toolkit.get_tool_schemas()
    print("初始技能目录", catalog or "（空）")
    print("本轮工具", [schema["function"]["name"] for schema in schemas])
    if catalog:
        result = await invoke(toolkit, state, "skill-1", "Skill", {"skill": name})
        print("取回正文", result.state.value, result.content[0].text)
    print("读说明后的出处", evidence)
    if verify:
        result = await invoke(toolkit, state, "read-1", "read_material", {"id": "receiving", "section": "R2"})
        print("资料回执", result.state.value, result.content[0].text)
        print("读取资料后的出处", evidence)


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--name", default="source-review")
    parser.add_argument("--binding", choices=["file", "enabled", "disabled", "unbound"], default="file")
    parser.add_argument("--brief", action="store_true", help="目录仅放名称")
    parser.add_argument("--verify", choices=["granted", "revoked"], help="另行执行一次资料查询")
    args = parser.parse_args()
    asyncio.run(main(args.name, args.binding, args.brief, args.verify))
