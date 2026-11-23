"""Keep the observable lesson outputs in sync with the SDK and the material workspace."""

import re
import sqlite3
import subprocess
import sys
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from textwrap import dedent

import pytest
from agentscope.message import ToolCallBlock
from agentscope.permission import PermissionBehavior, PermissionDecision
from pydantic import ValidationError

from biu_agent.identity import Problem
from tutorials.s03_permissions.code import OWNER, Note, consume_approval, model_view, request_note
from tutorials.s04_hooks.code import ReviewGate, append_once, open_ledger

ROOT = Path(__file__).resolve().parents[1]


def run_lesson(lesson, args):
    result = subprocess.run(
        [sys.executable, "-m", f"tutorials.{lesson}.code", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize(
    ("lesson", "args", "expected"),
    [
        ("s01_agent_loop", [], ["Result(call_id='read-1'", "[receiving#R2]"]),
        ("s01_agent_loop", ["--steps", "1"], ["Result(call_id='read-1'", "1 次模型决策已用完"]),
        (
            "s01_agent_loop",
            ["--engine", "manual", "--scope", "empty"],
            ["completed，模型调用 2 次", "没有可读取的材料"],
        ),
        ("s02_tools", [], ["必填参数 ['id', 'section']", "登记的出处 ['receiving#R2']"]),
        ("s02_tools", ["--extra"], ["工具参数无效", "登记的出处 []"]),
        ("s02_tools", ["--section", "R9"], ["not_found", "登记的出处 []"]),
        (
            "s02_tools",
            ["--call", "read_material", '{"id":"acceptance","section":"A2"}'],
            ["not_found", "登记的出处 []"],
        ),
        ("s02_tools", ["--call", "archive_material", "{}"], ["ToolNotFoundError", "登记的出处 []"]),
        (
            "s02_tools",
            ["--call", "list_sections", '{"id":"receiving","cursor":"wrong"}'],
            ["invalid_cursor", "登记的出处 []"],
        ),
        ("s02_tools", ["--report"], ["清单已读完", "登记的出处 ['receiving#R1', 'receiving#R2']"]),
        (
            "s02_tools",
            ["--report", "--max-pages", "1"],
            ["回执 read-1-R1 success", "清单未读完", "登记的出处 ['receiving#R1']"],
        ),
        (
            "s02_tools",
            ["--approval-demo"],
            [
                "复核前 缺少匹配的批准",
                "已批原稿 已登记，回执 1",
                "重复提交 复用回执 1",
                "改动正文 缺少匹配的批准",
                "更换对象 缺少匹配的批准",
                "待发队列回读 1 条 [(1, '展具组'",
                "[receiving#R2]')]",
                "登记的出处 ['receiving#R2']",
            ],
        ),
        ("s03_permissions", ["--profile-only"], ["目录 ['receiving']", "404 not_found", "原会话可继续"]),
        (
            "s03_permissions",
            ["--review", "approve"],
            ["读取 P-104 DENY", "等待时台账 0 条", "恢复 1 已写入", "恢复 2 not_found", "台账回读 1 条"],
        ),
        (
            "s03_permissions",
            ["--review", "edited"],
            [
                "读取 P-104 DENY",
                "等待时台账 0 条",
                "恢复 1 review_changed",
                "恢复 2 not_found",
                "台账回读 0 条",
            ],
        ),
        (
            "s03_permissions",
            ["--review", "revoked"],
            ["读取 P-104 DENY", "等待时台账 0 条", "恢复 1 not_found", "恢复 2 not_found", "台账回读 0 条"],
        ),
        (
            "s03_permissions",
            ["--review", "restored"],
            [
                "读取 P-104 DENY",
                "等待时台账 0 条",
                "恢复 1 scope_changed",
                "恢复 2 not_found",
                "台账回读 0 条",
            ],
        ),
        (
            "s03_permissions",
            ["--review", "expired"],
            ["读取 P-104 DENY", "等待时台账 0 条", "恢复 1 expired", "恢复 2 not_found", "台账回读 0 条"],
        ),
        (
            "s03_permissions",
            ["--review", "policy"],
            [
                "读取 P-104 DENY",
                "等待时台账 0 条",
                "恢复 1 review_changed",
                "恢复 2 not_found",
                "台账回读 0 条",
            ],
        ),
        (
            "s03_permissions",
            ["--review", "refused"],
            [
                "读取 P-104 DENY",
                "等待时台账 0 条",
                "恢复 1 not_approved",
                "恢复 2 not_found",
                "台账回读 0 条",
            ],
        ),
        (
            "s04_hooks",
            ["--sdk", "approve"],
            ["权限检查 ask ASK", "等待时台账 0 条", "权限检查 allow ASK", "台账回读 1 条 [('P-101'"],
        ),
        (
            "s04_hooks",
            ["--sdk", "revoked"],
            ["权限检查 ask ASK", "等待时台账 0 条", "权限检查 allow DENY", "台账回读 0 条 []"],
        ),
        (
            "s04_hooks",
            ["--sdk", "unrecorded"],
            ["权限检查 ask ASK", "等待时台账 0 条", "执行入口拒绝 not_approved", "台账回读 0 条 []"],
        ),
        (
            "s04_hooks",
            ["--sdk", "project-ask"],
            ["权限检查 allow ASK", "等待时台账 0 条", "权限检查 allow ASK", "台账回读 1 条 [('P-101'"],
        ),
        (
            "s04_hooks",
            ["--sdk", "framework-deny"],
            ["权限检查 deny ASK", "框架已拒绝，本轮没有待办", "台账回读 0 条 []"],
        ),
        ("s04_hooks", ["--fault", "before"], ["前置检查失败", "台账回读 0 条"]),
        (
            "s04_hooks",
            ["--fault", "after"],
            ["已写入，审计待补 0", "审计回读 [('note-1',)]", "台账回读 1 条"],
        ),
        ("s04_hooks", ["--fault", "timeout"], ["结果待确认", "原操作回执 ('P-101'", "台账回读 1 条"]),
        (
            "s05_skill_loading",
            ["--binding", "disabled", "--verify", "granted"],
            ["初始技能目录 （空）", "本轮工具 ['read_material']", "资料回执 success", "['receiving#R2']"],
        ),
        (
            "s05_skill_loading",
            ["--binding", "enabled", "--verify", "revoked"],
            ["取回正文 success", "读说明后的出处 []", "资料回执 error", "读取资料后的出处 []"],
        ),
        ("s05_skill_loading", ["--binding", "unbound"], ["初始技能目录 （空）", "本轮工具 []"]),
        (
            "s06_context",
            ["--history-window"],
            [
                "原问题4之前 ['问题1', '问题2', '问题3']",
                "后来只取问题 ['问题3', '问题4', '问题5', '问题6', '问题7', '问题8']",
                "所选回答版本 ['修订答4', '原答5']",
                "后来连同回答 ['问题7', '问题8']",
            ],
        ),
        ("s06_context", ["--case", "no-citation"], ["本轮实读 receiving#R2", "拒绝发布 missing_citation"]),
        ("s06_context", ["--case", "stale-version"], ["本轮实读 receiving#R2", "拒绝发布 source_changed"]),
        (
            "s06_context",
            ["--case", "wrong-claim"],
            ["本轮实读 receiving#R2", "发布 导向牌已通过验收。 [receiving#R2]"],
        ),
        (
            "s07_memory",
            ["--isolation", "--forget"],
            ["偏好表回读 [('灯光组', 'u-7', '段落')]", "另一租户的同号账号"],
        ),
        ("s07_memory", ["--update"], ["偏好表回读 [('展具组', 'u-7', '段落')]", "小舟\n收货点"]),
        ("s07_memory", ["--once"], ["偏好表回读 []", "小舟\n收货点"]),
        ("s07_memory", ["--revoked"], ["当前格式 表格，原文不可读 not_found", "青禾\n收货点"]),
        ("s08_cron", ["--clock", "utc"], ["该分钟已经完成", "实际报告数 1"]),
        ("s08_cron", ["--clock", "next-day"], ["实际报告数 2", "回读 2032-05-19T09:00+08:00"]),
        ("s08_cron", ["--clock", "missed"], ["未到触发分钟", "实际报告数 0"]),
        ("s08_cron", ["--clock", "concurrent"], ["该分钟已经完成", "实际报告数 1"]),
        (
            "s09_mcp",
            ["--annotations", "missing"],
            ["服务端声明 ['edition', 'submit']", "绑定到本轮 []", "服务端实际进入 0 次"],
        ),
        (
            "s09_mcp",
            ["--annotations", "conflict"],
            ["服务端声明 ['edition', 'submit']", "绑定到本轮 []", "服务端实际进入 0 次"],
        ),
        (
            "s09_mcp",
            ["--remote-deny"],
            ["绑定到本轮 ['edition']", "mcp__ext_lesson__edition error", "服务端实际进入 1 次"],
        ),
        (
            "s10_subagent",
            ["--handoff", "outside"],
            ["子 acceptance#A2", "父任务复核未通过 child_source_outside"],
        ),
        (
            "s10_subagent",
            ["--handoff", "mismatch"],
            ["子 acceptance#A2", "父任务复核未通过 child_version_changed"],
        ),
        ("s10_subagent", ["--handoff", "revoked"], ["子 acceptance#A2", "父任务复核未通过 not_found"]),
        ("s10_subagent", ["--keyword", "归档"], ["父 子任务未找到说明验收范围的段落"]),
        ("s10_subagent", ["--keyword", "通电"], ["子 acceptance#A1", "父 已对两台样品灯完成通电"]),
        (
            "lab_readonly_adapter",
            [],
            ["回执 list-1 success", "回执 read-1 success", "已登记出处 ['acceptance#A2']"],
        ),
        (
            "lab_readonly_adapter",
            ["--case", "limited"],
            [
                "回执 list-1 success",
                "回执 read-1 error",
                "not_found",
                "('/excerpt', '小舟')",
                "已登记出处 []",
            ],
        ),
        (
            "lab_readonly_adapter",
            ["--case", "revoke-after-list"],
            [
                "回执 list-1 success",
                "回执 read-1 error",
                "not_found",
                "('/excerpt', '小舟')",
                "已登记出处 []",
            ],
        ),
        (
            "lab_readonly_adapter",
            ["--case", "expired-session"],
            [
                "回执 list-1 success",
                "回执 read-1 error",
                "session_expired",
                "('/excerpt', '未认证')",
                "已登记出处 []",
            ],
        ),
        ("s03_permissions", [], ["acceptance#A2", "原会话可继续"]),
        ("s03_permissions", ["--revoke"], ["目录 ['receiving']", "404 not_found", "409 scope_changed"]),
        ("s04_hooks", [], ["read-1 start", "read-1 end", "Result(call_id='read-1'"]),
        ("s04_hooks", ["--budget", "0"], ["预算耗尽", "工具执行次数为 0"]),
        ("s04_hooks", ["--section", "R9"], ["read-1 start", "read-1 error", "不可见"]),
        ("s05_skill_loading", [], ["初始技能目录 source-review", "取回正文 success", "先看目录"]),
        ("s05_skill_loading", ["--name", "missing"], ["取回正文 error", "SkillNotFoundError"]),
        ("s06_context", [], ["样品灯验收了吗？", "那导向牌呢？", "发布 收货点", "[receiving#R2]"]),
        ("s06_context", ["--reuse-citation"], ["那导向牌呢？", "拒绝发布 unverified_source"]),
        ("s07_memory", [], ["小舟\n| 出处 | 原文 |", "青禾\n收货点"]),
        ("s07_memory", ["--forget"], ["小舟\n收货点", "青禾\n收货点"]),
        ("s08_cron", [], ["09:00 收货点", "09:00 该分钟已经完成", "实际报告数 1"]),
        ("s08_cron", ["--fail-first"], ["本次事务回滚", "09:00 收货点", "实际报告数 1"]),
        ("s08_cron", ["--schedule", "1 9 * * *"], ["09:00 未到触发分钟", "09:01 收货点", "实际报告数 1"]),
        (
            "s09_mcp",
            [],
            [
                "服务端声明 ['edition', 'submit']",
                "发现的只读工具 ['edition']",
                "绑定到本轮 ['edition']",
                "远端回执 lookup-1 mcp__ext_lesson__edition success 春季目录第三版",
                "服务端实际进入 1 次 ['edition']",
            ],
        ),
        (
            "s09_mcp",
            ["--select", "submit"],
            ["发现的只读工具 ['edition']", "绑定到本轮 []", "服务端实际进入 0 次 []"],
        ),
        ("s10_subagent", [], ["子任务收到", "子 acceptance#A2", "父 本次验收记录的范围仅含样品灯。"]),
        ("s10_subagent", ["--receiving-only"], ["授权 []", "父 当前资料范围内缺少验收依据。"]),
        ("s10_subagent", ["--child-steps", "1"], ["子 Result(", "父 子任务达到步数上限"]),
    ],
)
def test_lesson(lesson, args, expected):
    output = run_lesson(lesson, args)
    remaining = output
    for fragment in expected:
        _, match, remaining = remaining.partition(fragment)
        assert match, (fragment, output)


@pytest.mark.parametrize("engine", ["manual", "agentscope"])
def test_feedback_trace(engine):
    output = run_lesson("s01_agent_loop", ["--engine", engine, "--trace"])
    assert re.findall(r"输入中有 (\d+) 份工具回执", output) == ["0", "1", "5"]
    requested = re.findall(r"^  请求 (\S+) (\S+) ", output, re.MULTILINE)
    assert [name for _, name in requested] == ["list_materials"] + ["read_material"] * 4
    assert len({call_id for call_id, _ in requested}) == 5
    last_input, ending = output.split("模型调用 3，", 1)[1].split("结束原因 ", 1)
    received = re.findall(r"^  收到 (\S+) (\S+) success$", last_input, re.MULTILINE)
    assert sorted(received) == sorted(requested)
    assert ending.startswith("completed，模型调用 3 次")
    assert set(re.findall(r"\[([^\]]+)\]", ending)) == {
        "receiving#R1",
        "receiving#R2",
        "acceptance#A1",
        "acceptance#A2",
    }
    assert "导向牌的验收结论仍缺少依据" in ending


@pytest.mark.parametrize(
    ("engine", "reason", "calls"), [("manual", "budget_exhausted", 1), ("agentscope", "exceed_max_iters", 2)]
)
def test_limited_loop_has_no_candidate(engine, reason, calls):
    output = run_lesson("s01_agent_loop", ["--engine", engine, "--steps", "1"])
    assert f"结束原因 {reason}，模型调用 {calls} 次" in output
    assert "候选答复" not in output


@pytest.mark.parametrize(
    "lesson", sorted(path.parent.name for path in (ROOT / "tutorials").glob("*/code.py"))
)
def test_readme_quotes_linked_source(lesson):
    folder = ROOT / "tutorials" / lesson
    source = (folder / "code.py").read_text(encoding="utf-8").splitlines()
    readme = (folder / "README.md").read_text(encoding="utf-8")
    excerpts = re.findall(
        r"^\[[^\]\n]+\]\(code\.py#L(\d+)-L(\d+)\)\n\n```python\n(.*?)\n```$",
        readme,
        re.MULTILINE | re.DOTALL,
    )
    assert len(excerpts) == readme.count("```python\n") > 0
    for first, last, quoted in excerpts:
        assert 1 <= int(first) <= int(last) <= len(source)
        assert dedent("\n".join(source[int(first) - 1 : int(last)])) == quoted, (lesson, first, last)


def test_model_can_only_receive_authorized_project_fields():
    assert model_view(OWNER, {"P-101"}, "P-101") == {
        "id": "P-101",
        "name": "设备台账整理",
        "progress": "待补验收资料",
    }
    for actor, project in [(OWNER, "P-104"), (replace(OWNER, tenant_id="灯光组"), "P-101")]:
        with pytest.raises(Problem) as denied:
            model_view(actor, {"P-101"}, project)
        assert denied.value.code == "not_found"


def test_only_the_original_owner_and_call_can_consume_an_approval():
    inbox = {}
    grants = {"P-101"}
    raw = {"project": "P-101", "text": "  请核对设备。  "}
    with pytest.raises(ValidationError):
        request_note(inbox, OWNER, grants, "c1", {**raw, "approved": True}, now=0, policy_version=1)
    assert inbox == {}
    actor = replace(OWNER, credential="临时会话")
    item = request_note(inbox, actor, grants, "c1", raw, now=0, policy_version=1)
    assert item.note.text == "请核对设备。"
    assert item.owner.credential == ""
    for actor, call in [
        (replace(OWNER, user_id="另一个账号"), "c1"),
        (replace(OWNER, tenant_id="灯光组"), "c1"),
        (OWNER, "c2"),
    ]:
        with pytest.raises(Problem) as denied:
            consume_approval(
                inbox,
                item.operation_id,
                call,
                actor,
                grants,
                item.note,
                now=1,
                policy_version=1,
                approved=True,
            )
        assert denied.value.code == "not_found" and item.operation_id in inbox
    assert (
        consume_approval(
            inbox, item.operation_id, "c1", OWNER, grants, item.note, now=299, policy_version=1, approved=True
        )
        == item.note
    )
    assert inbox == {}


@pytest.mark.parametrize(
    ("framework", "expected"),
    [("allow", ["allow", "ask", "deny"]), ("ask", ["ask", "ask", "deny"]), ("deny", ["deny"] * 3)],
)
async def test_new_call_combines_framework_and_project_rules(framework, expected):
    gate = ReviewGate()

    async def check_framework(**kwargs):
        return PermissionDecision(behavior=PermissionBehavior(framework), message="框架规则")

    observed = []
    for tool, project in [("read", "P-101"), ("append_note", "P-101"), ("read", "P-104")]:
        call = ToolCallBlock(id="c1", name=tool, input="{}")
        inputs = {"tool_call": call, "tool_input": {"project": project}}
        combined = await gate.on_check_permission(None, inputs, check_framework)
        observed.append(combined.behavior.value)
    assert observed == expected


def test_business_receipt_replays_only_the_same_note():
    with closing(sqlite3.connect(":memory:")) as db:
        open_ledger(db)
        note = Note(project="P-101", text="核对 A18")
        original = append_once(db, "one", note)
        assert append_once(db, "one", note) == original
        with pytest.raises(Problem) as clash:
            append_once(db, "one", Note(project="P-101", text="核对 A19"))
        assert clash.value.code == "operation_conflict"
        assert db.execute("SELECT project,body FROM notes").fetchall() == [("P-101", "核对 A18")]
