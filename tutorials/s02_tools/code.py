"""从工具声明和分页读取，到按批准内容登记一条待发摘录。"""

import asyncio
import json
import sqlite3
from argparse import ArgumentParser
from contextlib import closing
from hashlib import sha256

from agentscope.message import ToolCallBlock
from agentscope.state import AgentState
from agentscope.tool import Toolkit, ToolResponse
from pydantic import Field

from biu_agent.identity import Problem
from biu_agent.tools import Inputs, tool
from examples.tickets.corpus import Passage, document, excerpt


class Page(Inputs):
    id: str = Field(min_length=1, max_length=48, description="已知的资料 ID，本题为 receiving。")
    cursor: str | None = Field(
        default=None, max_length=180, description="首次省略，续页原样传回 next_cursor。"
    )


def section_page(doc_id, cursor, allowed):
    doc = document(doc_id, allowed)
    sections = list(doc["sections"])
    tokens = [f"{doc_id}:{doc['sha256']}:{section}" for section in sections]
    if cursor is not None and cursor not in tokens:
        raise Problem(409, "invalid_cursor", "续页标记与当前资料版本不匹配，请重新列目录。")
    start = 0 if cursor is None else tokens.index(cursor)
    return {
        "id": doc_id,
        "sha256": doc["sha256"],
        "sections": sections[start : start + 1],
        "next_cursor": tokens[start + 1] if start + 1 < len(sections) else None,
    }


def build_toolkit(allowed, evidence):
    async def list_sections(id, cursor=None):
        return section_page(id, cursor, allowed)

    async def read_material(id, section):
        return excerpt(id, section, allowed)

    def record_excerpt(row):
        evidence[row["source"]] = row["sha256"]

    return Toolkit(
        tools=[
            tool(
                list_sections, Page, "已知资料 ID 时，逐页列出可读取的段号。将返回的段号用于 read_material。"
            ),
            tool(
                read_material,
                Passage,
                "已知资料 ID 与段号时，读取原文、出处和版本。未知段号先用 list_sections。",
                on_result=record_excerpt,
            ),
        ]
    )


async def invoke(toolkit, state, call_id, name, arguments):
    call = ToolCallBlock(id=call_id, name=name, input=json.dumps(arguments, ensure_ascii=False))
    print("请求", call.id, call.name, call.input)
    result = None
    async for event in toolkit.call_tool(call, state):
        if isinstance(event, ToolResponse):
            result = event
    if result is None:
        raise RuntimeError("SDK 工具流没有给出终态")
    print("回执", result.id, result.state)
    return result


async def report(toolkit, state, max_pages):
    cursor = None
    seen = set()
    for number in range(1, max_pages + 1):
        args = {"id": "receiving"}
        if cursor is not None:
            args["cursor"] = cursor
        response = await invoke(toolkit, state, f"page-{number}", "list_sections", args)
        if response.state != "success":
            return "目录未读完：" + response.content[0].text
        page = json.loads(response.content[0].text)
        for section in page["sections"]:
            point = {"id": page["id"], "section": section}
            response = await invoke(toolkit, state, f"read-{number}-{section}", "read_material", point)
            if response.state != "success":
                return "原文未读完：" + response.content[0].text
            row = json.loads(response.content[0].text)
            if row["sha256"] != page["sha256"]:
                return "清单未读完，目录与原文版本不同，请重新核对"
            print(f"原文 [{row['source']}] {row['text']}")

        cursor = page["next_cursor"]
        if cursor is None:
            return "清单已读完"
        if cursor in seen:
            return "清单未读完，续页标记重复"
        seen.add(cursor)
    return "清单未读完，已达到页数上限"


async def main(args):
    evidence = {}
    toolkit = build_toolkit({"receiving"}, evidence)
    state = AgentState()
    schemas = await toolkit.get_tool_schemas()
    if args.schema:
        print(json.dumps(schemas, ensure_ascii=False, indent=2))
        return
    if args.approval_demo:
        await approval_demo(toolkit, state)
    elif args.report:
        print(await report(toolkit, state, args.max_pages))
    else:
        name = "read_material"
        arguments = {"id": "receiving", "section": args.section}
        if args.call:
            name, arguments = args.call
        if args.extra:
            arguments["all"] = True
        for item in schemas:
            schema = item["function"]
            if schema["name"] == name:
                print("工具", name, "必填参数", schema["parameters"].get("required", []))
        result = await invoke(toolkit, state, "call-1", name, arguments)
        print("工具回传", result.content[0].text)
    print("登记的出处", list(evidence))


def enqueue_reviewed(db, review_id, recipient, body):
    digest = sha256(body.encode("utf-8")).hexdigest()
    with db:
        insert = db.execute(
            """INSERT INTO outbox(review_id, recipient, body)
               SELECT id, recipient, ? FROM approvals
               WHERE id=? AND recipient=? AND digest=?
               ON CONFLICT(review_id) DO NOTHING""",
            (body, review_id, recipient, digest),
        )
        if insert.rowcount:
            return f"已登记，回执 {review_id}"
        replay = db.execute(
            "SELECT review_id FROM outbox WHERE review_id=? AND recipient=? AND body=?",
            (review_id, recipient, body),
        ).fetchone()
    return f"复用回执 {replay[0]}" if replay else "缺少匹配的批准"


async def approval_demo(toolkit, state):
    response = await invoke(
        toolkit, state, "draft-read", "read_material", {"id": "receiving", "section": "R2"}
    )
    if response.state != "success":
        print("摘录生成失败", response.content[0].text)
        return
    row = json.loads(response.content[0].text)
    body = f"{row['text']} [{row['source']}]"
    recipient = "展具组"
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("CREATE TABLE approvals(id INTEGER PRIMARY KEY, recipient TEXT, digest TEXT)")
        db.execute("CREATE TABLE outbox(review_id INTEGER PRIMARY KEY, recipient TEXT, body TEXT)")
        print("复核前", enqueue_reviewed(db, 1, recipient, body))
        # 固定的人工复核样本。审批记录与工具清单分开，模型只能提出请求。
        with db:
            db.execute(
                "INSERT INTO approvals VALUES(1, ?, ?)", (recipient, sha256(body.encode()).hexdigest())
            )
        for label, target, text in [
            ("已批原稿", recipient, body),
            ("重复提交", recipient, body),
            ("改动正文", recipient, body + " 可以立即投入使用。"),
            ("更换对象", "场地组", body),
        ]:
            print(label, enqueue_reviewed(db, 1, target, text))
        queued = db.execute("SELECT review_id, recipient, body FROM outbox ORDER BY review_id").fetchall()
        print("待发队列回读", len(queued), "条", queued)


if __name__ == "__main__":
    parser = ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--schema", action="store_true", help="打印模型实际可见的工具声明")
    mode.add_argument("--report", action="store_true", help="按续页标记读完整份到货记录")
    mode.add_argument("--approval-demo", action="store_true", help="在内存数据库中验证签收摘录的审批与入队")
    mode.add_argument("--call", nargs=2, metavar=("NAME", "JSON"), help="给出工具名和 JSON 参数对象")
    parser.add_argument("--max-pages", type=int, default=8, help="分页实验最多读取多少页")
    parser.add_argument("--section", default="R2", help="单次读取的段号")
    parser.add_argument("--extra", action="store_true", help="在单次请求中加入未声明的 all 字段")
    args = parser.parse_args()
    if args.max_pages < 1:
        parser.error("--max-pages 至少为 1")
    if args.call:
        try:
            arguments = json.loads(args.call[1])
        except ValueError:
            parser.error("--call 的第二项必须是有效 JSON")
        if not isinstance(arguments, dict):
            parser.error("--call 的参数必须是 JSON 对象")
        args.call = (args.call[0], arguments)
    asyncio.run(main(args))
