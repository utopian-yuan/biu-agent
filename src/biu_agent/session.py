"""Durable conversation log with a single active turn and atomic publication."""

import json
from dataclasses import dataclass, field
from typing import Any

from .agents import memory
from .agents.context import attachments
from .agents.tools import workfiles
from .db import Database, new_id, now_ms, timestamp
from .identity import Principal, Problem, hidden, scope_changed

SCHEMA = """
CREATE TABLE sessions(id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
    access_epoch TEXT NOT NULL, created_at INTEGER NOT NULL);
CREATE TABLE turns(id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
    ordinal INTEGER NOT NULL, request_id TEXT NOT NULL, input_text TEXT NOT NULL, visible_set_id TEXT,
    status TEXT NOT NULL, answer_text TEXT, display_json TEXT, error_code TEXT, created_at INTEGER NOT NULL,
    ended_at INTEGER, UNIQUE(session_id,request_id), UNIQUE(session_id,ordinal));
CREATE UNIQUE INDEX one_running ON turns(session_id) WHERE status='running';
CREATE TABLE actions(id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
    turn_id TEXT NOT NULL UNIQUE REFERENCES turns(id), kind TEXT NOT NULL, target_kind TEXT NOT NULL,
    target_id TEXT NOT NULL, target_label TEXT NOT NULL, expected_version INTEGER NOT NULL,
    payload_json TEXT NOT NULL, status TEXT NOT NULL, receipt_json TEXT, error_code TEXT,
    created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, confirmed_at INTEGER, updated_at INTEGER NOT NULL);
CREATE INDEX session_actions ON actions(session_id,status);
CREATE TABLE spans(id TEXT PRIMARY KEY, trace_id TEXT NOT NULL,parent_id TEXT,session_id TEXT,turn_id TEXT,
    action_id TEXT,name TEXT NOT NULL,started_at INTEGER NOT NULL,duration_ms INTEGER NOT NULL,status TEXT NOT NULL,
    error_code TEXT,model_name TEXT,input_tokens INTEGER,output_tokens INTEGER);
CREATE INDEX turn_spans ON spans(turn_id);
CREATE INDEX action_spans ON spans(action_id)
"""

WORKSPACE_SCHEMA = """
ALTER TABLE sessions ADD COLUMN title TEXT NOT NULL DEFAULT '';
ALTER TABLE sessions ADD COLUMN deleted_at INTEGER;
ALTER TABLE sessions ADD COLUMN updated_at INTEGER;
ALTER TABLE sessions ADD COLUMN agent_id TEXT NOT NULL DEFAULT '';
ALTER TABLE sessions ADD COLUMN model_id TEXT NOT NULL DEFAULT '';
ALTER TABLE turns ADD COLUMN feedback TEXT;
ALTER TABLE turns ADD COLUMN source_turn TEXT;
ALTER TABLE turns ADD COLUMN evidence_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE turns ADD COLUMN model_label TEXT;
UPDATE sessions SET updated_at=COALESCE((SELECT MAX(created_at) FROM turns WHERE session_id=sessions.id),created_at),
title=COALESCE((SELECT substr(input_text,1,36) FROM turns WHERE session_id=sessions.id ORDER BY ordinal LIMIT 1),'');
CREATE INDEX owner_history ON sessions(tenant_id,user_id,access_epoch,deleted_at,updated_at DESC)
"""

CONTEXT_SCHEMA = """
ALTER TABLE turns ADD COLUMN context_json TEXT;
ALTER TABLE turns ADD COLUMN requested_model TEXT
"""


FILES_SCHEMA = """
CREATE INDEX revision_order ON turns(session_id,source_turn,ordinal DESC);
CREATE TABLE attachments(id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
    turn_id TEXT NOT NULL REFERENCES turns(id), title TEXT NOT NULL, sha256 TEXT NOT NULL, sections TEXT NOT NULL);
CREATE INDEX session_files ON attachments(session_id,turn_id)
"""


def check_session(db, sid, principal):
    row = db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not row or row["deleted_at"] is not None or (row["tenant_id"], row["user_id"]) != principal.scope[:2]:
        raise hidden()
    if row["access_epoch"] != principal.epoch_for(row["agent_id"]):
        raise scope_changed()
    return row


def public_action(row):
    return {
        "action_id": row["id"],
        "turn_id": row["turn_id"],
        "kind": row["kind"],
        "target_id": row["target_id"],
        "target_label": row["target_label"],
        "body": json.loads(row["payload_json"]).get("body", ""),
        "status": row["status"],
        "expires_at": timestamp(row["expires_at"]),
        "receipt": None,
    }


def expire_pending(db, sid, when):
    db.execute(
        """UPDATE actions SET status='expired',updated_at=?
                  WHERE session_id=? AND status='pending' AND expires_at<=?""",
        (when, sid, when),
    )


@dataclass
class TurnContext:
    principal: Principal
    session_id: str
    turn_id: str
    input_text: str
    visible_set_id: str | None
    previous_display: dict | None
    history: list[tuple[str, str]]
    agent_id: str = ""
    model_id: str = ""
    source_turn: str | None = None
    evidence: dict = field(default_factory=dict)
    model_label: str | None = None
    profile: dict = field(default_factory=dict)
    context_summary: dict | None = None
    client: Any = None
    display: dict | None = None
    candidate: dict | None = None
    _conflicting_drafts: bool = field(default=False, repr=False)
    files: dict = field(default_factory=dict, repr=False)
    work: workfiles.WorkFiles = field(default_factory=workfiles.WorkFiles, repr=False)

    def show_items(self, items, *, next_cursor=None, scope=None):
        ordered = [{**item, "position": i} for i, item in enumerate(items, start=1)]
        self.display = {
            "display_set_id": new_id(),
            "items": ordered,
            "has_more": next_cursor is not None,
            "next_cursor": next_cursor,
            "scope": scope,
        }
        self.candidate = None
        return self.display

    def resolve_ref(self, position: int) -> dict:
        card = self.previous_display
        if (
            self.display is not None
            or not card
            or card["display_set_id"] != self.visible_set_id
            or not 1 <= position <= len(card["items"])
        ):
            raise Problem(409, "clarify_reference", "请先查看当前列表，再指定第几项。")
        return card["items"][position - 1]

    def discard_draft(self):
        self.candidate = None
        self._conflicting_drafts = True

    def propose(self, kind: str, target: dict, payload: dict, version: int):
        if self.source_turn:
            raise Problem(409, "read_only_revision", "重新生成仅更新答复，请到原消息核对操作。")
        proposal = {"kind": kind, "target": target, "payload": payload, "version": version}
        if self.candidate and proposal != self.candidate:
            self.discard_draft()
        if self._conflicting_drafts or self.display is not None:
            raise Problem(409, "clarify_draft", "请确认目标和唯一正文后再起草。")
        self.candidate = proposal


class Sessions:
    def __init__(self, db: Database, clock=now_ms):
        self.db = db
        self.clock = clock

    async def initialize(self):
        await self.db.initialize(
            SCHEMA, migrations=(WORKSPACE_SCHEMA, CONTEXT_SCHEMA, FILES_SCHEMA, workfiles.SCHEMA)
        )

        def recover(db):
            db.execute(
                """UPDATE turns SET status='interrupted',error_code='interrupted',ended_at=?
                          WHERE status='running'""",
                (self.clock(),),
            )
            db.execute(
                "UPDATE actions SET status='uncertain',updated_at=? WHERE status='executing'", (self.clock(),)
            )

        await self.db.write(recover)

    async def create(self, principal, agent_id="", model_id=""):
        sid = new_id()
        await self.db.write(
            lambda db: db.execute(
                "INSERT INTO sessions(id,tenant_id,user_id,access_epoch,created_at,updated_at,agent_id,model_id) VALUES (?,?,?,?,?,?,?,?)",
                (
                    sid,
                    principal.tenant_id,
                    principal.user_id,
                    principal.epoch_for(agent_id),
                    self.clock(),
                    self.clock(),
                    agent_id,
                    model_id,
                ),
            )
        )
        return sid

    async def listing(self, principal, query="", offset=0):
        def fetch(db):
            rows = db.execute(
                """SELECT id,title,updated_at,agent_id,model_id FROM sessions
                WHERE tenant_id=? AND user_id=? AND deleted_at IS NULL AND
                access_epoch=COALESCE((SELECT value FROM json_each(?) WHERE key=sessions.agent_id),?)
                AND instr(lower(title),lower(?))>0 ORDER BY updated_at DESC,id LIMIT 51 OFFSET ?""",
                (
                    principal.tenant_id,
                    principal.user_id,
                    json.dumps(principal.agent_epochs),
                    principal.access_epoch,
                    query,
                    offset,
                ),
            ).fetchall()
            return {
                "items": [dict(r) for r in rows[:50]],
                "next_offset": offset + 50 if len(rows) > 50 else None,
            }

        return await self.db.read(fetch)

    async def manage(self, principal, sid, *, title=None, delete=False):
        def update(db):
            check_session(db, sid, principal)
            if delete:
                if db.execute(
                    """SELECT 1 FROM turns WHERE session_id=? AND status='running' UNION ALL
                    SELECT 1 FROM actions WHERE session_id=? AND status IN ('executing','uncertain') LIMIT 1""",
                    (sid, sid),
                ).fetchone():
                    raise Problem(409, "session_busy", "请先等回复完成或核对待处理的操作。")
                db.execute(
                    "UPDATE actions SET status='cancelled',updated_at=? WHERE session_id=? AND status='pending'",
                    (self.clock(), sid),
                )
                db.execute(
                    "DELETE FROM tool_passages WHERE turn_id IN (SELECT id FROM turns WHERE session_id=?)",
                    (sid,),
                )
                db.execute("DELETE FROM work_versions WHERE session_id=?", (sid,))
                db.execute("DELETE FROM attachments WHERE session_id=?", (sid,))
                db.execute("UPDATE sessions SET deleted_at=? WHERE id=?", (self.clock(), sid))
            else:
                db.execute("UPDATE sessions SET title=?,updated_at=? WHERE id=?", (title, self.clock(), sid))

        await self.db.write(update)

    async def feedback(self, principal, sid, tid, value):
        def update(db):
            check_session(db, sid, principal)
            if not db.execute(
                "UPDATE turns SET feedback=? WHERE id=? AND session_id=? AND status='succeeded'",
                (value, tid, sid),
            ).rowcount:
                raise hidden()

        await self.db.write(update)

    async def start(self, principal, sid, request_id, text, visible, *, source=None, model_id=None, docs=()):
        def begin(db):
            session = check_session(db, sid, principal)
            cutoff = 9223372036854775807
            if source:
                original = db.execute(
                    "SELECT * FROM turns WHERE id=? AND session_id=?", (source, sid)
                ).fetchone()
                if not original:
                    raise hidden()
                if original["source_turn"]:
                    original = db.execute(
                        "SELECT * FROM turns WHERE id=? AND session_id=?", (original["source_turn"], sid)
                    ).fetchone()
                source_id, text_value, visible_value, cutoff = (
                    original["id"],
                    original["input_text"],
                    original["visible_set_id"],
                    original["ordinal"],
                )
            else:
                source_id, text_value, visible_value = None, text, visible
            old = db.execute(
                "SELECT * FROM turns WHERE session_id=? AND request_id=?", (sid, request_id)
            ).fetchone()
            if old:
                saved_files = attachments.attached_names(db, sid, old["id"])
                same_files = [(f["title"], f["sha256"]) for f in saved_files] == [
                    (f["title"], f["sha256"]) for f in docs
                ]
                if not same_files or (
                    old["input_text"],
                    old["visible_set_id"],
                    old["source_turn"],
                    old["requested_model"],
                ) != (
                    text_value,
                    visible_value,
                    source_id,
                    model_id,
                ):
                    raise Problem(409, "request_conflict", "原请求编号已用于其他内容。")
                return {"turn_id": old["id"], "status": old["status"], "replayed": True}
            if db.execute("SELECT 1 FROM turns WHERE session_id=? AND status='running'", (sid,)).fetchone():
                raise Problem(409, "turn_busy", "上一轮仍在进行。", True)
            last = memory.last_display(db, sid, cutoff)
            card = json.loads(last) if last else None
            if visible_value and (not card or card["display_set_id"] != visible_value):
                raise Problem(409, "stale_display", "页面里的列表已过时，请刷新。")
            history = memory.history_before(db, sid, cutoff, questions_only=bool(session["agent_id"]))
            tid = new_id()
            db.execute(
                """INSERT INTO turns(id,session_id,ordinal,request_id,input_text,visible_set_id,status,created_at,source_turn,requested_model)
                          SELECT ?,?,COALESCE(MAX(ordinal),0)+1,?,?,?,'running',?,?,? FROM turns WHERE session_id=?""",
                (tid, sid, request_id, text_value, visible_value, self.clock(), source_id, model_id, sid),
            )
            attachments.store(db, sid, tid, docs)
            files = attachments.files_through(db, sid, cutoff)
            model = model_id or session["model_id"]
            db.execute(
                "UPDATE sessions SET title=CASE WHEN title='' THEN ? ELSE title END,updated_at=?,model_id=? WHERE id=?",
                (text_value[:36], self.clock(), model, sid),
            )
            return TurnContext(
                principal,
                sid,
                tid,
                text_value,
                visible_value,
                card,
                history,
                session["agent_id"],
                model,
                source_id,
                files=files,
                work=workfiles.WorkFiles.before(db, sid, cutoff, read_only=bool(source_id)),
            )

        return await self.db.write(begin)

    async def finish(self, ctx, answer):
        def commit(db):
            when = self.clock()
            display_json = json.dumps(ctx.display, ensure_ascii=False) if ctx.display is not None else None
            updated = db.execute(
                """UPDATE turns SET status='succeeded',answer_text=?,display_json=?,ended_at=?,evidence_json=?,model_label=?,context_json=?
                                    WHERE id=? AND status='running'""",
                (
                    answer,
                    display_json,
                    when,
                    json.dumps(list(ctx.evidence.values()), ensure_ascii=False),
                    ctx.model_label,
                    json.dumps(ctx.context_summary, ensure_ascii=False) if ctx.context_summary else None,
                    ctx.turn_id,
                ),
            )
            if not updated.rowcount:
                raise Problem(409, "turn_closed", "本轮已结束。")
            ctx.work.commit(db, ctx.session_id, ctx.turn_id)
            preview = ctx.candidate
            if not preview:
                return None
            target = preview["target"]
            db.execute(
                """UPDATE actions SET status='superseded',updated_at=?
                          WHERE session_id=? AND target_kind=? AND target_id=? AND status='pending'""",
                (when, ctx.session_id, target["kind"], target["id"]),
            )
            aid = new_id()
            row = db.execute(
                """INSERT INTO actions(id,session_id,turn_id,kind,target_kind,target_id,target_label,
                              expected_version,payload_json,status,created_at,expires_at,updated_at)
                              VALUES (?,?,?,?,?,?,?,?,?,'pending',?,?,?) RETURNING *""",
                (
                    aid,
                    ctx.session_id,
                    ctx.turn_id,
                    preview["kind"],
                    target["kind"],
                    target["id"],
                    target["label"],
                    preview["version"],
                    json.dumps(preview["payload"], ensure_ascii=False),
                    when,
                    when + 900_000,
                    when,
                ),
            ).fetchone()
            return public_action(row)

        return await self.db.write(commit)

    async def fail(self, tid, code, status="failed"):
        await self.db.write(
            lambda db: db.execute(
                """UPDATE turns SET status=?,error_code=?,ended_at=?
                                                     WHERE id=? AND status='running'""",
                (status, code, self.clock(), tid),
            )
        )

    async def snapshot(self, principal, sid, before=None):
        def fetch(db):
            session = check_session(db, sid, principal)
            expire_pending(db, sid, self.clock())
            rows = memory.page(db, sid, before if before is not None else 2**63 - 1, 21)
            page = rows[:20][::-1]
            turns = [
                {
                    "turn_id": r["id"],
                    "request_id": r["request_id"],
                    "ordinal": r["position"],
                    "root_turn_id": r["root_id"],
                    "revision_status": r["attempt_status"],
                    "revision_error": r["attempt_error"],
                    "outputs": workfiles.outputs(db, r["root_id"]),
                    "attachments": attachments.attached_names(db, sid, r["root_id"]),
                    "input_text": r["input_text"],
                    "answer_text": r["answer_text"],
                    "status": r["status"],
                    "display": json.loads(r["display_json"]) if r["display_json"] else None,
                    "error_code": r["error_code"],
                    "feedback": r["feedback"],
                    "source_turn": r["source_turn"],
                    "evidence": json.loads(r["evidence_json"]),
                    "model_label": r["model_label"],
                    "context": json.loads(r["context_json"]) if r["context_json"] else None,
                }
                for r in page
            ]
            actions = db.execute(
                """SELECT a.* FROM actions a JOIN turns t ON t.id=a.turn_id
                                   WHERE a.session_id=? AND ((t.ordinal BETWEEN ? AND ?) OR
                                   (? IS NULL AND a.status IN ('pending','executing','uncertain')))
                                   ORDER BY t.ordinal""",
                (sid, page[0]["position"] if page else 0, page[-1]["position"] if page else 0, before),
            ).fetchall()
            return {
                "session_id": sid,
                "title": session["title"],
                "agent_id": session["agent_id"],
                "model_id": session["model_id"],
                "turns": turns,
                "actions": [public_action(a) for a in actions],
                "next_before": page[0]["position"] if len(rows) > 20 else None,
            }

        return await self.db.write(fetch)
