"""把历史选择、模型输入、本轮读取和发布检查分开观察。"""

import sqlite3
from argparse import ArgumentParser
from contextlib import closing
from hashlib import sha256
from tempfile import TemporaryDirectory

from biu_agent.agents.context import messages_for
from biu_agent.agents.memory import history_before
from biu_agent.identity import Principal, Problem
from biu_agent.session import TurnContext
from examples.tickets.accounts import Accounts
from examples.tickets.corpus import excerpt
from examples.tickets.workspace import Workspace


def material_turn():
    ctx = TurnContext(
        principal=Principal("展具组", "小舟", "1", ""),
        session_id="练习会话",
        turn_id="第二问",
        input_text="那导向牌呢？",
        previous_display=None,
        visible_set_id=None,
        history=[("样品灯验收了吗？", "样品灯验收通过。[acceptance#A1]")],
    )
    ctx.profile["sources"] = {"receiving"}
    return ctx


def main(workspace, case, history):
    ctx = material_turn()
    if history == "empty":
        ctx.history.clear()
    for msg in messages_for(ctx, questions_only=history != "full"):
        print("本轮模型输入", msg.role, msg.get_text_content())
    row = excerpt("receiving", "R2", ctx.profile["sources"])
    ctx.evidence[row["source"]] = row.copy()
    ref = "acceptance#A1" if case == "reuse" else row["source"]
    claim = "导向牌已通过验收。" if case == "wrong-claim" else row["text"]
    candidate = claim if case == "no-citation" else f"{claim} [{ref}]"
    if case == "stale-version":
        # 重现读取旧版后发布的场景，让 verify 与当前磁盘文件重新对账。
        ctx.evidence[row["source"]]["sha256"] = sha256("此前的练习版本".encode()).hexdigest()
    print("本轮实读", row["source"], row["text"])
    try:
        print("发布", workspace.verify(ctx, candidate))
    except Problem as error:
        print("拒绝发布", error.code)


def history_window(db):
    # 只建产品查询读取的列，输入包含成功修订、失败修订和一份超长旧回答。
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE turns(id TEXT, session_id TEXT, ordinal INTEGER, source_turn TEXT,
                   status TEXT, error_code TEXT, input_text TEXT, answer_text TEXT)""")
    rows = [
        (f"q{i}", "demo", i, None, "succeeded", None, f"问题{i}", "长" * 12001 if i == 6 else f"原答{i}")
        for i in range(1, 8)
    ]
    rows.extend(
        [
            ("r4", "demo", 8, "q4", "succeeded", None, "问题4", "修订答4"),
            ("r5", "demo", 9, "q5", "failed", "fixture", "问题5", ""),
            ("q8", "demo", 10, None, "succeeded", None, "问题8", "原答8"),
        ]
    )
    db.executemany("INSERT INTO turns VALUES(?,?,?,?,?,?,?,?)", rows)
    for label, cutoff, only_questions in [
        ("原问题4之前", 4, True),
        ("后来只取问题", 11, True),
        ("后来连同回答", 11, False),
    ]:
        selected = history_before(db, "demo", cutoff, questions_only=only_questions)
        print(label, [question for question, _ in selected])
        if label == "后来只取问题":
            print("所选回答版本", [answer for q, answer in selected if q in ("问题4", "问题5")])


if __name__ == "__main__":
    parser = ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--reuse-citation", action="store_true")
    mode.add_argument(
        "--case", choices=["valid", "wrong-claim", "no-citation", "stale-version"], default="valid"
    )
    mode.add_argument("--history-window", action="store_true")
    parser.add_argument("--history", choices=["questions", "full", "empty"], default="questions")
    args = parser.parse_args()
    if args.history_window:
        with closing(sqlite3.connect(":memory:")) as db:
            history_window(db)
    else:
        with TemporaryDirectory() as directory:
            main(Workspace(Accounts(directory)), "reuse" if args.reuse_citation else args.case, args.history)
