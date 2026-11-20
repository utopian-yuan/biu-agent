"""Metadata-only local spans. Exporters receive the same allowlisted fields."""

import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field

from .db import Database, new_id, now_ms
from .identity import Problem

active_tool = ContextVar("biu_active_tool", default=None)


@asynccontextmanager
async def business_read(name: str):
    """Time a host read under its calling tool. Pass a constant operation name, never a URL."""
    active = active_tool.get()
    if active is None:
        yield
        return
    tracing, parent = active
    span = parent.child("business." + name)
    try:
        yield
    except Exception as exc:
        await tracing.end(span, "error", exc.code if isinstance(exc, Problem) else "read_failed")
        raise
    else:
        await tracing.end(span)


@dataclass
class Span:
    name: str
    session_id: str
    turn_id: str
    action_id: str | None = None
    parent_id: str | None = None
    trace_id: str = field(default_factory=new_id)
    id: str = field(default_factory=new_id)
    started_at: int = field(default_factory=now_ms)
    model_name: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    _start: float = field(default_factory=time.monotonic, repr=False)

    def child(self, name: str):
        return Span(
            name, self.session_id, self.turn_id, self.action_id, parent_id=self.id, trace_id=self.trace_id
        )


class Tracing:
    def __init__(self, db: Database, export=None):
        self.db = db
        self.export = export

    async def end(self, span: Span, status="ok", error_code=None):
        data = asdict(span)
        data["duration_ms"] = round((time.monotonic() - data.pop("_start")) * 1000)
        data.update(status=status, error_code=error_code)
        await self.db.write(
            lambda db: db.execute(
                """INSERT INTO spans(id,trace_id,parent_id,session_id,turn_id,action_id,name,started_at,duration_ms,
               status,error_code,model_name,input_tokens,output_tokens) VALUES
               (:id,:trace_id,:parent_id,:session_id,:turn_id,:action_id,:name,:started_at,:duration_ms,
               :status,:error_code,:model_name,:input_tokens,:output_tokens)""",
                data,
            )
        )
        if self.export is not None:
            # Export is a nonblocking enqueue. Remote availability cannot determine a business result.
            try:
                self.export(data)
            except Exception:
                pass
