# 04 · 用 Hooks 在工具前后插入检查

每个业务工具都要记调用状态。把日志写进每个资料读取函数，后面再加一个工具就容易少记错误分支。这里复用第一课的循环和读取函数，只包装传给循环的 `dispatch`。[完整代码](code.py)

```sh
uv run --no-sync python -m tutorials.s04_hooks.code
uv run --no-sync python -m tutorials.s04_hooks.code --budget 0
uv run --no-sync python -m tutorials.s04_hooks.code --section R9
```

## 哪些事件意味着工具真的执行了

```python
before(call)
try:
    result = execute(call)
except Exception:
    after(call, "error")
    raise
after(call, "end")
```

默认会看到同一个 `read-1` 的 `start` 和 `end`，然后才是循环回填的 R2。向上找 `with_hooks(read_receipt, before, after)`。传入的三个函数分别负责读资料、核对预算、记录结束状态。

把预算设为 0，循环仍打印模型提出的 `Call`。`before` 在进入 `execute` 之前抛错，因此没有 `start`、没有原文回执。把段号换成 R9，预算足够，观察到的顺序则是 `start → error`。异常重新抛出，循环没有收到成功结果。**模型请求过、工具开始过、工具成功过，是三件可以分别观测的事。**

**动手验证。** 在 `after` 增加你要收集的字段，例如 `call.args["section"]`。再跑 R2、R9 两次，确认成功和失败都有段号。再跑预算为 0 的输入，解释为什么这次要在 `before` 里另外登记拒绝事件，才能统计拒绝次数。

工作台的对应扩展点是 `TurnMiddleware.on_acting`。它先重新鉴权、检查本轮预算，再消费 SDK 的工具执行流，在退出流时收尾。`on_model_call` 位于另一个边界，可以记录模型调用和用量。需要判断答案有没有依据时，还要关联实际段落回执。[中间件源码](../../src/biu_agent/agents/hooks/__init__.py) · [循环如何挂载中间件](../../src/biu_agent/agent.py)

下一课看一个特殊工具怎样把操作说明送进模型上下文。[05 · Skills 加载](../s05_skill_loading/README.md)
