"""Small, connection-per-transaction SQLite store shared by the two applications."""

import asyncio
import sqlite3
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


def new_id() -> str:
    return str(uuid4())


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def timestamp(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Database:
    def __init__(self, path: Path):
        self.path = path

    def _run[T](self, begin: str, operation: Callable[[sqlite3.Connection], T]) -> T:
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA busy_timeout=5000")
            if begin == "initialize":
                db.execute("PRAGMA journal_mode=WAL")
                begin = "BEGIN IMMEDIATE"
            db.execute(begin)
            result = operation(db)
            db.commit()
            return result
        finally:
            db.close()  # Closing a failed transaction rolls it back.

    async def read[T](self, operation: Callable[[sqlite3.Connection], T]) -> T:
        return await asyncio.to_thread(self._run, "BEGIN", operation)

    async def write[T](self, operation: Callable[[sqlite3.Connection], T]) -> T:
        return await asyncio.to_thread(self._run, "BEGIN IMMEDIATE", operation)

    async def initialize(
        self,
        schema: str,
        seed: Callable[[sqlite3.Connection], None] | None = None,
        *,
        migrations=(),
        current_schema=False,
    ):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = 1 + len(migrations)

        def backup():
            if not migrations or not self.path.is_file():
                return
            with sqlite3.connect(self.path) as current:
                version = current.execute("PRAGMA user_version").fetchone()[0]
                if 0 < version < target:
                    with tempfile.NamedTemporaryFile(
                        prefix=f"{self.path.name}.v{version}-",
                        suffix=".bak",
                        dir=self.path.parent,
                        delete=False,
                    ) as f:
                        with sqlite3.connect(f.name) as dest:
                            current.backup(dest)

        def migrate(db):
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == target:
                return
            if version > target or (
                not version
                and db.execute("SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchone()
            ):
                raise RuntimeError(f"无法识别 {self.path.name} 的数据库版本，请核对备份。")
            steps = (
                [(target, schema)]
                if current_schema and not version
                else enumerate((schema, *migrations), start=1)
            )
            for number, script in steps:
                if number <= version:
                    continue
                for statement in script.split(";"):
                    if statement.strip():
                        db.execute(statement)
                if seed and not version and (number == 1 or current_schema):
                    seed(db)
                db.execute(f"PRAGMA user_version={number}")

        await asyncio.to_thread(backup)
        await asyncio.to_thread(self._run, "initialize", migrate)
