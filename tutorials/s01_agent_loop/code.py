"""从一次读取到多工具反馈，再用 AgentScope 运行同一道材料核对题。"""

import asyncio
from argparse import ArgumentParser
from contextlib import aclosing
from dataclasses import dataclass

from agentscope.agent import Agent, ContextConfig, InjectionConfig, ReActConfig
from agentscope.event import ReplyEndEvent
from agentscope.message import AssistantMsg, Msg, SystemMsg, ToolCallBlock, ToolResultBlock, UserMsg
from agentscope.state import AgentState
from agentscope.tool import Toolkit, ToolResponse

from biu_agent.tools import Inputs, tool
from examples.tickets.corpus import Passage, catalog, excerpt
from examples.tickets.teaching_model import TeachingModel


@dataclass
class Call:
    id: str
    name: str
    args: dict


@dataclass
class Result:
    call_id: str
    value: str


class StepLimit(Exception):
    pass


def run_loop(question, decide, dispatch, *, steps=3, observe=print):
    messages = [question]
    for _ in range(steps):
        action = decide(messages)
        messages.append(action)
        observe(action)
        if isinstance(action, str):
            return action
        result = Result(action.id, dispatch(action))
        messages.append(result)
        observe(result)
    raise StepLimit(f"{steps} 次模型决策已用完")


def decide_receipt(messages):
    last = messages[-1]
    if isinstance(last, Result):
        return last.value
    return Call("read-1", "read_material", {"id": "receiving", "section": "R2"})


def read_receipt(call):
    if call.name != "read_material":
        raise ValueError("未登记的工具")
    row = excerpt(call.args["id"], call.args["section"], {"receiving"})
    return f"{row['text']} [{row['source']}]"


QUESTION = "样品灯和导向牌是否签收？验收说明覆盖哪些物品？请引用原文。"
INSTRUCTION = "先查授权目录，再按段读取。区分签收和验收，每项材料事实附原文出处。"


def review_toolkit(allowed):
    async def list_materials():
        return [{"id": row["id"], "sections": row["sections"]} for row in catalog(allowed)]

    async def read_material(id, section):
        return excerpt(id, section, allowed)

    return Toolkit(
        tools=[
            tool(list_materials, Inputs, "查看当前可读资料的 ID 与段号。"),
            tool(read_material, Passage, "已知 ID 与段号时，读取原文和版本。"),
        ]
    )


class ObservedModel(TeachingModel):
    """在离线模型入口观察它实际收到的回执，保留原有选工具逻辑。"""

    def __init__(self, trace):
        super().__init__()
        self.calls = 0
        self.trace = trace

    async def _call_api(self, model_name, messages, tools=None, **kwargs):
        self.calls += 1
        receipts = [b for msg in messages for b in msg.content if isinstance(b, ToolResultBlock)]
        print(f"模型调用 {self.calls}，输入中有 {len(receipts)} 份工具回执")
        if self.trace:
            for receipt in receipts:
                print("  收到", receipt.id, receipt.name, receipt.state)
        response = await super()._call_api(model_name, messages, tools=tools, **kwargs)
        if self.trace:
            for block in response.content:
                if isinstance(block, ToolCallBlock):
                    print("  请求", block.id, block.name, block.input)
        return response


async def run_manual(model, toolkit, question, steps):
    messages = [SystemMsg("system", INSTRUCTION), UserMsg("user", question)]
    schemas = await toolkit.get_tool_schemas()
    state = AgentState()
    for _ in range(steps):
        response = await model(messages, tools=schemas)
        if not response.is_last or response.finished_reason != "completed":
            return "incomplete_response", None
        reply = AssistantMsg("biu", response.content)
        messages.append(reply)
        calls = [b for b in reply.content if isinstance(b, ToolCallBlock)]
        if not calls:
            text = reply.get_text_content()
            return ("completed", text) if text and text.strip() else ("empty_answer", None)

        request_ids = [call.id for call in calls]
        if len(set(request_ids)) != len(request_ids):
            return "duplicate_call_id", None
        results = []
        for call in calls:
            async for event in toolkit.call_tool(call, state):
                if isinstance(event, ToolResponse):
                    results.append(
                        ToolResultBlock(
                            id=event.id,
                            name=call.name,
                            output=event.content,
                            state=event.state,
                        )
                    )
        if [result.id for result in results] != request_ids:
            return "incomplete_tool_batch", None
        messages.append(AssistantMsg("biu", results))
    return "budget_exhausted", None


async def run_framework(model, toolkit, question, steps):
    agent = Agent(
        "biu",
        INSTRUCTION,
        model,
        toolkit=toolkit,
        react_config=ReActConfig(max_iters=steps),
        context_config=ContextConfig(compression_tool_enabled=False),
        injection_config=InjectionConfig(inject_runtime_state=False),
    )
    reason, text = "incomplete_response", None
    async with aclosing(agent.reply_stream(UserMsg("user", question), yield_final_msg=True)) as stream:
        async for event in stream:
            if isinstance(event, ReplyEndEvent):
                reason = event.finished_reason
            elif isinstance(event, Msg):
                text = event.get_text_content()
    if reason == "completed" and not (text and text.strip()):
        return "empty_answer", None
    return reason, text if reason == "completed" else None


async def review(engine, scope, steps, trace):
    grants = {"full": {"receiving", "acceptance"}, "receiving": {"receiving"}, "empty": set()}
    toolkit = review_toolkit(grants[scope])
    model = ObservedModel(trace)
    run = run_manual if engine == "manual" else run_framework
    reason, answer = await run(model, toolkit, QUESTION, steps)
    print(f"结束原因 {reason}，模型调用 {model.calls} 次")
    if answer:
        print("候选答复", answer, sep="\n")


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--steps", type=int, default=3)
    parser.add_argument("--engine", choices=["minimal", "manual", "agentscope"], default="minimal")
    parser.add_argument("--scope", choices=["full", "receiving", "empty"], default="full")
    parser.add_argument("--trace", action="store_true", help="打印核对题每次请求与回执的调用 ID")
    args = parser.parse_args()
    if args.engine != "minimal" and args.steps < 1:
        parser.error("--steps 至少为 1")
    if args.engine == "minimal":
        if args.scope != "full" or args.trace:
            parser.error("--scope 与 --trace 用于 manual 或 agentscope 核对题")
        try:
            run_loop("导向牌签收了吗？", decide_receipt, read_receipt, steps=args.steps)
        except StepLimit as error:
            print(error)
    else:
        asyncio.run(review(args.engine, args.scope, args.steps, args.trace))
