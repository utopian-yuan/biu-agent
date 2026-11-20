"""Versioned text files owned by one conversation. Paths are virtual, never operating-system paths."""

import json
import re
from dataclasses import dataclass, field

from ...identity import Problem, hidden

SCHEMA = """
CREATE TABLE work_versions(session_id TEXT NOT NULL REFERENCES sessions(id), path TEXT NOT NULL,
 version INTEGER NOT NULL, turn_id TEXT NOT NULL REFERENCES turns(id), body TEXT NOT NULL,
 PRIMARY KEY(session_id,path,version));
CREATE INDEX work_turn ON work_versions(turn_id);
CREATE TABLE tool_passages(turn_id TEXT NOT NULL REFERENCES turns(id), source TEXT NOT NULL, data TEXT NOT NULL,
 PRIMARY KEY(turn_id,source))
"""


def work_path(path):
    parts = path.split("/")
    if (
        len(path) > 200
        or len(parts) > 10
        or any(
            not s.strip() or s != s.strip() or s.startswith(".") or re.search(r"[\\:\x00-\x1f\x7f]", s)
            for s in parts
        )
    ):
        raise Problem(422, "bad_file_path", "请使用会话内的相对文件名。")
    if parts[0] in {"materials", "uploads"}:
        raise Problem(403, "source_read_only", "来源文件为只读，可另存到 work/。")
    if parts == ["work"]:
        raise Problem(422, "bad_file_path", "请输入 work/ 下的文件名。")
    return path if parts[0] == "work" else "work/" + path


@dataclass
class WorkFiles:
    versions: dict = field(default_factory=dict, repr=False)
    changes: dict = field(default_factory=dict, repr=False)
    passages: dict = field(default_factory=dict, repr=False)
    read_only: bool = False

    @classmethod
    def before(cls, db, sid, cutoff, *, read_only=False):
        rows = db.execute(
            """SELECT w.path,w.version,w.body FROM work_versions w JOIN (
                SELECT f.path,MAX(f.version) AS version FROM work_versions f JOIN turns t ON t.id=f.turn_id
                WHERE f.session_id=? AND t.ordinal<? GROUP BY f.path
            ) last ON last.path=w.path AND last.version=w.version WHERE w.session_id=?""",
            (sid, cutoff, sid),
        )
        return cls({r["path"]: (r["version"], r["body"]) for r in rows}, read_only=read_only)

    def paths(self):
        return self.versions.keys() | self.changes.keys()

    def get(self, path):
        if path not in self.paths():
            raise hidden()
        version, original = self.versions.get(path, (0, ""))
        return (version + 1, self.changes[path]) if path in self.changes else (version, original)

    def stage(self, path, content, expected_version=None):
        if self.read_only:
            raise Problem(409, "read_only_revision", "重新生成仅更新回答，请在新问题中改文件。")
        path = work_path(path)
        if len(content) > 64000 or "\x00" in content:
            raise Problem(422, "invalid_file", "会话文件最多 64000 个文本字符。")
        paths = self.paths()
        if any(path.startswith(p + "/") or p.startswith(path + "/") for p in paths):
            raise Problem(409, "file_directory_conflict", "该路径已用作文件或目录，请更换文件名。")
        if path not in paths and len(paths) >= 32:
            raise Problem(409, "file_quota", "每段对话最多 32 个生成文件。")
        current = self.get(path)[0] if path in paths else 0
        if expected_version is not None and current != expected_version:
            raise Problem(409, "file_changed", "文件版本已变化，请重新读取。")
        self.changes[path] = content
        return {
            "path": path,
            "version": self.get(path)[0],
            "characters": len(content),
            "status": "随本轮成功保存",
        }

    def commit(self, db, sid, tid):
        for path, body in self.changes.items():
            expected = self.versions.get(path, (0, ""))[0]
            latest = db.execute(
                "SELECT coalesce(max(version),0) FROM work_versions WHERE session_id=? AND path=?",
                (sid, path),
            ).fetchone()[0]
            if latest != expected:
                raise Problem(409, "file_changed", "文件已有新的版本，请重新读取。")
            db.execute("INSERT INTO work_versions VALUES(?,?,?,?,?)", (sid, path, expected + 1, tid, body))
        db.executemany(
            "INSERT INTO tool_passages VALUES(?,?,?)",
            [(tid, ref, json.dumps(row, ensure_ascii=False)) for ref, row in self.passages.items()],
        )


def outputs(db, tid):
    return [
        dict(r)
        for r in db.execute("SELECT path,version FROM work_versions WHERE turn_id=? ORDER BY path", (tid,))
    ]
