"""Only an authenticated confirmation can drive an immutable business operation."""

import asyncio
import json
from collections.abc import Callable, Mapping
from typing import Protocol

from .identity import Principal, Problem, hidden
from .session import Sessions, check_session, expire_pending, public_action
from .tracing import Span, Tracing


class ActionHandler(Protocol):
    async def execute(self, principal: Principal, action: dict) -> dict: ...
    async def lookup(self, principal: Principal, operation_id: str) -> dict | None: ...


class Actions:
    def __init__(self, sessions: Sessions, handlers: Mapping[str, ActionHandler], tracing: Tracing):
        self.sessions = sessions
        self.handlers = dict(handlers)
        self.tracing = tracing
        self.inflight: set[asyncio.Task] = set()

    async def _claim(self, principal, sid, aid, cancel):
        def claim(db):
            check_session(db, sid, principal)
            when = self.sessions.clock()
            expire_pending(db, sid, when)
            row = db.execute("SELECT * FROM actions WHERE id=? AND session_id=?", (aid, sid)).fetchone()
            if not row:
                raise hidden()
            old = row["status"]
            if not cancel and row["kind"] not in self.handlers:
                raise Problem(409, "action_unavailable", "宿主仅提供此动作的预览。")
            if cancel and old == "pending":
                db.execute(
                    "UPDATE actions SET status='cancelled',updated_at=? WHERE id=? AND status='pending'",
                    (when, aid),
                )
                return "cancelled", dict(row)
            if not cancel and old in ("pending", "uncertain"):
                db.execute(
                    """UPDATE actions SET status='executing',confirmed_at=COALESCE(confirmed_at,?),updated_at=?
                              WHERE id=? AND status=?""",
                    (when, when, aid, old),
                )
            return old, dict(row)

        return await self.sessions.db.write(claim)

    async def _settle(self, aid, status, receipt=None, error_code=None):
        await self.sessions.db.write(
            lambda db: db.execute(
                """UPDATE actions SET status=?,receipt_json=?,error_code=?,updated_at=? WHERE id=? AND status='executing'""",
                (
                    status,
                    json.dumps(receipt) if receipt is not None else None,
                    error_code,
                    self.sessions.clock(),
                    aid,
                ),
            )
        )

    @staticmethod
    def _result(aid, status, receipt=None):
        result = {"action_id": aid, "status": status}
        if status in ("executing", "uncertain"):
            return 202, {**result, "retry_after_ms": 1000}
        if receipt is not None:
            result["receipt"] = receipt
        return 200, result

    async def cancel(self, principal, sid, aid):
        state, row = await self._claim(principal, sid, aid, cancel=True)
        if state != "cancelled":
            raise Problem(409, "action_conflict", "当前状态无法取消。")
        await self.tracing.end(Span("action.cancel", sid, row["turn_id"], aid))
        return self._result(aid, state)

    async def confirm(self, principal, sid, aid, revalidate: Callable):
        # Claim and completion share the same shielded task, including if the caller disconnects at CAS.
        task = asyncio.create_task(self._confirm(principal, sid, aid, revalidate))
        self.inflight.add(task)

        def done(finished):
            self.inflight.discard(finished)
            if not finished.cancelled():
                finished.exception()

        task.add_done_callback(done)
        return await asyncio.shield(task)

    async def _confirm(self, principal, sid, aid, revalidate):
        state, row = await self._claim(principal, sid, aid, cancel=False)
        if state == "executing":
            return self._result(aid, state)
        if state not in ("pending", "uncertain", "succeeded"):
            raise Problem(409, "action_conflict", "当前动作已结束，请查看最新预览。")
        handler = self.handlers[row["kind"]]
        action = {
            **public_action(row),
            "expected_version": row["expected_version"],
            "payload": json.loads(row["payload_json"]),
            "target_kind": row["target_kind"],
            "recovering": state == "uncertain",
        }
        root = Span("action.confirm", sid, row["turn_id"], aid)
        settled = False
        try:
            receipt = None
            if state in ("uncertain", "succeeded"):
                receipt = await self._lookup(handler, principal, aid, root)
                if receipt is None and state == "succeeded":
                    raise Problem(503, "receipt_unavailable", "暂时无法核对已写入的回执。", True)
            if receipt is None:
                await revalidate()
                work = root.child("business.execute")
                async with asyncio.timeout(5):
                    try:
                        receipt = await handler.execute(principal, action)
                    finally:
                        await self.tracing.end(work, "ok" if receipt is not None else "error")
            if not isinstance(receipt, dict) or receipt.get("operation_id") != aid:
                raise Problem(503, "invalid_receipt", "业务回执未能匹配原操作。", True)
            if state != "succeeded":
                await self._settle(aid, "succeeded", receipt)
                settled = True
            await revalidate()
        except Exception as exc:
            error = (
                exc
                if isinstance(exc, Problem)
                else Problem(503, "business_unavailable", "业务结果待核对。", True)
            )
            await self.tracing.end(root, "error", error.code)
            if state == "succeeded" or settled:
                raise error from None
            terminals = {"write_denied": "denied", "stale_target": "stale", "invalid_input": "invalid"}
            # A late first request may still commit after a lookup. Never overwrite that history with a denial.
            terminal = terminals.get(error.code) if state == "pending" else None
            await self._settle(aid, terminal or "uncertain", error_code=error.code)
            if terminal or error.status in (401, 403, 404) or error.code == "scope_changed":
                raise error from None
            return self._result(aid, "uncertain")
        await self.tracing.end(root)
        return self._result(aid, "succeeded", receipt)

    async def _lookup(self, handler, principal, aid, parent=None):
        span = parent.child("business.lookup") if parent else None
        try:
            async with asyncio.timeout(5):
                result = await handler.lookup(principal, aid)
        except Exception:
            if span:
                await self.tracing.end(span, "error", "lookup_failed")
            raise
        if span:
            await self.tracing.end(span)
        return result

    async def receipts(self, principal, snapshot):
        for action in snapshot["actions"]:
            action["can_confirm"] = action["kind"] in self.handlers
            if action["status"] == "succeeded":
                handler = self.handlers.get(action["kind"])
                if handler is None:
                    raise Problem(503, "receipt_unavailable", "宿主尚未提供回执查询。")
                try:
                    record = await self._lookup(handler, principal, action["action_id"])
                except (TimeoutError, OSError):
                    raise Problem(503, "receipt_unavailable", "暂时无法核对业务回执。", True) from None
                if not record or record.get("operation_id") != action["action_id"]:
                    raise Problem(503, "receipt_unavailable", "暂时无法核对业务回执。", True)
                action["receipt"] = record
        return snapshot

    async def close(self):
        if self.inflight:
            await asyncio.gather(*self.inflight, return_exceptions=True)
