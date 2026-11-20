"""Discover bundled SKILL.md files and bind account-selected instructions to AgentScope."""

import re
from importlib.resources import files

import yaml
from agentscope.skill import Skill

from ...identity import Problem

SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def unpack(markdown):
    """Accept the front matter and body of a single declarative SKILL.md."""
    source = markdown.lstrip("\ufeff").replace("\r\n", "\n").strip()
    match = re.fullmatch(r"---[ \t]*\n(.*?)\n---[ \t]*\n(.+)", source, re.DOTALL)
    if not match or len(source) > 12000:
        raise Problem(422, "invalid_skill", "SKILL.md 需要 YAML 文件头、name、description 和正文。")
    try:
        header = yaml.safe_load(match[1])
    except (yaml.YAMLError, RecursionError):
        header = None
    if not isinstance(header, dict):
        raise Problem(422, "invalid_skill", "技能文件头格式无效。")
    name = header.get("name")
    description = header.get("description")
    body = match[2].strip()
    if not isinstance(name, str) or len(name) > 64 or not SLUG.fullmatch(name):
        raise Problem(422, "invalid_skill", "技能标识应由小写英文、数字和连字符组成，最多 64 字符。")
    meta = header.get("metadata") or {}
    title = meta.get("display_name", name) if isinstance(meta, dict) else name
    if (
        not isinstance(title, str)
        or not 1 <= len(title.strip()) <= 64
        or not isinstance(description, str)
        or not 1 <= len(description.strip()) <= 200
        or not 1 <= len(body) <= 6000
    ):
        raise Problem(422, "invalid_skill", "请检查技能的名称、用途与正文长度。")
    return name, {"name": title.strip(), "description": description.strip(), "content": body, "enabled": True}


def bundled():
    entries = {}
    for folder in sorted(files(__package__).iterdir(), key=lambda f: f.name):
        manifest = folder.joinpath("SKILL.md")
        if folder.is_dir() and manifest.is_file():
            slug, data = unpack(manifest.read_text(encoding="utf-8"))
            if folder.name != slug or slug in entries:
                raise ValueError("技能目录名须与 SKILL.md 中的唯一标识一致。")
            entries[slug] = data
    return entries


def export(row):
    header = {"name": row["id"], "description": row["description"], "metadata": {"display_name": row["name"]}}
    return (
        "---\n"
        + yaml.safe_dump(header, allow_unicode=True, sort_keys=False)
        + "---\n\n"
        + row["content"]
        + "\n"
    )


def bind(rows, selected):
    return [
        Skill(k, rows[k]["description"], "", rows[k]["content"], float(rows[k]["revision"]))
        for k in selected
        if rows[k]["enabled"]
    ]
