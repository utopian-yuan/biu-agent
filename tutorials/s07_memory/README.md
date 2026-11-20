# 07 · 新会话凭什么记得用户的偏好

「导向牌签收了吗」要查最新记录，「以后请用表格列出处」却可以下次继续用。上一课已经看到工作台怎样读取同一会话的历史。本课做一个独立的跨会话偏好实验，让两类信息各有明确的读写路径。[完整代码](code.py)

```sh
uv run --no-sync python -m tutorials.s07_memory.code
uv run --no-sync python -m tutorials.s07_memory.code --forget
```

## 跟踪一条偏好的生存期

1. **选择写入。** `remember(db, "小舟", "表格")` 模拟用户明确指定格式。表里只有账号和格式，数据库约束只允许两种格式。同账号的新选择通过 `ON CONFLICT(owner) DO UPDATE` 覆盖旧值。
2. **跨会话读取。** 第一个连接提交并关闭。第二个连接没有上轮的消息，`answer` 仍可按账号找到设置。找不到时使用段落格式。实验中账号来自固定样本，接入工作台时应取当前已认证身份的稳定 ID。
3. **影响本轮输出。** `answer` 先召回偏好，再调用 `excerpt` 读取当前 R2。偏好只决定排版。两个账号看到相同的原文和出处，小舟用表格，青禾用段落。
4. **忘记。** `--forget` 在新一轮回答前按小舟账号删除那条设置，再重复查询。两人都得到段落。实验使用临时数据库，每次启动都会重建这个场景。

```python
preference = db.execute("SELECT layout FROM preferences WHERE owner=?", (owner,)).fetchone()
layout = preference[0] if preference else "段落"
row = excerpt("receiving", "R2", {"receiving"})
```

**动手验证。** 在第一次 `remember` 后紧接着调用 `remember(db, "小舟", "段落")`。如果新连接仍然显示表格，要查写入冲突策略。如果青禾的设置跟着改变，要查读写使用的账号键。最后暂时打印 `SELECT owner, layout FROM preferences` 的结果，预期只有小舟的一行。

工作台已有另一种记忆。沿 `Sessions.start → history_before` 看它怎样按会话与时间截点定位原问题及当时可用的成功修订，再交给下一轮。产品当前的会话记忆与本章的偏好表分别可以回答「刚才问了什么」和「这个用户长期怎样读答案」。偏好表属于课程扩展。[会话入口](../../src/biu_agent/session.py) · [问题与修订投影](../../src/biu_agent/agents/memory/__init__.py)

下一课用时钟发起一轮任务。[08 · cron 调度](../s08_cron/README.md)
