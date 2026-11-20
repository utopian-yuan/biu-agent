"""Keep the observable lesson outputs in sync with the SDK and the material workspace."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("lesson", "args", "expected"),
    [
        ("s01_agent_loop", [], ["Result(call_id='read-1'", "[receiving#R2]"]),
        ("s01_agent_loop", ["--steps", "1"], ["Result(call_id='read-1'", "1 次模型决策已用完"]),
        ("s02_tools", [], ["必填参数 ['id', 'section']", "登记的出处 ['receiving#R2']"]),
        ("s02_tools", ["--extra"], ["工具参数无效", "登记的出处 []"]),
        ("s02_tools", ["--section", "R9"], ["not_found", "登记的出处 []"]),
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
        ("s09_mcp", [], ["发现的只读工具 ['edition']", "绑定到本轮 ['edition']", "春季目录第三版"]),
        ("s09_mcp", ["--select", "submit"], ["发现的只读工具 ['edition']", "绑定到本轮 []"]),
        ("s10_subagent", [], ["子任务收到", "子 acceptance#A2", "父 本次验收记录的范围仅含样品灯。"]),
        ("s10_subagent", ["--receiving-only"], ["授权 []", "父 当前资料范围内缺少验收依据。"]),
        ("s10_subagent", ["--child-steps", "1"], ["子 Result(", "父 子任务达到步数上限"]),
    ],
)
def test_lesson(lesson, args, expected):
    result = subprocess.run(
        [sys.executable, "-m", f"tutorials.{lesson}.code", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    remaining = result.stdout
    for fragment in expected:
        _, match, remaining = remaining.partition(fragment)
        assert match, (fragment, result.stdout)
