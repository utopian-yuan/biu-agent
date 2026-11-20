"""Decode user-selected documents into immutable, addressable passages."""

import base64
import binascii
import hashlib
import io
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path, PurePath
from uuid import uuid4
from xml.etree import ElementTree

from pydantic import BaseModel, ConfigDict, Field

from ...identity import Problem

MAX_BYTES = 2 * 1024 * 1024
MAX_TEXT = 64000
MAX_FILES = 4
MAX_BODY = MAX_FILES * (4 * ((MAX_BYTES + 2) // 3)) + 65536
SESSION_FILES = 12
TEXT_SUFFIXES = {".txt", ".md", ".csv", ".json", ".log"}
SUFFIXES = TEXT_SUFFIXES | {".docx", ".pdf"}
DOCX_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class Upload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    data: str = Field(min_length=1, repr=False)


def word_text(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        info = archive.getinfo("word/document.xml")
        if info.file_size > 2_000_000:
            raise ValueError
        xml = archive.read(info)
    if b"<!DOCTYPE" in xml.upper():
        raise ValueError
    doc = ElementTree.fromstring(xml)
    return "\n".join("".join(t.text or "" for t in p.iter(DOCX_NS + "t")) for p in doc.iter(DOCX_NS + "p"))


def pdf_text(raw):
    try:
        proc = subprocess.run(
            [sys.executable, "-I", str(Path(__file__).with_name("pdf_reader.py"))],
            input=raw,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=8,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise Problem(422, "pdf_timeout", "PDF 解析超时，请拆分文件或上传文本。") from None
    if proc.returncode:
        raise ValueError
    return proc.stdout.decode("utf-8")


def passages(text):
    # Keep paragraphs readable and leave headroom in the SDK's bounded JSON tool response.
    chunks = []
    for block in re.split(r"\n[ \t]*\n", text.replace("\r\n", "\n").replace("\r", "\n")):
        block = block.strip()
        while block:
            end = min(1000, len(block))
            if end < len(block):
                line_break = block.rfind("\n", 0, end)
                if line_break > 0:
                    end = line_break
            chunks.append(block[:end])
            block = block[end:].lstrip()
    return {f"S{i}": part for i, part in enumerate(chunks, 1)}


def decode(upload):
    name = upload.name.strip()
    if not name or re.search(r"[/\\\x00-\x1f\x7f]", name):
        raise Problem(422, "invalid_filename", "请选择带有有效文件名的本地文件。")
    suffix = PurePath(name).suffix.lower()
    if suffix not in SUFFIXES:
        raise Problem(415, "unsupported_file", "支持 TXT、Markdown、CSV、JSON、LOG、DOCX 和文字型 PDF。")
    if len(upload.data) > 4 * ((MAX_BYTES + 2) // 3):
        raise Problem(413, "file_too_large", "每个文件最大 2 MiB。")
    try:
        raw = base64.b64decode(upload.data, validate=True)
    except (ValueError, binascii.Error):
        raise Problem(422, "invalid_file", "文件传输不完整，请重新选择。") from None
    if len(raw) > MAX_BYTES:
        raise Problem(413, "file_too_large", "每个文件最大 2 MiB。")
    try:
        if suffix == ".docx":
            text = word_text(raw)
        elif suffix == ".pdf":
            text = pdf_text(raw)
        else:
            text = raw.decode("utf-8-sig")
        if any(ord(c) < 32 and c not in "\t\r\n\f" for c in text):
            raise ValueError
    except (KeyError, ValueError, OSError, RuntimeError, ElementTree.ParseError, zipfile.BadZipFile):
        raise Problem(
            422, "unreadable_file", "无法读取正文。请使用 UTF-8 文本、标准 DOCX 或可复制文字的 PDF。"
        ) from None
    if not text.strip():
        raise Problem(422, "empty_file", "文件没有可读取的文字。扫描件请先转成文本。")
    if len(text) > MAX_TEXT:
        raise Problem(422, "text_too_long", "每个文件最多 64000 个正文字符，请拆分后上传。")
    sections = passages(text)
    if len(sections) > 256:
        raise Problem(422, "many_sections", "每个文件最多 256 个段落，请合并短段或拆分文件。")
    return {
        "id": "file_" + uuid4().hex[:16],
        "title": name,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "sections": sections,
    }


def store(db, sid, tid, docs):
    if not docs:
        return
    count = db.execute("SELECT count(*) FROM attachments WHERE session_id=?", (sid,)).fetchone()[0]
    if count + len(docs) > SESSION_FILES:
        raise Problem(409, "file_quota", "每段对话最多可附 12 个文件，请新建对话。")
    db.executemany(
        "INSERT INTO attachments(id,session_id,turn_id,title,sha256,sections) VALUES(?,?,?,?,?,?)",
        [
            (d["id"], sid, tid, d["title"], d["sha256"], json.dumps(d["sections"], ensure_ascii=False))
            for d in docs
        ],
    )


def files_through(db, sid, position):
    rows = db.execute(
        """SELECT a.id,a.title,a.sha256,a.sections,a.turn_id FROM attachments a JOIN turns t ON t.id=a.turn_id AND t.session_id=a.session_id
           WHERE a.session_id=? AND t.ordinal<=? ORDER BY t.ordinal,a.rowid""",
        (sid, position),
    )
    return {r["id"]: {**dict(r), "sections": json.loads(r["sections"])} for r in rows}


def attached_names(db, sid, tid):
    return [
        dict(r)
        for r in db.execute(
            "SELECT id,title,sha256 FROM attachments WHERE session_id=? AND turn_id=? ORDER BY rowid",
            (sid, tid),
        )
    ]
