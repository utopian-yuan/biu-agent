"""The seven file, text processing and browsing tools visible in the workspace."""

import asyncio
import fnmatch
import hashlib
import json
from functools import cached_property, lru_cache

from pydantic import Field

from ...identity import Problem, hidden
from ...tools import Inputs, tool
from .bash import pipeline
from .browser import TextBrowser
from .workfiles import work_path

INFO = {
    "bash": (
        "Bash 工作区",
        "在会话目录使用 pwd、ls、echo、cat、head、tail、grep、sort、uniq、wc，可用管道组合。",
    ),
    "read": ("读取文件", "按行读取来源、附件或当前对话生成的文件。"),
    "write": ("写入文件", "在当前对话创建或更新文本文件，可按版本下载。"),
    "edit": ("编辑文件", "在会话文件内定位并替换原文，保留上一个版本。"),
    "glob": ("匹配文件名", "使用 *、? 或 ** 按路径查找当前可见的文件。"),
    "grep": ("查找文本", "按关键词在可见文件中查找匹配行和出处。"),
    "browser": ("浏览网页", "阅读本地材料页或问题中指定的公开 HTTPS 网页，支持查词、翻页和同站链接。"),
}


class FilePath(Inputs):
    path: str = Field(min_length=1, max_length=240)


class Read(FilePath):
    start_line: int = Field(default=1, ge=1)
    max_lines: int = Field(default=20, ge=1, le=50)


class Write(FilePath):
    content: str = Field(max_length=64000)
    expected_version: int | None = Field(default=None, ge=0)


class Edit(FilePath):
    old_text: str = Field(min_length=1, max_length=64000)
    new_text: str = Field(max_length=64000)
    replace_all: bool = False
    expected_version: int | None = Field(default=None, ge=0)


class Glob(Inputs):
    pattern: str = Field(default="**/*", min_length=1, max_length=240)
    offset: int = Field(default=0, ge=0)


class Grep(Glob):
    keyword: str = Field(min_length=1, max_length=120)
    case_sensitive: bool = False


class Bash(Inputs):
    command: str = Field(min_length=1, max_length=400)
    path: str | None = Field(default=None, min_length=1, max_length=240)


class Browser(Inputs):
    url: str = Field(default="biu://materials/", min_length=1, max_length=1500)
    find: str = Field(default="", max_length=120)
    start_line: int = Field(default=1, ge=1)
    max_lines: int = Field(default=12, ge=1, le=50)


def match_path(path, pattern):
    names, parts = path.split("/"), pattern.split("/")

    @lru_cache(maxsize=None)
    def match(i, j):
        if j == len(parts):
            return i == len(names)
        if parts[j] == "**":
            return match(i, j + 1) or (i < len(names) and match(i + 1, j))
        return i < len(names) and fnmatch.fnmatchcase(names[i], parts[j]) and match(i + 1, j + 1)

    return match(0, 0)


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def generated_document(path, version, body):
    pieces = [line[i : i + 800] for line in body.splitlines() for i in range(0, max(1, len(line)), 800)]
    checksum = digest(body)
    return {
        "id": "work_" + digest(path + ":" + checksum)[:20],
        "title": path,
        "sha256": checksum,
        "sections": {f"L{i}": value for i, value in enumerate(pieces, 1)},
        "body": body,
        "version": version,
    }


def line_rows(doc):
    index = 0
    for section, text in doc["sections"].items():
        ref = {
            "id": doc["id"],
            "title": doc["title"],
            "sha256": doc["sha256"],
            "section": section,
            "source": doc["id"] + "#" + section,
            "text": text,
        }
        if "url" in doc:
            ref["url"] = doc["url"]
        for line in text.splitlines() or [""]:
            for i in range(0, max(1, len(line)), 800):
                index += 1
                yield index, line[i : i + 800], ref


def size(data):
    return len(json.dumps(data, ensure_ascii=False, separators=(",", ":")))


class ConversationTools:
    def __init__(self, ctx, documents, record):
        self.work = ctx.work
        self.documents = documents
        self.record = record
        self.web = TextBrowser(ctx.input_text)
        self.refs = {}

    @cached_property
    def originals(self):
        return {
            ("uploads/" + d["id"] + "/" + d["title"] if "turn_id" in d else "materials/" + d["id"] + ".md"): d
            for d in self.documents()
        }

    def paths(self):
        return sorted(self.originals.keys() | self.work.paths())

    def document(self, path):
        if path.startswith(("materials/", "uploads/")):
            doc = self.originals.get(path)
            if not doc:
                raise hidden()
            return doc
        path = work_path(path)
        version, body = self.work.get(path)
        return generated_document(path, version, body)

    def observe(self, result):
        for ref in result.get("refs", []):
            row = self.refs[ref]
            self.record(row)
            if row["id"].startswith(("work_", "web_", "bash_")):
                self.work.passages[ref] = row

    def page(self, matches, offset, limit, **meta):
        result = {**meta, "items": [], "refs": [], "next_offset": None}
        for i, (item, ref) in enumerate(matches):
            if i < offset:
                continue
            candidate = [*result["items"], item]
            refs = list(dict.fromkeys([*result["refs"], ref["source"]]))
            if len(candidate) > limit or size({**result, "items": candidate, "refs": refs}) > 3850:
                result["next_offset"] = i
                break
            self.refs[ref["source"]] = ref
            result.update(items=candidate, refs=refs)
        return result

    @staticmethod
    def lines(doc, start, keyword=""):
        for n, text, ref in line_rows(doc):
            if n >= start and (not keyword or keyword.casefold() in text.casefold()):
                yield {"line": n, "text": text, "source": ref["source"]}, ref

    def read_page(self, doc, start, limit, keyword="", **meta):
        # Cursor is a source line number, including when matches are sparse.
        result = self.page(self.lines(doc, start, keyword), 0, limit, **meta)
        more = result.pop("next_offset") is not None
        result["next_line"] = result["items"][-1]["line"] + 1 if more and result["items"] else None
        return result

    def build(self):
        async def read(path, start_line=1, max_lines=20):
            doc = self.document(path)
            return self.read_page(doc, start_line, max_lines, path=path, version=doc.get("version"))

        async def write(path, content, expected_version=None):
            return self.work.stage(path, content, expected_version)

        async def edit(path, old_text, new_text, replace_all=False, expected_version=None):
            path = work_path(path)
            _, body = self.work.get(path)
            count = body.count(old_text)
            if not count or (count > 1 and not replace_all):
                raise Problem(409, "edit_match", "请提供唯一匹配的原文，或明确选择全部替换。")
            updated = body.replace(old_text, new_text, -1 if replace_all else 1)
            return {
                **self.work.stage(path, updated, expected_version),
                "replacements": count if replace_all else 1,
            }

        async def glob(pattern="**/*", offset=0):
            names = [p for p in self.paths() if match_path(p, pattern)]
            result = []
            for path in names[offset:]:
                if len(result) == 25 or size([*result, path]) > 3500:
                    break
                result.append(path)
            cursor = offset + len(result)
            return {"paths": result, "next_offset": cursor if cursor < len(names) else None}

        async def grep(keyword, pattern="**/*", case_sensitive=False, offset=0):
            needle = keyword if case_sensitive else keyword.casefold()

            def matched():
                for path in self.paths():
                    if match_path(path, pattern):
                        for n, text, ref in line_rows(self.document(path)):
                            if needle in (text if case_sensitive else text.casefold()):
                                yield {"path": path, "line": n, "text": text, "source": ref["source"]}, ref

            return self.page(matched(), offset, 20)

        async def bash(command, path=None):
            output = await asyncio.to_thread(pipeline, command, path, self.paths, self.document)
            shown = output[:1600]
            text = shown or "（无输出）"
            fingerprint = digest(output)
            key = "bash_" + digest(command + "\n" + (path or "") + "\n" + fingerprint)[:20]
            ref = key + "#R1"
            self.refs[ref] = {
                "source": ref,
                "id": key,
                "section": "R1",
                "title": "Bash · " + command[:70],
                "sha256": fingerprint,
                "text": text,
            }
            return {"output": shown, "truncated": len(shown) < len(output), "refs": [ref]}

        async def browser(url="biu://materials/", find="", start_line=1, max_lines=12):
            local = "biu://materials/"
            if url == local:
                docs = self.originals.values()
                return {
                    "title": "当前对话的资料目录",
                    "links": [{"title": d["title"], "url": local + d["id"]} for d in docs],
                }
            if url.startswith(local):
                doc = next((d for d in self.originals.values() if local + d["id"] == url), None)
                if not doc:
                    raise hidden()
                return self.read_page(doc, start_line, max_lines, find, title=doc["title"], url=url)
            doc, links = await asyncio.to_thread(self.web.open, url)
            shown = []
            for link in links:
                if size([*shown, link]) > 700:
                    break
                shown.append(link)
            self.web.allowed.update(shown)
            return self.read_page(
                doc, start_line, max_lines, find, title=doc["title"], url=doc["url"], links=shown
            )

        return [
            tool(
                bash,
                Bash,
                INFO["bash"][1] + " 例如 ls work/ 或 cat work/清单.txt | sort。也可通过 path 指定标准输入。",
                on_result=self.observe,
            ),
            tool(read, Read, INFO["read"][1] + " 路径先用 glob 查找，结果含出处。", on_result=self.observe),
            tool(
                write, Write, INFO["write"][1] + " 目标为 work/ 相对路径，本轮成功后保存。", read_only=False
            ),
            tool(
                edit,
                Edit,
                INFO["edit"][1] + " old_text 默认必须唯一，可传 expected_version。",
                read_only=False,
            ),
            tool(glob, Glob, INFO["glob"][1] + " 返回 materials/、uploads/ 和 work/ 下的路径。"),
            tool(
                grep,
                Grep,
                INFO["grep"][1] + " keyword 为固定文本，pattern 是路径通配符。",
                on_result=self.observe,
            ),
            tool(
                browser,
                Browser,
                INFO["browser"][1] + " 默认 biu://materials/，可用 next_line 翻页。",
                on_result=self.observe,
            ),
        ]
