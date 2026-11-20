"""Resolve only registered source IDs. Each excerpt identifies the exact file revision."""

import hashlib
from pathlib import Path

from pydantic import Field

from biu_agent.identity import hidden
from biu_agent.tools import Inputs

FILES = {"purchase": "采购清单", "receiving": "到货记录", "acceptance": "验收说明"}
DATA = Path(__file__).parent / "data"


class Passage(Inputs):
    id: str = Field(min_length=1, max_length=48)
    section: str = Field(min_length=1, max_length=12)


def document(doc, allowed, uploads=None):
    if uploads and doc in uploads:
        return uploads[doc]
    if doc not in FILES or doc not in allowed:
        raise hidden()
    raw = (DATA / (doc + ".md")).read_bytes()
    sections = dict(
        line.split(" ", 1) for line in raw.decode().splitlines() if line and not line.startswith("#")
    )
    return {"id": doc, "title": FILES[doc], "sha256": hashlib.sha256(raw).hexdigest(), "sections": sections}


def catalog(allowed):
    result = []
    for key in FILES:
        if key in allowed:
            row = document(key, allowed)
            result.append({**row, "sections": list(row["sections"])})
    return result


def excerpt(doc, section, allowed, uploads=None):
    row = document(doc, allowed, uploads)
    if section not in row["sections"]:
        raise hidden()
    return {
        "source": doc + "#" + section,
        "id": doc,
        "section": section,
        "title": row["title"],
        "sha256": row["sha256"],
        "text": row["sections"][section],
    }
