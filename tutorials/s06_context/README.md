# 06 · 追问里的「那」需要历史，答案里的出处需要本轮原文

先问「样品灯验收了吗？」，再问「那导向牌呢？」。直接丢掉历史会丢掉指代，把先前回答照搬进来又容易把样品灯的验收依据用到导向牌身上。沿着 `main` 追踪历史问答、本轮发给模型的消息，以及本轮允许发布的出处。[完整代码](code.py)

```sh
uv run --no-sync python -m tutorials.s06_context.code
uv run --no-sync python -m tutorials.s06_context.code --reuse-citation
```

## 从输入到发布

| 位置 | 放进去的内容 | 在这一步解决的问题 |
| --- | --- | --- |
| `ctx.history` | 第一问，以及带 `acceptance#A1` 的旧回答 | 重现一次真实追问的起点 |
| `messages_for(ctx, questions_only=True)` | 第一问和「那导向牌呢？」 | 给模型足够的语义去理解追问 |
| `ctx.evidence` | 本轮按当前授权读到的 `receiving#R2` | 为输出的原文引用提供核对对象 |

看 `messages_for` 的列表推导。它取每对问答的第一个元素，构造成一条用户消息。工作台在此之前还会调用 `history_before`，按原问题的位置向前查最近成功轮次，按条数和字符预算截断。重新生成早先的问题时，截点由原问题确定，后来的对话不会跑到它前面。[消息装配](../../src/biu_agent/agents/context/__init__.py) · [历史选择](../../src/biu_agent/agents/memory/__init__.py)

再看实验中唯一随开关变化的一行。

```python
ref = "acceptance#A1" if reuse_citation else row["source"]
```

两个候选答案有相同的正文，只有引用不同。`Workspace.verify` 从答案抽取引用，检查它们是否都是本轮读取的，再回查材料版本。默认发布 R2，加开关则拒绝发布并给出 `unverified_source`。[发布前校验](../../examples/tickets/workspace.py)

**动手验证。** 保留真实的 R2 引用，把候选正文改成「导向牌已通过验收」。观察当前校验仍能通过，再打开 R2 对照。这样你就能解释为什么「引用确实读过」可以自动检查，而「结论得到这段原文支持」还需要内容评测。恢复正文后进入下一课。[07 · 记忆系统](../s07_memory/README.md)
