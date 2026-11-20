"""Tool registry and the original section-based material tools."""

import asyncio
import shutil
import subprocess
from typing import Literal

from pydantic import Field

from ...identity import Problem
from ...tools import Inputs, tool
from .conversation import INFO as WORKSPACE_TOOLS

TOOL_INFO = {
    "list_materials": ("列出资料", "查看本轮获准使用的文件及段号。"),
    "read_material": ("读取段落", "按文件和段号读取原文，可核对版本。"),
    "search_materials": ("搜索资料", "按关键词定位当前可见资料中的段落。"),
    "shell": ("Shell 文本工具", "在授权段落上运行 wc 统计、head 预览或 grep 固定文本匹配。"),
    **WORKSPACE_TOOLS,
}


class Search(Inputs):
    keyword: str = Field(min_length=1, max_length=80)
    id: str | None = Field(default=None, min_length=1, max_length=48)


class Shell(Inputs):
    command: Literal["wc", "head", "grep"]
    id: str = Field(min_length=1, max_length=48)
    section: str = Field(min_length=1, max_length=12)
    lines: int = Field(default=5, ge=1, le=20)
    keyword: str = Field(default="", max_length=80)


def material_tools(documents, read, on_read):
    async def search_materials(keyword, id=None):
        hits = []
        for doc in documents(id):
            for section, value in doc["sections"].items():
                if keyword.casefold() in value.casefold():
                    hits.append({"id": doc["id"], "section": section})
                    if len(hits) == 25:
                        return {"matches": hits, "limit": 25}
        return {"matches": hits, "limit": 25}

    async def shell(command, id, section, lines=5, keyword=""):
        row = read(id, section)
        # The text is stdin. Only these executables and their specified options can be selected.
        binary = shutil.which(command, path="/usr/bin:/bin")
        if not binary:
            raise Problem(503, "command_unavailable", "本机没有安装所选文本命令。")
        if command == "grep" and (not keyword.strip() or any(c in keyword for c in "\x00\r\n")):
            raise Problem(422, "missing_keyword", "grep 需要单行固定关键词。")
        args = {"wc": ["-l", "-w", "-c"], "head": ["-n", str(lines)], "grep": ["-F", "-e", keyword]}
        try:
            out = await asyncio.to_thread(
                subprocess.run,
                [binary, *args[command]],
                input=row["text"],
                capture_output=True,
                timeout=2,
                check=False,
                encoding="utf-8",
                env={"LC_ALL": "C"},
            )
        except (OSError, subprocess.TimeoutExpired):
            raise Problem(503, "command_failed", "文本命令未完成。") from None
        if out.returncode and not (command == "grep" and out.returncode == 1):
            raise Problem(422, "command_failed", "文本命令返回错误。")
        return {
            **{k: v for k, v in row.items() if k != "text"},
            "command": command,
            "output": out.stdout.rstrip(),
        }

    return [
        tool(
            search_materials,
            Search,
            "按固定关键词在已授权资料中搜索，返回匹配的文件 ID 和段号，再按需读取原文。",
        ),
        tool(
            shell,
            Shell,
            "对一个已授权段落执行 wc（换行符、词和字节数）、head（前几行）、grep（固定关键词）。结果附出处。",
            on_result=on_read,
        ),
    ]
