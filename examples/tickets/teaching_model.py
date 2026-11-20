"""Offline exercise with real SDK tools; the model never opens the source files."""

import json
import re
from uuid import uuid4

from agentscope.credential import OpenAICredential
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import TextBlock, ToolCallBlock, ToolResultBlock
from agentscope.model import ChatModelBase, ChatResponse

from biu_agent.agents.tools.conversation import INFO


class TeachingModel(ChatModelBase):
    def __init__(self):
        super().__init__(
            OpenAICredential(api_key="teaching-fixture"),
            "教学演示",
            self.Parameters(),
            stream=False,
            max_retries=0,
        )
        self.formatter = OpenAIChatFormatter()

    @staticmethod
    def call(name, **args):
        return ToolCallBlock(id=uuid4().hex, name=name, input=json.dumps(args, ensure_ascii=False))

    def read_or_process(self, catalog, question, available):
        attached = [r for r in catalog if r.get("attachment")]
        named = [r for r in attached if r["title"] in question]
        rows = named or [r for r in attached if r["current"]] or attached[::-1] or catalog
        if not rows:
            return TextBlock(text="当前范围内没有可读取的材料。")
        section = rows[0]["sections"][0]
        command = re.search(r"\b(wc|head|grep)\b", question, re.I)
        keyword = re.search(r"[「“\"]([^」”\"]{1,80})[」”\"]", question)
        if command and "shell" in available:
            return self.call(
                "shell",
                command=command[1].lower(),
                id=rows[0]["id"],
                section=section,
                keyword=keyword[1] if keyword else "",
            )
        if keyword and "search_materials" in available and re.search(r"搜索|查找|检索", question):
            return self.call(
                "search_materials", keyword=keyword[1], **({"id": rows[0]["id"]} if named else {})
            )
        # Cover the first passage in each selected attachment before moving on to its next passage.
        groups = [
            [f"S{i}" for i in range(1, r["count"] + 1)] if r.get("attachment") else r["sections"]
            for r in rows
        ]
        targets = [
            (r, sections[i])
            for i in range(max(map(len, groups)))
            for r, sections in zip(rows, groups)
            if i < len(sections)
        ]
        return [self.call("read_material", id=r["id"], section=sec) for r, sec in targets[:6]]

    def exercise(self, question, available, results):
        # An explicit tool name and JSON object make each offline lesson reproducible.
        match = re.fullmatch(r"/?([a-z]+)(?:\s+(\{.*\}))?", question.strip(), re.DOTALL)
        if not match or match[1] not in INFO:
            return None
        name = match[1]
        if name not in available:
            return TextBlock(text="本轮未启用 " + name + "。请检查智能体的工具设置。")
        if not results:
            try:
                args = json.loads(match[2] or "{}")
                if not isinstance(args, dict):
                    raise ValueError
            except ValueError:
                return TextBlock(text="请在工具名后提供 JSON 对象。")
            # Construct directly so a provider argument cannot shadow the tool's name.
            return ToolCallBlock(id=uuid4().hex, name=name, input=json.dumps(args, ensure_ascii=False))
        value = results[-1][1]
        if not isinstance(value, dict):
            return TextBlock(text=str(value))
        if "error" in value:
            return TextBlock(text=value["error"]["message"])
        if name in {"write", "edit"}:
            return TextBlock(text=f"文件 {value['path']} 已保存为第 {value['version']} 版。")
        if name == "glob":
            output = "\n".join(value["paths"]) or "没有匹配的文件。"
        elif name == "bash":
            output = (value["output"].strip() or "（无输出）") + " [" + value["refs"][0] + "]"
        else:
            lines = [f"{row['text']} [{row['source']}]" for row in value.get("items", [])]
            for item in value.get("links", []):
                lines.append(item if isinstance(item, str) else item["title"] + " · " + item["url"])
            output = "\n".join(lines) or "没有匹配的内容。"
        return TextBlock(text=output)

    async def _call_api(self, model_name, messages, tools=None, **kwargs):
        available = {t["function"]["name"] for t in tools or []}
        question = max(i for i, msg in enumerate(messages) if msg.role == "user")
        results = [
            b for msg in messages[question + 1 :] for b in msg.content if isinstance(b, ToolResultBlock)
        ]
        parsed = []
        for block in results:
            raw = (
                block.output
                if isinstance(block.output, str)
                else "".join(b.text for b in block.output if isinstance(b, TextBlock))
            )
            try:
                parsed.append((block.name, json.loads(raw)))
            except ValueError:
                parsed.append((block.name, raw))
        user_text = messages[question].get_text_content() or ""
        if user_text.startswith("以下是用户的问题记录。"):
            user_text = json.loads(user_text.split("\n", 1)[1])["当前问题"]
        exercise = self.exercise(user_text, available, parsed)
        if exercise is not None:
            return ChatResponse(content=[exercise], is_last=True)
        instruction = "\n".join(m.get_text_content() or "" for m in messages if m.role == "system")
        skill = re.search(r"使用 Skill 读取 ([a-zA-Z0-9_-]+)。", instruction)
        if skill and "Skill" in available and not results:
            block = self.call("Skill", skill=skill.group(1))
        elif "list_materials" in available and not any(n == "list_materials" for n, _ in parsed):
            block = self.call("list_materials")
        elif (
            parsed
            and parsed[-1][0] == "list_materials"
            and isinstance(parsed[-1][1], list)
            and "read_material" in available
        ):
            block = self.read_or_process(parsed[-1][1], user_text, available)
        elif parsed and parsed[-1][0] == "search_materials" and isinstance(parsed[-1][1], dict):
            hits = parsed[-1][1].get("matches", [])
            block = [self.call("read_material", **hit) for hit in hits[:6]] or TextBlock(
                text="当前资料中没有找到这个关键词。"
            )
        else:
            facts = [r for _, r in parsed if isinstance(r, dict) and "source" in r]
            lines = []
            for fact in facts:
                if "command" in fact:
                    label = {"wc": "换行符、词数和字节数", "head": "段落开头", "grep": "匹配行"}[
                        fact["command"]
                    ]
                    value = label + " · " + (fact["output"].strip() or "无匹配")
                else:
                    value = fact["text"]
                lines.append(f"{value} [{fact['source']}]")
            answer = "\n".join(lines)
            passages = {row["source"]: row.get("text", "") for row in facts}
            if facts and all(r["id"] in {"purchase", "receiving"} and "text" in r for r in facts):
                answer += "\n现有可见段落缺少验收依据。"
            elif "仅含样品灯" in passages.get("acceptance#A2", ""):
                answer += "\n这份验收说明只覆盖样品灯，导向牌的验收结论仍缺少依据。"
            block = TextBlock(text=answer or "本轮没有读取到可核对的段落，请检查资料范围与工具。")
        return ChatResponse(content=block if isinstance(block, list) else [block], is_last=True)
