"""Exercise attached fictional documents, their scoped tools, and portable SKILL.md files."""

import base64
import io
import sqlite3
import zipfile
from uuid import uuid4

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from biu_agent.agents.context.attachments import MAX_BODY, MAX_BYTES
from biu_agent.db import Database
from biu_agent.identity import Principal
from biu_agent.session import Sessions

from .test_material_workspace import API, answer, client_at, login, new_session, save


def file(name, contents):
    raw = contents.encode() if isinstance(contents, str) else contents
    return {"name": name, "data": base64.b64encode(raw).decode("ascii")}


def test_files_follow_the_question_through_revisions_and_permissions(tmp_path):
    with client_at(tmp_path) as c:
        login(c)
        path = new_session(c)
        rid = str(uuid4())
        doc = file("展台交接.md", "折页柜共有四层。\n\n清点人是虚构角色阿蒲。")
        first, _ = answer(c, path, "请核对此附件", request_id=rid, attachments=[doc])
        assert [r["title"] for r in first["attachments"]] == ["展台交接.md"]
        assert "四层" in first["answer_text"] and len(first["evidence"]) == 2
        fid = first["attachments"][0]["id"]
        body = {"request_id": rid, "text": "请核对此附件", "attachments": [doc]}
        assert c.post(path + "/turns", json=body).json()["turn_id"] == first["turn_id"]
        assert c.post(path + "/turns", json={**body, "attachments": []}).status_code == 409
        second, _ = answer(c, path, "这份文件呢", attachments=[file("月台.txt", "蓝色刻度为七格。")])
        assert "七格" in second["answer_text"] and "展台交接.md" in second["context"]["sources"]
        follow, _ = answer(c, path, "展台交接.md 说了什么？")
        assert "四层" in follow["answer_text"] and not follow["attachments"]
        regenerated, _ = answer(c, path, source=first["turn_id"])
        assert regenerated["root_turn_id"] == first["turn_id"] and regenerated["ordinal"] == first["ordinal"]
        assert (
            regenerated["attachments"] == first["attachments"]
            and "月台.txt" not in regenerated["context"]["sources"]
        )
        assert [r["root_turn_id"] for r in c.get(path).json()["turns"]] == [
            first["turn_id"],
            second["turn_id"],
            follow["turn_id"],
        ]
        original = f"{path}/turns/{regenerated['turn_id']}/sources/{fid}/S1"
        assert c.get(original).json()["text"] == "折页柜共有四层。"
        other = new_session(c)
        other_turn, _ = answer(c, other, "读附件")
        assert "展台交接.md" not in other_turn["context"]["sources"]
        assert c.get(f"{other}/turns/{other_turn['turn_id']}/sources/{fid}/S1").status_code == 404
        login(c, credentials={"username": "虚构乙", "password": "fictional-other-account"})
        assert c.get(original).status_code == 404
        assert c.post(path + "/turns", json={**body, "request_id": str(uuid4())}).status_code == 404
        login(c, False)
        agent = c.get(API + "workspace").json()["agents"]["materials"]
        assert save(c, "agents", agent, allow_uploads=False).is_success
        assert c.get(original).status_code == 409
        assert c.post(new_session(c) + "/turns", json=body).status_code == 403


def test_shell_search_and_skill_import_export_use_real_bound_tools(tmp_path):
    manifest = "---\nname: exhibit-review\ndescription: 核对展签\nmetadata:\n  display_name: 展签核对\n---\n\n按位置与颜色逐项回答。\n"
    with client_at(tmp_path) as c:
        login(c)
        assert c.post(API + "skills/import", json={"markdown": manifest}).json()["id"] == "exhibit-review"
        exported = c.get(API + "skills/exhibit-review/export")
        assert "attachment" in exported.headers["content-disposition"] and "按位置与颜色" in exported.text
        assert c.post(API + "skills/import", json={"markdown": exported.text}).status_code == 409
        assert c.post(API + "skills/import", json={"markdown": "没有文件头"}).status_code == 422
        cfg = c.get(API + "workspace").json()
        assert {"search_materials", "shell"} <= cfg["tools"].keys()
        assert save(c, "agents", cfg["agents"]["materials"], skills=["exhibit-review"]).is_success
        path = new_session(c)
        turn, _ = answer(c, path, "用 wc 统计本段", attachments=[file("展签.txt", "blue lamp\nred flag")])
        assert turn["context"]["skills"] == ["展签核对"] and "Skill" in turn["context"]["calls"]
        assert "shell" in turn["context"]["calls"] and turn["evidence"][0]["title"] == "展签.txt"
        assert "1 4 18" in " ".join(turn["answer_text"].split())
        for question, expected in [("head 预览", "blue lamp"), ("grep 查「red」", "red flag")]:
            t, _ = answer(c, path, question)
            assert "shell" in t["context"]["calls"] and expected in t["answer_text"]
        match, _ = answer(c, path, "搜索「red」")
        assert {"search_materials", "read_material"} <= set(
            match["context"]["calls"]
        ) and "red flag" in match["answer_text"]
        assert save(c, "skills", cfg["skills"]["exhibit-review"], enabled=False).is_success
        assert save(c, "tools", cfg["tools"]["shell"], enabled=False).is_success
        restricted, _ = answer(c, new_session(c))
        assert "shell" not in restricted["context"]["tools"] and "Skill" not in restricted["context"]["calls"]
        assert not restricted["context"]["skills"]
        login(c, credentials={"username": "另一位作者", "password": "fictional-second-skill"})
        assert c.get(API + "skills/exhibit-review/export").status_code == 404
        assert c.post(API + "skills/import", json={"markdown": exported.text}).is_success


def make_pdf(contents=True):
    writer = PdfWriter()
    page = writer.add_blank_page(width=220, height=100)
    if contents:
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 12 50 Td (Amber marker 45.) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
    data = io.BytesIO()
    writer.write(data)
    return data.getvalue()


def test_docx_pdf_limits_and_atomic_file_validation(tmp_path):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>松木展架</w:t></w:r></w:p></w:body></w:document>',
        )
    with client_at(tmp_path) as c:
        login(c)
        path = new_session(c)
        files = [file("展架.docx", archive.getvalue()), file("标牌.pdf", make_pdf())]
        first, _ = answer(c, path, "逐项读附件", attachments=files)
        assert "松木展架" in first["answer_text"] and "Amber marker 45." in first["answer_text"]
        bad = [
            (file("图片.png", b"img"), 415),
            (file("损坏.txt", b"\xff"), 422),
            (file("扫描.pdf", make_pdf(False)), 422),
            (file("坏.docx", "x"), 422),
            (file("空.txt", " \n"), 422),
            (file("过长.txt", "x" * 65000), 422),
            (file("过大.txt", b"x" * (MAX_BYTES + 1)), 413),
            (file("x.log", b"x\x01"), 422),
        ]
        for invalid, status in bad:
            payload = {"request_id": str(uuid4()), "text": "看看文件", "attachments": [files[0], invalid]}
            assert c.post(path + "/turns", json=payload).status_code == status
        payload = {"request_id": str(uuid4()), "text": "太多", "attachments": [files[0]] * 5}
        assert c.post(path + "/turns", json=payload).status_code == 422
        assert c.post(path + "/turns", content=b"x" * (MAX_BODY + 1)).status_code == 413
        with sqlite3.connect(tmp_path / "core.sqlite3") as db:
            assert db.execute("select count(*) from attachments").fetchone()[0] == 2
        assert len(c.get(path).json()["turns"]) == 1
        assert c.post(path + "/delete", json={}).is_success
        with sqlite3.connect(tmp_path / "core.sqlite3") as db:
            assert db.execute("select count(*) from attachments").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_revisions_do_not_move_across_history_pages_or_displace_published_answers(tmp_path):
    store = Sessions(Database(tmp_path / "conversation.sqlite3"))
    await store.initialize()
    p = Principal("workspace", "reader", "1", "test-cookie")
    sid = await store.create(p)
    roots = []
    for i in range(23):
        ctx = await store.start(p, sid, str(uuid4()), f"Q{i}", None)
        await store.finish(ctx, f"A{i}")
        roots.append(ctx.turn_id)
    good = await store.start(p, sid, str(uuid4()), "", None, source=roots[1])
    assert good.history == [("Q0", "A0")]
    await store.finish(good, "最新答复")
    retry = await store.start(p, sid, str(uuid4()), "", None, source=good.turn_id)
    await store.fail(retry.turn_id, "model_failed")
    newest = await store.snapshot(p, sid)
    assert [t["root_turn_id"] for t in newest["turns"]] == roots[-20:]
    older = await store.snapshot(p, sid, newest["next_before"])
    assert older["next_before"] is None and [t["root_turn_id"] for t in older["turns"]] == roots[:3]
    slot = older["turns"][1]
    assert slot["turn_id"] == good.turn_id and slot["answer_text"] == "最新答复"
    assert slot["revision_status"] == "failed" and slot["revision_error"] == "model_failed"
    middle = await store.start(p, sid, str(uuid4()), "", None, source=roots[2])
    assert middle.history == [("Q0", "A0"), ("Q1", "A1")]
    await store.finish(middle, "复验")
    newer = await store.start(p, sid, str(uuid4()), "", None, source=roots[20])
    await store.finish(newer, "近期更新")
    final = await store.start(p, sid, str(uuid4()), "继续", None)
    assert ("Q20", "近期更新") in final.history


def test_bundled_library_explicitly_restores_instructions_without_changing_bindings(tmp_path):
    with client_at(tmp_path) as c:
        login(c)
        conf = c.get(API + "workspace").json()
        core = conf["skills"]["source-review"]
        assert save(c, "skills", core, content="我的版本", enabled=False).is_success
        item = c.get(API + "skills/library").json()["items"][0]
        assert not item["matches"] and item["revision"] == 2
        path = API + "skills/library/source-review"
        assert c.post(path, json={"revision": 1}).status_code == 409
        assert c.post(path, json={"revision": item["revision"]}).is_success
        assert c.get(API + "skills/library").json()["items"][0]["matches"]
        actual = c.get(API + "workspace").json()
        assert actual["skills"]["source-review"]["content"] == core["content"]
        assert not actual["skills"]["source-review"]["enabled"]
        assert "source-review" in actual["agents"]["materials"]["skills"]


def test_prior_workspace_tools_migrate_once_without_overriding_user_choices(tmp_path):
    with client_at(tmp_path) as c:
        login(c)
    with sqlite3.connect(tmp_path / "workspace.sqlite3") as db:
        db.execute("DELETE FROM resources WHERE kind='tools' AND id IN ('search_materials','shell')")
        db.execute("""UPDATE resources SET data=json_set(data,'$.tools',json('["list_materials","read_material"]'))
            WHERE kind='agents' AND id='materials'""")
        db.execute("""UPDATE resources SET data=json_set(data,'$.tools',json('["list_materials"]')),revision=2
            WHERE kind='agents' AND id='receiving'""")
    with client_at(tmp_path) as c:
        login(c, False)
        cfg = c.get(API + "workspace").json()
        assert "shell" in cfg["agents"]["materials"]["tools"]
        assert cfg["agents"]["receiving"]["tools"] == ["list_materials"]
        assert save(c, "tools", cfg["tools"]["shell"], enabled=False).is_success
    with client_at(tmp_path) as c:
        login(c, False)
        cfg = c.get(API + "workspace").json()
        assert not cfg["tools"]["shell"]["enabled"]
        assert cfg["agents"]["materials"]["tools"].count("shell") == 1


async def test_preexisting_revision_log_displays_in_place_after_upgrade(tmp_path):
    from biu_agent.session import CONTEXT_SCHEMA, SCHEMA, WORKSPACE_SCHEMA

    db = Database(tmp_path / "core.sqlite3")
    await db.initialize(SCHEMA, migrations=(WORKSPACE_SCHEMA, CONTEXT_SCHEMA))
    with sqlite3.connect(db.path) as con:
        con.execute(
            "INSERT INTO sessions(id,tenant_id,user_id,access_epoch,created_at) VALUES('s','local','u','1',0)"
        )
        con.executemany(
            """INSERT INTO turns(id,session_id,ordinal,request_id,input_text,status,created_at,answer_text,source_turn)
            VALUES(?,'s',?,?,?,'succeeded',0,?,?)""",
            [
                ("first", 1, "r1", "第一问", "原答复", None),
                ("second", 2, "r2", "第二问", "后续答复", None),
                ("revised", 3, "r3", "第一问", "已重写", "first"),
            ],
        )
    sessions = Sessions(db)
    await sessions.initialize()
    snapshot = await sessions.snapshot(Principal("local", "u", "1", "test"), "s")
    assert [(r["ordinal"], r["turn_id"], r["answer_text"]) for r in snapshot["turns"]] == [
        (1, "revised", "已重写"),
        (2, "second", "后续答复"),
    ]
    assert len(list(tmp_path.glob("core.sqlite3.v3-*.bak"))) == 1


@pytest.mark.live
def test_configured_model_reads_one_fictional_attachment_via_shell(tmp_path):
    from pathlib import Path

    from dotenv import load_dotenv
    from fastapi.testclient import TestClient

    from biu_agent import Settings
    from examples.tickets.app import create_materials

    from .test_material_workspace import ORIGIN

    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    settings = Settings.from_env()
    settings.data_dir = tmp_path
    settings.turn_seconds = 120
    with TestClient(create_materials(settings), base_url=ORIGIN, headers={"origin": ORIGIN}) as c:
        login(c)
        path = new_session(c, model="environment")
        t, _ = answer(
            c,
            path,
            "请用 Shell 的 grep 在随附清单第一段查找固定词「琥珀」，只回答匹配的那行和 [文件ID#段号]。",
            attachments=[file("展签练习.txt", "蓝色展签 3 枚\n琥珀展签 7 枚")],
        )
        assert "shell" in t["context"]["calls"] and "琥珀" in t["answer_text"] and "7" in t["answer_text"]
        assert all(r["title"] == "展签练习.txt" for r in t["evidence"])
        ref = t["evidence"][0]
        assert (
            c.get(f"{path}/turns/{t['turn_id']}/sources/{ref['id']}/{ref['section']}").json()["text"]
            == "蓝色展签 3 枚\n琥珀展签 7 枚"
        )
        print(t["model_label"], t["context"]["calls"], "附件引用回查通过")
