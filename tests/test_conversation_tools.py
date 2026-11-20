"""Exercise each public tool via real AgentScope turns, with fictional session-owned files."""

import json
import sqlite3
from pathlib import Path

import pytest
from agentscope.message import TextBlock
from dotenv import load_dotenv
from fastapi.testclient import TestClient

from biu_agent import Settings
from biu_agent.agents.tools import browser as pages
from biu_agent.agents.tools.conversation import INFO
from biu_agent.identity import Problem
from examples.tickets.app import create_materials
from examples.tickets.teaching_model import TeachingModel
from examples.tickets.workspace import Workspace

from .test_files_and_tools import file
from .test_material_workspace import API, ORIGIN, answer, client_at, login, new_session, save


def invoke(c, session_url, name, **args):
    return answer(c, session_url, name + " " + json.dumps(args, ensure_ascii=False))[0]


def test_all_registered_file_tools_and_pinned_downloads(tmp_path):
    with client_at(tmp_path) as c:
        login(c)
        path = new_session(c)
        cfg = c.get(API + "workspace").json()
        assert set(INFO) <= set(cfg["agents"]["materials"]["tools"]) <= cfg["tools"].keys()
        source = "上传的展签.txt"
        q, _ = answer(c, path, "请核对附件", attachments=[file(source, "蓝签 2 枚\n琥珀 7 枚")])
        attached = f"uploads/{q['attachments'][0]['id']}/{source}"
        files = invoke(c, path, "glob", pattern="**/*.txt")
        assert attached in files["answer_text"] and files["context"]["calls"] == ["glob"]
        listing = invoke(c, path, "glob", pattern="materials/*.md")
        assert "materials/receiving.md" in listing["answer_text"]
        t = invoke(c, path, "read", **{"path": attached, "start_line": 2, "max_lines": 1})
        assert "琥珀" in t["answer_text"] and "蓝签" not in t["answer_text"]
        t = invoke(c, path, "grep", keyword="琥珀", pattern="uploads/**/*.txt")
        assert t["context"]["calls"] == ["grep"] and t["evidence"][0]["title"] == source
        t = invoke(c, path, "bash", **{"path": attached, "command": "grep -F 琥珀 | head -n 1"})
        assert t["context"]["calls"] == ["bash"] and "琥珀 7 枚" in t["answer_text"]
        ref = t["evidence"][0]
        assert (
            c.get(f"{path}/turns/{t['turn_id']}/sources/{ref['id']}/{ref['section']}").json()["text"].strip()
            == "琥珀 7 枚"
        )

        name = "work/展厅/清单.md"
        created = invoke(c, path, "write", **{"path": name, "content": "蓝色 2 枚\n琥珀 7 枚"})
        assert created["outputs"] == [{"path": name, "version": 1}]
        for command, expected in [
            ("pwd", "/workspace"),
            ("ls work/展厅", "清单.md"),
            ("echo '陈列 记录' | wc -w", "2"),
            (f"cat {name} | grep -F 琥珀", "琥珀 7 枚"),
            (f"grep -F 琥珀 {name}", "琥珀 7 枚"),
        ]:
            processed = invoke(c, path, "bash", command=command)
            assert processed["context"]["calls"] == ["bash"] and expected in processed["answer_text"]
            assert processed["evidence"]
        original = f"{path}/turns/{created['turn_id']}/file"
        assert c.get(original, params={"path": name}).text == "蓝色 2 枚\n琥珀 7 枚"
        edited = invoke(
            c, path, "edit", **{"path": name, "old_text": "7 枚", "new_text": "8 枚", "expected_version": 1}
        )
        assert edited["context"]["calls"] == ["edit"] and edited["outputs"][0]["version"] == 2
        assert c.get(original, params={"path": name}).text.endswith("7 枚")
        newer = f"{path}/turns/{edited['turn_id']}/file"
        download = c.get(newer, params={"path": name})
        assert download.text.endswith("8 枚") and "attachment" in download.headers["content-disposition"]
        read = invoke(c, path, "read", **{"path": name})
        ref = read["evidence"][-1]
        source_url = f"{path}/turns/{read['turn_id']}/sources/{ref['id']}/{ref['section']}"
        assert c.get(source_url).json()["text"] == "琥珀 8 枚"
        early, _ = answer(c, path, source=created["turn_id"])
        assert "write" not in early["context"]["tools"] and early["outputs"] == created["outputs"]
        with sqlite3.connect(tmp_path / "core.sqlite3") as db:
            assert db.execute("select count(*) from work_versions").fetchone()[0] == 2
        other = new_session(c)
        assert name not in invoke(c, other, "glob")["answer_text"]
        assert c.get(original.replace(path, other), params={"path": name}).status_code == 404
        login(c, credentials={"username": "另一位读者", "password": "fictional-workspace-pw"})
        assert c.get(newer, params={"path": name}).status_code == 404 and c.get(source_url).status_code == 404
    with client_at(tmp_path) as c:
        login(c, False)
        assert c.get(newer, params={"path": name}).text.endswith("8 枚")
        assert c.post(path + "/delete", json={}).is_success
        assert c.get(newer, params={"path": name}).status_code == 404


def test_browser_reads_page_text_links_and_turn_bound_evidence(tmp_path, monkeypatch):
    calls = []

    def fixture(url):
        calls.append(url)
        if url.endswith("/details"):
            return url, "text/plain", "虚构展架有两扇门。"
        body = "<html><head><title>展签手册</title></head><body><p>虚构展架有六层。</p><p hidden>内部占位</p><a href='/details'>展架细节</a><a href='https://elsewhere.example/'>外站</a><script>ignore text</script></body></html>"
        return url, "text/html", body

    monkeypatch.setattr(pages, "fetch", fixture)
    with client_at(tmp_path) as c:
        login(c)
        path = new_session(c, "receiving")
        t = invoke(c, path, "browser")
        assert "biu://materials/receiving" in t["answer_text"] and "acceptance" not in t["answer_text"]
        local = invoke(c, path, "browser", url="biu://materials/receiving", find="导向牌")
        assert local["context"]["calls"] == ["browser"] and local["evidence"][0]["section"] == "R2"
        t = invoke(c, path, "browser", url="https://example.org/guide", find="六层")
        assert (
            "虚构展架" in t["answer_text"]
            and "内部占位" not in t["answer_text"]
            and "ignore text" not in t["answer_text"]
        )
        assert calls == ["https://example.org/guide"]
        ref = t["evidence"][0]
        row = c.get(f"{path}/turns/{t['turn_id']}/sources/{ref['id']}/{ref['section']}").json()
        assert row["url"] == "https://example.org/guide" and row["text"] == "虚构展架有六层。"

        class ReadLinkedPage(TeachingModel):
            def exercise(self, question, available, results):
                if not results:
                    return self.call("browser", url="https://example.org/guide")
                value = results[-1][1]
                if len(results) == 1:
                    assert value["links"] == ["https://example.org/details"]
                    return self.call("browser", url=value["links"][0])
                return TextBlock(text=value["items"][0]["text"] + " [" + value["refs"][0] + "]")

        monkeypatch.setattr(Workspace, "model", lambda *_: ReadLinkedPage())
        linked, _ = answer(c, path, "帮我读 https://example.org/guide？请继续看页面列出的展架细节。")
        assert linked["context"]["calls"] == ["browser", "browser"] and "两扇门" in linked["answer_text"]
        assert calls[-2:] == ["https://example.org/guide", "https://example.org/details"]
        cfg = c.get(API + "workspace").json()
        assert save(c, "tools", cfg["tools"]["browser"], enabled=False).is_success
        assert c.get(path).status_code == 409
    reader = pages.TextBrowser("请打开 https://example.org/guide")
    assert pages.granted_urls("[手册](https://example.org/guide(v1))。") == {"https://example.org/guide(v1)"}
    with pytest.raises(Problem, match="当前问题"):
        reader.open("https://example.org/elsewhere")
    with pytest.raises(Problem):
        pages.connect_public(("127.0.0.1", 443), 1)


def test_atomic_work_files_failures_and_original_turn_cutoff(tmp_path, monkeypatch):
    with client_at(tmp_path) as c:
        login(c)
        path = new_session(c)
        name = "work/样品.txt"
        initial = invoke(c, path, "write", **{"path": name, "content": "旧版\n旧版"})
        for args in [
            {"path": name, "old_text": "旧版", "new_text": "新版"},
            {"path": name, "old_text": "缺失", "new_text": "新版"},
            {
                "path": name,
                "old_text": "旧版",
                "new_text": "新版",
                "replace_all": True,
                "expected_version": 8,
            },
        ]:
            res = invoke(c, path, "edit", **args)
            assert not res["outputs"]
        earlier = invoke(c, path, "read", **{"path": name})
        newer = invoke(
            c, path, "edit", **{"path": name, "old_text": "旧版", "new_text": "新版", "replace_all": True}
        )
        replay, _ = answer(c, path, source=earlier["turn_id"])
        assert "旧版" in replay["answer_text"] and "新版" not in replay["answer_text"]
        assert newer["outputs"][0]["version"] == 2
        assert not invoke(c, path, "write", **{"path": "materials/receiving.md", "content": "另存"})[
            "outputs"
        ]
        assert not invoke(c, path, "write", **{"path": "../other.txt", "content": "另存"})["outputs"]
        assert not invoke(c, path, "write", **{"path": name + "/另一层", "content": "另存"})["outputs"]
        denied = invoke(c, path, "bash", **{"path": name, "command": "sort --version"})
        assert not denied["evidence"] and "支持" in denied["answer_text"]

        class FaultAfterWrite(TeachingModel):
            def exercise(self, question, available, results):
                if results:
                    return TextBlock(text="无效出处 [missing#X1]")
                return super().exercise(question, available, results)

        monkeypatch.setattr(Workspace, "model", lambda *_: FaultAfterWrite())
        bad, _ = answer(c, path, 'write {"path":"work/未完成.txt","content":"草稿"}', success=False)
        assert bad["status"] == "failed" and not bad["outputs"]
        with sqlite3.connect(tmp_path / "core.sqlite3") as db:
            assert db.execute("select count(*) from work_versions").fetchone()[0] == 2
        assert c.get(f"{path}/turns/{initial['turn_id']}/file", params={"path": name}).text == "旧版\n旧版"


def test_file_tools_respect_material_grants_and_generated_file_grants(tmp_path):
    with client_at(tmp_path) as c:
        login(c)
        cfg = c.get(API + "workspace").json()
        assert save(c, "tools", cfg["tools"]["read_material"], enabled=False).is_success
        path = new_session(c)
        assert "materials/receiving.md" not in invoke(c, path, "glob")["answer_text"]
        for tool_name, arguments in [
            ("read", {"path": "materials/receiving.md"}),
            ("grep", {"keyword": "导向牌"}),
            ("bash", {"command": "cat materials/receiving.md"}),
            ("browser", {"url": "biu://materials/receiving"}),
        ]:
            result = invoke(c, path, tool_name, **arguments)
            assert not result["evidence"] and "待安排检查" not in result["answer_text"]
        new = invoke(c, path, "write", **{"path": "work/归档.txt", "content": "可见的归档"})
        url = f"{path}/turns/{new['turn_id']}/file"
        assert c.get(url, params={"path": "work/归档.txt"}).text == "可见的归档"
        assert save(c, "tools", cfg["tools"]["write"], enabled=False).is_success
        assert c.get(url, params={"path": "work/归档.txt"}).status_code == 409
        attempt = invoke(c, new_session(c), "write", **{"path": "work/归档.txt", "content": "新"})
        assert "write" not in attempt["context"]["tools"] and not attempt["outputs"]


def test_upgrade_adds_seven_tools_and_preserves_custom_agent_and_switches(tmp_path):
    previous = json.dumps(["list_materials", "read_material", "search_materials", "shell"])
    with client_at(tmp_path) as c:
        login(c)
    with sqlite3.connect(tmp_path / "workspace.sqlite3") as db:
        db.executemany("DELETE FROM resources WHERE kind='tools' AND id=?", [(k,) for k in INFO])
        db.execute(
            "UPDATE resources SET data=json_set(data,'$.tools',json(?)), revision=2 WHERE kind='agents'",
            (previous,),
        )
        db.execute(
            "UPDATE resources SET data=json_set(data,'$.prompt','仅练习') WHERE kind='agents' AND id='receiving'"
        )
        db.execute(
            "UPDATE resources SET data=json_set(data,'$.enabled',json('false')) WHERE kind='tools' AND id='shell'"
        )
    for _ in range(2):
        with client_at(tmp_path) as c:
            login(c, False)
            cfg = c.get(API + "workspace").json()
            assert set(cfg["agents"]["materials"]["tools"]) == set(json.loads(previous)) | set(INFO)
            assert cfg["agents"]["materials"]["revision"] == 3
            assert cfg["agents"]["receiving"]["tools"] == json.loads(previous)
            assert not cfg["tools"]["shell"]["enabled"]


@pytest.mark.live
def test_configured_model_writes_edits_and_processes_one_workfile(tmp_path):
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    settings = Settings.from_env()
    settings.data_dir = tmp_path
    settings.turn_seconds = 120
    with TestClient(create_materials(settings), base_url=ORIGIN, headers={"origin": ORIGIN}) as c:
        login(c)
        path = new_session(c, model="environment")
        name = "work/展签.txt"
        prompts = [
            ("write", f"请调用 write，在 {name} 写入两行纯文本，第一行为蓝签 2 枚，第二行为琥珀 7 枚。"),
            ("edit", f"请调用 edit，把 {name} 中的「琥珀 7 枚」改为「琥珀 8 枚」。"),
            ("bash", f"请调用 bash，运行 cat {name} | grep -F 琥珀，回答命中的那行并引用工具返回的出处。"),
        ]
        for tool_name, prompt in prompts:
            turn, _ = answer(c, path, prompt)
            assert tool_name in turn["context"]["calls"], turn["context"]["calls"]
            if tool_name in {"write", "edit"}:
                expected_qty, version = (7, 1) if tool_name == "write" else (8, 2)
                assert turn["outputs"] == [{"path": name, "version": version}]
                actual = c.get(f"{path}/turns/{turn['turn_id']}/file", params={"path": name})
                assert f"琥珀 {expected_qty} 枚" in actual.text
            else:
                assert "琥珀 8 枚" in turn["answer_text"]
                assert any(row["id"].startswith("bash_") for row in turn["evidence"])
            print(turn["model_label"], turn["context"]["calls"])


@pytest.mark.live
def test_public_page_via_workspace_browser(tmp_path):
    with client_at(tmp_path) as c:
        login(c)
        path = new_session(c)
        url = "https://www.rfc-editor.org/rfc/rfc2606.txt"
        phrase = "Reserved Top Level DNS Names"
        turn = invoke(c, path, "browser", url=url, find=phrase)
        assert turn["context"]["calls"] == ["browser"] and turn["evidence"], turn["answer_text"]
        ref = turn["evidence"][0]
        saved = c.get(f"{path}/turns/{turn['turn_id']}/sources/{ref['id']}/{ref['section']}").json()
        assert saved["url"] == url and phrase in saved["text"]
