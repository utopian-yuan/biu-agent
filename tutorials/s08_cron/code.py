"""由固定时钟触发一次只读核对。槽位与本地报告在同一事务提交。"""

import sqlite3
from argparse import ArgumentParser
from contextlib import closing
from datetime import datetime, time
from zoneinfo import ZoneInfo

from tutorials.s01_agent_loop.code import decide_receipt, read_receipt, run_loop


def daily_time(expression):
    match expression.split():
        case [minute, hour, "*", "*", "*"] if minute.isdecimal() and hour.isdecimal():
            return time(int(hour), int(minute))
        case _:
            raise ValueError("本课支持每日固定时刻，例如 0 9 * * *")


def tick(db, now, due, read):
    local = now.astimezone(ZoneInfo("Asia/Shanghai"))
    if local.time().replace(second=0, microsecond=0) != due:
        return "未到触发分钟"
    slot = local.isoformat(timespec="minutes")
    with db:
        claimed = db.execute(
            "INSERT INTO reports(job, slot) VALUES (?, ?) ON CONFLICT(job,slot) DO NOTHING",
            ("签收核对", slot),
        )
        if not claimed.rowcount:
            return "该分钟已经完成"
        report = run_loop("导向牌签收了吗？", decide_receipt, read, observe=lambda _: None)
        db.execute("UPDATE reports SET body=? WHERE job=? AND slot=?", (report, "签收核对", slot))
    return report


def unavailable(call):
    raise ConnectionError("资料暂不可读，本次事务回滚")


def main(schedule, fail_first):
    due = daily_time(schedule)
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("CREATE TABLE reports(job TEXT, slot TEXT, body TEXT, PRIMARY KEY(job, slot))")
        for index, minute in enumerate(("08:59", "09:00", "09:00", "09:01")):
            now = datetime.fromisoformat(f"2032-05-18T{minute}:00+08:00")
            read = unavailable if fail_first and index == 1 else read_receipt
            try:
                print(minute, tick(db, now, due, read))
            except ConnectionError as error:
                print(minute, error)
        print("实际报告数", db.execute("SELECT COUNT(*) FROM reports WHERE body IS NOT NULL").fetchone()[0])


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--schedule", default="0 9 * * *")
    parser.add_argument("--fail-first", action="store_true")
    args = parser.parse_args()
    try:
        main(args.schedule, args.fail_first)
    except ValueError as error:
        parser.error(str(error))
