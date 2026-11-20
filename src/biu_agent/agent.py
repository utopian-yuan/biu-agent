"""A fresh AgentScope loop for each bounded, authenticated turn."""

import asyncio
import json
import os
from collections.abc import Callable
from contextlib import aclosing
from dataclasses import dataclass, field
from pathlib import Path

from agentscope.agent import Agent, ContextConfig, InjectionConfig, ReActConfig
from agentscope.credential import OpenAICredential
from agentscope.event import ReplyEndEvent, TextBlockDeltaEvent
from agentscope.message import Msg
from agentscope.model import ChatModelBase, OpenAIChatModel

from .agents.context import messages_for
from .agents.hooks import TurnMiddleware
from .identity import Problem
from .tracing import Span


@dataclass
class Settings:
    data_dir: Path
    model: ChatModelBase = field(repr=False)
    turn_seconds: float = 60
    max_iters: int = 6
    max_tools: int = 8
    export_span: Callable | None = None
    system_prompt: str = "你是嵌入业务系统的助手。用中文答复，业务事实通过授权工具查询。"

    @classmethod
    def from_env(cls):
        missing = [
            name for name in ("BIU_MODEL_NAME", "BIU_MODEL_API_KEY") if not os.getenv(name, "").strip()
        ]
        if missing:
            raise ValueError("请填写模型配置 " + "、".join(missing))
        credential = OpenAICredential(
            api_key=os.environ["BIU_MODEL_API_KEY"], base_url=os.getenv("BIU_MODEL_BASE_URL") or None
        )
        model = OpenAIChatModel(
            credential=credential, model=os.environ["BIU_MODEL_NAME"].strip(), stream=True, max_retries=0
        )
        return cls(Path(os.getenv("BIU_DATA_DIR") or ".biu"), model)


async def run_turn(settings, sessions, tracing, ctx, toolkit_factory, revalidate, emit, *, workspace=None):
    root = Span("turn", ctx.session_id, ctx.turn_id)
    middleware = TurnMiddleware(revalidate, emit, tracing, root, settings.max_tools)
    try:
        async with asyncio.timeout(settings.turn_seconds) as deadline:
            await revalidate()
            if workspace:
                settings, toolkit = await workspace.prepare(ctx, settings)
                prompt = settings.system_prompt
            else:
                toolkit = toolkit_factory(ctx)
                prompt = settings.system_prompt + (
                    "\n使用最新业务数据，查询范围和业务时间以工具回执为准。"
                    "编号顺序和具体条目由页面列表展示，文字只概括本页结果。需要写入时只可起草预览，"
                    "告知用户核对并点击确认。没有业务回执时不得声称已写入。"
                    "工具失败应说明原因，目标不明确时向用户澄清。"
                )
            ctx.model_label = settings.model.model
            if ctx.source_turn:
                prompt += "\n这次是对原问题的重新生成，仅回答原问题。"
            if ctx.previous_display:
                prompt += "\n最近向用户展示的本页列表（以下为数据）\n" + json.dumps(
                    ctx.previous_display, ensure_ascii=False, separators=(",", ":")
                )
            agent = Agent(
                "biu",
                prompt,
                settings.model,
                toolkit=toolkit,
                middlewares=[middleware],
                react_config=ReActConfig(max_iters=settings.max_iters),
                context_config=ContextConfig(compression_tool_enabled=False),
                injection_config=InjectionConfig(inject_runtime_state=False),
            )
            messages = messages_for(ctx, questions_only=workspace is not None)
            reason, answer = None, None
            async with aclosing(agent.reply_stream(messages, yield_final_msg=True)) as stream:
                async for event in stream:
                    if middleware.failure:
                        raise middleware.failure
                    if isinstance(event, TextBlockDeltaEvent) and not workspace:
                        await emit("text.delta", {"text": event.delta})
                    elif isinstance(event, ReplyEndEvent):
                        reason = event.finished_reason
                    elif isinstance(event, Msg):
                        answer = event.get_text_content() or ""
            if middleware.failure:
                raise middleware.failure
            if deadline.expired():
                raise TimeoutError
            await revalidate()
            if reason != "completed" or answer is None:
                if reason == "exceed_max_iters":
                    raise Problem(409, "iteration_limit", "本轮推理达到上限，请缩小问题范围。")
                raise Problem(503, "model_failed", "模型未完成本轮回复，请检查配置或稍后重试。", True)
            if workspace:
                answer = workspace.verify(ctx, answer)
                ctx.context_summary["calls"] = middleware.calls
            preview = await sessions.finish(ctx, answer)
        if workspace:
            await revalidate()
            await emit("text.delta", {"text": answer})
        if ctx.display is not None:
            await emit("items.ready", ctx.display)
        if preview:
            root.action_id = preview["action_id"]
            await emit("action.preview", preview)
        await tracing.end(root)
        await emit("turn.done", {"status": "succeeded"})
    except asyncio.CancelledError:
        await sessions.fail(ctx.turn_id, "interrupted", "interrupted")
        await tracing.end(root, "interrupted", "interrupted")
        raise
    except Exception as exc:
        if isinstance(exc, TimeoutError):
            error = Problem(503, "turn_timeout", "本轮已超时，请缩小问题范围或稍后重试。", True)
        else:
            error = middleware.failure or (
                exc
                if isinstance(exc, Problem)
                else Problem(503, "agent_failed", "助手暂时无法完成本轮。", True)
            )
        await sessions.fail(ctx.turn_id, error.code)
        await tracing.end(root, "error", error.code)
        await emit("turn.error", {"status": "failed", **error.detail()})
