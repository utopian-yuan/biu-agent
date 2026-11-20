"""Run with python -m tests.materials_browser [--screenshots DIRECTORY]. All state is temporary."""

import argparse
import asyncio
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event

from playwright.sync_api import expect, sync_playwright

from biu_agent import Settings
from examples.tickets.app import create_materials
from examples.tickets.teaching_model import TeachingModel

from .material_server import serve_local


def pause_material_read(app, release):
    """Expose the real UI after the tool starts and before its answer is available."""

    async def wrapped(scope, receive, send):
        async def deliver(message):
            await send(message)
            body = message.get("body", b"")
            if (
                not release.is_set()
                and body.startswith(b"event: tool.status")
                and b'"name":"read_material","phase":"start"' in body
            ):
                await asyncio.to_thread(release.wait, 15)

        await app(scope, receive, deliver)

    return wrapped


def exercise(page, origin, pictures, release):
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)

    def picture(name):
        if pictures:
            expect(page.locator("#toast")).to_be_hidden()
            pictures.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=pictures / (name + ".png"), animations="disabled")

    def navigate(view):
        page.locator(f'[data-nav="{view}"]').first.click()
        expect(page.locator("#location")).to_have_text(
            {
                "history": "历史对话",
                "sources": "素材库",
                "agents": "智能体",
                "models": "模型",
                "tools": "工具",
                "skills": "技能",
                "mcps": "MCP 连接",
                "chat": "助手对话",
            }[view]
        )

    def action(name):
        return page.get_by_role("button", name=name, exact=True)

    def anchored(trigger, role):
        pop = page.locator("#history-popover")
        expect(pop).to_be_visible()
        expect(pop).to_have_attribute("role", role)
        expect(page.locator("dialog[open]")).to_have_count(0)
        anchor, panel = trigger.evaluate("""async button => {
          const rail = button.closest('#rail');
          if (rail) await Promise.allSettled(rail.getAnimations().map(a => a.finished));
          await new Promise(requestAnimationFrame);
          return [button, document.querySelector('#history-popover')].map(el => el.getBoundingClientRect().toJSON());
        }""")
        assert (
            min(
                abs(panel["y"] - anchor["y"] - anchor["height"]),
                abs(anchor["y"] - panel["y"] - panel["height"]),
            )
            <= 7
        ), (anchor, panel)
        assert panel["x"] < anchor["right"] and anchor["x"] < panel["right"], (anchor, panel)
        assert panel["x"] >= 0 and panel["x"] + panel["width"] <= page.viewport_size["width"]
        return pop

    page.goto(origin)
    expect(page.get_by_role("heading", name="登录工作台")).to_be_visible()
    picture("login")
    action("注册账号").click()
    expect(page.get_by_role("heading", name="注册账号")).to_be_visible()
    picture("register")
    page.set_viewport_size({"width": 390, "height": 844})
    expect(action("注册")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    action("返回登录").click()
    action("注册账号").click()
    page.set_viewport_size({"width": 1360, "height": 850})
    page.locator("#username").fill("小禾")
    page.locator("#password").fill("browser-materials-example")
    page.locator("#confirm-password").fill("different-example")
    page.locator("#submit").click()
    expect(page.get_by_role("alert")).to_contain_text("两次输入的密码不一致")
    page.locator("#confirm-password").fill("browser-materials-example")
    page.locator("#submit").click()
    expect(page.get_by_role("heading", name="登录工作台")).to_be_visible()
    expect(page.get_by_role("status")).to_contain_text("注册成功")
    expect(page.locator("#username")).to_have_value("小禾")
    expect(page.locator("#password")).to_have_value("")
    assert all(cookie["name"] != "biu_workspace" for cookie in page.context.cookies())
    page.locator("#password").fill("browser-materials-example")
    page.locator("#submit").click()
    expect(page.locator("#guide")).to_be_visible()
    for n in range(1, 7):
        expect(page.locator("#guide-step")).to_have_text(f"{n} / 6")
        page.locator("#guide-next").click()
    expect(page.locator("#guide")).to_be_hidden()
    expect(page.locator("#owner")).to_have_text("小禾")
    picture("workspace")
    page.locator("#help").click()
    page.locator("#guide-skip").click()
    page.reload()
    expect(page.locator("#owner")).to_have_text("小禾")
    expect(page.locator("#guide")).to_be_hidden()

    page.get_by_role("button", name="核对到货与验收", exact=False).click()
    action("发送消息").click()
    turn = page.locator(".turn-card").first
    expect(turn.get_by_role("button", name="点赞", exact=True)).to_be_visible()
    expect(turn.locator(".inline-ref")).to_have_count(6)
    turn.get_by_role("button", name="查看引用 验收说明 A1").click()
    expect(page.locator("#dialog .excerpt")).to_contain_text("通电与数量检查")
    expect(page.locator("#dialog .fingerprint")).to_have_text(re.compile(r"^[a-f0-9]{64}$"))
    action("关闭").click()
    action("本轮上下文").click()
    expect(page.locator("#dialog-body")).to_contain_text("读取段落 × 6")
    action("关闭").click()
    action("点赞").click()
    expect(action("点赞")).to_have_attribute("aria-pressed", "true")
    action("点踩").click()
    expect(action("点踩")).to_have_attribute("aria-pressed", "true")
    expect(action("点赞")).to_have_attribute("aria-pressed", "false")
    action("复制回答").click()
    assert "receiving#R2" in page.evaluate("navigator.clipboard.readText()")
    picture("answer")
    first_root = turn.get_attribute("data-root")
    with page.expect_file_chooser() as chooser:
        action("上传本地文件").click()
    chooser.value.set_files(
        [
            {"name": "待移除.txt", "mimeType": "text/plain", "buffer": "这份文件留在本地。".encode()},
            {
                "name": "展签清点.md",
                "mimeType": "text/markdown",
                "buffer": "虚构展区摆放了三枚琥珀展签。".encode(),
            },
        ]
    )
    action("移除 待移除.txt").click()
    expect(page.locator("#draft-files")).to_contain_text("展签清点.md")
    composer = page.get_by_role("textbox", name="发送消息")
    question = "展签清点.md 记录了什么？"
    composer.fill(question)
    held = []
    route_pattern = "**/sessions/*/turns"
    page.route(route_pattern, lambda route: held.append(route))
    with page.expect_request(lambda r: r.method == "POST" and r.url.endswith("/turns")):
        action("发送消息").click()
    last = page.locator(".turn-card").last
    expect(last.locator(".question")).to_have_text(question)
    expect(composer).to_have_value("")
    expect(composer).to_be_focused()
    expect(page.locator("#draft-files")).to_be_hidden()
    expect(last.locator(".sent-files")).to_have_text("展签清点.md")
    expect(action("发送消息")).to_be_disabled()
    assert len(held) == 1
    release.clear()
    try:
        held[0].continue_()
        page.unroute(route_pattern)
        expect(page.locator("#status")).to_contain_text("正在调用 读取段落")
        expect(last.locator(".answer")).to_be_empty()
        expect(composer).to_have_value("")
        expect(last.locator(".sent-files")).to_have_text("展签清点.md")
        picture("composer-sent")
        # Even retyping the same question is a new draft.
        composer.fill(question)
        composer.press("Enter")
        expect(page.locator(".turn-card")).to_have_count(2)
    finally:
        release.set()
    expect(last.locator(".answer")).to_contain_text("三枚琥珀展签")
    expect(last.locator(".sent-files")).to_have_text("展签清点.md")
    expect(action("发送消息")).to_be_enabled()
    expect(composer).to_have_value(question)
    composer.fill("")
    last.get_by_role("button", name="查看引用 展签清点.md S1").click()
    expect(page.locator("#dialog .excerpt")).to_contain_text("三枚琥珀展签")
    action("关闭").click()
    picture("attachment")
    second_id = last.get_attribute("data-turn")
    pending_slots = []

    def during_revision(route):
        pending_slots.append(
            (
                page.locator(".turn-card").count(),
                turn.get_attribute("aria-busy"),
                last.get_attribute("data-turn"),
            )
        )
        route.continue_()

    page.route("**/regenerate", during_revision)
    composer.fill("稍后继续核对展签")
    page.locator("#file-input").set_input_files(
        {"name": "下次核对.txt", "mimeType": "text/plain", "buffer": "虚构的新草稿。".encode()}
    )
    turn.get_by_role("button", name="重新生成").click()
    expect(turn.locator(".revision-label").first).to_have_text("重新生成的回答")
    expect(page.locator(".turn-card")).to_have_count(2)
    assert pending_slots == [(2, "true", second_id)]
    expect(turn).to_have_attribute("data-root", first_root)
    expect(last).to_have_attribute("data-turn", second_id)
    expect(composer).to_have_value("稍后继续核对展签")
    action("移除 下次核对.txt").click()
    composer.fill("")
    expect(turn.locator(".answer")).not_to_contain_text("琥珀展签")
    turn.get_by_role("button", name="本轮上下文").click()
    expect(page.locator("#dialog-body")).not_to_contain_text("展签清点.md")
    action("关闭").click()
    page.reload()
    expect(page.locator(".turn-card")).to_have_count(2)
    expect(turn).to_have_attribute("data-root", first_root)
    expect(last).to_have_attribute("data-turn", second_id)
    trigger = page.locator("#recent").get_by_role("button", name=re.compile("^对话操作")).first
    trigger.click()
    nearby = anchored(trigger, "menu")
    picture("history-nearby")
    nearby.get_by_role("menuitem", name="重命名").press("ArrowDown")
    expect(nearby.get_by_role("menuitem", name="删除")).to_be_focused()
    nearby.get_by_role("menuitem", name="删除").press("Escape")
    expect(trigger).to_be_focused()
    trigger.click()
    nearby.get_by_role("menuitem", name="重命名").click()
    anchored(trigger, "dialog")
    page.get_by_label("对话标题", exact=True).fill("展具签收核对")
    action("保存").click()
    expect(page.locator("#recent")).to_contain_text("展具签收核对")
    navigate("history")
    page.get_by_role("searchbox").fill("展具签收")
    for label in ("重命名展具签收核对", "删除展具签收核对"):
        full_list_trigger = action(label)
        full_list_trigger.click()
        anchored(full_list_trigger, "dialog").get_by_role("button", name="取消").click()
        expect(full_list_trigger).to_be_focused()
    action("展具签收核对").last.click()
    expect(page.locator(".turn-card")).to_have_count(2)
    expect(turn.get_by_role("button", name="点踩")).to_have_attribute("aria-pressed", "false")

    page.get_by_label("当前智能体").select_option("receiving")
    navigate("sources")
    expect(page.locator(".source-card")).to_have_count(2)
    expect(page.locator("#page")).not_to_contain_text("验收说明")
    for view in ("models", "tools", "mcps", "agents", "skills"):
        navigate(view)
    action("内置技能").click()
    expect(page.locator("#dialog-body")).to_contain_text("已是内置版本")
    action("关闭").click()
    action("添加技能").click()
    page.get_by_label("名称", exact=True).fill("核对展具数量")
    page.get_by_label("用途", exact=True).fill("对照两份原文中的数量")
    page.get_by_label("技能说明", exact=True).fill("逐项给出数量与出处。")
    action("保存").click()
    expect(page.locator("#page")).to_contain_text("核对展具数量")
    with page.expect_file_chooser() as choose_skill:
        action("导入 SKILL.md").click()
    choose_skill.value.set_files(
        {
            "name": "SKILL.md",
            "mimeType": "text/markdown",
            "buffer": "---\nname: color-check\ndescription: 逐项核对色号\nmetadata:\n  display_name: 色号核对\n---\n\n把色号与文件名一并给出。".encode(),
        }
    )
    expect(page.locator("#page")).to_contain_text("色号核对")
    with page.expect_download() as exported:
        page.get_by_role("link", name="导出 色号核对 的 SKILL.md").click()
    assert "把色号与文件名一并给出" in Path(exported.value.path()).read_text()
    action("停用 色号核对").click()
    expect(action("启用 色号核对")).to_have_attribute("aria-pressed", "false")
    action("启用 色号核对").click()
    expect(action("停用 色号核对")).to_have_attribute("aria-pressed", "true")
    navigate("models")
    action("添加模型").click()
    page.get_by_label("名称", exact=True).fill("本机教学接口")
    page.get_by_label("模型标识", exact=False).fill("fixture-material")
    page.get_by_label("服务地址", exact=False).fill("http://localhost:13544/v1")
    action("保存").click()
    expect(page.locator("#page")).to_contain_text("本机教学接口")
    navigate("chat")
    page.get_by_label("当前模型").select_option(label="本机教学接口")
    page.get_by_label("当前模型").select_option("teaching")
    page.get_by_role("textbox", name="发送消息", exact=True).fill("哪些物品还缺少验收依据？")
    action("发送消息").click()
    expect(action("本轮上下文")).to_be_visible()
    expect(page.locator(".inline-ref")).to_have_count(4)

    navigate("tools")
    for tool_id in ("bash", "read", "write", "edit", "glob", "grep", "browser"):
        expect(page.locator("#page").get_by_text(tool_id, exact=True)).to_be_visible()
    navigate("chat")
    page.get_by_role("textbox", name="发送消息", exact=True).fill(
        'write {"path":"work/展签.txt","content":"展签七枚"}'
    )
    action("发送消息").click()
    saved = page.get_by_role("link", name="下载 work/展签.txt 第 1 版")
    expect(saved).to_be_visible()
    with page.expect_download() as file:
        saved.click()
    assert Path(file.value.path()).read_text() == "展签七枚"

    page.set_viewport_size({"width": 390, "height": 844})
    action("打开导航").click()
    page.locator("#new").click()
    expect(page.get_by_role("heading", name="想从资料中核对什么？")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    picture("mobile")
    action("打开导航").click()
    trigger = action("对话操作 · 展具签收核对")
    trigger.press("Enter")
    nearby = anchored(trigger, "menu")
    nearby.get_by_role("menuitem", name="重命名").click()
    anchored(trigger, "dialog")
    page.get_by_label("对话标题").fill("展具归档核对")
    action("保存").click()
    expect(action("展具归档核对")).to_be_visible()
    trigger = action("对话操作 · 展具归档核对")
    trigger.click()
    anchored(trigger, "menu").get_by_role("menuitem", name="删除").click()
    anchored(trigger, "dialog")
    action("取消").click()
    expect(action("展具归档核对")).to_be_visible()
    trigger = action("对话操作 · 展具归档核对")
    trigger.click()
    anchored(trigger, "menu").get_by_role("menuitem", name="删除").click()
    anchored(trigger, "dialog")
    nearby.get_by_role("button", name="删除对话", exact=True).click()
    expect(page.locator("#recent")).not_to_contain_text("展具归档核对")
    page.locator("#all-history").click()
    page.get_by_role("searchbox").fill("展具归档核对")
    expect(page.locator("#page")).to_contain_text("没有匹配的对话")
    action("打开导航").click()
    page.locator("#new").click()
    with page.expect_file_chooser() as new_file:
        action("上传本地文件").click()
    new_file.value.set_files(
        {"name": "窄屏练习.txt", "mimeType": "text/plain", "buffer": "虚构展柜有六格。".encode()}
    )
    created = []

    def hold_creation(route):
        if route.request.method == "POST":
            created.append(route)
        else:
            route.continue_()

    page.route("**/api/sessions", hold_creation)
    with page.expect_request(lambda r: r.method == "POST" and r.url.endswith("/sessions")):
        composer.press("Enter")
    expect(page.locator(".question")).to_have_text("请概括所附文件，并标注出处。")
    expect(page.locator(".sent-files")).to_have_text("窄屏练习.txt")
    expect(page.locator("#draft-files")).to_be_hidden()
    assert len(created) == 1
    created[0].continue_()
    page.unroute("**/api/sessions", hold_creation)
    expect(page.locator(".answer")).to_contain_text("虚构展柜有六格")
    expect(page.locator(".turn-card")).to_have_count(1)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    action("打开导航").click()
    action("退出登录").click()
    expect(page.get_by_role("heading", name="登录工作台")).to_be_visible()
    page.locator("#username").fill("小禾")
    page.locator("#password").fill("browser-materials-example")
    page.locator("#submit").click()
    expect(page.locator("#message")).to_be_visible()
    expect(page.locator("#guide")).to_be_hidden()
    page.route(
        "**/tutorial-embed",
        lambda route: route.fulfill(
            content_type="text/html", body='<iframe title="材料助手" src="/assistant/chat/"></iframe>'
        ),
    )
    page.goto(origin + "/tutorial-embed")
    expect(page.frame_locator("iframe").get_by_role("textbox", name="发送消息")).to_be_visible()
    assert not errors, errors
    print("材料工作台浏览器通过：账号、导览、两种权限、出处、上下文、回答操作、历史和配置、390px 窄屏。")


def rejected_drafts(context, origin):
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(origin)
    expect(page.locator("#owner")).to_have_text("小禾")
    field = page.get_by_role("textbox", name="发送消息")
    send = page.get_by_role("button", name="发送消息", exact=True)
    old, new = "  请读这份新展签。  ", "下一步再核对目录"
    page.locator("#file-input").set_input_files(
        {"name": "展签.txt", "mimeType": "text/plain", "buffer": b"\xff"}
    )
    field.fill(old)
    with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/turns")) as rejected:
        field.press("Enter")
    assert rejected.value.status == 422
    expect(field).to_have_value(old)
    expect(page.locator("#draft-files")).to_have_text("展签.txt")
    expect(page.locator(".question")).to_have_count(0)

    # Another rejection after the user starts a different draft must preserve both.
    held = []
    pattern = "**/sessions/*/turns"
    page.route(pattern, lambda route: held.append(route))
    with page.expect_request(lambda r: r.method == "POST" and r.url.endswith("/turns")):
        send.click()
    field.fill(new)
    with page.expect_response(lambda r: r.url.endswith("/turns")) as rejected:
        held[0].continue_()
    assert rejected.value.status == 422
    page.unroute(pattern)
    expect(field).to_have_value(new)
    expect(page.locator("#draft-files")).to_be_hidden()
    page.get_by_role("button", name="取回草稿 · " + old.strip()).click()
    expect(field).to_have_value(old)
    page.get_by_role("button", name="移除 展签.txt").click()
    page.locator("#file-input").set_input_files(
        {"name": "展签.txt", "mimeType": "text/plain", "buffer": "虚构青色展签有五枚。".encode()}
    )

    # Simulate a lost response after the real service has stored the question and files.
    attempts = []

    def lose_first_response(route):
        attempts.append(route.request.post_data_json["request_id"])
        if len(attempts) == 1:
            assert route.fetch().ok
            route.abort("failed")
        else:
            route.continue_()

    page.route(pattern, lose_first_response)
    send.click()
    expect(page.locator(".answer")).to_contain_text("青色展签有五枚")
    expect(page.locator(".question")).to_have_count(1)
    expect(page.locator(".sent-files")).to_have_text("展签.txt")
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    expect(send).to_be_enabled()
    page.get_by_role("button", name="取回草稿 · " + new).click()
    expect(field).to_have_value(new)
    expect(page.locator("#saved-drafts button")).to_have_count(0)
    assert not errors, errors
    page.close()
    print("提交被拒后原文与附件恢复，另一份草稿可取回，丢回执重试只产生一条消息。")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshots", type=Path)
    parser.add_argument("--browser", help="可选的本机 Chromium 可执行文件")
    args = parser.parse_args()
    with TemporaryDirectory() as tmp:
        app = create_materials(Settings(Path(tmp), TeachingModel()))
        release = Event()
        release.set()
        with serve_local(pause_material_read(app, release)) as origin, sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=args.browser)
            context = browser.new_context(
                viewport={"width": 1360, "height": 850},
                locale="zh-CN",
                permissions=["clipboard-read", "clipboard-write"],
            )
            page = context.new_page()
            page.set_default_timeout(6000)
            try:
                exercise(page, origin, args.screenshots, release)
                rejected_drafts(context, origin)
            finally:
                browser.close()


if __name__ == "__main__":
    main()
