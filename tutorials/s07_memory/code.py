"""只记用户明确保存的回答格式，重开数据库模拟新会话。"""

import sqlite3
from argparse import ArgumentParser
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from examples.tickets.corpus import excerpt


def remember(db, owner, layout):
    db.execute(
        """INSERT INTO preferences(owner, layout) VALUES (?, ?)
           ON CONFLICT(owner) DO UPDATE SET layout=excluded.layout""",
        (owner, layout),
    )


def answer(db, owner):
    preference = db.execute("SELECT layout FROM preferences WHERE owner=?", (owner,)).fetchone()
    layout = preference[0] if preference else "段落"
    row = excerpt("receiving", "R2", {"receiving"})
    if layout == "表格":
        return f"| 出处 | 原文 |\n| --- | --- |\n| {row['source']} | {row['text']} |"
    return f"{row['text']} [{row['source']}]"


def main(path, forget):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            """CREATE TABLE preferences(owner TEXT PRIMARY KEY,
               layout TEXT NOT NULL CHECK(layout IN ('段落', '表格')))"""
        )
        remember(db, "小舟", "表格")
    # 第二个连接只持有当前账号标识。上轮的消息列表已离开作用域。
    with closing(sqlite3.connect(path)) as db, db:
        if forget:
            db.execute("DELETE FROM preferences WHERE owner=?", ("小舟",))
        for owner in ("小舟", "青禾"):
            print(owner, answer(db, owner), sep="\n")


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--forget", action="store_true")
    args = parser.parse_args()
    with TemporaryDirectory() as directory:
        main(Path(directory) / "memory.sqlite3", args.forget)
