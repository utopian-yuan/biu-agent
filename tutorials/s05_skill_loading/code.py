"""观察 SDK 先暴露技能目录、再按名称返回正文。"""

import asyncio
import json
from argparse import ArgumentParser
from importlib.resources import files

from agentscope.message import ToolCallBlock
from agentscope.skill import LocalSkillLoader
from agentscope.state import AgentState
from agentscope.tool import Toolkit, ToolResponse


async def main(name):
    folder = files("biu_agent.agents").joinpath("skills", "source-review")
    toolkit = Toolkit(
        skills_or_loaders=[LocalSkillLoader(str(folder))],
        skill_instruction_template="{% for s in skills %}{{ s.name }}：{{ s.description }}{% endfor %}",
    )
    print("初始技能目录", await toolkit.get_skill_instructions())
    call = ToolCallBlock(id="skill-1", name="Skill", input=json.dumps({"skill": name}))
    async for response in toolkit.call_tool(call, AgentState()):
        if isinstance(response, ToolResponse):
            print("取回正文", response.state.value, response.content[0].text)


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--name", default="source-review")
    asyncio.run(main(parser.parse_args().name))
