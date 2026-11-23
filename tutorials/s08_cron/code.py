"""用带时区的固定时钟和独立 SQLite 连接验证漏跑、重试及并发触发。"""

import sqlite3
from argparse import ArgumentParser
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, time
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from zoneinfo import ZoneInfo

from tutorials.s01_agent_loop.code import decide_receipt, read_receipt, run_loop

ZONE = ZoneInfo("Asia/Shanghai")
JOB = "签收核对"
CLOCKS = {
    "default": [
        "2032-05-18T08:59+08:00",
        "2032-05-18T09:00+08:00",
        "2032-05-18T09:00+08:00",
        "2032-05-18T09:01+08:00",
    ],
    "utc": ["2032-05-18T01:00+00:00", "2032-05-18T09:00+08:00"],
    "next-day": ["2032-05-18T09:00+08:00", "2032-05-19T09:00+08:00"],
    "missed": ["2032-05-18T08:59+08:00", "2032-05-18T09:01+08:00"],
}


def daily_time(expression):
    match expression.split():
        case [minute, hour, "*", "*", "*"] if (
            minute.isascii() and hour.isascii() and minute.isdecimal() and hour.isdecimal()
        ):
            return time(int(hour), int(minute))
        case _:
            raise ValueError("本课支持每日固定时刻，例如 0 9 * * *")


def tick(db, now, due, read):
    if now.utcoffset() is None:
        raise ValueError("时钟输入需要时区")
    local = now.astimezone(ZONE)
    if local.time().replace(second=0, microsecond=0) != due:
        return "未到触发分钟"
    slot = local.isoformat(timespec="minutes")
    with db:
        claimed = db.execute(
            "INSERT INTO reports(job,slot) VALUES (?,?) ON CONFLICT(job,slot) DO NOTHING", (JOB, slot)
        )
        if not claimed.rowcount:
            return "该分钟已经完成"
        report = run_loop("导向牌签收了吗？", decide_receipt, read, observe=lambda _: None)
        db.execute("UPDATE reports SET body=? WHERE job=? AND slot=?", (report, JOB, slot))
    return report


def unavailable(call):
    raise ConnectionError("资料暂不可读，本次事务回滚")


def simultaneous(path, due):
    start = Barrier(2)

    def worker():
        with closing(sqlite3.connect(path)) as db:
            start.wait(timeout=5)
            return tick(db, datetime.fromisoformat("2032-05-18T09:00+08:00"), due, read_receipt)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker) for _ in range(2)]
        return sorted(future.result() for future in futures)


def main(path, due, clock, fail_first):
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE reports(job TEXT, slot TEXT, body TEXT, PRIMARY KEY(job,slot))")
    if clock == "concurrent":
        print("两个连接的结果", simultaneous(path, due))
    else:
        for moment in CLOCKS[clock]:
            now = datetime.fromisoformat(moment)
            label = now.strftime("%H:%M") if clock == "default" else moment
            with closing(sqlite3.connect(path)) as db:
                read = read_receipt
                if fail_first and now.astimezone(ZONE).time() == due:
                    read, fail_first = unavailable, False
                try:
                    print(label, tick(db, now, due, read))
                except ConnectionError as error:
                    print(label, error)
    with closing(sqlite3.connect(path)) as db:
        rows = db.execute("SELECT slot,body FROM reports ORDER BY slot").fetchall()
        print("实际报告数", len(rows))
        for slot, body in rows:
            print("回读", slot, body)


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--schedule", default="0 9 * * *")
    parser.add_argument("--fail-first", action="store_true")
    parser.add_argument("--clock", choices=[*CLOCKS, "concurrent"], default="default")
    args = parser.parse_args()
    if args.clock == "concurrent" and args.fail_first:
        parser.error("--fail-first 用于顺序时钟")
    try:
        due = daily_time(args.schedule)
    except ValueError as error:
        parser.error(str(error))
    with TemporaryDirectory() as directory:
        main(Path(directory) / "jobs.sqlite3", due, args.clock, args.fail_first)
