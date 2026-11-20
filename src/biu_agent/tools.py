"""Expose explicit schemas and bounded, user-authorized operations to the SDK."""

import json

from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import FunctionTool, ToolChunk
from pydantic import BaseModel, ConfigDict, ValidationError

from .identity import Problem


class Inputs(BaseModel):
    model_config = ConfigDict(extra="forbid")


def tool(func, args, description, *, read_only=True, on_result=None):
    async def guarded(**raw):
        try:
            # AgentScope exposes the schema to the provider. Validate arguments again at the application boundary.
            data = args.model_validate(raw)
            result = await func(**data.model_dump())
            text = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
            if len(text) > 4000:
                raise Problem(422, "tool_output_limit", "返回内容过长，请缩小范围。")
            if on_result:
                on_result(result)
            return text
        except ValidationError:
            return ToolChunk(
                content=[TextBlock(text="工具参数无效，请核对名称、范围和长度。")],
                state=ToolResultState.ERROR,
            )
        except Problem as exc:
            return ToolChunk(
                content=[TextBlock(text=json.dumps({"error": exc.detail()}, ensure_ascii=False))],
                state=ToolResultState.ERROR,
            )

    return FunctionTool(
        guarded,
        name=func.__name__,
        description=description,
        input_schema=args,
        is_read_only=read_only,
        is_concurrency_safe=False,
        permission=PermissionDecision(
            behavior=PermissionBehavior.ALLOW, message="宿主已按本轮权限装配此工具。"
        ),
    )
