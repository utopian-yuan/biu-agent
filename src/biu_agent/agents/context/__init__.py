"""Assemble the question and bounded conversation history for one model turn."""

import json

from agentscope.message import AssistantMsg, UserMsg


def messages_for(ctx, *, questions_only):
    if questions_only and ctx.history:
        record = {"先前问题": [q for q, _ in ctx.history], "当前问题": ctx.input_text}
        content = "以下是用户的问题记录。先前问题仅供理解指代。\n" + json.dumps(record, ensure_ascii=False)
        return [UserMsg("user", content)]
    messages = []
    for question, answer in ctx.history:
        messages.extend([UserMsg("user", question), AssistantMsg("biu", answer)])
    return [*messages, UserMsg("user", ctx.input_text)]
