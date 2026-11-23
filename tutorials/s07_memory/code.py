"""通过重开数据库检查跨会话偏好的写入、修改、隔离和删除。"""

import sqlite3
from argparse import ArgumentParser
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from biu_agent.identity import Principal, Problem
from examples.tickets.corpus import excerpt

XIAO = Principal("展具组", "u-7", "1", "", display_name="小舟")
QING = Principal("展具组", "u-8", "1", "", display_name="青禾")
OTHER = Principal("灯光组", "u-7", "1", "", display_name="另一租户的同号账号")


def remember(db, actor, layout):
    db.execute(
        """INSERT INTO preferences(tenant, owner, layout) VALUES (?, ?, ?)
           ON CONFLICT(tenant,owner) DO UPDATE SET layout=excluded.layout""",
        (actor.tenant_id, actor.user_id, layout),
    )


def forget(db, actor):
    db.execute("DELETE FROM preferences WHERE tenant=? AND owner=?", (actor.tenant_id, actor.user_id))


def answer(db, actor, grants):
    preference = db.execute(
        "SELECT layout FROM preferences WHERE tenant=? AND owner=?", (actor.tenant_id, actor.user_id)
    ).fetchone()
    layout = preference[0] if preference else "段落"
    try:
        row = excerpt("receiving", "R2", grants)
    except Problem as error:
        return f"当前格式 {layout}，原文不可读 {error.code}"
    if layout == "表格":
        return f"| 出处 | 原文 |\n| --- | --- |\n| {row['source']} | {row['text']} |"
    return f"{row['text']} [{row['source']}]"


def main(path, args):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("""CREATE TABLE preferences(tenant TEXT NOT NULL, owner TEXT NOT NULL,
                   layout TEXT NOT NULL CHECK(layout IN ('段落', '表格')), PRIMARY KEY(tenant,owner))""")
        if not args.once:
            remember(db, XIAO, "表格")
        if args.isolation:
            remember(db, OTHER, "段落")
    with closing(sqlite3.connect(path)) as db, db:
        if args.update:
            remember(db, XIAO, "段落")
        if args.forget:
            forget(db, XIAO)
    # 第三个连接只有当前身份与资料授权，读者可在这里核对实际留下的记录。
    with closing(sqlite3.connect(path)) as db:
        print(
            "偏好表回读",
            db.execute("SELECT tenant,owner,layout FROM preferences ORDER BY tenant,owner").fetchall(),
        )
        for actor in [XIAO, QING, OTHER] if args.isolation else [XIAO, QING]:
            grants = set() if args.revoked and actor == XIAO else {"receiving"}
            print(actor.display_name, answer(db, actor, grants), sep="\n")


if __name__ == "__main__":
    parser = ArgumentParser()
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--once", action="store_true", help="本次格式要求只用于当前对话，不保存")
    actions.add_argument("--update", action="store_true", help="下一次明确要求改为段落")
    actions.add_argument("--forget", action="store_true")
    parser.add_argument("--isolation", action="store_true", help="加入其他租户的同号账号")
    parser.add_argument("--revoked", action="store_true", help="新会话已无到货原文的读取权")
    args = parser.parse_args()
    with TemporaryDirectory() as directory:
        main(Path(directory) / "memory.sqlite3", args)
