"""把一次项目备注放到实际的前置检查、业务写入、后置审计和回执恢复中观察。"""

import asyncio
import sqlite3
from argparse import ArgumentParser
from contextlib import aclosing, closing

from agentscope.agent import Agent, ContextConfig, InjectionConfig
from agentscope.event import ConfirmResult, RequireUserConfirmEvent, UserConfirmResultEvent
from agentscope.message import TextBlock, ToolCallState, ToolResultBlock, UserMsg
from agentscope.middleware import MiddlewareBase
from agentscope.model import ChatResponse
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import FunctionTool, Toolkit, ToolResponse

from biu_agent.identity import Problem
from examples.tickets.teaching_model import TeachingModel
from tutorials.s01_agent_loop.code import decide_receipt, read_receipt, run_loop
from tutorials.s03_permissions.code import NOTE_TEXT, OWNER, Note, consume_approval, decision, request_note


class BudgetExceeded(Exception):
    pass


def with_hooks(execute, before, after):
    def dispatch(call):
        before(call)
        try:
            result = execute(call)
        except Exception:
            after(call, "error")
            raise
        after(call, "end")
        return result

    return dispatch


def main(budget, section):
    def before(call):
        nonlocal budget
        if budget <= 0:
            raise BudgetExceeded("预算耗尽，工具执行次数为 0")
        budget -= 1
        print(call.id, "start", call.name)

    def after(call, phase):
        print(call.id, phase, call.name)

    def decide(messages):
        action = decide_receipt(messages)
        if not isinstance(action, str):
            action.args["section"] = section
        return action

    dispatch = with_hooks(read_receipt, before, after)
    return run_loop("核对签收", decide, dispatch)


def open_ledger(db):
    db.execute("CREATE TABLE notes(operation_id TEXT PRIMARY KEY, project TEXT NOT NULL, body TEXT NOT NULL)")
    db.execute("CREATE TABLE audit(operation_id TEXT PRIMARY KEY)")


def append_once(db, operation_id, note):
    with db:
        db.execute(
            "INSERT INTO notes VALUES(?,?,?) ON CONFLICT(operation_id) DO NOTHING",
            (operation_id, note.project, note.text),
        )
        row = db.execute("SELECT project,body FROM notes WHERE operation_id=?", (operation_id,)).fetchone()
        if row != (note.project, note.text):
            raise Problem(409, "operation_conflict", "同一操作编号已经用于不同的备注。")
    return row


def record_audit(db, operation_id):
    with db:
        db.execute("INSERT INTO audit VALUES(?) ON CONFLICT(operation_id) DO NOTHING", (operation_id,))


def deliver_note(db, key, note, fault):
    if fault == "before":
        raise Problem(409, "precheck_failed", "前置检查失败，执行器未启动")
    receipt = append_once(db, key, note)
    if fault == "timeout":
        raise TimeoutError("调用方未收到响应，结果待确认")
    return receipt


def fault_demo(db, fault):
    # 从一份已通过宿主审核的固定样本开始，只改变故障发生的位置。
    note = Note(project="P-101", text=NOTE_TEXT)
    key = "note-1"
    try:
        deliver_note(db, key, note, fault)
    except Problem as error:
        print(error.message)
        return
    except TimeoutError as error:
        print(error)
        row = db.execute("SELECT project,body FROM notes WHERE operation_id=?", (key,)).fetchone()
        print("原操作回执", row)
        if row is None:
            return
    try:
        if fault == "after":
            raise ConnectionError("模拟审计接收端不可用")
        record_audit(db, key)
    except ConnectionError:
        print("已写入，审计待补", db.execute("SELECT count(*) FROM audit").fetchone()[0])
        record_audit(db, key)
    print("审计回读", db.execute("SELECT operation_id FROM audit").fetchall())


class NoteModel(TeachingModel):
    async def _call_api(self, model_name, messages, tools=None, **kwargs):
        receipts = [b for msg in messages for b in msg.content if isinstance(b, ToolResultBlock)]
        if receipts:
            block = TextBlock(text="本次调用的工具状态为 " + receipts[-1].state)
        else:
            block = self.call("append_note", project="P-101", text="  " + NOTE_TEXT)
        return ChatResponse(content=[block], is_last=True)


class ReviewGate(MiddlewareBase):
    """这个实验每轮只有一份待办，宿主把它绑定到实际等待的调用。"""

    def __init__(self):
        self.inbox = {}
        self.item = None
        self.grants = {"P-101"}
        self.approved_ids = set()

    async def on_check_permission(self, agent, input_kwargs, next_handler):
        framework = await next_handler(**input_kwargs)
        call = input_kwargs["tool_call"]
        project = input_kwargs["tool_input"]["project"]
        local = decision(OWNER, self.grants, project, call.name)
        print("权限检查", framework.behavior.value, local)
        if local == "DENY":
            return PermissionDecision(behavior=PermissionBehavior.DENY, message="项目权限已收回。")
        if (
            local == "ASK"
            and framework.behavior == PermissionBehavior.ALLOW
            and call.state != ToolCallState.ALLOWED
        ):
            return PermissionDecision(behavior=PermissionBehavior.ASK, message="项目要求核对本次备注。")
        return framework

    async def on_acting(self, agent, input_kwargs, next_handler):
        call = input_kwargs["tool_call"]
        try:
            if self.item is None:
                raise Problem(409, "no_review", "宿主没有这份待办。")
            consume_approval(
                self.inbox,
                self.item.operation_id,
                call.id,
                OWNER,
                self.grants,
                Note.model_validate_json(call.input),
                now=120,
                policy_version=1,
                approved=self.item.operation_id in self.approved_ids,
            )
        except Problem as error:
            print("执行入口拒绝", error.code)
            yield ToolResponse(id=call.id, state="denied", content=[TextBlock(text=error.message)])
            return
        async with aclosing(next_handler(**input_kwargs)) as events:
            async for event in events:
                yield event


async def sdk_demo(db, case):
    gate = ReviewGate()

    async def append_note(project, text):
        row = append_once(db, gate.item.operation_id, Note(project=project, text=text))
        return "台账已写入 " + row[0]

    tool_policy = {
        "project-ask": PermissionBehavior.ALLOW,
        "framework-deny": PermissionBehavior.DENY,
    }.get(case, PermissionBehavior.ASK)
    writer = FunctionTool(
        append_note,
        name="append_note",
        description="向已批准的项目追加一条备注。",
        input_schema=Note,
        is_concurrency_safe=False,
        permission=PermissionDecision(behavior=tool_policy, message="本工具的调用规则。"),
    )
    agent = Agent(
        "biu",
        "只申请一条展示给用户的项目备注。",
        NoteModel(),
        toolkit=Toolkit(tools=[writer]),
        middlewares=[gate],
        context_config=ContextConfig(compression_tool_enabled=False),
        injection_config=InjectionConfig(inject_runtime_state=False),
    )
    waiting = None
    async with aclosing(agent.reply_stream(UserMsg("user", "给 P-101 追加设备跟进备注。"))) as stream:
        async for event in stream:
            if isinstance(event, RequireUserConfirmEvent):
                waiting = event
    if case == "framework-deny" and waiting is None:
        print("框架已拒绝，本轮没有待办")
        return
    if waiting is None or len(waiting.tool_calls) != 1:
        raise RuntimeError("没有收到预期的单项确认事件")
    call = waiting.tool_calls[0]
    gate.item = request_note(
        gate.inbox,
        OWNER,
        gate.grants,
        call.id,
        Note.model_validate_json(call.input).model_dump(),
        now=0,
        policy_version=1,
    )
    print("待审原文", gate.item.note.project, gate.item.note.text)
    print("等待时台账", db.execute("SELECT count(*) FROM notes").fetchone()[0], "条")
    if case != "unrecorded":
        gate.approved_ids.add(gate.item.operation_id)
    if case == "revoked":
        gate.grants.clear()
    confirmation = ConfirmResult(tool_call=call, confirmed=True, rules=None)
    resume = UserConfirmResultEvent(reply_id=waiting.reply_id, confirm_results=[confirmation])
    async with aclosing(agent.reply_stream(resume)) as stream:
        async for _ in stream:
            pass


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--budget", type=int, default=1)
    parser.add_argument("--section", default="R2")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--fault", choices=["before", "after", "timeout"])
    modes.add_argument("--sdk", choices=["approve", "revoked", "unrecorded", "project-ask", "framework-deny"])
    args = parser.parse_args()
    if args.fault or args.sdk:
        with closing(sqlite3.connect(":memory:")) as db:
            open_ledger(db)
            if args.sdk:
                asyncio.run(sdk_demo(db, args.sdk))
            else:
                fault_demo(db, args.fault)
            notes = db.execute("SELECT project,body FROM notes ORDER BY operation_id").fetchall()
            print("台账回读", len(notes), "条", notes)
    else:
        try:
            main(args.budget, args.section)
        except (BudgetExceeded, Problem) as error:
            print(error)
