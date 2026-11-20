"""Authorize and trace each model and tool invocation."""

from contextlib import aclosing

from agentscope.middleware import MiddlewareBase
from agentscope.model import ChatResponse

from ...identity import Problem
from ...tracing import active_tool


class TurnMiddleware(MiddlewareBase):
    def __init__(self, revalidate, emit, tracing, root, limit):
        self.revalidate = revalidate
        self.emit = emit
        self.tracing = tracing
        self.root = root
        self.remaining = limit
        self.failure = None
        self.calls = []

    async def check(self):
        try:
            await self.revalidate()
        except Problem as exc:
            self.failure = exc
            raise

    async def on_model_call(self, agent, input_kwargs, next_handler):
        await self.check()
        span = self.root.child("model.call")
        span.model_name = input_kwargs["current_model"].model

        def usage(response):
            if response.usage is not None:
                span.input_tokens = response.usage.input_tokens
                span.output_tokens = response.usage.output_tokens

        try:
            response = await next_handler(**input_kwargs)
        except Exception:
            await self.tracing.end(span, "error", "model_error")
            raise
        if isinstance(response, ChatResponse):
            usage(response)
            await self.tracing.end(span)
            return response

        async def stream():
            complete = False
            try:
                async with aclosing(response):
                    async for chunk in response:
                        usage(chunk)
                        complete = chunk.is_last
                        yield chunk
            finally:
                await self.tracing.end(
                    span, "ok" if complete else "error", None if complete else "model_interrupted"
                )

        return stream()

    async def on_acting(self, agent, input_kwargs, next_handler):
        await self.check()
        name = input_kwargs["tool_call"].name
        self.calls.append(name)
        if name != "Skill":
            if self.remaining <= 0:
                self.failure = Problem(409, "tool_limit", "本轮工具调用达到上限，请缩小问题范围。")
                raise self.failure
            self.remaining -= 1
        span = self.root.child("tool." + name)
        await self.emit("tool.status", {"name": name, "phase": "start"})
        ok = False
        try:
            async with aclosing(next_handler(**input_kwargs)) as execution:
                while True:
                    token = active_tool.set((self.tracing, span))
                    try:
                        chunk = await anext(execution)
                    except StopAsyncIteration:
                        break
                    finally:
                        active_tool.reset(token)
                    ok = chunk.state not in ("error", "denied", "interrupted")
                    yield chunk
        finally:
            await self.tracing.end(span, "ok" if ok else "error", None if ok else "tool_error")
            await self.emit("tool.status", {"name": name, "phase": "end" if ok else "error"})
